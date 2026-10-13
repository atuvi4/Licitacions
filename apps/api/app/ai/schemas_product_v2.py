"""Contracte de sortida del perfil `product-v2` (revisio v2.1).

Requisits, criteris i notes: els MATEIXOS models que V1 (`app.ai.schemas`). L'unic canvi de
contingut es la incertesa: a V1 era `{note, page_number?}` i es descartava; a `product-v2` es
una incertesa MATERIAL amb ambit, document, pagina i cita opcional, i es persisteix a
`requirement_uncertainties` (0062). La cita la valida el backend amb la mateixa regla literal
que l'evidencia; si no es literal, la incertesa es conserva sense cita.

**Nomes product-v2.** V1 i v2 no tenen `parser`: continuen validant el chunk sencer amb
`output_model.model_validate` (un element invalid fa `schema_invalid` el chunk), sense canvis.

**Validacio element a element** (`parse_chunk_product_v2`). Requisits, criteris i incerteses
es validen un a un: un element mal format es descarta SOL, amb un rebuig de codis, i mai fa
perdre la resta del chunk. Res no es coacciona ni es reinterpreta: un valor fora del
vocabulari tancat continua sent un rebuig. El rebuig diu el tipus d'element, el camp, la regla
violada (`detail`), el tipus rebut i, nomes si es d'un vocabulari tancat, el valor rebut;
l'obligacio i la categoria (o l'ambit) valids; i les pagines declarades. Nomes un sobre
inservible (no es un objecte, o una de les llistes no es una llista) fa `schema_invalid` el
chunk sencer, com a V1.

v2.1 (post-mortem de l'Attempt 3, 2026-10-12): un requisit valid de la p7 amb
`category = "submission_documentation"` (un AMBIT d'incertesa, no una categoria) va invalidar
tot el chunk 1. Ara aquell requisit es rebutja sol, amb `field = category`,
`value_vocabulary = uncertainty_scope`, l'obligacio i les pagines declarades.
"""

from __future__ import annotations

import copy
import json
from typing import Annotated, Any

from pydantic import BaseModel, Field, PrivateAttr, StringConstraints, ValidationError

from app.ai.schemas import AwardCriterionOut, ChunkExtractionOut, RequirementOut, StrictModel
from app.models.enums import RequirementCategory, RequirementObligation, UncertaintyScope

SCHEMA_VERSION_PRODUCT_V2 = "requirements-schema-product-v2.1"

#: Una nota es una frase, no un paragraf ni una copia del plec.
MAX_UNCERTAINTY_NOTE_CHARS = 500
#: Sostre de `page_number`. Una pagina fora del chunk es desa (amb diagnostic): sense
#: sostre, un valor enorme desbordaria la columna INTEGER al commit del chunk i es
#: perdria tot el chunk (amb la linia de despesa). Aixi es un rebuig d'element.
MAX_UNCERTAINTY_PAGE_NUMBER = 100_000


class MaterialUncertaintyOut(StrictModel):
    """Una clausula material que el text no permet resoldre amb seguretat."""

    scope: UncertaintyScope
    #: El document del fragment, copiat de l'atribut del bloc DOCUMENT.
    extraction_id: str
    page_number: int = Field(ge=1, le=MAX_UNCERTAINTY_PAGE_NUMBER)
    #: Fragment literal de la pagina, si n'hi ha un. El backend el comprova.
    quote: str | None = None
    #: Que es indeterminat, en una frase. Una nota nomes d'espais no es cap nota.
    note: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_UNCERTAINTY_NOTE_CHARS
        ),
    ]


class ChunkExtractionOutProductV2(ChunkExtractionOut):
    """El de V1, amb les incerteses materials."""

    uncertainties: list[MaterialUncertaintyOut] = Field(default_factory=list)

    #: Posicio ORIGINAL a la resposta de cada element conservat.
    _requirement_indexes: tuple[int, ...] = PrivateAttr(default=())
    _award_indexes: tuple[int, ...] = PrivateAttr(default=())
    _uncertainty_indexes: tuple[int, ...] = PrivateAttr(default=())
    #: Requisits i criteris descartats per esquema (rebutjos de codis).
    _schema_rejections: tuple[dict[str, Any], ...] = PrivateAttr(default=())
    #: Incerteses descartades per esquema: `{item, index, stage, reason, detail, field,
    #: received_type, received_value, scope, cited_pages}` (nomes codis).
    _uncertainty_rejections: tuple[dict[str, Any], ...] = PrivateAttr(default=())

    @property
    def requirement_indexes(self) -> tuple[int, ...]:
        return self._requirement_indexes or tuple(range(len(self.requirements)))

    @property
    def award_indexes(self) -> tuple[int, ...]:
        return self._award_indexes or tuple(range(len(self.award_criteria)))

    @property
    def uncertainty_indexes(self) -> tuple[int, ...]:
        return self._uncertainty_indexes or tuple(range(len(self.uncertainties)))

    @property
    def schema_rejections(self) -> tuple[dict[str, Any], ...]:
        return self._schema_rejections

    @property
    def uncertainty_rejections(self) -> tuple[dict[str, Any], ...]:
        return self._uncertainty_rejections


#: Tipus d'error de Pydantic que es poden dir a la cobertura. Vocabulari tancat: la
#: resta es `other`. Propi (no el del contracte v3, que es intern i no s'importa aqui).
_SCHEMA_DETAILS: frozenset[str] = frozenset(
    {
        "missing", "extra_forbidden", "enum", "value_error", "string_type",
        "string_too_long", "string_too_short", "int_type", "int_parsing",
        "int_from_float", "greater_than_equal", "less_than_equal", "list_type",
        "dict_type", "model_type", "model_attributes_type", "float_type", "float_parsing",
    }
)  # fmt: skip

_REQUIREMENT_CATEGORIES = frozenset(c.value for c in RequirementCategory)
_OBLIGATIONS = frozenset(o.value for o in RequirementObligation)
_SCOPES = frozenset(s.value for s in UncertaintyScope)


def _schema_rejection(
    item: str, index: int, detail: str, field_name: str | None, **codes: Any
) -> dict[str, Any]:
    """Una entrada de rebuig: posicio i codis, mai text lliure ni una clau inventada."""
    entry: dict[str, Any] = {
        "item": item,
        "index": int(index),
        "stage": "schema",
        "reason": "schema_invalid",
        "detail": detail if detail in _SCHEMA_DETAILS else "other",
    }
    if field_name is not None:
        entry["field"] = field_name
    entry.update({k: v for k, v in codes.items() if v not in (None, [], ())})
    return entry


def _first_error(
    exc: ValidationError, model: type[BaseModel]
) -> tuple[str, str | None, Any, tuple[Any, ...]]:
    """(tipus, camp de primer nivell conegut, valor de l'error, `loc` sencer)."""
    errors = exc.errors(include_url=False, include_context=False)
    first = errors[0] if errors else {}
    loc = tuple(first.get("loc") or ())
    field_name = (
        loc[0] if loc and isinstance(loc[0], str) and loc[0] in model.model_fields else None
    )
    return str(first.get("type", "other")), field_name, first.get("input"), loc


def _closed(value: Any, vocabulary: frozenset[str]) -> str | None:
    """El valor NOMES si es una cadena del vocabulari tancat (mai llista, dict ni text lliure)."""
    return value if isinstance(value, str) and value in vocabulary else None


#: Els valors que es poden escriure tal qual en un diagnostic: vocabularis tancats coneguts.
_KNOWN_VALUES = _REQUIREMENT_CATEGORIES | _SCOPES | _OBLIGATIONS


def _received_type(value: Any) -> str:
    if value is None:
        return "null"
    for kind, name in ((bool, "bool"), (int, "int"), (float, "float"), (str, "str"),
                       (list, "list"), (dict, "object")):  # fmt: skip
        if isinstance(value, kind):
            return name
    return "other"


def _received(
    value: Any, field_name: str | None, detail: str, loc: tuple[Any, ...]
) -> dict[str, Any]:
    """Que es va rebre, NOMES quan se sap de quin camp es:

    * l'element sencer no es un objecte (`loc` buit): el tipus de l'element;
    * error directament sobre un camp conegut: el tipus del valor (o `absent` si falta) i el valor
      NOMES si es d'un vocabulari tancat conegut (mai text lliure del model);
    * error niat (dins d'una evidencia...) o sobre una clau desconeguda: res (el `field` i el
      `detail` ja ho diuen)."""
    if not loc:
        return {"received_type": _received_type(value)}
    if field_name is None or len(loc) != 1:
        return {}
    if detail == "missing":
        return {"received_type": "absent"}
    return {"received_type": _received_type(value), "received_value": _closed(value, _KNOWN_VALUES)}


def _valid_page(page: Any) -> bool:
    return (
        isinstance(page, int)
        and not isinstance(page, bool)
        and 1 <= page <= MAX_UNCERTAINTY_PAGE_NUMBER
    )


def _from_validation_error(index: int, raw: Any, exc: ValidationError) -> dict[str, Any]:
    detail, field_name, value, loc = _first_error(exc, MaterialUncertaintyOut)
    page = raw.get("page_number") if isinstance(raw, dict) else None
    return _schema_rejection(
        "uncertainty",
        index,
        detail,
        field_name,
        **_received(value, field_name, detail, loc),
        scope=_closed(raw.get("scope") if isinstance(raw, dict) else None, _SCOPES),
        cited_pages=[page] if _valid_page(page) else None,
    )


def _cited_pages(raw: Any) -> list[int]:
    if not isinstance(raw, dict) or not isinstance(raw.get("evidence"), list):
        return []
    pages = [e.get("page_number") for e in raw["evidence"] if isinstance(e, dict)]
    return sorted({p for p in pages if _valid_page(p)})


def _item_rejection(item: str, index: int, raw: Any, exc: ValidationError, model) -> dict[str, Any]:
    """Rebuig d'un requisit o criteri: el tipus d'error i el camp, l'obligacio i la categoria
    NOMES si son valors del vocabulari tancat, les pagines declarades i, si el valor rebutjat es
    un AMBIT d'incertesa (vocabulari que el model ha barrejat), `value_vocabulary`."""
    detail, field_name, value, loc = _first_error(exc, model)
    obligation = raw.get("obligation") if isinstance(raw, dict) else None
    category = raw.get("category") if isinstance(raw, dict) else None
    vocabulary = None
    # Nomes si l'error es DIRECTAMENT sobre la `category` (no un element niat ni un altre camp).
    if loc == ("category",) and _closed(value, _SCOPES) and value not in _REQUIREMENT_CATEGORIES:
        vocabulary = "uncertainty_scope"
    return _schema_rejection(
        item,
        index,
        detail,
        field_name,
        **_received(value, field_name, detail, loc),
        obligation=_closed(obligation, _OBLIGATIONS),
        category=_closed(category, _REQUIREMENT_CATEGORIES),
        cited_pages=_cited_pages(raw),
        value_vocabulary=vocabulary,
    )


def _validate_items(raw: list[Any], model, item: str) -> tuple[list, list[int], list[dict]]:
    kept, indexes, rejected = [], [], []
    for index, candidate in enumerate(raw):
        try:
            kept.append(model.model_validate(candidate))
        except ValidationError as exc:
            if item == "uncertainty":
                rejected.append(_from_validation_error(index, candidate, exc))
            else:
                rejected.append(_item_rejection(item, index, candidate, exc, model))
            continue
        indexes.append(index)
    return kept, indexes, rejected


def parse_chunk_product_v2(payload: Any) -> ChunkExtractionOutProductV2:
    """Valida el sobre i cada element per separat.

    * Sobre: un objecte amb les claus conegudes. Si no es un objecte, si te claus d'arrel
      desconegudes, o si `requirements`/`award_criteria`/`document_notes` no son llistes (un
      `null` explicit tampoc ho es; nomes una clau absent val per buida), `ValidationError` i
      el chunk sencer es `schema_invalid` (com a V1).
    * `uncertainties` que no es una llista: el camp sencer es descarta (un rebuig amb
      `index = -1`) i el chunk segueix.
    * Un requisit, criteri o incertesa invalid: es descarta NOMES aquell, amb un rebuig de
      codis. Mai es coacciona cap valor.
    """
    if not isinstance(payload, dict):
        return ChunkExtractionOutProductV2.model_validate(payload)
    # Una clau ABSENT val per una llista buida (com el `default_factory` de V1); un `null` explicit
    # NO: es un sobre invalid, com a V1, i el chunk es `schema_invalid` (i una represa el reenvia).
    lists = {
        key: payload[key] if key in payload else [] for key in ("requirements", "award_criteria")
    }
    if not all(isinstance(v, list) for v in lists.values()):
        # Sobre inservible: el validador estricte dona l'error de sempre.
        return ChunkExtractionOutProductV2.model_validate(payload)
    raw_uncertainties = payload.get("uncertainties")
    uncertainty_rejections: list[dict[str, Any]] = []
    if raw_uncertainties is None:
        raw_uncertainties = []
    if not isinstance(raw_uncertainties, list):
        uncertainty_rejections.append(
            _schema_rejection(
                "uncertainty",
                -1,
                "list_type",
                "uncertainties",
                received_type=_received_type(raw_uncertainties),
            )
        )
        raw_uncertainties = []

    requirements, requirement_indexes, requirement_rejections = _validate_items(
        lists["requirements"], RequirementOut, "requirement"
    )
    awards, award_indexes, award_rejections = _validate_items(
        lists["award_criteria"], AwardCriterionOut, "award_criterion"
    )
    uncertainties, uncertainty_indexes, item_rejections = _validate_items(
        raw_uncertainties, MaterialUncertaintyOut, "uncertainty"
    )
    parsed = ChunkExtractionOutProductV2.model_validate(
        {**payload, "requirements": [], "award_criteria": [], "uncertainties": []}
    )
    parsed.requirements = requirements
    parsed.award_criteria = awards
    parsed.uncertainties = uncertainties
    parsed._requirement_indexes = tuple(requirement_indexes)
    parsed._award_indexes = tuple(award_indexes)
    parsed._uncertainty_indexes = tuple(uncertainty_indexes)
    parsed._schema_rejections = tuple(requirement_rejections + award_rejections)
    parsed._uncertainty_rejections = tuple(uncertainty_rejections + item_rejections)
    return parsed


def chunk_output_json_schema_product_v2() -> dict[str, Any]:
    return ChunkExtractionOutProductV2.model_json_schema()


#: Esquema de TRANSPORT per a una tool `strict: true` (models que no admeten la tool forcada,
#: com Sonnet 5.5). No es un contracte nou: el contracte continua sent `SCHEMA_VERSION_PRODUCT_V2`
#: i el parser (`parse_chunk_product_v2`) valida la resposta exactament igual.
#: v1 (`...-strict`, Attempt 5, 2026-10-13): Anthropic la va refusar ABANS de generar (HTTP 400,
#: «The compiled grammar is too large»). Tenia 21 propietats opcionals (10 en un sol objecte) i
#: cada propietat opcional multiplica els camins de la gramatica. v2: cap propietat opcional
#: (tot viatja, `null` si no hi ha valor), l'operador com a enum amb `null` (sense unio), sense
#: `title` ni `default`, i sense definicions que ja no es fan servir.
TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT = "requirements-transport-product-v2.1-strict.2"
#: Restriccions que la tool estricta no admet (HTTP 400) i anotacions sense efecte en una
#: gramatica on tot es obligatori. No viatgen; el parser aplica les restriccions igual.
_STRICT_DROPPED_KEYWORDS = ("minLength", "maxLength", "minimum", "maximum", "title", "default")
#: `value` d'un requisit: la tool estricta no admet objectes lliures, aixi que viatja amb una
#: forma fixa (o `null`). El parser l'accepta igual (`dict | None`).
_STRICT_VALUE = {
    "anyOf": [
        {
            "type": "object",
            "properties": {
                "amount": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                "unit": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                "text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["amount", "unit", "text"],
            "additionalProperties": False,
        },
        {"type": "null"},
    ],
}


def _all_required(node: Any, *, mapping: bool = False) -> None:
    """Cada objecte: totes les propietats obligatories (cap camp opcional a la gramatica)."""
    if isinstance(node, dict):
        if not mapping and "properties" in node:
            node["required"] = list(node["properties"])
        for key, value in node.items():
            _all_required(value, mapping=not mapping and key in ("properties", "$defs"))
    elif isinstance(node, list):
        for value in node:
            _all_required(value)


def _drop_unused_definitions(schema: dict[str, Any]) -> None:
    text = json.dumps({k: v for k, v in schema.items() if k != "$defs"})
    definitions = schema["$defs"]
    used: set[str] = set()
    pending = {name for name in definitions if f'"#/$defs/{name}"' in text}
    while pending:
        name = pending.pop()
        used.add(name)
        body = json.dumps(definitions[name])
        pending |= {n for n in definitions if f'"#/$defs/{n}"' in body and n not in used}
    for name in set(definitions) - used:
        del definitions[name]


def _drop_strict_keywords(node: Any, *, mapping: bool = False) -> None:
    """Treu les restriccions no admeses. `mapping`: un diccionari de noms (`properties`,
    `$defs`), on les claus son noms i no paraules clau."""
    if isinstance(node, dict):
        if not mapping:
            for keyword in _STRICT_DROPPED_KEYWORDS:
                node.pop(keyword, None)
        for key, value in node.items():
            _drop_strict_keywords(value, mapping=not mapping and key in ("properties", "$defs"))
    elif isinstance(node, list):
        for value in node:
            _drop_strict_keywords(value)


def chunk_output_json_schema_product_v2_strict() -> dict[str, Any]:
    """L'esquema del perfil, apte per a una tool estricta i amb la gramatica mes petita possible
    SENSE perdre cap camp: les mateixes propietats, enums i tipus; sense les restriccions no
    admeses (el parser les aplica); `value` de forma fixa; cap propietat opcional; l'operador
    com a enum amb `null`. El parser del perfil continua sent el contracte: aquest esquema no
    accepta res que el parser no validi despres."""
    schema = copy.deepcopy(chunk_output_json_schema_product_v2())
    _drop_strict_keywords(schema)
    requirement = schema["$defs"]["RequirementOut"]["properties"]
    requirement["value"] = copy.deepcopy(_STRICT_VALUE)
    operators = schema["$defs"]["RequirementOperator"]["enum"]
    requirement["operator"] = {"enum": [*operators, None]}
    _all_required(schema)
    _drop_unused_definitions(schema)
    return schema
