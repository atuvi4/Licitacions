"""Normalitzacio estricta abans de Pydantic: nomes els dos patrons observats.

Els payloads reprodueixen la FORMA dels chunks que van fallar per
`schema_invalid` al run real `e0fc136c` (Palamos 2024-3137). El contingut no es
el del plec: nomes se'n va desar la diagnosi estructural (`path`, `type`).

* chunks 1 i 20: `list_type` a `requirements`, `award_criteria`,
  `document_notes` i `uncertainties` (les quatre llistes com a text JSON);
* chunk 3: `list_type` nomes a `requirements`;
* chunk 8: `extraction_id` a l'arrel, `extra_forbidden`.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.ai.output_normalization import normalise_output
from app.ai.schemas import ChunkExtractionOut
from app.ai.schemas_v2 import ChunkExtractionOutV2

EXTRACTION_ID = "84ba6a4b-a92d-4b04-ab73-87bebcc56384"


def _requirement() -> dict:
    return {
        "category": "economic_solvency",
        "obligation": "mandatory",
        "title": "Volum anual de negoci",
        "confidence": "high",
        "evidence": [
            {"extraction_id": EXTRACTION_ID, "page_number": 6, "quote": "volum anual de negocis"}
        ],
    }


def _criterion() -> dict:
    return {
        "name": "Centre de suport en catala",
        "evidence": [{"extraction_id": EXTRACTION_ID, "page_number": 27, "quote": "catala"}],
    }


def _normalise(payload: dict, model=ChunkExtractionOut):
    return normalise_output(payload, model, context_extraction_ids={EXTRACTION_ID})


def _error_types(payload: dict, model=ChunkExtractionOut) -> set[tuple[str, str]]:
    with pytest.raises(ValidationError) as caught:
        model.model_validate(payload)
    return {(".".join(str(p) for p in e["loc"]), e["type"]) for e in caught.value.errors()}


# --------------------------------------------------------------------------- #
# Els patrons observats                                                         #
# --------------------------------------------------------------------------- #


def test_chunks_1_i_20_les_quatre_llistes_com_a_text_json() -> None:
    payload = {
        "requirements": json.dumps([_requirement()], ensure_ascii=False),
        "award_criteria": json.dumps([_criterion()], ensure_ascii=False),
        "document_notes": json.dumps([{"note": "Annex V", "page_number": 7}]),
        "uncertainties": "[]",
    }
    # Sense normalitzar, es exactament la diagnosi que es va desar.
    assert _error_types(payload) == {
        ("requirements", "list_type"),
        ("award_criteria", "list_type"),
        ("document_notes", "list_type"),
        ("uncertainties", "list_type"),
    }

    data, applied = _normalise(payload)
    parsed = ChunkExtractionOut.model_validate(data)

    assert applied == [
        "requirements:json_string_list",
        "award_criteria:json_string_list",
        "document_notes:json_string_list",
        "uncertainties:json_string_list",
    ]
    assert parsed.requirements[0].evidence[0].quote == "volum anual de negocis"
    assert parsed.award_criteria[0].name == "Centre de suport en catala"
    assert parsed.uncertainties == []


def test_chunk_3_nomes_requirements_com_a_text_json() -> None:
    payload = {
        "requirements": json.dumps([_requirement(), _requirement()]),
        "award_criteria": [],
        "document_notes": [],
        "uncertainties": [],
    }
    assert _error_types(payload) == {("requirements", "list_type")}

    data, applied = _normalise(payload)

    assert applied == ["requirements:json_string_list"]
    assert len(ChunkExtractionOut.model_validate(data).requirements) == 2


def test_chunk_8_extraction_id_del_context_repetit_a_l_arrel() -> None:
    payload = {
        "extraction_id": EXTRACTION_ID,
        "requirements": [_requirement()],
        "award_criteria": [],
        "document_notes": [],
        "uncertainties": [],
    }
    assert _error_types(payload) == {("extraction_id", "extra_forbidden")}

    data, applied = _normalise(payload)

    assert applied == ["extraction_id:echoed_root_extraction_id"]
    assert "extraction_id" not in data
    ChunkExtractionOut.model_validate(data)


# --------------------------------------------------------------------------- #
# El que continua sent schema_invalid                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [
        '{"category": "economic_solvency"}',  # JSON, pero un objecte
        '"[]"',  # JSON, pero un text
        "[{'category': 'x'}]",  # no es JSON
        '[{"category": "economic_solvency"',  # JSON tallat
        "",
        "null",
    ],
)
def test_un_text_que_no_es_una_llista_json_no_es_toca(value: str) -> None:
    payload = {"requirements": value}

    data, applied = _normalise(payload)

    assert applied == []
    assert data["requirements"] == value
    assert ("requirements", "list_type") in _error_types(data)


def test_un_extraction_id_d_arrel_que_no_es_del_context_continua_prohibit() -> None:
    payload = {"extraction_id": "00000000-0000-0000-0000-000000000000", "requirements": []}

    data, applied = _normalise(payload)

    assert applied == []
    assert _error_types(data) == {("extraction_id", "extra_forbidden")}


def test_un_extraction_id_d_arrel_que_no_es_text_continua_prohibit() -> None:
    payload = {"extraction_id": [EXTRACTION_ID], "requirements": []}

    data, applied = _normalise(payload)

    assert applied == []
    assert _error_types(data) == {("extraction_id", "extra_forbidden")}


def test_qualsevol_altra_clau_desconeguda_continua_prohibida() -> None:
    payload = {"extraction_id": EXTRACTION_ID, "document_id": "x", "requirements": []}

    data, applied = _normalise(payload)

    assert applied == ["extraction_id:echoed_root_extraction_id"]
    assert _error_types(data) == {("document_id", "extra_forbidden")}


def test_extraction_id_dins_d_un_requisit_no_es_toca() -> None:
    """Fora de l'arrel, `extraction_id` nomes es valid a l'evidencia."""
    requirement = _requirement()
    requirement["extraction_id"] = EXTRACTION_ID
    data, applied = _normalise({"requirements": [requirement]})

    assert applied == []
    assert ("requirements.0.extraction_id", "extra_forbidden") in _error_types(data)


def test_els_elements_de_la_llista_desfeta_es_validen_igual() -> None:
    """Desfer la llista no rebaixa el contracte dels elements."""
    bad = _requirement()
    bad["category"] = "inventada"
    bad["nota"] = "x"
    data, applied = _normalise({"requirements": json.dumps([bad])})

    assert applied == ["requirements:json_string_list"]
    assert _error_types(data) == {
        ("requirements.0.category", "enum"),
        ("requirements.0.nota", "extra_forbidden"),
    }


# --------------------------------------------------------------------------- #
# Llistes niades i altres perfils                                               #
# --------------------------------------------------------------------------- #


def test_una_llista_niada_com_a_text_tambe_es_desfa_amb_el_seu_path() -> None:
    requirement = _requirement()
    requirement["evidence"] = json.dumps(requirement["evidence"])
    requirement["lot_labels"] = '["Lot 1"]'
    data, applied = _normalise({"requirements": json.dumps([requirement])})

    parsed = ChunkExtractionOut.model_validate(data)
    assert applied == [
        "requirements:json_string_list",
        "requirements.0.lot_labels:json_string_list",
        "requirements.0.evidence:json_string_list",
    ]
    assert parsed.requirements[0].lot_labels == ["Lot 1"]
    assert parsed.requirements[0].evidence[0].page_number == 6


def test_tambe_funciona_amb_el_contracte_v2() -> None:
    data, applied = _normalise({"requirements": "[]", "award_criteria": "[]"}, ChunkExtractionOutV2)

    assert applied == ["requirements:json_string_list", "award_criteria:json_string_list"]
    ChunkExtractionOutV2.model_validate(data)


def test_no_modifica_el_payload_original() -> None:
    payload = {"extraction_id": EXTRACTION_ID, "requirements": "[]"}
    snapshot = json.dumps(payload, sort_keys=True)

    _normalise(payload)

    assert json.dumps(payload, sort_keys=True) == snapshot


def test_un_payload_que_no_es_un_objecte_es_torna_tal_qual() -> None:
    assert _normalise("[]") == ("[]", [])  # type: ignore[arg-type]
