"""Contractes de les peticions de feina asincrona.

La vista que surt cap al client es la del tenant i prou: no hi ha l'id de
l'execucio global, ni si la feina l'havia comencat una altra empresa, ni el
progres acumulat abans de demanar-la.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import JobOutcome, WorkJobType


class AnalyzeRequirementsJobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False
    document_ids: list[uuid.UUID] = Field(default_factory=list)


class ProcessDocumentsJobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_ids: list[uuid.UUID] = Field(default_factory=list)
    force_download: bool = False
    force_reextract: bool = False


class JobProgressRead(BaseModel):
    done: int
    total: int


class JobRequestRead(BaseModel):
    id: uuid.UUID
    job_type: WorkJobType
    tender_id: uuid.UUID
    #: `accepted` mentre la feina no ha acabat, sigui a la cua o executant-se.
    status: Literal["accepted", "succeeded", "failed", "cancelled"]
    outcome: JobOutcome | None = None
    reused_existing: bool = False
    result_run_id: uuid.UUID | None = None
    error_code: str | None = None
    requested_at: datetime
    finished_at: datetime | None = None
    progress: JobProgressRead | None = None


class JobSubmissionRead(JobRequestRead):
    #: La mateixa empresa ja tenia aquesta peticio pendent.
    already_requested: bool = False
