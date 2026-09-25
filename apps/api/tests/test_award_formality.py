"""Criteri d'adjudicacio formal vs "el plec diu que es valorara".

Casos tallats dels criteris reals del run `a18fa4a6` (Palamos), sense text del plec.
"""

from __future__ import annotations

import pytest

from app.models.enums import TenderDocumentType
from app.services.award_formality import FORMAL, UNCONFIRMED, award_formality

ADMIN = TenderDocumentType.ADMINISTRATIVE_TERMS
TECH = TenderDocumentType.TECHNICAL_TERMS
ADDITIONAL = TenderDocumentType.ADDITIONAL


def _formality(**kwargs):
    base = {"points": None, "weight_percent": None, "formula": None, "formula_text": None,
            "evidence_document_types": ()}
    return award_formality(**{**base, **kwargs})


def test_una_casella_si_no_del_pcap_sense_punts_a_la_pagina_es_formal():
    """El model d'oferta del PCAP (p.27) no porta els punts; continua sent formal."""
    result = _formality(evidence_document_types=[ADMIN])

    assert result.status == FORMAL
    assert result.signals == ("administrative_terms_source",)


def test_un_es_valorara_del_ppt_sense_punts_no_es_formal():
    """Eficiencia energetica / auriculars biaurals: cita literal, cap puntuacio."""
    result = _formality(evidence_document_types=[TECH])

    assert result.status == UNCONFIRMED
    assert result.signals == ()


def test_un_criteri_amb_punts_fora_del_pcap_es_formal():
    """L'Informe de necessitat dona els punts de les 8 caselles (2, 5, 2, 2, 1, 1, 4, 3)."""
    assert _formality(points=5.0, evidence_document_types=[ADDITIONAL]).status == FORMAL


def test_un_pes_percentual_tambe_es_puntuacio():
    assert _formality(weight_percent=20.0, evidence_document_types=[TECH]).signals == ("scoring",)


def test_una_formula_sense_punts_locals_es_formal():
    result = _formality(formula_text="Vi = Bi x 80 punts / Max(Bs, Bmax)",
                        evidence_document_types=[ADDITIONAL])

    assert result.status == FORMAL
    assert result.signals == ("formula",)


@pytest.mark.parametrize("formula", ["", "   "])
def test_una_formula_buida_no_compta(formula):
    assert _formality(formula=formula, evidence_document_types=[TECH]).status == UNCONFIRMED


def test_cites_de_diversos_documents_amb_una_del_pcap_es_formal():
    result = _formality(evidence_document_types=[TECH, ADMIN])

    assert result.status == FORMAL


@pytest.mark.parametrize("value", [None, "desconegut", "notice"])
def test_tipus_de_document_desconeguts_o_d_anunci_sense_punts_no_basten(value):
    assert _formality(evidence_document_types=[value]).status == UNCONFIRMED


def test_els_senyals_s_acumulen():
    result = _formality(
        points=80.0, formula="Vi = ...", evidence_document_types=["administrative_terms"]
    )

    assert result.signals == ("scoring", "formula", "administrative_terms_source")


# --------------------------------------------------------------------------- #
# Casos 6 i 7: condicio d'execucio i afirmacio informativa                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("source", [TECH, ADDITIONAL])
def test_una_condicio_d_execucio_o_un_informatiu_fora_del_pcap_no_es_formal(source):
    """Si el model els desa com a criteri, sense punts ni formula, queden per revisar."""
    assert _formality(evidence_document_types=[source]).status == UNCONFIRMED


def test_limitacio_coneguda_una_condicio_d_execucio_del_pcap_passa_per_formal():
    """FALS POSITIU CONEGUT de la regla, fixat perque no s'oblidi.

    Una condicio especial d'execucio o una frase informativa del PCAP que el
    model hagi desat com a criteri, sense punts ni formula, es classifica
    `formal` nomes per la font. La regla no pot separar-ho sense un senyal mes
    (la seccio de criteris, el model d'oferta del Sobre 2...). Per aixo NO es
    connecta a la persistencia.
    """
    result = _formality(evidence_document_types=[ADMIN])

    assert result.status == FORMAL
    assert result.signals == ("administrative_terms_source",)
