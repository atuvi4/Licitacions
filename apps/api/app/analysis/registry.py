"""Registre unic de regles, explicacions i preguntes del motor d'analisi (0051).

Tres coses viuen aqui i enlloc mes: la versio de les regles, els codis de regla,
els codis d'explicacio i els codis de pregunta amb la seva plantilla. Son
identificadors **tancats i estables**: cada fila que el motor escriu en porta un,
i la interficie en deriva el text. Cap cadena arbitraria pel motor.

Canviar el significat d'una regla obliga a pujar `RULES_VERSION`: entra a
l'`inputs_hash` de cada run, de manera que un resultat calculat amb regles
diferents no es confon mai amb un d'actual.

El text es versiona amb les regles (`EXPLANATIONS_BY_VERSION`, `QUESTIONS_BY_VERSION`):
cada run es renderitza amb la taula de la seva `rules_version`, i les taules d'una
versio publicada queden congelades.
"""

from __future__ import annotations

from collections.abc import Mapping
from string import Formatter
from types import MappingProxyType

from app.knowledge import vocabulary

#: v2 (0052): llegeix solvencia, referencies d'execucio, assegurances, registres amb
#: abast, perfils, equips i fets quantitatius. Canviar-la fa caducar les runs v1.
RULES_VERSION_V2 = "phase3a-analysis-v2"
#: v3: empremta numericament canonica i copy propi. Canviar-la fa caducar les runs v2,
#: pero no en canvia el text: cada run es llegeix amb la taula de la seva versio.
RULES_VERSION_V3 = "phase3a-analysis-v3"
#: v4 (3B-2 §4.4 i §3.2): cada forat obert del paquet documental es una
#: `missing_information` del plec, una seccio buida i `unknown` per un forat no dona
#: cap avaluacio positiva, i la divergencia de rols de l'empresa es una troballa. El
#: copy es el de la v3 (cap codi canvia de significat). Fa caducar les runs v3.
RULES_VERSION_V4 = "phase3b-analysis-v4"
RULES_VERSION = RULES_VERSION_V4

#: Text neutre quan la versio (o el codi dins la seva taula) no te text. Mai el d'una
#: altra versio.
UNKNOWN_EXPLANATION_TEXT = "Explicació no disponible."
UNKNOWN_QUESTION_TEXT = "Pregunta no disponible."


class RuleCode:
    """Quina regla ha produit una fila. Una per familia, versionada."""

    #: Requisit que es llegeix d'un fet operatiu de l'empresa (`value_json.operating_fact`).
    FACT_REQUIREMENT = "fact_requirement_v1"
    #: Qualificacio (0052): `value_json.turnover_min_eur`, `execution_min_eur`,
    #: `insurance_type`, `registration_type`, `required_profiles`/`required_equipment`.
    TURNOVER_REQUIREMENT = "turnover_requirement_v1"
    EXECUTION_REQUIREMENT = "execution_requirement_v1"
    INSURANCE_REQUIREMENT = "insurance_requirement_v1"
    REGISTRATION_REQUIREMENT = "registration_requirement_v1"
    RESOURCES_REQUIREMENT = "resources_requirement_v1"
    #: Necessitat que es llegeix d'un fet quantitatiu (`value_json.operating_fact`).
    NEED_OPERATING_FACT = "need_operating_fact_v1"
    #: Requisit de certificacio amb norma (`value_json.standard`) i nivell opcional.
    CERTIFICATION_REQUIREMENT = "certification_requirement_v1"
    #: Requisit tecnic avaluat per les seves necessitats estructurades.
    CAPABILITY_NEEDS_REQUIREMENT = "capability_needs_requirement_v1"
    #: Requisit que depen de dades del perfil que la fotografia no te (solvencia, RC...).
    PROFILE_DATA_REQUIREMENT = "profile_data_requirement_v1"
    #: Condicio contractual, formal o de termini: no es compara amb cap dada d'empresa.
    CONTRACT_CONDITION_REQUIREMENT = "contract_condition_requirement_v1"
    #: Requisit sense cap estructura comparable.
    UNSTRUCTURED_REQUIREMENT = "unstructured_requirement_v1"
    #: Territori del requisit contra la cobertura: nomes identitat explicita.
    TERRITORY_IDENTITY = "territory_identity_v1"
    #: Proves exigides contra el que el model pot demostrar.
    PROOF_ITEMS = "proof_items_v1"
    #: Necessitat mapada al vocabulari contra les capacitats de les solucions actuals.
    NEED_CAPABILITY = "need_capability_v1"
    #: Necessitat sense codi global: no s'interpreta.
    NEED_UNMAPPED = "need_unmapped_v1"
    #: Criteri automatic avaluat per les seves necessitats puntuades (amb trams o sense).
    CRITERION_FROM_NEEDS = "criterion_from_needs_v1"
    #: Criteri automatic avaluat per la seva cara exigible (requisit enllacat).
    CRITERION_FROM_REQUIREMENT = "criterion_from_requirement_v1"
    #: Criteri de judici de valor o demostracio: la qualitat no s'avalua.
    CRITERION_JUDGEMENT = "criterion_judgement_v1"
    #: Criteri de preu: l'economia no es cosa d'aquest pas.
    CRITERION_PRICE = "criterion_price_v1"
    #: Criteri automatic sense cap estructura comparable.
    CRITERION_UNSTRUCTURED = "criterion_unstructured_v1"
    #: Vies de prestacio nomes des dels serveis actuals.
    DELIVERY_FROM_SERVICES = "delivery_from_services_v1"
    #: Troballes.
    BLOCKER_POTENTIAL = "blocker_potential_v1"
    REVIEW_REQUIRED = "review_required_v1"
    MISSING_INFORMATION = "missing_information_v1"
    EVIDENCE_GAP = "evidence_gap_v1"
    TERRITORY_GAP = "territory_gap_v1"
    DELIVERY_GAP = "delivery_gap_v1"
    DEMO_DEPENDENCY = "demo_dependency_warning_v1"


class ExplanationCode:
    """Per que una fila diu el que diu. Tancat; la interficie en deriva el text."""

    FACT_TRUE_VERIFIED = "FACT_TRUE_VERIFIED"
    FACT_TRUE_DECLARED = "FACT_TRUE_DECLARED"
    FACT_CONTRADICTS = "FACT_CONTRADICTS"
    FACT_UNKNOWN = "FACT_UNKNOWN"
    FACT_HISTORIC_ONLY = "FACT_HISTORIC_ONLY"
    FACT_TRUE_TERRITORY_UNMATCHED = "FACT_TRUE_TERRITORY_UNMATCHED"
    CERT_CURRENT_VERIFIED = "CERT_CURRENT_VERIFIED"
    CERT_CURRENT_DECLARED = "CERT_CURRENT_DECLARED"
    CERT_LEVEL_BELOW = "CERT_LEVEL_BELOW"
    CERT_UNKNOWN = "CERT_UNKNOWN"
    CERT_HISTORIC_ONLY = "CERT_HISTORIC_ONLY"
    CERT_STANDARD_NOT_STRUCTURED = "CERT_STANDARD_NOT_STRUCTURED"
    NEEDS_ALL_SATISFIED = "NEEDS_ALL_SATISFIED"
    NEEDS_CONTRADICTED = "NEEDS_CONTRADICTED"
    NEEDS_UNKNOWN = "NEEDS_UNKNOWN"
    NEEDS_NONE_MINIMUM = "NEEDS_NONE_MINIMUM"
    PROFILE_DATA_NOT_IN_SNAPSHOT = "PROFILE_DATA_NOT_IN_SNAPSHOT"
    CONTRACT_CONDITION_NOT_COMPARABLE = "CONTRACT_CONDITION_NOT_COMPARABLE"
    NO_STRUCTURE_TO_COMPARE = "NO_STRUCTURE_TO_COMPARE"
    PROOF_MISSING_FOR_CLAIM = "PROOF_MISSING_FOR_CLAIM"
    CAPABILITY_SUPPORTED = "CAPABILITY_SUPPORTED"
    CAPABILITY_SUPPORTED_WITH_LIMITS = "CAPABILITY_SUPPORTED_WITH_LIMITS"
    CAPABILITY_NOT_SUPPORTED = "CAPABILITY_NOT_SUPPORTED"
    CAPABILITY_BELOW_REQUIRED = "CAPABILITY_BELOW_REQUIRED"
    CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
    CAPABILITY_HISTORIC_ONLY = "CAPABILITY_HISTORIC_ONLY"
    CAPABILITY_UNMAPPED = "CAPABILITY_UNMAPPED"
    NEED_VALUE_NOT_COMPARABLE = "NEED_VALUE_NOT_COMPARABLE"
    CRITERION_SECURED = "CRITERION_SECURED"
    CRITERION_AT_RISK = "CRITERION_AT_RISK"
    CRITERION_LOST = "CRITERION_LOST"
    CRITERION_MIXED = "CRITERION_MIXED"
    CRITERION_NOT_ASSESSABLE_UNKNOWN = "CRITERION_NOT_ASSESSABLE_UNKNOWN"
    CRITERION_NOT_ASSESSABLE_JUDGEMENT = "CRITERION_NOT_ASSESSABLE_JUDGEMENT"
    CRITERION_NOT_ASSESSABLE_PRICE = "CRITERION_NOT_ASSESSABLE_PRICE"
    CRITERION_OPEN_COMMITMENT = "CRITERION_OPEN_COMMITMENT"
    CRITERION_TIMING_NOT_CONCLUSIVE = "CRITERION_TIMING_NOT_CONCLUSIVE"
    # v3 (C-E1): punts en risc perque alguna necessitat del criteri es `unknown`.
    CRITERION_NEEDS_UNKNOWN = "CRITERION_NEEDS_UNKNOWN"
    # v3 (I1): punts en risc perque una necessitat te dada actual de l'empresa (un fet
    # quantitatiu afirmat sense nombre) que no es pot comparar amb el que demana el criteri.
    CRITERION_NEEDS_NOT_COMPARABLE = "CRITERION_NEEDS_NOT_COMPARABLE"
    TERRITORY_MATCHED = "TERRITORY_MATCHED"
    TERRITORY_UNMATCHED = "TERRITORY_UNMATCHED"
    TERRITORY_UNKNOWN = "TERRITORY_UNKNOWN"
    TERRITORY_LIST_UNAVAILABLE = "TERRITORY_LIST_UNAVAILABLE"
    TERRITORY_NOT_APPLICABLE = "TERRITORY_NOT_APPLICABLE"
    DELIVERY_OWN_CURRENT = "DELIVERY_OWN_CURRENT"
    DELIVERY_PARTNER_CURRENT = "DELIVERY_PARTNER_CURRENT"
    DELIVERY_NO_PARTNER_ROLE = "DELIVERY_NO_PARTNER_ROLE"
    DELIVERY_MODE_UNKNOWN = "DELIVERY_MODE_UNKNOWN"
    DELIVERY_NO_SERVICE = "DELIVERY_NO_SERVICE"
    MANDATORY_DOES_NOT_SATISFY = "MANDATORY_DOES_NOT_SATISFY"
    MANDATORY_DOES_NOT_SATISFY_LATER = "MANDATORY_DOES_NOT_SATISFY_LATER"
    DECLARED_NOT_VERIFIED = "DECLARED_NOT_VERIFIED"
    DATA_MISSING = "DATA_MISSING"
    # v3 (C-E2): falta una dada per decidir, pero no es pot demostrar que sigui de
    # l'empresa (llindar o norma del plec illegibles, codi fora del vocabulari...).
    DATA_MISSING_UNATTRIBUTED = "DATA_MISSING_UNATTRIBUTED"
    PROOF_MISSING = "PROOF_MISSING"
    NO_AVAILABLE_DELIVERY = "NO_AVAILABLE_DELIVERY"
    DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES = "DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES"
    # Qualificacio i mitjans (0052).
    QUALIFICATION_THRESHOLD_NOT_COMPARABLE = "QUALIFICATION_THRESHOLD_NOT_COMPARABLE"
    QUALIFICATION_REFERENCE_DATE_MISSING = "QUALIFICATION_REFERENCE_DATE_MISSING"
    # v3 (C-E2): el finding de `QUALIFICATION_REFERENCE_DATE_MISSING`. Falta el termini
    # de presentacio, i falta al plec (no a l'empresa).
    TENDER_REFERENCE_DATE_MISSING = "TENDER_REFERENCE_DATE_MISSING"
    TURNOVER_MET = "TURNOVER_MET"
    TURNOVER_BELOW = "TURNOVER_BELOW"
    TURNOVER_UNKNOWN = "TURNOVER_UNKNOWN"
    EXECUTION_MET = "EXECUTION_MET"
    EXECUTION_BELOW = "EXECUTION_BELOW"
    EXECUTION_UNKNOWN = "EXECUTION_UNKNOWN"
    INSURANCE_MET = "INSURANCE_MET"
    INSURANCE_BELOW = "INSURANCE_BELOW"
    INSURANCE_UNKNOWN = "INSURANCE_UNKNOWN"
    INSURANCE_EXPIRED = "INSURANCE_EXPIRED"
    REGISTRATION_MET = "REGISTRATION_MET"
    REGISTRATION_SCOPE_UNKNOWN = "REGISTRATION_SCOPE_UNKNOWN"
    REGISTRATION_SCOPE_NOT_COVERED = "REGISTRATION_SCOPE_NOT_COVERED"
    REGISTRATION_UNKNOWN = "REGISTRATION_UNKNOWN"
    REGISTRATION_ONLY_OPERATOR_FACT = "REGISTRATION_ONLY_OPERATOR_FACT"
    RESOURCES_MET = "RESOURCES_MET"
    PROFILES_MISSING = "PROFILES_MISSING"
    EQUIPMENT_MISSING = "EQUIPMENT_MISSING"
    EQUIPMENT_CALIBRATION_UNKNOWN = "EQUIPMENT_CALIBRATION_UNKNOWN"
    EQUIPMENT_CALIBRATION_EXPIRED = "EQUIPMENT_CALIBRATION_EXPIRED"
    FACT_NUMBER_MEETS = "FACT_NUMBER_MEETS"
    FACT_NUMBER_BELOW = "FACT_NUMBER_BELOW"
    #: v4 (3B-2 §4.4): un forat obert del paquet documental (`missing_information`,
    #: `missing_from=tender`). Nomes a la taula v4: la v3 queda congelada.
    PACKAGE_GAP = "PACKAGE_GAP"
    #: v4 (3B-2 §3.2): els rols de document de l'empresa difereixen dels globals.
    ROLE_OVERRIDE_DIVERGES = "ROLE_OVERRIDE_DIVERGES"


#: Copy v2 de les explicacions: COPIA LITERAL CONGELADA dels textos de 7513e3a.
#: No es toca mai (els runs v2 en depenen); `test_the_v2_copy_is_frozen...` en fixa el hash.
#: Un codi nou va nomes a les taules de la v3 o posteriors.
_EXPLANATIONS_V2: dict[str, str] = {
    ExplanationCode.FACT_TRUE_VERIFIED: "El fet consta com a actual i verificat.",
    ExplanationCode.FACT_TRUE_DECLARED: (
        "El fet consta com a actual, declarat i pendent de verificar."
    ),
    ExplanationCode.FACT_CONTRADICTS: (
        "La dada actual de l'empresa contradiu el que el plec exigeix."
    ),
    ExplanationCode.FACT_UNKNOWN: "L'empresa no ha declarat aquest fet: no se sap.",
    ExplanationCode.FACT_HISTORIC_ONLY: "Nomes hi ha pistes historiques: no acrediten res.",
    ExplanationCode.FACT_TRUE_TERRITORY_UNMATCHED: (
        "El fet consta, pero la seva cobertura declarada no inclou el territori exigit."
    ),
    ExplanationCode.CERT_CURRENT_VERIFIED: "Certificacio actual i verificada.",
    ExplanationCode.CERT_CURRENT_DECLARED: "Certificacio actual, declarada i pendent de verificar.",
    ExplanationCode.CERT_LEVEL_BELOW: "La certificacio actual es d'un nivell inferior a l'exigit.",
    ExplanationCode.CERT_UNKNOWN: "Cap certificacio actual d'aquesta norma: no se sap.",
    ExplanationCode.CERT_HISTORIC_ONLY: "Nomes hi ha pistes historiques sobre la norma.",
    ExplanationCode.CERT_STANDARD_NOT_STRUCTURED: "El requisit no diu quina norma: no es compara.",
    ExplanationCode.NEEDS_ALL_SATISFIED: (
        "Totes les necessitats minimes es cobreixen amb dades actuals."
    ),
    ExplanationCode.NEEDS_CONTRADICTED: (
        "Una dada actual diu que una necessitat minima no es cobreix."
    ),
    ExplanationCode.NEEDS_UNKNOWN: "Alguna necessitat minima no te dada actual: no se sap.",
    ExplanationCode.NEEDS_NONE_MINIMUM: "Cap necessitat minima estructurada: no se sap.",
    ExplanationCode.PROFILE_DATA_NOT_IN_SNAPSHOT: (
        "Depen de dades del perfil (solvencia, assegurança, personal...) que aquest pas no llegeix."
    ),
    ExplanationCode.CONTRACT_CONDITION_NOT_COMPARABLE: (
        "Condicio del contracte o de l'oferta: no es compara amb cap dada d'empresa."
    ),
    ExplanationCode.NO_STRUCTURE_TO_COMPARE: "El requisit no te cap estructura comparable.",
    ExplanationCode.PROOF_MISSING_FOR_CLAIM: (
        "La dada consta, pero la prova exigida no es pot demostrar."
    ),
    ExplanationCode.CAPABILITY_SUPPORTED: "Una solucio actual cobreix la necessitat.",
    ExplanationCode.CAPABILITY_SUPPORTED_WITH_LIMITS: "Una solucio actual la cobreix amb limits.",
    ExplanationCode.CAPABILITY_NOT_SUPPORTED: "La solucio actual declara que no ho fa.",
    ExplanationCode.CAPABILITY_BELOW_REQUIRED: (
        "La solucio actual ho fa, pero per sota del valor exigit."
    ),
    ExplanationCode.CAPABILITY_UNKNOWN: "Cap capacitat actual declarada per a aquest codi.",
    ExplanationCode.CAPABILITY_HISTORIC_ONLY: "Nomes pistes historiques sobre la capacitat.",
    ExplanationCode.CAPABILITY_UNMAPPED: "Necessitat sense codi del vocabulari: no s'interpreta.",
    ExplanationCode.NEED_VALUE_NOT_COMPARABLE: (
        "El valor exigit no es pot comparar amb el vocabulari."
    ),
    ExplanationCode.CRITERION_SECURED: "Punts assegurats amb dades actuals verificades.",
    ExplanationCode.CRITERION_AT_RISK: (
        "Punts d'un compromis encara obert: dades declarades sense verificar o sense dada actual."
    ),
    ExplanationCode.CRITERION_LOST: "Una dada actual demostra que el criteri no es pot assolir.",
    ExplanationCode.CRITERION_MIXED: "Els punts es reparteixen entre mes d'un estat.",
    ExplanationCode.CRITERION_NOT_ASSESSABLE_UNKNOWN: "Sense dada actual suficient: no s'avalua.",
    ExplanationCode.CRITERION_NOT_ASSESSABLE_JUDGEMENT: (
        "Judici de valor o demostracio: la qualitat no s'avalua en aquest pas."
    ),
    ExplanationCode.CRITERION_NOT_ASSESSABLE_PRICE: (
        "Criteri de preu: l'economia no es cosa d'aquest pas."
    ),
    ExplanationCode.CRITERION_OPEN_COMMITMENT: (
        "Compromis automatic sense cap dada actual que l'asseguri ni el contradigui: en risc."
    ),
    ExplanationCode.CRITERION_TIMING_NOT_CONCLUSIVE: (
        "La temporalitat del criteri no permet concloure que els punts es perden."
    ),
    ExplanationCode.TERRITORY_MATCHED: "El territori exigit es a la cobertura declarada.",
    ExplanationCode.TERRITORY_UNMATCHED: (
        "La cobertura declarada es completa i no inclou el territori."
    ),
    ExplanationCode.TERRITORY_UNKNOWN: "No es pot demostrar que el territori sigui cobert.",
    ExplanationCode.TERRITORY_LIST_UNAVAILABLE: (
        "El plec no dona la llista de territoris (document absent)."
    ),
    ExplanationCode.TERRITORY_NOT_APPLICABLE: "Sense dimensio territorial.",
    ExplanationCode.DELIVERY_OWN_CURRENT: "Servei actual amb mitjans propis.",
    ExplanationCode.DELIVERY_PARTNER_CURRENT: (
        "Servei actual a traves d'un tercer amb paper compatible."
    ),
    ExplanationCode.DELIVERY_NO_PARTNER_ROLE: (
        "Servei actual per tercers, sense cap tercer compatible declarat."
    ),
    ExplanationCode.DELIVERY_MODE_UNKNOWN: "Servei actual sense dir com es presta.",
    ExplanationCode.DELIVERY_NO_SERVICE: "Cap servei actual d'aquesta linia.",
    ExplanationCode.MANDATORY_DOES_NOT_SATISFY: (
        "Requisit obligatori exigit en presentar que una dada actual contradiu."
    ),
    ExplanationCode.MANDATORY_DOES_NOT_SATISFY_LATER: (
        "Requisit obligatori que una dada actual contradiu, pero exigit despres de presentar: "
        "cal revisar-ho, no es cap bloqueig a la presentacio."
    ),
    ExplanationCode.DECLARED_NOT_VERIFIED: "Es compleix amb dades declarades: cal verificar-les.",
    ExplanationCode.DATA_MISSING: "Falta una dada de l'empresa per decidir.",
    ExplanationCode.PROOF_MISSING: "Falta la prova exigida.",
    ExplanationCode.NO_AVAILABLE_DELIVERY: (
        "Cap via de prestacio disponible amb el coneixement actual."
    ),
    ExplanationCode.DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES: (
        "La demostracio depen de capacitats que l'empresa no ha declarat."
    ),
    ExplanationCode.QUALIFICATION_THRESHOLD_NOT_COMPARABLE: (
        "El llindar del plec no es pot llegir com a dada estructurada: no es compara."
    ),
    ExplanationCode.QUALIFICATION_REFERENCE_DATE_MISSING: (
        "El plec no dona el termini de presentacio: no se sap quins son els darrers "
        "exercicis ni els darrers anys."
    ),
    ExplanationCode.TURNOVER_MET: "Algun dels darrers exercicis arriba al volum exigit.",
    ExplanationCode.TURNOVER_BELOW: (
        "Els darrers exercicis consten tots i cap no arriba al volum exigit."
    ),
    ExplanationCode.TURNOVER_UNKNOWN: (
        "No consten els darrers exercicis consecutius (o algun import): no se sap."
    ),
    ExplanationCode.EXECUTION_MET: "L'import executat l'any de major execucio arriba a l'exigit.",
    ExplanationCode.EXECUTION_BELOW: (
        "Amb tots els imports anuals coneguts, cap any de la finestra hi arriba."
    ),
    ExplanationCode.EXECUTION_UNKNOWN: (
        "Imports sense desglossar per any o cap referencia a la finestra: no se sap "
        "(les ofertes no compten)."
    ),
    ExplanationCode.INSURANCE_MET: "Una polissa vigent cobreix tots els limits exigits.",
    ExplanationCode.INSURANCE_BELOW: "Un limit declarat de la polissa vigent no hi arriba.",
    ExplanationCode.INSURANCE_UNKNOWN: (
        "Cap polissa vigent del tipus, o algun limit exigit no consta: no se sap."
    ),
    ExplanationCode.INSURANCE_EXPIRED: "La polissa d'aquest tipus ha caducat.",
    ExplanationCode.REGISTRATION_MET: (
        "Inscripcio vigent amb l'abast estructurat que cobreix el que es demana."
    ),
    ExplanationCode.REGISTRATION_SCOPE_UNKNOWN: (
        "Inscripcio vigent amb l'abast sense estructurar: no se sap si cobreix el plec."
    ),
    ExplanationCode.REGISTRATION_SCOPE_NOT_COVERED: (
        "L'abast inscrit no cobreix tots els serveis del plec: cal revisar-lo."
    ),
    ExplanationCode.REGISTRATION_UNKNOWN: "Cap inscripcio vigent d'aquest tipus: no se sap.",
    ExplanationCode.REGISTRATION_ONLY_OPERATOR_FACT: (
        "Consta el fet d'operador, pero cap inscripcio amb abast: no se sap quins serveis."
    ),
    ExplanationCode.RESOURCES_MET: (
        "Hi ha persones disponibles amb els perfils i equips amb el calibratge exigits."
    ),
    ExplanationCode.PROFILES_MISSING: (
        "Algun perfil exigit no te cap persona disponible declarada: no se sap."
    ),
    ExplanationCode.EQUIPMENT_MISSING: "Algun equip exigit no consta: no se sap.",
    ExplanationCode.EQUIPMENT_CALIBRATION_UNKNOWN: (
        "L'equip consta, pero sense data de calibratge: no se sap si es vigent."
    ),
    ExplanationCode.EQUIPMENT_CALIBRATION_EXPIRED: (
        "L'equip exigit consta, pero amb el calibratge caducat."
    ),
    ExplanationCode.FACT_NUMBER_MEETS: "El fet quantitatiu actual arriba al valor exigit.",
    ExplanationCode.FACT_NUMBER_BELOW: (
        "El fet quantitatiu actual no arriba al valor exigit (es pot ampliar)."
    ),
}


#: Copy v3 de les explicacions: catala correcte, sense codis interns. Un codi no canvia
#: de significat respecte de la v2, nomes de forma. Cap LLM: plantilles. Ha de cobrir
#: tots els codis; `test_analysis_copy_v3` en fixa el text exacte.
_EXPLANATIONS_V3: dict[str, str] = {
    ExplanationCode.FACT_TRUE_VERIFIED: "El fet consta com a actual i verificat.",
    ExplanationCode.FACT_TRUE_DECLARED: (
        "El fet consta com a actual, declarat i pendent de verificar."
    ),
    ExplanationCode.FACT_CONTRADICTS: (
        "La dada actual de l'empresa contradiu el que el plec exigeix."
    ),
    ExplanationCode.FACT_UNKNOWN: (
        "No consta aquest fet amb una dada actual que es pugui comparar: no se sap."
    ),
    ExplanationCode.FACT_HISTORIC_ONLY: "Només hi ha pistes històriques: no acrediten res.",
    ExplanationCode.FACT_TRUE_TERRITORY_UNMATCHED: (
        "El fet consta, però la seva cobertura declarada no inclou el territori exigit."
    ),
    ExplanationCode.CERT_CURRENT_VERIFIED: "Certificació actual i verificada.",
    ExplanationCode.CERT_CURRENT_DECLARED: "Certificació actual, declarada i pendent de verificar.",
    ExplanationCode.CERT_LEVEL_BELOW: "La certificació actual és d'un nivell inferior a l'exigit.",
    ExplanationCode.CERT_UNKNOWN: (
        "No consta cap certificació actual d'aquesta norma que es pugui comparar "
        "(nivell o abast): no se sap."
    ),
    ExplanationCode.CERT_HISTORIC_ONLY: "Només hi ha pistes històriques sobre la norma.",
    ExplanationCode.CERT_STANDARD_NOT_STRUCTURED: "El requisit no diu quina norma: no es compara.",
    ExplanationCode.NEEDS_ALL_SATISFIED: (
        "Totes les necessitats mínimes es cobreixen amb dades actuals."
    ),
    ExplanationCode.NEEDS_CONTRADICTED: (
        "Una dada actual diu que una necessitat mínima no es cobreix."
    ),
    ExplanationCode.NEEDS_UNKNOWN: "Alguna necessitat mínima no té dada actual: no se sap.",
    ExplanationCode.NEEDS_NONE_MINIMUM: "Cap necessitat mínima estructurada: no se sap.",
    ExplanationCode.PROFILE_DATA_NOT_IN_SNAPSHOT: (
        "Depèn de dades del perfil (solvència, assegurança, personal...) que aquest pas no llegeix."
    ),
    ExplanationCode.CONTRACT_CONDITION_NOT_COMPARABLE: (
        "Condició del contracte o de l'oferta: no es compara amb cap dada d'empresa."
    ),
    ExplanationCode.NO_STRUCTURE_TO_COMPARE: "El requisit no té cap estructura comparable.",
    ExplanationCode.PROOF_MISSING_FOR_CLAIM: (
        "La dada consta, però la prova exigida no es pot demostrar."
    ),
    ExplanationCode.CAPABILITY_SUPPORTED: "Una solució actual cobreix la necessitat.",
    ExplanationCode.CAPABILITY_SUPPORTED_WITH_LIMITS: "Una solució actual la cobreix amb límits.",
    ExplanationCode.CAPABILITY_NOT_SUPPORTED: "La solució actual declara que no ho fa.",
    ExplanationCode.CAPABILITY_BELOW_REQUIRED: (
        "La solució actual ho fa, però per sota del valor exigit."
    ),
    ExplanationCode.CAPABILITY_UNKNOWN: (
        "Cap solució actual declara aquesta capacitat amb una dada que es pugui comparar: "
        "no se sap."
    ),
    ExplanationCode.CAPABILITY_HISTORIC_ONLY: "Només hi ha pistes històriques sobre la capacitat.",
    ExplanationCode.CAPABILITY_UNMAPPED: (
        "La necessitat no està associada a cap capacitat del vocabulari: no s'interpreta."
    ),
    ExplanationCode.NEED_VALUE_NOT_COMPARABLE: (
        "El valor exigit no es pot comparar amb el vocabulari."
    ),
    ExplanationCode.CRITERION_SECURED: "Punts assegurats amb dades actuals verificades.",
    ExplanationCode.CRITERION_AT_RISK: (
        "Punts d'un compromís encara obert: dades declarades sense verificar o sense dada actual."
    ),
    ExplanationCode.CRITERION_LOST: "Una dada actual demostra que el criteri no es pot assolir.",
    ExplanationCode.CRITERION_MIXED: "Els punts es reparteixen entre més d'un estat.",
    ExplanationCode.CRITERION_NOT_ASSESSABLE_UNKNOWN: "Sense dada actual suficient: no s'avalua.",
    ExplanationCode.CRITERION_NOT_ASSESSABLE_JUDGEMENT: (
        "Judici de valor o demostració: la qualitat no s'avalua en aquest pas."
    ),
    ExplanationCode.CRITERION_NOT_ASSESSABLE_PRICE: (
        "Criteri de preu: l'economia no és cosa d'aquest pas."
    ),
    ExplanationCode.CRITERION_OPEN_COMMITMENT: (
        "Compromís automàtic sense cap dada actual que l'asseguri ni el contradigui: en risc."
    ),
    ExplanationCode.CRITERION_TIMING_NOT_CONCLUSIVE: (
        "La temporalitat del criteri no permet concloure que els punts es perden."
    ),
    ExplanationCode.CRITERION_NEEDS_UNKNOWN: (
        "Hi ha punts en risc perquè no consta cap dada actual de l'empresa sobre el que demana "
        "el criteri."
    ),
    ExplanationCode.CRITERION_NEEDS_NOT_COMPARABLE: (
        "Hi ha punts en risc perquè la dada actual de l'empresa no es pot comparar amb el que "
        "demana el criteri."
    ),
    ExplanationCode.TERRITORY_MATCHED: "El territori exigit és a la cobertura declarada.",
    ExplanationCode.TERRITORY_UNMATCHED: (
        "La cobertura declarada és completa i no inclou el territori."
    ),
    ExplanationCode.TERRITORY_UNKNOWN: "No es pot demostrar que el territori sigui cobert.",
    ExplanationCode.TERRITORY_LIST_UNAVAILABLE: (
        "El plec no dona la llista de territoris (document absent)."
    ),
    ExplanationCode.TERRITORY_NOT_APPLICABLE: "Sense dimensió territorial.",
    ExplanationCode.DELIVERY_OWN_CURRENT: "Servei actual amb mitjans propis.",
    ExplanationCode.DELIVERY_PARTNER_CURRENT: (
        "Servei actual a través d'un tercer amb paper compatible."
    ),
    ExplanationCode.DELIVERY_NO_PARTNER_ROLE: (
        "Servei actual per tercers, sense cap tercer compatible declarat."
    ),
    ExplanationCode.DELIVERY_MODE_UNKNOWN: "Servei actual sense dir com es presta.",
    ExplanationCode.DELIVERY_NO_SERVICE: "Cap servei actual d'aquesta línia.",
    ExplanationCode.MANDATORY_DOES_NOT_SATISFY: (
        "Requisit obligatori exigit en presentar que una dada actual contradiu."
    ),
    ExplanationCode.MANDATORY_DOES_NOT_SATISFY_LATER: (
        "Requisit obligatori que una dada actual contradiu, però exigit després de presentar: "
        "cal revisar-ho, no és cap bloqueig a la presentació."
    ),
    ExplanationCode.DECLARED_NOT_VERIFIED: "Es compleix amb dades declarades: cal verificar-les.",
    ExplanationCode.DATA_MISSING: "Falta una dada de l'empresa per decidir.",
    ExplanationCode.DATA_MISSING_UNATTRIBUTED: "Falta una dada per decidir.",
    ExplanationCode.PROOF_MISSING: "Falta la prova exigida.",
    ExplanationCode.NO_AVAILABLE_DELIVERY: (
        "Cap via de prestació disponible amb el coneixement actual."
    ),
    ExplanationCode.DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES: (
        "La demostració depèn de capacitats que l'empresa no ha declarat."
    ),
    ExplanationCode.QUALIFICATION_THRESHOLD_NOT_COMPARABLE: (
        "El llindar del plec no es pot llegir com a dada estructurada: no es compara."
    ),
    ExplanationCode.QUALIFICATION_REFERENCE_DATE_MISSING: (
        "El plec no dona el termini de presentació: no se sap quins són els darrers "
        "exercicis ni els darrers anys."
    ),
    ExplanationCode.TENDER_REFERENCE_DATE_MISSING: (
        "El plec no fixa el termini de presentació, que és la data de referència per "
        "calcular aquest requisit. Cal demanar-lo a l'òrgan."
    ),
    ExplanationCode.TURNOVER_MET: "Algun dels darrers exercicis arriba al volum exigit.",
    ExplanationCode.TURNOVER_BELOW: (
        "Els darrers exercicis consten tots i cap no arriba al volum exigit."
    ),
    ExplanationCode.TURNOVER_UNKNOWN: (
        "No consten els darrers exercicis consecutius (o algun import): no se sap."
    ),
    ExplanationCode.EXECUTION_MET: "L'import executat l'any de major execució arriba a l'exigit.",
    ExplanationCode.EXECUTION_BELOW: (
        "Amb tots els imports anuals coneguts, cap any de la finestra hi arriba."
    ),
    ExplanationCode.EXECUTION_UNKNOWN: (
        "Imports sense desglossar per any o cap referència a la finestra: no se sap "
        "(les ofertes no compten)."
    ),
    ExplanationCode.INSURANCE_MET: "Una pòlissa vigent cobreix tots els límits exigits.",
    ExplanationCode.INSURANCE_BELOW: "Un límit declarat de la pòlissa vigent no hi arriba.",
    ExplanationCode.INSURANCE_UNKNOWN: (
        "Cap pòlissa vigent del tipus, o algun límit exigit no consta: no se sap."
    ),
    ExplanationCode.INSURANCE_EXPIRED: "La pòlissa d'aquest tipus ha caducat.",
    ExplanationCode.REGISTRATION_MET: (
        "Inscripció vigent amb l'abast estructurat que cobreix el que es demana."
    ),
    ExplanationCode.REGISTRATION_SCOPE_UNKNOWN: (
        "Inscripció vigent amb l'abast sense estructurar: no se sap si cobreix el plec."
    ),
    ExplanationCode.REGISTRATION_SCOPE_NOT_COVERED: (
        "L'abast inscrit no cobreix tots els serveis del plec: cal revisar-lo."
    ),
    ExplanationCode.REGISTRATION_UNKNOWN: "Cap inscripció vigent d'aquest tipus: no se sap.",
    ExplanationCode.REGISTRATION_ONLY_OPERATOR_FACT: (
        "Consta el fet d'operador, però cap inscripció amb abast: no se sap quins serveis."
    ),
    ExplanationCode.RESOURCES_MET: (
        "Hi ha persones disponibles amb els perfils exigits i els equips exigits, amb el "
        "calibratge vigent si cal."
    ),
    ExplanationCode.PROFILES_MISSING: (
        "Algun perfil exigit no té cap persona disponible declarada: no se sap."
    ),
    ExplanationCode.EQUIPMENT_MISSING: "Algun equip exigit no consta: no se sap.",
    ExplanationCode.EQUIPMENT_CALIBRATION_UNKNOWN: (
        "L'equip consta, però sense data de calibratge: no se sap si és vigent."
    ),
    ExplanationCode.EQUIPMENT_CALIBRATION_EXPIRED: (
        "L'equip exigit consta, però amb el calibratge caducat."
    ),
    ExplanationCode.FACT_NUMBER_MEETS: "El fet quantitatiu actual arriba al valor exigit.",
    ExplanationCode.FACT_NUMBER_BELOW: (
        "El fet quantitatiu actual no arriba al valor exigit (es pot ampliar)."
    ),
}


class QuestionCode:
    """Que cal preguntar, a qui. El text surt de `QUESTIONS_BY_VERSION`."""

    COMPANY_CONFIRM_FACT = "company.confirm_fact"
    COMPANY_DECLARE_CAPABILITY = "company.declare_capability"
    COMPANY_PROVIDE_PROOF = "company.provide_proof"
    COMPANY_PROVIDE_PROFILE_DATA = "company.provide_profile_data"
    COMPANY_DECLARE_CERTIFICATION = "company.declare_certification"
    COMPANY_DECLARE_DELIVERY_MODE = "company.declare_delivery_mode"
    COMPANY_DECLARE_PARTNER = "company.declare_partner"
    COMPANY_CONFIRM_COVERAGE = "company.confirm_coverage"
    BUYER_PROVIDE_TERRITORY_LIST = "buyer.provide_territory_list"
    BUYER_CLARIFY_CRITERION_TIMING = "buyer.clarify_criterion_timing"
    BUYER_CLARIFY_REQUIREMENT_TIMING = "buyer.clarify_requirement_timing"
    COMPANY_REVIEW_REGISTRATION_SCOPE = "company.review_registration_scope"
    COMPANY_RENEW_CALIBRATION = "company.renew_calibration"
    # v3 (C-E2): el plec no dona el termini de presentacio (data de referencia).
    BUYER_PROVIDE_SUBMISSION_DEADLINE = "buyer.provide_submission_deadline"


#: Plantilles de pregunta v2 (`{...}` son `params_json`, interpolacio crua): COPIA
#: LITERAL CONGELADA de 7513e3a. No es toca mai; el test de hash ho vigila.
_QUESTIONS_V2: dict[str, str] = {
    QuestionCode.COMPANY_CONFIRM_FACT: (
        "Confirmeu si l'empresa compleix «{fact}» (requisit «{title}»)."
    ),
    QuestionCode.COMPANY_DECLARE_CAPABILITY: (
        "Declareu si la solucio actual de «{service_line}» cobreix «{capability}»."
    ),
    QuestionCode.COMPANY_PROVIDE_PROOF: "Aporteu la prova «{proof_kind}» del requisit «{title}».",
    QuestionCode.COMPANY_PROVIDE_PROFILE_DATA: (
        "Informeu les dades de «{category}» per al requisit «{title}»."
    ),
    QuestionCode.COMPANY_DECLARE_CERTIFICATION: "Declareu si l'empresa te la norma «{standard}».",
    QuestionCode.COMPANY_DECLARE_DELIVERY_MODE: "Digueu com es presta el servei «{service_line}».",
    QuestionCode.COMPANY_DECLARE_PARTNER: "Declareu un tercer per prestar «{service_line}».",
    QuestionCode.COMPANY_CONFIRM_COVERAGE: (
        "Confirmeu la cobertura de «{service_line}» als territoris del plec."
    ),
    QuestionCode.BUYER_PROVIDE_TERRITORY_LIST: (
        "Demaneu a l'organ la llista de territoris del requisit «{title}»."
    ),
    QuestionCode.BUYER_CLARIFY_CRITERION_TIMING: (
        "Demaneu a l'organ quan s'ha de complir el criteri «{name}»."
    ),
    QuestionCode.BUYER_CLARIFY_REQUIREMENT_TIMING: (
        "Demaneu a l'organ quan s'ha de complir el requisit «{title}»."
    ),
    QuestionCode.COMPANY_REVIEW_REGISTRATION_SCOPE: (
        "Reviseu l'abast inscrit a «{registration_type}» per al requisit «{title}»."
    ),
    QuestionCode.COMPANY_RENEW_CALIBRATION: (
        "Confirmeu o renoveu el calibratge dels equips del requisit «{title}»."
    ),
}


#: Plantilles de pregunta v3. Cada `{...}` es un parametre de `params_json` que
#: `question_text` resol abans d'interpolar (`_PARAM_LABELS`, `_FREE_TEXT_PARAMS`): un
#: codi conegut surt com el seu retol entre «», un text lliure tal com es entre «», i un
#: codi desconegut, `other` o absent com l'expressio neutra del parametre, sense «».
#: Per aixo cap plantilla porta «» ni una preposicio que s'hagi d'apostrofar davant
#: del valor. Ha de cobrir tots els codis.
_QUESTIONS_V3: dict[str, str] = {
    QuestionCode.COMPANY_CONFIRM_FACT: (
        "Confirmeu si l'empresa compleix {fact} (requisit {title})."
    ),
    QuestionCode.COMPANY_DECLARE_CAPABILITY: (
        "Declareu si la solució actual per a {service_line} cobreix {capability}."
    ),
    QuestionCode.COMPANY_PROVIDE_PROOF: "Aporteu la prova {proof_kind} per al requisit {title}.",
    QuestionCode.COMPANY_PROVIDE_PROFILE_DATA: (
        "Informeu les dades de la categoria {category} per al requisit {title}."
    ),
    QuestionCode.COMPANY_DECLARE_CERTIFICATION: (
        "Declareu si l'empresa té la certificació {standard}."
    ),
    QuestionCode.COMPANY_DECLARE_DELIVERY_MODE: "Digueu com presta l'empresa {service_line}.",
    QuestionCode.COMPANY_DECLARE_PARTNER: "Declareu un tercer per prestar {service_line}.",
    QuestionCode.COMPANY_CONFIRM_COVERAGE: (
        "Confirmeu la cobertura per a {service_line} als territoris on s'ha de prestar."
    ),
    QuestionCode.BUYER_PROVIDE_TERRITORY_LIST: (
        "Demaneu a l'òrgan la llista de territoris del requisit {title}."
    ),
    QuestionCode.BUYER_CLARIFY_CRITERION_TIMING: (
        "Demaneu a l'òrgan quan s'ha de complir el criteri {name}."
    ),
    QuestionCode.BUYER_CLARIFY_REQUIREMENT_TIMING: (
        "Demaneu a l'òrgan quan s'ha de complir el requisit {title}."
    ),
    QuestionCode.COMPANY_REVIEW_REGISTRATION_SCOPE: (
        "Reviseu l'abast de la inscripció a {registration_type} per al requisit {title}."
    ),
    QuestionCode.COMPANY_RENEW_CALIBRATION: (
        "Confirmeu o renoveu el calibratge dels equips del requisit {title}."
    ),
    QuestionCode.BUYER_PROVIDE_SUBMISSION_DEADLINE: (
        "Demaneu a l'òrgan el termini de presentació d'ofertes "
        "(cal per calcular el requisit {title})."
    ),
}

#: Parametres de codi de les preguntes v3: d'on surt el retol i quina expressio neutra
#: el substitueix si el codi no hi es (mai el codi). Cada expressio encaixa a totes les
#: plantilles on surt el parametre.
_PARAM_LABELS: dict[str, tuple[Mapping[str, str], str]] = {
    "service_line": (
        MappingProxyType({s.code: s.label for s in vocabulary.SERVICE_LINES}),
        "una línia de servei del plec",
    ),
    "capability": (
        MappingProxyType({c.code: c.label for c in vocabulary.CAPABILITIES}),
        "una capacitat que demana el plec",
    ),
    "standard": (
        MappingProxyType({s.code: s.label for s in vocabulary.CERTIFICATION_STANDARDS}),
        "que demana el plec",
    ),
    "proof_kind": (MappingProxyType(vocabulary.PROOF_KIND_LABELS), "que demana el plec"),
    "category": (MappingProxyType(vocabulary.REQUIREMENT_CATEGORY_LABELS), "que correspon"),
    "registration_type": (
        MappingProxyType(vocabulary.REGISTRATION_TYPE_LABELS),
        "un registre que demana el plec",
    ),
    "fact": (
        MappingProxyType(vocabulary.OPERATING_FACT_LABELS),
        "la condició que demana el plec",
    ),
}
#: Codis que no diuen res a qui llegeix: surten com l'expressio neutra.
_UNINFORMATIVE_CODES = frozenset({"other"})
#: Parametres de text lliure (titol del requisit, nom del criteri): passen tal com son.
_FREE_TEXT_PARAMS: dict[str, str] = {"title": "sense títol", "name": "sense nom"}


def _template_fields(template: str) -> frozenset[str]:
    return frozenset(name for _, name, _, _ in Formatter().parse(template) if name)


assert all(
    _template_fields(t) <= set(_PARAM_LABELS) | set(_FREE_TEXT_PARAMS)
    for t in _QUESTIONS_V3.values()
), "cada parametre d'una plantilla v3 sap com es resol"
assert not any("«" in t or "»" in t for t in _QUESTIONS_V3.values()), "les «» les posa el valor"


def _resolve_param(name: str, value: object) -> str:
    """El valor huma d'un parametre v3: retol o text entre «», o l'expressio neutra."""
    if name in _FREE_TEXT_PARAMS:
        if isinstance(value, str) and value.strip():
            return f"«{value}»"
        return _FREE_TEXT_PARAMS[name]
    labels, neutral = _PARAM_LABELS[name]
    if isinstance(value, str) and value not in _UNINFORMATIVE_CODES and value in labels:
        return f"«{labels[value]}»"
    return neutral


def _codes(cls: type) -> frozenset[str]:
    return frozenset(v for k, v in vars(cls).items() if k.isupper() and isinstance(v, str))


RULE_CODES: frozenset[str] = _codes(RuleCode)
EXPLANATION_CODES: frozenset[str] = _codes(ExplanationCode)
QUESTION_CODES: frozenset[str] = _codes(QuestionCode)

#: Codis nous de la v4 (3B-2): el copy v3 de la resta no canvia.
_EXPLANATIONS_V4_NEW: dict[str, str] = {
    ExplanationCode.PACKAGE_GAP: (
        "Al paquet documental del plec li falta un document: el que hi diria no es pot avaluar."
    ),
    ExplanationCode.ROLE_OVERRIDE_DIVERGES: (
        "Els tipus de document que ha fixat l'empresa difereixen dels de la plataforma: "
        "aquesta anàlisi llegeix els de la plataforma."
    ),
}

#: El copy es versiona per `rules_version` (spec 3.11): un run es renderitza sempre amb
#: la taula de la seva versio. Un sol `ExplanationCode`/`QuestionCode`: la versio nomes
#: tria la taula de text; un codi no canvia mai de significat entre versions.
EXPLANATIONS_BY_VERSION: dict[str, Mapping[str, str]] = {
    RULES_VERSION_V2: MappingProxyType(_EXPLANATIONS_V2),
    RULES_VERSION_V3: MappingProxyType(_EXPLANATIONS_V3),
    # Copia propia: la taula v3 queda congelada; la v4 hi afegeix els codis nous.
    RULES_VERSION_V4: MappingProxyType({**_EXPLANATIONS_V3, **_EXPLANATIONS_V4_NEW}),
}
QUESTIONS_BY_VERSION: dict[str, Mapping[str, str]] = {
    RULES_VERSION_V2: MappingProxyType(_QUESTIONS_V2),
    RULES_VERSION_V3: MappingProxyType(_QUESTIONS_V3),
    RULES_VERSION_V4: MappingProxyType(dict(_QUESTIONS_V3)),
}

assert set(EXPLANATIONS_BY_VERSION) == set(QUESTIONS_BY_VERSION), "cada versio te les dues taules"
assert EXPLANATION_CODES == frozenset(EXPLANATIONS_BY_VERSION[RULES_VERSION]), (
    "cada explicacio te text a la versio vigent"
)
assert QUESTION_CODES == frozenset(QUESTIONS_BY_VERSION[RULES_VERSION]), (
    "cada pregunta te plantilla a la versio vigent"
)
assert all(frozenset(t) <= EXPLANATION_CODES for t in EXPLANATIONS_BY_VERSION.values()), (
    "les versions antigues nomes tenen codis registrats"
)
assert all(frozenset(t) <= QUESTION_CODES for t in QUESTIONS_BY_VERSION.values()), (
    "les versions antigues nomes tenen codis registrats"
)


def explanation_text(code: str, rules_version: str | None) -> str:
    """El text de l'explicacio amb la taula de `rules_version` (la del run)."""
    table = EXPLANATIONS_BY_VERSION.get(rules_version or "")
    if table is None or code not in table:
        return UNKNOWN_EXPLANATION_TEXT
    return table[code]


def question_text(code: str, params: dict | None, rules_version: str | None) -> str:
    """La pregunta amb la plantilla de `rules_version` (la del run).

    v2: interpolacio crua dels codis; si falta un parametre, la plantilla tal com es.
    v3 i posteriors: cada parametre es resol a retol, text lliure o expressio neutra
    (`_resolve_param`); mai surt un codi. `params` no es modifica: es la clau de
    deduplicacio i el run immutable.
    """
    table = QUESTIONS_BY_VERSION.get(rules_version or "")
    if table is None or code not in table:
        return UNKNOWN_QUESTION_TEXT
    template = table[code]
    if rules_version == RULES_VERSION_V2:
        try:
            return template.format(**(params or {}))
        except KeyError:
            return template
    given = params or {}
    return template.format_map(
        {name: _resolve_param(name, given.get(name)) for name in _template_fields(template)}
    )
