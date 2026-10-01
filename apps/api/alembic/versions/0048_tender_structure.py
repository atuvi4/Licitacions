"""Estructura del plec per a l'analisi (fase 3A, pas 2).

* `tenders` guanya `duration_months`, `extension_months`, `regulated_harmonised`
  i `planned_start_date`, tots nul·lables i sense cap valor per defecte: `NULL`
  vol dir que no se sap, i `regulated_harmonised` es tri-estat (cap `false`
  implicit).
* `tender_blocks`: unitats economiques o tecniques del contracte que no son
  necessariament lots. `tender_sites`: seus del plec, amb `role` i `is_critical`
  a part; `municipality_code` + `municipality_code_system` (lliure, nul·lable,
  mai inferit) perque 0049 compari codis per (code, code_system).
* `requirement_blocks` i `award_criterion_blocks`: a quins blocs aplica cada
  requisit o criteri (N:M; sense files = tot el contracte).
* `timing` a `requirements` i `award_criteria`: quan s'ha de complir;
  `unknown` per a tot el que ja existeix.
* `tender_block_source_ref_links` i `tender_site_source_ref_links`: cites,
  amb el mateix patro que `requirement_source_ref_links`.

**Neutralitat.** No crea cap bloc, cap seu ni cap enllac, i no llegeix cap
document: nomes columnes nul·lables o amb `unknown`, i taules buides. Cap fila
existent canvia de significat.

**Downgrade.** Treu les taules i les columnes; es perd el que s'hi hagi escrit.

Revision ID: 0048_tender_structure
Revises: 0047_knowledge_coverage
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0048_tender_structure'
down_revision: str | None = '0047_knowledge_coverage'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_TIMING = ('at_submission', 'at_execution', 'ambiguous', 'unknown')
_SITE_ROLES = ('central', 'remote')
_TIMED_TABLES = ('requirements', 'award_criteria')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
    ]


def upgrade() -> None:
    # 1. Dades estructurals del contracte amb columna propia. Tot NULL.
    op.add_column('tenders', sa.Column('duration_months', sa.Integer(), nullable=True))
    op.add_column('tenders', sa.Column('extension_months', sa.Integer(), nullable=True))
    op.add_column('tenders', sa.Column('regulated_harmonised', sa.Boolean(), nullable=True))
    op.add_column('tenders', sa.Column('planned_start_date', sa.Date(), nullable=True))
    op.create_check_constraint(
        'tender_duration_not_negative', 'tenders',
        'duration_months IS NULL OR duration_months >= 0',
    )
    op.create_check_constraint(
        'tender_extension_not_negative', 'tenders',
        'extension_months IS NULL OR extension_months >= 0',
    )

    # 2. Blocs.
    op.create_table(
        'tender_blocks',
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('tender_lot_id', sa.Uuid(), nullable=True),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('base_price_ex_vat', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('price_points', sa.Float(), nullable=True),
        sa.Column('separately_awardable', sa.Boolean(), nullable=True),
        sa.Column('billing_terms_json', sa.JSON(), nullable=True),
        sa.Column('billing_schema', sa.String(length=40), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint('base_price_ex_vat IS NULL OR base_price_ex_vat >= 0',
                           name='block_base_price_not_negative'),
        sa.CheckConstraint('price_points IS NULL OR price_points >= 0',
                           name='block_price_points_not_negative'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_lot_id'], ['tender_lots.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tender_id', 'code', name='uq_tender_blocks_tender_code'),
    )
    op.create_index('ix_tender_blocks_tender_id', 'tender_blocks', ['tender_id'])

    # 3. Seus.
    op.create_table(
        'tender_sites',
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('role', _enum(_SITE_ROLES, 'tender_site_role'), nullable=False),
        sa.Column('is_critical', sa.Boolean(), nullable=False),
        sa.Column('address', sa.String(length=500), nullable=True),
        sa.Column('municipality_code', sa.String(length=10), nullable=True),
        sa.Column('municipality_code_system', sa.String(length=20), nullable=True),
        sa.Column('latitude', sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column('longitude', sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(_in_list('role', _SITE_ROLES), name='role_valid'),
        sa.CheckConstraint('latitude IS NULL OR (latitude >= -90 AND latitude <= 90)',
                           name='site_latitude_range'),
        sa.CheckConstraint('longitude IS NULL OR (longitude >= -180 AND longitude <= 180)',
                           name='site_longitude_range'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tender_id', 'code', name='uq_tender_sites_tender_code'),
    )
    op.create_index('ix_tender_sites_tender_id', 'tender_sites', ['tender_id'])

    # 4. Temporalitat: `unknown` per a tot el que ja hi ha.
    for table in _TIMED_TABLES:
        op.add_column(table, sa.Column(
            'timing', _enum(_TIMING, 'requirement_timing'),
            server_default='unknown', nullable=False,
        ))
        op.create_check_constraint('timing_valid', table, _in_list('timing', _TIMING))

    # 5. Blocs N:M. Cap fila = tot el contracte.
    op.create_table(
        'requirement_blocks',
        sa.Column('requirement_id', sa.Uuid(), nullable=False),
        sa.Column('block_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['block_id'], ['tender_blocks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('requirement_id', 'block_id'),
    )
    op.create_index('ix_requirement_blocks_block_id', 'requirement_blocks', ['block_id'])
    op.create_table(
        'award_criterion_blocks',
        sa.Column('award_criterion_id', sa.Uuid(), nullable=False),
        sa.Column('block_id', sa.Uuid(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['award_criterion_id'], ['award_criteria.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['block_id'], ['tender_blocks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('award_criterion_id', 'block_id'),
    )
    op.create_index('ix_award_criterion_blocks_block_id', 'award_criterion_blocks', ['block_id'])

    # 6. Cites de blocs i seus: el patro de `requirement_source_ref_links`.
    op.create_table(
        'tender_block_source_ref_links',
        sa.Column('block_id', sa.Uuid(), nullable=False),
        sa.Column('source_ref_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['block_id'], ['tender_blocks.id'], ondelete='CASCADE',
                                name='fk_block_source_ref_block'),
        sa.ForeignKeyConstraint(['source_ref_id'], ['tender_source_refs.id'],
                                ondelete='RESTRICT', name='fk_block_source_ref_source'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL',
                                name='fk_block_source_ref_author'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('block_id', 'source_ref_id', name='uq_tender_block_source_ref'),
    )
    op.create_index('ix_tender_block_source_ref_links_block', 'tender_block_source_ref_links',
                    ['block_id'])
    op.create_index('ix_tender_block_source_ref_links_source_ref',
                    'tender_block_source_ref_links', ['source_ref_id'])
    op.create_table(
        'tender_site_source_ref_links',
        sa.Column('site_id', sa.Uuid(), nullable=False),
        sa.Column('source_ref_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['site_id'], ['tender_sites.id'], ondelete='CASCADE',
                                name='fk_site_source_ref_site'),
        sa.ForeignKeyConstraint(['source_ref_id'], ['tender_source_refs.id'],
                                ondelete='RESTRICT', name='fk_site_source_ref_source'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL',
                                name='fk_site_source_ref_author'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('site_id', 'source_ref_id', name='uq_tender_site_source_ref'),
    )
    op.create_index('ix_tender_site_source_ref_links_site', 'tender_site_source_ref_links',
                    ['site_id'])
    op.create_index('ix_tender_site_source_ref_links_source_ref',
                    'tender_site_source_ref_links', ['source_ref_id'])


def downgrade() -> None:
    op.drop_index('ix_tender_site_source_ref_links_source_ref',
                  table_name='tender_site_source_ref_links')
    op.drop_index('ix_tender_site_source_ref_links_site', table_name='tender_site_source_ref_links')
    op.drop_table('tender_site_source_ref_links')
    op.drop_index('ix_tender_block_source_ref_links_source_ref',
                  table_name='tender_block_source_ref_links')
    op.drop_index('ix_tender_block_source_ref_links_block',
                  table_name='tender_block_source_ref_links')
    op.drop_table('tender_block_source_ref_links')
    op.drop_index('ix_award_criterion_blocks_block_id', table_name='award_criterion_blocks')
    op.drop_table('award_criterion_blocks')
    op.drop_index('ix_requirement_blocks_block_id', table_name='requirement_blocks')
    op.drop_table('requirement_blocks')
    for table in reversed(_TIMED_TABLES):
        op.drop_constraint(op.f(f'ck_{table}_timing_valid'), table, type_='check')
        op.drop_column(table, 'timing')
    op.drop_index('ix_tender_sites_tender_id', table_name='tender_sites')
    op.drop_table('tender_sites')
    op.drop_index('ix_tender_blocks_tender_id', table_name='tender_blocks')
    op.drop_table('tender_blocks')
    op.drop_constraint(op.f('ck_tenders_tender_extension_not_negative'), 'tenders', type_='check')
    op.drop_constraint(op.f('ck_tenders_tender_duration_not_negative'), 'tenders', type_='check')
    op.drop_column('tenders', 'planned_start_date')
    op.drop_column('tenders', 'regulated_harmonised')
    op.drop_column('tenders', 'extension_months')
    op.drop_column('tenders', 'duration_months')
