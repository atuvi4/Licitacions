"""Run global compartit + punter per empresa (3B-2 §6.5).

`company_tender_requirement_runs` (PK empresa+tender) diu quin run de requisits ha
acceptat cada empresa. `effective_requirements(session, tender_id, company_id)`
llegeix el punter i, si no n'hi ha, el canonic global. Un run canonic nou NO
substitueix el que una empresa te acceptat: l'empresa l'adopta explicitament
(`review_runs.adopt_requirement_run`), i aleshores la seva revisio s'ha de
reconciliar com sempre. Cap fila del run antic s'escriu.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.ai.providers import fake
from app.core.errors import ConflictError, NotFoundError
from app.knowledge import vocabulary
from app.models.company_runs import CompanyTenderRequirementRun
from app.models.enums import (
    AnalysisRunStatus,
    JobOutcome,
    JobRequestStatus,
    RequirementTiming,
    WorkJobType,
)
from app.models.jobs import JobRequest
from app.models.requirements import Requirement
from app.models.tenders import Tender
from app.services import review_runs, review_view
from app.services import tender_analysis as analysis
from app.services.manual_requirement import effective_requirements
from app.services.requirement_analysis import analyze_tender_requirements
from app.services.review_runs import ReconciliationRequiredError, adopt_requirement_run
from tests.real_provider import RealNamedFake
from tests.test_jobs import _add_extracted_pages
from tests.test_requirement_run_basis import _requirement


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _real(session, tender, extraction_id, page: int, *, model: str = "claude-test-model"):
    """Un run real canonic. Un model diferent es una altra identitat d'analisi: un run
    nou sobre el MATEIX corpus (cap `force`, que no es canonic)."""
    provider = RealNamedFake(
        responses=[{"requirements": [_requirement(extraction_id, page)]}], model=model
    )
    run = (await analyze_tender_requirements(session, tender, provider=provider)).run
    assert run.is_current, (run.status, run.error_code)
    return run


@pytest.fixture
async def two_runs(session, tender_document):
    """R1 canonic; els tests en fan despres un R2 canonic amb un requisit diferent."""
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)
    r1 = await _real(session, tender, plec["extraction"].id, 1)
    return tender, plec, r1


async def _ids(session, tender_id, company_id) -> tuple:
    effective = await effective_requirements(session, tender_id, company_id)
    return effective.run.id if effective.run else None, {r.id for r in effective.extracted}


async def _rows_of(session, run_id) -> dict:
    """Les files extretes d'una run, tal com son a la base de dades."""
    rows = await session.execute(
        sa.select(Requirement.id, Requirement.dedup_key, Requirement.updated_at).where(
            Requirement.run_id == run_id
        )
    )
    return {row.id: (row.dedup_key, row.updated_at) for row in rows}


async def test_a_mante_r1_fins_a_adoptar_r2_i_b_sense_punter_llegeix_r2(
    session, two_runs, tenant_a, tenant_b
):
    tender, plec, r1 = two_runs
    (user_a, company_a), (_user_b, company_b) = tenant_a, tenant_b
    adoption = await adopt_requirement_run(
        session, company_id=company_a.id, tender_id=tender.id, run_id=r1.id, user_id=user_a.id
    )
    await session.commit()
    pointer = await session.get(CompanyTenderRequirementRun, (company_a.id, tender.id))
    assert (pointer.run_id, pointer.accepted_by) == (r1.id, user_a.id)
    assert pointer.accepted_at is not None
    assert adoption.previous_run_id is None

    r1_rows = await _rows_of(session, r1.id)
    r2 = await _real(session, tender, plec["extraction"].id, 2, model="claude-test-model-2")
    await session.refresh(r1)
    assert (r1.is_current, r2.is_current) == (False, True)

    a_run, a_rows = await _ids(session, tender.id, company_a.id)
    b_run, b_rows = await _ids(session, tender.id, company_b.id)
    global_run, _ = await _ids(session, tender.id, None)
    assert (a_run, b_run, global_run) == (r1.id, r2.id, r2.id)
    assert a_rows == set(r1_rows) and a_rows.isdisjoint(b_rows)

    adoption = await adopt_requirement_run(
        session, company_id=company_a.id, tender_id=tender.id, run_id=r2.id, user_id=user_a.id
    )
    await session.commit()
    assert adoption.previous_run_id == r1.id
    assert (await _ids(session, tender.id, company_a.id))[0] == r2.id
    # Cap fila del run antic s'ha escrit.
    assert await _rows_of(session, r1.id) == r1_rows


async def test_l_inputs_hash_v3_segueix_el_punter_de_cada_empresa(
    session, two_runs, tenant_a, tenant_b
):
    tender, plec, r1 = two_runs
    (user_a, company_a), (_user_b, company_b) = tenant_a, tenant_b
    await adopt_requirement_run(
        session, company_id=company_a.id, tender_id=tender.id, run_id=r1.id, user_id=user_a.id
    )
    r2 = await _real(session, tender, plec["extraction"].id, 2, model="claude-test-model-2")
    await session.commit()

    a_before = (
        await analysis.analyze_tender(session, company_id=company_a.id, tender_id=tender.id)
    ).run
    b = (await analysis.analyze_tender(session, company_id=company_b.id, tender_id=tender.id)).run
    await session.commit()
    assert (a_before.extraction_run_id, b.extraction_run_id) == (r1.id, r2.id)
    assert a_before.inputs_hash != b.inputs_hash

    await adopt_requirement_run(
        session, company_id=company_a.id, tender_id=tender.id, run_id=r2.id, user_id=user_a.id
    )
    await session.commit()
    assert await analysis.is_stale(session, a_before) is True
    a_after = (
        await analysis.analyze_tender(session, company_id=company_a.id, tender_id=tender.id)
    ).run
    await session.commit()
    assert a_after.extraction_run_id == r2.id
    assert a_after.inputs_hash != a_before.inputs_hash


async def test_la_revisio_de_l_empresa_segueix_el_seu_punter(session, two_runs, tenant_a):
    """Amb el punter a R1, un R2 canonic no bloqueja la revisio d'A; adoptar-lo si."""
    tender, plec, r1 = two_runs
    user, company = tenant_a
    await adopt_requirement_run(
        session, company_id=company.id, tender_id=tender.id, run_id=r1.id, user_id=user.id
    )
    await review_view.start_review(
        session, company_id=company.id, tender_id=tender.id, actor_id=user.id
    )
    await session.commit()
    r2 = await _real(session, tender, plec["extraction"].id, 2, model="claude-test-model-2")
    await session.commit()

    assert await review_runs.current_run_id(session, tender.id, company.id) == r1.id
    # Res a reconciliar mentre A llegeix R1.
    await review_view.start_review(
        session, company_id=company.id, tender_id=tender.id, actor_id=user.id
    )
    await session.commit()

    adoption = await adopt_requirement_run(
        session, company_id=company.id, tender_id=tender.id, run_id=r2.id, user_id=user.id
    )
    await session.commit()
    assert adoption.reconciliation_required is True
    assert await review_runs.current_run_id(session, tender.id, company.id) == r2.id
    with pytest.raises(ReconciliationRequiredError):
        await review_view.start_review(
            session, company_id=company.id, tender_id=tender.id, actor_id=user.id
        )


def _requested(company, user, tender, run) -> JobRequest:
    """La peticio d'aquesta empresa que va donar `run`."""
    return JobRequest(
        company_id=company.id,
        user_id=user.id,
        tender_id=tender.id,
        job_type=WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
        parameters={},
        status=JobRequestStatus.SUCCEEDED,
        outcome=JobOutcome.EXECUTED,
        result_run_id=run.id,
        requested_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )


async def _refused(session, company, tender, run, user) -> None:
    with pytest.raises(ConflictError) as caught:
        await adopt_requirement_run(
            session, company_id=company.id, tender_id=tender.id, run_id=run.id, user_id=user.id
        )
    assert caught.value.code == "requirement_run_not_adoptable"
    assert await session.get(CompanyTenderRequirementRun, (company.id, tender.id)) is None


async def test_una_simulacio_no_s_adopta_mai_ni_si_l_ha_demanada_l_empresa(
    session, tender_document, tenant_a
):
    """M2: una simulacio no pot ser la base de cap empresa (§6.3)."""
    user, company = tenant_a
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)
    fake.queue_response({"requirements": [_requirement(plec["extraction"].id, 1)]})
    simulated = (
        await analyze_tender_requirements(session, tender, provider=fake.FakeProvider())
    ).run
    assert simulated.is_current is False
    await _refused(session, company, tender, simulated, user)
    session.add(_requested(company, user, tender, simulated))
    await session.flush()
    await _refused(session, company, tender, simulated, user)


async def test_un_parcial_real_demanat_per_l_empresa_s_adopta_pero_un_input_changed_no(
    session, tender_document, tenant_a, monkeypatch
):
    from app.ai.provider import OutputTruncatedError, Usage
    from app.core.config import settings

    user, company = tenant_a
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)
    partial = (
        await analyze_tender_requirements(
            session,
            tender,
            provider=RealNamedFake(
                responses=[
                    {"requirements": [_requirement(plec["extraction"].id, 1)]},
                    OutputTruncatedError("Limit.", usage=Usage(input_tokens=1, output_tokens=1)),
                ]
            ),
        )
    ).run
    assert partial.status == AnalysisRunStatus.PARTIAL and not partial.is_current
    await _refused(session, company, tender, partial, user)  # no demanat: no
    session.add(_requested(company, user, tender, partial))
    await session.flush()
    await adopt_requirement_run(
        session, company_id=company.id, tender_id=tender.id, run_id=partial.id, user_id=user.id
    )
    assert (await _ids(session, tender.id, company.id))[0] == partial.id

    # Un COMPLETED real que va quedar `input_changed` neix obsolet: no s'adopta.
    changed = (
        await analyze_tender_requirements(
            session, tender, provider=RealNamedFake(model="claude-test-model-3")
        )
    ).run
    changed.is_current = False
    changed.error_code = "input_changed"
    session.add(_requested(company, user, tender, changed))
    await session.flush()
    with pytest.raises(ConflictError) as caught:
        await adopt_requirement_run(
            session, company_id=company.id, tender_id=tender.id, run_id=changed.id, user_id=user.id
        )
    assert caught.value.code == "requirement_run_not_adoptable"


async def test_adoptar_en_una_licitacio_publica_no_escriu_cap_fila_global(
    session, tender_document, tenant_a
):
    """I5: la promocio canonica ja ha portat les anotacions; un reinici de l'operador a
    `unknown` sobre la run nova sobreviu a l'adopcio d'una empresa."""
    user, company = tenant_a
    plec = await _add_extracted_pages(session, tender_document, pages=2)
    tender = await session.get(Tender, tender_document.tender_id)
    r1 = await _real(session, tender, plec["extraction"].id, 1)
    old = await session.scalar(sa.select(Requirement).where(Requirement.run_id == r1.id))
    old.timing = RequirementTiming.AT_SUBMISSION  # anotacio de l'operador
    await adopt_requirement_run(
        session, company_id=company.id, tender_id=tender.id, run_id=r1.id, user_id=user.id
    )
    await session.commit()

    r2 = await _real(session, tender, plec["extraction"].id, 1, model="claude-test-model-2")
    new = await session.scalar(sa.select(Requirement).where(Requirement.run_id == r2.id))
    assert new.dedup_key == old.dedup_key
    assert new.timing == RequirementTiming.AT_SUBMISSION  # carry-over de la promocio
    new.timing = RequirementTiming.UNKNOWN  # reinici explicit de l'operador
    await session.commit()

    adoption = await adopt_requirement_run(
        session, company_id=company.id, tender_id=tender.id, run_id=r2.id, user_id=user.id
    )
    await session.commit()
    assert adoption.previous_run_id == r1.id
    await session.refresh(new)
    assert new.timing == RequirementTiming.UNKNOWN


async def test_un_run_d_una_altra_licitacio_no_es_troba(session, two_runs, tenant_a):
    tender, _plec, r1 = two_runs
    user, company = tenant_a
    other = Tender(source_id=tender.source_id, external_id="altre", title="Altra")
    session.add(other)
    await session.flush()
    with pytest.raises(NotFoundError):
        await adopt_requirement_run(
            session, company_id=company.id, tender_id=other.id, run_id=r1.id, user_id=user.id
        )


# --------------------------------------------------------------------------- #
# I3: la fitxa de requisits llegeix el punter, i l'adopcio per HTTP             #
# --------------------------------------------------------------------------- #


def _read_url(tender_id) -> str:
    return f"/api/v1/tenders/{tender_id}/analysis/requirements"


def _adopt_url(tender_id) -> str:
    return f"/api/v1/tenders/{tender_id}/analysis/requirements/adopt"


async def test_la_fitxa_llegeix_el_punter_i_s_hi_adopta_el_canonic(
    session, two_runs, tenant_a, client_a
):
    tender, plec, r1 = two_runs
    await session.commit()
    adopted = await client_a.post(_adopt_url(tender.id), json={"run_id": str(r1.id)})
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["effective_run_id"] == str(r1.id)
    r2 = await _real(session, tender, plec["extraction"].id, 2, model="claude-test-model-2")
    await session.commit()

    body = (await client_a.get(_read_url(tender.id))).json()
    assert body["run"]["id"] == str(r1.id)
    assert (body["effective_run_id"], body["canonical_run_id"], body["adoption_available"]) == (
        str(r1.id),
        str(r2.id),
        True,
    )

    adopted = await client_a.post(_adopt_url(tender.id), json={"run_id": str(r2.id)})
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["previous_run_id"] == str(r1.id)
    body = (await client_a.get(_read_url(tender.id))).json()
    assert (body["run"]["id"], body["adoption_available"]) == (str(r2.id), False)


async def test_un_rol_de_lectura_no_pot_adoptar(session, two_runs, tenant_a, client_a):
    from tests.test_company_knowledge_permissions_api import _role

    tender, _plec, r1 = two_runs
    user, company = tenant_a
    await session.commit()
    await _role(session, user, company, "viewer")
    refused = await client_a.post(_adopt_url(tender.id), json={"run_id": str(r1.id)})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "permission_denied"
    assert await session.get(CompanyTenderRequirementRun, (company.id, tender.id)) is None
    assert (await client_a.get(_read_url(tender.id))).status_code == 200


async def test_una_empresa_no_mou_ni_llegeix_el_punter_d_una_altra(
    session, two_runs, tenant_a, tenant_b, client_a, client_b
):
    tender, plec, r1 = two_runs
    (user_a, company_a), (_user_b, company_b) = tenant_a, tenant_b
    await adopt_requirement_run(
        session, company_id=company_a.id, tender_id=tender.id, run_id=r1.id, user_id=user_a.id
    )
    r2 = await _real(session, tender, plec["extraction"].id, 2, model="claude-test-model-2")
    await session.commit()

    body_b = (await client_b.get(_read_url(tender.id))).json()
    assert (body_b["effective_run_id"], body_b["adoption_available"]) == (str(r2.id), False)
    # A no pot fer-se passar per B, ni per llegir ni per escriure.
    spoofed_read = await client_a.get(
        _read_url(tender.id), params={"company_id": str(company_b.id)}
    )
    assert spoofed_read.status_code == 404
    spoofed = await client_a.post(
        _adopt_url(tender.id), json={"run_id": str(r2.id), "company_id": str(company_b.id)}
    )
    assert spoofed.status_code == 404
    # B no pot adoptar la run superada que A te acceptada (ni l'ha demanada ni es vigent).
    refused = await client_b.post(_adopt_url(tender.id), json={"run_id": str(r1.id)})
    assert refused.status_code == 409
    assert await session.get(CompanyTenderRequirementRun, (company_b.id, tender.id)) is None
    pointer_a = await session.get(
        CompanyTenderRequirementRun, (company_a.id, tender.id), populate_existing=True
    )
    assert pointer_a.run_id == r1.id
