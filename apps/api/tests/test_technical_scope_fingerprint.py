"""L'empremta de l'abast tecnic: sobre QUE es va confirmar (C-14).

No cobreix la feina feta dins de cada solucio —aixo es la validacio d'S3— sino
**quins requisits hi ha i quins ha triat una persona**. La distincio es tota la
gracia: reescriure una proposta no ha de caducar la revisio de l'abast, i
assenyalar un requisit nou si.
"""

from __future__ import annotations

import uuid

import pytest

from app.workspace.technical_scope import (
    LOT_BOUND,
    NO_LOTS,
    SCOPE_VERSION,
    SELECTED_LOTS,
    technical_scope_fingerprint,
)

R1, R2, R3 = (uuid.UUID(int=n) for n in (1, 2, 3))
L1, L2 = (uuid.UUID(int=n) for n in (11, 12))


def fingerprint(**canvis) -> str:
    base = {
        "lot_mode": SELECTED_LOTS,
        "tender_lot_ids": [L1],
        "requirements": [(R1, "aaa"), (R2, "bbb")],
        "selected_requirement_ids": [R1],
    }
    return technical_scope_fingerprint(**{**base, **canvis})


def test_l_empremta_es_sha256_en_hexadecimal():
    valor = fingerprint()

    assert len(valor) == 64
    assert set(valor) <= set("0123456789abcdef")


def test_la_mateixa_entrada_dona_sempre_la_mateixa_empremta():
    assert fingerprint() == fingerprint()


def test_l_ordre_de_les_colleccions_no_compta():
    """La base pot tornar les files en qualsevol ordre: l'empremta no pot dependre'n."""
    assert fingerprint(
        requirements=[(R2, "bbb"), (R1, "aaa")], tender_lot_ids=[L1]
    ) == fingerprint()


def test_un_requisit_nou_canvia_l_abast():
    assert fingerprint(requirements=[(R1, "aaa"), (R2, "bbb"), (R3, "ccc")]) != fingerprint()


def test_un_requisit_que_desapareix_canvia_l_abast():
    assert fingerprint(requirements=[(R1, "aaa")]) != fingerprint()


def test_un_requisit_que_demana_una_altra_cosa_canvia_l_abast():
    """Es el cas que `requirements_fingerprint(run)` no detectaria.

    Una correccio humana d'un requisit no canvia el run d'extraccio, pero si
    canvia el que aquell requisit demana.
    """
    assert fingerprint(requirements=[(R1, "aaa"), (R2, "ZZZ")]) != fingerprint()


def test_assenyalar_un_requisit_mes_canvia_l_abast():
    """La seleccio humana FORMA PART de l'abast.

    Si no hi fos, algu podria confirmar "ho he revisat tot", crear despres una
    solucio per a un requisit mes, i la confirmacio seguiria semblant vigent.
    """
    assert fingerprint(selected_requirement_ids=[R1, R2]) != fingerprint()


def test_deixar_de_necessitar_solucio_tambe_canvia_l_abast():
    assert fingerprint(selected_requirement_ids=[]) != fingerprint()


def test_canviar_de_lots_canvia_l_abast():
    assert fingerprint(tender_lot_ids=[L1, L2]) != fingerprint()
    assert fingerprint(tender_lot_ids=[L2]) != fingerprint()


def test_el_mode_de_lots_forma_part_de_l_empremta():
    """Un lot fix i un lot triat son dos abasts diferents encara que coincideixin."""
    assert fingerprint(lot_mode=LOT_BOUND) != fingerprint(lot_mode=SELECTED_LOTS)


def test_una_candidatura_sense_lots_te_la_seva_empremta():
    valor = technical_scope_fingerprint(
        lot_mode=NO_LOTS,
        tender_lot_ids=[],
        requirements=[(R1, "aaa")],
        selected_requirement_ids=[],
    )

    assert len(valor) == 64


def test_els_requisits_i_la_seleccio_no_es_poden_confondre():
    """Serialitzat com a objecte amb claus, no com una llista plana.

    Amb una concatenacio ingenua, moure un identificador d'una banda a l'altra
    podria donar la mateixa empremta per a dos abasts diferents.
    """
    assert fingerprint(
        requirements=[(R1, "aaa")], selected_requirement_ids=[R1, R2]
    ) != fingerprint(requirements=[(R1, "aaa"), (R2, "bbb")], selected_requirement_ids=[R1])


def test_la_versio_forma_part_de_l_empremta():
    """Si algun dia canvia el que cobreix, les empremtes velles no poden quedar
    silenciosament com a vigents."""
    assert SCOPE_VERSION >= 1


@pytest.mark.parametrize("duplicat", [[R1, R1], [R1, R1, R1]])
def test_repetir_una_seleccio_no_canvia_res(duplicat):
    """Son conjunts: dir dues vegades el mateix no es una altra cosa."""
    assert fingerprint(selected_requirement_ids=duplicat) == fingerprint(
        selected_requirement_ids=[R1]
    )
