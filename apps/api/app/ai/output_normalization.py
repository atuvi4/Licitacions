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
from collections.abc import Collection
from typing import Any, get_args, get_origin

from pydantic import BaseModel

#: Regla aplicada quan una llista arriba serialitzada com a text JSON.
RULE_JSON_STRING_LIST = "json_string_list"
#: Regla aplicada quan la llista arriba com a text JSON `{"<camp>": [...]}`.
RULE_JSON_STRING_WRAPPED_LIST = "json_string_wrapped_list"
#: Regla aplicada quan l'arrel porta l'`extraction_id` del context.
RULE_ECHOED_ROOT_EXTRACTION_ID = "echoed_root_extraction_id"

_ROOT_EXTRACTION_ID = "extraction_id"


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

    Dues formes acceptades, i cap mes:

    * `[...]`: el text JSON es directament la llista;
    * `{"<field>": [...]}`: un objecte amb UNA sola clau, que es exactament el
      nom del camp, i una llista com a valor.

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
    return None, ""


def _list_item_model(annotation: Any) -> type[BaseModel] | None:
    args = get_args(annotation)
    if len(args) == 1 and isinstance(args[0], type) and issubclass(args[0], BaseModel):
        return args[0]
    return None
