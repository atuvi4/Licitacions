"""Base del run, identitat d'analisi i «mai vigent si no es real» (3B-2 §6.2-§6.3).

Tres capes; aqui es proven les dues de dades:

* `_finalise_run` nomes promou a `is_current` un run `real` + `COMPLETED` + amb el
  corpus canonic actual. Un run `simulation` (fake/fixture) mai; un PARTIAL mai; un
  run real el corpus del qual ha canviat durant l'execucio tampoc (`input_changed`,
  el treball pagat es conserva); un run real COMPLETED sense cap requisit ni criteri
  sobre >= 5 pagines queda `completed_empty` amb `needs_review`.
* `run_basis` es deriva del proveidor (`fake`/`fixture` -> `simulation`, `gold`,
  `unconfigured`, la resta `real`).
* `analysis_identity` (sha256 del JSON canonic) es desa a la quota i al run, i el
  `dedupe_key` d'un tender public es `analyze:{tender}:canonical:{identity}`.

El proveidor «real» es `tests.real_provider.RealNamedFake` (cap xarxa).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.ai.profiles import DEFAULT_PROFILE
from app.ai.provider import OutputTruncatedError, Usage
from app.ai.providers import fake
from app.core.config import settings
from app.core.errors import ResumeNotAllowedError
from app.jobs import quotes as quote_service
from app.jobs import requests as job_requests
from app.models.enums import (
    AnalysisRunStatus,
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
    RequirementRunBasis,
)
from app.models.jobs import WorkJob
from app.models.requirements import RequirementExtractionRun
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender
from app.services import requirement_analysis
from app.services.requirement_analysis import (
    ANALYSIS_IDENTITY_VERSION,
    analysis_identity,
    analysis_input,
    analyze_tender_requirements,
    plan_resume,
)
from app.services.requirement_chunking import build_chunks
from tests.real_provider import RealNamedFake
from tests.test_jobs import QUOTE, _add_extracted_pages, _page_text


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


def _requirement(extraction_id, page: int) -> dict:
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


async def _tender(session, plec) -> Tender:
    return await session.get(Tender, plec["document"].tender_id)


async def _reextract(session, document, *, sha: str, pages: int) -> TenderDocumentExtraction:
    """Re-extraccio amb bytes nous: fitxer, contingut i extraccio nous vigents."""
    old_file = await session.scalar(
        sa.select(TenderDocumentFile).where(
            TenderDocumentFile.tender_document_id == document.id,
            TenderDocumentFile.is_current.is_(True),
        )
    )
    old_file.is_current = False
    await session.flush()
    content = TenderDocumentContent(
        sha256=sha,
        storage_key=f"tenders/documents/bb/bb/{sha}.pdf",
        file_size=4096,
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()
    texts = [_page_text(n) for n in range(1, pages + 1)]
    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="pypdf-v1",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=pages,
        extracted_page_count=pages,
        empty_page_count=0,
        text_char_count=sum(len(t) for t in texts),
        ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()
    for number, text in enumerate(texts, start=1):
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=number,
                text=text,
                char_count=len(text),
                text_hash="h" * 64,
                status=PageExtractionStatus.EXTRACTED,
            )
        )
    session.add(
        TenderDocumentFile(
            tender_document_id=document.id,
            content_id=content.id,
            source_url=document.url,
            version_number=2,
            is_current=True,
            download_status="downloaded",
        )
    )
    await session.flush()
    return extraction


# --------------------------------------------------------------------------- #
# run_basis                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("provider", "basis"),
    [
        ("fake", RequirementRunBasis.SIMULATION),
        ("fixture", RequirementRunBasis.SIMULATION),
        (" Fake ", RequirementRunBasis.SIMULATION),
        ("", RequirementRunBasis.UNCONFIGURED),
        (None, RequirementRunBasis.UNCONFIGURED),
        ("gold", RequirementRunBasis.GOLD),
        ("gold:lescala", RequirementRunBasis.GOLD),
        ("anthropic", RequirementRunBasis.REAL),
    ],
)
def test_la_base_del_run_es_deriva_del_proveidor(provider, basis):
    assert RequirementRunBasis.for_provider(provider) is basis


async def test_un_run_creat_sense_base_explicita_la_deriva_del_proveidor(session, tender_document):
    run = RequirementExtractionRun(
        tender_id=tender_document.tender_id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint="f" * 64,
        extractor_version="v",
        prompt_version="v",
        schema_version="v",
        provider="gold",
        model="manual",
    )
    session.add(run)
    await session.flush()
    assert run.run_basis == RequirementRunBasis.GOLD
    assert run.needs_review is False


# --------------------------------------------------------------------------- #
# Mai vigent si no es real                                                      #
# --------------------------------------------------------------------------- #


async def test_un_run_fake_buit_es_simulacio_completat_i_mai_vigent(session, tender_document):
    plec = await _add_extracted_pages(session, tender_document, pages=5)
    run = (
        await analyze_tender_requirements(
            session, await _tender(session, plec), provider=fake.FakeProvider()
        )
    ).run
    assert run.run_basis == RequirementRunBasis.SIMULATION
    # `completed_empty` es nomes per als runs reals: un buit simulat no diu res del plec.
    assert run.status == AnalysisRunStatus.COMPLETED
    assert (run.is_current, run.needs_review) == (False, False)
    assert await requirement_analysis.current_run_for(session, run.tender_id) is None


async def test_un_run_fake_amb_resultats_tampoc_es_vigent(session, tender_document):
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    fake.queue_response({"requirements": [_requirement(plec["extraction"].id, 2)]})
    run = (
        await analyze_tender_requirements(
            session, await _tender(session, plec), provider=fake.FakeProvider()
        )
    ).run
    assert (run.status, run.requirements_extracted) == (AnalysisRunStatus.COMPLETED, 1)
    assert run.is_current is False


@pytest.mark.parametrize("provider_class", [fake.FakeProvider, RealNamedFake])
async def test_un_run_parcial_mai_es_vigent(session, tender_document, monkeypatch, provider_class):
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    provider = provider_class(
        responses=[
            {"requirements": [_requirement(plec["extraction"].id, 1)]},
            OutputTruncatedError(
                "Limit de sortida.",
                usage=Usage(input_tokens=10, output_tokens=10, stop_reason="max_tokens"),
            ),
        ]
    )
    run = (
        await analyze_tender_requirements(session, await _tender(session, plec), provider=provider)
    ).run
    assert run.status == AnalysisRunStatus.PARTIAL
    assert run.is_current is False


async def test_un_run_real_complet_amb_resultats_i_corpus_actual_es_canonic(
    session, tender_document
):
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    provider = RealNamedFake(responses=[{"requirements": [_requirement(plec["extraction"].id, 2)]}])
    tender = await _tender(session, plec)
    run = (await analyze_tender_requirements(session, tender, provider=provider)).run
    assert run.run_basis == RequirementRunBasis.REAL
    assert (run.status, run.is_current, run.needs_review) == (
        AnalysisRunStatus.COMPLETED,
        True,
        False,
    )
    assert run.error_code is None
    current = await analysis_input(session, tender.id)
    assert run.analysis_identity == analysis_identity(
        current.documents, profile=DEFAULT_PROFILE, provider=provider
    )
    assert (await requirement_analysis.current_run_for(session, tender.id)).id == run.id


async def test_un_run_real_buit_sobre_cinc_pagines_es_completed_empty(session, tender_document):
    plec = await _add_extracted_pages(session, tender_document, pages=5)
    tender = await _tender(session, plec)
    run = (await analyze_tender_requirements(session, tender, provider=RealNamedFake())).run
    assert (run.requirements_extracted, run.award_criteria_extracted, run.pages_analyzed) == (
        0,
        0,
        5,
    )
    assert run.status == AnalysisRunStatus.COMPLETED_EMPTY
    assert (run.needs_review, run.is_current) == (True, False)
    assert await requirement_analysis.current_run_for(session, tender.id) is None


async def test_un_run_real_buit_sobre_menys_de_cinc_pagines_es_completat(session, tender_document):
    plec = await _add_extracted_pages(session, tender_document, pages=4)
    run = (
        await analyze_tender_requirements(
            session, await _tender(session, plec), provider=RealNamedFake()
        )
    ).run
    assert run.status == AnalysisRunStatus.COMPLETED
    assert run.needs_review is False


async def test_un_run_real_amb_el_corpus_canviat_durant_l_execucio_no_es_promou(
    session, tender_document
):
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await _tender(session, plec)

    async def reextract(index: int) -> None:
        if index == 0:
            await _reextract(session, tender_document, sha="b" * 64, pages=2)

    provider = RealNamedFake(
        responses=[{"requirements": [_requirement(plec["extraction"].id, 2)]}],
        before_call=reextract,
    )
    run = (await analyze_tender_requirements(session, tender, provider=provider)).run
    # El treball pagat es conserva (COMPLETED, amb el requisit), pero no es la base
    # canonica: el corpus ja no es el que va llegir.
    assert (run.status, run.requirements_extracted) == (AnalysisRunStatus.COMPLETED, 1)
    assert (run.is_current, run.error_code) == (False, "input_changed")
    assert await requirement_analysis.current_run_for(session, tender.id) is None


async def test_un_run_simulat_no_desplaca_el_canonic_real(session, tender_document):
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await _tender(session, plec)
    real = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=RealNamedFake(
                responses=[{"requirements": [_requirement(plec["extraction"].id, 2)]}]
            ),
        )
    ).run
    simulated = (
        await analyze_tender_requirements(session, tender, provider=fake.FakeProvider())
    ).run
    await session.refresh(real)
    assert (real.is_current, simulated.is_current) == (True, False)


# --------------------------------------------------------------------------- #
# Identitat d'analisi i dedupe canonic                                          #
# --------------------------------------------------------------------------- #


async def test_la_identitat_es_el_sha_del_json_canonic_de_la_spec(session, tender_document):
    plec = await _add_extracted_pages(session, tender_document, pages=3)
    current = await analysis_input(session, tender_document.tender_id)
    provider = RealNamedFake()
    document = current.documents[0]
    expected = {
        "fp_version": ANALYSIS_IDENTITY_VERSION,
        "documents": [
            [
                str(tender_document.id),
                "administrative_terms",
                plec["content"].sha256,
                str(plec["extraction"].id),
                "pypdf-v1",
                3,
            ]
        ],
        "chunking": {
            "pages_per_chunk": settings.REQUIREMENTS_CHUNK_PAGES,
            "overlap": settings.REQUIREMENTS_CHUNK_OVERLAP_PAGES,
            "max_pages": settings.REQUIREMENTS_MAX_PAGES_PER_RUN,
        },
        "versions": {
            "extractor_version": DEFAULT_PROFILE.extractor_version,
            "prompt_version": DEFAULT_PROFILE.prompt_version,
            "schema_version": DEFAULT_PROFILE.schema_version,
        },
        "provider": "anthropic",
        "model": provider.model,
    }
    canonical = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert document.document_type == "administrative_terms"
    assert (
        analysis_identity(current.documents, profile=DEFAULT_PROFILE, provider=provider)
        == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    )


async def test_la_identitat_canvia_amb_el_model_i_el_chunking(
    session, tender_document, monkeypatch
):
    await _add_extracted_pages(session, tender_document, pages=3)
    current = await analysis_input(session, tender_document.tender_id)
    base = analysis_identity(current.documents, profile=DEFAULT_PROFILE, provider=RealNamedFake())
    other_model = analysis_identity(
        current.documents, profile=DEFAULT_PROFILE, provider=RealNamedFake(model="altre")
    )
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 2)
    other_chunking = analysis_identity(
        current.documents, profile=DEFAULT_PROFILE, provider=RealNamedFake()
    )
    assert len({base, other_model, other_chunking}) == 3


async def test_el_dedupe_d_un_tender_public_es_canonic_i_sense_parametres_de_tenant(
    session, tender_document, tenant_a, tenant_b
):
    """Dues empreses, mateix tender public: una sola feina amb la clau canonica."""
    await _add_extracted_pages(session, tender_document, pages=2)
    tender_id = tender_document.tender_id
    (user_a, company_a), (user_b, company_b) = tenant_a, tenant_b
    submitted = []
    for user, company in ((user_a, company_a), (user_b, company_b)):
        submitted.append(
            await job_requests.submit_analysis_request(
                session,
                company_id=company.id,
                user_id=user.id,
                tender_id=tender_id,
                provider_factory=RealNamedFake,
            )
        )
    current = await analysis_input(session, tender_id)
    identity = analysis_identity(
        current.documents, profile=DEFAULT_PROFILE, provider=RealNamedFake()
    )
    jobs = (await session.scalars(sa.select(WorkJob))).all()
    assert len(jobs) == 1
    assert jobs[0].dedupe_key == f"analyze:{tender_id}:canonical:{identity}"
    assert {s.request.work_job_id for s in submitted} == {jobs[0].id}
    assert str(company_a.id) not in jobs[0].dedupe_key
    assert str(company_b.id) not in json.dumps(jobs[0].parameters)


async def test_un_tender_privat_conserva_la_clau_per_parametres(session, tender_document, tenant_a):
    user, company = tenant_a
    tender = await session.get(Tender, tender_document.tender_id)
    tender.owner_company_id = company.id
    await session.commit()
    await _add_extracted_pages(session, tender_document, pages=2)
    await job_requests.submit_analysis_request(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=tender.id,
        provider_factory=RealNamedFake,
    )
    job = await session.scalar(sa.select(WorkJob))
    assert job.dedupe_key.startswith(f"analyze:{tender.id}:normal:")


async def test_la_quota_desa_la_identitat_d_analisi(session, tender_document, tenant_a):
    user, company = tenant_a
    await _add_extracted_pages(session, tender_document, pages=2)
    quote = await quote_service.create_quote(
        session,
        company_id=company.id,
        user_id=user.id,
        tender_id=tender_document.tender_id,
        provider_factory=RealNamedFake,
    )
    current = await analysis_input(session, tender_document.tender_id)
    assert quote.analysis_identity == analysis_identity(
        current.documents, profile=DEFAULT_PROFILE, provider=RealNamedFake()
    )


# --------------------------------------------------------------------------- #
# Represa: el parcial ja no es vigent, i tambe des de `worker_lost`             #
# --------------------------------------------------------------------------- #


async def _partial(session, tender_document, monkeypatch, provider_class=RealNamedFake):
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    provider = provider_class(
        responses=[
            {"requirements": [_requirement(plec["extraction"].id, 1)]},
            OutputTruncatedError(
                "Limit de sortida.",
                usage=Usage(input_tokens=10, output_tokens=10, stop_reason="max_tokens"),
            ),
        ]
    )
    run = (
        await analyze_tender_requirements(session, await _tender(session, plec), provider=provider)
    ).run
    assert run.status == AnalysisRunStatus.PARTIAL and not run.is_current
    return run, provider


async def _resume_context(session, run, provider):
    current = await analysis_input(session, run.tender_id)
    return {
        "tender_id": run.tender_id,
        "fingerprint": current.fingerprint,
        "chunks": build_chunks(current.documents),
        "provider": provider,
        "profile": DEFAULT_PROFILE,
    }


async def test_un_parcial_no_vigent_es_pot_reprendre(session, tender_document, monkeypatch):
    run, provider = await _partial(session, tender_document, monkeypatch)
    plan = plan_resume(run, **(await _resume_context(session, run, provider)))
    assert (sorted(plan.reused), sorted(plan.send)) == ([0], [1])


@pytest.mark.parametrize("error_code", ["worker_lost", "internal_error"])
async def test_un_run_fallit_per_worker_lost_amb_chunks_completats_es_pot_reprendre(
    session, tender_document, monkeypatch, error_code
):
    run, provider = await _partial(session, tender_document, monkeypatch)
    run.status = AnalysisRunStatus.FAILED
    run.error_code = error_code
    await session.flush()
    plan = plan_resume(run, **(await _resume_context(session, run, provider)))
    assert sorted(plan.send) == [1]


async def test_un_run_fallit_per_un_altre_motiu_no_es_reprend(
    session, tender_document, monkeypatch
):
    run, provider = await _partial(session, tender_document, monkeypatch)
    run.status = AnalysisRunStatus.FAILED
    run.error_code = "all_chunks_failed"
    await session.flush()
    with pytest.raises(ResumeNotAllowedError) as caught:
        plan_resume(run, **(await _resume_context(session, run, provider)))
    assert caught.value.details["reason"] == "run_not_partial"


async def test_un_parcial_superat_per_un_run_mes_nou_no_es_reprend(
    session, tender_document, monkeypatch
):
    run, provider = await _partial(session, tender_document, monkeypatch)
    assert await requirement_analysis.run_is_superseded(session, run) is False
    newer = RequirementExtractionRun(
        tender_id=run.tender_id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=run.input_fingerprint,
        extractor_version="v",
        prompt_version="v",
        schema_version="v",
        provider="anthropic",
        model="m",
        started_at=datetime.now(UTC),
    )
    session.add(newer)
    await session.flush()
    assert await requirement_analysis.run_is_superseded(session, run) is True
    with pytest.raises(ResumeNotAllowedError) as caught:
        plan_resume(run, **(await _resume_context(session, run, provider)), superseded=True)
    assert caught.value.details["reason"] == "run_not_current"


# --------------------------------------------------------------------------- #
# 409 `requirements_outdated` per corpus (3B-2 §6.2)                           #
# --------------------------------------------------------------------------- #


def _runs_url(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis/runs"


async def _canonical(session, tender_document):
    from app.knowledge import vocabulary

    await vocabulary.seed_vocabulary(session)
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await _tender(session, plec)
    provider = RealNamedFake(responses=[{"requirements": [_requirement(plec["extraction"].id, 2)]}])
    run = (await analyze_tender_requirements(session, tender, provider=provider)).run
    assert run.is_current
    await session.commit()
    return tender, run


async def test_una_re_extraccio_amb_sha_nou_fa_caducar_els_requisits(
    session, tender_document, tenant_a, client_a
):
    _user, company = tenant_a
    tender, _run = await _canonical(session, tender_document)
    first = await client_a.post(_runs_url(company.id, tender.id))
    assert first.status_code == 201, first.text

    await _reextract(session, tender_document, sha="c" * 64, pages=2)
    await session.commit()
    refused = await client_a.post(_runs_url(company.id, tender.id))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "analysis_prerequisites_missing"
    assert error["details"]["missing"] == ["requirements_outdated"]


async def test_un_document_retirat_tambe_fa_caducar_els_requisits(
    session, tender_document, tenant_a, client_a
):
    _user, company = tenant_a
    tender, _run = await _canonical(session, tender_document)
    tender_document.removed_at = datetime.now(UTC)
    await session.commit()
    refused = await client_a.post(_runs_url(company.id, tender.id))
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["missing"] == ["requirements_outdated"]


# --------------------------------------------------------------------------- #
# M6 (de la Task 6): nomes s'anota la run vigent en una licitacio publica       #
# --------------------------------------------------------------------------- #


async def test_un_requisit_d_una_run_superada_no_s_anota_pero_es_llegeix(session, tender_document):
    from app.models.requirements import Requirement
    from app.services import tender_structure as structure

    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await _tender(session, plec)
    old = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=RealNamedFake(
                responses=[{"requirements": [_requirement(plec["extraction"].id, 1)]}]
            ),
        )
    ).run
    new = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=RealNamedFake(
                responses=[{"requirements": [_requirement(plec["extraction"].id, 2)]}],
                model="claude-test-model-2",
            ),
        )
    ).run
    await session.refresh(old)
    assert (old.is_current, new.is_current) == (False, True)
    superseded = await session.scalar(sa.select(Requirement).where(Requirement.run_id == old.id))
    current = await session.scalar(sa.select(Requirement).where(Requirement.run_id == new.id))

    with pytest.raises(structure.RequirementRunSupersededError) as caught:
        await structure.load_requirement(session, tender, superseded.id)
    assert caught.value.code == "requirement_run_superseded"
    assert (await structure.load_requirement(session, tender, current.id)).id == current.id
    read = await structure.load_requirement(session, tender, superseded.id, for_write=False)
    assert read.id == superseded.id


# --------------------------------------------------------------------------- #
# Fix round 1: seleccio explicita (I1, I2), gold (M7) i represa (M7)            #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def two_plecs(session, tender_document, second_tender_document):
    """Dos plecs analitzables del mateix tender (els dos al conjunt per defecte)."""
    first = await _add_extracted_pages(session, tender_document, pages=2)
    second = await _add_extracted_pages(session, second_tender_document, pages=2, sha="d" * 64)
    return first, second


async def _own(session, tender_id, company_id) -> Tender:
    tender = await session.get(Tender, tender_id)
    tender.owner_company_id = company_id
    await session.commit()
    return tender


async def _selected_run(session, tender, plec):
    """Run real amb seleccio explicita d'un sol plec."""
    provider = RealNamedFake(responses=[{"requirements": [_requirement(plec["extraction"].id, 1)]}])
    return (
        await analyze_tender_requirements(
            session, tender, provider=provider, document_ids=[plec["document"].id]
        )
    ).run


async def test_en_una_licitacio_manual_una_seleccio_explicita_real_es_vigent(
    session, two_plecs, tenant_a
):
    first, _second = two_plecs
    tender = await _own(session, first["document"].tender_id, tenant_a[1].id)
    run = await _selected_run(session, tender, first)
    assert (run.status, run.error_code, run.is_current) == (
        AnalysisRunStatus.COMPLETED,
        None,
        True,
    )


async def test_en_una_licitacio_publica_una_seleccio_no_canonica_no_es_vigent(session, two_plecs):
    first, _second = two_plecs
    tender = await _tender(session, first)
    run = await _selected_run(session, tender, first)
    assert (run.is_current, run.error_code) == (False, "selection_not_canonical")


async def _outdated(session, company_id, tender_id) -> bool:
    from app.core.errors import AnalysisPrerequisitesMissingError
    from app.services import tender_analysis

    try:
        await tender_analysis.assert_inputs_are_current(session, company_id, tender_id)
    except AnalysisPrerequisitesMissingError as exc:
        assert exc.details["missing"] == ["requirements_outdated"]
        return True
    return False


async def test_una_seleccio_explicita_sense_canvis_no_caduca(session, two_plecs, tenant_a):
    first, _second = two_plecs
    company_id = tenant_a[1].id
    tender = await _own(session, first["document"].tender_id, company_id)
    run = await _selected_run(session, tender, first)
    assert run.is_current
    assert await requirement_analysis.run_is_outdated(session, run) is False
    assert await _outdated(session, company_id, tender.id) is False


async def test_re_extreure_un_plec_seleccionat_fa_caducar_la_seleccio(session, two_plecs, tenant_a):
    first, _second = two_plecs
    company_id = tenant_a[1].id
    tender = await _own(session, first["document"].tender_id, company_id)
    run = await _selected_run(session, tender, first)
    await _reextract(session, first["document"], sha="e" * 64, pages=2)
    await session.commit()
    assert await requirement_analysis.run_is_outdated(session, run) is True
    assert await _outdated(session, company_id, tender.id) is True


async def test_canviar_un_plec_no_seleccionat_no_fa_caducar_la_seleccio(
    session, two_plecs, tenant_a
):
    first, second = two_plecs
    company_id = tenant_a[1].id
    tender = await _own(session, first["document"].tender_id, company_id)
    run = await _selected_run(session, tender, first)
    await _reextract(session, second["document"], sha="f" * 64, pages=2)
    await session.commit()
    assert await requirement_analysis.run_is_outdated(session, run) is False
    assert await _outdated(session, company_id, tender.id) is False


async def test_una_run_gold_no_caduca_pel_corpus(session, tender_document):
    """La seva empremta es la del dataset, no la d'un corpus: nomes la retirada compta."""
    await _add_extracted_pages(session, tender_document, pages=2)
    gold = RequirementExtractionRun(
        tender_id=tender_document.tender_id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint="9" * 64,
        extractor_version="tender-gold-v3",
        prompt_version="none",
        schema_version="gold",
        provider="gold",
        model="manual",
        is_current=True,
    )
    session.add(gold)
    await session.flush()
    assert gold.run_basis == RequirementRunBasis.GOLD
    assert await requirement_analysis.run_is_outdated(session, gold) is False


async def test_una_simulacio_mes_nova_no_supera_un_parcial_real(
    session, tender_document, monkeypatch
):
    run, _provider = await _partial(session, tender_document, monkeypatch)
    for provider, blocks in (("fake", False), ("anthropic", True)):
        session.add(
            RequirementExtractionRun(
                tender_id=run.tender_id,
                status=AnalysisRunStatus.COMPLETED,
                input_fingerprint=run.input_fingerprint,
                extractor_version="v",
                prompt_version="v",
                schema_version="v",
                provider=provider,
                model="m",
                started_at=datetime.now(UTC),
            )
        )
        await session.flush()
        assert await requirement_analysis.run_is_superseded(session, run) is blocks


async def test_la_darrera_simulacio_acabada_es_la_que_llegeix_la_demo(session, tender_document):
    """M5: una simulacio mai no es vigent; la demo (`LLM_PROVIDER=fixture`) troba la
    seva analisi amb la mateixa regla que `find_reusable_run` per a simulacions."""
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await _tender(session, plec)
    assert await requirement_analysis.latest_simulation_run(session, tender.id) is None
    simulated = (
        await analyze_tender_requirements(session, tender, provider=fake.FakeProvider())
    ).run
    real = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=RealNamedFake(
                responses=[{"requirements": [_requirement(plec["extraction"].id, 2)]}]
            ),
        )
    ).run
    assert real.is_current
    latest = await requirement_analysis.latest_simulation_run(session, tender.id)
    assert latest.id == simulated.id


# --------------------------------------------------------------------------- #
# Fix round 2 (N1): en una licitacio publica no hi ha «seleccio explicita»      #
# --------------------------------------------------------------------------- #


async def test_en_una_publica_una_seleccio_igual_al_corpus_caduca_si_entra_un_plec_nou(
    session, tender_document, second_tender_document, tenant_a, client_a
):
    """Una seleccio explicita igual al corpus canonic sencer es promou (empremtes
    iguals); si despres entra un plec analitzable nou, el corpus canonic ha canviat i
    la run es obsoleta: l'obsolescencia d'una publica es mira sempre amb el canonic."""
    from app.knowledge import vocabulary

    _user, company = tenant_a
    await vocabulary.seed_vocabulary(session)
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await _tender(session, plec)
    run = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=RealNamedFake(
                responses=[{"requirements": [_requirement(plec["extraction"].id, 2)]}]
            ),
            document_ids=[tender_document.id],
        )
    ).run
    assert (run.is_current, run.error_code) == (True, None)
    await session.commit()
    first = await client_a.post(_runs_url(company.id, tender.id))
    assert first.status_code == 201, first.text

    await _add_extracted_pages(session, second_tender_document, pages=2, sha="d" * 64)
    assert await requirement_analysis.run_is_outdated(session, run) is True
    refused = await client_a.post(_runs_url(company.id, tender.id))
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["missing"] == ["requirements_outdated"]
