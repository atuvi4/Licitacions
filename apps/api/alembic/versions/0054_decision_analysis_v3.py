"""Decisions v3: analisi i fotografia de la decisio (decisions v3, tasca 2).

Dues columnes NUL·LABLES a `workspace_decisions`, sense backfill:

* `analysis_run_id`: el run d'analisi v3 (`tender_analysis_runs`) contra el qual es va
  decidir. FK amb `ON DELETE SET NULL`, com la resta de referencies de la decisio, i
  un index propi.
* `analysis_snapshot_json`: la fotografia versionada (schema tancat) del que hi havia a
  la pantalla en decidir.

`analysis_snapshot_json` NULL vol dir decisio LEGACY (anterior a v3); NO NULL, decisio
v3. Les files existents no es toquen: queden amb les dues columnes a NULL.

Downgrade: elimina index, FK i columnes (en ordre invers). Les dades de les dues
columnes es perden, pero cap fila anterior a 0054 en tenia.

ATENCIO: un downgrade despres que existeixin decisions v3 perd de forma irreversible
els seus snapshots; aquestes decisions passen a llegir-se com a legacy, amb
comptadors 0 i "avisos no registrats".

Revision ID: 0054_decision_analysis_v3
Revises: 0053_project_service_lines
Create Date: 2026-10-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0054_decision_analysis_v3"
down_revision: str | None = "0053_project_service_lines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("workspace_decisions", sa.Column("analysis_run_id", sa.Uuid(), nullable=True))
    op.add_column(
        "workspace_decisions", sa.Column("analysis_snapshot_json", _json(), nullable=True)
    )
    op.create_foreign_key(
        "fk_workspace_decisions_analysis_run",
        "workspace_decisions",
        "tender_analysis_runs",
        ["analysis_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_workspace_decisions_analysis_run", "workspace_decisions", ["analysis_run_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_workspace_decisions_analysis_run", table_name="workspace_decisions")
    op.drop_constraint(
        "fk_workspace_decisions_analysis_run", "workspace_decisions", type_="foreignkey"
    )
    op.drop_column("workspace_decisions", "analysis_snapshot_json")
    op.drop_column("workspace_decisions", "analysis_run_id")
