"""Documents publics (0058): origen, source ownership, verificacio, alies i forats del paquet.

El que es prova aqui es el CONTRACTE del model: els CHECK que imposa la base de
dades i el predicat `ANALYZABLE_DOCUMENT`, que decideix quins documents poden
entrar a l'analisi. Un document aportat per l'operador no es analitzable fins que
una persona el verifica.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    DocumentContributionStatus,
    PackageGapCode,
    PackageGapProducer,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.sources import Source
from app.models.tenders import Tender, TenderDocument
from app.tenant_ops import PUBLIC_TABLES

NOW = datetime(2026, 10, 7, tzinfo=UTC)


async def _tender(session) -> Tender:
    source = Source(code=f"pd-{uuid.uuid4().hex[:8]}", name="Font de prova")
    session.add(source)
    await session.flush()
    tender = Tender(source_id=source.id, external_id=f"exp-{uuid.uuid4().hex[:8]}", title="T")
    session.add(tender)
    await session.flush()
    return tender


def _document(tender: Tender, **kwargs) -> TenderDocument:
    suffix = uuid.uuid4().hex[:8]
    values = {
        "tender_id": tender.id,
        "name": f"doc-{suffix}.pdf",
        "document_type": TenderDocumentType.OTHER,
        "url": f"https://example.com/{suffix}.pdf",
        "origin": TenderDocumentOrigin.FEED,
        "governing_source_id": tender.source_id,
    }
    values.update(kwargs)
    return TenderDocument(**values)


def test_els_valors_dels_enums_son_els_de_l_especificacio():
    assert {m.value for m in TenderDocumentOrigin} == {
        "feed",
        "owner_upload",
        "operator_url",
        "operator_upload",
    }
    assert {m.value for m in DocumentContributionStatus} == {
        "not_applicable",
        "pending_verification",
        "verified",
        "rejected",
    }
    assert {m.value for m in PackageGapCode} == {
        "required_role_missing",
        "referenced_document_missing",
        "no_text",
        "format_not_analyzable",
        "annex_missing",
        "declared_by_operator",
    }
    assert {m.value for m in PackageGapProducer} == {"classification", "operator"}


def test_les_taules_de_forats_son_publiques_i_sense_company_id():
    from app.models import Base

    assert "tender_package_gaps" in PUBLIC_TABLES
    assert "company_id" not in Base.metadata.tables["tender_package_gaps"].c
    assert "tender_document_proposals" not in Base.metadata.tables


async def test_un_document_nou_neix_feed_i_no_aplicable(session):
    tender = await _tender(session)
    document = _document(tender)
    session.add(document)
    await session.commit()
    assert document.origin == TenderDocumentOrigin.FEED
    assert document.contribution_status == DocumentContributionStatus.NOT_APPLICABLE
    assert document.duplicate_of_document_id is None
    assert document.governing_source_id == tender.source_id


async def test_un_feed_sense_font_que_el_governi_ho_omple_el_model(session):
    """Xarxa de seguretat: un feed sense font governant la rep de la licitacio."""
    tender = await _tender(session)
    document = TenderDocument(
        tender_id=tender.id,
        name="a.pdf",
        url="https://example.com/a.pdf",
    )
    session.add(document)
    await session.commit()
    assert document.origin == TenderDocumentOrigin.FEED
    assert document.governing_source_id == tender.source_id


async def _rejected(session, **kwargs) -> None:
    tender = await _tender(session)
    session.add(_document(tender, **kwargs))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_origin_feed_exigeix_una_font_que_el_governi(session):
    tender = await _tender(session)
    document = _document(tender)
    session.add(document)
    await session.commit()
    document.governing_source_id = None
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_un_document_de_loperador_no_te_font_governant(session):
    await _rejected(
        session,
        origin=TenderDocumentOrigin.OPERATOR_URL,
        contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
        # governing_source_id es el del feed: incoherent amb operator_url
    )


async def test_operator_sense_pending_o_verificat_es_rebutja(session):
    # operator_url amb not_applicable
    await _rejected(
        session,
        origin=TenderDocumentOrigin.OPERATOR_URL,
        governing_source_id=None,
        contribution_status=DocumentContributionStatus.NOT_APPLICABLE,
    )


async def test_feed_amb_estat_de_contribucio_es_rebutja(session):
    await _rejected(
        session,
        contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
    )


async def test_verified_at_va_lligat_a_verified(session):
    # verified sense verified_at
    await _rejected(
        session,
        origin=TenderDocumentOrigin.OPERATOR_URL,
        governing_source_id=None,
        contribution_status=DocumentContributionStatus.VERIFIED,
    )
    # verified_at sense verified
    await _rejected(
        session,
        origin=TenderDocumentOrigin.OPERATOR_URL,
        governing_source_id=None,
        contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
        verified_at=NOW,
    )


async def test_un_document_de_loperador_verificat_es_valid(session):
    tender = await _tender(session)
    document = _document(
        tender,
        origin=TenderDocumentOrigin.OPERATOR_UPLOAD,
        governing_source_id=None,
        contribution_status=DocumentContributionStatus.VERIFIED,
        verified_at=NOW,
        contribution_source_url="https://contractaciopublica.cat/x",
    )
    session.add(document)
    await session.commit()
    assert document.governing_source_id is None


async def test_un_owner_upload_no_te_font_governant(session):
    tender = await _tender(session)
    document = _document(tender, origin=TenderDocumentOrigin.OWNER_UPLOAD, governing_source_id=None)
    session.add(document)
    await session.commit()
    assert document.contribution_status == DocumentContributionStatus.NOT_APPLICABLE


async def _ids(session, tender: Tender) -> set[uuid.UUID]:
    from app.tenders.documents import analyzable_documents

    return {d.id for d in await analyzable_documents(session, tender.id)}


async def test_el_predicat_analitzable_exclou_pendents_rebutjats_alies_i_retirats(session):
    from app.tenders.documents import ANALYZABLE_DOCUMENT

    tender = await _tender(session)
    feed = _document(tender)
    owner = _document(tender, origin=TenderDocumentOrigin.OWNER_UPLOAD, governing_source_id=None)
    operator = {
        "origin": TenderDocumentOrigin.OPERATOR_URL,
        "governing_source_id": None,
    }
    verified = _document(
        tender,
        contribution_status=DocumentContributionStatus.VERIFIED,
        verified_at=NOW,
        **operator,
    )
    pending = _document(
        tender, contribution_status=DocumentContributionStatus.PENDING_VERIFICATION, **operator
    )
    rejected = _document(
        tender, contribution_status=DocumentContributionStatus.REJECTED, **operator
    )
    retired_feed = _document(tender, removed_at=NOW)
    retired_verified = _document(
        tender,
        contribution_status=DocumentContributionStatus.VERIFIED,
        verified_at=NOW,
        removed_at=NOW,
        **operator,
    )
    session.add_all([feed, owner, verified, pending, rejected, retired_feed, retired_verified])
    await session.flush()
    alias = _document(tender, duplicate_of_document_id=feed.id)
    session.add(alias)
    await session.commit()

    assert await _ids(session, tender) == {feed.id, owner.id, verified.id}
    # el predicat es una condicio reutilitzable en consultes propies
    rows = await session.scalars(
        sa.select(TenderDocument.id).where(
            TenderDocument.tender_id == tender.id, ANALYZABLE_DOCUMENT
        )
    )
    assert set(rows.all()) == {feed.id, owner.id, verified.id}


async def test_el_forat_del_paquet_es_unic_mentre_no_esta_resolt(session):
    from app.models.package import TenderPackageGap

    tender_id = (await _tender(session)).id

    def gap(**kwargs) -> TenderPackageGap:
        return TenderPackageGap(
            tender_id=tender_id,
            code=PackageGapCode.REFERENCED_DOCUMENT_MISSING,
            detail={"key": "annex-3"},
            producer=PackageGapProducer.CLASSIFICATION,
            **kwargs,
        )

    session.add(gap())
    await session.commit()
    session.add(gap())
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    # un forat resolt no ocupa la unicitat
    resolved = gap(resolved_at=NOW)
    session.add(resolved)
    await session.commit()
    assert resolved.id is not None
    assert resolved.would_provide is None and resolved.producer_run_id is None


async def test_un_document_d_operador_explicit_no_es_toca(session):
    tender = await _tender(session)
    document = TenderDocument(
        tender_id=tender.id,
        name="o.pdf",
        url="https://contractaciopublica.cat/o.pdf",
        origin=TenderDocumentOrigin.OPERATOR_URL,
        contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
    )
    session.add(document)
    await session.commit()
    assert document.governing_source_id is None
    assert document.origin == TenderDocumentOrigin.OPERATOR_URL
    assert document.contribution_status == DocumentContributionStatus.PENDING_VERIFICATION


async def test_operator_upload_sense_origin_es_refusa(session):
    tender = await _tender(session)
    session.add(TenderDocument(tender_id=tender.id, name="o.pdf", url="operator-upload://x"))
    with pytest.raises(ValueError, match="origin obligatori"):
        await session.flush()
    await session.rollback()


async def test_en_una_licitacio_manual_sense_origin_es_refusa(session):
    from app.models.enums import SourceType

    source = Source(
        code=f"man-{uuid.uuid4().hex[:6]}", name="Manual", source_type=SourceType.MANUAL
    )
    session.add(source)
    await session.flush()
    tender = Tender(source_id=source.id, external_id="m1", title="T")
    session.add(tender)
    await session.flush()
    session.add(TenderDocument(tender_id=tender.id, name="a.pdf", url="https://example.com/a.pdf"))
    with pytest.raises(ValueError, match="origin obligatori"):
        await session.flush()
    await session.rollback()


async def test_una_pujada_propia_sense_origin_es_owner_upload_en_manual(session):
    from app.models.enums import SourceType

    source = Source(
        code=f"man-{uuid.uuid4().hex[:6]}", name="Manual", source_type=SourceType.MANUAL
    )
    session.add(source)
    await session.flush()
    tender = Tender(source_id=source.id, external_id="m2", title="T")
    session.add(tender)
    await session.flush()
    document = TenderDocument(tender_id=tender.id, name="a.pdf", url="upload://abc")
    session.add(document)
    await session.commit()
    assert document.origin == TenderDocumentOrigin.OWNER_UPLOAD
    assert document.governing_source_id is None


async def test_sense_origin_en_una_font_de_sindicacio_es_feed_amb_font_governant(session):
    from app.models.enums import SourceType

    source = Source(
        code=f"syn-{uuid.uuid4().hex[:6]}", name="Feed", source_type=SourceType.SYNDICATION_ATOM
    )
    session.add(source)
    await session.flush()
    tender = Tender(source_id=source.id, external_id="s1", title="T")
    session.add(tender)
    await session.flush()
    document = TenderDocument(tender_id=tender.id, name="a.pdf", url="https://example.com/a.pdf")
    session.add(document)
    await session.commit()
    assert document.origin == TenderDocumentOrigin.FEED
    assert document.governing_source_id == tender.source_id
    assert document.contribution_status == DocumentContributionStatus.NOT_APPLICABLE
