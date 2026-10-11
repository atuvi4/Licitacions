"""Frontera de parametres i seleccio canonica d'un tender public (3B-2 §6.4).

En una licitacio PUBLICA (`owner_company_id IS NULL`) un tenant no tria res del que
entra al run global: ni documents (`document_ids`), ni perfil (`profile`), ni
`force`. La quota i la peticio d'analisi ho refusen amb 422
`public_tender_canonical_only`; la seleccio es la canonica del servidor i el perfil
el de producte (`REQUIREMENTS_PRODUCT_PROFILE`). `force` nomes el pot fer servir
l'operador, i un run forcat d'un tender public mai es canonic.

Prova obligatoria A/B: dues empreses, mateix tender public, opcions diferents →
mateixa clau canonica, un sol run, cap parametre de cap de les dues al run ni al
job, B rep `REUSED_EXISTING`, punters per empresa independents.

El proveidor «real» es `RealNamedFake` (cap xarxa).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.ai.profiles import PROFILES
from app.ai.providers import fake
from app.core.config import settings
from app.core.errors import PublicTenderCanonicalOnlyError
from app.jobs import quotes as quote_service
from app.jobs import requests as job_requests
from app.jobs.params import analysis_dedupe_key, canonical_analysis_dedupe_key
from app.models.analysis_quotes import AnalysisQuote
from app.models.enums import (
    AnalysisRunStatus,
    JobOutcome,
    JobRequestStatus,
    RequirementRunBasis,
    WorkJobStatus,
    WorkJobType,
)
from app.models.jobs import JobRequest, WorkJob
from app.models.requirements import RequirementExtractionRun
from app.models.tenders import Tender
from app.services import review_runs
from app.services.requirement_analysis import (
    FORCED_NOT_CANONICAL,
    analysis_identity,
    analysis_input,
    analyze_tender_requirements,
)
from tests.jobs_support import run_worker
from tests.quota_support import clean_package, pin_real_model, requirement, set_quality
from tests.real_provider import RealNamedFake
from tests.test_jobs import _add_extracted_pages


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
async def public_plec(session, tender_document):
    """Tender public amb un PCAP llegit, QA bona i paquet complet."""
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    await set_quality(session, plec["extraction"], "good")
    await clean_package(session, tender_document.tender_id)
    return plec


def _quotes_url(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis-quotes"


async def _count(session, model) -> int:
    return int(await session.scalar(sa.select(sa.func.count()).select_from(model)) or 0)


# --------------------------------------------------------------------------- #
# Frontera a la quota i a la peticio                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "payload",
    [
        {"force": True},
        {"document_ids": ["__DOC__"]},
        {"profile": "v2"},
        # Fins i tot el perfil de producte: el tenant no tria perfil, i prou.
        {"profile": "v1"},
    ],
    ids=["force", "document_ids", "profile", "profile_de_producte"],
)
async def test_un_tender_public_refusa_els_parametres_del_tenant_a_la_quota(
    client_a, tenant_a, public_plec, session, payload
):
    document = public_plec["document"]
    body = {
        key: [str(document.id)] if value == ["__DOC__"] else value for key, value in payload.items()
    }

    response = await client_a.post(_quotes_url(tenant_a[1].id, document.tender_id), json=body)

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "public_tender_canonical_only"
    assert await _count(session, AnalysisQuote) == 0
    assert fake.SENT_PAYLOADS == []


async def test_un_tender_public_sense_opcions_fa_servir_la_seleccio_i_el_perfil_canonics(
    client_a, tenant_a, public_plec, session
):
    document = public_plec["document"]

    response = await client_a.post(
        _quotes_url(tenant_a[1].id, document.tender_id),
        json={"accept_incomplete_package": True, "acknowledge_quality": True},
    )

    assert response.status_code == 201, response.text
    quote = response.json()
    assert quote["profile"] == settings.REQUIREMENTS_PRODUCT_PROFILE == "v1"
    assert quote["force"] is False
    stored = await session.get(AnalysisQuote, uuid.UUID(quote["id"]))
    assert stored.document_ids == []
    assert stored.accept_incomplete_package is True
    assert quote["accept_incomplete_package"] is True


async def test_el_perfil_de_producte_ve_de_la_configuracio(
    client_a, tenant_a, public_plec, monkeypatch
):
    monkeypatch.setattr(settings, "REQUIREMENTS_PRODUCT_PROFILE", "v2")
    document = public_plec["document"]

    response = await client_a.post(_quotes_url(tenant_a[1].id, document.tender_id))

    assert response.status_code == 201, response.text
    assert response.json()["profile"] == "v2"


async def test_la_peticio_d_analisi_d_un_tender_public_tambe_refusa_force_i_seleccio(
    session, tenant_a, public_plec
):
    user, company = tenant_a
    tender_id = public_plec["document"].tender_id
    for options in (
        {"force": True},
        {"document_ids": [public_plec["document"].id]},
        {"profile": PROFILES["v2"]},
    ):
        with pytest.raises(PublicTenderCanonicalOnlyError):
            await job_requests.submit_analysis_request(
                session,
                company_id=company.id,
                user_id=user.id,
                tender_id=tender_id,
                provider_factory=RealNamedFake,
                **options,
            )
    assert await _count(session, WorkJob) == 0
    assert await _count(session, JobRequest) == 0


async def test_un_tender_manual_propi_accepta_force_seleccio_i_perfil_com_abans(
    client_a, tenant_a, tender_document, session
):
    user, company = tenant_a
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)
    tender.owner_company_id = company.id
    await session.commit()
    url = _quotes_url(company.id, tender.id)

    forced = await client_a.post(url, json={"force": True})
    selected = await client_a.post(url, json={"document_ids": [str(plec["document"].id)]})
    profiled = await client_a.post(url, json={"profile": "v2"})
    unknown = await client_a.post(url, json={"profile": "v9"})

    assert forced.status_code == 201, forced.text
    assert forced.json()["force"] is True
    assert selected.status_code == 201, selected.text
    stored = await session.get(AnalysisQuote, uuid.UUID(selected.json()["id"]))
    assert stored.document_ids == [str(plec["document"].id)]
    assert profiled.status_code == 201, profiled.text
    assert profiled.json()["profile"] == "v2"
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_error"


async def test_una_peticio_normal_d_un_tender_public_no_s_enganxa_a_una_feina_forcada(
    session, tenant_a, public_plec
):
    """Una feina `force` (d'una via antiga) d'un tender public mai dona el run canonic:
    una peticio normal no s'hi pot enganxar."""
    user, company = tenant_a
    tender_id = public_plec["document"].tender_id
    current = await analysis_input(session, tender_id)
    llm = fake.FakeProvider()
    force_key = analysis_dedupe_key(
        tender_id=tender_id,
        fingerprint=current.fingerprint,
        profile="v1",
        provider=llm.name,
        model=llm.model,
        document_ids=[],
        force=True,
    )
    session.add(
        WorkJob(
            job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
            status=WorkJobStatus.QUEUED,
            tender_id=tender_id,
            parameters={"profile": "v1", "force": True, "document_ids": []},
            dedupe_key=force_key,
            input_fingerprint=current.fingerprint,
            provider=llm.name,
            model=llm.model,
            attempts=0,
            max_attempts=1,
        )
    )
    await session.commit()

    submission = await job_requests.submit_analysis_request(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=tender_id,
        provider_factory=fake.FakeProvider,
    )

    job = await session.get(WorkJob, submission.request.work_job_id)
    assert job.dedupe_key.startswith(f"analyze:{tender_id}:canonical:")


# --------------------------------------------------------------------------- #
# Prova obligatoria A/B                                                        #
# --------------------------------------------------------------------------- #


async def test_a_b_mateix_tender_public_una_sola_feina_canonica_sense_parametres_de_tenant(
    client_a, session, session_factory, tenant_a, tenant_b, public_plec, monkeypatch
):
    pin_real_model(monkeypatch, also=("claude-test-model-2",))
    (user_a, company_a), (user_b, company_b) = tenant_a, tenant_b
    document = public_plec["document"]
    tender_id = document.tender_id
    tenant_values = {str(company_a.id), str(company_b.id), str(user_a.id), str(user_b.id)}

    # A demana amb opcions (accepta paquet i qualitat); B, sense cap opcio.
    quote_a = await quote_service.create_quote(
        session,
        company_id=company_a.id,
        user_id=user_a.id,
        tender_id=tender_id,
        provider_factory=RealNamedFake,
        accept_incomplete_package=True,
        acknowledge_quality=True,
    )
    quote_b = await quote_service.create_quote(
        session,
        company_id=company_b.id,
        user_id=user_b.id,
        tender_id=tender_id,
        provider_factory=RealNamedFake,
    )
    assert quote_a.allowed and quote_b.allowed, (quote_a.prerequisites, quote_b.prerequisites)
    assert quote_a.analysis_identity == quote_b.analysis_identity is not None

    confirmed_a = await quote_service.confirm_quote(
        session,
        company_id=company_a.id,
        user_id=user_a.id,
        role="owner",
        quote_id=quote_a.id,
        provider_factory=RealNamedFake,
    )
    confirmed_b = await quote_service.confirm_quote(
        session,
        company_id=company_b.id,
        user_id=user_b.id,
        role="owner",
        quote_id=quote_b.id,
        provider_factory=RealNamedFake,
    )

    # Mateixa clau canonica, una sola feina.
    jobs = (await session.scalars(sa.select(WorkJob))).all()
    assert len(jobs) == 1
    job = jobs[0]
    current = await analysis_input(session, tender_id)
    identity = analysis_identity(
        current.documents, profile=PROFILES["v1"], provider=RealNamedFake()
    )
    assert identity == quote_a.analysis_identity
    assert job.dedupe_key == canonical_analysis_dedupe_key(
        tender_id=tender_id, analysis_identity=identity
    )
    assert confirmed_a.submission.request.work_job_id == job.id
    assert confirmed_b.submission.request.work_job_id == job.id
    # Cap parametre de cap tenant al job: perfil de producte, cap seleccio, cap force.
    assert job.parameters == {
        "profile": "v1",
        "force": False,
        "document_ids": [],
        "resume_from_run_id": None,
    }
    assert not tenant_values & set(json.dumps(job.parameters).split('"'))
    assert not any(value in job.dedupe_key for value in tenant_values)

    fake.queue_response({"requirements": [requirement(public_plec["extraction"].id, 1)]})
    executed = await run_worker(session_factory, provider_factory=RealNamedFake)
    assert len(executed) == 1

    runs = (
        await session.scalars(
            sa.select(RequirementExtractionRun).execution_options(populate_existing=True)
        )
    ).all()
    assert len(runs) == 1, "un sol run per a les dues empreses"
    run = runs[0]
    assert run.status == AnalysisRunStatus.COMPLETED
    assert run.run_basis == RequirementRunBasis.REAL and run.is_current is True
    assert run.analysis_identity == identity
    assert set(run.provider_metadata or {}) <= {"request_ids"}
    assert not tenant_values & set(json.dumps(run.provider_metadata or {}).split('"'))
    assert len(fake.SENT_PAYLOADS) == 1
    for sent in fake.SENT_PAYLOADS:
        assert not any(value in sent["user_content"] for value in tenant_values)

    requests = (
        await session.scalars(
            sa.select(JobRequest)
            .order_by(JobRequest.requested_at)
            .execution_options(populate_existing=True)
        )
    ).all()
    assert [(r.company_id, r.status, r.result_run_id) for r in requests] == [
        (company_a.id, JobRequestStatus.SUCCEEDED, run.id),
        (company_b.id, JobRequestStatus.SUCCEEDED, run.id),
    ]

    # B torna a demanar: reaprofita el run canonic sense cap feina nova.
    again_b = await quote_service.create_quote(
        session,
        company_id=company_b.id,
        user_id=user_b.id,
        tender_id=tender_id,
        provider_factory=RealNamedFake,
    )
    assert (again_b.reusable_run_id, again_b.chunks_to_send) == (run.id, 0)
    reused = await quote_service.confirm_quote(
        session,
        company_id=company_b.id,
        user_id=user_b.id,
        role="owner",
        quote_id=again_b.id,
        provider_factory=RealNamedFake,
    )
    assert reused.submission.request.outcome == JobOutcome.REUSED_EXISTING
    assert reused.submission.request.result_run_id == run.id
    assert await _count(session, WorkJob) == 1
    assert await _count(session, RequirementExtractionRun) == 1

    # Punters independents: A accepta el run; B no en te cap i llegeix el canonic.
    await review_runs.adopt_requirement_run(
        session, company_id=company_a.id, tender_id=tender_id, run_id=run.id, user_id=user_a.id
    )
    await session.commit()
    newer = (
        await analyze_tender_requirements(
            session,
            await session.get(Tender, tender_id),
            provider=RealNamedFake(
                responses=[{"requirements": [requirement(public_plec["extraction"].id, 2)]}],
                model="claude-test-model-2",
            ),
        )
    ).run
    assert newer.is_current is True
    assert await review_runs.current_run_id(session, tender_id, company_a.id) == run.id
    assert await review_runs.current_run_id(session, tender_id, company_b.id) == newer.id

    # A amb `force` (via API): 422, i res de nou.
    refused = await client_a.post(_quotes_url(company_a.id, tender_id), json={"force": True})
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "public_tender_canonical_only"

    # L'operador amb `force` (via de servei): run nou, mai canonic.
    forced = (
        await analyze_tender_requirements(
            session,
            await session.get(Tender, tender_id),
            force=True,
            provider=RealNamedFake(
                responses=[{"requirements": [requirement(public_plec["extraction"].id, 1)]}],
                model="claude-test-model-2",
            ),
        )
    ).run
    assert forced.status == AnalysisRunStatus.COMPLETED
    assert forced.is_current is False
    assert forced.error_code == FORCED_NOT_CANONICAL
    canonical = await session.scalar(
        sa.select(RequirementExtractionRun.id).where(
            RequirementExtractionRun.tender_id == tender_id,
            RequirementExtractionRun.is_current.is_(True),
        )
    )
    assert canonical == newer.id
    assert await review_runs.current_run_id(session, tender_id, company_a.id) == run.id


async def test_un_run_forcat_d_un_tender_manual_continua_podent_ser_vigent(
    session, tenant_a, tender_document
):
    """La regla «force mai canonic» es de les licitacions publiques (§6.4)."""
    _, company = tenant_a
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)
    tender.owner_company_id = company.id
    await session.commit()
    first = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=RealNamedFake(
                responses=[{"requirements": [requirement(plec["extraction"].id, 1)]}]
            ),
        )
    ).run

    second = (
        await analyze_tender_requirements(
            session,
            tender,
            force=True,
            provider=RealNamedFake(
                responses=[{"requirements": [requirement(plec["extraction"].id, 2)]}]
            ),
        )
    ).run

    await session.refresh(first)
    assert second.is_current is True and first.is_current is False


# --------------------------------------------------------------------------- #
# Carry-forward de Tasks 1-3: `force_download` mai sobre una pujada d'operador   #
# --------------------------------------------------------------------------- #


async def test_force_download_no_torna_a_baixar_una_pujada_de_l_operador(
    session, tender_document, monkeypatch
):
    """Els bytes d'un `operator_upload` son els que va pujar l'operador: no hi ha cap
    URL d'on tornar-los a baixar. Forcar-ne la descarrega no fa res (§2.2)."""
    from app.models.enums import DocumentContributionStatus, TenderDocumentOrigin
    from app.models.tender_documents import TenderDocumentFile
    from app.services import tender_document_processing

    plec = await _add_extracted_pages(session, tender_document, pages=2)
    document = plec["document"]
    document.origin = TenderDocumentOrigin.OPERATOR_UPLOAD
    document.governing_source_id = None
    document.contribution_status = DocumentContributionStatus.VERIFIED
    document.verified_at = datetime.now(UTC)
    document.url = "operator-upload://prova"
    await session.commit()

    async def no_download(*_args, **_kwargs):
        raise AssertionError("una pujada de l'operador no es torna a baixar mai")

    monkeypatch.setattr(tender_document_processing, "download_document", no_download)

    outcome = await tender_document_processing.process_tender_document(
        session, document, force_download=True
    )

    assert outcome.file.content_id == plec["content"].id
    files = await _count(session, TenderDocumentFile)
    assert files == 1


# --------------------------------------------------------------------------- #
# Ruta d'operador: `force` d'un tender public (§6.4), mai canonic              #
# --------------------------------------------------------------------------- #


def _force_url(tender_id) -> str:
    return f"/api/v1/platform/tenders/{tender_id}/analysis/force"


@pytest.fixture
async def op_client(client_factory, db):
    from tests.conftest import login
    from tests.test_platform_onboarding import create_operator

    operator = await create_operator(db)
    http_client = client_factory()
    await login(http_client, operator.email)
    http_client.operator_id = operator.id
    return http_client


async def test_l_operador_encua_un_run_forcat_que_mai_es_canonic(
    op_client, session, session_factory, public_plec, monkeypatch
):
    from app.api.v1.routers import platform_analysis
    from app.models.audit import AuditLog

    monkeypatch.setattr(platform_analysis, "get_provider", RealNamedFake)
    tender_id = public_plec["document"].tender_id
    canonical = (
        await analyze_tender_requirements(
            session,
            await session.get(Tender, tender_id),
            provider=RealNamedFake(
                responses=[{"requirements": [requirement(public_plec["extraction"].id, 1)]}]
            ),
        )
    ).run
    assert canonical.is_current

    response = await op_client.post(_force_url(tender_id), json={})

    assert response.status_code == 202, response.text
    body = response.json()
    job = await session.get(WorkJob, uuid.UUID(body["work_job_id"]))
    assert job.dedupe_key.startswith(f"analyze:{tender_id}:force:")
    assert job.parameters["force"] is True
    assert await _count(session, JobRequest) == 0, "cap peticio de tenant"
    audit = await session.scalar(sa.select(AuditLog).where(AuditLog.entity_id == job.id))
    assert audit is not None and audit.company_id is None
    assert audit.user_id == op_client.operator_id

    fake.queue_response({"requirements": [requirement(public_plec["extraction"].id, 2)]})
    await run_worker(session_factory, provider_factory=RealNamedFake)

    forced = await session.scalar(
        sa.select(RequirementExtractionRun)
        .where(RequirementExtractionRun.work_job_id == job.id)
        .execution_options(populate_existing=True)
    )
    assert forced.status == AnalysisRunStatus.COMPLETED
    assert (forced.is_current, forced.error_code) == (False, FORCED_NOT_CANONICAL)
    await session.refresh(canonical)
    assert canonical.is_current is True


async def test_la_ruta_de_force_es_nomes_de_l_operador(
    client_a, op_client, session, public_plec, tenant_a
):
    tender_id = public_plec["document"].tender_id

    denied = await client_a.post(_force_url(tender_id), json={})
    missing = await op_client.post(_force_url(uuid.uuid4()), json={})
    tender = await session.get(Tender, tender_id)
    tender.owner_company_id = tenant_a[1].id
    await session.commit()
    manual = await op_client.post(_force_url(tender_id), json={})

    assert denied.status_code == 403
    assert missing.status_code == 404
    assert manual.status_code == 422
    assert manual.json()["error"]["code"] == "tender_not_public"
    assert await _count(session, WorkJob) == 0
