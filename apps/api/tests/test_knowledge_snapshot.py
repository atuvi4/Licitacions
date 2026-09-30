"""La fotografia del coneixement: precedencia, vigencia, historic i empremta.

Funcio pura: aquests tests no toquen cap base de dades. Cada regla es prova
sola. El cas que les motiva es el d'una empresa que l'any passat "no tenia ENS"
i avui declara ENS Mitja.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.knowledge.snapshot import (
    CapabilityIn,
    CertificationIn,
    EvaluationResult,
    FactIn,
    KnowledgeInputs,
    KnowledgeSource,
    ObservationIn,
    PartnerIn,
    PartnerRoleIn,
    PendingKind,
    Provenance,
    ServiceIn,
    SolutionIn,
    build_snapshot,
    evaluate_capability,
    evaluate_certification,
    evaluate_fact,
)
from app.models.enums import (
    CapabilityStatus,
    KnowledgeLifecycle,
    KnowledgeOrigin,
    ObservationStatus,
    ObservationSubject,
    VerificationStatus,
)

TODAY = date(2026, 9, 30)


def prov(
    record_id: str,
    *,
    verified: bool = False,
    lifecycle: KnowledgeLifecycle = KnowledgeLifecycle.CURRENT,
    as_of: date = date(2026, 9, 29),
    valid_until: date | None = None,
    origin: KnowledgeOrigin = KnowledgeOrigin.DECLARED,
    evidence: bool | None = None,
) -> Provenance:
    """`verified` porta evidencia si no es diu el contrari: es l'unic estat valid."""
    return Provenance(
        record_id=record_id,
        origin=origin,
        as_of=as_of,
        valid_until=valid_until,
        verification=VerificationStatus.VERIFIED
        if verified
        else VerificationStatus.PENDING_VERIFICATION,
        lifecycle=lifecycle,
        has_evidence=verified if evidence is None else evidence,
    )


def ens(
    record_id: str = "c1", level: str = "medium", scope: tuple[str, ...] | None = None, **kwargs
) -> CertificationIn:
    return CertificationIn(
        prov=prov(record_id, **kwargs),
        name=f"ENS {level}",
        standard_code="ENS",
        level=level,
        scope=scope,
    )


def observation(
    obs_id: str,
    subject: ObservationSubject,
    key: str,
    statement: str,
    *,
    proposal: dict | None = None,
    status: ObservationStatus = ObservationStatus.CANDIDATE,
) -> ObservationIn:
    return ObservationIn(
        observation_id=obs_id,
        subject=subject,
        subject_key=key,
        statement=statement,
        status=status,
        proposal=proposal or {},
        observed_on=date(2025, 6, 1),
        source_ref="carpeta 2025",
    )


NO_ENS = observation(
    "o-ens",
    ObservationSubject.CERTIFICATION,
    "ENS",
    "No tenien ENS (2025)",
    proposal={"standard_code": "ENS", "held": False},
)


def snap(**kwargs) -> object:
    return build_snapshot(KnowledgeInputs(company_id="empresa-a", **kwargs), on=TODAY)


# --------------------------------------------------------------------------- #
# El cas obligatori: ENS                                                       #
# --------------------------------------------------------------------------- #


def test_current_declared_ens_wins_over_the_historic_no():
    snapshot = snap(certifications=[ens()], observations=[NO_ENS])

    entry = snapshot.certifications["ENS"]
    assert (entry.effective.value, entry.source, entry.accreditation_pending) == (
        "medium",
        KnowledgeSource.CURRENT_DECLARED,
        True,
    )
    # L'historic es conserva com a pista, pero no mana.
    assert [h.statement for h in entry.hints] == ["No tenien ENS (2025)"]
    candidate = next(
        p for p in snapshot.pending_validation if p.kind == PendingKind.HISTORIC_CANDIDATE
    )
    assert candidate.superseded_by_current is True


def test_once_verified_the_source_is_current_verified():
    snapshot = snap(certifications=[ens(verified=True)], observations=[NO_ENS])
    entry = snapshot.certifications["ENS"]
    assert (entry.source, entry.accreditation_pending) == (KnowledgeSource.CURRENT_VERIFIED, False)
    assert not [
        p for p in snapshot.pending_validation if p.kind == PendingKind.ACCREDITATION_PENDING
    ]


@pytest.mark.parametrize(
    ("required", "result"),
    [
        ("basic", EvaluationResult.SATISFIES),
        ("medium", EvaluationResult.SATISFIES),
        ("high", EvaluationResult.DOES_NOT_SATISFY),
        (None, EvaluationResult.SATISFIES),
    ],
)
def test_ens_medium_satisfies_medium_and_basic_but_not_high(required, result):
    evaluation = evaluate_certification(snap(certifications=[ens()]), "ENS", required)
    assert evaluation.result == result
    if result == EvaluationResult.SATISFIES:
        assert (evaluation.source, evaluation.accreditation_pending) == (
            KnowledgeSource.CURRENT_DECLARED,
            True,
        )


def test_verified_beats_declared_when_both_exist():
    """Un ENS basic verificat i un ENS mitja declarat: cadascun respon el que pot."""
    snapshot = snap(certifications=[ens("c-basic", "basic", verified=True), ens("c-medium")])

    assert snapshot.certifications["ENS"].effective.record_id == "c-basic"
    basic = evaluate_certification(snapshot, "ENS", "basic")
    assert (basic.source, basic.accreditation_pending) == (KnowledgeSource.CURRENT_VERIFIED, False)
    medium = evaluate_certification(snapshot, "ENS", "medium")
    assert (medium.result, medium.source, medium.accreditation_pending) == (
        EvaluationResult.SATISFIES,
        KnowledgeSource.CURRENT_DECLARED,
        True,
    )
    assert medium.record_ids == ("c-medium",)


# --------------------------------------------------------------------------- #
# Absencia i historic                                                          #
# --------------------------------------------------------------------------- #


def test_no_data_is_unknown_not_false():
    evaluation = evaluate_certification(snap(), "ISO_9001")
    assert (evaluation.result, evaluation.source, evaluation.reason) == (
        EvaluationResult.UNKNOWN,
        KnowledgeSource.UNKNOWN,
        "no_current_data",
    )


def test_a_historic_no_is_still_unknown_not_false():
    no_iso = observation(
        "o-iso",
        ObservationSubject.CERTIFICATION,
        "ISO_9001",
        "No tenien ISO 9001",
        proposal={"standard_code": "ISO_9001", "held": False},
    )
    evaluation = evaluate_certification(snap(observations=[no_iso]), "ISO_9001")
    assert (evaluation.result, evaluation.source, evaluation.reason) == (
        EvaluationResult.UNKNOWN,
        KnowledgeSource.HISTORIC,
        "historic_only",
    )
    assert [h.statement for h in evaluation.hints] == ["No tenien ISO 9001"]


def test_a_historic_yes_can_never_satisfy():
    tier3 = observation(
        "o-tier3",
        ObservationSubject.OPERATING_FACT,
        "datacenter_tier3",
        "CPD Tier-3 propi (2025)",
        proposal={"key": "datacenter_tier3", "value": True},
    )
    solution = observation(
        "o-pbx",
        ObservationSubject.SOLUTION,
        "Centraleta X",
        "Van oferir la centraleta X",
        proposal={"name": "Centraleta X", "service_line_code": "voice_pbx"},
    )
    snapshot = snap(observations=[tier3, solution])

    fact = evaluate_fact(snapshot, "datacenter_tier3")
    assert (fact.result, fact.source) == (EvaluationResult.UNKNOWN, KnowledgeSource.HISTORIC)
    queues = evaluate_capability(snapshot, "pbx.queues")
    assert (queues.result, queues.source) == (EvaluationResult.UNKNOWN, KnowledgeSource.HISTORIC)
    assert [h.statement for h in queues.hints] == ["Van oferir la centraleta X"]
    assert snapshot.solutions["Centraleta X"].claims == ()


def test_reviewed_observations_are_no_longer_hints():
    promoted = replace(NO_ENS, observation_id="o2", status=ObservationStatus.PROMOTED)
    dismissed = replace(NO_ENS, observation_id="o3", status=ObservationStatus.DISMISSED)
    snapshot = snap(observations=[promoted, dismissed])
    assert "ENS" not in snapshot.certifications
    assert snapshot.pending_validation == ()


def test_a_current_no_is_a_real_no():
    """Una dada actual negativa si que respon: no es el mateix que no saber-ho."""
    no_ftth = FactIn(prov=prov("f1"), key="own_ftth_infrastructure", value=False)
    evaluation = evaluate_fact(snap(facts=[no_ftth]), "own_ftth_infrastructure")
    assert (evaluation.result, evaluation.source) == (
        EvaluationResult.DOES_NOT_SATISFY,
        KnowledgeSource.CURRENT_DECLARED,
    )


# --------------------------------------------------------------------------- #
# Vigencia i cicle de vida                                                     #
# --------------------------------------------------------------------------- #


def test_an_expired_record_does_not_count_and_is_reported():
    expired = ens(verified=True, valid_until=date(2026, 9, 1))
    snapshot = snap(certifications=[expired])

    assert "ENS" not in snapshot.certifications
    assert evaluate_certification(snapshot, "ENS", "medium").result == EvaluationResult.UNKNOWN
    assert [(p.kind, p.key) for p in snapshot.pending_validation] == [(PendingKind.EXPIRED, "ENS")]
    # El mateix registre, mirat abans de caducar, si que compta.
    earlier = build_snapshot(
        KnowledgeInputs(company_id="empresa-a", certifications=[expired]), on=date(2026, 8, 1)
    )
    assert earlier.certifications["ENS"].source == KnowledgeSource.CURRENT_VERIFIED


def test_the_last_valid_day_still_counts():
    snapshot = snap(certifications=[ens(valid_until=TODAY)])
    assert snapshot.certifications["ENS"].effective is not None


def test_retired_does_not_count_and_in_progress_is_a_question():
    snapshot = snap(
        certifications=[
            ens("c-old", "basic", verified=True, lifecycle=KnowledgeLifecycle.RETIRED),
            ens("c-new", "high", lifecycle=KnowledgeLifecycle.IN_PROGRESS),
        ]
    )
    assert "ENS" not in snapshot.certifications
    assert [(p.kind, p.record_id) for p in snapshot.pending_validation] == [
        (PendingKind.IN_PROGRESS, "c-new")
    ]


def test_a_retired_solution_takes_its_capabilities_with_it():
    solution = SolutionIn(
        prov=prov("s1", lifecycle=KnowledgeLifecycle.RETIRED),
        name="PBX antiga",
        service_line_code="voice_pbx",
    )
    queues = CapabilityIn(
        prov=prov("k1", verified=True),
        solution_id="s1",
        capability_code="pbx.queues",
        status=CapabilityStatus.SUPPORTED,
    )
    snapshot = snap(solutions=[solution], capabilities=[queues])
    assert evaluate_capability(snapshot, "pbx.queues").result == EvaluationResult.UNKNOWN


def test_a_partner_can_play_the_same_role_on_two_lines():
    """Operador majorista de FTTH i de mobil alhora: un partner, dos papers."""
    partner = PartnerIn(prov=prov("p1"), name="Operador Demo")
    ftth = PartnerRoleIn(
        prov=prov("r1"), partner_id="p1", role="wholesale_operator", service_line_code="access_ftth"
    )
    mobile = PartnerRoleIn(
        prov=prov("r2"),
        partner_id="p1",
        role="wholesale_operator",
        service_line_code="mobile_telephony",
    )
    both = snap(partners=[partner], partner_roles=[ftth, mobile])
    assert list(both.partners) == ["Operador Demo"]
    assert both.partners["Operador Demo"].effective.value["roles"] == [
        {"role": "wholesale_operator", "line": "access_ftth", "territory": None},
        {"role": "wholesale_operator", "line": "mobile_telephony", "territory": None},
    ]
    only_ftth = snap(partners=[partner], partner_roles=[ftth])
    assert both.fingerprint != only_ftth.fingerprint


def test_mobile_telephony_is_a_service_line_like_any_other_in_the_snapshot():
    mobile = ServiceIn(
        prov=prov("s1"),
        name="Linies mobils",
        service_line_code="mobile_telephony",
        delivery_mode="resale",
    )
    snapshot = snap(services=[mobile])
    entry = snapshot.services["mobile_telephony"]
    assert entry.source == KnowledgeSource.CURRENT_DECLARED
    assert entry.effective.value == {"name": "Linies mobils", "mode": "resale"}
    as_ftth = snap(services=[replace(mobile, service_line_code="access_ftth")])
    assert list(as_ftth.services) == ["access_ftth"]
    assert as_ftth.fingerprint != snapshot.fingerprint
    assert snap(services=[mobile]).fingerprint == snapshot.fingerprint


def test_a_retired_partner_takes_its_roles_with_it():
    partner = PartnerIn(prov=prov("p1", lifecycle=KnowledgeLifecycle.RETIRED), name="Soci")
    role = PartnerRoleIn(
        prov=prov("r1"), partner_id="p1", role="subcontractor", service_line_code="civil_works"
    )
    snapshot = snap(partners=[partner], partner_roles=[role])
    assert snapshot.partners == {}
    assert snapshot.fingerprint == snap().fingerprint


# --------------------------------------------------------------------------- #
# Capacitats                                                                   #
# --------------------------------------------------------------------------- #


def _pbx(*capabilities: CapabilityIn) -> object:
    solution = SolutionIn(prov=prov("s1"), name="PBX", service_line_code="voice_pbx")
    return snap(solutions=[solution], capabilities=list(capabilities))


def _cap(code: str, status=CapabilityStatus.SUPPORTED, **values) -> CapabilityIn:
    return CapabilityIn(
        prov=prov(f"k-{code}"), solution_id="s1", capability_code=code, status=status, **values
    )


def test_numbers_compare_with_the_comparator_of_the_vocabulary():
    snapshot = _pbx(
        _cap("pbx.concurrent_calls", value_number=Decimal(32)),
        _cap("ops.sla_restore_hours", value_number=Decimal(4)),
    )
    assert evaluate_capability(snapshot, "pbx.concurrent_calls", 20).result == (
        EvaluationResult.SATISFIES
    )
    assert evaluate_capability(snapshot, "pbx.concurrent_calls", 50).result == (
        EvaluationResult.DOES_NOT_SATISFY
    )
    # lte: restablir en 4 h compleix un maxim de 4 h, no un de 2 h.
    assert evaluate_capability(snapshot, "ops.sla_restore_hours", 4).result == (
        EvaluationResult.SATISFIES
    )
    assert evaluate_capability(snapshot, "ops.sla_restore_hours", 2).result == (
        EvaluationResult.DOES_NOT_SATISFY
    )


def test_ordinal_scales_and_missing_values():
    snapshot = _pbx(
        _cap("switch.poe_standard", value_level="at"),
        _cap("pbx.extra_softphones_free"),  # suportat pero sense quantitat
    )
    assert evaluate_capability(snapshot, "switch.poe_standard", "af").result == (
        EvaluationResult.SATISFIES
    )
    assert evaluate_capability(snapshot, "switch.poe_standard", "bt").result == (
        EvaluationResult.DOES_NOT_SATISFY
    )
    missing = evaluate_capability(snapshot, "pbx.extra_softphones_free", 10)
    assert (missing.result, missing.reason) == (EvaluationResult.UNKNOWN, "current_value_missing")
    with pytest.raises(ValueError):
        evaluate_capability(snapshot, "pbx.concurrent_calls")


def test_not_supported_and_with_limits():
    snapshot = _pbx(
        _cap("pbx.hot_desking", status=CapabilityStatus.NOT_SUPPORTED),
        _cap("pbx.queue_callback_keep_position", status=CapabilityStatus.SUPPORTED_WITH_LIMITS),
    )
    assert evaluate_capability(snapshot, "pbx.hot_desking").result == (
        EvaluationResult.DOES_NOT_SATISFY
    )
    limited = evaluate_capability(snapshot, "pbx.queue_callback_keep_position")
    assert (limited.result, limited.reason) == (EvaluationResult.SATISFIES, "satisfied_with_limits")


# --------------------------------------------------------------------------- #
# Empremta                                                                     #
# --------------------------------------------------------------------------- #


def test_the_fingerprint_does_not_depend_on_order():
    facts = [
        FactIn(prov=prov("f1"), key="support_24x7", value=True),
        FactIn(prov=prov("f2"), key="datacenter_tier3", value=True, detail="CPD"),
    ]
    assert (
        snap(certifications=[ens()], facts=facts).fingerprint
        == snap(certifications=[ens()], facts=list(reversed(facts))).fingerprint
    )


def test_changing_current_knowledge_changes_the_fingerprint():
    base = snap(certifications=[ens()])
    assert snap(certifications=[ens(level="high")]).fingerprint != base.fingerprint
    assert snap(certifications=[ens(verified=True)]).fingerprint != base.fingerprint
    assert snap(certifications=[ens(valid_until=date(2027, 1, 1))]).fingerprint != (
        base.fingerprint
    )


def test_changing_history_does_not_change_the_fingerprint_nor_satisfy_anything():
    base = snap()
    with_history = snap(
        observations=[
            NO_ENS,
            observation(
                "o-yes",
                ObservationSubject.CERTIFICATION,
                "ENS",
                "Tenien ENS Alt?",
                proposal={"standard_code": "ENS", "level": "high"},
            ),
        ]
    )
    assert with_history.fingerprint == base.fingerprint
    assert evaluate_certification(with_history, "ENS", "basic").result == (EvaluationResult.UNKNOWN)


def test_what_is_still_to_validate_is_listed_in_a_stable_order():
    snapshot = snap(
        certifications=[ens()],
        facts=[FactIn(prov=prov("f1"), key="support_24x7", value=True)],
        observations=[NO_ENS],
    )
    assert [(p.kind, p.subject, p.key) for p in snapshot.pending_validation] == [
        (PendingKind.ACCREDITATION_PENDING, "certification", "ENS"),
        (PendingKind.ACCREDITATION_PENDING, "operating_fact", "support_24x7"),
        (PendingKind.SCOPE_UNKNOWN, "certification", "ENS"),
        (PendingKind.HISTORIC_CANDIDATE, "certification", "ENS"),
    ]


# --------------------------------------------------------------------------- #
# Verificat vol dir amb evidencia                                              #
# --------------------------------------------------------------------------- #


def test_verified_without_evidence_counts_as_declared():
    """Dades d'abans de la regla: un `verified` sense document no es creu."""
    snapshot = snap(certifications=[ens(verified=True, evidence=False)])
    entry = snapshot.certifications["ENS"]
    assert (entry.source, entry.accreditation_pending) == (
        KnowledgeSource.CURRENT_DECLARED,
        True,
    )
    pending = next(
        p for p in snapshot.pending_validation if p.kind == PendingKind.ACCREDITATION_PENDING
    )
    assert pending.detail == "Marcat verificat pero sense cap evidencia enllacada"
    evaluation = evaluate_certification(snapshot, "ENS", "medium")
    assert (evaluation.source, evaluation.accreditation_pending) == (
        KnowledgeSource.CURRENT_DECLARED,
        True,
    )


def test_evidence_changes_the_fingerprint():
    assert (
        snap(certifications=[ens(verified=True)]).fingerprint
        != snap(certifications=[ens(verified=True, evidence=False)]).fingerprint
    )


# --------------------------------------------------------------------------- #
# Abast estructurat                                                            #
# --------------------------------------------------------------------------- #

VOICE = ("voice_pbx", "voice_trunk")


def test_a_structured_scope_answers_coverage():
    snapshot = snap(certifications=[ens(scope=VOICE)])
    claim = snapshot.certifications["ENS"].effective
    assert (claim.scope, claim.scope_status) == (VOICE, "structured")
    assert not [p for p in snapshot.pending_validation if p.kind == PendingKind.SCOPE_UNKNOWN]

    covered = evaluate_certification(snapshot, "ENS", "medium", service_lines=["voice_pbx"])
    assert (covered.result, covered.accreditation_pending) == (EvaluationResult.SATISFIES, True)
    outside = evaluate_certification(
        snapshot, "ENS", "medium", service_lines=["voice_pbx", "dedicated_network"]
    )
    assert (outside.result, outside.reason) == (
        EvaluationResult.DOES_NOT_SATISFY,
        "scope_not_covered",
    )


def test_an_unstructured_scope_is_scope_unknown_and_never_inferred():
    snapshot = snap(certifications=[ens()])  # sense abast estructurat
    claim = snapshot.certifications["ENS"].effective
    assert (claim.scope, claim.scope_status) == (None, "scope_unknown")
    assert [
        (p.kind, p.key) for p in snapshot.pending_validation if p.kind == PendingKind.SCOPE_UNKNOWN
    ] == [(PendingKind.SCOPE_UNKNOWN, "ENS")]

    evaluation = evaluate_certification(snapshot, "ENS", "medium", service_lines=["voice_pbx"])
    assert (evaluation.result, evaluation.reason) == (EvaluationResult.UNKNOWN, "scope_unknown")
    # Sense demanar abast, el nivell continua responent.
    assert evaluate_certification(snapshot, "ENS", "medium").result == EvaluationResult.SATISFIES


def test_level_is_checked_before_scope():
    snapshot = snap(certifications=[ens(level="basic")])
    evaluation = evaluate_certification(snapshot, "ENS", "medium", service_lines=["voice_pbx"])
    assert (evaluation.result, evaluation.reason) == (
        EvaluationResult.DOES_NOT_SATISFY,
        "level_below",
    )


def test_a_verified_certificate_covering_the_lines_wins():
    snapshot = snap(
        certifications=[
            ens("c-declared", scope=("voice_pbx",)),
            ens("c-verified", scope=VOICE, verified=True),
        ]
    )
    evaluation = evaluate_certification(snapshot, "ENS", "basic", service_lines=VOICE)
    assert (evaluation.source, evaluation.record_ids) == (
        KnowledgeSource.CURRENT_VERIFIED,
        ("c-verified",),
    )


def test_scope_changes_the_fingerprint():
    assert (
        snap(certifications=[ens()]).fingerprint
        != snap(certifications=[ens(scope=VOICE)]).fingerprint
    )
