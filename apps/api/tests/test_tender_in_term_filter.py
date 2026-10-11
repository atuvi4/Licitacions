"""«En termini» es un estat accionable derivat, no l'estat cru de la font.

Error real (revisio amb navegador de 3B-2, F2): el filtre «En termini» enviava
`status=open` (PLACSP `PUB`, «publicada») i tornava licitacions vencudes, que la
mateixa targeta pintava «Fora de termini». Invariant: `in_term=true` vol dir
estat compatible (publicada) I termini conegut posterior a ARA, per instant
exacte (no per dia natural). Termini desconegut: mai «en termini». Un estat final
de la font (anul·lada, adjudicada...) mai hi entra.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.enums import TenderStatus
from tests.test_matching_api import make_source, make_tender
from tests.test_matching_ux import feed


async def _ids(client, company, **params) -> set[str]:
    body = await feed(client, company, mode="all", limit=100, **params)
    return {item["tender"]["title"] for item in body["items"]}


async def test_publicada_amb_termini_05_10_2026_no_surt_a_en_termini(db, client_a, tenant_a):
    """L'acceptance del propietari: `PUB` amb termini 05/10/2026, revisada despres."""
    _, company = tenant_a
    source = await make_source(db)
    expired = await make_tender(db, source, title="PUB vencuda 05-10", status=TenderStatus.OPEN)
    expired.deadline_at = datetime(2026, 10, 5, 14, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    await make_tender(db, source, title="PUB oberta", status=TenderStatus.OPEN, days_left=10)
    await db.commit()

    titles = await _ids(client_a, company, in_term="true")

    assert "PUB vencuda 05-10" not in titles
    assert titles == {"PUB oberta"}


async def test_el_venciment_es_per_instant_no_per_dia(db, client_a, tenant_a):
    """Venc fa una hora: el compte de dies diu «avui», pero ja no es en termini."""
    _, company = tenant_a
    source = await make_source(db)
    just_expired = await make_tender(db, source, title="Ha vencut fa una hora")
    just_expired.deadline_at = datetime.now(UTC) - timedelta(hours=1)
    still_open = await make_tender(db, source, title="Venc d aqui a una hora")
    still_open.deadline_at = datetime.now(UTC) + timedelta(hours=1)
    await db.commit()

    assert await _ids(client_a, company, in_term="true") == {"Venc d aqui a una hora"}


async def test_termini_desconegut_mai_es_en_termini(db, client_a, tenant_a):
    _, company = tenant_a
    source = await make_source(db)
    await make_tender(db, source, title="PUB sense termini", days_left=None)
    await make_tender(db, source, title="PUB amb termini", days_left=5)
    await db.commit()

    assert await _ids(client_a, company, in_term="true") == {"PUB amb termini"}


async def test_estats_finals_i_desconeguts_no_hi_entren_encara_que_el_termini_sigui_futur(
    db, client_a, tenant_a
):
    _, company = tenant_a
    source = await make_source(db)
    for status in (
        TenderStatus.CANCELLED,
        TenderStatus.AWARDED,
        TenderStatus.RESOLVED,
        TenderStatus.PENDING_AWARD,
        TenderStatus.UNKNOWN,
        TenderStatus.PRE_ANNOUNCEMENT,
    ):
        await make_tender(db, source, title=f"Estat {status.value}", status=status, days_left=10)
    await make_tender(db, source, title="Publicada i oberta", days_left=10)
    await db.commit()

    assert await _ids(client_a, company, in_term="true") == {"Publicada i oberta"}


async def test_sense_in_term_el_filtre_d_estat_cru_continua_igual(db, client_a, tenant_a):
    """L'estat cru es provenance: `status=open` torna les publicades, vencudes incloses."""
    _, company = tenant_a
    source = await make_source(db)
    expired = await make_tender(db, source, title="PUB vencuda")
    expired.deadline_at = datetime.now(UTC) - timedelta(days=3)
    await make_tender(db, source, title="PUB oberta", days_left=3)
    await db.commit()

    assert await _ids(client_a, company, status="open") == {"PUB vencuda", "PUB oberta"}


async def test_el_cataleg_public_tambe_enten_in_term(db, client_a, tenant_a):
    _, company = tenant_a
    source = await make_source(db)
    expired = await make_tender(db, source, title="PUB vencuda")
    expired.deadline_at = datetime.now(UTC) - timedelta(days=3)
    await make_tender(db, source, title="PUB oberta", days_left=3)
    await db.commit()

    response = await client_a.get("/api/v1/tenders?in_term=true&limit=100")
    assert response.status_code == 200, response.text
    assert {item["title"] for item in response.json()["items"]} == {"PUB oberta"}
