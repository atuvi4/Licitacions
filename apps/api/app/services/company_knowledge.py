"""Coneixement d'una empresa: carregar-lo, fotografiar-lo i validar-lo.

Orquestracio de la fase 2 del motor d'analisi. Les regles son a
`app/knowledge/snapshot.py` (pures); aqui hi ha nomes la persistencia i els
actes humans:

* **Fotografia**: es carreguen les files d'UNA empresa (sempre filtrades per
  `company_id`) i es construeix el `KnowledgeSnapshot`.
* **Promoure una observacio**: una persona valida el que suggeria l'historic.
  Neix una fila de coneixement actual amb `knowledge_origin =
  promoted_observation` i sense verificar (la validacio no es evidencia), i
  l'observacio queda `promoted` apuntant-hi. No hi ha cap cami automatic.
* **Descartar-la**: queda `dismissed`, amb qui i per que.
* **Verificar una dada actual**: una persona confirma que hi ha evidencia.

Si una promocio xoca amb coneixement actual que ja existeix, es refusa: el que
s'ha declarat mana i no el trepitja cap suggeriment de l'historic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import sqlalchemy as sa
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.knowledge import proposals as P
from app.knowledge.snapshot import (
    CapabilityIn,
    CertificationIn,
    FactIn,
    KnowledgeInputs,
    KnowledgeSnapshot,
    ObservationIn,
    PartnerIn,
    PartnerRoleIn,
    ProductIn,
    Provenance,
    ServiceIn,
    SolutionIn,
    SolutionProductIn,
    build_snapshot,
)
from app.knowledge.vocabulary import validate_level
from app.models.credentials import CompanyCertification
from app.models.enums import (
    HistoricBidDecision,
    HistoricBidResult,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    ObservationStatus,
    ObservationSubject,
    VerificationStatus,
)
from app.models.identity import User
from app.models.knowledge import (
    CompanyOperatingFact,
    CompanyPartner,
    CompanyProduct,
    CompanySolution,
    HistoricBid,
    KnowledgeObservation,
    PartnerRoleRow,
    SolutionCapability,
    SolutionProduct,
)
from app.models.profile import CompanyService
from app.services import knowledge_evidence


class ObservationNotCandidateError(ConflictError):
    code = "observation_not_candidate"


class ObservationNotPromotableError(ValidationError):
    code = "observation_not_promotable"


class KnowledgeAlreadyDeclaredError(ConflictError):
    code = "knowledge_already_declared"


class InvalidProposalError(ValidationError):
    code = "invalid_observation_proposal"


# --------------------------------------------------------------------------- #
# Fotografia                                                                   #
# --------------------------------------------------------------------------- #


def _prov(
    row: Any,
    evidence: set[tuple[str, str]],
    *,
    lifecycle: KnowledgeLifecycle | None = None,
    valid_until=None,
) -> Provenance:
    entity_type = knowledge_evidence.ENTITY_TYPE_BY_MODEL[type(row)]
    return Provenance(
        record_id=str(row.id),
        origin=KnowledgeOrigin(row.knowledge_origin),
        as_of=row.as_of,
        valid_until=valid_until if valid_until is not None else getattr(row, "valid_until", None),
        verification=VerificationStatus(row.verification_status),
        lifecycle=KnowledgeLifecycle(lifecycle or row.lifecycle),
        has_evidence=(str(entity_type), str(row.id)) in evidence,
    )


async def _rows(session: AsyncSession, model: Any, company_id: uuid.UUID) -> list[Any]:
    return list(await session.scalars(sa.select(model).where(model.company_id == company_id)))


async def load_inputs(session: AsyncSession, company_id: uuid.UUID) -> KnowledgeInputs:
    """Les files d'UNA empresa. Cap consulta no surt sense `company_id`."""
    certifications = await _rows(session, CompanyCertification, company_id)
    evidence = await knowledge_evidence.evidenced_ids(session, company_id)
    services = await _rows(session, CompanyService, company_id)
    return KnowledgeInputs(
        company_id=str(company_id),
        certifications=[
            CertificationIn(
                # `expires_at` fa de `valid_until` en aquesta taula.
                prov=_prov(c, evidence, valid_until=c.expires_at),
                name=c.name,
                standard_code=c.standard_code,
                level=c.level,
                scope=tuple(c.scope_service_lines) if c.scope_service_lines else None,
            )
            for c in certifications
        ],
        facts=[
            FactIn(prov=_prov(f, evidence), key=str(f.key), value=f.value, detail=f.detail)
            for f in await _rows(session, CompanyOperatingFact, company_id)
        ],
        services=[
            ServiceIn(
                # `active` fa de cicle de vida en aquesta taula.
                prov=_prov(
                    s,
                    evidence,
                    lifecycle=KnowledgeLifecycle.CURRENT
                    if s.active
                    else KnowledgeLifecycle.RETIRED,
                ),
                name=s.name,
                service_line_code=s.service_line_code,
                delivery_mode=str(s.delivery_mode) if s.delivery_mode else None,
            )
            for s in services
        ],
        solutions=[
            SolutionIn(
                prov=_prov(s, evidence),
                name=s.name,
                service_line_code=s.service_line_code,
                vendor=s.vendor,
                edition=s.edition,
            )
            for s in await _rows(session, CompanySolution, company_id)
        ],
        capabilities=[
            CapabilityIn(
                prov=_prov(c, evidence),
                solution_id=str(c.solution_id),
                capability_code=c.capability_code,
                status=c.status,
                value_number=c.value_number,
                value_level=c.value_level,
            )
            for c in await _rows(session, SolutionCapability, company_id)
        ],
        products=[
            ProductIn(
                prov=_prov(p, evidence),
                manufacturer=p.manufacturer,
                model=p.model,
                service_line_code=p.service_line_code,
            )
            for p in await _rows(session, CompanyProduct, company_id)
        ],
        solution_products=[
            SolutionProductIn(
                prov=_prov(link, evidence),
                solution_id=str(link.solution_id),
                product_id=str(link.product_id),
            )
            for link in await _rows(session, SolutionProduct, company_id)
        ],
        partners=[
            PartnerIn(prov=_prov(p, evidence), name=p.name)
            for p in await _rows(session, CompanyPartner, company_id)
        ],
        partner_roles=[
            PartnerRoleIn(
                prov=_prov(r, evidence),
                partner_id=str(r.partner_id),
                role=str(r.role),
                service_line_code=r.service_line_code,
                territory=r.territory,
            )
            for r in await _rows(session, PartnerRoleRow, company_id)
        ],
        observations=[
            ObservationIn(
                observation_id=str(o.id),
                subject=ObservationSubject(o.subject),
                subject_key=o.subject_key,
                statement=o.statement,
                status=ObservationStatus(o.status),
                proposal=o.proposal,
                observed_on=o.observed_on,
                source_ref=o.source_ref,
            )
            for o in await _rows(session, KnowledgeObservation, company_id)
        ],
    )


async def company_snapshot(
    session: AsyncSession, company_id: uuid.UUID, *, on: date | None = None
) -> KnowledgeSnapshot:
    return build_snapshot(await load_inputs(session, company_id), on=on or date.today())


# --------------------------------------------------------------------------- #
# Observacions de l'historic                                                   #
# --------------------------------------------------------------------------- #


def _validated(subject: ObservationSubject, proposal: dict) -> P._Proposal:
    try:
        return P.parse_proposal(subject, proposal)
    except PydanticValidationError as exc:
        raise InvalidProposalError(
            "La proposta de l'observacio no te la forma del seu subjecte.",
            details={"subject": subject, "errors": exc.errors(include_url=False)},
        ) from None


async def record_observation(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    subject: ObservationSubject,
    subject_key: str,
    statement: str,
    proposal: dict,
    observed_on: date | None = None,
    source_ref: str | None = None,
    historic_bid_id: uuid.UUID | None = None,
) -> KnowledgeObservation:
    _validated(subject, proposal)
    if historic_bid_id is not None:
        bid = await session.get(HistoricBid, historic_bid_id)
        if bid is None or bid.company_id != company_id:
            raise NotFoundError("Licitacio historica no trobada.")
    observation = KnowledgeObservation(
        company_id=company_id,
        historic_bid_id=historic_bid_id,
        subject=subject,
        subject_key=subject_key,
        statement=statement,
        proposal=proposal,
        observed_on=observed_on,
        source_ref=source_ref,
        status=ObservationStatus.CANDIDATE,
    )
    session.add(observation)
    await session.flush()
    return observation


async def _candidate(
    session: AsyncSession, company_id: uuid.UUID, observation_id: uuid.UUID
) -> KnowledgeObservation:
    observation = await session.scalar(
        sa.select(KnowledgeObservation).where(
            KnowledgeObservation.id == observation_id,
            KnowledgeObservation.company_id == company_id,
        )
    )
    if observation is None:
        raise NotFoundError("Observacio no trobada.")
    if observation.status != ObservationStatus.CANDIDATE:
        raise ObservationNotCandidateError(
            "Aquesta observacio ja s'ha revisat.", details={"status": observation.status}
        )
    return observation


async def _exists(session: AsyncSession, model: Any, company_id: uuid.UUID, *where) -> bool:
    found = await session.scalar(
        sa.select(sa.func.count()).select_from(model).where(model.company_id == company_id, *where)
    )
    return bool(found)


async def promote_observation(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    observation_id: uuid.UUID,
    user: User,
    note: str | None = None,
) -> KnowledgeObservation:
    """Una persona valida l'observacio: en neix coneixement actual declarat i traçable."""
    observation = await _candidate(session, company_id, observation_id)
    subject = ObservationSubject(observation.subject)
    proposal = _validated(subject, observation.proposal)
    promoted = {
        "knowledge_origin": KnowledgeOrigin.PROMOTED_OBSERVATION,
        "as_of": date.today(),
        "verification_status": VerificationStatus.PENDING_VERIFICATION,
    }

    def conflict(what: str) -> KnowledgeAlreadyDeclaredError:
        return KnowledgeAlreadyDeclaredError(
            f"Ja hi ha coneixement actual sobre {what}: mana el que s'ha declarat.",
            details={"subject": subject, "key": observation.subject_key},
        )

    entity: Any
    if isinstance(proposal, P.OperatingFactProposal):
        if await _exists(
            session, CompanyOperatingFact, company_id, CompanyOperatingFact.key == proposal.key
        ):
            raise conflict(proposal.key)
        entity = CompanyOperatingFact(
            company_id=company_id,
            key=proposal.key,
            value=proposal.value,
            detail=proposal.detail,
            lifecycle=KnowledgeLifecycle.CURRENT,
            **promoted,
        )
    elif isinstance(proposal, P.CertificationProposal):
        if not proposal.held:
            raise ObservationNotPromotableError(
                "Una observacio que diu que NO es te una certificacio no es pot promoure: "
                "descarta-la o declara la certificacio actual.",
                details={"standard_code": proposal.standard_code},
            )
        if await _exists(
            session,
            CompanyCertification,
            company_id,
            CompanyCertification.standard_code == proposal.standard_code,
            CompanyCertification.level.is_(None)
            if proposal.level is None
            else CompanyCertification.level == proposal.level,
            CompanyCertification.lifecycle == KnowledgeLifecycle.CURRENT,
        ):
            raise conflict(proposal.standard_code)
        entity = CompanyCertification(
            company_id=company_id,
            name=proposal.name or " ".join(filter(None, (proposal.standard_code, proposal.level))),
            standard_code=proposal.standard_code,
            level=proposal.level,
            scope_service_lines=proposal.scope_service_lines,
            lifecycle=KnowledgeLifecycle.CURRENT,
            created_by=user.id,
            updated_by=user.id,
            **promoted,
        )
    elif isinstance(proposal, P.ServiceProposal):
        if await _exists(session, CompanyService, company_id, CompanyService.name == proposal.name):
            raise conflict(proposal.name)
        entity = CompanyService(
            company_id=company_id,
            name=proposal.name,
            service_line_code=proposal.service_line_code,
            delivery_mode=proposal.delivery_mode,
            active=True,
            created_by=user.id,
            updated_by=user.id,
            **promoted,
        )
    elif isinstance(proposal, P.SolutionProposal):
        if await _exists(
            session, CompanySolution, company_id, CompanySolution.name == proposal.name
        ):
            raise conflict(proposal.name)
        entity = CompanySolution(
            company_id=company_id,
            name=proposal.name,
            service_line_code=proposal.service_line_code,
            vendor=proposal.vendor,
            edition=proposal.edition,
            lifecycle=KnowledgeLifecycle.CURRENT,
            **promoted,
        )
    elif isinstance(proposal, P.ProductProposal):
        if await _exists(
            session,
            CompanyProduct,
            company_id,
            CompanyProduct.manufacturer == proposal.manufacturer,
            CompanyProduct.model == proposal.model,
        ):
            raise conflict(f"{proposal.manufacturer} {proposal.model}")
        entity = CompanyProduct(
            company_id=company_id,
            manufacturer=proposal.manufacturer,
            model=proposal.model,
            service_line_code=proposal.service_line_code,
            lifecycle=KnowledgeLifecycle.CURRENT,
            **promoted,
        )
    else:
        assert isinstance(proposal, P.PartnerProposal)
        if await _exists(session, CompanyPartner, company_id, CompanyPartner.name == proposal.name):
            raise conflict(proposal.name)
        entity = CompanyPartner(
            company_id=company_id,
            name=proposal.name,
            lifecycle=KnowledgeLifecycle.CURRENT,
            **promoted,
        )
    session.add(entity)
    await session.flush()
    if isinstance(proposal, P.PartnerProposal):
        for role in proposal.roles:
            session.add(
                PartnerRoleRow(
                    company_id=company_id,
                    partner_id=entity.id,
                    role=role.role,
                    service_line_code=role.service_line_code,
                    territory=role.territory,
                    lifecycle=KnowledgeLifecycle.CURRENT,
                    **promoted,
                )
            )

    observation.status = ObservationStatus.PROMOTED
    observation.reviewed_by = user.id
    observation.reviewed_at = datetime.now(UTC)
    observation.review_note = note
    observation.promoted_entity_type = str(subject)
    observation.promoted_entity_id = entity.id
    await session.flush()
    return observation


async def dismiss_observation(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    observation_id: uuid.UUID,
    user: User,
    note: str,
) -> KnowledgeObservation:
    observation = await _candidate(session, company_id, observation_id)
    observation.status = ObservationStatus.DISMISSED
    observation.reviewed_by = user.id
    observation.reviewed_at = datetime.now(UTC)
    observation.review_note = note
    await session.flush()
    return observation


# --------------------------------------------------------------------------- #
# Verificar una dada actual                                                    #
# --------------------------------------------------------------------------- #


async def verify_record(
    session: AsyncSession, *, company_id: uuid.UUID, kind: str, record_id: uuid.UUID
) -> Any:
    """Passa una dada a verificada. **Nomes si te evidencia enllacada**: si no,
    `422 evidence_required` i la dada es queda com estava."""
    known = knowledge_evidence.KNOWLEDGE_KINDS.get(kind)
    if known is None:
        raise NotFoundError("Tipus de coneixement desconegut.")
    entity_type, model = known
    record = await session.scalar(
        sa.select(model).where(model.id == record_id, model.company_id == company_id)
    )
    if record is None:
        raise NotFoundError("Dada de coneixement no trobada.")
    await knowledge_evidence.verify(session, company_id, entity_type, record)
    await session.flush()
    return record


# --------------------------------------------------------------------------- #
# Importacio local de desenvolupament                                          #
# --------------------------------------------------------------------------- #


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


async def import_knowledge(
    session: AsyncSession, company_id: uuid.UUID, document: dict[str, Any]
) -> dict[str, int]:
    """Carrega un document de coneixement (actual + historic + observacions).

    Per a entorns de desenvolupament i per als golds: el document es local i fora
    de git. **Mai no conte costos, marges, tarifes ni percentatges interns**: si
    en porta, es refusa. No promou res: les observacions entren com a candidates.
    """
    forbidden = _confidential_keys(document)
    if forbidden:
        raise ValidationError(
            "El document porta camps economics confidencials: no s'importa.",
            details={"fields": sorted(forbidden)},
        )
    counts = {"certifications": 0, "operating_facts": 0, "historic_bids": 0, "observations": 0}
    current = document.get("current", {})
    # Un document importat no porta evidencia enllacada: no pot portar res verificat.
    for section in ("certifications", "operating_facts"):
        for item in current.get(section, []):
            if item.get("verification_status") == VerificationStatus.VERIFIED:
                raise knowledge_evidence.EvidenceRequiredError(
                    "Un document importat no pot portar dades verificades: la verificacio "
                    "demana evidencia enllacada.",
                    details={"section": section},
                )
    for item in current.get("certifications", []):
        validate_level(item["standard_code"], item.get("level"))
        session.add(
            CompanyCertification(
                company_id=company_id,
                name=item["name"],
                standard_code=item["standard_code"],
                level=item.get("level"),
                scope_service_lines=item.get("scope_service_lines"),
                as_of=_date(item.get("as_of")) or date.today(),
                expires_at=_date(item.get("valid_until")),
                verification_status=item.get(
                    "verification_status", VerificationStatus.PENDING_VERIFICATION
                ),
                lifecycle=item.get("lifecycle", KnowledgeLifecycle.CURRENT),
            )
        )
        counts["certifications"] += 1
    for item in current.get("operating_facts", []):
        session.add(
            CompanyOperatingFact(
                company_id=company_id,
                key=item["key"],
                value=item["value"],
                detail=item.get("detail"),
                as_of=_date(item.get("as_of")) or date.today(),
                valid_until=_date(item.get("valid_until")),
                verification_status=item.get(
                    "verification_status", VerificationStatus.PENDING_VERIFICATION
                ),
                lifecycle=item.get("lifecycle", KnowledgeLifecycle.CURRENT),
            )
        )
        counts["operating_facts"] += 1
    await session.flush()

    bids: dict[str, uuid.UUID] = {}
    for item in document.get("historic_bids", []):
        bid = HistoricBid(
            company_id=company_id,
            reference=item["reference"],
            buyer_name=item.get("buyer_name"),
            bid_date=_date(item.get("bid_date")),
            service_line_codes=item.get("service_line_codes"),
            base_budget=item.get("base_budget"),
            decision=HistoricBidDecision(item.get("decision", "undecided")),
            decision_reason=item.get("decision_reason"),
            result=HistoricBidResult(item.get("result", "unknown")),
            source_ref=item.get("source_ref"),
        )
        session.add(bid)
        await session.flush()
        bids[bid.reference] = bid.id
        counts["historic_bids"] += 1
    for item in document.get("observations", []):
        await record_observation(
            session,
            company_id=company_id,
            subject=ObservationSubject(item["subject"]),
            subject_key=item["subject_key"],
            statement=item["statement"],
            proposal=item["proposal"],
            observed_on=_date(item.get("observed_on")),
            source_ref=item.get("source_ref"),
            historic_bid_id=bids.get(item.get("historic_bid_reference", "")),
        )
        counts["observations"] += 1
    return counts


#: Noms de camp que delaten dades economiques internes. Un document que en porti
#: no s'importa, sigui on sigui.
CONFIDENTIAL_KEYS = frozenset(
    {
        "cost",
        "unit_cost",
        "monthly_cost",
        "margin",
        "margin_pct",
        "markup",
        "multiplier",
        "discount",
        "baixa",
        "baixa_pct",
        "offer",
        "offer_amount",
        "tariff",
        "fee",
        "fee_value",
        "fee_pct",
        "success_fee",
        "hourly_rate",
        "price",
    }
)


def _confidential_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in CONFIDENTIAL_KEYS:
                found.add(key)
            found |= _confidential_keys(item)
    elif isinstance(value, list):
        for item in value:
            found |= _confidential_keys(item)
    return found
