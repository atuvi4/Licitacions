"""Cobertura del paquet != processabilitat dels documents.

Invariant: `incomplete` NOMES vol dir que sabem que un document forma part del
paquet public i Tuvi no el te. Un document present que no es pot llegir (format no
suportat, extraccio fallida, sense text) o amb un rol desconegut no fa mai el
paquet incomplet: es un avis de processament, a part, amb la seva acceptacio.

Tot per les vies reals: classificador global, via de l'operador (afegir, verificar,
retirar), fitxa de la licitacio i quota. Cap LLM (`RealNamedFake`, cap xarxa).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.ai.profiles import V1
from app.core.config import settings
from app.models.enums import (
    ExtractionStatus,
    GlobalClassificationTrigger,
    PackageGapCode,
    TenderDocumentType,
)
from app.models.tender_documents import TenderDocumentExtraction
from app.services import global_document_classification as global_classification
from app.services import tender_analysis
from app.services.analysis_planning import build_preview
from app.services.requirement_analysis import analysis_input
from app.services.tender_document_processing import process_tender_document
from tests.conftest import login
from tests.fixtures.pdf_builder import build_pdf
from tests.quota_support import pin_real_model
from tests.real_provider import RealNamedFake
from tests.test_global_classification import (
    _BODY,
    QUADRE_PDF,
    add_feed_document,
    current_run,
    gaps_of,
    make_public_tender,
    platform_url,
    public_package,
    resolver,
    suggestions_of,
)
from tests.test_office_text import _docx, _p
from tests.test_platform_onboarding import create_operator
from tests.test_tender_document_processing import serving

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def public_tender(db):
    return await make_public_tender(db)


@pytest.fixture
async def op_client(client_factory, db):
    operator = await create_operator(db)
    http_client = client_factory()
    await login(http_client, operator.email)
    return http_client


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ZIP_BYTES = b"PK\x03\x04" + b"\x00" * 64  # un ZIP que no es cap document d'oficina
QUADRE_DEFINITIU_PDF = build_pdf(
    ["Quadre de caracteristiques i clausules especifiques (definitiu)\nCodi DOC-03\n" + _BODY]
)


async def _upload(op_client, tender, filename: str, payload: bytes, mime: str) -> str:
    response = await op_client.post(
        f"{platform_url(tender.id)}/documents/upload",
        files={"file": (filename, payload, mime)},
        data={
            "contribution_source_url": "https://contractaciopublica.cat/ca/detall/123",
            "document_type": TenderDocumentType.ADDITIONAL.value,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _verify(op_client, tender, document_id: str) -> None:
    response = await op_client.post(
        f"{platform_url(tender.id)}/documents/{document_id}/verify", json={}
    )
    assert response.status_code == 200, response.text


async def _retire(op_client, tender, document_id: str, reason: str) -> None:
    response = await op_client.post(
        f"{platform_url(tender.id)}/documents/{document_id}/retire", json={"reason": reason}
    )
    assert response.status_code == 200, response.text


async def _added(op_client, tender, filename, payload, mime="application/pdf") -> str:
    document_id = await _upload(op_client, tender, filename, payload, mime)
    await _verify(op_client, tender, document_id)
    return document_id


async def _package(client_a, db, tender) -> dict:
    """El paquet tal com el veu una empresa a la fitxa."""
    response = await client_a.get(f"/api/v1/tenders/{tender.id}")
    assert response.status_code == 200, response.text
    return response.json()["package"]


async def _complete_public_package(db, op_client, tender) -> str:
    """PCAP (DOC-01, cita DOC-03) + PPT (DOC-02) del feed i el DOC-03 aportat i verificat."""
    await public_package(db, tender)
    await global_classification.classify_tender_globally(
        db, tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
    )
    return await _added(op_client, tender, "DOC-03 Quadre.pdf", QUADRE_PDF)


# --------------------------------------------------------------------------- #
# 1. Tots presents + un format no processable                                   #
# --------------------------------------------------------------------------- #


async def test_tots_presents_i_un_format_no_processable_es_complet_amb_avis(
    db, client_a, op_client, public_tender
):
    await _complete_public_package(db, op_client, public_tender)
    zipped = await _added(op_client, public_tender, "Annexos.zip", ZIP_BYTES, "application/zip")

    package = await _package(client_a, db, public_tender)
    assert package["status"] == "complete"
    assert package["gaps"] == []
    assert package["processing"] == [
        {"code": "format_not_analyzable", "key": zipped, "would_provide": []}
    ]
    state = await tender_analysis.package_state(db, public_tender.id)
    assert state.status == "complete"


# --------------------------------------------------------------------------- #
# 2. Un document oficial conegut absent                                         #
# --------------------------------------------------------------------------- #


async def test_un_document_oficial_conegut_absent_fa_el_paquet_incomplet(
    db, client_a, public_tender
):
    await public_package(db, public_tender)  # el PCAP cita el DOC-03, que no hi es
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
    )

    package = await _package(client_a, db, public_tender)
    assert package["status"] == "incomplete"
    assert [(g["code"], g["key"]) for g in package["gaps"]] == [
        ("referenced_document_missing", "DOC-03")
    ]
    assert package["processing"] == []


# --------------------------------------------------------------------------- #
# 3. Present amb l'extraccio fallida                                            #
# --------------------------------------------------------------------------- #


async def test_un_document_present_amb_l_extraccio_fallida_no_es_cap_forat_documental(
    db, client_a, op_client, public_tender
):
    await _complete_public_package(db, op_client, public_tender)
    broken = await _added(
        op_client, public_tender, "Acord JGL.docx", b"PK\x03\x04 malmes" + b"\x00" * 32, DOCX
    )
    extraction = await db.scalar(
        sa.select(TenderDocumentExtraction)
        .where(TenderDocumentExtraction.parser_version == "docx-v1")
        .execution_options(populate_existing=True)
    )
    assert extraction.status == ExtractionStatus.FAILED

    package = await _package(client_a, db, public_tender)
    assert package["status"] == "complete"
    assert package["gaps"] == []
    assert [(g["code"], g["key"]) for g in package["processing"]] == [("no_text", broken)]


# --------------------------------------------------------------------------- #
# 4. Un document retirat no compta com a vigent                                 #
# --------------------------------------------------------------------------- #


async def test_un_document_retirat_no_compta_com_a_vigent(db, client_a, op_client, public_tender):
    quadre = await _complete_public_package(db, op_client, public_tender)
    assert (await _package(client_a, db, public_tender))["status"] == "complete"

    await _retire(op_client, public_tender, quadre, "Publicat per error a la plataforma")

    package = await _package(client_a, db, public_tender)
    assert package["status"] == "incomplete"
    assert [(g["code"], g["key"]) for g in package["gaps"]] == [
        ("referenced_document_missing", "DOC-03")
    ]
    run = await current_run(db, public_tender.id)
    assert quadre not in {str(d) for d in await suggestions_of(db, run)}


# --------------------------------------------------------------------------- #
# 5. Substitucio per esmena: nomes compta el vigent                             #
# --------------------------------------------------------------------------- #


async def test_una_esmena_que_substitueix_un_document_nomes_compta_el_vigent(
    db, client_a, op_client, public_tender
):
    original = await _complete_public_package(db, op_client, public_tender)
    definitiu = await _added(
        op_client, public_tender, "DOC-03 Quadre (definitiu).pdf", QUADRE_DEFINITIU_PDF
    )
    await _retire(op_client, public_tender, original, "Substituit per l'esmena oficial")

    package = await _package(client_a, db, public_tender)
    assert package["status"] == "complete"
    assert package["gaps"] == [] and package["processing"] == []

    run = await current_run(db, public_tender.id)
    suggestions = {str(d): s for d, s in (await suggestions_of(db, run)).items()}
    assert definitiu in suggestions and original not in suggestions
    assert suggestions[definitiu].signals["declared_code"] == "DOC-03"
    current = await analysis_input(db, public_tender.id)
    read = {d.tender_document_id for d in current.documents}
    assert definitiu in read and original not in read
    # Cap forat de cobertura obert: el DOC-03 vigent es el definitiu.
    open_gaps = [g for g in await gaps_of(db, public_tender.id) if g.resolved_at is None]
    assert open_gaps == []


# --------------------------------------------------------------------------- #
# 6. La quota no demana acceptar «paquet incomplet» per un avis de processament #
# --------------------------------------------------------------------------- #


async def test_la_quota_no_demana_acceptar_un_paquet_incomplet_si_nomes_hi_ha_avisos(
    db, op_client, public_tender, monkeypatch
):
    pin_real_model(monkeypatch)
    await _complete_public_package(db, op_client, public_tender)
    zipped = await _added(op_client, public_tender, "Annexos.zip", ZIP_BYTES, "application/zip")

    async def preview(**options):
        return await build_preview(
            db,
            public_tender.id,
            profile=V1,
            provider=RealNamedFake(),
            acknowledge_quality=True,
            **options,
        )

    refused = await preview()
    assert refused.blocking_reason == "processing_incomplete"
    assert "package_incomplete" not in refused.prerequisites["failed"]
    assert refused.prerequisites["package"]["status"] == "complete"
    assert refused.prerequisites["package"]["issues"] == []
    issues = refused.prerequisites["processing"]["issues"]
    assert {i.get("tender_document_id") for i in issues} == {zipped}
    assert {i["code"] for i in issues} == {"default_document_skipped", "package_gap"}

    accepted = await preview(acknowledge_processing=True)
    assert accepted.allowed is True, accepted.prerequisites
    assert accepted.prerequisites["package"]["accepted"] is False
    assert accepted.prerequisites["processing"]["acknowledged"] is True


async def test_la_quota_continua_demanant_acceptar_si_falta_un_document(
    db, public_tender, monkeypatch
):
    pin_real_model(monkeypatch)
    await public_package(db, public_tender)
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
    )

    preview = await build_preview(
        db, public_tender.id, profile=V1, provider=RealNamedFake(), acknowledge_quality=True
    )
    assert preview.blocking_reason == "package_incomplete"
    assert preview.prerequisites["package"]["status"] == "incomplete"
    # Acceptar el processament no tapa un document que falta.
    still = await build_preview(
        db,
        public_tender.id,
        profile=V1,
        provider=RealNamedFake(),
        acknowledge_quality=True,
        acknowledge_processing=True,
    )
    assert still.blocking_reason == "package_incomplete"


# --------------------------------------------------------------------------- #
# Empremta d'entrada dels requisits                                             #
# --------------------------------------------------------------------------- #


async def test_llegir_un_docx_que_abans_no_es_llegia_canvia_l_empremta_d_entrada(
    db, public_tender, monkeypatch
):
    """Un DOCX desat abans dels parsers d'oficina (`skipped_unsupported`, `pypdf-v1`)
    es torna a llegir al seguent processat normal, sense forcar res, i aleshores entra
    a l'entrada de l'analisi: l'empremta canonica canvia. El primer run real l'inclou."""
    await public_package(db, public_tender)
    annex = await add_feed_document(
        db, public_tender, "Annex tecnic.docx", TenderDocumentType.ADDITIONAL
    )
    payload = _docx(_p("Annex tecnic") + _p("La xarxa ha de tenir 25 seus connectades."))

    # Abans: la configuracio antiga no en llegia el text.
    monkeypatch.setattr(
        settings, "DOCUMENT_TEXT_EXTRACTION_MIME_TYPES", "application/pdf,text/plain"
    )
    async with serving(payload, DOCX) as client:
        await process_tender_document(db, annex, client=client, resolver=resolver)
    before = await analysis_input(db, public_tender.id)
    assert str(annex.id) in {s.tender_document_id for s in before.skipped}

    # Ara: el mateix contingut, cap descarrega nova, el processat idempotent el llegeix.
    monkeypatch.undo()
    pin = settings.DOCUMENT_TEXT_EXTRACTION_MIME_TYPES
    assert DOCX in pin
    outcome = await process_tender_document(db, annex)
    assert outcome.reused is False
    after = await analysis_input(db, public_tender.id)
    (read,) = [d for d in after.documents if d.tender_document_id == str(annex.id)]
    assert read.parser_version == "docx-v1"
    assert "25 seus" in read.pages[0].text
    assert after.fingerprint != before.fingerprint
    # I la classificacio global ja no hi veu cap avis de processament.
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
    )
    codes = {
        (g.code, (g.detail or {}).get("key"))
        for g in await gaps_of(db, public_tender.id)
        if g.resolved_at is None
    }
    assert (PackageGapCode.NO_TEXT, str(annex.id)) not in codes
    assert (PackageGapCode.FORMAT_NOT_ANALYZABLE, str(annex.id)) not in codes
