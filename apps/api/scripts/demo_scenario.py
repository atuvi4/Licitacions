"""Escenari de demo local: una licitacio recorreguda de punta a punta.

Nomes per a desenvolupament. Deixa `apps/api/var/dev.db` amb prou dades perque
la interficie es pugui revisar amb contingut: feed, fitxa, plec, analisi,
requisits, compliment, candidatura amb GO, dossier i paquet congelat que no es
pot aprovar perque l'analisi es una simulacio.

Dues licitacions recorren el flux sencer:

* **La licitacio estrella DEMO** (`scripts/demo_data/placsp_demo_estrella.atom.tmpl`):
  inventada i marcada `[DEMO]`, oberta, i pensada perque el pre-fit del perfil
  del seed la doni `candidate`. Es la que s'ensenya primer.
* **L'expedient 20232857** dels fixtures PLACSP: fora de termini i amb historial
  de revisions. Serveix per veure una candidatura sobre una licitacio vencuda.

La resta de fixtures (lots, dades insuficients) queden al feed per provar estats.

    cd apps/api
    DATABASE_URL=sqlite+aiosqlite:///./var/dev.db ENVIRONMENT=local \
    STORAGE_BACKEND=local STORAGE_LOCAL_PATH=./var/storage \
    LLM_PROVIDER=fixture ANTHROPIC_API_KEY= \
    .venv/Scripts/python.exe scripts/demo_scenario.py

Que el fa segur:

* **Nomes `var/dev.db`.** Si `DATABASE_URL` no es exactament aquest fitxer SQLite
  (Postgres, `licitacions`, `licitacions_benchmark` o qualsevol altre cami), no
  arrenca. Tampoc si el fitxer no existeix: no se'n crea cap de nou.
* **Cost zero.** Refusa qualsevol `*_API_KEY` informada, exigeix
  `LLM_PROVIDER=fixture`, impedeix importar el SDK d'Anthropic i talla qualsevol
  connexio de xarxa que no sigui local.
* **Cap drecera.** Tot passa per l'API (en proces, amb `ASGITransport`) i pels
  serveis que ja existeixen: ingesta de fixtures PLACSP, pre-fit, preview i
  confirmacio de l'analisi, worker, compliment, candidatura, dossier i paquet.
  L'unica escriptura directa es `scripts/seed_fixture_plec.py`, que fa el paper
  de la descarrega i l'extraccio del plec de desenvolupament.
* **Idempotent.** Cada pas comprova primer si ja esta fet. Una segona passada no
  duplica res.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from datetime import date, timedelta
from pathlib import Path
from string import Template
from urllib.parse import unquote

API_ROOT = Path(__file__).resolve().parents[1]
DEV_DB = API_ROOT / "var" / "dev.db"
STORAGE_ROOT = API_ROOT / "var" / "storage"
FIXTURES = API_ROOT / "tests" / "fixtures" / "placsp"

SQLITE_PREFIX = "sqlite+aiosqlite:///"
PLACSP_ID = "https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/"
#: L'expedient sense lots de `feed_sample.atom`. `feed_sample_modified.atom` n'allarga
#: el termini fins al 2027, i aixi la fitxa te historial de revisions.
DEMO_TENDER = PLACSP_ID + "20232857"
STAR_TENDER = "urn:licitacions:demo:estrella-001"
STAR_TEMPLATE = API_ROOT / "scripts" / "demo_data" / "placsp_demo_estrella.atom.tmpl"
STAR_FEED = API_ROOT / "var" / "demo" / "placsp_demo_estrella.atom"
#: Prou lluny per quedar a la banda de termini comode del pre-fit (>= 20 dies) i
#: abans de qualsevol venciment del seed (l'asseguranca caduca a +165 dies).
STAR_DEADLINE_DAYS = 60
MINIMAL_TENDER_SUFFIX = "99999001"
WEB_URL = "http://localhost:3000"

#: Els documents que el perfil del seed no te i que el plec de desenvolupament
#: demana. Per categoria de requisit; l'experiencia fa servir un del seed.
DEMO_DOCUMENTS = {
    "economic_solvency": "Comptes anuals (demo).pdf",
    "certification": "Certificat ISO 14001 (demo).pdf",
    "experience": "Certificat de bona execucio - Ajuntament Demo.pdf",
    "technical_requirement": "Memoria tecnica signada (demo).pdf",
}
#: El plec demana tres contractes similars (`count: 3`). Els dos primers son del
#: seed; el tercer es puja lligat al tercer projecte del seed.
EXPERIENCE_DOCUMENTS = (
    "Certificat de bona execucio - Ajuntament Demo.pdf",
    "Certificat de bona execucio - Consell Comarcal Demo.pdf",
    "Certificat de bona execucio - Videovigilancia (demo).pdf",
)
THIRD_PROJECT = "Videovigilancia poligon industrial (demo)"
ISO_14001_RENEWED_UNTIL = "2027-10-25"
ESTABLISHED_RELEVANCE = {"linked_evidence", "human_confirmed"}
DEMO_NOTE = (
    "[DEMO] Revisio d'exemple de l'escenari de demostracio. "
    "L'analisi es una simulacio i els documents no son reals."
)

DEMO_EMAIL = "demo@licitacions.dev"
DEMO_PASSWORD = "Demo12345!"  # noqa: S105 - credencial publica del seed de desenvolupament


class DemoRefused(RuntimeError):
    """L'entorn no es el de la demo local. No s'ha escrit res."""


class DemoStepFailed(RuntimeError):
    """Un pas de l'escenari no ha passat les regles del producte."""


# --------------------------------------------------------------------------- #
# Guardes: abans d'importar res de l'aplicacio                                  #
# --------------------------------------------------------------------------- #


def _same_path(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def check_environment(environ: dict[str, str]) -> None:
    """Refusa tot el que no sigui la base local i el proveidor de simulacio."""
    url = (environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise DemoRefused(
            "Cal DATABASE_URL explicita. Sense, la configuracio construeix una URL de "
            "Postgres a partir de POSTGRES_*."
        )
    lowered = url.lower()
    if not lowered.startswith(SQLITE_PREFIX) or "postgres" in lowered or "asyncpg" in lowered:
        raise DemoRefused(f"Nomes SQLite local. DATABASE_URL no ho es ({url.split(':')[0]}).")
    raw_path = unquote(url[len(SQLITE_PREFIX) :].split("?", 1)[0])
    db_path = Path(raw_path)
    if not db_path.is_absolute():
        db_path = API_ROOT / db_path
    if db_path.name.lower() != "dev.db" or not _same_path(db_path, DEV_DB):
        raise DemoRefused(f"DATABASE_URL ha d'apuntar a {DEV_DB}; apunta a {db_path}.")
    if not DEV_DB.is_file():
        raise DemoRefused(f"No existeix {DEV_DB}. Crea-la amb `python -m app.dev_bootstrap`.")

    keys = sorted(
        name
        for name, value in environ.items()
        if name.upper().endswith("_API_KEY") and value.strip()
    )
    if keys:
        raise DemoRefused(
            f"Hi ha claus d'API informades ({', '.join(keys)}). La demo no en fa servir cap: "
            "buida-les (p. ex. ANTHROPIC_API_KEY=)."
        )

    provider = (environ.get("LLM_PROVIDER") or "").strip().lower()
    if provider != "fixture":
        raise DemoRefused(f"Cal LLM_PROVIDER=fixture; ara es {provider or '(buit)'}.")

    environment = (environ.get("ENVIRONMENT") or "").strip().lower()
    if environment not in {"local", "development", "dev"}:
        raise DemoRefused(f"Cal ENVIRONMENT=local; ara es {environment or '(buit)'}.")

    if (environ.get("STORAGE_BACKEND") or "").strip().lower() != "local":
        raise DemoRefused("Cal STORAGE_BACKEND=local (el .env de l'arrel apunta a S3).")
    storage = Path(environ.get("STORAGE_LOCAL_PATH") or "./var/storage")
    if not storage.is_absolute():
        storage = API_ROOT / storage
    if not _same_path(storage, STORAGE_ROOT):
        raise DemoRefused(f"STORAGE_LOCAL_PATH ha de ser {STORAGE_ROOT}.")


def install_offline_guards() -> None:
    """Cap SDK d'Anthropic i cap connexio fora de la maquina."""
    # `None` a sys.modules fa que qualsevol `import anthropic` llanci ImportError.
    sys.modules["anthropic"] = None  # type: ignore[assignment]

    original_connect = socket.socket.connect

    def local_only(self: socket.socket, address, *args, **kwargs):  # type: ignore[no-untyped-def]
        host = address[0] if isinstance(address, tuple) else address
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise ConnectionRefusedError(f"demo_scenario: connexio bloquejada cap a {host!r}")
        return original_connect(self, address, *args, **kwargs)

    socket.socket.connect = local_only  # type: ignore[method-assign]


def check_settings() -> None:
    """La configuracio carregada (entorn + fitxers .env) ha de dir el mateix."""
    from app.core.config import settings
    from app.tenant_ops import assert_local_not_production

    assert_local_not_production()
    if settings.sqlalchemy_url != os.environ["DATABASE_URL"].strip():
        raise DemoRefused("La configuracio carregada no fa servir la DATABASE_URL de l'entorn.")
    if settings.LLM_PROVIDER.strip().lower() != "fixture":
        raise DemoRefused("La configuracio carregada no fa servir LLM_PROVIDER=fixture.")
    if settings.ANTHROPIC_API_KEY.strip():
        raise DemoRefused("La configuracio carregada te ANTHROPIC_API_KEY (potser d'un .env).")
    if settings.STORAGE_BACKEND.strip().lower() != "local":
        raise DemoRefused("La configuracio carregada no fa servir storage local.")


# --------------------------------------------------------------------------- #
# Escenari                                                                      #
# --------------------------------------------------------------------------- #


def step(title: str) -> None:
    print(f"\n== {title}")


def expect(response, *codes: int):  # type: ignore[no-untyped-def]
    if response.status_code not in codes:
        raise DemoStepFailed(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code}: {response.text[:600]}"
        )
    return response.json()


async def _tender(session, external_id: str):  # type: ignore[no-untyped-def]
    import sqlalchemy as sa

    from app.models.tenders import Tender

    return await session.scalar(sa.select(Tender).where(Tender.external_id == external_id))


async def ingest_tenders() -> None:
    import sqlalchemy as sa

    from app.db.session import SessionFactory
    from app.ingestion.placsp.service import PlacspIngestionService
    from app.models.tender_lifecycle import TenderRevision
    from app.models.tenders import Tender

    async def ingest(name: str) -> None:
        async with SessionFactory() as session:
            run = await PlacspIngestionService(session).run(fixture=FIXTURES / name)
            print(
                f"  {name}: {run.status.value} (noves={run.records_new}, "
                f"modificades={run.records_changed}, sense canvis={run.records_unchanged})"
            )
            if run.status.value not in {"success", "partial"}:
                raise DemoStepFailed(f"La ingesta de {name} ha acabat en {run.status.value}.")

    async with SessionFactory() as session:
        tender = await _tender(session, DEMO_TENDER)
    if tender is None:
        await ingest("feed_sample.atom")

    async with SessionFactory() as session:
        tender = await _tender(session, DEMO_TENDER)
        revisions = await session.scalar(
            sa.select(sa.func.count())
            .select_from(TenderRevision)
            .where(TenderRevision.tender_id == tender.id)
        )
    if (revisions or 0) < 2:
        await ingest("feed_sample_modified.atom")
    else:
        print("  feed_sample + feed_sample_modified: ja ingerits")

    async with SessionFactory() as session:
        minimal = await session.scalar(
            sa.select(Tender.id).where(Tender.external_id.like(f"%{MINIMAL_TENDER_SUFFIX}"))
        )
    if minimal is None:
        await ingest("feed_minimal.atom")
    else:
        print("  feed_minimal: ja ingerit")


def render_star_feed(today: date) -> str:
    """El feed de la licitacio estrella amb el termini relatiu a `today`."""
    template = Template(STAR_TEMPLATE.read_text(encoding="utf-8"))
    return template.substitute(
        DEADLINE=(today + timedelta(days=STAR_DEADLINE_DAYS)).isoformat(),
        PUBLISHED=today.isoformat(),
        UPDATED=f"{today.isoformat()}T09:00:00.000+02:00",
    )


async def ingest_star_tender() -> None:
    """Nomes la primera vegada.

    Si es tornes a generar cada dia, el termini canviaria a cada passada: una
    revisio nova, i l'analisi, el compliment i el paquet quedarien desfasats.
    """
    from app.db.session import SessionFactory
    from app.ingestion.placsp.service import PlacspIngestionService

    async with SessionFactory() as session:
        if await _tender(session, STAR_TENDER) is not None:
            print("  estrella DEMO: ja ingerida")
            return

    STAR_FEED.parent.mkdir(parents=True, exist_ok=True)
    STAR_FEED.write_text(render_star_feed(date.today()), encoding="utf-8")
    async with SessionFactory() as session:
        run = await PlacspIngestionService(session).run(fixture=STAR_FEED)
    print(f"  estrella DEMO: {run.status.value} (noves={run.records_new})")
    if run.status.value != "success" or run.records_new != 1:
        raise DemoStepFailed(f"La ingesta de l'estrella ha acabat en {run.status.value}.")


async def recalculate_matches() -> None:
    import sqlalchemy as sa

    from app.db.session import SessionFactory
    from app.matching.service import MatchingService
    from app.models.identity import Company

    async with SessionFactory() as session:
        companies = (await session.execute(sa.select(Company.id, Company.legal_name))).all()
        for company_id, name in companies:
            summary = await MatchingService(session).recalculate(company_id)
            print(
                f"  {name}: {summary.tenders_evaluated} licitacions "
                f"(creats={summary.created}, actualitzats={summary.updated})"
            )


async def analyse(client, base: str, tender_id: str) -> None:  # type: ignore[no-untyped-def]
    from app.ai.providers.fixture import FixtureProvider
    from app.db.session import SessionFactory
    from app.jobs.worker import Worker
    from app.models.enums import WorkJobType
    from app.services.bid_workspace import current_requirement_run

    async with SessionFactory() as session:
        current = await current_requirement_run(session, uuid_of(tender_id))
    if current is not None:
        print(
            f"  ja hi ha una analisi vigent: {current.status.value}, "
            f"{current.requirements_extracted} requisits ({current.provider})"
        )
        return

    quote = expect(await client.post(f"{base}/tenders/{tender_id}/analysis-quotes", json={}), 201)
    print(
        f"  preview: {len(quote['documents'])} document(s), {quote['pages_in_plan']} pagines, "
        f"{quote['chunks']} chunk(s), proveidor {quote['provider']}"
    )
    if quote["provider"] != "fixture":
        raise DemoStepFailed(f"El preview demana el proveidor {quote['provider']!r}.")
    if not quote["allowed"]:
        raise DemoStepFailed(f"El preview no es permet: {quote['blocking_reason']}.")

    request = expect(await client.post(f"{base}/analysis-quotes/{quote['id']}/confirm"), 200, 202)
    worker = Worker(
        SessionFactory,
        worker_id="demo-scenario",
        job_types=[WorkJobType.ANALYZE_TENDER_REQUIREMENTS],
        provider_factory=FixtureProvider,
    )
    for _ in range(10):
        if request["status"] != "accepted":
            break
        await worker.run_once()
        request = expect(await client.get(f"{base}/job-requests/{request['id']}"), 200)
    print(f"  peticio: {request['status']} ({request['outcome'] or request['error_code']})")
    if request["status"] != "succeeded":
        raise DemoStepFailed(f"L'analisi no ha acabat be: {request['error_code']}.")


def uuid_of(value: str):  # type: ignore[no-untyped-def]
    import uuid

    return uuid.UUID(value)


def _demo_pdf(title: str) -> bytes:
    from app.seed import _fake_pdf

    return _fake_pdf(title)


async def complete_company_evidence(client, base: str) -> None:  # type: ignore[no-untyped-def]
    """El que el plec demana i el perfil del seed no te, pujat com ho faria l'empresa.

    Tot per l'API de documents i de credencials, amb noms que diuen `(demo)`. No
    es toca cap regla: nomes s'aporten els papers perque les regles tinguin
    alguna cosa a comprovar.
    """
    documents = {d["name"]: d for d in expect(await client.get(f"{base}/documents"), 200)}

    async def upload(name: str, category: str, **form: str) -> None:
        if name in documents:
            print(f"  ja hi era: {name}")
            return
        expect(
            await client.post(
                f"{base}/documents",
                files={"file": (name, _demo_pdf(f"{name} (DEMOSTRACIO)"), "application/pdf")},
                data={"name": name, "category": category, **form},
            ),
            200,
            201,
        )
        print(f"  pujat: {name}")

    years = expect(await client.get(f"{base}/financial-years"), 200)
    latest_year = max(years, key=lambda year: year["year"])
    await upload(
        DEMO_DOCUMENTS["economic_solvency"],
        "financial",
        entity_type="company_financial_year",
        entity_id=latest_year["id"],
        relationship_type="financial_statement",
    )

    # El seed deixa l'ISO 14001 caducant abans del termini de la licitacio. Es un
    # forat real i el dossier el marca; la demo el tanca com ho faria l'empresa:
    # renovant el certificat.
    certifications = expect(await client.get(f"{base}/certifications"), 200)
    iso = next(c for c in certifications if c["name"].startswith("ISO 14001"))
    if (iso["expires_at"] or "") < ISO_14001_RENEWED_UNTIL:
        expect(
            await client.patch(
                f"{base}/certifications/{iso['id']}", json={"expires_at": ISO_14001_RENEWED_UNTIL}
            ),
            200,
        )
        print(f"  renovat: {iso['name']} fins a {ISO_14001_RENEWED_UNTIL}")
    await upload(
        DEMO_DOCUMENTS["certification"],
        "certification",
        expires_at=ISO_14001_RENEWED_UNTIL,
        entity_type="company_certification",
        entity_id=iso["id"],
        relationship_type="certificate",
    )
    await upload(DEMO_DOCUMENTS["technical_requirement"], "technical")

    projects = expect(await client.get(f"{base}/projects"), 200)
    project = next(p for p in projects if p["name"] == THIRD_PROJECT)
    await upload(
        EXPERIENCE_DOCUMENTS[2],
        "project_evidence",
        entity_type="company_project",
        entity_id=project["id"],
        relationship_type="certificate_of_good_execution",
    )


async def review_assessments(client, base: str, tender_id: str) -> None:  # type: ignore[no-untyped-def]
    """Revisio humana del que el motor no ha pogut decidir. Sempre amb motiu."""
    compliance = expect(await client.get(f"{base}/tenders/{tender_id}/compliance"), 200)
    pending = [
        a
        for a in compliance["assessments"]
        if a["obligation"] == "mandatory"
        and a["effective_result"] in {"unknown", "needs_review"}
        and a["review_status"] == "unreviewed"
    ]
    if not pending:
        print("  cap avaluacio obligatoria pendent de revisar")
    for assessment in pending:
        expect(
            await client.post(
                f"{base}/assessments/{assessment['id']}/review",
                json={
                    "review_status": "verified",
                    "reviewed_result": "satisfies",
                    "note": DEMO_NOTE,
                },
            ),
            200,
        )
        automatic = assessment["automatic_result"]
        print(f"  revisat: {assessment['rule_code']} ({automatic} -> satisfies)")


async def complete_dossier(client, base: str, workspace_id: str) -> dict:  # type: ignore[no-untyped-def]
    """Tria i confirma els documents del dossier, com una persona a la pantalla."""
    url = f"{base}/workspaces/{workspace_id}/dossier"
    documents = {d["name"]: d["id"] for d in expect(await client.get(f"{base}/documents"), 200)}
    dossier = expect(await client.get(url), 200)

    for item in _items(dossier):
        if item["status"] == "suggested" and item["document"] is not None:
            payload = {"status": "selected"}
        elif item["status"] == "missing" and item["mandatory"]:
            name = DEMO_DOCUMENTS.get(item["requirement_category"])
            if name is None or name not in documents:
                continue
            payload = {"status": "selected", "document_id": documents[name]}
        else:
            continue
        expect(await client.patch(f"{url}/{item['id']}", json=payload), 200)
        print(f"  seleccionat: {item['requirement_category']} <- {item['label'][:60]}")

    # Un requisit que demana N documents no queda cobert amb un de sol.
    dossier = expect(await client.get(url), 200)
    experience = {
        item["requirement_id"]
        for item in _items(dossier)
        if item["requirement_category"] == "experience" and item["requirement_id"]
    }
    for requirement_id in experience:
        present = {
            item["document"]["id"]
            for item in _items(dossier)
            if item["requirement_id"] == requirement_id and item["document"]
        }
        for name in EXPERIENCE_DOCUMENTS:
            if name not in documents or documents[name] in present:
                continue
            expect(
                await client.post(
                    url, json={"requirement_id": requirement_id, "document_id": documents[name]}
                ),
                200,
                201,
            )
            print(f"  afegit: experience <- {name}")

    dossier = expect(await client.get(url), 200)
    for item in _items(dossier):
        if item["status"] != "selected" or item["relevance"] in ESTABLISHED_RELEVANCE:
            continue
        expect(await client.post(f"{url}/{item['id']}/relevance", json={"note": DEMO_NOTE}), 200)
        print(f"  pertinença confirmada: {item['requirement_category']} ({item['relevance']})")

    return expect(await client.get(url), 200)


def _items(dossier: dict) -> list[dict]:
    return [item for group in dossier["groups"] for item in group["items"]]


async def build_scenario() -> list[dict[str, str]]:
    from httpx import ASGITransport, AsyncClient

    from app import seed as dev_seed
    from app.db.session import SessionFactory
    from app.main import app

    step("1. Perfil d'empresa (app.seed)")
    report = await dev_seed.seed()
    changes = f"creat={report.created} restaurat={report.restored}"
    print(f"  {'sense canvis' if report.clean else changes}")

    step("2. Licitacions (fixtures PLACSP i estrella DEMO, sense xarxa)")
    await ingest_tenders()
    await ingest_star_tender()

    step("3. Pre-fit")
    await recalculate_matches()

    async with SessionFactory() as session:
        star = await _tender(session, STAR_TENDER)
        expired = await _tender(session, DEMO_TENDER)
        tenders = [
            ("Estrella DEMO", str(star.id)),
            ("20232857 (fora de termini)", str(expired.id)),
        ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://demo.local") as client:
        expect(
            await client.post(
                "/api/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
            ),
            200,
        )
        companies = expect(await client.get("/api/v1/companies"), 200)
        company_id = next(c["id"] for c in companies if c["legal_name"] == dev_seed.DEMO_COMPANY)
        base = f"/api/v1/companies/{company_id}"

        step("4. L'estrella surt candidata")
        await check_star_candidate(client, base, tenders[0][1])

        step("5. Evidencies del perfil que el plec demana (demo, per l'API)")
        await complete_company_evidence(client, base)

        flows = []
        for label, tender_id in tenders:
            flows.append(await run_tender_flow(client, base, label=label, tender_id=tender_id))
    return flows


async def check_star_candidate(client, base: str, tender_id: str) -> None:  # type: ignore[no-untyped-def]
    """Sense tocar cap regla: si el pre-fit no la dona candidata, la demo s'atura."""
    match = expect(await client.get(f"{base}/matches/by-tender/{tender_id}"), 200)
    print(f"  estat: {match['status']}, score: {match['score']}")
    if match["status"] != "candidate" or (match["score"] or 0) < 60:
        raise DemoStepFailed(
            f"L'estrella no surt candidata amb score >= 60 ({match['status']}, "
            f"{match['score']}). Revisa el perfil del seed o la plantilla, no les regles."
        )


async def run_tender_flow(client, base: str, *, label: str, tender_id: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Documents, analisi, compliment, candidatura, GO, dossier i paquet."""
    import sqlalchemy as sa

    from app.db.session import SessionFactory
    from app.models.tenders import TenderDocument
    from scripts.seed_fixture_plec import DOCUMENT_NAME
    from scripts.seed_fixture_plec import seed as seed_fixture_plec

    print(f"\n#### {label}")

    step(f"{label} | plec de desenvolupament (scripts/seed_fixture_plec.py)")
    await seed_fixture_plec(uuid_of(tender_id))
    async with SessionFactory() as session:
        document_id = str(
            await session.scalar(
                sa.select(TenderDocument.id).where(
                    TenderDocument.tender_id == uuid_of(tender_id),
                    TenderDocument.name == DOCUMENT_NAME,
                )
            )
        )

    step(f"{label} | analisi de requisits (preview, confirmacio i worker amb fixture)")
    await analyse(client, base, tender_id)

    step(f"{label} | compliment")
    result = expect(await client.post(f"{base}/tenders/{tender_id}/compliance", json={}), 200)
    run = result["run"]
    print(
        f"  {'reaprofitat' if result['reused'] else 'calculat'}: "
        f"{run.get('overall_result')} / {run.get('recommendation')}"
    )
    await review_assessments(client, base, tender_id)

    step(f"{label} | candidatura")
    created = expect(await client.post(f"{base}/tenders/{tender_id}/workspace", json={}), 200)
    workspace_id = created["workspace"]["id"]
    print(f"  {'creada' if created['created'] else 'ja existia'}: {workspace_id}")
    checklist = expect(
        await client.post(f"{base}/workspaces/{workspace_id}/checklist/regenerate"), 200
    )
    print(f"  checklist: {checklist['total']} items")

    step(f"{label} | Go/No-Go")
    detail = expect(await client.get(f"{base}/workspaces/{workspace_id}"), 200)
    latest = detail.get("latest_decision")
    if latest and latest["decision"] == "go":
        print("  ja hi ha un GO")
    else:
        detail = expect(
            await client.post(
                f"{base}/workspaces/{workspace_id}/decisions",
                json={"decision": "go", "reason": DEMO_NOTE},
            ),
            200,
        )
        print(f"  GO registrat (avisos oberts: {', '.join(detail['open_warnings']) or 'cap'})")

    step(f"{label} | dossier")
    synced = expect(await client.post(f"{base}/workspaces/{workspace_id}/dossier/sync"), 200)
    print(f"  sync: {synced['suggested']} proposats, {synced['gaps']} forats")
    dossier = await complete_dossier(client, base, workspace_id)
    _print_dossier(dossier)

    readiness = dossier["readiness"]
    if readiness["state"] != "ready_for_final_review":
        raise DemoStepFailed(
            f"{label}: el dossier no esta a punt ({readiness['state']}, "
            f"blocadors {readiness['blockers']}). No s'ha congelat cap paquet."
        )

    step(f"{label} | paquet")
    packages_url = f"{base}/workspaces/{workspace_id}/packages"
    if dossier["workspace_status"] != "ready_for_final_review":
        expect(await client.post(f"{base}/workspaces/{workspace_id}/readiness/advance"), 200)
    # Congelar sempre crea una revisio nova. Si ja n'hi ha una de vigent, no.
    current = next(
        (p for p in expect(await client.get(packages_url), 200) if p["status"] == "frozen"),
        None,
    )
    if current is None:
        package = expect(await client.post(packages_url), 200)["package"]
        created_now = True
    else:
        package = expect(await client.get(f"{packages_url}/{current['id']}"), 200)
        created_now = False
    print(
        f"  {'congelat' if created_now else 'ja congelat'}: revisio "
        f"{package['revision_number']} ({package['status']}), "
        f"can_approve={package.get('can_approve')}, "
        f"simulation_only={package.get('simulation_only')}"
    )
    refused = await client.post(
        f"{packages_url}/{package['id']}/approvals",
        json={"decision": "approved_for_submission", "comment": "[DEMO] Prova."},
    )
    error = refused.json().get("error", {}) if refused.status_code >= 400 else {}
    print(f"  aprovacio: {refused.status_code} {error.get('code')}")
    if error.get("code") != "simulation_not_approvable":
        raise DemoStepFailed("L'aprovacio d'un paquet de simulacio no s'ha bloquejat.")

    return {
        "label": label,
        "tender_id": tender_id,
        "document_id": document_id,
        "workspace_id": workspace_id,
        "package_id": package["id"],
    }


def _print_dossier(dossier: dict) -> None:
    coverage = dossier["coverage"]
    print(
        "  cobertura obligatoris: " + ", ".join(f"{key}={value}" for key, value in coverage.items())
    )
    for group in dossier["groups"]:
        for item in group["items"]:
            print(
                f"    - [{item['status']}/{item.get('coverage')}/{item.get('relevance')}] "
                f"{item['requirement_category']}: {item['label'][:70]}"
            )


async def verify() -> None:
    import sqlalchemy as sa

    from app.db.session import SessionFactory
    from app.models.analysis_quotes import AnalysisQuote
    from app.models.jobs import WorkJob
    from app.models.requirements import RequirementExtractionRun

    step("Verificacio: cap proveidor real")
    async with SessionFactory() as session:
        run_providers = (
            await session.execute(
                sa.select(RequirementExtractionRun.provider, sa.func.count()).group_by(
                    RequirementExtractionRun.provider
                )
            )
        ).all()
        job_providers = (
            await session.execute(
                sa.select(WorkJob.provider, sa.func.count()).group_by(WorkJob.provider)
            )
        ).all()
        quote_providers = (
            await session.execute(
                sa.select(AnalysisQuote.provider, sa.func.count()).group_by(AnalysisQuote.provider)
            )
        ).all()
    print(f"  runs d'analisi per proveidor : {dict(run_providers)}")
    print(f"  work_jobs per proveidor      : {dict(job_providers)}")
    print(f"  previews per proveidor       : {dict(quote_providers)}")
    loaded = sys.modules.get("anthropic")
    print(f"  SDK anthropic carregat       : {'si' if loaded is not None else 'no'}")
    used = {p for p, _ in [*run_providers, *job_providers, *quote_providers] if p}
    if used - {"fixture"} or loaded is not None:
        raise DemoStepFailed(f"S'ha fet servir un proveidor diferent de fixture: {used}.")


def print_urls(flows: list[dict[str, str]]) -> None:
    step("URLs")
    print(f"  Credencials        {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"  Dashboard          {WEB_URL}/dashboard")
    print(f"  Feed (candidates)  {WEB_URL}/licitacions")
    for flow in flows:
        tender_url = f"{WEB_URL}/licitacions/{flow['tender_id']}"
        print(f"  -- {flow['label']}")
        print(f"     Fitxa + analisi {tender_url}")
        print(f"     Plec            {tender_url}/documents/{flow['document_id']}")
        print(f"     Candidatura     {WEB_URL}/candidatures/{flow['workspace_id']}")


async def _main() -> int:
    check_settings()
    try:
        flows = await build_scenario()
        await verify()
    finally:
        from app.db.session import engine

        await engine.dispose()
    print_urls(flows)
    return 0


def main() -> int:
    try:
        check_environment(dict(os.environ))
    except DemoRefused as refused:
        print(f"REFUSAT: {refused}", file=sys.stderr)
        return 2
    os.environ.pop("BENCHMARK_DATABASE_URL", None)
    os.chdir(API_ROOT)
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))
    install_offline_guards()
    try:
        return asyncio.run(_main())
    except DemoRefused as refused:
        print(f"REFUSAT: {refused}", file=sys.stderr)
        return 2
    except DemoStepFailed as failed:
        print(f"\nATURAT: {failed}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
