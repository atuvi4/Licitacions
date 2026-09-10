"""Materialitza el corpus de desenvolupament com un document d'una licitacio.

Nomes per a desenvolupament. Serveix per poder recorrer el flux sencer amb el
proveidor de simulacio, que **nomes sap respondre sobre aquest corpus**: sense
un document que el contingui, la simulacio es nega a respondre (i fa be).

Escriu les pagines directament, sense passar per la descarrega ni per
l'extractor de PDF: no hi ha res a descarregar, el text ja el tenim. Perque
aixo es vegi i ningu ho confongui amb un plec baixat de veritat, l'extraccio
queda marcada amb `parser_version = "fixture-corpus-v1"`.

    python -m scripts.seed_fixture_plec <tender_id>
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import uuid
from pathlib import Path

import sqlalchemy as sa

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:  # pragma: no cover - conveniencia d'execucio
    sys.path.insert(0, str(API_ROOT))

from app.ai.providers.fixture_corpus import CORPUS_ID, PAGES  # noqa: E402
from app.db.session import SessionFactory  # noqa: E402
from app.models.enums import (  # noqa: E402
    DownloadStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
    TenderDocumentType,
)
from app.models.tender_documents import (  # noqa: E402
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import TenderDocument  # noqa: E402

PARSER_VERSION = "fixture-corpus-v1"
DOCUMENT_NAME = "Plec de clausules administratives (corpus de desenvolupament)"


async def seed(tender_id: uuid.UUID) -> None:
    text = "\n".join(PAGES)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    async with SessionFactory() as session:
        existing = await session.scalar(
            sa.select(TenderDocument).where(
                TenderDocument.tender_id == tender_id,
                TenderDocument.name == DOCUMENT_NAME,
            )
        )
        if existing is not None:
            print(f"Ja hi era: {existing.id}")
            return

        document = TenderDocument(
            tender_id=tender_id,
            url=f"fixture://{CORPUS_ID}",
            name=DOCUMENT_NAME,
            document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
        )
        session.add(document)
        await session.flush()

        content = TenderDocumentContent(
            sha256=sha,
            storage_key=f"fixture/{CORPUS_ID}.txt",
            file_size=len(text.encode("utf-8")),
            mime_type="application/pdf",
            detected_extension="pdf",
            is_text_extractable=True,
        )
        session.add(content)
        await session.flush()

        session.add(
            TenderDocumentFile(
                tender_document_id=document.id,
                content_id=content.id,
                source_url=document.url,
                is_current=True,
                download_status=DownloadStatus.DOWNLOADED,
            )
        )

        extraction = TenderDocumentExtraction(
            content_id=content.id,
            parser_version=PARSER_VERSION,
            revision_number=1,
            status=ExtractionStatus.EXTRACTED,
            quality=ExtractionQuality.GOOD,
            page_count=len(PAGES),
            extracted_page_count=len(PAGES),
            empty_page_count=0,
            text_char_count=len(text),
            ocr_candidate=False,
            is_current=True,
        )
        session.add(extraction)
        await session.flush()

        for number, page_text in enumerate(PAGES, start=1):
            session.add(
                TenderDocumentPage(
                    extraction_id=extraction.id,
                    page_number=number,
                    text=page_text,
                    char_count=len(page_text),
                    text_hash=hashlib.sha256(page_text.encode("utf-8")).hexdigest(),
                    status=PageExtractionStatus.EXTRACTED,
                )
            )

        await session.commit()
        print(f"Corpus '{CORPUS_ID}' afegit com a document {document.id} "
              f"({len(PAGES)} pagines).")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    asyncio.run(seed(uuid.UUID(sys.argv[1])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
