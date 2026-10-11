"""Ajudes compartides dels tests de Task 8 (3B-2 §6.1, §6.4, §6.6, §6.7). Cap xarxa.

* `pin_real_model`: el proveidor «real» de test (`RealNamedFake`, `name="anthropic"`)
  amb el model admes (`LLM_ALLOWED_MODELS`) i un preu (`LLM_MODEL_PRICES`). Sense
  aixo, la quota i el handler el refusen (`model_not_pinned`, `price_unknown`).
* `clean_package`: una classificacio global vigent sense troballes, amb l'empremta
  dels documents d'ara (el paquet d'un tender public es `complete`).
* `set_quality`: el `qa_status` de l'extraccio d'un plec.
* `requirement`: un requisit v1 amb una cita literal de `_page_text`.
"""

from __future__ import annotations

import uuid

from app.core.config import settings
from app.models.enums import (
    ClassificationFindingCode,
    FindingSeverity,
    GlobalClassificationTrigger,
)
from app.models.global_classification import (
    GlobalDocumentClassificationFinding,
    GlobalDocumentClassificationRun,
)
from app.services.global_document_classification import compute_global_fingerprint
from tests.test_jobs import QUOTE

#: Model del proveidor «real» de test (el de `RealNamedFake` per defecte).
TEST_MODEL = "claude-test-model"


def pin_real_model(
    monkeypatch,
    *,
    model: str = TEST_MODEL,
    input_per_million: float = 3.0,
    output_per_million: float = 15.0,
    price: bool = True,
    also: tuple[str, ...] = (),
) -> None:
    """Admet `model` (i els de `also`) i, si `price`, els posa preu (dolars per milio
    de tokens). Substitueix els models pinats per defecte de `tests/conftest.py`."""
    models = [model, *also]
    monkeypatch.setattr(settings, "LLM_ALLOWED_MODELS", models)
    prices = (
        {name: {"input": input_per_million, "output": output_per_million} for name in models}
        if price
        else {}
    )
    monkeypatch.setattr(settings, "LLM_MODEL_PRICES", prices)


async def clean_package(session, tender_id: uuid.UUID) -> GlobalDocumentClassificationRun:
    """Classificacio global vigent, sense troballes, amb l'empremta d'ara."""
    fingerprint = await compute_global_fingerprint(session, tender_id)
    assert fingerprint is not None, "cal algun document analitzable amb contingut"
    run = GlobalDocumentClassificationRun(
        tender_id=tender_id,
        classifier_version="test",
        input_fingerprint=fingerprint,
        trigger=GlobalClassificationTrigger.DOCUMENT_PROCESSING,
        document_count=1,
        is_current=True,
    )
    session.add(run)
    await session.commit()
    return run


async def add_high_finding(
    session,
    run: GlobalDocumentClassificationRun,
    code: ClassificationFindingCode = ClassificationFindingCode.REQUIRED_ROLE_MISSING,
    *,
    tender_document_id: uuid.UUID | None = None,
    severity: FindingSeverity = FindingSeverity.HIGH,
) -> GlobalDocumentClassificationFinding:
    finding = GlobalDocumentClassificationFinding(
        run_id=run.id,
        code=code,
        severity=severity,
        tender_document_id=tender_document_id,
        detail={"role": "ppt"},
    )
    session.add(finding)
    await session.commit()
    return finding


async def set_quality(session, extraction, status: str | None) -> None:
    extraction.qa_status = status
    await session.commit()


def requirement(extraction_id, page: int) -> dict:
    return {
        "category": "economic_solvency",
        "obligation": "mandatory",
        "title": f"Solvencia {page}",
        "confidence": "high",
        "evidence": [
            {
                "extraction_id": str(extraction_id),
                "page_number": page,
                "quote": f"{QUOTE} en el lote {page}",
            }
        ],
    }
