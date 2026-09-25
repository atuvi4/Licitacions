"""Contractes de les peticions de feina asincrona.

La vista que surt cap al client es la del tenant i prou: no hi ha l'id de
l'execucio global, ni si la feina l'havia comencat una altra empresa, ni el
progres acumulat abans de demanar-la.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.enums import AnalysisQuoteStatus, JobOutcome, WorkJobType


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


# --------------------------------------------------------------------------- #
# Previews d'analisi (7B)                                                        #
# --------------------------------------------------------------------------- #


class AnalysisQuoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    force: bool = False
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Represa d'un run parcial: nomes s'enviaran els chunks que hi van fallar.
    resume_from_run_id: uuid.UUID | None = None


class QuoteDocumentRead(BaseModel):
    tender_document_id: str
    name: str
    document_type: str
    pages: int
    chars: int


class QuoteSkippedDocumentRead(BaseModel):
    tender_document_id: str
    name: str
    document_type: str
    reason: str


class AnalysisQuoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tender_id: uuid.UUID
    status: AnalysisQuoteStatus
    planning_version: str
    profile: str
    provider: str
    model: str
    force: bool
    input_fingerprint: str
    pages_per_chunk: int
    overlap_pages: int
    documents: list[QuoteDocumentRead]
    skipped_documents: list[QuoteSkippedDocumentRead]
    pages_total: int
    pages_in_plan: int
    chunks: int
    #: Chunks que arribarien al proveidor (0 si es reaprofita un resultat vigent).
    chunks_to_send: int
    reusable_run_id: uuid.UUID | None = None
    #: ESTIMACIO a partir de l'historic; `None` si no n'hi ha.
    estimated_input_tokens: int | None = None
    estimated_output_tokens: int | None = None
    estimate_basis: dict | None = None
    max_chunks: int
    allowed: bool
    blocking_reason: str | None = None
    expires_at: datetime
    confirmed_at: datetime | None = None
    superseded_by_id: uuid.UUID | None = None

    @computed_field
    @property
    def pages_truncated(self) -> bool:
        return self.pages_in_plan < self.pages_total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resume_from_run_id(self) -> uuid.UUID | None:
        """El run parcial que es repren, si ho es (`reusable_run_id` amb chunks a enviar)."""
        return self.reusable_run_id if self.reusable_run_id and self.chunks_to_send > 0 else None


class AnalysisConfirmationRead(JobSubmissionRead):
    quote_id: uuid.UUID


class AnalysisLimitsRead(BaseModel):
    max_chunks_per_run: int
    max_active_per_company: int
    active_now: int
    can_confirm: bool
    can_process_documents: bool
    confirm_roles: list[str]
