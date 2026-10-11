"""Gold end-to-end de 0051: el plec public de l'Escala (v2) contra el besso sintetic
del coneixement de l'empresa pilot.

El que es protegeix: cap informacio inventada; amb el DOC-04 real (font suplementaria)
els 15 FTTH tenen ubicacio pero la cobertura dels majoristes continua `unknown`, sense
cap pregunta al comprador; C1 es `unmatched` territorialment (i perd 5 punts sense
bloquejar), quatre vies `partner_current` per a cada accés FTTH i cap `own_current`,
la centraleta sense capacitats es `unknown`, J6 no es
perd, l'ENS declarat amb evidencia demana revisio i no bloqueja, R1/R2 son
`unknown` i no bloquegen, cap bloqueig confirmat, cap proposta, cap producte, i
ni el coneixement ni el plec canvien.

Qualificacio (0052, gold v3): l'RC satisfa amb dades declarades amb evidencia
(revisio, cap bloqueig); R1 i R2 son `unknown` perque el plec no dona el termini de
presentacio (cap rellotge decideix quins son els darrers exercicis o anys); el registre
d'operadors amb abast parcial queda
`unknown` amb revisio; els mitjans de R4a hi son pero el suport 24x7 amb escalat
no te codi i el calibratge no te prova: `unknown`; les 20 IPv4 declarades cobreixen
el minim de R8c i deixen els dos trams de C3 `at_risk` (no verificats).
"""

from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa

from app.analysis.engine import QuestionOut
from app.analysis.registry import ExplanationCode as X
from app.analysis.registry import QuestionCode as Q
from app.analysis.registry import explanation_text
from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.enums import FindingType
from app.models.requirements import AwardCriterion, Requirement
from app.models.tender_scope import (
    RequirementCapabilityNeed,
    RequirementTerritory,
    TenderScopeItem,
)
from app.models.tender_structure import TenderSite
from app.models.tenders import Tender
from app.services import tender_analysis as analysis
from tests.fixtures.company_twin import build_company_twin
from tests.test_analysis_copy_v3 import offending
from tests.test_company_knowledge_records_api import _link_evidence
from tests.test_tender_gold_lescala import (
    CASE,
    COMPANY_SIDE_PREFIXES,
    FTTH_SITES,
    _rows,
    _table_names,
)

pytestmark = pytest.mark.asyncio

#: Empremta d'estructura del gold de l'Escala ABANS del DOC-04 real (93827c9). Natural i
#: determinista: el DOC-04 ha de canviar-la (una run anterior queda desfasada).
PRE_DOC04_STRUCTURE_FINGERPRINT = "645c2b701969889fced635dc6271f71aedcac32952f31668fe2d400e025d9fa6"
R11A_STRUCTURE_FINGERPRINT = "be91ca42758ae076b9917002fdd2c1ee7e36372d550c0c0d11f30b8c461c12ee"


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _count(db, model) -> int:
    return await db.scalar(sa.select(sa.func.count()).select_from(model))


async def test_the_lescala_analysis_invents_nothing_and_decides_nothing(db, tenant_a, client_a):
    _, company = tenant_a
    dataset = tender_gold.load_dataset(CASE)
    loaded = await tender_gold.load_tender_gold(db, dataset)
    tender_id = loaded.tender.id
    rq = {code: r.id for code, r in loaded.requirements.items()}
    crit = {code: c.id for code, c in loaded.criteria.items()}
    items = {code: i.id for code, i in loaded.scope_items.items()}
    rows = await build_company_twin(db, company)
    await db.commit()  # l'API d'evidencia va en una altra transaccio
    await _link_evidence(client_a, company, "company_certification", rows["certification"].id)
    q = rows["qualification"]
    best_year = max(q["years"])
    for entity_type, entity_id in (
        ("company_financial_year", q["years"][best_year].id),
        ("company_project", q["projects"]["network"].id),
        ("company_insurance", q["insurance"].id),
    ):
        await _link_evidence(client_a, company, entity_type, entity_id)
    await db.commit()

    structure_before = analysis.structure_fingerprint(
        (
            await analysis.load_structure_view(
                db,
                await db.get(
                    __import__("app.models.tenders", fromlist=["Tender"]).Tender, tender_id
                ),
            )
        )[0]
    )
    knowledge_before = (await analysis.load_company_view(db, company.id)).knowledge_fingerprint
    outcome = await analysis.analyze_tender(db, company_id=company.id, tender_id=tender_id)
    await db.commit()
    run = outcome.run
    await db.refresh(run)
    fits = {a.requirement_id: a for a in run.fit_assessments}
    crits = {a.award_criterion_id: a for a in run.criterion_assessments}
    findings = run.findings

    # --- Res inventat: el plec i el coneixement son els mateixos abans i despres.
    assert run.tender_structure_fingerprint == structure_before
    assert run.knowledge_fingerprint == knowledge_before
    assert (
        await analysis.load_company_view(db, company.id)
    ).knowledge_fingerprint == knowledge_before
    assert await _count(db, TenderSite) == 25 and await _count(db, RequirementTerritory) == 64
    # El DOC-04 canvia el plec estructurat, no el coneixement de l'empresa.
    assert run.tender_structure_fingerprint != PRE_DOC04_STRUCTURE_FINGERPRINT
    assert await _count(db, Requirement) == 34 and await _count(db, AwardCriterion) == 28
    assert len(fits) == 34 and len(crits) == 28

    # --- DOC-04 real: els 15 FTTH tenen ubicacio; la llista ja no falta, pero la cobertura
    # (comparador sense GIS: un territori ancorat a una seu no es pot concloure) es unknown.
    ftth_codes = [f"S-B1-FTTH-{f}" for f in FTTH_SITES]
    ftth_items = [await db.get(TenderScopeItem, items[c]) for c in ftth_codes]
    assert all(i.quantity == 1 and i.site_id is not None for i in ftth_items)
    assert len({i.site_id for i in ftth_items}) == 15
    r8b = fits[rq["R8b"]]
    assert (r8b.territory_verdict, r8b.fit_result) == ("unknown", "unknown")
    assert r8b.support_json["territory"]["reason"] != "list_unavailable"
    assert not any(f.explanation_code == X.TERRITORY_LIST_UNAVAILABLE for f in findings)
    assert not any(q.question_code == Q.BUYER_PROVIDE_TERRITORY_LIST for q in run.questions)

    # --- C1: FTTH propia a la trama urbana -> unmatched (no unknown), 5 punts perduts, cap
    # bloqueig.
    c1 = fits[rq["C1R"]]
    assert (c1.territory_verdict, c1.fit_result, c1.knowledge_source) == (
        "unmatched",
        "does_not_satisfy",
        "current_declared",
    )
    assert c1.explanation_code == X.FACT_TRUE_TERRITORY_UNMATCHED
    assert c1.support_json["territory"]["areas"][0]["reason"] == "not_in_complete_coverage"
    c1_points = crits[crit["C1"]]
    assert (c1_points.criterion_result, c1_points.lost_points, c1_points.points_status) == (
        "not_achievable",
        5.0,
        "lost",
    )
    assert not any(
        f.finding_type == FindingType.BLOCKER_POTENTIAL and f.requirement_id == rq["C1R"]
        for f in findings
    )
    assert any(
        f.finding_type == FindingType.TERRITORY_GAP and f.requirement_id == rq["C1R"]
        for f in findings
    )

    # --- 15 FTTH x 4 majoristes = 60 partner_current, cap own_current, cap operador triat:
    # saber ON son els accessos no diu que cap majorista hi tingui cobertura.
    ftth_ids = {items[c] for c in ftth_codes}
    ftth = [c for c in run.delivery_candidates if c.scope_item_id in ftth_ids]
    assert sorted(c.candidate_type for c in ftth) == ["partner_current"] * 60
    wholesalers = {rows["roles"][n].id for n in ("Adamo", "Sewan", "Vera", "Cablemóvil")}
    for item_id in ftth_ids:
        assert {c.partner_role_id for c in ftth if c.scope_item_id == item_id} == wholesalers
    assert {c.readiness for c in ftth} == {"pending_coverage"} and {
        c.territory_verdict for c in ftth
    } == {"unknown"}
    confirm = {
        q.scope_item_id for q in run.questions if q.question_code == Q.COMPANY_CONFIRM_COVERAGE
    }
    assert ftth_ids <= confirm, "una pregunta de cobertura majorista per adreca"
    assert not any(c.candidate_type == "own_current" for c in ftth), (
        "el fet de FTTH propia no crea cap via"
    )
    assert not any(c.candidate_type == "own_future" for c in run.delivery_candidates)

    # --- 3CX sense capacitats: necessitats unknown; J6 no es perd; preguntes a l'empresa.
    pbx_needs = [
        n
        for n in run.need_assessments
        if n.capability_need_id in {x.id for x in loaded.needs["R10a"]}
    ]
    assert pbx_needs and all(n.fit_result == "unknown" for n in pbx_needs)
    assert {n.explanation_code for n in pbx_needs} <= {X.CAPABILITY_UNKNOWN, X.CAPABILITY_UNMAPPED}
    j6 = crits[crit["J6"]]
    assert (j6.criterion_result, j6.lost_points, j6.not_assessable_points, j6.max_points) == (
        "not_assessable",
        0.0,
        7.0,
        7.0,
    )
    assert any(
        f.finding_type == FindingType.WARNING and f.criterion_id == crit["J6"] for f in findings
    )
    asked = {
        q.params_json.get("capability")
        for q in run.questions
        if q.question_code == Q.COMPANY_DECLARE_CAPABILITY
    }
    assert {"pbx.queue_callback_keep_position", "pbx.hot_desking", "pbx.concurrent_calls"} <= asked
    for code in ("C2b", "C4a", "C4b"):
        assert (crits[crit[code]].points_status, crits[crit[code]].lost_points) == ("at_risk", 0.0)
    j4 = crits[crit["J4"]]
    assert (j4.criterion_result, j4.not_assessable_points) == ("not_assessable", 3.0)
    t1 = crits[crit["T1"]]
    assert (t1.at_risk_points, t1.lost_points, t1.not_assessable_points) == (2.0, 0.0, 0.0)

    # --- ENS declarat amb evidencia: satisfies, no verificat, revisio, cap bloqueig.
    ens = fits[rq["R6a"]]
    assert (ens.fit_result, ens.knowledge_source, ens.verification_status, ens.proof_status) == (
        "satisfies",
        "current_declared",
        "not_verified",
        "sufficient",
    )
    assert any(
        f.finding_type == FindingType.REVIEW_REQUIRED and f.requirement_id == rq["R6a"]
        for f in findings
    )
    assert not any(
        f.finding_type == FindingType.BLOCKER_POTENTIAL and f.requirement_id == rq["R6a"]
        for f in findings
    )

    # --- Qualificacio (0052): declarada amb evidencia -> satisfies, no verificada, revisio.
    for code, explanation in (("R5a", X.INSURANCE_MET),):
        fit = fits[rq[code]]
        assert (fit.fit_result, fit.knowledge_source, fit.verification_status) == (
            "satisfies",
            "current_declared",
            "not_verified",
        ), code
        assert (fit.explanation_code, fit.proof_status) == (explanation, "sufficient"), code
        assert any(
            f.finding_type == FindingType.REVIEW_REQUIRED
            and f.requirement_id == rq[code]
            and f.explanation_code == X.DECLARED_NOT_VERIFIED
            for f in findings
        ), code
    # R1: «els 3 ultims exercicis» es relatiu al plec. Sense termini de presentacio, no se
    # sap quins son: unknown, mai «els mes recents que hi ha a la base».
    r1 = fits[rq["R1"]]
    assert (r1.fit_result, r1.explanation_code) == (
        "unknown",
        X.QUALIFICATION_REFERENCE_DATE_MISSING,
    )
    assert r1.support_json["reference_date"] is None
    # R2: sense termini de presentacio no se sap quins son «els darrers 3 anys». El resultat
    # ja no ve de carregar l'import del projecte a l'any en que acaba.
    r2 = fits[rq["R2"]]
    assert (r2.fit_result, r2.explanation_code) == (
        "unknown",
        X.QUALIFICATION_REFERENCE_DATE_MISSING,
    )
    assert r2.support_json["reference_date"] is None
    # C-E2 (v3): el que falta es el termini, i es del plec. Una pregunta a l'organ per
    # requisit; cap «falta una dada de l'empresa» ni cap prova a demanar (no se sap de
    # quins exercicis o anys), i el fit no canvia.
    for code in ("R1", "R2"):
        assert fits[rq[code]].proof_status == "unknown", code
        mine = [f for f in findings if f.requirement_id == rq[code]]
        assert [
            (f.finding_type.value, f.explanation_code, (f.details_json or {}).get("missing_from"))
            for f in mine
        ] == [("missing_information", X.TENDER_REFERENCE_DATE_MISSING, "tender")], code
        assert not any(f.explanation_code in (X.DATA_MISSING, X.PROOF_MISSING) for f in mine)
        title = next(r["title"] for r in dataset["requirements"] if r["code"] == code)
        assert [
            (q.audience.value, q.question_code, q.params_json)
            for q in run.questions
            if q.requirement_id == rq[code]
        ] == [("buyer", Q.BUYER_PROVIDE_SUBMISSION_DEADLINE, {"title": title})], code
    # R3a: operador declarat amb una inscripcio d'abast parcial -> unknown + revisio, mai
    # satisfies ni bloqueig.
    cnmc = fits[rq["R3a"]]
    assert (cnmc.fit_result, cnmc.explanation_code) == (
        "unknown",
        X.REGISTRATION_SCOPE_NOT_COVERED,
    )
    assert any(
        f.finding_type == FindingType.REVIEW_REQUIRED
        and f.requirement_id == rq["R3a"]
        and f.explanation_code == X.REGISTRATION_SCOPE_NOT_COVERED
        for f in findings
    )
    assert any(
        question.question_code == Q.COMPANY_REVIEW_REGISTRATION_SCOPE
        and question.requirement_id == rq["R3a"]
        for question in run.questions
    )
    # R4a: perfils i equips calibrats hi son (declarats), pero el suport 24x7 amb escalat no
    # te codi i el calibratge no te prova: unknown, mai bloqueig. Disponible != adscrit.
    r4a = fits[rq["R4a"]]
    assert r4a.fit_result == "unknown" and r4a.proof_status == "missing"
    assert r4a.support_json["reasons"] == ["satisfied_declared", "satisfied_declared"]
    # R8c / C3: 20 IPv4 declarades -> minim de 10 cobert; els trams de 15 i 20, oberts.
    assert fits[rq["R8c"]].fit_result == "satisfies"
    c3 = crits[crit["C3"]]
    assert (c3.points_status, c3.at_risk_points, c3.lost_points) == ("at_risk", 2.0, 0.0)
    for code in ("R1", "R2", "R3a", "R4a", "R5a", "R8c"):
        assert not any(
            f.finding_type == FindingType.BLOCKER_POTENTIAL and f.requirement_id == rq[code]
            for f in findings
        ), code
    # --- Causa real dels punts en risc (v3, C-E1). Expectativa fixada pels inputs (plec +
    # besso), no per la primera execucio: primer les causes de cada necessitat...
    need_rows = (await db.scalars(sa.select(RequirementCapabilityNeed))).all()
    need_assessments = {a.capability_need_id: a for a in run.need_assessments}
    needs_of = {
        code: [need_assessments[n.id] for n in need_rows if n.award_criterion_id == cid]
        for code, cid in crit.items()
    }
    for code, count in (("T1", 3), ("C2b", 1), ("C4a", 2), ("C4b", 2)):
        assert len(needs_of[code]) == count, code
        assert all(n.fit_result == "unknown" for n in needs_of[code]), code
    assert len(needs_of["C3"]) == 2 and all(
        (n.fit_result, n.knowledge_source) == ("satisfies", "current_declared")
        for n in needs_of["C3"]
    )

    # ...i despres els findings de criteri: un per causa, amb les necessitats afectades.
    def criterion_findings(code: str) -> list[tuple[str, str, list[str]]]:
        return sorted(
            (
                f.finding_type.value,
                f.explanation_code,
                sorted((f.details_json or {}).get("need_ids", [])),
            )
            for f in findings
            if f.criterion_id == crit[code]
        )

    def ids(code: str) -> list[str]:
        return sorted(str(n.capability_need_id) for n in needs_of[code])

    for code in ("T1", "C2b", "C4a", "C4b"):
        assert criterion_findings(code) == [
            ("missing_information", X.CRITERION_NEEDS_UNKNOWN, ids(code))
        ], code
    assert criterion_findings("C3") == [("review_required", X.DECLARED_NOT_VERIFIED, ids("C3"))]
    # Invariant: «dades declarades» en un criteri nomes si CADA necessitat de la causa (o,
    # al cami enllacat, el requisit) compleix amb dada declarada; i sempre amb una causa.
    declared = ("satisfies", "current_declared")
    by_need_id = {str(a.capability_need_id): a for a in run.need_assessments}
    for f in findings:
        if f.criterion_id is None or f.explanation_code != X.DECLARED_NOT_VERIFIED:
            continue
        details = f.details_json or {}
        need_ids = details.get("need_ids", [])
        assert need_ids or details.get("requirement_id"), f.criterion_id
        for need_id in need_ids:
            n = by_need_id[need_id]
            assert (n.fit_result, n.knowledge_source) == declared, need_id
        if details.get("requirement_id"):
            linked = fits[uuid.UUID(details["requirement_id"])]
            assert (linked.fit_result, linked.knowledge_source) == declared
    # Invariant (I1): «no consta cap dada actual» mai amb una necessitat de font actual.
    unknown_findings = [f for f in findings if f.explanation_code == X.CRITERION_NEEDS_UNKNOWN]
    assert unknown_findings, "el gold exercita el codi"
    for f in unknown_findings:
        need_ids = (f.details_json or {}).get("need_ids", [])
        assert need_ids, f.criterion_id
        for need_id in need_ids:
            source = by_need_id[need_id].knowledge_source
            assert source not in ("current_declared", "current_verified"), need_id

    # Bloc 4: vies des de dades actuals, sense inventar cap soci local.
    fibre = [c for c in run.delivery_candidates if c.scope_item_id == items["S-B4-FIBRE"]]
    # El requisit de certificacio OTDR (necessitat mapada a `fiber_deployment`, sense
    # dimensio territorial) es l'unica correspondencia demostrable: disponible.
    assert [(c.candidate_type, c.readiness, c.territory_verdict) for c in fibre] == [
        ("own_current", "available", "not_applicable")
    ]
    civil = [c for c in run.delivery_candidates if c.scope_item_id == items["S-B4-CIVIL"]]
    assert [(c.candidate_type, c.territory_verdict) for c in civil] == [
        ("partner_current", "unknown")
    ]

    # --- Cap bloqueig confirmat, cap recomanacio, cap proposta, cap producte, cap taula de 3B.
    assert all(f.finding_type != "blocker_confirmed" for f in findings)
    assert (
        sum(
            c.secured_points + c.at_risk_points + c.lost_points + c.not_assessable_points
            for c in crits.values()
        )
        == 100.0
    )
    summary = analysis.summary_of(run)
    # Punts: `not_assessable` = preu + judici/demo; `lost` = nomes C1 (incompatibilitat
    # territorial demostrada); la resta d'automatics son compromisos oberts (`at_risk`);
    # res no es `secured` perque cap dada esta verificada. Derivat del gold, no hard-coded.
    expected_not_assessable = sum(
        c["points"]
        for c in dataset["award_criteria"]
        if c["evaluation_type"] == "judgment" or c["criterion_type"] == "price"
    )
    expected_lost = next(c["points"] for c in dataset["award_criteria"] if c["code"] == "C1")
    assert summary["max_points"] == 100.0
    assert summary["not_assessable_points"] == expected_not_assessable == 68
    assert summary["lost_points"] == expected_lost == 5.0
    assert summary["secured_points"] == 0.0
    assert summary["at_risk_points"] == 100 - expected_not_assessable - expected_lost
    assert (
        summary["secured_points"]
        + summary["at_risk_points"]
        + summary["lost_points"]
        + summary["not_assessable_points"]
    ) == 100.0
    automatic = [
        crits[crit[c["code"]]]
        for c in dataset["award_criteria"]
        if c["evaluation_type"] == "automatic"
        and c["criterion_type"] != "price"
        and c["code"] != "C1"
    ]
    assert all(a.points_status == "at_risk" and a.lost_points == 0.0 for a in automatic)
    assert summary["blocker_potential_count"] == 0, (
        "cap requisit obligatori te una dada actual que el contradigui"
    )
    assert summary["fit"]["does_not_satisfy"] == 1, "nomes C1, que es puntuacio"
    assert "score" not in summary and "recommendation" not in summary and "go" not in summary
    tables = await _table_names(db)
    for name in ("proposal_drafts", "tender_solutions", "offer_lines"):
        assert name not in tables or await _rows(db, name) == 0
    for table in [
        t
        for t in tables
        if t.startswith("company_products") or "proposal" in t or t.startswith("solution_products")
    ]:
        assert await _rows(db, table) == 0, table
    company_side = [
        t
        for t in tables
        if t.startswith(COMPANY_SIDE_PREFIXES) and not t.startswith("knowledge_coverage")
    ]
    assert company_side, "hi ha taules del costat empresa"

    # --- Determinisme: tornar a analitzar sense cap canvi reutilitza la run.
    again = await analysis.analyze_tender(db, company_id=company.id, tender_id=tender_id)
    assert again.reused and again.run.id == run.id

    # Resultat esperat, determinista i sense cap dada privada: les xifres de resum.
    expected = json.loads(
        '{"fit": {"satisfies": 4, "does_not_satisfy": 1, "unknown": 19, "not_applicable": 10}, '
        '"blocker_potential_count": 0, "lost_points": 5.0, "not_assessable_points": 95.0}'
    )
    keys = ("fit", "blocker_potential_count", "lost_points")
    assert {k: summary[k] for k in keys} == {k: expected[k] for k in keys}


async def test_the_lescala_v3_copy_shows_no_codes_and_keeps_coded_params(db, tenant_a, client_a):
    """Copy v3 (F1) sobre el gold: cap text que veu la persona (preguntes renderitzades
    per l'API i explicacions de cada fila) no porta codis ni formes sense accent; i
    `params_json` continua guardant codis, amb la mateixa clau de deduplicacio."""
    _, company = tenant_a
    loaded = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    tender_id = loaded.tender.id
    rows = await build_company_twin(db, company)
    await db.commit()
    await _link_evidence(client_a, company, "company_certification", rows["certification"].id)
    await db.commit()
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender_id)).run
    await db.commit()
    await db.refresh(run)
    # 3B-2 puja les regles a la v4 (forats del paquet, divergencia de rols); copy de la v3.
    assert run.rules_version == "phase3b-analysis-v4"

    base = f"/api/v1/companies/{company.id}/tenders/{tender_id}/analysis"
    response = await client_a.get(f"{base}/runs/{run.id}")
    assert response.status_code == 200, response.text
    texts = [q["text"] for q in response.json()["questions"]]
    assert texts and len(texts) == len(run.questions)
    rows_with_codes = [
        *run.fit_assessments,
        *run.need_assessments,
        *run.criterion_assessments,
        *run.delivery_candidates,
        *run.findings,
    ]
    codes = {r.explanation_code for r in rows_with_codes if r.explanation_code}
    texts += [explanation_text(code, run.rules_version) for code in sorted(codes)]
    assert {text: offending(text) for text in texts if offending(text)} == {}
    assert not any(t in ("Pregunta no disponible.", "Explicació no disponible.") for t in texts)

    # params_json: codis crus (no retols), i la clau de deduplicacio es la de sempre.
    for q in run.questions:
        params = q.params_json or {}
        if "service_line" in params:
            assert params["service_line"] in vocabulary.SERVICE_LINE_CODES
        if "capability" in params:
            assert params["capability"] in vocabulary.CAPABILITIES_BY_CODE
        assert (
            q.dedupe_key
            == QuestionOut(
                str(q.audience),
                q.question_code,
                q.rule_code,
                requirement_id=str(q.requirement_id) if q.requirement_id else None,
                criterion_id=str(q.criterion_id) if q.criterion_id else None,
                scope_item_id=str(q.scope_item_id) if q.scope_item_id else None,
                params=params,
            ).dedupe_key
        )
    assert any("service_line" in (q.params_json or {}) for q in run.questions)


async def test_the_lescala_r11a_quote_lives_in_the_ppt_and_not_in_doc05(db, tenant_a):
    """C-D1: la cita «no donen dret a cap exces» es del PPT (§6.2, p. 7), no del DOC-05.
    Les cites no entren a l'empremta d'estructura: moure-la no la canvia."""
    dataset = tender_gold.load_dataset(CASE)
    loaded = await tender_gold.load_tender_gold(db, dataset)
    await db.commit()
    tender = await db.get(Tender, loaded.tender.id)
    fingerprint = analysis.structure_fingerprint(
        (await analysis.load_structure_view(db, tender))[0]
    )
    assert fingerprint == R11A_STRUCTURE_FINGERPRINT

    r11a = next(r for r in dataset["requirements"] if r["code"] == "R11a")
    b4_fibre = next(i for i in dataset["scope_items"] if i["code"] == "S-B4-FIBRE")
    for item in (r11a, b4_fibre):
        quoted = [
            s for s in item["sources"] if "no donen dret a cap excés" in (s.get("quote") or "")
        ]
        assert [(s["doc"], s["where"]) for s in quoted] == [("PPT", "§6.2")]
        assert not any(s["doc"] == "DOC05" for s in item["sources"])
