"""El label d'un lot economic: una fotografia humana, no un identificador.

C-13. Es genera al backend, sempre amb el mateix helper, i es desa. No es
recalcula mai: si el plec canvia el titol del lot, la fila economica conserva el
que deia el dia que algu la va crear.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.offer.lot_label import (
    GLOBAL_LABEL,
    LABEL_MAX_LENGTH,
    UNIDENTIFIED_LABEL,
    economic_offer_lot_label,
)


@dataclass(frozen=True)
class FakeLot:
    lot_number: int | None = None
    external_lot_id: str | None = None
    title: str | None = None


def test_un_lot_amb_numero_i_titol_els_diu_tots_dos():
    label = economic_offer_lot_label(FakeLot(lot_number=2, title="Telefonia mobil"))

    assert label == "Lot 2 · Telefonia mobil"


def test_sense_numero_l_identificador_es_el_de_la_font():
    label = economic_offer_lot_label(
        FakeLot(lot_number=None, external_lot_id="L-04", title="Telefonia mobil")
    )

    assert label == "Lot L-04 · Telefonia mobil"


def test_un_lot_sense_titol_nomes_diu_l_identificador():
    assert economic_offer_lot_label(FakeLot(lot_number=2)) == "Lot 2"


def test_un_lot_sense_identificador_nomes_diu_el_titol():
    """Sense numero ni codi, escriure "Lot" davant del titol seria inventar-ne un."""
    label = economic_offer_lot_label(FakeLot(title="Telefonia mobil"))

    assert label == "Telefonia mobil"


def test_un_lot_sense_cap_dada_ho_diu_explicitament():
    """Mai l'UUID intern: no es una informacio que cap persona pugui fer servir."""
    label = economic_offer_lot_label(FakeLot())

    assert label == UNIDENTIFIED_LABEL == "Lot sense identificador"


def test_el_lot_zero_es_un_lot():
    """`lot_number = 0` existeix. Comprovar-ne la veracitat el faria desapareixer."""
    assert economic_offer_lot_label(FakeLot(lot_number=0)) == "Lot 0"


def test_una_oferta_sense_lots_te_el_seu_label_propi():
    assert economic_offer_lot_label(None) == GLOBAL_LABEL == "Oferta global"


@pytest.mark.parametrize(
    "lot,esperat",
    [
        (FakeLot(external_lot_id="  L-04  ", title="  Telefonia  "), "Lot L-04 · Telefonia"),
        (FakeLot(external_lot_id="   ", title="Telefonia"), "Telefonia"),
        (FakeLot(external_lot_id="L-04", title="   "), "Lot L-04"),
        (FakeLot(external_lot_id="   ", title="   "), UNIDENTIFIED_LABEL),
    ],
)
def test_els_espais_no_compten_com_a_dada(lot, esperat):
    assert economic_offer_lot_label(lot) == esperat


def test_el_titol_que_ja_diu_lot_no_es_toca():
    """Cap heuristica linguistica: el que diu la font es queda tal com ve."""
    label = economic_offer_lot_label(FakeLot(lot_number=2, title="Lot de telefonia"))

    assert label == "Lot 2 · Lot de telefonia"


def test_un_titol_desmesurat_es_retalla_a_l_amplada_de_la_columna():
    """Ho decideix el domini, no un error de PostgreSQL per llargada."""
    label = economic_offer_lot_label(FakeLot(lot_number=2, title="T" * 500))

    assert len(label) == LABEL_MAX_LENGTH == 300
    assert label.startswith("Lot 2 · TTT")
