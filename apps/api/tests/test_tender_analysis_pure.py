"""Les peces PURES del motor (0051): fingerprint, inputs hash, comparador
territorial, registre de regles i cubells de punts. Cap base de dades."""

from __future__ import annotations

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
    inputs_hash,
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


def test_inputs_hash_depends_on_exactly_the_seven_inputs():
    base = dict(
        tender_revision_id=None, extraction_run_id="e", classification_run_id=None,
        review_revision_hash=None, tender_structure_fingerprint="s" * 64,
        knowledge_fingerprint="k" * 64, rules_version=registry.RULES_VERSION,
    )  # fmt: skip
    digest = inputs_hash(**base)
    assert digest == inputs_hash(**base)
    for key, value in (
        ("tender_revision_id", "r"),
        ("extraction_run_id", None),
        ("classification_run_id", "c"),
        ("review_revision_hash", "x" * 64),
        ("tender_structure_fingerprint", "t" * 64),
        ("knowledge_fingerprint", "j" * 64),
        ("rules_version", "phase3a-analysis-v3"),
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
    assert registry.RULES_VERSION == "phase3a-analysis-v2"
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
    for code in registry.EXPLANATION_CODES:
        assert registry.explanation_text(code)
    assert "{service_line}" not in registry.question_text(
        registry.QuestionCode.COMPANY_DECLARE_CAPABILITY,
        {"capability": "pbx.hot_desking", "service_line": "voice_pbx"},
    )
    assert "{title}" in registry.question_text(registry.QuestionCode.COMPANY_CONFIRM_FACT)


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
