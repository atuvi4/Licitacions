"""El plec de desenvolupament: el text, i que en surt de cada frase.

Aixo NO es un plec real ni ho vol semblar. Es un corpus controlat que existeix
per una raó concreta: que el mode de simulacio pugui recorrer el flux sencer
**sense poder mentir**.

La versio anterior del fixture agafava qualsevol frase prou llarga del document
que li tocava i la feia servir com a cita del requisit que fos. Passava la
validacio d'evidencia —la frase hi era, literalment— i tot semblava correcte,
pero la cita no tenia res a veure amb el requisit: "Nº expediente nomenclatura
Plataforma del Estado" sostenia una exigencia d'experiencia. Una simulacio pot
inventar-se el contingut; el que no pot es inventar-se la RELACIO entre una
exigencia i el text que la sosté, perque llavors el que s'esta provant ja no es
el producte.

Aqui cada requisit va lligat a **una frase concreta d'aquest corpus**. El
proveidor la busca al text que rep; si no hi es, aquell requisit no surt. Si no
hi es cap, es que no s'esta executant sobre aquest corpus i es nega a respondre.
Cap coincidencia difusa, cap model, cap heuristica: `str.find`, i prou.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import (
    AwardEvaluationType,
    ExtractionConfidence,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
)

#: Identificador del corpus. Viatja al `document_notes` de cada resposta perque
#: qui miri un run sapiga sobre que es va executar.
CORPUS_ID = "plec-simulat-v1"

#: El text del plec, pagina a pagina (index 0 = pagina 1). Esta en castella
#: perque els plecs reals ho estan, i les frases que sostenen els requisits
#: estan escrites tal com les escriuria un organ de contractacio.
PAGES: tuple[str, ...] = (
    # --- Pagina 1: portada i objecte ---------------------------------------
    "PLIEGO DE CLAUSULAS ADMINISTRATIVAS PARTICULARES (DOCUMENTO DE PRUEBA)\n"
    "Expediente: SIM-2026/0001\n"
    "\n"
    "1. OBJETO DEL CONTRATO\n"
    "El presente pliego tiene por objeto la contratacion del suministro de "
    "energia electrica para los edificios municipales.\n"
    "\n"
    "AVISO: este documento es un corpus de desarrollo. No corresponde a ninguna "
    "licitacion real y no debe utilizarse para preparar ninguna oferta.\n",
    # --- Pagina 2: solvencia economica -------------------------------------
    "2. SOLVENCIA ECONOMICA Y FINANCIERA\n"
    "\n"
    "El volumen anual de negocios del licitador referido al mejor ejercicio de "
    "los tres ultimos disponibles debera ser igual o superior a 250.000 euros.\n"
    "\n"
    "La acreditacion se realizara mediante las cuentas anuales aprobadas y "
    "depositadas en el Registro Mercantil.\n",
    # --- Pagina 3: seguros --------------------------------------------------
    "3. SEGUROS EXIGIDOS\n"
    "\n"
    "El licitador debera acreditar un seguro de responsabilidad civil por "
    "riesgos profesionales con un limite total de 600.000 euros.\n"
    "\n"
    "Dentro de la misma poliza se exigira un sublimite por victima de 150.000 "
    "euros.\n"
    "\n"
    "La poliza debera permanecer vigente durante toda la ejecucion del "
    "contrato.\n",
    # --- Pagina 4: certificaciones -----------------------------------------
    "4. CERTIFICACIONES DE GESTION\n"
    "\n"
    "El licitador debera disponer de certificado ISO 14001 en vigor, emitido "
    "por entidad de certificacion acreditada.\n"
    "\n"
    "Se admitiran certificados equivalentes emitidos por organismos de otros "
    "Estados miembros.\n",
    # --- Pagina 5: experiencia i memoria tecnica ----------------------------
    "5. SOLVENCIA TECNICA\n"
    "\n"
    "Se exigira una relacion de al menos tres contratos de naturaleza igual o "
    "similar ejecutados en los tres ultimos anos, cada uno por importe igual o "
    "superior a 80.000 euros.\n"
    "\n"
    "Asimismo, se presentara una memoria tecnica descriptiva firmada por "
    "tecnico competente.\n",
    # --- Pagina 6: criteris d'adjudicacio -----------------------------------
    "6. CRITERIOS DE ADJUDICACION\n"
    "\n"
    "Oferta economica: hasta 51 puntos, mediante formula automatica.\n"
    "\n"
    "Memoria tecnica: hasta 49 puntos, sujeta a juicio de valor.\n"
    "\n"
    "El umbral minimo para continuar en el procedimiento sera de 25 puntos en "
    "el conjunto de los criterios sujetos a juicio de valor.\n",
)


@dataclass(frozen=True, slots=True)
class Anchored:
    """Una cosa simulada i la frase EXACTA d'aquest corpus que la sosté.

    `quote` no es orientativa: es la que el proveidor buscara al text i la que
    tornara com a evidencia. Si algu edita el corpus i la frase deixa de ser-hi
    tal qual, aquest element desapareix de la simulacio. Es deliberat: es el que
    fa que la relacio entre exigencia i cita no es pugui trencar en silenci.
    """

    key: str
    quote: str
    payload: dict[str, Any] = field(default_factory=dict)


#: Els requisits del plec simulat, cadascun amb la seva frase.
REQUIREMENTS: tuple[Anchored, ...] = (
    Anchored(
        key="economic",
        quote=(
            "El volumen anual de negocios del licitador referido al mejor ejercicio de "
            "los tres ultimos disponibles debera ser igual o superior a 250.000 euros."
        ),
        payload={
            "category": RequirementCategory.ECONOMIC_SOLVENCY.value,
            "obligation": RequirementObligation.MANDATORY.value,
            "operator": RequirementOperator.GTE.value,
            "title": "Volum anual de negoci igual o superior a 250.000 EUR",
            "description": (
                "Referit al millor exercici dels tres ultims disponibles, "
                "acreditat amb els comptes anuals dipositats."
            ),
            "value": {"amount": 250000, "currency": "EUR", "period": "annual", "years": 3},
            "confidence": ExtractionConfidence.HIGH.value,
        },
    ),
    Anchored(
        key="insurance_total",
        quote=(
            "El licitador debera acreditar un seguro de responsabilidad civil por "
            "riesgos profesionales con un limite total de 600.000 euros."
        ),
        payload={
            "category": RequirementCategory.INSURANCE.value,
            "obligation": RequirementObligation.MANDATORY.value,
            "operator": RequirementOperator.GTE.value,
            "title": "Responsabilitat civil amb limit total de 600.000 EUR",
            "description": "Polissa vigent durant tota l'execucio del contracte.",
            "value": {
                "coverage_amount": 600000,
                "currency": "EUR",
                "type": "civil_liability",
            },
            "confidence": ExtractionConfidence.HIGH.value,
        },
    ),
    Anchored(
        key="insurance_per_victim",
        quote=(
            "Dentro de la misma poliza se exigira un sublimite por victima de 150.000 "
            "euros."
        ),
        payload={
            "category": RequirementCategory.INSURANCE.value,
            "obligation": RequirementObligation.MANDATORY.value,
            "operator": RequirementOperator.GTE.value,
            "title": "Sublimit per victima de 150.000 EUR",
            "description": (
                "Limit diferent del total i dins de la mateixa polissa: "
                "s'acredita a part."
            ),
            "value": {
                "coverage_amount": 150000,
                "currency": "EUR",
                "type": "civil_liability_per_victim",
            },
            "confidence": ExtractionConfidence.MEDIUM.value,
        },
    ),
    Anchored(
        key="certification",
        quote=(
            "El licitador debera disponer de certificado ISO 14001 en vigor, emitido "
            "por entidad de certificacion acreditada."
        ),
        payload={
            "category": RequirementCategory.CERTIFICATION.value,
            "obligation": RequirementObligation.MANDATORY.value,
            "operator": RequirementOperator.EXISTS.value,
            "title": "Certificat ISO 14001 en vigor",
            "description": "S'admeten certificats equivalents d'altres Estats membres.",
            "value": {"standard": "ISO 14001", "must_be_valid": True},
            "confidence": ExtractionConfidence.HIGH.value,
        },
    ),
    Anchored(
        key="experience",
        quote=(
            "Se exigira una relacion de al menos tres contratos de naturaleza igual o "
            "similar ejecutados en los tres ultimos anos, cada uno por importe igual o "
            "superior a 80.000 euros."
        ),
        payload={
            "category": RequirementCategory.EXPERIENCE.value,
            "obligation": RequirementObligation.MANDATORY.value,
            "operator": RequirementOperator.GTE.value,
            "title": "Tres contractes similars en els tres ultims anys",
            "description": "Cadascun per import igual o superior a 80.000 EUR.",
            "value": {"count": 3, "years": 3, "amount": 80000, "currency": "EUR"},
            "confidence": ExtractionConfidence.MEDIUM.value,
        },
    ),
    Anchored(
        # El cas que la interficie ha de saber ensenyar: el plec ho exigeix i cap
        # dada estructurada del perfil no ho pot acreditar tota sola.
        key="needs_review",
        quote=(
            "Asimismo, se presentara una memoria tecnica descriptiva firmada por "
            "tecnico competente."
        ),
        payload={
            "category": RequirementCategory.TECHNICAL_REQUIREMENT.value,
            "obligation": RequirementObligation.MANDATORY.value,
            "operator": RequirementOperator.UNKNOWN.value,
            "title": "Memoria tecnica signada per tecnic competent",
            "description": (
                "Cap dada del perfil no acredita per si sola aquest extrem: "
                "ho ha de mirar una persona."
            ),
            "value": None,
            "confidence": ExtractionConfidence.LOW.value,
        },
    ),
)

#: Els criteris d'adjudicacio, amb la mateixa regla.
AWARD_CRITERIA: tuple[Anchored, ...] = (
    Anchored(
        key="price",
        quote="Oferta economica: hasta 51 puntos, mediante formula automatica.",
        payload={
            "name": "Oferta economica",
            "description": "Formula automatica sobre la baixa oferta.",
            "points": 51,
            "evaluation_type": AwardEvaluationType.AUTOMATIC.value,
            "confidence": ExtractionConfidence.HIGH.value,
        },
    ),
    Anchored(
        key="technical",
        quote="Memoria tecnica: hasta 49 puntos, sujeta a juicio de valor.",
        payload={
            "name": "Memoria tecnica",
            "description": "Subjecta a judici de valor, amb llindar minim de 25 punts.",
            "points": 49,
            "evaluation_type": AwardEvaluationType.JUDGMENT.value,
            "confidence": ExtractionConfidence.MEDIUM.value,
        },
    ),
)


def corpus_text() -> str:
    """El plec sencer, per si cal materialitzar-lo com a document."""
    return "\n".join(PAGES)
