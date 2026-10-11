"""Estat del paquet a l'empremta d'estructura (3B-2 §4.4).

`package_status`, `gaps` i `section_status` entren a la forma canonica NOMES quan hi
ha algun forat obert: un plec sense forats conserva exactament l'empremta d'abans
(precedent `submission_deadline`), i el gold de L'Escala no canvia. L'absencia d'una
passada de classificacio global no es una propietat del plec: per si sola no canvia
l'empremta.
"""

from __future__ import annotations

from dataclasses import replace

from app.analysis.fingerprint import (
    PACKAGE_COMPLETE,
    PACKAGE_INCOMPLETE,
    PACKAGE_UNKNOWN,
    SECTION_NONE_DECLARED,
    SECTION_PRESENT,
    SECTION_UNKNOWN,
    PackageGapView,
    TenderStructureView,
    gap_section_status,
    package_status_for,
    section_presence,
    section_status_for,
    structure_fingerprint,
)
from tests.test_tender_analysis_pure import _view

SITES_GAP = PackageGapView(
    code="referenced_document_missing",
    detail={"key": "DOC-04", "declared_codes": ["DOC-04"]},
    would_provide=("sites",),
    producer="classification",
)
UNKNOWN_IMPACT_GAP = PackageGapView(
    code="referenced_document_missing",
    detail={"key": "DOC-03"},
    would_provide=(),
    producer="classification",
)


def _with_package(view: TenderStructureView, gaps, *, has_run=True) -> TenderStructureView:
    presence = section_presence(view)
    return replace(
        view,
        package_status=package_status_for(gaps, has_run=has_run),
        gaps=tuple(gaps),
        section_status=section_status_for(presence, gaps, has_run=has_run),
    )


def test_without_gaps_the_fingerprint_is_exactly_the_previous_one():
    view = _view()
    base = structure_fingerprint(view)
    assert "package_status" not in view.canonical()
    # Amb passada vigent i sense forats: complet, i cap clau nova.
    complete = _with_package(view, [])
    assert complete.package_status == PACKAGE_COMPLETE
    assert structure_fingerprint(complete) == base
    # Sense cap passada: `unknown`, pero no es una propietat del plec.
    unknown = _with_package(view, [], has_run=False)
    assert unknown.package_status == PACKAGE_UNKNOWN
    assert SECTION_UNKNOWN not in {unknown.section_status[s] for s in ("blocks", "sites")}
    assert structure_fingerprint(unknown) == base
    # Un plec sense res (seccions buides, sense passada) tampoc no canvia.
    empty = TenderStructureView(tender_id="t")
    assert structure_fingerprint(_with_package(empty, [], has_run=False)) == (
        structure_fingerprint(empty)
    )


def test_an_open_gap_enters_the_canonical_form_and_changes_the_fingerprint():
    view = _view()
    base = structure_fingerprint(view)
    gapped = _with_package(view, [SITES_GAP])
    assert gapped.package_status == PACKAGE_INCOMPLETE
    canonical = gapped.canonical()
    assert canonical["package_status"] == PACKAGE_INCOMPLETE
    assert canonical["gaps"] == [
        {"code": "referenced_document_missing", "key": "DOC-04", "would_provide": ["sites"]}
    ]
    # La vista te seus: continuen `present` (fix 1, ruling 2). Sense seus, `unknown`.
    assert canonical["section_status"]["sites"] == SECTION_PRESENT
    assert structure_fingerprint(gapped) != base
    no_sites = _with_package(replace(view, sites=()), [SITES_GAP])
    assert no_sites.canonical()["section_status"]["sites"] == SECTION_UNKNOWN


def test_the_gap_part_is_canonical_order_detail_and_producer_do_not_count():
    view = _view()
    a = _with_package(view, [SITES_GAP, UNKNOWN_IMPACT_GAP])
    b = _with_package(
        view,
        [
            replace(UNKNOWN_IMPACT_GAP, detail={"key": "DOC-03", "quote": "vegeu el DOC-03"}),
            replace(SITES_GAP, producer="operator"),
        ],
    )
    assert structure_fingerprint(a) == structure_fingerprint(b)
    # Que aportaria el document que falta si que compta.
    c = _with_package(view, [replace(SITES_GAP, would_provide=("sites", "scope_items"))])
    assert structure_fingerprint(c) != structure_fingerprint(_with_package(view, [SITES_GAP]))


def test_with_gaps_the_run_does_not_change_the_fingerprint_either():
    """Amb forats, que hi hagi o no una passada vigent no canvia l'empremta: la forma
    canonica es deriva dels forats i de les files, mai de la passada."""
    view = _view()
    assert structure_fingerprint(_with_package(view, [SITES_GAP], has_run=True)) == (
        structure_fingerprint(_with_package(view, [SITES_GAP], has_run=False))
    )


def test_section_status_rules():
    empty = TenderStructureView(tender_id="t")
    presence = section_presence(empty)
    assert set(presence) == {
        "blocks",
        "sites",
        "scope_items",
        "territories",
        "award_criteria",
        "contract_facts",
    }
    # Passada vigent, cap forat: buit = `none_declared`.
    assert set(section_status_for(presence, [], has_run=True).values()) == {SECTION_NONE_DECLARED}
    # Sense passada: buit = `unknown`.
    assert set(section_status_for(presence, [], has_run=False).values()) == {SECTION_UNKNOWN}
    # Forat d'impacte desconegut: TOTA seccio buida es `unknown`, cap `none_declared`.
    assert set(section_status_for(presence, [UNKNOWN_IMPACT_GAP], has_run=True).values()) == {
        SECTION_UNKNOWN
    }
    # Un forat que nomena una seccio: aquella es `unknown`; la resta, `none_declared`.
    status = section_status_for(presence, [SITES_GAP], has_run=True)
    assert status["sites"] == SECTION_UNKNOWN
    assert {v for k, v in status.items() if k != "sites"} == {SECTION_NONE_DECLARED}

    view = _view()  # totes les seccions amb files
    full = section_presence(view)
    assert all(full.values())
    assert set(section_status_for(full, [], has_run=False).values()) == {SECTION_PRESENT}
    # Impacte desconegut: una seccio amb files continua `present`.
    assert set(section_status_for(full, [UNKNOWN_IMPACT_GAP], has_run=True).values()) == {
        SECTION_PRESENT
    }
    # Fix 1 (ruling 2): un forat que nomena una seccio AMB files la deixa `present`: les
    # files son fets citats; el forat continua sent una troballa i fa el paquet incomplet.
    assert section_status_for(full, [SITES_GAP], has_run=True)["sites"] == SECTION_PRESENT
    # La part que entra a l'empremta es la dels forats (mai la de la passada).
    assert gap_section_status(_with_package(view, [SITES_GAP], has_run=False)) == (
        section_status_for(full, [SITES_GAP], has_run=True)
    )


def test_only_some_codes_with_unknown_impact_broaden_to_every_empty_section():
    """Fix 1 (ruling 1): un format que no sabem llegir (o un forat declarat per
    l'operador sense `would_provide`) fa el paquet incomplet, pero no fa `unknown` cap
    seccio. Nomes `referenced_document_missing`, `required_role_missing` i `no_text`
    amb impacte desconegut s'estenen a totes les seccions buides."""
    presence = section_presence(TenderStructureView(tender_id="t"))
    for code in ("format_not_analyzable", "declared_by_operator"):
        gap = PackageGapView(code=code, detail={"key": "annex.docx"}, would_provide=())
        assert package_status_for([gap], has_run=True) == PACKAGE_INCOMPLETE
        assert set(section_status_for(presence, [gap], has_run=True).values()) == {
            SECTION_NONE_DECLARED
        }, code
    for code in ("referenced_document_missing", "required_role_missing", "no_text"):
        gap = PackageGapView(code=code, detail={"key": "x"}, would_provide=())
        assert set(section_status_for(presence, [gap], has_run=True).values()) == {
            SECTION_UNKNOWN
        }, code


def test_package_status_rules():
    assert package_status_for([], has_run=False) == PACKAGE_UNKNOWN
    # Fix 2 (M3): els forats son fets, independents de la passada: amb forats oberts,
    # `incomplete` sempre; `unknown` nomes sense passada i sense forats.
    assert package_status_for([SITES_GAP], has_run=False) == PACKAGE_INCOMPLETE
    assert package_status_for([], has_run=True) == PACKAGE_COMPLETE
    assert package_status_for([SITES_GAP], has_run=True) == PACKAGE_INCOMPLETE
