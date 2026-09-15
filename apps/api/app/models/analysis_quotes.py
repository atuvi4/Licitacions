"""Preview d'una analisi: el volum que l'usuari ha vist abans de confirmar-la.

Existeix per cost. Fins ara qualsevol membre d'una empresa podia llancar una
analisi de 80 chunks sense saber-ho. A partir d'ara cap analisi es demana sense
un preview previ, i el preview queda escrit al servidor: quins documents,
quantes pagines i chunks, amb quin perfil i model, quina estimacio de tokens i
amb quina versio del calcul (`planning_version`).

Que sigui una fila i no una resposta efimera no es burocracia. La confirmacio
compara el que es va ensenyar amb el que hi ha ara; si ha canviat, l'usuari ho
ha de tornar a veure. I quan arribi el ledger, es podra comparar l'estimat amb
el real seguint quote -> JobRequest -> WorkJob -> run.

Es dada de tenant (`company_id`): el mateix plec pot tenir previews de diverses
empreses, i cap no veu el de les altres.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import AnalysisQuoteStatus


class AnalysisQuote(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "analysis_quotes"
    __table_args__ = (
        sa.Index(
            "ix_analysis_quotes_company_tender", "company_id", "tender_id", "created_at"
        ),
        sa.Index("ix_analysis_quotes_status_expires", "status", "expires_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )

    #: Amb quina versio del calcul es va fer el preview. Si canvia entre el
    #: preview i la confirmacio, l'usuari l'ha de tornar a veure.
    planning_version: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    profile: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    force: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: Ids ordenats. Buida = seleccio per defecte de la canonada.
    document_ids: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False, default=list)

    input_fingerprint: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: El fingerprint no inclou el chunking: sense aixo, un canvi de mida de
    #: chunk passaria desapercebut entre el preview i la confirmacio.
    pages_per_chunk: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    overlap_pages: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    #: Documents que s'analitzarien i els que quedarien fora (noms publics dels
    #: plecs, pagines i caracters; cap text).
    documents: Mapped[list[dict[str, Any]]] = mapped_column(sa.JSON, nullable=False, default=list)
    skipped_documents: Mapped[list[dict[str, Any]]] = mapped_column(
        sa.JSON, nullable=False, default=list
    )
    documents_count: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    pages_total: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    pages_in_plan: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    chunks: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: Chunks que arribarien al proveidor: zero si es reaprofita un resultat.
    chunks_to_send: Mapped[int] = mapped_column(sa.Integer, nullable=False)

    reusable_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirement_extraction_runs.id", ondelete="SET NULL"),
    )
    estimated_input_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    estimated_output_tokens: Mapped[int | None] = mapped_column(sa.Integer)
    #: En que es basa l'estimacio (runs i peticions). NULL si no n'hi ha.
    estimate_basis: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)

    #: El limit vigent quan es va ensenyar, i si l'analisi hi cabia.
    max_chunks: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    allowed: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    blocking_reason: Mapped[str | None] = mapped_column(sa.String(50))

    status: Mapped[AnalysisQuoteStatus] = mapped_column(
        enum_column(AnalysisQuoteStatus, "analysis_quote_status"),
        nullable=False,
        default=AnalysisQuoteStatus.OPEN,
    )
    expires_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("analysis_quotes.id", ondelete="SET NULL")
    )
