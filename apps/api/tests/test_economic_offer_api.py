"""Oferta economica: servei i endpoints.

Tres fronteres que aquests tests protegeixen per sobre de tot:

* **Ocult no es desconegut.** Un `technical` no rep `unit_cost_ex_vat: null`, rep
  `internal_economics: null`. Si tots dos casos arribessin igual, ningu no podria
  distingir "no t'ho diem" de "encara no ho sabem", i la pantalla ensenyaria feina
  per fer que ja esta feta.
* **Desconegut no es zero, ni "dins de pressupost".** Sense cost no hi ha marge,
  sense durada no hi ha totals temporals i sense maxim de plec no es pot dir que
  l'oferta hi cap. S1 ja ho fa aixi i aqui nomes es comprova que no ho desfem.
* **Dos editors no es trepitgen.** Tota mutacio porta `expected_revision` i el
  contador puja amb un compare-and-swap atomic.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from decimal import Decimal

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
    RequirementScope,
    ReviewStatus,
    SourceType,
    TechnicalSolutionSource,
)
from app.models.identity import CompanyMembership
from app.models.offer import EconomicOffer, EconomicOfferLot, EconomicOfferSection
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tenders import Tender, TenderLot
from app.models.workspace import BidWorkspace, WorkspaceTechnicalSolution
from app.offer.lot_label import GLOBAL_LABEL
from app.services import economic_offer as service
from tests.conftest import grant_go

EUR = "EUR"


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
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
        expediente_number="9/2026",
        title=title,
        base_budget_without_tax=budget,
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


async def _lots(db, tender, *, budgets=(None, None)) -> list[TenderLot]:
    lots = [
        TenderLot(
            tender_id=tender.id,
            external_lot_id=str(number),
            lot_number=number,
            title=f"Lot numero {number}",
            base_budget_without_tax=budget,
        )
        for number, budget in enumerate(budgets, start=1)
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
    await grant_go(db, workspace)
    return workspace


@pytest.fixture
async def global_workspace(db, tenant_a):
    """Cas B de C-1: candidatura global i plec sense lots."""
    _user, company = tenant_a
    return await _workspace(db, company, await _tender(db))


@pytest.fixture
async def lot_workspace(db, tenant_a):
    """Cas A de C-1: la candidatura es d'un lot concret."""
    _user, company = tenant_a
    tender = await _tender(db)
    lots = await _lots(db, tender)
    return await _workspace(db, company, tender, tender_lot_id=lots[0].id)


@pytest.fixture
async def selectable_workspace(db, tenant_a):
    """Cas C de C-1: plec amb lots i candidatura global."""
    _user, company = tenant_a
    tender = await _tender(db)
    await _lots(db, tender)
    return await _workspace(db, company, tender)


def url(workspace, *parts: str) -> str:
    base = (
        f"/api/v1/companies/{workspace.company_id}"
        f"/workspaces/{workspace.id}/economic-offer"
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


async def open_offer(client, workspace) -> dict:
    response = await client.post(url(workspace))
    assert response.status_code == 200, response.text
    return response.json()["offer"]


async def set_duration(client, workspace, lot_id, months, *, expected=None):
    return await client.patch(
        url(workspace, "lots", lot_id),
        json={
            "duration_months": months,
            "expected_revision": (
                expected if expected is not None else await revision(client, workspace)
            ),
        },
    )


async def read(client, workspace) -> dict:
    response = await client.get(url(workspace))
    assert response.status_code == 200, response.text
    return response.json()


async def revision(client, workspace) -> int:
    return (await read(client, workspace))["revision"]


async def add_section(client, workspace, lot_id, *, title="Inversio inicial"):
    return await client.post(
        url(workspace, "lots", lot_id, "sections"),
        json={"title": title, "expected_revision": await revision(client, workspace)},
    )


async def a_section(client, workspace, lot_id, **kwargs) -> dict:
    response = await add_section(client, workspace, lot_id, **kwargs)
    assert response.status_code == 200, response.text
    return response.json()["lots"][0]["sections"][-1]


async def add_line(client, workspace, section_id, **body):
    payload = {
        "concept": "Servidor",
        "quantity": "1",
        "pricing_basis": "one_off",
        "expected_revision": await revision(client, workspace),
    }
    payload.update(body)
    return await client.post(url(workspace, "sections", section_id, "lines"), json=payload)


async def a_line(client, workspace, section_id, **body) -> dict:
    response = await add_line(client, workspace, section_id, **body)
    assert response.status_code == 200, response.text
    return find_line(response.json())


def find_line(offer: dict, index: int = -1) -> dict:
    for lot in offer["lots"]:
        for section in lot["sections"]:
            if section["lines"]:
                return section["lines"][index]
    raise AssertionError("no hi ha cap linia")


def only_lot(offer: dict) -> dict:
    return offer["lots"][0]


async def a_workspace_with_one_line(client, workspace, **line) -> tuple[dict, dict]:
    offer = await open_offer(client, workspace)
    section = await a_section(client, workspace, only_lot(offer)["id"])
    row = await a_line(client, workspace, section["id"], **line)
    return section, row


# --------------------------------------------------------------------------- #
# 1-7. Arrel i inicialitzacio (C-1)                                             #
# --------------------------------------------------------------------------- #


async def test_obrir_l_oferta_dues_vegades_no_en_crea_dues(client_a, global_workspace):
    primera = await client_a.post(url(global_workspace))
    segona = await client_a.post(url(global_workspace))

    assert primera.json()["created"] is True
    assert segona.json()["created"] is False
    assert segona.json()["offer"]["id"] == primera.json()["offer"]["id"]


async def test_una_oferta_neix_en_euros(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)

    assert offer["currency"] == EUR
    assert offer["revision"] == 0


async def test_una_candidatura_d_un_lot_neix_amb_aquell_lot(client_a, db, lot_workspace):
    """Cas A: no hi ha res a triar, i per tant no se li pregunta a ningu."""
    offer = await open_offer(client_a, lot_workspace)

    assert len(offer["lots"]) == 1
    assert only_lot(offer)["tender_lot_id"] == str(lot_workspace.tender_lot_id)
    assert only_lot(offer)["label"] == "Lot 1 · Lot numero 1"
    assert offer["can_select_lots"] is False


async def test_un_plec_sense_lots_neix_amb_l_oferta_global(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)

    assert len(offer["lots"]) == 1
    assert only_lot(offer)["tender_lot_id"] is None
    assert only_lot(offer)["label"] == GLOBAL_LABEL
    assert offer["can_select_lots"] is False


async def test_un_plec_amb_lots_i_candidatura_global_neix_buida(
    client_a, selectable_workspace
):
    """Cas C: a quins lots ens presentem ho diu una persona, no s'endevina."""
    offer = await open_offer(client_a, selectable_workspace)

    assert offer["lots"] == []
    assert offer["can_select_lots"] is True


@pytest.mark.parametrize("role", [MembershipRole.VIEWER, MembershipRole.TECHNICAL])
async def test_qui_no_mana_sobre_els_preus_no_obre_l_oferta(
    client_a, db, tenant_a, global_workspace, role
):
    await set_role(db, tenant_a, role)

    response = await client_a.post(url(global_workspace))

    assert response.status_code == 403


async def test_un_bid_manager_obre_l_oferta(client_a, db, tenant_a, global_workspace):
    await set_role(db, tenant_a, MembershipRole.BID_MANAGER)

    response = await client_a.post(url(global_workspace))

    assert response.status_code == 200, response.text


async def test_obrir_una_oferta_que_ja_existeix_no_diu_que_l_ha_creada(
    client_a, db, global_workspace
):
    """200 i no 201: la segona crida no ha creat res, i el codi ho ha de dir.

    Es el que fan `create_workspace` i la creacio des d'un requisit. Qui vulgui
    distingir els dos casos te `created`.
    """
    primera = await client_a.post(url(global_workspace))
    abans = await revision(client_a, global_workspace)
    rastres = len(await audit_rows(db, "economic_offer"))

    segona = await client_a.post(url(global_workspace))

    assert primera.status_code == segona.status_code == 200
    assert segona.json()["created"] is False
    assert segona.json()["offer"]["id"] == primera.json()["offer"]["id"]
    assert await revision(client_a, global_workspace) == abans
    assert len(await audit_rows(db, "economic_offer")) == rastres


# --------------------------------------------------------------------------- #
# 8-15. Seleccio de lots                                                        #
# --------------------------------------------------------------------------- #


async def test_una_persona_tria_a_quin_lot_es_presenta(client_a, db, selectable_workspace):
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()

    response = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    assert only_lot(response.json()["offer"])["tender_lot_id"] == str(lots[0].id)


async def test_un_lot_d_una_altra_licitacio_no_es_pot_oferir(
    client_a, db, selectable_workspace
):
    await open_offer(client_a, selectable_workspace)
    altre_tender = await _tender(db, title="Una altra licitacio")
    alie = (await _lots(db, altre_tender))[0]

    response = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(alie.id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )
    fantasma = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(uuid.uuid4()),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )

    assert response.status_code == fantasma.status_code == 404
    assert response.json() == fantasma.json()


async def test_triar_dues_vegades_el_mateix_lot_no_el_duplica(
    client_a, db, selectable_workspace
):
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()
    cos = {
        "tender_lot_id": str(lots[0].id),
        "expected_revision": await revision(client_a, selectable_workspace),
    }
    primera = await client_a.post(url(selectable_workspace, "lots", "from-tender-lot"), json=cos)

    segona = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )

    assert primera.json()["created"] is True
    assert segona.json()["created"] is False
    assert len(segona.json()["offer"]["lots"]) == 1


async def test_triar_un_lot_que_ja_hi_es_no_mou_el_contador_ni_deixa_rastre(
    client_a, db, selectable_workspace
):
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()
    await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )
    abans = await revision(client_a, selectable_workspace)
    rastres_abans = len(await audit_rows(db, "economic_offer_lot"))

    await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={"tender_lot_id": str(lots[0].id), "expected_revision": abans},
    )

    assert await revision(client_a, selectable_workspace) == abans
    assert len(await audit_rows(db, "economic_offer_lot")) == rastres_abans


async def _no_admet_mes_lots(client, db, workspace) -> None:
    await open_offer(client, workspace)
    tender_lot = await db.scalar(
        sa.select(TenderLot).where(TenderLot.tender_id == workspace.tender_id)
    )

    response = await client.post(
        url(workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(tender_lot.id if tender_lot else uuid.uuid4()),
            "expected_revision": await revision(client, workspace),
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "economic_offer_lot_selection_not_allowed"


async def test_una_candidatura_d_un_lot_no_admet_mes_lots(client_a, db, lot_workspace):
    """Cas A: el lot el determina la candidatura, no una tria."""
    await _no_admet_mes_lots(client_a, db, lot_workspace)


async def test_una_oferta_global_no_admet_lots(client_a, db, global_workspace):
    """Cas B: el plec no en te, aixi que no hi ha res a triar."""
    await _no_admet_mes_lots(client_a, db, global_workspace)


async def test_un_lot_triat_es_pot_arxivar(client_a, db, selectable_workspace):
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()
    triat = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )
    lot_id = only_lot(triat.json()["offer"])["id"]

    response = await client_a.post(
        url(selectable_workspace, "lots", lot_id, "archive"),
        json={"expected_revision": await revision(client_a, selectable_workspace)},
    )

    assert response.status_code == 200, response.text
    assert only_lot(response.json())["archived_at"] is not None


async def _lot_obligatori_no_arxivable(client, workspace) -> None:
    offer = await open_offer(client, workspace)

    response = await client.post(
        url(workspace, "lots", only_lot(offer)["id"], "archive"),
        json={"expected_revision": await revision(client, workspace)},
    )

    assert response.status_code == 409


async def test_el_lot_d_una_candidatura_de_lot_no_es_pot_arxivar(client_a, lot_workspace):
    """Sense el seu unic lot, l'oferta es quedaria sense cap bloc de preus."""
    await _lot_obligatori_no_arxivable(client_a, lot_workspace)


async def test_el_bloc_global_no_es_pot_arxivar(client_a, global_workspace):
    await _lot_obligatori_no_arxivable(client_a, global_workspace)


# --------------------------------------------------------------------------- #
# Durada del lot (C-3)                                                          #
# --------------------------------------------------------------------------- #


async def test_la_durada_del_lot_s_escriu_a_ma(client_a, global_workspace):
    """C-3: manual sempre. El sistema no la dedueix de res."""
    offer = await open_offer(client_a, global_workspace)

    response = await set_duration(client_a, global_workspace, only_lot(offer)["id"], 24)

    assert response.status_code == 200, response.text
    assert only_lot(response.json())["duration_months"] == 24


async def test_la_durada_escrita_es_llegeix(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    await set_duration(client_a, global_workspace, only_lot(offer)["id"], 24)

    assert only_lot(await read(client_a, global_workspace))["duration_months"] == 24


async def test_una_linia_mensual_sense_durada_propia_hereta_la_del_lot(
    client_a, global_workspace
):
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]
    await set_duration(client_a, global_workspace, lot_id, 24)
    section = await a_section(client_a, global_workspace, lot_id)

    line = await a_line(
        client_a,
        global_workspace,
        section["id"],
        pricing_basis="monthly",
        offered_unit_price_ex_vat="100",
    )

    assert line["effective_duration_months"] == 24
    assert Decimal(line["periods"]) == Decimal("24")
    assert Decimal(line["totals"]["total_revenue"]) == Decimal("2400")


async def test_una_linia_anual_amb_trenta_mesos_de_lot_son_dues_anualitats_i_mitja(
    client_a, global_workspace
):
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]
    await set_duration(client_a, global_workspace, lot_id, 30)
    section = await a_section(client_a, global_workspace, lot_id)

    line = await a_line(
        client_a,
        global_workspace,
        section["id"],
        pricing_basis="annual",
        offered_unit_price_ex_vat="1200",
    )

    assert Decimal(line["periods"]) == Decimal("2.5")
    assert Decimal(line["totals"]["total_revenue"]) == Decimal("3000")


async def test_la_durada_de_la_linia_guanya_sobre_la_del_lot(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]
    await set_duration(client_a, global_workspace, lot_id, 24)
    section = await a_section(client_a, global_workspace, lot_id)

    line = await a_line(
        client_a,
        global_workspace,
        section["id"],
        pricing_basis="monthly",
        duration_months_override=6,
    )

    assert line["effective_duration_months"] == 6


async def test_la_durada_del_lot_es_pot_treure(client_a, global_workspace):
    """`null` no vol dir "no ho toquis": vol dir "ja no la sabem"."""
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]
    await set_duration(client_a, global_workspace, lot_id, 24)

    response = await set_duration(client_a, global_workspace, lot_id, None)

    assert response.status_code == 200, response.text
    assert only_lot(response.json())["duration_months"] is None


async def test_sense_durada_de_lot_els_totals_temporals_tornen_a_ser_desconeguts(
    client_a, global_workspace
):
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]
    await set_duration(client_a, global_workspace, lot_id, 24)
    section = await a_section(client_a, global_workspace, lot_id)
    await a_line(
        client_a,
        global_workspace,
        section["id"],
        pricing_basis="monthly",
        offered_unit_price_ex_vat="100",
    )

    await set_duration(client_a, global_workspace, lot_id, None)

    line = find_line(await read(client_a, global_workspace))
    assert line["effective_duration_months"] is None
    assert line["periods"] is None
    assert line["totals"]["total_revenue"] is None


async def test_ometre_la_durada_no_la_toca(client_a, global_workspace):
    """Omes no es `null`, i el sentinella ha de poder distingir-los."""
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]
    await set_duration(client_a, global_workspace, lot_id, 24)

    response = await client_a.patch(
        url(global_workspace, "lots", lot_id),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    assert response.status_code == 200, response.text
    assert only_lot(response.json())["duration_months"] == 24


@pytest.mark.parametrize("mesos", [0, -1])
async def test_un_lot_no_pot_durar_zero_mesos(client_a, global_workspace, mesos):
    offer = await open_offer(client_a, global_workspace)

    response = await set_duration(client_a, global_workspace, only_lot(offer)["id"], mesos)

    assert response.status_code == 422


async def test_no_es_pot_datar_un_lot_arxivat(client_a, db, selectable_workspace):
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()
    triat = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )
    lot_id = only_lot(triat.json()["offer"])["id"]
    await client_a.post(
        url(selectable_workspace, "lots", lot_id, "archive"),
        json={"expected_revision": await revision(client_a, selectable_workspace)},
    )

    response = await set_duration(client_a, selectable_workspace, lot_id, 24)

    assert response.status_code == 409


@pytest.mark.parametrize("role", [MembershipRole.VIEWER, MembershipRole.TECHNICAL])
async def test_qui_no_mana_sobre_els_preus_no_data_el_lot(
    client_a, db, tenant_a, global_workspace, role
):
    offer = await open_offer(client_a, global_workspace)
    await set_role(db, tenant_a, role)

    response = await set_duration(client_a, global_workspace, only_lot(offer)["id"], 24)

    assert response.status_code == 403


async def test_una_durada_enviada_sobre_una_revisio_vella_es_rebutjada(
    client_a, global_workspace
):
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]
    await set_duration(client_a, global_workspace, lot_id, 24)

    response = await set_duration(client_a, global_workspace, lot_id, 36, expected=0)

    assert response.status_code == 409
    assert only_lot(await read(client_a, global_workspace))["duration_months"] == 24


async def test_datar_un_lot_puja_el_contador_un_sol_cop(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    abans = await revision(client_a, global_workspace)

    await set_duration(client_a, global_workspace, only_lot(offer)["id"], 24)

    assert await revision(client_a, global_workspace) == abans + 1


async def test_tornar_a_escriure_la_mateixa_durada_no_es_una_modificacio(
    client_a, db, global_workspace
):
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]
    await set_duration(client_a, global_workspace, lot_id, 24)
    abans = await revision(client_a, global_workspace)
    rastres = len(await audit_rows(db, "economic_offer_lot"))

    response = await set_duration(client_a, global_workspace, lot_id, 24)

    assert response.status_code == 200
    assert await revision(client_a, global_workspace) == abans
    assert len(await audit_rows(db, "economic_offer_lot")) == rastres


async def test_un_canvi_real_de_durada_queda_auditat(client_a, db, global_workspace):
    offer = await open_offer(client_a, global_workspace)

    await set_duration(client_a, global_workspace, only_lot(offer)["id"], 24)

    files = await audit_rows(db, "economic_offer_lot")
    assert [f.action for f in files] == [AuditAction.UPDATE]


async def test_el_label_del_lot_no_es_pot_reescriure(client_a, global_workspace):
    """C-13: es una fotografia, i no viatja mai al cos d'una peticio."""
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]

    response = await client_a.patch(
        url(global_workspace, "lots", lot_id),
        json={
            "label": "El que jo vulgui",
            "tender_lot_id": str(uuid.uuid4()),
            "position": 7,
            "duration_months": 24,
            "expected_revision": await revision(client_a, global_workspace),
        },
    )

    lot = only_lot(response.json())
    assert lot["label"] == GLOBAL_LABEL
    assert lot["tender_lot_id"] is None
    assert lot["position"] == 0
    assert lot["duration_months"] == 24


# --------------------------------------------------------------------------- #
# Limits: d'on surt exactament el maxim                                         #
# --------------------------------------------------------------------------- #


async def test_el_maxim_d_un_lot_surt_del_pressupost_del_seu_lot(client_a, db, tenant_a):
    """Del `TenderLot`, no del `Tender`: cada lot te el seu pressupost."""
    _user, company = tenant_a
    tender = await _tender(db, budget=Decimal("999999"))
    lots = await _lots(db, tender, budgets=(Decimal("5000"), Decimal("7000")))
    workspace = await _workspace(db, company, tender)
    await open_offer(client_a, workspace)
    triat = await client_a.post(
        url(workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, workspace),
        },
    )
    section = await a_section(client_a, workspace, only_lot(triat.json()["offer"])["id"])
    await a_line(client_a, workspace, section["id"], offered_unit_price_ex_vat="1000")

    limit = only_lot(await read(client_a, workspace))["limit"]

    assert Decimal(limit["tender_max"]) == Decimal("5000")
    assert Decimal(limit["difference_to_maximum"]) == Decimal("4000")
    assert limit["exceeds_lot_max"] is False


async def test_el_maxim_d_una_oferta_global_surt_del_pressupost_de_la_licitacio(
    client_a, db, tenant_a
):
    _user, company = tenant_a
    workspace = await _workspace(db, company, await _tender(db, budget=Decimal("8000")))
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")

    limit = only_lot(await read(client_a, workspace))["limit"]

    assert Decimal(limit["tender_max"]) == Decimal("8000")


async def test_el_maxim_no_surt_mai_del_valor_estimat_ni_del_pressupost_amb_iva(
    client_a, db, tenant_a
):
    """Son tres xifres diferents: nomes val el pressupost base SENSE IVA."""
    _user, company = tenant_a
    tender = await _tender(db, budget=None)
    await db.execute(
        sa.update(Tender)
        .where(Tender.id == tender.id)
        .values(estimated_value=Decimal("50000"), base_budget=Decimal("12100"))
    )
    await db.commit()
    workspace = await _workspace(db, company, tender)
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")

    limit = only_lot(await read(client_a, workspace))["limit"]

    assert limit["tender_max"] is None
    assert limit["exceeds_lot_max"] is None
    assert limit["difference_to_maximum"] is None


# --------------------------------------------------------------------------- #
# El contracte de lectura ensenya els arxivats                                  #
# --------------------------------------------------------------------------- #


async def test_els_arxivats_segueixen_sortint_a_la_lectura(
    client_a, db, selectable_workspace
):
    """La pantalla ha de poder ensenyar-los; el que no fan es sumar."""
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()
    triat = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )
    lot_id = only_lot(triat.json()["offer"])["id"]
    section = await a_section(client_a, selectable_workspace, lot_id)
    line = await a_line(
        client_a, selectable_workspace, section["id"], offered_unit_price_ex_vat="1000"
    )
    await client_a.post(
        url(selectable_workspace, "lines", line["id"], "archive"),
        json={"expected_revision": await revision(client_a, selectable_workspace)},
    )
    await client_a.post(
        url(selectable_workspace, "sections", section["id"], "archive"),
        json={"expected_revision": await revision(client_a, selectable_workspace)},
    )
    await client_a.post(
        url(selectable_workspace, "lots", lot_id, "archive"),
        json={"expected_revision": await revision(client_a, selectable_workspace)},
    )

    offer = await read(client_a, selectable_workspace)

    lot = only_lot(offer)
    assert lot["archived_at"] is not None
    assert lot["sections"][0]["archived_at"] is not None
    assert lot["sections"][0]["lines"][0]["archived_at"] is not None
    assert offer["totals"]["line_count"] == 0
    assert offer["totals"]["total_revenue"] is None


# --------------------------------------------------------------------------- #
# 16-20. Seccions                                                               #
# --------------------------------------------------------------------------- #


async def test_les_seccions_s_afegeixen_al_final(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    lot_id = only_lot(offer)["id"]

    await a_section(client_a, global_workspace, lot_id, title="Inversio inicial")
    await a_section(client_a, global_workspace, lot_id, title="Serveis recurrents")

    seccions = only_lot(await read(client_a, global_workspace))["sections"]
    assert [s["title"] for s in seccions] == ["Inversio inicial", "Serveis recurrents"]
    assert [s["position"] for s in seccions] == [0, 1]


async def test_una_seccio_es_pot_reanomenar(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])

    response = await client_a.patch(
        url(global_workspace, "sections", section["id"]),
        json={
            "title": "Equipament",
            "expected_revision": await revision(client_a, global_workspace),
        },
    )

    assert response.status_code == 200, response.text
    assert only_lot(response.json())["sections"][0]["title"] == "Equipament"


async def test_una_seccio_es_pot_arxivar(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])

    response = await client_a.post(
        url(global_workspace, "sections", section["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    assert response.status_code == 200, response.text
    assert only_lot(response.json())["sections"][0]["archived_at"] is not None


async def test_una_seccio_arxivada_ja_no_es_pot_editar(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])
    await client_a.post(
        url(global_workspace, "sections", section["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    response = await client_a.patch(
        url(global_workspace, "sections", section["id"]),
        json={
            "title": "Un altre nom",
            "expected_revision": await revision(client_a, global_workspace),
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "economic_offer_item_archived"


async def test_amb_el_lot_arxivat_no_es_pot_tocar_el_que_hi_penja(
    client_a, db, selectable_workspace
):
    """El fill no porta `archived_at`, pero tampoc es pot editar: la fotografia
    ha de dir que es va arxivar el lot, no cada linia de dins."""
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()
    triat = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )
    lot_id = only_lot(triat.json()["offer"])["id"]
    section = await a_section(client_a, selectable_workspace, lot_id)
    await client_a.post(
        url(selectable_workspace, "lots", lot_id, "archive"),
        json={"expected_revision": await revision(client_a, selectable_workspace)},
    )

    response = await client_a.patch(
        url(selectable_workspace, "sections", section["id"]),
        json={
            "title": "Canviat",
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )

    assert response.status_code == 409
    assert only_lot(await read(client_a, selectable_workspace))["sections"][0][
        "archived_at"
    ] is None


# --------------------------------------------------------------------------- #
# 21-34. Linies                                                                 #
# --------------------------------------------------------------------------- #


async def test_una_linia_s_afegeix_amb_el_que_s_ofereix(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        concept="Servidor d'aplicacions",
        quantity="2",
        offered_unit_price_ex_vat="1000.00",
    )

    assert line["concept"] == "Servidor d'aplicacions"
    assert Decimal(line["quantity"]) == Decimal("2")
    assert Decimal(line["offered_unit_price_ex_vat"]) == Decimal("1000")
    assert line["position"] == 0


async def test_una_linia_es_pot_editar(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(client_a, global_workspace)

    response = await client_a.patch(
        url(global_workspace, "lines", line["id"]),
        json={
            "concept": "Servidor de base de dades",
            "expected_revision": await revision(client_a, global_workspace),
        },
    )

    assert response.status_code == 200, response.text
    assert find_line(response.json())["concept"] == "Servidor de base de dades"


async def test_una_linia_es_pot_arxivar(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(client_a, global_workspace)

    response = await client_a.post(
        url(global_workspace, "lines", line["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    assert response.status_code == 200, response.text
    assert find_line(response.json())["archived_at"] is not None


async def test_una_linia_arxivada_ja_no_es_pot_editar(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(client_a, global_workspace)
    await client_a.post(
        url(global_workspace, "lines", line["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    response = await client_a.patch(
        url(global_workspace, "lines", line["id"]),
        json={
            "concept": "Canviat",
            "expected_revision": await revision(client_a, global_workspace),
        },
    )

    assert response.status_code == 409


async def test_els_diners_no_passen_mai_per_un_float(client_a, global_workspace):
    """0,1 + 0,2 en coma flotant no fa 0,3, i una oferta no es pot permetre aixo."""
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        quantity="3.3333",
        offered_unit_price_ex_vat="0.1000",
        unit_cost_ex_vat="0.2000",
    )

    assert Decimal(line["quantity"]) == Decimal("3.3333")
    assert Decimal(line["offered_unit_price_ex_vat"]) == Decimal("0.1")
    assert Decimal(line["internal_economics"]["unit_cost_ex_vat"]) == Decimal("0.2")


async def test_la_durada_propia_de_la_linia_mana(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])
    await client_a.patch(
        url(global_workspace, "sections", section["id"]),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )
    line = await a_line(
        client_a,
        global_workspace,
        section["id"],
        pricing_basis="monthly",
        duration_months_override=6,
    )

    assert line["effective_duration_months"] == 6


async def test_sense_durada_propia_la_linia_hereta_la_del_lot(
    client_a, db, global_workspace
):
    offer = await open_offer(client_a, global_workspace)
    await db.execute(
        sa.update(sa.table("economic_offer_lots", sa.column("duration_months"), sa.column("id")))
        .where(sa.column("id") == uuid.UUID(only_lot(offer)["id"]))
        .values(duration_months=24)
    )
    await db.commit()
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])

    line = await a_line(client_a, global_workspace, section["id"], pricing_basis="monthly")

    assert line["effective_duration_months"] == 24


async def test_sense_durada_enlloc_els_totals_temporals_no_es_saben(
    client_a, global_workspace
):
    """C-3: assumir dotze mesos seria inventar-se el contracte."""
    _section, line = await a_workspace_with_one_line(
        client_a, global_workspace, pricing_basis="monthly", offered_unit_price_ex_vat="100"
    )

    assert line["effective_duration_months"] is None
    assert line["periods"] is None
    assert line["totals"]["total_revenue"] is None


async def test_una_linia_pot_citar_un_requisit_del_seu_lot(
    client_a, db, global_workspace, requirement
):
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])

    line = await a_line(
        client_a, global_workspace, section["id"], requirement_id=str(requirement.id)
    )

    assert line["requirement_id"] == str(requirement.id)


async def test_un_requisit_d_un_altre_lot_no_es_pot_citar(
    client_a, db, selectable_workspace
):
    """Es comprova contra el lot DE LA LINIA, no contra el de la candidatura."""
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot)
            .where(TenderLot.tender_id == selectable_workspace.tender_id)
            .order_by(TenderLot.lot_number)
        )
    ).all()
    triat = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )
    section = await a_section(
        client_a, selectable_workspace, only_lot(triat.json()["offer"])["id"]
    )
    alie = await _requirement_row(db, selectable_workspace.tender_id, tender_lot_id=lots[1].id)

    response = await add_line(
        client_a, selectable_workspace, section["id"], requirement_id=str(alie.id)
    )

    assert response.status_code == 404


async def test_una_linia_pot_citar_una_solucio_tecnica_de_la_candidatura(
    client_a, db, global_workspace, tenant_a
):
    solution = await _solution(db, global_workspace)
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])

    line = await a_line(
        client_a, global_workspace, section["id"], technical_solution_id=str(solution.id)
    )

    assert line["technical_solution_id"] == str(solution.id)


async def test_una_solucio_d_una_altra_candidatura_no_es_pot_citar(
    client_a, db, tenant_a, global_workspace
):
    _user, company = tenant_a
    altra = await _workspace(db, company, await _tender(db, title="Una altra"))
    aliena = await _solution(db, altra)
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])

    response = await add_line(
        client_a, global_workspace, section["id"], technical_solution_id=str(aliena.id)
    )

    assert response.status_code == 404


async def test_una_linia_no_pot_dir_un_requisit_i_assenyalar_la_solucio_d_un_altre(
    client_a, db, global_workspace, requirement
):
    altre = await _requirement_row(db, global_workspace.tender_id)
    solution = await _solution(db, global_workspace, requirement_id=altre.id)
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])

    response = await add_line(
        client_a,
        global_workspace,
        section["id"],
        requirement_id=str(requirement.id),
        technical_solution_id=str(solution.id),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "economic_offer_link_mismatch"


async def test_una_solucio_manual_serveix_per_a_qualsevol_requisit(
    client_a, db, global_workspace, requirement
):
    """No resol cap requisit concret: no pot contradir el que digui la linia."""
    solution = await _solution(db, global_workspace)
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])

    line = await a_line(
        client_a,
        global_workspace,
        section["id"],
        requirement_id=str(requirement.id),
        technical_solution_id=str(solution.id),
    )

    assert line["requirement_id"] == str(requirement.id)
    assert line["technical_solution_id"] == str(solution.id)


# --------------------------------------------------------------------------- #
# 35-42. Redaccio: ocult no es desconegut                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "role", [MembershipRole.OWNER, MembershipRole.ADMIN, MembershipRole.BID_MANAGER]
)
async def test_qui_mana_sobre_els_preus_veu_cost_i_marge(
    client_a, db, tenant_a, global_workspace, role
):
    await a_workspace_with_one_line(
        client_a,
        global_workspace,
        unit_cost_ex_vat="600",
        offered_unit_price_ex_vat="1000",
    )
    await set_role(db, tenant_a, role)

    offer = await read(client_a, global_workspace)

    assert offer["can_view_internal_economics"] is True
    intern = find_line(offer)["internal_economics"]
    assert Decimal(intern["unit_cost_ex_vat"]) == Decimal("600")
    assert Decimal(intern["total_cost"]) == Decimal("600")
    assert Decimal(intern["margin_amount"]) == Decimal("400")
    assert offer["internal_economics"]["total_cost"] is not None


@pytest.mark.parametrize("role", [MembershipRole.TECHNICAL, MembershipRole.VIEWER])
async def test_qui_no_hi_mana_no_rep_el_bloc_intern(
    client_a, db, tenant_a, global_workspace, role
):
    """No es un `null` al cost: es el bloc que no hi es. Ocult != desconegut."""
    await a_workspace_with_one_line(
        client_a,
        global_workspace,
        unit_cost_ex_vat="600",
        offered_unit_price_ex_vat="1000",
    )
    await set_role(db, tenant_a, role)

    offer = await read(client_a, global_workspace)

    assert offer["can_view_internal_economics"] is False
    assert offer["internal_economics"] is None
    for lot in offer["lots"]:
        assert lot["internal_economics"] is None
        for section in lot["sections"]:
            assert section["internal_economics"] is None
            for line in section["lines"]:
                assert line["internal_economics"] is None


@pytest.mark.parametrize("role", [MembershipRole.TECHNICAL, MembershipRole.VIEWER])
async def test_cap_camp_derivat_deixa_deduir_el_cost(
    client_a, db, tenant_a, global_workspace, role
):
    """Ni el cost, ni el marge, ni el markup, ni cap equivalent mensual de cost."""
    await a_workspace_with_one_line(
        client_a,
        global_workspace,
        unit_cost_ex_vat="600",
        offered_unit_price_ex_vat="1000",
    )
    await set_role(db, tenant_a, role)

    cru = (await client_a.get(url(global_workspace))).text

    for filtrat in (
        "unit_cost_ex_vat",
        "total_cost",
        "margin_amount",
        "margin_on_sales_pct",
        "markup_on_cost_pct",
        "monthly_equivalent_cost",
        "incomplete_cost_lines",
        '"600',
    ):
        assert filtrat not in cru, filtrat


@pytest.mark.parametrize("role", [MembershipRole.TECHNICAL, MembershipRole.VIEWER])
async def test_el_que_s_ofereix_si_que_es_pot_veure(
    client_a, db, tenant_a, global_workspace, role
):
    """El preu ofert no es informacio interna: es el que es presentara."""
    await a_workspace_with_one_line(
        client_a,
        global_workspace,
        unit_cost_ex_vat="600",
        offered_unit_price_ex_vat="1000",
        vat_rate="21",
    )
    await set_role(db, tenant_a, role)

    line = find_line(await read(client_a, global_workspace))

    assert Decimal(line["offered_unit_price_ex_vat"]) == Decimal("1000")
    assert Decimal(line["totals"]["total_revenue"]) == Decimal("1000")
    assert Decimal(line["totals"]["vat_amount"]) == Decimal("210")


# --------------------------------------------------------------------------- #
# 43-53. Calcul: tot ve d'S1                                                    #
# --------------------------------------------------------------------------- #


async def test_una_entrega_unica_no_depen_de_la_durada(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        pricing_basis="one_off",
        quantity="2",
        offered_unit_price_ex_vat="1000",
    )

    assert Decimal(line["periods"]) == Decimal("1")
    assert Decimal(line["totals"]["total_revenue"]) == Decimal("2000")


async def test_una_linia_mensual_multiplica_pels_mesos(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        pricing_basis="monthly",
        quantity="1",
        offered_unit_price_ex_vat="100",
        duration_months_override=24,
    )

    assert Decimal(line["periods"]) == Decimal("24")
    assert Decimal(line["totals"]["total_revenue"]) == Decimal("2400")


async def test_trenta_mesos_son_dues_anualitats_i_mitja(client_a, global_workspace):
    """C-8: es prorrateja. Facturar periodes sencers seria una altra estructura."""
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        pricing_basis="annual",
        quantity="1",
        offered_unit_price_ex_vat="1200",
        duration_months_override=30,
    )

    assert Decimal(line["periods"]) == Decimal("2.5")
    assert Decimal(line["totals"]["total_revenue"]) == Decimal("3000")


async def test_per_unitat_no_es_multiplica_per_la_durada(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        pricing_basis="per_unit",
        quantity="500",
        offered_unit_price_ex_vat="2",
        duration_months_override=24,
    )

    assert Decimal(line["periods"]) == Decimal("1")
    assert Decimal(line["totals"]["total_revenue"]) == Decimal("1000")


async def test_un_cost_que_falta_no_val_zero(client_a, global_workspace):
    """Sumar-hi zero faria quadrar un marge que ningu no ha calculat."""
    _section, line = await a_workspace_with_one_line(
        client_a, global_workspace, offered_unit_price_ex_vat="1000"
    )

    intern = line["internal_economics"]
    assert intern["total_cost"] is None
    assert intern["margin_amount"] is None
    assert intern["incomplete_cost_lines"] == 1


async def test_es_pot_saber_l_ingres_sense_saber_el_cost(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(
        client_a, global_workspace, offered_unit_price_ex_vat="1000"
    )

    assert Decimal(line["totals"]["total_revenue"]) == Decimal("1000")
    assert line["internal_economics"]["total_cost"] is None


async def test_un_ingres_de_zero_no_dona_cap_percentatge(client_a, global_workspace):
    """Denominador zero: `None`, mai `0` ni `100`."""
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        unit_cost_ex_vat="0",
        offered_unit_price_ex_vat="0",
    )

    intern = line["internal_economics"]
    assert intern["margin_on_sales_pct"] is None
    assert intern["markup_on_cost_pct"] is None


async def test_una_oferta_sense_linies_no_te_totals(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)

    assert offer["totals"]["total_revenue"] is None
    assert offer["totals"]["line_count"] == 0


async def test_una_linia_arxivada_deixa_de_sumar(client_a, global_workspace):
    section, line = await a_workspace_with_one_line(
        client_a, global_workspace, offered_unit_price_ex_vat="1000"
    )
    await a_line(
        client_a, global_workspace, section["id"], offered_unit_price_ex_vat="500"
    )

    await client_a.post(
        url(global_workspace, "lines", line["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    offer = await read(client_a, global_workspace)
    assert Decimal(offer["totals"]["total_revenue"]) == Decimal("500")
    assert offer["totals"]["line_count"] == 1
    # Surt igualment a la lectura: qui mira la pantalla ha de poder veure-la.
    assert len(only_lot(offer)["sections"][0]["lines"]) == 2


async def test_arxivar_una_seccio_en_treu_les_linies_dels_totals(
    client_a, global_workspace
):
    section, _line = await a_workspace_with_one_line(
        client_a, global_workspace, offered_unit_price_ex_vat="1000"
    )

    await client_a.post(
        url(global_workspace, "sections", section["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    offer = await read(client_a, global_workspace)
    assert offer["totals"]["total_revenue"] is None
    assert offer["totals"]["line_count"] == 0


async def test_arxivar_un_lot_en_treu_tot_l_arbre_dels_totals(
    client_a, db, selectable_workspace
):
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()
    triat = await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )
    lot_id = only_lot(triat.json()["offer"])["id"]
    section = await a_section(client_a, selectable_workspace, lot_id)
    await a_line(
        client_a, selectable_workspace, section["id"], offered_unit_price_ex_vat="1000"
    )

    await client_a.post(
        url(selectable_workspace, "lots", lot_id, "archive"),
        json={"expected_revision": await revision(client_a, selectable_workspace)},
    )

    offer = await read(client_a, selectable_workspace)
    assert offer["totals"]["total_revenue"] is None
    assert offer["totals"]["line_count"] == 0


# --------------------------------------------------------------------------- #
# 54-59. Limits del plec                                                        #
# --------------------------------------------------------------------------- #


async def test_sense_maxim_de_plec_no_es_diu_si_se_supera(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(
        client_a, global_workspace, offered_unit_price_ex_vat="1000"
    )

    assert line["exceeds_unit_max"] is None


async def test_per_sota_del_maxim_unitari(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        offered_unit_price_ex_vat="900",
        tender_max_unit_price_ex_vat="1000",
    )

    assert line["exceeds_unit_max"] is False


async def test_per_sobre_del_maxim_unitari(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(
        client_a,
        global_workspace,
        offered_unit_price_ex_vat="1100",
        tender_max_unit_price_ex_vat="1000",
    )

    assert line["exceeds_unit_max"] is True


async def test_sense_pressupost_publicat_no_es_diu_que_l_oferta_hi_cap(
    client_a, tenant_a, db
):
    """Desconegut no es "dins de pressupost": seria afirmar-ho sobre el no-res."""
    _user, company = tenant_a
    workspace = await _workspace(db, company, await _tender(db, budget=None))
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")

    limit = only_lot(await read(client_a, workspace))["limit"]

    assert limit["tender_max"] is None
    assert limit["exceeds_lot_max"] is None
    assert limit["difference_to_maximum"] is None


async def test_una_oferta_per_sota_del_pressupost_del_lot(client_a, tenant_a, db):
    _user, company = tenant_a
    workspace = await _workspace(db, company, await _tender(db, budget=Decimal("5000")))
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")

    limit = only_lot(await read(client_a, workspace))["limit"]

    assert limit["exceeds_lot_max"] is False
    assert Decimal(limit["difference_to_maximum"]) == Decimal("4000")


async def test_una_oferta_per_sobre_del_pressupost_del_lot(client_a, tenant_a, db):
    _user, company = tenant_a
    workspace = await _workspace(db, company, await _tender(db, budget=Decimal("500")))
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")

    limit = only_lot(await read(client_a, workspace))["limit"]

    assert limit["exceeds_lot_max"] is True
    assert Decimal(limit["difference_to_maximum"]) == Decimal("-500")


# --------------------------------------------------------------------------- #
# 60-66. Concurrencia                                                           #
# --------------------------------------------------------------------------- #


async def test_cada_mutacio_puja_el_contador_exactament_un_cop(
    client_a, global_workspace
):
    offer = await open_offer(client_a, global_workspace)
    assert offer["revision"] == 0

    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])
    assert await revision(client_a, global_workspace) == 1

    await a_line(client_a, global_workspace, section["id"])
    assert await revision(client_a, global_workspace) == 2


async def test_una_revisio_endarrerida_es_rebutjada(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    await a_section(client_a, global_workspace, only_lot(offer)["id"])

    response = await add_section(client_a, global_workspace, only_lot(offer)["id"])
    obsoleta = await client_a.post(
        url(global_workspace, "lots", only_lot(offer)["id"], "sections"),
        json={"title": "Tard", "expected_revision": 0},
    )

    assert response.status_code == 200
    assert obsoleta.status_code == 409
    assert obsoleta.json()["error"]["code"] == "economic_offer_revision_conflict"


async def test_una_peticio_rebutjada_no_deixa_res_escrit(client_a, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    await a_section(client_a, global_workspace, only_lot(offer)["id"])
    abans = await read(client_a, global_workspace)

    await client_a.post(
        url(global_workspace, "lots", only_lot(offer)["id"], "sections"),
        json={"title": "No ha de quedar", "expected_revision": 0},
    )

    despres = await read(client_a, global_workspace)
    assert despres["revision"] == abans["revision"]
    assert [s["title"] for s in only_lot(despres)["sections"]] == [
        s["title"] for s in only_lot(abans)["sections"]
    ]


async def test_un_patch_que_no_canvia_res_no_mou_el_contador(client_a, global_workspace):
    """Si mogues la revisio, invalidaria la feina d'un altre sense haver escrit res."""
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])
    abans = await revision(client_a, global_workspace)

    response = await client_a.patch(
        url(global_workspace, "sections", section["id"]),
        json={"title": section["title"], "expected_revision": abans},
    )

    assert response.status_code == 200
    assert await revision(client_a, global_workspace) == abans


async def test_un_conflicte_d_arxivat_tampoc_mou_el_contador(client_a, global_workspace):
    _section, line = await a_workspace_with_one_line(client_a, global_workspace)
    await client_a.post(
        url(global_workspace, "lines", line["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )
    abans = await revision(client_a, global_workspace)

    response = await client_a.patch(
        url(global_workspace, "lines", line["id"]),
        json={"concept": "Canviat", "expected_revision": abans},
    )

    assert response.status_code == 409
    assert await revision(client_a, global_workspace) == abans


@pytest.mark.postgres
async def test_dos_editors_simultanis_i_nomes_un_guanya(
    db, session_factory, tenant_a, global_workspace
):
    """La cursa de debo, amb dues transaccions reals contra PostgreSQL.

    Amb un `SELECT` i despres un `UPDATE`, tots dos llegirien la mateixa revisio
    i tots dos escriurien: el segon es menjaria la feina del primer sense que
    ningu se n'assabentes. Amb el compare-and-swap, el segon espera al bloqueig
    de la fila i, quan hi arriba, la condicio ja no es compleix.

    El perdedor tampoc no ha de deixar la seva mutacio a mitges: el bump i la
    seva seccio son a la mateixa transaccio.
    """
    user, _company = tenant_a
    offer = EconomicOffer(
        company_id=global_workspace.company_id, workspace_id=global_workspace.id
    )
    db.add(offer)
    await db.flush()
    lot = EconomicOfferLot(
        company_id=offer.company_id, offer_id=offer.id, label=GLOBAL_LABEL, position=0
    )
    db.add(lot)
    await db.commit()
    offer_id, company_id, lot_id = offer.id, offer.company_id, lot.id

    # Sense sincronitzar-les, les dues transaccions s'executarien en serie i
    # aixo no provaria res: el solapament ha de ser obligatori.
    primer_ha_escrit = asyncio.Event()
    segon_ho_intenta = asyncio.Event()

    async def guanyador() -> str:
        async with session_factory() as session, session.begin():
            candidat = await session.get(EconomicOffer, offer_id)
            await service.bump_revision(session, candidat, 0, user)
            session.add(
                EconomicOfferSection(
                    company_id=company_id, offer_lot_id=lot_id, title="primer", position=0
                )
            )
            primer_ha_escrit.set()
            # No es tanca fins que l'altre ja hi ha entrat: el commit va despres.
            await segon_ho_intenta.wait()
        return "guanyat"

    async def perdedor() -> str:
        await primer_ha_escrit.wait()
        async with session_factory() as session:
            try:
                async with session.begin():
                    candidat = await session.get(EconomicOffer, offer_id)
                    # L'avis va ABANS de la sentencia que pot quedar bloquejada.
                    segon_ho_intenta.set()
                    await service.bump_revision(session, candidat, 0, user)
                    session.add(
                        EconomicOfferSection(
                            company_id=company_id,
                            offer_lot_id=lot_id,
                            title="segon",
                            position=1,
                        )
                    )
            except service.OfferRevisionConflictError:
                return "conflicte"
        return "guanyat"

    resultats = await asyncio.gather(guanyador(), perdedor())

    assert sorted(resultats) == ["conflicte", "guanyat"]

    db.expire_all()
    final = await db.scalar(
        sa.select(EconomicOffer.revision).where(EconomicOffer.id == offer_id)
    )
    seccions = (
        await db.scalars(
            sa.select(EconomicOfferSection.title).where(
                EconomicOfferSection.offer_lot_id == lot_id
            )
        )
    ).all()

    # Una sola pujada i una sola seccio: del perdedor no en queda res.
    assert final == 1
    assert len(list(seccions)) == 1


# --------------------------------------------------------------------------- #
# 67-72. Auditoria                                                              #
# --------------------------------------------------------------------------- #


async def audit_rows(db, entity_type: str) -> list[AuditLog]:
    return list(
        (
            await db.scalars(
                sa.select(AuditLog)
                .where(AuditLog.entity_type == entity_type)
                .order_by(AuditLog.created_at)
            )
        ).all()
    )


async def test_obrir_l_oferta_queda_auditat(client_a, db, global_workspace):
    offer = await open_offer(client_a, global_workspace)

    files = await audit_rows(db, "economic_offer")

    assert [str(f.entity_id) for f in files] == [offer["id"]]
    assert files[0].action == AuditAction.CREATE


async def test_triar_un_lot_queda_auditat(client_a, db, selectable_workspace):
    await open_offer(client_a, selectable_workspace)
    lots = (
        await db.scalars(
            sa.select(TenderLot).where(TenderLot.tender_id == selectable_workspace.tender_id)
        )
    ).all()

    await client_a.post(
        url(selectable_workspace, "lots", "from-tender-lot"),
        json={
            "tender_lot_id": str(lots[0].id),
            "expected_revision": await revision(client_a, selectable_workspace),
        },
    )

    assert len(await audit_rows(db, "economic_offer_lot")) == 1


async def test_el_cicle_d_una_seccio_queda_auditat(client_a, db, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])
    await client_a.patch(
        url(global_workspace, "sections", section["id"]),
        json={
            "title": "Un altre",
            "expected_revision": await revision(client_a, global_workspace),
        },
    )
    await client_a.post(
        url(global_workspace, "sections", section["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    accions = [f.action for f in await audit_rows(db, "economic_offer_section")]

    assert accions == [AuditAction.CREATE, AuditAction.UPDATE, AuditAction.ARCHIVE]


async def test_el_cicle_d_una_linia_queda_auditat(client_a, db, global_workspace):
    _section, line = await a_workspace_with_one_line(client_a, global_workspace)
    await client_a.patch(
        url(global_workspace, "lines", line["id"]),
        json={
            "concept": "Un altre",
            "expected_revision": await revision(client_a, global_workspace),
        },
    )
    await client_a.post(
        url(global_workspace, "lines", line["id"], "archive"),
        json={"expected_revision": await revision(client_a, global_workspace)},
    )

    accions = [f.action for f in await audit_rows(db, "economic_offer_line")]

    assert accions == [AuditAction.CREATE, AuditAction.UPDATE, AuditAction.ARCHIVE]


async def test_el_que_no_passa_no_s_audita(client_a, db, global_workspace):
    offer = await open_offer(client_a, global_workspace)
    section = await a_section(client_a, global_workspace, only_lot(offer)["id"])
    abans = len(await audit_rows(db, "economic_offer_section"))

    await client_a.patch(
        url(global_workspace, "sections", section["id"]),
        json={
            "title": section["title"],
            "expected_revision": await revision(client_a, global_workspace),
        },
    )

    assert len(await audit_rows(db, "economic_offer_section")) == abans


async def test_l_auditoria_no_guarda_cap_xifra_interna(client_a, db, global_workspace):
    """`summary` es rastre, no contingut.

    No es comprova buscant-hi els numeros: un UUID hexadecimal pot contenir "666"
    per atzar i el test seria fals segons la sort. Es comprova la FORMA: nomes
    identificadors i revisio, i res mes.
    """
    await a_workspace_with_one_line(
        client_a,
        global_workspace,
        unit_cost_ex_vat="666.66",
        offered_unit_price_ex_vat="999.99",
        notes="NOTA-SECRETA-marge-real",
    )

    summaries = list(
        (
            await db.scalars(
                sa.select(AuditLog.summary).where(
                    AuditLog.entity_type.like("economic_offer%"),
                    AuditLog.summary.is_not(None),
                )
            )
        ).all()
    )

    assert summaries
    # Nomes identificadors i el numero de revisio. Cap import, cap text lliure.
    permesos = re.compile(r"^(oferta|workspace|revisio)=[0-9a-f-]+$")
    for summary in summaries:
        for token in summary.split(" "):
            assert permesos.match(token), f"{token!r} a {summary!r}"


# --------------------------------------------------------------------------- #
# 73-75. Multitenant                                                            #
# --------------------------------------------------------------------------- #


async def test_una_oferta_d_una_altra_empresa_no_es_llegeix(
    client_a, client_b, global_workspace
):
    await open_offer(client_a, global_workspace)

    response = await client_b.get(url(global_workspace))

    assert response.status_code == 404


async def test_una_oferta_d_una_altra_empresa_no_es_muta(
    client_a, client_b, global_workspace
):
    offer = await open_offer(client_a, global_workspace)

    response = await client_b.post(
        url(global_workspace, "lots", only_lot(offer)["id"], "sections"),
        json={"title": "Alie", "expected_revision": 0},
    )

    assert response.status_code == 404


async def test_un_identificador_alie_no_diu_si_existeix(
    client_a, client_b, db, tenant_b, global_workspace
):
    other_user, other_company = tenant_b
    altra = await _workspace(db, other_company, await _tender(db, title="De l'altra"))
    offer_b = await open_offer(client_b, altra)
    section_b = await client_b.post(
        url(altra, "lots", only_lot(offer_b)["id"], "sections"),
        json={"title": "Seva", "expected_revision": 0},
    )
    aliena = only_lot(section_b.json())["sections"][0]["id"]
    await open_offer(client_a, global_workspace)

    alie = await client_a.patch(
        url(global_workspace, "sections", aliena),
        json={"title": "Canviat", "expected_revision": await revision(client_a, global_workspace)},
    )
    fantasma = await client_a.patch(
        url(global_workspace, "sections", str(uuid.uuid4())),
        json={"title": "Canviat", "expected_revision": await revision(client_a, global_workspace)},
    )

    assert alie.status_code == fantasma.status_code == 404
    assert alie.json() == fantasma.json()


# --------------------------------------------------------------------------- #
# Auxiliars de domini                                                           #
# --------------------------------------------------------------------------- #


async def _requirement_row(db, tender_id, *, tender_lot_id=None) -> Requirement:
    run = await db.scalar(
        sa.select(RequirementExtractionRun).where(
            RequirementExtractionRun.tender_id == tender_id
        )
    )
    if run is None:
        run = RequirementExtractionRun(
            tender_id=tender_id,
            status=AnalysisRunStatus.COMPLETED,
            input_fingerprint=uuid.uuid4().hex * 2,
            extractor_version="tender-requirements-v1",
            prompt_version="requirements-v1",
            schema_version="requirements-schema-v1",
            provider="fake",
            model="fake",
            run_basis="real",  # fila sintetica vigent (0061: a PG nomes real/gold)
            is_current=True,
        )
        db.add(run)
        await db.flush()
    requirement = Requirement(
        run_id=run.id,
        tender_id=tender_id,
        tender_lot_id=tender_lot_id,
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.MANDATORY,
        scope=RequirementScope.LOT if tender_lot_id else RequirementScope.TENDER,
        title="Centraleta VoIP redundant",
        confidence=ExtractionConfidence.HIGH,
        review_status=ReviewStatus.UNREVIEWED,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


@pytest.fixture
async def requirement(db, global_workspace):
    return await _requirement_row(db, global_workspace.tender_id)


async def _solution(db, workspace, *, requirement_id=None) -> WorkspaceTechnicalSolution:
    solution = WorkspaceTechnicalSolution(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        requirement_id=requirement_id,
        title="Centraleta amb dos nodes",
        source=(
            TechnicalSolutionSource.REQUIREMENT
            if requirement_id
            else TechnicalSolutionSource.MANUAL
        ),
    )
    db.add(solution)
    await db.commit()
    await db.refresh(solution)
    return solution
