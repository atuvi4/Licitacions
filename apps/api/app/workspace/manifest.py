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
MANIFEST_VERSION = "submission-manifest-v2"

#: La primera versio, que nomes cobria el dossier documental. Els paquets que la
#: porten NO es reescriuen (C-17): son la fotografia real del que es va aprovar
#: aquell dia. El que no es pot fer es aprovar-ne un de nou, perque aquell
#: manifest no diu res de la solucio tecnica ni de l'oferta economica.
MANIFEST_VERSION_V1 = "submission-manifest-v1"


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
class ManifestRelevance:
    """Sobre QUE es va confirmar que un paper acredita una exigencia (C-18).

    Hi son les empremtes i l'estat, no qui ho va signar ni quan: aixo ho diu
    l'auditoria. Si l'usuari es dona de baixa i la seva referencia queda a NULL,
    el paquet no ha de quedar obsolet per aixo: ningu no ha canviat el que es va
    confirmar.
    """

    evidence_item_id: Any
    requirement_id: Any = None
    relevance: str | None = None
    relevance_document_id: Any = None
    relevance_requirement_fingerprint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "evidence_item_id": self.evidence_item_id,
            "requirement_id": self.requirement_id,
            "relevance": self.relevance,
            "relevance_document_id": self.relevance_document_id,
            "relevance_requirement_fingerprint": self.relevance_requirement_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ManifestTechnicalValidation:
    """La base factual d'una validacio tecnica.

    Tornar a validar exactament el mateix contingut contra exactament el mateix
    requisit no canvia res d'aixo, i per tant no ha de superar el paquet encara
    que canviin la persona i l'hora.
    """

    solution_id: Any
    source: str | None = None
    requirement_id: Any = None
    status: str | None = None
    validated_requirement_fingerprint: str | None = None
    validated_content_fingerprint: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "solution_id": self.solution_id,
            "source": self.source,
            "requirement_id": self.requirement_id,
            "status": self.status,
            "validated_requirement_fingerprint": self.validated_requirement_fingerprint,
            "validated_content_fingerprint": self.validated_content_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ManifestSolution:
    """Una solucio tecnica tal com s'oferiria."""

    solution_id: Any
    requirement_id: Any = None
    title: str | None = None
    proposed_solution: str | None = None
    supplier_name: str | None = None
    product_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "solution_id": self.solution_id,
            "requirement_id": self.requirement_id,
            "title": self.title,
            "proposed_solution": self.proposed_solution,
            "supplier_name": self.supplier_name,
            "product_name": self.product_name,
        }


@dataclass(frozen=True, slots=True)
class ManifestAmounts:
    """Els imports que es presentarien. Mai cap xifra de cost ni de marge.

    `None` segueix sent `None`: un total indeterminat no es zero, i escriure-hi
    un zero faria quadrar una oferta que ningu no ha tancat.
    """

    revenue: Decimal | None = None
    vat_amount: Decimal | None = None
    total_with_vat: Decimal | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "revenue": self.revenue,
            "vat_amount": self.vat_amount,
            "total_with_vat": self.total_with_vat,
        }


@dataclass(frozen=True, slots=True)
class ManifestOfferLine:
    line_id: Any
    position: int
    concept: str
    quantity: Decimal | None = None
    pricing_basis: str | None = None
    duration_months_override: int | None = None
    effective_duration_months: int | None = None
    periods: Decimal | None = None
    offered_unit_price_ex_vat: Decimal | None = None
    vat_rate: Decimal | None = None
    supplier_name: str | None = None
    product_name: str | None = None
    requirement_id: Any = None
    technical_solution_id: Any = None
    amounts: ManifestAmounts = field(default_factory=ManifestAmounts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "line_id": self.line_id,
            "position": self.position,
            "concept": self.concept,
            "quantity": self.quantity,
            "pricing_basis": self.pricing_basis,
            "duration_months_override": self.duration_months_override,
            "effective_duration_months": self.effective_duration_months,
            "periods": self.periods,
            "offered_unit_price_ex_vat": self.offered_unit_price_ex_vat,
            "vat_rate": self.vat_rate,
            "supplier_name": self.supplier_name,
            "product_name": self.product_name,
            "requirement_id": self.requirement_id,
            "technical_solution_id": self.technical_solution_id,
            "amounts": self.amounts.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class ManifestOfferSection:
    section_id: Any
    title: str
    position: int
    lines: tuple[ManifestOfferLine, ...] = field(default_factory=tuple)
    amounts: ManifestAmounts = field(default_factory=ManifestAmounts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "section_id": self.section_id,
            "title": self.title,
            "position": self.position,
            "lines": [
                line.as_dict()
                for line in sorted(self.lines, key=lambda row: (row.position, str(row.line_id)))
            ],
            "amounts": self.amounts.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class ManifestOfferLot:
    lot_id: Any
    label: str
    position: int
    tender_lot_id: Any = None
    duration_months: int | None = None
    sections: tuple[ManifestOfferSection, ...] = field(default_factory=tuple)
    amounts: ManifestAmounts = field(default_factory=ManifestAmounts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lot_id": self.lot_id,
            "tender_lot_id": self.tender_lot_id,
            "label": self.label,
            "duration_months": self.duration_months,
            "position": self.position,
            "sections": [
                section.as_dict()
                for section in sorted(
                    self.sections, key=lambda row: (row.position, str(row.section_id))
                )
            ],
            "amounts": self.amounts.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class ManifestOffer:
    """L'oferta economica tal com es presentaria. Nomes files ACTIVES.

    El que esta arxivat no hi entra, i per aixo `archived_at` no es serialitza
    enlloc: arxivar una fila la treu del manifest, que es exactament la senyal
    que el paquet ja no descriu el que s'oferiria.
    """

    currency: str | None = None
    lots: tuple[ManifestOfferLot, ...] = field(default_factory=tuple)
    amounts: ManifestAmounts = field(default_factory=ManifestAmounts)

    def as_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "lots": [
                lot.as_dict()
                for lot in sorted(self.lots, key=lambda row: (row.position, str(row.lot_id)))
            ],
            "amounts": self.amounts.as_dict(),
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

    # --- Base d'aprovacio ---------------------------------------------------- #
    relevance: tuple[ManifestRelevance, ...] = field(default_factory=tuple)
    technical_scope_fingerprint: str | None = None
    technical_validations: tuple[ManifestTechnicalValidation, ...] = field(
        default_factory=tuple
    )

    # --- Contingut ----------------------------------------------------------- #
    solutions: tuple[ManifestSolution, ...] = field(default_factory=tuple)
    offer: ManifestOffer | None = None

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
            # Sobre QUE es va decidir. Canviar-ho vol dir que la persona que va
            # aprovar ja no aprovaria el mateix, encara que el text ofert sigui
            # identic.
            "approval_basis": {
                "analysis": {
                    "requirement_run_id": self.requirement_run_id,
                    "compliance_run_id": self.compliance_run_id,
                    "prefit_score": self.prefit_score,
                },
                # Ordenats aqui i no nomes a qui els construeix: el hash no pot
                # dependre de l'ordre en que la base hagi retornat les files.
                "dossier_relevance": [
                    item.as_dict()
                    for item in sorted(
                        self.relevance, key=lambda row: str(row.evidence_item_id)
                    )
                ],
                "technical_scope_fingerprint": self.technical_scope_fingerprint,
                "technical_validations": [
                    validation.as_dict()
                    for validation in sorted(
                        self.technical_validations, key=lambda row: str(row.solution_id)
                    )
                ],
            },
            # QUE es presentaria. Canviar-ho vol dir que el paquet ja no descriu
            # l'oferta.
            "content": {
                "coverage": {
                    "mandatory_total": self.mandatory_total,
                    "mandatory_covered": self.mandatory_covered,
                    "document_count": len(self.documents),
                },
                "documents": [
                    document.as_dict()
                    for document in sorted(self.documents, key=lambda d: d.position)
                ],
                "technical": [
                    solution.as_dict()
                    for solution in sorted(
                        self.solutions, key=lambda row: str(row.solution_id)
                    )
                ],
                "economic": self.offer.as_dict() if self.offer else None,
            },
        }


def build_manifest(data: ManifestInput) -> tuple[dict[str, Any], str]:
    """El manifest i el seu hash. Mateixa entrada, mateixa sortida, sempre."""
    payload = _scalar(data.as_dict())
    return payload, manifest_hash(payload)
