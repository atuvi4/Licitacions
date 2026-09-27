"""Capa de revisio canonica per empresa (S-R1). Zero crides externes.

Les files d'extraccio les produeix la canonada v3 real amb el proveidor fals
(una licitacio SINTETICA amb forma de Palamos: un requisit ordinari i sostingut,
i un altre amb un valor que no es a la cita). Es prova:

* la materialitzacio (una vegada, idempotent, estat inicial correcte);
* que l'extraccio no canvia ni un camp;
* la tenancy (dues empreses, dos elements, cap fuga);
* el vocabulari de la interficie i que no s'hi filtra cap codi tecnic;
* les garanties de la base (CHECK, unicitat, historial nomes d'afegir, esborrat).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.enums import (
    ReviewAction,
    ReviewItemKind,
    ReviewOriginHint,
    ReviewSourceRole,
    ReviewStatus,
    ReviewVerification,
)
from app.models.requirements import Requirement, RequirementEvidence
from app.models.review import ReviewItem, ReviewItemRevision, ReviewItemSource
from app.services import review_view
from tests import test_requirement_analysis as base
from tests.test_requirement_analysis import QUOTE_27
from tests.test_requirement_analysis_v3 import _evidence, _requirement, _run_v3

analyzable = base.analyzable
_clean_provider = base._clean_provider

ORDINARY = "Volum anual de negoci minim"
NEEDS_REVIEW = "Import de l'assegurança de responsabilitat civil"
FORBIDDEN = (
    "chunk", "normalised", "duplicate_key", "claim_not_supported", "mandatory/mandatory",
    "informative/informational", "value_not_in_quote", "binding_ambiguous", "no_claims",
)


def _payload(extraction_id):
    """SINTETIC: dos requisits amb forma de sortida de model."""
    ordinary = _requirement(extraction_id)  # mandatory/mandatory, 450.000 EUR a la cita
    unsupported = _requirement(
        extraction_id,
        category="insurance",
        obligation="informative",
        modality="informational",
        operator=None,
        title=NEEDS_REVIEW,
        claims=[{"id": "import_minim", "kind": "amount", "value": 500000, "unit": "EUR",
                 "anchor": "volumen anual de negocios"}],
        evidence=[_evidence(extraction_id, supports=["import_minim"])],
    )
    return {"requirements": [ordinary, unsupported], "award_candidates": []}


@pytest.fixture
async def extracted(session, analyzable):
    run = await _run_v3(session, analyzable, _payload(analyzable["extraction"].id))
    rows = {r.title: r for r in (await session.scalars(sa.select(Requirement))).all()}
    return {"run": run, "tender_id": analyzable["document"].tender_id, "rows": rows}


async def _raw_extraction(session):
    """Totes les columnes de l'extraccio, per comparar abans i despres."""
    requirements = [dict(r) for r in (await session.execute(
        sa.select(Requirement.__table__).order_by(Requirement.__table__.c.id))).mappings()]
    evidence = [dict(r) for r in (await session.execute(
        sa.select(RequirementEvidence.__table__).order_by(
            RequirementEvidence.__table__.c.id))).mappings()]
    return requirements, evidence


async def _start(session, company, user, tender_id):
    result = await review_view.start_review(
        session, company_id=company.id, tender_id=tender_id, actor_id=user.id)
    await session.commit()
    return result


# --------------------------------------------------------------------------- #
# Materialitzacio                                                               #
# --------------------------------------------------------------------------- #


async def test_a_b_es_materialitza_una_vegada_i_es_idempotent(session, extracted, tenant_a):
    user, company = tenant_a

    first = await _start(session, company, user, extracted["tender_id"])
    second = await _start(session, company, user, extracted["tender_id"])

    assert (first.created, first.already_present, first.total_requirements) == (2, 0, 2)
    assert (second.created, second.already_present) == (0, 2)
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItem)) == 2
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemSource)) == 2
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemRevision)) == 2
    revision = await session.scalar(sa.select(ReviewItemRevision).limit(1))
    assert (revision.revision_no, revision.action) == (1, ReviewAction.CREATE)
    item = await session.get(ReviewItem, revision.item_id)
    assert revision.accepted_json_after == item.accepted_json
    assert revision.verification_status_after == item.verification_status
    assert (item.kind, item.origin_hint, item.revision) == (
        ReviewItemKind.REQUIREMENT, ReviewOriginHint.EXTRACTION, 1)


async def test_c_d_estat_inicial_segons_el_senyal_de_l_extraccio(session, extracted, tenant_a):
    user, company = tenant_a
    rows = extracted["rows"]
    assert rows[NEEDS_REVIEW].review_status == ReviewStatus.NEEDS_REVIEW
    assert rows[ORDINARY].review_status == ReviewStatus.UNREVIEWED

    await _start(session, company, user, extracted["tender_id"])

    status = {
        (await session.get(Requirement, s.requirement_id)).title: (
            await session.get(ReviewItem, s.item_id)).verification_status
        for s in (await session.scalars(sa.select(ReviewItemSource))).all()
    }
    assert status == {NEEDS_REVIEW: ReviewVerification.NEEDS_REVIEW,
                      ORDINARY: ReviewVerification.UNREVIEWED}


async def test_e_l_extraccio_no_canvia_ni_un_camp(session, extracted, tenant_a, tenant_b):
    before = await _raw_extraction(session)

    for user, company in (tenant_a, tenant_b):
        await _start(session, company, user, extracted["tender_id"])
        await review_view.list_requirements(
            session, company_id=company.id, tender_id=extracted["tender_id"])
        await review_view.overview(session, company_id=company.id,
                                   tender_id=extracted["tender_id"])
    session.expire_all()

    assert await _raw_extraction(session) == before


async def test_accepted_json_es_una_instantania_tipada(session, extracted, tenant_a):
    user, company = tenant_a
    await _start(session, company, user, extracted["tender_id"])
    source = await session.scalar(sa.select(ReviewItemSource).where(
        ReviewItemSource.requirement_id == extracted["rows"][ORDINARY].id))
    item = await session.get(ReviewItem, source.item_id)

    assert item.accepted_json == {
        "title": ORDINARY, "description": None, "category": "economic_solvency",
        "obligation": "mandatory", "modality": "mandatory",
        "values": [{"key": "volum_minim", "operator": "gte", "value": 450000,
                    "unit_label": "EUR", "source": "document"}],
        "needs_review_from_extraction": False,
    }


# --------------------------------------------------------------------------- #
# Tenancy                                                                       #
# --------------------------------------------------------------------------- #


async def test_f_dues_empreses_dos_elements_per_a_la_mateixa_font(
    session, extracted, tenant_a, tenant_b
):
    for user, company in (tenant_a, tenant_b):
        await _start(session, company, user, extracted["tender_id"])

    requirement_id = extracted["rows"][ORDINARY].id
    sources = (await session.scalars(sa.select(ReviewItemSource).where(
        ReviewItemSource.requirement_id == requirement_id))).all()
    assert {s.company_id for s in sources} == {tenant_a[1].id, tenant_b[1].id}
    assert len({s.item_id for s in sources}) == 2
    for company in (tenant_a[1], tenant_b[1]):
        listed = await review_view.list_requirements(
            session, company_id=company.id, tender_id=extracted["tender_id"])
        owned = set((await session.scalars(sa.select(ReviewItem.id).where(
            ReviewItem.company_id == company.id))).all())
        assert {r.canonical_id for r in listed} == owned


async def test_g_una_empresa_no_llegeix_els_elements_d_una_altra(
    session, extracted, tenant_a, tenant_b, client_a, client_b
):
    tender_id = extracted["tender_id"]
    company_a, company_b = tenant_a[1], tenant_b[1]
    base_b = f"/api/v1/companies/{company_b.id}/tenders/{tender_id}/review"
    started = await client_b.post(f"{base_b}/start")
    assert started.status_code == 200, started.text
    item_b = await session.scalar(sa.select(ReviewItem.id).where(
        ReviewItem.company_id == company_b.id).limit(1))

    base_a = f"/api/v1/companies/{company_a.id}/tenders/{tender_id}/review"
    assert (await client_a.get(f"{base_a}/requirements/{item_b}")).status_code == 404
    assert (await client_a.get(f"{base_a}/requirements")).json() == []
    overview_a = (await client_a.get(f"{base_a}/overview")).json()
    assert (overview_a["total_requirements"], overview_a["pending_materialisation"]) == (0, 2)
    # Tampoc per la URL de l'altra empresa: no n'es membre.
    assert (await client_a.get(f"{base_b}/requirements")).status_code == 404
    assert (await client_a.post(f"{base_b}/start")).status_code == 404


async def test_la_base_no_deixa_dos_elements_per_a_la_mateixa_font(
    session, extracted, tenant_a
):
    user, company = tenant_a
    await _start(session, company, user, extracted["tender_id"])
    requirement_id = extracted["rows"][ORDINARY].id
    duplicate = ReviewItem(
        id=uuid.uuid4(), company_id=company.id, tender_id=extracted["tender_id"],
        kind=ReviewItemKind.REQUIREMENT, verification_status=ReviewVerification.UNREVIEWED,
        accepted_json={}, origin_hint=ReviewOriginHint.EXTRACTION, revision=1)
    session.add(duplicate)
    await session.flush()
    session.add(ReviewItemSource(item_id=duplicate.id, company_id=company.id,
                                 requirement_id=requirement_id,
                                 link_role=ReviewSourceRole.PRIMARY))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_una_font_no_pot_penjar_d_un_element_d_una_altra_empresa(
    session, extracted, tenant_a, tenant_b
):
    user, company_a = tenant_a
    await _start(session, company_a, user, extracted["tender_id"])
    item_a = await session.scalar(sa.select(ReviewItem.id).limit(1))

    session.add(ReviewItemSource(item_id=item_a, company_id=tenant_b[1].id,
                                 requirement_id=extracted["rows"][NEEDS_REVIEW].id,
                                 link_role=ReviewSourceRole.EXTRA_EVIDENCE))
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


# --------------------------------------------------------------------------- #
# Camps LEGACY                                                                  #
# --------------------------------------------------------------------------- #


async def test_h_els_camps_de_revisio_legacy_no_compten(
    session, extracted, tenant_a, client_a
):
    user, company = tenant_a
    ordinary = extracted["rows"][ORDINARY]
    ordinary.review_status = ReviewStatus.REJECTED
    ordinary.manual_override_json = {"title": "TITOL SOBREESCRIT LEGACY"}
    ordinary.review_note = "NOTA LEGACY"
    ordinary.reviewed_by = user.id
    ordinary.reviewed_at = datetime.now(UTC)
    await session.commit()

    await _start(session, company, user, extracted["tender_id"])
    # Una revisio legacy DESPRES de materialitzar tampoc no canvia res.
    unsupported = extracted["rows"][NEEDS_REVIEW]
    unsupported.review_status = ReviewStatus.VERIFIED
    unsupported.reviewed_by = user.id
    unsupported.reviewed_at = datetime.now(UTC)
    await session.commit()

    body = (await client_a.get(
        f"/api/v1/companies/{company.id}/tenders/{extracted['tender_id']}/review/requirements"
    )).json()
    by_title = {r["title"]: r for r in body}
    assert set(by_title) == {ORDINARY, NEEDS_REVIEW}
    # Rebutjat LEGACY: el senyal es refa amb les cites (tot sostingut) i no es "Descartat".
    assert by_title[ORDINARY]["verification_status"] == "unreviewed"
    assert by_title[NEEDS_REVIEW]["verification_status"] == "needs_review"
    text = json.dumps(body, ensure_ascii=False)
    assert "LEGACY" not in text


# --------------------------------------------------------------------------- #
# Vocabulari i evidencia                                                        #
# --------------------------------------------------------------------------- #


async def test_i_j_k_l_vocabulari_i_evidencia(session, extracted, tenant_a, client_a):
    user, company = tenant_a
    base_url = f"/api/v1/companies/{company.id}/tenders/{extracted['tender_id']}/review"
    assert (await client_a.post(f"{base_url}/start")).status_code == 200
    evidence_before = [dict(r) for r in (await session.execute(
        sa.select(RequirementEvidence.__table__))).mappings()]

    body = (await client_a.get(f"{base_url}/requirements")).json()
    by_title = {r["title"]: r for r in body}
    ordinary, unsupported = by_title[ORDINARY], by_title[NEEDS_REVIEW]

    # I: mandatory/mandatory -> Obligatori.
    assert ordinary["obligation_label"] == "Obligatori"
    assert ordinary["evidence_label"] == "Confirmat al document"
    assert ordinary["verification_label"] == "Pendent de revisar"
    assert ordinary["provenance_label"] == "Extret automàticament"
    assert ordinary["values"] == [{
        "key": "volum_minim", "operator": "gte", "value": 450000, "unit_label": "EUR",
        "source_label": "Del document", "status_label": "Confirmat al document"}]
    # J: informative/informational -> Informatiu.
    assert unsupported["obligation_label"] == "Informatiu"
    assert unsupported["category_label"] == "Assegurança"
    # K: value_not_in_quote -> etiqueta d'usuari, sense tocar l'evidencia font.
    assert unsupported["evidence_label"] == "Al document, valor per confirmar"
    assert unsupported["verification_label"] == "Cal revisar"
    assert unsupported["needs_review"] is True
    assert [dict(r) for r in (await session.execute(
        sa.select(RequirementEvidence.__table__))).mappings()] == evidence_before
    stored = await session.scalar(sa.select(RequirementEvidence.claim_support_json).where(
        RequirementEvidence.requirement_id == extracted["rows"][NEEDS_REVIEW].id))
    assert stored["claims"] == {"import_minim": "value_not_in_quote"}
    # L: cada evidencia apunta al seu document i pagina originals.
    for title, item in by_title.items():
        source = extracted["rows"][title]
        assert item["source_refs"] == [{"kind": "extracted_requirement", "id": str(source.id)}]
        assert [(e["page"], e["quote"]) for e in item["evidence"]] == [(27, QUOTE_27)]
        assert item["documents"] == ["PCAP.pdf"] and item["pages"] == [27]
    # Cap codi tecnic a la resposta.
    text = json.dumps(body)
    for word in FORBIDDEN:
        assert word not in text, word

    detail = await client_a.get(f"{base_url}/requirements/{ordinary['canonical_id']}")
    assert detail.status_code == 200 and detail.json() == ordinary


async def test_el_resum_compta_per_estat_i_provinenca(session, extracted, tenant_a, client_a):
    user, company = tenant_a
    base_url = f"/api/v1/companies/{company.id}/tenders/{extracted['tender_id']}/review"
    before = (await client_a.get(f"{base_url}/overview")).json()
    assert (before["review_started"], before["pending_materialisation"]) == (False, 2)

    await client_a.post(f"{base_url}/start")
    after = (await client_a.get(f"{base_url}/overview")).json()

    assert (after["review_started"], after["total_requirements"],
            after["pending_materialisation"], after["needs_review_count"]) == (True, 2, 0, 1)
    counts = {c["code"]: (c["label"], c["count"]) for c in after["verification_counts"]}
    assert counts["needs_review"] == ("Cal revisar", 1)
    assert counts["unreviewed"] == ("Pendent de revisar", 1)
    assert after["provenance_counts"] == [
        {"code": "automated", "label": "Extret automàticament", "count": 2}]
    assert "chunk" not in json.dumps(after)


def test_etiquetes_d_obligacio():
    label = review_view.obligation_label
    assert label("mandatory", "mandatory") == "Obligatori"
    assert label("mandatory", "conditional") == "Condicional"
    assert label("mandatory", "alternative") == "Alternatiu"
    assert label("informative", "informational") == "Informatiu"
    assert label("scored", "optional") == "Valorable"
    assert label("unknown", "unknown") == "Per determinar"


# --------------------------------------------------------------------------- #
# Garanties de la base (PostgreSQL)                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
@pytest.mark.parametrize(("table", "column", "value"), [
    ("review_items", "kind", "inventat"),
    ("review_items", "verification_status", "aprovat"),
    ("review_items", "award_role", "puntua_molt"),
    ("review_items", "origin_hint", "ia"),
])
async def test_pg_els_enums_els_imposa_la_base(session, extracted, tenant_a, table, column, value):
    user, company = tenant_a
    await _start(session, company, user, extracted["tender_id"])
    item_id = await session.scalar(sa.select(ReviewItem.id).limit(1))

    with pytest.raises(DBAPIError):
        await session.execute(
            sa.text(f"UPDATE {table} SET {column} = :value WHERE id = :id"),
            {"value": value, "id": item_id},
        )
    await session.rollback()


@pytest.mark.postgres
@pytest.mark.parametrize("statement", [
    "UPDATE review_item_revisions SET action = 'inventat'",
    "UPDATE review_item_sources SET link_role = 'inventat'",
    "UPDATE review_item_sources SET award_criterion_id = NULL, requirement_id = NULL",
])
async def test_pg_els_checks_de_fonts_i_historial(session, extracted, tenant_a, statement):
    user, company = tenant_a
    await _start(session, company, user, extracted["tender_id"])

    with pytest.raises(DBAPIError):
        await session.execute(sa.text(statement))
    await session.rollback()


@pytest.mark.postgres
@pytest.mark.parametrize("statement", [
    "UPDATE review_item_revisions SET note = 'reescrit'",
    "DELETE FROM review_item_revisions",
])
async def test_pg_l_historial_es_nomes_d_afegir(session, extracted, tenant_a, statement):
    user, company = tenant_a
    await _start(session, company, user, extracted["tender_id"])

    with pytest.raises(DBAPIError, match="nomes d'afegir"):
        await session.execute(sa.text(statement))
    await session.rollback()
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItemRevision)) == 2


@pytest.mark.postgres
async def test_pg_una_run_amb_historial_de_revisio_no_s_esborra_sola(
    session, extracted, tenant_a
):
    """Canvi de comportament documentat: abans esborrar una run s'enduia els
    requisits en cascada; ara, si una empresa hi te historial, falla."""
    user, company = tenant_a
    await _start(session, company, user, extracted["tender_id"])

    with pytest.raises(IntegrityError):
        await session.execute(sa.text("DELETE FROM requirement_extraction_runs WHERE id = :id"),
                              {"id": extracted["run"].id})
    await session.rollback()
    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 2


@pytest.mark.postgres
@pytest.mark.parametrize("target", ["tender", "company"])
async def test_pg_esborrar_licitacio_o_empresa_s_emporta_la_revisio(
    session, extracted, tenant_a, target
):
    user, company = tenant_a
    await _start(session, company, user, extracted["tender_id"])
    table, row_id = (("tenders", extracted["tender_id"]) if target == "tender"
                     else ("companies", company.id))

    await session.execute(sa.text(f"DELETE FROM {table} WHERE id = :id"), {"id": row_id})
    await session.commit()

    for model in (ReviewItem, ReviewItemSource, ReviewItemRevision):
        assert await session.scalar(sa.select(sa.func.count()).select_from(model)) == 0
