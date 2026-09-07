"""Regressio sobre el dataset etiquetat a ma.

Fa passar els fragments de `tests/fixtures/plec_fragments.py` per la canonada
sencera: chunking, esquema, validacio de cites, deduplicacio i persistencia.

Amb el proveidor fals aixo NO mesura la qualitat del model —aixo requereix una
clau real— pero si fixa que la canonada accepta i desa correctament el que un
extractor correcte hauria de produir, i que rebutja el que no toca.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.ai.providers import fake
from app.ai.schemas import ChunkExtractionOut, RequirementOut
from app.models.enums import (
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
    RequirementObligation,
    TenderDocumentType,
)
from app.models.requirements import AwardCriterion, Requirement, RequirementEvidence
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender
from app.services.requirement_analysis import analyze_tender_requirements
from tests.fixtures.plec_fragments import (
    ALL_FRAGMENTS,
    CERTIFICACIO,
    CRITERIS,
    PROMPT_INJECTION,
    SOLVENCIA_ECONOMICA,
    LabeledFragment,
)


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
async def dataset(session, tender_document):
    """Un PCAP amb una pagina per fragment etiquetat."""
    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS

    content = TenderDocumentContent(
        sha256="e" * 64,
        storage_key="tenders/documents/ee/ee/" + "e" * 64 + ".pdf",
        file_size=4096,
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
        page_count=len(ALL_FRAGMENTS),
        extracted_page_count=len(ALL_FRAGMENTS),
        text_char_count=sum(len(f.text) for f in ALL_FRAGMENTS),
        is_current=True,
    )
    session.add(extraction)
    await session.flush()

    for fragment in ALL_FRAGMENTS:
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=fragment.page_number,
                text=fragment.text,
                char_count=len(fragment.text),
                text_hash="h" * 64,
                status=PageExtractionStatus.EXTRACTED,
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
    await session.refresh(extraction)
    return {"document": tender_document, "extraction": extraction}


def requirements_from(fragment: LabeledFragment, extraction_id) -> list[dict]:
    """Converteix les etiquetes manuals en la sortida que donaria un bon extractor."""
    out = []
    for expected in fragment.expected_requirements:
        out.append(
            {
                "category": expected["category"],
                "obligation": expected.get("obligation", "unknown"),
                "operator": expected.get("operator"),
                "title": expected["quote"][:120],
                "value": expected.get("value"),
                "confidence": "high",
                "evidence": [
                    {
                        "extraction_id": str(extraction_id),
                        "page_number": fragment.page_number,
                        "quote": expected["quote"],
                    }
                ],
            }
        )
    return out


def criteria_from(fragment: LabeledFragment, extraction_id) -> list[dict]:
    out = []
    for expected in fragment.expected_award_criteria:
        out.append(
            {
                "name": expected["name"],
                "points": expected.get("points"),
                "formula": expected.get("formula"),
                "evaluation_type": expected.get("evaluation_type", "unknown"),
                "confidence": "high",
                "evidence": [
                    {
                        "extraction_id": str(extraction_id),
                        "page_number": fragment.page_number,
                        "quote": expected["quote"],
                    }
                ],
            }
        )
    return out


async def run_dataset(session, dataset, fragments):
    """Encua una resposta per chunk i executa l'analisi."""
    extraction_id = dataset["extraction"].id
    by_page = {f.page_number: f for f in fragments}

    # Un chunk pot cobrir diverses pagines: es responen totes les que hi caiguin.
    from app.services.requirement_analysis import collect_analyzable_documents
    from app.services.requirement_chunking import build_chunks

    documents, _ = await collect_analyzable_documents(session, dataset["document"].tender_id)
    for chunk in build_chunks(documents):
        requirements: list[dict] = []
        criteria: list[dict] = []
        for page in chunk.pages:
            fragment = by_page.get(page.page_number)
            if fragment is None:
                continue
            requirements += requirements_from(fragment, extraction_id)
            criteria += criteria_from(fragment, extraction_id)
        fake.queue_response({"requirements": requirements, "award_criteria": criteria})

    tender = await session.get(Tender, dataset["document"].tender_id)
    return await analyze_tender_requirements(session, tender)


# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fragment", ALL_FRAGMENTS, ids=lambda f: f.key)
def test_every_labeled_quote_is_literally_in_its_fragment(fragment: LabeledFragment):
    """El dataset ha de ser correcte abans de servir de referencia."""
    from app.services.evidence_validation import quote_is_present

    for expected in fragment.expected_requirements + fragment.expected_award_criteria:
        assert quote_is_present(fragment.text, expected["quote"]), (
            f"{fragment.key}: el quote etiquetat no es al text"
        )


@pytest.mark.parametrize("fragment", ALL_FRAGMENTS, ids=lambda f: f.key)
def test_every_labeled_requirement_fits_the_output_schema(fragment: LabeledFragment):
    """Les categories i operadors etiquetats han de ser dins dels enums tancats."""
    for expected in fragment.expected_requirements:
        RequirementOut.model_validate(
            {
                "category": expected["category"],
                "obligation": expected.get("obligation", "unknown"),
                "operator": expected.get("operator"),
                "title": expected["quote"][:120],
                "value": expected.get("value"),
                "evidence": [
                    {
                        "extraction_id": "x",
                        "page_number": fragment.page_number,
                        "quote": expected["quote"],
                    }
                ],
            }
        )


async def test_the_whole_dataset_survives_the_pipeline(session, dataset):
    outcome = await run_dataset(session, dataset, ALL_FRAGMENTS)

    expected_requirements = sum(len(f.expected_requirements) for f in ALL_FRAGMENTS)
    expected_criteria = sum(len(f.expected_award_criteria) for f in ALL_FRAGMENTS)

    stored = await session.scalar(sa.select(sa.func.count()).select_from(Requirement))
    criteria = await session.scalar(sa.select(sa.func.count()).select_from(AwardCriterion))

    assert stored == expected_requirements, "s'han perdut requisits etiquetats"
    assert criteria == expected_criteria
    assert outcome.run.evidence_rejected == 0, "cap cita etiquetada s'hauria de rebutjar"

    # Tot requisit desat te com a minim una evidencia localitzada.
    orphans = await session.scalar(
        sa.text(
            "SELECT count(*) FROM requirements r WHERE NOT EXISTS "
            "(SELECT 1 FROM requirement_evidence e WHERE e.requirement_id = r.id)"
        )
    )
    assert orphans == 0


async def test_scored_certification_is_not_promoted_to_mandatory(session, dataset):
    """El cas que mes es falla: "se valorara" no exclou ningu."""
    await run_dataset(session, dataset, [CERTIFICACIO])

    obligations = {
        r.title: r.obligation
        for r in (await session.scalars(sa.select(Requirement))).all()
    }
    scored = [o for o in obligations.values() if o == RequirementObligation.SCORED]
    mandatory = [o for o in obligations.values() if o == RequirementObligation.MANDATORY]

    assert len(mandatory) == 1, "nomes la ISO 27001 es obligatoria"
    assert len(scored) == 1, "la ISO 14001 es valorable, no obligatoria"


async def test_award_criteria_keep_points_and_do_not_invent_formulas(session, dataset):
    await run_dataset(session, dataset, [CRITERIS])

    criteria = {
        c.name: c for c in (await session.scalars(sa.select(AwardCriterion))).all()
    }
    assert criteria["Oferta economica"].points == 45
    assert criteria["Memoria tecnica"].points == 35
    assert criteria["Mejoras sin coste"].points == 20
    assert all(c.formula is None for c in criteria.values()), "el plec no dona cap formula"


async def test_the_economic_threshold_survives_exactly(session, dataset):
    """Els numeros no es poden arrodonir ni reinterpretar."""
    await run_dataset(session, dataset, [SOLVENCIA_ECONOMICA])

    requirement = await session.scalar(sa.select(Requirement))
    assert requirement.value_json["amount"] == 450000
    assert requirement.value_json["currency"] == "EUR"

    cite = await session.scalar(sa.select(RequirementEvidence))
    assert cite.page_number == 27
    assert "450.000 euros" in cite.quote


async def test_an_injected_instruction_cannot_produce_a_compliance_claim(session, dataset):
    """§36: el text del plec es dada. I l'esquema ni tan sols permet dir "compleix"."""
    await run_dataset(session, dataset, [PROMPT_INJECTION])

    # Nomes s'ha extret el requisit real de la pagina, no cap ordre incrustada.
    requirements = (await session.scalars(sa.select(Requirement))).all()
    assert len(requirements) == 1
    assert "garantia" in requirements[0].title.casefold()

    # I el contracte de sortida no te cap camp de compliment on posar-ho.
    fields = set(ChunkExtractionOut.model_fields) | set(RequirementOut.model_fields)
    for forbidden in ("compliant", "satisfied", "company_meets", "hard_blocker", "go_no_go"):
        assert forbidden not in fields

    payload = fake.SENT_PAYLOADS[0]
    assert "<UNTRUSTED_DOCUMENT_CONTENT>" in payload["user_content"]
