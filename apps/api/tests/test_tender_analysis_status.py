"""Font canonica de (analisi vigent, caducada): `tender_analysis.current_analysis`.

El router `GET /analysis/current` i les decisions la comparteixen. Dades sintetiques.
"""

from __future__ import annotations

import pytest

from app.knowledge import vocabulary
from app.models.enums import RequirementCategory
from app.models.tenders import Tender
from app.services import tender_analysis as analysis
from tests import test_tender_scope_models as m
from tests.test_tender_analysis_engine import _requirement, _synthetic_tender, _twin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


def _base(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis"


async def test_without_a_run_the_status_is_none_and_not_stale(db, tenant_a, client_a):
    _, company = tenant_a
    tender = await m._public_tender(db, title="Plec sintetic sense analisi")
    await db.commit()
    status = await analysis.current_analysis(db, company.id, tender.id)
    assert status == analysis.AnalysisStatus(run=None, stale=False)
    assert (await client_a.get(f"{_base(company.id, tender.id)}/current")).status_code == 404


async def test_a_fresh_run_is_current_and_not_stale(db, tenant_a, client_a):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    await db.commit()
    company_id, tender_id = company.id, tender.id
    posted = await client_a.post(f"{_base(company_id, tender_id)}/runs")
    assert posted.status_code == 201, posted.text
    status = await analysis.current_analysis(db, company_id, tender_id)
    assert status.run is not None and status.stale is False
    current = await client_a.get(f"{_base(company_id, tender_id)}/current")
    assert current.json()["id"] == str(status.run.id) == posted.json()["id"]
    assert current.json()["stale"] is status.stale


async def test_a_material_input_change_makes_the_status_stale_like_the_api(db, tenant_a, client_a):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    await db.commit()
    company_id, tender_id = company.id, tender.id
    first = (await client_a.post(f"{_base(company_id, tender_id)}/runs")).json()
    await _requirement(
        db,
        await db.get(Tender, tender_id),
        "Requisit manual nou",
        RequirementCategory.TECHNICAL_REQUIREMENT,
    )
    await db.commit()
    status = await analysis.current_analysis(db, company_id, tender_id)
    current = await client_a.get(f"{_base(company_id, tender_id)}/current")
    assert status.run is not None and str(status.run.id) == first["id"]
    assert status.stale is True
    assert current.json()["id"] == first["id"] and current.json()["stale"] is True
