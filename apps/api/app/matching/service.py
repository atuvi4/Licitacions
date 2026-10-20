"""Calcul i persistencia del pre-fit.

Aquest modul es l'unic del paquet que toca la base de dades. El motor
(`engine.py`) es pur i es pot provar sol.

Rendiment: el perfil de l'empresa es carrega UNA vegada i les licitacions es
llegeixen en bloc amb `selectinload`, de manera que puntuar 300 expedients son
poques consultes i no centenars.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload, selectinload

from app.matching.economic import AmountBasis, AmountReference, reference_amount
from app.matching.engine import MatchOutcome, aggregate_lots, evaluate
from app.matching.profile import CompanyMatchProfile, load_profile
from app.matching.rules import RULES_VERSION
from app.models.matching import CompanyTenderLotMatch, CompanyTenderMatch
from app.models.tender_lifecycle import TenderRevision
from app.models.tenders import Tender, TenderLot
from app.tenders.lot_amounts import LotAmounts, load_lot_amounts, resolve
from app.tenders.visibility import visible_to_company

logger = logging.getLogger("app.matching")


@dataclass(slots=True)
class RecalculationSummary:
    company_id: uuid.UUID
    tenders_evaluated: int = 0
    lots_evaluated: int = 0
    created: int = 0
    updated: int = 0
    duration_ms: int = 0
    rules_version: str = RULES_VERSION

    def as_log(self) -> dict[str, object]:
        """Camps per a `logging.info(extra=...)`.

        Els noms van prefixats amb `match_` a proposit: `LogRecord` ja te
        atributs propis anomenats `created`, `msg`, `name` o `module`, i passar
        un `extra` que en repeteixi cap fa petar el logger amb un KeyError.
        """
        return {
            "company_id": str(self.company_id),
            "match_tenders": self.tenders_evaluated,
            "match_lots": self.lots_evaluated,
            "match_created": self.created,
            "match_updated": self.updated,
            "match_duration_ms": self.duration_ms,
            "match_rules_version": self.rules_version,
        }


def evaluate_tender(
    profile: CompanyMatchProfile,
    tender: Tender,
    now: datetime | None = None,
    lot_amounts: Mapping[uuid.UUID, LotAmounts] | None = None,
) -> tuple[MatchOutcome, list[tuple[TenderLot, MatchOutcome]]]:
    """Puntua un expedient i, si en te, cadascun dels seus lots.

    `lot_amounts` (F-06e): els imports RESOLTS de cada lot (`app.tenders.lot_amounts`:
    PLACSP > 0, si no la PSCP `accepted`). Un lot que no hi es es resol nomes amb les
    seves columnes (el mateix que abans de F-06e).
    """
    tender_cpvs = [item.cpv_code for item in tender.cpvs]
    # v2 (F-06b): VEC → PBL sense IVA → PBL amb IVA, amb la base identificada.
    tender_amount = reference_amount(
        tender.estimated_value, tender.base_budget_without_tax, tender.base_budget
    )
    status = tender.status.value
    # Els lots que han desaparegut del feed es queden a la base de dades perque
    # una candidatura hi pot apuntar, pero no son candidats a res: puntuar-los
    # els faria sortir a la llista d'oportunitats com si encara existissin.
    lots = [lot for lot in tender.lots if lot.removed_at is None]

    if not lots:
        outcome = evaluate(
            profile,
            cpv_codes=tender_cpvs,
            nuts_code=tender.nuts_code,
            place_name=tender.place_of_execution,
            amount=tender_amount,
            tender_status=status,
            deadline=tender.deadline_at,
            now=now,
        )
        return outcome, []

    lot_results: list[tuple[TenderLot, MatchOutcome]] = []
    for lot in lots:
        # CPV del lot si en te; si no, els de l'expedient. Un lot sense CPV
        # propis hereta l'objecte del contracte, no es queda sense classificar.
        lot_cpvs = [item.cpv_code for item in lot.cpvs] or tender_cpvs
        # NUTS: el del lot mana; si no en te, s'aplica el de l'expedient.
        lot_nuts = lot.nuts_code or tender.nuts_code
        lot_place = lot.place_of_execution or tender.place_of_execution
        # Import: NOMES el del lot. No es reparteix ni s'hereta el total de
        # l'expedient, que donaria una xifra falsa per a cada lot. (F-06e) Els imports
        # resolts del lot: PLACSP > 0 i, si no, la PSCP `accepted`, amb la mateixa
        # `reference_amount` (VEC principal, PBL net cota inferior, R1, brut sol).
        resolved = lot_amounts.get(lot.id) if lot_amounts is not None else None
        if resolved is None:
            resolved = resolve(lot, {})
        if resolved.estimated_value.value is None and resolved.vec_incoherent is not None:
            # La PSCP publica un VEC del lot inferior al seu PBL net (en `conflict` per
            # `vec_order`): incoherent, com a l'expedient; mai el net com a cota inferior.
            vec, net = resolved.vec_incoherent
            lot_amount = AmountReference(vec, AmountBasis.VEC, incoherent_budget=net)
        else:
            lot_amount = reference_amount(
                resolved.estimated_value.value,
                resolved.base_budget_without_tax.value,
                resolved.base_budget.value,
            )
        lot_results.append(
            (
                lot,
                evaluate(
                    profile,
                    cpv_codes=lot_cpvs,
                    nuts_code=lot_nuts,
                    place_name=lot_place,
                    amount=lot_amount,
                    tender_status=status,
                    deadline=lot.deadline_at or tender.deadline_at,
                    now=now,
                ),
            )
        )

    summary = aggregate_lots([outcome for _, outcome in lot_results])
    return summary, lot_results


class MatchingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._preserve_unchanged = False

    async def _tenders(
        self, tender_ids: list[uuid.UUID] | None, *, company_id: uuid.UUID
    ) -> list[Tender]:
        """Les licitacions que aquesta empresa pot arribar a valorar.

        El filtre de visibilitat NO es opcional: sense ell, un recalcul de
        l'empresa B crearia files de match sobre licitacions privades de
        l'empresa A, i el feed de B les ensenyaria totes. Per aixo `company_id`
        es obligatori i va per paraula clau: afegir-lo com a opcional hauria
        deixat viva la crida antiga, que es exactament la que filtrava.
        """
        statement = (
            sa.select(Tender)
            .where(visible_to_company(company_id))
            .options(
                selectinload(Tender.cpvs),
                selectinload(Tender.lots).selectinload(TenderLot.cpvs),
                noload(Tender.documents),
            )
        )
        if tender_ids:
            statement = statement.where(Tender.id.in_(tender_ids))
        return list((await self.session.execute(statement)).scalars().unique().all())

    async def _current_revisions(self, tenders: list[Tender]) -> dict[uuid.UUID, uuid.UUID]:
        """La revisio vigent de cada licitacio, en una sola consulta.

        Es desa amb el match perque despres es pugui preguntar nomes pels canvis
        que afecten el pre-fit. Una consulta per licitacio dins del bucle
        convertiria un recalcul massiu en milers d'anades i tornades.
        """
        if not tenders:
            return {}
        ids = [tender.id for tender in tenders]
        newest = (
            sa.select(
                TenderRevision.tender_id,
                sa.func.max(TenderRevision.revision_number).label("revision_number"),
            )
            .where(TenderRevision.tender_id.in_(ids))
            .group_by(TenderRevision.tender_id)
            .subquery()
        )
        rows = (
            await self.session.execute(
                sa.select(TenderRevision.tender_id, TenderRevision.id).join(
                    newest,
                    sa.and_(
                        TenderRevision.tender_id == newest.c.tender_id,
                        TenderRevision.revision_number == newest.c.revision_number,
                    ),
                )
            )
        ).all()
        return dict(rows)

    async def recalculate(
        self,
        company_id: uuid.UUID,
        *,
        tender_ids: list[uuid.UUID] | None = None,
        now: datetime | None = None,
        commit: bool = True,
        preserve_unchanged: bool = False,
    ) -> RecalculationSummary:
        """Recalcula el pre-fit d'una empresa. Actualitza, no acumula.

        `commit=False` deixa els canvis a la sessio sense confirmar (el recalcul
        controlat els compara i decideix). `preserve_unchanged=True` no toca cap
        fila el resultat de la qual no canvia, ni tan sols `calculated_at`: aixi
        tornar a executar el mateix recalcul no escriu res (idempotent).
        """
        self._preserve_unchanged = preserve_unchanged
        started = time.monotonic()
        moment = now or datetime.now(UTC)
        summary = RecalculationSummary(company_id=company_id)

        profile = await load_profile(self.session, company_id)
        tenders = await self._tenders(tender_ids, company_id=company_id)
        revisions = await self._current_revisions(tenders)
        # (F-06e) Imports resolts de tots els lots vigents, en bloc (mai una consulta
        # per lot).
        lot_amounts = await load_lot_amounts(
            self.session,
            [lot for tender in tenders for lot in tender.lots if lot.removed_at is None],
        )

        existing = {
            match.tender_id: match
            for match in (
                await self.session.execute(
                    sa.select(CompanyTenderMatch)
                    .where(CompanyTenderMatch.company_id == company_id)
                    .options(selectinload(CompanyTenderMatch.lot_matches))
                )
            ).scalars().unique()
        }

        for tender in tenders:
            outcome, lot_results = evaluate_tender(profile, tender, moment, lot_amounts)
            summary.tenders_evaluated += 1
            summary.lots_evaluated += len(lot_results)

            match = existing.get(tender.id)
            if match is None:
                match = CompanyTenderMatch(company_id=company_id, tender_id=tender.id)
                # La col.leccio s'inicialitza a ma: en un objecte acabat de
                # crear, llegir-la despres del flush dispararia una carrega
                # mandrosa, i amb SQLAlchemy async aixo peta (MissingGreenlet).
                match.lot_matches = []
                self.session.add(match)
                summary.created += 1
                self._apply(match, outcome, moment, profile, tender, revisions.get(tender.id))
            elif self._apply(match, outcome, moment, profile, tender, revisions.get(tender.id)):
                # Amb `preserve_unchanged`, una fila que no canvia no compta com a actualitzada.
                summary.updated += 1

            candidates = [
                (lot, lot_outcome)
                for lot, lot_outcome in lot_results
                if lot_outcome.is_candidate
            ]
            best = (
                max(
                    (pair for pair in lot_results if pair[1].score is not None),
                    key=lambda pair: pair[1].score or 0,
                    default=None,
                )
            )
            match.total_lot_count = len(lot_results)
            match.candidate_lot_count = len(candidates)
            match.best_lot_id = best[0].id if best else None

            await self.session.flush()
            await self._sync_lot_matches(
                match, lot_results, moment, profile, tender, revisions.get(tender.id)
            )

        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        summary.duration_ms = int((time.monotonic() - started) * 1000)
        logger.info("matching.recalculated", extra=summary.as_log())
        return summary

    def _apply(
        self,
        match: CompanyTenderMatch | CompanyTenderLotMatch,
        outcome: MatchOutcome,
        moment: datetime,
        profile: CompanyMatchProfile,
        tender: Tender,
        revision_id: uuid.UUID | None = None,
    ) -> bool:
        """Desa el resultat. `False` si no ha calgut tocar la fila (`preserve_unchanged`)."""
        values = {
            "score": outcome.score,
            "status": outcome.status,
            "exclusion_kind": outcome.exclusion_kind,
            "exclusion_reason_code": outcome.exclusion_reason_code,
            "cpv_score": outcome.cpv_score,
            "geographic_score": outcome.geographic_score,
            "economic_score": outcome.economic_score,
            "timing_score": outcome.timing_score,
            "data_coverage": outcome.data_coverage,
            "factors": outcome.factors,
            "rules_version": outcome.rules_version,
            # Instantanies per saber despres si el resultat ha quedat obsolet.
            "company_profile_changed_at": profile.profile_changed_at,
            "tender_changed_at": tender.last_changed_at,
            "tender_revision_id": revision_id,
        }
        if (
            self._preserve_unchanged
            and match.id is not None
            and match.calculated_at is not None
            and all(getattr(match, key) == value for key, value in values.items())
        ):
            return False
        for key, value in values.items():
            setattr(match, key, value)
        match.calculated_at = moment
        return True

    async def _sync_lot_matches(
        self,
        match: CompanyTenderMatch,
        lot_results: list[tuple[TenderLot, MatchOutcome]],
        moment: datetime,
        profile: CompanyMatchProfile,
        tender: Tender,
        revision_id: uuid.UUID | None = None,
    ) -> None:
        current = {lot_match.tender_lot_id: lot_match for lot_match in match.lot_matches}
        seen: set[uuid.UUID] = set()

        for lot, outcome in lot_results:
            seen.add(lot.id)
            lot_match = current.get(lot.id)
            if lot_match is None:
                lot_match = CompanyTenderLotMatch(
                    company_id=match.company_id,
                    tender_match_id=match.id,
                    tender_id=tender.id,
                    tender_lot_id=lot.id,
                )
                self.session.add(lot_match)
                match.lot_matches.append(lot_match)
            self._apply(lot_match, outcome, moment, profile, tender, revision_id)

        # Si un lot ha desaparegut de la font, el seu match no te sentit.
        for lot_id, lot_match in current.items():
            if lot_id not in seen:
                await self.session.delete(lot_match)


def is_stale(
    match: CompanyTenderMatch,
    profile_changed_at: datetime | None,
    tender_changed_at: datetime | None,
) -> bool:
    """Un match es obsolet si el perfil o la licitacio han canviat despres.

    No hi ha cap sistema d'esdeveniments al darrere: es una comparacio de
    marques de temps, que per a aquest volum es suficient i no s'ha d'anar a
    mantenir.
    """
    if match.rules_version != RULES_VERSION:
        return True
    if profile_changed_at is not None and (
        match.company_profile_changed_at is None
        or profile_changed_at > match.company_profile_changed_at
    ):
        return True
    if tender_changed_at is not None and (
        match.tender_changed_at is None or tender_changed_at > match.tender_changed_at
    ):
        return True
    return False
