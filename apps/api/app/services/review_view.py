"""Capa de revisio canonica per empresa: materialitzacio i lectura (S-R1).

Aquest modul no escriu MAI a l'extraccio. Llegeix `requirements` i
`requirement_evidence` —que son immutables— i escriu nomes a les taules
`review_*`, sempre amb el `company_id` de qui ho demana.

**Els camps de revisio LEGACY no hi compten.** `Requirement.review_status`,
`manual_override_json`, `review_note`, `reviewed_by` i `reviewed_at` els
escriuen els endpoints globals `POST /requirements/{id}/review` i
`POST /award-criteria/{id}/review`, que modifiquen files COMPARTIDES entre
empreses. La unica cosa que se'n llegeix es el senyal del PIPELINE
(`review_status = needs_review`), i nomes si cap revisio humana no l'ha tocat
(`reviewed_at` i `reviewed_by` buits). Si algu l'ha tocat, el senyal es refa a
partir de les cites i els claims, que son immutables.

**Materialitzar es idempotent.** `start_review` crea un element per a cada
requisit EXTRET de la run vigent que aquesta empresa encara no tingui, amb la
seva font primaria i la revisio 1 (`create`). Tornar-lo a cridar no crea res.
Els manuals, els criteris d'adjudicacio i les runs velles no hi entren en
aquesta primera fase.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.contract_v3 import VALUE_SCHEMA_V3, stored_requirement_support
from app.core.errors import NotFoundError
from app.models.enums import (
    RequirementModality,
    RequirementObligation,
    ReviewAction,
    ReviewItemKind,
    ReviewOriginHint,
    ReviewSourceRole,
    ReviewStatus,
    ReviewVerification,
)
from app.models.provenance import TenderSourceRef
from app.models.requirements import Requirement, RequirementEvidence
from app.models.review import ReviewItem, ReviewItemRevision, ReviewItemSource
from app.models.tender_documents import TenderDocumentExtraction, TenderDocumentFile
from app.models.tenders import Tender, TenderDocument
from app.schemas.review import (
    AcceptedValue,
    CanonicalEvidence,
    CanonicalRequirement,
    CanonicalSourceRef,
    CanonicalValue,
    CountLabel,
    RequirementSnapshot,
    ReviewOverview,
    ReviewStartResult,
)
from app.services.manual_requirement import effective_requirements
from app.tenders.visibility import visible_to_company

# --------------------------------------------------------------------------- #
# Vocabulari de la interficie                                                   #
# --------------------------------------------------------------------------- #

VERIFICATION_LABELS = {
    ReviewVerification.UNREVIEWED: "Pendent de revisar",
    ReviewVerification.VERIFIED: "Verificat",
    ReviewVerification.PARTIAL: "Parcial",
    ReviewVerification.REJECTED: "Descartat",
    ReviewVerification.NEEDS_REVIEW: "Cal revisar",
}
PROVENANCE_AUTOMATED = "automated"
PROVENANCE_MANUAL = "manual"
PROVENANCE_CORRECTED = "automated_corrected"
PROVENANCE_ORDER = (PROVENANCE_AUTOMATED, PROVENANCE_CORRECTED, PROVENANCE_MANUAL)
PROVENANCE_LABELS = {
    PROVENANCE_AUTOMATED: "Extret automàticament",
    PROVENANCE_MANUAL: "Afegit manualment",
    PROVENANCE_CORRECTED: "Extret i corregit",
}
VALUE_SOURCE_LABELS = {"document": "Del document", "reviewer": "Afirmat pel revisor"}

EVIDENCE_CONFIRMED = "Confirmat al document"
EVIDENCE_NO_VALUES = "Al document, sense valors"
EVIDENCE_TO_CONFIRM = "Al document, valor per confirmar"
EVIDENCE_CITED = "Citat manualment"
EVIDENCE_NONE = "Sense cita"

CATEGORY_LABELS = {
    "deadline": "Termini",
    "economic_solvency": "Solvència econòmica",
    "technical_solvency": "Solvència tècnica",
    "experience": "Experiència",
    "certification": "Certificació",
    "registration": "Inscripció en registres",
    "business_classification": "Classificació empresarial",
    "insurance": "Assegurança",
    "personnel": "Personal",
    "professional_qualification": "Titulació professional",
    "equipment_or_resources": "Mitjans i equips",
    "administrative_documentation": "Documentació administrativa",
    "technical_requirement": "Requisit tècnic",
    "contract_condition": "Condició contractual",
    "other": "Altres",
}


def obligation_label(obligation: str, modality: str) -> str:
    """Una sola etiqueta a partir d'`obligation` i `modality`.

    La modalitat es mes precisa quan diu com obliga (condicional, alternativa);
    si no, mana l'obligacio. Mai no s'ensenya `mandatory/mandatory`.
    """
    if modality == RequirementModality.CONDITIONAL:
        return "Condicional"
    if modality == RequirementModality.ALTERNATIVE:
        return "Alternatiu"
    if (
        obligation == RequirementObligation.INFORMATIVE
        or modality == RequirementModality.INFORMATIONAL
    ):
        return "Informatiu"
    if obligation == RequirementObligation.SCORED or modality == RequirementModality.OPTIONAL:
        return "Valorable"
    if obligation == RequirementObligation.MANDATORY or modality == RequirementModality.MANDATORY:
        return "Obligatori"
    return "Per determinar"


# --------------------------------------------------------------------------- #
# Instantania a partir de l'extraccio                                           #
# --------------------------------------------------------------------------- #


def _claims(requirement: Requirement) -> list[Mapping[str, Any]]:
    if requirement.value_schema != VALUE_SCHEMA_V3 or not isinstance(
        requirement.value_json, Mapping
    ):
        return []
    return [c for c in requirement.value_json.get("claims") or [] if isinstance(c, Mapping)]


def needs_review_from_extraction(
    requirement: Requirement, evidence: Iterable[RequirementEvidence]
) -> bool:
    """El senyal de revisio que va deixar el PIPELINE, no cap revisio humana.

    Sense revisio humana, `review_status` es exactament el que va escriure la
    canonada. Si una revisio LEGACY l'ha tocat, aquell valor ja no es de la
    canonada: es refa amb les cites i els claims (v3), i si no n'hi ha, es
    demana revisio per prudencia.
    """
    if requirement.reviewed_at is None and requirement.reviewed_by is None:
        return requirement.review_status == ReviewStatus.NEEDS_REVIEW
    support = stored_requirement_support(
        requirement.value_schema,
        requirement.value_json,
        [e.claim_support_json for e in evidence],
    )
    return bool(support.review_reasons) if support is not None else True


def snapshot_of(
    requirement: Requirement, evidence: Iterable[RequirementEvidence]
) -> RequirementSnapshot:
    """El que l'empresa accepta al principi: exactament el que diu l'extraccio."""
    evidence = list(evidence)
    operator = requirement.operator.value if requirement.operator is not None else None
    values = [
        AcceptedValue(
            key=str(claim.get("id")),
            operator=operator,
            value=claim.get("value"),
            unit_label=claim.get("unit"),
            source="document",
        )
        for claim in _claims(requirement)
        if claim.get("id")
    ]
    return RequirementSnapshot(
        title=requirement.title,
        description=requirement.description,
        category=requirement.category.value,
        obligation=requirement.obligation.value,
        modality=requirement.modality.value,
        values=values,
        needs_review_from_extraction=needs_review_from_extraction(requirement, evidence),
    )


# --------------------------------------------------------------------------- #
# Acces                                                                          #
# --------------------------------------------------------------------------- #


async def load_visible_tender(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> Tender:
    """La licitacio, si aquesta empresa la pot veure. Si no, 404 com arreu."""
    tender = await session.scalar(
        sa.select(Tender).where(Tender.id == tender_id, visible_to_company(company_id))
    )
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def _evidence_by_requirement(
    session: AsyncSession, requirement_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[RequirementEvidence]]:
    ids = list(requirement_ids)
    grouped: dict[uuid.UUID, list[RequirementEvidence]] = defaultdict(list)
    if not ids:
        return grouped
    rows = await session.scalars(
        sa.select(RequirementEvidence)
        .where(RequirementEvidence.requirement_id.in_(ids))
        .order_by(RequirementEvidence.page_number, RequirementEvidence.id)
    )
    for row in rows:
        grouped[row.requirement_id].append(row)
    return grouped


# --------------------------------------------------------------------------- #
# Materialitzacio                                                               #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Materialised:
    created: int
    already_present: int


async def _active_primary_requirement_ids(
    session: AsyncSession, company_id: uuid.UUID, requirement_ids: Iterable[uuid.UUID]
) -> set[uuid.UUID]:
    ids = list(requirement_ids)
    if not ids:
        return set()
    rows = await session.scalars(
        sa.select(ReviewItemSource.requirement_id).where(
            ReviewItemSource.company_id == company_id,
            ReviewItemSource.requirement_id.in_(ids),
            ReviewItemSource.link_role == ReviewSourceRole.PRIMARY,
            ReviewItemSource.unlinked_at.is_(None),
        )
    )
    return {row for row in rows if row is not None}


async def start_review(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    actor_id: uuid.UUID | None,
) -> ReviewStartResult:
    """Crea els elements que falten per als requisits EXTRETS vigents. Idempotent.

    No fa commit: el decideix qui crida. Si una altra peticio materialitza el
    mateix alhora, l'index unic parcial ho atura, es desfa i es torna a
    comptar: el resultat final es el mateix que una sola crida.
    """
    effective = await effective_requirements(session, tender_id)
    extracted = list(effective.extracted)
    present = await _active_primary_requirement_ids(
        session, company_id, (r.id for r in extracted)
    )
    missing = [r for r in extracted if r.id not in present]
    evidence = await _evidence_by_requirement(session, (r.id for r in missing))

    children: list[ReviewItemSource | ReviewItemRevision] = []
    for requirement in missing:
        snapshot = snapshot_of(requirement, evidence.get(requirement.id, []))
        status = (
            ReviewVerification.NEEDS_REVIEW
            if snapshot.needs_review_from_extraction
            else ReviewVerification.UNREVIEWED
        )
        accepted = snapshot.model_dump(mode="json")
        item = ReviewItem(
            id=uuid.uuid4(),
            company_id=company_id,
            tender_id=tender_id,
            kind=ReviewItemKind.REQUIREMENT,
            verification_status=status,
            award_role=None,
            accepted_json=accepted,
            origin_hint=ReviewOriginHint.EXTRACTION,
            revision=1,
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(item)
        children.append(
            ReviewItemSource(
                item_id=item.id,
                company_id=company_id,
                requirement_id=requirement.id,
                link_role=ReviewSourceRole.PRIMARY,
                created_by=actor_id,
            )
        )
        children.append(
            ReviewItemRevision(
                item_id=item.id,
                company_id=company_id,
                revision_no=1,
                action=ReviewAction.CREATE,
                verification_status_after=status,
                accepted_json_after=accepted,
                payload_json={"source": "extraction", "run_id": str(requirement.run_id)},
                actor_id=actor_id,
            )
        )
    try:
        # Primer els elements i despres les filles: les filles els referencien.
        await session.flush()
        session.add_all(children)
        await session.flush()
    except IntegrityError:
        # Una materialitzacio concurrent ja les ha creat: res a fer.
        await session.rollback()
        present = await _active_primary_requirement_ids(
            session, company_id, (r.id for r in extracted)
        )
        return ReviewStartResult(
            tender_id=tender_id, created=0, already_present=len(present),
            total_requirements=len(present),
        )
    return ReviewStartResult(
        tender_id=tender_id,
        created=len(missing),
        already_present=len(extracted) - len(missing),
        total_requirements=len(extracted),
    )


# --------------------------------------------------------------------------- #
# Lectura canonica                                                              #
# --------------------------------------------------------------------------- #


def _checks_per_claim(evidence: Iterable[RequirementEvidence]) -> dict[str, list[str]]:
    checks: dict[str, list[str]] = defaultdict(list)
    for row in evidence:
        stored = (row.claim_support_json or {}).get("claims")
        if isinstance(stored, Mapping):
            for claim_id, check in stored.items():
                checks[str(claim_id)].append(str(check))
    return checks


def _value_status(checks: list[str]) -> str:
    return EVIDENCE_CONFIRMED if "supported" in checks else EVIDENCE_TO_CONFIRM


def _evidence_status(row: RequirementEvidence) -> str:
    stored = (row.claim_support_json or {}).get("claims")
    checks = list(stored.values()) if isinstance(stored, Mapping) else []
    if not checks:
        return EVIDENCE_NO_VALUES
    return EVIDENCE_CONFIRMED if all(c == "supported" for c in checks) else EVIDENCE_TO_CONFIRM


async def _document_names(
    session: AsyncSession, tender_id: uuid.UUID, extraction_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    ids = list(set(extraction_ids))
    if not ids:
        return {}
    rows = await session.execute(
        sa.select(TenderDocumentExtraction.id, TenderDocument.name)
        .join(
            TenderDocumentFile,
            TenderDocumentFile.content_id == TenderDocumentExtraction.content_id,
        )
        .join(TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id)
        .where(TenderDocumentExtraction.id.in_(ids), TenderDocument.tender_id == tender_id)
    )
    names: dict[uuid.UUID, str] = {}
    for extraction_id, name in rows:
        names.setdefault(extraction_id, name)
    return names


@dataclass(frozen=True)
class _ItemContext:
    """Tot el que cal per llegir un lot d'elements, carregat d'una vegada."""

    primary: dict[uuid.UUID, uuid.UUID]
    evidence: dict[uuid.UUID, list[RequirementEvidence]]
    names: dict[uuid.UUID, str]
    corrected: set[uuid.UUID]
    citations: dict[uuid.UUID, list[TenderSourceRef]]
    citation_names: dict[uuid.UUID, str]


async def _item_context(
    session: AsyncSession, tender_id: uuid.UUID, items: list[ReviewItem]
) -> _ItemContext:
    ids = [i.id for i in items]
    sources = (
        await session.execute(
            sa.select(
                ReviewItemSource.item_id,
                ReviewItemSource.requirement_id,
                ReviewItemSource.source_ref_id,
                ReviewItemSource.link_role,
            ).where(ReviewItemSource.item_id.in_(ids), ReviewItemSource.unlinked_at.is_(None))
        )
    ).all()
    primary = {
        item_id: requirement_id
        for item_id, requirement_id, _ref, role in sources
        if role == ReviewSourceRole.PRIMARY and requirement_id is not None
    }
    ref_links = [(item_id, ref) for item_id, _req, ref, _role in sources if ref is not None]
    refs: dict[uuid.UUID, TenderSourceRef] = {}
    if ref_links:
        rows = await session.scalars(
            sa.select(TenderSourceRef).where(
                TenderSourceRef.id.in_([ref for _item, ref in ref_links])
            )
        )
        refs = {ref.id: ref for ref in rows.all()}
    citations: dict[uuid.UUID, list[TenderSourceRef]] = defaultdict(list)
    for item_id, ref_id in ref_links:
        if ref_id in refs:
            citations[item_id].append(refs[ref_id])
    document_ids = {r.tender_document_id for r in refs.values() if r.tender_document_id}
    citation_names: dict[uuid.UUID, str] = {}
    if document_ids:
        named = await session.execute(
            sa.select(TenderDocument.id, TenderDocument.name).where(
                TenderDocument.id.in_(document_ids)
            )
        )
        citation_names = {doc_id: name for doc_id, name in named.all()}
    evidence = await _evidence_by_requirement(session, primary.values())
    names = await _document_names(
        session, tender_id, (e.extraction_id for rows in evidence.values() for e in rows)
    )
    corrected = set(
        (
            await session.scalars(
                sa.select(ReviewItemRevision.item_id).where(
                    ReviewItemRevision.item_id.in_(ids),
                    ReviewItemRevision.action == ReviewAction.CORRECT,
                )
            )
        ).all()
    )
    return _ItemContext(primary, evidence, names, corrected, citations, citation_names)


def provenance_of(item: ReviewItem, has_primary: bool, corrected: bool) -> str:
    """Derivada, mai desada: d'on surt i si el revisor l'ha corregit.

    * `manual`: l'ha afegit el revisor i no te cap requisit extret al darrere.
    * `automated`: te un requisit extret com a font primaria i cap correccio.
    * `automated_corrected`: te font extreta i almenys una correccio. Una
      correccio que no canvia res es refusa, aixi que n'hi ha prou que existeixi.
    """
    if has_primary:
        return PROVENANCE_CORRECTED if corrected else PROVENANCE_AUTOMATED
    return PROVENANCE_MANUAL


def _canonical_one(item: ReviewItem, ctx: _ItemContext) -> CanonicalRequirement:
    snapshot = RequirementSnapshot.model_validate(item.accepted_json)
    requirement_id = ctx.primary.get(item.id)
    rows = ctx.evidence.get(requirement_id, []) if requirement_id else []
    checks = _checks_per_claim(rows)
    values = [
        CanonicalValue(
            key=value.key,
            operator=value.operator,
            value=value.value,
            unit_label=value.unit_label,
            source_label=VALUE_SOURCE_LABELS[value.source],
            status_label=(
                _value_status(checks.get(value.key, []))
                if value.source == "document"
                else VALUE_SOURCE_LABELS["reviewer"]
            ),
        )
        for value in snapshot.values
    ]
    cited = [
        CanonicalEvidence(
            document=ctx.names.get(row.extraction_id),
            page=row.page_number,
            section=row.section,
            quote=row.quote,
            status_label=_evidence_status(row),
        )
        for row in rows
    ]
    refs = ctx.citations.get(item.id, [])
    cited += [
        CanonicalEvidence(
            document=(
                ctx.citation_names.get(ref.tender_document_id)
                if ref.tender_document_id
                else None
            ),
            page=ref.page_number,
            section=ref.location_label,
            quote=ref.quote,
            status_label=EVIDENCE_CITED,
        )
        for ref in refs
    ]
    document_values = [v for v in snapshot.values if v.source == "document"]
    document_labels = [
        _value_status(checks.get(v.key, [])) for v in document_values
    ]
    if requirement_id is None:
        evidence_label = EVIDENCE_CITED if refs else EVIDENCE_NONE
    elif not document_values:
        evidence_label = EVIDENCE_NO_VALUES
    elif all(label == EVIDENCE_CONFIRMED for label in document_labels):
        evidence_label = EVIDENCE_CONFIRMED
    else:
        evidence_label = EVIDENCE_TO_CONFIRM
    provenance = provenance_of(item, requirement_id is not None, item.id in ctx.corrected)
    verification = ReviewVerification(item.verification_status)
    source_refs = (
        [CanonicalSourceRef(kind="extracted_requirement", id=requirement_id)]
        if requirement_id
        else []
    ) + [CanonicalSourceRef(kind="citation", id=ref.id) for ref in refs]
    return CanonicalRequirement(
        canonical_id=item.id,
        title=snapshot.title,
        description=snapshot.description,
        category=snapshot.category.value,
        category_label=CATEGORY_LABELS.get(snapshot.category.value, "Altres"),
        obligation_label=obligation_label(snapshot.obligation.value, snapshot.modality.value),
        provenance=provenance,
        provenance_label=PROVENANCE_LABELS[provenance],
        verification_status=verification.value,
        verification_label=VERIFICATION_LABELS[verification],
        needs_review=verification == ReviewVerification.NEEDS_REVIEW,
        values=values,
        evidence_label=evidence_label,
        evidence=cited,
        documents=sorted({c.document for c in cited if c.document}),
        pages=sorted({c.page for c in cited if c.page is not None}),
        sections=sorted({c.section for c in cited if c.section}),
        source_refs=source_refs,
        review_revision=item.revision,
    )


async def canonical_items(
    session: AsyncSession, tender_id: uuid.UUID, items: list[ReviewItem]
) -> list[CanonicalRequirement]:
    if not items:
        return []
    ctx = await _item_context(session, tender_id, items)
    return [_canonical_one(item, ctx) for item in items]


def requirement_items(company_id: uuid.UUID, tender_id: uuid.UUID) -> sa.Select:
    return sa.select(ReviewItem).where(
        ReviewItem.company_id == company_id,
        ReviewItem.tender_id == tender_id,
        ReviewItem.kind == ReviewItemKind.REQUIREMENT,
        ReviewItem.archived_at.is_(None),
    )


async def list_requirements(
    session: AsyncSession, *, company_id: uuid.UUID, tender_id: uuid.UUID
) -> list[CanonicalRequirement]:
    items = list(
        (
            await session.scalars(
                requirement_items(company_id, tender_id).order_by(
                    ReviewItem.created_at, ReviewItem.id
                )
            )
        ).all()
    )
    return await canonical_items(session, tender_id, items)


async def get_requirement(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    item_id: uuid.UUID,
) -> CanonicalRequirement:
    """Un element d'AQUESTA empresa. El d'una altra empresa dona 404, no 403."""
    item = await session.scalar(
        requirement_items(company_id, tender_id).where(ReviewItem.id == item_id)
    )
    if item is None:
        raise NotFoundError("Element de revisio no trobat.")
    [canonical] = await canonical_items(session, tender_id, [item])
    return canonical


async def overview(
    session: AsyncSession, *, company_id: uuid.UUID, tender_id: uuid.UUID
) -> ReviewOverview:
    items = list((await session.scalars(requirement_items(company_id, tender_id))).all())
    effective = await effective_requirements(session, tender_id)
    present = await _active_primary_requirement_ids(
        session, company_id, (r.id for r in effective.extracted)
    )
    by_status = Counter(ReviewVerification(i.verification_status) for i in items)
    by_provenance: Counter[str] = Counter()
    if items:
        ctx = await _item_context(session, tender_id, items)
        by_provenance.update(
            provenance_of(i, i.id in ctx.primary, i.id in ctx.corrected) for i in items
        )
    return ReviewOverview(
        tender_id=tender_id,
        review_started=bool(items),
        total_requirements=len(items),
        pending_materialisation=len(effective.extracted) - len(present),
        needs_review_count=by_status[ReviewVerification.NEEDS_REVIEW],
        verification_counts=[
            CountLabel(code=status.value, label=VERIFICATION_LABELS[status],
                       count=by_status[status])
            for status in ReviewVerification
        ],
        provenance_counts=[
            CountLabel(code=code, label=PROVENANCE_LABELS[code], count=by_provenance[code])
            for code in PROVENANCE_ORDER
            if by_provenance[code] or code == PROVENANCE_AUTOMATED
        ],
    )
