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
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.knowledge import proposals as P
from app.knowledge.snapshot import (
    CapabilityIn,
    CertificationIn,
    EquipmentIn,
    ExecutionReferenceIn,
    FactIn,
    FinancialYearIn,
    InsuranceIn,
    KnowledgeInputs,
    KnowledgeSnapshot,
    ObservationIn,
    PartnerIn,
    PartnerRoleIn,
    PersonIn,
    ProductIn,
    Provenance,
    RegistrationIn,
    ServiceIn,
    SolutionIn,
    SolutionProductIn,
    build_snapshot,
)
from app.knowledge.vocabulary import (
    validate_capability_value,
    validate_fact_value,
    validate_level,
)
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.enums import (
    CoverageEntityType,
    HistoricBidDecision,
    HistoricBidResult,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    LinkedEntityType,
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
from app.models.people import CompanyPerson
from app.models.profile import CompanyFinancialYear, CompanyService
from app.models.projects import CompanyProject
from app.models.resources import CompanyEquipment
from app.services import knowledge_coverage, knowledge_evidence
from app.services.entities import delete_links_for_entity


class ObservationNotCandidateError(ConflictError):
    code = "observation_not_candidate"


class ObservationNotPromotableError(ValidationError):
    code = "observation_not_promotable"


class KnowledgeAlreadyDeclaredError(ConflictError):
    code = "knowledge_already_declared"


class InvalidProposalError(ValidationError):
    code = "invalid_observation_proposal"


class InvalidKnowledgeError(ValidationError):
    code = "invalid_knowledge"


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
        as_of=row.as_of if row.as_of is not None else _created_on(row),
        valid_until=valid_until if valid_until is not None else getattr(row, "valid_until", None),
        verification=VerificationStatus(row.verification_status),
        lifecycle=KnowledgeLifecycle(lifecycle or row.lifecycle),
        has_evidence=(str(entity_type), str(row.id)) in evidence,
    )


def _created_on(row: Any) -> date:
    """La data de creacio en UTC: el `as_of` d'una dada que no en diu cap (0052).
    Mai la zona horaria de la sessio ni del servidor."""
    created = row.created_at
    if created.tzinfo is not None:
        created = created.astimezone(UTC)
    return created.date()


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
            FactIn(
                prov=_prov(f, evidence),
                key=str(f.key),
                value=f.value,
                detail=f.detail,
                coverage=knowledge_coverage.coverage_input(f),
                value_number=f.value_number,
            )
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
                coverage=knowledge_coverage.coverage_input(s),
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
                coverage=knowledge_coverage.coverage_input(r),
            )
            for r in await _rows(session, PartnerRoleRow, company_id)
        ],
        # Qualificacio i mitjans (0052): les taules del perfil que ara son coneixement.
        financial_years=[
            FinancialYearIn(
                prov=_prov(y, evidence), year=y.year, revenue=y.revenue, currency=y.currency
            )
            for y in await _rows(session, CompanyFinancialYear, company_id)
        ],
        execution_references=[
            ExecutionReferenceIn(
                prov=_prov(p, evidence),
                name=p.name,
                status=str(p.status),
                amount=p.amount,
                currency=p.currency,
                client_type=str(p.client_type),
                start_date=p.start_date,
                end_date=p.end_date,
                service_lines=tuple(
                    sorted({s.service_line_code for s in p.services if s.service_line_code})
                ),
                annual_amounts=tuple((a.year, a.amount) for a in p.annual_amounts),
            )
            for p in await _rows(session, CompanyProject, company_id)
        ],
        insurances=[
            InsuranceIn(
                # `expires_at` fa de `valid_until` en aquesta taula.
                prov=_prov(i, evidence, valid_until=i.expires_at),
                insurance_type=str(i.type),
                provider=i.provider,
                currency=i.currency,
                limits=tuple(sorted((str(limit.kind), limit.amount) for limit in i.limits)),
                additional_insured=i.additional_insured,
            )
            for i in await _rows(session, CompanyInsurance, company_id)
        ],
        registrations=[
            RegistrationIn(
                prov=_prov(r, evidence, valid_until=r.expires_at),
                name=r.name,
                registration_type=str(r.type),
                issuer=r.issuer,
                registration_number=r.registration_number,
                scope=tuple(r.scope_service_lines) if r.scope_service_lines else None,
            )
            for r in await _rows(session, CompanyRegistration, company_id)
        ],
        people=[
            PersonIn(
                prov=_prov(p, evidence),
                availability=str(p.availability_status),
                profile_codes=tuple(p.profile_codes) if p.profile_codes else None,
                years_experience=p.years_experience,
                role=p.role,
            )
            for p in await _rows(session, CompanyPerson, company_id)
        ],
        equipment=[
            EquipmentIn(
                prov=_prov(e, evidence),
                equipment_type=str(e.equipment_type),
                quantity=e.quantity,
                manufacturer=e.manufacturer,
                model=e.model,
                serial_reference=e.serial_reference,
                access_mode=str(e.access_mode) if e.access_mode else None,
                calibration_required=e.calibration_required,
                calibration_valid_until=e.calibration_valid_until,
            )
            for e in await _rows(session, CompanyEquipment, company_id)
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


def _conflict(subject: str, key: str) -> KnowledgeAlreadyDeclaredError:
    return KnowledgeAlreadyDeclaredError(
        f"Ja hi ha coneixement actual sobre {key}: mana el que s'ha declarat.",
        details={"subject": subject, "key": key},
    )


async def _new_entity(
    session: AsyncSession,
    company_id: uuid.UUID,
    proposal: P._Proposal,
    provenance: dict[str, Any],
    user: User,
) -> Any:
    """La fila de coneixement actual que neix d'una proposta, ja afegida i amb id.

    La mateixa proposta serveix per promoure una observacio de l'historic i per
    declarar a ma: nomes canvia la procedencia (`provenance`). Si ja hi ha
    coneixement actual sobre la mateixa clau, es refusa: mana el que hi ha.
    """
    entity: Any
    if isinstance(proposal, P.OperatingFactProposal):
        if await _exists(
            session, CompanyOperatingFact, company_id, CompanyOperatingFact.key == proposal.key
        ):
            raise _conflict("operating_fact", proposal.key)
        entity = CompanyOperatingFact(
            company_id=company_id,
            key=proposal.key,
            value=proposal.value,
            value_number=proposal.value_number,
            detail=proposal.detail,
            **provenance,
        )
        _check_fact(entity)
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
            raise _conflict("certification", proposal.standard_code)
        entity = CompanyCertification(
            company_id=company_id,
            name=proposal.name or " ".join(filter(None, (proposal.standard_code, proposal.level))),
            standard_code=proposal.standard_code,
            level=proposal.level,
            scope_service_lines=proposal.scope_service_lines,
            created_by=user.id,
            updated_by=user.id,
            **{k: v for k, v in provenance.items() if k != "valid_until"},
            expires_at=provenance.get("valid_until"),
        )
    elif isinstance(proposal, P.ServiceProposal):
        if await _exists(session, CompanyService, company_id, CompanyService.name == proposal.name):
            raise _conflict("service", proposal.name)
        entity = CompanyService(
            company_id=company_id,
            name=proposal.name,
            service_line_code=proposal.service_line_code,
            delivery_mode=proposal.delivery_mode,
            active=provenance.get("lifecycle", KnowledgeLifecycle.CURRENT)
            == KnowledgeLifecycle.CURRENT,
            created_by=user.id,
            updated_by=user.id,
            **{k: v for k, v in provenance.items() if k != "lifecycle"},
        )
    elif isinstance(proposal, P.SolutionProposal):
        if await _exists(
            session, CompanySolution, company_id, CompanySolution.name == proposal.name
        ):
            raise _conflict("solution", proposal.name)
        entity = CompanySolution(
            company_id=company_id,
            name=proposal.name,
            service_line_code=proposal.service_line_code,
            vendor=proposal.vendor,
            edition=proposal.edition,
            **provenance,
        )
    elif isinstance(proposal, P.ProductProposal):
        if await _exists(
            session,
            CompanyProduct,
            company_id,
            CompanyProduct.manufacturer == proposal.manufacturer,
            CompanyProduct.model == proposal.model,
        ):
            raise _conflict("product", f"{proposal.manufacturer} {proposal.model}")
        entity = CompanyProduct(
            company_id=company_id,
            manufacturer=proposal.manufacturer,
            model=proposal.model,
            service_line_code=proposal.service_line_code,
            **provenance,
        )
    else:
        assert isinstance(proposal, P.PartnerProposal)
        if await _exists(session, CompanyPartner, company_id, CompanyPartner.name == proposal.name):
            raise _conflict("partner", proposal.name)
        entity = CompanyPartner(
            company_id=company_id,
            name=proposal.name,
            tax_id=getattr(proposal, "tax_id", None),
            **provenance,
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
                    **provenance,
                )
            )
        await session.flush()
    return entity


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
        "valid_until": None,
        "verification_status": VerificationStatus.PENDING_VERIFICATION,
        "lifecycle": KnowledgeLifecycle.CURRENT,
    }
    entity = await _new_entity(session, company_id, proposal, promoted, user)

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
# Declaracio directa (fets, solucions, capacitats, productes, tercers)         #
# --------------------------------------------------------------------------- #
#
# Les certificacions i els serveis tenen els seus CRUD. La resta de coneixement
# actual entra per aqui: neix `declared` i `pending_verification`, sempre.

#: Tipus declarables: el que no te CRUD propi.
DECLARABLE_KINDS = frozenset(
    {
        "operating_fact",
        "solution",
        "solution_capability",
        "product",
        "solution_product",
        "partner",
        "partner_role",
        # Mitjans operatius (0052). La resta de la qualificacio (exercicis, projectes,
        # assegurances, registres, persones) te el seu CRUD.
        "equipment",
    }
)

_PROVENANCE_FIELDS = ("as_of", "valid_until", "lifecycle")


def _declared_provenance(payload: BaseModel) -> dict[str, Any]:
    return {
        "knowledge_origin": KnowledgeOrigin.DECLARED,
        "as_of": getattr(payload, "as_of", None) or date.today(),
        "valid_until": getattr(payload, "valid_until", None),
        "lifecycle": getattr(payload, "lifecycle", None) or KnowledgeLifecycle.CURRENT,
        "verification_status": VerificationStatus.PENDING_VERIFICATION,
    }


def _kind_model(kind: str) -> tuple[LinkedEntityType, Any]:
    if kind not in DECLARABLE_KINDS:
        raise NotFoundError("Tipus de coneixement desconegut.")
    return knowledge_evidence.KNOWLEDGE_KINDS[kind]


async def _owned(session: AsyncSession, model: Any, record_id: uuid.UUID, company_id: uuid.UUID):
    record = await session.scalar(
        sa.select(model).where(model.id == record_id, model.company_id == company_id)
    )
    if record is None:
        raise NotFoundError("Dada de coneixement no trobada.")
    return record


async def _flush_or_conflict(session: AsyncSession, what: str) -> None:
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise KnowledgeAlreadyDeclaredError(
            f"Ja hi ha una dada igual sobre {what}.", details={"key": what}
        ) from exc


def _check_fact(record: CompanyOperatingFact) -> None:
    try:
        validate_fact_value(record.key, record.value, record.value_number)
    except ValueError as exc:
        raise InvalidKnowledgeError(str(exc), details={"field": "value_number"}) from None


def _check_equipment(record: CompanyEquipment) -> None:
    if record.calibration_valid_until is not None and record.calibration_required is not True:
        raise InvalidKnowledgeError(
            "Una data de calibratge nomes te sentit si l'equip en necessita "
            "(`calibration_required = true`).",
            details={"field": "calibration_valid_until"},
        )
    if record.quantity is None or record.quantity < 1:
        raise InvalidKnowledgeError("Cal almenys una unitat.", details={"field": "quantity"})


def _check_capability(record: SolutionCapability) -> None:
    try:
        validate_capability_value(record.capability_code, record.value_number, record.value_level)
    except ValueError as exc:
        raise InvalidKnowledgeError(str(exc), details={"field": "capability_code"}) from None


async def declare_record(
    session: AsyncSession, *, company_id: uuid.UUID, kind: str, payload: BaseModel, user: User
) -> Any:
    """Una persona declara coneixement actual. Neix declarat i pendent de verificar."""
    _kind_model(kind)
    provenance = _declared_provenance(payload)
    coverage = getattr(payload, "coverage", None)
    if kind in ("operating_fact", "solution", "product", "partner"):
        # Els esquemes d'alta d'aquests tipus SON la proposta d'una observacio.
        assert isinstance(payload, P._Proposal), kind
        entity = await _new_entity(session, company_id, payload, provenance, user)
        if coverage is not None and knowledge_coverage.supports_coverage(entity):
            await knowledge_coverage.apply_coverage(session, entity, coverage)
            await session.flush()
            await knowledge_coverage.refresh_coverage(session, entity)
        return entity

    entity: Any
    if kind == "solution_capability":
        solution = await _owned(session, CompanySolution, payload.solution_id, company_id)
        entity = SolutionCapability(
            company_id=company_id,
            solution_id=solution.id,
            capability_code=payload.capability_code,
            status=payload.status,
            value_number=payload.value_number,
            value_level=payload.value_level,
            limits_note=payload.limits_note,
            **provenance,
        )
        _check_capability(entity)
        what = f"{solution.name} / {payload.capability_code}"
    elif kind == "solution_product":
        solution = await _owned(session, CompanySolution, payload.solution_id, company_id)
        product = await _owned(session, CompanyProduct, payload.product_id, company_id)
        entity = SolutionProduct(
            company_id=company_id, solution_id=solution.id, product_id=product.id, **provenance
        )
        what = f"{solution.name} / {product.manufacturer} {product.model}"
    elif kind == "equipment":
        entity = CompanyEquipment(
            company_id=company_id,
            equipment_type=payload.equipment_type,
            manufacturer=payload.manufacturer,
            model=payload.model,
            serial_reference=payload.serial_reference,
            access_mode=payload.access_mode,
            quantity=payload.quantity,
            calibration_required=payload.calibration_required,
            calibration_valid_until=payload.calibration_valid_until,
            notes=payload.notes,
            **provenance,
        )
        _check_equipment(entity)
        what = f"{payload.equipment_type} / {payload.serial_reference or payload.model or '-'}"
    else:
        assert kind == "partner_role", kind
        partner = await _owned(session, CompanyPartner, payload.partner_id, company_id)
        entity = PartnerRoleRow(
            company_id=company_id,
            partner_id=partner.id,
            role=payload.role,
            service_line_code=payload.service_line_code,
            territory=payload.territory,
            **provenance,
        )
        what = f"{partner.name} / {payload.role}"
    session.add(entity)
    if coverage is not None and knowledge_coverage.supports_coverage(entity):
        await knowledge_coverage.apply_coverage(session, entity, coverage)
    await _flush_or_conflict(session, what)
    if knowledge_coverage.supports_coverage(entity):
        await knowledge_coverage.refresh_coverage(session, entity)
    return entity


async def update_record(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    kind: str,
    record_id: uuid.UUID,
    payload: BaseModel,
) -> tuple[Any, bool]:
    """Modifica una dada declarada o promoguda. Retorna la fila i si ha deixat
    d'estar verificada.

    La procedencia (`knowledge_origin`) no es toca. **Una modificacio material
    d'una dada verificada n'invalida la verificacio**: torna a
    `pending_verification` i conserva l'evidencia enllacada, a punt per tornar-la
    a verificar."""
    _, model = _kind_model(kind)
    record = await _owned(session, model, record_id, company_id)
    changes = payload.model_dump(exclude_unset=True)
    # La cobertura no es cap columna de la fila: s'aplica a part, com un tot.
    coverage = getattr(payload, "coverage", None) if changes.pop("coverage", None) else None
    for field, value in changes.items():
        setattr(record, field, value)
    # Abans de qualsevol consulta: l'autoflush esborraria l'historial dels camps.
    unverified = knowledge_evidence.unverify_if_materially_changed(record)
    if coverage is not None and knowledge_coverage.supports_coverage(record):
        # El mateix payload de cobertura no es cap canvi; un de diferent, si.
        if await knowledge_coverage.apply_coverage(session, record, coverage):
            unverified = (
                knowledge_coverage.unverify_for_coverage_change(record, asked=False) or unverified
            )
    if record.valid_until is not None and record.valid_until < record.as_of:
        raise InvalidKnowledgeError(
            "`valid_until` no pot ser anterior a `as_of`.", details={"field": "valid_until"}
        )
    if isinstance(record, SolutionCapability):
        _check_capability(record)
    elif isinstance(record, CompanyOperatingFact):
        _check_fact(record)
    elif isinstance(record, CompanyEquipment):
        _check_equipment(record)
    await _flush_or_conflict(session, str(record_id))
    if knowledge_coverage.supports_coverage(record):
        await knowledge_coverage.refresh_coverage(session, record)
    return record, unverified


async def delete_record(
    session: AsyncSession, *, company_id: uuid.UUID, kind: str, record_id: uuid.UUID
) -> None:
    """Esborra la dada i l'evidencia enllacada, tambe la de les seves filles
    (capacitats i productes d'una solucio, papers d'un tercer): els enllacos son
    polimorfics i cap FK no els neteja."""
    entity_type, model = _kind_model(kind)
    record = await _owned(session, model, record_id, company_id)
    targets: list[tuple[LinkedEntityType, uuid.UUID]] = [(entity_type, record.id)]
    children: list[tuple[LinkedEntityType, Any, Any]] = []
    if isinstance(record, CompanySolution):
        children = [
            (
                LinkedEntityType.SOLUTION_CAPABILITY,
                SolutionCapability,
                SolutionCapability.solution_id,
            ),
            (LinkedEntityType.SOLUTION_PRODUCT, SolutionProduct, SolutionProduct.solution_id),
        ]
    elif isinstance(record, CompanyProduct):
        children = [
            (LinkedEntityType.SOLUTION_PRODUCT, SolutionProduct, SolutionProduct.product_id)
        ]
    elif isinstance(record, CompanyPartner):
        children = [(LinkedEntityType.PARTNER_ROLE, PartnerRoleRow, PartnerRoleRow.partner_id)]
    for child_type, child_model, parent_column in children:
        ids = await session.scalars(
            sa.select(child_model.id).where(
                parent_column == record.id, child_model.company_id == company_id
            )
        )
        targets.extend((child_type, child_id) for child_id in ids)
    for target_type, target_id in targets:
        await delete_links_for_entity(session, target_type, target_id)
        coverage_type = _COVERAGE_TYPE_BY_LINK.get(target_type)
        if coverage_type is not None:
            await knowledge_coverage.delete_coverage_for_entity(session, coverage_type, target_id)
    await session.delete(record)
    await session.flush()


#: Tipus d'entitat enllacable → tipus de cobertura, per a les dades que en tenen.
_COVERAGE_TYPE_BY_LINK = {
    LinkedEntityType.COMPANY_OPERATING_FACT: CoverageEntityType.OPERATING_FACT,
    LinkedEntityType.COMPANY_SERVICE: CoverageEntityType.SERVICE,
    LinkedEntityType.PARTNER_ROLE: CoverageEntityType.PARTNER_ROLE,
}


async def list_records(session: AsyncSession, company_id: uuid.UUID) -> dict[str, list[Any]]:
    """Totes les files declarables d'UNA empresa, per editar-les."""

    async def rows(model: Any, *order: Any) -> list[Any]:
        return list(
            await session.scalars(
                sa.select(model).where(model.company_id == company_id).order_by(*order)
            )
        )

    return {
        "operating_facts": await rows(CompanyOperatingFact, CompanyOperatingFact.key),
        "solutions": await rows(CompanySolution, CompanySolution.name),
        "solution_capabilities": await rows(
            SolutionCapability, SolutionCapability.solution_id, SolutionCapability.capability_code
        ),
        "products": await rows(CompanyProduct, CompanyProduct.manufacturer, CompanyProduct.model),
        "solution_products": await rows(
            SolutionProduct, SolutionProduct.solution_id, SolutionProduct.product_id
        ),
        "partners": await rows(CompanyPartner, CompanyPartner.name),
        "partner_roles": await rows(PartnerRoleRow, PartnerRoleRow.partner_id, PartnerRoleRow.role),
        "equipment": await rows(
            CompanyEquipment, CompanyEquipment.equipment_type, CompanyEquipment.serial_reference
        ),
    }


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
