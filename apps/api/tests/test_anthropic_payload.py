"""El cos EXACTE que l'adaptador d'Anthropic enviaria, inspeccionat sense sortir del proces.

Transport HTTP en memoria (`httpx2.MockTransport`): cap socket, cap credencial real. Que es
protegeix (canari Haiku 5.5, product-v2.1):

* cap parametre de mostreig (`temperature`, `top_p`, `top_k`) per a cap model;
* `thinking` NOMES per als models de `LLM_THINKING_DISABLED_MODELS`, i nomes `disabled` (la
  tool forcada hi es incompatible); la resta de models (V1 inclos) envien el mateix de sempre;
* model exacte, `max_tokens`, tool forcada amb l'esquema de product-v2.1, timeout i reintents;
* `refusal` i `model_context_window_exceeded` mai es fan servir, encara que portin la tool.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("anthropic")

import anthropic  # noqa: E402
import httpx2  # noqa: E402

from app.ai.profiles import INTERNAL_PROFILES  # noqa: E402
from app.ai.provider import OutputTruncatedError, ProviderError  # noqa: E402
from app.ai.providers.anthropic import _TOOL_NAME, AnthropicProvider  # noqa: E402
from app.core.config import settings  # noqa: E402

pytestmark = pytest.mark.asyncio

HAIKU_55 = "claude-haiku-5-5"
PRODUCT_V2 = INTERNAL_PROFILES["product-v2"]
SAMPLING = {"temperature", "top_p", "top_k"}


def _message(stop_reason: str, *, model: str = HAIKU_55, with_tool: bool = True) -> dict:
    content = (
        [{"type": "tool_use", "id": "toolu_local", "name": _TOOL_NAME,
          "input": {"requirements": [], "award_criteria": [], "uncertainties": []}}]
        if with_tool else [{"type": "text", "text": "No puc respondre."}]
    )  # fmt: skip
    return {
        "id": "msg_local", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1234, "output_tokens": 56},
    }  # fmt: skip


@pytest.fixture
def capture(monkeypatch):
    """Configura l'adaptador amb un transport local i torna (preparador, peticions)."""
    sent: list[dict] = []
    clients: list[dict] = []
    replies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=replies.pop(0), headers={"request-id": "req_local"})

    real_client = anthropic.AsyncAnthropic

    def local_client(**kwargs):
        clients.append({k: v for k, v in kwargs.items() if k != "api_key"})
        return real_client(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        )

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "clau-local-sense-xarxa")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", local_client)

    def prepare(model: str, *, thinking_disabled: list[str], reply: dict) -> AnthropicProvider:
        monkeypatch.setattr(settings, "LLM_MODEL", model)
        monkeypatch.setattr(settings, "LLM_THINKING_DISABLED_MODELS", thinking_disabled)
        replies.append(reply)
        return AnthropicProvider(max_retries=0)

    return prepare, sent, clients


async def _call(provider: AnthropicProvider):
    return await provider.extract_structured(
        system=PRODUCT_V2.system_prompt,
        user_content="<DOCUMENT>...</DOCUMENT>",
        json_schema=PRODUCT_V2.json_schema(),
        max_output_tokens=settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS,
    )


async def test_haiku_55_envia_model_exacte_tool_forcada_i_thinking_desactivat(capture):
    prepare, sent, clients = capture
    provider = prepare(HAIKU_55, thinking_disabled=[HAIKU_55], reply=_message("tool_use"))
    result = await _call(provider)

    (body,) = sent
    assert body["model"] == HAIKU_55
    assert body["max_tokens"] == 8000
    assert body["tool_choice"] == {"type": "tool", "name": _TOOL_NAME}
    assert [tool["name"] for tool in body["tools"]] == [_TOOL_NAME]
    assert body["tools"][0]["input_schema"] == PRODUCT_V2.json_schema()
    assert body["thinking"] == {"type": "disabled"}
    assert not SAMPLING & set(body)
    assert set(body) == {
        "model",
        "max_tokens",
        "system",
        "messages",
        "tools",
        "tool_choice",
        "thinking",
    }
    # Client: timeout de configuracio i cap reintent automatic.
    assert (
        clients[-1]["timeout"] == settings.LLM_TIMEOUT_SECONDS and clients[-1]["max_retries"] == 0
    )
    # Resposta: dades de la tool, us i model servit tal com venen.
    assert result.model == HAIKU_55 and result.usage.stop_reason == "tool_use"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1234, 56)
    assert set(result.data) == {"requirements", "award_criteria", "uncertainties"}


@pytest.mark.parametrize("model", ["claude-haiku-4-5-20251001", "claude-test-model"])
async def test_la_resta_de_models_envien_exactament_el_mateix_cos_de_sempre(capture, model):
    prepare, sent, _clients = capture
    provider = prepare(model, thinking_disabled=[HAIKU_55], reply=_message("tool_use", model=model))
    await _call(provider)
    (body,) = sent
    assert set(body) == {"model", "max_tokens", "system", "messages", "tools", "tool_choice"}
    assert not SAMPLING & set(body)


@pytest.mark.parametrize("stop_reason", ["model_context_window_exceeded", "max_tokens"])
async def test_una_resposta_tallada_mai_es_fa_servir_encara_que_porti_la_tool(capture, stop_reason):
    prepare, _sent, _clients = capture
    provider = prepare(HAIKU_55, thinking_disabled=[HAIKU_55], reply=_message(stop_reason))
    with pytest.raises(OutputTruncatedError) as caught:
        await _call(provider)
    assert caught.value.usage.stop_reason == stop_reason
    assert caught.value.usage.input_tokens == 1234  # l'us es conserva: es paga igual


@pytest.mark.parametrize("with_tool", [True, False])
async def test_un_refus_mai_es_fa_servir(capture, with_tool):
    prepare, _sent, _clients = capture
    provider = prepare(HAIKU_55, thinking_disabled=[HAIKU_55],
                       reply=_message("refusal", with_tool=with_tool))  # fmt: skip
    with pytest.raises(ProviderError) as caught:
        await _call(provider)
    assert caught.value.code == "refusal" and caught.value.usage.output_tokens == 56


async def test_la_configuracio_per_defecte_no_desactiva_el_thinking_de_cap_model():
    assert settings.LLM_THINKING_DISABLED_MODELS == []


async def test_el_cos_dels_altres_models_es_exactament_l_anterior(capture):
    """Equivalencia estructural completa amb el cos de 6d13b09 (abans d'aquest canvi)."""
    prepare, sent, _clients = capture
    model = "claude-haiku-4-5-20251001"
    provider = prepare(model, thinking_disabled=[HAIKU_55], reply=_message("tool_use", model=model))
    await _call(provider)
    (body,) = sent
    assert body == {
        "model": model,
        "max_tokens": 8000,
        "system": PRODUCT_V2.system_prompt,
        "messages": [{"role": "user", "content": "<DOCUMENT>...</DOCUMENT>"}],
        "tools": [
            {
                "name": _TOOL_NAME,
                "description": (
                    "Registra els requisits i criteris trobats al fragment. "
                    "Utilitza NOMES informacio explicita del text proporcionat."
                ),
                "input_schema": PRODUCT_V2.json_schema(),
            }
        ],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
    }


async def test_el_model_servit_es_torna_tal_com_ve_sense_normalitzar(capture):
    """L'adaptador no substitueix ni normalitza l'ID; si el model servit es un altre, el
    resultat ho diu tal qual (l'arnes del canari ho tracta com a HARD STOP)."""
    prepare, sent, _clients = capture
    provider = prepare(HAIKU_55, thinking_disabled=[HAIKU_55],
                       reply=_message("tool_use", model="claude-haiku-5-5-altre"))  # fmt: skip
    result = await _call(provider)
    assert sent[0]["model"] == HAIKU_55
    assert result.model == "claude-haiku-5-5-altre"
