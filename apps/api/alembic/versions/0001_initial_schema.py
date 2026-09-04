"""Esquema inicial: identitat, perfil corporatiu, experiencia i evidencies.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOW = sa.text("now()")


def _enum(name: str, *values: str) -> sa.Enum:
    """Enum portable: VARCHAR amb validacio a la capa d'aplicacio.

    No es fa servir el tipus ENUM natiu de PostgreSQL per poder afegir valors
    nous sense ALTER TYPE, i no s'hi posa CHECK per no obligar a migrar cada
    cop que el domini creix. La validesa la garanteixen Pydantic i SQLAlchemy.
    """
    return sa.Enum(*values, name=name, native_enum=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
    ]


def _actor() -> list[sa.Column]:
    return [
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("updated_by", sa.Uuid(as_uuid=True), nullable=True),
    ]


def _actor_fks(table: str) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=f"fk_{table}_created_by_users", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=f"fk_{table}_updated_by_users", ondelete="SET NULL"
        ),
    ]


def _company_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["company_id"], ["companies.id"], name=f"fk_{table}_company_id_companies", ondelete="CASCADE"
    )


def upgrade() -> None:
    # ---------------------------------------------------------------- identitat
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_superuser", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False),
        sa.Column("mfa_secret", sa.String(255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "companies",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("legal_name", sa.String(255), nullable=False),
        sa.Column("trade_name", sa.String(255), nullable=True),
        sa.Column("tax_id", sa.String(64), nullable=True),
        sa.Column("legal_form", sa.String(120), nullable=True),
        sa.Column("incorporation_date", sa.Date(), nullable=True),
        sa.Column("website", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("country", sa.String(2), nullable=True),
        sa.Column("autonomous_region", sa.String(120), nullable=True),
        sa.Column("province", sa.String(120), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("postal_code", sa.String(16), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("employee_count", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_companies"),
    )
    op.create_index("ix_companies_tax_id", "companies", ["tax_id"])

    op.create_table(
        "company_memberships",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            _enum("membership_role", "owner", "admin", "bid_manager", "technical", "viewer"),
            nullable=False,
        ),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_company_memberships"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_company_memberships_user_id_users", ondelete="CASCADE"
        ),
        _company_fk("company_memberships"),
        sa.UniqueConstraint("user_id", "company_id", name="uq_membership_user_company"),
    )
    op.create_index("ix_company_memberships_user_id", "company_memberships", ["user_id"])
    op.create_index("ix_company_memberships_company_id", "company_memberships", ["company_id"])

    # --------------------------------------------------------------------- cpv
    op.create_table(
        "cpv_codes",
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("parent_code", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("code", name="pk_cpv_codes"),
    )
    op.create_index("ix_cpv_codes_parent_code", "cpv_codes", ["parent_code"])

    # ----------------------------------------------------------------- perfil
    op.create_table(
        "company_services",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "specialization_level",
            _enum("specialization_level", "basic", "intermediate", "advanced", "expert"),
            nullable=True,
        ),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_services"),
        _company_fk("company_services"),
        *_actor_fks("company_services"),
        sa.UniqueConstraint("company_id", "name", name="uq_company_services_company_name"),
    )
    op.create_index("ix_company_services_company_id", "company_services", ["company_id"])

    op.create_table(
        "company_territories",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column(
            "territory_type",
            _enum(
                "territory_type",
                "municipality",
                "comarca",
                "province",
                "autonomous_region",
                "country",
                "nuts",
                "other",
            ),
            nullable=False,
        ),
        sa.Column("code", sa.String(32), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("willing_to_work", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_territories"),
        _company_fk("company_territories"),
        *_actor_fks("company_territories"),
        sa.UniqueConstraint(
            "company_id", "territory_type", "name", name="uq_company_territories_company_type_name"
        ),
    )
    op.create_index("ix_company_territories_company_id", "company_territories", ["company_id"])

    op.create_table(
        "company_financial_years",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_financial_years"),
        _company_fk("company_financial_years"),
        *_actor_fks("company_financial_years"),
        sa.UniqueConstraint("company_id", "year", name="uq_company_financial_years_company_year"),
        sa.CheckConstraint(
            "year >= 1900 AND year <= 2200", name="ck_company_financial_years_financial_year_range"
        ),
    )
    op.create_index(
        "ix_company_financial_years_company_id", "company_financial_years", ["company_id"]
    )

    op.create_table(
        "company_contract_preferences",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("minimum_interesting_contract", sa.Numeric(18, 2), nullable=True),
        sa.Column("preferred_contract_min", sa.Numeric(18, 2), nullable=True),
        sa.Column("preferred_contract_max", sa.Numeric(18, 2), nullable=True),
        sa.Column("maximum_recommended_contract", sa.Numeric(18, 2), nullable=True),
        sa.Column("maximum_assumable_contract", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_contract_preferences"),
        _company_fk("company_contract_preferences"),
        *_actor_fks("company_contract_preferences"),
    )
    op.create_index(
        "ix_company_contract_preferences_company_id",
        "company_contract_preferences",
        ["company_id"],
        unique=True,
    )

    # ---------------------------------------------------------- acreditacions
    op.create_table(
        "company_certifications",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("issuer", sa.String(200), nullable=True),
        sa.Column("certificate_number", sa.String(120), nullable=True),
        sa.Column("scope", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column(
            "verification_status",
            _enum("verification_status", "pending_verification", "verified"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_certifications"),
        _company_fk("company_certifications"),
        *_actor_fks("company_certifications"),
        sa.CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at >= issued_at",
            name="ck_company_certifications_certification_dates_order",
        ),
    )
    op.create_index(
        "ix_company_certifications_company_id", "company_certifications", ["company_id"]
    )

    op.create_table(
        "company_registrations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column(
            "type",
            _enum(
                "registration_type",
                "rolece",
                "reli",
                "business_classification",
                "professional_registry",
                "administrative_accreditation",
                "other",
            ),
            nullable=False,
        ),
        sa.Column("registration_number", sa.String(120), nullable=True),
        sa.Column("issuer", sa.String(200), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_registrations"),
        _company_fk("company_registrations"),
        *_actor_fks("company_registrations"),
        sa.CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at >= issued_at",
            name="ck_company_registrations_registration_dates_order",
        ),
    )
    op.create_index(
        "ix_company_registrations_company_id", "company_registrations", ["company_id"]
    )

    op.create_table(
        "company_insurances",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "type",
            _enum(
                "insurance_type",
                "civil_liability",
                "professional_indemnity",
                "workers_compensation",
                "surety_bond",
                "all_risk",
                "other",
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(200), nullable=True),
        sa.Column("policy_number", sa.String(120), nullable=True),
        sa.Column("coverage_description", sa.Text(), nullable=True),
        sa.Column("coverage_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("starts_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_insurances"),
        _company_fk("company_insurances"),
        *_actor_fks("company_insurances"),
        sa.CheckConstraint(
            "expires_at IS NULL OR starts_at IS NULL OR expires_at >= starts_at",
            name="ck_company_insurances_insurance_dates_order",
        ),
    )
    op.create_index("ix_company_insurances_company_id", "company_insurances", ["company_id"])

    # ------------------------------------------------------------- equip huma
    op.create_table(
        "company_people",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("first_name", sa.String(120), nullable=False),
        sa.Column("last_name", sa.String(160), nullable=True),
        sa.Column("role", sa.String(160), nullable=True),
        sa.Column("department", sa.String(160), nullable=True),
        sa.Column("professional_profile", sa.Text(), nullable=True),
        sa.Column("years_experience", sa.Integer(), nullable=True),
        sa.Column(
            "availability_status",
            _enum(
                "availability_status",
                "available",
                "partially_available",
                "unavailable",
                "unknown",
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_people"),
        _company_fk("company_people"),
        *_actor_fks("company_people"),
    )
    op.create_index("ix_company_people_company_id", "company_people", ["company_id"])

    op.create_table(
        "person_qualifications",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("person_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "type",
            _enum(
                "qualification_type",
                "degree",
                "professional_certification",
                "training",
                "authorization",
                "other",
            ),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("issuer", sa.String(200), nullable=True),
        sa.Column("credential_number", sa.String(120), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_person_qualifications"),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["company_people.id"],
            name="fk_person_qualifications_person_id_company_people",
            ondelete="CASCADE",
        ),
        *_actor_fks("person_qualifications"),
        sa.CheckConstraint(
            "expires_at IS NULL OR issued_at IS NULL OR expires_at >= issued_at",
            name="ck_person_qualifications_qualification_dates_order",
        ),
    )
    op.create_index("ix_person_qualifications_person_id", "person_qualifications", ["person_id"])

    # ----------------------------------------------------------- experiencia
    op.create_table(
        "company_projects",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("client_name", sa.String(255), nullable=True),
        sa.Column(
            "client_type",
            _enum("client_type", "public", "private", "mixed", "other"),
            nullable=False,
        ),
        sa.Column("sector", sa.String(160), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column(
            "status",
            _enum("project_status", "planned", "in_progress", "completed", "cancelled"),
            nullable=False,
        ),
        sa.Column("reference_contact", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_company_projects"),
        _company_fk("company_projects"),
        *_actor_fks("company_projects"),
        sa.CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="ck_company_projects_project_dates_order",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0", name="ck_company_projects_project_amount_positive"
        ),
    )
    op.create_index("ix_company_projects_company_id", "company_projects", ["company_id"])
    op.create_index(
        "ix_company_projects_company_end_date", "company_projects", ["company_id", "end_date"]
    )
    op.create_index(
        "ix_company_projects_company_amount", "company_projects", ["company_id", "amount"]
    )

    op.create_table(
        "project_services",
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("service_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("project_id", "service_id", name="pk_project_services"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["company_projects.id"],
            name="fk_project_services_project_id_company_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["company_services.id"],
            name="fk_project_services_service_id_company_services",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "project_cpvs",
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("cpv_code", sa.String(10), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("project_id", "cpv_code", name="pk_project_cpvs"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["company_projects.id"],
            name="fk_project_cpvs_project_id_company_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cpv_code"], ["cpv_codes.code"], name="fk_project_cpvs_cpv_code_cpv_codes", ondelete="CASCADE"
        ),
    )

    op.create_table(
        "service_cpvs",
        sa.Column("service_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("cpv_code", sa.String(10), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("service_id", "cpv_code", name="pk_service_cpvs"),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["company_services.id"],
            name="fk_service_cpvs_service_id_company_services",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["cpv_code"], ["cpv_codes.code"], name="fk_service_cpvs_cpv_code_cpv_codes", ondelete="CASCADE"
        ),
    )

    op.create_table(
        "company_cpv_preferences",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("cpv_code", sa.String(10), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("excluded", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_company_cpv_preferences"),
        _company_fk("company_cpv_preferences"),
        sa.ForeignKeyConstraint(
            ["cpv_code"],
            ["cpv_codes.code"],
            name="fk_company_cpv_preferences_cpv_code_cpv_codes",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "company_id", "cpv_code", name="uq_company_cpv_preferences_company_code"
        ),
    )
    op.create_index(
        "ix_company_cpv_preferences_company_id", "company_cpv_preferences", ["company_id"]
    )
    op.create_index("ix_company_cpv_preferences_cpv_code", "company_cpv_preferences", ["cpv_code"])

    # ------------------------------------------------------------ evidencies
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(160), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column(
            "category",
            _enum(
                "document_category",
                "certification",
                "insurance",
                "registration",
                "project_evidence",
                "financial",
                "legal",
                "cv",
                "technical",
                "commercial",
                "other",
            ),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("issued_at", sa.Date(), nullable=True),
        sa.Column("expires_at", sa.Date(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        *_timestamps(),
        *_actor(),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        _company_fk("documents"),
        *_actor_fks("documents"),
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
        sa.CheckConstraint("file_size >= 0", name="ck_documents_document_size_positive"),
    )
    op.create_index("ix_documents_company_id", "documents", ["company_id"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])
    op.create_index("ix_documents_company_sha256", "documents", ["company_id", "sha256"])

    op.create_table(
        "document_links",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("document_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "entity_type",
            _enum(
                "linked_entity_type",
                "company",
                "company_service",
                "company_financial_year",
                "company_certification",
                "company_registration",
                "company_insurance",
                "company_person",
                "person_qualification",
                "company_project",
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "relationship_type",
            _enum(
                "relationship_type",
                "certificate",
                "certificate_of_good_execution",
                "policy",
                "contract",
                "invoice",
                "financial_statement",
                "cv",
                "diploma",
                "accreditation",
                "technical_sheet",
                "reference_letter",
                "supporting_document",
                "other",
            ),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_document_links"),
        _company_fk("document_links"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_links_document_id_documents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name="fk_document_links_created_by_users", ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "document_id",
            "entity_type",
            "entity_id",
            "relationship_type",
            name="uq_document_links_unique_link",
        ),
    )
    op.create_index("ix_document_links_company_id", "document_links", ["company_id"])
    op.create_index("ix_document_links_document_id", "document_links", ["document_id"])
    op.create_index("ix_document_links_entity", "document_links", ["entity_type", "entity_id"])
    op.create_index(
        "ix_document_links_company_entity",
        "document_links",
        ["company_id", "entity_type", "entity_id"],
    )

    # -------------------------------------------------------------- auditoria
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "action",
            _enum(
                "audit_action",
                "create",
                "update",
                "delete",
                "login",
                "login_failed",
                "logout",
                "download",
            ),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(80), nullable=True),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("summary", sa.String(500), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
        _company_fk("audit_logs"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_audit_logs_user_id_users", ondelete="SET NULL"
        ),
    )
    op.create_index("ix_audit_logs_company_created", "audit_logs", ["company_id", "created_at"])


def downgrade() -> None:
    for table in (
        "audit_logs",
        "document_links",
        "documents",
        "company_cpv_preferences",
        "service_cpvs",
        "project_cpvs",
        "project_services",
        "company_projects",
        "person_qualifications",
        "company_people",
        "company_insurances",
        "company_registrations",
        "company_certifications",
        "company_contract_preferences",
        "company_financial_years",
        "company_territories",
        "company_services",
        "cpv_codes",
        "company_memberships",
        "companies",
        "users",
    ):
        op.drop_table(table)
