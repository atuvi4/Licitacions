"""Classificacio global dels documents, revisio de rols de l'operador i override ancorat.

Fase 3B-2 (spec §3.2). Additiva i reversible.

Quatre taules globals noves (sense `company_id`):

* `global_document_classification_runs`: una passada del classificador sobre els
  documents analitzables d'un tender. Unica per `(tender_id, input_fingerprint)`;
  una sola vigent per tender (index parcial).
* `global_document_role_suggestions`: el rol proposat per a cada document,
  ancorat al `content_sha256` que es va classificar.
* `global_document_classification_findings`: el que no quadra del conjunt.
* `document_role_reviews`: el rol global revisat per l'operador. NOMES D'AFEGIR:
  un trigger de PostgreSQL rebutja UPDATE i DELETE directe (com
  `review_item_revisions`, 0040); una cascada de document o licitacio passa.

`tender_document_role_decisions` (override de cada empresa) guanya
`content_sha256` NOT NULL: la versio del fitxer sobre la qual es va decidir.
Backfill: el sha del fitxer VIGENT del document. **Una decisio d'un document
sense cap fitxer vigent amb contingut s'esborra** (no hi ha cap versio a la qual
ancorar-la, i un sha buit o inventat la faria semblar vigent); la migracio en
diu el nombre amb un `RAISE NOTICE` a PostgreSQL (`0059 - N decisions ...`). En
una base real nomes poden existir decisions des de la classificacio d'una
licitacio manual, que nomes classifica documents amb contingut: el nombre
esperat es zero.

`tender_analysis_runs` guanya `accepted_incomplete_package` (boolea nullable,
sense valor per defecte; 3B-2 §4.4): si el paquet constava com a incomplet, si qui
va demanar l'analisi ho va acceptar explicitament. Les runs existents queden a nul
(no constava res). El downgrade la treu.

Tots els enums porten CHECK. SQLite (nomes tests, que creen l'esquema amb els
models): les taules es creen senceres; la columna nova s'afegeix sense NOT NULL
ni backfill i el trigger no existeix.

Downgrade: refusa de manera explicita (RAISE abans de cap DROP) si hi ha alguna
revisio de rol: tornar enrere en perdria la decisio humana. Sense revisions,
treu el trigger ABANS de la taula, les quatre taules i les dues columnes (les decisions
esborrades per l'upgrade no es restauren).

Revision ID: 0059_global_classification
Revises: 0058_public_documents
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0059_global_classification"
down_revision: str | None = "0058_public_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNS = "global_document_classification_runs"
_SUGGESTIONS = "global_document_role_suggestions"
_FINDINGS = "global_document_classification_findings"
_REVIEWS = "document_role_reviews"
_DECISIONS = "tender_document_role_decisions"
_ANALYSIS_RUNS = "tender_analysis_runs"

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_TRIGGERS = ("document_processing", "document_intake", "operator_request")
_ROLES = (
    "pcap",
    "ppt",
    "characteristics_table",
    "offer_model",
    "inventory_annex",
    "justification_report",
    "approval_resolution",
    "administrative_form",
    "notice",
    "other",
)
_CONFIDENCE = ("high", "medium", "low")
_DOCUMENT_TYPES = (
    "administrative_terms",
    "technical_terms",
    "notice",
    "award",
    "additional",
    "other",
)
_FINDING_CODES = (
    "title_content_mismatch",
    "duplicate_bytes",
    "duplicate_text",
    "duplicate_internal_code",
    "referenced_document_missing",
    "required_role_missing",
    "no_text",
    "format_not_analyzable",
    "role_undetermined",
)
_SEVERITIES = ("high", "medium", "low", "info")
_AUTHORITIES = ("platform_operator", "promoted_from_company")

# El mateix text que `app.models.global_classification`.
_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION document_role_reviews_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'document_role_reviews es nomes d''afegir (%)', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""
_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER document_role_reviews_append_only
BEFORE UPDATE OR DELETE ON document_role_reviews
FOR EACH ROW EXECUTE FUNCTION document_role_reviews_append_only()
"""
_DROP_TRIGGER = "DROP TRIGGER IF EXISTS document_role_reviews_append_only ON document_role_reviews"
_DROP_FUNCTION = "DROP FUNCTION IF EXISTS document_role_reviews_append_only()"

#: El sha del fitxer vigent de cada document amb decisio.
_BACKFILL_SHA = (
    "UPDATE tender_document_role_decisions SET content_sha256 = ("
    "SELECT tender_document_contents.sha256 FROM tender_document_files "
    "JOIN tender_document_contents "
    "ON tender_document_contents.id = tender_document_files.content_id "
    "WHERE tender_document_files.tender_document_id = "
    "tender_document_role_decisions.tender_document_id "
    "AND tender_document_files.is_current LIMIT 1)"
)
#: Les decisions sense versio a la qual ancorar-se s'esborren, i se'n diu el nombre.
_DROP_UNANCHORED = """
DO $$
DECLARE removed integer;
BEGIN
    DELETE FROM tender_document_role_decisions WHERE content_sha256 IS NULL;
    GET DIAGNOSTICS removed = ROW_COUNT;
    RAISE NOTICE '0059 - % decisions de rol sense fitxer vigent esborrades', removed;
END
$$
"""

# Sense `:`, `%` ni cometes al missatge: `op.execute` el compila com a `text()`.
_REFUSAL = (
    "0059 downgrade refusat - hi ha revisions de rol de l operador "
    "(document_role_reviews) i tornar enrere les perdria"
)
_REFUSE_DOWNGRADE = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM document_role_reviews) THEN
        RAISE EXCEPTION '{_REFUSAL}';
    END IF;
END
$$
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        _RUNS,
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("classifier_version", sa.String(length=40), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("trigger", _enum(_TRIGGERS, "global_classification_trigger"), nullable=False),
        sa.Column("document_count", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        _created_at(),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in_list("trigger", _TRIGGERS), name="trigger_valid"),
        sa.CheckConstraint("document_count >= 0", name="count_not_negative"),
        sa.UniqueConstraint(
            "tender_id",
            "input_fingerprint",
            name="uq_global_document_classification_runs_fingerprint",
        ),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "uq_global_document_classification_runs_current",
        _RUNS,
        ["tender_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current = 1"),
    )

    op.create_table(
        _SUGGESTIONS,
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tender_document_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=True),
        sa.Column("role", _enum(_ROLES, "document_role"), nullable=False),
        sa.Column("confidence", _enum(_CONFIDENCE, "role_confidence"), nullable=False),
        sa.Column("document_type", _enum(_DOCUMENT_TYPES, "tender_document_type"), nullable=False),
        sa.Column("signals", _json(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in_list("role", _ROLES), name="role_valid"),
        sa.CheckConstraint(_in_list("confidence", _CONFIDENCE), name="confidence_valid"),
        sa.CheckConstraint(_in_list("document_type", _DOCUMENT_TYPES), name="document_type_valid"),
        sa.UniqueConstraint(
            "run_id", "tender_document_id", name="uq_global_document_role_suggestions_document"
        ),
        sa.ForeignKeyConstraint(["run_id"], [f"{_RUNS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tender_document_id"], ["tender_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["tender_document_extractions.id"], ondelete="SET NULL"
        ),
    )

    op.create_table(
        _FINDINGS,
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("code", _enum(_FINDING_CODES, "classification_finding_code"), nullable=False),
        sa.Column("severity", _enum(_SEVERITIES, "finding_severity"), nullable=False),
        sa.Column("tender_document_id", sa.Uuid(), nullable=True),
        sa.Column("related_document_id", sa.Uuid(), nullable=True),
        sa.Column("detail", _json(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in_list("code", _FINDING_CODES), name="code_valid"),
        sa.CheckConstraint(_in_list("severity", _SEVERITIES), name="severity_valid"),
        sa.ForeignKeyConstraint(["run_id"], [f"{_RUNS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tender_document_id"], ["tender_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["related_document_id"], ["tender_documents.id"], ondelete="CASCADE"
        ),
    )
    op.create_index("ix_global_document_classification_findings_run", _FINDINGS, ["run_id"])

    op.create_table(
        _REVIEWS,
        sa.Column("tender_document_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("role", _enum(_ROLES, "document_role"), nullable=False),
        sa.Column("authority", _enum(_AUTHORITIES, "role_review_authority"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("based_on_run_id", sa.Uuid(), nullable=True),
        sa.Column("based_on_suggestion_id", sa.Uuid(), nullable=True),
        sa.Column("source_ref_id", sa.Uuid(), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        _created_at(),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(_in_list("role", _ROLES), name="role_valid"),
        sa.CheckConstraint(_in_list("authority", _AUTHORITIES), name="authority_valid"),
        sa.CheckConstraint("length(trim(reason)) > 0", name="reason_not_empty"),
        sa.ForeignKeyConstraint(
            ["tender_document_id"], ["tender_documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["based_on_run_id"], [f"{_RUNS}.id"], ondelete="NO ACTION"),
        sa.ForeignKeyConstraint(
            ["based_on_suggestion_id"], [f"{_SUGGESTIONS}.id"], ondelete="NO ACTION"
        ),
        sa.ForeignKeyConstraint(["source_ref_id"], ["tender_source_refs.id"], ondelete="NO ACTION"),
        sa.ForeignKeyConstraint(["supersedes_id"], [f"{_REVIEWS}.id"], ondelete="NO ACTION"),
    )
    op.create_index(
        "ix_document_role_reviews_document", _REVIEWS, ["tender_document_id", "created_at"]
    )

    op.add_column(_DECISIONS, sa.Column("content_sha256", sa.String(length=64), nullable=True))
    if _is_postgresql():
        op.execute(_APPEND_ONLY_FUNCTION)
        op.execute(_APPEND_ONLY_TRIGGER)
        op.execute(sa.text(_BACKFILL_SHA))
        op.execute(_DROP_UNANCHORED)
        op.alter_column(_DECISIONS, "content_sha256", nullable=False)
    # 3B-2 §4.4: sense valor per defecte; les runs existents queden a nul.
    op.add_column(
        _ANALYSIS_RUNS, sa.Column("accepted_incomplete_package", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    postgresql_ = _is_postgresql()
    if postgresql_:
        # Primer el refus: amb revisions de l'operador no es toca res.
        op.execute(_REFUSE_DOWNGRADE)
        # El trigger abans de la taula (i la funcio despres del trigger).
        op.execute(_DROP_TRIGGER)
        op.execute(_DROP_FUNCTION)
    op.drop_column(_ANALYSIS_RUNS, "accepted_incomplete_package")
    op.drop_column(_DECISIONS, "content_sha256")
    op.drop_index("ix_document_role_reviews_document", table_name=_REVIEWS)
    op.drop_table(_REVIEWS)
    op.drop_index("ix_global_document_classification_findings_run", table_name=_FINDINGS)
    op.drop_table(_FINDINGS)
    op.drop_table(_SUGGESTIONS)
    op.drop_index("uq_global_document_classification_runs_current", table_name=_RUNS)
    op.drop_table(_RUNS)
