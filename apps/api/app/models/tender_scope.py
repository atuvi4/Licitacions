"""Abast i semantica del plec (fase 3A, pas 3): que es compra, on aplica cada
requisit, quines proves exigeix, quines necessitats tecniques en surten, de quin
criteri es la cara exigible i quins fets contractuals no tenen columna propia.

Tot es el costat PLEC, public i global com els requisits: cap taula porta
`company_id`. Res d'aixo diu si l'empresa ho compleix, si el territori encaixa,
si una capacitat esta coberta ni quants punts s'asseguren: aixo es 0050.

* **Element d'abast** (`tender_scope_items`): "15 accessos FTTH", "4.206 m de
  fibra". Sempre amb `tender_id` propi: pot ser de tot el contracte (sense bloc
  ni lot), d'un bloc o d'un lot, **mai de tots dos** (el bloc ja determina el
  lot si en te). Quantitats i imports en decimal.
* **Territori d'un requisit** (`requirement_territories`): el vocabulari d'area
  de 0047 (`AreaKind`), amb un matis literal (`qualifier`) o una seu del plec.
  Nomes s'emmagatzema: cap `matched`, cap GIS, cap comparacio amb la cobertura.
* **Necessitat tecnica** (`requirement_capability_needs`): una capacitat del
  vocabulari (`capability_code`) o, si encara no hi es, el nom tal com el diu el
  plec (`unmapped_capability`); mai se'n crea cap de global des d'aqui. Porta
  `tender_id` propi per tenir arrel inequivoca i cites amb control cross-tender.
* **Requisit ↔ criteri** (`requirement_award_links`): "el plec demana X i X es
  la cara exigible del criteri C". Ni punts ni text es copien.
* **Fet contractual** (`tender_contract_facts`): sobres, limits de pagines,
  garantia, RC, subcontractacio... amb `value_json` versionat. El que ja te
  columna (PBL, VEC, organ, termini, durada, SARA, inici) no hi va.
  `contract_fact_blocks`: a quins blocs aplica (cap = tot el contracte).
  **Invariant de servei:** `billing_terms` es nomes facturacio general i no
  pot tenir cap enllac amb blocs (la del bloc viu a `tender_blocks`).
* **Cites**: el patro de `RequirementSourceRefLink`, una taula d'enllac per
  entitat citable (element d'abast, necessitat, fet), RESTRICT cap a la cita.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import AreaKind, ContractFactCode, NeedComparator, NeedType
from app.models.review import in_values

MONEY = sa.Numeric(18, 2)
#: Quantitats amb decimals (metres, hores): mai float.
QUANTITY = sa.Numeric(14, 3)


def _source_ref_link_columns(entity_table: str, entity_column: str, prefix: str):
    """Les columnes comunes d'una taula d'enllac entitat ↔ cita."""
    return (
        mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(f"{entity_table}.id", ondelete="CASCADE", name=f"fk_{prefix}_entity"),
            nullable=False,
        ),
        mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tender_source_refs.id", ondelete="RESTRICT", name=f"fk_{prefix}_source"),
            nullable=False,
        ),
        mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL", name=f"fk_{prefix}_author"),
        ),
        mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


# --------------------------------------------------------------------------- #
# Que es compra                                                                 #
# --------------------------------------------------------------------------- #


class TenderScopeItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tender_scope_items"
    __table_args__ = (
        sa.Index("ix_tender_scope_items_tender_id", "tender_id"),
        sa.Index("ix_tender_scope_items_block_id", "block_id"),
        # O bloc o lot: el bloc ja determina el lot si en te.
        sa.CheckConstraint("block_id IS NULL OR tender_lot_id IS NULL", name="scope_block_xor_lot"),
        sa.CheckConstraint("quantity IS NULL OR quantity >= 0", name="scope_quantity_not_negative"),
        sa.CheckConstraint(
            "unit_base_price_ex_vat IS NULL OR unit_base_price_ex_vat >= 0",
            name="scope_unit_price_not_negative",
        ),
        # Unes especificacions estructurades porten sempre la versio de la forma.
        sa.CheckConstraint(
            "specs_json IS NULL OR specs_schema IS NOT NULL", name="scope_specs_versioned"
        ),
    )

    #: Arrel inequivoca: un element pot ser de tot el contracte, sense bloc ni lot.
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: Un bloc posseeix el seu abast: si desapareix, se l'emporta.
    block_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_blocks.id", ondelete="CASCADE")
    )
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_lots.id", ondelete="SET NULL")
    )
    #: La seu, si l'element es d'una en concret. Perdre-la no perd l'element.
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_sites.id", ondelete="SET NULL")
    )
    #: Del vocabulari global: sense linia no es pot comparar amb res.
    service_line_code: Mapped[str] = mapped_column(
        sa.String(40), sa.ForeignKey("service_lines.code", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal | None] = mapped_column(QUANTITY)
    unit: Mapped[str | None] = mapped_column(sa.String(40))
    #: `none_as_null`: un `None` ha de ser NULL de SQL, no el JSON `null`, perque
    #: el CHECK de versio i les lectures ho puguin distingir.
    specs_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    specs_schema: Mapped[str | None] = mapped_column(sa.String(50))
    unit_base_price_ex_vat: Mapped[Decimal | None] = mapped_column(MONEY)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderScopeItem {self.quantity} {self.unit or ''} {self.service_line_code}>"


class TenderScopeItemSourceRefLink(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_scope_item_source_ref_links"
    __table_args__ = (
        sa.UniqueConstraint(
            "scope_item_id", "source_ref_id", name="uq_tender_scope_item_source_ref"
        ),
        sa.Index("ix_tender_scope_item_source_ref_links_item", "scope_item_id"),
        sa.Index("ix_tender_scope_item_source_ref_links_source_ref", "source_ref_id"),
    )

    scope_item_id: Mapped[uuid.UUID]
    source_ref_id: Mapped[uuid.UUID]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime]
    scope_item_id, source_ref_id, created_by, created_at = _source_ref_link_columns(
        "tender_scope_items", "scope_item_id", "scope_item_source_ref"
    )


# --------------------------------------------------------------------------- #
# Territoris, necessitats i criteris d'un requisit                              #
# --------------------------------------------------------------------------- #


class RequirementTerritory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "requirement_territories"
    __table_args__ = (
        sa.Index("ix_requirement_territories_requirement_id", "requirement_id"),
        sa.CheckConstraint(in_values("kind", AreaKind), name="kind_valid"),
        # Un territori ha d'apuntar a alguna cosa: un nom, un codi o una seu.
        sa.CheckConstraint(
            "label IS NOT NULL OR code IS NOT NULL OR site_id IS NOT NULL",
            name="territory_has_reference",
        ),
    )

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    #: El mateix vocabulari que la cobertura d'una empresa (0047): cap segon enum.
    kind: Mapped[AreaKind] = mapped_column(enum_column(AreaKind, "area_kind"), nullable=False)
    label: Mapped[str | None] = mapped_column(sa.String(200))
    code: Mapped[str | None] = mapped_column(sa.String(40))
    code_system: Mapped[str | None] = mapped_column(sa.String(20))
    #: El matis literal del plec ("trama urbana"). No s'interpreta.
    qualifier: Mapped[str | None] = mapped_column(sa.String(200))
    #: Un territori ancorat a una seu es l'adreca d'aquella seu: si la seu
    #: desapareix del plec, el territori se'n va amb ella (no pot quedar buit).
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tender_sites.id", ondelete="CASCADE")
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class RequirementCapabilityNeed(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "requirement_capability_needs"
    __table_args__ = (
        sa.Index("ix_requirement_capability_needs_requirement_id", "requirement_id"),
        sa.Index("ix_requirement_capability_needs_tender_id", "tender_id"),
        sa.CheckConstraint(in_values("comparator", NeedComparator), name="comparator_valid"),
        sa.CheckConstraint(in_values("need_type", NeedType), name="need_type_valid"),
        sa.CheckConstraint("points IS NULL OR points >= 0", name="need_points_not_negative"),
        # Una capacitat del vocabulari O un nom sense mapar: una de les dues, no cap ni totes dues.
        sa.CheckConstraint(
            "(capability_code IS NULL) <> (unmapped_capability IS NULL)",
            name="need_mapped_xor_unmapped",
        ),
        sa.CheckConstraint(
            "value_json IS NULL OR value_schema IS NOT NULL", name="need_value_versioned"
        ),
    )

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="CASCADE"), nullable=False
    )
    #: Redundant amb el requisit a proposit: arrel inequivoca i control
    #: cross-tender de les cites sense cap join.
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: El criteri que puntua aquesta necessitat, si n'hi ha. No en toca els punts.
    award_criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("award_criteria.id", ondelete="SET NULL")
    )
    capability_code: Mapped[str | None] = mapped_column(
        sa.String(80), sa.ForeignKey("capability_definitions.code", ondelete="RESTRICT")
    )
    unmapped_capability: Mapped[str | None] = mapped_column(sa.String(200))
    comparator: Mapped[NeedComparator] = mapped_column(
        enum_column(NeedComparator, "need_comparator"), nullable=False
    )
    value_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON(none_as_null=True))
    value_schema: Mapped[str | None] = mapped_column(sa.String(50))
    need_type: Mapped[NeedType] = mapped_column(enum_column(NeedType, "need_type"), nullable=False)
    #: Mateix tipus que `award_criteria.points`. No se suma ni es puntua encara.
    points: Mapped[float | None] = mapped_column(sa.Float)

    def __repr__(self) -> str:  # pragma: no cover
        name = self.capability_code or self.unmapped_capability
        return f"<RequirementCapabilityNeed {name} {self.comparator}>"


class RequirementCapabilityNeedSourceRefLink(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "requirement_capability_need_source_ref_links"
    __table_args__ = (
        sa.UniqueConstraint("need_id", "source_ref_id", name="uq_capability_need_source_ref"),
        sa.Index("ix_capability_need_source_ref_links_need", "need_id"),
        sa.Index("ix_capability_need_source_ref_links_source_ref", "source_ref_id"),
    )

    need_id: Mapped[uuid.UUID]
    source_ref_id: Mapped[uuid.UUID]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime]
    need_id, source_ref_id, created_by, created_at = _source_ref_link_columns(
        "requirement_capability_needs", "need_id", "capability_need_source_ref"
    )


class RequirementAwardLink(Base):
    """El requisit es la cara exigible del criteri. Nomes la relacio."""

    __tablename__ = "requirement_award_links"
    __table_args__ = (sa.Index("ix_requirement_award_links_criterion", "award_criterion_id"),)

    requirement_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirements.id", ondelete="CASCADE"),
        primary_key=True,
    )
    award_criterion_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("award_criteria.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# Fets contractuals                                                             #
# --------------------------------------------------------------------------- #


class TenderContractFact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tender_contract_facts"
    __table_args__ = (
        sa.Index("ix_tender_contract_facts_tender_id", "tender_id"),
        sa.CheckConstraint(in_values("fact_code", ContractFactCode), name="fact_code_valid"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    fact_code: Mapped[ContractFactCode] = mapped_column(
        enum_column(ContractFactCode, "contract_fact_code"), nullable=False
    )
    #: El valor estructurat, amb la versio de la seva forma. Cap regla de risc aqui.
    value_json: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False)
    value_schema: Mapped[str] = mapped_column(sa.String(50), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderContractFact {self.fact_code}>"


class ContractFactBlock(Base):
    """A quins blocs aplica un fet. Cap fila = tot el contracte. Cap repartiment."""

    __tablename__ = "contract_fact_blocks"
    __table_args__ = (sa.Index("ix_contract_fact_blocks_block_id", "block_id"),)

    fact_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tender_contract_facts.id", ondelete="CASCADE"),
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


class TenderContractFactSourceRefLink(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "tender_contract_fact_source_ref_links"
    __table_args__ = (
        sa.UniqueConstraint("fact_id", "source_ref_id", name="uq_tender_contract_fact_source_ref"),
        sa.Index("ix_tender_contract_fact_source_ref_links_fact", "fact_id"),
        sa.Index("ix_tender_contract_fact_source_ref_links_source_ref", "source_ref_id"),
    )

    fact_id: Mapped[uuid.UUID]
    source_ref_id: Mapped[uuid.UUID]
    created_by: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime]
    fact_id, source_ref_id, created_by, created_at = _source_ref_link_columns(
        "tender_contract_facts", "fact_id", "contract_fact_source_ref"
    )
