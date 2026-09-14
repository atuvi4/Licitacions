"""L'adaptador d'Anthropic no pot perdre el consum d'una resposta inservible.

El client es un doble en memoria: aquest fitxer no fa cap crida de xarxa ni
necessita credencial. El que es protegeix es que una resposta sense la tool de
sortida —que el proveidor factura igual— porti l'us i el motiu de parada fins
al servei d'analisi, en lloc de desapareixer amb l'excepcio.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("anthropic")

import anthropic  # noqa: E402
import httpx2  # noqa: E402

from app.ai.provider import (  # noqa: E402
    NoToolCallError,
    OutputTruncatedError,
    ProviderError,
    get_provider,
)
from app.ai.providers.anthropic import _TOOL_NAME, AnthropicProvider  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.evaluation import benchmark  # noqa: E402


class _Messages:
    def __init__(self, response: SimpleNamespace) -> None:
        self._response = response

    async def create(self, **_: object) -> SimpleNamespace:
        return self._response


def _provider_returning(response: SimpleNamespace) -> AnthropicProvider:
    provider = AnthropicProvider()
    # Client injectat: `_ensure_client` no en construeix cap de real.
    provider._client = SimpleNamespace(messages=_Messages(response))
    return provider


def _response(content: list, stop_reason: str) -> SimpleNamespace:
    return SimpleNamespace(
        id="msg_1",
        model="claude-test",
        stop_reason=stop_reason,
        content=content,
        usage=SimpleNamespace(input_tokens=23000, output_tokens=8000),
    )


async def _call(provider: AnthropicProvider):
    return await provider.extract_structured(
        system="s", user_content="u", json_schema={"type": "object"}, max_output_tokens=8000
    )


async def test_resposta_sense_tool_porta_l_us_i_el_motiu_de_parada() -> None:
    provider = _provider_returning(
        _response([SimpleNamespace(type="text", text="...")], stop_reason="end_turn")
    )
    with pytest.raises(NoToolCallError) as caught:
        await _call(provider)

    assert caught.value.code == "no_tool_call"
    usage = caught.value.usage
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (23000, 8000)
    assert usage.request_id == "msg_1"
    assert usage.stop_reason == "end_turn"
    assert "end_turn" in str(caught.value)


async def test_una_resposta_tallada_es_output_truncated_encara_que_validi() -> None:
    """Un tros de JSON que valida no es una extraccio completa."""
    block = SimpleNamespace(type="tool_use", name=_TOOL_NAME, input={"requirements": []})
    provider = _provider_returning(_response([block], stop_reason="max_tokens"))

    with pytest.raises(OutputTruncatedError) as caught:
        await _call(provider)

    assert caught.value.code == "output_truncated"
    assert caught.value.usage.output_tokens == 8000


async def test_tallada_i_sense_tool_tambe_es_output_truncated() -> None:
    provider = _provider_returning(
        _response([SimpleNamespace(type="text", text="...")], stop_reason="max_tokens")
    )
    with pytest.raises(OutputTruncatedError):
        await _call(provider)


async def test_resposta_bona_tambe_registra_el_motiu_de_parada() -> None:
    block = SimpleNamespace(type="tool_use", name=_TOOL_NAME, input={"requirements": []})
    provider = _provider_returning(_response([block], stop_reason="tool_use"))

    result = await _call(provider)

    assert result.data == {"requirements": []}
    assert result.usage.stop_reason == "tool_use"
    assert result.usage.input_tokens == 23000


# --------------------------------------------------------------------------- #
# Reintents: el benchmark de pagament no en fa cap                              #
# --------------------------------------------------------------------------- #


def test_produccio_conserva_la_politica_de_reintents_configurada() -> None:
    assert get_provider("anthropic").max_retries == settings.LLM_MAX_RETRIES


def test_el_benchmark_de_pagament_resol_el_proveidor_sense_reintents(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "clau-de-test-sense-xarxa")
    provider = benchmark.resolve_provider("anthropic")

    assert benchmark.PAID_RUN_MAX_RETRIES == 0
    assert provider.max_retries == 0
    # El client real del SDK es construeix amb la mateixa politica. Construir-lo
    # no fa cap peticio.
    assert provider._ensure_client().max_retries == 0


async def test_sense_reintents_un_error_del_proveidor_es_una_sola_peticio(monkeypatch) -> None:
    """Amb max_retries=0 el SDK no repeteix una peticio que ha fallat amb 500."""
    peticions: list[str] = []

    def _handler(request: httpx2.Request) -> httpx2.Response:
        peticions.append(str(request.url))
        return httpx2.Response(500, json={"type": "error", "error": {"type": "api_error"}})

    real_client = anthropic.AsyncAnthropic

    def _client_amb_transport_local(**kwargs):
        # Transport en memoria: la peticio no surt mai del proces.
        return real_client(**kwargs, http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(_handler)
        ))

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "clau-de-test-sense-xarxa")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", _client_amb_transport_local)
    provider = benchmark.resolve_provider("anthropic")

    with pytest.raises(ProviderError):
        await _call(provider)

    assert len(peticions) == 1, "cap reintent automatic: cada intent es podria pagar"
