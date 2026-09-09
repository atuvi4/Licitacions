"""Que passa amb l'anotacio original quan se'n verifica una que l'afina.

Una anotacio que n'afina una altra descriu **la mateixa exigencia** amb mes
detall: `TSA0082096-040::ins-2` diu que la polissa te 500.000 de cobertura i un
sublimit per victima de 300.000; `ins-1`, verificada abans, nomes podia dir
500.000. No son dos requisits. Son un requisit i una manera millor d'escriure'l.

Si totes dues comptessin, el gold demanaria dues assegurances on el plec en
demana una: el denominador s'infla i un extractor correcte, que n'extreu una,
sortiria com si se n'hagues deixat una. La metrica empitjoraria per una decisio
d'anotacio, que es exactament el que un gold no ha de fer mai.

Per aixo aqui no hi ha cap avis: hi ha un **error**. Verificar un refinament
sense dir que passa amb l'original atura l'aplicacio de decisions. Un warning al
report seria una nota que algu llegeix despres d'haver publicat la xifra.

I la resolucio no esborra res. `superseded_by` deixa l'original sencer —text,
cita, revisor i data— i nomes el treu del denominador. El que va passar ha de
continuar sent auditable; el que canvia es contra que es compara el model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.evaluation.dataset import AnnotationStatus, GoldDataset, GoldRevision

#: Clau de la decisio humana que resol el conflicte. Ha de ser explicita: no hi
#: ha valor per defecte, perque el defecte seria decidir per una persona.
SUPERSEDE_KEY = "supersede_refined"


class RefinementConflict(ValueError):
    """Decisions que deixarien dues anotacions comptant per la mateixa cosa."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class RefinementOutcome:
    """Que ha resolt aquesta tanda de decisions."""

    #: (superada, que la supera), amb el format `case::gold_id`.
    superseded: list[tuple[str, str]] = field(default_factory=list)
    #: Constancia per a `dataset.revisions`. Una supersessio mai es silenciosa.
    revisions: list[GoldRevision] = field(default_factory=list)


def _index(dataset: GoldDataset) -> dict[str, Any]:
    return {
        f"{case.case_id}::{item.gold_id}": item
        for case in dataset.cases
        for item in [*case.requirements, *case.award_criteria]
    }


def _counts(item: Any) -> bool:
    """Si una anotacio entra al denominador, sigui de la versio que sigui."""
    counts = getattr(item, "counts_for_metrics", None)
    return bool(counts if counts is not None else item.is_verified)


def check_decisions(dataset: GoldDataset, decisions: dict[str, Any]) -> None:
    """Rebutja les decisions que deixarien un conflicte sense resoldre.

    Es comprova ABANS d'aplicar res: si aixo salta, el dataset no s'ha tocat i
    la persona pot tornar a la pagina de revisio i decidir.
    """
    index = _index(dataset)
    problems: list[str] = []

    for item_id, decision in (decisions or {}).items():
        item = index.get(item_id)
        if item is None or not getattr(item, "refines", None):
            continue
        if decision.get("status") != AnnotationStatus.VERIFIED.value:
            # Rebutjar-lo o deixar-lo per revisar no toca l'original.
            continue

        case_id = item_id.split("::")[0]
        refined_id = f"{case_id}::{item.refines}"
        refined = index.get(refined_id)
        if refined is None:
            problems.append(
                f"{item_id} afina {refined_id}, que no existeix al dataset."
            )
            continue

        choice = decision.get(SUPERSEDE_KEY)
        if choice is None:
            problems.append(
                f"{item_id} es verifica i afina {refined_id}: cal dir que passa amb "
                f"l'original. Afegeix `{SUPERSEDE_KEY}: true` per marcar-lo com a "
                f"substituit, o pren una decisio explicita sobre {refined_id} "
                "(rejected / needs_review)."
            )
            continue
        if choice:
            continue

        # `false` vol dir "l'original es queda". Nomes val si deixa de comptar
        # per una altra decisio d'aquesta mateixa tanda.
        refined_decision = (decisions.get(refined_id) or {}).get("status")
        still_counts = (
            _counts(refined)
            if refined_decision is None
            else refined_decision == AnnotationStatus.VERIFIED.value
        )
        if still_counts:
            problems.append(
                f"{item_id} es verifica amb `{SUPERSEDE_KEY}: false` pero {refined_id} "
                "segueix comptant: el gold demanaria dues vegades la mateixa "
                "exigencia."
            )

    if problems:
        raise RefinementConflict(problems)


def apply_supersessions(
    dataset: GoldDataset,
    decisions: dict[str, Any],
    *,
    reviewer: str,
    when: date,
) -> RefinementOutcome:
    """Marca com a superades les anotacions que la revisio ha substituit."""
    index = _index(dataset)
    outcome = RefinementOutcome()

    for item_id, decision in (decisions or {}).items():
        item = index.get(item_id)
        if item is None or not getattr(item, "refines", None):
            continue
        if decision.get("status") != AnnotationStatus.VERIFIED.value:
            continue
        if not decision.get(SUPERSEDE_KEY):
            continue

        case_id = item_id.split("::")[0]
        refined = index[f"{case_id}::{item.refines}"]
        refined.superseded_by = item.gold_id
        refined.superseded_at = when
        outcome.superseded.append((f"{case_id}::{item.refines}", item_id))
        outcome.revisions.append(
            GoldRevision(
                date=when,
                reviewer=reviewer,
                case_id=case_id,
                gold_id=item.refines,
                reason=(
                    f"Substituida per {item.gold_id} dins del gold v2: descriuen la "
                    "mateixa exigencia i nomes l'afinada entra a les metriques. "
                    "L'anotacio original es conserva sencera i segueix sent auditable."
                ),
            )
        )

    return outcome


def check_no_double_counting(dataset: GoldDataset) -> None:
    """Postcondicio: cap parella afinada/afinadora comptant alhora.

    Es la xarxa de seguretat de tot el modul. Encara que algu editi el JSON a ma
    o afegeixi un cami nou per aplicar decisions, aquesta comprovacio ha de
    seguir sent l'ultim que es passa abans de desar.
    """
    pairs = getattr(dataset, "double_counted", None)
    if pairs is None:
        return
    conflicts = pairs()
    if conflicts:
        raise RefinementConflict(
            [
                f"{refined} i {refinement} compten totes dues: son la mateixa "
                "exigencia escrita dues vegades."
                for refined, refinement in conflicts
            ]
        )
