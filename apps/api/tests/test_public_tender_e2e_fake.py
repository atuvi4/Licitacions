"""Cas d'acceptacio E2E amb `fake` sobre la fixture de tender PUBLIC (3B-2 §8 fins al pas 5,
§10): el mateix cami que seguira L'Escala al pilot, sense cap crida a cap model.

    Totes -> processar (worker en proces) -> classificacio global vigent
      -> quota `allowed=false` `provider_not_real` (pas 6 bloquejat honestament)
      -> workspace unic sense decisio -> `analysis_state=absent`
      -> historial amb 1 `source` + N `internal`

El que es protegeix:

* **cap LLM**: 0 crides al proveidor (`fake.SENT_PAYLOADS` buit), cap fabrica de
  proveidor invocada pel worker i cap modul d'un client real importat;
* **cap run d'extraccio**, vigent o no: la quota d'una simulacio fora de test queda
  bloquejada abans de crear cap feina;
* **la fixture es sintetica**: hosts `example.test`, cap NIF, cap URL real, cap nom
  d'empresa real, cap text d'un plec real.
"""

from __future__ import annotations

import re
import sys
from collections import Counter

import pytest
import sqlalchemy as sa

from app.ai.providers import fake
from app.core.config import settings
from app.ingestion.placsp.service import PlacspIngestionService
from app.models.enums import (
    DocumentRole,
    DownloadStatus,
    ExtractionQuality,
    GlobalClassificationTrigger,
    IngestionStatus,
    JobRequestStatus,
    RoleConfidence,
    TenderDocumentOrigin,
    TenderDocumentType,
    WorkJobType,
)
from app.models.global_classification import (
    GlobalDocumentClassificationFinding,
    GlobalDocumentClassificationRun,
    GlobalDocumentRoleSuggestion,
)
from app.models.jobs import WorkJob
from app.models.package import TenderPackageGap
from app.models.requirements import RequirementExtractionRun
from app.models.tender_documents import TenderDocumentExtraction, TenderDocumentFile
from app.models.tenders import Tender, TenderDocument
from app.models.workspace import WorkspaceDecision
from app.tenders.documents import is_analyzable_document
from tests.fixtures import public_tender as fixture
from tests.jobs_support import run_worker

#: Noms de dominis reals de contractacio: cap no pot apareixer a la fixture.
REAL_HOSTS = ("contrataciondelestado", "contractaciopublica", "gencat", "gob.es", "seu-e")
#: Un NIF/CIF espanyol (lletra + 8 digits, o 8 digits + lletra).
NIF_LIKE = re.compile(r"\b(?:[A-Z]\d{7}[A-Z0-9]|\d{8}[A-Z])\b")


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
def pilot_like_gate(monkeypatch):
    """Com al pilot: `fake` fora de test i sense `ALLOW_SIMULATED_ANALYSIS`."""
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(settings, "ALLOW_SIMULATED_ANALYSIS", False)


def _forbidden_provider():
    raise AssertionError("el cami public amb fake no ha de construir cap proveidor")


# --------------------------------------------------------------------------- #
# La fixture                                                                    #
# --------------------------------------------------------------------------- #


def test_the_public_fixture_is_synthetic_and_small():
    atom = fixture.FEED.read_text(encoding="utf-8")
    # Els namespaces XML (`xmlns`) son URI de l'estandard, no URL de la fixture.
    without_namespaces = re.sub(r'xmlns(?::\w+)?="[^"]*"', "", atom)
    hosts = set(re.findall(r"https?://([^/\"'<\s]+)", without_namespaces))

    assert hosts and all(host.endswith(".example.test") for host in hosts), sorted(hosts)
    assert not any(real in atom.lower() for real in REAL_HOSTS)
    assert NIF_LIKE.search(atom) is None, "cap NIF ni CIF a la fixture"
    assert fixture.EXTERNAL_ID in atom and fixture.EXPEDIENTE in atom
    assert len(atom.encode("utf-8")) < 8_000

    documents = fixture.documents()
    assert set(documents) == {fixture.PCAP_URL, fixture.PPT_URL}
    for url, pdf in documents.items():
        assert pdf.startswith(b"%PDF-"), url
        assert len(pdf) < 4_000, url
        assert len(fixture.pages_of(url)) == 2, url
        text = "\n".join(fixture.pages_of(url)).lower()
        assert not any(real in text for real in REAL_HOSTS)
        assert NIF_LIKE.search(text.upper()) is None


# --------------------------------------------------------------------------- #
# De cap a cap                                                                  #
# --------------------------------------------------------------------------- #


async def _documents(db, tender_id) -> list[TenderDocument]:
    rows = await db.scalars(
        sa.select(TenderDocument)
        .where(TenderDocument.tender_id == tender_id)
        .order_by(TenderDocument.name)
        .execution_options(populate_existing=True)
    )
    return list(rows.all())


async def _count(db, model) -> int:
    return int(await db.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


async def test_public_tender_end_to_end_with_fake_and_zero_provider_calls(
    db, storage, session_factory, client_a, tenant_a, pilot_like_gate
):
    _user, company = tenant_a
    modules_before = set(sys.modules)
    base = f"/api/v1/companies/{company.id}"

    # 1. Ingesta de la fixture publica (font agregada, cap xarxa).
    run = await PlacspIngestionService(db, storage).run(
        fixture=fixture.FEED, source_code=fixture.SOURCE_CODE
    )
    assert run.status == IngestionStatus.SUCCESS, run.error_summary
    assert (run.records_new, run.tenders_created) == (1, 1)
    tender = await db.scalar(sa.select(Tender).where(Tender.external_id == fixture.EXTERNAL_ID))
    assert tender is not None
    assert tender.owner_company_id is None, "un tender del feed es public"
    assert tender.expediente_number == fixture.EXPEDIENTE
    assert tender.buyer is not None and tender.buyer.name == fixture.BUYER_NAME
    documents = await _documents(db, tender.id)
    assert [(d.name, d.url) for d in documents] == [
        ("PCAP.pdf", fixture.PCAP_URL),
        ("PPT.pdf", fixture.PPT_URL),
    ]
    assert [d.document_type for d in documents] == [
        TenderDocumentType.ADMINISTRATIVE_TERMS,
        TenderDocumentType.TECHNICAL_TERMS,
    ]
    for document in documents:
        assert document.origin == TenderDocumentOrigin.FEED
        assert document.governing_source_id == tender.source_id
        assert is_analyzable_document(document)

    # 2. «Totes»: la licitacio es visible per a qualsevol empresa, i la fitxa tambe.
    catalogue = await client_a.get("/api/v1/tenders")
    assert catalogue.status_code == 200, catalogue.text
    assert str(tender.id) in {item["id"] for item in catalogue.json()["items"]}
    detail = await client_a.get(f"/api/v1/tenders/{tender.id}")
    assert detail.status_code == 200, detail.text
    assert len(detail.json()["documents"]) == 2

    # 3. Processar: la peticio surt de l'empresa i el worker (en proces) baixa els
    #    dos PDF sintetics del fake de descarrega i deixa la classificacio global.
    requested = await client_a.post(
        f"{base}/tenders/{tender.id}/job-requests/process-documents", json={}
    )
    assert requested.status_code == 202, requested.text
    async with fixture.serving() as http:
        executed = await run_worker(
            session_factory,
            provider_factory=_forbidden_provider,
            download_client=http,
            resolver=fixture.resolver,
        )
    assert [job.job_type for job in executed] == [WorkJobType.PROCESS_TENDER_DOCUMENTS]
    final = await client_a.get(f"{base}/job-requests/{requested.json()['id']}")
    assert final.status_code == 200, final.text
    assert final.json()["status"] == JobRequestStatus.SUCCEEDED

    for document in documents:
        file = await db.scalar(
            sa.select(TenderDocumentFile).where(
                TenderDocumentFile.tender_document_id == document.id,
                TenderDocumentFile.is_current.is_(True),
            )
        )
        assert file is not None and file.download_status == DownloadStatus.DOWNLOADED
        assert file.content.sha256 == fixture.sha256_of(document.url)
        extraction = await db.scalar(
            sa.select(TenderDocumentExtraction).where(
                TenderDocumentExtraction.content_id == file.content_id,
                TenderDocumentExtraction.is_current.is_(True),
            )
        )
        assert extraction is not None
        assert (extraction.page_count, extraction.quality) == (2, ExtractionQuality.GOOD)

    classification = await db.scalar(
        sa.select(GlobalDocumentClassificationRun).where(
            GlobalDocumentClassificationRun.tender_id == tender.id,
            GlobalDocumentClassificationRun.is_current.is_(True),
        )
    )
    assert classification is not None, "cap classificacio global vigent"
    assert classification.trigger == GlobalClassificationTrigger.DOCUMENT_PROCESSING
    assert classification.document_count == 2
    suggestions = {
        s.tender_document_id: s
        for s in await db.scalars(
            sa.select(GlobalDocumentRoleSuggestion).where(
                GlobalDocumentRoleSuggestion.run_id == classification.id
            )
        )
    }
    pcap, ppt = documents
    assert (suggestions[pcap.id].role, suggestions[pcap.id].confidence) == (
        DocumentRole.PCAP,
        RoleConfidence.HIGH,
    )
    assert (suggestions[ppt.id].role, suggestions[ppt.id].confidence) == (
        DocumentRole.PPT,
        RoleConfidence.HIGH,
    )
    findings = (
        await db.scalars(
            sa.select(GlobalDocumentClassificationFinding).where(
                GlobalDocumentClassificationFinding.run_id == classification.id
            )
        )
    ).all()
    assert findings == [], [f.code for f in findings]
    assert await _count(db, TenderPackageGap) == 0
    package = (await client_a.get(f"/api/v1/tenders/{tender.id}")).json()["package"]
    assert package is not None and (package["status"], package["gaps"]) == ("complete", [])

    # 4. Quota: amb `fake` fora de test, bloquejada abans de cap feina (pas 6 de §8).
    created = await client_a.post(f"{base}/tenders/{tender.id}/analysis-quotes", json={})
    assert created.status_code == 201, created.text
    quote = created.json()
    assert (quote["allowed"], quote["blocking_reason"]) == (False, "provider_not_real")
    assert quote["provider_basis"] == "simulation"
    assert quote["prerequisites"]["failed"][0] == "provider_not_real"
    confirm = await client_a.post(f"{base}/analysis-quotes/{quote['id']}/confirm")
    assert confirm.status_code == 422, confirm.text
    assert confirm.json()["error"]["code"] == "provider_not_real"
    assert await _count(db, RequirementExtractionRun) == 0, "cap run, vigent o no"
    job_types = (await db.scalars(sa.select(WorkJob.job_type))).all()
    assert job_types == [WorkJobType.PROCESS_TENDER_DOCUMENTS], "cap feina d'analisi"

    # 5. Workspace unic, sense cap decisio, i `analysis_state` honest.
    opened = await client_a.post(f"{base}/tenders/{tender.id}/workspace", json={})
    assert opened.status_code in (200, 201), opened.text
    assert opened.json()["created"] is True
    again = await client_a.post(f"{base}/tenders/{tender.id}/workspace", json={})
    assert again.status_code in (200, 201), again.text
    assert again.json()["created"] is False
    workspace_id = opened.json()["workspace"]["id"]
    assert again.json()["workspace"]["id"] == workspace_id
    listed = (await client_a.get(f"{base}/workspaces")).json()
    assert [w["id"] for w in listed if w["tender_id"] == str(tender.id)] == [workspace_id]
    workspace = (await client_a.get(f"{base}/workspaces/{workspace_id}")).json()
    assert workspace["human_decision"] is None
    coverage = workspace["dossier_coverage"]
    assert coverage["analysis_state"] == "absent"
    assert coverage["mandatory_total"] is None
    assert await _count(db, WorkspaceDecision) == 0

    # 6. Historial: una revisio de la font (la ingesta) i les internes del processat.
    history = await client_a.get(f"/api/v1/tenders/{tender.id}/history")
    assert history.status_code == 200, history.text
    origins = Counter(revision["origin"] for revision in history.json())
    assert origins["source"] == 1 and origins["internal"] >= 1
    assert origins["manual"] == 0
    assert [r["trigger"] for r in history.json() if r["origin"] == "source"] == ["ingestion"]

    # 0 crides al proveidor i cap client real importat durant el cami.
    assert fake.SENT_PAYLOADS == []
    imported = {name for name in set(sys.modules) - modules_before if "anthropic" in name}
    assert imported == set(), imported
