"""Sistema de puntuacio estructurat (QUALITY-01): model, validacio, calcul prudent i reconciliacio.

Pur: sense base de dades, sense migracions i sense cap model. Vegeu `model.py`.
"""

from app.scoring.calculator import (
    OfferScore,
    ScoringInputInvalid,
    ScoringInputMissing,
    ScoringRuleMissing,
    automatic_points,
    economic_points,
    judgment_points,
    offered_amount,
    scene_points,
    score_offer,
)
from app.scoring.model import Evaluation, RuleKind, ScoringSystem, SpecError, Status, load_system
from app.scoring.reconcile import Reconciliation, reconcile
from app.scoring.validation import ValidationReport, validate_system

__all__ = [
    "Evaluation",
    "OfferScore",
    "Reconciliation",
    "RuleKind",
    "ScoringInputInvalid",
    "ScoringInputMissing",
    "ScoringRuleMissing",
    "ScoringSystem",
    "SpecError",
    "Status",
    "ValidationReport",
    "automatic_points",
    "economic_points",
    "judgment_points",
    "load_system",
    "offered_amount",
    "reconcile",
    "scene_points",
    "score_offer",
    "validate_system",
]
