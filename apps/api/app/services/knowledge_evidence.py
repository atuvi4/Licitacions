"""Verificar el coneixement de l'empresa vol dir tenir-ne evidencia.

**Regla.** `verification_status = verified` nomes pot existir si el registre te
almenys un document enllacat (`document_links`). Una revisio humana sense
evidencia continua sent `pending_verification`: dir "ho hem mirat" no es poder-ho
acreditar davant d'un organ de contractacio.

La base no ho pot imposar (els enllacos d'evidencia son polimorfics, sense clau
forana), aixi que es fa aqui, a totes les portes d'escriptura:

* l'endpoint de verificacio (`POST /knowledge/records/{kind}/{id}/verify`);
* els CRUD de certificacions i serveis (`build_crud_router(..., validate=...)`);
* la importacio (`import_knowledge`), que no pot portar res verificat;
* treure l'ultima evidencia (esborrar l'enllac o el document) torna el registre
  a `pending_verification`.

**I una verificacio val per al que es va verificar.** Modificar materialment una
dada verificada la torna a `pending_verification` conservant l'evidencia
(`unverify_if_materially_changed`): cal tornar-la a verificar.

I, per si de cas hi ha dades d'abans d'aquesta regla, el `KnowledgeSnapshot` tampoc
no es creu un `verified` sense evidencia: el compta com a declarat.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.documents import DocumentLink
from app.models.enums import LinkedEntityType, VerificationStatus
from app.models.knowledge import (
    CompanyOperatingFact,
    CompanyPartner,
    CompanyProduct,
    CompanySolution,
    PartnerRoleRow,
    SolutionCapability,
    SolutionProduct,
)
from app.models.people import CompanyPerson
from app.models.profile import CompanyFinancialYear, CompanyService
from app.models.projects import CompanyProject
from app.models.resources import CompanyEquipment


class EvidenceRequiredError(ValidationError):
    code = "evidence_required"


#: Tipus de coneixement verificable → (tipus d'entitat enllacable, model).
KNOWLEDGE_KINDS: dict[str, tuple[LinkedEntityType, Any]] = {
    "certification": (LinkedEntityType.COMPANY_CERTIFICATION, CompanyCertification),
    "service": (LinkedEntityType.COMPANY_SERVICE, CompanyService),
    "operating_fact": (LinkedEntityType.COMPANY_OPERATING_FACT, CompanyOperatingFact),
    "solution": (LinkedEntityType.COMPANY_SOLUTION, CompanySolution),
    "solution_capability": (LinkedEntityType.SOLUTION_CAPABILITY, SolutionCapability),
    "product": (LinkedEntityType.COMPANY_PRODUCT, CompanyProduct),
    "solution_product": (LinkedEntityType.SOLUTION_PRODUCT, SolutionProduct),
    "partner": (LinkedEntityType.COMPANY_PARTNER, CompanyPartner),
    "partner_role": (LinkedEntityType.PARTNER_ROLE, PartnerRoleRow),
    # Qualificacio i mitjans (0052): les taules del perfil que ara son coneixement.
    "financial_year": (LinkedEntityType.COMPANY_FINANCIAL_YEAR, CompanyFinancialYear),
    "project": (LinkedEntityType.COMPANY_PROJECT, CompanyProject),
    "insurance": (LinkedEntityType.COMPANY_INSURANCE, CompanyInsurance),
    "registration": (LinkedEntityType.COMPANY_REGISTRATION, CompanyRegistration),
    "person": (LinkedEntityType.COMPANY_PERSON, CompanyPerson),
    "equipment": (LinkedEntityType.COMPANY_EQUIPMENT, CompanyEquipment),
}
MODEL_BY_ENTITY_TYPE = {entity_type: model for entity_type, model in KNOWLEDGE_KINDS.values()}
ENTITY_TYPE_BY_MODEL = {model: entity_type for entity_type, model in KNOWLEDGE_KINDS.values()}


async def has_evidence(
    session: AsyncSession, company_id: uuid.UUID, entity_type: LinkedEntityType, entity_id
) -> bool:
    if entity_id is None:
        return False
    found = await session.scalar(
        sa.select(DocumentLink.id)
        .where(
            DocumentLink.company_id == company_id,
            DocumentLink.entity_type == entity_type,
            DocumentLink.entity_id == entity_id,
        )
        .limit(1)
    )
    return found is not None


async def evidenced_ids(session: AsyncSession, company_id: uuid.UUID) -> set[tuple[str, str]]:
    """`(entity_type, entity_id)` de tot el coneixement d'una empresa que te evidencia."""
    rows = await session.execute(
        sa.select(DocumentLink.entity_type, DocumentLink.entity_id)
        .where(
            DocumentLink.company_id == company_id,
            DocumentLink.entity_type.in_(list(MODEL_BY_ENTITY_TYPE)),
        )
        .distinct()
    )
    return {(str(entity_type), str(entity_id)) for entity_type, entity_id in rows}


def _refuse(entity_type: LinkedEntityType) -> EvidenceRequiredError:
    return EvidenceRequiredError(
        "Nomes es pot marcar com a verificat amb evidencia enllacada. "
        "Enllaca-hi primer el document que ho acredita.",
        details={"entity_type": entity_type},
    )


#: Camps que es poden retocar sense canviar el que la dada afirma. Tota la resta
#: es material: si canvia, el que es va verificar ja no es el que hi ha.
EDITORIAL_FIELDS = frozenset({"notes"})
_BOOKKEEPING_FIELDS = frozenset(
    {"verification_status", "created_at", "created_by", "updated_at", "updated_by"}
)


def material_changes(instance: Any) -> list[str]:
    """Els camps materials que aquesta peticio ha canviat de debo (un valor igual
    al que hi havia no es un canvi).

    S'ha de cridar abans de qualsevol consulta: l'autoflush desa els canvis i
    n'esborra l'historial.
    """
    state = sa.inspect(instance)
    return sorted(
        column.key
        for column in state.mapper.column_attrs
        if column.key not in EDITORIAL_FIELDS | _BOOKKEEPING_FIELDS
        and state.attrs[column.key].history.has_changes()
    )


def unverify_for_dependent_change(instance: Any) -> bool:
    """Una dada verificada que canvia per una filla seva (un limit d'una polissa,
    els serveis d'un projecte) torna a `pending_verification`: el document
    acreditava el conjunt. Cert si l'ha desverificada."""
    if instance.verification_status != VerificationStatus.VERIFIED:
        return False
    instance.verification_status = VerificationStatus.PENDING_VERIFICATION
    return True


def unverify_if_materially_changed(instance: Any) -> bool:
    """**Editar una dada verificada n'invalida la verificacio.**

    El document enllacat acreditava el que hi havia; si la dada canvia (el nivell,
    l'abast, la vigencia, el valor...), algu ha de tornar a mirar que encara
    l'acredita. La dada torna a `pending_verification` i **l'evidencia es
    conserva**: queda "declarada, pendent de verificar", no "sense evidencia".
    Cert si l'ha desverificada.
    """
    if instance.verification_status != VerificationStatus.VERIFIED:
        return False
    if not material_changes(instance):
        return False
    instance.verification_status = VerificationStatus.PENDING_VERIFICATION
    return True


async def require_evidence_if_verified(
    session: AsyncSession, instance: Any, company_id: uuid.UUID
) -> None:
    """Per als CRUD de certificacions i serveis: que en queda, de la verificacio,
    despres d'una alta o una modificacio.

    * **Demanar `verified`** (una alta, o passar-hi des de pendent) exigeix
      evidencia enllacada; sense, `422 evidence_required`. Un registre nou no en
      pot tenir: no pot neixer verificat.
    * **Modificar materialment una dada que ja era verificada** la torna a
      `pending_verification` i conserva l'evidencia
      (`unverify_if_materially_changed`). La via per tornar-la a verificar es
      `/verify`.
    * **Dades anteriors a la regla.** Una fila que ja era `verified` sense cap
      evidencia no es refusa, encara que el canvi sigui nomes editorial: es
      normalitza a `pending_verification`, que es el que la fotografia ja en diu.
    """
    if instance.verification_status != VerificationStatus.VERIFIED:
        return
    entity_type = ENTITY_TYPE_BY_MODEL[type(instance)]
    # Abans de consultar res: la consulta fa autoflush i esborraria l'historial
    # dels camps, que es el que diu que ha demanat i que ha canviat la peticio.
    state = sa.inspect(instance)
    asked = not state.persistent or state.attrs.verification_status.history.has_changes()
    edited = state.persistent and bool(material_changes(instance))
    evidenced = await has_evidence(session, company_id, entity_type, instance.id)
    if asked:
        if not evidenced:
            raise _refuse(entity_type)
        return
    if edited or not evidenced:
        instance.verification_status = VerificationStatus.PENDING_VERIFICATION


async def verify(
    session: AsyncSession, company_id: uuid.UUID, entity_type: LinkedEntityType, record: Any
) -> None:
    if not await has_evidence(session, company_id, entity_type, record.id):
        raise _refuse(entity_type)
    record.verification_status = VerificationStatus.VERIFIED


async def revoke_unsupported_verifications(
    session: AsyncSession,
    company_id: uuid.UUID,
    targets: Iterable[tuple[LinkedEntityType, uuid.UUID]],
) -> int:
    """Despres de treure evidencia: el que s'ha quedat sense cap document deixa
    d'estar verificat. Retorna quants registres s'han desverificat."""
    revoked = 0
    for entity_type, entity_id in set(targets):
        model = MODEL_BY_ENTITY_TYPE.get(LinkedEntityType(entity_type))
        if model is None:
            continue
        record = await session.scalar(
            sa.select(model).where(model.id == entity_id, model.company_id == company_id)
        )
        if record is None or record.verification_status != VerificationStatus.VERIFIED:
            continue
        if not await has_evidence(session, company_id, LinkedEntityType(entity_type), entity_id):
            record.verification_status = VerificationStatus.PENDING_VERIFICATION
            revoked += 1
    return revoked
