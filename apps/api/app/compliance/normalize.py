"""Normalitzador de LECTURA per als valors en format lliure (v1 i product-v2).

El perfil `product-v2` (com la v1) no produeix valors tipats: escriu el que diu el
plec tal com el diu, `{"amount": "91.904,74 €", "period": "millor dels 3 últims
exercicis"}`. Les regles de 4C no ho saben llegir (`to_decimal("91.904,74")` no
es cap nombre, i `to_decimal("300.000")` seria 300, no 300.000), i el requisit
acaba en `REQUIREMENT_VALUE_MISSING` encara que el plec digui la xifra ben clara.

Aquest modul tradueix, en el moment de llegir i SENSE escriure res, aquella forma
lliure a l'estructura v2 (`app.ai.value_schemas`) que `specs` ja sap llegir. Res
del que fa no es desa: `requirements.value_json` queda com el va escriure
l'extractor, i el valor normalitzat viatja nomes dins de l'avaluacio
(`explanation_params.normalization`), amb la procedencia de cada camp.

Tres regles que el governen tot:

* **Ordre de fonts.** Primer el `value_json` del requisit. Si no hi es o no te el
  camp, el text LITERAL de les cites verificades del mateix requisit (la `quote`
  de la seva evidencia, que la canonada ja ha localitzat caracter a caracter dins
  de la pagina), i nomes si n'hi ha **un sol** valor inequivoc del tipus que la
  regla necessita. El titol i la descripcio (text redactat pel model) NO son mai
  font d'un valor; nomes poden fer el resultat mes prudent (p. ex. marcar que el
  plec demana treballs «similars», cosa que porta a revisio).
* **Ambiguitat = no es normalitza.** Mes d'un import possible, unitats
  contradictories, un format numeric que admet dues lectures: el requisit queda
  com ara i el motiu queda escrit (`reason`).
* **Mai no s'inventa res.** Ni la base d'un percentatge, ni el nivell d'una
  certificacio, ni un recompte de persones a partir d'una llista de perfils.

Modul pur: ni base de dades ni model.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.ai.value_schemas import VALUE_SCHEMA_VERSION
from app.models.enums import (
    AggregationType,
    EconomicMetric,
    InsuranceLimitKind,
    InsuranceType,
    PercentageBase,
    RequirementCategory,
    RequirementObligation,
)

#: Versio del normalitzador. Va a cada `explanation_params.normalization`: un
#: canvi de criteri ha de ser visible a l'avaluacio que el va fer servir.
NORMALIZER_VERSION = "free-format-normalizer-v1"

#: Perfils amb valor lliure. Els altres (v2 tipat, v3 de claims) no es toquen.
FREE_FORMAT_PROFILES = frozenset({"v1", "product-v2"})

#: Categories el valor de les quals llegeix alguna regla. La resta no s'avalua
#: comparant dades i no se'n normalitza res.
_VALUE_CATEGORIES = frozenset(
    {
        RequirementCategory.ECONOMIC_SOLVENCY,
        RequirementCategory.INSURANCE,
        RequirementCategory.EXPERIENCE,
        RequirementCategory.TECHNICAL_SOLVENCY,
        RequirementCategory.PERSONNEL,
        RequirementCategory.CERTIFICATION,
        RequirementCategory.REGISTRATION,
    }
)

#: Categories on un requisit que NOMENA l'ENS es llegeix com a exigencia de
#: certificacio (brief pas 1, §1.3). Les tecniques i contractuals hi son perque
#: a L'Escala l'ENS nomes hi apareix aixi; cap altra categoria es recategoritza.
_ENS_CATEGORIES = frozenset(
    {
        RequirementCategory.CERTIFICATION,
        RequirementCategory.TECHNICAL_REQUIREMENT,
        RequirementCategory.CONTRACT_CONDITION,
    }
)


# --------------------------------------------------------------------------- #
# Resultat                                                                      #
# --------------------------------------------------------------------------- #

#: Codis de `reason`. Tancats: la interficie i l'informe en deriven el text.
REASON_NO_SOURCE = "no_value_in_value_json_or_quotes"
REASON_AMOUNT_AMBIGUOUS = "amount_ambiguous"
REASON_AMOUNT_UNPARSEABLE = "amount_unparseable"
REASON_CURRENCY_CONFLICT = "currency_conflict"
REASON_PERIOD_AMBIGUOUS = "period_ambiguous"
REASON_METRIC_NOT_STATED = "metric_not_stated"
REASON_LIMIT_KIND_AMBIGUOUS = "limit_kind_ambiguous"
REASON_EXPERIENCE_BASIS_AMBIGUOUS = "experience_amount_basis_ambiguous"
REASON_NO_COUNT = "no_count_stated"
REASON_CERTIFICATION_NOT_NAMED = "certification_not_named"
REASON_REGISTRY_NOT_NAMED = "registry_not_named_explicitly"
REASON_NOT_MANDATORY = "not_mandatory"
REASON_ENS_LEVEL_AMBIGUOUS = "ens_level_ambiguous"

#: Rutes: el requisit es llegeix amb una regla diferent de la de la seva categoria
#: o es resol directament amb revisio (sense reescriure la categoria).
ROUTE_ENS_CERTIFICATION = "ens_certification"
ROUTE_ENS_LEVEL_NOT_STATED = "ens_level_not_stated"
ROUTE_REGISTRY_DOCUMENTATION_ONLY = "registry_documentation_only"
ROUTE_SECTOR_OPERATOR_REGISTRY = "sector_operator_registry"


@dataclass(frozen=True, slots=True)
class Provenance:
    """D'on surt un camp normalitzat."""

    field: str
    #: `value_json` o `quote` (text literal d'una cita verificada); `title` /
    #: `description` nomes per a marques que fan el resultat mes prudent.
    source: str
    raw: str
    evidence_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = {"field": self.field, "source": self.source, "raw": self.raw[:200]}
        if self.evidence_id:
            data["evidence_id"] = self.evidence_id
        return data


@dataclass(frozen=True, slots=True)
class Normalization:
    """El que el normalitzador ha pogut llegir d'un requisit, i per que no."""

    #: `normalized` (hi ha valor v2), `not_normalized` (s'avalua com ara, amb el
    #: motiu) o `routed` (una ruta explicita, amb o sense valor).
    status: str
    #: Valor amb la forma de `app.ai.value_schemas` (v2). `None` si no n'hi ha.
    value_json: dict[str, Any] | None = None
    provenance: tuple[Provenance, ...] = ()
    reason: str | None = None
    route: str | None = None
    #: Categoria amb que s'ha de llegir, si no es la del requisit (ruta ENS).
    read_as: RequirementCategory | None = None
    #: Detall per a l'explicacio (p. ex. l'esquema nomenat).
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def has_value(self) -> bool:
        return self.value_json is not None

    def explain(self, profile: str | None) -> dict[str, Any]:
        """El que es guarda a `explanation_params.normalization`."""
        data: dict[str, Any] = {
            "version": NORMALIZER_VERSION,
            "profile": profile,
            "status": self.status,
        }
        if self.value_json is not None:
            data["value"] = self.value_json
        if self.provenance:
            data["provenance"] = [item.as_dict() for item in self.provenance]
        if self.reason:
            data["reason"] = self.reason
        if self.route:
            data["route"] = self.route
        if self.detail:
            data["detail"] = self.detail
        return data

    def consolidation_value(self) -> dict[str, Any] | None:
        """Les dades DECISIVES de l'exigencia, per agrupar duplicats.

        Nomes el que decideix el veredicte: import, moneda, unitat del limit,
        periode i agregacio. Un percentatge informatiu al costat d'un import (el
        plec ja ha fet el calcul) i les marques de prudencia (similitud) no hi
        entren: dues cites del mateix llindar no deixen de ser la mateixa
        exigencia perque una digui tambe «el 70%».
        """
        value = self.value_json
        if value is None:
            return None
        kind = value.get("kind")
        if kind == "economic_solvency":
            threshold = value.get("threshold") or {}
            period = value.get("reference_period") or {}
            return {
                "kind": kind,
                "metric": value.get("metric"),
                "operator": value.get("operator"),
                "amount": threshold.get("amount"),
                "currency": threshold.get("currency"),
                "percentage": None if threshold.get("amount") else threshold.get("percentage"),
                "years": period.get("years"),
                "aggregation": value.get("aggregation"),
            }
        if kind == "insurance":
            return {
                "kind": kind,
                "insurance_type": value.get("insurance_type"),
                "limits": sorted(
                    (limit["kind"], limit["amount"], limit.get("currency"))
                    for limit in value.get("limits") or []
                ),
            }
        if kind == "experience":
            period = value.get("reference_period") or {}

            def amount(raw: dict[str, Any] | None) -> Any:
                if not raw:
                    return None
                return [raw.get("amount"), raw.get("currency")]

            return {
                "kind": kind,
                "years": period.get("years"),
                "min_count": value.get("min_count"),
                "individual": amount(value.get("min_individual_amount")),
                "accumulated": amount(value.get("min_accumulated_amount")),
                "aggregation": value.get("aggregation"),
            }
        if kind == "certification":
            return {"kind": kind, "name": (value.get("name") or "").casefold()}
        return None


# --------------------------------------------------------------------------- #
# Lectura de text                                                               #
# --------------------------------------------------------------------------- #


def _flat(text: str) -> str:
    """Espais col·lapsats, apostrofs tipografics unificats. Els accents es queden."""
    text = unicodedata.normalize("NFC", text).replace("’", "'").replace("´", "'")
    return " ".join(text.split())


def _fold(text: str) -> str:
    """Per comparar paraules: sense accents i en minuscules."""
    decomposed = unicodedata.normalize("NFD", _flat(text))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").casefold()


#: Un import en format local (punt de milers, coma decimal) o un enter.
_LOCAL_NUMBER = r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+|\d+"
_LOCAL_NUMBER_FULL = re.compile(rf"^(?:{_LOCAL_NUMBER})$")
#: Marca de moneda. Nomes l'euro: cap altra moneda apareix als plecs del pilot, i
#: una altra no es convertiria igualment.
_CURRENCY_TOKEN = re.compile(r"(?:€|\beur\b|\beuros?\b)", re.I)
#: Un import seguit d'una marca d'euro, dins d'una cita.
_AMOUNT_IN_TEXT = re.compile(rf"(?<![\d.,])({_LOCAL_NUMBER})\s*(?:€|euros?\b|EUR\b)", re.I)


def _local_decimal(token: str) -> Decimal | None:
    if not _LOCAL_NUMBER_FULL.match(token):
        return None
    try:
        return Decimal(token.replace(".", "").replace(",", "."))
    except InvalidOperation:  # pragma: no cover - el patro ja ho impedeix
        return None


def parse_amount(raw: Any) -> tuple[Decimal | None, str | None, str | None]:
    """`(import, moneda, motiu)` d'un valor lliure.

    Accepta un nombre JSON, `"91.904,74"`, `"91.904,74 €"`, `"300.000 €"`,
    `"42.888,88 euros"`. Un text amb paraules (`"1,5 vegades l'anualitat"`) no es
    cap import. Un `"1.5"` o `"38326.08"` (punt decimal anglosaxo) tampoc: en
    format local el punt es de milers i admetre totes dues lectures seria
    triar-ne una a l'atzar.
    """
    if raw is None or isinstance(raw, bool):
        return None, None, REASON_NO_SOURCE
    if isinstance(raw, int | float | Decimal):
        try:
            return Decimal(str(raw)), None, None
        except InvalidOperation:
            return None, None, REASON_AMOUNT_UNPARSEABLE
    if not isinstance(raw, str):
        return None, None, REASON_AMOUNT_UNPARSEABLE
    text = _flat(raw)
    currency = "EUR" if _CURRENCY_TOKEN.search(text) else None
    bare = _CURRENCY_TOKEN.sub(" ", text).strip()
    amount = _local_decimal(bare)
    if amount is None:
        return None, currency, REASON_AMOUNT_UNPARSEABLE
    return amount, currency, None


def _currency_field(raw: Any) -> tuple[str | None, bool]:
    """La moneda d'un camp `currency` lliure. `(moneda, reconeguda)`."""
    if raw is None:
        return None, True
    text = _flat(str(raw))
    if text.upper() == "EUR" or _CURRENCY_TOKEN.fullmatch(text.strip()):
        return "EUR", True
    return text.upper()[:3] or None, False


def amounts_in_text(text: str) -> list[Decimal]:
    """Els imports amb marca d'euro d'un text literal, en ordre."""
    found = []
    for match in _AMOUNT_IN_TEXT.finditer(_flat(text)):
        amount = _local_decimal(match.group(1))
        if amount is not None:
            found.append(amount)
    return found


def _number_word(token: str) -> int | None:
    words = {
        "un": 1, "una": 1, "dos": 2, "dues": 2, "tres": 3, "quatre": 4,
        "cinc": 5, "uno": 1, "cuatro": 4, "cinco": 5,
    }  # fmt: skip
    if token.isdigit():
        return int(token)
    return words.get(token)


_N = r"(\d+|un|una|dos|dues|tres|quatre|cinc|uno|cuatro|cinco)(?:\s*\(\s*(\d+)\s*\))?"
_LAST = r"(?:darrers|ultims|ultimes|ultimos|ultimas)"
_YEARS_PATTERNS = (
    # `(?!\w)` i no `\b`: amb `\b` el «(2)» de «tres (2)» es podria descartar
    # en silenci i la contradiccio no es veuria.
    re.compile(rf"\b{_LAST}\s+{_N}(?!\w)"),
    re.compile(rf"\b{_N}\s+{_LAST}\b"),
)
_MAX_HINT = re.compile(r"\bmillor\b|\bmajor\b|\bmejor\b|\bmayor\b|\bmes alt")
_EACH_HINT = re.compile(
    r"\bcadascun\b|\bcada un\b|\bcada any\b|\bcada exercici\b|\bcada ejercicio\b"
)


def parse_years(text: str) -> tuple[int | None, bool]:
    """Quants anys mira un periode. `(anys, ambigu)`.

    «dels 3 últims», «darrers tres (3) anys», «tres últims». Un «tres (3)» ha de
    dir el mateix les dues vegades; dos recomptes diferents al mateix text son
    ambiguitat, no una tria.
    """
    folded = _fold(text)
    values: set[int] = set()
    for pattern in _YEARS_PATTERNS:
        for match in pattern.finditer(folded):
            first = _number_word(match.group(1))
            second = int(match.group(2)) if match.group(2) else None
            if first is None or (second is not None and second != first):
                return None, True
            values.add(first)
    if len(values) > 1:
        return None, True
    return (values.pop() if values else None), False


def parse_aggregation(text: str) -> tuple[AggregationType, bool]:
    """Com es combinen els anys. `(agregacio, ambigua)`.

    Nomes dues lectures inequivoques: el millor/major any (`max`) i cadascun dels
    anys (`each_year`). La mitjana NO es dedueix: «l'anualitat mitjana» del
    contracte no te res a veure amb la mitjana dels exercicis de l'empresa.
    """
    folded = _fold(text)
    is_max = bool(_MAX_HINT.search(folded))
    is_each = bool(_EACH_HINT.search(folded))
    if is_max and is_each:
        return AggregationType.UNKNOWN, True
    if is_max:
        return AggregationType.MAX, False
    if is_each:
        return AggregationType.EACH_YEAR, False
    return AggregationType.UNKNOWN, False


def parse_percentage(raw: Any) -> Decimal | None:
    """`"70%"`, `"5"`, `70` -> Decimal. Res mes."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float | Decimal):
        return Decimal(str(raw))
    text = _flat(str(raw)).replace("%", "").replace("per cent", "").strip()
    if not re.fullmatch(r"\d+(?:,\d+)?", text):
        return None
    return Decimal(text.replace(",", "."))


_MULTIPLE = re.compile(r"^(\d+(?:,\d+)?)\s*(?:vegades|veces|x)\b")


def parse_multiple(raw: Any) -> Decimal | None:
    """`"1,5 vegades l'anualitat mitjana"` -> 1.5 (un multiple, no un import)."""
    if not isinstance(raw, str):
        return None
    match = _MULTIPLE.match(_fold(raw))
    if not match:
        return None
    return Decimal(match.group(1).replace(",", "."))


def percentage_base(text: str) -> PercentageBase:
    folded = _fold(text)
    if "anualitat mitjana" in folded or "anualidad media" in folded:
        return PercentageBase.CONTRACT_ANNUITY
    if "pressupost base" in folded or "presupuesto base" in folded:
        return PercentageBase.BASE_BUDGET
    if "valor estimat" in folded or "valor estimado" in folded:
        return PercentageBase.ESTIMATED_VALUE
    return PercentageBase.UNKNOWN


# --------------------------------------------------------------------------- #
# Fonts del requisit                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Quote:
    evidence_id: str
    text: str


def verified_quotes(requirement: Any) -> list[_Quote]:
    """Les cites del requisit que la canonada va localitzar dins de la pagina.

    Una cita persistida ja ha passat `validate_evidence_detailed` (literal i
    continua a la pagina declarada); `char_start` es la marca d'aquesta
    localitzacio. Sense, no es font de cap valor.
    """
    quotes = []
    for evidence in getattr(requirement, "evidence", None) or []:
        if getattr(evidence, "char_start", None) is None:
            continue
        text = getattr(evidence, "quote", None)
        if text:
            quotes.append(_Quote(evidence_id=str(evidence.id), text=text))
    return quotes


def _value_texts(value: dict[str, Any] | None) -> list[tuple[str, str]]:
    """Els textos del `value_json`, amb el seu camí."""
    texts: list[tuple[str, str]] = []

    def walk(node: Any, path: str) -> None:
        if isinstance(node, str):
            texts.append((path, node))
        elif isinstance(node, dict):
            for key, child in node.items():
                walk(child, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")

    walk(value or {}, "")
    return texts


class _Sources:
    """Les fonts d'un requisit, en l'ordre que mana."""

    def __init__(self, requirement: Any) -> None:
        raw = getattr(requirement, "value_json", None)
        self.value: dict[str, Any] = raw if isinstance(raw, dict) else {}
        self.value_texts = _value_texts(self.value)
        self.quotes = verified_quotes(requirement)
        self.title = getattr(requirement, "title", None) or ""
        self.description = getattr(requirement, "description", None) or ""

    def literal_text(self) -> str:
        """Value + cites: les uniques fonts d'un VALOR."""
        return " ".join([text for _, text in self.value_texts] + [q.text for q in self.quotes])

    def find_flag(self, pattern: re.Pattern[str], field_name: str) -> Provenance | None:
        """Una marca de prudencia: value, cites, titol o descripcio, per aquest ordre."""
        for _path, text in self.value_texts:
            if pattern.search(_fold(text)):
                return Provenance(field_name, "value_json", text, None)
        for quote in self.quotes:
            if pattern.search(_fold(quote.text)):
                return Provenance(field_name, "quote", quote.text, quote.evidence_id)
        for source, text in (("title", self.title), ("description", self.description)):
            if text and pattern.search(_fold(text)):
                return Provenance(field_name, source, text, None)
        return None

    def literal_flag(self, pattern: re.Pattern[str], field_name: str) -> Provenance | None:
        """Com `find_flag`, pero nomes value i cites (text literal)."""
        for _path, text in self.value_texts:
            if pattern.search(_fold(text)):
                return Provenance(field_name, "value_json", text, None)
        for quote in self.quotes:
            if pattern.search(_fold(quote.text)):
                return Provenance(field_name, "quote", quote.text, quote.evidence_id)
        return None


_AMOUNT_KEYS = ("amount", "import", "minimum_amount", "coverage_amount", "threshold")


def _amount(
    sources: _Sources, field_name: str
) -> tuple[Decimal | None, str | None, Provenance | None, str | None]:
    """L'import exigit: del `value_json` i, si no n'hi ha, d'una cita inequivoca.

    Retorna `(import, moneda, procedencia, motiu)`.
    """
    for key in _AMOUNT_KEYS:
        if key not in sources.value or isinstance(sources.value.get(key), dict):
            continue
        raw = sources.value.get(key)
        amount, currency, reason = parse_amount(raw)
        if amount is None:
            return None, None, None, reason
        declared, known = _currency_field(sources.value.get("currency"))
        if not known:
            return None, None, None, REASON_CURRENCY_CONFLICT
        if currency and declared and currency != declared:
            return None, None, None, REASON_CURRENCY_CONFLICT
        return (
            amount,
            currency or declared or "EUR",
            Provenance(field_name, "value_json", str(raw)),
            None,
        )

    found: dict[Decimal, _Quote] = {}
    for quote in sources.quotes:
        for amount in amounts_in_text(quote.text):
            found.setdefault(amount, quote)
    if not found:
        return None, None, None, REASON_NO_SOURCE
    if len(found) > 1:
        return None, None, None, REASON_AMOUNT_AMBIGUOUS
    amount, quote = next(iter(found.items()))
    return amount, "EUR", Provenance(field_name, "quote", quote.text, quote.evidence_id), None


def _text_for(sources: _Sources, key: str) -> tuple[str | None, Provenance | None]:
    """Un camp textual del value (p. ex. `period`)."""
    raw = sources.value.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw, Provenance(key, "value_json", raw)
    return None, None


def _from_quotes(
    sources: _Sources, parse: Any, field_name: str
) -> tuple[Any, Provenance | None, bool]:
    """Un valor que nomes es pot llegir de les cites: unic, o res.

    `parse(text) -> (valor | None, ambigu)`. Cites que diuen valors diferents son
    ambiguitat; cites que diuen el mateix, una sola lectura.
    """
    values: dict[Any, _Quote] = {}
    for quote in sources.quotes:
        value, ambiguous = parse(quote.text)
        if ambiguous:
            return None, None, True
        if value is not None:
            values.setdefault(value, quote)
    if len(values) > 1:
        return None, None, True
    if not values:
        return None, None, False
    value, quote = next(iter(values.items()))
    return value, Provenance(field_name, "quote", quote.text, quote.evidence_id), False


def _period(
    sources: _Sources,
) -> tuple[int | None, AggregationType, list[Provenance], str | None]:
    """Anys i agregacio: del `period` del value i, el que hi falti, de les cites."""
    provenance: list[Provenance] = []
    years: int | None = None
    aggregation = AggregationType.UNKNOWN
    text, origin = _text_for(sources, "period")
    if text is not None:
        years, ambiguous = parse_years(text)
        if ambiguous:
            return None, AggregationType.UNKNOWN, [], REASON_PERIOD_AMBIGUOUS
        aggregation, ambiguous = parse_aggregation(text)
        if ambiguous:
            return None, AggregationType.UNKNOWN, [], REASON_PERIOD_AMBIGUOUS
        if years is not None:
            provenance.append(Provenance("reference_period.years", "value_json", text))
        if aggregation != AggregationType.UNKNOWN:
            provenance.append(Provenance("aggregation", "value_json", text))
    if years is None:
        years, origin_quote, ambiguous = _from_quotes(
            sources, parse_years, "reference_period.years"
        )
        if ambiguous:
            return None, AggregationType.UNKNOWN, [], REASON_PERIOD_AMBIGUOUS
        if origin_quote is not None:
            provenance.append(origin_quote)
    if aggregation == AggregationType.UNKNOWN:

        def parse(quote: str) -> tuple[AggregationType | None, bool]:
            value, ambiguous = parse_aggregation(quote)
            return (None if value == AggregationType.UNKNOWN else value), ambiguous

        value, origin_quote, ambiguous = _from_quotes(sources, parse, "aggregation")
        if ambiguous:
            return None, AggregationType.UNKNOWN, [], REASON_PERIOD_AMBIGUOUS
        if value is not None:
            aggregation = value
            provenance.append(origin_quote)
    return years, aggregation, provenance, None


def _percentage(sources: _Sources) -> tuple[Decimal | None, PercentageBase, Provenance | None]:
    """Un percentatge (`percent`) o un multiple (`multiple`) del value, i la seva base."""
    for key in ("percent", "percentage"):
        if key in sources.value:
            value = parse_percentage(sources.value.get(key))
            if value is not None:
                return (
                    value,
                    percentage_base(sources.literal_text()),
                    Provenance("threshold.percentage", "value_json", str(sources.value[key])),
                )
    raw = sources.value.get("multiple")
    multiple = parse_multiple(raw)
    if multiple is not None:
        return (
            multiple * Decimal(100),
            percentage_base(str(raw)),
            Provenance("threshold.percentage", "value_json", str(raw)),
        )
    return None, PercentageBase.UNKNOWN, None


def _money(amount: Decimal | None, currency: str | None) -> dict[str, Any]:
    return {"amount": None if amount is None else str(amount), "currency": currency or "EUR"}


# --------------------------------------------------------------------------- #
# Per categoria                                                                 #
# --------------------------------------------------------------------------- #

_TURNOVER = re.compile(
    r"volum anual de negoci|volum de negoci|xifra anual de negoci|xifra de negoci"
    r"|volumen anual de negocio|volumen de negocio|cifra anual de negocio"
)


def _economic(sources: _Sources, requirement: Any) -> Normalization:
    amount, currency, origin, reason = _amount(sources, "threshold.amount")
    percentage, base, percentage_origin = _percentage(sources)
    if amount is None and percentage is None:
        return Normalization(status="not_normalized", reason=reason or REASON_NO_SOURCE)
    if amount is None and reason not in (None, REASON_NO_SOURCE):
        # Hi ha un import pero no es llegible: no es substitueix per un percentatge.
        return Normalization(status="not_normalized", reason=reason)
    metric_origin = sources.literal_flag(_TURNOVER, "metric")
    if metric_origin is None:
        return Normalization(status="not_normalized", reason=REASON_METRIC_NOT_STATED)
    years, aggregation, period_origin, reason = _period(sources)
    if reason:
        return Normalization(status="not_normalized", reason=reason)
    operator = getattr(requirement, "operator", None)
    threshold = _money(amount, currency)
    if percentage is not None:
        threshold["percentage"] = str(percentage)
        threshold["percentage_base"] = base.value
    value = {
        "kind": "economic_solvency",
        "metric": EconomicMetric.ANNUAL_TURNOVER.value,
        "operator": str(operator) if operator else "gte",
        "threshold": threshold,
        "reference_period": (
            {"type": "last_n_years", "years": years} if years else {"type": "unknown"}
        ),
        "aggregation": aggregation.value,
    }
    provenance = [p for p in (origin, percentage_origin, metric_origin) if p] + period_origin
    return Normalization(status="normalized", value_json=value, provenance=tuple(provenance))


_PER_CLAIM_AND_YEAR = re.compile(r"per sinistre i (?:per )?any|por siniestro y (?:por )?ano")
_PER_CLAIM = re.compile(r"per sinistre|por siniestro")
_PER_VICTIM = re.compile(r"per victima|por victima")
_ANNUAL = re.compile(r"agregat anual|agregado anual|\bper any\b|\bpor ano\b|\banual\b")
_TOTAL = re.compile(r"limit total|limite total|\btotal\b")
_EMPLOYER = re.compile(r"patronal")
_CIVIL_LIABILITY = re.compile(r"responsabilitat civil|responsabilidad civil")


def limit_kinds(text: str) -> tuple[tuple[InsuranceLimitKind, ...] | None, bool]:
    """Quins limits nomena un text. `(tipus, ambigu)`; `None` si no en nomena cap.

    «per sinistre i any» son DOS limits (per sinistre i agregat anual) del mateix
    import: es la lectura habitual de «por siniestro y año». «per víctima» de la
    RC patronal es el sublimit patronal, diferent del general per victima.
    """
    folded = _fold(text)
    found: list[InsuranceLimitKind] = []
    if _PER_CLAIM_AND_YEAR.search(folded):
        found += [InsuranceLimitKind.PER_CLAIM, InsuranceLimitKind.ANNUAL_AGGREGATE]
    elif _PER_CLAIM.search(folded):
        found.append(InsuranceLimitKind.PER_CLAIM)
    if _PER_VICTIM.search(folded):
        found.append(
            InsuranceLimitKind.EMPLOYER_PER_VICTIM
            if _EMPLOYER.search(folded)
            else InsuranceLimitKind.PER_VICTIM
        )
    if not found and _ANNUAL.search(folded):
        found.append(InsuranceLimitKind.ANNUAL_AGGREGATE)
    if not found and _TOTAL.search(folded):
        found.append(InsuranceLimitKind.TOTAL)
    if not found:
        return None, False
    if InsuranceLimitKind.PER_CLAIM in found and len(found) > 2:
        # Un sol import i tres unitats: no es diu a quina correspon.
        return None, True
    return tuple(found), False


def _insurance(sources: _Sources) -> Normalization:
    amount, currency, origin, reason = _amount(sources, "limits.amount")
    if amount is None:
        return Normalization(status="not_normalized", reason=reason)

    provenance = [origin]
    kinds: tuple[InsuranceLimitKind, ...] | None = None
    for key in ("unit", "limit_kind", "kind", "type", "basis"):
        raw = sources.value.get(key)
        if isinstance(raw, str) and raw.strip():
            kinds, ambiguous = limit_kinds(raw)
            if ambiguous:
                return Normalization(status="not_normalized", reason=REASON_LIMIT_KIND_AMBIGUOUS)
            if kinds is not None:
                provenance.append(Provenance("limits.kind", "value_json", raw))
                break
    # «patronal» pot ser a la cita encara que la unitat del value digui només
    # «per víctima»: el sublimit patronal es una condicio diferent.
    if kinds and InsuranceLimitKind.PER_VICTIM in kinds:
        employer = sources.literal_flag(_EMPLOYER, "limits.kind")
        if employer is not None:
            kinds = tuple(
                InsuranceLimitKind.EMPLOYER_PER_VICTIM
                if kind == InsuranceLimitKind.PER_VICTIM
                else kind
                for kind in kinds
            )
            provenance.append(employer)
    if kinds is None:
        kinds, origin_quote, ambiguous = _from_quotes(sources, limit_kinds, "limits.kind")
        if ambiguous:
            return Normalization(status="not_normalized", reason=REASON_LIMIT_KIND_AMBIGUOUS)
        if origin_quote is not None:
            provenance.append(origin_quote)
    kinds = kinds or (InsuranceLimitKind.UNKNOWN,)

    insurance_type = None
    type_origin = sources.literal_flag(_CIVIL_LIABILITY, "insurance_type")
    if type_origin is not None:
        insurance_type = InsuranceType.CIVIL_LIABILITY.value
        provenance.append(type_origin)

    value = {
        "kind": "insurance",
        "insurance_type": insurance_type,
        "limits": [
            {"kind": kind.value, "amount": str(amount), "currency": currency or "EUR"}
            for kind in kinds
        ],
    }
    return Normalization(status="normalized", value_json=value, provenance=tuple(provenance))


_SIMILAR = re.compile(
    r"similar|semblant|naturalesa igual|mateixa naturalesa|igual naturaleza|analog"
)
_ACCUMULATED = re.compile(r"acumulat|acumulado|any de major execucio|ano de mayor ejecucion")
_INDIVIDUAL = re.compile(
    r"cada contracte|cadascun dels contractes|per contracte|cada contrato|por contrato"
)


def _experience(sources: _Sources) -> Normalization:
    amount, currency, origin, reason = _amount(sources, "amount")
    if amount is None:
        return Normalization(status="not_normalized", reason=reason)
    accumulated = sources.literal_flag(_ACCUMULATED, "amount_basis")
    individual = sources.literal_flag(_INDIVIDUAL, "amount_basis")
    if (accumulated is None) == (individual is None):
        # Ni l'un ni l'altre, o tots dos: no es pot saber si l'import es de cada
        # contracte o de la suma, i confondre'ls canvia el veredicte.
        return Normalization(status="not_normalized", reason=REASON_EXPERIENCE_BASIS_AMBIGUOUS)
    years, aggregation, period_origin, reason = _period(sources)
    if reason:
        return Normalization(status="not_normalized", reason=reason)
    percentage, base, percentage_origin = _percentage(sources)
    money = _money(amount, currency)
    if percentage is not None:
        money["percentage"] = str(percentage)
        money["percentage_base"] = base.value
    provenance = [p for p in (origin, accumulated or individual, percentage_origin) if p]
    provenance += period_origin
    value: dict[str, Any] = {
        "kind": "experience",
        "reference_period": (
            {"type": "last_n_years", "years": years} if years else {"type": "unknown"}
        ),
        "aggregation": aggregation.value,
        "min_individual_amount": money if individual else None,
        "min_accumulated_amount": money if accumulated else None,
    }
    similar = sources.find_flag(_SIMILAR, "similarity_note")
    if similar is not None:
        # Marca de prudencia: «serveis similars» sense criteri objectiu (CPV) ho
        # ha de valorar una persona (`EXPERIENCE_SIMILARITY_REVIEW`).
        value["similarity_note"] = _flat(similar.raw)[:300]
        provenance.append(similar)
    return Normalization(status="normalized", value_json=value, provenance=tuple(provenance))


def _personnel(sources: _Sources) -> Normalization:
    for key in ("minimum_people", "count", "min_people"):
        raw = sources.value.get(key)
        if isinstance(raw, str) and raw.strip().isdigit():
            return Normalization(
                status="normalized",
                value_json={"minimum_people": int(raw.strip())},
                provenance=(Provenance("minimum_people", "value_json", raw),),
            )
    # Una llista de perfils («cap de projecte, especialista...») no es un
    # recompte: comptar-ne els elements seria inventar el minim.
    return Normalization(status="not_normalized", reason=REASON_NO_COUNT)


# --- ENS ------------------------------------------------------------------- #

_ENS = re.compile(r"\bENS\b|Esquema\s+Nacional\s+de\s+Segur(?:etat|idad)", re.I)
_ENS_STRICT = re.compile(r"\bENS\b|Esquema\s+Nacional\s+de\s+Segur(?:etat|idad)")
_ENS_LEVEL = re.compile(
    r"(?:categoria|nivell|nivel)\s+(basica|basic|mitjana|mitja|media|medio|alta|alt)\b"
)
ENS_LEVELS = {
    "basica": "bàsica", "basic": "bàsica",
    "mitjana": "mitjana", "mitja": "mitjana", "media": "mitjana", "medio": "mitjana",
    "alta": "alta", "alt": "alta",
}  # fmt: skip


def names_ens(text: str) -> bool:
    """Cert si el text NOMENA l'ENS (sigla en majuscules o el nom sencer)."""
    return bool(_ENS_STRICT.search(_flat(text)))


def ens_level(text: str) -> str | None:
    match = _ENS_LEVEL.search(_fold(text))
    return ENS_LEVELS.get(match.group(1)) if match else None


def _ens(sources: _Sources, requirement: Any) -> Normalization | None:
    """L'ENS nomenat explicitament: certificacio, amb nivell nomes si el plec el diu."""
    texts = [("title", sources.title, None), ("description", sources.description, None)]
    texts += [("value_json", text, None) for _, text in sources.value_texts]
    texts += [("quote", q.text, q.evidence_id) for q in sources.quotes]
    named = next(((s, t, e) for s, t, e in texts if t and names_ens(t)), None)
    if named is None:
        return None
    if getattr(requirement, "obligation", None) != RequirementObligation.MANDATORY:
        return Normalization(status="not_normalized", reason=REASON_NOT_MANDATORY)
    origin = Provenance("certification.scheme", named[0], named[1], named[2])
    # El nivell, NOMES del text literal (value o cites).
    levels = {
        ens_level(text): (source, text, evidence)
        for source, text, evidence in texts
        # Una cita compta si nomena l'ENS: «categoria alta» d'una altra cosa no
        # es el nivell de l'esquema.
        if (source == "value_json" or (source == "quote" and names_ens(text))) and ens_level(text)
    }
    if len(levels) > 1:
        return Normalization(
            status="routed",
            route=ROUTE_ENS_LEVEL_NOT_STATED,
            reason=REASON_ENS_LEVEL_AMBIGUOUS,
            read_as=RequirementCategory.CERTIFICATION,
            provenance=(origin,),
            detail={"scheme": "ENS", "levels": sorted(levels)},
        )
    if not levels:
        return Normalization(
            status="routed",
            route=ROUTE_ENS_LEVEL_NOT_STATED,
            read_as=RequirementCategory.CERTIFICATION,
            provenance=(origin,),
            detail={"scheme": "ENS"},
        )
    level, (source, text, evidence) = next(iter(levels.items()))
    return Normalization(
        status="routed",
        route=ROUTE_ENS_CERTIFICATION,
        read_as=RequirementCategory.CERTIFICATION,
        value_json={
            "kind": "certification",
            "name": f"ENS categoria {level}",
            "equivalence_allowed": False,
        },
        provenance=(origin, Provenance("certification.level", source, text, evidence)),
        detail={"scheme": "ENS", "level": level},
    )


# --- Registres -------------------------------------------------------------- #

_RELI_ROLECE = re.compile(r"\breli\b|\brolece\b")
#: «les empreses inscrites», «licitadors inscrits»: la clausula parla de QUI ja hi
#: es inscrit (que ha d'aportar), no exigeix inscriure-s'hi.
_REGISTERED_SUBJECT = re.compile(
    r"\b(?:empreses|empresas|licitadors|licitadores|persones licitadores)\s+inscrit(?:e|a|o)?s\b"
    r"|\bque\s+(?:estiguin|esten|estan)\s+inscrit"
)
_SECTOR_OPERATOR = re.compile(
    r"\bcnmc\b|comissio nacional dels mercats|comision nacional de los mercados"
    r"|registre d'operadors|registro de operadores"
)


def _registration(sources: _Sources, requirement: Any) -> Normalization | None:
    if sources.find_flag(_RELI_ROLECE, "registry") is not None:
        subject = sources.find_flag(_REGISTERED_SUBJECT, "registry.subject")
        if subject is not None:
            # Documentacio que aporta qui JA hi es inscrit. No es l'exigencia
            # d'estar-hi: avaluar-la com a tal demanaria un ROLECE que el plec no
            # exigeix.
            return Normalization(
                status="routed",
                route=ROUTE_REGISTRY_DOCUMENTATION_ONLY,
                provenance=(subject,),
            )
        return None
    named = sources.find_flag(_SECTOR_OPERATOR, "registry")
    if named is not None:
        if getattr(requirement, "obligation", None) != RequirementObligation.MANDATORY:
            return Normalization(status="not_normalized", reason=REASON_NOT_MANDATORY)
        return Normalization(
            status="routed",
            route=ROUTE_SECTOR_OPERATOR_REGISTRY,
            value_json={"type": "sector_operator_registry", "name": _flat(named.raw)[:200]},
            provenance=(named,),
        )
    return Normalization(status="not_normalized", reason=REASON_REGISTRY_NOT_NAMED)


def _certification(sources: _Sources) -> Normalization:
    for key in ("name", "certification", "standard"):
        raw = sources.value.get(key)
        if isinstance(raw, str) and raw.strip():
            return Normalization(
                status="normalized",
                value_json={"kind": "certification", "name": raw.strip()},
                provenance=(Provenance("certification.name", "value_json", raw),),
            )
    return Normalization(status="not_normalized", reason=REASON_CERTIFICATION_NOT_NAMED)


# --------------------------------------------------------------------------- #
# Porta d'entrada                                                               #
# --------------------------------------------------------------------------- #


def normalize_requirement(requirement: Any, profile: str | None) -> Normalization | None:
    """El valor normalitzat d'un requisit d'un perfil de format lliure.

    `None` vol dir "aquest requisit no te res a normalitzar" (perfil tipat,
    categoria que cap regla llegeix): s'avalua exactament com abans i no porta
    cap rastre de normalitzacio.
    """
    if profile not in FREE_FORMAT_PROFILES:
        return None
    category = getattr(requirement, "category", None)
    sources = _Sources(requirement)

    if category in _ENS_CATEGORIES:
        routed = _ens(sources, requirement)
        if routed is not None and (routed.status == "routed" or category in _VALUE_CATEGORIES):
            return routed
    if category not in _VALUE_CATEGORIES:
        return None

    if category == RequirementCategory.REGISTRATION:
        return _registration(sources, requirement)
    if category == RequirementCategory.ECONOMIC_SOLVENCY:
        return _economic(sources, requirement)
    if category == RequirementCategory.INSURANCE:
        return _insurance(sources)
    if category in (RequirementCategory.EXPERIENCE, RequirementCategory.TECHNICAL_SOLVENCY):
        return _experience(sources)
    if category == RequirementCategory.PERSONNEL:
        return _personnel(sources)
    if category == RequirementCategory.CERTIFICATION:
        return _certification(sources)
    return None  # pragma: no cover - `_VALUE_CATEGORIES` es tancat


def canonical(value: Any) -> str:
    """Forma canonica d'un valor per comparar-lo exactament."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


__all__ = [
    "FREE_FORMAT_PROFILES",
    "NORMALIZER_VERSION",
    "Normalization",
    "Provenance",
    "VALUE_SCHEMA_VERSION",
    "amounts_in_text",
    "canonical",
    "ens_level",
    "limit_kinds",
    "names_ens",
    "normalize_requirement",
    "parse_aggregation",
    "parse_amount",
    "parse_years",
]
