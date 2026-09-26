"""Trobar un valor dins d'una cita, de manera determinista. Sense LLM ni fuzzy.

Nomes respon preguntes literals: "hi ha un 600 seguit de Mbps en aquest tros
de text?", "hi apareix el terme GPON?". Mai "vol dir el mateix?". Si una
pregunta no es pot respondre literalment, qui crida ha de tractar el claim com a
no verificat.

Formats del plec que es reconeixen:

* numeros catalans/espanyols: `155.239,16`, `500.000`, `72.444,94`, `10,5`;
* imports amb `€`, `EUR`, `euros` (davant o darrere);
* percentatges amb `%`, `per cent`, `por ciento`;
* durades en segons/minuts/hores/dies/setmanes/mesos/anys (ca/es), tambe `tres anys`;
* amples de banda en kbps/Mbps/Gbps (`Mb/s`, `Mbit/s`);
* v3.1: magnituds fisiques i de dades amb unitat tancada (W, g, m, dBA, GB, ").

Les conversions son nomes les exactes (1 any = 12 mesos, 1 Gbps = 1000 Mbps,
1 m = 1000 mm). Mai entre families.

v3.1 (replay pagat de Palamos). Tres coses que feien fallar suports legitims, i
com es resolen sense tornar-se fuzzy:

* **Format.** Un salt de linia, espais repetits, NBSP o cometes tipografiques
  dins d'una ancora ja no la fan "absent": `flatten` ho redueix tot a un espai.
* **Files partides pel PDF.** Una linia que acaba en xifra continua a la
  seguent si aquesta comenca en minuscula ("... mes de 500" / "entrades").
* **Digits d'etiqueta.** Els numeros de "3G/4G", "800/900", "802.3af", "8.1.4"
  o de la propia ancora ("Adrecament IP (1 adreca) 26") no son valors rivals.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.ai.schemas_v3 import ClaimKind, ClaimUnit

_SPACES = {" ": " ", " ": " ", " ": " ", " ": " "}
_QUOTES = {
    "’": "'", "‘": "'", "´": "'",
    "“": '"', "”": '"', "„": '"', "″": '"',
}


def normalise(text: str) -> str:
    """Minuscules, sense accents, espais col·lapsats. Conserva digits, signes i salts."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("­", "")  # guionet tou del PDF
    for source, target in {**_SPACES, **_QUOTES}.items():
        text = text.replace(source, target)
    return re.sub(r"[ \t\r\f\v]+", " ", text.lower())


def flatten(text: str) -> str:
    """`normalise` i tots els espais (tambe salts de linia) reduits a un de sol."""
    return re.sub(r"\s+", " ", normalise(text)).strip()


#: On es talla una fila en trossos independents. `, ` i no `,`: la coma
#: decimal (`155.239,16`) no porta mai espai darrere.
_SEGMENT_SPLIT = re.compile(r";|•|\||, | - ")
#: Una linia que acaba aixi pot tancar una fila o una frase.
_ROW_END = re.compile(r"[\d€%.:;\"”″]\s*$")


def rows(text: str) -> list[str]:
    """Les files de la cita, aplanades i normalitzades.

    Un salt de linia nomes tanca una fila si la linia acaba en xifra, `€`, `%`,
    cometa de polzada o puntuacio final, I la seguent NO comenca en minuscula.
    El PDF talla files de taula ("... pels equipaments" / "municipals* 26") i
    frases ("... amb mes de 500" / "entrades"). Ajuntar linies mai no fa sostenir
    res per casualitat: un tros amb dos valors rivals continua sent ambigu.
    """
    lines = [line.strip() for line in (text or "").replace("\r", "").split("\n")]
    out: list[str] = []
    pending: list[str] = []
    for position, line in enumerate(lines):
        if not line:
            if pending:
                out.append(" ".join(pending))
                pending = []
            continue
        pending.append(line)
        following = next((later for later in lines[position + 1 :] if later), None)
        continues = following is not None and following[:1].islower()
        if following is None or (_ROW_END.search(line) and not continues):
            out.append(" ".join(pending))
            pending = []
    if pending:
        out.append(" ".join(pending))
    return [flat for flat in (flatten(row) for row in out) if flat]


def segments(text: str) -> list[str]:
    """Els trossos d'una cita que es poden llegir com una sola afirmacio."""
    return [
        part.strip()
        for row in rows(text)
        for part in _SEGMENT_SPLIT.split(row)
        if part.strip()
    ]


_NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?)(?!\d)")


def raw_numbers(text: str) -> set[float]:
    """Tots els numeros de la cita, tambe els d'etiqueta. Nomes per dir "hi es"."""
    values = (parse_number(match.group(1)) for match in _NUMBER.finditer(flatten(text)))
    return {value for value in values if value is not None}


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
    ClaimUnit.SECONDS: ("time_s", 1.0),
    ClaimUnit.MINUTES: ("time_s", 60.0),
    ClaimUnit.HOURS: ("time_s", 3600.0),
    ClaimUnit.DAYS: ("time_s", 86400.0),
    ClaimUnit.WEEKS: ("time_s", 604800.0),
    ClaimUnit.MONTHS: ("time_m", 1.0),
    ClaimUnit.YEARS: ("time_m", 12.0),
    ClaimUnit.KBPS: ("bw", 1.0),
    ClaimUnit.MBPS: ("bw", 1000.0),
    ClaimUnit.GBPS: ("bw", 1_000_000.0),
    ClaimUnit.W: ("power", 1.0),
    ClaimUnit.KW: ("power", 1000.0),
    ClaimUnit.G: ("mass", 1.0),
    ClaimUnit.KG: ("mass", 1000.0),
    ClaimUnit.MM: ("length", 1.0),
    ClaimUnit.CM: ("length", 10.0),
    ClaimUnit.M: ("length", 1000.0),
    # dBA i dB NO son la mateixa magnitud (ponderacio A): families separades.
    ClaimUnit.DBA: ("sound_dba", 1.0),
    ClaimUnit.DB: ("sound_db", 1.0),
    ClaimUnit.MB: ("data", 1.0),
    ClaimUnit.GB: ("data", 1000.0),
    ClaimUnit.TB: ("data", 1_000_000.0),
    ClaimUnit.INCH: ("inch", 1.0),
}

# Els patrons de darrere del numero, ja normalitzats (sense accents). L'ORDRE
# importa: primer els mes llargs ("mbps" abans que "mb", "mesos" abans que "m").
_UNIT_AFTER: list[tuple[ClaimUnit, re.Pattern[str]]] = [
    (ClaimUnit.EUR, re.compile(r"\s?(?:€|eur\b|euros?\b)")),
    (ClaimUnit.PERCENT, re.compile(r"\s?(?:%|per ?cent\b|por ?ciento\b|percent\b)")),
    (ClaimUnit.KBPS, re.compile(r"\s?(?:kbps|kb/s|kbit/s|kbits?)\b")),
    (ClaimUnit.MBPS, re.compile(r"\s?(?:mbps|mb/s|mbit/s|mbits?|megas)\b")),
    (ClaimUnit.GBPS, re.compile(r"\s?(?:gbps|gb/s|gbit/s|gbits?)\b")),
    (ClaimUnit.MB, re.compile(r"\s?(?:mb|megabytes?)\b")),
    (ClaimUnit.GB, re.compile(r"\s?(?:gb|gigabytes?|gigues|gigas)\b")),
    (ClaimUnit.TB, re.compile(r"\s?(?:tb|terabytes?)\b")),
    (ClaimUnit.SECONDS, re.compile(r"\s?(?:segons|segon|segundos|segundo|seg)\b")),
    (ClaimUnit.MINUTES, re.compile(r"\s?(?:minuts|minut|minutos|minuto|min)\b")),
    (ClaimUnit.HOURS, re.compile(r"\s?(?:hores|hora|horas|h)\b")),
    (ClaimUnit.DAYS, re.compile(r"\s?(?:dies|dia|dias)\b")),
    (ClaimUnit.WEEKS, re.compile(r"\s?(?:setmanes|setmana|semanas|semana)\b")),
    (ClaimUnit.MONTHS, re.compile(r"\s?(?:mesos|mes|meses)\b")),
    (ClaimUnit.YEARS, re.compile(r"\s?(?:anys|any|anos|ano|anualitats|anualidades)\b")),
    (ClaimUnit.KW, re.compile(r"\s?(?:kw|quilowatts?|kilovatios?)\b")),
    (ClaimUnit.W, re.compile(r"\s?(?:w|watts?|vats?|vatios?)\b")),
    (ClaimUnit.KG, re.compile(r"\s?(?:kg|quilograms?|kilogramos?)\b")),
    (ClaimUnit.G, re.compile(r"\s?(?:g|grams?|gramos?)\b")),
    (ClaimUnit.MM, re.compile(r"\s?(?:mm|mil·limetres?|milimetros?)\b")),
    (ClaimUnit.CM, re.compile(r"\s?(?:cm|centimetres?|centimetros?)\b")),
    (ClaimUnit.M, re.compile(r"\s?(?:m|metres?|metros?)\b")),
    (ClaimUnit.DBA, re.compile(r"\s?(?:dba|db\(a\))\b")),
    (ClaimUnit.DB, re.compile(r"\s?db\b")),
    (ClaimUnit.INCH, re.compile(r"\s?(?:\"|''|polzades?|pulgadas?|inch(?:es)?)")),
]
_EUR_BEFORE = re.compile(r"(?:€|eur)\s?$")

_KIND_FAMILIES: dict[ClaimKind, frozenset[str]] = {
    ClaimKind.AMOUNT: frozenset({"eur"}),
    ClaimKind.PERCENTAGE: frozenset({"percent"}),
    ClaimKind.DURATION: frozenset({"time_s", "time_m"}),
    ClaimKind.BANDWIDTH: frozenset({"bw"}),
    ClaimKind.QUANTITY: frozenset(
        {"power", "mass", "length", "sound_dba", "sound_db", "data", "inch"}
    ),
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

#: Tecnologies de xarxa mobil: "3G", "4G", "5G". No son grams.
_MOBILE_GENERATION = re.compile(r"g(?![\w/])|g/")
#: Una "paraula" amb dos grups de xifres units per `/`: "800/900", "3g/4g".
_SLASH_TOKEN = re.compile(r"\d[\w.,]*/[\w.,]*\d")
#: Numeracio de seccio o d'enumeracio al principi d'un tros: "8.1.4", "21.1", "7.", "2)".
_SECTION_NUMBER = re.compile(r"^(?:\d+(?:\.\d{1,2})+\.?|\d+[.)])$")


@dataclass(frozen=True, slots=True)
class Occurrence:
    """Un numero trobat, amb la unitat que l'acompanya (o cap)."""

    value: float
    unit: ClaimUnit | None


def _token_at(text: str, start: int, end: int) -> str:
    left = text.rfind(" ", 0, start) + 1
    right = text.find(" ", end)
    return text[left : right if right != -1 else len(text)]


def _is_label(text: str, start: int, end: int, unit: ClaimUnit | None) -> bool:
    """Digits que formen part d'una etiqueta, no d'un valor."""
    token = _token_at(text, start, end)
    if _SLASH_TOKEN.search(token):
        return True  # 800/900, 3g/4g, 100/1000
    if unit is None and end < len(text) and text[end].isalpha():
        return True  # 802.3af, 4k, g729 (sense unitat reconeguda enganxada)
    if unit == ClaimUnit.G and text[start:end].isdigit() and 2 <= int(text[start:end]) <= 5:
        after = text[end:]
        if _MOBILE_GENERATION.match(after):
            return True  # 3G, 4G, 5G: tecnologia, no grams
    stripped_left = text[:start].rstrip()
    first_token = not stripped_left or stripped_left.endswith((":", ";", "|"))
    return bool(first_token and _SECTION_NUMBER.match(token))


def occurrences(segment: str) -> list[Occurrence]:
    """Els numeros d'un tros ja normalitzat que poden ser VALORS, amb la seva unitat."""
    found: list[Occurrence] = []
    for match in _NUMBER.finditer(segment):
        value = parse_number(match.group(1))
        if value is None:
            continue
        unit = _unit_at(segment, match.start(), match.end())
        if _is_label(segment, match.start(), match.end(), unit):
            continue
        found.append(Occurrence(value, unit))
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


def contains_term(haystack: str, term: str) -> bool:
    """El terme apareix literalment, llevat de format (accents, majuscules, espais,
    salts de linia, NBSP, cometes tipografiques). Mai per semblanca."""
    needle = flatten(term)
    return bool(needle) and needle in flatten(haystack)


@dataclass(frozen=True, slots=True)
class AnchorWindow:
    """On apareix l'ancora dins d'una fila, i el tros que l'envolta."""

    #: Els numeros del tros FORA de l'ancora: els rivals possibles.
    outside: list[Occurrence]
    #: Els numeros DINS de l'ancora ("mes de 500 entrades").
    inside: list[Occurrence]


def anchor_windows(quote: str, anchor: str) -> list[AnchorWindow]:
    """Cada aparicio de l'ancora, amb el tros de fila que l'envolta.

    El tros va del separador anterior a l'ancora (`;`, `, `, `|`, ` - `) fins al
    seguent, sense tallar mai l'ancora. Els numeros de dins de l'ancora no son
    rivals del valor: son part del que l'ancora anomena ("Adrecament IP (1
    adreca)"). Si l'ancora no cap en cap fila (l'ha partida el PDF), es busca a
    la cita sencera aplanada, amb la mateixa regla de tros.
    """
    needle = flatten(anchor)
    if not needle:
        return []
    candidates = rows(quote)
    if not any(needle in row for row in candidates):
        candidates = [flatten(quote)]
    windows: list[AnchorWindow] = []
    for row in candidates:
        position = row.find(needle)
        while position != -1:
            end = position + len(needle)
            left = 0
            for match in _SEGMENT_SPLIT.finditer(row, 0, position):
                left = match.end()
            following = _SEGMENT_SPLIT.search(row, end)
            right = following.start() if following else len(row)
            outside_text = f"{row[left:position]} ‖ {row[end:right]}"
            windows.append(AnchorWindow(
                outside=occurrences(outside_text),
                inside=occurrences(row[position:end]),
            ))
            position = row.find(needle, position + 1)
    return windows


def compact(text: str) -> str:
    """Per comparar formules: sense espais ni signes de multiplicar variants."""
    text = normalise(text).replace("×", "x").replace("*", "x").replace("·", "x")
    return re.sub(r"\s+", "", text)
