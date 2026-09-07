"""Schemas de sortida de la capa documental.

Regla dura: `storage_key` NO apareix enlloc. La clau interna de l'storage es
un detall d'implementacio, i exposar-la convidaria a saltar-se l'endpoint
autoritzat que serveix l'original.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TenderDocumentContentRead(ORMModel):
    id: uuid.UUID
    sha256: str
    file_size: int
    mime_type: str
    detected_extension: str | None = None
    is_text_extractable: bool


class TenderDocumentExtractionRead(ORMModel):
    id: uuid.UUID
    parser_version: str
    #: Revisio 1-based dins del contingut. Identifica l'evidencia de forma
    #: estable: una extraccio, un cop escrita, no canvia mai.
    revision_number: int
    status: ExtractionStatus
    quality: ExtractionQuality
    page_count: int
    extracted_page_count: int
    empty_page_count: int
    text_char_count: int
    #: Indici de possible escaneig, no una prova que calgui OCR.
    ocr_candidate: bool
    is_current: bool
    parse_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    completed_at: datetime | None = None


class TenderDocumentPageRead(ORMModel):
    id: uuid.UUID
    #: 1-based, igual que a la base de dades i a la interficie.
    page_number: int
    text: str
    char_count: int
    text_hash: str
    status: PageExtractionStatus


class TenderDocumentFileRead(ORMModel):
    id: uuid.UUID
    tender_document_id: uuid.UUID
    version_number: int
    is_current: bool
    download_status: DownloadStatus
    http_status: int | None = None
    source_url: str
    final_url: str | None = None
    original_filename: str | None = None
    detected_filename: str | None = None
    declared_mime_type: str | None = None
    redirect_count: int
    download_ms: int | None = None
    downloaded_at: datetime | None = None
    last_checked_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    content: TenderDocumentContentRead | None = None
    #: L'extraccio vigent del contingut, si n'hi ha.
    extraction: TenderDocumentExtractionRead | None = None


class TenderDocumentProcessRequest(BaseModel):
    #: Torna a demanar la URL i aplica els casos A/B/C del versionat.
    force_download: bool = False
    #: Re-parseja els bytes ja guardats, sense tornar a baixar.
    force_reextract: bool = False


class TenderDocumentProcessResult(BaseModel):
    #: Cert si no ha calgut ni baixar ni parsejar res.
    reused: bool
    file: TenderDocumentFileRead
