"""Schemas de la porta final.

Codis, no frases. El servidor diu `economic_offer_price_missing` i la interficie
en fa el text, com a tot el projecte. Aqui no hi ha cap missatge, i per tant
tampoc cap manera que s'hi coli res que soni a assessorament: el sistema
constata fets i qui decideix que fer-ne es una persona.

Tampoc no hi ha cap xifra interna. Cost, marge i markup son informacio de
gestio, viuen a l'oferta i nomes per a qui pot veure'ls; si apareguessin aqui,
aquest contracte s'hauria de redactar per rol i la frontera que va tancar
l'oferta economica es tornaria a obrir per una altra porta.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import WorkspaceReadiness


class FinalReadinessFinding(BaseModel):
    """Un fet, amb el seu codi. Sense text i sense numeros de dins."""

    code: str
    severity: Literal["blocker", "warning", "info"]
    domain: Literal["workspace", "dossier", "technical", "economic"]
    #: La fila concreta, quan el fet en te una: una linia, una solucio, un lot.
    entity_id: uuid.UUID | None = None
    #: Context factual minim: comptadors i estats, mai contingut escrit.
    context: dict[str, Any] = Field(default_factory=dict)


class DossierReadinessRead(BaseModel):
    """Tal com ho diu el dossier. No es recalcula ni es reinterpreta."""

    state: WorkspaceReadiness
    blockers: list[str] = Field(default_factory=list)


class TechnicalReadinessRead(BaseModel):
    """L'abast tecnic i la seva revisio (C-14).

    `scope_reviewed=False` no vol dir que falti feina: vol dir que ningu no ha
    dit encara quina feina fa falta. Es una pregunta oberta, no un incompliment.
    """

    scope_reviewed: bool = False
    scope_stale: bool = False
    scope_reviewed_at: datetime | None = None
    scope_reviewed_by: uuid.UUID | None = None
    requirements_in_scope: int = 0
    findings: list[FinalReadinessFinding] = Field(default_factory=list)


class DocumentReviewRead(BaseModel):
    """Fins on arriba la revisio HUMANA dels plecs.

    `reviewed=False` no vol dir que hi hagi res malament: vol dir que ningu no
    n'ha dit res, i callar no es aprovar. `stale` vol dir que des d'aleshores
    han arribat documents o versions noves —**no** que els requisits hagin
    canviat, que es una altra pregunta i te el seu propi avis.
    """

    reviewed: bool = False
    stale: bool = False
    reviewed_at: datetime | None = None
    #: Quants plecs te la licitacio ara. Zero vol dir que no hi ha res a revisar.
    documents: int = 0


class EconomicReadinessRead(BaseModel):
    offer_exists: bool = False
    findings: list[FinalReadinessFinding] = Field(default_factory=list)


class FinalReadinessRead(BaseModel):
    """El veredicte agregat: dossier, solucio tecnica i oferta economica.

    `ready_for_final_review` es exactament "no hi ha cap finding bloquejant".
    Els avisos i la informacio no impedeixen res, i per aixo hi son: perque es
    puguin veure sense convertir-los en una porta tancada.
    """

    workspace_id: uuid.UUID
    #: La licitacio d'aquesta candidatura. Hi es perque hi ha fets —els plecs—
    #: que no es resolen dins de Preparar sino a la fitxa de la licitacio.
    tender_id: uuid.UUID
    ready_for_final_review: bool
    findings: list[FinalReadinessFinding] = Field(default_factory=list)
    dossier: DossierReadinessRead
    documents: DocumentReviewRead
    technical: TechnicalReadinessRead
    economic: EconomicReadinessRead


class PreparationCount(BaseModel):
    """Pendents de preparacio d'una candidatura: els blocadors de `final-readiness`.

    `pending` nul vol dir que no s'han pogut comptar, mai zero.
    """

    workspace_id: uuid.UUID
    pending: int | None = None


class PreparationCountsRead(BaseModel):
    """Els pendents de preparacio de totes les candidatures obertes i dins de termini."""

    items: list[PreparationCount] = Field(default_factory=list)
