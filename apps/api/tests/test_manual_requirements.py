"""Requisits escrits per una persona i el conjunt EFECTIU (S3-S5).

Dues coses que aquests tests no deixen tornar enrere:

**No s'inventa cap execució d'anàlisi.** Una `RequirementExtractionRun` modela
una passada real amb el seu proveïdor, el seu model i el seu corpus d'entrada.
Fabricar-ne una per encabir-hi un requisit escrit a mà faria que tot el que s'hi
recolza —el fingerprint del corpus, l'avís que l'anàlisi no cobreix els
documents d'ara— digués una cosa que no ha passat.

**Els requisits vigents són la unió, i una sola porta la calcula.** L'extracció
vigent més els manuals no arxivats. Si cada consumidor la reconstruís pel seu
compte, acabaríem amb dues respostes a la mateixa pregunta.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.enums import RequirementOrigin
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.workspace import BidWorkspace
from app.services import manual_requirement
from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

CREATE_TENDER = "/api/v1/companies/{company}/tenders"
UPLOAD = "/api/v1/companies/{company}/tenders/{tender}/documents"
REQUIREMENTS = "/api/v1/companies/{company}/tenders/{tender}/requirements"
ONE = "/api/v1/companies/{company}/tenders/{tender}/requirements/{requirement}"
ARCHIVE = ONE + "/archive"
CITATIONS = ONE + "/citations"
ANALYSIS = "/api/v1/tenders/{tender}/analysis/requirements"

PLEC = build_pdf(["Clausula primera. Cal una assegurança de responsabilitat civil. " * 4])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_V1 = b"PK\x03\x04" + b"\x01" * 64
XLSX_V2 = b"PK\x03\x04" + b"\x02" * 64


def payload(**overrides) -> dict:
    body = {
        "title": "Assegurança de responsabilitat civil",
        "category": "insurance",
        "obligation": "mandatory",
        "scope": "tender",
    }
    body.update(overrides)
    return body


async def _tender(client, company, title="Serveis de comunicacions") -> str:
    response = await client.post(
        CREATE_TENDER.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()["tender"]["id"]


async def _create(client, company, tender_id, **overrides):
    return await client.post(
        REQUIREMENTS.format(company=company.id, tender=tender_id),
        json=payload(**overrides),
    )


async def _ok(client, company, tender_id, **overrides) -> dict:
    response = await _create(client, company, tender_id, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


async def _upload(client, company, tender_id, *, name, data, mime="application/pdf") -> dict:
    response = await client.post(
        UPLOAD.format(company=company.id, tender=tender_id),
        files={"file": (name, data, mime)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _extracted_run(db, tender_id: str, *, current=True) -> RequirementExtractionRun:
    """Una run d'extracció de debò. No se'n fabrica cap per als manuals."""
    from app.models.enums import AnalysisRunStatus

    await db.execute(
        sa.update(RequirementExtractionRun)
        .where(RequirementExtractionRun.tender_id == uuid.UUID(tender_id))
        .values(is_current=False)
    )
    run = RequirementExtractionRun(
        tender_id=uuid.UUID(tender_id),
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="anthropic",
        model="claude-sonnet-5",
        is_current=current,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _extracted_requirement(db, run, tender_id: str, title="Extret") -> Requirement:
    from app.models.enums import (
        RequirementCategory,
        RequirementObligation,
        RequirementOperator,
        RequirementScope,
    )

    requirement = Requirement(
        run_id=run.id,
        tender_id=uuid.UUID(tender_id),
        origin=RequirementOrigin.EXTRACTED,
        category=RequirementCategory.CERTIFICATION,
        obligation=RequirementObligation.MANDATORY,
        operator=RequirementOperator.EXISTS,
        scope=RequirementScope.TENDER,
        title=title,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


# --------------------------------------------------------------------------- #
# A. Crear un requisit a mà                                                     #
# --------------------------------------------------------------------------- #


async def test_a_manual_requirement_has_no_run(client_a, db, tenant_a):
    """L'invariant que sosté tota la fase (D-09)."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)

    body = await _ok(client_a, company, tender_id)

    assert body["origin"] == "manual"
    assert body["run_id"] is None
    assert body["archived_at"] is None
    row = await db.get(Requirement, uuid.UUID(body["id"]))
    assert row.run_id is None
    assert row.origin == RequirementOrigin.MANUAL


async def test_writing_a_requirement_creates_no_extraction_run(client_a, db, tenant_a):
    """Cap run fabricada. Ni una."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    before = (
        await db.execute(sa.select(sa.func.count()).select_from(RequirementExtractionRun))
    ).scalar_one()

    await _ok(client_a, company, tender_id)

    after = (
        await db.execute(sa.select(sa.func.count()).select_from(RequirementExtractionRun))
    ).scalar_one()
    assert after == before


async def test_a_manual_requirement_is_not_verified_by_default(client_a, tenant_a):
    """Que l'hagi escrit una persona no vol dir que estigui comprovat."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)

    body = await _ok(client_a, company, tender_id)

    assert body["review_status"] == "unreviewed"


async def test_the_server_does_not_pretend_a_model_said_anything(client_a, tenant_a):
    """`confidence` es de l'extractor, i aqui no n'hi ha cap."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)

    body = await _ok(client_a, company, tender_id)

    assert body["confidence"] is None


@pytest.mark.parametrize("field", ["origin", "run_id", "archived_at", "confidence"])
async def test_the_client_cannot_declare_how_the_row_was_born(client_a, tenant_a, field):
    """`extra="forbid"`: es diu que no, no es fa veure que no s'ha enviat."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)

    response = await _create(client_a, company, tender_id, **{field: "extracted"})
    assert response.status_code == 422


async def test_a_lot_of_another_tender_is_refused(client_a, db, tenant_a):
    from app.models.tenders import TenderLot

    _, company = tenant_a
    mine = await _tender(client_a, company)
    other = await _tender(client_a, company, title="Una altra")
    lot = TenderLot(tender_id=uuid.UUID(other), external_lot_id="L1", title="Zona nord")
    db.add(lot)
    await db.commit()

    response = await _create(client_a, company, mine, tender_lot_id=str(lot.id))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "lot_tender_mismatch"


# --------------------------------------------------------------------------- #
# B. Editar i arxivar                                                           #
# --------------------------------------------------------------------------- #


async def test_an_extracted_requirement_is_not_editable_here(client_a, db, tenant_a):
    """Editar-lo seria reescriure la sortida d'una execucio concreta.

    Llavors la run ja no diria el que va produir. Els extrets tenen el seu
    cami, que es la revisio.
    """
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    run = await _extracted_run(db, tender_id)
    extracted = await _extracted_requirement(db, run, tender_id)

    response = await client_a.patch(
        ONE.format(company=company.id, tender=tender_id, requirement=extracted.id),
        json={"title": "Reescrit"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "requirement_not_manual"


async def test_editing_records_who_and_leaves_a_trace(client_a, db, tenant_a):
    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    created = await _ok(client_a, company, tender_id)
    # L'id de l'usuari es captura ABANS d'expirar la sessio.
    user_id = user.id

    response = await client_a.patch(
        ONE.format(company=company.id, tender=tender_id, requirement=created["id"]),
        json={"title": "Assegurança amb cobertura ampliada"},
    )
    assert response.status_code == 200
    assert response.json()["changed"] is True

    db.expire_all()
    row = await db.get(Requirement, uuid.UUID(created["id"]))
    assert row.title == "Assegurança amb cobertura ampliada"
    assert row.updated_by == user_id
    assert row.created_by == user_id


async def test_a_patch_that_changes_nothing_is_not_a_change(client_a, db, tenant_a):
    """Si cada desat repetit comptés com a canvi, el senyal es gastaria."""
    from app.models.audit import AuditLog
    from app.models.enums import AuditAction

    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    created = await _ok(client_a, company, tender_id)

    response = await client_a.patch(
        ONE.format(company=company.id, tender=tender_id, requirement=created["id"]),
        json={"title": created["title"]},
    )
    assert response.status_code == 200
    assert response.json()["changed"] is False

    updates = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.entity_id == uuid.UUID(created["id"]),
                AuditLog.action == AuditAction.UPDATE,
            )
        )
    ).scalar_one()
    assert updates == 0


async def test_archiving_does_not_delete(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    created = await _ok(client_a, company, tender_id)

    response = await client_a.post(
        ARCHIVE.format(company=company.id, tender=tender_id, requirement=created["id"])
    )
    assert response.status_code == 200
    assert response.json()["changed"] is True

    db.expire_all()
    row = await db.get(Requirement, uuid.UUID(created["id"]))
    assert row is not None, "arxivar no es esborrar"
    assert row.archived_at is not None


async def test_archiving_twice_is_idempotent(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    created = await _ok(client_a, company, tender_id)
    url = ARCHIVE.format(company=company.id, tender=tender_id, requirement=created["id"])

    first = await client_a.post(url)
    db.expire_all()
    archived_at = (await db.get(Requirement, uuid.UUID(created["id"]))).archived_at

    second = await client_a.post(url)
    assert (first.json()["changed"], second.json()["changed"]) == (True, False)
    db.expire_all()
    assert (await db.get(Requirement, uuid.UUID(created["id"]))).archived_at == archived_at


async def test_editing_an_archived_requirement_is_refused(client_a, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    created = await _ok(client_a, company, tender_id)
    await client_a.post(
        ARCHIVE.format(company=company.id, tender=tender_id, requirement=created["id"])
    )

    response = await client_a.patch(
        ONE.format(company=company.id, tender=tender_id, requirement=created["id"]),
        json={"title": "Un altre"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "requirement_archived"


# --------------------------------------------------------------------------- #
# C. El conjunt efectiu                                                         #
# --------------------------------------------------------------------------- #


async def test_the_effective_set_is_the_current_run_plus_live_manuals(
    client_a, db, tenant_a
):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    run = await _extracted_run(db, tender_id)
    extracted = await _extracted_requirement(db, run, tender_id)
    manual = await _ok(client_a, company, tender_id)

    effective = await manual_requirement.effective_requirements(db, uuid.UUID(tender_id))

    assert [r.id for r in effective.extracted] == [extracted.id]
    assert [str(r.id) for r in effective.manual] == [manual["id"]]
    assert len(effective.all) == 2


async def test_an_old_run_does_not_contribute(client_a, db, tenant_a):
    """Els requisits d'una run que ja no es vigent parlen d'uns altres documents."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    old_run = await _extracted_run(db, tender_id)
    old = await _extracted_requirement(db, old_run, tender_id, title="D'abans")
    new_run = await _extracted_run(db, tender_id)
    new = await _extracted_requirement(db, new_run, tender_id, title="D'ara")

    effective = await manual_requirement.effective_requirements(db, uuid.UUID(tender_id))

    assert [r.id for r in effective.extracted] == [new.id]
    assert old.id not in {r.id for r in effective.all}


async def test_a_re_extraction_does_not_take_the_manuals_away(client_a, db, tenant_a):
    """Una afirmacio humana no desapareix perque s'hagi tornat a analitzar."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    first_run = await _extracted_run(db, tender_id)
    await _extracted_requirement(db, first_run, tender_id, title="Primera passada")
    manual = await _ok(client_a, company, tender_id)

    second_run = await _extracted_run(db, tender_id)
    await _extracted_requirement(db, second_run, tender_id, title="Segona passada")

    effective = await manual_requirement.effective_requirements(db, uuid.UUID(tender_id))
    assert manual["id"] in {str(r.id) for r in effective.manual}
    assert len(effective.extracted) == 1


async def test_an_archived_manual_leaves_the_effective_set(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    created = await _ok(client_a, company, tender_id)
    await client_a.post(
        ARCHIVE.format(company=company.id, tender=tender_id, requirement=created["id"])
    )

    db.expire_all()
    effective = await manual_requirement.effective_requirements(db, uuid.UUID(tender_id))
    assert effective.manual == ()


async def test_without_any_run_the_manuals_are_the_whole_set(client_a, db, tenant_a):
    """Una licitacio oberta a ma pot no haver-se analitzat mai."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    created = await _ok(client_a, company, tender_id)

    effective = await manual_requirement.effective_requirements(db, uuid.UUID(tender_id))
    assert effective.run is None
    assert [str(r.id) for r in effective.all] == [created["id"]]


# --------------------------------------------------------------------------- #
# D. La base de dades també hi diu la seva                                      #
# --------------------------------------------------------------------------- #


async def test_a_manual_requirement_with_a_run_is_impossible(db, tender_document):
    from sqlalchemy.exc import IntegrityError

    from app.models.enums import RequirementCategory, RequirementScope

    run = await _extracted_run(db, str(tender_document.tender_id))
    db.add(
        Requirement(
            run_id=run.id,
            tender_id=tender_document.tender_id,
            origin=RequirementOrigin.MANUAL,
            category=RequirementCategory.OTHER,
            scope=RequirementScope.TENDER,
            title="Impossible",
            dedup_key=uuid.uuid4().hex,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_an_extracted_requirement_without_a_run_is_impossible(db, tender_document):
    from sqlalchemy.exc import IntegrityError

    from app.models.enums import RequirementCategory, RequirementScope

    db.add(
        Requirement(
            run_id=None,
            tender_id=tender_document.tender_id,
            origin=RequirementOrigin.EXTRACTED,
            category=RequirementCategory.OTHER,
            scope=RequirementScope.TENDER,
            title="Impossible",
            dedup_key=uuid.uuid4().hex,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# --------------------------------------------------------------------------- #
# E. Aïllament i licitacions públiques                                          #
# --------------------------------------------------------------------------- #


async def test_another_company_cannot_write_requirements(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    tender_id = await _tender(client_a, company_a)
    created = await _ok(client_a, company_a, tender_id)

    assert (await _create(client_b, company_b, tender_id)).status_code == 404
    assert (
        await client_b.patch(
            ONE.format(company=company_b.id, tender=tender_id, requirement=created["id"]),
            json={"title": "Robat"},
        )
    ).status_code == 404
    assert (
        await client_b.post(
            ARCHIVE.format(company=company_b.id, tender=tender_id, requirement=created["id"])
        )
    ).status_code == 404


async def test_a_user_of_both_companies_cannot_write_from_the_other_context(
    client_a, client_factory, db, tenant_a, tenant_b
):
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership
    from tests.conftest import TEST_PASSWORD, login

    user, company_a = tenant_a
    _, company_b = tenant_b
    tender_id = await _tender(client_a, company_a)
    created = await _ok(client_a, company_a, tender_id)
    db.add(
        CompanyMembership(user_id=user.id, company_id=company_b.id, role=MembershipRole.OWNER)
    )
    await db.commit()

    http = client_factory()
    await login(http, user.email, TEST_PASSWORD)

    assert (await _create(http, company_b, tender_id)).status_code == 404
    assert (
        await http.patch(
            ONE.format(company=company_b.id, tender=tender_id, requirement=created["id"]),
            json={"title": "Des de B"},
        )
    ).status_code == 404
    # I des d'A, si.
    assert (
        await http.patch(
            ONE.format(company=company_a.id, tender=tender_id, requirement=created["id"]),
            json={"title": "Des d'A"},
        )
    ).status_code == 200


async def test_a_public_tender_does_not_accept_manual_requirements_yet(
    client_a, tenant_a, tender_document
):
    """V1: `Requirement` no té `owner_company_id`.

    Deixar escriure'n sobre una licitació pública faria que el que escriu una
    empresa aparegués a totes les altres. Mentre no hi hagi tenancy a
    `Requirement`, la porta és només la ingesta manual.
    """
    _, company = tenant_a

    response = await _create(client_a, company, str(tender_document.tender_id))
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_not_manual"


async def test_the_extracted_requirements_of_a_public_tender_still_read(
    client_a, db, tender_document
):
    """La lectura no canvia: nomes s'ha tancat l'escriptura."""
    run = await _extracted_run(db, str(tender_document.tender_id))
    await _extracted_requirement(db, run, str(tender_document.tender_id))

    response = await client_a.get(ANALYSIS.format(tender=tender_document.tender_id))
    assert response.status_code == 200
    body = response.json()
    assert len(body["requirements"]) == 1
    assert body["requirements"][0]["origin"] == "extracted"
    assert body["requirements"][0]["run_id"] is not None


@pytest.mark.parametrize("role", ["owner", "admin", "bid_manager", "technical"])
async def test_whoever_prepares_the_candidacy_can_write_requirements(
    client_a, db, tenant_a, role
):
    """Incloent `technical`: escriure requisits es feina tecnica."""
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership

    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company.id,
        )
        .values(role=MembershipRole(role))
    )
    await db.commit()

    assert (await _create(client_a, company, tender_id)).status_code == 201


async def test_a_viewer_only_reads(client_a, db, tenant_a):
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership

    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company.id,
        )
        .values(role=MembershipRole.VIEWER)
    )
    await db.commit()

    assert (await _create(client_a, company, tender_id)).status_code == 404


# --------------------------------------------------------------------------- #
# F. Provinença                                                                 #
# --------------------------------------------------------------------------- #


async def _page_ref(db, client, company, tender_id) -> tuple[dict, str]:
    """Una cita a una pàgina d'un PDF real d'aquesta licitació."""
    from app.models.tender_documents import TenderDocumentPage
    from app.services import source_refs

    uploaded = await _upload(client, company, tender_id, name="PCAP.pdf", data=PLEC)
    page = (
        await db.execute(
            sa.select(TenderDocumentPage).where(
                TenderDocumentPage.extraction_id
                == uuid.UUID(uploaded["file"]["extraction_id"])
            )
        )
    ).scalars().first()
    start = page.text.index("Cal una assegurança")
    ref = await source_refs.create_extracted_page_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        extraction_id=uuid.UUID(uploaded["file"]["extraction_id"]),
        page_id=page.id,
        char_start=start,
        char_end=start + len("Cal una assegurança de responsabilitat civil."),
    )
    await db.commit()
    return uploaded, str(ref.id)


async def test_a_manual_requirement_can_cite_a_page(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded, ref_id = await _page_ref(db, client_a, company, tender_id)

    body = await _ok(client_a, company, tender_id, source_ref_ids=[ref_id])

    assert len(body["citations"]) == 1
    citation = body["citations"][0]
    assert citation["page_number"] == 1
    assert "assegurança" in citation["quote"]
    assert citation["content_sha256"] == uploaded["file"]["sha256"]


async def test_a_manual_requirement_can_cite_a_spreadsheet(client_a, db, tenant_a):
    """Un XLSX no té pàgines i tot i això es pot citar."""
    from app.services import source_refs

    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(
        client_a, company, tender_id, name="preus.xlsx", data=XLSX_V1, mime=XLSX
    )
    ref = await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(uploaded["file"]["file_id"]),
        location_label="Full Calculs, fila 42",
    )
    await db.commit()

    body = await _ok(client_a, company, tender_id, source_ref_ids=[str(ref.id)])

    assert body["citations"][0]["location_label"] == "Full Calculs, fila 42"
    assert body["citations"][0]["page_number"] is None


async def test_a_citation_of_another_tender_is_refused(client_a, db, tenant_a):
    from app.services import source_refs

    _, company = tenant_a
    mine = await _tender(client_a, company)
    other = await _tender(client_a, company, title="Una altra")
    uploaded = await _upload(
        client_a, company, other, name="preus.xlsx", data=XLSX_V1, mime=XLSX
    )
    ref = await source_refs.create_document_file_ref(
        db, tender_id=uuid.UUID(other), file_id=uuid.UUID(uploaded["file"]["file_id"])
    )
    await db.commit()

    response = await _create(client_a, company, mine, source_ref_ids=[str(ref.id)])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "source_ref_tender_mismatch"


async def test_a_citation_survives_a_new_document_version(client_a, db, tenant_a):
    """La cita del requisit segueix apuntant a v1, com totes."""
    from app.services import source_refs

    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    uploaded = await _upload(
        client_a, company, tender_id, name="preus.xlsx", data=XLSX_V1, mime=XLSX
    )
    sha_v1 = uploaded["file"]["sha256"]
    ref = await source_refs.create_document_file_ref(
        db,
        tender_id=uuid.UUID(tender_id),
        file_id=uuid.UUID(uploaded["file"]["file_id"]),
        location_label="Full Calculs, fila 42",
    )
    await db.commit()
    created = await _ok(client_a, company, tender_id, source_ref_ids=[str(ref.id)])

    versions = (
        f"/api/v1/companies/{company.id}/tenders/{tender_id}"
        f"/documents/{uploaded['id']}/versions"
    )
    v2 = await client_a.post(versions, files={"file": ("preus.xlsx", XLSX_V2, XLSX)})
    assert v2.status_code == 201

    response = await client_a.get(ANALYSIS.format(tender=tender_id))
    citation = next(
        r for r in response.json()["requirements"] if r["id"] == created["id"]
    )["citations"][0]
    assert citation["content_sha256"] == sha_v1
    assert citation["version_number"] == 1
    assert citation["content_sha256"] != v2.json()["file"]["sha256"]


async def test_unlinking_a_citation_does_not_delete_it(client_a, db, tenant_a):
    """Treure l'enllaç vol dir «ja no m'hi recolzo», no «no va existir»."""
    from app.models.provenance import TenderSourceRef

    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    _uploaded, ref_id = await _page_ref(db, client_a, company, tender_id)
    created = await _ok(client_a, company, tender_id, source_ref_ids=[ref_id])

    response = await client_a.delete(
        CITATIONS.format(company=company.id, tender=tender_id, requirement=created["id"])
        + f"/{ref_id}"
    )
    assert response.status_code == 200
    assert response.json()["citations"] == []

    assert await db.get(TenderSourceRef, uuid.UUID(ref_id)) is not None


async def test_archiving_keeps_the_citations(client_a, db, tenant_a):
    from app.models.provenance import RequirementSourceRefLink

    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    _uploaded, ref_id = await _page_ref(db, client_a, company, tender_id)
    created = await _ok(client_a, company, tender_id, source_ref_ids=[ref_id])

    await client_a.post(
        ARCHIVE.format(company=company.id, tender=tender_id, requirement=created["id"])
    )

    links = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(RequirementSourceRefLink)
            .where(RequirementSourceRefLink.requirement_id == uuid.UUID(created["id"]))
        )
    ).scalar_one()
    assert links == 1, "arxivar no es esborrar"


# --------------------------------------------------------------------------- #
# G. Què se'n ressent, i què no                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def workspace_of(db, tenant_a, client_a):
    """Una candidatura oberta sobre una licitació manual pròpia."""
    from app.models.enums import WorkspaceDecisionType
    from app.services import bid_workspace as workspace_service

    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    tender = await db.get(__import__("app.models.tenders", fromlist=["Tender"]).Tender,
                          uuid.UUID(tender_id))
    workspace = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=tender, user_id=user.id
        )
    ).workspace
    await workspace_service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=__import__("app.models.enums", fromlist=["MembershipRole"]).MembershipRole.OWNER,
        reason="Ens hi presentem.",
    )
    await db.commit()
    # Els identificadors es guarden a part: els tests expiren la sessio per
    # rellegir l'estat, i despres una instancia expirada no es pot consultar
    # des de codi sincron.
    return {
        "workspace": workspace,
        "workspace_id": workspace.id,
        "tender_id": tender_id,
        "company": company,
        "company_id": company.id,
        "user": user,
    }


async def test_a_manual_requirement_reaches_the_technical_scope(client_a, db, workspace_of):
    """Si no hi arribés, el sistema diria que no cal cap solució per a una cosa
    que sí que en necessita."""
    from app.services import technical_scope

    created = await _ok(client_a, workspace_of["company"], workspace_of["tender_id"])

    scope = await technical_scope.resolve(db, workspace_of["workspace"])
    assert created["id"] in {str(r.id) for r in scope.requirements}


async def test_writing_a_requirement_makes_a_confirmed_scope_stale(
    client_a, db, workspace_of
):
    """L'empremta de l'abast es fa del conjunt efectiu, així que es mou sola."""
    from app.services import technical_scope

    workspace = workspace_of["workspace"]
    await technical_scope.confirm(db, workspace, user=workspace_of["user"])
    await db.commit()
    assert (await technical_scope.review_state(db, workspace)).stale is False

    await _ok(client_a, workspace_of["company"], workspace_of["tender_id"])

    db.expire_all()
    workspace = await db.get(BidWorkspace, workspace_of["workspace_id"])
    assert (await technical_scope.review_state(db, workspace)).stale is True


async def test_archiving_a_requirement_also_makes_the_scope_stale(client_a, db, workspace_of):
    from app.services import technical_scope

    workspace = workspace_of["workspace"]
    created = await _ok(client_a, workspace_of["company"], workspace_of["tender_id"])
    await technical_scope.confirm(db, workspace, user=workspace_of["user"])
    await db.commit()
    assert (await technical_scope.review_state(db, workspace)).stale is False

    await client_a.post(
        ARCHIVE.format(
            company=workspace_of["company_id"],
            tender=workspace_of["tender_id"],
            requirement=created["id"],
        )
    )

    db.expire_all()
    workspace = await db.get(BidWorkspace, workspace_of["workspace_id"])
    assert (await technical_scope.review_state(db, workspace)).stale is True


async def test_a_patch_that_changes_nothing_does_not_make_the_scope_stale(
    client_a, db, workspace_of
):
    """El no-op no ha d'encendre res: si ho fes, el senyal es gastaria."""
    from app.services import technical_scope

    workspace = workspace_of["workspace"]
    created = await _ok(client_a, workspace_of["company"], workspace_of["tender_id"])
    await technical_scope.confirm(db, workspace, user=workspace_of["user"])
    await db.commit()

    await client_a.patch(
        ONE.format(
            company=workspace_of["company_id"],
            tender=workspace_of["tender_id"],
            requirement=created["id"],
        ),
        json={"title": created["title"]},
    )

    db.expire_all()
    workspace = await db.get(BidWorkspace, workspace_of["workspace_id"])
    assert (await technical_scope.review_state(db, workspace)).stale is False


async def test_a_manual_requirement_generates_checklist_work(client_a, db, workspace_of):
    """La checklist surt del conjunt efectiu."""
    from app.services import bid_workspace as workspace_service

    created = await _ok(client_a, workspace_of["company"], workspace_of["tender_id"])

    relevant = await workspace_service.relevant_requirements(db, workspace_of["workspace"])
    assert created["id"] in {str(r.id) for r in relevant}


async def test_manual_requirements_do_not_touch_the_analysed_corpus(client_a, db, tenant_a):
    """`input_fingerprint` parla del corpus DOCUMENTAL d'una extracció.

    Escriure un requisit a mà no analitza res, així que no pot fer que l'anàlisi
    cobreixi uns documents que ningú no ha tornat a llegir.
    """
    from app.services import requirement_analysis

    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    await _upload(
        client_a, company, tender_id, name="PCAP.pdf", data=PLEC,
    )
    before = await requirement_analysis.analysis_input(db, uuid.UUID(tender_id))

    await _ok(client_a, company, tender_id)

    after = await requirement_analysis.analysis_input(db, uuid.UUID(tender_id))
    assert after.fingerprint == before.fingerprint


async def test_manual_requirements_do_not_mark_documents_as_reviewed(
    client_a, db, workspace_of
):
    """Llegir els plecs i escriure un requisit són dues accions diferents."""
    from app.services import document_review

    workspace = workspace_of["workspace"]
    before = await document_review.review_state(db, workspace)

    await _ok(client_a, workspace_of["company"], workspace_of["tender_id"])

    db.expire_all()
    workspace = await db.get(BidWorkspace, workspace_of["workspace_id"])
    after = await document_review.review_state(db, workspace)
    assert after.reviewed == before.reviewed
    assert workspace.documents_reviewed_at is None


async def test_requirement_evidence_is_untouched(db):
    """La provinença dels extrets segueix sent la que era."""
    from app.models.requirements import RequirementEvidence

    columns = RequirementEvidence.__table__.columns
    assert columns["extraction_id"].nullable is False
    assert columns["page_id"].nullable is False
