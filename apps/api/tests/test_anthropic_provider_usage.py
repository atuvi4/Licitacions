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

from app.ai.provider import InvalidStructuredOutputError  # noqa: E402
from app.ai.providers.anthropic import _TOOL_NAME, AnthropicProvider  # noqa: E402


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
        _response([SimpleNamespace(type="text", text="...")], stop_reason="max_tokens")
    )
    with pytest.raises(InvalidStructuredOutputError) as caught:
        await _call(provider)

    usage = caught.value.usage
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (23000, 8000)
    assert usage.request_id == "msg_1"
    assert usage.stop_reason == "max_tokens"
    assert "max_tokens" in str(caught.value)


async def test_resposta_bona_tambe_registra_el_motiu_de_parada() -> None:
    block = SimpleNamespace(type="tool_use", name=_TOOL_NAME, input={"requirements": []})
    provider = _provider_returning(_response([block], stop_reason="tool_use"))

    result = await _call(provider)

    assert result.data == {"requirements": []}
    assert result.usage.stop_reason == "tool_use"
    assert result.usage.input_tokens == 23000
