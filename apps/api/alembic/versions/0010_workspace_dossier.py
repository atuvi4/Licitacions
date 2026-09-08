"""Dossier documental: amb quins documents acreditarem cada requisit.

Afegeix una taula DE TENANT i una columna:

* `workspace_evidence_items`: la baula entre "sabem que complim" i "aixo es el
  que presentarem". Un document pot cobrir N requisits i un requisit pot
  necessitar N documents, per aixo es una taula d'items i no un camp. El fitxer
  NO es copia: `document_id` apunta a la biblioteca corporativa que ja existeix.

  Dos index unics PARCIALS. El primer evita proposar dues vegades el mateix
  document per al mateix requisit —requisits diferents SI que el poden
  compartir—. El segon deixa un sol marcador "encara no tenim document" per
  requisit: sense ell, sincronitzar dues vegades ompliria el dossier de forats
  repetits.

* `workspace_tasks.mandatory`: una tasca obligatoria pendent impedeix que el
  dossier arribi a la revisio final. Les que neixen d'un forat sobre un requisit
  obligatori ho son soles.

NO altera cap altra taula, i cap dada existent no canvia de significat.

Revision ID: 0010_dossier
Revises: 0009_workspace
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0010_dossier'
down_revision: str | None = '0009_workspace'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('workspace_evidence_items',
    sa.Column('workspace_id', sa.Uuid(), nullable=False),
    sa.Column('requirement_id', sa.Uuid(), nullable=True),
    sa.Column('checklist_item_id', sa.Uuid(), nullable=True),
    sa.Column('document_id', sa.Uuid(), nullable=True),
    sa.Column('company_evidence_type', sa.Enum('financial_year', 'certification', 'registration', 'insurance', 'project', 'person', 'qualification', 'document', name='company_evidence_kind', native_enum=False), nullable=True),
    sa.Column('company_evidence_id', sa.Uuid(), nullable=True),
    sa.Column('label', sa.String(length=300), nullable=False),
    sa.Column('status', sa.Enum('suggested', 'selected', 'missing', 'needs_review', 'not_applicable', name='evidence_item_status', native_enum=False), nullable=False),
    sa.Column('source', sa.Enum('compliance', 'manual', name='evidence_item_source', native_enum=False), nullable=False),
    sa.Column('selected_by', sa.Uuid(), nullable=True),
    sa.Column('selected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('updated_by', sa.Uuid(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['checklist_item_id'], ['workspace_checklist_items.id'], name='fk_workspace_evidence_items_checklist_item', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name='fk_workspace_evidence_items_company_id_companies', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name='fk_workspace_evidence_items_created_by_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name='fk_workspace_evidence_items_document_id_documents', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['requirement_id'], ['requirements.id'], name='fk_workspace_evidence_items_requirement_id_requirements', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['selected_by'], ['users.id'], name='fk_workspace_evidence_items_selected_by_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['updated_by'], ['users.id'], name='fk_workspace_evidence_items_updated_by_users', ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['workspace_id'], ['bid_workspaces.id'], name='fk_workspace_evidence_items_workspace_id_bid_workspaces', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name='pk_workspace_evidence_items'),
    )
    op.create_index('ix_workspace_evidence_items_company_id', 'workspace_evidence_items', ['company_id'], unique=False)
    op.create_index('ix_workspace_evidence_items_document', 'workspace_evidence_items', ['document_id'], unique=False)
    op.create_index('ix_workspace_evidence_items_requirement', 'workspace_evidence_items', ['workspace_id', 'requirement_id'], unique=False)
    op.create_index('ix_workspace_evidence_items_workspace', 'workspace_evidence_items', ['workspace_id'], unique=False)
    op.create_index('uq_workspace_evidence_items_requirement_document', 'workspace_evidence_items', ['workspace_id', 'requirement_id', 'document_id'], unique=True,
        postgresql_where=sa.text('document_id IS NOT NULL'),
        sqlite_where=sa.text('document_id IS NOT NULL'))
    op.create_index('uq_workspace_evidence_items_requirement_gap', 'workspace_evidence_items', ['workspace_id', 'requirement_id'], unique=True,
        postgresql_where=sa.text('document_id IS NULL AND requirement_id IS NOT NULL'),
        sqlite_where=sa.text('document_id IS NULL AND requirement_id IS NOT NULL'))

    # `server_default` nomes per a les files que ja existeixin: el model no en
    # te, i deixar-lo posat faria divergir model i base de dades.
    op.add_column(
        'workspace_tasks',
        sa.Column('mandatory', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('workspace_tasks', 'mandatory', server_default=None)


def downgrade() -> None:
    op.drop_column('workspace_tasks', 'mandatory')
    op.drop_table('workspace_evidence_items')
