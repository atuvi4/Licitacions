"""La diagnosi de `schema_invalid` diu la FORMA del que va arribar, mai el contingut.

Motiu: els 5 chunks `schema_invalid` del run real `0ae5d95e` (Palamos) nomes
van deixar `{path, type}`. La normalitzacio estricta no els va tocar, i sense
saber si la llista arribava com a text no JSON, com a objecte o buida no es pot
escriure cap normalitzacio "del patro observat". Aqui es fixa que, a partir
d'ara, la diagnosi ho diu, i que cap d'aquestes formes es relaxa.

Les formes dels 5 casos:

* chunk 2 (PCAP 11-16): `extra_forbidden` a l'arrel, clau desconeguda;
* chunk 3 (PCAP 16-21): `list_type` a les quatre llistes;
* chunks 8, 12, 15: `list_type` a `requirements`.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.ai.output_normalization import normalise_output
from app.ai.schemas import ChunkExtractionOut, chunk_output_json_schema
from app.services.requirement_analysis import (
    describe_shape,
    describe_unknown_key,
    known_schema_fields,
    schema_diagnostics,
)

EXTRACTION_ID = "84ba6a4b-a92d-4b04-ab73-87bebcc56384"
SECRET = "El volum anual de negocis haura de ser de 155.239,16 euros"
KNOWN = known_schema_fields(chunk_output_json_schema())


def _requirement() -> dict:
    return {
        "category": "economic_solvency",
        "title": SECRET,
        "evidence": [{"extraction_id": EXTRACTION_ID, "page_number": 6, "quote": SECRET}],
    }


def _diagnose(payload):
    data, applied = normalise_output(
        payload, ChunkExtractionOut, context_extraction_ids={EXTRACTION_ID}
    )
    with pytest.raises(ValidationError) as caught:
        ChunkExtractionOut.model_validate(data)
    detail = schema_diagnostics(caught.value, KNOWN)
    assert SECRET not in json.dumps(detail, ensure_ascii=False), "cap text del plec"
    assert "155.239" not in json.dumps(detail)
    return applied, detail


# --------------------------------------------------------------------------- #
# Les formes possibles darrere d'un `list_type` (chunks 3, 8, 12, 15)            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "shape"),
    [
        (None, "null"),
        ({"items": [_requirement()]}, "object(keys:items)"),
        ({SECRET: [_requirement()]}, "object(keys:<key>)"),
        (json.dumps([_requirement()])[:-3], None),  # JSON tallat
        ("[{'category': 'economic_solvency'}]", None),  # cometes de Python
        ('{"requirements": []}', None),  # JSON, pero un objecte
        (SECRET, None),  # text lliure
        (42, "number"),
    ],
)
def test_una_llista_que_no_ho_es_continua_invalida_i_en_diu_la_forma(value, shape):
    applied, detail = _diagnose({"requirements": value})

    assert applied == [], "cap normalitzacio nova: no son patrons observats"
    [item] = [d for d in detail if d["path"] == "requirements"]
    assert item["type"] == "list_type"
    if shape is not None:
        assert item["shape"] == shape
    else:
        assert item["shape"].startswith("string(")


def test_json_tallat_es_distingeix_de_text_lliure():
    truncated = json.dumps([_requirement()])[:-3]
    assert describe_shape(truncated).startswith("string(not_json,starts:[")
    assert describe_shape(SECRET).startswith("string(not_json,starts:text")
    assert describe_shape('{"a": 1}') == "string(json:object(keys:a),chars:8)"
    assert describe_shape("null") == "string(json:null,chars:4)"
    assert describe_shape("") == "string(not_json,starts:empty,chars:0)"
    assert describe_shape("   ") == "string(not_json,starts:empty,chars:3)"


def test_chunk_3_les_quatre_llistes_diuen_cadascuna_la_seva_forma():
    applied, detail = _diagnose(
        {
            "requirements": None,
            "award_criteria": {"items": []},
            "document_notes": "no es JSON",
            "uncertainties": 7,
        }
    )

    assert applied == []
    shapes = {d["path"]: d["shape"] for d in detail}
    assert shapes == {
        "requirements": "null",
        "award_criteria": "object(keys:items)",
        "document_notes": "string(not_json,starts:text,chars:10)",
        "uncertainties": "number",
    }


# --------------------------------------------------------------------------- #
# Chunk 2: una clau desconeguda a l'arrel                                       #
# --------------------------------------------------------------------------- #


def test_chunk_2_una_clau_identificador_es_nomena_i_continua_rebutjada():
    applied, detail = _diagnose({"requirements": [], "document_id": "x"})

    assert applied == []
    assert detail == [{"path": "<key>", "type": "extra_forbidden", "shape": "key:document_id"}]


def test_chunk_2_una_clau_que_es_text_no_es_nomena():
    applied, detail = _diagnose({"requirements": [], SECRET: []})

    assert detail == [
        {"path": "<key>", "type": "extra_forbidden", "shape": f"key:<text,{len(SECRET)} chars>"}
    ]


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("items", "key:items"),
        ("extraction_id", "key:extraction_id"),
        ("Solvencia", "key:<text,9 chars>"),  # majuscula: podria ser text
        ("a b", "key:<text,3 chars>"),
        ("x" * 41, "key:<text,41 chars>"),
    ],
)
def test_nomes_es_nomenen_claus_amb_forma_d_identificador(key, expected):
    assert describe_unknown_key(key) == expected


# --------------------------------------------------------------------------- #
# El que ja es normalitzava continua igual                                      #
# --------------------------------------------------------------------------- #


def test_la_llista_com_a_text_json_segueix_normalitzant_se():
    data, applied = normalise_output(
        {"requirements": json.dumps([])}, ChunkExtractionOut, context_extraction_ids=set()
    )

    assert applied == ["requirements:json_string_list"]
    ChunkExtractionOut.model_validate(data)


# --------------------------------------------------------------------------- #
# Chunk 3 (run a18fa4a6): text JSON que decodifica a un objecte                 #
# --------------------------------------------------------------------------- #


def test_chunk_3_el_text_json_que_es_un_objecte_diu_les_seves_claus():
    """La forma observada: `string(json:object,chars:22)` a `award_criteria`.

    Amb les claus es podra saber si el model embolcalla la llista amb el nom del
    mateix camp. Aqui NO es desfa res: continua sent `list_type`.
    """
    wrapped = json.dumps({"award_criteria": []})
    assert len(wrapped) == 22, "la mateixa llargada que al run real"

    applied, detail = _diagnose(
        {
            "requirements": json.dumps({"requirements": [_requirement()]}),
            "award_criteria": wrapped,
            "document_notes": json.dumps({"notes": [], "extra": 1}),
            "uncertainties": json.dumps({SECRET: []}),
        }
    )

    assert applied == [], "cap desembolcallament: encara no es un patro provat"
    shapes = {d["path"]: d["shape"] for d in detail}
    assert shapes["requirements"].startswith("string(json:object(keys:requirements),chars:")
    assert shapes["award_criteria"] == "string(json:object(keys:award_criteria),chars:22)"
    assert shapes["document_notes"].startswith("string(json:object(keys:extra,notes),chars:")
    assert shapes["uncertainties"].startswith("string(json:object(keys:<key>),chars:")
    assert all(d["type"] == "list_type" for d in detail)


def test_les_claus_insegures_de_l_objecte_decodificat_no_es_nomenen():
    shape = describe_shape(json.dumps({SECRET: 1, "Majuscula": 2, "ok_key": 3}))

    assert shape.startswith("string(json:object(keys:<key>,<key>,ok_key),chars:")
    assert "Majuscula" not in shape
    assert "155" not in shape


def test_mai_no_es_desa_cap_valor_de_l_objecte_decodificat():
    shape = describe_shape(json.dumps({"requirements": [{"title": SECRET}]}))

    assert SECRET not in shape
    assert "title" not in shape, "nomes les claus del primer nivell"


def test_un_objecte_amb_moltes_claus_es_retalla():
    shape = describe_shape(json.dumps({f"k{i}": i for i in range(8)}))

    assert shape.startswith("string(json:object(keys:k0,k1,k2,k3,k4,...),chars:")
