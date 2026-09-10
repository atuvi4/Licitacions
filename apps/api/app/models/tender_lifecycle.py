"""La historia d'una licitacio: com hem arribat a l'estat que ensenyem.

`Tender` guarda l'estat actual, que es el que es consulta cent vegades al dia.
Aquestes dues taules guarden **com hi hem arribat**, i existeixen per una cosa
que sense elles no es pot fer: quan l'organ publica una modificacio, poder dir
que ha canviat, quan ho vam saber, i que deixa de ser vigent per culpa d'aixo.

Sense historia, l'unica alternativa honesta seria invalidar-ho tot a cada
reingestió —i llavors ningu es creuria cap avis— o no invalidar res, i llavors
algu presentaria una oferta contra un plec que ja no existeix.

Totes dues son **append-only**. Una revisio escrita no es modifica mai: es la
que una decisio humana citara per dir "vaig decidir GO sobre aquest estat".

Son publiques i globals, com tot el que descriu la licitacio: cap `company_id`.
Si cent empreses miren el mateix expedient, la seva historia es una de sola.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin


class TenderRevision(UUIDPrimaryKeyMixin, Base):
    """Una fotografia immutable de l'estat normalitzat d'una licitacio.

    El `normalized_hash` es el que fa que reingerir la mateixa entrada no
    generi res: mateix contingut, mateix hash, cap revisio nova, cap event i cap
    avis. Es calcula sobre l'estat **normalitzat** i no sobre el XML original,
    de manera que un canvi de format de la font que no altera cap dada no
    despertara mig sistema.

    Hi entra tambe el SHA-256 dels documents que ja s'han baixat. Aixo es el que
    permet distingir "la mateixa URL serveix un plec nou" de "el plec es el
    mateix i nomes ha canviat l'adreça", que son coses molt diferents: la
    primera obliga a tornar a analitzar-ho tot i la segona no ha de despertar
    ningu.
    """

    __tablename__ = "tender_revisions"
    __table_args__ = (
        sa.UniqueConstraint(
            "tender_id", "revision_number", name="uq_tender_revisions_tender_number"
        ),
        sa.Index("ix_tender_revisions_tender_id", "tender_id"),
        sa.Index("ix_tender_revisions_hash", "normalized_hash"),
        sa.CheckConstraint("revision_number >= 1", name="tender_revision_is_one_based"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: 1-based i consecutiu dins de la licitacio.
    revision_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    #: D'on ve aquesta revisio, quan ve de la font. `NULL` quan la provoca un
    #: canvi que no ve del feed, com baixar un document i descobrir-ne el SHA.
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("source_records.id", ondelete="SET NULL")
    )
    #: L'estat normalitzat sencer. Es el que es compara amb el de la seguent.
    normalized_snapshot: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False)
    normalized_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Els canvis respecte de la revisio anterior, ja calculats. Es desa perque
    #: tornar-los a deduir demà amb una versio diferent del detector donaria un
    #: resultat diferent del que la persona va veure quan va decidir.
    change_summary: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    #: Que ho ha provocat: `ingestion`, `document_processing`...
    trigger: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="ingestion")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    events: Mapped[list[TenderChangeEvent]] = relationship(
        back_populates="to_revision",
        foreign_keys="TenderChangeEvent.to_revision_id",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderRevision {self.tender_id} r{self.revision_number}>"


class TenderChangeEvent(UUIDPrimaryKeyMixin, Base):
    """Un canvi concret entre dues revisions, amb el seu impacte.

    Es una fila per canvi i no un diff en un camp de text perque el que es fara
    amb aixo es decidir: avisar o no avisar, invalidar o no invalidar, i mes
    endavant notificar. "El JSON ha canviat" no permet decidir res;
    `DEADLINE_CHANGED 20/09 -> 27/09` si.

    Els impactes es desen amb l'event i no es tornen a deduir. Si algun dia
    canviem la matriu, els events antics han de seguir explicant per que en
    aquell moment es va invalidar el que es va invalidar.
    """

    __tablename__ = "tender_change_events"
    __table_args__ = (
        sa.Index("ix_tender_change_events_tender_id", "tender_id"),
        sa.Index("ix_tender_change_events_to_revision_id", "to_revision_id"),
        sa.Index("ix_tender_change_events_code", "change_code"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: `NULL` a la primera revisio: no hi ha res abans amb que comparar.
    from_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_revisions.id", ondelete="CASCADE")
    )
    to_revision_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    change_code: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: Que identifica l'element afectat: el codi d'un lot, la URL d'un document.
    subject: Mapped[str | None] = mapped_column(sa.String(500))
    old_value: Mapped[Any | None] = mapped_column(sa.JSON)
    new_value: Mapped[Any | None] = mapped_column(sa.JSON)
    #: Els impactes, ordenats. Es desen: la matriu pot evolucionar i aquest
    #: event ha de seguir explicant la invalidacio que va provocar.
    impacts: Mapped[list[str]] = mapped_column(sa.JSON, nullable=False)
    #: Cert si invalida alguna cosa. Derivat dels impactes en el moment
    #: d'escriure'l, desat per poder filtrar sense recalcular.
    is_material: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    detected_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    to_revision: Mapped[TenderRevision] = relationship(
        back_populates="events", foreign_keys=[to_revision_id]
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderChangeEvent {self.change_code} {self.subject or ''}>"
