"""Notes de la candidatura: corregir-les i esborrar-les (Bloc 1).

Que es demostra:

* `PATCH .../notes/{id}` canvia el text i desa `updated_at`; la resposta el porta.
* `DELETE .../notes/{id}` l'esborra de manera definitiva (204) i la llista ja no la
  te.
* Totes dues deixen una fila d'auditoria amb els identificadors i SENSE el text.
* La nota d'una altra candidatura es 404 (mai es toca), i un rol de lectura no hi
  pot fer res (403). La guarda de la decisio (nomes un NO-GO tanca) es a
  `test_workspace_go_gate.py`, com la de crear-les.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from app.models.audit import AuditLog
from app.models.enums import AuditAction, MembershipRole
from app.models.workspace import WorkspaceNote
from tests.conftest import login
from tests.test_bid_workspace import add_member
from tests.test_workspace_go_gate import _base, _tender, workspace  # noqa: F401 - fixture


async def _note(client, workspace_, text="Primera versio") -> dict:  # noqa: F811
    response = await client.post(f"{_base(workspace_)}/notes", json={"text": text})
    assert response.status_code == 200, response.text
    return response.json()


async def test_corregir_una_nota_canvia_el_text_i_desa_la_data(
    db, client_a, workspace  # noqa: F811
):
    note = await _note(client_a, workspace)
    assert note["updated_at"] is None

    response = await client_a.patch(
        f"{_base(workspace)}/notes/{note['id']}", json={"text": "Versio corregida"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "Versio corregida"
    assert body["updated_at"] is not None
    assert body["created_at"] == note["created_at"]
    listed = (await client_a.get(f"{_base(workspace)}/notes")).json()
    assert [n["text"] for n in listed] == ["Versio corregida"]
    audit = (
        await db.scalars(
            sa.select(AuditLog).where(
                AuditLog.entity_type == "workspace_note", AuditLog.action == AuditAction.UPDATE
            )
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].summary == f"candidatura={workspace.id}"
    assert "corregida" not in (audit[0].summary or "")


async def test_esborrar_una_nota_es_definitiu_i_queda_a_l_auditoria(
    db, client_a, workspace  # noqa: F811
):
    note = await _note(client_a, workspace, "Nota repetida")

    response = await client_a.delete(f"{_base(workspace)}/notes/{note['id']}")

    assert response.status_code == 204, response.text
    assert (await client_a.get(f"{_base(workspace)}/notes")).json() == []
    assert await db.scalar(sa.select(sa.func.count()).select_from(WorkspaceNote)) == 0
    deleted = (
        await db.scalars(
            sa.select(AuditLog).where(
                AuditLog.entity_type == "workspace_note", AuditLog.action == AuditAction.DELETE
            )
        )
    ).all()
    assert [str(row.entity_id) for row in deleted] == [note["id"]]
    assert "repetida" not in (deleted[0].summary or "")


async def test_un_text_buit_no_es_una_correccio(client_a, workspace):  # noqa: F811
    note = await _note(client_a, workspace)
    response = await client_a.patch(f"{_base(workspace)}/notes/{note['id']}", json={"text": ""})
    assert response.status_code == 422


async def test_la_nota_d_una_altra_candidatura_es_404(db, client_a, tenant_a, workspace):  # noqa: F811
    from app.services import bid_workspace as workspace_service

    user, company = tenant_a
    other = (
        await workspace_service.create_workspace(
            db, company_id=company.id, tender=await _tender(db), user_id=user.id
        )
    ).workspace
    note = await _note(client_a, other, "De l'altra")

    patched = await client_a.patch(
        f"{_base(workspace)}/notes/{note['id']}", json={"text": "Intrusa"}
    )
    deleted = await client_a.delete(f"{_base(workspace)}/notes/{note['id']}")

    assert patched.status_code == 404
    assert deleted.status_code == 404
    kept = await db.get(WorkspaceNote, uuid.UUID(note["id"]))
    assert kept is not None and kept.text == "De l'altra"


async def test_un_rol_de_lectura_no_pot_corregir_ni_esborrar(
    db, client_a, client_factory, tenant_a, workspace  # noqa: F811
):
    note = await _note(client_a, workspace)
    _user, company = tenant_a
    viewer = await add_member(db, company.id, role=MembershipRole.VIEWER)
    client = client_factory()
    await login(client, viewer.email)

    patched = await client.patch(
        f"{_base(workspace)}/notes/{note['id']}", json={"text": "No puc"}
    )
    deleted = await client.delete(f"{_base(workspace)}/notes/{note['id']}")

    assert patched.status_code == 403
    assert deleted.status_code == 403
    assert (await client_a.get(f"{_base(workspace)}/notes")).json()[0]["text"] == "Primera versio"
