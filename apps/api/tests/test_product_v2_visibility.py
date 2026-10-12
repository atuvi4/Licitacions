"""product-v2 es intern fins que es promocioni, i el run buit es per perfil.

Que es protegeix:
* cap tenant (ni cap ruta que resol un perfil pel NOM) no pot demanar `product-v2`:
  el refus es el normal d'un perfil desconegut; els perfils publics no canvien i V1
  continua sent el defecte i el de producte;
* el flux intern (canari, operador des del codi) l'executa passant el perfil;
* `previous_run_completed_empty` nomes mira runs del MATEIX perfil: un run buit de V1 no
  diu res de product-v2 (ni l'impedeix, ni el reaprofita) i a l'inreves;
* un run product-v2 (experimental) mai es la base vigent, ni en una licitacio publica ni
  en una de manual, i el seu historial es separat per a la represa.

Tot offline, amb el proveidor fals.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.ai.profiles import (
    DEFAULT_PROFILE,
    INTERNAL_PROFILES,
    PRODUCT_V2,
    PROFILES,
    V1,
    V2,
    profile_for_run,
)
from app.ai.providers import fake
from app.core.config import Settings, settings
from app.core.errors import ValidationError as AppValidationError
from app.jobs import quotes as quote_service
from app.jobs import requests as job_requests
from app.models.enums import AnalysisRunStatus
from app.models.jobs import WorkJob
from app.models.requirements import RequirementExtractionRun
from app.models.tenders import Tender
from app.services.analysis_planning import build_preview
from app.services.requirement_analysis import (
    EXPERIMENTAL_PROFILE_NOT_CURRENT,
    analyze_tender_requirements,
    run_is_superseded,
)
from tests.quota_support import clean_package, pin_real_model, requirement, set_quality
from tests.real_provider import RealNamedFake
from tests.test_jobs import _add_extracted_pages

pytestmark = pytest.mark.asyncio

WARNING = "previous_run_completed_empty"


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


# --------------------------------------------------------------------------- #
# Visibilitat                                                                   #
# --------------------------------------------------------------------------- #


async def test_els_perfils_publics_no_canvien_i_v1_es_el_defecte():
    assert set(PROFILES) == {"v1", "v2"}
    assert PROFILES["v1"] is V1 and PROFILES["v2"] is V2
    assert set(INTERNAL_PROFILES) == {"v3", "product-v2"}
    assert INTERNAL_PROFILES["product-v2"] is PRODUCT_V2
    assert DEFAULT_PROFILE is V1
    assert settings.REQUIREMENTS_PRODUCT_PROFILE == "v1"
    assert quote_service.product_profile() is V1
    # Els runs product-v2 es continuen podent llegir amb el seu contracte.
    assert profile_for_run(PRODUCT_V2.extractor_version) is PRODUCT_V2


async def test_product_v2_no_pot_ser_el_perfil_de_producte_per_configuracio():
    with pytest.raises(ValidationError):
        Settings(REQUIREMENTS_PRODUCT_PROFILE="product-v2")


async def test_un_tenant_no_pot_demanar_product_v2_en_un_tender_manual_propi(
    client_a, tenant_a, tender_document, session
):
    _, company = tenant_a
    await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)
    tender.owner_company_id = company.id
    await session.commit()
    url = f"/api/v1/companies/{company.id}/tenders/{tender.id}/analysis-quotes"

    refused = await client_a.post(url, json={"profile": "product-v2"})
    public = await client_a.post(url, json={"profile": "v2"})

    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["profiles"] == ["v1", "v2"]  # product-v2 no s'anuncia
    assert public.status_code == 201, public.text
    assert fake.SENT_PAYLOADS == []


async def test_la_ruta_d_operador_per_nom_tampoc_resol_product_v2(
    client_factory, db, session, tender_document
):
    from tests.conftest import login
    from tests.test_platform_onboarding import create_operator

    operator = await create_operator(db)
    client = client_factory()
    await login(client, operator.email)
    await _add_extracted_pages(session, tender_document, pages=2)

    response = await client.post(
        f"/api/v1/platform/tenders/{tender_document.tender_id}/analysis/force",
        json={"profile": "product-v2"},
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["details"]["profiles"] == ["v1", "v2"]


async def test_el_flux_intern_executa_product_v2_passant_el_perfil(session, tender_document):
    await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)

    preview = await build_preview(
        session, tender.id, profile=PRODUCT_V2, provider=fake.FakeProvider()
    )
    run = (await analyze_tender_requirements(session, tender, profile=PRODUCT_V2)).run

    assert preview.profile == "product-v2" and preview.chunks_to_send == 1
    assert run.status == AnalysisRunStatus.COMPLETED
    assert (run.extractor_version, run.prompt_version, run.schema_version) == (
        PRODUCT_V2.extractor_version,
        PRODUCT_V2.prompt_version,
        PRODUCT_V2.schema_version,
    )


# --------------------------------------------------------------------------- #
# Run buit per perfil                                                           #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def corpus(session, tender_document, monkeypatch):
    """Un corpus que es pot quotar amb despesa real (model fixat, paquet net)."""
    pin_real_model(monkeypatch)
    plec = await _add_extracted_pages(session, tender_document, pages=5)
    await set_quality(session, plec["extraction"], "good")
    await clean_package(session, tender_document.tender_id)
    return await session.get(Tender, tender_document.tender_id)


async def _empty_run(session, tender, profile):
    run = (
        await analyze_tender_requirements(
            session, tender, profile=profile, provider=RealNamedFake()
        )
    ).run
    assert run.status == AnalysisRunStatus.COMPLETED_EMPTY
    return run


async def _preview(session, tender, profile):
    return await build_preview(session, tender.id, profile=profile, provider=RealNamedFake())


async def test_un_run_buit_de_v1_no_afecta_product_v2(session, corpus):
    empty = await _empty_run(session, corpus, V1)

    v1 = await _preview(session, corpus, V1)
    pv2 = await _preview(session, corpus, PRODUCT_V2)

    assert v1.prerequisites["warnings"] == [WARNING]  # el mateix perfil, com abans
    assert pv2.prerequisites["warnings"] == []
    # Ni equivalent ni reaprofitable: product-v2 envia els seus chunks.
    assert pv2.reusable_run_id is None and pv2.chunks_to_send == pv2.chunks > 0
    assert pv2.input_fingerprint == empty.input_fingerprint  # mateix corpus


async def test_un_run_buit_de_product_v2_no_afecta_v1(session, corpus):
    empty = await _empty_run(session, corpus, PRODUCT_V2)

    v1 = await _preview(session, corpus, V1)
    pv2 = await _preview(session, corpus, PRODUCT_V2)

    assert v1.prerequisites["warnings"] == []
    assert v1.reusable_run_id is None and v1.chunks_to_send == v1.chunks > 0
    assert pv2.prerequisites["warnings"] == [WARNING]
    assert v1.input_fingerprint == empty.input_fingerprint  # mateix corpus


async def test_un_run_buit_posterior_d_un_altre_perfil_no_amaga_el_del_mateix(session, corpus):
    """L'ultim run real del corpus es de V1 (buit) despres d'un product-v2 buit: product-v2
    continua veient el SEU run buit, i V1 el seu."""
    await _empty_run(session, corpus, PRODUCT_V2)
    await _empty_run(session, corpus, V1)

    assert (await _preview(session, corpus, PRODUCT_V2)).prerequisites["warnings"] == [WARNING]
    assert (await _preview(session, corpus, V1)).prerequisites["warnings"] == [WARNING]


# --------------------------------------------------------------------------- #
# Feines encuades i base vigent                                                 #
# --------------------------------------------------------------------------- #


async def test_cap_via_de_cua_no_encua_product_v2(session, tenant_a, tender_document):
    user, company = tenant_a
    await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)
    tender.owner_company_id = company.id
    await session.commit()

    with pytest.raises(AppValidationError):
        await job_requests.submit_analysis_request(
            session, company_id=company.id, user_id=user.id, tender_id=tender.id,
            profile=PRODUCT_V2,
        )  # fmt: skip
    with pytest.raises(AppValidationError):
        await job_requests.submit_operator_analysis(
            session, tender_id=tender.id, user_id=user.id, profile=PRODUCT_V2
        )
    assert await session.scalar(sa.select(sa.func.count(WorkJob.id))) == 0


@pytest.mark.parametrize("manual", [False, True])
async def test_un_run_product_v2_mai_es_la_base_vigent(
    session, tenant_a, tender_document, monkeypatch, manual
):
    pin_real_model(monkeypatch)
    plec = await _add_extracted_pages(session, tender_document, pages=1)
    tender = await session.get(Tender, tender_document.tender_id)
    if manual:
        tender.owner_company_id = tenant_a[1].id
        await session.commit()
    payload = {"requirements": [requirement(plec["extraction"].id, 1)]}

    pv2 = (
        await analyze_tender_requirements(
            session, tender, profile=PRODUCT_V2, provider=RealNamedFake(responses=[payload])
        )
    ).run
    assert pv2.status == AnalysisRunStatus.COMPLETED
    assert (pv2.is_current, pv2.error_code) == (False, EXPERIMENTAL_PROFILE_NOT_CURRENT)

    # Control: el mateix run amb V1 si que es la base vigent.
    v1 = (
        await analyze_tender_requirements(
            session, tender, profile=V1, provider=RealNamedFake(responses=[payload])
        )
    ).run
    assert v1.status == AnalysisRunStatus.COMPLETED and v1.is_current is True


def _row(tender_id, profile, status, started_at) -> RequirementExtractionRun:
    return RequirementExtractionRun(
        tender_id=tender_id,
        status=status,
        input_fingerprint="f" * 64,
        extractor_version=profile.extractor_version,
        prompt_version=profile.prompt_version,
        schema_version=profile.schema_version,
        provider="anthropic",
        model="m",
        started_at=started_at,
        needs_review=status == AnalysisRunStatus.COMPLETED_EMPTY,
    )


@pytest.mark.parametrize(
    ("partial_profile", "newer_profile", "superseded"),
    [
        (V1, PRODUCT_V2, False),
        (PRODUCT_V2, V1, False),
        (PRODUCT_V2, PRODUCT_V2, True),
        (V1, V1, True),  # el mateix perfil: com abans
        (V1, V2, True),  # entre perfils publics: com abans
    ],
)
async def test_la_represa_nomes_la_supera_un_run_del_mateix_historial(
    session, tender_document, partial_profile, newer_profile, superseded
):
    now = datetime.now(UTC)
    partial = _row(
        tender_document.tender_id, partial_profile, AnalysisRunStatus.PARTIAL, now - timedelta(1)
    )
    session.add(partial)
    await session.flush()
    assert await run_is_superseded(session, partial) is False
    session.add(
        _row(tender_document.tender_id, newer_profile, AnalysisRunStatus.COMPLETED_EMPTY, now)
    )
    await session.flush()
    assert await run_is_superseded(session, partial) is superseded
