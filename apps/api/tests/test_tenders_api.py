"""Tests del feed public de licitacions."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ingestion.placsp.service import PlacspIngestionService

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

    async def test_facetes(self, client_a, ingested):
        facets = (await client_a.get("/api/v1/tenders/facets")).json()
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
        assert detail["revision_count"] == 1
        assert detail["first_seen_at"] and detail["last_seen_at"]

    async def test_la_fitxa_inclou_tracabilitat(self, client_a, ingested):
        items = (await client_a.get("/api/v1/tenders")).json()["items"]
        detail = (await client_a.get(f"/api/v1/tenders/{items[0]['id']}")).json()

        trace = detail["traceability"]
        assert trace["source_code"] == "PLACSP"
        assert trace["parser_version"] == "placsp-v1"
        assert len(trace["raw_hash"]) == 64
        assert trace["external_id"].startswith("https://")

    async def test_lots_i_documents_per_separat(self, client_a, ingested):
        items = (await client_a.get("/api/v1/tenders")).json()["items"]
        tender_id = next(i["id"] for i in items if i["expediente_number"] == "26-002735")

        lots = (await client_a.get(f"/api/v1/tenders/{tender_id}/lots")).json()
        documents = (await client_a.get(f"/api/v1/tenders/{tender_id}/documents")).json()
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
    async def test_llistat_dorganismes(self, client_a, ingested):
        response = (await client_a.get("/api/v1/buyers")).json()
        assert response["total"] == 3
        assert response["items"][0]["name"]

    async def test_fitxa_dorganisme(self, client_a, ingested):
        buyer_id = (await client_a.get("/api/v1/buyers")).json()["items"][0]["id"]
        detail = (await client_a.get(f"/api/v1/buyers/{buyer_id}")).json()
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
