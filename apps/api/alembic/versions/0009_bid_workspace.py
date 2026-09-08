"""Workspace de candidatura: decisio humana, checklist, tasques i notes.

Afegeix cinc taules DE TENANT (totes porten `company_id`):

* `bid_workspaces`: la candidatura d'una empresa a una licitacio o a un lot.
  Dos index unics PARCIALS en comptes d'un de sol perque `tender_lot_id` pot ser
  NULL i a SQL NULL != NULL: sense el segon index es podrien obrir dos
  workspaces "de tot l'expedient" per a la mateixa empresa.
* `workspace_decisions`: el GO / NO-GO / defer humà. Append-only; no te
  `updated_at` perque aquestes files no s'actualitzen mai. `decided_by` es NOT
  NULL amb FK RESTRICT: una decisio ha de quedar sempre atribuida.
* `workspace_checklist_items`: que cal aportar, derivat dels requisits. L'index
  unic parcial per `(workspace_id, requirement_id)` es el que fa que regenerar
  la checklist no dupliqui res.
* `workspace_tasks` i `workspace_notes`: feina manual i text de treball.

NO altera cap taula existent: ni licitacions, ni requisits, ni compliment, ni
pre-fit, ni dades d'empresa.

Revision ID: 0009_workspace
Revises: 0008_compliance
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0009_workspace'
down_revision: str | None = '0008_compliance'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('bid_workspaces',
    sa.Column('tender_id', sa.Uuid(), nullable=False),
    sa.Column('tender_lot_id', sa.Uuid(), nullable=True),
    sa.Column('status', sa.Enum('analysis', 'needs_review', 'awaiting_decision', 'go_approved', 'no_go_approved', 'preparing_bid', 'ready_for_final_review', 'submitted', 'withdrawn', name='workspace_status', native_enum=False), nullable=False),
    sa.Column('current_compliance_run_id', sa.Uuid(), nullable=True),
    sa.Column('current_requirement_run_id', sa.Uuid(), nullable=True),
    sa.Column('prefit_score', sa.Integer(), nullable=True),
    sa.Column('prefit_captured_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_bid_workspaces_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_bid_workspaces_created_by_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['current_compliance_run_id'], ['company_compliance_runs.id'], name='fk_bid_workspaces_compliance_run', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['current_requirement_run_id'], ['requirement_extraction_runs.id'], name='fk_bid_workspaces_requirement_run', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], name='fk_bid_workspaces_tender_id_tenders', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tender_lot_id'], ['tender_lots.id'], name='fk_bid_workspaces_tender_lot_id_tender_lots', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_bid_workspaces'),
    )
    op.create_index('ix_bid_workspaces_company_id', 'bid_workspaces', ['company_id'], unique=False)
    op.create_index('ix_bid_workspaces_company_status', 'bid_workspaces', ['company_id', 'status'], unique=False)
    op.create_index('ix_bid_workspaces_company_tender', 'bid_workspaces', ['company_id', 'tender_id'], unique=False)
    op.create_index('uq_bid_workspaces_company_tender_lot', 'bid_workspaces', ['company_id', 'tender_id', 'tender_lot_id'], unique=True,
        postgresql_where=sa.text('tender_lot_id IS NOT NULL'),
        sqlite_where=sa.text('tender_lot_id IS NOT NULL'))
    op.create_index('uq_bid_workspaces_company_tender_nolot', 'bid_workspaces', ['company_id', 'tender_id'], unique=True,
        postgresql_where=sa.text('tender_lot_id IS NULL'),
        sqlite_where=sa.text('tender_lot_id IS NULL'))

    op.create_table('workspace_decisions',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('decision', sa.Enum('go', 'no_go', 'defer', name='workspace_decision_type', native_enum=False), nullable=False),
    sa.Column('decided_by', sa.Uuid(), nullable=False),
    sa.Column('decided_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('compliance_run_id', sa.Uuid(), nullable=True),
    sa.Column('requirement_run_id', sa.Uuid(), nullable=True),
    sa.Column('prefit_score', sa.Integer(), nullable=True),
    sa.Column('confirmed_blockers', sa.Integer(), nullable=False),
    sa.Column('potential_blockers', sa.Integer(), nullable=False),
    sa.Column('mandatory_unknown', sa.Integer(), nullable=False),
    sa.Column('mandatory_needs_review', sa.Integer(), nullable=False),
    sa.Column('automated_recommendation', sa.String(length=40), nullable=True),
    sa.Column('deadline_passed', sa.Boolean(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_workspace_decisions_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['compliance_run_id'], ['company_compliance_runs.id'], name='fk_workspace_decisions_compliance_run', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['decided_by'], ['users.id'], name='fk_workspace_decisions_decided_by_users', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['requirement_run_id'], ['requirement_extraction_runs.id'], name='fk_workspace_decisions_requirement_run', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['bid_workspaces.id'], name='fk_workspace_decisions_workspace_id_bid_workspaces', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_workspace_decisions'),
    )
    op.create_index('ix_workspace_decisions_company_id', 'workspace_decisions', ['company_id'], unique=False)
    op.create_index('ix_workspace_decisions_workspace_decided', 'workspace_decisions', ['workspace_id', 'decided_at'], unique=False)

    op.create_table('workspace_checklist_items',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('requirement_id', sa.Uuid(), nullable=True),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('category', sa.Enum('deadline', 'economic_solvency', 'technical_solvency', 'experience', 'certification', 'registration', 'business_classification', 'insurance', 'personnel', 'professional_qualification', 'equipment_or_resources', 'administrative_documentation', 'technical_requirement', 'contract_condition', 'other', name='requirement_category', native_enum=False), nullable=False),
    sa.Column('mandatory', sa.Boolean(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'in_progress', 'completed', 'blocked', 'not_applicable', name='checklist_item_status', native_enum=False), nullable=False),
    sa.Column('source', sa.Enum('requirement', 'manual', name='checklist_item_source', native_enum=False), nullable=False),
    sa.Column('assigned_to', sa.Uuid(), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], name='fk_workspace_checklist_items_assigned_to_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_workspace_checklist_items_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_workspace_checklist_items_created_by_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], name='fk_workspace_checklist_items_requirement_id_requirements', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['bid_workspaces.id'], name='fk_workspace_checklist_items_workspace_id_bid_workspaces', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_workspace_checklist_items'),
    )
    op.create_index('ix_workspace_checklist_items_company_id', 'workspace_checklist_items', ['company_id'], unique=False)
    op.create_index('ix_workspace_checklist_items_workspace', 'workspace_checklist_items', ['workspace_id'], unique=False)
    op.create_index('uq_workspace_checklist_items_requirement', 'workspace_checklist_items', ['workspace_id', 'requirement_id'], unique=True,
        postgresql_where=sa.text('requirement_id IS NOT NULL'),
        sqlite_where=sa.text('requirement_id IS NOT NULL'))

    op.create_table('workspace_tasks',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('assigned_to', sa.Uuid(), nullable=True),
    sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('status', sa.Enum('pending', 'in_progress', 'completed', 'cancelled', name='workspace_task_status', native_enum=False), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['assigned_to'], ['users.id'], name='fk_workspace_tasks_assigned_to_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_workspace_tasks_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_workspace_tasks_created_by_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['bid_workspaces.id'], name='fk_workspace_tasks_workspace_id_bid_workspaces', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_workspace_tasks'),
    )
    op.create_index('ix_workspace_tasks_assigned', 'workspace_tasks', ['assigned_to'], unique=False)
    op.create_index('ix_workspace_tasks_company_id', 'workspace_tasks', ['company_id'], unique=False)
    op.create_index('ix_workspace_tasks_workspace', 'workspace_tasks', ['workspace_id'], unique=False)

    op.create_table('workspace_notes',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('author_id', sa.Uuid(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], name='fk_workspace_notes_author_id_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_workspace_notes_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['bid_workspaces.id'], name='fk_workspace_notes_workspace_id_bid_workspaces', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_workspace_notes'),
    )
    op.create_index('ix_workspace_notes_company_id', 'workspace_notes', ['company_id'], unique=False)
    op.create_index('ix_workspace_notes_workspace', 'workspace_notes', ['workspace_id', 'created_at'], unique=False)


def downgrade() -> None:
    # Ordre invers al de creacio: primer el que depen del workspace.
    op.drop_table('workspace_notes')
    op.drop_table('workspace_tasks')
    op.drop_table('workspace_checklist_items')
    op.drop_table('workspace_decisions')
    op.drop_table('bid_workspaces')
