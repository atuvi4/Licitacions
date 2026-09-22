"""Revisió humana dels plecs, versions noves i els tres findings (S3-S3).

Tres preguntes que abans compartien una sola resposta i que aquí queden
separades per sempre:

* **hi ha plecs que ningú no ha confirmat haver mirat?** — que ningú no ho hagi
  dit no vol dir que hi hagi res malament; vol dir que no se n'ha dit res, i
  callar no és aprovar;
* **el conjunt ha canviat des que algú ho va dir?**;
* **l'anàlisi vigent cobreix el corpus d'ara?**

Cap de les tres diu que els requisits hagin canviat. Això només es pot saber
tornant-los a extreure, i això és una altra acció que ningú no fa sol.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.tender_documents import TenderDocumentFile
from app.services import document_review
from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

CREATE = "/api/v1/companies/{company}/tenders"
UPLOAD = "/api/v1/companies/{company}/tenders/{tender}/documents"
VERSIONS = "/api/v1/companies/{company}/tenders/{tender}/documents/{document}/versions"

PDF = "application/pdf"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 64


async def _tender(client, company) -> str:
    response = await client.post(
        CREATE.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": "Serveis de comunicacions"},
    )
    assert response.status_code == 201, response.text
    return response.json()["tender"]["id"]


async def _upload(client, company, tender_id, *, name, data, mime=PDF, document_type=None):
    """Puja un plec. `document_type` el declara qui puja; sense ell es `OTHER`.

    Te consequencia real: l'analisi automatica nomes tria PCAP, PPT i annexos,
    aixi que un document sense classificar no hi entra sol.
    """
    return await client.post(
        UPLOAD.format(company=company.id, tender=tender_id),
        files={"file": (name, data, mime)},
        data={"document_type": document_type} if document_type else None,
    )


async def _version(client, company, tender_id, document_id, *, name, data, mime=PDF):
    return await client.post(
        VERSIONS.format(company=company.id, tender=tender_id, document=document_id),
        files={"file": (name, data, mime)},
    )


# --------------------------------------------------------------------------- #
# A. L'empremta documental                                                      #
# --------------------------------------------------------------------------- #


async def test_a_tender_without_documents_has_a_stable_fingerprint(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)

    first = await document_review.fingerprint_of(db, uuid.UUID(tender_id))
    second = await document_review.fingerprint_of(db, uuid.UUID(tender_id))

    assert first.document_count == 0
    assert first.fingerprint == second.fingerprint


async def test_the_fingerprint_moves_when_a_document_arrives(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    before = await document_review.fingerprint_of(db, uuid.UUID(tender_id))

    await _upload(client_a, company, tender_id, name="PCAP.pdf", data=build_pdf(["Plec"]))

    after = await document_review.fingerprint_of(db, uuid.UUID(tender_id))
    assert after.fingerprint != before.fingerprint
    assert after.document_count == 1


async def test_a_format_we_cannot_read_counts_the_same(client_a, db, tenant_a):
    """No saber-ne llegir el text es una limitacio NOSTRA, no un fet del plec.

    Una persona ha de revisar un XLSX igual que un PDF, i si no entres a
    l'empremta, afegir-ne un seria invisible per a la revisio.
    """
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    await _upload(client_a, company, tender_id, name="PCAP.pdf", data=build_pdf(["Plec"]))
    before = await document_review.fingerprint_of(db, uuid.UUID(tender_id))

    uploaded = await _upload(
        client_a, company, tender_id, name="preus.xlsx", data=ZIP_BYTES, mime=XLSX
    )
    assert uploaded.json()["file"]["analysis_status"] == "unsupported"

    after = await document_review.fingerprint_of(db, uuid.UUID(tender_id))
    assert after.fingerprint != before.fingerprint
    assert after.document_count == 2


async def test_the_fingerprint_does_not_depend_on_the_extraction(client_a, db, tenant_a):
    """Reextreure no canvia QUINS documents hi ha. Es empremta DOCUMENTAL."""
    from app.services import tender_document_processing

    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    body = (
        await _upload(client_a, company, tender_id, name="PCAP.pdf", data=build_pdf(["Plec"]))
    ).json()
    before = await document_review.fingerprint_of(db, uuid.UUID(tender_id))

    from app.models.tender_documents import TenderDocumentContent

    content = (
        await db.execute(
            sa.select(TenderDocumentContent)
            .join(
                TenderDocumentFile,
                TenderDocumentFile.content_id == TenderDocumentContent.id,
            )
            .where(TenderDocumentFile.id == uuid.UUID(body["file"]["file_id"]))
        )
    ).scalars().one()
    await tender_document_processing._ensure_current_extraction(db, content, force=True)
    await db.commit()

    after = await document_review.fingerprint_of(db, uuid.UUID(tender_id))
    assert after.fingerprint == before.fingerprint


# --------------------------------------------------------------------------- #
# B. Versions                                                                   #
# --------------------------------------------------------------------------- #


async def test_new_bytes_create_a_new_version_of_the_same_document(client_a, db, tenant_a):
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    first = (
        await _upload(client_a, company, tender_id, name="PCAP.pdf", data=build_pdf(["v1"]))
    ).json()

    response = await _version(
        client_a, company, tender_id, first["id"], name="PCAP.pdf", data=build_pdf(["v2"])
    )
    assert response.status_code == 201, response.text
    body = response.json()

    assert body["id"] == first["id"], "el document logic es el mateix"
    assert body["file"]["version_number"] == 2
    assert body["file"]["file_id"] != first["file"]["file_id"]


async def test_the_previous_version_survives_intact(client_a, db, tenant_a):
    """Una cita pot apuntar a la versio anterior i ha de continuar dient el mateix."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    first = (
        await _upload(client_a, company, tender_id, name="PCAP.pdf", data=build_pdf(["v1"]))
    ).json()
    await _version(
        client_a, company, tender_id, first["id"], name="PCAP.pdf", data=build_pdf(["v2"])
    )

    db.expire_all()
    files = (
        await db.execute(
            sa.select(TenderDocumentFile)
            .where(TenderDocumentFile.tender_document_id == uuid.UUID(first["id"]))
            .order_by(TenderDocumentFile.version_number)
        )
    ).scalars().all()
    assert [f.version_number for f in files] == [1, 2]
    assert [f.is_current for f in files] == [False, True]
    # La v1 conserva el seu contingut: no s'ha sobreescrit res.
    assert files[0].content_id is not None
    assert files[0].content_id != files[1].content_id


async def test_the_same_bytes_are_not_a_new_version(client_a, db, tenant_a):
    """Tornar a pujar el mateix fitxer no es cap canvi, i no ho ha de semblar."""
    _, company = tenant_a
    tender_id = await _tender(client_a, company)
    payload = build_pdf(["Igual"])
    first = (
        await _upload(client_a, company, tender_id, name="PCAP.pdf", data=payload)
    ).json()
    before = await document_review.fingerprint_of(db, uuid.UUID(tender_id))

    response = await _version(
        client_a, company, tender_id, first["id"], name="PCAP.pdf", data=payload
    )
    assert response.status_code == 200
    body = response.json()
    assert body["file"]["version_number"] == 1
    assert body["content_reused"] is True

    db.expire_all()
    files = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocumentFile)
            .where(TenderDocumentFile.tender_document_id == uuid.UUID(first["id"]))
        )
    ).scalar_one()
    assert files == 1
    after = await document_review.fingerprint_of(db, uuid.UUID(tender_id))
    assert after.fingerprint == before.fingerprint


async def test_a_version_of_a_document_of_another_tender_is_refused(client_a, tenant_a):
    """La ruta afirma una relacio; afirmar-la sense comprovar-la seria un forat."""
    _, company = tenant_a
    first_tender = await _tender(client_a, company)
    other_tender = await _tender(client_a, company)
    document = (
        await _upload(client_a, company, first_tender, name="PCAP.pdf", data=build_pdf(["x"]))
    ).json()

    response = await _version(
        client_a, company, other_tender, document["id"], name="PCAP.pdf", data=build_pdf(["y"])
    )
    assert response.status_code == 404


async def test_another_company_cannot_add_a_version(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    tender_id = await _tender(client_a, company_a)
    document = (
        await _upload(client_a, company_a, tender_id, name="PCAP.pdf", data=build_pdf(["x"]))
    ).json()

    response = await _version(
        client_b, company_b, tender_id, document["id"], name="PCAP.pdf", data=build_pdf(["y"])
    )
    assert response.status_code == 404


@pytest.mark.parametrize("role", ["technical", "viewer"])
async def test_who_does_not_decide_does_not_add_versions(client_a, db, tenant_a, role):
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership

    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    document = (
        await _upload(client_a, company, tender_id, name="PCAP.pdf", data=build_pdf(["x"]))
    ).json()
    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company.id,
        )
        .values(role=MembershipRole(role))
    )
    await db.commit()

    response = await _version(
        client_a, company, tender_id, document["id"], name="PCAP.pdf", data=build_pdf(["y"])
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# C. La revisió humana i els tres findings                                      #
# --------------------------------------------------------------------------- #

from tests.test_submission_package import (  # noqa: E402
    add_extraction_run,
    build_ready_candidacy,
)

REVIEW = "/api/v1/companies/{company}/workspaces/{workspace}/documents/review"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"

#: Els tipus que l'analisi automatica tria (`DEFAULT_DOCUMENT_TYPES`). Un plec
#: sense classificar es queda fora del corpus, i per aixo als tests de corpus
#: s'hi declara.
PCAP = "administrative_terms"
PPT = "technical_terms"

#: Uns bytes prou llargs perque l'extraccio els consideri text aprofitable.
PLEC = build_pdf(["Plec " * 40])
TECNIC = build_pdf(["Tecnic " * 40])
PLEC_V2 = build_pdf(["v2 " * 40])


@pytest.fixture
async def tender(db, tenant_a):
    """Una licitacio oberta a ma, que es la que aquesta empresa pot editar."""
    from datetime import UTC, datetime, timedelta

    from app.services import manual_tender, tender_lifecycle

    user, company = tenant_a
    tender, _created = await manual_tender.create_manual_tender(
        db,
        company_id=company.id,
        user=user,
        data=manual_tender.ManualTenderInput(
            client_token=uuid.uuid4(), title="Servei de manteniment integral"
        ),
    )
    tender.deadline_at = datetime.now(UTC) + timedelta(days=40)
    await db.flush()
    await tender_lifecycle.record_revision(db, tender, trigger="manual_edit")
    await db.commit()
    await db.refresh(tender)
    return tender


@pytest.fixture
async def extraction_run(db, tender):
    return await add_extraction_run(db, tender)


@pytest.fixture
async def ready(db, tenant_a, tender, extraction_run):
    return await build_ready_candidacy(db, tenant_a, tender, extraction_run)


async def _codes(client, ready) -> set[str]:
    response = await client.get(
        READINESS.format(company=ready["company"].id, workspace=ready["workspace"].id)
    )
    assert response.status_code == 200, response.text
    return {finding["code"] for finding in response.json()["findings"]}


async def _finding(client, ready, code) -> dict | None:
    response = await client.get(
        READINESS.format(company=ready["company"].id, workspace=ready["workspace"].id)
    )
    return next((f for f in response.json()["findings"] if f["code"] == code), None)


async def _review(client, ready):
    return await client.post(
        REVIEW.format(company=ready["company"].id, workspace=ready["workspace"].id)
    )


async def test_without_documents_there_is_nothing_to_review(client_a, ready):
    """Cap document per revisar no es cap pendent: es que no hi ha res.

    Demanar que algu confirmi el buit seria una feina inventada, i el finding
    s'aniria gastant fins que ningu no el miraria el dia que si que calgues.
    """
    assert "tender_documents_unreviewed" not in await _codes(client_a, ready)


async def test_a_document_that_nobody_has_looked_at_is_reported(client_a, ready):
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["Plec"]))

    finding = await _finding(client_a, ready, "tender_documents_unreviewed")
    assert finding is not None
    assert finding["severity"] == "info"
    assert finding["domain"] == "workspace"
    assert finding["context"]["documents"] == 1


async def test_marking_the_review_clears_it(client_a, ready):
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["Plec"]))

    response = await _review(client_a, ready)
    assert response.status_code == 200

    codes = await _codes(client_a, ready)
    assert "tender_documents_unreviewed" not in codes
    assert "tender_documents_changed" not in codes


async def test_a_new_document_after_the_review_is_a_change(client_a, ready):
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["Plec"]))
    await _review(client_a, ready)

    await _upload(client_a, company, tender.id, name="PPT.pdf", data=build_pdf(["Tecnic"]))

    finding = await _finding(client_a, ready, "tender_documents_changed")
    assert finding is not None
    assert finding["severity"] == "warning"
    assert finding["domain"] == "workspace"


async def test_a_new_version_after_the_review_is_also_a_change(client_a, ready):
    company, tender = ready["company"], ready["tender"]
    document = (
        await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["v1"]))
    ).json()
    await _review(client_a, ready)

    await _version(
        client_a, company, tender.id, document["id"], name="PCAP.pdf", data=build_pdf(["v2"])
    )

    assert "tender_documents_changed" in await _codes(client_a, ready)


async def test_re_uploading_the_same_bytes_does_not_reopen_the_review(client_a, ready):
    """El cas que gastaria el senyal si no es distingis."""
    company, tender = ready["company"], ready["tender"]
    payload = build_pdf(["Igual"])
    document = (
        await _upload(client_a, company, tender.id, name="PCAP.pdf", data=payload)
    ).json()
    await _review(client_a, ready)

    await _version(client_a, company, tender.id, document["id"], name="PCAP.pdf", data=payload)

    assert "tender_documents_changed" not in await _codes(client_a, ready)


async def test_reviewing_again_clears_the_change(client_a, ready):
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["Plec"]))
    await _review(client_a, ready)
    await _upload(client_a, company, tender.id, name="PPT.pdf", data=build_pdf(["Tecnic"]))
    assert "tender_documents_changed" in await _codes(client_a, ready)

    await _review(client_a, ready)

    assert "tender_documents_changed" not in await _codes(client_a, ready)


async def test_an_unreadable_format_also_needs_reviewing(client_a, ready):
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["Plec"]))
    await _review(client_a, ready)

    await _upload(client_a, company, tender.id, name="preus.xlsx", data=ZIP_BYTES, mime=XLSX)

    assert "tender_documents_changed" in await _codes(client_a, ready)


async def test_reviewing_twice_without_changes_is_idempotent(client_a, db, ready):
    """No mou la data: la revisio vigent segueix sent la mateixa."""
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["Plec"]))
    await _review(client_a, ready)

    from app.models.workspace import BidWorkspace

    # Les URL es capturen ABANS d'expirar la sessio: despres, llegir
    # `ready["company"].id` tornaria a la base de dades des de codi sincron.
    workspace_id = ready["workspace"].id
    review_url = REVIEW.format(company=ready["company"].id, workspace=workspace_id)

    async def stored():
        db.expire_all()
        row = (
            await db.execute(
                sa.select(
                    BidWorkspace.documents_reviewed_at,
                    BidWorkspace.documents_reviewed_fingerprint,
                ).where(BidWorkspace.id == workspace_id)
            )
        ).one()
        return row

    first = await stored()
    assert first[1] is not None

    assert (await client_a.post(review_url)).status_code == 200

    assert await stored() == first


async def test_the_review_does_not_extract_or_validate_anything(client_a, db, ready):
    """Es una declaracio humana i prou.

    Llegir els plecs i analitzar-los son dues accions diferents. Si marcar la
    revisio disparés una extraccio, algu es trobaria una analisi nova —i una
    factura— per haver dit que havia mirat uns documents.
    """
    from app.models.requirements import RequirementExtractionRun

    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["Plec"]))
    before = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(RequirementExtractionRun)
            .where(RequirementExtractionRun.tender_id == tender.id)
        )
    ).scalar_one()

    await _review(client_a, ready)

    after = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(RequirementExtractionRun)
            .where(RequirementExtractionRun.tender_id == tender.id)
        )
    ).scalar_one()
    assert after == before


@pytest.mark.parametrize("role", ["technical", "viewer"])
async def test_who_does_not_decide_does_not_mark_the_review(client_a, db, ready, role):
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership

    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == ready["user"].id,
            CompanyMembership.company_id == ready["company"].id,
        )
        .values(role=MembershipRole(role))
    )
    await db.commit()

    assert (await _review(client_a, ready)).status_code == 404


# --------------------------------------------------------------------------- #
# D. L'anàlisi i el corpus d'ara                                                #
# --------------------------------------------------------------------------- #
#
# Aquest finding NO compara dates. Compara el CORPUS: `input_fingerprint` diu
# sobre quines extraccions es va fer una run, i `analysis_input` recalcula la
# mateixa empremta amb el que hi ha ara. Dues dates poden ordenar-se bé i parlar
# de coses diferents —reextreure un document amb un parser nou no mou cap
# `created_at` de la run—, i aquesta comparació no té aquest problema.


async def _align_run_with_corpus(db, tender):
    """Deixa la run vigent apuntant al corpus documental actual.

    És el que faria una anàlisi de debò: llegir el que hi ha ara i desar-ne
    l'empremta. Aquí es fa a mà perquè analitzar de veritat costa diners i
    aquesta fase no crida cap model.
    """
    from app.services import requirement_analysis

    current = await requirement_analysis.analysis_input(db, tender.id)
    run = await requirement_analysis.current_run_for(db, tender.id)
    run.input_fingerprint = current.fingerprint
    await db.commit()
    return current


async def test_an_analysis_over_the_current_corpus_says_nothing(client_a, db, ready):
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=PLEC, document_type=PCAP)
    await _align_run_with_corpus(db, tender)

    assert "requirement_analysis_predates_documents" not in await _codes(client_a, ready)


async def test_a_document_that_arrives_after_the_analysis_is_reported(client_a, db, ready):
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=PLEC, document_type=PCAP)
    await _align_run_with_corpus(db, tender)

    await _upload(client_a, company, tender.id, name="PPT.pdf", data=TECNIC, document_type=PPT)

    finding = await _finding(client_a, ready, "requirement_analysis_predates_documents")
    assert finding is not None
    assert finding["severity"] == "warning"
    assert finding["domain"] == "technical"


async def test_a_new_version_after_the_analysis_is_reported_too(client_a, db, ready):
    company, tender = ready["company"], ready["tender"]
    document = (
        await _upload(client_a, company, tender.id, name="PCAP.pdf", data=PLEC, document_type=PCAP)
    ).json()
    await _align_run_with_corpus(db, tender)

    await _version(
        client_a, company, tender.id, document["id"], name="PCAP.pdf", data=PLEC_V2
    )

    assert "requirement_analysis_predates_documents" in await _codes(client_a, ready)


async def test_re_analysing_over_the_current_corpus_clears_it(client_a, db, ready):
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=PLEC, document_type=PCAP)
    await _align_run_with_corpus(db, tender)
    await _upload(client_a, company, tender.id, name="PPT.pdf", data=TECNIC, document_type=PPT)
    assert "requirement_analysis_predates_documents" in await _codes(client_a, ready)

    await _align_run_with_corpus(db, tender)

    assert "requirement_analysis_predates_documents" not in await _codes(client_a, ready)


async def test_marking_the_review_does_not_clear_the_analysis_finding(client_a, db, ready):
    """Són dues accions diferents i no es tapen l'una a l'altra.

    Dir que has mirat els plecs no fa que l'anàlisi els cobreixi. Si marcar la
    revisió apagués aquest avís, algú tancaria una candidatura amb uns requisits
    extrets d'uns documents que ja no són els que hi ha.
    """
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=PLEC, document_type=PCAP)
    await _align_run_with_corpus(db, tender)
    await _upload(client_a, company, tender.id, name="PPT.pdf", data=TECNIC, document_type=PPT)

    await _review(client_a, ready)

    codes = await _codes(client_a, ready)
    assert "tender_documents_changed" not in codes
    assert "requirement_analysis_predates_documents" in codes


async def test_a_format_without_text_does_not_outdate_the_analysis(client_a, db, ready):
    """Un XLSX nou s'ha de revisar, però l'anàlisi no el cobria abans ni ara.

    Dir que l'anàlisi ha quedat endarrerida per un document del qual no sabem
    llegir el text seria demanar una reanàlisi que no canviaria res.
    """
    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=PLEC, document_type=PCAP)
    await _align_run_with_corpus(db, tender)

    await _upload(client_a, company, tender.id, name="preus.xlsx", data=ZIP_BYTES, mime=XLSX)

    codes = await _codes(client_a, ready)
    assert "tender_documents_unreviewed" in codes
    assert "requirement_analysis_predates_documents" not in codes


# --------------------------------------------------------------------------- #
# E. Aïllament                                                                  #
# --------------------------------------------------------------------------- #


async def test_the_review_of_a_workspace_of_another_company_is_not_reachable(
    client_b, tenant_b, ready
):
    _, company_b = tenant_b
    response = await client_b.post(
        REVIEW.format(company=company_b.id, workspace=ready["workspace"].id)
    )
    assert response.status_code == 404


async def test_the_same_bytes_in_two_companies_are_reviewed_separately(
    client_a, client_factory, db, tenant_a, tenant_b, ready
):
    """El contingut es comparteix; la revisió, no.

    Els mateixos bytes donen un sol `TenderDocumentContent`. Si la revisió
    anés lligada al contingut, que una empresa hagués mirat el seu plec faria
    desaparèixer l'avís de l'altra, que no l'ha mirat mai.
    """
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership
    from app.services import document_review as review_service
    from app.services import manual_tender
    from tests.conftest import TEST_PASSWORD, login

    user, company_a = tenant_a
    _, company_b = tenant_b
    payload = build_pdf(["El mateix plec per a totes dues. " * 20])

    company, tender = ready["company"], ready["tender"]
    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=payload)
    await _review(client_a, ready)

    db.add(
        CompanyMembership(user_id=user.id, company_id=company_b.id, role=MembershipRole.OWNER)
    )
    await db.commit()
    http = client_factory()
    await login(http, user.email, TEST_PASSWORD)

    tender_b, _created = await manual_tender.create_manual_tender(
        db,
        company_id=company_b.id,
        user=user,
        data=manual_tender.ManualTenderInput(
            client_token=uuid.uuid4(), title="La de B"
        ),
    )
    await db.commit()
    uploaded = await _upload(http, company_b, tender_b.id, name="PCAP.pdf", data=payload)
    assert uploaded.status_code == 201
    # Mateixos bytes, un sol contingut.
    assert uploaded.json()["content_reused"] is True

    # A te la seva revisio feta; B no n'ha fet cap, i les dues empremtes son
    # de licitacions diferents encara que els bytes coincideixin.
    #
    # Cal rellegir el workspace: la revisio la va escriure la sessio de la
    # peticio, i la d'aquest test encara te la fila tal com estava.
    await db.refresh(ready["workspace"])
    state_a = await review_service.review_state(db, ready["workspace"])
    assert state_a.reviewed is True
    assert state_a.stale is False

    fingerprint_b = await review_service.fingerprint_of(db, tender_b.id)
    assert fingerprint_b.fingerprint != state_a.current_fingerprint


# --------------------------------------------------------------------------- #
# F. El paquet i la solució tècnica                                             #
# --------------------------------------------------------------------------- #


async def test_a_new_document_still_does_not_invalidate_an_approved_package(
    client_a, db, ready
):
    """L'invariant d'S3-S2.1, intacte."""
    from app.services import submission_package
    from tests.test_submission_package import approve, freeze

    package = await freeze(db, ready)
    await approve(db, ready, package)
    company, tender = ready["company"], ready["tender"]

    await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["Plec"]))

    report = await submission_package.evaluate_staleness(db, ready["workspace"], package)
    assert "tender_changed" not in report.reasons


async def test_a_new_version_does_make_the_package_stale_today(client_a, db, ready):
    """AVUI una versió nova SÍ que caduca el paquet, i cal decidir-ho.

    Ve de la taxonomia existent: substituir el contingut sota el mateix
    document dona `DOCUMENT_REPLACED`, que porta `WORKSPACE_CRITICAL` perquè es
    va escriure per al cas d'un òrgan que canvia un plec ja publicat.
    Classificar-ho d'una altra manera seria canviar la taxonomia, i això no es
    fa en silenci: queda reportat.

    Aquest test fixa el comportament REAL d'avui, no el desitjat.
    """
    from app.services import submission_package
    from tests.test_submission_package import approve, freeze

    company, tender = ready["company"], ready["tender"]
    document = (
        await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["v1"]))
    ).json()
    package = await freeze(db, ready)
    await approve(db, ready, package)

    await _version(
        client_a, company, tender.id, document["id"], name="PCAP.pdf", data=build_pdf(["v2"])
    )

    report = await submission_package.evaluate_staleness(db, ready["workspace"], package)
    assert "tender_changed" in report.reasons


async def test_a_new_version_does_not_unvalidate_a_technical_solution(client_a, db, ready):
    """Substituir un plec no desfà la firma de ningú.

    La validació es lliga a l'empremta del contingut de la proposta i a la del
    requisit. Cap de les dues no la mou un document que encara no s'ha tornat a
    llegir.
    """
    from app.services import technical_scope, technical_solution
    from tests.test_submission_package import _with_validated_solution

    company, tender = ready["company"], ready["tender"]
    document = (
        await _upload(client_a, company, tender.id, name="PCAP.pdf", data=build_pdf(["v1"]))
    ).json()
    solution = await _with_validated_solution(db, ready)
    validated = (solution.validated_at, solution.validated_by)
    await technical_scope.confirm(db, ready["workspace"], user=ready["user"])

    await _version(
        client_a, company, tender.id, document["id"], name="PCAP.pdf", data=build_pdf(["v2"])
    )

    await db.refresh(solution)
    assert (solution.validated_at, solution.validated_by) == validated
    assert await technical_solution.stale_reasons_for(db, [solution]) == {solution.id: []}
    assert (await technical_scope.review_state(db, ready["workspace"])).stale is False
