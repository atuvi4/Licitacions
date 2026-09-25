"""Extraccio de requisits: el que separa una eina util d'una al·lucinacio.

La regla que governa tot el fitxer: **cap requisit sense cita verificable**. El
proveidor es fals i determinista, aixi que aqui es poden provocar exactament els
casos que un model real produeix quan vol i que costen mes de detectar.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
import sqlalchemy as sa

from app.ai.providers import fake
from app.core.errors import AnalysisInProgressError, NothingAnalyzableError
from app.models.enums import (
    AnalysisRunStatus,
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    ReviewStatus,
)
from app.models.requirements import (
    AwardCriterion,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.services.requirement_analysis import analyze_tender_requirements

PAGE_27 = (
    "8.1.2. Solvencia economica y financiera.\n"
    "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros\n"
    "referido al mejor ejercicio de los tres ultimos disponibles.\n"
)
PAGE_28 = (
    "9. Criterios de adjudicacion.\n"
    "Oferta economica: hasta 45 puntos, mediante formula automatica.\n"
    "Memoria tecnica: hasta 35 puntos, sujeta a juicio de valor.\n"
)

QUOTE_27 = "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros"
QUOTE_28 = "Oferta economica: hasta 45 puntos"


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
async def analyzable(session, tender_document):
    """Un TenderDocument amb contingut, extraccio vigent i dues pagines de text."""
    from app.models.enums import TenderDocumentType

    # La seleccio automatica nomes agafa PCAP, PPT i annexos: un document
    # generic quedaria fora, i amb rao.
    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS

    content = TenderDocumentContent(
        sha256="c" * 64,
        storage_key="tenders/documents/cc/cc/" + "c" * 64 + ".pdf",
        file_size=2048,
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()

    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="pypdf-v1",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=2,
        extracted_page_count=2,
        empty_page_count=0,
        text_char_count=len(PAGE_27) + len(PAGE_28),
        ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()

    pages = []
    for number, text in ((27, PAGE_27), (28, PAGE_28)):
        page = TenderDocumentPage(
            extraction_id=extraction.id,
            page_number=number,
            text=text,
            char_count=len(text),
            text_hash="h" * 64,
            status=PageExtractionStatus.EXTRACTED,
        )
        session.add(page)
        pages.append(page)

    session.add(
        TenderDocumentFile(
            tender_document_id=tender_document.id,
            content_id=content.id,
            source_url=tender_document.url,
            version_number=1,
            is_current=True,
            download_status="downloaded",
        )
    )
    await session.commit()
    for page in pages:
        await session.refresh(page)
    await session.refresh(extraction)

    return {"document": tender_document, "extraction": extraction, "pages": pages}


async def tender_of(session, tender_document):
    from app.models.tenders import Tender

    return await session.get(Tender, tender_document.tender_id)


def requirement_payload(extraction_id, *, page=27, quote=QUOTE_27, **overrides):
    payload = {
        "category": "economic_solvency",
        "obligation": "mandatory",
        "operator": "gte",
        "scope": "tender",
        "lot_labels": [],
        "title": "Volum anual de negoci minim de 450.000 EUR",
        "description": "El licitador ha d'acreditar 450.000 EUR de facturacio anual.",
        "value": {"amount": 450000, "currency": "EUR", "period": "annual", "years": 1},
        "confidence": "high",
        "evidence": [
            {
                "extraction_id": str(extraction_id),
                "page_number": page,
                "quote": quote,
                "evidence_role": "primary",
            }
        ],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# Cami felic i provenance                                                       #
# --------------------------------------------------------------------------- #


async def test_extracts_a_requirement_with_validated_evidence(session, analyzable):
    extraction = analyzable["extraction"]
    fake.queue_response({"requirements": [requirement_payload(extraction.id)]})

    tender = await tender_of(session, analyzable["document"])
    outcome = await analyze_tender_requirements(session, tender)

    assert outcome.reused is False
    run = outcome.run
    assert run.status == AnalysisRunStatus.COMPLETED
    assert run.is_current is True
    assert run.requirements_extracted == 1
    assert run.provider == "fake"
    assert run.extractor_version == "tender-requirements-v1"
    assert run.prompt_version == "requirements-v1"
    assert run.schema_version == "requirements-schema-v1"

    requirement = await session.scalar(sa.select(Requirement))
    assert requirement.category == RequirementCategory.ECONOMIC_SOLVENCY
    assert requirement.obligation == RequirementObligation.MANDATORY
    assert requirement.operator == RequirementOperator.GTE
    assert requirement.value_json["amount"] == 450000
    assert requirement.review_status == ReviewStatus.UNREVIEWED

    evidence = (await session.scalars(sa.select(RequirementEvidence))).all()
    assert len(evidence) == 1
    cite = evidence[0]
    assert cite.page_number == 27
    assert cite.extraction_id == extraction.id
    assert cite.page_id == analyzable["pages"][0].id
    # Els offsets els calcula el backend sobre el text immutable.
    assert PAGE_27[cite.char_start : cite.char_end] == QUOTE_27


async def test_every_requirement_carries_full_provenance(session, analyzable):
    """§58: cap requisit visible pot existir sense la seva cadena de provenance."""
    extraction = analyzable["extraction"]
    fake.queue_response({"requirements": [requirement_payload(extraction.id)]})

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    requirement = await session.scalar(sa.select(Requirement))
    cite = (await session.scalars(sa.select(RequirementEvidence))).all()[0]

    assert requirement.run_id == run.id
    assert cite.extraction_id is not None
    assert cite.page_id is not None
    assert cite.page_number >= 1
    assert cite.quote
    assert run.extractor_version and run.prompt_version and run.model and run.provider


# --------------------------------------------------------------------------- #
# Validacio de l'evidencia                                                      #
# --------------------------------------------------------------------------- #


async def test_a_requirement_without_evidence_is_not_persisted(session, analyzable):
    fake.queue_response(
        {"requirements": [requirement_payload(analyzable["extraction"].id, evidence=[])]}
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 0
    assert run.requirements_extracted == 0
    assert run.requirements_rejected == 1


async def test_evidence_pointing_at_a_page_outside_the_context_is_rejected(session, analyzable):
    fake.queue_response(
        {"requirements": [requirement_payload(analyzable["extraction"].id, page=999)]}
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 0
    assert run.evidence_rejected == 1
    assert run.requirements_rejected == 1


async def test_a_quote_that_is_not_in_the_page_is_rejected(session, analyzable):
    """El cas que justifica tota aquesta capa: el model s'inventa la cita."""
    fake.queue_response(
        {
            "requirements": [
                requirement_payload(
                    analyzable["extraction"].id,
                    quote="El volumen anual de negocios debera ser de 900.000 euros",
                )
            ]
        }
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 0
    assert run.evidence_rejected == 1


async def test_evidence_pointing_at_another_extraction_is_rejected(session, analyzable):
    fake.queue_response({"requirements": [requirement_payload(uuid.uuid4())]})

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 0
    assert run.evidence_rejected == 1


async def test_a_quote_split_across_lines_is_accepted(session, analyzable):
    """Tolerancia tipografica si; semantica no."""
    fake.queue_response(
        {
            "requirements": [
                requirement_payload(
                    analyzable["extraction"].id,
                    quote="debera ser al menos de 450.000 euros referido al mejor ejercicio",
                )
            ]
        }
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert run.requirements_extracted == 1


# --------------------------------------------------------------------------- #
# Contracte de sortida                                                          #
# --------------------------------------------------------------------------- #


async def test_an_invented_category_is_rejected_by_the_schema(session, analyzable):
    fake.queue_response(
        {
            "requirements": [
                requirement_payload(analyzable["extraction"].id, category="categoria_inventada")
            ]
        }
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert run.status == AnalysisRunStatus.FAILED
    assert run.validation_errors == 1
    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 0


async def test_invalid_json_from_the_provider_fails_the_run(session, analyzable):
    fake.queue_invalid_json()

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert run.status == AnalysisRunStatus.FAILED
    assert run.is_current is False


async def test_a_provider_timeout_fails_the_run(session, analyzable):
    fake.queue_timeout()

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert run.status == AnalysisRunStatus.FAILED
    assert run.error_code == "all_chunks_failed"
    assert "timeout" in (run.error_message or "")


async def test_a_provider_error_fails_the_run(session, analyzable):
    fake.queue_error()

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert run.status == AnalysisRunStatus.FAILED
    assert run.chunks_failed == 1


async def test_els_tokens_d_una_resposta_inservible_tambe_compten(session, analyzable):
    """Una resposta sense la tool es paga igual: no pot desapareixer del cost."""
    from app.ai.provider import InvalidStructuredOutputError, Usage

    fake.queue_response(
        InvalidStructuredOutputError(
            "Sense tool.",
            usage=Usage(
                input_tokens=900, output_tokens=8000, request_id="req-1", stop_reason="max_tokens"
            ),
        )
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert run.status == AnalysisRunStatus.FAILED
    assert run.input_tokens == 900
    assert run.output_tokens == 8000
    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["status"] == "failed"
    assert chunk["error"] == "invalid_structured_output"
    assert (chunk["input_tokens"], chunk["output_tokens"]) == (900, 8000)
    assert chunk["stop_reason"] == "max_tokens"


async def test_una_sortida_que_no_compleix_el_contracte_es_schema_invalid(session, analyzable):
    """La diagnosi diu on i de quin tipus es l'error, sense copiar cap text."""
    from app.ai.provider import StructuredResult, Usage

    extraction = analyzable["extraction"]
    secret = "TEXT LITERAL DEL PLEC QUE NO S'HA DE DESAR"
    bad = requirement_payload(extraction.id, category="categoria_inventada", title=secret)
    bad["camp_inventat"] = secret
    fake.queue_response(
        StructuredResult(
            data={"requirements": [bad]},
            usage=Usage(input_tokens=700, output_tokens=300, stop_reason="tool_use"),
        )
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    [chunk] = run.chunk_coverage["chunks"]
    assert run.status == AnalysisRunStatus.FAILED
    assert chunk["error"] == "schema_invalid"
    assert (chunk["input_tokens"], chunk["output_tokens"]) == (700, 300)
    detail = chunk["error_detail"]
    assert {"path": "requirements.0.category", "type": "enum"} in detail
    assert {"path": "requirements.0.<key>", "type": "extra_forbidden"} in detail
    assert all(set(item) == {"path", "type"} for item in detail)
    assert secret not in str(run.chunk_coverage)
    assert secret not in (run.error_message or "")


async def test_una_clau_dinamica_amb_text_del_plec_no_arriba_a_la_diagnosi(session, analyzable):
    """El path nomes conserva camps de l'esquema i indexs; la resta es `<key>`."""
    extraction = analyzable["extraction"]
    literal = (
        "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros"
    )
    bad = requirement_payload(extraction.id)
    bad[literal] = "x"
    bad["evidence"][0][f"nota {literal}"] = "y"
    fake.queue_response({"requirements": [bad], literal: []})

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["error"] == "schema_invalid"
    paths = {item["path"] for item in chunk["error_detail"]}
    assert paths == {"<key>", "requirements.0.<key>", "requirements.0.evidence.0.<key>"}
    for fragment in (literal, "volumen", "450.000"):
        assert fragment not in json.dumps(run.chunk_coverage, ensure_ascii=False)
        assert fragment not in (run.error_message or "")


def test_el_path_de_la_diagnosi_nomes_admet_camps_coneguts_i_indexs():
    from pydantic import BaseModel, ConfigDict, ValidationError

    from app.services.requirement_analysis import known_schema_fields, schema_diagnostics

    class Fill(BaseModel):
        model_config = ConfigDict(extra="forbid")
        quote: str

    class Pare(BaseModel):
        model_config = ConfigDict(extra="forbid")
        items: list[Fill]

    with pytest.raises(ValidationError) as caught:
        Pare.model_validate({"items": [{"quote": 3, "text secret del plec": 1}]})

    known = known_schema_fields(Pare.model_json_schema())
    detail = schema_diagnostics(caught.value, known)

    assert known == {"items", "quote"}
    assert {"path": "items.0.quote", "type": "string_type"} in detail
    assert {"path": "items.0.<key>", "type": "extra_forbidden"} in detail
    assert all(set(item) == {"path", "type"} for item in detail)


async def test_la_diagnosi_de_schema_invalid_es_limitada(session, analyzable):
    from app.services.requirement_analysis import MAX_SCHEMA_DIAGNOSTICS

    extraction = analyzable["extraction"]
    bad = [requirement_payload(extraction.id, category="no-existeix") for _ in range(12)]
    fake.queue_response({"requirements": bad})

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    [chunk] = run.chunk_coverage["chunks"]
    assert len(chunk["error_detail"]) == MAX_SCHEMA_DIAGNOSTICS


async def test_una_resposta_tallada_falla_encara_que_el_payload_validi(session, analyzable):
    """`max_tokens` vol dir incomplet: ni es persisteix ni compta com a analitzat."""
    from app.ai.provider import StructuredResult, Usage

    extraction = analyzable["extraction"]
    fake.queue_response(
        StructuredResult(
            data={"requirements": [requirement_payload(extraction.id)]},
            usage=Usage(input_tokens=900, output_tokens=8000, stop_reason="max_tokens"),
        )
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["status"] == "failed"
    assert chunk["error"] == "output_truncated"
    assert chunk["stop_reason"] == "max_tokens"
    assert run.output_tokens == 8000, "la resposta tallada tambe es paga"
    assert run.chunks_completed == 0
    persisted = (
        await session.scalars(sa.select(Requirement).where(Requirement.run_id == run.id))
    ).all()
    assert persisted == []


async def test_el_proveidor_sense_tool_queda_com_no_tool_call(session, analyzable):
    from app.ai.provider import NoToolCallError, Usage

    fake.queue_response(
        NoToolCallError("Sense tool.", usage=Usage(output_tokens=40, stop_reason="end_turn"))
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["error"] == "no_tool_call"
    assert chunk["error_detail"] is None


async def test_un_error_sense_resposta_no_s_inventa_tokens(session, analyzable):
    fake.queue_error()

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    [chunk] = run.chunk_coverage["chunks"]
    assert run.input_tokens is None
    assert chunk["input_tokens"] is None and chunk["stop_reason"] is None


async def test_la_cobertura_registra_les_pagines_i_el_consum_de_cada_chunk(session, analyzable):
    extraction = analyzable["extraction"]
    fake.queue_response({"requirements": [requirement_payload(extraction.id)]})

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["pages"] == [27, 28]
    assert chunk["status"] == "completed"
    assert (chunk["input_tokens"], chunk["output_tokens"]) == (100, 50)
    assert run.input_tokens == 100


# --------------------------------------------------------------------------- #
# Semantica de l'obligatorietat                                                 #
# --------------------------------------------------------------------------- #


async def test_scored_is_not_turned_into_mandatory(session, analyzable):
    """"Se valorara" dona punts, no exclou. §16."""
    fake.queue_response(
        {
            "requirements": [
                requirement_payload(
                    analyzable["extraction"].id, obligation="scored", operator=None, value=None
                )
            ]
        }
    )

    tender = await tender_of(session, analyzable["document"])
    await analyze_tender_requirements(session, tender)

    requirement = await session.scalar(sa.select(Requirement))
    assert requirement.obligation == RequirementObligation.SCORED


async def test_no_hard_blocker_is_stored(session, analyzable):
    """§17: mandatory no es un no-go. Aquesta columna no ha d'existir encara."""
    assert not hasattr(Requirement, "hard_blocker")
    assert "hard_blocker" not in Requirement.__table__.columns


# --------------------------------------------------------------------------- #
# Deduplicacio i criteris                                                       #
# --------------------------------------------------------------------------- #


async def test_the_same_requirement_seen_twice_is_stored_once(session, analyzable):
    """El solapament de chunks genera duplicats: es dedupliquen deterministament."""
    extraction = analyzable["extraction"]
    payload = requirement_payload(extraction.id)
    fake.queue_response({"requirements": [payload, dict(payload)]})

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 1
    assert run.requirements_extracted == 1


async def test_two_different_amounts_are_both_kept(session, analyzable):
    """§28/§29: davant del dubte, es conserven tots dos. No es fusiona."""
    extraction = analyzable["extraction"]
    first = requirement_payload(extraction.id)
    second = requirement_payload(
        extraction.id,
        title="Volum anual de negoci minim de 600.000 EUR",
        value={"amount": 600000, "currency": "EUR", "period": "annual", "years": 1},
    )
    fake.queue_response({"requirements": [first, second]})

    tender = await tender_of(session, analyzable["document"])
    await analyze_tender_requirements(session, tender)

    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 2


async def test_extracts_award_criteria_with_evidence(session, analyzable):
    extraction = analyzable["extraction"]
    fake.queue_response(
        {
            "requirements": [],
            "award_criteria": [
                {
                    "name": "Oferta economica",
                    "points": 45,
                    "evaluation_type": "automatic",
                    "formula": None,
                    "confidence": "high",
                    "evidence": [
                        {
                            "extraction_id": str(extraction.id),
                            "page_number": 28,
                            "quote": QUOTE_28,
                        }
                    ],
                }
            ],
        }
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    criterion = await session.scalar(sa.select(AwardCriterion))
    assert criterion.name == "Oferta economica"
    assert criterion.points == 45
    assert criterion.formula is None, "no s'inventa cap formula"
    assert run.award_criteria_extracted == 1


async def test_an_award_criterion_without_evidence_is_rejected(session, analyzable):
    fake.queue_response(
        {
            "requirements": [],
            "award_criteria": [
                {"name": "Millores", "points": 15, "evidence": []},
            ],
        }
    )

    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert await session.scalar(sa.select(sa.func.count()).select_from(AwardCriterion)) == 0
    assert run.requirements_rejected == 1


# --------------------------------------------------------------------------- #
# Documents no analitzables                                                     #
# --------------------------------------------------------------------------- #


async def test_a_tender_without_extracted_text_cannot_be_analysed(session, tender_document):
    tender = await tender_of(session, tender_document)

    with pytest.raises(NothingAnalyzableError):
        await analyze_tender_requirements(session, tender)


async def test_an_unsupported_document_is_reported_not_invented(session, tender_document):
    """Un OLE2 no te text: queda fora i es registra, no se n'inventa el contingut."""
    content = TenderDocumentContent(
        sha256="d" * 64,
        storage_key="tenders/documents/dd/dd/" + "d" * 64 + ".ole",
        file_size=1024,
        mime_type="application/x-ole-storage",
        detected_extension="ole",
        is_text_extractable=False,
    )
    session.add(content)
    await session.flush()
    session.add(
        TenderDocumentExtraction(
            content_id=content.id,
            parser_version="pypdf-v1",
            revision_number=1,
            status=ExtractionStatus.SKIPPED_UNSUPPORTED,
            quality=ExtractionQuality.NOT_APPLICABLE,
            is_current=True,
            error_code="unsupported_for_text_extraction",
        )
    )
    session.add(
        TenderDocumentFile(
            tender_document_id=tender_document.id,
            content_id=content.id,
            source_url=tender_document.url,
            version_number=1,
            is_current=True,
            download_status="downloaded",
        )
    )
    await session.commit()

    tender = await tender_of(session, tender_document)
    with pytest.raises(NothingAnalyzableError):
        await analyze_tender_requirements(session, tender)

    # I no s'ha enviat res al proveidor.
    assert fake.SENT_PAYLOADS == []


# --------------------------------------------------------------------------- #
# Cap dada de l'empresa                                                         #
# --------------------------------------------------------------------------- #


async def test_no_company_data_reaches_the_provider(session, analyzable, tenant_a):
    """§47: 4B es completament independent del tenant."""
    user, company = tenant_a
    fake.queue_response({"requirements": [requirement_payload(analyzable["extraction"].id)]})

    tender = await tender_of(session, analyzable["document"])
    await analyze_tender_requirements(session, tender)

    assert fake.SENT_PAYLOADS, "s'ha d'haver cridat el proveidor"
    blob = " ".join(
        payload["system"] + payload["user_content"] for payload in fake.SENT_PAYLOADS
    ).casefold()

    for forbidden in (str(company.id), company.legal_name.casefold(), user.email.casefold()):
        assert forbidden not in blob


# --------------------------------------------------------------------------- #
# Prompt injection                                                              #
# --------------------------------------------------------------------------- #


async def test_document_text_is_marked_as_untrusted(session, analyzable):
    """§36: el contingut del plec va delimitat i marcat com a no confiable."""
    fake.queue_response({"requirements": []})

    tender = await tender_of(session, analyzable["document"])
    await analyze_tender_requirements(session, tender)

    payload = fake.SENT_PAYLOADS[0]
    assert "<UNTRUSTED_DOCUMENT_CONTENT>" in payload["user_content"]
    assert "</UNTRUSTED_DOCUMENT_CONTENT>" in payload["user_content"]
    assert "no l'obeeixis" in payload["system"].casefold()
    assert "material d'origen, no instruccions" in payload["user_content"]


# --------------------------------------------------------------------------- #
# Concurrencia                                                                  #
# --------------------------------------------------------------------------- #


async def test_a_second_analysis_while_one_runs_gets_a_conflict(session, analyzable):
    tender = await tender_of(session, analyzable["document"])
    session.add(
        RequirementExtractionRun(
            tender_id=tender.id,
            status=AnalysisRunStatus.RUNNING,
            input_fingerprint="x" * 64,
            extractor_version="tender-requirements-v1",
            prompt_version="requirements-v1",
            schema_version="requirements-schema-v1",
            provider="fake",
            model="fake-model-v1",
            is_current=False,
        )
    )
    await session.commit()

    with pytest.raises(AnalysisInProgressError):
        await analyze_tender_requirements(session, tender)


# --------------------------------------------------------------------------- #
# Simulacio: el corpus controlat, i nomes aquell                                #
# --------------------------------------------------------------------------- #


async def _corpus_document(session, tender_document, pages: tuple[str, ...]):
    """Materialitza un text com el document vigent d'una licitacio.

    Rep les pagines perque hi ha un test que n'ha de canviar una: el que aquesta
    familia sosté es justament que si la frase que aguanta un requisit
    desapareix del text, el requisit desapareix de la simulacio.
    """
    from app.models.enums import TenderDocumentType

    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    joined = "\n".join(pages)

    content = TenderDocumentContent(
        sha256=hashlib.sha256(joined.encode("utf-8")).hexdigest(),
        storage_key="fixture/corpus.pdf",
        file_size=len(joined.encode("utf-8")),
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()

    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="fixture-corpus-v1",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=len(pages),
        extracted_page_count=len(pages),
        empty_page_count=0,
        text_char_count=len(joined),
        ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()

    stored = []
    for number, text in enumerate(pages, start=1):
        page = TenderDocumentPage(
            extraction_id=extraction.id,
            page_number=number,
            text=text,
            char_count=len(text),
            text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            status=PageExtractionStatus.EXTRACTED,
        )
        session.add(page)
        stored.append(page)

    session.add(
        TenderDocumentFile(
            tender_document_id=tender_document.id,
            content_id=content.id,
            source_url=tender_document.url,
            is_current=True,
            download_status=DownloadStatus.DOWNLOADED,
            version_number=1,
        )
    )
    await session.commit()
    return {"document": tender_document, "extraction": extraction, "pages": stored}


@pytest.fixture
async def corpus(session, tender_document):
    """El plec de desenvolupament, sencer, com a document vigent."""
    from app.ai.providers.fixture_corpus import PAGES

    return await _corpus_document(session, tender_document, PAGES)


class TestProveidorDeSimulacio:
    """La simulacio no pot inventar-se la relacio entre exigencia i cita.

    Una simulacio pot inventar-se el CONTINGUT —per aixo es una simulacio— pero
    si a mes es pot inventar quina frase del plec sosté cada requisit, el que
    s'esta provant ja no es el producte: la validacio d'evidencia passa
    igualment i el resultat no vol dir res.

    La primera versio d'aquest proveidor feia exactament aixo: agafava
    qualsevol frase prou llarga del document i l'enganxava al requisit que
    toques. Ara cada requisit va lligat a una frase concreta del corpus, i si la
    frase no hi es, el requisit no surt.
    """

    async def test_produeix_els_requisits_del_corpus(self, session, corpus):
        from app.ai.providers.fixture import FixtureProvider
        from app.models.enums import RequirementCategory

        tender = await tender_of(session, corpus["document"])

        run = (
            await analyze_tender_requirements(session, tender, provider=FixtureProvider())
        ).run

        assert run.status == AnalysisRunStatus.COMPLETED
        assert run.requirements_extracted == 6
        assert run.award_criteria_extracted == 2

        requirements = (
            await session.scalars(sa.select(Requirement).where(Requirement.run_id == run.id))
        ).all()
        assert {r.category for r in requirements} == {
            RequirementCategory.ECONOMIC_SOLVENCY,
            RequirementCategory.INSURANCE,
            RequirementCategory.CERTIFICATION,
            RequirementCategory.EXPERIENCE,
            RequirementCategory.TECHNICAL_REQUIREMENT,
        }

    async def test_cada_cita_sosté_el_seu_requisit(self, session, corpus):
        """El cor d'aquesta iteracio: la cita ha de dir el que el requisit afirma.

        No es comprova amb cap model ni amb cap semblança difusa: cada requisit
        del corpus declara la seva frase, i aqui es comprova que l'evidencia
        desada es aquella i no una altra qualsevol del document.
        """
        from app.ai.providers.fixture import FixtureProvider, _normalise
        from app.ai.providers.fixture_corpus import REQUIREMENTS

        tender = await tender_of(session, corpus["document"])
        run = (
            await analyze_tender_requirements(session, tender, provider=FixtureProvider())
        ).run

        rows = (
            await session.execute(
                sa.select(Requirement.title, RequirementEvidence.quote)
                .join(RequirementEvidence, RequirementEvidence.requirement_id == Requirement.id)
                .where(Requirement.run_id == run.id)
            )
        ).all()
        quotes = {title: _normalise(quote) for title, quote in rows}

        assert len(quotes) == 6
        for anchor in REQUIREMENTS:
            assert quotes[anchor.payload["title"]] == _normalise(anchor.quote), anchor.key

    async def test_una_frase_que_desapareix_se_emporta_el_seu_requisit(
        self, session, tender_document
    ):
        """Si el text deixa de dir-ho, la simulacio deixa de sostenir-ho.

        Es la prova que la relacio entre requisit i cita no es decorativa: no hi
        ha cap manera de mantenir el requisit si la frase que l'aguanta ja no es
        al document.
        """
        from app.ai.providers.fixture import FixtureProvider
        from app.ai.providers.fixture_corpus import PAGES

        eliminada = (
            "El volumen anual de negocios del licitador referido al mejor "
            "ejercicio de los tres ultimos disponibles debera ser igual o "
            "superior a 250.000 euros."
        )
        mutilat = tuple(page.replace(eliminada, "[frase eliminada]") for page in PAGES)
        assert "250.000 euros" not in "\n".join(mutilat)

        corpus = await _corpus_document(session, tender_document, mutilat)
        tender = await tender_of(session, corpus["document"])

        run = (
            await analyze_tender_requirements(session, tender, provider=FixtureProvider())
        ).run

        titles = set(
            (
                await session.scalars(
                    sa.select(Requirement.title).where(Requirement.run_id == run.id)
                )
            ).all()
        )
        assert "Volum anual de negoci igual o superior a 250.000 EUR" not in titles
        assert run.requirements_extracted == 5
        # La resta segueix sencera: nomes cau el que depenia de la frase.
        assert "Certificat ISO 14001 en vigor" in titles

    async def test_es_nega_a_respondre_sobre_un_document_qualsevol(self, session, analyzable):
        """Un plec real no es el corpus: no s'hi improvisa cap analisi.

        `analyzable` te text perfectament citable —frases llargues i concretes—
        i precisament per aixo serveix: la versio anterior del proveidor
        n'hauria tret sis requisits tan convincents com falsos.
        """
        from app.ai.providers.fixture import CORPUS_MISSING, FixtureProvider

        tender = await tender_of(session, analyzable["document"])

        run = (
            await analyze_tender_requirements(session, tender, provider=FixtureProvider())
        ).run

        assert run.status == AnalysisRunStatus.FAILED
        assert run.requirements_extracted == 0
        assert CORPUS_MISSING in f"{run.error_code or ''} {run.error_message or ''}"

    async def test_lasseguranca_porta_el_total_i_el_sublimit_per_victima(self, session, corpus):
        """Son dos limits diferents i el corpus els diu en dues frases."""
        from app.ai.providers.fixture import FixtureProvider
        from app.models.enums import RequirementCategory

        tender = await tender_of(session, corpus["document"])
        run = (
            await analyze_tender_requirements(session, tender, provider=FixtureProvider())
        ).run

        insurance = (
            await session.scalars(
                sa.select(Requirement).where(
                    Requirement.run_id == run.id,
                    Requirement.category == RequirementCategory.INSURANCE,
                )
            )
        ).all()

        assert len(insurance) == 2
        assert sorted((r.value_json or {}).get("coverage_amount") for r in insurance) == [
            150000,
            600000,
        ]

    async def test_els_dos_limits_arriben_a_la_checklist_amb_noms_diferents(
        self, session, corpus
    ):
        """La cita diu "limite total" i "sublimite por victima": el titol tambe.

        Aixo no es cosmetica. Les dues files de la checklist deien "Aportar
        polissa RC >= ..." i nomes es distingien per la xifra, que es exactament
        com algu acaba buscant dues polisses per una sola exigencia. El tipus de
        limit surt del valor estructurat, no d'endevinar-lo del titol.
        """
        from app.ai.providers.fixture import FixtureProvider
        from app.models.enums import RequirementCategory
        from app.workspace.checklist import checklist_title

        tender = await tender_of(session, corpus["document"])
        run = (
            await analyze_tender_requirements(session, tender, provider=FixtureProvider())
        ).run

        insurance = (
            await session.scalars(
                sa.select(Requirement).where(
                    Requirement.run_id == run.id,
                    Requirement.category == RequirementCategory.INSURANCE,
                )
            )
        ).all()
        titles = {checklist_title(r) for r in insurance}

        assert len(titles) == 2
        assert any("limit total" in t for t in titles)
        assert any("sublimit per victima" in t for t in titles)

    async def test_el_run_diu_que_es_una_simulacio(self, session, corpus):
        from app.ai.providers.fixture import FixtureProvider
        from app.models.enums import AnalysisBasis

        tender = await tender_of(session, corpus["document"])
        run = (
            await analyze_tender_requirements(session, tender, provider=FixtureProvider())
        ).run

        assert run.provider == "fixture"
        assert AnalysisBasis.for_provider(run.provider) == AnalysisBasis.SIMULATION
        assert AnalysisBasis.for_provider(run.provider).is_trustworthy is False

    async def test_una_simulacio_si_que_dona_veredicte_de_compliment(self, session, corpus):
        """El punt de tot plegat: amb requisits, el compliment deixa d'estar cec."""
        from app.ai.providers.fixture import FixtureProvider
        from app.models.enums import EligibilityResult
        from app.models.identity import Company
        from app.services.compliance_analysis import analyze_company_compliance

        company = Company(legal_name="Empresa de prova")
        session.add(company)
        await session.flush()

        tender = await tender_of(session, corpus["document"])
        await analyze_tender_requirements(session, tender, provider=FixtureProvider())

        outcome = await analyze_company_compliance(session, company, tender)

        assert outcome.run.requirements_total == 6
        assert outcome.run.overall_result != EligibilityResult.UNDETERMINED



# --------------------------------------------------------------------------- #
# Enduriment despres del run real e0fc136c (Palamos 2024-3137)                   #
# --------------------------------------------------------------------------- #


class TestNormalitzacioEstricta:
    """Les dues normalitzacions arriben al run; la validacio d'evidencia no canvia."""

    async def test_llistes_com_a_text_json_es_persisteixen_i_queden_registrades(
        self, session, analyzable
    ):
        extraction_id = analyzable["extraction"].id
        fake.queue_response(
            {
                "requirements": json.dumps([requirement_payload(extraction_id)]),
                "award_criteria": "[]",
                "document_notes": "[]",
                "uncertainties": "[]",
            }
        )

        tender = await tender_of(session, analyzable["document"])
        run = (await analyze_tender_requirements(session, tender)).run

        assert run.status == AnalysisRunStatus.COMPLETED
        assert run.requirements_extracted == 1
        [chunk] = run.chunk_coverage["chunks"]
        assert chunk["normalised"] == [
            "requirements:json_string_list",
            "award_criteria:json_string_list",
            "document_notes:json_string_list",
            "uncertainties:json_string_list",
        ]

    async def test_la_cita_d_una_llista_desfeta_es_continua_verificant(self, session, analyzable):
        extraction_id = analyzable["extraction"].id
        invented = requirement_payload(extraction_id, quote="Aquesta frase no surt al plec")
        fake.queue_response({"requirements": json.dumps([invented])})

        tender = await tender_of(session, analyzable["document"])
        run = (await analyze_tender_requirements(session, tender)).run

        assert run.requirements_extracted == 0
        assert run.requirements_rejected == 1
        assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 0

    async def test_l_extraction_id_del_context_a_l_arrel_es_treu(self, session, analyzable):
        extraction_id = analyzable["extraction"].id
        fake.queue_response(
            {
                "extraction_id": str(extraction_id),
                "requirements": [requirement_payload(extraction_id)],
            }
        )

        tender = await tender_of(session, analyzable["document"])
        run = (await analyze_tender_requirements(session, tender)).run

        assert run.status == AnalysisRunStatus.COMPLETED
        assert run.requirements_extracted == 1
        [chunk] = run.chunk_coverage["chunks"]
        assert chunk["normalised"] == ["extraction_id:echoed_root_extraction_id"]

    async def test_un_extraction_id_a_l_arrel_que_no_es_del_context_falla(
        self, session, analyzable
    ):
        extraction_id = analyzable["extraction"].id
        fake.queue_response(
            {
                "extraction_id": str(uuid.uuid4()),
                "requirements": [requirement_payload(extraction_id)],
            }
        )

        tender = await tender_of(session, analyzable["document"])
        run = (await analyze_tender_requirements(session, tender)).run

        [chunk] = run.chunk_coverage["chunks"]
        assert run.status == AnalysisRunStatus.FAILED
        assert chunk["error"] == "schema_invalid"
        assert chunk["error_detail"] == [{"path": "extraction_id", "type": "extra_forbidden"}]
        assert chunk["normalised"] is None


class TestErrorsDelProveidor:
    """Diagnosi sanititzada i aturada davant d'un error que es repetiria a tots."""

    @pytest.fixture
    def two_chunks(self, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
        monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)

    @staticmethod
    def _http_error(status: int, *, fatal: bool):
        from app.ai.provider import ProviderError

        return ProviderError(
            f"HTTP {status} del proveidor (invalid_request_error).",
            "provider_error",
            details={
                "status": status,
                "type": "invalid_request_error",
                "message": "Your credit balance is too low to access the Anthropic API.",
                "request_id": "req_011Test",
            },
            fatal=fatal,
        )

    async def test_un_error_fatal_atura_el_run_sense_enviar_la_resta(
        self, session, analyzable, two_chunks
    ):
        fake.queue_response(self._http_error(400, fatal=True))

        tender = await tender_of(session, analyzable["document"])
        run = (await analyze_tender_requirements(session, tender)).run

        assert len(fake.SENT_PAYLOADS) == 1, "el segon chunk no s'ha d'enviar"
        assert run.status == AnalysisRunStatus.FAILED
        assert (run.chunks_total, run.chunks_completed, run.chunks_failed) == (2, 0, 2)
        first, second = run.chunk_coverage["chunks"]
        assert first["error"] == "provider_error"
        assert first["provider_error"] == {
            "status": 400,
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API.",
            "request_id": "req_011Test",
        }
        assert second["error"] == "not_sent"
        assert second["input_tokens"] is None
        assert "not_sent: 1" in run.error_message
        assert "provider_error: 1" in run.error_message

    async def test_un_error_no_fatal_no_atura_el_run(self, session, analyzable, two_chunks):
        extraction_id = analyzable["extraction"].id
        fake.queue_many(
            [
                self._http_error(500, fatal=False),
                {"requirements": [requirement_payload(extraction_id)]},
            ]
        )

        tender = await tender_of(session, analyzable["document"])
        run = (await analyze_tender_requirements(session, tender)).run

        assert len(fake.SENT_PAYLOADS) == 2
        assert run.status == AnalysisRunStatus.PARTIAL
        first, second = run.chunk_coverage["chunks"]
        assert first["provider_error"]["status"] == 500
        assert second["status"] == "completed"


def test_el_resum_d_un_run_parcial_compta_cada_motiu():
    """El run real deia nomes `schema_invalid` i amagava 9 chunks tallats."""
    from app.services.requirement_analysis import failure_summary

    failures = {i: "output_truncated" for i in (2, 11, 12, 13, 14, 15, 16, 18, 19)}
    failures.update({i: "schema_invalid" for i in (1, 3, 8, 20)})

    summary = failure_summary(failures, 22, "schema_invalid: La sortida no compleix (4 errors).")

    assert summary == (
        "13 de 22 chunks fallits (output_truncated: 9, schema_invalid: 4). "
        "Darrer error: schema_invalid: La sortida no compleix (4 errors)."
    )


def test_sense_fallades_el_resum_es_l_ultim_error():
    from app.services.requirement_analysis import failure_summary

    assert failure_summary({}, 3, None) is None
