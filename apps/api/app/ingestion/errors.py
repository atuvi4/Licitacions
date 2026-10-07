"""Classificacio segura dels errors de la ingesta.

Un error d'una unitat (entrada o lapida), d'una pagina o del run es redueix a
una classe estable i a uns pocs identificadors que mai no porten dades:

  * `parse_validation`     ParseError del parser, ValidationError (pydantic).
  * `db_data`              valor que la base no admet (SQLSTATE 22xxx, DataError):
                           p. ex. StringDataRightTruncation (valor massa llarg).
  * `integrity_constraint` clau unica, clau forana, NOT NULL... (23xxx, IntegrityError).
  * `network_source`       la font no respon o respon malament (FeedFetchError, httpx).
  * `unexpected`           qualsevol altra cosa.

Per als errors de base de dades es conserva el nom de la classe DBAPI REAL (la
d'asyncpg/psycopg, no l'embolcall generic de SQLAlchemy), el SQLSTATE i, si es
pot inferir de manera segura, `taula.columna` o el nom del constraint. Mai no es
copia `str(exc)`: porta l'SQL complet i els parametres (titols, NIF, XML...).
La taula i la columna nomes s'accepten si existeixen al model; de l'SQL i dels
parametres nomes se'n llegeixen identificadors i longituds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

import sqlalchemy as sa

PARSE_VALIDATION = "parse_validation"
DB_DATA = "db_data"
INTEGRITY_CONSTRAINT = "integrity_constraint"
NETWORK_SOURCE = "network_source"
UNEXPECTED = "unexpected"

#: Ordre estable (tambe el del resum per classe).
ERROR_CLASSES = (PARSE_VALIDATION, DB_DATA, INTEGRITY_CONSTRAINT, NETWORK_SOURCE, UNEXPECTED)

#: Classes transitories: es reintenten sempre que la mateixa publicacio torna a
#: arribar. Les altres (`db_data`, `integrity_constraint`, `parse_validation`) son
#: deterministes: nomes es reintenten si canvia el parser o l'esquema (vegeu
#: `app.ingestion.placsp.service._existing_verdict`).
TRANSIENT_CLASSES = frozenset({NETWORK_SOURCE, UNEXPECTED})
DETERMINISTIC_CLASSES = frozenset({PARSE_VALIDATION, DB_DATA, INTEGRITY_CONSTRAINT})

MAX_LINE = 200
_MAX_DETAIL = 120
_NETWORK_REASON = "feed no disponible"

_IDENT = r"[A-Za-z_][A-Za-z0-9_]{0,62}"
_IDENT_RE = re.compile(rf"^{_IDENT}$")
_SQLSTATE_RE = re.compile(r"^[0-9A-Z]{5}$")
_WIDTH_RE = re.compile(r"character varying\((\d+)\)")
_INSERT_RE = re.compile(rf"^\s*INSERT INTO ({_IDENT}) \(([^)]*)\) VALUES (.*)$", re.S)
_UPDATE_RE = re.compile(rf"^\s*UPDATE ({_IDENT}) SET (.*?)(?: WHERE .*)?$", re.S)
#: Marcadors de parametre: `$n` (asyncpg), `?` (sqlite) i `%(nom)s` (psycopg).
_PLACEHOLDER_RE = re.compile(r"\$(\d+)|\?|%\((\w+)\)s")

_CLASSES_RE = "|".join(ERROR_CLASSES)
#: Gramatica de les linies segures: `[pagina N: ]classe: Excepcio[ (SQLSTATE)][ objectiu]`
#: i, nomes per a `parse_validation` i `network_source`, `: motiu` (text propi, curt).
_LINE_RE = re.compile(
    rf"^(?:pagina \d+: )?(?P<klass>{_CLASSES_RE}): (?P<exc>{_IDENT})"
    rf"(?: \([0-9A-Z]{{5}}\))?(?: {_IDENT}(?:\.{_IDENT})?)?(?:: (?P<detail>[^\n]+))?$"
)
_SUMMARY_ITEM = (
    rf"\d+ (?:{_CLASSES_RE}) \({_IDENT}(?: [0-9A-Z]{{5}})?(?:, {_IDENT}(?:\.{_IDENT})?)?\)"
)
SUMMARY_RE = re.compile(rf"^{_SUMMARY_ITEM}(?: \+ {_SUMMARY_ITEM})*$")
#: «N fallades conegudes (classe, ...)»: fallades deterministes ja conegudes que
#: aquest run no ha reintentat.
KNOWN_FAILURES_RE = re.compile(
    rf"^\d+ fallades conegudes \((?:{_CLASSES_RE})(?:, (?:{_CLASSES_RE}))*\)$"
)


@dataclass(frozen=True, slots=True)
class SafeError:
    error_class: str
    exception: str
    sqlstate: str | None = None
    #: `taula.columna`, `taula` o el nom d'un constraint; nomes identificadors del model.
    target: str | None = None
    #: Nomes per a `parse_validation` (missatge propi del parser) i `network_source`.
    detail: str | None = None

    def label(self) -> str:
        """`Excepcio[ SQLSTATE][, objectiu]` (per al resum per classe)."""
        text = self.exception + (f" {self.sqlstate}" if self.sqlstate else "")
        return f"{text}, {self.target}" if self.target else text

    def line(self, prefix: str | None = None) -> str:
        text = f"{self.error_class}: {self.exception}"
        if self.sqlstate:
            text += f" ({self.sqlstate})"
        if self.target:
            text += f" {self.target}"
        if self.detail:
            text += f": {self.detail}"
        if prefix:
            text = f"{prefix}: {text}"
        return text[:MAX_LINE]


def classify_exception(exc: BaseException) -> SafeError:
    """La classe estable i els identificadors segurs d'una excepcio."""
    from app.ingestion.placsp.client import FeedFetchError
    from app.ingestion.placsp.parser import ParseError

    name = type(exc).__name__
    if isinstance(exc, ParseError):
        return SafeError(PARSE_VALIDATION, name, detail=_parser_detail(exc))
    if _is_validation_error(exc):
        # El missatge de pydantic inclou l'entrada: nomes la classe.
        return SafeError(PARSE_VALIDATION, name)
    if isinstance(exc, FeedFetchError):
        return SafeError(NETWORK_SOURCE, name, detail=_NETWORK_REASON)
    if _is_httpx_error(exc):
        return SafeError(NETWORK_SOURCE, name)
    if isinstance(exc, sa.exc.DBAPIError):
        return _classify_db(exc)
    return SafeError(UNEXPECTED, name)


def safe_error_line(line: str) -> str | None:
    """La linia si segueix la gramatica segura (<= 200 caracters); si no, `None`."""
    match = _LINE_RE.match(line)
    if match is None or len(line) > MAX_LINE:
        return None
    if match.group("detail") is not None and match.group("klass") not in (
        PARSE_VALIDATION,
        NETWORK_SOURCE,
    ):
        return None
    return line


def summary_line(errors: Iterable[SafeError]) -> str | None:
    """`N classe (primer error representatiu) + M classe (...)`, en l'ordre estable."""
    counts: dict[str, int] = {}
    first: dict[str, SafeError] = {}
    for error in errors:
        counts[error.error_class] = counts.get(error.error_class, 0) + 1
        first.setdefault(error.error_class, error)
    parts = [
        f"{counts[klass]} {klass} ({first[klass].label()})"
        for klass in ERROR_CLASSES
        if klass in counts
    ]
    if not parts:
        return None
    text = ""
    for part in parts:  # mai un element tallat a mitges
        candidate = f"{text} + {part}" if text else part
        if len(candidate) > MAX_LINE:
            break
        text = candidate
    return text or None


def known_failures_line(classes: Iterable[str]) -> str | None:
    """`N fallades conegudes (classe, ...)`, classes diferents en l'ordre estable."""
    seen = list(classes)
    if not seen:
        return None
    present = [klass for klass in ERROR_CLASSES if klass in seen]
    return f"{len(seen)} fallades conegudes ({', '.join(present)})"


def error_class_of_message(message: str | None) -> str | None:
    """La classe d'un `error_message` desat amb aquest format (o `None`)."""
    if not message:
        return None
    head = message.split(":", 1)[0]
    return head if head in ERROR_CLASSES else None


# --------------------------------------------------------------------------- #
# Interns                                                                       #
# --------------------------------------------------------------------------- #


def _parser_detail(exc: BaseException) -> str | None:
    """Els missatges del parser son textos propis: es conserven curts i en una linia."""
    text = " ".join(str(exc).split())
    return text[:_MAX_DETAIL] or None


def _is_validation_error(exc: BaseException) -> bool:
    try:
        import pydantic
    except ImportError:  # pragma: no cover - pydantic es dependencia del projecte
        return False
    return isinstance(exc, pydantic.ValidationError)


def _is_httpx_error(exc: BaseException) -> bool:
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return False
    return isinstance(exc, httpx.HTTPError)


def _classify_db(exc: sa.exc.DBAPIError) -> SafeError:
    orig = exc.orig
    # asyncpg: SQLAlchemy l'embolcalla en una classe generica i deixa l'original a
    # `__cause__`; psycopg/sqlite3: `orig` ja es la classe real.
    real = orig.__cause__ if isinstance(getattr(orig, "__cause__", None), Exception) else orig
    name = type(real).__name__ if real is not None else type(exc).__name__
    if not _IDENT_RE.match(name):
        name = type(exc).__name__
    sqlstate = getattr(real, "sqlstate", None) or getattr(orig, "sqlstate", None)
    sqlstate = sqlstate if isinstance(sqlstate, str) and _SQLSTATE_RE.match(sqlstate) else None

    if (sqlstate and sqlstate.startswith("22")) or isinstance(exc, sa.exc.DataError):
        klass = DB_DATA
    elif (sqlstate and sqlstate.startswith("23")) or isinstance(exc, sa.exc.IntegrityError):
        klass = INTEGRITY_CONSTRAINT
    else:
        return SafeError(UNEXPECTED, name, sqlstate)

    table, columns = _statement_columns(exc.statement)
    target = None
    if klass == INTEGRITY_CONSTRAINT:
        constraint = getattr(real, "constraint_name", None)
        if isinstance(constraint, str) and _IDENT_RE.match(constraint):
            target = constraint
    if target is None and table is not None:
        target = table
        if klass == DB_DATA:
            column = _too_long_column(table, columns, exc.params, real)
            if column is not None:
                target = f"{table}.{column}"
    return SafeError(klass, name, sqlstate, target)


def _model_table(name: str) -> sa.Table | None:
    from app.models import Base  # totes les taules registrades a la metadata

    return Base.metadata.tables.get(name)


def _split_top_level(text: str) -> list[str]:
    parts, depth, current = [], 0, []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return [part.strip() for part in parts]


def _statement_columns(
    statement: str | None,
) -> tuple[str | None, list[tuple[str, int | str]]]:
    """Taula i parelles (columna, posicio o nom del parametre) d'un INSERT/UPDATE propi.

    Nomes es llegeixen identificadors i posicions de marcadors (`$n` o `?`); els
    valors no surten mai d'aqui. La taula s'accepta nomes si es del model.
    """
    if not statement:
        return None, []
    pairs: list[tuple[str, str]] = []
    insert = _INSERT_RE.match(statement)
    update = None if insert else _UPDATE_RE.match(statement)
    if insert:
        table = insert.group(1)
        columns = [c.strip().strip('"') for c in insert.group(2).split(",")]
        values = insert.group(3)
        rows = re.findall(r"\(((?:[^()]|\([^()]*\))*)\)", values)
        for row in rows:
            expressions = _split_top_level(row)
            if len(expressions) != len(columns):
                return (table if _model_table(table) is not None else None), []
            pairs.extend(zip(columns, expressions, strict=True))
    elif update:
        table = update.group(1)
        for assignment in _split_top_level(update.group(2)):
            column, _, expression = assignment.partition("=")
            pairs.append((column.strip().strip('"'), expression))
    else:
        return None, []
    if _model_table(table) is None:
        return None, []

    result: list[tuple[str, int | str]] = []
    sequential = 0
    for column, expression in pairs:
        for match in _PLACEHOLDER_RE.finditer(expression):
            key: int | str
            if match.group(1) is not None:
                key = int(match.group(1)) - 1
            elif match.group(2) is not None:
                key = match.group(2)
            else:
                key = sequential
                sequential += 1
            if _IDENT_RE.match(column):
                result.append((column, key))
    return table, result


def _too_long_column(
    table: str, columns: list[tuple[str, int | str]], params, real: BaseException | None
) -> str | None:
    """La columna el valor de la qual supera l'amplada, si n'hi ha exactament una.

    Dels parametres nomes se'n mira el tipus i la longitud.
    """
    model = _model_table(table)
    if model is None or not columns:
        return None
    if isinstance(params, dict):
        values = params
    elif isinstance(params, (list, tuple)):
        values = dict(enumerate(params))
    else:
        return None
    # L'amplada que la base diu que s'ha superat (p. ex. `character varying(1000)`):
    # nomes se n'extreu el numero.
    width_match = _WIDTH_RE.search(str(real)) if real is not None else None
    width = int(width_match.group(1)) if width_match else None

    candidates: list[str] = []
    for column, key in columns:
        if column not in model.c:
            continue
        value = values.get(key)
        if not isinstance(value, str):
            continue
        declared = getattr(model.c[column].type, "length", None)
        limit = width or declared
        if limit is not None and len(value) > limit and column not in candidates:
            candidates.append(column)
    if len(candidates) > 1 and width is not None:
        # Desempat nomes amb una pista del model (mateixa amplada declarada).
        candidates = [
            column
            for column in candidates
            if getattr(model.c[column].type, "length", None) == width
        ]
    return candidates[0] if len(candidates) == 1 else None
