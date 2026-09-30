"""Estat del client a l'Administracio Tuvi (consola de plataforma).

Additiva sobre `companies`:

* `platform_status` (`pilot` | `active` | `paused`), VARCHAR + CHECK. Les
  empreses que ja existeixen queden `active`: no canvia res per a elles.
* `pilot_started_at` / `pilot_ends_at`: el periode de pilot. Un pilot sempre
  te fi (CHECK) i la fi no es anterior a l'inici (CHECK).
* `status_changed_at`: l'ultim canvi d'estat.
* `onboarded_by`: l'operador que va fer l'alta (`SET NULL` si es dona de baixa).

`paused` encara no bloqueja l'acces dels usuaris del client: nomes es registra.

**Downgrade.** Treu les constraints i les columnes. Es perd l'estat del client i
les dates del pilot (que abans d'aquesta revisio no existien).

Revision ID: 0043_company_platform_status
Revises: 0042_user_password_change
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0043_company_platform_status'
down_revision: str | None = '0042_user_password_change'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ('pilot', 'active', 'paused')


def upgrade() -> None:
    op.add_column(
        'companies',
        sa.Column(
            'platform_status',
            sa.Enum(*_STATUSES, name='company_platform_status', native_enum=False),
            nullable=False,
            server_default='active',
        ),
    )
    op.add_column('companies', sa.Column('pilot_started_at', sa.Date(), nullable=True))
    op.add_column('companies', sa.Column('pilot_ends_at', sa.Date(), nullable=True))
    op.add_column(
        'companies', sa.Column('status_changed_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column('companies', sa.Column('onboarded_by', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f('fk_companies_onboarded_by_users'),
        'companies',
        'users',
        ['onboarded_by'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_check_constraint(
        op.f('ck_companies_platform_status_valid'),
        'companies',
        "platform_status IN ('pilot', 'active', 'paused')",
    )
    op.create_check_constraint(
        op.f('ck_companies_pilot_has_end'),
        'companies',
        "platform_status <> 'pilot' OR pilot_ends_at IS NOT NULL",
    )
    op.create_check_constraint(
        op.f('ck_companies_pilot_dates_ordered'),
        'companies',
        'pilot_started_at IS NULL OR pilot_ends_at IS NULL OR pilot_ends_at >= pilot_started_at',
    )


def downgrade() -> None:
    # Primer les constraints, despres les columnes que hi fan referencia.
    op.drop_constraint(op.f('ck_companies_pilot_dates_ordered'), 'companies', type_='check')
    op.drop_constraint(op.f('ck_companies_pilot_has_end'), 'companies', type_='check')
    op.drop_constraint(op.f('ck_companies_platform_status_valid'), 'companies', type_='check')
    op.drop_constraint(op.f('fk_companies_onboarded_by_users'), 'companies', type_='foreignkey')
    op.drop_column('companies', 'onboarded_by')
    op.drop_column('companies', 'status_changed_at')
    op.drop_column('companies', 'pilot_ends_at')
    op.drop_column('companies', 'pilot_started_at')
    op.drop_column('companies', 'platform_status')
