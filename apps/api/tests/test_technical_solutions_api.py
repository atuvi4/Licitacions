"""Solucio tecnica: servei i endpoints.

Dos camins de creacio i cap d'automatic (C-11). Ningu no infereix quins requisits
necessiten solucio: ho diu una persona, o amb un POST manual, o assenyalant un
requisit. Per aixo el client no envia mai `source`: el fixa el servidor, i si el
pogues triar, la distincio entre "algu ho ha decidit" i "ho ha deduit el sistema"
deixaria de ser comprovable.

La validacio es humana i es historia: editar una solucio validada no esborra qui
la va signar ni quan. El que es deriva en llegir es si aquella firma encara val.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import (
    AnalysisRunStatus,
    AuditAction,
    ExtractionConfidence,
    MembershipRole,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementScope,
    ReviewStatus,
    SourceType,
    TechnicalSolutionSource,
    TechnicalSolutionStatus,
)
from app.models.identity import CompanyMembership
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tenders import Tender, TenderLot
from app.models.workspace import WorkspaceTechnicalSolution
from app.services import bid_workspace as workspace_service
from app.workspace.checklist import checklist_title

# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


async def _tender(db, *, title="Servei de manteniment integral") -> Tender:
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="5B/2026",
        title=title,
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


async def _requirement(
    db,
    tender,
    *,
    title="Certificat ISO 9001",
    value=None,
    category=RequirementCategory.TECHNICAL_REQUIREMENT,
    obligation=RequirementObligation.MANDATORY,
) -> Requirement:
    run = await db.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == tender.id
        )
    )
    run = run or RequirementExtractionRun(
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
    await db.flush()
    # Tecnic i obligatori: es el tipus de requisit que demana solucio tecnica
    # (`app/workspace/requirement_routing.py`).
    requirement = Requirement(
        run_id=run.id,
        tender_id=tender.id,
        category=category,
        obligation=obligation,
        operator=RequirementOperator.EXISTS,
        scope=RequirementScope.TENDER,
        title=title,
        value_json=value,
        confidence=ExtractionConfidence.HIGH,
        review_status=ReviewStatus.UNREVIEWED,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


@pytest.fixture
async def workspace(db, tenant_a):
    user, company = tenant_a
    tender = await _tender(db)
    outcome = await workspace_service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=user.id
    )
    return outcome.workspace


@pytest.fixture
async def requirement(db, workspace):
    tender = await db.get(Tender, workspace.tender_id)
    return await _requirement(db, tender)


def url(workspace, *parts: str) -> str:
    base = (
        f"/api/v1/companies/{workspace.company_id}"
        f"/workspaces/{workspace.id}/technical-solutions"
    )
    return "/".join([base, *parts])


async def set_role(db, tenant, role: MembershipRole) -> None:
    user, company = tenant
    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == user.id,
            CompanyMembership.company_id == company.id,
        )
        .values(role=role)
    )
    await db.commit()


async def create_manual(client, workspace, **body):
    return await client.post(url(workspace), json={"title": "Solucio", **body})


async def create_from_requirement(client, workspace, requirement_id):
    return await client.post(
        url(workspace, "from-requirement"), json={"requirement_id": str(requirement_id)}
    )


async def a_solution(client, workspace, **body) -> dict:
    response = await create_manual(client, workspace, **body)
    assert response.status_code == 201, response.text
    return response.json()


async def a_validated_solution(client, workspace, **body) -> dict:
    solution = await a_solution(
        client, workspace, proposed_solution="Dos servidors redundants", **body
    )
    response = await client.post(
        url(workspace, solution["id"], "validation"), json={"note": "Ho hem revisat"}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def read_one(client, workspace, solution_id) -> dict:
    response = await client.get(url(workspace))
    assert response.status_code == 200, response.text
    rows = [row for row in response.json() if row["id"] == str(solution_id)]
    assert rows, "la solucio no surt al llistat"
    return rows[0]


# --------------------------------------------------------------------------- #
# 1-9. Permisos                                                                 #
# --------------------------------------------------------------------------- #


async def test_un_viewer_pot_llegir_les_solucions(client_a, db, tenant_a, workspace):
    await a_solution(client_a, workspace)
    await set_role(db, tenant_a, MembershipRole.VIEWER)

    response = await client_a.get(url(workspace))

    assert response.status_code == 200
    assert len(response.json()) == 1


async def test_un_viewer_no_pot_crear_una_solucio_manual(client_a, db, tenant_a, workspace):
    await set_role(db, tenant_a, MembershipRole.VIEWER)

    response = await create_manual(client_a, workspace)

    assert response.status_code == 403


async def test_un_viewer_no_pot_crear_una_solucio_des_d_un_requisit(
    client_a, db, tenant_a, workspace, requirement
):
    await set_role(db, tenant_a, MembershipRole.VIEWER)

    response = await create_from_requirement(client_a, workspace, requirement.id)

    assert response.status_code == 403


async def test_un_viewer_no_pot_editar_una_solucio(client_a, db, tenant_a, workspace):
    solution = await a_solution(client_a, workspace)
    await set_role(db, tenant_a, MembershipRole.VIEWER)

    response = await client_a.patch(
        url(workspace, solution["id"]), json={"title": "Un altre titol"}
    )

    assert response.status_code == 403


async def test_un_viewer_no_pot_validar_una_solucio(client_a, db, tenant_a, workspace):
    solution = await a_solution(client_a, workspace, proposed_solution="Proposta")
    await set_role(db, tenant_a, MembershipRole.VIEWER)

    response = await client_a.post(
        url(workspace, solution["id"], "validation"), json={"note": "Va be"}
    )

    assert response.status_code == 403


async def test_un_perfil_tecnic_pot_crear_i_editar(client_a, db, tenant_a, workspace):
    """`technical` prepara la candidatura: aquesta capa es exactament la seva feina."""
    await set_role(db, tenant_a, MembershipRole.TECHNICAL)

    created = await create_manual(client_a, workspace, title="Solucio tecnica")
    assert created.status_code == 201, created.text

    patched = await client_a.patch(
        url(workspace, created.json()["id"]), json={"supplier_name": "Proveidor SL"}
    )

    assert patched.status_code == 200
    assert patched.json()["supplier_name"] == "Proveidor SL"


async def test_un_perfil_tecnic_pot_validar(client_a, db, tenant_a, workspace):
    """A diferencia del GO/NO-GO: validar una solucio no compromet l'empresa."""
    solution = await a_solution(client_a, workspace, proposed_solution="Proposta")
    await set_role(db, tenant_a, MembershipRole.TECHNICAL)

    response = await client_a.post(
        url(workspace, solution["id"], "validation"), json={"note": "Revisat"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == TechnicalSolutionStatus.VALIDATED.value


async def test_un_bid_manager_pot_validar(client_a, db, tenant_a, workspace):
    solution = await a_solution(client_a, workspace, proposed_solution="Proposta")
    await set_role(db, tenant_a, MembershipRole.BID_MANAGER)

    response = await client_a.post(
        url(workspace, solution["id"], "validation"), json={"note": "Revisat"}
    )

    assert response.status_code == 200


async def test_una_altra_empresa_no_veu_les_solucions(client_b, workspace, client_a):
    """404 i no 403: per a qui no hi pertany, aquest workspace no existeix."""
    await a_solution(client_a, workspace)

    response = await client_b.get(url(workspace))

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# 10-14. Creacio manual                                                         #
# --------------------------------------------------------------------------- #


async def test_una_creacio_manual_queda_marcada_com_a_manual(client_a, workspace):
    solution = await a_solution(client_a, workspace)

    assert solution["source"] == TechnicalSolutionSource.MANUAL.value


async def test_una_creacio_manual_no_va_lligada_a_cap_requisit(client_a, workspace):
    solution = await a_solution(client_a, workspace)

    assert solution["requirement_id"] is None


async def test_el_client_no_pot_triar_ni_l_origen_ni_el_requisit(
    client_a, workspace, requirement
):
    """Enviar-ho no ha de servir de res: el servidor mana sobre la provenance."""
    solution = await a_solution(
        client_a, workspace, source="requirement", requirement_id=str(requirement.id)
    )

    assert solution["source"] == TechnicalSolutionSource.MANUAL.value
    assert solution["requirement_id"] is None


async def test_no_es_pot_neixer_validada(client_a, workspace):
    """`validated` nomes s'escriu signant, mai creant."""
    response = await create_manual(
        client_a, workspace, status=TechnicalSolutionStatus.VALIDATED.value
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    "status",
    [
        TechnicalSolutionStatus.NOT_STARTED.value,
        TechnicalSolutionStatus.DRAFTING.value,
        TechnicalSolutionStatus.NEEDS_VALIDATION.value,
    ],
)
async def test_es_pot_neixer_en_qualsevol_estat_no_validat(client_a, workspace, status):
    solution = await a_solution(client_a, workspace, status=status)

    assert solution["status"] == status


async def test_una_proposta_llarga_no_la_rebutja_cap_limit_inventat(client_a, workspace):
    """`proposed_solution` es el cos de la proposta tecnica, i es `Text` a la base.

    Cap decisio de producte en fixa la llargada. Mentre no n'hi hagi cap, l'API no
    pot rebutjar una proposta llarga per un numero que no ha decidit ningu.
    """
    llarga = "Arquitectura proposada. " * 4000  # ~96.000 caracters

    response = await create_manual(client_a, workspace, proposed_solution=llarga)

    assert response.status_code == 201, response.text
    assert response.json()["proposed_solution"] == llarga


async def test_una_proposta_llarga_tambe_es_pot_editar(client_a, workspace):
    llarga = "Revisio de l'arquitectura. " * 4000

    solution = await a_solution(client_a, workspace)
    response = await client_a.patch(
        url(workspace, solution["id"]), json={"proposed_solution": llarga}
    )

    assert response.status_code == 200, response.text
    assert response.json()["proposed_solution"] == llarga


async def test_el_titol_es_obligatori(client_a, workspace):
    response = await client_a.post(url(workspace), json={"proposed_solution": "Sense titol"})

    assert response.status_code == 422


async def test_un_responsable_d_una_altra_empresa_es_rebutjat(client_a, db, workspace, tenant_b):
    other_user, _ = tenant_b

    response = await create_manual(client_a, workspace, owner_id=str(other_user.id))

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "assignee_not_in_company"


# --------------------------------------------------------------------------- #
# 15-20. Creacio des d'un requisit                                              #
# --------------------------------------------------------------------------- #


async def test_una_solucio_des_d_un_requisit_ho_diu(client_a, workspace, requirement):
    response = await create_from_requirement(client_a, workspace, requirement.id)

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert response.json()["solution"]["source"] == TechnicalSolutionSource.REQUIREMENT.value


async def test_una_solucio_des_d_un_requisit_hi_queda_lligada(
    client_a, workspace, requirement
):
    response = await create_from_requirement(client_a, workspace, requirement.id)

    assert response.json()["solution"]["requirement_id"] == str(requirement.id)


async def test_un_requisit_d_una_altra_licitacio_es_rebutjat(client_a, db, workspace):
    other_tender = await _tender(db, title="Una altra licitacio")
    alie = await _requirement(db, other_tender)

    response = await create_from_requirement(client_a, workspace, alie.id)

    assert response.status_code == 404


async def test_un_requisit_d_un_altre_lot_es_rebutjat(client_a, db, tenant_a):
    """Un workspace de lot nomes hereta els requisits del seu lot i els generals.

    Mateixa regla que la checklist i que el motor de compliment
    (`applies_to_workspace_lot`): comprovar nomes la licitacio deixaria entrar
    l'exigencia d'un lot al qual no ens presentem.
    """
    user, company = tenant_a
    tender = await _tender(db, title="Licitacio amb lots")
    lots = [
        TenderLot(tender_id=tender.id, external_lot_id="1", lot_number=1),
        TenderLot(tender_id=tender.id, external_lot_id="2", lot_number=2),
    ]
    db.add_all(lots)
    await db.commit()
    nostre = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=tender, user_id=user.id, tender_lot_id=lots[0].id
        )
    ).workspace
    alie = await _requirement(db, tender, title="Requisit del lot 2")
    await db.execute(
        sa.update(Requirement)
        .where(Requirement.id == alie.id)
        .values(tender_lot_id=lots[1].id, scope=RequirementScope.LOT)
    )
    await db.commit()

    response = await create_from_requirement(client_a, nostre, alie.id)

    assert response.status_code == 404


async def test_un_requisit_general_val_per_a_un_workspace_de_lot(client_a, db, tenant_a):
    """Si el requisit no diu de quin lot es, val per a tots: es la regla del projecte."""
    user, company = tenant_a
    tender = await _tender(db, title="Licitacio amb lots")
    lot = TenderLot(tender_id=tender.id, external_lot_id="1", lot_number=1)
    db.add(lot)
    await db.commit()
    nostre = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=tender, user_id=user.id, tender_lot_id=lot.id
        )
    ).workspace
    general = await _requirement(db, tender, title="Requisit general")

    response = await create_from_requirement(client_a, nostre, general.id)

    assert response.status_code == 200
    assert response.json()["created"] is True


async def test_un_requisit_alie_i_un_d_inexistent_responen_igual(client_a, db, workspace):
    """Si no responguessin igual, provant IDs es podria saber que existeix."""
    other_tender = await _tender(db, title="Una altra licitacio")
    alie = await _requirement(db, other_tender)

    alie_response = await create_from_requirement(client_a, workspace, alie.id)
    fantasma_response = await create_from_requirement(client_a, workspace, uuid.uuid4())

    assert alie_response.status_code == fantasma_response.status_code == 404
    assert alie_response.json() == fantasma_response.json()


async def test_assenyalar_dues_vegades_el_mateix_requisit_no_duplica(
    client_a, workspace, requirement
):
    """Idempotent, com `create_workspace`: s'obre el que ja hi havia."""
    first = await create_from_requirement(client_a, workspace, requirement.id)
    second = await create_from_requirement(client_a, workspace, requirement.id)

    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["solution"]["id"] == first.json()["solution"]["id"]

    listed = await client_a.get(url(workspace))
    assert len(listed.json()) == 1


async def test_assenyalar_dues_vegades_no_deixa_dos_rastres_d_auditoria(
    client_a, db, workspace, requirement
):
    """La segona crida no crea res, aixi que no hi ha res a auditar.

    Un CREATE per cada pulsacio diria que s'han definit dues solucions quan
    nomes se n'ha definit una.
    """
    await create_from_requirement(client_a, workspace, requirement.id)
    await create_from_requirement(client_a, workspace, requirement.id)

    rows = await _audit(db, "workspace_technical_solution")
    creacions = [row for row in rows if row.action == AuditAction.CREATE]

    assert len(creacions) == 1
    files = await db.scalars(
        sa.select(WorkspaceTechnicalSolution).where(
            WorkspaceTechnicalSolution.workspace_id == workspace.id
        )
    )
    assert len(list(files.all())) == 1


async def test_el_titol_surt_del_generador_canonic_de_titols(
    client_a, db, workspace, requirement
):
    """El mateix helper que ja titula els items de checklist i del dossier.

    Ni el text cru del requisit ni cap identificador: una persona ha de poder
    llegir la fila sense obrir el plec.
    """
    response = await create_from_requirement(client_a, workspace, requirement.id)

    assert response.json()["solution"]["title"] == checklist_title(requirement)[:500]


async def test_una_solucio_des_d_un_requisit_neix_sense_definir(
    client_a, workspace, requirement
):
    response = await create_from_requirement(client_a, workspace, requirement.id)

    assert response.json()["solution"]["status"] == TechnicalSolutionStatus.NOT_STARTED.value


# --------------------------------------------------------------------------- #
# 21-26. PATCH                                                                  #
# --------------------------------------------------------------------------- #


async def test_es_poden_editar_els_camps_de_treball(client_a, workspace):
    solution = await a_solution(client_a, workspace)

    response = await client_a.patch(
        url(workspace, solution["id"]),
        json={
            "title": "Servidors en alta disponibilitat",
            "proposed_solution": "Dos nodes actius",
            "supplier_name": "Proveidor SL",
            "product_name": "Model X",
            "notes": "Pendent de confirmar terminis",
            "status": TechnicalSolutionStatus.DRAFTING.value,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Servidors en alta disponibilitat"
    assert body["proposed_solution"] == "Dos nodes actius"
    assert body["supplier_name"] == "Proveidor SL"
    assert body["product_name"] == "Model X"
    assert body["notes"] == "Pendent de confirmar terminis"
    assert body["status"] == TechnicalSolutionStatus.DRAFTING.value


async def test_el_patch_no_pot_canviar_l_origen(client_a, workspace, requirement):
    created = await create_from_requirement(client_a, workspace, requirement.id)
    solution = created.json()["solution"]

    response = await client_a.patch(
        url(workspace, solution["id"]), json={"source": TechnicalSolutionSource.MANUAL.value}
    )

    assert response.status_code == 200
    assert response.json()["source"] == TechnicalSolutionSource.REQUIREMENT.value


async def test_el_patch_no_pot_canviar_el_requisit(client_a, db, workspace, requirement):
    created = await create_from_requirement(client_a, workspace, requirement.id)
    solution = created.json()["solution"]
    tender = await db.get(Tender, workspace.tender_id)
    other = await _requirement(db, tender, title="Un altre requisit")

    response = await client_a.patch(
        url(workspace, solution["id"]), json={"requirement_id": str(other.id)}
    )

    assert response.status_code == 200
    assert response.json()["requirement_id"] == str(requirement.id)


async def test_el_patch_no_pot_validar(client_a, workspace):
    """Validar te signatura, data i motiu. El PATCH generic no en te cap."""
    solution = await a_solution(client_a, workspace)

    response = await client_a.patch(
        url(workspace, solution["id"]),
        json={"status": TechnicalSolutionStatus.VALIDATED.value},
    )

    assert response.status_code == 422


async def test_canviar_les_notes_no_caduca_la_validacio(client_a, workspace):
    solution = await a_validated_solution(client_a, workspace)

    response = await client_a.patch(
        url(workspace, solution["id"]), json={"notes": "Recordatori intern"}
    )

    assert response.status_code == 200
    assert response.json()["validation_stale"] is False
    assert response.json()["validation_stale_reasons"] == []


async def test_canviar_el_responsable_no_caduca_la_validacio(client_a, db, workspace, tenant_a):
    user, _ = tenant_a
    solution = await a_validated_solution(client_a, workspace)

    response = await client_a.patch(
        url(workspace, solution["id"]), json={"owner_id": str(user.id)}
    )

    assert response.status_code == 200
    assert response.json()["owner_id"] == str(user.id)
    assert response.json()["validation_stale"] is False


async def test_el_patch_no_toca_un_altre_workspace(client_a, db, tenant_a, workspace):
    """Un ID d'una altra candidatura ha de ser indistingible d'un inexistent."""
    user, company = tenant_a
    other_tender = await _tender(db, title="Una altra licitacio")
    other = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=other_tender, user_id=user.id
        )
    ).workspace
    solution = await a_solution(client_a, other)

    aliena = await client_a.patch(url(workspace, solution["id"]), json={"title": "Canviat"})
    fantasma = await client_a.patch(url(workspace, str(uuid.uuid4())), json={"title": "Canviat"})

    assert aliena.status_code == fantasma.status_code == 404


# --------------------------------------------------------------------------- #
# 27-32. Validacio humana                                                       #
# --------------------------------------------------------------------------- #


async def test_no_es_pot_validar_una_proposta_buida(client_a, workspace):
    """Signar "hem revisat que oferirem" sense dir que s'ofereix no vol dir res."""
    solution = await a_solution(client_a, workspace, proposed_solution="   ")

    response = await client_a.post(
        url(workspace, solution["id"], "validation"), json={"note": "Endavant"}
    )

    assert response.status_code == 422


async def test_no_es_pot_validar_sense_motiu(client_a, workspace):
    solution = await a_solution(client_a, workspace, proposed_solution="Proposta")

    response = await client_a.post(
        url(workspace, solution["id"], "validation"), json={"note": "   "}
    )

    assert response.status_code == 422


async def test_una_solucio_manual_es_valida_sense_empremta_de_requisit(client_a, workspace):
    """No hi ha requisit: inventar-ne una empremta seria signar sobre el no-res."""
    solution = await a_validated_solution(client_a, workspace)

    assert solution["validated_requirement_fingerprint"] is None
    assert solution["validated_content_fingerprint"] is not None


async def test_una_solucio_lligada_es_valida_amb_l_empremta_del_requisit(
    client_a, workspace, requirement
):
    created = await create_from_requirement(client_a, workspace, requirement.id)
    solution_id = created.json()["solution"]["id"]
    await client_a.patch(url(workspace, solution_id), json={"proposed_solution": "Proposta"})

    response = await client_a.post(
        url(workspace, solution_id, "validation"), json={"note": "Revisat"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["validated_requirement_fingerprint"] is not None


async def test_l_empremta_del_contingut_es_sha256(client_a, workspace):
    solution = await a_validated_solution(client_a, workspace)

    empremta = solution["validated_content_fingerprint"]

    assert len(empremta) == 64
    assert set(empremta) <= set("0123456789abcdef")


async def test_la_validacio_desa_qui_quan_i_per_que(client_a, workspace, tenant_a):
    user, _ = tenant_a

    solution = await a_validated_solution(client_a, workspace)

    assert solution["validated_by"] == str(user.id)
    assert solution["validated_at"] is not None
    assert solution["validated_note"] == "Ho hem revisat"
    assert solution["status"] == TechnicalSolutionStatus.VALIDATED.value


# --------------------------------------------------------------------------- #
# 33-40. Vigencia de la validacio                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "camp,valor",
    [
        ("proposed_solution", "Una altra proposta"),
        ("supplier_name", "Un altre proveidor"),
        ("product_name", "Un altre model"),
        ("title", "Un altre titol"),
    ],
)
async def test_canviar_el_contingut_signat_caduca_la_validacio(
    client_a, workspace, camp, valor
):
    solution = await a_validated_solution(client_a, workspace)

    response = await client_a.patch(url(workspace, solution["id"]), json={camp: valor})

    assert response.json()["validation_stale"] is True
    assert response.json()["validation_stale_reasons"] == ["content_changed"]


async def test_la_validacio_caducada_no_esborra_la_firma(client_a, workspace, tenant_a):
    """Es historia: qui va signar i quan no desapareixen perque hagi canviat res."""
    user, _ = tenant_a
    solution = await a_validated_solution(client_a, workspace)

    response = await client_a.patch(
        url(workspace, solution["id"]), json={"proposed_solution": "Una altra"}
    )

    body = response.json()
    assert body["validated_by"] == str(user.id)
    assert body["validated_at"] is not None
    assert body["validated_note"] == "Ho hem revisat"
    assert body["status"] == TechnicalSolutionStatus.VALIDATED.value


async def test_si_el_requisit_canvia_el_que_demana_la_validacio_caduca(
    client_a, db, workspace, requirement
):
    created = await create_from_requirement(client_a, workspace, requirement.id)
    solution_id = created.json()["solution"]["id"]
    await client_a.patch(url(workspace, solution_id), json={"proposed_solution": "Proposta"})
    await client_a.post(url(workspace, solution_id, "validation"), json={"note": "Revisat"})

    await db.execute(
        sa.update(Requirement)
        .where(Requirement.id == requirement.id)
        .values(title="Ara demanem una altra certificacio")
    )
    await db.commit()

    row = await read_one(client_a, workspace, solution_id)

    assert row["validation_stale"] is True
    assert row["validation_stale_reasons"] == ["requirement_changed"]


async def test_si_el_requisit_desapareix_la_validacio_caduca(
    client_a, db, workspace, requirement
):
    created = await create_from_requirement(client_a, workspace, requirement.id)
    solution_id = created.json()["solution"]["id"]
    await client_a.patch(url(workspace, solution_id), json={"proposed_solution": "Proposta"})
    await client_a.post(url(workspace, solution_id, "validation"), json={"note": "Revisat"})

    await db.execute(sa.delete(Requirement).where(Requirement.id == requirement.id))
    await db.commit()

    row = await read_one(client_a, workspace, solution_id)

    # La FK es SET NULL: la solucio sobreviu i el que es perd es el vincle.
    assert row["requirement_id"] is None
    assert row["source"] == TechnicalSolutionSource.REQUIREMENT.value
    assert row["validation_stale"] is True
    assert row["validation_stale_reasons"] == ["requirement_missing"]
    assert "requirement_changed" not in row["validation_stale_reasons"]
    # La feina escrita no la pot esborrar que desaparegui el requisit.
    assert row["proposed_solution"] == "Proposta"
    assert row["validated_note"] == "Revisat"


async def test_una_solucio_manual_no_caduca_mai_per_un_requisit(client_a, workspace):
    solution = await a_validated_solution(client_a, workspace)

    row = await read_one(client_a, workspace, solution["id"])

    assert row["validation_stale"] is False
    assert "requirement_missing" not in row["validation_stale_reasons"]
    assert "requirement_changed" not in row["validation_stale_reasons"]


async def test_tornar_a_validar_una_solucio_caducada_la_torna_vigent(client_a, workspace):
    solution = await a_validated_solution(client_a, workspace)
    stale = await client_a.patch(
        url(workspace, solution["id"]), json={"proposed_solution": "Una altra proposta"}
    )
    assert stale.json()["validation_stale"] is True

    response = await client_a.post(
        url(workspace, solution["id"], "validation"), json={"note": "Revisat de nou"}
    )

    assert response.status_code == 200
    assert response.json()["validation_stale"] is False
    assert response.json()["validated_note"] == "Revisat de nou"


# --------------------------------------------------------------------------- #
# 41-44. Auditoria                                                              #
# --------------------------------------------------------------------------- #


async def _audit(db, entity_type: str) -> list[AuditLog]:
    return list(
        (
            await db.scalars(
                sa.select(AuditLog)
                .where(AuditLog.entity_type == entity_type)
                .order_by(AuditLog.created_at)
            )
        ).all()
    )


async def test_una_creacio_manual_queda_auditada(client_a, db, workspace):
    solution = await a_solution(client_a, workspace)

    rows = await _audit(db, "workspace_technical_solution")

    assert [str(row.entity_id) for row in rows] == [solution["id"]]


async def test_una_creacio_des_d_un_requisit_queda_auditada(client_a, db, workspace, requirement):
    created = await create_from_requirement(client_a, workspace, requirement.id)

    rows = await _audit(db, "workspace_technical_solution")

    assert [str(row.entity_id) for row in rows] == [created.json()["solution"]["id"]]
    assert str(requirement.id) in (rows[0].summary or "")


async def test_una_edicio_queda_auditada(client_a, db, workspace):
    solution = await a_solution(client_a, workspace)

    await client_a.patch(url(workspace, solution["id"]), json={"title": "Un altre titol"})

    rows = await _audit(db, "workspace_technical_solution")
    assert len(rows) == 2


async def test_una_validacio_queda_auditada(client_a, db, workspace):
    solution = await a_validated_solution(client_a, workspace)

    rows = await _audit(db, "technical_solution_validation")

    assert [str(row.entity_id) for row in rows] == [solution["id"]]


async def test_l_auditoria_no_guarda_cap_text_del_contingut(client_a, db, workspace):
    """`summary` es rastre, no contingut.

    Hi va el que cal per seguir el fil —identificadors, estat, origen— i mai el
    que ha escrit una persona: la proposta, les notes o el motiu de la validacio
    poden portar informacio sensible de l'oferta.
    """
    proposta = "PROPOSTA-SECRETA-arquitectura-i-preus-interns"
    notes = "NOTES-SECRETES-marge-real"
    motiu = "MOTIU-SECRET-per-que-ho-donem-per-bo"

    solution = await a_solution(client_a, workspace, proposed_solution=proposta, notes=notes)
    await client_a.patch(url(workspace, solution["id"]), json={"notes": notes})
    await client_a.post(url(workspace, solution["id"], "validation"), json={"note": motiu})

    summaries = list(
        (await db.scalars(sa.select(AuditLog.summary).where(AuditLog.summary.is_not(None)))).all()
    )

    assert summaries, "no s'ha auditat res"
    for summary in summaries:
        assert proposta not in summary
        assert notes not in summary
        assert motiu not in summary
        assert len(summary) <= 500


# --------------------------------------------------------------------------- #
# 45-47. Multitenant                                                            #
# --------------------------------------------------------------------------- #


async def test_una_solucio_d_una_altra_empresa_no_es_llegible(
    client_a, client_b, db, tenant_b, workspace
):
    other_user, other_company = tenant_b
    other_tender = await _tender(db, title="Licitacio de l'altra empresa")
    other_workspace = (
        await workspace_service.create_workspace(
            db, company_id=other_company.id, tender=other_tender, user_id=other_user.id
        )
    ).workspace
    await a_solution(client_b, other_workspace)

    response = await client_a.get(url(other_workspace))

    assert response.status_code == 404


async def test_una_solucio_d_una_altra_empresa_no_es_modificable(
    client_a, client_b, db, tenant_b, workspace
):
    other_user, other_company = tenant_b
    other_tender = await _tender(db, title="Licitacio de l'altra empresa")
    other_workspace = (
        await workspace_service.create_workspace(
            db, company_id=other_company.id, tender=other_tender, user_id=other_user.id
        )
    ).workspace
    solution = await a_solution(client_b, other_workspace)

    response = await client_a.patch(
        url(other_workspace, solution["id"]), json={"title": "Canviat"}
    )

    assert response.status_code == 404
    unchanged = await db.get(WorkspaceTechnicalSolution, uuid.UUID(solution["id"]))
    await db.refresh(unchanged)
    assert unchanged.title == "Solucio"


async def test_un_id_alie_no_diu_si_existeix(client_a, client_b, db, tenant_b, workspace):
    """Mateixa resposta per a un ID d'una altra empresa i per a un d'inventat."""
    other_user, other_company = tenant_b
    other_tender = await _tender(db, title="Licitacio de l'altra empresa")
    other_workspace = (
        await workspace_service.create_workspace(
            db, company_id=other_company.id, tender=other_tender, user_id=other_user.id
        )
    ).workspace
    solution = await a_solution(client_b, other_workspace)

    alie = await client_a.patch(url(workspace, solution["id"]), json={"title": "Canviat"})
    fantasma = await client_a.patch(url(workspace, str(uuid.uuid4())), json={"title": "Canviat"})

    assert alie.status_code == fantasma.status_code == 404
    assert alie.json() == fantasma.json()


# --------------------------------------------------------------------------- #
# Lectura                                                                       #
# --------------------------------------------------------------------------- #


async def test_la_lectura_porta_tot_el_que_cal_per_pintar_la_fila(client_a, workspace):
    solution = await a_validated_solution(client_a, workspace, supplier_name="Proveidor SL")

    esperats = {
        "id",
        "workspace_id",
        "requirement_id",
        "title",
        "proposed_solution",
        "supplier_name",
        "product_name",
        "owner_id",
        "status",
        "source",
        "notes",
        "validated_by",
        "validated_at",
        "validated_note",
        "validated_requirement_fingerprint",
        "validated_content_fingerprint",
        "validation_stale",
        "validation_stale_reasons",
        "created_by",
        "updated_by",
        "created_at",
        "updated_at",
    }

    assert esperats <= set(solution)


async def test_una_solucio_sense_validar_no_es_caducada(client_a, workspace):
    solution = await a_solution(client_a, workspace)

    assert solution["validation_stale"] is False
    assert solution["validation_stale_reasons"] == []


async def test_el_llistat_nomes_porta_les_d_aquesta_candidatura(
    client_a, db, tenant_a, workspace
):
    user, company = tenant_a
    other_tender = await _tender(db, title="Una altra licitacio")
    other = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=other_tender, user_id=user.id
        )
    ).workspace
    await a_solution(client_a, workspace, title="D'aquesta")
    await a_solution(client_a, other, title="De l'altra")

    response = await client_a.get(url(workspace))

    assert [row["title"] for row in response.json()] == ["D'aquesta"]
