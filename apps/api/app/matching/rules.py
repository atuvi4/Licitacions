"""Regles del pre-fit: pesos, llindars i codis de motiu.

TOTS els numeros del motor viuen aqui. Si algun dia cal ajustar-los, es toca
aquest fitxer i es puja `RULES_VERSION`; no hi ha constants magiques escampades
per la resta de modul.

Cap d'aquestes regles fa servir IA, embeddings ni similitud semantica. Son
comparacions deterministes sobre metadades estructurades: el mateix input dona
sempre el mateix resultat.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.models.enums import DimensionResult

# Puja aquesta versio sempre que canvii una regla, un pes o un llindar.
# Permet respondre "amb quines regles es va calcular aquest 82?".
#
# v2 (F-06b): sense VEC, el pressupost base sense IVA (PBL) entra com a COTA
# INFERIOR del VEC, identificat; uns imports incoherents (VEC < PBL) no donen
# cap conclusio. Amb VEC coherent, el resultat es el mateix que a v1.
RULES_VERSION = "metadata-match-v2"


# ---------------------------------------------------------------------------
# Pesos
# ---------------------------------------------------------------------------
# Sumen 100. Escollits sabent la cobertura real de les dades ingerides:
# CPV 99%, NUTS 100%, imports 100%, termini 99%. Cap dimensio quedara
# sistematicament sense avaluar.
WEIGHT_CPV = 40
WEIGHT_GEOGRAPHY = 25
WEIGHT_ECONOMIC = 25
WEIGHT_TIMING = 10

TOTAL_WEIGHT = WEIGHT_CPV + WEIGHT_GEOGRAPHY + WEIGHT_ECONOMIC + WEIGHT_TIMING
assert TOTAL_WEIGHT == 100


# ---------------------------------------------------------------------------
# Llindars d'estat
# ---------------------------------------------------------------------------
CANDIDATE_THRESHOLD = 60      # a partir d'aqui val la pena revisar-la

# Motius de CPV que impedeixen ser candidata encara que la resta de dimensions
# siguin perfectes.
#
# Per que cal aquesta regla: sense coincidencia de CPV, el maxim possible es
# exactament 0+25+25+10 = 60, es a dir, just el llindar. Una licitacio sense cap
# relacio amb l'activitat de l'empresa podia arribar a candidata nomes per estar
# ben ubicada i tenir un import comode. I una coincidencia de DIVISIO (2 digits,
# "45 Treballs de construccio") arribava a 68, quan milers de contractes sense
# cap relacio comparteixen divisio.
#
# El CPV es QUE ES el contracte. Si no hi ha interseccio real amb l'activitat
# declarada, la ubicacio i el pressupost no fan la licitacio rellevant. Cal
# almenys coincidencia de GRUP (3 digits) per poder ser candidata.
CPV_CANDIDATE_BLOCKERS = frozenset(
    {
        "CPV_NO_OVERLAP",       # cap digit en comu
        "CPV_RELATED_DIVISION",  # nomes comparteixen els 2 primers digits
        "CPV_INACTIVE_ONLY",     # nomes encaixa amb CPV que l'empresa te desactivats
    }
)
HIGH_FIT_THRESHOLD = 80       # nomes per a metriques del dashboard
# Si no s'ha pogut avaluar almenys aquesta fraccio del pes total, el resultat
# no es un score baix: es "no tenim prou dades".
MIN_DATA_COVERAGE = 0.5


# ---------------------------------------------------------------------------
# Termini (dies naturals fins a la data limit)
# ---------------------------------------------------------------------------
DEADLINE_COMFORTABLE_DAYS = 20
DEADLINE_SHORT_DAYS = 10
DEADLINE_CRITICAL_DAYS = 5


# ---------------------------------------------------------------------------
# CPV: nivells de coincidencia deterministes segons l'estructura del codi
# ---------------------------------------------------------------------------
# Un CPV son 8 digits amb jerarquia per prefix: 2 = divisio, 3 = grup,
# 4 = classe, 5 = categoria. Comparar prefixos NO es inventar equivalencies:
# es llegir l'estructura oficial del codi. No cal tenir el cataleg carregat.
CPV_PREFIX_LEVELS: tuple[tuple[int, str, float], ...] = (
    (8, "EXACT", 1.00),
    (5, "CATEGORY", 0.75),
    (4, "CLASS", 0.55),
    (3, "GROUP", 0.35),
    (2, "DIVISION", 0.20),
)

# Multiplicador segons la prioritat que l'empresa ha donat al seu CPV (0-100).
CPV_PRIORITY_HIGH = 8     # >= 8 -> prioritat alta
CPV_PRIORITY_MEDIUM = 4   # >= 4 -> mitjana
CPV_PRIORITY_FACTORS = {"high": 1.00, "medium": 0.85, "low": 0.70}
# Un CPV desactivat no exclou: nomes deixa de sumar com un d'actiu.
CPV_INACTIVE_FACTOR = 0.30


# ---------------------------------------------------------------------------
# Territori: com de bo es un encaix segons la prioritat declarada
# ---------------------------------------------------------------------------
TERRITORY_PRIORITY_HIGH = 8
TERRITORY_PRIORITY_MEDIUM = 4
TERRITORY_PRIORITY_FACTORS = {"high": 1.00, "medium": 0.80, "low": 0.60}

# Territori NO declarat: l'empresa ha dit on PREFEREIX treballar, no on es nega
# a fer-ho (per a aixo hi ha `willing_to_work=false`, que si que exclou). Tractar
# un territori no declarat com un de rebutjat faria desapareixer del radar una
# licitacio excel.lent en CPV, import i termini nomes per ser a una altra
# provincia, quan justament el que ha de fer un filtre de prioritzacio es
# ensenyar-la mes avall a la llista i deixar decidir l'empresa.
TERRITORY_NOT_DECLARED_FACTOR = 0.25


# ---------------------------------------------------------------------------
# Import: fraccio del pes economic per a cada tram
# ---------------------------------------------------------------------------
AMOUNT_FACTORS = {
    "preferred": 1.00,          # dins del rang preferit
    "interesting": 0.75,        # per sobre del minim interessant, sota el preferit
    "above_preferred": 0.60,    # entre el maxim preferit i el recomanat
    "above_recommended": 0.30,  # entre el recomanat i el maxim assumible
    "below_minimum": 0.15,      # per sota del minim interessant
}


# ---------------------------------------------------------------------------
# Codis de motiu
# ---------------------------------------------------------------------------
# El text de la interficie es deriva d'aquests codis, mai al reves. Aixi el
# motiu es tradueix, es filtra i es pot analitzar.
class ReasonCode:
    CPV_EXACT_HIGH_PRIORITY = "CPV_EXACT_HIGH_PRIORITY"
    CPV_EXACT_MEDIUM_PRIORITY = "CPV_EXACT_MEDIUM_PRIORITY"
    CPV_EXACT_LOW_PRIORITY = "CPV_EXACT_LOW_PRIORITY"
    CPV_RELATED_CATEGORY = "CPV_RELATED_CATEGORY"
    CPV_RELATED_CLASS = "CPV_RELATED_CLASS"
    CPV_RELATED_GROUP = "CPV_RELATED_GROUP"
    CPV_RELATED_DIVISION = "CPV_RELATED_DIVISION"
    CPV_INACTIVE_ONLY = "CPV_INACTIVE_ONLY"
    CPV_NO_OVERLAP = "CPV_NO_OVERLAP"
    CPV_EXCLUDED = "CPV_EXCLUDED"
    CPV_UNKNOWN = "CPV_UNKNOWN"
    CPV_NO_COMPANY_PREFERENCES = "CPV_NO_COMPANY_PREFERENCES"

    TERRITORY_HIGH_PRIORITY = "TERRITORY_HIGH_PRIORITY"
    TERRITORY_MEDIUM_PRIORITY = "TERRITORY_MEDIUM_PRIORITY"
    TERRITORY_ALLOWED = "TERRITORY_ALLOWED"
    TERRITORY_NOT_DECLARED = "TERRITORY_NOT_DECLARED"
    TERRITORY_EXCLUDED = "TERRITORY_EXCLUDED"
    TERRITORY_UNKNOWN = "TERRITORY_UNKNOWN"
    TERRITORY_NO_COMPANY_DATA = "TERRITORY_NO_COMPANY_DATA"

    AMOUNT_PREFERRED = "AMOUNT_PREFERRED"
    AMOUNT_INTERESTING = "AMOUNT_INTERESTING"
    AMOUNT_BELOW_MINIMUM = "AMOUNT_BELOW_MINIMUM"
    AMOUNT_ABOVE_PREFERRED = "AMOUNT_ABOVE_PREFERRED"
    AMOUNT_ABOVE_RECOMMENDED = "AMOUNT_ABOVE_RECOMMENDED"
    AMOUNT_ABOVE_MAXIMUM = "AMOUNT_ABOVE_MAXIMUM"
    AMOUNT_UNKNOWN = "AMOUNT_UNKNOWN"
    AMOUNT_NO_COMPANY_PREFERENCES = "AMOUNT_NO_COMPANY_PREFERENCES"
    # v2: nomes hi ha el PBL i no supera cap llindar; el VEC podria ser mes alt.
    AMOUNT_BUDGET_LOWER_BOUND = "AMOUNT_BUDGET_LOWER_BOUND"
    # v2: el VEC publicat es inferior al PBL sense IVA, que no pot ser.
    AMOUNT_INCOHERENT = "AMOUNT_INCOHERENT"
    # v2: nomes hi ha el PBL AMB IVA. No es cota del VEC (que es sense IVA): cap conclusio.
    AMOUNT_BUDGET_WITH_TAX_ONLY = "AMOUNT_BUDGET_WITH_TAX_ONLY"

    DEADLINE_COMFORTABLE = "DEADLINE_COMFORTABLE"
    DEADLINE_ADEQUATE = "DEADLINE_ADEQUATE"
    DEADLINE_SHORT = "DEADLINE_SHORT"
    DEADLINE_CRITICAL = "DEADLINE_CRITICAL"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    DEADLINE_UNKNOWN = "DEADLINE_UNKNOWN"

    STATUS_NOT_OPEN = "STATUS_NOT_OPEN"


# Estats de licitacio que ja no son accionables per presentar-s'hi.
NON_ACTIONABLE_STATUSES = {"awarded", "resolved", "cancelled"}


@dataclass(slots=True)
class Factor:
    """Una comparacio concreta, amb el seu motiu i els valors que la sustenten."""

    dimension: str
    result: DimensionResult
    reason_code: str
    weight: int
    score: float | None
    company_value: str | None = None
    source_value: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "result": self.result.value,
            "reason_code": self.reason_code,
            "weight": self.weight,
            "score": round(self.score, 2) if self.score is not None else None,
            "max_score": self.weight,
            "company_value": self.company_value,
            "source_value": self.source_value,
            "detail": self.detail,
        }


@dataclass(slots=True)
class DimensionOutcome:
    """Resultat d'avaluar una dimensio.

    `score` a None vol dir UNKNOWN: la dimensio queda fora del calcul en comptes
    de comptar com un zero, que seria penalitzar una dada que no tenim.
    """

    factor: Factor
    excluded: bool = False

    @property
    def score(self) -> float | None:
        return self.factor.score

    @property
    def weight(self) -> int:
        return self.factor.weight

    @property
    def is_unknown(self) -> bool:
        return self.factor.result is DimensionResult.UNKNOWN


def priority_band(priority: int, high: int, medium: int) -> str:
    if priority >= high:
        return "high"
    if priority >= medium:
        return "medium"
    return "low"
