"""Analitzar un plec per a una empresa (0051): l'unica entrada que llegeix i escriu.

    run = await analyze_tender(session, company_id=..., tender_id=...)

1. carrega l'estructura vigent del plec (0048-0050) en una vista pura;
2. en calcula el `tender_structure_fingerprint`;
3. fa la fotografia actual del coneixement de l'empresa (fase 2);
4. en pren el `knowledge_fingerprint`;
5. resol les versions que ha llegit (revisio, extraccio, classificacio, revisio
   humana);
6. calcula l'`inputs_hash`;
7. si ja hi ha una run identica, la reutilitza (i la torna a fer vigent);
8. executa el motor pur;
9. persisteix run + resultats en una sola transaccio;
10. deixa l'anterior no vigent i la nova vigent.

Cap efecte sobre el coneixement, la licitacio, els requisits, els criteris ni
l'abast: l'analisi nomes escriu a les set taules de 0051.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import engine
from app.analysis.fingerprint import (
    PACKAGE_INCOMPLETE,
    BlockView,
    CriterionView,
    FactView,
    NeedView,
    PackageGapView,
    RequirementView,
    ScopeItemView,
    SiteView,
    TenderStructureView,
    TerritoryView,
    inputs_hash,
    package_status_for,
    review_revision_hash,
    section_presence,
    section_status_for,
    sha256_of,
    structure_fingerprint,
)
from app.analysis.registry import RULES_VERSION, ExplanationCode
from app.analysis.territory import Area, Coverage
from app.core.errors import AnalysisPrerequisitesMissingError, NotFoundError
from app.models.classification import TenderDocumentClassificationRun
from app.models.enums import KnowledgeLifecycle, RoleSource
from app.models.knowledge import CompanyPartner, PartnerRoleRow
from app.models.package import TenderPackageGap
from app.models.profile import CompanyService
from app.models.requirements import AwardCriterion
from app.models.tender_analysis import (
    AnalysisFinding,
    AnalysisQuestion,
    CapabilityNeedAssessment,
    CriterionAssessment,
    DeliveryCandidate,
    TenderAnalysisRun,
    TenderFitAssessment,
)
from app.models.tender_structure import TenderSite
from app.models.tenders import Tender, TenderDocument
from app.services import (
    company_knowledge,
    document_classification,
    document_roles,
    global_document_classification,
    manual_requirement,
    requirement_analysis,
    tender_lifecycle,
)
from app.services import tender_scope as scope
from app.services.knowledge_coverage import coverage_input
from app.tenders.visibility import visible_to_company

# --------------------------------------------------------------------------- #
# Vista del plec                                                                #
# --------------------------------------------------------------------------- #


def _site_key(site: TenderSite) -> str:
    return SiteView(code=site.code, name=site.name, role=str(site.role)).key


@dataclass(frozen=True, slots=True)
class PackageState:
    """L'estat del paquet documental d'un plec (3B-2 §4.4)."""

    status: str
    #: Nomes els forats OBERTS.
    gaps: tuple[PackageGapView, ...]
    #: Hi ha una passada de classificacio global vigent.
    has_run: bool


async def package_state(session: AsyncSession, tender_id: uuid.UUID) -> PackageState:
    """`incomplete` si hi ha algun forat obert (amb passada de classificacio global o
    sense: els forats son fets); sense forats, `complete` amb passada vigent i
    `unknown` sense. Els forats resolts no hi son mai."""
    run = await global_document_classification.current_global_run(session, tender_id)
    rows = await session.scalars(
        sa.select(TenderPackageGap)
        .where(TenderPackageGap.tender_id == tender_id, TenderPackageGap.resolved_at.is_(None))
        .order_by(TenderPackageGap.created_at, TenderPackageGap.id)
    )
    gaps = tuple(
        PackageGapView(
            code=str(gap.code),
            detail=gap.detail,
            would_provide=tuple(str(item) for item in (gap.would_provide or [])),
            producer=str(gap.producer),
        )
        for gap in rows
    )
    has_run = run is not None
    return PackageState(package_status_for(gaps, has_run=has_run), gaps, has_run)


async def load_structure_view(
    session: AsyncSession, tender: Tender, company_id: uuid.UUID | None = None
) -> tuple[TenderStructureView, dict[str, Any]]:
    """L'estructura vigent del plec com a vista pura, i les versions llegides.

    Els requisits i criteris son els de la run EFECTIVA de l'empresa (3B-2 §6.5:
    el seu punter o la canonica); sense empresa, la canonica global. Per aixo
    `extraction_run_id` (i l'`inputs_hash` v3) segueix el punter de cada empresa.

    La vista porta tambe l'estat del paquet documental (`package_status`, els forats
    oberts i `section_status`, 3B-2 §4.4); a l'empremta nomes hi entra quan hi ha
    forats (vegeu `TenderStructureView.canonical`).

    * Qualsevol forat obert fa el paquet `incomplete`, amb passada global o sense (una
      licitacio manual no en te mai: un forat declarat per l'operador hi val igual),
      tambe un `format_not_analyzable`: aquell document no el podem llegir. Sense
      forats: `complete` amb passada vigent, `unknown` sense.
    * Una seccio amb files es `present` encara que un forat la nomeni: les files son
      fets citats (la troballa del forat hi es igualment).
    * Una seccio buida es `unknown` si un forat la nomena, si no hi ha passada vigent,
      o si un forat amb impacte desconegut (`would_provide` buit) es d'un codi que pot
      afectar-ho tot (`referenced_document_missing`, `required_role_missing`,
      `no_text`). `format_not_analyzable` i `declared_by_operator` sense
      `would_provide` no deixen mai cap seccio `unknown`: nomes en surt la troballa.
    """
    await session.refresh(tender, attribute_names=["blocks", "sites", "lots"])
    lots = {lot.id: lot.external_lot_id for lot in tender.lots}
    blocks = {b.id: b for b in tender.blocks}
    sites = {s.id: s for s in tender.sites}

    effective = await manual_requirement.effective_requirements(session, tender.id, company_id)
    requirements = list(effective.all)
    criteria: list[AwardCriterion] = []
    if effective.run is not None:
        criteria = list(
            await session.scalars(
                sa.select(AwardCriterion)
                .where(AwardCriterion.run_id == effective.run.id)
                .order_by(AwardCriterion.created_at, AwardCriterion.id)
            )
        )
    criterion_key = {c.id: c.dedup_key for c in criteria}

    requirement_views: list[RequirementView] = []
    review_items: list[dict[str, Any]] = []
    for r in requirements:
        await session.refresh(
            r, attribute_names=["block_links", "territories", "capability_needs", "award_links"]
        )
        needs = tuple(
            NeedView(
                id=str(n.id),
                capability_code=n.capability_code,
                unmapped_capability=n.unmapped_capability,
                comparator=str(n.comparator),
                value_json=n.value_json,
                value_schema=n.value_schema,
                need_type=str(n.need_type),
                points=n.points,
                criterion_key=criterion_key.get(n.award_criterion_id),
            )
            for n in sorted(r.capability_needs, key=lambda n: (n.created_at, n.id))
        )
        requirement_views.append(
            RequirementView(
                id=str(r.id),
                key=r.dedup_key,
                title=r.title,
                category=str(r.category),
                obligation=str(r.obligation),
                timing=str(r.timing),
                block_codes=tuple(
                    blocks[link.block_id].code for link in r.block_links if link.block_id in blocks
                ),
                territory_match_mode=str(r.territory_match_mode),
                territory_completeness=str(r.territory_completeness),
                territories=tuple(
                    TerritoryView(
                        kind=str(t.kind),
                        label=t.label,
                        code=t.code,
                        code_system=t.code_system,
                        qualifier=t.qualifier,
                        site_key=_site_key(sites[t.site_id]) if t.site_id in sites else None,
                    )
                    for t in r.territories
                ),
                proof_json=r.proof_json,
                proof_schema=r.proof_schema,
                needs=needs,
                criterion_keys=tuple(
                    criterion_key[link.award_criterion_id]
                    for link in r.award_links
                    if link.award_criterion_id in criterion_key
                ),
                value_json=r.value_json,
            )
        )
        review_items.append(
            {
                "kind": "requirement",
                "key": r.dedup_key,
                "review_status": str(r.review_status),
                "manual_override": r.manual_override_json,
                "archived": r.archived_at is not None,
            }
        )

    criterion_views: list[CriterionView] = []
    for c in criteria:
        await session.refresh(c, attribute_names=["block_links"])
        criterion_views.append(
            CriterionView(
                id=str(c.id),
                key=c.dedup_key,
                name=c.name,
                points=c.points,
                criterion_type=str(c.criterion_type),
                evaluation_type=str(c.evaluation_type),
                timing=str(c.timing),
                block_codes=tuple(
                    blocks[link.block_id].code for link in c.block_links if link.block_id in blocks
                ),
                tranches=c.tranches,
                formula_text=c.formula_text,
            )
        )
        review_items.append(
            {
                "kind": "criterion",
                "key": c.dedup_key,
                "review_status": str(c.review_status),
                "manual_override": c.manual_override_json,
            }
        )

    items = await scope.scope_items_of(session, tender.id)
    facts = await scope.facts_of(session, tender.id)
    fact_blocks = await scope.fact_block_ids(session, [f.id for f in facts])

    view = TenderStructureView(
        tender_id=str(tender.id),
        duration_months=tender.duration_months,
        extension_months=tender.extension_months,
        regulated_harmonised=tender.regulated_harmonised,
        planned_start_date=tender.planned_start_date,
        submission_deadline=(
            tender.deadline_at.astimezone(UTC).date()
            if tender.deadline_at is not None and tender.deadline_at.tzinfo is not None
            else (tender.deadline_at.date() if tender.deadline_at is not None else None)
        ),
        blocks=tuple(
            BlockView(
                code=b.code,
                name=b.name,
                base_price_ex_vat=b.base_price_ex_vat,
                price_points=b.price_points,
                separately_awardable=b.separately_awardable,
                billing_terms_json=b.billing_terms_json,
                billing_schema=b.billing_schema,
                lot_key=lots.get(b.tender_lot_id),
            )
            for b in tender.blocks
        ),
        sites=tuple(
            SiteView(
                code=s.code,
                name=s.name,
                role=str(s.role),
                is_critical=s.is_critical,
                address=s.address,
                municipality_code=s.municipality_code,
                municipality_code_system=s.municipality_code_system,
                latitude=s.latitude,
                longitude=s.longitude,
            )
            for s in tender.sites
        ),
        scope_items=tuple(
            ScopeItemView(
                id=str(i.id),
                service_line_code=i.service_line_code,
                block_code=blocks[i.block_id].code if i.block_id in blocks else None,
                lot_key=lots.get(i.tender_lot_id),
                site_key=_site_key(sites[i.site_id]) if i.site_id in sites else None,
                quantity=i.quantity,
                unit=i.unit,
                specs_json=i.specs_json,
                specs_schema=i.specs_schema,
                unit_base_price_ex_vat=i.unit_base_price_ex_vat,
            )
            for i in items
        ),
        requirements=tuple(requirement_views),
        criteria=tuple(criterion_views),
        facts=tuple(
            FactView(
                fact_code=str(f.fact_code),
                value_json=f.value_json,
                value_schema=f.value_schema,
                block_codes=tuple(blocks[b].code for b in fact_blocks.get(f.id, []) if b in blocks),
            )
            for f in facts
        ),
    )
    package = await package_state(session, tender.id)
    view = replace(
        view,
        package_status=package.status,
        gaps=package.gaps,
        section_status=section_status_for(
            section_presence(view), package.gaps, has_run=package.has_run
        ),
    )
    revision = await tender_lifecycle.latest_analysis_affecting_revision(session, tender.id)
    versions = {
        "tender_revision_id": revision.id if revision else None,
        "extraction_run_id": effective.run.id if effective.run else None,
        "review_revision_hash": review_revision_hash(review_items),
    }
    return view, versions


# --------------------------------------------------------------------------- #
# Vista de l'empresa                                                            #
# --------------------------------------------------------------------------- #


def _coverage(row: Any) -> Coverage | None:
    """La cobertura d'una fila de coneixement, com a entrada del comparador.
    L'estat neutre (`unknown` sense arees) es `None`: no afirma res."""
    cov = coverage_input(row)
    if str(cov.completeness) == "unknown" and not cov.areas:
        return None
    return Coverage(
        completeness=str(cov.completeness),
        areas=tuple(
            Area(kind=str(a.kind), label=a.label, code=a.code, code_system=a.code_system)
            for a in cov.areas
        ),
    )


async def load_company_view(
    session: AsyncSession, company_id: uuid.UUID, *, on: date | None = None
) -> engine.CompanyView:
    snapshot = await company_knowledge.company_snapshot(session, company_id, on=on)
    services: list[engine.ServiceClaimView] = []
    for entry in snapshot.services.values():
        for claim in entry.claims:
            row = await session.get(CompanyService, uuid.UUID(claim.record_id))
            if row is None or not row.active or row.service_line_code is None:
                continue
            services.append(
                engine.ServiceClaimView(
                    service_id=str(row.id),
                    name=row.name,
                    service_line_code=row.service_line_code,
                    delivery_mode=str(row.delivery_mode) if row.delivery_mode else None,
                    source=claim.source.value,
                    coverage=_coverage(row),
                )
            )
    roles: list[engine.PartnerRoleView] = []
    live_partner_names = {
        name for name, entry in snapshot.partners.items() if entry.effective is not None
    }
    partner_rows = {
        p.id: p
        for p in await session.scalars(
            sa.select(CompanyPartner).where(
                CompanyPartner.company_id == company_id,
                CompanyPartner.lifecycle == KnowledgeLifecycle.CURRENT,
            )
        )
    }
    role_rows = await session.scalars(
        sa.select(PartnerRoleRow)
        .where(
            PartnerRoleRow.company_id == company_id,
            PartnerRoleRow.lifecycle == KnowledgeLifecycle.CURRENT,
        )
        .order_by(PartnerRoleRow.as_of, PartnerRoleRow.id)
    )
    for role in role_rows:
        partner = partner_rows.get(role.partner_id)
        if partner is None or partner.name not in live_partner_names:
            continue
        if role.valid_until is not None and on is not None and role.valid_until < on:
            continue
        roles.append(
            engine.PartnerRoleView(
                role_id=str(role.id),
                partner_id=str(partner.id),
                partner_name=partner.name,
                role=str(role.role),
                service_line_code=role.service_line_code,
                source="current_verified"
                if str(role.verification_status) == "verified"
                else "current_declared",
                coverage=_coverage(role),
            )
        )
    services.sort(key=lambda s: (s.service_line_code, s.name, s.service_id))
    roles.sort(key=lambda r: (r.service_line_code or "", r.partner_name, r.role, r.role_id))
    return engine.CompanyView(
        company_id=str(company_id),
        snapshot=snapshot,
        services=tuple(services),
        partner_roles=tuple(roles),
    )


# --------------------------------------------------------------------------- #
# L'entrada                                                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    run: TenderAnalysisRun
    reused: bool
    result: engine.AnalysisResult | None
    summary: dict[str, Any]


async def _visible_tender(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> Tender:
    tender = await session.scalar(
        sa.select(Tender).where(Tender.id == tender_id, visible_to_company(company_id))
    )
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")
    return tender


async def _classification_run_id(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> uuid.UUID | None:
    return await session.scalar(
        sa.select(TenderDocumentClassificationRun.id).where(
            TenderDocumentClassificationRun.company_id == company_id,
            TenderDocumentClassificationRun.tender_id == tender_id,
            TenderDocumentClassificationRun.is_current.is_(True),
        )
    )


async def _classification_inputs(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    *,
    divergences: tuple[engine.RoleDivergenceView, ...] | None = None,
) -> dict[str, str | None]:
    """Els dos inputs de classificacio de l'`inputs_hash` (3B-2 §3.2).

    La passada GLOBAL vigent (la mateixa per a totes les empreses) i el hash dels
    overrides de rol d'AQUESTA empresa, ancorats a la versio del fitxer. La
    passada tenant (`classification_run_id`) es continua desant a la run, pero ja
    no entra al hash.

    Si algun override diverteix del rol global (`ROLE_OVERRIDE_DIVERGES`), la
    divergencia tambe hi entra: una revisio de rol de l'operador es informativa (no
    canvia la revisio del plec ni la passada global) pero pot fer apareixer o
    desapareixer la troballa, i aleshores l'analisi ha de constar com a caducada.
    Sense divergencies el valor es exactament el de `role_overrides_hash`.
    `divergences`: les ja calculades per qui crida (`analyze_tender`), per no tornar a
    passar el resolutor; si no, es calculen aqui (`is_stale`), una sola vegada.
    """
    run = await global_document_classification.current_global_run(session, tender_id)
    overrides = await document_roles.role_overrides_hash(session, tender_id, company_id)
    if overrides is not None:
        if divergences is None:
            divergences = await role_divergences(session, tender_id, company_id)
        if divergences:
            overrides = sha256_of([overrides, [d.as_dict() for d in divergences]])
    return {
        "global_classification_run_id": str(run.id) if run else None,
        "role_overrides_hash": overrides,
    }


async def role_divergences(
    session: AsyncSession, tender_id: uuid.UUID, company_id: uuid.UUID
) -> tuple[engine.RoleDivergenceView, ...]:
    """Els documents analitzables on el rol efectiu de l'empresa (el seu override
    vigent) no es el global (3B-2 §3.2, opcio A).

    Es compara el rol; si la capa global nomes te el tipus del feed (`role=None`), el
    tipus efectiu. Un override d'una versio anterior (`stale_override`) no mana i no
    diverteix.
    """
    mine = await document_roles.effective_document_roles(session, tender_id, company_id)
    overridden = [d for d, role in mine.items() if role.source == RoleSource.PERSON]
    if not overridden:
        return ()
    shared = await document_roles.effective_document_roles(session, tender_id, None)
    names = dict(
        (
            await session.execute(
                sa.select(TenderDocument.id, TenderDocument.name).where(
                    TenderDocument.id.in_(overridden)
                )
            )
        ).all()
    )
    divergences: list[engine.RoleDivergenceView] = []
    for document_id in overridden:
        company_role, global_role = mine[document_id], shared.get(document_id)
        if global_role is None:
            continue
        if global_role.role is not None:
            diverges = company_role.role != global_role.role
        else:
            diverges = company_role.document_type != global_role.document_type
        if not diverges:
            continue
        divergences.append(
            engine.RoleDivergenceView(
                document_id=str(document_id),
                document_name=names.get(document_id) or "",
                company_role=str(company_role.role) if company_role.role else None,
                company_document_type=str(company_role.document_type),
                global_role=str(global_role.role) if global_role.role else None,
                global_document_type=str(global_role.document_type),
                global_source=str(global_role.source),
            )
        )
    return tuple(sorted(divergences, key=lambda d: (d.document_name, d.document_id)))


#: Codis tancats de `details["missing"]` (mateix contracte que `requirements` del router).
MISSING_REQUIREMENTS_OUTDATED = "requirements_outdated"
MISSING_CLASSIFICATION_OUTDATED = "classification_outdated"


async def assert_inputs_are_current(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> None:
    """Cap analisi nova sobre una run de requisits obsoleta (3B-2 §6.2).

    La run d'extraccio EFECTIVA de l'empresa (el seu punter o la canonica) i la de
    classificacio son la base de la vista del plec. La de requisits es obsoleta si
    el corpus canonic d'ara no es el que va llegir (una re-extraccio amb bytes
    nous, un document nou o retirat) o si va llegir o cita un document que ja no
    es a la publicacio vigent (`requirement_analysis.run_is_outdated`). Analitzar-la
    ara faria una analisi NOVA sobre uns plecs que ja no son aquells. Es refusa amb
    el mateix error de prerequisits que ja coneix la interficie; el remei es el
    flux normal: tornar a analitzar els plecs. Un canvi de paquet mai re-executa
    res per si sol.
    """
    missing: list[str] = []
    effective_run = await manual_requirement.effective_run(session, tender_id, company_id)
    if effective_run is not None and await requirement_analysis.run_is_outdated(
        session, effective_run
    ):
        missing.append(MISSING_REQUIREMENTS_OUTDATED)
    classification = await document_classification.current_run(session, company_id, tender_id)
    if classification is not None and await document_classification.run_includes_removed_document(
        session, classification
    ):
        missing.append(MISSING_CLASSIFICATION_OUTDATED)
    if missing:
        raise AnalysisPrerequisitesMissingError(
            "Els requisits vigents son d'uns plecs que han canviat o d'un document "
            "que ja no es a la font: cal tornar a processar els plecs.",
            details={"missing": missing},
        )


async def current_run(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> TenderAnalysisRun | None:
    return await session.scalar(
        sa.select(TenderAnalysisRun).where(
            TenderAnalysisRun.company_id == company_id,
            TenderAnalysisRun.tender_id == tender_id,
            TenderAnalysisRun.is_current.is_(True),
        )
    )


async def _make_current(session: AsyncSession, run: TenderAnalysisRun) -> None:
    """L'unica mutacio permesa: el punter. Primer cap, despres aquesta."""
    await session.execute(
        sa.update(TenderAnalysisRun)
        .where(
            TenderAnalysisRun.company_id == run.company_id,
            TenderAnalysisRun.tender_id == run.tender_id,
            TenderAnalysisRun.is_current.is_(True),
            TenderAnalysisRun.id != run.id,
        )
        .values(is_current=False)
    )
    await session.flush()
    if not run.is_current:
        run.is_current = True
        await session.flush()


def _persist(run: TenderAnalysisRun, result: engine.AnalysisResult) -> None:
    j = engine.as_json
    for a in result.fit_assessments:
        run.fit_assessments.append(
            TenderFitAssessment(
                requirement_id=uuid.UUID(a.requirement_id),
                fit_result=a.fit_result,
                knowledge_source=a.knowledge_source,
                proof_status=a.proof_status,
                territory_verdict=a.territory_verdict,
                verification_status=a.verification_status,
                rule_code=a.rule_code,
                explanation_code=a.explanation_code,
                support_json=j(a.support),
            )
        )
    for n in result.need_assessments:
        run.need_assessments.append(
            CapabilityNeedAssessment(
                capability_need_id=uuid.UUID(n.need_id),
                fit_result=n.fit_result,
                knowledge_source=n.knowledge_source,
                verification_status=n.verification_status,
                rule_code=n.rule_code,
                explanation_code=n.explanation_code,
                support_json=j(n.support),
            )
        )
    for c in result.criterion_assessments:
        run.criterion_assessments.append(
            CriterionAssessment(
                award_criterion_id=uuid.UUID(c.criterion_id),
                criterion_result=c.criterion_result,
                knowledge_source=c.knowledge_source,
                proof_status=c.proof_status,
                points_status=c.points_status,
                max_points=c.max_points,
                secured_points=c.secured_points,
                at_risk_points=c.at_risk_points,
                lost_points=c.lost_points,
                not_assessable_points=c.not_assessable_points,
                rule_code=c.rule_code,
                explanation_code=c.explanation_code,
                support_json=j(c.support),
            )
        )
    for d in result.candidates:
        run.delivery_candidates.append(
            DeliveryCandidate(
                scope_item_id=uuid.UUID(d.scope_item_id),
                company_service_id=uuid.UUID(d.company_service_id)
                if d.company_service_id
                else None,
                partner_role_id=uuid.UUID(d.partner_role_id) if d.partner_role_id else None,
                candidate_type=d.candidate_type,
                origin=d.origin,
                readiness=d.readiness,
                territory_verdict=d.territory_verdict,
                area_verdicts_json=j(d.area_verdicts),
                rule_code=d.rule_code,
                explanation_code=d.explanation_code,
            )
        )
    for f in result.findings:
        run.findings.append(
            AnalysisFinding(
                finding_type=f.finding_type,
                rule_code=f.rule_code,
                explanation_code=f.explanation_code,
                requirement_id=uuid.UUID(f.requirement_id) if f.requirement_id else None,
                criterion_id=uuid.UUID(f.criterion_id) if f.criterion_id else None,
                scope_item_id=uuid.UUID(f.scope_item_id) if f.scope_item_id else None,
                details_json=j(f.details),
            )
        )
    for q in result.questions:
        run.questions.append(
            AnalysisQuestion(
                audience=q.audience,
                question_code=q.question_code,
                rule_code=q.rule_code,
                requirement_id=uuid.UUID(q.requirement_id) if q.requirement_id else None,
                criterion_id=uuid.UUID(q.criterion_id) if q.criterion_id else None,
                scope_item_id=uuid.UUID(q.scope_item_id) if q.scope_item_id else None,
                params_json=j(q.params),
                dedupe_key=q.dedupe_key,
            )
        )


async def analyze_tender(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    on: date | None = None,
    persist_hook=None,
    accept_incomplete_package: bool = False,
) -> AnalysisOutcome:
    """Executa (o reutilitza) l'analisi i la deixa vigent. No fa commit.

    `persist_hook` nomes serveix als tests per provocar un error enmig de la
    persistencia i comprovar que no queda res a mitges.

    Refusa (`AnalysisPrerequisitesMissingError`) si la base vigent va llegir un
    plec ja retirat de la font: vegeu `assert_inputs_are_current`.

    Una run nova registra `accepted_incomplete_package`: si el paquet consta com a
    incomplet, si qui l'ha demanada ho ha acceptat explicitament (cert o fals); si
    no, `None`. Una run REUTILITZADA (mateix `inputs_hash`) conserva el valor amb que
    es va crear: una acceptacio posterior sobre les mateixes entrades no el canvia (no
    es «la darrera eleccio de l'usuari», sino la de qui la va crear).
    """
    tender = await _visible_tender(session, company_id, tender_id)
    await assert_inputs_are_current(session, company_id, tender_id)
    view, versions = await load_structure_view(session, tender, company_id)
    fingerprint = structure_fingerprint(view)
    company = await load_company_view(session, company_id, on=on)
    # Una sola passada del resolutor: el motor i l'`inputs_hash` en fan servir la mateixa.
    divergences = await role_divergences(session, tender_id, company_id)
    company = replace(company, role_divergences=divergences)
    classification_run_id = await _classification_run_id(session, company_id, tender_id)
    digest = inputs_hash(
        tender_revision_id=str(versions["tender_revision_id"])
        if versions["tender_revision_id"]
        else None,
        extraction_run_id=str(versions["extraction_run_id"])
        if versions["extraction_run_id"]
        else None,
        **await _classification_inputs(session, company_id, tender_id, divergences=divergences),
        review_revision_hash=versions["review_revision_hash"],
        tender_structure_fingerprint=fingerprint,
        knowledge_fingerprint=company.knowledge_fingerprint,
        rules_version=RULES_VERSION,
    )

    existing = await session.scalar(
        sa.select(TenderAnalysisRun).where(
            TenderAnalysisRun.company_id == company_id,
            TenderAnalysisRun.tender_id == tender_id,
            TenderAnalysisRun.inputs_hash == digest,
        )
    )
    if existing is not None:
        await _make_current(session, existing)
        await session.refresh(
            existing,
            attribute_names=[
                "fit_assessments",
                "need_assessments",
                "criterion_assessments",
                "delivery_candidates",
                "findings",
                "questions",
            ],
        )
        return AnalysisOutcome(existing, True, None, summary_of(existing))

    result = engine.analyze(view, company)
    async with session.begin_nested():
        run = TenderAnalysisRun(
            company_id=company_id,
            tender_id=tender_id,
            tender_revision_id=versions["tender_revision_id"],
            extraction_run_id=versions["extraction_run_id"],
            classification_run_id=classification_run_id,
            review_revision_hash=versions["review_revision_hash"],
            tender_structure_fingerprint=fingerprint,
            knowledge_fingerprint=company.knowledge_fingerprint,
            rules_version=RULES_VERSION,
            inputs_hash=digest,
            accepted_incomplete_package=(
                bool(accept_incomplete_package)
                if view.package_status == PACKAGE_INCOMPLETE
                else None
            ),
            is_current=False,
        )
        session.add(run)
        _persist(run, result)
        if persist_hook is not None:
            persist_hook(run)
        await session.flush()
        await _make_current(session, run)
    await session.refresh(
        run,
        attribute_names=[
            "fit_assessments",
            "need_assessments",
            "criterion_assessments",
            "delivery_candidates",
            "findings",
            "questions",
        ],
    )
    return AnalysisOutcome(run, False, result, summary_of(run))


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


def summary_of(run: TenderAnalysisRun) -> dict[str, Any]:
    """Els vectors factuals d'una run persistida. Cap puntuacio global."""
    fits = run.fit_assessments
    crits = run.criterion_assessments
    findings = run.findings
    cands = run.delivery_candidates
    pending = ("pending_coverage", "pending_partner", "pending_validation")
    return {
        # 3B-2 §4.4: el paquet documental tenia algun forat obert quan es va analitzar.
        "package_incomplete": any(
            f.explanation_code == ExplanationCode.PACKAGE_GAP for f in findings
        ),
        "fit": {
            value: sum(1 for f in fits if f.fit_result == value)
            for value in ("satisfies", "does_not_satisfy", "unknown", "not_applicable")
        },
        "blocker_potential_count": sum(
            1 for f in findings if f.finding_type == "blocker_potential"
        ),
        "review_required_count": sum(1 for f in findings if f.finding_type == "review_required"),
        "secured_points": round(sum(c.secured_points for c in crits), 6),
        "at_risk_points": round(sum(c.at_risk_points for c in crits), 6),
        "lost_points": round(sum(c.lost_points for c in crits), 6),
        "not_assessable_points": round(sum(c.not_assessable_points for c in crits), 6),
        "max_points": round(sum(c.max_points for c in crits), 6),
        "delivery": {
            "available": sum(1 for c in cands if c.readiness == "available"),
            "pending": sum(1 for c in cands if c.readiness in pending),
            "unavailable": sum(1 for c in cands if c.readiness == "unavailable"),
        },
        "questions": {
            "buyer": sum(1 for q in run.questions if q.audience == "buyer"),
            "company": sum(1 for q in run.questions if q.audience == "company"),
        },
    }


async def load_run(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID, run_id: uuid.UUID
) -> TenderAnalysisRun:
    run = await session.scalar(
        sa.select(TenderAnalysisRun).where(
            TenderAnalysisRun.id == run_id,
            TenderAnalysisRun.company_id == company_id,
            TenderAnalysisRun.tender_id == tender_id,
        )
    )
    if run is None:
        raise NotFoundError("Analisi no trobada.")
    return run


async def is_stale(
    session: AsyncSession, run: TenderAnalysisRun, *, on: date | None = None
) -> bool:
    """Cert si els inputs d'ara donarien un `inputs_hash` diferent."""
    tender = await session.get(Tender, run.tender_id)
    assert tender is not None
    view, versions = await load_structure_view(session, tender, run.company_id)
    company = await load_company_view(session, run.company_id, on=on)
    digest = inputs_hash(
        tender_revision_id=str(versions["tender_revision_id"])
        if versions["tender_revision_id"]
        else None,
        extraction_run_id=str(versions["extraction_run_id"])
        if versions["extraction_run_id"]
        else None,
        **await _classification_inputs(session, run.company_id, run.tender_id),
        review_revision_hash=versions["review_revision_hash"],
        tender_structure_fingerprint=structure_fingerprint(view),
        knowledge_fingerprint=company.knowledge_fingerprint,
        rules_version=RULES_VERSION,
    )
    return digest != run.inputs_hash


@dataclass(frozen=True)
class AnalysisStatus:
    """L'analisi vigent d'un plec i si esta caducada: la unica font de (run, stale)."""

    run: TenderAnalysisRun | None
    stale: bool


async def current_analysis(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    *,
    on: date | None = None,
) -> AnalysisStatus:
    run = await current_run(session, company_id, tender_id)
    if run is None:
        return AnalysisStatus(run=None, stale=False)
    return AnalysisStatus(run=run, stale=await is_stale(session, run, on=on))


__all__ = [
    "AnalysisOutcome",
    "AnalysisStatus",
    "PackageState",
    "analyze_tender",
    "current_analysis",
    "current_run",
    "is_stale",
    "load_company_view",
    "load_run",
    "load_structure_view",
    "package_state",
    "role_divergences",
    "summary_of",
]
