"""Trobar un valor dins d'una cita, de manera determinista. Sense LLM ni fuzzy.

Nomes respon preguntes literals: "hi ha un 600 seguit de Mbps en aquest tros
de text?", "hi apareix el terme GPON?". Mai "vol dir el mateix?". Si una
pregunta no es pot respondre literalment, qui crida ha de tractar el claim com a
no verificat.

Formats del plec que es reconeixen:

* numeros catalans/espanyols: `155.239,16`, `500.000`, `72.444,94`, `10,5`;
* imports amb `€`, `EUR`, `euros` (davant o darrere);
* percentatges amb `%`, `per cent`, `por ciento`;
* durades en hores/dies/setmanes/mesos/anys (ca/es), tambe `tres anys`;
* amples de banda en kbps/Mbps/Gbps (`Mb/s`, `Mbit/s`).

Les conversions son nomes les exactes (1 any = 12 mesos, 1 Gbps = 1000 Mbps).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.ai.schemas_v3 import ClaimKind, ClaimUnit


def normalise(text: str) -> str:
    """Minuscules, sense accents, espais col·lapsats. Conserva digits i signes."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace(" ", " ").replace(" ", " ").lower()
    # Apostrof tipografic dels PDF ("d’adjudicacio") -> apostrof pla.
    text = text.replace("’", "'").replace("‘", "'").replace("´", "'")
    return re.sub(r"[ \t\r\f\v]+", " ", text)


#: On es talla una linia en trossos independents. `, ` i no `,`: la coma
#: decimal (`155.239,16`) no porta mai espai darrere.
_SEGMENT_SPLIT = re.compile(r";|•|\||, | - ")
#: Una linia que acaba aixi tanca una fila o una frase. Si no, el salt de linia
#: es de maquetacio del PDF i la fila continua a la linia seguent.
_ROW_END = re.compile(r"[\d€%.:;]\s*$")


def segments(text: str) -> list[str]:
    """Els trossos d'una cita que es poden llegir com una sola afirmacio.

    Un salt de linia nomes tanca un tros si la linia acaba en xifra, `€`, `%` o
    puntuacio final. El PDF talla files de taula: a Palamos, "Acces a Internet
    GPON a 600 Mbps simetrics pels equipaments" + "municipals* 26" son UNA fila.
    Tallar-les separaria el terme del seu valor. Ajuntar linies mai no fa
    sostenir res per casualitat: un tros amb dos valors rivals es ambigu.
    """
    rows: list[str] = []
    pending = ""
    for line in normalise(text).split("\n"):
        pending = f"{pending} {line.strip()}".strip()
        if pending and _ROW_END.search(pending):
            rows.append(pending)
            pending = ""
    if pending:
        rows.append(pending)
    return [part.strip() for row in rows for part in _SEGMENT_SPLIT.split(row) if part.strip()]


_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)(?!\d)")


def parse_number(raw: str) -> float | None:
    raw = raw.strip()
    if "," in raw:  # 155.239,16 / 10,5
        raw = raw.replace(".", "").replace(",", ".")
    elif "." in raw and all(len(part) == 3 for part in raw.split(".")[1:]):
        raw = raw.replace(".", "")  # 500.000
    try:
        return float(raw)
    except ValueError:
        return None


# Unitat -> (familia, factor a la base de la familia). Nomes conversions exactes.
_UNIT_BASE: dict[ClaimUnit, tuple[str, float]] = {
    ClaimUnit.EUR: ("eur", 1.0),
    ClaimUnit.PERCENT: ("percent", 1.0),
    ClaimUnit.HOURS: ("time_h", 1.0),
    ClaimUnit.DAYS: ("time_h", 24.0),
    ClaimUnit.WEEKS: ("time_h", 168.0),
    ClaimUnit.MONTHS: ("time_m", 1.0),
    ClaimUnit.YEARS: ("time_m", 12.0),
    ClaimUnit.KBPS: ("bw", 1.0),
    ClaimUnit.MBPS: ("bw", 1000.0),
    ClaimUnit.GBPS: ("bw", 1_000_000.0),
}

# Els patrons de darrere del numero, ja normalitzats (sense accents).
_UNIT_AFTER: list[tuple[ClaimUnit, re.Pattern[str]]] = [
    (ClaimUnit.EUR, re.compile(r"\s?(?:€|eur\b|euros?\b)")),
    (ClaimUnit.PERCENT, re.compile(r"\s?(?:%|per ?cent\b|por ?ciento\b|percent\b)")),
    (ClaimUnit.HOURS, re.compile(r"\s?(?:hores|hora|horas|h)\b")),
    (ClaimUnit.DAYS, re.compile(r"\s?(?:dies|dia|dias)\b")),
    (ClaimUnit.WEEKS, re.compile(r"\s?(?:setmanes|setmana|semanas|semana)\b")),
    (ClaimUnit.MONTHS, re.compile(r"\s?(?:mesos|mes|meses)\b")),
    (ClaimUnit.YEARS, re.compile(r"\s?(?:anys|any|anos|ano|anualitats|anualidades)\b")),
    (ClaimUnit.KBPS, re.compile(r"\s?(?:kbps|kb/s|kbit/s|kbits?)\b")),
    (ClaimUnit.MBPS, re.compile(r"\s?(?:mbps|mb/s|mbit/s|mbits?|megas)\b")),
    (ClaimUnit.GBPS, re.compile(r"\s?(?:gbps|gb/s|gbit/s|gbits?)\b")),
]
_EUR_BEFORE = re.compile(r"(?:€|eur)\s?$")

_KIND_FAMILIES: dict[ClaimKind, frozenset[str]] = {
    ClaimKind.AMOUNT: frozenset({"eur"}),
    ClaimKind.PERCENTAGE: frozenset({"percent"}),
    ClaimKind.DURATION: frozenset({"time_h", "time_m"}),
    ClaimKind.BANDWIDTH: frozenset({"bw"}),
}

#: Nomes per a durades ("tres anys", "un any"). Fora de durades, "un" es un
#: article i donaria coincidencies falses.
_NUMBER_WORDS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "dues": 2, "tres": 3, "quatre": 4,
    "cuatro": 4, "cinc": 5, "cinco": 5, "sis": 6, "seis": 6, "set": 7, "siete": 7,
    "vuit": 8, "ocho": 8, "nou": 9, "nueve": 9, "deu": 10, "diez": 10, "onze": 11,
    "once": 11, "dotze": 12, "doce": 12,
}
_WORD_NUMBER = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b")


@dataclass(frozen=True, slots=True)
class Occurrence:
    """Un numero trobat, amb la unitat que l'acompanya (o cap)."""

    value: float
    unit: ClaimUnit | None


def occurrences(segment: str) -> list[Occurrence]:
    """Tots els numeros d'un tros ja normalitzat, amb la seva unitat."""
    found: list[Occurrence] = []
    for match in _NUMBER.finditer(segment):
        value = parse_number(match.group(1))
        if value is None:
            continue
        found.append(Occurrence(value, _unit_at(segment, match.start(), match.end())))
    for match in _WORD_NUMBER.finditer(segment):
        unit = _unit_at(segment, match.start(), match.end())
        if unit is not None and _UNIT_BASE[unit][0] in _KIND_FAMILIES[ClaimKind.DURATION]:
            found.append(Occurrence(float(_NUMBER_WORDS[match.group(1)]), unit))
    return found


def _unit_at(segment: str, start: int, end: int) -> ClaimUnit | None:
    after = segment[end:]
    for unit, pattern in _UNIT_AFTER:
        if pattern.match(after):
            return unit
    if _EUR_BEFORE.search(segment[max(0, start - 5):start]):
        return ClaimUnit.EUR
    return None


def canonical(value: float, unit: ClaimUnit | None) -> tuple[str, float]:
    if unit is None:
        return ("count", value)
    family, factor = _UNIT_BASE[unit]
    return (family, value * factor)


def typed(occs: list[Occurrence], kind: ClaimKind) -> list[tuple[str, float]]:
    """Les ocurrencies que son del mateix tipus que el claim, en forma canonica.

    Un COUNT nomes compta numeros SENSE unitat: el `600` de `600 Mbps` no es un
    recompte, i no pot sostenir `accessos = 600` per casualitat.
    """
    if kind == ClaimKind.COUNT:
        return [canonical(o.value, None) for o in occs if o.unit is None]
    families = _KIND_FAMILIES[kind]
    return [
        canonical(o.value, o.unit)
        for o in occs
        if o.unit is not None and _UNIT_BASE[o.unit][0] in families
    ]


def same_value(a: tuple[str, float], b: tuple[str, float]) -> bool:
    return a[0] == b[0] and abs(a[1] - b[1]) < 0.005


def contains_term(segment: str, term: str) -> bool:
    """El terme apareix literalment (sense accents ni majuscules)."""
    needle = normalise(term).strip()
    return bool(needle) and needle in segment


def compact(text: str) -> str:
    """Per comparar formules: sense espais ni signes de multiplicar variants."""
    text = normalise(text).replace("×", "x").replace("*", "x").replace("·", "x")
    return re.sub(r"\s+", "", text)
