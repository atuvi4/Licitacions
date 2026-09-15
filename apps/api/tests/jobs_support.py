"""Ajudes per als tests que fan servir la cua de feina.

La feina llarga ja no s'executa dins de la peticio HTTP: el test demana la
feina com ho faria la interficie i, despres, executa el worker com un proces
separat, amb el proveidor fals i el mateix motor de la suite.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.ai.providers import fake
from app.jobs.worker import Worker


async def run_worker(session_factory, *, max_jobs: int = 20, **options: Any) -> list:
    """Executa jobs fins que la cua queda buida. Torna els jobs executats."""
    options.setdefault("provider_factory", fake.FakeProvider)
    options.setdefault("heartbeat_seconds", 3600)
    worker = Worker(session_factory, **options)
    executed = []
    for _ in range(max_jobs):
        job = await worker.run_once()
        if job is None:
            break
        executed.append(job)
    return executed


async def request_analysis(
    client: AsyncClient,
    company_id,
    tender_id,
    session_factory,
    *,
    json: dict | None = None,
    run: bool = True,
):
    """Preview, confirmacio i, si cal, worker. Torna la peticio final.

    Es el cami de la interficie: cap analisi es demana sense un preview confirmat.
    """
    response = await confirm_new_quote(client, company_id, tender_id, json=json)
    assert response.status_code in (200, 202), response.text
    if not run:
        return response
    await run_worker(session_factory)
    request_id = response.json()["id"]
    final = await client.get(f"/api/v1/companies/{company_id}/job-requests/{request_id}")
    assert final.status_code == 200, final.text
    return final


async def create_quote(client: AsyncClient, company_id, tender_id, *, json: dict | None = None):
    response = await client.post(
        f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis-quotes", json=json or {}
    )
    assert response.status_code == 201, response.text
    return response.json()


async def confirm_quote(client: AsyncClient, company_id, quote_id):
    return await client.post(f"/api/v1/companies/{company_id}/analysis-quotes/{quote_id}/confirm")


async def confirm_new_quote(
    client: AsyncClient, company_id, tender_id, *, json: dict | None = None
):
    """Preview i confirmacio immediata. Torna la resposta de la confirmacio."""
    quote = await create_quote(client, company_id, tender_id, json=json)
    return await confirm_quote(client, company_id, quote["id"])


async def request_processing(
    client: AsyncClient,
    company_id,
    tender_id,
    session_factory,
    *,
    json: dict | None = None,
    run: bool = True,
):
    response = await client.post(
        f"/api/v1/companies/{company_id}/tenders/{tender_id}/job-requests/process-documents",
        json=json or {},
    )
    assert response.status_code in (200, 202), response.text
    if not run:
        return response
    await run_worker(session_factory)
    request_id = response.json()["id"]
    final = await client.get(f"/api/v1/companies/{company_id}/job-requests/{request_id}")
    assert final.status_code == 200, final.text
    return final
