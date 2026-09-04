"""Afegeix el flag `active` als CPV de l'empresa.

Un CPV pot estar temporalment desactivat sense ser una exclusio explicita:
`active=false` vol dir "ara no ens interessa", `excluded=true` vol dir "no el
volem mai". Son dimensions diferents i el matching futur les fara servir de
manera diferent.

Revision ID: 0002_cpv_active
Revises: 0001_initial
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_cpv_active"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # server_default per poder afegir la columna NOT NULL sobre files existents;
    # despres es retira perque el valor per defecte el posa l'aplicacio.
    op.add_column(
        "company_cpv_preferences",
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("company_cpv_preferences", "active", server_default=None)


def downgrade() -> None:
    op.drop_column("company_cpv_preferences", "active")
