"""Schemas de sortida de l'analisi de plecs.

Regla que travessa tot el fitxer: cada requisit visible arriba amb la seva
cadena de provenance sencera (run, extraccio, revisio, pagina i cita). Si algun
dia algu vol amagar-la per fer la resposta mes lleugera, aquest comentari diu
que no: sense provenance, un requisit extret automaticament no es una
afirmacio, es una opinio.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.ai.contract_v3 import (
    StoredSupport,
    adjudication_review_reasons,
    award_class_of,
    stored_requirement_support,
)
from app.models.enums import (
    AnalysisBasis,
    AnalysisRunStatus,
    AwardCriterionType,
    AwardEvaluationType,
    EvidenceRole,
    ExtractionConfidence,
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    RequirementOperator,
    RequirementOrigin,
    RequirementScope,
    RequirementTiming,
    ReviewStatus,
    TerritoryCompleteness,
    TerritoryMatchMode,
)
from app.schemas.provenance import TenderSourceRefRead
from app.schemas.tender_scope import CapabilityNeedRead, RequirementTerritoryRead
from app.workspace.requirement_routing import RequirementRouting, route_requirement


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EvidenceRead(ORMModel):
    id: uuid.UUID
    extraction_id: uuid.UUID
    page_id: uuid.UUID
    tender_document_id: uuid.UUID | None = None
    #: 1-based, com arreu del sistema.
    page_number: int
    quote: str
    char_start: int | None = None
    char_end: int | None = None
    section: str | None = None
    evidence_role: EvidenceRole
    #: Revisio de l'extraccio citada. La interficie hi ha d'anar exactament,
    #: no a l'extraccio vigent: la cita es immutable.
    extraction_revision: int | None = None
    document_name: str | None = None
    #: v3: `{contract, ref, claims: {claim_id: comprovacio}}`. Nomes codis, cap
    #: text. `None` = cita anterior a la v3 (no comprovada, que no es "no sosté").
    claim_support_json: dict[str, Any] | None = None


class RequirementRead(ORMModel):
    id: uuid.UUID
    #: `None` als requisits escrits a ma: no venen de cap execucio d'analisi i
    #: no se n'hi inventa cap (D-09).
    run_id: uuid.UUID | None = None
    #: Qui l'ha escrit. La interficie n'ha de derivar l'atribucio: un requisit
    #: manual NO porta atribucio d'IA.
    origin: RequirementOrigin = RequirementOrigin.EXTRACTED
    #: Nomes als manuals. Un d'extret surt del conjunt efectiu quan la seva run
    #: deixa de ser vigent i no li cal cap marca.
    archived_at: datetime | None = None
    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None
    category: RequirementCategory
    obligation: RequirementObligation
    operator: RequirementOperator | None = None
    scope: RequirementScope
    title: str
    description: str | None = None
    value_json: dict[str, Any] | None = None
    #: Quin contracte segueix `value_json`. `None` = forma lliure de la v1.
    value_schema: str | None = None
    #: El matis de com obliga el plec. `obligation` en guarda la consequencia
    #: conservadora: una alternativa mai hi arriba com a `mandatory`.
    modality: RequirementModality = RequirementModality.UNKNOWN
    #: Requisits amb el mateix grup son vies alternatives de la mateixa exigencia.
    alternative_group: str | None = None
    #: `None` = no consta si totes les vies de la disjuncio hi son.
    alternative_group_complete: bool | None = None
    equivalence_allowed: bool = False
    condition_note: str | None = None
    raw_lot_labels: list[Any] | None = None
    #: `None` als manuals. La columna es NOT NULL per historia —tot requisit
    #: sortia d'un model—, pero aqui no s'exposa: dir "confianca baixa de la IA"
    #: d'una cosa que ha escrit una persona seria una atribucio falsa.
    confidence: ExtractionConfidence | None = None
    review_status: ReviewStatus
    review_note: str | None = None
    manual_override_json: dict[str, Any] | None = None
    reviewed_at: datetime | None = None
    has_conflict: bool
    conflict_group: str | None = None
    created_at: datetime
    #: La provinenca dels EXTRETS. Els manuals la porten a `citations`.
    evidence: list[EvidenceRead] = Field(default_factory=list)
    #: La provinenca dels MANUALS: cites a `tender_source_refs`. Son dues
    #: llistes i no una d'unida perque son dos emmagatzematges amb garanties
    #: diferents, i `RequirementEvidence` no s'ha tocat.
    citations: list[TenderSourceRefRead] = Field(default_factory=list)
    #: 0048: quan s'ha de complir (`unknown` fins que algu ho digui) i a quins
    #: blocs aplica (buit = a tot el contracte).
    timing: RequirementTiming = RequirementTiming.UNKNOWN
    block_ids: list[uuid.UUID] = Field(default_factory=list)
    #: 0049: territoris (nomes emmagatzemats), proves exigides, necessitats
    #: tecniques i criteris dels quals es la cara exigible. Tot neutre a les
    #: files antigues: `any`, `None`, llistes buides.
    territory_match_mode: TerritoryMatchMode = TerritoryMatchMode.ANY
    territory_completeness: TerritoryCompleteness = TerritoryCompleteness.UNKNOWN
    territories: list[RequirementTerritoryRead] = Field(default_factory=list)
    proof_json: list[Any] | None = None
    proof_schema: str | None = None
    capability_needs: list[CapabilityNeedRead] = Field(default_factory=list)
    award_criterion_ids: list[uuid.UUID] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def routing(self) -> RequirementRouting:
        """On es treballa dins d'una candidatura. La regla viu al backend
        (`app/workspace/requirement_routing.py`); la interficie nomes la llegeix."""
        return route_requirement(self)

    def _stored_support(self) -> StoredSupport | None:
        return stored_requirement_support(
            self.value_schema, self.value_json, (e.claim_support_json for e in self.evidence)
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def claim_status(self) -> dict[str, str] | None:
        """v3: estat de cada claim (`supported`, `unverified`, `not_supported`,
        `unknown`). `None` per a v1/v2 i manuals: alla no hi ha claims."""
        support = self._stored_support()
        return support.per_claim if support else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def support_level(self) -> str | None:
        """v3: `quote_valid` < `claim_supported` < `requirement_supported`."""
        support = self._stored_support()
        return support.level if support else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def support_status(self) -> str | None:
        support = self._stored_support()
        return support.status if support else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def review_reasons(self) -> list[str]:
        """v3: per que va a revisio, en codis. Buit per a v1/v2 i manuals."""
        support = self._stored_support()
        return support.review_reasons if support else []


class AwardCriterionRead(ORMModel):
    id: uuid.UUID
    run_id: uuid.UUID
    tender_id: uuid.UUID
    tender_lot_id: uuid.UUID | None = None
    category: str | None = None
    name: str
    description: str | None = None
    points: float | None = None
    weight_percent: float | None = None
    #: NULL quan el plec no la dona. No se n'inventa cap.
    formula: str | None = None
    #: La frase literal que la conte, per poder entendre-la i citar-la.
    formula_text: str | None = None
    formula_variables: list[Any] | None = None
    tranches: list[Any] | None = None
    criterion_type: AwardCriterionType = AwardCriterionType.UNKNOWN
    evaluation_type: AwardEvaluationType
    confidence: ExtractionConfidence
    review_status: ReviewStatus
    review_note: str | None = None
    manual_override_json: dict[str, Any] | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    #: v3: mecanisme, comprovacio i cites (per id) que fan formal o no el
    #: criteri. Nomes codis. `None` per a les files v1/v2.
    adjudication_basis_json: dict[str, Any] | None = None
    evidence: list[EvidenceRead] = Field(default_factory=list)
    #: 0048: quan s'ha de complir i a quins blocs aplica (buit = tot el
    #: contracte). `points` NO es reparteix entre blocs.
    timing: RequirementTiming = RequirementTiming.UNKNOWN
    block_ids: list[uuid.UUID] = Field(default_factory=list)
    #: 0049: requisits que en son la cara exigible. Nomes identificadors.
    requirement_ids: list[uuid.UUID] = Field(default_factory=list)
    #: 0050: cites del criteri (mateix vocabulari que `RequirementRead.citations`).
    citations: list[TenderSourceRefRead] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def award_class(self) -> str | None:
        """v3: la classe que ha decidit el backend. `None` per a v1/v2."""
        return award_class_of(self.adjudication_basis_json)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def review_reasons(self) -> list[str]:
        """v3: per que no es formal, en codis. Buit per a v1/v2 i formals."""
        return adjudication_review_reasons(self.adjudication_basis_json)


class ExtractionRunRead(ORMModel):
    """Metriques i procedencia d'una passada d'analisi."""

    id: uuid.UUID
    tender_id: uuid.UUID
    status: AnalysisRunStatus
    is_current: bool
    input_fingerprint: str
    extractor_version: str
    prompt_version: str
    schema_version: str
    provider: str
    model: str
    started_at: datetime
    completed_at: datetime | None = None

    documents_analyzed: int
    pages_analyzed: int
    chunks_total: int
    chunks_completed: int
    chunks_failed: int
    requirements_extracted: int
    requirements_rejected: int
    award_criteria_extracted: int
    evidence_rejected: int
    validation_errors: int

    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    duration_ms: int | None = None
    #: Documents que no s'han pogut analitzar, amb el motiu. Mai s'inventa el
    #: seu contingut.
    skipped_documents: list[Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    #: Sobre QUE s'ha construit aquesta analisi. `status` nomes diu que el
    #: proces ha acabat; aixo diu si darrere hi ha una lectura real del plec o
    #: una simulacio de desenvolupament. Es deriva del proveidor que ja es desa
    #: al run: cap columna nova que algu hagi de mantenir sincronitzada.
    @computed_field  # type: ignore[prop-decorator]
    @property
    def analysis_basis(self) -> AnalysisBasis:
        return AnalysisBasis.for_provider(self.provider)


class AnalysisRequest(BaseModel):
    #: Ignora la cache i llanca una passada nova.
    force: bool = False
    #: Seleccio manual. Buida = seleccio automatica (PCAP, PPT i annexos).
    document_ids: list[uuid.UUID] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    #: Cert si s'ha reutilitzat un run anterior sense gastar tokens.
    reused: bool
    run: ExtractionRunRead


class ManualRequirementCreate(BaseModel):
    """Un requisit escrit per una persona.

    Nomes hi son els camps que una persona pot DEFINIR. No hi ha `origin`,
    `run_id`, `archived_at`, `confidence` ni `dedup_key`: son fets sobre com ha
    nascut la fila i els fixa el servidor. Deixar-los escriure voldria dir
    deixar que algu digues "aixo ho ha extret un model amb confianca alta" d'una
    cosa que acaba d'escriure ell.
    """

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    category: RequirementCategory
    obligation: RequirementObligation = RequirementObligation.UNKNOWN
    operator: RequirementOperator | None = None
    scope: RequirementScope = RequirementScope.UNKNOWN
    modality: RequirementModality = RequirementModality.UNKNOWN
    value_json: dict[str, Any] | None = None
    tender_lot_id: uuid.UUID | None = None
    alternative_group: str | None = Field(default=None, max_length=80)
    equivalence_allowed: bool = False
    condition_note: str | None = Field(default=None, max_length=500)
    #: Cites ja creades que sostenen aquest requisit. Poden ser zero: un
    #: requisit sense font es una afirmacio humana sense acreditar, i neix
    #: `unreviewed` justament per aixo.
    source_ref_ids: list[uuid.UUID] = Field(default_factory=list)


class ManualRequirementUpdate(BaseModel):
    """Correccions. S'apliquen nomes els camps que arriben."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    category: RequirementCategory | None = None
    obligation: RequirementObligation | None = None
    operator: RequirementOperator | None = None
    scope: RequirementScope | None = None
    modality: RequirementModality | None = None
    value_json: dict[str, Any] | None = None
    tender_lot_id: uuid.UUID | None = None
    alternative_group: str | None = Field(default=None, max_length=80)
    equivalence_allowed: bool | None = None
    condition_note: str | None = Field(default=None, max_length=500)


class RequirementCitationLink(BaseModel):
    """Enllaça una cita JA CREADA. Aqui no se'n crea cap: una cita es immutable
    i es crea pel seu propi cami."""

    model_config = ConfigDict(extra="forbid")

    source_ref_id: uuid.UUID


class ManualRequirementResult(BaseModel):
    """`changed` es fals quan el PATCH no ha canviat cap valor."""

    changed: bool = True
    requirement: RequirementRead


class RequirementAnalysisRead(BaseModel):
    """El que necessita la fitxa per pintar la seccio sencera."""

    run: ExtractionRunRead | None = None
    requirements: list[RequirementRead] = Field(default_factory=list)
    #: NOMES criteris d'adjudicacio: els de v1/v2 i els v3 formals.
    award_criteria: list[AwardCriterionRead] = Field(default_factory=list)
    #: v3: candidats a revisio, caracteristiques desitjables i text informatiu.
    #: Es conserven, pero fora de `award_criteria`: la fitxa actual els pintaria
    #: com a criteris que puntuen. Buit per a tota analisi v1/v2.
    award_candidates: list[AwardCriterionRead] = Field(default_factory=list)
    #: Cert si els documents han canviat des de l'analisi vigent.
    is_stale: bool = False


class ReviewRequest(BaseModel):
    """Revisio humana. NO sobreescriu l'output del model.

    `manual_override` es guarda a banda precisament per no perdre que va dir
    l'extractor: sense aixo no es podria auditar mai si el model encertava.
    """

    review_status: ReviewStatus
    note: str | None = None
    manual_override: dict[str, Any] | None = None
