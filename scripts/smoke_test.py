"""Smoke test del cami real: API + PostgreSQL + object storage S3.

No substitueix la suite de pytest (que corre sobre SQLite i storage local):
serveix per demostrar, de manera reproduible, que la infraestructura REAL
funciona d'extrem a extrem.

    # contra docker compose (a traves del proxy de Next.js)
    python scripts/smoke_test.py

    # contra l'API directament
    python scripts/smoke_test.py --base-url http://localhost:8000/api/v1

## Sobre quines dades treballa

**Cap prova de mutacio toca el tenant demo.** Cada execucio es crea la seva
propia empresa efimera ("Smoke Test <uuid>") amb el seu usuari, hi fa tot el
CRUD, i la destrueix en acabar dins d'un `finally` — tambe si una comprovacio
falla pel cami. Aixo no es un detall d'higiene: la versio anterior treballava
sobre l'empresa demo i li va deixar el nom comercial canviat a "Smoke 9b235f49"
i un servei, un projecte i un document residuals.

A mes, l'execucio fotografia el tenant demo abans i despres i falla si ha
canviat res. Un test que embruta les dades que hauria de deixar intactes no es
un test.

Comprova, en aquest ordre:

    entorn no es produccio -> tenant efimer -> login -> CRUD de les entitats
    del perfil -> pujada d'un PDF -> objecte realment al bucket -> metadata a
    la base de dades -> descarrega byte a byte -> SHA-256 -> deduplicacio ->
    enllac dada->evidencia -> destruccio del tenant -> cap orfe enlloc

Modes:

    (per defecte)         CRUD complet sobre un tenant efimer, i el destrueix
    --keep-tenant         no el destrueix: el deixa per a `--check-persistence`
    --check-persistence   comprova que el tenant anterior ha sobreviscut i el destrueix
    --check-ingestion     cami PLACSP -> raw store -> model canonic -> feed (nomes lectura)
    --check-prefit        pre-fit sobre PostgreSQL real (nomes lectura + recalcul)

Sortida: codi 0 si tot passa, 1 al primer error, 2 si l'entorn es de produccio.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

# `app` viu dins d'apps/api i aquest script s'executa des de l'arrel del repo.
API_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.tenant_ops import (  # noqa: E402
    EphemeralTenant,
    ProductionGuardError,
    TenantSnapshot,
    assert_local_not_production,
    assert_mutations_allowed,
    create_ephemeral,
    destroy_tenant,
    session_scope,
    snapshot,
)

STATE_FILE = Path(__file__).resolve().parent / ".smoke_state.json"

PASS = "  [OK]  "
FAIL = "  [KO]  "
WARN = "  [--]  "

DEMO_COMPANY_NAME = "Empresa Demo Telecom SL"

# PDF minim valid, amb contingut unic per execucio.
PDF_TEMPLATE = b"%%PDF-1.4 smoke test %s\ntrailer << /Root 1 0 R >>\n%%%%EOF\n"


class SmokeError(Exception):
    pass


class Checker:
    def __init__(self) -> None:
        self.failures = 0

    def check(self, description: str, condition: bool, detail: str = "") -> None:
        if condition:
            print(f"{PASS}{description}" + (f" — {detail}" if detail else ""))
        else:
            self.failures += 1
            print(f"{FAIL}{description}" + (f" — {detail}" if detail else ""))
            raise SmokeError(description)


def s3_client() -> Any | None:
    """Client cap al bucket, per mirar-lo pel seu compte i no fiar-nos de l'API."""
    endpoint = os.getenv("STORAGE_ENDPOINT_URL")
    bucket = os.getenv("STORAGE_BUCKET")
    if not bucket:
        return None
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return None
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=os.getenv("STORAGE_REGION", "us-east-1"),
        aws_access_key_id=os.getenv("STORAGE_ACCESS_KEY"),
        aws_secret_access_key=os.getenv("STORAGE_SECRET_KEY"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def object_exists(client: Any, bucket: str, key: str) -> bool:
    from botocore.exceptions import ClientError

    try:
        client.head_object(Bucket=bucket, Key=key)
    except ClientError:
        return False
    return True


def find_key_by_size_and_prefix(client: Any, bucket: str, company_id: str, size: int) -> str | None:
    """L'API no exposa la storage_key (i esta be que no ho faci): la busquem al bucket."""
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"companies/{company_id}/"):
        for item in page.get("Contents", []):
            if item["Size"] == size:
                return item["Key"]
    return None


def find_object_by_record_id(client: Any, bucket: str, record_id: str) -> str | None:
    """La clau d'storage no s'exposa a l'API (i esta be): es busca pel seu nom."""
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="sources/placsp/"):
        for item in page.get("Contents", []):
            if item["Key"].endswith(f"{record_id}.xml"):
                return item["Key"]
    return None


# ---------------------------------------------------------------------------
# Entorn i tenant efimer
# ---------------------------------------------------------------------------


def remote_environment(client: httpx.Client) -> str | None:
    """Entorn que declara el backend.

    Es llegeix de `GET /config`, no de la URL ni del nom de la base de dades:
    qualsevol pot apuntar un `DATABASE_URL` que sembli de desenvolupament cap a
    una instancia real. Requereix sessio, aixi que es consulta just despres del
    login i abans de qualsevol mutacio via HTTP.
    """
    try:
        response = client.get("/config")
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    return response.json().get("environment")


async def _provision() -> EphemeralTenant:
    async with session_scope() as session:
        return await create_ephemeral(session)


async def _teardown(tenant: EphemeralTenant) -> Any:
    async with session_scope() as session:
        return await destroy_tenant(
            session,
            uuid.UUID(tenant.company_id),
            user_id=uuid.UUID(tenant.user_id),
        )


async def _demo_snapshot() -> TenantSnapshot | None:
    """Radiografia del tenant demo, si existeix. Serveix per provar que no el toquem."""
    import sqlalchemy as sa

    from app.models.identity import Company

    async with session_scope() as session:
        company_id = (
            await session.execute(
                sa.select(Company.id).where(Company.legal_name == DEMO_COMPANY_NAME)
            )
        ).scalar_one_or_none()
        if company_id is None:
            return None
        return await snapshot(session, company_id)


def teardown_and_report(checker: Checker, tenant: EphemeralTenant, s3: Any, bucket: str) -> None:
    """Destrueix el tenant i comprova que no queda cap rastre.

    Es crida des d'un `finally`: si una comprovacio ha fallat abans, la neteja
    s'ha de fer igualment. Per aixo aquestes comprovacions no fan servir
    `checker.check` (que llenca), sino que reporten i acumulen.
    """
    print("\n  Neteja del tenant efimer")
    try:
        report = asyncio.run(_teardown(tenant))
    except Exception as error:  # noqa: BLE001 - un error aqui deixaria orfes
        checker.failures += 1
        print(f"{FAIL}No s'ha pogut destruir el tenant efimer — {error}")
        return

    print(f"{PASS}Tenant esborrat — {report.total_rows} files de {len(report.rows_deleted)} taules")

    for label, condition, detail in (
        ("Cap fila orfe a la base de dades", not report.leftovers, str(report.leftovers or "")),
        ("L'usuari efimer s'ha esborrat", report.user_deleted, ""),
        (
            "Cap objecte orfe a l'storage",
            not report.storage_objects_failed,
            str(report.storage_objects_failed or ""),
        ),
    ):
        if condition:
            print(f"{PASS}{label}" + (f" — {detail}" if detail else ""))
        else:
            checker.failures += 1
            print(f"{FAIL}{label}" + (f" — {detail}" if detail else ""))

    if report.storage_objects_deleted:
        print(f"{PASS}Objectes esborrats de l'storage — {report.storage_objects_deleted}")

    # Segona opinio: mirem el bucket pel nostre compte, no ens fiem del report.
    if s3 and bucket:
        paginator = s3.get_paginator("list_objects_v2")
        remaining = [
            item["Key"]
            for page in paginator.paginate(
                Bucket=bucket, Prefix=f"companies/{tenant.company_id}/"
            )
            for item in page.get("Contents", [])
        ]
        if remaining:
            checker.failures += 1
            print(f"{FAIL}El bucket encara te objectes del tenant — {len(remaining)}")
        else:
            print(f"{PASS}El bucket ja no te cap objecte del tenant")


def check_demo_untouched(checker: Checker, before: TenantSnapshot | None) -> None:
    """El tenant demo ha de sortir de l'execucio exactament com hi va entrar."""
    if before is None:
        print(f"{WARN}No hi ha tenant demo: se salta la comprovacio d'integritat.")
        return
    after = asyncio.run(_demo_snapshot())
    if after is None:
        checker.failures += 1
        print(f"{FAIL}El tenant demo ha desaparegut durant l'execucio")
        return
    problems = before.diff(after)
    if problems:
        checker.failures += 1
        print(f"{FAIL}El tenant demo ha canviat — {'; '.join(problems)}")
    else:
        print(
            f"{PASS}El tenant demo no s'ha tocat — {before.total_rows} files, "
            f"{len(before.document_hashes)} documents"
        )


# ---------------------------------------------------------------------------
# CRUD complet sobre un tenant efimer
# ---------------------------------------------------------------------------


def run(base_url: str, keep_tenant: bool) -> int:
    checker = Checker()
    client = httpx.Client(base_url=base_url, timeout=60.0, follow_redirects=True)
    s3 = s3_client()
    bucket = os.getenv("STORAGE_BUCKET", "")

    print(f"\nSmoke test contra {base_url}")
    print(f"Bucket S3: {bucket or '(no configurat: se salten les comprovacions directes)'}")

    # Primera barrera, abans de tocar res: el proces que escriu a la base de
    # dades es aquest, aixi que el seu ENVIRONMENT es el que mana per als
    # escriptures directes.
    assert_local_not_production()

    demo_before = asyncio.run(_demo_snapshot())
    tenant = asyncio.run(_provision())
    print(f"Tenant efimer: {tenant.legal_name} ({tenant.company_id})\n")

    company_id = tenant.company_id
    base = f"/companies/{company_id}"

    try:
        # ---------------------------------------------------------------- login
        response = client.post(
            "/auth/login", json={"email": tenant.email, "password": tenant.password}
        )
        checker.check(
            "Login amb l'usuari efimer", response.status_code == 200, f"HTTP {response.status_code}"
        )
        # Que l'usuari que acabem de crear a la base de dades pugui iniciar
        # sessio demostra que el backend i aquest script parlen amb la MATEIXA
        # base de dades. Sense aixo, la resta del test no voldria dir res.
        checker.check(
            "El backend comparteix la base de dades amb aquest script",
            response.json()["memberships"][0]["company_id"] == company_id,
        )

        # ------------------------------------------ segona barrera: entorn remot
        environment = remote_environment(client)
        assert_mutations_allowed(environment)
        checker.check("El backend no es produccio", True, f"ENVIRONMENT={environment}")

        # ------------------------------------------------------------ CRUD real
        marker = uuid.uuid4().hex[:8]

        response = client.patch(base, json={"trade_name": f"Smoke {marker}"})
        checker.check("Editar empresa", response.status_code == 200)

        created: dict[str, str] = {}
        for label, path, payload in (
            ("servei", "/services", {"name": f"Servei smoke {marker}"}),
            ("CPV", "/cpv-preferences", {"cpv_code": "50324100", "description": "Manteniment de sistemes"}),
            ("exercici", "/financial-years", {"year": 1990, "revenue": "123456.78"}),
            ("certificacio", "/certifications", {"name": f"ISO smoke {marker}"}),
            ("projecte", "/projects", {"name": f"Projecte smoke {marker}", "amount": "200000"}),
            ("persona", "/people", {"first_name": "Smoke", "last_name": marker}),
        ):
            response = client.post(base + path, json=payload)
            checker.check(
                f"Crear {label}", response.status_code == 201, f"HTTP {response.status_code}"
            )
            created[label] = response.json()["id"]

        response = client.post(
            f"{base}/people/{created['persona']}/qualifications", json={"name": "Titulacio smoke"}
        )
        checker.check("Crear titulacio", response.status_code == 201)

        # ------------------------------------------------------- pujada del PDF
        payload = PDF_TEMPLATE % marker.encode()
        digest = hashlib.sha256(payload).hexdigest()

        response = client.post(
            f"{base}/documents",
            files={"file": (f"smoke-{marker}.pdf", payload, "application/pdf")},
            data={
                "name": f"Document smoke {marker}",
                "category": "project_evidence",
                "entity_type": "company_project",
                "entity_id": created["projecte"],
                "relationship_type": "certificate_of_good_execution",
            },
        )
        checker.check("Pujar PDF", response.status_code == 201, f"HTTP {response.status_code}")
        document = response.json()
        document_id = document["id"]

        checker.check("SHA-256 calculat pel backend", document["sha256"] == digest, digest[:16] + "...")
        checker.check("Mida registrada", document["file_size"] == len(payload), f"{len(payload)} bytes")
        checker.check("Enllac dada -> evidencia creat", len(document["links"]) == 1)
        checker.check("La storage_key no s'exposa a l'API", "storage_key" not in document)

        # --------------------------------------------- l'objecte hi es de debo?
        storage_key = None
        if s3 and bucket:
            storage_key = find_key_by_size_and_prefix(s3, bucket, company_id, len(payload))
            checker.check("Objecte present al bucket", storage_key is not None, str(storage_key))
            raw = s3.get_object(Bucket=bucket, Key=storage_key)["Body"].read()
            checker.check(
                "El contingut del bucket coincideix byte a byte",
                hashlib.sha256(raw).hexdigest() == digest,
            )

        # ----------------------------------------------------------- descarrega
        response = client.get(f"{base}/documents/{document_id}/download")
        checker.check("Descarrega", response.status_code == 200)
        checker.check(
            "Descarrega identica a l'original",
            hashlib.sha256(response.content).hexdigest() == digest,
        )

        # -------------------------------------------------------- deduplicacio
        response = client.post(
            f"{base}/documents",
            files={"file": (f"copia-{marker}.pdf", payload, "application/pdf")},
        )
        checker.check(
            "Duplicat rebutjat pel SHA-256",
            response.status_code == 409
            and response.json()["error"]["code"] == "duplicate_document",
            f"HTTP {response.status_code}",
        )
        checker.check(
            "L'error identifica el document existent",
            response.json()["error"]["details"]["existing_document_id"] == document_id,
        )

        # ------------------------------------------------------------ evidencia
        response = client.get(f"{base}/evidence/company_project/{created['projecte']}")
        checker.check(
            "L'evidencia es consultable des de la dada",
            response.status_code == 200 and len(response.json()) == 1,
        )

        # ------------------------------------------------------- esborrat per API
        # El cami d'esborrat tambe s'ha de provar: el `finally` esborra per
        # base de dades, que es una altra cosa.
        response = client.delete(f"{base}/documents/{document_id}")
        checker.check("Esborrar el document per l'API", response.status_code == 204)
        checker.check(
            "Ja no hi es a la base de dades",
            client.get(f"{base}/documents/{document_id}").status_code == 404,
        )
        if s3 and bucket and storage_key:
            checker.check(
                "L'objecte tambe ha desaparegut del bucket",
                not object_exists(s3, bucket, storage_key),
            )

    except SmokeError:
        return 1
    except ProductionGuardError as error:
        print(f"\nBLOQUEJAT: {error}")
        return 2
    finally:
        client.close()
        if keep_tenant:
            STATE_FILE.write_text(
                json.dumps(
                    {
                        "company_id": tenant.company_id,
                        "user_id": tenant.user_id,
                        "email": tenant.email,
                        "password": tenant.password,
                        "legal_name": tenant.legal_name,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            print(
                f"\n  Tenant conservat a {STATE_FILE.name}. "
                "Executa `--check-persistence` per verificar-lo i esborrar-lo."
            )
        else:
            teardown_and_report(checker, tenant, s3, bucket)
        check_demo_untouched(checker, demo_before)

    print(f"\n{'Tot correcte.' if checker.failures == 0 else 'Hi ha errors.'}")
    return 1 if checker.failures else 0


def verify_persistence(base_url: str) -> int:
    """Comprova que el tenant efimer ha sobreviscut a un reinici, i el destrueix."""
    checker = Checker()
    if not STATE_FILE.exists():
        print(
            f"{FAIL}No hi ha estat previ ({STATE_FILE.name}). "
            "Executa primer `smoke_test.py --keep-tenant`."
        )
        return 1
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    tenant = EphemeralTenant(**state)

    client = httpx.Client(base_url=base_url, timeout=60.0, follow_redirects=True)
    s3 = s3_client()
    bucket = os.getenv("STORAGE_BUCKET", "")
    demo_before = asyncio.run(_demo_snapshot())

    print(f"\nComprovant que {tenant.legal_name} ha sobreviscut al reinici\n")
    try:
        response = client.post(
            "/auth/login", json={"email": tenant.email, "password": tenant.password}
        )
        checker.check("L'usuari efimer segueix podent entrar", response.status_code == 200)

        base = f"/companies/{tenant.company_id}"
        services = client.get(f"{base}/services").json()
        checker.check("Els serveis persisteixen", len(services) > 0, f"{len(services)} serveis")

        projects = client.get(f"{base}/projects").json()
        checker.check("Els projectes persisteixen", len(projects) > 0)
    except SmokeError:
        return 1
    finally:
        client.close()
        teardown_and_report(checker, tenant, s3, bucket)
        check_demo_untouched(checker, demo_before)
        STATE_FILE.unlink(missing_ok=True)

    print(f"\n{'Tot correcte.' if checker.failures == 0 else 'Hi ha errors.'}")
    return 1 if checker.failures else 0


# ---------------------------------------------------------------------------
# Comprovacions de nomes lectura (no creen cap tenant)
# ---------------------------------------------------------------------------


def run_ingestion_checks(base_url: str, email: str, password: str) -> int:
    """Comprova el cami real d'ingestio: font -> raw store -> canonic -> feed."""
    checker = Checker()
    client = httpx.Client(base_url=base_url, timeout=60.0, follow_redirects=True)
    s3 = s3_client()
    bucket = os.getenv("STORAGE_BUCKET", "")

    print(f"\nSmoke test d'ingestio contra {base_url}\n")
    try:
        response = client.post("/auth/login", json={"email": email, "password": password})
        checker.check("Login", response.status_code == 200)

        runs = client.get("/ingestion/runs?limit=5").json()
        checker.check("Hi ha execucions d'ingesta registrades", runs["total"] > 0)
        last = runs["items"][0]
        checker.check(
            "L'ultima execucio ha acabat be",
            last["status"] in {"success", "partial"},
            f"{last['status']}, {last['records_fetched']} entrades",
        )
        checker.check(
            "L'execucio esta auditada",
            last["finished_at"] is not None and last["duration_ms"] is not None,
        )

        feed = client.get("/tenders?limit=5&sort=amount").json()
        checker.check("El feed retorna licitacions", feed["total"] > 0, f"{feed['total']} en total")
        checker.check("Cap licitacio porta company_id", "company_id" not in feed["items"][0])

        with_lots = client.get("/tenders?has_lots=true&limit=1").json()
        checker.check("Hi ha expedients amb lots", with_lots["total"] > 0)
        tender_id = with_lots["items"][0]["id"]

        detail = client.get(f"/tenders/{tender_id}").json()
        checker.check("La fitxa carrega", bool(detail["title"]))
        checker.check("La fitxa te lots", len(detail["lots"]) > 0, f"{len(detail['lots'])} lots")
        checker.check("La fitxa te CPV", len(detail["cpvs"]) > 0)
        checker.check(
            "La fitxa te organisme contractant", detail["buyer"] is not None
        )

        lots = client.get(f"/tenders/{tender_id}/lots").json()
        documents = client.get(f"/tenders/{tender_id}/documents").json()
        checker.check("Endpoint de lots", len(lots) == len(detail["lots"]))
        checker.check(
            "Els documents nomes son metadata i URL",
            all(document["url"].startswith("http") for document in documents),
            f"{len(documents)} documents",
        )

        trace = detail["traceability"]
        checker.check("La fitxa mostra la font", trace["source_code"] == "PLACSP")
        checker.check("Hi ha SHA-256 del XML original", len(trace["raw_hash"]) == 64)
        checker.check("Hi ha versio del parser", bool(trace["parser_version"]))

        revisions = client.get(f"/tenders/{tender_id}/revisions").json()
        checker.check("Hi ha historial de revisions", len(revisions) >= 1)

        if s3 and bucket:
            key = find_object_by_record_id(s3, bucket, trace["latest_source_record_id"])
            checker.check("El XML original es al bucket", key is not None, str(key))
            raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            checker.check(
                "El SHA-256 del bucket coincideix amb el de la base de dades",
                hashlib.sha256(raw).hexdigest() == trace["raw_hash"],
            )
            checker.check(
                "El raw es guarda fora de l'espai de noms corporatiu",
                key.startswith("sources/placsp/"),
            )
    except SmokeError:
        return 1
    finally:
        client.close()

    print("\nTot correcte.")
    return 0


def run_prefit_checks(base_url: str, email: str, password: str) -> int:
    """Comprova el pre-fit sobre la infraestructura real.

    Els tests de `pytest` corren sobre SQLite: aixo verifica el que aquella
    suite no pot verificar, sobretot que la columna JSON de motius i les
    puntuacions sobreviuen a PostgreSQL.

    Recalcular no crea ni esborra dades del perfil: nomes reescriu les files de
    pre-fit del tenant, que son derivades i es tornen a generar igual.
    """
    checker = Checker()
    client = httpx.Client(base_url=base_url, timeout=120.0, follow_redirects=True)

    print(f"\nSmoke test del pre-fit contra {base_url}\n")
    try:
        response = client.post("/auth/login", json={"email": email, "password": password})
        checker.check("Login", response.status_code == 200)
        company_id = response.json()["memberships"][0]["company_id"]
        base = f"/companies/{company_id}/matches"

        assert_mutations_allowed(remote_environment(client))

        recalculated = client.post(f"{base}/recalculate").json()
        checker.check(
            "El recalcul s'executa",
            recalculated["tenders_evaluated"] > 0,
            f"{recalculated['tenders_evaluated']} licitacions en {recalculated['duration_ms']} ms",
        )

        summary = client.get(f"{base}/summary").json()
        checker.check(
            "Totes les licitacions tenen pre-fit",
            summary["candidates"]
            + summary["low_fit"]
            + summary["excluded"]
            + summary["closed"]
            + summary["insufficient_data"]
            == summary["total_tenders"],
            f"{summary['total_tenders']} licitacions",
        )
        checker.check("Cap pre-fit queda desactualitzat despres del recalcul", summary["stale"] == 0)
        checker.check("La versio de regles queda registrada", bool(summary["rules_version"]))

        feed = client.get(f"{base}?sort=score&limit=5").json()
        checker.check("El feed retorna pre-fit", len(feed["items"]) > 0)
        top = feed["items"][0]["match"]
        checker.check(
            "Els motius sobreviuen a la columna JSON de PostgreSQL",
            len(top["factors"]) == 4
            and all(factor["reason_code"] for factor in top["factors"]),
        )
        checker.check(
            "Les dimensions son les quatre esperades",
            {factor["dimension"] for factor in top["factors"]}
            == {"cpv", "geography", "economic", "timing"},
        )

        excluded = client.get(f"{base}?match_status=excluded&limit=1").json()
        if excluded["items"]:
            match = excluded["items"][0]["match"]
            checker.check(
                "Una exclusio no inventa cap puntuacio",
                match["score"] is None and bool(match["exclusion_reason_code"]),
                match["exclusion_reason_code"],
            )

        # Els criteris de cerca han de donar el mateix als dos feeds.
        query = "nuts=ES51"
        public = client.get(f"/tenders?{query}&limit=1").json()
        personal = client.get(f"{base}?{query}&limit=1").json()
        checker.check(
            "El cataleg public i el feed amb pre-fit filtren igual",
            public["total"] == personal["total"],
            f"{public['total']} licitacions",
        )

        # Aillament: cap altra empresa ha de ser accessible.
        other = client.get("/companies/00000000-0000-0000-0000-000000000000/matches")
        checker.check(
            "Una empresa aliena retorna 404, no 403",
            other.status_code == 404,
            f"HTTP {other.status_code}",
        )
    except SmokeError:
        return 1
    except ProductionGuardError as error:
        print(f"\nBLOQUEJAT: {error}")
        return 2
    finally:
        client.close()

    print("\nTot correcte.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.getenv("SMOKE_BASE_URL", "http://localhost:3000/bff"),
        help="Arrel de l'API. Per defecte el proxy de Next.js.",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("SMOKE_EMAIL", "demo@licitacions.dev"),
        help="Nomes per als modes de lectura (--check-ingestion, --check-prefit).",
    )
    parser.add_argument("--password", default=os.getenv("SMOKE_PASSWORD", "Demo12345!"))
    parser.add_argument(
        "--keep-tenant",
        action="store_true",
        help="No destrueix el tenant efimer: el deixa per a --check-persistence.",
    )
    parser.add_argument(
        "--check-persistence",
        action="store_true",
        help="Verifica que el tenant de --keep-tenant ha sobreviscut, i l'esborra.",
    )
    parser.add_argument(
        "--check-ingestion",
        action="store_true",
        help="Nomes verifica el cami PLACSP -> raw store -> model canonic -> feed.",
    )
    parser.add_argument(
        "--check-prefit",
        action="store_true",
        help="Nomes verifica el pre-fit sobre PostgreSQL real.",
    )
    args = parser.parse_args()

    try:
        if args.check_ingestion:
            return run_ingestion_checks(args.base_url, args.email, args.password)
        if args.check_prefit:
            return run_prefit_checks(args.base_url, args.email, args.password)
        if args.check_persistence:
            return verify_persistence(args.base_url)
        return run(args.base_url, args.keep_tenant)
    except ProductionGuardError as error:
        print(f"BLOQUEJAT: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
