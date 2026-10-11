"""Router d'operador `/platform/tenders/{id}/documents` (3B-2 §2.2, §2.4, §2.6).

L'operador de plataforma (superusuari sense empresa) incorpora documents
publics oficials que el feed no enumera: per URL d'un host oficial, descarregat
pel sistema, o pujant-lo quan la URL no es descarregable. Tot entra
`pending_verification` i nomes la verificacio humana el fa analitzable.

Els limits que es protegeixen:

* Nomes `PlatformAdmin`. Una empresa (i un superusuari amb empresa) rep 403;
  cap endpoint tenant escriu documents en un tender public (`tender_not_manual`)
  i no hi ha cap workflow de propostes.
* Allowlist `DOCUMENT_OFFICIAL_HOSTS` (sufix de domini) a la URL i a cada salt.
* Un `pending_verification` no es analitzable i la seva arribada no mou cap
  empremta; `DOCUMENT_ADDED` (requirements_affecting) arriba nomes en verificar.
* Retirar/restaurar amb actor i motiu; mai esborrat fisic.
* Cap test toca la xarxa: la descarrega va per un transport fals.
"""

from __future__ import annotations

import hashlib
import uuid

import httpx
import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import (
    AuditAction,
    DocumentContributionStatus,
    DocumentFileOrigin,
    SourceType,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.sources import Source
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
)
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Tender, TenderDocument
from app.services.requirement_analysis import collect_analyzable_documents
from app.services.requirement_chunking import compute_input_fingerprint
from app.services.tender_document_processing import process_tender_document
from app.services.tender_lifecycle import DOCUMENT_INTAKE_TRIGGER, record_revision
from app.tenders.changes import ChangeCode, ChangeImpact
from app.tenders.documents import analyzable_documents
from tests.conftest import create_user_with_company, login
from tests.fixtures.pdf_builder import build_pdf
from tests.test_platform_onboarding import create_operator

pytestmark = pytest.mark.asyncio

PUBLIC_IP = "93.184.216.34"
PCAP = build_pdf(["Plec de clausules administratives particulars. " * 8] * 3)
PPT = build_pdf(["Plec de prescripcions tecniques del servei. " * 8] * 2)
ANNEX = build_pdf(["Annex: model de declaracio responsable. " * 8] * 2)
HTML = b"<!doctype html><html><body>Perfil del contractant</body></html>"

OFFICIAL = "https://contractaciopublica.cat/ecofin_pscp/AppJava/download/annex1.pdf"
OFFICIAL_PPT = "https://www.contractaciopublica.cat/docs/ppt.pdf"
SOURCE_PAGE = "https://contractaciopublica.cat/ca/detall-publicacio/12345"


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


public_resolver = resolver


class FakeOrigin:
    """Servidor fals: URL -> (status, bytes, content-type, location)."""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, bytes, str, str | None]] = {}
        self.calls: list[str] = []

    def serve(self, url: str, payload: bytes, content_type: str = "application/pdf") -> None:
        self.routes[url] = (200, payload, content_type, None)

    def redirect(self, url: str, location: str) -> None:
        self.routes[url] = (302, b"", "text/plain", location)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        status, payload, content_type, location = self.routes.get(
            url, (404, b"", "text/plain", None)
        )
        headers = {"content-type": content_type}
        if location:
            headers["location"] = location
        return httpx.Response(status, content=payload, headers=headers)


@pytest.fixture
def origin(monkeypatch) -> FakeOrigin:
    fake = FakeOrigin()
    from app.services import public_document_intake as intake

    original = intake.download_document

    async def patched(url, *, client=None, resolver=None, allowed_hosts=None):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handler), follow_redirects=False
        ) as fake_client:
            return await original(
                url, client=fake_client, resolver=public_resolver, allowed_hosts=allowed_hosts
            )

    monkeypatch.setattr(intake, "download_document", patched)
    return fake


@pytest.fixture
async def operator(db):
    return await create_operator(db)


@pytest.fixture
def operator_id(operator) -> uuid.UUID:
    """L'id, capturat un cop (les lectures del test no depenen de l.objecte carregat)."""
    return operator.id


@pytest.fixture
async def op_client(client_factory, operator):
    http_client = client_factory()
    await login(http_client, operator.email)
    return http_client


@pytest.fixture
async def public_tender(db) -> Tender:
    """Tender public (font sindicada, sense propietari) amb una revisio de base."""
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


async def feed_document(db, tender: Tender, *, payload: bytes = PCAP) -> TenderDocument:
    """Un PCAP del feed, ja processat (bytes + extraccio)."""
    document = TenderDocument(
        tender_id=tender.id,
        name="PCAP.pdf",
        document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
        url=f"https://contrataciondelestado.es/feed/{uuid.uuid4().hex[:6]}/pcap.pdf",
        origin=TenderDocumentOrigin.FEED,
        governing_source_id=tender.source_id,
    )
    db.add(document)
    await db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=False
    ) as client:
        await process_tender_document(db, document, client=client, resolver=resolver)
    await db.refresh(document)
    return document


def docs_url(tender_id) -> str:
    return f"/api/v1/platform/tenders/{tender_id}/documents"


async def add_by_url(op_client, tender_id, url=OFFICIAL, **extra) -> httpx.Response:
    body = {"url": url, "source_page_url": SOURCE_PAGE, **extra}
    return await op_client.post(f"{docs_url(tender_id)}/by-url", json=body)


async def latest_revision(db, tender_id) -> TenderRevision:
    return await db.scalar(
        sa.select(TenderRevision)
        .where(TenderRevision.tender_id == tender_id)
        .order_by(TenderRevision.revision_number.desc())
        .limit(1)
    )


async def events_of(db, revision: TenderRevision) -> list[TenderChangeEvent]:
    return list(
        (
            await db.scalars(
                sa.select(TenderChangeEvent).where(TenderChangeEvent.to_revision_id == revision.id)
            )
        ).all()
    )


async def reload(db, document_id) -> TenderDocument:
    """Llegeix el document tal com l'ha deixat l'API (una altra sessio)."""
    return await db.get(TenderDocument, document_id, populate_existing=True)


async def fingerprint(db, tender_id) -> str:
    documents, _skipped = await collect_analyzable_documents(db, tender_id)
    return compute_input_fingerprint(documents)


# --------------------------------------------------------------------------- #
# Qui hi entra                                                                  #
# --------------------------------------------------------------------------- #


async def test_una_empresa_no_hi_entra(client_a, public_tender, origin):
    origin.serve(OFFICIAL, ANNEX)
    tender_id = public_tender.id
    document_id = uuid.uuid4()
    for method, path, kwargs in (
        ("post", f"{docs_url(tender_id)}/by-url", {"json": {"url": OFFICIAL}}),
        ("get", docs_url(tender_id), {}),
        ("post", f"{docs_url(tender_id)}/{document_id}/verify", {"json": {}}),
        ("post", f"{docs_url(tender_id)}/{document_id}/reject", {"json": {"reason": "x"}}),
        ("post", f"{docs_url(tender_id)}/{document_id}/retire", {"json": {"reason": "x"}}),
        ("post", f"{docs_url(tender_id)}/{document_id}/restore", {"json": {}}),
        ("post", f"{docs_url(tender_id)}/{document_id}/hand-over-to-feed", {"json": {}}),
    ):
        response = await getattr(client_a, method)(path, **kwargs)
        assert response.status_code == 403, (method, path, response.text)
    assert origin.calls == []


async def test_un_superusuari_amb_empresa_tampoc(db, client_factory, public_tender, origin):
    user, _company = await create_user_with_company(
        db, email=f"su-{uuid.uuid4().hex[:6]}@empresa.com", company_name="Empresa SU"
    )
    user.is_superuser = True
    await db.commit()
    http_client = client_factory()
    await login(http_client, user.email)

    response = await add_by_url(http_client, public_tender.id)
    assert response.status_code == 403
    assert (await http_client.get(docs_url(public_tender.id))).status_code == 403


async def test_sense_sessio_401(client, public_tender):
    assert (await client.get(docs_url(public_tender.id))).status_code == 401


# --------------------------------------------------------------------------- #
# Afegir per URL                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/annex.pdf",
        "https://contractaciopublica.cat.evil.example/annex.pdf",
        "https://evilcontractaciopublica.cat/annex.pdf",
    ],
)
async def test_host_fora_de_l_allowlist_422(db, op_client, public_tender, origin, url):
    origin.serve(url, ANNEX)

    response = await add_by_url(op_client, public_tender.id, url=url)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "host_not_official"
    assert origin.calls == [], "no s'ha de demanar res a un host no oficial"
    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TenderDocument)
        .where(TenderDocument.tender_id == public_tender.id)
    )
    assert count == 0


async def test_una_redireccio_cap_a_un_host_no_oficial_tambe_es_refusa(
    op_client, public_tender, origin
):
    origin.redirect(OFFICIAL, "https://example.com/annex.pdf")
    origin.serve("https://example.com/annex.pdf", ANNEX)

    response = await add_by_url(op_client, public_tender.id)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "host_not_official"
    assert origin.calls == [OFFICIAL]


async def test_una_url_que_serveix_html_es_invalid_content(db, op_client, public_tender, origin):
    origin.serve(OFFICIAL, HTML, content_type="text/html")

    response = await add_by_url(op_client, public_tender.id)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_content"
    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TenderDocument)
        .where(TenderDocument.tender_id == public_tender.id)
    )
    assert count == 0


async def test_afegir_per_url_crea_un_document_pendent_amb_provenance(
    db, op_client, operator_id, public_tender, origin
):
    origin.serve(OFFICIAL, ANNEX)

    response = await add_by_url(
        op_client,
        public_tender.id,
        name="Annex 1 - Declaracio responsable",
        document_type="additional",
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin"] == "operator_url"
    assert body["contribution_status"] == "pending_verification"
    assert body["governing_source_id"] is None
    assert body["contributed_by"] == str(operator_id)
    assert body["contribution_source_url"] == SOURCE_PAGE
    assert body["url"] == OFFICIAL
    assert body["name"] == "Annex 1 - Declaracio responsable"
    assert body["document_type"] == "additional"
    assert body["latest_file"]["sha256"] == hashlib.sha256(ANNEX).hexdigest()

    document = await reload(db, uuid.UUID(body["id"]))
    assert document.origin == TenderDocumentOrigin.OPERATOR_URL
    assert document.contribution_status == DocumentContributionStatus.PENDING_VERIFICATION
    assert document.contributed_at is not None
    assert document.verified_at is None

    file = await db.scalar(
        sa.select(TenderDocumentFile).where(TenderDocumentFile.tender_document_id == document.id)
    )
    assert file.origin == DocumentFileOrigin.FETCHED
    assert file.source_url == OFFICIAL
    assert file.final_url == OFFICIAL
    assert file.http_status == 200
    assert file.downloaded_at is not None
    assert file.uploaded_by == operator_id
    assert file.is_current is True
    content = await db.get(TenderDocumentContent, file.content_id)
    assert content.sha256 == hashlib.sha256(ANNEX).hexdigest()
    extraction = await db.scalar(
        sa.select(TenderDocumentExtraction).where(
            TenderDocumentExtraction.content_id == content.id,
            TenderDocumentExtraction.is_current.is_(True),
        )
    )
    assert extraction is not None

    revision = await latest_revision(db, public_tender.id)
    assert revision.revision_number == 2
    assert revision.trigger == DOCUMENT_INTAKE_TRIGGER
    codes = {event.change_code for event in await events_of(db, revision)}
    assert ChangeCode.DOCUMENT_ADDED.value not in codes
    assert codes == {ChangeCode.DOCUMENT_CONTRIBUTION_CHANGED.value}
    assert all(not event.is_material for event in await events_of(db, revision))

    audit = (
        await db.scalars(
            sa.select(AuditLog).where(
                AuditLog.entity_type == "tender_document", AuditLog.entity_id == document.id
            )
        )
    ).all()
    assert [(row.action, row.company_id, row.user_id) for row in audit] == [
        (AuditAction.CREATE, None, operator_id)
    ]


async def test_la_mateixa_url_dues_vegades_es_409(op_client, public_tender, origin):
    origin.serve(OFFICIAL, ANNEX)
    assert (await add_by_url(op_client, public_tender.id)).status_code == 201

    again = await add_by_url(op_client, public_tender.id)

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "document_url_exists"


async def test_un_tender_que_no_existeix_es_404(op_client, origin):
    response = await add_by_url(op_client, uuid.uuid4())
    assert response.status_code == 404
    assert origin.calls == []


async def test_un_tender_manual_no_es_public(db, op_client, origin, tenant_a):
    from app.services.manual_tender import get_or_create_manual_source

    source = await get_or_create_manual_source(db)
    tender = Tender(
        source_id=source.id,
        external_id=f"manual-{uuid.uuid4().hex[:6]}",
        title="Licitacio propia",
        owner_company_id=tenant_a[1].id,
    )
    db.add(tender)
    await db.commit()
    origin.serve(OFFICIAL, ANNEX)

    response = await add_by_url(op_client, tender.id)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "tender_not_public"
    assert origin.calls == []


async def test_els_mateixos_bytes_que_un_del_feed_queden_com_a_alies(
    db, op_client, public_tender, origin
):
    feed = await feed_document(db, public_tender, payload=PCAP)
    origin.serve(OFFICIAL, PCAP)

    response = await add_by_url(op_client, public_tender.id)

    assert response.status_code == 201, response.text
    assert response.json()["duplicate_of_document_id"] == str(feed.id)
    listing = (await op_client.get(docs_url(public_tender.id))).json()
    by_id = {item["id"]: item for item in listing}
    assert by_id[str(feed.id)]["duplicate_of_document_id"] is None


# --------------------------------------------------------------------------- #
# Pujada de l'operador                                                         #
# --------------------------------------------------------------------------- #


async def upload(op_client, tender_id, payload=ANNEX, *, data=None, content_type="application/pdf"):
    form = {"contribution_source_url": SOURCE_PAGE} if data is None else data
    return await op_client.post(
        f"{docs_url(tender_id)}/upload",
        files={"file": ("annex2.pdf", payload, content_type)},
        data=form,
    )


async def test_pujar_sense_pagina_d_origen_es_422(op_client, public_tender):
    response = await upload(op_client, public_tender.id, data={})
    assert response.status_code == 422


async def test_pujar_un_fals_pdf_es_422(op_client, public_tender):
    response = await upload(op_client, public_tender.id, payload=HTML)
    assert response.status_code == 422


async def test_pujar_crea_un_document_operator_upload_pendent(
    db, op_client, operator_id, public_tender
):
    response = await upload(
        op_client,
        public_tender.id,
        data={"contribution_source_url": SOURCE_PAGE, "document_type": "technical_terms"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin"] == "operator_upload"
    assert body["contribution_status"] == "pending_verification"
    assert body["url"].startswith("operator-upload://")
    assert body["contribution_source_url"] == SOURCE_PAGE
    assert body["document_type"] == "technical_terms"
    assert body["name"] == "annex2.pdf"

    file = await db.scalar(
        sa.select(TenderDocumentFile).where(
            TenderDocumentFile.tender_document_id == uuid.UUID(body["id"])
        )
    )
    assert file.origin == DocumentFileOrigin.UPLOADED
    assert file.source_url is None
    assert file.uploaded_by == operator_id
    revision = await latest_revision(db, public_tender.id)
    assert revision.trigger == DOCUMENT_INTAKE_TRIGGER
    audit = await db.scalar(
        sa.select(AuditLog).where(AuditLog.entity_id == file.tender_document_id)
    )
    assert (audit.action, audit.company_id) == (AuditAction.CREATE, None)


# --------------------------------------------------------------------------- #
# Verificar, rebutjar, retirar i restaurar                                     #
# --------------------------------------------------------------------------- #


async def test_un_pendent_no_es_analitzable_i_no_mou_cap_empremta(
    db, op_client, public_tender, origin
):
    """Review focus 3: la seva arribada no canvia res; la verificacio si."""
    await feed_document(db, public_tender, payload=PCAP)
    before = await fingerprint(db, public_tender.id)
    origin.serve(OFFICIAL_PPT, PPT)

    added = await add_by_url(
        op_client, public_tender.id, url=OFFICIAL_PPT, document_type="technical_terms"
    )
    assert added.status_code == 201, added.text
    document_id = uuid.UUID(added.json()["id"])

    assert document_id not in {d.id for d in await analyzable_documents(db, public_tender.id)}
    assert await fingerprint(db, public_tender.id) == before

    verified = await op_client.post(f"{docs_url(public_tender.id)}/{document_id}/verify", json={})
    assert verified.status_code == 200, verified.text

    assert document_id in {d.id for d in await analyzable_documents(db, public_tender.id)}
    assert await fingerprint(db, public_tender.id) != before


async def test_verificar_porta_document_added_requirements_affecting(
    db, op_client, operator_id, public_tender, origin
):
    origin.serve(OFFICIAL, ANNEX)
    document_id = (await add_by_url(op_client, public_tender.id)).json()["id"]
    pending_revision = await latest_revision(db, public_tender.id)
    pending_codes = {e.change_code for e in await events_of(db, pending_revision)}
    assert ChangeCode.DOCUMENT_ADDED.value not in pending_codes

    response = await op_client.post(
        f"{docs_url(public_tender.id)}/{document_id}/verify",
        json={"note": "Comprovat al perfil del contractant"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contribution_status"] == "verified"
    assert body["verified_by"] == str(operator_id)
    assert body["verified_at"] is not None
    assert body["verification_note"] == "Comprovat al perfil del contractant"

    revision = await latest_revision(db, public_tender.id)
    assert revision.revision_number == pending_revision.revision_number + 1
    assert revision.trigger == DOCUMENT_INTAKE_TRIGGER
    added = [
        e for e in await events_of(db, revision) if e.change_code == ChangeCode.DOCUMENT_ADDED.value
    ]
    assert len(added) == 1
    assert added[0].subject == OFFICIAL
    assert ChangeImpact.REQUIREMENTS_AFFECTING.value in added[0].impacts
    assert added[0].is_material is True

    actions = (
        await db.scalars(
            sa.select(AuditLog.action)
            .where(AuditLog.entity_id == uuid.UUID(document_id))
            .order_by(AuditLog.created_at)
        )
    ).all()
    assert AuditAction.VERIFY in actions

    again = await op_client.post(f"{docs_url(public_tender.id)}/{document_id}/verify", json={})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "document_already_verified"


async def test_rebutjar_demana_motiu_i_no_es_analitzable(db, op_client, public_tender, origin):
    origin.serve(OFFICIAL, ANNEX)
    document_id = (await add_by_url(op_client, public_tender.id)).json()["id"]
    url = f"{docs_url(public_tender.id)}/{document_id}/reject"

    assert (await op_client.post(url, json={})).status_code == 422
    assert (await op_client.post(url, json={"reason": "  "})).status_code == 422

    response = await op_client.post(url, json={"reason": "No es el document publicat"})

    assert response.status_code == 200, response.text
    assert response.json()["contribution_status"] == "rejected"
    assert response.json()["verification_note"] == "No es el document publicat"
    assert uuid.UUID(document_id) not in {
        d.id for d in await analyzable_documents(db, public_tender.id)
    }
    revision = await latest_revision(db, public_tender.id)
    assert all(not e.is_material for e in await events_of(db, revision))


async def test_retirar_i_restaurar_amb_actor_i_motiu(
    db, op_client, operator_id, public_tender, origin
):
    origin.serve(OFFICIAL, ANNEX)
    document_id = (await add_by_url(op_client, public_tender.id)).json()["id"]
    base = f"{docs_url(public_tender.id)}/{document_id}"
    assert (await op_client.post(f"{base}/verify", json={})).status_code == 200

    assert (await op_client.post(f"{base}/retire", json={})).status_code == 422

    retired = await op_client.post(f"{base}/retire", json={"reason": "Substituit per l'esmena"})

    assert retired.status_code == 200, retired.text
    body = retired.json()
    assert body["removed_at"] is not None
    assert body["retired_by"] == str(operator_id)
    assert body["retired_reason"] == "Substituit per l'esmena"
    revision = await latest_revision(db, public_tender.id)
    assert revision.trigger == DOCUMENT_INTAKE_TRIGGER
    assert ChangeCode.DOCUMENT_REMOVED.value in {
        e.change_code for e in await events_of(db, revision)
    }
    # Mai esborrat fisic: el document i els seus fitxers hi continuen.
    document = await reload(db, uuid.UUID(document_id))
    assert document is not None
    assert (
        await db.scalar(
            sa.select(sa.func.count())
            .select_from(TenderDocumentFile)
            .where(TenderDocumentFile.tender_document_id == document.id)
        )
        == 1
    )
    listing = (await op_client.get(docs_url(public_tender.id))).json()
    assert [item["id"] for item in listing] == [document_id]

    twice = await op_client.post(f"{base}/retire", json={"reason": "x"})
    assert twice.status_code == 409

    restored = await op_client.post(f"{base}/restore", json={})

    assert restored.status_code == 200, restored.text
    body = restored.json()
    assert body["removed_at"] is None
    assert body["retired_by"] is None
    assert body["retired_reason"] is None
    assert body["contribution_status"] == "verified"
    revision = await latest_revision(db, public_tender.id)
    assert ChangeCode.DOCUMENT_ADDED.value in {e.change_code for e in await events_of(db, revision)}

    actions = (
        await db.scalars(
            sa.select(AuditLog.action).where(AuditLog.entity_id == uuid.UUID(document_id))
        )
    ).all()
    assert {AuditAction.CREATE, AuditAction.VERIFY, AuditAction.RETIRE, AuditAction.RESTORE} <= set(
        actions
    )
    rows = (await db.scalars(sa.select(AuditLog).where(AuditLog.entity_id == document.id))).all()
    assert all(row.company_id is None and row.user_id == operator_id for row in rows)


async def test_retirar_el_canonic_promou_l_alies(db, op_client, public_tender, origin):
    """Mateixos bytes: operator_url (canonic) i operator_upload (alies, menys prioritat)."""
    origin.serve(OFFICIAL, ANNEX)
    first = (await add_by_url(op_client, public_tender.id)).json()["id"]
    second = (await upload(op_client, public_tender.id, ANNEX)).json()
    assert second["duplicate_of_document_id"] == first

    retired = await op_client.post(
        f"{docs_url(public_tender.id)}/{first}/retire", json={"reason": "Duplicat"}
    )
    assert retired.status_code == 200

    promoted = await reload(db, uuid.UUID(second["id"]))
    assert promoted.duplicate_of_document_id is None

    restored = await op_client.post(f"{docs_url(public_tender.id)}/{first}/restore", json={})
    assert restored.status_code == 200
    # Reagrupat: el mes antic torna a ser el canonic.
    assert restored.json()["duplicate_of_document_id"] is None
    again = await reload(db, uuid.UUID(second["id"]))
    assert again.duplicate_of_document_id == uuid.UUID(first)


async def test_cap_accio_d_operador_sobre_un_document_del_feed(db, op_client, public_tender):
    feed = await feed_document(db, public_tender)
    base = f"{docs_url(public_tender.id)}/{feed.id}"
    for action, body in (
        ("verify", {}),
        ("reject", {"reason": "x"}),
        ("retire", {"reason": "x"}),
        ("restore", {}),
    ):
        response = await op_client.post(f"{base}/{action}", json=body)
        assert response.status_code == 409, (action, response.text)
        assert response.json()["error"]["code"] == "document_not_operator_managed"
    document = await reload(db, feed.id)
    assert document.removed_at is None
    assert document.contribution_status == DocumentContributionStatus.NOT_APPLICABLE


async def test_un_document_d_un_altre_tender_es_404(db, op_client, public_tender, origin):
    origin.serve(OFFICIAL, ANNEX)
    document_id = (await add_by_url(op_client, public_tender.id)).json()["id"]
    response = await op_client.post(f"{docs_url(uuid.uuid4())}/{document_id}/verify", json={})
    assert response.status_code == 404


async def test_el_llistat_inclou_els_retirats_i_els_camps_de_provenance(
    db, op_client, public_tender, origin
):
    feed = await feed_document(db, public_tender)
    origin.serve(OFFICIAL, ANNEX)
    document_id = (await add_by_url(op_client, public_tender.id)).json()["id"]
    await op_client.post(
        f"{docs_url(public_tender.id)}/{document_id}/retire", json={"reason": "Error"}
    )

    response = await op_client.get(docs_url(public_tender.id))

    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()}
    assert set(by_id) == {str(feed.id), document_id}
    assert by_id[str(feed.id)]["origin"] == "feed"
    assert by_id[str(feed.id)]["governing_source_id"] == str(public_tender.source_id)
    assert by_id[str(feed.id)]["contribution_status"] == "not_applicable"
    operator_doc = by_id[document_id]
    assert operator_doc["origin"] == "operator_url"
    assert operator_doc["removed_at"] is not None
    assert operator_doc["retired_reason"] == "Error"
    assert operator_doc["latest_file"]["sha256"] == hashlib.sha256(ANNEX).hexdigest()


# --------------------------------------------------------------------------- #
# Cedir al feed                                                                #
# --------------------------------------------------------------------------- #


FEED_ENTRY = """<entry xmlns="http://www.w3.org/2005/Atom"
 xmlns:cac-place-ext="urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2"
 xmlns:cac="urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2"
 xmlns:cbc="urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2">
 <id>https://contrataciondelestado.es/sindicacion/licitacionesPerfilContratante/{external}</id>
 <updated>2026-10-01T10:00:00+02:00</updated>
 <title>Servei de manteniment de la xarxa de fibra</title>
 <cac-place-ext:ContractFolderStatus>
  <cbc:ContractFolderID>EXP-1</cbc:ContractFolderID>
  {references}
 </cac-place-ext:ContractFolderStatus>
</entry>"""

FEED_REFERENCE = """<cac:AdditionalDocumentReference>
   <cbc:ID>doc</cbc:ID>
   <cac:Attachment><cac:ExternalReference><cbc:URI>{url}</cbc:URI></cac:ExternalReference>
   </cac:Attachment>
  </cac:AdditionalDocumentReference>"""


async def publish_in_feed(db, tender: Tender, urls: list[str]) -> uuid.UUID:
    """Darrera entrada de la font per al tender, amb el raw a l'storage, que publica `urls`."""
    from app.models.sources import SourceRecord
    from app.services.storage import get_storage

    raw = FEED_ENTRY.format(
        external=tender.external_id,
        references="\n  ".join(FEED_REFERENCE.format(url=url) for url in urls),
    ).encode("utf-8")
    key = f"raw/test/{uuid.uuid4()}.xml"
    await get_storage().upload(key, raw, "application/xml")
    record = SourceRecord(
        source_id=tender.source_id,
        external_id=tender.external_id,
        raw_hash=hashlib.sha256(raw).hexdigest(),
        raw_storage_key=key,
        raw_size=len(raw),
        parser_version="test",
        tender_id=tender.id,
    )
    db.add(record)
    await db.flush()
    tender.latest_source_record_id = record.id
    await db.commit()
    return record.id


def hand_over_url(tender_id, document_id) -> str:
    return f"{docs_url(tender_id)}/{document_id}/hand-over-to-feed"


async def test_cedir_al_feed_un_pendent_o_un_rebutjat_es_409(db, op_client, public_tender, origin):
    """I1: cedir no pot saltar-se la verificacio, ni rescatar un rebutjat."""
    tender_id = public_tender.id
    await publish_in_feed(db, public_tender, [OFFICIAL])
    origin.serve(OFFICIAL, ANNEX)
    document_id = (await add_by_url(op_client, tender_id)).json()["id"]

    pending = await op_client.post(hand_over_url(tender_id, document_id), json={"reason": "x"})
    assert pending.status_code == 409, pending.text
    assert pending.json()["error"]["code"] == "document_not_verified"

    await op_client.post(f"{docs_url(tender_id)}/{document_id}/reject", json={"reason": "No"})
    rejected = await op_client.post(hand_over_url(tender_id, document_id), json={"reason": "x"})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "document_not_verified"

    document = await reload(db, uuid.UUID(document_id))
    assert document.origin == TenderDocumentOrigin.OPERATOR_URL


async def test_cedir_al_feed_sense_prova_que_el_feed_la_publiqui_es_409(
    db, op_client, public_tender, origin
):
    """I1: la prova es la darrera entrada de la font, no la paraula de l'operador."""
    tender_id = public_tender.id
    origin.serve(OFFICIAL, ANNEX)
    document_id = (await add_by_url(op_client, tender_id)).json()["id"]
    await op_client.post(f"{docs_url(tender_id)}/{document_id}/verify", json={})

    # Sense cap entrada de la font.
    no_record = await op_client.post(hand_over_url(tender_id, document_id), json={"reason": "x"})
    assert no_record.status_code == 409
    assert no_record.json()["error"]["code"] == "feed_does_not_publish_url"

    # L'entrada existeix pero publica una altra URL.
    await publish_in_feed(db, public_tender, ["https://contrataciondelestado.es/altre.pdf"])
    other = await op_client.post(hand_over_url(tender_id, document_id), json={"reason": "x"})
    assert other.status_code == 409
    assert other.json()["error"]["code"] == "feed_does_not_publish_url"

    document = await reload(db, uuid.UUID(document_id))
    assert document.origin == TenderDocumentOrigin.OPERATOR_URL
    assert document.contribution_status == DocumentContributionStatus.VERIFIED


async def test_cedir_al_feed_amb_el_raw_absent_de_l_storage_es_409(
    db, op_client, public_tender, origin
):
    """N1: sense raw llegible no hi ha prova; falla tancat amb 409, mai amb 502."""
    from app.models.sources import SourceRecord

    tender_id = public_tender.id
    origin.serve(OFFICIAL, ANNEX)
    document_id = (await add_by_url(op_client, tender_id)).json()["id"]
    await op_client.post(f"{docs_url(tender_id)}/{document_id}/verify", json={})
    record_id = await publish_in_feed(db, public_tender, [OFFICIAL])
    record = await db.get(SourceRecord, record_id)
    record.raw_storage_key = f"raw/test/no-existeix-{uuid.uuid4().hex}.xml"
    await db.commit()

    response = await op_client.post(hand_over_url(tender_id, document_id), json={"reason": "x"})

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "feed_does_not_publish_url"
    document = await reload(db, uuid.UUID(document_id))
    assert document.origin == TenderDocumentOrigin.OPERATOR_URL
    assert document.governing_source_id is None
    assert document.contribution_status == DocumentContributionStatus.VERIFIED


async def test_cedir_al_feed_canvia_el_govern_i_conserva_els_bytes(
    db, op_client, operator_id, public_tender, origin
):
    tender_id, source_id = public_tender.id, public_tender.source_id
    origin.serve(OFFICIAL, ANNEX)
    document_id = uuid.UUID((await add_by_url(op_client, tender_id)).json()["id"])
    await op_client.post(f"{docs_url(tender_id)}/{document_id}/verify", json={})
    record_id = await publish_in_feed(
        db, public_tender, ["https://contrataciondelestado.es/pcap.pdf", OFFICIAL]
    )
    file_before = await db.scalar(
        sa.select(TenderDocumentFile).where(TenderDocumentFile.tender_document_id == document_id)
    )
    revisions_before = (await latest_revision(db, tender_id)).revision_number
    reason = "El feed ja publica aquesta URL. " + "x" * 400

    response = await op_client.post(
        hand_over_url(tender_id, document_id), json={"reason": reason[:300]}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["origin"] == "feed"
    assert body["governing_source_id"] == str(source_id)
    assert body["contribution_status"] == "not_applicable"
    assert body["verified_at"] is None
    assert body["verified_by"] is None
    # I2: la provenance de l'aportacio es queda a la fila.
    assert body["contributed_by"] == str(operator_id)
    assert body["contributed_at"] is not None
    assert body["contribution_source_url"] == SOURCE_PAGE
    assert "El feed ja publica aquesta URL" in body["verification_note"]
    # La revisio no es perd: la fotografia d'un verificat no canvia, aixi que no n'hi ha de nova.
    assert (await latest_revision(db, tender_id)).revision_number == revisions_before

    files = (
        await db.scalars(
            sa.select(TenderDocumentFile).where(
                TenderDocumentFile.tender_document_id == document_id
            )
        )
    ).all()
    assert [f.id for f in files] == [file_before.id]
    assert document_id in {d.id for d in await analyzable_documents(db, public_tender.id)}
    audit = (
        await db.scalars(
            sa.select(AuditLog).where(
                AuditLog.entity_id == document_id, AuditLog.action == AuditAction.UPDATE
            )
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].company_id is None
    summary = audit[0].summary
    # I2: el resum cap sencer (<= 500) i els fets essencials hi son, el motiu al final.
    assert len(summary) <= 500
    assert summary.endswith("x"), "el motiu va retallat al final, no s'hi perd res mes"
    assert str(record_id) in summary
    assert "operator_url" in summary
    assert str(operator_id) in summary

    # Ja no es de l'operador.
    again = await op_client.post(
        f"{docs_url(public_tender.id)}/{document_id}/retire", json={"reason": "x"}
    )
    assert again.status_code == 409


async def test_nomes_es_cedeix_un_operator_url(db, op_client, public_tender):
    feed = await feed_document(db, public_tender)
    uploaded = await upload(op_client, public_tender.id)
    body = {"reason": "x"}

    from_feed = await op_client.post(
        f"{docs_url(public_tender.id)}/{feed.id}/hand-over-to-feed", json=body
    )
    assert from_feed.status_code == 409
    assert from_feed.json()["error"]["code"] == "document_not_operator_managed"
    from_upload = await op_client.post(
        f"{docs_url(public_tender.id)}/{uploaded.json()['id']}/hand-over-to-feed", json=body
    )
    assert from_upload.status_code == 409
    assert from_upload.json()["error"]["code"] == "document_not_operator_url"


# --------------------------------------------------------------------------- #
# Els tenants no escriuen documents publics                                    #
# --------------------------------------------------------------------------- #


async def test_cap_endpoint_tenant_escriu_documents_en_un_tender_public(
    db, client_a, tenant_a, public_tender
):
    company_id = tenant_a[1].id
    feed = await feed_document(db, public_tender)
    scoped = f"/api/v1/companies/{company_id}/tenders/{public_tender.id}"

    created = await client_a.post(
        f"{scoped}/documents", files={"file": ("pcap.pdf", PCAP, "application/pdf")}
    )
    assert created.status_code == 422
    assert created.json()["error"]["code"] == "tender_not_manual"

    version = await client_a.post(
        f"{scoped}/documents/{feed.id}/versions",
        files={"file": ("pcap.pdf", PPT, "application/pdf")},
    )
    assert version.status_code == 422
    assert version.json()["error"]["code"] == "tender_not_manual"

    for action in ("verify", "reject", "retire", "restore", "hand-over-to-feed"):
        response = await client_a.post(f"{scoped}/documents/{feed.id}/{action}", json={})
        assert response.status_code in (404, 405), (action, response.status_code)

    for path in (
        f"{scoped}/document-proposals",
        f"/api/v1/companies/{company_id}/document-proposals",
        f"/api/v1/tenders/{public_tender.id}/document-proposals",
    ):
        assert (await client_a.get(path)).status_code == 404, path
        assert (await client_a.post(path, json={})).status_code == 404, path

    document = await reload(db, feed.id)
    assert document.removed_at is None
    count = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TenderDocument)
        .where(TenderDocument.tender_id == public_tender.id)
    )
    assert count == 1


def process_url(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/job-requests/process-documents"


@pytest.mark.parametrize("flag", ["force_download", "force_reextract"])
async def test_una_empresa_no_pot_forcar_el_processat_d_un_tender_public(
    db, client_a, tenant_a, public_tender, flag
):
    """I3 (spec §2.4, §2.6): forcar en un tender public es de l'operador."""
    await feed_document(db, public_tender)

    response = await client_a.post(process_url(tenant_a[1].id, public_tender.id), json={flag: True})

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "public_tender_canonical_only"
    # Sense forcar, el processat continua obert a l'empresa.
    plain = await client_a.post(process_url(tenant_a[1].id, public_tender.id), json={})
    assert plain.status_code in (200, 202), plain.text


async def test_en_un_tender_manual_propi_es_pot_forcar(db, client_a, tenant_a):
    from app.services.manual_tender import get_or_create_manual_source

    source = await get_or_create_manual_source(db)
    tender = Tender(
        source_id=source.id,
        external_id=f"manual-{uuid.uuid4().hex[:6]}",
        title="Licitacio propia",
        owner_company_id=tenant_a[1].id,
    )
    db.add(tender)
    await db.flush()
    db.add(TenderDocument(tender_id=tender.id, name="PCAP.pdf", url=f"upload://{uuid.uuid4()}"))
    await db.commit()

    for flag in ("force_download", "force_reextract"):
        response = await client_a.post(process_url(tenant_a[1].id, tender.id), json={flag: True})
        assert response.status_code in (200, 202), (flag, response.text)


# --------------------------------------------------------------------------- #
# Pagina d'origen (M2)                                                         #
# --------------------------------------------------------------------------- #


async def test_la_pagina_d_origen_no_admet_credencials(op_client, public_tender, origin):
    page = "https://usuari:secret@contractaciopublica.cat/perfil"
    origin.serve(OFFICIAL, ANNEX)

    by_url = await add_by_url(op_client, public_tender.id, source_page_url=page)
    uploaded = await upload(op_client, public_tender.id, data={"contribution_source_url": page})

    for response in (by_url, uploaded):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "invalid_page_url"
        assert "secret" not in response.text
    assert origin.calls == []


async def test_la_pagina_d_origen_ha_de_ser_oficial(op_client, public_tender, origin):
    page = "https://example.com/perfil-del-contractant"
    origin.serve(OFFICIAL, ANNEX)

    by_url = await add_by_url(op_client, public_tender.id, source_page_url=page)
    uploaded = await upload(op_client, public_tender.id, data={"contribution_source_url": page})

    for response in (by_url, uploaded):
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "host_not_official"
    assert origin.calls == []


# --------------------------------------------------------------------------- #
# Atomicitat (M3)                                                              #
# --------------------------------------------------------------------------- #


async def _rows(db, tender_id) -> dict[str, int]:
    async def count(query):
        return await db.scalar(sa.select(sa.func.count()).select_from(query.subquery()))

    documents = sa.select(TenderDocument.id).where(TenderDocument.tender_id == tender_id)
    return {
        "documents": await count(documents),
        "files": await count(
            sa.select(TenderDocumentFile.id).where(
                TenderDocumentFile.tender_document_id.in_(documents)
            )
        ),
        "contents": await count(
            sa.select(TenderDocumentContent.id).where(
                TenderDocumentContent.sha256 == hashlib.sha256(ANNEX).hexdigest()
            )
        ),
        "extractions": await count(sa.select(TenderDocumentExtraction.id)),
        "audit": await count(
            sa.select(AuditLog.id).where(AuditLog.entity_type == "tender_document")
        ),
        "revisions": await count(
            sa.select(TenderRevision.id).where(TenderRevision.tender_id == tender_id)
        ),
    }


@pytest.mark.parametrize("via", ["by-url", "upload"])
async def test_incorporar_es_atomic(db, op_client, public_tender, origin, monkeypatch, via):
    """Document, fitxer, contingut, extraccio, auditoria i revisio: tot o res."""
    from app.services import public_document_intake as intake

    tender_id = public_tender.id
    before = await _rows(db, tender_id)
    origin.serve(OFFICIAL, ANNEX)

    async def broken(*args, **kwargs):
        raise RuntimeError("revisio fallida")

    monkeypatch.setattr(intake, "record_revision", broken)

    with pytest.raises(RuntimeError):
        response = (
            await add_by_url(op_client, tender_id)
            if via == "by-url"
            else await upload(op_client, tender_id)
        )
        assert response.status_code >= 500

    assert await _rows(db, tender_id) == before


async def test_la_carrera_per_la_mateixa_url_es_document_url_exists(
    op_client, public_tender, origin, monkeypatch
):
    """La comprovacio previa es salta; la UNIQUE `(tender_id, url)` ho resol igual."""
    from app.services import public_document_intake as intake

    async def free(*args, **kwargs):
        return None

    monkeypatch.setattr(intake, "_assert_url_free", free)
    origin.serve(OFFICIAL, ANNEX)
    assert (await add_by_url(op_client, public_tender.id)).status_code == 201

    again = await add_by_url(op_client, public_tender.id)

    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "document_url_exists"


async def test_nomes_la_unique_de_la_url_es_document_url_exists():
    import sqlite3

    from sqlalchemy.exc import IntegrityError

    from app.services.public_document_intake import is_url_conflict

    class PgError(Exception):
        def __init__(self, constraint_name):
            super().__init__("violacio")
            self.constraint_name = constraint_name

    class AsyncpgWrapper(Exception):
        """Com l'adaptador d'asyncpg: l'error real a `__cause__`."""

    def wrapped(constraint_name):
        outer = AsyncpgWrapper("violacio")
        outer.__cause__ = PgError(constraint_name)
        return outer

    def integrity(orig):
        return IntegrityError("INSERT ...", {}, orig)

    assert is_url_conflict(integrity(wrapped("uq_tender_documents_tender_url")))
    assert is_url_conflict(integrity(PgError("uq_tender_documents_tender_url")))
    assert not is_url_conflict(integrity(wrapped("ck_tender_documents_origin_valid")))
    assert is_url_conflict(
        integrity(
            sqlite3.IntegrityError(
                "UNIQUE constraint failed: tender_documents.tender_id, tender_documents.url"
            )
        )
    )
    assert not is_url_conflict(
        integrity(sqlite3.IntegrityError("CHECK constraint failed: contribution_matches_origin"))
    )


# --------------------------------------------------------------------------- #
# Verificar un alies (M1, M6)                                                  #
# --------------------------------------------------------------------------- #


async def test_verificar_un_alies_d_un_document_del_feed(db, op_client, public_tender, origin):
    """Mateixos bytes que el PCAP del feed: continua sent alies i no mou l'empremta.

    La revisio porta igualment `DOCUMENT_ADDED` material: sobreinvalidacio acceptada
    (mateixa semantica que un alies que arriba pel feed, Task 2).
    """
    tender_id = public_tender.id
    feed = await feed_document(db, public_tender, payload=PCAP)
    feed_id = feed.id
    before = await fingerprint(db, tender_id)
    origin.serve(OFFICIAL, PCAP)
    added = await add_by_url(op_client, tender_id, document_type="administrative_terms")
    document_id = added.json()["id"]
    assert added.json()["duplicate_of_document_id"] == str(feed_id)

    verified = await op_client.post(f"{docs_url(tender_id)}/{document_id}/verify", json={})

    assert verified.status_code == 200, verified.text
    assert verified.json()["duplicate_of_document_id"] == str(feed_id)
    assert uuid.UUID(document_id) not in {d.id for d in await analyzable_documents(db, tender_id)}
    assert await fingerprint(db, tender_id) == before
    revision = await latest_revision(db, tender_id)
    added_events = [
        e for e in await events_of(db, revision) if e.change_code == ChangeCode.DOCUMENT_ADDED.value
    ]
    assert len(added_events) == 1 and added_events[0].is_material is True
