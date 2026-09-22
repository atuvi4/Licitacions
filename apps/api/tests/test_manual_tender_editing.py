"""Corregir una licitacio oberta a ma (S3-S1b).

El PATCH no porta cap mecanisme propi: aplica els camps i deixa que
`tender_lifecycle.record_revision` —la mateixa porta que fa servir la ingesta—
decideixi si hi ha revisio. D'aqui surten de franc la idempotencia del no-op i
la distincio material/descriptiu, que ja viuen al backend.

El test que mes importa es el de **C-12**: tenir un paquet congelat o aprovat
NO pot convertir l'aprovacio en un pany. Editar continua permes; el que passa
es que el paquet queda obsolet, exactament com quan l'organ publica un canvi.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Buyer, Tender
from app.services import manual_tender
from tests.test_submission_package import (
    add_extraction_run,
    approve,
    build_ready_candidacy,
    freeze,
)

pytestmark = pytest.mark.asyncio

CREATE = "/api/v1/companies/{company}/tenders"
PATCH = "/api/v1/companies/{company}/tenders/{tender}"


async def _create(client, company, **extra) -> dict:
    body = {"client_token": str(uuid.uuid4()), "title": "Serveis de comunicacions"}
    body.update(extra)
    response = await client.post(CREATE.format(company=company.id), json=body)
    assert response.status_code == 201, response.text
    return response.json()["tender"]


async def _patch(client, company, tender_id, **body) -> tuple[int, dict]:
    response = await client.patch(
        PATCH.format(company=company.id, tender=tender_id), json=body
    )
    return response.status_code, response.json()


async def _current_revision(session, tender_id) -> int:
    """El numero de revisio que el client hauria llegit."""
    return await manual_tender.current_revision_number(session, tender_id)


async def _revisions(session, tender_id) -> list[TenderRevision]:
    return list(
        (
            await session.execute(
                sa.select(TenderRevision)
                .where(TenderRevision.tender_id == tender_id)
                .order_by(TenderRevision.revision_number)
            )
        ).scalars().all()
    )


# --------------------------------------------------------------------------- #
# A. Una licitacio manual neix amb historia                                     #
# --------------------------------------------------------------------------- #


async def test_a_manual_tender_is_born_with_revision_one(client_a, db, tenant_a):
    """Sense baseline, el primer PATCH no tindria amb que comparar-se."""
    _, company = tenant_a
    tender = await _create(client_a, company)

    revisions = await _revisions(db, uuid.UUID(tender["id"]))
    assert [r.revision_number for r in revisions] == [1]
    assert revisions[0].trigger == "manual_edit"


# --------------------------------------------------------------------------- #
# B. Permisos i tenancy                                                         #
# --------------------------------------------------------------------------- #


async def test_the_owner_may_edit(client_a, tenant_a):
    _, company = tenant_a
    tender = await _create(client_a, company)
    status, body = await _patch(
        client_a, company, tender["id"], expected_revision=1, title="Titol corregit"
    )
    assert status == 200
    assert body["tender"]["title"] == "Titol corregit"


async def test_another_company_cannot_edit_it(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    tender = await _create(client_a, company_a)

    status, _body = await _patch(
        client_b, company_b, tender["id"], expected_revision=1, title="Robat"
    )
    assert status == 404


async def test_a_user_of_both_companies_cannot_edit_a_from_b(
    client_factory, db, tenant_a, tenant_b, client_a
):
    """Ser membre de les dues no fa que el context de B abasti la licitacio d'A."""
    from app.models.enums import MembershipRole
    from app.models.identity import CompanyMembership
    from tests.conftest import TEST_PASSWORD, login

    user, company_a = tenant_a
    _, company_b = tenant_b
    tender = await _create(client_a, company_a)
    db.add(
        CompanyMembership(user_id=user.id, company_id=company_b.id, role=MembershipRole.OWNER)
    )
    await db.commit()

    http = client_factory()
    await login(http, user.email, TEST_PASSWORD)
    status, _body = await _patch(
        http, company_b, tender["id"], expected_revision=1, title="Des de B"
    )
    assert status == 404


async def test_a_public_tender_is_not_editable_here(client_a, tenant_a, tender_document):
    """Una licitacio d'una font sindicada no s'edita per aquesta via.

    Aqui no cal dissimular: una licitacio publica existeix per a tothom, aixi
    que dir-ne que no s'edita no revela res que no fos visible. El 404 es
    reserva per a les que son d'una altra empresa.
    """
    _, company = tenant_a
    status, error = await _patch(
        client_a, company, tender_document.tender_id, expected_revision=0, title="No"
    )
    assert status == 422
    assert error["error"]["code"] == "tender_not_manual"


# --------------------------------------------------------------------------- #
# C. Identitat immutable                                                        #
# --------------------------------------------------------------------------- #


async def test_editing_never_moves_the_identity(client_a, db, tenant_a):
    _, company = tenant_a
    created = await _create(client_a, company)
    before = (
        await db.execute(sa.select(Tender).where(Tender.id == uuid.UUID(created["id"])))
    ).scalars().one()
    identity = (before.id, before.source_id, before.external_id, before.owner_company_id)

    await _patch(client_a, company, created["id"], expected_revision=1, title="Un altre")

    await db.refresh(before)
    assert (before.id, before.source_id, before.external_id, before.owner_company_id) == identity


@pytest.mark.parametrize("field", ["id", "source_id", "external_id", "owner_company_id"])
async def test_identity_fields_are_rejected_and_not_ignored(client_a, tenant_a, field):
    """`extra="forbid"`: es diu que no, no es fa veure que no s'ha enviat."""
    _, company = tenant_a
    tender = await _create(client_a, company)
    status, _body = await _patch(
        client_a, company, tender["id"], expected_revision=1, **{field: str(uuid.uuid4())}
    )
    assert status == 422


# --------------------------------------------------------------------------- #
# D. CAS                                                                        #
# --------------------------------------------------------------------------- #


async def test_a_stale_expected_revision_is_a_conflict(client_a, tenant_a):
    _, company = tenant_a
    tender = await _create(client_a, company)
    await _patch(client_a, company, tender["id"], expected_revision=1, title="Primera")

    status, error = await _patch(
        client_a, company, tender["id"], expected_revision=1, title="Segona"
    )
    assert status == 409
    assert error["error"]["code"] == "tender_revision_conflict"


async def test_a_conflict_writes_nothing(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _create(client_a, company)
    await _patch(client_a, company, tender["id"], expected_revision=1, title="Primera")

    await _patch(client_a, company, tender["id"], expected_revision=1, title="Perduda")

    row = (
        await db.execute(sa.select(Tender).where(Tender.id == uuid.UUID(tender["id"])))
    ).scalars().one()
    assert row.title == "Primera"


async def test_the_current_revision_is_accepted(client_a, tenant_a):
    _, company = tenant_a
    tender = await _create(client_a, company)
    _status, first = await _patch(
        client_a, company, tender["id"], expected_revision=1, title="Primera"
    )
    assert first["revision"] == 2

    status, second = await _patch(
        client_a, company, tender["id"], expected_revision=2, title="Segona"
    )
    assert (status, second["revision"]) == (200, 3)


# --------------------------------------------------------------------------- #
# E. No-op                                                                      #
# --------------------------------------------------------------------------- #


async def test_writing_the_same_values_creates_no_revision(client_a, db, tenant_a):
    """La idempotencia no es d'aquesta fase: la posa `record_revision`."""
    _, company = tenant_a
    tender = await _create(client_a, company, title="Igual")

    status, body = await _patch(
        client_a, company, tender["id"], expected_revision=1, title="Igual"
    )
    assert status == 200
    assert body["revised"] is False
    assert body["revision"] == 1
    assert len(await _revisions(db, uuid.UUID(tender["id"]))) == 1


async def test_an_empty_patch_is_a_no_op(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _create(client_a, company)
    _status, body = await _patch(client_a, company, tender["id"], expected_revision=1)
    assert body["revised"] is False
    assert len(await _revisions(db, uuid.UUID(tender["id"]))) == 1


# --------------------------------------------------------------------------- #
# F. Revisio i traçabilitat                                                     #
# --------------------------------------------------------------------------- #


async def test_a_real_edit_adds_exactly_one_revision(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _create(client_a, company, title="Abans")
    await _patch(client_a, company, tender["id"], expected_revision=1, title="Despres")

    revisions = await _revisions(db, uuid.UUID(tender["id"]))
    assert [r.revision_number for r in revisions] == [1, 2]
    assert revisions[1].trigger == "manual_edit"
    assert revisions[1].normalized_snapshot["title"] == "Despres"


async def test_the_change_is_explained_by_the_existing_event_log(client_a, db, tenant_a):
    """No hi ha auditoria paral·lela: s'escriu a `TenderChangeEvent`."""
    _, company = tenant_a
    tender = await _create(client_a, company, title="Abans")
    await _patch(client_a, company, tender["id"], expected_revision=1, title="Despres")

    events = (
        await db.execute(
            sa.select(TenderChangeEvent).where(
                TenderChangeEvent.tender_id == uuid.UUID(tender["id"])
            )
        )
    ).scalars().all()
    assert events
    assert any(e.old_value == "Abans" and e.new_value == "Despres" for e in events)
    assert all(e.from_revision_id is not None for e in events)


async def test_the_correction_is_signed(client_a, db, tenant_a):
    """Qui ha corregit que, i quan. Nomes quan hi ha hagut correccio."""
    from app.models.audit import AuditLog
    from app.models.enums import AuditAction

    user, company = tenant_a
    tender = await _create(client_a, company, title="Abans")
    await _patch(client_a, company, tender["id"], expected_revision=1, title="Despres")

    entries = (
        await db.execute(
            sa.select(AuditLog).where(
                AuditLog.entity_id == uuid.UUID(tender["id"]),
                AuditLog.action == AuditAction.UPDATE,
            )
        )
    ).scalars().all()
    assert len(entries) == 1
    assert entries[0].user_id == user.id
    assert entries[0].company_id == company.id
    assert "title" in (entries[0].summary or "")


async def test_the_signature_names_the_buyer_too(client_a, db, tenant_a):
    """Canviar d'organisme es una correccio, encara que no sigui un camp mes."""
    from app.models.audit import AuditLog
    from app.models.enums import AuditAction

    _, company = tenant_a
    buyer = await _global_buyer(db)
    tender = await _create(client_a, company)
    await _patch(
        client_a, company, tender["id"], expected_revision=1, buyer_id=str(buyer.id)
    )

    entry = (
        await db.execute(
            sa.select(AuditLog).where(
                AuditLog.entity_id == uuid.UUID(tender["id"]),
                AuditLog.action == AuditAction.UPDATE,
            )
        )
    ).scalars().one()
    assert "buyer" in (entry.summary or "")


async def test_a_no_op_is_not_signed(client_a, db, tenant_a):
    """Firmar una correccio que no ha existit seria firmar en fals."""
    from app.models.audit import AuditLog
    from app.models.enums import AuditAction

    _, company = tenant_a
    tender = await _create(client_a, company, title="Igual")
    await _patch(client_a, company, tender["id"], expected_revision=1, title="Igual")

    entries = (
        await db.execute(
            sa.select(AuditLog).where(
                AuditLog.entity_id == uuid.UUID(tender["id"]),
                AuditLog.action == AuditAction.UPDATE,
            )
        )
    ).scalars().all()
    assert entries == []


async def test_a_field_outside_the_snapshot_does_not_create_a_revision(
    client_a, db, tenant_a
):
    """`expediente_number` no es a la fotografia canonica. No s'hi afegeix.

    Es documenta tal com es: el snapshot descriu el que publica un organ, i
    decidir que hi entra no es feina d'aquesta fase.
    """
    _, company = tenant_a
    tender = await _create(client_a, company, expediente_number="2024-1")

    status, body = await _patch(
        client_a, company, tender["id"], expected_revision=1, expediente_number="2024-2"
    )
    assert status == 200
    assert body["revised"] is False
    assert len(await _revisions(db, uuid.UUID(tender["id"]))) == 1

    # I tot i aixi s'ha desat: no generar revisio no vol dir no guardar.
    db.expire_all()
    row = (
        await db.execute(sa.select(Tender).where(Tender.id == uuid.UUID(tender["id"])))
    ).scalars().one()
    assert row.expediente_number == "2024-2"


# --------------------------------------------------------------------------- #
# G. Organisme                                                                  #
# --------------------------------------------------------------------------- #


async def _global_buyer(session) -> Buyer:
    from app.models.sources import Source

    source = Source(code=f"placsp-{uuid.uuid4().hex[:8]}", name="PLACSP")
    session.add(source)
    await session.flush()
    buyer = Buyer(
        source_id=source.id,
        external_identifier=f"org-{uuid.uuid4().hex[:8]}",
        name="Ajuntament public",
    )
    session.add(buyer)
    await session.commit()
    return buyer


async def test_a_buyer_can_be_added_afterwards(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _create(client_a, company)
    assert tender["buyer"] is None
    buyer = await _global_buyer(db)

    status, body = await _patch(
        client_a, company, tender["id"], expected_revision=1, buyer_id=str(buyer.id)
    )
    assert status == 200
    assert body["tender"]["buyer"]["id"] == str(buyer.id)
    assert body["revised"] is True


async def test_a_manual_buyer_can_be_written_afterwards(client_a, db, tenant_a):
    _, company = tenant_a
    tender = await _create(client_a, company)

    _status, body = await _patch(
        client_a,
        company,
        tender["id"],
        expected_revision=1,
        buyer_manual={"client_token": str(uuid.uuid4()), "name": "Escrit despres"},
    )
    created = (
        await db.execute(
            sa.select(Buyer).where(Buyer.id == uuid.UUID(body["tender"]["buyer"]["id"]))
        )
    ).scalars().one()
    assert created.owner_company_id == company.id


async def test_a_foreign_private_buyer_is_refused(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    theirs = await _create(
        client_b,
        company_b,
        buyer_manual={"client_token": str(uuid.uuid4()), "name": "De B"},
    )
    mine = await _create(client_a, company_a)

    status, _body = await _patch(
        client_a, company_a, mine["id"], expected_revision=1,
        buyer_id=theirs["buyer"]["id"],
    )
    assert status == 404


async def test_an_explicit_null_clears_the_buyer(client_a, db, tenant_a):
    """Ometre no toca; `null` treu. No cal cap indicador addicional."""
    _, company = tenant_a
    buyer = await _global_buyer(db)
    tender = await _create(client_a, company, buyer_id=str(buyer.id))
    assert tender["buyer"] is not None

    _status, body = await _patch(
        client_a, company, tender["id"], expected_revision=1, buyer_id=None
    )
    assert body["tender"]["buyer"] is None
    assert body["revised"] is True


async def test_omitting_the_buyer_leaves_it_alone(client_a, db, tenant_a):
    _, company = tenant_a
    buyer = await _global_buyer(db)
    tender = await _create(client_a, company, buyer_id=str(buyer.id))

    _status, body = await _patch(
        client_a, company, tender["id"], expected_revision=1, title="Nomes el titol"
    )
    assert body["tender"]["buyer"]["id"] == str(buyer.id)


async def test_buyer_id_and_buyer_manual_remain_exclusive(client_a, db, tenant_a):
    _, company = tenant_a
    buyer = await _global_buyer(db)
    tender = await _create(client_a, company)

    status, error = await _patch(
        client_a,
        company,
        tender["id"],
        expected_revision=1,
        buyer_id=str(buyer.id),
        buyer_manual={"client_token": str(uuid.uuid4()), "name": "Tots dos"},
    )
    assert status == 422
    assert error["error"]["code"] == "buyer_choice_ambiguous"


async def test_a_refused_buyer_leaves_no_trace(client_a, client_b, db, tenant_a, tenant_b):
    """Atomicitat: si el PATCH falla, l'organisme nou no queda al cataleg."""
    _, company_a = tenant_a
    _, company_b = tenant_b
    theirs = await _create(
        client_b, company_b,
        buyer_manual={"client_token": str(uuid.uuid4()), "name": "De B"},
    )
    mine = await _create(client_a, company_a)

    await _patch(
        client_a, company_a, mine["id"], expected_revision=1, buyer_id=theirs["buyer"]["id"]
    )

    row = (
        await db.execute(sa.select(Tender).where(Tender.id == uuid.UUID(mine["id"])))
    ).scalars().one()
    assert row.buyer_id is None


# --------------------------------------------------------------------------- #
# H. Pre-fit                                                                    #
# --------------------------------------------------------------------------- #


async def test_a_real_edit_recalculates_only_this_tender(client_a, db, tenant_a):
    from app.models.matching import CompanyTenderMatch

    _, company = tenant_a
    first = await _create(client_a, company, title="Primera")
    second = await _create(client_a, company, title="Segona")

    before = (
        await db.execute(
            sa.select(CompanyTenderMatch.calculated_at).where(
                CompanyTenderMatch.tender_id == uuid.UUID(second["id"])
            )
        )
    ).scalar_one()

    await _patch(client_a, company, first["id"], expected_revision=1, title="Canviada")

    after = (
        await db.execute(
            sa.select(CompanyTenderMatch.calculated_at).where(
                CompanyTenderMatch.tender_id == uuid.UUID(second["id"])
            )
        )
    ).scalar_one()
    assert after == before


async def test_a_no_op_does_not_recalculate(client_a, db, tenant_a):
    from app.models.matching import CompanyTenderMatch

    _, company = tenant_a
    tender = await _create(client_a, company, title="Igual")
    before = (
        await db.execute(
            sa.select(CompanyTenderMatch.calculated_at).where(
                CompanyTenderMatch.tender_id == uuid.UUID(tender["id"])
            )
        )
    ).scalar_one()

    await _patch(client_a, company, tender["id"], expected_revision=1, title="Igual")

    after = (
        await db.execute(
            sa.select(CompanyTenderMatch.calculated_at).where(
                CompanyTenderMatch.tender_id == uuid.UUID(tender["id"])
            )
        )
    ).scalar_one()
    assert after == before


# --------------------------------------------------------------------------- #
# I. Rols                                                                       #
# --------------------------------------------------------------------------- #


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


@pytest.mark.parametrize(
    "role", ["owner", "admin", "bid_manager"]
)
async def test_whoever_may_open_an_expedient_may_correct_it(client_a, db, tenant_a, role):
    from app.models.enums import MembershipRole

    _, company = tenant_a
    tender = await _create(client_a, company)
    await _set_role(db, tenant_a, MembershipRole(role))

    status, _body = await _patch(
        client_a, company, tender["id"], expected_revision=1, title="Corregit"
    )
    assert status == 200


@pytest.mark.parametrize("role", ["technical", "viewer"])
async def test_who_does_not_decide_does_not_correct(client_a, db, tenant_a, role):
    """Corregir una licitacio es el mateix acte de negoci que obrir-la."""
    from app.models.enums import MembershipRole

    _, company = tenant_a
    tender = await _create(client_a, company)
    await _set_role(db, tenant_a, MembershipRole(role))

    status, _body = await _patch(
        client_a, company, tender["id"], expected_revision=1, title="No hi toca"
    )
    assert status == 404


# --------------------------------------------------------------------------- #
# J. C-12: aprovar no es tancar amb clau                                        #
# --------------------------------------------------------------------------- #
#
# Es el test que mes importa d'aquesta fase. Un paquet congelat o aprovat NO pot
# impedir corregir la licitacio: qui aprova aprova un dossier, no es fa amo de
# les dades de l'expedient. El que ha de passar es el mateix que quan l'organ
# publica un canvi —el paquet queda obsolet i es diu— i no que el servidor
# converteixi una aprovacio en un pany.
#
# La maquinaria per arribar fins aqui —dossier complet, gate final, congelacio,
# aprovacio— ja viu a S5C i es reutilitza tal qual. L'unica peça que es canvia
# es la licitacio de sota: en comptes d'una de sindicada, una d'oberta a ma.

@pytest.fixture
async def extraction_run(db, tender):
    return await add_extraction_run(db, tender)


@pytest.fixture
async def ready(db, tenant_a, tender, extraction_run):
    """La candidatura completa d'S5C, pero sobre la licitacio d'aqui sota."""
    return await build_ready_candidacy(db, tenant_a, tender, extraction_run)


@pytest.fixture
async def tender(db, tenant_a):
    """La licitacio de sota: en comptes d'una de sindicada, una d'oberta a ma.

    Es crea per la via real i despres se li posa el termini, que l'alta manual
    encara no demana; es desa una revisio nova perque la fotografia de la qual
    penjara el paquet sigui la que descriu la licitacio de veritat.
    """
    from datetime import UTC, datetime, timedelta

    from app.models.identity import User
    from app.services import manual_tender, tender_lifecycle

    user, company = tenant_a
    tender, _created = await manual_tender.create_manual_tender(
        db,
        company_id=company.id,
        user=await db.get(User, user.id),
        data=manual_tender.ManualTenderInput(
            client_token=uuid.uuid4(), title="Servei de manteniment integral"
        ),
    )
    tender.deadline_at = datetime.now(UTC) + timedelta(days=40)
    await db.flush()
    await tender_lifecycle.record_revision(db, tender, trigger="manual_edit")
    await db.commit()
    await db.refresh(tender)
    return tender


def _new_deadline() -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) + timedelta(days=60)).isoformat()


async def test_an_approved_package_does_not_lock_the_tender(client_a, db, ready):
    """C-12: amb el paquet aprovat, corregir el termini continua permes."""
    package = await freeze(db, ready)
    await approve(db, ready, package)

    company, tender = ready["company"], ready["tender"]
    revision = await _current_revision(db, tender.id)
    status, body = await _patch(
        client_a, company, tender.id, expected_revision=revision, deadline_at=_new_deadline()
    )
    assert status == 200, body
    assert body["revised"] is True


async def test_the_approved_package_stays_exactly_as_it_was(client_a, db, ready):
    """Corregir despres no reescriu el que algu va aprovar."""
    from app.models.submission import SubmissionPackage

    package = await freeze(db, ready)
    await approve(db, ready, package)
    package_id = package.id
    before = (
        package.status,
        package.manifest_hash,
        package.manifest_json,
        package.tender_revision_id,
        package.revision_number,
    )

    company, tender = ready["company"], ready["tender"]
    revision = await _current_revision(db, tender.id)
    await _patch(
        client_a, company, tender.id, expected_revision=revision, deadline_at=_new_deadline()
    )

    db.expire_all()
    after = (
        await db.execute(
            sa.select(SubmissionPackage).where(SubmissionPackage.id == package_id)
        )
    ).scalars().one()
    assert (
        after.status,
        after.manifest_hash,
        after.manifest_json,
        after.tender_revision_id,
        after.revision_number,
    ) == before


async def test_the_package_is_told_that_the_tender_has_changed(client_a, db, ready):
    """L'obsolescencia no s'inventa aqui: la calcula `evaluate_staleness`."""
    from app.services import submission_package

    package = await freeze(db, ready)
    await approve(db, ready, package)
    workspace = ready["workspace"]
    assert "tender_changed" not in (
        await submission_package.evaluate_staleness(db, workspace, package)
    ).reasons

    company, tender = ready["company"], ready["tender"]
    revision = await _current_revision(db, tender.id)
    status, body = await _patch(
        client_a, company, tender.id, expected_revision=revision, deadline_at=_new_deadline()
    )
    assert (status, body["revised"]) == (200, True), body

    report = await submission_package.evaluate_staleness(db, workspace, package)
    assert "tender_changed" in report.reasons


async def test_an_informational_correction_does_not_invalidate_the_package(
    client_a, db, ready
):
    """Corregir el titol es una revisio, pero no obliga a tornar a aprovar res.

    Ho decideix la taxonomia que ja hi havia —`TITLE_CHANGED` es informatiu—, i
    aqui no se'n fa una de nova. Si cada correccio d'una lletra reobris una
    aprovacio, el senyal es gastaria i el dia que canviï el termini ningu se'l
    miraria.
    """
    from app.services import submission_package

    package = await freeze(db, ready)
    await approve(db, ready, package)
    company, tender = ready["company"], ready["tender"]
    revision = await _current_revision(db, tender.id)

    status, body = await _patch(
        client_a, company, tender.id, expected_revision=revision, title="Titol corregit"
    )
    assert (status, body["revised"]) == (200, True), body

    report = await submission_package.evaluate_staleness(db, ready["workspace"], package)
    assert "tender_changed" not in report.reasons


async def test_a_no_op_does_not_make_the_package_stale(client_a, db, ready):
    """Si no ha canviat res, no hi ha res a tornar a revisar."""
    from app.services import submission_package

    package = await freeze(db, ready)
    await approve(db, ready, package)
    company, tender = ready["company"], ready["tender"]
    revision = await _current_revision(db, tender.id)

    _status, body = await _patch(
        client_a, company, tender.id, expected_revision=revision, title=tender.title
    )
    assert body["revised"] is False

    report = await submission_package.evaluate_staleness(db, ready["workspace"], package)
    assert "tender_changed" not in report.reasons
