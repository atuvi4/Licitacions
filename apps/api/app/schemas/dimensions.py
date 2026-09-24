"""Com es llegeixen i s'escriuen les dimensions (S3-S6).

Dues coses que el client **no** decideix, i per aixo no son als schemas
d'escriptura: l'estat i el valor de treball. Decidir te el seu endpoint i
exigeix un motiu; si es pogues fer amb un PATCH de metadades, "decidit" seria
nomes un camp mes i el motiu es podria esquivar.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DimensionStatus, ObservationSourceKind
from app.schemas.common import ORMModel
from app.schemas.provenance import TenderSourceRefRead


class ObservationRead(ORMModel):
    """Una font que afirma un valor.

    `value_num` es `None` quan el valor no es comparable. No vol dir que no
    n'hi hagi: vol dir que ningu no ha dit quin numero es, i el sistema no ho
    dedueix del text.
    """

    id: uuid.UUID
    source_kind: ObservationSourceKind
    value_text: str
    value_num: Decimal | None = None
    note: str | None = None
    created_at: datetime
    created_by: uuid.UUID | None = None
    #: La cita, resolta. Apunta a la versio que es va llegir, no a la vigent.
    source_ref: TenderSourceRefRead | None = None


class DimensionRead(ORMModel):
    """Una magnitud, el seu valor de treball i les fonts que la sostenen."""

    id: uuid.UUID
    key: str
    label: str
    unit: str | None = None
    scope_label: str | None = None
    working_value_text: str | None = None
    working_value_num: Decimal | None = None
    status: DimensionStatus
    decision_reason: str | None = None
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    archived_at: datetime | None = None
    #: El testimoni de CAS. El seguent escrit ha de portar aquest numero.
    revision: int
    observations: list[ObservationRead] = Field(default_factory=list)


class DimensionCreate(BaseModel):
    """Crear el concepte. **Crear no es decidir.**

    No hi ha valor de treball ni estat: neix `undecided` encara que qui la crea
    ja sapiga el numero, perque fixar-lo exigeix explicar-se.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=300)
    unit: str | None = Field(default=None, max_length=50)
    scope_label: str | None = Field(default=None, max_length=300)


class DimensionUpdate(BaseModel):
    """Corregir les metadades. Res que toqui la decisio."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    key: str | None = Field(default=None, min_length=1, max_length=100)
    label: str | None = Field(default=None, min_length=1, max_length=300)
    unit: str | None = Field(default=None, max_length=50)
    scope_label: str | None = Field(default=None, max_length=300)


class ObservationCreate(BaseModel):
    """Registrar que una font diu aixo.

    `value_num` l'omple qui ho registra quan el valor es un numero; el servidor
    no el treu del text. `source_ref_id` es una cita JA creada —de la mateixa
    licitacio—, i nomes te sentit quan la font es el plec.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    source_kind: ObservationSourceKind
    value_text: str = Field(min_length=1, max_length=500)
    value_num: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    note: str | None = None
    source_ref_id: uuid.UUID | None = None


class DimensionDecide(BaseModel):
    """Fixar el valor de treball. El motiu no es opcional."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    working_value_text: str = Field(min_length=1, max_length=500)
    working_value_num: Decimal | None = Field(default=None, max_digits=18, decimal_places=4)
    reason: str = Field(min_length=1)


class DimensionArchive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
