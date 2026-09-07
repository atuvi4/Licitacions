"""Chunking i fingerprint: deterministes i sense IA."""

from __future__ import annotations

from app.services.requirement_chunking import (
    AnalyzableDocument,
    PageRef,
    build_chunks,
    compute_input_fingerprint,
    render_chunk,
)


def make_document(
    pages: int, *, doc_id: str = "doc-1", extraction_id: str = "ext-1", dtype: str = "administrative_terms"
) -> AnalyzableDocument:
    return AnalyzableDocument(
        tender_document_id=doc_id,
        document_name="PCAP",
        document_type=dtype,
        extraction_id=extraction_id,
        revision_number=1,
        parser_version="pypdf-v1",
        content_sha256="a" * 64,
        pages=tuple(PageRef(f"p-{n}", n, f"Text de la pagina {n}") for n in range(1, pages + 1)),
    )


def test_pages_are_never_split_between_chunks():
    chunks = build_chunks([make_document(10)], pages_per_chunk=4, overlap_pages=0)

    seen = [page.page_number for chunk in chunks for page in chunk.pages]
    assert seen == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert all(len(chunk.pages) >= 1 for chunk in chunks)


def test_every_page_appears_at_least_once():
    document = make_document(17)
    chunks = build_chunks([document], pages_per_chunk=5, overlap_pages=1)

    covered = {page.page_number for chunk in chunks for page in chunk.pages}
    assert covered == set(range(1, 18))


def test_overlap_repeats_the_boundary_pages():
    chunks = build_chunks([make_document(9)], pages_per_chunk=4, overlap_pages=1)

    numbers = [[page.page_number for page in chunk.pages] for chunk in chunks]
    assert numbers[0] == [1, 2, 3, 4]
    assert numbers[1][0] == 4, "la pagina de frontera es repeteix"
    assert 4 in chunks[1].overlap_page_numbers


def test_a_chunk_never_mixes_two_documents():
    documents = [
        make_document(3, doc_id="a", extraction_id="ext-a"),
        make_document(3, doc_id="b", extraction_id="ext-b", dtype="technical_terms"),
    ]
    chunks = build_chunks(documents, pages_per_chunk=10, overlap_pages=0)

    assert len(chunks) == 2
    assert {chunk.document.tender_document_id for chunk in chunks} == {"a", "b"}


def test_documents_are_ordered_pcap_then_ppt_then_annexes():
    documents = [
        make_document(1, doc_id="annex", extraction_id="e1", dtype="additional"),
        make_document(1, doc_id="ppt", extraction_id="e2", dtype="technical_terms"),
        make_document(1, doc_id="pcap", extraction_id="e3", dtype="administrative_terms"),
    ]
    chunks = build_chunks(documents, pages_per_chunk=5, overlap_pages=0)

    assert [c.document.tender_document_id for c in chunks] == ["pcap", "ppt", "annex"]


def test_chunk_indexes_are_sequential():
    chunks = build_chunks([make_document(12)], pages_per_chunk=3, overlap_pages=1)

    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_an_overlap_as_big_as_the_chunk_still_advances():
    """Un solapament mal configurat no pot generar un bucle infinit."""
    chunks = build_chunks([make_document(8)], pages_per_chunk=3, overlap_pages=99)

    assert len(chunks) < 20
    covered = {page.page_number for chunk in chunks for page in chunk.pages}
    assert covered == set(range(1, 9))


def test_rendering_marks_pages_and_identifiers():
    chunk = build_chunks([make_document(2)], pages_per_chunk=5, overlap_pages=0)[0]

    rendered = render_chunk(chunk)

    assert 'extraction_id="ext-1"' in rendered
    assert 'document_type="administrative_terms"' in rendered
    assert '<PAGE number="1">' in rendered
    assert '<PAGE number="2">' in rendered
    assert "Text de la pagina 2" in rendered


def test_the_fingerprint_is_stable_regardless_of_order():
    a = make_document(2, doc_id="a", extraction_id="ext-a")
    b = make_document(2, doc_id="b", extraction_id="ext-b")

    assert compute_input_fingerprint([a, b]) == compute_input_fingerprint([b, a])


def test_the_fingerprint_changes_with_the_extraction_revision():
    base = make_document(2)
    reextracted = AnalyzableDocument(
        tender_document_id=base.tender_document_id,
        document_name=base.document_name,
        document_type=base.document_type,
        extraction_id="ext-2",
        revision_number=2,
        parser_version=base.parser_version,
        content_sha256=base.content_sha256,
        pages=base.pages,
    )

    assert compute_input_fingerprint([base]) != compute_input_fingerprint([reextracted])


def test_the_fingerprint_changes_with_the_parser_version():
    base = make_document(1)
    other = AnalyzableDocument(
        tender_document_id=base.tender_document_id,
        document_name=base.document_name,
        document_type=base.document_type,
        extraction_id=base.extraction_id,
        revision_number=base.revision_number,
        parser_version="pdfminer-v2",
        content_sha256=base.content_sha256,
        pages=base.pages,
    )

    assert compute_input_fingerprint([base]) != compute_input_fingerprint([other])
