"""Oferta economica: regles, persistencia i concurrencia.

Tres coses que aquesta capa NO fa, i que son les que la mantenen honesta:

**No calcula res.** Les formules son d'`app.offer.pricing` (S1). Aqui nomes es
construeix el `LineInput` de cada linia activa —resolent la durada efectiva, que
es l'unica decisio que S1 no pot prendre sola (C-3)— i es pugen els resultats
amb `aggregate()`. Cap derivat es desa.

**No barreja "ocult" amb "desconegut".** Un cost que no es pot veure i un cost
que encara no s'ha escrit son fets diferents. Per aixo el cost no viatja mai com
un `null` mes: viatja dins de `internal_economics`, que existeix o no existeix
segons qui pregunta. `unit_cost_ex_vat = null` vol dir, sempre, que ningu no
l'ha escrit.

**No deixa que dos escriptors es trepitgin.** Tota mutacio posterior a la creacio
porta `expected_revision`, i el contador s'incrementa amb un compare-and-swap
atomic a la base: `UPDATE ... WHERE revision = :expected RETURNING revision`. Un
`SELECT` seguit d'un `UPDATE` tindria una finestra enmig en la qual dos clients
poden llegir el mateix numero i escriure els dos.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from app.models.enums import MembershipRole, PricingBasis
from app.models.identity import User
from app.models.offer import (
    EconomicOffer,
    EconomicOfferLine,
    EconomicOfferLot,
    EconomicOfferSection,
)
from app.models.requirements import Requirement
from app.models.tenders import Tender, TenderLot
from app.models.workspace import BidWorkspace, WorkspaceTechnicalSolution
from app.offer.lot_label import economic_offer_lot_label
from app.offer.pricing import (
    LineInput,
    LineTotals,
    LotLimit,
    Totals,
    aggregate,
    line_totals,
    lot_limit,
    proposed_unit_price,
)
from app.offer.pricing import unit_amount as _unit_amount
from app.tenders.amounts import useful_amount
from app.tenders.lot_amounts import LotAmounts, load_lot_amounts
from app.workspace.checklist import applies_to_workspace_lot


def unit_amount_of(value: Decimal | None) -> Decimal | None:
    """`None` es propaga: desconegut no es zero."""
    return None if value is None else _unit_amount(value)

#: Qui pot escriure preus (C-6). `technical` no hi es: prepara la solucio
#: tecnica, no compromet el marge de l'empresa.
ECONOMIC_EDIT_ROLES = frozenset(
    {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.BID_MANAGER}
)

#: Qui pot veure cost intern, marge i markup. Coincideix amb els que poden
#: escriure, pero es una constant propia: son dues politiques diferents i
#: barrejar-les faria que canviar-ne una canvies l'altra sense voler.
ECONOMIC_INTERNAL_ROLES = ECONOMIC_EDIT_ROLES

#: Sentinella per distingir "no toquis aquest camp" de "posa-hi NULL".
UNSET = object()


class OfferRevisionConflictError(ConflictError):
    """Algu ha canviat l'oferta entremig: el que s'enviava ja no s'hi aplica."""

    code = "economic_offer_revision_conflict"


class OfferAlreadyExistsError(ConflictError):
    """Nomes hi ha una oferta per candidatura."""

    code = "economic_offer_already_exists"


class ArchivedNotEditableError(ConflictError):
    """Arxivat vol dir que ja no es toca: es la fotografia del que es va oferir."""

    code = "economic_offer_item_archived"


class LotSelectionNotAllowedError(ConflictError):
    """Els lots d'aquesta oferta els determina la candidatura (C-1)."""

    code = "economic_offer_lot_selection_not_allowed"


class SolutionRequirementMismatchError(ValidationError):
    """La linia diu un requisit i la solucio tecnica en resol un altre."""

    code = "economic_offer_link_mismatch"


def assert_can_edit(role: MembershipRole) -> None:
    """403 i no 404: l'usuari hi te acces, el que no te es potestat sobre preus."""
    if role not in ECONOMIC_EDIT_ROLES:
        raise PermissionDeniedError("El teu rol no pot editar l'oferta economica.")


def can_view_internal_economics(role: MembershipRole) -> bool:
    return role in ECONOMIC_INTERNAL_ROLES


# --------------------------------------------------------------------------- #
# Concurrencia                                                                  #
# --------------------------------------------------------------------------- #


async def bump_revision(
    session: AsyncSession, offer: EconomicOffer, expected_revision: int, user: User
) -> int:
    """Compare-and-swap atomic. Retorna la revisio nova o falla amb 409.

    Es una sola sentencia a proposit. Amb `SELECT` + comparacio a Python +
    `UPDATE` hi hauria una finestra enmig: dos clients poden llegir el mateix
    numero i escriure tots dos, i el segon es menjaria el primer sense que ningu
    se n'assabentes.

    Com que l'`UPDATE` bloqueja la fila de l'oferta, tambe serialitza la resta de
    la transaccio: el segon escriptor espera, i quan passa ja no compleix la
    condicio. El bump i la mutacio van a la MATEIXA transaccio, aixi que si la
    mutacio falla despres, el contador tampoc no avanca.
    """
    revision = (
        await session.execute(
            sa.update(EconomicOffer)
            .where(
                EconomicOffer.id == offer.id,
                EconomicOffer.company_id == offer.company_id,
                EconomicOffer.revision == expected_revision,
            )
            .values(revision=EconomicOffer.revision + 1, updated_by=user.id)
            .returning(EconomicOffer.revision)
        )
    ).scalar_one_or_none()

    if revision is None:
        raise OfferRevisionConflictError(
            "L'oferta ha canviat mentre editaves: torna a carregar-la.",
            details={"expected_revision": expected_revision},
        )
    return revision


async def assert_revision(
    session: AsyncSession, offer: EconomicOffer, expected_revision: int
) -> None:
    """Per a les operacions que no muten res.

    Un client que treballa sobre una versio vella no s'ha de poder colar per la
    porta de la idempotencia: si demana una cosa que ja hi es, pero la demana
    sobre un estat que ja no existeix, val mes que ho sapiga.
    """
    current = (
        await session.execute(
            sa.select(EconomicOffer.revision).where(EconomicOffer.id == offer.id)
        )
    ).scalar_one()
    if current != expected_revision:
        raise OfferRevisionConflictError(
            "L'oferta ha canviat mentre editaves: torna a carregar-la.",
            details={"expected_revision": expected_revision},
        )


# --------------------------------------------------------------------------- #
# C-1: quins lots te aquesta oferta                                             #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OfferShape:
    """Com es reparteix aquesta oferta, segons la candidatura i el plec (C-1)."""

    #: La candidatura es d'un lot concret: l'oferta te aquell lot i prou.
    bound_tender_lot_id: uuid.UUID | None
    #: El plec te lots i la candidatura es global: una persona tria a quins es presenta.
    selectable: bool

    @property
    def fixed(self) -> bool:
        """L'unic lot el determina la candidatura: ni se'n pot afegir ni treure."""
        return not self.selectable


async def offer_shape(session: AsyncSession, workspace: BidWorkspace) -> OfferShape:
    if workspace.tender_lot_id is not None:
        return OfferShape(bound_tender_lot_id=workspace.tender_lot_id, selectable=False)
    has_lots = (
        await session.execute(
            sa.select(sa.func.count(TenderLot.id)).where(
                TenderLot.tender_id == workspace.tender_id
            )
        )
    ).scalar_one() > 0
    return OfferShape(bound_tender_lot_id=None, selectable=has_lots)


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def get_offer(session: AsyncSession, workspace: BidWorkspace) -> EconomicOffer:
    offer = await session.scalar(
        sa.select(EconomicOffer)
        .where(
            EconomicOffer.workspace_id == workspace.id,
            EconomicOffer.company_id == workspace.company_id,
        )
        .options(
            selectinload(EconomicOffer.lots)
            .selectinload(EconomicOfferLot.sections)
            .selectinload(EconomicOfferSection.lines)
        )
        # Sense aixo, una lectura posterior a una escriptura dins de la mateixa
        # peticio tornaria les colleccions que la sessio ja tenia carregades, i
        # la fila que s'acaba d'afegir no hi sortiria.
        .execution_options(populate_existing=True)
    )
    if offer is None:
        raise NotFoundError("Aquesta candidatura encara no te oferta economica.")
    return offer


def _ordered(rows: Sequence, ) -> list:
    """Ordre estable: posicio, i despres la data i l'identificador.

    Sense desempat, dues files a la mateixa posicio podrien sortir en un ordre
    diferent a cada lectura i la pantalla ballaria sola.
    """
    return sorted(rows, key=lambda row: (row.position, row.created_at, str(row.id)))


def active(rows: Sequence) -> list:
    return [row for row in rows if row.archived_at is None]


# --------------------------------------------------------------------------- #
# Calcul: nomes construeix l'entrada i puja el resultat                         #
# --------------------------------------------------------------------------- #


def effective_duration_months(line: EconomicOfferLine, lot: EconomicOfferLot) -> int | None:
    """La durada de la linia, o la del lot (C-3). Mai s'assumeix res.

    Es l'unica decisio que S1 no pot prendre sola: el motor rep la durada ja
    resolta i, si es `None`, deixa indeterminats els totals que en depenen.
    """
    if line.duration_months_override is not None:
        return line.duration_months_override
    return lot.duration_months


def line_input(line: EconomicOfferLine, lot: EconomicOfferLot) -> LineInput:
    return LineInput(
        pricing_basis=line.pricing_basis,
        quantity=line.quantity,
        unit_cost_ex_vat=line.unit_cost_ex_vat,
        offered_unit_price_ex_vat=line.offered_unit_price_ex_vat,
        effective_duration_months=effective_duration_months(line, lot),
        vat_rate=line.vat_rate,
        tender_max_unit_price_ex_vat=line.tender_max_unit_price_ex_vat,
    )


@dataclass(frozen=True, slots=True)
class LineView:
    line: EconomicOfferLine
    totals: LineTotals
    effective_duration_months: int | None


@dataclass(frozen=True, slots=True)
class SectionView:
    section: EconomicOfferSection
    lines: list[LineView] = field(default_factory=list)
    totals: Totals = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class LotView:
    lot: EconomicOfferLot
    sections: list[SectionView] = field(default_factory=list)
    totals: Totals = None  # type: ignore[assignment]
    limit: LotLimit = None  # type: ignore[assignment]


@dataclass(frozen=True, slots=True)
class OfferView:
    offer: EconomicOffer
    shape: OfferShape
    lots: list[LotView] = field(default_factory=list)
    totals: Totals = None  # type: ignore[assignment]


async def build_view(
    session: AsyncSession, workspace: BidWorkspace, offer: EconomicOffer
) -> OfferView:
    """L'arbre sencer amb els numeros resolts.

    Els arxivats hi surten —la pantalla ha de poder ensenyar-los— pero no sumen:
    a cada nivell nomes s'agrega el que esta actiu, i com que un fill d'un pare
    arxivat no arriba a agregar-se mai, no cal propagar res cap avall.
    """
    shape = await offer_shape(session, workspace)
    maxims = await _lot_maximums(session, workspace, offer)

    lot_views: list[LotView] = []
    for lot in _ordered(offer.lots):
        section_views: list[SectionView] = []
        for section in _ordered(lot.sections):
            line_views = [
                LineView(
                    line=line,
                    totals=line_totals(line_input(line, lot)),
                    effective_duration_months=effective_duration_months(line, lot),
                )
                for line in _ordered(section.lines)
            ]
            section_views.append(
                SectionView(
                    section=section,
                    lines=line_views,
                    totals=aggregate([view.totals for view in active_views(line_views)]),
                )
            )
        lot_totals = aggregate([view.totals for view in active_views(section_views)])
        lot_views.append(
            LotView(
                lot=lot,
                sections=section_views,
                totals=lot_totals,
                limit=lot_limit(lot_totals.total_revenue, maxims.get(lot.id)),
            )
        )

    return OfferView(
        offer=offer,
        shape=shape,
        lots=lot_views,
        totals=aggregate([view.totals for view in active_views(lot_views)]),
    )


def active_views(views: Sequence) -> list:
    """Els que compten. Un element arxivat no participa en cap suma."""
    return [view for view in views if _row_of(view).archived_at is None]


def _row_of(view):
    return getattr(view, "line", None) or getattr(view, "section", None) or view.lot


async def _lot_maximums(
    session: AsyncSession, workspace: BidWorkspace, offer: EconomicOffer
) -> dict[uuid.UUID, Decimal | None]:
    """El maxim de cada lot: el pressupost base SENSE IVA.

    Amb lot del plec, el PBL sense IVA RESOLT del lot (F-06e,
    `app.tenders.lot_amounts`: el de la PLACSP si es > 0 i, si no, el de la PSCP
    `accepted`); sense lot, el de la licitacio, com sempre. Si no n'hi ha cap (o
    la PSCP el te en `conflict` o `no_value`), es queda a `None` i S1 deixa el
    limit indeterminat: dir que l'oferta hi cap seria afirmar-ho sobre un
    pressupost que no coneixem. Un `0.00` publicat tampoc no es un maxim
    (`app.tenders.amounts`): `None`, mai «qualsevol oferta positiva supera el
    maxim».
    """
    tender_lot_ids = {lot.tender_lot_id for lot in offer.lots if lot.tender_lot_id}
    resolved: dict[uuid.UUID, LotAmounts] = {}
    if tender_lot_ids:
        rows = await session.scalars(
            sa.select(TenderLot).where(TenderLot.id.in_(tender_lot_ids))
        )
        resolved = await load_lot_amounts(session, rows.all())
    tender = await session.get(Tender, workspace.tender_id)

    maximums: dict[uuid.UUID, Decimal | None] = {}
    for lot in offer.lots:
        if lot.tender_lot_id is not None:
            amounts = resolved.get(lot.tender_lot_id)
            maximums[lot.id] = amounts.base_budget_without_tax.value if amounts else None
        else:
            maximums[lot.id] = useful_amount(getattr(tender, "base_budget_without_tax", None))
    return maximums


# --------------------------------------------------------------------------- #
# Creacio de l'arrel                                                            #
# --------------------------------------------------------------------------- #


async def create_offer(
    session: AsyncSession, workspace: BidWorkspace, *, user: User
) -> tuple[EconomicOffer, bool]:
    """Obre l'oferta, o retorna la que ja hi havia.

    Idempotent com `create_workspace`: el boto de la pantalla no ha de poder
    crear dues ofertes ni que es premi dues vegades.

    Els lots inicials els determina C-1: si la candidatura es d'un lot, l'oferta
    neix amb aquell i prou; si el plec no te lots, amb el bloc global; i si el
    plec en te i la candidatura es global, neix BUIDA, perque a quins lots ens
    presentem ho diu una persona i no s'endevina.
    """
    existing = await session.scalar(
        sa.select(EconomicOffer).where(
            EconomicOffer.workspace_id == workspace.id,
            EconomicOffer.company_id == workspace.company_id,
        )
    )
    if existing is not None:
        return existing, False

    offer = EconomicOffer(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(offer)
    await session.flush()

    shape = await offer_shape(session, workspace)
    if shape.bound_tender_lot_id is not None:
        tender_lot = await session.get(TenderLot, shape.bound_tender_lot_id)
        await _add_lot(session, offer, tender_lot=tender_lot, user=user)
    elif not shape.selectable:
        await _add_lot(session, offer, tender_lot=None, user=user)

    await session.commit()
    await session.refresh(offer)
    return offer, True


async def _add_lot(
    session: AsyncSession,
    offer: EconomicOffer,
    *,
    tender_lot: TenderLot | None,
    user: User,
) -> EconomicOfferLot:
    """Els tres camins de C-1 passen per aqui, i per tant pel mateix label (C-13)."""
    position = (
        await session.execute(
            sa.select(sa.func.coalesce(sa.func.max(EconomicOfferLot.position), -1)).where(
                EconomicOfferLot.offer_id == offer.id
            )
        )
    ).scalar_one()
    lot = EconomicOfferLot(
        company_id=offer.company_id,
        offer_id=offer.id,
        tender_lot_id=tender_lot.id if tender_lot is not None else None,
        label=economic_offer_lot_label(tender_lot),
        position=position + 1,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(lot)
    await session.flush()
    return lot


async def update_offer(
    session: AsyncSession,
    offer: EconomicOffer,
    *,
    currency: object = UNSET,
    notes: object = UNSET,
    expected_revision: int,
    user: User,
) -> tuple[EconomicOffer, bool]:
    """Nomes moneda i notes. Retorna si ha canviat res de debo."""
    canvis = {}
    # `currency` es NOT NULL: enviar-hi `null` no es "treu-la", es una peticio sense sentit.
    if currency is not UNSET and currency is not None and currency != offer.currency:
        canvis["currency"] = currency
    if notes is not UNSET and notes != offer.notes:
        canvis["notes"] = notes

    if not canvis:
        # Un PATCH que no canvia res no es una modificacio: no ha de moure el
        # contador ni deixar rastre, o el seguent client quedaria invalidat per
        # una escriptura que no ha passat.
        await assert_revision(session, offer, expected_revision)
        return offer, False

    await bump_revision(session, offer, expected_revision, user)
    for camp, valor in canvis.items():
        setattr(offer, camp, valor)
    offer.updated_by = user.id
    await session.commit()
    await session.refresh(offer)
    return offer, True


# --------------------------------------------------------------------------- #
# Lots                                                                          #
# --------------------------------------------------------------------------- #


async def get_lot(
    session: AsyncSession, offer: EconomicOffer, lot_id: uuid.UUID
) -> EconomicOfferLot:
    lot = await session.scalar(
        sa.select(EconomicOfferLot).where(
            EconomicOfferLot.id == lot_id,
            EconomicOfferLot.offer_id == offer.id,
            EconomicOfferLot.company_id == offer.company_id,
        )
    )
    if lot is None:
        raise NotFoundError("Lot de l'oferta no trobat.")
    return lot


async def add_lot_from_tender_lot(
    session: AsyncSession,
    workspace: BidWorkspace,
    offer: EconomicOffer,
    *,
    tender_lot_id: uuid.UUID,
    expected_revision: int,
    user: User,
) -> tuple[EconomicOfferLot, bool]:
    """Una persona diu a quin lot del plec ens presentem (C-1, cas C)."""
    shape = await offer_shape(session, workspace)
    if shape.fixed:
        raise LotSelectionNotAllowedError(
            "Els lots d'aquesta oferta els determina la candidatura."
        )

    tender_lot = await session.scalar(
        sa.select(TenderLot).where(
            TenderLot.id == tender_lot_id, TenderLot.tender_id == workspace.tender_id
        )
    )
    if tender_lot is None:
        # Mateixa resposta que per a un identificador inventat.
        raise NotFoundError("Lot no trobat en aquesta licitacio.")

    existing = await session.scalar(
        sa.select(EconomicOfferLot).where(
            EconomicOfferLot.offer_id == offer.id,
            EconomicOfferLot.tender_lot_id == tender_lot.id,
            EconomicOfferLot.archived_at.is_(None),
        )
    )
    if existing is not None:
        # Ja hi es i actiu: no es crea res, no es toca el label i no es mou el
        # contador. Pero la revisio es comprova igual, perque la idempotencia no
        # pot ser una porta del darrere per a un client que va endarrerit.
        await assert_revision(session, offer, expected_revision)
        return existing, False

    await bump_revision(session, offer, expected_revision, user)
    # Si n'hi havia un d'arxivat, no es restaura: se'n crea un de nou, amb el
    # label derivat de les dades d'ara. La fila vella es la fotografia d'abans.
    lot = await _add_lot(session, offer, tender_lot=tender_lot, user=user)
    await session.commit()
    await session.refresh(lot)
    return lot, True


async def update_lot(
    session: AsyncSession,
    offer: EconomicOffer,
    lot: EconomicOfferLot,
    *,
    duration_months: object = UNSET,
    expected_revision: int,
    user: User,
) -> tuple[EconomicOfferLot, bool]:
    """La durada del lot, que es l'unica cosa configurable d'un lot (C-3).

    Ni el label, ni el lot del plec, ni la posicio: no son parametres, i per aixo
    no s'hi poden colar. Aixo val per als tres casos de C-1, perque el que C-1
    prohibeix es AFEGIR o TREURE lots quan els determina la candidatura, no
    dir quant dura el que hi ha.
    """
    await _assert_writable_lot(session, lot)

    if duration_months is UNSET or duration_months == lot.duration_months:
        # Res a canviar: es comprova la revisio i no es mou el contador, o
        # invalidariem la feina d'un altre sense haver escrit res.
        await assert_revision(session, offer, expected_revision)
        return lot, False

    await bump_revision(session, offer, expected_revision, user)
    lot.duration_months = duration_months
    lot.updated_by = user.id
    await session.commit()
    await session.refresh(lot)
    return lot, True


async def archive_lot(
    session: AsyncSession,
    workspace: BidWorkspace,
    offer: EconomicOffer,
    lot: EconomicOfferLot,
    *,
    expected_revision: int,
    user: User,
) -> EconomicOfferLot:
    shape = await offer_shape(session, workspace)
    if shape.fixed:
        raise LotSelectionNotAllowedError(
            "Aquesta oferta te un sol lot i el determina la candidatura."
        )
    if lot.archived_at is not None:
        raise ArchivedNotEditableError("Aquest lot ja esta arxivat.")

    await bump_revision(session, offer, expected_revision, user)
    lot.archived_at = datetime.now(UTC)
    lot.updated_by = user.id
    await session.commit()
    await session.refresh(lot)
    return lot


# --------------------------------------------------------------------------- #
# Seccions                                                                      #
# --------------------------------------------------------------------------- #


async def get_section(
    session: AsyncSession, offer: EconomicOffer, section_id: uuid.UUID
) -> EconomicOfferSection:
    section = await session.scalar(
        sa.select(EconomicOfferSection)
        .join(EconomicOfferLot, EconomicOfferLot.id == EconomicOfferSection.offer_lot_id)
        .where(
            EconomicOfferSection.id == section_id,
            EconomicOfferSection.company_id == offer.company_id,
            EconomicOfferLot.offer_id == offer.id,
        )
    )
    if section is None:
        raise NotFoundError("Seccio de l'oferta no trobada.")
    return section


async def _assert_writable_lot(session: AsyncSession, lot: EconomicOfferLot) -> None:
    if lot.archived_at is not None:
        raise ArchivedNotEditableError("Aquest lot esta arxivat: ja no es pot editar.")


async def _assert_writable_section(
    session: AsyncSession, section: EconomicOfferSection
) -> None:
    """Un fill d'un pare arxivat tambe es nomes de lectura.

    No es propaga `archived_at` cap avall a proposit: la fotografia ha de poder
    dir que la seccio es va arxivar un dia i les seves linies no.
    """
    if section.archived_at is not None:
        raise ArchivedNotEditableError("Aquesta seccio esta arxivada: ja no es pot editar.")
    lot = await session.get(EconomicOfferLot, section.offer_lot_id)
    await _assert_writable_lot(session, lot)


async def create_section(
    session: AsyncSession,
    offer: EconomicOffer,
    lot: EconomicOfferLot,
    *,
    title: str,
    expected_revision: int,
    user: User,
) -> EconomicOfferSection:
    await _assert_writable_lot(session, lot)
    await bump_revision(session, offer, expected_revision, user)

    position = (
        await session.execute(
            sa.select(
                sa.func.coalesce(sa.func.max(EconomicOfferSection.position), -1)
            ).where(EconomicOfferSection.offer_lot_id == lot.id)
        )
    ).scalar_one()
    section = EconomicOfferSection(
        company_id=offer.company_id,
        offer_lot_id=lot.id,
        title=title,
        position=position + 1,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(section)
    await session.commit()
    await session.refresh(section)
    return section


async def update_section(
    session: AsyncSession,
    offer: EconomicOffer,
    section: EconomicOfferSection,
    *,
    title: object = UNSET,
    position: object = UNSET,
    expected_revision: int,
    user: User,
) -> tuple[EconomicOfferSection, bool]:
    await _assert_writable_section(session, section)

    canvis = {}
    if title is not UNSET and title != section.title:
        canvis["title"] = title
    if position is not UNSET and position != section.position:
        canvis["position"] = position
    if not canvis:
        await assert_revision(session, offer, expected_revision)
        return section, False

    await bump_revision(session, offer, expected_revision, user)
    for camp, valor in canvis.items():
        setattr(section, camp, valor)
    section.updated_by = user.id
    await session.commit()
    await session.refresh(section)
    return section, True


async def archive_section(
    session: AsyncSession,
    offer: EconomicOffer,
    section: EconomicOfferSection,
    *,
    expected_revision: int,
    user: User,
) -> EconomicOfferSection:
    await _assert_writable_section(session, section)
    await bump_revision(session, offer, expected_revision, user)
    section.archived_at = datetime.now(UTC)
    section.updated_by = user.id
    await session.commit()
    await session.refresh(section)
    return section


# --------------------------------------------------------------------------- #
# Linies                                                                        #
# --------------------------------------------------------------------------- #


async def get_line(
    session: AsyncSession, offer: EconomicOffer, line_id: uuid.UUID
) -> EconomicOfferLine:
    line = await session.scalar(
        sa.select(EconomicOfferLine)
        .join(
            EconomicOfferSection, EconomicOfferSection.id == EconomicOfferLine.section_id
        )
        .join(EconomicOfferLot, EconomicOfferLot.id == EconomicOfferSection.offer_lot_id)
        .where(
            EconomicOfferLine.id == line_id,
            EconomicOfferLine.company_id == offer.company_id,
            EconomicOfferLot.offer_id == offer.id,
        )
    )
    if line is None:
        raise NotFoundError("Linia de l'oferta no trobada.")
    return line


async def _assert_writable_line(session: AsyncSession, line: EconomicOfferLine) -> None:
    if line.archived_at is not None:
        raise ArchivedNotEditableError("Aquesta linia esta arxivada: ja no es pot editar.")
    section = await session.get(EconomicOfferSection, line.section_id)
    await _assert_writable_section(session, section)


async def _validate_links(
    session: AsyncSession,
    workspace: BidWorkspace,
    lot: EconomicOfferLot,
    *,
    requirement_id: uuid.UUID | None,
    technical_solution_id: uuid.UUID | None,
) -> None:
    """Que la linia no digui una cosa i n'assenyali una altra.

    El requisit es comprova contra el lot D'AQUESTA LINIA i no contra el de la
    candidatura: un workspace global pot presentar-se a diversos lots, i cada
    bloc de preus nomes pot citar els requisits que apliquen al seu.
    """
    requirement = None
    if requirement_id is not None:
        requirement = await session.scalar(
            sa.select(Requirement).where(
                Requirement.id == requirement_id,
                Requirement.tender_id == workspace.tender_id,
            )
        )
        if requirement is None or not applies_to_workspace_lot(
            requirement, lot.tender_lot_id
        ):
            raise NotFoundError("Requisit no trobat en aquest lot.")

    if technical_solution_id is None:
        return

    solution = await session.scalar(
        sa.select(WorkspaceTechnicalSolution).where(
            WorkspaceTechnicalSolution.id == technical_solution_id,
            WorkspaceTechnicalSolution.company_id == workspace.company_id,
            WorkspaceTechnicalSolution.workspace_id == workspace.id,
        )
    )
    if solution is None:
        raise NotFoundError("Solucio tecnica no trobada en aquesta candidatura.")

    # Una solucio manual no resol cap requisit concret i es pot fer servir a
    # qualsevol linia. La que SI en resol un no pot acabar sota un altre: la
    # linia diria que cobreix el requisit A amb la solucio del requisit B.
    if (
        requirement_id is not None
        and solution.requirement_id is not None
        and solution.requirement_id != requirement_id
    ):
        raise SolutionRequirementMismatchError(
            "Aquesta solucio tecnica resol un altre requisit."
        )


async def create_line(
    session: AsyncSession,
    workspace: BidWorkspace,
    offer: EconomicOffer,
    section: EconomicOfferSection,
    *,
    expected_revision: int,
    user: User,
    concept: str,
    quantity: Decimal,
    pricing_basis: PricingBasis,
    requirement_id: uuid.UUID | None = None,
    technical_solution_id: uuid.UUID | None = None,
    supplier_name: str | None = None,
    product_name: str | None = None,
    duration_months_override: int | None = None,
    unit_cost_ex_vat: Decimal | None = None,
    offered_unit_price_ex_vat: Decimal | None = None,
    #: Objectiu de markup (S3-S8b). Hi es perque `LineCreateRequest` ja el porta
    #: des d'aleshores: el PATCH l'acceptava i la creacio no, de manera que
    #: enviar-lo al cos d'una linia nova esclatava en comptes d'escriure'l.
    #: Posar-hi un objectiu NO mou cap preu: nomes `apply-target` ho fa.
    target_markup_pct: Decimal | None = None,
    tender_max_unit_price_ex_vat: Decimal | None = None,
    vat_rate: Decimal | None = None,
    notes: str | None = None,
    #: D'on ve la linia (S3-S8). NOMES el servei d'adopcio el passa: no es a
    #: cap schema d'escriptura, de manera que un client no pot fabricar una
    #: provinenca enviant un identificador qualsevol.
    solution_option_id: uuid.UUID | None = None,
) -> EconomicOfferLine:
    await _assert_writable_section(session, section)
    lot = await session.get(EconomicOfferLot, section.offer_lot_id)
    await _validate_links(
        session,
        workspace,
        lot,
        requirement_id=requirement_id,
        technical_solution_id=technical_solution_id,
    )
    await bump_revision(session, offer, expected_revision, user)

    position = (
        await session.execute(
            sa.select(sa.func.coalesce(sa.func.max(EconomicOfferLine.position), -1)).where(
                EconomicOfferLine.section_id == section.id
            )
        )
    ).scalar_one()
    line = EconomicOfferLine(
        company_id=offer.company_id,
        section_id=section.id,
        concept=concept,
        requirement_id=requirement_id,
        technical_solution_id=technical_solution_id,
        supplier_name=supplier_name,
        product_name=product_name,
        quantity=quantity,
        pricing_basis=pricing_basis,
        duration_months_override=duration_months_override,
        unit_cost_ex_vat=unit_cost_ex_vat,
        offered_unit_price_ex_vat=offered_unit_price_ex_vat,
        target_markup_pct=target_markup_pct,
        tender_max_unit_price_ex_vat=tender_max_unit_price_ex_vat,
        vat_rate=vat_rate,
        notes=notes,
        solution_option_id=solution_option_id,
        position=position + 1,
        created_by=user.id,
        updated_by=user.id,
    )
    session.add(line)
    await session.commit()
    await session.refresh(line)
    return line


#: Els camps que un PATCH de linia pot tocar. `section_id`, `company_id`,
#: `archived_at`, els actors i les dates no hi son, i no s'hi poden colar.
LINE_FIELDS = (
    "concept",
    "requirement_id",
    "technical_solution_id",
    "supplier_name",
    "product_name",
    "quantity",
    "pricing_basis",
    "duration_months_override",
    "unit_cost_ex_vat",
    "offered_unit_price_ex_vat",
    # Objectiu comercial (S3-S8b). Nomes hi arriben els rols economics,
    # com a tota la resta d'aquest router.
    "target_markup_pct",
    "tender_max_unit_price_ex_vat",
    "vat_rate",
    "notes",
    "position",
)


async def update_line(
    session: AsyncSession,
    workspace: BidWorkspace,
    offer: EconomicOffer,
    line: EconomicOfferLine,
    *,
    fields: dict,
    expected_revision: int,
    user: User,
) -> tuple[EconomicOfferLine, bool]:
    await _assert_writable_line(session, line)

    canvis = {
        camp: valor
        for camp, valor in fields.items()
        if camp in LINE_FIELDS and valor != getattr(line, camp)
    }
    if not canvis:
        await assert_revision(session, offer, expected_revision)
        return line, False

    if "requirement_id" in canvis or "technical_solution_id" in canvis:
        section = await session.get(EconomicOfferSection, line.section_id)
        lot = await session.get(EconomicOfferLot, section.offer_lot_id)
        await _validate_links(
            session,
            workspace,
            lot,
            requirement_id=canvis.get("requirement_id", line.requirement_id),
            technical_solution_id=canvis.get(
                "technical_solution_id", line.technical_solution_id
            ),
        )

    await bump_revision(session, offer, expected_revision, user)
    for camp, valor in canvis.items():
        setattr(line, camp, valor)
    line.updated_by = user.id
    await session.commit()
    await session.refresh(line)
    return line, True


async def archive_line(
    session: AsyncSession,
    offer: EconomicOffer,
    line: EconomicOfferLine,
    *,
    expected_revision: int,
    user: User,
) -> EconomicOfferLine:
    await _assert_writable_line(session, line)
    await bump_revision(session, offer, expected_revision, user)
    line.archived_at = datetime.now(UTC)
    line.updated_by = user.id
    await session.commit()
    await session.refresh(line)
    return line


def audit_summary(**parts: object) -> str:
    """Rastre curt: identificadors, context i revisio.

    Mai preus, ni costos, ni marges, ni notes. `AuditLog` nomes te `summary`, que
    es text pla de 500 caracters, i qualsevol xifra interna que hi caigues seria
    una segona porta d'acces al que C-6 amaga a l'API.
    """
    return " ".join(f"{clau}={valor}" for clau, valor in parts.items() if valor is not None)


# --------------------------------------------------------------------------- #
# Objectiu comercial (S3-S8b)                                                   #
# --------------------------------------------------------------------------- #


class TargetInputMissingError(ValidationError):
    """Per aplicar l'objectiu calen el cost i el markup, i n'hi falta algun.

    No s'hi posa `0` a cap dels dos: un cost desconegut no es zero, i un markup
    desconegut no es "a cost". Escriuriem un preu que ningu no ha decidit.
    """

    code = "economic_line_target_inputs_missing"


async def apply_target(
    session: AsyncSession,
    offer: EconomicOffer,
    line: EconomicOfferLine,
    *,
    expected_revision: int,
    user: User,
) -> tuple[EconomicOfferLine, bool]:
    """Escriu el preu proposat al preu ofert. **Nomes quan algu ho demana.**

    Es l'UNIC cami pel qual el preu ofert canvia a partir del cost i de
    l'objectiu. Ni editar el markup ni sincronitzar el cost el toquen: aquesta
    accio existeix justament perque escriure el preu es una decisio, i com que
    l'usuari acaba de demanar-la, pot substituir un preu escrit a ma.

    El calcul surt del cost de la LINIA —el snapshot adoptat—, mai del de
    l'opcio viva. Si l'opcio ha canviat i es vol el cost nou, primer se
    sincronitza i despres s'aplica: son dues decisions.
    """
    await _assert_writable_line(session, line)

    missing = [
        name
        for name in ("unit_cost_ex_vat", "target_markup_pct")
        if getattr(line, name) is None
    ]
    if missing:
        raise TargetInputMissingError(
            "Falten dades per calcular el preu proposat.", details={"missing": missing}
        )

    proposed = proposed_unit_price(line.unit_cost_ex_vat, line.target_markup_pct)
    if unit_amount_of(line.offered_unit_price_ex_vat) == proposed:
        # Ja hi era: aplicar-lo no canvia res i no ha de moure el testimoni.
        await assert_revision(session, offer, expected_revision)
        return line, False

    await bump_revision(session, offer, expected_revision, user)
    line.offered_unit_price_ex_vat = proposed
    line.updated_by = user.id
    await session.commit()
    await session.refresh(line)
    return line, True
