"""Estructura del plec per a l'analisi (fase 3A, pas 2): blocs, seus i a que
aplica cada requisit o criteri.

Tot es PUBLIC i GLOBAL com la resta del plec: cap taula porta `company_id`,
la tenancy es la de la licitacio. Qui ho pot escriure ho diu la politica de la
licitacio oberta a ma (`manual_tender`), no aquest modul.

* **Bloc** (`tender_blocks`): una unitat economica o tecnica dins del contracte
  que NO es necessariament un lot. L'Escala en te quatre i un sol adjudicatari.
  `separately_awardable` es tri-estat: `NULL` vol dir que el plec no ho diu, i
  no es cap `False`. `billing_terms_json` es nomes per a condicions propies del
  bloc; la facturacio general del contracte es dada del contracte (0049).
* **Seu** (`tender_sites`): una ubicacio del plec. `role` diu que hi fa
  (`central`, `remote`) i `is_critical` es una marca a part: una seu critica
  NO es un paper nou (`remote` + `is_critical`). `NULL` a l'adreca, al municipi
  o a les coordenades vol dir que encara no se sap. No es geocodifica ni es
  dedueix cap municipi. No te res a veure amb les seus d'una empresa.
* **Blocs d'un requisit / d'un criteri** (`requirement_blocks`,
  `award_criterion_blocks`): N:M. Cap enllac = aplica a tot el contracte; un =
  a aquell bloc; N = a aquells N. **Els punts no es reparteixen**: un criteri de
  6 punts enllacat a B1 i B2 continua sent un criteri de 6 punts.
* **Cites** (`tender_block_source_ref_links`, `tender_site_source_ref_links`):
  el mateix patro que `RequirementSourceRefLink`: l'enllac es mutable, la cita
  (`TenderSourceRef`) no, i RESTRICT cap a la cita.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import TenderSiteRole
from app.models.review import in_values

MONEY = sa.Numeric(18, 2)
#: Prou per a qualsevol coordenada en graus decimals (sis decimals ≈ 10 cm).
COORDINATE = sa.Numeric(9, 6)


class TenderBlock(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tender_blocks"
    __table_args__ = (
        sa.UniqueConstraint("tender_id", "code", name="uq_tender_blocks_tender_code"),
        sa.Index("ix_tender_blocks_tender_id", "tender_id"),
        sa.CheckConstraint(
            "base_price_ex_vat IS NULL OR base_price_ex_vat >= 0",
            name="block_base_price_not_negative",
        ),
        sa.CheckConstraint(
            "price_points IS NULL OR price_points >= 0", name="block_price_points_not_negative"
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: Nomes si el bloc es identifica amb un lot. Un bloc NO es un lot: la
    #: majoria no en tenen cap. El servei comprova que el lot sigui del mateix
    #: expedient.
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="SET NULL")
    )
    #: Codi curt dins de l'expedient ("B1"). Unic per licitacio.
    code: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    name: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    #: Import base del bloc sense IVA. Decimal, mai float.
    base_price_ex_vat: Mapped[Decimal | None] = mapped_column(MONEY)
    #: Punts del preu d'aquest bloc, amb el mateix tipus que `award_criteria.points`.
    price_points: Mapped[float | None] = mapped_column(sa.Float)
    #: Tri-estat: `NULL` = el plec no ho diu. No es cap `False` per defecte.
    separately_awardable: Mapped[bool | None] = mapped_column(sa.Boolean)
    #: Nomes condicions de facturacio PROPIES del bloc. Les generals del
    #: contracte no van aqui (0049, `tender_contract_facts`).
    billing_terms_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)
    #: Codi curt del patro de facturacio del bloc ("monthly", "per_site"...),
    #: lliure fins que 0049 en fixi el vocabulari.
    billing_schema: Mapped[str | None] = mapped_column(sa.String(40))

    tender: Mapped[Tender] = relationship(back_populates="blocks")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderBlock {self.code} {self.name[:30]}>"


class TenderSite(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tender_sites"
    __table_args__ = (
        sa.UniqueConstraint("tender_id", "code", name="uq_tender_sites_tender_code"),
        sa.Index("ix_tender_sites_tender_id", "tender_id"),
        sa.CheckConstraint(in_values("role", TenderSiteRole), name="role_valid"),
        # Identitat parcial: un ramal nomes amb codi, una seu critica nomes amb nom.
        sa.CheckConstraint("code IS NOT NULL OR name IS NOT NULL", name="site_has_identity"),
        sa.CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)", name="site_latitude_range"
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="site_longitude_range",
        ),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: Codi del document (p. ex. el ramal `D02`) o nom real: almenys un dels dos (CHECK).
    #: Mai un codi sintetic a partir del nom, ni un nom copiat del codi.
    code: Mapped[str | None] = mapped_column(sa.String(40))
    name: Mapped[str | None] = mapped_column(sa.String(500))
    role: Mapped[TenderSiteRole] = mapped_column(
        enum_column(TenderSiteRole, "tender_site_role"), nullable=False
    )
    #: Tri-estat: `True` la font diu que es critica, `False` diu que no, `NULL` no se
    #: sap. Sense valor per defecte: «no se sap» no es converteix mai en «no».
    is_critical: Mapped[bool | None] = mapped_column(sa.Boolean)
    #: `NULL` = encara no se sap. No es dedueix de res.
    address: Mapped[str | None] = mapped_column(sa.String(500))
    municipality_code: Mapped[str | None] = mapped_column(sa.String(10))
    #: Amb quin sistema esta codificat el municipi ("INE", "IDESCAT"...). Lliure i
    #: nul·lable: no s'assumeix mai INE. 0049 compara codis per (code, code_system).
    municipality_code_system: Mapped[str | None] = mapped_column(sa.String(20))
    latitude: Mapped[Decimal | None] = mapped_column(COORDINATE)
    longitude: Mapped[Decimal | None] = mapped_column(COORDINATE)

    tender: Mapped[Tender] = relationship(back_populates="sites")  # noqa: F821

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderSite {self.code} {self.role}{' critical' if self.is_critical else ''}>"


class RequirementBlock(Base):
    """A quins blocs aplica un requisit. Sense files: a tot el contracte."""

    __tablename__ = "requirement_blocks"
    __table_args__ = (sa.Index("ix_requirement_blocks_block_id", "block_id"),)

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirements.id", ondelete="CASCADE"),
        primary_key=True,
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_blocks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class AwardCriterionBlock(Base):
    """A quins blocs aplica un criteri. Els punts NO es reparteixen entre ells."""

    __tablename__ = "award_criterion_blocks"
    __table_args__ = (sa.Index("ix_award_criterion_blocks_block_id", "block_id"),)

    award_criterion_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("award_criteria.id", ondelete="CASCADE"),
        primary_key=True,
    )
    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_blocks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class TenderBlockSourceRefLink(UUIDPrimaryKeyMixin, Base):
    """Quines cites sostenen un bloc. Mateix patro que `RequirementSourceRefLink`."""

    __tablename__ = "tender_block_source_ref_links"
    __table_args__ = (
        sa.UniqueConstraint("block_id", "source_ref_id", name="uq_tender_block_source_ref"),
        sa.Index("ix_tender_block_source_ref_links_block", "block_id"),
        sa.Index("ix_tender_block_source_ref_links_source_ref", "source_ref_id"),
    )

    block_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_blocks.id", ondelete="CASCADE", name="fk_block_source_ref_block"),
        nullable=False,
    )
    source_ref_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_source_refs.id", ondelete="RESTRICT", name="fk_block_source_ref_source"
        ),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_block_source_ref_author"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class TenderSiteSourceRefLink(UUIDPrimaryKeyMixin, Base):
    """Quines cites sostenen una seu. Mateix patro que `RequirementSourceRefLink`."""

    __tablename__ = "tender_site_source_ref_links"
    __table_args__ = (
        sa.UniqueConstraint("site_id", "source_ref_id", name="uq_tender_site_source_ref"),
        sa.Index("ix_tender_site_source_ref_links_site", "site_id"),
        sa.Index("ix_tender_site_source_ref_links_source_ref", "source_ref_id"),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_sites.id", ondelete="CASCADE", name="fk_site_source_ref_site"),
        nullable=False,
    )
    source_ref_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_source_refs.id", ondelete="RESTRICT", name="fk_site_source_ref_source"
        ),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_site_source_ref_author"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class AwardCriterionSourceRefLink(UUIDPrimaryKeyMixin, Base):
    """Quines cites sostenen un criteri d'adjudicacio (0050). Mateix patro."""

    __tablename__ = "award_criterion_source_ref_links"
    __table_args__ = (
        sa.UniqueConstraint(
            "award_criterion_id", "source_ref_id", name="uq_award_criterion_source_ref"
        ),
        sa.Index("ix_award_criterion_source_ref_links_criterion", "award_criterion_id"),
        sa.Index("ix_award_criterion_source_ref_links_source_ref", "source_ref_id"),
    )

    award_criterion_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "award_criteria.id", ondelete="CASCADE", name="fk_criterion_source_ref_criterion"
        ),
        nullable=False,
    )
    source_ref_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_source_refs.id", ondelete="RESTRICT", name="fk_criterion_source_ref_source"
        ),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_criterion_source_ref_author"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


from app.models.tenders import Tender  # noqa: E402  (relacions per nom; evita el cicle)
