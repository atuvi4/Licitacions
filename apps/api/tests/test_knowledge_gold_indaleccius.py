"""Gold del coneixement amb les dades REALS d'una empresa pilot (Indaleccius).

El document es local i fora de git (`KNOWLEDGE_GOLD_INDALECCIUS` o
`var/gold/indaleccius-knowledge.json`); si no hi es, el test se salta. Conte:

* ACTUAL: nomes el que l'empresa ha declarat avui (ENS Mitja, sense evidencia);
* HISTORIC: les licitacions analitzades (nomes dades publiques i la decisio) i
  les observacions que en surten (3CX, FreePBX, CPD Tier-3, NRN, 24x7,
  productes i socis), totes `candidate`.

El que es protegeix: que res de l'historic es converteix sol en coneixement
actual, i que la fotografia diu exactament que falta validar. Cap cost, marge ni
tarifa: el document es refusaria.

Cap nom de l'empresa no surt dels models ni dels vocabularis: s'importa sobre
una empresa de test qualsevol.
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import pytest

from app.knowledge.snapshot import (
    EvaluationResult,
    KnowledgeInputs,
    KnowledgeSource,
    PendingKind,
    build_snapshot,
    evaluate_capability,
    evaluate_certification,
    evaluate_fact,
)
from app.services import company_knowledge

API_ROOT = Path(__file__).resolve().parents[1]
GOLD = Path(
    os.environ.get("KNOWLEDGE_GOLD_INDALECCIUS")
    or API_ROOT / "var" / "gold" / "indaleccius-knowledge.json"
)


@pytest.fixture(autouse=True)
async def vocabulary(db):
    from app.knowledge.vocabulary import seed_vocabulary

    await seed_vocabulary(db)
    await db.commit()


@pytest.mark.skipif(not GOLD.is_file(), reason="el document de coneixement pilot no hi es")
async def test_the_pilot_company_shows_what_is_left_to_validate(db, tenant_a):
    _, company = tenant_a
    document = json.loads(GOLD.read_text(encoding="utf-8"))
    counts = await company_knowledge.import_knowledge(db, company.id, document)
    await db.commit()
    assert counts["certifications"] == 1
    assert counts["operating_facts"] == 0
    assert counts["observations"] >= 20

    inputs = await company_knowledge.load_inputs(db, company.id)
    snapshot = build_snapshot(inputs, on=date(2026, 9, 30))

    # ACTUAL: ENS Mitja declarat, evidencia pendent. L'historic "no en tenien" no mana.
    ens = snapshot.certifications["ENS"]
    assert (ens.effective.value, ens.source, ens.accreditation_pending) == (
        "medium",
        KnowledgeSource.CURRENT_DECLARED,
        True,
    )
    assert ens.hints, "l'historic de l'ENS s'ha de veure com a pista"
    assert evaluate_certification(snapshot, "ENS", "medium").result == EvaluationResult.SATISFIES
    assert evaluate_certification(snapshot, "ENS", "high").result == (
        EvaluationResult.DOES_NOT_SATISFY
    )
    # "No tenien ISO" a l'historic: desconegut, no fals.
    assert evaluate_certification(snapshot, "ISO_9001").result == EvaluationResult.UNKNOWN

    # HISTORIC: res no es actual fins que una persona ho validi.
    for key in (
        "telecom_operator_cnmc",
        "own_number_range_nrn",
        "datacenter_tier3",
        "support_24x7",
        "own_ftth_infrastructure",
    ):
        evaluation = evaluate_fact(snapshot, key)
        assert (evaluation.result, evaluation.source) == (
            EvaluationResult.UNKNOWN,
            KnowledgeSource.HISTORIC,
        ), key
    for name in ("3CX", "FreePBX"):
        assert snapshot.solutions[name].claims == (), name
        assert snapshot.solutions[name].source == KnowledgeSource.HISTORIC
    queues = evaluate_capability(snapshot, "pbx.queues")
    assert (queues.result, queues.source) == (EvaluationResult.UNKNOWN, KnowledgeSource.HISTORIC)
    assert {h.statement.split(":")[0] for h in queues.hints} >= {"Centraleta 3CX"}
    assert all(entry.claims == () for entry in snapshot.products.values())
    assert all(entry.claims == () for entry in snapshot.partners.values())
    assert {"InaTIC", "Templus", "InstantByte"} <= set(snapshot.partners)

    # Que falta validar: l'evidencia de l'ENS i cada suggeriment de l'historic.
    pending = snapshot.pending_validation
    assert [(p.kind, p.key) for p in pending if p.kind == PendingKind.ACCREDITATION_PENDING] == [
        (PendingKind.ACCREDITATION_PENDING, "ENS")
    ]
    # L'abast de l'ENS no esta estructurat: no se sap quines linies cobreix.
    assert ens.effective.scope_status == "scope_unknown"
    assert (PendingKind.SCOPE_UNKNOWN, "ENS") in [(p.kind, p.key) for p in pending]
    ens_voice = evaluate_certification(snapshot, "ENS", "medium", service_lines=["voice_pbx"])
    assert (ens_voice.result, ens_voice.reason) == (EvaluationResult.UNKNOWN, "scope_unknown")
    candidates = {(p.subject, p.key) for p in pending if p.kind == PendingKind.HISTORIC_CANDIDATE}
    assert {
        ("solution", "3CX"),
        ("solution", "FreePBX"),
        ("operating_fact", "datacenter_tier3"),
        ("operating_fact", "own_number_range_nrn"),
        ("operating_fact", "support_24x7"),
        ("operating_fact", "telecom_operator_cnmc"),
        ("partner", "InaTIC"),
        ("partner", "Templus"),
        ("product", "Yealink T46U"),
    } <= candidates
    ens_candidate = next(
        p for p in pending if p.kind == PendingKind.HISTORIC_CANDIDATE and p.key == "ENS"
    )
    assert ens_candidate.superseded_by_current is True

    # L'empremta nomes depen del coneixement actual.
    only_current = build_snapshot(
        KnowledgeInputs(company_id=inputs.company_id, certifications=inputs.certifications),
        on=snapshot.evaluated_on,
    )
    assert snapshot.fingerprint == only_current.fingerprint


@pytest.mark.skipif(not GOLD.is_file(), reason="el document de coneixement pilot no hi es")
def test_the_pilot_document_carries_no_confidential_economics():
    document = json.loads(GOLD.read_text(encoding="utf-8"))
    assert company_knowledge._confidential_keys(document) == set()
