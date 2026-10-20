"""`GET /workspaces/preparation-counts` (P2-09): els pendents de preparacio d'Avui en una peticio.

Abans el navegador demanava `final-readiness` per a cada candidatura oberta i en
comptava els blocadors. Ara ho fa el servidor amb la MATEIXA lectura
(`workspace_readiness.final_readiness`), i el resultat ha de ser identic al de la
crida individual. L'abast es el de «Pendents de preparacio»: no tancades i amb
`deadline_passed` fals, de l'empresa del context.

Tambe es comprova que la lectura de `final-readiness` es de nomes lectura: cap
INSERT/UPDATE/DELETE, cap flush, cap auditoria.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.workspace import BidWorkspace
from app.services import workspace_readiness
from tests.conftest import satisfy_final_gate
from tests.test_workspace_list_batch import counting, rich_fixture

COUNTS = "/api/v1/companies/{company}/workspaces/preparation-counts"
READINESS = "/api/v1/companies/{company}/workspaces/{workspace}/final-readiness"

#: Les candidatures d'A dins de l'abast al fixture ric, en l'ordre de la llista:
#: sense termini, termini futur, i la del lot obert (expedient fora de termini,
#: pero el termini que mana es el del lot). Fora: tancades, el lot retirat (sense
#: termini propi, el de l'expedient ja ha passat) i la de B.
IN_SCOPE = ["lot_open", "current", "bare"]


async def _blockers(client, workspace) -> int:
    response = await client.get(
        READINESS.format(company=workspace.company_id, workspace=workspace.id)
    )
    assert response.status_code == 200, response.text
    return sum(1 for finding in response.json()["findings"] if finding["severity"] == "blocker")


async def _counts(client, company_id) -> dict[str, int | None]:
    response = await client.get(COUNTS.format(company=company_id))
    assert response.status_code == 200, response.text
    return {item["workspace_id"]: item["pending"] for item in response.json()["items"]}


async def _fixture(db, tenant_a, tenant_b):
    ws = await rich_fixture(db, tenant_a, tenant_b)
    # Una de les obertes amb oferta i abast revisat: un recompte diferent de les altres.
    await satisfy_final_gate(db, ws["current"], tenant_a[0])
    await db.commit()
    return ws


async def test_el_recompte_es_el_de_final_readiness_per_a_cada_candidatura(
    db, client_a, tenant_a, tenant_b
):
    _, company = tenant_a
    ws = await _fixture(db, tenant_a, tenant_b)

    counts = await _counts(client_a, company.id)

    # Mateix ordre que la llista (`created_at` descendent) i nomes l'abast.
    assert list(counts) == [str(ws[key].id) for key in IN_SCOPE]
    for key in IN_SCOPE:
        assert counts[str(ws[key].id)] == await _blockers(client_a, ws[key]), key
    # El fixture distingeix els recomptes: la igualtat no es casual.
    assert len(set(counts.values())) > 1, counts


async def test_fora_de_l_abast_tancades_termini_passat_i_altres_empreses(
    db, client_a, client_b, tenant_a, tenant_b
):
    (_, company_a), (_, company_b) = tenant_a, tenant_b
    ws = await _fixture(db, tenant_a, tenant_b)

    counts = await _counts(client_a, company_a.id)
    for key in ("changed", "submitted", "withdrawn", "lot_removed"):
        assert str(ws[key].id) not in counts, key

    # La de B es seva: B la veu, A no.
    other = await db.scalar(
        sa.select(BidWorkspace.id).where(BidWorkspace.company_id == company_b.id)
    )
    assert str(other) not in counts
    assert list(await _counts(client_b, company_b.id)) == [str(other)]
    # A no pot demanar els recomptes de B.
    assert (await client_a.get(COUNTS.format(company=company_b.id))).status_code in (403, 404)


async def test_una_candidatura_que_falla_queda_sense_comptar_i_les_altres_continuen(
    db, client_a, tenant_a, tenant_b, monkeypatch
):
    """Un error de base de dades dins d'una lectura no trenca la sessio de les altres."""
    _, company = tenant_a
    ws = await _fixture(db, tenant_a, tenant_b)
    expected = await _counts(client_a, company.id)
    target = ws["current"].id
    original = workspace_readiness.final_readiness

    async def failing(session, workspace):
        if workspace.id == target:
            await session.execute(sa.text("SELECT * FROM taula_que_no_existeix"))
        return await original(session, workspace)

    monkeypatch.setattr(workspace_readiness, "final_readiness", failing)
    counts = await _counts(client_a, company.id)

    assert counts[str(target)] is None
    assert {key: value for key, value in counts.items() if key != str(target)} == {
        key: value for key, value in expected.items() if key != str(target)
    }
    assert all(value is not None for key, value in counts.items() if key != str(target))


async def test_final_readiness_es_de_nomes_lectura(
    db, engine, client_a, tenant_a, tenant_b, session_factory
):
    """Cap escriptura ni per HTTP (individual i per lots) ni a la sessio del servei."""
    _, company = tenant_a
    ws = await _fixture(db, tenant_a, tenant_b)
    audits = await db.scalar(sa.select(sa.func.count()).select_from(AuditLog))

    with counting(engine) as statements:
        for key in IN_SCOPE:
            await _blockers(client_a, ws[key])
        await _counts(client_a, company.id)
    writes = [
        statement
        for statement in statements
        if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}
    ]
    assert writes == []
    assert await db.scalar(sa.select(sa.func.count()).select_from(AuditLog)) == audits

    flushes: list[str] = []
    async with session_factory() as session:
        sa.event.listen(session.sync_session, "before_flush", lambda *_: flushes.append("flush"))
        for key in IN_SCOPE:
            workspace = await session.get(BidWorkspace, ws[key].id)
            await workspace_readiness.final_readiness(session, workspace)
            assert not session.new and not session.dirty and not session.deleted
    assert flushes == []


async def test_sense_candidatures_obertes_la_resposta_es_buida(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.get(COUNTS.format(company=company.id))
    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_la_ruta_no_col_lideix_amb_la_d_un_workspace(db, client_a, tenant_a, tenant_b):
    _, company = tenant_a
    ws = await _fixture(db, tenant_a, tenant_b)
    detail = await client_a.get(f"/api/v1/companies/{company.id}/workspaces/{ws['bare'].id}")
    assert detail.status_code == 200
    unknown = await client_a.get(f"/api/v1/companies/{company.id}/workspaces/{uuid.uuid4()}")
    assert unknown.status_code == 404


async def test_el_limit_de_la_fase_1_es_50(client_a, tenant_a):
    """Cada candidatura fa la seva lectura sencera en serie: com a molt la pagina d'Avui."""
    _, company = tenant_a
    url = COUNTS.format(company=company.id)
    assert (await client_a.get(url, params={"limit": 50})).status_code == 200
    assert (await client_a.get(url, params={"limit": 51})).status_code == 422
