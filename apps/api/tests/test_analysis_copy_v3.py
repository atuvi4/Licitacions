"""Copy v3 del motor d'analisi (spec 3.3 i 3.11): oracle escrit a ma.

Cada `ExplanationCode` i cada `QuestionCode` de la v3 es compara amb el text exacte
esperat, escrit a ma en aquestes taules: son el lliurable de revisio humana. Les
preguntes es proven amb parametres coneguts (codis -> etiqueta) i desconeguts (codi
fora del vocabulari, `other`, parametre absent): mai surt el codi, surt l'expressio
neutra del parametre. La v2 no canvia: interpolacio crua, taula congelada.
"""

from __future__ import annotations

import copy
import re

import pytest

from app.analysis import registry
from app.analysis.registry import ExplanationCode as X
from app.analysis.registry import QuestionCode as Q
from app.knowledge import vocabulary
from app.models.enums import OperatingFactKey, ProofKind, RegistrationType, RequirementCategory

V2 = registry.RULES_VERSION_V2
V3 = registry.RULES_VERSION_V3

# --------------------------------------------------------------------------- #
# Oracle: explicacions v3                                                       #
# --------------------------------------------------------------------------- #

EXPECTED_EXPLANATIONS_V3: dict[str, str] = {
    X.FACT_TRUE_VERIFIED: "El fet consta com a actual i verificat.",
    X.FACT_TRUE_DECLARED: "El fet consta com a actual, declarat i pendent de verificar.",
    X.FACT_CONTRADICTS: "La dada actual de l'empresa contradiu el que el plec exigeix.",
    X.FACT_UNKNOWN: "No consta aquest fet amb una dada actual que es pugui comparar: no se sap.",
    X.FACT_HISTORIC_ONLY: "Només hi ha pistes històriques: no acrediten res.",
    X.FACT_TRUE_TERRITORY_UNMATCHED: (
        "El fet consta, però la seva cobertura declarada no inclou el territori exigit."
    ),
    X.CERT_CURRENT_VERIFIED: "Certificació actual i verificada.",
    X.CERT_CURRENT_DECLARED: "Certificació actual, declarada i pendent de verificar.",
    X.CERT_LEVEL_BELOW: "La certificació actual és d'un nivell inferior a l'exigit.",
    X.CERT_UNKNOWN: (
        "No consta cap certificació actual d'aquesta norma que es pugui comparar "
        "(nivell o abast): no se sap."
    ),
    X.CERT_HISTORIC_ONLY: "Només hi ha pistes històriques sobre la norma.",
    X.CERT_STANDARD_NOT_STRUCTURED: "El requisit no diu quina norma: no es compara.",
    X.NEEDS_ALL_SATISFIED: "Totes les necessitats mínimes es cobreixen amb dades actuals.",
    X.NEEDS_CONTRADICTED: "Una dada actual diu que una necessitat mínima no es cobreix.",
    X.NEEDS_UNKNOWN: "Alguna necessitat mínima no té dada actual: no se sap.",
    X.NEEDS_NONE_MINIMUM: "Cap necessitat mínima estructurada: no se sap.",
    X.PROFILE_DATA_NOT_IN_SNAPSHOT: (
        "Depèn de dades del perfil (solvència, assegurança, personal...) que aquest pas no llegeix."
    ),
    X.CONTRACT_CONDITION_NOT_COMPARABLE: (
        "Condició del contracte o de l'oferta: no es compara amb cap dada d'empresa."
    ),
    X.NO_STRUCTURE_TO_COMPARE: "El requisit no té cap estructura comparable.",
    X.PROOF_MISSING_FOR_CLAIM: "La dada consta, però la prova exigida no es pot demostrar.",
    X.CAPABILITY_SUPPORTED: "Una solució actual cobreix la necessitat.",
    X.CAPABILITY_SUPPORTED_WITH_LIMITS: "Una solució actual la cobreix amb límits.",
    X.CAPABILITY_NOT_SUPPORTED: "La solució actual declara que no ho fa.",
    X.CAPABILITY_BELOW_REQUIRED: "La solució actual ho fa, però per sota del valor exigit.",
    X.CAPABILITY_UNKNOWN: (
        "Cap solució actual declara aquesta capacitat amb una dada que es pugui comparar: "
        "no se sap."
    ),
    X.CAPABILITY_HISTORIC_ONLY: "Només hi ha pistes històriques sobre la capacitat.",
    X.CAPABILITY_UNMAPPED: (
        "La necessitat no està associada a cap capacitat del vocabulari: no s'interpreta."
    ),
    X.NEED_VALUE_NOT_COMPARABLE: "El valor exigit no es pot comparar amb el vocabulari.",
    X.CRITERION_SECURED: "Punts assegurats amb dades actuals verificades.",
    X.CRITERION_AT_RISK: (
        "Punts d'un compromís encara obert: dades declarades sense verificar o sense dada actual."
    ),
    X.CRITERION_LOST: "Una dada actual demostra que el criteri no es pot assolir.",
    X.CRITERION_MIXED: "Els punts es reparteixen entre més d'un estat.",
    X.CRITERION_NOT_ASSESSABLE_UNKNOWN: "Sense dada actual suficient: no s'avalua.",
    X.CRITERION_NOT_ASSESSABLE_JUDGEMENT: (
        "Judici de valor o demostració: la qualitat no s'avalua en aquest pas."
    ),
    X.CRITERION_NOT_ASSESSABLE_PRICE: "Criteri de preu: l'economia no és cosa d'aquest pas.",
    X.CRITERION_OPEN_COMMITMENT: (
        "Compromís automàtic sense cap dada actual que l'asseguri ni el contradigui: en risc."
    ),
    X.CRITERION_TIMING_NOT_CONCLUSIVE: (
        "La temporalitat del criteri no permet concloure que els punts es perden."
    ),
    X.CRITERION_NEEDS_UNKNOWN: (
        "Hi ha punts en risc perquè no consta cap dada actual de l'empresa sobre el que "
        "demana el criteri."
    ),
    X.CRITERION_NEEDS_NOT_COMPARABLE: (
        "Hi ha punts en risc perquè la dada actual de l'empresa no es pot comparar amb el "
        "que demana el criteri."
    ),
    X.TERRITORY_MATCHED: "El territori exigit és a la cobertura declarada.",
    X.TERRITORY_UNMATCHED: "La cobertura declarada és completa i no inclou el territori.",
    X.TERRITORY_UNKNOWN: "No es pot demostrar que el territori sigui cobert.",
    X.TERRITORY_LIST_UNAVAILABLE: "El plec no dona la llista de territoris (document absent).",
    X.TERRITORY_NOT_APPLICABLE: "Sense dimensió territorial.",
    X.DELIVERY_OWN_CURRENT: "Servei actual amb mitjans propis.",
    X.DELIVERY_PARTNER_CURRENT: "Servei actual a través d'un tercer amb paper compatible.",
    X.DELIVERY_NO_PARTNER_ROLE: "Servei actual per tercers, sense cap tercer compatible declarat.",
    X.DELIVERY_MODE_UNKNOWN: "Servei actual sense dir com es presta.",
    X.DELIVERY_NO_SERVICE: "Cap servei actual d'aquesta línia.",
    X.MANDATORY_DOES_NOT_SATISFY: (
        "Requisit obligatori exigit en presentar que una dada actual contradiu."
    ),
    X.MANDATORY_DOES_NOT_SATISFY_LATER: (
        "Requisit obligatori que una dada actual contradiu, però exigit després de presentar: "
        "cal revisar-ho, no és cap bloqueig a la presentació."
    ),
    X.DECLARED_NOT_VERIFIED: "Es compleix amb dades declarades: cal verificar-les.",
    X.DATA_MISSING: "Falta una dada de l'empresa per decidir.",
    X.DATA_MISSING_UNATTRIBUTED: "Falta una dada per decidir.",
    X.PROOF_MISSING: "Falta la prova exigida.",
    X.NO_AVAILABLE_DELIVERY: "Cap via de prestació disponible amb el coneixement actual.",
    X.DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES: (
        "La demostració depèn de capacitats que l'empresa no ha declarat."
    ),
    X.QUALIFICATION_THRESHOLD_NOT_COMPARABLE: (
        "El llindar del plec no es pot llegir com a dada estructurada: no es compara."
    ),
    X.QUALIFICATION_REFERENCE_DATE_MISSING: (
        "El plec no dona el termini de presentació: no se sap quins són els darrers "
        "exercicis ni els darrers anys."
    ),
    X.TENDER_REFERENCE_DATE_MISSING: (
        "El plec no fixa el termini de presentació, que és la data de referència per "
        "calcular aquest requisit. Cal demanar-lo a l'òrgan."
    ),
    X.TURNOVER_MET: "Algun dels darrers exercicis arriba al volum exigit.",
    X.TURNOVER_BELOW: "Els darrers exercicis consten tots i cap no arriba al volum exigit.",
    X.TURNOVER_UNKNOWN: (
        "No consten els darrers exercicis consecutius (o algun import): no se sap."
    ),
    X.EXECUTION_MET: "L'import executat l'any de major execució arriba a l'exigit.",
    X.EXECUTION_BELOW: "Amb tots els imports anuals coneguts, cap any de la finestra hi arriba.",
    X.EXECUTION_UNKNOWN: (
        "Imports sense desglossar per any o cap referència a la finestra: no se sap "
        "(les ofertes no compten)."
    ),
    X.INSURANCE_MET: "Una pòlissa vigent cobreix tots els límits exigits.",
    X.INSURANCE_BELOW: "Un límit declarat de la pòlissa vigent no hi arriba.",
    X.INSURANCE_UNKNOWN: (
        "Cap pòlissa vigent del tipus, o algun límit exigit no consta: no se sap."
    ),
    X.INSURANCE_EXPIRED: "La pòlissa d'aquest tipus ha caducat.",
    X.REGISTRATION_MET: "Inscripció vigent amb l'abast estructurat que cobreix el que es demana.",
    X.REGISTRATION_SCOPE_UNKNOWN: (
        "Inscripció vigent amb l'abast sense estructurar: no se sap si cobreix el plec."
    ),
    X.REGISTRATION_SCOPE_NOT_COVERED: (
        "L'abast inscrit no cobreix tots els serveis del plec: cal revisar-lo."
    ),
    X.REGISTRATION_UNKNOWN: "Cap inscripció vigent d'aquest tipus: no se sap.",
    X.REGISTRATION_ONLY_OPERATOR_FACT: (
        "Consta el fet d'operador, però cap inscripció amb abast: no se sap quins serveis."
    ),
    X.RESOURCES_MET: (
        "Hi ha persones disponibles amb els perfils exigits i els equips exigits, amb el "
        "calibratge vigent si cal."
    ),
    X.PROFILES_MISSING: "Algun perfil exigit no té cap persona disponible declarada: no se sap.",
    X.EQUIPMENT_MISSING: "Algun equip exigit no consta: no se sap.",
    X.EQUIPMENT_CALIBRATION_UNKNOWN: (
        "L'equip consta, però sense data de calibratge: no se sap si és vigent."
    ),
    X.EQUIPMENT_CALIBRATION_EXPIRED: "L'equip exigit consta, però amb el calibratge caducat.",
    X.FACT_NUMBER_MEETS: "El fet quantitatiu actual arriba al valor exigit.",
    X.FACT_NUMBER_BELOW: "El fet quantitatiu actual no arriba al valor exigit (es pot ampliar).",
}


#: Codis que nomes existeixen a la v4 (3B-2): la taula v3 queda congelada sense ells.
V4_ONLY_EXPLANATIONS: dict[str, str] = {
    X.PACKAGE_GAP: (
        "Al paquet documental del plec li falta un document: el que hi diria no es pot avaluar."
    ),
    X.ROLE_OVERRIDE_DIVERGES: (
        "Els tipus de document que ha fixat l'empresa difereixen dels de la plataforma: "
        "aquesta anàlisi llegeix els de la plataforma."
    ),
}


def test_the_oracle_covers_every_explanation_code():
    assert set(EXPECTED_EXPLANATIONS_V3) == registry.EXPLANATION_CODES - set(V4_ONLY_EXPLANATIONS)


@pytest.mark.parametrize("code", sorted(V4_ONLY_EXPLANATIONS))
def test_the_v4_only_explanations_are_exact_human_catalan(code):
    assert registry.explanation_text(code, registry.RULES_VERSION_V4) == V4_ONLY_EXPLANATIONS[code]
    assert registry.explanation_text(code, V3) == registry.UNKNOWN_EXPLANATION_TEXT
    assert offending(V4_ONLY_EXPLANATIONS[code]) == []


@pytest.mark.parametrize("code", sorted(EXPECTED_EXPLANATIONS_V3))
def test_every_v3_explanation_is_exactly_the_expected_text(code):
    assert registry.explanation_text(code, V3) == EXPECTED_EXPLANATIONS_V3[code]


# --------------------------------------------------------------------------- #
# Oracle: preguntes v3                                                          #
# --------------------------------------------------------------------------- #

#: (codi, params, text esperat). `known` = codis del vocabulari i text lliure;
#: `unknown` = codi fora del vocabulari, `other`, buit, d'un altre tipus o absent.
EXPECTED_QUESTIONS_V3: list[tuple[str, str, dict | None, str]] = [
    # company.confirm_fact
    (
        Q.COMPANY_CONFIRM_FACT,
        "known",
        {"fact": "support_24x7", "title": "Suport tècnic permanent"},
        "Confirmeu si l'empresa compleix «Suport 24x7» (requisit «Suport tècnic permanent»).",
    ),
    (
        Q.COMPANY_CONFIRM_FACT,
        "known",
        {"fact": "own_number_range_nrn", "title": "Numeració pròpia"},
        "Confirmeu si l'empresa compleix «Rang de numeració propi (NRN)» "
        "(requisit «Numeració pròpia»).",
    ),
    (
        Q.COMPANY_CONFIRM_FACT,
        "unknown",
        {"fact": "satellite_uplink", "title": "Enllaç per satèl·lit"},
        "Confirmeu si l'empresa compleix la condició que demana el plec "
        "(requisit «Enllaç per satèl·lit»).",
    ),
    (
        Q.COMPANY_CONFIRM_FACT,
        "unknown",
        None,
        "Confirmeu si l'empresa compleix la condició que demana el plec (requisit sense títol).",
    ),
    # company.declare_capability
    (
        Q.COMPANY_DECLARE_CAPABILITY,
        "known",
        {"capability": "pbx.hot_desking", "service_line": "voice_pbx"},
        "Declareu si la solució actual per a «Centraleta i veu IP» cobreix «Hot desking».",
    ),
    (
        Q.COMPANY_DECLARE_CAPABILITY,
        "known",
        {"capability": "fiber.otdr_level", "service_line": "fiber_deployment"},
        "Declareu si la solució actual per a «Desplegament i certificació de fibra» cobreix "
        "«Nivell de certificació de fibra (1 = OLTS, 2 = OTDR)».",
    ),
    (
        Q.COMPANY_DECLARE_CAPABILITY,
        "unknown",
        {"capability": "pbx.teleport", "service_line": "quantum_link"},
        "Declareu si la solució actual per a una línia de servei del plec cobreix "
        "una capacitat que demana el plec.",
    ),
    (
        Q.COMPANY_DECLARE_CAPABILITY,
        "unknown",
        {},
        "Declareu si la solució actual per a una línia de servei del plec cobreix "
        "una capacitat que demana el plec.",
    ),
    # company.provide_proof
    (
        Q.COMPANY_PROVIDE_PROOF,
        "known",
        {"proof_kind": "calibration_certificate", "title": "Equips de mesura"},
        "Aporteu la prova «Certificat de calibratge» per al requisit «Equips de mesura».",
    ),
    (
        Q.COMPANY_PROVIDE_PROOF,
        "known",
        {"proof_kind": "responsible_declaration", "title": "Mitjans adscrits"},
        "Aporteu la prova «Declaració responsable» per al requisit «Mitjans adscrits».",
    ),
    (
        Q.COMPANY_PROVIDE_PROOF,
        "known",
        {"proof_kind": "annual_accounts", "title": "Volum de negoci"},
        "Aporteu la prova «Comptes anuals» per al requisit «Volum de negoci».",
    ),
    (
        Q.COMPANY_PROVIDE_PROOF,
        "unknown",
        {"proof_kind": "other", "title": "Mitjans adscrits"},
        "Aporteu la prova que demana el plec per al requisit «Mitjans adscrits».",
    ),
    (
        Q.COMPANY_PROVIDE_PROOF,
        "unknown",
        {"proof_kind": "notarial_deed", "title": "   "},
        "Aporteu la prova que demana el plec per al requisit sense títol.",
    ),
    # company.provide_profile_data
    (
        Q.COMPANY_PROVIDE_PROFILE_DATA,
        "known",
        {"category": "economic_solvency", "title": "Volum de negoci"},
        "Informeu les dades de la categoria «Solvència econòmica» per al requisit "
        "«Volum de negoci».",
    ),
    (
        Q.COMPANY_PROVIDE_PROFILE_DATA,
        "known",
        {"category": "insurance", "title": "Assegurança de responsabilitat civil"},
        "Informeu les dades de la categoria «Assegurances» per al requisit "
        "«Assegurança de responsabilitat civil».",
    ),
    (
        Q.COMPANY_PROVIDE_PROFILE_DATA,
        "unknown",
        {"category": "other", "title": "Volum de negoci"},
        "Informeu les dades de la categoria que correspon per al requisit «Volum de negoci».",
    ),
    (
        Q.COMPANY_PROVIDE_PROFILE_DATA,
        "unknown",
        {"category": None, "title": None},
        "Informeu les dades de la categoria que correspon per al requisit sense títol.",
    ),
    # company.declare_certification
    (
        Q.COMPANY_DECLARE_CERTIFICATION,
        "known",
        {"standard": "ENS"},
        "Declareu si l'empresa té la certificació «Esquema Nacional de Seguretat».",
    ),
    (
        Q.COMPANY_DECLARE_CERTIFICATION,
        "known",
        {"standard": "ISO_27001"},
        "Declareu si l'empresa té la certificació «ISO/IEC 27001 (seguretat de la informació)».",
    ),
    (
        Q.COMPANY_DECLARE_CERTIFICATION,
        "unknown",
        {"standard": "ISO_45001"},
        "Declareu si l'empresa té la certificació que demana el plec.",
    ),
    (
        Q.COMPANY_DECLARE_CERTIFICATION,
        "unknown",
        {"standard": None},
        "Declareu si l'empresa té la certificació que demana el plec.",
    ),
    # company.declare_delivery_mode
    (
        Q.COMPANY_DECLARE_DELIVERY_MODE,
        "known",
        {"service_line": "access_ftth"},
        "Digueu com presta l'empresa «Accessos FTTH».",
    ),
    (
        Q.COMPANY_DECLARE_DELIVERY_MODE,
        "known",
        {"service_line": "voice_trunk"},
        "Digueu com presta l'empresa «Troncals de veu i numeració».",
    ),
    (
        Q.COMPANY_DECLARE_DELIVERY_MODE,
        "unknown",
        {"service_line": "quantum_link"},
        "Digueu com presta l'empresa una línia de servei del plec.",
    ),
    (
        Q.COMPANY_DECLARE_DELIVERY_MODE,
        "unknown",
        {"service_line": 7},
        "Digueu com presta l'empresa una línia de servei del plec.",
    ),
    # company.declare_partner
    (
        Q.COMPANY_DECLARE_PARTNER,
        "known",
        {"service_line": "mobile_telephony"},
        "Declareu un tercer per prestar «Telefonia mòbil».",
    ),
    (
        Q.COMPANY_DECLARE_PARTNER,
        "unknown",
        {"service_line": "quantum_link"},
        "Declareu un tercer per prestar una línia de servei del plec.",
    ),
    # company.confirm_coverage
    (
        Q.COMPANY_CONFIRM_COVERAGE,
        "known",
        {"service_line": "radio_link"},
        "Confirmeu la cobertura per a «Radioenllaços» als territoris on s'ha de prestar.",
    ),
    (
        Q.COMPANY_CONFIRM_COVERAGE,
        "unknown",
        None,
        "Confirmeu la cobertura per a una línia de servei del plec als territoris on s'ha de "
        "prestar.",
    ),
    # buyer.provide_territory_list
    (
        Q.BUYER_PROVIDE_TERRITORY_LIST,
        "known",
        {"title": "Cobertura FTTH als nuclis"},
        "Demaneu a l'òrgan la llista de territoris del requisit «Cobertura FTTH als nuclis».",
    ),
    (
        Q.BUYER_PROVIDE_TERRITORY_LIST,
        "unknown",
        {"title": ""},
        "Demaneu a l'òrgan la llista de territoris del requisit sense títol.",
    ),
    # buyer.clarify_criterion_timing
    (
        Q.BUYER_CLARIFY_CRITERION_TIMING,
        "known",
        {"name": "Millores de cobertura"},
        "Demaneu a l'òrgan quan s'ha de complir el criteri «Millores de cobertura».",
    ),
    (
        Q.BUYER_CLARIFY_CRITERION_TIMING,
        "unknown",
        None,
        "Demaneu a l'òrgan quan s'ha de complir el criteri sense nom.",
    ),
    # buyer.clarify_requirement_timing
    (
        Q.BUYER_CLARIFY_REQUIREMENT_TIMING,
        "known",
        {"title": "Centre de dades Tier-3"},
        "Demaneu a l'òrgan quan s'ha de complir el requisit «Centre de dades Tier-3».",
    ),
    (
        Q.BUYER_CLARIFY_REQUIREMENT_TIMING,
        "unknown",
        {"title": None},
        "Demaneu a l'òrgan quan s'ha de complir el requisit sense títol.",
    ),
    # company.review_registration_scope
    (
        Q.COMPANY_REVIEW_REGISTRATION_SCOPE,
        "known",
        {"registration_type": "sector_operator_registry", "title": "Operador inscrit"},
        "Reviseu l'abast de la inscripció a «Registre sectorial d'operadors» per al requisit "
        "«Operador inscrit».",
    ),
    (
        Q.COMPANY_REVIEW_REGISTRATION_SCOPE,
        "known",
        {"registration_type": "rolece", "title": "Inscripció al ROLECE"},
        "Reviseu l'abast de la inscripció a «ROLECE» per al requisit «Inscripció al ROLECE».",
    ),
    (
        Q.COMPANY_REVIEW_REGISTRATION_SCOPE,
        "unknown",
        {"registration_type": None, "title": "Operador inscrit"},
        "Reviseu l'abast de la inscripció a un registre que demana el plec per al requisit "
        "«Operador inscrit».",
    ),
    (
        Q.COMPANY_REVIEW_REGISTRATION_SCOPE,
        "unknown",
        {"registration_type": "other", "title": "Operador inscrit"},
        "Reviseu l'abast de la inscripció a un registre que demana el plec per al requisit "
        "«Operador inscrit».",
    ),
    # company.renew_calibration
    (
        Q.COMPANY_RENEW_CALIBRATION,
        "known",
        {"title": "Equips de certificació de fibra"},
        "Confirmeu o renoveu el calibratge dels equips del requisit "
        "«Equips de certificació de fibra».",
    ),
    (
        Q.COMPANY_RENEW_CALIBRATION,
        "unknown",
        None,
        "Confirmeu o renoveu el calibratge dels equips del requisit sense títol.",
    ),
    # buyer.provide_submission_deadline
    (
        Q.BUYER_PROVIDE_SUBMISSION_DEADLINE,
        "known",
        {"title": "Volum de negoci"},
        "Demaneu a l'òrgan el termini de presentació d'ofertes "
        "(cal per calcular el requisit «Volum de negoci»).",
    ),
    (
        Q.BUYER_PROVIDE_SUBMISSION_DEADLINE,
        "unknown",
        {},
        "Demaneu a l'òrgan el termini de presentació d'ofertes "
        "(cal per calcular el requisit sense títol).",
    ),
]


def test_the_oracle_covers_every_question_code_with_known_and_unknown_params():
    seen = {(code, kind) for code, kind, _, _ in EXPECTED_QUESTIONS_V3}
    for code in registry.QUESTION_CODES:
        assert (code, "known") in seen, code
        assert (code, "unknown") in seen, code


@pytest.mark.parametrize(
    ("code", "kind", "params", "expected"),
    EXPECTED_QUESTIONS_V3,
    ids=[f"{code}-{kind}-{i}" for i, (code, kind, _, _) in enumerate(EXPECTED_QUESTIONS_V3)],
)
def test_every_v3_question_is_exactly_the_expected_text(code, kind, params, expected):
    assert registry.question_text(code, params, V3) == expected


def test_rendering_never_touches_the_stored_params():
    """`params_json` continua amb codis: forma part de la clau de deduplicacio."""
    params = {"capability": "pbx.hot_desking", "service_line": "voice_pbx"}
    before = copy.deepcopy(params)
    registry.question_text(Q.COMPANY_DECLARE_CAPABILITY, params, V3)
    assert params == before


def test_v2_keeps_its_raw_interpolation():
    """La taula v2 no canvia: els codis surten crus, com a 7513e3a."""
    assert (
        registry.question_text(
            Q.COMPANY_DECLARE_CAPABILITY,
            {"capability": "pbx.hot_desking", "service_line": "voice_pbx"},
            V2,
        )
        == "Declareu si la solucio actual de «voice_pbx» cobreix «pbx.hot_desking»."
    )
    assert (
        registry.question_text(Q.COMPANY_CONFIRM_FACT, None, V2)
        == registry.QUESTIONS_BY_VERSION[V2][Q.COMPANY_CONFIRM_FACT]
    )
    assert registry.explanation_text(X.FACT_HISTORIC_ONLY, V2) == (
        "Nomes hi ha pistes historiques: no acrediten res."
    )


# --------------------------------------------------------------------------- #
# Regressio: cap codi intern ni cap forma sense accent                         #
# --------------------------------------------------------------------------- #

CODE_TOKENS = (re.compile(r"\b[a-z]+_[a-z0-9_]+\b"), re.compile(r"\b[a-z]+\.[a-z_]+\b"))
FORBIDDEN_UNACCENTED = (
    "solucio",
    "organ",
    "prestacio",
    "numeracio",
    "pero",
    "polissa",
    "historiques",
    "minima",
    "minimes",
    "Certificacio",
    "demostracio",
    "Depen",
    "calibracio",
)
FORBIDDEN_RE = re.compile(r"\b(" + "|".join(FORBIDDEN_UNACCENTED) + r")\b", re.IGNORECASE)


def offending(text: str) -> list[str]:
    """Els fragments d'un text huma que semblen codis o formes sense accent."""
    found = [m.group(0) for pattern in CODE_TOKENS for m in pattern.finditer(text)]
    found += [m.group(0) for m in FORBIDDEN_RE.finditer(text)]
    return found + [brace for brace in ("{", "}") if brace in text]


def _all_rendered_v3_texts() -> list[str]:
    texts = [registry.explanation_text(code, V3) for code in sorted(registry.EXPLANATION_CODES)]
    texts += [registry.question_text(c, p, V3) for c, _, p, _ in EXPECTED_QUESTIONS_V3]
    # Cada codi del vocabulari en cada parametre on pot sortir.
    for line in vocabulary.SERVICE_LINES:
        texts.append(
            registry.question_text(Q.COMPANY_DECLARE_PARTNER, {"service_line": line.code}, V3)
        )
    for cap in vocabulary.CAPABILITIES:
        texts.append(
            registry.question_text(
                Q.COMPANY_DECLARE_CAPABILITY,
                {"capability": cap.code, "service_line": cap.service_line_code},
                V3,
            )
        )
    for standard in vocabulary.CERTIFICATION_STANDARDS:
        texts.append(
            registry.question_text(Q.COMPANY_DECLARE_CERTIFICATION, {"standard": standard.code}, V3)
        )
    for kind in ProofKind:
        texts.append(
            registry.question_text(
                Q.COMPANY_PROVIDE_PROOF, {"proof_kind": kind.value, "title": "T"}, V3
            )
        )
    for category in RequirementCategory:
        texts.append(
            registry.question_text(
                Q.COMPANY_PROVIDE_PROFILE_DATA, {"category": category.value, "title": "T"}, V3
            )
        )
    for registration in RegistrationType:
        texts.append(
            registry.question_text(
                Q.COMPANY_REVIEW_REGISTRATION_SCOPE,
                {"registration_type": registration.value, "title": "T"},
                V3,
            )
        )
    for fact in OperatingFactKey:
        texts.append(
            registry.question_text(Q.COMPANY_CONFIRM_FACT, {"fact": fact.value, "title": "T"}, V3)
        )
    return texts


def test_no_v3_text_carries_internal_codes_or_unaccented_forms():
    bad = {text: offending(text) for text in _all_rendered_v3_texts() if offending(text)}
    assert bad == {}


def test_the_scan_does_catch_what_it_must():
    """El detector no es buit: rebutja el copy v2 i els codis crus."""
    assert offending("Nomes pistes historiques sobre la capacitat.") == ["historiques"]
    assert offending("Cobreix «pbx.hot_desking» a «voice_pbx»?") == [
        "hot_desking",
        "voice_pbx",
        "pbx.hot_desking",
    ]
    assert offending("Demaneu a l'organ el {title}.") == ["organ", "{", "}"]
    assert offending("Depen de la solucio, pero no de la polissa.") == [
        "Depen",
        "solucio",
        "pero",
        "polissa",
    ]
    assert offending("La solució de l'òrgan, però amb la pòlissa.") == []
    # «calibratge» es catala correcte (DIEC); la forma sense accent prohibida es «calibracio».
    assert offending("L'equip consta, amb el calibratge caducat.") == []
    assert offending("Sense data de calibracio.") == ["calibracio"]


# --------------------------------------------------------------------------- #
# Etiquetes del vocabulari                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("name", "enum"),
    [
        ("PROOF_KIND_LABELS", ProofKind),
        ("REQUIREMENT_CATEGORY_LABELS", RequirementCategory),
        ("REGISTRATION_TYPE_LABELS", RegistrationType),
        ("OPERATING_FACT_LABELS", OperatingFactKey),
    ],
)
def test_every_new_label_map_covers_its_enum_exactly(name, enum):
    labels = getattr(vocabulary, name)
    assert set(labels) == {member.value for member in enum}
    assert all(isinstance(label, str) and label.strip() for label in labels.values())


def test_every_vocabulary_label_is_human_text():
    labels = [
        *(s.label for s in vocabulary.SERVICE_LINES),
        *(c.label for c in vocabulary.CAPABILITIES),
        *(s.label for s in vocabulary.CERTIFICATION_STANDARDS),
        *(p.label for p in vocabulary.PROFESSIONAL_PROFILES),
        *vocabulary.EQUIPMENT_TYPE_LABELS.values(),
        *vocabulary.PROOF_KIND_LABELS.values(),
        *vocabulary.REQUIREMENT_CATEGORY_LABELS.values(),
        *vocabulary.REGISTRATION_TYPE_LABELS.values(),
        *vocabulary.OPERATING_FACT_LABELS.values(),
    ]
    assert {label: offending(label) for label in labels if offending(label)} == {}


def test_the_interpolated_vocabulary_labels_carry_their_accents():
    lines = {s.code: s.label for s in vocabulary.SERVICE_LINES}
    assert lines["voice_trunk"] == "Troncals de veu i numeració"
    assert lines["radio_link"] == "Radioenllaços"
    assert lines["network_switching"] == "Electrònica de xarxa"
    assert lines["fiber_deployment"] == "Desplegament i certificació de fibra"
    assert lines["support_ops"] == "Suport, operació i nivells de servei"
    assert lines["mobile_telephony"] == "Telefonia mòbil"
    caps = vocabulary.CAPABILITIES_BY_CODE
    assert caps["pbx.concurrent_calls"].label == "Trucades simultànies"
    assert caps["pbx.blf"].label == "Estat de línia (BLF)"
    assert caps["pbx.voicemail_email"].label == "Bústia amb avís per correu"
    assert caps["switch.poe_standard"].label == "Estàndard PoE"
    assert vocabulary.STANDARDS_BY_CODE["ISO_9001"].label == "ISO 9001 (gestió de la qualitat)"
    # Les unitats son claus que el web tradueix: no canvien.
    assert caps["pbx.outbound_number_select"].unit == "numeros"
    assert caps["pbx.extra_softphones_free"].unit == "llicencies"
