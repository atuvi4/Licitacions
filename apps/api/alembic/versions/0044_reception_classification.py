"""Recepcio de documents i classificacio documental (fase 1 del motor d'analisi).

Sis taules noves de tenant, en dues capes que no es coneixen entre elles:

**Recepcio** (per on i com han arribat els documents):

* `tender_document_receptions`: una entrega, amb el canal (avui nomes
  `zip_upload`) i, si n'hi havia, el contenidor original (`RESTRICT`).
* `tender_document_reception_items`: cada fitxer de l'entrega i el document que
  en va sortir.

**Classificacio** (que es cada document, vingui d'on vingui):

* `tender_document_classification_runs`: una passada sobre tots els documents
  de la licitacio; nomes una vigent per (empresa, licitacio).
* `tender_document_role_suggestions`: el rol proposat per a cada document.
* `tender_document_classification_findings`: les incoherencies del conjunt.
* `tender_document_role_decisions`: el rol que ha fixat una persona. Penja del
  document, no de la passada, perque sobrevisqui a les reclassificacions.

L'unic lligam entre capes es `trigger_reception_id` (`SET NULL`, informatiu).
Les taules filles referencien el seu pare per `(id, company_id)`. Tots els enums
porten CHECK.

Neixen buides. El downgrade esborra nomes aquestes taules: els documents que es
van crear son documents normals i s'hi queden.

Revision ID: 0044_reception_classification
Revises: 0043_company_platform_status
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0044_reception_classification'
down_revision: str | None = '0043_company_platform_status'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECEPTIONS = 'tender_document_receptions'
_ITEMS = 'tender_document_reception_items'
_RUNS = 'tender_document_classification_runs'
_SUGGESTIONS = 'tender_document_role_suggestions'
_FINDINGS = 'tender_document_classification_findings'
_DECISIONS = 'tender_document_role_decisions'

# Copiats dels enums de l'aplicacio: la migracio no importa l'aplicacio.
_CHANNELS = ('zip_upload',)
_RECEPTION_STATUS = ('processing', 'completed', 'failed')
_ITEM_STATUS = ('accepted', 'skipped', 'expanded')
_SKIP_REASONS = (
    'system_file', 'empty', 'too_large', 'unsupported_type', 'type_mismatch',
    'nested_too_deep', 'unreadable_archive',
)
_ROLES = (
    'pcap', 'ppt', 'characteristics_table', 'offer_model', 'inventory_annex',
    'justification_report', 'approval_resolution', 'administrative_form', 'notice', 'other',
)
_CONFIDENCE = ('high', 'medium', 'low')
_DOCUMENT_TYPES = (
    'administrative_terms', 'technical_terms', 'notice', 'award', 'additional', 'other',
)
_TRIGGERS = ('reception', 'request')
_FINDING_CODES = (
    'title_content_mismatch', 'duplicate_bytes', 'duplicate_text', 'duplicate_internal_code',
    'referenced_document_missing', 'required_role_missing', 'no_text',
    'format_not_analyzable', 'role_undetermined',
)
_SEVERITIES = ('high', 'medium', 'low', 'info')
_CURRENT = 'is_current'


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def _created_at() -> sa.Column:
    return sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                     nullable=False)


def upgrade() -> None:
    # --- Recepcio -------------------------------------------------------------
    op.create_table(
        _RECEPTIONS,
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('channel', _enum(_CHANNELS, 'reception_channel'), nullable=False),
        sa.Column('label', sa.String(length=255), nullable=False),
        sa.Column('reference', sa.String(length=1000), nullable=True),
        sa.Column('container_content_id', sa.Uuid(), nullable=True),
        sa.Column('status', _enum(_RECEPTION_STATUS, 'reception_status'), nullable=False),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('item_count', sa.Integer(), nullable=False),
        sa.Column('accepted_count', sa.Integer(), nullable=False),
        sa.Column('skipped_count', sa.Integer(), nullable=False),
        sa.Column('received_by', sa.Uuid(), nullable=True),
        _created_at(),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('channel', _CHANNELS), name='channel_valid'),
        sa.CheckConstraint(_in_list('status', _RECEPTION_STATUS), name='status_valid'),
        sa.CheckConstraint('item_count >= 0 AND accepted_count >= 0 AND skipped_count >= 0',
                           name='counts_not_negative'),
        sa.UniqueConstraint('id', 'company_id', name='uq_tender_document_receptions_id_company'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['container_content_id'], ['tender_document_contents.id'],
                                ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['received_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_tender_document_receptions_scope', _RECEPTIONS,
                    ['company_id', 'tender_id', 'created_at'])

    op.create_table(
        _ITEMS,
        sa.Column('reception_id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('path', sa.String(length=1000), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('packed_size', sa.BigInteger(), nullable=True),
        sa.Column('sha256', sa.String(length=64), nullable=True),
        sa.Column('detected_mime_type', sa.String(length=160), nullable=True),
        sa.Column('source_reference', sa.String(length=1000), nullable=True),
        sa.Column('status', _enum(_ITEM_STATUS, 'reception_item_status'), nullable=False),
        sa.Column('skip_reason', _enum(_SKIP_REASONS, 'reception_skip_reason'), nullable=True),
        sa.Column('tender_document_id', sa.Uuid(), nullable=True),
        _created_at(),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('status', _ITEM_STATUS), name='status_valid'),
        sa.CheckConstraint('skip_reason IS NULL OR ' + _in_list('skip_reason', _SKIP_REASONS),
                           name='skip_reason_valid'),
        sa.CheckConstraint("(status = 'skipped') = (skip_reason IS NOT NULL)",
                           name='skip_reason_iff_skipped'),
        sa.CheckConstraint("status = 'accepted' OR tender_document_id IS NULL",
                           name='document_only_if_accepted'),
        sa.CheckConstraint('position >= 0', name='position_not_negative'),
        sa.UniqueConstraint('reception_id', 'position',
                            name='uq_tender_document_reception_items_position'),
        sa.UniqueConstraint('reception_id', 'path',
                            name='uq_tender_document_reception_items_path'),
        sa.ForeignKeyConstraint(
            ['reception_id', 'company_id'],
            ['tender_document_receptions.id', 'tender_document_receptions.company_id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_document_id'], ['tender_documents.id'],
                                ondelete='SET NULL'),
    )
    op.create_index('ix_tender_document_reception_items_document', _ITEMS,
                    ['tender_document_id'])

    # --- Classificacio ----------------------------------------------------------
    op.create_table(
        _RUNS,
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('classifier_version', sa.String(length=40), nullable=False),
        sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('trigger', _enum(_TRIGGERS, 'classification_trigger'), nullable=False),
        sa.Column('trigger_reception_id', sa.Uuid(), nullable=True),
        sa.Column('document_count', sa.Integer(), nullable=False),
        sa.Column('is_current', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        _created_at(),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('trigger', _TRIGGERS), name='trigger_valid'),
        sa.CheckConstraint('document_count >= 0', name='count_not_negative'),
        sa.UniqueConstraint('id', 'company_id',
                            name='uq_tender_document_classification_runs_id_company'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['trigger_reception_id'], ['tender_document_receptions.id'],
                                ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('uq_tender_document_classification_runs_current', _RUNS,
                    ['company_id', 'tender_id'], unique=True,
                    postgresql_where=sa.text(_CURRENT), sqlite_where=sa.text('is_current = 1'))
    op.create_index('ix_tender_document_classification_runs_scope', _RUNS,
                    ['company_id', 'tender_id', 'created_at'])

    run_fk = sa.ForeignKeyConstraint(
        ['run_id', 'company_id'],
        ['tender_document_classification_runs.id',
         'tender_document_classification_runs.company_id'],
        ondelete='CASCADE',
    )
    op.create_table(
        _SUGGESTIONS,
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('tender_document_id', sa.Uuid(), nullable=False),
        sa.Column('role', _enum(_ROLES, 'document_role'), nullable=False),
        sa.Column('confidence', _enum(_CONFIDENCE, 'role_confidence'), nullable=False),
        sa.Column('document_type', _enum(_DOCUMENT_TYPES, 'tender_document_type'),
                  nullable=False),
        sa.Column('signals', _json(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('role', _ROLES), name='role_valid'),
        sa.CheckConstraint(_in_list('confidence', _CONFIDENCE), name='confidence_valid'),
        sa.CheckConstraint(_in_list('document_type', _DOCUMENT_TYPES),
                           name='document_type_valid'),
        sa.UniqueConstraint('run_id', 'tender_document_id',
                            name='uq_tender_document_role_suggestions_document'),
        run_fk,
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_document_id'], ['tender_documents.id'],
                                ondelete='CASCADE'),
    )

    op.create_table(
        _FINDINGS,
        sa.Column('run_id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('code', _enum(_FINDING_CODES, 'classification_finding_code'), nullable=False),
        sa.Column('severity', _enum(_SEVERITIES, 'finding_severity'), nullable=False),
        sa.Column('tender_document_id', sa.Uuid(), nullable=True),
        sa.Column('related_document_id', sa.Uuid(), nullable=True),
        sa.Column('detail', _json(), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('code', _FINDING_CODES), name='code_valid'),
        sa.CheckConstraint(_in_list('severity', _SEVERITIES), name='severity_valid'),
        sa.ForeignKeyConstraint(
            ['run_id', 'company_id'],
            ['tender_document_classification_runs.id',
             'tender_document_classification_runs.company_id'],
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_document_id'], ['tender_documents.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['related_document_id'], ['tender_documents.id'],
                                ondelete='CASCADE'),
    )
    op.create_index('ix_tender_document_classification_findings_run', _FINDINGS, ['run_id'])

    op.create_table(
        _DECISIONS,
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('tender_document_id', sa.Uuid(), nullable=False),
        sa.Column('role', _enum(_ROLES, 'document_role'), nullable=False),
        sa.Column('decided_by', sa.Uuid(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('role', _ROLES), name='role_valid'),
        sa.UniqueConstraint('company_id', 'tender_document_id',
                            name='uq_tender_document_role_decisions_document'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_document_id'], ['tender_documents.id'],
                                ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['decided_by'], ['users.id'], ondelete='SET NULL'),
    )


def downgrade() -> None:
    op.drop_table(_DECISIONS)
    op.drop_index('ix_tender_document_classification_findings_run', table_name=_FINDINGS)
    op.drop_table(_FINDINGS)
    op.drop_table(_SUGGESTIONS)
    op.drop_index('ix_tender_document_classification_runs_scope', table_name=_RUNS)
    op.drop_index('uq_tender_document_classification_runs_current', table_name=_RUNS)
    op.drop_table(_RUNS)
    op.drop_index('ix_tender_document_reception_items_document', table_name=_ITEMS)
    op.drop_table(_ITEMS)
    op.drop_index('ix_tender_document_receptions_scope', table_name=_RECEPTIONS)
    op.drop_table(_RECEPTIONS)
