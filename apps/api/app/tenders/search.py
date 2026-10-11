"""Representacio de cerca normalitzada del cataleg (spec 3B-1 §7.2).

Una sola funcio, `normalize_search_text`, decideix com es compara text:

    NFKD -> fora marques diacritiques (categoria `Mn`) -> `casefold()` ->
    nomes lletres, digits i `/ . -`; la resta, espai -> espais compactats.

S'aplica a l'aplicacio, i no a la base de dades, per dues raons: es
determinista als dos motors (SQLite de proves i PostgreSQL) i no depen
d'`unaccent`, que ni es una extensio garantida ni normalitza igual que Python.
`Tender.search_text` es aquesta normalitzacio de titol, expedient, organisme i
titols dels lots vigents (la descripcio no: al feed 1044 es un text fix); `q`
passa per **exactament** la mateixa
funcio i es parteix en termes. Com que tots dos costats ja son normalitzats,
n'hi ha prou amb `search_text LIKE '%terme%'` (a PostgreSQL l'accelera l'index
GIN trigram de 0056).

Els filtres d'organisme i de lloc (`buyer_q`, `place_q`) comparen amb columnes
que no tenen cap representacio normalitzada desada. Per a aquestes hi ha
`sql_fold`: el plegat caracter a caracter que la mateixa normalitzacio fa sobre
els alfabets llatins, traduit a SQL portable. No es una segona regla: la taula
de plegat es genera a partir de `normalize_search_text`.
"""

from __future__ import annotations

import unicodedata
import uuid
from collections.abc import Iterable
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.util import identity_key
from sqlalchemy.sql.expression import ColumnElement, FunctionElement

from app.models.tenders import Buyer, Tender, TenderLot

#: Maxim de termes que es fan servir d'una consulta `q` (spec §7.3).
MAX_TERMS = 6

_KEPT_PUNCTUATION = frozenset("/.-")
#: Punt volat (U+00B7) i punt de guionatge (U+2027): l'ela geminada catalana.
_GEMINATE_DOTS = str.maketrans("", "", "\u00b7\u2027")


def _fold_chars(text: str) -> str:
    """La normalitzacio sense compactar espais (un caracter pot donar-ne mes d'un).

    L'ela geminada es plega a `ll` abans del filtre de caracters: el punt volat
    desapareix («instal·lació», «ŀl») i la substitucio habitual amb punt
    (`l.l`, «instal.lació») es llegeix igual. La resta de punts es conserven.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    folded = stripped.casefold().translate(_GEMINATE_DOTS).replace("l.l", "ll")
    return "".join(ch if ch.isalnum() or ch in _KEPT_PUNCTUATION else " " for ch in folded)


def normalize_search_text(*parts: str | None) -> str:
    """Normalitza i uneix les parts amb un espai. Les parts buides no compten."""
    text = " ".join(part for part in parts if part)
    return " ".join(_fold_chars(text).split())


def search_terms(q: str | None, max_terms: int = MAX_TERMS) -> list[str]:
    """Els termes de `q`, normalitzats, sense repetits i com a molt `max_terms`.

    Cap terme pot portar `%` ni `_`: la normalitzacio els converteix en espai.
    """
    terms: list[str] = []
    for term in normalize_search_text(q).split():
        if term not in terms:
            terms.append(term)
        if len(terms) == max_terms:
            break
    return terms


class _Searchable(Protocol):
    title: str
    expediente_number: str | None


class _LotLike(Protocol):
    lot_number: int | None
    external_lot_id: str
    title: str | None


def _lot_order(lot: _LotLike) -> tuple[bool, int, str]:
    """Ordre determinista dels lots: `lot_number` (sense numero, al final), `external_lot_id`.

    Es fa a Python i no a SQL perque els dos motors no ordenen igual els nuls.
    """
    return (lot.lot_number is None, lot.lot_number or 0, lot.external_lot_id or "")


def ordered_lot_titles(lots: Iterable[_LotLike]) -> list[str | None]:
    """Els titols dels lots en l'ordre que fan servir la ingesta i el backfill."""
    return [lot.title for lot in sorted(lots, key=_lot_order)]


def build_search_text(
    tender: _Searchable, buyer_name: str | None, lot_titles: Iterable[str | None]
) -> str | None:
    """`title ⊕ expediente ⊕ buyer.name ⊕ titols de lots`, normalitzat.

    La descripcio no hi entra: al feed agregat (1044) es un text fix («Id
    licitación…; Órgano de Contratación…; Importe…; Estado…») que fa que
    `contratacion` o `importe` trobin tot el cataleg, i el titol ja porta
    l'objecte. Els titols de lots s'esperen ja ordenats (`ordered_lot_titles`).
    """
    text = normalize_search_text(tender.title, tender.expediente_number, buyer_name, *lot_titles)
    return text or None


#: Comencament i camp obligat de la plantilla del `summary` del feed agregat, ja
#: normalitzats (`normalize_search_text`): «Id licitación: …; Órgano de
#: Contratación: …; Importe: …; Estado: …».
_TEMPLATE_HEAD = "id licitacion "
_TEMPLATE_FIELD = " organo de contratacion "


def is_feed_summary_template(text: str | None) -> bool:
    """Cert si `text` es la plantilla fixa del `summary` del feed agregat (1044).

    L'UNICA deteccio de la plantilla (3B-2 §7.1.2): la fan servir la normalitzacio
    de la ingesta (`description=None`, text cru a `source_metadata.feed_summary`) i
    el backfill dels existents. No es una descripcio de l'objecte: son metadades
    de la font (expedient, organ, import, estat) que la fitxa ja ensenya.
    """
    folded = normalize_search_text(text)
    return folded.startswith(_TEMPLATE_HEAD) and _TEMPLATE_FIELD in f"{folded} "


async def refresh_search_text(session: AsyncSession, tender: Tender) -> str | None:
    """Recalcula `tender.search_text` llegint l'organisme i els lots vigents de la BD.

    Per als camins d'escriptura que no tenen l'entrada normalitzada a ma (alta i
    edicio manual). Els lots retirats (`removed_at`) ja no son a la font: no
    compten.
    """
    buyer_name = None
    if tender.buyer_id is not None:
        buyer_name = (
            await session.execute(sa.select(Buyer.name).where(Buyer.id == tender.buyer_id))
        ).scalar_one_or_none()
    lot_titles = await live_lot_titles(session, [tender.id])
    tender.search_text = build_search_text(tender, buyer_name, lot_titles.get(tender.id, []))
    return tender.search_text


async def refresh_search_text_for_buyer(session: AsyncSession, buyer_id: uuid.UUID) -> int:
    """Recalcula `search_text` de totes les licitacions d'un organisme. Torna quantes canvia.

    Per quan canvia el nom canonic de l'organisme (ingesta): les seves altres
    licitacions s'han de trobar pel nom nou **ja**, no despres d'un backfill.
    S'executa a la transaccio de qui crida i en bloc, mai una consulta per
    licitacio: una SELECT de licitacions (amb el nom de l'organisme), una dels
    lots vigents i, si cal, un sol `UPDATE` executemany per clau primaria (un joc
    de parametres per fila). Nomes escriu les files que difereixen, de manera que
    el backfill posterior (mateixa composicio) no hi troba res a fer.

    Cap llista d'ids creix amb l'organisme: les dues SELECT filtren per
    `buyer_id` (els lots, amb una subconsulta). Un organisme amb desenes de
    milers de licitacions no topa amb el limit de parametres del motor.

    L'`UPDATE` massiu no toca els objectes ja carregats a la sessio: se'ls
    actualitza el valor en memoria com a valor ja desat, perque cap lectura
    posterior a la mateixa transaccio no en vegi un d'antic.
    """
    of_buyer = Tender.buyer_id == buyer_id
    rows = (
        await session.execute(
            sa.select(
                Tender.id, Tender.title, Tender.expediente_number, Tender.search_text, Buyer.name
            )
            .join(Buyer, Buyer.id == Tender.buyer_id)
            .where(of_buyer)
        )
    ).all()
    if not rows:
        return 0
    lot_titles = await _live_lot_titles_where(
        session, TenderLot.tender_id.in_(sa.select(Tender.id).where(of_buyer))
    )
    changes = []
    for row in rows:
        wanted = build_search_text(row, row.name, lot_titles.get(row.id, []))
        if wanted != row.search_text:
            changes.append({"id": row.id, "search_text": wanted})
    if not changes:
        return 0
    # UPDATE massiu per clau primaria (ORM 2.0): una sola sentencia executemany.
    await session.execute(sa.update(Tender), changes)
    identity_map = session.sync_session.identity_map
    for change in changes:
        loaded = identity_map.get(identity_key(Tender, change["id"]))
        if loaded is not None:
            set_committed_value(loaded, "search_text", change["search_text"])
    return len(changes)


async def live_lot_titles(
    session: AsyncSession, tender_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[str | None]]:
    if not tender_ids:
        return {}
    return await _live_lot_titles_where(session, TenderLot.tender_id.in_(tender_ids))


async def _live_lot_titles_where(
    session: AsyncSession, condition: ColumnElement[bool]
) -> dict[uuid.UUID, list[str | None]]:
    """Titols dels lots vigents de les licitacions que compleixen `condition`."""
    rows = await session.execute(
        sa.select(
            TenderLot.tender_id, TenderLot.lot_number, TenderLot.external_lot_id, TenderLot.title
        ).where(condition, TenderLot.removed_at.is_(None))
    )
    lots: dict[uuid.UUID, list[Any]] = {}
    for row in rows:
        lots.setdefault(row.tender_id, []).append(row)
    return {tender_id: ordered_lot_titles(items) for tender_id, items in lots.items()}


# --------------------------------------------------------------------------- #
# Plegat SQL per a columnes sense representacio normalitzada                    #
# --------------------------------------------------------------------------- #
#
# Garantia: per a tot text amb caracters ASCII, Latin-1 i Latin Extended-A,
# les tirades de lletres/digits/`/.-` de `sql_fold(x)` son exactament les de
# `normalize_search_text(x)`. Un terme (que nomes porta aquests caracters) hi
# cau dins d'una tirada, i per tant `sql_fold(x) LIKE '%terme%'` equival a
# `terme in normalize_search_text(x)`. La resta de puntuacio es queda tal qual
# al plegat SQL: separa tirades igual que l'espai.

_FOLD_RANGES = (range(0x00B7, 0x00B8), range(0x00C0, 0x0180), range(0x2027, 0x2028))
#: L'ela geminada amb punt (`l.l` -> `ll`), aplicada despres de `lower()` com a
#: `_fold_chars` l'aplica despres de `casefold()`.
_GEMINATE_SQL = ("l.l", "ll")


def _fold_table() -> tuple[tuple[str, str], ...]:
    pairs = []
    for block in _FOLD_RANGES:
        for codepoint in block:
            char = chr(codepoint)
            folded = _fold_chars(char)
            if folded != char:
                pairs.append((char, folded))
    return tuple(pairs)


_FOLD_TABLE = _fold_table()
_SINGLE = tuple((src, dst) for src, dst in _FOLD_TABLE if len(dst) == 1)
_MULTI = tuple((src, dst) for src, dst in _FOLD_TABLE if len(dst) != 1)


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


class sql_fold(FunctionElement):  # noqa: N801 - s'usa com una funcio SQL
    """`lower()` del text amb els caracters llatins plegats com ho fa la normalitzacio."""

    type = sa.String()
    inherit_cache = True
    name = "sql_fold"


@compiles(sql_fold)
def _compile_fold_generic(element: Any, compiler: Any, **kw: Any) -> str:
    # SQLite no te `translate`: una cadena de `replace`, literals i sense parametres.
    sql = compiler.process(next(iter(element.clauses)), **kw)
    for src, dst in _FOLD_TABLE:
        sql = f"replace({sql}, {_quote(src)}, {_quote(dst)})"
    src, dst = _GEMINATE_SQL
    return f"replace(lower({sql}), {_quote(src)}, {_quote(dst)})"


@compiles(sql_fold, "postgresql")
def _compile_fold_postgresql(element: Any, compiler: Any, **kw: Any) -> str:
    # A PostgreSQL, un sol `translate` per als plegats d'un caracter i `replace`
    # nomes per als que en donen mes d'un o cap (Œ -> oe, ß -> ss, · -> "").
    sql = compiler.process(next(iter(element.clauses)), **kw)
    for src, dst in _MULTI:
        sql = f"replace({sql}, {_quote(src)}, {_quote(dst)})"
    sources = "".join(src for src, _ in _SINGLE)
    targets = "".join(dst for _, dst in _SINGLE)
    src, dst = _GEMINATE_SQL
    return (
        f"replace(lower(translate({sql}, {_quote(sources)}, {_quote(targets)})), "
        f"{_quote(src)}, {_quote(dst)})"
    )


def fold_matches(column: ColumnElement[Any], terms: list[str]) -> ColumnElement[bool]:
    """Cada terme (ja normalitzat) apareix al plegat de la columna."""
    folded = sql_fold(column)
    return sa.and_(*[folded.like(f"%{term}%") for term in terms])
