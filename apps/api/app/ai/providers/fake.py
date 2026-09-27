"""Proveidor determinista per a tests. No fa cap crida de xarxa.

Existeix per dues raons. La primera es obvia: la suite no pot gastar tokens ni
dependre d'Internet. La segona importa mes: els casos que hem de provar son
justament els que un proveidor real no produeix quan vol, com que citi una
pagina inexistent o que inventi un quote. Aqui es poden programar.

S'utilitza de dues maneres:

* `FakeProvider()` sense arguments: llegeix les respostes d'una cua global que
  el test omple amb `queue_response(...)`. Aixi el test controla el proveidor
  que construeix el servei per configuracio.
* `FakeProvider(responses=[...])`: injectat directament.
"""

from __future__ import annotations

import json
from collections import deque
from typing import Any

from app.ai.provider import (
    InvalidStructuredOutputError,
    ProviderError,
    ProviderTimeoutError,
    StructuredResult,
    Usage,
)

#: Respostes pendents. Cada element es un `StructuredResult`, una excepcio a
#: llancar, o un dict amb el JSON de sortida.
_QUEUE: deque[Any] = deque()
#: Tot el que s'ha enviat al proveidor. Els tests hi comproven que no hi viatgi
#: cap dada de l'empresa.
SENT_PAYLOADS: list[dict[str, Any]] = []


def reset() -> None:
    _QUEUE.clear()
    SENT_PAYLOADS.clear()


def queue_response(response: Any) -> None:
    """Encua una resposta, un dict de sortida o una excepcio."""
    _QUEUE.append(response)


def queue_many(responses: list[Any]) -> None:
    for response in responses:
        queue_response(response)


def queue_timeout() -> None:
    queue_response(ProviderTimeoutError())


def queue_error(message: str = "El proveidor ha fallat.") -> None:
    queue_response(ProviderError(message))


def queue_invalid_json() -> None:
    queue_response(InvalidStructuredOutputError("La resposta no es JSON valid."))


class FakeProvider:
    """Compleix `StructuredLLMProvider` sense sortir del proces."""

    name = "fake"

    def __init__(self, responses: list[Any] | None = None, model: str = "fake-model-v1") -> None:
        self._own: deque[Any] | None = deque(responses) if responses is not None else None
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        return True

    async def extract_structured(
        self,
        *,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> StructuredResult:
        SENT_PAYLOADS.append(
            {"system": system, "user_content": user_content, "schema": json_schema}
        )

        source = self._own if self._own is not None else _QUEUE
        if not source:
            # Sense res encuat, un chunk no troba res. Es el cas neutre: les
            # llistes que l'esquema fa obligatories (v3.2), buides.
            empty = {key: [] for key in json_schema.get("required", [])}
            return StructuredResult(data=empty or {"requirements": []}, model=self._model)

        item = source.popleft()
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, StructuredResult):
            return item
        if isinstance(item, str):
            # Cadena crua: simula una resposta que no es pot parsejar.
            try:
                parsed = json.loads(item)
            except json.JSONDecodeError as exc:
                raise InvalidStructuredOutputError(f"JSON invalid: {exc}") from exc
            return StructuredResult(data=parsed, model=self._model)
        return StructuredResult(
            data=item,
            usage=Usage(input_tokens=100, output_tokens=50, request_id="fake-req"),
            model=self._model,
        )
