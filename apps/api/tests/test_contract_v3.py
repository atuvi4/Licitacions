"""Extraction Contract v3 (disseny, no connectat): claims i criteris d'adjudicacio.

Els casos surten dels runs reals de Palamos, sense text del plec copiat: nomes
la forma (una cita GPON que no parla de FTTH; un "es valorara" del PPT).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai import profiles
from app.ai.contract_v3 import (
    AwardCandidateOutV3,
    AwardClass,
    CitedQuote,
    ClaimStatus,
    EvidenceOutV3,
    claim_support,
    classify_award,
)

GPON = "Acces a Internet GPON a 600 Mbps simetrics pels equipaments municipals* 26"
FTTH = "Fibres individuals FTTH a 600 Mbps per diversos serveis 11"


# --------------------------------------------------------------------------- #
# Claims                                                                       #
# --------------------------------------------------------------------------- #


def test_una_cita_sosté_el_valor_que_declara():
    result = claim_support(
        {"accessos_GPON_600Mbps": 26}, [CitedQuote(GPON, ("accessos_GPON_600Mbps",))]
    )

    assert result.per_claim == {"accessos_GPON_600Mbps": ClaimStatus.SUPPORTED}
    assert result.requirement_supported


def test_el_cas_ftth_la_cita_gpon_no_sosté_el_valor_ftth():
    """Requisit 33a917b0: el valor hi era, la cita no el sostenia."""
    value = {"accessos_GPON_600Mbps": 26, "fibres_FTTH_600Mbps": 11}

    result = claim_support(value, [CitedQuote(GPON, ("accessos_GPON_600Mbps",))])

    assert result.per_claim["fibres_FTTH_600Mbps"] == ClaimStatus.UNSUPPORTED
    assert not result.requirement_supported
    assert result.unsupported == ["fibres_FTTH_600Mbps"]


def test_declarar_un_valor_que_la_cita_no_conte_no_n_hi_ha_prou():
    """Declarar-ho no basta: el numero ha de ser a la cita."""
    result = claim_support(
        {"fibres_FTTH_600Mbps": 11}, [CitedQuote(GPON, ("fibres_FTTH_600Mbps",))]
    )

    assert result.per_claim["fibres_FTTH_600Mbps"] == ClaimStatus.DECLARED_NOT_FOUND


def test_diversos_claims_amb_diverses_cites():
    value = {"accessos_GPON_600Mbps": 26, "fibres_FTTH_600Mbps": 11, "pool_IPs": 5}
    quotes = [
        CitedQuote(GPON, ("accessos_GPON_600Mbps",)),
        CitedQuote(FTTH, ("fibres_FTTH_600Mbps",)),
    ]

    result = claim_support(value, quotes)

    assert result.per_claim == {
        "accessos_GPON_600Mbps": ClaimStatus.SUPPORTED,
        "fibres_FTTH_600Mbps": ClaimStatus.SUPPORTED,
        "pool_IPs": ClaimStatus.UNSUPPORTED,
    }


@pytest.mark.parametrize(
    ("claim", "quote"),
    [
        ("155.239,16 €", "un volum anual de negocis igual o superior a 155.239,16€"),
        ("500.000 euros", "amb un import minim de 500.000 euros"),
        (72444.94, "igual o superior a 72.444,94€"),
    ],
)
def test_els_formats_numerics_del_plec_es_reconeixen(claim, quote):
    assert claim_support({"amount": claim}, [CitedQuote(quote, ("amount",))]).requirement_supported


def test_un_valor_de_text_nomes_s_ha_de_declarar():
    result = claim_support({"period": "any de major volum"}, [CitedQuote("qualsevol", ("period",))])

    assert result.per_claim["period"] == ClaimStatus.SUPPORTED


def test_sense_valors_el_requisit_es_sostingut_per_la_cita():
    assert claim_support(None, []).requirement_supported


def test_la_cita_v3_declara_que_sosté_i_segueix_sent_estricta():
    EvidenceOutV3(extraction_id="x", page_number=1, quote="q", supports=["amount"])
    with pytest.raises(ValidationError):
        EvidenceOutV3(extraction_id="x", page_number=1, quote="q", inventat=1)


# --------------------------------------------------------------------------- #
# Criteris d'adjudicacio                                                        #
# --------------------------------------------------------------------------- #


def _candidate(**kwargs) -> AwardCandidateOutV3:
    base = {"name": "Criteri", "kind": "award_criterion"}
    return AwardCandidateOutV3(**{**base, **kwargs})


def test_criteri_formal_amb_punts():
    candidate = _candidate(points=80.0, adjudication_basis=["points", "formula"])

    assert classify_award(candidate, valid_quotes=[]) == AwardClass.FORMAL_AWARD_CRITERION


def test_criteri_formal_sense_punts_locals_pero_amb_mecanisme_citat():
    """Una casella SI/NO del model d'oferta del Sobre 2: la cita prova el mecanisme."""
    candidate = _candidate(adjudication_basis=["offer_form"])
    quotes = [CitedQuote("( ) SI ( ) NO", ("adjudication_basis",))]

    assert classify_award(candidate, valid_quotes=quotes) == AwardClass.FORMAL_AWARD_CRITERION


def test_un_mecanisme_declarat_pero_no_citat_va_a_revisio():
    candidate = _candidate(adjudication_basis=["criteria_section"])

    assert classify_award(candidate, valid_quotes=[]) == AwardClass.CANDIDATE_AWARD_CRITERION


def test_es_valorara_sense_mecanisme_es_candidat_a_revisio_no_formal():
    """L'eficiencia energetica del PPT: el model diu criteri, pero no ho prova."""
    candidate = _candidate()

    assert classify_award(candidate, valid_quotes=[]) == AwardClass.CANDIDATE_AWARD_CRITERION


def test_una_caracteristica_tecnica_desitjable_no_es_criteri():
    candidate = _candidate(kind="desirable_feature")

    assert classify_award(candidate, valid_quotes=[]) == AwardClass.TECHNICAL_DESIRABLE


def test_una_frase_informativa_no_es_criteri():
    assert classify_award(_candidate(kind="informative"), valid_quotes=[]) == AwardClass.INFORMATIVE


def test_cap_classe_descarta_el_candidat():
    """Qualsevol entrada acaba en una classe: res no desapareix en silenci."""
    for kind in ("award_criterion", "desirable_feature", "informative"):
        assert classify_award(_candidate(kind=kind), valid_quotes=[]) in set(AwardClass)


def test_el_model_ha_de_declarar_la_classe_i_no_pot_inventar_mecanismes():
    with pytest.raises(ValidationError):
        AwardCandidateOutV3(name="x")
    with pytest.raises(ValidationError):
        _candidate(adjudication_basis=["intuicio"])


# --------------------------------------------------------------------------- #
# No connectat                                                                 #
# --------------------------------------------------------------------------- #


def test_el_contracte_v3_no_es_cap_perfil_actiu():
    assert all("v3" not in name for name in profiles.PROFILES)
    assert profiles.DEFAULT_PROFILE is profiles.V1
