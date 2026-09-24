"""Objectiu comercial i sincronització explícita (S3-S8b).

Dues necessitats que aquests tests no deixen barrejar.

**A. Assistència de preu.** El cost i un objectiu de markup donen un preu
proposat, i el preu proposat **no és el preu ofert**. Ningú no escriu res fins
que una persona ho demana.

**B. La font viva pot haver canviat.** La línia és un snapshot: quan l'opció es
mou, la línia es queda i el sistema ho diu. Adoptar-ho és una acció, camp a
camp, i sincronitzar el cost mai no toca el preu.

I una tercera que és la trampa fàcil d'aquesta fase:

    12,345678 (opció)  ==  12,3457 (línia)

Són el mateix número a dues escales. Una línia acabada de crear no pot néixer
divergida.
"""

from __future__ import annotations

import re

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
OPTION = OPTIONS + "/{option}"
ADOPT = OPTION + "/to-offer-line"
OFFER = "/api/v1/companies/{company}/workspaces/{workspace}/economic-offer"
LINES = OFFER + "/sections/{section}/lines"
LINE = OFFER + "/lines/{line}"
APPLY = LINE + "/apply-target"
SYNC = LINE + "/sync-from-option"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"


@pytest.fixture
async def candidacy(db, tenant_a, client_a):
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
    tender = await db.get(Tender, uuid.UUID(created.json()["tender"]["id"]))
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
        "user": user,
        "user_id": user.id,
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


async def _revision(client, ctx) -> int:
    response = await client.get(
        OFFER.format(company=ctx["company_id"], workspace=ctx["workspace_id"])
    )
    assert response.status_code == 200, response.text
    return response.json()["revision"]


def _lines_of(payload: dict) -> list[dict]:
    return [
        line
        for lot in payload["lots"]
        for section in lot["sections"]
        for line in section["lines"]
    ]


async def _offer(client, ctx) -> dict:
    response = await client.get(
        OFFER.format(company=ctx["company_id"], workspace=ctx["workspace_id"])
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _line(client, ctx, line_id: str) -> dict:
    for line in _lines_of(await _offer(client, ctx)):
        if line["id"] == line_id:
            return line
    raise AssertionError("linia no trobada")


async def _manual_line(client, ctx, **overrides) -> dict:
    """Una línia escrita a mà, sense cap opció al darrere."""
    body = {
        "expected_revision": await _revision(client, ctx),
        "concept": "A mà",
        "quantity": "1",
        "pricing_basis": "one_off",
    }
    body.update(overrides)
    response = await client.post(
        LINES.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], section=ctx["section_id"]
        ),
        json=body,
    )
    assert response.status_code in (200, 201), response.text
    return _lines_of(response.json())[-1]


async def _patch_line(client, ctx, line_id, **fields):
    return await client.patch(
        LINE.format(company=ctx["company_id"], workspace=ctx["workspace_id"], line=line_id),
        json={"expected_revision": await _revision(client, ctx), **fields},
    )


async def _option(client, ctx, **overrides) -> dict:
    body = {
        "supplier_name": "Proveïdor A",
        "product_name": "Terminal X",
        "quantity": "181",
        "pricing_basis": "monthly",
    }
    body.update(overrides)
    internal = body.pop("internal_economics", None)
    if internal is not None:
        body["internal_economics"] = internal
    response = await client.post(
        OPTIONS.format(company=ctx["company_id"], workspace=ctx["workspace_id"]), json=body
    )
    assert response.status_code == 201, response.text
    option = response.json()
    chosen = await client.post(
        OPTION.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option["id"]
        )
        + "/selection",
        json={"expected_revision": option["revision"], "selection": "selected"},
    )
    assert chosen.status_code == 200, chosen.text
    return chosen.json()


async def _patch_option(client, ctx, option_id, revision, **fields):
    return await client.patch(
        OPTION.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option_id
        ),
        json={"expected_revision": revision, **fields},
    )


async def _adopt(client, ctx, option_id, concept="Terminals IP") -> dict:
    response = await client.post(
        ADOPT.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option_id
        ),
        json={
            "section_id": str(ctx["section_id"]),
            "expected_revision": await _revision(client, ctx),
            "concept": concept,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _findings(client, ctx, code: str) -> list[dict]:
    response = await client.get(
        READINESS.format(company=ctx["company_id"], workspace=ctx["workspace_id"])
    )
    assert response.status_code == 200, response.text
    return [f for f in response.json()["findings"] if f["code"] == code]


# --------------------------------------------------------------------------- #
# A. L'objectiu no és cap preu                                                  #
# --------------------------------------------------------------------------- #


async def test_the_whole_target_walk(client_a, db, candidacy):
    """El criteri d'acceptació de la part comercial.

    Cost 100, preu ofert 140. Posar un objectiu del 20 % proposa 120 i **no
    toca el 140**. Aplicar-lo explícitament escriu 120. Canviar l'objectiu al
    25 % proposa 125 i torna a deixar el preu quiet.
    """
    line = await _manual_line(
        client_a, candidacy, unit_cost_ex_vat="100", offered_unit_price_ex_vat="140"
    )
    assert line["internal_economics"]["target_markup_pct"] is None
    assert line["internal_economics"]["proposed_unit_price_ex_vat"] is None
    assert line["internal_economics"]["price_matches_target"] is None

    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    after = await _line(client_a, candidacy, line["id"])
    assert Decimal(after["internal_economics"]["proposed_unit_price_ex_vat"]) == Decimal("120")
    assert Decimal(after["offered_unit_price_ex_vat"]) == Decimal("140"), "el preu no es mou"
    assert after["internal_economics"]["price_matches_target"] is False

    applied = await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": await _revision(client_a, candidacy)},
    )
    assert applied.status_code == 200, applied.text
    now = await _line(client_a, candidacy, line["id"])
    assert Decimal(now["offered_unit_price_ex_vat"]) == Decimal("120")
    assert now["internal_economics"]["price_matches_target"] is True

    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="25")
    later = await _line(client_a, candidacy, line["id"])
    assert Decimal(later["internal_economics"]["proposed_unit_price_ex_vat"]) == Decimal("125")
    assert Decimal(later["offered_unit_price_ex_vat"]) == Decimal("120"), "res automàtic"
    assert later["internal_economics"]["price_matches_target"] is False


async def test_a_negative_target_is_allowed(client_a, candidacy):
    """Una oferta pot anar deliberadament a cost, o per sota.

    Prohibir-ho seria prohibir una decisió comercial legítima, i el domini no
    ho exigeix enlloc.
    """
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")

    response = await _patch_line(client_a, candidacy, line["id"], target_markup_pct="-10")
    assert response.status_code == 200, response.text

    after = await _line(client_a, candidacy, line["id"])
    assert Decimal(after["internal_economics"]["proposed_unit_price_ex_vat"]) == Decimal("90")


async def test_without_cost_there_is_no_proposal(client_a, candidacy):
    """Desconegut no és zero: sense cost no hi ha preu proposat."""
    line = await _manual_line(client_a, candidacy)
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")

    after = await _line(client_a, candidacy, line["id"])
    assert after["internal_economics"]["proposed_unit_price_ex_vat"] is None
    assert after["internal_economics"]["price_matches_target"] is None


async def test_without_an_offered_price_the_match_is_unknown(client_a, candidacy):
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")

    after = await _line(client_a, candidacy, line["id"])
    assert Decimal(after["internal_economics"]["proposed_unit_price_ex_vat"]) == Decimal("120")
    assert after["internal_economics"]["price_matches_target"] is None


async def test_the_derived_fields_are_not_stored(db):
    """El preu proposat i la coincidència són càlculs, no columnes."""
    columns = set(EconomicOfferLine.__table__.columns.keys())

    assert "target_markup_pct" in columns
    for derived in (
        "proposed_unit_price_ex_vat",
        "price_matches_target",
        "diverged",
        "synced_at",
        "option_hash",
    ):
        assert derived not in columns, derived


async def test_the_proposal_uses_the_canonical_quantization(client_a, candidacy):
    """Quatre decimals i `ROUND_HALF_UP`, com la resta de preus unitaris."""
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="33.3333")
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="15")

    after = await _line(client_a, candidacy, line["id"])
    # 33.3333 x 1.15 = 38.333295 -> 38.3333
    assert after["internal_economics"]["proposed_unit_price_ex_vat"] == "38.3333"


# --------------------------------------------------------------------------- #
# B. Aplicar l'objectiu                                                         #
# --------------------------------------------------------------------------- #


async def test_applying_without_the_inputs_is_refused(client_a, db, candidacy):
    """Ni cost zero ni markup zero: escriuríem un preu que ningú no ha decidit."""
    line = await _manual_line(client_a, candidacy, offered_unit_price_ex_vat="140")

    response = await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": await _revision(client_a, candidacy)},
    )
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "economic_line_target_inputs_missing"
    assert set(body["details"]["missing"]) == {"unit_cost_ex_vat", "target_markup_pct"}

    db.expire_all()
    stored = await db.get(EconomicOfferLine, uuid.UUID(line["id"]))
    assert stored.offered_unit_price_ex_vat == Decimal("140.0000")


async def test_applying_twice_is_a_no_op(client_a, candidacy):
    line = await _manual_line(
        client_a, candidacy, unit_cost_ex_vat="100", offered_unit_price_ex_vat="120"
    )
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    before = await _revision(client_a, candidacy)

    response = await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": before},
    )
    assert response.status_code == 200
    assert await _revision(client_a, candidacy) == before, "un no-op no mou el testimoni"


async def test_a_stale_revision_is_a_conflict(client_a, candidacy):
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    stale = await _revision(client_a, candidacy) - 1

    response = await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": stale},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "economic_offer_revision_conflict"


async def test_apply_target_uses_the_line_cost_never_the_live_option(
    client_a, db, candidacy
):
    """La prova que separa les dues decisions.

    L'opció ha canviat de cost, però la línia encara porta el que va adoptar.
    `apply-target` treballa amb el cost de la LÍNIA: per fer servir el nou,
    primer cal sincronitzar-lo, i això és una segona decisió.
    """
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "100"}
    )
    line = await _adopt(client_a, candidacy, option["id"])
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")

    # L'opcio baixa a 50; la linia continua amb 100.
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.patch(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": stored.revision, "unit_cost_ex_vat": "50"},
    )

    await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": await _revision(client_a, candidacy)},
    )

    after = await _line(client_a, candidacy, line["id"])
    # 100 x 1,20 i no 50 x 1,20.
    assert Decimal(after["offered_unit_price_ex_vat"]) == Decimal("120")


async def test_a_manual_line_can_still_use_the_target(client_a, candidacy):
    """El preu objectiu no depèn de cap opció."""
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")
    assert line["solution_option_id"] is None

    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    applied = await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": await _revision(client_a, candidacy)},
    )
    assert applied.status_code == 200
    after = await _line(client_a, candidacy, line["id"])
    assert Decimal(after["offered_unit_price_ex_vat"]) == Decimal("120")


async def test_the_target_can_be_written_when_the_line_is_born(client_a, candidacy):
    """`LineCreateRequest` porta `target_markup_pct` des d'aquesta fase.

    El PATCH l'acceptava i la creació no: enviar-lo al cos d'una línia nova
    esclatava en comptes d'escriure'l. Un camp que el contracte anuncia i que
    el servei no admet no és una limitació, és una porta trencada.

    I escriure'l al néixer continua sense moure cap preu: el preu ofert només
    el toca `apply-target`.
    """
    line = await _manual_line(
        client_a, candidacy, unit_cost_ex_vat="100", target_markup_pct="20"
    )
    assert line["offered_unit_price_ex_vat"] is None
    assert Decimal(line["internal_economics"]["target_markup_pct"]) == Decimal("20")
    assert Decimal(line["internal_economics"]["proposed_unit_price_ex_vat"]) == Decimal("120")

    applied = await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": await _revision(client_a, candidacy)},
    )
    assert applied.status_code == 200, applied.text
    after = await _line(client_a, candidacy, line["id"])
    assert Decimal(after["offered_unit_price_ex_vat"]) == Decimal("120")


# --------------------------------------------------------------------------- #
# C. La divergència és derivada                                                 #
# --------------------------------------------------------------------------- #


async def test_a_fresh_line_is_never_diverged(client_a, db, candidacy):
    """El blocador de la fase: 12,345678 i 12,3457 són el mateix número.

    L'opció desa el cost amb sis decimals i la línia amb quatre. Comparar-los en
    brut faria que tota línia acabada de crear es veiés divergida i que l'avís
    no volgués dir res.
    """
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "12.345678"}
    )
    line = await _adopt(client_a, candidacy, option["id"])

    db.expire_all()
    stored = await db.get(EconomicOfferLine, uuid.UUID(line["id"]))
    assert stored.unit_cost_ex_vat == Decimal("12.3457")

    assert line["diverged_fields"] == []
    assert await _findings(client_a, candidacy, "economic_line_option_diverged") == []


async def test_a_cost_change_that_survives_the_line_scale_diverges(
    client_a, db, candidacy
):
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "12.345678"}
    )
    line = await _adopt(client_a, candidacy, option["id"])

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.patch(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": stored.revision, "unit_cost_ex_vat": "99.5"},
    )

    after = await _line(client_a, candidacy, line["id"])
    assert after["diverged_fields"] == ["unit_cost_ex_vat"]


async def test_a_cost_change_invisible_at_the_line_scale_does_not_diverge(
    client_a, db, candidacy
):
    """12,345678 → 12,345699 continuen sent 12,3457 a la línia.

    Res no ha canviat del que l'oferta farà servir, i dir el contrari seria
    demanar una revisió que no té cap efecte possible.
    """
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "12.345678"}
    )
    line = await _adopt(client_a, candidacy, option["id"])

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.patch(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": stored.revision, "unit_cost_ex_vat": "12.345699"},
    )

    after = await _line(client_a, candidacy, line["id"])
    assert after["diverged_fields"] == []


async def test_editing_the_option_diverges_without_touching_the_line(
    client_a, db, candidacy
):
    """Cap escriptura sobre la línia, cap revisió de l'oferta, i un finding info."""
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    revision = await _revision(client_a, candidacy)

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    updated_before = (
        await db.get(EconomicOfferLine, uuid.UUID(line["id"]))
    ).updated_at
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name="Proveïdor B"
    )

    after = await _line(client_a, candidacy, line["id"])
    assert after["supplier_name"] == "Proveïdor A", "la línia no es mou"
    assert after["diverged_fields"] == ["supplier_name"]
    assert await _revision(client_a, candidacy) == revision, "cap mutació de l'oferta"

    db.expire_all()
    assert (
        await db.get(EconomicOfferLine, uuid.UUID(line["id"]))
    ).updated_at == updated_before

    findings = await _findings(client_a, candidacy, "economic_line_option_diverged")
    assert len(findings) == 1
    assert findings[0]["severity"] == "info", "informa; no diu que la línia sigui dolenta"
    # El detall viu al read-model de la línia, que sí que està redactat per rol.
    # Readiness només diu QUINA opció mirar (S3-S8b.1).
    assert set(findings[0]["context"]) == {"solution_option_id"}


async def test_a_manual_line_never_diverges(client_a, candidacy):
    await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")

    assert await _findings(client_a, candidacy, "economic_line_option_diverged") == []


async def test_an_archived_line_does_not_diverge(client_a, db, candidacy):
    """És la fotografia del que es va oferir: no hi ha res a revisar-hi."""
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name="Proveïdor B"
    )
    assert await _findings(client_a, candidacy, "economic_line_option_diverged")

    archived = await client_a.post(
        LINE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        )
        + "/archive",
        json={"expected_revision": await _revision(client_a, candidacy)},
    )
    assert archived.status_code in (200, 204), archived.text

    assert await _findings(client_a, candidacy, "economic_line_option_diverged") == []


async def test_the_finding_carries_neither_values_nor_field_names(
    client_a, db, candidacy
):
    """Readiness diu que la font ha canviat, i prou.

    Transportar-hi el diff la convertiria en una via sense redacció cap al
    cost: el mateix finding el llegeix qualsevol membre.

    La divergència és MIXTA a propòsit —proveïdor i cost—, que és el cas que
    sí emet finding. Amb una divergència només de cost no n'hi ha cap, i això
    ho defensa `test_a_cost_only_divergence_says_nothing_in_the_shared_readiness`.
    """
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "12.345678"}
    )
    await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.patch(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": stored.revision, "unit_cost_ex_vat": "99.5"},
    )
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name="Proveïdor B"
    )

    findings = await _findings(client_a, candidacy, "economic_line_option_diverged")
    assert findings
    # Ni els valors ni els NOMS: "el cost ha canviat" ja és saber alguna cosa
    # del cost, i readiness la llegeix tothom.
    rendered = str(findings[0])
    for value in ("12.345678", "12.3457", "99.5", "unit_cost_ex_vat", "changed_fields"):
        assert value not in rendered, value


# --------------------------------------------------------------------------- #
# D. Sincronitzar, camp a camp                                                  #
# --------------------------------------------------------------------------- #


async def _sync(client, ctx, line_id, fields, revision=None):
    return await client.post(
        SYNC.format(company=ctx["company_id"], workspace=ctx["workspace_id"], line=line_id),
        json={
            "expected_revision": revision
            if revision is not None
            else await _revision(client, ctx),
            "fields": fields,
        },
    )


async def test_partial_sync_leaves_the_rest_alone(client_a, db, candidacy):
    """El criteri d'acceptació de la part de sincronització.

    L'opció canvia tres camps; se n'adopta un i el finding **continua**, perquè
    els altres dos encara difereixen. El preu ofert no es mou en cap moment.
    """
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "12"}
    )
    line = await _adopt(client_a, candidacy, option["id"])
    await _patch_line(
        client_a, candidacy, line["id"], offered_unit_price_ex_vat="17", target_markup_pct="25"
    )

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    patched = await _patch_option(
        client_a,
        candidacy,
        option["id"],
        stored.revision,
        supplier_name="Proveïdor B",
        product_name="Terminal Y",
    )
    await client_a.patch(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": patched.json()["revision"], "unit_cost_ex_vat": "10"},
    )

    before = await _line(client_a, candidacy, line["id"])
    assert set(before["diverged_fields"]) == {
        "supplier_name",
        "product_name",
        "unit_cost_ex_vat",
    }

    # Només el proveïdor.
    synced = await _sync(client_a, candidacy, line["id"], ["supplier_name"])
    assert synced.status_code == 200, synced.text
    mid = await _line(client_a, candidacy, line["id"])
    assert mid["supplier_name"] == "Proveïdor B"
    assert mid["product_name"] == "Terminal X", "no s'ha refrescat el que no s'ha demanat"
    assert set(mid["diverged_fields"]) == {"product_name", "unit_cost_ex_vat"}
    assert await _findings(client_a, candidacy, "economic_line_option_diverged")

    # I ara la resta.
    rest = await _sync(
        client_a, candidacy, line["id"], ["product_name", "unit_cost_ex_vat"]
    )
    assert rest.status_code == 200, rest.text
    after = await _line(client_a, candidacy, line["id"])
    assert after["product_name"] == "Terminal Y"
    assert after["diverged_fields"] == []
    assert await _findings(client_a, candidacy, "economic_line_option_diverged") == []

    # El preu ofert i l'objectiu, intactes de punta a punta.
    assert Decimal(after["offered_unit_price_ex_vat"]) == Decimal("17")
    assert Decimal(after["internal_economics"]["target_markup_pct"]) == Decimal("25")


async def test_syncing_the_cost_never_touches_the_price(client_a, db, candidacy):
    """§28, explícit: cost 12 → 10 i el preu continua a 17."""
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "12"}
    )
    line = await _adopt(client_a, candidacy, option["id"])
    await _patch_line(
        client_a, candidacy, line["id"], offered_unit_price_ex_vat="17", target_markup_pct="25"
    )
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.patch(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": stored.revision, "unit_cost_ex_vat": "10"},
    )

    await _sync(client_a, candidacy, line["id"], ["unit_cost_ex_vat"])

    after = await _line(client_a, candidacy, line["id"])
    assert Decimal(after["internal_economics"]["unit_cost_ex_vat"]) == Decimal("10")
    assert Decimal(after["offered_unit_price_ex_vat"]) == Decimal("17"), "cap apply implícit"
    assert Decimal(after["internal_economics"]["target_markup_pct"]) == Decimal("25")
    # I el derivat s'ha recalculat sol: 10 x 1,25.
    assert Decimal(
        after["internal_economics"]["proposed_unit_price_ex_vat"]
    ) == Decimal("12.5")
    assert after["internal_economics"]["price_matches_target"] is False


async def test_syncing_the_product_never_touches_the_concept(client_a, db, candidacy):
    """El concepte va ser una decisió humana d'S3-S8."""
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"], concept="Terminals IP")
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, product_name="Terminal Y"
    )

    await _sync(client_a, candidacy, line["id"], ["product_name"])

    after = await _line(client_a, candidacy, line["id"])
    assert after["product_name"] == "Terminal Y"
    assert after["concept"] == "Terminals IP"


@pytest.mark.parametrize(
    "field", ["concept", "offered_unit_price_ex_vat", "vat_rate", "target_markup_pct",
              "solution_option_id", "notes", "position"]
)
async def test_fields_the_option_is_not_authority_for_are_refused(
    client_a, candidacy, field
):
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])

    response = await _sync(client_a, candidacy, line["id"], [field])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "economic_line_sync_field_not_allowed"


async def test_an_empty_field_list_is_refused(client_a, candidacy):
    """No hi ha cap «sincronitza-ho tot» implícit."""
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])

    response = await _sync(client_a, candidacy, line["id"], [])
    assert response.status_code == 422


async def test_a_line_without_an_option_cannot_sync(client_a, candidacy):
    line = await _manual_line(client_a, candidacy)

    response = await _sync(client_a, candidacy, line["id"], ["supplier_name"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "economic_line_has_no_solution_option"


async def test_the_source_never_comes_from_the_body(client_a, candidacy):
    """Enviar una altra opció no canvia d'on surt la xifra."""
    option = await _option(client_a, candidacy)
    other = await _option(client_a, candidacy, supplier_name="Un altre")
    line = await _adopt(client_a, candidacy, option["id"])

    response = await client_a.post(
        SYNC.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={
            "expected_revision": await _revision(client_a, candidacy),
            "fields": ["supplier_name"],
            "solution_option_id": other["id"],
        },
    )
    # El camp s'ignora i la font continua sent la de la línia.
    assert response.status_code == 200, response.text
    after = await _line(client_a, candidacy, line["id"])
    assert after["supplier_name"] == "Proveïdor A"


async def test_a_nullable_field_can_be_synced_to_null(client_a, db, candidacy):
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name=None
    )

    response = await _sync(client_a, candidacy, line["id"], ["supplier_name"])
    assert response.status_code == 200, response.text

    after = await _line(client_a, candidacy, line["id"])
    assert after["supplier_name"] is None


async def test_a_required_field_cannot_be_synced_from_null(client_a, db, candidacy):
    """Ni `quantity = 0` ni `one_off` per omplir el forat."""
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(client_a, candidacy, option["id"], stored.revision, quantity=None)

    response = await _sync(client_a, candidacy, line["id"], ["quantity"])
    assert response.status_code == 422
    body = response.json()["error"]
    assert body["code"] == "economic_line_sync_source_missing"
    assert body["details"]["missing"] == ["quantity"]

    after = await _line(client_a, candidacy, line["id"])
    assert Decimal(after["quantity"]) == Decimal("181")


async def test_a_sync_that_changes_nothing_does_not_move_the_token(
    client_a, db, candidacy
):
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    before = await _revision(client_a, candidacy)

    response = await _sync(client_a, candidacy, line["id"], ["supplier_name"])
    assert response.status_code == 200
    assert await _revision(client_a, candidacy) == before


async def test_a_real_sync_moves_the_token_exactly_once(client_a, db, candidacy):
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name="Proveïdor B"
    )
    before = await _revision(client_a, candidacy)

    await _sync(client_a, candidacy, line["id"], ["supplier_name"])
    assert await _revision(client_a, candidacy) == before + 1


async def test_a_stale_sync_is_a_conflict(client_a, db, candidacy):
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name="Proveïdor B"
    )

    response = await _sync(
        client_a, candidacy, line["id"], ["supplier_name"], revision=0
    )
    assert response.status_code == 409


async def test_syncing_from_an_archived_option_is_refused(client_a, db, candidacy):
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name="Proveïdor B"
    )
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.post(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/archive",
        json={"expected_revision": stored.revision},
    )

    response = await _sync(client_a, candidacy, line["id"], ["supplier_name"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "solution_option_archived"


async def test_syncing_does_not_touch_the_technical_solution(client_a, db, candidacy):
    from app.models.enums import TechnicalSolutionSource, TechnicalSolutionStatus
    from app.models.workspace import WorkspaceTechnicalSolution

    solution = WorkspaceTechnicalSolution(
        company_id=candidacy["company_id"],
        workspace_id=candidacy["workspace_id"],
        title="La proposta",
        source=TechnicalSolutionSource.MANUAL,
        status=TechnicalSolutionStatus.VALIDATED,
        proposed_solution="Text validat",
    )
    db.add(solution)
    await db.commit()
    solution_id = solution.id
    before = (solution.status, solution.proposed_solution)

    option = await _option(client_a, candidacy, technical_solution_id=str(solution_id))
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name="Proveïdor B"
    )
    await _sync(client_a, candidacy, line["id"], ["technical_solution_id"])

    db.expire_all()
    after = await db.get(WorkspaceTechnicalSolution, solution_id)
    assert (after.status, after.proposed_solution) == before


# --------------------------------------------------------------------------- #
# E. Rols i redacció                                                            #
# --------------------------------------------------------------------------- #


async def test_a_technical_never_sees_the_target(client_a, db, candidacy, tenant_a):
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    payload = await _offer(client_a, candidacy)
    assert payload["can_view_internal_economics"] is False
    for item in _lines_of(payload):
        assert item["internal_economics"] is None
    assert "target_markup_pct" not in str(payload)
    assert "proposed_unit_price" not in str(payload)


async def test_a_technical_cannot_apply_or_sync(client_a, db, candidacy, tenant_a):
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    applied = await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": 0},
    )
    assert applied.status_code == 403

    synced = await client_a.post(
        SYNC.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": 0, "fields": ["unit_cost_ex_vat"]},
    )
    assert synced.status_code == 403


async def test_a_technical_cannot_write_the_target(client_a, db, candidacy, tenant_a):
    """Tot el router econòmic exigeix rols econòmics: també el PATCH de línia."""
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    response = await _patch_line(client_a, candidacy, line["id"], target_markup_pct="99")
    assert response.status_code == 403

    db.expire_all()
    stored = await db.get(EconomicOfferLine, uuid.UUID(line["id"]))
    assert stored.target_markup_pct == Decimal("20.0000"), "el camp ocult es preserva"


async def test_a_viewer_sees_no_internal_economics(client_a, db, candidacy, tenant_a):
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    await _set_role(db, tenant_a, MembershipRole.VIEWER)

    payload = await _offer(client_a, candidacy)
    assert payload["can_view_internal_economics"] is False
    assert "target_markup_pct" not in str(payload)


async def test_the_audit_trail_carries_no_figures(client_a, db, candidacy):
    from app.models.audit import AuditLog

    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "100"}
    )
    line = await _adopt(client_a, candidacy, option["id"])
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": await _revision(client_a, candidacy)},
    )

    # Els identificadors s'esborren abans de buscar-hi xifres: un UUID porta
    # trenta-dos digits hexadecimals i qualsevol dels imports hi cau tard o
    # d'hora per atzar. Buscar-hi a pel seria un test que falla sol.
    identifier = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

    entries = (await db.scalars(sa.select(AuditLog))).all()
    for entry in entries:
        summary = entry.summary or ""
        without_ids = identifier.sub("", summary)
        for value in ("120", "100", "20.0000"):
            assert value not in without_ids, summary


# --------------------------------------------------------------------------- #
# F. El paquet                                                                  #
# --------------------------------------------------------------------------- #


async def _digest(db, candidacy) -> str:
    from app.models.workspace import BidWorkspace
    from app.services import submission_manifest as manifest_service

    db.expire_all()
    workspace = await db.get(BidWorkspace, candidacy["workspace_id"])
    _, _, value = await manifest_service.current_manifest(db, workspace, revision_number=1)
    return value


async def test_the_target_alone_does_not_stale_the_package(client_a, db, candidacy):
    """L'objectiu és economia interna i no és contingut del manifest."""
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")
    base = await _digest(db, candidacy)

    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")

    assert await _digest(db, candidacy) == base


async def test_applying_the_target_does_stale_the_package(client_a, db, candidacy):
    """Perquè canvia el preu ofert, que sí que hi és."""
    line = await _manual_line(client_a, candidacy, unit_cost_ex_vat="100")
    await _patch_line(client_a, candidacy, line["id"], target_markup_pct="20")
    base = await _digest(db, candidacy)

    await client_a.post(
        APPLY.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line["id"],
        ),
        json={"expected_revision": await _revision(client_a, candidacy)},
    )

    assert await _digest(db, candidacy) != base


async def test_syncing_only_the_cost_does_not_stale_the_package(
    client_a, db, candidacy
):
    """El cost és economia interna: no entra al manifest."""
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "12"}
    )
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await client_a.patch(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": stored.revision, "unit_cost_ex_vat": "10"},
    )
    base = await _digest(db, candidacy)

    await _sync(client_a, candidacy, line["id"], ["unit_cost_ex_vat"])

    assert await _digest(db, candidacy) == base


async def test_syncing_a_manifest_field_does_stale_the_package(client_a, db, candidacy):
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, quantity="200"
    )
    base = await _digest(db, candidacy)

    await _sync(client_a, candidacy, line["id"], ["quantity"])

    assert await _digest(db, candidacy) != base


async def test_editing_the_option_alone_never_stales_the_package(
    client_a, db, candidacy
):
    option = await _option(client_a, candidacy)
    await _adopt(client_a, candidacy, option["id"])
    base = await _digest(db, candidacy)

    db.expire_all()
    stored = await db.get(WorkspaceSolutionOption, uuid.UUID(option["id"]))
    await _patch_option(
        client_a, candidacy, option["id"], stored.revision, supplier_name="Proveïdor B"
    )

    assert await _digest(db, candidacy) == base


# --------------------------------------------------------------------------- #
# G. La divergència també es redacta (S3-S8b.1)                                 #
# --------------------------------------------------------------------------- #
#
# El NOM d'un camp intern ja és informació de dins: saber que «el cost ha
# canviat» és saber alguna cosa del cost. Filtrar-ho a la pantalla no serveix
# —la resposta ja ha sortit del servidor—, així que aquests tests miren el JSON
# de l'API i no cap component.


async def _diverged_of(client, ctx, line_id: str) -> list[str]:
    return (await _line(client, ctx, line_id))["diverged_fields"]


async def _cost_only_divergence(client, db, ctx) -> str:
    """Una línia on l'ÚNICA diferència amb la seva opció és el cost."""
    option = await _option(client, ctx, internal_economics={"unit_cost_ex_vat": "12"})
    line = await _adopt(client, ctx, option["id"])
    # Adoptar no mou la revisió de l'opció: ja la portem de la resposta, i així
    # no cal expirar la sessió (que caducaria també l'usuari de la fixture).
    await client.patch(
        OPTION.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option["id"]
        )
        + "/internal-economics",
        json={"expected_revision": option["revision"], "unit_cost_ex_vat": "99.5"},
    )
    return line["id"]


async def _mixed_divergence(client, ctx, line_concept="Terminals IP") -> dict:
    """Una línia on divergeixen un camp públic i un d'intern.

    Les revisions surten de cada resposta: expirar la sessió caducaria també
    l'usuari de la fixture.
    """
    option = await _option(client, ctx, internal_economics={"unit_cost_ex_vat": "12"})
    line = await _adopt(client, ctx, option["id"], concept=line_concept)

    renamed = await _patch_option(
        client, ctx, option["id"], option["revision"], supplier_name="Proveïdor B"
    )
    assert renamed.status_code == 200, renamed.text

    repriced = await client.patch(
        OPTION.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], option=option["id"]
        )
        + "/internal-economics",
        json={"expected_revision": renamed.json()["revision"], "unit_cost_ex_vat": "99.5"},
    )
    assert repriced.status_code == 200, repriced.text
    return line


async def _diverged_findings(client, ctx) -> list[dict]:
    return await _findings(client, ctx, "economic_line_option_diverged")


async def test_a_cost_only_divergence_says_nothing_in_the_shared_readiness(
    client_a, db, candidacy
):
    """El cas que tanca el forat.

    Readiness és una sola representació compartida i no té redacció per rol.
    Treure'n els noms de camp no bastava: si l'únic camp divergit és el cost,
    l'existència mateixa del finding ja ho diu, perquè qui el llegeix veu que
    cap camp públic no ha canviat i només en queda un de possible.
    """
    line_id = await _cost_only_divergence(client_a, db, candidacy)

    # Qui pot veure el cost, el veu divergit al seu read-model.
    assert await _diverged_of(client_a, candidacy, line_id) == ["unit_cost_ex_vat"]
    # I la readiness compartida no diu res.
    assert await _diverged_findings(client_a, candidacy) == []


@pytest.mark.parametrize("role", [MembershipRole.TECHNICAL, MembershipRole.VIEWER])
async def test_a_role_without_internal_economics_cannot_deduce_the_cost_moved(
    client_a, db, candidacy, tenant_a, role
):
    """Les dues meitats alhora, que és com es deduïa.

    Amb el finding present i `diverged_fields` buit, el rol restava: «hi ha
    divergència i no és cap camp públic, per tant és el cost». Ara no hi ha res
    a restar.
    """
    line_id = await _cost_only_divergence(client_a, db, candidacy)
    await _set_role(db, tenant_a, role)

    payload = await _offer(client_a, candidacy)
    line = next(item for item in _lines_of(payload) if item["id"] == line_id)
    assert line["internal_economics"] is None
    assert line["diverged_fields"] == []
    assert await _diverged_findings(client_a, candidacy) == []


async def test_a_public_divergence_does_reach_the_shared_readiness(client_a, candidacy):
    """Amagar-ho tot no seria redactar: seria apagar la funcionalitat."""
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    await _patch_option(
        client_a, candidacy, option["id"], option["revision"], supplier_name="Proveïdor B"
    )

    findings = await _diverged_findings(client_a, candidacy)
    assert [finding["entity_id"] for finding in findings] == [line["id"]]
    assert await _diverged_of(client_a, candidacy, line["id"]) == ["supplier_name"]


async def test_a_mixed_divergence_reaches_the_shared_readiness_too(
    client_a, db, candidacy
):
    """N'hi ha prou amb un camp públic perquè el finding tingui sentit."""
    line = await _mixed_divergence(client_a, candidacy)

    assert await _diverged_findings(client_a, candidacy)
    assert set(await _diverged_of(client_a, candidacy, line["id"])) == {
        "supplier_name",
        "unit_cost_ex_vat",
    }


async def test_syncing_the_public_part_removes_the_shared_finding(
    client_a, db, candidacy
):
    """La transició que ho lliga tot.

    Mixta → se sincronitza el proveïdor → només queda el cost. El finding
    compartit ha de desaparèixer, i el read-model econòmic ha de continuar
    dient que el cost divergeix: si no, el fix hauria amagat informació a qui
    sí que la pot veure.
    """
    line = await _mixed_divergence(client_a, candidacy)
    assert await _diverged_findings(client_a, candidacy)

    synced = await _sync(client_a, candidacy, line["id"], ["supplier_name"])
    assert synced.status_code == 200, synced.text

    assert await _diverged_of(client_a, candidacy, line["id"]) == ["unit_cost_ex_vat"]
    assert await _diverged_findings(client_a, candidacy) == []


async def test_syncing_the_cost_leaves_no_divergence_anywhere(client_a, db, candidacy):
    """I quan s'adopta el cost, tampoc no en queda al read-model."""
    line_id = await _cost_only_divergence(client_a, db, candidacy)
    assert await _diverged_of(client_a, candidacy, line_id) == ["unit_cost_ex_vat"]

    synced = await _sync(client_a, candidacy, line_id, ["unit_cost_ex_vat"])
    assert synced.status_code == 200, synced.text

    assert await _diverged_of(client_a, candidacy, line_id) == []
    assert await _diverged_findings(client_a, candidacy) == []


async def test_the_shared_finding_never_counts_how_many_fields_moved(
    client_a, db, candidacy
):
    """Un recompte també deixaria restar: 1 públic de 2 en total diria que
    l'altre és el cost."""
    await _mixed_divergence(client_a, candidacy)

    finding = (await _diverged_findings(client_a, candidacy))[0]
    assert set(finding["context"]) == {"solution_option_id"}
    for leak in ("count", "fields", "unit_cost", "99.5", "12.0000"):
        assert leak not in str(finding), leak


async def test_an_economic_role_sees_the_cost_divergence(client_a, db, candidacy):
    line_id = await _cost_only_divergence(client_a, db, candidacy)

    assert await _diverged_of(client_a, candidacy, line_id) == ["unit_cost_ex_vat"]


@pytest.mark.parametrize("role", [MembershipRole.TECHNICAL, MembershipRole.VIEWER])
async def test_a_role_without_internal_economics_never_sees_the_cost_divergence(
    client_a, db, candidacy, tenant_a, role
):
    """El nom del camp no ha de sortir del servidor per a aquests rols.

    Amb la divergència NOMÉS al cost, la línia s'ha de veure igual que si no
    n'hi hagués cap: no hi ha res que aquest rol pugui revisar, i insinuar-ho
    seria dir-li que el cost s'ha mogut.
    """
    line_id = await _cost_only_divergence(client_a, db, candidacy)
    await _set_role(db, tenant_a, role)

    payload = await _offer(client_a, candidacy)
    assert payload["can_view_internal_economics"] is False

    line = next(item for item in _lines_of(payload) if item["id"] == line_id)
    assert line["diverged_fields"] == []
    assert line["internal_economics"] is None
    # I cap xifra, per si de cas.
    for value in ("12.0000", "99.5", "99.5000"):
        assert value not in str(payload), value


@pytest.mark.parametrize("role", [MembershipRole.TECHNICAL, MembershipRole.VIEWER])
async def test_a_public_divergence_is_not_hidden_from_anyone(
    client_a, db, candidacy, tenant_a, role
):
    """La correcció no pot amagar tota la funcionalitat.

    El proveïdor no és economia interna: que hagi canviat ho ha de poder veure
    qualsevol que llegeixi l'oferta.
    """
    option = await _option(client_a, candidacy)
    line = await _adopt(client_a, candidacy, option["id"])
    await _patch_option(
        client_a, candidacy, option["id"], option["revision"], supplier_name="Proveïdor B"
    )
    await _set_role(db, tenant_a, role)

    assert await _diverged_of(client_a, candidacy, line["id"]) == ["supplier_name"]


async def test_a_mixed_divergence_keeps_the_public_part_and_drops_the_internal(
    client_a, db, candidacy, tenant_a
):
    """El cas que separa «redactar» de «amagar-ho tot»."""
    option = await _option(
        client_a, candidacy, internal_economics={"unit_cost_ex_vat": "12"}
    )
    line = await _adopt(client_a, candidacy, option["id"])
    patched = await _patch_option(
        client_a, candidacy, option["id"], option["revision"], supplier_name="Proveïdor B"
    )
    await client_a.patch(
        OPTION.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            option=option["id"],
        )
        + "/internal-economics",
        json={"expected_revision": patched.json()["revision"], "unit_cost_ex_vat": "99.5"},
    )

    assert set(await _diverged_of(client_a, candidacy, line["id"])) == {
        "supplier_name",
        "unit_cost_ex_vat",
    }

    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)
    assert await _diverged_of(client_a, candidacy, line["id"]) == ["supplier_name"]


@pytest.mark.parametrize(
    "role",
    [MembershipRole.OWNER, MembershipRole.BID_MANAGER, MembershipRole.TECHNICAL,
     MembershipRole.VIEWER],
)
async def test_readiness_never_carries_internal_field_names(
    client_a, db, candidacy, tenant_a, role
):
    """Readiness no té redacció per rol: el mateix finding el llegeix tothom.

    Per tant el diff no hi pot viatjar de cap manera —ni tan sols per als rols
    econòmics, perquè seria el mateix camp amb dos significats segons qui
    mira—. El detall sencer ja és al read-model de la línia, que sí que està
    redactat.

    La divergència ha de ser MIXTA: amb una de només cost no s'emet cap finding,
    que és el que defensa
    `test_a_cost_only_divergence_says_nothing_in_the_shared_readiness`.
    """
    await _mixed_divergence(client_a, candidacy)
    await _set_role(db, tenant_a, role)

    findings = await _findings(client_a, candidacy, "economic_line_option_diverged")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["severity"] == "info"
    assert set(finding["context"]) == {"solution_option_id"}

    rendered = str(finding)
    for forbidden in (
        "unit_cost_ex_vat",
        "target_markup_pct",
        "proposed_unit_price",
        "price_matches_target",
        "changed_fields",
        "99.5",
        "12.0000",
    ):
        assert forbidden not in rendered, forbidden


async def test_no_surface_leaks_the_internal_field_name(client_a, db, candidacy, tenant_a):
    """L'escombrada: cap resposta que aquest rol pugui demanar el porta."""
    from app.models.audit import AuditLog

    line_id = await _cost_only_divergence(client_a, db, candidacy)
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    offer_body = (
        await client_a.get(
            OFFER.format(
                company=candidacy["company_id"], workspace=candidacy["workspace_id"]
            )
        )
    ).text
    readiness_body = (
        await client_a.get(
            READINESS.format(
                company=candidacy["company_id"], workspace=candidacy["workspace_id"]
            )
        )
    ).text
    # I una resposta d'error del mateix recurs.
    refused = await client_a.post(
        SYNC.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            line=line_id,
        ),
        json={"expected_revision": 0, "fields": ["unit_cost_ex_vat"]},
    )
    assert refused.status_code == 403, "un technical no pot sincronitzar"

    for body in (offer_body, readiness_body):
        assert "unit_cost_ex_vat" not in body
        assert "target_markup_pct" not in body

    # L'auditoria tampoc no ha guanyat res de nou.
    for entry in (await db.scalars(sa.select(AuditLog))).all():
        assert "99.5" not in (entry.summary or "")
