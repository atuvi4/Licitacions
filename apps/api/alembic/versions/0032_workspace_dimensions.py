"""Dimensions i les fonts que les afirmen (S3-S6).

Dues taules i una sola idea: una **dimensio** es el concepte i el valor de
treball que ha decidit una persona; una **observacio** es una font que afirma un
valor. Separar-les es tot el sentit de la migracio, perque el sistema ha de
poder dir "el plec diu 151 i el full de calcul diu 181" sense triar-ne cap.

**La provinenca no es duplica.** `source_ref_id` apunta a `tender_source_refs`,
que ja porta la versio, el SHA, la pagina i la cita. El disseny antic amb
`tender_document_id`, `page_number` i `quote` a l'observacio queda superat:
tenir-ne dues copies voldria dir que un dia dirien coses diferents.

**`Numeric(18,4)`, mai `Float`.** Aqui hi acabaran havent quantitats que
multipliquen preus, i un binari que no sap representar 0,1 no te res a fer en
una xifra que anira a una oferta.

**El CHECK obliga a explicar-se.** Una dimensio nomes pot ser `decided` si te
motiu escrit. Sense aixo, "decidit" seria nomes un estat; el que fa util una
decisio es justament el per que.

Les dues taules neixen buides: cap backfill, cap dada existent tocada. El
downgrade les esborra —0031 no coneix cap dimensio i no hi ha res a preservar-hi
que tingui sentit fora d'aquesta feature.

Revision ID: 0032_workspace_dimensions
Revises: 0031_manual_requirements
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0032_workspace_dimensions'
down_revision: str | None = '0031_manual_requirements'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIMENSIONS = 'workspace_dimensions'
_OBSERVATIONS = 'workspace_dimension_observations'

_STATUS = sa.Enum(
    'undecided', 'decided', 'disputed', name='dimension_status', native_enum=False
)
_SOURCE_KIND = sa.Enum(
    'tender_document', 'supplier', 'internal', 'other',
    name='observation_source_kind', native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        _DIMENSIONS,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('label', sa.String(length=300), nullable=False),
        sa.Column('unit', sa.String(length=50), nullable=True),
        sa.Column('scope_label', sa.String(length=300), nullable=True),
        sa.Column('working_value_text', sa.String(length=500), nullable=True),
        sa.Column('working_value_num', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('status', _STATUS, nullable=False),
        sa.Column('decision_reason', sa.Text(), nullable=True),
        sa.Column('decided_by', sa.Uuid(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['bid_workspaces.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['decided_by'], ['users.id'],
            name='fk_workspace_dimensions_decider', ondelete='SET NULL',
        ),
        sa.UniqueConstraint(
            'workspace_id', 'key', 'scope_label', name='uq_workspace_dimension_key_scope'
        ),
        sa.CheckConstraint(
            "status <> 'decided' OR decision_reason IS NOT NULL",
            name='dimension_decided_has_reason',
        ),
    )
    op.create_index(
        'ix_workspace_dimensions_workspace', _DIMENSIONS, ['workspace_id', 'archived_at']
    )

    op.create_table(
        _OBSERVATIONS,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('dimension_id', sa.Uuid(), nullable=False),
        sa.Column('source_kind', _SOURCE_KIND, nullable=False),
        sa.Column('source_ref_id', sa.Uuid(), nullable=True),
        sa.Column('value_text', sa.String(length=500), nullable=False),
        sa.Column('value_num', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['dimension_id'], ['workspace_dimensions.id'],
            name='fk_dimension_observations_dimension', ondelete='CASCADE',
        ),
        # SET NULL i no RESTRICT: aqui la cita ajuda a entendre l'observacio,
        # pero el valor que la font deia segueix sent llegible sense ella.
        sa.ForeignKeyConstraint(
            ['source_ref_id'], ['tender_source_refs.id'],
            name='fk_dimension_observations_source_ref', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'],
            name='fk_dimension_observations_author', ondelete='SET NULL',
        ),
    )
    op.create_index('ix_dimension_observations_dimension', _OBSERVATIONS, ['dimension_id'])


def downgrade() -> None:
    # Perdua de dades propia d'una feature nova: 0031 no coneix cap dimensio i
    # no hi ha res a preservar que tingui sentit fora d'aqui.
    op.drop_index('ix_dimension_observations_dimension', table_name=_OBSERVATIONS)
    op.drop_table(_OBSERVATIONS)
    op.drop_index('ix_workspace_dimensions_workspace', table_name=_DIMENSIONS)
    op.drop_table(_DIMENSIONS)
