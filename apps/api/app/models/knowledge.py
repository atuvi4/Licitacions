"""Coneixement de l'empresa: vocabularis globals, estat actual i historic.

**Vocabularis globals** (sense `company_id`, com `cpv_codes`): `service_lines`,
`capability_definitions` i `certification_standards`. Els omple una migracio a
partir de `app/knowledge/vocabulary.py`; cap API no hi escriu. Esborrar una
empresa no els toca.

**Estat actual** (tenant): el que l'empresa es i pot fer avui. Cada fila porta
la seva procedencia (`KnowledgeProvenanceMixin`): qui l'afirma, de quan es, fins
quan val, si esta verificada i si encara es vigent. Les taules existents que ja
cobrien un concepte (serveis, certificacions) s'amplien a
`app/models/profile.py` i `app/models/credentials.py`; no es dupliquen aqui.

**Historic** (tenant): `historic_bids` i `knowledge_observations`. Una
observacio es un suggeriment. Nomes una persona la pot promoure a coneixement
actual, i aleshores en queda la traça en tots dos sentits.

Les filles referencien el pare per `(id, company_id)`: res d'una empresa pot
penjar d'una fila d'una altra. Tots els enums porten CHECK.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    AreaKind,
    CapabilityComparator,
    CapabilityStatus,
    CapabilityValueType,
    CoverageCompleteness,
    CoverageEntityType,
    HistoricBidDecision,
    HistoricBidResult,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    ObservationStatus,
    ObservationSubject,
    OperatingFactKey,
    PartnerRole,
    VerificationStatus,
)
from app.models.review import JSONDocument, in_values

# --------------------------------------------------------------------------- #
# Vocabularis globals                                                          #
# --------------------------------------------------------------------------- #


class ServiceLineRow(Base):
    __tablename__ = "service_lines"

    code: Mapped[str] = mapped_column(sa.String(40), primary_key=True)
    label: Mapped[str] = mapped_column(sa.String(160), nullable=False)


class CapabilityDefinitionRow(Base):
    __tablename__ = "capability_definitions"
    __table_args__ = (
        sa.CheckConstraint(in_values("value_type", CapabilityValueType), name="value_type_valid"),
        sa.CheckConstraint(in_values("comparator", CapabilityComparator), name="comparator_valid"),
    )

    code: Mapped[str] = mapped_column(sa.String(80), primary_key=True)
    service_line_code: Mapped[str] = mapped_column(
        sa.String(40), sa.ForeignKey("service_lines.code", ondelete="RESTRICT"), nullable=False
    )
    label: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    value_type: Mapped[CapabilityValueType] = mapped_column(
        enum_column(CapabilityValueType, "capability_value_type"), nullable=False
    )
    comparator: Mapped[CapabilityComparator] = mapped_column(
        enum_column(CapabilityComparator, "capability_comparator"), nullable=False
    )
    unit: Mapped[str | None] = mapped_column(sa.String(20))
    #: Nomes per a `ordinal`: de menor a major.
    ordinal_scale: Mapped[list[str] | None] = mapped_column(JSONDocument)


class CertificationStandardRow(Base):
    __tablename__ = "certification_standards"

    code: Mapped[str] = mapped_column(sa.String(40), primary_key=True)
    label: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: De menor a major. Nul si la norma no te nivells.
    levels: Mapped[list[str] | None] = mapped_column(JSONDocument)


# --------------------------------------------------------------------------- #
# Procedencia del coneixement actual                                           #
# --------------------------------------------------------------------------- #


def origin_constraints() -> tuple[sa.CheckConstraint, ...]:
    """Els CHECK de l'origen (`KnowledgeOriginMixin`), un joc nou per taula."""
    return (
        sa.CheckConstraint(
            in_values("knowledge_origin", KnowledgeOrigin), name="knowledge_origin_valid"
        ),
        sa.CheckConstraint(
            in_values("verification_status", VerificationStatus), name="verification_valid"
        ),
        sa.CheckConstraint(in_values("lifecycle", KnowledgeLifecycle), name="lifecycle_valid"),
    )


def provenance_constraints() -> tuple[sa.CheckConstraint, ...]:
    """Els CHECK de la procedencia completa (`KnowledgeProvenanceMixin`)."""
    return (
        *origin_constraints(),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until >= as_of", name="valid_until_after_as_of"
        ),
    )


class _OriginStatusMixin:
    """Origen, verificacio i cicle de vida d'una dada actual.

    * `knowledge_origin`: declarada per una persona o promoguda des de l'historic.
    * `verification_status`: si hi ha evidencia verificada (decisio humana).
    * `lifecycle`: `current` compta; `in_progress` i `retired`, no.
    """

    knowledge_origin: Mapped[KnowledgeOrigin] = mapped_column(
        enum_column(KnowledgeOrigin, "knowledge_origin"),
        nullable=False,
        default=KnowledgeOrigin.DECLARED,
        server_default=KnowledgeOrigin.DECLARED.value,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        enum_column(VerificationStatus, "verification_status"),
        nullable=False,
        default=VerificationStatus.PENDING_VERIFICATION,
        server_default=VerificationStatus.PENDING_VERIFICATION.value,
    )
    lifecycle: Mapped[KnowledgeLifecycle] = mapped_column(
        enum_column(KnowledgeLifecycle, "knowledge_lifecycle"),
        nullable=False,
        default=KnowledgeLifecycle.CURRENT,
        server_default=KnowledgeLifecycle.CURRENT.value,
    )


class KnowledgeOriginMixin(_OriginStatusMixin):
    """Les taules del perfil que, a 0052, passen a ser coneixement actual (solvencia,
    experiencia, assegurances, registres i persones). Tenen la seva propia vigencia
    (l'any, la data de fi, `expires_at`) o cap.

    `as_of` es EXPLICIT: nul = ningu no ha dit de quan es la dada (totes les files
    anteriors a 0052 i les que no la informen). La fotografia hi posa la data de
    creacio en UTC. Cap valor per defecte: un valor per defecte no es distingiria
    d'una data dita per una persona.
    """

    as_of: Mapped[date | None] = mapped_column(sa.Date)


class KnowledgeProvenanceMixin(_OriginStatusMixin):
    """La procedencia completa del coneixement de 0045 en endavant: de quan es la dada
    (`as_of`, obligatoria) i fins quan val (`valid_until`; nul = sense data)."""

    as_of: Mapped[date] = mapped_column(
        sa.Date, nullable=False, default=date.today, server_default=sa.text("CURRENT_DATE")
    )
    valid_until: Mapped[date | None] = mapped_column(sa.Date)


def _company_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )


def _service_line_fk(nullable: bool) -> Any:
    return mapped_column(
        sa.String(40),
        sa.ForeignKey("service_lines.code", ondelete="RESTRICT"),
        nullable=nullable,
    )


def coverage_completeness_column() -> Mapped[CoverageCompleteness]:
    """`unknown` per defecte: el que hi havia abans de la 0047 no afirmava res."""
    return mapped_column(
        enum_column(CoverageCompleteness, "coverage_completeness"),
        nullable=False,
        default=CoverageCompleteness.UNKNOWN,
        server_default=CoverageCompleteness.UNKNOWN.value,
    )


def coverage_completeness_check() -> sa.CheckConstraint:
    return sa.CheckConstraint(
        in_values("coverage_completeness", CoverageCompleteness),
        name="coverage_completeness_valid",
    )


class CoverageReadMixin:
    """`coverage` tal com la llegeix l'API: completesa i arees en ordre canonic."""

    @property
    def coverage(self) -> dict[str, Any]:
        from app.knowledge.coverage import AreaIn, CoverageIn, coverage_dict

        return coverage_dict(
            CoverageIn(
                completeness=self.coverage_completeness,  # type: ignore[attr-defined]
                areas=tuple(
                    AreaIn(a.kind, a.label, a.code, a.code_system)
                    for a in self.coverage_areas  # type: ignore[attr-defined]
                ),
            )
        )


def coverage_areas_relationship(model_name: str, entity_type: CoverageEntityType) -> Any:
    """Les arees d'una dada, nomes per llegir-les. L'enllac es polimorfic (sense
    FK), com el de l'evidencia; escriure-les es cosa del servei de cobertura."""
    return relationship(
        "KnowledgeCoverageArea",
        primaryjoin=(
            f"and_(foreign(KnowledgeCoverageArea.entity_id) == {model_name}.id, "
            f"KnowledgeCoverageArea.entity_type == '{entity_type.value}')"
        ),
        viewonly=True,
        lazy="selectin",
        order_by="KnowledgeCoverageArea.created_at",
    )


# --------------------------------------------------------------------------- #
# Estat actual                                                                 #
# --------------------------------------------------------------------------- #


class CompanyOperatingFact(UUIDPrimaryKeyMixin, KnowledgeProvenanceMixin, CoverageReadMixin, Base):
    """Un fet de l'empresa que no te lloc a cap altra taula (operador CNMC, NRN...)."""

    __tablename__ = "company_operating_facts"
    __table_args__ = (
        *provenance_constraints(),
        sa.CheckConstraint(in_values("key", OperatingFactKey), name="key_valid"),
        sa.UniqueConstraint("company_id", "key", name="uq_company_operating_facts_key"),
        coverage_completeness_check(),
        # 0052: un nombre nomes als fets quantitatius, i mai negatiu. Que un fet
        # quantitatiu afirmat porti el nombre ho imposa el servei (vocabulari).
        sa.CheckConstraint(
            "value_number IS NULL OR value_number >= 0", name="value_number_non_negative"
        ),
        sa.CheckConstraint(
            "value_number IS NULL OR key IN ('ipv4_assignable_count')",
            name="value_number_numeric_key",
        ),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    key: Mapped[OperatingFactKey] = mapped_column(
        enum_column(OperatingFactKey, "operating_fact_key"), nullable=False
    )
    value: Mapped[bool] = mapped_column(sa.Boolean, nullable=False)
    #: Nomes als fets quantitatius (0052; `vocabulary.OPERATING_FACT_VALUE_TYPES`):
    #: quants (p. ex. adreces IPv4 assignables). Als booleans es sempre nul.
    value_number: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4))
    #: Aclariment curt ("CPD propi a un centre Tier-3"). Mai imports ni condicions comercials.
    detail: Mapped[str | None] = mapped_column(sa.String(500))
    #: Cobertura territorial del fet (0047): on es cert, i si la llista es tota.
    coverage_completeness: Mapped[CoverageCompleteness] = coverage_completeness_column()
    coverage_areas: Mapped[list[KnowledgeCoverageArea]] = coverage_areas_relationship(
        "CompanyOperatingFact", CoverageEntityType.OPERATING_FACT
    )


class CompanySolution(UUIDPrimaryKeyMixin, KnowledgeProvenanceMixin, Base):
    """Com l'empresa dona un servei: «centraleta cloud allotjada al CPD propi», per exemple."""

    __tablename__ = "company_solutions"
    __table_args__ = (
        *provenance_constraints(),
        sa.UniqueConstraint("company_id", "name", name="uq_company_solutions_name"),
        sa.UniqueConstraint("id", "company_id", name="uq_company_solutions_id_company"),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    service_line_code: Mapped[str] = _service_line_fk(nullable=False)
    name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    vendor: Mapped[str | None] = mapped_column(sa.String(160))
    #: La llicencia importa: una funcio pot ser nomes d'una edicio.
    edition: Mapped[str | None] = mapped_column(sa.String(80))


class SolutionCapability(UUIDPrimaryKeyMixin, KnowledgeProvenanceMixin, Base):
    """Que fa una solucio respecte d'una capacitat del vocabulari.

    L'absencia de fila vol dir **desconegut**, no "no ho fa". Per dir que no ho
    fa hi ha `not_supported`.
    """

    __tablename__ = "solution_capabilities"
    __table_args__ = (
        *provenance_constraints(),
        sa.CheckConstraint(in_values("status", CapabilityStatus), name="status_valid"),
        sa.UniqueConstraint(
            "solution_id", "capability_code", name="uq_solution_capabilities_capability"
        ),
        sa.ForeignKeyConstraint(
            ["solution_id", "company_id"],
            ["company_solutions.id", "company_solutions.company_id"],
            ondelete="CASCADE",
        ),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    solution_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    capability_code: Mapped[str] = mapped_column(
        sa.String(80),
        sa.ForeignKey("capability_definitions.code", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[CapabilityStatus] = mapped_column(
        enum_column(CapabilityStatus, "capability_status"), nullable=False
    )
    #: Per a capacitats `number` (canals, minuts...).
    value_number: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4))
    #: Per a capacitats `ordinal` (un valor de l'escala del vocabulari).
    value_level: Mapped[str | None] = mapped_column(sa.String(20))
    limits_note: Mapped[str | None] = mapped_column(sa.Text)


class CompanyProduct(UUIDPrimaryKeyMixin, KnowledgeProvenanceMixin, Base):
    """Un producte que l'empresa fa servir o homologa. Sense preus: no toca encara."""

    __tablename__ = "company_products"
    __table_args__ = (
        *provenance_constraints(),
        sa.UniqueConstraint(
            "company_id", "manufacturer", "model", name="uq_company_products_model"
        ),
        sa.UniqueConstraint("id", "company_id", name="uq_company_products_id_company"),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    manufacturer: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    service_line_code: Mapped[str | None] = _service_line_fk(nullable=True)


class SolutionProduct(UUIDPrimaryKeyMixin, KnowledgeProvenanceMixin, Base):
    """Un producte compatible o homologat per a una solucio."""

    __tablename__ = "solution_products"
    __table_args__ = (
        *provenance_constraints(),
        sa.UniqueConstraint("solution_id", "product_id", name="uq_solution_products_pair"),
        sa.ForeignKeyConstraint(
            ["solution_id", "company_id"],
            ["company_solutions.id", "company_solutions.company_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id", "company_id"],
            ["company_products.id", "company_products.company_id"],
            ondelete="CASCADE",
        ),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    solution_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    product_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)


class CompanyPartner(UUIDPrimaryKeyMixin, KnowledgeProvenanceMixin, Base):
    """Un tercer: proveidor, operador majorista, subcontractista, soci...

    Una sola taula per a tots: el mateix tercer sovint fa diversos papers, i el que
    en diu que fa es a `partner_roles`. Sense condicions economiques.
    """

    __tablename__ = "company_partners"
    __table_args__ = (
        *provenance_constraints(),
        sa.UniqueConstraint("company_id", "name", name="uq_company_partners_name"),
        sa.UniqueConstraint("id", "company_id", name="uq_company_partners_id_company"),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    tax_id: Mapped[str | None] = mapped_column(sa.String(32))


class PartnerRoleRow(UUIDPrimaryKeyMixin, KnowledgeProvenanceMixin, CoverageReadMixin, Base):
    __tablename__ = "partner_roles"
    __table_args__ = (
        *provenance_constraints(),
        sa.CheckConstraint(in_values("role", PartnerRole), name="role_valid"),
        coverage_completeness_check(),
        sa.ForeignKeyConstraint(
            ["partner_id", "company_id"],
            ["company_partners.id", "company_partners.company_id"],
            ondelete="CASCADE",
        ),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    partner_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    role: Mapped[PartnerRole] = mapped_column(
        enum_column(PartnerRole, "partner_role"), nullable=False
    )
    service_line_code: Mapped[str | None] = _service_line_fk(nullable=True)
    #: Territori en text lliure (anterior a la 0047). Es conserva tal qual.
    territory: Mapped[str | None] = mapped_column(sa.String(160))
    #: Cobertura territorial estructurada del paper (0047).
    coverage_completeness: Mapped[CoverageCompleteness] = coverage_completeness_column()
    coverage_areas: Mapped[list[KnowledgeCoverageArea]] = coverage_areas_relationship(
        "PartnerRoleRow", CoverageEntityType.PARTNER_ROLE
    )


# --------------------------------------------------------------------------- #
# Historic                                                                     #
# --------------------------------------------------------------------------- #


class KnowledgeCoverageArea(UUIDPrimaryKeyMixin, Base):
    """Una area on una dada de coneixement es certa: un municipi, una comarca, una
    adreca... Penja d'un fet operatiu, d'un servei o d'un paper de partner
    (`entity_type` + `entity_id`, sense FK, com els enllacos d'evidencia).

    Es nomes estructura: `code` i `code_system` (INE, NUTS...) son opcionals i
    no hi ha cap jerarquia ni cap GIS. Comparar arees es cosa del comparador
    territorial (fase posterior); aqui nomes es conserva el que l'empresa diu.
    Les arees d'una dada son un conjunt: `(kind, code_system, code, label)` es
    unic per dada (`label` ja arriba amb els espais normalitzats).
    """

    __tablename__ = "knowledge_coverage_areas"
    __table_args__ = (
        sa.CheckConstraint(in_values("entity_type", CoverageEntityType), name="entity_type_valid"),
        sa.CheckConstraint(in_values("kind", AreaKind), name="kind_valid"),
        sa.Index("ix_knowledge_coverage_areas_entity", "company_id", "entity_type", "entity_id"),
        # Les arees d'una dada son un conjunt: la mateixa identitat canonica, un sol cop.
        # `coalesce` perque dos NULL no son "diferents" aqui.
        sa.Index(
            "uq_knowledge_coverage_areas_identity",
            "entity_type",
            "entity_id",
            "kind",
            sa.text("coalesce(code_system, '')"),
            sa.text("coalesce(code, '')"),
            "label",
            unique=True,
        ),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    entity_type: Mapped[CoverageEntityType] = mapped_column(
        enum_column(CoverageEntityType, "coverage_entity_type"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid(as_uuid=True), nullable=False)
    kind: Mapped[AreaKind] = mapped_column(enum_column(AreaKind, "area_kind"), nullable=False)
    #: El nom tal com el diu l'empresa ("Banyoles"). Obligatori: una area sense nom no es res.
    label: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: Codi oficial opcional ("17015") i el seu sistema ("INE", "NUTS").
    code: Mapped[str | None] = mapped_column(sa.String(40))
    code_system: Mapped[str | None] = mapped_column(sa.String(20))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class HistoricBid(UUIDPrimaryKeyMixin, Base):
    """Una licitacio analitzada en el passat. Nomes dades publiques i la decisio.

    Sense oferta, baixa ni marge: son dades confidencials i aquesta iteracio no
    les necessita.
    """

    __tablename__ = "historic_bids"
    __table_args__ = (
        sa.CheckConstraint(in_values("decision", HistoricBidDecision), name="decision_valid"),
        sa.CheckConstraint(in_values("result", HistoricBidResult), name="result_valid"),
        sa.UniqueConstraint("company_id", "reference", name="uq_historic_bids_reference"),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    #: Identificador de l'expedient a l'origen (la carpeta, per exemple).
    reference: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    buyer_name: Mapped[str | None] = mapped_column(sa.String(300))
    bid_date: Mapped[date | None] = mapped_column(sa.Date)
    service_line_codes: Mapped[list[str] | None] = mapped_column(JSONDocument)
    #: Pressupost base de licitacio (public).
    base_budget: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 2))
    decision: Mapped[HistoricBidDecision] = mapped_column(
        enum_column(HistoricBidDecision, "historic_bid_decision"), nullable=False
    )
    decision_reason: Mapped[str | None] = mapped_column(sa.Text)
    result: Mapped[HistoricBidResult] = mapped_column(
        enum_column(HistoricBidResult, "historic_bid_result"),
        nullable=False,
        default=HistoricBidResult.UNKNOWN,
    )
    source_ref: Mapped[str | None] = mapped_column(sa.String(500))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


class KnowledgeObservation(UUIDPrimaryKeyMixin, Base):
    """El que l'historic diu de l'empresa. Un suggeriment, mai coneixement actual.

    Porta una `proposal` amb la forma exacta de la fila que en naixeria si una
    persona la promogues (`app/knowledge/proposals.py`). Promoure-la es un acte
    humà; aquesta taula no en te cap cami automatic.
    """

    __tablename__ = "knowledge_observations"
    __table_args__ = (
        sa.CheckConstraint(in_values("subject", ObservationSubject), name="subject_valid"),
        sa.CheckConstraint(in_values("status", ObservationStatus), name="status_valid"),
        sa.CheckConstraint(
            "status <> 'promoted' OR promoted_entity_id IS NOT NULL", name="promoted_has_target"
        ),
        sa.CheckConstraint(
            "status = 'candidate' OR reviewed_at IS NOT NULL", name="reviewed_has_date"
        ),
        sa.Index("ix_knowledge_observations_scope", "company_id", "subject", "status"),
    )

    company_id: Mapped[uuid.UUID] = _company_fk()
    historic_bid_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("historic_bids.id", ondelete="SET NULL")
    )
    subject: Mapped[ObservationSubject] = mapped_column(
        enum_column(ObservationSubject, "observation_subject"), nullable=False
    )
    #: La clau de coneixement de que parla ("ENS", "datacenter_tier3", el nom d'una solucio).
    subject_key: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    #: Que diu l'historic, en una frase. Mai imports ni condicions comercials.
    statement: Mapped[str] = mapped_column(sa.Text, nullable=False)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONDocument, nullable=False)
    observed_on: Mapped[date | None] = mapped_column(sa.Date)
    source_ref: Mapped[str | None] = mapped_column(sa.String(500))
    status: Mapped[ObservationStatus] = mapped_column(
        enum_column(ObservationStatus, "observation_status"),
        nullable=False,
        default=ObservationStatus.CANDIDATE,
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(sa.Text)
    #: Quina fila de coneixement actual en va neixer en promoure-la.
    promoted_entity_type: Mapped[str | None] = mapped_column(sa.String(40))
    promoted_entity_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
