"""Gold de la classificacio amb els documents REALS de l'Escala (Telecomunicacions 2027).

Els documents son els de la carpeta de la licitacio (8 fitxers, amb "Resta
Documentació/"), rebuts com un paquet ZIP. No son al repositori: es busquen a
`CLASSIFICATION_GOLD_LESCALA_ZIP` o a `var/gold/lescala-2026.zip`, i si no hi son
el test se salta. Les mateixes asserts corren sempre amb el bessó sintetic
(`test_document_classification_api.py::test_the_lescala_twin_matches_the_gold`).

Aqui, a mes, es comprova que cada fitxer es exactament el del gold (SHA-256):
un gold que passa amb uns altres fitxers no demostra res.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from tests.classification_gold import assert_matches_gold, load_gold

pytestmark = pytest.mark.asyncio

API_ROOT = Path(__file__).resolve().parents[1]
GOLD = load_gold("lescala-2026")


def _archive() -> Path:
    configured = os.environ.get(GOLD["archive"]["env"])
    return Path(configured) if configured else API_ROOT / GOLD["archive"]["default_location"]


@pytest.mark.skipif(not _archive().is_file(), reason="els documents reals de l'Escala no hi son")
async def test_the_real_lescala_documents_match_the_gold(client_a, tenant_a):
    _, company = tenant_a
    created = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders",
        json={"client_token": str(uuid.uuid4()), "title": "Telecomunicacions 2027"},
    )
    tender_id = created.json()["tender"]["id"]

    response = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{tender_id}/receptions/zip",
        files={"file": ("lescala-2026.zip", _archive().read_bytes(), "application/zip")},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    reception = body["reception"]
    assert (reception["item_count"], reception["accepted_count"], reception["skipped_count"]) == (
        8,
        8,
        0,
    )
    assert_matches_gold(body["classification"], GOLD, check_sha256=True)

    # La cita que demostra que falta el DOC-04 es a la primera pagina del PPT.
    findings = body["classification"]["findings"]
    missing = next(f for f in findings if f["code"] == "referenced_document_missing")
    first = missing["detail"]["references"][0]
    assert (first["path"], first["page"]) == ("PPT (1).pdf", 1)
    assert "inventaris vinculats al DOC-04" in first["excerpt"]
