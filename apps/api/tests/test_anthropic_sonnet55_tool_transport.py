"""Claude Sonnet 5.5 amb tool NO estricta (post-mortem dels Attempts 5 i 6). Cap socket, cap clau.

El servidor va refusar la gramatica estricta v1 i v2 («The compiled grammar is too large»).
Sonnet 5.5 va ara amb `tool_choice: auto`, UNA tool `record_extraction` SENSE `strict`, amb
l'esquema CANONIC de product-v2 com a guia, `thinking: between_tools` i `max_tokens` 8000. El
servidor ja no garanteix la forma: la garanteix el parser product-v2.2, igual que sempre.

non-strict transport != validacio de producte mes fluixa. Que es protegeix:

1-6, 12. La resposta de la tool passa pel MATEIX pipeline product-v2.2: un element mal format
   (camps incorrectes, enum inventat, pagina 0, incertesa incorrecta) es rebutja sol; una cita
   no literal es rebutja; els elements valids sobreviuen.
7-11. Cap crida, JSON dins del text, dues crides, una tool equivocada o arguments que no son un
   objecte: FATAL, cap reintent, cap text llegit.
13. Els altres models (Haiku 5.5, Haiku 4.5, V1) envien el mateix cos de sempre.
14. Un run d'un transport estricte (v1 o v2) mai es repren amb el transport de tool, ni a
   l'inreves; la identitat de l'analisi distingeix els tres transports.
"""

from __future__ import annotations

import dataclasses
import json
import uuid

import pytest

pytest.importorskip("anthropic")

import anthropic  # noqa: E402
import httpx2  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from app.ai.profiles import (  # noqa: E402
    INTERNAL_PROFILES,
    TOOL_TRANSPORT_UNAVAILABLE,
    TRANSPORT_METADATA_KEY,
    V1,
    auto_tool_envelope_problem,
    transport_identity,
    transport_json_schema,
)
from app.ai.provider import (  # noqa: E402
    MalformedToolArgumentsError,
    MultipleToolCallsError,
    NoToolCallError,
    OutputTruncatedError,
    ProviderError,
    StructuredResult,
    UnexpectedToolCallError,
    Usage,
)
from app.ai.providers import fake  # noqa: E402
from app.ai.providers.anthropic import (  # noqa: E402
    _TOOL_NAME,
    AUTO_TOOL_INSTRUCTION,
    AUTO_TOOL_TRANSPORT_VERSION,
    AnthropicProvider,
)
from app.ai.schemas_product_v2 import (  # noqa: E402
    TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT,
    TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_TOOL,
    parse_chunk_product_v2,
)
from app.core.config import settings  # noqa: E402
from app.core.errors import AnalysisNotAllowedError, ResumeNotAllowedError  # noqa: E402
from app.models.enums import AnalysisRunStatus  # noqa: E402
from app.models.llm_budget import LlmSpendLedger  # noqa: E402
from app.models.requirements import Requirement, RequirementExtractionRun  # noqa: E402
from app.services.requirement_analysis import analysis_identity, plan_resume  # noqa: E402
from tests.quota_support import pin_real_model, requirement  # noqa: E402
from tests.real_provider import RealNamedFake  # noqa: E402
from tests.test_llm_budget import _analyze  # noqa: E402
from tests.test_llm_budget import plec3 as plec3_fixture  # noqa: E402

#: Tender public de 3 pagines i 3 chunks (fixture de `test_llm_budget`).
plec3 = plec3_fixture

SONNET_55 = "claude-sonnet-5-5"
HAIKU_55 = "claude-haiku-5-5"
HAIKU_45 = "claude-haiku-4-5-20251001"
PRODUCT_V2 = INTERNAL_PROFILES["product-v2"]
TOOL_DESCRIPTION = (
    "Registra els requisits i criteris trobats al fragment. "
    "Utilitza NOMES informacio explicita del text proporcionat."
)
EMPTY = {"requirements": [], "award_criteria": [], "document_notes": [], "uncertainties": []}


@pytest.fixture(autouse=True)
def _clean_fake():
    """`fake.SENT_PAYLOADS` es global: cada prova comenca i acaba neta."""
    fake.reset()
    yield
    fake.reset()


def _configure(monkeypatch, *, strict: bool = False) -> None:
    """Sonnet 5.5 com a l'Attempt 7: `auto`, sense `strict` (llevat que es demani)."""
    monkeypatch.setattr(settings, "LLM_AUTO_TOOL_CHOICE_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_STRICT_TOOL_MODELS", [SONNET_55] if strict else [])
    monkeypatch.setattr(settings, "LLM_THINKING_BETWEEN_TOOLS_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_THINKING_DISABLED_MODELS", [HAIKU_55])


def _tool(**overrides) -> dict:
    return {"type": "tool_use", "id": "toolu_local", "name": _TOOL_NAME, "input": EMPTY} | overrides


def _message(stop_reason: str, content: list[dict], *, model: str = SONNET_55) -> dict:
    return {
        "id": "msg_local", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1234, "output_tokens": 56},
    }  # fmt: skip


@pytest.fixture
def sonnet(monkeypatch):
    sent: list[dict] = []
    replies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=replies.pop(0), headers={"request-id": "req_local"})

    real_client = anthropic.AsyncAnthropic

    def local_client(**kwargs):
        return real_client(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        )

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "clau-local-sense-xarxa")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", local_client)
    _configure(monkeypatch)

    def prepare(model: str = SONNET_55, *, reply: dict | None = None) -> AnthropicProvider:
        monkeypatch.setattr(settings, "LLM_MODEL", model)
        if reply is not None:
            replies.append(reply)
        return AnthropicProvider(max_retries=0)

    return prepare, sent


async def _call(provider: AnthropicProvider):
    return await provider.extract_structured(
        system=PRODUCT_V2.system_prompt,
        user_content="<fragment local>",
        json_schema=PRODUCT_V2.json_schema(),
        max_output_tokens=settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS,
    )


# --------------------------------------------------------------------------- #
# Cos HTTP                                                                      #
# --------------------------------------------------------------------------- #


async def test_sonnet_55_envia_la_tool_auto_sense_strict_amb_l_esquema_canonic(sonnet):
    prepare, sent = sonnet
    result = await _call(prepare(reply=_message("tool_use", [_tool()])))

    assert sent == [
        {
            "model": SONNET_55,
            "max_tokens": 8000,
            "system": PRODUCT_V2.system_prompt.rstrip("\n") + AUTO_TOOL_INSTRUCTION,
            "messages": [{"role": "user", "content": "<fragment local>"}],
            "tools": [
                {
                    "name": _TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "input_schema": PRODUCT_V2.json_schema(),  # el CANONIC, no l'estricte
                }
            ],
            "tool_choice": {"type": "auto"},
            "thinking": {"type": "between_tools"},
        }
    ]
    assert "strict" not in sent[0]["tools"][0]
    assert (result.data, result.model) == (EMPTY, SONNET_55)


# --------------------------------------------------------------------------- #
# 7-11: hard stops (FATALS, cap reintent)                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("content", "error"),
    [
        ([{"type": "text", "text": "No hi ha res a extreure."}], NoToolCallError),  # 7
        ([{"type": "text", "text": json.dumps(EMPTY)}], NoToolCallError),  # 8: JSON al text
        ([_tool(), _tool(id="toolu_2")], MultipleToolCallsError),  # 9
        ([_tool(name="other_tool")], UnexpectedToolCallError),  # 10
        ([_tool(), _tool(id="toolu_2", name="other_tool")], UnexpectedToolCallError),  # 10
        ([_tool(input="no es un objecte")], MalformedToolArgumentsError),  # 11
        ([_tool(input=["requirements"])], MalformedToolArgumentsError),  # 11
    ],
)
async def test_tot_el_que_no_es_una_crida_valida_a_la_tool_es_fatal(sonnet, content, error):
    prepare, sent = sonnet
    stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
    with pytest.raises(error) as caught:
        await _call(prepare(reply=_message(stop, content)))
    assert caught.value.fatal is True
    assert caught.value.usage.output_tokens == 56  # l'us es conserva (es paga)
    assert len(sent) == 1


@pytest.mark.parametrize(
    ("stop_reason", "error", "code"),
    [
        ("refusal", ProviderError, "refusal"),
        ("max_tokens", OutputTruncatedError, "output_truncated"),
        ("model_context_window_exceeded", OutputTruncatedError, "output_truncated"),
    ],
)
async def test_refus_i_talls_son_fatals_sense_fer_servir_la_resposta(
    sonnet, stop_reason, error, code
):
    prepare, _sent = sonnet
    with pytest.raises(error) as caught:
        await _call(prepare(reply=_message(stop_reason, [_tool()])))
    assert (caught.value.code, caught.value.fatal) == (code, True)
    assert caught.value.usage.output_tokens == 56


@pytest.mark.parametrize("model", [HAIKU_55, HAIKU_45, "claude-test-model"])
async def test_13_els_altres_models_no_canvien_ni_el_cos_ni_els_errors(sonnet, model):
    prepare, sent = sonnet
    await _call(prepare(model, reply=_message("tool_use", [_tool()], model=model)))
    expected = {
        "model": model,
        "max_tokens": 8000,
        "system": PRODUCT_V2.system_prompt,
        "messages": [{"role": "user", "content": "<fragment local>"}],
        "tools": [
            {
                "name": _TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "input_schema": PRODUCT_V2.json_schema(),
            }
        ],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
    }
    if model == HAIKU_55:
        expected["thinking"] = {"type": "disabled"}
    assert sent == [expected]
    # Un refus o un tall d'aquests models continua sense ser fatal (com sempre).
    for stop_reason in ("refusal", "max_tokens"):
        with pytest.raises(ProviderError) as caught:
            await _call(prepare(model, reply=_message(stop_reason, [_tool()], model=model)))
        assert caught.value.fatal is False


# --------------------------------------------------------------------------- #
# 1-6, 12: validacio product-v2.2 despres de la tool                            #
# --------------------------------------------------------------------------- #


def _requirement(**overrides) -> dict:
    evidence = {"extraction_id": "e1", "page_number": 3, "quote": "Cal una polissa."}
    return {"category": "insurance", "title": "RC", "obligation": "mandatory",
            "evidence": [evidence]} | overrides  # fmt: skip


def test_1_una_tool_perfecta_s_accepta_al_contracte_canonic():
    payload = {**EMPTY, "requirements": [_requirement()],
               "uncertainties": [{"scope": "solvency", "extraction_id": "e1", "page_number": 5,
                                  "quote": None, "note": "Pendent de concretar."}]}  # fmt: skip
    parsed = parse_chunk_product_v2(payload)
    assert len(parsed.requirements) == 1 and len(parsed.uncertainties) == 1
    assert not parsed._schema_rejections and not parsed._uncertainty_rejections


@pytest.mark.parametrize(
    "bad",
    [
        _requirement(title=""),  # 2: arguments parcialment incorrectes
        _requirement(obligation=["mandatory"]),  # 2
        _requirement(category="submission_documentation"),  # 3: enum d'un altre vocabulari
        _requirement(category="certificacio_inventada"),  # 3: enum inventat
        _requirement(evidence=[{"extraction_id": "e1", "page_number": 0, "quote": "x"}]),  # 4
    ],
)
def test_2_3_4_12_un_element_mal_format_es_rebutja_sol(bad):
    payload = {**EMPTY, "requirements": [_requirement(title="Valid"), bad]}
    parsed = parse_chunk_product_v2(payload)
    assert [r.title for r in parsed.requirements] == ["Valid"]  # 12: el valid sobreviu
    assert len(parsed._schema_rejections) == 1


@pytest.mark.parametrize(
    "bad",
    [
        {"scope": "eligibility", "extraction_id": "e1", "page_number": 5, "note": "x"},
        {"scope": "solvency", "extraction_id": "e1", "page_number": 5, "note": ""},
        {"scope": "solvency", "extraction_id": "e1", "page_number": 0, "note": "x"},
        {"scope": "solvency", "extraction_id": "e1", "page_number": 5, "note": "x" * 501},
    ],
)
def test_6_una_incertesa_incorrecta_es_rebutja_i_la_valida_sobreviu(bad):
    good = {"scope": "solvency", "extraction_id": "e1", "page_number": 5, "note": "Pendent."}
    parsed = parse_chunk_product_v2({**EMPTY, "uncertainties": [good, bad]})
    assert len(parsed.uncertainties) == 1 and len(parsed._uncertainty_rejections) == 1


async def test_5_12_la_canonada_rebutja_cites_no_literals_i_elements_dolents(
    session, plec3, monkeypatch
):
    """De cap a cap amb el proveidor de Sonnet 5.5 (sense `strict`): el chunk 0 porta un requisit
    valid, un amb la cita alterada, un amb un enum inventat i una incertesa amb un ambit que no
    existeix. Nomes el valid es desa."""
    pin_real_model(monkeypatch, model=SONNET_55, input_per_million=2.0, output_per_million=10.0)
    _configure(monkeypatch)
    extraction = plec3["extraction"].id
    good = requirement(extraction, 1)
    altered = requirement(extraction, 1)
    altered["title"] = "Cita alterada"
    altered["evidence"] = [{**altered["evidence"][0], "quote": "Text que no es a la pagina"}]
    invented = {**requirement(extraction, 1), "title": "Enum inventat", "category": "inventada"}
    wrong_scope = {"scope": "eligibility", "extraction_id": str(extraction), "page_number": 1,
                   "note": "Ambit inexistent."}  # fmt: skip
    chunk0 = {**EMPTY, "requirements": [good, altered, invented], "uncertainties": [wrong_scope]}
    usage = Usage(input_tokens=1000, output_tokens=100, request_id="r", stop_reason="tool_use")
    responses = [StructuredResult(data=chunk0, usage=usage, model=SONNET_55)] + [
        StructuredResult(data=EMPTY, usage=usage, model=SONNET_55) for _ in range(2)
    ]
    provider = RealNamedFake(responses=responses, model=SONNET_55)

    run = await _analyze(session, plec3, provider=provider, profile=PRODUCT_V2)

    titles = (
        await session.scalars(sa.select(Requirement.title).where(Requirement.run_id == run.id))
    ).all()
    assert list(titles) == [good["title"]]  # 12: el valid sobreviu
    counts = (run.requirements_extracted, run.requirements_rejected, run.evidence_rejected)
    assert counts == (1, 2, 1)
    chunks = run.chunk_coverage["chunks"]
    assert [(r["item"], r["index"], r["stage"], r["reason"]) for r in chunks[0]["rejections"]] == [
        ("requirement", 2, "schema", "schema_invalid"),  # 3: enum inventat
        ("requirement", 1, "evidence", "quote_not_found"),  # 5: cita no literal
        ("requirement", 1, "item", "no_valid_evidence"),
        ("uncertainty", 0, "schema", "schema_invalid"),  # 6: ambit inexistent
    ]
    assert [c.get("rejections") for c in chunks[1:]] == [None, None]
    # L'esquema que ha viatjat es el canonic (sense `strict`, cap esquema de transport).
    assert [p["schema"] for p in fake.SENT_PAYLOADS] == [PRODUCT_V2.json_schema()] * 3
    assert run.provider_metadata[TRANSPORT_METADATA_KEY] == {
        "transport": AUTO_TOOL_TRANSPORT_VERSION,
        "strict": False,
        "schema": TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_TOOL,
        "thinking": "between_tools",
    }


# --------------------------------------------------------------------------- #
# 14: identitat i represa entre transports                                      #
# --------------------------------------------------------------------------- #


def _provider(model: str = SONNET_55):
    return type("P", (), {"name": "anthropic", "model": model})()


def test_14_la_identitat_distingeix_estricte_v1_estricte_v2_i_tool(monkeypatch):
    _configure(monkeypatch)
    tool = transport_identity(PRODUCT_V2, _provider())
    assert tool == {
        "transport": AUTO_TOOL_TRANSPORT_VERSION,
        "strict": False,
        "schema": TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_TOOL,
        "thinking": "between_tools",
    }
    assert transport_json_schema(PRODUCT_V2, _provider()) == PRODUCT_V2.json_schema()
    _configure(monkeypatch, strict=True)
    strict_v2 = transport_identity(PRODUCT_V2, _provider())
    v1_profile = dataclasses.replace(
        PRODUCT_V2, strict_transport_version="requirements-transport-product-v2.1-strict"
    )
    strict_v1 = transport_identity(v1_profile, _provider())
    assert strict_v2["schema"] == TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT
    assert len({json.dumps(t, sort_keys=True) for t in (tool, strict_v1, strict_v2)}) == 3
    identities = set()
    for strict, profile in ((False, PRODUCT_V2), (True, PRODUCT_V2), (True, v1_profile)):
        _configure(monkeypatch, strict=strict)
        identities.add(analysis_identity([], profile=profile, provider=_provider()))
    assert len(identities) == 3


def _partial_run(transport: dict | None) -> RequirementExtractionRun:
    return RequirementExtractionRun(
        tender_id=uuid.UUID(int=1), status=AnalysisRunStatus.PARTIAL, input_fingerprint="f" * 64,
        extractor_version=PRODUCT_V2.extractor_version, prompt_version=PRODUCT_V2.prompt_version,
        schema_version=PRODUCT_V2.schema_version, provider="anthropic", model=SONNET_55,
        provider_metadata={TRANSPORT_METADATA_KEY: transport} if transport else None,
    )  # fmt: skip


def _resume(source: RequirementExtractionRun) -> str:
    try:
        plan_resume(source, tender_id=uuid.UUID(int=1), fingerprint="f" * 64, chunks=[],
                    provider=_provider(), profile=PRODUCT_V2)  # fmt: skip
    except ResumeNotAllowedError as exc:
        return exc.details["reason"]
    return "allowed"


def test_14_un_run_estricte_no_es_repren_amb_el_transport_de_tool_ni_a_l_inreves(monkeypatch):
    _configure(monkeypatch, strict=True)
    strict_v2 = transport_identity(PRODUCT_V2, _provider())
    v1_profile = dataclasses.replace(
        PRODUCT_V2, strict_transport_version="requirements-transport-product-v2.1-strict"
    )
    strict_v1 = transport_identity(v1_profile, _provider())
    _configure(monkeypatch)  # ara, tool no estricta
    tool = transport_identity(PRODUCT_V2, _provider())
    assert _resume(_partial_run(strict_v1)) == "transport_mismatch"
    assert _resume(_partial_run(strict_v2)) == "transport_mismatch"
    assert _resume(_partial_run(None)) == "transport_mismatch"  # run sense transport desat
    # El mateix transport passa aquesta comprovacio (i s'atura despres, al pla de chunks buit).
    assert _resume(_partial_run(tool)) == "chunk_plan_mismatch"
    _configure(monkeypatch, strict=True)
    assert _resume(_partial_run(tool)) == "transport_mismatch"


async def test_la_porta_refusa_un_perfil_sense_transport_de_tool(session, plec3, monkeypatch):
    pin_real_model(monkeypatch, model=SONNET_55, input_per_million=2.0, output_per_million=10.0)
    _configure(monkeypatch)
    provider = RealNamedFake(model=SONNET_55)
    with pytest.raises(AnalysisNotAllowedError) as caught:
        await _analyze(session, plec3, provider=provider, profile=V1)
    assert caught.value.code == TOOL_TRANSPORT_UNAVAILABLE
    assert provider.calls == 0


# --------------------------------------------------------------------------- #
# Revisio independent: model servit, motiu de parada, envoltori, cap a cap      #
# --------------------------------------------------------------------------- #


async def test_un_model_servit_diferent_es_fatal_i_no_es_fa_servir(sonnet):
    prepare, _sent = sonnet
    reply = _message("tool_use", [_tool()], model="claude-sonnet-5")
    with pytest.raises(ProviderError) as caught:
        await _call(prepare(reply=reply))
    assert (caught.value.code, caught.value.fatal) == ("served_model_mismatch", True)
    assert caught.value.usage.output_tokens == 56


async def test_els_models_forcats_no_canvien_amb_un_model_servit_diferent(sonnet):
    prepare, _sent = sonnet  # com sempre: el model servit es torna, l'arnes el compara
    reply = _message("tool_use", [_tool()], model="claude-haiku-4-5")
    result = await _call(prepare(HAIKU_45, reply=reply))
    assert result.model == "claude-haiku-4-5"


@pytest.mark.parametrize("stop_reason", ["end_turn", "pause_turn", None])
async def test_una_crida_valida_amb_un_motiu_de_parada_que_no_es_la_tool_es_fatal(
    sonnet, stop_reason
):
    prepare, _sent = sonnet
    with pytest.raises(ProviderError) as caught:
        await _call(prepare(reply=_message(stop_reason, [_tool()])))
    assert (caught.value.code, caught.value.fatal) == ("unexpected_stop_reason", True)


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({}, "falten"),
        ({**EMPTY, "requirements": "[]"}, "no son llistes"),
        ({**EMPTY, "requirements": None}, "no son llistes"),
        ({**EMPTY, "extraction_id": "e1"}, "claus inesperades"),
        ({k: v for k, v in EMPTY.items() if k != "document_notes"}, "falten"),
        ([], "no es un objecte"),
    ],
)
def test_l_envoltori_ha_de_ser_exactament_el_canonic(data, problem):
    assert problem in auto_tool_envelope_problem(data, PRODUCT_V2)
    assert auto_tool_envelope_problem(EMPTY, PRODUCT_V2) is None


@pytest.mark.parametrize(
    "envelope",
    [{}, {**EMPTY, "requirements": "[]"}, {**EMPTY, "requirements": None},
     {**EMPTY, "extraction_id": "e1"}],
)  # fmt: skip
async def test_un_envoltori_il_legal_atura_el_run_i_no_compta_com_a_chunk_fet(
    session, plec3, monkeypatch, envelope
):
    pin_real_model(monkeypatch, model=SONNET_55, input_per_million=2.0, output_per_million=10.0)
    _configure(monkeypatch)
    usage = Usage(input_tokens=1000, output_tokens=10, request_id="r", stop_reason="tool_use")
    responses = [StructuredResult(data=envelope, usage=usage, model=SONNET_55)] * 3
    provider = RealNamedFake(responses=responses, model=SONNET_55)

    run = await _analyze(session, plec3, provider=provider, profile=PRODUCT_V2)

    assert provider.calls == 1  # fatal: cap chunk mes
    chunks = run.chunk_coverage["chunks"]
    assert [c.get("error") for c in chunks] == ["illegal_envelope", "not_sent", "not_sent"]
    assert run.requirements_extracted == 0
    assert run.status == AnalysisRunStatus.FAILED
    ledger = (
        await session.scalars(
            sa.select(LlmSpendLedger.chunk_index).where(LlmSpendLedger.run_id == run.id)
        )
    ).all()
    assert list(ledger) == [0]  # la resposta inservible s'ha pagat igual


async def test_de_cap_a_cap_amb_l_adaptador_real_i_http_local(sonnet, session, plec3, monkeypatch):
    """L'adaptador d'Anthropic (HTTP en memoria) dins de la canonada: chunk 0 valid i desat;
    chunk 1 sense tool -> FATAL (pagat, al llibre); chunk 2 no s'envia mai."""
    prepare, sent = sonnet
    pin_real_model(monkeypatch, model=SONNET_55, input_per_million=2.0, output_per_million=10.0)
    extraction = plec3["extraction"].id
    first = _tool(input={**EMPTY, "requirements": [requirement(extraction, 1)]})
    provider = prepare(reply=_message("tool_use", [first]))
    prepare(reply=_message("end_turn", [{"type": "text", "text": json.dumps(EMPTY)}]))

    run = await _analyze(session, plec3, provider=provider, profile=PRODUCT_V2)

    assert len(sent) == 2 and "strict" not in sent[0]["tools"][0]
    chunks = run.chunk_coverage["chunks"]
    assert [c.get("error") for c in chunks] == [None, "no_tool_call", "not_sent"]
    assert run.requirements_extracted == 1
    ledger = (
        await session.scalars(
            sa.select(LlmSpendLedger.chunk_index).where(LlmSpendLedger.run_id == run.id)
        )
    ).all()
    assert sorted(ledger) == [0, 1]  # la resposta sense tool tambe s'ha pagat


async def test_un_run_d_un_model_forcat_no_desa_cap_transport(session, plec3):
    run = await _analyze(session, plec3)
    assert TRANSPORT_METADATA_KEY not in (run.provider_metadata or {})


async def test_el_transport_neix_amb_el_run_i_la_represa_el_compara(session, plec3, monkeypatch):
    """El transport es desa en reservar el run (abans de cap chunk): un run perdut abans de
    tancar-se tambe el porta. Represa amb el mateix transport: si; amb l'estricte: no."""
    pin_real_model(monkeypatch, model=SONNET_55, input_per_million=2.0, output_per_million=10.0)
    _configure(monkeypatch)
    extraction = plec3["extraction"].id
    usage = Usage(input_tokens=1000, output_tokens=10, request_id="r", stop_reason="tool_use")
    seen: list = []

    async def inspect(index: int) -> None:
        if index == 0:
            running = await session.scalar(sa.select(RequirementExtractionRun))
            seen.append((running.status, (running.provider_metadata or {}).get("transport")))

    stop = NoToolCallError("cap crida", usage=usage)
    stop.fatal = True
    ok = StructuredResult(
        data={**EMPTY, "requirements": [requirement(extraction, 1)]}, usage=usage, model=SONNET_55
    )
    first = RealNamedFake(responses=[ok, stop], model=SONNET_55, before_call=inspect)
    run1 = await _analyze(session, plec3, provider=first, profile=PRODUCT_V2)
    tool = transport_identity(PRODUCT_V2, first)
    assert seen == [(AnalysisRunStatus.RUNNING, tool)]
    assert run1.status == AnalysisRunStatus.PARTIAL

    _configure(monkeypatch, strict=True)
    with pytest.raises(ResumeNotAllowedError) as caught:
        await _analyze(session, plec3, provider=RealNamedFake(model=SONNET_55),
                       profile=PRODUCT_V2, resume_from_run_id=run1.id)  # fmt: skip
    assert caught.value.details["reason"] == "transport_mismatch"

    _configure(monkeypatch)
    empty = StructuredResult(data=EMPTY, usage=usage, model=SONNET_55)
    second = RealNamedFake(responses=[empty, empty], model=SONNET_55)
    run2 = await _analyze(session, plec3, provider=second, profile=PRODUCT_V2,
                          resume_from_run_id=run1.id)  # fmt: skip
    assert second.calls == 2  # chunk 0 reaprofitat, no es torna a enviar
    assert run2.provider_metadata[TRANSPORT_METADATA_KEY] == tool


def test_el_missatge_d_envoltori_no_repeteix_text_inventat_pel_model():
    invented = "Clàusula 8. Classificació empresarial exigible"
    problem = auto_tool_envelope_problem({**EMPTY, invented: [], "extra_key": []}, PRODUCT_V2)
    assert invented not in problem and "<text," in problem and "extra_key" in problem


async def test_cap_normalitzacio_indulgent_per_als_models_auto(session, plec3, monkeypatch):
    """Una evidencia serialitzada com a text JSON: amb la tool forcada es descodifica (com
    sempre); amb un model `auto` no, i el requisit es rebutja (cap JSON llegit d'un text)."""
    pin_real_model(monkeypatch, model=SONNET_55, input_per_million=2.0, output_per_million=10.0)
    _configure(monkeypatch)
    extraction = plec3["extraction"].id
    item = requirement(extraction, 1)
    item["evidence"] = json.dumps(item["evidence"])
    usage = Usage(input_tokens=1000, output_tokens=10, request_id="r", stop_reason="tool_use")
    chunk0 = {**EMPTY, "requirements": [item]}
    responses = [StructuredResult(data=chunk0, usage=usage, model=SONNET_55)] + [
        StructuredResult(data=EMPTY, usage=usage, model=SONNET_55) for _ in range(2)
    ]
    run = await _analyze(
        session, plec3, provider=RealNamedFake(responses=responses, model=SONNET_55),
        profile=PRODUCT_V2,
    )  # fmt: skip
    assert run.requirements_extracted == 0
    rejection = run.chunk_coverage["chunks"][0]["rejections"][0]
    assert (rejection["item"], rejection["reason"], rejection["field"]) == (
        "requirement", "schema_invalid", "evidence",
    )  # fmt: skip


@pytest.mark.parametrize(
    ("values", "ok"),
    [
        ({"LLM_AUTO_TOOL_CHOICE_MODELS": [SONNET_55], "LLM_STRICT_TOOL_MODELS": [SONNET_55]}, True),
        ({"LLM_STRICT_TOOL_MODELS": [SONNET_55]}, False),
        ({"LLM_THINKING_BETWEEN_TOOLS_MODELS": [SONNET_55],
          "LLM_THINKING_DISABLED_MODELS": [SONNET_55]}, False),
    ],
)  # fmt: skip
def test_la_configuracio_incoherent_fa_soroll(values, ok):
    from pydantic import ValidationError

    from app.core.config import Settings

    if ok:
        Settings(**values)
    else:
        with pytest.raises(ValidationError):
            Settings(**values)
