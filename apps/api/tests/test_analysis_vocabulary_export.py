"""El vocabulari del motor que consumeix el web: generat, determinista i complet."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.analysis.registry import EXPLANATIONS_BY_VERSION, RULES_VERSION, ExplanationCode

API_ROOT = Path(__file__).resolve().parents[1]


def _script():
    path = API_ROOT / "scripts" / "export_analysis_vocabulary.py"
    spec = importlib.util.spec_from_file_location("export_analysis_vocabulary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _codes(cls) -> set[str]:
    return {v for k, v in vars(cls).items() if k.isupper() and isinstance(v, str)}


def test_every_explanation_code_has_its_canonical_text():
    assert set(EXPLANATIONS_BY_VERSION[RULES_VERSION]) == _codes(ExplanationCode)
    for table in EXPLANATIONS_BY_VERSION.values():
        assert set(table) <= _codes(ExplanationCode)


def test_the_committed_json_is_exactly_what_the_script_generates():
    script = _script()
    expected = script.render(script.build_vocabulary())
    assert script.TARGET.read_text(encoding="utf-8") == expected, (
        "Vocabulari desactualitzat. Regenera'l: "
        "apps/api/.venv/Scripts/python apps/api/scripts/export_analysis_vocabulary.py"
    )


def test_the_output_is_deterministic_and_has_no_timestamps():
    script = _script()
    first = script.render(script.build_vocabulary())
    assert first == script.render(script.build_vocabulary())
    assert (
        first.endswith("\n")
        and "generated_at" not in first
        and "202" not in first.split('"explanations"')[0]
    )
    vocabulary = script.build_vocabulary()
    assert set(vocabulary) == {"contract", "explanations", "rendered", "rules_version"}
    for values in (*vocabulary["contract"].values(), *vocabulary["rendered"].values()):
        assert values == sorted(values)


def test_explanations_are_exported_per_rules_version():
    """El web renderitza cada run amb el copy de la seva `rules_version` (spec 3.11)."""
    vocabulary = _script().build_vocabulary()
    assert vocabulary["rules_version"] == RULES_VERSION
    assert set(vocabulary["explanations"]) == set(EXPLANATIONS_BY_VERSION)
    assert {"phase3a-analysis-v2", "phase3a-analysis-v3"} <= set(vocabulary["explanations"])
    for version, table in EXPLANATIONS_BY_VERSION.items():
        assert vocabulary["explanations"][version] == dict(table)
        assert list(vocabulary["explanations"][version]) == sorted(table)
