"""Analisi forcada d'un tender public: nomes l'operador de plataforma (3B-2 §6.4).

En un tender public cap empresa no tria documents, perfil ni `force` (422
`public_tender_canonical_only` a la quota i a la peticio). Aquesta es l'unica via
per fer-ho: `POST /platform/tenders/{id}/analysis/force` encua al worker una analisi
forcada (opcionalment amb una seleccio de documents i un perfil). El run que en surt
es desa pero MAI no es canonic (`forced_not_canonical`), i passa per la mateixa porta
de despesa (§6.7) que qualsevol altre.

`PlatformAdmin` (superusuari sense empresa); una empresa rep 403. Un tender que no
existeix, 404; un de manual, 422 `tender_not_public` (com els routers germans).
Auditoria amb `company_id=None`. La logica viu a `jobs/requests.submit_operator_analysis`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.ai.profiles import PROFILES
from app.ai.provider import get_provider
from app.api.deps import PlatformAdmin, SessionDep
from app.core.errors import ValidationError
from app.jobs import requests as job_requests

router = APIRouter(prefix="/platform/tenders/{tender_id}/analysis", tags=["platform"])


class OperatorForcedAnalysisIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Buida = la seleccio canonica (tots els documents analitzables per defecte).
    document_ids: list[uuid.UUID] = Field(default_factory=list)
    #: Buit = el perfil de producte (`REQUIREMENTS_PRODUCT_PROFILE`).
    profile: str | None = Field(default=None, max_length=20)


class OperatorForcedAnalysisRead(BaseModel):
    work_job_id: uuid.UUID
    status: str


@router.post(
    "/force",
    response_model=OperatorForcedAnalysisRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Encua una analisi forcada (mai canonica) d'un tender public",
)
async def force_analysis(
    tender_id: uuid.UUID,
    admin: PlatformAdmin,
    session: SessionDep,
    payload: OperatorForcedAnalysisIn | None = None,
) -> OperatorForcedAnalysisRead:
    options = payload or OperatorForcedAnalysisIn()
    profile = None
    if options.profile is not None:
        profile = PROFILES.get(options.profile.strip().lower())
        if profile is None:
            raise ValidationError(
                f"Perfil d'extraccio desconegut: {options.profile!r}.",
                details={"profiles": sorted(PROFILES)},
            )
    job = await job_requests.submit_operator_analysis(
        session,
        tender_id=tender_id,
        user_id=admin.id,
        document_ids=options.document_ids,
        profile=profile,
        # Es llegeix en cada crida (i no com a valor per defecte): el proveidor
        # configurat al proces de l'API, el mateix que el worker ha de tenir.
        provider_factory=get_provider,
    )
    return OperatorForcedAnalysisRead(work_job_id=job.id, status=str(job.status))
