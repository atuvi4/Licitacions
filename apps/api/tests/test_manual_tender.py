"""Alta manual d'una licitacio: identitat, idempotencia i aillament (S3-S1).

El que es prova aqui no es «que es pugui crear», sino les tres coses que fan
que crear sigui segur:

* el **token** identifica, no el numero d'expedient ni el nom de l'organisme,
  de manera que dues empreses poden donar d'alta el mateix expedient i el
  mateix ajuntament sense xocar;
* un **reintent** recupera la que ja hi havia i no en crea una altra, pero si
  el mateix token torna amb una licitacio diferent es un error i no una
  sobreescriptura silenciosa;
* la licitacio i el seu organisme privat neixen **junts o no neixen**: si la
  licitacio falla, no queda cap organisme orfe al cataleg.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.enums import SourceType
from app.models.matching import CompanyTenderMatch
from app.models.sources import Source
from app.models.tenders import Buyer, Tender

pytestmark = pytest.mark.asyncio

URL = "/api/v1/companies/{company}/tenders"


def payload(**overrides) -> dict:
    body = {"client_token": str(uuid.uuid4()), "title": "Serveis de comunicacions"}
    body.update(overrides)
    return body


async def _post(client, company, body) -> tuple[int, dict]:
    response = await client.post(URL.format(company=company.id), json=body)
    return response.status_code, response.json()


async def _global_buyer(session) -> Buyer:
    """Un organisme d'una font sindicada: public i de tothom."""
    source = Source(code=f"placsp-{uuid.uuid4().hex[:8]}", name="PLACSP")
    session.add(source)
    await session.flush()
    buyer = Buyer(
        source_id=source.id,
        external_identifier=f"org-{uuid.uuid4().hex[:8]}",
        name="Ajuntament public",
    )
    session.add(buyer)
    await session.commit()
    return buyer


# --------------------------------------------------------------------------- #
# A. La font MANUAL                                                             #
# --------------------------------------------------------------------------- #


async def test_the_manual_source_is_created_once_and_shared(client_a, db, tenant_a):
    _, company = tenant_a
    for _ in range(2):
        status, _body = await _post(client_a, company, payload())
        assert status in (200, 201)

    sources = (
        await db.execute(sa.select(Source).where(Source.source_type == SourceType.MANUAL))
    ).scalars().all()
    assert len(sources) == 1
    assert sources[0].code == "manual"


async def test_two_companies_share_the_same_manual_source(
    client_a, client_b, db, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    await _post(client_a, company_a, payload())
    await _post(client_b, company_b, payload())

    sources = (
        await db.execute(sa.select(Source).where(Source.source_type == SourceType.MANUAL))
    ).scalars().all()
    assert len(sources) == 1


# --------------------------------------------------------------------------- #
# B. Identitat                                                                  #
# --------------------------------------------------------------------------- #


async def test_the_external_id_comes_from_the_token_and_not_the_expedient(
    client_a, db, tenant_a
):
    _, company = tenant_a
    token = str(uuid.uuid4())
    status, body = await _post(
        client_a, company, payload(client_token=token, expediente_number="2024-3137")
    )
    assert status == 201

    tender = (
        await db.execute(sa.select(Tender).where(Tender.id == uuid.UUID(body["tender"]["id"])))
    ).scalars().one()
    assert tender.external_id == f"manual:{token}"
    assert tender.expediente_number == "2024-3137"
    assert tender.owner_company_id == company.id


async def test_two_companies_may_open_the_same_expedient(
    client_a, client_b, db, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    _, a = await _post(client_a, company_a, payload(expediente_number="2024-3137"))
    _, b = await _post(client_b, company_b, payload(expediente_number="2024-3137"))

    assert a["tender"]["id"] != b["tender"]["id"]
    assert a["tender"]["expediente_number"] == b["tender"]["expediente_number"] == "2024-3137"


# --------------------------------------------------------------------------- #
# C. Idempotencia                                                               #
# --------------------------------------------------------------------------- #


async def test_a_retry_with_the_same_token_returns_the_same_tender(client_a, db, tenant_a):
    _, company = tenant_a
    body = payload(expediente_number="2024-3137")

    first_status, first = await _post(client_a, company, body)
    second_status, second = await _post(client_a, company, body)

    assert (first_status, first["created"]) == (201, True)
    assert (second_status, second["created"]) == (200, False)
    assert first["tender"]["id"] == second["tender"]["id"]

    total = (
        await db.execute(
            sa.select(sa.func.count()).select_from(Tender).where(
                Tender.owner_company_id == company.id
            )
        )
    ).scalar_one()
    assert total == 1


@pytest.mark.parametrize(
    "field,value",
    [("title", "Un altre objecte"), ("expediente_number", "2025-9999")],
)
async def test_the_same_token_with_a_different_tender_is_a_conflict(
    client_a, tenant_a, field, value
):
    """Els camps materials son titol, expedient i organisme."""
    _, company = tenant_a
    body = payload(expediente_number="2024-3137")
    assert (await _post(client_a, company, body))[0] == 201

    status, error = await _post(client_a, company, {**body, field: value})
    assert status == 409
    assert error["error"]["code"] == "manual_tender_payload_conflict"


async def test_a_descriptive_difference_does_not_block_the_retry(client_a, tenant_a):
    """Nomes els camps materials bloquegen; la resta s'edita despres."""
    _, company = tenant_a
    token = str(uuid.uuid4())
    body = payload(client_token=token, expediente_number="2024-3137")
    assert (await _post(client_a, company, body))[0] == 201
    status, second = await _post(client_a, company, dict(body))
    assert (status, second["created"]) == (200, False)


# --------------------------------------------------------------------------- #
# D. Organismes                                                                 #
# --------------------------------------------------------------------------- #


async def test_a_global_buyer_can_be_reused_by_anyone_and_is_never_modified(
    client_a, client_b, db, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    buyer = await _global_buyer(db)
    before = buyer.name

    for client, company in ((client_a, company_a), (client_b, company_b)):
        status, body = await _post(client, company, payload(buyer_id=str(buyer.id)))
        assert status == 201
        assert body["tender"]["buyer"]["id"] == str(buyer.id)

    await db.refresh(buyer)
    assert buyer.name == before
    assert buyer.owner_company_id is None


async def test_a_manual_buyer_is_private_to_the_company_that_wrote_it(
    client_a, db, tenant_a
):
    _, company = tenant_a
    status, body = await _post(
        client_a,
        company,
        payload(buyer_manual={"client_token": str(uuid.uuid4()), "name": "Ajuntament X"}),
    )
    assert status == 201

    buyer = (
        await db.execute(
            sa.select(Buyer).where(Buyer.id == uuid.UUID(body["tender"]["buyer"]["id"]))
        )
    ).scalars().one()
    assert buyer.owner_company_id == company.id
    assert buyer.external_identifier.startswith("manual:")


async def test_two_companies_may_write_the_same_buyer_without_colliding(
    client_a, client_b, db, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    name = "Ajuntament de Provaville"
    ids = []
    for client, company in ((client_a, company_a), (client_b, company_b)):
        _status, body = await _post(
            client,
            company,
            payload(buyer_manual={"client_token": str(uuid.uuid4()), "name": name}),
        )
        ids.append(body["tender"]["buyer"]["id"])

    assert ids[0] != ids[1]
    owners = (
        await db.execute(
            sa.select(Buyer.owner_company_id).where(
                Buyer.id.in_([uuid.UUID(i) for i in ids])
            )
        )
    ).scalars().all()
    assert set(owners) == {company_a.id, company_b.id}


async def test_a_private_buyer_of_another_company_cannot_be_selected(
    client_a, client_b, db, tenant_a, tenant_b
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    _s, body = await _post(
        client_a,
        company_a,
        payload(buyer_manual={"client_token": str(uuid.uuid4()), "name": "Privat d'A"}),
    )
    buyer_id = body["tender"]["buyer"]["id"]

    status, error = await _post(client_b, company_b, payload(buyer_id=buyer_id))
    assert status == 404
    assert error["error"]["code"] == "not_found"


async def test_the_same_buyer_token_does_not_create_a_second_buyer(client_a, db, tenant_a):
    _, company = tenant_a
    buyer_token = str(uuid.uuid4())
    manual = {"client_token": buyer_token, "name": "Ajuntament repetit"}
    await _post(client_a, company, payload(buyer_manual=manual))
    await _post(client_a, company, payload(buyer_manual=manual))

    total = (
        await db.execute(
            sa.select(sa.func.count()).select_from(Buyer).where(
                Buyer.owner_company_id == company.id
            )
        )
    ).scalar_one()
    assert total == 1


async def test_buyer_id_and_buyer_manual_are_mutually_exclusive(client_a, db, tenant_a):
    _, company = tenant_a
    buyer = await _global_buyer(db)
    status, error = await _post(
        client_a,
        company,
        payload(
            buyer_id=str(buyer.id),
            buyer_manual={"client_token": str(uuid.uuid4()), "name": "Tots dos"},
        ),
    )
    assert status == 422
    assert error["error"]["code"] == "buyer_choice_ambiguous"


async def test_a_tender_may_start_without_any_buyer(client_a, tenant_a):
    _, company = tenant_a
    status, body = await _post(client_a, company, payload())
    assert status == 201
    assert body["tender"]["buyer"] is None


# --------------------------------------------------------------------------- #
# E. Atomicitat                                                                 #
# --------------------------------------------------------------------------- #


async def test_a_failed_tender_leaves_no_orphan_buyer(client_a, db, tenant_a):
    """Si la licitacio no es pot crear, l'organisme no es queda al cataleg."""
    _, company = tenant_a
    token = str(uuid.uuid4())
    await _post(client_a, company, payload(client_token=token, title="Primera"))

    # Mateix token de licitacio, titol diferent -> 409. L'organisme nou que
    # porta la peticio no ha d'arribar a existir.
    status, error = await _post(
        client_a,
        company,
        payload(
            client_token=token,
            title="Segona",
            buyer_manual={"client_token": str(uuid.uuid4()), "name": "Orfe"},
        ),
    )
    assert status == 409
    assert error["error"]["code"] == "manual_tender_payload_conflict"

    orphans = (
        await db.execute(sa.select(sa.func.count()).select_from(Buyer).where(Buyer.name == "Orfe"))
    ).scalar_one()
    assert orphans == 0


# --------------------------------------------------------------------------- #
# F. Camí cap a Oportunitats                                                    #
# --------------------------------------------------------------------------- #


async def test_the_new_tender_gets_a_match_for_its_owner(client_a, db, tenant_a):
    _, company = tenant_a
    _status, body = await _post(client_a, company, payload())
    tender_id = uuid.UUID(body["tender"]["id"])

    match = (
        await db.execute(
            sa.select(CompanyTenderMatch).where(
                CompanyTenderMatch.company_id == company.id,
                CompanyTenderMatch.tender_id == tender_id,
            )
        )
    ).scalars().one_or_none()
    assert match is not None


async def test_it_shows_up_in_opportunities_for_its_owner(client_a, tenant_a):
    _, company = tenant_a
    _status, body = await _post(client_a, company, payload(title="Nova a ma"))

    response = await client_a.get(
        f"/api/v1/companies/{company.id}/matches", params={"limit": 100}
    )
    assert response.status_code == 200
    seen = {item["tender"]["id"] for item in response.json()["items"]}
    assert body["tender"]["id"] in seen


async def test_another_company_never_sees_it(client_a, client_b, db, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    _status, body = await _post(client_a, company_a, payload(title="Nomes d'A"))
    tender_id = body["tender"]["id"]

    detail = await client_b.get(f"/api/v1/tenders/{tender_id}")
    assert detail.status_code == 404

    await client_b.post(f"/api/v1/companies/{company_b.id}/matches/recalculate")
    matches = await client_b.get(
        f"/api/v1/companies/{company_b.id}/matches", params={"limit": 100}
    )
    assert tender_id not in {item["tender"]["id"] for item in matches.json()["items"]}

    rows = (
        await db.execute(
            sa.select(CompanyTenderMatch.tender_id).where(
                CompanyTenderMatch.company_id == company_b.id
            )
        )
    ).scalars().all()
    assert uuid.UUID(tender_id) not in set(rows)


# --------------------------------------------------------------------------- #
# G. Rols                                                                       #
# --------------------------------------------------------------------------- #


async def test_a_company_cannot_open_a_tender_for_another_company(client_b, tenant_a):
    _, company_a = tenant_a
    status, _error = await _post(client_b, company_a, payload())
    assert status in (403, 404)
