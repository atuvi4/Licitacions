"""L'empremta no depen de si un valor ve de la sessio que el carrega (`20`) o de la
base (`20.0` d'una columna Float): una relectura ha de donar el mateix hash."""

from __future__ import annotations

import pytest

from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.tenders import Tender
from app.services import tender_analysis as analysis
from tests.fixtures.company_twin import build_company_twin
from tests.test_tender_gold_lescala import CASE

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def test_structure_fingerprint_survives_a_reload_from_the_database(db, session_factory):
    loaded = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    tender_id = loaded.tender.id
    in_session = analysis.structure_fingerprint(
        (await analysis.load_structure_view(db, await db.get(Tender, tender_id)))[0]
    )
    await db.commit()

    async with session_factory() as fresh:
        view, _ = await analysis.load_structure_view(fresh, await fresh.get(Tender, tender_id))
        from_db = analysis.structure_fingerprint(view)
    assert from_db == in_session


async def test_knowledge_fingerprint_survives_a_reload_from_the_database(
    db, session_factory, tenant_a
):
    _, company = tenant_a
    await build_company_twin(db, company)
    in_session = (await analysis.load_company_view(db, company.id)).knowledge_fingerprint
    await db.commit()

    async with session_factory() as fresh:
        from_db = (await analysis.load_company_view(fresh, company.id)).knowledge_fingerprint
    assert from_db == in_session
