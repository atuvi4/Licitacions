"""On es treballa cada requisit: una regla, al backend, i cap requisit amagat.

Motiu (Palamos 2024-3137): Solucio tecnica demanava "Defineix una solucio" per
al DEUC, la capacitat d'obrar o el correu per a notificacions. El model els
havia classificat be (`administrative_documentation`); l'abast tecnic
simplement agafava tots els requisits del lot.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.models.enums import (
    RequirementCategory,
    RequirementObligation,
    TechnicalSolutionSource,
    TechnicalSolutionStatus,
)
from app.models.tenders import Tender
from app.models.workspace import WorkspaceTechnicalSolution
from app.services import technical_scope
from app.services.workspace_readiness import technical_findings
from app.workspace.readiness import TECHNICAL_SOLUTION_UNVALIDATED
from app.workspace.requirement_routing import (
    COMPLIANCE_DOSSIER,
    REVIEW_ROUTING,
    TECHNICAL_SOLUTION,
    WORK_PLAN,
    route_requirement,
)
from tests.conftest import grant_go
from tests.test_technical_solutions_api import (
    _requirement,
    _tender,
    create_from_requirement,
    url,
)

C = RequirementCategory
O = RequirementObligation  # noqa: E741

ANALYSIS = "/api/v1/tenders/{tender}/analysis/requirements"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"


@pytest.fixture
async def workspace(db, tenant_a):
    from app.services import bid_workspace as workspace_service

    user, company = tenant_a
    tender = await _tender(db)
    outcome = await workspace_service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=user.id
    )
    await grant_go(db, outcome.workspace, user)
    return outcome.workspace


def _route(category, obligation=O.MANDATORY) -> str:
    return route_requirement(SimpleNamespace(category=category, obligation=obligation))


# --------------------------------------------------------------------------- #
# La regla                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "category",
    [
        C.ADMINISTRATIVE_DOCUMENTATION,
        C.ECONOMIC_SOLVENCY,
        C.TECHNICAL_SOLVENCY,
        C.EXPERIENCE,
        C.CERTIFICATION,
        C.REGISTRATION,
        C.BUSINESS_CLASSIFICATION,
        C.INSURANCE,
        C.PROFESSIONAL_QUALIFICATION,
    ],
)
@pytest.mark.parametrize("obligation", list(O))
def test_els_requisits_d_acreditacio_van_a_compliment_i_dossier(category, obligation):
    assert _route(category, obligation) == COMPLIANCE_DOSSIER


@pytest.mark.parametrize(
    "category", [C.TECHNICAL_REQUIREMENT, C.EQUIPMENT_OR_RESOURCES, C.PERSONNEL]
)
@pytest.mark.parametrize("obligation", [O.MANDATORY, O.SCORED])
def test_els_tecnics_que_obliguen_o_puntuen_demanen_solucio(category, obligation):
    assert _route(category, obligation) == TECHNICAL_SOLUTION


@pytest.mark.parametrize(
    "category", [C.TECHNICAL_REQUIREMENT, C.EQUIPMENT_OR_RESOURCES, C.PERSONNEL]
)
@pytest.mark.parametrize("obligation", [O.INFORMATIVE, O.UNKNOWN])
def test_un_tecnic_informatiu_o_ambigu_queda_per_revisar(category, obligation):
    assert _route(category, obligation) == REVIEW_ROUTING


@pytest.mark.parametrize("obligation", list(O))
def test_els_terminis_van_al_pla_de_treball(obligation):
    assert _route(C.DEADLINE, obligation) == WORK_PLAN


@pytest.mark.parametrize("category", [C.CONTRACT_CONDITION, C.OTHER, "inventada"])
def test_condicions_other_i_valors_desconeguts_queden_per_revisar(category):
    assert _route(category) == REVIEW_ROUTING


def test_totes_les_categories_tenen_destinacio():
    for category in C:
        for obligation in O:
            assert _route(category, obligation) in {
                COMPLIANCE_DOSSIER,
                TECHNICAL_SOLUTION,
                WORK_PLAN,
                REVIEW_ROUTING,
            }


# --------------------------------------------------------------------------- #
# L'abast tecnic                                                                #
# --------------------------------------------------------------------------- #


async def _set(db, workspace):
    tender = await db.get(Tender, workspace.tender_id)
    made = {
        "deuc": await _requirement(
            db, tender, title="Declaracio responsable DEUC",
            category=C.ADMINISTRATIVE_DOCUMENTATION,
        ),
        "solvencia": await _requirement(
            db, tender, title="Volum anual de negoci", category=C.ECONOMIC_SOLVENCY
        ),
        "sla": await _requirement(db, tender, title="SLA de l'Annex V"),
        "equips": await _requirement(
            db, tender, title="Terminals IP", category=C.EQUIPMENT_OR_RESOURCES
        ),
        "termini": await _requirement(
            db, tender, title="Pla d'implantacio en 15 dies", category=C.DEADLINE
        ),
        "condicio": await _requirement(
            db, tender, title="Facturacio mensual", category=C.CONTRACT_CONDITION
        ),
        "altre": await _requirement(db, tender, title="No divisio en lots", category=C.OTHER),
    }
    return made


async def test_l_abast_tecnic_nomes_porta_els_que_demanen_solucio(db, workspace):
    made = await _set(db, workspace)

    scope = await technical_scope.resolve(db, workspace)

    assert {r.id for r in scope.requirements} == {made["sla"].id, made["equips"].id}


@pytest.mark.parametrize("key", ["deuc", "solvencia", "termini", "condicio", "altre"])
async def test_no_es_pot_demanar_solucio_d_un_requisit_que_no_en_demana(
    client_a, db, workspace, key
):
    made = await _set(db, workspace)

    response = await create_from_requirement(client_a, workspace, made[key].id)

    assert response.status_code == 404


async def test_un_tecnic_si_que_la_pot_demanar(client_a, db, workspace):
    made = await _set(db, workspace)

    response = await create_from_requirement(client_a, workspace, made["sla"].id)

    assert response.status_code in (200, 201), response.text
    assert response.json()["created"] is True


async def test_l_api_dona_la_destinacio_i_no_n_amaga_cap(client_a, db, workspace):
    """Requisits els ensenya tots; cadascun amb la destinacio calculada al backend."""
    made = await _set(db, workspace)

    response = await client_a.get(ANALYSIS.format(tender=workspace.tender_id))

    assert response.status_code == 200, response.text
    routing = {row["title"]: row["routing"] for row in response.json()["requirements"]}
    assert routing == {
        "Declaracio responsable DEUC": COMPLIANCE_DOSSIER,
        "Volum anual de negoci": COMPLIANCE_DOSSIER,
        "SLA de l'Annex V": TECHNICAL_SOLUTION,
        "Terminals IP": TECHNICAL_SOLUTION,
        "Pla d'implantacio en 15 dies": WORK_PLAN,
        "Facturacio mensual": REVIEW_ROUTING,
        "No divisio en lots": REVIEW_ROUTING,
    }
    assert len(routing) == len(made)


async def test_el_recompte_de_l_api_i_el_de_la_preparacio_coincideixen(
    client_a, db, workspace
):
    """La pantalla filtra per `routing` i la preparacio compta l'abast: el mateix."""
    await _set(db, workspace)

    analysis = (await client_a.get(ANALYSIS.format(tender=workspace.tender_id))).json()
    readiness = await client_a.get(
        READINESS.format(company=workspace.company_id, workspace=workspace.id)
    )

    assert readiness.status_code == 200, readiness.text
    technical_rows = [
        row
        for row in analysis["requirements"]
        if row["routing"] == TECHNICAL_SOLUTION and row["tender_lot_id"] is None
    ]
    assert readiness.json()["technical"]["requirements_in_scope"] == len(technical_rows) == 2


async def test_una_solucio_antiga_d_un_requisit_administratiu_es_conserva(
    client_a, db, tenant_a, workspace
):
    """Creada abans de la regla: no s'esborra, surt al llistat i no bloqueja."""
    user, _ = tenant_a
    made = await _set(db, workspace)
    legacy = WorkspaceTechnicalSolution(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        requirement_id=made["deuc"].id,
        source=TechnicalSolutionSource.REQUIREMENT,
        title="Preparar DEUC",
        status=TechnicalSolutionStatus.NOT_STARTED,
        created_by=user.id,
        updated_by=user.id,
    )
    db.add(legacy)
    await db.commit()

    listed = await client_a.get(url(workspace))
    findings = await technical_findings(db, workspace)

    assert str(legacy.id) in {row["id"] for row in listed.json()}
    assert await db.get(WorkspaceTechnicalSolution, legacy.id) is not None
    assert [
        f for f in findings if f.code == TECHNICAL_SOLUTION_UNVALIDATED and f.entity_id == legacy.id
    ] == []


async def test_un_requisit_administratiu_nou_tambe_caduca_la_revisio_de_l_abast(
    db, tenant_a, workspace
):
    """L'empremta es fa de TOTS els requisits del lot, no nomes dels tecnics.

    Es la mateixa empremta que entra al manifest del paquet: si un canvi al plec
    administratiu no la mogues, un paquet aprovat no se n'assabentaria.
    """
    user, _ = tenant_a
    await _set(db, workspace)
    await technical_scope.confirm(db, workspace, user=user)
    await db.commit()

    tender = await db.get(Tender, workspace.tender_id)
    await _requirement(
        db, tender, title="Declaracio de grup empresarial", category=C.ADMINISTRATIVE_DOCUMENTATION
    )

    assert (await technical_scope.review_state(db, workspace)).stale is True


async def test_l_empremta_no_depen_del_routing(db, workspace):
    """Afegir la regla no pot fer caducar res: l'empremta es la de sempre."""
    await _set(db, workspace)
    scope = await technical_scope.resolve(db, workspace)

    assert len(scope.requirements) == 2
    assert len(scope.lot_requirements) == 7


async def test_un_requisit_tecnic_nou_si_que_la_caduca(db, tenant_a, workspace):
    user, _ = tenant_a
    await _set(db, workspace)
    await technical_scope.confirm(db, workspace, user=user)
    await db.commit()

    tender = await db.get(Tender, workspace.tender_id)
    await _requirement(db, tender, title="Centraleta al nuvol")

    assert (await technical_scope.review_state(db, workspace)).stale is True


async def test_cap_requisit_no_desapareix_del_conjunt_efectiu(db, workspace):
    from app.services import manual_requirement

    made = await _set(db, workspace)

    effective = await manual_requirement.effective_requirements(
        db, workspace.tender_id, workspace.company_id
    )

    assert {r.id for r in made.values()} <= {r.id for r in effective.all}
    assert (
        await db.scalar(sa.select(sa.func.count()).select_from(WorkspaceTechnicalSolution))
    ) == 0
    assert uuid.UUID(str(workspace.id))
