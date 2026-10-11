"""El motor d'analisi (0051) contra la base: semantica dels requisits obligatoris,
dels criteris, de les proves i de les vies de prestacio; staleness, idempotencia,
transaccio, model i lectura. Un plec sintetic petit i un besso d'empresa.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.analysis import registry
from app.analysis.registry import RULES_VERSION, RULES_VERSION_V2
from app.analysis.registry import ExplanationCode as X
from app.analysis.registry import QuestionCode as Q
from app.analysis.registry import RuleCode as R
from app.knowledge import vocabulary
from app.models.enums import (
    AreaKind,
    AwardCriterionType,
    AwardEvaluationType,
    CapabilityStatus,
    ContractFactCode,
    DeliveryMode,
    NeedComparator,
    NeedType,
    OperatingFactKey,
    RequirementCategory,
    RequirementObligation,
    RequirementTiming,
    TerritoryCompleteness,
    VerificationStatus,
)
from app.models.knowledge import CompanyOperatingFact, SolutionCapability
from app.models.profile import CompanyService
from app.models.tender_analysis import (
    AnalysisFinding,
    AnalysisQuestion,
    CriterionAssessment,
    DeliveryCandidate,
    TenderAnalysisRun,
    TenderFitAssessment,
)
from app.services import tender_analysis as analysis
from app.services import tender_scope as scope
from app.services import tender_structure
from tests import test_tender_scope_models as m
from tests.fixtures.company_twin import build_company_twin
from tests.structure_support import PROV
from tests.test_company_knowledge_records_api import _link_evidence

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _requirement(db, tender, title, category, **fields):
    r = await m._requirement(db, tender, title=title)
    r.category = category
    r.obligation = fields.pop("obligation", RequirementObligation.MANDATORY)
    for key, value in fields.items():
        setattr(r, key, value)
    await db.flush()
    return r


async def _count(db, model, **where) -> int:
    query = sa.select(sa.func.count()).select_from(model)
    for key, value in where.items():
        query = query.where(getattr(model, key) == value)
    return await db.scalar(query)


def _by_requirement(run, requirement):
    return next(a for a in run.fit_assessments if a.requirement_id == requirement.id)


def _by_criterion(run, criterion):
    return next(a for a in run.criterion_assessments if a.award_criterion_id == criterion.id)


async def _synthetic_tender(db):
    """Un plec petit que toca cada regla una vegada."""
    tender = await m._public_tender(db, title="Plec sintetic de l'analisi")
    b1 = await m._block(db, tender, "B1")
    b3 = await m._block(db, tender, "B3")
    run_criterion = await m._criterion(db, tender, name="C1 FTTH propia", points=5.0)
    run_criterion.criterion_type = AwardCriterionType.IMPROVEMENT
    run_criterion.evaluation_type = AwardEvaluationType.AUTOMATIC
    run_criterion.timing = RequirementTiming.AT_SUBMISSION
    t1 = await m._criterion(db, tender, name="T1 canals", points=2.0)
    t1.run_id = run_criterion.run_id
    t1.criterion_type = AwardCriterionType.IMPROVEMENT
    t1.evaluation_type = AwardEvaluationType.AUTOMATIC
    t1.tranches = [
        {"min": 30, "points": 0.67},
        {"min": 40, "points": 1.33},
        {"min": 50, "points": 2},
    ]
    j6 = await m._criterion(db, tender, name="J6 demo", points=7.0)
    j6.run_id = run_criterion.run_id
    j6.criterion_type = AwardCriterionType.TECHNICAL
    j6.evaluation_type = AwardEvaluationType.JUDGMENT
    p1 = await m._criterion(db, tender, name="P1 preu", points=8.0)
    p1.run_id = run_criterion.run_id
    p1.criterion_type = AwardCriterionType.PRICE
    p1.evaluation_type = AwardEvaluationType.AUTOMATIC
    await db.flush()
    # La run de `_criterion` no es vigent: en fem una de vigent per a tots.
    from app.models.requirements import RequirementExtractionRun

    run = await db.get(RequirementExtractionRun, run_criterion.run_id)
    run.run_basis = "real"  # sintetica vigent: a PG nomes real/gold (0061)
    run.is_current = True
    await db.flush()

    reqs = {}
    reqs["fact_proof"] = await _requirement(
        db, tender, "Operador registrat", RequirementCategory.REGISTRATION,
        value_json={"operating_fact": "telecom_operator_cnmc", "expected": True},
        timing=RequirementTiming.AT_SUBMISSION,
    )  # fmt: skip
    scope.set_proof(
        reqs["fact_proof"],
        [{"kind": "registry_inscription", "text": "Certificat", "mandatory": True}],
    )
    reqs["fact_false"] = await _requirement(
        db, tender, "Suport 24x7", RequirementCategory.TECHNICAL_REQUIREMENT,
        value_json={"operating_fact": "support_24x7", "expected": True},
        timing=RequirementTiming.AT_SUBMISSION,
    )  # fmt: skip
    scope.set_proof(reqs["fact_false"], [])
    reqs["cert"] = await _requirement(
        db, tender, "ENS", RequirementCategory.CERTIFICATION,
        value_json={"standard": "ENS", "level": None}, timing=RequirementTiming.AT_EXECUTION,
    )  # fmt: skip
    scope.set_proof(
        reqs["cert"], [{"kind": "certificate", "text": "Certificat ENS", "mandatory": False}]
    )
    reqs["profile"] = await _requirement(
        db, tender, "Volum de negoci", RequirementCategory.ECONOMIC_SOLVENCY,
        value_json={"amount_eur": "91904.74"}, timing=RequirementTiming.AT_SUBMISSION,
    )  # fmt: skip
    scope.set_proof(
        reqs["profile"], [{"kind": "annual_accounts", "text": "Comptes", "mandatory": True}]
    )
    reqs["contract"] = await _requirement(
        db, tender, "Garantia definitiva", RequirementCategory.CONTRACT_CONDITION,
        timing=RequirementTiming.BEFORE_FORMALIZATION,
    )  # fmt: skip
    scope.set_proof(reqs["contract"], [])
    reqs["c1"] = await _requirement(
        db, tender, "FTTH propia a la trama urbana", RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.SCORED,
        value_json={"operating_fact": "own_ftth_infrastructure", "expected": True},
        timing=RequirementTiming.AT_SUBMISSION,
    )  # fmt: skip
    scope.set_proof(
        reqs["c1"], [{"kind": "coverage_map", "text": "Cartografia", "mandatory": True}]
    )
    await tender_structure.set_requirement_blocks(db, tender, reqs["c1"], [b1.id])
    await scope.set_territory_semantics(
        db, tender, reqs["c1"], completeness=TerritoryCompleteness.COMPLETE,
        territories=[
            {"kind": AreaKind.MUNICIPALITY, "label": "L'Escala", "qualifier": "trama urbana"}
        ],
        provenance=PROV,
    )  # fmt: skip
    await scope.set_award_links(db, tender, reqs["c1"], [run_criterion.id], provenance=PROV)
    reqs["pbx"] = await _requirement(
        db, tender, "Centraleta", RequirementCategory.TECHNICAL_REQUIREMENT,
        timing=RequirementTiming.AT_EXECUTION,
    )  # fmt: skip
    scope.set_proof(reqs["pbx"], [])
    await tender_structure.set_requirement_blocks(db, tender, reqs["pbx"], [b3.id])
    await scope.set_territory_completeness(db, reqs["pbx"], TerritoryCompleteness.NOT_APPLICABLE)
    await scope.set_award_links(db, tender, reqs["pbx"], [t1.id, j6.id], provenance=PROV)

    def need(**f):
        return {
            "capability_code": None,
            "unmapped_capability": None,
            "comparator": NeedComparator.PRESENT,
            "value_json": None,
            "value_schema": None,
            "need_type": NeedType.MINIMUM,
            "points": None,
            "award_criterion_id": None,
            **f,
        }

    await scope.create_need(
        db,
        tender,
        reqs["pbx"],
        need(
            capability_code="pbx.concurrent_calls",
            comparator=NeedComparator.GTE,
            value_json={"channels": 20},
            value_schema="v1",
        ),
        provenance=PROV,
    )
    await scope.create_need(
        db, tender, reqs["pbx"], need(capability_code="pbx.queues"), provenance=PROV
    )
    for threshold, points in ((30, 0.67), (40, 1.33), (50, 2.0)):
        await scope.create_need(
            db,
            tender,
            reqs["pbx"],
            need(
                capability_code="pbx.concurrent_calls",
                comparator=NeedComparator.GTE,
                value_json={"channels": threshold},
                value_schema="v1",
                need_type=NeedType.SCORED_IMPROVEMENT,
                points=points,
                award_criterion_id=t1.id,
            ),
            provenance=PROV,
        )
    await scope.create_need(
        db,
        tender,
        reqs["pbx"],
        need(
            capability_code="pbx.hot_desking",
            need_type=NeedType.DEMO,
            points=1,
            award_criterion_id=j6.id,
        ),
        provenance=PROV,
    )
    await scope.create_need(
        db,
        tender,
        reqs["pbx"],
        need(
            unmapped_capability="escena_usuari",
            need_type=NeedType.DEMO,
            points=1,
            award_criterion_id=j6.id,
        ),
        provenance=PROV,
    )
    # Cap dimensio territorial als requisits que no en tenen: si no, `unknown` per defecte
    # (el que te tot plec antic) genera, amb rao, una pregunta per la llista de territoris.
    for code in ("fact_proof", "fact_false", "cert", "profile", "contract"):
        await scope.set_territory_completeness(db, reqs[code], TerritoryCompleteness.NOT_APPLICABLE)
    await scope.create_fact(
        db,
        tender,
        fact_code=ContractFactCode.ENVELOPES,
        value_json={"A": "x"},
        value_schema="v1",
        block_ids=[],
        provenance=PROV,
    )

    items = {
        "ftth": await scope.create_scope_item(
            db,
            tender,
            {
                "service_line_code": "access_ftth",
                "block_id": b1.id,
                "quantity": Decimal("15"),
                "unit": "acces",
            },
            provenance=PROV,
        ),
        "pbx": await scope.create_scope_item(
            db,
            tender,
            {"service_line_code": "voice_pbx", "block_id": b3.id, "quantity": Decimal("123")},
            provenance=PROV,
        ),
        "civil": await scope.create_scope_item(
            db, tender, {"service_line_code": "civil_works", "block_id": b1.id}, provenance=PROV
        ),
        "mobile": await scope.create_scope_item(
            db,
            tender,
            {"service_line_code": "mobile_telephony", "block_id": b3.id},
            provenance=PROV,
        ),
    }
    await db.flush()
    return (
        tender,
        reqs,
        {"c1": run_criterion, "t1": t1, "j6": j6, "p1": p1},
        items,
        {"b1": b1, "b3": b3},
    )


async def _twin(db, company, client=None):
    rows = await build_company_twin(db, company)
    # El fet de suport 24x7 el besso el diu CERT; aqui l'empresa diu que NO.
    rows["facts"]["support_24x7"].value = False
    # Commit abans d'enllacar evidencia per l'API: a PostgreSQL la peticio va en una
    # altra transaccio i no veuria res de pendent.
    await db.commit()
    if client is not None:
        await _link_evidence(client, company, "company_certification", rows["certification"].id)
    await db.commit()
    return rows


# --------------------------------------------------------------------------- #
# Semantica                                                                     #
# --------------------------------------------------------------------------- #


async def test_mandatory_proof_criteria_and_delivery_semantics(db, tenant_a, client_a):
    _, company = tenant_a
    tender, reqs, crits, items, blocks = await _synthetic_tender(db)
    rows = await _twin(db, company, client_a)
    knowledge_before = (await analysis.load_company_view(db, company.id)).knowledge_fingerprint

    outcome = await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)
    await db.commit()
    run = outcome.run
    assert not outcome.reused and run.is_current and run.rules_version == RULES_VERSION
    await db.refresh(run)

    # «No consta» es unknown, mai does_not_satisfy; una dada declarada demana revisio.
    cert = _by_requirement(run, reqs["cert"])
    assert (
        cert.fit_result, cert.knowledge_source, cert.verification_status, cert.proof_status
    ) == (
        "satisfies", "current_declared", "not_verified", "sufficient",
    )  # fmt: skip
    assert cert.explanation_code == X.CERT_CURRENT_DECLARED
    proof = _by_requirement(run, reqs["fact_proof"])
    assert (proof.fit_result, proof.proof_status, proof.explanation_code) == (
        "unknown",
        "missing",
        X.PROOF_MISSING_FOR_CLAIM,
    )
    profile = _by_requirement(run, reqs["profile"])
    assert (profile.fit_result, profile.knowledge_source, profile.proof_status) == (
        "unknown",
        "none",
        "unknown",
    )
    contract = _by_requirement(run, reqs["contract"])
    assert (contract.fit_result, contract.knowledge_source, contract.verification_status) == (
        "not_applicable",
        "not_applicable",
        "not_applicable",
    )
    false = _by_requirement(run, reqs["fact_false"])
    assert (false.fit_result, false.explanation_code) == ("does_not_satisfy", X.FACT_CONTRADICTS)
    # Territori: FTTH propia amb cobertura completa en altres municipis -> unmatched, punts perduts.
    c1 = _by_requirement(run, reqs["c1"])
    assert (c1.fit_result, c1.territory_verdict, c1.explanation_code) == (
        "does_not_satisfy",
        "unmatched",
        X.FACT_TRUE_TERRITORY_UNMATCHED,
    )
    assert c1.support_json["territory"]["basis"] == "fact:own_ftth_infrastructure"
    c1_points = _by_criterion(run, crits["c1"])
    assert (c1_points.criterion_result, c1_points.points_status, c1_points.lost_points) == (
        "not_achievable",
        "lost",
        5.0,
    )
    # Necessitats: 3CX sense capacitats -> unknown; trams sense dada -> no avaluables, mai perduts.
    pbx = _by_requirement(run, reqs["pbx"])
    assert (pbx.fit_result, pbx.explanation_code) == ("unknown", X.NEEDS_UNKNOWN)
    t1 = _by_criterion(run, crits["t1"])
    assert (t1.points_status, t1.at_risk_points, t1.lost_points) == ("at_risk", 2.0, 0.0), (
        "sense dada: compromis obert, mai perdut ni sense avaluar"
    )
    j6 = _by_criterion(run, crits["j6"])
    assert (j6.criterion_result, j6.not_assessable_points, j6.lost_points) == (
        "not_assessable",
        7.0,
        0.0,
    )
    p1 = _by_criterion(run, crits["p1"])
    assert (p1.criterion_result, p1.explanation_code) == (
        "not_assessable",
        X.CRITERION_NOT_ASSESSABLE_PRICE,
    )
    for c in run.criterion_assessments:
        assert (
            abs(
                c.secured_points
                + c.at_risk_points
                + c.lost_points
                + c.not_assessable_points
                - c.max_points
            )
            < 1e-6
        )

    # Troballes: cap bloqueig confirmat; un de potencial pel fet que contradiu; revisio per l'ENS.
    kinds = {(f.finding_type, f.requirement_id, f.criterion_id) for f in run.findings}
    assert ("blocker_potential", reqs["fact_false"].id, None) in kinds
    assert ("blocker_potential", reqs["c1"].id, None) not in kinds, "puntuacio, no exclusio"
    assert ("review_required", reqs["cert"].id, None) in kinds
    assert ("territory_gap", reqs["c1"].id, None) in kinds
    assert ("evidence_gap", reqs["fact_proof"].id, None) in kinds
    assert ("warning", None, crits["j6"].id) in kinds
    assert not any(f.finding_type == "blocker_confirmed" for f in run.findings)
    assert not any(
        f.requirement_id == reqs["profile"].id and f.finding_type == "blocker_potential"
        for f in run.findings
    )
    questions = {(q.audience, q.question_code) for q in run.questions}
    assert ("company", Q.COMPANY_PROVIDE_PROOF) in questions
    assert ("company", Q.COMPANY_PROVIDE_PROFILE_DATA) in questions
    assert ("company", Q.COMPANY_DECLARE_CAPABILITY) in questions
    assert len({q.dedupe_key for q in run.questions}) == len(run.questions)

    # Vies de prestacio: nomes des dels serveis actuals.
    def candidates(item):
        return [c for c in run.delivery_candidates if c.scope_item_id == item.id]

    ftth = candidates(items["ftth"])
    assert sorted(c.candidate_type for c in ftth) == ["partner_current"] * 4
    assert {c.partner_role_id for c in ftth} == {
        r.id for n, r in rows["roles"].items() if n != "civil_works"
    }
    assert {c.readiness for c in ftth} == {"pending_coverage"} and {
        c.territory_verdict for c in ftth
    } == {"unknown"}
    pbx_c = candidates(items["pbx"])
    assert [(c.candidate_type, c.readiness, c.territory_verdict) for c in pbx_c] == [
        ("own_current", "available", "not_applicable")
    ]
    assert pbx_c[0].company_service_id == rows["services"]["voice_pbx"].id
    civil = candidates(items["civil"])
    assert [(c.candidate_type, c.readiness) for c in civil] == [
        ("partner_current", "pending_coverage")
    ]
    mobile = candidates(items["mobile"])
    assert [(c.candidate_type, c.readiness, c.explanation_code) for c in mobile] == [
        ("subcontract_unknown", "pending_validation", X.DELIVERY_NO_SERVICE)
    ]
    assert any(
        f.finding_type == "delivery_gap" and f.scope_item_id == items["mobile"].id
        for f in run.findings
    )
    assert not any(c.candidate_type == "own_future" for c in run.delivery_candidates)

    # Cap efecte sobre el coneixement ni sobre el plec.
    assert (
        await analysis.load_company_view(db, company.id)
    ).knowledge_fingerprint == knowledge_before
    view, _ = await analysis.load_structure_view(db, tender)
    assert analysis.structure_fingerprint(view) == run.tender_structure_fingerprint
    summary = analysis.summary_of(run)
    assert (
        summary["blocker_potential_count"] == 1
        and "score" not in summary
        and "recommendation" not in summary
    )


async def test_a_capability_with_data_moves_points_and_a_verified_fact_secures(
    db, tenant_a, client_a
):
    _, company = tenant_a
    tender, reqs, crits, items, _ = await _synthetic_tender(db)
    rows = await _twin(db, company, client_a)
    # 40 canals declarats: el minim (20) i els trams 30/40 en risc, el tram 50 perdut.
    db.add(
        SolutionCapability(
            company_id=company.id,
            solution_id=rows["solution"].id,
            capability_code="pbx.concurrent_calls",
            status=CapabilityStatus.SUPPORTED,
            value_number=Decimal("40"),
        )
    )
    db.add(
        SolutionCapability(
            company_id=company.id,
            solution_id=rows["solution"].id,
            capability_code="pbx.queues",
            status=CapabilityStatus.SUPPORTED,
        )
    )
    await db.commit()
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run)
    pbx = _by_requirement(run, reqs["pbx"])
    assert (pbx.fit_result, pbx.knowledge_source, pbx.verification_status) == (
        "satisfies",
        "current_declared",
        "not_verified",
    )
    t1 = _by_criterion(run, crits["t1"])
    # 40 canals declarats: els trams 30 i 40 en risc (declarats) i el tram 50 TAMBE en
    # risc, no perdut: es pot ampliar com a compromis d'oferta.
    assert (
        round(t1.at_risk_points, 2),
        round(t1.lost_points, 2),
        t1.points_status,
        t1.criterion_result,
    ) == (2.0, 0.0, "at_risk", "needs_review")
    below = [n for n in run.need_assessments if n.explanation_code == X.CAPABILITY_BELOW_REQUIRED]
    assert len(below) == 1 and below[0].fit_result == "does_not_satisfy"
    # Causa real de T1 (v3): els trams 30 i 40 son declarats sense verificar; el de 50
    # queda per sota del valor d'avui. Un finding per causa, amb les seves necessitats.
    scored = {
        str(n.capability_need_id): n
        for n in run.need_assessments
        if (n.support_json or {}).get("required") in ("30", "40", "50")
    }
    declared = sorted(
        k for k, n in scored.items() if (n.fit_result, n.knowledge_source) == (
            "satisfies", "current_declared"
        )
    )  # fmt: skip
    assert len(declared) == 2 and [str(below[0].capability_need_id)] == sorted(
        set(scored) - set(declared)
    )
    t1_findings = sorted(
        (f.finding_type.value, f.explanation_code, sorted(f.details_json["need_ids"]))
        for f in run.findings
        if f.criterion_id == crits["t1"].id
    )
    assert t1_findings == [
        ("review_required", X.CAPABILITY_BELOW_REQUIRED, [str(below[0].capability_need_id)]),
        ("review_required", X.DECLARED_NOT_VERIFIED, declared),
    ]
    # Una capacitat que l'empresa diu que NO te: la necessitat minima no es compleix.
    db.add(
        SolutionCapability(
            company_id=company.id,
            solution_id=rows["solution"].id,
            capability_code="pbx.hot_desking",
            status=CapabilityStatus.NOT_SUPPORTED,
        )
    )
    await db.commit()
    run2 = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run2)
    demo = [n for n in run2.need_assessments if n.explanation_code == X.CAPABILITY_NOT_SUPPORTED]
    assert len(demo) == 1 and demo[0].fit_result == "does_not_satisfy"
    j6 = _by_criterion(run2, crits["j6"])
    assert j6.not_assessable_points == 7.0, (
        "el judici de valor no es puntua encara que una escena falli"
    )


# --------------------------------------------------------------------------- #
# Staleness, idempotencia, transaccio                                           #
# --------------------------------------------------------------------------- #


async def test_runs_are_immutable_idempotent_and_go_stale_only_on_material_change(
    db, tenant_a, client_a
):
    _, company = tenant_a
    tender, reqs, crits, items, blocks = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    a = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    assert await analysis.is_stale(db, a) is False
    # Mateixos inputs: cap run nova.
    again = await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)
    await db.commit()
    assert again.reused and again.run.id == a.id and await _count(db, TenderAnalysisRun) == 1
    # Nomes una cita: mateix fingerprint, no stale.
    from app.models.enums import DocumentFileOrigin, DownloadStatus, TenderDocumentType
    from app.models.provenance import TenderSourceRef
    from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
    from app.models.tenders import TenderDocument
    from app.services import source_refs

    document = TenderDocument(
        tender_id=tender.id, name="PCAP", document_type=TenderDocumentType.OTHER, url="gold://x"
    )
    db.add(document)
    await db.flush()
    content = TenderDocumentContent(
        sha256="a" * 64, storage_key="k", file_size=0, mime_type="application/pdf"
    )
    db.add(content)
    await db.flush()
    file = TenderDocumentFile(
        tender_document_id=document.id,
        content_id=content.id,
        origin=DocumentFileOrigin.UPLOADED,
        download_status=DownloadStatus.DOWNLOADED,
    )
    db.add(file)
    await db.flush()
    ref = await source_refs.create_document_file_ref(
        db, tender_id=tender.id, file_id=file.id, location_label="cl. 1"
    )
    await tender_structure.link_source_ref(
        db, entity=blocks["b1"], source_ref_id=ref.id, provenance=PROV
    )
    await db.commit()
    assert await _count(db, TenderSourceRef) == 1
    assert await analysis.is_stale(db, a) is False
    # Canvi material del coneixement: stale, run nova, l'antiga queda intacta i no vigent.
    db.add(
        CompanyOperatingFact(
            company_id=company.id, key=OperatingFactKey.TELECOM_OPERATOR_CNMC, value=True
        )
        if False
        else CompanyService(
            company_id=company.id,
            name="Mobil",
            service_line_code="mobile_telephony",
            delivery_mode=DeliveryMode.RESALE,
        )
    )
    await db.commit()
    assert await analysis.is_stale(db, a) is True
    b = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(a)
    await db.refresh(b)
    assert b.id != a.id and b.is_current and not a.is_current
    assert (
        b.knowledge_fingerprint != a.knowledge_fingerprint
        and b.tender_structure_fingerprint == a.tender_structure_fingerprint
    )
    assert await _count(db, TenderFitAssessment, run_id=a.id) == len(reqs), (
        "la run antiga no es muta"
    )
    # Canvi material de l'estructura: stale una altra vegada.
    await tender_structure.create_site(
        db, tender, {"name": "Can Maset", "role": "remote"}, provenance=PROV
    )
    await db.commit()
    assert await analysis.is_stale(db, b) is True
    c = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    assert c.tender_structure_fingerprint != b.tender_structure_fingerprint
    assert await _count(db, TenderAnalysisRun) == 3
    assert await _count(db, TenderAnalysisRun, is_current=True) == 1


async def test_a_failure_while_persisting_leaves_nothing_behind(db, tenant_a, client_a):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)

    def boom(run):
        raise RuntimeError("persist failed")

    with pytest.raises(RuntimeError):
        await analysis.analyze_tender(
            db, company_id=company.id, tender_id=tender.id, persist_hook=boom
        )
    await db.commit()
    assert await _count(db, TenderAnalysisRun) == 0
    assert await _count(db, TenderFitAssessment) == 0 and await _count(db, AnalysisFinding) == 0
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    assert run.is_current and await _count(db, TenderAnalysisRun) == 1


async def test_an_invisible_tender_is_not_analysed(db, tenant_a, tenant_b, client_a):
    _, company_a = tenant_a
    _, company_b = tenant_b
    from app.core.errors import NotFoundError
    from app.models.tenders import Tender

    tender, *_ = await _synthetic_tender(db)
    private = Tender(
        source_id=tender.source_id,
        external_id="priv",
        title="Privada",
        owner_company_id=company_b.id,
    )
    db.add(private)
    await db.commit()
    with pytest.raises(NotFoundError):
        await analysis.analyze_tender(db, company_id=company_a.id, tender_id=private.id)


# --------------------------------------------------------------------------- #
# Model                                                                         #
# --------------------------------------------------------------------------- #


async def test_the_model_enforces_one_current_run_closed_enums_and_cascade(db, tenant_a, client_a):
    _, company = tenant_a
    tender, reqs, crits, items, _ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    # Una segona run vigent per la mateixa empresa i licitacio: no.
    await m._rejected_by_db(
        db,
        sa.insert(TenderAnalysisRun).values(
            id=uuid.uuid4(), company_id=company.id, tender_id=tender.id,
            tender_structure_fingerprint="x" * 64, knowledge_fingerprint="y" * 64,
            rules_version=RULES_VERSION, inputs_hash="z" * 64, is_current=True,
        ),
    )  # fmt: skip
    # La mateixa run (mateix inputs_hash) dues vegades: no.
    await m._rejected_by_db(
        db,
        sa.insert(TenderAnalysisRun).values(
            id=uuid.uuid4(), company_id=company.id, tender_id=tender.id,
            tender_structure_fingerprint="x" * 64, knowledge_fingerprint="y" * 64,
            rules_version=RULES_VERSION, inputs_hash=run.inputs_hash, is_current=False,
        ),
    )  # fmt: skip
    # Enums tancats (el CHECK, per SQL cru: l'ORM ja ho refusa abans) i `own_future`
    # nomes com a hipotesi.
    if db.bind.dialect.name == "postgresql":
        ids, params = (
            "CAST(:fid AS uuid), CAST(:rid AS uuid)",
            {"fid": str(uuid.uuid4()), "rid": str(run.id)},
        )
    else:
        ids, params = ":fid, :rid", {"fid": uuid.uuid4().hex, "rid": run.id.hex}
    await m._rejected_by_db(
        db,
        sa.text(
            "INSERT INTO analysis_findings (id, run_id, finding_type, rule_code, explanation_code) "
            f"VALUES ({ids}, 'blocker_confirmed', 'r', 'e')"
        ).bindparams(**params),
    )
    await m._rejected_by_db(
        db,
        sa.insert(DeliveryCandidate).values(
            id=uuid.uuid4(), run_id=run.id, scope_item_id=items["pbx"].id,
            candidate_type="own_future",
            origin="current_knowledge", readiness="available", territory_verdict="unknown",
            rule_code="r", explanation_code="e",
        ),
    )  # fmt: skip
    await m._rejected_by_db(
        db,
        sa.insert(CriterionAssessment).values(
            id=uuid.uuid4(), run_id=run.id, award_criterion_id=crits["p1"].id,
            criterion_result="achievable",
            knowledge_source="none", proof_status="unknown", points_status="secured", max_points=-1,
            secured_points=0, at_risk_points=0, lost_points=0, not_assessable_points=0,
            rule_code="r", explanation_code="e",
        ),
    )  # fmt: skip
    # Esborrar la run s'emporta tots els resultats.
    before = await _count(db, AnalysisQuestion)
    assert before > 0
    await db.execute(sa.delete(TenderAnalysisRun).where(TenderAnalysisRun.id == run.id))
    await db.commit()
    for model in (
        TenderFitAssessment,
        CriterionAssessment,
        DeliveryCandidate,
        AnalysisFinding,
        AnalysisQuestion,
    ):
        assert await _count(db, model) == 0, model.__tablename__


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def test_the_read_api_exposes_the_current_run_and_a_given_run(
    db, tenant_a, client_a, client_b
):
    _, company = tenant_a
    tender, reqs, crits, items, _ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    base = f"/api/v1/companies/{company.id}/tenders/{tender.id}/analysis"
    assert (await client_a.get(f"{base}/current")).status_code == 404
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    current = await client_a.get(f"{base}/current")
    assert current.status_code == 200, current.text
    body = current.json()
    assert body["id"] == str(run.id) and body["is_current"] and body["stale"] is False
    assert body["rules_version"] == RULES_VERSION and len(body["fit_assessments"]) == len(reqs)
    assert body["summary"]["blocker_potential_count"] == 1
    assert all(q["text"] and "{" not in q["text"] for q in body["questions"])
    assert set(body["summary"]) >= {"fit", "secured_points", "lost_points", "delivery", "questions"}
    assert "score" not in body["summary"] and "recommendation" not in body
    one = await client_a.get(f"{base}/runs/{run.id}")
    assert one.status_code == 200 and one.json()["id"] == str(run.id)
    assert (await client_a.get(f"{base}/runs/{uuid.uuid4()}")).status_code == 404
    # Una altra empresa no veu l'analisi d'aquesta.
    other = await client_b.get(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/analysis/current"
    )
    assert other.status_code in (403, 404)
    # Res no s'edita per aqui.
    assert (await client_a.post(f"{base}/current", json={})).status_code in (404, 405)
    assert (await client_a.patch(f"{base}/runs/{run.id}", json={})).status_code in (404, 405)


# --------------------------------------------------------------------------- #
# Semantic gate: compromisos automatics, timing dels bloqueigs, territori      #
# --------------------------------------------------------------------------- #


async def _scored_boolean_criterion(db, tender, reqs, crits, name="T2 hot desking"):
    """Un criteri automatic amb una sola necessitat booleana puntuada."""
    t2 = await m._criterion(db, tender, name=name, points=2.0)
    t2.run_id = crits["t1"].run_id
    t2.criterion_type = AwardCriterionType.IMPROVEMENT
    t2.evaluation_type = AwardEvaluationType.AUTOMATIC
    await db.flush()
    await scope.create_need(
        db,
        tender,
        reqs["pbx"],
        {
            "capability_code": "pbx.hot_desking", "unmapped_capability": None,
            "comparator": NeedComparator.PRESENT, "value_json": None, "value_schema": None,
            "need_type": NeedType.SCORED_IMPROVEMENT, "points": 2, "award_criterion_id": t2.id,
        },
        provenance=PROV,
    )  # fmt: skip
    await db.flush()
    return t2


async def test_scored_improvements_are_open_commitments_until_proved_or_contradicted(
    db, tenant_a, client_a
):
    _, company = tenant_a
    tender, reqs, crits, items, _ = await _synthetic_tender(db)
    t2 = await _scored_boolean_criterion(db, tender, reqs, crits)
    rows = await _twin(db, company, client_a)

    # A. Sense dada: at_risk, mai lost, mai not_assessable (aixo es per a preu i judici).
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run)
    out = _by_criterion(run, t2)
    assert (out.points_status, out.at_risk_points, out.lost_points, out.not_assessable_points) == (
        "at_risk", 2.0, 0.0, 0.0,
    )  # fmt: skip
    assert out.criterion_result == "needs_review"
    unstructured = [
        c for c in run.criterion_assessments if c.explanation_code == X.CRITERION_OPEN_COMMITMENT
    ]
    assert unstructured == [], "al plec sintetic tots els automatics tenen estructura"

    # A. Declarat: at_risk.
    cap = SolutionCapability(
        company_id=company.id, solution_id=rows["solution"].id,
        capability_code="pbx.hot_desking", status=CapabilityStatus.SUPPORTED,
    )  # fmt: skip
    db.add(cap)
    await db.commit()
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run)
    out = _by_criterion(run, t2)
    assert (out.points_status, out.at_risk_points, out.knowledge_source) == (
        "at_risk",
        2.0,
        "current_declared",
    )

    # A. Verificat amb evidencia: secured.
    await _link_evidence(client_a, company, "solution_capability", cap.id)
    cap = await db.get(SolutionCapability, cap.id)
    cap.verification_status = VerificationStatus.VERIFIED
    await db.commit()
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run)
    out = _by_criterion(run, t2)
    assert (out.points_status, out.secured_points, out.criterion_result, out.knowledge_source) == (
        "secured", 2.0, "achievable", "current_verified",
    )  # fmt: skip

    # A. Incompatibilitat actual explicita (la solucio declara que NO ho fa): lost.
    cap = await db.get(SolutionCapability, cap.id)
    cap.status = CapabilityStatus.NOT_SUPPORTED
    cap.verification_status = VerificationStatus.PENDING_VERIFICATION
    await db.commit()
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run)
    out = _by_criterion(run, t2)
    assert (out.points_status, out.lost_points, out.criterion_result) == (
        "lost",
        2.0,
        "not_achievable",
    )
    for c in run.criterion_assessments:
        total = c.secured_points + c.at_risk_points + c.lost_points + c.not_assessable_points
        assert abs(total - c.max_points) < 1e-6


async def test_a_lower_current_tier_does_not_lose_the_upper_tier(db, tenant_a, client_a):
    _, company = tenant_a
    tender, reqs, crits, items, _ = await _synthetic_tender(db)
    rows = await _twin(db, company, client_a)
    db.add(SolutionCapability(
        company_id=company.id, solution_id=rows["solution"].id,
        capability_code="pbx.concurrent_calls", status=CapabilityStatus.SUPPORTED,
        value_number=Decimal("40"),
    ))  # fmt: skip
    await db.commit()
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run)
    t1 = _by_criterion(run, crits["t1"])
    # B. 40 canals avui: el tram de 50 no es perd (cap limit maxim explicit), queda en risc.
    assert (round(t1.at_risk_points, 2), t1.lost_points, t1.secured_points) == (2.0, 0.0, 0.0)
    assert t1.support_json["needs"]
    fifty = [n for n in run.need_assessments if n.explanation_code == X.CAPABILITY_BELOW_REQUIRED]
    assert len(fifty) == 1, (
        "la necessitat del tram 50 diu que avui no s'hi arriba, pero no perd res"
    )


# --------------------------------------------------------------------------- #
# Causa real dels punts en risc d'un criteri (v3, C-E1)                          #
# --------------------------------------------------------------------------- #


def _pure_need(nid: str, threshold: int | None = None, capability: str = "pbx.concurrent_calls"):
    from app.analysis.fingerprint import NeedView

    return NeedView(
        id=nid, capability_code=capability, unmapped_capability=None, comparator="gte",
        value_json={"channels": threshold} if threshold is not None else None,
        value_schema="v1", need_type="scored_improvement", points=None, criterion_key="k-c",
    )  # fmt: skip


def _pure_out(nid: str, fit: str, source: str, explanation: str = X.CAPABILITY_SUPPORTED):
    from app.analysis import engine as pure

    return pure.NeedAssessmentOut(
        need_id=nid, requirement_id="r", fit_result=fit, knowledge_source=source,
        verification_status="not_verified", rule_code="need_capability_v1",
        explanation_code=explanation,
    )  # fmt: skip


def _criterion_findings_for(pairs, tranches=None, requirements=(), fits_by_key=None, timing=None):
    """Valora un criteri automatic pur i en retorna `(assessment, troballes)`."""
    from app.analysis import engine as pure
    from app.analysis.fingerprint import CriterionView, TenderStructureView

    criterion = CriterionView(
        id="c", key="k-c", name="C", points=2.0, criterion_type="improvement",
        evaluation_type="automatic", timing=timing or "at_submission", tranches=tranches,
    )  # fmt: skip
    tender = TenderStructureView(
        tender_id="t", requirements=tuple(requirements), criteria=(criterion,)
    )
    needs = {"k-c": list(pairs)} if pairs else {}
    out, _ = pure.assess_criterion(criterion, tender, needs, fits_by_key or {})
    findings = pure.criterion_findings(criterion, out, needs.get("k-c", []))
    # Invariant: «dades declarades» nomes si CADA necessitat de la causa (o el requisit
    # enllacat) compleix amb dada declarada, i sempre amb almenys una causa.
    declared = ("satisfies", "current_declared")
    outs = {o.need_id: o for _, o in pairs}
    linked = {fit.requirement_id: fit for _, fit in (fits_by_key or {}).values()}
    for f in findings:
        if f.explanation_code != X.DECLARED_NOT_VERIFIED:
            continue
        assert f.details.get("need_ids") or f.details.get("requirement_id")
        for need_id in f.details.get("need_ids", []):
            assert (outs[need_id].fit_result, outs[need_id].knowledge_source) == declared
        if f.details.get("requirement_id"):
            fit = linked[f.details["requirement_id"]]
            assert (fit.fit_result, fit.knowledge_source) == declared
    # Invariant (I1): «no consta cap dada actual» mai amb una necessitat de font actual.
    for f in findings:
        if f.explanation_code == X.CRITERION_NEEDS_UNKNOWN:
            for need_id in f.details.get("need_ids", []):
                assert outs[need_id].knowledge_source not in CURRENT_SOURCES, need_id
    return out, findings


CURRENT_SOURCES = ("current_declared", "current_verified")


def _kinds(findings):
    return [(f.finding_type, f.explanation_code, f.details.get("need_ids")) for f in findings]


async def test_an_unknown_need_puts_points_at_risk_for_missing_information():
    out, findings = _criterion_findings_for(
        [(_pure_need("n1"), _pure_out("n1", "unknown", "none"))]
    )
    assert (out.points_status, out.at_risk_points) == ("at_risk", 2.0)
    assert _kinds(findings) == [("missing_information", X.CRITERION_NEEDS_UNKNOWN, ["n1"])]


async def test_a_need_whose_value_cannot_be_read_is_not_missing_company_data():
    """`NEED_VALUE_NOT_COMPARABLE`: el motor no ha llegit cap dada de l'empresa (el valor del
    plec no es comparable), aixi que no es «no consta cap dada actual»: es revisio, amb la
    seva propia explicacio. Una necessitat sense codi continua sent «no consta»."""
    out, findings = _criterion_findings_for(
        [
            (
                _pure_need("n1", capability="pbx.queues"),
                _pure_out("n1", "unknown", "none", X.NEED_VALUE_NOT_COMPARABLE),
            ),
            (
                _pure_need("n2", capability="pbx.hot_desking"),
                _pure_out("n2", "unknown", "none", X.CAPABILITY_UNMAPPED),
            ),
        ]
    )
    assert out.at_risk_points == 2.0
    assert sorted(_kinds(findings)) == [
        ("missing_information", X.CRITERION_NEEDS_UNKNOWN, ["n2"]),
        ("review_required", X.NEED_VALUE_NOT_COMPARABLE, ["n1"]),
    ]


def _pure_fact_need(nid: str, count: int = 10):
    from app.analysis.fingerprint import NeedView

    return NeedView(
        id=nid, capability_code=None, unmapped_capability=None, comparator="gte",
        value_json={"operating_fact": "ipv4_assignable_count", "count": count},
        value_schema="v1", need_type="scored_improvement", points=None, criterion_key="k-c",
    )  # fmt: skip


@pytest.mark.parametrize("source", CURRENT_SOURCES)
async def test_a_current_capability_without_a_comparable_value_is_review_not_missing(source):
    """I1: la capacitat consta amb dada actual (declarada o verificada) pero sense nombre o
    nivell. Hi ha dada actual, aixi que no es «no consta cap dada actual»: es revisio, amb
    l'explicacio propia de la necessitat."""
    out, findings = _criterion_findings_for(
        [(_pure_need("n1"), _pure_out("n1", "unknown", source, X.CAPABILITY_UNKNOWN))]
    )
    assert (out.points_status, out.at_risk_points) == ("at_risk", 2.0)
    assert _kinds(findings) == [("review_required", X.CAPABILITY_UNKNOWN, ["n1"])]
    assert "missing_from" not in findings[0].details


@pytest.mark.parametrize("source", CURRENT_SOURCES)
async def test_a_current_numeric_fact_without_a_number_is_not_comparable(source):
    """I1: el fet quantitatiu consta amb dada actual pero sense nombre: no es pot comparar
    (codi nomes v3), mai «no consta cap dada actual»."""
    out, findings = _criterion_findings_for(
        [(_pure_fact_need("n1"), _pure_out("n1", "unknown", source, X.FACT_UNKNOWN))]
    )
    assert (out.points_status, out.at_risk_points) == ("at_risk", 2.0)
    assert _kinds(findings) == [("review_required", X.CRITERION_NEEDS_NOT_COMPARABLE, ["n1"])]
    assert "missing_from" not in findings[0].details


async def test_without_current_data_an_unknown_need_is_still_missing_information():
    """Sense cap dada actual (cap coneixement o nomes historic), la causa continua sent
    «no consta cap dada actual», tant per a capacitats com per a fets quantitatius."""
    out, findings = _criterion_findings_for(
        [
            (_pure_need("n1"), _pure_out("n1", "unknown", "none", X.CAPABILITY_UNKNOWN)),
            (
                _pure_need("n2", capability="pbx.queues"),
                _pure_out("n2", "unknown", "historical", X.CAPABILITY_HISTORIC_ONLY),
            ),
            (_pure_fact_need("n3"), _pure_out("n3", "unknown", "none", X.FACT_UNKNOWN)),
            (_pure_fact_need("n4"), _pure_out("n4", "unknown", "historical", X.FACT_UNKNOWN)),
        ]
    )
    assert out.at_risk_points == 2.0
    assert _kinds(findings) == [
        ("missing_information", X.CRITERION_NEEDS_UNKNOWN, ["n1", "n2", "n3", "n4"])
    ]


def _snapshot_company(capabilities=None, facts=None):
    from app.analysis import engine as pure
    from app.knowledge.snapshot import KnowledgeSnapshot

    snapshot = KnowledgeSnapshot(
        company_id="e", evaluated_on=date(2026, 1, 1), certifications={},
        facts=facts or {}, services={}, solutions={}, capabilities=capabilities or {},
        products={}, partners={}, pending_validation=(), fingerprint="x",
    )  # fmt: skip
    return pure.CompanyView(company_id="e", snapshot=snapshot)


def _current_claim(record_id: str, value, number=None):
    from app.knowledge.snapshot import Claim, KnowledgeSource

    return Claim(
        record_id=record_id, source=KnowledgeSource.CURRENT_DECLARED, value=value,
        as_of=date(2026, 1, 1), valid_until=None, number=number,
    )  # fmt: skip


async def test_current_data_without_a_value_reaches_the_criterion_as_review():
    """De punta a punta (avaluadors reals del coneixement): una capacitat declarada sense
    nombre i un fet afirmat sense nombre donen necessitats `unknown` de font actual, i el
    criteri ho diu com a revisio, no com a «no consta cap dada actual»."""
    from app.analysis import engine as pure
    from app.analysis.fingerprint import RequirementView
    from app.knowledge.snapshot import Entry

    requirement = RequirementView(
        id="r", key="k-r", title="R", category="technical_requirement",
        obligation="optional", timing="at_execution",
    )  # fmt: skip
    capability = _current_claim(
        "cap", {"solution": "S", "status": "supported", "number": None, "level": None}
    )
    fact = _current_claim("fact", True, number=None)
    company = _snapshot_company(
        capabilities={"pbx.concurrent_calls": Entry("pbx.concurrent_calls", (capability,), ())},
        facts={"ipv4_assignable_count": Entry("ipv4_assignable_count", (fact,), ())},
    )
    cap_need, fact_need = _pure_need("n1", 30), _pure_fact_need("n2")
    cap_out = pure.assess_need(cap_need, requirement, company)
    fact_out = pure.assess_need(fact_need, requirement, company)
    assert (cap_out.fit_result, cap_out.knowledge_source, cap_out.explanation_code) == (
        "unknown",
        "current_declared",
        X.CAPABILITY_UNKNOWN,
    )
    assert (fact_out.fit_result, fact_out.knowledge_source, fact_out.explanation_code) == (
        "unknown",
        "current_declared",
        X.FACT_UNKNOWN,
    )
    _, findings = _criterion_findings_for([(cap_need, cap_out), (fact_need, fact_out)])
    assert sorted(_kinds(findings)) == [
        ("review_required", X.CAPABILITY_UNKNOWN, ["n1"]),
        ("review_required", X.CRITERION_NEEDS_NOT_COMPARABLE, ["n2"]),
    ]


async def test_a_declared_need_puts_points_at_risk_for_review():
    out, findings = _criterion_findings_for(
        [(_pure_need("n1"), _pure_out("n1", "satisfies", "current_declared"))]
    )
    assert (out.points_status, out.at_risk_points) == ("at_risk", 2.0)
    assert _kinds(findings) == [("review_required", X.DECLARED_NOT_VERIFIED, ["n1"])]


async def test_a_declared_and_an_unknown_need_give_one_finding_per_cause():
    out, findings = _criterion_findings_for(
        [
            (
                _pure_need("n1", capability="pbx.queues"),
                _pure_out("n1", "satisfies", "current_declared"),
            ),
            (_pure_need("n2", capability="pbx.hot_desking"), _pure_out("n2", "unknown", "none")),
            (_pure_need("n3", capability="pbx.cdr_export"), _pure_out("n3", "unknown", "none")),
        ]
    )
    assert out.at_risk_points == 2.0
    assert sorted(_kinds(findings)) == [
        ("missing_information", X.CRITERION_NEEDS_UNKNOWN, ["n2", "n3"]),
        ("review_required", X.DECLARED_NOT_VERIFIED, ["n1"]),
    ]
    assert all(f.criterion_id == "c" and f.requirement_id is None for f in findings)


async def test_a_verified_need_gives_no_review_finding():
    out, findings = _criterion_findings_for(
        [(_pure_need("n1"), _pure_out("n1", "satisfies", "current_verified"))]
    )
    assert (out.points_status, out.secured_points) == ("secured", 2.0)
    assert findings == []


async def test_only_the_tranches_left_at_risk_name_their_cause():
    """Trams: el tram verificat no es causa de res; el declarat i el desconegut, si."""
    tranches = [{"min": 30, "points": 0.5}, {"min": 40, "points": 1.0}, {"min": 50, "points": 2}]
    out, findings = _criterion_findings_for(
        [
            (_pure_need("n30", 30), _pure_out("n30", "satisfies", "current_verified")),
            (_pure_need("n40", 40), _pure_out("n40", "satisfies", "current_declared")),
            (_pure_need("n50", 50), _pure_out("n50", "unknown", "none")),
        ],
        tranches=tranches,
    )
    assert (out.secured_points, out.at_risk_points, out.points_status) == (0.5, 1.5, "mixed")
    assert sorted(_kinds(findings)) == [
        ("missing_information", X.CRITERION_NEEDS_UNKNOWN, ["n50"]),
        ("review_required", X.DECLARED_NOT_VERIFIED, ["n40"]),
    ]


async def test_an_unstructured_or_linked_criterion_names_its_own_cause():
    from app.analysis import engine as pure
    from app.analysis.fingerprint import RequirementView

    # Sense cap estructura: compromis obert, mai «dades declarades».
    _, findings = _criterion_findings_for([])
    assert _kinds(findings) == [("review_required", X.CRITERION_OPEN_COMMITMENT, None)]

    def linked(fit: str, source: str, timing: str = "at_submission"):
        requirement = RequirementView(
            id="r", key="k-r", title="R", category="technical_requirement",
            obligation="optional", timing="at_execution", criterion_keys=("k-c",),
        )  # fmt: skip
        fit_out = pure.FitAssessmentOut(
            requirement_id="r", fit_result=fit, knowledge_source=source,
            proof_status="not_required", territory_verdict="not_applicable",
            verification_status="not_verified", rule_code="r",
            explanation_code=X.FACT_TRUE_DECLARED,
        )  # fmt: skip
        return _criterion_findings_for(
            [], requirements=[requirement], fits_by_key={"k-r": (requirement, fit_out)},
            timing=timing,
        )  # fmt: skip

    out, findings = linked("satisfies", "current_declared")
    assert out.at_risk_points == 2.0
    assert [
        (f.finding_type, f.explanation_code, f.details.get("requirement_id")) for f in findings
    ] == [("review_required", X.DECLARED_NOT_VERIFIED, "r")]
    out, findings = linked("does_not_satisfy", "current_declared", timing="ambiguous")
    assert out.explanation_code == X.CRITERION_TIMING_NOT_CONCLUSIVE
    assert [(f.finding_type, f.explanation_code) for f in findings] == [
        ("review_required", X.CRITERION_TIMING_NOT_CONCLUSIVE)
    ]
    _, findings = linked("satisfies", "current_verified")
    assert findings == []


# --------------------------------------------------------------------------- #
# D'on falta la dada d'un `missing_information` (v3, C-E2)                         #
# --------------------------------------------------------------------------- #

COMPANY, TENDER = "company", "tender"
#: Auditoria dels productors de `missing_information` al finding generic: per a cada
#: explicacio del fit, d'on falta la dada (`details.missing_from`), o `None` (cap camp) si
#: no es pot demostrar a partir de la regla. Amb `company` el finding es `DATA_MISSING`
#: («falta una dada de l'empresa»); sense, `DATA_MISSING_UNATTRIBUTED`. Els codis del plec
#: son del vocabulari (`_IN_VOCABULARY`); fora del vocabulari, vegeu el test seguent.
DATA_MISSING_FROM = [
    (R.FACT_REQUIREMENT, X.FACT_UNKNOWN, COMPANY),
    (R.FACT_REQUIREMENT, X.FACT_HISTORIC_ONLY, COMPANY),
    (R.FACT_REQUIREMENT, X.QUALIFICATION_THRESHOLD_NOT_COMPARABLE, None),
    (R.FACT_REQUIREMENT, X.PROOF_MISSING_FOR_CLAIM, COMPANY),
    (R.CERTIFICATION_REQUIREMENT, X.CERT_UNKNOWN, COMPANY),
    (R.CERTIFICATION_REQUIREMENT, X.CERT_HISTORIC_ONLY, COMPANY),
    (R.CERTIFICATION_REQUIREMENT, X.CERT_STANDARD_NOT_STRUCTURED, None),
    (R.CERTIFICATION_REQUIREMENT, X.PROOF_MISSING_FOR_CLAIM, COMPANY),
    (R.PROFILE_DATA_REQUIREMENT, X.PROFILE_DATA_NOT_IN_SNAPSHOT, None),
    (R.CAPABILITY_NEEDS_REQUIREMENT, X.NEEDS_UNKNOWN, None),
    (R.CAPABILITY_NEEDS_REQUIREMENT, X.NEEDS_NONE_MINIMUM, None),
    (R.CAPABILITY_NEEDS_REQUIREMENT, X.PROOF_MISSING_FOR_CLAIM, COMPANY),
    (R.TURNOVER_REQUIREMENT, X.TURNOVER_UNKNOWN, COMPANY),
    (R.TURNOVER_REQUIREMENT, X.QUALIFICATION_THRESHOLD_NOT_COMPARABLE, None),
    (R.TURNOVER_REQUIREMENT, X.PROOF_MISSING_FOR_CLAIM, COMPANY),
    (R.EXECUTION_REQUIREMENT, X.EXECUTION_UNKNOWN, COMPANY),
    (R.EXECUTION_REQUIREMENT, X.QUALIFICATION_THRESHOLD_NOT_COMPARABLE, None),
    (R.EXECUTION_REQUIREMENT, X.PROOF_MISSING_FOR_CLAIM, COMPANY),
    (R.INSURANCE_REQUIREMENT, X.INSURANCE_UNKNOWN, COMPANY),
    (R.INSURANCE_REQUIREMENT, X.INSURANCE_EXPIRED, COMPANY),
    (R.INSURANCE_REQUIREMENT, X.QUALIFICATION_THRESHOLD_NOT_COMPARABLE, None),
    (R.INSURANCE_REQUIREMENT, X.PROOF_MISSING_FOR_CLAIM, COMPANY),
    (R.REGISTRATION_REQUIREMENT, X.REGISTRATION_UNKNOWN, COMPANY),
    (R.REGISTRATION_REQUIREMENT, X.REGISTRATION_SCOPE_UNKNOWN, COMPANY),
    (R.REGISTRATION_REQUIREMENT, X.REGISTRATION_SCOPE_NOT_COVERED, COMPANY),
    (R.REGISTRATION_REQUIREMENT, X.REGISTRATION_ONLY_OPERATOR_FACT, COMPANY),
    (R.REGISTRATION_REQUIREMENT, X.PROOF_MISSING_FOR_CLAIM, COMPANY),
    (R.RESOURCES_REQUIREMENT, X.PROFILES_MISSING, COMPANY),
    (R.RESOURCES_REQUIREMENT, X.EQUIPMENT_MISSING, COMPANY),
    (R.RESOURCES_REQUIREMENT, X.EQUIPMENT_CALIBRATION_UNKNOWN, COMPANY),
    (R.RESOURCES_REQUIREMENT, X.QUALIFICATION_THRESHOLD_NOT_COMPARABLE, None),
    (R.RESOURCES_REQUIREMENT, X.PROOF_MISSING_FOR_CLAIM, COMPANY),
]


#: Un `value_json` amb tots els codis del plec dins del vocabulari.
_IN_VOCABULARY = {
    "operating_fact": "support_24x7",
    "standard": "ISO_9001",
    "registration_type": "sector_operator_registry",
    "insurance_type": "civil_liability",
    "insurance_limits_eur": {"per_claim": "300000"},
    "required_profiles": {"project_manager": 1},
    "required_equipment": {"otdr": 1},
    "service_lines": ["dedicated_network"],
    "client_types": ["public"],
}


def _pure_requirement_findings(
    rule: str,
    explanation: str,
    fit: str = "unknown",
    proof: str = "not_required",
    territory: str = "not_applicable",
    value_json: dict | None = None,
    support: dict | None = None,
):
    from app.analysis import engine as pure
    from app.analysis.fingerprint import RequirementView

    requirement = RequirementView(
        id="r", key="k-r", title="Volum de negoci", category="economic_solvency",
        obligation="mandatory", timing="at_submission",
        value_json=_IN_VOCABULARY if value_json is None else value_json,
    )  # fmt: skip
    fit_out = pure.FitAssessmentOut(
        requirement_id="r", fit_result=fit, knowledge_source="none", proof_status=proof,
        territory_verdict=territory, verification_status="unknown", rule_code=rule,
        explanation_code=explanation, support=support or {},
    )  # fmt: skip
    return pure.requirement_findings(requirement, fit_out, [], None)


def _missing(findings):
    return [
        (f.explanation_code, f.details.get("missing_from", "<cap>"))
        for f in findings
        if f.finding_type == "missing_information"
    ]


@pytest.mark.parametrize(("rule", "explanation", "expected"), DATA_MISSING_FROM)
async def test_data_missing_says_where_the_data_is_missing_only_when_it_can_show_it(
    rule, explanation, expected
):
    findings, _ = _pure_requirement_findings(rule, explanation)
    if expected == COMPANY:
        assert _missing(findings) == [(X.DATA_MISSING, COMPANY)]
    else:
        assert _missing(findings) == [(X.DATA_MISSING_UNATTRIBUTED, "<cap>")]


@pytest.mark.parametrize(
    ("rule", "explanation", "outside"),
    [
        (R.FACT_REQUIREMENT, X.FACT_UNKNOWN, {"operating_fact": "fax_line"}),
        (R.REGISTRATION_REQUIREMENT, X.REGISTRATION_UNKNOWN, {"registration_type": "guild"}),
        (R.REGISTRATION_REQUIREMENT, X.REGISTRATION_UNKNOWN, {"registration_type": "other"}),
        (R.INSURANCE_REQUIREMENT, X.INSURANCE_UNKNOWN, {"insurance_type": "cyber_risk"}),
        (
            R.INSURANCE_REQUIREMENT,
            X.INSURANCE_UNKNOWN,
            {"insurance_limits_eur": {"per_claim": "1", "per_hacker": "1"}},
        ),
        (R.RESOURCES_REQUIREMENT, X.PROFILES_MISSING, {"required_profiles": {"astronaut": 1}}),
        (R.RESOURCES_REQUIREMENT, X.EQUIPMENT_MISSING, {"required_equipment": ["telescope"]}),
        (R.EXECUTION_REQUIREMENT, X.EXECUTION_UNKNOWN, {"service_lines": ["teleportation"]}),
        (R.EXECUTION_REQUIREMENT, X.EXECUTION_UNKNOWN, {"client_types": ["galactic"]}),
    ],
)
async def test_a_tender_code_outside_the_vocabulary_is_not_missing_company_data(
    rule, explanation, outside
):
    """L'empresa nomes pot declarar codis del vocabulari: si el plec en demana un altre (o
    `other`, que no identifica res), no es pot demostrar que la dada falti a l'empresa."""
    findings, _ = _pure_requirement_findings(
        rule, explanation, value_json={**_IN_VOCABULARY, **outside}
    )
    assert _missing(findings) == [(X.DATA_MISSING_UNATTRIBUTED, "<cap>")]


async def test_the_audit_table_matches_the_engine_and_covers_every_producer():
    """Cada productor de `missing_information` del motor es a l'auditoria: si se n'afegeix
    un, aquest test obliga a decidir (i fixar) el seu `missing_from`."""
    import inspect

    from app.analysis import engine as pure

    assert pure.DATA_MISSING_FROM_COMPANY == frozenset(
        x for _, x, where in DATA_MISSING_FROM if where == COMPANY
    )
    # DATA_MISSING, TENDER_REFERENCE_DATE_MISSING, TERRITORY_LIST_UNAVAILABLE (requisit),
    # CRITERION_NEEDS_UNKNOWN (criteri) i PACKAGE_GAP (forat del paquet, 3B-2 §4.4).
    assert inspect.getsource(pure).count("FindingType.MISSING_INFORMATION") == 5
    from app.analysis.fingerprint import PackageGapView, TenderStructureView

    gapped = TenderStructureView(
        tender_id="t", gaps=(PackageGapView(code="no_text", detail={"key": "d"}),)
    )
    assert _missing(pure.package_findings(gapped)) == [(X.PACKAGE_GAP, TENDER)]
    # El codi sense atribucio es nomes de la v3: la taula v2 no el te.
    assert X.DATA_MISSING_UNATTRIBUTED not in registry.EXPLANATIONS_BY_VERSION[RULES_VERSION_V2]
    findings, questions = _pure_requirement_findings(
        R.FACT_REQUIREMENT, X.FACT_TRUE_VERIFIED, fit="satisfies", territory="unknown"
    )
    assert _missing(findings) == [(X.TERRITORY_LIST_UNAVAILABLE, TENDER)]
    _, findings = _criterion_findings_for(
        [(_pure_need("n1"), _pure_out("n1", "unknown", "none", X.CAPABILITY_UNKNOWN))]
    )
    # Una necessitat `unknown` pot ser una capacitat sense codi o un valor que el vocabulari
    # no recull: no es pot demostrar que falti a l'empresa.
    assert _missing(findings) == [(X.CRITERION_NEEDS_UNKNOWN, "<cap>")]
    # I1: un fet quantitatiu actual sense nombre no es `missing_information` sino revisio,
    # sense `missing_from` (la dada hi es, pero no es pot comparar). Codi nomes v3.
    _, findings = _criterion_findings_for(
        [(_pure_fact_need("n1"), _pure_out("n1", "unknown", "current_declared", X.FACT_UNKNOWN))]
    )
    assert _missing(findings) == []
    assert [(f.finding_type, f.explanation_code, f.details.get("missing_from", "<cap>"))
            for f in findings] == [
        ("review_required", X.CRITERION_NEEDS_NOT_COMPARABLE, "<cap>")
    ]  # fmt: skip
    assert (
        X.CRITERION_NEEDS_NOT_COMPARABLE not in registry.EXPLANATIONS_BY_VERSION[RULES_VERSION_V2]
    )


@pytest.mark.parametrize(
    ("rule", "explanation"),
    [(R.TURNOVER_REQUIREMENT, X.TURNOVER_UNKNOWN), (R.EXECUTION_REQUIREMENT, X.EXECUTION_UNKNOWN)],
)
async def test_no_company_data_and_no_deadline_asks_both_the_company_and_the_buyer(
    rule, explanation
):
    """Sense cap dada de l'empresa el motor no arriba a mirar el termini: falten totes dues
    coses, i es diu (i es pregunta) cadascuna on toca."""
    findings, questions = _pure_requirement_findings(
        rule, explanation, support={"reference_date": None}
    )
    assert sorted(_missing(findings)) == [
        (X.DATA_MISSING, COMPANY),
        (X.TENDER_REFERENCE_DATE_MISSING, TENDER),
    ]
    assert sorted((q.audience, q.question_code) for q in questions) == [
        ("buyer", Q.BUYER_PROVIDE_SUBMISSION_DEADLINE),
        ("company", Q.COMPANY_PROVIDE_PROFILE_DATA),
    ]
    # Amb termini, nomes l'empresa.
    findings, questions = _pure_requirement_findings(
        rule, explanation, support={"reference_date": "2026-03-01"}
    )
    assert _missing(findings) == [(X.DATA_MISSING, COMPANY)]
    assert [q.question_code for q in questions] == [Q.COMPANY_PROVIDE_PROFILE_DATA]


async def test_without_a_window_only_the_record_proofs_become_unknown():
    """`window_unknown`: les proves que depenen de les files usades (exercicis, projectes...)
    no es poden avaluar; un certificat de norma s'avalua igual que abans."""
    from app.analysis import engine as pure
    from app.analysis.fingerprint import RequirementView
    from app.knowledge.snapshot import Claim, Entry, KnowledgeSnapshot, KnowledgeSource

    def claim(record_id: str, evidence: bool) -> Claim:
        return Claim(
            record_id=record_id, source=KnowledgeSource.CURRENT_DECLARED, value={},
            as_of=date(2026, 1, 1), valid_until=None, has_evidence=evidence,
        )  # fmt: skip

    def company(cert_evidence: bool):
        snapshot = KnowledgeSnapshot(
            company_id="e", evaluated_on=date(2026, 1, 1),
            certifications={"ISO_9001": Entry("ISO_9001", (claim("c", cert_evidence),), ())},
            facts={}, services={}, solutions={}, capabilities={}, products={}, partners={},
            pending_validation=(), fingerprint="x",
            financial_years={"2025": Entry("2025", (claim("y", False),), ())},
        )  # fmt: skip
        return pure.CompanyView(company_id="e", snapshot=snapshot)

    requirement = RequirementView(
        id="r", key="k-r", title="Volum", category="economic_solvency",
        obligation="mandatory", timing="at_submission",
        value_json={"turnover_min_eur": "1", "standard": "ISO_9001"},
        proof_json=[{"kind": "annual_accounts"}, {"kind": "certificate"}],
    )  # fmt: skip

    def statuses(cert_evidence: bool, window_unknown: bool):
        overall, support = pure.assess_proof(
            requirement, company(cert_evidence), ("y",), window_unknown=window_unknown
        )
        return overall, [(i["kind"], i["status"]) for i in support["items"]]

    # Amb finestra: l'exercici usat no te evidencia -> missing.
    assert statuses(False, False) == (
        "missing",
        [("annual_accounts", "missing"), ("certificate", "missing")],
    )
    # Sense finestra: els comptes, unknown; el certificat s'avalua igual que abans.
    assert statuses(False, True) == (
        "missing",
        [("annual_accounts", "unknown"), ("certificate", "missing")],
    )
    # M4: sense finestra, una prova `unknown` i la resta `sufficient` no es «falta la
    # prova»: cap prova no consta com a absent, aixi que el conjunt es `unknown`...
    assert statuses(True, True) == (
        "unknown",
        [("annual_accounts", "unknown"), ("certificate", "sufficient")],
    )
    # ...i el requisit no porta cap `evidence_gap` ni cap pregunta de prova.
    overall, proof = pure.assess_proof(requirement, company(True), ("y",), window_unknown=True)
    fit_out = pure.FitAssessmentOut(
        requirement_id="r", fit_result="unknown", knowledge_source="none", proof_status=overall,
        territory_verdict="not_applicable", verification_status="unknown",
        rule_code=R.TURNOVER_REQUIREMENT, explanation_code=X.QUALIFICATION_REFERENCE_DATE_MISSING,
        support={"proof": proof, "reference_date": None},
    )  # fmt: skip
    findings, questions = pure.requirement_findings(requirement, fit_out, [], None)
    assert [f.finding_type for f in findings if f.finding_type == "evidence_gap"] == []
    assert [q for q in questions if q.question_code == Q.COMPANY_PROVIDE_PROOF] == []
    # Fora del cami sense finestra, l'agregacio general no canvia (fora d'abast de M4):
    # `sufficient` + una prova no avaluable (`unknown`) continua `partial`.
    other = RequirementView(
        id="r", key="k-r", title="Volum", category="economic_solvency",
        obligation="mandatory", timing="at_submission",
        value_json={"standard": "ISO_9001"},
        proof_json=[{"kind": "certificate"}, {"kind": "technical_sheet"}],
    )  # fmt: skip
    assert pure.assess_proof(other, company(True), ())[0] == "partial"


@pytest.mark.parametrize("rule", [R.TURNOVER_REQUIREMENT, R.EXECUTION_REQUIREMENT])
async def test_without_a_reference_date_the_buyer_is_asked_for_the_deadline(rule):
    findings, questions = _pure_requirement_findings(
        rule, X.QUALIFICATION_REFERENCE_DATE_MISSING, proof="unknown"
    )
    assert [(f.finding_type, f.explanation_code, f.details) for f in findings] == [
        (
            "missing_information",
            X.TENDER_REFERENCE_DATE_MISSING,
            {
                "fit": "unknown",
                "explanation": X.QUALIFICATION_REFERENCE_DATE_MISSING,
                "category": "economic_solvency",
                "missing_from": TENDER,
            },
        )
    ]
    assert [(q.audience, q.question_code, q.rule_code, q.params) for q in questions] == [
        (
            "buyer",
            Q.BUYER_PROVIDE_SUBMISSION_DEADLINE,
            R.MISSING_INFORMATION,
            {"title": "Volum de negoci"},
        )
    ]


@pytest.mark.parametrize(
    ("timing", "finding", "asks_buyer"),
    [
        (RequirementTiming.AT_SUBMISSION, "blocker_potential", False),
        (RequirementTiming.BEFORE_FORMALIZATION, "review_required", False),
        (RequirementTiming.AT_EXECUTION, "review_required", False),
        (RequirementTiming.AMBIGUOUS, "review_required", True),
        (RequirementTiming.UNKNOWN, "review_required", True),
    ],
)
async def test_a_contradicted_mandatory_requirement_blocks_only_at_submission(
    db, tenant_a, client_a, timing, finding, asks_buyer
):
    _, company = tenant_a
    tender, reqs, crits, items, _ = await _synthetic_tender(db)
    reqs["fact_false"].timing = timing
    await db.flush()
    await _twin(db, company, client_a)
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run)
    fit = _by_requirement(run, reqs["fact_false"])
    assert fit.fit_result == "does_not_satisfy", "la incompatibilitat continua visible"
    mine = [f for f in run.findings if f.requirement_id == reqs["fact_false"].id]
    assert {f.finding_type.value for f in mine} == {finding}
    assert not any(f.finding_type == "blocker_confirmed" for f in run.findings)
    if finding == "review_required":
        assert mine[0].explanation_code == X.MANDATORY_DOES_NOT_SATISFY_LATER
        assert mine[0].details_json["timing"] == timing.value
    asked = any(
        q.question_code == Q.BUYER_CLARIFY_REQUIREMENT_TIMING
        and q.requirement_id == reqs["fact_false"].id
        for q in run.questions
    )
    assert asked is asks_buyer


async def test_a_block_territory_does_not_contaminate_other_scope_items(db, tenant_a, client_a):
    """B1 te FTTH (territori unknown: falta l'annex) i radioenllac. El radioenllac no
    hereta aquell unknown com si fos seu: sense correspondencia demostrable, cap area."""
    _, company = tenant_a
    tender = await m._public_tender(db, title="Bloc amb elements independents")
    b1 = await m._block(db, tender, "B1")
    ftth_req = await _requirement(
        db, tender, "15 accessos FTTH", RequirementCategory.TECHNICAL_REQUIREMENT,
        timing=RequirementTiming.AT_EXECUTION,
    )  # fmt: skip
    scope.set_proof(ftth_req, [])
    await tender_structure.set_requirement_blocks(db, tender, ftth_req, [b1.id])
    await scope.set_territory_semantics(
        db,
        tender,
        ftth_req,
        completeness=TerritoryCompleteness.UNKNOWN,
        territories=[],
        provenance=PROV,
    )
    sla_req = await _requirement(
        db, tender, "SLA a Can Maset", RequirementCategory.TECHNICAL_REQUIREMENT,
        timing=RequirementTiming.AT_EXECUTION,
    )  # fmt: skip
    scope.set_proof(sla_req, [])
    await tender_structure.set_requirement_blocks(db, tender, sla_req, [b1.id])
    await scope.set_territory_semantics(
        db, tender, sla_req, completeness=TerritoryCompleteness.COMPLETE,
        territories=[{"kind": AreaKind.CUSTOM, "label": "Can Maset"}],
        provenance=PROV,
    )  # fmt: skip
    await scope.create_need(db, tender, sla_req, {
        "capability_code": "ops.sla_response_minutes", "unmapped_capability": None,
        "comparator": NeedComparator.LTE, "value_json": {"minutes": 120}, "value_schema": "v1",
        "need_type": NeedType.MINIMUM, "points": None, "award_criterion_id": None,
    }, provenance=PROV)  # fmt: skip
    ftth = await scope.create_scope_item(
        db,
        tender,
        {"service_line_code": "access_ftth", "block_id": b1.id, "quantity": Decimal("15")},
        provenance=PROV,
    )
    radio = await scope.create_scope_item(
        db,
        tender,
        {"service_line_code": "radio_link", "block_id": b1.id, "quantity": Decimal("2")},
        provenance=PROV,
    )
    support = await scope.create_scope_item(
        db, tender, {"service_line_code": "support_ops", "block_id": b1.id}, provenance=PROV
    )
    await db.flush()
    await _twin(db, company, client_a)

    # Al nivell pur: nomes el suport te una correspondencia demostrable (necessitat mapada
    # de la seva linia); el radioenllac i els FTTH, cap.
    view, _ = await analysis.load_structure_view(db, tender)
    by_line = {i.service_line_code: i for i in view.scope_items}
    from app.analysis import engine as pure

    assert pure.scope_item_territory_context(by_line["radio_link"], view)[3] == ()
    assert pure.scope_item_territory_context(by_line["access_ftth"], view)[3] == ()
    areas, completeness, _, basis = pure.scope_item_territory_context(by_line["support_ops"], view)
    assert basis == (sla_req.dedup_key,) and completeness == "complete" and len(areas) == 1

    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    await db.refresh(run)

    def candidates(item):
        return [c for c in run.delivery_candidates if c.scope_item_id == item.id]

    radio_c = candidates(radio)
    assert [(c.candidate_type, c.territory_verdict, c.area_verdicts_json) for c in radio_c] == [
        ("own_current", "unknown", [])
    ], "cap area heretada del bloc"
    assert radio_c[0].readiness == "pending_coverage", "conservador: no sabem on"
    ftth_c = candidates(ftth)
    assert {c.territory_verdict for c in ftth_c} == {"unknown"} and all(
        c.area_verdicts_json == [] for c in ftth_c
    )
    support_c = candidates(support)
    assert len(support_c) == 1 and len(support_c[0].area_verdicts_json) == 1
    assert support_c[0].area_verdicts_json[0]["area"]["label"] == "Can Maset"
