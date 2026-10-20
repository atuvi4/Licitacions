"""Complement de LECTURA per als requisits en format lliure del perfil `product-v2`.

El perfil `product-v2` no produeix valors tipats: escriu el que diu el plec tal
com el diu, `{"amount": "91.904,74 €", "period": "millor dels 3 últims
exercicis"}`. Les regles de 4C no ho saben llegir (`to_decimal("91.904,74")` no
es cap nombre, i `to_decimal("300.000")` seria 300, no 300.000), i el requisit
acaba en `REQUIREMENT_VALUE_MISSING` encara que el plec digui la xifra ben clara.

Aquest modul es un **complement estret, no una relectura global** (decisio del
controlador, ronda 1):

* **Abast.** Nomes requisits d'un run `product-v2` amb `value_schema` NUL. Cap
  `v1`, cap `v2` tipat, cap `v3`, cap `gold`, cap requisit amb `value_schema`: per
  a tots ells `normalize_requirement` torna `None` i el motor fa exactament el que
  feia a la v4. Ho verifica una prova diferencial contra el codi de `21d5f9e`.
* **Enriquir, no reconstruir.** El valor normalitzat es construeix a sobre del
  `value_json`: es tradueixen els camps en text i es TRASLLADEN les condicions que
  ja hi eren (`minimum_projects`, `cpv_codes`, `additional_insured`, validesa...).
  Una clau que no se sap traslladar fa que no es normalitzi (`unmapped_conditions`):
  perdre una condicio en silenci podria fer passar a «compleix» una exigencia.
* **Fonts.** Primer el `value_json`; si no hi es o es nul, el text LITERAL de les
  cites verificades (localitzades caracter a caracter dins de la pagina), i nomes
  si en surt UN sol valor inequivoc en un context de llindar. El titol i la
  descripcio (text del model) no son mai font d'un valor; nomes poden fer el
  resultat mes prudent (similitud, documentacio de qui ja es inscrit).
* **Ambiguitat = no es normalitza**, i el motiu queda escrit (`reason`).

Les regles nomes apliquen comportaments nous (sublimit patronal estricte, registre
d'operadors) sobre requisits adaptats en memoria amb `NORMALIZED_ATTRIBUTE`, que
nomes posa el motor amb un valor d'aqui. Res no es desa: el valor viatja dins de l'avaluacio
(`explanation_params.normalization`), amb la procedencia de cada camp.

Modul pur: ni base de dades ni model.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from app.models.enums import (
    AggregationType,
    EconomicMetric,
    InsuranceLimitKind,
    InsuranceType,
    PercentageBase,
    RequirementCategory,
    RequirementObligation,
)

#: Versio del normalitzador. Va a cada `explanation_params.normalization`.
NORMALIZER_VERSION = "product-v2-normalizer-v2"

#: L'UNIC perfil al qual s'aplica.
NORMALIZED_PROFILE = "product-v2"

#: Atribut del requisit ADAPTAT en memoria (`engine._AdaptedRequirement`) que
#: activa els comportaments nous de la v5 a les regles. No viu mai dins de cap
#: valor: un `value_json` desat o arribat de fora no pot activar res (ronda 2).
NORMALIZED_ATTRIBUTE = "_v5_normalized"


def is_normalized(requirement: object) -> bool:
    """Cert NOMES per a un requisit adaptat pel complement `product-v2`."""
    return getattr(requirement, NORMALIZED_ATTRIBUTE, False) is True


# --------------------------------------------------------------------------- #
# Resultat                                                                      #
# --------------------------------------------------------------------------- #

#: Codis de `reason`. Tancats: la interficie i l'informe en deriven el text.
REASON_NO_SOURCE = "no_value_in_value_json_or_quotes"
REASON_AMOUNT_AMBIGUOUS = "amount_ambiguous"
REASON_AMOUNT_UNPARSEABLE = "amount_unparseable"
REASON_AMOUNT_NOT_THRESHOLD = "quote_amount_not_in_threshold_context"
REASON_CURRENCY_CONFLICT = "currency_conflict"
REASON_PERIOD_AMBIGUOUS = "period_ambiguous"
REASON_METRIC_NOT_STATED = "metric_not_stated"
REASON_LIMIT_KIND_AMBIGUOUS = "limit_kind_ambiguous"
REASON_EXPERIENCE_BASIS_AMBIGUOUS = "experience_amount_basis_ambiguous"
REASON_NO_COUNT = "no_count_stated"
REASON_REGISTRY_NOT_NAMED = "registry_not_named_explicitly"
REASON_NOT_MANDATORY = "not_mandatory"
REASON_UNMAPPED_CONDITIONS = "unmapped_conditions"
REASON_PERCENTAGE_BASE_AMBIGUOUS = "percentage_base_ambiguous"

#: Rutes: el requisit es resol amb una regla diferent de la de la seva categoria
#: o directament amb revisio (sense reescriure la categoria).
ROUTE_ENS = "ens_certification"
ROUTE_REGISTRY_DOCUMENTATION_ONLY = "registry_documentation_only"
ROUTE_SECTOR_OPERATOR_REGISTRY = "sector_operator_registry"
ROUTE_NOT_REQUIRED = "not_required"


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
class EnsReading:
    """Que demana el plec de l'ENS. `level=None` = no se'n pot dir la categoria."""

    level: str | None
    #: El plec admet una categoria superior («o superior», «com a minim»...).
    or_higher: bool = False
    equivalence_allowed: bool = False
    valid_at_deadline: bool = False


@dataclass(frozen=True, slots=True)
class Normalization:
    """El que el normalitzador ha pogut llegir d'un requisit, i per que no."""

    #: `normalized` (hi ha valor), `not_normalized` (s'avalua com ara, amb el
    #: motiu) o `routed` (una ruta explicita).
    status: str
    #: Valor amb la forma que llegeixen les regles.
    value_json: dict[str, Any] | None = None
    #: `value_schema` amb que s'ha de llegir `value_json` (v2 o `None` = lliure).
    value_schema: str | None = None
    provenance: tuple[Provenance, ...] = ()
    reason: str | None = None
    route: str | None = None
    ens: EnsReading | None = None
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
            data["value"] = dict(self.value_json)
        if self.provenance:
            data["provenance"] = [item.as_dict() for item in self.provenance]
        if self.reason:
            data["reason"] = self.reason
        if self.route:
            data["route"] = self.route
        if self.ens is not None:
            data["ens"] = {
                "level": self.ens.level,
                "or_higher": self.ens.or_higher,
            }
        if self.detail:
            data["detail"] = self.detail
        return data

    def consolidation_value(self) -> dict[str, Any] | None:
        """Les dades DECISIVES de l'exigencia, per agrupar duplicats.

        Nomes el llindar: import, moneda, tipus de limit, periode i agregacio. Les
        condicions (validesa, similitud) i un percentatge informatiu al costat d'un
        import no hi entren: si dos duplicats en difereixen, el grup es resol pel
        mes prudent i queda marcat com a divergent.
        """
        value = self.value_json
        if value is None:
            if self.ens is not None and self.ens.level is not None:
                return {"kind": "ens", "level": self.ens.level, "or_higher": self.ens.or_higher}
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
                "cpv_codes": sorted(value.get("cpv_codes") or []),
            }
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
_THOUSANDS = re.compile(r"^\d{1,3}(?:\.\d{3})+(?:,\d+)?$")
#: Marca de moneda. Nomes l'euro.
_CURRENCY_TOKEN = re.compile(r"(?:€|\beur\b|\beuros?\b)", re.I)
#: Qualsevol nombre d'una cita (per decidir si n'hi ha mes d'un).
_ANY_NUMBER = re.compile(r"(?<![\w.,])(\d[\d.]*(?:,\d+)?)(?![\w])")
_CURRENCY_AFTER = re.compile(r"^\s*(?:€|euros?\b|eur\b)", re.I)
_RATIO_AFTER = re.compile(r"^\s*(?:%|per cent\b|vegades\b|veces\b)", re.I)
#: Paraules que lliguen l'import d'una cita al llindar, just abans de l'import.
_THRESHOLD_MARKER = re.compile(
    r"(?:igual o superior a|superior a|no inferior a|es a dir,?|es decir,?|minim(?:a)? de"
    r"|minimo de|al menys de|almenys|al menos|com a minim|como minimo|≥|>=|import minim de"
    r"|import minim|per un import de)\s*$"
)


def _local_decimal(token: str) -> Decimal | None:
    if not _LOCAL_NUMBER_FULL.match(token):
        return None
    try:
        return Decimal(token.replace(".", "").replace(",", "."))
    except InvalidOperation:  # pragma: no cover - el patro ja ho impedeix
        return None


def parse_amount(raw: Any) -> tuple[Decimal | None, str | None, str | None]:
    """`(import, moneda, motiu)` d'un valor lliure del `value_json`.

    Accepta un nombre JSON, `"91.904,74"`, `"91.904,74 €"`, `"300.000 €"`,
    `"42.888,88 euros"`. Un text amb paraules (`"1,5 vegades l'anualitat"`) no es
    cap import. Un `"1.5"` o `"38326.08"` (punt decimal anglosaxo) tampoc: en
    format local el punt es de milers i admetre totes dues lectures seria triar-ne
    una a l'atzar.
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


@dataclass(frozen=True, slots=True)
class _Candidate:
    """Un nombre d'una cita que podria ser un import."""

    amount: Decimal | None
    start: int
    end: int
    marked: bool


def quote_amounts(text: str) -> tuple[list[_Candidate], bool]:
    """Els imports candidats d'una cita i si hi ha un multiple o un percentatge.

    Compta TOTS els nombres que poden ser un import, portin «€» o no: amb punt de
    milers, amb decimals i «€», o enters de 5 xifres o mes (un any, «3 últims» o
    «RD 311/2022» no ho son). Un «1,5 vegades» o un «70%» no es import, pero diu
    que el text parla d'una base.
    """
    flat = _flat(text)
    folded = _fold(text)
    candidates: list[_Candidate] = []
    ratio = False
    for match in _ANY_NUMBER.finditer(flat):
        token = match.group(1).rstrip(".")
        after = flat[match.end() : match.end() + 12]
        if _RATIO_AFTER.match(after):
            ratio = True
            continue
        with_currency = bool(_CURRENCY_AFTER.match(after))
        amount_like = (
            bool(_THOUSANDS.match(token))
            or with_currency
            or (token.isdigit() and len(token) >= 5)
            or ("." in token and not _THOUSANDS.match(token))
        )
        if not amount_like:
            continue
        before = folded[max(0, match.start() - 40) : match.start()]
        candidates.append(
            _Candidate(
                amount=_local_decimal(token),
                start=match.start(),
                end=match.end(),
                marked=bool(_THRESHOLD_MARKER.search(before)),
            )
        )
    return candidates, ratio


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
#: «El millor», «l'any de major execució», «el de major volum». NO «igual o
#: major»: aixo es l'operador, no l'agregacio.
_MAX_HINT = re.compile(
    r"\bmillor\b|\bmejor\b|\bde major (?:volum|execucio|import)\b"
    r"|\bde mayor (?:volumen|ejecucion|importe)\b|\bmes alt\b"
)
_EACH_HINT = re.compile(
    r"\bcadascun\b|\bcada un\b|\bcada any\b|\bcada exercici\b|\bcada ejercicio\b"
)


def parse_years(text: str) -> tuple[int | None, bool]:
    """Quants anys mira un periode. `(anys, ambigu)`."""
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


_BASES = (
    (("anualitat mitjana", "anualidad media"), PercentageBase.CONTRACT_ANNUITY),
    (("pressupost base", "presupuesto base"), PercentageBase.BASE_BUDGET),
    (("valor estimat", "valor estimado"), PercentageBase.ESTIMATED_VALUE),
)


def percentage_base(text: str) -> tuple[PercentageBase, bool]:
    """La base d'un percentatge i si el text en nomena mes d'una (ambigu)."""
    folded = _fold(text)
    found = {base for words, base in _BASES if any(word in folded for word in words)}
    if len(found) > 1:
        return PercentageBase.UNKNOWN, True
    return (found.pop() if found else PercentageBase.UNKNOWN), False


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
    """Els textos del `value_json`, amb el seu cami."""
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

    def literal_texts(self) -> list[tuple[str, str, str | None]]:
        """`(font, text, evidencia)` del value i de les cites: les fonts d'un VALOR."""
        return [("value_json", text, None) for _, text in self.value_texts] + [
            ("quote", q.text, q.evidence_id) for q in self.quotes
        ]

    def literal_text(self) -> str:
        return " ".join(text for _, text, _ in self.literal_texts())

    def literal_flag(self, pattern: re.Pattern[str], field_name: str) -> Provenance | None:
        """Una marca en el text literal (value o cites)."""
        for source, text, evidence in self.literal_texts():
            if pattern.search(_fold(text)):
                return Provenance(field_name, source, text, evidence)
        return None

    def find_flag(self, pattern: re.Pattern[str], field_name: str) -> Provenance | None:
        """Com `literal_flag`, i tambe titol i descripcio: NOMES per a prudencia."""
        found = self.literal_flag(pattern, field_name)
        if found is not None:
            return found
        for source, text in (("title", self.title), ("description", self.description)):
            if text and pattern.search(_fold(text)):
                return Provenance(field_name, source, text, None)
        return None


def _unmapped(value: dict[str, Any], known: frozenset[str]) -> list[str]:
    """Les claus del value que el normalitzador no sap ni traduir ni traslladar."""
    return sorted(key for key, raw in value.items() if key not in known and raw not in (None, ""))


def _value_amount(
    sources: _Sources, keys: tuple[str, ...], field_name: str
) -> tuple[Decimal | None, str | None, Provenance | None, str | None, bool]:
    """L'import del `value_json`. `(import, moneda, procedencia, motiu, present)`.

    `present` fals vol dir que el value no en porta cap (clau absent o nul·la): llavors
    es poden mirar les cites.
    """
    for key in keys:
        raw = sources.value.get(key)
        if raw is None or isinstance(raw, dict):
            continue
        amount, currency, reason = parse_amount(raw)
        if amount is None:
            return None, None, None, reason, True
        declared, known = _currency_field(sources.value.get("currency"))
        if not known or (currency and declared and currency != declared):
            return None, None, None, REASON_CURRENCY_CONFLICT, True
        provenance = Provenance(field_name, "value_json", str(raw))
        return amount, currency or declared or "EUR", provenance, None, True
    return None, None, None, REASON_NO_SOURCE, False


def _quote_amount(
    sources: _Sources, field_name: str
) -> tuple[Decimal | None, Provenance | None, _Quote | None, _Candidate | None, str | None]:
    """L'import de llindar de les cites. `(import, procedencia, cita, candidat, motiu)`.

    Nomes si cada cita que en porta en te UN sol (comptant els que no porten «€»),
    precedit d'una marca de llindar («igual o superior a», «és a dir», «mínima
    de»...), i totes diuen el mateix. Si una cita en te mes d'un, o parla d'un
    multiple o percentatge sense lligar l'import al llindar, no se n'agafa cap.
    """
    found: dict[Decimal, tuple[_Quote, _Candidate]] = {}
    for quote in sources.quotes:
        candidates, _ratio = quote_amounts(quote.text)
        if not candidates:
            continue
        if len(candidates) > 1:
            return None, None, None, None, REASON_AMOUNT_AMBIGUOUS
        candidate = candidates[0]
        if candidate.amount is None:
            return None, None, None, None, REASON_AMOUNT_AMBIGUOUS
        if not candidate.marked:
            # Sense marca, un import al costat d'un multiple o d'un percentatge pot
            # ser la BASE (l'anualitat), no el llindar. Ni amb ni sense multiple
            # s'agafa: el context ha de dir que es el llindar.
            return None, None, None, None, REASON_AMOUNT_NOT_THRESHOLD
        found.setdefault(candidate.amount, (quote, candidate))
    if not found:
        return None, None, None, None, REASON_NO_SOURCE
    if len(found) > 1:
        return None, None, None, None, REASON_AMOUNT_AMBIGUOUS
    amount, (quote, candidate) = next(iter(found.items()))
    origin = Provenance(field_name, "quote", quote.text, quote.evidence_id)
    return amount, origin, quote, candidate, None


def _text_for(sources: _Sources, key: str) -> str | None:
    raw = sources.value.get(key)
    if isinstance(raw, str) and raw.strip():
        return raw
    return None


def _from_quotes(
    sources: _Sources, parse: Any, field_name: str
) -> tuple[Any, Provenance | None, bool]:
    """Un valor que nomes es pot llegir de les cites: unic, o res."""
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
    """Anys i agregacio: del `period` (o `years`) del value i, el que hi falti, de les cites."""
    provenance: list[Provenance] = []
    years: int | None = None
    aggregation = AggregationType.UNKNOWN
    raw_years = sources.value.get("years")
    if isinstance(raw_years, int) and not isinstance(raw_years, bool) and raw_years > 0:
        years = raw_years
        provenance.append(Provenance("reference_period.years", "value_json", str(raw_years)))
    text = _text_for(sources, "period")
    if text is not None:
        parsed_years, ambiguous = parse_years(text)
        if ambiguous or (years is not None and parsed_years not in (None, years)):
            return None, AggregationType.UNKNOWN, [], REASON_PERIOD_AMBIGUOUS
        aggregation, ambiguous = parse_aggregation(text)
        if ambiguous:
            return None, AggregationType.UNKNOWN, [], REASON_PERIOD_AMBIGUOUS
        if years is None and parsed_years is not None:
            years = parsed_years
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


def _percentage(
    sources: _Sources,
) -> tuple[Decimal | None, PercentageBase, Provenance | None, bool]:
    """Un percentatge o un multiple del value i la seva base. `(..., ambigu)`."""
    for key in ("percent", "percentage"):
        if sources.value.get(key) is not None:
            value = parse_percentage(sources.value.get(key))
            if value is not None:
                base, ambiguous = percentage_base(sources.literal_text())
                return (
                    value,
                    base,
                    Provenance("threshold.percentage", "value_json", str(sources.value[key])),
                    ambiguous,
                )
    raw = sources.value.get("multiple")
    multiple = parse_multiple(raw)
    if multiple is not None:
        base, ambiguous = percentage_base(str(raw))
        return (
            multiple * Decimal(100),
            base,
            Provenance("threshold.percentage", "value_json", str(raw)),
            ambiguous,
        )
    return None, PercentageBase.UNKNOWN, None, False


def _money(amount: Decimal | None, currency: str | None) -> dict[str, Any]:
    return {"amount": None if amount is None else str(amount), "currency": currency or "EUR"}


def _not_normalized(reason: str, **detail: Any) -> Normalization:
    return Normalization(status="not_normalized", reason=reason, detail=detail)


# --------------------------------------------------------------------------- #
# Solvencia economica                                                           #
# --------------------------------------------------------------------------- #

_TURNOVER = re.compile(
    r"volum anual de negoci|volum de negoci|xifra anual de negoci|xifra de negoci"
    r"|volumen anual de negocio|volumen de negocio|cifra anual de negocio"
)
_ECONOMIC_KEYS = frozenset(
    {"amount", "import", "currency", "period", "years", "multiple", "percent", "percentage"}
)


def _economic(sources: _Sources, requirement: Any) -> Normalization:
    unmapped = _unmapped(sources.value, _ECONOMIC_KEYS)
    if unmapped:
        return _not_normalized(REASON_UNMAPPED_CONDITIONS, keys=unmapped)
    amount, currency, origin, reason, present = _value_amount(
        sources, ("amount", "import"), "threshold.amount"
    )
    if present and amount is None:
        # Hi ha un import pero no es llegible: no es substitueix per res.
        return _not_normalized(reason or REASON_AMOUNT_UNPARSEABLE)
    if not present:
        amount, origin, _quote, _candidate, quote_reason = _quote_amount(
            sources, "threshold.amount"
        )
        currency = "EUR" if amount is not None else None
        if amount is None and quote_reason not in (None, REASON_NO_SOURCE):
            return _not_normalized(quote_reason)
    percentage, base, percentage_origin, base_ambiguous = _percentage(sources)
    if base_ambiguous:
        return _not_normalized(REASON_PERCENTAGE_BASE_AMBIGUOUS)
    if amount is None and percentage is None:
        return _not_normalized(REASON_NO_SOURCE)
    metric_origin = sources.literal_flag(_TURNOVER, "metric")
    if metric_origin is None:
        return _not_normalized(REASON_METRIC_NOT_STATED)
    years, aggregation, period_origin, reason = _period(sources)
    if reason:
        return _not_normalized(reason)
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
    return Normalization(
        status="normalized", value_json=value, value_schema=_V2, provenance=tuple(provenance)
    )


# --------------------------------------------------------------------------- #
# Assegurances                                                                  #
# --------------------------------------------------------------------------- #

_PER_CLAIM_AND_YEAR = re.compile(r"per sinistre i (?:per )?any\b|por siniestro y (?:por )?ano\b")
_PER_CLAIM = re.compile(r"per sinistre|por siniestro")
_PER_VICTIM = re.compile(r"per victima|por victima")
#: Nomes «agregat anual» o «per any»: «prima anual» no es cap limit.
_ANNUAL = re.compile(r"agregat anual|agregado anual|\bper any\b|\bpor ano\b")
_TOTAL = re.compile(r"limit total|limite total|cobertura total")
_EMPLOYER = re.compile(r"patronal")
_CIVIL_LIABILITY = re.compile(r"responsabilitat civil|responsabilidad civil")
#: Bases de sublimit que la v2 no sap representar: si el text en parla, no es
#: compara res (la mateixa guarda que `specs._SUBLIMIT_HINT`).
_UNMAPPED_BASE = re.compile(
    r"\bper (?:cada )?(?:ocurrencia|assegurat|occurrence)\b"
    r"|\bpor (?:cada )?(?:ocurrencia|asegurado)\b"
)
_SUBLIMIT_WORD = re.compile(r"sub[\s_-]*limit|sublimite")
#: «mantenir vigent durant tota l'execució»: la condicio que una regla no comprova.
_VALID_THROUGH = re.compile(
    r"durant tota l'execucio|durant tota la (?:vigencia|durada|execucio) del contracte"
    r"|durante toda la (?:ejecucion|vigencia|duracion)|mantenir(?:-la)? vigent|mantener vigente"
)
_INSURANCE_KEYS = frozenset(
    {"amount", "coverage_amount", "currency", "unit", "limit_kind", "kind", "type", "basis"}
)
#: Condicions que es TRASLLADEN tal qual al camp v2 equivalent.
_INSURANCE_CONDITIONS = frozenset(
    {"additional_insured", "must_be_valid_through_contract", "validity_note", "proof"}
)


def limit_kinds(text: str) -> tuple[tuple[InsuranceLimitKind, ...] | None, bool]:
    """Quins limits nomena un text. `(tipus, ambigu)`; `None` si no en nomena cap.

    «per sinistre i any» son DOS limits (per sinistre i agregat anual) del mateix
    import: es l'unica combinacio en que un import val per a dos limits. Qualsevol
    altra combinacio, un sublimit que no se sap mapar («sublímit», «per
    ocurrència», «per assegurat») o un «sublímit» sense base: ambigu.
    """
    folded = _fold(text)
    if _UNMAPPED_BASE.search(folded):
        return None, True
    if _PER_CLAIM_AND_YEAR.search(folded):
        rest = _PER_CLAIM_AND_YEAR.sub(" ", folded)
        if _PER_CLAIM.search(rest) or _PER_VICTIM.search(rest) or _ANNUAL.search(rest):
            return None, True
        return (InsuranceLimitKind.PER_CLAIM, InsuranceLimitKind.ANNUAL_AGGREGATE), False
    found: list[InsuranceLimitKind] = []
    if _PER_CLAIM.search(folded):
        found.append(InsuranceLimitKind.PER_CLAIM)
    if _PER_VICTIM.search(folded):
        found.append(
            InsuranceLimitKind.EMPLOYER_PER_VICTIM
            if _EMPLOYER.search(folded)
            else InsuranceLimitKind.PER_VICTIM
        )
    if _ANNUAL.search(folded):
        found.append(InsuranceLimitKind.ANNUAL_AGGREGATE)
    if _TOTAL.search(folded):
        found.append(InsuranceLimitKind.TOTAL)
    if len(found) > 1:
        return None, True
    if not found:
        return (None, True) if _SUBLIMIT_WORD.search(folded) else (None, False)
    return tuple(found), False


def _base_of(kinds: tuple[InsuranceLimitKind, ...]) -> tuple[InsuranceLimitKind, ...]:
    """La base d'un import, sense distingir «patronal» (que es decideix a part)."""
    return tuple(
        InsuranceLimitKind.PER_VICTIM if kind == InsuranceLimitKind.EMPLOYER_PER_VICTIM else kind
        for kind in kinds
    )


def _insurance(sources: _Sources) -> Normalization:
    unmapped = _unmapped(sources.value, _INSURANCE_KEYS | _INSURANCE_CONDITIONS)
    if unmapped:
        return _not_normalized(REASON_UNMAPPED_CONDITIONS, keys=unmapped)
    # Cap cita del requisit pot parlar d'un sublimit que no se sap mapar.
    for _source, text, _evidence in sources.literal_texts():
        if _UNMAPPED_BASE.search(_fold(text)):
            return _not_normalized(REASON_LIMIT_KIND_AMBIGUOUS)

    amount, currency, origin, reason, present = _value_amount(
        sources, ("amount", "coverage_amount"), "limits.amount"
    )
    quote = candidate = None
    if present and amount is None:
        return _not_normalized(reason or REASON_AMOUNT_UNPARSEABLE)
    if not present:
        amount, origin, quote, candidate, quote_reason = _quote_amount(sources, "limits.amount")
        currency = "EUR"
        if amount is None:
            return _not_normalized(quote_reason or REASON_NO_SOURCE)

    provenance = [origin]
    kinds: tuple[InsuranceLimitKind, ...] | None = None
    for key in ("unit", "limit_kind", "kind", "type", "basis"):
        raw = sources.value.get(key)
        if isinstance(raw, str) and raw.strip():
            kinds, ambiguous = limit_kinds(raw)
            if ambiguous:
                return _not_normalized(REASON_LIMIT_KIND_AMBIGUOUS)
            if kinds is not None:
                provenance.append(Provenance("limits.kind", "value_json", raw))
                break

    # El tipus que diuen les cites: el text just darrere de l'import a la cita que
    # el DONA o, si l'import es del value, a les cites on aquell import es l'unic.
    segments: list[tuple[str, _Quote]] = []
    if quote is not None and candidate is not None:
        segments.append((_flat(quote.text)[candidate.end :], quote))
    else:
        for item in sources.quotes:
            found, _ratio = quote_amounts(item.text)
            if len(found) == 1 and found[0].amount == amount:
                segments.append((_flat(item.text)[found[0].end :], item))
            elif len(found) > 1 and any(c.amount == amount for c in found):
                return _not_normalized(REASON_LIMIT_KIND_AMBIGUOUS)
    readings: dict[tuple[InsuranceLimitKind, ...], _Quote] = {}
    for segment, item in segments:
        read, ambiguous = limit_kinds(segment)
        if ambiguous:
            return _not_normalized(REASON_LIMIT_KIND_AMBIGUOUS)
        if read is not None:
            readings.setdefault(read, item)
    if len(readings) > 1:
        return _not_normalized(REASON_LIMIT_KIND_AMBIGUOUS)
    if readings:
        quoted, item = next(iter(readings.items()))
        if kinds is not None and _base_of(quoted) != _base_of(kinds):
            # IR-2: el value diu una base i la cita una altra («per sinistre» contra
            # «per sinistre i any»). Unitats contradictories: no es tria.
            return _not_normalized(REASON_LIMIT_KIND_AMBIGUOUS)
        if kinds is None:
            kinds = quoted
        provenance.append(Provenance("limits.kind", "quote", item.text, item.evidence_id))
    if kinds is not None and InsuranceLimitKind.PER_VICTIM in kinds:
        # «patronal» pot ser a la cita encara que la unitat digui nomes «per víctima».
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
        # Cap tipus i cap marca de sublimit: cobertura contra cobertura, com la v1.
        if any(_SUBLIMIT_WORD.search(_fold(text)) for _, text, _ in sources.literal_texts()):
            return _not_normalized(REASON_LIMIT_KIND_AMBIGUOUS)
        kinds = (InsuranceLimitKind.UNKNOWN,)

    value: dict[str, Any] = {
        "kind": "insurance",
        "insurance_type": None,
        "limits": [
            {"kind": kind.value, "amount": str(amount), "currency": currency or "EUR"}
            for kind in kinds
        ],
    }
    type_origin = sources.literal_flag(_CIVIL_LIABILITY, "insurance_type")
    if type_origin is not None:
        value["insurance_type"] = InsuranceType.CIVIL_LIABILITY.value
        provenance.append(type_origin)
    for key in _INSURANCE_CONDITIONS:
        if sources.value.get(key) not in (None, ""):
            value[key] = sources.value[key]
            provenance.append(Provenance(key, "value_json", str(sources.value[key])))
    if not value.get("must_be_valid_through_contract"):
        through = sources.literal_flag(_VALID_THROUGH, "must_be_valid_through_contract")
        if through is not None:
            # Com faria un requisit v2 tipat: una condicio que cap regla no
            # comprova i que impedeix `satisfies` (INSURANCE_CONDITION_NOT_CHECKED).
            value["must_be_valid_through_contract"] = True
            provenance.append(through)
    return Normalization(
        status="normalized", value_json=value, value_schema=_V2, provenance=tuple(provenance)
    )


# --------------------------------------------------------------------------- #
# Experiencia                                                                   #
# --------------------------------------------------------------------------- #

_SIMILAR = re.compile(
    r"similar|semblant|naturalesa igual|mateixa naturalesa|igual naturaleza|analog"
)
_ACCUMULATED = re.compile(r"acumulat|acumulado|any de major execucio|ano de mayor ejecucion")
_INDIVIDUAL = re.compile(
    r"cada contracte|cadascun dels contractes|per contracte|cada contrato|por contrato"
)
_EXPERIENCE_KEYS = frozenset(
    {
        "amount", "minimum_amount", "minimum_amount_each", "currency", "period", "years",
        "percent", "percentage", "multiple",
        # Condicions que es traslladen:
        "minimum_projects", "cpv_codes", "services", "similarity_condition", "lookback_years",
    }
)  # fmt: skip


def _int_of(raw: Any) -> int | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw > 0:
        return raw
    if isinstance(raw, str) and raw.strip().isdigit() and int(raw.strip()) > 0:
        return int(raw.strip())
    return None


_CPV = re.compile(r"^\d{8}(?:-\d)?$")
_LIST_SEPARATOR = re.compile(r"[,;\n]")


def _text_list(raw: Any) -> list[str] | None:
    """Una llista de textos. Un text sol es UN element (o diversos amb separador
    explicit), mai una llista de caracters. `None` = forma desconeguda."""
    if raw in (None, "", []):
        return []
    if isinstance(raw, str):
        return [item.strip() for item in _LIST_SEPARATOR.split(raw) if item.strip()]
    if isinstance(raw, list) and all(isinstance(item, str | int) for item in raw):
        return [str(item).strip() for item in raw if str(item).strip()]
    return None


def _cpv_codes(raw: Any) -> list[str] | None:
    """Els CPV del value, validats (8 xifres, amb digit de control opcional)."""
    items = _text_list(raw)
    if items is None or not all(_CPV.match(item) for item in items):
        return None
    return items


def _experience(sources: _Sources) -> Normalization:
    value_in = sources.value
    unmapped = _unmapped(value_in, _EXPERIENCE_KEYS)
    if unmapped:
        return _not_normalized(REASON_UNMAPPED_CONDITIONS, keys=unmapped)
    for key in ("minimum_projects", "lookback_years"):
        if value_in.get(key) is not None and _int_of(value_in.get(key)) is None:
            return _not_normalized(REASON_UNMAPPED_CONDITIONS, keys=[key])

    # `minimum_amount(_each)` es, a la lectura de sempre, l'import de CADA contracte.
    each_key = next(
        (key for key in ("minimum_amount_each", "minimum_amount") if value_in.get(key) is not None),
        None,
    )
    amount_keys = (each_key,) if each_key else ("amount",)
    amount, currency, origin, reason, present = _value_amount(sources, amount_keys, "amount")
    if present and amount is None:
        return _not_normalized(reason or REASON_AMOUNT_UNPARSEABLE)
    if not present:
        amount, origin, _quote, _candidate, quote_reason = _quote_amount(sources, "amount")
        currency = "EUR" if amount is not None else None
        if amount is None and quote_reason not in (None, REASON_NO_SOURCE):
            return _not_normalized(quote_reason)
    count = _int_of(value_in.get("minimum_projects"))
    if amount is None and count is None:
        return _not_normalized(REASON_NO_SOURCE)

    provenance = [p for p in (origin,) if p]
    individual = accumulated = None
    if amount is not None:
        accumulated = sources.literal_flag(_ACCUMULATED, "amount_basis")
        individual = sources.literal_flag(_INDIVIDUAL, "amount_basis")
        if each_key and accumulated is None:
            individual = individual or Provenance("amount_basis", "value_json", each_key)
        if (accumulated is None) == (individual is None):
            # Ni l'un ni l'altre, o tots dos: no se sap si l'import es de cada
            # contracte o de la suma, i confondre'ls canvia el veredicte.
            return _not_normalized(REASON_EXPERIENCE_BASIS_AMBIGUOUS)
        provenance.append(accumulated or individual)

    cpv_codes = _cpv_codes(value_in.get("cpv_codes"))
    services = _text_list(value_in.get("services"))
    if cpv_codes is None or services is None:
        return _not_normalized(REASON_UNMAPPED_CONDITIONS, keys=["cpv_codes/services"])

    years, aggregation, period_origin, reason = _period(sources)
    if reason:
        return _not_normalized(reason)
    lookback = _int_of(value_in.get("lookback_years"))
    if lookback is not None:
        if years not in (None, lookback):
            return _not_normalized(REASON_PERIOD_AMBIGUOUS)
        years = lookback
        provenance.append(Provenance("reference_period.years", "value_json", str(lookback)))
    percentage, base, percentage_origin, base_ambiguous = _percentage(sources)
    if base_ambiguous:
        return _not_normalized(REASON_PERCENTAGE_BASE_AMBIGUOUS)
    money = _money(amount, currency) if amount is not None else None
    if money is not None and percentage is not None:
        money["percentage"] = str(percentage)
        money["percentage_base"] = base.value
        provenance.append(percentage_origin)
    provenance += period_origin
    value: dict[str, Any] = {
        "kind": "experience",
        "reference_period": (
            {"type": "last_n_years", "years": years} if years else {"type": "unknown"}
        ),
        "aggregation": aggregation.value,
        "min_count": count,
        "min_individual_amount": money if individual else None,
        "min_accumulated_amount": money if accumulated else None,
        "cpv_codes": cpv_codes,
        "categories": services,
    }
    if count is not None:
        provenance.append(Provenance("min_count", "value_json", str(value_in["minimum_projects"])))
    note = value_in.get("similarity_condition")
    if note:
        value["similarity_note"] = str(note)[:300]
        provenance.append(Provenance("similarity_note", "value_json", str(note)))
    else:
        similar = sources.find_flag(_SIMILAR, "similarity_note")
        if similar is not None:
            # Marca de prudencia: «serveis similars» sense criteri objectiu ho ha de
            # valorar una persona (`EXPERIENCE_SIMILARITY_REVIEW`).
            value["similarity_note"] = _flat(similar.raw)[:300]
            provenance.append(similar)
    return Normalization(
        status="normalized", value_json=value, value_schema=_V2, provenance=tuple(provenance)
    )


# --------------------------------------------------------------------------- #
# Personal                                                                      #
# --------------------------------------------------------------------------- #


def _personnel(sources: _Sources) -> Normalization | None:
    for key in ("minimum_people", "count"):
        raw = sources.value.get(key)
        if isinstance(raw, int) and not isinstance(raw, bool):
            return None  # la lectura de sempre ja el llegeix
        if isinstance(raw, str) and raw.strip().isdigit():
            # Enriquir: es conserven totes les claus (un perfil concret continua
            # anant a la regla de titulacions).
            value = {**sources.value, "minimum_people": int(raw.strip())}
            return Normalization(
                status="normalized",
                value_json=value,
                provenance=(Provenance("minimum_people", "value_json", raw),),
            )
    # Una llista de perfils («cap de projecte, especialista...») no es un recompte.
    return _not_normalized(REASON_NO_COUNT)


# --------------------------------------------------------------------------- #
# ENS                                                                           #
# --------------------------------------------------------------------------- #

_ENS_STRICT = re.compile(r"\bENS\b|Esquema\s+Nacional\s+de\s+Segur(?:etat|idad)")
_LEVEL = r"(?P<level>basica|basico|basic|mitjana|mitja|media|medio|alta|alto|alt)"
_ENS_NAME = r"(?:\bens\b|esquema nacional de segur(?:etat|idad))(?:\s*\(ens\))?"
#: Paraules que poden anar ENTRE l'esquema i la categoria sense trencar el
#: sintagma: «de», «la», «categoria», «nivell», i les formes de «com a mínim».
#: Cap coma ni cap altra paraula (IR-1: «l'ENS, alta disponibilitat» no diu cap
#: categoria).
_JOIN_WORD = (
    r"(?:de|del|la|el|categoria|nivell|nivel|com a minim|como minimo|minim|minima|minimo"
    r"|igual o superior a)"
)
#: «ENS Alta», «ENS (categoria Mitjana)», «ENS - MEDIA», «ENS nivell MITJÀ»,
#: «Esquema Nacional de Seguretat de nivell bàsic», «ENS com a mínim de categoria
#: MITJANA». La categoria ha d'acabar el sintagma: «alta disponibilitat» no ho fa.
_ENS_LEVEL_AFTER = re.compile(
    _ENS_NAME
    + r"(?:\s*[(\-–:]\s*|\s+)(?P<join>(?:"
    + _JOIN_WORD
    + r"\s*:?\s+)*)"
    + _LEVEL
    + r"\b(?=\s*(?:$|[).;,:]|o\b|i\b|y\b|\())"
)
#: «categoria alta de l'ENS», «nivel medio del Esquema Nacional de Seguridad».
_ENS_LEVEL_BEFORE = re.compile(
    r"\b(?:categoria|nivell|nivel)\s*:?\s*"
    + _LEVEL
    + r"\s+(?:de|del)\s+(?:l'|la\s+|el\s+)?"
    + _ENS_NAME
)
_OR_HIGHER_JOIN = re.compile(r"minim|como minimo|igual o superior")
#: «o superior» just darrere de la categoria (o del sintagma «... de l'ENS»).
_OR_HIGHER_AFTER = re.compile(r"^\s*\)?\s*,?\s*o\s+(?:una\s+)?(?:categoria\s+)?superior\b")
#: «com a mínim (de) categoria alta de l'ENS» just davant del sintagma.
_OR_HIGHER_BEFORE = re.compile(
    r"(?:com a minim|como minimo|minim[ao]?|igual o superior a)\s*(?:de\s+)?(?:la\s+)?$"
)
ENS_LEVELS = {
    "basica": "basica", "basico": "basica", "basic": "basica",
    "mitjana": "mitjana", "mitja": "mitjana", "media": "mitjana", "medio": "mitjana",
    "alta": "alta", "alto": "alta", "alt": "alta",
}  # fmt: skip
#: Ordre de les categories de l'ENS (RD 311/2022).
ENS_ORDER = {"basica": 1, "mitjana": 2, "alta": 3}


def names_ens(text: str) -> bool:
    """Cert si el text NOMENA l'ENS (sigla en majuscules o el nom sencer)."""
    return bool(_ENS_STRICT.search(_flat(text or "")))


def ens_reading(text: str) -> tuple[str | None, bool, bool]:
    """`(categoria, ambigua, o_superior)` de l'ENS que diu un text.

    Nomes una categoria LLIGADA a l'esquema en el mateix sintagma (vegeu
    `_ENS_LEVEL_AFTER` i `_ENS_LEVEL_BEFORE`), i nomes si el text nomena l'ENS.
    «O superior» / «com a mínim» nomes compten dins d'aquell sintagma.
    """
    if not names_ens(text):
        return None, False, False
    folded = _fold(text)
    found: set[str] = set()
    or_higher = False
    for match in _ENS_LEVEL_AFTER.finditer(folded):
        found.add(ENS_LEVELS[match.group("level")])
        if _OR_HIGHER_JOIN.search(match.group("join") or ""):
            or_higher = True
        if _OR_HIGHER_AFTER.search(folded[match.end() :]):
            or_higher = True
    for match in _ENS_LEVEL_BEFORE.finditer(folded):
        found.add(ENS_LEVELS[match.group("level")])
        if _OR_HIGHER_AFTER.search(folded[match.end() :]):
            or_higher = True
        if _OR_HIGHER_BEFORE.search(folded[max(0, match.start() - 30) : match.start()]):
            or_higher = True
    if len(found) > 1:
        return None, True, False
    if not found:
        return None, False, False
    return found.pop(), False, or_higher


def ens_level(text: str) -> tuple[str | None, bool]:
    """La categoria de l'ENS que diu un text. `(categoria, ambigua)`."""
    level, ambiguous, _or_higher = ens_reading(text)
    return level, ambiguous


#: Categories on un requisit que NOMENA l'ENS es llegeix com a exigencia de
#: certificacio. A L'Escala l'ENS nomes hi apareix com a requisit tecnic.
_ENS_CATEGORIES = frozenset(
    {
        RequirementCategory.CERTIFICATION,
        RequirementCategory.TECHNICAL_REQUIREMENT,
        RequirementCategory.CONTRACT_CONDITION,
    }
)
_ENS_CERTIFICATION_KEYS = frozenset(
    {"name", "valid_at_deadline", "equivalents_allowed", "equivalence_allowed"}
)


def _ens(sources: _Sources, requirement: Any) -> Normalization | None:
    """L'ENS nomenat explicitament: certificacio, amb categoria nomes si el plec la diu."""
    texts = [("title", sources.title, None), ("description", sources.description, None)]
    texts += sources.literal_texts()
    named = next(((s, t, e) for s, t, e in texts if t and names_ens(t)), None)
    if named is None:
        return None
    if getattr(requirement, "obligation", None) != RequirementObligation.MANDATORY:
        return _not_normalized(REASON_NOT_MANDATORY)
    category = getattr(requirement, "category", None)
    if category == RequirementCategory.CERTIFICATION:
        unmapped = _unmapped(sources.value, _ENS_CERTIFICATION_KEYS)
        if unmapped:
            # `names` (una de diverses), etc.: la regla de sempre en sap mes.
            return _not_normalized(REASON_UNMAPPED_CONDITIONS, keys=unmapped)
    provenance = [Provenance("certification.scheme", named[0], named[1], named[2])]
    levels: dict[str, tuple[str, str, str | None]] = {}
    higher: dict[str, bool] = {}
    for source, text, evidence in sources.literal_texts():
        level, ambiguous, text_or_higher = ens_reading(text)
        if ambiguous:
            levels = {}
            break
        if level is not None:
            levels.setdefault(level, (source, text, evidence))
            higher[level] = higher.get(level, False) or text_or_higher
    level = None
    or_higher = False
    if len(levels) == 1:
        level, (source, text, evidence) = next(iter(levels.items()))
        provenance.append(Provenance("certification.level", source, text, evidence))
        or_higher = higher[level]
        if or_higher:
            provenance.append(Provenance("certification.or_higher", source, text, evidence))
    value = sources.value if category == RequirementCategory.CERTIFICATION else {}
    reading = EnsReading(
        level=level,
        or_higher=or_higher,
        equivalence_allowed=bool(
            value.get("equivalents_allowed")
            or value.get("equivalence_allowed")
            or getattr(requirement, "equivalence_allowed", False)
        ),
        valid_at_deadline=bool(value.get("valid_at_deadline")),
    )
    return Normalization(
        status="routed",
        route=ROUTE_ENS,
        ens=reading,
        provenance=tuple(provenance),
        detail={"scheme": "ENS", "levels_found": sorted(levels)} if len(levels) > 1 else {},
    )


# --------------------------------------------------------------------------- #
# Registres i classificacio                                                     #
# --------------------------------------------------------------------------- #

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
#: «no és necessari disposar de classificació», «la classificació no s'exigeix».
_NOT_REQUIRED = re.compile(
    r"\bno es necessari\b|\bno s'exigeix\b|\bno s'exigira\b|\bno es requereix\b"
    r"|\bno se exige\b|\bno es necesari[oa]\b|\bno sera exigible\b|\bno es exigible\b"
)
_NOT_REQUIRED_CATEGORIES = frozenset(
    {RequirementCategory.REGISTRATION, RequirementCategory.BUSINESS_CLASSIFICATION}
)


def _not_required(sources: _Sources, requirement: Any) -> Normalization | None:
    """El plec diu, literalment, que aquest registre o classificacio NO s'exigeix.

    Nomes amb text literal (value o cita) i mai per a un requisit `mandatory`.
    """
    if getattr(requirement, "obligation", None) == RequirementObligation.MANDATORY:
        return None
    origin = sources.literal_flag(_NOT_REQUIRED, "requirement.not_required")
    if origin is None:
        return None
    return Normalization(status="routed", route=ROUTE_NOT_REQUIRED, provenance=(origin,))


def _registration(sources: _Sources, requirement: Any) -> Normalization | None:
    if sources.find_flag(_RELI_ROLECE, "registry") is not None:
        subject = sources.find_flag(_REGISTERED_SUBJECT, "registry.subject")
        if subject is not None:
            # Documentacio que aporta qui JA hi es inscrit: no es l'exigencia
            # d'estar-hi. Marca de prudencia (porta a revisio), pot venir del titol.
            return Normalization(
                status="routed", route=ROUTE_REGISTRY_DOCUMENTATION_ONLY, provenance=(subject,)
            )
        return None
    # La CNMC nomes compta si ho diu el text LITERAL (mai el titol del model).
    named = sources.literal_flag(_SECTOR_OPERATOR, "registry")
    if named is not None:
        if getattr(requirement, "obligation", None) != RequirementObligation.MANDATORY:
            return _not_normalized(REASON_NOT_MANDATORY)
        value = {**sources.value, "type": "sector_operator_registry"}
        return Normalization(
            status="routed",
            route=ROUTE_SECTOR_OPERATOR_REGISTRY,
            value_json=value,
            provenance=(named,),
        )
    return _not_normalized(REASON_REGISTRY_NOT_NAMED)


# --------------------------------------------------------------------------- #
# Porta d'entrada                                                               #
# --------------------------------------------------------------------------- #

_V2 = "requirements-value-v2"


def applies(requirement: Any, profile: str | None) -> bool:
    """El complement s'aplica? Nomes product-v2 i nomes valor lliure (sense esquema)."""
    return (
        profile == NORMALIZED_PROFILE
        and getattr(requirement, "value_schema", None) is None
        and getattr(requirement, "run_id", None) is not None
    )


def normalize_requirement(requirement: Any, profile: str | None) -> Normalization | None:
    """La lectura complementaria d'un requisit `product-v2` de format lliure.

    `None` vol dir "aquest requisit no es toca": fora d'abast (`applies`) o
    categoria que cap regla llegeix. S'avalua exactament com a la v4.
    """
    if not applies(requirement, profile):
        return None
    category = getattr(requirement, "category", None)
    sources = _Sources(requirement)

    if category in _ENS_CATEGORIES:
        routed = _ens(sources, requirement)
        if routed is not None and (
            routed.status == "routed" or category == RequirementCategory.CERTIFICATION
        ):
            return routed
    if category in _NOT_REQUIRED_CATEGORIES:
        not_required = _not_required(sources, requirement)
        if not_required is not None:
            return not_required

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
    return None


def canonical(value: Any) -> str:
    """Forma canonica d'un valor per comparar-lo exactament."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


__all__ = [
    "ENS_ORDER",
    "NORMALIZED_ATTRIBUTE",
    "is_normalized",
    "NORMALIZED_PROFILE",
    "NORMALIZER_VERSION",
    "EnsReading",
    "Normalization",
    "Provenance",
    "applies",
    "canonical",
    "ens_level",
    "limit_kinds",
    "names_ens",
    "normalize_requirement",
    "parse_aggregation",
    "parse_amount",
    "parse_years",
    "quote_amounts",
]
