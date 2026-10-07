"""Radar 3B-1: salut de font, provenance canonica, presencia a la font i cerca.

Migracio ADDITIVA, sense backfill i sense cap taula nova:

* `sources`: salut de l'ultima sincronitzacio (estat, pagines, entrades, tombstones,
  error), marca d'aigua `updated_watermark` i lease per font. `last_sync_status` es un
  text pla (`success|partial|failed`), no un enum.
* `ingestion_runs`: comptadors nous (pagines, superades, conflictes, tombstones,
  restaurades), tots NOT NULL amb default 0.
* `tenders`: `source_updated_at` (el `updated` de la font), `source_removed_at`
  (presencia a la font, mai estat), `source_metadata` (JSON amb claus tancades) i
  `search_text` (cerca normalitzada a l'aplicacio).
* `tender_documents`: `removed_at` (absencia observada; mai delete fisic).
* Indexs: `tender_cpvs(cpv_code)`, `tender_lot_cpvs(cpv_code)`, `tenders(first_seen_at)`
  i `tenders(source_updated_at)`.

Cap canvi a `work_jobs` ni cap taula de descart. Els enums Python nous
(`ProcessingStatus.CONFLICT`, `ChangeCode.SOURCE_ENTRY_*`) no necessiten migracio: cap
enum te CHECK.

Downgrade: elimina indexs i columnes en ordre invers. Es perden les dades d'aquestes
columnes (cap fila anterior a 0055 en tenia).

Revision ID: 0055_radar_ingestion
Revises: 0054_decision_analysis_v3
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0055_radar_ingestion"
down_revision: str | None = "0054_decision_analysis_v3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _tz() -> sa.types.TypeEngine:
    return sa.DateTime(timezone=True)


SOURCE_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("last_feed_updated_at", _tz()),
    ("last_sync_started_at", _tz()),
    ("last_sync_finished_at", _tz()),
    ("last_sync_status", sa.String(20)),
    ("last_sync_error", sa.Text()),
    ("updated_watermark", _tz()),
    ("sync_lease_owner", sa.String(120)),
    ("sync_lease_until", _tz()),
]
SOURCE_COUNTERS = ["last_sync_pages", "last_sync_entries", "last_sync_tombstones"]
RUN_COUNTERS = [
    "pages_processed",
    "records_superseded",
    "records_conflicted",
    "tombstones_applied",
    "entries_restored",
]


def upgrade() -> None:
    for name, type_ in SOURCE_COLUMNS:
        op.add_column("sources", sa.Column(name, type_, nullable=True))
    for name in SOURCE_COUNTERS:
        op.add_column("sources", sa.Column(name, sa.Integer(), nullable=False, server_default="0"))
    for name in RUN_COUNTERS:
        op.add_column(
            "ingestion_runs", sa.Column(name, sa.Integer(), nullable=False, server_default="0")
        )

    op.add_column("tenders", sa.Column("source_updated_at", _tz(), nullable=True))
    op.add_column("tenders", sa.Column("source_removed_at", _tz(), nullable=True))
    op.add_column("tenders", sa.Column("source_metadata", _json(), nullable=True))
    op.add_column("tenders", sa.Column("search_text", sa.Text(), nullable=True))
    op.add_column("tender_documents", sa.Column("removed_at", _tz(), nullable=True))

    op.create_index("ix_tender_cpvs_cpv_code", "tender_cpvs", ["cpv_code"])
    op.create_index("ix_tender_lot_cpvs_cpv_code", "tender_lot_cpvs", ["cpv_code"])
    op.create_index("ix_tenders_first_seen_at", "tenders", ["first_seen_at"])
    op.create_index("ix_tenders_source_updated_at", "tenders", ["source_updated_at"])


def downgrade() -> None:
    op.drop_index("ix_tenders_source_updated_at", table_name="tenders")
    op.drop_index("ix_tenders_first_seen_at", table_name="tenders")
    op.drop_index("ix_tender_lot_cpvs_cpv_code", table_name="tender_lot_cpvs")
    op.drop_index("ix_tender_cpvs_cpv_code", table_name="tender_cpvs")

    op.drop_column("tender_documents", "removed_at")
    op.drop_column("tenders", "search_text")
    op.drop_column("tenders", "source_metadata")
    op.drop_column("tenders", "source_removed_at")
    op.drop_column("tenders", "source_updated_at")

    for name in reversed(RUN_COUNTERS):
        op.drop_column("ingestion_runs", name)
    for name in reversed(SOURCE_COUNTERS):
        op.drop_column("sources", name)
    for name, _type in reversed(SOURCE_COLUMNS):
        op.drop_column("sources", name)
