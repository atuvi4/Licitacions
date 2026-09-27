"""Normalitzacio estricta de la sortida del model ABANS de Pydantic.

Existeix per dos patrons concrets observats amb un proveidor real (run
`e0fc136c`, Palamos 2024-3137), i per res mes:

* **Llista serialitzada com a text.** El model envia `"requirements": "[{...}]"`
  en lloc de `"requirements": [{...}]`. Es el mateix contingut amb una capa de
  JSON de mes. Es desfa NOMES si el camp es una llista a l'esquema i el text
  es un JSON que dona exactament una llista. Qualsevol altra cosa es deixa com
  esta i Pydantic la continua rebutjant (`schema_invalid`).
* **Llista embolcallada amb el seu propi nom, com a text.** El model envia
  `"requirements": "{\"requirements\": [{...}]}"`: text JSON que es un objecte
  amb una sola clau, el nom del mateix camp, i una llista a dins. Observat al
  chunk 3 de Palamos en tres runs seguits (`3da6147` ho va fer visible). Es
  desfa NOMES si l'objecte te exactament aquesta clau i el valor es una llista.
* **Llista embolcallada amb `value`, com a text** (v3.2, runs `f7353279` i
  `eb60ec1d`, chunks 6 i 18). El model envia `"requirements": "{\"value\": [...]}"`.
  Es desfa NOMES si el text (dins del mateix limit de mida que l'embolcall
  d'arrel) es un objecte amb exactament la clau `value` i una llista a dins.
* **Sobre embolcallat a l'arrel** (v3.2, run `f7353279`). El model posa tota la
  resposta sota UNA clau (`{"extraction": {...}}`, `{"parameters": {...}}`) o
  com a text JSON sota una clau (`{"value": "{...}"}`). Es desembolcalla NOMES
  si el contracte te camps obligatoris, l'arrel te exactament una clau que no
  es cap camp del contracte, i el de dins es un objecte que porta TOTS els
  camps obligatoris, cadascun del tipus llista que declara el model. La prova
  surt de l'estructura de dins, mai del nom de l'embolcall. Una sola capa: un
  segon embolcall no porta els camps obligatoris i no es toca.
* **`extraction_id` repetit a l'arrel.** El model copia a l'arrel l'atribut del
  bloc DOCUMENT que se li ha enviat. Es treu NOMES si l'arrel no declara aquest
  camp i el valor es, caracter a caracter, un dels identificadors que el
  backend va posar al context. Un valor diferent, o qualsevol altra clau
  desconeguda, continua sent `extra_forbidden`.

El que NO fa, a proposit: cap `extra="ignore"`, cap reparacio de JSON trencat,
cap canvi de tipus que no sigui text → llista, cap tolerancia a claus
inventades. La validacio d'evidencia (pagina, extraccio, cita literal) es fa
despres, igual que sempre.

Cada normalitzacio aplicada es retorna com a `path:regla`, sense valors: queda
al `chunk_coverage` perque despres es pugui saber que un chunk va necessitar-la.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Any, get_args, get_origin

from pydantic import BaseModel

#: Regla aplicada quan una llista arriba serialitzada com a text JSON.
RULE_JSON_STRING_LIST = "json_string_list"
#: Regla aplicada quan la llista arriba com a text JSON `{"<camp>": [...]}`.
RULE_JSON_STRING_WRAPPED_LIST = "json_string_wrapped_list"
#: Regla aplicada quan la llista arriba com a text JSON `{"value": [...]}`.
RULE_JSON_STRING_VALUE_WRAPPED_LIST = "json_string_value_wrapped_list"
#: L'unica clau generica d'embolcall de llista acceptada: observada, no suposada.
_VALUE_KEY = "value"
#: Regla aplicada quan l'arrel porta l'`extraction_id` del context.
RULE_ECHOED_ROOT_EXTRACTION_ID = "echoed_root_extraction_id"
#: Regla aplicada quan tot el sobre arriba dins d'un objecte d'una sola clau.
RULE_SINGLE_OBJECT_WRAPPER = "single_object_wrapper"
#: Regla aplicada quan tot el sobre arriba com a text JSON sota una sola clau.
RULE_JSON_STRING_OBJECT_WRAPPER = "json_string_object_wrapper"
#: Mida maxima d'un text JSON que es desa per desembolcallar el sobre. La
#: sortida del model te un limit de 20.000 tokens: aixo en sobra molt.
MAX_WRAPPED_JSON_CHARS = 1_000_000

_ROOT_EXTRACTION_ID = "extraction_id"
_SAFE_KEY = re.compile(r"[a-z][a-z0-9_]{0,39}")


def normalise_output(
    payload: Any,
    model: type[BaseModel],
    *,
    context_extraction_ids: Collection[str],
) -> tuple[Any, list[str]]:
    """Retorna el payload normalitzat i les regles aplicades.

    No modifica l'objecte d'entrada. Si el payload no es un objecte, es torna
    tal qual: que ho rebutgi Pydantic.
    """
    if not isinstance(payload, dict):
        return payload, []
    applied: list[str] = []
    payload = _unwrap_root(payload, model, applied)
    result = _coerce_lists(payload, model, path="", applied=applied)

    if (
        _ROOT_EXTRACTION_ID in result
        and _ROOT_EXTRACTION_ID not in model.model_fields
        and isinstance(result[_ROOT_EXTRACTION_ID], str)
        and result[_ROOT_EXTRACTION_ID] in {str(item) for item in context_extraction_ids}
    ):
        del result[_ROOT_EXTRACTION_ID]
        applied.append(f"{_ROOT_EXTRACTION_ID}:{RULE_ECHOED_ROOT_EXTRACTION_ID}")
    return result, applied


def _unwrap_root(
    payload: dict[str, Any], model: type[BaseModel], applied: list[str]
) -> dict[str, Any]:
    """Treu UNA capa d'embolcall d'arrel si el de dins es, provadament, el sobre.

    Nomes si el contracte te camps obligatoris (v3.2: `requirements` i
    `award_candidates`); si no, no hi ha res que provi que el de dins es el sobre.
    Si qualsevol condicio falla, el payload es torna tal qual i Pydantic el
    rebutja com sempre.
    """
    required = [name for name, field in model.model_fields.items() if field.is_required()]
    if not required or len(payload) != 1:
        return payload
    [(key, value)] = payload.items()
    if key in model.model_fields:
        return payload
    rule = RULE_SINGLE_OBJECT_WRAPPER
    if isinstance(value, str):
        if len(value) > MAX_WRAPPED_JSON_CHARS:
            return payload
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return payload
        rule = RULE_JSON_STRING_OBJECT_WRAPPER
    if not isinstance(value, dict):
        return payload
    for name in required:
        if name not in value:
            return payload
        if get_origin(model.model_fields[name].annotation) is list and not isinstance(
            value[name], list
        ):
            return payload
    applied.append(f"root:{rule}:{_safe_key(key)}")
    return dict(value)


def _safe_key(key: Any) -> str:
    """El nom de la clau nomes si no pot ser text del plec."""
    text = str(key)
    return text if _SAFE_KEY.fullmatch(text) else f"<text,{len(text)} chars>"


def _coerce_lists(
    data: dict[str, Any], model: type[BaseModel], *, path: str, applied: list[str]
) -> dict[str, Any]:
    result = dict(data)
    for name, field in model.model_fields.items():
        if name not in result or get_origin(field.annotation) is not list:
            continue
        value = result[name]
        here = f"{path}{name}"
        if isinstance(value, str):
            decoded, rule = _decode_list(value, name)
            if decoded is None:
                continue
            value = decoded
            result[name] = value
            applied.append(f"{here}:{rule}")
        if not isinstance(value, list):
            continue
        item_model = _list_item_model(field.annotation)
        if item_model is None:
            continue
        result[name] = [
            _coerce_lists(item, item_model, path=f"{here}.{index}.", applied=applied)
            if isinstance(item, dict)
            else item
            for index, item in enumerate(value)
        ]
    return result


def _decode_list(value: str, field: str) -> tuple[list[Any] | None, str]:
    """El text nomes es desfa si dona una llista. Si no, `(None, "")`.

    Tres formes acceptades, i cap mes:

    * `[...]`: el text JSON es directament la llista;
    * `{"<field>": [...]}`: un objecte amb UNA sola clau, que es exactament el
      nom del camp, i una llista com a valor;
    * `{"value": [...]}` (v3.2): igual, amb la clau `value`, si el text no
      passa de `MAX_WRAPPED_JSON_CHARS`.

    Una sola descodificacio: `{"value": {"value": [...]}}` o una llista com a
    text dins de `value` no es desfan.

    Una clau diferent, mes d'una clau, o un valor que no sigui llista es
    queden com estan: Pydantic els continua rebutjant.
    """
    try:
        decoded = json.loads(value)
    except (ValueError, RecursionError):
        return None, ""
    if isinstance(decoded, list):
        return decoded, RULE_JSON_STRING_LIST
    if (
        isinstance(decoded, dict)
        and len(decoded) == 1
        and field in decoded
        and isinstance(decoded[field], list)
    ):
        return decoded[field], RULE_JSON_STRING_WRAPPED_LIST
    if (
        len(value) <= MAX_WRAPPED_JSON_CHARS
        and isinstance(decoded, dict)
        and len(decoded) == 1
        and _VALUE_KEY in decoded
        and isinstance(decoded[_VALUE_KEY], list)
    ):
        return decoded[_VALUE_KEY], RULE_JSON_STRING_VALUE_WRAPPED_LIST
    return None, ""


def _list_item_model(annotation: Any) -> type[BaseModel] | None:
    args = get_args(annotation)
    if len(args) == 1 and isinstance(args[0], type) and issubclass(args[0], BaseModel):
        return args[0]
    return None
