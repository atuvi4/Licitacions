"""Els plecs d'una licitacio privada no son de tothom (S3-S2).

Tota la capa de lectura de plecs es va escriure quan TOTES les licitacions eren
publiques. Llavors no calia filtrar res: si un document existia, qualsevol
usuari registrat el podia llegir, i era cert. Des que hi ha licitacions d'una
empresa, la mateixa consulta global s'ha convertit en una fuita, i la pitjor:
`/original` retorna els BYTES del plec.

Es el tercer cop que apareix el mateix patro —`MatchingService`, el cataleg
d'organismes i ara els plecs—, i sempre per la mateixa causa: una enumeracio
global escrita quan tot era public.

Els plecs d'una licitacio publicada continuen sent de tothom. El que deixa de
ser-ho es el que penja d'una licitacio privada.
"""

from __future__ import annotations

import uuid

import pytest

from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

CREATE = "/api/v1/companies/{company}/tenders"
UPLOAD = "/api/v1/companies/{company}/tenders/{tender}/documents"


@pytest.fixture
async def privat(client_a, tenant_a) -> dict:
    """Una licitacio d'A amb un plec pujat i llegit."""
    _, company = tenant_a
    created = await client_a.post(
        CREATE.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": "Reservada"},
    )
    assert created.status_code == 201, created.text
    tender_id = created.json()["tender"]["id"]

    uploaded = await client_a.post(
        UPLOAD.format(company=company.id, tender=tender_id),
        files={"file": ("PCAP.pdf", build_pdf(["Clausula reservada. " * 30]), "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    return {
        "tender_id": tender_id,
        "document_id": body["id"],
        "file_id": body["file"]["file_id"],
        "extraction_id": body["file"]["extraction_id"],
    }


def _routes(privat: dict) -> list[str]:
    return [
        f"/api/v1/tenders/{privat['tender_id']}/documents/{privat['document_id']}/files",
        f"/api/v1/tender-document-files/{privat['file_id']}",
        f"/api/v1/tender-document-files/{privat['file_id']}/extractions",
        f"/api/v1/tender-document-files/{privat['file_id']}/pages",
        f"/api/v1/tender-document-files/{privat['file_id']}/original",
        f"/api/v1/tender-document-extractions/{privat['extraction_id']}/pages",
        f"/api/v1/tender-document-extractions/{privat['extraction_id']}/pages/1",
    ]


async def test_the_owner_reaches_every_document_route(client_a, privat):
    for route in _routes(privat):
        response = await client_a.get(route)
        assert response.status_code == 200, f"{route}: {response.text}"


@pytest.mark.parametrize("index", range(7))
async def test_another_company_reaches_none_of_them(client_b, privat, index):
    """Conèixer l'UUID no es tenir-hi dret. I l'UUID viatja."""
    route = _routes(privat)[index]
    response = await client_b.get(route)
    assert response.status_code == 404, f"{route}: {response.status_code}"


async def test_the_bytes_of_a_private_plec_do_not_leave_the_company(client_b, privat):
    """La pitjor de les fuites: no metadata, el document sencer."""
    response = await client_b.get(
        f"/api/v1/tender-document-files/{privat['file_id']}/original"
    )
    assert response.status_code == 404
    assert b"%PDF" not in response.content


async def test_the_text_of_a_private_plec_does_not_leave_either(client_b, privat):
    response = await client_b.get(
        f"/api/v1/tender-document-extractions/{privat['extraction_id']}/pages"
    )
    assert response.status_code == 404
    assert "Clausula reservada" not in response.text


async def test_a_user_of_both_companies_still_reaches_it(
    client_factory, db, tenant_a, tenant_b, privat
):
    """El limit es de l'empresa, no del context: qui es d'A hi te dret sempre.

    Aquests endpoints no pengen de `/companies/{id}` i per tant no tenen
    context: la pregunta que responen es "aquest usuari pot veure la
    licitacio?", i ser membre d'A vol dir que si.
    """
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership
    from tests.conftest import TEST_PASSWORD, login

    user, _company_a = tenant_a
    _, company_b = tenant_b
    db.add(
        CompanyMembership(user_id=user.id, company_id=company_b.id, role=MembershipRole.OWNER)
    )
    await db.commit()

    http = client_factory()
    await login(http, user.email, TEST_PASSWORD)
    for route in _routes(privat):
        assert (await http.get(route)).status_code == 200, route


async def test_a_public_plec_is_still_everyones(client_b, db, tender_document):
    """La correccio no pot tancar el que sempre ha sigut public."""
    response = await client_b.get(
        f"/api/v1/tenders/{tender_document.tender_id}/documents/{tender_document.id}/files"
    )
    assert response.status_code == 200
