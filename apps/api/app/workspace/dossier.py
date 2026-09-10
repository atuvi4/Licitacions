"""Cobertura documental i readiness. **Deterministic, sense cap LLM.**

Tres coses que aquest modul manté separades, i que es confondrien facilment:

* **Compliment** (4C): "podem acreditar que complim?". Ja calculat.
* **Cobertura del dossier**: "tenim el document preparat?". Es una altra
  pregunta, i la resposta pot ser NO amb el compliment en `satisfies`: saber que
  facturem prou no vol dir tenir els comptes anuals signats a la carpeta.
* **Readiness**: "el dossier sembla complet per fer la revisio humana final?".
  No es una nota ni un percentatge: es un estat amb una llista explicita del que
  falta. Un numero opac no diu a ningu que ha de fer.

I una que no es toca: `ready_for_final_review` **no es** `submitted`. Nomes vol
dir que el dossier sembla complet perque una persona el revisi.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.models.enums import (
    DocumentValidity,
    DossierCoverage,
    EvidenceItemStatus,
    RequirementCategory,
    WorkspaceReadiness,
)
from app.workspace.relevance import ESTABLISHED, RelevanceState

#: Agrupacio del dossier. Es la que fa servir un cap de licitacions quan prepara
#: la carpeta, no la taxonomia interna de requisits.
DOSSIER_GROUPS: dict[RequirementCategory, str] = {
    RequirementCategory.ECONOMIC_SOLVENCY: "economic_solvency",
    RequirementCategory.TECHNICAL_SOLVENCY: "experience",
    RequirementCategory.EXPERIENCE: "experience",
    RequirementCategory.CERTIFICATION: "certifications",
    RequirementCategory.INSURANCE: "insurances",
    RequirementCategory.PERSONNEL: "team",
    RequirementCategory.PROFESSIONAL_QUALIFICATION: "team",
    RequirementCategory.REGISTRATION: "registrations",
    RequirementCategory.BUSINESS_CLASSIFICATION: "registrations",
    RequirementCategory.ADMINISTRATIVE_DOCUMENTATION: "administrative",
    RequirementCategory.DEADLINE: "administrative",
    RequirementCategory.CONTRACT_CONDITION: "other",
    RequirementCategory.EQUIPMENT_OR_RESOURCES: "other",
    RequirementCategory.TECHNICAL_REQUIREMENT: "other",
    RequirementCategory.OTHER: "other",
}

#: Ordre de presentacio dels grups.
GROUP_ORDER: tuple[str, ...] = (
    "economic_solvency",
    "experience",
    "certifications",
    "insurances",
    "team",
    "registrations",
    "administrative",
    "other",
)

#: Estats d'una peca que compten com a coberta. Nomes n'hi ha un: triar es un
#: acte huma i no hi ha cap altra manera de cobrir un requisit.
COVERING_STATUSES = frozenset({EvidenceItemStatus.SELECTED})


def document_validity(
    expires_at: date | None,
    *,
    deadline: datetime | date | None,
    today: date | None = None,
    has_document: bool = True,
) -> DocumentValidity:
    """Vigencia d'un document RESPECTE D'AQUESTA LICITACIO.

    El cas que importa i que no es veu sol: un document vigent avui que caduca
    abans del dia de presentacio. Formalment encara val; per a aquesta licitacio,
    no serveix.
    """
    if not has_document:
        return DocumentValidity.NO_DOCUMENT
    if expires_at is None:
        return DocumentValidity.NO_EXPIRY

    reference = today or date.today()
    if expires_at < reference:
        return DocumentValidity.EXPIRED

    limit = deadline.date() if isinstance(deadline, datetime) else deadline
    if limit is not None and expires_at < limit:
        return DocumentValidity.EXPIRES_BEFORE_DEADLINE
    return DocumentValidity.VALID


#: Vigencies que no serveixen per presentar-se.
UNUSABLE_VALIDITIES = frozenset(
    {DocumentValidity.EXPIRED, DocumentValidity.EXPIRES_BEFORE_DEADLINE}
)


@dataclass(frozen=True, slots=True)
class EvidenceView:
    """Una peca del dossier, ja resolta contra el document i el termini."""

    item_id: object
    requirement_id: object | None
    status: EvidenceItemStatus
    validity: DocumentValidity
    has_document: bool
    mandatory: bool
    #: Com de sostinguda esta la pertinença d'aquest document (`RelevanceState`).
    #: Que estigui triat no vol dir que acrediti el que el plec demana.
    relevance: str = RelevanceState.UNPROVEN
    #: Quants documents demana el requisit. Un de sol no cobreix una exigencia
    #: de tres certificats per molt que la categoria encaixi.
    required_pieces: int = 1


def coverage_for_requirement(pieces: list[EvidenceView]) -> DossierCoverage:
    """Cobertura d'UN requisit a partir de les seves peces.

    Un requisit pot necessitar diversos documents (tres certificats de bona
    execucio, per exemple). Qualsevol peca marcada per revisar el deixa en
    `needs_review`: es la persona qui ho ha dit.

    I **seleccionat no vol dir acreditat**. Una peça triada nomes cobreix si la
    seva pertinença esta establerta —enllaç amb l'evidencia del compliment o
    confirmacio humana—; si no, el requisit queda en revisio encara que hi hagi
    un paper a la fila. Es el que evita que un titol universitari acrediti una
    xifra de negoci nomes perque algu el va triar del desplegable.
    """
    if not pieces:
        return DossierCoverage.MISSING_DOCUMENT
    if all(piece.status == EvidenceItemStatus.NOT_APPLICABLE for piece in pieces):
        return DossierCoverage.NOT_APPLICABLE

    live = [piece for piece in pieces if piece.status != EvidenceItemStatus.NOT_APPLICABLE]
    if any(piece.status == EvidenceItemStatus.NEEDS_REVIEW for piece in live):
        return DossierCoverage.NEEDS_REVIEW

    selected = [piece for piece in live if piece.status in COVERING_STATUSES]
    if not selected:
        return DossierCoverage.MISSING_DOCUMENT
    if any(piece.validity in UNUSABLE_VALIDITIES for piece in selected):
        # Triat pero inservible per a aquesta licitacio: es un forat, no una
        # cobertura. Dir el contrari seria el pitjor error possible aqui.
        return DossierCoverage.MISSING_DOCUMENT

    # A partir d'aqui hi ha papers triats i utilitzables. Falta la pregunta que
    # el dossier no es feia: acrediten el que el plec demana?
    if any(piece.relevance not in ESTABLISHED for piece in selected):
        return DossierCoverage.NEEDS_REVIEW

    expected = max((piece.required_pieces for piece in live), default=1)
    if len(selected) < expected:
        # El plec en demana mes dels que hi ha. Tenir-ne un de la categoria
        # correcta no cobreix una exigencia de tres.
        return DossierCoverage.NEEDS_REVIEW
    return DossierCoverage.COVERED


@dataclass(frozen=True, slots=True)
class DossierCoverageCounters:
    """Cobertura documental dels requisits OBLIGATORIS. No es el compliment."""

    mandatory_total: int = 0
    covered: int = 0
    missing_document: int = 0
    needs_review: int = 0
    not_applicable: int = 0
    #: Documents seleccionats que no serveixen per a aquesta licitacio.
    expired_documents: int = 0
    #: Requisits no obligatoris amb dossier, per context.
    optional_total: int = 0
    optional_covered: int = 0

    @property
    def is_complete(self) -> bool:
        return self.mandatory_total > 0 and self.covered + self.not_applicable == (
            self.mandatory_total
        )


def summarise_coverage(
    coverage_by_requirement: dict[object, DossierCoverage],
    *,
    mandatory: set[object],
    expired_documents: int = 0,
) -> DossierCoverageCounters:
    counts = dict.fromkeys(
        ("covered", "missing_document", "needs_review", "not_applicable"), 0
    )
    optional_total = optional_covered = 0
    mandatory_total = 0

    for requirement_id, coverage in coverage_by_requirement.items():
        if requirement_id in mandatory:
            mandatory_total += 1
            counts[coverage.value] += 1
        else:
            optional_total += 1
            if coverage == DossierCoverage.COVERED:
                optional_covered += 1

    return DossierCoverageCounters(
        mandatory_total=mandatory_total,
        expired_documents=expired_documents,
        optional_total=optional_total,
        optional_covered=optional_covered,
        **counts,
    )


# --------------------------------------------------------------------------- #
# Readiness                                                                     #
# --------------------------------------------------------------------------- #

#: Motius que impedeixen arribar a la revisio final. La interficie en deriva el
#: text; el servidor nomes envia codis, com a la resta del sistema.
BLOCKER_DECISION_NOT_GO = "decision_not_go"
BLOCKER_MANDATORY_UNRESOLVED = "mandatory_unresolved"
#: Una analisi de requisits o un calcul de compliment nous: el dossier ja no
#: descriu el que el sistema sap ara, i no es pot seguir presentant com a llest.
BLOCKER_ANALYSIS_OUTDATED = "analysis_outdated"
BLOCKER_CHECKLIST_BLOCKED = "mandatory_checklist_blocked"
BLOCKER_MISSING_EVIDENCE = "mandatory_evidence_missing"
BLOCKER_EXPIRED_DOCUMENTS = "mandatory_document_expired"
BLOCKER_OPEN_TASKS = "mandatory_tasks_open"
#: Hi ha papers triats per a requisits obligatoris i ningu no ha establert que
#: acreditin el que el plec demana. Bloqueja de veritat: es exactament el forat
#: pel qual un titol universitari arribava a un paquet congelat.
BLOCKER_RELEVANCE_UNPROVEN = "mandatory_relevance_unproven"
#: Aquest no impedeix res per si sol: nomes deixa el dossier a `needs_review`.
BLOCKER_NEEDS_REVIEW = "evidence_needs_review"

#: Motius que nomes demanen una mirada humana, no feina pendent.
SOFT_BLOCKERS = frozenset({BLOCKER_NEEDS_REVIEW})


@dataclass(frozen=True, slots=True)
class ReadinessInput:
    """Tot el que decideix la readiness, ja resolt. Cap consulta aqui dins."""

    dossier_started: bool = False
    decision_is_go: bool = False
    analysis_outdated: bool = False
    mandatory_unresolved: int = 0
    blocked_checklist_items: int = 0
    missing_mandatory_evidence: int = 0
    expired_mandatory_documents: int = 0
    open_mandatory_tasks: int = 0
    needs_review_items: int = 0
    #: Requisits obligatoris amb document triat i pertinença sense acreditar.
    unproven_mandatory_evidence: int = 0
    blockers: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    state: WorkspaceReadiness
    blockers: tuple[str, ...]

    @property
    def is_ready(self) -> bool:
        return self.state == WorkspaceReadiness.READY_FOR_FINAL_REVIEW


def evaluate_readiness(data: ReadinessInput) -> ReadinessResult:
    """L'estat del dossier i, sobretot, **per que**.

    No retorna cap puntuacio. Un 73 % no diu a ningu que ha de fer; "falten dos
    documents obligatoris i una tasca" si.

    L'ordre de les regles importa: sense decisio humana de GO no hi ha res a
    preparar, i per aixo aquest blocador surt primer encara que el dossier
    estigui impecable.

    Un item de checklist NOMES bloqueja si algu l'ha marcat com a `blocked`. Que
    segueixi `pending` no impedeix res: el senyal fiable de si el document hi es
    el dona el dossier, no l'estat d'una casella.
    """
    blockers: list[str] = []

    if not data.decision_is_go:
        blockers.append(BLOCKER_DECISION_NOT_GO)
    if data.analysis_outdated:
        blockers.append(BLOCKER_ANALYSIS_OUTDATED)
    if data.mandatory_unresolved:
        blockers.append(BLOCKER_MANDATORY_UNRESOLVED)
    if data.blocked_checklist_items:
        blockers.append(BLOCKER_CHECKLIST_BLOCKED)
    if data.missing_mandatory_evidence:
        blockers.append(BLOCKER_MISSING_EVIDENCE)
    if data.expired_mandatory_documents:
        blockers.append(BLOCKER_EXPIRED_DOCUMENTS)
    if data.open_mandatory_tasks:
        blockers.append(BLOCKER_OPEN_TASKS)
    if data.unproven_mandatory_evidence:
        blockers.append(BLOCKER_RELEVANCE_UNPROVEN)
    if data.needs_review_items:
        blockers.append(BLOCKER_NEEDS_REVIEW)

    ordered = tuple(blockers)

    if not data.dossier_started:
        return ReadinessResult(WorkspaceReadiness.NOT_STARTED, ordered)
    if not ordered:
        return ReadinessResult(WorkspaceReadiness.READY_FOR_FINAL_REVIEW, ordered)
    if set(ordered) <= SOFT_BLOCKERS:
        # Res pendent de fer: nomes algu que ha demanat mirar-s'ho.
        return ReadinessResult(WorkspaceReadiness.NEEDS_REVIEW, ordered)
    return ReadinessResult(WorkspaceReadiness.INCOMPLETE, ordered)


# --------------------------------------------------------------------------- #
# Tasques a partir de forats                                                    #
# --------------------------------------------------------------------------- #


def task_title_for_gap(checklist_title: str | None, requirement_title: str | None) -> str:
    """Titol de la tasca que tanca un forat del dossier. Plantilla, no redaccio.

    Es reaprofita el titol de la checklist —que ja va sortir d'una plantilla
    revisable a `checklist.py`— i nomes se'n canvia el verb: la checklist diu
    que cal acreditar, la tasca diu que cal ACONSEGUIR el document.
    """
    base = (checklist_title or requirement_title or "").strip()
    if not base:
        return "Aconseguir el document acreditatiu"
    for prefix, replacement in (
        ("Acreditar ", "Obtenir el document que acredita "),
        ("Aportar ", "Aconseguir "),
        ("Preparar ", "Preparar "),
    ):
        if base.startswith(prefix):
            return f"{replacement}{base[len(prefix):]}"[:300]
    return f"Aconseguir el document per a: {base}"[:300]
