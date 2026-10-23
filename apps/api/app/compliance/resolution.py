"""Exigencies que es resolen entre diverses files (v6): disjuncions i classificacio.

Dues situacions on el veredicte d'una fila no es pot llegir tot sol:

* **Vies alternatives** (`alternative_group`). «A o B» es UNA exigencia. Si cap via
  no l'acredita i consta que hi son totes (`alternatives.resolve_group` ->
  `NOT_SATISFIED`), la disjuncio genera UN SOL bloqueig, a la via canonica (la
  d'identificador mes petit de les que no compleixen, estable entre el calcul i els
  recomptes); les altres vies no en porten cap. Una via que no aplica (`not_applicable`)
  continua sense aplicar i no bloqueja; si cap no aplica, la disjuncio tampoc. Si no
  consta que hi siguin totes, cap via no bloqueja tota sola (pot haver-hi una altra
  via). Abans de la v6 no en generava cap.
* **Substitucio per classificacio en serveis** (art. 77.1.b LCSP). En un contracte de
  serveis la classificacio no es exigible: si l'empresa l'acredita, substitueix la
  solvencia economica i tecnica del mateix abast; si no l'acredita, la solvencia
  s'acredita pels altres mitjans i la classificacio no bloqueja ni compta com a
  obligatoria. En obres (i qualsevol altre tipus) no s'hi toca res. Una fila de
  solvencia amb veredicte huma, o que no aplica, no se substitueix.

Aquestes regles s'apliquen al calcul i a cada recompte (`count_rows`), amb el
veredicte HUMA d'una fila si n'hi ha: si una persona diu que la classificacio no
s'acredita, la solvencia torna a dependre d'ella mateixa, i si diu que una via si que
acredita la disjuncio, el bloqueig desapareix.

**La fila desada es la que compta.** Un veredicte huma explicit mana sobre la seva fila
(tambe dins d'un grup). La part automatica de cada fila especial es reescriu perque
digui el que el sistema hi veu (`apply_outcome`; per a una fila amb veredicte, el que
hi veuria sense el seu), i les que deixen de ser especials tornen al que deien soles.
Aixi la fila, els comptadors del run i els de la candidatura (que llegeixen les files
desades) no poden discrepar.

**Una confirmacio parla d'un resultat mostrat.** Una persona que «confirma» (revisa
sense veredicte propi), o que posa com a veredicte el mateix que la fila nomes mostrava
per un grup o una substitucio, respon del resultat que tenia davant (`review_basis`).
No es un veredicte independent: si el recompte en mostra un altre, ni el fa confirmat
ni es conserva (en queda la traca).

Modul pur: ni base de dades ni model (treballa amb qualsevol objecte amb els atributs
d'una avaluacio).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.compliance import consolidation
from app.compliance.alternatives import (
    EXPLANATION_BY_OUTCOME,
    Branch,
    GroupOutcome,
    GroupResolution,
    resolve_group,
)
from app.compliance.certifications import POTENTIAL_ONLY_KEY
from app.compliance.rules import ExplanationCode, RuleCode
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    BlockerStatus,
    RegistrationType,
    RequirementCategory,
    RequirementObligation,
    ReviewStatus,
)

#: La solvencia que una classificacio pot substituir (art. 77.1.b i 79 LCSP). Ni
#: l'asseguranca ni l'adscripcio de mitjans: aquelles no son solvencia.
SOLVENCY_CATEGORIES = frozenset(
    {
        RequirementCategory.ECONOMIC_SOLVENCY,
        RequirementCategory.TECHNICAL_SOLVENCY,
        RequirementCategory.EXPERIENCE,
    }
)
#: Clau de `explanation_params` amb la substitucio aplicada en calcular.
SUBSTITUTION_KEY = "substitution"
#: Clau de `explanation_params` amb el resultat propi d'una via d'un grup.
BRANCH_RESULT_KEY = "branch_result"
#: Les altres claus del veredicte propi d'una via (per tornar-lo si el grup canvia).
BRANCH_EXPLANATION_KEY = "branch_explanation"
BRANCH_RULE_KEY = "branch_rule"
BRANCH_MODE_KEY = "branch_mode"
#: Clau del motiu propi d'una classificacio que en serveis no es exigible.
OWN_EXPLANATION_KEY = "own_explanation"
#: Clau de l'obligatorietat amb que es va calcular una fila que el recompte desa
#: d'una altra manera (una classificacio en serveis es desa com a informativa).
OWN_OBLIGATION_KEY = "own_obligation"
#: Clau de `explanation_params` amb la base d'una revisio humana: el run on es va
#: fer, el resultat automatic que la persona tenia davant, el propi de la fila (sense
#: grup ni substitucio) i l'empremta del requisit.
REVIEW_BASIS_KEY = "review_basis"
#: Clau de `explanation_params` amb la traca d'una revisio conservada d'un run anterior.
CARRIED_REVIEW_KEY = "review_carried_over"
#: Clau de `explanation_params` quan una revisio NO s'ha conservat.
DROPPED_REVIEW_KEY = "review_not_carried"
#: Motiu de `DROPPED_REVIEW_KEY` quan el que la persona tenia davant venia d'una altra
#: fila (un grup o la classificacio) i el recompte ja no ho mostra.
SHOWN_CHANGED = "shown_changed"

KIND_GROUP = "group"
KIND_CLASSIFICATION = "classification"
KIND_SOLVENCY = "solvency"
#: Una fila que no es especial (o que ja no ho es: el contracte ja no es de serveis):
#: diu el que diu ella sola.
KIND_PLAIN = "plain"


def is_classification(category: Any, rule_code: Any, params: dict | None) -> bool:
    """Una fila de classificacio empresarial: per la categoria o per la regla.

    Un requisit de registre que demana una classificacio s'avalua amb la regla de
    classificacio (o, sense dir quina, amb el registre de classificacio): en serveis
    tampoc no es exigible.
    """
    return (
        category == RequirementCategory.BUSINESS_CLASSIFICATION
        or rule_code == RuleCode.BUSINESS_CLASSIFICATION_GTE
        or (params or {}).get("required_type") == RegistrationType.BUSINESS_CLASSIFICATION.value
    )


def agreed_with_rewrite(verdict: Any, basis: dict | None) -> bool:
    """El veredicte es el mateix que la fila nomes MOSTRAVA per un grup o una
    substitucio (no el que deia ella sola): la persona hi va estar d'acord, no el va
    decidir. Es tracta com una confirmacio d'aquell resultat."""
    basis = basis or {}
    shown, own = basis.get("automatic_result"), basis.get("own_result")
    return bool(verdict is not None and own and shown and shown != own and str(verdict) == shown)


@dataclass(frozen=True, slots=True)
class RowState:
    """El que cal saber d'una fila per resoldre-la amb les altres."""

    key: Any
    category: Any
    tender_lot_id: Any
    obligation: RequirementObligation
    modality: Any
    #: El requisit esta verificat (font validada).
    requirement_verified: bool
    alternative_group: str | None
    group_complete: bool | None
    #: El resultat propi, sense la reescriptura del grup ni de la substitucio.
    own: AssessmentResult
    #: El veredicte huma independent, si n'hi ha.
    reviewed: AssessmentResult | None = None
    #: Una persona l'ha revisat (`VERIFIED` o `REJECTED`).
    assessment_reviewed: bool = False
    #: Una confirmacio (o un veredicte que reafirmava el que es mostrava): el resultat
    #: que la persona tenia davant.
    confirmed: AssessmentResult | None = None
    #: El «no compleix» nomes pot ser potencial (`potential_only`).
    potential_only: bool = False
    #: Es una classificacio empresarial (`is_classification`).
    classification: bool = False

    @property
    def verdict(self) -> AssessmentResult:
        return self.reviewed if self.reviewed is not None else self.own

    def reviewed_for(self, result: AssessmentResult) -> bool:
        """Una persona respon d'aquest resultat: amb un veredicte propi, o confirmant-lo.

        Una confirmacio parla del resultat que tenia davant: un «compleix» confirmat no
        pot acabar sent un bloqueig confirmat quan el grup o la substitucio canvien.
        Sense base (revisions d'abans de la traca), com sempre.
        """
        if not self.assessment_reviewed:
            return False
        if self.reviewed is not None:
            return True
        return self.confirmed is None or self.confirmed == result


@dataclass(frozen=True, slots=True)
class RowOutcome:
    """Com compta una fila especial, quin bloqueig porta i que hi mostra el sistema."""

    effective: AssessmentResult
    obligation: RequirementObligation
    blocker: BlockerStatus
    #: La classificacio que la substitueix, si n'hi ha.
    substituted_by: Any = None
    #: `KIND_GROUP`, `KIND_CLASSIFICATION`, `KIND_SOLVENCY` o `KIND_PLAIN`.
    kind: str = KIND_GROUP
    #: El desenllac del grup, per a una via alternativa.
    group_outcome: GroupOutcome | None = None
    #: Nomes per a una fila amb veredicte huma: el que el sistema hi mostraria sense
    #: el seu veredicte (la part automatica de la fila).
    shown: AssessmentResult | None = None
    shown_substituted_by: Any = None
    shown_group_outcome: GroupOutcome | None = None

    def view(self) -> RowOutcome:
        """La part automatica de la fila: el que el sistema hi mostra."""
        if self.shown is None:
            return self
        return replace(
            self,
            effective=self.shown,
            substituted_by=self.shown_substituted_by,
            group_outcome=self.shown_group_outcome,
        )


def reviewed(review_status: Any) -> bool:
    return review_status in (ReviewStatus.VERIFIED, ReviewStatus.REJECTED)


def substitution_map(rows: list[RowState], *, include_reviewed: bool = False) -> dict[Any, Any]:
    """Solvencia -> classificacio que la substitueix (nomes en serveis).

    Una classificacio de tota la licitacio substitueix tota la solvencia; una d'un
    lot, nomes la del mateix lot. Les files d'una via alternativa les resol el grup;
    una solvencia que no aplica, o amb veredicte huma, es queda com es (amb
    `include_reviewed`, el que el sistema hi mostraria sense el veredicte).
    """
    satisfied = [
        row
        for row in rows
        if row.classification
        and not row.alternative_group
        and row.verdict == AssessmentResult.SATISFIES
    ]
    if not satisfied:
        return {}
    tender_wide = sorted((row for row in satisfied if row.tender_lot_id is None), key=_order)
    by_lot: dict[Any, RowState] = {}
    for row in sorted(satisfied, key=_order):
        if row.tender_lot_id is not None:
            by_lot.setdefault(row.tender_lot_id, row)
    mapping: dict[Any, Any] = {}
    for row in rows:
        if row.category not in SOLVENCY_CATEGORIES or row.alternative_group:
            continue
        if row.own == AssessmentResult.NOT_APPLICABLE:
            continue
        if row.reviewed is not None and not include_reviewed:
            continue
        if tender_wide:
            mapping[row.key] = tender_wide[0].key
        elif row.tender_lot_id is not None and row.tender_lot_id in by_lot:
            mapping[row.key] = by_lot[row.tender_lot_id].key
    return mapping


def finalize_rows(rows: list[RowState], *, services: bool) -> dict[Any, RowOutcome]:
    """Les files especials, resoltes amb les altres. Les que no hi son, com sempre."""
    outcomes: dict[Any, RowOutcome] = {}

    groups: dict[str, list[RowState]] = {}
    for row in rows:
        if row.alternative_group:
            groups.setdefault(row.alternative_group, []).append(row)
    for members in groups.values():
        outcomes.update(_resolve_group(members))

    if not services:
        return outcomes

    mapping = substitution_map(rows)
    shown_mapping = substitution_map(rows, include_reviewed=True)
    for row in rows:
        if row.alternative_group:
            continue
        if row.classification:
            # En serveis no es exigible: compta com a informativa i no bloqueja mai.
            outcomes[row.key] = RowOutcome(
                row.verdict,
                RequirementObligation.INFORMATIVE,
                BlockerStatus.NONE,
                kind=KIND_CLASSIFICATION,
                shown=row.own if row.reviewed is not None else None,
            )
        elif row.category in SOLVENCY_CATEGORIES:
            if row.key in mapping:
                outcomes[row.key] = RowOutcome(
                    AssessmentResult.SATISFIES,
                    row.obligation,
                    BlockerStatus.NONE,
                    substituted_by=mapping[row.key],
                    kind=KIND_SOLVENCY,
                )
            else:
                # Sense classificacio acreditada, la solvencia es la seva: el
                # veredicte propi (o l'huma) i el bloqueig que en resulta.
                outcome = _own_outcome(row, KIND_SOLVENCY)
                if row.reviewed is not None and row.key in shown_mapping:
                    outcome = replace(
                        outcome,
                        shown=AssessmentResult.SATISFIES,
                        shown_substituted_by=shown_mapping[row.key],
                    )
                outcomes[row.key] = outcome
    return outcomes


def _group_view(
    members: list[RowState], verdicts: dict[Any, AssessmentResult]
) -> tuple[GroupResolution | None, dict[Any, AssessmentResult]]:
    """El desenllac d'una disjuncio amb aquests veredictes, i el que mostra cada via."""
    if all(verdicts[m.key] == AssessmentResult.NOT_APPLICABLE for m in members):
        # Cap via no aplica: la disjuncio tampoc, sigui o no completa.
        return None, {m.key: AssessmentResult.NOT_APPLICABLE for m in members}
    resolution = resolve_group(
        [
            Branch(key=m.key, result=verdicts[m.key], group_complete=m.group_complete)
            for m in members
        ]
    )
    if resolution.outcome == GroupOutcome.SATISFIED:
        return resolution, {m.key: AssessmentResult.SATISFIES for m in members}
    if resolution.outcome == GroupOutcome.INCOMPLETE:
        return resolution, {m.key: AssessmentResult.NEEDS_REVIEW for m in members}
    # NOT_SATISFIED: cada via diu el que es (no compleix o no aplica).
    return resolution, {m.key: verdicts[m.key] for m in members}


def _resolve_group(members: list[RowState]) -> dict[Any, RowOutcome]:
    """Una disjuncio: cada via amb el que en resulta i, si no s'acredita, un bloqueig."""
    from app.compliance.engine import derive_blocker_status

    verdicts = {m.key: m.verdict for m in members}
    resolution, shown = _group_view(members, verdicts)
    # El veredicte huma d'una via mana sobre la seva fila; la resta, el del grup.
    effective = {
        m.key: m.reviewed if m.reviewed is not None else shown[m.key] for m in members
    }
    blockers = dict.fromkeys(verdicts, BlockerStatus.NONE)
    failing = [m for m in members if m.verdict == AssessmentResult.DOES_NOT_SATISFY]
    mandatory = any(m.obligation == RequirementObligation.MANDATORY for m in members)
    if resolution is not None and resolution.outcome == GroupOutcome.NOT_SATISFIED and failing:
        # Nomes la canonica de les que no compleixen porta el bloqueig de tota la
        # disjuncio. Si no consta que hi siguin totes (INCOMPLETE), cap via no bloqueja
        # tota sola, encara que una persona les marqui totes «no compleix» (pas 2: en pot
        # haver una altra; com a la v5).
        canonical = min(failing, key=_order)
        blockers[canonical.key] = derive_blocker_status(
            obligation=RequirementObligation.MANDATORY if mandatory else canonical.obligation,
            effective_result=AssessmentResult.DOES_NOT_SATISFY,
            requirement_review_status=(
                ReviewStatus.VERIFIED
                if all(m.requirement_verified for m in failing)
                else ReviewStatus.UNREVIEWED
            ),
            assessment_reviewed=all(
                m.reviewed_for(AssessmentResult.DOES_NOT_SATISFY) for m in failing
            ),
            group_blocks=True,
            confirmable_by_source=not any(m.potential_only for m in failing),
        )
    outcomes = {}
    for member in members:
        outcome = RowOutcome(
            effective[member.key],
            member.obligation,
            blockers[member.key],
            group_outcome=resolution.outcome if resolution else None,
        )
        if member.reviewed is not None:
            # La part automatica d'una via amb veredicte: el que el grup hi mostraria
            # sense el seu veredicte (amb el de les altres).
            alternative = {**verdicts, member.key: member.own}
            shown_resolution, shown_rows = _group_view(members, alternative)
            outcome = replace(
                outcome,
                shown=shown_rows[member.key],
                shown_group_outcome=shown_resolution.outcome if shown_resolution else None,
            )
        outcomes[member.key] = outcome
    return outcomes


def _own_outcome(row: RowState, kind: str) -> RowOutcome:
    """La fila tota sola: el seu veredicte i el bloqueig que en resulta."""
    from app.compliance.engine import derive_blocker_status

    return RowOutcome(
        row.verdict,
        row.obligation,
        derive_blocker_status(
            obligation=row.obligation,
            effective_result=row.verdict,
            requirement_review_status=(
                ReviewStatus.VERIFIED if row.requirement_verified else ReviewStatus.UNREVIEWED
            ),
            assessment_reviewed=row.reviewed_for(row.verdict),
            modality=row.modality,
            confirmable_by_source=not row.potential_only,
        ),
        kind=kind,
        shown=row.own if row.reviewed is not None else None,
    )


# --------------------------------------------------------------------------- #
# Files desades                                                                 #
# --------------------------------------------------------------------------- #


def own_result(assessment: Any) -> AssessmentResult:
    """El resultat propi d'una fila desada: sense la reescriptura del grup ni de la
    substitucio (que en guarden l'original)."""
    params = assessment.explanation_params or {}
    raw = params.get(BRANCH_RESULT_KEY) or (params.get(SUBSTITUTION_KEY) or {}).get(
        "own_result"
    )
    return AssessmentResult(raw) if raw else assessment.automatic_result


def own_obligation(assessment: Any) -> RequirementObligation:
    """L'obligatorietat amb que es va calcular la fila, no la que el recompte hi ha desat.

    Una classificacio en serveis es desa com a informativa (`apply_outcome`), i
    l'original es guarda a `OWN_OBLIGATION_KEY`.
    """
    own = (assessment.explanation_params or {}).get(OWN_OBLIGATION_KEY)
    return RequirementObligation(own) if own else assessment.obligation


def assessment_state(requirement: Any, assessment: Any) -> RowState:
    """Una fila desada: el resultat propi (sense grup ni substitucio) i el veredicte huma."""
    params = assessment.explanation_params or {}
    verdict = assessment.reviewed_result
    confirmed = None
    if reviewed(assessment.review_status):
        basis = params.get(REVIEW_BASIS_KEY) or {}
        if verdict is None:
            if basis.get("automatic_result"):
                confirmed = AssessmentResult(basis["automatic_result"])
        elif agreed_with_rewrite(verdict, basis):
            # Reafirmava el que el grup o la substitucio feien dir a la fila: com una
            # confirmacio d'aquell resultat, no un veredicte independent.
            confirmed, verdict = verdict, None
    return RowState(
        key=assessment.requirement_id,
        category=getattr(requirement, "category", None),
        tender_lot_id=(
            requirement.tender_lot_id if requirement is not None else assessment.tender_lot_id
        ),
        obligation=own_obligation(assessment),
        modality=getattr(requirement, "modality", None),
        requirement_verified=(
            requirement is not None and requirement.review_status == ReviewStatus.VERIFIED
        ),
        alternative_group=getattr(requirement, "alternative_group", None),
        group_complete=getattr(requirement, "alternative_group_complete", None),
        own=own_result(assessment),
        reviewed=verdict,
        assessment_reviewed=reviewed(assessment.review_status),
        confirmed=confirmed,
        potential_only=bool(params.get(POTENTIAL_ONLY_KEY)),
        classification=is_classification(
            getattr(requirement, "category", None),
            params.get(BRANCH_RULE_KEY) or assessment.rule_code,
            params,
        ),
    )


def _needs_outcome(assessment: Any, state: RowState) -> bool:
    """Una fila que no es especial pero que el recompte ha de tornar a mirar: la que es
    va desar reescrita (grup, substitucio o serveis) o la que porta una confirmacio d'un
    resultat que ja no mostra."""
    params = assessment.explanation_params or {}
    if (
        params.get(OWN_OBLIGATION_KEY)
        or params.get(SUBSTITUTION_KEY)
        or params.get(BRANCH_RESULT_KEY)
        or assessment.explanation_code == ExplanationCode.CLASSIFICATION_OPTIONAL_IN_SERVICES
    ):
        return True
    return (
        state.reviewed is None
        and state.confirmed is not None
        and state.confirmed != assessment.automatic_result
    )


def count_rows(
    pairs: list[tuple[Any, Any]], *, services: bool, persist: bool
) -> list[tuple[str, RequirementObligation, AssessmentResult, BlockerStatus]]:
    """Les files del recompte, amb les especials resoltes juntes (`finalize_rows`).

    `persist` reescriu les files especials perque diguin el mateix que el recompte, i
    torna al seu estat les que ho van ser i ja no ho son (`apply_outcome`).
    """
    states = [assessment_state(requirement, assessment) for requirement, assessment in pairs]
    special = finalize_rows(states, services=services)
    rows = []
    for (_requirement, assessment), state in zip(pairs, states, strict=True):
        outcome = special.get(assessment.requirement_id)
        if outcome is None and _needs_outcome(assessment, state):
            outcome = _own_outcome(state, KIND_PLAIN)
        if outcome is None:
            effective, obligation, blocker = (
                assessment.effective_result,
                assessment.obligation,
                assessment.blocker_status,
            )
        else:
            if persist:
                apply_outcome(assessment, state, outcome)
            effective, obligation, blocker = outcome.effective, outcome.obligation, outcome.blocker
        marker = consolidation.group_marker(
            assessment.requirement_id, assessment.explanation_params
        )
        rows.append((marker, obligation, effective, blocker))
    return rows


def apply_outcome(assessment: Any, state: RowState, outcome: RowOutcome) -> None:
    """Deixa una fila desada tal com la compta el recompte.

    El bloqueig i l'obligatorietat sempre (l'original queda a `OWN_OBLIGATION_KEY`
    mentre siguin diferents). La part automatica, el que el sistema hi mostra
    (`RowOutcome.view`): primer es desfan les reescriptures que ja no toquen
    (substitucio, grup) i despres s'aplica la que toca. El veredicte huma no es toca
    mai; una confirmacio d'un resultat que ja no es mostra deixa de valer
    (`_drop_stale_confirmation`).
    """
    params = dict(assessment.explanation_params or {})
    original = params.get(OWN_OBLIGATION_KEY) or str(assessment.obligation)
    if str(outcome.obligation) != original:
        params[OWN_OBLIGATION_KEY] = original
    else:
        params.pop(OWN_OBLIGATION_KEY, None)
    assessment.blocker_status = outcome.blocker
    assessment.obligation = outcome.obligation

    view = outcome.view()
    if view.kind != KIND_SOLVENCY or view.substituted_by is None:
        _restore_substitution(assessment, params)
    if view.kind != KIND_GROUP:
        _restore_group(assessment, params, state.own)
    if view.kind == KIND_SOLVENCY and view.substituted_by is not None:
        _set_substitution(assessment, params, view.substituted_by)
    if view.kind == KIND_GROUP:
        _apply_group(assessment, params, state.own, view)
    _apply_classification(assessment, params, view)
    if state.reviewed is None:
        _drop_stale_confirmation(assessment, params, state)
    assessment.explanation_params = params


def substitution_params(
    result: AssessmentResult, explanation: str, rule: str, mode: Any, classification: Any
) -> dict[str, Any]:
    """El que es desa d'una solvencia substituida: de quina classificacio i el que deia."""
    return {
        "classification_requirement": str(classification),
        "own_result": str(result),
        "own_explanation": explanation,
        "own_rule": rule,
        "own_mode": str(mode) if mode is not None else None,
    }


def _set_substitution(assessment: Any, params: dict, classification: Any) -> None:
    base = params.get(SUBSTITUTION_KEY) or substitution_params(
        assessment.automatic_result,
        assessment.explanation_code,
        assessment.rule_code,
        assessment.assessment_mode,
        classification,
    )
    params[SUBSTITUTION_KEY] = {**base, "classification_requirement": str(classification)}
    assessment.automatic_result = AssessmentResult.SATISFIES
    assessment.rule_code = RuleCode.CLASSIFICATION_SUBSTITUTION
    assessment.explanation_code = ExplanationCode.CLASSIFICATION_SUBSTITUTES_SOLVENCY
    assessment.assessment_mode = AssessmentMode.AUTOMATIC


def _restore_substitution(assessment: Any, params: dict) -> None:
    """La classificacio ja no l'acredita (o la fila ja no es una solvencia substituible):
    torna el veredicte propi."""
    current = params.pop(SUBSTITUTION_KEY, None)
    if not current:
        return
    own = AssessmentResult(current["own_result"])
    assessment.automatic_result = own
    assessment.rule_code = current.get("own_rule") or assessment.rule_code
    assessment.explanation_code = current.get("own_explanation") or assessment.explanation_code
    assessment.assessment_mode = _mode(current.get("own_mode"), own)


def group_rewrites(own: AssessmentResult, effective: AssessmentResult, outcome: Any) -> bool:
    """Si el grup reescriu una via: quan en canvia el resultat, o quan la via que no
    compleix forma part d'una disjuncio que no s'acredita (i en porta el motiu)."""
    return effective != own or (
        outcome == GroupOutcome.NOT_SATISFIED and own == AssessmentResult.DOES_NOT_SATISFY
    )


def _apply_group(assessment: Any, params: dict, own: AssessmentResult, view: RowOutcome) -> None:
    if not group_rewrites(own, view.effective, view.group_outcome):
        _restore_group(assessment, params, own)
        return
    if BRANCH_RESULT_KEY not in params:
        # La fila ja diu el que diu sola (s'han desfet les altres reescriptures).
        params[BRANCH_RESULT_KEY] = str(assessment.automatic_result)
        params[BRANCH_EXPLANATION_KEY] = assessment.explanation_code
        params[BRANCH_RULE_KEY] = assessment.rule_code
        params[BRANCH_MODE_KEY] = str(assessment.assessment_mode)
    if view.group_outcome is not None:
        params["group_outcome"] = view.group_outcome.value
        assessment.explanation_code = EXPLANATION_BY_OUTCOME[view.group_outcome]
    assessment.automatic_result = view.effective
    assessment.rule_code = RuleCode.ALTERNATIVE_GROUP
    assessment.assessment_mode = (
        AssessmentMode.REVIEW_REQUIRED
        if view.effective == AssessmentResult.NEEDS_REVIEW
        else _mode(params.get(BRANCH_MODE_KEY), view.effective)
    )


def _restore_group(assessment: Any, params: dict, own: AssessmentResult) -> None:
    """El grup ja no reescriu aquesta via: torna el seu veredicte."""
    if BRANCH_RESULT_KEY not in params:
        return
    params.pop(BRANCH_RESULT_KEY)
    assessment.automatic_result = own
    assessment.explanation_code = (
        params.pop(BRANCH_EXPLANATION_KEY, None) or assessment.explanation_code
    )
    assessment.rule_code = params.pop(BRANCH_RULE_KEY, None) or assessment.rule_code
    assessment.assessment_mode = _mode(params.pop(BRANCH_MODE_KEY, None), own)
    params.pop("group_outcome", None)


def _apply_classification(assessment: Any, params: dict, view: RowOutcome) -> None:
    """En serveis, una classificacio que no s'acredita diu que no es exigible; fora de
    serveis (o si s'acredita), torna el seu motiu. Tambe sota un veredicte huma: es el
    text de la part automatica."""
    optional_code = ExplanationCode.CLASSIFICATION_OPTIONAL_IN_SERVICES
    optional = view.kind == KIND_CLASSIFICATION and assessment.automatic_result not in (
        AssessmentResult.SATISFIES,
        AssessmentResult.NOT_APPLICABLE,
    )
    shown = assessment.explanation_code == optional_code
    if optional and not shown:
        params[OWN_EXPLANATION_KEY] = assessment.explanation_code
        assessment.explanation_code = optional_code
    elif not optional and shown and params.get(OWN_EXPLANATION_KEY):
        assessment.explanation_code = params.pop(OWN_EXPLANATION_KEY)


def _drop_stale_confirmation(assessment: Any, params: dict, state: RowState) -> None:
    """Una confirmacio d'un resultat que el recompte ja no mostra no hi parla.

    Com en recalcular (`_carry_review`): no es conserva, i en queda la traca
    (`DROPPED_REVIEW_KEY`) perque la persona sapigui que l'ha de tornar a mirar. Tambe
    un veredicte que nomes reafirmava el que la fila mostrava per un grup o una
    substitucio (`agreed_with_rewrite`): conservar-lo amagaria el «no compleix» propi.
    """
    if not state.assessment_reviewed or state.confirmed is None:
        return
    if state.confirmed == assessment.automatic_result:
        return
    basis = params.get(REVIEW_BASIS_KEY) or {}
    params[DROPPED_REVIEW_KEY] = {
        "from_run": basis.get("run"),
        "reviewed_against": str(state.confirmed),
        "reviewed_result": (
            str(assessment.reviewed_result) if assessment.reviewed_result is not None else None
        ),
        "review_note": assessment.review_note,
        "reason": SHOWN_CHANGED,
        "same_run": basis.get("run") == str(assessment.assessment_run_id),
    }
    params.pop(REVIEW_BASIS_KEY, None)
    params.pop(CARRIED_REVIEW_KEY, None)
    assessment.reviewed_result = None
    assessment.review_status = ReviewStatus.UNREVIEWED
    assessment.review_note = None
    assessment.reviewed_by = None
    assessment.reviewed_at = None


def _mode(raw: Any, result: AssessmentResult) -> AssessmentMode:
    if raw:
        try:
            return AssessmentMode(raw)
        except ValueError:
            pass
    return (
        AssessmentMode.REVIEW_REQUIRED
        if result in (AssessmentResult.NEEDS_REVIEW, AssessmentResult.UNKNOWN)
        else AssessmentMode.AUTOMATIC
    )


def _order(row: RowState) -> str:
    return str(row.key)


__all__ = [
    "BRANCH_EXPLANATION_KEY",
    "BRANCH_MODE_KEY",
    "BRANCH_RESULT_KEY",
    "BRANCH_RULE_KEY",
    "CARRIED_REVIEW_KEY",
    "DROPPED_REVIEW_KEY",
    "KIND_CLASSIFICATION",
    "KIND_GROUP",
    "KIND_PLAIN",
    "KIND_SOLVENCY",
    "OWN_EXPLANATION_KEY",
    "OWN_OBLIGATION_KEY",
    "REVIEW_BASIS_KEY",
    "SHOWN_CHANGED",
    "SOLVENCY_CATEGORIES",
    "SUBSTITUTION_KEY",
    "RowOutcome",
    "RowState",
    "agreed_with_rewrite",
    "apply_outcome",
    "assessment_state",
    "count_rows",
    "finalize_rows",
    "group_rewrites",
    "is_classification",
    "own_obligation",
    "own_result",
    "reviewed",
    "substitution_map",
    "substitution_params",
]
