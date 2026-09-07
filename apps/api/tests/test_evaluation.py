"""Benchmark d'extraccio: que l'avaluador mesuri el que diu que mesura.

Un evaluador amb un bug es pitjor que no tenir-ne: dona una xifra que ningu
qüestiona. Aquests tests fixen els casos on es podria enganyar sol —comptar com
a encert una cita inventada, dissimular un requisit perdut, o donar per bo un
import equivocat.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.evaluation.dataset import (
    GoldCase,
    GoldDataset,
    GoldRequirement,
    load_dataset,
    save_dataset,
)
from app.evaluation.matching import (
    ErrorKind,
    PredictedRequirement,
    classify_pair_errors,
    match_requirements,
    text_overlap,
)
from app.evaluation.metrics import (
    EvaluationSummary,
    accumulate_case,
    is_automatable,
    update_evidence,
)
from app.evaluation.report import build_payload, render_html, render_markdown, warnings_for
from app.models.enums import (
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
)

QUOTE = "El volumen anual de negocios debera ser al menos de 450.000 euros"


def gold(**overrides) -> GoldRequirement:
    data = {
        "gold_id": "g1",
        "category": RequirementCategory.ECONOMIC_SOLVENCY,
        "obligation": RequirementObligation.MANDATORY,
        "operator": RequirementOperator.GTE,
        "scope": RequirementScope.TENDER,
        "value": {"amount": 450000, "currency": "EUR", "period": "annual"},
        "document_name": "PCAP",
        "page_number": 27,
        "quote": QUOTE,
    }
    data.update(overrides)
    return GoldRequirement(**data)


def predicted(**overrides) -> PredictedRequirement:
    data = {
        "id": "p1",
        "category": RequirementCategory.ECONOMIC_SOLVENCY,
        "obligation": RequirementObligation.MANDATORY,
        "operator": RequirementOperator.GTE,
        "scope": RequirementScope.TENDER,
        "title": "Facturacio anual minima de 450.000 EUR",
        "value": {"amount": 450000, "currency": "EUR", "period": "annual"},
        "evidence": (("PCAP", 27, QUOTE, True),),
    }
    data.update(overrides)
    return PredictedRequirement(**data)


def summarise_one(gold_items, predictions, *, pages=frozenset({27})) -> EvaluationSummary:
    summary = EvaluationSummary(
        dataset_id="t", provider="fake", model="m",
        prompt_version="requirements-v1", extractor_version="e", schema_version="s",
    )
    outcome = match_requirements(gold_items, predictions)
    accumulate_case(summary, case_id="c1", outcome=outcome, analysed_pages=set(pages))
    for pair in outcome.pairs:
        update_evidence(
            summary.evidence,
            gold_page=pair.gold.page_number,
            gold_document=pair.gold.document_name,
            predicted=pair.predicted,
        )
    return summary


# --------------------------------------------------------------------------- #
# Deteccio                                                                      #
# --------------------------------------------------------------------------- #


def test_a_perfect_prediction_scores_one():
    summary = summarise_one([gold()], [predicted()])

    assert summary.overall.true_positives == 1
    assert summary.overall.false_positives == 0
    assert summary.overall.false_negatives == 0
    assert summary.overall.precision == 1.0
    assert summary.overall.recall == 1.0
    assert summary.critical.recall == 1.0
    assert summary.structure.fully_correct == 1


def test_a_missed_mandatory_requirement_is_a_critical_miss():
    summary = summarise_one([gold()], [])

    assert summary.overall.recall == 0.0
    assert summary.critical.false_negatives == 1
    assert len(summary.critical_misses) == 1
    miss = summary.critical_misses[0]
    assert miss.gold_id == "g1"
    assert miss.page_number == 27
    assert miss.likely_cause == "model_miss"
    assert summary.errors[ErrorKind.MISSED_REQUIREMENT.value] == 1


def test_a_missed_requirement_on_an_unanalysed_page_says_so():
    summary = summarise_one([gold()], [], pages=frozenset({1, 2}))

    assert summary.critical_misses[0].likely_cause == "page_not_analysed"


def test_a_hallucinated_requirement_is_a_false_positive_and_a_false_blocker():
    extra = predicted(id="p2", title="Requisit inventat", value={"amount": 999})

    summary = summarise_one([], [extra])

    assert summary.overall.false_positives == 1
    assert summary.errors[ErrorKind.HALLUCINATED_REQUIREMENT.value] == 1
    assert len(summary.false_blockers) == 1
    assert summary.false_blockers[0].gold_obligation is None


def test_a_false_mandatory_on_a_scored_requirement_is_flagged():
    """§12: el cas perillos, perque pot provocar un NO-GO fals."""
    summary = summarise_one(
        [gold(obligation=RequirementObligation.SCORED)],
        [predicted(obligation=RequirementObligation.MANDATORY)],
    )

    assert len(summary.false_blockers) == 1
    assert summary.false_blockers[0].gold_obligation == "scored"
    assert summary.errors[ErrorKind.WRONG_OBLIGATION.value] == 1


def test_a_wrong_amount_is_detected_but_not_fully_correct():
    """Detectar que existeix NO es encertar-lo: 45.000 no es 450.000."""
    summary = summarise_one(
        [gold()], [predicted(value={"amount": 45000, "currency": "EUR", "period": "annual"})]
    )

    assert summary.overall.true_positives == 1, "es la mateixa exigencia"
    assert summary.structure.amount_ok == 0
    assert summary.structure.fully_correct == 0
    assert summary.errors[ErrorKind.WRONG_NUMERIC_VALUE.value] == 1


def test_a_partially_structured_requirement_is_incomplete_not_wrong():
    summary = summarise_one([gold()], [predicted(value=None)])

    assert summary.errors[ErrorKind.INCOMPLETE_STRUCTURE.value] == 1
    assert summary.structure.fully_correct == 0


def test_a_wrong_page_is_not_matched():
    summary = summarise_one(
        [gold()], [predicted(evidence=(("PCAP", 99, QUOTE, True),))]
    )

    assert summary.overall.true_positives == 0
    assert summary.overall.false_negatives == 1
    assert summary.overall.false_positives == 1


def test_an_unlocatable_quote_never_counts_as_a_true_positive():
    """§9: un requisit sense evidencia valida no es un encert complet."""
    summary = summarise_one(
        [gold()], [predicted(evidence=(("PCAP", 27, "cita inventada", False),))]
    )

    assert summary.overall.true_positives == 0
    assert summary.overall.false_positives == 1
    assert summary.overall.false_negatives == 1
    assert summary.errors[ErrorKind.WRONG_EVIDENCE.value] >= 1
    assert summary.evidence.quote_locatable == 0


def test_a_duplicate_prediction_counts_once_and_is_reported():
    summary = summarise_one([gold()], [predicted(), predicted(id="p2")])

    assert summary.overall.true_positives == 1
    assert summary.errors[ErrorKind.DUPLICATE.value] == 1
    assert summary.overall.false_positives == 1


def test_a_wrong_lot_scope_is_classified():
    summary = summarise_one([gold()], [predicted(scope=RequirementScope.LOT)])

    assert summary.errors[ErrorKind.WRONG_SCOPE.value] == 1
    assert summary.structure.scope_ok == 0


def test_a_wrong_operator_is_classified():
    pairs = match_requirements([gold()], [predicted(operator=RequirementOperator.LTE)]).pairs

    assert ErrorKind.WRONG_OPERATOR in classify_pair_errors(pairs[0])


def test_a_different_category_is_never_matched():
    summary = summarise_one(
        [gold()], [predicted(category=RequirementCategory.INSURANCE)]
    )

    assert summary.overall.true_positives == 0


# --------------------------------------------------------------------------- #
# automatable_rate: es mesura amb el motor de 4C, no s'estima                   #
# --------------------------------------------------------------------------- #


def test_a_structured_threshold_is_automatable():
    assert is_automatable(predicted()) is True


def test_a_requirement_without_value_is_not_automatable():
    assert is_automatable(predicted(value=None)) is False


def test_free_text_similarity_is_not_automatable():
    item = predicted(
        category=RequirementCategory.EXPERIENCE,
        value={"minimum_projects": 3, "similarity_condition": "naturaleza similar"},
        operator=None,
    )

    assert is_automatable(item) is False


def test_the_automatable_rate_only_counts_correct_critical_requirements():
    summary = summarise_one(
        [gold(), gold(gold_id="g2", page_number=30)],
        [predicted(), predicted(id="p2", value=None, evidence=(("PCAP", 30, QUOTE, True),))],
    )

    assert summary.critical_correct == 2
    assert summary.automatable_correct == 1
    assert summary.automatable_rate == 0.5


# --------------------------------------------------------------------------- #
# Criteris d'adjudicacio i casos no analitzats                                  #
# --------------------------------------------------------------------------- #


def test_no_award_criteria_predicted_counts_as_missed():
    from app.evaluation.dataset import GoldAwardCriterion
    from app.evaluation.matching import match_award_criteria

    item = GoldAwardCriterion(
        gold_id="a1", name="Oferta economica", points=45,
        document_name="PCAP", page_number=33, quote="Oferta economica: hasta 45 puntos",
    )
    pairs, missed, spurious = match_award_criteria([item], [])

    assert pairs == [] and len(missed) == 1 and spurious == []


def test_a_case_the_provider_could_not_analyse_still_counts_its_gold_as_missed():
    """Si el sistema no arriba a analitzar el plec, tampoc en treu els requisits."""
    summary = summarise_one([gold()], [], pages=frozenset())

    assert summary.overall.recall == 0.0
    assert summary.critical_misses[0].likely_cause == "document_not_processed"


# --------------------------------------------------------------------------- #
# Dataset i informes                                                            #
# --------------------------------------------------------------------------- #


def test_the_shipped_gold_dataset_is_valid():
    dataset = load_dataset("gold-v1")

    assert dataset.dataset_id == "gold-v1"
    assert dataset.cases, "el gold no pot estar buit"
    assert dataset.total_requirements > 0
    for case in dataset.cases:
        for requirement in case.requirements:
            assert requirement.quote.strip(), "tota anotacio necessita cita"
            assert requirement.page_number >= 1


def test_gold_ids_must_be_unique_within_a_case():
    with pytest.raises(ValueError):
        GoldCase(case_id="c", requirements=[gold(), gold()])


def test_a_dataset_round_trips_through_disk(tmp_path: Path):
    dataset = GoldDataset(
        dataset_id="tmp-v1", version=1, created_at=date(2026, 9, 7),
        description="prova", cases=[GoldCase(case_id="c1", requirements=[gold()])],
    )
    save_dataset(dataset, base_dir=tmp_path)
    loaded = load_dataset("tmp-v1", base_dir=tmp_path)

    assert loaded.total_requirements == 1
    assert loaded.cases[0].requirements[0].quote == QUOTE


def test_an_unknown_field_in_the_gold_is_rejected(tmp_path: Path):
    """El gold es una font de veritat: un camp inventat ha de petar."""
    path = tmp_path / "bad-v1.json"
    path.write_text(
        json.dumps(
            {
                "dataset_id": "bad-v1", "version": 1, "created_at": "2026-09-07",
                "description": "x",
                "cases": [{"case_id": "c", "requirements": [], "camp_inventat": 1}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_dataset("bad-v1", base_dir=tmp_path)


def test_the_report_warns_about_low_critical_recall():
    summary = summarise_one([gold(), gold(gold_id="g2", page_number=30)], [predicted()])

    alerts = warnings_for(summary)

    assert any("critic" in alert.lower() for alert in alerts)


def test_the_report_warns_about_false_blockers():
    summary = summarise_one([], [predicted()])

    assert any("blocador" in alert.lower() for alert in warnings_for(summary))


def test_the_reports_render():
    summary = summarise_one([gold()], [predicted()])
    details = [
        {
            "case_id": "c1",
            "expediente_number": "1/2026",
            "gold_requirements": 1,
            "predicted_requirements": 1,
            "matched": 1,
            "missed": [],
            "spurious": [],
            "pairs": [
                {
                    "gold_id": "g1",
                    "score": 0.9,
                    "gold_quote": QUOTE,
                    "predicted_title": "Facturacio",
                    "gold_value": {"amount": 450000},
                    "predicted_value": {"amount": 450000},
                    "gold_obligation": "mandatory",
                    "predicted_obligation": "mandatory",
                    "evidence_valid": True,
                }
            ],
        }
    ]
    payload = build_payload(summary, details)

    markdown = render_markdown(summary, details)
    html = render_html(summary, details)

    assert payload["summary"]["detection"]["overall"]["recall"] == 1.0
    assert "Recall" in markdown and "automatable_rate" in markdown
    assert "<table>" in html and "450000" in html


def test_text_overlap_is_symmetric_and_bounded():
    assert text_overlap("a b c", "a b c") == 1.0
    assert text_overlap("a b", "c d") == 0.0
    assert text_overlap("", "x") == 0.0
