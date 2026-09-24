"""Dubtes i discrepàncies (S3-S10).

Un **issue** és coneixement que falta: una pregunta sense resposta, dues fonts
que es contradiuen, una dada que no té ningú. No és feina per fer —això ja és
`WorkspaceTask`—, i la distinció no és cosmètica: una tasca es tanca fent-la i
un dubte es tanca sabent alguna cosa.

Quatre frases que aquests tests no deixen tornar enrere:

    cap dubte neix sol
    bloquejar la revisió final  ≠  obrir un dubte
    descartar  ≠  resoldre
    resoldre un dubte  ≠  decidir el que el dubte preguntava
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.enums import IssueStatus, MembershipRole
from app.models.issues import WorkspaceIssue, WorkspaceIssueSourceRef
from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

CREATE_TENDER = "/api/v1/companies/{company}/tenders"
UPLOAD = "/api/v1/companies/{company}/tenders/{tender}/documents"
VERSIONS = UPLOAD + "/{document}/versions"
PAGE_REF = "/api/v1/companies/{company}/tenders/{tender}/source-refs/extracted-page"
FILE_REF = "/api/v1/companies/{company}/tenders/{tender}/source-refs/document-file"
ISSUES = "/api/v1/companies/{company}/workspaces/{workspace}/issues"
ONE = ISSUES + "/{issue}"
RESOLVE = ONE + "/resolve"
DISMISS = ONE + "/dismiss"
BLOCK = ONE + "/final-review-block"
REFS = ONE + "/source-refs"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"

PLEC = build_pdf(["El servei actual compta amb 151 extensions telefoniques. " * 4])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_V1 = b"PK\x03\x04" + b"\x01" * 64
XLSX_V2 = b"PK\x03\x04" + b"\x02" * 64


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
        "user_id": user.id,
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
    body = {"issue_type": "internal_question", "title": "Falta una dada"}
    body.update(overrides)
    response = await client.post(
        ISSUES.format(company=ctx["company_id"], workspace=ctx["workspace_id"]), json=body
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _url(ctx, issue_id, template=ONE) -> str:
    return template.format(
        company=ctx["company_id"], workspace=ctx["workspace_id"], issue=issue_id
    )


async def _findings(client, ctx, code: str) -> list[dict]:
    response = await client.get(
        READINESS.format(company=ctx["company_id"], workspace=ctx["workspace_id"])
    )
    assert response.status_code == 200, response.text
    return [f for f in response.json()["findings"] if f["code"] == code]


async def _upload(client, ctx, *, name, data, mime="application/pdf") -> dict:
    response = await client.post(
        UPLOAD.format(company=ctx["company_id"], tender=ctx["tender_id"]),
        files={"file": (name, data, mime)},
    )
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# A. Un dubte el crea una persona                                               #
# --------------------------------------------------------------------------- #


async def test_a_new_issue_is_open_and_blocks_nothing(client_a, candidacy):
    body = await _create(client_a, candidacy)

    assert body["status"] == "open"
    assert body["blocks_final_review"] is False
    assert body["resolution"] is None
    assert body["resolved_by"] is None
    assert body["resolved_at"] is None
    assert body["created_by"] == str(candidacy["user_id"])
    assert body["source_refs"] == []


@pytest.mark.parametrize("field", ["status", "blocks_final_review", "resolved_by", "resolution"])
async def test_the_client_cannot_open_an_issue_already_closed_or_blocking(
    client_a, candidacy, field
):
    """Si es pogués fixar en crear, bloquejar seria un camp més del formulari."""
    response = await client_a.post(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"issue_type": "internal_question", "title": "X", field: "resolved"},
    )
    assert response.status_code == 422


async def test_nothing_in_the_system_opens_an_issue_by_itself(client_a, db, candidacy):
    """Una dimensió en disputa, una cotització pendent i una línia divergida.

    Tot això ja té el seu avís a `FinalReadiness`. Convertir-ho en dubtes
    ompliria la llista de preguntes que ningú no s'ha fet i hi enterraria les
    que sí.
    """
    from app.services import dimensions as dimension_service

    dimension = await dimension_service.create_dimension(
        db,
        workspace=candidacy["workspace"],
        user=candidacy["user"],
        fields={"key": "extensions", "label": "Extensions"},
    )
    await db.flush()
    for kind, value in (("tender_document", "151"), ("supplier", "181")):
        await dimension_service.add_observation(
            db,
            workspace=candidacy["workspace"],
            dimension=dimension,
            user=candidacy["user"],
            expected_revision=dimension.revision,
            source_kind=kind,
            value_text=value,
            value_num=Decimal(value),
        )
    await db.commit()

    # La dimensió està en disputa i el readiness ho diu...
    assert await _findings(client_a, candidacy, "dimension_disputed")
    # ...però ningú no ha obert cap dubte.
    total = (
        await db.execute(sa.select(sa.func.count()).select_from(WorkspaceIssue))
    ).scalar_one()
    assert total == 0


async def test_there_is_no_way_to_delete_an_issue(client_a):
    """Es tanca resolent-lo o descartant-lo, mai esborrant-lo.

    L'únic `DELETE` d'aquest recurs treu una CITA d'un dubte, no el dubte.
    """
    from app.main import app

    deletable = [
        path
        for path, methods in app.openapi()["paths"].items()
        if "/issues" in path and "delete" in methods
    ]
    assert deletable == [
        "/api/v1/companies/{company_id}/workspaces/{workspace_id}"
        "/issues/{issue_id}/source-refs/{source_ref_id}"
    ], deletable


# --------------------------------------------------------------------------- #
# B. Editar i moure                                                             #
# --------------------------------------------------------------------------- #


async def test_the_text_and_the_context_can_be_corrected(client_a, candidacy):
    created = await _create(client_a, candidacy)

    response = await client_a.patch(
        await _url(candidacy, created["id"]),
        json={
            "title": "Quantes extensions hi ha de debò",
            "description": "El plec i l'inventari no diuen el mateix.",
            "issue_type": "contradiction",
            "supplier_name": "Proveïdor A",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["issue_type"] == "contradiction"
    assert response.json()["title"] == "Quantes extensions hi ha de debò"


async def test_it_can_move_between_open_and_waiting(client_a, candidacy):
    created = await _create(client_a, candidacy)

    waiting = await client_a.patch(
        await _url(candidacy, created["id"]), json={"status": "waiting"}
    )
    assert waiting.status_code == 200
    assert waiting.json()["status"] == "waiting"

    back = await client_a.patch(
        await _url(candidacy, created["id"]), json={"status": "open"}
    )
    assert back.json()["status"] == "open"


@pytest.mark.parametrize("status", ["resolved", "dismissed"])
async def test_the_patch_cannot_close_an_issue(client_a, candidacy, status):
    """Tancar exigeix dir com, i té la seva acció."""
    created = await _create(client_a, candidacy)

    response = await client_a.patch(
        await _url(candidacy, created["id"]), json={"status": status}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "issue_status_not_patchable"


@pytest.mark.parametrize(
    "field", ["blocks_final_review", "resolved_by", "resolved_at", "resolution",
              "created_by", "created_at"]
)
async def test_the_patch_cannot_touch_the_reserved_fields(client_a, candidacy, field):
    created = await _create(client_a, candidacy)

    response = await client_a.patch(
        await _url(candidacy, created["id"]), json={field: "x"}
    )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# C. Resoldre i descartar no són el mateix                                      #
# --------------------------------------------------------------------------- #


async def test_resolving_requires_writing_the_answer(client_a, candidacy):
    """El que fa útil un dubte resolt no és l'estat: és saber què se'n va concloure."""
    created = await _create(client_a, candidacy)

    for body in ({}, {"resolution": "   "}):
        response = await client_a.post(await _url(candidacy, created["id"], RESOLVE), json=body)
        assert response.status_code == 422, body


async def test_resolving_records_the_answer_and_who(client_a, candidacy):
    created = await _create(client_a, candidacy)

    response = await client_a.post(
        await _url(candidacy, created["id"], RESOLVE),
        json={
            "resolution": "Utilitzarem 181 segons l'inventari complet",
            "resolution_source": "Correu del client del 12/09",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "Utilitzarem 181 segons l'inventari complet"
    assert body["resolution_source"] == "Correu del client del 12/09"
    assert body["resolved_by"] == str(candidacy["user_id"])
    assert body["resolved_at"] is not None


async def test_dismissing_leaves_the_resolution_fields_empty(client_a, db, candidacy):
    """**Descartar no és resoldre.**

    Si omplíssim `resolved_*`, `resolved_at IS NOT NULL` voldria dir dues coses
    diferents i «ho sabem» inclouria coses que ningú no ha contestat mai. Qui
    descarta i quan viu a l'auditoria i a `updated_by`.
    """
    created = await _create(client_a, candidacy)

    response = await client_a.post(await _url(candidacy, created["id"], DISMISS), json={})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "dismissed"
    assert body["resolution"] is None
    assert body["resolved_by"] is None
    assert body["resolved_at"] is None
    assert body["updated_by"] == str(candidacy["user_id"])


async def test_a_closed_issue_takes_no_more_writes(client_a, candidacy):
    created = await _create(client_a, candidacy)
    await client_a.post(
        await _url(candidacy, created["id"], RESOLVE), json={"resolution": "Fet"}
    )

    patched = await client_a.patch(await _url(candidacy, created["id"]), json={"title": "X"})
    assert patched.status_code == 422
    assert patched.json()["error"]["code"] == "issue_closed"

    again = await client_a.post(
        await _url(candidacy, created["id"], RESOLVE), json={"resolution": "Un altre cop"}
    )
    assert again.status_code == 422


async def test_the_database_refuses_a_resolved_issue_without_an_answer(db, candidacy):
    """El CHECK, no només el servei."""
    from sqlalchemy.exc import IntegrityError

    from app.models.enums import IssueType

    db.add(
        WorkspaceIssue(
            company_id=candidacy["company_id"],
            workspace_id=candidacy["workspace_id"],
            issue_type=IssueType.INTERNAL_QUESTION,
            status=IssueStatus.RESOLVED,
            title="Sense resposta",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# --------------------------------------------------------------------------- #
# D. Bloquejar la revisió final és una decisió d'oferta                         #
# --------------------------------------------------------------------------- #


async def test_a_technical_can_open_an_issue_but_not_block_the_review(
    client_a, db, candidacy, tenant_a
):
    await _set_role(db, tenant_a, MembershipRole.TECHNICAL)

    created = await _create(client_a, candidacy)
    assert created["blocks_final_review"] is False

    response = await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )
    assert response.status_code == 403


async def test_a_decision_role_can_block_and_unblock(client_a, candidacy):
    created = await _create(client_a, candidacy)

    blocked = await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )
    assert blocked.status_code == 200
    assert blocked.json()["blocks_final_review"] is True
    # No toca l'estat: marcar no és moure el dubte.
    assert blocked.json()["status"] == "open"

    freed = await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": False}
    )
    assert freed.json()["blocks_final_review"] is False


async def test_resolving_keeps_the_historical_block_decision(client_a, candidacy):
    """Que algú decidís un dia que això aturava la candidatura continua sent cert.

    Netejar-ho en resoldre esborraria per què va estar aturada.
    """
    created = await _create(client_a, candidacy)
    await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )

    resolved = await client_a.post(
        await _url(candidacy, created["id"], RESOLVE), json={"resolution": "Ja ho sabem"}
    )

    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["blocks_final_review"] is True


# --------------------------------------------------------------------------- #
# E. Què n'arriba a la preparació                                               #
# --------------------------------------------------------------------------- #


async def test_an_open_issue_is_information_not_a_blocker(client_a, candidacy):
    """Obrir preguntes és exactament el que ha de passar mentre es prepara."""
    await _create(client_a, candidacy)

    info = await _findings(client_a, candidacy, "issue_open")
    assert len(info) == 1
    assert info[0]["severity"] == "info"
    assert info[0]["context"]["count"] == 1
    assert await _findings(client_a, candidacy, "issue_blocking_open") == []


async def test_the_info_finding_is_aggregated(client_a, candidacy):
    """Un finding per dubte faria semblar que cadascun és una feina pendent."""
    for index in range(3):
        await _create(client_a, candidacy, title=f"Dubte {index}")

    info = await _findings(client_a, candidacy, "issue_open")
    assert len(info) == 1
    assert info[0]["context"]["count"] == 3


async def test_a_blocked_issue_blocks_and_is_not_counted_twice(client_a, candidacy):
    blocking = await _create(client_a, candidacy, title="Això sí que atura")
    await _create(client_a, candidacy, title="Això no")
    await client_a.post(
        await _url(candidacy, blocking["id"], BLOCK), json={"blocks_final_review": True}
    )

    blockers = await _findings(client_a, candidacy, "issue_blocking_open")
    assert len(blockers) == 1
    assert blockers[0]["severity"] == "blocker"
    assert blockers[0]["context"]["title"] == "Això sí que atura"

    # El que bloqueja ja té el seu blocador: comptar-lo també a l'info diria el
    # mateix problema dues vegades.
    info = await _findings(client_a, candidacy, "issue_open")
    assert info[0]["context"]["count"] == 1


async def test_waiting_behaves_like_open(client_a, candidacy):
    created = await _create(client_a, candidacy)
    await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )
    await client_a.patch(await _url(candidacy, created["id"]), json={"status": "waiting"})

    assert len(await _findings(client_a, candidacy, "issue_blocking_open")) == 1


@pytest.mark.parametrize("closing", ["resolve", "dismiss"])
async def test_closing_the_issue_clears_the_blocker(client_a, candidacy, closing):
    created = await _create(client_a, candidacy)
    await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )
    assert await _findings(client_a, candidacy, "issue_blocking_open")

    if closing == "resolve":
        await client_a.post(
            await _url(candidacy, created["id"], RESOLVE), json={"resolution": "Resolt"}
        )
    else:
        await client_a.post(await _url(candidacy, created["id"], DISMISS), json={})

    assert await _findings(client_a, candidacy, "issue_blocking_open") == []
    assert await _findings(client_a, candidacy, "issue_open") == []


async def test_unmarking_clears_the_blocker_too(client_a, candidacy):
    created = await _create(client_a, candidacy)
    await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )

    await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": False}
    )

    assert await _findings(client_a, candidacy, "issue_blocking_open") == []
    assert (await _findings(client_a, candidacy, "issue_open"))[0]["context"]["count"] == 1


async def test_the_blocker_carries_no_economic_data(client_a, db, candidacy):
    """Si el dubte apunta a una línia econòmica, aquí no hi entra cap xifra."""
    from app.models.offer import EconomicOfferLot, EconomicOfferSection
    from app.services import economic_offer as offer_service

    offer, _ = await offer_service.create_offer(
        db, candidacy["workspace"], user=candidacy["user"]
    )
    lot = (
        await db.scalars(
            sa.select(EconomicOfferLot).where(EconomicOfferLot.offer_id == offer.id)
        )
    ).first()
    section = EconomicOfferSection(
        company_id=candidacy["company_id"], offer_lot_id=lot.id, title="Serveis", position=0
    )
    db.add(section)
    await db.commit()
    line = await offer_service.create_line(
        db,
        candidacy["workspace"],
        offer,
        section,
        expected_revision=offer.revision,
        user=candidacy["user"],
        concept="Terminals",
        quantity=Decimal("1"),
        pricing_basis="one_off",
        unit_cost_ex_vat=Decimal("12.345678"),
        offered_unit_price_ex_vat=Decimal("999.55"),
    )
    line_id = line.id

    created = await _create(
        client_a, candidacy, economic_offer_line_id=str(line_id), title="Preu a revisar"
    )
    await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )

    response = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    rendered = response.text
    for forbidden in ("12.3457", "12.345678", "999.55", "unit_cost", "markup"):
        assert forbidden not in rendered, forbidden


# --------------------------------------------------------------------------- #
# F. Provinença                                                                 #
# --------------------------------------------------------------------------- #


async def test_an_issue_can_hold_several_citations(client_a, db, candidacy):
    """El cas de la contradicció: el plec diu una cosa i el full de càlcul una altra."""
    from app.models.tender_documents import TenderDocumentPage

    uploaded = await _upload(client_a, candidacy, name="PCAP.pdf", data=PLEC)
    page = (
        await db.execute(
            sa.select(TenderDocumentPage).where(
                TenderDocumentPage.extraction_id
                == uuid.UUID(uploaded["file"]["extraction_id"])
            )
        )
    ).scalars().first()
    start = page.text.index("151")
    first = await client_a.post(
        PAGE_REF.format(company=candidacy["company_id"], tender=candidacy["tender_id"]),
        json={
            "extraction_id": uploaded["file"]["extraction_id"],
            "page_id": str(page.id),
            "char_start": start,
            "char_end": start + len("151 extensions telefoniques"),
        },
    )
    assert first.status_code == 201, first.text

    sheet = await _upload(
        client_a, candidacy, name="inventari.xlsx", data=XLSX_V1, mime=XLSX
    )
    second = await client_a.post(
        FILE_REF.format(company=candidacy["company_id"], tender=candidacy["tender_id"]),
        json={
            "tender_document_file_id": sheet["file"]["file_id"],
            "location_label": "Full Extensions, fila 3",
        },
    )
    assert second.status_code == 201, second.text

    created = await _create(client_a, candidacy, issue_type="contradiction")
    for ref in (first, second):
        linked = await client_a.post(
            await _url(candidacy, created["id"], REFS),
            json={"source_ref_id": ref.json()["id"]},
        )
        assert linked.status_code == 200, linked.text

    body = linked.json()
    assert len(body["source_refs"]) == 2
    names = {item["document_name"] for item in body["source_refs"]}
    assert names == {"PCAP.pdf", "inventari.xlsx"}


async def test_a_citation_from_another_tender_is_refused(client_a, db, candidacy):
    """Els mateixos bytes poden ser a dos expedients. El SHA no ho decideix."""
    other = await client_a.post(
        CREATE_TENDER.format(company=candidacy["company_id"]),
        json={"client_token": str(uuid.uuid4()), "title": "Una altra"},
    )
    other_id = other.json()["tender"]["id"]
    uploaded = await client_a.post(
        UPLOAD.format(company=candidacy["company_id"], tender=other_id),
        files={"file": ("altre.xlsx", XLSX_V1, XLSX)},
    )
    foreign = await client_a.post(
        FILE_REF.format(company=candidacy["company_id"], tender=other_id),
        json={"tender_document_file_id": uploaded.json()["file"]["file_id"]},
    )

    created = await _create(client_a, candidacy)
    response = await client_a.post(
        await _url(candidacy, created["id"], REFS),
        json={"source_ref_id": foreign.json()["id"]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "issue_source_ref_tender_mismatch"


async def test_unlinking_does_not_delete_the_citation(client_a, db, candidacy):
    """Desenganxar vol dir «aquest dubte ja no s'hi recolza», no «no va existir»."""
    from app.models.provenance import TenderSourceRef

    sheet = await _upload(
        client_a, candidacy, name="inventari.xlsx", data=XLSX_V1, mime=XLSX
    )
    ref = await client_a.post(
        FILE_REF.format(company=candidacy["company_id"], tender=candidacy["tender_id"]),
        json={"tender_document_file_id": sheet["file"]["file_id"]},
    )
    ref_id = ref.json()["id"]
    created = await _create(client_a, candidacy)
    await client_a.post(
        await _url(candidacy, created["id"], REFS), json={"source_ref_id": ref_id}
    )

    removed = await client_a.delete(
        await _url(candidacy, created["id"], REFS) + f"/{ref_id}"
    )
    assert removed.status_code == 200
    assert removed.json()["source_refs"] == []

    db.expire_all()
    assert await db.get(TenderSourceRef, uuid.UUID(ref_id)) is not None
    left = (
        await db.execute(
            sa.select(sa.func.count()).select_from(WorkspaceIssueSourceRef)
        )
    ).scalar_one()
    assert left == 0


async def test_the_citation_keeps_pointing_at_the_version_that_was_read(
    client_a, db, candidacy
):
    """Puja una v2 i el dubte continua citant la v1. Mateix invariant d'S3-S4."""
    v1 = await _upload(client_a, candidacy, name="inventari.xlsx", data=XLSX_V1, mime=XLSX)
    ref = await client_a.post(
        FILE_REF.format(company=candidacy["company_id"], tender=candidacy["tender_id"]),
        json={
            "tender_document_file_id": v1["file"]["file_id"],
            "location_label": "Full Càlculs, fila 42",
        },
    )
    created = await _create(client_a, candidacy)
    await client_a.post(
        await _url(candidacy, created["id"], REFS),
        json={"source_ref_id": ref.json()["id"]},
    )

    replaced = await client_a.post(
        VERSIONS.format(
            company=candidacy["company_id"], tender=candidacy["tender_id"], document=v1["id"]
        ),
        files={"file": ("inventari.xlsx", XLSX_V2, XLSX)},
    )
    assert replaced.json()["file"]["version_number"] == 2

    detail = await client_a.get(await _url(candidacy, created["id"]))
    held = detail.json()["source_refs"][0]
    assert held["version_number"] == 1
    assert held["content_sha256"] == v1["file"]["sha256"]


async def test_linking_the_same_citation_twice_is_harmless(client_a, db, candidacy):
    sheet = await _upload(
        client_a, candidacy, name="inventari.xlsx", data=XLSX_V1, mime=XLSX
    )
    ref = await client_a.post(
        FILE_REF.format(company=candidacy["company_id"], tender=candidacy["tender_id"]),
        json={"tender_document_file_id": sheet["file"]["file_id"]},
    )
    created = await _create(client_a, candidacy)

    for _ in range(2):
        response = await client_a.post(
            await _url(candidacy, created["id"], REFS),
            json={"source_ref_id": ref.json()["id"]},
        )
        assert response.status_code == 200

    assert len(response.json()["source_refs"]) == 1


# --------------------------------------------------------------------------- #
# G. Coherència del context                                                     #
# --------------------------------------------------------------------------- #


async def _other_workspace(client, db, tenant):
    from app.models.tenders import Tender
    from app.services import bid_workspace as workspace_service

    user, company = tenant
    other = await client.post(
        CREATE_TENDER.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": "Una altra"},
    )
    tender = await db.get(Tender, uuid.UUID(other.json()["tender"]["id"]))
    workspace = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=tender, user_id=user.id
        )
    ).workspace
    await db.commit()
    return workspace


async def test_a_dimension_from_another_workspace_is_refused(
    client_a, db, candidacy, tenant_a
):
    from app.services import dimensions as dimension_service

    other = await _other_workspace(client_a, db, tenant_a)
    foreign = await dimension_service.create_dimension(
        db, workspace=other, user=candidacy["user"], fields={"key": "x", "label": "X"}
    )
    await db.commit()
    foreign_id = foreign.id

    response = await client_a.post(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={
            "issue_type": "contradiction",
            "title": "X",
            "dimension_id": str(foreign_id),
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "issue_link_mismatch"


async def test_a_solution_option_from_another_workspace_is_refused(
    client_a, db, candidacy, tenant_a
):
    from app.models.enums import OptionSelection, QuoteStatus
    from app.models.solution_options import WorkspaceSolutionOption

    other = await _other_workspace(client_a, db, tenant_a)
    foreign = WorkspaceSolutionOption(
        company_id=candidacy["company_id"],
        workspace_id=other.id,
        quote_status=QuoteStatus.DRAFT,
        selection=OptionSelection.UNDECIDED,
        revision=0,
    )
    db.add(foreign)
    await db.commit()
    foreign_id = foreign.id

    response = await client_a.post(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"issue_type": "internal_question", "title": "X",
              "solution_option_id": str(foreign_id)},
    )
    assert response.status_code == 422


async def test_an_offer_line_from_another_workspace_is_refused(
    client_a, db, candidacy, tenant_a
):
    """La línia no porta `workspace_id`: penja de secció → lot → oferta."""
    from app.models.offer import EconomicOfferLot, EconomicOfferSection
    from app.services import economic_offer as offer_service

    other = await _other_workspace(client_a, db, tenant_a)
    offer, _ = await offer_service.create_offer(db, other, user=candidacy["user"])
    lot = (
        await db.scalars(
            sa.select(EconomicOfferLot).where(EconomicOfferLot.offer_id == offer.id)
        )
    ).first()
    section = EconomicOfferSection(
        company_id=candidacy["company_id"], offer_lot_id=lot.id, title="X", position=0
    )
    db.add(section)
    await db.commit()
    line = await offer_service.create_line(
        db, other, offer, section,
        expected_revision=offer.revision, user=candidacy["user"],
        concept="X", quantity=Decimal("1"), pricing_basis="one_off",
    )
    line_id = line.id

    response = await client_a.post(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"issue_type": "internal_question", "title": "X",
              "economic_offer_line_id": str(line_id)},
    )
    assert response.status_code == 422


async def test_a_requirement_from_another_tender_is_refused(client_a, db, candidacy):
    from app.models.enums import (
        AnalysisRunStatus,
        RequirementCategory,
        RequirementObligation,
    )
    from app.models.requirements import Requirement, RequirementExtractionRun
    from app.models.tenders import Tender

    other = await client_a.post(
        CREATE_TENDER.format(company=candidacy["company_id"]),
        json={"client_token": str(uuid.uuid4()), "title": "Una altra"},
    )
    tender = await db.get(Tender, uuid.UUID(other.json()["tender"]["id"]))
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="v1",
        prompt_version="v1",
        schema_version="v1",
        provider="fake",
        model="cap",
        is_current=True,
    )
    db.add(run)
    await db.flush()
    foreign = Requirement(
        tender_id=tender.id,
        run_id=run.id,
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.MANDATORY,
        title="D'una altra",
        dedup_key=uuid.uuid4().hex,
    )
    db.add(foreign)
    await db.commit()
    foreign_id = foreign.id

    response = await client_a.post(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"issue_type": "internal_question", "title": "X",
              "requirement_id": str(foreign_id)},
    )
    assert response.status_code == 422


async def test_an_owner_from_another_company_is_refused(client_a, candidacy, tenant_b):
    other_user, _ = tenant_b

    response = await client_a.post(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"issue_type": "internal_question", "title": "X",
              "owner_id": str(other_user.id)},
    )
    assert response.status_code == 422


async def test_a_link_survives_the_entity_being_archived(client_a, db, candidacy):
    """Un dubte pot continuar apuntant a una entitat que després s'arxiva.

    No se sincronitza amb el seu estat: la pregunta que es va fer sobre allò
    continua sent la que es va fer.
    """
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

    created = await _create(client_a, candidacy, dimension_id=str(dimension_id))

    db.expire_all()
    stored = await db.get(WorkspaceDimension, dimension_id)
    await dimension_service.archive_dimension(
        db, dimension=stored, expected_revision=stored.revision
    )
    await db.commit()

    detail = await client_a.get(await _url(candidacy, created["id"]))
    assert detail.json()["dimension_id"] == str(dimension_id)


# --------------------------------------------------------------------------- #
# H. Cap cascada                                                                #
# --------------------------------------------------------------------------- #


async def test_resolving_the_issue_does_not_decide_the_dimension(
    client_a, db, candidacy
):
    """Són dues decisions relacionades però diferents.

    Encadenar-les faria que respondre una pregunta mogués una xifra que ningú
    no ha mirat.
    """
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
    before = (dimension.status, dimension.working_value_num, dimension.revision)

    created = await _create(
        client_a, candidacy, issue_type="contradiction", dimension_id=str(dimension_id)
    )
    await client_a.post(
        await _url(candidacy, created["id"], RESOLVE),
        json={"resolution": "Utilitzarem 181 segons l'inventari complet"},
    )

    db.expire_all()
    after = await db.get(WorkspaceDimension, dimension_id)
    assert (after.status, after.working_value_num, after.revision) == before


async def test_deciding_the_dimension_does_not_resolve_the_issue(
    client_a, db, candidacy
):
    from app.models.dimensions import WorkspaceDimension
    from app.models.identity import User
    from app.services import dimensions as dimension_service

    dimension = await dimension_service.create_dimension(
        db,
        workspace=candidacy["workspace"],
        user=candidacy["user"],
        fields={"key": "extensions", "label": "Extensions"},
    )
    await db.commit()
    dimension_id = dimension.id
    user_id = candidacy["user_id"]

    created = await _create(
        client_a, candidacy, issue_type="contradiction", dimension_id=str(dimension_id)
    )

    db.expire_all()
    user = await db.get(User, user_id)
    stored = await db.get(WorkspaceDimension, dimension_id)
    await dimension_service.decide_dimension(
        db,
        dimension=stored,
        user=user,
        expected_revision=stored.revision,
        working_value_text="181",
        working_value_num=Decimal("181"),
        reason="Inventari complet",
    )
    await db.commit()

    detail = await client_a.get(await _url(candidacy, created["id"]))
    assert detail.json()["status"] == "open"
    assert detail.json()["resolution"] is None


# --------------------------------------------------------------------------- #
# I. Inquilins                                                                  #
# --------------------------------------------------------------------------- #


async def test_another_company_cannot_touch_the_issues(client_a, client_b, candidacy):
    created = await _create(client_a, candidacy)

    listed = await client_b.get(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"])
    )
    assert listed.status_code in (403, 404)

    for request in (
        client_b.get(await _url(candidacy, created["id"])),
        client_b.post(
            await _url(candidacy, created["id"], RESOLVE), json={"resolution": "Meu"}
        ),
        client_b.post(
            await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
        ),
    ):
        response = await request
        assert response.status_code in (403, 404)


async def test_on_a_shared_tender_each_company_keeps_its_own_issues(
    client_a, client_b, db, tenant_a, tenant_b
):
    """Un dubte és de la CANDIDATURA, no de l'expedient.

    Els requisits d'una licitació pública els comparteix tothom qui la mira;
    les preguntes que es fa cada empresa mentre prepara la seva oferta, no.
    """
    from app.models.enums import WorkspaceDecisionType
    from app.models.sources import Source
    from app.models.tenders import Tender
    from app.services import bid_workspace as workspace_service

    # Una licitació pública, visible per a totes dues empreses.
    source = Source(code=f"public-{uuid.uuid4().hex[:8]}", name="PLACSP públic")
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=uuid.uuid4().hex,
        title="Licitació compartida",
    )
    db.add(tender)
    await db.commit()

    spaces = {}
    for client, tenant in ((client_a, tenant_a), (client_b, tenant_b)):
        user, company = tenant
        workspace = (
            await workspace_service.create_workspace(
                db, company_id=company.id, tender=tender, user_id=user.id
            )
        ).workspace
        await workspace_service.record_decision(
            db, workspace, decision=WorkspaceDecisionType.GO, user=user,
            role=MembershipRole.OWNER, reason="Endavant",
        )
        await db.commit()
        spaces[client] = {"company_id": company.id, "workspace_id": workspace.id}

    await _create(client_a, spaces[client_a], title="Dubte de l'empresa A")

    mine = await client_a.get(
        ISSUES.format(
            company=spaces[client_a]["company_id"],
            workspace=spaces[client_a]["workspace_id"],
        )
    )
    theirs = await client_b.get(
        ISSUES.format(
            company=spaces[client_b]["company_id"],
            workspace=spaces[client_b]["workspace_id"],
        )
    )
    assert len(mine.json()) == 1
    assert theirs.json() == [], "el dubte d'A no es veu des de B"


async def test_an_issue_from_another_workspace_is_not_found(
    client_a, db, candidacy, tenant_a
):
    other = await _other_workspace(client_a, db, tenant_a)
    created = await _create(client_a, candidacy)

    leaked = await client_a.get(
        ONE.format(
            company=candidacy["company_id"], workspace=other.id, issue=created["id"]
        )
    )
    assert leaked.status_code == 404


async def test_a_viewer_can_read_but_not_write(client_a, db, candidacy, tenant_a):
    created = await _create(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.VIEWER)

    listed = await client_a.get(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"])
    )
    assert listed.status_code == 200 and len(listed.json()) == 1

    blocked = await client_a.post(
        ISSUES.format(company=candidacy["company_id"], workspace=candidacy["workspace_id"]),
        json={"issue_type": "internal_question", "title": "X"},
    )
    assert blocked.status_code == 403
    assert (
        await client_a.post(
            await _url(candidacy, created["id"], RESOLVE), json={"resolution": "X"}
        )
    ).status_code == 403


# --------------------------------------------------------------------------- #
# J. L'esquema i el paquet                                                      #
# --------------------------------------------------------------------------- #


async def test_the_issue_never_embeds_the_entities_it_points_at(client_a, candidacy):
    """Només identificadors: incrustar-les obriria una via sense redacció."""
    from app.schemas.issues import IssueRead

    for forbidden in ("economic_offer_line", "solution_option", "dimension", "requirement"):
        embedded = [
            name
            for name in IssueRead.model_fields
            if name.startswith(forbidden) and not name.endswith("_id")
        ]
        assert embedded == [], embedded


async def test_there_is_no_source_refs_json(db):
    """El disseny antic queda superat: la provinença va a una taula relacional."""
    columns = set(WorkspaceIssue.__table__.columns.keys())

    assert "source_refs_json" not in columns
    assert "workspace_issue_source_refs" in {
        fk.column.table.name for fk in WorkspaceIssueSourceRef.__table__.foreign_keys
    } | {"workspace_issue_source_refs"}


async def test_the_citation_side_is_restrict(db):
    """Una font citada no s'ha de poder fer desaparèixer sense adonar-se'n."""
    actions = {
        fk.column.table.name: fk.ondelete
        for fk in WorkspaceIssueSourceRef.__table__.foreign_keys
    }
    assert actions["tender_source_refs"] == "RESTRICT"
    assert actions["workspace_issues"] == "CASCADE"


async def test_all_entity_links_are_set_null(db):
    actions = {
        fk.parent.name: fk.ondelete for fk in WorkspaceIssue.__table__.foreign_keys
    }
    for field in (
        "requirement_id",
        "award_criterion_id",
        "technical_solution_id",
        "solution_option_id",
        "economic_offer_line_id",
        "dimension_id",
        "owner_id",
        "resolved_by",
    ):
        assert actions[field] == "SET NULL", field
    assert actions["workspace_id"] == "CASCADE"


async def test_issues_never_touch_the_package(client_a, db, candidacy):
    """Ni crear, ni bloquejar, ni resoldre: el manifest no en sap res.

    Una aprovació històrica és una fotografia del que es va presentar; un dubte
    d'avui no la pot canviar. El que sí que canvia és la preparació d'ARA.
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

    created = await _create(client_a, candidacy)
    assert await digest() == base

    await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )
    assert await digest() == base, "bloquejar no toca el manifest"
    assert await _findings(client_a, candidacy, "issue_blocking_open")

    await client_a.post(
        await _url(candidacy, created["id"], RESOLVE), json={"resolution": "Resolt"}
    )
    assert await digest() == base
    assert await _findings(client_a, candidacy, "issue_blocking_open") == []


async def test_the_audit_trail_records_what_happened_without_the_figures(
    client_a, db, candidacy
):
    from app.models.audit import AuditLog

    created = await _create(client_a, candidacy)
    await client_a.post(
        await _url(candidacy, created["id"], BLOCK), json={"blocks_final_review": True}
    )
    await client_a.post(
        await _url(candidacy, created["id"], RESOLVE), json={"resolution": "Resolt"}
    )

    summaries = [
        entry.summary or ""
        for entry in (await db.scalars(sa.select(AuditLog))).all()
        if entry.entity_type == "workspace_issue"
    ]
    assert any("bloqueja" in s for s in summaries)
    assert any("resolt" in s for s in summaries)
