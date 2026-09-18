"""El manifest d'una candidatura: que presentariem i sobre que es va decidir.

Fins a la v1 el paquet nomes congelava el dossier documental. Des de la v2 tambe
hi entren la solucio tecnica i l'oferta economica, que es el que realment
s'ofereix, i la base humana sobre la qual algu ho aprova.

**Aquest modul no calcula res.** El dossier el descriu `workspace_dossier`, els
imports els dona el mateix `build_view()` que veu l'API de l'oferta, i l'abast
tecnic el resol `technical_scope`. Si el manifest tingues les seves propies
formules, congelar i comparar podrien discrepar i el hash deixaria de voler dir
res.

**Dues meitats amb intencions diferents.** `approval_basis` es sobre QUE es va
decidir: les empremtes de les confirmacions i validacions, l'abast revisat i els
calculs vigents. `content` es QUE es presentaria. Les dues van al mateix hash
—un paquet obsolet ho es igual per un motiu que per l'altre— pero separades es
pot llegir per que ho es.

**El cost intern no hi entra mai.** El manifest congelat es descarregable per
qualsevol membre de l'empresa, `technical` i `viewer` inclosos: posar-hi el cost
obriria per aquesta porta exactament el que C-6 tanca a l'API de l'oferta.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.enums import RequirementCategory, TechnicalSolutionSource
from app.models.workspace import BidWorkspace
from app.services import bid_workspace as workspace_service
from app.services import economic_offer as offer_service
from app.services import technical_scope as scope_service
from app.services import technical_solution as solution_service
from app.services import workspace_dossier as dossier_service
from app.workspace.manifest import (
    ManifestAmounts,
    ManifestDocument,
    ManifestInput,
    ManifestOffer,
    ManifestOfferLine,
    ManifestOfferLot,
    ManifestOfferSection,
    ManifestRelevance,
    ManifestSolution,
    ManifestTechnicalValidation,
    build_manifest,
)


async def current_manifest(
    session: AsyncSession, workspace: BidWorkspace, *, revision_number: int
) -> tuple[list[Any], dict, str]:
    """El manifest del que es presentaria ARA, amb el seu hash.

    Retorna tambe les files del dossier triades perque qui congela les necessita
    per crear les peces del paquet i qui compara les necessita per mirar-ne la
    vigencia documental.
    """
    state = await workspace_service.load_state(session, workspace)
    dossier = await dossier_service.load_dossier(session, workspace)
    rows = dossier_service.selected_rows(dossier)

    documents = tuple(
        ManifestDocument(
            position=position,
            document_id=row.document.id,
            filename=row.document.name,
            sha256=row.document.sha256,
            file_size=row.document.file_size,
            mime_type=row.document.mime_type,
            category=(
                row.requirement.category.value
                if row.requirement
                else RequirementCategory.OTHER.value
            ),
            requirement_id=row.item.requirement_id,
            requirement_title=(
                row.checklist_item.title
                if row.checklist_item
                else (row.requirement.title if row.requirement else None)
            ),
            expires_at=row.document.expires_at,
        )
        for position, row in enumerate(rows)
    )

    # C-18: sobre que es va confirmar que aquests papers acrediten el que
    # demana el plec. Mateix ordre que els documents: ja es determinista.
    relevance = tuple(
        ManifestRelevance(
            evidence_item_id=row.item.id,
            requirement_id=row.item.requirement_id,
            relevance=str(row.relevance),
            relevance_document_id=row.item.relevance_document_id,
            relevance_requirement_fingerprint=row.item.relevance_requirement_fingerprint,
        )
        for row in rows
    )

    solutions, validations, scope_fingerprint = await _technical(session, workspace)
    offer = await _economic(session, workspace)

    payload, digest = build_manifest(
        ManifestInput(
            revision_number=revision_number,
            workspace_id=workspace.id,
            company_id=workspace.company_id,
            tender_id=workspace.tender_id,
            tender_lot_id=workspace.tender_lot_id,
            expediente_number=state.tender.expediente_number,
            deadline_at=state.deadline_at,
            requirement_run_id=state.requirement_run.id if state.requirement_run else None,
            compliance_run_id=state.compliance_run.id if state.compliance_run else None,
            prefit_score=state.prefit_score,
            mandatory_total=dossier.counters.mandatory_total,
            mandatory_covered=dossier.counters.covered,
            documents=documents,
            relevance=relevance,
            technical_scope_fingerprint=scope_fingerprint,
            technical_validations=validations,
            solutions=solutions,
            offer=offer,
        )
    )
    return rows, payload, digest


async def _technical(
    session: AsyncSession, workspace: BidWorkspace
) -> tuple[
    tuple[ManifestSolution, ...], tuple[ManifestTechnicalValidation, ...], str | None
]:
    """Les solucions que s'oferirien i la base factual de la seva validacio.

    Hi entren les manuals i les que resolen un requisit **dins de l'abast
    actual** (C-16). Una fila que resolia un requisit que ja no aplica es
    historia: no s'ofereix i no ha de moure el hash. Una que va perdre el
    requisit (`SET NULL`) tampoc, pel mateix motiu.

    Ordre per identificador: `WorkspaceTechnicalSolution` no te posicio i
    inventar-ne una seria donar per bo un ordre que ningu no ha decidit.
    """
    scope = await scope_service.resolve(session, workspace)
    in_scope = {requirement.id for requirement in scope.requirements}
    fingerprint = await scope_service.scope_fingerprint(session, workspace, scope)

    triades = [
        solution
        for solution in await solution_service.list_solutions(session, workspace)
        if solution.source != TechnicalSolutionSource.REQUIREMENT
        or (solution.requirement_id is not None and solution.requirement_id in in_scope)
    ]
    triades.sort(key=lambda solution: str(solution.id))

    solutions = tuple(
        ManifestSolution(
            solution_id=solution.id,
            requirement_id=solution.requirement_id,
            title=solution.title,
            proposed_solution=solution.proposed_solution,
            supplier_name=solution.supplier_name,
            product_name=solution.product_name,
        )
        for solution in triades
    )
    validations = tuple(
        ManifestTechnicalValidation(
            solution_id=solution.id,
            source=str(solution.source),
            requirement_id=solution.requirement_id,
            status=str(solution.status),
            validated_requirement_fingerprint=solution.validated_requirement_fingerprint,
            validated_content_fingerprint=solution.validated_content_fingerprint,
        )
        for solution in triades
    )
    return solutions, validations, fingerprint


def _amounts(totals) -> ManifestAmounts:
    """Nomes el que es presenta. Cost i marge no passen d'aqui."""
    return ManifestAmounts(
        revenue=totals.total_revenue,
        vat_amount=totals.vat_amount,
        total_with_vat=totals.total_with_vat,
    )


async def _economic(
    session: AsyncSession, workspace: BidWorkspace
) -> ManifestOffer | None:
    """L'oferta activa, amb els imports que ja calcula el motor de preus.

    Nomes files actives i en l'ordre canonic d'S5 (posicio, i despres data i
    identificador): l'ordre forma part del document que s'ofereix, i no pot
    dependre de com la base retorni les files.
    """
    try:
        offer = await offer_service.get_offer(session, workspace)
    except NotFoundError:
        return None

    view = await offer_service.build_view(session, workspace, offer)
    lots = tuple(
        ManifestOfferLot(
            lot_id=lot_view.lot.id,
            tender_lot_id=lot_view.lot.tender_lot_id,
            label=lot_view.lot.label,
            duration_months=lot_view.lot.duration_months,
            position=lot_view.lot.position,
            sections=tuple(
                ManifestOfferSection(
                    section_id=section_view.section.id,
                    title=section_view.section.title,
                    position=section_view.section.position,
                    lines=tuple(
                        ManifestOfferLine(
                            line_id=line_view.line.id,
                            position=line_view.line.position,
                            concept=line_view.line.concept,
                            quantity=line_view.line.quantity,
                            pricing_basis=str(line_view.line.pricing_basis),
                            duration_months_override=line_view.line.duration_months_override,
                            effective_duration_months=line_view.effective_duration_months,
                            periods=line_view.totals.periods,
                            offered_unit_price_ex_vat=line_view.line.offered_unit_price_ex_vat,
                            vat_rate=line_view.line.vat_rate,
                            supplier_name=line_view.line.supplier_name,
                            product_name=line_view.line.product_name,
                            requirement_id=line_view.line.requirement_id,
                            technical_solution_id=line_view.line.technical_solution_id,
                            amounts=_amounts(line_view.totals),
                        )
                        for line_view in offer_service.active_views(section_view.lines)
                    ),
                    amounts=_amounts(section_view.totals),
                )
                for section_view in offer_service.active_views(lot_view.sections)
            ),
            amounts=_amounts(lot_view.totals),
        )
        for lot_view in offer_service.active_views(view.lots)
    )
    return ManifestOffer(
        currency=offer.currency, lots=lots, amounts=_amounts(view.totals)
    )
