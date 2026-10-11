"""Documents publics: origen, source ownership, verificacio, alies i forats del paquet.

Fase 3B-2 (spec §2.1 i §2.5). Additiva: cap fila existent canvia de significat.

`tender_documents` guanya dotze columnes:

* `origin` (NOT NULL, default `feed`): qui l'ha fet arribar. Backfill: `owner_upload`
  si la URL comenca per `upload://` (pujada/ZIP propia d'una licitacio manual),
  `feed` la resta.
* `governing_source_id` (FK `sources`, RESTRICT): el connector que governa el
  document. Backfill: `tenders.source_id` per als `feed`; NULL per a la resta.
* `contribution_status` (NOT NULL, default `not_applicable`) i la provenance de
  l'aportacio de l'operador (`contributed_by/at`, `contribution_source_url`),
  la verificacio humana (`verified_by/at`, `verification_note`), l'alies per
  contingut (`duplicate_of_document_id`) i la retirada per l'operador
  (`retired_by`, `retired_reason`).

Tres CHECK (PostgreSQL) en imposen la coherencia, creats DESPRES del backfill:
`origin = 'feed'` <=> `governing_source_id IS NOT NULL`;
`contribution_status = 'not_applicable'` <=> `origin IN ('feed', 'owner_upload')`;
`verified_at IS NOT NULL` <=> `contribution_status = 'verified'`. Mes els dos
CHECK de valors de cada enum (cap enum es un tipus natiu, com a la resta del repo).

Taula nova global `tender_package_gaps`: que falta al paquet documental. Un forat
obert es unic per `(tender, code, detail.key)`.

Index unic parcial nou `uq_tender_document_files_current`: un sol fitxer
`is_current` per document (§2.4). Abans de crear-lo es deixa vigent nomes la
versio mes alta de les vigents de cada document (la que ja llegia
`current_file_for`); el downgrade treu l'index i no restaura els duplicats.

SQLite: nomes s'afegeixen les columnes sense claus ni CHECK (ALTER de constraints
no hi es possible sense recrear la taula, i SQLite nomes serveix per als tests,
que creen l'esquema amb els models); la taula nova si que es crea sencera.

Downgrade: refusa de manera explicita (RAISE abans de cap DROP) si hi ha algun
document `verified` o algun alies: tornar enrere perdria la decisio humana de la
verificacio o l'enllac a l'original, i un alies sense l'enllac tornaria a entrar a
l'analisi com a duplicat. Sense aquests casos, esborra la taula i les columnes
(informacio derivada de l'aportacio, no de la font).

Revision ID: 0058_public_documents
Revises: 0057_lot_title_text
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0058_public_documents"
down_revision: str | None = "0057_lot_title_text"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_ORIGINS = ("feed", "owner_upload", "operator_url", "operator_upload")
_CONTRIBUTION = ("not_applicable", "pending_verification", "verified", "rejected")
_GAP_CODES = (
    "required_role_missing",
    "referenced_document_missing",
    "no_text",
    "format_not_analyzable",
    "annex_missing",
    "declared_by_operator",
)
_GAP_PRODUCERS = ("classification", "operator")

#: CHECK de `tender_documents` (nom sense prefix: la convencio hi afegeix `ck_<taula>_`).
_CHECKS = (
    ("origin_valid", "origin IN ({})".format(", ".join(f"'{v}'" for v in _ORIGINS))),
    (
        "contribution_status_valid",
        "contribution_status IN ({})".format(", ".join(f"'{v}'" for v in _CONTRIBUTION)),
    ),
    (
        "contribution_matches_origin",
        "(contribution_status = 'not_applicable') = (origin IN ('feed', 'owner_upload'))",
    ),
    (
        "verified_at_matches_status",
        "(verified_at IS NOT NULL) = (contribution_status = 'verified')",
    ),
    (
        "feed_has_governing_source",
        "(origin = 'feed') = (governing_source_id IS NOT NULL)",
    ),
)

_GAP_KEY = "coalesce(detail ->> 'key', '')"

# Sense `:`, `%` ni cometes al missatge: `op.execute` el compila com a `text()`.
_REFUSAL = (
    "0058 downgrade refusat - hi ha documents verificats o alies "
    "(tender_documents.contribution_status verified o duplicate_of_document_id) "
    "i tornar enrere en perdria la verificacio o l enllac a l original"
)
_REFUSE_DOWNGRADE = f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM tender_documents
        WHERE contribution_status = 'verified' OR duplicate_of_document_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION '{_REFUSAL}';
    END IF;
END
$$
"""


#: Deixa vigent nomes la versio mes alta de les vigents de cada document.
_DEDUP_CURRENT_FILES = sa.text(
    "UPDATE tender_document_files SET is_current = false "
    "WHERE is_current AND EXISTS ("
    "SELECT 1 FROM tender_document_files AS newer "
    "WHERE newer.tender_document_id = tender_document_files.tender_document_id "
    "AND newer.is_current "
    "AND newer.version_number > tender_document_files.version_number)"
)


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _tz() -> sa.types.TypeEngine:
    return sa.DateTime(timezone=True)


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _fk(column: str, target: str, ondelete: str) -> sa.ForeignKey:
    """FK amb el nom que dona la convencio, nomes a PostgreSQL."""
    if not _is_postgresql():
        return None  # type: ignore[return-value]
    table = target.split(".")[0]
    return sa.ForeignKey(
        target, name=op.f(f"fk_tender_documents_{column}_{table}"), ondelete=ondelete
    )


def _uuid_column(name: str, target: str, ondelete: str = "SET NULL") -> sa.Column:
    constraint = _fk(name, target, ondelete)
    args = (constraint,) if constraint is not None else ()
    return sa.Column(name, sa.Uuid(), *args, nullable=True)


def upgrade() -> None:
    op.add_column(
        "tender_documents",
        sa.Column("origin", sa.String(length=15), nullable=False, server_default="feed"),
    )
    op.add_column("tender_documents", _uuid_column("governing_source_id", "sources.id", "RESTRICT"))
    op.add_column(
        "tender_documents",
        sa.Column(
            "contribution_status",
            sa.String(length=20),
            nullable=False,
            server_default="not_applicable",
        ),
    )
    op.add_column("tender_documents", _uuid_column("contributed_by", "users.id"))
    op.add_column("tender_documents", sa.Column("contributed_at", _tz(), nullable=True))
    op.add_column(
        "tender_documents",
        sa.Column("contribution_source_url", sa.String(length=1000), nullable=True),
    )
    op.add_column("tender_documents", _uuid_column("verified_by", "users.id"))
    op.add_column("tender_documents", sa.Column("verified_at", _tz(), nullable=True))
    op.add_column(
        "tender_documents", sa.Column("verification_note", sa.String(length=500), nullable=True)
    )
    op.add_column(
        "tender_documents", _uuid_column("duplicate_of_document_id", "tender_documents.id")
    )
    op.add_column("tender_documents", _uuid_column("retired_by", "users.id"))
    op.add_column(
        "tender_documents", sa.Column("retired_reason", sa.String(length=200), nullable=True)
    )

    # Backfill ABANS dels CHECK: tot el que ja hi ha es `feed` o pujada propia.
    op.execute(
        sa.text("UPDATE tender_documents SET origin = 'owner_upload' WHERE url LIKE 'upload://%'")
    )
    op.execute(
        sa.text(
            "UPDATE tender_documents SET governing_source_id = "
            "(SELECT tenders.source_id FROM tenders WHERE tenders.id = tender_documents.tender_id) "
            "WHERE origin = 'feed'"
        )
    )
    if _is_postgresql():
        for name, condition in _CHECKS:
            op.create_check_constraint(
                op.f(f"ck_tender_documents_{name}"), "tender_documents", condition
            )

    op.create_table(
        "tender_package_gaps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=27), nullable=False),
        sa.Column("detail", _json(), nullable=True),
        sa.Column("referenced_from_source_ref_id", sa.Uuid(), nullable=True),
        sa.Column("would_provide", _json(), nullable=True),
        sa.Column("producer", sa.String(length=14), nullable=False),
        sa.Column("producer_run_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_by_document_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", _tz(), nullable=True),
        sa.Column("created_at", _tz(), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "code IN ({})".format(", ".join(f"'{v}'" for v in _GAP_CODES)),
            name=op.f("ck_tender_package_gaps_code_valid"),
        ),
        sa.CheckConstraint(
            "producer IN ({})".format(", ".join(f"'{v}'" for v in _GAP_PRODUCERS)),
            name=op.f("ck_tender_package_gaps_producer_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name=op.f("fk_tender_package_gaps_tender_id_tenders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["referenced_from_source_ref_id"],
            ["tender_source_refs.id"],
            name=op.f("fk_tender_package_gaps_referenced_from_source_ref_id_tender_source_refs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by_document_id"],
            ["tender_documents.id"],
            name=op.f("fk_tender_package_gaps_resolved_by_document_id_tender_documents"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tender_package_gaps")),
    )
    op.create_index("ix_tender_package_gaps_tender_id", "tender_package_gaps", ["tender_id"])
    op.create_index(
        "uq_tender_package_gaps_open",
        "tender_package_gaps",
        ["tender_id", "code", sa.text(_GAP_KEY)],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
        sqlite_where=sa.text("resolved_at IS NULL"),
    )

    # Un sol fitxer vigent per document (§2.4). Abans una descarrega bona despres
    # d'una fallada (vigent perque no n'hi havia cap de bona) en deixava dos; es
    # queda la versio mes alta, que es la que ja llegia `current_file_for`.
    op.execute(_DEDUP_CURRENT_FILES)
    op.create_index(
        "uq_tender_document_files_current",
        "tender_document_files",
        ["tender_document_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
        sqlite_where=sa.text("is_current = 1"),
    )


def downgrade() -> None:
    postgresql_ = _is_postgresql()
    if postgresql_:
        # Primer el refus: si hi ha verificats o alies no es toca res.
        op.execute(_REFUSE_DOWNGRADE)
    # Els `is_current` destronats per l'upgrade no es restauren: eren duplicats.
    op.drop_index("uq_tender_document_files_current", table_name="tender_document_files")
    op.drop_index("uq_tender_package_gaps_open", table_name="tender_package_gaps")
    op.drop_index("ix_tender_package_gaps_tender_id", table_name="tender_package_gaps")
    op.drop_table("tender_package_gaps")
    if postgresql_:
        for name, _condition in reversed(_CHECKS):
            op.drop_constraint(
                op.f(f"ck_tender_documents_{name}"), "tender_documents", type_="check"
            )
    for column in (
        "retired_reason",
        "retired_by",
        "duplicate_of_document_id",
        "verification_note",
        "verified_at",
        "verified_by",
        "contribution_source_url",
        "contributed_at",
        "contributed_by",
        "contribution_status",
        "governing_source_id",
        "origin",
    ):
        op.drop_column("tender_documents", column)
