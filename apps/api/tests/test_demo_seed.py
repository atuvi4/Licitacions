"""El seed de la demo ha de produir una candidatura que s'aguanti sola.

No comprova que hi hagi files: comprova que la historia es pot explicar. Hem
analitzat el plec, hem decidit quins requisits necessiten resposta tecnica, algu
les ha validades, hem preparat una oferta, hem revisat l'abast i la candidatura
esta a punt per a la revisio final.

El dossier hi es en la seva forma minima a proposit: aqui es prova el que S8
afegeix —solucio tecnica, oferta i revisio d'abast—, no es torna a provar el
dossier, que ja te els seus tests.
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models.credentials import CompanyCertification
from app.models.documents import Document, DocumentLink
from app.models.enums import (
    AnalysisRunStatus,
    DocumentCategory,
    EvidenceItemStatus,
    ExtractionConfidence,
    LinkedEntityType,
    MembershipRole,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    ReviewStatus,
    SourceType,
    WorkspaceDecisionType,
)
from app.models.identity import CompanyMembership
from app.models.offer import EconomicOffer
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.submission import SubmissionPackage
from app.models.tenders import Tender
from app.models.workspace import WorkspaceEvidenceItem, WorkspaceTechnicalSolution
from app.services import bid_workspace as workspace_service
from app.services import workspace_dossier as dossier_service
from app.services.compliance_analysis import analyze_company_compliance
from app.services.storage import get_storage

API_ROOT = Path(__file__).resolve().parents[1]


def _load_demo():
    """El seed viu a `scripts/`, fora del paquet: es carrega com el seu test."""
    spec = importlib.util.spec_from_file_location(
        "demo_scenario_seed", API_ROOT / "scripts" / "demo_scenario.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_demo()

TODAY = date.today()
DEADLINE = datetime.now(UTC) + timedelta(days=40)


# --------------------------------------------------------------------------- #
# Una candidatura amb el dossier ja resolt                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def preparada(db, tenant_a, storage, client_a):
    """El punt on arriba la demo just abans del que afegeix S8."""
    user, company = tenant_a

    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="DEMO-TEST-001",
        title="Desplegament de xarxa de fibra optica",
        deadline_at=DEADLINE,
        base_budget_without_tax=Decimal("240000"),
    )
    db.add(tender)
    await db.flush()

    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        # Proveidor REAL a proposit, com a la resta de tests que aproven
        # paquets: la demo de veritat corre sobre fixtures i per aixo s'atura a
        # `simulation_not_approvable`, que es justament el que vol ensenyar.
        provider="anthropic",
        model="claude-sonnet-5",
        is_current=True,
    )
    db.add(run)
    await db.flush()

    def _requirement(**kwargs):
        base = {
            "run_id": run.id,
            "tender_id": tender.id,
            "scope": RequirementScope.TENDER,
            "confidence": ExtractionConfidence.HIGH,
            "review_status": ReviewStatus.UNREVIEWED,
            "dedup_key": uuid.uuid4().hex,
        }
        return Requirement(**{**base, **kwargs})

    certificat = _requirement(
        category=RequirementCategory.CERTIFICATION,
        obligation=RequirementObligation.MANDATORY,
        operator=RequirementOperator.EXISTS,
        title="Certificat ISO 14001 en vigor",
        value_json={"name": "ISO 14001"},
    )
    # Informatiu a proposit: aqui interessa que existeixi i que entri a l'abast
    # tecnic, no tornar a provar els forats del dossier.
    memoria = _requirement(
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.INFORMATIVE,
        operator=RequirementOperator.EXISTS,
        title="Memoria tecnica signada per tecnic competent",
    )
    db.add_all([certificat, memoria])

    certificacio = CompanyCertification(
        company_id=company.id, name="ISO 14001", expires_at=TODAY + timedelta(days=400)
    )
    db.add(certificacio)
    await db.flush()
    # Els BYTES han de ser-hi de debo: congelar comprova que el fitxer existeix
    # i que el seu SHA-256 es el que diu la fila.
    contingut = f"[DEMO] certificat {uuid.uuid4().hex}".encode()
    clau = f"docs/{uuid.uuid4().hex}.pdf"
    await get_storage().upload(clau, contingut, "application/pdf")
    document = Document(
        company_id=company.id,
        name="ISO 14001.pdf",
        original_filename="ISO 14001.pdf",
        storage_key=clau,
        mime_type="application/pdf",
        file_size=len(contingut),
        sha256=hashlib.sha256(contingut).hexdigest(),
        category=DocumentCategory.CERTIFICATION,
        expires_at=TODAY + timedelta(days=400),
    )
    db.add(document)
    await db.flush()
    db.add(
        DocumentLink(
            company_id=company.id,
            document_id=document.id,
            entity_type=LinkedEntityType.COMPANY_CERTIFICATION,
            entity_id=certificacio.id,
        )
    )
    await db.commit()

    await analyze_company_compliance(db, company, tender, force=True)
    workspace = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=tender, user_id=user.id
        )
    ).workspace
    await workspace_service.record_decision(
        db,
        workspace,
        decision=WorkspaceDecisionType.GO,
        user=user,
        role=MembershipRole.OWNER,
        reason="[DEMO] Ens hi presentem.",
    )
    await dossier_service.sync_dossier(db, workspace, user_id=user.id)
    await db.commit()
    item = await db.scalar(
        sa.select(WorkspaceEvidenceItem).where(
            WorkspaceEvidenceItem.workspace_id == workspace.id
        )
    )
    await dossier_service.update_item(
        db, workspace, item, status=EvidenceItemStatus.SELECTED, user=user
    )
    assert (await dossier_service.evaluate_workspace_readiness(db, workspace)).is_ready

    return {
        "user": user,
        "company": company,
        "tender": tender,
        "workspace": workspace,
        "requirement": memoria,
        "base": f"/api/v1/companies/{company.id}",
    }


async def _seed(client, preparada) -> dict:
    """Els tres passos que afegeix S8, en el seu ordre."""
    base, workspace_id = preparada["base"], str(preparada["workspace"].id)
    solutions = await demo.seed_demo_technical(
        client, base, workspace_id, str(preparada["tender"].id)
    )
    await demo.seed_demo_economic(client, base, workspace_id, solutions)
    return await demo.confirm_demo_scope(client, base, workspace_id)


async def _final(client, preparada) -> dict:
    response = await client.get(
        f"{preparada['base']}/workspaces/{preparada['workspace'].id}/final-readiness"
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _count(db, model, **where) -> int:
    stmt = sa.select(sa.func.count()).select_from(model)
    for camp, valor in where.items():
        stmt = stmt.where(getattr(model, camp) == valor)
    return (await db.execute(stmt)).scalar_one()


# --------------------------------------------------------------------------- #
# La historia es pot explicar                                                   #
# --------------------------------------------------------------------------- #


async def test_el_seed_deixa_la_candidatura_a_punt(db, client_a, preparada):
    """La comprovacio que resumeix totes les altres."""
    await _seed(client_a, preparada)

    final = await _final(client_a, preparada)

    assert final["ready_for_final_review"] is True
    assert [f for f in final["findings"] if f["severity"] == "blocker"] == []


async def test_el_seed_valida_les_solucions_pel_flux_real(db, client_a, preparada):
    await _seed(client_a, preparada)

    solutions = (
        await db.scalars(
            sa.select(WorkspaceTechnicalSolution).where(
                WorkspaceTechnicalSolution.workspace_id == preparada["workspace"].id
            )
        )
    ).all()

    assert len(list(solutions)) >= 1
    for solution in solutions:
        assert solution.status.value == "validated"
        # Les empremtes les calcula el servei: si algu les escrivia a ma, la
        # validacio semblaria vigent sense haver passat per cap comprovacio.
        assert solution.validated_content_fingerprint
        assert solution.validated_by == preparada["user"].id
        assert len(solution.proposed_solution or "") > 60


async def test_el_seed_lliga_una_solucio_a_un_requisit_del_plec(db, client_a, preparada):
    """C-11: la tria es humana, i al seed la persona es el propi seed."""
    await _seed(client_a, preparada)

    lligades = (
        await db.scalars(
            sa.select(WorkspaceTechnicalSolution).where(
                WorkspaceTechnicalSolution.workspace_id == preparada["workspace"].id,
                WorkspaceTechnicalSolution.requirement_id.is_not(None),
            )
        )
    ).all()

    assert [s.requirement_id for s in lligades] == [preparada["requirement"].id]


async def test_el_seed_deixa_l_abast_revisat_i_vigent(db, client_a, preparada):
    """Confirmar abans de crear res deixaria la revisio caducada tot seguit."""
    await _seed(client_a, preparada)

    final = await _final(client_a, preparada)

    assert final["technical"]["scope_reviewed"] is True
    assert final["technical"]["scope_stale"] is False
    assert final["technical"]["requirements_in_scope"] >= 1


async def test_el_seed_construeix_una_oferta_llegible(db, client_a, preparada):
    await _seed(client_a, preparada)

    offer = (
        await client_a.get(
            f"{preparada['base']}/workspaces/{preparada['workspace'].id}/economic-offer"
        )
    ).json()

    lot = offer["lots"][0]
    linies = [line for section in lot["sections"] for line in section["lines"]]
    assert len(lot["sections"]) == 3
    assert 4 <= len(linies) <= 7
    assert lot["duration_months"] == demo.DEMO_DURATION_MONTHS
    assert {line["pricing_basis"] for line in linies} >= {"one_off", "monthly", "per_unit"}
    for line in linies:
        assert line["offered_unit_price_ex_vat"] is not None
        assert line["vat_rate"] is not None
        if line["pricing_basis"] in ("monthly", "annual"):
            assert line["effective_duration_months"] == demo.DEMO_DURATION_MONTHS


async def test_l_oferta_del_seed_cap_dins_del_pressupost(db, client_a, preparada):
    await _seed(client_a, preparada)

    offer = (
        await client_a.get(
            f"{preparada['base']}/workspaces/{preparada['workspace'].id}/economic-offer"
        )
    ).json()

    limit = offer["lots"][0]["limit"]
    assert limit["exceeds_lot_max"] is False
    assert Decimal(limit["difference_to_maximum"]) > 0


async def test_una_linia_economica_respon_a_una_solucio_tecnica(db, client_a, preparada):
    """El que ha de poder ensenyar la pantalla: aquest import respon a aixo."""
    await _seed(client_a, preparada)

    offer = (
        await client_a.get(
            f"{preparada['base']}/workspaces/{preparada['workspace'].id}/economic-offer"
        )
    ).json()

    linies = [
        line
        for section in offer["lots"][0]["sections"]
        for line in section["lines"]
        if line["technical_solution_id"]
    ]
    assert len(linies) == 1
    assert linies[0]["requirement_id"] == str(preparada["requirement"].id)


async def test_el_seed_no_congela_cap_paquet(db, client_a, preparada):
    """La demo ha de poder ensenyar en directe congelar i aprovar."""
    await _seed(client_a, preparada)

    assert await _count(db, SubmissionPackage) == 0


def test_per_defecte_el_seed_no_congela_res():
    """El default de la funcio, no nomes el que faci qui la crida.

    Si algun dia algu afegeix una licitacio al guio i s'oblida del parametre, la
    demo ha de quedar a punt i prou. Congelar es el pas que volem ensenyar en
    directe, i un default que el fes sol el tornaria a amagar.
    """
    parametre = inspect.signature(demo.run_tender_flow).parameters["freeze_package_at_end"]

    assert parametre.default is False


# --------------------------------------------------------------------------- #
# Idempotencia                                                                  #
# --------------------------------------------------------------------------- #


async def test_passar_el_seed_dues_vegades_no_duplica_res(db, client_a, preparada):
    await _seed(client_a, preparada)
    workspace_id = preparada["workspace"].id
    abans = {
        "solucions": await _count(db, WorkspaceTechnicalSolution, workspace_id=workspace_id),
        "ofertes": await _count(db, EconomicOffer, workspace_id=workspace_id),
        "evidencies": await _count(db, WorkspaceEvidenceItem, workspace_id=workspace_id),
    }
    offer_abans = (
        await client_a.get(f"{preparada['base']}/workspaces/{workspace_id}/economic-offer")
    ).json()

    await _seed(client_a, preparada)

    offer = (
        await client_a.get(f"{preparada['base']}/workspaces/{workspace_id}/economic-offer")
    ).json()
    assert {
        "solucions": await _count(db, WorkspaceTechnicalSolution, workspace_id=workspace_id),
        "ofertes": await _count(db, EconomicOffer, workspace_id=workspace_id),
        "evidencies": await _count(db, WorkspaceEvidenceItem, workspace_id=workspace_id),
    } == abans
    assert offer["revision"] == offer_abans["revision"]
    assert len(offer["lots"][0]["sections"]) == len(offer_abans["lots"][0]["sections"])


async def test_tornar_a_passar_el_seed_no_caduca_la_revisio_de_l_abast(
    db, client_a, preparada
):
    """Si la segona passada mogues l'empremta, la demo sortiria amb l'abast
    caducat sense que ningu hagues canviat res."""
    await _seed(client_a, preparada)
    await db.refresh(preparada["workspace"])
    empremta = preparada["workspace"].technical_scope_fingerprint

    await _seed(client_a, preparada)

    await db.refresh(preparada["workspace"])
    assert preparada["workspace"].technical_scope_fingerprint == empremta
    final = await _final(client_a, preparada)
    assert final["technical"]["scope_stale"] is False


# --------------------------------------------------------------------------- #
# Rols: el mateix demo serveix per ensenyar la frontera                         #
# --------------------------------------------------------------------------- #


async def _set_role(db, preparada, role) -> None:
    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == preparada["user"].id,
            CompanyMembership.company_id == preparada["company"].id,
        )
        .values(role=role)
    )
    await db.commit()


async def test_qui_mana_sobre_els_preus_veu_el_marge_del_demo(db, client_a, preparada):
    await _seed(client_a, preparada)

    offer = (
        await client_a.get(
            f"{preparada['base']}/workspaces/{preparada['workspace'].id}/economic-offer"
        )
    ).json()

    assert offer["can_view_internal_economics"] is True
    assert offer["internal_economics"]["total_cost"] is not None
    assert offer["internal_economics"]["margin_on_sales_pct"] is not None


@pytest.mark.parametrize("role", [MembershipRole.TECHNICAL, MembershipRole.VIEWER])
async def test_qui_no_hi_mana_no_veu_el_cost_del_demo(db, client_a, preparada, role):
    await _seed(client_a, preparada)
    await _set_role(db, preparada, role)
    url = f"{preparada['base']}/workspaces/{preparada['workspace'].id}"

    offer = (await client_a.get(f"{url}/economic-offer")).json()
    final = (await client_a.get(f"{url}/final-readiness")).text

    assert offer["internal_economics"] is None
    assert "unit_cost_ex_vat" not in final
    assert "margin_amount" not in final
    # El cost d'una linia concreta del seed, per si algun dia s'escapa.
    assert "1420" not in final


# --------------------------------------------------------------------------- #
# El flux que la demo ha de poder ensenyar despres                              #
# --------------------------------------------------------------------------- #


async def test_despres_del_seed_es_pot_congelar_i_aprovar(db, client_a, preparada):
    """No es deixa aprovat al seed: es comprova que les dades ho suporten.

    A la demo real l'analisi es una simulacio i l'aprovacio queda barrada amb
    `simulation_not_approvable`. Aqui l'analisi es marca com a real per poder
    recorrer el pas final; el que es prova es que les dades del seed aguanten
    congelar, generar manifest v2 i aprovar.
    """
    await _seed(client_a, preparada)
    url = f"{preparada['base']}/workspaces/{preparada['workspace'].id}"

    assert (await _final(client_a, preparada))["ready_for_final_review"] is True
    avancat = await client_a.post(f"{url}/readiness/advance")
    assert avancat.status_code == 200, avancat.text

    congelat = await client_a.post(f"{url}/packages")
    assert congelat.status_code == 200, congelat.text
    package_id = congelat.json()["package"]["id"]

    manifest = (await client_a.get(f"{url}/packages/{package_id}/manifest")).json()
    assert manifest["manifest_version"] == "submission-manifest-v2"
    assert manifest["content"]["technical"], "la solucio tecnica ha d'estar congelada"
    assert manifest["content"]["economic"]["lots"], "l'oferta ha d'estar congelada"
    assert manifest["approval_basis"]["technical_scope_fingerprint"]

    aprovat = await client_a.post(
        f"{url}/packages/{package_id}/approvals",
        json={"decision": "approved_for_submission", "comment": "[DEMO] Revisat."},
    )

    assert aprovat.status_code in (200, 201), aprovat.text
    await db.refresh(preparada["workspace"])
    assert preparada["workspace"].status.value != "submitted"
