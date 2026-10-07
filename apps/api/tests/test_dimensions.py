"""Dimensionament: fonts, discrepàncies i decisions humanes (S3-S6).

El cas que dona sentit a tot: el plec diu 151 extensions i el full de càlcul en
diu 181. El sistema **no tria**. Diu que hi ha dues xifres, ensenya d'on surt
cadascuna, i espera que una persona fixi la que farà servir la candidatura
explicant per què.

Dues frases que aquests tests no deixen tornar enrere:

    observació  ≠  decisió
    dues fonts diferents  ≠  error resolt automàticament

I una tercera que és fàcil de perdre: **una decisió no es reobre sola**. Les
observacions que van motivar la discrepància continuen a l'historial per sempre,
i si es tornessin a comptar, la dimensió quedaria disputada just després de
decidir-la.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.dimensions import WorkspaceDimension, WorkspaceDimensionObservation
from app.models.enums import DimensionStatus
from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

CREATE_TENDER = "/api/v1/companies/{company}/tenders"
UPLOAD = "/api/v1/companies/{company}/tenders/{tender}/documents"
VERSIONS = UPLOAD + "/{document}/versions"
FILE_REF = "/api/v1/companies/{company}/tenders/{tender}/source-refs/document-file"
PAGE_REF = "/api/v1/companies/{company}/tenders/{tender}/source-refs/extracted-page"
DIMENSIONS = "/api/v1/companies/{company}/workspaces/{workspace}/dimensions"
ONE = DIMENSIONS + "/{dimension}"
OBSERVATIONS = ONE + "/observations"
DECIDE = ONE + "/decide"
ARCHIVE = ONE + "/archive"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"

PLEC = build_pdf(["El servei actual compta amb 151 extensions telefoniques. " * 4])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_V1 = b"PK\x03\x04" + b"\x01" * 64
XLSX_V2 = b"PK\x03\x04" + b"\x02" * 64


@pytest.fixture
async def candidacy(db, tenant_a, client_a):
    """Una candidatura oberta sobre una licitació manual pròpia."""
    from app.models.enums import MembershipRole, WorkspaceDecisionType
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


def _instant(value: str) -> datetime:
    """La mateixa data amb `Z` i sense ha de comparar igual."""
    return datetime.fromisoformat(value.replace("Z", "")).replace(tzinfo=None)


async def _create(client, ctx, **overrides) -> dict:
    body = {"key": "extensions", "label": "Extensions telefòniques", "unit": "unitats"}
    body.update(overrides)
    response = await client.post(
        DIMENSIONS.format(company=ctx["company_id"], workspace=ctx["workspace_id"]),
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _observe(client, ctx, dimension, *, revision, value, num=None, kind="internal", **extra):
    body = {
        "expected_revision": revision,
        "source_kind": kind,
        "value_text": value,
        **extra,
    }
    if num is not None:
        body["value_num"] = num
    return await client.post(
        OBSERVATIONS.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], dimension=dimension
        ),
        json=body,
    )


async def _decide(client, ctx, dimension, *, revision, text, num=None, reason="Perquè sí"):
    body = {
        "expected_revision": revision,
        "working_value_text": text,
        "reason": reason,
    }
    if num is not None:
        body["working_value_num"] = num
    return await client.post(
        DECIDE.format(
            company=ctx["company_id"], workspace=ctx["workspace_id"], dimension=dimension
        ),
        json=body,
    )


# --------------------------------------------------------------------------- #
# A. Crear no és decidir                                                        #
# --------------------------------------------------------------------------- #


async def test_a_new_dimension_is_undecided_and_empty(client_a, candidacy):
    """Crear el concepte no fixa cap número, encara que qui la crea el sàpiga."""
    body = await _create(client_a, candidacy)

    assert body["status"] == "undecided"
    assert body["working_value_text"] is None
    assert body["working_value_num"] is None
    assert body["decision_reason"] is None
    assert body["revision"] == 0
    assert body["observations"] == []


@pytest.mark.parametrize(
    "field", ["status", "working_value_text", "working_value_num", "decision_reason", "revision"]
)
async def test_the_client_cannot_decide_through_the_back_door(client_a, candidacy, field):
    """Si es pogués decidir amb un PATCH, el motiu seria esquivable."""
    created = await _create(client_a, candidacy)

    response = await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": 0, field: "decided"},
    )
    assert response.status_code == 422


async def test_two_dimensions_with_the_same_key_and_scope_collide(client_a, candidacy):
    await _create(client_a, candidacy, scope_label="Policia Local")

    response = await client_a.post(
        DIMENSIONS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        ),
        json={"key": "extensions", "label": "Un altre", "scope_label": "Policia Local"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dimension_duplicate_key"


async def test_two_dimensions_without_scope_also_collide(client_a, candidacy):
    """El `UNIQUE` no ho garanteix sol: a PostgreSQL dos NULL són distints.

    El constraint es queda tal com està —tocar-lo en silenci seria canviar
    l'esquema congelat— i el servei ho complementa.
    """
    await _create(client_a, candidacy)

    response = await client_a.post(
        DIMENSIONS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        ),
        json={"key": "extensions", "label": "Un altre"},
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------- #
# B. El cas real: 151 contra 181                                                #
# --------------------------------------------------------------------------- #


async def test_two_sources_with_different_numbers_open_a_dispute(client_a, candidacy):
    """El criteri d'acceptació de la fase.

    Ningú no ha dit que hi hagi un problema: el sistema ho ha vist perquè hi ha
    dos números i no coincideixen. Això és tot el que automatitza.
    """
    created = await _create(client_a, candidacy)

    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151",
        kind="tender_document",
    )
    assert first.status_code == 201
    assert first.json()["status"] == "undecided", "una sola font no es cap discrepancia"

    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181", num="181", kind="supplier",
    )
    assert second.status_code == 201
    assert second.json()["status"] == "disputed"
    assert len(second.json()["observations"]) == 2


async def test_the_same_number_twice_is_not_a_dispute(client_a, candidacy):
    """Dues fonts que diuen el mateix es confirmen, no es contradiuen."""
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="181", num="181"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181 extensions", num="181",
    )

    assert second.json()["status"] == "undecided"


async def test_text_without_a_number_is_never_a_dispute(client_a, candidacy):
    """El sistema NO decideix si «cent vuitanta-una» és 181.

    No hi ha cap parseig difús ni cap model: si ningú no ha dit quin número és,
    es mostra a la persona i prou.
    """
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="cent cinquanta-una"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="cent vuitanta-una",
    )

    assert second.json()["status"] == "undecided"
    assert all(item["value_num"] is None for item in second.json()["observations"])


async def test_decimals_are_compared_as_decimals(client_a, candidacy):
    """`Numeric`, no `Float`: 0,1 + 0,2 no ha de semblar diferent de 0,3."""
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="1,5", num="1.5000"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="1,5", num="1.5",
    )

    assert second.json()["status"] == "undecided", "1.5000 i 1.5 son el mateix numero"


# --------------------------------------------------------------------------- #
# C. Decidir                                                                    #
# --------------------------------------------------------------------------- #


async def test_deciding_requires_saying_why(client_a, candidacy):
    """El que fa útil una decisió no és el número: és el per què."""
    created = await _create(client_a, candidacy)

    response = await client_a.post(
        DECIDE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": 0, "working_value_text": "181", "reason": "   "},
    )
    assert response.status_code == 422


async def test_deciding_records_the_value_the_reason_and_who(client_a, db, candidacy):
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181", num="181",
    )
    assert second.json()["status"] == "disputed"

    decided = await _decide(
        client_a, candidacy, created["id"], revision=second.json()["revision"],
        text="181", num="181", reason="Valor de treball segons inventari complet",
    )

    assert decided.status_code == 200
    body = decided.json()
    assert body["status"] == "decided"
    assert Decimal(body["working_value_num"]) == Decimal("181")
    assert body["decision_reason"] == "Valor de treball segons inventari complet"
    assert body["decided_by"] == str(candidacy["user"].id)
    assert body["decided_at"] is not None
    # Les dues observacions continuen: son el que explica la decisio.
    assert {item["value_text"] for item in body["observations"]} == {"151", "181"}


async def test_deciding_without_any_dispute_is_allowed(client_a, candidacy):
    """Una sola font fiable pot ser prou. El que no pot és decidir la màquina."""
    created = await _create(client_a, candidacy)
    observed = await _observe(
        client_a, candidacy, created["id"], revision=0, value="26 seus", num="26"
    )
    assert observed.json()["status"] == "undecided"

    decided = await _decide(
        client_a, candidacy, created["id"], revision=observed.json()["revision"],
        text="26", num="26", reason="Inventari verificat amb el client",
    )
    assert decided.json()["status"] == "decided"


async def test_a_decision_does_not_reopen_itself(client_a, candidacy):
    """Les observacions que la van motivar no la poden desfer.

    Si es tornessin a comptar, la dimensió quedaria disputada just després de
    resoldre-la, i l'avís no serviria per a res.
    """
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181", num="181",
    )
    decided = await _decide(
        client_a, candidacy, created["id"], revision=second.json()["revision"],
        text="181", num="181", reason="Inventari complet",
    )

    # Rellegir-la no la torna a disputar.
    listed = await client_a.get(
        DIMENSIONS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    assert listed.json()[0]["status"] == "decided"
    assert decided.json()["status"] == "decided"


async def test_new_evidence_after_a_decision_reopens_it(client_a, candidacy):
    """Evidència NOVA sí. Això és el que separa «resolt» de «tapat»."""
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181", num="181",
    )
    decided = await _decide(
        client_a, candidacy, created["id"], revision=second.json()["revision"],
        text="181", num="181", reason="Inventari complet",
    )

    third = await _observe(
        client_a, candidacy, created["id"], revision=decided.json()["revision"],
        value="204", num="204", kind="supplier",
    )

    assert third.json()["status"] == "disputed"
    assert len(third.json()["observations"]) == 3


async def test_new_evidence_that_agrees_does_not_reopen_it(client_a, candidacy):
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181", num="181",
    )
    decided = await _decide(
        client_a, candidacy, created["id"], revision=second.json()["revision"],
        text="181", num="181", reason="Inventari complet",
    )

    third = await _observe(
        client_a, candidacy, created["id"], revision=decided.json()["revision"],
        value="181 confirmades", num="181", kind="supplier",
    )
    assert third.json()["status"] == "decided"


# --------------------------------------------------------------------------- #
# D. Append-only i arxivat                                                      #
# --------------------------------------------------------------------------- #


async def test_an_observation_cannot_be_edited_or_deleted(client_a, candidacy):
    """No és cap oblit: diu què deia una font el dia que algú ho va llegir."""
    from app.main import app

    # Nomes les observacions d'una dimensio: les del coneixement de l'empresa
    # (`/knowledge/observations`) son una altra cosa i si que es revisen.
    paths = [p for p in app.openapi()["paths"] if "/dimensions/" in p and "observations" in p]
    assert paths, "la ruta de les observacions d'una dimensio ha d'existir"
    methods = {
        method
        for path in paths
        for method in app.openapi()["paths"][path]
    }
    assert methods == {"post"}


async def test_archiving_keeps_the_observations(client_a, db, candidacy):
    created = await _create(client_a, candidacy)
    observed = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )

    archived = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": observed.json()["revision"]},
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None

    left = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(WorkspaceDimensionObservation)
            .where(WorkspaceDimensionObservation.dimension_id == uuid.UUID(created["id"]))
        )
    ).scalar_one()
    assert left == 1


async def test_an_archived_dimension_leaves_the_active_list(client_a, candidacy):
    created = await _create(client_a, candidacy)
    await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": 0},
    )

    listed = await client_a.get(
        DIMENSIONS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    assert listed.json() == []

    with_archived = await client_a.get(
        DIMENSIONS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
        + "?include_archived=true"
    )
    assert len(with_archived.json()) == 1


# --------------------------------------------------------------------------- #
# E. El testimoni de CAS                                                        #
# --------------------------------------------------------------------------- #


async def test_a_stale_revision_is_a_conflict(client_a, candidacy):
    """Dues persones hi escriuen alhora: qui perd s'ha d'assabentar."""
    created = await _create(client_a, candidacy)
    await _observe(client_a, candidacy, created["id"], revision=0, value="151", num="151")

    late = await _observe(
        client_a, candidacy, created["id"], revision=0, value="181", num="181"
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "dimension_revision_conflict"


async def test_a_stale_decision_does_not_overwrite(client_a, candidacy):
    created = await _create(client_a, candidacy)
    await _observe(client_a, candidacy, created["id"], revision=0, value="151", num="151")

    response = await _decide(
        client_a, candidacy, created["id"], revision=0, text="151", reason="Tard"
    )
    assert response.status_code == 409


async def test_every_write_moves_the_token(client_a, candidacy):
    created = await _create(client_a, candidacy)
    assert created["revision"] == 0

    observed = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )
    assert observed.json()["revision"] == 1

    patched = await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": 1, "label": "Extensions, totes les seus"},
    )
    assert patched.json()["revision"] == 2

    decided = await _decide(
        client_a, candidacy, created["id"], revision=2, text="151", reason="Una font"
    )
    assert decided.json()["revision"] == 3


async def test_a_patch_that_changes_nothing_does_not_move_the_token(client_a, candidacy):
    created = await _create(client_a, candidacy)

    response = await client_a.patch(
        ONE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": 0, "label": created["label"]},
    )
    assert response.status_code == 200
    assert response.json()["revision"] == 0


# --------------------------------------------------------------------------- #
# F. D'on surt cada xifra                                                       #
# --------------------------------------------------------------------------- #


async def _upload(client, company_id, tender_id, *, name, data, mime="application/pdf"):
    response = await client.post(
        UPLOAD.format(company=company_id, tender=tender_id),
        files={"file": (name, data, mime)},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_an_observation_can_point_at_the_page_it_was_read_from(
    client_a, db, candidacy
):
    """La cita no es copia: s'hi apunta.

    L'observació no guarda ni la pàgina ni el text ni el SHA. Si en guardés una
    còpia, el dia que el document canviés tindríem dues versions del mateix fet
    i cap manera de saber quina era certa.
    """
    from app.models.tender_documents import TenderDocumentPage

    company_id, tender_id = candidacy["company_id"], candidacy["tender_id"]
    uploaded = await _upload(client_a, company_id, tender_id, name="PCAP.pdf", data=PLEC)
    page = (
        await db.execute(
            sa.select(TenderDocumentPage).where(
                TenderDocumentPage.extraction_id
                == uuid.UUID(uploaded["file"]["extraction_id"])
            )
        )
    ).scalars().first()
    start = page.text.index("151")

    cited = await client_a.post(
        PAGE_REF.format(company=company_id, tender=tender_id),
        json={
            "extraction_id": uploaded["file"]["extraction_id"],
            "page_id": str(page.id),
            "char_start": start,
            "char_end": start + len("151 extensions telefoniques"),
        },
    )
    assert cited.status_code == 201, cited.text

    created = await _create(client_a, candidacy)
    observed = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151",
        kind="tender_document", source_ref_id=cited.json()["id"],
    )

    assert observed.status_code == 201, observed.text
    ref = observed.json()["observations"][0]["source_ref"]
    assert ref["document_name"] == "PCAP.pdf"
    assert ref["page_number"] == 1
    assert "151 extensions" in ref["quote"]
    assert ref["content_sha256"] == uploaded["file"]["sha256"]


async def test_the_citation_keeps_pointing_at_the_version_that_was_read(
    client_a, db, candidacy
):
    """Puja una v2 i la font continua dient que la v1 deia 151.

    És el punt de tot plegat: si la cita seguís la versió vigent, l'observació
    passaria a afirmar una cosa que aquella font no va dir mai.
    """
    company_id, tender_id = candidacy["company_id"], candidacy["tender_id"]
    v1 = await _upload(
        client_a, company_id, tender_id, name="inventari.xlsx", data=XLSX_V1, mime=XLSX
    )
    cited = await client_a.post(
        FILE_REF.format(company=company_id, tender=tender_id),
        json={
            "tender_document_file_id": v1["file"]["file_id"],
            "location_label": "Full Extensions, fila 3",
        },
    )
    assert cited.status_code == 201, cited.text

    created = await _create(client_a, candidacy)
    await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151",
        kind="supplier", source_ref_id=cited.json()["id"],
    )

    replaced = await client_a.post(
        VERSIONS.format(company=company_id, tender=tender_id, document=v1["id"]),
        files={"file": ("inventari.xlsx", XLSX_V2, XLSX)},
    )
    assert replaced.status_code in (200, 201), replaced.text
    v2 = replaced.json()
    assert v2["file"]["version_number"] == 2

    listed = await client_a.get(
        DIMENSIONS.format(company=company_id, workspace=candidacy["workspace_id"])
    )
    ref = listed.json()[0]["observations"][0]["source_ref"]
    assert ref["version_number"] == 1
    assert ref["content_sha256"] == v1["file"]["sha256"]
    assert ref["content_sha256"] != v2["file"]["sha256"]


async def test_a_citation_from_another_tender_is_refused(client_a, db, candidacy):
    """Els mateixos bytes poden ser de dues licitacions. El SHA no ho decideix."""
    company_id = candidacy["company_id"]
    other = await client_a.post(
        CREATE_TENDER.format(company=company_id),
        json={"client_token": str(uuid.uuid4()), "title": "Una altra cosa"},
    )
    other_id = other.json()["tender"]["id"]
    uploaded = await _upload(
        client_a, company_id, other_id, name="altre.xlsx", data=XLSX_V1, mime=XLSX
    )
    cited = await client_a.post(
        FILE_REF.format(company=company_id, tender=other_id),
        json={"tender_document_file_id": uploaded["file"]["file_id"]},
    )

    created = await _create(client_a, candidacy)
    response = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151",
        source_ref_id=cited.json()["id"],
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "source_ref_tender_mismatch"


async def test_an_invented_citation_is_refused(client_a, candidacy):
    created = await _create(client_a, candidacy)
    response = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151",
        source_ref_id=str(uuid.uuid4()),
    )
    assert response.status_code == 404


async def test_a_citation_of_a_removed_document_is_refused(client_a, db, candidacy):
    """Una observacio nova es un input nou: mai sobre un plec retirat de la font (3B-1).

    La cita es va fer mentre el document era vigent i es conserva; el que es
    refusa es fer-la servir de nou.
    """
    from datetime import UTC

    from app.models.tenders import TenderDocument

    company_id, tender_id = candidacy["company_id"], candidacy["tender_id"]
    uploaded = await _upload(
        client_a, company_id, tender_id, name="inventari.xlsx", data=XLSX_V1, mime=XLSX
    )
    cited = await client_a.post(
        FILE_REF.format(company=company_id, tender=tender_id),
        json={"tender_document_file_id": uploaded["file"]["file_id"]},
    )
    assert cited.status_code == 201, cited.text
    document = await db.get(TenderDocument, uuid.UUID(uploaded["id"]))
    document.removed_at = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    await db.commit()

    created = await _create(client_a, candidacy)
    response = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151",
        kind="supplier", source_ref_id=cited.json()["id"],
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "source_ref_document_removed"
    listed = await client_a.get(
        DIMENSIONS.format(company=company_id, workspace=candidacy["workspace_id"])
    )
    assert listed.json()[0]["observations"] == []


# --------------------------------------------------------------------------- #
# G. Una empresa no veu el dimensionament d'una altra                           #
# --------------------------------------------------------------------------- #


async def test_another_company_cannot_read_the_dimensions(client_b, candidacy):
    listed = await client_b.get(
        DIMENSIONS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    assert listed.status_code in (403, 404)


async def test_another_company_cannot_decide_for_you(client_a, client_b, candidacy):
    created = await _create(client_a, candidacy)

    response = await client_b.post(
        DECIDE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": 0, "working_value_text": "1", "reason": "meu"},
    )
    assert response.status_code in (403, 404)


async def test_a_dimension_from_another_workspace_is_not_found(
    client_a, db, candidacy, tenant_a
):
    """Mateixa empresa, una altra candidatura: tampoc.

    Un id no és cap autorització; ha de penjar de la candidatura de la URL.
    """
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
    response = await client_a.get(
        DIMENSIONS.format(company=company.id, workspace=other_workspace.id)
    )
    assert response.status_code == 200
    assert response.json() == []

    leaked = await client_a.post(
        DECIDE.format(
            company=company.id, workspace=other_workspace.id, dimension=created["id"]
        ),
        json={"expected_revision": 0, "working_value_text": "1", "reason": "x"},
    )
    assert leaked.status_code == 404


async def _set_role(db, tenant, role) -> None:
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


async def test_a_viewer_can_read_the_dimensions(client_a, db, tenant_a, candidacy):
    """Qui mira la candidatura ha de poder veure d'on surt cada xifra.

    Amagar-li el dimensionament seria amagar-li justament la part que explica
    per què l'oferta diu el que diu.
    """
    from app.models.enums import MembershipRole

    await _create(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.VIEWER)

    listed = await client_a.get(
        DIMENSIONS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1


async def test_a_viewer_cannot_decide(client_a, db, tenant_a, candidacy):
    """Mirar no és decidir. La decisió porta un nom al costat."""
    from app.models.enums import MembershipRole

    created = await _create(client_a, candidacy)
    await _set_role(db, tenant_a, MembershipRole.VIEWER)

    decided = await _decide(
        client_a, candidacy, created["id"], revision=0, text="181", reason="jo ho dic"
    )
    assert decided.status_code == 404

    observed = await _observe(
        client_a, candidacy, created["id"], revision=0, value="181", num="181"
    )
    assert observed.status_code == 404


# --------------------------------------------------------------------------- #
# H. Què n'arriba a la preparació                                               #
# --------------------------------------------------------------------------- #


async def test_a_dispute_becomes_a_warning_not_a_blocker(client_a, candidacy):
    """Avisa. No impedeix res.

    Bloquejar per una discrepància seria dir que el sistema sap que hi ha un
    error, i no ho sap: sap que dues fonts no diuen el mateix.
    """
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )
    await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181", num="181",
    )

    readiness = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    assert readiness.status_code == 200, readiness.text
    findings = [
        f for f in readiness.json()["findings"] if f["code"] == "dimension_disputed"
    ]
    assert len(findings) == 1
    assert findings[0]["severity"] == "warning"
    assert findings[0]["context"]["key"] == "extensions"


async def test_an_undecided_dimension_says_nothing(client_a, candidacy):
    """No decidida encara no vol dir malament. Queixar-se'n seria soroll."""
    await _create(client_a, candidacy)

    readiness = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    codes = [f["code"] for f in readiness.json()["findings"]]
    assert "dimension_disputed" not in codes
    assert "dimension_undecided_in_use" not in codes


async def test_deciding_clears_the_warning(client_a, candidacy):
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181", num="181",
    )
    await _decide(
        client_a, candidacy, created["id"], revision=second.json()["revision"],
        text="181", num="181", reason="Inventari complet",
    )

    readiness = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    codes = [f["code"] for f in readiness.json()["findings"]]
    assert "dimension_disputed" not in codes


async def test_archiving_a_disputed_dimension_clears_the_warning(client_a, candidacy):
    created = await _create(client_a, candidacy)
    first = await _observe(
        client_a, candidacy, created["id"], revision=0, value="151", num="151"
    )
    second = await _observe(
        client_a, candidacy, created["id"], revision=first.json()["revision"],
        value="181", num="181",
    )
    await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": second.json()["revision"]},
    )

    readiness = await client_a.get(
        READINESS.format(
            company=candidacy["company_id"], workspace=candidacy["workspace_id"]
        )
    )
    codes = [f["code"] for f in readiness.json()["findings"]]
    assert "dimension_disputed" not in codes


# --------------------------------------------------------------------------- #
# I. L'esquema                                                                  #
# --------------------------------------------------------------------------- #


async def test_quantities_are_decimal_and_never_float(db):
    """`Float` no sap representar 0,1. Aquestes xifres acaben en una oferta."""
    for table, column in (
        (WorkspaceDimension, "working_value_num"),
        (WorkspaceDimensionObservation, "value_num"),
    ):
        kind = table.__table__.columns[column].type
        assert isinstance(kind, sa.Numeric) and not isinstance(kind, sa.Float)
        assert (kind.precision, kind.scale) == (18, 4)


async def test_an_observation_has_no_updated_at_and_no_archived_at(db):
    """Append-only també a l'esquema: no hi ha on desar-hi un canvi."""
    columns = set(WorkspaceDimensionObservation.__table__.columns.keys())
    assert "updated_at" not in columns
    assert "archived_at" not in columns


async def test_deleting_a_dimension_does_not_leave_loose_observations(db):
    dimension_fk = next(
        fk
        for fk in WorkspaceDimensionObservation.__table__.foreign_keys
        if fk.column.table.name == "workspace_dimensions"
    )
    assert dimension_fk.ondelete == "CASCADE"


async def test_losing_a_citation_does_not_lose_what_the_source_said(db):
    """`SET NULL`, no `RESTRICT`: el valor segueix llegible sense la cita."""
    ref_fk = next(
        fk
        for fk in WorkspaceDimensionObservation.__table__.foreign_keys
        if fk.column.table.name == "tender_source_refs"
    )
    assert ref_fk.ondelete == "SET NULL"


async def test_the_database_itself_refuses_a_decision_without_a_reason(db, candidacy):
    """El CHECK, no només el servei.

    Si l'única guarda fos el codi de l'endpoint, un script de manteniment o una
    migració futura podria deixar una decisió sense explicar, i ja no hi hauria
    manera de saber per què aquella candidatura va fer servir aquella xifra.
    """
    from sqlalchemy.exc import IntegrityError

    db.add(
        WorkspaceDimension(
            company_id=candidacy["company_id"],
            workspace_id=candidacy["workspace_id"],
            key="sense_motiu",
            label="Sense motiu",
            status=DimensionStatus.DECIDED,
            working_value_text="181",
            revision=0,
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


# --------------------------------------------------------------------------- #
# J. Escriure sobre una dimensió arxivada                                       #
# --------------------------------------------------------------------------- #


async def test_an_archived_dimension_takes_no_more_writes(client_a, candidacy):
    created = await _create(client_a, candidacy)
    archived = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": 0},
    )
    revision = archived.json()["revision"]

    observed = await _observe(
        client_a, candidacy, created["id"], revision=revision, value="151", num="151"
    )
    assert observed.status_code == 409
    assert observed.json()["error"]["code"] == "dimension_archived"

    decided = await _decide(
        client_a, candidacy, created["id"], revision=revision, text="151", reason="x"
    )
    assert decided.status_code == 409


async def test_archiving_twice_is_harmless(client_a, candidacy):
    created = await _create(client_a, candidacy)
    first = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": 0},
    )
    second = await client_a.post(
        ARCHIVE.format(
            company=candidacy["company_id"],
            workspace=candidacy["workspace_id"],
            dimension=created["id"],
        ),
        json={"expected_revision": first.json()["revision"]},
    )
    assert second.status_code == 200
    # Es compara l'instant i no la cadena: sobre SQLite el `timestamptz` torna
    # sense zona i la segona lectura el serialitza sense la `Z`. El que ha de
    # ser igual es el moment, no com s'escriu.
    assert _instant(second.json()["archived_at"]) == _instant(
        first.json()["archived_at"]
    )


# --------------------------------------------------------------------------- #
# K. El recorregut sencer                                                       #
# --------------------------------------------------------------------------- #


async def test_the_whole_walk_from_two_numbers_to_one_decision(client_a, candidacy):
    """El plec diu 151, l'inventari diu 181, i algú decideix 181 dient per què.

    Aquest és el criteri d'acceptació de la fase, de punta a punta i pels
    mateixos endpoints que fa servir la interfície. El que ha de sobreviure a
    qualsevol refactor és la seqüència: **el sistema veu la contradicció, la
    persona la resol, i l'historial conserva totes dues xifres.**
    """
    company_id, workspace_id = candidacy["company_id"], candidacy["workspace_id"]

    created = await _create(client_a, candidacy, key="extensions", unit="unitats")
    assert created["status"] == "undecided"

    plec = await _observe(
        client_a, candidacy, created["id"], revision=created["revision"],
        value="151 extensions", num="151", kind="tender_document",
        note="Plec tècnic, apartat 2.1",
    )
    assert plec.json()["status"] == "undecided"

    inventari = await _observe(
        client_a, candidacy, created["id"], revision=plec.json()["revision"],
        value="181 extensions", num="181", kind="supplier",
        note="Inventari complet facilitat pel client",
    )
    assert inventari.json()["status"] == "disputed"

    warned = await client_a.get(
        READINESS.format(company=company_id, workspace=workspace_id)
    )
    assert any(
        f["code"] == "dimension_disputed" and f["severity"] == "warning"
        for f in warned.json()["findings"]
    )

    decided = await _decide(
        client_a, candidacy, created["id"], revision=inventari.json()["revision"],
        text="181", num="181",
        reason="Valor de treball segons inventari complet",
    )

    body = decided.json()
    assert body["status"] == "decided"
    assert Decimal(body["working_value_num"]) == Decimal("181")
    assert body["decision_reason"] == "Valor de treball segons inventari complet"

    # Les dues xifres continuen. La que es va descartar també: sense ella, el
    # motiu de la decisió no s'entendria d'aquí a sis mesos.
    values = {item["value_text"] for item in body["observations"]}
    assert values == {"151 extensions", "181 extensions"}

    calm = await client_a.get(READINESS.format(company=company_id, workspace=workspace_id))
    assert not any(f["code"] == "dimension_disputed" for f in calm.json()["findings"])


async def _wait_until_blocked(session, *, timeout: float = 5.0) -> None:
    """Espera que alguna altra sessio quedi encallada en un bloqueig de fila."""
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
async def test_two_editors_at_once_and_only_one_wins(db, session_factory, candidacy, tenant_a):
    """La cursa de debò, amb dues transaccions reals contra PostgreSQL.

    És el que cap test contra SQLite no pot demostrar. Amb un `SELECT` i
    després un `UPDATE`, totes dues llegirien la revisió 0 i totes dues
    escriurien: la segona es menjaria la feina de la primera sense que ningú
    se n'assabentés. Amb el compare-and-swap, la segona espera al bloqueig de
    la fila i, quan hi arriba, la condició ja no es compleix.
    """
    import asyncio

    from app.services import dimensions as service

    user, _company = tenant_a
    workspace = candidacy["workspace"]
    created = await service.create_dimension(
        db, workspace=workspace, user=user, fields={"key": "cursa", "label": "Cursa"}
    )
    await db.commit()
    dimension_id = created.id

    # Sense sincronitzar-les, s'executarien en sèrie i això no provaria res.
    primera_ha_escrit = asyncio.Event()
    segona_ho_intenta = asyncio.Event()

    async def guanyadora() -> str:
        async with session_factory() as session, session.begin():
            candidata = await session.get(service.WorkspaceDimension, dimension_id)
            await service.decide_dimension(
                session,
                dimension=candidata,
                user=user,
                expected_revision=0,
                working_value_text="181",
                working_value_num=None,
                reason="La primera",
            )
            primera_ha_escrit.set()
            # No n'hi ha prou amb saber que l'altra ho ha intentat: cal saber
            # que ja esta ESPERANT el bloqueig d'aquesta fila. Si no, la seva
            # lectura podria arribar despres d'aquest commit, i llavors fins i
            # tot un compare+commit ingenu se'n sortiria per pura sort i el
            # test deixaria de detectar-lo.
            await segona_ho_intenta.wait()
            await _wait_until_blocked(session)
        return "guanyat"

    async def perdedora() -> str:
        await primera_ha_escrit.wait()
        async with session_factory() as session:
            try:
                async with session.begin():
                    candidata = await session.get(service.WorkspaceDimension, dimension_id)
                    # L'avís va ABANS de la sentència que pot quedar bloquejada.
                    segona_ho_intenta.set()
                    await service.decide_dimension(
                        session,
                        dimension=candidata,
                        user=user,
                        expected_revision=0,
                        working_value_text="151",
                        working_value_num=None,
                        reason="La segona",
                    )
            except service.DimensionRevisionConflictError:
                return "conflicte"
        return "guanyat"

    resultats = await asyncio.gather(guanyadora(), perdedora())
    assert sorted(resultats) == ["conflicte", "guanyat"]

    db.expire_all()
    final = await db.get(service.WorkspaceDimension, dimension_id)
    assert final.revision == 1, "nomes una escriptura ha de moure el testimoni"
    assert final.working_value_text == "181"
    assert final.decision_reason == "La primera"
