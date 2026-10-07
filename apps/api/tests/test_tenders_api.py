"""Tests del feed public de licitacions."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.ingestion.placsp.service import PlacspIngestionService
from app.models.tenders import Tender

FIXTURES = Path(__file__).parent / "fixtures" / "placsp"


@pytest.fixture
async def ingested(db, storage):
    """Deixa la base de dades amb les tres licitacions del fixture."""
    await PlacspIngestionService(db, storage).run(fixture=FIXTURES / "feed_sample.atom")


class TestFeed:
    async def test_el_feed_requereix_sessio(self, client):
        assert (await client.get("/api/v1/tenders")).status_code == 401

    async def test_llista_paginada(self, client_a, ingested):
        response = await client_a.get("/api/v1/tenders")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3
        assert body["limit"] == 25
        assert body["offset"] == 0

    async def test_la_paginacio_funciona(self, client_a, ingested):
        first = (await client_a.get("/api/v1/tenders?limit=2&offset=0")).json()
        second = (await client_a.get("/api/v1/tenders?limit=2&offset=2")).json()

        assert len(first["items"]) == 2
        assert len(second["items"]) == 1
        assert {item["id"] for item in first["items"]}.isdisjoint(
            {item["id"] for item in second["items"]}
        )

    async def test_la_fila_del_feed_no_te_cap_puntuacio_dencaix(self, client_a, ingested):
        """Encara no hi ha matching: el feed no pot suggerir cap compatibilitat."""
        item = (await client_a.get("/api/v1/tenders")).json()["items"][0]
        prohibits = {"fit_score", "score", "match", "compatibility", "recommendation"}
        assert prohibits.isdisjoint(item.keys())
        assert "company_id" not in item

    async def test_informacio_minima_de_la_fila(self, client_a, ingested):
        items = (await client_a.get("/api/v1/tenders")).json()["items"]
        by_expedient = {item["expediente_number"]: item for item in items}

        con_lots = by_expedient["26-002735"]
        assert con_lots["lot_count"] == 3
        assert con_lots["buyer"]["name"]
        assert con_lots["cpvs"]
        assert con_lots["source_code"] == "PLACSP"
        assert con_lots["is_modified"] is False

    async def test_filtre_per_import(self, client_a, ingested):
        gran = (await client_a.get("/api/v1/tenders?min_amount=1000000")).json()
        assert gran["total"] == 1
        assert gran["items"][0]["expediente_number"] == "2026-044-O"

    async def test_filtre_per_cpv(self, client_a, ingested):
        response = (await client_a.get("/api/v1/tenders?cpv=33000000")).json()
        assert response["total"] == 1
        assert response["items"][0]["expediente_number"] == "26-002735"

    async def test_filtre_per_lots(self, client_a, ingested):
        amb = (await client_a.get("/api/v1/tenders?has_lots=true")).json()
        sense = (await client_a.get("/api/v1/tenders?has_lots=false")).json()
        assert amb["total"] == 2
        assert sense["total"] == 1

    async def test_filtre_de_text(self, client_a, ingested):
        response = (await client_a.get("/api/v1/tenders?q=material")).json()
        assert response["total"] == 1

    async def test_ordenacions(self, client_a, ingested):
        per_import = (await client_a.get("/api/v1/tenders?sort=amount")).json()["items"]
        assert per_import[0]["expediente_number"] == "2026-044-O"

        per_termini = (await client_a.get("/api/v1/tenders?sort=deadline")).json()["items"]
        terminis = [item["deadline_at"] for item in per_termini if item["deadline_at"]]
        assert terminis == sorted(terminis)

    async def test_facetes(self, client_a, tenant_a, ingested):
        _, company = tenant_a
        facets = (
            await client_a.get(f"/api/v1/companies/{company.id}/tenders/facets")
        ).json()
        assert facets["total_tenders"] == 3
        assert "resolved" in facets["statuses"]
        assert facets["nuts_codes"]


class TestDetail:
    async def test_fitxa_completa(self, client_a, ingested):
        items = (await client_a.get("/api/v1/tenders")).json()["items"]
        tender_id = next(i["id"] for i in items if i["expediente_number"] == "26-002735")

        detail = (await client_a.get(f"/api/v1/tenders/{tender_id}")).json()
        assert detail["expediente_number"] == "26-002735"
        assert detail["buyer"]["tax_identifier"]
        assert len(detail["lots"]) == 3
        assert detail["lots"][0]["cpvs"]
        assert detail["documents"]
        assert detail["source_publication_count"] == 1
        assert detail["first_seen_at"] and detail["last_seen_at"]

    async def test_la_fitxa_porta_la_presencia_a_la_font_com_la_fila(self, db, client_a, ingested):
        """`source_removed_at`, `source_updated_at` i `source_code`, igual que la fila."""
        tender = (
            await db.execute(sa.select(Tender).where(Tender.expediente_number == "26-002735"))
        ).scalar_one()
        removed_at = datetime(2026, 10, 2, 8, 30, tzinfo=UTC)
        tender.source_removed_at = removed_at
        await db.commit()

        detail = (await client_a.get(f"/api/v1/tenders/{tender.id}")).json()
        row = next(
            item
            for item in (await client_a.get("/api/v1/tenders")).json()["items"]
            if item["id"] == str(tender.id)
        )
        returned = datetime.fromisoformat(detail["source_removed_at"])
        # SQLite no desa la zona horaria; PostgreSQL si. El valor es el mateix (UTC).
        assert returned.replace(tzinfo=returned.tzinfo or UTC) == removed_at
        assert detail["source_code"] == "PLACSP"  # el codi tal com esta desat
        for field in ("source_removed_at", "source_updated_at", "source_code"):
            assert detail[field] == row[field], field

    async def test_la_fitxa_inclou_tracabilitat(self, client_a, ingested):
        items = (await client_a.get("/api/v1/tenders")).json()["items"]
        detail = (await client_a.get(f"/api/v1/tenders/{items[0]['id']}")).json()

        trace = detail["traceability"]
        assert trace["source_code"] == "PLACSP"
        assert trace["parser_version"] == "placsp-v2"
        assert len(trace["raw_hash"]) == 64
        assert trace["external_id"].startswith("https://")

    async def test_lots_i_documents_per_separat(self, client_a, tenant_a, ingested):
        _, company = tenant_a
        items = (await client_a.get("/api/v1/tenders")).json()["items"]
        tender_id = next(i["id"] for i in items if i["expediente_number"] == "26-002735")

        lots = (await client_a.get(f"/api/v1/tenders/{tender_id}/lots")).json()
        # Els plecs es llegeixen dins d'una empresa. Aquesta es publica, i per
        # aixo es veu des de qualsevol.
        documents = (
            await client_a.get(f"/api/v1/companies/{company.id}/tenders/{tender_id}/documents")
        ).json()
        assert len(lots) == 3
        assert all(document["url"].startswith("http") for document in documents)

    async def test_revisions(self, client_a, db, storage, ingested):
        await PlacspIngestionService(db, storage).run(
            fixture=FIXTURES / "feed_sample_modified.atom"
        )
        items = (await client_a.get("/api/v1/tenders")).json()["items"]
        tender_id = next(
            i["id"] for i in items if i["expediente_number"] == "AMARCO/2026/MUSEO/607BAS"
        )

        revisions = (await client_a.get(f"/api/v1/tenders/{tender_id}/revisions")).json()
        assert len(revisions) == 2
        assert revisions[0]["revision_number"] == 2
        assert revisions[0]["raw_hash"] != revisions[1]["raw_hash"]

    async def test_licitacio_inexistent(self, client_a):
        response = await client_a.get(
            "/api/v1/tenders/00000000-0000-0000-0000-000000000000"
        )
        assert response.status_code == 404


class TestBuyers:
    async def test_llistat_dorganismes(self, client_a, tenant_a, ingested):
        _, company = tenant_a
        response = (await client_a.get(f"/api/v1/companies/{company.id}/buyers")).json()
        assert response["total"] == 3
        assert response["items"][0]["name"]

    async def test_fitxa_dorganisme(self, client_a, tenant_a, ingested):
        _, company = tenant_a
        base = f"/api/v1/companies/{company.id}/buyers"
        buyer_id = (await client_a.get(base)).json()["items"][0]["id"]
        detail = (await client_a.get(f"{base}/{buyer_id}")).json()
        assert detail["external_identifier"]


class TestIngestionRuns:
    async def test_historial_dexecucions(self, client_a, ingested):
        response = (await client_a.get("/api/v1/ingestion/runs")).json()
        assert response["total"] >= 1
        run = response["items"][0]
        assert run["records_fetched"] == 3
        assert run["status"] in {"success", "partial"}

    async def test_no_hi_ha_cap_endpoint_public_per_llancar_ingesta(self, client_a):
        """La ingesta s'engega per CLI, no exposada a la xarxa."""
        response = await client_a.post("/api/v1/ingestion/placsp/run", json={})
        assert response.status_code in {404, 405}


async def test_els_documents_retirats_de_la_font_no_es_presenten_com_a_vigents(
    client_a, tenant_a, session, tender_document
):
    """Fitxa i llista de plecs exclouen `removed_at` per defecte; l'historial ho explica."""
    from app.models.enums import TenderDocumentType
    from app.models.tenders import TenderDocument

    removed = TenderDocument(
        tender_id=tender_document.tender_id,
        name="PPT retirat.pdf",
        document_type=TenderDocumentType.TECHNICAL_TERMS,
        url="https://example.com/ppt-antic.pdf",
        removed_at=datetime(2026, 10, 6, 18, 0, tzinfo=UTC),
    )
    session.add(removed)
    await session.commit()
    _, company = tenant_a
    tender_id = tender_document.tender_id

    detail = (await client_a.get(f"/api/v1/tenders/{tender_id}")).json()
    assert [d["id"] for d in detail["documents"]] == [str(tender_document.id)]
    assert detail["documents"][0]["removed_at"] is None

    base = f"/api/v1/companies/{company.id}/tenders/{tender_id}/documents"
    listed = (await client_a.get(base)).json()
    assert [d["id"] for d in listed] == [str(tender_document.id)]

    everything = (await client_a.get(f"{base}?include_removed=true")).json()
    by_id = {d["id"]: d for d in everything}
    assert set(by_id) == {str(tender_document.id), str(removed.id)}
    assert by_id[str(removed.id)]["removed_at"].startswith("2026-10-06")


async def test_the_tender_detail_reports_the_documental_state(client_a, tender_document):
    """Sense processar, un document no te cap fitxer: el camp ha de ser null."""
    response = await client_a.get(f"/api/v1/tenders/{tender_document.tender_id}")

    assert response.status_code == 200
    documents = response.json()["documents"]
    assert len(documents) == 1
    assert documents[0]["latest_file"] is None


async def test_the_tender_detail_summarises_a_processed_document(
    client_a, tender_document, tenant_a, session_factory
):
    import httpx

    from app.services import tender_document_processing as processing
    from tests.fixtures.pdf_builder import build_pdf

    pdf = build_pdf(["Plec de clausules administratives particulars. " * 8] * 2)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=pdf, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as fake:
        original = processing.download_document

        async def patched(url, *, client=None, resolver=None):
            return await original(url, client=fake, resolver=lambda h, p: ["93.184.216.34"])

        processing.download_document = patched
        try:
            from tests.jobs_support import request_processing

            await request_processing(
                client_a,
                tenant_a[1].id,
                tender_document.tender_id,
                session_factory,
                json={"document_ids": [str(tender_document.id)]},
            )
        finally:
            processing.download_document = original

    response = await client_a.get(f"/api/v1/tenders/{tender_document.tender_id}")
    summary = response.json()["documents"][0]["latest_file"]

    assert summary["download_status"] == "downloaded"
    assert summary["extraction_status"] == "extracted"
    assert summary["page_count"] == 2
    assert summary["quality"] == "good"
    assert summary["ocr_candidate"] is False
    assert "storage_key" not in response.text
