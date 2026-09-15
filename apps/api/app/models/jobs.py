"""Feina asincrona persistent: l'execucio global i la peticio de cada tenant.

Existeix perque processar plecs i analitzar-ne els requisits pot durar minuts, i
cap peticio HTTP no pot quedar-se oberta tot aquest temps: el proxy de Next la
talla als 30 s i el backend continua treballant (i pagant) sense que ningu ho
vegi. La peticio HTTP nomes crea o consulta; la feina la fa un worker.

Dues taules, i no una, perque son dues coses diferents:

* `WorkJob` es l'**execucio tecnica**, global. Els plecs i els requisits no son de
  cap tenant, i dues empreses que demanen la mateixa analisi han de compartir-ne
  una sola execucio: pagar-la dues vegades seria absurd. Per aixo no te cap
  `requested_by`.
* `JobRequest` es la **peticio d'un tenant**: qui ho ha demanat, quan, i que n'ha
  obtingut. Aqui hi viuen l'auditoria, els permisos i, mes endavant, el cost.

L'enllaç entre una execucio d'analisi i el run que genera es
`RequirementExtractionRun.work_job_id`: s'escriu a la mateixa transaccio que
crea el run, i per tant no hi ha cap moment en que el run existeixi sense saber
de quin job ve. No hi ha cap `work_jobs.result_run_id` que el dupliqui.

Postgres fa de cua: `FOR UPDATE SKIP LOCKED` per reclamar, un lease amb token per
saber qui es el propietari, i indexs unics parcials per a les garanties que no
poden dependre de cap codi (una execucio activa per clau, un job actiu per
licitacio, una peticio pendent per empresa i execucio).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import JobOutcome, JobRequestStatus, WorkJobStatus, WorkJobType

#: Estats en que una execucio encara pot satisfer peticions noves.
ACTIVE_WORK_JOB_STATUSES = (
    WorkJobStatus.QUEUED,
    WorkJobStatus.RUNNING,
    WorkJobStatus.ORPHANED,
)
#: Estats en que una execucio ocupa la seva licitacio.
BUSY_WORK_JOB_STATUSES = (WorkJobStatus.RUNNING, WorkJobStatus.ORPHANED)
TERMINAL_WORK_JOB_STATUSES = (
    WorkJobStatus.SUCCEEDED,
    WorkJobStatus.FAILED,
    WorkJobStatus.CANCELLED,
)

_ACTIVE_SQL = "status IN ('queued', 'running', 'orphaned')"
_BUSY_SQL = "status IN ('running', 'orphaned')"
_PENDING_SQL = "status = 'pending'"


class WorkJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Una execucio tecnica. Global: no pertany a cap tenant."""

    __tablename__ = "work_jobs"
    __table_args__ = (
        # Dues peticions compatibles no poden crear dues execucions actives.
        sa.Index(
            "uq_work_jobs_active_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=sa.text(_ACTIVE_SQL),
            sqlite_where=sa.text(_ACTIVE_SQL),
        ),
        # Un sol job actiu per licitacio: ni dues analisis alhora, ni analitzar
        # mentre es tornen a baixar els plecs. Un job orfe la manté ocupada fins
        # que es reconcilia.
        sa.Index(
            "uq_work_jobs_busy_tender",
            "tender_id",
            unique=True,
            postgresql_where=sa.text(_BUSY_SQL),
            sqlite_where=sa.text(_BUSY_SQL),
        ),
        sa.Index("ix_work_jobs_status_created", "status", "created_at"),
        sa.Index("ix_work_jobs_tender_id", "tender_id"),
        sa.CheckConstraint("attempts >= 0", name="work_job_attempts_non_negative"),
    )

    job_type: Mapped[WorkJobType] = mapped_column(
        enum_column(WorkJobType, "work_job_type"), nullable=False
    )
    status: Mapped[WorkJobStatus] = mapped_column(
        enum_column(WorkJobStatus, "work_job_status"),
        nullable=False,
        default=WorkJobStatus.QUEUED,
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    #: Parametres validats per tipus (`app/jobs/params.py`). Mai text dels plecs.
    parameters: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    #: Identitat tecnica de la feina. Nomes dues peticions amb la mateixa clau
    #: poden compartir execucio.
    dedupe_key: Mapped[str] = mapped_column(sa.String(300), nullable=False)

    #: Analisi: el fingerprint dels plecs confirmat en crear el job. Si abans de
    #: cridar el proveidor ja no coincideix, el job acaba en `input_changed`.
    input_fingerprint: Mapped[str | None] = mapped_column(sa.String(64))
    #: Analisi: amb quin proveidor i model s'ha d'executar.
    provider: Mapped[str | None] = mapped_column(sa.String(50))
    model: Mapped[str | None] = mapped_column(sa.String(120))

    attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    #: No es pot reclamar abans d'aquest moment (jobs de documents reencuats).
    run_after: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    worker_id: Mapped[str | None] = mapped_column(sa.String(120))
    #: Fencing token. Es regenera a cada reclamacio: tota escriptura del worker
    #: el porta, i un worker que l'ha perdut no pot tocar res.
    lease_token: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    progress_current: Mapped[int | None] = mapped_column(sa.Integer)
    progress_total: Mapped[int | None] = mapped_column(sa.Integer)

    outcome: Mapped[JobOutcome | None] = mapped_column(enum_column(JobOutcome, "job_outcome"))
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    error_message: Mapped[str | None] = mapped_column(sa.String(500))
    #: Documents: que ha passat amb cadascun (ids i codis, cap text).
    result: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)


class JobRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """La peticio d'un tenant. Aqui hi ha qui, quan, i que n'ha obtingut."""

    __tablename__ = "job_requests"
    __table_args__ = (
        # Un doble clic de la mateixa empresa no crea dues peticions.
        sa.Index(
            "uq_job_requests_pending_company_job",
            "company_id",
            "work_job_id",
            unique=True,
            postgresql_where=sa.text(_PENDING_SQL),
            sqlite_where=sa.text(_PENDING_SQL),
        ),
        sa.Index("ix_job_requests_company_tender", "company_id", "tender_id", "created_at"),
        sa.Index("ix_job_requests_work_job_id", "work_job_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False
    )
    job_type: Mapped[WorkJobType] = mapped_column(
        enum_column(WorkJobType, "work_job_type"), nullable=False
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(sa.JSON, nullable=False, default=dict)
    #: NULL si el resultat ja existia i no ha calgut encuar res.
    work_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("work_jobs.id", ondelete="SET NULL")
    )
    requested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    status: Mapped[JobRequestStatus] = mapped_column(
        enum_column(JobRequestStatus, "job_request_status"),
        nullable=False,
        default=JobRequestStatus.PENDING,
    )
    outcome: Mapped[JobOutcome | None] = mapped_column(enum_column(JobOutcome, "job_outcome"))
    #: Cert si en crear la peticio ja hi havia resultat.
    reused_existing: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: INTERN, mai exposat: la peticio s'ha vinculat a una execucio que no ha
    #: creat ella. Servira per repartir el cost; revelar-ho diria a una empresa
    #: que una altra ja ho estava analitzant.
    linked_existing: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    #: Progres de l'execucio en el moment de vincular-s'hi. El tenant veu el
    #: progres a partir d'aqui, com si la feina fos nova.
    progress_baseline: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    result_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirement_extraction_runs.id", ondelete="SET NULL"),
    )
    error_code: Mapped[str | None] = mapped_column(sa.String(50))
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
