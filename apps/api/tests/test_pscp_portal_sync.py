"""Sincronitzacio d'una licitacio amb el portal de la PSCP (`pscp_portal.service`).

Contra el portal fals de `pscp_portal_support`: les respostes enregistrades de Pineda de
Mar, Parc Tauli i Cornella, i fitxers sintetics amb l'MD5 publicat ajustat als seus bytes.
"""

from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.ingestion.pscp_portal import PORTAL_SYNC_TRIGGER, SOURCE_CODE
from app.ingestion.pscp_portal import client as portal_client
from app.ingestion.pscp_portal.client import (
    PortalClient,
    PortalDisabledError,
    PortalFetchError,
)
from app.ingestion.pscp_portal.parser import PortalParseError, download_url
from app.ingestion.pscp_portal.service import (
    PortalSyncNotAvailableError,
    StoredBytes,
    _take_over,
    portal_source,
    sync_tender,
)
from app.models.audit import AuditLog
from app.models.enums import (
    DocumentContributionStatus,
    DocumentRole,
    DownloadStatus,
    ExtractionStatus,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.global_classification import GlobalDocumentClassificationRun
from app.models.sources import Source
from app.models.tender_documents import TenderDocumentFile
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Tender, TenderDocument
from app.services.document_roles import effective_document_roles
from app.services.tender_document_processing import (
    CHECKSUM_MISMATCH,
    current_extraction_for,
    current_file_for,
    get_or_create_content,
    permanently_failed,
)
from tests.pscp_portal_support import (
    CORNELLA,
    CORNELLA_CURRENT,
    PARC_TAULI,
    PARC_TAULI_CURRENT,
    PINEDA,
    PINEDA_CURRENT,
    PINEDA_ORIGINAL,
    cornella,
    parc_tauli,
    pineda,
    resolver,
    source_url,
)

# Pineda 5125/2026, versio vigent de l'anunci (ids del portal).
PCAP, PPT, MEMORIA, DECRET = 302674550, 302674551, 302674549, 302674548
ANNEX_I, ANNEX_III = 302674545, 302674547


@pytest.fixture(autouse=True)
def portal_on(monkeypatch):
    monkeypatch.setattr(settings, "PSCP_PORTAL_ENABLED", True)
    monkeypatch.setattr(settings, "PSCP_PORTAL_PAUSE_SECONDS", 0.0)

    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(portal_client, "_sleep", no_sleep)


async def public_tender(db, expedient: str, publication_id: int, **extra) -> Tender:
    source = Source(code=f"feed-{uuid.uuid4().hex[:8]}", name="Feed de prova")
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title="Licitacio de prova del portal",
        source_url=source_url(expedient, publication_id),
        source_metadata={"pscp_id_intern": expedient},
        **extra,
    )
    db.add(tender)
    await db.commit()
    return tender


async def add_document(db, tender: Tender, url: str, **fields) -> TenderDocument:
    fields.setdefault("name", "document.pdf")
    fields.setdefault("document_type", TenderDocumentType.OTHER)
    document = TenderDocument(tender_id=tender.id, url=url, **fields)
    if document.origin in (None, TenderDocumentOrigin.FEED):
        document.origin = TenderDocumentOrigin.FEED
        document.governing_source_id = tender.source_id
    db.add(document)
    await db.commit()
    return document


async def store_current(db, document: TenderDocument, data: bytes) -> None:
    """Uns bytes ja baixats i vigents per al document (com si ja s'hagues processat)."""
    content, _ = await get_or_create_content(
        db,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="application/pdf",
        detected_extension="pdf",
    )
    db.add(
        TenderDocumentFile(
            tender_document_id=document.id,
            content_id=content.id,
            source_url=document.url,
            version_number=1,
            is_current=True,
            download_status=DownloadStatus.DOWNLOADED,
            downloaded_at=datetime.now(UTC),
        )
    )
    await db.commit()


async def operator_url(db, tender, url, status=DocumentContributionStatus.VERIFIED, data=None):
    now = datetime.now(UTC)
    document = await add_document(
        db,
        tender,
        url,
        origin=TenderDocumentOrigin.OPERATOR_URL,
        contribution_status=status,
        contributed_at=now,
        contribution_source_url=source_url(PINEDA, PINEDA_CURRENT),
        verified_at=now if status == DocumentContributionStatus.VERIFIED else None,
    )
    if data is not None:
        await store_current(db, document, data)
    return document


async def sync(session_factory, tender, fake):
    async with fake.client() as http, PortalClient(client=http) as portal:
        return await sync_tender(session_factory, tender.id, portal=portal, resolver=resolver)


async def documents_of(db, tender) -> list[TenderDocument]:
    """Els documents tal com son ara a la base (les sincronitzacions usen altres sessions)."""
    return list(
        (
            await db.scalars(
                sa.select(TenderDocument)
                .where(TenderDocument.tender_id == tender.id)
                .order_by(TenderDocument.created_at, TenderDocument.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def portal_source_id(db) -> uuid.UUID:
    return await db.scalar(sa.select(Source.id).where(Source.code == SOURCE_CODE))


async def triggers(db, tender) -> list[str]:
    return list(
        (
            await db.scalars(
                sa.select(TenderRevision.trigger)
                .where(TenderRevision.tender_id == tender.id)
                .order_by(TenderRevision.revision_number)
            )
        ).all()
    )


async def pineda_with_feed(db, fake) -> Tender:
    """Pineda com al pilot: el feed ja publica (i governa) el PCAP i el PPT."""
    tender = await public_tender(db, PINEDA, PINEDA_CURRENT)
    await add_document(db, tender, fake.url(PCAP), name="PCAP segellat.pdf")
    await add_document(db, tender, fake.url(PPT), name="PPT.pdf")
    return tender


# --------------------------------------------------------------------------- #
# Primera sincronitzacio                                                       #
# --------------------------------------------------------------------------- #


async def test_pineda_first_sync_brings_the_whole_package(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)

    result = await sync(session_factory, tender, fake)

    counts = result.reconciled.counts()
    # 8 fitxers del portal (11 de la publicacio - PCAP i PPT del feed - 1 buit), l'adjunt
    # d'una resposta, les preguntes i respostes i l'esmena.
    assert counts["created"] == 11
    assert counts["feed_owned"] == 2
    assert counts["removed"] == counts["taken_over"] == counts["not_governed"] == 0
    assert result.state.skipped_empty == ("Memoria castellano.pdf",)
    assert result.partial is False and result.missing_content == []
    assert (result.state.answers, result.state.notices, result.state.amendments) == (5, 0, 1)
    assert result.any_failed is False and result.changed is True

    documents = await documents_of(db, tender)
    source_id = await portal_source_id(db)
    portal = [d for d in documents if d.origin == TenderDocumentOrigin.PORTAL]
    assert len(portal) == 11
    assert all(d.governing_source_id == source_id for d in portal)
    assert all(d.contribution_status == DocumentContributionStatus.NOT_APPLICABLE for d in portal)
    feed = [d for d in documents if d.origin == TenderDocumentOrigin.FEED]
    assert sorted(d.name for d in feed) == ["PCAP segellat.pdf", "PPT.pdf"]
    assert "Memoria castellano.pdf" not in {d.name for d in documents}

    by_name = {d.name: d for d in portal}
    assert by_name["Memoria.pdf"].document_type == TenderDocumentType.OTHER
    assert by_name["Memoria.pdf"].source_document_type_code == "memoriaJustificativaContracte"
    assert by_name["Decret.pdf"].document_type == TenderDocumentType.OTHER
    assert by_name["Annex III del plec tècnic_Acords a nivell de servei.pdf"].document_type == (
        TenderDocumentType.ADDITIONAL
    )
    attachment = by_name["Volumetria_voz_Ajuntament_Pineda_de_Mar.xlsx"]
    assert attachment.source_document_type_code == "resposta"
    # Els mateixos bytes a l'original i a l'esmena: data de la primera versio que els porta.
    assert by_name["Memoria.pdf"].published_at.isoformat() == "2026-09-14"
    assert by_name["Resolucio_ampliacio_termini.pdf"].published_at.isoformat() == "2026-10-13"

    # Nomes els 9 fitxers del portal es baixen (el feed ja te els seus), i tots amb l'MD5 bo.
    assert len(fake.downloads()) == 9
    assert len(result.downloads) == 9
    assert {item["download_status"] for item in result.downloads} == {"downloaded"}

    qa = by_name["Preguntes i respostes (PSCP)"]
    assert qa.url == f"pscp-portal://{PINEDA}/preguntes-respostes"
    assert qa.document_type == TenderDocumentType.ADDITIONAL
    qa_file = await current_file_for(db, qa.id)
    assert qa_file.content.mime_type == "text/plain" and qa_file.version_number == 1
    extraction = await current_extraction_for(db, qa_file.content_id)
    assert extraction.page_count == 1
    amendment = next(d for d in portal if d.source_document_type_code == "esmena")
    assert amendment.document_type == TenderDocumentType.NOTICE
    assert amendment.name.startswith("Esmena de la publicació (PSCP): Ampliació termini")

    assert await triggers(db, tender) == [PORTAL_SYNC_TRIGGER, "document_processing"]
    runs = (
        await db.scalars(
            sa.select(GlobalDocumentClassificationRun).where(
                GlobalDocumentClassificationRun.tender_id == tender.id
            )
        )
    ).all()
    assert len(runs) == 1
    # Les respostes oficials es llegeixen amb el plec: rol `clarification`.
    roles = await effective_document_roles(db, tender.id, None)
    assert roles[qa.id].role == DocumentRole.CLARIFICATION


async def test_a_second_sync_without_changes_does_nothing(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    first = await sync(session_factory, tender, fake)
    files_before = await db.scalar(sa.select(sa.func.count(TenderDocumentFile.id)))
    downloads_before = len(fake.downloads())

    second = await sync(session_factory, tender, fake)

    assert second.changed is False
    assert second.state.fingerprint == first.state.fingerprint
    assert not any(second.reconciled.counts()[key] for key in ("created", "updated", "removed"))
    assert second.reconciled.generated_versions == []
    assert all(item["reused"] for item in second.downloads)
    assert len(fake.downloads()) == downloads_before
    assert await db.scalar(sa.select(sa.func.count(TenderDocumentFile.id))) == files_before
    assert await triggers(db, tender) == [PORTAL_SYNC_TRIGGER, "document_processing"]


async def test_documents_the_portal_withdraws_are_marked_and_come_back(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    await sync(session_factory, tender, fake)
    others = fake.publication(PINEDA, PINEDA_CURRENT)["dades"]["publicacio"]["dadesPublicacio"][
        "altresDocuments"
    ]["docs"]
    annex = next(item for item in others if item["id"] == ANNEX_III)
    others.remove(annex)

    withdrawn = await sync(session_factory, tender, fake)

    assert [d.external_id for d in withdrawn.reconciled.removed] == [str(ANNEX_III)]
    documents = await documents_of(db, tender)
    gone = next(d for d in documents if d.external_id == str(ANNEX_III))
    assert gone.removed_at is not None
    assert await current_file_for(db, gone.id) is not None  # mai s'esborra res

    others.append(annex)
    back = await sync(session_factory, tender, fake)

    assert [d.external_id for d in back.reconciled.restored] == [str(ANNEX_III)]
    documents = await documents_of(db, tender)
    assert next(d for d in documents if d.external_id == str(ANNEX_III)).removed_at is None


# --------------------------------------------------------------------------- #
# Lectures incompletes: no es toca res                                         #
# --------------------------------------------------------------------------- #


async def test_a_portal_that_fails_midway_changes_nothing(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.status["detall-avis/resposta/300908253"] = [503]

    with pytest.raises(PortalFetchError):
        await sync(session_factory, tender, fake)

    assert [d.origin for d in await documents_of(db, tender)] == [TenderDocumentOrigin.FEED] * 2
    assert fake.downloads() == []
    assert await triggers(db, tender) == []


async def test_a_list_that_does_not_match_the_board_retires_nothing(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    await sync(session_factory, tender, fake)
    fake.json["answers:0"]["content"].pop()

    with pytest.raises(PortalParseError, match="comptador"):
        await sync(session_factory, tender, fake)

    assert all(d.removed_at is None for d in await documents_of(db, tender))


# --------------------------------------------------------------------------- #
# MD5                                                                          #
# --------------------------------------------------------------------------- #


async def test_bytes_that_are_not_the_published_ones_are_retried_later(db, session_factory):
    """Uns bytes que no son els publicats (pagina de manteniment, memoria cau antiga) son
    una fallada de la descarrega: es torna a provar, pero no a cada sincronitzacio."""
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    good = fake.files[DECRET]
    fake.files[DECRET] = b"%PDF-1.4 uns altres bytes que no son els publicats"

    result = await sync(session_factory, tender, fake)

    assert result.any_failed is True and result.partial is True
    documents = await documents_of(db, tender)
    decret = next(d for d in documents if d.name == "Decret.pdf")
    file = await current_file_for(db, decret.id)
    assert file.download_status == DownloadStatus.FAILED
    assert file.error_code == CHECKSUM_MISMATCH
    assert file.content_id is None
    assert result.missing_content == [str(decret.id)]

    soon = await sync(session_factory, tender, fake)

    assert soon.skipped == [
        {"document_id": str(decret.id), "reason": "recent", "error_code": CHECKSUM_MISMATCH}
    ]
    assert soon.partial is True
    assert len(fake.downloads_of(DECRET)) == 1

    # Al cap d'una estona, amb el portal ja bé, es torna a baixar i ara hi entra.
    fake.files[DECRET] = good
    file.started_at = datetime.now(UTC) - timedelta(hours=2)
    db.add(file)
    await db.commit()
    later = await sync(session_factory, tender, fake)

    assert later.partial is False
    assert len(fake.downloads_of(DECRET)) == 2
    assert (await current_file_for(db, decret.id)).content_id is not None


# --------------------------------------------------------------------------- #
# Documents generats                                                           #
# --------------------------------------------------------------------------- #


async def test_a_new_answer_is_a_new_version_of_the_same_document(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    await sync(session_factory, tender, fake)
    qa = next(d for d in await documents_of(db, tender) if d.name == "Preguntes i respostes (PSCP)")
    first = await current_file_for(db, qa.id)

    listed = fake.json["answers:0"]
    item = dict(listed["content"][0], id=300999001, dataPublicacio="2026-10-20T08:00:00.000Z")
    listed["content"].append(item)
    listed["totalElements"] += 1
    fake.json["board"]["comptadorPreguntesRespostes"] += 1
    detail = dict(fake.json["detall-avis/resposta/300908253"])
    detail["dades"] = dict(detail["dades"], titol="Pregunta nova?", descripcio="Resposta nova.")
    detail["dataPublicacio"] = item["dataPublicacio"]
    fake.json["detall-avis/resposta/300999001"] = detail

    result = await sync(session_factory, tender, fake)

    assert [d.id for d in result.reconciled.generated_versions] == [qa.id]
    second = await current_file_for(db, qa.id)
    assert second.version_number == 2 and second.id != first.id
    await db.refresh(first)
    assert first.is_current is False
    extraction = await current_extraction_for(db, second.content_id)
    assert extraction is not None


async def test_cornella_notices_become_notice_documents(db, session_factory):
    fake = cornella()
    tender = await public_tender(db, CORNELLA, CORNELLA_CURRENT)

    result = await sync(session_factory, tender, fake)

    assert result.state.notices == 3 and result.state.amendments == 1
    # Les 9 respostes reals, 6 amb un PDF adjunt que es baixa i es llegeix amb el plec.
    assert (result.state.answers, result.state.answers_before) == (9, 0)
    attachments = [
        d for d in await documents_of(db, tender) if d.source_document_type_code == "resposta"
    ]
    assert len(attachments) == 6
    assert all(d.document_type == TenderDocumentType.ADDITIONAL for d in attachments)
    assert result.partial is False
    notices = [
        d
        for d in await documents_of(db, tender)
        if (d.source_document_type_code or "").startswith("avis:")
    ]
    assert sorted(d.source_document_type_code for d in notices) == [
        "avis:avisos",
        "avis:esmenes",
        "avis:mesa",
    ]
    assert all(d.document_type == TenderDocumentType.NOTICE for d in notices)
    assert all(d.name.startswith("Avís del tauler (PSCP): ") for d in notices)
    assert not [call for call in fake.calls if call.startswith("avisos-tauler/")]


async def test_a_category_without_a_direct_link_is_read_from_its_list(db, session_factory):
    fake = cornella()
    fake.json["board"]["urlAvis"] = None
    tender = await public_tender(db, CORNELLA, CORNELLA_CURRENT)

    result = await sync(session_factory, tender, fake)

    assert result.state.notices == 3
    assert f"avisos-tauler/1008176/{CORNELLA}" in fake.calls


async def test_parc_tauli_reads_only_the_licitacio_phase(db, session_factory):
    """6 de les 10 respostes del tauler son de febrer i marc (la consulta preliminar del
    mercat), set mesos abans de l'anunci del 06-10: no son aclariments de la licitacio."""
    fake = parc_tauli()
    tender = await public_tender(db, PARC_TAULI, PARC_TAULI_CURRENT)

    result = await sync(session_factory, tender, fake)

    assert (result.state.answers, result.state.amendments) == (4, 0)
    assert result.state.answers_before == 6
    qa = next(d for d in await documents_of(db, tender) if d.name == "Preguntes i respostes (PSCP)")
    text = await current_extraction_for(db, (await current_file_for(db, qa.id)).content_id)
    assert text is not None
    documents = await documents_of(db, tender)
    assert len(documents) == 6  # 5 fitxers de la publicacio i les preguntes i respostes
    assert not [call for call in fake.calls if call.endswith("/300789814")]
    assert len(fake.downloads()) == 5


# --------------------------------------------------------------------------- #
# Duplicats: feed > portal > operador                                          #
# --------------------------------------------------------------------------- #


async def test_the_same_bytes_as_a_feed_document_make_the_portal_copy_an_alias(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    feed_copy = await add_document(
        db, tender, "https://contrataciondelestado.es/doc/decret.pdf", name="Decret (feed).pdf"
    )
    await store_current(db, feed_copy, fake.files[DECRET])

    await sync(session_factory, tender, fake)

    documents = await documents_of(db, tender)
    portal_decret = next(d for d in documents if d.name == "Decret.pdf")
    assert portal_decret.origin == TenderDocumentOrigin.PORTAL
    assert portal_decret.duplicate_of_document_id == feed_copy.id
    assert next(d for d in documents if d.id == feed_copy.id).duplicate_of_document_id is None


async def test_an_operator_url_with_the_published_bytes_is_handed_over(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    verified = await operator_url(db, tender, fake.url(MEMORIA), data=fake.files[MEMORIA])
    pending_without_bytes = await operator_url(
        db, tender, fake.url(DECRET), status=DocumentContributionStatus.PENDING_VERIFICATION
    )
    other_bytes = await operator_url(
        db,
        tender,
        fake.url(ANNEX_III),
        status=DocumentContributionStatus.PENDING_VERIFICATION,
        data=b"%PDF-1.4 no son els bytes publicats",
    )
    rejected = await operator_url(
        db,
        tender,
        fake.url(ANNEX_I),
        status=DocumentContributionStatus.REJECTED,
    )

    result = await sync(session_factory, tender, fake)

    assert sorted(d.id for d in result.reconciled.taken_over) == sorted(
        [verified.id, pending_without_bytes.id]
    )
    assert sorted(d.id for d in result.reconciled.not_governed) == sorted(
        [other_bytes.id, rejected.id]
    )
    documents = {d.id: d for d in await documents_of(db, tender)}
    handed = documents[verified.id]
    assert handed.origin == TenderDocumentOrigin.PORTAL
    assert handed.governing_source_id == await portal_source_id(db)
    assert handed.contribution_status == DocumentContributionStatus.NOT_APPLICABLE
    assert handed.verified_at is None and handed.verified_by is None
    assert handed.verification_note.startswith("Cedit al portal de la PSCP")
    assert handed.contributed_at is not None  # la provinenca de l'aportacio es queda
    assert documents[rejected.id].origin == TenderDocumentOrigin.OPERATOR_URL
    assert documents[other_bytes.id].origin == TenderDocumentOrigin.OPERATOR_URL
    # El verificat ja tenia els bytes bons: no es torna a baixar. El pendent sense bytes, si.
    assert fake.downloads_of(MEMORIA) == []
    assert len(fake.downloads_of(DECRET)) == 1
    audits = (
        await db.scalars(
            sa.select(AuditLog).where(
                AuditLog.entity_id.in_([verified.id, pending_without_bytes.id])
            )
        )
    ).all()
    assert len(audits) == 2
    assert all(entry.user_id is None and entry.company_id is None for entry in audits)
    assert all(entry.summary.startswith("cedit al portal; md5=") for entry in audits)


# --------------------------------------------------------------------------- #
# Abast                                                                        #
# --------------------------------------------------------------------------- #


async def test_a_private_tender_is_not_synced(db, session_factory, tenant_a):
    _, company = tenant_a
    tender = await public_tender(db, PINEDA, PINEDA_CURRENT, owner_company_id=company.id)
    with pytest.raises(PortalSyncNotAvailableError) as raised:
        await sync(session_factory, tender, pineda())
    assert raised.value.reason == "private_tender"


async def test_a_tender_without_a_pscp_publication_is_not_synced(db, session_factory):
    tender = await public_tender(db, PINEDA, PINEDA_CURRENT)
    tender.source_metadata = {}
    await db.commit()
    with pytest.raises(PortalSyncNotAvailableError) as raised:
        await sync(session_factory, tender, pineda())
    assert raised.value.reason == "no_pscp_publication"


async def test_the_switch_off_stops_before_any_request(db, session_factory, monkeypatch):
    monkeypatch.setattr(settings, "PSCP_PORTAL_ENABLED", False)
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    with pytest.raises(PortalDisabledError):
        await sync(session_factory, tender, fake)
    assert fake.calls == []


async def test_an_operator_url_whose_bytes_cannot_be_read_is_not_handed_over(
    db, session_factory, storage
):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    document = await operator_url(db, tender, fake.url(MEMORIA), data=fake.files[MEMORIA])
    current = await current_file_for(db, document.id)
    await storage.delete(current.content.storage_key)

    result = await sync(session_factory, tender, fake)

    assert [d.id for d in result.reconciled.not_governed] == [document.id]
    assert result.reconciled.taken_over == []
    refreshed = next(d for d in await documents_of(db, tender) if d.id == document.id)
    assert refreshed.origin == TenderDocumentOrigin.OPERATOR_URL


# --------------------------------------------------------------------------- #
# Esmenes i govern entre fonts                                                 #
# --------------------------------------------------------------------------- #


def _original_as_current(fake) -> dict:
    """Pineda abans de l'esmena: la versio original es la vigent (torna la vigent real)."""
    original = fake.publication(PINEDA, PINEDA_ORIGINAL)
    saved = copy.deepcopy(original)
    original["navegacioFases"][0]["publicacioId"] = PINEDA_ORIGINAL
    original["navegacioEsmenes"] = original["navegacioEsmenes"][:1]
    return saved


async def test_an_amendment_with_new_ids_moves_the_documents(db, session_factory):
    """L'esmena de Pineda torna a publicar els 10 fitxers amb identificadors nous i els
    mateixos bytes: son els mateixos documents (es mouen), no se'n retira ni se'n baixa cap."""
    fake = pineda()
    saved = _original_as_current(fake)
    tender = await public_tender(db, PINEDA, PINEDA_ORIGINAL)
    await sync(session_factory, tender, fake)
    before = {d.name: d.id for d in await documents_of(db, tender)}
    downloads = len(fake.downloads())

    fake.json[f"detall-publicacio-expedient/{PINEDA}/{PINEDA_ORIGINAL}"] = saved
    result = await sync(session_factory, tender, fake)

    counts = result.reconciled.counts()
    assert counts["repointed"] == 9 and counts["removed"] == 0
    assert sorted(d.name for d in result.reconciled.created) == [
        "Esmena de la publicació (PSCP): Ampliació termini presentació ofertes.",
        "Resolucio_ampliacio_termini.pdf",
    ]
    after = {d.name: d for d in await documents_of(db, tender)}
    assert after["PCAP segellat.pdf"].id == before["PCAP segellat.pdf"]
    assert after["PCAP segellat.pdf"].url == fake.url(PCAP)
    assert after["PCAP segellat.pdf"].removed_at is None
    # Nomes es baixa el fitxer nou.
    assert len(fake.downloads()) == downloads + 1
    codes = (
        await db.scalars(
            sa.select(TenderChangeEvent.change_code)
            .join(TenderRevision, TenderRevision.id == TenderChangeEvent.to_revision_id)
            .where(
                TenderRevision.tender_id == tender.id,
                TenderRevision.trigger == PORTAL_SYNC_TRIGGER,
                TenderRevision.revision_number > 2,
            )
        )
    ).all()
    assert codes.count("DOCUMENT_URL_CHANGED") == 9
    assert "DOCUMENT_REMOVED" not in codes


async def test_a_url_the_feed_stopped_publishing_passes_to_the_portal(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    retired = await add_document(
        db, tender, fake.url(MEMORIA), name="Memoria (feed).pdf", removed_at=datetime.now(UTC)
    )

    result = await sync(session_factory, tender, fake)

    assert [d.id for d in result.reconciled.adopted_from_feed] == [retired.id]
    document = next(d for d in await documents_of(db, tender) if d.id == retired.id)
    assert document.origin == TenderDocumentOrigin.PORTAL
    assert document.governing_source_id == await portal_source_id(db)
    assert document.removed_at is None and document.name == "Memoria.pdf"
    audit = await db.scalar(sa.select(AuditLog).where(AuditLog.entity_id == retired.id))
    assert audit.user_id is None and audit.summary.startswith("cedit al portal: el feed")


async def test_content_the_feed_already_has_under_another_url_is_not_duplicated(
    db, session_factory
):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    md5 = hashlib.md5(fake.files[MEMORIA]).hexdigest()
    feed_copy = await add_document(db, tender, download_url(999000111, md5), name="Memoria.pdf")

    result = await sync(session_factory, tender, fake)

    assert feed_copy.id in {d.id for d in result.reconciled.feed_owned}
    portal_names = {
        d.name for d in await documents_of(db, tender) if d.origin == TenderDocumentOrigin.PORTAL
    }
    assert "Memoria.pdf" not in portal_names
    assert fake.downloads_of(MEMORIA) == []


async def test_an_empty_publication_retires_nothing(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    await sync(session_factory, tender, fake)
    groups = fake.publication(PINEDA, PINEDA_CURRENT)["dades"]["publicacio"]["dadesPublicacio"]
    for group in groups.values():
        if isinstance(group, dict) and "docs" in group:
            group["docs"] = []

    with pytest.raises(PortalParseError, match="sospitosa"):
        await sync(session_factory, tender, fake)

    assert all(d.removed_at is None for d in await documents_of(db, tender))


async def test_without_a_licitacio_phase_the_tender_is_not_available(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.publication(PINEDA, PINEDA_CURRENT)["navegacioFases"][0]["fase"] = 1000038

    with pytest.raises(PortalSyncNotAvailableError) as raised:
        await sync(session_factory, tender, fake)

    assert raised.value.reason == "no_licitacio_phase"


async def test_the_entry_publication_can_be_of_another_phase(db, session_factory):
    """La fitxa pot apuntar a la publicacio d'una altra fase (sense documents): nomes se'n
    llegeix la navegacio per arribar a l'anunci de licitacio."""
    fake = parc_tauli()
    current = fake.publication(PARC_TAULI, PARC_TAULI_CURRENT)
    fake.json[f"detall-publicacio-expedient/{PARC_TAULI}/300789814"] = {
        "expedientId": PARC_TAULI,
        "publicacioId": 300789814,
        "fase": 1000038,
        "navegacioFases": current["navegacioFases"],
        "navegacioEsmenes": [],
        "dades": {},
    }
    tender = await public_tender(db, PARC_TAULI, 300789814)

    result = await sync(session_factory, tender, fake)

    assert result.state.publication.publication_id == PARC_TAULI_CURRENT
    assert len(fake.downloads()) == 5


async def test_the_order_of_the_answers_in_the_api_changes_nothing(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    first = await sync(session_factory, tender, fake)
    fake.json["answers:0"]["content"].reverse()

    again = await sync(session_factory, tender, fake)

    assert again.state.fingerprint == first.state.fingerprint
    assert again.changed is False


async def test_a_list_that_changes_while_it_is_read_is_read_again(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    short = copy.deepcopy(fake.json["answers:0"])
    short["content"].pop()
    fake.next_json["answers:0"] = [short]

    result = await sync(session_factory, tender, fake)

    assert result.state.answers == 5
    assert fake.calls.count(f"respostes/{PINEDA}") == 2


# --------------------------------------------------------------------------- #
# Fitxers que no es poden desar                                                #
# --------------------------------------------------------------------------- #


async def test_an_unsupported_published_file_is_partial_and_not_retried(db, session_factory):
    """Els bytes son els publicats (l'MD5 quadra) pero no son d'un tipus que es pugui desar."""
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.replace_file(DECRET, b"Rar!\x1a\x07\x00 dades", "application/x-rar")

    first = await sync(session_factory, tender, fake)
    again = await sync(session_factory, tender, fake)

    assert first.partial is True and again.partial is True
    decret = next(d for d in await documents_of(db, tender) if d.name == "Decret.pdf")
    assert again.skipped == [
        {"document_id": str(decret.id), "reason": "permanent", "error_code": "invalid_content"}
    ]
    assert len(fake.downloads_of(DECRET)) == 1


async def test_an_image_attached_to_an_answer_is_stored(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.replace_file(302678096, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")

    result = await sync(session_factory, tender, fake)

    assert result.partial is False
    image = next(
        d for d in await documents_of(db, tender) if d.source_document_type_code == "resposta"
    )
    file = await current_file_for(db, image.id)
    assert file.content.mime_type == "image/png"
    extraction = await current_extraction_for(db, file.content_id)
    assert extraction.status == ExtractionStatus.SKIPPED_UNSUPPORTED


# --------------------------------------------------------------------------- #
# Concurrencia                                                                 #
# --------------------------------------------------------------------------- #


async def test_the_final_revision_sees_what_changed_during_the_downloads(
    db, session_factory, monkeypatch
):
    """La revision final es fa en una sessio nova: un canvi fet mentre es baixava (p. ex.
    el feed amplia el termini) no es desfa."""
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    new_deadline = datetime(2026, 11, 11, 22, 59, tzinfo=UTC)
    original_wait = PortalClient.wait_turn
    changed = {"done": False}

    async def wait_turn(self):
        if not changed["done"]:
            changed["done"] = True
            async with session_factory() as other:
                row = await other.get(Tender, tender.id)
                row.deadline_at = new_deadline
                await other.commit()
        await original_wait(self)

    monkeypatch.setattr(PortalClient, "wait_turn", wait_turn)

    await sync(session_factory, tender, fake)

    last = await db.scalar(
        sa.select(TenderRevision)
        .where(TenderRevision.tender_id == tender.id)
        .order_by(TenderRevision.revision_number.desc())
        .limit(1)
    )
    assert changed["done"] is True
    assert last.normalized_snapshot["deadline_at"].startswith("2026-11-11T22:59")


async def test_a_takeover_does_not_override_a_decision_taken_meanwhile(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    document = await operator_url(db, tender, fake.url(MEMORIA), data=fake.files[MEMORIA])
    source = await portal_source(db)
    # L'operador el rebutja des d'una altra sessio: l'objecte que te el sync no ho sap.
    await db.execute(
        sa.update(TenderDocument)
        .where(TenderDocument.id == document.id)
        .values(
            contribution_status=DocumentContributionStatus.REJECTED,
            verified_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    wanted = type("Wanted", (), {"md5": hashlib.md5(fake.files[MEMORIA]).hexdigest().upper()})

    stored = StoredBytes(wanted.md5, (await current_file_for(db, document.id)).content_id)
    taken = await _take_over(db, document, source, wanted, stored, datetime.now(UTC))

    assert taken is False
    assert document.origin == TenderDocumentOrigin.OPERATOR_URL
    assert document.contribution_status == DocumentContributionStatus.REJECTED


async def test_an_operator_url_written_otherwise_is_handed_over_and_kept(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    written = fake.url(MEMORIA).replace("://", "://www.").lower()
    document = await operator_url(db, tender, written, data=fake.files[MEMORIA])

    first = await sync(session_factory, tender, fake)
    again = await sync(session_factory, tender, fake)

    assert [d.id for d in first.reconciled.taken_over] == [document.id]
    assert again.reconciled.removed == []
    kept = next(d for d in await documents_of(db, tender) if d.id == document.id)
    assert kept.origin == TenderDocumentOrigin.PORTAL and kept.removed_at is None
    assert "document_intake" in await triggers(db, tender)


# --------------------------------------------------------------------------- #
# Re-revisio                                                                   #
# --------------------------------------------------------------------------- #


def _empty_groups(fake) -> None:
    groups = fake.publication(PINEDA, PINEDA_CURRENT)["dades"]["publicacio"]["dadesPublicacio"]
    for group in groups.values():
        if isinstance(group, dict) and "docs" in group:
            group["docs"] = []


async def test_a_publication_without_files_but_with_answer_attachments_keeps_syncing(
    db, session_factory
):
    """Els adjunts de respostes no son fitxers de la publicacio: no fan saltar la guarda."""
    fake = pineda()
    _empty_groups(fake)
    tender = await public_tender(db, PINEDA, PINEDA_CURRENT)
    first = await sync(session_factory, tender, fake)
    assert first.state.answers == 5

    again = await sync(session_factory, tender, fake)

    assert again.changed is False


async def test_an_amended_version_without_its_navigation_is_refused(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.publication(PINEDA, PINEDA_CURRENT)["navegacioEsmenes"] = []

    with pytest.raises(PortalParseError, match="esmenada"):
        await sync(session_factory, tender, fake)


async def test_a_board_announcing_too_much_is_refused(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.json["board"]["comptadorPreguntesRespostes"] = 5000

    with pytest.raises(PortalParseError, match="tauler"):
        await sync(session_factory, tender, fake)

    assert [d.origin for d in await documents_of(db, tender)] == [TenderDocumentOrigin.FEED] * 2


async def test_bytes_that_arrived_meanwhile_are_not_handed_over_unchecked(db, session_factory):
    """La cessio es decideix amb els bytes llegits abans d'escriure: si n'arriben uns altres
    entremig, no es cedeixen sense que se'n comprovi l'MD5."""
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    document = await operator_url(
        db, tender, fake.url(MEMORIA), status=DocumentContributionStatus.PENDING_VERIFICATION
    )
    seen_before = StoredBytes(None, None)
    await store_current(db, document, b"%PDF-1.4 uns bytes arribats mentre es reconciliava")
    source = await portal_source(db)
    wanted = type("Wanted", (), {"md5": hashlib.md5(fake.files[MEMORIA]).hexdigest().upper()})

    taken = await _take_over(db, document, source, wanted, seen_before, datetime.now(UTC))

    assert taken is False
    assert document.origin == TenderDocumentOrigin.OPERATOR_URL


async def test_an_amendment_that_moves_a_file_without_bytes_is_only_an_address_change(
    db, session_factory
):
    """Un fitxer publicat que no es pot desar (MD5 bo, tipus no admes) i que l'esmena torna a
    publicar amb un identificador nou: es un canvi d'adreca, no una retirada i una alta."""
    fake = pineda()
    saved = _original_as_current(fake)
    rar = b"Rar!\x1a\x07\x00 dades"
    original_decret = next(
        item["id"]
        for item in fake.publication(PINEDA, PINEDA_ORIGINAL)["dades"]["publicacio"][
            "dadesPublicacio"
        ]["documentsAprovacio"]["docs"]
    )
    fake.replace_file(original_decret, rar, "application/x-rar")
    fake.replace_file(DECRET, rar, "application/x-rar")
    tender = await public_tender(db, PINEDA, PINEDA_ORIGINAL)
    await sync(session_factory, tender, fake)

    fake.json[f"detall-publicacio-expedient/{PINEDA}/{PINEDA_ORIGINAL}"] = saved
    fake.replace_file(DECRET, rar, "application/x-rar")
    result = await sync(session_factory, tender, fake)

    assert "Decret.pdf" in {d.name for d in result.reconciled.repointed}
    codes = (
        await db.scalars(
            sa.select(TenderChangeEvent.change_code)
            .join(TenderRevision, TenderRevision.id == TenderChangeEvent.to_revision_id)
            .where(
                TenderRevision.tender_id == tender.id,
                TenderRevision.trigger == PORTAL_SYNC_TRIGGER,
                TenderRevision.revision_number > 2,
            )
        )
    ).all()
    assert "DOCUMENT_REMOVED" not in codes
    assert codes.count("DOCUMENT_URL_CHANGED") == 9


async def test_a_permanent_failure_is_tried_again_after_a_week(db, session_factory):
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    fake.replace_file(DECRET, b"Rar!\x1a\x07\x00 dades", "application/x-rar")
    await sync(session_factory, tender, fake)
    decret = next(d for d in await documents_of(db, tender) if d.name == "Decret.pdf")
    failed = await current_file_for(db, decret.id)

    assert permanently_failed(decret, failed) is True
    assert permanently_failed(decret, failed, datetime.now(UTC) + timedelta(days=8)) is False


async def test_an_interrupted_sync_still_closes_what_it_saved(db, session_factory):
    """Si el worker perd el lease a mitja descarrega, el que ja es a la base es tanca igual."""
    fake = pineda()
    tender = await pineda_with_feed(db, fake)
    calls = {"n": 0}

    async def keep_going() -> bool:
        calls["n"] += 1
        return calls["n"] < 3

    async with fake.client() as http, PortalClient(client=http) as portal:
        result = await sync_tender(
            session_factory, tender.id, portal=portal, resolver=resolver, should_continue=keep_going
        )

    assert result is None
    assert await triggers(db, tender) == [PORTAL_SYNC_TRIGGER, "document_processing"]
    runs = await db.scalar(
        sa.select(sa.func.count(GlobalDocumentClassificationRun.id)).where(
            GlobalDocumentClassificationRun.tender_id == tender.id
        )
    )
    assert runs == 1


async def test_the_closing_revision_is_retried_once_after_a_conflict(
    db, session_factory, monkeypatch
):
    from sqlalchemy.exc import IntegrityError

    from app.ingestion.pscp_portal import service as portal_service

    real = portal_service.record_revision
    raised = {"done": False}

    async def flaky(session, tender, **kwargs):
        if kwargs.get("trigger") == "document_processing" and not raised["done"]:
            raised["done"] = True
            raise IntegrityError("INSERT", {}, Exception("revision_number"))
        return await real(session, tender, **kwargs)

    monkeypatch.setattr(portal_service, "record_revision", flaky)
    fake = pineda()
    tender = await pineda_with_feed(db, fake)

    await sync(session_factory, tender, fake)

    assert raised["done"] is True
    assert await triggers(db, tender) == [PORTAL_SYNC_TRIGGER, "document_processing"]
