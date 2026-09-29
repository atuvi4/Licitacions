"""«Totes» = les licitacions VISIBLES per a l'empresa, amb el pre-fit al costat si n'hi ha.

Error real (QA Palamós): una empresa sense cap pre-fit calculat veia «Totes 9»
(`summary.total_tenders`, les licitacions visibles) i una llista buida, perque
la llista partia dels pre-fits. Invariant: el comptador de «Totes» i la llista
de «Totes» parlen del mateix univers. Les vistes per estat continuen partint del
pre-fit. Una licitacio privada d'una altra empresa no hi apareix mai.
"""

from __future__ import annotations

import pytest

from app.matching.service import MatchingService
from tests.test_matching_api import configure_company, make_source, make_tender
from tests.test_matching_ux import NETEJA, feed

SUMMARY = "/api/v1/companies/{company}/matches/summary"


async def _summary(client, company):
    response = await client.get(SUMMARY.format(company=company.id))
    assert response.status_code == 200, response.text
    return response.json()


async def test_sense_cap_pre_fit_totes_llista_el_que_compta(db, client_a, tenant_a):
    """El cas de QA: licitacions visibles, zero pre-fits calculats."""
    _, company = tenant_a
    source = await make_source(db)
    palamos = await make_tender(db, source, title="Servei de telefonia Palamós")
    other = await make_tender(db, source, title="Neteja", cpv_codes=(NETEJA,))
    await db.commit()

    summary = await _summary(client_a, company)
    body = await feed(client_a, company)

    assert summary["total_tenders"] == 2
    assert body["total"] == summary["total_tenders"]
    assert {item["tender"]["id"] for item in body["items"]} == {str(palamos.id), str(other.id)}
    assert all(item["match"] is None for item in body["items"])  # «Sense calcular»
    assert summary["candidates"] == 0  # i per tant «0 oportunitats obertes» a la cerca


async def test_amb_pre_fit_parcial_totes_no_n_amaga_cap(db, client_a, tenant_a):
    _, company = tenant_a
    source = await make_source(db)
    first = await make_tender(db, source, title="Abans del calcul")
    await db.commit()
    await configure_company(db, company.id)
    await MatchingService(db).recalculate(company.id)
    later = await make_tender(db, source, title="Despres del calcul")
    await db.commit()

    summary = await _summary(client_a, company)
    body = await feed(client_a, company)

    assert body["total"] == summary["total_tenders"] == 2
    by_id = {item["tender"]["id"]: item for item in body["items"]}
    assert by_id[str(first.id)]["match"] is not None
    assert by_id[str(later.id)]["match"] is None


async def test_sense_cap_licitacio_el_compte_i_la_llista_son_zero(client_a, tenant_a):
    _, company = tenant_a

    summary = await _summary(client_a, company)
    body = await feed(client_a, company)

    assert summary["total_tenders"] == 0
    assert (body["total"], body["items"]) == (0, [])


async def test_un_filtre_explicit_sense_coincidencies_torna_zero_i_el_resum_no_canvia(
    db, client_a, tenant_a
):
    _, company = tenant_a
    source = await make_source(db)
    await make_tender(db, source, title="Servei de telefonia")
    await db.commit()

    body = await feed(client_a, company, q="inexistent")
    summary = await _summary(client_a, company)

    assert (body["total"], body["items"]) == (0, [])
    assert summary["total_tenders"] == 1  # l'univers; el filtre es visible a la UI


@pytest.mark.parametrize("sort", ["score", "deadline", "recent", "published", "amount"])
async def test_totes_funciona_amb_cada_ordenacio_sense_pre_fit(db, client_a, tenant_a, sort):
    _, company = tenant_a
    source = await make_source(db)
    for index in range(3):
        await make_tender(db, source, title=f"Licitacio {index}")
    await db.commit()

    body = await feed(client_a, company, sort=sort, limit=2)
    rest = await feed(client_a, company, sort=sort, limit=2, offset=2)

    assert body["total"] == rest["total"] == 3
    ids = [i["tender"]["id"] for i in body["items"]] + [i["tender"]["id"] for i in rest["items"]]
    assert len(ids) == len(set(ids)) == 3  # paginacio estable, sense repeticions


async def test_les_vistes_per_estat_continuen_partint_del_pre_fit(db, client_a, tenant_a):
    _, company = tenant_a
    source = await make_source(db)
    await make_tender(db, source, title="Sense pre-fit")
    await db.commit()

    for status in ("candidate", "low_fit", "excluded", "insufficient_data", "closed"):
        body = await feed(client_a, company, match_status=status)
        assert (body["total"], body["items"]) == (0, []), status
    assert (await feed(client_a, company, min_score=0))["total"] == 0


async def test_una_licitacio_privada_d_una_altra_empresa_no_hi_apareix(
    db, client_a, client_b, tenant_a, tenant_b
):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    source = await make_source(db)
    public = await make_tender(db, source, title="Publica")
    private_b = await make_tender(db, source, title="Privada de B")
    private_b.owner_company_id = company_b.id
    await db.commit()

    body_a = await feed(client_a, company_a)
    summary_a = await _summary(client_a, company_a)
    body_b = await feed(client_b, company_b)

    assert [i["tender"]["id"] for i in body_a["items"]] == [str(public.id)]
    assert body_a["total"] == summary_a["total_tenders"] == 1
    assert {i["tender"]["id"] for i in body_b["items"]} == {str(public.id), str(private_b.id)}
