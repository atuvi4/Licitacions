"""Frontera amb els proveidors d'inferencia.

Tot el domini parla amb `StructuredLLMProvider` i amb res mes. Cap `import
anthropic` fora de `app/ai/providers/`: canviar de proveidor ha de ser una
linia de configuracio, no una refactoritzacio.

El contracte es deliberadament estret: una operacio que rep instruccions, un
context i un esquema, i retorna JSON validat mes metriques d'us. No hi ha
"chat", ni historial, ni streaming: aquesta capa no fa conversa, fa extraccio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.core.config import settings


class ProviderError(Exception):
    """Fallada atribuible al proveidor: xarxa, quota, 5xx, resposta il·legible."""

    def __init__(self, message: str, code: str = "provider_error") -> None:
        super().__init__(message)
        self.code = code


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str = "El proveidor no ha respost a temps.") -> None:
        super().__init__(message, "provider_timeout")


class ProviderNotConfiguredError(ProviderError):
    """Falta la clau o el proveidor no existeix. L'aplicacio ha d'arrencar igual."""

    def __init__(self, message: str) -> None:
        super().__init__(message, "provider_not_configured")


class InvalidStructuredOutputError(ProviderError):
    """El proveidor ha respost, pero el JSON no compleix l'esquema."""

    def __init__(self, message: str) -> None:
        super().__init__(message, "invalid_structured_output")


@dataclass(frozen=True, slots=True)
class Usage:
    """Consum d'una crida. Serveix per mesurar cost, no per facturar."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class StructuredResult:
    """Resposta d'una crida: dades ja parsejades i que ha costat."""

    data: dict[str, Any]
    usage: Usage = field(default_factory=Usage)
    model: str = ""


@runtime_checkable
class StructuredLLMProvider(Protocol):
    """Un proveidor capac de retornar JSON conforme a un esquema."""

    #: Nom estable que es desa al Run.
    name: str

    @property
    def model(self) -> str:
        """Model efectiu, per registrar-lo amb el resultat."""
        ...

    def is_available(self) -> bool:
        """Fals si falta configuracio. No ha de llancar."""
        ...

    async def extract_structured(
        self,
        *,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> StructuredResult:
        """Retorna JSON validat contra `json_schema`, o llanca `ProviderError`."""
        ...


def get_provider(name: str | None = None) -> StructuredLLMProvider:
    """Construeix el proveidor configurat.

    S'importa dins de la funcio a proposit: aixi el SDK d'un proveidor nomes es
    carrega si de veritat es fa servir, i l'aplicacio arrenca encara que no
    estigui instal·lat.
    """
    chosen = (name or settings.LLM_PROVIDER or "fake").strip().lower()

    if chosen == "fake":
        from app.ai.providers.fake import FakeProvider

        return FakeProvider()
    if chosen == "anthropic":
        from app.ai.providers.anthropic import AnthropicProvider

        return AnthropicProvider()
    raise ProviderNotConfiguredError(f"Proveidor d'IA desconegut: {chosen!r}.")
