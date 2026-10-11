"""Doble de proves amb nom de proveidor REAL (3B-2 §6.3). Cap xarxa.

`run_basis` es deriva del nom del proveidor: `fake`/`fixture` son simulacio i mai
passen a vigents. Per provar el cami d'un run real (promocio, `completed_empty`,
`input_changed`) cal un proveidor que es digui `anthropic` sense ser-ho: aquest.
S'injecta NOMES per `provider=`/`provider_factory=` dels tests; `get_provider` no el
coneix i `LLM_PROVIDER` continua sent `fake`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.ai.providers import fake

#: Es crida abans de cada extraccio (index de la crida, 0-based).
BeforeCall = Callable[[int], Awaitable[None]]


class RealNamedFake(fake.FakeProvider):
    """`FakeProvider` amb `name = "anthropic"`: base `real`, cap crida de xarxa."""

    name = "anthropic"

    def __init__(
        self,
        responses: list[Any] | None = None,
        model: str = "claude-test-model",
        *,
        before_call: BeforeCall | None = None,
    ) -> None:
        super().__init__(responses=responses, model=model)
        self._before_call = before_call
        self.calls = 0

    async def extract_structured(self, **kwargs: Any):
        index = self.calls
        self.calls += 1
        if self._before_call is not None:
            await self._before_call(index)
        return await super().extract_structured(**kwargs)
