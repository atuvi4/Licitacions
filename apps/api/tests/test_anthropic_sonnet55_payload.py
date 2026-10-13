"""Tool `auto` + `strict` (capacitat generica, `LLM_STRICT_TOOL_MODELS`). Cap socket, cap clau.

Sonnet 5.5 ja NO fa servir aquest cami: el servidor va refusar la gramatica estricta v1 i v2
(Attempts 5 i 6). Ara va per la tool no estricta (`test_anthropic_sonnet55_tool_transport.py`).
Aquest fitxer protegeix el cami estricte tal com queda, configurat explicitament.

Sonnet 5.5 refusa la tool forcada (`tool_choice` `tool` o `any`: 400) i `thinking: disabled`
(400); pensa per defecte (adaptatiu). Que es protegeix:

* el cos HTTP EXACTE per a Sonnet 5.5: model, `max_tokens`, sistema = prompt del perfil +
  `AUTO_TOOL_INSTRUCTION`, la tool amb `strict: true` i l'esquema de TRANSPORT estricte,
  `tool_choice` `auto`, `thinking` `between_tools`; cap mostreig, cap `output_config`;
* una crida a la tool es processa; cap crida, o mes d'una, es FATAL (cap text, cap JSON del
  text, cap reintent); `refusal`, `max_tokens` i `model_context_window_exceeded` mai es fan
  servir;
* un esquema no apte per a la tool estricta, o `disabled` + `between_tools` alhora: no s'envia;
* la resta de models (Haiku 5.5, Haiku 4.5, V1) envien el mateix cos de sempre i no canvien de
  comportament;
* l'esquema estricte respecta els limits documentats (per definicio i per us) i el parser del
  perfil l'accepta igual;
* la porta refusa abans de cap crida un perfil sense esquema estricte o un `thinking` no
  configurat; el transport entra a la identitat de l'analisi NOMES d'aquests models.
"""

from __future__ import annotations

import hashlib
import json

import pytest

pytest.importorskip("anthropic")

import anthropic  # noqa: E402
import httpx2  # noqa: E402
import sqlalchemy as sa  # noqa: E402

from app.ai.profiles import (  # noqa: E402
    INTERNAL_PROFILES,
    STRICT_SCHEMA_UNAVAILABLE,
    THINKING_MISCONFIGURED,
    THINKING_UNCONFIGURED,
    V1,
    transport_identity,
    transport_json_schema,
)
from app.ai.provider import (  # noqa: E402
    MultipleToolCallsError,
    NoToolCallError,
    OutputTruncatedError,
    ProviderError,
)
from app.ai.providers import fake  # noqa: E402
from app.ai.providers.anthropic import (  # noqa: E402
    _TOOL_NAME,
    AUTO_TOOL_INSTRUCTION,
    AUTO_TOOL_TRANSPORT_VERSION,
    AnthropicProvider,
    strict_schema_counts,
    strict_schema_problems,
)
from app.ai.schemas_product_v2 import (  # noqa: E402
    TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT,
    parse_chunk_product_v2,
)
from app.core.config import Settings, settings  # noqa: E402
from app.core.errors import AnalysisNotAllowedError  # noqa: E402
from app.models.requirements import RequirementExtractionRun  # noqa: E402
from app.services.requirement_analysis import analysis_identity  # noqa: E402
from tests.quota_support import pin_real_model  # noqa: E402
from tests.real_provider import RealNamedFake  # noqa: E402
from tests.test_llm_budget import _analyze  # noqa: E402
from tests.test_llm_budget import plec3 as plec3_fixture  # noqa: E402

#: Tender public de 3 pagines i 3 chunks (fixture de `test_llm_budget`).
plec3 = plec3_fixture

SONNET_55 = "claude-sonnet-5-5"
HAIKU_55 = "claude-haiku-5-5"
HAIKU_45 = "claude-haiku-4-5-20251001"
PRODUCT_V2 = INTERNAL_PROFILES["product-v2"]
TOOL_DESCRIPTION = (
    "Registra els requisits i criteris trobats al fragment. "
    "Utilitza NOMES informacio explicita del text proporcionat."
)
EMPTY = {"requirements": [], "award_criteria": [], "document_notes": [], "uncertainties": []}
#: La instruccio de sortida, fixada amb la versio del transport: canviar-ne el text demana una
#: `AUTO_TOOL_TRANSPORT_VERSION` nova (entra a la identitat de l'analisi).
INSTRUCTION_SHA256 = {
    "anthropic-auto-tool-v2": "c417765ab7339286e609b56914889b72ba5afd6155d19dfd34152a7900ec7cab",
}


@pytest.fixture(autouse=True)
def _clean_fake():
    """`fake.SENT_PAYLOADS` es global: cada prova comenca i acaba neta."""
    fake.reset()
    yield
    fake.reset()


def _message(stop_reason: str, *, content: list[dict] | None = None, model: str = SONNET_55):
    tool = {"type": "tool_use", "id": "toolu_local", "name": _TOOL_NAME, "input": EMPTY}
    return {
        "id": "msg_local", "type": "message", "role": "assistant", "model": model,
        "content": content if content is not None else [tool],
        "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1234, "output_tokens": 56},
    }  # fmt: skip


@pytest.fixture
def sonnet(monkeypatch):
    """Sonnet 5.5 configurat com a l'Attempt 5, amb un transport HTTP local."""
    sent: list[dict] = []
    replies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=replies.pop(0), headers={"request-id": "req_local"})

    real_client = anthropic.AsyncAnthropic

    def local_client(**kwargs):
        return real_client(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
        )

    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "clau-local-sense-xarxa")
    monkeypatch.setattr(anthropic, "AsyncAnthropic", local_client)
    monkeypatch.setattr(settings, "LLM_AUTO_TOOL_CHOICE_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_STRICT_TOOL_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_THINKING_BETWEEN_TOOLS_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_THINKING_DISABLED_MODELS", [HAIKU_55])

    def prepare(model: str = SONNET_55, *, reply: dict | None = None) -> AnthropicProvider:
        monkeypatch.setattr(settings, "LLM_MODEL", model)
        if reply is not None:
            replies.append(reply)
        return AnthropicProvider(max_retries=0)

    return prepare, sent


async def _call(provider: AnthropicProvider, schema: dict | None = None):
    return await provider.extract_structured(
        system=PRODUCT_V2.system_prompt,
        user_content="<fragment local>",
        json_schema=schema if schema is not None else PRODUCT_V2.strict_json_schema(),
        max_output_tokens=settings.LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS,
    )


def _tool(**overrides) -> dict:
    return {"type": "tool_use", "id": "toolu_local", "name": _TOOL_NAME, "input": EMPTY} | overrides


# --------------------------------------------------------------------------- #
# Cos HTTP i respostes                                                          #
# --------------------------------------------------------------------------- #


async def test_sonnet_55_envia_exactament_el_cos_auto_strict_i_between_tools(sonnet):
    prepare, sent = sonnet
    result = await _call(prepare(reply=_message("tool_use")))

    assert sent == [
        {
            "model": SONNET_55,
            "max_tokens": 8000,
            "system": PRODUCT_V2.system_prompt.rstrip("\n") + AUTO_TOOL_INSTRUCTION,
            "messages": [{"role": "user", "content": "<fragment local>"}],
            "tools": [
                {
                    "name": _TOOL_NAME,
                    "description": TOOL_DESCRIPTION,
                    "input_schema": PRODUCT_V2.strict_json_schema(),
                    "strict": True,
                }
            ],
            "tool_choice": {"type": "auto"},
            "thinking": {"type": "between_tools"},
        }
    ]
    assert "\n\n\n" not in sent[0]["system"][-len(AUTO_TOOL_INSTRUCTION) - 2 :]
    assert "Respon NOMES cridant la tool `record_extraction`" in sent[0]["system"]
    assert (result.data, result.model) == (EMPTY, SONNET_55)
    assert (result.usage.input_tokens, result.usage.output_tokens) == (1234, 56)


def test_la_instruccio_de_sortida_esta_fixada_per_la_versio_del_transport():
    digest = hashlib.sha256(AUTO_TOOL_INSTRUCTION.encode()).hexdigest()
    assert digest == INSTRUCTION_SHA256[AUTO_TOOL_TRANSPORT_VERSION]


async def test_una_resposta_textual_sense_tool_es_fatal_i_no_es_llegeix(sonnet):
    prepare, sent = sonnet
    text = [{"type": "text", "text": json.dumps(EMPTY)}]  # JSON al text: mai es fa servir
    with pytest.raises(NoToolCallError) as caught:
        await _call(prepare(reply=_message("end_turn", content=text)))
    assert caught.value.fatal is True
    assert caught.value.usage.output_tokens == 56
    assert len(sent) == 1


@pytest.mark.parametrize("model", [HAIKU_55, HAIKU_45, "claude-test-model"])
async def test_sense_tool_els_altres_models_no_canvien_no_es_fatal(sonnet, model):
    prepare, _sent = sonnet
    text = [{"type": "text", "text": "res"}]
    with pytest.raises(NoToolCallError) as caught:
        await _call(
            prepare(model, reply=_message("end_turn", content=text, model=model)),
            schema=PRODUCT_V2.json_schema(),
        )
    assert caught.value.fatal is False


async def test_mes_d_una_crida_a_la_tool_es_fatal(sonnet):
    prepare, _sent = sonnet
    with pytest.raises(MultipleToolCallsError) as caught:
        await _call(prepare(reply=_message("tool_use", content=[_tool(), _tool(id="toolu_2")])))
    assert (caught.value.code, caught.value.fatal) == ("multiple_tool_calls", True)
    assert caught.value.usage.output_tokens == 56


async def test_la_tool_forcada_dels_altres_models_continua_triant_la_primera(sonnet):
    prepare, _sent = sonnet
    first = _tool(input={**EMPTY, "document_notes": [{"note": "primera"}]})
    reply = _message("tool_use", content=[first, _tool(id="toolu_2")], model=HAIKU_45)
    result = await _call(prepare(HAIKU_45, reply=reply), schema=PRODUCT_V2.json_schema())
    assert result.data["document_notes"] == [{"note": "primera"}]


async def test_el_text_i_el_pensament_al_costat_de_la_tool_s_ignoren(sonnet):
    prepare, _sent = sonnet
    content = [
        {"type": "thinking", "thinking": "", "signature": "sig_local"},
        {"type": "text", "text": "Aqui tens el resultat."},
        _tool(),
    ]
    result = await _call(prepare(reply=_message("tool_use", content=content)))
    assert result.data == EMPTY


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
async def test_una_resposta_tallada_mai_es_fa_servir(sonnet, stop_reason):
    prepare, _sent = sonnet
    with pytest.raises(OutputTruncatedError) as caught:
        await _call(prepare(reply=_message(stop_reason)))
    assert caught.value.usage.stop_reason == stop_reason


async def test_un_refus_mai_es_fa_servir(sonnet):
    prepare, _sent = sonnet
    with pytest.raises(ProviderError) as caught:
        await _call(prepare(reply=_message("refusal")))
    assert caught.value.code == "refusal" and caught.value.usage.output_tokens == 56


async def test_un_esquema_no_apte_per_a_la_tool_estricta_no_s_envia(sonnet):
    prepare, sent = sonnet
    with pytest.raises(ProviderError) as caught:
        await _call(prepare(), schema=PRODUCT_V2.json_schema())  # l'esquema del perfil
    assert (caught.value.code, caught.value.fatal) == ("strict_schema_unsupported", True)
    assert sent == []


async def test_disabled_i_between_tools_alhora_no_s_envia(sonnet, monkeypatch):
    prepare, sent = sonnet
    monkeypatch.setattr(settings, "LLM_THINKING_DISABLED_MODELS", [SONNET_55])
    with pytest.raises(ProviderError) as caught:
        await _call(prepare())
    assert (caught.value.code, caught.value.fatal) == ("provider_misconfigured", True)
    assert sent == []


@pytest.mark.parametrize(
    ("model", "thinking"),
    [(HAIKU_55, {"type": "disabled"}), (HAIKU_45, None), ("claude-test-model", None)],
)
async def test_la_resta_de_models_envien_el_cos_de_sempre(sonnet, model, thinking):
    prepare, sent = sonnet
    reply = _message("tool_use", model=model)
    await _call(prepare(model, reply=reply), schema=PRODUCT_V2.json_schema())
    expected = {
        "model": model,
        "max_tokens": 8000,
        "system": PRODUCT_V2.system_prompt,
        "messages": [{"role": "user", "content": "<fragment local>"}],
        "tools": [
            {
                "name": _TOOL_NAME,
                "description": TOOL_DESCRIPTION,
                "input_schema": PRODUCT_V2.json_schema(),
            }
        ],
        "tool_choice": {"type": "tool", "name": _TOOL_NAME},
    }
    if thinking is not None:
        expected["thinking"] = thinking
    assert sent == [expected]


def test_la_configuracio_per_defecte_no_canvia_cap_model():
    fields = Settings.model_fields
    assert fields["LLM_AUTO_TOOL_CHOICE_MODELS"].default_factory() == []
    assert fields["LLM_THINKING_BETWEEN_TOOLS_MODELS"].default_factory() == []


# --------------------------------------------------------------------------- #
# Esquema de transport estricte                                                 #
# --------------------------------------------------------------------------- #


#: Cada versio del transport fixa el seu esquema exacte: canviar-lo demana una versio nova (entra
#: a la identitat de l'analisi). La v1 (`...-strict`) va rebre un 400 a l'Attempt 5 («The
#: compiled grammar is too large»): no es pot tornar a enviar.
STRICT_SCHEMA_SHA256 = {
    "requirements-transport-product-v2.1-strict.2": (
        "e28780bdb863a40de31a0f46223ed30f1a6a4077c232f8b000dee07ef8c37d70"
    ),
}
STRICT_V1_SHA256 = "e46829bbbdc6d2850d9f2c591143c375e93d40fc7dcdee4c594dc36f5700514f"


def _sha(schema: dict) -> str:
    return hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()


def test_l_esquema_estricte_respecta_els_limits_i_el_del_perfil_no():
    strict = PRODUCT_V2.strict_json_schema()
    assert strict_schema_problems(strict) == []
    # Cap propietat opcional; unions: 13 per definicio, 14 per us (l'evidencia hi es dues
    # vegades). Limits documentats: 24 i 16.
    assert strict_schema_counts(strict) == {"per_definition": (0, 13), "per_usage": (0, 14)}
    problems = strict_schema_problems(PRODUCT_V2.json_schema())
    assert "parametres opcionals: 28" in problems
    assert any(p.endswith("value/anyOf/0: additionalProperties") for p in problems)
    assert PRODUCT_V2.strict_transport_version == TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT
    assert (
        TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT == "requirements-transport-product-v2.1-strict.2"
    )


def test_l_esquema_de_transport_esta_fixat_per_versio_i_no_es_el_refusat():
    strict = PRODUCT_V2.strict_json_schema()
    assert _sha(strict) == STRICT_SCHEMA_SHA256[TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT]
    assert _sha(strict) != STRICT_V1_SHA256


def test_cap_objecte_del_transport_te_propietats_opcionals():
    def objects(node, mapping=False):
        if isinstance(node, list):
            for value in node:
                yield from objects(value)
        elif isinstance(node, dict):
            if mapping:
                for value in node.values():
                    yield from objects(value)
                return
            if "properties" in node:
                yield node
            for key, value in node.items():
                yield from objects(value, key in ("properties", "$defs"))

    found = list(objects(PRODUCT_V2.strict_json_schema()))
    assert len(found) == 7  # arrel, 5 definicions d'objecte i el `value`
    for node in found:
        assert node["required"] == list(node["properties"])
        assert node["additionalProperties"] is False


def test_el_comprovador_de_l_esquema_estricte_es_una_llista_blanca():
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "a": {"oneOf": [{"type": "string"}]},
            "b": {"type": ["object", "null"]},
            "c": {"type": "string", "format": "regex"},
            "d": {"$ref": "#/$defs/R"},
            "e": {"$ref": "#/$defs/maximum"},
            "f": {"type": "string", "enum": ["maxLength"], "default": "minimum"},
        },
        "$defs": {
            "R": {"type": "object", "additionalProperties": False,
                  "properties": {"r": {"$ref": "#/$defs/R"}}},
            "maximum": {"type": "string"},
        },
    }  # fmt: skip
    assert strict_schema_problems(schema) == [
        "#/properties/a: oneOf",
        "#/properties/b: additionalProperties",
        "#/properties/c: format regex",
        "#/$defs/R: esquema recursiu",
    ]


def _accepted(prop: dict, defs: dict) -> frozenset:
    """Que admet una propietat, en termes de tipus JSON i valors d'enum (sense restriccions)."""
    if "$ref" in prop:
        return _accepted(defs[prop["$ref"].split("/")[-1]], defs)
    if "anyOf" in prop:
        return frozenset().union(*(_accepted(branch, defs) for branch in prop["anyOf"]))
    if "enum" in prop:  # `null` dins d'un enum es el mateix que el tipus `null`
        return frozenset(("type", "null") if v is None else ("enum", v) for v in prop["enum"])
    kind = prop.get("type")
    if kind == "array":
        return frozenset({("array", _accepted(prop["items"], defs))})
    return frozenset({("type", kind)})


def test_el_transport_es_el_mateix_contracte_que_el_canonic():
    normal, strict = PRODUCT_V2.json_schema(), PRODUCT_V2.strict_json_schema()
    ndefs, sdefs = normal["$defs"], strict["$defs"]
    assert set(sdefs) == set(ndefs) - {"RequirementOperator"}  # ara inline, com a enum
    for owner_normal, owner_strict in [(normal, strict)] + [
        (ndefs[name], sdefs[name]) for name in sdefs if "properties" in ndefs[name]
    ]:
        assert list(owner_strict["properties"]) == list(owner_normal["properties"])
        for name, prop in owner_normal["properties"].items():
            expected = _accepted(prop, ndefs)
            got = _accepted(owner_strict["properties"][name], sdefs)
            if name == "value":  # objecte lliure -> objecte de forma fixa: tots dos `object`
                assert expected == got == {("type", "object"), ("type", "null")}
            else:
                assert got == expected, name
    # Paraules clau (no noms de propietat: `title` es una propietat del requisit).
    dropped = {"minLength", "maxLength", "minimum", "maximum", "default", "title"}
    assert dropped & set(_keys(strict)) == set()
    assert dropped & set(_keys(normal))  # el canonic si que les te


def _keys(node, mapping=False):
    if isinstance(node, list):
        for value in node:
            yield from _keys(value)
    elif isinstance(node, dict):
        for key, value in node.items():
            if not mapping:
                yield key
            yield from _keys(value, not mapping and key in ("properties", "$defs"))


def test_valid_pel_transport_no_vol_dir_valid_pel_producte():
    # Dins del transport (cap restriccio de llargada ni de rang), pero el parser ho rebutja:
    # titol buit, pagina 0, cita buida i nota de mes de 500 caracters. Rebutjat element a
    # element, com sempre; res no es coacciona.
    evidence = {"extraction_id": "e1", "page_number": 0, "quote": "", "section": None,
                "evidence_role": "primary"}  # fmt: skip
    payload = {
        **EMPTY,
        "requirements": [
            {"category": "insurance", "title": "", "obligation": "mandatory", "operator": None,
             "scope": "unknown", "lot_labels": [], "description": None, "confidence": "high",
             "value": None, "evidence": [evidence]},
        ],
        "uncertainties": [
            {"scope": "solvency", "extraction_id": "e1", "page_number": 5, "quote": None,
             "note": "x" * 501},
        ],
    }  # fmt: skip
    parsed = parse_chunk_product_v2(payload)
    assert parsed.requirements == [] and parsed.uncertainties == []
    assert parsed._schema_rejections and parsed._uncertainty_rejections


def test_els_nuls_explicits_del_transport_equivalen_als_valors_omesos():
    evidence = {"extraction_id": "e1", "page_number": 3, "quote": "x"}
    omitted = {"category": "insurance", "title": "RC", "evidence": [evidence]}
    explicit = {
        **omitted,
        "obligation": "unknown", "operator": None, "scope": "unknown", "lot_labels": [],
        "description": None, "value": None, "confidence": "low",
        "evidence": [{**evidence, "section": None, "evidence_role": "primary"}],
    }  # fmt: skip
    one = parse_chunk_product_v2({**EMPTY, "requirements": [omitted]}).requirements
    two = parse_chunk_product_v2({**EMPTY, "requirements": [explicit]}).requirements
    assert [r.model_dump() for r in one] == [r.model_dump() for r in two]


@pytest.mark.parametrize(
    "value",
    [{"amount": 600000, "unit": "EUR", "text": None}, {"amount": None, "unit": None, "text": None},
     None],
)  # fmt: skip
def test_el_parser_del_perfil_accepta_les_formes_estrictes_de_value(value):
    # Un `value` amb tots els camps nuls es desa tal qual (no es coacciona a `null`).
    evidence = {"extraction_id": "e1", "page_number": 3, "quote": "x", "section": None,
                "evidence_role": "primary"}  # fmt: skip
    payload = {
        **EMPTY,
        "requirements": [
            {"category": "insurance", "title": "RC", "obligation": "mandatory", "operator": "gte",
             "scope": "unknown", "lot_labels": [], "description": None, "confidence": "high",
             "value": value, "evidence": [evidence]},
        ],
    }  # fmt: skip
    parsed = parse_chunk_product_v2(payload)
    assert len(parsed.requirements) == 1 and parsed.requirements[0].value == value
    assert not parsed._schema_rejections


# --------------------------------------------------------------------------- #
# Porta, identitat i canonada                                                   #
# --------------------------------------------------------------------------- #


def _provider(name: str, model: str):
    return type("P", (), {"name": name, "model": model})()


def test_l_esquema_que_viatja_depen_del_model(monkeypatch):
    monkeypatch.setattr(settings, "LLM_AUTO_TOOL_CHOICE_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_STRICT_TOOL_MODELS", [SONNET_55])
    assert transport_json_schema(PRODUCT_V2, _provider("anthropic", SONNET_55)) == (
        PRODUCT_V2.strict_json_schema()
    )
    for other in (_provider("anthropic", HAIKU_55), _provider("fake", SONNET_55)):
        assert transport_json_schema(PRODUCT_V2, other) == PRODUCT_V2.json_schema()
    with pytest.raises(ProviderError) as caught:
        transport_json_schema(V1, _provider("anthropic", SONNET_55))
    assert (caught.value.code, caught.value.fatal) == (STRICT_SCHEMA_UNAVAILABLE, True)


def test_el_transport_entra_a_la_identitat_nomes_dels_models_de_tool_estricta(monkeypatch):
    sonnet, haiku = _provider("anthropic", SONNET_55), _provider("anthropic", HAIKU_55)
    before = {
        model: analysis_identity([], profile=PRODUCT_V2, provider=provider)
        for model, provider in ((SONNET_55, sonnet), (HAIKU_55, haiku))
    }
    monkeypatch.setattr(settings, "LLM_AUTO_TOOL_CHOICE_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_STRICT_TOOL_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_THINKING_BETWEEN_TOOLS_MODELS", [SONNET_55])
    assert transport_identity(PRODUCT_V2, haiku) is None
    assert transport_identity(PRODUCT_V2, sonnet) == {
        "transport": AUTO_TOOL_TRANSPORT_VERSION,
        "strict": True,
        "schema": TRANSPORT_SCHEMA_VERSION_PRODUCT_V2_STRICT,
        "thinking": "between_tools",
    }
    assert analysis_identity([], profile=PRODUCT_V2, provider=haiku) == before[HAIKU_55]
    assert analysis_identity([], profile=PRODUCT_V2, provider=sonnet) != before[SONNET_55]


async def _sonnet_pipeline(monkeypatch, *, between_tools: list[str], disabled: list[str]):
    pin_real_model(monkeypatch, model=SONNET_55, input_per_million=2.0, output_per_million=10.0)
    monkeypatch.setattr(settings, "LLM_AUTO_TOOL_CHOICE_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_STRICT_TOOL_MODELS", [SONNET_55])
    monkeypatch.setattr(settings, "LLM_THINKING_BETWEEN_TOOLS_MODELS", between_tools)
    monkeypatch.setattr(settings, "LLM_THINKING_DISABLED_MODELS", disabled)
    return RealNamedFake(model=SONNET_55)


@pytest.mark.parametrize(
    ("profile", "between_tools", "disabled", "code"),
    [
        (V1, [SONNET_55], [], STRICT_SCHEMA_UNAVAILABLE),
        (PRODUCT_V2, [], [], THINKING_UNCONFIGURED),
        (PRODUCT_V2, [SONNET_55], [SONNET_55], THINKING_MISCONFIGURED),
    ],
)
async def test_la_porta_refusa_abans_de_cap_crida(
    session, plec3, monkeypatch, profile, between_tools, disabled, code
):
    provider = await _sonnet_pipeline(monkeypatch, between_tools=between_tools, disabled=disabled)

    with pytest.raises(AnalysisNotAllowedError) as caught:
        await _analyze(session, plec3, provider=provider, profile=profile)

    assert caught.value.code == code
    assert provider.calls == 0
    assert await session.scalar(sa.select(sa.func.count(RequirementExtractionRun.id))) == 0


async def test_la_canonada_envia_l_esquema_estricte_a_sonnet_55(session, plec3, monkeypatch):
    provider = await _sonnet_pipeline(monkeypatch, between_tools=[SONNET_55], disabled=[])

    await _analyze(session, plec3, provider=provider, profile=PRODUCT_V2)

    assert provider.calls == 3
    assert [sent["schema"] for sent in fake.SENT_PAYLOADS] == [PRODUCT_V2.strict_json_schema()] * 3
