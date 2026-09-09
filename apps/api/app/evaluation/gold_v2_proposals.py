"""Les cinc correccions que la revisio humana del gold va demanar.

No son generades: estan escrites a ma, aqui, perque es puguin llegir i discutir
en un pull request abans que ningu les verifiqui. Cada una porta la frase del
plec que la sosté, **copiada literalment del text extret**, amb els seus
artefactes inclosos ("se val orará", "valorar án"): la cita ha de ser
localitzable a la pagina real, no ha de quedar bonica. Per aixo els literals
citables porten accents i la resta del fitxer no: son text del plec, no text
nostre.

Tres coses que aquest fitxer NO fa, i el motiu:

* **No inventa el que no hi es.** Al cas de Madrid (300/2025/03877) l'expressio
  de la formula no apareix al text extret: la pagina 70 acaba amb "se aplicara
  la siguiente formula:" i la 71 comença amb les definicions de les variables.
  Molt probablement es una imatge. Per tant `formula` queda a `None` i el buit
  queda escrit a `unresolved`. Posar-hi una formula plausible faria que el
  benchmark castigues el model per no extreure una cosa que el text no conte.
* **No verifica res.** Les propostes neixen sense `reviewed_by`. La decisio es
  d'una persona, a la pagina de revisio.
* **No toca les 11 anotacions ja verificades.** Nomes proposa sobre les 5 que
  la revisio va deixar en `needs_review`.
"""

from __future__ import annotations

from app.ai.value_schemas import VALUE_SCHEMA_VERSION
from app.evaluation.dataset import AnnotationStatus
from app.evaluation.dataset_v2 import (
    AnnotationOrigin,
    GoldAwardCriterionV2,
    GoldEvidence,
    GoldFormulaVariable,
    GoldRequirementV2,
)
from app.models.enums import (
    AwardCriterionType,
    AwardEvaluationType,
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
)

#: Qui escriu les propostes. Diferent de qui les revisara: una anotacio no
#: s'autoverifica mai.
PROPOSED_BY = "assistant/4D.2A"

PCA = "Plec de clausules administratives"


# --------------------------------------------------------------------------- #
# A. Classificacio empresarial com a VIA, no com a exigencia                    #
# --------------------------------------------------------------------------- #
#
# El text diu "podrá ... presentar un certificado de clasificación". La v1 ho va
# anotar `mandatory`, i a 4C aixo es converteix en un blocador: una empresa
# sense classificacio quedaria fora d'una licitacio a la qual s'hi pot
# presentar acreditant la solvencia per l'altra via.
#
# Es queda en `needs_review` i no en `draft` perque l'alternativa NO es pot
# determinar sencera: el grup, el subgrup i la categoria es remeten a l'apartat
# 7.d) del quadre de caracteristiques, que no es en aquesta pagina. Es pot
# afirmar que es una via alternativa; no es pot afirmar de que.

CLASSIFICATION_ALTERNATIVE = (
    "2026SE00013",
    GoldRequirementV2(
        gold_id="class-1",
        annotation_status=AnnotationStatus.NEEDS_REVIEW,
        annotated_by=PROPOSED_BY,
        origin=AnnotationOrigin.PROPOSAL,
        proposal_reason=(
            "La revisio humana va marcar que el text diu 'podra presentar' la "
            "classificacio per acreditar solvencia: es un mitja alternatiu, no un "
            "requisit mandatory."
        ),
        category=RequirementCategory.BUSINESS_CLASSIFICATION,
        # `alternative` + `mandatory` seria contradictori i el model del gold ho
        # rebutja: una via no es una exigencia.
        obligation=RequirementObligation.UNKNOWN,
        modality=RequirementModality.ALTERNATIVE,
        alternative_group="acreditacio-solvencia",
        operator=None,
        scope=RequirementScope.TENDER,
        value_schema=VALUE_SCHEMA_VERSION,
        value={
            "kind": "classification",
            # Grup, subgrup i categoria NO son al text: es remeten a l'apartat
            # 7.d). Deixar-los sense informar es la informacio correcta.
            "equivalence_allowed": False,
            "proof": "certificate",
        },
        document_name=PCA,
        page_number=11,
        quote=(
            "La empresa licitadora podrá, para acreditar su solvencia, presentar un "
            "certificado de clasificación en el grupo, subgrupo y categoría que, "
            "adecuados al objeto del presente contrato, se indiquen en el apartado "
            "7.d) del cuadro de características"
        ),
        extra_evidence=[
            GoldEvidence(
                document_name=PCA,
                page_number=11,
                quote=(
                    "La solvencia económica, financiera y técnica o profesional se "
                    "acreditará mediante la presentación de la documentación requerida "
                    "en el apartado 7 del cuadro de características."
                ),
                role="L'altra via de la disjuncio, tal com la formula la mateixa pagina",
            )
        ],
        unresolved=[
            "El grup, el subgrup i la categoria es remeten a l'apartat 7.d) del quadre "
            "de caracteristiques: no son en aquesta pagina i no s'inventen.",
            "L'altra branca de la disjuncio (la documentacio de l'apartat 7) es cita pero "
            "no esta anotada com a requisit propi: el grup d'alternatives te un sol membre.",
        ],
        critical=None,
        notes=(
            "Cas de blocador fals: anotar-ho com a `mandatory` exclouria una empresa que "
            "pot acreditar la solvencia per l'altra via."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# B. Experiencia: el llindar quantitatiu que la v1 no podia representar         #
# --------------------------------------------------------------------------- #
#
# La v1 va anotar nomes "relación de los principales servicios ... tres últimos
# años" i va perdre el llindar sencer. El text el dona dues vegades: com a
# percentatge amb base ("70% de la anualidad media del contrato") i com a
# import explicit ("21.406,71 €"). Es conserven tots dos: el percentatge es el
# que diu el plec, l'import es el que en surt, i tenir-los alhora permet mesurar
# si el model conserva la base o nomes copia la xifra.

EXPERIENCE_THRESHOLD = (
    "300-2025-03877",
    GoldRequirementV2(
        gold_id="exp-1",
        annotation_status=AnnotationStatus.DRAFT,
        annotated_by=PROPOSED_BY,
        origin=AnnotationOrigin.PROPOSAL,
        proposal_reason=(
            "La revisio humana va marcar que faltava el llindar de solvencia tecnica: "
            "import anual acumulat de serveis similars en l'any de major execucio "
            ">= 70% de l'anualitat mitjana del contracte, es a dir >= 21.406,71 EUR."
        ),
        category=RequirementCategory.EXPERIENCE,
        obligation=RequirementObligation.MANDATORY,
        modality=RequirementModality.MANDATORY,
        # A la v1 l'operador era null perque no s'havia extret cap llindar.
        operator=RequirementOperator.GTE,
        scope=RequirementScope.TENDER,
        value_schema=VALUE_SCHEMA_VERSION,
        value={
            "kind": "experience",
            "work_type": "servicios o trabajos",
            "reference_period": {"type": "last_n_years", "years": 3},
            # Acumulat, no per contracte: el plec suma l'any sencer.
            "min_accumulated_amount": {
                "amount": 21406.71,
                "currency": "EUR",
                "percentage": 70,
                "percentage_base": "contract_annuity",
            },
            # "del año de mayor ejecución": no es cada any, es el millor.
            "aggregation": "max",
            "similarity_note": "igual o similar naturaleza",
            "proof": "certificate",
        },
        document_name=PCA,
        page_number=68,
        quote=(
            "Se considerará acreditada la solvencia cuando el importe anual acumulado de "
            "todos los servicios o trabajos del año de mayor ejecución sea igual o "
            "superior al 70% de la anualidad media del contrato (IVA excluido), es decir "
            "igual o superior a la cuantía de 21.406,71 €."
        ),
        extra_evidence=[
            GoldEvidence(
                document_name=PCA,
                page_number=68,
                quote=(
                    "una relación de los servicios o trabajos realizados de igual o similar "
                    "naturaleza que los que constituyen el objeto del contrato en el curso "
                    "de los tres últimos años 2024, 2025 y 2026"
                ),
                role="Periode de referencia i condicio de similitud",
            )
        ],
        unresolved=[
            "El plec no dona codis CPV: diu que la similitud es determina pels tres primers "
            "digits dels codis respectius, pero no els enumera.",
        ],
        critical=None,
        notes=(
            "El percentatge i l'import conviuen a proposit: el 70% es el que diu el plec i "
            "21.406,71 EUR es el que en resulta, explicitat al mateix text."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# C. Formula economica que el text extret NO conte                             #
# --------------------------------------------------------------------------- #
#
# Aquest es el cas incomode, i per aixo es el mes important de deixar ben
# escrit. La pagina 70 acaba anunciant la formula i la 71 comença definint-ne
# les variables: l'expressio, entremig, no es al text extret. Anotar-hi una
# formula deduida de les variables seria inventar gold, i el benchmark passaria
# a mesurar la nostra imaginacio.
#
# Per tant: `formula = None`, `formula_text` amb la frase literal que l'anuncia,
# les quatre variables que SI que estan definides literalment, i el buit escrit
# a `unresolved`.

AWARD_FORMULA_NOT_IN_TEXT = (
    "300-2025-03877",
    GoldAwardCriterionV2(
        gold_id="award-1",
        annotation_status=AnnotationStatus.DRAFT,
        annotated_by=PROPOSED_BY,
        origin=AnnotationOrigin.PROPOSAL,
        proposal_reason=(
            "La revisio humana va marcar que faltava la formula de puntuacio de "
            "l'oferta economica; el plec indica explicitament que se n'aplicara una."
        ),
        name="Oferta economica",
        criterion_type=AwardCriterionType.PRICE,
        points=45.0,
        weight_percent=None,
        evaluation_type=AwardEvaluationType.AUTOMATIC,
        formula=None,
        formula_text=(
            "Se pretende otorgar la puntuación en función de la proporción entre la "
            "máxima baja ofertada y el resto de éstas, correspondiendo 0 puntos a "
            "aquellas empresas que no realicen baja alguna, para ello se aplicará la "
            "siguiente fórmula:"
        ),
        formula_variables=[
            GoldFormulaVariable(symbol="P", meaning="es la puntuación de la oferta a valorar"),
            GoldFormulaVariable(
                symbol="PBL", meaning="es el presupuesto base de licitación sin IVA"
            ),
            GoldFormulaVariable(
                symbol="OE",
                meaning="es la oferta correspondiente al licitador que se valora sin IVA",
            ),
            GoldFormulaVariable(
                symbol="OB", meaning="es la oferta económica más baja admitida sin IVA"
            ),
        ],
        document_name=PCA,
        page_number=70,
        quote=(
            "Se pretende otorgar la puntuación en función de la proporción entre la máxima "
            "baja ofertada y el resto de éstas, correspondiendo 0 puntos a aquellas "
            "empresas que no realicen baja alguna, para ello se aplicará la siguiente "
            "fórmula:"
        ),
        extra_evidence=[
            GoldEvidence(
                document_name=PCA,
                page_number=70,
                quote="A la oferta económica se le otorgará una ponderación de 45 puntos.",
                role="Puntuacio maxima del criteri",
            ),
            GoldEvidence(
                document_name=PCA,
                page_number=71,
                quote=(
                    "P: es la puntuación de la oferta a valorar PBL: es el presupuesto base "
                    "de licitación sin IVA OE: es la oferta correspondiente al licitador que "
                    "se valora sin IVA OB: es la oferta económica más baja admitida sin IVA"
                ),
                role="Definicio literal de les variables, a la pagina seguent",
            ),
        ],
        unresolved=[
            "L'EXPRESSIO de la formula no apareix al text extret: la pagina 70 acaba amb "
            "'se aplicará la siguiente fórmula:' i la 71 comença amb les definicions de les "
            "variables. Molt probablement es una imatge que l'extraccio de text no recull.",
            "Sense l'expressio, aquesta anotacio pot mesurar que el model conserva les "
            "variables i la frase, pero NO que reprodueixi la formula.",
        ],
        notes=(
            "No s'hi escriu cap formula deduida de les variables: seria gold inventat, i el "
            "benchmark castigaria el model per no extreure el que el text no conte."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# D. Formula literal, amb operands en llenguatge natural                       #
# --------------------------------------------------------------------------- #
#
# Aqui la formula hi es sencera. Es copia tal com surt del PDF, amb els talls de
# paraula inclosos ("se val orará de la for ma sig uiente"): la cita ha de ser
# localitzable al text real.
#
# `formula_variables` queda BUIDA, i no es un descuit: la formula no fa servir
# simbols definits ("PL" no es defineix enlloc), sino operands escrits en
# paraules. Inventar-hi definicions seria afegir gold que el plec no dona.

AWARD_FORMULA_NATURAL_OPERANDS = (
    "422-2026",
    GoldAwardCriterionV2(
        gold_id="award-1",
        annotation_status=AnnotationStatus.DRAFT,
        annotated_by=PROPOSED_BY,
        origin=AnnotationOrigin.PROPOSAL,
        proposal_reason=(
            "La revisio humana va marcar que faltava la formula: "
            "PL=10 x Oferta más baja / Oferta a valorar."
        ),
        name="Propuesta economica",
        criterion_type=AwardCriterionType.PRICE,
        points=10.0,
        weight_percent=None,
        evaluation_type=AwardEvaluationType.AUTOMATIC,
        formula="PL=10 x Oferta más baja / Oferta a valorar",
        formula_text=(
            "La oferta económica se val orará de la for ma sig uiente: Se otorgará la "
            "puntuación máxima (10 puntos) a la oferta más económica, y la puntuación del "
            "resto de las ofertas se calculará mediante la siguiente fórmula: PL=10 x "
            "Oferta más baja / Oferta a valorar"
        ),
        formula_variables=[],
        document_name=PCA,
        page_number=12,
        quote=(
            "la puntuación del resto de las ofertas se calculará mediante la siguiente "
            "fórmula: PL=10 x Oferta más baja / Oferta a valorar"
        ),
        extra_evidence=[
            GoldEvidence(
                document_name=PCA,
                page_number=12,
                quote="PROPUESTA ECONÓMICA (HASTA UN MÁXIMO DE 10 PUNTOS)",
                role="Puntuacio maxima del criteri",
            )
        ],
        unresolved=[
            "La formula no defineix simbols: els operands son en llenguatge natural "
            "('Oferta más baja', 'Oferta a valorar') i 'PL' no es defineix enlloc del text. "
            "Per aixo `formula_variables` es buida.",
        ],
        notes=(
            "La cita conserva els talls de paraula del PDF: es el text real de la pagina i "
            "ha de ser localitzable tal qual."
        ),
    ),
)


# --------------------------------------------------------------------------- #
# E. Formula literal amb variables definides                                   #
# --------------------------------------------------------------------------- #
#
# El cas complet: expressio i definicions, totes dues al text. Es l'unic dels
# tres que permet mesurar de veritat la conservacio d'una formula.

AWARD_FORMULA_WITH_VARIABLES = (
    "P4107500C-2026-65",
    GoldAwardCriterionV2(
        gold_id="award-1",
        annotation_status=AnnotationStatus.DRAFT,
        annotated_by=PROPOSED_BY,
        origin=AnnotationOrigin.PROPOSAL,
        proposal_reason="La revisio humana va marcar que faltava la formula: P = 75 x (OM/OF).",
        name="Oferta economica",
        criterion_type=AwardCriterionType.PRICE,
        points=75.0,
        weight_percent=None,
        evaluation_type=AwardEvaluationType.AUTOMATIC,
        formula="P = 75 x (OM/OF)",
        formula_text=(
            "El resto de las ofertas se valorar án en proporción inversa a las cuantías "
            "ofertadas respecto a la oferta más económica siendo la fórmula a aplicar la "
            "siguiente: P = 75 x (OM/OF), siendo: P: puntuación obtenida. OF: oferta "
            "económica del licitador que se valora. OM: oferta económica más ventajosa."
        ),
        formula_variables=[
            GoldFormulaVariable(symbol="P", meaning="puntuación obtenida"),
            GoldFormulaVariable(
                symbol="OF", meaning="oferta económica del licitador que se valora"
            ),
            GoldFormulaVariable(symbol="OM", meaning="oferta económica más ventajosa"),
        ],
        document_name=PCA,
        page_number=13,
        quote=(
            "siendo la fórmula a aplicar la siguiente: P = 75 x (OM/OF), siendo: P: "
            "puntuación obtenida. OF: oferta económica del licitador que se valora. OM: "
            "oferta económica más ventajosa."
        ),
        extra_evidence=[
            GoldEvidence(
                document_name=PCA,
                page_number=13,
                quote="1) Oferta económica, hasta un máximo de 75 puntos.",
                role="Puntuacio maxima del criteri",
            )
        ],
        notes="Cas complet: expressio i definicions de variables, totes dues literals.",
    ),
)


#: Les cinc correccions, per cas. Substitueixen una anotacio existent.
PROPOSALS = (
    CLASSIFICATION_ALTERNATIVE,
    EXPERIENCE_THRESHOLD,
    AWARD_FORMULA_NOT_IN_TEXT,
    AWARD_FORMULA_NATURAL_OPERANDS,
    AWARD_FORMULA_WITH_VARIABLES,
)


# --------------------------------------------------------------------------- #
# F. Els dos limits de la mateixa polissa                                      #
# --------------------------------------------------------------------------- #
#
# Aquesta NO substitueix res: s'afegeix. `TSA0082096-040::ins-1` esta verificada
# i es queda exactament com esta; el que passa es que el seu valor v1
# (`coverage_amount: 500000`) no pot expressar que la mateixa polissa te tambe
# un sublimit per victima de 300.000 €, i tornar a obrir una anotacio que una
# persona ja va signar seria rebaixar-la.
#
# El motiu de fons: sense aixo, `insurance_limits` es l'unica dimensio de la v2
# que ni tan sols podria arribar a ser mesurable —hi ha cinc anotacions
# d'assegurança verificades i cap distingeix els limits—, i el sublimit es
# precisament la part mes restrictiva d'una polissa: perdre'l fa semblar coberta
# una empresa que no ho esta.
#
# Que les dues convisquin te un preu, i esta escrit a `unresolved`: si algu
# verifiques aquesta sense retirar l'altra, el gold demanaria dues assegurances
# on el plec en demana una. `refines` fa que aixo es pugui detectar.

INSURANCE_SUBLIMIT = (
    "TSA0082096-040",
    GoldRequirementV2(
        gold_id="ins-2",
        annotation_status=AnnotationStatus.DRAFT,
        annotated_by=PROPOSED_BY,
        origin=AnnotationOrigin.PROPOSAL,
        refines="ins-1",
        proposal_reason=(
            "La v1 va aplanar la polissa en un sol import i el sublimit per victima "
            "de 300.000 EUR va desapareixer. Aquesta proposta representa els dos "
            "limits amb el seu tipus, sense tocar l'anotacio ja verificada."
        ),
        category=RequirementCategory.INSURANCE,
        obligation=RequirementObligation.MANDATORY,
        modality=RequirementModality.MANDATORY,
        operator=RequirementOperator.GTE,
        scope=RequirementScope.TENDER,
        value_schema=VALUE_SCHEMA_VERSION,
        value={
            "kind": "insurance",
            "insurance_type": "civil_liability",
            # El text del plec al costat de l'enum: la classificacio es una
            # lectura, la frase es el que hi diu.
            "insurance_type_note": "Responsabilidad Civil de Explotación",
            # DUES condicions sobre la MATEIXA polissa, no dos requisits.
            "limits": [
                {
                    "kind": "total",
                    "amount": 500000,
                    "currency": "EUR",
                    "note": "por daños materiales y personales",
                },
                {"kind": "per_victim", "amount": 300000, "currency": "EUR"},
            ],
        },
        document_name="Plec de prescripcions tecniques",
        page_number=4,
        quote=(
            "El ofertante tendrá suscrito seguro de Responsabilidad Civil de Explotación "
            "con cobertura de 500.000 euros por daños materiales y personales con un "
            "sublímite por víctima de 300.000 €."
        ),
        extra_evidence=[
            GoldEvidence(
                document_name="Plec de prescripcions tecniques",
                page_number=4,
                quote=(
                    "El ofertante deberá tener suscrita póliza de Seguros de "
                    "Responsabilidad Civil General de las actividades que realice:"
                ),
                role="L'obligacio de tenir la polissa, de la qual aquests son els limits",
            )
        ],
        unresolved=[
            "Es la MATEIXA polissa que `TSA0082096-040::ins-1`, que ja esta verificada. "
            "Verificar aquesta sense retirar l'altra faria que el gold demanes dues "
            "assegurances on el plec en demana una.",
            "La pagina no diu ni fins quan ha d'estar vigent la polissa ni com "
            "s'acredita: `proof` i la vigencia queden sense informar.",
        ],
        critical=None,
        notes=(
            "Existeix per fer mesurable `insurance_limits`: es l'unic cas del gold amb "
            "un sublimit explicit al text."
        ),
    ),
)


#: Anotacions NOVES, que no substitueixen cap anotacio existent. Van a part de
#: `PROPOSALS` a proposit: afegir i corregir son coses diferents, i confondre-les
#: faria que un `gold_id` mal escrit s'afegis en silenci en comptes de fallar.
ADDITIONS = (INSURANCE_SUBLIMIT,)
