"""Envoltori dels models `auto` v3 (post-mortem del primer run real de L'Escala, 2026-10-14).

El run real va fallar amb `illegal_envelope:falten document_notes`: l'esquema enviat a Sonnet 5.5
no te `required` a l'arrel (les quatre llistes son opcionals, amb `default_factory`), pero el
validador exigia totes les `properties`. Ara:

* nomes es obligatori el que l'esquema ENVIAT declara obligatori;
* una llista opcional absent s'accepta i queda registrada (`absent_optional_list:<nom>`), sense
  inventar res: absent no es buida (unes incerteses absents no diuen que no n'hi hagi);
* `{}`, claus desconegudes, tipus incorrectes i respostes tallades continuen sent fatals;
* V1 (tool forcada) no canvia; la versio del transport passa a v3 i un run v2 no es repren.
"""
from __future__ import annotations

import pytest

pytest.importorskip("anthropic")

from app.ai.profiles import (  # noqa: E402
    ABSENT_OPTIONAL_LIST,
    INTERNAL_PROFILES,
    PROFILES,
    auto_tool_envelope_check,
    auto_tool_envelope_problem,
    transport_identity,
    transport_json_schema,
)
from app.ai.provider import StructuredResult, Usage  # noqa: E402
from app.ai.providers import fake  # noqa: E402
from app.ai.providers.anthropic import AUTO_TOOL_TRANSPORT_VERSION  # noqa: E402
from app.models.enums import AnalysisRunStatus  # noqa: E402
from tests.quota_support import pin_real_model, requirement  # noqa: E402
from tests.real_provider import RealNamedFake  # noqa: E402
from tests.test_anthropic_sonnet55_tool_transport import (  # noqa: E402
    EMPTY,
    PRODUCT_V2,
    SONNET_55,
    _configure,
    _partial_run,
    _provider,
    _resume,
)
from tests.test_llm_budget import _analyze  # noqa: E402
from tests.test_llm_budget import plec3 as plec3_fixture  # noqa: E402

plec3 = plec3_fixture
LISTS = ("requirements", "award_criteria", "document_notes", "uncertainties")


@pytest.fixture(autouse=True)
def _clean_fake():
    fake.reset()
    yield
    fake.reset()


def _without(*names: str) -> dict:
    return {key: value for key, value in EMPTY.items() if key not in names}


# --------------------------------------------------------------------------- #
# Unitat: el validador                                                          #
# --------------------------------------------------------------------------- #


def test_document_notes_absent_s_accepta_i_es_registra():
    check = auto_tool_envelope_check(_without("document_notes"), PRODUCT_V2)
    assert check.problem is None
    assert check.absent_optional == ("document_notes",)
    assert check.rules == [f"{ABSENT_OPTIONAL_LIST}:document_notes"]


def test_document_notes_buit_s_accepta_sense_cap_registre():
    check = auto_tool_envelope_check(EMPTY, PRODUCT_V2)
    assert (check.problem, check.absent_optional, check.rules) == (None, (), [])


@pytest.mark.parametrize("absent", LISTS)
def test_qualsevol_llista_opcional_absent_s_accepta_i_es_registra(absent):
    check = auto_tool_envelope_check(_without(absent), PRODUCT_V2)
    assert check.problem is None
    assert check.rules == [f"{ABSENT_OPTIONAL_LIST}:{absent}"]


def test_la_forma_de_la_resposta_real_de_l_escala_s_accepta():
    """Les claus exactes de la resposta del run a7b79748 (sense `document_notes`)."""
    data = {"requirements": [], "award_criteria": [], "uncertainties": []}
    assert auto_tool_envelope_check(data, PRODUCT_V2).rules == [
        f"{ABSENT_OPTIONAL_LIST}:document_notes"
    ]


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ({}, "falten totes les llistes"),
        ({"notes": []}, "falten totes les llistes"),  # nomes claus inventades
        ({**EMPTY, "extraction_id": "e1"}, "claus inesperades"),
        ({**_without("document_notes"), "extra_key": []}, "claus inesperades"),
        ({**EMPTY, "requirements": "[]"}, "no son llistes"),
        ({**EMPTY, "requirements": None}, "no son llistes"),
        ({**EMPTY, "uncertainties": {}}, "no son llistes"),
        ({"document_notes": "x"}, "no son llistes"),
        ([], "no es un objecte"),
        ("{}", "no es un objecte"),
    ],
)
def test_els_envoltoris_il_legals_continuen_sent_fatals(data, problem):
    assert problem in auto_tool_envelope_problem(data, PRODUCT_V2)


def test_els_obligatoris_de_l_esquema_enviat_continuen_sent_obligatoris():
    schema = {**PRODUCT_V2.json_schema(), "required": ["requirements", "uncertainties"]}
    assert "falten uncertainties" == auto_tool_envelope_problem(
        _without("uncertainties"), PRODUCT_V2, schema=schema
    )
    check = auto_tool_envelope_check(_without("document_notes"), PRODUCT_V2, schema=schema)
    assert check.problem is None and check.absent_optional == ("document_notes",)


def test_amb_la_tool_estricta_l_esquema_enviat_les_exigeix_totes(monkeypatch):
    _configure(monkeypatch, strict=True)
    strict = transport_json_schema(PRODUCT_V2, _provider())
    assert set(strict.get("required") or ()) == set(LISTS)
    assert auto_tool_envelope_problem(
        _without("document_notes"), PRODUCT_V2, schema=strict
    ) == "falten document_notes"


@pytest.mark.parametrize("strict", [False, True])
def test_contracte_el_validador_no_exigeix_res_que_l_esquema_enviat_no_declari(monkeypatch, strict):
    """Per a cada perfil amb transport `auto`: les claus que el validador fa obligatories son
    EXACTAMENT el `required` de l'esquema que viatja."""
    _configure(monkeypatch, strict=strict)
    checked = 0
    for profile in {**PROFILES, **INTERNAL_PROFILES}.values():
        has_transport = profile.strict_json_schema if strict else profile.tool_transport_version
        if has_transport is None:
            continue
        sent = transport_json_schema(profile, _provider())
        known = set(sent.get("properties") or ())
        full = {key: [] for key in known}
        mandatory = {
            key for key in known
            if auto_tool_envelope_problem(
                {k: v for k, v in full.items() if k != key}, profile, schema=sent
            ) is not None
        }  # fmt: skip
        assert mandatory == set(sent.get("required") or ()), profile.name
        checked += 1
    assert checked >= 1


# --------------------------------------------------------------------------- #
# Integracio: el pipeline                                                       #
# --------------------------------------------------------------------------- #


def _sonnet(monkeypatch) -> None:
    pin_real_model(monkeypatch, model=SONNET_55, input_per_million=2.0, output_per_million=10.0)
    _configure(monkeypatch)


def _reply(data: dict, *, stop_reason: str = "tool_use") -> StructuredResult:
    usage = Usage(input_tokens=1000, output_tokens=10, request_id="r", stop_reason=stop_reason)
    return StructuredResult(data=data, usage=usage, model=SONNET_55)


async def test_un_chunk_sense_document_notes_es_completa_i_ho_registra(session, plec3, monkeypatch):
    _sonnet(monkeypatch)
    extraction = plec3["extraction"].id
    responses = [
        _reply({"requirements": [requirement(extraction, 1)], "award_criteria": [],
                "uncertainties": []}),  # la forma de la resposta real
        _reply({**EMPTY, "requirements": [requirement(extraction, 2)]}),  # llista buida present
        _reply({"requirements": [requirement(extraction, 3)], "award_criteria": [],
                "document_notes": []}),  # incerteses ABSENTS
    ]  # fmt: skip
    provider = RealNamedFake(responses=responses, model=SONNET_55)

    run = await _analyze(session, plec3, provider=provider, profile=PRODUCT_V2)

    assert provider.calls == 3
    assert run.status is AnalysisRunStatus.COMPLETED
    assert run.requirements_extracted == 3 and run.evidence_rejected == 0
    chunks = run.chunk_coverage["chunks"]
    assert [c["status"] for c in chunks] == ["completed"] * 3
    assert [c.get("normalised") for c in chunks] == [
        [f"{ABSENT_OPTIONAL_LIST}:document_notes"],
        None,  # buida i present: res a registrar
        [f"{ABSENT_OPTIONAL_LIST}:uncertainties"],  # absent != «cap incertesa comprovada»
    ]
    # Cap dada inventada: cap nota ni incertesa persistida per a les llistes absents.
    assert [c.get("notes") for c in chunks] == [None, None, None]
    # Product-v2 mai vigent; el transport desat es el v3, i el que ha viatjat no canvia.
    assert run.is_current is False
    assert run.provider_metadata["transport"]["transport"] == AUTO_TOOL_TRANSPORT_VERSION
    assert AUTO_TOOL_TRANSPORT_VERSION == "anthropic-auto-tool-v3"
    assert [p["schema"] for p in fake.SENT_PAYLOADS] == [PRODUCT_V2.json_schema()] * 3


@pytest.mark.parametrize("envelope", [{}, {"notes": []}, {**EMPTY, "requirements": {}}])
async def test_un_envoltori_il_legal_continua_aturant_el_run(session, plec3, monkeypatch, envelope):
    _sonnet(monkeypatch)
    provider = RealNamedFake(responses=[_reply(envelope)] * 3, model=SONNET_55)

    run = await _analyze(session, plec3, provider=provider, profile=PRODUCT_V2)

    assert provider.calls == 1
    assert [c.get("error") for c in run.chunk_coverage["chunks"]] == [
        "illegal_envelope", "not_sent", "not_sent"
    ]  # fmt: skip
    assert run.requirements_extracted == 0


async def test_una_resposta_tallada_es_rebutja_encara_que_l_envoltori_sigui_valid(
    session, plec3, monkeypatch
):
    _sonnet(monkeypatch)
    extraction = plec3["extraction"].id
    truncated = _reply({"requirements": [requirement(extraction, 1)]}, stop_reason="max_tokens")
    provider = RealNamedFake(responses=[truncated] * 3, model=SONNET_55)

    run = await _analyze(session, plec3, provider=provider, profile=PRODUCT_V2)

    first = run.chunk_coverage["chunks"][0]
    assert first["status"] != "completed" and first.get("error") == "output_truncated"
    assert run.status is not AnalysisRunStatus.COMPLETED


async def test_v1_amb_tool_forcada_no_canvia(session, plec3):
    """El cami per defecte (V1, model de tool forcada) accepta les llistes absents com sempre,
    via `normalise_output`, i no hi apareix cap regla d'absencia."""
    run = await _analyze(session, plec3)

    assert run.status is AnalysisRunStatus.COMPLETED
    assert run.requirements_extracted == 3
    for chunk in run.chunk_coverage["chunks"]:
        assert not any(ABSENT_OPTIONAL_LIST in rule for rule in chunk.get("normalised") or [])
    assert run.provider_metadata is None or "transport" not in run.provider_metadata


def test_un_run_v2_no_es_repren_amb_el_transport_v3(monkeypatch):
    _configure(monkeypatch)
    v3 = transport_identity(PRODUCT_V2, _provider())
    v2 = {**v3, "transport": "anthropic-auto-tool-v2"}
    assert v3["transport"] == "anthropic-auto-tool-v3"
    assert _resume(_partial_run(v2)) == "transport_mismatch"
    assert _resume(_partial_run(v3)) == "chunk_plan_mismatch"  # mateix transport: passa la porta
