"""Opcions de solució: proveïdors, cotitzacions i tria humana (S3-S7).

El cas que dona sentit a tot: Orange o Sewan, Yealink o Jabra. Dues vies
possibles, totes dues reals, i una persona que en tria una i descarta l'altra
dient per què. Les dues files es queden: la descartada és el que explica la
tria.

Tres frases que aquests tests no deixen tornar enrere:

    una opció  ≠  una línia d'oferta
    «sense decidir»  ≠  «descartada»
    triar-ne una  ≠  descartar les altres

I una quarta que és una porta de seguretat i no una preferència: **el cost
intern no surt mai per a qui no el pot veure**, i no hi surt com a `null` ni com
a `0`, sinó que el bloc sencer no hi és.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.enums import MembershipRole, OptionSelection, QuoteStatus
from app.models.solution_options import WorkspaceSolutionOption

pytestmark = pytest.mark.asyncio

CREATE_TENDER = "/api/v1/companies/{company}/tenders"
OPTIONS = "/api/v1/companies/{company}/workspaces/{workspace}/solution-options"
ONE = OPTIONS + "/{option}"
INTERNAL = ONE + "/internal-economics"
SELECTION = ONE + "/selection"
ARCHIVE = ONE + "/archive"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"


@pytest.fixture
async def candidacy(db, tenant_a, client_a):
    """Una candidatura oberta sobre una licitació manual pròpia."""
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


async def _create(client, ctx, **overrides) -> dict:
    body = {"supplier_name": "Proveïdor A", "product_name": "Terminal X"}
    body.update(overrides)
    response = await client.post(
        OPTIONS.format(company=ctx["company_id"], workspace=ctx["workspace_id"]), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _patch(client, ctx, option_id, *, revision, **fields):
    return await client.patch(
        ONE.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option_id
        ),
        json={"expected_revision": revision, **fields},
    )


async def _cost(client, ctx, option_id, *, revision, **fields):
    return await client.patch(
        INTERNAL.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option_id
        ),
        json={"expected_revision": revision, **fields},
    )


async def _select(client, ctx, option_id, *, revision, selection, reason=None):
    body = {"expected_revision": revision, "selection": selection}
    if reason is not None:
        body["reason"] = reason
    return await client.post(
        SELECTION.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option_id
        ),
        json=body,
    )


async def _list(client, ctx, **params) -> dict:
    query = "".join(f"&{k}={v}" for k, v in params.items())
    response = await client.get(
        OPTIONS.format(company=ctx["company_id"], workspace=ctx["workspace_id"])
        + ("?" + query[1:] if query else "")
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# A. Crear no és triar                                                          #
# --------------------------------------------------------------------------- #


async def test_a_new_option_is_undecided_and_undrafted(client_a, candidacy):
    """Neix sense decidir i sense cotitzar, encara que qui la crea ja ho sàpiga."""
    body = await _create(client_a, candidacy)

    assert body["selection"] == "undecided"
    assert body["quote_status"] == "draft"
    assert body["selection_reason"] is None
    assert body["selected_by"] is None
    assert body["selected_at"] is None
    assert body["archived_at"] is None
    assert body["revision"] == 0


async def test_creating_does_not_touch_the_economic_offer(client_a, db, candidacy):
    """Una opció no és una línia d'oferta, i crear-la no en fabrica cap."""
    from app.models.offer import EconomicOffer, EconomicOfferLine

    await _create(client_a, candidacy)

    lines = (
        await db.execute(sa.select(sa.func.count()).select_from(EconomicOfferLine))
    ).scalar_one()
    offers = (
        await db.execute(sa.select(sa.func.count()).select_from(EconomicOffer))
    ).scalar_one()
    assert (lines, offers) == (0, 0)


async def test_there_is_no_sale_price_anywhere(db):
    """`unit_price`, marge i markup no hi són, i no és cap oblit.

    El preu de venda viu a `EconomicOfferLine`. Tenir-lo a totes dues taules
    voldria dir que un dia dirien coses diferents i ningú no sabria quina mana.
    """
    columns = set(WorkspaceSolutionOption.__table__.columns.keys())

    assert "unit_cost_ex_vat" in columns
    for forbidden in ("unit_price", "offered_unit_price", "sale_price", "margin", "markup"):
        assert forbidden not in columns
    # I cap sinònim del cost: un sol nom canònic.
    assert {name for name in columns if "cost" in name} == {"unit_cost_ex_vat"}


# --------------------------------------------------------------------------- #
# B. Triar i descartar                                                          #
# --------------------------------------------------------------------------- #


async def test_the_whole_walk_two_siblings_one_chosen(client_a, db, candidacy):
    """El criteri d'acceptació de la fase, de punta a punta.

    Dues opcions germanes, una persona que en tria una i descarta l'altra amb
    motiu, i **totes dues files continuen**. Sense crear cap línia econòmica,
    sense invalidar cap solució tècnica i sense embrutar cap paquet.
    """
    a = await _create(client_a, candidacy, supplier_name="Proveïdor A", product_name="Terminal X")
    b = await _create(client_a, candidacy, supplier_name="Proveïdor B", product_name="Terminal Y")
    assert (a["selection"], b["selection"]) == ("undecided", "undecided")

    chosen = await _select(
        client_a, candidacy, a["id"], revision=a["revision"], selection="selected"
    )
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["selection"] == "selected"
    assert chosen.json()["selected_by"] == str(candidacy["user"].id)
    assert chosen.json()["selected_at"] is not None

    dropped = await _select(
        client_a,
        candidacy,
        b["id"],
        revision=b["revision"],
        selection="rejected",
        reason="Prestacions inferiors a la configuració escollida.",
    )
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()["selection"] == "rejected"
    assert (
        dropped.json()["selection_reason"]
        == "Prestacions inferiors a la configuració escollida."
    )

    # Les dues files continuen existint.
    rows = await _list(client_a, candidacy)
    assert {item["id"] for item in rows["items"]} == {a["id"], b["id"]}

    from app.models.offer import EconomicOfferLine

    left = (
        await db.execute(sa.select(sa.func.count()).select_from(EconomicOfferLine))
    ).scalar_one()
    assert left == 0, "triar una opcio no crea cap linia economica"


async def test_choosing_one_does_not_reject_its_siblings(client_a, candidacy):
    """Sewan, Cable i Orange poden coexistir.

    Auto-rebutjar les germanes convertiria una decisió que ningú no ha pres en
    una que sembla presa, amb un motiu que ningú no ha escrit.
    """
    a = await _create(client_a, candidacy, supplier_name="Sewan")
    b = await _create(client_a, candidacy, supplier_name="Cable")
    c = await _create(client_a, candidacy, supplier_name="Orange")

    await _select(client_a, candidacy, a["id"], revision=0, selection="selected")

    rows = {item["supplier_name"]: item for item in (await _list(client_a, candidacy))["items"]}
    assert rows["Sewan"]["selection"] == "selected"
    assert rows["Cable"]["selection"] == "undecided"
    assert rows["Orange"]["selection"] == "undecided"
    assert rows["Cable"]["selection_reason"] is None
    assert b["id"] and c["id"]


async def test_rejecting_requires_saying_why(client_a, candidacy):
    created = await _create(client_a, candidacy)

    response = await _select(
        client_a, candidacy, created["id"], revision=0, selection="rejected"
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "solution_option_reason_required"


async def test_whitespace_is_not_a_reason(client_a, candidacy):
    created = await _create(client_a, candidacy)

    response = await _select(
        client_a, candidacy, created["id"], revision=0, selection="rejected", reason="   "
    )
    assert response.status_code == 422


async def test_undecided_is_not_rejected(client_a, db, candidacy):
    """La distinció que justifica l'enum en comptes d'un booleà."""
    created = await _create(client_a, candidacy)
    assert created["selection"] == "undecided"

    option = await db.get(WorkspaceSolutionOption, uuid.UUID(created["id"]))
    assert option.selection is OptionSelection.UNDECIDED
    assert option.selection is not OptionSelection.REJECTED
    assert option.selection_reason is None


async def test_unselecting_does_not_pretend_it_is_still_chosen(client_a, candidacy):
    """Deixar de ser la triada ha d'esborrar qui i quan la va triar."""
    created = await _create(client_a, candidacy)
    chosen = await _select(
        client_a, candidacy, created["id"], revision=0, selection="selected"
    )
    assert chosen.json()["selected_by"] is not None

    back = await _select(
        client_a,
        candidacy,
        created["id"],
        revision=chosen.json()["revision"],
        selection="undecided",
    )
    assert back.json()["selection"] == "undecided"
    assert back.json()["selected_by"] is None
    assert back.json()["selected_at"] is None


async def test_the_database_refuses_a_rejection_without_a_reason(db, candidacy):
    """El CHECK, no només el servei."""
    from sqlalchemy.exc import IntegrityError

    db.add(
        WorkspaceSolutionOption(
            company_id=candidacy["company_id"],
            workspace_id=candidacy["workspace_id"],
            supplier_name="Sense motiu",
            quote_status=QuoteStatus.DRAFT,
            selection=OptionSelection.REJECTED,
            revision=0,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# --------------------------------------------------------------------------- #
# C. El cost no surt per a qui no el pot veure                                   #
# --------------------------------------------------------------------------- #


async def test_a_technical_never_sees_the_cost_anywhere(client_a, db, candidacy, tenant_a):
    """La porta de seguretat de la fase, a totes les respostes.

    No és `unit_cost_ex_vat = null` ni `0`: és que el bloc `internal_economics`
    no hi és. Un `null` voldria dir «encara no ho sabem», i un `0` seria una
    xifra falsa que algú podria arribar a fer servir.
    """
    created = await _create(client_a, candidacy)
    priced = await _cost(
        client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="12.345678"
    )
    assert priced.status_code == 200, priced.text
    assert priced.json()["internal_economics"]["unit_cost_ex_vat"] == "12.345678"

    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    listed = await _list(client_a, candidacy)
    assert listed["can_view_internal_economics"] is False
    assert listed["items"][0]["internal_economics"] is None
    assert "unit_cost_ex_vat" not in listed["items"][0]

    detail = await client_a.get(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=created["id"],
        )
    )
    assert detail.json()["internal_economics"] is None
    assert "12.345678" not in detail.text

    # Ni a les respostes d'escriptura.
    patched = await _patch(
        client_a, candidacy, created["id"], revision=1, product_name="Nou nom"
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["internal_economics"] is None
    assert "12.345678" not in patched.text

    chosen = await _select(
        client_a, candidacy, created["id"], revision=2, selection="selected"
    )
    assert chosen.json()["internal_economics"] is None
    assert "12.345678" not in chosen.text

    archived = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=created["id"],
        ),
        json={"expected_revision": chosen.json()["revision"]},
    )
    assert archived.json()["internal_economics"] is None
    assert "12.345678" not in archived.text


async def test_a_viewer_never_sees_the_cost(client_a, db, candidacy, tenant_a):
    created = await _create(client_a, candidacy)
    await _cost(client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="12.345678")
    await _set_role(db, tenant_a, MembershipRole.VIEWER)

    listed = await _list(client_a, candidacy)
    assert listed["can_view_internal_economics"] is False
    assert "12.345678" not in str(listed)


async def test_a_technical_edit_preserves_the_cost_it_cannot_see(
    client_a, db, candidacy, tenant_a
):
    """El camp amagat no es pot netejar sense voler.

    És la raó per la qual hi ha dos PATCH i no un de sol amb el cost opcional:
    un desat de la fitxa pública no ha de poder tocar una xifra que qui desa
    ni tan sols veu.
    """
    created = await _create(client_a, candidacy)
    await _cost(client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="12.345678")
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    patched = await _patch(
        client_a, candidacy, created["id"], revision=1, product_name="Nou nom"
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["product_name"] == "Nou nom"

    db.expire_all()
    option = await db.get(WorkspaceSolutionOption, uuid.UUID(created["id"]))
    assert option.unit_cost_ex_vat == Decimal("12.345678")


async def test_a_technical_cannot_smuggle_the_cost_through_the_public_patch(
    client_a, db, candidacy, tenant_a
):
    """Ni s'ignora en silenci: qui l'envia ha de saber que no s'ha desat."""
    created = await _create(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    response = await _patch(
        client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="1.0"
    )
    assert response.status_code == 422


async def test_a_technical_cannot_use_the_internal_endpoint(
    client_a, db, candidacy, tenant_a
):
    created = await _create(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    response = await _cost(
        client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="1.0"
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "solution_option_internal_economics_forbidden"


async def test_a_technical_cannot_create_with_a_cost(client_a, db, candidacy, tenant_a):
    """Enviar-lo és un error, mai un camp descartat sense dir res."""
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    response = await client_a.post(
        OPTIONS.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={
            "supplier_name": "Proveïdor A",
            "internal_economics": {"unit_cost_ex_vat": "10.0"},
        },
    )
    assert response.status_code == 403


async def test_an_economic_role_can_write_and_read_the_cost(client_a, candidacy):
    created = await _create(client_a, candidacy)

    priced = await _cost(
        client_a,
        candidacy,
        created["id"],
        revision=0,
        unit_cost_ex_vat="12.345678",
        internal_note="Preu tancat amb el comercial",
    )
    assert priced.status_code == 200
    block = priced.json()["internal_economics"]
    assert Decimal(block["unit_cost_ex_vat"]) == Decimal("12.345678")
    assert block["internal_note"] == "Preu tancat amb el comercial"


async def test_the_audit_trail_does_not_carry_the_figure(client_a, db, candidacy):
    """Pot dir que el cost ha canviat; mai quant era.

    Escriure la xifra a l'auditoria l'exposaria a qui no la pot veure a la
    fitxa, que és exactament la superfície que aquesta fase tanca.
    """
    from app.models.audit import AuditLog

    created = await _create(client_a, candidacy)
    await _cost(client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="12.345678")

    entries = (await db.scalars(sa.select(AuditLog))).all()
    assert entries
    for entry in entries:
        assert "12.345678" not in (entry.summary or "")
        assert "12.345678" not in str(getattr(entry, "metadata_json", "") or "")


# --------------------------------------------------------------------------- #
# D. Cotitzacions                                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "status", ["draft", "requested", "pending", "received", "expired", "declined"]
)
async def test_every_quote_status_is_writable(client_a, candidacy, status):
    created = await _create(client_a, candidacy)

    response = await _patch(
        client_a, candidacy, created["id"], revision=0, quote_status=status
    )
    assert response.status_code == 200, response.text
    assert response.json()["quote_status"] == status


async def test_the_quote_metadata_is_kept_as_written(client_a, candidacy):
    """No es descarrega ni es parseja res: es desa el que diu la persona."""
    created = await _create(
        client_a,
        candidacy,
        quote_status="received",
        quote_date="2026-09-15",
        quote_reference="COT-2026-4412",
        quote_source="https://exemple.org/cotitzacions/4412",
    )

    assert created["quote_date"] == "2026-09-15"
    assert created["quote_reference"] == "COT-2026-4412"
    assert created["quote_source"] == "https://exemple.org/cotitzacions/4412"


async def test_an_impossible_quote_status_is_refused(client_a, candidacy):
    created = await _create(client_a, candidacy)

    response = await _patch(
        client_a, candidacy, created["id"], revision=0, quote_status="aprovada"
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# E. Què n'arriba a la preparació                                               #
# --------------------------------------------------------------------------- #


async def _findings(client, ctx, code: str) -> list[dict]:
    response = await client.get(
        READINESS.format(company=ctx["company_id"], workspace=ctx["workspace_id"])
    )
    assert response.status_code == 200, response.text
    return [f for f in response.json()["findings"] if f["code"] == code]


async def test_a_chosen_option_still_waiting_for_its_quote_warns(client_a, candidacy):
    created = await _create(client_a, candidacy, quote_status="requested")
    await _select(client_a, candidacy, created["id"], revision=0, selection="selected")

    findings = await _findings(client_a, candidacy, "solution_option_quote_pending")
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert findings[0]["context"]["quote_status"] == "requested"


async def test_the_warning_clears_when_the_quote_arrives(client_a, candidacy):
    created = await _create(client_a, candidacy, quote_status="requested")
    chosen = await _select(
        client_a, candidacy, created["id"], revision=0, selection="selected"
    )
    await _patch(
        client_a,
        candidacy,
        created["id"],
        revision=chosen.json()["revision"],
        quote_status="received",
    )

    assert await _findings(client_a, candidacy, "solution_option_quote_pending") == []


async def test_an_option_nobody_chose_does_not_warn(client_a, candidacy):
    """Que una alternativa que ningú no farà servir no tingui cotització no és
    cap feina pendent."""
    await _create(client_a, candidacy, quote_status="requested")

    assert await _findings(client_a, candidacy, "solution_option_quote_pending") == []


async def test_an_expired_quote_is_not_a_blocker_yet(client_a, candidacy):
    """Encara no es pot calcular honestament, així que no s'inventa.

    El contracte congelat exigeix que una línia econòmica VIVA depengui de
    l'opció, i aquest vincle encara no existeix. Deduir-lo pel nom o per la
    quantitat seria una heurística.
    """
    created = await _create(client_a, candidacy, quote_status="expired")
    await _select(client_a, candidacy, created["id"], revision=0, selection="selected")

    response = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    codes = [f["code"] for f in response.json()["findings"]]
    assert "solution_option_quote_expired" not in codes
    assert "dimension_undecided_in_use" not in codes


# --------------------------------------------------------------------------- #
# F. Arxivar                                                                    #
# --------------------------------------------------------------------------- #


async def test_archiving_keeps_everything_and_chooses_nothing(client_a, db, candidacy):
    a = await _create(client_a, candidacy, supplier_name="Proveïdor A", quote_status="requested")
    b = await _create(client_a, candidacy, supplier_name="Proveïdor B")
    chosen = await _select(
        client_a, candidacy, a["id"], revision=0, selection="selected"
    )

    archived = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=a["id"],
        ),
        json={"expected_revision": chosen.json()["revision"]},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    # Fora de la llista activa, però la fila hi és sencera.
    active = await _list(client_a, candidacy)
    assert {item["id"] for item in active["items"]} == {b["id"]}

    with_archived = await _list(client_a, candidacy, include_archived="true")
    assert len(with_archived["items"]) == 2

    db.expire_all()
    option = await db.get(WorkspaceSolutionOption, uuid.UUID(a["id"]))
    assert option.supplier_name == "Proveïdor A"
    assert option.selection is OptionSelection.SELECTED
    assert option.quote_status is QuoteStatus.REQUESTED

    # I no ha seleccionat cap germana.
    sibling = await db.get(WorkspaceSolutionOption, uuid.UUID(b["id"]))
    assert sibling.selection is OptionSelection.UNDECIDED


async def test_an_archived_option_stops_warning(client_a, candidacy):
    created = await _create(client_a, candidacy, quote_status="requested")
    chosen = await _select(
        client_a, candidacy, created["id"], revision=0, selection="selected"
    )
    assert await _findings(client_a, candidacy, "solution_option_quote_pending")

    await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=created["id"],
        ),
        json={"expected_revision": chosen.json()["revision"]},
    )
    assert await _findings(client_a, candidacy, "solution_option_quote_pending") == []


async def test_an_archived_option_takes_no_more_writes(client_a, candidacy):
    created = await _create(client_a, candidacy)
    archived = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=created["id"],
        ),
        json={"expected_revision": 0},
    )
    revision = archived.json()["revision"]

    assert (
        await _patch(client_a, candidacy, created["id"], revision=revision, unit="u")
    ).status_code == 409
    assert (
        await _select(
            client_a, candidacy, created["id"], revision=revision, selection="selected"
        )
    ).status_code == 409
    assert (
        await _cost(
            client_a, candidacy, created["id"], revision=revision, unit_cost_ex_vat="1"
        )
    ).status_code == 409


async def test_archiving_twice_is_harmless(client_a, candidacy):
    created = await _create(client_a, candidacy)
    first = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=created["id"],
        ),
        json={"expected_revision": 0},
    )
    second = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=created["id"],
        ),
        json={"expected_revision": first.json()["revision"]},
    )
    assert second.status_code == 200
    assert second.json()["revision"] == first.json()["revision"]


async def test_an_option_is_never_deleted(client_a):
    """No és cap oblit: una alternativa descartada explica una decisió.

    Des d'S3-S7b hi ha un `DELETE`, però és el d'una DEPENDÈNCIA amb una
    magnitud: treu una relació, no l'opció. Cap ruta pot esborrar una opció.
    """
    from app.main import app

    deletable = [
        path
        for path, methods in app.openapi()["paths"].items()
        if "solution-options" in path and "delete" in methods
    ]
    assert deletable == [
        "/api/v1/companies/{company_id}/workspaces/{workspace_id}"
        "/solution-options/{option_id}/dimensions/{dimension_id}"
    ], deletable


# --------------------------------------------------------------------------- #
# G. El testimoni de CAS                                                        #
# --------------------------------------------------------------------------- #


async def test_a_stale_revision_is_a_conflict(client_a, candidacy):
    created = await _create(client_a, candidacy)
    await _patch(client_a, candidacy, created["id"], revision=0, unit="línia")

    late = await _patch(client_a, candidacy, created["id"], revision=0, unit="seu")
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "solution_option_revision_conflict"


async def test_every_write_moves_the_token(client_a, candidacy):
    created = await _create(client_a, candidacy)
    assert created["revision"] == 0

    public = await _patch(client_a, candidacy, created["id"], revision=0, unit="línia")
    assert public.json()["revision"] == 1

    internal = await _cost(
        client_a, candidacy, created["id"], revision=1, unit_cost_ex_vat="3.5"
    )
    assert internal.json()["revision"] == 2

    chosen = await _select(
        client_a, candidacy, created["id"], revision=2, selection="selected"
    )
    assert chosen.json()["revision"] == 3

    archived = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=created["id"],
        ),
        json={"expected_revision": 3},
    )
    assert archived.json()["revision"] == 4


async def test_a_write_that_changes_nothing_does_not_move_the_token(client_a, candidacy):
    created = await _create(client_a, candidacy)

    response = await _patch(
        client_a, candidacy, created["id"], revision=0, supplier_name=created["supplier_name"]
    )
    assert response.status_code == 200
    assert response.json()["revision"] == 0


async def test_a_stale_internal_write_does_not_overwrite(client_a, candidacy):
    created = await _create(client_a, candidacy)
    await _cost(client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="10")

    late = await _cost(client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="1")
    assert late.status_code == 409


async def _wait_until_blocked(session, *, timeout: float = 5.0) -> None:
    """Espera que alguna altra sessió quedi encallada en un bloqueig de fila."""
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
async def test_two_editors_at_once_and_only_one_wins(
    db, session_factory, candidacy, tenant_a
):
    """La cursa de debò, amb dues transaccions reals contra PostgreSQL.

    Amb un `SELECT`, una comparació a Python i després un `UPDATE`, totes dues
    llegirien la revisió 0 i totes dues escriurien: la segona es menjaria la
    feina de la primera sense que ningú se n'assabentés.

    El guanyador no tanca fins que `pg_stat_activity` confirma que l'altra
    sessió ESPERA el bloqueig: si no, la lectura de la segona podria caure
    després del commit i el test deixaria de detectar un CAS ingenu.
    """
    import asyncio

    from app.services import solution_options as service

    user, _company = tenant_a
    option = await service.create_option(
        db,
        workspace=candidacy["workspace"],
        role=MembershipRole.OWNER,
        fields={"supplier_name": "Cursa"},
    )
    await db.commit()
    option_id = option.id

    primera_ha_escrit = asyncio.Event()
    segona_ho_intenta = asyncio.Event()

    async def guanyadora() -> str:
        async with session_factory() as session, session.begin():
            candidata = await session.get(WorkspaceSolutionOption, option_id)
            await service.set_selection(
                session,
                option=candidata,
                user=user,
                expected_revision=0,
                selection=OptionSelection.SELECTED,
                reason=None,
            )
            primera_ha_escrit.set()
            await segona_ho_intenta.wait()
            await _wait_until_blocked(session)
        return "guanyat"

    async def perdedora() -> str:
        await primera_ha_escrit.wait()
        async with session_factory() as session:
            try:
                async with session.begin():
                    candidata = await session.get(WorkspaceSolutionOption, option_id)
                    segona_ho_intenta.set()
                    await service.set_selection(
                        session,
                        option=candidata,
                        user=user,
                        expected_revision=0,
                        selection=OptionSelection.REJECTED,
                        reason="La segona",
                    )
            except service.OptionRevisionConflictError:
                return "conflicte"
        return "guanyat"

    resultats = await asyncio.gather(guanyadora(), perdedora())
    assert sorted(resultats) == ["conflicte", "guanyat"]

    db.expire_all()
    final = await db.get(WorkspaceSolutionOption, option_id)
    assert final.revision == 1, "nomes una escriptura ha de moure el testimoni"
    assert final.selection is OptionSelection.SELECTED
    assert final.selection_reason is None


# --------------------------------------------------------------------------- #
# H. Coherència de les referències                                              #
# --------------------------------------------------------------------------- #


async def test_a_technical_solution_from_another_workspace_is_refused(
    client_a, db, candidacy, tenant_a
):
    """Les FK soles no ho demostren: totes dues taules existeixen."""
    from app.models.enums import TechnicalSolutionSource, TechnicalSolutionStatus
    from app.models.tenders import Tender
    from app.models.workspace import WorkspaceTechnicalSolution
    from app.services import bid_workspace as workspace_service

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
    foreign = WorkspaceTechnicalSolution(
        company_id=company.id,
        workspace_id=other_workspace.id,
        title="D'una altra",
        source=TechnicalSolutionSource.MANUAL,
        status=TechnicalSolutionStatus.NOT_STARTED,
    )
    db.add(foreign)
    await db.commit()

    response = await client_a.post(
        OPTIONS.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"supplier_name": "X", "technical_solution_id": str(foreign.id)},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "solution_option_link_mismatch"


async def test_a_requirement_from_another_tender_is_refused(client_a, db, candidacy):
    """`requirements` és global per licitació: la FK acceptaria l'altre expedient."""
    from app.models.enums import AnalysisRunStatus, RequirementCategory, RequirementObligation
    from app.models.requirements import Requirement, RequirementExtractionRun
    from app.models.tenders import Tender

    other = await client_a.post(
        CREATE_TENDER.format(company=candidacy["company_id"]),
        json={"client_token": str(uuid.uuid4()), "title": "Una altra"},
    )
    other_tender = await db.get(Tender, uuid.UUID(other.json()["tender"]["id"]))
    run = RequirementExtractionRun(
        tender_id=other_tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="v1",
        prompt_version="v1",
        schema_version="v1",
        provider="fake",
        model="cap: run de prova",
        is_current=True,
    )
    db.add(run)
    await db.flush()
    foreign = Requirement(
        tender_id=other_tender.id,
        run_id=run.id,
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.MANDATORY,
        title="D'una altra licitació",
        dedup_key=uuid.uuid4().hex,
    )
    db.add(foreign)
    await db.commit()

    response = await client_a.post(
        OPTIONS.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"supplier_name": "X", "requirement_id": str(foreign.id)},
    )
    assert response.status_code == 422


async def test_an_archived_manual_requirement_cannot_be_linked(client_a, db, candidacy):
    """Fora del conjunt vigent: no s'hi pot enllaçar de nou."""
    from app.models.enums import RequirementCategory, RequirementObligation, RequirementOrigin
    from app.models.requirements import Requirement

    archived = Requirement(
        tender_id=uuid.UUID(candidacy["tender_id"]),
        run_id=None,
        origin=RequirementOrigin.MANUAL,
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.MANDATORY,
        title="Arxivat",
        dedup_key=uuid.uuid4().hex,
        archived_at=sa.func.now(),
    )
    db.add(archived)
    await db.commit()

    response = await client_a.post(
        OPTIONS.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"supplier_name": "X", "requirement_id": str(archived.id)},
    )
    assert response.status_code == 422


async def test_an_option_survives_its_requirement_leaving_the_effective_set(
    client_a, db, candidacy
):
    """No s'esborra: es diu que el vincle ha deixat de ser vigent.

    Esborrar-la seria perdre una cotització que algú va demanar de veritat
    perquè una extracció nova ha canviat de parer.
    """
    from app.models.enums import RequirementCategory, RequirementObligation, RequirementOrigin
    from app.models.requirements import Requirement

    manual = Requirement(
        tender_id=uuid.UUID(candidacy["tender_id"]),
        run_id=None,
        origin=RequirementOrigin.MANUAL,
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.MANDATORY,
        title="Viu de moment",
        dedup_key=uuid.uuid4().hex,
    )
    db.add(manual)
    await db.commit()

    created = await _create(client_a, candidacy, requirement_id=str(manual.id))
    assert created["requirement_is_effective"] is True

    # Ara s'arxiva el requisit.
    await db.execute(
        sa.update(Requirement)
        .where(Requirement.id == manual.id)
        .values(archived_at=sa.func.now())
    )
    await db.commit()

    rows = await _list(client_a, candidacy)
    assert len(rows["items"]) == 1
    assert rows["items"][0]["id"] == created["id"]
    assert rows["items"][0]["requirement_id"] == str(manual.id)
    assert rows["items"][0]["requirement_is_effective"] is False


# --------------------------------------------------------------------------- #
# I. Permisos i inquilins                                                       #
# --------------------------------------------------------------------------- #


async def test_a_viewer_can_read_but_not_write(client_a, db, candidacy, tenant_a):
    created = await _create(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.VIEWER)

    listed = await _list(client_a, candidacy)
    assert len(listed["items"]) == 1

    assert (
        await _patch(client_a, candidacy, created["id"], revision=0, unit="u")
    ).status_code == 403
    assert (
        await _select(
            client_a, candidacy, created["id"], revision=0, selection="selected"
        )
    ).status_code == 403


async def test_a_technical_can_do_all_the_public_work(client_a, db, candidacy, tenant_a):
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    created = await _create(client_a, candidacy)
    patched = await _patch(
        client_a, candidacy, created["id"], revision=0, product_name="Terminal Z", unit="u"
    )
    assert patched.status_code == 200
    chosen = await _select(
        client_a, candidacy, created["id"], revision=1, selection="selected"
    )
    assert chosen.status_code == 200


async def test_another_company_cannot_touch_the_options(client_a, client_b, candidacy):
    created = await _create(client_a, candidacy)

    listed = await client_b.get(
        OPTIONS.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"])
    )
    assert listed.status_code in (403, 404)

    for request in (
        client_b.get(
            ONE.format(
                company=candidacy["company_id"],
                workspace=candidacy["workspace_id"],
                option=created["id"],
            )
        ),
        client_b.post(
            SELECTION.format(
                company=candidacy["company_id"],
                workspace=candidacy["workspace_id"],
                option=created["id"],
            ),
            json={"expected_revision": 0, "selection": "selected"},
        ),
    ):
        response = await request
        assert response.status_code in (403, 404)


async def test_an_option_from_another_workspace_is_not_found(
    client_a, db, candidacy, tenant_a
):
    """Un id no és cap autorització: ha de penjar de la candidatura de la URL."""
    from app.models.tenders import Tender
    from app.services import bid_workspace as workspace_service

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
    await db.commit()

    created = await _create(client_a, candidacy)
    leaked = await client_a.get(
        ONE.format(
            company=company.id, workspace=other_workspace.id, option=created["id"]
        )
    )
    assert leaked.status_code == 404


async def test_the_company_never_comes_from_the_body(client_a, client_b, candidacy, tenant_b):
    """`company_id` surt del context, no del que enviï ningú."""
    _, other_company = tenant_b

    response = await client_a.post(
        OPTIONS.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"supplier_name": "X", "company_id": str(other_company.id)},
    )
    assert response.status_code == 422


async def test_the_table_is_tenant_owned(db):
    """§34: no és global. Si es destrueix el tenant, se'n va amb ell."""
    from app.tenant_ops import _direct_tables

    assert "workspace_solution_options" in set(_direct_tables())


# --------------------------------------------------------------------------- #
# J. L'esquema                                                                  #
# --------------------------------------------------------------------------- #


async def test_money_and_quantities_are_decimal_never_float(db):
    columns = WorkspaceSolutionOption.__table__.columns
    for name, precision, scale in (
        ("quantity", 18, 4),
        ("unit_cost_ex_vat", 18, 6),
    ):
        kind = columns[name].type
        assert isinstance(kind, sa.Numeric) and not isinstance(kind, sa.Float)
        assert (kind.precision, kind.scale) == (precision, scale)


async def test_decimals_survive_the_round_trip(client_a, db, candidacy):
    """Sis decimals de cost i quatre de quantitat, exactes."""
    created = await _create(client_a, candidacy, quantity="151.5000")
    await _cost(client_a, candidacy, created["id"], revision=0, unit_cost_ex_vat="12.345678")

    db.expire_all()
    option = await db.get(WorkspaceSolutionOption, uuid.UUID(created["id"]))
    assert option.quantity == Decimal("151.5000")
    assert option.unit_cost_ex_vat == Decimal("12.345678")


async def test_losing_the_solution_or_the_requirement_does_not_lose_the_option(db):
    """`SET NULL` a totes dues: què es va cotitzar continua sent cert."""
    names = {
        fk.column.table.name: fk.ondelete
        for fk in WorkspaceSolutionOption.__table__.foreign_keys
    }
    assert names["workspace_technical_solutions"] == "SET NULL"
    assert names["requirements"] == "SET NULL"
    assert names["users"] == "SET NULL"
    assert names["bid_workspaces"] == "CASCADE"


async def test_the_option_does_not_carry_the_offer_economics(db):
    """El preu de venda no viu aquí, i no hi viurà.

    Des d'S3-S8 la línia sí que sap de quina opció ve (`solution_option_id`),
    però la direcció importa: la línia apunta a l'opció, no al revés. L'opció no
    guanya cap camp d'oferta per això.
    """
    from app.models.offer import EconomicOfferLine

    assert "solution_option_id" in EconomicOfferLine.__table__.columns
    # L'objectiu de markup viu a la LÍNIA (S3-S8b), que és on hi ha el preu.
    # A l'opció no: allà només hi ha cost.
    assert "target_markup_pct" in EconomicOfferLine.__table__.columns
    assert "target_markup_pct" not in WorkspaceSolutionOption.__table__.columns
    for forbidden in ("offered_unit_price_ex_vat", "offer_line_id"):
        assert forbidden not in WorkspaceSolutionOption.__table__.columns


async def test_the_option_does_not_carry_a_dimension_column(db):
    """La dependència amb les magnituds és N:N i viu a la seva taula (0034).

    A l'opció no hi ha cap `dimension_id`: una opció depèn de diverses
    magnituds, i una magnitud la fan servir diverses opcions.
    """
    from app.models import Base

    assert "workspace_solution_option_dimensions" in Base.metadata.tables
    assert "dimension_id" not in WorkspaceSolutionOption.__table__.columns


# --------------------------------------------------------------------------- #
# K. El que no ha de moure's                                                    #
# --------------------------------------------------------------------------- #


async def test_options_do_not_touch_the_technical_solution(client_a, db, candidacy):
    """Ni el proveïdor, ni el producte, ni la validació.

    La capa d'opcions és configuració a part. Sincronitzar-la automàticament
    faria que triar una alternativa reescrivís una proposta que algú ja havia
    validat.
    """
    from app.models.enums import TechnicalSolutionSource, TechnicalSolutionStatus
    from app.models.workspace import WorkspaceTechnicalSolution

    solution = WorkspaceTechnicalSolution(
        company_id=candidacy["company_id"],
        workspace_id=candidacy["workspace_id"],
        title="La proposta",
        supplier_name="Qui ja hi era",
        product_name="El que ja hi era",
        source=TechnicalSolutionSource.MANUAL,
        status=TechnicalSolutionStatus.VALIDATED,
        proposed_solution="Text validat",
    )
    db.add(solution)
    await db.commit()
    solution_id = solution.id
    before = (solution.supplier_name, solution.product_name, solution.status)

    created = await _create(
        client_a,
        candidacy,
        supplier_name="Un altre proveïdor",
        product_name="Un altre producte",
        technical_solution_id=str(solution.id),
    )
    await _select(client_a, candidacy, created["id"], revision=0, selection="selected")

    db.expire_all()
    after = await db.get(WorkspaceTechnicalSolution, solution_id)
    assert (after.supplier_name, after.product_name, after.status) == before
    assert after.proposed_solution == "Text validat"


async def test_options_do_not_make_the_package_stale(client_a, db, candidacy):
    """Crear, cotitzar, triar i arxivar no embruten cap paquet."""
    from app.models.tenders import Tender
    from app.services import tender_lifecycle

    tender = await db.get(Tender, uuid.UUID(candidacy["tender_id"]))
    before = await tender_lifecycle.build_snapshot(db, tender)

    created = await _create(client_a, candidacy, quote_status="requested")
    await _select(client_a, candidacy, created["id"], revision=0, selection="selected")
    await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=created["id"],
        ),
        json={"expected_revision": 1},
    )

    db.expire_all()
    tender = await db.get(Tender, uuid.UUID(candidacy["tender_id"]))
    after = await tender_lifecycle.build_snapshot(db, tender)
    assert after == before


async def test_options_do_not_move_the_technical_scope_fingerprint(
    client_a, db, candidacy
):
    from app.models.workspace import BidWorkspace
    from app.services import technical_scope

    before = await technical_scope.scope_fingerprint(db, candidacy["workspace"])

    created = await _create(client_a, candidacy)
    await _select(client_a, candidacy, created["id"], revision=0, selection="selected")

    # L'objecte de la fixture ha quedat expirat: es torna a llegir en comptes de
    # tocar-ne cap atribut des de codi sincron.
    db.expire_all()
    workspace = await db.get(BidWorkspace, candidacy["workspace_id"])
    after = await technical_scope.scope_fingerprint(db, workspace)
    assert after == before


async def test_options_do_not_touch_the_dimensions(client_a, db, candidacy):
    """Cap sincronització de quantitat, en cap sentit."""
    from app.models.dimensions import WorkspaceDimension
    from app.services import dimensions as dimension_service

    dimension = await dimension_service.create_dimension(
        db,
        workspace=candidacy["workspace"],
        user=candidacy["user"],
        fields={"key": "extensions", "label": "Extensions"},
    )
    await db.commit()
    dimension_id = dimension.id
    before = (dimension.working_value_num, dimension.status, dimension.revision)

    created = await _create(client_a, candidacy, quantity="181")
    await _select(client_a, candidacy, created["id"], revision=0, selection="selected")

    db.expire_all()
    after = await db.get(WorkspaceDimension, dimension_id)
    assert (after.working_value_num, after.status, after.revision) == before
