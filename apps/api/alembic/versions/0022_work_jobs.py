"""Feina asincrona persistent: execucions globals i peticions per tenant.

Processar plecs i analitzar-ne els requisits dura minuts, i fins ara es feia
dins de la peticio HTTP. El proxy de Next la talla als 30 s i el backend
continuava treballant, i pagant, sense que ningu ho veies. A partir d'ara la
peticio nomes crea o consulta, i la feina la fa un worker que llegeix la cua
d'aquesta base de dades.

`work_jobs` es l'execucio tecnica, global i sense cap `requested_by`: dues
empreses que demanen la mateixa analisi en comparteixen una. `job_requests` es
la peticio de cada tenant, amb qui i quan, i es on viuran permisos i cost.

`requirement_extraction_runs.work_job_id` es l'enllaç canonic entre una
execucio d'analisi i el run que genera. Nullable perque els runs antics, i els
que es llancen fora de la cua, no en tenen; unic quan hi es, perque un job no
pot generar dos runs.

Els indexs unics parcials son les garanties que no poden dependre del codi: una
execucio activa per clau, un job actiu per licitacio i una peticio pendent per
empresa i execucio.

Revision ID: 0022_work_jobs
Revises: 0021_chunk_coverage
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0022_work_jobs'
down_revision: str | None = '0021_chunk_coverage'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_TYPES = ('process_tender_documents', 'analyze_tender_requirements')
_JOB_STATUSES = ('queued', 'running', 'orphaned', 'succeeded', 'failed', 'cancelled')
_REQUEST_STATUSES = ('pending', 'succeeded', 'failed', 'cancelled')
_OUTCOMES = ('executed', 'reused', 'reused_existing', 'partial')

_ACTIVE = "status IN ('queued', 'running', 'orphaned')"
_BUSY = "status IN ('running', 'orphaned')"
_PENDING = "status = 'pending'"
_HAS_JOB = 'work_job_id IS NOT NULL'


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        'work_jobs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'job_type', sa.Enum(*_JOB_TYPES, name='work_job_type', native_enum=False),
            nullable=False,
        ),
        sa.Column(
            'status', sa.Enum(*_JOB_STATUSES, name='work_job_status', native_enum=False),
            nullable=False,
        ),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('parameters', sa.JSON(), nullable=False),
        sa.Column('dedupe_key', sa.String(length=300), nullable=False),
        sa.Column('input_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=True),
        sa.Column('model', sa.String(length=120), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('run_after', sa.DateTime(timezone=True), nullable=True),
        sa.Column('worker_id', sa.String(length=120), nullable=True),
        sa.Column('lease_token', sa.Uuid(), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('progress_current', sa.Integer(), nullable=True),
        sa.Column('progress_total', sa.Integer(), nullable=True),
        sa.Column(
            'outcome', sa.Enum(*_OUTCOMES, name='job_outcome', native_enum=False), nullable=True
        ),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.String(length=500), nullable=True),
        sa.Column('result', sa.JSON(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint('attempts >= 0', name='ck_work_jobs_work_job_attempts_non_negative'),
        sa.ForeignKeyConstraint(
            ['tender_id'], ['tenders.id'], name='fk_work_jobs_tender_id_tenders',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_work_jobs'),
    )
    op.create_index(
        'uq_work_jobs_active_dedupe', 'work_jobs', ['dedupe_key'], unique=True,
        postgresql_where=sa.text(_ACTIVE), sqlite_where=sa.text(_ACTIVE),
    )
    op.create_index(
        'uq_work_jobs_busy_tender', 'work_jobs', ['tender_id'], unique=True,
        postgresql_where=sa.text(_BUSY), sqlite_where=sa.text(_BUSY),
    )
    op.create_index('ix_work_jobs_status_created', 'work_jobs', ['status', 'created_at'])
    op.create_index('ix_work_jobs_tender_id', 'work_jobs', ['tender_id'])

    op.create_table(
        'job_requests',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=True),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column(
            'job_type', sa.Enum(*_JOB_TYPES, name='work_job_type', native_enum=False),
            nullable=False,
        ),
        sa.Column('parameters', sa.JSON(), nullable=False),
        sa.Column('work_job_id', sa.Uuid(), nullable=True),
        sa.Column(
            'requested_at', sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'status', sa.Enum(*_REQUEST_STATUSES, name='job_request_status', native_enum=False),
            nullable=False,
        ),
        sa.Column(
            'outcome', sa.Enum(*_OUTCOMES, name='job_outcome', native_enum=False), nullable=True
        ),
        sa.Column('reused_existing', sa.Boolean(), nullable=False),
        sa.Column('linked_existing', sa.Boolean(), nullable=False),
        sa.Column('progress_baseline', sa.Integer(), nullable=False),
        sa.Column('result_run_id', sa.Uuid(), nullable=True),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'], name='fk_job_requests_company_id_companies',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'], name='fk_job_requests_user_id_users', ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['tender_id'], ['tenders.id'], name='fk_job_requests_tender_id_tenders',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['work_job_id'], ['work_jobs.id'], name='fk_job_requests_work_job_id_work_jobs',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['result_run_id'], ['requirement_extraction_runs.id'],
            name='fk_job_requests_result_run_id_requirement_extraction_runs',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_job_requests'),
    )
    op.create_index(
        'uq_job_requests_pending_company_job', 'job_requests', ['company_id', 'work_job_id'],
        unique=True, postgresql_where=sa.text(_PENDING), sqlite_where=sa.text(_PENDING),
    )
    op.create_index(
        'ix_job_requests_company_tender', 'job_requests',
        ['company_id', 'tender_id', 'created_at'],
    )
    op.create_index('ix_job_requests_work_job_id', 'job_requests', ['work_job_id'])

    op.add_column(
        'requirement_extraction_runs', sa.Column('work_job_id', sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        'fk_requirement_extraction_runs_work_job_id_work_jobs',
        'requirement_extraction_runs',
        'work_jobs',
        ['work_job_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'uq_requirement_extraction_runs_work_job', 'requirement_extraction_runs',
        ['work_job_id'], unique=True,
        postgresql_where=sa.text(_HAS_JOB), sqlite_where=sa.text(_HAS_JOB),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_requirement_extraction_runs_work_job', table_name='requirement_extraction_runs',
        postgresql_where=sa.text(_HAS_JOB), sqlite_where=sa.text(_HAS_JOB),
    )
    op.drop_constraint(
        'fk_requirement_extraction_runs_work_job_id_work_jobs',
        'requirement_extraction_runs',
        type_='foreignkey',
    )
    op.drop_column('requirement_extraction_runs', 'work_job_id')

    op.drop_index('ix_job_requests_work_job_id', table_name='job_requests')
    op.drop_index('ix_job_requests_company_tender', table_name='job_requests')
    op.drop_index(
        'uq_job_requests_pending_company_job', table_name='job_requests',
        postgresql_where=sa.text(_PENDING), sqlite_where=sa.text(_PENDING),
    )
    op.drop_table('job_requests')

    op.drop_index('ix_work_jobs_tender_id', table_name='work_jobs')
    op.drop_index('ix_work_jobs_status_created', table_name='work_jobs')
    op.drop_index(
        'uq_work_jobs_busy_tender', table_name='work_jobs',
        postgresql_where=sa.text(_BUSY), sqlite_where=sa.text(_BUSY),
    )
    op.drop_index(
        'uq_work_jobs_active_dedupe', table_name='work_jobs',
        postgresql_where=sa.text(_ACTIVE), sqlite_where=sa.text(_ACTIVE),
    )
    op.drop_table('work_jobs')
