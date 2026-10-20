"""Prova diferencial v4 -> v5 a la CI: el que la v4 llegia dona el mateix `RuleOutcome`.

`fixtures/compliance_v4_outcomes.json.gz` (≈ 213 KB comprimit, ≈ 5,3 MB de JSON)
conte 1766 entrades distintes (de 2316 avaluacions enregistrades) de les proves de
compliment, requisits, avaluacio (gold), candidatura, dossier i promocio: el motor
(perfils v1, v2, v3, cap, i product-v2 amb `value_schema`) i les set regles cridades
directament. Cada entrada porta l'ENTRADA (requisit, perfil d'empresa, termini,
expedient, amb els tipus etiquetats) i el `RuleOutcome` que en va donar el CODI de
`21d5f9e` (company-compliance-v4), calculat amb aquell codi en un proces a part
(`validacio\\pas1\\evidence\\r1_differential\\build_fixture.py`).

Exclou nomes el que el complement `product-v2` normalitza (format lliure), que es
justament el que la v5 canvia a proposit. Es una prova de no-regressio sobre les
entrades que exerciten les proves, no una prova exhaustiva de totes les entrades
possibles.
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.compliance.certifications as certifications
import app.compliance.economic as economic
import app.compliance.engine as engine
import app.compliance.experience as experience
import app.compliance.insurance as insurance
import app.compliance.personnel as personnel
from app.compliance.profile import DocumentRef, ProfileSnapshot
from app.models import enums

FIXTURE = Path(__file__).parent / "fixtures" / "compliance_v4_outcomes.json.gz"

FUNCTIONS = {
    "app.compliance.economic.assess_economic_solvency": economic.assess_economic_solvency,
    "app.compliance.insurance.assess_insurance": insurance.assess_insurance,
    "app.compliance.experience.assess_experience": experience.assess_experience,
    "app.compliance.certifications.assess_certification": certifications.assess_certification,
    "app.compliance.personnel.assess_personnel": personnel.assess_personnel,
    "app.compliance.personnel.assess_qualification": personnel.assess_qualification,
    "app.compliance.personnel.assess_registration": personnel.assess_registration,
}


def dec(value):
    if isinstance(value, list):
        return [dec(item) for item in value]
    if not isinstance(value, dict):
        return value
    if "$enum" in value:
        return getattr(enums, value["$enum"])(value["v"])
    if "$dec" in value:
        return Decimal(value["$dec"])
    if "$dt" in value:
        return datetime.fromisoformat(value["$dt"])
    if "$date" in value:
        return date.fromisoformat(value["$date"])
    if "$uuid" in value:
        return uuid.UUID(value["$uuid"])
    if "$tuple" in value:
        return tuple(dec(item) for item in value["$tuple"])
    if "$map" in value:
        return {dec(k): dec(v) for k, v in value["$map"]}
    if "$docref" in value:
        return DocumentRef(**{k: dec(v) for k, v in value["$docref"].items()})
    if "$snap" in value:
        return ProfileSnapshot(**{k: dec(v) for k, v in value["$snap"].items()})
    if "$ns" in value:
        return SimpleNamespace(**{k: dec(v) for k, v in value["$ns"].items()})
    return {k: dec(v) for k, v in value.items()}


@cache
def entries() -> list[dict]:
    with gzip.open(FIXTURE, "rb") as handle:
        return json.loads(handle.read())["entries"]


def key(outcome):
    return {
        "result": outcome.result,
        "rule_code": outcome.rule_code,
        "explanation_code": outcome.explanation_code,
        "params": outcome.params,
        "mode": outcome.mode,
        "company_value": outcome.company_value,
        "strength": outcome.strength,
        "evidence": [
            (e.kind, e.entity_id, e.label, e.strength, e.document_id) for e in outcome.evidence
        ],
    }


def dump(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False))


def test_el_fixture_cobreix_el_motor_i_les_set_regles():
    kinds = {entry["kind"] for entry in entries()}
    assert kinds == {"engine.assess_requirement", *FUNCTIONS}
    profiles = {entry["profile"] for entry in entries() if entry["kind"].startswith("engine")}
    assert {"v1", "v2", "v3", None, "product-v2"} <= profiles
    assert len(entries()) >= 1700


@pytest.mark.parametrize("chunk", range(8))
def test_cada_entrada_dona_el_mateix_rule_outcome_que_la_v4(chunk):
    differences = []
    selected = entries()[chunk::8]
    for entry in selected:
        requirement = dec(entry["requirement"])
        args = [dec(arg) for arg in entry["args"]]
        kwargs = {k: dec(v) for k, v in entry["kwargs"].items()}
        if entry["kind"] == "engine.assess_requirement":
            outcome = engine.assess_requirement(
                requirement, *args, profile=entry["profile"], **kwargs
            )
        else:
            outcome = FUNCTIONS[entry["kind"]](requirement, *args, **kwargs)
        if dump(key(outcome)) != entry["v4"]:
            differences.append((entry["kind"], entry["profile"], entry["v4"], dump(key(outcome))))
    assert not differences, differences[:3]
    assert selected


def test_cap_entrada_del_fixture_no_entra_al_complement_product_v2():
    from app.compliance import normalize

    for entry in entries():
        if entry["kind"] == "engine.assess_requirement":
            assert not normalize.applies(dec(entry["requirement"]), entry["profile"])


def _never_used() -> None:  # pragma: no cover - documenta els tipus etiquetats
    _ = (dataclasses, enum)
