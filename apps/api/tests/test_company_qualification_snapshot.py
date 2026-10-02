"""Qualificacio i mitjans a la fotografia del coneixement (0052). Funcio pura.

Exercicis, referencies d'execucio, assegurances, registres, perfils, equips i fets
quantitatius: precedencia, vigencia, empremta i avaluacions. «No consta» es
`unknown`; nomes una dada actual completa que no arriba es `does_not_satisfy`.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from app.knowledge.snapshot import (
    CertificationIn,
    EquipmentIn,
    EvaluationResult,
    ExecutionReferenceIn,
    FactIn,
    FinancialYearIn,
    InsuranceIn,
    KnowledgeInputs,
    KnowledgeSource,
    PendingKind,
    PersonIn,
    Provenance,
    RegistrationIn,
    ServiceIn,
    build_snapshot,
    evaluate_equipment,
    evaluate_execution,
    evaluate_insurance,
    evaluate_numeric_fact,
    evaluate_profiles,
    evaluate_registration,
    evaluate_turnover,
)
from app.models.enums import KnowledgeLifecycle, KnowledgeOrigin, VerificationStatus

TODAY = date(2026, 10, 2)
Y = TODAY.year
D = Decimal


def prov(
    record_id: str,
    *,
    verified: bool = False,
    lifecycle: KnowledgeLifecycle = KnowledgeLifecycle.CURRENT,
    valid_until: date | None = None,
) -> Provenance:
    return Provenance(
        record_id=record_id,
        origin=KnowledgeOrigin.DECLARED,
        as_of=date(2026, 9, 1),
        valid_until=valid_until,
        verification=VerificationStatus.VERIFIED
        if verified
        else VerificationStatus.PENDING_VERIFICATION,
        lifecycle=lifecycle,
        has_evidence=verified,
    )


def snap(**sections):
    return build_snapshot(KnowledgeInputs(company_id="c", **sections), on=TODAY)


def years(*revenues, verified=()):
    return [
        FinancialYearIn(prov(f"y{Y - n}", verified=(Y - n) in verified), Y - n, D(r))
        for n, r in zip((3, 2, 1), revenues, strict=True)
        if r is not None
    ]


# --------------------------------------------------------------------------- #
# Neutralitat i empremta                                                        #
# --------------------------------------------------------------------------- #


def test_legacy_rows_keep_exactly_the_fingerprint_of_the_base_code():
    """Calculada amb el codi de 93827c9 (0051): les files d'abans no canvien d'empremta."""
    legacy = snap(
        certifications=[
            CertificationIn(prov("c1", verified=True), "ENS", "ENS", "medium", ("voice_pbx",))
        ],
        facts=[
            FactIn(
                Provenance(
                    "f1", KnowledgeOrigin.DECLARED, date(2026, 9, 1), None,
                    VerificationStatus.PENDING_VERIFICATION, KnowledgeLifecycle.CURRENT,
                ),
                "support_24x7",
                True,
                "nota",
            ),
            FactIn(prov("f2"), "telecom_operator_cnmc", False),
        ],
        services=[ServiceIn(prov("s1"), "Veu", "voice_pbx", "own")],
    )  # fmt: skip
    assert legacy.fingerprint == (
        "d8af7830cf65f952548010b1ba618fc1cc3ccd243689d1b3f88f0452357df12c"
    )
    assert legacy.financial_years == {} and legacy.equipment == {}


def test_every_new_material_value_changes_the_fingerprint():
    base = dict(
        financial_years=years(80000, 120000, 150000),
        execution_references=[
            ExecutionReferenceIn(prov("p1"), "A", "completed", D(60000), end_date=date(Y - 1, 2, 1),
                                 service_lines=("dedicated_network",)),
        ],
        insurances=[
            InsuranceIn(prov("i1"), "civil_liability", limits=(("per_claim", D(600000)),))
        ],
        registrations=[
            RegistrationIn(prov("r1"), "Registre", "sector_operator_registry", issuer="X",
                           scope=("access_ftth",)),
        ],
        people=[PersonIn(prov("h1"), "available", ("voip_specialist",))],
        equipment=[
            EquipmentIn(prov("e1"), "otdr", calibration_required=True,
                        calibration_valid_until=TODAY + timedelta(days=10)),
        ],
        facts=[FactIn(prov("f1"), "ipv4_assignable_count", True, value_number=D(20))],
    )  # fmt: skip
    fingerprint = snap(**base).fingerprint
    variants = {
        "revenue": {**base, "financial_years": years(80000, 120000, 150001)},
        "amount": {
            **base,
            "execution_references": [replace(base["execution_references"][0], amount=D(60001))],
        },
        "limit": {
            **base,
            "insurances": [replace(base["insurances"][0], limits=(("per_claim", D(1)),))],
        },
        "scope": {
            **base,
            "registrations": [replace(base["registrations"][0], scope=("voice_trunk",))],
        },
        "profile": {
            **base,
            "people": [replace(base["people"][0], profile_codes=("project_manager",))],
        },
        "calibration": {
            **base,
            "equipment": [
                replace(base["equipment"][0], calibration_valid_until=TODAY + timedelta(days=11))
            ],
        },
        "number": {**base, "facts": [replace(base["facts"][0], value_number=D(21))]},
        "annual": {
            **base,
            "execution_references": [
                replace(base["execution_references"][0], annual_amounts=((Y - 1, D(60000)),))
            ],
        },
    }
    for name, sections in variants.items():
        assert snap(**sections).fingerprint != fingerprint, name
    # La mateixa dada dues vegades: la mateixa empremta.
    assert snap(**base).fingerprint == fingerprint


# --------------------------------------------------------------------------- #
# R1: el millor dels 3 ultims exercicis (cap rellotge)                          #
# --------------------------------------------------------------------------- #


def fy(year, revenue, *, verified=False, currency="EUR"):
    return FinancialYearIn(
        prov(f"y{year}", verified=verified), year, D(revenue) if revenue else None, currency
    )


#: Termini de presentacio del plec: la referencia de «els 3 ultims exercicis».
TURNOVER_REFERENCE = date(2026, 3, 1)


def turnover(rows, minimum="91904.74", reference_date=TURNOVER_REFERENCE):
    return evaluate_turnover(snap(financial_years=rows), D(minimum), 3, reference_date)


def test_three_complete_exercises_give_the_maximum():
    ev = turnover([fy(2023, 80000), fy(2024, 120000), fy(2025, 150000)])
    assert ev.result == EvaluationResult.SATISFIES and ev.record_ids == ("y2025",)
    assert ev.source == KnowledgeSource.CURRENT_DECLARED and ev.accreditation_pending
    below = turnover([fy(2023, 80000), fy(2024, 120000), fy(2025, 150000)], minimum="200000")
    assert (below.result, below.reason) == (EvaluationResult.DOES_NOT_SATISFY, "turnover_below")
    assert set(below.record_ids) == {"y2023", "y2024", "y2025"}


def test_with_four_exercises_only_the_last_three_count():
    rows = [fy(2022, 500000), fy(2023, 80000), fy(2024, 90000), fy(2025, 85000)]
    ev = turnover(rows)
    assert (ev.result, ev.reason) == (EvaluationResult.DOES_NOT_SATISFY, "turnover_below")
    assert "y2022" not in ev.record_ids, "un exercici antic no pot satisfer"


def test_fewer_than_three_consecutive_exercises_is_unknown_even_if_one_meets():
    for rows in (
        [fy(2025, 150000)],
        [fy(2024, 150000), fy(2025, 150000)],
        [fy(2023, 150000), fy(2025, 150000)],  # falta el 2024: no es pot demostrar
    ):
        ev = turnover(rows)
        assert (ev.result, ev.reason) == (EvaluationResult.UNKNOWN, "exercises_incomplete")
    assert turnover([]).reason == "no_current_data"


def test_the_order_of_rows_is_irrelevant_and_no_clock_is_read():
    rows = [fy(2025, 150000), fy(2023, 80000), fy(2024, 120000)]
    assert turnover(rows) == turnover(list(reversed(rows)))
    # Mateixes dades, una altra data d'avaluacio: mateix resultat.
    other_day = build_snapshot(
        KnowledgeInputs(company_id="c", financial_years=rows), on=date(2031, 6, 1)
    )
    assert evaluate_turnover(other_day, D("91904.74"), 3, TURNOVER_REFERENCE) == turnover(rows)


def test_without_a_reference_date_the_last_exercises_are_unknown():
    """Ni el rellotge ni «els mes recents que hi ha»: un plec antic no es pot satisfer
    amb exercicis posteriors."""
    rows = [fy(2023, 80000), fy(2024, 120000), fy(2025, 150000)]
    ev = turnover(rows, reference_date=None)
    assert (ev.result, ev.reason) == (EvaluationResult.UNKNOWN, "reference_date_missing")
    old_tender = turnover(rows, reference_date=date(2019, 5, 1))
    assert (old_tender.result, old_tender.reason) == (
        EvaluationResult.UNKNOWN,
        "none_before_reference",
    )


def test_a_reference_date_excludes_exercises_not_closed_before_it():
    rows = [fy(2023, 80000), fy(2024, 120000), fy(2025, 150000), fy(2026, 10**7)]
    ev = turnover(rows, reference_date=date(2026, 3, 1))
    assert ev.result == EvaluationResult.SATISFIES and ev.record_ids == ("y2025",)
    later = turnover(rows, reference_date=date(2027, 3, 1))
    assert later.result == EvaluationResult.SATISFIES and later.record_ids == ("y2026",)


def test_verified_wins_a_tie_and_unknown_amounts_or_currency_stay_unknown():
    tie = [fy(2023, 150000, verified=True), fy(2024, 120000), fy(2025, 150000)]
    assert turnover(tie).source == KnowledgeSource.CURRENT_VERIFIED
    missing = turnover([fy(2023, 80000), fy(2024, None), fy(2025, 85000)])
    assert (missing.result, missing.reason) == (EvaluationResult.UNKNOWN, "revenue_missing")
    usd = turnover([fy(2023, 80000), fy(2024, 80000), fy(2025, 10**6, currency="USD")])
    assert (usd.result, usd.reason) == (EvaluationResult.UNKNOWN, "currency_not_comparable")


# --------------------------------------------------------------------------- #
# R2: l'any de major execucio (imports per any, data de referencia)            #
# --------------------------------------------------------------------------- #

REFERENCE = date(2026, 3, 1)  # finestra 2023-2025


def ref(record_id, amount, start, end=None, *, annual=(), status="completed",
        lines=("dedicated_network",)):  # fmt: skip
    return ExecutionReferenceIn(
        prov(record_id),
        record_id,
        status,
        D(amount) if amount is not None else None,
        start_date=date(start, 2, 1) if start else None,
        end_date=date(end or start, 11, 30) if (end or start) else None,
        service_lines=lines,
        annual_amounts=tuple((y, D(a)) for y, a in annual),
    )


def execution(refs, minimum="42888.88", reference_date=REFERENCE, lines=None):
    return evaluate_execution(
        snap(execution_references=refs), D(minimum), 3, service_lines=lines,
        reference_date=reference_date,
    )  # fmt: skip


def test_a_multi_year_total_is_never_imputed_to_its_last_year():
    """2023-2025 per 90.000 sense desglossament: no es «90.000 el 2025»."""
    ev = execution([ref("p", 90000, 2023, 2025)])
    assert (ev.result, ev.reason) == (EvaluationResult.UNKNOWN, "amount_unallocated")
    split = [(2023, 30000), (2024, 30000), (2025, 30000)]
    ev = execution([ref("p", 90000, 2023, 2025, annual=split)])
    assert (ev.result, ev.reason) == (EvaluationResult.DOES_NOT_SATISFY, "execution_below")


def test_annual_amounts_of_several_projects_add_up_per_year():
    refs = [
        ref("a", 60000, 2024, 2025, annual=[(2024, 20000), (2025, 40000)]),
        ref("b", 30000, 2024),  # un sol any, sense desglossament: es d'aquell any
    ]
    ev = execution(refs)
    assert ev.result == EvaluationResult.SATISFIES and set(ev.record_ids) == {"a", "b"}


def test_known_amounts_already_above_the_threshold_satisfy_despite_unallocated_ones():
    refs = [ref("big", 50000, 2025), ref("open", 200000, 2020, 2025)]
    assert execution(refs).result == EvaluationResult.SATISFIES


def test_unallocated_amounts_that_could_change_the_result_make_it_unknown():
    refs = [ref("small", 10000, 2025), ref("open", 200000, 2020, 2025)]
    assert execution(refs).reason == "amount_unallocated"
    # Un plurianual que acaba abans de la finestra no la pot tocar: no la deixa oberta.
    refs = [ref("small", 10000, 2025), ref("old", 200000, 2018, 2021)]
    assert execution(refs).result == EvaluationResult.DOES_NOT_SATISFY


def test_without_a_reference_date_the_last_years_are_unknown():
    ev = execution([ref("big", 10**6, 2025)], reference_date=None)
    assert (ev.result, ev.reason) == (EvaluationResult.UNKNOWN, "reference_date_missing")


def test_planned_out_of_window_or_other_line_projects_do_not_accredit():
    refs = [
        ref("planned", 90000, 2025, status="planned"),
        ref("old", 90000, 2019),
        ref("pbx", 90000, 2025, lines=("voice_pbx",)),
    ]
    assert execution(refs, lines=["dedicated_network"]).reason == "none_in_window"
    assert execution([]).reason == "no_current_data"


def test_an_ongoing_project_counts_by_its_executed_annual_amounts():
    """A: en curs amb import executat acreditable aquell any: compta."""
    ongoing = ref("o", 200000, 2024, 2027, annual=[(2025, 50000)], status="in_progress")
    ev = execution([ongoing])
    assert ev.result == EvaluationResult.SATISFIES and ev.record_ids == ("o",)


def test_an_ongoing_project_without_annual_execution_invents_nothing():
    """B: en curs sense execucio anual: l'import es contractat, no executat."""
    ongoing = ref("o", 200000, 2025, status="in_progress")
    ev = execution([ongoing])
    assert (ev.result, ev.reason) == (EvaluationResult.UNKNOWN, "amount_unallocated")
    assert execution([ongoing, ref("small", 10000, 2025)]).result == EvaluationResult.UNKNOWN


def test_a_completed_project_with_a_breakdown_counts_the_same_way():
    """C: acabat amb desglossament: el mateix import executat, el mateix resultat."""
    done = ref("d", 200000, 2024, 2027, annual=[(2025, 50000)], status="completed")
    ongoing = ref("d", 200000, 2024, 2027, annual=[(2025, 50000)], status="in_progress")
    assert execution([done]) == execution([ongoing])


def test_a_planned_project_never_counts_even_with_amounts():
    planned = ref("p", 200000, 2025, annual=[(2025, 200000)], status="planned")
    assert execution([planned]).reason == "none_relevant"


def test_the_window_comes_from_the_reference_date_not_from_the_clock():
    refs = [ref("p", 50000, 2025)]
    assert execution(refs, reference_date=date(2026, 1, 15)).result == (EvaluationResult.SATISFIES)
    assert execution(refs, reference_date=date(2030, 1, 15)).reason == "none_in_window"


# --------------------------------------------------------------------------- #
# Assegurances                                                                  #
# --------------------------------------------------------------------------- #

RC_REQUIRED = {"per_claim": D(300000), "annual_aggregate": D(300000),
               "employer_per_victim": D(150000)}  # fmt: skip


def rc(record_id="i1", valid_until=None, **limits):
    return InsuranceIn(
        prov(record_id, valid_until=valid_until),
        "civil_liability",
        limits=tuple(sorted((k, D(v)) for k, v in limits.items())),
    )


def test_insurance_needs_every_required_limit_declared_and_enough():
    full = dict(per_claim=600000, annual_aggregate=600000, employer_per_victim=300000)

    def result(**limits):
        return evaluate_insurance(
            snap(insurances=[rc(**limits)]), "civil_liability", RC_REQUIRED
        ).result

    assert result(**full) == EvaluationResult.SATISFIES
    low = {**full, "employer_per_victim": 100000}
    assert result(**low) == EvaluationResult.DOES_NOT_SATISFY
    partial = dict(per_claim=600000, annual_aggregate=600000)
    assert result(**partial) == EvaluationResult.UNKNOWN, "un sublimit absent no es cap zero"


def test_an_expired_insurance_does_not_count_and_is_pending():
    s = snap(insurances=[rc(valid_until=TODAY - timedelta(days=1), per_claim=10**6,
                            annual_aggregate=10**6, employer_per_victim=10**6)])  # fmt: skip
    assert evaluate_insurance(s, "civil_liability", RC_REQUIRED).result == EvaluationResult.UNKNOWN
    assert any(p.kind == PendingKind.EXPIRED and p.subject == "insurance"
               for p in s.pending_validation)  # fmt: skip


# --------------------------------------------------------------------------- #
# Registres                                                                     #
# --------------------------------------------------------------------------- #


def reg(scope, issuer="CNMC", record_id="r1"):
    return RegistrationIn(prov(record_id), "Registre", "sector_operator_registry", issuer=issuer,
                          scope=scope)  # fmt: skip


def test_registration_scope_is_compared_only_when_structured():
    lines = ["voice_trunk", "access_ftth", "dedicated_network"]
    full = snap(registrations=[reg(("access_ftth", "dedicated_network", "voice_trunk"))])
    assert evaluate_registration(full, "sector_operator_registry", lines, "cnmc").result == (
        EvaluationResult.SATISFIES
    )
    partial = evaluate_registration(
        snap(registrations=[reg(("access_ftth",))]), "sector_operator_registry", lines
    )
    assert (partial.result, partial.reason) == (EvaluationResult.UNKNOWN, "scope_not_covered")
    loose = snap(registrations=[reg(None)])
    assert evaluate_registration(loose, "sector_operator_registry", lines).reason == "scope_unknown"
    assert any(p.kind == PendingKind.SCOPE_UNKNOWN and p.subject == "registration"
               for p in loose.pending_validation)  # fmt: skip
    # Un altre emissor no es el registre que es demana; un altre tipus, tampoc.
    other = snap(registrations=[reg(tuple(lines), issuer="Un altre organisme")])
    assert evaluate_registration(other, "sector_operator_registry", lines, "CNMC").reason == (
        "no_current_data"
    )
    assert evaluate_registration(full, "rolece").result == EvaluationResult.UNKNOWN


# --------------------------------------------------------------------------- #
# Perfils i equips                                                              #
# --------------------------------------------------------------------------- #


def test_one_person_can_cover_several_profiles_and_unavailable_people_do_not_count():
    s = snap(people=[
        PersonIn(prov("a"), "available", ("network_security_specialist", "project_manager")),
        PersonIn(prov("b"), "unavailable", ("voip_specialist",)),
    ])  # fmt: skip
    both = {"project_manager": 1, "network_security_specialist": 1}
    assert evaluate_profiles(s, both).result == EvaluationResult.SATISFIES
    missing = evaluate_profiles(s, {"voip_specialist": 1})
    assert (missing.result, missing.reason) == (EvaluationResult.UNKNOWN, "profiles_missing")
    assert evaluate_profiles(s, {"project_manager": 2}).result == EvaluationResult.UNKNOWN
    # Sense perfils estructurats, la persona no cobreix res.
    assert snap(people=[PersonIn(prov("c"), "available", None)]).profiles == {}


def unit(record_id, kind, until, required=True, lifecycle=KnowledgeLifecycle.CURRENT):
    return EquipmentIn(prov(record_id, lifecycle=lifecycle), kind,
                       calibration_required=required, calibration_valid_until=until)  # fmt: skip


def test_equipment_calibration_valid_expired_or_unknown():
    ok = snap(equipment=[unit("o", "otdr", TODAY + timedelta(days=30)),
                         unit("l", "olts", TODAY)])  # fmt: skip
    need = {"otdr": 1, "olts": 1}
    assert (
        evaluate_equipment(ok, need, calibration_required=True).result == EvaluationResult.SATISFIES
    )
    expired = snap(equipment=[unit("o", "otdr", TODAY - timedelta(days=1)),
                              unit("l", "olts", TODAY + timedelta(days=30))])  # fmt: skip
    ev = evaluate_equipment(expired, need, calibration_required=True)
    assert (ev.result, ev.reason) == (EvaluationResult.DOES_NOT_SATISFY, "calibration_expired")
    undated = snap(equipment=[unit("o", "otdr", None, required=None), unit("l", "olts", None)])
    assert evaluate_equipment(undated, need, calibration_required=True).reason == (
        "calibration_unknown"
    )
    # Sense exigencia de calibratge, l'equip compta; retirat, no.
    assert evaluate_equipment(undated, need, calibration_required=False).result == (
        EvaluationResult.SATISFIES
    )
    retired = snap(equipment=[unit("o", "otdr", None, lifecycle=KnowledgeLifecycle.RETIRED)])
    assert evaluate_equipment(retired, {"otdr": 1}, calibration_required=False).reason == (
        "equipment_missing"
    )


# --------------------------------------------------------------------------- #
# Fets quantitatius                                                             #
# --------------------------------------------------------------------------- #


def test_a_numeric_fact_is_compared_against_a_threshold():
    s = snap(facts=[FactIn(prov("f"), "ipv4_assignable_count", True, value_number=D(20))])
    assert evaluate_numeric_fact(s, "ipv4_assignable_count", "gte", D(20)).result == (
        EvaluationResult.SATISFIES
    )
    assert evaluate_numeric_fact(s, "ipv4_assignable_count", "gte", D(25)).result == (
        EvaluationResult.DOES_NOT_SATISFY
    )
    none = snap(facts=[FactIn(prov("f"), "ipv4_assignable_count", False)])
    assert evaluate_numeric_fact(none, "ipv4_assignable_count", "gte", D(1)).result == (
        EvaluationResult.DOES_NOT_SATISFY
    )
    assert evaluate_numeric_fact(snap(), "ipv4_assignable_count", "gte", D(1)).result == (
        EvaluationResult.UNKNOWN
    )
