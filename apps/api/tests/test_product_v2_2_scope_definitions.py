"""Product-v2.2: definicio generica de cada ambit d'incertesa i regla de desempat.

Post-mortem de l'Attempt 4: el prompt v2.1 enumerava els nou ambits sense definir-ne cap. Que es
protegeix: cap ambit sense definicio (una sola per ambit, generada de l'enum), la regla de
desempat (naturalesa principal, no consequencia de l'incompliment; cap duplicat), cap concepte
del fixture del canari dins del prompt, el V1 intacte i l'esquema sense canvis.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata

import pytest

from app.ai.profiles import PRODUCT_V2, V1
from app.ai.prompts.requirements_product_v2 import SCOPE_DEFINITIONS
from app.ai.prompts.requirements_v1 import SYSTEM_PROMPT as V1_PROMPT
from app.models.enums import UncertaintyScope


def _block() -> str:
    prompt = PRODUCT_V2.system_prompt
    start = prompt.index("AMBITS DE LES INCERTESES")
    return prompt[start : prompt.index("MATERIAL NO CONFIABLE", start)]


def _definition_lines() -> dict[str, str]:
    lines = [line.strip() for line in _block().splitlines()]
    out: dict[str, str] = {}
    for scope in UncertaintyScope:
        found = [line for line in lines if line.startswith(f"- `{scope.value}`: ")]
        assert len(found) == 1, (scope, found)
        out[scope.value] = found[0]
    return out


def test_cada_ambit_te_exactament_una_definicio():
    assert set(SCOPE_DEFINITIONS) == set(UncertaintyScope)
    lines = _definition_lines()
    for scope, definition in SCOPE_DEFINITIONS.items():
        assert definition.strip() and lines[scope.value].endswith(definition)
    assert PRODUCT_V2.system_prompt.count("AMBITS DE LES INCERTESES") == 1


def test_la_regla_de_desempat_es_la_naturalesa_principal_i_sense_duplicats():
    block = _block()
    assert "tria UN unic `scope` segons la naturalesa\nprincipal del que regula" in block
    assert "no segons la consequencia secundaria del seu incompliment" in block
    assert "classifica pel que la clausula es, no pel que pot passar si no es\ncompleix" in block
    assert "No dupliquis una mateixa incertesa en diversos ambits." in block


def test_sizing_mai_es_la_capacitat_del_licitador_i_solvency_si():
    lines = _definition_lines()
    assert "mai la capacitat o qualificacio del licitador" in lines["sizing"]
    assert (
        "capacitat economica/financera, tecnica o professional del licitador" in lines["solvency"]
    )
    assert "quan no son propiament acreditacio de capacitat" in lines["admissibility"]


def _fold(text: str) -> str:
    """Minuscules, sense accents ni ela geminada: el fixture porta accents i el prompt no."""
    plain = unicodedata.normalize("NFKD", text.replace("l·l", "ll").replace("l.l", "ll"))
    return "".join(ch for ch in plain if not unicodedata.combining(ch)).lower()


def _occurrences(term: str, text: str) -> int:
    return len(re.findall(rf"\b{re.escape(_fold(term))}\b", _fold(text)))


@pytest.mark.parametrize(
    "fixture_term",
    ["classificació empresarial", "classificació", "grup", "subgrup", "ISO 9001", "sobre digital",
     "600.000", "4 hores", "enllumenat", "Vilasintètica", "CANARY"],
)  # fmt: skip
def test_el_prompt_no_codifica_cap_concepte_del_fixture(fixture_term):
    # Tot el prompt, sense accents i per paraula sencera: product-v2 no hi afegeix res que el V1
    # no tingues ja.
    assert _occurrences(fixture_term, PRODUCT_V2.system_prompt) == _occurrences(
        fixture_term, V1_PROMPT
    )


#: Cada revisio fixa el seu text: canviar-lo (una definicio, una regla) demana una
#: `PROMPT_VERSION` nova. Igual per a l'esquema JSON i la seva `schema_version`.
PROMPT_SHA256 = {
    "requirements-product-v2.2": "bff59cbf27803f4a74cf6ca96ded186d861614c54b6492240eea453db35ba93c",
}
SCHEMA_SHA256 = {
    "requirements-schema-product-v2.1": (
        "bf855a14c9551fc8bc5e66d184f32fa1b9906a20ad420fc402192480d754af33"
    ),
}


def test_el_text_i_l_esquema_estan_fixats_per_versio():
    prompt = hashlib.sha256(PRODUCT_V2.system_prompt.encode()).hexdigest()
    schema = json.dumps(PRODUCT_V2.json_schema(), sort_keys=True).encode()
    assert prompt == PROMPT_SHA256[PRODUCT_V2.prompt_version]
    assert hashlib.sha256(schema).hexdigest() == SCHEMA_SHA256[PRODUCT_V2.schema_version]


def test_la_revisio_es_nova_i_el_v1_i_l_esquema_no_canvien():
    assert PRODUCT_V2.prompt_version == "requirements-product-v2.2"
    assert PRODUCT_V2.schema_version == "requirements-schema-product-v2.1"
    assert PRODUCT_V2.extractor_version == "tender-requirements-product-v2"
    assert "AMBITS DE LES INCERTESES" not in V1_PROMPT
    assert V1.system_prompt == V1_PROMPT
