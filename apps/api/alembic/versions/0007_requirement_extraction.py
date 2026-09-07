"""Extraccio de requisits: runs, requisits, criteris i evidencia.

Afegeix cinc taules GLOBALS (cap porta `company_id`): els requisits deriven de
documents publics i s'extreuen una sola vegada per licitacio, les miri l'empresa
que les miri.

* `requirement_extraction_runs`: una passada d'analisi. Immutable; repetir-la
  crea un run nou i nomes el nou passa a vigent, i nomes si acaba be.
* `requirements` / `requirement_evidence`: que exigeix el plec i on ho diu.
* `award_criteria` / `award_criterion_evidence`: criteris de puntuacio i la seva
  font.

L'evidencia apunta amb RESTRICT a `tender_document_extractions` i
`tender_document_pages`: una cita no pot quedar orfe ni pot desapareixer el text
que la sosté.

Dos indexs unics parcials sobre els runs: un garanteix un sol run vigent per
licitacio, l'altre fa de lock de concurrencia contra el doble clic.

NO altera cap taula existent: ni licitacions, ni documents, ni extraccions, ni
pre-fit, ni dades d'empresa.

Revision ID: 0007_requirements
Revises: 0006_extraction_rev
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0007_requirements'
down_revision: str | None = '0006_extraction_rev'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('requirement_extraction_runs',
    sa.Column('tender_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', 'partial', 'failed', name='analysis_run_status', native_enum=False), nullable=False),
    sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('extractor_version', sa.String(length=50), nullable=False),
    sa.Column('prompt_version', sa.String(length=50), nullable=False),
    sa.Column('schema_version', sa.String(length=50), nullable=False),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('model', sa.String(length=120), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('documents_analyzed', sa.Integer(), nullable=False),
    sa.Column('pages_analyzed', sa.Integer(), nullable=False),
    sa.Column('chunks_total', sa.Integer(), nullable=False),
    sa.Column('chunks_completed', sa.Integer(), nullable=False),
    sa.Column('chunks_failed', sa.Integer(), nullable=False),
    sa.Column('requirements_extracted', sa.Integer(), nullable=False),
    sa.Column('requirements_rejected', sa.Integer(), nullable=False),
    sa.Column('award_criteria_extracted', sa.Integer(), nullable=False),
    sa.Column('validation_errors', sa.Integer(), nullable=False),
    sa.Column('evidence_rejected', sa.Integer(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('estimated_cost', sa.Float(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('provider_metadata', sa.JSON(), nullable=True),
    sa.Column('skipped_documents', sa.JSON(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], name=op.f('fk_requirement_extraction_runs_tender_id_tenders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requirement_extraction_runs'))
    )
    op.create_index('ix_requirement_extraction_runs_cache', 'requirement_extraction_runs', ['tender_id', 'input_fingerprint', 'extractor_version', 'prompt_version', 'schema_version'], unique=False)
    op.create_index('ix_requirement_extraction_runs_tender_id', 'requirement_extraction_runs', ['tender_id'], unique=False)
    op.create_index('uq_requirement_extraction_runs_current', 'requirement_extraction_runs', ['tender_id'], unique=True, postgresql_where=sa.text('is_current'), sqlite_where=sa.text('is_current = 1'))
    op.create_index('uq_requirement_extraction_runs_in_flight', 'requirement_extraction_runs', ['tender_id'], unique=True, postgresql_where=sa.text("status IN ('pending', 'running')"), sqlite_where=sa.text("status IN ('pending', 'running')"))
    op.create_table('award_criteria',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('tender_id', sa.Uuid(), nullable=False),
    sa.Column('tender_lot_id', sa.Uuid(), nullable=True),
    sa.Column('category', sa.String(length=100), nullable=True),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('points', sa.Float(), nullable=True),
    sa.Column('weight_percent', sa.Float(), nullable=True),
    sa.Column('formula', sa.Text(), nullable=True),
    sa.Column('evaluation_type', sa.Enum('automatic', 'judgment', 'mixed', 'unknown', name='award_evaluation_type', native_enum=False), nullable=False),
    sa.Column('confidence', sa.Enum('high', 'medium', 'low', name='extraction_confidence', native_enum=False), nullable=False),
    sa.Column('review_status', sa.Enum('unreviewed', 'verified', 'needs_review', 'rejected', name='review_status', native_enum=False), nullable=False),
    sa.Column('manual_override_json', sa.JSON(), nullable=True),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.Uuid(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dedup_key', sa.String(length=64), nullable=False),
    sa.Column('source_chunk_index', sa.Integer(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_award_criteria_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['requirement_extraction_runs.id'], name=op.f('fk_award_criteria_run_id_requirement_extraction_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], name=op.f('fk_award_criteria_tender_id_tenders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tender_lot_id'], ['tender_lots.id'], name=op.f('fk_award_criteria_tender_lot_id_tender_lots'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_award_criteria')),
    sa.UniqueConstraint('run_id', 'dedup_key', name='uq_award_criteria_run_dedup')
    )
    op.create_index('ix_award_criteria_run_id', 'award_criteria', ['run_id'], unique=False)
    op.create_index('ix_award_criteria_tender_id', 'award_criteria', ['tender_id'], unique=False)
    op.create_table('requirements',
    sa.Column('run_id', sa.Uuid(), nullable=False),
    sa.Column('tender_id', sa.Uuid(), nullable=False),
    sa.Column('tender_lot_id', sa.Uuid(), nullable=True),
    sa.Column('category', sa.Enum('deadline', 'economic_solvency', 'technical_solvency', 'experience', 'certification', 'registration', 'business_classification', 'insurance', 'personnel', 'professional_qualification', 'equipment_or_resources', 'administrative_documentation', 'technical_requirement', 'contract_condition', 'other', name='requirement_category', native_enum=False), nullable=False),
    sa.Column('obligation', sa.Enum('mandatory', 'scored', 'informative', 'unknown', name='requirement_obligation', native_enum=False), nullable=False),
    sa.Column('operator', sa.Enum('gte', 'gt', 'lte', 'lt', 'eq', 'one_of', 'all_of', 'contains', 'exists', 'date_before', 'date_after', 'unknown', name='requirement_operator', native_enum=False), nullable=True),
    sa.Column('scope', sa.Enum('tender', 'lot', 'multiple_lots', 'unknown', name='requirement_scope', native_enum=False), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('value_json', sa.JSON(), nullable=True),
    sa.Column('raw_lot_labels', sa.JSON(), nullable=True),
    sa.Column('confidence', sa.Enum('high', 'medium', 'low', name='extraction_confidence', native_enum=False), nullable=False),
    sa.Column('review_status', sa.Enum('unreviewed', 'verified', 'needs_review', 'rejected', name='review_status', native_enum=False), nullable=False),
    sa.Column('manual_override_json', sa.JSON(), nullable=True),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.Uuid(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('has_conflict', sa.Boolean(), nullable=False),
    sa.Column('conflict_group', sa.String(length=64), nullable=True),
    sa.Column('dedup_key', sa.String(length=64), nullable=False),
    sa.Column('source_chunk_index', sa.Integer(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_requirements_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['run_id'], ['requirement_extraction_runs.id'], name=op.f('fk_requirements_run_id_requirement_extraction_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], name=op.f('fk_requirements_tender_id_tenders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tender_lot_id'], ['tender_lots.id'], name=op.f('fk_requirements_tender_lot_id_tender_lots'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requirements')),
    sa.UniqueConstraint('run_id', 'dedup_key', name='uq_requirements_run_dedup')
    )
    op.create_index('ix_requirements_category', 'requirements', ['category'], unique=False)
    op.create_index('ix_requirements_run_id', 'requirements', ['run_id'], unique=False)
    op.create_index('ix_requirements_tender_id', 'requirements', ['tender_id'], unique=False)
    op.create_table('award_criterion_evidence',
    sa.Column('award_criterion_id', sa.Uuid(), nullable=False),
    sa.Column('extraction_id', sa.Uuid(), nullable=False),
    sa.Column('page_id', sa.Uuid(), nullable=False),
    sa.Column('tender_document_id', sa.Uuid(), nullable=True),
    sa.Column('page_number', sa.Integer(), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('char_start', sa.Integer(), nullable=True),
    sa.Column('char_end', sa.Integer(), nullable=True),
    sa.Column('section', sa.String(length=200), nullable=True),
    sa.Column('evidence_role', sa.Enum('primary', 'supporting', name='evidence_role', native_enum=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('page_number >= 1', name=op.f('ck_award_criterion_evidence_award_evidence_page_number_is_one_based')),
    sa.ForeignKeyConstraint(['award_criterion_id'], ['award_criteria.id'], name=op.f('fk_award_criterion_evidence_award_criterion_id_award_criteria'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['extraction_id'], ['tender_document_extractions.id'], name=op.f('fk_award_criterion_evidence_extraction_id_tender_document_extractions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['page_id'], ['tender_document_pages.id'], name=op.f('fk_award_criterion_evidence_page_id_tender_document_pages'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['tender_document_id'], ['tender_documents.id'], name=op.f('fk_award_criterion_evidence_tender_document_id_tender_documents'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_award_criterion_evidence'))
    )
    op.create_index('ix_award_criterion_evidence_criterion_id', 'award_criterion_evidence', ['award_criterion_id'], unique=False)
    op.create_table('requirement_evidence',
    sa.Column('requirement_id', sa.Uuid(), nullable=False),
    sa.Column('extraction_id', sa.Uuid(), nullable=False),
    sa.Column('page_id', sa.Uuid(), nullable=False),
    sa.Column('tender_document_id', sa.Uuid(), nullable=True),
    sa.Column('page_number', sa.Integer(), nullable=False),
    sa.Column('quote', sa.Text(), nullable=False),
    sa.Column('char_start', sa.Integer(), nullable=True),
    sa.Column('char_end', sa.Integer(), nullable=True),
    sa.Column('section', sa.String(length=200), nullable=True),
    sa.Column('evidence_role', sa.Enum('primary', 'supporting', name='evidence_role', native_enum=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.CheckConstraint('page_number >= 1', name=op.f('ck_requirement_evidence_evidence_page_number_is_one_based')),
    sa.ForeignKeyConstraint(['extraction_id'], ['tender_document_extractions.id'], name=op.f('fk_requirement_evidence_extraction_id_tender_document_extractions'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['page_id'], ['tender_document_pages.id'], name=op.f('fk_requirement_evidence_page_id_tender_document_pages'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], name=op.f('fk_requirement_evidence_requirement_id_requirements'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tender_document_id'], ['tender_documents.id'], name=op.f('fk_requirement_evidence_tender_document_id_tender_documents'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_requirement_evidence'))
    )
    op.create_index('ix_requirement_evidence_page_id', 'requirement_evidence', ['page_id'], unique=False)
    op.create_index('ix_requirement_evidence_requirement_id', 'requirement_evidence', ['requirement_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_requirement_evidence_requirement_id', table_name='requirement_evidence')
    op.drop_index('ix_requirement_evidence_page_id', table_name='requirement_evidence')
    op.drop_table('requirement_evidence')
    op.drop_index('ix_award_criterion_evidence_criterion_id', table_name='award_criterion_evidence')
    op.drop_table('award_criterion_evidence')
    op.drop_index('ix_requirements_tender_id', table_name='requirements')
    op.drop_index('ix_requirements_run_id', table_name='requirements')
    op.drop_index('ix_requirements_category', table_name='requirements')
    op.drop_table('requirements')
    op.drop_index('ix_award_criteria_tender_id', table_name='award_criteria')
    op.drop_index('ix_award_criteria_run_id', table_name='award_criteria')
    op.drop_table('award_criteria')
    op.drop_index('uq_requirement_extraction_runs_in_flight', table_name='requirement_extraction_runs', postgresql_where=sa.text("status IN ('pending', 'running')"), sqlite_where=sa.text("status IN ('pending', 'running')"))
    op.drop_index('uq_requirement_extraction_runs_current', table_name='requirement_extraction_runs', postgresql_where=sa.text('is_current'), sqlite_where=sa.text('is_current = 1'))
    op.drop_index('ix_requirement_extraction_runs_tender_id', table_name='requirement_extraction_runs')
    op.drop_index('ix_requirement_extraction_runs_cache', table_name='requirement_extraction_runs')
    op.drop_table('requirement_extraction_runs')
