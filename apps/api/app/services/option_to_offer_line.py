"""Adoptar una opcio com a linia d'oferta (S3-S8).

La frase que governa el modul:

    l'opcio es la configuracio VIVA; la linia es el snapshot ADOPTAT.

Quan es crea la linia, es copien els camps homolegs i s'hi queden. Que despres
canviin el proveidor, la quantitat o el cost de l'opcio **no toca la linia**: la
xifra que va a l'oferta la va adoptar una persona un dia concret, i moure-la
sola voldria dir canviar una oferta sense que ningu ho hagues decidit. La sync
explicita arriba mes endavant amb el seu propi cami de resolucio.

**Idempotencia abans que res.** Un doble clic, un reintent de xarxa o una
peticio que va arribar dues vegades no han de crear dues linies ni petar: si ja
hi ha linia viva per a aquesta opcio, es retorna aquella i no es toca res. Per
aixo la comprovacio va PRIMER, fins i tot abans de mirar la revisio: el
`expected_revision` d'un reintent ha quedat antic justament perque la primera
crida va tenir exit, i castigar-lo seria castigar l'exit.

**I una segona defensa per a la cursa de debo.** Dues peticions simultanies
poden veure totes dues que no hi ha linia. L'index unic parcial de la base es
qui ho resol; aqui nomes cal convergir-hi: qui perd torna a llegir i retorna la
linia que ha creat l'altre, sense consumir cap revisio i sense un 500.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models.enums import OptionSelection
from app.models.identity import User
from app.models.offer import EconomicOffer, EconomicOfferLine
from app.models.solution_options import WorkspaceSolutionOption
from app.models.workspace import BidWorkspace
from app.services import economic_offer as offer_service
from app.services import solution_options as option_service


class OptionNotSelectedError(ValidationError):
    """Nomes s'adopta el que algu ha triat (D-03).

    Crear una linia economica es afirmar "aquesta opcio s'ha adoptat". Si ningu
    no l'ha triada, l'afirmacio no es certa, i seleccionar-la automaticament
    seria prendre la decisio en lloc seu.
    """

    code = "solution_option_not_selected"


class OptionNotReadyForOfferLineError(ValidationError):
    """A l'opcio li falta el que la linia necessita per existir.

    `quantity` i `pricing_basis` son obligatoris a `EconomicOfferLine` i
    opcionals a l'opcio. Posar-hi `0` o `one_off` per omplir el forat
    fabricaria l'economia de l'oferta: la base de facturacio multiplica el preu
    pels mesos del contracte, i una quantitat zero faria que la linia sumes zero
    sense que ningu se n'adones.
    """

    code = "solution_option_not_ready_for_offer_line"


@dataclass(slots=True)
class AdoptedLine:
    line: EconomicOfferLine
    #: Fals quan la linia ja hi era. La crida ha estat un reintent, no un canvi.
    created: bool


async def live_line_for(
    session: AsyncSession, option_id: uuid.UUID
) -> EconomicOfferLine | None:
    """La linia VIVA d'aquesta opcio, si n'hi ha.

    Les arxivades no compten: conserven la seva provinenca com a historia i
    alhora deixen l'opcio lliure per tornar a convertir-se.
    """
    return await session.scalar(
        sa.select(EconomicOfferLine).where(
            EconomicOfferLine.solution_option_id == option_id,
            EconomicOfferLine.archived_at.is_(None),
        )
    )


def _assert_ready(option: WorkspaceSolutionOption) -> None:
    missing = [
        name
        for name in ("quantity", "pricing_basis")
        if getattr(option, name) is None
    ]
    if missing:
        raise OptionNotReadyForOfferLineError(
            "A aquesta opcio li falten dades per poder ser una linia d'oferta.",
            details={"missing": missing},
        )


async def to_offer_line(
    session: AsyncSession,
    *,
    workspace: BidWorkspace,
    offer: EconomicOffer,
    option_id: uuid.UUID,
    section_id: uuid.UUID,
    expected_revision: int,
    concept: str,
    user: User,
) -> AdoptedLine:
    """Crea la linia d'oferta d'aquesta opcio, o retorna la que ja hi havia.

    `concept` l'escriu qui crea la linia: l'opcio no en te cap homoleg, i
    derivar-lo del producte o del proveidor seria inventar-se el nom d'una
    linia que anira a una oferta de debo.
    """
    option = await option_service.load_option(session, workspace, option_id)

    # PRIMER de tot: si ja hi es, aixo es un reintent i no un canvi.
    existing = await live_line_for(session, option.id)
    if existing is not None:
        return AdoptedLine(line=existing, created=False)

    if option.archived_at is not None:
        raise option_service.OptionArchivedError("Aquesta opcio esta arxivada.")
    if option.selection != OptionSelection.SELECTED:
        raise OptionNotSelectedError(
            "Nomes es pot portar a l'oferta una opcio seleccionada.",
            details={"selection": option.selection.value},
        )
    _assert_ready(option)

    # La seccio ha de ser d'aquesta oferta i d'aquesta empresa: ho comprova el
    # servei canonic, i no es duplica aqui.
    section = await offer_service.get_section(session, offer, section_id)

    try:
        # La linia neix AMB la seva provinenca, en un sol INSERT: aixi l'index
        # unic parcial protegeix la insercio de debo, sense cap finestra entre
        # crear la linia i dir d'on ve.
        line = await offer_service.create_line(
            session,
            workspace,
            offer,
            section,
            expected_revision=expected_revision,
            user=user,
            concept=concept.strip(),
            # Els homolegs reals, amb els noms canonics dels dos models. El que
            # no existeix als dos costats no s'inventa: `concept` el dona la
            # persona i el preu de venda no es deriva mai del cost.
            quantity=option.quantity,
            pricing_basis=option.pricing_basis,
            requirement_id=option.requirement_id,
            technical_solution_id=option.technical_solution_id,
            supplier_name=option.supplier_name,
            product_name=option.product_name,
            duration_months_override=option.duration_months_override,
            unit_cost_ex_vat=option.unit_cost_ex_vat,
            solution_option_id=option.id,
        )
    except offer_service.OfferRevisionConflictError:
        # El CAS ha fallat sense escriure res: la transaccio segueix sana i no
        # cal desfer-la. Si la revisio s'ha mogut perque una altra peticio
        # acabava d'adoptar AQUESTA mateixa opcio, la resposta bona es la seva
        # linia. Si s'ha mogut per qualsevol altra cosa, aixo si que es un
        # conflicte de debo i ha de ser un 409.
        winner = await live_line_for(session, option.id)
        if winner is not None:
            return AdoptedLine(line=winner, created=False)
        raise
    except IntegrityError:
        # L'index unic parcial ha dit que no: una altra peticio ha inserit la
        # linia d'aquesta opcio entremig. Aqui la transaccio SI que ha quedat
        # trencada i cal desfer-la abans de tornar a llegir. El bump de revisio
        # se'n va amb ella, o sigui que la perdedora no consumeix res.
        await session.rollback()
        winner = await live_line_for(session, option.id)
        if winner is not None:
            return AdoptedLine(line=winner, created=False)
        raise

    return AdoptedLine(line=line, created=True)


async def live_lines_for(
    session: AsyncSession, option_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """Quina linia viva te cada opcio, en una sola consulta.

    En batch: amb una llista de vint opcions, preguntar-ho fila a fila serien
    vint consultes per pintar una taula.
    """
    if not option_ids:
        return {}
    rows = (
        await session.execute(
            sa.select(
                EconomicOfferLine.solution_option_id, EconomicOfferLine.id
            ).where(
                EconomicOfferLine.solution_option_id.in_(option_ids),
                EconomicOfferLine.archived_at.is_(None),
            )
        )
    ).all()
    return {option_id: line_id for option_id, line_id in rows}
