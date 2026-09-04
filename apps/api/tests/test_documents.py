"""Tests de la biblioteca documental i de la traçabilitat dada -> evidencia."""

from __future__ import annotations

import hashlib

PDF = b"%PDF-1.4 contingut de prova"


async def test_pujada_calcula_hash_i_metadata(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.post(
        f"/api/v1/companies/{company.id}/documents",
        files={"file": ("Certificat Final.pdf", PDF, "application/pdf")},
        data={"category": "certification", "name": "Certificat ISO 9001"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["sha256"] == hashlib.sha256(PDF).hexdigest()
    assert body["file_size"] == len(PDF)
    assert body["mime_type"] == "application/pdf"
    assert body["category"] == "certification"
    # La metadata es propia i no depen del nom del fitxer.
    assert body["name"] == "Certificat ISO 9001"
    assert body["original_filename"] == "Certificat Final.pdf"
    # La clau interna d'storage no s'exposa a l'API.
    assert "storage_key" not in body


async def test_es_rebutgen_tipus_no_permesos(client_a, tenant_a):
    _, company = tenant_a
    response = await client_a.post(
        f"/api/v1/companies/{company.id}/documents",
        files={"file": ("virus.exe", b"MZ", "application/x-msdownload")},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"


async def test_descarrega_retorna_el_contingut(client_a, tenant_a):
    _, company = tenant_a
    document_id = (
        await client_a.post(
            f"/api/v1/companies/{company.id}/documents",
            files={"file": ("doc.pdf", PDF, "application/pdf")},
        )
    ).json()["id"]

    response = await client_a.get(
        f"/api/v1/companies/{company.id}/documents/{document_id}/download"
    )
    assert response.status_code == 200
    assert response.content == PDF
    assert "attachment" in response.headers["content-disposition"]


async def test_evidencia_dun_projecte(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"

    project_id = (
        await client_a.post(f"{base}/projects", json={"name": "Obra demo", "amount": "150000"})
    ).json()["id"]

    # Pujada i enllac en una sola operacio.
    document = await client_a.post(
        f"{base}/documents",
        files={"file": ("acta.pdf", PDF, "application/pdf")},
        data={
            "category": "project_evidence",
            "entity_type": "company_project",
            "entity_id": project_id,
            "relationship_type": "certificate_of_good_execution",
        },
    )
    assert document.status_code == 201
    assert len(document.json()["links"]) == 1

    evidence = await client_a.get(f"{base}/evidence/company_project/{project_id}")
    assert evidence.status_code == 200
    items = evidence.json()
    assert len(items) == 1
    assert items[0]["relationship_type"] == "certificate_of_good_execution"
    assert items[0]["document"]["name"] == "acta.pdf"


async def test_no_es_pot_duplicar_el_mateix_enllac(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    certification_id = (
        await client_a.post(f"{base}/certifications", json={"name": "ISO 9001"})
    ).json()["id"]
    document_id = (
        await client_a.post(
            f"{base}/documents", files={"file": ("iso.pdf", PDF, "application/pdf")}
        )
    ).json()["id"]

    payload = {
        "entity_type": "company_certification",
        "entity_id": certification_id,
        "relationship_type": "certificate",
    }
    first = await client_a.post(f"{base}/documents/{document_id}/links", json=payload)
    assert first.status_code == 201
    second = await client_a.post(f"{base}/documents/{document_id}/links", json=payload)
    assert second.status_code == 409


async def test_esborrar_lentitat_neteja_els_enllacos(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    project_id = (await client_a.post(f"{base}/projects", json={"name": "Obra"})).json()["id"]
    document_id = (
        await client_a.post(
            f"{base}/documents",
            files={"file": ("acta.pdf", PDF, "application/pdf")},
            data={"entity_type": "company_project", "entity_id": project_id},
        )
    ).json()["id"]

    assert (await client_a.delete(f"{base}/projects/{project_id}")).status_code == 204

    document = await client_a.get(f"{base}/documents/{document_id}")
    assert document.status_code == 200
    # El document sobreviu, pero l'enllac orfe desapareix.
    assert document.json()["links"] == []


async def test_esborrar_el_document_esborra_els_enllacos(client_a, tenant_a, storage):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    certification_id = (
        await client_a.post(f"{base}/certifications", json={"name": "ISO 9001"})
    ).json()["id"]
    document_id = (
        await client_a.post(
            f"{base}/documents",
            files={"file": ("iso.pdf", PDF, "application/pdf")},
            data={"entity_type": "company_certification", "entity_id": certification_id},
        )
    ).json()["id"]

    assert (await client_a.delete(f"{base}/documents/{document_id}")).status_code == 204

    evidence = await client_a.get(f"{base}/evidence/company_certification/{certification_id}")
    assert evidence.json() == []


async def test_edicio_de_metadata(client_a, tenant_a):
    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}"
    document_id = (
        await client_a.post(
            f"{base}/documents", files={"file": ("doc.pdf", PDF, "application/pdf")}
        )
    ).json()["id"]

    response = await client_a.patch(
        f"{base}/documents/{document_id}",
        json={"name": "Nom corregit", "expires_at": "2099-01-01"},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Nom corregit"
    assert response.json()["temporal_status"] == "valid"
    # El hash i la mida no son editables.
    assert response.json()["sha256"] == hashlib.sha256(PDF).hexdigest()


async def test_es_detecta_un_duplicat_pel_sha256(client_a, tenant_a):
    """El mateix contingut no s'ha de guardar dues vegades a la biblioteca."""
    _, company = tenant_a
    url = f"/api/v1/companies/{company.id}/documents"

    first = await client_a.post(url, files={"file": ("iso.pdf", PDF, "application/pdf")})
    assert first.status_code == 201
    original_id = first.json()["id"]

    # Mateix contingut, un altre nom de fitxer: segueix sent el mateix document.
    duplicate = await client_a.post(
        url, files={"file": ("copia-iso.pdf", PDF, "application/pdf")}
    )
    assert duplicate.status_code == 409
    error = duplicate.json()["error"]
    assert error["code"] == "duplicate_document"
    assert error["details"]["existing_document_id"] == original_id
    assert error["details"]["sha256"] == hashlib.sha256(PDF).hexdigest()

    # I no s'ha creat res.
    assert len((await client_a.get(url)).json()) == 1


async def test_es_pot_forcar_un_duplicat(client_a, tenant_a):
    _, company = tenant_a
    url = f"/api/v1/companies/{company.id}/documents"
    await client_a.post(url, files={"file": ("iso.pdf", PDF, "application/pdf")})

    forced = await client_a.post(
        url,
        files={"file": ("iso-v2.pdf", PDF, "application/pdf")},
        data={"allow_duplicate": "true"},
    )
    assert forced.status_code == 201
    assert len((await client_a.get(url)).json()) == 2


async def test_el_duplicat_es_per_empresa(client_a, client_b, tenant_a, tenant_b):
    """Dues empreses poden tenir el mateix fitxer sense interferir-se."""
    _, company_a = tenant_a
    _, company_b = tenant_b

    a = await client_a.post(
        f"/api/v1/companies/{company_a.id}/documents",
        files={"file": ("comu.pdf", PDF, "application/pdf")},
    )
    b = await client_b.post(
        f"/api/v1/companies/{company_b.id}/documents",
        files={"file": ("comu.pdf", PDF, "application/pdf")},
    )
    assert a.status_code == 201
    assert b.status_code == 201
    assert a.json()["sha256"] == b.json()["sha256"]
    assert a.json()["id"] != b.json()["id"]


async def test_es_pot_cercar_per_sha256(client_a, tenant_a):
    _, company = tenant_a
    url = f"/api/v1/companies/{company.id}/documents"
    digest = (
        await client_a.post(url, files={"file": ("iso.pdf", PDF, "application/pdf")})
    ).json()["sha256"]

    found = await client_a.get(url, params={"sha256": digest})
    assert len(found.json()) == 1
