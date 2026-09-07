"""Compliment: runs, assessments i evidencia corporativa.

Afegeix tres taules DE TENANT (porten `company_id` i queden sotmeses a
l'aillament multi-tenant, a diferencia de les capes 4A i 4B, que son publiques):

* `company_compliance_runs`: un calcul de compliment d'una empresa sobre una
  licitacio. Immutable; repetir-lo crea un run nou.
* `company_requirement_assessments`: el veredicte sobre cada requisit, amb la
  regla aplicada i el motiu. Guarda per separat el resultat automatic i el
  revisat: sense aixo no es podria auditar si el motor encertava.
* `assessment_evidence`: quina dada corporativa sosté cada veredicte.

De passada corregeix un defecte introduit a la 0007: el nom del check
constraint de `award_criterion_evidence` sortia de 67 caracters un cop aplicada
la convencio de noms, i PostgreSQL nomes n'admet 63, de manera que el truncava i
el model i la base de dades no coincidien mai. Es reanomena a una forma que hi
cap.

NO altera cap altra taula: ni licitacions, ni documents, ni pagines, ni
requisits, ni pre-fit, ni dades d'empresa.

Revision ID: 0008_compliance
Revises: 0007_requirements
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0008_compliance'
down_revision: str | None = '0007_requirements'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('company_compliance_runs',
    sa.Column('tender_id', sa.Uuid(), nullable=False),
    sa.Column('requirement_extraction_run_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', 'failed', name='compliance_run_status', native_enum=False), nullable=False),
    sa.Column('rules_version', sa.String(length=50), nullable=False),
    sa.Column('company_profile_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('requirements_fingerprint', sa.String(length=64), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('requirements_total', sa.Integer(), nullable=False),
    sa.Column('mandatory_total', sa.Integer(), nullable=False),
    sa.Column('satisfies_count', sa.Integer(), nullable=False),
    sa.Column('does_not_satisfy_count', sa.Integer(), nullable=False),
    sa.Column('unknown_count', sa.Integer(), nullable=False),
    sa.Column('needs_review_count', sa.Integer(), nullable=False),
    sa.Column('not_applicable_count', sa.Integer(), nullable=False),
    sa.Column('potential_blockers_count', sa.Integer(), nullable=False),
    sa.Column('confirmed_blockers_count', sa.Integer(), nullable=False),
    sa.Column('overall_result', sa.Enum('eligible', 'review_required', 'no_go', name='eligibility_result', native_enum=False), nullable=False),
    sa.Column('recommendation', sa.Enum('go_candidate', 'eligible_low_fit', 'review', 'no_go', name='recommendation_result', native_enum=False), nullable=False),
    sa.Column('prefit_score', sa.Integer(), nullable=True),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('error_code', sa.String(length=50), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_company_compliance_runs_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requirement_extraction_run_id'], ['requirement_extraction_runs.id'], name=op.f('fk_company_compliance_runs_requirement_extraction_run_id_requirement_extraction_runs'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], name=op.f('fk_company_compliance_runs_tender_id_tenders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_company_compliance_runs'))
    )
    op.create_index(op.f('ix_company_compliance_runs_company_id'), 'company_compliance_runs', ['company_id'], unique=False)
    op.create_index('ix_company_compliance_runs_company_tender', 'company_compliance_runs', ['company_id', 'tender_id'], unique=False)
    op.create_index('uq_company_compliance_runs_current', 'company_compliance_runs', ['company_id', 'tender_id'], unique=True, postgresql_where=sa.text('is_current'), sqlite_where=sa.text('is_current = 1'))
    op.create_index('uq_company_compliance_runs_in_flight', 'company_compliance_runs', ['company_id', 'tender_id'], unique=True, postgresql_where=sa.text("status IN ('pending', 'running')"), sqlite_where=sa.text("status IN ('pending', 'running')"))
    op.create_table('company_requirement_assessments',
    sa.Column('assessment_run_id', sa.Uuid(), nullable=False),
    sa.Column('requirement_id', sa.Uuid(), nullable=False),
    sa.Column('tender_id', sa.Uuid(), nullable=False),
    sa.Column('tender_lot_id', sa.Uuid(), nullable=True),
    sa.Column('automatic_result', sa.Enum('satisfies', 'does_not_satisfy', 'unknown', 'needs_review', 'not_applicable', name='assessment_result', native_enum=False), nullable=False),
    sa.Column('reviewed_result', sa.Enum('satisfies', 'does_not_satisfy', 'unknown', 'needs_review', 'not_applicable', name='assessment_result_reviewed', native_enum=False), nullable=True),
    sa.Column('assessment_mode', sa.Enum('automatic', 'review_required', name='assessment_mode', native_enum=False), nullable=False),
    sa.Column('rule_code', sa.String(length=80), nullable=False),
    sa.Column('explanation_code', sa.String(length=80), nullable=False),
    sa.Column('explanation_params', sa.JSON(), nullable=True),
    sa.Column('company_value_json', sa.JSON(), nullable=True),
    sa.Column('requirement_value_json', sa.JSON(), nullable=True),
    sa.Column('obligation', sa.Enum('mandatory', 'scored', 'informative', 'unknown', name='requirement_obligation', native_enum=False), nullable=False),
    sa.Column('blocker_status', sa.Enum('none', 'potential', 'confirmed', name='blocker_status', native_enum=False), nullable=False),
    sa.Column('evidence_strength', sa.Enum('strong', 'supporting', 'missing', name='evidence_strength', native_enum=False), nullable=False),
    sa.Column('review_status', sa.Enum('unreviewed', 'verified', 'needs_review', 'rejected', name='review_status', native_enum=False), nullable=False),
    sa.Column('review_note', sa.Text(), nullable=True),
    sa.Column('reviewed_by', sa.Uuid(), nullable=True),
    sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('calculated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assessment_run_id'], ['company_compliance_runs.id'], name=op.f('fk_company_requirement_assessments_assessment_run_id_company_compliance_runs'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_company_requirement_assessments_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], name=op.f('fk_company_requirement_assessments_requirement_id_requirements'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reviewed_by'], ['users.id'], name=op.f('fk_company_requirement_assessments_reviewed_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], name=op.f('fk_company_requirement_assessments_tender_id_tenders'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tender_lot_id'], ['tender_lots.id'], name=op.f('fk_company_requirement_assessments_tender_lot_id_tender_lots'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_company_requirement_assessments')),
    sa.UniqueConstraint('assessment_run_id', 'requirement_id', name='uq_company_requirement_assessments_run_requirement')
    )
    op.create_index(op.f('ix_company_requirement_assessments_company_id'), 'company_requirement_assessments', ['company_id'], unique=False)
    op.create_index('ix_company_requirement_assessments_company_tender', 'company_requirement_assessments', ['company_id', 'tender_id'], unique=False)
    op.create_index('ix_company_requirement_assessments_requirement', 'company_requirement_assessments', ['requirement_id'], unique=False)
    op.create_index('ix_company_requirement_assessments_run', 'company_requirement_assessments', ['assessment_run_id'], unique=False)
    op.create_table('assessment_evidence',
    sa.Column('assessment_id', sa.Uuid(), nullable=False),
    sa.Column('entity_type', sa.Enum('financial_year', 'certification', 'registration', 'insurance', 'project', 'person', 'qualification', 'document', name='company_evidence_kind', native_enum=False), nullable=False),
    sa.Column('entity_id', sa.Uuid(), nullable=False),
    sa.Column('document_id', sa.Uuid(), nullable=True),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('strength', sa.Enum('strong', 'supporting', 'missing', name='evidence_strength', native_enum=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['assessment_id'], ['company_requirement_assessments.id'], name=op.f('fk_assessment_evidence_assessment_id_company_requirement_assessments'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_assessment_evidence_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_assessment_evidence_document_id_documents'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_assessment_evidence'))
    )
    op.create_index('ix_assessment_evidence_assessment', 'assessment_evidence', ['assessment_id'], unique=False)
    op.create_index(op.f('ix_assessment_evidence_company_id'), 'assessment_evidence', ['company_id'], unique=False)
    op.create_index('ix_assessment_evidence_entity', 'assessment_evidence', ['entity_type', 'entity_id'], unique=False)
    op.drop_constraint(op.f('ck_award_criterion_evidence_award_evidence_page_number__e368'), 'award_criterion_evidence', type_='check')
    op.create_check_constraint(op.f('ck_award_criterion_evidence_award_evidence_page_one_based'), 'award_criterion_evidence', 'page_number >= 1')


def downgrade() -> None:
    op.drop_constraint(op.f('ck_award_criterion_evidence_award_evidence_page_one_based'), 'award_criterion_evidence', type_='check')
    op.create_check_constraint(op.f('ck_award_criterion_evidence_award_evidence_page_number__e368'), 'award_criterion_evidence', 'page_number >= 1')
    op.drop_index('ix_assessment_evidence_entity', table_name='assessment_evidence')
    op.drop_index(op.f('ix_assessment_evidence_company_id'), table_name='assessment_evidence')
    op.drop_index('ix_assessment_evidence_assessment', table_name='assessment_evidence')
    op.drop_table('assessment_evidence')
    op.drop_index('ix_company_requirement_assessments_run', table_name='company_requirement_assessments')
    op.drop_index('ix_company_requirement_assessments_requirement', table_name='company_requirement_assessments')
    op.drop_index('ix_company_requirement_assessments_company_tender', table_name='company_requirement_assessments')
    op.drop_index(op.f('ix_company_requirement_assessments_company_id'), table_name='company_requirement_assessments')
    op.drop_table('company_requirement_assessments')
    op.drop_index('uq_company_compliance_runs_in_flight', table_name='company_compliance_runs', postgresql_where=sa.text("status IN ('pending', 'running')"), sqlite_where=sa.text("status IN ('pending', 'running')"))
    op.drop_index('uq_company_compliance_runs_current', table_name='company_compliance_runs', postgresql_where=sa.text('is_current'), sqlite_where=sa.text('is_current = 1'))
    op.drop_index('ix_company_compliance_runs_company_tender', table_name='company_compliance_runs')
    op.drop_index(op.f('ix_company_compliance_runs_company_id'), table_name='company_compliance_runs')
    op.drop_table('company_compliance_runs')
