"""Quin run de requisits ha acceptat cada empresa (0061, 3B-2 §6.5).

El run de requisits d'un tender public es GLOBAL i no registra cap empresa. El
que cada empresa ha acceptat es dada de tenant: una fila per (empresa, tender)
que apunta a un run. Sense fila, l'empresa llegeix el canonic global
(`is_current`). Un run canonic nou no mou cap punter: l'empresa l'adopta quan vol
(`review_runs.adopt_requirement_run`), i aleshores reconcilia la seva revisio.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CompanyTenderRequirementRun(TimestampMixin, Base):
    __tablename__ = "company_tender_requirement_runs"
    __table_args__ = (sa.Index("ix_company_tender_requirement_runs_run_id", "run_id"),)

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tenders.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: El run acceptat. Si desaparegues (nomes per cascada del tender), el punter
    #: hi va darrere i l'empresa torna a llegir el canonic.
    run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        # Nom explicit: el de la convencio passaria dels 63 caracters de PostgreSQL.
        sa.ForeignKey(
            "requirement_extraction_runs.id",
            ondelete="CASCADE",
            name="fk_company_tender_requirement_runs_run_id",
        ),
        nullable=False,
    )
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "users.id",
            ondelete="SET NULL",
            name="fk_company_tender_requirement_runs_accepted_by",
        ),
    )
    accepted_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
