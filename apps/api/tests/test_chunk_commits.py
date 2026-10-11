"""Commit per chunk i represa des de `worker_lost` (3B-2 §6.6).

Mentre el run es RUNNING (i mai vigent), cada chunk respost es desa en un commit
propi: requisits i evidencies, `chunk_coverage`, usage i la linia del llibre de
despesa. Un worker que mor a mig run no perd la feina pagada: el reconciliador el
marca `worker_lost` i una represa (`plan_resume`) nomes envia els chunks pendents.

El proveidor «real» es `RealNamedFake` (cap xarxa). El «crash» es una excepcio que
no es `ProviderError` llancada abans de la tercera crida: surt de l'analisi sense
passar pel tancament del run, com un proces que mor.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.ai.profiles import DEFAULT_PROFILE
from app.ai.provider import OutputTruncatedError, SchemaInvalidError, Usage
from app.ai.providers import fake
from app.core.config import settings
from app.models.enums import AnalysisRunStatus
from app.models.llm_budget import LlmSpendLedger
from app.models.requirements import Requirement, RequirementEvidence, RequirementExtractionRun
from app.models.tenders import Tender
from app.services.requirement_analysis import (
    analysis_input,
    analyze_tender_requirements,
    plan_resume,
    run_is_superseded,
)
from app.services.requirement_chunking import build_chunks
from tests.quota_support import pin_real_model, requirement
from tests.real_provider import RealNamedFake
from tests.test_jobs import _add_extracted_pages


class WorkerDied(Exception):
    """El proces del worker mor (no es cap error del proveidor)."""


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
async def plec3(session, tender_document, monkeypatch):
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    pin_real_model(monkeypatch)
    return await _add_extracted_pages(session, tender_document, pages=3)


def _responses(plec, pages=(1, 2, 3)) -> list[dict]:
    return [{"requirements": [requirement(plec["extraction"].id, page)]} for page in pages]


async def _crash_before_third_call(session, plec) -> RequirementExtractionRun:
    async def die_on_third(index: int) -> None:
        if index == 2:
            raise WorkerDied()

    tender = await session.get(Tender, plec["document"].tender_id)
    provider = RealNamedFake(responses=_responses(plec), before_call=die_on_third)
    with pytest.raises(WorkerDied):
        await analyze_tender_requirements(session, tender, provider=provider)
    await session.rollback()
    return await session.scalar(sa.select(RequirementExtractionRun))


async def test_un_crash_despres_del_chunk_2_de_3_conserva_els_chunks_1_i_2(
    session_factory, session, plec3
):
    run = await _crash_before_third_call(session, plec3)

    # Una sessio nova: nomes hi ha el que s'ha confirmat.
    async with session_factory() as fresh:
        stored = await fresh.get(RequirementExtractionRun, run.id)
        assert stored.status == AnalysisRunStatus.RUNNING
        assert stored.is_current is False
        assert stored.chunks_completed == 2
        assert (stored.input_tokens, stored.output_tokens) == (200, 100)
        statuses = [(c["index"], c["status"]) for c in stored.chunk_coverage["chunks"]]
        assert statuses == [(0, "completed"), (1, "completed"), (2, "failed")]
        assert stored.chunk_coverage["chunks"][2]["error"] == "pending"
        titles = sorted(
            (
                await fresh.scalars(
                    sa.select(Requirement.title).where(Requirement.run_id == run.id)
                )
            ).all()
        )
        assert titles == ["Solvencia 1", "Solvencia 2"]
        evidence = await fresh.scalar(
            sa.select(sa.func.count(RequirementEvidence.id))
            .join(Requirement, Requirement.id == RequirementEvidence.requirement_id)
            .where(Requirement.run_id == run.id)
        )
        assert evidence == 2
        ledger = (
            await fresh.scalars(
                sa.select(LlmSpendLedger.chunk_index)
                .where(LlmSpendLedger.run_id == run.id)
                .order_by(LlmSpendLedger.chunk_index)
            )
        ).all()
        assert list(ledger) == [0, 1]


@pytest.mark.parametrize("error_code", ["worker_lost", "internal_error"])
async def test_la_represa_des_de_worker_lost_envia_nomes_el_chunk_3(session, plec3, error_code):
    run = await _crash_before_third_call(session, plec3)
    # El reconciliador (o el worker en capturar l'excepcio) el dona per perdut.
    await session.execute(
        sa.update(RequirementExtractionRun)
        .where(RequirementExtractionRun.id == run.id)
        .values(status=AnalysisRunStatus.FAILED, error_code=error_code)
    )
    await session.commit()
    source = await session.get(RequirementExtractionRun, run.id, populate_existing=True)
    current = await analysis_input(session, run.tender_id)

    plan = plan_resume(
        source,
        tender_id=run.tender_id,
        fingerprint=current.fingerprint,
        chunks=build_chunks(current.documents),
        provider=RealNamedFake(),
        profile=DEFAULT_PROFILE,
        superseded=await run_is_superseded(session, source),
    )
    assert (sorted(plan.reused), sorted(plan.send)) == ([0, 1], [2])

    provider = RealNamedFake(responses=_responses(plec3, pages=(3,)))
    resumed = (
        await analyze_tender_requirements(
            session,
            await session.get(Tender, run.tender_id),
            provider=provider,
            resume_from_run_id=run.id,
        )
    ).run

    assert provider.calls == 1
    assert resumed.status == AnalysisRunStatus.COMPLETED and resumed.is_current is True
    titles = sorted(
        (
            await session.scalars(
                sa.select(Requirement.title).where(Requirement.run_id == resumed.id)
            )
        ).all()
    )
    assert titles == ["Solvencia 1", "Solvencia 2", "Solvencia 3"]
    resumed_ledger = (
        await session.scalars(
            sa.select(LlmSpendLedger.chunk_index).where(LlmSpendLedger.run_id == resumed.id)
        )
    ).all()
    assert list(resumed_ledger) == [2], "els chunks represos no es tornen a pagar"


async def test_cada_chunk_es_confirmat_abans_d_enviar_el_seguent(session_factory, session, plec3):
    """Abans de cada crida, el que ha respost el chunk anterior ja es a la base."""
    seen: list[tuple[int, int, int]] = []

    async def look(index: int) -> None:
        async with session_factory() as other:
            run = await other.scalar(sa.select(RequirementExtractionRun))
            requirements = await other.scalar(sa.select(sa.func.count(Requirement.id)))
            ledger = await other.scalar(sa.select(sa.func.count(LlmSpendLedger.id)))
            seen.append((index, run.chunks_completed, requirements + 10 * ledger))

    tender = await session.get(Tender, plec3["document"].tender_id)
    provider = RealNamedFake(responses=_responses(plec3), before_call=look)
    run = (await analyze_tender_requirements(session, tender, provider=provider)).run

    assert run.status == AnalysisRunStatus.COMPLETED
    assert seen == [(0, 0, 0), (1, 1, 11), (2, 2, 22)]


@pytest.mark.parametrize(
    "error",
    [
        OutputTruncatedError(
            "Limit de sortida.",
            usage=Usage(input_tokens=10, output_tokens=8000, stop_reason="max_tokens"),
        ),
        SchemaInvalidError("Contracte trencat.", usage=Usage(input_tokens=10, output_tokens=5)),
    ],
    ids=["output_truncated", "schema_invalid"],
)
async def test_una_sortida_inservible_falla_el_chunk_no_el_run_i_es_paga(session, plec3, error):
    responses = _responses(plec3)
    responses[1] = error
    tender = await session.get(Tender, plec3["document"].tender_id)

    run = (
        await analyze_tender_requirements(
            session, tender, provider=RealNamedFake(responses=responses)
        )
    ).run

    assert run.status == AnalysisRunStatus.PARTIAL
    assert (run.chunks_completed, run.chunks_failed) == (2, 1)
    ledger = (
        await session.scalars(sa.select(LlmSpendLedger).order_by(LlmSpendLedger.chunk_index))
    ).all()
    # El chunk caigut tambe es va respondre: aquells tokens es paguen.
    assert [line.chunk_index for line in ledger] == [0, 1, 2]
    assert ledger[1].input_tokens == 10
