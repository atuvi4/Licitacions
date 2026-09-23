"""De quines magnituds depèn una opció (S3-S7b).

Una opció de servei mòbil depèn del nombre de línies, del de terminals i del de
SIM de dades. I el nombre de línies el fan servir totes les opcions que s'hi
comparen. Per això és N:N.

La frase que aquests tests defensen per damunt de tota la resta:

    declarar una dependència  ≠  copiar la xifra

Enllaçar una magnitud de 181 no escriu 181 enlloc, i canviar-la després a 209 no
toca l'opció. Si això s'automatitzés, una oferta canviaria de números sense que
ningú no ho hagués decidit —i ningú no sabria quan va passar.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.enums import MembershipRole
from app.models.solution_option_dimensions import WorkspaceSolutionOptionDimension
from app.models.solution_options import WorkspaceSolutionOption

pytestmark = pytest.mark.asyncio

CREATE_TENDER = "/api/v1/companies/{company}/tenders"
OPTIONS = "/api/v1/companies/{company}/workspaces/{workspace}/solution-options"
ONE = OPTIONS + "/{option}"
LINKS = ONE + "/dimensions"
LINK = LINKS + "/{dimension}"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"


@pytest.fixture
async def candidacy(db, tenant_a, client_a):
    from app.models.enums import WorkspaceDecisionType
    from app.models.tenders import Tender
    from app.services import bid_workspace as workspace_service

    user, company = tenant_a
    created = await client_a.post(
        CREATE_TENDER.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": "Serveis de comunicacions"},
    )
    assert created.status_code == 201, created.text
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
    await db.commit()
    return {
        "company_id": company.id,
        "workspace_id": workspace.id,
        "workspace": workspace,
        "tender_id": tender_id,
        "user": user,
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


async def _option(client, ctx, **overrides) -> dict:
    body = {"supplier_name": "Proveïdor A", "product_name": "Solució X"}
    body.update(overrides)
    response = await client.post(
        OPTIONS.format(company=ctx["company_id"], workspace=ctx["workspace_id"]), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _dimension(db, ctx, *, key: str, label: str, value: str | None = None) -> uuid.UUID:
    """Una magnitud real, decidida amb el servei si se li dona un valor."""
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
            reason="Valor de treball acordat",
        )
    await db.commit()
    return dimension.id


async def _attach(client, ctx, option_id, dimension_id, *, revision):
    return await client.put(
        LINK.format(
            company=ctx["company_id"],
            workspace=ctx["workspace_id"],
            option=option_id,
            dimension=dimension_id,
        ),
        json={"expected_revision": revision},
    )


async def _detach(client, ctx, option_id, dimension_id, *, revision):
    return await client.delete(
        LINK.format(
            company=ctx["company_id"],
            workspace=ctx["workspace_id"],
            option=option_id,
            dimension=dimension_id,
        )
        + f"?expected_revision={revision}"
    )


async def _linked(client, ctx, option_id) -> list[dict]:
    response = await client.get(
        LINKS.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option_id
        )
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# A. El recorregut sencer                                                       #
# --------------------------------------------------------------------------- #


async def test_an_option_can_depend_on_several_dimensions(client_a, db, candidacy):
    """El criteri d'acceptació de la fase.

    Dues magnituds decidides, una opció que depèn de totes dues, una tercera
    opció que reutilitza la primera, i després un canvi de valor que **no**
    toca res de l'opció.
    """
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions", value="181")
    seus = await _dimension(db, candidacy, key="seus", label="Seus", value="26")

    option = await _option(client_a, candidacy, quantity="181")
    first = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    assert first.status_code == 200, first.text
    second = await _attach(
        client_a, candidacy, option["id"], seus, revision=first.json()["revision"]
    )
    assert second.status_code == 200, second.text

    body = second.json()
    assert {item["key"] for item in body["dimensions"]} == {"extensions", "seus"}
    assert len(body["dimensions"]) == 2

    # La mateixa magnitud la pot fer servir una altra opcio.
    other = await _option(client_a, candidacy, supplier_name="Proveïdor B")
    shared = await _attach(client_a, candidacy, other["id"], extensions, revision=0)
    assert shared.status_code == 200
    assert [item["key"] for item in shared.json()["dimensions"]] == ["extensions"]

    total = (
        await db.execute(
            sa.select(sa.func.count()).select_from(WorkspaceSolutionOptionDimension)
        )
    ).scalar_one()
    assert total == 3, "dues per la primera opcio i una per la segona"


async def test_attaching_does_not_copy_the_value_into_the_quantity(
    client_a, db, candidacy
):
    """La magnitud diu 181 i l'opció en diu 40. Enllaçar-les deixa 40.

    Aquest test és el que distingeix «declarar una dependència» de «copiar la
    xifra». Amb quantitats iguals als dos costats, una còpia automàtica seria
    invisible: per això aquí són diferents a propòsit.
    """
    extensions = await _dimension(
        db, candidacy, key="extensions", label="Extensions", value="181"
    )
    option = await _option(client_a, candidacy, quantity="40")

    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    assert attached.status_code == 200, attached.text
    assert Decimal(attached.json()["quantity"]) == Decimal("40")
    assert Decimal(attached.json()["dimensions"][0]["working_value_num"]) == Decimal("181")

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    assert stored.quantity == Decimal("40.0000"), "enllacar no escriu cap xifra"


async def test_attaching_to_an_option_without_quantity_leaves_it_empty(
    client_a, db, candidacy
):
    """Sense quantitat i amb una magnitud decidida: continua sense quantitat.

    «Encara no ho hem dit» no s'ha de convertir sol en un número; si algú el vol,
    l'ha d'escriure.
    """
    extensions = await _dimension(
        db, candidacy, key="extensions", label="Extensions", value="181"
    )
    option = await _option(client_a, candidacy)
    assert option["quantity"] is None

    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    assert attached.json()["quantity"] is None
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    assert stored.quantity is None


async def test_detaching_does_not_clear_the_quantity_either(client_a, db, candidacy):
    """Ni en treure la dependència: la xifra és de l'opció, no de la magnitud."""
    extensions = await _dimension(
        db, candidacy, key="extensions", label="Extensions", value="181"
    )
    option = await _option(client_a, candidacy, quantity="40")
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    detached = await _detach(
        client_a, candidacy, option["id"], extensions, revision=attached.json()["revision"]
    )
    assert Decimal(detached.json()["quantity"]) == Decimal("40")


async def test_changing_the_dimension_does_not_touch_the_option(client_a, db, candidacy):
    """181 → 209 i la quantitat de l'opció continua sent 181.

    És l'invariant dur de la fase. La dependència diu de què depèn l'opció; qui
    decideix quina quantitat hi posa és una persona, i fins que no ho digui, la
    xifra de l'oferta no es mou sola.
    """
    from app.models.dimensions import WorkspaceDimension
    from app.models.identity import User
    from app.services import dimensions as dimension_service

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions", value="181")
    option = await _option(client_a, candidacy, quantity="181")
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    before_revision = attached.json()["revision"]
    # Els ids, ABANS d'expirar: `expire_all()` també caduca l'usuari de la
    # fixture, i llegir-ne un atribut caducat fora d'un greenlet peta.
    user_id = candidacy["user"].id

    # Algu decideix un valor nou.
    db.expire_all()
    user = await db.get(User, user_id)
    dimension = await db.get(WorkspaceDimension, extensions)
    await dimension_service.decide_dimension(
        db,
        dimension=dimension,
        user=user,
        expected_revision=dimension.revision,
        working_value_text="209",
        working_value_num=Decimal("209"),
        reason="Recompte revisat",
    )
    await db.commit()

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    assert stored.quantity == Decimal("181.0000"), "la quantitat de l'opcio no es mou sola"
    assert stored.revision == before_revision, "ni el testimoni de l'opcio"

    # I la dependencia continua, ara ensenyant el valor NOU.
    linked = await _linked(client_a, candidacy, option["id"])
    assert len(linked) == 1
    assert Decimal(linked[0]["working_value_num"]) == Decimal("209")


async def test_nothing_creates_an_economic_line(client_a, db, candidacy):
    from app.models.offer import EconomicOffer, EconomicOfferLine

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions", value="181")
    option = await _option(client_a, candidacy)
    await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    for model in (EconomicOfferLine, EconomicOffer):
        count = (
            await db.execute(sa.select(sa.func.count()).select_from(model))
        ).scalar_one()
        assert count == 0


# --------------------------------------------------------------------------- #
# B. Idempotència i CAS                                                         #
# --------------------------------------------------------------------------- #


async def test_attaching_twice_leaves_one_relation(client_a, db, candidacy):
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)

    first = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    assert first.json()["revision"] == 1

    second = await _attach(
        client_a, candidacy, option["id"], extensions, revision=first.json()["revision"]
    )
    assert second.status_code == 200
    # Un no-op no es cap canvi: no mou el testimoni.
    assert second.json()["revision"] == 1
    assert len(second.json()["dimensions"]) == 1

    total = (
        await db.execute(
            sa.select(sa.func.count()).select_from(WorkspaceSolutionOptionDimension)
        )
    ).scalar_one()
    assert total == 1


async def test_detaching_something_that_is_not_there_is_harmless(client_a, db, candidacy):
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)

    response = await _detach(client_a, candidacy, option["id"], extensions, revision=0)
    assert response.status_code == 200
    assert response.json()["revision"] == 0


async def test_detaching_leaves_both_sides_intact(client_a, db, candidacy):
    from app.models.dimensions import WorkspaceDimension

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions", value="181")
    option = await _option(client_a, candidacy, quantity="181")
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    detached = await _detach(
        client_a, candidacy, option["id"], extensions, revision=attached.json()["revision"]
    )
    assert detached.status_code == 200
    assert detached.json()["dimensions"] == []

    db.expire_all()
    dimension = await db.get(WorkspaceDimension, extensions)
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    assert dimension is not None and dimension.working_value_num == Decimal("181.0000")
    assert stored.quantity == Decimal("181.0000")
    assert stored.supplier_name == "Proveïdor A"


async def test_a_stale_revision_is_a_conflict(client_a, db, candidacy):
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    seus = await _dimension(db, candidacy, key="seus", label="Seus")
    option = await _option(client_a, candidacy)
    await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    late = await _attach(client_a, candidacy, option["id"], seus, revision=0)
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "solution_option_revision_conflict"


async def test_after_rereading_the_second_link_goes_in(client_a, db, candidacy):
    """Qui perd la cursa no perd la feina: rellegeix i torna a provar."""
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    seus = await _dimension(db, candidacy, key="seus", label="Seus")
    option = await _option(client_a, candidacy)

    first = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    assert (await _attach(client_a, candidacy, option["id"], seus, revision=0)).status_code == 409

    retried = await _attach(
        client_a, candidacy, option["id"], seus, revision=first.json()["revision"]
    )
    assert retried.status_code == 200
    assert {item["key"] for item in retried.json()["dimensions"]} == {"extensions", "seus"}


async def _wait_until_blocked(session, *, timeout: float = 5.0) -> None:
    import asyncio as _asyncio

    deadline = _asyncio.get_running_loop().time() + timeout
    while _asyncio.get_running_loop().time() < deadline:
        waiting = await session.scalar(
            sa.text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE wait_event_type = 'Lock' AND pid <> pg_backend_pid()"
            )
        )
        if waiting:
            return
        await _asyncio.sleep(0.02)
    raise AssertionError("la segona transaccio no ha arribat mai a esperar el bloqueig")


@pytest.mark.postgres
async def test_two_clients_attaching_at_once_and_only_one_wins(
    db, session_factory, candidacy, tenant_a
):
    """Dues transaccions reals: A enllaça D1 i B enllaça D2, totes dues des de 0.

    Sense CAS, totes dues escriurien i el testimoni només pujaria un cop: la
    segona pantalla creuria tenir la versió bona quan no la té. El guanyador no
    tanca fins que `pg_stat_activity` confirma que l'altra sessió espera el
    bloqueig de la fila.
    """
    import asyncio

    from app.services import solution_option_dimensions as link_service
    from app.services import solution_options as option_service

    user, _company = tenant_a
    d1 = await _dimension(db, candidacy, key="extensions", label="Extensions")
    d2 = await _dimension(db, candidacy, key="seus", label="Seus")
    option = await option_service.create_option(
        db,
        workspace=candidacy["workspace"],
        role=MembershipRole.OWNER,
        fields={"supplier_name": "Cursa"},
    )
    await db.commit()
    option_id, workspace_id = option.id, candidacy["workspace_id"]

    primera_ha_escrit = asyncio.Event()
    segona_ho_intenta = asyncio.Event()

    async def client(dimension_id, first: bool) -> str:
        from app.models.workspace import BidWorkspace

        if not first:
            await primera_ha_escrit.wait()
        async with session_factory() as session:
            try:
                async with session.begin():
                    workspace = await session.get(BidWorkspace, workspace_id)
                    candidata = await session.get(WorkspaceSolutionOption, option_id)
                    if not first:
                        segona_ho_intenta.set()
                    await link_service.attach_dimension(
                        session,
                        workspace=workspace,
                        option=candidata,
                        dimension_id=dimension_id,
                        user=user,
                        expected_revision=0,
                    )
                    if first:
                        primera_ha_escrit.set()
                        await segona_ho_intenta.wait()
                        await _wait_until_blocked(session)
            except option_service.OptionRevisionConflictError:
                return "conflicte"
        return "guanyat"

    resultats = await asyncio.gather(client(d1, True), client(d2, False))
    assert sorted(resultats) == ["conflicte", "guanyat"]

    db.expire_all()
    final = await db.get(WorkspaceSolutionOption, option_id)
    assert final.revision == 1, "nomes una escriptura ha de moure el testimoni"
    links = (
        await db.execute(
            sa.select(sa.func.count()).select_from(WorkspaceSolutionOptionDimension)
        )
    ).scalar_one()
    assert links == 1, "el perdedor no ha deixat cap relacio a mitges"


# --------------------------------------------------------------------------- #
# C. Coherència i inquilins                                                     #
# --------------------------------------------------------------------------- #


async def test_a_dimension_from_another_workspace_is_refused(
    client_a, db, candidacy, tenant_a
):
    """Mateixa empresa, una altra candidatura: no.

    La FK no ho impedeix —apunta a totes les magnituds—, i sense això una opció
    podria dependre d'un número d'un altre expedient.
    """
    from app.models.tenders import Tender
    from app.services import bid_workspace as workspace_service
    from app.services import dimensions as dimension_service

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
    foreign = await dimension_service.create_dimension(
        db, workspace=other_workspace, user=user, fields={"key": "altres", "label": "Altres"}
    )
    await db.commit()
    foreign_id = foreign.id

    option = await _option(client_a, candidacy)
    response = await _attach(client_a, candidacy, option["id"], foreign_id, revision=0)

    assert response.status_code == 422
    assert (
        response.json()["error"]["code"]
        == "solution_option_dimension_workspace_mismatch"
    )


async def test_an_invented_dimension_is_refused(client_a, candidacy):
    option = await _option(client_a, candidacy)

    response = await _attach(client_a, candidacy, option["id"], uuid.uuid4(), revision=0)
    assert response.status_code == 422


async def test_another_company_cannot_link_anything(client_a, client_b, db, candidacy):
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)

    listed = await client_b.get(
        LINKS.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
    )
    assert listed.status_code in (403, 404)

    attached = await client_b.put(
        LINK.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
            dimension=extensions,
        ),
        json={"expected_revision": 0},
    )
    assert attached.status_code in (403, 404)


async def test_the_option_must_belong_to_the_workspace_in_the_url(
    client_a, db, candidacy, tenant_a
):
    """Un UUID no dona autorització."""
    from app.models.tenders import Tender
    from app.services import bid_workspace as workspace_service

    user, company = tenant_a
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)

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
    await db.commit()

    leaked = await client_a.put(
        LINK.format(
            company=company.id,
            workspace=other_workspace.id,
            option=option["id"],
            dimension=extensions,
        ),
        json={"expected_revision": 0},
    )
    assert leaked.status_code == 404


async def test_the_pair_is_unique_in_the_database(db, candidacy, client_a):
    """El UNIQUE, no només el servei."""
    from sqlalchemy.exc import IntegrityError

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    db.add(
        WorkspaceSolutionOptionDimension(
            solution_option_id=uuid.UUID(option["id"]), dimension_id=extensions
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# --------------------------------------------------------------------------- #
# D. Arxivats                                                                   #
# --------------------------------------------------------------------------- #


async def test_an_archived_option_takes_no_new_dependencies(client_a, db, candidacy):
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    archived = await client_a.post(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/archive",
        json={"expected_revision": 0},
    )

    response = await _attach(
        client_a, candidacy, option["id"], extensions, revision=archived.json()["revision"]
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "solution_option_archived"


async def test_archiving_an_option_keeps_its_dependencies(client_a, db, candidacy):
    """Arxivar no és esborrar: de què depenia continua sent llegible."""
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    await client_a.post(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/archive",
        json={"expected_revision": attached.json()["revision"]},
    )

    left = (
        await db.execute(
            sa.select(sa.func.count()).select_from(WorkspaceSolutionOptionDimension)
        )
    ).scalar_one()
    assert left == 1
    assert await _linked(client_a, candidacy, option["id"])


async def test_an_archived_dimension_cannot_start_a_new_dependency(
    client_a, db, candidacy
):
    from app.models.dimensions import WorkspaceDimension
    from app.services import dimensions as dimension_service

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    dimension = await db.get(WorkspaceDimension, extensions)
    await dimension_service.archive_dimension(
        db, dimension=dimension, expected_revision=dimension.revision
    )
    await db.commit()

    option = await _option(client_a, candidacy)
    response = await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "solution_option_dimension_archived"


async def test_an_existing_dependency_survives_the_dimension_being_archived(
    client_a, db, candidacy
):
    """Es conserva el context històric, i el read-model ho diu.

    Esborrar-la automàticament faria desaparèixer, sense avisar, la raó per la
    qual algú havia configurat aquella opció.
    """
    from app.models.dimensions import WorkspaceDimension
    from app.services import dimensions as dimension_service

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions", value="181")
    option = await _option(client_a, candidacy)
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    db.expire_all()
    dimension = await db.get(WorkspaceDimension, extensions)
    await dimension_service.archive_dimension(
        db, dimension=dimension, expected_revision=dimension.revision
    )
    await db.commit()

    linked = await _linked(client_a, candidacy, option["id"])
    assert len(linked) == 1
    assert linked[0]["archived_at"] is not None

    # I es pot desenganxar, que és com se'n neteja el rastre.
    detached = await _detach(
        client_a, candidacy, option["id"], extensions, revision=attached.json()["revision"]
    )
    assert detached.status_code == 200
    assert detached.json()["dimensions"] == []


# --------------------------------------------------------------------------- #
# E. Estat de la magnitud                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("value", [None, "181"])
async def test_a_dimension_can_be_linked_whatever_its_status(client_a, db, candidacy, value):
    """Representar una dependència no és afirmar que el valor ja estigui resolt."""
    extensions = await _dimension(
        db, candidacy, key="extensions", label="Extensions", value=value
    )
    option = await _option(client_a, candidacy)

    response = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    assert response.status_code == 200
    assert response.json()["dimensions"][0]["status"] == (
        "decided" if value else "undecided"
    )


async def test_a_disputed_dimension_can_be_linked_and_warns_only_once(
    client_a, db, candidacy
):
    """El `dimension_disputed` que ja existia continua sent l'únic avís.

    Un segon finding perquè a més està enllaçada seria dir dues vegades el
    mateix problema.
    """
    from app.models.dimensions import WorkspaceDimension
    from app.services import dimensions as dimension_service

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    dimension = await db.get(WorkspaceDimension, extensions)
    first = await dimension_service.add_observation(
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
    assert first is not None

    option = await _option(client_a, candidacy)
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    assert attached.status_code == 200
    assert attached.json()["dimensions"][0]["status"] == "disputed"

    readiness = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    disputed = [f for f in readiness.json()["findings"] if f["code"] == "dimension_disputed"]
    assert len(disputed) == 1


async def test_an_undecided_dimension_in_use_is_not_a_blocker_yet(client_a, db, candidacy):
    """Encara no es pot calcular honestament: «en ús» exigeix una línia viva.

    Redefinir-ho com «existeix una opció» o «l'opció està triada» seria canviar
    el significat del blocador per poder-lo escriure abans d'hora.
    """
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    await client_a.post(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/selection",
        json={"expected_revision": attached.json()["revision"], "selection": "selected"},
    )

    readiness = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    codes = [f["code"] for f in readiness.json()["findings"]]
    assert "dimension_undecided_in_use" not in codes
    assert "solution_option_quote_expired" not in codes


# --------------------------------------------------------------------------- #
# F. Permisos i redacció                                                        #
# --------------------------------------------------------------------------- #


async def test_a_technical_can_manage_dependencies(client_a, db, candidacy, tenant_a):
    """Enllaçar magnituds és configuració tècnica, no una decisió econòmica."""
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    response = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    assert response.status_code == 200


async def test_a_viewer_can_read_but_not_link(client_a, db, candidacy, tenant_a):
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    await _set_role(db, tenant_a, MembershipRole.VIEWER)

    assert len(await _linked(client_a, candidacy, option["id"])) == 1
    blocked = await _attach(client_a, candidacy, option["id"], extensions, revision=1)
    assert blocked.status_code == 403


async def test_the_cost_stays_hidden_even_with_dependencies(
    client_a, db, candidacy, tenant_a
):
    """Regressió d'S3-S7: afegir dimensions no pot obrir cap escletxa."""
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions", value="181")
    option = await _option(client_a, candidacy)
    await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": 0, "unit_cost_ex_vat": "12.345678"},
    )
    await _attach(client_a, candidacy, option["id"], extensions, revision=1)

    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    listed = await client_a.get(
        OPTIONS.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"])
    )
    assert listed.json()["can_view_internal_economics"] is False
    assert listed.json()["items"][0]["internal_economics"] is None
    assert len(listed.json()["items"][0]["dimensions"]) == 1
    assert "12.345678" not in listed.text

    detail = await client_a.get(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
    )
    assert detail.json()["internal_economics"] is None
    assert "12.345678" not in detail.text

    # I tampoc per la porta nova.
    linked = await client_a.get(
        LINKS.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
    )
    assert "12.345678" not in linked.text


async def test_the_audit_trail_keeps_the_figures_out(client_a, db, candidacy):
    from app.models.audit import AuditLog

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions", value="181")
    option = await _option(client_a, candidacy)
    await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": 0, "unit_cost_ex_vat": "12.345678"},
    )
    await _attach(client_a, candidacy, option["id"], extensions, revision=1)

    entries = (await db.scalars(sa.select(AuditLog))).all()
    for entry in entries:
        assert "12.345678" not in (entry.summary or "")
        assert "181" not in (entry.summary or "")


async def test_a_no_op_leaves_no_audit_noise(client_a, db, candidacy):
    from app.models.audit import AuditLog

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    before = (await db.execute(sa.select(sa.func.count()).select_from(AuditLog))).scalar_one()
    await _attach(client_a, candidacy, option["id"], extensions, revision=1)
    after = (await db.execute(sa.select(sa.func.count()).select_from(AuditLog))).scalar_one()

    assert after == before


# --------------------------------------------------------------------------- #
# G. El que no ha de moure's                                                    #
# --------------------------------------------------------------------------- #


async def test_linking_does_not_touch_selection_or_the_quote(client_a, db, candidacy):
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy, quote_status="received", quote_reference="COT-1")

    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    body = attached.json()

    assert body["selection"] == "undecided"
    assert body["selection_reason"] is None
    assert body["selected_by"] is None
    assert body["selected_at"] is None
    assert body["quote_status"] == "received"
    assert body["quote_reference"] == "COT-1"


async def test_linking_does_not_touch_the_technical_solution(client_a, db, candidacy):
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

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy, technical_solution_id=str(solution_id))
    await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    db.expire_all()
    after = await db.get(WorkspaceTechnicalSolution, solution_id)
    assert (after.supplier_name, after.status, after.proposed_solution) == before


async def test_linking_does_not_move_the_scope_fingerprint(client_a, db, candidacy):
    from app.models.workspace import BidWorkspace
    from app.services import technical_scope

    before = await technical_scope.scope_fingerprint(db, candidacy["workspace"])

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    await _attach(client_a, candidacy, option["id"], extensions, revision=0)

    db.expire_all()
    workspace = await db.get(BidWorkspace, candidacy["workspace_id"])
    assert await technical_scope.scope_fingerprint(db, workspace) == before


async def test_linking_does_not_make_the_package_stale(client_a, db, candidacy):
    from app.models.tenders import Tender
    from app.services import tender_lifecycle

    tender = await db.get(Tender, uuid.UUID(candidacy["tender_id"]))
    before = await tender_lifecycle.build_snapshot(db, tender)

    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy)
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    await _detach(
        client_a, candidacy, option["id"], extensions, revision=attached.json()["revision"]
    )

    db.expire_all()
    tender = await db.get(Tender, uuid.UUID(candidacy["tender_id"]))
    assert await tender_lifecycle.build_snapshot(db, tender) == before


async def test_the_quote_pending_warning_still_works(client_a, db, candidacy):
    """Regressió d'S3-S7: el finding que ja hi havia continua igual."""
    extensions = await _dimension(db, candidacy, key="extensions", label="Extensions")
    option = await _option(client_a, candidacy, quote_status="requested")
    attached = await _attach(client_a, candidacy, option["id"], extensions, revision=0)
    await client_a.post(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/selection",
        json={"expected_revision": attached.json()["revision"], "selection": "selected"},
    )

    readiness = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    pending = [
        f for f in readiness.json()["findings"] if f["code"] == "solution_option_quote_pending"
    ]
    assert len(pending) == 1
    assert pending[0]["severity"] == "warning"


# --------------------------------------------------------------------------- #
# H. L'esquema                                                                  #
# --------------------------------------------------------------------------- #


async def test_the_relation_stores_only_the_pair(db):
    """Cap snapshot: l'estat d'ara el diu la dimensió, i prou.

    Amb una còpia aquí hi hauria dues respostes a la mateixa pregunta, i un dia
    no coincidirien sense que ningú no sabés quina manava.
    """
    columns = set(WorkspaceSolutionOptionDimension.__table__.columns.keys())

    assert columns == {"id", "solution_option_id", "dimension_id", "created_by", "created_at"}
    for forbidden in (
        "working_value_num",
        "working_value_text",
        "snapshot_value_num",
        "snapshot_taken_at",
        "dimension_revision",
        "quantity",
        "quantity_factor",
        "multiplier",
        "role",
        "status",
    ):
        assert forbidden not in columns, forbidden


async def test_both_sides_cascade(db):
    actions = {
        fk.column.table.name: fk.ondelete
        for fk in WorkspaceSolutionOptionDimension.__table__.foreign_keys
    }
    assert actions["workspace_solution_options"] == "CASCADE"
    assert actions["workspace_dimensions"] == "CASCADE"
    assert actions["users"] == "SET NULL"


async def test_the_economic_offer_line_is_still_untouched(db):
    from app.models.offer import EconomicOfferLine

    assert "solution_option_id" not in EconomicOfferLine.__table__.columns
    assert "target_markup_pct" not in EconomicOfferLine.__table__.columns
    assert "dimension_id" not in EconomicOfferLine.__table__.columns


async def test_the_join_table_is_tenant_owned(db):
    from app.tenant_ops import INDIRECT_TABLES

    assert "workspace_solution_option_dimensions" in INDIRECT_TABLES
