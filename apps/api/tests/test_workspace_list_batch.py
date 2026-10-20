"""`GET /workspaces` per lots (P2-09): la mateixa resposta amb un nombre fix de consultes.

Abans cada fila feia `_summary` (`load_state` + organisme + estat de l'analisi):
unes 20 sentencies per candidatura. Ara la llista es llegeix per lots i la
derivacio es la mateixa (`outdated_reasons`, `effective_deadline`...).

Dues invariants:

* **igualtat exacta**: el JSON de la llista nova es el de `_summary` cridat
  candidatura a candidatura (el cami antic, que es conserva i es la referencia),
  sobre un fixture que cobreix cada branca de la derivacio;
* **consultes fixes**: el nombre de sentencies no depen de N. L'unica part que
  encara va per run es `run_is_outdated` (l'empremta del corpus d'ara, document a
  document): es mesura a part i el test ho fa explicit.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.api.v1.routers import workspaces as workspaces_router
from app.models.company_runs import CompanyTenderRequirementRun
from app.models.compliance import CompanyComplianceRun
from app.models.enums import (
    AnalysisRunStatus,
    ComplianceRunStatus,
    EligibilityResult,
    MatchStatus,
    RecommendationResult,
    RequirementCategory,
    RequirementObligation,
    RequirementOrigin,
    SourceType,
    TenderDocumentType,
    TenderStatus,
    WorkspaceDecisionType,
    WorkspaceStatus,
)
from app.models.matching import CompanyTenderMatch
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Buyer, Tender, TenderDocument, TenderLot
from app.models.workspace import BidWorkspace, WorkspaceDecision
from app.services.requirement_analysis import run_is_outdated

LIST = "/api/v1/companies/{company}/workspaces"

NOW = datetime.now(UTC)
# Lluny de qualsevol frontera de dia: els dos camins calculen `days_remaining` ara.
FUTURE = NOW + timedelta(days=20, hours=6)
PAST = NOW - timedelta(days=3, hours=6)


# --------------------------------------------------------------------------- #
# Constructors                                                                  #
# --------------------------------------------------------------------------- #


async def make_source(db) -> Source:
    source = Source(
        code=f"lot-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    return source


async def make_buyer(db, source) -> Buyer:
    buyer = Buyer(
        source_id=source.id,
        external_identifier=f"org-{uuid.uuid4().hex[:8]}",
        name="Ajuntament de Proves",
    )
    db.add(buyer)
    await db.flush()
    return buyer


async def make_tender(
    db, source, *, title, deadline, buyer=None, status=TenderStatus.OPEN
) -> Tender:
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number=f"EXP-{uuid.uuid4().hex[:6]}",
        title=title,
        deadline_at=deadline,
        buyer_id=buyer.id if buyer else None,
        status=status,
    )
    db.add(tender)
    await db.flush()
    return tender


async def make_lot(db, tender, number, *, deadline=None, removed=False) -> TenderLot:
    lot = TenderLot(
        tender_id=tender.id,
        external_lot_id=str(number),
        lot_number=number,
        title=f"Lot {number}",
        deadline_at=deadline,
        removed_at=NOW if removed else None,
    )
    db.add(lot)
    await db.flush()
    return lot


async def make_revision(db, tender, number) -> TenderRevision:
    revision = TenderRevision(
        tender_id=tender.id,
        revision_number=number,
        normalized_snapshot={"n": number},
        normalized_hash=(uuid.uuid4().hex * 2)[:64],
        trigger="test",
    )
    db.add(revision)
    await db.flush()
    return revision


async def make_event(db, tender, revision, impacts: list[str]) -> TenderChangeEvent:
    event = TenderChangeEvent(
        tender_id=tender.id,
        to_revision_id=revision.id,
        change_code="documents_changed",
        impacts=impacts,
        is_material=True,
    )
    db.add(event)
    await db.flush()
    return event


async def make_requirement_run(
    db, tender, *, revision=None, current=True, chunk_coverage=None
) -> RequirementExtractionRun:
    run = RequirementExtractionRun(
        tender_id=tender.id,
        tender_revision_id=revision.id if revision else None,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="fake",
        model="fake-model-v1",
        # Fila sintetica vigent: a PostgreSQL nomes un run real/gold ho pot ser (0061).
        run_basis="real",
        is_current=current,
        chunk_coverage=chunk_coverage,
    )
    db.add(run)
    await db.flush()
    return run


async def make_compliance(
    db,
    company,
    tender,
    *,
    revision=None,
    current=True,
    result=EligibilityResult.ELIGIBLE,
    recommendation=RecommendationResult.GO_CANDIDATE,
) -> CompanyComplianceRun:
    run = CompanyComplianceRun(
        company_id=company.id,
        tender_id=tender.id,
        tender_revision_id=revision.id if revision else None,
        status=ComplianceRunStatus.COMPLETED,
        rules_version="compliance-test",
        company_profile_fingerprint="p" * 64,
        requirements_fingerprint="r" * 64,
        overall_result=result,
        recommendation=recommendation,
        is_current=current,
    )
    db.add(run)
    await db.flush()
    return run


async def make_match(db, company, tender, *, score, revision=None) -> CompanyTenderMatch:
    match = CompanyTenderMatch(
        company_id=company.id,
        tender_id=tender.id,
        score=score,
        status=MatchStatus.CANDIDATE,
        data_coverage=1.0,
        factors=[],
        rules_version="rules-test",
        calculated_at=NOW,
        tender_revision_id=revision.id if revision else None,
    )
    db.add(match)
    await db.flush()
    return match


async def make_workspace(
    db,
    company,
    tender,
    user,
    *,
    created_at,
    status=WorkspaceStatus.ANALYSIS,
    lot=None,
    compliance=None,
    requirement=None,
    prefit_score=None,
) -> BidWorkspace:
    workspace = BidWorkspace(
        company_id=company.id,
        tender_id=tender.id,
        tender_lot_id=lot.id if lot else None,
        status=status,
        current_compliance_run_id=compliance.id if compliance else None,
        current_requirement_run_id=requirement.id if requirement else None,
        prefit_score=prefit_score,
        deadline_at=tender.deadline_at,
        created_by=user.id,
        created_at=created_at,
    )
    db.add(workspace)
    await db.flush()
    return workspace


async def decide(db, workspace, user, decision, *, at) -> WorkspaceDecision:
    record = WorkspaceDecision(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        decision=decision,
        decided_by=user.id,
        decided_at=at,
        reason="Decisio de prova",
    )
    db.add(record)
    await db.flush()
    return record


@contextmanager
def counting(engine):
    """Compta les sentencies SQL que passen pel motor de test."""
    statements: list[str] = []

    def before(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    target = engine.sync_engine
    sa.event.listen(target, "before_cursor_execute", before)
    try:
        yield statements
    finally:
        sa.event.remove(target, "before_cursor_execute", before)


async def reference(session_factory, company_id, *, limit=50) -> list[dict]:
    """El cami antic: `_summary` (`load_state`) candidatura a candidatura, sessio nova."""
    async with session_factory() as session:
        workspaces = (
            await session.scalars(
                sa.select(BidWorkspace)
                .where(BidWorkspace.company_id == company_id)
                .order_by(BidWorkspace.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            (await workspaces_router._summary(session, workspace)).model_dump(mode="json")
            for workspace in workspaces
        ]


# --------------------------------------------------------------------------- #
# Fixture ric                                                                   #
# --------------------------------------------------------------------------- #


async def rich_fixture(db, tenant_a, tenant_b) -> dict[str, BidWorkspace]:
    """Set candidatures d'A, i una de B sobre la mateixa licitacio que una d'A.

    Cada una exerceix una branca de la derivacio: sense res; runs vigents; analisi
    endarrerida i run caducada; lots (un de retirat) amb punter de l'empresa;
    estats tancats; compliment que falta; licitacio anul.lada amb requisits manuals.
    """
    user_a, company_a = tenant_a
    user_b, company_b = tenant_b
    source = await make_source(db)
    buyer = await make_buyer(db, source)
    when = [NOW - timedelta(hours=10 - index) for index in range(10)]
    ws: dict[str, BidWorkspace] = {}

    # 1. Sense res: ni organisme, ni termini, ni runs, ni pre-fit, ni decisio.
    bare = await make_tender(db, source, title="Sense res", deadline=None)
    ws["bare"] = await make_workspace(db, company_a, bare, user_a, created_at=when[0])

    # 2. Runs vigents al dia, pre-fit, i dues decisions (la vigent es la mes recent).
    current = await make_tender(db, source, title="Al dia", deadline=FUTURE, buyer=buyer)
    rev = await make_revision(db, current, 1)
    run2 = await make_requirement_run(db, current, revision=rev)
    compliance2 = await make_compliance(db, company_a, current, revision=rev)
    await make_match(db, company_a, current, score=81, revision=rev)
    ws["current"] = await make_workspace(
        db,
        company_a,
        current,
        user_a,
        created_at=when[1],
        status=WorkspaceStatus.GO_APPROVED,
        compliance=compliance2,
        requirement=run2,
    )
    await decide(db, ws["current"], user_a, WorkspaceDecisionType.DEFER, at=NOW - timedelta(days=2))
    await decide(db, ws["current"], user_a, WorkspaceDecisionType.GO, at=NOW - timedelta(days=1))
    # La de B sobre la mateixa licitacio: no ha de sortir mai a la llista d'A.
    other = await make_workspace(db, company_b, current, user_b, created_at=when[9])
    await decide(db, other, user_b, WorkspaceDecisionType.NO_GO, at=NOW)

    # 3. Plec canviat despres de l'analisi (requisits i compliment endarrerits), run
    #    que cita un document retirat (caducada), NO-GO aprovat, termini passat.
    changed = await make_tender(db, source, title="Plec canviat", deadline=PAST, buyer=buyer)
    old = await make_revision(db, changed, 1)
    new = await make_revision(db, changed, 2)
    await make_event(db, changed, new, ["requirements_affecting", "compliance_affecting"])
    removed = TenderDocument(
        tender_id=changed.id,
        name="PPT-retirat.pdf",
        document_type=TenderDocumentType.OTHER,
        url="https://example.com/ppt.pdf",
        removed_at=NOW,
    )
    db.add(removed)
    await db.flush()
    run3 = await make_requirement_run(
        db,
        changed,
        revision=old,
        chunk_coverage={"chunks": [{"tender_document_id": str(removed.id)}]},
    )
    compliance3 = await make_compliance(
        db,
        company_a,
        changed,
        revision=old,
        result=EligibilityResult.NO_GO,
        recommendation=RecommendationResult.NO_GO,
    )
    ws["changed"] = await make_workspace(
        db,
        company_a,
        changed,
        user_a,
        created_at=when[2],
        status=WorkspaceStatus.NO_GO_APPROVED,
        compliance=compliance3,
        requirement=run3,
    )
    await decide(db, ws["changed"], user_a, WorkspaceDecisionType.NO_GO, at=NOW)

    # 4-5. Dos lots de la mateixa licitacio (termini passat a l'expedient, futur al
    #      lot 1; el lot 2 retirat), punter de l'empresa a una run no canonica i
    #      pre-fit endarrerit per un canvi `prefit_affecting`.
    lots = await make_tender(db, source, title="Amb lots", deadline=PAST, buyer=buyer)
    first = await make_revision(db, lots, 1)
    second = await make_revision(db, lots, 2)
    await make_event(db, lots, second, ["prefit_affecting"])
    await make_requirement_run(db, lots, revision=second)  # la canonica
    pointed = await make_requirement_run(db, lots, revision=second, current=False)
    db.add(
        CompanyTenderRequirementRun(
            company_id=company_a.id, tender_id=lots.id, run_id=pointed.id, accepted_by=user_a.id
        )
    )
    await make_match(db, company_a, lots, score=64, revision=first)
    lot1 = await make_lot(db, lots, 1, deadline=FUTURE)
    lot2 = await make_lot(db, lots, 2, removed=True)
    ws["lot_open"] = await make_workspace(
        db,
        company_a,
        lots,
        user_a,
        created_at=when[3],
        status=WorkspaceStatus.AWAITING_DECISION,
        lot=lot1,
        requirement=pointed,
    )
    await decide(db, ws["lot_open"], user_a, WorkspaceDecisionType.DEFER, at=NOW)
    ws["lot_removed"] = await make_workspace(
        db,
        company_a,
        lots,
        user_a,
        created_at=when[4],
        status=WorkspaceStatus.PREPARING_BID,
        lot=lot2,
    )
    await decide(db, ws["lot_removed"], user_a, WorkspaceDecisionType.GO, at=NOW)

    # 6. Presentada, amb un compliment vigent que no es el del workspace i el
    #    pre-fit capturat en obrir (cap pre-fit d'ara).
    submitted = await make_tender(db, source, title="Presentada", deadline=PAST)
    await make_compliance(
        db,
        company_a,
        submitted,
        result=EligibilityResult.REVIEW_REQUIRED,
        recommendation=RecommendationResult.REVIEW,
    )
    ws["submitted"] = await make_workspace(
        db,
        company_a,
        submitted,
        user_a,
        created_at=when[5],
        status=WorkspaceStatus.SUBMITTED,
        prefit_score=55,
    )
    await decide(db, ws["submitted"], user_a, WorkspaceDecisionType.GO, at=NOW)

    # 7. Retirada, licitacio anul.lada, compliment que ja no es vigent (falta) i
    #    requisits escrits a ma (analisi sense run).
    cancelled = await make_tender(
        db, source, title="Anul.lada", deadline=FUTURE, status=TenderStatus.CANCELLED
    )
    stale_compliance = await make_compliance(db, company_a, cancelled, current=False)
    db.add(
        Requirement(
            tender_id=cancelled.id,
            run_id=None,
            origin=RequirementOrigin.MANUAL,
            category=RequirementCategory.TECHNICAL_REQUIREMENT,
            obligation=RequirementObligation.MANDATORY,
            title="Escrit a ma",
            dedup_key=uuid.uuid4().hex,
        )
    )
    ws["withdrawn"] = await make_workspace(
        db,
        company_a,
        cancelled,
        user_a,
        created_at=when[6],
        status=WorkspaceStatus.WITHDRAWN,
        compliance=stale_compliance,
    )
    await db.commit()
    return ws


# --------------------------------------------------------------------------- #
# Igualtat exacta                                                               #
# --------------------------------------------------------------------------- #


async def test_la_llista_per_lots_es_identica_al_cami_per_candidatura(
    db, client_a, tenant_a, tenant_b, session_factory
):
    _, company = tenant_a
    await rich_fixture(db, tenant_a, tenant_b)

    expected = await reference(session_factory, company.id)
    response = await client_a.get(LIST.format(company=company.id))
    assert response.status_code == 200, response.text

    assert response.json() == expected
    # El fixture cobreix el que diu cobrir: si no, la igualtat no demostraria res.
    assert len(expected) == 7
    assert {row["company_id"] for row in expected} == {str(company.id)}
    assert {row["analysis_state"] for row in expected} == {"absent", "current", "stale"}
    assert {row["human_decision"] for row in expected} == {None, "go", "no_go", "defer"}
    assert {row["status"] for row in expected} >= {"submitted", "withdrawn", "no_go_approved"}
    assert {row["deadline_passed"] for row in expected} == {True, False}
    assert any(row["deadline_at"] is None for row in expected)
    assert any(row["buyer_name"] is None for row in expected)
    assert {row["analysis_outdated"] for row in expected} == {True, False}
    assert any(row["tender_lot_id"] for row in expected)
    assert {row["eligibility"] for row in expected} >= {None, "eligible", "no_go"}


async def test_el_limit_i_l_ordre_son_els_d_abans(
    db, client_a, tenant_a, tenant_b, session_factory
):
    _, company = tenant_a
    await rich_fixture(db, tenant_a, tenant_b)

    expected = await reference(session_factory, company.id, limit=3)
    response = await client_a.get(LIST.format(company=company.id), params={"limit": 3})
    assert response.status_code == 200, response.text
    assert response.json() == expected
    assert [row["tender_title"] for row in expected] == ["Anul.lada", "Presentada", "Amb lots"]


async def test_la_llista_d_una_empresa_sense_candidatures_es_buida(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.get(LIST.format(company=company.id))
    assert response.status_code == 200
    assert response.json() == []


# --------------------------------------------------------------------------- #
# Consultes fixes                                                               #
# --------------------------------------------------------------------------- #


async def _full_workspaces(db, tenant, count, *, with_run=False, offset=0) -> None:
    """`count` candidatures amb totes les peces que es llegeixen per lots.

    Licitacio amb organisme i lot, revisions i un canvi material, compliment,
    pre-fit, decisio; amb `with_run`, tambe una run de requisits vigent propia.
    """
    user, company = tenant
    source = await make_source(db)
    buyer = await make_buyer(db, source)
    for index in range(count):
        tender = await make_tender(
            db, source, title=f"Licitacio {offset + index}", deadline=FUTURE, buyer=buyer
        )
        first = await make_revision(db, tender, 1)
        second = await make_revision(db, tender, 2)
        await make_event(db, tender, second, ["prefit_affecting"])
        lot = await make_lot(db, tender, 1, deadline=FUTURE)
        compliance = await make_compliance(db, company, tender, revision=first)
        await make_match(db, company, tender, score=70, revision=first)
        run = await make_requirement_run(db, tender, revision=first) if with_run else None
        workspace = await make_workspace(
            db,
            company,
            tender,
            user,
            created_at=NOW - timedelta(minutes=offset + index),
            lot=lot,
            compliance=compliance,
            requirement=run,
        )
        await decide(db, workspace, user, WorkspaceDecisionType.GO, at=NOW)
    await db.commit()


async def _count_list(engine, client, company) -> int:
    with counting(engine) as statements:
        response = await client.get(LIST.format(company=company.id))
    assert response.status_code == 200, response.text
    return len(statements)


async def test_el_nombre_de_consultes_no_depen_de_n(db, engine, client_a, tenant_a):
    _, company = tenant_a
    await _full_workspaces(db, tenant_a, 1)
    one = await _count_list(engine, client_a, company)
    await _full_workspaces(db, tenant_a, 5, offset=1)
    six = await _count_list(engine, client_a, company)

    assert one == six, (one, six)
    # Fix i acotat: la peticio sencera (autenticacio inclosa), no 20 per candidatura.
    print(f"GET /workspaces: N=1 -> {one} SQL, N=6 -> {six} SQL")
    assert six <= 25, six


async def test_amb_runs_de_requisits_nomes_s_hi_afegeix_run_is_outdated(
    db, engine, client_a, tenant_a, session_factory
):
    """L'estat de l'analisi d'una run (`run_is_outdated`) es l'unica part per run.

    La resta de la llista es fixa: la diferencia entre N=6 i N=1 es exactament
    cinc vegades el cost de `run_is_outdated` d'una run.
    """
    _, company = tenant_a
    await _full_workspaces(db, tenant_a, 1, with_run=True)
    one = await _count_list(engine, client_a, company)
    await _full_workspaces(db, tenant_a, 5, with_run=True, offset=1)
    six = await _count_list(engine, client_a, company)

    async with session_factory() as session:
        run = await session.scalar(
            sa.select(RequirementExtractionRun)
            .where(RequirementExtractionRun.is_current.is_(True))
            .limit(1)
        )
        with counting(engine) as statements:
            assert await run_is_outdated(session, run) is False
        per_run = len(statements)

    print(f"GET /workspaces amb runs: N=1 -> {one}, N=6 -> {six}, run_is_outdated -> {per_run}")
    assert per_run > 0
    assert six - one == 5 * per_run, (one, six, per_run)
