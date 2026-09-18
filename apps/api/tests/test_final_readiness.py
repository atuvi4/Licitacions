"""La porta final: dossier, solucio tecnica i oferta economica alhora.

La pregunta que sosté tot el fitxer: **zero solucions tecniques, que vol dir?**
Sense que ningu ho hagi mirat, no vol dir res —i per tant no pot voler dir "tot
correcte"—; amb una revisio humana vigent, vol dir que ningu no n'ha necessitat
cap (C-14). El sistema no ho pot deduir sol perque C-11 li ho prohibeix.

I una segona: **una sola porta**. Un dossier impecable amb l'oferta a mitges no
es una candidatura a punt.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import (
    AnalysisRunStatus,
    ExtractionConfidence,
    MembershipRole,
    PricingBasis,
    RequirementCategory,
    RequirementObligation,
    RequirementScope,
    ReviewStatus,
    SourceType,
    TechnicalSolutionSource,
    TechnicalSolutionStatus,
    WorkspaceReadiness,
)
from app.models.identity import CompanyMembership
from app.models.offer import EconomicOffer
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.submission import SubmissionPackage
from app.models.tenders import Tender, TenderLot
from app.models.workspace import BidWorkspace, WorkspaceTechnicalSolution
from app.services import economic_offer as offer_service
from app.services import technical_scope as scope_service
from app.services import workspace_readiness as service
from app.workspace.dossier import ReadinessResult
from app.workspace.readiness import (
    BLOCKER,
    DOMAIN_ECONOMIC,
    DOMAIN_TECHNICAL,
    ECONOMIC_BID_LOTS_NOT_SELECTED,
    ECONOMIC_DURATION_MISSING,
    ECONOMIC_LOT_BUDGET_EXCEEDED,
    ECONOMIC_LOT_BUDGET_UNKNOWN,
    ECONOMIC_LOT_EMPTY,
    ECONOMIC_OFFER_NOT_STARTED,
    ECONOMIC_OFFER_PRICE_MISSING,
    ECONOMIC_UNIT_LIMIT_EXCEEDED,
    ECONOMIC_UNIT_LIMIT_UNKNOWN,
    ECONOMIC_VAT_MISSING,
    INFO,
    TECHNICAL_SCOPE_STALE,
    TECHNICAL_SCOPE_UNREVIEWED,
    TECHNICAL_SOLUTION_REQUIREMENT_MISSING,
    TECHNICAL_SOLUTION_UNVALIDATED,
    TECHNICAL_SOLUTION_VALIDATION_STALE,
    WARNING,
    Finding,
    evaluate_final,
)

# --------------------------------------------------------------------------- #
# Construccio                                                                   #
# --------------------------------------------------------------------------- #


async def _tender(db, *, budget=None, title="Renovacio de la xarxa") -> Tender:
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="12/2026",
        title=title,
        base_budget_without_tax=budget,
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


async def _run(db, tender) -> RequirementExtractionRun:
    run = await db.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == tender.id
        )
    )
    if run is not None:
        return run
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="fake",
        model="fake",
        is_current=True,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _requirement(db, tender, *, tender_lot_id=None, title="Centraleta") -> Requirement:
    run = await _run(db, tender)
    requirement = Requirement(
        run_id=run.id,
        tender_id=tender.id,
        tender_lot_id=tender_lot_id,
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.MANDATORY,
        scope=RequirementScope.LOT if tender_lot_id else RequirementScope.TENDER,
        title=title,
        confidence=ExtractionConfidence.HIGH,
        review_status=ReviewStatus.UNREVIEWED,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


async def _lots(db, tender, *, budgets=(None, None)) -> list[TenderLot]:
    lots = [
        TenderLot(
            tender_id=tender.id,
            external_lot_id=str(n),
            lot_number=n,
            title=f"Lot numero {n}",
            base_budget_without_tax=budget,
        )
        for n, budget in enumerate(budgets, start=1)
    ]
    db.add_all(lots)
    await db.commit()
    for lot in lots:
        await db.refresh(lot)
    return lots


async def _workspace(db, company, tender, *, tender_lot_id=None) -> BidWorkspace:
    workspace = BidWorkspace(
        company_id=company.id, tender_id=tender.id, tender_lot_id=tender_lot_id
    )
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)
    return workspace


@pytest.fixture
async def workspace(db, tenant_a):
    """Cas B: plec sense lots."""
    _user, company = tenant_a
    return await _workspace(db, company, await _tender(db))


@pytest.fixture
async def user(tenant_a):
    return tenant_a[0]


async def _offer_with_line(db, workspace, user, **line):
    await offer_service.create_offer(db, workspace, user=user)
    offer = await offer_service.get_offer(db, workspace)
    lot = offer.lots[0]
    section = await offer_service.create_section(
        db, offer, lot, title="Oferta", expected_revision=offer.revision, user=user
    )
    await db.refresh(offer)
    dades = {
        "concept": "Servei",
        "quantity": Decimal("1"),
        "pricing_basis": PricingBasis.ONE_OFF,
        "offered_unit_price_ex_vat": Decimal("1000"),
        "vat_rate": Decimal("21"),
    }
    dades.update(line)
    await offer_service.create_line(
        db, workspace, offer, section, expected_revision=offer.revision, user=user, **dades
    )
    return await offer_service.get_offer(db, workspace)


async def _solution(db, workspace, *, requirement=None, status=None, proposed=None):
    solution = WorkspaceTechnicalSolution(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        requirement_id=requirement.id if requirement else None,
        source=(
            TechnicalSolutionSource.REQUIREMENT
            if requirement
            else TechnicalSolutionSource.MANUAL
        ),
        title="Centraleta amb dos nodes",
        proposed_solution=proposed,
        status=status or TechnicalSolutionStatus.DRAFTING,
    )
    db.add(solution)
    await db.commit()
    await db.refresh(solution)
    return solution


def codes(findings, domain=None) -> list[str]:
    return [f.code for f in findings if domain is None or f.domain == domain]


async def technical(db, workspace) -> list:
    return await service.technical_findings(db, workspace)


async def economic(db, workspace) -> list:
    return await service.economic_findings(db, workspace)


def url(workspace, *parts) -> str:
    base = f"/api/v1/companies/{workspace.company_id}/workspaces/{workspace.id}"
    return "/".join([base, *parts])


async def set_role(db, tenant, role) -> None:
    usuari, company = tenant
    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == usuari.id,
            CompanyMembership.company_id == company.id,
        )
        .values(role=role)
    )
    await db.commit()


# --------------------------------------------------------------------------- #
# 1-9. C-14: la revisio de l'abast                                              #
# --------------------------------------------------------------------------- #


async def test_sense_revisar_l_abast_zero_solucions_no_vol_dir_res(db, workspace):
    """El cas que motiva tota la decisio C-14."""
    findings = await technical(db, workspace)

    assert TECHNICAL_SCOPE_UNREVIEWED in codes(findings)


async def test_amb_l_abast_revisat_zero_solucions_es_una_resposta(db, workspace, user):
    await scope_service.confirm(db, workspace, user=user)

    findings = await technical(db, workspace)

    assert codes(findings) == []


async def test_assenyalar_un_requisit_despres_de_revisar_caduca_la_revisio(
    db, workspace, user
):
    """La seleccio humana forma part de l'abast: si no, es podria confirmar "ho
    he mirat tot" i despres afegir feina sense que ningu se n'assabentes."""
    requirement = await _requirement(db, await db.get(Tender, workspace.tender_id))
    await scope_service.confirm(db, workspace, user=user)

    await _solution(db, workspace, requirement=requirement)

    assert TECHNICAL_SCOPE_STALE in codes(await technical(db, workspace))


async def test_canviar_el_que_demana_un_requisit_caduca_la_revisio(db, workspace, user):
    """El cas que `requirements_fingerprint(run)` no detectaria."""
    requirement = await _requirement(db, await db.get(Tender, workspace.tender_id))
    await scope_service.confirm(db, workspace, user=user)

    await db.execute(
        sa.update(Requirement)
        .where(Requirement.id == requirement.id)
        .values(title="Ara demanem una altra cosa")
    )
    await db.commit()

    assert TECHNICAL_SCOPE_STALE in codes(await technical(db, workspace))


async def test_un_requisit_nou_caduca_la_revisio(db, workspace, user):
    tender = await db.get(Tender, workspace.tender_id)
    await _requirement(db, tender)
    await scope_service.confirm(db, workspace, user=user)

    await _requirement(db, tender, title="Un requisit que abans no hi era")

    assert TECHNICAL_SCOPE_STALE in codes(await technical(db, workspace))


async def test_reescriure_una_proposta_no_caduca_l_abast(db, workspace, user):
    """L'abast es "quins requisits", no "que hi hem escrit"."""
    requirement = await _requirement(db, await db.get(Tender, workspace.tender_id))
    solution = await _solution(db, workspace, requirement=requirement)
    await scope_service.confirm(db, workspace, user=user)

    solution.proposed_solution = "Una altra manera de fer-ho"
    await db.commit()

    assert TECHNICAL_SCOPE_STALE not in codes(await technical(db, workspace))


async def test_reescriure_una_proposta_si_que_caduca_la_seva_validacio(
    db, workspace, user, tenant_a
):
    """La vigencia de la validacio i la de l'abast son dues coses diferents."""
    from app.services import technical_solution

    requirement = await _requirement(db, await db.get(Tender, workspace.tender_id))
    solution = await _solution(db, workspace, requirement=requirement, proposed="Dos nodes")
    await technical_solution.validate_solution(
        db, workspace, solution, note="Revisat", user=user
    )
    await scope_service.confirm(db, workspace, user=user)

    solution.proposed_solution = "Una altra cosa"
    await db.commit()

    findings = await technical(db, workspace)
    assert TECHNICAL_SOLUTION_VALIDATION_STALE in codes(findings)
    assert TECHNICAL_SCOPE_STALE not in codes(findings)


async def test_tornar_a_revisar_actualitza_l_empremta(db, workspace, user):
    tender = await db.get(Tender, workspace.tender_id)
    await scope_service.confirm(db, workspace, user=user)
    primera = workspace.technical_scope_fingerprint
    await _requirement(db, tender)

    await scope_service.confirm(db, workspace, user=user)

    assert workspace.technical_scope_fingerprint != primera
    assert codes(await technical(db, workspace)) == []


async def test_tornar_a_revisar_el_mateix_abast_no_es_un_fet_nou(db, workspace, user):
    await scope_service.confirm(db, workspace, user=user)
    quan = workspace.technical_scope_reviewed_at

    _ws, changed = await scope_service.confirm(db, workspace, user=user)

    assert changed is False
    assert workspace.technical_scope_reviewed_at == quan


# --------------------------------------------------------------------------- #
# 10-17. C-16: l'abast amb lots                                                 #
# --------------------------------------------------------------------------- #


async def test_una_candidatura_de_lot_veu_el_seu_lot_i_els_generals(db, tenant_a):
    _user, company = tenant_a
    tender = await _tender(db)
    lots = await _lots(db, tender)
    workspace = await _workspace(db, company, tender, tender_lot_id=lots[0].id)
    general = await _requirement(db, tender, title="General")
    seu = await _requirement(db, tender, tender_lot_id=lots[0].id, title="Del lot 1")
    alie = await _requirement(db, tender, tender_lot_id=lots[1].id, title="Del lot 2")

    scope = await scope_service.resolve(db, workspace)

    dins = {r.id for r in scope.requirements}
    assert general.id in dins
    assert seu.id in dins
    assert alie.id not in dins


async def test_una_candidatura_global_veu_els_lots_que_ofereix(db, tenant_a, user):
    """C-16: els lots als quals ens presentem els diu l'oferta economica."""
    _user, company = tenant_a
    tender = await _tender(db)
    lots = await _lots(db, tender)
    workspace = await _workspace(db, company, tender)
    general = await _requirement(db, tender, title="General")
    del_lot_1 = await _requirement(db, tender, tender_lot_id=lots[0].id, title="Del lot 1")
    del_lot_2 = await _requirement(db, tender, tender_lot_id=lots[1].id, title="Del lot 2")
    await offer_service.create_offer(db, workspace, user=user)
    offer = await offer_service.get_offer(db, workspace)
    await offer_service.add_lot_from_tender_lot(
        db, workspace, offer, tender_lot_id=lots[0].id, expected_revision=offer.revision, user=user
    )

    scope = await scope_service.resolve(db, workspace)

    dins = {r.id for r in scope.requirements}
    assert general.id in dins
    assert del_lot_1.id in dins
    assert del_lot_2.id not in dins


async def test_arxivar_un_lot_ofert_caduca_la_revisio_de_l_abast(db, tenant_a, user):
    _user, company = tenant_a
    tender = await _tender(db)
    lots = await _lots(db, tender)
    workspace = await _workspace(db, company, tender)
    await offer_service.create_offer(db, workspace, user=user)
    offer = await offer_service.get_offer(db, workspace)
    lot, _ = await offer_service.add_lot_from_tender_lot(
        db, workspace, offer, tender_lot_id=lots[0].id, expected_revision=offer.revision, user=user
    )
    await db.refresh(offer)
    await offer_service.add_lot_from_tender_lot(
        db, workspace, offer, tender_lot_id=lots[1].id, expected_revision=offer.revision, user=user
    )
    await scope_service.confirm(db, workspace, user=user)

    await db.refresh(offer)
    await offer_service.archive_lot(
        db, workspace, offer, lot, expected_revision=offer.revision, user=user
    )

    assert TECHNICAL_SCOPE_STALE in codes(await technical(db, workspace))


async def test_sense_lots_triats_l_abast_no_es_pot_confirmar(db, tenant_a, user):
    """No es un abast buit: es un abast desconegut."""
    _user, company = tenant_a
    tender = await _tender(db)
    await _lots(db, tender)
    workspace = await _workspace(db, company, tender)

    with pytest.raises(scope_service.ScopeNotConfirmableError):
        await scope_service.confirm(db, workspace, user=user)


async def test_no_es_pot_definir_una_solucio_per_a_un_lot_que_no_oferim(
    db, client_a, tenant_a, user
):
    """S3 i S6 comparteixen la MATEIXA regla d'abast."""
    _user, company = tenant_a
    tender = await _tender(db)
    lots = await _lots(db, tender)
    workspace = await _workspace(db, company, tender)
    await offer_service.create_offer(db, workspace, user=user)
    offer = await offer_service.get_offer(db, workspace)
    await offer_service.add_lot_from_tender_lot(
        db, workspace, offer, tender_lot_id=lots[0].id, expected_revision=offer.revision, user=user
    )
    alie = await _requirement(db, tender, tender_lot_id=lots[1].id, title="Del lot 2")
    general = await _requirement(db, tender, title="General")

    rebutjat = await client_a.post(
        url(workspace, "technical-solutions", "from-requirement"),
        json={"requirement_id": str(alie.id)},
    )
    acceptat = await client_a.post(
        url(workspace, "technical-solutions", "from-requirement"),
        json={"requirement_id": str(general.id)},
    )

    assert rebutjat.status_code == 404
    assert acceptat.status_code == 200, acceptat.text


# --------------------------------------------------------------------------- #
# 18-25. Solucions tecniques                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status",
    [
        TechnicalSolutionStatus.NOT_STARTED,
        TechnicalSolutionStatus.DRAFTING,
        TechnicalSolutionStatus.NEEDS_VALIDATION,
    ],
)
async def test_una_solucio_sense_validar_bloqueja(db, workspace, user, status):
    requirement = await _requirement(db, await db.get(Tender, workspace.tender_id))
    await _solution(db, workspace, requirement=requirement, status=status)
    await scope_service.confirm(db, workspace, user=user)

    assert TECHNICAL_SOLUTION_UNVALIDATED in codes(await technical(db, workspace))


async def test_una_solucio_validada_i_vigent_no_bloqueja(db, workspace, user):
    from app.services import technical_solution

    requirement = await _requirement(db, await db.get(Tender, workspace.tender_id))
    solution = await _solution(db, workspace, requirement=requirement, proposed="Dos nodes")
    await technical_solution.validate_solution(
        db, workspace, solution, note="Revisat", user=user
    )
    await scope_service.confirm(db, workspace, user=user)

    assert codes(await technical(db, workspace)) == []


async def test_una_solucio_manual_sense_validar_tambe_bloqueja(db, workspace, user):
    await _solution(db, workspace)
    await scope_service.confirm(db, workspace, user=user)

    assert TECHNICAL_SOLUTION_UNVALIDATED in codes(await technical(db, workspace))


async def test_una_solucio_fora_de_l_abast_no_bloqueja(db, tenant_a, user):
    """Es historia: el requisit que resolia ja no aplica a aquesta candidatura."""
    _user, company = tenant_a
    tender = await _tender(db)
    lots = await _lots(db, tender)
    workspace = await _workspace(db, company, tender, tender_lot_id=lots[0].id)
    alie = await _requirement(db, tender, tender_lot_id=lots[1].id, title="Del lot 2")
    await _solution(db, workspace, requirement=alie)
    await scope_service.confirm(db, workspace, user=user)

    assert codes(await technical(db, workspace)) == []


async def test_un_requisit_desaparegut_avisa_pero_no_bloqueja(db, workspace, user):
    """No es pot reparar —no hi ha ni arxivat ni reenllac— i bloquejar per aixo
    deixaria la candidatura en un estat sense sortida."""
    requirement = await _requirement(db, await db.get(Tender, workspace.tender_id))
    await _solution(db, workspace, requirement=requirement)
    await db.execute(sa.delete(Requirement).where(Requirement.id == requirement.id))
    await db.commit()
    await scope_service.confirm(db, workspace, user=user)

    findings = await technical(db, workspace)

    avisos = [f for f in findings if f.code == TECHNICAL_SOLUTION_REQUIREMENT_MISSING]
    assert avisos and avisos[0].severity == WARNING
    assert not [f for f in findings if f.severity == BLOCKER]


# --------------------------------------------------------------------------- #
# 26-37. Oferta economica                                                       #
# --------------------------------------------------------------------------- #


async def test_sense_oferta_no_es_pot_tancar(db, workspace):
    """C-15: regla de producte de la V1. L'absencia no vol dir mai "no cal"."""
    assert codes(await economic(db, workspace)) == [ECONOMIC_OFFER_NOT_STARTED]


async def test_sense_lots_triats_l_oferta_bloqueja(db, tenant_a, user):
    _user, company = tenant_a
    tender = await _tender(db)
    await _lots(db, tender)
    workspace = await _workspace(db, company, tender)
    await offer_service.create_offer(db, workspace, user=user)

    assert codes(await economic(db, workspace)) == [ECONOMIC_BID_LOTS_NOT_SELECTED]


async def test_un_lot_sense_linies_bloqueja(db, workspace, user):
    await offer_service.create_offer(db, workspace, user=user)

    assert ECONOMIC_LOT_EMPTY in codes(await economic(db, workspace))


async def test_una_linia_sense_preu_bloqueja(db, workspace, user):
    await _offer_with_line(db, workspace, user, offered_unit_price_ex_vat=None)

    assert ECONOMIC_OFFER_PRICE_MISSING in codes(await economic(db, workspace))


async def test_una_linia_mensual_sense_durada_bloqueja(db, workspace, user):
    """C-3: sense durada el total es indeterminat, i no es pot presentar."""
    await _offer_with_line(db, workspace, user, pricing_basis=PricingBasis.MONTHLY)

    assert ECONOMIC_DURATION_MISSING in codes(await economic(db, workspace))


async def test_superar_el_maxim_unitari_bloqueja(db, workspace, user):
    await _offer_with_line(
        db,
        workspace,
        user,
        offered_unit_price_ex_vat=Decimal("1100"),
        tender_max_unit_price_ex_vat=Decimal("1000"),
    )

    assert ECONOMIC_UNIT_LIMIT_EXCEEDED in codes(await economic(db, workspace))


async def test_superar_el_pressupost_del_lot_bloqueja(db, tenant_a, user):
    _user, company = tenant_a
    workspace = await _workspace(db, company, await _tender(db, budget=Decimal("500")))
    await _offer_with_line(db, workspace, user)

    assert ECONOMIC_LOT_BUDGET_EXCEEDED in codes(await economic(db, workspace))


async def test_un_maxim_unitari_desconegut_no_bloqueja(db, workspace, user):
    await _offer_with_line(db, workspace, user)

    findings = await economic(db, workspace)

    unknown = [f for f in findings if f.code == ECONOMIC_UNIT_LIMIT_UNKNOWN]
    assert unknown and unknown[0].severity == INFO
    assert not [f for f in findings if f.severity == BLOCKER]


async def test_un_pressupost_de_lot_desconegut_no_bloqueja(db, workspace, user):
    """Desconegut no vol dir "dins del limit"."""
    await _offer_with_line(db, workspace, user)

    findings = await economic(db, workspace)

    unknown = [f for f in findings if f.code == ECONOMIC_LOT_BUDGET_UNKNOWN]
    assert unknown and unknown[0].severity == INFO


async def test_l_iva_que_falta_avisa(db, workspace, user):
    await _offer_with_line(db, workspace, user, vat_rate=None)

    findings = await economic(db, workspace)

    avisos = [f for f in findings if f.code == ECONOMIC_VAT_MISSING]
    assert avisos and avisos[0].severity == WARNING


async def test_el_cost_intern_no_arriba_mai_a_la_porta_final(db, workspace, user):
    """Ni com a blocador ni com a avis: no es una condicio per presentar, i si hi
    fos, la readiness s'hauria de redactar per rol."""
    await _offer_with_line(db, workspace, user, unit_cost_ex_vat=None)

    findings = await economic(db, workspace)

    for finding in findings:
        assert "cost" not in finding.code
        assert "margin" not in finding.code
        assert "markup" not in finding.code


async def test_un_marge_negatiu_no_impedeix_presentar(db, workspace, user):
    """Presentar a perdua pot ser una decisio comercial legitima."""
    await _offer_with_line(
        db,
        workspace,
        user,
        unit_cost_ex_vat=Decimal("2000"),
        offered_unit_price_ex_vat=Decimal("1000"),
    )

    findings = await economic(db, workspace)

    assert not [f for f in findings if f.severity == BLOCKER]


# --------------------------------------------------------------------------- #
# 38-41. El dossier s'agrega, no es recalcula                                   #
# --------------------------------------------------------------------------- #


def test_els_blocadors_del_dossier_hi_entren_tal_com_venen():
    dossier = ReadinessResult(
        WorkspaceReadiness.INCOMPLETE, ("mandatory_evidence_missing",)
    )

    final = evaluate_final(dossier=dossier)

    assert [f.code for f in final.findings] == ["mandatory_evidence_missing"]
    assert final.findings[0].domain == "dossier"


@pytest.mark.parametrize("code", ["decision_not_go", "analysis_outdated"])
def test_els_blocadors_de_candidatura_no_es_dupliquen(code):
    """Els calcula el dossier pero parlen del workspace: es reclassifiquen, no
    es tornen a avaluar."""
    final = evaluate_final(
        dossier=ReadinessResult(WorkspaceReadiness.INCOMPLETE, (code,))
    )

    assert [f.code for f in final.findings] == [code]
    assert final.findings[0].domain == "workspace"


def test_una_revisio_demanada_segueix_impedint_tancar():
    """`evidence_needs_review` no es feina pendent, pero tampoc es "a punt"."""
    final = evaluate_final(
        dossier=ReadinessResult(WorkspaceReadiness.NEEDS_REVIEW, ("evidence_needs_review",))
    )

    assert final.ready is False
    assert final.as_readiness_result().state == WorkspaceReadiness.NEEDS_REVIEW


# --------------------------------------------------------------------------- #
# 42-47. El veredicte                                                           #
# --------------------------------------------------------------------------- #


def test_sense_cap_blocador_la_candidatura_esta_a_punt():
    final = evaluate_final(dossier=ReadinessResult(WorkspaceReadiness.READY_FOR_FINAL_REVIEW, ()))

    assert final.ready is True


def test_un_sol_blocador_ja_ho_impedeix():
    final = evaluate_final(
        dossier=ReadinessResult(WorkspaceReadiness.READY_FOR_FINAL_REVIEW, ()),
        technical=[
            Finding(code=TECHNICAL_SCOPE_UNREVIEWED, severity=BLOCKER, domain=DOMAIN_TECHNICAL)
        ],
    )

    assert final.ready is False


def test_un_dossier_impecable_no_basta_per_si_sol():
    """La regla que evita dues portes: el dossier no decideix sol."""
    final = evaluate_final(
        dossier=ReadinessResult(WorkspaceReadiness.READY_FOR_FINAL_REVIEW, ()),
        economic=[
            Finding(code=ECONOMIC_OFFER_NOT_STARTED, severity=BLOCKER, domain=DOMAIN_ECONOMIC)
        ],
    )

    assert final.ready is False
    assert final.as_readiness_result().state == WorkspaceReadiness.INCOMPLETE


@pytest.mark.parametrize("severity", [WARNING, INFO])
def test_els_avisos_i_la_informacio_no_tanquen_cap_porta(severity):
    final = evaluate_final(
        dossier=ReadinessResult(WorkspaceReadiness.READY_FOR_FINAL_REVIEW, ()),
        economic=[
            Finding(code=ECONOMIC_VAT_MISSING, severity=severity, domain=DOMAIN_ECONOMIC)
        ],
    )

    assert final.ready is True


async def test_una_candidatura_existent_sense_revisar_no_queda_a_punt(db, workspace, user):
    """Cap grandfathering: `technical_scope_fingerprint` a NULL vol dir NO revisat."""
    assert workspace.technical_scope_fingerprint is None

    final = await service.final_readiness(db, workspace)

    assert final.ready is False
    assert TECHNICAL_SCOPE_UNREVIEWED in final.blockers


async def test_un_blocador_tecnic_impedeix_passar_a_revisio_final(
    db, client_a, workspace, user
):
    await _offer_with_line(db, workspace, user)

    response = await client_a.post(url(workspace, "readiness", "advance"))

    assert response.status_code == 409
    assert TECHNICAL_SCOPE_UNREVIEWED in response.json()["error"]["details"]["blockers"]


# --------------------------------------------------------------------------- #
# 48-51. Permisos i multitenant                                                 #
# --------------------------------------------------------------------------- #


async def test_qualsevol_membre_pot_llegir_la_porta_final(db, client_a, tenant_a, workspace):
    await set_role(db, tenant_a, MembershipRole.VIEWER)

    response = await client_a.get(url(workspace, "final-readiness"))

    assert response.status_code == 200
    assert response.json()["ready_for_final_review"] is False


async def test_un_viewer_no_pot_confirmar_l_abast(db, client_a, tenant_a, workspace):
    await set_role(db, tenant_a, MembershipRole.VIEWER)

    response = await client_a.post(url(workspace, "technical-scope", "review"))

    assert response.status_code == 403


async def test_un_perfil_tecnic_si_que_pot_confirmar_l_abast(
    db, client_a, tenant_a, workspace
):
    """Revisar l'abast es part de preparar la solucio tecnica, no una decisio
    economica: `technical` hi te potestat."""
    await set_role(db, tenant_a, MembershipRole.TECHNICAL)

    response = await client_a.post(url(workspace, "technical-scope", "review"))

    assert response.status_code == 200, response.text
    assert response.json()["technical"]["scope_reviewed"] is True


async def test_una_candidatura_d_una_altra_empresa_no_es_llegeix(client_b, workspace):
    lectura = await client_b.get(url(workspace, "final-readiness"))
    confirmacio = await client_b.post(url(workspace, "technical-scope", "review"))

    assert lectura.status_code == confirmacio.status_code == 404


async def test_confirmar_l_abast_queda_auditat_un_sol_cop(db, client_a, workspace):
    await client_a.post(url(workspace, "technical-scope", "review"))
    await client_a.post(url(workspace, "technical-scope", "review"))

    files = (
        await db.scalars(
            sa.select(AuditLog).where(AuditLog.entity_type == "workspace_technical_scope")
        )
    ).all()

    assert len(list(files)) == 1


# --------------------------------------------------------------------------- #
# Hardening: la lectura no escriu, i no filtra res de dins                      #
# --------------------------------------------------------------------------- #


async def _snapshot(db, workspace) -> dict:
    """Tot el que S6 consulta, tal com esta ara."""
    await db.refresh(workspace)
    offer = await db.scalar(
        sa.select(EconomicOffer).where(EconomicOffer.workspace_id == workspace.id)
    )
    if offer is not None:
        await db.refresh(offer)
    solucions = (
        await db.execute(
            sa.select(
                WorkspaceTechnicalSolution.id,
                WorkspaceTechnicalSolution.status,
                WorkspaceTechnicalSolution.updated_at,
            ).where(WorkspaceTechnicalSolution.workspace_id == workspace.id)
        )
    ).all()
    return {
        "workspace_status": workspace.status,
        "scope_reviewed_at": workspace.technical_scope_reviewed_at,
        "scope_reviewed_by": workspace.technical_scope_reviewed_by,
        "scope_fingerprint": workspace.technical_scope_fingerprint,
        "workspace_updated_at": workspace.updated_at,
        "offer_revision": offer.revision if offer else None,
        "offer_updated_at": offer.updated_at if offer else None,
        "solutions": sorted((str(s.id), str(s.status), s.updated_at) for s in solucions),
        "audit": (await db.execute(sa.select(sa.func.count(AuditLog.id)))).scalar_one(),
        "packages": (
            await db.execute(sa.select(sa.func.count(SubmissionPackage.id)))
        ).scalar_one(),
    }


async def test_llegir_la_porta_final_no_escriu_absolutament_res(
    db, client_a, workspace, user
):
    """Una lectura que escriu va degradar un workspace real mentre nomes es
    comprovava que les dades es llegien be.

    `GET` avalua i planifica; no reconcilia i no persisteix. Aquest test hi es
    perque aquella regressio no pugui tornar per la porta nova.
    """
    await _offer_with_line(db, workspace, user)
    requirement = await _requirement(db, await db.get(Tender, workspace.tender_id))
    await _solution(db, workspace, requirement=requirement)
    await scope_service.confirm(db, workspace, user=user)
    abans = await _snapshot(db, workspace)

    resposta = await client_a.get(url(workspace, "final-readiness"))

    assert resposta.status_code == 200
    db.expire_all()
    assert await _snapshot(db, workspace) == abans


async def test_llegir_la_porta_final_no_filtra_cap_xifra_interna(
    db, client_a, workspace, user
):
    """Cost, marge i markup no son condicions per presentar.

    Si hi fossin, aquest contracte s'hauria de redactar per rol i tornariem a
    obrir el forat que l'oferta economica ja va tancar. Per aixo no hi son per a
    NINGU: ni tan sols per a qui els podria veure a l'oferta.
    """
    await _offer_with_line(
        db,
        workspace,
        user,
        unit_cost_ex_vat=Decimal("666.66"),
        offered_unit_price_ex_vat=Decimal("999.99"),
    )

    cru = (await client_a.get(url(workspace, "final-readiness"))).text

    for filtrat in (
        "unit_cost_ex_vat",
        "total_cost",
        "margin_amount",
        "margin_on_sales_pct",
        "markup_on_cost_pct",
        "monthly_equivalent_cost",
        "incomplete_cost_lines",
        "666.66",
        "666",
    ):
        assert filtrat not in cru, filtrat


async def test_un_maxim_unitari_desconegut_no_afirma_res_ni_ensenya_res(
    db, workspace, user
):
    """`info` vol dir "no ho sabem", i no ha de semblar mai "dins del limit"."""
    await _offer_with_line(db, workspace, user)
    await scope_service.confirm(db, workspace, user=user)

    final = await service.final_readiness(db, workspace)

    desconegut = [f for f in final.findings if f.code == ECONOMIC_UNIT_LIMIT_UNKNOWN]
    assert len(desconegut) == 1, "agregat per lot, no una vegada per linia"
    assert desconegut[0].severity == INFO
    assert desconegut[0].code not in final.blockers
    assert set(desconegut[0].context) == {"lines"}
    assert desconegut[0].context["lines"] == 1
