"""Relacions estructurades entre elements de revisio (S-R3). Zero crides externes.

Licitacio SINTETICA amb forma de Palamos (la mateixa que `test_review_actions`).
Els elements automatics fan de substituts de les files reals: el requisit
"ordinari" fa de fila de plataforma al nuvol (p32) i el d'assegurança, de
numeracio DDI del troncal SIP. Els manuals M06 i M21 es creen per l'API.
"""

from __future__ import annotations

import json
import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.audit import AuditLog
from app.models.enums import MembershipRole, ReviewRelationType
from app.models.identity import CompanyMembership, User
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.review import ReviewItem, ReviewItemRelation, ReviewItemRevision
from app.models.tenders import Tender
from tests import test_review_actions as actions_tests
from tests.conftest import TEST_PASSWORD, login

analyzable = actions_tests.analyzable
_clean_provider = actions_tests._clean_provider
palamos_like = actions_tests.palamos_like
_url = actions_tests._url
_item_of = actions_tests._item_of
ORDINARY, INSURANCE, LATENCY = (
    actions_tests.ORDINARY, actions_tests.INSURANCE, actions_tests.LATENCY)


async def _manual(client, company, tender_id, title):
    response = await client.post(_url(company, tender_id, "/requirements"),
                                 json={"title": title, "category": "technical_requirement"})
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["canonical_id"])


@pytest.fixture
async def items(session, palamos_like, tenant_a, client_a):
    """Elements de l'empresa A: tres automatics i dos manuals (M06, M21)."""
    _, company = tenant_a
    tid = palamos_like["tender_id"]
    assert (await client_a.post(_url(company, tid, "/start"))).status_code == 200
    rows = palamos_like["rows"]
    return {
        "tid": tid,
        "cloud": (await _item_of(session, company, rows[ORDINARY])).id,
        "ddi_numbering": (await _item_of(session, company, rows[INSURANCE])).id,
        "latency": (await _item_of(session, company, rows[LATENCY])).id,
        "m06": await _manual(client_a, company, tid, "M06 Solució cloud d'alta disponibilitat"),
        "m21": await _manual(client_a, company, tid, "M21 Accés directe a extensió (DDI)"),
    }


async def _relate(client, company, tid, source, target, kind, note=None):
    body = {"from_item_id": str(source), "to_item_id": str(target), "relation_type": kind}
    if note is not None:
        body["note"] = note
    return await client.post(_url(company, tid, "/relations"), json=body)


async def _relations(client, company, tid, item_id, archived=False):
    suffix = f"/items/{item_id}/relations" + ("?include_archived=true" if archived else "")
    response = await client.get(_url(company, tid, suffix))
    assert response.status_code == 200, response.text
    return response.json()


async def _raw(session):
    snapshot = {}
    for model in (Requirement, RequirementEvidence, AwardCriterion, AwardCriterionEvidence,
                  RequirementExtractionRun):
        table = model.__table__
        snapshot[table.name] = [dict(r) for r in (await session.execute(
            sa.select(table).order_by(table.c.id))).mappings()]
    return snapshot


# --------------------------------------------------------------------------- #
# Semantica amb forma de Palamos                                                #
# --------------------------------------------------------------------------- #


async def test_a_m06_cobert_parcialment_per_la_fila_de_plataforma(
    session, items, tenant_a, client_a
):
    _, company = tenant_a
    tid = items["tid"]

    response = await _relate(client_a, company, tid, items["m06"], items["cloud"],
                             "partial_match_of", "Nomes la fila de dimensionament (p32)")

    assert response.status_code == 201, response.text
    created = response.json()
    assert (created["direction"], created["relation_type"], created["relation_label"]) == (
        "outgoing", "partial_match_of", "Cobert parcialment per")
    assert created["other_item_id"] == str(items["cloud"])
    assert created["foldable"] is False
    incoming = await _relations(client_a, company, tid, items["cloud"])
    assert [(r["direction"], r["relation_label"], r["other_item_title"]) for r in incoming] == [
        ("incoming", "Cobreix parcialment", "M06 Solució cloud d'alta disponibilitat")]
    # Lectura canonica: visible, no plegada, i la llista no amaga res.
    m06 = (await client_a.get(_url(company, tid, f"/requirements/{items['m06']}"))).json()
    assert [r["relation_label"] for r in m06["visible_relations"]] == ["Cobert parcialment per"]
    assert m06["folded_relations"] == []
    assert len((await client_a.get(_url(company, tid, "/requirements"))).json()) == 5


async def test_b_k_m21_relacionat_amb_la_numeracio_ddi_es_desa_una_vegada(
    session, items, tenant_a, client_a
):
    _, company = tenant_a
    tid = items["tid"]

    first = await _relate(client_a, company, tid, items["m21"], items["ddi_numbering"],
                          "related_to", "Relacionats, no equivalents")
    reverse = await _relate(client_a, company, tid, items["ddi_numbering"], items["m21"],
                            "related_to")

    assert first.status_code == 201
    # Simetrica: la inversa es la mateixa relacio i no es desa dues vegades.
    assert reverse.status_code == 409
    assert reverse.json()["error"]["details"]["relation_id"] == first.json()["relation_id"]
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemRelation)) == 1
    other_side = await _relations(client_a, company, tid, items["ddi_numbering"])
    assert [(r["direction"], r["relation_label"]) for r in other_side] == [
        ("incoming", "Relacionat amb")]


async def test_reiteracio_i_pare_amb_la_direccio_explicita(session, items, tenant_a, client_a):
    _, company = tenant_a
    tid = items["tid"]

    restated = await _relate(client_a, company, tid, items["latency"], items["cloud"],
                             "restatement_of")
    parent = await _relate(client_a, company, tid, items["cloud"], items["ddi_numbering"],
                           "parent_of")

    assert (restated.json()["relation_label"], parent.json()["relation_label"]) == (
        "També apareix com", "Agrupa")
    child = await _relations(client_a, company, tid, items["ddi_numbering"])
    assert [r["relation_label"] for r in child] == ["Forma part de"]
    original = await _relations(client_a, company, tid, items["cloud"])
    assert sorted(r["relation_label"] for r in original) == ["Agrupa", "Reiterat a"]


async def test_duplicat_es_plegable_pero_no_s_amaga_encara(session, items, tenant_a, client_a):
    _, company = tenant_a
    tid = items["tid"]

    await _relate(client_a, company, tid, items["latency"], items["cloud"], "duplicate_of")

    duplicate = (await client_a.get(_url(company, tid, f"/requirements/{items['latency']}"))).json()
    assert [(r["relation_label"], r["foldable"]) for r in duplicate["folded_relations"]] == [
        ("Duplicat de", True)]
    assert duplicate["visible_relations"] == []
    original = (await client_a.get(_url(company, tid, f"/requirements/{items['cloud']}"))).json()
    assert [r["relation_label"] for r in original["folded_relations"]] == ["Té un duplicat"]
    listed = (await client_a.get(_url(company, tid, "/requirements"))).json()
    assert str(items["latency"]) in {r["canonical_id"] for r in listed}


# --------------------------------------------------------------------------- #
# Regles                                                                        #
# --------------------------------------------------------------------------- #


async def test_f_una_relacio_amb_si_mateix_es_refusa(items, tenant_a, client_a):
    _, company = tenant_a
    response = await _relate(client_a, company, items["tid"], items["m06"], items["m06"],
                             "related_to")
    assert response.status_code == 422


async def test_g_una_relacio_repetida_es_409(session, items, tenant_a, client_a):
    _, company = tenant_a
    tid = items["tid"]
    first = await _relate(client_a, company, tid, items["m06"], items["cloud"], "partial_match_of")
    again = await _relate(client_a, company, tid, items["m06"], items["cloud"], "partial_match_of")

    assert (first.status_code, again.status_code) == (201, 409)
    assert again.json()["error"]["code"] == "review_relation_exists"
    # Un altre tipus entre els mateixos elements si que es pot.
    assert (await _relate(client_a, company, tid, items["m06"], items["cloud"],
                          "related_to")).status_code == 201


@pytest.mark.parametrize(("kind", "chain"), [
    ("parent_of", [("cloud", "ddi_numbering"), ("ddi_numbering", "cloud")]),
    ("duplicate_of", [("cloud", "ddi_numbering"), ("ddi_numbering", "latency"),
                      ("latency", "cloud")]),
    ("supersedes", [("m06", "cloud"), ("cloud", "m06")]),
    ("supersedes", [("m06", "cloud"), ("cloud", "m21"), ("m21", "m06")]),
])
async def test_h_i_j_els_cicles_es_refusen(session, items, tenant_a, client_a, kind, chain):
    _, company = tenant_a
    tid = items["tid"]
    *ok_links, closing = chain

    for source, target in ok_links:
        assert (await _relate(client_a, company, tid, items[source], items[target],
                              kind)).status_code == 201
    response = await _relate(client_a, company, tid, items[closing[0]], items[closing[1]], kind)

    assert response.status_code == 422
    assert "cicle" in response.json()["error"]["message"]
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemRelation)) == \
        len(ok_links)


@pytest.mark.parametrize(("kind", "links"), [
    ("related_to", [("cloud", "ddi_numbering"), ("ddi_numbering", "latency"),
                    ("latency", "cloud")]),
    ("restatement_of", [("cloud", "ddi_numbering"), ("ddi_numbering", "cloud")]),
    ("partial_match_of", [("m06", "cloud"), ("cloud", "m06")]),
])
async def test_els_tipus_no_jerarquics_admeten_cicles(items, tenant_a, client_a, kind, links):
    _, company = tenant_a
    for source, target in links:
        assert (await _relate(client_a, company, items["tid"], items[source], items[target],
                              kind)).status_code == 201


async def test_un_element_te_un_sol_pare_i_es_duplicat_d_un_sol_altre(items, tenant_a, client_a):
    _, company = tenant_a
    tid = items["tid"]
    assert (await _relate(client_a, company, tid, items["cloud"], items["latency"],
                          "parent_of")).status_code == 201
    second_parent = await _relate(client_a, company, tid, items["m06"], items["latency"],
                                  "parent_of")
    assert (await _relate(client_a, company, tid, items["m21"], items["cloud"],
                          "duplicate_of")).status_code == 201
    second_target = await _relate(client_a, company, tid, items["m21"], items["ddi_numbering"],
                                  "duplicate_of")

    assert (second_parent.status_code, second_target.status_code) == (422, 422)


# --------------------------------------------------------------------------- #
# Arxiu                                                                         #
# --------------------------------------------------------------------------- #


async def test_l_arxivar_la_treu_de_la_lectura_pero_la_conserva(session, items, tenant_a,
                                                                client_a):
    _, company = tenant_a
    tid = items["tid"]
    relation_id = (await _relate(client_a, company, tid, items["m21"], items["ddi_numbering"],
                                 "related_to")).json()["relation_id"]

    first = await client_a.post(_url(company, tid, f"/relations/{relation_id}/archive"))
    again = await client_a.post(_url(company, tid, f"/relations/{relation_id}/archive"))

    assert (first.status_code, first.json()["changed"]) == (200, True)
    assert (again.status_code, again.json()["changed"]) == (200, False)
    assert await _relations(client_a, company, tid, items["m21"]) == []
    archived = await _relations(client_a, company, tid, items["m21"], archived=True)
    assert archived[0]["relation_id"] == relation_id and archived[0]["archived_at"]
    row = await session.get(ReviewItemRelation, uuid.UUID(relation_id))
    assert row is not None and row.archived_at is not None
    logs = (await session.scalars(sa.select(AuditLog).where(
        AuditLog.entity_type == "review_relation"))).all()
    assert sorted(log.action.value for log in logs) == ["archive", "create"]
    # Arxivada, la mateixa relacio es pot tornar a crear.
    assert (await _relate(client_a, company, tid, items["m21"], items["ddi_numbering"],
                          "related_to")).status_code == 201


# --------------------------------------------------------------------------- #
# Tenancy, permisos i abast                                                     #
# --------------------------------------------------------------------------- #


async def test_c_d_una_relacio_d_a_es_invisible_per_a_b_i_no_es_pot_creuar(
    session, items, tenant_a, tenant_b, client_a, client_b
):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    tid = items["tid"]
    assert (await client_b.post(_url(company_b, tid, "/start"))).status_code == 200
    item_b = await _item_of(session, company_b, (await session.get(
        Requirement, (await session.scalar(sa.select(Requirement.id).where(
            Requirement.title == ORDINARY)))))
    )
    relation_id = (await _relate(client_a, company_a, tid, items["m06"], items["cloud"],
                                 "partial_match_of")).json()["relation_id"]

    # C: B no la veu, ni al seu element ni pel d'A.
    assert await _relations(client_b, company_b, tid, item_b.id) == []
    assert (await client_b.get(_url(company_b, tid, f"/items/{items['cloud']}/relations"))
            ).status_code == 404
    assert (await client_b.post(_url(company_b, tid, f"/relations/{relation_id}/archive"))
            ).status_code == 404
    # D: A no pot relacionar el seu element amb un de B.
    crossed = await _relate(client_a, company_a, tid, items["m06"], item_b.id, "related_to")
    assert crossed.status_code == 404
    # I la base tampoc no ho deixa.
    session.add(ReviewItemRelation(company_id=company_a.id, tender_id=tid,
                                   from_item_id=items["m06"], to_item_id=item_b.id,
                                   relation_type=ReviewRelationType.RELATED_TO))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_e_una_relacio_entre_licitacions_es_refusa(session, items, tenant_a, client_a):
    _, company = tenant_a
    tid = items["tid"]
    tender = await session.get(Tender, tid)
    other = Tender(source_id=tender.source_id, external_id=f"exp-{uuid.uuid4().hex[:8]}",
                   title="Una altra licitacio")
    session.add(other)
    await session.commit()
    elsewhere = await _manual(client_a, company, other.id, "Requisit d'una altra licitacio")

    response = await _relate(client_a, company, tid, items["m06"], elsewhere, "related_to")

    assert response.status_code == 404
    session.add(ReviewItemRelation(company_id=company.id, tender_id=tid,
                                   from_item_id=items["m06"], to_item_id=elsewhere,
                                   relation_type=ReviewRelationType.RELATED_TO))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_un_rol_de_lectura_llegeix_pero_no_escriu(session, items, tenant_a, client_a,
                                                        client_factory):
    _, company = tenant_a
    tid = items["tid"]
    relation_id = (await _relate(client_a, company, tid, items["m06"], items["cloud"],
                                 "partial_match_of")).json()["relation_id"]
    viewer = User(email=f"v-{uuid.uuid4().hex[:8]}@empresa-a.com",
                  hashed_password=(await session.get(User, tenant_a[0].id)).hashed_password,
                  full_name="Lectora", is_active=True)
    session.add(viewer)
    await session.flush()
    session.add(CompanyMembership(user_id=viewer.id, company_id=company.id,
                                  role=MembershipRole.VIEWER, is_default=True))
    await session.commit()
    client = client_factory()
    await login(client, viewer.email, TEST_PASSWORD)

    assert len(await _relations(client, company, tid, items["m06"])) == 1
    assert (await _relate(client, company, tid, items["m21"], items["cloud"],
                          "related_to")).status_code == 403
    assert (await client.post(_url(company, tid, f"/relations/{relation_id}/archive"))
            ).status_code == 403


async def test_un_element_arxivat_no_es_pot_relacionar(session, items, tenant_a, client_a):
    _, company = tenant_a
    item = await session.get(ReviewItem, items["m21"])
    item.archived_at = sa.func.now()
    await session.commit()

    response = await _relate(client_a, company, items["tid"], items["m06"], items["m21"],
                             "related_to")

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# Auditoria i immutabilitat                                                     #
# --------------------------------------------------------------------------- #


async def test_l_auditoria_nomes_porta_identificadors(session, items, tenant_a, client_a):
    _, company = tenant_a
    secret_note = "Text literal del plec a la nota"
    await _relate(client_a, company, items["tid"], items["m06"], items["cloud"],
                  "partial_match_of", secret_note)

    [log] = (await session.scalars(sa.select(AuditLog).where(
        AuditLog.entity_type == "review_relation"))).all()
    assert "tipus=partial_match_of" in log.summary
    for forbidden in (secret_note, "M06", "Solució", ORDINARY):
        assert forbidden not in log.summary


async def test_les_relacions_no_toquen_l_extraccio_ni_els_elements(session, items, tenant_a,
                                                                   client_a):
    _, company = tenant_a
    tid = items["tid"]
    before = await _raw(session)
    item_ids = [items[k] for k in ("cloud", "ddi_numbering", "latency", "m06", "m21")]
    snapshots = {i: (await session.get(ReviewItem, i)) for i in item_ids}
    snapshots = {i: (dict(item.accepted_json), item.revision) for i, item in snapshots.items()}
    revisions = await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemRevision))

    relation_id = (await _relate(client_a, company, tid, items["m06"], items["cloud"],
                                 "partial_match_of")).json()["relation_id"]
    await _relate(client_a, company, tid, items["cloud"], items["latency"], "parent_of")
    await _relate(client_a, company, tid, items["m21"], items["ddi_numbering"], "related_to")
    await client_a.post(_url(company, tid, f"/relations/{relation_id}/archive"))
    assert "chunk" not in json.dumps(await _relations(client_a, company, tid, items["cloud"]))

    session.expire_all()
    assert await _raw(session) == before
    for item_id, (accepted, revision) in snapshots.items():
        item = await session.get(ReviewItem, item_id)
        assert (item.accepted_json, item.revision) == (accepted, revision)
    assert await session.scalar(
        sa.select(sa.func.count()).select_from(ReviewItemRevision)) == revisions


@pytest.mark.postgres
async def test_pg_dues_relacions_simultanies_no_poden_tancar_un_cicle(
    session_factory, items, tenant_a
):
    """El lock per licitacio serialitza: la segona veu la primera i es refusa."""
    import asyncio

    from app.core.errors import ValidationError as DomainValidationError
    from app.services import review_relations

    user, company = tenant_a
    common = {"company_id": company.id, "tender_id": items["tid"], "note": None,
              "actor_id": user.id, "relation_type": ReviewRelationType.PARENT_OF}
    async with session_factory() as first, session_factory() as second:
        await review_relations.create_relation(
            first, from_item_id=items["cloud"], to_item_id=items["latency"], **common)
        racing = asyncio.create_task(review_relations.create_relation(
            second, from_item_id=items["latency"], to_item_id=items["cloud"], **common))
        await asyncio.sleep(0.3)
        assert not racing.done(), "la segona hauria d'esperar el lock de la licitacio"
        await first.commit()
        with pytest.raises(DomainValidationError, match="cicle"):
            await racing
        await second.rollback()
