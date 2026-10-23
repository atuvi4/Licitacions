"""Compliment v6 (Bloc 1): ENS per categories, ISO, classificacio, registres, grups i D2.

Que es demostra, en l'ordre de les decisions del propietari (22-10-2026):

1. ENS `basica < mitjana < alta`: igual o superior compleix en tots els perfils,
   amb el `level` estructurat primer i nomes les certificacions vigents.
2. ISO: la ruta del normalitzador llegeix totes les normes del text literal i les
   exigeix totes (o una, si el plec diu «o»); «o equivalent» sense la norma i amb el
   perfil complet es un «no compleix» nomes potencial.
3. Classificacio comparada (grup i subgrup exactes, categoria >=, lletres antigues),
   mai «compleix» pel sol tipus; «RELI o ROLECE» qualsevol dels dos; documentacio de
   qui ja hi es inscrit avaluada; la capacitat legal de prestar telecomunicacions, la
   CNMC (mai el registre d'instal·ladors).
4. Una disjuncio completa sense cap via genera UN bloqueig (tambe en revisar i en
   recomptar); en serveis la classificacio substitueix la solvencia i no bloqueja.
5. D2: un pre-fit `low_fit` no fa candidata una licitacio elegible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa

from app.compliance import normalize
from app.compliance.engine import assess_requirement, derive_blocker_status, summarise
from app.compliance.normalize import parse_classifications
from app.compliance.profile import ProfileSnapshot
from app.compliance.resolution import RowState, finalize_rows, is_classification
from app.compliance.rules import ExplanationCode, RuleCode
from app.models.compliance import CompanyRequirementAssessment
from app.models.credentials import CompanyRegistration
from app.models.enums import (
    AssessmentResult,
    BlockerStatus,
    ContractType,
    KnowledgeLifecycle,
    MatchStatus,
    RecommendationResult,
    RegistrationType,
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    ReviewStatus,
)
from app.models.matching import CompanyTenderMatch
from app.models.profile import CompanyFinancialYear
from app.services.compliance_analysis import analyze_company_compliance, review_assessment
from tests.test_compliance_api import (  # noqa: F401 - fixtures
    add_requirement,
    company_of,
    extraction_run,
    tender,
)
from tests.test_compliance_normalize_product_v2 import PV2, FakeEvidence, FakeRequirement, req

AR = AssessmentResult
RC = RequirementCategory

DEADLINE = date(2026, 11, 3)
#: Una data de caducitat posterior al termini.
VALID = date(2028, 7, 26)


@dataclass
class Cert:
    name: str
    standard_code: str | None = None
    level: str | None = None
    lifecycle: Any = KnowledgeLifecycle.CURRENT
    expires_at: date | None = None
    verification_status: str = "pending_verification"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Reg:
    type: RegistrationType
    name: str
    registration_number: str | None = None
    expires_at: date | None = None
    issued_at: date | None = None
    lifecycle: Any = KnowledgeLifecycle.CURRENT
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def snap(*, certs=(), regs=(), complete=True) -> ProfileSnapshot:
    ratio = 1.0 if complete else 0.5
    return ProfileSnapshot(
        company_id=uuid.uuid4(),
        certifications=list(certs),
        registrations=list(regs),
        section_ratio={"certifications": ratio, "registrations": ratio},
    )


def mk(category, quotes, *, title="Requisit", obligation=RequirementObligation.MANDATORY):
    return FakeRequirement(
        id=uuid.uuid4(),
        category=category,
        obligation=obligation,
        title=title,
        description=None,
        value_json=None,
        evidence=[FakeEvidence(id=f"q{i}", quote=text) for i, text in enumerate(quotes)],
    )


# --------------------------------------------------------------------------- #
# 1. ENS per categories                                                         #
# --------------------------------------------------------------------------- #

#: El certificat real d'Indaleccius: el nom no diu la categoria, el `level` si.
ENS_INDALECCIUS = Cert(
    "Certificat ENS ACCM", standard_code="ENS", level="medium", expires_at=date(2028, 7, 26)
)


@pytest.mark.parametrize(
    ("required", "result", "code"),
    [
        ("categoria BÀSICA", AssessmentResult.SATISFIES, "CERTIFICATION_LEVEL_HIGHER_MET"),
        ("categoria MITJANA", AssessmentResult.SATISFIES, "CERTIFICATION_FOUND_VALID"),
        ("categoria ALTA", AssessmentResult.DOES_NOT_SATISFY, "CERTIFICATION_LEVEL_BELOW"),
    ],
)
def test_l_ens_mitja_de_l_empresa_compleix_basica_i_mitjana_i_no_alta(required, result, code):
    requirement = mk(
        RequirementCategory.CERTIFICATION,
        [f"Certificació de conformitat amb l'Esquema Nacional de Seguretat {required}"],
    )
    outcome = assess_requirement(requirement, snap(certs=[ENS_INDALECCIUS]), profile=PV2)
    assert (outcome.result, outcome.explanation_code) == (result, code)
    assert outcome.rule_code == RuleCode.ENS_CATEGORY_GTE


def test_amb_el_perfil_incomplet_una_categoria_inferior_es_sense_dades():
    requirement = mk(RequirementCategory.CERTIFICATION, ["ENS de categoria ALTA"])
    outcome = assess_requirement(
        requirement, snap(certs=[ENS_INDALECCIUS], complete=False), profile=PV2
    )
    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_LEVEL_BELOW


def test_un_ens_en_tramit_no_acredita_cap_categoria():
    in_progress = Cert(
        "ENS categoria Mitjana", standard_code="ENS", level="medium",
        lifecycle=KnowledgeLifecycle.IN_PROGRESS,
    )  # fmt: skip
    requirement = mk(RequirementCategory.CERTIFICATION, ["ENS de categoria BÀSICA"])
    outcome = assess_requirement(requirement, snap(certs=[in_progress]), profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_NOT_CURRENT
    assert outcome.params["lifecycle"] == ["in_progress"]


def test_l_ens_d_un_requisit_manual_tambe_es_per_categories():
    """Tots els perfils: un requisit fora de `product-v2` amb el nom «ENS Bàsica»."""
    requirement = FakeRequirement(
        id=uuid.uuid4(),
        category=RequirementCategory.CERTIFICATION,
        obligation=RequirementObligation.MANDATORY,
        title="ENS",
        description=None,
        value_json={"name": "ENS Bàsica"},
        evidence=[],
        run_id=None,
    )
    outcome = assess_requirement(requirement, snap(certs=[ENS_INDALECCIUS]))
    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_LEVEL_HIGHER_MET


# --------------------------------------------------------------------------- #
# 2. ISO                                                                        #
# --------------------------------------------------------------------------- #


def test_les_tres_iso_de_parc_tauli_s_exigeixen_totes():
    requirement = mk(
        RequirementCategory.CERTIFICATION,
        ["ISO/IEC 27001, ISO/IEC 20000 i ISO 9001 (o equivalent)"],
    )
    reading = normalize.normalize_requirement(requirement, PV2)
    assert reading.route == normalize.ROUTE_ISO
    assert reading.value_json["names"] == ["ISO 27001", "ISO 20000", "ISO 9001"]
    assert reading.value_json["all_of"] is True
    assert reading.value_json["equivalents_allowed"] is True

    two = snap(
        certs=[
            Cert("ISO 27001:2022", expires_at=VALID),
            Cert("Qualitat", standard_code="ISO_9001", expires_at=VALID),
        ]
    )
    outcome = assess_requirement(requirement, two, profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_SOME_MISSING
    assert outcome.params["missing"] == ["ISO 20000"]
    # La que falta es «o equivalent» amb el perfil complet: nomes potencial.
    assert outcome.params["potential_only"] is True

    three = snap(
        certs=[
            *two.certifications,
            Cert("Gestio de serveis TI", standard_code="ISO_20000", expires_at=VALID),
        ]
    )
    outcome = assess_requirement(requirement, three, profile=PV2)
    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_ALL_FOUND


def test_iso_alternatives_amb_o_n_hi_ha_prou_amb_una():
    requirement = mk(RequirementCategory.CERTIFICATION, ["Certificació ISO 9001 o ISO 14001."])
    assert normalize.normalize_requirement(requirement, PV2).value_json["all_of"] is False
    held = snap(certs=[Cert("UNE-EN ISO 14001", expires_at=VALID)])
    outcome = assess_requirement(requirement, held, profile=PV2)
    assert outcome.result == AssessmentResult.SATISFIES


def test_un_numero_sol_en_llista_es_una_norma_pero_un_any_no():
    requirement = mk(
        RequirementCategory.CERTIFICATION, ["Certificats ISO 9001:2015 i 14001, vigents el 2026."]
    )
    reading = normalize.normalize_requirement(requirement, PV2)
    assert reading.value_json["names"] == ["ISO 9001", "ISO 14001"]


def test_ens_alt_o_iso_27001_es_una_disjuncio():
    """Sant Just: «ENS ALT o ISO 27001». Amb la ISO n'hi ha prou."""
    requirement = mk(RequirementCategory.CERTIFICATION, ["Disposar de l'ENS ALT o ISO 27001."])
    reading = normalize.normalize_requirement(requirement, PV2)
    assert sorted(reading.value_json["names"]) == ["ENS alta", "ISO 27001"]
    assert reading.value_json["all_of"] is False
    with_iso = snap(certs=[Cert("ISO/IEC 27001", expires_at=VALID)])
    outcome = assess_requirement(requirement, with_iso, profile=PV2)
    assert outcome.result == AssessmentResult.SATISFIES
    only_ens = snap(certs=[ENS_INDALECCIUS])
    outcome = assess_requirement(requirement, only_ens, profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_SOME_MISSING


def test_o_equivalent_sense_la_norma_amb_el_perfil_incomplet_es_sense_dades():
    requirement = mk(RequirementCategory.CERTIFICATION, ["ISO 9001 o equivalent"])
    outcome = assess_requirement(requirement, snap(complete=False), profile=PV2)
    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_DATA_INCOMPLETE


def test_una_iso_en_tramit_no_acredita():
    in_progress = Cert("ISO 27001", lifecycle=KnowledgeLifecycle.IN_PROGRESS)
    requirement = mk(RequirementCategory.CERTIFICATION, ["Certificació ISO 27001."])
    outcome = assess_requirement(requirement, snap(certs=[in_progress]), profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_NOT_CURRENT


def test_un_iso_puntuable_no_es_normalitza():
    requirement = mk(
        RequirementCategory.CERTIFICATION,
        ["ISO 27001: 2 punts"],
        obligation=RequirementObligation.SCORED,
    )
    reading = normalize.normalize_requirement(requirement, PV2)
    assert (reading.status, reading.reason) == ("not_normalized", normalize.REASON_NOT_MANDATORY)


def test_o_equivalent_no_es_confirma_verificant_la_font():
    """Nomes una revisio humana confirma un `potential_only`."""
    status = derive_blocker_status(
        obligation=RequirementObligation.MANDATORY,
        effective_result=AssessmentResult.DOES_NOT_SATISFY,
        requirement_review_status=ReviewStatus.VERIFIED,
        assessment_reviewed=False,
        confirmable_by_source=False,
    )
    assert status == BlockerStatus.POTENTIAL
    reviewed = derive_blocker_status(
        obligation=RequirementObligation.MANDATORY,
        effective_result=AssessmentResult.DOES_NOT_SATISFY,
        requirement_review_status=ReviewStatus.UNREVIEWED,
        assessment_reviewed=True,
        confirmable_by_source=False,
    )
    assert reviewed == BlockerStatus.CONFIRMED


# --------------------------------------------------------------------------- #
# 3. Classificacio i registres                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Grup V, Subgrup 4, Categoria 1", [("v", "4", 1)]),
        ("Classificació V-4-1 (RD 773/2015)", [("v", "4", 1)]),
        ("grupo V, subgrupo 4, categoría C", [("v", "4", 3)]),
        ("Grup: V Subgrup: V-4 Categoria: 3", [("v", "4", 3)]),
        ("V-4, categoria 2", [("v", "4", 2)]),
        ("classificació en el subgrup V-4", [("v", "4", None)]),
        ("Lot 1 i lot 2, annex a 1", []),
    ],
)
def test_el_lector_de_classificacions(text, expected):
    found = [(c.group, c.subgroup, c.category) for c in parse_classifications(text)]
    assert found == expected


def test_la_classificacio_es_compara_grup_subgrup_i_categoria():
    requirement = mk(
        RequirementCategory.BUSINESS_CLASSIFICATION,
        ["Classificació empresarial: Grup V, Subgrup 4, Categoria 1."],
    )
    reading = normalize.normalize_requirement(requirement, PV2)
    assert reading.route == normalize.ROUTE_CLASSIFICATION
    assert reading.value_json == {
        "type": "business_classification", "group": "V", "subgroup": "4", "category": 1,
    }  # fmt: skip

    v41 = Reg(RegistrationType.BUSINESS_CLASSIFICATION, "Classificació empresarial", "V-4-1")
    outcome = assess_requirement(requirement, snap(regs=[v41]), profile=PV2)
    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.CLASSIFICATION_MET

    higher = mk(RequirementCategory.BUSINESS_CLASSIFICATION, ["Grup V, Subgrup 4, Categoria 3"])
    outcome = assess_requirement(higher, snap(regs=[v41]), profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CLASSIFICATION_CATEGORY_BELOW

    other = mk(RequirementCategory.BUSINESS_CLASSIFICATION, ["Grup U, Subgrup 1, Categoria 1"])
    outcome = assess_requirement(other, snap(regs=[v41]), profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CLASSIFICATION_NOT_HELD


def test_una_classificacio_sense_dir_quina_ja_no_compleix_pel_sol_tipus():
    """El fals positiu de la v5: valor nul, titol «Classificació», i la V-4-1 complia."""
    requirement = mk(
        RequirementCategory.BUSINESS_CLASSIFICATION,
        ["Caldrà acreditar la classificació empresarial que correspongui."],
        title="Classificació empresarial",
    )
    v41 = Reg(RegistrationType.BUSINESS_CLASSIFICATION, "V-4-1")
    outcome = assess_requirement(requirement, snap(regs=[v41]), profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.CLASSIFICATION_NOT_STATED


def test_diverses_classificacions_per_lots_son_ambigues():
    requirement = mk(
        RequirementCategory.BUSINESS_CLASSIFICATION,
        [
            "Ambdós lots: Grup V, Subgrup 4, Categoria 3. "
            "Només lot 1: Grup V, Subgrup 4, Categoria 1."
        ],
    )
    reading = normalize.normalize_requirement(requirement, PV2)
    assert reading.reason == normalize.REASON_CLASSIFICATION_AMBIGUOUS


def test_reli_o_rolece_es_qualsevol_dels_dos():
    requirement = mk(
        RequirementCategory.REGISTRATION,
        ["És obligatori estar inscrit al RELI o al ROLECE (art. 159.4.a LCSP)."],
        title="Inscripció al RELI o ROLECE",
    )
    reading = normalize.normalize_requirement(requirement, PV2)
    assert reading.value_json == {"type": "registry_any_of", "types": ["reli", "rolece"]}
    outcome = assess_requirement(
        requirement, snap(regs=[Reg(RegistrationType.RELI, "RELIC")]), profile=PV2
    )
    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.rule_code == RuleCode.REGISTRATION_ANY_OF
    outcome = assess_requirement(requirement, snap(), profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_la_documentacio_de_qui_ja_es_inscrit_amb_marca_literal_no_aplica_si_no_ho_es():
    requirement = mk(
        RequirementCategory.REGISTRATION,
        ["Les empreses inscrites al RELI o al ROLECE n'aportaran el certificat."],
    )
    outcome = assess_requirement(requirement, snap(), profile=PV2)
    assert outcome.result == AssessmentResult.NOT_APPLICABLE
    assert outcome.explanation_code == ExplanationCode.REGISTRATION_DOCUMENTATION_NOT_APPLICABLE


def test_el_registre_d_instal_ladors_no_es_la_cnmc():
    requirement = mk(
        RequirementCategory.REGISTRATION,
        [
            "Inscripció al registre d'empreses instal·ladores de telecomunicacions per "
            "prestar els serveis de telecomunicacions."
        ],
    )
    reading = normalize.normalize_requirement(requirement, PV2)
    assert reading.reason == normalize.REASON_REGISTRY_NOT_NAMED


def test_la_capacitat_legal_de_l_escala_es_la_cnmc():
    requirement = req("0a70dd0c")
    cnmc = Reg(RegistrationType.SECTOR_OPERATOR_REGISTRY, "Registre d'operadors (CNMC)")
    outcome = assess_requirement(requirement, snap(regs=[cnmc]), profile=PV2)
    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.REGISTRATION_FOUND_VALID


# --------------------------------------------------------------------------- #
# 4. Grups i substitucio (funcio pura)                                          #
# --------------------------------------------------------------------------- #


def _row(
    category, own, *, group=None, complete=None, lot=None, key=None, reviewed=None, rule=None
):
    return RowState(
        key=key or uuid.uuid4(),
        category=category,
        tender_lot_id=lot,
        obligation=RequirementObligation.MANDATORY,
        modality=RequirementModality.ALTERNATIVE if group else RequirementModality.UNKNOWN,
        requirement_verified=False,
        alternative_group=group,
        group_complete=complete,
        own=own,
        reviewed=reviewed,
        classification=is_classification(category, rule, {}),
    )


def test_una_disjuncio_completa_sense_cap_via_porta_un_sol_bloqueig():
    dns = AssessmentResult.DOES_NOT_SATISFY
    rows = [
        _row(RequirementCategory.EXPERIENCE, dns, group="g", complete=True),
        _row(RequirementCategory.BUSINESS_CLASSIFICATION, dns, group="g", complete=True),
    ]
    outcomes = finalize_rows(rows, services=False)
    blockers = [o.blocker for o in outcomes.values()]
    assert blockers.count(BlockerStatus.POTENTIAL) == 1
    assert blockers.count(BlockerStatus.NONE) == 1


def test_si_una_via_compleix_el_grup_queda_resolt():
    rows = [
        _row(RC.EXPERIENCE, AR.DOES_NOT_SATISFY, group="g", complete=True),
        _row(RC.BUSINESS_CLASSIFICATION, AR.SATISFIES, group="g", complete=True),
    ]
    outcomes = finalize_rows(rows, services=False)
    assert {o.effective for o in outcomes.values()} == {AssessmentResult.SATISFIES}
    assert {o.blocker for o in outcomes.values()} == {BlockerStatus.NONE}


def test_en_serveis_la_classificacio_substitueix_la_solvencia():
    classification = _row(RequirementCategory.BUSINESS_CLASSIFICATION, AssessmentResult.SATISFIES)
    economic = _row(RequirementCategory.ECONOMIC_SOLVENCY, AssessmentResult.DOES_NOT_SATISFY)
    technical = _row(RequirementCategory.TECHNICAL_SOLVENCY, AssessmentResult.NEEDS_REVIEW)
    insurance = _row(RequirementCategory.INSURANCE, AssessmentResult.DOES_NOT_SATISFY)
    outcomes = finalize_rows([classification, economic, technical, insurance], services=True)
    assert outcomes[economic.key].effective == AssessmentResult.SATISFIES
    assert outcomes[economic.key].substituted_by == classification.key
    assert outcomes[technical.key].effective == AssessmentResult.SATISFIES
    assert insurance.key not in outcomes  # l'asseguranca no es solvencia
    # En obres (o qualsevol altre tipus) no s'hi toca res.
    assert finalize_rows([classification, economic], services=False) == {}


def test_en_serveis_una_classificacio_que_no_s_acredita_no_bloqueja_ni_obliga():
    classification = _row(RC.BUSINESS_CLASSIFICATION, AR.DOES_NOT_SATISFY)
    economic = _row(RequirementCategory.ECONOMIC_SOLVENCY, AssessmentResult.DOES_NOT_SATISFY)
    outcomes = finalize_rows([classification, economic], services=True)
    assert outcomes[classification.key].blocker == BlockerStatus.NONE
    assert outcomes[classification.key].obligation == RequirementObligation.INFORMATIVE
    # Sense classificacio, la solvencia es la seva i bloqueja com sempre.
    assert outcomes[economic.key].blocker == BlockerStatus.POTENTIAL


def test_una_classificacio_d_un_lot_nomes_substitueix_la_solvencia_del_mateix_lot():
    lot_a, lot_b = uuid.uuid4(), uuid.uuid4()
    classification = _row(RC.BUSINESS_CLASSIFICATION, AR.SATISFIES, lot=lot_a)
    same = _row(RequirementCategory.ECONOMIC_SOLVENCY, AssessmentResult.DOES_NOT_SATISFY, lot=lot_a)
    other = _row(RC.ECONOMIC_SOLVENCY, AR.DOES_NOT_SATISFY, lot=lot_b)
    tender_wide = _row(RequirementCategory.ECONOMIC_SOLVENCY, AssessmentResult.DOES_NOT_SATISFY)
    outcomes = finalize_rows([classification, same, other, tender_wide], services=True)
    assert outcomes[same.key].effective == AssessmentResult.SATISFIES
    assert outcomes[other.key].effective == AssessmentResult.DOES_NOT_SATISFY
    assert outcomes[tender_wide.key].effective == AssessmentResult.DOES_NOT_SATISFY


# --------------------------------------------------------------------------- #
# 5. D2                                                                         #
# --------------------------------------------------------------------------- #


def test_d2_un_low_fit_amb_60_punts_o_mes_no_es_candidat():
    rows = [(RequirementObligation.MANDATORY, AssessmentResult.SATISFIES, BlockerStatus.NONE)]
    assert summarise(rows, prefit_score=75, prefit_status="low_fit").recommendation == (
        RecommendationResult.ELIGIBLE_LOW_FIT
    )
    assert summarise(rows, prefit_score=75, prefit_status="candidate").recommendation == (
        RecommendationResult.GO_CANDIDATE
    )
    # Sense estat (runs anteriors a la 0064), com abans: la puntuacio.
    assert summarise(rows, prefit_score=75).recommendation == RecommendationResult.GO_CANDIDATE
    assert summarise(rows, prefit_score=40).recommendation == RecommendationResult.ELIGIBLE_LOW_FIT


# --------------------------------------------------------------------------- #
# Servei: base de dades                                                         #
# --------------------------------------------------------------------------- #


async def _revenue(db, company, amount: str) -> None:
    db.add(
        CompanyFinancialYear(
            company_id=company.id, year=2025, revenue=Decimal(amount), currency="EUR"
        )
    )
    await db.commit()


async def _alternative(db, run, tender_, amount: int, title: str):
    requirement = await add_requirement(
        db, run, tender_, value={"amount": amount, "currency": "EUR"}, title=title
    )
    requirement.modality = RequirementModality.ALTERNATIVE
    requirement.alternative_group = "solvencia"
    requirement.alternative_group_complete = True
    await db.commit()
    return requirement


async def _assessments(db, run_id) -> list[CompanyRequirementAssessment]:
    return list(
        (
            await db.scalars(
                sa.select(CompanyRequirementAssessment).where(
                    CompanyRequirementAssessment.assessment_run_id == run_id
                )
            )
        ).all()
    )


async def test_servei_un_grup_complet_sense_cap_via_bloqueja_una_vegada(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company = await company_of(db, tenant_a)
    await _revenue(db, company, "100000")
    await _alternative(db, extraction_run, tender, 900000, "Via A")
    await _alternative(db, extraction_run, tender, 800000, "Via B")

    run = (await analyze_company_compliance(db, company, tender)).run

    assert run.potential_blockers_count == 1
    rows = await _assessments(db, run.id)
    assert sorted(a.blocker_status for a in rows) == [BlockerStatus.NONE, BlockerStatus.POTENTIAL]
    assert {a.explanation_code for a in rows} == {"ALTERNATIVE_GROUP_NOT_SATISFIED"}

    # Una persona diu que una via SI acredita: el recompte desfa el bloqueig del grup.
    blocking = next(a for a in rows if a.blocker_status == BlockerStatus.POTENTIAL)
    other = next(a for a in rows if a is not blocking)
    user, _ = tenant_a
    await review_assessment(
        db,
        other,
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Acreditat per la via B amb el certificat de l'any 2024.",
        user_id=user.id,
    )
    await db.refresh(run)
    await db.refresh(blocking)
    assert run.potential_blockers_count == 0
    assert blocking.blocker_status == BlockerStatus.NONE


async def test_servei_en_serveis_la_classificacio_substitueix_la_solvencia(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company = await company_of(db, tenant_a)
    tender.contract_type = ContractType.SERVICES
    db.add(
        CompanyRegistration(
            company_id=company.id,
            type=RegistrationType.BUSINESS_CLASSIFICATION,
            name="Classificació empresarial",
            registration_number="V-4-1",
        )
    )
    await _revenue(db, company, "100000")
    solvency = await add_requirement(
        db, extraction_run, tender, value={"amount": 900000, "currency": "EUR"}, title="Volum"
    )
    await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.BUSINESS_CLASSIFICATION,
        value={"group": "V", "subgroup": "4", "category": "1"},
        title="Classificació alternativa",
    )

    run = (await analyze_company_compliance(db, company, tender)).run

    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    substituted = rows[solvency.id]
    assert substituted.automatic_result == AssessmentResult.SATISFIES
    assert substituted.explanation_code == ExplanationCode.CLASSIFICATION_SUBSTITUTES_SOLVENCY
    assert substituted.explanation_params["substitution"]["own_result"] == "does_not_satisfy"
    assert run.potential_blockers_count == 0


async def test_servei_d2_el_run_desa_l_estat_del_pre_fit(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company = await company_of(db, tenant_a)
    await _revenue(db, company, "2000000")
    await add_requirement(db, extraction_run, tender, value={"amount": 450000, "currency": "EUR"})
    db.add(
        CompanyTenderMatch(
            company_id=company.id,
            tender_id=tender.id,
            score=74,
            status=MatchStatus.LOW_FIT,
            data_coverage=1.0,
            rules_version="metadata-match-v3",
        )
    )
    await db.commit()

    run = (await analyze_company_compliance(db, company, tender)).run

    assert run.prefit_score == 74
    assert run.prefit_status == MatchStatus.LOW_FIT
    assert run.recommendation == RecommendationResult.ELIGIBLE_LOW_FIT


# --------------------------------------------------------------------------- #
# 6. Correccions de la revisio independent (22-10-2026)                        #
# --------------------------------------------------------------------------- #

def _assess(requirement, profile_snapshot):
    return assess_requirement(requirement, profile_snapshot, deadline=DEADLINE, profile=PV2)


@pytest.mark.parametrize(
    "quotes",
    [
        ["Certificat ISO 9001, ISO 14001 o ISO 45001."],
        [
            "Certificació ISO 9001 o ISO 14001.",
            "Caldrà disposar de la certificació ISO 9001 o ISO 14001 vigent.",
        ],
        ["Certificació ISO 9001 o ISO 14001.", "S'aportarà el certificat ISO 9001."],
        ["Certificat ISO 9001 i/o ISO 14001."],
        ["Certificat ISO 9001 (gestió de la qualitat) o bé ISO 14001 (gestió ambiental)."],
    ],
)
def test_m1_una_llista_amb_o_es_decideix_text_a_text(quotes):
    """«A, B o C», la mateixa llista en dues cites o una cita amb una sola norma de la
    llista: n'hi ha prou amb una (abans s'exigien totes, i el bloqueig es confirmava)."""
    requirement = mk(RC.CERTIFICATION, quotes)
    assert normalize.normalize_requirement(requirement, PV2).value_json["all_of"] is False
    outcome = _assess(requirement, snap(certs=[Cert("ISO 14001", expires_at=VALID)]))
    assert outcome.result == AR.SATISFIES


def test_m1_el_titol_i_la_descripcio_amb_o_tambe_son_alternatives():
    requirement = mk(
        RC.CERTIFICATION, ["Certificat de gestió vigent."], title="Certificat ISO 9001 o ISO 14001"
    )
    requirement.description = "Disposar del certificat ISO 9001 o ISO 14001."
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.value_json["all_of"] is False
    assert _assess(requirement, snap(certs=[Cert("ISO 9001", expires_at=VALID)])).result == (
        AR.SATISFIES
    )


def test_m1_o_equivalent_no_separa_normes():
    requirement = mk(RC.CERTIFICATION, ["ISO 9001 o equivalent i ISO 14001 o equivalent."])
    value = normalize.normalize_requirement(requirement, PV2).value_json
    assert (value["names"], value["all_of"], value["equivalents_allowed"]) == (
        ["ISO 9001", "ISO 14001"],
        True,
        True,
    )


@pytest.mark.parametrize(
    "quotes",
    [
        ["Certificats ISO 9001 i ISO 14001 o ISO 45001."],
        ["Certificació ISO 9001 o ISO 14001.", "Certificació ISO 27001 o ISO 20000."],
        ["Certificació ISO 9001 o ISO 14001.", "També caldrà la ISO 27001."],
        ["Certificat ISO 9001 / ISO 14001."],
    ],
)
def test_m1_una_llista_que_no_es_pot_llegir_va_a_revisio(quotes):
    requirement = mk(RC.CERTIFICATION, quotes)
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.reason == normalize.REASON_CERTIFICATION_LIST_AMBIGUOUS
    outcome = _assess(requirement, snap(certs=[Cert("ISO 9001", expires_at=VALID)]))
    assert outcome.result == AR.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("quote", "held"),
    [
        ("Certificat de gestió ambiental ISO 14001 o EMAS.", "EMAS"),
        ("Certificats ISO 9001 i EMAS vigents.", "ISO 9001"),
        ("Certificats ISO 9001, ISO 14001 i OHSAS 18001.", "ISO 9001"),
        ("Certificació UNE-EN ISO 9001 i UNE 166002.", "ISO 9001"),
        ("Certificat ISO 9001 i conformitat amb la norma EN 50131.", "ISO 9001"),
    ],
)
def test_m2_un_altre_esquema_al_costat_de_les_iso_va_a_revisio(quote, held):
    """Llegir nomes les ISO seria jutjar un altre requisit: ni «compleix» ni «no compleix»."""
    requirement = mk(RC.CERTIFICATION, [quote])
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.reason == normalize.REASON_CERTIFICATION_OTHER_SCHEME
    outcome = _assess(requirement, snap(certs=[Cert(held, expires_at=VALID)]))
    assert outcome.result == AR.NEEDS_REVIEW


def test_m2_una_preposicio_en_minuscula_no_es_cap_esquema():
    requirement = mk(RC.CERTIFICATION, ["Certificado ISO 9001 vigente en 2026."])
    assert normalize.normalize_requirement(requirement, PV2).value_json["names"] == ["ISO 9001"]


@pytest.mark.parametrize(
    ("expires_at", "result", "code"),
    [
        (date(2024, 3, 1), AR.DOES_NOT_SATISFY, ExplanationCode.CERTIFICATION_FOUND_EXPIRED),
        (None, AR.NEEDS_REVIEW, ExplanationCode.CERTIFICATION_FOUND_NO_EXPIRY),
        (VALID, AR.SATISFIES, ExplanationCode.CERTIFICATION_FOUND_VALID),
    ],
)
def test_m3_una_iso_ha_de_ser_vigent_al_termini(expires_at, result, code):
    """El valor lliure no diu si cal vigencia; la llei si (art. 140.4 LCSP)."""
    requirement = mk(RC.CERTIFICATION, ["Certificat ISO 27001 vigent a la data de presentació."])
    outcome = _assess(requirement, snap(certs=[Cert("ISO/IEC 27001", expires_at=expires_at)]))
    assert (outcome.result, outcome.explanation_code) == (result, code)


def test_m3_una_ens_caducada_no_acredita_la_categoria():
    requirement = mk(RC.CERTIFICATION, ["ENS de categoria MITJANA."])
    expired = Cert("ENS", standard_code="ENS", level="medium", expires_at=date(2025, 1, 1))
    outcome = _assess(requirement, snap(certs=[expired]))
    assert (outcome.result, outcome.explanation_code) == (
        AR.DOES_NOT_SATISFY,
        ExplanationCode.CERTIFICATION_FOUND_EXPIRED,
    )


def test_m4_o_equivalent_amb_la_norma_en_tramit_es_com_si_no_hi_fos():
    """Decisio 2: el bloqueig nomes el confirma una persona, encara que la font es verifiqui."""
    requirement = mk(RC.CERTIFICATION, ["Certificat ISO 14001 o equivalent."])
    certs = [
        Cert("ISO 14001", lifecycle=KnowledgeLifecycle.IN_PROGRESS),
        Cert("EMAS", expires_at=VALID),
    ]
    outcome = _assess(requirement, snap(certs=certs))
    assert (outcome.result, outcome.explanation_code) == (
        AR.DOES_NOT_SATISFY,
        ExplanationCode.CERTIFICATION_EQUIVALENT_NOT_DECLARED,
    )
    assert outcome.params["potential_only"] is True
    assert outcome.params["not_current"] == ["in_progress"]
    incomplete = _assess(requirement, snap(certs=certs, complete=False))
    assert incomplete.result == AR.UNKNOWN


def test_m4_o_equivalent_amb_la_norma_caducada_nomes_es_potencial():
    requirement = mk(RC.CERTIFICATION, ["Certificat ISO 14001 o equivalent."])
    outcome = _assess(requirement, snap(certs=[Cert("ISO 14001", expires_at=date(2025, 1, 1))]))
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_FOUND_EXPIRED
    assert outcome.params["potential_only"] is True
    assert derive_blocker_status(
        obligation=RequirementObligation.MANDATORY,
        effective_result=outcome.result,
        requirement_review_status=ReviewStatus.VERIFIED,
        assessment_reviewed=False,
        confirmable_by_source=False,
    ) == BlockerStatus.POTENTIAL


def test_m4_una_ens_en_tramit_amb_o_equivalent_nomes_es_potencial():
    requirement = mk(RC.CERTIFICATION, ["ENS de categoria MITJANA o equivalent."])
    in_progress = Cert(
        "ENS", standard_code="ENS", level="medium", lifecycle=KnowledgeLifecycle.IN_PROGRESS
    )
    outcome = _assess(requirement, snap(certs=[in_progress]))
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_EQUIVALENT_NOT_DECLARED
    assert outcome.params["potential_only"] is True


def test_m1_de_l_ens_entre_diverses_certificacions_la_que_millor_acredita():
    """Una ENS Mitjana caducada i una d'Alta vigent: acredita l'Alta (troballa m1)."""
    requirement = mk(RC.CERTIFICATION, ["ENS de categoria MITJANA."])
    certs = [
        Cert("ENS Mitjana", standard_code="ENS", level="medium", expires_at=date(2025, 3, 1)),
        Cert("ENS Alta", standard_code="ENS", level="high", expires_at=VALID),
    ]
    outcome = _assess(requirement, snap(certs=certs))
    assert (outcome.result, outcome.explanation_code, outcome.params["found"]) == (
        AR.SATISFIES,
        ExplanationCode.CERTIFICATION_LEVEL_HIGHER_MET,
        "ENS Alta",
    )


def test_m8_en_un_conjunt_qualsevol_de_n_hi_ha_prou_amb_una_via_potencial():
    from app.compliance.certifications import POTENTIAL_ONLY_KEY, _potential_only
    from app.compliance.rules import RuleOutcome

    def failure(potential: bool) -> RuleOutcome:
        return RuleOutcome(
            result=AR.DOES_NOT_SATISFY,
            rule_code=RuleCode.CERTIFICATION_EXACT,
            explanation_code=ExplanationCode.CERTIFICATION_FOUND_EXPIRED,
            params={POTENTIAL_ONLY_KEY: True} if potential else {},
        )

    failed = [("ISO 9001", failure(False)), ("ISO 14001", failure(True))]
    assert _potential_only(failed, all_required=False) == {POTENTIAL_ONLY_KEY: True}
    assert _potential_only(failed, all_required=True) == {}


def test_m6_un_rolece_retirat_no_acredita_reli_o_rolece():
    requirement = mk(RC.REGISTRATION, ["Inscripció al RELI o al ROLECE vigent."])
    retired = Reg(RegistrationType.ROLECE, "ROLECE", lifecycle=KnowledgeLifecycle.RETIRED)
    outcome = _assess(requirement, snap(regs=[retired]))
    assert (outcome.result, outcome.explanation_code) == (
        AR.DOES_NOT_SATISFY,
        ExplanationCode.REGISTRATION_NOT_CURRENT,
    )


def test_m6_una_classificacio_en_tramit_no_acredita_ni_substitueix():
    requirement = mk(
        RC.BUSINESS_CLASSIFICATION, ["Classificació: Grup V, Subgrup 4, Categoria 1."]
    )
    in_progress = Reg(
        RegistrationType.BUSINESS_CLASSIFICATION,
        "Classificació V-4-3",
        lifecycle=KnowledgeLifecycle.IN_PROGRESS,
    )
    outcome = _assess(requirement, snap(regs=[in_progress]))
    assert (outcome.result, outcome.explanation_code) == (
        AR.DOES_NOT_SATISFY,
        ExplanationCode.REGISTRATION_NOT_CURRENT,
    )
    # I en serveis, sense classificacio acreditada, la solvencia es la seva.
    classification = _row(RC.BUSINESS_CLASSIFICATION, outcome.result)
    economic = _row(RC.ECONOMIC_SOLVENCY, AR.DOES_NOT_SATISFY)
    outcomes = finalize_rows([classification, economic], services=True)
    assert outcomes[economic.key].substituted_by is None
    assert outcomes[economic.key].blocker == BlockerStatus.POTENTIAL


def test_m6_la_documentacio_de_qui_es_inscrit_no_compta_un_registre_retirat():
    from app.compliance.personnel import assess_registry_documentation

    retired = Reg(RegistrationType.ROLECE, "ROLECE", lifecycle=KnowledgeLifecycle.RETIRED)
    outcome = assess_registry_documentation(snap(regs=[retired]), DEADLINE, literal_subject=True)
    assert outcome.result == AR.NOT_APPLICABLE


def test_m6_l_empremta_del_perfil_canvia_amb_el_cicle_de_vida_d_un_registre():
    from app.compliance.profile import compute_profile_fingerprint

    current = Reg(RegistrationType.ROLECE, "ROLECE")
    retired = Reg(
        RegistrationType.ROLECE, "ROLECE", lifecycle=KnowledgeLifecycle.RETIRED, id=current.id
    )
    assert compute_profile_fingerprint(snap(regs=[current])) != compute_profile_fingerprint(
        snap(regs=[retired])
    )


@pytest.mark.parametrize(
    "quote",
    [
        "Habilitació per a la prestació de serveis d'instal·lació de telecomunicacions.",
        "Habilitación para la prestación de servicios de instalación de telecomunicaciones.",
    ],
)
def test_m3_la_habilitacio_d_instal_lacio_no_es_la_cnmc(quote):
    requirement = mk(RC.REGISTRATION, [quote])
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.route != normalize.ROUTE_SECTOR_OPERATOR_REGISTRY


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Es requereix la classificació indicada a l'apartat F-2 del quadre.", []),
        ("Classificació empresarial segons la clàusula C-3 del PCAP.", []),
        ("Classificació: Grup V, Subgrup 4, Categoria 1 (vegeu l'annex A-1).", [("v", "4", 1)]),
        ("Lot 1: V-4-1. Lot 2: V-4-2.", []),  # sense cap paraula de classificacio
        ("Lot 1: classificació V-4-1.", [("v", "4", 1)]),
    ],
)
def test_m4_una_referencia_no_es_una_classificacio(text, expected):
    found = [(c.group, c.subgroup, c.category) for c in parse_classifications(text)]
    assert found == expected


def test_m4_el_registre_de_classificacio_de_l_empresa_es_llegeix_sense_context():
    found = parse_classifications("V-4-1", context_required=False)
    assert [(c.group, c.subgroup, c.category) for c in found] == [("v", "4", 1)]


def test_m5_la_classificacio_no_es_llegeix_del_titol_que_ha_escrit_el_model():
    requirement = mk(
        RC.BUSINESS_CLASSIFICATION,
        ["Caldrà acreditar la classificació empresarial que correspongui."],
        title="Classificació Grup V, Subgrup 4, Categoria 3",
    )
    company = Reg(RegistrationType.BUSINESS_CLASSIFICATION, "Classificació V-4-1")
    outcome = _assess(requirement, snap(regs=[company]))
    assert (outcome.result, outcome.explanation_code) == (
        AR.NEEDS_REVIEW,
        ExplanationCode.CLASSIFICATION_NOT_STATED,
    )


def test_m6_una_classificacio_d_un_requisit_de_registre_tampoc_no_obliga_en_serveis():
    """Es classificacio per la regla, no nomes per la categoria (`is_classification`)."""
    assert is_classification(RC.REGISTRATION, RuleCode.BUSINESS_CLASSIFICATION_GTE, {})
    assert is_classification(
        RC.REGISTRATION, RuleCode.REGISTRATION_EXACT, {"required_type": "business_classification"}
    )
    row = _row(RC.REGISTRATION, AR.DOES_NOT_SATISFY, rule=RuleCode.BUSINESS_CLASSIFICATION_GTE)
    outcome = finalize_rows([row], services=True)[row.key]
    assert (outcome.obligation, outcome.blocker) == (
        RequirementObligation.INFORMATIVE,
        BlockerStatus.NONE,
    )


@pytest.mark.parametrize(
    ("text", "category", "category_max"),
    [
        ("Grup V, Subgrup 4, Categoria D", 4, 5),  # serveis: D -> 4 o 5
        ("Grup V, Subgrup 4, Categoria C", 3, None),
        ("Grup C, Subgrup 2, Categoria b", 1, None),  # obres: A i B -> 1
        ("Grup C, Subgrup 2, Categoria d", 3, None),  # obres: D -> 3
        ("Grup C, Subgrup 2, Categoria f", 5, 6),  # obres: F -> 5 o 6
    ],
)
def test_m9_les_lletres_antigues_segons_les_taules_del_rd_773_2015(text, category, category_max):
    (found,) = parse_classifications(text)
    assert (found.category, found.category_max) == (category, category_max)


def test_m9_una_lletra_antiga_de_serveis_que_no_existeix_es_ambigua():
    requirement = mk(RC.BUSINESS_CLASSIFICATION, ["Classificació Grup V, Subgrup 4, Categoria E."])
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.reason == normalize.REASON_CLASSIFICATION_AMBIGUOUS


@pytest.mark.parametrize(
    ("held", "result", "code"),
    [
        ("V-4-5", AR.SATISFIES, ExplanationCode.CLASSIFICATION_MET),
        ("V-4-4", AR.NEEDS_REVIEW, ExplanationCode.CLASSIFICATION_CATEGORY_AMBIGUOUS),
        ("V-4-3", AR.DOES_NOT_SATISFY, ExplanationCode.CLASSIFICATION_CATEGORY_BELOW),
    ],
)
def test_m9_entre_les_dues_equivalencies_d_una_d_antiga_revisa_una_persona(held, result, code):
    requirement = mk(RC.BUSINESS_CLASSIFICATION, ["Classificació Grup V, Subgrup 4, Categoria D."])
    company = Reg(RegistrationType.BUSINESS_CLASSIFICATION, f"Classificació {held}")
    outcome = _assess(requirement, snap(regs=[company]))
    assert (outcome.result, outcome.explanation_code) == (result, code)


def test_m2_una_disjuncio_on_cap_via_no_aplica_no_bloqueja():
    rows = [
        _row(RC.EXPERIENCE, AR.NOT_APPLICABLE, group="g", complete=True),
        _row(RC.TECHNICAL_SOLVENCY, AR.NOT_APPLICABLE, group="g", complete=True),
    ]
    outcomes = finalize_rows(rows, services=False)
    assert {(o.effective, o.blocker) for o in outcomes.values()} == {
        (AR.NOT_APPLICABLE, BlockerStatus.NONE)
    }


def test_m2_una_via_que_no_aplica_no_compta_com_a_incompliment():
    dns = _row(RC.EXPERIENCE, AR.DOES_NOT_SATISFY, group="g", complete=True)
    na = _row(RC.TECHNICAL_SOLVENCY, AR.NOT_APPLICABLE, group="g", complete=True)
    outcomes = finalize_rows([dns, na], services=False)
    assert (outcomes[dns.key].effective, outcomes[dns.key].blocker) == (
        AR.DOES_NOT_SATISFY,
        BlockerStatus.POTENTIAL,
    )
    assert (outcomes[na.key].effective, outcomes[na.key].blocker) == (
        AR.NOT_APPLICABLE,
        BlockerStatus.NONE,
    )


def test_m2_una_solvencia_que_no_aplica_o_amb_veredicte_huma_no_se_substitueix():
    classification = _row(RC.BUSINESS_CLASSIFICATION, AR.SATISFIES)
    not_applicable = _row(RC.ECONOMIC_SOLVENCY, AR.NOT_APPLICABLE)
    human = _row(RC.TECHNICAL_SOLVENCY, AR.NEEDS_REVIEW, reviewed=AR.DOES_NOT_SATISFY)
    outcomes = finalize_rows([classification, not_applicable, human], services=True)
    assert outcomes[not_applicable.key].effective == AR.NOT_APPLICABLE
    assert outcomes[not_applicable.key].substituted_by is None
    assert outcomes[human.key].effective == AR.DOES_NOT_SATISFY
    assert outcomes[human.key].substituted_by is None


# --------------------------------------------------------------------------- #
# 7. M5: la fila desada, el run i la candidatura diuen el mateix (base de dades) #
# --------------------------------------------------------------------------- #


def _same_counts(counters, run) -> None:
    """Els comptadors de la candidatura son els del run, fila a fila."""
    assert (
        counters.satisfies,
        counters.does_not_satisfy,
        counters.unknown,
        counters.needs_review,
        counters.not_applicable,
        counters.mandatory_total,
        counters.potential_blockers,
        counters.confirmed_blockers,
    ) == (
        run.satisfies_count,
        run.does_not_satisfy_count,
        run.unknown_count,
        run.needs_review_count,
        run.not_applicable_count,
        run.mandatory_total,
        run.potential_blockers_count,
        run.confirmed_blockers_count,
    )


async def test_servei_m5_en_serveis_la_classificacio_es_desa_com_a_informativa(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    from app.services.bid_workspace import compliance_counters

    company = await company_of(db, tenant_a)
    tender.contract_type = ContractType.SERVICES
    await _revenue(db, company, "2000000")
    await add_requirement(db, extraction_run, tender, value={"amount": 450000, "currency": "EUR"})
    classification = await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.BUSINESS_CLASSIFICATION,
        value={"group": "V", "subgroup": "4", "category": "1"},
        title="Classificació",
    )

    run = (await analyze_company_compliance(db, company, tender)).run

    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    stored = rows[classification.id]
    assert stored.obligation == RequirementObligation.INFORMATIVE
    assert stored.explanation_params["own_obligation"] == "mandatory"
    assert stored.blocker_status == BlockerStatus.NONE
    counters = await compliance_counters(db, run, None)
    _same_counts(counters, run)
    assert counters.mandatory_unresolved == 0


async def test_servei_m5_si_la_classificacio_no_s_acredita_la_solvencia_torna_a_ser_la_seva(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    from app.services.bid_workspace import compliance_counters

    company = await company_of(db, tenant_a)
    tender.contract_type = ContractType.SERVICES
    db.add(
        CompanyRegistration(
            company_id=company.id,
            type=RegistrationType.BUSINESS_CLASSIFICATION,
            name="Classificació empresarial",
            registration_number="V-4-1",
        )
    )
    await _revenue(db, company, "100000")
    solvency = await add_requirement(
        db, extraction_run, tender, value={"amount": 900000, "currency": "EUR"}, title="Volum"
    )
    classification = await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.BUSINESS_CLASSIFICATION,
        value={"group": "V", "subgroup": "4", "category": "1"},
        title="Classificació alternativa",
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    own_explanation = rows[solvency.id].explanation_params["substitution"]["own_explanation"]

    user, _ = tenant_a
    await review_assessment(
        db,
        rows[classification.id],
        reviewed_result=AssessmentResult.DOES_NOT_SATISFY,
        review_status=ReviewStatus.VERIFIED,
        note="La classificació no es pot aportar: el certificat és d'una altra empresa.",
        user_id=user.id,
    )
    await db.refresh(run)
    restored = await db.get(CompanyRequirementAssessment, rows[solvency.id].id)
    await db.refresh(restored)
    # La fila diu el mateix que el recompte: el veredicte propi i el seu bloqueig.
    assert restored.automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert restored.explanation_code == own_explanation
    assert "substitution" not in restored.explanation_params
    assert restored.blocker_status == BlockerStatus.POTENTIAL
    assert run.potential_blockers_count == 1
    _same_counts(await compliance_counters(db, run, None), run)


async def test_servei_m5_una_via_sense_revisar_es_desa_amb_el_que_decideix_el_grup(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    from app.services.bid_workspace import compliance_counters

    company = await company_of(db, tenant_a)
    await _revenue(db, company, "100000")
    await _alternative(db, extraction_run, tender, 900000, "Via A")
    await _alternative(db, extraction_run, tender, 800000, "Via B")
    run = (await analyze_company_compliance(db, company, tender)).run
    rows = await _assessments(db, run.id)
    blocking = next(a for a in rows if a.blocker_status == BlockerStatus.POTENTIAL)
    other = next(a for a in rows if a is not blocking)

    user, _ = tenant_a
    await review_assessment(
        db,
        other,
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Acreditat per la via B amb el certificat de l'any 2024.",
        user_id=user.id,
    )
    await db.refresh(run)
    await db.refresh(blocking)
    assert blocking.automatic_result == AssessmentResult.SATISFIES
    assert blocking.explanation_code == "ALTERNATIVE_GROUP_SATISFIED"
    assert blocking.explanation_params["branch_result"] == "does_not_satisfy"
    _same_counts(await compliance_counters(db, run, None), run)


async def test_servei_m7_un_run_d_una_versio_anterior_es_recompta_com_es_va_calcular(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    """Revisar una fila d'un run v5 no hi aplica les regles de la v6 (troballa m7)."""
    company = await company_of(db, tenant_a)
    tender.contract_type = ContractType.SERVICES
    db.add(
        CompanyRegistration(
            company_id=company.id,
            type=RegistrationType.BUSINESS_CLASSIFICATION,
            name="Classificació empresarial",
            registration_number="V-4-1",
        )
    )
    await _revenue(db, company, "100000")
    solvency = await add_requirement(
        db, extraction_run, tender, value={"amount": 900000, "currency": "EUR"}, title="Volum"
    )
    classification = await add_requirement(
        db,
        extraction_run,
        tender,
        category=RequirementCategory.BUSINESS_CLASSIFICATION,
        value={"group": "V", "subgroup": "4", "category": "1"},
        title="Classificació alternativa",
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    # Com ho hauria desat la v5: la solvencia amb el seu «no compleix» i el bloqueig.
    stale = rows[solvency.id]
    stale.automatic_result = AssessmentResult.DOES_NOT_SATISFY
    stale.blocker_status = BlockerStatus.POTENTIAL
    stale.explanation_params = {}
    run.rules_version = "company-compliance-v5"
    await db.commit()

    user, _ = tenant_a
    await review_assessment(
        db,
        rows[classification.id],
        reviewed_result=None,
        review_status=ReviewStatus.VERIFIED,
        note=None,
        user_id=user.id,
    )
    await db.refresh(run)
    await db.refresh(stale)
    assert stale.automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert stale.blocker_status == BlockerStatus.POTENTIAL
    assert run.potential_blockers_count == 1


# --------------------------------------------------------------------------- #
# 8. Segona revisio independent (22-10-2026)                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "quote",
    [
        "Certificats ISO 9001 i, si escau, ISO 14001.",
        "Certificado ISO 9001 y, en su caso, ISO 14001.",
        "Certificats ISO 9001 i, preferentment, ISO 14001.",
        "Certificat ISO 9001 (obligatori) i ISO 14001 (es valorarà).",
    ],
)
def test_n3_una_norma_condicional_o_puntuable_no_es_llegeix_com_a_exigida(quote):
    requirement = mk(RC.CERTIFICATION, [quote])
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.reason == normalize.REASON_CERTIFICATION_CONDITIONAL
    outcome = _assess(requirement, snap(certs=[Cert("ISO 9001", expires_at=VALID)]))
    assert outcome.result == AR.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("quote", "names", "all_of"),
    [
        ("Caldrà disposar de la ISO 9001 o la 14001.", ["ISO 9001", "ISO 14001"], False),
        ("ISO 9001 o bé 14001.", ["ISO 9001", "ISO 14001"], False),
        ("ISO 9001 i també 14001.", ["ISO 9001", "ISO 14001"], True),
        (
            "Certificats ISO 9001 i 14001, 5000 hores de servei anual.",
            ["ISO 9001", "ISO 14001"],
            True,
        ),
    ],
)
def test_n5_un_numero_sol_es_norma_en_llista_pero_no_amb_unitat(quote, names, all_of):
    value = normalize.normalize_requirement(mk(RC.CERTIFICATION, [quote]), PV2).value_json
    assert (value["names"], value["all_of"]) == (names, all_of)


def test_m2_un_altre_esquema_en_una_cita_tambe_compta_si_la_iso_ve_del_titol():
    requirement = mk(RC.CERTIFICATION, ["Certificat EMAS vigent."], title="Certificat ISO 14001")
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.reason == normalize.REASON_CERTIFICATION_OTHER_SCHEME
    outcome = _assess(requirement, snap(certs=[Cert("EMAS", expires_at=VALID)]))
    assert outcome.result == AR.NEEDS_REVIEW


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Classificació: Grup C, subgrups 2 i 4, categoria 3.", [("c", "2", 3), ("c", "4", 3)]),
        ("Grup C, Subgrup 2 i Subgrup 4, Categoria 3", [("c", "2", 3), ("c", "4", 3)]),
        ("Grup V, subgrups 4 i 5, categoria 2", [("v", "4", 2), ("v", "5", 2)]),
        ("Grup C, Subgrup 2 o 4, categoria 3", [("c", "2", 3), ("c", "4", 3)]),
    ],
)
def test_n4_els_subgrups_coordinats_son_diverses_classificacions(text, expected):
    """Mai un grup «I» fantasma ni una classificacio a mitges: cadascun, i el plec,
    que en dona diverses, va a revisio."""
    found = [(c.group, c.subgroup, c.category) for c in parse_classifications(text)]
    assert found == expected
    requirement = mk(RC.BUSINESS_CLASSIFICATION, [text])
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.reason == normalize.REASON_CLASSIFICATION_AMBIGUOUS


def test_n8_un_registre_retirat_no_fa_el_veredicte_mes_dur_que_no_tenir_ne():
    requirement = mk(RC.REGISTRATION, ["Inscripció al RELI o al ROLECE vigent."])
    retired = Reg(RegistrationType.ROLECE, "ROLECE", lifecycle=KnowledgeLifecycle.RETIRED)
    incomplete = _assess(requirement, snap(regs=[retired], complete=False))
    assert (incomplete.result, incomplete.explanation_code) == (
        AR.UNKNOWN,
        ExplanationCode.REGISTRATION_DATA_INCOMPLETE,
    )
    complete = _assess(requirement, snap(regs=[retired]))
    assert complete.explanation_code == ExplanationCode.REGISTRATION_NOT_CURRENT


def test_n8_una_classificacio_vigent_illegible_passa_per_davant_d_una_retirada():
    requirement = mk(
        RC.BUSINESS_CLASSIFICATION, ["Classificació: Grup V, Subgrup 4, Categoria 1."]
    )
    regs = [
        Reg(
            RegistrationType.BUSINESS_CLASSIFICATION,
            "Classificació V-4-2",
            lifecycle=KnowledgeLifecycle.RETIRED,
        ),
        Reg(RegistrationType.BUSINESS_CLASSIFICATION, "Certificat de classificació"),
    ]
    outcome = _assess(requirement, snap(regs=regs))
    assert (outcome.result, outcome.explanation_code) == (
        AR.NEEDS_REVIEW,
        ExplanationCode.CLASSIFICATION_COMPANY_UNREADABLE,
    )


def test_n10_una_disjuncio_on_cap_via_no_aplica_no_bloqueja_encara_que_no_sigui_completa():
    rows = [
        _row(RC.EXPERIENCE, AR.NOT_APPLICABLE, group="g"),
        _row(RC.TECHNICAL_SOLVENCY, AR.NOT_APPLICABLE, group="g"),
    ]
    outcomes = finalize_rows(rows, services=False)
    assert {(o.effective, o.blocker) for o in outcomes.values()} == {
        (AR.NOT_APPLICABLE, BlockerStatus.NONE)
    }


def test_n2_una_confirmacio_nomes_respon_del_resultat_que_va_confirmar():
    """Un «compleix» confirmat no pot acabar sent un bloqueig confirmat."""
    confirmed = RowState(
        key=uuid.uuid4(),
        category=RC.ECONOMIC_SOLVENCY,
        tender_lot_id=None,
        obligation=RequirementObligation.MANDATORY,
        modality=RequirementModality.UNKNOWN,
        requirement_verified=False,
        alternative_group=None,
        group_complete=None,
        own=AR.DOES_NOT_SATISFY,
        assessment_reviewed=True,
        confirmed=AR.SATISFIES,
    )
    assert confirmed.reviewed_for(AR.SATISFIES)
    assert not confirmed.reviewed_for(AR.DOES_NOT_SATISFY)
    outcome = finalize_rows([confirmed], services=True)[confirmed.key]
    assert (outcome.effective, outcome.blocker) == (AR.DOES_NOT_SATISFY, BlockerStatus.POTENTIAL)


def test_un_veredicte_huma_d_una_via_mana_sobre_la_seva_fila():
    """La fila desada es la que compta: dins d'un grup acreditat per una altra via, el
    «no compleix» d'una persona compta com a tal (sense bloqueig)."""
    human = _row(RC.EXPERIENCE, AR.NEEDS_REVIEW, group="g", reviewed=AR.DOES_NOT_SATISFY)
    other = _row(RC.TECHNICAL_SOLVENCY, AR.SATISFIES, group="g")
    outcomes = finalize_rows([human, other], services=False)
    assert (outcomes[human.key].effective, outcomes[human.key].blocker) == (
        AR.DOES_NOT_SATISFY,
        BlockerStatus.NONE,
    )
    assert outcomes[other.key].effective == AR.SATISFIES


async def _services_classification(db, tenant_a, tender_, extraction_run_):
    company = await company_of(db, tenant_a)
    tender_.contract_type = ContractType.SERVICES
    db.add(
        CompanyRegistration(
            company_id=company.id,
            type=RegistrationType.BUSINESS_CLASSIFICATION,
            name="Classificació empresarial",
            registration_number="V-4-1",
        )
    )
    await _revenue(db, company, "100000")
    solvency = await add_requirement(
        db, extraction_run_, tender_, value={"amount": 900000, "currency": "EUR"}, title="Volum"
    )
    classification = await add_requirement(
        db,
        extraction_run_,
        tender_,
        category=RequirementCategory.BUSINESS_CLASSIFICATION,
        value={"group": "V", "subgroup": "4", "category": "1"},
        title="Classificació",
    )
    return company, solvency, classification


async def test_servei_n1_recalcular_conserva_la_revisio_d_una_classificacio_en_serveis(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company, solvency, classification = await _services_classification(
        db, tenant_a, tender, extraction_run
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    user, _ = tenant_a
    await review_assessment(
        db,
        rows[classification.id],
        reviewed_result=AssessmentResult.DOES_NOT_SATISFY,
        review_status=ReviewStatus.VERIFIED,
        note="El certificat de classificació és d'una altra empresa.",
        user_id=user.id,
    )

    again = (await analyze_company_compliance(db, company, tender, force=True)).run

    rows = {a.requirement_id: a for a in await _assessments(db, again.id)}
    carried = rows[classification.id]
    assert carried.reviewed_result == AssessmentResult.DOES_NOT_SATISFY
    assert "review_not_carried" not in (carried.explanation_params or {})
    assert rows[solvency.id].automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert again.potential_blockers_count == 1
    assert again.recommendation == RecommendationResult.REVIEW


async def test_servei_n2_una_confirmacio_del_que_era_una_substitucio_no_confirma_cap_bloqueig(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company, solvency, classification = await _services_classification(
        db, tenant_a, tender, extraction_run
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    user, _ = tenant_a
    # «Confirma» el que diu el sistema: «compleix» (substituida per la classificacio).
    await review_assessment(
        db,
        rows[solvency.id],
        reviewed_result=None,
        review_status=ReviewStatus.VERIFIED,
        note=None,
        user_id=user.id,
    )
    await review_assessment(
        db,
        rows[classification.id],
        reviewed_result=AssessmentResult.DOES_NOT_SATISFY,
        review_status=ReviewStatus.VERIFIED,
        note="El certificat de classificació és d'una altra empresa.",
        user_id=user.id,
    )
    await db.refresh(run)
    stale = await db.get(CompanyRequirementAssessment, rows[solvency.id].id)
    await db.refresh(stale)
    assert stale.automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert stale.blocker_status == BlockerStatus.POTENTIAL
    assert stale.review_status == ReviewStatus.UNREVIEWED
    dropped = stale.explanation_params["review_not_carried"]
    assert (dropped["reviewed_against"], dropped["reason"]) == ("satisfies", "shown_changed")
    assert run.confirmed_blockers_count == 0


async def test_servei_n6_recalcular_conserva_la_confirmacio_d_una_via_acreditada_pel_grup(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company = await company_of(db, tenant_a)
    await _revenue(db, company, "100000")
    a_req = await _alternative(db, extraction_run, tender, 900000, "Via A")
    b_req = await _alternative(db, extraction_run, tender, 800000, "Via B")
    run = (await analyze_company_compliance(db, company, tender)).run
    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    user, _ = tenant_a
    await review_assessment(
        db,
        rows[b_req.id],
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Acreditat per la via B.",
        user_id=user.id,
    )
    a = await db.get(CompanyRequirementAssessment, rows[a_req.id].id)
    await db.refresh(a)
    assert a.automatic_result == AssessmentResult.SATISFIES  # pel grup
    await review_assessment(
        db, a, reviewed_result=None, review_status=ReviewStatus.VERIFIED, note=None,
        user_id=user.id,
    )

    again = (await analyze_company_compliance(db, company, tender, force=True)).run

    carried = {x.requirement_id: x for x in await _assessments(db, again.id)}[a_req.id]
    assert carried.review_status == ReviewStatus.VERIFIED
    assert carried.automatic_result == AssessmentResult.SATISFIES
    assert "review_not_carried" not in (carried.explanation_params or {})


async def test_servei_n7_els_comptadors_son_els_del_run_encara_que_el_requisit_canvii(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    from app.services.bid_workspace import compliance_counters

    company = await company_of(db, tenant_a)
    await _revenue(db, company, "100000")
    requirements = [
        await _alternative(db, extraction_run, tender, 900000, "Via A"),
        await _alternative(db, extraction_run, tender, 800000, "Via B"),
    ]
    run = (await analyze_company_compliance(db, company, tender)).run
    for requirement in requirements:
        requirement.review_status = ReviewStatus.VERIFIED
    await db.commit()
    _same_counts(await compliance_counters(db, run, None), run)


async def test_servei_n9_una_fila_que_deixa_de_ser_especial_torna_al_que_diu_sola(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    """El contracte passa a obres: la classificacio torna a obligar i la solvencia ja no
    es substitueix (en el recompte seguent)."""
    company, solvency, classification = await _services_classification(
        db, tenant_a, tender, extraction_run
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    assert rows[classification.id].obligation == RequirementObligation.INFORMATIVE
    tender.contract_type = ContractType.WORKS
    await db.commit()

    user, _ = tenant_a
    await review_assessment(
        db,
        rows[classification.id],
        reviewed_result=None,
        review_status=ReviewStatus.VERIFIED,
        note=None,
        user_id=user.id,
    )
    restored = {a.requirement_id: a for a in await _assessments(db, run.id)}
    for row in restored.values():
        await db.refresh(row)
    assert restored[classification.id].obligation == RequirementObligation.MANDATORY
    assert "own_obligation" not in restored[classification.id].explanation_params
    assert restored[solvency.id].automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert "substitution" not in restored[solvency.id].explanation_params
    assert restored[solvency.id].blocker_status == BlockerStatus.POTENTIAL


# --------------------------------------------------------------------------- #
# 9. Tercera revisio independent (22-10-2026)                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "quote",
    [
        "Certificats ISO 9001 i, si cal, ISO 14001.",
        "Certificats ISO 9001 i, quan escaigui, ISO 14001.",
        "Certificado ISO 9001 y, si fuera necesario, ISO 14001.",
        "Certificat ISO 9001 i ISO 14001 (voluntari).",
        "Certificado ISO 9001 e ISO 14001 (facultativo).",
        "Certificat ISO 9001 i ISO 14001 (aquest darrer, com a millora).",
    ],
)
def test_p1_mes_formes_condicionals_no_es_llegeixen_com_a_exigides(quote):
    requirement = mk(RC.CERTIFICATION, [quote])
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.reason == normalize.REASON_CERTIFICATION_CONDITIONAL


@pytest.mark.parametrize(
    "quote",
    ["Certificat ISO 9001 de servei puntual.", "Certificat ISO 9001 (no puntuable)."],
)
def test_n3_puntual_o_no_puntuable_no_son_condicionals(quote):
    value = normalize.normalize_requirement(mk(RC.CERTIFICATION, [quote]), PV2).value_json
    assert value["names"] == ["ISO 9001"]


@pytest.mark.parametrize(
    "quote",
    [
        "Certificats ISO 9001 de gestió de la qualitat i 14001 de gestió ambiental.",
        "Certificat ISO 9001 (qualitat) i 14001 (medi ambient).",
        "Certificación ISO 9001 en vigor y 14001 en vigor.",
        "ISO 9001 o, alternativament, 14001.",
    ],
)
def test_p2_una_norma_sense_iso_que_no_es_pot_lligar_a_la_llista_va_a_revisio(quote):
    """Mai una llista parcial: ni «compleix» ni «no compleix» amb una norma de menys."""
    requirement = mk(RC.CERTIFICATION, [quote])
    normalization = normalize.normalize_requirement(requirement, PV2)
    assert normalization.reason == normalize.REASON_CERTIFICATION_LIST_AMBIGUOUS
    outcome = _assess(requirement, snap(certs=[Cert("ISO 9001", expires_at=VALID)]))
    assert outcome.result == AR.NEEDS_REVIEW


@pytest.mark.parametrize(
    "quote",
    [
        "Clasificación: Grupo V, Subgrupo 4 (Servicios de telecomunicaciones), Categoría 2.",
        "Grup V, subgrup 4: Serveis de telecomunicacions, categoria 2.",
        "Grup V, subgrup 4, categoria 2 i subgrup 5, categoria 1.",
    ],
)
def test_p3_una_classificacio_llegida_a_mitges_va_a_revisio(quote):
    """Una categoria que no es pot llegir o un subgrup fora de patro: ni «compleix» (que
    en serveis substituiria la solvencia) ni «no compleix»."""
    requirement = mk(RC.BUSINESS_CLASSIFICATION, [quote])
    assert normalize.normalize_requirement(requirement, PV2).reason == (
        normalize.REASON_CLASSIFICATION_AMBIGUOUS
    )
    company = Reg(RegistrationType.BUSINESS_CLASSIFICATION, "Classificació V-4-1")
    outcome = _assess(requirement, snap(regs=[company]))
    # n1: dient que el plec en dona diverses, no que no en diu cap.
    assert (outcome.result, outcome.explanation_code) == (
        AR.NEEDS_REVIEW,
        ExplanationCode.CLASSIFICATION_AMBIGUOUS,
    )


def test_p3_el_nom_real_del_registre_d_indaleccius_es_continua_llegint():
    found = parse_classifications(
        "Classificacio empresarial Grup V, subgrup 4, categoria 1 (V-4-1)", context_required=False
    )
    assert [(c.group, c.subgroup, c.category, c.category_invalid) for c in found] == [
        ("v", "4", 1, False)
    ]


async def _services_with_registration(db, tenant_a, tender_, extraction_run_):
    company, solvency, classification = await _services_classification(
        db, tenant_a, tender_, extraction_run_
    )
    registration = await db.scalar(
        sa.select(CompanyRegistration).where(CompanyRegistration.company_id == company.id)
    )
    return company, solvency, classification, registration


async def test_servei_f6_un_compleix_que_nomes_reafirmava_la_substitucio_no_amaga_un_incompliment(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company, solvency, _classification, registration = await _services_with_registration(
        db, tenant_a, tender, extraction_run
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    user, _ = tenant_a
    rows = {a.requirement_id: a for a in await _assessments(db, run.id)}
    await review_assessment(
        db,
        rows[solvency.id],
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Acreditada amb la classificació V-4-1.",
        user_id=user.id,
    )
    await db.delete(registration)  # l'empresa ja no la te
    await db.commit()

    again = (await analyze_company_compliance(db, company, tender, force=True)).run

    row = {a.requirement_id: a for a in await _assessments(db, again.id)}[solvency.id]
    assert row.reviewed_result is None
    assert row.automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert row.blocker_status == BlockerStatus.POTENTIAL
    dropped = row.explanation_params["review_not_carried"]
    assert (dropped["reason"], dropped["reviewed_result"]) == ("shown_changed", "satisfies")
    assert again.recommendation == RecommendationResult.REVIEW


async def test_servei_f6b_un_compleix_que_nomes_reafirmava_el_grup_no_l_acredita_sol(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company = await company_of(db, tenant_a)
    revenue = CompanyFinancialYear(
        company_id=company.id, year=2025, revenue=Decimal("500000"), currency="EUR"
    )
    db.add(revenue)
    await db.commit()
    a_req = await _alternative(db, extraction_run, tender, 900000, "Via A")
    await _alternative(db, extraction_run, tender, 400000, "Via B")
    run = (await analyze_company_compliance(db, company, tender)).run
    a = {x.requirement_id: x for x in await _assessments(db, run.id)}[a_req.id]
    assert a.automatic_result == AssessmentResult.SATISFIES  # per la via B
    user, _ = tenant_a
    await review_assessment(
        db,
        a,
        reviewed_result=AssessmentResult.SATISFIES,
        review_status=ReviewStatus.VERIFIED,
        note="Acreditat (per la via B).",
        user_id=user.id,
    )
    revenue.revenue = Decimal("100000")
    await db.commit()

    again = (await analyze_company_compliance(db, company, tender, force=True)).run

    a2 = {x.requirement_id: x for x in await _assessments(db, again.id)}[a_req.id]
    assert a2.reviewed_result is None
    assert a2.explanation_params["review_not_carried"]["reason"] == "shown_changed"
    assert again.potential_blockers_count == 1


async def test_servei_f1_una_confirmacio_de_la_substitucio_no_es_confirma_en_obres(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company, solvency, _classification = await _services_classification(
        db, tenant_a, tender, extraction_run
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    user, _ = tenant_a
    await review_assessment(
        db,
        {a.requirement_id: a for a in await _assessments(db, run.id)}[solvency.id],
        reviewed_result=None,
        review_status=ReviewStatus.VERIFIED,
        note=None,
        user_id=user.id,
    )
    tender.contract_type = ContractType.WORKS
    await db.commit()

    again = (await analyze_company_compliance(db, company, tender, force=True)).run

    row = {a.requirement_id: a for a in await _assessments(db, again.id)}[solvency.id]
    assert row.automatic_result == AssessmentResult.DOES_NOT_SATISFY
    assert row.review_status == ReviewStatus.UNREVIEWED
    assert row.blocker_status == BlockerStatus.POTENTIAL
    assert again.confirmed_blockers_count == 0


async def test_servei_f2_el_recompte_refa_tambe_el_total_d_obligatoris(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    from app.services.bid_workspace import compliance_counters

    company, _solvency, classification = await _services_classification(
        db, tenant_a, tender, extraction_run
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    tender.contract_type = ContractType.WORKS
    await db.commit()
    user, _ = tenant_a
    await review_assessment(
        db,
        {a.requirement_id: a for a in await _assessments(db, run.id)}[classification.id],
        reviewed_result=None,
        review_status=ReviewStatus.VERIFIED,
        note=None,
        user_id=user.id,
    )
    await db.refresh(run)
    _same_counts(await compliance_counters(db, run, None), run)


async def test_servei_f5_una_fila_amb_veredicte_mostra_el_que_hi_veu_ara_el_sistema(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    """La part automatica d'una fila revisada no es queda enrere: «Confirma» confirma el
    que es mostra de debo i es conserva."""
    company, solvency, classification = await _services_classification(
        db, tenant_a, tender, extraction_run
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    user, _ = tenant_a
    for requirement_id, note in (
        (solvency.id, "La facturació no hi arriba."),
        (classification.id, "Classificació caducada."),
    ):
        await review_assessment(
            db,
            {a.requirement_id: a for a in await _assessments(db, run.id)}[requirement_id],
            reviewed_result=AssessmentResult.DOES_NOT_SATISFY,
            review_status=ReviewStatus.VERIFIED,
            note=note,
            user_id=user.id,
        )
    row = {a.requirement_id: a for a in await _assessments(db, run.id)}[solvency.id]
    await db.refresh(row)
    assert row.automatic_result == AssessmentResult.DOES_NOT_SATISFY  # ja no «substituida»
    await review_assessment(
        db, row, reviewed_result=None, review_status=ReviewStatus.VERIFIED, note=None,
        user_id=user.id,
    )
    await db.refresh(row)
    assert row.review_status == ReviewStatus.VERIFIED
    assert row.blocker_status == BlockerStatus.CONFIRMED


async def test_servei_n2_una_confirmacio_es_conserva_si_es_continua_mostrant_el_mateix(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    company = await company_of(db, tenant_a)
    revenue = CompanyFinancialYear(
        company_id=company.id, year=2025, revenue=Decimal("100000"), currency="EUR"
    )
    db.add(revenue)
    await db.commit()
    a_req = await _alternative(db, extraction_run, tender, 900000, "Via A")
    await _alternative(db, extraction_run, tender, 50000, "Via B")
    run = (await analyze_company_compliance(db, company, tender)).run
    user, _ = tenant_a
    await review_assessment(
        db,
        {x.requirement_id: x for x in await _assessments(db, run.id)}[a_req.id],
        reviewed_result=None,
        review_status=ReviewStatus.VERIFIED,
        note=None,
        user_id=user.id,
    )
    revenue.revenue = Decimal("1000000")  # ara A tambe compleix tota sola
    await db.commit()

    again = (await analyze_company_compliance(db, company, tender, force=True)).run

    a2 = {x.requirement_id: x for x in await _assessments(db, again.id)}[a_req.id]
    assert a2.review_status == ReviewStatus.VERIFIED
    assert "review_not_carried" not in (a2.explanation_params or {})


async def test_servei_f3_una_fila_que_canvia_de_mena_desfa_la_reescriptura_d_abans(
    db, tenant_a, tender, extraction_run  # noqa: F811 - fixtures importades
):
    """Una solvencia substituida que passa a ser una via alternativa (requisit manual
    editat per API) no guarda la substitucio com a resultat propi de la via."""
    from app.services.bid_workspace import compliance_counters

    company, solvency, classification = await _services_classification(
        db, tenant_a, tender, extraction_run
    )
    run = (await analyze_company_compliance(db, company, tender)).run
    solvency.alternative_group = "g"
    solvency.modality = RequirementModality.ALTERNATIVE
    await db.commit()
    user, _ = tenant_a
    for _ in range(2):
        await review_assessment(
            db,
            {a.requirement_id: a for a in await _assessments(db, run.id)}[classification.id],
            reviewed_result=None,
            review_status=ReviewStatus.VERIFIED,
            note=None,
            user_id=user.id,
        )
        row = {a.requirement_id: a for a in await _assessments(db, run.id)}[solvency.id]
        await db.refresh(row)
        # Via sola d'un grup que no consta complet: «cal revisar», recompte rere recompte.
        assert row.automatic_result == AssessmentResult.NEEDS_REVIEW
        assert row.explanation_params["branch_result"] == "does_not_satisfy"
        assert "substitution" not in row.explanation_params
    await db.refresh(run)
    _same_counts(await compliance_counters(db, run, None), run)
