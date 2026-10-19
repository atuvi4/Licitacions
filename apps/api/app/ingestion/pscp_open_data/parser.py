"""Lectura exacta dels valors que publica el dataset `ybgg-dgi6`.

Res d'aqui no interpreta ni recalcula un import: llegeix el text tal com es publica i
diu si es representable sense alterar-lo (mai mes de 2 decimals, mai negatiu, mai una
notacio que no sigui un decimal pla). La llicencia no permet alterar el contingut, i
`Numeric(18, 2)` arrodoniria sense avisar.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

#: Un decimal pla tal com el publica SODA ("12190.5", "298628", "1600257.50").
_PLAIN_DECIMAL = re.compile(r"^(\d+)(?:\.(\d+))?$")
_PUBLICATION_URL = re.compile(
    r"/detall-publicacio/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12})/(\d+)/?$"
)
_ASCII_DIGITS = re.compile(r"[0-9]+")
#: Un `id_intern` que es pot desar com a clau (`source_row_id`, 200) i demanar en una
#: `evidence_url` curta: UUID + un sufix de caracters segurs.
KEYABLE_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z_.\-]{0,199}$")
MAX_LOT_NUMBER_LENGTH = 100
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

#: Camps del dataset que fa servir l'adaptador (`$select` minim: cap adjudicatari).
SELECT_FIELDS = (
    ":id",
    ":updated_at",
    "id_intern",
    "codi_organ",
    "fase_publicacio",
    "numero_lot",
    "pressupost_licitacio_amb_1",
    "pressupost_licitacio_sense_1",
    "valor_estimat_expedient",
    "pressupost_licitacio_amb",
    "pressupost_licitacio_sense",
    "valor_estimat_contracte",
    "data_publicacio_futura",
    "data_publicacio_consulta",
    "data_publicacio_previ",
    "data_publicacio_anunci",
    "data_publicacio_avaluacio",
    "data_publicacio_adjudicacio",
    "data_publicacio_formalitzacio",
    "data_publicacio_anul",
    "enllac_publicacio",
    "url_json_futura",
    "url_json_cpm",
    "url_json_previ",
    "url_json_licitacio",
    "url_json_avaluacio",
    "url_json_adjudicacio",
    "url_json_formalitzacio",
    "url_json_anulacio",
)
URL_JSON_FIELDS = tuple(field for field in SELECT_FIELDS if field.startswith("url_json_"))
PUBLICATION_DATE_FIELDS = tuple(f for f in SELECT_FIELDS if f.startswith("data_publicacio_"))
#: Les dates que la decisio del propietari fa servir per demostrar una substitucio.
SUPERSESSION_DATE_FIELDS = (
    "data_publicacio_anunci",
    "data_publicacio_avaluacio",
    "data_publicacio_adjudicacio",
)

#: Camps d'import: el d'expedient es repeteix a cada fila (tambe a les de lot).
EXPEDIENT_WITH_TAX = "pressupost_licitacio_amb_1"
EXPEDIENT_WITHOUT_TAX = "pressupost_licitacio_sense_1"
EXPEDIENT_ESTIMATED = "valor_estimat_expedient"
LOT_WITH_TAX = "pressupost_licitacio_amb"
LOT_WITHOUT_TAX = "pressupost_licitacio_sense"
LOT_ESTIMATED = "valor_estimat_contracte"

#: Ordre explicit de les fases de publicacio (`fase_publicacio`). Una fase que no hi es
#: no te ordre: una substitucio que en depengui no es concloent.
PHASE_ORDER: dict[str, int] = {
    "Alerta futura": 0,
    "Consulta preliminar del mercat": 1,
    "Anunci previ": 2,
    "Anunci de licitaci\u00f3": 3,
    "Expedient en avaluaci\u00f3": 4,
    "Adjudicaci\u00f3": 5,
    "Formalitzaci\u00f3": 6,
    "Execuci\u00f3": 7,
    "Anul\u00b7laci\u00f3": 8,
}


@dataclass(frozen=True)
class ParsedAmount:
    """Un import tal com es publica: el text, el valor exacte i, si cal, el problema."""

    text: str | None
    value: Decimal | None
    #: None si es representable (o no es publica); si no, per que es refusa.
    problem: str | None = None

    @property
    def published(self) -> bool:
        return self.text is not None

    @property
    def usable(self) -> bool:
        return self.problem is None and self.value is not None


def parse_amount(raw: Any) -> ParsedAmount:
    """Llegeix un import publicat sense alterar-lo.

    Buit o absent -> no publicat (`value` None, sense problema). Un decimal pla de com a
    molt 2 decimals -> el seu valor exacte. Qualsevol altra cosa (negatiu, exponent, mes
    de 2 decimals, un tipus inesperat) -> `problem`, i el valor no s'accepta mai.
    """
    if raw is None:
        return ParsedAmount(None, None)
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return ParsedAmount(repr(raw), None, "unexpected_type")
    text = str(raw)
    stripped = text.strip()
    if stripped == "":
        return ParsedAmount(None, None)
    match = _PLAIN_DECIMAL.match(stripped)
    if match is None:
        if stripped.startswith("-") and _PLAIN_DECIMAL.match(stripped[1:]):
            return ParsedAmount(text, None, "negative")
        return ParsedAmount(text, None, "not_a_plain_decimal")
    decimals = match.group(2) or ""
    if len(decimals) > 2:
        return ParsedAmount(text, None, "more_than_2_decimals")
    return ParsedAmount(text, Decimal(stripped))


def cents(value: Decimal | None) -> str | None:
    """Text estable d'un import al centim (per a la validacio, mai per a l'import desat)."""
    if value is None:
        return None
    return str(value.quantize(Decimal("0.01")))


def normalize_lot_number(raw: Any) -> str | None:
    """`numero_lot` com a text comparable amb `tender_lots.lot_number` ("01" -> "1")."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    # Nomes digits ASCII: `"\u00b2".isdigit()` es cert pero `int()` el refusa (m-1).
    return str(int(text)) if _ASCII_DIGITS.fullmatch(text) else text


def link_url(value: Any) -> str | None:
    """Els camps d'enllac de SODA son `{"url": ...}`."""
    if isinstance(value, dict):
        url = value.get("url")
        return url if isinstance(url, str) and url else None
    return value if isinstance(value, str) and value else None


def publication_id(row: dict[str, Any]) -> int | None:
    """L'identificador numeric de publicacio d'`enllac_publicacio`
    (`.../detall-publicacio/<UUID>/<id>`), nomes si l'UUID es el de la fila."""
    url = link_url(row.get("enllac_publicacio"))
    if not url:
        return None
    match = _PUBLICATION_URL.search(url)
    if match is None:
        return None
    if match.group(1).lower() != str(row.get("id_intern", ""))[:36].lower():
        return None
    return int(match.group(2))


def phase_rank(phase: Any) -> int | None:
    return PHASE_ORDER.get(phase) if isinstance(phase, str) else None


def floating_date(raw: Any) -> datetime | None:
    """Una data de publicacio de SODA (`2026-07-29T12:50:00.000`, sense zona).

    Una data amb zona (o qualsevol altra forma) no es comparable amb les flotants: es
    torna None, i qui la necessita la tracta com a no llegible (m-1).
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return None if moment.tzinfo is not None else moment


def unreadable_publication_dates(row: dict[str, Any]) -> list[str]:
    """Els camps de data de publicacio presents que no es poden llegir."""
    return [
        name
        for name in PUBLICATION_DATE_FIELDS
        if row.get(name) not in (None, "") and floating_date(row.get(name)) is None
    ]


def system_timestamp(raw: Any) -> datetime | None:
    """Un camp de sistema de SODA (`:updated_at`, `2026-09-03T08:00:14.874Z`), en UTC."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def latest_publication_date(row: dict[str, Any]) -> datetime | None:
    dates = [floating_date(row.get(field)) for field in PUBLICATION_DATE_FIELDS]
    present = [moment for moment in dates if moment is not None]
    return max(present) if present else None


def rows_updated_at_iso(epoch: Any) -> str | None:
    """`rowsUpdatedAt` de les metadades (segons Unix) en ISO 8601 UTC."""
    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
        return None
    return datetime.fromtimestamp(int(epoch), UTC).isoformat()
