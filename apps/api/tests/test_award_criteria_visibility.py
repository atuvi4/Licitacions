"""Visibilitat dels endpoints de detall i revisio d'un criteri d'adjudicacio.

`GET /award-criteria/{id}` i `POST /award-criteria/{id}/review` es consultaven
nomes per id: qualsevol usuari amb sessio que en sabes l'UUID podia llegir i
revisar el criteri d'una licitacio privada d'una altra empresa. Ara apliquen la
mateixa regla que la lectura de l'analisi (`visible_to_user`): no visible = 404.
Els dos POST de revisio son RETIRATS: visible = 410 sense escriure res.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.requirements import AwardCriterion
from tests.test_tender_gold_lescala import CASE


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _loaded(db, owner_company_id=None):
    loaded = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    loaded.tender.owner_company_id = owner_company_id
    await db.commit()
    return loaded, loaded.criteria["C1"]


def _detail(criterion) -> str:
    return f"/api/v1/award-criteria/{criterion.id}"


async def _snapshot(db, criterion_id):
    db.expire_all()
    row = (
        await db.execute(
            sa.select(
                AwardCriterion.review_status,
                AwardCriterion.review_note,
                AwardCriterion.manual_override_json,
                AwardCriterion.reviewed_by,
                AwardCriterion.reviewed_at,
            ).where(AwardCriterion.id == criterion_id)
        )
    ).one()
    return tuple(row)


async def test_other_company_cannot_read_a_private_tender_criterion(
    db, client_a, client_b, tenant_a
):
    _, company_a = tenant_a
    _, criterion = await _loaded(db, company_a.id)

    own = await client_a.get(_detail(criterion))
    assert own.status_code == 200, own.text
    assert own.json()["name"] == criterion.name

    foreign = await client_b.get(_detail(criterion))
    assert foreign.status_code == 404
    assert (
        foreign.json()
        == (
            await client_b.get("/api/v1/award-criteria/00000000-0000-4000-8000-000000000000")
        ).json()
    )
    assert criterion.name not in foreign.text


MISSING = "00000000-0000-4000-8000-000000000000"
BODY = {"review_status": "rejected", "note": "intent", "manual_override": {"points": 0}}


async def _same_as_missing(client, url, missing_url):
    got = await client.post(url, json=BODY)
    gone = await client.post(missing_url, json=BODY)
    assert got.status_code == gone.status_code == 404
    assert got.json() == gone.json()
    assert "intent" not in got.text
    return got


async def test_other_company_cannot_review_a_private_tender_criterion(
    db, client_a, client_b, tenant_a
):
    _, company_a = tenant_a
    _, criterion = await _loaded(db, company_a.id)
    criterion_id = criterion.id
    before = await _snapshot(db, criterion_id)

    await _same_as_missing(
        client_b,
        f"/api/v1/award-criteria/{criterion_id}/review",
        f"/api/v1/award-criteria/{MISSING}/review",
    )
    assert await _snapshot(db, criterion_id) == before


async def test_visible_criterion_review_is_gone_and_writes_nothing(db, client_a, tenant_a):
    _, company_a = tenant_a
    _, criterion = await _loaded(db, company_a.id)
    criterion_id = criterion.id
    before = await _snapshot(db, criterion_id)

    response = await client_a.post(f"/api/v1/award-criteria/{criterion_id}/review", json=BODY)

    assert response.status_code == 410, response.text
    assert response.json()["error"]["code"] == "endpoint_moved"
    assert await _snapshot(db, criterion_id) == before
    assert (await client_a.get(f"/api/v1/award-criteria/{criterion_id}")).status_code == 200


async def test_public_tender_criterion_read_ok_review_gone(db, client_a, client_b):
    _, criterion = await _loaded(db, None)
    criterion_id = criterion.id
    before = await _snapshot(db, criterion_id)

    assert (await client_a.get(f"/api/v1/award-criteria/{criterion_id}")).status_code == 200
    assert (await client_b.get(f"/api/v1/award-criteria/{criterion_id}")).status_code == 200
    reviewed = await client_b.post(f"/api/v1/award-criteria/{criterion_id}/review", json=BODY)
    assert reviewed.status_code == 410, reviewed.text
    assert await _snapshot(db, criterion_id) == before


async def _requirement_of(db, tender_id):
    from app.models.requirements import Requirement

    return await db.scalar(sa.select(Requirement).where(Requirement.tender_id == tender_id))


async def _req_snapshot(db, requirement_id):
    from app.models.requirements import Requirement

    db.expire_all()
    row = (
        await db.execute(
            sa.select(
                Requirement.review_status,
                Requirement.review_note,
                Requirement.manual_override_json,
                Requirement.reviewed_by,
                Requirement.reviewed_at,
                Requirement.updated_at,
            ).where(Requirement.id == requirement_id)
        )
    ).one()
    return tuple(row)


async def test_other_company_cannot_read_or_review_a_private_requirement(
    db, client_a, client_b, tenant_a
):
    _, company_a = tenant_a
    loaded, _ = await _loaded(db, company_a.id)
    requirement = await _requirement_of(db, loaded.tender.id)
    assert requirement is not None
    requirement_id = requirement.id
    title = requirement.title
    before = await _req_snapshot(db, requirement_id)

    assert (await client_a.get(f"/api/v1/requirements/{requirement_id}")).status_code == 200
    read = await client_b.get(f"/api/v1/requirements/{requirement_id}")
    missing = await client_b.get(f"/api/v1/requirements/{MISSING}")
    assert read.status_code == 404
    assert read.json() == missing.json()
    assert title not in read.text

    await _same_as_missing(
        client_b,
        f"/api/v1/requirements/{requirement_id}/review",
        f"/api/v1/requirements/{MISSING}/review",
    )
    assert await _req_snapshot(db, requirement_id) == before


async def test_own_and_public_requirement_read_ok_review_gone(db, client_a, client_b, tenant_a):
    _, company_a = tenant_a
    loaded, _ = await _loaded(db, company_a.id)
    own = await _requirement_of(db, loaded.tender.id)
    requirement_id = own.id
    before = await _req_snapshot(db, requirement_id)
    url = f"/api/v1/requirements/{requirement_id}/review"

    assert (await client_a.get(f"/api/v1/requirements/{requirement_id}")).status_code == 200
    gone = await client_a.post(url, json={"review_status": "verified"})
    assert gone.status_code == 410, gone.text
    assert gone.json()["error"]["code"] == "endpoint_moved"
    assert await _req_snapshot(db, requirement_id) == before

    loaded.tender.owner_company_id = None
    await db.commit()
    before = await _req_snapshot(db, requirement_id)
    assert (await client_b.get(f"/api/v1/requirements/{requirement_id}")).status_code == 200
    assert (await client_b.post(url, json={"review_status": "needs_review"})).status_code == 410
    assert await _req_snapshot(db, requirement_id) == before
