"""Manifest del paquet de presentacio: serialitzacio canonica i hash.

La pregunta que ha de poder respondre el sistema es "exactament que estem
aprovant". Un identificador de paquet no ho respon: cal poder demostrar que el
contingut no ha canviat. Aixo es un hash, i un hash nomes val si la
serialitzacio es **canonica**: mateixa entrada, mateixos bytes, sempre.

Regles de la serialitzacio, totes per la mateixa rao:

* claus ordenades i sense espais superflus, aixi el hash no depen de l'ordre en
  que Python hagi construit els diccionaris;
* dates en ISO-8601 amb zona, mai objectes `datetime`;
* UUID i decimals com a cadena;
* documents ordenats per `position`, que es un enter estable guardat a la fila i
  no l'ordre en que la base de dades hagi retornat les files.

El modul es pur: no toca base de dades ni HTTP i es pot provar sol.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

#: Versio del format. Si algun dia canvia la forma del manifest, els hashos
#: antics deixarien de ser reproduibles sense saber amb quina versio es van fer.
MANIFEST_VERSION = "submission-manifest-v1"


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list | tuple):
        return [_scalar(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _scalar(item) for key, item in sorted(value.items())}
    return str(value)


def canonical_json(payload: dict[str, Any]) -> str:
    """Bytes deterministes per a un mateix contingut.

    `sort_keys` i separadors sense espais no son estetica: son el que fa que dos
    processos diferents, o el mateix proces d'aqui a un any, produeixin la
    mateixa cadena i per tant el mateix hash.
    """
    return json.dumps(
        _scalar(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def manifest_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ManifestDocument:
    """Un document tal com entra al manifest."""

    position: int
    document_id: Any
    filename: str
    sha256: str
    file_size: int
    mime_type: str
    category: str
    requirement_id: Any = None
    requirement_title: str | None = None
    expires_at: date | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "position": self.position,
            "document_id": self.document_id,
            "filename": self.filename,
            "sha256": self.sha256,
            "file_size": self.file_size,
            "mime_type": self.mime_type,
            "category": self.category,
            "requirement_id": self.requirement_id,
            "requirement_title": self.requirement_title,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, slots=True)
class ManifestInput:
    """Tot el que identifica el paquet. Res que no sigui del paquet.

    Deliberadament NO hi entra res volatil —qui mira la pantalla, quina hora es,
    quantes vegades s'ha congelat—: el hash ha de canviar quan canvia el paquet i
    no quan canvia el context.
    """

    revision_number: int
    workspace_id: Any
    company_id: Any
    tender_id: Any
    tender_lot_id: Any = None
    expediente_number: str | None = None
    deadline_at: datetime | None = None
    requirement_run_id: Any = None
    compliance_run_id: Any = None
    prefit_score: int | None = None
    mandatory_total: int = 0
    mandatory_covered: int = 0
    documents: tuple[ManifestDocument, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "manifest_version": MANIFEST_VERSION,
            "revision_number": self.revision_number,
            "workspace_id": self.workspace_id,
            "company_id": self.company_id,
            "tender": {
                "tender_id": self.tender_id,
                "tender_lot_id": self.tender_lot_id,
                "expediente_number": self.expediente_number,
                "deadline_at": self.deadline_at,
            },
            "analysis": {
                "requirement_run_id": self.requirement_run_id,
                "compliance_run_id": self.compliance_run_id,
                "prefit_score": self.prefit_score,
            },
            "coverage": {
                "mandatory_total": self.mandatory_total,
                "mandatory_covered": self.mandatory_covered,
                "document_count": len(self.documents),
            },
            "documents": [
                document.as_dict()
                for document in sorted(self.documents, key=lambda d: d.position)
            ],
        }


def build_manifest(data: ManifestInput) -> tuple[dict[str, Any], str]:
    """El manifest i el seu hash. Mateixa entrada, mateixa sortida, sempre."""
    payload = _scalar(data.as_dict())
    return payload, manifest_hash(payload)
