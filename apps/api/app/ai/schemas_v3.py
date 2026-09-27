"""Contracte de sortida del model, versio 3. **No connectat a cap perfil.**

La v1 i la v2 queden intactes. La v3 existeix per dues coses que els runs reals
de Palamos van fer veure i que la v1/v2 no poden dir:

* **Una cita valida no prova cada valor.** A la v3 el requisit no porta un
  `value` lliure: porta `claims` explicits, i cada cita declara a `supports`
  quins claims sosté. El backend ho comprova (`app.ai.contract_v3`).
* **"Es valorara" no fa un criteri d'adjudicacio.** Cada candidat declara que
  creu que es (`kind`) i, si diu que es criteri, QUIN mecanisme el fa formal
  (`adjudication_basis`) i quina cita ho prova. La classe final la decideix el
  backend, i cap candidat no es descarta.

Regles:

* `extra = forbid` a tot arreu; enums tancats.
* `unknown != false`: un claim sense valor es diu `value = null` amb
  `certainty = unknown`, mai `false` ni `0`.
* `partial != complete`: `claims_complete` diu si els claims cobreixen tot el
  requisit, i per defecte es `unknown`, no `complete`.
* Les referencies creuades (una cita que sosté un claim inexistent, un
  mecanisme sense cita) NO fan invalid l'esquema: el backend les detecta element
  a element i les registra, en comptes de perdre el chunk sencer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from app.ai.schemas import DocumentNoteOut, EvidenceOut, StrictModel, UncertaintyOut
from app.models.enums import (
    AwardCriterionType,
    AwardEvaluationType,
    ExtractionConfidence,
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
)

#: Versio del contracte. Es desa a cada Run i forma part de la clau de cache.
#:
#: v3.1 (replay pagat de Palamos): `quantity` amb unitats fisiques i de dades
#: tancades, minuts i segons a les durades, i ancores de fins a 160 caracters.
#: Un run v3 no es pot reprendre en v3.1: el contracte de sortida ha canviat.
SCHEMA_VERSION_V3 = "requirements-schema-v3.2"

#: Identificador d'un claim: tambe es la clau que tindra a `value_json`.
CLAIM_ID_PATTERN = r"^[a-z][a-z0-9_]{0,59}$"
#: Identificador LOCAL d'una cita dins del seu element (`e1`, `e2`...).
EVIDENCE_ID_PATTERN = r"^e[1-9][0-9]?$"

MAX_CLAIMS = 30
MAX_EVIDENCE = 20


class ClaimKind(StrEnum):
    """Que es el valor. Decideix com el backend el busca dins de la cita."""

    COUNT = "count"  # 26 accessos
    AMOUNT = "amount"  # 155.239,16 EUR
    PERCENTAGE = "percentage"  # 10 %
    DURATION = "duration"  # 3 anys
    BANDWIDTH = "bandwidth"  # 600 Mbps
    #: Magnitud fisica o de dades amb unitat tancada: 6,5 W, 100 g, 1,5 m,
    #: 120 dBA, 10 GB, 2,5". Nomes es compara amb la mateixa familia d'unitats.
    QUANTITY = "quantity"
    TEXT = "text"  # "ISO 9001"
    BOOLEAN = "boolean"  # si/no; mai comprovable literalment


class ClaimUnit(StrEnum):
    EUR = "EUR"
    PERCENT = "percent"
    HOURS = "hours"
    DAYS = "days"
    WEEKS = "weeks"
    MONTHS = "months"
    YEARS = "years"
    KBPS = "kbps"
    MBPS = "mbps"
    GBPS = "gbps"
    # v3.1: durades curtes (minuts de trucada, temps de resposta).
    SECONDS = "seconds"
    MINUTES = "minutes"
    # v3.1: `quantity`. Un conjunt TANCAT: una unitat desconeguda es refusa.
    W = "W"
    KW = "kW"
    G = "g"
    KG = "kg"
    MM = "mm"
    CM = "cm"
    M = "m"
    DBA = "dBA"
    DB = "dB"
    MB = "MB"
    GB = "GB"
    TB = "TB"
    INCH = "inch"


_QUANTITY_UNITS = frozenset({
    ClaimUnit.W, ClaimUnit.KW, ClaimUnit.G, ClaimUnit.KG, ClaimUnit.MM, ClaimUnit.CM,
    ClaimUnit.M, ClaimUnit.DBA, ClaimUnit.DB, ClaimUnit.MB, ClaimUnit.GB, ClaimUnit.TB,
    ClaimUnit.INCH,
})

_UNITS_BY_KIND: dict[ClaimKind, frozenset[ClaimUnit]] = {
    ClaimKind.AMOUNT: frozenset({ClaimUnit.EUR}),
    ClaimKind.PERCENTAGE: frozenset({ClaimUnit.PERCENT}),
    ClaimKind.DURATION: frozenset({
        ClaimUnit.SECONDS, ClaimUnit.MINUTES, ClaimUnit.HOURS, ClaimUnit.DAYS,
        ClaimUnit.WEEKS, ClaimUnit.MONTHS, ClaimUnit.YEARS,
    }),
    ClaimKind.BANDWIDTH: frozenset({ClaimUnit.KBPS, ClaimUnit.MBPS, ClaimUnit.GBPS}),
    ClaimKind.QUANTITY: _QUANTITY_UNITS,
}
_NUMERIC_KINDS = frozenset(
    {ClaimKind.COUNT, ClaimKind.AMOUNT, ClaimKind.PERCENTAGE, ClaimKind.DURATION,
     ClaimKind.BANDWIDTH, ClaimKind.QUANTITY}
)
#: Llargada maxima d'una ancora. Les files de taula tecnica de Palamos passen
#: dels 80 caracters ("Routers amb connexions 3G/4G per linia Backup i ...").
MAX_ANCHOR = 160


class ClaimCertainty(StrEnum):
    #: El plec ho diu explicitament.
    STATED = "stated"
    #: El model ho dedueix del context. Mai no pot quedar sostingut sense revisio.
    INFERRED = "inferred"
    #: El plec ho hauria de dir i no ho diu (o no s'ha vist). `value` es null.
    UNKNOWN = "unknown"


class Completeness(StrEnum):
    COMPLETE = "complete"
    #: Se sap que en falta (taula tallada pel chunk, llista amb "etc.").
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class ClaimOutV3(StrictModel):
    """Un valor concret que el requisit afirma, i com trobar-lo a la cita."""

    id: str = Field(pattern=CLAIM_ID_PATTERN)
    kind: ClaimKind
    value: bool | int | float | str | None
    unit: ClaimUnit | None = None
    #: Terme LITERAL de la cita que diu de que es el valor ("GPON", "FTTH",
    #: "volum anual de negocis"). Sense ancora, un numero que coincideix per
    #: casualitat no es pot distingir d'un que sosté el claim.
    anchor: str | None = Field(default=None, min_length=1, max_length=MAX_ANCHOR)
    certainty: ClaimCertainty = ClaimCertainty.STATED

    @model_validator(mode="after")
    def _value_matches_kind(self) -> ClaimOutV3:
        value = self.value
        if value is None:
            if self.certainty != ClaimCertainty.UNKNOWN:
                raise ValueError("value null nomes amb certainty=unknown")
            return self
        if self.certainty == ClaimCertainty.UNKNOWN:
            raise ValueError("certainty=unknown exigeix value null (unknown != false)")
        if self.kind == ClaimKind.BOOLEAN:
            if not isinstance(value, bool):
                raise ValueError("un claim boolean vol true/false")
        elif self.kind == ClaimKind.TEXT:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("un claim text vol una cadena no buida")
        elif isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"un claim {self.kind} vol un numero")
        allowed = _UNITS_BY_KIND.get(self.kind)
        if allowed is None:
            if self.unit is not None:
                raise ValueError(f"un claim {self.kind} no porta unitat")
        elif self.unit not in allowed:
            raise ValueError(f"un claim {self.kind} vol una unitat de {sorted(allowed)}")
        return self

    @property
    def is_numeric(self) -> bool:
        return self.kind in _NUMERIC_KINDS


class EvidenceOutV3(EvidenceOut):
    """Una cita v3: on es, amb un id local, i QUE sosté.

    En un requisit, `supports` son ids de claims. En un candidat a criteri, son
    mecanismes d'`adjudication_basis` (`points`, `formula`...). Una cita pot
    sostenir-ne diversos; un claim pot tenir diverses cites. Una cita que no
    sosté res es valida (context), pero no prova cap valor.
    """

    id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    supports: list[str] = Field(default_factory=list, max_length=MAX_CLAIMS)


def _unique_ids(items: list[Any], what: str) -> None:
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"ids de {what} repetits")


class RequirementOutV3(StrictModel):
    category: RequirementCategory
    obligation: RequirementObligation = RequirementObligation.UNKNOWN
    modality: RequirementModality = RequirementModality.UNKNOWN
    operator: RequirementOperator | None = None
    scope: RequirementScope = RequirementScope.UNKNOWN
    lot_labels: list[str] = Field(default_factory=list)

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    #: Els valors del requisit. **No hi ha `value`**: `value_json` es deriva
    #: d'aqui al backend, i aixi no hi ha dues copies que puguin divergir.
    claims: list[ClaimOutV3] = Field(default_factory=list, max_length=MAX_CLAIMS)
    claims_complete: Completeness = Completeness.UNKNOWN

    alternative_group: str | None = Field(default=None, max_length=80)
    equivalence_allowed: bool = False
    condition_note: str | None = Field(default=None, max_length=500)

    confidence: ExtractionConfidence = ExtractionConfidence.LOW
    evidence: list[EvidenceOutV3] = Field(default_factory=list, max_length=MAX_EVIDENCE)

    @field_validator("lot_labels")
    @classmethod
    def _cap_lot_labels(cls, value: list[str]) -> list[str]:
        return value[:20]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> RequirementOutV3:
        _unique_ids(self.claims, "claims")
        _unique_ids(self.evidence, "cites")
        return self


class AwardCandidateKind(StrEnum):
    """El que el MODEL diu que es. El backend decideix la classe final."""

    AWARD_CRITERION = "award_criterion"
    DESIRABLE_FEATURE = "desirable_feature"
    INFORMATIVE = "informative"


class AdjudicationBasis(StrEnum):
    """El mecanisme que fa que un criteri participi a l'adjudicacio."""

    POINTS = "points"
    WEIGHT = "weight"
    FORMULA = "formula"
    CRITERIA_TABLE = "criteria_table"
    OFFER_FORM = "offer_form"  # model d'oferta del Sobre 2 / caselles
    CRITERIA_SECTION = "criteria_section"  # clausula formal de criteris


class AwardCandidateOutV3(StrictModel):
    """Qualsevol cosa que sembli puntuar. Cap candidat no es descarta."""

    name: str = Field(min_length=1, max_length=500)
    kind: AwardCandidateKind
    #: Els mecanismes que el model diu que el fan formal. Cadascun ha de tenir
    #: una cita que el declari a `supports`; si no, no compta.
    adjudication_basis: list[AdjudicationBasis] = Field(default_factory=list, max_length=6)
    category: str | None = None
    description: str | None = None
    points: float | None = None
    weight_percent: float | None = None
    formula: str | None = None
    criterion_type: AwardCriterionType = AwardCriterionType.UNKNOWN
    evaluation_type: AwardEvaluationType = AwardEvaluationType.UNKNOWN
    scope: RequirementScope = RequirementScope.UNKNOWN
    lot_labels: list[str] = Field(default_factory=list)
    confidence: ExtractionConfidence = ExtractionConfidence.LOW
    evidence: list[EvidenceOutV3] = Field(default_factory=list, max_length=MAX_EVIDENCE)

    @field_validator("adjudication_basis")
    @classmethod
    def _basis_unique(cls, value: list[AdjudicationBasis]) -> list[AdjudicationBasis]:
        if len(value) != len(set(value)):
            raise ValueError("mecanisme repetit")
        return value

    @field_validator("lot_labels")
    @classmethod
    def _cap_lot_labels(cls, value: list[str]) -> list[str]:
        return value[:20]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> AwardCandidateOutV3:
        _unique_ids(self.evidence, "cites")
        return self


class ChunkExtractionOutV3(StrictModel):
    """El que el model retorna per a UN chunk, a la v3.

    v3.2: les dues llistes d'extraccio son OBLIGATORIES (buides si no hi ha res).
    Una resposta sense alguna d'elles no diu "no hi ha res": no es una resposta
    del contracte, i el chunk falla. Les notes i incerteses son opcionals.
    """

    requirements: list[RequirementOutV3]
    award_candidates: list[AwardCandidateOutV3]
    document_notes: list[DocumentNoteOut] = Field(default_factory=list)
    uncertainties: list[UncertaintyOut] = Field(default_factory=list)


def chunk_output_json_schema_v3() -> dict[str, Any]:
    return ChunkExtractionOutV3.model_json_schema()
