"""Cobertura territorial a la fotografia (0047): representacio, neutralitat i empremta.

Funcio pura: cap base de dades. El que es protegeix:

* **neutralitat de la migracio**: `unknown` sense arees es exactament el que
  tenia tota dada abans de la 0047, i no deixa rastre al payload canonic, de
  manera que migrar no canvia cap fingerprint (cas A);
* **qualsevol altre estat afirma alguna cosa** i entra a l'empremta: `complete`
  amb arees (B), `partial` amb arees (C), `partial` sense cap (D) i `complete`
  sense cap («no cobrim enlloc», E);
* **determinisme**: l'ordre d'entrada de les arees no importa, i un canvi de
  rètol, codi o sistema de codis es un canvi material;
* **conservar no es comparar**: la fotografia guarda la cobertura tal qual i no
  dedueix res d'un municipi que no hi es.

Totes les dades son sintetiques.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from app.knowledge.coverage import (
    NEUTRAL_COVERAGE,
    AreaIn,
    CoverageIn,
    area_identity,
    canonical_coverage,
    coverage_dict,
    dedupe_areas,
)
from app.knowledge.snapshot import (
    EvaluationResult,
    FactIn,
    KnowledgeInputs,
    KnowledgeSource,
    PartnerIn,
    PartnerRoleIn,
    Provenance,
    ServiceIn,
    build_snapshot,
    evaluate_fact,
)
from app.models.enums import (
    AreaKind,
    CoverageCompleteness,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    VerificationStatus,
)

TODAY = date(2026, 10, 1)


def prov(record_id: str, *, verified: bool = False) -> Provenance:
    return Provenance(
        record_id=record_id,
        origin=KnowledgeOrigin.DECLARED,
        as_of=date(2026, 9, 29),
        valid_until=None,
        verification=VerificationStatus.VERIFIED
        if verified
        else VerificationStatus.PENDING_VERIFICATION,
        lifecycle=KnowledgeLifecycle.CURRENT,
        has_evidence=verified,
    )


def municipality(label: str, code: str | None = None) -> AreaIn:
    return AreaIn(
        kind=AreaKind.MUNICIPALITY, label=label, code=code, code_system="INE" if code else None
    )


ABC = (municipality("Municipi A"), municipality("Municipi B"), municipality("Municipi C"))
COMPLETE_ABC = CoverageIn(completeness=CoverageCompleteness.COMPLETE, areas=ABC)
PARTIAL_ABC = CoverageIn(completeness=CoverageCompleteness.PARTIAL, areas=ABC)
PARTIAL_NONE = CoverageIn(completeness=CoverageCompleteness.PARTIAL)
COMPLETE_NONE = CoverageIn(completeness=CoverageCompleteness.COMPLETE)


def fact(coverage: CoverageIn = NEUTRAL_COVERAGE, *, verified: bool = False) -> FactIn:
    return FactIn(
        prov=prov("f1", verified=verified),
        key="own_ftth_infrastructure",
        value=True,
        coverage=coverage,
    )


def snap(**kwargs) -> object:
    return build_snapshot(KnowledgeInputs(company_id="empresa-a", **kwargs), on=TODAY)


# --------------------------------------------------------------------------- #
# Representacio canonica                                                       #
# --------------------------------------------------------------------------- #


def test_the_neutral_state_has_no_canonical_form_but_is_always_readable():
    assert NEUTRAL_COVERAGE.is_neutral
    assert canonical_coverage(NEUTRAL_COVERAGE) is None
    # A la lectura hi es sempre, tambe quan no diu res.
    assert coverage_dict(NEUTRAL_COVERAGE) == {"completeness": "unknown", "areas": []}


def test_the_canonical_form_keeps_only_the_structural_fields_in_a_stable_order():
    coverage = CoverageIn(
        completeness=CoverageCompleteness.COMPLETE,
        areas=(
            AreaIn(AreaKind.MUNICIPALITY, "  Municipi   B "),
            AreaIn(AreaKind.COMARCA, "Comarca X", code="C1", code_system="IDESCAT"),
            AreaIn(AreaKind.MUNICIPALITY, "Municipi A", code="17001", code_system="INE"),
        ),
    )
    assert canonical_coverage(coverage) == {
        "completeness": "complete",
        "areas": [
            {"kind": "comarca", "label": "Comarca X", "code": "C1", "code_system": "IDESCAT"},
            # Sense codi abans que amb codi dins del mateix tipus; els espais, normalitzats.
            {"kind": "municipality", "label": "Municipi B", "code": None, "code_system": None},
            {"kind": "municipality", "label": "Municipi A", "code": "17001", "code_system": "INE"},
        ],
    }


# --------------------------------------------------------------------------- #
# Empremta: A-E                                                                #
# --------------------------------------------------------------------------- #


def test_a_unknown_without_areas_is_omitted_and_does_not_change_the_fingerprint():
    """Cas A: la migracio 0047 posa `unknown` a tot; cap empremta no es mou."""
    before = snap(facts=[FactIn(prov=prov("f1"), key="own_ftth_infrastructure", value=True)])
    after = snap(facts=[fact(NEUTRAL_COVERAGE)])
    assert after.fingerprint == before.fingerprint
    assert after.facts["own_ftth_infrastructure"].claims[0].coverage is None


def test_b_c_d_e_any_other_state_enters_the_fingerprint():
    base = snap(facts=[fact()]).fingerprint
    cases = {
        "B complete + arees": COMPLETE_ABC,
        "C partial + arees": PARTIAL_ABC,
        "D partial sense arees": PARTIAL_NONE,
        "E complete sense arees": COMPLETE_NONE,
    }
    fingerprints = {name: snap(facts=[fact(cov)]).fingerprint for name, cov in cases.items()}
    for name, fingerprint in fingerprints.items():
        assert fingerprint != base, name
    # I son quatre afirmacions diferents entre elles.
    assert len(set(fingerprints.values())) == 4


def test_the_fingerprint_does_not_depend_on_the_order_of_the_areas():
    shuffled = CoverageIn(completeness=CoverageCompleteness.COMPLETE, areas=tuple(reversed(ABC)))
    assert snap(facts=[fact(COMPLETE_ABC)]).fingerprint == snap(facts=[fact(shuffled)]).fingerprint


def test_a_label_or_code_change_is_a_material_change():
    base = snap(facts=[fact(COMPLETE_ABC)]).fingerprint
    renamed = replace(COMPLETE_ABC, areas=(*ABC[:2], municipality("Municipi D")))
    coded = replace(COMPLETE_ABC, areas=(*ABC[:2], municipality("Municipi C", code="17003")))
    fewer = replace(COMPLETE_ABC, areas=ABC[:2])
    assert snap(facts=[fact(renamed)]).fingerprint != base
    assert snap(facts=[fact(coded)]).fingerprint != base
    assert snap(facts=[fact(fewer)]).fingerprint != base
    # Nomes els espais no compten: es el mateix nom.
    spaced = replace(COMPLETE_ABC, areas=(*ABC[:2], municipality("Municipi   C")))
    assert snap(facts=[fact(spaced)]).fingerprint == base


def test_services_and_partner_roles_carry_coverage_the_same_way():
    service = ServiceIn(prov=prov("s1"), name="FTTH", service_line_code="access_ftth")
    partner = PartnerIn(prov=prov("p1"), name="Operador Demo")
    role = PartnerRoleIn(
        prov=prov("r1"), partner_id="p1", role="wholesale_operator", service_line_code="access_ftth"
    )
    base = snap(services=[service], partners=[partner], partner_roles=[role])
    assert base.services["access_ftth"].claims[0].coverage is None
    assert "coverage" not in base.partners["Operador Demo"].claims[0].value["roles"][0]

    covered = snap(
        services=[replace(service, coverage=PARTIAL_ABC)],
        partners=[partner],
        partner_roles=[replace(role, coverage=COMPLETE_ABC)],
    )
    assert covered.fingerprint != base.fingerprint
    assert covered.services["access_ftth"].claims[0].coverage == canonical_coverage(PARTIAL_ABC)
    role_value = covered.partners["Operador Demo"].claims[0].value["roles"][0]
    assert role_value["coverage"] == canonical_coverage(COMPLETE_ABC)
    # Nomes la cobertura del servei: una altra empremta, i la del paper, una altra.
    only_service = snap(
        services=[replace(service, coverage=PARTIAL_ABC)], partners=[partner], partner_roles=[role]
    )
    assert only_service.fingerprint not in {base.fingerprint, covered.fingerprint}


# --------------------------------------------------------------------------- #
# Les arees son un conjunt                                                     #
# --------------------------------------------------------------------------- #


def test_the_same_area_twice_is_one_area_and_does_not_change_the_fingerprint():
    once = CoverageIn(completeness=CoverageCompleteness.COMPLETE, areas=(municipality("Banyoles"),))
    twice = CoverageIn(
        completeness=CoverageCompleteness.COMPLETE,
        areas=(municipality("Banyoles"), municipality("  Banyoles ")),
    )
    assert area_identity(municipality("Banyoles")) == area_identity(municipality(" Banyoles  "))
    assert canonical_coverage(twice) == canonical_coverage(once)
    assert len(canonical_coverage(twice)["areas"]) == 1
    assert snap(facts=[fact(twice)]).fingerprint == snap(facts=[fact(once)]).fingerprint
    # Tampoc enmig d'altres, ni en un altre ordre.
    base = snap(facts=[fact(COMPLETE_ABC)]).fingerprint
    with_duplicate = replace(COMPLETE_ABC, areas=(ABC[2], ABC[0], ABC[1], ABC[0]))
    assert snap(facts=[fact(with_duplicate)]).fingerprint == base


def test_really_different_areas_are_all_kept():
    """Mateix nom pero un altre tipus, o amb codi i sense: son identitats diferents,
    i aqui no es resol cap equivalencia geografica."""
    areas = (
        municipality("Banyoles"),
        AreaIn(AreaKind.COMARCA, "Banyoles"),
        municipality("Banyoles", code="17015"),
        AreaIn(AreaKind.MUNICIPALITY, "Banyoles", code="17015", code_system="IDESCAT"),
        municipality("banyoles"),
    )
    coverage = CoverageIn(completeness=CoverageCompleteness.PARTIAL, areas=areas)
    assert len({area_identity(a) for a in areas}) == 5
    assert len(canonical_coverage(coverage)["areas"]) == 5
    assert len(dedupe_areas(areas + areas)) == 5


# --------------------------------------------------------------------------- #
# Gold sintetic: FTTH propia, completa, a tres municipis                       #
# --------------------------------------------------------------------------- #


def test_gold_own_ftth_complete_in_three_municipalities_is_kept_as_is_and_nothing_is_inferred():
    snapshot = snap(facts=[fact(COMPLETE_ABC, verified=True)])
    entry = snapshot.facts["own_ftth_infrastructure"]
    assert entry.source == KnowledgeSource.CURRENT_VERIFIED
    assert entry.claims[0].coverage == {
        "completeness": "complete",
        "areas": [
            {"kind": "municipality", "label": "Municipi A", "code": None, "code_system": None},
            {"kind": "municipality", "label": "Municipi B", "code": None, "code_system": None},
            {"kind": "municipality", "label": "Municipi C", "code": None, "code_system": None},
        ],
    }
    # El fet continua avaluant-se com a fet: cert. La fotografia no respon
    # «i a Municipi D?»: cap camp no en diu res, i no hi ha cap comparador.
    assert evaluate_fact(snapshot, "own_ftth_infrastructure").result == EvaluationResult.SATISFIES
    assert "Municipi D" not in str(entry.claims[0].coverage)
    assert not hasattr(entry, "covers")
