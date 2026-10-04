"""Els retols del vocabulari son text, mai claus (copy v3, F1).

Els retols de linies de servei, capacitats, normes, perfils i equips s'han corregit
d'accents. Aixo nomes es segur si cap codi (backend ni web) no els fa servir com a
clau o en una condicio: es comparen sempre els codis. Aquest test ho comprova amb un
grep sobre el codi font (no sobre els tests, que poden portar retols com a dades).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from app.knowledge import vocabulary

API_ROOT = Path(__file__).resolve().parents[1]
WEB_SRC = API_ROOT.parent / "web" / "src"
MIGRATIONS = (
    API_ROOT / "alembic" / "versions" / "0045_company_knowledge.py",
    API_ROOT / "alembic" / "versions" / "0046_mobile_telephony_line.py",
)

#: Retols de perfils i equips abans de la v3 (no son a cap migracio: viuen nomes al
#: vocabulari i a l'API).
OLD_PROFILE_AND_EQUIPMENT_LABELS = (
    "Responsable de fibra i certificacio",
    "Especialista en radioenllacos",
    "Tecnic de camp",
    "Agent de suport i atencio",
    "Reflectometre optic (OTDR)",
    "Equip de perdues optiques (OLTS)",
    "Mesurador de potencia optica",
)


def _migration(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _current_labels() -> set[str]:
    return {
        *(s.label for s in vocabulary.SERVICE_LINES),
        *(c.label for c in vocabulary.CAPABILITIES),
        *(s.label for s in vocabulary.CERTIFICATION_STANDARDS),
        *(p.label for p in vocabulary.PROFESSIONAL_PROFILES),
        *vocabulary.EQUIPMENT_TYPE_LABELS.values(),
    }


def _old_labels() -> set[str]:
    """Els retols antics que han canviat: els de les migracions i els de perfils/equips."""
    old: set[str] = set(OLD_PROFILE_AND_EQUIPMENT_LABELS)
    for path in MIGRATIONS:
        module = _migration(path)
        old |= {label for _, label in getattr(module, "_SERVICE_LINES", ())}
        old |= {row[2] for row in getattr(module, "_CAPABILITIES", ())}
        old |= {label for _, label, _ in getattr(module, "_STANDARDS", ())}
    return old - _current_labels()


def _is_test(path: Path) -> bool:
    name = path.name
    return (
        ".test." in name
        or name.startswith("test_")
        or "test" in {p.lower() for p in path.relative_to(path.anchor).parts[:-1]}
    )


def _sources() -> list[Path]:
    """Codi font del backend (app, scripts) i del web (src), sense tests ni migracions."""
    files = [
        *(API_ROOT / "app").rglob("*.py"),
        *(API_ROOT / "scripts").rglob("*.py"),
        *(p for ext in ("*.ts", "*.tsx") for p in WEB_SRC.rglob(ext)),
    ]
    return [p for p in files if not _is_test(p) and "__pycache__" not in p.parts]


def test_the_sources_are_found():
    sources = _sources()
    assert any(p.name == "vocabulary.py" for p in sources)
    assert any(p.name == "knowledge.ts" for p in sources)
    assert not any(p.name.endswith(".test.tsx") for p in sources)


def test_some_labels_did_change():
    old = _old_labels()
    assert "Troncals de veu i numeracio" in old
    assert "Telefonia mobil" in old
    assert "Tecnic de camp" in old
    assert "Centraleta i veu IP" not in old  # no ha canviat


def test_no_source_keeps_an_old_label():
    """Cap codi font no conserva un retol antic com a literal sencer (ni com a clau ni
    com a text). Un text lliure que el conte dins (un titol de demo) no es un retol."""
    old = _old_labels()
    hits = [
        (path.relative_to(API_ROOT.parent).as_posix(), label)
        for path in _sources()
        for label in old
        if re.search(r"[\"'`]" + re.escape(label) + r"[\"'`]", path.read_text(encoding="utf-8"))
    ]
    assert hits == []


def _condition_patterns(label: str) -> list[re.Pattern[str]]:
    q = r"[\"'`]" + re.escape(label) + r"[\"'`]"
    return [
        re.compile(r"(?:===?|!==?)\s*" + q),  # x === "Retol" / x == "Retol"
        re.compile(q + r"\s*(?:===?|!==?)"),  # "Retol" === x
        re.compile(r"\bcase\s+" + q),  # case "Retol":
        re.compile(r"\bin\s*[\(\[\{]\s*" + q),  # x in ("Retol", ...)
        re.compile(r"\.(?:includes|startsWith|endsWith|indexOf|has|get)\(\s*" + q),
        re.compile(r"\[\s*" + q + r"\s*\]"),  # mapa["Retol"]
        re.compile(r"[\{,]\s*" + q + r"\s*:"),  # {"Retol": ...} com a clau
    ]


@pytest.mark.parametrize("which", ["old", "current"])
def test_no_source_uses_a_label_as_a_key_or_in_a_condition(which):
    labels = _old_labels() if which == "old" else _current_labels()
    hits = []
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        for label in labels:
            if label not in text:
                continue
            for pattern in _condition_patterns(label):
                hits += [
                    (path.relative_to(API_ROOT.parent).as_posix(), m.group(0))
                    for m in pattern.finditer(text)
                ]
    assert hits == []


def test_the_condition_detector_is_not_empty():
    label = "Telefonia mòbil"
    for snippet in (
        'if line.label == "Telefonia mòbil":',
        "label === 'Telefonia mòbil'",
        'case "Telefonia mòbil":',
        'if label in ("Telefonia mòbil", "x"):',
        'labels.includes("Telefonia mòbil")',
        'BY_LABEL["Telefonia mòbil"]',
        '{ "Telefonia mòbil": "mobile_telephony" }',
    ):
        assert any(p.search(snippet) for p in _condition_patterns(label)), snippet
    assert not any(
        p.search('mobile_telephony: "Telefonia mòbil",') for p in _condition_patterns(label)
    )
