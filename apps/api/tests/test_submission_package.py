"""Paquet de presentacio: exactament que aprova una persona.

Aprovar "el workspace" no vol dir res: el workspace canvia. Aquests tests fixen
que l'aprovacio apunti a una fotografia concreta —documents, hashos, calculs,
termini— i que aquella fotografia no es pugui retocar despres.

El cas que importa mes: aprovar la revisio 1, canviar una polissa i comprovar
que la revisio 1 segueix aprovada i auditable mentre el que hi ha ara **no ho
esta**. Traslladar una aprovacio d'una revisio a una altra seria dir que una
persona ha aprovat una cosa que no ha vist mai.

I `submitted` segueix sent inassolible: aprovar per presentar no es presentar.

Cap crida a cap proveidor: congelar es copiar i fer un hash.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.core.errors import NotFoundError, PermissionDeniedError
from app.models.compliance import CompanyComplianceRun
from app.models.credentials import CompanyCertification, CompanyInsurance
from app.models.documents import Document, DocumentLink
from app.models.enums import (
    AnalysisRunStatus,
    DocumentCategory,
    DownloadStatus,
    EvidenceItemStatus,
    ExtractionConfidence,
    InsuranceType,
    LinkedEntityType,
    MembershipRole,
    PackageApprovalDecision,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    ReviewStatus,
    SourceType,
    SubmissionPackageStatus,
    TenderDocumentType,
    WorkspaceDecisionType,
    WorkspaceReadiness,
    WorkspaceStatus,
)
from app.models.identity import Company
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.submission import (
    SubmissionPackage,
    SubmissionPackageApproval,
    SubmissionPackageItem,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentFile,
)
from app.models.tenders import Tender, TenderDocument
from app.models.workspace import WorkspaceEvidenceItem
from app.services import bid_workspace as workspace_service
from app.services import submission_package as service
from app.services import tender_lifecycle as lifecycle
from app.services import workspace_dossier as dossier_service
from app.services.compliance_analysis import analyze_company_compliance
from app.services.storage import get_storage
from app.workspace.manifest import (
    ManifestDocument,
    ManifestInput,
    build_manifest,
    canonical_json,
    manifest_hash,
)
from app.workspace.transitions import (
    FINAL_APPROVAL_STATUSES,
    AutomaticTransitionForbiddenError,
    TransitionActor,
    assert_transition,
)
from tests.dml import DmlRecorder

DEADLINE = datetime.now(UTC) + timedelta(days=40)
TODAY = date.today()


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def tender(db):
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="5C/2026",
        title="Servei de manteniment integral",
        deadline_at=DEADLINE,
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


@pytest.fixture
async def extraction_run(db, tender):
    return await add_extraction_run(db, tender)


async def add_extraction_run(db, tender) -> RequirementExtractionRun:
    await db.execute(
        sa.update(RequirementExtractionRun)
        .where(RequirementExtractionRun.tender_id == tender.id)
        .values(is_current=False)
    )
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        # Proveidor REAL a proposit: aquests tests aproven paquets, i un paquet
        # sostingut per una simulacio no s'ha de poder aprovar. Veure
        # `PackageSimulationError`.
        provider="anthropic",
        model="claude-sonnet-5",
        is_current=True,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def add_requirement(
    db,
    run,
    tender,
    *,
    category=RequirementCategory.CERTIFICATION,
    value=None,
    operator=RequirementOperator.EXISTS,
    title="Requisit",
) -> Requirement:
    requirement = Requirement(
        run_id=run.id,
        tender_id=tender.id,
        category=category,
        obligation=RequirementObligation.MANDATORY,
        operator=operator,
        scope=RequirementScope.TENDER,
        title=title,
        value_json=value,
        confidence=ExtractionConfidence.HIGH,
        review_status=ReviewStatus.UNREVIEWED,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


async def add_document(
    db,
    company_id,
    *,
    name="ISO 27001.pdf",
    body: bytes | None = None,
    expires_at=None,
    category=DocumentCategory.CERTIFICATION,
) -> Document:
    """Un document amb els BYTES realment pujats a l'storage.

    Fa falta que hi siguin: la comprovacio d'integritat del paquet mira que el
    fitxer existeixi i que el seu SHA-256 sigui el que diu la fila.
    """
    payload = body or f"contingut de {name} {uuid.uuid4().hex}".encode()
    key = f"docs/{uuid.uuid4().hex}.pdf"
    await get_storage().upload(key, payload, "application/pdf")
    document = Document(
        company_id=company_id,
        name=name,
        original_filename=name,
        storage_key=key,
        mime_type="application/pdf",
        file_size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        category=category,
        expires_at=expires_at,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def link_document(db, company_id, document, entity_type, entity_id):
    db.add(
        DocumentLink(
            company_id=company_id,
            document_id=document.id,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    )
    await db.commit()


async def run_compliance(db, company_id, tender):
    company = await db.get(Company, company_id)
    return (await analyze_company_compliance(db, company, tender, force=True)).run


@pytest.fixture
async def ready(db, tenant_a, tender, extraction_run):
    """Una candidatura amb GO, dossier complet i llesta per congelar.

    Es passa per tot el cami real de 5A i 5B: sense aixo, els tests de 5C
    partirien d'un estat que el sistema no ha acceptat mai.
    """
    user, company = tenant_a
    certification = CompanyCertification(
        company_id=company.id, name="ISO 27001", expires_at=TODAY + timedelta(days=400)
    )
    db.add(certification)
    await db.commit()
    await db.refresh(certification)

    document = await add_document(db, company.id, expires_at=TODAY + timedelta(days=400))
    await link_document(
        db, company.id, document, LinkedEntityType.COMPANY_CERTIFICATION, certification.id
    )
    requirement = await add_requirement(
        db, extraction_run, tender, value={"name": "ISO 27001"}, title="ISO 27001"
    )
    await run_compliance(db, company.id, tender)

    workspace = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=tender, user_id=user.id
        )
    ).workspace
    await workspace_service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Ens hi presentem.",
    )
    await dossier_service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(
        sa.select(WorkspaceEvidenceItem).where(WorkspaceEvidenceItem.workspace_id == workspace.id)
    )
    await dossier_service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )
    assert (await dossier_service.evaluate_workspace_readiness(db, workspace)).is_ready
    return {
        "user": user,
        "company": company,
        "workspace": workspace,
        "requirement": requirement,
        "document": document,
        "evidence_item": item,
        "tender": tender,
        "requirement_run": extraction_run,
        "certification": certification,
    }



async def replace_document(db, ready, name: str):
    """Substitueix el document del requisit per un de nou, ben acreditat.

    Enllaça el document nou a la MATEIXA certificacio que el compliment va
    citar: es el que faria qui renova un ISO i puja el certificat nou. Sense
    aquest enllaç la pertinença no estaria establerta i el dossier ho diria,
    que es justament el que proven els tests de `TestPertinenciaDocumental`.
    """
    replacement = await add_document(db, ready["company"].id, name=name)
    await link_document(
        db,
        ready["company"].id,
        replacement,
        LinkedEntityType.COMPANY_CERTIFICATION,
        ready["certification"].id,
    )
    await dossier_service.update_item(
        db,
        ready["workspace"],
        ready["evidence_item"],
        document_id=replacement.id,
        user=ready["user"],
    )
    return replacement


async def freeze(db, ready, *, role=MembershipRole.OWNER):
    return (
        await service.freeze_package(
            db, ready["workspace"], user=ready["user"], role=role
        )
    ).package


async def approve(db, ready, package, *, role=MembershipRole.OWNER, comment="Dossier revisat."):
    return await service.approve_package(
        db,
        ready["workspace"],
        package,
        user=ready["user"],
        role=role,
        decision=PackageApprovalDecision.APPROVED_FOR_SUBMISSION,
        comment=comment,
    )


# --------------------------------------------------------------------------- #
# Manifest: determinisme                                                        #
# --------------------------------------------------------------------------- #


def sample_manifest(**overrides) -> ManifestInput:
    data = {
        "revision_number": 1,
        "workspace_id": uuid.UUID(int=1),
        "company_id": uuid.UUID(int=2),
        "tender_id": uuid.UUID(int=3),
        "expediente_number": "5C/2026",
        "deadline_at": datetime(2026, 12, 1, 12, 0, tzinfo=UTC),
        "requirement_run_id": uuid.UUID(int=4),
        "compliance_run_id": uuid.UUID(int=5),
        "mandatory_total": 1,
        "mandatory_covered": 1,
        "documents": (
            ManifestDocument(
                position=0,
                document_id=uuid.UUID(int=6),
                filename="ISO 27001.pdf",
                sha256="a" * 64,
                file_size=1024,
                mime_type="application/pdf",
                category="certification",
            ),
        ),
    }
    data.update(overrides)
    return ManifestInput(**data)


def test_the_same_package_always_produces_the_same_hash():
    """Un hash nomes val si la serialitzacio es canonica."""
    hashes = {build_manifest(sample_manifest())[1] for _ in range(20)}

    assert len(hashes) == 1


def test_key_order_does_not_change_the_hash():
    """El hash no pot dependre de l'ordre en que Python construeixi els dicts."""
    first = canonical_json({"b": 1, "a": {"d": 4, "c": 3}})
    second = canonical_json({"a": {"c": 3, "d": 4}, "b": 1})

    assert first == second
    assert manifest_hash({"b": 1, "a": 2}) == manifest_hash({"a": 2, "b": 1})


def test_changing_one_document_changes_the_hash():
    """§4: qualsevol canvi real, hash diferent."""
    base = build_manifest(sample_manifest())[1]
    other = build_manifest(
        sample_manifest(
            documents=(
                ManifestDocument(
                    position=0,
                    document_id=uuid.UUID(int=6),
                    filename="ISO 27001.pdf",
                    sha256="b" * 64,  # nomes canvia el hash del fitxer
                    file_size=1024,
                    mime_type="application/pdf",
                    category="certification",
                ),
            )
        )
    )[1]

    assert base != other


def test_the_document_order_inside_the_manifest_is_stable():
    documents = tuple(
        ManifestDocument(
            position=index,
            document_id=uuid.UUID(int=10 + index),
            filename=f"{index}.pdf",
            sha256=str(index) * 64,
            file_size=10,
            mime_type="application/pdf",
            category="other",
        )
        for index in range(3)
    )

    forward = build_manifest(sample_manifest(documents=documents))[1]
    backward = build_manifest(sample_manifest(documents=tuple(reversed(documents))))[1]

    assert forward == backward, "l'ordre l'ha de fixar `position`, no la llista"


def test_the_manifest_carries_what_it_was_measured_against():
    payload, _digest = build_manifest(sample_manifest())

    assert payload["manifest_version"] == "submission-manifest-v1"
    assert payload["analysis"]["compliance_run_id"] == str(uuid.UUID(int=5))
    assert payload["analysis"]["requirement_run_id"] == str(uuid.UUID(int=4))
    assert payload["tender"]["deadline_at"].startswith("2026-12-01")
    assert payload["coverage"]["document_count"] == 1


# --------------------------------------------------------------------------- #
# Congelar                                                                      #
# --------------------------------------------------------------------------- #


async def test_freezing_captures_the_documents_and_their_hashes(db, ready):
    package = await freeze(db, ready)

    assert package.status == SubmissionPackageStatus.FROZEN
    assert package.revision_number == 1
    assert package.manifest_hash and len(package.manifest_hash) == 64
    assert package.frozen_at is not None
    assert package.frozen_by == ready["user"].id
    assert package.document_count == 1

    item = package.items[0]
    assert item.document_id == ready["document"].id
    assert item.document_sha256 == ready["document"].sha256
    assert item.file_size == ready["document"].file_size
    assert item.requirement_id == ready["requirement"].id


async def test_freezing_requires_a_ready_dossier(db, ready):
    """§5: es recalcula sempre; no es mira `workspace.status`."""
    workspace, user = ready["workspace"], ready["user"]
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], status=EvidenceItemStatus.SUGGESTED, user=user
    )

    with pytest.raises(service.PackageNotReadyError) as caught:
        await freeze(db, ready)

    assert caught.value.details["blockers"]
    assert await db.scalar(sa.select(sa.func.count()).select_from(SubmissionPackage)) == 0


async def test_freezing_does_not_trust_a_stale_ready_label(db, ready, tender):
    """L'etiqueta pot quedar enrere; el gate recalcula igualment."""
    workspace = ready["workspace"]
    # Congelar ja deixa el workspace en revisio final.
    await service.freeze_package(
        db, workspace, user=ready["user"], role=MembershipRole.OWNER
    )
    assert workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW

    # Canvi extern: ningu no ha tornat a mirar el workspace.
    await add_extraction_run(db, tender)

    with pytest.raises(service.PackageNotReadyError):
        await freeze(db, ready)


async def test_a_package_without_documents_is_not_frozen(db, tenant_a, tender, extraction_run):
    user, company = tenant_a
    workspace = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=tender, user_id=user.id
        )
    ).workspace
    await workspace_service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="Endavant",
    )

    with pytest.raises((service.PackageNotReadyError, service.NoDocumentsError)):
        await service.freeze_package(db, workspace, user=user, role=MembershipRole.OWNER)


async def test_a_viewer_cannot_freeze(db, ready):
    with pytest.raises(PermissionDeniedError):
        await freeze(db, ready, role=MembershipRole.VIEWER)


# --------------------------------------------------------------------------- #
# Immutabilitat i revisions                                                     #
# --------------------------------------------------------------------------- #


async def test_there_is_no_endpoint_to_edit_a_frozen_package(db, client_a, ready):
    """§6: no s'edita un paquet congelat. No es un oblit: no ha d'existir."""
    package = await freeze(db, ready)
    base = (
        f"/api/v1/companies/{ready['company'].id}"
        f"/workspaces/{ready['workspace'].id}/packages/{package.id}"
    )

    assert (await client_a.patch(base, json={"document_count": 99})).status_code in (404, 405)
    assert (await client_a.delete(base)).status_code in (404, 405)
    assert (await client_a.post(f"{base}/items", json={})).status_code in (404, 405)


async def test_a_second_revision_leaves_the_first_untouched(db, ready):
    """§16: revision 2 no altera revision 1. Mai."""
    first = await freeze(db, ready)
    first_hash = first.manifest_hash
    first_documents = [(i.document_id, i.document_sha256) for i in first.items]

    # Es canvia el document del requisit.
    await replace_document(db, ready, "ISO 27001 renovat.pdf")

    second = await freeze(db, ready)

    assert second.revision_number == 2
    assert second.manifest_hash != first_hash
    await db.refresh(first)
    assert first.manifest_hash == first_hash, "la revisio 1 no s'ha tocat"
    assert [(i.document_id, i.document_sha256) for i in first.items] == first_documents
    assert first.status == SubmissionPackageStatus.SUPERSEDED, (
        "superada, pero intacta i consultable"
    )


async def test_the_manifest_survives_the_live_entities_going_away(db, ready):
    """Es guarda el manifest sencer i no nomes el hash: ha de dir QUE hi havia.

    El document en si no es pot esborrar —queda retingut— pero la resta del
    context viu si: la peca del dossier, el requisit, o el nom del fitxer. El
    manifest congelat no en depen de cap.
    """
    package = await freeze(db, ready)
    snapshot = dict(package.manifest_json["documents"][0])

    await db.delete(await db.get(WorkspaceEvidenceItem, ready["evidence_item"].id))
    await db.delete(await db.get(Requirement, ready["requirement"].id))
    document = await db.get(Document, ready["document"].id)
    document.name = "Un altre nom del tot.pdf"
    await db.commit()

    await db.refresh(package)
    assert package.manifest_json["documents"][0] == snapshot
    assert package.items[0].document_sha256 == snapshot["sha256"]
    assert package.items[0].filename == snapshot["filename"], (
        "el nom d'aquell dia, no el d'ara"
    )


# --------------------------------------------------------------------------- #
# Integritat de l'storage                                                       #
# --------------------------------------------------------------------------- #


async def test_a_missing_blob_blocks_the_freeze(db, ready):
    """§11: no es congela un paquet que apunta a un fitxer que ja no hi es."""
    await get_storage().delete(ready["document"].storage_key)

    with pytest.raises(service.PackageIntegrityError) as caught:
        await freeze(db, ready)

    assert caught.value.details["issues"][0]["problem"] == "missing_from_storage"
    assert await db.scalar(sa.select(sa.func.count()).select_from(SubmissionPackage)) == 0


async def test_a_mutated_blob_blocks_the_approval(db, ready):
    """El hash es dels BYTES: si algu els canvia, l'aprovacio no passa."""
    package = await freeze(db, ready)
    # Mateixa mida, contingut diferent: nomes el SHA-256 ho detecta.
    original = await get_storage().download(ready["document"].storage_key)
    await get_storage().upload(
        ready["document"].storage_key, b"X" * len(original), "application/pdf"
    )

    with pytest.raises(service.PackageIntegrityError) as caught:
        await approve(db, ready, package)

    assert caught.value.details["issues"][0]["problem"] == "sha256_mismatch"
    assert await db.scalar(sa.select(sa.func.count()).select_from(SubmissionPackageApproval)) == 0


async def test_a_shallow_check_still_catches_a_size_change(db, ready):
    package = await freeze(db, ready)
    await get_storage().upload(
        ready["document"].storage_key, b"molt mes curt", "application/pdf"
    )

    issues = await service.verify_storage_integrity(package.items, deep=False)

    assert [issue.problem for issue in issues] == ["size_mismatch"]


# --------------------------------------------------------------------------- #
# Aprovacio final humana                                                        #
# --------------------------------------------------------------------------- #


async def test_the_approval_records_the_person_the_moment_and_the_manifest(db, ready):
    """§17: reconstruir "qui va aprovar exactament que, quan i amb quin motiu"."""
    package = await freeze(db, ready)

    approval = await approve(db, ready, package)

    assert approval.approved_by == ready["user"].id
    assert approval.approved_at is not None
    assert approval.comment == "Dossier revisat."
    assert approval.package_manifest_hash == package.manifest_hash
    assert approval.readiness_snapshot == WorkspaceReadiness.READY_FOR_FINAL_REVIEW
    assert approval.blockers_snapshot == []
    await db.refresh(package)
    assert package.status == SubmissionPackageStatus.APPROVED


async def test_approving_moves_the_workspace_but_never_to_submitted(db, ready):
    """§9: `approved_for_submission` si. `submitted` no, ni ara ni per aqui."""
    workspace = ready["workspace"]
    package = await freeze(db, ready)

    await approve(db, ready, package)

    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.APPROVED_FOR_SUBMISSION
    assert workspace.status != WorkspaceStatus.SUBMITTED


async def test_only_the_final_approval_actor_can_write_the_approved_status(db, ready):
    """§8: ni SYSTEM ni HUMAN_DECISION. Nomes l'aprovacio final."""
    for actor in (TransitionActor.SYSTEM, TransitionActor.HUMAN_DECISION):
        with pytest.raises(AutomaticTransitionForbiddenError):
            assert_transition(
                WorkspaceStatus.READY_FOR_FINAL_REVIEW,
                WorkspaceStatus.APPROVED_FOR_SUBMISSION,
                actor=actor,
            )

    assert WorkspaceStatus.APPROVED_FOR_SUBMISSION in FINAL_APPROVAL_STATUSES
    # I es l'unic servei que hi arriba.
    import inspect

    source = inspect.getsource(service)
    assert "TransitionActor.HUMAN_FINAL_APPROVAL" in source


async def test_a_viewer_cannot_approve_a_package(db, ready):
    package = await freeze(db, ready)

    with pytest.raises(PermissionDeniedError):
        await approve(db, ready, package, role=MembershipRole.VIEWER)

    await db.refresh(package)
    assert package.status == SubmissionPackageStatus.FROZEN
    assert await db.scalar(sa.select(sa.func.count()).select_from(SubmissionPackageApproval)) == 0


async def test_approvals_are_append_only(db, ready):
    """§7: rebutjar despres d'aprovar afegeix una entrada; no n'esborra cap."""
    package = await freeze(db, ready)
    approved = await approve(db, ready, package)

    rejected = await service.approve_package(
        db,
        ready["workspace"],
        package,
        user=ready["user"],
        role=MembershipRole.OWNER,
        decision=PackageApprovalDecision.REJECTED,
        comment="Ens hem repensat la polissa.",
    )

    await db.refresh(package)
    assert len(package.approvals) == 2
    assert package.approvals[0].id == rejected.id, "la vigent es l'ultima"
    assert package.approvals[1].id == approved.id
    assert package.approvals[1].comment == "Dossier revisat.", "l'aprovacio anterior intacta"
    assert service.approval_of(package) is None, "amb un rebuig al davant, ja no esta aprovat"


# --------------------------------------------------------------------------- #
# Obsolescencia                                                                 #
# --------------------------------------------------------------------------- #


async def test_a_package_whose_documents_changed_cannot_be_approved(db, ready):
    """§10: no s'aprova en silenci una cosa que ja no es la que es va congelar."""
    package = await freeze(db, ready)

    await replace_document(db, ready, "Un altre.pdf")

    with pytest.raises(service.PackageStaleError) as caught:
        await approve(db, ready, package)

    assert "manifest_changed" in caught.value.details["reasons"]
    await db.refresh(package)
    assert package.status == SubmissionPackageStatus.SUPERSEDED


async def test_a_new_requirements_run_makes_the_package_stale(db, ready, tender):
    package = await freeze(db, ready)

    await add_extraction_run(db, tender)

    report = await service.evaluate_staleness(db, ready["workspace"], package)
    assert "requirement_run_changed" in report.reasons
    with pytest.raises((service.PackageStaleError, service.PackageNotReadyError)):
        await approve(db, ready, package)


async def test_a_new_compliance_run_makes_the_package_stale(db, ready, tender):
    package = await freeze(db, ready)

    await run_compliance(db, ready["company"].id, tender)

    report = await service.evaluate_staleness(db, ready["workspace"], package)
    assert "compliance_run_changed" in report.reasons


async def test_a_document_expiring_before_the_deadline_makes_the_package_stale(db, ready):
    """§14: el que ha canviat no es el fitxer, es el calendari."""
    package = await freeze(db, ready)

    document = await db.get(Document, ready["document"].id)
    document.expires_at = (DEADLINE - timedelta(days=5)).date()
    await db.commit()

    report = await service.evaluate_staleness(db, ready["workspace"], package)

    assert "document_expired_for_deadline" in report.reasons or "manifest_changed" in (
        report.reasons
    )
    with pytest.raises((service.PackageStaleError, service.PackageNotReadyError)):
        await approve(db, ready, package)


async def test_an_approval_never_moves_to_another_revision(db, ready):
    """§16: el cas critic. La revisio 1 segueix aprovada; la 2 no ho esta."""
    first = await freeze(db, ready)
    approval = await approve(db, ready, first)

    # Es canvia una peca i es congela una revisio nova.
    await replace_document(db, ready, "Polissa RC 2027.pdf")
    second = await freeze(db, ready)

    await db.refresh(first)
    # L'APROVACIO de la r1 segueix sent valida per a aquella fotografia; el que
    # deixa de ser cert es que sigui la fotografia d'ara.
    assert service.approval_of(first).id == approval.id
    assert first.approvals[0].package_manifest_hash == first.manifest_hash
    assert first.status == SubmissionPackageStatus.SUPERSEDED

    assert second.status == SubmissionPackageStatus.FROZEN
    assert second.approvals == [], "la revisio nova NO hereta res"
    assert service.approval_of(second) is None
    assert second.manifest_hash != first.manifest_hash


async def test_a_superseded_revision_cannot_be_approved_later(db, ready):
    first = await freeze(db, ready)
    await replace_document(db, ready, "Nou.pdf")
    await freeze(db, ready)

    await db.refresh(first)
    with pytest.raises(service.PackageStaleError):
        await approve(db, ready, first)


# --------------------------------------------------------------------------- #
# `submitted` segueix inassolible                                               #
# --------------------------------------------------------------------------- #


def test_submitted_is_still_unreachable_for_every_wired_actor():
    """§9 i §22: aprovar per presentar no es presentar, i ningu no presenta."""
    for actor in (TransitionActor.SYSTEM, TransitionActor.HUMAN_DECISION):
        with pytest.raises(AutomaticTransitionForbiddenError):
            assert_transition(
                WorkspaceStatus.APPROVED_FOR_SUBMISSION,
                WorkspaceStatus.SUBMITTED,
                actor=actor,
            )


def test_no_service_writes_submitted():
    """L'unica garantia real: cap modul no anomena `SUBMITTED` per assignar-lo."""
    from pathlib import Path

    import app

    mentions = {
        path.name
        for path in Path(app.__file__).parent.rglob("*.py")
        if "SUBMITTED" in path.read_text(encoding="utf-8")
    }

    assert mentions == {"enums.py", "transitions.py"}


# --------------------------------------------------------------------------- #
# Multi-tenant                                                                  #
# --------------------------------------------------------------------------- #


async def test_another_company_cannot_read_or_approve_a_package(db, client_b, ready, tenant_b):
    package = await freeze(db, ready)
    _user_b, company_b = tenant_b
    workspace = ready["workspace"]

    with pytest.raises(NotFoundError):
        await service.get_package(db, company_b.id, package.id)

    for url in (
        f"/api/v1/companies/{company_b.id}/workspaces/{workspace.id}/packages/{package.id}",
        f"/api/v1/companies/{ready['company'].id}/workspaces/{workspace.id}"
        f"/packages/{package.id}",
    ):
        assert (await client_b.get(url)).status_code == 404

    assert (
        await client_b.post(
            f"/api/v1/companies/{company_b.id}/workspaces/{workspace.id}"
            f"/packages/{package.id}/approvals",
            json={"decision": "approved_for_submission"},
        )
    ).status_code == 404


async def test_package_rows_carry_the_company(db, ready):
    package = await freeze(db, ready)

    assert package.company_id == ready["company"].id
    item = await db.scalar(sa.select(SubmissionPackageItem))
    assert item.company_id == ready["company"].id
    await approve(db, ready, package)
    approval = await db.scalar(sa.select(SubmissionPackageApproval))
    assert approval.company_id == ready["company"].id


# --------------------------------------------------------------------------- #
# API                                                                           #
# --------------------------------------------------------------------------- #


async def test_the_api_serves_the_frozen_manifest_bytes(db, client_a, ready):
    """§18: es serveix el JSON guardat, que es el que te aquell hash."""
    package = await freeze(db, ready)
    base = (
        f"/api/v1/companies/{ready['company'].id}"
        f"/workspaces/{ready['workspace'].id}/packages/{package.id}"
    )

    response = await client_a.get(f"{base}/manifest")

    assert response.status_code == 200
    assert response.headers["x-manifest-sha256"] == package.manifest_hash
    assert "attachment" in response.headers["content-disposition"]
    assert response.json()["revision_number"] == 1
    assert response.json()["documents"][0]["sha256"] == ready["document"].sha256


async def test_the_api_refuses_to_approve_a_stale_package_and_says_why(
    db, client_a, ready, tender
):
    package = await freeze(db, ready)
    await add_extraction_run(db, tender)
    base = (
        f"/api/v1/companies/{ready['company'].id}"
        f"/workspaces/{ready['workspace'].id}/packages/{package.id}"
    )

    refused = await client_a.post(f"{base}/approvals", json={"decision": "approved_for_submission"})

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] in {"package_stale", "package_not_ready"}
    assert refused.json()["error"]["details"]


async def test_the_api_shows_what_changed_since_the_freeze(db, client_a, ready):
    workspace, company = ready["workspace"], ready["company"]
    package = await freeze(db, ready)
    await replace_document(db, ready, "Substitut.pdf")
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}/packages/{package.id}"

    body = (await client_a.get(base)).json()

    assert body["is_stale"] is True
    assert "manifest_changed" in body["stale_reasons"]
    assert body["can_approve"] is False


# --------------------------------------------------------------------------- #
# Acceptacio (§24)                                                              #
# --------------------------------------------------------------------------- #


async def test_the_full_final_approval_workflow(db, client_a, ready):
    """GO -> dossier llest -> congelar r1 -> revisar -> APROVAR -> canviar -> r2.

    I al final: `submitted` segueix sent fals, i la r2 no hereta l'aprovacio.
    """
    workspace, company, user = ready["workspace"], ready["company"], ready["user"]
    root = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

    # 1. Congelar la revisio 1.
    frozen = await client_a.post(f"{root}/packages")
    assert frozen.status_code == 200, frozen.text
    package = frozen.json()["package"]
    assert package["revision_number"] == 1
    assert package["status"] == "frozen"
    assert package["can_approve"] is True
    assert package["is_stale"] is False

    # 2. Veure exactament els documents i els seus hashos.
    detail = (await client_a.get(f"{root}/packages/{package['id']}")).json()
    assert detail["manifest_hash_short"] == detail["manifest_hash"][:12]
    assert len(detail["items"]) == 1
    assert detail["items"][0]["document_sha256"] == ready["document"].sha256
    assert detail["items"][0]["changed_since_freeze"] is False
    assert detail["coverage"]["mandatory_covered"] == detail["coverage"]["mandatory_total"]

    # 3. Aprovar per presentar.
    approved = await client_a.post(
        f"{root}/packages/{package['id']}/approvals",
        json={"decision": "approved_for_submission", "comment": "Dossier revisat."},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["approval"]["approved_by"] == str(user.id)
    assert body["approval"]["package_manifest_hash"] == detail["manifest_hash"]
    assert body["approval"]["comment"] == "Dossier revisat."
    assert body["workspace_status"] == "approved_for_submission"

    # 4. I encara no s'ha presentat res.
    assert body["is_submitted"] is False
    await db.refresh(workspace)
    assert workspace.status != WorkspaceStatus.SUBMITTED

    # 5. Es canvia un document: cal una revisio nova, que NO hereta l'aprovacio.
    await replace_document(db, ready, "Polissa RC nova.pdf")
    stale = (await client_a.get(f"{root}/packages/{package['id']}")).json()
    assert stale["is_stale"] is True
    assert stale["can_approve"] is False
    assert stale["approval"] is not None, "la r1 segueix aprovada i auditable"

    second = (await client_a.post(f"{root}/packages")).json()["package"]
    assert second["revision_number"] == 2
    assert second["manifest_hash"] != detail["manifest_hash"]
    assert second["approval"] is None, "la r2 no hereta l'aprovacio de la r1"
    assert second["is_submitted"] is False

    # 6. L'historial de revisions es complet i no s'ha esborrat res.
    revisions = (await client_a.get(f"{root}/packages")).json()
    assert [r["revision_number"] for r in revisions] == [2, 1]
    assert revisions[1]["is_approved"] is True
    assert revisions[0]["is_approved"] is False


# --------------------------------------------------------------------------- #
# Cap IA                                                                        #
# --------------------------------------------------------------------------- #


def test_the_package_never_calls_a_model():
    """§25: congelar es copiar i fer un hash. Aprovar es una persona."""
    import inspect

    from app.workspace import manifest

    for module in (service, manifest):
        source = inspect.getsource(module)
        for forbidden in (
            "get_provider",
            "StructuredLLMProvider",
            "anthropic",
            "from app.ai",
            "import app.ai",
        ):
            assert forbidden not in source, f"{module.__name__} fa servir {forbidden}"


async def test_freezing_does_not_copy_any_file(db, ready, storage):
    """§19: la immutabilitat es document_id + SHA-256, no una segona copia."""
    uploads_before = len(storage.uploads)

    await freeze(db, ready)

    assert len(storage.uploads) == uploads_before, "congelar no puja cap fitxer"
    documents = await db.scalar(sa.select(sa.func.count()).select_from(Document))
    assert documents == 1


async def test_an_insurance_document_is_reused_not_duplicated(db, ready, tenant_a):
    """Un mateix fitxer pot acabar a diverses revisions sense duplicar-se."""
    company = ready["company"]
    db.add(
        CompanyInsurance(
            company_id=company.id,
            type=InsuranceType.CIVIL_LIABILITY,
            coverage_amount=Decimal("2000000"),
            currency="EUR",
        )
    )
    await db.commit()
    first = await freeze(db, ready)
    second_hash = first.items[0].document_sha256

    assert second_hash == ready["document"].sha256
    documents = await db.scalar(sa.select(sa.func.count()).select_from(Document))
    assert documents == 1


# --------------------------------------------------------------------------- #
# A. `approved_for_submission` no pot mentir                                    #
# --------------------------------------------------------------------------- #


async def approved_workspace(db, ready):
    """Un workspace amb la revisio 1 aprovada de veritat."""
    package = await freeze(db, ready)
    await approve(db, ready, package)
    await db.refresh(ready["workspace"])
    assert ready["workspace"].status == WorkspaceStatus.APPROVED_FOR_SUBMISSION
    return package


async def test_a_second_revision_stops_the_workspace_from_being_approved(db, ready):
    """L'aprovacio de la r1 no pot mantenir el workspace aprovat quan hi ha r2."""
    workspace = ready["workspace"]
    first = await approved_workspace(db, ready)

    await replace_document(db, ready, "Polissa nova.pdf")
    second = await freeze(db, ready)

    await db.refresh(workspace)
    assert second.revision_number == 2
    assert workspace.status != WorkspaceStatus.APPROVED_FOR_SUBMISSION
    assert workspace.status == WorkspaceStatus.READY_FOR_FINAL_REVIEW
    # I l'aprovacio de la r1 segueix existint, intacta.
    await db.refresh(first)
    assert service.approval_of(first) is not None


async def test_changing_a_document_stops_the_workspace_from_being_approved(db, ready):
    """Canviar una peca invalida l'estat aprovat encara sense congelar res mes."""
    workspace = ready["workspace"]
    first = await approved_workspace(db, ready)

    await replace_document(db, ready, "Substitut.pdf")

    await db.refresh(workspace)
    assert workspace.status != WorkspaceStatus.APPROVED_FOR_SUBMISSION
    await db.refresh(first)
    assert first.status == SubmissionPackageStatus.SUPERSEDED
    assert service.approval_of(first) is not None, "l'aprovacio historica no es toca"


async def test_a_new_compliance_run_stops_the_workspace_from_being_approved(db, ready, tender):
    workspace = ready["workspace"]
    first = await approved_workspace(db, ready)

    await run_compliance(db, ready["company"].id, tender)
    await dossier_service.reconcile_readiness(db, workspace)

    await db.refresh(workspace)
    assert workspace.status != WorkspaceStatus.APPROVED_FOR_SUBMISSION
    assert workspace.status == WorkspaceStatus.PREPARING_BID, (
        "amb l'analisi endarrerida no esta ni a punt per a la revisio final"
    )
    await db.refresh(first)
    assert service.approval_of(first) is not None


async def test_removing_the_selected_document_stops_the_workspace_from_being_approved(
    db, ready
):
    workspace, user = ready["workspace"], ready["user"]
    await approved_workspace(db, ready)

    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], status=EvidenceItemStatus.MISSING, user=user
    )

    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.PREPARING_BID


async def test_a_document_expiring_before_the_deadline_un_approves_the_workspace(db, ready):
    workspace = ready["workspace"]
    await approved_workspace(db, ready)

    document = await db.get(Document, ready["document"].id)
    document.expires_at = (DEADLINE - timedelta(days=5)).date()
    await db.commit()
    await dossier_service.reconcile_readiness(db, workspace)

    await db.refresh(workspace)
    assert workspace.status != WorkspaceStatus.APPROVED_FOR_SUBMISSION


async def test_the_historical_approval_survives_every_invalidation(db, ready):
    """El que canvia es l'estat del workspace, mai l'historial."""
    first = await approved_workspace(db, ready)
    approval_id = service.approval_of(first).id
    manifest = first.manifest_hash

    await replace_document(db, ready, "Un altre.pdf")
    await freeze(db, ready)

    stored = await db.scalar(
        sa.select(SubmissionPackageApproval).where(SubmissionPackageApproval.id == approval_id)
    )
    assert stored is not None
    assert stored.package_manifest_hash == manifest
    assert stored.decision == PackageApprovalDecision.APPROVED_FOR_SUBMISSION
    await db.refresh(first)
    assert first.manifest_json is not None, "i el manifest d'aquell dia tambe hi es"


async def test_a_workspace_that_is_still_coherent_stays_approved(db, ready):
    """La invalidacio no pot ser paranoica: si res no ha canviat, no toca res."""
    workspace = ready["workspace"]
    await approved_workspace(db, ready)

    for _ in range(3):
        await dossier_service.reconcile_readiness(db, workspace)

    await db.refresh(workspace)
    assert workspace.status == WorkspaceStatus.APPROVED_FOR_SUBMISSION


async def test_the_api_reports_the_workspace_no_longer_approved(db, client_a, ready):
    workspace, company = ready["workspace"], ready["company"]
    first = await approved_workspace(db, ready)
    root = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

    await replace_document(db, ready, "Nou.pdf")

    body = (await client_a.get(f"{root}/packages/{first.id}")).json()
    assert body["workspace_status"] != "approved_for_submission"
    assert body["is_stale"] is True
    assert body["approval"] is not None, "la r1 segueix aprovada historicament"
    assert body["is_submitted"] is False


def test_the_status_reconciliation_never_assigns_directly():
    """Tot passa per `set_status` i el graf, tambe la des-aprovacio."""
    import inspect

    from app.services import workspace_dossier

    writers = [
        line.strip()
        for line in inspect.getsource(workspace_dossier).splitlines()
        if line.strip().startswith("workspace.status =")
        and not line.strip().startswith("workspace.status ==")
    ]

    assert set(writers) == {"workspace.status = workspace_service.set_status("}


# --------------------------------------------------------------------------- #
# B. Retencio dels bytes aprovats                                               #
# --------------------------------------------------------------------------- #


async def test_a_document_inside_an_approved_package_cannot_be_deleted(db, client_a, ready):
    """§B: sense els bytes, el SHA del manifest deixaria de ser comprovable."""
    package = await freeze(db, ready)
    await approve(db, ready, package)
    document = ready["document"]

    refused = await client_a.delete(
        f"/api/v1/companies/{ready['company'].id}/documents/{document.id}"
    )

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "document_retained_by_package"
    assert refused.json()["error"]["details"]["package_revisions"] == [1]
    assert await db.get(Document, document.id) is not None
    assert await get_storage().exists(document.storage_key), "i el fitxer segueix al seu lloc"


async def test_the_retained_bytes_still_hash_to_the_package_item(db, client_a, ready):
    """Manifest hash -> SHA del document -> bytes exactes. La cadena sencera."""
    package = await freeze(db, ready)
    await approve(db, ready, package)
    item = package.items[0]

    await client_a.delete(
        f"/api/v1/companies/{ready['company'].id}/documents/{ready['document'].id}"
    )

    payload = await get_storage().download(item.storage_key)
    assert hashlib.sha256(payload).hexdigest() == item.document_sha256
    # I el manifest aprovat segueix apuntant a aquest mateix hash.
    assert package.manifest_json["documents"][0]["sha256"] == item.document_sha256
    approval = service.approval_of(package)
    assert approval.package_manifest_hash == package.manifest_hash


async def test_a_superseded_revision_also_retains_its_documents(db, ready, client_a):
    """Una revisio superada segueix sent historial: els seus bytes tambe."""
    company = ready["company"]
    first = await freeze(db, ready)
    original = ready["document"]

    await replace_document(db, ready, "Substitut.pdf")
    await freeze(db, ready)
    await db.refresh(first)
    assert first.status == SubmissionPackageStatus.SUPERSEDED

    refused = await client_a.delete(
        f"/api/v1/companies/{company.id}/documents/{original.id}"
    )

    assert refused.status_code == 409


async def test_an_unreferenced_document_is_deleted_as_always(db, client_a, ready):
    """La retencio no pot convertir tota la biblioteca en inesborrable."""
    loose = await add_document(db, ready["company"].id, name="Esborrany intern.pdf")

    deleted = await client_a.delete(
        f"/api/v1/companies/{ready['company'].id}/documents/{loose.id}"
    )

    assert deleted.status_code == 204
    # Es consulta la base de dades i no `db.get`: l'esborrat l'ha fet la sessio
    # de l'aplicacio i aquesta encara te l'objecte a l'identity map.
    remaining = await db.scalar(
        sa.select(sa.func.count()).select_from(Document).where(Document.id == loose.id)
    )
    assert remaining == 0
    assert not await get_storage().exists(loose.storage_key)


async def test_the_database_itself_refuses_to_orphan_a_package_item(db, ready):
    """La FK es `RESTRICT`: no depen nomes que el servei se'n recordi."""
    from sqlalchemy.exc import IntegrityError

    package = await freeze(db, ready)
    assert package.items[0].document_id == ready["document"].id

    with pytest.raises(IntegrityError):
        await db.execute(
            sa.delete(Document).where(Document.id == ready["document"].id)
        )
        await db.flush()
    await db.rollback()


def test_no_endpoint_can_overwrite_the_bytes_of_a_document():
    """L'altra meitat de la retencio: els bytes no es poden substituir.

    `DocumentUpdate` nomes edita metadata i la clau d'storage es genera amb un
    UUID a cada pujada, aixi que no hi ha cap cami que reescrigui una clau
    existent. Aixo ho fixa perque no s'hi obri sense adonar-se'n.
    """
    from app.schemas.documents import DocumentUpdate

    immutable = {"storage_key", "sha256", "file_size", "mime_type", "original_filename"}

    assert immutable.isdisjoint(set(DocumentUpdate.model_fields))


# --------------------------------------------------------------------------- #
# Regressions trobades validant contra infraestructura real (5D)                #
# --------------------------------------------------------------------------- #


async def test_the_freeze_response_lists_the_documents_it_just_froze(db, client_a, ready):
    """La resposta de congelar deia `document_count: 1` amb `items: []`.

    Un paquet acabat de crear ja te les col·leccions inicialitzades a buides a la
    sessio, i la consulta posterior no les sobreescrivia. La pantalla ensenyava
    un paquet congelat sense cap document.
    """
    root = f"/api/v1/companies/{ready['company'].id}/workspaces/{ready['workspace'].id}"

    frozen = (await client_a.post(f"{root}/packages")).json()["package"]

    assert frozen["coverage"]["document_count"] == 1
    assert len(frozen["items"]) == 1, "el que es congela s'ha de poder veure a l'acte"
    assert frozen["items"][0]["document_sha256"] == ready["document"].sha256
    # I ha de coincidir amb el que retorna llegir-lo despres.
    fetched = (await client_a.get(f"{root}/packages/{frozen['id']}")).json()
    assert [i["id"] for i in fetched["items"]] == [i["id"] for i in frozen["items"]]


# --------------------------------------------------------------------------- #
# El paquet davant d'un canvi de la licitacio                                   #
# --------------------------------------------------------------------------- #


async def _revise(db, tender, **changes):
    """Aplica un canvi a la licitacio i n'enregistra la revisio."""
    for field, value in changes.items():
        setattr(tender, field, value)
    outcome = await lifecycle.record_revision(db, tender)
    await db.commit()
    return outcome


async def _add_tender_document(db, tender, *, url, sha=None):
    """Un document de la LICITACIO —un plec—, no un document corporatiu."""
    document = TenderDocument(
        tender_id=tender.id,
        url=url,
        name="PCAP",
        document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
    )
    db.add(document)
    await db.flush()
    if sha is not None:
        await _replace_content(db, document, sha=sha)
    await db.commit()
    return document


async def _replace_content(db, document, *, sha):
    """Substitueix el contingut vigent d'un plec per un de nou."""
    previous = (
        await db.scalars(
            sa.select(TenderDocumentFile).where(
                TenderDocumentFile.tender_document_id == document.id
            )
        )
    ).all()
    for file in previous:
        file.is_current = False
    content = TenderDocumentContent(
        sha256=sha,
        storage_key=f"documents/{sha}.pdf",
        file_size=1024,
        mime_type="application/pdf",
        is_text_extractable=True,
    )
    db.add(content)
    await db.flush()
    db.add(
        TenderDocumentFile(
            tender_document_id=document.id,
            content_id=content.id,
            source_url=document.url,
            version_number=len(previous) + 1,
            is_current=True,
            download_status=DownloadStatus.DOWNLOADED,
            downloaded_at=datetime.now(UTC),
        )
    )
    await db.commit()


async def test_freezing_records_which_state_of_the_tender_it_was_frozen_against(
    db, ready, tender
):
    """Sense aixo, un paquet aprovat no pot dir si es d'abans o de despres."""
    revision = (await lifecycle.record_revision(db, tender)).revision
    await db.commit()

    package = await freeze(db, ready)

    assert package.tender_revision_id == revision.id
    assert package.tender_normalized_hash == revision.normalized_hash


async def test_an_approved_package_survives_a_new_plec_but_stops_being_current(
    db, ready, tender
):
    """El cas que importa: aprovat contra r1, i despres canvia el plec.

    L'aprovacio NO es desfa. Segueix dient qui va signar, quan i sobre quin
    estat, perque es historia i la historia no es reescriu. El que canvia es la
    resposta a una altra pregunta —si aixo encara s'envia— i abans les dues
    preguntes compartien una sola resposta.
    """
    document = await _add_tender_document(db, tender, url="https://x/pcap.pdf", sha="a" * 64)
    await lifecycle.record_revision(db, tender)
    await db.commit()

    package = await freeze(db, ready)
    approval = await approve(db, ready, package)
    signed_at, signed_by = approval.approved_at, approval.approved_by

    await _replace_content(db, document, sha="b" * 64)
    await lifecycle.record_revision(db, tender)
    await db.commit()

    stale = await service.evaluate_staleness(db, ready["workspace"], package)
    assert "tender_changed" in stale.reasons

    # L'aprovacio, intacta.
    await db.refresh(approval)
    assert (approval.approved_at, approval.approved_by) == (signed_at, signed_by)
    assert approval.decision == PackageApprovalDecision.APPROVED_FOR_SUBMISSION
    # I el paquet tambe: segueix APROVAT, no "superat" ni degradat a res.
    await db.refresh(package)
    assert package.status == SubmissionPackageStatus.APPROVED
    assert package.tender_normalized_hash is not None


async def test_a_document_that_only_moved_does_not_invalidate_an_approved_package(
    db, ready, tender
):
    """URL nova amb el mateix contingut: no ha canviat res del que es demana."""
    document = await _add_tender_document(
        db, tender, url="https://x/vell/pcap.pdf", sha="a" * 64
    )
    await lifecycle.record_revision(db, tender)
    await db.commit()

    package = await freeze(db, ready)
    await approve(db, ready, package)

    document.url = "https://x/nou/pcap.pdf"
    await lifecycle.record_revision(db, tender)
    await db.commit()

    stale = await service.evaluate_staleness(db, ready["workspace"], package)

    assert "tender_changed" not in stale.reasons
    assert stale.is_stale is False


async def test_a_new_deadline_makes_an_approved_package_stop_being_presentable(
    db, ready, tender
):
    await lifecycle.record_revision(db, tender)
    await db.commit()
    package = await freeze(db, ready)
    await approve(db, ready, package)

    await _revise(db, tender, deadline_at=DEADLINE + timedelta(days=10))

    stale = await service.evaluate_staleness(db, ready["workspace"], package)
    assert "tender_changed" in stale.reasons


async def test_a_package_frozen_before_the_tender_had_history_is_not_assumed_coherent(
    db, ready, tender
):
    """NULL vol dir "no ho sabem", i "no ho sabem" no es "quadra"."""
    package = await freeze(db, ready)
    assert package.tender_revision_id is None

    await lifecycle.record_revision(db, tender)
    await db.commit()

    stale = await service.evaluate_staleness(db, ready["workspace"], package)
    assert "tender_revision_unknown" in stale.reasons


async def test_a_tender_change_blocks_the_approval_of_a_frozen_package(db, ready, tender):
    """No s'aprova en silenci una cosa que ja no es la que es va congelar."""
    await lifecycle.record_revision(db, tender)
    await db.commit()
    package = await freeze(db, ready)

    await _revise(db, tender, deadline_at=DEADLINE + timedelta(days=10))

    with pytest.raises(service.PackageStaleError) as excinfo:
        await approve(db, ready, package)

    assert "tender_changed" in excinfo.value.details["reasons"]


async def test_after_a_tender_change_the_path_back_is_a_new_revision(db, ready, tender):
    """Tornar al cami de revisio es el cami que ja hi havia: congelar de nou."""
    await lifecycle.record_revision(db, tender)
    await db.commit()
    first = await freeze(db, ready)
    await approve(db, ready, first)
    assert ready["workspace"].status == WorkspaceStatus.APPROVED_FOR_SUBMISSION

    await _revise(db, tender, deadline_at=DEADLINE + timedelta(days=10))

    second = await freeze(db, ready)

    assert second.revision_number == first.revision_number + 1
    assert ready["workspace"].status == WorkspaceStatus.READY_FOR_FINAL_REVIEW
    # La revisio nova sap contra quin estat es congela, i es el nou.
    assert second.tender_revision_id != first.tender_revision_id
    stale = await service.evaluate_staleness(db, ready["workspace"], second)
    assert stale.is_stale is False
    # I la primera no s'ha tocat: segueix sent el que es va aprovar aquell dia.
    await db.refresh(first)
    assert first.tender_revision_id is not None


async def test_the_api_says_whether_an_approved_package_is_still_presentable(
    db, client_a, ready, tender
):
    await lifecycle.record_revision(db, tender)
    await db.commit()
    root = f"/api/v1/companies/{ready['company'].id}/workspaces/{ready['workspace'].id}"
    package = await freeze(db, ready)
    await approve(db, ready, package)

    before = (await client_a.get(f"{root}/packages/{package.id}")).json()
    assert before["coherent_for_submission"] is True
    assert before["tender_revision_number"] == 1

    await _revise(db, tender, deadline_at=DEADLINE + timedelta(days=10))

    after = (await client_a.get(f"{root}/packages/{package.id}")).json()
    assert after["coherent_for_submission"] is False
    assert "tender_changed" in after["stale_reasons"]
    # Aprovat segueix sent aprovat: son dues preguntes diferents.
    assert after["approval"]["decision"] == "approved_for_submission"

    listed = (await client_a.get(f"{root}/packages")).json()
    assert listed[0]["is_approved"] is True
    assert listed[0]["tender_changed_since_freeze"] is True


# --------------------------------------------------------------------------- #
# Una simulacio no arriba mai a l'aprovacio final                               #
# --------------------------------------------------------------------------- #


async def _run_provider(db, run_id, provider: str, model: str = "x") -> None:
    """Canvia qui va respondre una extraccio. Nomes per muntar l'escenari."""
    run = await db.get(RequirementExtractionRun, run_id)
    run.provider = provider
    run.model = model
    await db.commit()


class TestSimulacioNoAprovable:
    """Congelar un paquet de simulacio si; aprovar-lo, no.

    Congelar-lo cal: es l'unica manera de provar la pantalla de revisio final
    en desenvolupament. Aprovar-lo seria una altra cosa: l'aprovacio final es
    l'unic moment en que una persona signa "aixo es exactament el que
    presentarem", i signar-ho sobre requisits que ningu ha extret de cap plec
    convertiria un artefacte de proves en un document amb aparença d'aprovat.

    La regla viu al domini i no a la configuracio d'entorn a proposit: que un
    desplegament estigui marcat com a desenvolupament no fa menys fals el
    paquet.
    """

    async def test_una_simulacio_si_que_es_pot_congelar(self, db, ready):
        """Cal poder-hi arribar: si no, la pantalla no es pot provar."""
        await _run_provider(db, ready["requirement_run"].id, "fixture")

        package = await freeze(db, ready)

        assert package.status == SubmissionPackageStatus.FROZEN
        assert package.manifest_hash

    async def test_una_simulacio_no_es_pot_aprovar(self, db, ready):
        await _run_provider(db, ready["requirement_run"].id, "fixture")
        package = await freeze(db, ready)

        with pytest.raises(service.PackageSimulationError) as raised:
            await approve(db, ready, package)

        assert raised.value.code == "simulation_not_approvable"

    async def test_sense_cap_motor_configurat_tampoc(self, db, ready):
        """`fake` davant d'un usuari no vol dir "sense requisits": vol dir sense motor."""
        await _run_provider(db, ready["requirement_run"].id, "fake")
        package = await freeze(db, ready)

        with pytest.raises(service.PackageSimulationError):
            await approve(db, ready, package)

    async def test_una_analisi_real_segueix_aprovant_se_igual(self, db, ready):
        """La regla no toca res del cami de sempre."""
        package = await freeze(db, ready)

        approval = await approve(db, ready, package)

        assert approval.decision == PackageApprovalDecision.APPROVED_FOR_SUBMISSION
        assert package.status == SubmissionPackageStatus.APPROVED

    async def test_barrejar_real_i_simulacio_tampoc_saprova(self, db, ready):
        """El paquet no es mes fiable que la seva pitjor meitat.

        Es el cas incomode: el paquet cita una extraccio real, pero el
        compliment sobre el qual es va congelar es va calcular contra una altra
        que era simulada. Mirar nomes una de les dues portes deixaria passar
        exactament aixo.
        """
        package = await freeze(db, ready)

        simulada = RequirementExtractionRun(
            tender_id=ready["tender"].id,
            status=AnalysisRunStatus.COMPLETED,
            input_fingerprint=uuid.uuid4().hex * 2,
            extractor_version="tender-requirements-v1",
            prompt_version="requirements-v1",
            schema_version="requirements-schema-v1",
            provider="fixture",
            model="fixture-plec-v1",
            is_current=False,
        )
        db.add(simulada)
        await db.flush()
        compliance = await db.get(CompanyComplianceRun, package.compliance_run_id)
        compliance.requirement_extraction_run_id = simulada.id
        await db.commit()

        with pytest.raises(service.PackageSimulationError):
            await approve(db, ready, package)

    async def test_un_rebuig_si_que_es_pot_registrar_sobre_una_simulacio(self, db, ready):
        """Nomes es bloqueja l'APROVACIO. Dir que no a una simulacio no fa mal a ningu."""
        await _run_provider(db, ready["requirement_run"].id, "fixture")
        package = await freeze(db, ready)

        rejection = await service.approve_package(
            db,
            ready["workspace"],
            package,
            user=ready["user"],
            role=MembershipRole.OWNER,
            decision=PackageApprovalDecision.REJECTED,
            comment="Es una simulacio.",
        )

        assert rejection.decision == PackageApprovalDecision.REJECTED

    async def test_una_aprovacio_historica_real_no_canvia(self, db, ready):
        """El que ja estava aprovat sobre una analisi real segueix aprovat.

        La regla nova mira el moment d'aprovar, no reescriu res del passat: un
        paquet signat abans continua sent el que era i amb el mateix hash.
        """
        package = await freeze(db, ready)
        approval = await approve(db, ready, package)
        hash_signat = approval.package_manifest_hash

        # Encara que despres algu canvies el proveidor del run que el sosté.
        await _run_provider(db, ready["requirement_run"].id, "fixture")
        await db.refresh(package)
        await db.refresh(approval)

        assert package.status == SubmissionPackageStatus.APPROVED
        assert approval.package_manifest_hash == hash_signat
        assert approval.decision == PackageApprovalDecision.APPROVED_FOR_SUBMISSION

    async def test_lapi_no_ofereix_el_boto_daprovar_una_simulacio(self, client_a, ready, db):
        """`can_approve` ha de ser fals abans que ningu premi res."""
        await _run_provider(db, ready["requirement_run"].id, "fixture")
        package = await freeze(db, ready)

        response = await client_a.get(
            f"/api/v1/companies/{ready['company'].id}/workspaces/"
            f"{ready['workspace'].id}/packages/{package.id}"
        )

        body = response.json()
        assert response.status_code == 200
        assert body["can_approve"] is False
        assert body["simulation_only"] is True
        assert "simulation" in " ".join(body["analysis_bases"])

    async def test_lapi_rebutja_laprovacio_amb_un_codi_llegible(self, client_a, ready, db):
        await _run_provider(db, ready["requirement_run"].id, "fixture")
        package = await freeze(db, ready)

        response = await client_a.post(
            f"/api/v1/companies/{ready['company'].id}/workspaces/"
            f"{ready['workspace'].id}/packages/{package.id}/approvals",
            json={"decision": "approved_for_submission", "comment": "Endavant."},
        )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "simulation_not_approvable"


# --------------------------------------------------------------------------- #
# Llegir no escriu                                                              #
# --------------------------------------------------------------------------- #
#
# Un GET pot CALCULAR l'estat efectiu i ensenyar-lo, pero no el pot desar. El
# 11/09/2026 una simple comprovacio de lectura (login -> candidatura -> dossier)
# va degradar un workspace real de `ready_for_final_review` a `preparing_bid`,
# perque les lectures reconciliaven. Les transicions persistents nomes passen
# per camins de mutacio explicits.

#: Tot el que una lectura del workspace podria tocar.
READ_ONLY_TABLES = (
    "bid_workspaces",
    "workspace_evidence_items",
    "workspace_checklist_items",
    "workspace_decisions",
    "workspace_tasks",
    "workspace_notes",
    "submission_packages",
    "submission_package_items",
    "submission_package_approvals",
    "audit_logs",
)


async def _rows(session_factory) -> dict[str, list[tuple]]:
    """Totes les files, tal com son a la base: sense passar per cap sessio oberta."""
    async with session_factory() as session:
        out = {}
        for table in READ_ONLY_TABLES:
            rows = (await session.execute(sa.text(f"select * from {table}"))).mappings().all()
            out[table] = sorted(tuple(sorted((k, str(v)) for k, v in r.items())) for r in rows)
        return out


def _read_paths(ready, package) -> list[str]:
    company, workspace = ready["company"], ready["workspace"]
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"
    return [
        base,
        f"/api/v1/companies/{company.id}/tenders/{ready['tender'].id}/workspace",
        f"{base}/dossier",
        f"{base}/readiness",
        f"{base}/decisions",
        f"{base}/notes",
        f"{base}/packages",
        f"{base}/packages/{package.id}",
        f"{base}/packages/{package.id}/manifest",
        f"{base}/packages/{package.id}/approvals",
    ]


async def _external_change(db, workspace) -> None:
    """Un canvi que no passa per cap mutacio del producte: l'etiqueta queda enrere."""
    await db.execute(
        sa.update(WorkspaceEvidenceItem)
        .where(WorkspaceEvidenceItem.workspace_id == workspace.id)
        .values(status=EvidenceItemStatus.SUGGESTED, selected_by=None, selected_at=None)
    )
    await db.commit()


@pytest.mark.parametrize("approved", [False, True], ids=["congelat", "aprovat"])
async def test_reading_never_writes_even_when_the_stored_state_is_stale(
    db, engine, session_factory, client_a, ready, approved
):
    workspace = ready["workspace"]
    package = await (approved_workspace(db, ready) if approved else freeze(db, ready))
    await db.refresh(workspace)
    stored = workspace.status
    assert stored == (
        WorkspaceStatus.APPROVED_FOR_SUBMISSION
        if approved
        else WorkspaceStatus.READY_FOR_FINAL_REVIEW
    )
    await _external_change(db, workspace)
    before = await _rows(session_factory)

    with DmlRecorder(engine) as dml:
        for _ in range(3):
            for path in _read_paths(ready, package):
                response = await client_a.get(path)
                assert response.status_code == 200, (path, response.text)

    assert dml.statements == [], "cap GET pot escriure"
    assert await _rows(session_factory) == before, "totes les files identiques, cap de nova"

    # Llegir no escriu, pero diu la veritat: l'estat efectiu es a la resposta.
    base = _read_paths(ready, package)[0]
    assert (await client_a.get(base)).json()["status"] == "preparing_bid"
    detail = (await client_a.get(f"{base}/packages/{package.id}")).json()
    assert detail["workspace_status"] == "preparing_bid"
    assert detail["status"] == ("superseded" if approved else "frozen")
    await db.refresh(workspace)
    await db.refresh(package)
    assert workspace.status == stored
    assert package.status == (
        SubmissionPackageStatus.APPROVED if approved else SubmissionPackageStatus.FROZEN
    )

    # La mutacio explicita si que fa la transicio, i la desa.
    assert (await client_a.post(f"{base}/checklist/regenerate")).status_code == 200
    await db.refresh(workspace)
    await db.refresh(package)
    assert workspace.status == WorkspaceStatus.PREPARING_BID
    assert package.status == (
        SubmissionPackageStatus.SUPERSEDED if approved else SubmissionPackageStatus.FROZEN
    )
