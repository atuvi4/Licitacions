"""El resum del pre-fit distingeix «no calculat» de «calculat sense candidates».

Error real (revisio amb navegador de 3B-2, F1/F3): Avui deia «0 candidates: cap
coincidencia» i Oportunitats «podeu ampliar els CPV» amb ZERO files de
`company_tender_matches`. El resum nomes exposava recomptes per estat, i el
frontend deduia la causa del perfil. Invariant: `evaluated_count` diu quantes
licitacions visibles tenen pre-fit calculat per a l'empresa; amb 0, la UI
nomes pot dir que el Radar encara no s'ha calculat.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.matching.service import MatchingService
from app.models.matching import CompanyTenderMatch
from tests.test_matching_api import configure_company, make_source, make_tender

SUMMARY = "/api/v1/companies/{company}/matches/summary"


async def _summary(client, company):
    response = await client.get(SUMMARY.format(company=company.id))
    assert response.status_code == 200, response.text
    return response.json()


async def test_sense_cap_avaluacio_evaluated_count_es_zero(db, client_a, tenant_a):
    """El cas de la revisio: perfil complet, licitacions, cap pre-fit calculat."""
    _, company = tenant_a
    source = await make_source(db)
    await make_tender(db, source, title="Servei de telefonia")
    await make_tender(db, source, title="Cablejat")
    await db.commit()
    await configure_company(db, company.id)

    summary = await _summary(client_a, company)

    assert summary["total_tenders"] == 2
    assert summary["evaluated_count"] == 0
    assert summary["candidates"] == 0


async def test_avaluades_sense_candidates(db, client_a, tenant_a):
    _, company = tenant_a
    source = await make_source(db)
    await make_tender(
        db,
        source,
        title="Neteja a Lleida",
        cpv_codes=("90910000",),
        nuts_code="ES513",
        place="Lleida",
    )
    await db.commit()
    await configure_company(db, company.id)
    await MatchingService(db).recalculate(company.id)

    summary = await _summary(client_a, company)

    assert summary["evaluated_count"] == 1
    assert summary["candidates"] == 0


async def test_avaluades_amb_candidates(db, client_a, tenant_a):
    _, company = tenant_a
    source = await make_source(db)
    await make_tender(db, source, title="Cablejat a Girona")
    await make_tender(db, source, title="Mes cablejat a Girona")
    await db.commit()
    await configure_company(db, company.id)
    await MatchingService(db).recalculate(company.id)

    summary = await _summary(client_a, company)

    assert summary["evaluated_count"] == 2
    assert summary["candidates"] == 2


async def test_avaluades_amb_dades_insuficients(db, client_a, tenant_a):
    _, company = tenant_a
    source = await make_source(db)
    await make_tender(
        db, source, title="Sense metadades", cpv_codes=(), nuts_code=None, place=None, amount=None
    )
    await db.commit()
    await configure_company(db, company.id)
    await MatchingService(db).recalculate(company.id)

    summary = await _summary(client_a, company)

    assert summary["evaluated_count"] == 1
    assert summary["insufficient_data"] == 1
    assert summary["candidates"] == 0


async def test_evaluated_count_nomes_compta_licitacions_visibles(db, client_a, tenant_a, tenant_b):
    """Un pre-fit sobre una licitacio que ja no es visible no compta com a avaluada."""
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    source = await make_source(db)
    public = await make_tender(db, source, title="Publica")
    later_private = await make_tender(db, source, title="Ara privada de B")
    await db.commit()
    await configure_company(db, company_a.id)
    await MatchingService(db).recalculate(company_a.id)
    later_private.owner_company_id = company_b.id
    await db.commit()

    summary = await _summary(client_a, company_a)

    assert public.id is not None
    assert summary["total_tenders"] == 1
    assert summary["evaluated_count"] == 1


async def test_high_fit_i_stale_tambe_nomes_compten_licitacions_visibles(
    db, client_a, tenant_a, tenant_b
):
    """Coherencia del resum: cap metrica compta pre-fits de licitacions no visibles."""
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    source = await make_source(db)
    await make_tender(db, source, title="Cablejat a Girona")
    later_private = await make_tender(db, source, title="Cablejat privat de B")
    await db.commit()
    await configure_company(db, company_a.id)
    await MatchingService(db).recalculate(company_a.id)
    # Tots dos pre-fits queden obsolets (regles antigues) i amb encaix alt.
    await db.execute(
        sa.update(CompanyTenderMatch)
        .where(CompanyTenderMatch.company_id == company_a.id)
        .values(rules_version="regles-antigues", score=95)
    )
    later_private.owner_company_id = company_b.id
    await db.commit()

    summary = await _summary(client_a, company_a)

    assert summary["evaluated_count"] == 1
    assert summary["high_fit"] == 1
    assert summary["stale"] == 1
