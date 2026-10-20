"""Motor: dispatch per categoria i derivacio d'elegibilitat.

Aqui viuen les decisions que mes facil seria equivocar, i per aixo son les mes
explicites del fitxer:

* **Un `unknown` no es un NO-GO.** No saber no es incomplir. Un mandatory sense
  dades demana completar el perfil, no descartar la licitacio.
* **Un `needs_review` tampoc.** Si la regla no es prou determinista, la decisio
  es de la persona.
* **Un blocador d'un requisit no verificat es `POTENTIAL`.** Mentre la qualitat
  de l'extractor real no estigui mesurada, deixar que una extraccio automatica
  provoqui un NO-GO definitiu seria regalar falsos negatius.
* **Els blocadors no es promitgen mai.** Vuit requisits complerts i un
  d'obligatori incomplert no fan un 89 % acceptable: fan un bloqueig.

L'elegibilitat i el pre-fit es mantenen separats a proposit. El pre-fit respon
"ens interessa?"; aixo respon "podem acreditar que complim?". Un 95 de pre-fit
amb un blocador confirmat es un NO-GO, i el 95 no pot amagar-ho.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from app.ai.contract_v3 import VALUE_SCHEMA_V3
from app.compliance import normalize
from app.compliance.certifications import assess_certification, assess_ens
from app.compliance.economic import assess_economic_solvency
from app.compliance.experience import assess_experience
from app.compliance.insurance import assess_insurance
from app.compliance.personnel import assess_personnel, assess_qualification, assess_registration
from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import ExplanationCode, RuleCode, RuleOutcome, needs_review
from app.compliance.specs import is_effectively_mandatory
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    BlockerStatus,
    EligibilityResult,
    RecommendationResult,
    RequirementCategory,
    RequirementObligation,
    RequirementScope,
    ReviewStatus,
)

#: Pre-fit a partir del qual una licitacio elegible es candidata de veritat.
GO_CANDIDATE_PREFIT_THRESHOLD = 60


@dataclass(frozen=True, slots=True)
class _AdaptedRequirement:
    """El requisit amb el valor tradult a la forma que esperen les regles.

    Es un embolcall i no una copia del model: les regles nomes llegeixen uns
    quants atributs, i mutar el requisit real per adaptar-lo seria escriure a
    l'objecte que despres es desa. El complement `product-v2`
    (`app.compliance.normalize`) el fa servir per donar a les regles el valor que
    ha llegit; el requisit de la base de dades no es toca.
    """

    _wrapped: Any
    value_json: dict[str, Any] | None
    value_schema: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def assess_requirement(
    requirement: Any,
    snapshot: ProfileSnapshot,
    *,
    deadline: date | None = None,
    tender: Any = None,
    profile: str | None = None,
) -> RuleOutcome:
    """`assess_requirement_traced` sense el rastre de normalitzacio."""
    outcome, _ = assess_requirement_traced(
        requirement, snapshot, deadline=deadline, tender=tender, profile=profile
    )
    return outcome


def assess_requirement_traced(
    requirement: Any,
    snapshot: ProfileSnapshot,
    *,
    deadline: date | None = None,
    tender: Any = None,
    profile: str | None = None,
) -> tuple[RuleOutcome, normalize.Normalization | None]:
    """Avalua un requisit i diu quina lectura complementaria s'hi ha aplicat.

    `profile` es el perfil d'extraccio del run del requisit (`None` per als
    manuals). El complement (`app.compliance.normalize`) NOMES s'aplica a un
    requisit `product-v2` amb `value_schema` nul; per a tota la resta (v1, v2
    tipat, v3, gold, manuals) aixo es exactament el `_dispatch` de la v4 i no
    porta cap rastre. Res d'aixo escriu al requisit.
    """
    normalization = normalize.normalize_requirement(requirement, profile)
    if normalization is None:
        return _dispatch(requirement, snapshot, deadline=deadline, tender=tender), None

    if normalization.route == normalize.ROUTE_ENS and normalization.ens is not None:
        # L'ENS nomenat: per categories, i sense suposar-ne cap si el plec no la
        # diu (`assess_ens`).
        outcome = assess_ens(normalization.ens, snapshot, deadline)
    elif normalization.route == normalize.ROUTE_REGISTRY_DOCUMENTATION_ONLY:
        # Sembla documentacio que aporta qui JA es inscrit al RELI/ROLECE:
        # avaluar-la com a exigencia d'estar-hi demanaria un registre que potser el
        # plec no demana. Revisio.
        outcome = needs_review(
            RuleCode.REGISTRATION_EXACT, ExplanationCode.REGISTRATION_DOCUMENTATION_ONLY
        )
    elif normalization.route == normalize.ROUTE_NOT_REQUIRED:
        # El plec diu literalment que no s'exigeix (p. ex. «no és necessari
        # disposar de classificació»): no hi ha res a acreditar.
        outcome = RuleOutcome(
            result=AssessmentResult.NOT_APPLICABLE,
            rule_code=RuleCode.REGISTRATION_EXACT,
            explanation_code=ExplanationCode.REQUIREMENT_NOT_REQUIRED,
            mode=AssessmentMode.AUTOMATIC,
        )
    elif normalization.has_value:
        adapted = _AdaptedRequirement(
            requirement,
            value_json=normalization.value_json,
            value_schema=normalization.value_schema,
        )
        outcome = _dispatch(adapted, snapshot, deadline=deadline, tender=tender)
    else:
        outcome = _dispatch(requirement, snapshot, deadline=deadline, tender=tender)

    return (
        replace(
            outcome,
            params={**(outcome.params or {}), "normalization": normalization.explain(profile)},
        ),
        normalization,
    )


def _dispatch(
    requirement: Any,
    snapshot: ProfileSnapshot,
    *,
    deadline: date | None = None,
    tender: Any = None,
) -> RuleOutcome:
    """Tria la regla segons la categoria del requisit.

    Una categoria sense regla determinista NO s'endevina: retorna revisio. Es
    preferible dir "aixo ho ha de mirar algu" que fabricar un veredicte.

    Les regles llegeixen el requisit a traves de `specs`, que unifica la forma
    lliure de la v1 i la tipada de la v2. El que la v2 afegeix i encara no es pot
    resoldre —una base de percentatge que no consta a l'expedient, un sublimit
    que la polissa no declara— surt com a `needs_review` des de la propia regla,
    amb el detall del que falta, i mai com a `does_not_satisfy`.

    El `tender` hi entra perque un llindar en percentatge nomes es comparable si
    se'n pot llegir la base. Sense expedient, aquests requisits queden en
    revisio en comptes de comparar-se contra una base inventada.
    """
    category = requirement.category

    # Extraction Contract v3: el valor son claims, i cap regla encara no els
    # llegeix. Deixar-lo passar per la lectura lliure de la v1 voldria dir
    # avaluar-lo sense el seu llindar. Fins que hi hagi un lector v3, revisio.
    if getattr(requirement, "value_schema", None) == VALUE_SCHEMA_V3:
        return needs_review(
            RuleCode.NOT_AUTOMATABLE,
            ExplanationCode.RULE_NOT_AUTOMATABLE,
            category=str(category),
            value_schema=VALUE_SCHEMA_V3,
        )

    if category == RequirementCategory.ECONOMIC_SOLVENCY:
        return assess_economic_solvency(requirement, snapshot, deadline, tender)
    if category == RequirementCategory.CERTIFICATION:
        return assess_certification(requirement, snapshot, deadline, tender)
    if category == RequirementCategory.INSURANCE:
        return assess_insurance(requirement, snapshot, deadline, tender)
    if category in (RequirementCategory.EXPERIENCE, RequirementCategory.TECHNICAL_SOLVENCY):
        return assess_experience(requirement, snapshot, None, tender)
    if category in (
        RequirementCategory.REGISTRATION,
        RequirementCategory.BUSINESS_CLASSIFICATION,
    ):
        return assess_registration(requirement, snapshot, deadline)
    if category == RequirementCategory.PROFESSIONAL_QUALIFICATION:
        return assess_qualification(requirement, snapshot, deadline)
    if category == RequirementCategory.PERSONNEL:
        return assess_personnel(requirement, snapshot)

    # Terminis, documentacio administrativa, condicions tecniques i de
    # contracte: no son acreditables comparant dades del perfil.
    return needs_review(
        RuleCode.NOT_AUTOMATABLE,
        ExplanationCode.RULE_NOT_AUTOMATABLE,
        category=str(category),
    )


@dataclass(frozen=True, slots=True)
class _ObligationView:
    """Nomes el que cal per decidir si una exigencia obliga de veritat.

    Existeix per no duplicar la regla: `is_effectively_mandatory` es la mateixa
    tant si se li dona un Requirement sencer com si nomes se li donen aquests
    dos camps.
    """

    obligation: RequirementObligation
    modality: Any = None


def applies_to_lot(
    requirement: Any, lot_id: Any | None, *, tender_has_lots: bool = True
) -> bool | None:
    """Diu si el requisit aplica al lot demanat. `None` = no es pot saber.

    Un abast desconegut nomes es ambigu si l'expedient te lots. Si no en te, la
    pregunta "a quin lot aplica?" no existeix, i tractar-la com a ambigua
    silenciaria l'avaluacio real de tots els requisits.
    """
    scope = requirement.scope
    if scope == RequirementScope.TENDER:
        return True
    if scope == RequirementScope.UNKNOWN:
        return True if not tender_has_lots else None
    if requirement.tender_lot_id is None:
        # Diu que es de lot pero no s'ha pogut resoldre quin.
        return None
    if lot_id is None:
        return True
    return requirement.tender_lot_id == lot_id


def derive_blocker_status(
    *,
    obligation: RequirementObligation,
    effective_result: AssessmentResult,
    requirement_review_status: ReviewStatus,
    assessment_reviewed: bool,
    modality: Any = None,
) -> BlockerStatus:
    """Quan un incompliment es un bloqueig, i de quina classe.

    Nomes una exigencia **efectivament obligatoria** amb `does_not_satisfy`
    bloqueja. `unknown` i `needs_review` NO bloquegen mai: demanen accio, no
    descarten.

    "Efectivament obligatoria" exclou el que la v2 sap distingir i la v1 no: una
    via alternativa i una condicio no avaluada no obliguen per si soles, per molt
    que l'obligatorietat digui `mandatory`. El que obliga es la disjuncio
    sencera, i qui la resol es `alternatives.resolve_group`.

    Es `CONFIRMED` quan algu ha validat la font: o be el requisit esta verificat,
    o be una persona ha revisat aquest mateix assessment. Si no, `POTENTIAL`.
    """
    if not is_effectively_mandatory(
        _ObligationView(obligation=obligation, modality=modality)
    ):
        return BlockerStatus.NONE
    if effective_result != AssessmentResult.DOES_NOT_SATISFY:
        return BlockerStatus.NONE
    if requirement_review_status == ReviewStatus.VERIFIED or assessment_reviewed:
        return BlockerStatus.CONFIRMED
    return BlockerStatus.POTENTIAL


def _has_verdict(
    rows: list[tuple[RequirementObligation, AssessmentResult, BlockerStatus]],
) -> bool:
    """Hi ha alguna cosa que sostingui una afirmacio sobre l'empresa?

    Cap fila vol dir que no s'ha analitzat el plec. Totes "no aplicable" vol dir
    que s'ha analitzat i que res del que demana afecta aquesta empresa: tampoc
    s'ha verificat res d'ella. En tots dos casos el veredicte honest es "no ho
    sabem", i no "compleix".
    """
    return any(result != AssessmentResult.NOT_APPLICABLE for _, result, _ in rows)


@dataclass(frozen=True, slots=True)
class EligibilitySummary:
    eligibility: EligibilityResult
    recommendation: RecommendationResult
    satisfies: int = 0
    does_not_satisfy: int = 0
    unknown: int = 0
    needs_review: int = 0
    not_applicable: int = 0
    mandatory_total: int = 0
    potential_blockers: int = 0
    confirmed_blockers: int = 0


def summarise(
    rows: list[tuple[RequirementObligation, AssessmentResult, BlockerStatus]],
    *,
    prefit_score: int | None = None,
) -> EligibilitySummary:
    """Agrega els assessments en elegibilitat i recomanacio.

    Cap mitjana: els comptadors es miren un a un i el bloqueig sempre domina.

    **Sense res avaluat no hi ha veredicte.** Zero requisits donava zero
    bloquejadors i, per la via dels comptadors, `ELIGIBLE`: el sistema deia
    "podeu acreditar que compliu" sense haver llegit ni un plec. Zero
    informacio i zero impediments son coses oposades, i ara es diuen diferent.
    """
    if not _has_verdict(rows):
        return EligibilitySummary(
            eligibility=EligibilityResult.UNDETERMINED,
            recommendation=RecommendationResult.UNDETERMINED,
            not_applicable=sum(
                1 for _, result, _ in rows if result == AssessmentResult.NOT_APPLICABLE
            ),
        )
    counts = dict.fromkeys(AssessmentResult, 0)
    mandatory_total = 0
    potential = confirmed = 0
    mandatory_unresolved = 0

    for obligation, result, blocker in rows:
        counts[result] += 1
        if obligation == RequirementObligation.MANDATORY:
            mandatory_total += 1
            if result in (AssessmentResult.UNKNOWN, AssessmentResult.NEEDS_REVIEW):
                mandatory_unresolved += 1
        if blocker == BlockerStatus.CONFIRMED:
            confirmed += 1
        elif blocker == BlockerStatus.POTENTIAL:
            potential += 1

    if confirmed:
        eligibility = EligibilityResult.NO_GO
    elif potential or mandatory_unresolved:
        eligibility = EligibilityResult.REVIEW_REQUIRED
    else:
        eligibility = EligibilityResult.ELIGIBLE

    if eligibility == EligibilityResult.NO_GO:
        recommendation = RecommendationResult.NO_GO
    elif eligibility == EligibilityResult.REVIEW_REQUIRED:
        recommendation = RecommendationResult.REVIEW
    elif prefit_score is not None and prefit_score < GO_CANDIDATE_PREFIT_THRESHOLD:
        # Legal no vol dir interessant: son dimensions diferents i es mostren
        # per separat.
        recommendation = RecommendationResult.ELIGIBLE_LOW_FIT
    else:
        recommendation = RecommendationResult.GO_CANDIDATE

    return EligibilitySummary(
        eligibility=eligibility,
        recommendation=recommendation,
        satisfies=counts[AssessmentResult.SATISFIES],
        does_not_satisfy=counts[AssessmentResult.DOES_NOT_SATISFY],
        unknown=counts[AssessmentResult.UNKNOWN],
        needs_review=counts[AssessmentResult.NEEDS_REVIEW],
        not_applicable=counts[AssessmentResult.NOT_APPLICABLE],
        mandatory_total=mandatory_total,
        potential_blockers=potential,
        confirmed_blockers=confirmed,
    )


def not_applicable(reason: str = ExplanationCode.SCOPE_NOT_APPLICABLE) -> RuleOutcome:
    return RuleOutcome(
        result=AssessmentResult.NOT_APPLICABLE,
        rule_code=RuleCode.NOT_APPLICABLE,
        explanation_code=reason,
        mode=AssessmentMode.AUTOMATIC,
    )
