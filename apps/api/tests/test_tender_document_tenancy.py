"""Els plecs d'una licitacio privada es llegeixen EN UN CONTEXT D'EMPRESA.

Tota la capa de lectura de plecs es va escriure quan TOTES les licitacions eren
publiques. Llavors no calia filtrar res: si un document existia, qualsevol
usuari registrat el podia llegir, i era cert. Des que hi ha licitacions d'una
empresa, la mateixa consulta global es una fuita, i la pitjor: `/original`
retorna els BYTES del plec.

La primera correccio va filtrar per PERTINENÇA de l'usuari, i aixo no n'hi ha
prou. Qui es membre d'A i de B treballa sempre DINS d'una empresa: en context B
no ha de poder llegir un plec d'A nomes perque tambe es d'A. El limit d'un
tenant no es qui ets, es on ets.

Els plecs d'una licitacio publicada continuen sent de tothom: `visible_to_company`
els inclou en qualsevol context.
"""

from __future__ import annotations

import uuid

import pytest

from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

CREATE = "/api/v1/companies/{company}/tenders"
UPLOAD = "/api/v1/companies/{company}/tenders/{tender}/documents"


async def _private_tender_with_plec(client, company, *, title: str) -> dict:
    created = await client.post(
        CREATE.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": title},
    )
    assert created.status_code == 201, created.text
    tender_id = created.json()["tender"]["id"]

    uploaded = await client.post(
        UPLOAD.format(company=company.id, tender=tender_id),
        files={"file": ("PCAP.pdf", build_pdf([f"Clausula de {title}. " * 30]), "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    return {
        "tender_id": tender_id,
        "document_id": body["id"],
        "file_id": body["file"]["file_id"],
        "extraction_id": body["file"]["extraction_id"],
    }


@pytest.fixture
async def privat(client_a, tenant_a) -> dict:
    """Una licitacio d'A amb un plec pujat i llegit."""
    _, company = tenant_a
    return await _private_tender_with_plec(client_a, company, title="Reservada")


def _routes(company_id, plec: dict) -> list[str]:
    """Els set camins de lectura, tots dins d'un context d'empresa."""
    root = f"/api/v1/companies/{company_id}"
    return [
        f"{root}/tenders/{plec['tender_id']}/documents/{plec['document_id']}/files",
        f"{root}/tender-document-files/{plec['file_id']}",
        f"{root}/tender-document-files/{plec['file_id']}/extractions",
        f"{root}/tender-document-files/{plec['file_id']}/pages",
        f"{root}/tender-document-files/{plec['file_id']}/original",
        f"{root}/tender-document-extractions/{plec['extraction_id']}/pages",
        f"{root}/tender-document-extractions/{plec['extraction_id']}/pages/1",
    ]


# --------------------------------------------------------------------------- #
# A. La propia empresa                                                          #
# --------------------------------------------------------------------------- #


async def test_the_owner_company_reaches_every_document_route(client_a, tenant_a, privat):
    _, company = tenant_a
    for route in _routes(company.id, privat):
        response = await client_a.get(route)
        assert response.status_code == 200, f"{route}: {response.text}"


# --------------------------------------------------------------------------- #
# B. Una altra empresa                                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("index", range(7))
async def test_another_company_reaches_none_of_them(client_b, tenant_b, privat, index):
    """Coneixer l'UUID no es tenir-hi dret. I l'UUID viatja."""
    _, company_b = tenant_b
    route = _routes(company_b.id, privat)[index]
    response = await client_b.get(route)
    assert response.status_code == 404, f"{route}: {response.status_code}"


async def test_the_bytes_of_a_private_plec_do_not_leave_the_company(client_b, tenant_b, privat):
    """La pitjor de les fuites: no metadata, el document sencer."""
    _, company_b = tenant_b
    response = await client_b.get(
        f"/api/v1/companies/{company_b.id}/tender-document-files/{privat['file_id']}/original"
    )
    assert response.status_code == 404
    assert b"%PDF" not in response.content


async def test_the_text_of_a_private_plec_does_not_leave_either(client_b, tenant_b, privat):
    _, company_b = tenant_b
    response = await client_b.get(
        f"/api/v1/companies/{company_b.id}"
        f"/tender-document-extractions/{privat['extraction_id']}/pages"
    )
    assert response.status_code == 404
    assert "Clausula" not in response.text


# --------------------------------------------------------------------------- #
# C. El gate: el mateix usuari a dues empreses                                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def both(client_factory, db, tenant_a, tenant_b, client_a):
    """El mateix usuari, membre d'A i de B, amb un plec privat a cada una."""
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership
    from tests.conftest import TEST_PASSWORD, login

    user, company_a = tenant_a
    _, company_b = tenant_b
    plec_a = await _private_tender_with_plec(client_a, company_a, title="D'A")
    db.add(
        CompanyMembership(user_id=user.id, company_id=company_b.id, role=MembershipRole.OWNER)
    )
    await db.commit()

    http = client_factory()
    await login(http, user.email, TEST_PASSWORD)
    plec_b = await _private_tender_with_plec(http, company_b, title="De B")
    return {
        "client": http,
        "company_a": company_a,
        "company_b": company_b,
        "plec_a": plec_a,
        "plec_b": plec_b,
    }


@pytest.mark.parametrize("index", range(7))
async def test_in_context_b_the_user_cannot_read_the_plec_of_a(both, index):
    """El gate principal. Ser d'A no dona dret a llegir-ho des de B.

    Nomes filtrar per pertinença deixava aquest forat obert: la consulta deia
    que si, perque l'usuari ES d'A, encara que estigues treballant a B.
    """
    route = _routes(both["company_b"].id, both["plec_a"])[index]
    response = await both["client"].get(route)
    assert response.status_code == 404, f"{route}: {response.status_code}"


@pytest.mark.parametrize("index", range(7))
async def test_in_context_a_the_same_user_reads_it_perfectly(both, index):
    """I el contrari: el limit es el context, no una restriccio nova."""
    route = _routes(both["company_a"].id, both["plec_a"])[index]
    response = await both["client"].get(route)
    assert response.status_code == 200, f"{route}: {response.text}"


async def test_the_bytes_of_a_do_not_arrive_through_the_context_of_b(both):
    response = await both["client"].get(
        f"/api/v1/companies/{both['company_b'].id}"
        f"/tender-document-files/{both['plec_a']['file_id']}/original"
    )
    assert response.status_code == 404
    assert b"%PDF" not in response.content


async def test_each_context_reads_its_own(both):
    """Les dues empreses conviuen: cadascuna veu la seva i nomes la seva."""
    own_b = await both["client"].get(
        f"/api/v1/companies/{both['company_b'].id}"
        f"/tender-document-files/{both['plec_b']['file_id']}"
    )
    assert own_b.status_code == 200

    crossed = await both["client"].get(
        f"/api/v1/companies/{both['company_a'].id}"
        f"/tender-document-files/{both['plec_b']['file_id']}"
    )
    assert crossed.status_code == 404


# --------------------------------------------------------------------------- #
# D. Contingut compartit                                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def same_bytes(client_factory, db, tenant_a, tenant_b, client_a):
    """El MATEIX fitxer pujat a A i a B. Un sol contingut, una sola extraccio."""
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership
    from tests.conftest import TEST_PASSWORD, login

    user, company_a = tenant_a
    _, company_b = tenant_b
    db.add(
        CompanyMembership(user_id=user.id, company_id=company_b.id, role=MembershipRole.OWNER)
    )
    await db.commit()

    http = client_factory()
    await login(http, user.email, TEST_PASSWORD)
    payload = build_pdf(["Un plec que tenen totes dues. " * 20])

    def make(client, company):
        return client.post(
            CREATE.format(company=company.id),
            json={"client_token": str(uuid.uuid4()), "title": "Compartida"},
        )

    created_a = await make(client_a, company_a)
    tender_a = created_a.json()["tender"]["id"]
    up_a = await client_a.post(
        UPLOAD.format(company=company_a.id, tender=tender_a),
        files={"file": ("PCAP.pdf", payload, "application/pdf")},
    )
    created_b = await make(http, company_b)
    tender_b = created_b.json()["tender"]["id"]
    up_b = await http.post(
        UPLOAD.format(company=company_b.id, tender=tender_b),
        files={"file": ("PCAP.pdf", payload, "application/pdf")},
    )

    a, b = up_a.json(), up_b.json()
    assert a["file"]["sha256"] == b["file"]["sha256"]
    assert a["file"]["extraction_id"] == b["file"]["extraction_id"], "una sola extraccio"
    return {
        "client": http,
        "company_a": company_a,
        "company_b": company_b,
        "extraction_id": a["file"]["extraction_id"],
    }


async def test_a_shared_extraction_is_readable_from_both_contexts(same_bytes):
    """L'autoritzacio no es de qui va crear l'extraccio.

    Els mateixos bytes donen un sol `TenderDocumentContent` i una sola
    extraccio. Si el dret depengues de qui la va crear, la segona empresa no
    podria llegir el text del SEU propi plec nomes perque algu altre el va
    pujar abans.
    """
    for company in (same_bytes["company_a"], same_bytes["company_b"]):
        response = await same_bytes["client"].get(
            f"/api/v1/companies/{company.id}"
            f"/tender-document-extractions/{same_bytes['extraction_id']}/pages"
        )
        assert response.status_code == 200, f"{company.legal_name}: {response.text}"
        assert response.json()["total"] >= 1


async def test_a_shared_extraction_that_only_a_references_is_not_readable_from_b(
    both, client_b, tenant_b
):
    """I el contrari: si nomes A referencia aquell contingut, B no hi arriba."""
    _, company_b = tenant_b
    response = await client_b.get(
        f"/api/v1/companies/{company_b.id}"
        f"/tender-document-extractions/{both['plec_a']['extraction_id']}/pages"
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# E. Els plecs publics                                                          #
# --------------------------------------------------------------------------- #


async def test_a_public_plec_is_readable_from_any_context(client_b, tenant_b, tender_document):
    """La correccio no pot tancar el que sempre ha sigut public.

    `visible_to_company` inclou les licitacions sense propietari, aixi que un
    plec publicat es llegeix des de qualsevol empresa.
    """
    _, company_b = tenant_b
    response = await client_b.get(
        f"/api/v1/companies/{company_b.id}"
        f"/tenders/{tender_document.tender_id}/documents/{tender_document.id}/files"
    )
    assert response.status_code == 200


async def test_the_old_global_routes_are_gone(client_a, privat):
    """No queden portes user-global per a documents privats.

    Mantenir-les "per compatibilitat" voldria dir mantenir el forat: eren
    exactament el cami pel qual un membre de dues empreses llegia el plec de
    l'altra.
    """
    for route in (
        f"/api/v1/tender-document-files/{privat['file_id']}",
        f"/api/v1/tender-document-files/{privat['file_id']}/original",
        f"/api/v1/tender-document-extractions/{privat['extraction_id']}/pages",
    ):
        assert (await client_a.get(route)).status_code == 404, route
