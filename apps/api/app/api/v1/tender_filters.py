"""Filtres sobre licitacions, compartits pels dos feeds.

Hi ha dos feeds que llisten les mateixes licitacions:

- `/tenders`: el cataleg public, sense cap relacio amb l'empresa.
- `/companies/{id}/matches`: el mateix cataleg amb el pre-fit al costat.

Els criteris de cerca han de ser identics als dos llocs — si no, l'usuari
filtra per CPV al cataleg, canvia a la vista de pre-fit i li desapareixen
resultats sense motiu. Per aixo viuen aqui i no duplicats a cada router.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from fastapi import Query

from app.models.enums import ContractType, ProcedureType, TenderStatus
from app.models.tenders import Tender, TenderCPV, TenderLot


@dataclass(slots=True)
class TenderFilters:
    """Criteris de cerca sobre licitacions. FastAPI els injecta com a dependencia."""

    q: str | None = None
    cpv: list[str] | None = None
    buyer_id: uuid.UUID | None = None
    status: list[TenderStatus] | None = None
    procedure_type: list[ProcedureType] | None = None
    contract_type: list[ContractType] | None = None
    nuts: str | None = None
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    published_from: date | None = None
    published_to: date | None = None
    deadline_from: datetime | None = None
    deadline_to: datetime | None = None
    has_lots: bool | None = None
    only_open: bool = False

    def criteria(self) -> list:
        """Clausules SQLAlchemy sobre `Tender`. Buida si no s'ha filtrat res."""
        clauses = []
        if self.q:
            pattern = f"%{self.q.lower()}%"
            clauses.append(
                sa.or_(
                    sa.func.lower(Tender.title).like(pattern),
                    sa.func.lower(sa.func.coalesce(Tender.expediente_number, "")).like(pattern),
                )
            )
        if self.cpv:
            clauses.append(
                Tender.id.in_(
                    sa.select(TenderCPV.tender_id).where(TenderCPV.cpv_code.in_(self.cpv))
                )
            )
        if self.buyer_id:
            clauses.append(Tender.buyer_id == self.buyer_id)
        if self.status:
            clauses.append(Tender.status.in_(self.status))
        if self.procedure_type:
            clauses.append(Tender.procedure_type.in_(self.procedure_type))
        if self.contract_type:
            clauses.append(Tender.contract_type.in_(self.contract_type))
        if self.nuts:
            clauses.append(Tender.nuts_code.like(f"{self.nuts.upper()}%"))
        if self.min_amount is not None:
            clauses.append(
                sa.func.coalesce(Tender.estimated_value, Tender.base_budget) >= self.min_amount
            )
        if self.max_amount is not None:
            clauses.append(
                sa.func.coalesce(Tender.estimated_value, Tender.base_budget) <= self.max_amount
            )
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
        if self.has_lots is not None:
            exists = sa.select(TenderLot.id).where(TenderLot.tender_id == Tender.id).exists()
            clauses.append(exists if self.has_lots else sa.not_(exists))
        return clauses


def tender_filters(
    q: str | None = Query(default=None, max_length=200, description="Text al titol o expedient"),
    cpv: list[str] | None = Query(default=None, description="Codis CPV (qualsevol d'ells)"),
    buyer_id: uuid.UUID | None = Query(default=None),
    status: list[TenderStatus] | None = Query(default=None),
    procedure_type: list[ProcedureType] | None = Query(default=None),
    contract_type: list[ContractType] | None = Query(default=None),
    nuts: str | None = Query(default=None, max_length=10, description="Prefix de codi NUTS"),
    min_amount: Decimal | None = Query(default=None, ge=0),
    max_amount: Decimal | None = Query(default=None, ge=0),
    published_from: date | None = Query(default=None),
    published_to: date | None = Query(default=None),
    deadline_from: datetime | None = Query(default=None),
    deadline_to: datetime | None = Query(default=None),
    has_lots: bool | None = Query(default=None),
    only_open: bool = Query(default=False, description="Nomes amb termini encara obert"),
) -> TenderFilters:
    return TenderFilters(
        q=q,
        cpv=cpv,
        buyer_id=buyer_id,
        status=status,
        procedure_type=procedure_type,
        contract_type=contract_type,
        nuts=nuts,
        min_amount=min_amount,
        max_amount=max_amount,
        published_from=published_from,
        published_to=published_to,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
        has_lots=has_lots,
        only_open=only_open,
    )
