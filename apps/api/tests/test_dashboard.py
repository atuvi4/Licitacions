"""Tests del resum de dashboard i de la completitud del perfil."""

from __future__ import annotations

from datetime import date, timedelta

PDF = b"%PDF-1.4 evidencia dashboard"

EXPECTED_SECTIONS = {
    "general",
    "services",
    "territories",
    "cpv",
    "solvency",
    "certifications",
    "registrations",
    "insurances",
    "team",
    "experience",
    "documents",
}


def sections_of(body) -> dict:
    return {section["key"]: section for section in body["completeness"]["sections"]}


async def test_perfil_buit_te_puntuacio_baixa(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.get(f"/api/v1/companies/{company.id}/summary")
    assert response.status_code == 200
    body = response.json()

    assert body["company"]["legal_name"] == "Empresa A"
    sections = sections_of(body)
    assert set(sections) == EXPECTED_SECTIONS
    assert sum(section["weight"] for section in sections.values()) == 100
    # Nomes hi ha dades generals minimes: no pot semblar un perfil complet.
    assert body["completeness"]["score"] < 10
    assert all(section["status"] == "empty" for key, section in sections.items() if key != "general")


async def test_les_dades_generals_no_poden_dominar_la_puntuacio(client_a, tenant_a):
    """Encara que s'omplin TOTES les dades generals, el perfil no pot passar del 12%."""
    _, company = tenant_a
    await client_a.patch(
        f"/api/v1/companies/{company.id}",
        json={
            "legal_name": "Empresa A",
            "trade_name": "A",
            "tax_id": "B12345678",
            "legal_form": "SL",
            "incorporation_date": "2010-01-01",
            "email": "a@empresa-a.com",
            "phone": "+34 900 000 000",
            "country": "ES",
            "province": "Girona",
            "city": "Girona",
            "postal_code": "17001",
            "address": "Carrer 1",
        },
    )
    body = (await client_a.get(f"/api/v1/companies/{company.id}/summary")).json()
    sections = sections_of(body)
    assert sections["general"]["status"] == "complete"
    assert sections["general"]["points"] == 12
    assert body["completeness"]["score"] == 12


async def test_una_dada_sense_evidencia_es_parcial(client_a, tenant_a):
    """Regla central de la iteracio: sense document justificatiu, nomes 'parcial'."""
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"

    certification_id = (
        await client_a.post(f"{base}/certifications", json={"name": "ISO 9001"})
    ).json()["id"]

    sections = sections_of((await client_a.get(f"{base}/summary")).json())
    assert sections["certifications"]["requires_evidence"] is True
    assert sections["certifications"]["status"] == "partial"
    assert "sense evidencia" in sections["certifications"]["detail"]

    await client_a.post(
        f"{base}/documents",
        files={"file": ("iso9001.pdf", PDF, "application/pdf")},
        data={
            "category": "certification",
            "entity_type": "company_certification",
            "entity_id": certification_id,
            "relationship_type": "certificate",
        },
    )

    sections = sections_of((await client_a.get(f"{base}/summary")).json())
    assert sections["certifications"]["status"] == "complete"
    assert "tots amb evidencia" in sections["certifications"]["detail"]


async def test_experiencia_sense_evidencia_no_arriba_al_maxim(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    for index in range(3):
        await client_a.post(f"{base}/projects", json={"name": f"Projecte {index}"})

    sections = sections_of((await client_a.get(f"{base}/summary")).json())
    # Volum complet (3 projectes) pero cap acreditat: exactament la meitat.
    assert sections["experience"]["status"] == "partial"
    assert sections["experience"]["points"] == 5.0


async def test_la_puntuacio_puja_en_omplir_el_perfil(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    before = (await client_a.get(f"{base}/summary")).json()["completeness"]["score"]

    for name in ("Fibra", "CCTV", "Manteniment"):
        await client_a.post(f"{base}/services", json={"name": name})
    for code in ("45314320", "32412110", "35125300"):
        await client_a.post(f"{base}/cpv-preferences", json={"cpv_code": code})

    body = (await client_a.get(f"{base}/summary")).json()
    sections = sections_of(body)
    assert body["completeness"]["score"] > before
    assert sections["services"]["status"] == "complete"
    assert sections["cpv"]["status"] == "complete"
    assert body["counts"]["services"] == 3
    assert body["counts"]["cpv_preferences"] == 3


async def test_els_cpv_exclosos_no_compten(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    for code in ("45314320", "32412110", "35125300"):
        await client_a.post(f"{base}/cpv-preferences", json={"cpv_code": code, "excluded": True})

    sections = sections_of((await client_a.get(f"{base}/summary")).json())
    assert sections["cpv"]["status"] == "empty"


async def test_la_completitud_es_deterministica(client_a, tenant_a):
    _, company = tenant_a
    url = f"/api/v1/companies/{company.id}/summary"
    first = (await client_a.get(url)).json()["completeness"]
    second = (await client_a.get(url)).json()["completeness"]
    assert first == second


async def test_avisos_de_caducitat(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"

    expired = (date.today() - timedelta(days=3)).isoformat()
    soon = (date.today() + timedelta(days=20)).isoformat()
    far = (date.today() + timedelta(days=900)).isoformat()

    await client_a.post(f"{base}/certifications", json={"name": "Caducada", "expires_at": expired})
    await client_a.post(f"{base}/certifications", json={"name": "Aviat", "expires_at": soon})
    await client_a.post(f"{base}/certifications", json={"name": "Llunyana", "expires_at": far})

    body = (await client_a.get(f"{base}/summary")).json()
    alerts = {alert["label"]: alert for alert in body["expiry_alerts"]}

    assert "Llunyana" not in alerts
    assert alerts["Caducada"]["temporal_status"] == "expired"
    assert alerts["Caducada"]["days_left"] < 0
    assert alerts["Aviat"]["temporal_status"] == "expiring_soon"
    assert body["expiry_alerts"][0]["label"] == "Caducada"


async def test_avisos_inclouen_titulacions_i_polisses(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    soon = (date.today() + timedelta(days=15)).isoformat()

    person_id = (await client_a.post(f"{base}/people", json={"first_name": "Anna"})).json()["id"]
    await client_a.post(
        f"{base}/people/{person_id}/qualifications",
        json={"name": "Habilitacio", "expires_at": soon},
    )
    await client_a.post(
        f"{base}/insurances", json={"type": "civil_liability", "expires_at": soon}
    )

    body = (await client_a.get(f"{base}/summary")).json()
    tipus = {alert["entity_type"] for alert in body["expiry_alerts"]}
    assert {"person_qualification", "company_insurance"} <= tipus


async def test_el_resum_es_per_empresa(client_a, client_b, tenant_a, tenant_b):
    _, company_a = tenant_a
    _, company_b = tenant_b
    await client_b.post(f"/api/v1/companies/{company_b.id}/services", json={"name": "Alie"})

    body = (await client_a.get(f"/api/v1/companies/{company_a.id}/summary")).json()
    assert body["counts"]["services"] == 0
