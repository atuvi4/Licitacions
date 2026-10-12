"""product-v2.1: robustesa del contracte despres del post-mortem de l'Attempt 3.

L'Attempt 3 (Haiku 4.5, 2026-10-12) va perdre el chunk 1 sencer perque UN requisit (la p7,
correcte i amb cita literal) portava `category = "submission_documentation"`: un AMBIT
d'incertesa que el prompt de product-v2 enumera, no una categoria de requisit. Que es protegeix:

* el requisit invalid es rebutja SOL, amb un diagnostic de codis que diu el camp, el
  vocabulari barrejat, l'obligacio i les pagines declarades; la resta del chunk es conserva;
* res no es coacciona: un valor fora del vocabulari continua sent un rebuig;
* un sobre inservible continua invalidant el chunk sencer;
* les posicions reportades son les originals de la resposta;
* V1 continua sent estricte i el seu prompt no canvia;
* el prompt v2.1 enumera les categories (de l'enum) i les separa dels ambits;
* una revisio anterior de product-v2 continua sent experimental.

Tot offline, amb el proveidor fals i el fixture sintetic (Vilasintetica). Cap dada real.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from app.ai.profiles import PRODUCT_V2, V1, V2
from app.ai.prompts.requirements_v1 import SYSTEM_PROMPT as V1_PROMPT
from app.ai.schemas import ChunkExtractionOut, chunk_output_json_schema
from app.ai.schemas_product_v2 import ChunkExtractionOutProductV2
from app.models.enums import AnalysisRunStatus, RequirementCategory, UncertaintyScope
from app.models.requirements import Requirement, RequirementExtractionRun
from app.services.requirement_analysis import experimental_profile_of, run_is_superseded
from tests import test_requirement_uncertainties as uncertainty_tests
from tests.test_requirement_uncertainties import _quote, _req, _rows, _run, ideal_responses

# Fixtures compartides amb els tests de les incerteses.
canary = uncertainty_tests.canary
_clean_provider = uncertainty_tests._clean_provider

pytestmark = pytest.mark.asyncio

FIXTURE = Path(__file__).parent / "fixtures" / "canary_attempt3_chunk1.json"


def attempt3_chunk1(canary) -> dict:
    """La sortida REAL de Haiku al chunk 1 (p6-7) de l'Attempt 3, sanititzada: nomes s'hi ha
    substituit l'`extraction_id` per la del fixture del test (text sintetic, cap dada real)."""
    raw = FIXTURE.read_text(encoding="utf-8").replace("__EXTRACTION_ID__", canary["extraction_id"])
    return json.loads(raw)["payload"]


def _titles(payload: dict) -> list[str]:
    return [item["title"] for item in payload["requirements"]]


async def test_la_sortida_real_de_l_attempt_3_amb_el_contracte_antic_perd_el_chunk(canary):
    """Amb la validacio de 69fcea0 (el model sencer, estricte) el chunk sencer es invalid."""
    payload = attempt3_chunk1(canary)
    with pytest.raises(PydanticValidationError) as caught:
        ChunkExtractionOutProductV2.model_validate(payload)
    errors = caught.value.errors()
    assert [(e["loc"], e["type"], e["input"]) for e in errors] == [
        (("requirements", 1, "category"), "enum", "submission_documentation")
    ]


async def test_la_sortida_real_de_l_attempt_3_conserva_el_chunk_i_rebutja_un_item(db, canary):
    payload = attempt3_chunk1(canary)
    penalty, submission, deadline = _titles(payload)
    responses = ideal_responses(canary)
    responses[1] = payload
    run = await _run(db, canary, responses)

    assert run.status == AnalysisRunStatus.COMPLETED
    chunk1 = run.chunk_coverage["chunks"][1]
    assert chunk1["status"] == "completed" and chunk1["error"] is None  # el chunk no desapareix
    stored = await _rows(db, Requirement, run.id)
    # La penalitat (p6) i el termini (p7) es conserven; el sobre digital (p7), no.
    assert {r.title for r in stored if r.source_chunk_index == 1} == {penalty, deadline}
    assert submission not in {r.title for r in stored}
    assert {
        "item": "requirement",
        "index": 1,
        "stage": "schema",
        "reason": "schema_invalid",
        "detail": "enum",
        "field": "category",
        "received_type": "str",
        "received_value": "submission_documentation",
        "value_vocabulary": "uncertainty_scope",
        "obligation": "mandatory",
        "cited_pages": [7],
    } in chunk1["rejections"]
    # Compta com a rebutjat (el chunk 0 ideal no en rebutja cap).
    assert run.requirements_rejected == 1
    # Mai es coacciona: cap requisit `other`, cap alias ni cap categoria inventada.
    assert not [r for r in stored if r.category == RequirementCategory.OTHER]
    assert "submission_documentation" not in {c.value for c in RequirementCategory}


async def test_un_criteri_mal_format_es_descarta_sol(db, canary):
    responses = ideal_responses(canary)
    criteria = responses[0]["award_criteria"]
    criteria.insert(0, {**criteria[0], "name": "Preu dolent", "points": "seixanta"})
    run = await _run(db, canary, responses)
    assert run.status == AnalysisRunStatus.COMPLETED and run.award_criteria_extracted == 1
    rejections = run.chunk_coverage["chunks"][0]["rejections"]
    assert any(
        r["item"] == "award_criterion"
        and r["index"] == 0
        and r["stage"] == "schema"
        and r["field"] == "points"
        for r in rejections
    )


async def test_un_sobre_inservible_continua_invalidant_el_chunk(db, canary):
    responses = ideal_responses(canary)
    responses[1] = {"requirements": "no es una llista"}
    run = await _run(db, canary, responses)
    assert run.chunk_coverage["chunks"][1]["error"] == "schema_invalid"
    responses = ideal_responses(canary)
    responses[1] = {"requirements": [], "inventat": 1}
    run = await _run(db, canary, responses, force=True)
    assert run.chunk_coverage["chunks"][1]["error"] == "schema_invalid"


async def test_les_posicions_reportades_son_les_originals(db, canary):
    responses = ideal_responses(canary)
    bad = {**responses[0]["requirements"][0], "category": "solvency"}  # ambit, no categoria
    altered = _req(
        canary,
        3,
        "",
        title="Cita alterada",
        quote=_quote(canary, 3, "4 hores des de").replace("màxim", "maxim"),
    )
    responses[0]["requirements"] = [bad, altered]
    run = await _run(db, canary, responses)
    rejections = run.chunk_coverage["chunks"][0]["rejections"]
    assert {
        (r["item"], r["index"], r["stage"]) for r in rejections if r["item"] == "requirement"
    } >= {("requirement", 0, "schema"), ("requirement", 1, "evidence"), ("requirement", 1, "item")}


async def test_v1_continua_estricte_i_perd_el_chunk_com_abans(db, canary):
    from tests.test_requirement_uncertainties import v1_responses

    responses = v1_responses(canary)
    responses[1] = {**attempt3_chunk1(canary), "uncertainties": []}
    run = await _run(db, canary, responses, profile=V1)
    assert run.chunk_coverage["chunks"][1]["error"] == "schema_invalid"


async def test_un_valor_no_hashable_al_rebuig_no_fa_caure_el_run(db, canary):
    """Revisio B1: `obligation`/`category` com a llista o dict al requisit rebutjat."""
    responses = ideal_responses(canary)
    first = responses[0]["requirements"][0]
    responses[0]["requirements"] = [
        {**first, "obligation": ["mandatory"]},
        {**first, "category": {"x": 1}, "obligation": {"y": 2}},
        *responses[0]["requirements"][1:],
    ]
    run = await _run(db, canary, responses)
    assert run.status == AnalysisRunStatus.COMPLETED
    rejected = [r for r in run.chunk_coverage["chunks"][0]["rejections"] if r["stage"] == "schema"]
    assert [(r["index"], r.get("obligation"), r.get("category")) for r in rejected] == [
        (0, None, "certification"),
        (1, None, None),
    ]
    assert run.requirements_extracted == 4 and run.requirements_rejected == 2  # 3 + la p7


@pytest.mark.parametrize("key", ["requirements", "award_criteria"])
async def test_una_llista_null_explicita_continua_invalidant_el_chunk(db, canary, key):
    """Revisio S1: nomes una clau ABSENT val per buida; `null` es un sobre invalid (com V1)."""
    responses = ideal_responses(canary)
    responses[1] = {**responses[1], key: None}
    run = await _run(db, canary, responses)
    assert run.chunk_coverage["chunks"][1]["error"] == "schema_invalid"
    responses = ideal_responses(canary)
    responses[1] = {k: v for k, v in responses[1].items() if k != key}
    run = await _run(db, canary, responses, force=True)
    assert run.chunk_coverage["chunks"][1]["status"] == "completed"


async def test_el_vocabulari_nomes_es_marca_quan_l_error_es_al_camp(db, canary):
    """Revisio N1: un element que es una cadena o un error niat no porten `value_vocabulary`."""
    responses = ideal_responses(canary)
    first = responses[0]["requirements"][0]
    nested = {**first, "evidence": [{**first["evidence"][0], "evidence_role": "solvency"}]}
    responses[0]["requirements"] = [
        "solvency",
        nested,
        {**first, "category": "cost"},
        {**first, "evidence": "solvency"},  # ambit en un camp sense vocabulari
    ]
    run = await _run(db, canary, responses)
    rejected = [r for r in run.chunk_coverage["chunks"][0]["rejections"] if r["stage"] == "schema"]
    assert [(r["index"], r.get("field"), r.get("value_vocabulary")) for r in rejected] == [
        (0, None, None),
        (1, "evidence", None),
        (2, "category", "uncertainty_scope"),
        (3, "evidence", None),
    ]


async def test_el_prompt_v2_1_separa_categories_i_ambits():
    prompt = PRODUCT_V2.system_prompt
    block = prompt[prompt.index("VOCABULARIS TANCATS") : prompt.index("MATERIAL NO CONFIABLE")]
    for category in RequirementCategory:
        assert f"`{category.value}`" in block
    # Els ambits que no son categories, explicits; `deadline` (que es a tots dos) no hi es.
    scope_only_line = next(line for line in block.splitlines() if "NOMES ambits" in line)
    for scope in UncertaintyScope:
        is_category = scope.value in {c.value for c in RequirementCategory}
        assert (f"`{scope.value}`" in scope_only_line) is not is_category, scope
    assert "`deadline` (valid com a categoria i com a ambit)" in block
    assert "`administrative_documentation`" in block
    for scope in UncertaintyScope:  # els ambits continuen enumerats al seu bloc
        assert f"`{scope.value}`" in prompt
    assert "VOCABULARIS TANCATS" not in V1_PROMPT and "VOCABULARIS TANCATS" not in V1.system_prompt
    assert PRODUCT_V2.prompt_version == "requirements-product-v2.1"
    assert PRODUCT_V2.schema_version == "requirements-schema-product-v2.1"
    assert PRODUCT_V2.extractor_version == "tender-requirements-product-v2"


def _row(tender_id, versions, status, started_at) -> RequirementExtractionRun:
    extractor, prompt, schema = versions
    return RequirementExtractionRun(
        tender_id=tender_id, status=status, input_fingerprint="f" * 64,
        extractor_version=extractor, prompt_version=prompt, schema_version=schema,
        provider="anthropic", model="m", started_at=started_at,
        needs_review=status == AnalysisRunStatus.COMPLETED_EMPTY,
    )  # fmt: skip


async def test_una_revisio_anterior_de_product_v2_continua_experimental(session, tender_document):
    old = (
        "tender-requirements-product-v2",
        "requirements-product-v2",
        "requirements-schema-product-v2",
    )
    v1 = (V1.extractor_version, V1.prompt_version, V1.schema_version)
    now = datetime.now(UTC)
    partial = _row(tender_document.tender_id, v1, AnalysisRunStatus.PARTIAL, now - timedelta(1))
    session.add(partial)
    await session.flush()
    newer_old_revision = _row(tender_document.tender_id, old, AnalysisRunStatus.COMPLETED, now)
    session.add(newer_old_revision)
    await session.flush()
    assert experimental_profile_of(newer_old_revision) is PRODUCT_V2
    # Una run product-v2 (de qualsevol revisio) no supera el parcial V1.
    assert await run_is_superseded(session, partial) is False


# --------------------------------------------------------------------------- #
# V1: regressio zero                                                            #
# --------------------------------------------------------------------------- #

#: Empremtes de V1 a b3d7a02 i a 69fcea0 (identiques): el prompt i l'esquema no canvien.
V1_PROMPT_SHA256 = "ac46d695c79469fb08137a2e73f4b7c07b1b41271f57bf7ad1328f292db6a243"
V1_SCHEMA_SHA256 = "073622e6d31e7eb1e207ef0f4b3ab87c2d36ad051293c4093cdaab5bbeb35e74"


async def test_v1_no_comparteix_la_validacio_element_a_element():
    assert hashlib.sha256(V1_PROMPT.encode()).hexdigest() == V1_PROMPT_SHA256
    assert V1.system_prompt == V1_PROMPT
    schema = json.dumps(chunk_output_json_schema(), sort_keys=True).encode()
    assert hashlib.sha256(schema).hexdigest() == V1_SCHEMA_SHA256
    assert (V1.extractor_version, V1.prompt_version, V1.schema_version) == (
        "tender-requirements-v1",
        "requirements-v1",
        "requirements-schema-v1",
    )
    # V1 i v2 validen el chunk sencer (cap `parser` propi); nomes product-v2 en te.
    assert V1.parser is None and V2.parser is None and PRODUCT_V2.parser is not None
    parsed = V1.parse({"requirements": []})
    assert type(parsed) is ChunkExtractionOut
    for attribute in ("schema_rejections", "requirement_indexes", "award_indexes"):
        assert not hasattr(parsed, attribute), attribute


async def test_v1_conserva_la_semantica_de_chunks_i_rebutjos(db, canary):
    """Un V1 amb una cita alterada: mateixos indexs, mateix recompte, cap rebuig d'esquema."""
    from tests.test_requirement_uncertainties import v1_responses

    responses = v1_responses(canary)
    reqs = responses[0]["requirements"]
    reqs[1]["evidence"][0]["quote"] = reqs[1]["evidence"][0]["quote"].replace(
        "definitiva", "definitva"
    )
    run = await _run(db, canary, responses, profile=V1)
    assert run.status == AnalysisRunStatus.COMPLETED
    rejections = run.chunk_coverage["chunks"][0]["rejections"]
    assert {(r["item"], r["index"], r["stage"]) for r in rejections} == {
        ("requirement", 1, "evidence"),
        ("requirement", 1, "item"),
    }
    assert not [r for r in rejections if r["stage"] == "schema"]
    assert run.requirements_rejected == 1 and run.requirements_extracted == 4


async def test_el_tipus_rebut_nomes_es_diu_quan_se_sap_de_quin_camp_es(db, canary):
    """Revisio final S1/N1: absent si falta, res si l'error es niat o una clau desconeguda,
    el tipus de l'element si no es un objecte; cap pagina fora de rang al diagnostic."""
    responses = ideal_responses(canary)
    first = responses[0]["requirements"][0]
    missing = {k: v for k, v in first.items() if k != "category"}
    nested = {**first, "evidence": [{**first["evidence"][0], "quote": 5}]}
    extra = {**first, "inventat": [1, 2]}
    responses[0]["requirements"] = [missing, nested, extra, "text", first]
    unc = responses[0]["uncertainties"][0]
    responses[0]["uncertainties"] = [{**unc, "page_number": 10**30}, unc]
    run = await _run(db, canary, responses)
    rejections = run.chunk_coverage["chunks"][0]["rejections"]
    schema = {(r["item"], r["index"]): r for r in rejections if r["stage"] == "schema"}
    assert schema[("requirement", 0)]["received_type"] == "absent"
    assert "received_value" not in schema[("requirement", 0)]
    assert schema[("requirement", 1)]["field"] == "evidence"
    assert "received_type" not in schema[("requirement", 1)]
    assert (
        "field" not in schema[("requirement", 2)]
        and "received_type" not in schema[("requirement", 2)]
    )
    assert schema[("requirement", 3)]["received_type"] == "str"
    page = schema[("uncertainty", 0)]
    assert page["field"] == "page_number" and page["received_type"] == "int"
    assert "cited_pages" not in page and str(10**30) not in json.dumps(rejections)
