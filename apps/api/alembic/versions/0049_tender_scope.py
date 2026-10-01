"""Abast i semantica del plec (fase 3A, pas 3).

* `tender_scope_items`: que es compra (linia del vocabulari, quantitat, unitat,
  especificacions versionades, preu unitari), de tot el contracte, d'un bloc o
  d'un lot (mai de tots dos), i opcionalment d'una seu.
* `requirement_territories`: on aplica un requisit, amb el vocabulari d'area de
  0047; `requirements.territory_match_mode` (`any` per defecte).
* `requirements.proof_json` + `proof_schema`: proves exigides pel plec
  (NULL = no estructurat; `[]` = cap prova identificada).
* `requirement_capability_needs`: necessitats tecniques (capacitat del
  vocabulari O nom sense mapar), amb comparador tancat i punts sense sumar.
* `requirement_award_links`: el requisit es la cara exigible d'un criteri.
* `tender_contract_facts` + `contract_fact_blocks`: fets heterogenis sense
  columna propia i a quins blocs apliquen.
* Tres taules d'enllac amb `tender_source_refs` (elements d'abast, necessitats,
  fets), amb el patro de `requirement_source_ref_links`.

**Neutralitat.** Cap INSERT, UPDATE ni DELETE: els requisits existents queden
amb `territory_match_mode = 'any'` i `proof_json` NULL, i totes les taules
noves neixen buides. Cap gold es carrega.

**Downgrade.** Treu les taules i les columnes; es perd el que s'hi hagi escrit.

Revision ID: 0049_tender_scope
Revises: 0048_tender_structure
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0049_tender_scope'
down_revision: str | None = '0048_tender_structure'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_MATCH_MODES = ('all', 'any')
_AREA_KINDS = ('municipality', 'comarca', 'province', 'region', 'country', 'address', 'custom')
_COMPARATORS = ('present', 'eq', 'gte', 'lte', 'contains')
_NEED_TYPES = ('minimum', 'scored_improvement', 'demo')
_FACT_CODES = (
    'envelopes', 'page_limits', 'definitive_guarantee', 'liability_insurance', 'subcontracting',
    'abnormal_offer_params', 'penalties', 'live_demo', 'site_visit', 'variants_policy',
    'billing_terms', 'max_modification', 'reversibility', 'data_protection',
    'essential_obligations',
)


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def _now() -> sa.Column:
    return sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                     nullable=False)


def _timestamps() -> list[sa.Column]:
    return [
        _now(),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
    ]


def _source_ref_links(table: str, entity_table: str, entity_column: str, prefix: str,
                      uq_name: str, ix_prefix: str) -> None:
    op.create_table(
        table,
        sa.Column(entity_column, sa.Uuid(), nullable=False),
        sa.Column('source_ref_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        _now(),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint([entity_column], [f'{entity_table}.id'], ondelete='CASCADE',
                                name=f'fk_{prefix}_entity'),
        sa.ForeignKeyConstraint(['source_ref_id'], ['tender_source_refs.id'],
                                ondelete='RESTRICT', name=f'fk_{prefix}_source'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL',
                                name=f'fk_{prefix}_author'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(entity_column, 'source_ref_id', name=uq_name),
    )
    op.create_index(f'{ix_prefix}_source_ref', table, ['source_ref_id'])


def upgrade() -> None:
    # 1. Que es compra.
    op.create_table(
        'tender_scope_items',
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('block_id', sa.Uuid(), nullable=True),
        sa.Column('tender_lot_id', sa.Uuid(), nullable=True),
        sa.Column('site_id', sa.Uuid(), nullable=True),
        sa.Column('service_line_code', sa.String(length=40), nullable=False),
        sa.Column('quantity', sa.Numeric(precision=14, scale=3), nullable=True),
        sa.Column('unit', sa.String(length=40), nullable=True),
        sa.Column('specs_json', sa.JSON(), nullable=True),
        sa.Column('specs_schema', sa.String(length=50), nullable=True),
        sa.Column('unit_base_price_ex_vat', sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint('block_id IS NULL OR tender_lot_id IS NULL', name='scope_block_xor_lot'),
        sa.CheckConstraint('quantity IS NULL OR quantity >= 0', name='scope_quantity_not_negative'),
        sa.CheckConstraint('unit_base_price_ex_vat IS NULL OR unit_base_price_ex_vat >= 0',
                           name='scope_unit_price_not_negative'),
        sa.CheckConstraint('specs_json IS NULL OR specs_schema IS NOT NULL',
                           name='scope_specs_versioned'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['block_id'], ['tender_blocks.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_lot_id'], ['tender_lots.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['site_id'], ['tender_sites.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['service_line_code'], ['service_lines.code'],
                                ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tender_scope_items_tender_id', 'tender_scope_items', ['tender_id'])
    op.create_index('ix_tender_scope_items_block_id', 'tender_scope_items', ['block_id'])
    _source_ref_links('tender_scope_item_source_ref_links', 'tender_scope_items', 'scope_item_id',
                      'scope_item_source_ref', 'uq_tender_scope_item_source_ref',
                      'ix_tender_scope_item_source_ref_links')
    op.create_index('ix_tender_scope_item_source_ref_links_item',
                    'tender_scope_item_source_ref_links', ['scope_item_id'])

    # 2. Requisits: mode territorial i proves (neutres per a tot el que existeix).
    op.add_column('requirements', sa.Column(
        'territory_match_mode', _enum(_MATCH_MODES, 'territory_match_mode'),
        server_default='any', nullable=False,
    ))
    op.add_column('requirements', sa.Column('proof_json', sa.JSON(), nullable=True))
    op.add_column('requirements', sa.Column('proof_schema', sa.String(length=50), nullable=True))
    op.create_check_constraint('territory_match_mode_valid', 'requirements',
                               _in_list('territory_match_mode', _MATCH_MODES))
    op.create_check_constraint(
        'proof_json_versioned', 'requirements',
        '(proof_json IS NULL AND proof_schema IS NULL)'
        ' OR (proof_json IS NOT NULL AND proof_schema IS NOT NULL)',
    )

    # 3. Territoris d'un requisit.
    op.create_table(
        'requirement_territories',
        sa.Column('requirement_id', sa.Uuid(), nullable=False),
        sa.Column('kind', _enum(_AREA_KINDS, 'area_kind'), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=True),
        sa.Column('code', sa.String(length=40), nullable=True),
        sa.Column('code_system', sa.String(length=20), nullable=True),
        sa.Column('qualifier', sa.String(length=200), nullable=True),
        sa.Column('site_id', sa.Uuid(), nullable=True),
        _now(),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.CheckConstraint(_in_list('kind', _AREA_KINDS), name='kind_valid'),
        sa.CheckConstraint('label IS NOT NULL OR code IS NOT NULL OR site_id IS NOT NULL',
                           name='territory_has_reference'),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['site_id'], ['tender_sites.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_requirement_territories_requirement_id', 'requirement_territories',
                    ['requirement_id'])

    # 4. Necessitats tecniques.
    op.create_table(
        'requirement_capability_needs',
        sa.Column('requirement_id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('award_criterion_id', sa.Uuid(), nullable=True),
        sa.Column('capability_code', sa.String(length=80), nullable=True),
        sa.Column('unmapped_capability', sa.String(length=200), nullable=True),
        sa.Column('comparator', _enum(_COMPARATORS, 'need_comparator'), nullable=False),
        sa.Column('value_json', sa.JSON(), nullable=True),
        sa.Column('value_schema', sa.String(length=50), nullable=True),
        sa.Column('need_type', _enum(_NEED_TYPES, 'need_type'), nullable=False),
        sa.Column('points', sa.Float(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(_in_list('comparator', _COMPARATORS), name='comparator_valid'),
        sa.CheckConstraint(_in_list('need_type', _NEED_TYPES), name='need_type_valid'),
        sa.CheckConstraint('points IS NULL OR points >= 0', name='need_points_not_negative'),
        sa.CheckConstraint('(capability_code IS NULL) <> (unmapped_capability IS NULL)',
                           name='need_mapped_xor_unmapped'),
        sa.CheckConstraint('value_json IS NULL OR value_schema IS NOT NULL',
                           name='need_value_versioned'),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['award_criterion_id'], ['award_criteria.id'],
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['capability_code'], ['capability_definitions.code'],
                                ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_requirement_capability_needs_requirement_id',
                    'requirement_capability_needs', ['requirement_id'])
    op.create_index('ix_requirement_capability_needs_tender_id', 'requirement_capability_needs',
                    ['tender_id'])
    _source_ref_links('requirement_capability_need_source_ref_links',
                      'requirement_capability_needs', 'need_id', 'capability_need_source_ref',
                      'uq_capability_need_source_ref', 'ix_capability_need_source_ref_links')
    op.create_index('ix_capability_need_source_ref_links_need',
                    'requirement_capability_need_source_ref_links', ['need_id'])

    # 5. Requisit ↔ criteri.
    op.create_table(
        'requirement_award_links',
        sa.Column('requirement_id', sa.Uuid(), nullable=False),
        sa.Column('award_criterion_id', sa.Uuid(), nullable=False),
        _now(),
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['award_criterion_id'], ['award_criteria.id'],
                                ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('requirement_id', 'award_criterion_id'),
    )
    op.create_index('ix_requirement_award_links_criterion', 'requirement_award_links',
                    ['award_criterion_id'])

    # 6. Fets contractuals i a quins blocs apliquen.
    op.create_table(
        'tender_contract_facts',
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('fact_code', _enum(_FACT_CODES, 'contract_fact_code'), nullable=False),
        sa.Column('value_json', sa.JSON(), nullable=False),
        sa.Column('value_schema', sa.String(length=50), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(_in_list('fact_code', _FACT_CODES), name='fact_code_valid'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tender_contract_facts_tender_id', 'tender_contract_facts', ['tender_id'])
    op.create_table(
        'contract_fact_blocks',
        sa.Column('fact_id', sa.Uuid(), nullable=False),
        sa.Column('block_id', sa.Uuid(), nullable=False),
        _now(),
        sa.ForeignKeyConstraint(['fact_id'], ['tender_contract_facts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['block_id'], ['tender_blocks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('fact_id', 'block_id'),
    )
    op.create_index('ix_contract_fact_blocks_block_id', 'contract_fact_blocks', ['block_id'])
    _source_ref_links('tender_contract_fact_source_ref_links', 'tender_contract_facts', 'fact_id',
                      'contract_fact_source_ref', 'uq_tender_contract_fact_source_ref',
                      'ix_tender_contract_fact_source_ref_links')
    op.create_index('ix_tender_contract_fact_source_ref_links_fact',
                    'tender_contract_fact_source_ref_links', ['fact_id'])


def downgrade() -> None:
    op.drop_index('ix_tender_contract_fact_source_ref_links_fact',
                  table_name='tender_contract_fact_source_ref_links')
    op.drop_index('ix_tender_contract_fact_source_ref_links_source_ref',
                  table_name='tender_contract_fact_source_ref_links')
    op.drop_table('tender_contract_fact_source_ref_links')
    op.drop_index('ix_contract_fact_blocks_block_id', table_name='contract_fact_blocks')
    op.drop_table('contract_fact_blocks')
    op.drop_index('ix_tender_contract_facts_tender_id', table_name='tender_contract_facts')
    op.drop_table('tender_contract_facts')
    op.drop_index('ix_requirement_award_links_criterion', table_name='requirement_award_links')
    op.drop_table('requirement_award_links')
    op.drop_index('ix_capability_need_source_ref_links_need',
                  table_name='requirement_capability_need_source_ref_links')
    op.drop_index('ix_capability_need_source_ref_links_source_ref',
                  table_name='requirement_capability_need_source_ref_links')
    op.drop_table('requirement_capability_need_source_ref_links')
    op.drop_index('ix_requirement_capability_needs_tender_id',
                  table_name='requirement_capability_needs')
    op.drop_index('ix_requirement_capability_needs_requirement_id',
                  table_name='requirement_capability_needs')
    op.drop_table('requirement_capability_needs')
    op.drop_index('ix_requirement_territories_requirement_id', table_name='requirement_territories')
    op.drop_table('requirement_territories')
    op.drop_constraint(op.f('ck_requirements_proof_json_versioned'), 'requirements', type_='check')
    op.drop_constraint(op.f('ck_requirements_territory_match_mode_valid'), 'requirements',
                       type_='check')
    op.drop_column('requirements', 'proof_schema')
    op.drop_column('requirements', 'proof_json')
    op.drop_column('requirements', 'territory_match_mode')
    op.drop_index('ix_tender_scope_item_source_ref_links_item',
                  table_name='tender_scope_item_source_ref_links')
    op.drop_index('ix_tender_scope_item_source_ref_links_source_ref',
                  table_name='tender_scope_item_source_ref_links')
    op.drop_table('tender_scope_item_source_ref_links')
    op.drop_index('ix_tender_scope_items_block_id', table_name='tender_scope_items')
    op.drop_index('ix_tender_scope_items_tender_id', table_name='tender_scope_items')
    op.drop_table('tender_scope_items')
