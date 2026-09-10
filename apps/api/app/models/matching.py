"""Pre-fit: la primera connexio entre el perfil d'una empresa i una licitacio.

ATENCIO amb el que aixo NO es: no es un Go/No-Go, no valida solvencia exigida,
ni certificacions obligatories, ni experiencia minima. Tot aixo viu als plecs i
els plecs encara no s'han llegit. Aixo es un **filtre de prioritzacio** calculat
amb metadades publiques i preferencies de l'empresa.

Aquestes taules SI que son dades de tenant: porten `company_id` i estan
sotmeses a l'aillament multi-tenant de sempre. El `Tender` continua sent global.

## Per que dues taules i no una

L'alternativa era una sola taula amb `tender_lot_id` nullable, pero:

1. Un UNIQUE amb una columna NULL no restringeix res a PostgreSQL: es podrien
   inserir dos matches de nivell expedient per a la mateixa parella. La
   integritat quedaria en mans del codi Python, i el que volem es que la base
   de dades la garanteixi.
2. Les dues files signifiquen coses diferents. La de nivell expedient es un
   **resum** (millor lot, quants lots son candidats); la de nivell lot es un
   **candidat puntuat**. Amb una sola taula, la meitat de les columnes no
   tindrien sentit per a cada tipus de fila.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import ExclusionKind, MatchStatus
from app.models.tenders import Tender, TenderLot


class _MatchColumns:
    """Columnes compartides pels dos nivells de match."""

    score: Mapped[int | None] = mapped_column(sa.Integer)
    status: Mapped[MatchStatus] = mapped_column(
        enum_column(MatchStatus, "match_status"), nullable=False
    )
    exclusion_kind: Mapped[ExclusionKind | None] = mapped_column(
        enum_column(ExclusionKind, "exclusion_kind")
    )
    exclusion_reason_code: Mapped[str | None] = mapped_column(sa.String(60))

    # Puntuacio per dimensio, ja ponderada. `None` = dimensio desconeguda.
    cpv_score: Mapped[float | None] = mapped_column(sa.Float)
    geographic_score: Mapped[float | None] = mapped_column(sa.Float)
    economic_score: Mapped[float | None] = mapped_column(sa.Float)
    timing_score: Mapped[float | None] = mapped_column(sa.Float)

    # Proporcio del pes total que s'ha pogut avaluar (0..1). Amb cobertura
    # baixa el resultat es `insufficient_data` en comptes d'un score enganyos.
    data_coverage: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)

    # Detall estructurat de cada factor: reason_code, valors comparats i punts.
    # JSON i no text lliure: la UI deriva el text dels codis, no al reves.
    factors: Mapped[list[dict[str, Any]]] = mapped_column(
        sa.JSON, nullable=False, default=list
    )

    rules_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    # Marques per saber si el resultat ha quedat obsolet, sense muntar cap
    # infraestructura d'esdeveniments: es comparen amb l'estat actual.
    company_profile_changed_at: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    tender_changed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Sobre quina revisio de la licitacio s'ha calculat. La marca de temps de
    #: dalt diu "la licitacio s'ha tocat"; aixo diu QUE s'ha tocat, perque
    #: permet mirar els events posteriors i preguntar nomes pels que afecten el
    #: pre-fit. Sense aixo, reingerir sense cap canvi real ja feia semblar
    #: obsolet tot el que s'havia calculat.
    tender_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_revisions.id", ondelete="SET NULL")
    )


class CompanyTenderMatch(UUIDPrimaryKeyMixin, TimestampMixin, _MatchColumns, Base):
    """Pre-fit a nivell d'expedient.

    Si l'expedient te lots, aquesta fila es un RESUM: el score es el del millor
    lot, mai una mitjana (una mitjana amagaria justament el lot que interessa).
    """

    __tablename__ = "company_tender_matches"
    __table_args__ = (
        sa.UniqueConstraint("company_id", "tender_id", name="uq_company_tender_match"),
        sa.Index("ix_company_tender_matches_company_status", "company_id", "status"),
        sa.Index("ix_company_tender_matches_company_score", "company_id", "score"),
        sa.Index("ix_company_tender_matches_tender", "tender_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )

    # Resum dels lots, quan n'hi ha.
    best_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="SET NULL")
    )
    candidate_lot_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    total_lot_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    tender: Mapped[Tender] = relationship(lazy="joined")
    lot_matches: Mapped[list[CompanyTenderLotMatch]] = relationship(
        back_populates="tender_match", cascade="all, delete-orphan", lazy="selectin"
    )


class CompanyTenderLotMatch(UUIDPrimaryKeyMixin, TimestampMixin, _MatchColumns, Base):
    """Pre-fit a nivell de lot.

    El matching real es fa aqui quan l'expedient te lots: un mateix expedient
    pot tenir un lot que encaixa perfectament i un altre que esta exclos.
    """

    __tablename__ = "company_tender_lot_matches"
    __table_args__ = (
        sa.UniqueConstraint("company_id", "tender_lot_id", name="uq_company_tender_lot_match"),
        sa.Index("ix_company_tender_lot_matches_company_status", "company_id", "status"),
        sa.Index("ix_company_tender_lot_matches_match", "tender_match_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    tender_match_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("company_tender_matches.id", ondelete="CASCADE"),
        nullable=False,
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    tender_lot_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="CASCADE"), nullable=False
    )

    tender_match: Mapped[CompanyTenderMatch] = relationship(back_populates="lot_matches")
    lot: Mapped[TenderLot] = relationship(lazy="joined")
