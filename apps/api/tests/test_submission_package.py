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
from app.models.credentials import CompanyCertification, CompanyInsurance
from app.models.documents import Document, DocumentLink
from app.models.enums import (
    AnalysisRunStatus,
    DocumentCategory,
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
from app.models.tenders import Tender
from app.models.workspace import WorkspaceEvidenceItem
from app.services import bid_workspace as workspace_service
from app.services import submission_package as service
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
        provider="fake",
        model="fake-model-v1",
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
    item = await db.scalar(sa.select(WorkspaceEvidenceItem))
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
    }


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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    first = await freeze(db, ready)
    first_hash = first.manifest_hash
    first_documents = [(i.document_id, i.document_sha256) for i in first.items]

    # Es canvia el document del requisit.
    replacement = await add_document(db, company.id, name="ISO 27001 renovat.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )

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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    package = await freeze(db, ready)

    replacement = await add_document(db, company.id, name="Un altre.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )

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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    first = await freeze(db, ready)
    approval = await approve(db, ready, first)

    # Es canvia una peca i es congela una revisio nova.
    replacement = await add_document(db, company.id, name="Polissa RC 2027.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )
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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    first = await freeze(db, ready)
    replacement = await add_document(db, company.id, name="Nou.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )
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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    package = await freeze(db, ready)
    replacement = await add_document(db, company.id, name="Substitut.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )
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
    replacement = await add_document(db, company.id, name="Polissa RC nova.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )
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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    first = await approved_workspace(db, ready)

    replacement = await add_document(db, company.id, name="Polissa nova.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )
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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    first = await approved_workspace(db, ready)

    replacement = await add_document(db, company.id, name="Substitut.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )

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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    first = await approved_workspace(db, ready)
    approval_id = service.approval_of(first).id
    manifest = first.manifest_hash

    replacement = await add_document(db, company.id, name="Un altre.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )
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
    workspace, company, user = ready["workspace"], ready["company"], ready["user"]
    first = await approved_workspace(db, ready)
    root = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"

    replacement = await add_document(db, company.id, name="Nou.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )

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
    workspace, user, company = ready["workspace"], ready["user"], ready["company"]
    first = await freeze(db, ready)
    original = ready["document"]

    replacement = await add_document(db, company.id, name="Substitut.pdf")
    await dossier_service.update_item(
        db, workspace, ready["evidence_item"], document_id=replacement.id, user=user
    )
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
