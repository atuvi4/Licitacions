"""Contracte extern de la classificacio documental (capa 3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models.enums import (
    ClassificationFindingCode,
    ClassificationTrigger,
    DocumentRole,
    FindingSeverity,
    RoleConfidence,
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
