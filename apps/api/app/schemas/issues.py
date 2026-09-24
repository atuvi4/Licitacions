"""Com es llegeixen i s'escriuen els dubtes (S3-S10).

Dues coses que el client no decideix, i per aixo no son als schemes
d'escriptura general: **l'estat de tancament** i **si el dubte bloqueja la
revisio final**. Cadascun te la seva accio, perque tancar exigeix dir com i
bloquejar es una decisio d'oferta que nomes pren `DECISION_ROLES`. Si es
poguessin escriure amb el PATCH general, qualsevol editor tancaria un dubte
sense resposta o aturaria una candidatura sense adonar-se'n.

I una tercera: l'issue enllaca entitats **per identificador**, no incrustant-les.
Si hi portes la linia economica sencera, el cost hi entraria per una via que no
te redaccio.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import IssueStatus, IssueType
from app.schemas.common import ORMModel
from app.schemas.provenance import TenderSourceRefRead


class IssueRead(ORMModel):
    """Un dubte tal com el veu qui prepara la candidatura."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    issue_type: IssueType
    status: IssueStatus
    title: str
    description: str | None = None

    #: Nomes identificadors: el context el resol la pantalla amb el que ja te.
    #: Incrustar-hi les entitats obriria una via lateral cap a dades internes.
    requirement_id: uuid.UUID | None = None
    award_criterion_id: uuid.UUID | None = None
    technical_solution_id: uuid.UUID | None = None
    solution_option_id: uuid.UUID | None = None
    economic_offer_line_id: uuid.UUID | None = None
    dimension_id: uuid.UUID | None = None

    supplier_name: str | None = None
    owner_id: uuid.UUID | None = None
    due_at: datetime | None = None

    resolution: str | None = None
    resolution_source: str | None = None
    #: Nomes els omple RESOLDRE. Descartar els deixa buits: descartar no es
    #: resoldre.
    resolved_by: uuid.UUID | None = None
    resolved_at: datetime | None = None

    #: Decisio d'oferta. Nomes `DECISION_ROLES` la canvia, i per la seva accio.
    blocks_final_review: bool

    created_by: uuid.UUID | None = None
    updated_by: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    #: Les cites que el sosten, resoltes. Apunten a la versio que es va llegir.
    source_refs: list[TenderSourceRefRead] = Field(default_factory=list)


class IssueCreate(BaseModel):
    """Obrir un dubte.

    No hi ha `status` ni `blocks_final_review`: neix obert i sense bloquejar res.
    Deixar-los entrar aqui seria una via per fabricar una candidatura bloquejada
    des de qualsevol rol que pugui escriure.
    """

    model_config = ConfigDict(extra="forbid")

    issue_type: IssueType
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None

    requirement_id: uuid.UUID | None = None
    award_criterion_id: uuid.UUID | None = None
    technical_solution_id: uuid.UUID | None = None
    solution_option_id: uuid.UUID | None = None
    economic_offer_line_id: uuid.UUID | None = None
    dimension_id: uuid.UUID | None = None

    supplier_name: str | None = Field(default=None, max_length=300)
    owner_id: uuid.UUID | None = None
    due_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El titol no pot ser nomes espais.")
        return value.strip()


class IssueUpdate(BaseModel):
    """Corregir el que va escriure una persona.

    `status` nomes admet `open` i `waiting`: tancar un dubte exigeix dir com, i
    te la seva accio.
    """

    model_config = ConfigDict(extra="forbid")

    issue_type: IssueType | None = None
    status: IssueStatus | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None

    requirement_id: uuid.UUID | None = None
    award_criterion_id: uuid.UUID | None = None
    technical_solution_id: uuid.UUID | None = None
    solution_option_id: uuid.UUID | None = None
    economic_offer_line_id: uuid.UUID | None = None
    dimension_id: uuid.UUID | None = None

    supplier_name: str | None = Field(default=None, max_length=300)
    owner_id: uuid.UUID | None = None
    due_at: datetime | None = None
    resolution_source: str | None = None


class IssueResolve(BaseModel):
    """Tancar amb resposta. El text no es opcional."""

    model_config = ConfigDict(extra="forbid")

    resolution: str = Field(min_length=1)
    resolution_source: str | None = None


class IssueDismiss(BaseModel):
    """Tancar perque ja no ve al cas.

    Sense motiu obligatori: el disseny congelat no en demana cap, i exigir-lo
    faria escriure frases buides per tancar coses que simplement han deixat de
    ser rellevants.
    """

    model_config = ConfigDict(extra="forbid")


class IssueBlockWrite(BaseModel):
    """Decidir si el dubte impedeix la revisio final."""

    model_config = ConfigDict(extra="forbid")

    blocks_final_review: bool


class IssueSourceRefWrite(BaseModel):
    """Enganxar una cita JA creada, de la mateixa licitacio."""

    model_config = ConfigDict(extra="forbid")

    source_ref_id: uuid.UUID
