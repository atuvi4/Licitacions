"""Comparar una classificacio documental amb un gold de `datasets/classification/`.

La comparen l'expedient real (si hi es) i el bessó sintetic, amb les mateixes
asserts. El gold descriu documents i troballes, no l'entrega: el mateix resultat
s'ha d'obtenir arribin els documents pel canal que arribin.
"""

from __future__ import annotations

import json
from pathlib import Path

DATASETS = Path(__file__).resolve().parents[1] / "datasets" / "classification"


def load_gold(case: str) -> dict:
    return json.loads((DATASETS / f"{case}.gold.json").read_text(encoding="utf-8"))


def assert_matches_gold(classification: dict, gold: dict, *, check_sha256: bool) -> None:
    run = classification["run"]
    assert run is not None, "no hi ha cap classificacio vigent"
    assert run["classifier_version"] == gold["classifier_version"]
    assert run["document_count"] == len(gold["documents"])

    documents = {d["path"]: d for d in classification["documents"]}
    assert list(documents) == [d["path"] for d in gold["documents"]], "documents o ordre diferents"
    by_id = {d["tender_document_id"]: d["path"] for d in classification["documents"]}

    for expected in gold["documents"]:
        document = documents[expected["path"]]
        where = expected["path"]
        assert document["role"] == expected["role"], where
        assert document["role_source"] == "classifier", where
        assert document["suggested_role"] == expected["role"], where
        assert document["suggested_confidence"] == expected["confidence"], where
        assert document["document_type"] == expected["document_type"], where
        assert document["needs_review"] is expected["needs_review"], where
        signals = document["signals"]
        assert signals["filename_role"] == expected["filename_role"], where
        assert signals["content_role"] == expected["content_role"], where
        assert signals["declared_code"] == expected["declared_code"], where
        duplicate_of = signals["duplicate_of"]
        assert (by_id[duplicate_of] if duplicate_of else None) == expected["duplicate_of"], where
        if check_sha256:
            assert document["sha256"] == expected["sha256"], f"{where}: no es el fitxer del gold"

    def shape(finding: dict) -> tuple:
        return (
            finding["code"],
            finding["severity"],
            by_id.get(finding["tender_document_id"]),
            by_id.get(finding["related_document_id"]),
        )

    assert sorted(shape(f) for f in classification["findings"]) == sorted(
        (f["code"], f["severity"], f["path"], f["related_path"]) for f in gold["findings"]
    ), "troballes diferents"

    for expected in gold["findings"]:
        key = (expected["code"], expected["severity"], expected["path"], expected["related_path"])
        finding = next(f for f in classification["findings"] if shape(f) == key)
        for name, value in expected["detail"].items():
            assert finding["detail"][name] == value, (expected["code"], name)
        if "referenced_from" in expected:
            paths = {ref["path"] for ref in finding["detail"]["references"]}
            assert expected["referenced_from"] in paths
