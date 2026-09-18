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
        for index, (label, tender_id) in enumerate(tenders):
            flows.append(
                await run_tender_flow(
                    client,
                    base,
                    label=label,
                    tender_id=tender_id,
                    # Nomes la segona congela, i ho demana explicitament: es la
                    # que ensenya que un paquet sostingut per una simulacio no es
                    # pot aprovar. L'estrella es queda a punt perque es pugui
                    # recorrer el pas a ma.
                    freeze_package_at_end=index > 0,
                )
            )
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


#: El requisit del plec de demo que demana una resposta tecnica. La resta
#: —solvencia, assegurança, ISO, experiencia— s'acrediten amb papers, no amb una
#: proposta: qui decideix quins en necessiten es una persona (C-11), i aqui la
#: persona es aquest seed.
DEMO_TECHNICAL_REQUIREMENT = "Memoria tecnica"

#: Durada del contracte. El plec de demo la declara (12 mesos a `PlannedPeriod`)
#: pero el model no la guarda: no hi ha camp de durada a `Tender`. Per tant es
#: entrada MANUAL de la preparacio (C-3), no una extraccio, i per aixo s'escriu
#: aqui amb el mateix endpoint que faria servir una persona.
DEMO_DURATION_MONTHS = 12


async def seed_demo_technical(client, base: str, workspace_id: str, tender_id: str) -> list[dict]:  # type: ignore[no-untyped-def]
    """Les solucions tecniques de la demo, pel flux real d'S3.

    Res no s'escriu a ma: es crea pel mateix `from-requirement` que premeria una
    persona, s'omple amb un `PATCH` i es valida amb l'endpoint de validacio, que
    es l'unic que pot deixar una solucio en `validated` i el que calcula les
    empremtes.
    """
    url = f"{base}/workspaces/{workspace_id}/technical-solutions"
    existing = expect(await client.get(url), 200)
    if existing:
        print(f"  ja hi ha {len(existing)} solucions tecniques")
        return existing

    analysis = expect(await client.get(f"/api/v1/tenders/{tender_id}/analysis/requirements"), 200)
    requirement = next(
        (
            item
            for item in analysis.get("requirements", [])
            if DEMO_TECHNICAL_REQUIREMENT.lower() in (item.get("title") or "").lower()
        ),
        None,
    )
    if requirement is None:
        raise DemoStepFailed(
            f"El plec de demo no porta cap requisit {DEMO_TECHNICAL_REQUIREMENT!r}: "
            "revisa el corpus de fixtures, no aquest seed."
        )

    created = expect(
        await client.post(f"{url}/from-requirement", json={"requirement_id": requirement["id"]}),
        200,
    )
    solution = created["solution"]
    expect(
        await client.patch(
            f"{url}/{solution['id']}",
            json={
                "proposed_solution": (
                    "Memoria signada per enginyer de telecomunicacio col·legiat. "
                    "Desplegament FTTH amb topologia en anell entre els tres nodes "
                    "municipals i escomeses GPON als punts de servei. Redundancia "
                    "de cami a l'anell troncal i electronica d'acces gestionada."
                ),
                "status": "needs_validation",
            },
        ),
        200,
    )
    expect(
        await client.post(
            f"{url}/{solution['id']}/validation",
            json={
                "note": (
                    "[DEMO] Revisada l'arquitectura i la redundancia contra el que "
                    "demana el plec."
                )
            },
        ),
        200,
    )

    # Una solucio manual: l'equipament no respon a cap exigencia concreta del
    # plec, pero forma part del que oferim i la pantalla l'ha de poder ensenyar.
    manual = expect(
        await client.post(
            url,
            json={
                "title": "Electronica de xarxa i equipament d'acces",
                "proposed_solution": (
                    "Commutadors d'acces gestionats als tres nodes i terminals "
                    "optics d'usuari als punts de servei, amb configuracio i "
                    "inventari lliurats en posar en servei."
                ),
                "supplier_name": "Distribucions Xarxa SL",
                "product_name": "Serie AX-2400",
                "status": "needs_validation",
            },
        ),
        201,
    )
    expect(
        await client.post(
            f"{url}/{manual['id']}/validation",
            json={"note": "[DEMO] Equipament comprovat contra el dimensionament."},
        ),
        200,
    )

    solutions = expect(await client.get(url), 200)
    print(f"  {len(solutions)} solucions tecniques validades")
    return solutions


#: L'oferta de la demo. Preus plausibles i dins del pressupost del plec
#: (240.000 EUR sense IVA): no busca marges perfectes, busca ser llegible.
DEMO_SECTIONS: tuple[tuple[str, tuple[dict, ...]], ...] = (
    (
        "Equipament",
        (
            {
                "concept": "Commutadors d'acces gestionats",
                "quantity": "6",
                "pricing_basis": "one_off",
                "offered_unit_price_ex_vat": "1850.00",
                "unit_cost_ex_vat": "1420.00",
                "vat_rate": "21.00",
            },
            {
                "concept": "Terminals optics d'usuari (ONT)",
                "quantity": "40",
                "pricing_basis": "per_unit",
                "offered_unit_price_ex_vat": "95.00",
                "unit_cost_ex_vat": "68.00",
                "tender_max_unit_price_ex_vat": "110.00",
                "vat_rate": "21.00",
            },
        ),
    ),
    (
        "Implantacio",
        (
            {
                "concept": "Desplegament de fibra i fusions",
                "quantity": "1",
                "pricing_basis": "one_off",
                "offered_unit_price_ex_vat": "78000.00",
                "unit_cost_ex_vat": "61000.00",
                "vat_rate": "21.00",
                "link": True,
            },
            {
                "concept": "Posada en servei i documentacio",
                "quantity": "1",
                "pricing_basis": "one_off",
                "offered_unit_price_ex_vat": "9500.00",
                "unit_cost_ex_vat": "7200.00",
                "vat_rate": "21.00",
            },
        ),
    ),
    (
        "Serveis recurrents",
        (
            {
                "concept": "Manteniment preventiu i correctiu",
                "quantity": "1",
                "pricing_basis": "monthly",
                "offered_unit_price_ex_vat": "1450.00",
                "unit_cost_ex_vat": "1010.00",
                "vat_rate": "21.00",
            },
            {
                "concept": "Suport 24x7 amb acord de nivell de servei",
                "quantity": "1",
                "pricing_basis": "monthly",
                "offered_unit_price_ex_vat": "620.00",
                "unit_cost_ex_vat": "430.00",
                "vat_rate": "21.00",
            },
        ),
    ),
)


async def seed_demo_economic(client, base: str, workspace_id: str, solutions: list[dict]) -> dict:  # type: ignore[no-untyped-def]
    """L'oferta economica de la demo, pels serveis d'S5.

    Cada mutacio llegeix la revisio vigent i l'envia com a `expected_revision`,
    com hauria de fer qualsevol client: el seed no te cap privilegi sobre la
    concurrencia.
    """
    url = f"{base}/workspaces/{workspace_id}/economic-offer"
    opened = expect(await client.post(url), 200)
    offer = opened["offer"]
    if offer["lots"] and offer["lots"][0]["sections"]:
        print(f"  l'oferta ja hi era ({offer['lots'][0]['label']})")
        return offer

    lot = offer["lots"][0]
    offer = expect(
        await client.patch(
            f"{url}/lots/{lot['id']}",
            json={
                "duration_months": DEMO_DURATION_MONTHS,
                "expected_revision": offer["revision"],
            },
        ),
        200,
    )

    lligada = next((s for s in solutions if s.get("requirement_id")), None)
    for title, lines in DEMO_SECTIONS:
        offer = expect(
            await client.post(
                f"{url}/lots/{lot['id']}/sections",
                json={"title": title, "expected_revision": offer["revision"]},
            ),
            200,
        )
        section = offer["lots"][0]["sections"][-1]
        for line in lines:
            payload = {k: v for k, v in line.items() if k != "link"}
            payload["expected_revision"] = offer["revision"]
            if line.get("link") and lligada is not None:
                # La linia que respon a la memoria tecnica: la pantalla ha de
                # poder ensenyar que aquest import respon a aquella solucio.
                payload["requirement_id"] = lligada["requirement_id"]
                payload["technical_solution_id"] = lligada["id"]
            offer = expect(
                await client.post(f"{url}/sections/{section['id']}/lines", json=payload), 200
            )

    totals = offer["totals"]
    limit = offer["lots"][0]["limit"]
    print(
        f"  oferta: {len(offer['lots'][0]['sections'])} seccions, "
        f"{totals['line_count']} linies, {totals['total_revenue']} EUR"
        f" (maxim del plec: {limit['tender_max']})"
    )
    if limit["exceeds_lot_max"]:
        raise DemoStepFailed("L'oferta de demo supera el pressupost del plec.")
    return offer


async def confirm_demo_scope(client, base: str, workspace_id: str) -> dict:  # type: ignore[no-untyped-def]
    """Una persona diu que ha revisat quins requisits necessiten solucio (C-14).

    Va DESPRES de crear les solucions i de fixar els lots oferts: l'empremta
    cobreix les dues coses, i confirmar abans deixaria la revisio caducada tot
    seguit.
    """
    final = expect(
        await client.post(f"{base}/workspaces/{workspace_id}/technical-scope/review"), 200
    )
    tecnic = final["technical"]
    if not tecnic["scope_reviewed"] or tecnic["scope_stale"]:
        raise DemoStepFailed(
            "L'abast tecnic no ha quedat revisat i vigent: "
            f"reviewed={tecnic['scope_reviewed']} stale={tecnic['scope_stale']}"
        )
    print(
        f"  abast revisat: {tecnic['requirements_in_scope']} requisits, "
        f"caducat={tecnic['scope_stale']}"
    )
    return final


async def run_tender_flow(  # type: ignore[no-untyped-def]
    client,
    base: str,
    *,
    label: str,
    tender_id: str,
    freeze_package_at_end: bool = False,
) -> dict[str, str]:
    """Documents, analisi, compliment, candidatura, GO, dossier i Sprint 2.

    Per defecte **no congela cap paquet**: deixa la candidatura a punt i prou.
    Congelar es un pas que la demo ha de poder ensenyar en directe, i un default
    que el fes sol tornaria a deixar una fotografia que ningu no ha demanat.
    Nomes la segona licitacio el demana explicitament, per ensenyar que un
    paquet sostingut per una simulacio no es pot aprovar.
    """
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

    step(f"{label} | solucio tecnica")
    solutions = await seed_demo_technical(client, base, workspace_id, tender_id)

    step(f"{label} | oferta economica")
    await seed_demo_economic(client, base, workspace_id, solutions)

    # L'ULTIM pas dels tres: l'empremta de l'abast cobreix els requisits, els
    # lots oferts i quines solucions s'han declarat necessaries.
    step(f"{label} | revisio de l'abast tecnic")
    final = await confirm_demo_scope(client, base, workspace_id)

    step(f"{label} | porta final")
    if not final["ready_for_final_review"]:
        raise DemoStepFailed(
            f"{label}: la candidatura no esta a punt "
            f"(blocadors {[f['code'] for f in final['findings'] if f['severity'] == 'blocker']})."
        )
    print("  a punt per a la revisio final, sense blocadors")

    if not freeze_package_at_end:
        # La candidatura estrella es queda just ABANS de congelar: la demo ha de
        # poder ensenyar en directe congelar, veure el manifest v2 i provar
        # d'aprovar. Congelar-la aqui li trauria el pas mes interessant.
        print("  paquet: no se'n congela cap (queda per fer a la demo)")
        return {
            "label": label,
            "tender_id": tender_id,
            "document_id": document_id,
            "workspace_id": workspace_id,
            "package_id": None,
        }

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
