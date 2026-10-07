"""Un document retirat de la font no alimenta cap analisi nova (3B-1, §4.4-4.5).

Regla: *document retirat (`TenderDocument.removed_at IS NOT NULL`) != document
vigent*. No s'esborra res historic: les runs antigues i les seves cites
continuen exactament igual i auditables. El que canvia es que cap carregador
d'inputs NOUS (extraccio de requisits, classificacio, estructura de l'analisi
v3, cites noves) torna a llegir-lo.

Flux real: ingesta PLACSP (fixtures publiques `entry_lots*.atom`: A = PCAP,
B = PPT) -> revisions -> caducitat -> recalcul, amb el proveidor fals. El
contingut dels dos plecs es sintetic (com a `test_requirement_analysis`): la
suite no baixa res.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from app.ai.provider import StructuredResult, Usage
from app.core.errors import NothingAnalyzableError
from app.ingestion.placsp.service import PlacspIngestionService
from app.jobs import requests as job_requests
from app.knowledge import vocabulary
from app.models.classification import (
    TenderDocumentClassificationFinding,
    TenderDocumentClassificationRun,
    TenderDocumentRoleSuggestion,
)
from app.models.enums import (
    AnalysisRunStatus,
    ClassificationTrigger,
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    IngestionStatus,
    PageExtractionStatus,
    TenderDocumentType,
)
from app.models.provenance import TenderSourceRef
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.tender_analysis import (
    AnalysisFinding,
    AnalysisQuestion,
    CapabilityNeedAssessment,
    CriterionAssessment,
    DeliveryCandidate,
    TenderAnalysisRun,
    TenderFitAssessment,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tender_structure import TenderBlock
from app.models.tenders import Tender, TenderDocument
from app.services import (
    document_classification,
    document_review,
    manual_requirement,
    source_refs,
)
from app.services import tender_analysis as analysis
from app.services import tender_lifecycle as lifecycle
from app.services import tender_structure as structure
from app.services.requirement_analysis import analysis_input
from tests.fixtures.company_twin import build_company_twin
from tests.jobs_support import run_worker
from tests.test_ingestion_pagination import PAGE2_URL, Feed, _install

pytestmark = pytest.mark.asyncio

AGREGADAS = Path(__file__).parent / "fixtures" / "placsp" / "agregadas"
BASE_ID = "https://contrataciondelestado.es/sindicacion/PlataformasAgregadasSinMenores/20529505"
SOURCE_CODE = "placsp_agregadas"

PAGE_A = (
    "Clausula 8. Solvencia economica y financiera.\n"
    "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros.\n"
)
QUOTE_A = "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros"
PAGE_B = (
    "Prescripcion tecnica 3. Medios materiales.\n"
    "El adjudicatario dispondra de una fusionadora de fibra optica calibrada.\n"
    "Criterio 2. Memoria tecnica: hasta 35 puntos, sujeta a juicio de valor.\n"
)
QUOTE_B = "El adjudicatario dispondra de una fusionadora de fibra optica calibrada"
QUOTE_B_CRITERION = "Memoria tecnica: hasta 35 puntos"

#: Columnes que canvien legitimament quan una run deixa de ser vigent.
VOLATILE = frozenset({"is_current", "updated_at"})


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


# --------------------------------------------------------------------------- #
# Ajudants                                                                      #
# --------------------------------------------------------------------------- #


class ByExtractionProvider:
    """Proveidor fals que respon segons l'extraccio que porta el chunk.

    Un chunk mai barreja documents (`build_chunks`): si el chunk du el text de B,
    el "model" en treu el requisit i el criteri de B; si du A, el de A. Aixi el
    recalcul nomes pot treure requisits de B si algu li torna a passar B.
    """

    name = "fake"
    model = "fake-model-v1"

    def __init__(self, responses: dict[str, dict[str, list[dict[str, Any]]]]) -> None:
        self.responses = responses
        self.seen: list[str] = []

    def is_available(self) -> bool:
        return True

    async def extract_structured(
        self, *, system: str, user_content: str, json_schema: dict, max_output_tokens: int
    ) -> StructuredResult:
        data: dict[str, list[Any]] = {"requirements": [], "award_criteria": []}
        for extraction_id, response in self.responses.items():
            if f'extraction_id="{extraction_id}"' in user_content:
                self.seen.append(extraction_id)
                for key, items in response.items():
                    data[key].extend(items)
        return StructuredResult(
            data=data,
            usage=Usage(input_tokens=10, output_tokens=5, request_id="fake-req"),
            model=self.model,
        )


def _evidence(extraction_id: uuid.UUID, quote: str) -> list[dict[str, Any]]:
    return [
        {
            "extraction_id": str(extraction_id),
            "page_number": 1,
            "quote": quote,
            "evidence_role": "primary",
        }
    ]


def _responses(extraction_a: uuid.UUID, extraction_b: uuid.UUID) -> dict:
    return {
        str(extraction_a): {
            "requirements": [
                {
                    "category": "economic_solvency",
                    "obligation": "mandatory",
                    "operator": "gte",
                    "scope": "tender",
                    "title": "Volum anual de negoci minim de 450.000 EUR",
                    "value": {"amount": 450000, "currency": "EUR"},
                    "confidence": "high",
                    "evidence": _evidence(extraction_a, QUOTE_A),
                }
            ]
        },
        str(extraction_b): {
            "requirements": [
                {
                    "category": "equipment_or_resources",
                    "obligation": "mandatory",
                    "scope": "tender",
                    "title": "Disposar d'una fusionadora de fibra optica calibrada",
                    "confidence": "high",
                    "evidence": _evidence(extraction_b, QUOTE_B),
                }
            ],
            "award_criteria": [
                {
                    "name": "Memoria tecnica",
                    "points": 35,
                    "evaluation_type": "judgment",
                    "scope": "tender",
                    "confidence": "high",
                    "evidence": _evidence(extraction_b, QUOTE_B_CRITERION),
                }
            ],
        },
    }


async def ingest(db, name_or_path: str | Path) -> None:
    path = name_or_path if isinstance(name_or_path, Path) else AGREGADAS / name_or_path
    run = await PlacspIngestionService(db).run(fixture=path, source_code=SOURCE_CODE)
    assert run.status == IngestionStatus.SUCCESS, run.error_summary


async def the_tender(db) -> Tender:
    return (
        await db.execute(
            sa.select(Tender)
            .where(Tender.external_id == BASE_ID)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def documents_by_type(db, tender_id) -> dict[TenderDocumentType, TenderDocument]:
    rows = (
        await db.execute(
            sa.select(TenderDocument)
            .where(TenderDocument.tender_id == tender_id)
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return {TenderDocumentType(d.document_type): d for d in rows}


async def attach_text(db, document: TenderDocument, text: str):
    """El que deixaria `process_tender_documents`: fitxer, contingut, extraccio i pagina."""
    sha = hashlib.sha256(text.encode()).hexdigest()
    content = TenderDocumentContent(
        sha256=sha,
        storage_key=f"tenders/documents/{sha[:2]}/{sha}.pdf",
        file_size=len(text),
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )
    db.add(content)
    await db.flush()
    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="pypdf-v1",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=1,
        extracted_page_count=1,
        empty_page_count=0,
        text_char_count=len(text),
        ocr_candidate=False,
        is_current=True,
    )
    db.add(extraction)
    await db.flush()
    page = TenderDocumentPage(
        extraction_id=extraction.id,
        page_number=1,
        text=text,
        char_count=len(text),
        text_hash=sha,
        status=PageExtractionStatus.EXTRACTED,
    )
    file = TenderDocumentFile(
        tender_document_id=document.id,
        content_id=content.id,
        source_url=document.url,
        version_number=1,
        is_current=True,
        download_status=DownloadStatus.DOWNLOADED,
    )
    db.add_all([page, file])
    await db.commit()
    return file, extraction, page


async def extract_via_job(
    db, session_factory, *, tender_id, company_id, user_id, provider
) -> RequirementExtractionRun:
    """El cami real de l'extraccio: peticio a la cua i worker, amb el proveidor fals."""

    def factory():
        return provider

    submission = await job_requests.submit_analysis_request(
        db, company_id=company_id, user_id=user_id, tender_id=tender_id, provider_factory=factory
    )
    assert submission.request.work_job_id is not None, "s'havia de tornar a extreure"
    executed = await run_worker(session_factory, provider_factory=factory)
    assert executed
    run = (
        await db.execute(
            sa.select(RequirementExtractionRun)
            .where(
                RequirementExtractionRun.tender_id == tender_id,
                RequirementExtractionRun.is_current.is_(True),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    assert run.work_job_id == submission.request.work_job_id
    assert run.status == AnalysisRunStatus.COMPLETED
    return run


async def classify(db, tender: Tender, company_id) -> None:
    """Classificacio documental. Per a un plec public nomes hi ha la via de servei."""
    await document_classification.classify_tender(
        db, tender=tender, company_id=company_id, trigger=ClassificationTrigger.REQUEST
    )


def runs_url(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis/runs"


async def analyze_via_api(client, db, company_id, tender_id) -> TenderAnalysisRun:
    """El boto «Recalcula» de la web: `POST /analysis/runs`."""
    response = await client.post(runs_url(company_id, tender_id))
    assert response.status_code in (200, 201), response.text
    return await db.get(TenderAnalysisRun, uuid.UUID(response.json()["id"]), populate_existing=True)


async def count_runs(db, company_id, tender_id) -> int:
    return (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderAnalysisRun)
            .where(
                TenderAnalysisRun.company_id == company_id,
                TenderAnalysisRun.tender_id == tender_id,
            )
        )
    ).scalar_one()


async def evidence_documents(db, extraction_run_id) -> set[uuid.UUID]:
    req = (
        await db.execute(
            sa.select(RequirementEvidence.tender_document_id)
            .join(Requirement, Requirement.id == RequirementEvidence.requirement_id)
            .where(Requirement.run_id == extraction_run_id)
        )
    ).scalars()
    crit = (
        await db.execute(
            sa.select(AwardCriterionEvidence.tender_document_id)
            .join(AwardCriterion, AwardCriterion.id == AwardCriterionEvidence.award_criterion_id)
            .where(AwardCriterion.run_id == extraction_run_id)
        )
    ).scalars()
    return set(req) | set(crit)


async def evidence_pointers(db, extraction_run_id) -> set[uuid.UUID]:
    """Extraccions i pagines citades per una run d'extraccio."""
    pointers: set[uuid.UUID] = set()
    for model, parent, fk in (
        (RequirementEvidence, Requirement, RequirementEvidence.requirement_id),
        (AwardCriterionEvidence, AwardCriterion, AwardCriterionEvidence.award_criterion_id),
    ):
        rows = await db.execute(
            sa.select(model.extraction_id, model.page_id)
            .join(parent, parent.id == fk)
            .where(parent.run_id == extraction_run_id)
        )
        for extraction_id, page_id in rows:
            pointers.update({extraction_id, page_id})
    return pointers


def _jsonable(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, datetime, date, Decimal)):
        return str(value)
    return value


async def snapshot(db, ids: dict[str, set[uuid.UUID]]) -> str:
    """Fotografia JSON de les files historiques (sense els punters de vigencia)."""
    selections: list[tuple[str, Any]] = [
        (
            "analysis_runs",
            sa.select(TenderAnalysisRun).where(TenderAnalysisRun.id.in_(ids["analysis_runs"])),
        ),
        (
            "extraction_runs",
            sa.select(RequirementExtractionRun).where(
                RequirementExtractionRun.id.in_(ids["extraction_runs"])
            ),
        ),
        (
            "requirements",
            sa.select(Requirement).where(Requirement.run_id.in_(ids["extraction_runs"])),
        ),
        (
            "requirement_evidence",
            sa.select(RequirementEvidence)
            .join(Requirement, Requirement.id == RequirementEvidence.requirement_id)
            .where(Requirement.run_id.in_(ids["extraction_runs"])),
        ),
        (
            "criteria",
            sa.select(AwardCriterion).where(AwardCriterion.run_id.in_(ids["extraction_runs"])),
        ),
        (
            "criterion_evidence",
            sa.select(AwardCriterionEvidence)
            .join(AwardCriterion, AwardCriterion.id == AwardCriterionEvidence.award_criterion_id)
            .where(AwardCriterion.run_id.in_(ids["extraction_runs"])),
        ),
        (
            "classification_runs",
            sa.select(TenderDocumentClassificationRun).where(
                TenderDocumentClassificationRun.id.in_(ids["classification_runs"])
            ),
        ),
        (
            "suggestions",
            sa.select(TenderDocumentRoleSuggestion).where(
                TenderDocumentRoleSuggestion.run_id.in_(ids["classification_runs"])
            ),
        ),
        (
            "classification_findings",
            sa.select(TenderDocumentClassificationFinding).where(
                TenderDocumentClassificationFinding.run_id.in_(ids["classification_runs"])
            ),
        ),
        ("events", sa.select(TenderChangeEvent).where(TenderChangeEvent.id.in_(ids["events"]))),
        ("revisions", sa.select(TenderRevision).where(TenderRevision.id.in_(ids["revisions"]))),
        (
            "source_refs",
            sa.select(TenderSourceRef).where(TenderSourceRef.id.in_(ids["source_refs"])),
        ),
    ]
    for model in (
        TenderFitAssessment,
        CapabilityNeedAssessment,
        CriterionAssessment,
        DeliveryCandidate,
        AnalysisFinding,
        AnalysisQuestion,
    ):
        selections.append(
            (model.__tablename__, sa.select(model).where(model.run_id.in_(ids["analysis_runs"])))
        )

    result: dict[str, list[dict[str, Any]]] = {}
    for label, query in selections:
        rows = (await db.execute(query.execution_options(populate_existing=True))).scalars()
        dumped = []
        for row in rows:
            columns = sa.inspect(type(row)).mapper.column_attrs
            dumped.append(
                {c.key: _jsonable(getattr(row, c.key)) for c in columns if c.key not in VOLATILE}
            )
        result[label] = sorted(dumped, key=lambda r: str(r["id"]))
    return json.dumps(result, sort_keys=True, default=str)


async def ids_of(db, model, tender_id) -> set[uuid.UUID]:
    return set(
        (await db.execute(sa.select(model.id).where(model.tender_id == tender_id))).scalars()
    )


# --------------------------------------------------------------------------- #
# El flux sencer                                                                #
# --------------------------------------------------------------------------- #


async def test_document_retirat_no_alimenta_cap_input_nou(
    db, session_factory, tenant_a, client_a, monkeypatch
):
    user, company = tenant_a
    await build_company_twin(db, company)
    await db.commit()

    # ---- 1. A + B processats; run v3 inicial (H1) ---------------------------
    await ingest(db, "entry_lots.atom")
    tender = await the_tender(db)
    by_type = await documents_by_type(db, tender.id)
    doc_a = by_type[TenderDocumentType.ADMINISTRATIVE_TERMS]
    doc_b = by_type[TenderDocumentType.TECHNICAL_TERMS]
    _file_a, extraction_a, page_a = await attach_text(db, doc_a, PAGE_A)
    file_b, extraction_b, page_b = await attach_text(db, doc_b, PAGE_B)
    b_ids = {doc_b.id, file_b.id, extraction_b.id, page_b.id}
    provider = ByExtractionProvider(_responses(extraction_a.id, extraction_b.id))

    # Una cita humana a B mentre era vigent, i un bloc on algu la podria enllacar.
    ref_b = await source_refs.create_extracted_page_ref(
        db, tender_id=tender.id, extraction_id=extraction_b.id, page_id=page_b.id
    )
    block = await structure.create_block(db, tender, {"code": "B1", "name": "Bloc unic"})
    manual = await manual_requirement.create_manual_requirement(
        db,
        tender=tender,
        user=user,
        fields={"category": "other", "obligation": "mandatory", "title": "Requisit manual"},
    )
    await db.commit()

    def extract():
        return extract_via_job(
            db,
            session_factory,
            tender_id=tender.id,
            company_id=company.id,
            user_id=user.id,
            provider=provider,
        )

    await classify(db, tender, company.id)
    await extract()
    run1 = await analyze_via_api(client_a, db, company.id, tender.id)
    extraction_run1 = run1.extraction_run_id
    assert doc_b.id in await evidence_documents(db, extraction_run1)
    b_pointers = (await evidence_pointers(db, extraction_run1)) & b_ids
    assert b_pointers == {extraction_b.id, page_b.id}
    b_requirement_keys = set(
        (
            await db.execute(
                sa.select(Requirement.dedup_key)
                .join(RequirementEvidence, RequirementEvidence.requirement_id == Requirement.id)
                .where(
                    Requirement.run_id == extraction_run1,
                    RequirementEvidence.tender_document_id == doc_b.id,
                )
            )
        ).scalars()
    )
    b_criterion_keys = set(
        (
            await db.execute(
                sa.select(AwardCriterion.dedup_key)
                .join(
                    AwardCriterionEvidence,
                    AwardCriterionEvidence.award_criterion_id == AwardCriterion.id,
                )
                .where(
                    AwardCriterion.run_id == extraction_run1,
                    AwardCriterionEvidence.tender_document_id == doc_b.id,
                )
            )
        ).scalars()
    )
    assert b_requirement_keys and b_criterion_keys
    view1, _ = await analysis.load_structure_view(db, tender)
    assert b_requirement_keys <= {r.key for r in view1.requirements}
    assert b_criterion_keys <= {c.key for c in view1.criteria}
    classification1 = await document_classification.current_run(db, company.id, tender.id)
    review_before = await document_review.fingerprint_of(db, tender.id)
    assert review_before.document_count == 2
    basis1 = run1.tender_revision_id

    # ---- 2. publicacio completa posterior sense B -----------------------------
    await ingest(db, "entry_lots_doc_removed.atom")
    tender = await the_tender(db)
    events = list(
        (
            await db.execute(
                sa.select(TenderChangeEvent).where(TenderChangeEvent.tender_id == tender.id)
            )
        ).scalars()
    )
    assert "DOCUMENT_REMOVED" in {e.change_code for e in events}
    by_type = await documents_by_type(db, tender.id)
    assert by_type[TenderDocumentType.TECHNICAL_TERMS].removed_at is not None
    assert by_type[TenderDocumentType.ADMINISTRATIVE_TERMS].removed_at is None

    # ---- 3. la run vigent caduca i la base de l'analisi es mou ----------------
    assert await analysis.is_stale(db, run1) is True
    basis2 = await lifecycle.latest_analysis_affecting_revision(db, tender.id)
    assert basis2.id != basis1

    # ---- 4. B continua a la BD i a l'historial --------------------------------
    for model, row_id in (
        (TenderDocument, doc_b.id),
        (TenderDocumentFile, file_b.id),
        (TenderDocumentExtraction, extraction_b.id),
        (TenderDocumentPage, page_b.id),
    ):
        assert await db.get(model, row_id) is not None
    history = await client_a.get(f"/api/v1/tenders/{tender.id}/history")
    assert history.status_code == 200, history.text
    assert "DOCUMENT_REMOVED" in {
        event["change_code"] for revision in history.json() for event in revision["events"]
    }
    listed = await client_a.get(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/documents",
        params={"include_removed": "true"},
    )
    assert str(doc_b.id) in {d["id"] for d in listed.json()}

    # ---- 5a. sense refer res, el boto «Recalcula» es refusa -------------------
    # La run d'extraccio vigent (i la classificacio) van llegir B: una analisi nova
    # sobre aquesta base estaria alimentada per un plec retirat.
    runs_before = await count_runs(db, company.id, tender.id)
    refused = await client_a.post(runs_url(company.id, tender.id))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "analysis_prerequisites_missing"
    assert error["details"]["missing"] == ["requirements_outdated", "classification_outdated"]
    assert "ja no es a la font" in error["message"]
    assert await count_runs(db, company.id, tender.id) == runs_before
    requirements_read = await client_a.get(f"/api/v1/tenders/{tender.id}/analysis/requirements")
    assert requirements_read.json()["is_stale"] is True

    # ---- 5b. el flux normal: tornar a analitzar els plecs --------------------
    current = await analysis_input(db, tender.id)
    assert {d.tender_document_id for d in current.documents} == {str(doc_a.id)}
    assert str(page_b.id) not in {p.page_id for d in current.documents for p in d.pages}
    assert str(extraction_b.id) not in {d.extraction_id for d in current.documents}

    extraction2 = await extract()
    assert provider.seen.count(str(extraction_b.id)) == 1  # nomes la primera extraccio
    still = await client_a.post(runs_url(company.id, tender.id))
    assert still.status_code == 409, still.text
    assert still.json()["error"]["details"]["missing"] == ["classification_outdated"]
    await classify(db, tender, company.id)
    run2 = await analyze_via_api(client_a, db, company.id, tender.id)
    extraction_run2 = run2.extraction_run_id
    assert extraction_run2 == extraction2.id
    requirements_read = await client_a.get(f"/api/v1/tenders/{tender.id}/analysis/requirements")
    assert requirements_read.json()["is_stale"] is False
    assert extraction_run2 != extraction_run1
    assert doc_b.id not in await evidence_documents(db, extraction_run2)
    assert not (await evidence_pointers(db, extraction_run2)) & b_ids
    assert await evidence_documents(db, extraction_run2) == {doc_a.id}

    classification2 = await document_classification.current_run(db, company.id, tender.id)
    assert classification2.id != classification1.id
    suggested = set(
        (
            await db.execute(
                sa.select(TenderDocumentRoleSuggestion.tender_document_id).where(
                    TenderDocumentRoleSuggestion.run_id == classification2.id
                )
            )
        ).scalars()
    )
    assert suggested == {doc_a.id}
    finding_docs = (
        await db.execute(
            sa.select(
                TenderDocumentClassificationFinding.tender_document_id,
                TenderDocumentClassificationFinding.related_document_id,
            ).where(TenderDocumentClassificationFinding.run_id == classification2.id)
        )
    ).all()
    assert doc_b.id not in {d for row in finding_docs for d in row}
    classification_view = await document_classification.load_view(db, company.id, tender.id)
    assert {d.document.id for d in classification_view.documents} == {doc_a.id}

    view2, _ = await analysis.load_structure_view(db, tender)
    assert not b_requirement_keys & {r.key for r in view2.requirements}
    assert not b_criterion_keys & {c.key for c in view2.criteria}

    review_after = await document_review.fingerprint_of(db, tender.id)
    assert review_after.document_count == 1
    assert review_after.fingerprint != review_before.fingerprint

    # ---- 6. inputs_hash i empremta d'estructura nous -------------------------
    assert run2.inputs_hash != run1.inputs_hash
    assert run2.tender_structure_fingerprint != run1.tender_structure_fingerprint
    assert run2.tender_revision_id == basis2.id

    # ---- 7. cap cita ni enllac nou cap a B ------------------------------------
    with pytest.raises(source_refs.SourceRefDocumentRemovedError):
        await source_refs.create_extracted_page_ref(
            db, tender_id=tender.id, extraction_id=extraction_b.id, page_id=page_b.id
        )
    with pytest.raises(source_refs.SourceRefDocumentRemovedError):
        await source_refs.create_document_file_ref(db, tender_id=tender.id, file_id=file_b.id)
    block = await db.get(TenderBlock, block.id)
    with pytest.raises(source_refs.SourceRefDocumentRemovedError):
        await structure.link_source_ref(db, entity=block, source_ref_id=ref_b.id)
    manual = await db.get(Requirement, manual.id)
    with pytest.raises(source_refs.SourceRefDocumentRemovedError):
        await manual_requirement.link_source_ref(db, requirement=manual, source_ref_id=ref_b.id)
    refs_to_b = set(
        (
            await db.execute(
                sa.select(TenderSourceRef.id).where(TenderSourceRef.tender_document_id == doc_b.id)
            )
        ).scalars()
    )
    assert refs_to_b == {ref_b.id}  # nomes la historica, intacta

    # ---- 8. republicacio amb B: torna a ser elegible; la historia no canvia ----
    history_ids = {
        "analysis_runs": {run1.id, run2.id},
        "extraction_runs": {extraction_run1, extraction_run2},
        "classification_runs": {classification1.id, classification2.id},
        "events": await ids_of(db, TenderChangeEvent, tender.id),
        "revisions": await ids_of(db, TenderRevision, tender.id),
        "source_refs": await ids_of(db, TenderSourceRef, tender.id),
    }
    before = await snapshot(db, history_ids)

    await ingest(db, "entry_lots_republished.atom")
    tender = await the_tender(db)
    assert (await documents_by_type(db, tender.id))[
        TenderDocumentType.TECHNICAL_TERMS
    ].removed_at is None
    await extract()
    await classify(db, tender, company.id)
    run3 = await analyze_via_api(client_a, db, company.id, tender.id)
    assert doc_b.id in await evidence_documents(db, run3.extraction_run_id)
    assert {d.tender_document_id for d in (await analysis_input(db, tender.id)).documents} == {
        str(doc_a.id),
        str(doc_b.id),
    }

    assert await snapshot(db, history_ids) == before

    # ---- 9. un sync `partial` no retira B ni fa caducar l'analisi -------------
    # La pagina 2 (que duria una publicacio sense B) falla: no s'aplica res.
    later = re.sub(
        rb"<updated>2026-10-06T20:00:34.510\+02:00</updated>",
        b"<updated>2026-10-09T20:00:34.510+02:00</updated>",
        (AGREGADAS / "entry_lots_doc_removed.atom").read_bytes(),
    )
    _install(monkeypatch, Feed(page2=later, page2_status=500))
    partial = await PlacspIngestionService(db).run(source_code=SOURCE_CODE, mode="incremental")
    assert partial.status == IngestionStatus.PARTIAL
    tender = await the_tender(db)
    assert (await documents_by_type(db, tender.id))[
        TenderDocumentType.TECHNICAL_TERMS
    ].removed_at is None
    run3 = await db.get(TenderAnalysisRun, run3.id)
    assert await analysis.is_stale(db, run3) is False


async def test_si_es_retiren_tots_els_plecs_no_queda_cap_base_per_analitzar(
    db, session_factory, tenant_a, client_a, tmp_path
):
    """Sense cap plec vigent, ni la classificacio ni l'extraccio antigues son base de res."""
    user, company = tenant_a
    await build_company_twin(db, company)
    await db.commit()
    await ingest(db, "entry_lots.atom")
    tender = await the_tender(db)
    by_type = await documents_by_type(db, tender.id)
    _, extraction_a, _ = await attach_text(
        db, by_type[TenderDocumentType.ADMINISTRATIVE_TERMS], PAGE_A
    )
    _, extraction_b, _ = await attach_text(db, by_type[TenderDocumentType.TECHNICAL_TERMS], PAGE_B)
    provider = ByExtractionProvider(_responses(extraction_a.id, extraction_b.id))
    await classify(db, tender, company.id)
    await extract_via_job(
        db,
        session_factory,
        tender_id=tender.id,
        company_id=company.id,
        user_id=user.id,
        provider=provider,
    )
    first = await analyze_via_api(client_a, db, company.id, tender.id)
    classification = await document_classification.current_run(db, company.id, tender.id)

    # Publicacio completa posterior sense cap plec (variant en memoria).
    text = (AGREGADAS / "entry_lots_doc_removed.atom").read_text(encoding="utf-8")
    without_documents, replaced = re.subn(
        r"<cac:LegalDocumentReference>.*?</cac:LegalDocumentReference>",
        "",
        text,
        flags=re.DOTALL,
    )
    assert replaced == 1
    variant = tmp_path / "entry_lots_sense_plecs.atom"
    variant.write_text(without_documents, encoding="utf-8")
    await ingest(db, variant)
    tender = await the_tender(db)
    assert all(d.removed_at is not None for d in (await documents_by_type(db, tender.id)).values())

    # La classificacio que en parlava deixa de ser vigent (es conserva com a historia).
    assert await classify(db, tender, company.id) is None
    assert await document_classification.current_run(db, company.id, tender.id) is None
    kept = await db.get(TenderDocumentClassificationRun, classification.id, populate_existing=True)
    assert kept is not None and kept.is_current is False

    # Res a tornar a extreure, i l'extraccio antiga no pot alimentar cap analisi nova.
    with pytest.raises(NothingAnalyzableError):
        await job_requests.submit_analysis_request(
            db,
            company_id=company.id,
            user_id=user.id,
            tender_id=tender.id,
            provider_factory=lambda: provider,
        )
    requirements_read = await client_a.get(f"/api/v1/tenders/{tender.id}/analysis/requirements")
    assert requirements_read.json()["is_stale"] is True
    refused = await client_a.post(runs_url(company.id, tender.id))
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["missing"] == ["requirements_outdated"]
    assert await count_runs(db, company.id, tender.id) == 1
    current = await client_a.get(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/analysis/current"
    )
    assert current.json()["id"] == str(first.id)  # la vigent no es toca: queda caducada


async def test_publicacio_completa_de_pagina_llegida_s_aplica_amb_run_partial(db, monkeypatch):
    """§4.4 ratificat: l'absencia la declara l'entrada (completa i posterior), no la
    parcialitat. El que mai s'aplica es el que hi hagi a les pagines no obtingudes."""
    await ingest(db, "entry_lots.atom")
    page1 = (AGREGADAS / "entry_lots_doc_removed.atom").read_bytes()
    page1 = page1.replace(
        b'rel="self"/>',
        b'rel="self"/>\n    <link href="' + PAGE2_URL.encode() + b'" rel="next"/>',
        1,
    )
    _install(monkeypatch, Feed(page1=page1, page2_status=500))
    run = await PlacspIngestionService(db).run(source_code=SOURCE_CODE, mode="incremental")
    assert run.status == IngestionStatus.PARTIAL and run.pages_processed == 1
    tender = await the_tender(db)
    by_type = await documents_by_type(db, tender.id)
    assert by_type[TenderDocumentType.TECHNICAL_TERMS].removed_at is not None
    assert by_type[TenderDocumentType.ADMINISTRATIVE_TERMS].removed_at is None
