"""Paquet de presentacio: la fotografia exacta que una persona aprova.

Afegeix tres taules DE TENANT i un valor nou a `workspace_status`:

* `submission_packages`: una revisio congelada. `manifest_json` es guarda sencer
  i no nomes el seu hash: el hash diu si una cosa ha canviat, pero no QUE hi
  havia, i per respondre "que va aprovar aquesta persona" d'aqui a dos anys cal
  el contingut d'aquell dia.
* `submission_package_items`: cada document amb el SHA-256, la mida i la clau
  d'storage del moment de congelar. La FK cap a `documents` es `RESTRICT`: un
  paquet ha de ser REPRODUIBLE, i un document esborrat deixaria un manifest que
  diu quin hash hi havia sense manera d'arribar als bytes. Son copies deliberades: comparar-les amb el
  document actual es el que detecta que algu l'ha substituit sense canviar
  l'identificador. Els fitxers NO es dupliquen a l'storage.
* `submission_package_approvals`: append-only. Sense `updated_at` a proposit.
  Guarda el `package_manifest_hash` aprovat, no nomes l'identificador del paquet.

`workspace_status` incorpora `approved_for_submission`, entre
`ready_for_final_review` i `submitted`. Els enums d'aquest projecte son VARCHAR
sense CHECK —per poder creixer sense migracions— pero la columna esta
dimensionada al valor mes llarg, i el nou en fa 23 quan la columna en tenia 22.
Per aixo cal eixamplar-la: si no, PostgreSQL rebutjaria el valor. El test de
migracions compara noms de columna i NO ho hauria detectat.

NO altera cap altra taula ni cap dada existent.

Revision ID: 0011_submission
Revises: 0010_dossier
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0011_submission'
down_revision: str | None = '0010_dossier'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: `bid_workspaces.status` es un VARCHAR dimensionat al valor mes llarg de
#: l'enum (els enums d'aquest projecte son VARCHAR sense CHECK, per poder creixer
#: sense migracions). `approved_for_submission` en te 23 i la columna en tenia
#: 22: sense eixamplar-la, PostgreSQL rebutjaria el valor nou. El model ja diu
#: 23, aixi que aixo nomes posa la base de dades al dia.
_STATUS_WIDTH_BEFORE = 22
_STATUS_WIDTH_AFTER = 23


def _resize_workspace_status(length: int) -> None:
    op.alter_column(
        'bid_workspaces',
        'status',
        existing_type=sa.String(length=_STATUS_WIDTH_BEFORE),
        type_=sa.String(length=length),
        existing_nullable=False,
    )


def upgrade() -> None:
    _resize_workspace_status(_STATUS_WIDTH_AFTER)

    op.create_table('submission_packages',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('revision_number', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('draft', 'frozen', 'approved', 'superseded', name='submission_package_status', native_enum=False), nullable=False),
    sa.Column('manifest_hash', sa.String(length=64), nullable=True),
    sa.Column('manifest_json', sa.JSON(), nullable=True),
    sa.Column('compliance_run_id', sa.Uuid(), nullable=True),
    sa.Column('requirement_run_id', sa.Uuid(), nullable=True),
    sa.Column('prefit_score', sa.Integer(), nullable=True),
    sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('mandatory_total', sa.Integer(), nullable=False),
    sa.Column('mandatory_covered', sa.Integer(), nullable=False),
    sa.Column('document_count', sa.Integer(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('frozen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('frozen_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('revision_number >= 1', name='ck_submission_packages_package_revision_positive'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_submission_packages_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['compliance_run_id'], ['company_compliance_runs.id'], name='fk_submission_packages_compliance_run', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_submission_packages_created_by_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['frozen_by'], ['users.id'], name='fk_submission_packages_frozen_by_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['requirement_run_id'], ['requirement_extraction_runs.id'], name='fk_submission_packages_requirement_run', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['bid_workspaces.id'], name='fk_submission_packages_workspace_id_bid_workspaces', ondelete='CASCADE'),
    sa.UniqueConstraint('workspace_id', 'revision_number', name='uq_submission_packages_revision'),
    sa.PrimaryKeyConstraint('id', name='pk_submission_packages'),
    )
    op.create_index('ix_submission_packages_company_id', 'submission_packages', ['company_id'], unique=False)
    op.create_index('ix_submission_packages_company_status', 'submission_packages', ['company_id', 'status'], unique=False)
    op.create_index('ix_submission_packages_manifest_hash', 'submission_packages', ['manifest_hash'], unique=False)
    op.create_index('ix_submission_packages_workspace', 'submission_packages', ['workspace_id', 'revision_number'], unique=False)

    op.create_table('submission_package_items',
    sa.Column('package_id', sa.Uuid(), nullable=False),
    sa.Column('workspace_evidence_item_id', sa.Uuid(), nullable=True),
    sa.Column('document_id', sa.Uuid(), nullable=True),
    sa.Column('requirement_id', sa.Uuid(), nullable=True),
    sa.Column('document_sha256', sa.String(length=64), nullable=False),
    sa.Column('storage_key', sa.String(length=512), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('mime_type', sa.String(length=160), nullable=False),
    sa.Column('file_size', sa.BigInteger(), nullable=False),
    sa.Column('requirement_title', sa.String(length=500), nullable=True),
    sa.Column('category', sa.Enum('deadline', 'economic_solvency', 'technical_solvency', 'experience', 'certification', 'registration', 'business_classification', 'insurance', 'personnel', 'professional_qualification', 'equipment_or_resources', 'administrative_documentation', 'technical_requirement', 'contract_condition', 'other', name='requirement_category', native_enum=False), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_submission_package_items_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name='fk_submission_package_items_document_id_documents', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['workspace_evidence_item_id'], ['workspace_evidence_items.id'], name='fk_submission_package_items_evidence', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['package_id'], ['submission_packages.id'], name='fk_submission_package_items_package_id_submission_packages', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], name='fk_submission_package_items_requirement_id_requirements', ondelete='SET NULL'),
    sa.UniqueConstraint('package_id', 'document_id', 'requirement_id', name='uq_submission_package_items_document_requirement'),
    sa.PrimaryKeyConstraint('id', name='pk_submission_package_items'),
    )
    op.create_index('ix_submission_package_items_company_id', 'submission_package_items', ['company_id'], unique=False)
    op.create_index('ix_submission_package_items_document', 'submission_package_items', ['document_id'], unique=False)
    op.create_index('ix_submission_package_items_package', 'submission_package_items', ['package_id', 'position'], unique=False)

    op.create_table('submission_package_approvals',
    sa.Column('package_id', sa.Uuid(), nullable=False),
    sa.Column('package_manifest_hash', sa.String(length=64), nullable=False),
    sa.Column('decision', sa.Enum('approved_for_submission', 'rejected', name='package_approval_decision', native_enum=False), nullable=False),
    sa.Column('approved_by', sa.Uuid(), nullable=False),
    sa.Column('approved_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('readiness_snapshot', sa.Enum('not_started', 'incomplete', 'needs_review', 'ready_for_final_review', name='workspace_readiness', native_enum=False), nullable=False),
    sa.Column('blockers_snapshot', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], name='fk_submission_package_approvals_approved_by_users', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_submission_package_approvals_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['package_id'], ['submission_packages.id'], name='fk_submission_package_approvals_package_id_submission_packages', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_submission_package_approvals'),
    )
    op.create_index('ix_submission_package_approvals_company_id', 'submission_package_approvals', ['company_id'], unique=False)
    op.create_index('ix_submission_package_approvals_package', 'submission_package_approvals', ['package_id', 'approved_at'], unique=False)


def downgrade() -> None:
    op.drop_table('submission_package_approvals')
    op.drop_table('submission_package_items')
    op.drop_table('submission_packages')
    _resize_workspace_status(_STATUS_WIDTH_BEFORE)
