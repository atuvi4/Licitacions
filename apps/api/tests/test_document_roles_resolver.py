"""Resolutor de rols efectius (3B-2 §3.2) i la seleccio global de l'extraccio.

Ordre, de mes a menys autoritat:

1. override de l'empresa (`tender_document_role_decisions`) **de la mateixa
   versio del fitxer**; un override d'una versio anterior s'ignora i es diu
   (`stale_override`);
2. revisio de l'operador (`document_role_reviews`) de la mateixa versio;
3. suggeriment de la classificacio global vigent amb confianca alta i no duplicat;
4. el `document_type` del feed.

Cap capa escriu sobre una altra: el `document_type` del feed queda intacte en
tots els casos. L'extraccio global llegeix el resolutor amb `company_id=None`:
l'override d'una empresa no canvia la seleccio compartida (opcio A).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.core.errors import ConflictError, ValidationError
from app.models.classification import TenderDocumentRoleDecision
from app.models.enums import (
    DocumentRole,
    GlobalClassificationTrigger,
    RoleReviewAuthority,
    RoleSource,
    SourceRefKind,
    TenderDocumentType,
)
from app.models.global_classification import DocumentRoleReview, GlobalDocumentRoleSuggestion
from app.models.provenance import TenderSourceRef
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.services import document_roles
from app.services import global_document_classification as global_classification
from app.services.requirement_analysis import collect_analyzable_documents
from tests.fixtures.pdf_builder import build_pdf
from tests.test_global_classification import (
    PCAP_PDF,
    PPT_PDF,
    processed_feed_document,
    public_package,
)
from tests.test_platform_onboarding import create_operator

pytestmark = pytest.mark.asyncio

#: Un PDF amb text que no diu que es: el classificador no en pot dir res segur.
NEUTRAL_PDF = build_pdf(["Document de treball annex\n" + " ".join(["Contingut sense titol."] * 20)])


async def current_sha(db, document_id) -> str:
    return await db.scalar(
        sa.select(TenderDocumentContent.sha256)
        .join(TenderDocumentFile, TenderDocumentFile.content_id == TenderDocumentContent.id)
        .where(
            TenderDocumentFile.tender_document_id == document_id,
            TenderDocumentFile.is_current.is_(True),
        )
    )


async def decide(db, company, document, role, sha) -> TenderDocumentRoleDecision:
    decision = TenderDocumentRoleDecision(
        company_id=company.id,
        tender_document_id=document.id,
        role=role,
        content_sha256=sha,
        decided_at=datetime.now(UTC),
    )
    db.add(decision)
    await db.commit()
    return decision


@pytest.fixture
async def operator(db):
    return await create_operator(db)


@pytest.fixture
async def classified(db, public_tender):
    pcap, ppt = await public_package(db, public_tender)
    run = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    return pcap, ppt, run


@pytest.fixture
async def public_tender(db):
    from tests.test_global_classification import make_public_tender

    return await make_public_tender(db)


# --------------------------------------------------------------------------- #
# Ordre de les capes                                                           #
# --------------------------------------------------------------------------- #


async def test_sense_res_mes_mana_el_feed(db, public_tender):
    pcap = await processed_feed_document(
        db, public_tender, "PCAP.pdf", TenderDocumentType.ADMINISTRATIVE_TERMS, PCAP_PDF
    )
    roles = await document_roles.effective_document_roles(db, public_tender.id, None)
    assert roles[pcap.id].source == RoleSource.FEED
    assert roles[pcap.id].document_type == TenderDocumentType.ADMINISTRATIVE_TERMS
    assert roles[pcap.id].role is None


async def test_el_classificador_global_amb_confianca_alta_passa_davant_del_feed(db, public_tender):
    # El feed el publica com a "addicional"; el contingut diu que es el PPT.
    ppt = await processed_feed_document(
        db, public_tender, "Memoria.pdf", TenderDocumentType.ADDITIONAL, PPT_PDF
    )
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    roles = await document_roles.effective_document_roles(db, public_tender.id, None)
    assert roles[ppt.id].source == RoleSource.GLOBAL_CLASSIFIER
    assert (roles[ppt.id].role, roles[ppt.id].document_type) == (
        DocumentRole.PPT,
        TenderDocumentType.TECHNICAL_TERMS,
    )
    await db.refresh(ppt)
    assert ppt.document_type == TenderDocumentType.ADDITIONAL  # el feed, intacte


async def test_un_suggeriment_sense_confianca_alta_no_mana(db, public_tender):
    doc = await processed_feed_document(
        db, public_tender, "Annex.pdf", TenderDocumentType.ADDITIONAL, NEUTRAL_PDF
    )
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    roles = await document_roles.effective_document_roles(db, public_tender.id, None)
    assert roles[doc.id].source == RoleSource.FEED
    assert roles[doc.id].document_type == TenderDocumentType.ADDITIONAL


async def test_la_revisio_de_l_operador_passa_davant_del_classificador(db, classified, operator):
    pcap, _ppt, run = classified
    sha = await current_sha(db, pcap.id)
    await document_roles.record_role_review(
        db,
        document_id=pcap.id,
        content_sha256=sha,
        role=DocumentRole.CHARACTERISTICS_TABLE,
        actor=operator,
        reason="La primera pagina es el quadre de caracteristiques.",
        based_on_run_id=run.id,
    )
    await db.commit()

    roles = await document_roles.effective_document_roles(db, pcap.tender_id, None)
    assert roles[pcap.id].source == RoleSource.OPERATOR
    assert roles[pcap.id].role == DocumentRole.CHARACTERISTICS_TABLE
    assert roles[pcap.id].document_type == TenderDocumentType.ADMINISTRATIVE_TERMS


async def test_l_override_de_l_empresa_passa_davant_de_tot_pero_nomes_per_a_ella(
    db, classified, operator, tenant_a, tenant_b
):
    pcap, ppt, run = classified
    _user, company_a = tenant_a
    _user_b, company_b = tenant_b
    sha = await current_sha(db, ppt.id)
    await document_roles.record_role_review(
        db,
        document_id=ppt.id,
        content_sha256=sha,
        role=DocumentRole.PPT,
        actor=operator,
        reason="Confirmat.",
        based_on_run_id=run.id,
    )
    await db.commit()
    await decide(db, company_a, ppt, DocumentRole.NOTICE, sha)

    for_a = await document_roles.effective_document_roles(db, ppt.tender_id, company_a.id)
    assert for_a[ppt.id].source == RoleSource.PERSON
    assert (for_a[ppt.id].role, for_a[ppt.id].document_type) == (
        DocumentRole.NOTICE,
        TenderDocumentType.NOTICE,
    )
    for_b = await document_roles.effective_document_roles(db, ppt.tender_id, company_b.id)
    assert for_b[ppt.id].source == RoleSource.OPERATOR
    shared = await document_roles.effective_document_roles(db, ppt.tender_id, None)
    assert shared[ppt.id].source == RoleSource.OPERATOR
    await db.refresh(ppt)
    assert ppt.document_type == TenderDocumentType.TECHNICAL_TERMS


async def test_un_override_d_una_versio_anterior_s_ignora_i_es_diu(db, classified, tenant_a):
    pcap, _ppt, _run = classified
    _user, company = tenant_a
    await decide(db, company, pcap, DocumentRole.NOTICE, "0" * 64)

    roles = await document_roles.effective_document_roles(db, pcap.tender_id, company.id)
    assert roles[pcap.id].source == RoleSource.STALE_OVERRIDE
    # Mana la capa seguent (el classificador global), i es diu quina.
    assert roles[pcap.id].fallback_source == RoleSource.GLOBAL_CLASSIFIER
    assert roles[pcap.id].role == DocumentRole.PCAP
    assert roles[pcap.id].document_type == TenderDocumentType.ADMINISTRATIVE_TERMS
    await db.refresh(pcap)
    assert pcap.document_type == TenderDocumentType.ADMINISTRATIVE_TERMS


async def test_una_revisio_d_una_versio_anterior_tampoc_mana(db, classified, operator):
    pcap, _ppt, run = classified
    db.add(
        DocumentRoleReview(
            tender_document_id=pcap.id,
            content_sha256="1" * 64,
            role=DocumentRole.NOTICE,
            authority=RoleReviewAuthority.PLATFORM_OPERATOR,
            actor_id=operator.id,
            reason="Sobre una versio antiga.",
            based_on_run_id=run.id,
        )
    )
    await db.commit()
    roles = await document_roles.effective_document_roles(db, pcap.tender_id, None)
    assert roles[pcap.id].source == RoleSource.GLOBAL_CLASSIFIER


# --------------------------------------------------------------------------- #
# Revisio de l'operador: nomes d'afegir                                        #
# --------------------------------------------------------------------------- #


async def test_una_revisio_nova_substitueix_l_anterior_sense_tocar_la(db, classified, operator):
    pcap, _ppt, run = classified
    sha = await current_sha(db, pcap.id)
    suggestion = await db.scalar(
        sa.select(GlobalDocumentRoleSuggestion).where(
            GlobalDocumentRoleSuggestion.run_id == run.id,
            GlobalDocumentRoleSuggestion.tender_document_id == pcap.id,
        )
    )
    first = await document_roles.record_role_review(
        db,
        document_id=pcap.id,
        content_sha256=sha,
        role=DocumentRole.CHARACTERISTICS_TABLE,
        actor=operator,
        reason="Primera lectura.",
        based_on_run_id=run.id,
        based_on_suggestion_id=suggestion.id,
    )
    await db.commit()
    second = await document_roles.record_role_review(
        db,
        document_id=pcap.id,
        content_sha256=sha,
        role=DocumentRole.PCAP,
        actor=operator,
        reason="Rectifico: es el PCAP.",
        based_on_run_id=run.id,
    )
    await db.commit()

    assert second.supersedes_id == first.id
    assert first.supersedes_id is None
    rows = (
        await db.scalars(
            sa.select(DocumentRoleReview).where(DocumentRoleReview.tender_document_id == pcap.id)
        )
    ).all()
    assert {r.role for r in rows} == {DocumentRole.CHARACTERISTICS_TABLE, DocumentRole.PCAP}
    assert first.authority == RoleReviewAuthority.PLATFORM_OPERATOR
    assert first.based_on_suggestion_id == suggestion.id
    roles = await document_roles.effective_document_roles(db, pcap.tender_id, None)
    assert (roles[pcap.id].source, roles[pcap.id].role) == (RoleSource.OPERATOR, DocumentRole.PCAP)


async def test_una_revisio_sense_motiu_o_d_una_altra_versio_es_refusa(db, classified, operator):
    pcap, _ppt, run = classified
    sha = await current_sha(db, pcap.id)
    with pytest.raises(ValidationError):
        await document_roles.record_role_review(
            db,
            document_id=pcap.id,
            content_sha256=sha,
            role=DocumentRole.PCAP,
            actor=operator,
            reason="   ",
            based_on_run_id=run.id,
        )
    with pytest.raises(ConflictError) as caught:
        await document_roles.record_role_review(
            db,
            document_id=pcap.id,
            content_sha256="2" * 64,
            role=DocumentRole.PCAP,
            actor=operator,
            reason="Versio que ja no es la vigent.",
            based_on_run_id=run.id,
        )
    assert caught.value.code == "document_version_changed"


# --------------------------------------------------------------------------- #
# La seleccio de l'extraccio global                                            #
# --------------------------------------------------------------------------- #


async def test_l_extraccio_global_llegeix_el_tipus_efectiu(db, public_tender, operator):
    """El feed diu "addicional" i "administratiu"; el resolutor diu PPT i anunci."""
    ppt = await processed_feed_document(
        db, public_tender, "Memoria.pdf", TenderDocumentType.ADDITIONAL, PPT_PDF
    )
    pcap = await processed_feed_document(
        db, public_tender, "PCAP.pdf", TenderDocumentType.ADMINISTRATIVE_TERMS, PCAP_PDF
    )
    run = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    await document_roles.record_role_review(
        db,
        document_id=pcap.id,
        content_sha256=await current_sha(db, pcap.id),
        role=DocumentRole.NOTICE,
        actor=operator,
        reason="Es l'anunci, no el plec.",
        based_on_run_id=run.id,
    )
    await db.commit()

    documents, _skipped = await collect_analyzable_documents(db, public_tender.id)
    by_id = {uuid.UUID(d.tender_document_id): d for d in documents}
    assert set(by_id) == {ppt.id}  # l'anunci queda fora de la seleccio per defecte
    assert by_id[ppt.id].document_type == TenderDocumentType.TECHNICAL_TERMS


async def test_l_override_divergent_d_una_empresa_no_canvia_la_seleccio(db, classified, tenant_a):
    pcap, ppt, _run = classified
    _user, company = tenant_a
    before, _ = await collect_analyzable_documents(db, pcap.tender_id)
    await decide(db, company, ppt, DocumentRole.NOTICE, await current_sha(db, ppt.id))
    await decide(db, company, pcap, DocumentRole.OTHER, await current_sha(db, pcap.id))

    after, _ = await collect_analyzable_documents(db, pcap.tender_id)
    assert [(d.tender_document_id, d.document_type) for d in after] == [
        (d.tender_document_id, d.document_type) for d in before
    ]
    assert {d.tender_document_id for d in after} == {str(pcap.id), str(ppt.id)}


async def test_el_hash_d_overrides_canvia_amb_les_decisions_de_l_empresa(
    db, classified, tenant_a, tenant_b
):
    pcap, _ppt, _run = classified
    _user, company_a = tenant_a
    _user_b, company_b = tenant_b
    assert await document_roles.role_overrides_hash(db, pcap.tender_id, company_a.id) is None
    await decide(db, company_a, pcap, DocumentRole.OTHER, await current_sha(db, pcap.id))
    digest = await document_roles.role_overrides_hash(db, pcap.tender_id, company_a.id)
    assert digest is not None and len(digest) == 64
    assert await document_roles.role_overrides_hash(db, pcap.tender_id, company_b.id) is None


async def test_un_document_retirat_no_surt_al_resolutor(db, classified):
    pcap, ppt, _run = classified
    ppt.removed_at = datetime.now(UTC)
    await db.commit()
    roles = await document_roles.effective_document_roles(db, pcap.tender_id, None)
    assert set(roles) == {pcap.id}
    assert isinstance(next(iter(roles.values())), document_roles.EffectiveRole)


# --------------------------------------------------------------------------- #
# Fix round 1                                                                  #
# --------------------------------------------------------------------------- #


async def test_un_suggeriment_alt_d_un_duplicat_no_mana(db, public_tender):
    """M7: el mateix text en bytes diferents; la copia porta `duplicate_of` i no mana."""
    from tests.test_global_classification import PPT_TEXT

    # Mateix text normalitzat (espais), bytes diferents: no es un alies de bytes.
    copy_pdf = build_pdf([PPT_TEXT.replace("\n", "\n\n")])
    assert copy_pdf != PPT_PDF
    ppt = await processed_feed_document(
        db, public_tender, "PPT.pdf", TenderDocumentType.ADDITIONAL, PPT_PDF
    )
    copy = await processed_feed_document(
        db, public_tender, "PPT_copia.pdf", TenderDocumentType.ADDITIONAL, copy_pdf
    )
    run = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    suggestions = await global_classification.run_suggestions(db, run)
    # Quin dels dos es el canonic depen de l'ordre d'arribada (mateix instant ->
    # id): el que porta `duplicate_of` es la copia.
    duplicate, canonical = (
        (copy, ppt) if suggestions[copy.id].signals["duplicate_of"] else (ppt, copy)
    )
    assert suggestions[duplicate.id].confidence == "high"
    assert suggestions[duplicate.id].signals["duplicate_of"] == str(canonical.id)

    roles = await document_roles.effective_document_roles(db, public_tender.id, None)
    assert roles[canonical.id].source == RoleSource.GLOBAL_CLASSIFIER
    assert (roles[duplicate.id].source, roles[duplicate.id].document_type) == (
        RoleSource.FEED,
        TenderDocumentType.ADDITIONAL,
    )


async def _file_ref(db, document) -> TenderSourceRef:
    file = await db.scalar(
        sa.select(TenderDocumentFile).where(
            TenderDocumentFile.tender_document_id == document.id,
            TenderDocumentFile.is_current.is_(True),
        )
    )
    ref = TenderSourceRef(
        tender_id=document.tender_id,
        ref_kind=SourceRefKind.DOCUMENT_FILE,
        tender_document_file_id=file.id,
        tender_document_id=document.id,
        content_sha256=await current_sha(db, document.id),
        location_label="pagina 1",
    )
    db.add(ref)
    await db.commit()
    return ref


async def test_la_cita_de_la_revisio_ha_de_ser_del_mateix_tender_i_document(
    db, classified, operator
):
    """M3: una cita d'un altre tender, o d'un altre document, es refusa (422)."""
    from tests.test_global_classification import make_public_tender

    pcap, ppt, run = classified
    other_tender = await make_public_tender(db)
    other = await processed_feed_document(
        db, other_tender, "PCAP.pdf", TenderDocumentType.ADMINISTRATIVE_TERMS, PCAP_PDF
    )
    sha = await current_sha(db, pcap.id)

    for ref in (await _file_ref(db, other), await _file_ref(db, ppt)):
        with pytest.raises(ValidationError) as caught:
            await document_roles.record_role_review(
                db,
                document_id=pcap.id,
                content_sha256=sha,
                role=DocumentRole.PCAP,
                actor=operator,
                reason="Amb cita.",
                based_on_run_id=run.id,
                source_ref_id=ref.id,
            )
        assert caught.value.code == "source_ref_not_in_tender"

    good = await _file_ref(db, pcap)
    review = await document_roles.record_role_review(
        db,
        document_id=pcap.id,
        content_sha256=sha,
        role=DocumentRole.PCAP,
        actor=operator,
        reason="Amb cita del mateix plec.",
        based_on_run_id=run.id,
        source_ref_id=good.id,
    )
    await db.commit()
    assert review.source_ref_id == good.id
