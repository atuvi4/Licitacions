"""Extraction Contract v3 (no connectat): esquemes, suport de claims, adjudicacio i rebutjos.

Els casos surten dels runs reals de Palamos, sense text del plec copiat: nomes
la forma (una fila GPON que no parla de FTTH; un "es valorara" del PPT).

Invariant central: QUOTE_VALID != CLAIM_SUPPORTED. Una cita que existeix no
prova cap valor; si el suport no es pot provar literalment, `unverified`.
"""

from __future__ import annotations

import json
import uuid

import pytest
from pydantic import ValidationError

from app.ai import profiles
from app.ai.contract_v3 import (
    LEGACY_REASONS,
    MAX_REJECTIONS_PER_CHUNK,
    VALUE_SCHEMA_V3,
    AwardClass,
    AwardReason,
    ClaimCheck,
    ClaimStatus,
    EvidenceLevel,
    MechanismCheck,
    RejectionItem,
    RejectionLog,
    RejectionReason,
    RejectionStage,
    RequirementSupportStatus,
    adjudication_basis_json,
    check_claim,
    classify_award,
    evidence_support_json,
    parse_chunk_v3,
    record_requirement_support,
    rejection,
    requirement_support,
    requirement_value_json,
)
from app.ai.schemas_v3 import (
    SCHEMA_VERSION_V3,
    AwardCandidateOutV3,
    ClaimOutV3,
    EvidenceOutV3,
    RequirementOutV3,
    chunk_output_json_schema_v3,
)

GPON = "Acces a Internet GPON a 600 Mbps simetrics pels equipaments municipals* 26"
FTTH = "Fibres individuals FTTH a 600 Mbps per diversos serveis 11"


def _claim(**kwargs) -> ClaimOutV3:
    return ClaimOutV3(**{"id": "c", "kind": "count", "value": 1, **kwargs})


def _req(claims, evidence, complete="complete") -> RequirementOutV3:
    return RequirementOutV3(
        category="other",
        title="t",
        claims=claims,
        claims_complete=complete,
        evidence=[
            EvidenceOutV3(extraction_id="x", page_number=1, quote=q, id=eid, supports=list(s))
            for eid, q, s in evidence
        ],
    )


def _quotes(evidence):
    return {eid: q for eid, q, _ in evidence}


# --------------------------------------------------------------------------- #
# Esquemes                                                                     #
# --------------------------------------------------------------------------- #


def test_els_esquemes_v3_son_estrictes():
    with pytest.raises(ValidationError):
        _claim(inventat=1)
    with pytest.raises(ValidationError):
        EvidenceOutV3(extraction_id="x", page_number=1, quote="q", id="e1", inventat=1)
    with pytest.raises(ValidationError):
        RequirementOutV3(category="other", title="t", value={"a": 1})  # no hi ha `value`
    with pytest.raises(ValidationError):
        AwardCandidateOutV3(name="x", kind="award_criterion", inventat=1)


def test_la_cita_v3_porta_id_i_declara_que_sosté():
    evidence = EvidenceOutV3(extraction_id="x", page_number=1, quote="q", id="e1", supports=["a"])
    assert evidence.supports == ["a"]
    with pytest.raises(ValidationError):
        EvidenceOutV3(extraction_id="x", page_number=1, quote="q")  # sense id
    with pytest.raises(ValidationError):
        EvidenceOutV3(extraction_id="x", page_number=1, quote="q", id="cita-1")


def test_ids_repetits_son_invalids():
    with pytest.raises(ValidationError):
        _req([_claim(id="a"), _claim(id="a")], [])
    with pytest.raises(ValidationError):
        _req([], [("e1", "q", ()), ("e1", "q", ())])


@pytest.mark.parametrize(
    "kwargs",
    [
        {"kind": "count", "value": "26"},  # numero com a text
        {"kind": "count", "value": True},  # booleà no es numero
        {"kind": "amount", "value": 10.0},  # import sense unitat
        {"kind": "amount", "value": 10.0, "unit": "percent"},
        {"kind": "count", "value": 3, "unit": "years"},
        {"kind": "text", "value": 3},
        {"kind": "boolean", "value": "si"},
        {"kind": "text", "value": "   "},
    ],
)
def test_el_valor_ha_de_casar_amb_el_tipus(kwargs):
    with pytest.raises(ValidationError):
        _claim(**kwargs)


def test_unknown_no_es_false():
    """Desconegut es `null` + `unknown`, mai `false` ni `0`."""
    unknown = _claim(kind="boolean", value=None, certainty="unknown")
    assert unknown.value is None
    with pytest.raises(ValidationError):
        _claim(kind="boolean", value=False, certainty="unknown")
    with pytest.raises(ValidationError):
        _claim(kind="boolean", value=None)  # null sense dir que es desconegut


def test_parcial_no_es_complet_i_per_defecte_es_desconegut():
    assert RequirementOutV3(category="other", title="t").claims_complete == "unknown"


def test_el_candidat_ha_de_declarar_la_classe_i_no_pot_inventar_mecanismes():
    with pytest.raises(ValidationError):
        AwardCandidateOutV3(name="x")
    with pytest.raises(ValidationError):
        AwardCandidateOutV3(name="x", kind="award_criterion", adjudication_basis=["intuicio"])
    with pytest.raises(ValidationError):
        AwardCandidateOutV3(
            name="x", kind="award_criterion", adjudication_basis=["points", "points"]
        )


def test_el_json_schema_v3_es_serialitzable_i_estricte():
    schema = chunk_output_json_schema_v3()
    json.dumps(schema)
    assert schema["additionalProperties"] is False
    assert "award_candidates" in schema["properties"]
    assert SCHEMA_VERSION_V3 == "requirements-schema-v3"


# --------------------------------------------------------------------------- #
# Suport de claims                                                             #
# --------------------------------------------------------------------------- #


def test_gpon_26_sostingut():
    claim = _claim(id="accessos_gpon", value=26, anchor="GPON")

    assert check_claim(claim, GPON) == ClaimCheck.SUPPORTED


def test_ftth_11_no_sostingut_per_una_cita_nomes_gpon():
    """Requisit 33a917b0: el valor hi era, la cita no el sostenia."""
    evidence = [("e1", GPON, ("accessos_gpon", "fibres_ftth"))]
    requirement = _req(
        [_claim(id="accessos_gpon", value=26, anchor="GPON"),
         _claim(id="fibres_ftth", value=11, anchor="FTTH")],
        evidence,
    )

    support = requirement_support(requirement, _quotes(evidence))

    assert support.per_evidence["e1"]["fibres_ftth"] == ClaimCheck.VALUE_NOT_IN_QUOTE
    assert support.per_claim == {
        "accessos_gpon": ClaimStatus.SUPPORTED,
        "fibres_ftth": ClaimStatus.NOT_SUPPORTED,
    }
    assert support.level == EvidenceLevel.CLAIM_SUPPORTED
    assert support.status == RequirementSupportStatus.NOT_SUPPORTED
    assert not support.requirement_supported


def test_un_claim_que_cap_cita_declara_no_es_sostingut():
    evidence = [("e1", GPON, ("accessos_gpon",))]
    requirement = _req(
        [_claim(id="accessos_gpon", value=26, anchor="GPON"),
         _claim(id="fibres_ftth", value=11, anchor="FTTH")],
        evidence,
    )

    assert requirement_support(requirement, _quotes(evidence)).per_claim["fibres_ftth"] == (
        ClaimStatus.NOT_SUPPORTED
    )


def test_una_cita_invalida_no_sosté_res_encara_que_ho_declari():
    evidence = [("e1", GPON, ("accessos_gpon",))]
    requirement = _req([_claim(id="accessos_gpon", value=26, anchor="GPON")], evidence)

    support = requirement_support(requirement, {})  # e1 no ha passat locate_quote

    assert support.per_claim["accessos_gpon"] == ClaimStatus.NOT_SUPPORTED


def test_el_mateix_numero_per_casualitat_no_prova_el_claim():
    """600 Mbps es a la cita GPON; un claim FTTH de 600 Mbps no hi queda sostingut."""
    claim = _claim(id="ftth_bw", kind="bandwidth", value=600, unit="mbps", anchor="FTTH")

    assert check_claim(claim, GPON) == ClaimCheck.ANCHOR_NOT_IN_QUOTE


def test_un_numero_amb_unitat_no_sosté_un_recompte():
    """El `600` de `600 Mbps` no es un recompte de 600."""
    claim = _claim(id="accessos_gpon", value=600, anchor="GPON")

    assert check_claim(claim, GPON) == ClaimCheck.UNIT_NOT_IN_QUOTE


def test_sense_ancora_un_numero_no_es_prova():
    claim = _claim(id="accessos_gpon", value=26)

    assert check_claim(claim, GPON) == ClaimCheck.ANCHOR_MISSING


def test_dues_files_enganxades_fan_el_lligam_ambigu():
    """Terme i valor a la mateixa cita, pero amb un valor rival al mateix tros."""
    joined = GPON + " " + FTTH
    claim = _claim(id="fibres_ftth", value=26, anchor="FTTH")

    assert check_claim(claim, joined) == ClaimCheck.BINDING_AMBIGUOUS
    assert check_claim(claim.model_copy(update={"value": 11}), joined) == (
        ClaimCheck.BINDING_AMBIGUOUS
    )


def test_dues_files_separades_cadascuna_sosté_la_seva():
    table = GPON + "\n" + FTTH

    assert check_claim(_claim(id="a", value=26, anchor="GPON"), table) == ClaimCheck.SUPPORTED
    assert check_claim(_claim(id="b", value=11, anchor="FTTH"), table) == ClaimCheck.SUPPORTED
    assert check_claim(_claim(id="b", value=26, anchor="FTTH"), table) == (
        ClaimCheck.BINDING_AMBIGUOUS
    )


@pytest.mark.parametrize(
    ("value", "quote"),
    [
        (155239.16, "un volum anual de negocis igual o superior a 155.239,16€"),
        (155239.16, "volum anual de negocis: 155.239,16 euros"),
        (500000, "volum anual de negocis minim de 500.000 EUR"),
        (72444.94, "volum anual de negocis igual o superior a 72.444,94 €"),
        (1000000, "volum anual de negocis d'1.000.000,00 €"),
        (1500, "volum anual de negocis de € 1.500"),
    ],
)
def test_imports_en_format_catala(value, quote):
    claim = _claim(id="volum", kind="amount", value=value, unit="EUR", anchor="volum anual")

    assert check_claim(claim, quote) == ClaimCheck.SUPPORTED


def test_un_import_sense_moneda_no_queda_provat():
    claim = _claim(id="volum", kind="amount", value=500000, unit="EUR", anchor="volum")

    assert check_claim(claim, "volum minim de 500.000") == ClaimCheck.UNIT_NOT_IN_QUOTE


def test_un_import_diferent_no_es_el_mateix():
    claim = _claim(id="volum", kind="amount", value=155239.61, unit="EUR", anchor="volum")

    assert check_claim(claim, "volum de 155.239,16 €") == ClaimCheck.VALUE_NOT_IN_QUOTE


@pytest.mark.parametrize("quote", ["baixa temeraria del 10%", "baixa del 10 %",
                                   "baixa del 10 per cent", "baixa del 10 por ciento"])
def test_percentatge(quote):
    claim = _claim(id="baixa", kind="percentage", value=10, unit="percent", anchor="baixa")

    assert check_claim(claim, quote) == ClaimCheck.SUPPORTED


def test_un_10_que_no_es_un_percentatge_no_el_prova():
    claim = _claim(id="baixa", kind="percentage", value=10, unit="percent", anchor="baixa")

    assert check_claim(claim, "baixa presentada en 10 dies") == ClaimCheck.UNIT_NOT_IN_QUOTE


@pytest.mark.parametrize(
    ("value", "unit", "quote"),
    [
        (3, "years", "la durada del contracte es de 3 anys"),
        (3, "years", "la durada del contracte es de tres anys"),
        (36, "months", "la durada del contracte es de 3 anys"),
        (2, "years", "durada: dos años"),
        (15, "days", "termini d'execucio de 15 dies naturals"),
    ],
)
def test_durada(value, unit, quote):
    anchor = "termini" if "termini" in quote else "durada"
    claim = _claim(id="durada", kind="duration", value=value, unit=unit, anchor=anchor)

    assert check_claim(claim, quote) == ClaimCheck.SUPPORTED


def test_durada_amb_prorroga_al_mateix_tros_es_ambigua():
    claim = _claim(id="durada", kind="duration", value=3, unit="years", anchor="durada")

    assert check_claim(claim, "durada de 3 anys prorrogable 1 any") == (
        ClaimCheck.BINDING_AMBIGUOUS
    )
    assert check_claim(claim, "durada de 3 anys, prorrogable 1 any") == ClaimCheck.SUPPORTED


def test_mesos_i_dies_no_es_converteixen():
    """Nomes conversions exactes: 1 mes no es 30 dies."""
    claim = _claim(id="d", kind="duration", value=30, unit="days", anchor="durada")

    assert check_claim(claim, "durada d'1 mes") != ClaimCheck.SUPPORTED


@pytest.mark.parametrize("quote", ["FTTH a 600 Mbps", "FTTH a 600Mbps", "FTTH 600 Mb/s",
                                   "FTTH a 0,6 Gbps"])
def test_ample_de_banda(quote):
    claim = _claim(id="bw", kind="bandwidth", value=600, unit="mbps", anchor="FTTH")

    assert check_claim(claim, quote) == ClaimCheck.SUPPORTED


def test_text_literal_sostingut_i_parafrasi_no_verificada():
    literal = _claim(id="cert", kind="text", value="ISO 9001", anchor=None)
    paraphrase = _claim(id="periode", kind="text", value="any de major volum")

    assert check_claim(literal, "certificat ISO 9001 o equivalent") == ClaimCheck.SUPPORTED
    assert check_claim(paraphrase, "l'exercici amb mes facturacio") == (
        ClaimCheck.SEMANTIC_UNVERIFIED
    )


def test_un_booleà_mai_no_queda_sostingut_sol():
    claim = _claim(id="ute", kind="boolean", value=True)

    assert check_claim(claim, "s'admet la concurrencia en UTE") == ClaimCheck.SEMANTIC_UNVERIFIED


def test_el_text_no_verificat_va_a_revisio_no_a_suportat():
    evidence = [("e1", "l'exercici amb mes facturacio", ("periode",))]
    requirement = _req([_claim(id="periode", kind="text", value="any de major volum")], evidence)

    support = requirement_support(requirement, _quotes(evidence))

    assert support.per_claim["periode"] == ClaimStatus.UNVERIFIED
    assert support.status == RequirementSupportStatus.NEEDS_REVIEW
    assert support.level == EvidenceLevel.QUOTE_VALID


def test_la_millor_cita_mana():
    evidence = [("e1", GPON, ("a",)), ("e2", "equipaments GPON: 26", ("a",))]
    requirement = _req([_claim(id="a", value=26)], evidence)  # sense ancora

    support = requirement_support(requirement, _quotes(evidence))

    assert support.per_claim["a"] == ClaimStatus.UNVERIFIED  # ANCHOR_MISSING a totes dues


def test_requisit_sostingut_nomes_si_tot_i_complet():
    evidence = [("e1", GPON + "\n" + FTTH, ("a", "b"))]
    claims = [_claim(id="a", value=26, anchor="GPON"), _claim(id="b", value=11, anchor="FTTH")]

    complete = requirement_support(_req(claims, evidence, "complete"), _quotes(evidence))
    partial = requirement_support(_req(claims, evidence, "partial"), _quotes(evidence))
    unknown = requirement_support(_req(claims, evidence, "unknown"), _quotes(evidence))

    assert complete.level == EvidenceLevel.REQUIREMENT_SUPPORTED
    assert complete.status == RequirementSupportStatus.SUPPORTED
    for result in (partial, unknown):
        assert result.level == EvidenceLevel.CLAIM_SUPPORTED
        assert result.status == RequirementSupportStatus.NEEDS_REVIEW


def test_un_valor_desconegut_no_es_ni_fals_ni_no_sostingut():
    evidence = [("e1", GPON, ("a",))]
    claims = [_claim(id="a", value=26, anchor="GPON"),
              _claim(id="b", kind="boolean", value=None, certainty="unknown")]

    support = requirement_support(_req(claims, evidence), _quotes(evidence))

    assert support.per_claim["b"] == ClaimStatus.UNKNOWN
    assert support.status == RequirementSupportStatus.NEEDS_REVIEW
    assert not support.requirement_supported


def test_sense_claims_la_cita_nomes_es_valida():
    evidence = [("e1", "Cal presentar el DEUC", ())]

    support = requirement_support(_req([], evidence), _quotes(evidence))

    assert support.level == EvidenceLevel.QUOTE_VALID
    assert support.status == RequirementSupportStatus.QUOTE_ONLY


def test_una_referencia_a_un_claim_inexistent_es_detecta():
    evidence = [("e1", GPON, ("a", "fantasma"))]

    support = requirement_support(_req([_claim(id="a", value=26, anchor="GPON")], evidence),
                                  _quotes(evidence))

    assert support.unknown_refs == {"e1": ["fantasma"]}
    assert support.per_claim["a"] == ClaimStatus.SUPPORTED


# --------------------------------------------------------------------------- #
# Criteris d'adjudicacio                                                       #
# --------------------------------------------------------------------------- #


def _candidate(evidence=(), **kwargs) -> AwardCandidateOutV3:
    base = {"name": "Criteri", "kind": "award_criterion"}
    return AwardCandidateOutV3(
        **{**base, **kwargs},
        evidence=[
            EvidenceOutV3(extraction_id="x", page_number=1, quote=q, id=eid, supports=list(s))
            for eid, q, s in evidence
        ],
    )


def _classify(candidate, evidence=None):
    quotes = {e.id: e.quote for e in candidate.evidence} if evidence is None else evidence
    return classify_award(candidate, quotes)


def test_formal_amb_punts_citats():
    candidate = _candidate(
        evidence=[("e1", "Preu: fins a 80 punts", ("points",))],
        points=80.0, adjudication_basis=["points"],
    )

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.FORMAL_AWARD_CRITERION
    assert decision.mechanisms["points"] == (MechanismCheck.VERIFIED, ["e1"])


def test_punts_declarats_pero_no_a_la_cita_no_fan_formal():
    candidate = _candidate(
        evidence=[("e1", "Preu: fins a 60 punts", ("points",))],
        points=80.0, adjudication_basis=["points"],
    )

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.CANDIDATE_AWARD_CRITERION
    assert decision.mechanisms["points"][0] == MechanismCheck.NOT_IN_QUOTE


def test_punts_sense_cap_cita_que_els_declari_no_fan_formal():
    """A la fase 1, `points` sol ja feia formal. Ara s'ha de citar."""
    candidate = _candidate(evidence=[("e1", "Preu: fins a 80 punts", ())], points=80.0,
                           adjudication_basis=["points"])

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.CANDIDATE_AWARD_CRITERION
    assert decision.mechanisms["points"][0] == MechanismCheck.NOT_EVIDENCED


def test_pes_i_formula():
    candidate = _candidate(
        evidence=[("e1", "Oferta economica (60%): P = 60 x (OM / OF)", ("weight", "formula"))],
        weight_percent=60.0, formula="P = 60 × (OM/OF)",
        adjudication_basis=["weight", "formula"],
    )

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.FORMAL_AWARD_CRITERION
    assert decision.mechanisms["weight"][0] == MechanismCheck.VERIFIED
    assert decision.mechanisms["formula"][0] == MechanismCheck.VERIFIED


def test_formula_declarada_sense_text_no_compta():
    candidate = _candidate(evidence=[("e1", "segons formula", ("formula",))],
                           adjudication_basis=["formula"])

    assert _classify(candidate).mechanisms["formula"][0] == MechanismCheck.MISSING_VALUE


def test_taula_de_criteris():
    table = "Criteri 1 ... 30 punts\nCriteri 2 ... 20 punts"
    candidate = _candidate(evidence=[("e1", table, ("criteria_table",))],
                           adjudication_basis=["criteria_table"])

    assert _classify(candidate).award_class == AwardClass.FORMAL_AWARD_CRITERION


def test_model_d_oferta_del_sobre_2():
    """Una casella SI/NO del model d'oferta: formal sense punts a la mateixa pagina."""
    candidate = _candidate(
        evidence=[("e1", "Telefons amb classe A: ( ) SI ( ) NO", ("offer_form",))],
        adjudication_basis=["offer_form"],
    )

    assert _classify(candidate).award_class == AwardClass.FORMAL_AWARD_CRITERION


def test_seccio_formal_de_criteris():
    candidate = _candidate(
        evidence=[("e1", "Clausula 12. Criteris d'adjudicacio", ("criteria_section",))],
        adjudication_basis=["criteria_section"],
    )

    assert _classify(candidate).award_class == AwardClass.FORMAL_AWARD_CRITERION


def test_es_valorara_no_fa_formal():
    """L'eficiencia energetica del PPT: diu criteri, pero no ho prova."""
    candidate = _candidate(
        evidence=[("e1", "Es valorara l'eficiencia energetica dels terminals",
                   ("criteria_section",))],
        adjudication_basis=["criteria_section"],
    )

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.CANDIDATE_AWARD_CRITERION
    assert decision.reasons == [AwardReason.NO_MECHANISM_VERIFIED]


def test_venir_del_pcap_no_fa_formal():
    candidate = AwardCandidateOutV3(
        name="Millores", kind="award_criterion",
        evidence=[EvidenceOutV3(extraction_id="x", page_number=9, section="PCAP",
                                quote="Es valoraran les millores", id="e1")],
    )

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.CANDIDATE_AWARD_CRITERION
    assert decision.reasons == [AwardReason.NO_MECHANISM_DECLARED]


def test_desitjable_i_informatiu_no_es_descarten():
    assert _classify(_candidate(kind="desirable_feature")).award_class == (
        AwardClass.TECHNICAL_DESIRABLE
    )
    assert _classify(_candidate(kind="informative")).award_class == AwardClass.INFORMATIVE
    for kind in ("award_criterion", "desirable_feature", "informative"):
        assert _classify(_candidate(kind=kind)).award_class in set(AwardClass)


def test_un_desitjable_amb_mecanisme_provat_va_a_revisio():
    candidate = _candidate(kind="desirable_feature", points=5.0, adjudication_basis=["points"],
                           evidence=[("e1", "5 punts", ("points",))])

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.CANDIDATE_AWARD_CRITERION
    assert decision.reasons == [AwardReason.KIND_CONFLICTS_WITH_MECHANISM]


def test_una_cita_invalida_no_prova_cap_mecanisme():
    candidate = _candidate(evidence=[("e1", "80 punts", ("points",))], points=80.0,
                           adjudication_basis=["points"])

    decision = classify_award(candidate, {})

    assert decision.award_class == AwardClass.CANDIDATE_AWARD_CRITERION


# --------------------------------------------------------------------------- #
# Rebutjos                                                                     #
# --------------------------------------------------------------------------- #


def test_el_vocabulari_de_rebuig_v3():
    assert {r.value for r in RejectionReason} == {
        "schema_invalid", "evidence_not_found", "page_not_in_chunk", "extraction_mismatch",
        "no_valid_evidence", "claim_not_supported", "duplicate_key",
    }
    assert LEGACY_REASONS["quote_not_found"] == RejectionReason.EVIDENCE_NOT_FOUND


def test_un_element_invalid_no_s_emporta_el_chunk():
    secret = "TEXT-DEL-MODEL-QUE-NO-HA-DE-SORTIR"
    payload = {
        "requirements": [
            {"category": "other", "title": "ok"},
            {"category": "other", "title": "dolent", secret: 1},
            {"category": "other", "title": "dolent", "claims": [{"id": "a", "kind": "count",
                                                                  "value": secret}]},
        ],
        "award_candidates": [{"name": "x"}],
    }

    parsed, log = parse_chunk_v3(payload)

    assert [r.title for r in parsed.requirements] == ["ok"]
    assert [e["reason"] for e in log.entries] == ["schema_invalid"] * 3
    assert log.entries[0] == {"item": "requirement", "index": 1, "stage": "schema",
                              "reason": "schema_invalid", "detail": "extra_forbidden"}
    assert log.entries[1]["field"] == "claims"
    assert log.entries[2]["item"] == "award_candidate"
    assert secret not in json.dumps(log.entries)


@pytest.mark.parametrize("payload", [[], "text", {"requirements": [], "inventat": []},
                                     {"requirements": "no-llista"}])
def test_un_sobre_invalid_fa_invalid_el_chunk(payload):
    with pytest.raises(ValidationError):
        parse_chunk_v3(payload)


def test_els_claims_no_sostinguts_es_registren_sense_text():
    evidence = [("e1", GPON, ("accessos_gpon", "fibres_ftth", "fantasma"))]
    requirement = _req(
        [_claim(id="accessos_gpon", value=26, anchor="GPON"),
         _claim(id="fibres_ftth", value=11, anchor="FTTH"),
         _claim(id="pool_ips", value=5, anchor="IP")],
        evidence,
    )
    support = requirement_support(requirement, _quotes(evidence))
    log = RejectionLog()

    record_requirement_support(log, 4, requirement, support)

    assert log.entries == [
        {"item": "requirement", "index": 4, "stage": "claim",
         "reason": "claim_not_supported", "claim": 1, "detail": "value_not_in_quote"},
        {"item": "requirement", "index": 4, "stage": "claim",
         "reason": "claim_not_supported", "claim": 2, "detail": "not_declared"},
        {"item": "requirement", "index": 4, "stage": "claim",
         "reason": "claim_not_supported", "detail": "unknown_ref"},
    ]
    dumped = json.dumps(log.entries)
    for leaked in ("GPON", "fibres_ftth", "fantasma", "26"):
        assert leaked not in dumped


def test_els_diagnostics_estan_acotats():
    log = RejectionLog()
    for index in range(MAX_REJECTIONS_PER_CHUNK + 7):
        log.add(rejection(RejectionItem.REQUIREMENT, index, RejectionStage.ITEM,
                          RejectionReason.NO_VALID_EVIDENCE))

    assert len(log.entries) == MAX_REJECTIONS_PER_CHUNK
    assert log.dropped == 7


def test_un_detail_fora_del_vocabulari_es_other():
    entry = rejection(RejectionItem.REQUIREMENT, 0, RejectionStage.SCHEMA,
                      RejectionReason.SCHEMA_INVALID, detail="api_key=sk-...")

    assert entry["detail"] == "other"


# --------------------------------------------------------------------------- #
# Persistencia                                                                 #
# --------------------------------------------------------------------------- #


def test_value_json_son_els_claims_sense_copia_ni_estat():
    requirement = _req([_claim(id="a", value=26, anchor="GPON")], [("e1", GPON, ("a",))])

    value = requirement_value_json(requirement)

    assert VALUE_SCHEMA_V3 == "requirements-claims-v3"
    assert value == {
        "claims": [{"id": "a", "kind": "count", "value": 26, "unit": None, "anchor": "GPON",
                    "certainty": "stated"}],
        "claims_complete": "complete",
    }


def test_el_suport_desat_a_la_cita_no_porta_text():
    evidence = [("e1", GPON, ("a",))]
    requirement = _req([_claim(id="a", value=26, anchor="GPON")], evidence)
    support = requirement_support(requirement, _quotes(evidence))

    stored = evidence_support_json("e1", support)

    assert stored == {"contract": "claim-support-v1", "ref": "e1", "claims": {"a": "supported"}}
    assert "GPON" not in json.dumps(stored)


def test_la_base_d_adjudicacio_desada_referencia_files_i_no_text():
    candidate = _candidate(evidence=[("e1", "Preu: fins a 80 punts", ("points",))],
                           points=80.0, adjudication_basis=["points", "formula"])
    decision = _classify(candidate)
    row = uuid.uuid4()

    stored = adjudication_basis_json(candidate, decision, {"e1": row})

    assert stored == {
        "contract": "adjudication-basis-v1",
        "declared_kind": "award_criterion",
        "award_class": "formal_award_criterion",
        "reasons": ["mechanism_verified"],
        "mechanisms": [
            {"kind": "points", "check": "verified", "evidence_ids": [str(row)]},
            {"kind": "formula", "check": "not_evidenced", "evidence_ids": []},
        ],
    }
    assert "punts" not in json.dumps(stored)


# --------------------------------------------------------------------------- #
# No connectat                                                                 #
# --------------------------------------------------------------------------- #


def test_el_contracte_v3_no_es_cap_perfil_actiu():
    assert all("v3" not in name for name in profiles.PROFILES)
    assert all(p.schema_version != SCHEMA_VERSION_V3 for p in profiles.PROFILES.values())
    assert profiles.DEFAULT_PROFILE is profiles.V1


def test_nomes_el_perfil_i_la_canonada_importen_el_contracte_v3():
    """Fase 3: el perfil intern V3 i la seva branca. Cap router, job ni lector."""
    import re
    from pathlib import Path

    importer = re.compile(
        r"^\s*(?:from|import)\s+app\.ai\.(?:contract|schemas)_v3(?![0-9A-Za-z_])", re.M
    )
    app_root = Path(__file__).resolve().parents[1] / "app"
    users = [
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*.py")
        if importer.search(path.read_text(encoding="utf-8"))
    ]
    assert sorted(users) == [
        "ai/claim_matching.py",
        "ai/contract_v3.py",
        "ai/profiles.py",
        # Fase 3.5: guardes de lectura, perque cap candidat passi per criteri.
        "api/v1/routers/requirements.py",
        "compliance/engine.py",
        "evaluation/runner.py",
        # Replay offline: l'API exposa l'estat dels claims i els motius.
        "schemas/requirements.py",
        "services/requirement_analysis.py",
    ]


@pytest.mark.parametrize(
    ("basis", "counts"),
    [
        (None, True),  # v1/v2: es llegeix com sempre
        ({"award_class": "formal_award_criterion"}, True),
        ({"award_class": "candidate_award_criterion"}, False),
        ({"award_class": "technical_desirable"}, False),
        ({"award_class": "informative"}, False),
        ({}, False),  # mal formada: no compta
        ({"award_class": "inventada"}, False),
    ],
)
def test_nomes_compta_com_a_criteri_el_formal_o_el_v1(basis, counts):
    from app.ai.contract_v3 import award_class_of, counts_as_award_criterion

    assert counts_as_award_criterion(basis) is counts
    if basis is None or basis.get("award_class") == "inventada":
        assert award_class_of(basis) is None


def test_la_represa_reapunta_les_cites_de_la_base_d_adjudicacio():
    from app.ai.contract_v3 import remap_evidence_ids

    old, new, gone = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    basis = {
        "contract": "adjudication-basis-v1",
        "award_class": "formal_award_criterion",
        "mechanisms": [{"kind": "points", "check": "verified", "evidence_ids": [old, gone]}],
    }

    remapped = remap_evidence_ids(basis, {old: new})

    assert remapped["mechanisms"][0]["evidence_ids"] == [new]
    assert remapped["award_class"] == "formal_award_criterion"
    assert basis["mechanisms"][0]["evidence_ids"] == [old, gone], "no muta l'original"
    assert remap_evidence_ids(None, {old: new}) is None
