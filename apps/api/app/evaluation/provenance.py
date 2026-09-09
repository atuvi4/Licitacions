"""Corregir a quin Tender apunta un cas del gold, sense tocar-ne el contingut.

Hi ha dues coses ben diferents que es poden equivocar en una anotacio del gold:
**que diu** i **d'on surt**. La primera es una correccio de contingut i obliga a
tornar a verificar-la; la segona es una correccio de proveniencia i no toca res
del que una persona va comprovar.

Aquest modul nomes fa la segona, i s'assegura que nomes fa la segona: canvia el
`tender_external_id` d'un cas i despres **comprova camp a camp que no ha canviat
res mes**. Si hi ha qualsevol altra diferencia, falla en comptes de desar.

Per que cal: el numero d'expedient no es unic —el posa cada organ pel seu
compte— i el gold pot haver quedat lligat al Tender equivocat de dos que el
comparteixen. La cita, la pagina i el valor eren correctes; el que apuntava
malament era el document on anar-los a buscar.

La correccio crea sempre una versio nova amb la seva entrada a `revisions`: qui
llegeixi el gold d'aqui a un any ha de poder veure que va canviar i per que.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.evaluation.dataset import GoldDataset, GoldRevision

#: Camps del cas que aquesta operacio pot tocar. Qualsevol altra diferencia
#: entre l'original i el resultat es un error, no una correccio.
MUTABLE_CASE_FIELDS = frozenset({"tender_external_id"})


class ProvenanceError(ValueError):
    """La correccio hauria canviat alguna cosa que no li pertoca."""


def _case_snapshot(dataset: GoldDataset, case_id: str) -> dict[str, Any]:
    case = dataset.case(case_id)
    if case is None:
        raise ProvenanceError(f"El cas {case_id!r} no existeix a {dataset.dataset_id!r}.")
    return case.model_dump(mode="json")


def retarget_case(
    dataset: GoldDataset,
    *,
    new_dataset_id: str,
    case_id: str,
    new_external_id: str,
    reviewer: str,
    reason: str,
    when: date | None = None,
) -> GoldDataset:
    """Nova versio del gold amb el cas apuntant al Tender correcte.

    El dataset d'origen no es modifica: se'n fa una copia profunda. El resultat
    porta `derived_from` i una `GoldRevision` que diu l'identificador vell, el
    nou, el motiu, qui ho signa i quan.
    """
    before = _case_snapshot(dataset, case_id)
    old_external_id = before.get("tender_external_id")
    if old_external_id == new_external_id:
        raise ProvenanceError(
            f"{case_id} ja apunta a {new_external_id!r}: no hi ha res a corregir."
        )

    corrected = dataset.model_copy(deep=True)
    corrected.dataset_id = new_dataset_id
    corrected.version = dataset.version + 1
    corrected.created_at = when or date.today()
    if hasattr(corrected, "derived_from"):
        corrected.derived_from = dataset.dataset_id

    case = corrected.case(case_id)
    assert case is not None  # ja validat sobre l'original
    case.tender_external_id = new_external_id

    _assert_only_provenance_changed(before, _case_snapshot(corrected, case_id))

    corrected.revisions = [
        *dataset.revisions,
        GoldRevision(
            date=corrected.created_at,
            reviewer=reviewer,
            case_id=case_id,
            gold_id=None,
            reason=(
                f"Proveniencia: tender_external_id {old_external_id!r} -> "
                f"{new_external_id!r}. {reason}"
            ),
        ),
    ]
    return corrected


def _assert_only_provenance_changed(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Cap anotacio pot haver canviat. Ni l'estat, ni la cita, ni el valor.

    Es la garantia que converteix aixo en una correccio de proveniencia i no en
    una reescriptura silenciosa del gold: si alguna cosa mes s'ha mogut, aqui
    s'atura.
    """
    changed = {
        key
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }
    illegal = changed - MUTABLE_CASE_FIELDS
    if illegal:
        raise ProvenanceError(
            "Una correccio de proveniencia no pot canviar: " + ", ".join(sorted(illegal))
        )
