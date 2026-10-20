"""El Radar d'Avui (P2-05/P2-09): recomptes del perfil al resum i candidates noves.

* `MatchSummary.profile_counts` porta els tres recomptes que Avui feia servir de
  `GET /summary`, amb els MATEIXOS valors: Avui ja no ha de demanar el resum del
  perfil sencer.
* `exclude_open_workspaces` treu del feed (abans de paginar i de comptar) les
  licitacions que ja tenen una candidatura OBERTA de l'empresa, i
  `open_candidates_with_workspace` diu quantes en treu amb el filtre exacte d'Avui.
"""

from __future__ import annotations

from app.matching.service import MatchingService
from app.models.cpv import CompanyCPVPreference
from app.models.enums import TerritoryType, WorkspaceStatus
from app.models.profile import CompanyTerritory
from app.models.workspace import BidWorkspace
from tests.test_matching_api import (
    SANITAT,
    TELECOM,
    configure_company,
    ensure_cpv_codes,
    make_source,
    make_tender,
)
from tests.test_workspace_list_batch import counting

SUMMARY = "/api/v1/companies/{company}/matches/summary"
PROFILE = "/api/v1/companies/{company}/summary"
MATCHES = "/api/v1/companies/{company}/matches"
#: La crida de «Candidates del Radar» d'Avui.
AVUI = {"match_status": "candidate", "sort": "deadline", "only_open": "true", "limit": 5}


async def _json(client, url, **params):
    response = await client.get(url, params=params or None)
    assert response.status_code == 200, response.text
    return response.json()


def _profile_counts(summary: dict) -> dict:
    counts = summary["counts"]
    return {
        "cpv_preferences": counts["cpv_preferences"],
        "territories": counts["territories"],
        "contract_preferences": counts["contract_preferences"],
    }


# --------------------------------------------------------------------------- #
# profile_counts = /summary.counts                                               #
# --------------------------------------------------------------------------- #


async def test_profile_counts_son_els_de_summary_amb_dades_d_una_altra_empresa_al_costat(
    db, client_a, client_b, tenant_a, tenant_b
):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    await configure_company(db, company_a.id)
    # Una preferencia inactiva i un territori mes: `build_summary` compta files.
    await ensure_cpv_codes(db, SANITAT)
    db.add(
        CompanyCPVPreference(
            company_id=company_a.id, cpv_code=SANITAT, priority=1, active=False, excluded=True
        )
    )
    db.add(
        CompanyTerritory(
            company_id=company_a.id,
            name="Lleida",
            territory_type=TerritoryType.PROVINCE,
            code="ES513",
            priority=5,
            willing_to_work=False,
        )
    )
    await db.commit()
    await configure_company(db, company_b.id, cpv=(TELECOM, SANITAT), capacity=False)

    for client, company in ((client_a, company_a), (client_b, company_b)):
        radar = await _json(client, SUMMARY.format(company=company.id))
        profile = await _json(client, PROFILE.format(company=company.id))
        assert radar["profile_counts"] == _profile_counts(profile)

    radar_a = await _json(client_a, SUMMARY.format(company=company_a.id))
    assert radar_a["profile_counts"] == {
        "cpv_preferences": 2,
        "territories": 2,
        "contract_preferences": 1,
    }


async def test_profile_counts_sense_preferencies_son_zeros_com_a_summary(
    db, client_a, tenant_a, tenant_b
):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    await configure_company(db, company_b.id)

    radar = await _json(client_a, SUMMARY.format(company=company_a.id))
    profile = await _json(client_a, PROFILE.format(company=company_a.id))

    assert radar["profile_counts"] == _profile_counts(profile)
    assert radar["profile_counts"] == {
        "cpv_preferences": 0,
        "territories": 0,
        "contract_preferences": 0,
    }


# --------------------------------------------------------------------------- #
# Candidates noves: exclude_open_workspaces                                     #
# --------------------------------------------------------------------------- #


async def _radar_with_workspaces(db, tenant_a, tenant_b):
    """Quatre candidates obertes d'A: una amb candidatura oberta d'A, una amb una
    de tancada d'A, una amb candidatura de B i una sense res. I una NO candidata
    amb candidatura oberta d'A, que no ha de comptar enlloc."""
    (user_a, company_a), (user_b, company_b) = tenant_a, tenant_b
    source = await make_source(db)
    tenders = {
        name: await make_tender(db, source, title=f"Cablejat a Girona {name}", days_left=days)
        for days, name in enumerate(("open_ws", "closed_ws", "other_ws", "plain"), start=10)
    }
    tenders["not_candidate"] = await make_tender(
        db, source, title="Hospital", cpv_codes=(SANITAT,), nuts_code="ES513", place="Lleida"
    )
    await db.commit()
    await configure_company(db, company_a.id)
    await MatchingService(db).recalculate(company_a.id)

    def workspace(company, user, tender, status):
        return BidWorkspace(
            company_id=company.id,
            tender_id=tender.id,
            status=status,
            created_by=user.id,
        )

    db.add_all(
        [
            workspace(company_a, user_a, tenders["open_ws"], WorkspaceStatus.ANALYSIS),
            workspace(company_a, user_a, tenders["closed_ws"], WorkspaceStatus.WITHDRAWN),
            workspace(company_b, user_b, tenders["other_ws"], WorkspaceStatus.PREPARING_BID),
            workspace(company_a, user_a, tenders["not_candidate"], WorkspaceStatus.ANALYSIS),
        ]
    )
    await db.commit()
    return tenders


async def test_exclude_open_workspaces_treu_nomes_les_que_tenen_candidatura_oberta(
    db, client_a, tenant_a, tenant_b
):
    _, company = tenant_a
    tenders = await _radar_with_workspaces(db, tenant_a, tenant_b)
    url = MATCHES.format(company=company.id)

    before = await _json(client_a, url, **AVUI)
    after = await _json(client_a, url, **AVUI, exclude_open_workspaces="true")

    ids_before = [item["tender"]["id"] for item in before["items"]]
    ids_after = [item["tender"]["id"] for item in after["items"]]
    assert before["total"] == 4
    assert after["total"] == 3
    assert str(tenders["open_ws"].id) in ids_before
    assert str(tenders["open_ws"].id) not in ids_after
    # Una candidatura tancada no l'exclou; la d'una altra empresa tampoc.
    assert str(tenders["closed_ws"].id) in ids_after
    assert str(tenders["other_ws"].id) in ids_after
    excluded = str(tenders["open_ws"].id)
    assert ids_after == [tender_id for tender_id in ids_before if tender_id != excluded]

    # Abans de paginar: amb una pagina d'un sol element el total segueix sent de les noves.
    page = await _json(client_a, url, **{**AVUI, "limit": 1}, exclude_open_workspaces="true")
    assert page["total"] == 3
    assert [item["tender"]["id"] for item in page["items"]] == ids_after[:1]

    summary = await _json(client_a, SUMMARY.format(company=company.id))
    assert summary["open_candidates_with_workspace"] == 1
    assert summary["open_candidates_with_workspace"] == before["total"] - after["total"]


async def test_sense_el_parametre_el_feed_es_el_d_abans(db, client_a, tenant_a, tenant_b):
    _, company = tenant_a
    await _radar_with_workspaces(db, tenant_a, tenant_b)
    url = MATCHES.format(company=company.id)

    for params in (AVUI, {}, {"match_status": "candidate"}, {"min_score": 1}):
        omitted = await _json(client_a, url, **params)
        explicit = await _json(client_a, url, **params, exclude_open_workspaces="false")
        assert omitted == explicit, params
    # «Totes» (sense filtre de pre-fit) inclou les cinc licitacions visibles.
    assert (await _json(client_a, url))["total"] == 5


async def test_open_candidates_with_workspace_es_zero_sense_candidatures(db, client_a, tenant_a):
    _, company = tenant_a
    source = await make_source(db)
    await make_tender(db, source, title="Cablejat a Girona")
    await db.commit()
    await configure_company(db, company.id)
    await MatchingService(db).recalculate(company.id)

    summary = await _json(client_a, SUMMARY.format(company=company.id))
    assert summary["candidates"] == 1
    assert summary["open_candidates_with_workspace"] == 0


async def test_el_resum_del_radar_fa_una_sola_sentencia_mes(db, engine, client_a, tenant_a):
    """Els recomptes del perfil i les candidates amb candidatura: UNA sentencia."""
    _, company = tenant_a
    await configure_company(db, company.id)
    with counting(engine) as statements:
        await _json(client_a, SUMMARY.format(company=company.id))
    extra = [
        statement
        for statement in statements
        if "company_cpv_preferences" in statement and "bid_workspaces" in statement
    ]
    print(f"GET /matches/summary: {len(statements)} SQL")
    assert len(extra) == 1
