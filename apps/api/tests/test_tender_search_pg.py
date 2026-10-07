"""Cerca del cataleg a PostgreSQL: `relevance` amb `similarity` i l'index trigram.

Nomes amb `--postgres`. El harness crea l'schema aillat amb `create_all` (sense
l'index de 0056, que es nomes de migracio): aqui s'executa la funcio real
`upgrade()` de 0056 sobre l'schema, i les connexions de l'API veuen tambe
`public`, on pot viure l'extensio `pg_trgm` si ja hi era.
"""

from __future__ import annotations

import importlib.util
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

from app.api.v1.tender_filters import TenderFilters
from app.models.enums import ContractType, ProcedureType, SourceType, TenderStatus
from app.models.sources import Source
from app.models.tenders import Tender
from app.tenders.search import build_search_text

pytestmark = pytest.mark.postgres

API_ROOT = Path(__file__).resolve().parents[1]
INDEX = "ix_tenders_search_text_trgm"


def _load_0056():
    path = API_ROOT / "alembic" / "versions" / "0056_radar_search.py"
    spec = importlib.util.spec_from_file_location("mig_0056_search", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def trgm(engine):
    """L'index real de 0056 a l'schema aillat, i `public` al search_path de l'API."""
    m56 = _load_0056()
    async with engine.begin() as conn:
        schema = (await conn.execute(sa.text("SELECT current_schema()"))).scalar_one()
        await conn.execute(
            sa.text("SELECT set_config('search_path', current_schema() || ',public', true)")
        )

        def upgrade(connection):
            with Operations.context(MigrationContext.configure(connection)):
                m56.upgrade()

        await conn.run_sync(upgrade)

    def _search_path(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute(f'SET search_path TO "{schema}", public')
        cursor.close()

    # Les connexions ja obertes tenen el search_path del harness: es tanquen.
    await engine.dispose()
    sa.event.listen(engine.sync_engine, "connect", _search_path)
    try:
        yield schema
    finally:
        sa.event.remove(engine.sync_engine, "connect", _search_path)


async def _source(db) -> Source:
    source = Source(
        code="placsp_agregadas",
        name="PLACSP · plataformes agregades",
        source_type=SourceType.SYNDICATION_ATOM,
        enabled=True,
    )
    db.add(source)
    await db.flush()
    return source


def _row(
    source: Source,
    index: int,
    title: str,
    published: date | None,
    description: str | None = None,
) -> dict:
    now = datetime(2026, 10, 1, tzinfo=UTC) - timedelta(minutes=index)
    return {
        "source_id": source.id,
        "external_id": f"urn:pg:{index}",
        "title": title,
        "description": description,
        "procedure_type": ProcedureType.OPEN,
        "contract_type": ContractType.SERVICES,
        "status": TenderStatus.OPEN,
        "currency": "EUR",
        "publication_date": published,
        "first_seen_at": now,
        "last_seen_at": now,
        "search_text": build_search_text(
            SimpleNamespace(title=title, expediente_number=None, description=description),
            None,
            [],
        ),
    }


async def test_relevance_amb_similarity(db, client_a, tenant_a, trgm):
    _, company = tenant_a
    source = await _source(db)
    exacta = "Fibra òptica"
    llarga = (
        "Contracte de manteniment integral de les instal·lacions municipals amb "
        "fibra òptica, enllumenat, climatitzacio i altres serveis de l'ajuntament"
    )
    await db.execute(
        sa.insert(Tender),
        [
            _row(source, 1, exacta, date(2026, 9, 1)),
            _row(source, 2, llarga, date(2026, 9, 20)),  # mes recent
            _row(source, 3, "Neteja viaria", date(2026, 9, 25)),
        ],
    )
    await db.commit()

    for url in ("/api/v1/tenders", f"/api/v1/companies/{company.id}/matches"):

        async def titles(url=url, **params) -> list[str]:
            response = await client_a.get(url, params=params)
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            return [(item.get("tender") or item)["title"] for item in items]

        # Per defecte, neutral: la mes recent primer.
        assert await titles(q="FIBRA OPTICA") == [llarga, exacta]
        # `relevance` = similarity(search_text, q normalitzada) desc.
        assert await titles(q="FIBRA OPTICA", sort="relevance") == [exacta, llarga]
        # Sense q, `relevance` cau a l'ordre neutral.
        assert (await titles(sort="relevance"))[0] == "Neteja viaria"


async def test_explain_usa_l_index_trgm(db, trgm):
    source = await _source(db)
    vocabulary = (
        "servei manteniment subministrament obres neteja enllumenat xarxa fibra "
        "telecomunicacio ajuntament consell comarcal diputacio escola edifici "
        "municipal public contracte lot via parc aigua residus transport"
    ).split()
    rng = random.Random(3)
    rows = []
    for index in range(5000):
        words = rng.sample(vocabulary, 6)
        if index % 1000 == 0:
            words.append("radioenllac")  # 5 files de 5.000
        # Una descripcio de mida realista (resum de l'anunci): la fila ocupa el
        # que ocupa al cataleg real i el cost d'un seq scan es el que toca.
        description = " ".join(rng.choices(vocabulary, k=60))
        rows.append(
            _row(source, index, " ".join(words).capitalize(), date(2026, 9, 1), description)
        )
    await db.execute(sa.insert(Tender), rows)
    await db.commit()
    # Les insercions noves d'un GIN van a la llista pendent (`fastupdate`), que
    # l'autovacuum buida en produccio. Aqui es buida explicitament: el que es
    # comprova es el pla en regim, no el d'una taula acabada de carregar.
    await db.execute(sa.text(f"SELECT gin_clean_pending_list('{INDEX}'::regclass)"))
    await db.execute(sa.text("ANALYZE tenders"))

    statement = sa.select(Tender.id).where(*TenderFilters(q="Radioenllaç").criteria())
    sql = str(statement.compile(dialect=db.bind.dialect, compile_kwargs={"literal_binds": True}))
    assert "search_text LIKE '%radioenllac%'" in sql
    plan = "\n".join((await db.execute(sa.text(f"EXPLAIN {sql}"))).scalars().all())
    assert INDEX in plan, plan  # sense forcar el planificador (enable_seqscan intacte)

    found = (await db.execute(statement)).scalars().all()
    assert len(found) == 5
