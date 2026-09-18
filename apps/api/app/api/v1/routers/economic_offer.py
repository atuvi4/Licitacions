"""Oferta economica d'una candidatura: HTTP.

Tres coses passen aqui i enlloc mes:

**La redaccio.** Qui no pot veure cost ni marge (C-6) no rep un `null` on hi
hauria d'anar el numero: rep l'absencia del bloc `internal_economics`. Es la
frontera que fa distingible "no t'ho diem" de "encara no se sap".

**La resposta sencera.** Tota mutacio retorna l'arbre complet, com fa el dossier:
canviar una linia canvia els totals de la seva seccio, del seu lot i de l'oferta,
i tornar nomes la fila obligaria la pantalla a recalcular pel seu compte allo que
justament no ha de calcular ningu mes que S1.

**`expected_revision`.** Tota mutacio posterior a la creacio el porta al cos, i
el servei el comprova amb un compare-and-swap atomic.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.api.deps import CompanyCtx, SessionDep
from app.models.enums import AuditAction
from app.schemas.offer import (
    EconomicOfferCreateResult,
    EconomicOfferRead,
    EconomicOfferUpdateRequest,
    InternalTotalsRead,
    LineCreateRequest,
    LineInternalRead,
    LineRead,
    LineUpdateRequest,
    LotFromTenderLotRequest,
    LotLimitRead,
    LotRead,
    LotUpdateRequest,
    RevisionGuarded,
    SectionCreateRequest,
    SectionRead,
    SectionUpdateRequest,
    TotalsRead,
)
from app.services import audit
from app.services import bid_workspace as workspace_service
from app.services import economic_offer as service

router = APIRouter(prefix="/workspaces/{workspace_id}/economic-offer", tags=["economic-offer"])


# --------------------------------------------------------------------------- #
# Presentacio                                                                   #
# --------------------------------------------------------------------------- #


def _totals(totals) -> TotalsRead:
    return TotalsRead(
        total_revenue=totals.total_revenue,
        vat_amount=totals.vat_amount,
        total_with_vat=totals.total_with_vat,
        line_count=totals.line_count,
        incomplete_revenue_lines=totals.incomplete_revenue_lines,
        incomplete_vat_lines=totals.incomplete_vat_lines,
    )


def _internal(totals) -> InternalTotalsRead:
    return InternalTotalsRead(
        total_cost=totals.total_cost,
        margin_amount=totals.margin_amount,
        margin_on_sales_pct=totals.margin_on_sales_pct,
        markup_on_cost_pct=totals.markup_on_cost_pct,
        incomplete_cost_lines=totals.incomplete_cost_lines,
    )


def _line_read(view, *, internal: bool) -> LineRead:
    """El cost unitari viu DINS del bloc intern, no al cos de la linia.

    Si fos un camp mes de la fila, amagar-lo el convertiria en un `null`
    indistingible d'un cost que ningu no ha escrit encara.
    """
    return LineRead(
        **{
            camp: getattr(view.line, camp)
            for camp in (
                "id",
                "section_id",
                "concept",
                "requirement_id",
                "technical_solution_id",
                "supplier_name",
                "product_name",
                "quantity",
                "pricing_basis",
                "duration_months_override",
                "offered_unit_price_ex_vat",
                "tender_max_unit_price_ex_vat",
                "vat_rate",
                "notes",
                "position",
                "archived_at",
                "created_by",
                "updated_by",
                "created_at",
                "updated_at",
            )
        },
        effective_duration_months=view.effective_duration_months,
        periods=view.totals.periods,
        monthly_equivalent_revenue=view.totals.monthly_equivalent_revenue,
        exceeds_unit_max=view.totals.exceeds_unit_max,
        totals=_totals(view.totals),
        internal_economics=(
            LineInternalRead(
                **_internal(view.totals).model_dump(),
                unit_cost_ex_vat=view.line.unit_cost_ex_vat,
                monthly_equivalent_cost=view.totals.monthly_equivalent_cost,
            )
            if internal
            else None
        ),
    )


def _offer_read(view, *, internal: bool, can_select_lots: bool) -> EconomicOfferRead:
    return EconomicOfferRead(
        **{
            camp: getattr(view.offer, camp)
            for camp in (
                "id",
                "workspace_id",
                "currency",
                "notes",
                "revision",
                "created_by",
                "updated_by",
                "created_at",
                "updated_at",
            )
        },
        can_view_internal_economics=internal,
        can_select_lots=can_select_lots,
        totals=_totals(view.totals),
        internal_economics=_internal(view.totals) if internal else None,
        lots=[
            LotRead(
                **{
                    camp: getattr(lot_view.lot, camp)
                    for camp in (
                        "id",
                        "tender_lot_id",
                        "label",
                        "duration_months",
                        "position",
                        "archived_at",
                        "created_by",
                        "updated_by",
                        "created_at",
                        "updated_at",
                    )
                },
                totals=_totals(lot_view.totals),
                internal_economics=_internal(lot_view.totals) if internal else None,
                limit=LotLimitRead(
                    tender_max=lot_view.limit.tender_max,
                    difference_to_maximum=lot_view.limit.difference_to_maximum,
                    exceeds_lot_max=lot_view.limit.exceeds_lot_max,
                ),
                sections=[
                    SectionRead(
                        **{
                            camp: getattr(section_view.section, camp)
                            for camp in (
                                "id",
                                "offer_lot_id",
                                "title",
                                "position",
                                "archived_at",
                                "created_by",
                                "updated_by",
                                "created_at",
                                "updated_at",
                            )
                        },
                        totals=_totals(section_view.totals),
                        internal_economics=(
                            _internal(section_view.totals) if internal else None
                        ),
                        lines=[
                            _line_read(line_view, internal=internal)
                            for line_view in section_view.lines
                        ],
                    )
                    for section_view in lot_view.sections
                ],
            )
            for lot_view in view.lots
        ],
    )


async def _read(session: SessionDep, ctx: CompanyCtx, workspace, offer) -> EconomicOfferRead:
    # Es torna a carregar l'arbre despres de cada mutacio: els totals de tots els
    # nivells depenen de la fila que s'acaba de tocar.
    fresh = await service.get_offer(session, workspace)
    view = await service.build_view(session, workspace, fresh)
    return _offer_read(
        view,
        internal=service.can_view_internal_economics(ctx.membership.role),
        can_select_lots=view.shape.selectable,
    )


# --------------------------------------------------------------------------- #
# Arrel                                                                         #
# --------------------------------------------------------------------------- #


@router.get("", response_model=EconomicOfferRead, summary="Oferta economica")
async def get_offer(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Lectura per a qualsevol membre; els numeros de dins, nomes per a qui pot."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    return await _read(session, ctx, workspace, offer)


@router.post("", response_model=EconomicOfferCreateResult, summary="Obre l'oferta economica")
async def create_offer(workspace_id: uuid.UUID, session: SessionDep, ctx: CompanyCtx):
    """Idempotent: si ja hi havia oferta, s'obre la que hi era (`created=false`).

    Els lots inicials els determina C-1, i els posa el servidor.
    """
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    service.assert_can_edit(ctx.membership.role)
    offer, created = await service.create_offer(session, workspace, user=ctx.user)
    if created:
        await audit.record(
            session,
            company_id=ctx.company_id,
            user_id=ctx.user.id,
            action=AuditAction.CREATE,
            entity_type="economic_offer",
            entity_id=offer.id,
            summary=service.audit_summary(
                oferta=offer.id, workspace=workspace.id, revisio=offer.revision
            ),
        )
        await session.commit()
    return EconomicOfferCreateResult(
        created=created, offer=await _read(session, ctx, workspace, offer)
    )


@router.patch("", response_model=EconomicOfferRead, summary="Moneda i notes de l'oferta")
async def update_offer(
    workspace_id: uuid.UUID,
    payload: EconomicOfferUpdateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    fields = payload.model_dump(exclude_unset=True)
    offer, changed = await service.update_offer(
        session,
        offer,
        currency=fields.get("currency", service.UNSET),
        notes=fields.get("notes", service.UNSET),
        expected_revision=payload.expected_revision,
        user=ctx.user,
    )
    if changed:
        await _audit(session, ctx, AuditAction.UPDATE, "economic_offer", offer, offer)
    return await _read(session, ctx, workspace, offer)


# --------------------------------------------------------------------------- #
# Lots                                                                          #
# --------------------------------------------------------------------------- #


@router.post(
    "/lots/from-tender-lot",
    response_model=EconomicOfferCreateResult,
    summary="Presenta't a un lot del plec",
)
async def add_lot(
    workspace_id: uuid.UUID,
    payload: LotFromTenderLotRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes quan la candidatura es global i el plec te lots (C-1, cas C)."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    lot, created = await service.add_lot_from_tender_lot(
        session,
        workspace,
        offer,
        tender_lot_id=payload.tender_lot_id,
        expected_revision=payload.expected_revision,
        user=ctx.user,
    )
    if created:
        await _audit(session, ctx, AuditAction.CREATE, "economic_offer_lot", offer, lot)
    return EconomicOfferCreateResult(
        created=created, offer=await _read(session, ctx, workspace, offer)
    )


@router.patch(
    "/lots/{lot_id}", response_model=EconomicOfferRead, summary="Durada del lot"
)
async def update_lot(
    workspace_id: uuid.UUID,
    lot_id: uuid.UUID,
    payload: LotUpdateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Nomes `duration_months`, i val per als tres casos de C-1.

    Ometre el camp no el toca; enviar-hi `null` treu la durada. La diferencia
    importa: sense durada, les linies mensuals i anuals tornen a no tenir totals,
    que es exactament el que ha de passar quan ningu no sap quant dura (C-3).
    """
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    lot = await service.get_lot(session, offer, lot_id)
    fields = payload.model_dump(exclude_unset=True)
    lot, changed = await service.update_lot(
        session,
        offer,
        lot,
        duration_months=fields.get("duration_months", service.UNSET),
        expected_revision=payload.expected_revision,
        user=ctx.user,
    )
    if changed:
        await _audit(session, ctx, AuditAction.UPDATE, "economic_offer_lot", offer, lot)
    return await _read(session, ctx, workspace, offer)


@router.post(
    "/lots/{lot_id}/archive", response_model=EconomicOfferRead, summary="Arxiva un lot"
)
async def archive_lot(
    workspace_id: uuid.UUID,
    lot_id: uuid.UUID,
    payload: RevisionGuarded,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Arxivar no esborra (C-9): la fila es queda amb el que es va oferir.

    Nomes al cas C: quan la candidatura determina l'unic lot, treure'l deixaria
    una oferta sense cap bloc de preus.
    """
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    lot = await service.get_lot(session, offer, lot_id)
    lot = await service.archive_lot(
        session,
        workspace,
        offer,
        lot,
        expected_revision=payload.expected_revision,
        user=ctx.user,
    )
    await _audit(session, ctx, AuditAction.ARCHIVE, "economic_offer_lot", offer, lot)
    return await _read(session, ctx, workspace, offer)


@router.post(
    "/lots/{lot_id}/sections", response_model=EconomicOfferRead, summary="Afegeix una seccio"
)
async def create_section(
    workspace_id: uuid.UUID,
    lot_id: uuid.UUID,
    payload: SectionCreateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    lot = await service.get_lot(session, offer, lot_id)
    section = await service.create_section(
        session,
        offer,
        lot,
        title=payload.title,
        expected_revision=payload.expected_revision,
        user=ctx.user,
    )
    await _audit(session, ctx, AuditAction.CREATE, "economic_offer_section", offer, section)
    return await _read(session, ctx, workspace, offer)


@router.patch(
    "/sections/{section_id}", response_model=EconomicOfferRead, summary="Edita una seccio"
)
async def update_section(
    workspace_id: uuid.UUID,
    section_id: uuid.UUID,
    payload: SectionUpdateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    section = await service.get_section(session, offer, section_id)
    fields = payload.model_dump(exclude_unset=True)
    section, changed = await service.update_section(
        session,
        offer,
        section,
        title=fields.get("title", service.UNSET),
        position=fields.get("position", service.UNSET),
        expected_revision=payload.expected_revision,
        user=ctx.user,
    )
    if changed:
        await _audit(
            session, ctx, AuditAction.UPDATE, "economic_offer_section", offer, section
        )
    return await _read(session, ctx, workspace, offer)


@router.post(
    "/sections/{section_id}/archive",
    response_model=EconomicOfferRead,
    summary="Arxiva una seccio",
)
async def archive_section(
    workspace_id: uuid.UUID,
    section_id: uuid.UUID,
    payload: RevisionGuarded,
    session: SessionDep,
    ctx: CompanyCtx,
):
    """Les linies de dins NO es marquen: la fotografia ha de poder dir que es va
    arxivar la seccio, no cadascuna de les seves linies. Deixen de comptar perque
    la seva seccio no compta."""
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    section = await service.get_section(session, offer, section_id)
    section = await service.archive_section(
        session, offer, section, expected_revision=payload.expected_revision, user=ctx.user
    )
    await _audit(session, ctx, AuditAction.ARCHIVE, "economic_offer_section", offer, section)
    return await _read(session, ctx, workspace, offer)


@router.post(
    "/sections/{section_id}/lines",
    response_model=EconomicOfferRead,
    summary="Afegeix una linia",
)
async def create_line(
    workspace_id: uuid.UUID,
    section_id: uuid.UUID,
    payload: LineCreateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    section = await service.get_section(session, offer, section_id)
    fields = payload.model_dump(exclude_unset=True)
    fields.pop("expected_revision", None)
    line = await service.create_line(
        session,
        workspace,
        offer,
        section,
        expected_revision=payload.expected_revision,
        user=ctx.user,
        **fields,
    )
    await _audit(session, ctx, AuditAction.CREATE, "economic_offer_line", offer, line)
    return await _read(session, ctx, workspace, offer)


@router.patch(
    "/lines/{line_id}", response_model=EconomicOfferRead, summary="Edita una linia"
)
async def update_line(
    workspace_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: LineUpdateRequest,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    line = await service.get_line(session, offer, line_id)
    fields = payload.model_dump(exclude_unset=True)
    fields.pop("expected_revision", None)
    line, changed = await service.update_line(
        session,
        workspace,
        offer,
        line,
        fields=fields,
        expected_revision=payload.expected_revision,
        user=ctx.user,
    )
    if changed:
        await _audit(session, ctx, AuditAction.UPDATE, "economic_offer_line", offer, line)
    return await _read(session, ctx, workspace, offer)


@router.post(
    "/lines/{line_id}/archive", response_model=EconomicOfferRead, summary="Arxiva una linia"
)
async def archive_line(
    workspace_id: uuid.UUID,
    line_id: uuid.UUID,
    payload: RevisionGuarded,
    session: SessionDep,
    ctx: CompanyCtx,
):
    workspace = await workspace_service.get_workspace(session, ctx.company_id, workspace_id)
    offer = await service.get_offer(session, workspace)
    service.assert_can_edit(ctx.membership.role)
    line = await service.get_line(session, offer, line_id)
    line = await service.archive_line(
        session, offer, line, expected_revision=payload.expected_revision, user=ctx.user
    )
    await _audit(session, ctx, AuditAction.ARCHIVE, "economic_offer_line", offer, line)
    return await _read(session, ctx, workspace, offer)


# --------------------------------------------------------------------------- #
# Auditoria                                                                     #
# --------------------------------------------------------------------------- #


async def _audit(session, ctx, action, entity_type, offer, row) -> None:
    """Nomes mutacions reals, i mai amb xifres a dins."""
    await audit.record(
        session,
        company_id=ctx.company_id,
        user_id=ctx.user.id,
        action=action,
        entity_type=entity_type,
        entity_id=row.id,
        summary=service.audit_summary(oferta=offer.id, revisio=offer.revision),
    )
    await session.commit()
