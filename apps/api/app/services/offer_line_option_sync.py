"""Divergencia i sincronitzacio explicita linia <-> opcio (S3-S8b).

L'opcio es la configuracio VIVA; la linia es el snapshot ADOPTAT. Quan l'opcio
canvia, la linia **no es mou**: el que passa es que hi ha una diferencia, i
aquest modul la calcula i la sap resoldre quan una persona ho demana, camp a
camp.

**Res d'aixo es desa.** No hi ha cap `diverged`, cap `synced_at` i cap hash: la
divergencia es una comparacio entre dues files que existeixen. Desar-la voldria
dir tenir una copia que envelleix i que un dia diria una cosa diferent de la que
surt de mirar-les.

**I no hi ha cap trigger.** Ni event, ni "mantenir sincronitzat". Sincronitzar
es una accio que demana algu, i diu QUINS camps adopta. Fer-ho sol voldria dir
canviar una oferta sense que ningu ho hagues decidit.

La comparacio del cost passa per `unit_amount()` als dos costats, i aixo es
imprescindible: l'opcio el guarda amb sis decimals i la linia amb quatre, o
sigui que un snapshot acabat de crear es veuria diferent de la seva font si es
comparessin en brut. Una linia nova no pot neixer divergida.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models.offer import EconomicOffer, EconomicOfferLine
from app.models.solution_options import WorkspaceSolutionOption
from app.models.workspace import BidWorkspace
from app.offer.pricing import unit_amount

#: Els camps que l'opcio es autoritat per actualitzar. **Es exactament el
#: mapatge que fa servir `to-offer-line`**: si el sync pogues tocar alguna cosa
#: que l'adopcio no copia, la linia acabaria en un estat que no s'hauria pogut
#: crear mai.
#:
#: El que NO hi es, i per que:
#:
#: * `concept` — l'opcio no en te cap homoleg; el va escriure una persona;
#: * `offered_unit_price_ex_vat` — l'opcio no te preu de venda;
#: * `vat_rate` i `target_markup_pct` — no son de l'opcio;
#: * `solution_option_id` — es la provinenca, no un valor a sincronitzar.
SYNCABLE_FIELDS = (
    "supplier_name",
    "product_name",
    "quantity",
    "unit_cost_ex_vat",
    "pricing_basis",
    "duration_months_override",
    "requirement_id",
    "technical_solution_id",
)

#: Els camps de la whitelist que son ECONOMIA INTERNA. El seu NOM ja es
#: informacio de dins: saber "el cost ha canviat" es saber alguna cosa del cost.
#:
#: Per aixo la redaccio no es nomes dels valors. Un rol sense economia interna
#: no ha de rebre aquests noms per cap superficie —ni al read-model, ni a
#: readiness, ni a l'auditoria—, i filtrar-ho a la pantalla no serveix: la
#: resposta ja ha sortit del servidor.
INTERNAL_ONLY_FIELDS = frozenset({"unit_cost_ex_vat"})


def visible_fields(fields: list[str], *, internal: bool) -> list[str]:
    """Els noms de camps divergents que aquest rol pot rebre.

    Amb `internal` es tota la whitelist; sense, nomes els publics. Es UNA sola
    funcio i la fan servir totes les sortides, perque una redaccio que cada
    endpoint decidis pel seu compte acabaria tenint un forat.
    """
    if internal:
        return list(fields)
    return [name for name in fields if name not in INTERNAL_ONLY_FIELDS]


#: Camps que a la linia son NOT NULL. Si l'opcio els te buits no s'hi poden
#: sincronitzar: no hi ha cap valor a adoptar, i inventar-ne un (`0`, `one_off`)
#: fabricaria l'economia de l'oferta.
REQUIRED_ON_LINE = ("quantity", "pricing_basis")

#: Els camps que es desen amb menys precisio a la linia que a l'opcio. Comparar
#: o copiar sense passar per aqui inventaria diferencies inexistents.
_UNIT_SCALED = ("unit_cost_ex_vat",)


class LineHasNoOptionError(ValidationError):
    """Aquesta linia no ve de cap opcio: no hi ha res des d'on sincronitzar."""

    code = "economic_line_has_no_solution_option"


class UnknownSyncFieldError(ValidationError):
    """Nomes es poden adoptar els camps de què l'opcio es autoritat."""

    code = "economic_line_sync_field_not_allowed"


class SourceValueMissingError(ValidationError):
    """L'opcio te buit un camp que a la linia es obligatori."""

    code = "economic_line_sync_source_missing"


def _comparable(field: str, value: Any) -> Any:
    """El valor tal com quedaria DESAT a la linia.

    Es el que fa que un snapshot acabat de crear no es vegi divergit: el cost de
    l'opcio te sis decimals i el de la linia quatre, i 12,345678 i 12,3457 son
    el mateix numero a dues escales.
    """
    if value is None:
        return None
    if field in _UNIT_SCALED and isinstance(value, Decimal):
        return unit_amount(value)
    return value


@dataclass(frozen=True, slots=True)
class FieldDivergence:
    field: str
    #: El que diu la linia ara. Es el valor adoptat, no un error.
    line_value: Any
    #: El que diu l'opcio viva.
    option_value: Any


def diverged_fields(
    line: EconomicOfferLine, option: WorkspaceSolutionOption
) -> list[FieldDivergence]:
    """En que difereix el snapshot de la font viva. **Una sola funcio ho diu.**

    La fa servir el read-model, el finding i la pantalla: si cadascu ho
    calcules pel seu compte, un dia dirien coses diferents sobre la mateixa
    linia.
    """
    out: list[FieldDivergence] = []
    for field in SYNCABLE_FIELDS:
        current = _comparable(field, getattr(line, field))
        incoming = _comparable(field, getattr(option, field))
        if current != incoming:
            out.append(
                FieldDivergence(field=field, line_value=current, option_value=incoming)
            )
    return out


async def diverged_lines(
    session: AsyncSession, workspace: BidWorkspace
) -> list[tuple[EconomicOfferLine, WorkspaceSolutionOption, list[FieldDivergence]]]:
    """Les linies VIVES d'aquesta candidatura que ja no diuen el que diu la seva
    opcio.

    Les arxivades no: son la fotografia del que es va oferir, i no hi ha res a
    revisar-hi.
    """
    from app.models.offer import EconomicOfferLot, EconomicOfferSection

    rows = (
        await session.execute(
            sa.select(EconomicOfferLine, WorkspaceSolutionOption)
            .join(
                WorkspaceSolutionOption,
                WorkspaceSolutionOption.id == EconomicOfferLine.solution_option_id,
            )
            .join(
                EconomicOfferSection,
                EconomicOfferSection.id == EconomicOfferLine.section_id,
            )
            .join(EconomicOfferLot, EconomicOfferLot.id == EconomicOfferSection.offer_lot_id)
            .join(EconomicOffer, EconomicOffer.id == EconomicOfferLot.offer_id)
            .where(
                EconomicOffer.workspace_id == workspace.id,
                EconomicOfferLine.archived_at.is_(None),
            )
        )
    ).all()

    out = []
    for line, option in rows:
        differences = diverged_fields(line, option)
        if differences:
            out.append((line, option, differences))
    return out


async def divergence_by_line(
    session: AsyncSession, workspace: BidWorkspace
) -> dict[uuid.UUID, list[FieldDivergence]]:
    """Per pintar l'indicador sense preguntar-ho linia a linia."""
    return {
        line.id: differences
        for line, _option, differences in await diverged_lines(session, workspace)
    }


def assert_syncable(fields: list[str]) -> None:
    unknown = [name for name in fields if name not in SYNCABLE_FIELDS]
    if unknown:
        raise UnknownSyncFieldError(
            "Aquests camps no venen de l'opcio.",
            details={"fields": unknown, "allowed": list(SYNCABLE_FIELDS)},
        )


def apply_fields(
    line: EconomicOfferLine, option: WorkspaceSolutionOption, fields: list[str]
) -> list[str]:
    """Adopta NOMES els camps demanats. Retorna els que han canviat de debo.

    Per camps: sincronitzar el proveidor no refresca el cost, i no hi ha cap
    "ja que hi som". Qui ho demana tria que adopta i que no.

    **No toca el preu ofert, ni l'objectiu, ni el concepte**: no son de l'opcio.
    """
    missing = [
        name
        for name in fields
        if name in REQUIRED_ON_LINE and getattr(option, name) is None
    ]
    if missing:
        raise SourceValueMissingError(
            "L'opcio te buits camps que a la linia son obligatoris.",
            details={"missing": missing},
        )

    changed: list[str] = []
    for name in fields:
        incoming = _comparable(name, getattr(option, name))
        if _comparable(name, getattr(line, name)) != incoming:
            setattr(line, name, incoming)
            changed.append(name)
    return changed


async def sync_from_option(
    session: AsyncSession,
    workspace: BidWorkspace,
    offer: EconomicOffer,
    line: EconomicOfferLine,
    *,
    fields: list[str],
    expected_revision: int,
    user: Any,
) -> tuple[EconomicOfferLine, list[str]]:
    """Adopta els camps demanats de l'opcio viva. Retorna els que han canviat.

    **Explicit i per camps.** No hi ha cap "sincronitza-ho tot" implicit, i
    sincronitzar el proveidor no arrossega el cost. Qui ho demana tria.
    """
    from app.services import economic_offer as offer_service
    from app.services import solution_options as option_service

    await offer_service._assert_writable_line(session, line)
    assert_syncable(fields)

    if line.solution_option_id is None:
        raise LineHasNoOptionError(
            "Aquesta linia no ve de cap opcio de solucio.", details={"line_id": str(line.id)}
        )

    # La font es la de la linia, mai una que envii el client: un identificador
    # al cos deixaria fabricar d'on ve una xifra.
    option = await option_service.load_option(
        session, workspace, line.solution_option_id
    )
    if option.archived_at is not None:
        raise option_service.OptionArchivedError("Aquesta opcio esta arxivada.")

    changed = apply_fields(line, option, fields)
    if not changed:
        await offer_service.assert_revision(session, offer, expected_revision)
        return line, []

    await offer_service.bump_revision(session, offer, expected_revision, user)
    line.updated_by = user.id
    await session.commit()
    await session.refresh(line)
    return line, changed
