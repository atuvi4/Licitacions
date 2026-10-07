"""Base de l'analisi = ultima revisio `analysis_affecting` (spec 3B-1 §4.5).

Un canvi de titol, de CPV o una cancel·lacio no han de caducar l'analisi; un
canvi d'import, de termini o de documents, si.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.cpv import CPVCode
from app.models.enums import (
    DocumentFileOrigin,
    DownloadStatus,
    SourceType,
    TenderDocumentType,
    TenderStatus,
)
from app.models.sources import Source
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tenders import Tender, TenderCPV, TenderDocument
from app.services import tender_analysis as analysis
from app.services import tender_lifecycle as lifecycle
from tests.fixtures.company_twin import build_company_twin
from tests.test_tender_gold_lescala import CASE

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _first_document(db, tender_id) -> TenderDocument:
    return (
        await db.scalars(
            sa.select(TenderDocument)
            .where(TenderDocument.tender_id == tender_id)
            .order_by(TenderDocument.url)
        )
    ).first()


async def apply_change(db, tender: Tender, change: str) -> None:
    """Modifica el tender com ho faria la ingestio i en registra la revisio."""
    if change == "title":
        tender.title = "Un altre titol"
    elif change == "cpv_only":
        db.add(CPVCode(code="90610000", description="Neteja de carrers"))
        await db.flush()
        db.add(TenderCPV(tender_id=tender.id, cpv_code="90610000", is_main=True))
    elif change == "amount":
        tender.estimated_value = Decimal("123456.00")
    elif change == "deadline":
        tender.deadline_at = datetime.now(UTC) + timedelta(days=90)
    elif change == "document_added":
        db.add(
            TenderDocument(
                tender_id=tender.id,
                name="Nou",
                document_type=TenderDocumentType.OTHER,
                url="https://x/nou.pdf",
            )
        )
    elif change == "document_removed":
        (await _first_document(db, tender.id)).removed_at = datetime.now(UTC)
    elif change == "document_replaced":
        document = await _first_document(db, tender.id)
        content = TenderDocumentContent(
            sha256="f" * 64,
            storage_key="documents/replaced.pdf",
            file_size=1,
            mime_type="application/pdf",
            is_text_extractable=False,
        )
        db.add(content)
        await db.flush()
        for file in (
            await db.scalars(
                sa.select(TenderDocumentFile).where(
                    TenderDocumentFile.tender_document_id == document.id
                )
            )
        ).all():
            file.is_current = False
        db.add(
            TenderDocumentFile(
                tender_document_id=document.id,
                content_id=content.id,
                origin=DocumentFileOrigin.UPLOADED,
                download_status=DownloadStatus.DOWNLOADED,
                version_number=99,
                is_current=True,
            )
        )
    elif change == "cancelled_only":
        tender.status = TenderStatus.CANCELLED
    else:  # pragma: no cover
        raise AssertionError(change)
    await db.flush()
    outcome = await lifecycle.record_revision(db, tender)
    assert outcome.unchanged is False
    await db.commit()


@pytest.mark.parametrize(
    ("change", "expects_new_basis"),
    [
        ("title", False),
        ("cpv_only", False),
        ("amount", True),
        ("deadline", True),
        ("document_added", True),
        ("document_removed", True),
        ("document_replaced", True),
        ("cancelled_only", False),
    ],
)
async def test_basis_v3_segons_impacte(db, tenant_a, change, expects_new_basis):
    _user, company = tenant_a
    loaded = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    tender = loaded.tender
    await build_company_twin(db, company)
    await lifecycle.record_revision(db, tender)
    await db.commit()

    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    await db.commit()
    basis_before = run.tender_revision_id
    assert basis_before is not None

    await apply_change(db, tender, change)

    assert (await analysis.is_stale(db, run)) is expects_new_basis
    basis_after = await lifecycle.latest_analysis_affecting_revision(db, tender.id)
    assert (basis_after.id != basis_before) is expects_new_basis


async def test_sense_cap_revisio_affecting_retorna_la_primera(db):
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="7/2026",
        title="Servei",
    )
    db.add(tender)
    await db.commit()
    assert await lifecycle.latest_analysis_affecting_revision(db, tender.id) is None

    first = (await lifecycle.record_revision(db, tender)).revision
    for title in ("Titol B", "Titol C", "Titol D"):
        tender.title = title
        await lifecycle.record_revision(db, tender)
        await db.commit()

    latest = await lifecycle.latest_revision(db, tender.id)
    assert latest.revision_number == 4
    basis = await lifecycle.latest_analysis_affecting_revision(db, tender.id)
    assert basis.id == first.id


async def test_una_informativa_despres_d_una_affecting_conserva_la_base_affecting(db):
    """Base = ultima `affecting`, no l'ultima revisio: una informativa posterior no la mou."""
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="8/2026",
        title="Servei",
    )
    db.add(tender)
    await db.commit()
    await lifecycle.record_revision(db, tender)
    await db.commit()

    await apply_change(db, tender, "amount")  # affecting
    affecting = await lifecycle.latest_revision(db, tender.id)
    assert (await lifecycle.latest_analysis_affecting_revision(db, tender.id)).id == affecting.id

    await apply_change(db, tender, "title")  # informativa
    latest = await lifecycle.latest_revision(db, tender.id)
    assert latest.id != affecting.id and latest.revision_number == affecting.revision_number + 1
    basis = await lifecycle.latest_analysis_affecting_revision(db, tender.id)
    assert basis.id == affecting.id
