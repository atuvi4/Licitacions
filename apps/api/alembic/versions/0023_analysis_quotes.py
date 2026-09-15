"""Previews d'analisi: el volum que l'usuari ha vist abans de confirmar-la.

Fins ara qualsevol membre d'una empresa podia demanar una analisi de 80 chunks
sense saber-ho. `analysis_quotes` desa el que se li va ensenyar (documents,
pagines, chunks, perfil, proveidor, model, estimacio de tokens i versio del
calcul) i si ho va confirmar. La confirmacio el compara amb el que hi ha ara.

`job_requests.analysis_quote_id` enllaça la peticio amb el preview confirmat.
Nullable perque el processat de plecs no en te, ni les analisis demanades abans
d'aquesta revisio; unic quan hi es, perque un preview dona com a molt una peticio.

Revision ID: 0023_analysis_quotes
Revises: 0022_work_jobs
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0023_analysis_quotes'
down_revision: str | None = '0022_work_jobs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = ('open', 'confirmed', 'superseded', 'expired')
_HAS_QUOTE = 'analysis_quote_id IS NOT NULL'


def upgrade() -> None:
    op.create_table(
        'analysis_quotes',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('planning_version', sa.String(length=50), nullable=False),
        sa.Column('profile', sa.String(length=20), nullable=False),
        sa.Column('provider', sa.String(length=50), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column('force', sa.Boolean(), nullable=False),
        sa.Column('document_ids', sa.JSON(), nullable=False),
        sa.Column('input_fingerprint', sa.String(length=64), nullable=False),
        sa.Column('pages_per_chunk', sa.Integer(), nullable=False),
        sa.Column('overlap_pages', sa.Integer(), nullable=False),
        sa.Column('documents', sa.JSON(), nullable=False),
        sa.Column('skipped_documents', sa.JSON(), nullable=False),
        sa.Column('documents_count', sa.Integer(), nullable=False),
        sa.Column('pages_total', sa.Integer(), nullable=False),
        sa.Column('pages_in_plan', sa.Integer(), nullable=False),
        sa.Column('chunks', sa.Integer(), nullable=False),
        sa.Column('chunks_to_send', sa.Integer(), nullable=False),
        sa.Column('reusable_run_id', sa.Uuid(), nullable=True),
        sa.Column('estimated_input_tokens', sa.Integer(), nullable=True),
        sa.Column('estimated_output_tokens', sa.Integer(), nullable=True),
        sa.Column('estimate_basis', sa.JSON(), nullable=True),
        sa.Column('max_chunks', sa.Integer(), nullable=False),
        sa.Column('allowed', sa.Boolean(), nullable=False),
        sa.Column('blocking_reason', sa.String(length=50), nullable=True),
        sa.Column(
            'status',
            sa.Enum(*_STATUSES, name='analysis_quote_status', native_enum=False),
            nullable=False,
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('superseded_by_id', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'], name='fk_analysis_quotes_company_id_companies',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name='fk_analysis_quotes_user_id_users',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['tender_id'], ['tenders.id'], name='fk_analysis_quotes_tender_id_tenders',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['reusable_run_id'], ['requirement_extraction_runs.id'],
            name='fk_analysis_quotes_reusable_run_id_requirement_extraction_runs',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['superseded_by_id'], ['analysis_quotes.id'],
            name='fk_analysis_quotes_superseded_by_id_analysis_quotes',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_analysis_quotes'),
    )
    op.create_index(
        'ix_analysis_quotes_company_tender', 'analysis_quotes',
        ['company_id', 'tender_id', 'created_at'],
    )
    op.create_index(
        'ix_analysis_quotes_status_expires', 'analysis_quotes', ['status', 'expires_at']
    )

    op.add_column('job_requests', sa.Column('analysis_quote_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_job_requests_analysis_quote_id_analysis_quotes',
        'job_requests',
        'analysis_quotes',
        ['analysis_quote_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'uq_job_requests_analysis_quote', 'job_requests', ['analysis_quote_id'], unique=True,
        postgresql_where=sa.text(_HAS_QUOTE), sqlite_where=sa.text(_HAS_QUOTE),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_job_requests_analysis_quote', table_name='job_requests',
        postgresql_where=sa.text(_HAS_QUOTE), sqlite_where=sa.text(_HAS_QUOTE),
    )
    op.drop_constraint(
        'fk_job_requests_analysis_quote_id_analysis_quotes', 'job_requests', type_='foreignkey'
    )
    op.drop_column('job_requests', 'analysis_quote_id')
    op.drop_index('ix_analysis_quotes_status_expires', table_name='analysis_quotes')
    op.drop_index('ix_analysis_quotes_company_tender', table_name='analysis_quotes')
    op.drop_table('analysis_quotes')
