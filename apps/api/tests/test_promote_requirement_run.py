"""Promocio auditada d'un run `product-v2` i el que en veu el producte.

Un run product-v2 real i acabat (fet amb el proveidor fals amb nom real: cap
crida de xarxa ni cap LLM) amb els textos de L'Escala: dues cites de la
solvencia economica de 91.904,74 EUR i dues de la RC de 300.000 EUR per
sinistre i any. Es comprova:

* totes les condicions de refus de l'ordre, i que cap refus escriu res;
* la promocio (vigent, error net, auditoria) i la idempotencia;
* que el cami automatic continua refusant product-v2;
* que cap fila d'extraccio canvia (recomptes i hash abans/despres);
* que el run promogut funciona com a vigent a la resta del producte (historial,
  obsolescencia, compliment, candidatura) i que la candidatura no rep cap
  escriptura de l'ordre;
* el compliment v5 sobre aquest run: normalitzacio, grups de duplicats, recompte
  per exigencies, revisio d'un membre i comptadors de la candidatura.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.ai.profiles import PRODUCT_V2, V1
from app.ai.providers import fake
from app.models.audit import AuditLog
from app.models.compliance import CompanyRequirementAssessment
from app.models.enums import (
    AnalysisRunStatus,
    AssessmentResult,
    AuditAction,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
    ReviewStatus,
    TenderDocumentType,
    WorkJobStatus,
    WorkJobType,
)
from app.models.identity import Company
from app.models.jobs import WorkJob
from app.models.profile import CompanyFinancialYear
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
    RequirementUncertainty,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender
from app.ops import promote_requirement_run as ops
from app.services import bid_workspace, workspace_readiness
from app.services.compliance_analysis import analyze_company_compliance, review_assessment
from app.services.requirement_analysis import (
    EXPERIMENTAL_PROFILE_NOT_CURRENT,
    analyze_tender_requirements,
    run_is_outdated,
)
from tests.quota_support import pin_real_model
from tests.real_provider import RealNamedFake

pytestmark = pytest.mark.asyncio

ECON_A = (
    "Volum anual de negoci del millor dels 3 últims exercicis ≥ 1,5 vegades "
    "l’anualitat mitjana del contracte. És a dir, 91.904,74 €"
)
ECON_B = (
    "La solvència econòmica i financera s'acreditarà mitjançant el volum anual de negocis, "
    "referit al millor exercici dins dels tres (3) últims disponibles, per un import igual o "
    "superior a 91.904,74 euros."
)
RC_A = (
    "haurà d'acreditar que disposa d'una pòlissa de responsabilitat civil vigent amb una "
    "cobertura mínima de 300.000 euros per sinistre i any"
)
RC_B = (
    "una pòlissa de responsabilitat civil amb una cobertura mínima de 300.000 € per sinistre i any"
)
TECH = "Centre d’atenció amb tiquet únic, severitat, actuacions, causa arrel i tancament acceptat."

REASON = "Validacio integral pas 1: run3 de L'Escala com a cas de validacio, sense LLM."


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


async def _plec(session, tender_document, texts: list[str], sha: str = "c" * 64):
    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    content = TenderDocumentContent(
        sha256=sha,
        storage_key=f"tenders/documents/cc/cc/{sha}.pdf",
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
        page_count=len(texts),
        extracted_page_count=len(texts),
        empty_page_count=0,
        text_char_count=sum(len(t) for t in texts),
        ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()
    for number, text in enumerate(texts, start=1):
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=number,
                text=text,
                char_count=len(text),
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
    return extraction


def _item(extraction_id, title, quote, value=None, category="economic_solvency"):
    return {
        "category": category,
        "obligation": "mandatory",
        "scope": "tender",
        "title": title,
        "value": value,
        "confidence": "high",
        "evidence": [{"extraction_id": str(extraction_id), "page_number": 1, "quote": quote}],
    }


def _payload(extraction_id) -> dict:
    return {
        "requirements": [
            _item(
                extraction_id,
                "Solvència econòmica: volum anual de negoci",
                ECON_A,
                {
                    "amount": "91.904,74 €",
                    "multiple": "1,5 vegades l'anualitat mitjana",
                    "period": "millor dels 3 últims exercicis",
                },
            ),
            _item(
                extraction_id,
                "Volum anual de negocis mínim",
                ECON_B,
                {
                    "amount": "91.904,74",
                    "currency": "EUR",
                    "period": "millor exercici dins dels tres (3) últims disponibles",
                },
            ),
            _item(
                extraction_id,
                "Pòlissa de RC: 300.000 euros per sinistre i any",
                RC_A,
                None,
                "insurance",
            ),
            _item(
                extraction_id,
                "Pòlissa de RC mínima 300.000 € per sinistre i any",
                RC_B,
                {"amount": "300.000 €", "unit": "per sinistre i any"},
                "insurance",
            ),
            _item(extraction_id, "Operació i informes", TECH, None, "technical_requirement"),
        ]
    }


@pytest.fixture
async def run3(session, tender_document, monkeypatch):
    """Un run product-v2 real, acabat i no vigent, com el run3 de L'Escala."""
    pin_real_model(monkeypatch)
    extraction = await _plec(
        session, tender_document, [" ".join([ECON_A, ECON_B, RC_A, RC_B, TECH])]
    )
    tender = await session.get(Tender, tender_document.tender_id)
    tender.deadline_at = datetime(2026, 10, 20, 21, 59, 59, tzinfo=UTC)
    await session.commit()
    run = (
        await analyze_tender_requirements(
            session,
            tender,
            profile=PRODUCT_V2,
            provider=RealNamedFake(responses=[_payload(extraction.id)]),
        )
    ).run
    assert run.status == AnalysisRunStatus.COMPLETED
    assert (run.is_current, run.error_code) == (False, EXPERIMENTAL_PROFILE_NOT_CURRENT)
    assert run.requirements_extracted == 5
    return run


@pytest.fixture
async def operator(session):
    """Un operador de plataforma, sense empresa (no es un usuari de tenant)."""
    from app.core.security import hash_password
    from app.models.identity import User

    user = User(
        email=f"op-{uuid.uuid4().hex[:8]}@plataforma.test",
        hashed_password=hash_password("x" * 16),
        is_active=True,
        is_superuser=True,
    )
    session.add(user)
    await session.commit()
    return user


async def _extraction_state(session, run_id) -> tuple[dict, str]:
    """Recomptes i hash de TOTES les files d'extraccio del run."""
    requirement_ids = sa.select(Requirement.id).where(Requirement.run_id == run_id)
    criterion_ids = sa.select(AwardCriterion.id).where(AwardCriterion.run_id == run_id)
    tables = {
        "requirements": sa.select(Requirement.__table__).where(Requirement.run_id == run_id),
        "award_criteria": sa.select(AwardCriterion.__table__).where(
            AwardCriterion.run_id == run_id
        ),
        "uncertainties": sa.select(RequirementUncertainty.__table__).where(
            RequirementUncertainty.run_id == run_id
        ),
        "requirement_evidence": sa.select(RequirementEvidence.__table__).where(
            RequirementEvidence.requirement_id.in_(requirement_ids)
        ),
        "award_criterion_evidence": sa.select(AwardCriterionEvidence.__table__).where(
            AwardCriterionEvidence.award_criterion_id.in_(criterion_ids)
        ),
    }
    counts: dict[str, int] = {}
    digest = hashlib.sha256()
    for name, query in tables.items():
        rows = (await session.execute(query)).mappings().all()
        counts[name] = len(rows)
        for row in sorted(rows, key=lambda r: str(r["id"])):
            digest.update(json.dumps(dict(row), sort_keys=True, default=str).encode())
    return counts, digest.hexdigest()


async def _promote(session, run, *, apply=True, reason=REASON, actor=None):
    return await ops.promote_requirement_run(
        session,
        run.id,
        reason=reason,
        actor_email=actor.email if actor else "ningu@example.com",
        release="test-release",
        apply=apply,
    )


async def _audit_rows(session, run_id) -> list[AuditLog]:
    return list(
        (
            await session.scalars(
                sa.select(AuditLog).where(
                    AuditLog.entity_type == ops.ENTITY_TYPE, AuditLog.entity_id == run_id
                )
            )
        ).all()
    )


# --------------------------------------------------------------------------- #
# Refusos                                                                       #
# --------------------------------------------------------------------------- #


async def test_el_dry_run_no_escriu_res(session, run3, operator):
    before = await _extraction_state(session, run3.id)
    result = await _promote(session, run3, apply=False, actor=operator)
    assert result.status == ops.WOULD_PROMOTE and not result.applied and not result.refused
    assert result.run_fingerprint == result.current_fingerprint
    await session.rollback()
    await session.refresh(run3)
    assert (run3.is_current, run3.error_code) == (False, EXPERIMENTAL_PROFILE_NOT_CURRENT)
    assert await _audit_rows(session, run3.id) == []
    assert await _extraction_state(session, run3.id) == before


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("", ops.REASON_TOO_SHORT),
        ("   massa curt   ", ops.REASON_TOO_SHORT),
        ("x" * 201, ops.REASON_TOO_LONG),
    ],
)
async def test_sense_motiu_suficient_refusa(session, run3, operator, reason, code):
    result = await _promote(session, run3, reason=reason, actor=operator)
    assert result.status == code and result.refused


async def test_un_actor_que_no_es_operador_refusa(session, run3, tenant_a):
    user, _ = tenant_a
    assert (await _promote(session, run3, actor=user)).status == ops.ACTOR_NOT_OPERATOR
    assert (await _promote(session, run3)).status == ops.ACTOR_NOT_OPERATOR


async def test_un_run_que_no_existeix(session, operator):
    result = await ops.promote_requirement_run(
        session, uuid.uuid4(), reason=REASON, actor_email=operator.email, release=None, apply=True
    )
    assert result.status == ops.RUN_NOT_FOUND


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("status", AnalysisRunStatus.PARTIAL, ops.RUN_NOT_COMPLETED),
        ("run_basis", "simulation", ops.RUN_NOT_REAL),
        ("error_code", "input_changed", ops.RUN_UNEXPECTED_ERROR_CODE),
    ],
)
async def test_un_run_que_no_compleix_les_condicions_refusa(
    session, run3, operator, field, value, code
):
    setattr(run3, field, value)
    await session.commit()
    result = await _promote(session, run3, actor=operator)
    assert result.status == code
    await session.refresh(run3)
    assert run3.is_current is False


async def test_un_run_d_un_perfil_public_no_passa_per_aqui(session, run3, operator):
    run3.extractor_version = V1.extractor_version
    await session.commit()
    assert (await _promote(session, run3, actor=operator)).status == ops.RUN_NOT_EXPERIMENTAL


async def test_una_empremta_diferent_refusa(session, run3, operator, second_tender_document):
    # Un document nou a la licitacio: l'entrada d'ara ja no es la que va llegir.
    second_tender_document.tender_id = run3.tender_id
    await session.commit()
    await _plec(session, second_tender_document, ["Un altre plec amb text nou."], sha="d" * 64)
    result = await _promote(session, run3, actor=operator)
    assert result.status == ops.INPUT_FINGERPRINT_MISMATCH
    assert result.run_fingerprint != result.current_fingerprint
    await session.refresh(run3)
    assert run3.is_current is False


@pytest.mark.parametrize("kind", ["run", "job", "process"])
async def test_una_analisi_en_curs_refusa(session, run3, operator, kind):
    if kind == "run":
        session.add(
            RequirementExtractionRun(
                tender_id=run3.tender_id,
                status=AnalysisRunStatus.RUNNING,
                input_fingerprint="f" * 64,
                extractor_version=V1.extractor_version,
                prompt_version=V1.prompt_version,
                schema_version=V1.schema_version,
                provider="anthropic",
                model="m",
                started_at=datetime.now(UTC),
            )
        )
    else:
        session.add(
            WorkJob(
                job_type=(
                    WorkJobType.ANALYZE_TENDER_REQUIREMENTS
                    if kind == "job"
                    else WorkJobType.PROCESS_TENDER_DOCUMENTS
                ),
                status=WorkJobStatus.QUEUED if kind == "job" else WorkJobStatus.RUNNING,
                tender_id=run3.tender_id,
                parameters={},
                dedupe_key=f"test-{uuid.uuid4().hex}",
            )
        )
    await session.commit()
    result = await _promote(session, run3, actor=operator)
    assert result.status == ops.ANALYSIS_IN_PROGRESS


# --------------------------------------------------------------------------- #
# Promocio                                                                      #
# --------------------------------------------------------------------------- #


async def test_la_promocio_fa_vigent_el_run_i_ho_audita(session, run3, operator):
    before = await _extraction_state(session, run3.id)
    result = await _promote(session, run3, actor=operator)
    assert result.status == ops.PROMOTED and result.applied
    assert result.original_error_code == EXPERIMENTAL_PROFILE_NOT_CURRENT

    await session.refresh(run3)
    assert run3.is_current is True
    assert (run3.error_code, run3.error_message) == (None, None)
    assert await run_is_outdated(session, run3) is False

    [row] = await _audit_rows(session, run3.id)
    assert row.action == AuditAction.UPDATE and row.user_id == operator.id
    assert "original_error=experimental_profile_not_current" in row.summary
    assert f"fingerprint={run3.input_fingerprint}" in row.summary
    assert "release=test-release" in row.summary and REASON in row.summary

    # Cap fila d'extraccio canviada.
    after = await _extraction_state(session, run3.id)
    assert after == before
    assert after[0]["requirements"] == 5


async def test_promoure_dues_vegades_la_segona_no_fa_res(session, run3, operator):
    assert (await _promote(session, run3, actor=operator)).status == ops.PROMOTED
    second = await _promote(session, run3, actor=operator)
    assert second.status == ops.ALREADY_CURRENT and not second.applied and not second.refused
    assert len(await _audit_rows(session, run3.id)) == 1


async def test_el_vigent_anterior_deixa_de_ser_ho(session, run3, operator):
    previous = RequirementExtractionRun(
        tender_id=run3.tender_id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint="e" * 64,
        extractor_version=V1.extractor_version,
        prompt_version=V1.prompt_version,
        schema_version=V1.schema_version,
        provider="anthropic",
        model="m",
        run_basis="real",
        is_current=True,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session.add(previous)
    await session.commit()
    result = await _promote(session, run3, actor=operator)
    assert result.status == ops.PROMOTED
    assert result.previous_current_run_id == str(previous.id)
    await session.refresh(previous)
    await session.refresh(run3)
    assert (previous.is_current, run3.is_current) == (False, True)
    [row] = await _audit_rows(session, run3.id)
    assert f"previous={previous.id}" in row.summary


async def test_el_cami_automatic_continua_refusant_product_v2(session, run3, operator):
    """Un product-v2 nou no passa sol a vigent, ni abans ni despres de promoure."""
    await _promote(session, run3, actor=operator)
    tender = await session.get(Tender, run3.tender_id)
    extraction_id = await session.scalar(sa.select(RequirementEvidence.extraction_id).limit(1))
    newer = (
        await analyze_tender_requirements(
            session,
            tender,
            profile=PRODUCT_V2,
            force=True,
            provider=RealNamedFake(responses=[_payload(extraction_id)]),
        )
    ).run
    assert newer.id != run3.id
    assert (newer.is_current, newer.error_code) == (False, EXPERIMENTAL_PROFILE_NOT_CURRENT)
    await session.refresh(run3)
    assert run3.is_current is True


async def test_l_historial_el_mostra_com_a_vigent(session, run3, operator, client_a):
    await _promote(session, run3, actor=operator)
    response = await client_a.get(f"/api/v1/tenders/{run3.tender_id}/analysis/runs")
    assert response.status_code == 200
    [row] = [r for r in response.json() if r["id"] == str(run3.id)]
    assert row["is_current"] is True and row["error_code"] is None


# --------------------------------------------------------------------------- #
# Candidatura: el que canvia i el que no                                        #
# --------------------------------------------------------------------------- #


async def test_la_candidatura_veu_l_analisi_pero_l_ordre_no_li_escriu_res(
    session, run3, operator, tenant_a
):
    user, company = tenant_a
    tender = await session.get(Tender, run3.tender_id)
    workspace = (
        await bid_workspace.create_workspace(
            session, company_id=company.id, tender=tender, user_id=user.id
        )
    ).workspace
    assert workspace.current_requirement_run_id is None
    final = await workspace_readiness.final_readiness(session, workspace)
    assert "analysis_missing" in final.blockers

    await _promote(session, run3, actor=operator)
    await session.refresh(workspace)
    # L'ordre no toca la candidatura...
    assert workspace.current_requirement_run_id is None
    final = await workspace_readiness.final_readiness(session, workspace)
    # ...pero la lectura ja troba una analisi vigent: `analysis_missing` desapareix
    # i, com amb qualsevol analisi nova, la candidatura queda desfasada fins que
    # l'usuari regenera la checklist.
    assert "analysis_missing" not in final.blockers
    assert "analysis_outdated" in final.blockers

    created = await bid_workspace.regenerate_checklist(session, workspace, user_id=user.id)
    assert created == 5
    await session.refresh(workspace)
    assert workspace.current_requirement_run_id == run3.id
    final = await workspace_readiness.final_readiness(session, workspace)
    assert "analysis_outdated" not in final.blockers and "analysis_missing" not in final.blockers


# --------------------------------------------------------------------------- #
# Compliment v5 sobre el run promogut                                           #
# --------------------------------------------------------------------------- #


async def test_compliment_normalitza_i_compta_exigencies(
    session, run3, operator, tenant_a, client_a
):
    _user, company_ref = tenant_a
    await _promote(session, run3, actor=operator)
    company = await session.get(Company, company_ref.id)
    session.add(
        CompanyFinancialYear(
            company_id=company.id, year=2024, revenue=Decimal("120000"), currency="EUR"
        )
    )
    await session.commit()
    tender = await session.get(Tender, run3.tender_id)
    before = await _extraction_state(session, run3.id)

    run = (await analyze_company_compliance(session, company, tender)).run
    assert run.rules_version == "company-compliance-v5"
    # 5 requisits, 3 exigencies: economica (2), RC (2) i la tecnica.
    assert run.requirements_total == 3
    assert run.satisfies_count == 1  # la solvencia economica, un sol cop
    assert run.unknown_count == 1  # la RC: cap polissa al perfil
    assert run.needs_review_count == 1  # la tecnica

    assessments = list(
        (
            await session.scalars(
                sa.select(CompanyRequirementAssessment).where(
                    CompanyRequirementAssessment.assessment_run_id == run.id
                )
            )
        ).all()
    )
    assert len(assessments) == 5  # una avaluacio per requisit, com sempre
    economic = [a for a in assessments if a.rule_code == "economic_turnover_aggregate_v2"]
    assert {a.automatic_result for a in economic} == {AssessmentResult.SATISFIES}
    groups = {a.explanation_params["consolidation"]["canonical_requirement_id"] for a in economic}
    assert len(groups) == 1
    assert sum(a.explanation_params["consolidation"]["is_canonical"] for a in economic) == 1
    for assessment in economic:
        normalization = assessment.explanation_params["normalization"]
        assert normalization["profile"] == "product-v2"
        assert normalization["value"]["threshold"]["amount"] == "91904.74"
    # La fila d'extraccio no ha canviat (el valor normalitzat no s'hi escriu).
    assert await _extraction_state(session, run3.id) == before

    # API: el grup amb totes les cites dels membres.
    response = await client_a.get(f"/api/v1/companies/{company.id}/tenders/{tender.id}/compliance")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["groups"]) == 2
    by_id = {a["id"]: a for a in body["assessments"]}
    for group in body["groups"]:
        assert len(group["member_assessment_ids"]) == 2
        quotes = [
            c["quote"]
            for member in group["member_assessment_ids"]
            for c in by_id[member]["requirement"]["citations"]
        ]
        assert len(quotes) == 2
    assert {g["effective_result"] for g in body["groups"]} == {"satisfies", "unknown"}

    # Comptadors de la candidatura: tambe per exigencies.
    counters = await bid_workspace.compliance_counters(session, run, None)
    assert (counters.total, counters.satisfies, counters.unknown) == (3, 1, 1)

    # Revisar UN membre (dir que no compleix) fa divergent el grup: mana el mes
    # prudent, i el recompte ho reflecteix sense tocar l'altre membre.
    member = next(a for a in economic if not a.explanation_params["consolidation"]["is_canonical"])
    await review_assessment(
        session,
        member,
        reviewed_result=AssessmentResult.DOES_NOT_SATISFY,
        review_status=ReviewStatus.VERIFIED,
        note="Els comptes de 2024 no estan dipositats.",
        user_id=_user.id,
    )
    await session.refresh(run)
    assert (run.requirements_total, run.satisfies_count, run.does_not_satisfy_count) == (3, 0, 1)
    response = await client_a.get(f"/api/v1/companies/{company.id}/tenders/{tender.id}/compliance")
    economic_group = next(
        g
        for g in response.json()["groups"]
        if member.id.hex in {uuid.UUID(m).hex for m in g["member_assessment_ids"]}
    )
    assert economic_group["divergent"] is True
    assert economic_group["effective_result"] == "does_not_satisfy"
    # La divergencia de cada avaluacio es la del grup ara, no la desada al calcul.
    members = {m for m in economic_group["member_assessment_ids"]}
    for read in response.json()["assessments"]:
        if read["id"] in members:
            assert read["consolidation"]["divergent"] is True
            # I6: el valor comparat (normalitzat) i la seva procedencia, a banda del cru.
            assert read["compared_value"]["threshold"]["amount"] == "91904.74"
            assert read["requirement_value_json"]["amount"].startswith("91.904,74")
            assert read["compared_provenance"][0]["source"] == "value_json"
            assert read["normalization_status"] == "normalized"
            assert "_normalizer" not in read["compared_value"]


async def test_un_run_que_cita_un_document_retirat_refusa(session, run3, operator, monkeypatch):
    """La branca de `run_includes_removed_document` (amb l'empremta igual)."""

    async def cites_removed(_session, _run):
        return True

    monkeypatch.setattr(ops, "run_includes_removed_document", cites_removed)
    result = await _promote(session, run3, actor=operator)
    assert result.status == ops.RUN_CITES_REMOVED_DOCUMENT
    await session.refresh(run3)
    assert run3.is_current is False


async def test_un_run_llancat_amb_force_en_una_licitacio_publica_refusa(session, run3, operator):
    job = WorkJob(
        job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
        status=WorkJobStatus.SUCCEEDED,
        tender_id=run3.tender_id,
        parameters={"force": True},
        dedupe_key=f"test-{uuid.uuid4().hex}",
    )
    session.add(job)
    await session.flush()
    run3.work_job_id = job.id
    await session.commit()
    result = await _promote(session, run3, actor=operator)
    assert (result.status, result.forced) == (ops.RUN_FORCED_NOT_CANONICAL, True)


async def test_sense_feina_forced_es_desconegut_i_queda_a_l_auditoria(session, run3, operator):
    result = await _promote(session, run3, actor=operator)
    assert result.status == ops.PROMOTED and result.forced is None
    [row] = await _audit_rows(session, run3.id)
    assert "forced=-" in row.summary


async def test_un_resum_que_no_cap_sencer_a_l_auditoria_refusa(session, run3, operator):
    result = await ops.promote_requirement_run(
        session,
        run3.id,
        reason="x" * 200,
        actor_email=operator.email,
        release="r" * 120,
        apply=True,
    )
    assert result.status == ops.AUDIT_SUMMARY_TOO_LONG
    assert await _audit_rows(session, run3.id) == []
    # Amb un release normal, el motiu de 200 caracters hi cap sencer.
    result = await ops.promote_requirement_run(
        session, run3.id, reason="y" * 200, actor_email=operator.email, release="abc1234",
        apply=True,
    )  # fmt: skip
    assert result.status == ops.PROMOTED
    [row] = await _audit_rows(session, run3.id)
    assert row.summary.endswith("y" * 200)
