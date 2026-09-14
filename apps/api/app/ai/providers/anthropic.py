"""Adaptador d'Anthropic. **L'unic fitxer del projecte que en coneix el SDK.**

Decisions que val la pena explicitar:

* El SDK s'importa dins del constructor. Aixi l'aplicacio arrenca encara que el
  paquet no estigui instal·lat o no hi hagi clau: nomes queda no disponible la
  funcio d'analisi, no tot el servei.
* La sortida estructurada es demana amb una **tool** amb `input_schema`: es la
  manera que te aquest proveidor de garantir JSON conforme. No es parseja text
  lliure amb expressions regulars.
* El model NO es una constant de domini: ve de `settings.LLM_MODEL`.
* No es loguega mai ni la clau ni el contingut dels plecs.
"""

from __future__ import annotations

import logging
from typing import Any

from app.ai.provider import (
    InvalidStructuredOutputError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderTimeoutError,
    StructuredResult,
    Usage,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

#: Nom de la tool per la qual es forca la sortida estructurada.
_TOOL_NAME = "record_extraction"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        self._client: Any | None = None
        self._model = settings.LLM_MODEL

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        if not settings.ANTHROPIC_API_KEY:
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not settings.ANTHROPIC_API_KEY:
            raise ProviderNotConfiguredError(
                "ANTHROPIC_API_KEY no esta configurada: l'analisi de plecs no esta disponible."
            )
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depen de l'entorn
            raise ProviderNotConfiguredError(
                "El paquet `anthropic` no esta instal·lat."
            ) from exc

        self._client = anthropic.AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            max_retries=settings.LLM_MAX_RETRIES,
        )
        return self._client

    async def extract_structured(
        self,
        *,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> StructuredResult:
        client = self._ensure_client()
        import anthropic

        try:
            response = await client.messages.create(
                model=self._model,
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user_content}],
                tools=[
                    {
                        "name": _TOOL_NAME,
                        "description": (
                            "Registra els requisits i criteris trobats al fragment. "
                            "Utilitza NOMES informacio explicita del text proporcionat."
                        ),
                        "input_schema": json_schema,
                    }
                ],
                # Obliga el model a respondre per la tool: sense text lliure.
                tool_choice={"type": "tool", "name": _TOOL_NAME},
            )
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"HTTP {exc.status_code} del proveidor.", "provider_error") from exc
        except anthropic.APIError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

        # L'us es llegeix abans de validar: una resposta inservible tambe s'ha pagat.
        raw_usage = getattr(response, "usage", None)
        usage = Usage(
            input_tokens=getattr(raw_usage, "input_tokens", None),
            output_tokens=getattr(raw_usage, "output_tokens", None),
            request_id=getattr(response, "id", None),
            stop_reason=getattr(response, "stop_reason", None),
        )

        payload: dict[str, Any] | None = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
                payload = dict(block.input)
                break
        if payload is None:
            raise InvalidStructuredOutputError(
                "El proveidor no ha retornat cap crida a la tool de sortida "
                f"(stop_reason={usage.stop_reason}).",
                usage=usage,
            )

        return StructuredResult(
            data=payload,
            usage=usage,
            model=getattr(response, "model", self._model),
        )
