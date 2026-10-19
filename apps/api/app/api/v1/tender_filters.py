"""Filtres i ordre sobre licitacions, compartits pels dos feeds.

Hi ha dos feeds que llisten les mateixes licitacions:

- `/tenders`: el cataleg public, sense cap relacio amb l'empresa.
- `/companies/{id}/matches`: el mateix cataleg amb el pre-fit al costat.

Els criteris de cerca han de ser identics als dos llocs — si no, l'usuari
filtra per CPV al cataleg, canvia a la vista de pre-fit i li desapareixen
resultats sense motiu. Per aixo viuen aqui i no duplicats a cada router: els
filtres (`TenderFilters`), l'ordre (`catalogue_order`), el recompte d'exclosos
per import (`without_amount_clause`) i el context de cada fila
(`load_row_context`).

Regla de «Totes» (spec 3B-1 §7.1): cap filtre s'aplica si l'usuari no l'ha
fixat, i l'ordre per defecte es neutral — publicacio desc (nuls al final),
`first_seen_at` desc, id. El pre-fit mai decideix que hi entra.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal

import sqlalchemy as sa
from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cpv import CPVCode
from app.models.enums import ContractType, ProcedureType, TenderStatus
from app.models.sources import Source
from app.models.tenders import (
    Buyer,
    Tender,
    TenderCPV,
    TenderDocument,
    TenderLot,
    TenderLotCPV,
)
from app.schemas.tenders import BuyerSummary, TenderCPVRead, TenderListItem
from app.tenders.amounts import useful_amount_sql
from app.tenders.documents import ACTIVE_DOCUMENT
from app.tenders.search import fold_matches, normalize_search_text, search_terms

#: Ordres del cataleg. `recent` es l'alies historic de `first_seen` (la web
#: anterior a 3B-1 l'envia); `relevance` nomes ordena amb `q` i a PostgreSQL;
#: `score` nomes te sentit amb empresa (al feed public cau a l'ordre neutral).
CatalogueSort = Literal[
    "published",
    "relevance",
    "deadline",
    "amount",
    "first_seen",
    "source_updated",
    "score",
    "recent",
]

DEFAULT_SORT: CatalogueSort = "published"

_NOT_DIGIT = re.compile(r"\D")


def amount_expression() -> sa.ColumnElement:
    """L'import d'una licitacio per filtrar i ordenar (spec §7.3).

    VEC -> PBL sense IVA -> PBL amb IVA, saltant els que no son utils (<= 0): un
    `0.00` publicat per la PLACSP es com si no hi fos (`app.tenders.amounts`). Sense
    cap import util, l'import es NULL i la licitacio compta com a «sense import
    conegut» (`without_amount_criteria`).
    """
    return sa.func.coalesce(
        useful_amount_sql(Tender.estimated_value),
        useful_amount_sql(Tender.base_budget_without_tax),
        useful_amount_sql(Tender.base_budget),
    )


def _cpv_prefixes(codes: list[str]) -> list[str]:
    """Nomes digits, com a molt els 8 del codi (sense el digit de control)."""
    prefixes: list[str] = []
    for code in codes:
        digits = _NOT_DIGIT.sub("", code)[:8]
        if digits and digits not in prefixes:
            prefixes.append(digits)
    return prefixes


@dataclass(slots=True)
class TenderFilters:
    """Criteris de cerca sobre licitacions. FastAPI els injecta com a dependencia."""

    q: str | None = None
    cpv: list[str] | None = None
    buyer_id: uuid.UUID | None = None
    buyer_q: str | None = None
    status: list[TenderStatus] | None = None
    procedure_type: list[ProcedureType] | None = None
    contract_type: list[ContractType] | None = None
    nuts: str | None = None
    place_q: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    published_from: date | None = None
    published_to: date | None = None
    deadline_from: datetime | None = None
    deadline_to: datetime | None = None
    has_lots: bool | None = None
    only_open: bool = False
    # Estat accionable «En termini» (revisio 3B-2, F2): NO es l'estat cru de la
    # font. Publicada (`open`, PLACSP `PUB`) I termini conegut posterior a ara,
    # per instant exacte. Sense termini, mai; un estat final de la font, mai.
    in_term: bool = False
    source: list[str] | None = None
    source_removed: bool | None = None

    @property
    def filters_amount(self) -> bool:
        """Si l'usuari ha fixat un filtre d'import (nomes llavors s'exclouen nuls)."""
        return self.min_amount is not None or self.max_amount is not None

    def criteria(self, *, include_amount: bool = True) -> list:
        """Clausules SQLAlchemy sobre `Tender`. Buida si no s'ha filtrat res."""
        clauses = []
        # `search_text` ja es normalitzat: LIKE de cada terme normalitzat de `q`.
        clauses.extend(Tender.search_text.like(f"%{term}%") for term in search_terms(self.q))
        if self.cpv:
            clauses.append(_cpv_clause(_cpv_prefixes(self.cpv)))
        if self.buyer_id:
            clauses.append(Tender.buyer_id == self.buyer_id)
        buyer_terms = search_terms(self.buyer_q)
        if buyer_terms:
            clauses.append(
                Tender.buyer_id.in_(
                    sa.select(Buyer.id).where(fold_matches(Buyer.name, buyer_terms))
                )
            )
        if self.status:
            clauses.append(Tender.status.in_(self.status))
        if self.procedure_type:
            clauses.append(Tender.procedure_type.in_(self.procedure_type))
        if self.contract_type:
            clauses.append(Tender.contract_type.in_(self.contract_type))
        if self.nuts:
            clauses.append(Tender.nuts_code.like(f"{self.nuts.upper()}%"))
        place_terms = search_terms(self.place_q)
        if place_terms:
            clauses.append(
                sa.or_(
                    fold_matches(Tender.place_of_execution, place_terms),
                    sa.select(TenderLot.id)
                    .where(
                        TenderLot.tender_id == Tender.id,
                        TenderLot.removed_at.is_(None),
                        fold_matches(TenderLot.place_of_execution, place_terms),
                    )
                    .exists(),
                )
            )
        if include_amount:
            amount = amount_expression()
            if self.min_amount is not None:
                clauses.append(amount >= self.min_amount)
            if self.max_amount is not None:
                clauses.append(amount <= self.max_amount)
        if self.published_from:
            clauses.append(Tender.publication_date >= self.published_from)
        if self.published_to:
            clauses.append(Tender.publication_date <= self.published_to)
        if self.deadline_from:
            clauses.append(Tender.deadline_at >= self.deadline_from)
        if self.deadline_to:
            clauses.append(Tender.deadline_at <= self.deadline_to)
        if self.only_open:
            clauses.append(Tender.deadline_at >= sa.func.now())
        if self.in_term:
            # La mateixa regla que `deadline_has_passed`: l'instant, no el dia.
            # `deadline_at > ara` exclou els nuls (termini desconegut).
            clauses.append(Tender.status == TenderStatus.OPEN)
            clauses.append(Tender.deadline_at > datetime.now(UTC))
        if self.has_lots is not None:
            # Lots vigents, com `lot_count`: un lot retirat de la font ja no hi es.
            exists = (
                sa.select(TenderLot.id)
                .where(TenderLot.tender_id == Tender.id, TenderLot.removed_at.is_(None))
                .exists()
            )
            clauses.append(exists if self.has_lots else sa.not_(exists))
        if self.source:
            clauses.append(
                Tender.source_id.in_(sa.select(Source.id).where(Source.code.in_(self.source)))
            )
        if self.source_removed is not None:
            clauses.append(
                Tender.source_removed_at.is_not(None)
                if self.source_removed
                else Tender.source_removed_at.is_(None)
            )
        return clauses

    def without_amount_criteria(self) -> list:
        """Les files que el filtre d'import exclou nomes per no tenir import.

        La resta de filtres hi son aplicats: es el recompte que la UI explica
        com «N sense import conegut no es mostren».
        """
        return [*self.criteria(include_amount=False), amount_expression().is_(None)]


def _cpv_clause(prefixes: list[str]) -> sa.ColumnElement[bool]:
    """Exacte o prefix, a la licitacio o a qualsevol lot vigent."""
    if not prefixes:
        # S'ha fixat un filtre de CPV que no conte cap codi: no s'ignora, que
        # tornaria tot el cataleg com si no s'hagues filtrat.
        return sa.false()
    on_tender = sa.or_(*[TenderCPV.cpv_code.like(f"{prefix}%") for prefix in prefixes])
    on_lot = sa.or_(*[TenderLotCPV.cpv_code.like(f"{prefix}%") for prefix in prefixes])
    return sa.or_(
        Tender.id.in_(sa.select(TenderCPV.tender_id).where(on_tender)),
        Tender.id.in_(
            sa.select(TenderLot.tender_id)
            .join(TenderLotCPV, TenderLotCPV.tender_lot_id == TenderLot.id)
            .where(TenderLot.removed_at.is_(None), on_lot)
        ),
    )


def neutral_order() -> list:
    """`published desc nulls last, first_seen_at desc, id` (spec §7.1)."""
    return [
        Tender.publication_date.desc().nullslast(),
        Tender.first_seen_at.desc(),
        Tender.id.asc(),
    ]


def catalogue_order(
    sort: CatalogueSort,
    q: str | None,
    *,
    dialect_name: str,
    score: sa.ColumnElement | None = None,
) -> list:
    """L'ordre demanat, sempre amb el desempat neutral al darrere.

    `relevance` es `similarity(search_text, q normalitzada)` i nomes a
    PostgreSQL (`pg_trgm`); sense `q` o a SQLite cau a l'ordre neutral. `score`
    sense pre-fit (feed public) tambe.
    """
    primary: list = []
    if sort == "relevance":
        normalized = normalize_search_text(q)
        if normalized and dialect_name == "postgresql":
            primary = [sa.func.similarity(Tender.search_text, normalized).desc().nullslast()]
    elif sort == "deadline":
        primary = [Tender.deadline_at.asc().nullslast()]
    elif sort == "amount":
        primary = [amount_expression().desc().nullslast()]
    elif sort in ("first_seen", "recent"):
        primary = [Tender.first_seen_at.desc()]
    elif sort == "source_updated":
        primary = [Tender.source_updated_at.desc().nullslast()]
    elif sort == "score" and score is not None:
        primary = [score.desc().nullslast()]
    return [*primary, *neutral_order()]


@dataclass(slots=True)
class RowContext:
    """El que cada fila necessita i la licitacio carregada no porta."""

    cpv_descriptions: dict[str, str]
    source_codes: dict[uuid.UUID, str]
    document_counts: dict[uuid.UUID, int]


async def load_row_context(session: AsyncSession, tenders: list[Tender]) -> RowContext:
    codes = {item.cpv_code for tender in tenders for item in tender.cpvs}
    descriptions: dict[str, str] = {}
    if codes:
        descriptions = dict(
            (
                await session.execute(
                    sa.select(CPVCode.code, CPVCode.description).where(CPVCode.code.in_(codes))
                )
            ).all()
        )
    source_codes = dict((await session.execute(sa.select(Source.id, Source.code))).all())
    document_counts: dict[uuid.UUID, int] = {}
    ids = [tender.id for tender in tenders]
    if ids:
        # Documents vigents: un document retirat de la font (`removed_at`) no compta.
        document_counts = dict(
            (
                await session.execute(
                    sa.select(TenderDocument.tender_id, sa.func.count())
                    .where(TenderDocument.tender_id.in_(ids), ACTIVE_DOCUMENT)
                    .group_by(TenderDocument.tender_id)
                )
            ).all()
        )
    return RowContext(descriptions, source_codes, document_counts)


def list_item(tender: Tender, context: RowContext) -> TenderListItem:
    """La fila del cataleg, identica als dos feeds."""
    return TenderListItem(
        id=tender.id,
        external_id=tender.external_id,
        expediente_number=tender.expediente_number,
        title=tender.title,
        status=tender.status,
        procedure_type=tender.procedure_type,
        contract_type=tender.contract_type,
        estimated_value=tender.estimated_value,
        base_budget=tender.base_budget,
        base_budget_without_tax=tender.base_budget_without_tax,
        currency=tender.currency,
        publication_date=tender.publication_date,
        deadline_at=tender.deadline_at,
        nuts_code=tender.nuts_code,
        place_of_execution=tender.place_of_execution,
        buyer=BuyerSummary.model_validate(tender.buyer) if tender.buyer else None,
        cpvs=[
            TenderCPVRead(
                cpv_code=item.cpv_code,
                is_main=item.is_main,
                description=context.cpv_descriptions.get(item.cpv_code),
            )
            for item in sorted(tender.cpvs, key=lambda item: (not item.is_main, item.cpv_code))
        ],
        # Lots vigents: un lot retirat de la font es conserva pero ja no hi es.
        lot_count=sum(1 for lot in tender.lots if lot.removed_at is None),
        source_publication_count=tender.revision_count,
        source_code=context.source_codes.get(tender.source_id),
        is_modified=tender.revision_count > 1,
        source_updated_at=tender.source_updated_at,
        source_removed_at=tender.source_removed_at,
        document_count=context.document_counts.get(tender.id, 0),
    )


def tender_filters(
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Text normalitzat (titol, expedient, organisme, descripcio, lots); "
        "fins a 6 termes, tots han de coincidir",
    ),
    cpv: list[str] | None = Query(
        default=None, description="Codis CPV, exactes o prefix (a la licitacio o a un lot)"
    ),
    buyer_id: uuid.UUID | None = Query(default=None),
    buyer_q: str | None = Query(
        default=None, max_length=200, description="Text normalitzat sobre el nom de l'organisme"
    ),
    status: list[TenderStatus] | None = Query(default=None),
    procedure_type: list[ProcedureType] | None = Query(default=None),
    contract_type: list[ContractType] | None = Query(default=None),
    nuts: str | None = Query(default=None, max_length=10, description="Prefix de codi NUTS"),
    place_q: str | None = Query(
        default=None, max_length=200, description="Text normalitzat sobre el lloc d'execucio"
    ),
    min_amount: Decimal | None = Query(default=None, ge=0),
    max_amount: Decimal | None = Query(default=None, ge=0),
    published_from: date | None = Query(default=None),
    published_to: date | None = Query(default=None),
    deadline_from: datetime | None = Query(default=None),
    deadline_to: datetime | None = Query(default=None),
    has_lots: bool | None = Query(default=None),
    only_open: bool = Query(default=False, description="Nomes amb termini encara obert"),
    in_term: bool = Query(
        default=False,
        description="Estat accionable «En termini»: publicada i termini conegut posterior a ara "
        "(per instant). No es l'estat cru de la font",
    ),
    source: list[str] | None = Query(
        default=None, description="Codis de font tal com estan desats (`Source.code`)"
    ),
    source_removed: bool | None = Query(
        default=None, description="true: ja no disponibles a la font; false: presents"
    ),
) -> TenderFilters:
    return TenderFilters(
        q=q,
        cpv=cpv,
        buyer_id=buyer_id,
        buyer_q=buyer_q,
        status=status,
        procedure_type=procedure_type,
        contract_type=contract_type,
        nuts=nuts,
        place_q=place_q,
        min_amount=min_amount,
        max_amount=max_amount,
        published_from=published_from,
        published_to=published_to,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        has_lots=has_lots,
        only_open=only_open,
        in_term=in_term,
        source=source,
        source_removed=source_removed,
    )
