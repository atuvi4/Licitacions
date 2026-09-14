"""Preu dels tokens, per poder posar un cost a una execucio del benchmark.

Els tokens ja es comptaven (`RequirementExtractionRun.input_tokens` i
`output_tokens`); el que faltava era el preu, i per aixo `estimated_cost` sortia
sempre buit a l'informe i al manifest.

**Els preus NO viuen al codi.** Son configuracio (`LLM_MODEL_PRICES`), per dues
raons:

* Canvien sense que canvii el nostre codi, i un preu desactualitzat dins del
  repositori es una xifra falsa que ningu no revisa.
* Un preu inventat es pitjor que no tenir-ne cap: un cost per expedient errat
  es el tipus de numero que acaba en una decisio de negoci. Si no hi ha preu
  configurat per al model, el cost queda `None` i l'informe diu per que.

Format de `LLM_MODEL_PRICES` (JSON, dolars per milio de tokens):

    {"<model>": {"input": <USD per 1M>, "output": <USD per 1M>}}

Els valors surten del full de preus vigent del proveidor, no d'aquest fitxer.

La clau pot ser el nom exacte del model o un prefix: `claude-haiku-4-5` cobreix
`claude-haiku-4-5-20251001`. Si n'hi ha mes d'un que encaixa, guanya el prefix
mes llarg, que es el mes especific.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: Unitat dels preus configurats. Es el que fan servir els fulls de preus dels
#: proveidors, i evita numeros amb sis zeros decimals a la configuracio.
TOKENS_PER_UNIT = 1_000_000

#: Divisa dels preus. Els fulls de preus dels proveidors son en dolars; no es
#: converteix a euros aqui perque el tipus de canvi del dia no es part de la
#: mesura.
CURRENCY = "USD"


class PriceConfigError(ValueError):
    """La configuracio de preus no es llegible. Val mes dir-ho que ignorar-la."""


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Preu d'un model, en dolars per milio de tokens."""

    input_per_unit: float
    output_per_unit: float

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.input_per_unit + output_tokens * self.output_per_unit
        ) / TOKENS_PER_UNIT


def parse_prices(raw: Mapping[str, Any] | None) -> dict[str, ModelPrice]:
    """Converteix la configuracio en preus, o peta dient que hi ha malament."""
    if not raw:
        return {}
    prices: dict[str, ModelPrice] = {}
    for model, value in raw.items():
        if not isinstance(value, Mapping):
            raise PriceConfigError(
                f"El preu de {model!r} ha de ser un objecte amb 'input' i 'output'."
            )
        try:
            entrada = float(value["input"])
            sortida = float(value["output"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PriceConfigError(
                f"El preu de {model!r} necessita 'input' i 'output' numerics."
            ) from exc
        if entrada < 0 or sortida < 0:
            raise PriceConfigError(f"El preu de {model!r} no pot ser negatiu.")
        prices[model] = ModelPrice(input_per_unit=entrada, output_per_unit=sortida)
    return prices


def price_for(model: str, prices: Mapping[str, ModelPrice]) -> ModelPrice | None:
    """El preu del model: coincidencia exacta o, si no, el prefix mes especific."""
    if not model or not prices:
        return None
    exact = prices.get(model)
    if exact is not None:
        return exact
    candidates = [key for key in prices if model.startswith(key)]
    if not candidates:
        return None
    return prices[max(candidates, key=len)]


def configured_prices() -> dict[str, ModelPrice]:
    """Els preus de l'entorn. Importat aqui dins per no lligar el modul a settings."""
    from app.core.config import settings

    return parse_prices(settings.LLM_MODEL_PRICES)
