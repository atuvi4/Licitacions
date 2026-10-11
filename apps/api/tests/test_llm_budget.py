"""Caps de despesa, llibre i autoritzacions (3B-2 §6.7, decisio 5 de §13).

* Configuracio: `LLM_BUDGET_CANARY_USD=1`, `LLM_BUDGET_RUN_USD=3`,
  `LLM_BUDGET_PILOT_TOTAL_USD=10`; el cap vigent d'un scope es l'ultima autoritzacio
  d'aquell scope o, si no n'hi ha cap, la configuracio.
* `llm_spend_ledger` (global): una linia per chunk respost, cost = usage x preus,
  amb l'scope (`canary` o `run`) del run que l'ha pagat.
* Una sola porta (`llm_budget.provider_gate`) dins `analyze_tender_requirements`,
  abans de reservar el run i de cap crida, per al worker, la sonda i el benchmark:
  proveidor permes, model pinat, preu, pressupost no esgotat i estimacio <= cap.
* Abans de confirmar una quota: cost estimat <= cap de run (`budget_exceeded`) i
  gastat + estimat <= cap total (`budget_exhausted`). La quota d'una empresa nomes
  en porta booleans: cap import global.
* Abans de cada chunk: gastat total + estimacio del seguent <= cap total i gastat de la
  cadena de represa + estimacio <= cap del seu scope; si no, el run es tanca `PARTIAL`
  amb `budget_exceeded` i l'scope excedit. Nomes una autoritzacio posterior d'aquell
  scope o de `pilot_total` el reobre.
* `/platform/llm-budget`: GET estat per scope; POST `authorizations` nomes l'operador.

El proveidor «real» es `RealNamedFake`. Per poder superar un cap A MIG RUN (i no a la
porta, que compara l'estimacio), cada resposta declara 600.000 tokens d'entrada, a
1 $/M, i cap de sortida de pagament: 0,6 $ per chunk, mentre que l'estimacio per
caracters es d'unes poques centenes de tokens.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.ai.profiles import V1
from app.ai.provider import StructuredResult, Usage
from app.ai.providers import fake
from app.core.config import Settings, settings
from app.core.errors import AnalysisNotAllowedError
from app.models.audit import AuditLog
from app.models.enums import AnalysisRunStatus, LlmBudgetScope
from app.models.llm_budget import LlmBudgetAuthorization, LlmSpendLedger
from app.models.requirements import RequirementExtractionRun
from app.models.tenders import Tender
from app.services import llm_budget
from app.services.analysis_planning import build_preview
from app.services.requirement_analysis import analyze_tender_requirements
from tests.conftest import login
from tests.quota_support import (
    TEST_MODEL,
    clean_package,
    pin_real_model,
    requirement,
    set_quality,
)
from tests.real_provider import RealNamedFake
from tests.test_jobs import _add_extracted_pages
from tests.test_platform_onboarding import create_operator

BUDGET_URL = "/api/v1/platform/llm-budget"

#: 600.000 tokens d'entrada x 1 $/M = 0,6 $ per chunk.
CHUNK_INPUT_TOKENS = 600_000
CHUNK_COST = Decimal("0.600000")


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
async def plec3(session, tender_document, monkeypatch):
    """Tender public amb 3 pagines i 3 chunks (una pagina per chunk), paquet net."""
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    plec = await _add_extracted_pages(session, tender_document, pages=3)
    await set_quality(session, plec["extraction"], "good")
    await clean_package(session, tender_document.tender_id)
    pin_real_model(monkeypatch, input_per_million=1.0, output_per_million=0.0)
    return plec


@pytest.fixture
async def op_client(client_factory, db):
    operator = await create_operator(db)
    http_client = client_factory()
    await login(http_client, operator.email)
    http_client.operator_id = operator.id
    return http_client


def _responses(plec, pages=(1, 2, 3)) -> list[StructuredResult]:
    return [
        StructuredResult(
            data={"requirements": [requirement(plec["extraction"].id, page)]},
            usage=Usage(input_tokens=CHUNK_INPUT_TOKENS, output_tokens=0, request_id="r"),
            model=TEST_MODEL,
        )
        for page in pages
    ]


async def _analyze(session, plec, **options):
    tender = await session.get(Tender, plec["document"].tender_id)
    provider = options.pop("provider", None) or RealNamedFake(responses=_responses(plec))
    return (await analyze_tender_requirements(session, tender, provider=provider, **options)).run


async def _ledger(session) -> list[LlmSpendLedger]:
    return list(
        (
            await session.scalars(
                sa.select(LlmSpendLedger)
                .order_by(LlmSpendLedger.created_at, LlmSpendLedger.chunk_index)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def _spend(session, amount: str, *, run_id=None) -> None:
    session.add(
        LlmSpendLedger(
            run_id=run_id,
            chunk_index=0,
            provider="anthropic",
            model=TEST_MODEL,
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal(amount),
            budget_scope=LlmBudgetScope.RUN,
        )
    )
    await session.commit()


async def _preview(session, plec):
    return await build_preview(
        session, plec["document"].tender_id, profile=V1, provider=RealNamedFake()
    )


# --------------------------------------------------------------------------- #
# Configuracio                                                                 #
# --------------------------------------------------------------------------- #


def test_valors_per_defecte_de_la_configuracio(monkeypatch):
    for name in (
        "ALLOW_SIMULATED_ANALYSIS",
        "REQUIREMENTS_PRODUCT_PROFILE",
        "LLM_ALLOWED_MODELS",
        "LLM_MODEL_PRICES",
        "LLM_BUDGET_CANARY_USD",
        "LLM_BUDGET_RUN_USD",
        "LLM_BUDGET_PILOT_TOTAL_USD",
        "LLM_TIMEOUT_SECONDS",
        "LLM_MAX_RETRIES",
    ):
        monkeypatch.delenv(name, raising=False)
    fresh = Settings()
    assert fresh.ALLOW_SIMULATED_ANALYSIS is False
    assert fresh.REQUIREMENTS_PRODUCT_PROFILE == "v1"
    assert fresh.LLM_ALLOWED_MODELS == []
    assert fresh.LLM_MODEL_PRICES == {}
    assert (
        fresh.LLM_BUDGET_CANARY_USD,
        fresh.LLM_BUDGET_RUN_USD,
        fresh.LLM_BUDGET_PILOT_TOTAL_USD,
    ) == (1, 3, 10)
    assert fresh.LLM_TIMEOUT_SECONDS == 300
    # §6.6: cap reintent automatic; un re-enviament nomes via represa.
    assert fresh.LLM_MAX_RETRIES == 0


def test_un_perfil_de_producte_desconegut_no_arrenca():
    with pytest.raises(ValueError):
        Settings(REQUIREMENTS_PRODUCT_PROFILE="v9")


# --------------------------------------------------------------------------- #
# Cost, scope i cap vigent                                                      #
# --------------------------------------------------------------------------- #


def test_el_cost_d_un_chunk_es_usage_per_preu_en_decimal(monkeypatch):
    pin_real_model(monkeypatch, input_per_million=3.0, output_per_million=15.0)

    cost = llm_budget.chunk_cost(TEST_MODEL, input_tokens=1234, output_tokens=567)

    # 1234 x 3 / 1e6 + 567 x 15 / 1e6 = 0,003702 + 0,008505
    assert cost == Decimal("0.012207")
    assert llm_budget.chunk_cost("claude-sense-preu", input_tokens=1, output_tokens=1) is None


@pytest.mark.parametrize(
    ("only_chunks", "scope"),
    [
        (None, LlmBudgetScope.RUN),
        (frozenset({0}), LlmBudgetScope.CANARY),
        (frozenset({0, 5}), LlmBudgetScope.CANARY),
        (frozenset({0, 1, 2}), LlmBudgetScope.RUN),
    ],
)
def test_un_canari_es_una_prova_de_com_a_molt_dos_chunks(only_chunks, scope):
    assert llm_budget.budget_scope_for(only_chunks) is scope


async def test_el_cap_vigent_es_la_config_o_l_ultima_autoritzacio(session, tenant_a):
    user, _ = tenant_a
    config = await llm_budget.cap_usd(session, LlmBudgetScope.PILOT_TOTAL)
    assert (config.cap_usd, config.source) == (Decimal("10"), "config")

    for scope, cap in (
        (LlmBudgetScope.PILOT_TOTAL, "12"),
        (LlmBudgetScope.PILOT_TOTAL, "15.5"),
        (LlmBudgetScope.RUN, "2"),
    ):
        await llm_budget.authorize(
            session, scope=scope, cap_usd=Decimal(cap), user_id=user.id, note="nota"
        )

    total = await llm_budget.cap_usd(session, LlmBudgetScope.PILOT_TOTAL)
    run = await llm_budget.cap_usd(session, LlmBudgetScope.RUN)
    canary = await llm_budget.cap_usd(session, LlmBudgetScope.CANARY)
    assert (total.cap_usd, total.source) == (Decimal("15.5"), "authorization")
    assert (run.cap_usd, run.source) == (Decimal("2"), "authorization")
    assert (canary.cap_usd, canary.source) == (Decimal("1"), "config")


async def test_el_gastat_total_suma_el_llibre(session):
    await _spend(session, "0.25")
    await _spend(session, "1.5")

    assert await llm_budget.spent_usd(session, LlmBudgetScope.PILOT_TOTAL) == Decimal("1.75")


# --------------------------------------------------------------------------- #
# Abans de confirmar (quota)                                                   #
# --------------------------------------------------------------------------- #


async def test_un_cost_estimat_per_sobre_del_cap_de_run_bloqueja_budget_exceeded(
    session, plec3, monkeypatch
):
    monkeypatch.setattr(settings, "LLM_BUDGET_RUN_USD", 0.0001)

    preview = await _preview(session, plec3)

    assert preview.estimated_cost_usd > Decimal("0.0001")
    assert (preview.allowed, preview.blocking_reason) == (False, "budget_exceeded")
    budget = preview.prerequisites["budget"]
    assert (budget["within_run_cap"], budget["within_total_cap"]) == (False, True)
    assert budget["basis"] == "characters"


async def test_gastat_mes_estimat_per_sobre_del_cap_total_bloqueja_budget_exhausted(session, plec3):
    await _spend(session, "10.00")

    preview = await _preview(session, plec3)

    assert (preview.allowed, preview.blocking_reason) == (False, "budget_exhausted")
    assert preview.prerequisites["budget"]["within_total_cap"] is False


async def test_la_quota_d_una_empresa_no_exposa_cap_import_global(
    client_a, tenant_a, plec3, session, monkeypatch
):
    """La despesa i els caps de la plataforma son de l'operador (`GET /platform/...`):
    una empresa nomes veu el seu estimat i, si cal, el motiu del bloqueig."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(settings, "LLM_MODEL", TEST_MODEL)
    await _spend(session, "2.468")

    response = await client_a.post(
        f"/api/v1/companies/{tenant_a[1].id}/tenders/{plec3['document'].tender_id}/analysis-quotes"
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["provider_basis"] == "real" and body["allowed"] is True
    budget = body["prerequisites"]["budget"]
    assert set(budget) == {
        "estimated_cost_usd",
        "estimated_input_tokens",
        "estimated_output_tokens",
        "basis",
        "within_run_cap",
        "within_total_cap",
        "tripped",
    }
    text = response.text
    for forbidden in ("total_spent_usd", "total_cap_usd", "run_cap_usd", "2.468"):
        assert forbidden not in text, forbidden
    stored = (await session.execute(sa.text("SELECT prerequisites FROM analysis_quotes"))).scalar()
    assert "total_spent_usd" not in str(stored)


async def test_l_estimacio_fa_servir_l_historic_si_n_hi_ha(session, plec3):
    await _analyze(session, plec3)

    reuse = await _preview(session, plec3)
    # El run anterior es canonic i es reaprofita: res a enviar, res a pagar.
    assert reuse.chunks_to_send == 0
    assert reuse.estimated_cost_usd == Decimal("0")

    forced = await build_preview(
        session, plec3["document"].tender_id, profile=V1, provider=RealNamedFake(), force=True
    )
    assert forced.prerequisites["budget"]["basis"] == "history"
    assert forced.estimated_cost_usd == CHUNK_COST * 3


# --------------------------------------------------------------------------- #
# La porta unica, abans de cap crida                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        ("not_pinned", "model_not_pinned"),
        ("no_price", "price_unknown"),
        ("estimate_over_cap", "budget_exceeded"),
        ("total_spent", "budget_exhausted"),
    ],
)
async def test_la_porta_refusa_abans_de_reservar_el_run_i_de_cap_crida(
    session, plec3, monkeypatch, setup, code
):
    if setup == "not_pinned":
        pin_real_model(monkeypatch, model="claude-un-altre")
    elif setup == "no_price":
        pin_real_model(monkeypatch, price=False)
    elif setup == "estimate_over_cap":
        monkeypatch.setattr(settings, "LLM_BUDGET_RUN_USD", 0.0001)
    else:
        await _spend(session, "10.5")
    provider = RealNamedFake(responses=_responses(plec3))

    with pytest.raises(AnalysisNotAllowedError) as caught:
        await _analyze(session, plec3, provider=provider)

    assert caught.value.code == code
    assert provider.calls == 0
    assert await session.scalar(sa.select(sa.func.count(RequirementExtractionRun.id))) == 0


async def test_la_porta_refusa_una_simulacio_fora_de_test(session, plec3, monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "local")
    monkeypatch.setattr(settings, "ALLOW_SIMULATED_ANALYSIS", False)

    with pytest.raises(AnalysisNotAllowedError) as caught:
        await _analyze(session, plec3, provider=fake.FakeProvider(responses=_responses(plec3)))

    assert caught.value.code == "provider_not_real"
    assert fake.SENT_PAYLOADS == []


# --------------------------------------------------------------------------- #
# Durant el run                                                                #
# --------------------------------------------------------------------------- #


async def test_cada_chunk_respost_deixa_una_linia_al_llibre(session, plec3):
    run = await _analyze(session, plec3)

    ledger = await _ledger(session)
    assert run.status == AnalysisRunStatus.COMPLETED
    assert [(line.run_id, line.chunk_index) for line in ledger] == [
        (run.id, 0),
        (run.id, 1),
        (run.id, 2),
    ]
    for line in ledger:
        assert (line.provider, line.model) == ("anthropic", TEST_MODEL)
        assert (line.input_tokens, line.output_tokens) == (CHUNK_INPUT_TOKENS, 0)
        assert line.cost_usd == CHUNK_COST
        assert line.budget_scope == LlmBudgetScope.RUN
        assert line.created_at is not None
    assert run.estimated_cost == pytest.approx(1.8)


async def test_una_simulacio_no_escriu_al_llibre(session, plec3):
    await _analyze(session, plec3, provider=fake.FakeProvider(responses=_responses(plec3)))

    assert await _ledger(session) == []


async def test_superar_el_cap_total_atura_el_run_parcial_i_el_pagat_queda_al_llibre(
    session, plec3, monkeypatch
):
    monkeypatch.setattr(settings, "LLM_BUDGET_PILOT_TOTAL_USD", 1.3)
    provider = RealNamedFake(responses=_responses(plec3))

    run = await _analyze(session, plec3, provider=provider)

    # Cap 1,3 (B2: gastat + estimacio del seguent <= cap). Abans del chunk 0: 0 + cota per
    # caracters (minima); de l'1: 0,6 + 0,6 = 1,2 <= 1,3; del 2: 1,2 + 0,6 = 1,8 > 1,3.
    assert provider.calls == 2
    assert run.status == AnalysisRunStatus.PARTIAL
    assert run.error_code == "budget_exceeded"
    assert run.provider_metadata["budget_exceeded_scope"] == "pilot_total"
    assert run.is_current is False
    assert run.chunks_completed == 2
    ledger = await _ledger(session)
    assert [line.chunk_index for line in ledger] == [0, 1]
    assert sum(line.cost_usd for line in ledger) == CHUNK_COST * 2
    statuses = [(c["status"], c["error"]) for c in run.chunk_coverage["chunks"]]
    assert statuses == [("completed", None), ("completed", None), ("failed", "budget_exceeded")]
    assert run.chunk_coverage["chunks"][2]["origin"] == "not_sent"


async def test_superar_el_cap_de_run_atura_el_run(session, plec3, monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_RUN_USD", 0.5)
    provider = RealNamedFake(responses=_responses(plec3))

    run = await _analyze(session, plec3, provider=provider)

    assert provider.calls == 1
    assert (run.status, run.error_code) == (AnalysisRunStatus.PARTIAL, "budget_exceeded")
    assert run.provider_metadata["budget_exceeded_scope"] == "run"


async def test_un_canari_fa_servir_el_cap_de_canari(session, plec3, monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_CANARY_USD", 0.5)
    provider = RealNamedFake(responses=_responses(plec3))

    run = await _analyze(session, plec3, provider=provider, only_chunks=frozenset({0, 1}))

    assert provider.calls == 1
    assert run.error_code == "budget_exceeded"
    assert run.provider_metadata["budget_exceeded_scope"] == "canary"
    assert {line.budget_scope for line in await _ledger(session)} == {LlmBudgetScope.CANARY}


async def test_el_cap_de_run_compta_tota_la_cadena_de_represa(session, plec3, monkeypatch):
    """Canari (0,6 $) + represa: el cap de 1,8 $ es de l'analisi sencera, no de cada fila.

    La porta estima la represa amb l'historic (0,6 $ x 2 chunks = 1,2 $; 0,6 + 1,2 cap
    just). Cada chunk de la represa en costa 1,5: sol, el primer (1,5) cabria al cap i
    se n'enviaria un segon; amb la cadena (0,6 + 1,5 = 2,1 > 1,8), no.
    """
    monkeypatch.setattr(settings, "LLM_BUDGET_RUN_USD", 1.8)
    canary = await _analyze(
        session,
        plec3,
        provider=RealNamedFake(responses=_responses(plec3, pages=(1,))),
        only_chunks=frozenset({0}),
    )
    assert canary.status == AnalysisRunStatus.PARTIAL
    expensive = [
        StructuredResult(
            data={"requirements": [requirement(plec3["extraction"].id, page)]},
            usage=Usage(input_tokens=1_500_000, output_tokens=0),
            model=TEST_MODEL,
        )
        for page in (2, 3)
    ]
    provider = RealNamedFake(responses=expensive)

    resumed = await _analyze(session, plec3, provider=provider, resume_from_run_id=canary.id)

    assert provider.calls == 1
    assert (resumed.status, resumed.error_code) == (AnalysisRunStatus.PARTIAL, "budget_exceeded")
    assert resumed.provider_metadata["budget_exceeded_scope"] == "run"


async def test_la_porta_tambe_compta_la_cadena_de_represa(session, plec3, monkeypatch):
    canary = await _analyze(
        session,
        plec3,
        provider=RealNamedFake(responses=_responses(plec3, pages=(1,))),
        only_chunks=frozenset({0}),
    )
    # Estimacio de la represa: 1,2 $ (cabria sola a 1,5); amb el canari, 1,8 > 1,5.
    monkeypatch.setattr(settings, "LLM_BUDGET_RUN_USD", 1.5)
    provider = RealNamedFake(responses=_responses(plec3, pages=(2, 3)))

    with pytest.raises(AnalysisNotAllowedError) as caught:
        await _analyze(session, plec3, provider=provider, resume_from_run_id=canary.id)

    assert caught.value.code == "budget_exceeded"
    assert provider.calls == 0


async def test_un_error_fatal_amb_usage_tambe_queda_al_llibre(session, plec3):
    from app.ai.provider import ProviderError

    responses = _responses(plec3)
    responses[1] = ProviderError(
        "Compte suspes.",
        "provider_auth",
        usage=Usage(input_tokens=CHUNK_INPUT_TOKENS, output_tokens=0),
        fatal=True,
    )
    run = await _analyze(session, plec3, provider=RealNamedFake(responses=responses))

    assert run.status == AnalysisRunStatus.PARTIAL
    assert [line.chunk_index for line in await _ledger(session)] == [0, 1]


# --------------------------------------------------------------------------- #
# Esgotat i reobertura per scope                                                #
# --------------------------------------------------------------------------- #


async def _stop_by(session, plec3, monkeypatch, scope: str) -> RequirementExtractionRun:
    if scope == "run":
        monkeypatch.setattr(settings, "LLM_BUDGET_RUN_USD", 1.0)
    else:
        monkeypatch.setattr(settings, "LLM_BUDGET_PILOT_TOTAL_USD", 1.0)
    run = await _analyze(session, plec3)
    assert run.error_code == "budget_exceeded"
    assert run.provider_metadata["budget_exceeded_scope"] == scope
    # A partir d'aqui, els caps de config son amples: nomes compta l'esgotament.
    monkeypatch.setattr(settings, "LLM_BUDGET_RUN_USD", 100.0)
    monkeypatch.setattr(settings, "LLM_BUDGET_PILOT_TOTAL_USD", 100.0)
    return run


async def _authorize(session, scope: LlmBudgetScope) -> None:
    await llm_budget.authorize(
        session, scope=scope, cap_usd=Decimal("50"), user_id=None, note="autoritzacio"
    )


async def test_un_run_aturat_pel_cap_de_run_no_es_reobre_amb_un_canari(session, plec3, monkeypatch):
    await _stop_by(session, plec3, monkeypatch, "run")
    assert (await _preview(session, plec3)).blocking_reason == "budget_exhausted"

    await _authorize(session, LlmBudgetScope.CANARY)
    assert (await _preview(session, plec3)).blocking_reason == "budget_exhausted"
    with pytest.raises(AnalysisNotAllowedError) as caught:
        await _analyze(session, plec3, force=True)
    assert caught.value.code == "budget_exhausted"

    await _authorize(session, LlmBudgetScope.RUN)
    reopened = await _preview(session, plec3)
    assert reopened.allowed is True, reopened.prerequisites


async def test_un_run_aturat_pel_cap_de_run_es_reobre_amb_pilot_total(session, plec3, monkeypatch):
    await _stop_by(session, plec3, monkeypatch, "run")

    await _authorize(session, LlmBudgetScope.PILOT_TOTAL)

    assert (await _preview(session, plec3)).allowed is True


async def test_un_run_aturat_pel_cap_total_nomes_es_reobre_amb_pilot_total(
    session, plec3, monkeypatch
):
    await _stop_by(session, plec3, monkeypatch, "pilot_total")

    await _authorize(session, LlmBudgetScope.RUN)
    assert (await _preview(session, plec3)).blocking_reason == "budget_exhausted"

    await _authorize(session, LlmBudgetScope.PILOT_TOTAL)
    assert (await _preview(session, plec3)).allowed is True


# --------------------------------------------------------------------------- #
# Router de l'operador                                                         #
# --------------------------------------------------------------------------- #


async def test_despres_d_un_run_aturat_nomes_l_operador_reobre(
    session, plec3, op_client, client_a, monkeypatch
):
    await _stop_by(session, plec3, monkeypatch, "pilot_total")

    denied = await client_a.post(
        f"{BUDGET_URL}/authorizations",
        json={"scope": "pilot_total", "cap_usd": 20, "note": "intent"},
    )
    assert denied.status_code == 403
    response = await op_client.post(
        f"{BUDGET_URL}/authorizations",
        json={"scope": "pilot_total", "cap_usd": 20, "note": "Ampliacio aprovada pel propietari"},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["scope"], created["cap_usd"]) == ("pilot_total", 20.0)

    reopened = await _preview(session, plec3)
    assert reopened.allowed is True, reopened.prerequisites

    audit = await session.scalar(
        sa.select(AuditLog).where(AuditLog.entity_type == "llm_budget_authorization")
    )
    assert audit is not None and audit.company_id is None
    assert audit.user_id == op_client.operator_id
    authorization = await session.scalar(sa.select(LlmBudgetAuthorization))
    assert authorization.authorized_by == op_client.operator_id
    assert authorization.note == "Ampliacio aprovada pel propietari"


async def test_l_estat_del_pressupost_per_scope(session, plec3, op_client, client_a):
    # Un canari d'un chunk (0,6 $) i, despres, una analisi sencera de 3 chunks (1,8 $).
    await _analyze(
        session,
        plec3,
        provider=RealNamedFake(responses=_responses(plec3, pages=(1,))),
        only_chunks=frozenset({0}),
    )
    await _analyze(session, plec3, force=True)
    await op_client.post(
        f"{BUDGET_URL}/authorizations", json={"scope": "run", "cap_usd": 2.5, "note": "run"}
    )

    response = await op_client.get(BUDGET_URL)

    assert response.status_code == 200, response.text
    body = response.json()
    by_scope = {scope["scope"]: scope for scope in body["scopes"]}
    assert set(by_scope) == {"canary", "run", "pilot_total"}
    assert by_scope["pilot_total"]["spent_usd"] == pytest.approx(2.4)
    assert (by_scope["pilot_total"]["cap_usd"], by_scope["pilot_total"]["cap_source"]) == (
        10.0,
        "config",
    )
    assert by_scope["canary"]["spent_usd"] == pytest.approx(0.6), "nomes els canaris"
    assert (by_scope["run"]["cap_usd"], by_scope["run"]["cap_source"]) == (2.5, "authorization")
    assert by_scope["run"]["spent_usd"] == pytest.approx(1.8), "el run que mes ha gastat"
    assert body["exhausted"] is False
    assert "unpriced_lines" not in body
    assert [line.budget_scope for line in await _ledger(session)] == [
        LlmBudgetScope.CANARY,
        LlmBudgetScope.RUN,
        LlmBudgetScope.RUN,
        LlmBudgetScope.RUN,
    ]
    assert (await client_a.get(BUDGET_URL)).status_code == 403


@pytest.mark.parametrize(
    "payload",
    [
        {"scope": "pilot_total", "cap_usd": -1, "note": "x"},
        {"scope": "pilot_total", "cap_usd": 0, "note": "x"},
        {"scope": "galaxy", "cap_usd": 1, "note": "x"},
        {"scope": "run", "cap_usd": 1, "note": ""},
    ],
)
async def test_una_autoritzacio_invalida_422(op_client, session, payload):
    response = await op_client.post(f"{BUDGET_URL}/authorizations", json=payload)
    assert response.status_code == 422
    assert await session.scalar(sa.select(sa.func.count(LlmBudgetAuthorization.id))) == 0


# --------------------------------------------------------------------------- #
# Vies d'operador: el benchmark passa per la mateixa porta                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("setup", "code"), [("no_price", "price_unknown"), ("exhausted", "budget_exhausted")]
)
async def test_el_benchmark_passa_per_la_porta(session, plec3, monkeypatch, setup, code):
    from app.evaluation.runner import run_case
    from tests.test_benchmark_harness import gold

    if setup == "no_price":
        pin_real_model(monkeypatch, price=False)
    else:
        await _spend(session, "10.5")
    tender = await session.get(Tender, plec3["document"].tender_id)
    case = gold(tender_external_id=tender.external_id).cases[0]
    provider = RealNamedFake(responses=_responses(plec3))

    outcome = await run_case(session, case, provider)

    assert outcome.skipped_reason == f"not_allowed:{code}"
    assert provider.calls == 0


# --------------------------------------------------------------------------- #
# Dades (PostgreSQL)                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
async def test_els_check_del_llibre_i_de_les_autoritzacions_a_postgres(engine):
    from sqlalchemy.exc import DBAPIError

    columns = (
        "id, chunk_index, provider, model, input_tokens, output_tokens, cost_usd, budget_scope"
    )
    for statement in (
        f"INSERT INTO llm_spend_ledger ({columns}) VALUES "
        "(gen_random_uuid(), 0, 'a', 'm', 1, 1, -0.1, 'run')",
        f"INSERT INTO llm_spend_ledger ({columns}) VALUES "
        "(gen_random_uuid(), 0, 'a', 'm', -1, 1, 0, 'run')",
        f"INSERT INTO llm_spend_ledger ({columns}) VALUES "
        "(gen_random_uuid(), 0, 'a', 'm', 1, 1, NULL, 'run')",
        f"INSERT INTO llm_spend_ledger ({columns}) VALUES "
        "(gen_random_uuid(), 0, 'a', 'm', 1, 1, 0, 'pilot_total')",
        "INSERT INTO llm_budget_authorizations (id, scope, cap_usd, note) VALUES "
        "(gen_random_uuid(), 'galaxy', 1, 'x')",
        "INSERT INTO llm_budget_authorizations (id, scope, cap_usd, note) VALUES "
        "(gen_random_uuid(), 'run', 0, 'x')",
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as conn:
                await conn.execute(sa.text(statement))


async def test_el_llibre_no_perd_la_despesa_si_s_esborra_el_run(session, plec3):
    run = await _analyze(session, plec3)
    await session.execute(
        sa.delete(RequirementExtractionRun).where(RequirementExtractionRun.id == run.id)
    )
    await session.commit()

    ledger = await _ledger(session)
    assert len(ledger) == 3 and {line.run_id for line in ledger} == {None}
    assert await llm_budget.spent_usd(session, LlmBudgetScope.PILOT_TOTAL) == CHUNK_COST * 3


async def test_cap_de_run_no_es_supera_mai_el_tercer_chunk_no_s_envia(session, plec3, monkeypatch):
    """Abans de cada chunk: gastat + estimacio del seguent <= cap (B2, revisio final).

    Cap 1,5 $; cada chunk en costa 0,6. Abans del 3r: 1,2 + 0,6 = 1,8 > 1,5. S'atura
    amb 2 pagats (1,2 $) i no se n'envia un tercer que portaria el run a 1,8 $.
    """
    monkeypatch.setattr(settings, "LLM_BUDGET_RUN_USD", 1.5)
    provider = RealNamedFake(responses=_responses(plec3))

    run = await _analyze(session, plec3, provider=provider)

    assert provider.calls == 2
    assert (run.status, run.error_code) == (AnalysisRunStatus.PARTIAL, "budget_exceeded")
    assert run.provider_metadata["budget_exceeded_scope"] == "run"
    ledger = await _ledger(session)
    assert [line.chunk_index for line in ledger] == [0, 1]
    assert sum(line.cost_usd for line in ledger) <= Decimal("1.5")
    assert run.chunk_coverage["chunks"][2]["origin"] == "not_sent"


async def test_cap_total_no_es_supera_mai_el_tercer_chunk_no_s_envia(session, plec3, monkeypatch):
    monkeypatch.setattr(settings, "LLM_BUDGET_PILOT_TOTAL_USD", 1.5)
    provider = RealNamedFake(responses=_responses(plec3))

    run = await _analyze(session, plec3, provider=provider)

    assert provider.calls == 2
    assert run.provider_metadata["budget_exceeded_scope"] == "pilot_total"
    assert sum(line.cost_usd for line in await _ledger(session)) <= Decimal("1.5")
