"""Mitjans operatius de l'empresa (0052): equips i instruments adscrivibles.

Un equip (OTDR, OLTS, fusionadora...) es un MITJA que l'empresa pot adscriure a
un contracte, no un producte que vengui (`company_products`) ni una capacitat
d'una solucio. Es coneixement actual amb la procedencia completa: `valid_until`
diu fins quan en disposa (un lloguer), `lifecycle = retired` que ja no.

El calibratge es una dada propia: `calibration_required` (nul = no consta) i
`calibration_valid_until`. Un plec que demana «OTDR calibrat» es compara amb la
data; sense data, no se sap. L'evidencia (certificat de calibratge, factura)
s'enllaca com a document, com a la resta del coneixement.
"""

from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import EquipmentAccessMode, EquipmentType
from app.models.knowledge import KnowledgeProvenanceMixin, provenance_constraints
from app.models.review import in_values


class CompanyEquipment(UUIDPrimaryKeyMixin, KnowledgeProvenanceMixin, Base):
    __tablename__ = "company_equipment"
    __table_args__ = (
        *provenance_constraints(),
        sa.CheckConstraint(in_values("equipment_type", EquipmentType), name="equipment_type_valid"),
        sa.CheckConstraint(
            "access_mode IS NULL OR " + in_values("access_mode", EquipmentAccessMode),
            name="access_mode_valid",
        ),
        sa.CheckConstraint("quantity >= 1", name="quantity_positive"),
        # Una data de calibratge nomes te sentit si l'equip en necessita.
        sa.CheckConstraint(
            "calibration_valid_until IS NULL OR calibration_required IS TRUE",
            name="calibration_date_needs_requirement",
        ),
        sa.UniqueConstraint("company_id", "serial_reference", name="uq_company_equipment_serial"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    equipment_type: Mapped[EquipmentType] = mapped_column(
        enum_column(EquipmentType, "equipment_type"), nullable=False
    )
    manufacturer: Mapped[str | None] = mapped_column(sa.String(160))
    model: Mapped[str | None] = mapped_column(sa.String(160))
    #: Numero de serie o referencia interna. Unic per empresa quan hi es.
    serial_reference: Mapped[str | None] = mapped_column(sa.String(120))
    #: Com se'n disposa. Nul = no consta.
    access_mode: Mapped[EquipmentAccessMode | None] = mapped_column(
        enum_column(EquipmentAccessMode, "equipment_access_mode")
    )
    #: Unitats que representa la fila (una per serie; mes d'una si no s'itemitzen).
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1, server_default="1")
    calibration_required: Mapped[bool | None] = mapped_column(sa.Boolean)
    calibration_valid_until: Mapped[date | None] = mapped_column(sa.Date)
    notes: Mapped[str | None] = mapped_column(sa.Text)

    @property
    def label(self) -> str:
        parts = [str(self.equipment_type), self.manufacturer, self.model]
        return " ".join(p for p in parts if p)
