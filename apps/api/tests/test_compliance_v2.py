"""4C amb estructures v2: el que ara sap decidir, i el que segueix sense poder.

Cada test d'aqui es una frase que ha de ser demostrable davant d'una persona:

    "Els ultims tres exercicis son 55k / 72k / 68k; el plec exigeix el millor
    >= 60.750; compleix perque el maxim es 72k."

I la meitat son sobre el que NO s'ha de poder afirmar. Un motor de compliment
que s'equivoca cap al SI fa perdre una licitacio per una oferta que no es podia
acreditar; un que s'equivoca cap al NO fa que ni tan sols s'hi presenti. El
segon error es pitjor perque es invisible: ningu no sabra mai que hi hauria
guanyat.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.compliance.alternatives import (
    Branch,
    GroupOutcome,
    effective_result,
    resolve_group,
)
from app.compliance.certifications import assess_certification
from app.compliance.economic import assess_economic_solvency
from app.compliance.engine import derive_blocker_status
from app.compliance.experience import assess_experience
from app.compliance.insurance import assess_insurance
from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import ExplanationCode
from app.compliance.specs import read_insurance
from app.models.enums import (
    AssessmentResult,
    BlockerStatus,
    InsuranceLimitKind,
    InsuranceType,
    ProjectStatus,
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    RequirementOperator,
    ReviewStatus,
)
from app.workspace.checklist import checklist_title

VALUE_V2 = "requirements-value-v2"


# --------------------------------------------------------------------------- #
# Dobles minims del perfil corporatiu                                           #
# --------------------------------------------------------------------------- #


@dataclass
class FakeRequirement:
    category: RequirementCategory = RequirementCategory.ECONOMIC_SOLVENCY
    obligation: RequirementObligation = RequirementObligation.MANDATORY
    modality: RequirementModality = RequirementModality.MANDATORY
    operator: RequirementOperator | None = RequirementOperator.GTE
    value_json: dict[str, Any] | None = None
    value_schema: str | None = VALUE_V2
    equivalence_allowed: bool = False
    alternative_group: str | None = None
    title: str | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    review_status: ReviewStatus = ReviewStatus.VERIFIED


@dataclass
class FakeYear:
    year: int
    revenue: Decimal | None
    currency: str = "EUR"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class FakeLimit:
    kind: InsuranceLimitKind
    amount: Decimal
    currency: str = "EUR"


@dataclass
class FakeInsurance:
    type: InsuranceType = InsuranceType.CIVIL_LIABILITY
    coverage_amount: Decimal | None = None
    currency: str = "EUR"
    expires_at: date | None = None
    limits: list = field(default_factory=list)
    additional_insured: bool | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class FakeProject:
    name: str
    amount: Decimal | None
    end_date: date | None
    currency: str = "EUR"
    status: ProjectStatus = ProjectStatus.COMPLETED
    start_date: date | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    cpvs: list = field(default_factory=list)
    services: list = field(default_factory=list)


@dataclass
class FakeTender:
    estimated_value: Decimal | None = None
    base_budget: Decimal | None = None
    currency: str = "EUR"


def snapshot(**kwargs) -> ProfileSnapshot:
    sections = kwargs.pop("sections", {})
    return ProfileSnapshot(company_id=uuid.uuid4(), section_ratio=sections, **kwargs)


def accredited(snap: ProfileSnapshot, projects: list[FakeProject]) -> ProfileSnapshot:
    """Marca els projectes com a acreditables amb un certificat de bona execucio.

    Sense document, un projecte no compta: no es pot fer valer davant l'organ.
    """
    from app.compliance.profile import DocumentRef
    from app.models.enums import DocumentCategory, LinkedEntityType

    for project in projects:
        snap.documents_by_entity[(LinkedEntityType.COMPANY_PROJECT.value, project.id)] = [
            DocumentRef(
                id=uuid.uuid4(),
                name="Certificat de bona execucio",
                category=DocumentCategory.PROJECT_EVIDENCE,
                expires_at=None,
            )
        ]
    return snap


# --------------------------------------------------------------------------- #
# Solvencia economica: agregacions                                              #
# --------------------------------------------------------------------------- #


def economic(amount: float, *, years: int | None = 3, aggregation: str = "max") -> FakeRequirement:
    return FakeRequirement(
        value_json={
            "kind": "economic_solvency",
            "metric": "annual_turnover",
            "operator": "gte",
            "threshold": {"amount": amount, "currency": "EUR"},
            "reference_period": (
                {"type": "last_n_years", "years": years} if years else {"type": "unknown"}
            ),
            "aggregation": aggregation,
        }
    )


THREE_YEARS = [
    FakeYear(2025, Decimal("55000")),
    FakeYear(2024, Decimal("72000")),
    FakeYear(2023, Decimal("68000")),
]


def test_the_best_of_the_last_three_years_satisfies():
    """L'exemple exacte que ha de ser demostrable davant d'una persona.

    55k / 72k / 68k, el plec exigeix el millor >= 60.750: compleix, i el motiu
    es que el maxim es 72k. La xifra i el seu per que han de sortir tots dos al
    resultat, o l'explicacio no es comprovable.
    """
    profile = snapshot(financial_years=THREE_YEARS)

    outcome = assess_economic_solvency(economic(60750), profile, date(2026, 6, 1))

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_THRESHOLD_MET
    assert outcome.params["actual"] == 72000.0
    assert outcome.params["required"] == 60750.0
    assert outcome.params["aggregation"] == "max"
    assert outcome.params["years_used"] == [2025, 2024, 2023]
    assert outcome.params["revenue_by_year"] == {
        "2025": 55000.0,
        "2024": 72000.0,
        "2023": 68000.0,
    }


def test_the_best_of_the_last_three_years_can_also_fail():
    profile = snapshot(financial_years=THREE_YEARS)

    outcome = assess_economic_solvency(economic(90000), profile, date(2026, 6, 1))

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_THRESHOLD_NOT_MET
    assert outcome.params["actual"] == 72000.0


def test_each_year_is_not_the_same_as_the_best_year():
    """La distincio que la v1 no podia fer, i que canvia el veredicte.

    Amb els mateixos tres exercicis: el millor arriba a 60.750, pero el pitjor
    no. Si "cadascun dels tres" s'hagues resolt com "el millor", el motor diria
    que compleix una empresa que no compleix.
    """
    profile = snapshot(financial_years=THREE_YEARS)

    best = assess_economic_solvency(economic(60750, aggregation="max"), profile, date(2026, 6, 1))
    every = assess_economic_solvency(
        economic(60750, aggregation="each_year"), profile, date(2026, 6, 1)
    )

    assert best.result == AssessmentResult.SATISFIES
    assert every.result == AssessmentResult.DOES_NOT_SATISFY
    assert every.explanation_code == ExplanationCode.ECONOMIC_YEAR_BELOW_THRESHOLD
    assert every.params["failing_year"] == 2025


def test_the_average_and_the_sum_are_computed_when_every_year_is_there():
    profile = snapshot(financial_years=THREE_YEARS)

    average = assess_economic_solvency(
        economic(64000, aggregation="average"), profile, date(2026, 6, 1)
    )
    total = assess_economic_solvency(
        economic(190000, aggregation="cumulative"), profile, date(2026, 6, 1)
    )

    assert average.result == AssessmentResult.SATISFIES
    assert average.params["actual"] == 65000.0
    assert total.result == AssessmentResult.SATISFIES
    assert total.params["actual"] == 195000.0


def test_a_missing_year_makes_an_average_unknowable():
    """Una mitjana a la qual li falta un any no es aquella mitjana.

    Es la diferencia entre no saber i incomplir. Amb 2023 sense registrar, els
    altres dos podrien fer pujar o baixar la mitjana i el motor no ho pot dir.
    """
    profile = snapshot(financial_years=THREE_YEARS[:2])

    outcome = assess_economic_solvency(
        economic(64000, aggregation="average"), profile, date(2026, 6, 1)
    )

    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_PERIOD_INCOMPLETE
    assert outcome.params["years_missing"] == [2023]


def test_a_missing_year_does_not_stop_a_max_that_already_passes():
    """Amb dades incompletes nomes es pot afirmar el SI.

    Si un exercici ja supera el llindar, cap exercici que falti pot desfer-ho.
    Dir `unknown` aqui seria demanar feina a una persona per a res.
    """
    profile = snapshot(financial_years=THREE_YEARS[:2])

    outcome = assess_economic_solvency(economic(60750), profile, date(2026, 6, 1))

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.params["years_missing"] == [2023]


def test_a_missing_year_makes_a_failing_max_unknown():
    """I al reves: si no arriba, l'any que falta podria ser el que compleix."""
    profile = snapshot(financial_years=THREE_YEARS[:2])

    outcome = assess_economic_solvency(economic(90000), profile, date(2026, 6, 1))

    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_PERIOD_INCOMPLETE


def test_a_metric_the_profile_cannot_answer_is_not_guessed():
    """Patrimoni net, fons de maniobra: el perfil nomes te facturacio."""
    requirement = economic(60750)
    requirement.value_json["metric"] = "net_worth"
    profile = snapshot(financial_years=THREE_YEARS)

    outcome = assess_economic_solvency(requirement, profile, date(2026, 6, 1))

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_METRIC_UNSUPPORTED


# --------------------------------------------------------------------------- #
# Percentatges                                                                  #
# --------------------------------------------------------------------------- #


def test_a_percentage_with_a_readable_base_is_computed():
    """El 30% d'un valor estimat que consta a l'expedient si que es un import."""
    requirement = economic(0)
    requirement.value_json["threshold"] = {
        "currency": "EUR",
        "percentage": 30,
        "percentage_base": "estimated_value",
    }
    profile = snapshot(financial_years=THREE_YEARS)

    outcome = assess_economic_solvency(
        requirement, profile, date(2026, 6, 1), FakeTender(estimated_value=Decimal("200000"))
    )

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.params["required"] == 60000.0
    assert outcome.params["percentage"] == 30.0
    assert outcome.params["percentage_base_amount"] == 200000.0
    assert outcome.params["threshold_source"] == "percentage_of_base"


def test_a_percentage_without_a_base_is_never_estimated():
    """"El 70% de l'anualitat mitjana" sense saber la durada del contracte.

    Estimar la base voldria dir estimar el llindar, i despres comparar-hi la
    facturacio d'una empresa de veritat. El resultat sembla una mesura i no ho
    es.
    """
    requirement = economic(0)
    requirement.value_json["threshold"] = {
        "currency": "EUR",
        "percentage": 70,
        "percentage_base": "contract_annuity",
    }
    profile = snapshot(financial_years=THREE_YEARS)

    outcome = assess_economic_solvency(requirement, profile, date(2026, 6, 1), FakeTender())

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.PERCENTAGE_BASE_UNAVAILABLE
    assert outcome.params["unresolved_reason"] == "contract_duration_unknown"


def test_an_absolute_amount_wins_over_its_own_percentage():
    """Quan el plec dona les dues formes, la xifra es la del propi organ.

    "El 70% de l'anualitat mitjana, es a dir 21.406,71 EUR": no cal deduir cap
    base, perque ja esta deduida i signada per qui convoca.
    """
    requirement = economic(0)
    requirement.value_json["threshold"] = {
        "amount": 21406.71,
        "currency": "EUR",
        "percentage": 70,
        "percentage_base": "contract_annuity",
    }
    profile = snapshot(financial_years=THREE_YEARS)

    outcome = assess_economic_solvency(requirement, profile, date(2026, 6, 1), FakeTender())

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.params["required"] == 21406.71
    assert outcome.params["threshold_source"] == "absolute_amount"


# --------------------------------------------------------------------------- #
# Experiencia                                                                   #
# --------------------------------------------------------------------------- #


def experience(**value) -> FakeRequirement:
    return FakeRequirement(
        category=RequirementCategory.TECHNICAL_SOLVENCY,
        value_json={
            "kind": "experience",
            "reference_period": {"type": "last_n_years", "years": 3},
            **value,
        },
    )


def test_the_accumulated_amount_of_the_best_year_is_compared():
    """El cas real del gold: l'any de major execucio, no el trienni sencer.

    Dos contractes de 12.000 el 2025 sumen 24.000 aquell any i superen el
    llindar; sumar-los amb els d'altres anys donaria una xifra mes gran pero
    respondria una pregunta que el plec no fa.
    """
    projects = [
        FakeProject("Neteja A", Decimal("12000"), date(2025, 6, 1)),
        FakeProject("Neteja B", Decimal("12000"), date(2025, 9, 1)),
        FakeProject("Neteja C", Decimal("5000"), date(2024, 3, 1)),
    ]
    profile = accredited(snapshot(projects=projects), projects)

    outcome = assess_experience(
        experience(
            min_accumulated_amount={"amount": 21406.71, "currency": "EUR"},
            aggregation="max",
        ),
        profile,
        date(2026, 6, 1),
    )

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_ACCUMULATED_MET
    assert outcome.params["accumulated"] == 24000.0
    assert outcome.params["amount_by_year"] == {"2024": 5000.0, "2025": 24000.0}


def test_the_accumulated_amount_can_fall_short():
    projects = [FakeProject("Neteja A", Decimal("9000"), date(2025, 6, 1))]
    profile = accredited(
        snapshot(projects=projects, sections={"experience": 1.0}), projects
    )

    outcome = assess_experience(
        experience(
            min_accumulated_amount={"amount": 21406.71, "currency": "EUR"},
            aggregation="max",
        ),
        profile,
        date(2026, 6, 1),
    )

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_ACCUMULATED_NOT_MET
    assert outcome.params["accumulated"] == 9000.0


def test_an_experience_percentage_without_a_base_needs_review():
    projects = [FakeProject("Neteja A", Decimal("90000"), date(2025, 6, 1))]
    profile = accredited(snapshot(projects=projects), projects)

    outcome = assess_experience(
        experience(
            min_accumulated_amount={
                "currency": "EUR",
                "percentage": 70,
                "percentage_base": "contract_annuity",
            },
            aggregation="max",
        ),
        profile,
        date(2026, 6, 1),
        FakeTender(),
    )

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.PERCENTAGE_BASE_UNAVAILABLE
    assert outcome.params["scope"] == "min_accumulated_amount"


def test_free_text_similarity_is_still_never_decided_by_the_engine():
    """"Naturalesa igual o similar" es el que discuteixen les meses."""
    projects = [FakeProject("Neteja A", Decimal("90000"), date(2025, 6, 1))]
    profile = accredited(snapshot(projects=projects), projects)

    outcome = assess_experience(
        experience(
            min_accumulated_amount={"amount": 1000, "currency": "EUR"},
            similarity_note="igual o similar naturaleza",
        ),
        profile,
        date(2026, 6, 1),
    )

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_SIMILARITY_REVIEW


# --------------------------------------------------------------------------- #
# Assegurances: cada limit per separat                                          #
# --------------------------------------------------------------------------- #


def insurance_requirement(*limits: tuple[str, float]) -> FakeRequirement:
    return FakeRequirement(
        category=RequirementCategory.INSURANCE,
        value_json={
            "kind": "insurance",
            "insurance_type": "civil_liability",
            "limits": [
                {"kind": kind, "amount": amount, "currency": "EUR"} for kind, amount in limits
            ],
        },
    )


def test_both_limits_declared_and_sufficient_satisfies():
    policy = FakeInsurance(
        limits=[
            FakeLimit(InsuranceLimitKind.TOTAL, Decimal("1000000")),
            FakeLimit(InsuranceLimitKind.PER_VICTIM, Decimal("400000")),
        ]
    )
    profile = snapshot(insurances=[policy])

    outcome = assess_insurance(
        insurance_requirement(("total", 500000), ("per_victim", 300000)), profile
    )

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMITS_MET
    assert [check["meets"] for check in outcome.params["checked"]] == [True, True]


def test_a_big_total_does_not_answer_an_undeclared_sublimit():
    """L'exemple exacte de l'acceptacio, i el mes facil d'equivocar.

    La polissa te 1.000.000 en total i el plec demana 500.000 en total i 300.000
    per victima. La xifra grossa impressiona i no respon la pregunta: ningu ha
    declarat quin sublimit per victima te aquesta polissa.
    """
    policy = FakeInsurance(
        limits=[FakeLimit(InsuranceLimitKind.TOTAL, Decimal("1000000"))]
    )
    profile = snapshot(insurances=[policy], sections={"insurances": 1.0})

    outcome = assess_insurance(
        insurance_requirement(("total", 500000), ("per_victim", 300000)), profile
    )

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.result != AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_UNKNOWN
    assert outcome.params["undeclared_limits"] == ["per_victim"]


def test_an_explicit_sublimit_below_the_threshold_does_not_satisfy():
    """Aqui si que hi ha dada, i la dada demostra l'incompliment."""
    policy = FakeInsurance(
        limits=[
            FakeLimit(InsuranceLimitKind.TOTAL, Decimal("1000000")),
            FakeLimit(InsuranceLimitKind.PER_VICTIM, Decimal("100000")),
        ]
    )
    profile = snapshot(insurances=[policy])

    outcome = assess_insurance(
        insurance_requirement(("total", 500000), ("per_victim", 300000)), profile
    )

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_BELOW
    assert outcome.params["insufficient_limits"] == ["per_victim"]


def test_an_old_policy_with_a_single_amount_still_answers_the_total():
    """Compatibilitat: una fitxa d'abans nomes tenia `coverage_amount`.

    Aquell camp sempre ha volgut dir la cobertura total, i ha de seguir
    responent-la. El que no pot fer es respondre un sublimit.
    """
    policy = FakeInsurance(coverage_amount=Decimal("800000"))
    profile = snapshot(insurances=[policy])

    total_only = assess_insurance(insurance_requirement(("total", 500000)), profile)
    with_sublimit = assess_insurance(
        insurance_requirement(("total", 500000), ("per_victim", 300000)), profile
    )

    assert total_only.result == AssessmentResult.SATISFIES
    assert with_sublimit.result == AssessmentResult.NEEDS_REVIEW


# --------------------------------------------------------------------------- #
# Vies alternatives                                                             #
# --------------------------------------------------------------------------- #


def test_one_branch_satisfying_settles_the_whole_disjunction():
    """Una via ja acredita el requisit, i el plec en demana una, no totes."""
    resolution = resolve_group(
        [
            Branch("a", AssessmentResult.SATISFIES),
            Branch("b", AssessmentResult.DOES_NOT_SATISFY),
        ]
    )

    assert resolution.outcome == GroupOutcome.SATISFIED
    assert not resolution.blocks
    # I la branca que no complia deixa d'importar.
    assert (
        effective_result(AssessmentResult.DOES_NOT_SATISFY, resolution)
        == AssessmentResult.SATISFIES
    )


def test_a_satisfied_branch_settles_it_even_if_the_group_is_incomplete():
    """Que en falti alguna per trobar no treu valor a la que ja acredita."""
    resolution = resolve_group(
        [Branch("a", AssessmentResult.SATISFIES, group_complete=None)]
    )

    assert resolution.outcome == GroupOutcome.SATISFIED
    assert resolution.complete is False, "no consta que les tinguem totes..."
    assert not resolution.blocks, "...i tot i aixi el requisit esta acreditat"


def test_two_failing_branches_do_not_settle_it_when_completeness_is_unknown():
    """El nucli d'aquest hardening: haver-ne vist dues no vol dir que siguin dues.

    Amb la regla anterior aixo produia un `not_satisfied` i, amb un requisit
    verificat i obligatori, un blocador confirmat. Es a dir: el sistema deia a
    una empresa que no es podia presentar perque fallaven les vies que haviem
    extret, quan el plec podia tenir-ne una tercera que no haviem vist.
    """
    resolution = resolve_group(
        [
            Branch("a", AssessmentResult.DOES_NOT_SATISFY, group_complete=None),
            Branch("b", AssessmentResult.DOES_NOT_SATISFY, group_complete=None),
        ]
    )

    assert resolution.outcome == GroupOutcome.INCOMPLETE
    assert resolution.branches == 2
    assert not resolution.blocks
    assert (
        effective_result(AssessmentResult.DOES_NOT_SATISFY, resolution)
        == AssessmentResult.NEEDS_REVIEW
    )


def test_a_branch_denying_completeness_keeps_the_group_open():
    """Si una branca sap que en falta una altra (la C que no s'ha extret)."""
    resolution = resolve_group(
        [
            Branch("a", AssessmentResult.DOES_NOT_SATISFY, group_complete=False),
            Branch("b", AssessmentResult.DOES_NOT_SATISFY, group_complete=True),
        ]
    )

    assert resolution.outcome == GroupOutcome.INCOMPLETE
    assert resolution.complete is False, "una negacio mana sobre una afirmacio"
    assert not resolution.blocks


def test_every_known_branch_failing_settles_it_only_when_completeness_is_stated():
    """I el contrari: amb la completitud demostrada, si que es pot dir que no.

    La prudencia no pot arribar a no dir mai que no, o la capa seria inutil.
    """
    resolution = resolve_group(
        [
            Branch("a", AssessmentResult.DOES_NOT_SATISFY, group_complete=True),
            Branch("b", AssessmentResult.DOES_NOT_SATISFY),
        ]
    )

    assert resolution.outcome == GroupOutcome.NOT_SATISFIED
    assert resolution.complete is True
    assert resolution.blocks
    assert (
        effective_result(AssessmentResult.DOES_NOT_SATISFY, resolution)
        == AssessmentResult.DOES_NOT_SATISFY
    )


def test_an_unresolved_branch_keeps_the_group_open_even_when_complete():
    """Amb totes les vies representades pero una encara sense resoldre."""
    resolution = resolve_group(
        [
            Branch("a", AssessmentResult.DOES_NOT_SATISFY, group_complete=True),
            Branch("b", AssessmentResult.UNKNOWN),
        ]
    )

    assert resolution.outcome == GroupOutcome.INCOMPLETE
    assert resolution.unresolved == ("b",)
    assert not resolution.blocks


def test_a_single_branch_group_is_never_complete_by_itself():
    """El cas `2026SE00013::class-1`. La completitud no s'infereix del recompte."""
    resolution = resolve_group([Branch("class-1", AssessmentResult.DOES_NOT_SATISFY)])

    assert resolution.outcome == GroupOutcome.INCOMPLETE
    assert resolution.complete is False
    assert not resolution.blocks


@pytest.mark.parametrize(
    "branches",
    [
        pytest.param(
            [Branch("a", AssessmentResult.DOES_NOT_SATISFY)], id="una-branca"
        ),
        pytest.param(
            [
                Branch("a", AssessmentResult.DOES_NOT_SATISFY),
                Branch("b", AssessmentResult.DOES_NOT_SATISFY),
            ],
            id="dues-sense-completitud",
        ),
        pytest.param(
            [
                Branch("a", AssessmentResult.DOES_NOT_SATISFY, group_complete=False),
                Branch("b", AssessmentResult.DOES_NOT_SATISFY),
            ],
            id="completitud-negada",
        ),
        pytest.param(
            [
                Branch("a", AssessmentResult.DOES_NOT_SATISFY, group_complete=True),
                Branch("b", AssessmentResult.UNKNOWN),
            ],
            id="branca-sense-resoldre",
        ),
    ],
)
def test_no_incomplete_group_can_ever_produce_a_confirmed_blocker(branches):
    """La garantia que ho justifica tot, sobre totes les formes d'incompletitud.

    Aqui la modalitat es `mandatory` a proposit: aixi la unica cosa que impedeix
    el blocador es la resolucio del grup, i el test mesura aquesta defensa i no
    la de la modalitat.
    """
    resolution = resolve_group(branches)
    assert resolution.outcome != GroupOutcome.NOT_SATISFIED

    assert (
        derive_blocker_status(
            obligation=RequirementObligation.MANDATORY,
            effective_result=effective_result(AssessmentResult.DOES_NOT_SATISFY, resolution),
            requirement_review_status=ReviewStatus.VERIFIED,
            assessment_reviewed=True,
            modality=RequirementModality.MANDATORY,
        )
        == BlockerStatus.NONE
    )


def test_a_complete_group_that_fails_does_block():
    """Contrapart necessaria: si no, el test anterior no diria res."""
    resolution = resolve_group(
        [
            Branch("a", AssessmentResult.DOES_NOT_SATISFY, group_complete=True),
            Branch("b", AssessmentResult.DOES_NOT_SATISFY),
        ]
    )

    assert (
        derive_blocker_status(
            obligation=RequirementObligation.MANDATORY,
            effective_result=effective_result(AssessmentResult.DOES_NOT_SATISFY, resolution),
            requirement_review_status=ReviewStatus.VERIFIED,
            assessment_reviewed=True,
            modality=RequirementModality.MANDATORY,
        )
        == BlockerStatus.CONFIRMED
    )


# --------------------------------------------------------------------------- #
# Equivalencies                                                                 #
# --------------------------------------------------------------------------- #


@dataclass
class FakeCertification:
    name: str
    expires_at: date | None = None
    verification_status: str = "unverified"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def certification(name: str, *, equivalence: bool) -> FakeRequirement:
    return FakeRequirement(
        category=RequirementCategory.CERTIFICATION,
        equivalence_allowed=equivalence,
        value_json={"kind": "certification", "name": name},
    )


def test_the_exact_certification_satisfies():
    profile = snapshot(certifications=[FakeCertification("ISO 9001:2015")])

    outcome = assess_certification(certification("ISO 9001", equivalence=True), profile)

    assert outcome.result == AssessmentResult.SATISFIES


def test_a_different_certification_with_equivalence_allowed_needs_review():
    """"O equivalent" i l'empresa en te una altra: ho decideix una persona.

    El motor no pot dir que ISO 14001 equival a ISO 9001, i tampoc pot dir que
    no: sense l'exacta, amb equivalents admesos, no hi ha incompliment
    demostrat.
    """
    profile = snapshot(
        certifications=[FakeCertification("ISO 14001")], sections={"certifications": 1.0}
    )

    outcome = assess_certification(certification("ISO 9001", equivalence=True), profile)

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_EQUIVALENCE_REVIEW
    assert outcome.params["company_certifications"] == ["ISO 14001"]


def test_without_equivalence_a_complete_profile_still_settles_it():
    """Sense "o equivalent", el perfil complet segueix demostrant l'absencia."""
    profile = snapshot(
        certifications=[FakeCertification("ISO 14001")], sections={"certifications": 1.0}
    )

    outcome = assess_certification(certification("ISO 9001", equivalence=False), profile)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


# --------------------------------------------------------------------------- #
# Blocadors                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "modality",
    [
        RequirementModality.ALTERNATIVE,
        RequirementModality.CONDITIONAL,
        RequirementModality.OPTIONAL,
        RequirementModality.INFORMATIONAL,
    ],
)
def test_only_an_effectively_mandatory_requirement_can_block(modality):
    assert (
        derive_blocker_status(
            obligation=RequirementObligation.MANDATORY,
            effective_result=AssessmentResult.DOES_NOT_SATISFY,
            requirement_review_status=ReviewStatus.VERIFIED,
            assessment_reviewed=True,
            modality=modality,
        )
        == BlockerStatus.NONE
    )


def test_a_verified_mandatory_failure_is_a_confirmed_blocker():
    """I el contrari: quan hi ha totes les condicions, s'ha de bloquejar.

    La prudencia no pot arribar a l'extrem de no dir mai que no: aixo faria
    inutil tota la capa.
    """
    assert (
        derive_blocker_status(
            obligation=RequirementObligation.MANDATORY,
            effective_result=AssessmentResult.DOES_NOT_SATISFY,
            requirement_review_status=ReviewStatus.VERIFIED,
            assessment_reviewed=False,
            modality=RequirementModality.MANDATORY,
        )
        == BlockerStatus.CONFIRMED
    )


def test_a_v1_requirement_without_modality_still_blocks():
    """Compatibilitat cap enrere: sense modalitat, mana l'obligatorietat."""
    assert (
        derive_blocker_status(
            obligation=RequirementObligation.MANDATORY,
            effective_result=AssessmentResult.DOES_NOT_SATISFY,
            requirement_review_status=ReviewStatus.VERIFIED,
            assessment_reviewed=False,
            modality=None,
        )
        == BlockerStatus.CONFIRMED
    )


@pytest.mark.parametrize(
    "result", [AssessmentResult.UNKNOWN, AssessmentResult.NEEDS_REVIEW]
)
def test_not_knowing_is_never_a_blocker(result):
    assert (
        derive_blocker_status(
            obligation=RequirementObligation.MANDATORY,
            effective_result=result,
            requirement_review_status=ReviewStatus.VERIFIED,
            assessment_reviewed=True,
            modality=RequirementModality.MANDATORY,
        )
        == BlockerStatus.NONE
    )


# --------------------------------------------------------------------------- #
# Assegurat addicional i condicions no comprovades                              #
# --------------------------------------------------------------------------- #


def insurance_with(**conditions) -> FakeRequirement:
    return FakeRequirement(
        category=RequirementCategory.INSURANCE,
        value_json={
            "kind": "insurance",
            "insurance_type": "civil_liability",
            "limits": [{"kind": "total", "amount": 500000, "currency": "EUR"}],
            **conditions,
        },
    )


def _covered(**policy) -> ProfileSnapshot:
    return snapshot(
        insurances=[
            FakeInsurance(
                limits=[FakeLimit(InsuranceLimitKind.TOTAL, Decimal("1000000"))], **policy
            )
        ]
    )


def test_additional_insured_required_and_declared_satisfies():
    outcome = assess_insurance(
        insurance_with(additional_insured=True), _covered(additional_insured=True)
    )

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.params["additional_insured_declared"] is True


def test_additional_insured_explicitly_denied_does_not_satisfy():
    """La polissa diu que no inclou l'organ: aixo si que es un incompliment."""
    outcome = assess_insurance(
        insurance_with(additional_insured=True), _covered(additional_insured=False)
    )

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.INSURANCE_ADDITIONAL_INSURED_MISSING


def test_additional_insured_not_declared_needs_review():
    """No consta no es "no". La cobertura encaixa i tot i aixi no es pot afirmar."""
    outcome = assess_insurance(
        insurance_with(additional_insured=True), _covered(additional_insured=None)
    )

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.result != AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.INSURANCE_ADDITIONAL_INSURED_UNKNOWN


@pytest.mark.parametrize(
    ("condition", "label"),
    [
        ({"must_be_valid_through_contract": True}, "valid_through_contract"),
        ({"validity_note": "durant el periode de garantia"}, "validity_note"),
        ({"proof": "insurance_certificate"}, "proof:insurance_certificate"),
    ],
)
def test_an_unchecked_condition_never_yields_satisfies(condition, label):
    """Els imports encaixen i tot i aixi no es pot dir que compleix.

    Es la regla que impedeix el pitjor error d'aquesta capa: donar per bona una
    polissa perque la xifra grossa quadra, quan el plec exigeix una cosa mes que
    ningu ha comprovat. Amb una condicio sense mirar, `satisfies` seria afirmar
    mes del que s'ha mirat.
    """
    outcome = assess_insurance(insurance_with(**condition), _covered())

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.INSURANCE_CONDITION_NOT_CHECKED
    assert label in outcome.params["unchecked_conditions"]


def test_a_policy_with_no_extra_conditions_still_satisfies():
    """Contrapart: la prudencia no pot deixar la regla sense veredictes."""
    outcome = assess_insurance(insurance_with(), _covered())

    assert outcome.result == AssessmentResult.SATISFIES
    assert "unchecked_conditions" not in outcome.params


def test_a_proved_shortfall_wins_over_an_unchecked_condition():
    """Un incompliment demostrat mana: cap condicio pendent el pot rescatar."""
    requirement = insurance_with(must_be_valid_through_contract=True)
    requirement.value_json["limits"] = [
        {"kind": "total", "amount": 5000000, "currency": "EUR"}
    ]

    outcome = assess_insurance(requirement, _covered())

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_BELOW


def test_every_structured_insurance_condition_is_either_checked_or_reported():
    """Cap camp del contracte v2 pot quedar ignorat en silenci.

    Si algu afegeix una condicio a `InsuranceValue` i no decideix que se'n fa,
    aquest test ho ha de dir: el perill no es que falli, es que passi
    desapercebuda i el motor digui `satisfies` sense haver-la mirat.
    """
    from app.ai.value_schemas import InsuranceValue
    from app.compliance.specs import UNCHECKED_INSURANCE_FIELDS

    #: Camps que la regla SI que comprova, un per un.
    checked = {"kind", "limits", "insurance_type", "insurance_type_note", "additional_insured"}
    reported = {field for field, _label in UNCHECKED_INSURANCE_FIELDS} | {"proof"}

    declared = set(InsuranceValue.model_fields)
    assert declared <= checked | reported, (
        f"condicions sense decidir: {sorted(declared - checked - reported)}"
    )


# --------------------------------------------------------------------------- #
# Un import sense tipus no acredita un sublimit (N3)                            #
# --------------------------------------------------------------------------- #


def v1_insurance(amount: float, type_note: str | None = None) -> FakeRequirement:
    """Un requisit d'assegurança amb la forma EXACTA del contracte v1.

    La v1 no tenia camp per al tipus de limit; quan hi era, viatjava dins de
    `type`. Es la forma que va originar N3 i el perfil per defecte segueix sent
    aquest, aixi que es aqui on la regressio ha de viure.
    """
    return FakeRequirement(
        category=RequirementCategory.INSURANCE,
        value_schema=None,
        value_json={"coverage_amount": amount, "currency": "EUR", "type": type_note},
    )


class TestSublimitSenseTipus:
    """El cas de l'auditoria: 1.000.000 «unknown» davant d'un sublimit de 150.000.

    La polissa declarava una xifra sola i sense tipus; el plec demanava un
    sublimit **per victima**. El sistema comparava els dos numeros i deia
    "compleix". Podia ser perfectament el limit total contra un sublimit, que es
    la comparacio que fa semblar coberta una empresa que no ho esta.
    """

    def test_el_cas_exacte_de_lauditoria_ja_no_diu_compleix(self):
        # Tal com estava a la base de dades: un sol import, sense tipus.
        policy = FakeInsurance(coverage_amount=Decimal("1000000"))
        profile = snapshot(insurances=[policy])

        outcome = assess_insurance(
            v1_insurance(150000, "civil_liability_per_victim"), profile
        )

        assert outcome.result == AssessmentResult.NEEDS_REVIEW

    def test_el_qualificador_del_camp_legacy_es_conserva(self):
        """`civil_liability_per_victim` diu de quin limit parla. No es perd."""
        spec = read_insurance(v1_insurance(150000, "civil_liability_per_victim"))

        assert [limit.kind for limit in spec.limits] == [InsuranceLimitKind.PER_VICTIM]

    def test_un_sublimit_explicit_i_suficient_si_que_compleix(self):
        """La correccio no pot fer-nos cecs al cas que si que s'acredita."""
        policy = FakeInsurance(
            limits=[
                FakeLimit(InsuranceLimitKind.TOTAL, Decimal("1000000")),
                FakeLimit(InsuranceLimitKind.PER_VICTIM, Decimal("300000")),
            ]
        )
        profile = snapshot(insurances=[policy])

        outcome = assess_insurance(
            v1_insurance(150000, "civil_liability_per_victim"), profile
        )

        assert outcome.result == AssessmentResult.SATISFIES

    def test_un_sublimit_explicit_i_insuficient_es_un_incompliment(self):
        """Aixo si que es demostrable: consta, i no arriba."""
        policy = FakeInsurance(
            limits=[FakeLimit(InsuranceLimitKind.PER_VICTIM, Decimal("50000"))]
        )
        profile = snapshot(insurances=[policy])

        outcome = assess_insurance(
            v1_insurance(150000, "civil_liability_per_victim"), profile
        )

        assert outcome.result == AssessmentResult.DOES_NOT_SATISFY

    def test_un_sublimit_que_no_sabem_mapar_no_es_compara_amb_res(self):
        """El plec parla "por cada victima" i el patro no ho sap mapar.

        Aquest es el cas que de veritat es perillos: el qualificador hi es —el
        plec NO parla de la xifra global— pero no el sabem llegir. Comparar-lo
        contra el total de la polissa seria l'aplanament de sempre, amb una capa
        de pintura. Es demana revisio i s'hi diu per que.
        """
        policy = FakeInsurance(coverage_amount=Decimal("1000000"))
        profile = snapshot(insurances=[policy])

        outcome = assess_insurance(
            v1_insurance(150000, "responsabilidad civil por cada victima"), profile
        )

        assert outcome.result == AssessmentResult.NEEDS_REVIEW
        assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_KIND_AMBIGUOUS

    def test_la_xifra_global_de_sempre_segueix_responent_la_xifra_global(self):
        """Compatibilitat: quan cap dels dos costats parla de sublimits.

        `coverage_amount` vol dir el mateix als dos costats des de la v1. Aqui no
        s'esta comparant un total contra un sublimit: s'esta comparant la
        cobertura amb la cobertura, i degradar-ho a revisio seria soroll a
        l'unic cas d'assegurança que es dona sempre.
        """
        policy = FakeInsurance(coverage_amount=Decimal("1000000"))
        profile = snapshot(insurances=[policy])

        outcome = assess_insurance(
            v1_insurance(600000, "responsabilidad civil"), profile
        )

        assert outcome.result == AssessmentResult.SATISFIES

    def test_un_total_declarat_si_que_respon_un_import_sense_tipus(self):
        """Si la polissa s'ha molestat a dir "total", ja no estem endevinant."""
        policy = FakeInsurance(
            limits=[FakeLimit(InsuranceLimitKind.TOTAL, Decimal("1000000"))]
        )
        profile = snapshot(insurances=[policy])

        outcome = assess_insurance(v1_insurance(150000, "civil_liability"), profile)

        assert outcome.result == AssessmentResult.SATISFIES

    def test_un_sublimit_que_no_sabem_llegir_demana_revisio(self):
        """El plec anuncia un sublimit i no diu de quin: no s'endevina."""
        policy = FakeInsurance(
            limits=[FakeLimit(InsuranceLimitKind.TOTAL, Decimal("1000000"))]
        )
        profile = snapshot(insurances=[policy])

        outcome = assess_insurance(v1_insurance(150000, "sublimite especial"), profile)

        assert outcome.result == AssessmentResult.NEEDS_REVIEW
        assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_KIND_AMBIGUOUS

    def test_el_cas_tipat_v2_es_comporta_igual(self):
        """Mateixa semantica pel cami v2, perque no en divergeixin dos."""
        policy = FakeInsurance(coverage_amount=Decimal("1000000"))
        profile = snapshot(insurances=[policy])

        outcome = assess_insurance(insurance_requirement(("per_victim", 150000)), profile)

        assert outcome.result == AssessmentResult.NEEDS_REVIEW

    def test_no_es_combinen_imports_de_polisses_diferents(self):
        """Dues polisses no es sumen per fabricar-ne una que compleixi."""
        total_only = FakeInsurance(
            limits=[FakeLimit(InsuranceLimitKind.TOTAL, Decimal("1000000"))]
        )
        victim_only = FakeInsurance(
            limits=[FakeLimit(InsuranceLimitKind.PER_VICTIM, Decimal("300000"))]
        )
        profile = snapshot(insurances=[total_only, victim_only])

        outcome = assess_insurance(
            insurance_requirement(("total", 900000), ("per_victim", 200000)), profile
        )

        # Cap de les dues polisses respon les dues condicions tota sola.
        assert outcome.result == AssessmentResult.NEEDS_REVIEW


# --------------------------------------------------------------------------- #
# Els titols derivats conserven el qualificador (N5)                            #
# --------------------------------------------------------------------------- #


def _insurance(amount: int, type_note: str | None) -> FakeRequirement:
    return FakeRequirement(
        category=RequirementCategory.INSURANCE,
        value_json={"coverage_amount": amount, "currency": "EUR", "type": type_note},
        operator=RequirementOperator.GTE,
    )


class TestQualificadorsAlsTitols:
    """Un limit total i un sublimit per victima son condicions de la MATEIXA
    polissa. Anomenar-los tots dos "Aportar polissa RC >= X" els convertia en
    dues polisses d'imports diferents, i qui llegis la checklist aniria a buscar
    dos contractes en comptes d'un.
    """

    def test_el_sublimit_per_victima_es_diu_pel_seu_nom(self):
        title = checklist_title(_insurance(150000, "civil_liability_per_victim"))

        assert "sublimit per victima" in title
        assert "150.000" in title

    def test_el_limit_total_tambe(self):
        title = checklist_title(_insurance(600000, "responsabilidad civil limite total"))

        assert "limit total" in title

    def test_els_dos_titols_no_es_confonen(self):
        """El cas exacte de l'auditoria: dues files identiques tret de la xifra."""
        total = checklist_title(_insurance(600000, "responsabilidad civil limite total"))
        victim = checklist_title(_insurance(150000, "civil_liability_per_victim"))

        assert total != victim
        assert "sublimit" not in total
        assert "sublimit" in victim

    def test_sense_qualificador_no_se_nhi_inventa_cap(self):
        """Si el tipus segueix sent desconegut, el titol calla."""
        title = checklist_title(_insurance(600000, "civil_liability"))

        assert title == "Aportar polissa RC " + chr(0x2265) + " 600.000 " + chr(0x20AC)

    def test_un_sublimit_illegible_es_diu_que_esta_per_determinar(self):
        title = checklist_title(_insurance(90000, "responsabilidad civil por cada victima"))

        assert "per determinar" in title
