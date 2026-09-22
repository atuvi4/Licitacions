"""Enregistrar que una licitacio ha canviat, i deduir que deixa de ser vigent.

Una sola porta d'entrada: `record_revision()`. La crida la ingestió quan
reingereix un expedient i el processat de documents quan un plec baixa amb un
contingut nou. Les dues coses son canvis de la licitacio i han de deixar el
mateix rastre.

**Idempotent per construccio.** La fotografia es normalitza i s'hi calcula un
hash; si coincideix amb el de l'ultima revisio, no passa res: ni revisio nova,
ni events, ni avisos. Reingerir el mateix feed cent vegades deixa el sistema
exactament igual.

El que aquest modul **no** fa, i es deliberat: no toca cap decisio humana, no
esborra cap run i no modifica cap paquet. La invalidacio d'aquest sistema ja es
**derivada** —el workspace compara identificadors de run, el paquet recalcula el
seu manifest, el compliment compara empremtes— i afegir-hi banderes que algu
hagi de mantenir sincronitzades seria una segona font de veritat destinada a
divergir. Aqui nomes s'escriu historia; qui la llegeix en treu les conclusions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SourceType
from app.models.sources import Source
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Tender, TenderCPV, TenderDocument, TenderLot, TenderLotCPV
from app.tenders.changes import (
    NOT_ACTIONABLE_STATUSES,
    Change,
    ChangeImpact,
    ChangeSummary,
    canonical_instant,
    diff_snapshots,
    snapshot_hash,
    summarise,
)


@dataclass(frozen=True, slots=True)
class RevisionOutcome:
    revision: TenderRevision
    #: Cert quan no hi havia res de nou i s'ha reaprofitat la revisio anterior.
    unchanged: bool
    summary: ChangeSummary

    @property
    def is_material(self) -> bool:
        return not self.unchanged and self.summary.is_material


def _money(value: Any) -> str | None:
    """Els imports es comparen com a text normalitzat.

    `Decimal("1000")` i `Decimal("1000.00")` son el mateix import i han de donar
    el mateix hash; si no, un canvi d'escala de la font semblaria una
    modificacio del pressupost i despertaria mig sistema.
    """
    if value is None:
        return None
    from decimal import Decimal

    return format(Decimal(str(value)).normalize(), "f")


async def build_snapshot(session: AsyncSession, tender: Tender) -> dict[str, Any]:
    """L'estat normalitzat sencer d'una licitacio, tal com esta ara.

    Inclou el SHA-256 dels documents que ja s'han baixat. Es el que permet
    distingir "la mateixa URL serveix un plec nou" de "el plec es el mateix i
    nomes ha canviat l'adreça". Als que encara no s'han baixat el SHA es `None`:
    es una limitacio real i es preferible a inventar-se una identitat.

    **Tots els instants hi entren en UTC** (`canonical_instant`). El mateix
    moment escrit amb dos offsets diferents ha de donar el mateix hash; si no,
    tornar a construir la fotografia sense que l'organ hagi tocat res genera una
    revisio i un `DEADLINE_CHANGED` material que no ha passat.

    A una licitacio **oberta a ma** s'hi afegeixen `expediente_number` i
    `country_code` (`MANUAL_ONLY_FIELDS`). A una de sindicada no: els fixa la
    font, ningu no els corregeix, i posar-los-hi faria semblar canviades totes
    les que ja hi ha. A una de manual son editables, i un camp editable fora de
    la fotografia deixaria un forat al CAS —dos clients que han llegit la
    mateixa revisio podrien escriure tots dos sense assabentar-se'n.
    """
    cpvs = (
        await session.execute(
            sa.select(TenderCPV.cpv_code, TenderCPV.is_main)
            .where(TenderCPV.tender_id == tender.id)
            .order_by(TenderCPV.cpv_code)
        )
    ).all()

    lots = (
        await session.scalars(
            sa.select(TenderLot)
            .where(TenderLot.tender_id == tender.id)
            .order_by(TenderLot.external_lot_id)
        )
    ).all()
    lot_cpvs: dict[uuid.UUID, list[str]] = {}
    if lots:
        rows = (
            await session.execute(
                sa.select(TenderLotCPV.tender_lot_id, TenderLotCPV.cpv_code).where(
                    TenderLotCPV.tender_lot_id.in_([lot.id for lot in lots])
                )
            )
        ).all()
        for lot_id, code in rows:
            lot_cpvs.setdefault(lot_id, []).append(code)

    documents = (
        await session.scalars(
            sa.select(TenderDocument)
            .where(TenderDocument.tender_id == tender.id)
            .order_by(TenderDocument.url)
        )
    ).all()
    shas: dict[uuid.UUID, str] = {}
    if documents:
        rows = (
            await session.execute(
                sa.select(TenderDocumentFile.tender_document_id, TenderDocumentContent.sha256)
                .join(
                    TenderDocumentContent,
                    TenderDocumentContent.id == TenderDocumentFile.content_id,
                )
                .where(
                    TenderDocumentFile.tender_document_id.in_([d.id for d in documents]),
                    TenderDocumentFile.is_current.is_(True),
                )
            )
        ).all()
        shas = dict(rows)

    snapshot: dict[str, Any] = {
        "title": tender.title,
        "description": tender.description,
        "buyer": str(tender.buyer_id) if tender.buyer_id else None,
        "status": str(tender.status),
        "procedure_type": str(tender.procedure_type),
        "contract_type": str(tender.contract_type),
        "publication_date": str(tender.publication_date) if tender.publication_date else None,
        "deadline_at": canonical_instant(tender.deadline_at),
        "estimated_value": _money(tender.estimated_value),
        "base_budget": _money(tender.base_budget),
        "base_budget_without_tax": _money(tender.base_budget_without_tax),
        "currency": tender.currency,
        "place_of_execution": tender.place_of_execution,
        "nuts_code": tender.nuts_code,
        "cpvs": [{"code": code, "is_main": bool(is_main)} for code, is_main in cpvs],
        "lots": [
            {
                "external_lot_id": lot.external_lot_id,
                "lot_number": lot.lot_number,
                "title": lot.title,
                "estimated_value": _money(lot.estimated_value),
                "base_budget": _money(lot.base_budget),
                "currency": lot.currency,
                # El termini del lot mana sobre el de l'expedient per a qui s'hi
                # presenta: si es mou, no pot ser invisible per a la historia.
                "deadline_at": canonical_instant(lot.deadline_at),
                # Un lot que ja no es al feed es queda a la fotografia marcat.
                # No s'esborra la fila: una candidatura oberta hi apunta.
                "removed": lot.removed_at is not None,
                "cpvs": sorted(lot_cpvs.get(lot.id, [])),
            }
            for lot in lots
        ],
        "documents": [
            {
                "url": document.url,
                "name": document.name,
                "document_type": str(document.document_type),
                "published_at": canonical_instant(document.published_at),
                "sha256": shas.get(document.id),
            }
            for document in documents
        ],
    }

    if await is_manual_tender(session, tender):
        snapshot["expediente_number"] = tender.expediente_number
        snapshot["country_code"] = tender.country_code
    return snapshot


async def is_manual_tender(session: AsyncSession, tender: Tender) -> bool:
    """Si la licitacio ve de la font MANUAL.

    Es mira la font i no el disparador de la crida: que una licitacio s'hagi
    obert a ma es una propietat seva, no de qui en desa la revisio.
    """
    source_type = await session.scalar(
        sa.select(Source.source_type).where(Source.id == tender.source_id)
    )
    return source_type == SourceType.MANUAL


async def latest_revision(session: AsyncSession, tender_id: uuid.UUID) -> TenderRevision | None:
    return await session.scalar(
        sa.select(TenderRevision)
        .where(TenderRevision.tender_id == tender_id)
        .order_by(TenderRevision.revision_number.desc())
        .limit(1)
    )


async def record_revision(
    session: AsyncSession,
    tender: Tender,
    *,
    source_record_id: uuid.UUID | None = None,
    trigger: str = "ingestion",
) -> RevisionOutcome:
    """Desa una revisio nova si l'estat ha canviat. Si no, no fa res.

    Retorna sempre la revisio vigent, hagi calgut crear-la o no, de manera que
    qui la crida pugui lligar-hi una decisio o un run sense preguntar-se si hi
    havia canvis.
    """
    snapshot = await build_snapshot(session, tender)
    digest = snapshot_hash(snapshot)
    previous = await latest_revision(session, tender.id)

    if previous is not None and previous.normalized_hash == digest:
        # Mateix contingut: ni revisio, ni events, ni avisos. Es tot el
        # mecanisme d'idempotencia, i viu en aquestes tres linies.
        return RevisionOutcome(revision=previous, unchanged=True, summary=summarise([]))

    changes: list[Change] = (
        diff_snapshots(previous.normalized_snapshot, snapshot) if previous is not None else []
    )
    summary = summarise(changes)

    revision = TenderRevision(
        tender_id=tender.id,
        revision_number=(previous.revision_number if previous else 0) + 1,
        source_record_id=source_record_id,
        normalized_snapshot=snapshot,
        normalized_hash=digest,
        change_summary=summary.as_dict() if previous is not None else None,
        trigger=trigger,
    )
    session.add(revision)
    await session.flush()

    for change in changes:
        session.add(
            TenderChangeEvent(
                tender_id=tender.id,
                from_revision_id=previous.id if previous else None,
                to_revision_id=revision.id,
                change_code=change.code.value,
                subject=(change.subject or None) and str(change.subject)[:500],
                old_value=_jsonable(change.old_value),
                new_value=_jsonable(change.new_value),
                impacts=sorted(impact.value for impact in change.impacts),
                is_material=change.is_material,
            )
        )
    await session.flush()
    return RevisionOutcome(revision=revision, unchanged=False, summary=summary)


def _jsonable(value: Any) -> Any:
    """JSON admet pocs tipus; la resta es desa com a text."""
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


# --------------------------------------------------------------------------- #
# Lectura: que ha canviat des d'una revisio                                     #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ChangeLog:
    """Els canvis materials d'una licitacio, llegits un sol cop.

    Cada artefacte —el pre-fit, l'extraccio de requisits, el compliment, el
    paquet, la decisio humana— es va calcular sobre una revisio concreta i es
    pregunta el mateix: **que ha passat des de llavors que m'afecti a mi?** Amb
    la matriu d'impacte, "que m'afecti a mi" te resposta exacta, i per aixo un
    canvi de titol no fa semblar caducat un compliment.

    Nomes hi entren els canvis MATERIALS. Els informatius son historia i es
    llegeixen a l'historial de la licitacio; si arribessin fins aqui, cada
    correccio d'errades encendria mitja pantalla d'avisos i el dia que canvies
    un plec de debo ja no el miraria ningu.
    """

    #: La revisio vigent. `None` = d'aquesta licitacio encara no se n'ha
    #: enregistrat cap, que no es el mateix que dir que no ha canviat.
    latest: TenderRevision | None
    #: `(numero de revisio, event)`, de la mes antiga a la mes recent.
    entries: tuple[tuple[int, TenderChangeEvent], ...] = ()

    def basis_unknown(self, basis: TenderRevision | None) -> bool:
        """Cert quan no es pot saber si el que es va calcular segueix valid.

        Passa amb el que es d'abans que existissin revisions: la licitacio ja te
        historia, pero d'aquell calcul no en sabem el punt de partida. **No es
        el mateix que estar al dia**, i tractar-ho com si ho fos seria la
        mentida mes caduca del sistema: diria "vigent" sense haver-ho comprovat.
        """
        return basis is None and self.latest is not None

    def since(self, basis: TenderRevision | None) -> tuple[TenderChangeEvent, ...]:
        """Els canvis materials posteriors a una revisio."""
        if basis is None:
            return ()
        return tuple(
            event for number, event in self.entries if number > basis.revision_number
        )

    def impacts_since(self, basis: TenderRevision | None) -> frozenset[ChangeImpact]:
        result: set[ChangeImpact] = set()
        for event in self.since(basis):
            for impact in event.impacts or []:
                try:
                    result.add(ChangeImpact(impact))
                except ValueError:  # pragma: no cover - codi d'una versio antiga
                    continue
        return frozenset(result)

    def is_outdated(self, basis: TenderRevision | None, impact: ChangeImpact) -> bool:
        """Si el que es va calcular sobre `basis` ja no val per a aquest impacte.

        Una base desconeguda NO compta com a obsoleta: obligaria a recalcular-ho
        tot el dia que aixo s'estreni, i el motiu seria fals. Qui necessiti
        distingir-ho te `basis_unknown`, i la decisio humana —l'unic artefacte
        que no es pot recalcular sol— si que ho distingeix.
        """
        return impact in self.impacts_since(basis)


async def load_change_log(session: AsyncSession, tender_id: uuid.UUID) -> ChangeLog:
    """Llegeix la historia material d'una licitacio en una sola consulta."""
    rows = (
        await session.execute(
            sa.select(TenderRevision.revision_number, TenderChangeEvent)
            .join(TenderRevision, TenderRevision.id == TenderChangeEvent.to_revision_id)
            .where(
                TenderChangeEvent.tender_id == tender_id,
                TenderChangeEvent.is_material.is_(True),
            )
            .order_by(TenderRevision.revision_number, TenderChangeEvent.change_code)
        )
    ).all()
    return ChangeLog(
        latest=await latest_revision(session, tender_id),
        entries=tuple((number, event) for number, event in rows),
    )


async def revision_of(
    session: AsyncSession, revision_id: uuid.UUID | None
) -> TenderRevision | None:
    """La revisio d'un identificador que pot ser `None`, sense repetir el `if`."""
    if revision_id is None:
        return None
    return await session.get(TenderRevision, revision_id)


def is_actionable(tender: Tender) -> bool:
    """Si encara es pot preparar i presentar una oferta a aquest expedient.

    Nomes es fa cas del que la font distingeix de veritat. No s'hi inventa cap
    mapatge legal: si PLACSP no diu clarament que l'expedient esta anul.lat,
    aqui no s'hi decideix.
    """
    return str(tender.status) not in NOT_ACTIONABLE_STATUSES


def lot_is_gone(lot: TenderLot | None) -> bool:
    """Si el lot d'una candidatura ja no es al feed.

    La font no publica cap "lot anul.lat" que es pugui distingir, aixi que
    desapareixer ES el senyal i no s'hi inventa res mes. Per a qui es presentava
    a aquell lot equival a quedar-se sense expedient: el que preparava ja no te
    on anar, encara que la licitacio segueixi viva per als altres lots.
    """
    return lot is not None and lot.removed_at is not None
