"""El motor d'analisi (0051) contra la base: semantica dels requisits obligatoris,
dels criteris, de les proves i de les vies de prestacio; staleness, idempotencia,
transaccio, model i lectura. Un plec sintetic petit i un besso d'empresa.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.analysis.registry import RULES_VERSION
from app.analysis.registry import ExplanationCode as X
from app.analysis.registry import QuestionCode as Q
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
    )  # fmt: skip
    await scope.set_award_links(db, tender, reqs["c1"], [run_criterion.id])
    reqs["pbx"] = await _requirement(
        db, tender, "Centraleta", RequirementCategory.TECHNICAL_REQUIREMENT,
        timing=RequirementTiming.AT_EXECUTION,
    )  # fmt: skip
    scope.set_proof(reqs["pbx"], [])
    await tender_structure.set_requirement_blocks(db, tender, reqs["pbx"], [b3.id])
    await scope.set_territory_completeness(db, reqs["pbx"], TerritoryCompleteness.NOT_APPLICABLE)
    await scope.set_award_links(db, tender, reqs["pbx"], [t1.id, j6.id])

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
    )
    await scope.create_need(db, tender, reqs["pbx"], need(capability_code="pbx.queues"))
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
        ),
        "pbx": await scope.create_scope_item(
            db,
            tender,
            {"service_line_code": "voice_pbx", "block_id": b3.id, "quantity": Decimal("123")},
        ),
        "civil": await scope.create_scope_item(
            db, tender, {"service_line_code": "civil_works", "block_id": b1.id}
        ),
        "mobile": await scope.create_scope_item(
            db, tender, {"service_line_code": "mobile_telephony", "block_id": b3.id}
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
    assert ("review_required", crits["t1"].id) in {
        (f.finding_type, f.criterion_id) for f in run.findings
    }
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
    await tender_structure.link_source_ref(db, entity=blocks["b1"], source_ref_id=ref.id)
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
    await tender_structure.create_site(db, tender, {"name": "Can Maset", "role": "remote"})
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
        db, tender, ftth_req, completeness=TerritoryCompleteness.UNKNOWN, territories=[]
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
    )  # fmt: skip
    await scope.create_need(db, tender, sla_req, {
        "capability_code": "ops.sla_response_minutes", "unmapped_capability": None,
        "comparator": NeedComparator.LTE, "value_json": {"minutes": 120}, "value_schema": "v1",
        "need_type": NeedType.MINIMUM, "points": None, "award_criterion_id": None,
    })  # fmt: skip
    ftth = await scope.create_scope_item(
        db,
        tender,
        {"service_line_code": "access_ftth", "block_id": b1.id, "quantity": Decimal("15")},
    )
    radio = await scope.create_scope_item(
        db, tender, {"service_line_code": "radio_link", "block_id": b1.id, "quantity": Decimal("2")}
    )
    support = await scope.create_scope_item(
        db, tender, {"service_line_code": "support_ops", "block_id": b1.id}
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
