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

v6 (`company-compliance-v6`, Bloc 1): la v6 canvia A PROPOSIT algunes lectures que
la v4 feia per a tots els perfils. Cada transicio volguda (regla, motiu i resultat
de la v4 -> els de la v6) es a `V6_INTENDED`, amb el motiu; qualsevol altra
diferencia continua fallant, i `test_les_divergencies_v6_son_exactament_les_volgudes`
fixa quantes n'hi ha (una de nova o una que desapareix fa fallar la prova).
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


#: Transicions volgudes de la v6: (regla, motiu, resultat) de la v4 -> de la v6.
_ENS = "ens_category_gte_v1"
_SET = "certification_set_v1"
_CERT = "certification_exact_v1"
_REG = "registration_exact_v1"
_ANY = "registration_any_of_v1"
_CLASS = "business_classification_gte_v1"
_SAT, _DNS, _UNK, _REV = "satisfies", "does_not_satisfy", "unknown", "needs_review"
_CDI = "CERTIFICATION_DATA_INCOMPLETE"
_CNF = "CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE"
_CER = "CERTIFICATION_EQUIVALENCE_REVIEW"
_CEND = "CERTIFICATION_EQUIVALENT_NOT_DECLARED"
_RNF = "REGISTRATION_NOT_FOUND_PROFILE_COMPLETE"
_RDI = "REGISTRATION_DATA_INCOMPLETE"
_RCR = "REGISTRATION_CLASSIFICATION_REVIEW"
V6_INTENDED = {
    # ENS per categories en tots els perfils: el mateix veredicte amb la regla nova,
    # una categoria inferior es «inferior» (abans «no trobada»), i casa la mateixa
    # categoria encara que el nom no sigui identic (abans un fals «no compleix»).
    ((_CERT, _CDI, _UNK), (_ENS, _CDI, _UNK)),
    ((_CERT, _CNF, _DNS), (_ENS, _CNF, _DNS)),
    ((_CERT, _CNF, _DNS), (_ENS, "CERTIFICATION_LEVEL_BELOW", _DNS)),
    ((_CERT, _CNF, _DNS), (_ENS, "CERTIFICATION_FOUND_NO_EXPIRY", _REV)),
    ((_CERT, _CER, _REV), (_ENS, "CERTIFICATION_FOUND_VALID", _SAT)),
    # «O equivalent» (decisio 2): sense la norma i amb el perfil complet, «no compleix»
    # nomes potencial; amb el perfil incomplet, «sense dades».
    ((_CERT, _CER, _UNK), (_ENS, _CDI, _UNK)),
    ((_CERT, _CER, _UNK), (_ENS, _CEND, _DNS)),
    ((_CERT, _CER, _UNK), (_CERT, _CDI, _UNK)),
    ((_CERT, _CER, _REV), (_CERT, _CEND, _DNS)),
    # Una llista de noms («qualsevol de», operador `one_of`) es resol norma a norma.
    ((_CERT, _CDI, _UNK), (_SET, _CDI, _UNK)),
    ((_CERT, _CNF, _DNS), (_SET, "CERTIFICATION_SOME_MISSING", _DNS)),
    ((_CERT, "CERTIFICATION_FOUND_VALID", _SAT), (_SET, "CERTIFICATION_FOUND_VALID", _SAT)),
    # «RELI o ROLECE» es qualsevol dels dos (abans nomes el ROLECE).
    ((_REG, _RNF, _DNS), (_ANY, _RNF, _DNS)),
    ((_REG, _RDI, _UNK), (_ANY, _RDI, _UNK)),
    # La classificacio es compara (grup, subgrup, categoria) i, sense dir quina, amb una
    # empresa que no en te cap, es el veredicte de sempre del registre.
    ((_REG, _RCR, _REV), (_CLASS, "CLASSIFICATION_NOT_HELD", _DNS)),
    ((_REG, _RCR, _REV), (_REG, _RDI, _UNK)),
}
#: Quantes entrades del fixture canvien a proposit (v6). Fixat: una de nova o una
#: que desapareix es una regressio o un canvi no documentat.
V6_INTENDED_COUNT = 162


def _signature(outcome: dict) -> tuple[str, str, str]:
    return (outcome["rule_code"], outcome["explanation_code"], outcome["result"])


def _evaluate(entry: dict) -> dict:
    requirement = dec(entry["requirement"])
    args = [dec(arg) for arg in entry["args"]]
    kwargs = {k: dec(v) for k, v in entry["kwargs"].items()}
    if entry["kind"] == "engine.assess_requirement":
        outcome = engine.assess_requirement(requirement, *args, profile=entry["profile"], **kwargs)
    else:
        outcome = FUNCTIONS[entry["kind"]](requirement, *args, **kwargs)
    return dump(key(outcome))


@pytest.mark.parametrize("chunk", range(8))
def test_cada_entrada_dona_el_mateix_rule_outcome_que_la_v4(chunk):
    differences = []
    selected = entries()[chunk::8]
    for entry in selected:
        outcome = _evaluate(entry)
        if outcome == entry["v4"]:
            continue
        if (_signature(entry["v4"]), _signature(outcome)) in V6_INTENDED:
            continue
        differences.append((entry["kind"], entry["profile"], entry["v4"], outcome))
    assert not differences, differences[:3]
    assert selected


def test_les_divergencies_v6_son_exactament_les_volgudes():
    found = set()
    count = 0
    for entry in entries():
        outcome = _evaluate(entry)
        if outcome != entry["v4"]:
            count += 1
            found.add((_signature(entry["v4"]), _signature(outcome)))
    assert found <= V6_INTENDED
    assert found == V6_INTENDED, sorted(V6_INTENDED - found)
    assert count == V6_INTENDED_COUNT


def test_cap_entrada_del_fixture_no_entra_al_complement_product_v2():
    from app.compliance import normalize

    for entry in entries():
        if entry["kind"] == "engine.assess_requirement":
            assert not normalize.applies(dec(entry["requirement"]), entry["profile"])


def _never_used() -> None:  # pragma: no cover - documenta els tipus etiquetats
    _ = (dataclasses, enum)
