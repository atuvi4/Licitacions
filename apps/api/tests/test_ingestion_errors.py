"""Classificacio segura dels errors de la ingesta (hotfix radar, item 3).

Cada error d'una unitat (entrada o lapida), d'una pagina o del run es redueix a
una classe estable (`parse_validation`, `db_data`, `integrity_constraint`,
`network_source`, `unexpected`) i a uns pocs identificadors segurs: el nom de la
classe DBAPI real, el SQLSTATE i, si es pot inferir, `taula.columna`. Mai SQL,
parametres, secrets ni XML.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx
import pydantic
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql.asyncpg import AsyncAdapt_asyncpg_dbapi

from app.ingestion.errors import (
    DB_DATA,
    ERROR_CLASSES,
    INTEGRITY_CONSTRAINT,
    NETWORK_SOURCE,
    PARSE_VALIDATION,
    UNEXPECTED,
    classify_exception,
    safe_error_line,
    summary_line,
)
from app.ingestion.placsp.client import FeedFetchError
from app.ingestion.placsp.parser import ParseError

LONG_TITLE = "Dializador de superficie mayor o igual a 2,3 m2 " * 25  # > 1000, dades d'entrada
SECRET_PARAM = "B12345678"
FORBIDDEN = (
    "INSERT",
    "UPDATE ",
    "SELECT",
    "VALUES",
    "$1",
    "?",
    "parameters",
    "Dializador",
    SECRET_PARAM,
    "<",
    "sqlalche.me",
    "character varying",
)


def _asyncpg_dbapi_error(pg_error: Exception, statement: str, params: tuple) -> sa.exc.DBAPIError:
    """Com ho fa el dialecte asyncpg de SQLAlchemy (`_handle_exception`)."""
    translated = AsyncAdapt_asyncpg_dbapi.Error(f"{type(pg_error)}: {pg_error}")
    translated.pgcode = translated.sqlstate = pg_error.sqlstate
    translated.__cause__ = pg_error
    return sa.exc.DBAPIError(statement, params, translated)


def _assert_safe(text: str) -> None:
    assert len(text) <= 200
    for forbidden in FORBIDDEN:
        assert forbidden not in text, (forbidden, text)


def test_les_classes_son_estables():
    assert ERROR_CLASSES == (
        "parse_validation",
        "db_data",
        "integrity_constraint",
        "network_source",
        "unexpected",
    )


def test_titol_massa_llarg_a_postgres_es_db_data_amb_classe_real_i_columna():
    """La forma exacta del pilot: asyncpg StringDataRightTruncation dins un DBAPIError generic."""
    lot_id, tender_id = uuid.uuid4(), uuid.uuid4()
    statement = (
        "INSERT INTO tender_lots (tender_id, external_lot_id, lot_number, title, description, "
        "estimated_value, base_budget, base_budget_without_tax, currency, place_of_execution, "
        "nuts_code, deadline_at, status, removed_at, owner_company_id, id) VALUES ($1::UUID, "
        "$2::VARCHAR, $3::INTEGER, $4::VARCHAR, $5::VARCHAR, $6::NUMERIC(18, 2), "
        "$7::NUMERIC(18, 2), $8::NUMERIC(18, 2), $9::VARCHAR, $10::VARCHAR, $11::VARCHAR, "
        "$12::TIMESTAMP WITH TIME ZONE, $13::VARCHAR, $14::TIMESTAMP WITH TIME ZONE, "
        "$15::UUID, $16::UUID) RETURNING tender_lots.created_at, tender_lots.updated_at"
    )
    params = (
        tender_id,
        "1",
        1,
        LONG_TITLE,
        None,
        None,
        None,
        None,
        "EUR",
        SECRET_PARAM,
        None,
        None,
        None,
        None,
        None,
        lot_id,
    )
    pg_error = asyncpg.exceptions.StringDataRightTruncationError(
        "value too long for type character varying(1000)"
    )
    error = classify_exception(_asyncpg_dbapi_error(pg_error, statement, params))

    assert error.error_class == DB_DATA
    assert error.exception == "StringDataRightTruncationError"
    assert error.sqlstate == "22001"
    assert error.target == "tender_lots.title"
    assert error.line() == "db_data: StringDataRightTruncationError (22001) tender_lots.title"
    _assert_safe(error.line())


def test_update_massa_llarg_infereix_la_columna_del_set():
    statement = (
        "UPDATE tenders SET title=$1::VARCHAR, search_text=$2::VARCHAR, "
        "updated_at=now() WHERE tenders.id = $3::UUID"
    )
    pg_error = asyncpg.exceptions.StringDataRightTruncationError(
        "value too long for type character varying(1000)"
    )
    error = classify_exception(
        _asyncpg_dbapi_error(pg_error, statement, (LONG_TITLE, "curt", uuid.uuid4()))
    )
    assert error.target == "tenders.title"

    # Dos valors candidats i cap pista al model: no s'endevina la columna, nomes la taula.
    ambiguous = classify_exception(
        _asyncpg_dbapi_error(pg_error, statement, (LONG_TITLE, LONG_TITLE * 3, uuid.uuid4()))
    )
    assert ambiguous.target == "tenders"


def test_sense_poder_inferir_la_columna_queda_la_taula_o_res():
    pg_error = asyncpg.exceptions.StringDataRightTruncationError("value too long")
    only_table = classify_exception(
        _asyncpg_dbapi_error(pg_error, "INSERT INTO tender_lots (title) VALUES ($1::VARCHAR)", ())
    )
    assert only_table.target == "tender_lots"
    unknown_table = classify_exception(
        _asyncpg_dbapi_error(pg_error, "INSERT INTO taula_inventada (x) VALUES ($1)", ("x",))
    )
    assert unknown_table.target is None
    assert unknown_table.error_class == DB_DATA


def test_violacio_d_unicitat_a_postgres_es_integrity_constraint_amb_el_nom_del_constraint():
    pg_error = asyncpg.exceptions.UniqueViolationError.new(
        {
            "C": "23505",
            "M": f'duplicate key value violates unique constraint "uq_tender_lots_tender_external" '
            f"Key (tender_id, external_lot_id)=({SECRET_PARAM}, 1) already exists.",
            "n": "uq_tender_lots_tender_external",
            "t": "tender_lots",
        }
    )
    translated = AsyncAdapt_asyncpg_dbapi.IntegrityError(f"{type(pg_error)}: {pg_error}")
    translated.pgcode = translated.sqlstate = pg_error.sqlstate
    translated.__cause__ = pg_error
    exc = sa.exc.IntegrityError(
        "INSERT INTO tender_lots (tender_id, external_lot_id) VALUES ($1::UUID, $2::VARCHAR)",
        (uuid.uuid4(), "1"),
        translated,
    )
    error = classify_exception(exc)
    assert error.error_class == INTEGRITY_CONSTRAINT
    assert error.exception == "UniqueViolationError"
    assert error.sqlstate == "23505"
    assert error.target == "uq_tender_lots_tender_external"
    _assert_safe(error.line())


def test_violacio_d_unicitat_a_sqlite():
    import sqlite3

    orig = sqlite3.IntegrityError(
        "UNIQUE constraint failed: tender_lots.tender_id, tender_lots.external_lot_id"
    )
    exc = sa.exc.IntegrityError(
        "INSERT INTO tender_lots (tender_id, external_lot_id) VALUES (?, ?)", ("x", "1"), orig
    )
    error = classify_exception(exc)
    assert error.error_class == INTEGRITY_CONSTRAINT
    assert error.exception == "IntegrityError"
    assert error.target == "tender_lots"
    _assert_safe(error.line())


def test_data_error_de_sqlalchemy_es_db_data():
    exc = sa.exc.DataError("SELECT 1", {"p": SECRET_PARAM}, Exception("bad value " + SECRET_PARAM))
    error = classify_exception(exc)
    assert error.error_class == DB_DATA
    _assert_safe(error.line())


def test_errors_de_parseig_i_validacio():
    parse = classify_exception(ParseError("Entrada sense <id>."))
    assert parse.error_class == PARSE_VALIDATION
    assert parse.line() == "parse_validation: ParseError: Entrada sense <id>."

    class Model(pydantic.BaseModel):
        value: int

    with pytest.raises(pydantic.ValidationError) as caught:
        Model(value=SECRET_PARAM)
    validation = classify_exception(caught.value)
    assert validation.error_class == PARSE_VALIDATION
    assert validation.exception == "ValidationError"
    # El missatge de pydantic porta l'entrada: no se'n copia res.
    _assert_safe(validation.line())


def test_errors_de_xarxa_o_de_la_font():
    fetch = classify_exception(FeedFetchError(f"HTTP 500 a https://x/{SECRET_PARAM}"))
    assert fetch.error_class == NETWORK_SOURCE
    assert fetch.line() == "network_source: FeedFetchError: feed no disponible"
    transport = classify_exception(httpx.ConnectError(f"connect to {SECRET_PARAM} failed"))
    assert transport.error_class == NETWORK_SOURCE
    assert transport.exception == "ConnectError"
    _assert_safe(transport.line())


def test_la_resta_es_unexpected_i_no_copia_el_missatge():
    from sqlalchemy.exc import MissingGreenlet

    for exc in (
        RuntimeError(f"SELECT * FROM x WHERE nif = '{SECRET_PARAM}'"),
        MissingGreenlet("greenlet_spawn has not been called"),
        sa.exc.ProgrammingError("SELECT * FROM taula", (SECRET_PARAM,), Exception("no table")),
    ):
        error = classify_exception(exc)
        assert error.error_class == UNEXPECTED
        assert error.line() == f"unexpected: {error.exception}"
        _assert_safe(error.line())


def test_resum_per_classe_amb_el_primer_error_representatiu():
    first = classify_exception(
        _asyncpg_dbapi_error(
            asyncpg.exceptions.StringDataRightTruncationError(
                "value too long for type character varying(1000)"
            ),
            "INSERT INTO tender_lots (title) VALUES ($1::VARCHAR)",
            (LONG_TITLE,),
        )
    )
    other = classify_exception(RuntimeError("boom"))
    line = summary_line([first, other, other, first])
    assert line == (
        "2 db_data (StringDataRightTruncationError 22001, tender_lots.title)"
        " + 2 unexpected (RuntimeError)"
    )
    _assert_safe(line)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "db_data: StringDataRightTruncationError (22001) tender_lots.title",
            "db_data: StringDataRightTruncationError (22001) tender_lots.title",
        ),
        (
            "pagina 3: network_source: FeedFetchError: feed no disponible",
            "pagina 3: network_source: FeedFetchError: feed no disponible",
        ),
        ("unexpected: RuntimeError", "unexpected: RuntimeError"),
        # Un text que no segueix la gramatica no passa el filtre.
        ("db_data: StringDataRightTruncationError: INSERT INTO x VALUES ('B12345678')", None),
        ("unexpected: RuntimeError: boom B12345678", None),
        ("IntegrityError: (x) INSERT ...", None),
    ],
)
def test_nomes_les_linies_amb_la_gramatica_segura_passen(line, expected):
    assert safe_error_line(line) == expected
