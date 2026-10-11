"""Contracte extern de la classificacio documental (capa 3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import (
    ClassificationFindingCode,
    ClassificationTrigger,
    DocumentRole,
    FindingSeverity,
    GlobalClassificationTrigger,
    PackageGapCode,
    PackageGapProducer,
    RoleConfidence,
    RoleReviewAuthority,
    RoleSource,
    TenderDocumentType,
)
from app.schemas.common import APIModel, ORMModel


class ClassificationRunRead(ORMModel):
    id: uuid.UUID
    classifier_version: str
    trigger: ClassificationTrigger
    trigger_reception_id: uuid.UUID | None
    document_count: int
    created_at: datetime


class ClassifiedDocumentRead(BaseModel):
    tender_document_id: uuid.UUID
    name: str
    #: On era dins de l'entrega, si va arribar per una. Si no, el nom.
    path: str
    mime_type: str
    sha256: str
    #: El que llegeix l'analisi (capa 4).
    document_type: TenderDocumentType
    #: El rol vigent: el que ha fixat una persona o, si no, el proposat.
    role: DocumentRole | None
    role_source: RoleSource | None
    suggested_role: DocumentRole | None
    suggested_confidence: RoleConfidence | None
    decided_at: datetime | None
    #: Cert si ningu no ha fixat el rol i la proposta no es prou segura o hi ha
    #: alguna troballa alta o mitjana sobre el document.
    needs_review: bool
    signals: dict[str, Any] | None


class ClassificationFindingRead(ORMModel):
    id: uuid.UUID
    code: ClassificationFindingCode
    severity: FindingSeverity
    tender_document_id: uuid.UUID | None
    related_document_id: uuid.UUID | None
    detail: dict[str, Any]


class ClassificationRead(BaseModel):
    run: ClassificationRunRead | None
    documents: list[ClassifiedDocumentRead]
    findings: list[ClassificationFindingRead]


class RoleDecisionWrite(APIModel):
    role: DocumentRole


# --------------------------------------------------------------------------- #
# Classificacio global i revisio de l'operador (3B-2 §3.2)                      #
# --------------------------------------------------------------------------- #


class GlobalClassificationRunRead(ORMModel):
    id: uuid.UUID
    classifier_version: str
    input_fingerprint: str
    trigger: GlobalClassificationTrigger
    document_count: int
    created_at: datetime


class GlobalRoleSuggestionRead(ORMModel):
    """El que va proposar el classificador. Immutable."""

    id: uuid.UUID
    tender_document_id: uuid.UUID
    content_sha256: str
    extraction_id: uuid.UUID | None
    role: DocumentRole
    confidence: RoleConfidence
    document_type: TenderDocumentType
    signals: dict[str, Any]


class GlobalClassificationFindingRead(ORMModel):
    id: uuid.UUID
    code: ClassificationFindingCode
    severity: FindingSeverity
    tender_document_id: uuid.UUID | None
    related_document_id: uuid.UUID | None
    detail: dict[str, Any]


class DocumentRoleReviewRead(ORMModel):
    """El que va revisar l'operador, a part del que va proposar el classificador."""

    id: uuid.UUID
    tender_document_id: uuid.UUID
    content_sha256: str
    role: DocumentRole
    authority: RoleReviewAuthority
    actor_id: uuid.UUID
    reason: str
    based_on_run_id: uuid.UUID | None
    based_on_suggestion_id: uuid.UUID | None
    source_ref_id: uuid.UUID | None
    supersedes_id: uuid.UUID | None
    created_at: datetime


class EffectiveRoleRead(BaseModel):
    tender_document_id: uuid.UUID
    role: DocumentRole | None
    document_type: TenderDocumentType
    source: RoleSource
    content_sha256: str | None
    fallback_source: RoleSource | None = None


class PackageGapRead(ORMModel):
    id: uuid.UUID
    code: PackageGapCode
    detail: dict[str, Any] | None
    would_provide: list[Any] | None
    producer: PackageGapProducer
    producer_run_id: uuid.UUID | None
    resolved_by_document_id: uuid.UUID | None
    resolved_at: datetime | None
    created_at: datetime


class GlobalClassificationRead(BaseModel):
    run: GlobalClassificationRunRead | None
    suggestions: list[GlobalRoleSuggestionRead]
    findings: list[GlobalClassificationFindingRead]
    reviews: list[DocumentRoleReviewRead]
    #: La lectura compartida (`company_id=None`): la que llegeix l'extraccio.
    effective_roles: list[EffectiveRoleRead]
    gaps: list[PackageGapRead]


class RoleReviewWrite(APIModel):
    role: DocumentRole
    #: Obligatori: qui revisa respon del que ha vist.
    reason: str = Field(min_length=1, max_length=500)
    #: La versio del fitxer que s'ha mirat; si ja no es la vigent, 409.
    content_sha256: str = Field(min_length=64, max_length=64)
    #: Per defecte, la passada vigent i el seu suggeriment per al document.
    based_on_run_id: uuid.UUID | None = None
    based_on_suggestion_id: uuid.UUID | None = None
    source_ref_id: uuid.UUID | None = None
