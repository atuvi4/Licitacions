"""Adoptar una opció com a línia d'oferta (S3-S8).

La frase que aquests tests defensen:

    l'opció és la configuració VIVA; la línia és el snapshot ADOPTAT

Quan es crea la línia es copien els camps homòlegs i s'hi queden. Que després
canviï el proveïdor, la quantitat o el cost de l'opció **no toca la línia**: la
xifra que va a l'oferta la va adoptar una persona un dia concret.

I una segona, que és la que fa que el producte aguanti un doble clic:

    un reintent no és un canvi

Si ja hi ha línia viva per a aquesta opció, es retorna aquella. No un 409, no
una segona línia, i no un increment de revisió.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.enums import MembershipRole
from app.models.offer import EconomicOfferLine
from app.models.solution_options import WorkspaceSolutionOption

pytestmark = pytest.mark.asyncio

CREATE_TENDER = "/api/v1/companies/{company}/tenders"
OPTIONS = "/api/v1/companies/{company}/workspaces/{workspace}/solution-options"
ONE = OPTIONS + "/{option}"
ADOPT = ONE + "/to-offer-line"
LINK = ONE + "/dimensions/{dimension}"
OFFER = "/api/v1/companies/{company}/workspaces/{workspace}/economic-offer"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"


@pytest.fixture
async def candidacy(db, tenant_a, client_a):
    """Candidatura amb oferta econòmica i una secció on posar-hi línies."""
    from app.models.enums import WorkspaceDecisionType
    from app.models.offer import EconomicOfferLot, EconomicOfferSection
    from app.models.tenders import Tender
    from app.services import bid_workspace as workspace_service
    from app.services import economic_offer as offer_service

    user, company = tenant_a
    created = await client_a.post(
        CREATE_TENDER.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": "Serveis de comunicacions"},
    )
    tender_id = created.json()["tender"]["id"]
    tender = await db.get(Tender, uuid.UUID(tender_id))

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
        reason="Ens hi presentem.",
    )
    offer, _ = await offer_service.create_offer(db, workspace, user=user)
    lot = (
        await db.scalars(
            sa.select(EconomicOfferLot).where(EconomicOfferLot.offer_id == offer.id)
        )
    ).first()
    section = EconomicOfferSection(
        company_id=company.id, offer_lot_id=lot.id, title="Serveis", position=0
    )
    db.add(section)
    await db.commit()

    return {
        "company_id": company.id,
        "workspace_id": workspace.id,
        "workspace": workspace,
        "tender_id": tender_id,
        "user": user,
        "offer": offer,
        "offer_id": offer.id,
        "section_id": section.id,
    }


async def _set_role(db, tenant, role: MembershipRole) -> None:
    from app.models.identity import CompanyMembership

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


async def _option(client, ctx, *, selected=True, **overrides) -> dict:
    """Una opció llesta per adoptar: triada, amb quantitat i base."""
    body = {
        "supplier_name": "Proveïdor A",
        "product_name": "Terminal X",
        "quantity": "181",
        "pricing_basis": "monthly",
    }
    body.update(overrides)
    response = await client.post(
        OPTIONS.format(company=ctx["company_id"], workspace=ctx["workspace_id"]), json=body
    )
    assert response.status_code == 201, response.text
    option = response.json()

    if selected:
        chosen = await client.post(
            ONE.format(
                company=ctx["company_id"],
                workspace=ctx["workspace_id"],
                option=option["id"],
            )
            + "/selection",
            json={"expected_revision": option["revision"], "selection": "selected"},
        )
        assert chosen.status_code == 200, chosen.text
        option = chosen.json()
    return option


async def _offer_revision(client, ctx) -> int:
    response = await client.get(
        OFFER.format(company=ctx["company_id"], workspace=ctx["workspace_id"])
    )
    assert response.status_code == 200, response.text
    return response.json()["revision"]


async def _adopt(client, ctx, option_id, *, revision=None, concept="Terminals IP", section=None):
    if revision is None:
        revision = await _offer_revision(client, ctx)
    return await client.post(
        ADOPT.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option_id
        ),
        json={
            "section_id": str(section or ctx["section_id"]),
            "expected_revision": revision,
            "concept": concept,
        },
    )


async def _cost(client, ctx, option_id, *, revision, value):
    return await client.patch(
        ONE.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option_id
        )
        + "/internal-economics",
        json={"expected_revision": revision, "unit_cost_ex_vat": value},
    )


async def _lines(db) -> list[EconomicOfferLine]:
    return list((await db.scalars(sa.select(EconomicOfferLine))).all())


# --------------------------------------------------------------------------- #
# A. El recorregut sencer                                                       #
# --------------------------------------------------------------------------- #


async def test_the_whole_walk_from_option_to_line(client_a, db, candidacy):
    """El criteri d'acceptació de la fase.

    Una opció triada es converteix en exactament una línia viva amb el seu
    snapshot; canviar l'opció després no la toca; un reintent retorna la
    mateixa; arxivar-la i tornar-hi crea una de nova amb el snapshot d'ara.
    """
    option = await _option(client_a, candidacy)
    await _cost(client_a, candidacy, option["id"], revision=option["revision"], value="12.345678")

    created = await _adopt(client_a, candidacy, option["id"], concept="Terminals IP")
    assert created.status_code == 200, created.text
    line = created.json()

    assert line["solution_option_id"] == option["id"]
    assert line["concept"] == "Terminals IP"
    assert line["supplier_name"] == "Proveïdor A"
    assert line["product_name"] == "Terminal X"
    assert Decimal(line["quantity"]) == Decimal("181")
    assert line["pricing_basis"] == "monthly"
    # El preu de venda NO es deriva del cost. Continua sense escriure.
    assert line["offered_unit_price_ex_vat"] is None

    assert len(await _lines(db)) == 1

    # Canviar l'opcio no toca la linia.
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    patched = await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        ),
        json={
            "expected_revision": stored.revision,
            "supplier_name": "Proveïdor B",
            "quantity": "40",
        },
    )
    assert patched.status_code == 200, patched.text
    await _cost(
        client_a, candidacy, option["id"], revision=patched.json()["revision"], value="10"
    )

    db.expire_all()
    lines = await _lines(db)
    assert len(lines) == 1
    assert lines[0].supplier_name == "Proveïdor A", "la linia es un snapshot"
    assert lines[0].quantity == Decimal("181.0000")


async def test_the_cost_snapshot_uses_the_line_scale(client_a, db, candidacy):
    """El cost viu a l'opció amb sis decimals i a la línia amb quatre.

    No és cap pèrdua accidental: `EconomicOfferLine.unit_cost_ex_vat` és
    `Numeric(18,4)` des de l'Sprint 2 i és la precisió amb què treballa
    l'oferta. Aquest test documenta què passa de debò —12.345678 → 12.3457—
    en comptes d'afirmar una igualtat que la columna no pot sostenir.
    """
    option = await _option(client_a, candidacy)
    await _cost(client_a, candidacy, option["id"], revision=option["revision"], value="12.345678")

    await _adopt(client_a, candidacy, option["id"])

    db.expire_all()
    line = (await _lines(db))[0]
    assert line.unit_cost_ex_vat == Decimal("12.3457")
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    assert stored.unit_cost_ex_vat == Decimal("12.345678"), "l'opcio conserva els sis"


async def test_an_unknown_cost_stays_unknown(client_a, db, candidacy):
    """`NULL` no és `0`: una línia sense cost no costa zero, no se'n sap el cost."""
    option = await _option(client_a, candidacy)

    await _adopt(client_a, candidacy, option["id"])

    db.expire_all()
    line = (await _lines(db))[0]
    assert line.unit_cost_ex_vat is None
    assert line.offered_unit_price_ex_vat is None


# --------------------------------------------------------------------------- #
# B. Idempotència                                                               #
# --------------------------------------------------------------------------- #


async def test_a_retry_returns_the_same_line(client_a, db, candidacy):
    option = await _option(client_a, candidacy)
    first = await _adopt(client_a, candidacy, option["id"])
    before = await _offer_revision(client_a, candidacy)

    second = await _adopt(client_a, candidacy, option["id"])

    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert len(await _lines(db)) == 1
    assert await _offer_revision(client_a, candidacy) == before, "un reintent no muta res"


async def test_a_retry_with_a_stale_revision_still_works(client_a, db, candidacy):
    """El testimoni ha quedat antic perquè la PRIMERA crida va funcionar.

    Castigar-lo amb un 409 seria castigar l'èxit: el client que reintenta
    després d'un timeout de xarxa porta forçosament la revisió d'abans.
    """
    option = await _option(client_a, candidacy)
    stale = await _offer_revision(client_a, candidacy)
    first = await _adopt(client_a, candidacy, option["id"], revision=stale)
    assert first.status_code == 200

    retry = await _adopt(client_a, candidacy, option["id"], revision=stale)

    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]
    assert len(await _lines(db)) == 1


async def test_a_retry_on_an_archived_option_still_returns_the_line(
    client_a, db, candidacy
):
    """§10: la idempotència preval, i arxivar l'opció no reviu res."""
    option = await _option(client_a, candidacy)
    first = await _adopt(client_a, candidacy, option["id"])

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    archived = await client_a.post(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/archive",
        json={"expected_revision": stored.revision},
    )
    assert archived.status_code == 200

    retry = await _adopt(client_a, candidacy, option["id"])
    assert retry.status_code == 200
    assert retry.json()["id"] == first.json()["id"]

    db.expire_all()
    still = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    assert still.archived_at is not None, "no s'ha reviscut l'opcio"


async def test_a_real_conflict_with_another_economic_write_is_a_409(
    client_a, db, candidacy
):
    """Idempotència no vol dir que el CAS deixi de protegir res."""
    option = await _option(client_a, candidacy)
    stale = await _offer_revision(client_a, candidacy)

    # Una altra mutacio economica mou la revisio.
    other = await _option(client_a, candidacy, supplier_name="Proveïdor B")
    assert (await _adopt(client_a, candidacy, other["id"], concept="Altra")).status_code == 200

    response = await _adopt(client_a, candidacy, option["id"], revision=stale)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "economic_offer_revision_conflict"


# --------------------------------------------------------------------------- #
# C. Validacions                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("selection", ["undecided", "rejected"])
async def test_only_a_selected_option_can_be_adopted(client_a, db, candidacy, selection):
    """D-03: crear una línia és afirmar «aquesta opció s'ha adoptat»."""
    option = await _option(client_a, candidacy, selected=False)
    if selection == "rejected":
        rejected = await client_a.post(
            ONE.format(
                company=candidacy["company_id"],
                workspace=candidacy["workspace_id"],
                option=option["id"],
            )
            + "/selection",
            json={
                "expected_revision": option["revision"],
                "selection": "rejected",
                "reason": "Prestacions inferiors",
            },
        )
        assert rejected.status_code == 200

    response = await _adopt(client_a, candidacy, option["id"])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "solution_option_not_selected"
    assert await _lines(db) == []

    # I no l'ha seleccionada pel seu compte.
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    assert stored.selection.value == selection


async def test_an_archived_option_cannot_start_a_new_line(client_a, db, candidacy):
    option = await _option(client_a, candidacy)
    await client_a.post(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/archive",
        json={"expected_revision": option["revision"]},
    )

    response = await _adopt(client_a, candidacy, option["id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "solution_option_archived"
    assert await _lines(db) == []


@pytest.mark.parametrize("missing", ["quantity", "pricing_basis"])
async def test_an_option_without_quantity_or_basis_cannot_become_a_line(
    client_a, db, candidacy, missing
):
    """No s'inventa cap default.

    `quantity = 0` faria que la línia sumés zero a l'oferta sense que ningú se
    n'adonés, i `pricing_basis = one_off` canviaria el total pels mesos del
    contracte. Falta informació, i el que toca és dir-ho.
    """
    overrides = {"quantity": "181", "pricing_basis": "monthly"}
    overrides[missing] = None
    option = await _option(client_a, candidacy, **overrides)

    response = await _adopt(client_a, candidacy, option["id"])

    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "solution_option_not_ready_for_offer_line"
    assert missing in body["details"]["missing"]
    assert await _lines(db) == []


async def test_the_concept_comes_from_the_request_and_is_never_invented(
    client_a, db, candidacy
):
    """L'opció no té cap homòleg de `concept`, i no se'n deriva cap.

    Ni del producte, ni del proveïdor, ni de la descripció: una línia que anirà
    a una oferta real no es pot dir «Opció» perquè faltava un camp.
    """
    option = await _option(client_a, candidacy)

    blank = await _adopt(client_a, candidacy, option["id"], concept="   ")
    assert blank.status_code == 422

    missing = await client_a.post(
        ADOPT.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        ),
        json={
            "section_id": str(candidacy["section_id"]),
            "expected_revision": await _offer_revision(client_a, candidacy),
        },
    )
    assert missing.status_code == 422
    assert await _lines(db) == []

    written = await _adopt(
        client_a, candidacy, option["id"], concept="  Terminals IP per a extensions  "
    )
    assert written.status_code == 200
    assert written.json()["concept"] == "Terminals IP per a extensions"


async def test_a_section_from_another_workspace_is_refused(client_a, db, candidacy, tenant_a):
    """No es confia en cap UUID."""
    from app.models.offer import EconomicOfferLot, EconomicOfferSection
    from app.models.tenders import Tender
    from app.services import bid_workspace as workspace_service
    from app.services import economic_offer as offer_service

    user, company = tenant_a
    other = await client_a.post(
        CREATE_TENDER.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": "Una altra"},
    )
    tender = await db.get(Tender, uuid.UUID(other.json()["tender"]["id"]))
    other_workspace = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=tender, user_id=user.id
        )
    ).workspace
    other_offer, _ = await offer_service.create_offer(db, other_workspace, user=user)
    other_lot = (
        await db.scalars(
            sa.select(EconomicOfferLot).where(EconomicOfferLot.offer_id == other_offer.id)
        )
    ).first()
    foreign = EconomicOfferSection(
        company_id=company.id, offer_lot_id=other_lot.id, title="D'una altra", position=0
    )
    db.add(foreign)
    await db.commit()
    foreign_id = foreign.id

    option = await _option(client_a, candidacy)
    response = await _adopt(client_a, candidacy, option["id"], section=foreign_id)

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# D. Permisos i inquilins                                                       #
# --------------------------------------------------------------------------- #


async def test_a_technical_cannot_put_a_figure_into_the_offer(
    client_a, db, candidacy, tenant_a
):
    """Configurar l'opció sí; convertir-la en preu, no."""
    option = await _option(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    response = await _adopt(client_a, candidacy, option["id"])
    assert response.status_code == 403
    assert await _lines(db) == []


async def test_a_viewer_cannot_either(client_a, db, candidacy, tenant_a):
    option = await _option(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.VIEWER)

    assert (await _adopt(client_a, candidacy, option["id"])).status_code == 403


async def test_another_company_cannot_adopt_your_option(
    client_a, client_b, db, candidacy
):
    option = await _option(client_a, candidacy)

    response = await client_b.post(
        ADOPT.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        ),
        json={
            "section_id": str(candidacy["section_id"]),
            "expected_revision": 0,
            "concept": "Meu",
        },
    )
    assert response.status_code in (403, 404)
    assert await _lines(db) == []


# --------------------------------------------------------------------------- #
# E. Provinença només de lectura                                                #
# --------------------------------------------------------------------------- #


async def test_the_generic_line_endpoints_cannot_set_the_provenance(
    client_a, db, candidacy
):
    """La provinença es guanya adoptant una opció, no enviant-ne l'UUID.

    `solution_option_id` no és a cap schema d'escriptura genèric, o sigui que
    un client que l'enviï no en fabrica cap: el camp queda com estava.
    """
    option = await _option(client_a, candidacy)
    lines_url = (
        f"/api/v1/companies/{candidacy['company_id']}/workspaces/"
        f"{candidacy['workspace_id']}/economic-offer/sections/{candidacy['section_id']}/lines"
    )

    created = await client_a.post(
        lines_url,
        json={
            "expected_revision": await _offer_revision(client_a, candidacy),
            "concept": "A ma",
            "quantity": "1",
            "pricing_basis": "one_off",
            "solution_option_id": option["id"],
        },
    )
    assert created.status_code in (200, 201), created.text

    # L'endpoint generic retorna l'oferta sencera: la linia es busca a dins.
    lines = [
        line
        for lot in created.json()["lots"]
        for section in lot["sections"]
        for line in section["lines"]
    ]
    assert len(lines) == 1
    assert lines[0]["solution_option_id"] is None

    stored = (await _lines(db))[0]
    assert stored.solution_option_id is None


# --------------------------------------------------------------------------- #
# F. Arxivar i tornar-hi                                                        #
# --------------------------------------------------------------------------- #


async def test_archiving_the_line_frees_the_option_for_a_new_one(
    client_a, db, candidacy
):
    """La vella conserva la provinença com a història; la nova porta el snapshot d'ara."""
    option = await _option(client_a, candidacy)
    first = await _adopt(client_a, candidacy, option["id"], concept="Primera")
    first_id = first.json()["id"]

    archived = await client_a.post(
        f"/api/v1/companies/{candidacy['company_id']}/workspaces/"
        f"{candidacy['workspace_id']}/economic-offer/lines/{first_id}/archive",
        json={"expected_revision": await _offer_revision(client_a, candidacy)},
    )
    assert archived.status_code in (200, 204), archived.text

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        ),
        json={"expected_revision": stored.revision, "supplier_name": "Proveïdor B"},
    )

    second = await _adopt(client_a, candidacy, option["id"], concept="Segona")
    assert second.status_code == 200
    assert second.json()["id"] != first_id
    assert second.json()["supplier_name"] == "Proveïdor B", "snapshot d'ara"

    db.expire_all()
    old = await db.get(EconomicOfferLine, uuid.UUID(first_id))
    assert old.archived_at is not None
    assert old.solution_option_id == uuid.UUID(option["id"]), "historia intacta"
    assert old.supplier_name == "Proveïdor A"


# --------------------------------------------------------------------------- #
# G. Readiness                                                                  #
# --------------------------------------------------------------------------- #


async def _findings(client, ctx, code: str) -> list[dict]:
    response = await client.get(
        READINESS.format(company=ctx["company_id"], workspace=ctx["workspace_id"])
    )
    assert response.status_code == 200, response.text
    return [f for f in response.json()["findings"] if f["code"] == code]


async def _dimension(db, ctx, *, key="extensions", label="Extensions", value=None):
    from app.services import dimensions as dimension_service

    dimension = await dimension_service.create_dimension(
        db, workspace=ctx["workspace"], user=ctx["user"], fields={"key": key, "label": label}
    )
    await db.flush()
    if value is not None:
        await dimension_service.decide_dimension(
            db,
            dimension=dimension,
            user=ctx["user"],
            expected_revision=dimension.revision,
            working_value_text=value,
            working_value_num=Decimal(value),
            reason="Acordat",
        )
    await db.commit()
    return dimension.id


async def test_an_undecided_dimension_only_blocks_once_it_is_in_the_offer(
    client_a, db, candidacy
):
    """Això és exactament què vol dir «in use».

    Una opció triada que depèn d'una magnitud per decidir és feina normal. El
    que no pot passar és que una xifra que ja ha entrat a l'oferta es recolzi en
    un número que ningú no ha fixat.
    """
    dimension_id = await _dimension(db, candidacy)
    option = await _option(client_a, candidacy)
    linked = await client_a.put(
        LINK.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
            dimension=dimension_id,
        ),
        json={"expected_revision": option["revision"]},
    )
    assert linked.status_code == 200, linked.text

    assert await _findings(client_a, candidacy, "dimension_undecided_in_use") == []

    adopted = await _adopt(client_a, candidacy, option["id"])
    assert adopted.status_code == 200, adopted.text

    findings = await _findings(client_a, candidacy, "dimension_undecided_in_use")
    assert len(findings) == 1
    assert findings[0]["severity"] == "blocker"
    assert findings[0]["context"]["key"] == "extensions"


async def test_deciding_the_dimension_clears_the_blocker(client_a, db, candidacy):
    from app.models.dimensions import WorkspaceDimension
    from app.models.identity import User
    from app.services import dimensions as dimension_service

    dimension_id = await _dimension(db, candidacy)
    user_id = candidacy["user"].id
    option = await _option(client_a, candidacy)
    await client_a.put(
        LINK.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
            dimension=dimension_id,
        ),
        json={"expected_revision": option["revision"]},
    )
    await _adopt(client_a, candidacy, option["id"])
    assert await _findings(client_a, candidacy, "dimension_undecided_in_use")

    db.expire_all()
    user = await db.get(User, user_id)
    dimension = await db.get(WorkspaceDimension, dimension_id)
    await dimension_service.decide_dimension(
        db,
        dimension=dimension,
        user=user,
        expected_revision=dimension.revision,
        working_value_text="181",
        working_value_num=Decimal("181"),
        reason="Inventari complet",
    )
    await db.commit()

    assert await _findings(client_a, candidacy, "dimension_undecided_in_use") == []


async def test_a_disputed_dimension_does_not_become_this_blocker(client_a, db, candidacy):
    """Té el seu avís propi; convertir-la aquí canviaria el que significa."""
    from app.models.dimensions import WorkspaceDimension
    from app.services import dimensions as dimension_service

    dimension_id = await _dimension(db, candidacy)
    dimension = await db.get(WorkspaceDimension, dimension_id)
    await dimension_service.add_observation(
        db,
        workspace=candidacy["workspace"],
        dimension=dimension,
        user=candidacy["user"],
        expected_revision=dimension.revision,
        source_kind="tender_document",
        value_text="151",
        value_num=Decimal("151"),
    )
    await dimension_service.add_observation(
        db,
        workspace=candidacy["workspace"],
        dimension=dimension,
        user=candidacy["user"],
        expected_revision=dimension.revision,
        source_kind="supplier",
        value_text="181",
        value_num=Decimal("181"),
    )
    await db.commit()

    option = await _option(client_a, candidacy)
    await client_a.put(
        LINK.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
            dimension=dimension_id,
        ),
        json={"expected_revision": option["revision"]},
    )
    await _adopt(client_a, candidacy, option["id"])

    assert await _findings(client_a, candidacy, "dimension_undecided_in_use") == []
    assert len(await _findings(client_a, candidacy, "dimension_disputed")) == 1


async def test_an_expired_quote_only_blocks_once_it_is_in_the_offer(
    client_a, db, candidacy
):
    option = await _option(client_a, candidacy, quote_status="expired")

    assert await _findings(client_a, candidacy, "solution_option_quote_expired") == []

    await _adopt(client_a, candidacy, option["id"])

    findings = await _findings(client_a, candidacy, "solution_option_quote_expired")
    assert len(findings) == 1
    assert findings[0]["severity"] == "blocker"
    assert findings[0]["context"]["supplier_name"] == "Proveïdor A"


async def test_a_received_quote_clears_the_blocker(client_a, db, candidacy):
    """I no s'ha tocat `selection`: caducada no vol dir rebutjada."""
    option = await _option(client_a, candidacy, quote_status="expired")
    await _adopt(client_a, candidacy, option["id"])
    assert await _findings(client_a, candidacy, "solution_option_quote_expired")

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    updated = await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        ),
        json={"expected_revision": stored.revision, "quote_status": "received"},
    )
    assert updated.status_code == 200

    assert await _findings(client_a, candidacy, "solution_option_quote_expired") == []
    db.expire_all()
    again = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    assert again.selection.value == "selected"


async def test_the_quote_pending_warning_is_unchanged(client_a, db, candidacy):
    """Contracte diferent: no necessita cap línia i continua sent un avís."""
    option = await _option(client_a, candidacy, quote_status="requested")

    findings = await _findings(client_a, candidacy, "solution_option_quote_pending")
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert option["id"]


async def test_the_new_findings_carry_no_internal_economics(client_a, db, candidacy):
    option = await _option(client_a, candidacy, quote_status="expired")
    await _cost(client_a, candidacy, option["id"], revision=option["revision"], value="12.345678")
    dimension_id = await _dimension(db, candidacy)
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.put(
        LINK.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
            dimension=dimension_id,
        ),
        json={"expected_revision": stored.revision},
    )
    await _adopt(client_a, candidacy, option["id"])

    response = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    relevant = [
        f
        for f in response.json()["findings"]
        if f["code"] in ("solution_option_quote_expired", "dimension_undecided_in_use")
    ]
    assert len(relevant) == 2
    for finding in relevant:
        rendered = str(finding)
        assert "12.345678" not in rendered
        assert "12.3457" not in rendered
        for forbidden in ("unit_cost", "internal_note", "margin", "markup", "price"):
            assert forbidden not in rendered


# --------------------------------------------------------------------------- #
# H. El que no ha de moure's                                                    #
# --------------------------------------------------------------------------- #


async def test_adopting_does_not_touch_the_technical_solution(client_a, db, candidacy):
    from app.models.enums import TechnicalSolutionSource, TechnicalSolutionStatus
    from app.models.workspace import WorkspaceTechnicalSolution

    solution = WorkspaceTechnicalSolution(
        company_id=candidacy["company_id"],
        workspace_id=candidacy["workspace_id"],
        title="La proposta",
        supplier_name="Qui ja hi era",
        source=TechnicalSolutionSource.MANUAL,
        status=TechnicalSolutionStatus.VALIDATED,
        proposed_solution="Text validat",
    )
    db.add(solution)
    await db.commit()
    solution_id = solution.id
    before = (solution.supplier_name, solution.status, solution.proposed_solution)

    option = await _option(client_a, candidacy, technical_solution_id=str(solution_id))
    await _adopt(client_a, candidacy, option["id"])

    db.expire_all()
    after = await db.get(WorkspaceTechnicalSolution, solution_id)
    assert (after.supplier_name, after.status, after.proposed_solution) == before


async def test_editing_the_option_afterwards_does_not_stale_the_package(
    client_a, db, candidacy
):
    """El contrast de §31.

    Crear la línia SÍ que és una mutació econòmica real i el paquet en depèn
    pel mecanisme d'Sprint 2. Però editar l'opció després **no**: la línia és un
    snapshot i l'opció no és contingut del manifest.
    """
    from app.models.workspace import BidWorkspace
    from app.services import submission_manifest as manifest_service

    async def digest() -> str:
        db.expire_all()
        workspace = await db.get(BidWorkspace, candidacy["workspace_id"])
        _, _, value = await manifest_service.current_manifest(
            db, workspace, revision_number=1
        )
        return value

    base = await digest()
    option = await _option(client_a, candidacy)
    assert await digest() == base, "crear una opcio no es contingut del manifest"

    await _adopt(client_a, candidacy, option["id"])
    with_line = await digest()
    assert with_line != base, (
        "crear una linia SI que es una mutacio economica: el paquet ho ha de "
        "veure pel mecanisme d'Sprint 2, i no s'ha preservat artificialment"
    )

    # I ara el contrast que defineix la fase: la linia es un snapshot, o sigui
    # que tocar l'opcio despres no mou res del paquet.
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    patched = await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        ),
        json={"expected_revision": stored.revision, "supplier_name": "Proveïdor B"},
    )
    assert patched.status_code == 200, patched.text
    await _cost(
        client_a, candidacy, option["id"], revision=patched.json()["revision"], value="10"
    )

    assert await digest() == with_line, "editar l'opcio no toca el paquet"


@pytest.mark.postgres
async def test_two_requests_at_once_converge_on_one_line(
    db, session_factory, candidacy, tenant_a, client_a
):
    """Dues peticions reals que veuen totes dues que encara no hi ha línia.

    L'índex únic parcial és l'última defensa: no n'hi ha prou amb comprovar-ho
    abans, perquè entre la comprovació i la inserció hi cap l'altra petició.
    Qui perd ha de **convergir** a la línia que ha creat l'altre —ni un 500, ni
    una segona línia, ni una revisió consumida per no res.
    """
    import asyncio

    from app.models.workspace import BidWorkspace
    from app.services import option_to_offer_line as adopt_service

    user, _company = tenant_a
    option = await _option(client_a, candidacy)
    option_id = uuid.UUID(option["id"])
    workspace_id = candidacy["workspace_id"]
    section_id = candidacy["section_id"]
    user_id = user.id
    revision = await _offer_revision(client_a, candidacy)

    async def request(concept: str) -> str:
        from app.models.identity import User
        from app.services import economic_offer as offer_service

        async with session_factory() as session:
            workspace = await session.get(BidWorkspace, workspace_id)
            actor = await session.get(User, user_id)
            offer = await offer_service.get_offer(session, workspace)
            try:
                adopted = await adopt_service.to_offer_line(
                    session,
                    workspace=workspace,
                    offer=offer,
                    option_id=option_id,
                    section_id=section_id,
                    expected_revision=revision,
                    concept=concept,
                    user=actor,
                )
            except offer_service.OfferRevisionConflictError:
                return "conflicte"
            await session.commit()
            return "creada" if adopted.created else "existent"

    resultats = await asyncio.gather(request("Primera"), request("Segona"))

    db.expire_all()
    lines = await _lines(db)
    assert len(lines) == 1, f"nomes una linia, i n'hi ha {len(lines)}"
    assert lines[0].solution_option_id == option_id
    # Una l'ha creada; l'altra hi ha convergit o ha vist el conflicte de CAS.
    # El que no pot passar de cap manera es que totes dues creïn.
    assert resultats.count("creada") == 1, resultats

    # I el testimoni de l'oferta nomes s'ha mogut un cop.
    assert await _offer_revision(client_a, candidacy) == revision + 1
