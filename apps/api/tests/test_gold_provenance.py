"""Corregir d'on surt una anotacio sense tocar el que diu.

`gold-v2` tenia el cas `1-2026` lligat al Tender equivocat: dos organs de
contractacio fan servir el numero d'expedient "1/2026" i el gold apuntava al que
no conte la cita. La correccio es de **proveniencia**: canvia on anar a buscar
el document, i res mes.

El perill d'una operacio aixi es que s'hi coli qualsevol altra cosa —un estat de
revisio, una cita, un valor— i que el gold quedi reescrit en silenci sota la
signatura d'algu que no ho va mirar. Per aixo la meitat d'aquests tests son
sobre el que NO ha de canviar.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from app.evaluation.dataset import AnnotationStatus, dataset_path, load_dataset
from app.evaluation.dataset_v2 import load_dataset_v2
from app.evaluation.provenance import (
    ProvenanceError,
    _assert_only_provenance_changed,
    retarget_case,
)

CASE = "1-2026"
OLD_EXTERNAL_ID = (
    "https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/20000643"
)
NEW_EXTERNAL_ID = (
    "https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/20367620"
)


@pytest.fixture
def previous():
    return load_dataset_v2("gold-v2")


@pytest.fixture
def corrected():
    return load_dataset_v2("gold-v2.1")


def test_only_the_provenance_of_one_case_changed(previous, corrected):
    """Un unic camp, en un unic cas. Tota la resta, byte a byte igual."""
    before = json.loads(dataset_path("gold-v2").read_text(encoding="utf-8"))
    after = json.loads(dataset_path("gold-v2.1").read_text(encoding="utf-8"))

    differences = [
        (a["case_id"], key)
        for a, b in zip(before["cases"], after["cases"], strict=True)
        for key in set(a) | set(b)
        if a.get(key) != b.get(key)
    ]
    assert differences == [(CASE, "tender_external_id")]

    assert previous.case(CASE).tender_external_id == OLD_EXTERNAL_ID
    assert corrected.case(CASE).tender_external_id == NEW_EXTERNAL_ID


def test_the_annotation_itself_is_untouched(previous, corrected):
    """La cita, la pagina, el valor, l'estat i el revisor original es queden.

    Es el que distingeix una correccio de proveniencia d'una reanotacio: ningu
    ha de tornar a verificar res.
    """
    old = previous.case(CASE).requirements[0]
    new = corrected.case(CASE).requirements[0]

    assert new.gold_id == old.gold_id
    assert new.annotation_status == old.annotation_status == AnnotationStatus.VERIFIED
    assert new.quote == old.quote
    assert new.page_number == old.page_number
    assert new.document_name == old.document_name
    assert new.value == old.value
    assert new.obligation == old.obligation
    assert new.is_critical == old.is_critical is True
    assert new.reviewed_by == old.reviewed_by
    assert new.reviewed_at == old.reviewed_at


def test_the_correction_is_recorded_with_both_identifiers(previous, corrected):
    """Qui llegeixi el gold d'aqui a un any ha de veure que va passar."""
    assert corrected.derived_from == "gold-v2"
    assert corrected.version == previous.version + 1

    new_revisions = corrected.revisions[len(previous.revisions) :]
    assert len(new_revisions) == 1
    revision = new_revisions[0]
    assert revision.reviewer == "AlbertTuvi1"
    assert revision.case_id == CASE
    assert OLD_EXTERNAL_ID in revision.reason
    assert NEW_EXTERNAL_ID in revision.reason
    assert "duplicat" in revision.reason


def test_the_earlier_datasets_are_untouched():
    """Ni el v1 revisat ni el v2: cada versio ha de poder-se citar per sempre."""
    v1 = load_dataset(dataset_path("gold-v1-reviewed").stem)
    statuses = [
        item.annotation_status
        for case in v1.cases
        for item in [*case.requirements, *case.award_criteria]
    ]
    assert statuses.count(AnnotationStatus.VERIFIED) == 11
    assert statuses.count(AnnotationStatus.NEEDS_REVIEW) == 5

    v2 = load_dataset_v2("gold-v2")
    assert v2.case(CASE).tender_external_id == OLD_EXTERNAL_ID
    assert v2.status_counts()["counts_for_metrics"] == 14


def test_the_corrected_dataset_keeps_every_invariant(corrected):
    counts = corrected.status_counts()
    assert counts["counts_for_metrics"] == 14
    assert counts["draft"] == 0
    assert counts["needs_review"] == 2
    assert corrected.double_counted() == []

    awards = sorted(
        f"{case.case_id}::{item.gold_id}"
        for case in corrected.cases
        for item in case.verified_award_criteria
    )
    assert awards == ["422-2026::award-1", "P4107500C-2026-65::award-1"]


def test_retarget_refuses_to_change_anything_but_the_pointer(previous):
    """La garantia estructural: si s'hi cola un altre canvi, falla.

    Sense aixo, aquesta operacio seria una porta oberta per reescriure el gold
    sense que ningu ho revises.
    """
    with pytest.raises(ProvenanceError, match="ja apunta"):
        retarget_case(
            previous,
            new_dataset_id="tmp",
            case_id=CASE,
            new_external_id=OLD_EXTERNAL_ID,
            reviewer="AlbertTuvi1",
            reason="cap canvi",
        )

    with pytest.raises(ProvenanceError, match="no existeix"):
        retarget_case(
            previous,
            new_dataset_id="tmp",
            case_id="cas-inexistent",
            new_external_id=NEW_EXTERNAL_ID,
            reviewer="AlbertTuvi1",
            reason="",
        )


def test_the_structural_guard_rejects_any_other_change():
    """La xarxa que impedeix que aixo es converteixi en una reescriptura.

    `retarget_case` avui nomes toca el punter, pero el dia que algu hi afegeixi
    una linia, aquesta comprovacio ha de ser la que ho aturi. Es prova
    directament, perque si nomes es provés a traves de `retarget_case` el test
    seguiria verd amb la comprovacio desactivada.
    """
    base = {
        "case_id": "1-2026",
        "tender_external_id": OLD_EXTERNAL_ID,
        "requirements": [{"gold_id": "exp-1", "annotation_status": "verified"}],
    }

    # Nomes el punter: passa.
    _assert_only_provenance_changed(base, {**base, "tender_external_id": NEW_EXTERNAL_ID})

    for field, value in (
        ("requirements", [{"gold_id": "exp-1", "annotation_status": "draft"}]),
        ("case_id", "un-altre"),
        ("expediente_number", "9/2026"),
    ):
        with pytest.raises(ProvenanceError, match=field):
            _assert_only_provenance_changed(base, {**base, field: value})


def test_retarget_does_not_mutate_the_source_dataset(previous):
    before = previous.model_dump(mode="json")

    result = retarget_case(
        previous,
        new_dataset_id="tmp",
        case_id=CASE,
        new_external_id=NEW_EXTERNAL_ID,
        reviewer="AlbertTuvi1",
        reason="prova",
        when=date(2026, 9, 9),
    )

    assert previous.model_dump(mode="json") == before
    assert result.case(CASE).tender_external_id == NEW_EXTERNAL_ID
