"""Les peces PURES del motor (0051): fingerprint, inputs hash, comparador
territorial, registre de regles i cubells de punts. Cap base de dades."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.analysis import engine, points, registry, territory
from app.analysis.fingerprint import (
    BlockView,
    CriterionView,
    FactView,
    NeedView,
    RequirementView,
    ScopeItemView,
    SiteView,
    TenderStructureView,
    TerritoryView,
    canonical_json,
    inputs_hash,
    jsonable,
    review_revision_hash,
    structure_fingerprint,
)

API_ROOT = Path(__file__).resolve().parents[1]


def _view() -> TenderStructureView:
    t1 = TerritoryView(kind="municipality", label="L'Escala", qualifier="trama urbana")
    t2 = TerritoryView(kind="custom", label="Can Maset")
    need = NeedView(
        id="n1", capability_code="pbx.concurrent_calls", unmapped_capability=None,
        comparator="gte", value_json={"channels": 20}, value_schema="v1",
        need_type="minimum", points=None, criterion_key=None,
    )  # fmt: skip
    r1 = RequirementView(
        id="r1", key="k-r1", title="R1", category="technical_requirement", obligation="mandatory",
        timing="at_execution", block_codes=("B1",), territory_completeness="complete",
        territories=(t1, t2), proof_json=[], proof_schema="proof_v1", needs=(need,),
        criterion_keys=("k-c1",),
    )  # fmt: skip
    c1 = CriterionView(
        id="c1", key="k-c1", name="C1", points=5, criterion_type="improvement",
        evaluation_type="automatic", timing="at_submission", block_codes=("B1",),
    )  # fmt: skip
    return TenderStructureView(
        tender_id="t",
        duration_months=48,
        extension_months=0,
        regulated_harmonised=True,
        planned_start_date=date(2027, 1, 1),
        blocks=(BlockView("B1", "Connectivitat", Decimal("75600.00"), 8, False),),
        sites=(SiteView(code="D02", name=None, role="remote", is_critical=None),),
        scope_items=(
            ScopeItemView(
                id="s1", service_line_code="access_ftth", block_code="B1", quantity=Decimal("15")
            ),
        ),
        requirements=(r1,),
        criteria=(c1,),
        facts=(FactView("penalties", {"items": []}, "v1", ("B1",)),),
    )


def test_same_semantics_same_hash_whatever_the_order_or_the_duplicates():
    view = _view()
    base = structure_fingerprint(view)
    r1 = view.requirements[0]
    shuffled = replace(
        view,
        requirements=(
            replace(
                r1,
                territories=tuple(reversed(r1.territories)) + (r1.territories[0],),
                block_codes=("B1", "B1"),
            ),
        ),
        blocks=tuple(reversed(view.blocks + view.blocks)),
    )
    assert structure_fingerprint(shuffled) == base
    # Espais i Decimal amb una altra escala: mateixa semantica.
    spaced = replace(
        view,
        requirements=(
            replace(
                r1, territories=(replace(r1.territories[0], label="  L'Escala "), r1.territories[1])
            ),
        ),
    )
    assert structure_fingerprint(spaced) == base
    rescaled = replace(
        view, blocks=(replace(view.blocks[0], base_price_ex_vat=Decimal("75600.0000")),)
    )
    assert structure_fingerprint(rescaled) == base
    # Les cites no hi son: la vista no en porta cap, i `source` no entra al hash.
    assert structure_fingerprint(replace(view, source={"citations": 37})) == base


def test_a_material_change_changes_the_hash():
    view = _view()
    base = structure_fingerprint(view)
    r1 = view.requirements[0]
    assert structure_fingerprint(replace(view, duration_months=36)) != base
    assert (
        structure_fingerprint(replace(view, sites=(replace(view.sites[0], is_critical=False),)))
        != base
    )
    assert (
        structure_fingerprint(replace(view, sites=(replace(view.sites[0], is_critical=True),)))
        != base
    )
    assert (
        structure_fingerprint(
            replace(view, requirements=(replace(r1, timing="before_formalization"),))
        )
        != base
    )
    assert (
        structure_fingerprint(
            replace(view, requirements=(replace(r1, territory_completeness="unknown"),))
        )
        != base
    )
    assert (
        structure_fingerprint(
            replace(view, requirements=(replace(r1, territories=r1.territories[:1]),))
        )
        != base
    )
    assert (
        structure_fingerprint(
            replace(view, criteria=(replace(view.criteria[0], timing="at_execution"),))
        )
        != base
    )
    assert structure_fingerprint(replace(view, facts=())) != base


def test_inputs_hash_depends_on_exactly_the_eight_inputs():
    # 3B-2 §3.2: `classification_run_id` (passada de l'empresa) ha deixat de ser
    # input; en el seu lloc, la passada global i el hash dels overrides de rol.
    base = dict(
        tender_revision_id=None, extraction_run_id="e", global_classification_run_id=None,
        role_overrides_hash=None, review_revision_hash=None,
        tender_structure_fingerprint="s" * 64, knowledge_fingerprint="k" * 64,
        rules_version=registry.RULES_VERSION,
    )  # fmt: skip
    digest = inputs_hash(**base)
    assert digest == inputs_hash(**base)
    for key, value in (
        ("tender_revision_id", "r"),
        ("extraction_run_id", None),
        ("global_classification_run_id", "g"),
        ("role_overrides_hash", "o" * 64),
        ("review_revision_hash", "x" * 64),
        ("tender_structure_fingerprint", "t" * 64),
        ("knowledge_fingerprint", "j" * 64),
        ("rules_version", "phase3a-analysis-v2"),
    ):
        assert inputs_hash(**{**base, key: value}) != digest, key
    assert review_revision_hash([]) is None
    items = [
        {"kind": "requirement", "key": "a", "review_status": "verified"},
        {"kind": "criterion", "key": "b", "review_status": "unreviewed"},
    ]
    assert review_revision_hash(items) == review_revision_hash(list(reversed(items)))
    assert review_revision_hash(items) != review_revision_hash(items[:1])


# --------------------------------------------------------------------------- #
# Comparador territorial                                                        #
# --------------------------------------------------------------------------- #

ESCALA = territory.Area("municipality", "L'Escala")
ESCALA_INE = territory.Area("municipality", "L'Escala", "17062", "INE")
COMPLETE_ELSEWHERE = territory.Coverage(
    "complete",
    (
        territory.Area("municipality", "Banyoles", "17015", "INE"),
        territory.Area("municipality", "Mont-ras", "17110", "INE"),
    ),
)


def _cmp(territories, completeness="complete", mode="any", coverage=COMPLETE_ELSEWHERE):
    return territory.compare_territories(
        territories=territories, completeness=completeness, match_mode=mode, coverage=coverage
    )


def test_territory_identity_only_no_containment_no_geocoding():
    assert _cmp([ESCALA]).verdict == "unmatched", "cobertura completa sense l'Escala"
    covered = territory.Coverage("complete", (ESCALA_INE,))
    assert _cmp([ESCALA_INE], coverage=covered).verdict == "matched"
    assert (
        _cmp([territory.Area("municipality", "l'escala")], coverage=covered).verdict == "matched"
    ), "mateix nom, sense codi"
    assert (
        _cmp([territory.Area("municipality", "Escala", "17062", "INE")], coverage=covered).verdict
        == "matched"
    ), "mateix codi explicit"
    # Cap contencio: un municipi no es dins d'una provincia, ni una adreca dins d'un municipi.
    girona = territory.Coverage("complete", (territory.Area("province", "Girona", "17", "INE"),))
    assert _cmp([ESCALA_INE], coverage=girona).verdict == "unmatched"
    address = territory.Area("address", "Carrer Major 1", site_key=None)
    assert _cmp([address], coverage=covered).verdict == "unmatched"
    # Un codi que contradiu el nom no casa.
    other_code = territory.Coverage(
        "complete", (territory.Area("municipality", "L'Escala", "99999", "INE"),)
    )
    assert _cmp([ESCALA_INE], coverage=other_code).verdict == "unmatched"
    # Un matis que no es pot comprovar deixa l'area en `unknown`, encara que el municipi casi.
    urban = territory.Area("municipality", "L'Escala", qualifier="trama urbana")
    assert _cmp([urban], coverage=covered).verdict == "unknown"
    # Una area ancorada a una seu no te res a comparar.
    anchored = territory.Area("address", site_key="D02")
    assert _cmp([anchored], coverage=covered).verdict == "unknown"


def test_territory_completeness_rules_never_match_by_vacuous_truth():
    assert _cmp([], completeness="not_applicable").verdict == "not_applicable"
    assert _cmp([], completeness="unknown").verdict == "unknown"
    assert _cmp([], completeness="unknown").reason == "list_unavailable"
    covered = territory.Coverage("complete", (ESCALA_INE,))
    assert (
        _cmp([ESCALA_INE], completeness="partial", mode="all", coverage=covered).verdict
        == "unknown"
    )
    assert (
        _cmp([ESCALA_INE], completeness="partial", mode="any", coverage=covered).verdict
        == "matched"
    )
    # Cobertura parcial o desconeguda: l'absencia no es `unmatched`.
    partial = territory.Coverage("partial", COMPLETE_ELSEWHERE.areas)
    assert _cmp([ESCALA], coverage=partial).verdict == "unknown"
    assert _cmp([ESCALA], coverage=None).verdict == "unknown"
    assert _cmp([ESCALA], coverage=territory.Coverage("unknown", ())).verdict == "unknown"


def test_territory_aggregation_all_and_any():
    covered = territory.Coverage("complete", (ESCALA_INE,))
    canmaset = territory.Area("custom", "Can Maset")
    both = [ESCALA_INE, canmaset]
    assert _cmp(both, mode="all", coverage=covered).verdict == "unmatched"
    assert _cmp(both, mode="any", coverage=covered).verdict == "matched"
    assert _cmp([ESCALA_INE], mode="all", coverage=covered).verdict == "matched"
    partial_cov = territory.Coverage("partial", (ESCALA_INE,))
    assert _cmp([ESCALA_INE, canmaset], mode="all", coverage=partial_cov).verdict == "unknown"
    assert _cmp([canmaset], mode="any", coverage=partial_cov).verdict == "unknown"
    result = _cmp(both, mode="any", coverage=covered)
    assert [v.verdict for v in result.area_verdicts] == ["matched", "unmatched"]
    assert result.area_verdicts[0].matched == ESCALA_INE.as_dict()


# --------------------------------------------------------------------------- #
# Registre                                                                      #
# --------------------------------------------------------------------------- #


def test_the_registry_is_closed_and_nothing_is_orphan():
    # 3B-2: v4 (forats del paquet i divergencia de rols); el copy es el de la v3.
    assert registry.RULES_VERSION == "phase3b-analysis-v4"
    source = "\n".join(
        (API_ROOT / "app" / "analysis" / name).read_text(encoding="utf-8")
        for name in ("engine.py", "territory.py", "points.py")
    )
    used_rules = set(re.findall(r"\bR\.([A-Z_]+)\b", source))
    used_explanations = set(re.findall(r"\bX\.([A-Z_]+)\b", source))
    used_questions = set(re.findall(r"\bQ\.([A-Z_]+)\b", source))
    rule_names = {k for k in vars(registry.RuleCode) if k.isupper()}
    explanation_names = {k for k in vars(registry.ExplanationCode) if k.isupper()}
    question_names = {k for k in vars(registry.QuestionCode) if k.isupper()}
    assert used_rules <= rule_names and used_explanations <= explanation_names
    assert used_questions <= question_names
    assert rule_names - used_rules == set(), "regla registrada que el motor no fa servir"
    assert explanation_names - used_explanations == set(), "explicacio orfe"
    assert question_names - used_questions == set(), "pregunta orfe"
    assert len(registry.RULE_CODES) == len(rule_names)
    assert all(code.endswith("_v1") for code in registry.RULE_CODES), "regles versionades"
    current = registry.RULES_VERSION
    for code in registry.EXPLANATION_CODES:
        assert registry.explanation_text(code, current) != registry.UNKNOWN_EXPLANATION_TEXT
    assert "{service_line}" not in registry.question_text(
        registry.QuestionCode.COMPANY_DECLARE_CAPABILITY,
        {"capability": "pbx.hot_desking", "service_line": "voice_pbx"},
        current,
    )
    # v3: un parametre absent surt com l'expressio neutra, mai com a `{title}`; la v2
    # conserva la interpolacio crua.
    assert "{" not in registry.question_text(
        registry.QuestionCode.COMPANY_CONFIRM_FACT, None, current
    )
    assert "{title}" in registry.question_text(
        registry.QuestionCode.COMPANY_CONFIRM_FACT, None, registry.RULES_VERSION_V2
    )


# --------------------------------------------------------------------------- #
# Copy versionat per `rules_version` (spec 3.11)                                #
# --------------------------------------------------------------------------- #

V2 = "phase3a-analysis-v2"
V3 = "phase3a-analysis-v3"
#: 3B-2 (forats del paquet i divergencia de rols): la versio vigent, amb el copy de la v3.
V4 = "phase3b-analysis-v4"

#: sha256 de les taules v2 (explicacions i plantilles de pregunta) tal com eren a
#: 7513e3a (`EXPLANATIONS` i `QUESTIONS` del registre), serialitzades per `_copy_digest`.
#: Fixat abans de cap canvi de text: si falla, algu ha tocat el copy congelat de la v2.
V2_COPY_SHA256 = "613a3408fb6debf80ea67c8a4ddf0016dfd74a1f6cdc05f29c73c165e38b4592"
#: El mateix per a la v3, que deixa de ser la vigent a 3B-2 (v4): les taules v3 tal com
#: eren a 507ac57 (HEAD de la branca), serialitzades per `_copy_digest`. Si falla, algu ha
#: tocat el copy congelat de la v3 (els codis nous van a la taula v4, no a la v3).
V3_COPY_SHA256 = "a28f33261716b8fc31712955be73bada07e5e7edbeb9680ca9a62bba74406233"


def _copy_digest(explanations, questions) -> str:
    """Serialitzacio canonica: JSON UTF-8, claus ordenades, sense espais."""
    payload = {"explanations": dict(explanations), "questions": dict(questions)}
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def test_the_v2_copy_is_frozen_exactly_as_it_was_at_7513e3a():
    assert (
        _copy_digest(registry.EXPLANATIONS_BY_VERSION[V2], registry.QUESTIONS_BY_VERSION[V2])
        == V2_COPY_SHA256
    )


def test_the_v3_copy_is_frozen_exactly_as_it_was_at_507ac57():
    assert (
        _copy_digest(registry.EXPLANATIONS_BY_VERSION[V3], registry.QUESTIONS_BY_VERSION[V3])
        == V3_COPY_SHA256
    )


def test_every_version_has_both_tables_and_the_current_one_covers_every_code():
    assert registry.RULES_VERSION == V4
    assert set(registry.EXPLANATIONS_BY_VERSION) == set(registry.QUESTIONS_BY_VERSION)
    assert {V2, V3, V4} <= set(registry.EXPLANATIONS_BY_VERSION)
    # 3B-2: la v4 canvia regles (forats del paquet, divergencia de rols), no el copy dels
    # codis que ja hi eren; nomes hi afegeix els dos codis nous (Task 9).
    codes = registry.ExplanationCode
    v4_only = {codes.PACKAGE_GAP, codes.ROLE_OVERRIDE_DIVERGES}
    v4 = dict(registry.EXPLANATIONS_BY_VERSION[V4])
    assert set(v4) - set(registry.EXPLANATIONS_BY_VERSION[V3]) == v4_only
    assert {k: v for k, v in v4.items() if k not in v4_only} == dict(
        registry.EXPLANATIONS_BY_VERSION[V3]
    )
    assert dict(registry.QUESTIONS_BY_VERSION[V4]) == dict(registry.QUESTIONS_BY_VERSION[V3])
    current = registry.RULES_VERSION
    assert set(registry.EXPLANATIONS_BY_VERSION[current]) == registry.EXPLANATION_CODES
    assert set(registry.QUESTIONS_BY_VERSION[current]) == registry.QUESTION_CODES
    # Les versions antigues son subconjunts: un codi no canvia mai de significat.
    for version in registry.EXPLANATIONS_BY_VERSION:
        assert set(registry.EXPLANATIONS_BY_VERSION[version]) <= registry.EXPLANATION_CODES
        assert set(registry.QUESTIONS_BY_VERSION[version]) <= registry.QUESTION_CODES
    # La v2 no es pot modificar en calent.
    with pytest.raises(TypeError):
        registry.EXPLANATIONS_BY_VERSION[V2]["FACT_UNKNOWN"] = "x"  # type: ignore[index]
    with pytest.raises(TypeError):
        registry.QUESTIONS_BY_VERSION[V2]["company.confirm_fact"] = "x"  # type: ignore[index]


def test_the_text_comes_from_the_runs_version_table(monkeypatch):
    code = registry.ExplanationCode.FACT_UNKNOWN
    question = registry.QuestionCode.COMPANY_CONFIRM_FACT
    params = {"fact": "support_24x7", "title": "Suport 24x7"}
    v2_explanation = registry.EXPLANATIONS_BY_VERSION[V2][code]
    v2_question = registry.QUESTIONS_BY_VERSION[V2][question].format(**params)
    # Una v3 que ja digui una altra cosa: cada run es llegeix amb la seva taula.
    monkeypatch.setitem(
        registry.EXPLANATIONS_BY_VERSION, V3, {**registry.EXPLANATIONS_BY_VERSION[V3], code: "v3"}
    )
    monkeypatch.setitem(
        registry.QUESTIONS_BY_VERSION,
        V3,
        {**registry.QUESTIONS_BY_VERSION[V3], question: "v3 {title}"},
    )
    assert registry.explanation_text(code, V2) == v2_explanation
    assert registry.explanation_text(code, V3) == "v3"
    assert registry.question_text(question, params, V2) == v2_question
    # La v3 resol els parametres: el text lliure surt entre «».
    assert registry.question_text(question, params, V3) == "v3 «Suport 24x7»"
    # La v2 conserva la interpolacio crua: sense parametres, la plantilla tal com es.
    assert registry.question_text(question, None, V2) == registry.QUESTIONS_BY_VERSION[V2][question]


@pytest.mark.parametrize("version", ["phase3a-analysis-v1", "phase3a-analysis-v9", "", None])
def test_an_unknown_version_gets_neutral_text_never_another_table(version):
    assert registry.UNKNOWN_EXPLANATION_TEXT == "Explicació no disponible."
    assert registry.UNKNOWN_QUESTION_TEXT == "Pregunta no disponible."
    for code in registry.EXPLANATION_CODES:
        assert registry.explanation_text(code, version) == "Explicació no disponible."
    for code in registry.QUESTION_CODES:
        assert registry.question_text(code, {"title": "T"}, version) == "Pregunta no disponible."


def test_a_code_missing_from_the_runs_table_gets_neutral_text(monkeypatch):
    """Un codi nou nomes a la v3 no surt mai amb text d'una altra taula en un run v2."""
    v2_explanations = dict(registry.EXPLANATIONS_BY_VERSION[V2])
    v2_questions = dict(registry.QUESTIONS_BY_VERSION[V2])
    del v2_explanations[registry.ExplanationCode.FACT_UNKNOWN]
    del v2_questions[registry.QuestionCode.COMPANY_CONFIRM_FACT]
    monkeypatch.setitem(registry.EXPLANATIONS_BY_VERSION, V2, v2_explanations)
    monkeypatch.setitem(registry.QUESTIONS_BY_VERSION, V2, v2_questions)
    assert (
        registry.explanation_text(registry.ExplanationCode.FACT_UNKNOWN, V2)
        == "Explicació no disponible."
    )
    assert (
        registry.question_text(registry.QuestionCode.COMPANY_CONFIRM_FACT, {"title": "T"}, V2)
        == "Pregunta no disponible."
    )
    assert registry.explanation_text("SOME_NEW_CODE", V3) == "Explicació no disponible."
    assert registry.question_text("company.some_new_code", None, V3) == "Pregunta no disponible."


# --------------------------------------------------------------------------- #
# Cubells de punts                                                              #
# --------------------------------------------------------------------------- #


def test_points_buckets_always_sum_to_max_and_tranches_split_increments():
    assert points.points_status(points.all_in("secured", 5), 5) == "secured"
    assert points.points_status(points.all_in("lost", 5), 5) == "lost"
    assert points.points_status(points.all_in("not_assessable", 7), 7) == "not_assessable"
    assert points.points_status(points.Buckets(), 0) == "not_assessable"
    tranches = [{"min": 30, "points": 0.67}, {"min": 40, "points": 1.33}, {"min": 50, "points": 2}]
    increments = points.tranche_increments(2, tranches)
    assert [round(i, 2) for _, i in increments] == [0.67, 0.66, 0.67]
    assert [t for t, _ in increments] == [30, 40, 50]
    buckets = points.allocate_tranches(2, tranches, lambda t: "at_risk" if t <= 40 else "lost")
    assert (round(buckets.at_risk, 2), round(buckets.lost, 2)) == (1.33, 0.67)
    assert abs(buckets.total - 2) <= points.TOLERANCE
    assert points.points_status(buckets, 2) == "mixed"
    unknown = points.allocate_tranches(2, tranches, lambda t: "not_assessable")
    assert points.points_status(unknown, 2) == "not_assessable"
    with pytest.raises(AssertionError):
        points.points_status(points.Buckets(secured=1), 2)


def test_the_engine_reads_the_required_value_from_known_keys():
    assert engine._single_number({"minutes": 30, "site_class": "critica", "sites": 7}) == Decimal(
        "30"
    )
    assert engine._single_number({"channels": 20}) == Decimal("20")
    assert engine._single_number({"a": 1, "b": 2}) is None
    assert engine._single_number({"with_cdr": True}) is None
    assert engine._single_number(None) is None


# --------------------------------------------------------------------------- #
# Empremta numericament canonica                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value",
    [20, 20.0, Decimal("20.00"), Decimal("2E+1"), Decimal("20.000")],
)
def test_every_numeric_type_of_the_same_value_has_one_form(value):
    assert jsonable(value) == {"$num": "20"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(Decimal("1.50"), "1.5"), (0.1, "0.1"), (1.5, "1.5")],
)
def test_fractional_numbers_have_the_shortest_decimal_form(value, expected):
    assert jsonable(value) == {"$num": expected}


@pytest.mark.parametrize("value", [-0.0, 0, Decimal("-0"), Decimal("0E-3"), 0.0])
def test_every_zero_is_the_same_zero(value):
    assert jsonable(value) == {"$num": "0"}


@pytest.mark.parametrize(
    "value", [float("inf"), float("-inf"), float("nan"), Decimal("NaN"), Decimal("Infinity")]
)
def test_non_finite_numbers_are_rejected(value):
    with pytest.raises(ValueError):
        jsonable(value)


def test_booleans_stay_booleans():
    assert jsonable(True) is True
    assert jsonable(False) is False
    assert jsonable({"a": True}) == {"a": True}


def test_nested_numbers_are_normalised_too():
    assert jsonable({"a": [20.0, {"b": Decimal("1.50")}], "c": (3, 4.0)}) == {
        "a": [{"$num": "20"}, {"b": {"$num": "1.5"}}],
        "c": [{"$num": "3"}, {"$num": "4"}],
    }
    assert canonical_json({"p": 20}) == canonical_json({"p": 20.0})


def test_stored_json_keeps_numbers_as_numbers():
    # `support_json` es desa i es mostra: nomes l'empremta passa els nombres a cadena.
    assert engine.as_json({"n": 3, "p": 20.5, "d": Decimal("1.50"), "ok": True}) == {
        "n": 3,
        "p": 20.5,
        "d": "1.5",
        "ok": True,
    }


def test_big_numbers_keep_every_digit():
    assert jsonable(10**40 + 7) == {"$num": "10000000000000000000000000000000000000007"}
    assert jsonable(10**40 + 7) != jsonable(10**40)
    big = "1234567890123456789012345678901234"
    assert jsonable(Decimal(big)) == {"$num": big}
    assert jsonable(Decimal(big + ".50")) == {"$num": big + ".5"}
    assert jsonable(Decimal("1E+40")) == jsonable(10**40) == {"$num": "1" + "0" * 40}


def test_a_number_is_never_a_string():
    assert jsonable(20) != jsonable("20")
    assert canonical_json(20) != canonical_json("20")
    assert canonical_json({"c": 20.0}) != canonical_json({"c": "20"})
    # Cap dict d'usuari no pot imitar un nombre etiquetat.
    assert canonical_json({"$num": "20"}) != canonical_json(20)


def test_a_string_vs_number_value_json_changes_the_structure_fingerprint():
    view = _view()
    r1 = view.requirements[0]

    def with_value(v):
        return replace(view, requirements=(replace(r1, value_json={"count": v}),))

    assert structure_fingerprint(with_value("20")) != structure_fingerprint(with_value(20))
    assert structure_fingerprint(with_value(20)) == structure_fingerprint(with_value(20.0))
