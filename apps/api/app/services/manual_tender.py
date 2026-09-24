"""Alta manual d'una licitacio que cap font sindicada no porta (S3-S1).

Tres decisions governen tot el que hi ha aqui.

**La identitat no es el que escriu la persona.** El numero d'expedient no es
unic —el model ja ho documenta: dos organismes en poden fer servir el mateix— i
el nom d'un organisme encara menys. La identitat d'una licitacio manual es
`external_id = "manual:<company_id>:<client_token>"`, amb el token que genera el
formulari i manté estable durant tots els reintents. La d'un organisme manual,
igual amb el seu propi token. El prefix d'empresa hi es perque la font MANUAL es
una i global: sense ell, l'espai de tokens d'una empresa limitaria el d'una
altra. La clau d'idempotencia conceptual es `(company_id, client_token)`.

**La idempotencia surt de la unicitat, no d'una taula de peticions.** Ja hi ha
`UNIQUE(source_id, external_id)` a `tenders` i `UNIQUE(source_id,
external_identifier)` a `buyers`. Un reintent busca primer; si el troba, el
retorna. El que NO fa mai es sobreescriure: si el mateix token torna amb una
licitacio materialment diferent, aixo no es un reintent, es un error, i es diu.

**La font no autoritza.** Hi ha una sola `Source` MANUAL, global i compartida,
i qui pot veure que ho diu `owner_company_id` de cada fila. Una font per empresa
convertiria la provinenca en un control d'acces, i esborrar una empresa
arrossegaria la font i amb ella licitacions d'altri.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import MembershipRole, SourceType
from app.models.identity import User
from app.models.sources import Source
from app.models.tenders import Buyer, Tender
from app.services import tender_lifecycle
from app.tenders.visibility import buyer_visible_to_company, visible_to_company

#: Qui pot obrir un expedient. Coincideix amb qui decideix, no amb qui edita
#: una solucio tecnica: donar d'alta una licitacio es un acte de negoci.
CREATE_ROLES = frozenset(
    {MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.BID_MANAGER}
)

#: Editar-la es la mateixa classe d'acte que obrir-la. Es una constant propia i
#: no un alias perque son dues politiques, encara que avui coincideixin.
EDIT_ROLES = frozenset(CREATE_ROLES)

#: Els disparadors que queden escrits a `TenderRevision.trigger`, al costat
#: d'`ingestion`. Son dos i no un: obrir un expedient no es corregir-lo, i la
#: revisio base amb `manual_edit` faria semblar que algu ja l'havia tocat.
MANUAL_CREATE_TRIGGER = "manual_create"
MANUAL_EDIT_TRIGGER = "manual_edit"

#: Sentinella per distingir "no toquis aquest camp" de "posa-hi NULL", el
#: mateix patro que ja fa servir el servei de l'oferta economica.
UNSET = object()

#: Els camps que una persona pot corregir. Tots existeixen ja al model.
#:
#: Els que son al snapshot canonic de `tender_lifecycle.build_snapshot`
#: produeixen revisio quan canvien; `expediente_number` i `country_code` no hi
#: son, aixi que canviar-los no en genera cap. No s'hi afegeixen a la fotografia
#: per aconseguir-ho: el snapshot descriu el que un organ publica, i decidir que
#: hi entra no es feina d'aquesta fase.
EDITABLE_FIELDS = (
    "title",
    "description",
    "expediente_number",
    "procedure_type",
    "contract_type",
    "estimated_value",
    "base_budget",
    "base_budget_without_tax",
    "currency",
    "publication_date",
    "deadline_at",
    "place_of_execution",
    "nuts_code",
    "country_code",
)

#: El codi de la font manual. Unic a `sources.code`, aixi que serveix de clau.
MANUAL_SOURCE_CODE = "manual"
MANUAL_SOURCE_NAME = "Importacio manual"

#: Els camps que identifiquen materialment una licitacio per a un mateix token.
#: Un reintent els ha de portar iguals; la resta —descripcio, imports, dates—
#: son descriptius i s'editen despres, aixi que no bloquegen el reintent.
MATERIAL_FIELDS = ("title", "expediente_number", "buyer_id")

#: El mateix, per a un organisme escrit a ma. Com que a Sprint 3 no hi ha PATCH
#: de `Buyer`, acceptar un token repetit amb un nom diferent voldria dir triar
#: en silenci quina de les dues versions es queda. No es tria: es diu.
BUYER_MATERIAL_FIELDS = ("name", "tax_identifier", "nuts_code")


class ManualTenderConflictError(ConflictError):
    """Mateix token, licitacio diferent. No es un reintent."""

    code = "manual_tender_payload_conflict"


class ManualTenderOwnershipError(ConflictError):
    """El token ja existeix i es d'una altra empresa."""

    code = "manual_tender_owned_by_other_company"


class ManualBuyerOwnershipError(ConflictError):
    """El token d'organisme ja existeix i es d'una altra empresa."""

    code = "manual_buyer_owned_by_other_company"


class ManualBuyerConflictError(ConflictError):
    """Mateix token d'organisme, organisme diferent. No es un reintent."""

    code = "manual_buyer_payload_conflict"


class TenderNotManualError(ValidationError):
    """Nomes s'editen les licitacions obertes a ma. Les d'una font, no."""

    code = "tender_not_manual"


class TenderRevisionConflictError(ConflictError):
    """Algu altre l'ha canviada des que la vas llegir."""

    code = "tender_revision_conflict"


class BuyerChoiceError(ValidationError):
    """`buyer_id` i `buyer_manual` son excloents."""

    code = "buyer_choice_ambiguous"


@dataclass(frozen=True, slots=True)
class ManualBuyerInput:
    client_token: uuid.UUID
    name: str
    tax_identifier: str | None = None
    nuts_code: str | None = None


@dataclass(frozen=True, slots=True)
class ManualTenderInput:
    client_token: uuid.UUID
    title: str
    expediente_number: str | None = None
    buyer_id: uuid.UUID | None = None
    buyer_manual: ManualBuyerInput | None = None


def assert_can_create(role: MembershipRole) -> None:
    if role not in CREATE_ROLES:
        raise NotFoundError("No es pot obrir una licitacio amb aquest rol.")


def external_id_for(company_id: uuid.UUID, client_token: uuid.UUID) -> str:
    """`manual:<company>:<token>`. Determinista, sintetic, opac i immutable.

    Porta l'empresa perque la font MANUAL es una i global, i amb ella ho es
    tambe `UNIQUE(source_id, external_id)`. Sense el prefix, que l'empresa A
    fes servir un token no deixaria que l'empresa B fes servir el mateix, i
    l'espai de claus de A limitaria el de B. La clau d'idempotencia conceptual
    es **`(company_id, client_token)`**, i aixi s'escriu.

    No es deriva de `Tender.id` perque el default d'`UUIDPrimaryKeyMixin`
    s'avalua a l'INSERT: abans del flush l'objecte encara no en te.

    80 caracters, dins de `tenders.external_id` (500) i de
    `buyers.external_identifier` (200).
    """
    return f"manual:{company_id}:{client_token}"


async def get_or_create_manual_source(session: AsyncSession) -> Source:
    """L'unica font MANUAL. Idempotent per `UNIQUE(sources.code)`.

    No en depen cap nom introduit per l'usuari i no n'hi ha una per empresa:
    la font es provinenca, no autoritzacio.
    """
    source = (
        await session.execute(sa.select(Source).where(Source.code == MANUAL_SOURCE_CODE))
    ).scalars().one_or_none()
    if source is not None:
        return source
    source = Source(
        code=MANUAL_SOURCE_CODE,
        name=MANUAL_SOURCE_NAME,
        source_type=SourceType.MANUAL,
        enabled=True,
    )
    session.add(source)
    await session.flush()
    return source


async def _resolve_existing_buyer(
    session: AsyncSession, company_id: uuid.UUID, buyer_id: uuid.UUID
) -> Buyer:
    """Un organisme global o de la mateixa empresa. Mai d'una altra.

    404 i no 403, com la resta d'objectes: dir "no pots" ja seria dir que hi es.
    """
    buyer = (
        await session.execute(
            sa.select(Buyer).where(
                Buyer.id == buyer_id, buyer_visible_to_company(company_id)
            )
        )
    ).scalars().one_or_none()
    if buyer is None:
        raise NotFoundError("Organisme no trobat.")
    return buyer


async def _get_or_create_manual_buyer(
    session: AsyncSession,
    source: Source,
    company_id: uuid.UUID,
    data: ManualBuyerInput,
) -> Buyer:
    """Un organisme privat, idempotent pel seu propi token."""
    external = external_id_for(company_id, data.client_token)
    existing = (
        await session.execute(
            sa.select(Buyer).where(
                Buyer.source_id == source.id, Buyer.external_identifier == external
            )
        )
    ).scalars().one_or_none()
    if existing is not None:
        # Amb el prefix d'empresa aixo ja no hauria de passar mai. Es queda com
        # a defensa: es un limit de tenancy, i tres linies hi son barates.
        if existing.owner_company_id != company_id:
            raise ManualBuyerOwnershipError("Aquest organisme es d'una altra empresa.")
        wanted = {
            "name": data.name,
            "tax_identifier": data.tax_identifier,
            "nuts_code": data.nuts_code,
        }
        if any(getattr(existing, f) != wanted[f] for f in BUYER_MATERIAL_FIELDS):
            raise ManualBuyerConflictError(
                "Aquest token ja identifica un organisme diferent."
            )
        return existing

    buyer = Buyer(
        source_id=source.id,
        external_identifier=external,
        owner_company_id=company_id,
        name=data.name,
        tax_identifier=data.tax_identifier,
        nuts_code=data.nuts_code,
    )
    session.add(buyer)
    await session.flush()
    return buyer


def _differs(tender: Tender, *, buyer_id: uuid.UUID | None, data: ManualTenderInput) -> bool:
    wanted = {"title": data.title, "expediente_number": data.expediente_number,
              "buyer_id": buyer_id}
    return any(getattr(tender, field) != wanted[field] for field in MATERIAL_FIELDS)


async def create_manual_tender(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    user: User,
    data: ManualTenderInput,
) -> tuple[Tender, bool]:
    """Retorna la licitacio i si s'ha creat ara.

    Idempotent: amb el mateix `company_id` i el mateix `client_token` sempre
    surt la mateixa licitacio, i el segon cop `created` es `False`. No es fa
    cap escriptura fins que tot esta resolt, de manera que si la licitacio no
    es pot crear no queda cap organisme manual orfe: la transaccio es una.
    """
    if data.buyer_id is not None and data.buyer_manual is not None:
        raise BuyerChoiceError("Cal triar un organisme existent o escriure'n un, no tots dos.")

    source = await get_or_create_manual_source(session)
    external = external_id_for(company_id, data.client_token)

    existing = (
        await session.execute(
            sa.select(Tender).where(
                Tender.source_id == source.id, Tender.external_id == external
            )
        )
    ).scalars().one_or_none()

    if existing is not None:
        if existing.owner_company_id != company_id:
            raise ManualTenderOwnershipError("Aquesta licitacio es d'una altra empresa.")
        # Un reintent ha de portar la mateixa licitacio. Si en porta una altra,
        # sobreescriure-la en silenci seria perdre la que ja hi havia.
        buyer_id = existing.buyer_id
        if data.buyer_id is not None:
            buyer_id = (await _resolve_existing_buyer(session, company_id, data.buyer_id)).id
        elif data.buyer_manual is not None:
            buyer_id = (
                await _get_or_create_manual_buyer(session, source, company_id, data.buyer_manual)
            ).id
        if _differs(existing, buyer_id=buyer_id, data=data):
            raise ManualTenderConflictError(
                "Aquest token ja identifica una licitacio diferent."
            )
        return existing, False

    buyer_id: uuid.UUID | None = None
    if data.buyer_id is not None:
        buyer_id = (await _resolve_existing_buyer(session, company_id, data.buyer_id)).id
    elif data.buyer_manual is not None:
        buyer_id = (
            await _get_or_create_manual_buyer(session, source, company_id, data.buyer_manual)
        ).id

    tender = Tender(
        source_id=source.id,
        external_id=external,
        owner_company_id=company_id,
        title=data.title,
        expediente_number=data.expediente_number,
        buyer_id=buyer_id,
    )
    session.add(tender)
    await session.flush()
    # Revisio 1 des del primer moment. Sense aquesta linia, una licitacio manual
    # no tindria historia: el primer PATCH no tindria amb que comparar-se i
    # `evaluate_staleness` d'un paquet congelat diria `tender_revision_unknown`
    # en comptes de poder afirmar sobre quina versio es va congelar.
    await tender_lifecycle.record_revision(
        session, tender, trigger=MANUAL_CREATE_TRIGGER
    )
    return tender, True


def audit_summary(**fields: object) -> str:
    return " ".join(f"{key}={value}" for key, value in fields.items())


# --------------------------------------------------------------------------- #
# Edicio (S3-S1b)                                                               #
# --------------------------------------------------------------------------- #
#
# No hi ha historial paral·lel. L'unica porta es `tender_lifecycle`, la mateixa
# que fa servir la ingesta, i amb ella venen de franc tres coses que aqui no
# s'han de reimplementar: la idempotencia —mateixa fotografia, mateix hash, cap
# revisio—, la distincio material/descriptiu —`TenderChangeEvent.is_material`,
# que ja existeix— i la traçabilitat que llegeixen l'staleness del paquet i el
# pre-fit.


def assert_can_edit(role: MembershipRole) -> None:
    if role not in EDIT_ROLES:
        raise NotFoundError("No es pot editar una licitacio amb aquest rol.")


def is_manual(tender: Tender, source: Source) -> bool:
    """Manual vol dir que ve de la font MANUAL. No es dedueix del format de res."""
    return tender.source_id == source.id


async def load_editable_tender(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> Tender:
    """La licitacio a editar, si aquesta empresa la pot editar.

    Una licitacio d'una altra empresa dona 404, com arreu: dir «no pots» ja
    seria dir que existeix. Una de publica existeix per a tothom, aixi que
    aquesta si que pot dir que no s'edita per aquesta via.
    """
    tender = (
        await session.execute(
            sa.select(Tender).where(Tender.id == tender_id, visible_to_company(company_id))
        )
    ).scalars().one_or_none()
    if tender is None:
        raise NotFoundError("Licitacio no trobada.")

    source = await get_or_create_manual_source(session)
    if not is_manual(tender, source):
        raise TenderNotManualError("Aquesta licitacio no s'ha obert a ma.")
    return tender


async def current_revision_number(session: AsyncSession, tender_id: uuid.UUID) -> int:
    """El numero de la revisio vigent. Zero quan encara no n'hi ha cap."""
    revision = await tender_lifecycle.latest_revision(session, tender_id)
    return revision.revision_number if revision is not None else 0


async def update_manual_tender(
    session: AsyncSession,
    *,
    tender: Tender,
    company_id: uuid.UUID,
    expected_revision: int,
    fields: dict[str, object],
    buyer_id: uuid.UUID | None | object = UNSET,
    buyer_manual: ManualBuyerInput | None = None,
) -> tuple[Tender, bool]:
    """Aplica els camps demanats i deixa que `tender_lifecycle` decideixi.

    Retorna la licitacio i si ha calgut una revisio nova. Si els valors son els
    que ja hi havia, la fotografia dona el mateix hash i no es crea res: ni
    revisio, ni events, ni obsolescencia inventada.
    """
    if buyer_id is not UNSET and buyer_manual is not None:
        raise BuyerChoiceError("Cal triar un organisme existent o escriure'n un, no tots dos.")

    actual = await current_revision_number(session, tender.id)
    if actual != expected_revision:
        raise TenderRevisionConflictError(
            "La licitacio ha canviat des que la vas llegir.",
            details={"expected_revision": expected_revision, "current_revision": actual},
        )

    for name, value in fields.items():
        if name not in EDITABLE_FIELDS:  # pragma: no cover - el schema ja ho impedeix
            raise ValidationError(f"Camp no editable: {name}")
        setattr(tender, name, value)

    if buyer_manual is not None:
        source = await get_or_create_manual_source(session)
        tender.buyer_id = (
            await _get_or_create_manual_buyer(session, source, company_id, buyer_manual)
        ).id
    elif buyer_id is not UNSET:
        # `None` explicit vol dir treure'l; ometre el camp vol dir no tocar-lo.
        tender.buyer_id = (
            None
            if buyer_id is None
            else (await _resolve_existing_buyer(session, company_id, buyer_id)).id  # type: ignore[arg-type]
        )

    await session.flush()
    outcome = await tender_lifecycle.record_revision(
        session, tender, trigger=MANUAL_EDIT_TRIGGER
    )
    return tender, not outcome.unchanged
