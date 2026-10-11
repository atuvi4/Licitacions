"""Prerequisits de la quota (3B-2 §6.1) i guardes del handler (§6.3 capa 1, §6.7).

`build_preview` calcula els prerequisits, la quota els desa
(`analysis_quotes.prerequisites`) i formen part de la identitat del preview: si
canvien entre el preview i la confirmacio, cal tornar-lo a ensenyar. Un sol
`blocking_reason`, en aquest ordre de prioritat:

    provider_not_real > model_not_pinned > price_unknown > budget_exhausted >
    budget_exceeded > package_incomplete > quality_unknown > quality_warning >
    selection_not_canonical

i la resta d'incompliments a `prerequisites.failed[]`. El handler refusa un
proveidor no real (fora de test o de `ALLOW_SIMULATED_ANALYSIS`), un model no
pinat i un model sense preu ABANS de cap crida.

El proveidor «real» es `RealNamedFake` (cap xarxa).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.ai.profiles import V1
from app.ai.providers import fake
from app.core.config import settings
from app.jobs import quotes as quote_service
from app.jobs.requests import submit_analysis_request
from app.models.analysis_quotes import AnalysisQuote
from app.models.enums import (
    AnalysisQuoteStatus,
    AnalysisRunStatus,
    ClassificationFindingCode,
    JobRequestStatus,
    PackageGapCode,
    PackageGapProducer,
    WorkJobStatus,
)
from app.models.jobs import JobRequest, WorkJob
from app.models.package import TenderPackageGap
from app.models.requirements import RequirementExtractionRun
from app.models.tenders import Tender
from app.services.analysis_planning import BLOCKING_PRIORITY, build_preview
from app.services.requirement_analysis import analyze_tender_requirements
from tests.conftest import create_user_with_company
from tests.jobs_support import run_worker
from tests.quota_support import (
    add_high_finding,
    clean_package,
    pin_real_model,
    requirement,
    set_quality,
)
from tests.real_provider import RealNamedFake
from tests.test_jobs import _add_extracted_pages


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
async def plec(session, tender_document):
    """Tender public amb un PCAP llegit, QA bona i paquet complet."""
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    await set_quality(session, plec["extraction"], "good")
    plec["classification"] = await clean_package(session, tender_document.tender_id)
    return plec


async def _preview(session, plec, provider=None, **options):
    return await build_preview(
        session,
        plec["document"].tender_id,
        profile=V1,
        provider=provider or RealNamedFake(),
        **options,
    )


def test_l_ordre_de_prioritat_es_el_de_la_spec():
    assert BLOCKING_PRIORITY[:9] == (
        "provider_not_real",
        "model_not_pinned",
        "price_unknown",
        "budget_exhausted",
        "budget_exceeded",
        "package_incomplete",
        "quality_unknown",
        "quality_warning",
        "selection_not_canonical",
    )


# --------------------------------------------------------------------------- #
# §6.1.1 proveidor real, model pinat, preu                                     #
# --------------------------------------------------------------------------- #


async def test_fake_fora_de_test_bloqueja_provider_not_real(session, plec, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(settings, "ALLOW_SIMULATED_ANALYSIS", False)

    preview = await _preview(session, plec, provider=fake.FakeProvider())

    assert preview.allowed is False
    assert preview.blocking_reason == "provider_not_real"
    assert preview.prerequisites["provider_basis"] == "simulation"
    assert preview.prerequisites["failed"][0] == "provider_not_real"


@pytest.mark.parametrize("name", ["fake", "fixture"])
async def test_amb_la_bandera_una_simulacio_es_pot_demanar(session, plec, monkeypatch, name):
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(settings, "ALLOW_SIMULATED_ANALYSIS", True)
    provider = fake.FakeProvider()
    provider.name = name

    preview = await _preview(session, plec, provider=provider)

    assert preview.allowed is True, preview.prerequisites
    assert preview.prerequisites["provider_basis"] == "simulation"


async def test_en_test_una_simulacio_es_pot_demanar_sense_bandera(session, plec, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "test")
    monkeypatch.setattr(settings, "ALLOW_SIMULATED_ANALYSIS", False)

    preview = await _preview(session, plec, provider=fake.FakeProvider())

    assert preview.allowed is True


async def test_un_model_real_fora_de_la_llista_bloqueja_model_not_pinned(
    session, plec, monkeypatch
):
    pin_real_model(monkeypatch, model="claude-un-altre")

    preview = await _preview(session, plec)

    assert (preview.allowed, preview.blocking_reason) == (False, "model_not_pinned")
    # Tambe li falta el preu: queda a la llista, no al motiu.
    assert preview.prerequisites["failed"] == ["model_not_pinned", "price_unknown"]


async def test_la_llista_buida_per_defecte_no_admet_cap_model_real(session, plec, monkeypatch):
    monkeypatch.setattr(settings, "LLM_ALLOWED_MODELS", [])

    preview = await _preview(session, plec)

    assert preview.blocking_reason == "model_not_pinned"


async def test_un_model_pinat_sense_preu_bloqueja_price_unknown(session, plec, monkeypatch):
    pin_real_model(monkeypatch, price=False)

    preview = await _preview(session, plec)

    assert (preview.allowed, preview.blocking_reason) == (False, "price_unknown")
    assert preview.estimated_cost_usd is None


async def test_el_preu_ha_de_ser_del_model_exacte(session, plec, monkeypatch):
    """Model pinat a un ID exacte (§13.5): un prefix no val com a preu."""
    pin_real_model(monkeypatch)
    monkeypatch.setattr(
        settings, "LLM_MODEL_PRICES", {"claude-test": {"input": 3.0, "output": 15.0}}
    )

    preview = await _preview(session, plec)

    assert preview.blocking_reason == "price_unknown"


async def test_tot_correcte_permet_i_desa_els_prerequisits_a_la_quota(
    session, plec, tenant_a, monkeypatch
):
    pin_real_model(monkeypatch)
    user, company = tenant_a

    quote = await quote_service.create_quote(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=plec["document"].tender_id,
        provider_factory=RealNamedFake,
    )

    assert quote.allowed is True and quote.blocking_reason is None
    prerequisites = quote.prerequisites
    assert prerequisites["provider_basis"] == "real"
    assert prerequisites["failed"] == []
    assert prerequisites["enforced"] is True
    assert prerequisites["package"]["status"] == "complete"
    assert prerequisites["quality"]["status"] == "good"
    assert prerequisites["canonical"] is True
    # Sense historic, el cost surt dels caracters del pla i del sostre de sortida.
    assert quote.estimated_cost_usd is not None and quote.estimated_cost_usd > 0
    assert prerequisites["budget"]["estimated_cost_usd"] == float(quote.estimated_cost_usd)
    stored = await session.get(AnalysisQuote, quote.id, populate_existing=True)
    assert stored.prerequisites == prerequisites


# --------------------------------------------------------------------------- #
# §6.1.2-3 paquet                                                              #
# --------------------------------------------------------------------------- #


async def test_una_troballa_high_sense_acceptacio_bloqueja_package_incomplete(
    session, plec, monkeypatch
):
    pin_real_model(monkeypatch)
    await add_high_finding(session, plec["classification"])

    refused = await _preview(session, plec)
    accepted = await _preview(session, plec, accept_incomplete_package=True)

    assert (refused.allowed, refused.blocking_reason) == (False, "package_incomplete")
    codes = [issue["code"] for issue in refused.prerequisites["package"]["issues"]]
    assert codes == ["required_role_missing"]
    assert accepted.allowed is True
    assert accepted.prerequisites["package"]["accepted"] is True
    assert accepted.prerequisites["failed"] == []


async def test_una_troballa_que_no_es_high_no_bloqueja(session, plec, monkeypatch):
    from app.models.enums import FindingSeverity

    pin_real_model(monkeypatch)
    await add_high_finding(
        session,
        plec["classification"],
        ClassificationFindingCode.DUPLICATE_TEXT,
        severity=FindingSeverity.MEDIUM,
    )

    assert (await _preview(session, plec)).allowed is True


async def test_un_forat_obert_del_paquet_bloqueja_package_incomplete(session, plec, monkeypatch):
    pin_real_model(monkeypatch)
    session.add(
        TenderPackageGap(
            tender_id=plec["document"].tender_id,
            code=PackageGapCode.DECLARED_BY_OPERATOR,
            producer=PackageGapProducer.OPERATOR,
            detail={"note": "falta el DOC-03"},
        )
    )
    await session.commit()

    preview = await _preview(session, plec)

    assert preview.blocking_reason == "package_incomplete"
    assert preview.prerequisites["package"]["status"] == "incomplete"


async def test_sense_classificacio_global_vigent_el_paquet_no_es_complet(
    session, tender_document, monkeypatch
):
    pin_real_model(monkeypatch)
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    await set_quality(session, plec["extraction"], "good")

    preview = await _preview(session, plec)

    assert preview.blocking_reason == "package_incomplete"
    codes = [issue["code"] for issue in preview.prerequisites["package"]["issues"]]
    assert codes == ["classification_not_current"]
    # F10 (revisio 3B-2): no comprovat no vol dir que hi faltin documents. El
    # bloqueig es el mateix (§6.1.3), pero l'estat del paquet es `unknown`.
    assert preview.prerequisites["package"]["status"] == "unknown"


async def test_sense_classificacio_vigent_i_amb_un_forat_el_paquet_es_incomplet(
    session, tender_document, monkeypatch
):
    pin_real_model(monkeypatch)
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    await set_quality(session, plec["extraction"], "good")
    session.add(
        TenderPackageGap(
            tender_id=plec["document"].tender_id,
            code=PackageGapCode.DECLARED_BY_OPERATOR,
            producer=PackageGapProducer.OPERATOR,
            detail={"note": "falta el DOC-03"},
        )
    )
    await session.commit()

    preview = await _preview(session, plec)

    assert preview.blocking_reason == "package_incomplete"
    codes = {issue["code"] for issue in preview.prerequisites["package"]["issues"]}
    assert codes == {"classification_not_current", "package_gap"}
    assert preview.prerequisites["package"]["status"] == "incomplete"


async def test_un_plec_per_defecte_sense_text_bloqueja_package_incomplete(
    session, plec, second_tender_document, monkeypatch
):
    from app.models.enums import TenderDocumentType

    pin_real_model(monkeypatch)
    second_tender_document.document_type = TenderDocumentType.TECHNICAL_TERMS
    await session.commit()

    preview = await _preview(session, plec)

    assert preview.blocking_reason == "package_incomplete"
    issues = preview.prerequisites["package"]["issues"]
    assert {
        "code": "default_document_skipped",
        "tender_document_id": str(second_tender_document.id),
        "reason": "document_not_processed",
    } in issues


# --------------------------------------------------------------------------- #
# §6.1.4 qualitat del text                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [None, "unknown", "poor"])
async def test_qa_desconeguda_o_pobra_bloqueja_quality_unknown(session, plec, monkeypatch, status):
    pin_real_model(monkeypatch)
    await set_quality(session, plec["extraction"], status)

    preview = await _preview(session, plec, acknowledge_quality=True)

    assert (preview.allowed, preview.blocking_reason) == (False, "quality_unknown")


async def test_qa_amb_avisos_demana_acknowledge_quality(session, plec, monkeypatch):
    pin_real_model(monkeypatch)
    await set_quality(session, plec["extraction"], "warning")

    refused = await _preview(session, plec)
    acknowledged = await _preview(session, plec, acknowledge_quality=True)

    assert (refused.allowed, refused.blocking_reason) == (False, "quality_warning")
    assert acknowledged.allowed is True
    assert acknowledged.prerequisites["quality"]["acknowledged"] is True


# --------------------------------------------------------------------------- #
# §6.1.5 elegibilitat canonica i prioritat                                     #
# --------------------------------------------------------------------------- #


async def test_un_force_en_un_tender_public_no_es_canonic(session, plec, monkeypatch):
    pin_real_model(monkeypatch)

    preview = await _preview(session, plec, force=True)

    assert (preview.allowed, preview.blocking_reason) == (False, "selection_not_canonical")
    assert preview.prerequisites["canonical"] is False


async def test_en_un_tender_manual_la_seleccio_explicita_no_es_cap_incompliment(
    session, plec, tenant_a, monkeypatch
):
    pin_real_model(monkeypatch)
    tender = await session.get(Tender, plec["document"].tender_id)
    tender.owner_company_id = tenant_a[1].id
    await session.commit()

    preview = await _preview(session, plec, document_ids=[plec["document"].id])

    # Una licitacio manual no te passada global: el paquet es mira pels forats.
    assert preview.allowed is True, preview.prerequisites
    assert preview.prerequisites["public_tender"] is False


async def test_un_sol_motiu_prioritzat_i_la_resta_a_failed(session, plec, monkeypatch):
    pin_real_model(monkeypatch, model="claude-un-altre")
    await add_high_finding(session, plec["classification"])
    await set_quality(session, plec["extraction"], "warning")

    preview = await _preview(session, plec, force=True)

    assert preview.blocking_reason == "model_not_pinned"
    assert preview.prerequisites["failed"] == [
        "model_not_pinned",
        "price_unknown",
        "package_incomplete",
        "quality_warning",
        "selection_not_canonical",
    ]


async def test_una_simulacio_en_test_registra_els_incompliments_sense_bloquejar(
    session, tender_document
):
    """Una simulacio no gasta i mai es vigent: el paquet i la qualitat queden com a
    avis (`advisory`), no com a bloqueig."""
    plec = await _add_extracted_pages(session, tender_document, pages=2)

    preview = await _preview(session, plec, provider=fake.FakeProvider())

    assert preview.allowed is True
    assert preview.prerequisites["enforced"] is False
    assert preview.prerequisites["failed"] == []
    assert preview.prerequisites["advisory"] == ["package_incomplete", "quality_unknown"]


async def test_reaprofitar_un_run_canonic_no_demana_els_prerequisits_de_paquet(
    session, plec, monkeypatch
):
    pin_real_model(monkeypatch)
    tender = await session.get(Tender, plec["document"].tender_id)
    run = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=RealNamedFake(
                responses=[{"requirements": [requirement(plec["extraction"].id, 1)]}]
            ),
        )
    ).run
    assert run.is_current
    await set_quality(session, plec["extraction"], "warning")

    preview = await _preview(session, plec)

    assert (preview.reusable_run_id, preview.chunks_to_send) == (run.id, 0)
    assert preview.allowed is True
    assert preview.prerequisites["advisory"] == ["quality_warning"]


async def test_avis_si_l_ultim_run_real_del_mateix_corpus_va_quedar_buit(
    session, tender_document, monkeypatch
):
    """`completed_empty` (§6.3) no bloqueja la quota: la marca per revisar-la."""
    pin_real_model(monkeypatch)
    plec = await _add_extracted_pages(session, tender_document, pages=5)
    await set_quality(session, plec["extraction"], "good")
    await clean_package(session, tender_document.tender_id)
    empty = (
        await analyze_tender_requirements(
            session,
            await session.get(Tender, tender_document.tender_id),
            provider=RealNamedFake(),
        )
    ).run
    assert empty.status == AnalysisRunStatus.COMPLETED_EMPTY

    preview = await _preview(session, plec)

    assert preview.allowed is True
    assert preview.prerequisites["warnings"] == ["previous_run_completed_empty"]


# --------------------------------------------------------------------------- #
# Confirmacio                                                                  #
# --------------------------------------------------------------------------- #


async def test_confirmar_una_quota_bloquejada_respon_el_motiu_i_no_crea_res(
    client_a, tenant_a, plec, session, monkeypatch
):
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(settings, "ALLOW_SIMULATED_ANALYSIS", False)
    company = tenant_a[1]
    created = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{plec['document'].tender_id}/analysis-quotes"
    )
    assert created.status_code == 201, created.text
    quote = created.json()
    assert (quote["allowed"], quote["blocking_reason"]) == (False, "provider_not_real")
    assert quote["provider_basis"] == "simulation"
    assert quote["prerequisites"]["failed"][0] == "provider_not_real"

    confirm = await client_a.post(
        f"/api/v1/companies/{company.id}/analysis-quotes/{quote['id']}/confirm"
    )

    assert confirm.status_code == 422, confirm.text
    assert confirm.json()["error"]["code"] == "provider_not_real"
    assert await session.scalar(sa.select(sa.func.count(JobRequest.id))) == 0
    assert await session.scalar(sa.select(sa.func.count(WorkJob.id))) == 0


async def test_els_prerequisits_formen_part_de_la_identitat_del_preview(
    session, plec, tenant_a, monkeypatch
):
    pin_real_model(monkeypatch)
    user, company = tenant_a
    quote = await quote_service.create_quote(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=plec["document"].tender_id,
        provider_factory=RealNamedFake,
    )
    assert quote.allowed
    await set_quality(session, plec["extraction"], "warning")

    with pytest.raises(quote_service.PreviewChangedError) as caught:
        await quote_service.confirm_quote(
            session,
            company_id=company.id,
            user_id=user.id,
            role="owner",
            quote_id=quote.id,
            provider_factory=RealNamedFake,
        )

    replacement = caught.value.quote
    assert replacement.blocking_reason == "quality_warning"
    old = await session.get(AnalysisQuote, quote.id, populate_existing=True)
    assert old.status == AnalysisQuoteStatus.SUPERSEDED
    assert await session.scalar(sa.select(sa.func.count(JobRequest.id))) == 0


async def test_una_quota_desada_amb_la_semantica_antiga_del_paquet_no_es_reutilitza(
    session, plec, tenant_a, monkeypatch
):
    """F10: el paquet `unknown` canvia la forma dels prerequisits (v2).

    Una quota desada amb la versio anterior (on «sense comprovar» es deia
    `incomplete`) no es pot confirmar tal qual: es torna a ensenyar.
    """
    pin_real_model(monkeypatch)
    user, company = tenant_a
    quote = await quote_service.create_quote(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=plec["document"].tender_id,
        provider_factory=RealNamedFake,
    )
    assert quote.allowed
    assert quote.prerequisites["version"] != "quote-prerequisites-v1"
    quote.prerequisites = {**quote.prerequisites, "version": "quote-prerequisites-v1"}
    await session.commit()

    with pytest.raises(quote_service.PreviewChangedError) as caught:
        await quote_service.confirm_quote(
            session,
            company_id=company.id,
            user_id=user.id,
            role="owner",
            quote_id=quote.id,
            provider_factory=RealNamedFake,
        )

    assert caught.value.quote.prerequisites["version"] == "quote-prerequisites-v2"
    old = await session.get(AnalysisQuote, quote.id, populate_existing=True)
    assert old.status == AnalysisQuoteStatus.SUPERSEDED
    assert await session.scalar(sa.select(sa.func.count(JobRequest.id))) == 0


async def test_les_acceptacions_es_conserven_en_reconfirmar(session, plec, tenant_a, monkeypatch):
    pin_real_model(monkeypatch)
    await add_high_finding(session, plec["classification"])
    user, company = tenant_a
    quote = await quote_service.create_quote(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=plec["document"].tender_id,
        provider_factory=RealNamedFake,
        accept_incomplete_package=True,
    )
    assert quote.allowed and quote.accept_incomplete_package is True

    confirmation = await quote_service.confirm_quote(
        session,
        company_id=company.id,
        user_id=user.id,
        role="owner",
        quote_id=quote.id,
        provider_factory=RealNamedFake,
    )

    assert confirmation.quote.status == AnalysisQuoteStatus.CONFIRMED
    assert confirmation.submission.request.analysis_quote_id == quote.id


# --------------------------------------------------------------------------- #
# Handler: cap crida abans de les guardes                                       #
# --------------------------------------------------------------------------- #


async def _queue_job(session, tender_id, provider) -> WorkJob:
    user, company = await create_user_with_company(
        session, email="handler@empresa.com", company_name="Empresa handler"
    )
    submission = await submit_analysis_request(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=tender_id,
        provider_factory=lambda: provider,
    )
    return await session.get(WorkJob, submission.request.work_job_id)


@pytest.mark.parametrize(
    ("setup", "expected"),
    [
        ("simulation_outside_test", "provider_not_real"),
        ("model_not_pinned", "model_not_pinned"),
        ("price_unknown", "price_unknown"),
    ],
)
async def test_el_handler_refusa_abans_de_cap_crida(
    session, session_factory, plec, monkeypatch, setup, expected
):
    if setup == "simulation_outside_test":
        provider = fake.FakeProvider()
    else:
        provider = RealNamedFake()
    job = await _queue_job(session, plec["document"].tender_id, provider)
    if setup == "simulation_outside_test":
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")
        monkeypatch.setattr(settings, "ALLOW_SIMULATED_ANALYSIS", False)
    elif setup == "model_not_pinned":
        pin_real_model(monkeypatch, model="claude-un-altre")
    else:
        pin_real_model(monkeypatch, price=False)

    await run_worker(session_factory, provider_factory=lambda: provider)

    job = await session.get(WorkJob, job.id, populate_existing=True)
    assert job.status == WorkJobStatus.FAILED
    assert job.error_code == expected
    assert fake.SENT_PAYLOADS == []
    assert await session.scalar(sa.select(sa.func.count(RequirementExtractionRun.id))) == 0
    request = await session.scalar(sa.select(JobRequest).execution_options(populate_existing=True))
    assert (request.status, request.error_code) == (JobRequestStatus.FAILED, expected)
