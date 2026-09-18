"""L'empremta del contingut i la vigencia d'una validacio, sense base de dades.

Dues afirmacions diferents i totes dues deterministes:

* **Que s'ha validat.** L'empremta cobreix NOMES el que una persona llegeix quan
  signa: titol, proposta, proveidor i producte. Si hi entressin les notes, el
  responsable o l'estat, canviar-los invalidaria una validacio que ningu no ha
  contradit.
* **Si aquella validacio encara val.** No s'esborra mai: es historia. El que es
  deriva es si segueix vigent, i per que no.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.workspace.technical_solution import (
    CONTENT_CHANGED,
    REQUIREMENT_CHANGED,
    REQUIREMENT_MISSING,
    StaleInput,
    content_fingerprint,
    validation_stale_reasons,
)

VALIDAT = datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


def fingerprint(**canvis) -> str:
    base = {
        "title": "Subministrament de servidors",
        "proposed_solution": "Dos servidors en alta disponibilitat",
        "supplier_name": "Proveidor SL",
        "product_name": "Model X",
    }
    return content_fingerprint(**{**base, **canvis})


# --------------------------------------------------------------------------- #
# Empremta del contingut                                                        #
# --------------------------------------------------------------------------- #


def test_l_empremta_es_sha256_en_hexadecimal():
    valor = fingerprint()

    assert len(valor) == 64
    assert set(valor) <= set("0123456789abcdef")


def test_la_mateixa_entrada_dona_sempre_la_mateixa_empremta():
    assert fingerprint() == fingerprint()


@pytest.mark.parametrize(
    "camp",
    ["title", "proposed_solution", "supplier_name", "product_name"],
)
def test_cada_camp_del_contingut_canvia_l_empremta(camp):
    assert fingerprint(**{camp: "una altra cosa"}) != fingerprint()


def test_els_espais_del_voltant_no_compten():
    assert fingerprint(title="  Subministrament de servidors  ") == fingerprint()


def test_none_i_cadena_buida_son_la_mateixa_absencia():
    """Esborrar un camp deixant-lo buit o deixant-lo nul es el mateix fet.

    Si no fossin iguals, netejar un camp de dues maneres equivalents donaria dues
    empremtes diferents i una de les dues invalidaria la validacio sense motiu.
    """
    assert fingerprint(supplier_name=None) == fingerprint(supplier_name="")
    assert fingerprint(supplier_name=None) == fingerprint(supplier_name="   ")


def test_el_contingut_no_es_pot_confondre_entre_camps():
    """Moure text d'un camp a un altre ha de canviar l'empremta.

    Una concatenacio ingenua ("a"+"b") donaria el mateix per a ("ab", "") que per
    a ("a", "b"): serien dues propostes diferents amb la mateixa firma.
    """
    assert fingerprint(supplier_name="AB", product_name="") != fingerprint(
        supplier_name="A", product_name="B"
    )


# --------------------------------------------------------------------------- #
# Vigencia de la validacio                                                      #
# --------------------------------------------------------------------------- #


def entrada(**canvis) -> StaleInput:
    base = {
        "validated_at": VALIDAT,
        "source": "requirement",
        "current_content_fingerprint": "aa",
        "validated_content_fingerprint": "aa",
        "requirement_resolvable": True,
        "current_requirement_fingerprint": "bb",
        "validated_requirement_fingerprint": "bb",
    }
    return StaleInput(**{**base, **canvis})


def test_una_solucio_sense_validar_no_es_caducada():
    """`stale` parla d'una validacio que existeix. Sense validacio no hi ha res a caducar."""
    assert validation_stale_reasons(entrada(validated_at=None)) == []


def test_una_validacio_intacta_es_vigent():
    assert validation_stale_reasons(entrada()) == []


def test_el_contingut_canviat_caduca_la_validacio():
    assert validation_stale_reasons(entrada(current_content_fingerprint="zz")) == [
        CONTENT_CHANGED
    ]


def test_el_requisit_canviat_caduca_la_validacio():
    assert validation_stale_reasons(entrada(current_requirement_fingerprint="zz")) == [
        REQUIREMENT_CHANGED
    ]


def test_el_requisit_desaparegut_caduca_la_validacio():
    assert validation_stale_reasons(entrada(requirement_resolvable=False)) == [
        REQUIREMENT_MISSING
    ]


def test_un_requisit_desaparegut_no_es_alhora_un_requisit_canviat():
    """Sense requisit no hi ha res amb que comparar: dir les dues coses seria soroll."""
    motius = validation_stale_reasons(
        entrada(requirement_resolvable=False, current_requirement_fingerprint=None)
    )

    assert motius == [REQUIREMENT_MISSING]


def test_els_motius_s_acumulen():
    motius = validation_stale_reasons(
        entrada(current_content_fingerprint="zz", current_requirement_fingerprint="yy")
    )

    assert motius == [CONTENT_CHANGED, REQUIREMENT_CHANGED]


@pytest.mark.parametrize("motiu", [REQUIREMENT_CHANGED, REQUIREMENT_MISSING])
def test_una_solucio_manual_mai_caduca_per_requisit(motiu):
    """Una fila manual no en te cap: no pot caducar per una cosa que no la lliga."""
    motius = validation_stale_reasons(
        entrada(
            source="manual",
            requirement_resolvable=False,
            current_requirement_fingerprint=None,
            validated_requirement_fingerprint=None,
        )
    )

    assert motiu not in motius
    assert motius == []
