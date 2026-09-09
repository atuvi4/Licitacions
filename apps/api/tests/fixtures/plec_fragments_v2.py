"""Els cinc patrons que la revisio humana del gold va trobar que es perdien.

No son casos inventats per omplir una suite: cada un surt d'una anotacio real de
`gold-v1-reviewed`, on una persona va llegir el plec i va escriure que faltava.
Els fragments estan reduits al minim que conserva el patro i **no porten cap
identificador d'expedient**: el que es prova es la forma del text, no un
document concret.

Cada fixture porta el valor v2 que un extractor correcte hauria de produir. Aixo
no demostra que el model ho faci —aixo sera el benchmark— pero si que fixa que la
canonada ho pot representar, persistir i tornar a llegir sense perdre res.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class V2Fragment:
    key: str
    page_number: int
    text: str
    #: Que en va dir la revisio humana. Explica per que el cas existeix.
    gold_note: str
    expected_requirements: list[dict[str, Any]] = field(default_factory=list)
    expected_award_criteria: list[dict[str, Any]] = field(default_factory=list)
    must_not_extract: list[str] = field(default_factory=list)


BEST_YEAR_OF_LAST_THREE = V2Fragment(
    key="best_year_of_last_three",
    page_number=27,
    gold_note=(
        "La v1 ho representava com `years: 3`, que no distingeix 'cadascun dels "
        "tres ultims' de 'el de major volum dels tres ultims'."
    ),
    text=(
        "8.1.2. Solvencia economica y financiera.\n"
        "El volumen anual de negocios del licitador, referido al de mayor volumen "
        "de los tres ultimos ejercicios disponibles, debera ser igual o superior a "
        "60.750 euros.\n"
        "Se acreditara mediante las cuentas anuales depositadas en el Registro Mercantil.\n"
    ),
    expected_requirements=[
        {
            "category": "economic_solvency",
            "obligation": "mandatory",
            "modality": "mandatory",
            "operator": "gte",
            "value": {
                "kind": "economic_solvency",
                "metric": "annual_turnover",
                "operator": "gte",
                "threshold": {"amount": 60750, "currency": "EUR"},
                "reference_period": {"type": "last_n_years", "years": 3},
                # El camp que la v1 no tenia.
                "aggregation": "max",
                "proof": "financial_statement",
            },
            "quote": "referido al de mayor volumen de los tres ultimos ejercicios",
        }
    ],
    must_not_extract=[
        "`aggregation: each_year`: el text diu explicitament el contrari.",
        "Un requisit a part per a la forma d'acreditar-ho: es `value.proof`.",
    ],
)


ACCUMULATED_EXPERIENCE_PERCENTAGE = V2Fragment(
    key="accumulated_experience_percentage",
    page_number=28,
    gold_note=(
        "Anotacio 300-2025-03877::exp-1: «Falta extreure el llindar de solvencia "
        "tecnica: import anual acumulat de serveis similars en l'any de major "
        "execucio >= 70% de l'anualitat mitjana del contracte»."
    ),
    text=(
        "8.2. Solvencia tecnica o profesional.\n"
        "El importe anual acumulado de los servicios similares ejecutados por el "
        "licitador en el ano de mayor ejecucion de los tres ultimos anos debera ser "
        "igual o superior al 70% de la anualidad media del contrato.\n"
        "Se acreditara mediante certificados de buena ejecucion expedidos por el "
        "organo de contratacion.\n"
    ),
    expected_requirements=[
        {
            "category": "technical_solvency",
            "obligation": "mandatory",
            "modality": "mandatory",
            "operator": "gte",
            "value": {
                "kind": "experience",
                "work_type": "servicios similares",
                "reference_period": {"type": "last_n_years", "years": 3},
                "min_accumulated_amount": {
                    "percentage": 70,
                    "percentage_base": "contract_annuity",
                },
                "aggregation": "max",
                "proof": "certificate_of_good_execution",
            },
            "quote": "igual o superior al 70% de la anualidad media del contrato",
        }
    ],
    must_not_extract=[
        "Un import absolut: el plec dona un percentatge, no una xifra.",
        "`min_individual_amount`: el llindar es sobre l'acumulat, no per contracte.",
    ],
)


INSURANCE_WITH_PER_VICTIM_SUBLIMIT = V2Fragment(
    key="insurance_with_per_victim_sublimit",
    page_number=31,
    gold_note=(
        "La v1 aplanava els dos imports en un de sol i el sublimit per victima "
        "desapareixia."
    ),
    text=(
        "El contratista debera mantener durante la vigencia del contrato una poliza "
        "de responsabilidad civil con una cobertura minima de 1.000.000 de euros, "
        "con un sublimite minimo de 300.000 euros por victima.\n"
        "La poliza debera incluir al organo de contratacion como asegurado adicional "
        "y se acreditara mediante certificado de la entidad aseguradora.\n"
    ),
    expected_requirements=[
        {
            "category": "insurance",
            "obligation": "mandatory",
            "modality": "mandatory",
            "operator": "gte",
            "value": {
                "kind": "insurance",
                "insurance_type": "civil_liability",
                # DUES condicions sobre la MATEIXA polissa, no dos requisits.
                "limits": [
                    {"kind": "total", "amount": 1000000, "currency": "EUR"},
                    {"kind": "per_victim", "amount": 300000, "currency": "EUR"},
                ],
                "must_be_valid_through_contract": True,
                "additional_insured": True,
                "proof": "insurance_certificate",
            },
            "quote": "cobertura minima de 1.000.000 de euros",
        }
    ],
    must_not_extract=[
        "Dos requisits d'assegurança separats: son dos limits del mateix.",
        "Nomes el limit total: perdre el sublimit el faria semblar mes facil.",
    ],
)


CLASSIFICATION_AS_ALTERNATIVE = V2Fragment(
    key="classification_as_alternative",
    page_number=25,
    gold_note=(
        "Anotacio 2026SE00013::class-1: «el text diu 'podra presentar' la "
        "classificacio per acreditar solvencia; pot ser un mitja alternatiu i no "
        "un requisit mandatory»."
    ),
    text=(
        "8.3. Acreditacion de la solvencia.\n"
        "El licitador debera acreditar su solvencia economica y tecnica conforme a "
        "los apartados anteriores. Alternativamente, podra acreditarla mediante la "
        "clasificacion empresarial en el grupo L, subgrupo 1, categoria 2.\n"
    ),
    expected_requirements=[
        {
            "category": "business_classification",
            # Ni `mandatory` a l'obligatorietat ni al matis: es una VIA.
            "obligation": "unknown",
            "modality": "alternative",
            "alternative_group": "solvencia-1",
            "value": {
                "kind": "classification",
                "group": "L",
                "subgroup": "1",
                "category": "2",
            },
            "quote": "podra acreditarla mediante la clasificacion empresarial",
        }
    ],
    must_not_extract=[
        "`obligation: mandatory`: 'podra' no demostra obligatorietat.",
        "Un blocador: no tenir la classificacio no impedeix presentar-se si "
        "s'acredita per l'altra via.",
    ],
)


EQUIVALENT_CERTIFICATION = V2Fragment(
    key="equivalent_certification",
    page_number=26,
    gold_note=(
        "'o equivalent' es perdia i el requisit quedava com si exigis exactament "
        "aquella certificacio."
    ),
    text=(
        "El licitador debera disponer de certificado ISO 9001 de gestion de la "
        "calidad, o equivalente, en vigor en la fecha de presentacion de ofertas.\n"
    ),
    expected_requirements=[
        {
            "category": "certification",
            "obligation": "mandatory",
            "modality": "mandatory",
            "equivalence_allowed": True,
            "value": {
                "kind": "certification",
                "name": "ISO 9001",
                "equivalence_allowed": True,
                "must_be_valid_at_submission": True,
            },
            "quote": "certificado ISO 9001 de gestion de la calidad, o equivalente",
        }
    ],
    must_not_extract=[
        "Quina certificacio concreta hi seria equivalent: no ho decideix l'extractor.",
    ],
)


ECONOMIC_AWARD_FORMULA = V2Fragment(
    key="economic_award_formula",
    page_number=34,
    gold_note=(
        "Anotacions 422-2026::award-1 i P4107500C-2026-65::award-1: «falta "
        "extreure la formula»."
    ),
    text=(
        "10.1. Criterios evaluables mediante formulas.\n"
        "Oferta economica: hasta 75 puntos. Se puntuara aplicando la siguiente "
        "formula: P = 75 x (OM / OF), siendo OM la oferta mas baja admitida y OF la "
        "oferta a valorar.\n"
    ),
    expected_award_criteria=[
        {
            "name": "Oferta economica",
            "criterion_type": "price",
            "points": 75,
            "evaluation_type": "automatic",
            "formula": "P = 75 x (OM / OF)",
            "formula_text": (
                "Se puntuara aplicando la siguiente formula: P = 75 x (OM / OF), "
                "siendo OM la oferta mas baja admitida y OF la oferta a valorar."
            ),
            "formula_variables": [
                {"symbol": "OM", "meaning": "oferta mas baja admitida"},
                {"symbol": "OF", "meaning": "oferta a valorar"},
            ],
            "quote": "P = 75 x (OM / OF)",
        }
    ],
    must_not_extract=[
        "Cap resultat de la formula: aqui no s'executa res.",
        "Cap formula inventada quan el plec no en dona.",
    ],
)


ALL_V2_FRAGMENTS: tuple[V2Fragment, ...] = (
    BEST_YEAR_OF_LAST_THREE,
    ACCUMULATED_EXPERIENCE_PERCENTAGE,
    INSURANCE_WITH_PER_VICTIM_SUBLIMIT,
    CLASSIFICATION_AS_ALTERNATIVE,
    EQUIVALENT_CERTIFICATION,
    ECONOMIC_AWARD_FORMULA,
)
