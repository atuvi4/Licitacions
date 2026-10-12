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
import re
from typing import Any

from app.ai.provider import (
    NoToolCallError,
    OutputTruncatedError,
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

#: Errors HTTP que no depenen del chunk sino de la configuracio, del compte o de
#: la forma de la peticio (clau, credit, permisos, model inexistent). El SDK no
#: els reintenta mai, i continuar amb la resta de chunks nomes repetiria el mateix
#: error. 429, 408, 409 i 5xx NO hi son: son transitoris i els gestiona
#: `LLM_MAX_RETRIES` com sempre.
FATAL_HTTP_STATUSES = frozenset({400, 401, 403, 404})
#: Motius de parada que deixen la sortida incompleta: mai es fa servir, encara que hi hagi
#: una crida a la tool (un JSON tallat pot validar). `max_tokens`: el limit de sortida;
#: `model_context_window_exceeded`: la finestra de context del model.
TRUNCATION_STOP_REASONS = frozenset({"max_tokens", "model_context_window_exceeded"})
#: El model ha refusat respondre: cap contingut, encara que n'hi hagi, es fa servir.
REFUSAL_STOP_REASON = "refusal"

#: Llargada maxima del missatge del proveidor que es desa.
MAX_ERROR_MESSAGE_CHARS = 300
_KEY_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]+")


def sanitise_provider_message(message: Any) -> str | None:
    """Una linia, sense cap clau i retallada. `None` si no n'hi ha."""
    if message is None:
        return None
    text = " ".join(str(message).split())
    text = _KEY_PATTERN.sub("[redacted]", text)
    if settings.ANTHROPIC_API_KEY:
        text = text.replace(settings.ANTHROPIC_API_KEY, "[redacted]")
    return text[:MAX_ERROR_MESSAGE_CHARS] or None


def status_error_details(exc: Any) -> dict[str, Any]:
    """Diagnostic d'un `APIStatusError`: estat, tipus, missatge i request_id."""
    body = getattr(exc, "body", None)
    error = body.get("error") if isinstance(body, dict) else None
    error = error if isinstance(error, dict) else {}
    error_type = getattr(exc, "type", None) or error.get("type")
    request_id = getattr(exc, "request_id", None)
    if request_id is None and isinstance(body, dict):
        request_id = body.get("request_id")
    return {
        "status": getattr(exc, "status_code", None),
        "type": str(error_type) if error_type else None,
        "message": sanitise_provider_message(error.get("message")),
        "request_id": str(request_id) if request_id else None,
    }


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, max_retries: int | None = None) -> None:
        self._client: Any | None = None
        self._model = settings.LLM_MODEL
        #: Reintents automatics del SDK. `None` = `LLM_MAX_RETRIES` (produccio).
        #: El benchmark de pagament hi posa 0: el SDK reintenta sol en timeouts,
        #: 429 i 5xx, i cada intent que el proveidor arriba a processar es paga.
        self._max_retries = settings.LLM_MAX_RETRIES if max_retries is None else max_retries

    @property
    def max_retries(self) -> int:
        return self._max_retries

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
            max_retries=self._max_retries,
        )
        return self._client

    def request_params(
        self,
        *,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> dict[str, Any]:
        """El cos de `messages.create`. Cap parametre de mostreig (`temperature`, `top_p`,
        `top_k`): es fan servir els del model. `thinking` NOMES per als models de
        `LLM_THINKING_DISABLED_MODELS`, i nomes per desactivar-lo (tool forcada)."""
        params: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user_content}],
            "tools": [
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
            "tool_choice": {"type": "tool", "name": _TOOL_NAME},
        }
        if self._model in settings.LLM_THINKING_DISABLED_MODELS:
            params["thinking"] = {"type": "disabled"}
        return params

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
            response = await client.messages.create(**self.request_params(
                system=system,
                user_content=user_content,
                json_schema=json_schema,
                max_output_tokens=max_output_tokens,
            ))
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            details = status_error_details(exc)
            label = f" ({details['type']})" if details["type"] else ""
            raise ProviderError(
                f"HTTP {exc.status_code} del proveidor{label}.",
                "provider_error",
                details=details,
                fatal=exc.status_code in FATAL_HTTP_STATUSES,
            ) from exc
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

        # Primer el limit de sortida (o de context): una resposta tallada es inservible
        # encara que el tros que ha arribat sembli un JSON valid.
        if usage.stop_reason in TRUNCATION_STOP_REASONS:
            raise OutputTruncatedError(
                f"La resposta s'ha tallat ({usage.stop_reason}; sortida maxima "
                f"{max_output_tokens} tokens).",
                usage=usage,
            )
        if usage.stop_reason == REFUSAL_STOP_REASON:
            raise ProviderError(
                "El model ha refusat respondre (stop_reason=refusal).", "refusal", usage=usage
            )

        payload: dict[str, Any] | None = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == _TOOL_NAME:
                payload = dict(block.input)
                break
        if payload is None:
            raise NoToolCallError(
                "El proveidor no ha retornat cap crida a la tool de sortida "
                f"(stop_reason={usage.stop_reason}).",
                usage=usage,
            )

        return StructuredResult(
            data=payload,
            usage=usage,
            model=getattr(response, "model", self._model),
        )
