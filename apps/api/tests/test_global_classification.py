"""Classificacio global dels documents d'una licitacio publica (3B-2 §3.2, §2.5).

Que es protegeix aqui:

* **el worker classifica sol**: despres de processar els documents d'un tender
  public hi ha una passada global vigent (`document_processing`), sense cap
  accio de cap empresa;
* **nomes fets globals**: l'empremta no depen de cap empresa (cap ruta de
  recepcio). Dues empreses, el mateix tender: la mateixa empremta;
* **immutable i idempotent**: mateixa empremta, mateixa passada (cap fila nova);
* **el paquet incomplet es declara**: un plec que cita un DOC-03 que no hi es
  dona `referenced_document_missing` i un forat a `tender_package_gaps` amb el
  que aportaria; quan el document arriba (verificat), el forat es resol;
* **una licitacio manual no es classifica globalment**: els seus documents son
  d'una empresa i viuen a la classificacio tenant.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest
import sqlalchemy as sa

from app.ai.providers import fake
from app.jobs import handlers
from app.models.classification import TenderDocumentRoleDecision
from app.models.enums import (
    ClassificationFindingCode,
    DocumentContributionStatus,
    DocumentRole,
    GlobalClassificationTrigger,
    PackageGapCode,
    PackageGapProducer,
    ReceptionChannel,
    ReceptionItemStatus,
    ReceptionStatus,
    RoleConfidence,
    RoleSource,
    SourceType,
    TenderDocumentOrigin,
    TenderDocumentType,
    WorkJobType,
)
from app.models.global_classification import (
    GlobalDocumentClassificationFinding,
    GlobalDocumentClassificationRun,
    GlobalDocumentRoleSuggestion,
)
from app.models.package import TenderPackageGap
from app.models.reception import TenderDocumentReception, TenderDocumentReceptionItem
from app.models.sources import Source
from app.models.tenders import Tender, TenderDocument
from app.services import document_classification
from app.services import global_document_classification as global_classification
from app.services.tender_document_processing import process_tender_document
from app.services.tender_lifecycle import record_revision
from tests.conftest import create_user_with_company
from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

PUBLIC_IP = "93.184.216.34"
_BODY = " ".join(["Text del plec amb prou contingut per llegir-lo be."] * 8)

#: PCAP que es declara DOC-01 i cita el quadre DOC-03, que no hi es.
PCAP_TEXT = (
    "Plec de clausules administratives particulars\nCodi DOC-01\n"
    "Els blocs i els terminis son al quadre de caracteristiques (DOC-03).\n" + _BODY
)
PPT_TEXT = "Plec de prescripcions tecniques particulars\nCodi DOC-02\n" + _BODY
QUADRE_TEXT = "Quadre de caracteristiques i clausules especifiques\nCodi DOC-03\n" + _BODY
PCAP_PDF = build_pdf([PCAP_TEXT])
PPT_PDF = build_pdf([PPT_TEXT])
QUADRE_PDF = build_pdf([QUADRE_TEXT])


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def _serving(routes: dict[str, bytes]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        payload = routes.get(str(request.url))
        if payload is None:
            return httpx.Response(404)
        return httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def make_public_tender(db) -> Tender:
    source = Source(
        code=f"placsp-{uuid.uuid4().hex[:8]}",
        name="Font sindicada",
        source_type=SourceType.SYNDICATION_ATOM,
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title="Servei de manteniment de la xarxa de fibra",
    )
    db.add(tender)
    await db.commit()
    await record_revision(db, tender, trigger="ingestion")
    await db.commit()
    return tender


@pytest.fixture
async def public_tender(db) -> Tender:
    return await make_public_tender(db)


def feed_url(name: str) -> str:
    return f"https://contrataciondelestado.es/feed/{uuid.uuid4().hex[:6]}/{name}"


async def add_feed_document(
    db, tender: Tender, name: str, document_type: TenderDocumentType
) -> TenderDocument:
    document = TenderDocument(
        tender_id=tender.id,
        name=name,
        document_type=document_type,
        url=feed_url(name),
        origin=TenderDocumentOrigin.FEED,
        governing_source_id=tender.source_id,
    )
    db.add(document)
    await db.commit()
    return document


async def processed_feed_document(
    db, tender: Tender, name: str, document_type: TenderDocumentType, payload: bytes
) -> TenderDocument:
    """Un document del feed ja descarregat i extret (sense passar pel worker)."""
    document = await add_feed_document(db, tender, name, document_type)
    async with _serving({document.url: payload}) as client:
        await process_tender_document(db, document, client=client, resolver=resolver)
    await db.refresh(document)
    return document


async def public_package(db, tender: Tender) -> tuple[TenderDocument, TenderDocument]:
    pcap = await processed_feed_document(
        db, tender, "PCAP.pdf", TenderDocumentType.ADMINISTRATIVE_TERMS, PCAP_PDF
    )
    ppt = await processed_feed_document(
        db, tender, "PPT.pdf", TenderDocumentType.TECHNICAL_TERMS, PPT_PDF
    )
    return pcap, ppt


async def current_run(db, tender_id) -> GlobalDocumentClassificationRun | None:
    return await db.scalar(
        sa.select(GlobalDocumentClassificationRun)
        .where(
            GlobalDocumentClassificationRun.tender_id == tender_id,
            GlobalDocumentClassificationRun.is_current.is_(True),
        )
        .execution_options(populate_existing=True)
    )


async def run_count(db, tender_id) -> int:
    return await db.scalar(
        sa.select(sa.func.count())
        .select_from(GlobalDocumentClassificationRun)
        .where(GlobalDocumentClassificationRun.tender_id == tender_id)
    )


async def suggestions_of(db, run) -> dict[uuid.UUID, GlobalDocumentRoleSuggestion]:
    rows = await db.scalars(
        sa.select(GlobalDocumentRoleSuggestion).where(GlobalDocumentRoleSuggestion.run_id == run.id)
    )
    return {s.tender_document_id: s for s in rows}


async def findings_of(db, run) -> list[GlobalDocumentClassificationFinding]:
    return list(
        (
            await db.scalars(
                sa.select(GlobalDocumentClassificationFinding).where(
                    GlobalDocumentClassificationFinding.run_id == run.id
                )
            )
        ).all()
    )


async def gaps_of(db, tender_id) -> list[TenderPackageGap]:
    return list(
        (
            await db.scalars(
                sa.select(TenderPackageGap)
                .where(TenderPackageGap.tender_id == tender_id)
                .order_by(TenderPackageGap.created_at)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


def _job_context(session_factory, tender_id, client) -> handlers.JobContext:
    async def yes() -> bool:
        return True

    async def progress(_done: int, _total: int) -> None:
        return None

    return handlers.JobContext(
        session_factory=session_factory,
        job=handlers.JobSnapshot(
            id=uuid.uuid4(),
            job_type=WorkJobType.PROCESS_TENDER_DOCUMENTS,
            tender_id=tender_id,
            parameters={},
            input_fingerprint=None,
            provider=None,
            model=None,
        ),
        should_continue=yes,
        report_progress=progress,
        provider_factory=fake.FakeProvider,
        download_client=client,
        resolver=resolver,
    )


# --------------------------------------------------------------------------- #
# El worker la produeix                                                        #
# --------------------------------------------------------------------------- #


async def test_el_worker_deixa_una_classificacio_global_vigent_despres_de_processar(
    db, session_factory, public_tender
):
    pcap = await add_feed_document(
        db, public_tender, "PCAP.pdf", TenderDocumentType.ADMINISTRATIVE_TERMS
    )
    ppt = await add_feed_document(db, public_tender, "PPT.pdf", TenderDocumentType.TECHNICAL_TERMS)
    assert await current_run(db, public_tender.id) is None

    async with _serving({pcap.url: PCAP_PDF, ppt.url: PPT_PDF}) as client:
        completion = await handlers.process_tender_documents(
            _job_context(session_factory, public_tender.id, client)
        )
    assert completion is not None

    run = await current_run(db, public_tender.id)
    assert run is not None
    assert run.trigger == GlobalClassificationTrigger.DOCUMENT_PROCESSING
    assert run.document_count == 2
    suggestions = await suggestions_of(db, run)
    assert (suggestions[pcap.id].role, suggestions[pcap.id].confidence) == (
        DocumentRole.PCAP,
        RoleConfidence.HIGH,
    )
    assert (suggestions[ppt.id].role, suggestions[ppt.id].confidence) == (
        DocumentRole.PPT,
        RoleConfidence.HIGH,
    )
    assert len(suggestions[pcap.id].content_sha256) == 64

    findings = await findings_of(db, run)
    referenced = ClassificationFindingCode.REFERENCED_DOCUMENT_MISSING
    missing = [f for f in findings if f.code == referenced]
    assert [f.detail["code"] for f in missing] == ["DOC-03"]

    gaps = await gaps_of(db, public_tender.id)
    assert [(g.code, g.detail["key"]) for g in gaps] == [
        (PackageGapCode.REFERENCED_DOCUMENT_MISSING, "DOC-03")
    ]
    gap = gaps[0]
    # Impacte desconegut (3B-2 I1): els codis DOC-NN son de cada organ; el codi i
    # les cites es queden a `detail`, el que aportaria no s'inventa.
    assert gap.would_provide == []
    assert gap.detail["code"] == "DOC-03" and gap.detail["references"]
    assert gap.producer == PackageGapProducer.CLASSIFICATION
    assert gap.producer_run_id == run.id
    assert gap.resolved_at is None

    # La classificacio global no escriu mai el tipus del feed.
    for document in (pcap, ppt):
        await db.refresh(document)
    assert pcap.document_type == TenderDocumentType.ADMINISTRATIVE_TERMS
    assert ppt.document_type == TenderDocumentType.TECHNICAL_TERMS


async def test_mateixa_empremta_mateixa_passada_sense_cap_fila_nova(db, public_tender):
    await public_package(db, public_tender)
    first = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    again = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
    )
    assert again.id == first.id
    assert await run_count(db, public_tender.id) == 1
    # I el forat no es duplica.
    assert len(await gaps_of(db, public_tender.id)) == 1


async def test_l_empremta_es_la_dels_fets_globals(db, public_tender):
    pcap, ppt = await public_package(db, public_tender)
    run = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    expected = global_classification.global_fingerprint(
        [
            (pcap.id, (await suggestions_of(db, run))[pcap.id].content_sha256, pcap.name,
             (await suggestions_of(db, run))[pcap.id].extraction_id),
            (ppt.id, (await suggestions_of(db, run))[ppt.id].content_sha256, ppt.name,
             (await suggestions_of(db, run))[ppt.id].extraction_id),
        ]
    )  # fmt: skip
    assert run.input_fingerprint == expected


async def test_dues_empreses_el_mateix_tender_public_la_mateixa_empremta(db, public_tender):
    """Prova A/B: cap ruta de recepcio de cap empresa entra a l'empremta global."""
    pcap, _ppt = await public_package(db, public_tender)
    _user_a, company_a = await create_user_with_company(
        db, email=f"a-{uuid.uuid4().hex[:6]}@empresa.com", company_name="Empresa A"
    )
    _user_b, company_b = await create_user_with_company(
        db, email=f"b-{uuid.uuid4().hex[:6]}@empresa.com", company_name="Empresa B"
    )
    before = await global_classification.compute_global_fingerprint(db, public_tender.id)

    # L'empresa A va rebre el PCAP dins d'un ZIP seu, amb una altra ruta.
    reception = TenderDocumentReception(
        company_id=company_a.id,
        tender_id=public_tender.id,
        channel=ReceptionChannel.ZIP_UPLOAD,
        label="paquet.zip",
        status=ReceptionStatus.COMPLETED,
        item_count=1,
        accepted_count=1,
        skipped_count=0,
    )
    db.add(reception)
    await db.flush()
    db.add(
        TenderDocumentReceptionItem(
            reception_id=reception.id,
            company_id=company_a.id,
            position=0,
            path="Carpeta privada/01 PCAP definitiu.pdf",
            filename="01 PCAP definitiu.pdf",
            size_bytes=10,
            status=ReceptionItemStatus.ACCEPTED,
            tender_document_id=pcap.id,
        )
    )
    await db.commit()

    # La lectura tenant si que en depen (per aixo no es pot reutilitzar)...
    inputs_a = await document_classification._inputs(db, company_a.id, public_tender.id)
    inputs_b = await document_classification._inputs(db, company_b.id, public_tender.id)
    assert document_classification._fingerprint(inputs_a) != document_classification._fingerprint(
        inputs_b
    )
    # ...la global no.
    after = await global_classification.compute_global_fingerprint(db, public_tender.id)
    assert after == before
    run = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    assert run.input_fingerprint == before


async def test_un_document_pendent_de_verificar_no_entra_a_la_classificacio(db, public_tender):
    await public_package(db, public_tender)
    run = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    pending = TenderDocument(
        tender_id=public_tender.id,
        name="Quadre.pdf",
        document_type=TenderDocumentType.OTHER,
        url="https://contractaciopublica.cat/perfil/quadre.pdf",
        origin=TenderDocumentOrigin.OPERATOR_URL,
        contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
    )
    db.add(pending)
    await db.commit()

    assert (
        await global_classification.compute_global_fingerprint(db, public_tender.id)
        == run.input_fingerprint
    )


async def test_un_document_nou_dona_una_passada_nova_i_l_anterior_es_conserva(db, public_tender):
    await public_package(db, public_tender)
    first = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    await processed_feed_document(
        db, public_tender, "Quadre.pdf", TenderDocumentType.ADDITIONAL, QUADRE_PDF
    )
    second = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
    )

    assert second.id != first.id
    await db.refresh(first)
    assert first.is_current is False and second.is_current is True
    assert await run_count(db, public_tender.id) == 2
    assert len(await suggestions_of(db, first)) == 2  # immutable


async def test_el_forat_es_resol_quan_arriba_el_document_citat(db, public_tender):
    await public_package(db, public_tender)
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    quadre = await processed_feed_document(
        db, public_tender, "Quadre.pdf", TenderDocumentType.ADDITIONAL, QUADRE_PDF
    )
    run = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
    )

    codes = {f.code for f in await findings_of(db, run)}
    assert ClassificationFindingCode.REFERENCED_DOCUMENT_MISSING not in codes
    (gap,) = await gaps_of(db, public_tender.id)
    assert gap.resolved_at is not None
    assert gap.resolved_by_document_id == quadre.id


async def test_un_rol_obligatori_que_falta_es_un_forat(db, public_tender):
    await processed_feed_document(
        db, public_tender, "PCAP.pdf", TenderDocumentType.ADMINISTRATIVE_TERMS, PCAP_PDF
    )
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    keys = {(g.code, g.detail["key"]) for g in await gaps_of(db, public_tender.id)}
    assert (PackageGapCode.REQUIRED_ROLE_MISSING, "ppt") in keys


async def test_un_forat_declarat_per_l_operador_no_el_tanca_la_classificacio(db, public_tender):
    await public_package(db, public_tender)
    db.add(
        TenderPackageGap(
            tender_id=public_tender.id,
            code=PackageGapCode.DECLARED_BY_OPERATOR,
            detail={"key": "annex-plànols"},
            producer=PackageGapProducer.OPERATOR,
        )
    )
    await db.commit()
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    declared = [
        g for g in await gaps_of(db, public_tender.id) if g.producer == PackageGapProducer.OPERATOR
    ]
    assert len(declared) == 1 and declared[0].resolved_at is None


async def test_una_licitacio_manual_no_es_classifica_globalment(db, tenant_a):
    _user, company = tenant_a
    from app.services.manual_tender import get_or_create_manual_source

    source = await get_or_create_manual_source(db)
    tender = Tender(
        source_id=source.id,
        external_id=f"man-{uuid.uuid4().hex[:6]}",
        title="Manual propia",
        owner_company_id=company.id,
    )
    db.add(tender)
    await db.commit()
    db.add(
        TenderDocument(
            tender_id=tender.id,
            name="PCAP.pdf",
            document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
            url=f"upload://{uuid.uuid4()}",
            origin=TenderDocumentOrigin.OWNER_UPLOAD,
        )
    )
    await db.commit()

    run = await global_classification.classify_tender_globally(
        db, tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    assert run is None
    assert await run_count(db, tender.id) == 0


async def test_la_decisio_tenant_porta_la_versio_del_fitxer(db, public_tender, tenant_a):
    """`content_sha256` NOT NULL: la decisio s'ancora al fitxer vigent."""
    _user, company = tenant_a
    pcap, _ppt = await public_package(db, public_tender)
    run = await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    sha = (await suggestions_of(db, run))[pcap.id].content_sha256
    decision = TenderDocumentRoleDecision(
        company_id=company.id,
        tender_document_id=pcap.id,
        role=DocumentRole.CHARACTERISTICS_TABLE,
        content_sha256=sha,
        decided_at=datetime.now(UTC),
    )
    db.add(decision)
    await db.commit()
    assert decision.content_sha256 == sha


# --------------------------------------------------------------------------- #
# Via de l'operador: /platform/tenders/{id}/classification                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def operator(db):
    from tests.test_platform_onboarding import create_operator

    return await create_operator(db)


@pytest.fixture
async def op_client(client_factory, operator):
    from tests.conftest import login

    http_client = client_factory()
    await login(http_client, operator.email)
    return http_client


def platform_url(tender_id) -> str:
    return f"/api/v1/platform/tenders/{tender_id}"


async def test_una_empresa_no_hi_entra(client_a, db, public_tender):
    pcap, _ppt = await public_package(db, public_tender)
    base = platform_url(public_tender.id)
    for method, path, kwargs in (
        ("get", f"{base}/classification", {}),
        ("post", f"{base}/classification/run", {}),
        (
            "post",
            f"{base}/documents/{pcap.id}/role-review",
            {"json": {"role": "pcap", "reason": "x", "content_sha256": "a" * 64}},
        ),
    ):
        response = await getattr(client_a, method)(path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)
    assert await run_count(db, public_tender.id) == 0


async def test_l_operador_classifica_llegeix_i_revisa(db, op_client, operator, public_tender):
    pcap, _ppt = await public_package(db, public_tender)
    base = platform_url(public_tender.id)

    response = await op_client.post(f"{base}/classification/run")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["run"]["trigger"] == "operator_request"
    suggestion = next(s for s in body["suggestions"] if s["tender_document_id"] == str(pcap.id))
    assert (suggestion["role"], suggestion["confidence"]) == ("pcap", "high")
    assert [g["code"] for g in body["gaps"]] == ["referenced_document_missing"]
    assert body["gaps"][0]["would_provide"] == []
    roles = {r["tender_document_id"]: r for r in body["effective_roles"]}
    assert roles[str(pcap.id)]["source"] == "global_classifier"
    assert body["reviews"] == []

    review_url = f"{base}/documents/{pcap.id}/role-review"
    sha = suggestion["content_sha256"]
    # Sense motiu: no.
    response = await op_client.post(
        review_url, json={"role": "characteristics_table", "content_sha256": sha}
    )
    assert response.status_code == 422
    # D'una versio que ja no es la vigent: 409.
    response = await op_client.post(
        review_url,
        json={"role": "characteristics_table", "reason": "Quadre", "content_sha256": "f" * 64},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "document_version_changed"

    response = await op_client.post(
        review_url,
        json={
            "role": "characteristics_table",
            "reason": "La primera pagina es el quadre.",
            "content_sha256": sha,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    (review,) = body["reviews"]
    assert review["actor_id"] == str(operator.id)
    assert review["authority"] == "platform_operator"
    assert review["based_on_run_id"] == body["run"]["id"]
    assert review["based_on_suggestion_id"] == suggestion["id"]
    assert review["content_sha256"] == sha
    # El que va proposar el classificador es conserva, a part.
    kept = next(s for s in body["suggestions"] if s["tender_document_id"] == str(pcap.id))
    assert kept["role"] == "pcap"
    roles = {r["tender_document_id"]: r for r in body["effective_roles"]}
    assert (roles[str(pcap.id)]["source"], roles[str(pcap.id)]["role"]) == (
        "operator",
        "characteristics_table",
    )
    # La revisio deixa una revisio interna del tender (`document_intake`).
    from app.models.tender_lifecycle import TenderRevision

    latest = await db.scalar(
        sa.select(TenderRevision)
        .where(TenderRevision.tender_id == public_tender.id)
        .order_by(TenderRevision.revision_number.desc())
        .limit(1)
    )
    assert latest.trigger == "document_intake"
    await db.refresh(pcap)
    assert pcap.document_type == TenderDocumentType.ADMINISTRATIVE_TERMS


async def test_una_licitacio_manual_no_es_de_l_operador(db, op_client, tenant_a):
    _user, company = tenant_a
    from app.services.manual_tender import get_or_create_manual_source

    source = await get_or_create_manual_source(db)
    tender = Tender(
        source_id=source.id,
        external_id=f"man-{uuid.uuid4().hex[:6]}",
        title="Manual propia",
        owner_company_id=company.id,
    )
    db.add(tender)
    await db.commit()
    response = await op_client.get(f"{platform_url(tender.id)}/classification")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_not_public"


async def test_verificar_un_document_aportat_reclassifica_i_resol_el_forat(
    db, op_client, public_tender
):
    """Ganxo de la incorporacio: verificar el DOC-03 tanca el forat que el citava."""
    await public_package(db, public_tender)
    await global_classification.classify_tender_globally(
        db, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
    )
    (gap,) = await gaps_of(db, public_tender.id)
    assert gap.resolved_at is None

    response = await op_client.post(
        f"{platform_url(public_tender.id)}/documents/upload",
        files={"file": ("DOC-03 Quadre.pdf", QUADRE_PDF, "application/pdf")},
        data={"contribution_source_url": "https://contractaciopublica.cat/ca/detall/123"},
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["id"]
    # Pendent de verificar: encara no compta, el forat continua obert.
    (gap,) = await gaps_of(db, public_tender.id)
    assert gap.resolved_at is None

    response = await op_client.post(
        f"{platform_url(public_tender.id)}/documents/{document_id}/verify", json={}
    )
    assert response.status_code == 200, response.text

    run = await current_run(db, public_tender.id)
    assert run.trigger == GlobalClassificationTrigger.DOCUMENT_INTAKE
    (gap,) = await gaps_of(db, public_tender.id)
    assert gap.resolved_at is not None
    assert str(gap.resolved_by_document_id) == document_id


# --------------------------------------------------------------------------- #
# Fix round 1                                                                  #
# --------------------------------------------------------------------------- #


async def test_el_worker_processa_per_defecte_un_plec_mal_tipat_pel_feed(
    db, session_factory, public_tender
):
    """M2: tots els tipus (no alies). El classificador global en corregeix el tipus."""
    from app.services.document_roles import effective_document_roles

    ppt = await add_feed_document(db, public_tender, "Annex_2.pdf", TenderDocumentType.OTHER)
    async with _serving({ppt.url: PPT_PDF}) as client:
        await handlers.process_tender_documents(
            _job_context(session_factory, public_tender.id, client)
        )

    run = await current_run(db, public_tender.id)
    assert run is not None and run.document_count == 1
    roles = await effective_document_roles(db, public_tender.id, None)
    assert (roles[ppt.id].source, roles[ppt.id].document_type) == (
        RoleSource.GLOBAL_CLASSIFIER,
        TenderDocumentType.TECHNICAL_TERMS,
    )
    await db.refresh(ppt)
    assert ppt.document_type == TenderDocumentType.OTHER  # el feed, intacte


async def test_classify_safely_registra_la_fallada_sense_propagar_la(
    db, public_tender, monkeypatch, caplog
):
    async def boom(*_args, **_kwargs):
        raise RuntimeError("fallada de prova")

    monkeypatch.setattr(global_classification, "classify_tender_globally", boom)
    with caplog.at_level("ERROR", logger=global_classification.logger.name):
        result = await global_classification.classify_safely(
            db, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
        )
    assert result is None
    records = [r for r in caplog.records if r.getMessage() == "global_classification.failed"]
    assert len(records) == 1
    assert records[0].tender_id == str(public_tender.id)
    # La sessio continua usable despres del rollback.
    assert await run_count(db, public_tender.id) == 0


async def test_la_cursa_entre_worker_i_operador_no_dona_500(
    db, session_factory, op_client, public_tender, monkeypatch
):
    """M5: el worker desa la mateixa passada entremig; l'operador rep l'existent (200).

    Mentre l'operador llegeix els fets (despres de comprovar que no hi ha cap
    passada amb aquella empremta), una altra sessio classifica i fa commit. El
    flush de l'operador topa amb l'index unic i no pot acabar en un 500.
    """
    await public_package(db, public_tender)
    real_facts = global_classification._facts
    state = {"raced": False}

    async def facts_with_race(session, position, item):
        if not state["raced"]:
            state["raced"] = True
            async with session_factory() as worker:
                await global_classification.classify_tender_globally(
                    worker, public_tender.id, GlobalClassificationTrigger.DOCUMENT_PROCESSING
                )
        return await real_facts(session, position, item)

    monkeypatch.setattr(global_classification, "_facts", facts_with_race)

    response = await op_client.post(f"{platform_url(public_tender.id)}/classification/run")
    assert response.status_code == 200, response.text
    assert state["raced"] is True
    assert await run_count(db, public_tender.id) == 1
    run = await current_run(db, public_tender.id)
    assert response.json()["run"]["id"] == str(run.id)
    assert run.trigger == GlobalClassificationTrigger.DOCUMENT_PROCESSING
