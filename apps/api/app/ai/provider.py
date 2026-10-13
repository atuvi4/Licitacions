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
    """Fallada atribuible al proveidor: xarxa, quota, 5xx, resposta il·legible.

    `usage` porta el consum quan el proveidor SI que ha respost i la resposta no
    serveix (per exemple, sense la crida a la tool). Aquells tokens es paguen
    igual: si es perdessin aqui, el cost d'un run amb chunks caiguts sortiria
    per sota del real. `None` vol dir que no hi va haver resposta (xarxa, 4xx/5xx).

    `details` es el diagnostic sanititzat d'un error HTTP (estat, tipus, missatge
    retallat i `request_id`): mai la clau ni text del plec. `fatal` vol dir que
    el mateix error es repetiria a tots els chunks (configuracio, compte,
    peticio mal formada): el run s'atura en lloc d'enviar-ne la resta.
    """

    def __init__(
        self,
        message: str,
        code: str = "provider_error",
        usage: Usage | None = None,
        *,
        details: dict[str, Any] | None = None,
        fatal: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.usage = usage
        self.details = details
        self.fatal = fatal


class ProviderTimeoutError(ProviderError):
    def __init__(self, message: str = "El proveidor no ha respost a temps.") -> None:
        super().__init__(message, "provider_timeout")


class ProviderNotConfiguredError(ProviderError):
    """Falta la clau o el proveidor no existeix. L'aplicacio ha d'arrencar igual."""

    def __init__(self, message: str) -> None:
        super().__init__(message, "provider_not_configured")


class InvalidStructuredOutputError(ProviderError):
    """El proveidor ha respost, pero la sortida no es pot fer servir."""

    code = "invalid_structured_output"

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message, type(self).code, usage)


class OutputTruncatedError(InvalidStructuredOutputError):
    """El model ha topat amb el limit de sortida (`stop_reason == "max_tokens"`).

    Es una fallada SEMPRE, encara que el tros que ha arribat validi: una
    extraccio tallada pot semblar completa i haver perdut la meitat dels
    requisits del chunk.
    """

    code = "output_truncated"


class NoToolCallError(InvalidStructuredOutputError):
    """El proveidor ha respost sense cridar la tool de sortida."""

    code = "no_tool_call"


class MultipleToolCallsError(InvalidStructuredOutputError):
    """Sense tool forcada, el proveidor ha cridat mes d'un cop la tool de sortida."""

    code = "multiple_tool_calls"


class UnexpectedToolCallError(InvalidStructuredOutputError):
    """Sense tool forcada, el proveidor ha cridat una tool que no es la de sortida."""

    code = "unexpected_tool_call"


class MalformedToolArgumentsError(InvalidStructuredOutputError):
    """La crida a la tool de sortida no porta un objecte d'arguments."""

    code = "malformed_tool_arguments"


class IllegalEnvelopeError(InvalidStructuredOutputError):
    """Sense tool forcada, l'envoltori de la resposta no es exactament el canonic."""

    code = "illegal_envelope"


class ServedModelMismatchError(ProviderError):
    """El proveidor ha servit un model diferent del demanat."""

    def __init__(self, message: str, usage: Usage | None = None) -> None:
        super().__init__(message, "served_model_mismatch", usage, fatal=True)


class SchemaInvalidError(InvalidStructuredOutputError):
    """Hi ha crida a la tool, pero el contracte del perfil (Pydantic) la rebutja.

    `diagnostics` es nomes estructural: on (`path`) i quin tipus d'error
    (`type`), com a molt `MAX_SCHEMA_DIAGNOSTICS`. Mai el valor rebut ni el
    missatge de l'error, que poden portar text del plec.
    """

    code = "schema_invalid"

    def __init__(
        self,
        message: str,
        usage: Usage | None = None,
        diagnostics: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(message, usage)
        self.diagnostics = diagnostics or []


@dataclass(frozen=True, slots=True)
class Usage:
    """Consum d'una crida. Serveix per mesurar cost, no per facturar."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None
    #: Per que el model va deixar d'escriure (`end_turn`, `tool_use`,
    #: `max_tokens`...). Sense aixo, un "invalid_structured_output" no distingeix
    #: una resposta mal formada d'una resposta tallada pel limit de sortida.
    stop_reason: str | None = None


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


def get_provider(
    name: str | None = None, *, max_retries: int | None = None
) -> StructuredLLMProvider:
    """Construeix el proveidor configurat.

    S'importa dins de la funcio a proposit: aixi el SDK d'un proveidor nomes es
    carrega si de veritat es fa servir, i l'aplicacio arrenca encara que no
    estigui instal·lat.

    `max_retries` substitueix `LLM_MAX_RETRIES` nomes per a aquest proveidor.
    `None` deixa la politica de produccio tal com esta; el benchmark de
    pagament hi passa 0 (veure `app.evaluation.benchmark.PAID_RUN_MAX_RETRIES`).
    """
    chosen = (name or settings.LLM_PROVIDER or "fake").strip().lower()

    if chosen == "fake":
        from app.ai.providers.fake import FakeProvider

        return FakeProvider()
    if chosen == "fixture":
        from app.ai.providers.fixture import FixtureProvider

        return FixtureProvider()
    if chosen == "anthropic":
        from app.ai.providers.anthropic import AnthropicProvider

        return AnthropicProvider(max_retries=max_retries)
    raise ProviderNotConfiguredError(f"Proveidor d'IA desconegut: {chosen!r}.")
