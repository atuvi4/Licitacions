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


#: El DOC-04 real, rebut DESPRES del paquet inicial. Tampoc no es al repositori: es busca a
#: `CLASSIFICATION_GOLD_LESCALA_DOC04` o a `var/gold/lescala-2026-doc04.pdf`.
DOC04_ENV = "CLASSIFICATION_GOLD_LESCALA_DOC04"
DOC04_SHA = "84990c5d8bfb884413d730e1f7ffa94fb1d6ad8dac3796c1504dc00d67dce537"


def _doc04() -> Path:
    configured = os.environ.get(DOC04_ENV)
    return Path(configured) if configured else API_ROOT / "var" / "gold" / "lescala-2026-doc04.pdf"


@pytest.mark.skipif(
    not (_archive().is_file() and _doc04().is_file()),
    reason="el paquet real o el DOC-04 real de l'Escala no hi son",
)
async def test_the_real_doc04_arriving_later_clears_the_missing_document(client_a, tenant_a):
    """La historia no es reescriu: el paquet inicial continua sense DOC-04 (el fitxer amb el
    seu nom es el DOC-03). El DOC-04 real arriba despres, sol, i la passada seguent ja no el
    troba a faltar; el fals continua a la licitacio i auditable."""
    import hashlib

    data = _doc04().read_bytes()
    assert hashlib.sha256(data).hexdigest() == DOC04_SHA, "no es el DOC-04 publicat"
    _, company = tenant_a
    created = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders",
        json={"client_token": str(uuid.uuid4()), "title": "Telecomunicacions 2027"},
    )
    base = f"/api/v1/companies/{company.id}/tenders/{created.json()['tender']['id']}"
    first = (
        await client_a.post(
            f"{base}/receptions/zip",
            files={"file": ("lescala-2026.zip", _archive().read_bytes(), "application/zip")},
        )
    ).json()["classification"]
    assert_matches_gold(first, GOLD, check_sha256=True)
    codes = [f["code"] for f in first["findings"]]
    assert "referenced_document_missing" in codes and "duplicate_text" in codes

    name = "04_Annex_Mestre_Inventaris_Ubicacions_FTTH_Telefonia (definitiu).pdf"
    uploaded = await client_a.post(
        f"{base}/documents",
        files={"file": (name, data, "application/pdf")},
        data={"document_type": "other"},
    )
    assert uploaded.status_code == 201, uploaded.text
    second = (await client_a.post(f"{base}/document-classification")).json()

    assert second["run"]["id"] != first["run"]["id"]
    assert second["run"]["document_count"] == 9
    codes = [f["code"] for f in second["findings"]]
    assert "referenced_document_missing" not in codes
    documents = {d["path"]: d for d in second["documents"]}
    real = next(d for path, d in documents.items() if "definitiu" in path)
    assert (real["role"], real["sha256"]) == ("inventory_annex", DOC04_SHA)
    # El fals del paquet inicial no desapareix ni canvia: continua sent una copia del DOC-03.
    fake = next(d for d in GOLD["documents"] if d["sha256"] != DOC04_SHA and "04_" in d["path"])
    assert fake["path"] in {d["path"] for d in second["documents"]}
    assert "duplicate_text" in codes
