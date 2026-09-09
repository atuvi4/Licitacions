"""Limits d'assegurança per separat, i assegurat addicional.

`company_insurances.coverage_amount` guardava un sol import i no deia de quin
limit parlava. Amb aixo, una polissa que cobreix 1.000.000 en total pero nomes
300.000 per victima era indistingible d'una que cobreix 1.000.000 sense
sublimits, i el compliment no podia respondre un plec que exigeix les dues
coses.

Es una taula i no unes columnes noves per un motiu concret: **l'absencia ha de
ser llegible**. Amb columnes, un sublimit per victima buit voldria dir alhora
"no en te" i "no ho sabem", i aquesta diferencia es la que decideix si es pot
afirmar que una empresa compleix o nomes que no consta. Sense fila, no consta; i
no consta no es incomplir.

`coverage_amount` es conserva tal com esta: es el que hi ha registrat avui i cap
dada no canvia de significat. Les regles el llegeixen com un limit de tipus
desconegut, que es exactament el que sempre ha estat.

`additional_insured` es nullable a proposit: `NULL` vol dir que no s'ha declarat.
Un `false` per defecte hauria convertit un "no consta" en un "no ho compleix".

Revision ID: 0013_insurance_limits
Revises: 0012_requirements_v2
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0013_insurance_limits'
down_revision: str | None = '0012_requirements_v2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LIMIT_KIND = sa.Enum(
    'total', 'per_claim', 'per_victim', 'annual_aggregate', 'sublimit', 'unknown',
    name='insurance_limit_kind', native_enum=False,
)


def upgrade() -> None:
    op.add_column(
        'company_insurances', sa.Column('additional_insured', sa.Boolean(), nullable=True)
    )

    op.create_table(
        'company_insurance_limits',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('insurance_id', sa.Uuid(), nullable=False),
        sa.Column('kind', _LIMIT_KIND, nullable=False),
        sa.Column('amount', sa.Numeric(18, 2), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.CheckConstraint('amount >= 0', name='insurance_limit_amount_positive'),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'], name='fk_ins_limit_company', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['insurance_id'],
            ['company_insurances.id'],
            name='fk_ins_limit_insurance',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_ins_limit_created_by'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name='fk_ins_limit_updated_by'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'insurance_id', 'kind', name='uq_company_insurance_limits_insurance_kind'
        ),
    )
    op.create_index(
        'ix_company_insurance_limits_company_id',
        'company_insurance_limits',
        ['company_id'],
    )
    op.create_index(
        'ix_company_insurance_limits_insurance_id',
        'company_insurance_limits',
        ['insurance_id'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_company_insurance_limits_insurance_id', table_name='company_insurance_limits'
    )
    op.drop_index(
        'ix_company_insurance_limits_company_id', table_name='company_insurance_limits'
    )
    op.drop_table('company_insurance_limits')
    op.drop_column('company_insurances', 'additional_insured')
