"""Congelar el paquet i aprovar-lo. L'ultim OK abans de presentar.

Aquesta capa respon una sola pregunta, i tota la seva forma en depen:
**exactament quin paquet estem aprovant?**

Per aixo l'aprovacio no apunta al workspace —que canvia— sino a una revisio
congelada amb els documents i els seus hashos d'aquell dia. Si despres canvia
qualsevol peca, l'aprovacio anterior segueix sent auditable i **no serveix** per
al paquet nou: cal congelar una revisio nova i tornar a aprovar.

Cap crida a cap model. Congelar es copiar i calcular un hash; aprovar es una
persona prement un boto.

No presenta res enlloc. `submitted` queda fora d'aquesta iteracio.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.errors import ConflictError, NotFoundError
from app.models.compliance import CompanyComplianceRun
from app.models.documents import Document
from app.models.enums import (
    AnalysisBasis,
    MembershipRole,
    PackageApprovalDecision,
    RequirementCategory,
    SubmissionPackageStatus,
    WorkspaceStatus,
)
from app.models.identity import User
from app.models.requirements import RequirementExtractionRun
from app.models.submission import (
    SubmissionPackage,
    SubmissionPackageApproval,
    SubmissionPackageItem,
)
from app.models.workspace import BidWorkspace
from app.services import bid_workspace as workspace_service
from app.services import workspace_dossier as dossier_service
from app.services.storage import StorageError, get_storage
from app.services.tender_lifecycle import load_change_log, revision_of
from app.workspace.dossier import UNUSABLE_VALIDITIES
from app.workspace.transitions import TransitionActor

#: Qui pot signar l'aprovacio final. La mateixa politica de 5A: no s'inventa cap
#: RBAC nou per a aixo.
FINAL_APPROVAL_ROLES = workspace_service.DECISION_ROLES


class PackageNotReadyError(ConflictError):
    """S'ha demanat congelar amb el dossier incomplet."""

    code = "package_not_ready"


class TenderNotActionableError(ConflictError):
    """L'expedient —o el lot— ja no admet preparar cap presentacio nova.

    No esborra res. El workspace, les decisions i els paquets ja aprovats
    segueixen sent historia consultable; el que es bloqueja es continuar
    preparant una oferta per a una licitacio que ja no existeix, o per a un lot
    que ha desaparegut del feed.
    """

    code = "tender_not_actionable"


class PackageImmutableError(ConflictError):
    """S'ha intentat tocar un paquet que ja no es un esborrany."""

    code = "package_immutable"


class PackageStaleError(ConflictError):
    """El paquet ja no descriu el que hi ha ara: cal una revisio nova."""

    code = "package_stale"


class PackageIntegrityError(ConflictError):
    """Un fitxer del paquet ha desaparegut o ja no es el que era."""

    code = "package_integrity_failed"


class NoDocumentsError(ConflictError):
    """No es congela un paquet buit."""

    code = "package_has_no_documents"


class PackageSimulationError(ConflictError):
    """S'ha intentat aprovar un paquet sostingut per una analisi que no ho es.

    Congelar un paquet de simulacio SI que es pot: es el que permet provar la
    pantalla de revisio final en desenvolupament. Aprovar-lo, no. L'aprovacio
    final es l'unic moment en que una persona diu "aixo es exactament el que
    presentarem", i signar-ho sobre requisits que ningu ha extret de cap plec
    convertiria un artefacte de proves en un document amb aparença d'aprovat.

    La regla viu al domini i no a la configuracio d'entorn a proposit: que un
    desplegament estigui marcat com a desenvolupament no fa menys fals el
    paquet, i el dia que algu miri l'historial voldra saber que allo mai va
    arribar a aprovar-se.
    """

    code = "simulation_not_approvable"


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def get_package(
    session: AsyncSession, company_id: uuid.UUID, package_id: uuid.UUID
) -> SubmissionPackage:
    """Sempre filtrat per empresa. 404 si es d'un altre tenant."""
    package = await session.scalar(
        sa.select(SubmissionPackage)
        .options(
            selectinload(SubmissionPackage.items),
            selectinload(SubmissionPackage.approvals),
        )
        .where(
            SubmissionPackage.id == package_id,
            SubmissionPackage.company_id == company_id,
        )
        # `populate_existing`: un paquet acabat de crear ja te les col·leccions
        # inicialitzades a BUIDES a la sessio, i sense aixo la consulta no les
        # sobreescriuria. El resultat era un paquet amb `document_count: 1` i
        # `items: []` just despres de congelar-lo.
        .execution_options(populate_existing=True)
    )
    if package is None:
        raise NotFoundError("Paquet no trobat.")
    return package


async def list_packages(
    session: AsyncSession, workspace: BidWorkspace
) -> list[SubmissionPackage]:
    return list(
        (
            await session.scalars(
                sa.select(SubmissionPackage)
                .options(
                    selectinload(SubmissionPackage.items),
                    selectinload(SubmissionPackage.approvals),
                )
                .where(SubmissionPackage.workspace_id == workspace.id)
                .order_by(SubmissionPackage.revision_number.desc())
            )
        ).all()
    )


async def latest_package(
    session: AsyncSession, workspace: BidWorkspace
) -> SubmissionPackage | None:
    packages = await list_packages(session, workspace)
    return packages[0] if packages else None


def approval_of(package: SubmissionPackage) -> SubmissionPackageApproval | None:
    """L'ultima aprovacio d'aquesta revisio, si n'hi ha cap de vigent.

    Es mira `decision`, no nomes l'existencia: rebutjar despres d'aprovar afegeix
    una entrada nova i la vigent es l'ultima.
    """
    latest = next(iter(package.approvals), None)
    if latest is None or latest.decision != PackageApprovalDecision.APPROVED_FOR_SUBMISSION:
        return None
    return latest


# --------------------------------------------------------------------------- #
# Congelar                                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class IntegrityIssue:
    document_id: uuid.UUID | None
    filename: str
    problem: str


async def verify_storage_integrity(
    items: list[SubmissionPackageItem], *, deep: bool = False
) -> list[IntegrityIssue]:
    """Comprova que cada fitxer segueix sent el que el paquet diu que es.

    Sense `deep` no es descarrega res: nomes existencia i mida, que es el que
    l'storage pot respondre amb una consulta de metadades. Amb `deep` es
    recalcula el SHA-256 dels bytes.

    La comprovacio superficial NO detectaria una mutacio de la mateixa mida; per
    aixo l'aprovacio final la fa profunda i el congelat no. Congelar es barat i
    repetible; aprovar passa una vegada i ha de ser fiable.
    """
    storage = get_storage()
    issues: list[IntegrityIssue] = []
    for item in items:
        try:
            if not await storage.exists(item.storage_key):
                issues.append(
                    IntegrityIssue(item.document_id, item.filename, "missing_from_storage")
                )
                continue
            size = await storage.size(item.storage_key)
        except (StorageError, NotImplementedError):  # pragma: no cover - backend antic
            continue

        if size is not None and size != item.file_size:
            issues.append(IntegrityIssue(item.document_id, item.filename, "size_mismatch"))
            continue
        if not deep:
            continue
        try:
            payload = await storage.download(item.storage_key)
        except StorageError:
            issues.append(
                IntegrityIssue(item.document_id, item.filename, "missing_from_storage")
            )
            continue
        if hashlib.sha256(payload).hexdigest() != item.document_sha256:
            issues.append(IntegrityIssue(item.document_id, item.filename, "sha256_mismatch"))
    return issues


@dataclass(frozen=True, slots=True)
class FreezeOutcome:
    package: SubmissionPackage
    created: bool


async def freeze_package(
    session: AsyncSession,
    workspace: BidWorkspace,
    *,
    user: User,
    role: MembershipRole,
) -> FreezeOutcome:
    """Congela una revisio nova amb el que hi ha ara mateix al dossier.

    **Recalcula sempre la readiness.** No es mira `workspace.status`: l'estat
    guardat es un reflex i un reflex pot quedar enrere. Si el dossier no esta a
    punt, no es congela res i l'error diu que falta.

    El paquet neix en `draft` i passa a `frozen` dins de la mateixa transaccio,
    un cop calculat el manifest i comprovada la integritat dels fitxers. Si algo
    falla, no queda cap revisio a mitges.
    """
    workspace_service.assert_can_decide(role)

    # Un expedient anul.lat no admet cap preparacio nova, i un lot que ha
    # desaparegut del feed tampoc: per a qui s'hi presentava es igual de
    # definitiu. No s'esborra res del que ja hi ha —el workspace, les decisions i
    # els paquets anteriors segueixen sent historia auditable— pero congelar-ne
    # un de nou seria preparar una oferta que no te on anar.
    state = await workspace_service.load_state(session, workspace)
    if not state.workspace_actionable:
        raise TenderNotActionableError(
            "Aquest lot ja no es a la licitacio."
            if state.lot_removed
            else "Aquesta licitacio ja no admet presentacio.",
            details={
                "status": str(state.tender.status),
                "reason": "lot_removed" if state.lot_removed else "tender_not_actionable",
            },
        )

    readiness = await dossier_service.reconcile_readiness(session, workspace)
    if not readiness.is_ready:
        raise PackageNotReadyError(
            "El dossier encara no esta a punt per congelar-lo.",
            details={"blockers": list(readiness.blockers)},
        )

    dossier = await dossier_service.load_dossier(session, workspace)

    previous = await latest_package(session, workspace)
    revision_number = (previous.revision_number + 1) if previous else 1

    # Una sola implementacio del manifest, compartida amb la deteccio
    # d'obsolescencia: si congelar i comparar el construissin per separat, dos
    # hashos diferents no voldrien dir res.
    selected, payload, digest = await dossier_service.current_manifest(
        session, workspace, revision_number=revision_number
    )
    if not selected:
        raise NoDocumentsError("No hi ha cap document seleccionat per presentar.")
    package = SubmissionPackage(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        revision_number=revision_number,
        status=SubmissionPackageStatus.DRAFT,
        compliance_run_id=state.compliance_run.id if state.compliance_run else None,
        requirement_run_id=state.requirement_run.id if state.requirement_run else None,
        prefit_score=state.prefit_score,
        deadline_at=state.deadline_at,
        # Contra quin estat de la licitacio es congela. El manifest diu QUE
        # enviavem; aixo diu contra QUE. Sense les dues coses, un paquet aprovat
        # no pot respondre si es d'abans o de despres que canviessin el plec.
        tender_revision_id=state.tender_revision.id if state.tender_revision else None,
        tender_normalized_hash=(
            state.tender_revision.normalized_hash if state.tender_revision else None
        ),
        mandatory_total=dossier.counters.mandatory_total,
        mandatory_covered=dossier.counters.covered,
        document_count=len(selected),
        created_by=user.id,
    )
    session.add(package)
    await session.flush()

    items: list[SubmissionPackageItem] = []
    for position, row in enumerate(selected):
        document = row.document
        item = SubmissionPackageItem(
            company_id=workspace.company_id,
            package_id=package.id,
            workspace_evidence_item_id=row.item.id,
            document_id=document.id,
            requirement_id=row.item.requirement_id,
            # Copia deliberada: si despres es modifica el document, comparar
            # aixo amb el que hi ha ara es el que detecta el canvi.
            document_sha256=document.sha256,
            storage_key=document.storage_key,
            filename=document.name,
            mime_type=document.mime_type,
            file_size=document.file_size,
            requirement_title=(
                row.checklist_item.title
                if row.checklist_item
                else (row.requirement.title if row.requirement else None)
            ),
            category=row.requirement.category if row.requirement else RequirementCategory.OTHER,
            position=position,
        )
        session.add(item)
        items.append(item)

    issues = await verify_storage_integrity(items)
    if issues:
        await session.rollback()
        raise PackageIntegrityError(
            "Algun fitxer del paquet no es al seu lloc o ja no es el mateix.",
            details={"issues": [asdict(issue) for issue in issues]},
        )

    package.manifest_json = payload
    package.manifest_hash = digest
    package.status = SubmissionPackageStatus.FROZEN
    package.frozen_at = datetime.now(UTC)
    package.frozen_by = user.id

    # Les revisions anteriors que encara no s'havien aprovat queden superades.
    # Les APROVADES no es toquen: son historial.
    for old in await list_packages(session, workspace):
        if old.id != package.id and old.status == SubmissionPackageStatus.FROZEN:
            old.status = SubmissionPackageStatus.SUPERSEDED

    await session.commit()

    # Congelar per a la revisio final ES posar el workspace en revisio final: ja
    # s'ha comprovat la mateixa readiness que exigeix el gate de 5B. Si venia
    # d'una revisio aprovada, hi torna: la revisio nova encara no ho esta.
    if workspace.status != WorkspaceStatus.READY_FOR_FINAL_REVIEW:
        await dossier_service.advance_to_final_review(
            session, workspace, user=user, role=role
        )

    # Es torna a llegir amb els items carregats: despres d'un commit les
    # col·leccions queden expirades i resoldre-les mandrosament dins d'una
    # corutina no es possible.
    package = await get_package(session, workspace.company_id, package.id)
    return FreezeOutcome(package=package, created=True)


# --------------------------------------------------------------------------- #
# Obsolescencia                                                                 #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StaleReport:
    """Per que aquest paquet ja no descriu el que hi ha ara.

    **Estar obsolet no desfa res.** Un paquet aprovat segueix aprovat, amb la
    seva signatura i la seva data: es historia i la historia no es reescriu. El
    que diu aixo es una altra cosa —si encara es POT PRESENTAR— i son dues
    preguntes diferents que abans no es podien distingir.
    """

    reasons: tuple[str, ...]
    current_manifest_hash: str | None

    @property
    def is_stale(self) -> bool:
        return bool(self.reasons)


async def analysis_bases_behind(
    session: AsyncSession, package: SubmissionPackage
) -> set[AnalysisBasis]:
    """Sobre quines menes d'analisi descansa aquest paquet.

    Se'n miren les DUES portes: el run de requisits que el paquet cita, i el que
    va fer servir el calcul de compliment. No n'hi ha prou amb una: un paquet
    pot citar una extraccio real i haver-se congelat sobre un compliment
    calculat contra una altra, i llavors la barreja no es mes fiable que la
    seva pitjor meitat.

    Un paquet que no cita cap run entra aqui com a `PROVIDER_NOT_CONFIGURED`:
    no saber sobre que es va construir no es el mateix que saber que es real, i
    entre les dues lectures la prudent es l'unica honesta.
    """
    run_ids: set[uuid.UUID] = set()
    if package.requirement_run_id:
        run_ids.add(package.requirement_run_id)
    if package.compliance_run_id:
        compliance = await session.get(CompanyComplianceRun, package.compliance_run_id)
        if compliance is not None and compliance.requirement_extraction_run_id:
            run_ids.add(compliance.requirement_extraction_run_id)

    if not run_ids:
        return {AnalysisBasis.PROVIDER_NOT_CONFIGURED}

    providers = (
        await session.scalars(
            sa.select(RequirementExtractionRun.provider).where(
                RequirementExtractionRun.id.in_(run_ids)
            )
        )
    ).all()
    if not providers:
        return {AnalysisBasis.PROVIDER_NOT_CONFIGURED}
    return {AnalysisBasis.for_provider(provider) for provider in providers}


async def is_approvable_analysis(
    session: AsyncSession, package: SubmissionPackage
) -> bool:
    """Cert nomes si TOT el que sosté el paquet surt de llegir un plec de veritat."""
    return all(basis.is_trustworthy for basis in await analysis_bases_behind(session, package))


async def evaluate_staleness(
    session: AsyncSession, workspace: BidWorkspace, package: SubmissionPackage
) -> StaleReport:
    """Compara el paquet amb l'estat actual. Deterministic i sense efectes.

    Es recalcula el manifest amb el que hi ha ara i es compara el hash: si
    difereix, hi ha alguna cosa que ja no es la mateixa —un document substituit,
    un que s'ha tret, un requisit que ja no hi es— i el paquet no es pot aprovar.

    I es mira tambe la LICITACIO, que el manifest no cobreix: el manifest parla
    dels nostres papers, no del que demana l'organ. Un termini que s'avança o un
    plec substituit no canvien cap byte del que enviariem, i tot i aixi el fan
    inservible.
    """
    reasons: list[str] = []
    state = await workspace_service.load_state(session, workspace)

    log = await load_change_log(session, workspace.tender_id)
    frozen_on = await revision_of(session, package.tender_revision_id)
    if log.basis_unknown(frozen_on):
        # Congelat abans que la licitacio tingues historia: no es pot afirmar
        # que segueixi coherent, i afirmar-ho seria justament el que no toca en
        # una pantalla d'aprovacio final.
        reasons.append("tender_revision_unknown")
    elif log.since(frozen_on):
        reasons.append("tender_changed")
    if not state.workspace_actionable:
        reasons.append("lot_removed" if state.lot_removed else "tender_not_actionable")

    if state.compliance_run is not None and state.compliance_run.id != package.compliance_run_id:
        reasons.append("compliance_run_changed")
    if (
        state.requirement_run is not None
        and state.requirement_run.id != package.requirement_run_id
    ):
        reasons.append("requirement_run_changed")

    selected, _payload, digest = await dossier_service.current_manifest(
        session, workspace, revision_number=package.revision_number
    )
    if digest != package.manifest_hash:
        reasons.append("manifest_changed")

    # Un document del paquet que hagi deixat de servir per al termini invalida el
    # paquet encara que el manifest no hagi canviat: el que ha canviat es el
    # calendari, no el fitxer.
    if any(row.validity in UNUSABLE_VALIDITIES for row in selected):
        reasons.append("document_expired_for_deadline")

    return StaleReport(reasons=tuple(reasons), current_manifest_hash=digest)


# --------------------------------------------------------------------------- #
# Aprovacio final humana                                                        #
# --------------------------------------------------------------------------- #


async def approve_package(
    session: AsyncSession,
    workspace: BidWorkspace,
    package: SubmissionPackage,
    *,
    user: User,
    role: MembershipRole,
    decision: PackageApprovalDecision,
    comment: str | None = None,
    deep_integrity: bool = True,
) -> SubmissionPackageApproval:
    """L'aprovacio humana d'UNA revisio. **L'unic us de `HUMAN_FINAL_APPROVAL`.**

    Es recalcula tot de zero abans de signar: readiness, obsolescencia i
    integritat dels fitxers. No hi ha cap drecera que es fii de
    `workspace.status` ni de l'estat guardat del paquet, perque entre congelar i
    aprovar hi pot haver passat qualsevol cosa.

    A diferencia del GO inicial, aqui NO s'aprova amb avisos oberts. Alla
    s'aprovava anar-hi; aqui s'aprova exactament el que s'envia.

    Aprovar **no** presenta res: deixa el workspace en `approved_for_submission`.
    `submitted` es una altra iteracio.
    """
    workspace_service.assert_can_decide(role)

    if package.status == SubmissionPackageStatus.SUPERSEDED:
        raise PackageStaleError(
            "Aquesta revisio ha quedat superada per una de posterior.",
            details={"reasons": ["superseded"]},
        )

    if decision == PackageApprovalDecision.APPROVED_FOR_SUBMISSION:
        # Abans que res: signar "aixo es el que presentarem" sobre requisits que
        # ningu ha extret de cap plec no es una aprovacio, es un malentes amb
        # data i signatura.
        if not await is_approvable_analysis(session, package):
            bases = await analysis_bases_behind(session, package)
            raise PackageSimulationError(
                "Aquest paquet es de simulacio: no es pot aprovar per presentar.",
                details={"analysis_bases": sorted(str(basis) for basis in bases)},
            )

        readiness = await dossier_service.reconcile_readiness(session, workspace)
        if not readiness.is_ready:
            raise PackageNotReadyError(
                "El dossier ja no esta a punt: no es pot aprovar aquest paquet.",
                details={"blockers": list(readiness.blockers)},
            )

        stale = await evaluate_staleness(session, workspace, package)
        if stale.is_stale:
            # No s'aprova en silenci una cosa que ja no es la que es va congelar.
            package.status = SubmissionPackageStatus.SUPERSEDED
            await session.commit()
            raise PackageStaleError(
                "El paquet ja no descriu el que hi ha ara: cal congelar una revisio nova.",
                details={"reasons": list(stale.reasons)},
            )

        issues = await verify_storage_integrity(package.items, deep=deep_integrity)
        if issues:
            raise PackageIntegrityError(
                "Algun fitxer del paquet no es al seu lloc o ja no es el mateix.",
                details={"issues": [asdict(issue) for issue in issues]},
            )
    else:
        readiness = await dossier_service.evaluate_workspace_readiness(session, workspace)

    approval = SubmissionPackageApproval(
        company_id=workspace.company_id,
        package_id=package.id,
        # El hash aprovat, no nomes l'identificador del paquet.
        package_manifest_hash=package.manifest_hash or "",
        decision=decision,
        approved_by=user.id,
        approved_at=datetime.now(UTC),
        comment=(comment or "").strip() or None,
        readiness_snapshot=readiness.state,
        blockers_snapshot=list(readiness.blockers),
    )
    session.add(approval)

    if decision == PackageApprovalDecision.APPROVED_FOR_SUBMISSION:
        package.status = SubmissionPackageStatus.APPROVED
        workspace.status = workspace_service.set_status(
            workspace,
            WorkspaceStatus.APPROVED_FOR_SUBMISSION,
            actor=TransitionActor.HUMAN_FINAL_APPROVAL,
        )

    await session.commit()
    await session.refresh(approval)
    await session.refresh(workspace)
    # Explicit: l'identity map retorna el mateix objecte i no repoblaria una
    # col·leccio que ja tenia carregada (buida). Qui cridi aixo ha de veure
    # l'aprovacio que acaba de signar.
    await session.refresh(package, attribute_names=["status", "approvals"])
    return approval


async def document_for_download(
    session: AsyncSession, company_id: uuid.UUID, document_id: uuid.UUID
) -> Document:  # pragma: no cover - utilitat per a la UI
    document = await session.scalar(
        sa.select(Document).where(
            Document.id == document_id, Document.company_id == company_id
        )
    )
    if document is None:
        raise NotFoundError("Document no trobat.")
    return document
