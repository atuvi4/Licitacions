"""Capa de revisio canonica per empresa (S-R1).

Quatre taules noves i cap canvi a les existents. L'extraccio (`requirements`,
`requirement_evidence`, `award_criteria`, `award_criterion_evidence`,
`requirement_extraction_runs`) no es toca: la revisio hi APUNTA i guarda a part
el que el revisor accepta i com s'hi ha arribat.

* `review_items`: el requisit o criteri canonic tal com el veu UNA empresa.
* `review_item_sources`: quina fila immutable el sosté (una sola font per fila).
* `review_item_revisions`: l'historial. Nomes s'hi afegeix; a PostgreSQL un
  trigger rebutja UPDATE i DELETE directes (una cascada si que passa).
* `review_item_relations`: duplicat, reiteracio, pare, coincidencia parcial...

**Tenancy a la base.** Les taules filles referencien l'element per
`(item_id, company_id)` —i les relacions per `(item_id, company_id,
tender_id)`—, de manera que res d'una empresa pot penjar d'un element d'una
altra, ni una relacio pot creuar licitacions.

**Esborrat de les fonts: `NO ACTION`.** Una run o un requisit amb historial de
revisio no es pot esborrar sol. Esborrar una licitacio o una empresa continua
funcionant, perque `NO ACTION` es comprova al final de la sentencia i la
cascada ja s'ha emportat la revisio. Avui l'aplicacio no esborra runs ni
requisits enlloc.

Neixen buides: cap backfill. Els camps de revisio LEGACY de `requirements` i
`award_criteria` no s'hi copien. El downgrade esborra nomes aquestes taules.

Revision ID: 0040_review_layer
Revises: 0039_award_adjudication_basis
Create Date: 2026-09-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0040_review_layer'
down_revision: str | None = '0039_award_adjudication_basis'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ITEMS = 'review_items'
_SOURCES = 'review_item_sources'
_REVISIONS = 'review_item_revisions'
_RELATIONS = 'review_item_relations'

_KINDS = ('requirement', 'award_criterion')
_VERIFICATION = ('unreviewed', 'verified', 'partial', 'rejected', 'needs_review')
_AWARD_ROLES = ('scored', 'parent_total', 'offer_form_view', 'not_scored', 'desirable')
_ORIGINS = ('extraction', 'manual_review')
_LINK_ROLES = ('primary', 'absorbed_duplicate', 'extra_evidence')
_ACTIONS = (
    'create', 'confirm', 'correct', 'reject', 'mark_partial', 'note',
    'set_status', 'set_award_role', 'link_source', 'archive',
)
_RELATION_TYPES = (
    'duplicate_of', 'restatement_of', 'parent_of', 'partial_match_of', 'supersedes',
    'related_to',
)

_EXACTLY_ONE_SOURCE = (
    '(CASE WHEN requirement_id IS NOT NULL THEN 1 ELSE 0 END'
    ' + CASE WHEN award_criterion_id IS NOT NULL THEN 1 ELSE 0 END'
    ' + CASE WHEN source_ref_id IS NOT NULL THEN 1 ELSE 0 END) = 1'
)
_ACTIVE_PRIMARY = "link_role = 'primary' AND unlinked_at IS NULL"

# El mateix text que `app.models.review`: la migracio no importa l'aplicacio.
_APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION review_item_revisions_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' OR pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'review_item_revisions es nomes d''afegir (%)', TG_OP
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""
_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER review_item_revisions_append_only
BEFORE UPDATE OR DELETE ON review_item_revisions
FOR EACH ROW EXECUTE FUNCTION review_item_revisions_append_only()
"""


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == 'postgresql'


def upgrade() -> None:
    op.create_table(
        _ITEMS,
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('kind', sa.Enum(*_KINDS, name='review_item_kind', native_enum=False),
                  nullable=False),
        sa.Column(
            'verification_status',
            sa.Enum(*_VERIFICATION, name='review_verification', native_enum=False),
            nullable=False,
        ),
        sa.Column(
            'award_role',
            sa.Enum(*_AWARD_ROLES, name='review_award_role', native_enum=False),
            nullable=True,
        ),
        sa.Column('accepted_json', _json(), nullable=False),
        sa.Column(
            'origin_hint',
            sa.Enum(*_ORIGINS, name='review_origin_hint', native_enum=False),
            nullable=False,
        ),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('kind', _KINDS), name='kind_valid'),
        sa.CheckConstraint(_in_list('verification_status', _VERIFICATION),
                           name='verification_valid'),
        sa.CheckConstraint('award_role IS NULL OR ' + _in_list('award_role', _AWARD_ROLES),
                           name='award_role_valid'),
        sa.CheckConstraint(_in_list('origin_hint', _ORIGINS), name='origin_hint_valid'),
        sa.CheckConstraint('revision >= 1', name='revision_positive'),
        sa.UniqueConstraint('id', 'company_id', name='uq_review_items_id_company'),
        sa.UniqueConstraint('id', 'company_id', 'tender_id',
                            name='uq_review_items_id_company_tender'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_review_items_scope', _ITEMS,
                    ['company_id', 'tender_id', 'kind', 'archived_at'])
    op.create_index('ix_review_items_verification', _ITEMS,
                    ['company_id', 'tender_id', 'verification_status'])

    op.create_table(
        _SOURCES,
        sa.Column('item_id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('requirement_id', sa.Uuid(), nullable=True),
        sa.Column('award_criterion_id', sa.Uuid(), nullable=True),
        sa.Column('source_ref_id', sa.Uuid(), nullable=True),
        sa.Column(
            'link_role',
            sa.Enum(*_LINK_ROLES, name='review_source_role', native_enum=False),
            nullable=False,
        ),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('unlinked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_EXACTLY_ONE_SOURCE, name='exactly_one_source'),
        sa.CheckConstraint(_in_list('link_role', _LINK_ROLES), name='link_role_valid'),
        sa.CheckConstraint("source_ref_id IS NULL OR link_role = 'extra_evidence'",
                           name='source_ref_is_extra_evidence'),
        sa.ForeignKeyConstraint(['item_id', 'company_id'],
                                ['review_items.id', 'review_items.company_id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        # NO ACTION: una font amb historial no desapareix sola (vegeu el docstring).
        sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], ondelete='NO ACTION'),
        sa.ForeignKeyConstraint(['award_criterion_id'], ['award_criteria.id'],
                                ondelete='NO ACTION'),
        sa.ForeignKeyConstraint(['source_ref_id'], ['tender_source_refs.id'],
                                ondelete='NO ACTION'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('uq_review_item_sources_active_primary', _SOURCES, ['item_id'],
                    unique=True, postgresql_where=sa.text(_ACTIVE_PRIMARY),
                    sqlite_where=sa.text(_ACTIVE_PRIMARY))
    requirement_primary = f'requirement_id IS NOT NULL AND {_ACTIVE_PRIMARY}'
    op.create_index('uq_review_item_sources_company_requirement', _SOURCES,
                    ['company_id', 'requirement_id'], unique=True,
                    postgresql_where=sa.text(requirement_primary),
                    sqlite_where=sa.text(requirement_primary))
    criterion_primary = f'award_criterion_id IS NOT NULL AND {_ACTIVE_PRIMARY}'
    op.create_index('uq_review_item_sources_company_criterion', _SOURCES,
                    ['company_id', 'award_criterion_id'], unique=True,
                    postgresql_where=sa.text(criterion_primary),
                    sqlite_where=sa.text(criterion_primary))
    op.create_index('ix_review_item_sources_item', _SOURCES, ['item_id'])
    op.create_index('ix_review_item_sources_requirement', _SOURCES, ['requirement_id'])
    op.create_index('ix_review_item_sources_criterion', _SOURCES, ['award_criterion_id'])

    op.create_table(
        _REVISIONS,
        sa.Column('item_id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('revision_no', sa.Integer(), nullable=False),
        sa.Column('action', sa.Enum(*_ACTIONS, name='review_action', native_enum=False),
                  nullable=False),
        sa.Column(
            'verification_status_after',
            sa.Enum(*_VERIFICATION, name='review_verification_after', native_enum=False),
            nullable=False,
        ),
        sa.Column('accepted_json_after', _json(), nullable=False),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('payload_json', _json(), nullable=True),
        sa.Column('actor_id', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('action', _ACTIONS), name='action_valid'),
        sa.CheckConstraint(_in_list('verification_status_after', _VERIFICATION),
                           name='verification_after_valid'),
        sa.CheckConstraint('revision_no >= 1', name='revision_no_positive'),
        sa.UniqueConstraint('item_id', 'revision_no', name='uq_review_item_revisions_item_no'),
        sa.ForeignKeyConstraint(['item_id', 'company_id'],
                                ['review_items.id', 'review_items.company_id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_review_item_revisions_item', _REVISIONS, ['item_id', 'revision_no'])

    op.create_table(
        _RELATIONS,
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('from_item_id', sa.Uuid(), nullable=False),
        sa.Column('to_item_id', sa.Uuid(), nullable=False),
        sa.Column(
            'relation_type',
            sa.Enum(*_RELATION_TYPES, name='review_relation_type', native_enum=False),
            nullable=False,
        ),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('archived_by', sa.Uuid(), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('relation_type', _RELATION_TYPES),
                           name='relation_type_valid'),
        sa.CheckConstraint('from_item_id <> to_item_id', name='not_self'),
        sa.ForeignKeyConstraint(
            ['from_item_id', 'company_id', 'tender_id'],
            ['review_items.id', 'review_items.company_id', 'review_items.tender_id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['to_item_id', 'company_id', 'tender_id'],
            ['review_items.id', 'review_items.company_id', 'review_items.tender_id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['archived_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('uq_review_item_relations_active', _RELATIONS,
                    ['from_item_id', 'to_item_id', 'relation_type'], unique=True,
                    postgresql_where=sa.text('archived_at IS NULL'),
                    sqlite_where=sa.text('archived_at IS NULL'))
    op.create_index('ix_review_item_relations_from', _RELATIONS, ['from_item_id'])
    op.create_index('ix_review_item_relations_to', _RELATIONS, ['to_item_id'])

    if _is_postgresql():
        op.execute(_APPEND_ONLY_FUNCTION)
        op.execute(_APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    # Les filles primer; l'extraccio no es toca.
    if _is_postgresql():
        op.execute(
            'DROP TRIGGER IF EXISTS review_item_revisions_append_only ON review_item_revisions'
        )
        op.execute('DROP FUNCTION IF EXISTS review_item_revisions_append_only()')
    op.drop_index('ix_review_item_relations_to', table_name=_RELATIONS)
    op.drop_index('ix_review_item_relations_from', table_name=_RELATIONS)
    op.drop_index('uq_review_item_relations_active', table_name=_RELATIONS)
    op.drop_table(_RELATIONS)
    op.drop_index('ix_review_item_revisions_item', table_name=_REVISIONS)
    op.drop_table(_REVISIONS)
    op.drop_index('ix_review_item_sources_criterion', table_name=_SOURCES)
    op.drop_index('ix_review_item_sources_requirement', table_name=_SOURCES)
    op.drop_index('ix_review_item_sources_item', table_name=_SOURCES)
    op.drop_index('uq_review_item_sources_company_criterion', table_name=_SOURCES)
    op.drop_index('uq_review_item_sources_company_requirement', table_name=_SOURCES)
    op.drop_index('uq_review_item_sources_active_primary', table_name=_SOURCES)
    op.drop_table(_SOURCES)
    op.drop_index('ix_review_items_verification', table_name=_ITEMS)
    op.drop_index('ix_review_items_scope', table_name=_ITEMS)
    op.drop_table(_ITEMS)
