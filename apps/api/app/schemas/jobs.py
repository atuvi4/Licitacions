"""Contractes de les peticions de feina asincrona.

La vista que surt cap al client es la del tenant i prou: no hi ha l'id de
l'execucio global, ni si la feina l'havia comencat una altra empresa, ni el
progres acumulat abans de demanar-la.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

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
    #: Mentre es `accepted`: `false` si encara es a la cua, `true` si ja s'executa
    #: per a aquesta peticio. `null` quan ja ha acabat.
    started: bool | None = None


class JobSubmissionRead(JobRequestRead):
    #: La mateixa empresa ja tenia aquesta peticio pendent.
    already_requested: bool = False


# --------------------------------------------------------------------------- #
# Previews d'analisi (7B)                                                        #
# --------------------------------------------------------------------------- #


class AnalysisQuoteIn(BaseModel):
    """Opcions d'una quota.

    En una licitacio PUBLICA, `force`, `document_ids` i `profile` responen 422
    `public_tender_canonical_only` (3B-2 §6.4): la seleccio i el perfil son els
    canonics del servidor. En una MANUAL propia, com sempre.
    """

    model_config = ConfigDict(extra="forbid")

    force: bool = False
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Perfil d'extraccio (nomes licitacions manuals). Buit = el de producte.
    profile: str | None = Field(default=None, max_length=20)
    #: Represa d'un run parcial: nomes s'enviaran els chunks que hi van fallar.
    resume_from_run_id: uuid.UUID | None = None
    #: Qui confirma accepta un paquet incomplet (§6.1.3). No entra al run.
    accept_incomplete_package: bool = False
    #: Qui confirma accepta un text amb avisos de qualitat (§6.1.4). No entra al run.
    acknowledge_quality: bool = False
    #: Qui confirma accepta que hi ha documents PRESENTS que Tuvi encara no pot
    #: processar completament (no es cap paquet incomplet). No entra al run.
    acknowledge_processing: bool = False


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
    #: L'unic motiu prioritzat pel qual no es pot confirmar (3B-2 §6.1).
    blocking_reason: str | None = None
    #: Prerequisits de §6.1 tal com es van calcular (`failed`, `advisory`, `warnings`,
    #: paquet, qualitat, pressupost). NUL als previews anteriors a 3B-2.
    prerequisites: dict[str, Any] | None = None
    #: Cost estimat en dolars (§6.7). NUL sense preu del model.
    estimated_cost_usd: float | None = None
    accept_incomplete_package: bool = False
    expires_at: datetime
    confirmed_at: datetime | None = None
    superseded_by_id: uuid.UUID | None = None

    @computed_field
    @property
    def pages_truncated(self) -> bool:
        return self.pages_in_plan < self.pages_total

    @computed_field  # type: ignore[prop-decorator]
    @property
    def provider_basis(self) -> str | None:
        """`real` o `simulation` (o `unconfigured`): sobre que es faria l'analisi."""
        return (self.prerequisites or {}).get("provider_basis")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def warnings(self) -> list[str]:
        """Avisos que no bloquegen (p. ex. `previous_run_completed_empty`)."""
        return list((self.prerequisites or {}).get("warnings") or [])

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
