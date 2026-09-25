"""Represa d'un run parcial: reaprofitar els chunks bons i enviar nomes els fallits.

El cas que ho motiva es el run real de Palamos 2024-3137 (`e0fc136c`): 22
chunks, 9 de bons i 13 de fallits. Tornar-lo a fer sencer pagaria dues vegades
els 9 que ja es van llegir be. Aqui es reprodueix la mateixa forma (22 chunks,
els mateixos indexs bons) amb el proveidor fals.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.ai.profiles import V1, V2
from app.ai.provider import OutputTruncatedError, SchemaInvalidError, Usage
from app.ai.providers import fake
from app.core.config import settings
from app.core.errors import ResumeNotAllowedError
from app.models.enums import AnalysisRunStatus, JobRequestStatus
from app.models.requirements import (
    AwardCriterion,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.tenders import Tender
from app.services.analysis_planning import build_preview
from app.services.requirement_analysis import (
    analysis_input,
    analyze_tender_requirements,
    plan_resume,
)
from app.services.requirement_chunking import build_chunks
from tests.jobs_support import confirm_quote, create_quote, run_worker
from tests.test_jobs import QUOTE, _add_extracted_pages

#: Els chunks que van anar be al run real de Palamos.
GOOD = (0, 4, 5, 6, 7, 9, 10, 17, 21)
FAILED = tuple(i for i in range(22) if i not in GOOD)


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
def palamos_plan(monkeypatch):
    """23 pagines, 2 per chunk i 1 de solapament: 22 chunks, com Palamos."""
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 2)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 1)


@pytest.fixture
async def plec23(session, tender_document, palamos_plan):
    return await _add_extracted_pages(session, tender_document, pages=23)


def _req(extraction_id, page: int) -> dict:
    """Un requisit que cita la pagina `page` (cada pagina te el seu text)."""
    return {
        "category": "economic_solvency",
        "obligation": "mandatory",
        "title": f"Solvencia del lot {page}",
        "confidence": "high",
        "evidence": [
            {
                "extraction_id": str(extraction_id),
                "page_number": page,
                "quote": f"{QUOTE} en el lote {page}",
            }
        ],
    }


def _criterion(extraction_id, page: int) -> dict:
    return {
        "name": f"Criteri {page}",
        "evidence": [
            {
                "extraction_id": str(extraction_id),
                "page_number": page,
                "quote": f"{QUOTE} en el lote {page}",
            }
        ],
    }


def _failure(index: int):
    if index % 3 == 0:
        return SchemaInvalidError(
            "La sortida no compleix el contracte del perfil (1 errors).",
            usage=Usage(input_tokens=900, output_tokens=400, stop_reason="tool_use"),
            diagnostics=[{"path": "requirements", "type": "list_type"}],
        )
    return OutputTruncatedError(
        "La resposta ha topat amb el limit de sortida.",
        usage=Usage(input_tokens=1000, output_tokens=5000, stop_reason="max_tokens"),
    )


async def _partial_run(session, plec) -> RequirementExtractionRun:
    """9 bons i 13 fallits. Els bons citen la segona pagina del seu chunk."""
    extraction_id = plec["extraction"].id
    for index in range(22):
        if index in GOOD:
            payload = {"requirements": [_req(extraction_id, index + 2)]}
            if index == 0:
                payload["award_criteria"] = [_criterion(extraction_id, 2)]
            fake.queue_response(payload)
        else:
            fake.queue_response(_failure(index))
    tender = await session.get(Tender, plec["document"].tender_id)
    run = (await analyze_tender_requirements(session, tender, provider=fake.FakeProvider())).run
    assert (run.status, run.chunks_completed, run.chunks_failed) == (
        AnalysisRunStatus.PARTIAL,
        9,
        13,
    )
    fake.reset()
    return run


def _queue_resumed(extraction_id) -> None:
    """Els 13 fallits, ara bons. El chunk 1 torna a veure el requisit del chunk 0."""
    for index in FAILED:
        requirements = [_req(extraction_id, index + 2)]
        if index == 1:
            # Pagines 2-3: la 2 es solapament amb el chunk 0, que ja la va citar.
            requirements.append(_req(extraction_id, 2))
        fake.queue_response({"requirements": requirements})


async def _snapshot(session, run_id) -> dict:
    """Tot el que el run d'origen te, excepte `is_current` i `updated_at`."""
    run = await session.get(RequirementExtractionRun, run_id, populate_existing=True)
    row = {
        c.key: getattr(run, c.key)
        for c in RequirementExtractionRun.__table__.columns
        if c.key not in ("is_current", "updated_at")
    }
    requirements = (
        await session.execute(
            sa.select(Requirement.id, Requirement.dedup_key, Requirement.title).where(
                Requirement.run_id == run_id
            )
        )
    ).all()
    evidence = (
        await session.execute(
            sa.select(RequirementEvidence.id, RequirementEvidence.quote)
            .join(Requirement, Requirement.id == RequirementEvidence.requirement_id)
            .where(Requirement.run_id == run_id)
        )
    ).all()
    return {"run": row, "requirements": sorted(requirements), "evidence": sorted(evidence)}


# --------------------------------------------------------------------------- #
# El pressupost nomes compta el que s'enviara                                   #
# --------------------------------------------------------------------------- #


async def test_el_preview_d_una_represa_compta_nomes_els_13_fallits(session, plec23):
    run = await _partial_run(session, plec23)

    preview = await build_preview(
        session,
        run.tender_id,
        profile=V1,
        provider=fake.FakeProvider(),
        resume_from_run_id=run.id,
    )

    assert preview.chunks == 22
    assert preview.chunks_to_send == 13
    assert preview.reusable_run_id == run.id
    assert preview.resume_from_run_id == run.id
    assert preview.allowed is True


async def test_avui_sense_represa_es_reaprofitaria_sencer_o_s_enviarien_22(session, plec23):
    """El comportament d'abans no canvia: normal = 0 chunks, force = 22."""
    run = await _partial_run(session, plec23)
    provider = fake.FakeProvider()

    normal = await build_preview(session, run.tender_id, profile=V1, provider=provider)
    forced = await build_preview(
        session, run.tender_id, profile=V1, provider=provider, force=True
    )

    assert (normal.chunks_to_send, normal.reusable_run_id) == (0, run.id)
    assert forced.chunks_to_send == 22


async def test_el_quote_per_api_diu_13_i_de_quin_run(client_a, tenant_a, session, plec23):
    run = await _partial_run(session, plec23)

    quote = await create_quote(
        client_a,
        tenant_a[1].id,
        run.tender_id,
        json={"resume_from_run_id": str(run.id)},
    )

    assert (quote["chunks"], quote["chunks_to_send"]) == (22, 13)
    assert quote["resume_from_run_id"] == str(run.id)
    assert quote["force"] is False


async def test_una_represa_no_pot_ser_force(client_a, tenant_a, session, plec23):
    run = await _partial_run(session, plec23)

    response = await client_a.post(
        f"/api/v1/companies/{tenant_a[1].id}/tenders/{run.tender_id}/analysis-quotes",
        json={"resume_from_run_id": str(run.id), "force": True},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


# --------------------------------------------------------------------------- #
# Nomes es repren EXACTAMENT la mateixa analisi                                 #
# --------------------------------------------------------------------------- #


async def _context(session, run):
    current = await analysis_input(session, run.tender_id)
    return {
        "tender_id": run.tender_id,
        "fingerprint": current.fingerprint,
        "chunks": build_chunks(current.documents),
        "provider": fake.FakeProvider(),
        "profile": V1,
    }


def _source_like(run, **changes):
    fields = {
        c.key: getattr(run, c.key) for c in RequirementExtractionRun.__table__.columns
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


async def test_el_run_real_passa_la_comprovacio(session, plec23):
    run = await _partial_run(session, plec23)

    plan = plan_resume(run, **(await _context(session, run)))

    assert set(plan.reused) == set(GOOD)
    assert plan.send == frozenset(FAILED)


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"tender_id": uuid.uuid4()}, "run_not_found"),
        ({"status": AnalysisRunStatus.COMPLETED}, "run_not_partial"),
        ({"status": AnalysisRunStatus.FAILED}, "run_not_partial"),
        ({"is_current": False}, "run_not_current"),
        ({"input_fingerprint": "0" * 64}, "input_changed"),
        ({"prompt_version": "requirements-v0"}, "profile_mismatch"),
        ({"schema_version": "requirements-schema-v0"}, "profile_mismatch"),
        ({"extractor_version": "tender-requirements-v0"}, "profile_mismatch"),
        ({"provider": "anthropic"}, "provider_mismatch"),
        ({"model": "claude-sonnet-5"}, "provider_mismatch"),
        ({"chunks_total": 21}, "chunk_plan_mismatch"),
    ],
)
async def test_qualsevol_diferencia_rebutja_la_represa(session, plec23, changes, reason):
    run = await _partial_run(session, plec23)

    with pytest.raises(ResumeNotAllowedError) as caught:
        plan_resume(_source_like(run, **changes), **(await _context(session, run)))

    assert caught.value.details["reason"] == reason


async def test_un_altre_pla_de_chunks_rebutja_la_represa(session, plec23, monkeypatch):
    run = await _partial_run(session, plec23)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)

    with pytest.raises(ResumeNotAllowedError) as caught:
        plan_resume(run, **(await _context(session, run)))

    assert caught.value.details["reason"] == "chunk_plan_mismatch"


async def test_un_altre_document_al_mateix_index_rebutja_la_represa(session, plec23):
    run = await _partial_run(session, plec23)
    coverage = dict(run.chunk_coverage)
    coverage["chunks"] = [dict(entry) for entry in coverage["chunks"]]
    coverage["chunks"][3]["tender_document_id"] = str(uuid.uuid4())

    with pytest.raises(ResumeNotAllowedError) as caught:
        plan_resume(_source_like(run, chunk_coverage=coverage), **(await _context(session, run)))

    assert caught.value.details["reason"] == "chunk_plan_mismatch"


async def test_un_run_sense_chunks_fallits_no_es_repren(session, plec23):
    run = await _partial_run(session, plec23)
    coverage = dict(run.chunk_coverage)
    coverage["chunks"] = [{**entry, "status": "completed"} for entry in coverage["chunks"]]

    with pytest.raises(ResumeNotAllowedError) as caught:
        plan_resume(_source_like(run, chunk_coverage=coverage), **(await _context(session, run)))

    assert caught.value.details["reason"] == "nothing_to_resume"


async def test_un_altre_model_o_perfil_rebutja_la_represa_abans_de_cap_crida(
    session, plec23
):
    run = await _partial_run(session, plec23)
    tender = await session.get(Tender, run.tender_id)

    with pytest.raises(ResumeNotAllowedError):
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider(model="un-altre"), resume_from_run_id=run.id
        )
    with pytest.raises(ResumeNotAllowedError):
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider(), profile=V2, resume_from_run_id=run.id
        )

    assert fake.SENT_PAYLOADS == []
    assert (
        await session.scalar(
            sa.select(sa.func.count()).select_from(RequirementExtractionRun)
        )
    ) == 1


async def test_els_plecs_canviats_rebutgen_la_represa(session, plec23):
    run = await _partial_run(session, plec23)
    # Una pagina mes a l'extraccio vigent: l'empremta ja no es la mateixa.
    from app.models.enums import PageExtractionStatus
    from app.models.tender_documents import TenderDocumentPage

    session.add(
        TenderDocumentPage(
            extraction_id=plec23["extraction"].id,
            page_number=24,
            text="Pagina afegida.",
            char_count=15,
            text_hash="z" * 64,
            status=PageExtractionStatus.EXTRACTED,
        )
    )
    await session.commit()
    tender = await session.get(Tender, run.tender_id)

    with pytest.raises(ResumeNotAllowedError) as caught:
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider(), resume_from_run_id=run.id
        )

    assert caught.value.details["reason"] in {"input_changed", "chunk_plan_mismatch"}
    assert fake.SENT_PAYLOADS == []


# --------------------------------------------------------------------------- #
# L'execucio                                                                    #
# --------------------------------------------------------------------------- #


async def _resume(session, plec23):
    run = await _partial_run(session, plec23)
    before = await _snapshot(session, run.id)
    _queue_resumed(plec23["extraction"].id)
    tender = await session.get(Tender, run.tender_id)
    resumed = (
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider(), resume_from_run_id=run.id
        )
    ).run
    return run, resumed, before


async def test_nomes_s_envien_els_chunks_fallits(session, plec23):
    run, resumed, _ = await _resume(session, plec23)

    assert len(fake.SENT_PAYLOADS) == 13
    sent_first_pages = sorted(
        int(payload["user_content"].split('<PAGE number="')[1].split('"')[0])
        for payload in fake.SENT_PAYLOADS
    )
    assert sent_first_pages == sorted(index + 1 for index in FAILED)


async def test_el_run_nou_es_complet_i_vigent(session, plec23):
    run, resumed, _ = await _resume(session, plec23)

    assert resumed.id != run.id
    assert resumed.status == AnalysisRunStatus.COMPLETED
    assert (resumed.chunks_total, resumed.chunks_completed, resumed.chunks_failed) == (22, 22, 0)
    assert resumed.is_current is True
    assert resumed.provider_metadata["resumed_from_run_id"] == str(run.id)
    assert resumed.provider_metadata["reused_chunks"] == list(GOOD)


async def test_la_cobertura_diu_que_es_reaprofitat_i_que_s_ha_enviat(session, plec23):
    run, resumed, _ = await _resume(session, plec23)

    chunks = {entry["index"]: entry for entry in resumed.chunk_coverage["chunks"]}
    for index in GOOD:
        assert chunks[index]["origin"] == "reused"
        assert chunks[index]["reused_from_run_id"] == str(run.id)
        assert chunks[index]["input_tokens"] is None, "ja es van pagar al run d'origen"
        assert chunks[index]["status"] == "completed"
    for index in FAILED:
        assert chunks[index]["origin"] == "sent"
        assert chunks[index]["status"] == "completed"
    # Els tokens del run nou son nomes els dels 13 enviats (100 + 50 al fals).
    assert (resumed.input_tokens, resumed.output_tokens) == (13 * 100, 13 * 50)


async def test_els_resultats_i_les_cites_reaprofitats_es_conserven(session, plec23):
    run, resumed, _ = await _resume(session, plec23)

    def by_title(run_id):
        return (
            sa.select(Requirement.title, RequirementEvidence.page_number, RequirementEvidence.quote)
            .join(RequirementEvidence, RequirementEvidence.requirement_id == Requirement.id)
            .where(Requirement.run_id == run_id)
        )

    original = set((await session.execute(by_title(run.id))).all())
    copied = set((await session.execute(by_title(resumed.id))).all())
    assert original <= copied
    criteria = await session.scalar(
        sa.select(sa.func.count()).select_from(AwardCriterion).where(
            AwardCriterion.run_id == resumed.id
        )
    )
    assert criteria == 1


async def test_la_deduplicacio_funciona_despres_de_la_fusio(session, plec23):
    """El chunk 1 torna a citar la pagina 2, que el chunk 0 reaprofitat ja tenia."""
    run, resumed, _ = await _resume(session, plec23)

    titles = (
        await session.scalars(sa.select(Requirement.title).where(Requirement.run_id == resumed.id))
    ).all()
    assert len(titles) == len(set(titles)) == 22
    assert resumed.requirements_extracted == 22


async def test_el_run_parcial_original_no_es_modifica(session, plec23):
    run, resumed, before = await _resume(session, plec23)

    after = await _snapshot(session, run.id)
    refreshed = await session.get(RequirementExtractionRun, run.id, populate_existing=True)

    assert after == before
    # L'unic canvi es el de qualsevol run substituit: deixa de ser el vigent.
    assert refreshed.is_current is False


async def test_la_validacio_de_cites_continua_als_chunks_reenviats(session, plec23):
    run = await _partial_run(session, plec23)
    extraction_id = plec23["extraction"].id
    for index in FAILED:
        invented = _req(extraction_id, index + 2)
        invented["evidence"][0]["quote"] = "Aquesta frase no surt enlloc"
        fake.queue_response({"requirements": [invented]})
    tender = await session.get(Tender, run.tender_id)

    resumed = (
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider(), resume_from_run_id=run.id
        )
    ).run

    assert resumed.requirements_extracted == 9, "nomes els reaprofitats"
    assert resumed.requirements_rejected == 13


async def test_una_represa_que_torna_a_fallar_queda_parcial_i_es_pot_tornar_a_reprendre(
    session, plec23
):
    run = await _partial_run(session, plec23)
    extraction_id = plec23["extraction"].id
    for index in FAILED:
        fake.queue_response(
            _failure(index) if index == 12 else {"requirements": [_req(extraction_id, index + 2)]}
        )
    tender = await session.get(Tender, run.tender_id)
    resumed = (
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider(), resume_from_run_id=run.id
        )
    ).run

    assert (resumed.status, resumed.chunks_completed, resumed.chunks_failed) == (
        AnalysisRunStatus.PARTIAL,
        21,
        1,
    )
    plan = plan_resume(resumed, **(await _context(session, resumed)))
    assert plan.send == frozenset({12})
    # I el run d'origen, ja substituit, no es pot reprendre.
    with pytest.raises(ResumeNotAllowedError):
        plan_resume(
            await session.get(RequirementExtractionRun, run.id, populate_existing=True),
            **(await _context(session, run)),
        )


# --------------------------------------------------------------------------- #
# De punta a punta: quote, confirmacio i worker                                 #
# --------------------------------------------------------------------------- #


async def test_quote_confirmacio_i_worker_envien_nomes_13(
    client_a, tenant_a, session, session_factory, plec23
):
    run = await _partial_run(session, plec23)
    company_id = tenant_a[1].id
    quote = await create_quote(
        client_a, company_id, run.tender_id, json={"resume_from_run_id": str(run.id)}
    )
    confirmed = await confirm_quote(client_a, company_id, quote["id"])
    assert confirmed.status_code == 202, confirmed.text
    _queue_resumed(plec23["extraction"].id)

    await run_worker(session_factory)

    assert len(fake.SENT_PAYLOADS) == 13
    final = await client_a.get(
        f"/api/v1/companies/{company_id}/job-requests/{confirmed.json()['id']}"
    )
    assert final.json()["status"] == JobRequestStatus.SUCCEEDED.value
    current = await session.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == run.tender_id,
            RequirementExtractionRun.is_current.is_(True),
        ).execution_options(populate_existing=True)
    )
    assert current.id != run.id
    assert current.provider_metadata["resumed_from_run_id"] == str(run.id)


async def test_si_el_run_deixa_de_ser_reprenible_la_confirmacio_no_crea_res(
    client_a, tenant_a, session, plec23
):
    run = await _partial_run(session, plec23)
    company_id = tenant_a[1].id
    quote = await create_quote(
        client_a, company_id, run.tender_id, json={"resume_from_run_id": str(run.id)}
    )
    # Entremig, algu fa una analisi completa nova i el parcial deixa de ser vigent.
    await session.execute(
        sa.update(RequirementExtractionRun)
        .where(RequirementExtractionRun.id == run.id)
        .values(is_current=False)
    )
    await session.commit()

    response = await confirm_quote(client_a, company_id, quote["id"])

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "resume_not_allowed"
    from app.models.jobs import WorkJob

    assert await session.scalar(sa.select(sa.func.count()).select_from(WorkJob)) == 0


# --------------------------------------------------------------------------- #
# El sostre de chunks compta els que s'envien, no els planificats              #
# --------------------------------------------------------------------------- #


async def test_el_sostre_de_la_represa_compta_nomes_els_chunks_que_s_envien(session, plec23):
    """`ANALYSIS_MAX_CHUNKS_PER_RUN=13` deixa reprendre un run de 22 amb 13 fallits."""
    from app.core.errors import AnalysisTooLargeError

    run = await _partial_run(session, plec23)
    tender = await session.get(Tender, run.tender_id)

    with pytest.raises(AnalysisTooLargeError):
        await analyze_tender_requirements(
            session,
            tender,
            provider=fake.FakeProvider(),
            resume_from_run_id=run.id,
            chunk_limit=12,
        )
    assert fake.SENT_PAYLOADS == [], "amb el sostre superat no surt cap crida"

    _queue_resumed(plec23["extraction"].id)
    resumed = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=fake.FakeProvider(),
            resume_from_run_id=run.id,
            chunk_limit=13,
        )
    ).run

    assert resumed.status == AnalysisRunStatus.COMPLETED
    assert len(fake.SENT_PAYLOADS) == 13


async def test_el_preview_de_la_represa_aplica_el_sostre_als_chunks_a_enviar(session, plec23):
    run = await _partial_run(session, plec23)
    provider = fake.FakeProvider()

    at_cap = await build_preview(
        session, run.tender_id, profile=V1, provider=provider,
        resume_from_run_id=run.id, max_chunks=13,
    )
    below = await build_preview(
        session, run.tender_id, profile=V1, provider=provider,
        resume_from_run_id=run.id, max_chunks=12,
    )
    full = await build_preview(
        session, run.tender_id, profile=V1, provider=provider, force=True, max_chunks=13
    )

    assert (at_cap.chunks, at_cap.chunks_to_send, at_cap.allowed) == (22, 13, True)
    assert (below.allowed, below.blocking_reason) == (False, "analysis_too_large")
    assert (full.chunks_to_send, full.allowed) == (22, False)


async def test_el_worker_aplica_el_sostre_als_chunks_a_enviar(
    client_a, tenant_a, session, session_factory, plec23, monkeypatch
):
    run = await _partial_run(session, plec23)
    company_id = tenant_a[1].id
    monkeypatch.setattr(settings, "ANALYSIS_MAX_CHUNKS_PER_RUN", 13)
    quote = await create_quote(
        client_a, company_id, run.tender_id, json={"resume_from_run_id": str(run.id)}
    )
    assert (quote["chunks_to_send"], quote["max_chunks"], quote["allowed"]) == (13, 13, True)
    confirmed = await confirm_quote(client_a, company_id, quote["id"])
    assert confirmed.status_code == 202, confirmed.text
    _queue_resumed(plec23["extraction"].id)

    await run_worker(session_factory)

    assert len(fake.SENT_PAYLOADS) == 13
    final = await client_a.get(
        f"/api/v1/companies/{company_id}/job-requests/{confirmed.json()['id']}"
    )
    assert final.json()["status"] == JobRequestStatus.SUCCEEDED.value
