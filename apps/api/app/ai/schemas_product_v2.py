"""Contracte de sortida del perfil `product-v2`.

Requisits, criteris i notes: EXACTAMENT els de V1 (`app.ai.schemas`). L'unic canvi es
la incertesa: a V1 era `{note, page_number?}` i es descartava; a `product-v2` es una
incertesa MATERIAL amb ambit, document, pagina i cita opcional, i es persisteix a
`requirement_uncertainties` (0062). La cita la valida el backend amb la mateixa regla
literal que l'evidencia; si no es literal, la incertesa es conserva sense cita.

Les incerteses es validen ELEMENT A ELEMENT (`parse_chunk_product_v2`): una incertesa
mal formada es descarta amb un rebuig de codis i mai invalida el chunk ni els seus
requisits. La resta del sobre es tan estricta com a V1.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, PrivateAttr, StringConstraints, ValidationError

from app.ai.schemas import ChunkExtractionOut, StrictModel
from app.models.enums import UncertaintyScope

SCHEMA_VERSION_PRODUCT_V2 = "requirements-schema-product-v2"

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

    #: Posicio ORIGINAL a la resposta de cada incertesa conservada.
    _uncertainty_indexes: tuple[int, ...] = PrivateAttr(default=())
    #: Incerteses descartades per esquema: `{item, index, stage, reason, detail, field}`.
    _uncertainty_rejections: tuple[dict[str, Any], ...] = PrivateAttr(default=())

    @property
    def uncertainty_indexes(self) -> tuple[int, ...]:
        return self._uncertainty_indexes or tuple(range(len(self.uncertainties)))

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
        "dict_type", "model_type", "model_attributes_type",
    }
)  # fmt: skip


def _uncertainty_schema_rejection(
    index: int, detail: str, field_name: str | None
) -> dict[str, Any]:
    """Una entrada de rebuig: posicio i codis, mai el valor ni el nom d'una clau inventada."""
    entry: dict[str, Any] = {
        "item": "uncertainty",
        "index": int(index),
        "stage": "schema",
        "reason": "schema_invalid",
        "detail": detail if detail in _SCHEMA_DETAILS else "other",
    }
    if field_name is not None:
        entry["field"] = field_name
    return entry


def _from_validation_error(index: int, exc: ValidationError) -> dict[str, Any]:
    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    first = errors[0] if errors else {}
    loc = first.get("loc") or ()
    field_name = (
        loc[0]
        if loc and isinstance(loc[0], str) and loc[0] in MaterialUncertaintyOut.model_fields
        else None
    )
    return _uncertainty_schema_rejection(index, str(first.get("type", "other")), field_name)


def parse_chunk_product_v2(payload: Any) -> ChunkExtractionOutProductV2:
    """Valida el sobre com V1 i les incerteses una a una.

    * Sobre (requisits, criteris, notes, claus d'arrel): estricte, com V1. Si falla,
      `ValidationError` i el chunk sencer es `schema_invalid`.
    * `uncertainties` que no es una llista: el camp sencer es descarta (un rebuig
      amb `index = -1`) i el chunk segueix.
    * Una incertesa invalida: es descarta NOMES aquella, amb un rebuig de codis (tipus
      d'error i camp de primer nivell; mai el valor).
    """
    if not isinstance(payload, dict) or "uncertainties" not in payload:
        return ChunkExtractionOutProductV2.model_validate(payload)
    raw = payload["uncertainties"]
    kept: list[MaterialUncertaintyOut] = []
    indexes: list[int] = []
    rejected: list[dict[str, Any]] = []
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        rejected.append(_uncertainty_schema_rejection(-1, "list_type", "uncertainties"))
        raw = []
    for index, item in enumerate(raw):
        try:
            kept.append(MaterialUncertaintyOut.model_validate(item))
        except ValidationError as exc:
            rejected.append(_from_validation_error(index, exc))
            continue
        indexes.append(index)
    parsed = ChunkExtractionOutProductV2.model_validate({**payload, "uncertainties": []})
    parsed.uncertainties = kept
    parsed._uncertainty_indexes = tuple(indexes)
    parsed._uncertainty_rejections = tuple(rejected)
    return parsed


def chunk_output_json_schema_product_v2() -> dict[str, Any]:
    return ChunkExtractionOutProductV2.model_json_schema()
