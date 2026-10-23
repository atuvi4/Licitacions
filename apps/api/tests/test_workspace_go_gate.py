"""Guard del pas Preparar: cap mutacio ni generacio sense un GO huma vigent.

3B-2 §7.1.6 (decisio 4 del propietari, addendum A18): el pas Preparar es pot
obrir i llegir sense GO, pero tota mutacio o generacio (dossier, solucio
tecnica, oferta economica, paquet de presentacio, tasques) exigeix una decisio
humana GO vigent. El guard viu al backend
(`app.services.workspace_gate.assert_workspace_go`) i respon 403
`decision_required`.

Ajust del propietari (22-10-2026): les notes i els dubtes/discrepancies
s'identifiquen en l'analisi, ABANS de decidir. Sense cap decisio, amb la
decisio ajornada o amb GO s'hi pot escriure; nomes un NO-GO vigent els tanca
(`assert_workspace_study_open`, el mateix 403). Decidir si un dubte impedeix la
revisio final es una decisio d'oferta i continua a `PREPARE_MUTATORS`.

`PREPARE_MUTATORS` i `STUDY_MUTATORS` son la llista EXPLICITA de tots els
endpoints mutadors que pengen del workspace.
`test_la_llista_explicita_cobreix_tots_els_mutadors` les compara amb les rutes
reals de l'aplicacio: un endpoint nou sota `/workspaces/{workspace_id}` que no
hi sigui fa fallar el test, i quan s'hi afegeix, els tests parametritzats li
exigeixen el guard. Les excepcions son `POST .../decisions` (el pas Decidir, no
Preparar) i «Marca com revisats».
"""

from __future__ import annotations

import re
import uuid

import pytest
import sqlalchemy as sa

from app.main import app
from app.models.enums import SourceType
from app.models.offer import EconomicOffer
from app.models.sources import Source
from app.models.submission import SubmissionPackage
from app.models.tenders import Tender
from app.models.workspace import WorkspaceEvidenceItem
from app.services import bid_workspace as workspace_service

WORKSPACE_PREFIX = "/api/v1/companies/{company_id}/workspaces/{workspace_id}"

#: (metode, ruta relativa al workspace, cos JSON valid o None). Els `{id}` i
#: `{other}` es substitueixen per UUID aleatoris: el guard va abans de cap
#: cerca, aixi que sense GO la resposta ha de ser 403 encara que la fila no
#: existeixi. Els cossos han de passar la validacio de l'esquema, perque FastAPI
#: valida ABANS d'entrar a l'endpoint i un 422 amagaria el guard.
PREPARE_MUTATORS: list[tuple[str, str, dict | None]] = [
    # Dossier (routers/dossier.py)
    ("POST", "dossier/sync", None),
    ("POST", "dossier/{id}/relevance", {"note": "Es el document que demana el plec."}),
    ("POST", "dossier", {"notes": "Evidencia afegida a ma."}),
    ("PATCH", "dossier/{id}", {"notes": "Nota"}),
    ("POST", "dossier/{id}/task", None),
    ("POST", "readiness/advance", None),
    # Checklist i tasques (routers/workspaces.py). Les notes, a `STUDY_MUTATORS`.
    ("POST", "checklist/regenerate", None),
    ("POST", "checklist", {"title": "Item manual"}),
    ("PATCH", "checklist/{id}", {"note": "Nota"}),
    ("POST", "tasks", {"title": "Tasca"}),
    ("PATCH", "tasks/{id}", {"title": "Tasca"}),
    # Solucio tecnica (routers/technical_solutions.py)
    ("POST", "technical-solutions", {"title": "Solucio"}),
    ("POST", "technical-solutions/from-requirement", {"requirement_id": str(uuid.uuid4())}),
    ("PATCH", "technical-solutions/{id}", {"title": "Solucio"}),
    ("POST", "technical-solutions/{id}/validation", {"note": "Validada"}),
    # Oferta economica (routers/economic_offer.py)
    ("POST", "economic-offer", None),
    ("PATCH", "economic-offer", {"expected_revision": 0, "notes": "Nota"}),
    (
        "POST",
        "economic-offer/lots/from-tender-lot",
        {"expected_revision": 0, "tender_lot_id": str(uuid.uuid4())},
    ),
    ("PATCH", "economic-offer/lots/{id}", {"expected_revision": 0, "duration_months": 12}),
    ("POST", "economic-offer/lots/{id}/archive", {"expected_revision": 0}),
    ("POST", "economic-offer/lots/{id}/sections", {"expected_revision": 0, "title": "Seccio"}),
    ("PATCH", "economic-offer/sections/{id}", {"expected_revision": 0, "title": "Seccio"}),
    ("POST", "economic-offer/sections/{id}/archive", {"expected_revision": 0}),
    (
        "POST",
        "economic-offer/sections/{id}/lines",
        {"expected_revision": 0, "concept": "Linia", "quantity": "1", "pricing_basis": "one_off"},
    ),
    ("PATCH", "economic-offer/lines/{id}", {"expected_revision": 0, "concept": "Linia"}),
    ("POST", "economic-offer/lines/{id}/archive", {"expected_revision": 0}),
    ("POST", "economic-offer/lines/{id}/apply-target", {"expected_revision": 0}),
    (
        "POST",
        "economic-offer/lines/{id}/sync-from-option",
        {"expected_revision": 0, "fields": ["supplier_name"]},
    ),
    # Paquet de presentacio (routers/submission.py)
    ("POST", "packages", None),
    ("POST", "packages/{id}/approvals", {"comment": "Revisat."}),
    # Revisio de l'abast tecnic (routers/readiness.py). «Marca com revisats»
    # (`documents/review`) NO hi es: llegir els plecs es feina de Decidir (C3).
    ("POST", "technical-scope/review", None),
    # Dimensionament (routers/dimensions.py)
    ("POST", "dimensions", {"key": "punts", "label": "Punts de servei"}),
    ("PATCH", "dimensions/{id}", {"expected_revision": 0, "label": "Punts"}),
    (
        "POST",
        "dimensions/{id}/observations",
        {"expected_revision": 0, "source_kind": "tender_document", "value_text": "10"},
    ),
    (
        "POST",
        "dimensions/{id}/decide",
        {"expected_revision": 0, "working_value_text": "10", "reason": "Ho diu el plec."},
    ),
    ("POST", "dimensions/{id}/archive", {"expected_revision": 0}),
    # Dubtes (routers/issues.py): nomes decidir si impedeix la revisio final,
    # que es una decisio d'oferta. La resta del dubte, a `STUDY_MUTATORS`.
    ("POST", "issues/{id}/final-review-block", {"blocks_final_review": True}),
    # Opcions de solucio (routers/solution_options.py)
    ("POST", "solution-options", {"supplier_name": "Proveidor"}),
    ("PATCH", "solution-options/{id}", {"expected_revision": 0, "supplier_name": "Proveidor"}),
    (
        "PATCH",
        "solution-options/{id}/internal-economics",
        {"expected_revision": 0, "internal_note": "Nota"},
    ),
    ("POST", "solution-options/{id}/selection", {"expected_revision": 0, "selection": "undecided"}),
    ("POST", "solution-options/{id}/archive", {"expected_revision": 0}),
    ("PUT", "solution-options/{id}/dimensions/{other}", {"expected_revision": 0}),
    ("DELETE", "solution-options/{id}/dimensions/{other}?expected_revision=0", None),
    (
        "POST",
        "solution-options/{id}/to-offer-line",
        {"expected_revision": 0, "section_id": str(uuid.uuid4()), "concept": "Linia"},
    ),
]

#: Notes i dubtes/discrepancies: s'escriuen en l'analisi, abans de decidir. Sense
#: cap decisio, amb la decisio ajornada o amb GO responen el que toqui (201, 200,
#: 404 per un id inventat...); nomes un NO-GO vigent hi respon 403
#: `decision_required` (`app.services.workspace_gate.assert_workspace_study_open`).
STUDY_MUTATORS: list[tuple[str, str, dict | None]] = [
    # Notes (routers/workspaces.py). Corregir-les i esborrar-les (Bloc 1) porten la
    # mateixa guarda que escriure-les.
    ("POST", "notes", {"text": "Nota d'estudi"}),
    ("PATCH", "notes/{id}", {"text": "Nota corregida"}),
    ("DELETE", "notes/{id}", None),
    # Dubtes i discrepancies (routers/issues.py)
    ("POST", "issues", {"issue_type": "internal_question", "title": "Dubte"}),
    ("PATCH", "issues/{id}", {"title": "Dubte"}),
    ("POST", "issues/{id}/resolve", {"resolution": "Resolt."}),
    ("POST", "issues/{id}/dismiss", {}),
    ("POST", "issues/{id}/source-refs", {"source_ref_id": str(uuid.uuid4())}),
    ("DELETE", "issues/{id}/source-refs/{other}", None),
]

ALL_MUTATORS = PREPARE_MUTATORS + STUDY_MUTATORS

#: Lectures que han de continuar responent 200 sense cap decisio.
PREPARE_READS = [
    "",
    "decisions",
    "dossier",
    "readiness",
    "notes",
    "technical-solutions",
    "dimensions",
    "issues",
    "solution-options",
    "packages",
    "final-readiness",
]


def _ids(mutators: list[tuple[str, str, dict | None]]) -> list[str]:
    return [f"{method} {path}" for method, path, _ in mutators]


MUTATOR_IDS = _ids(PREPARE_MUTATORS)
STUDY_IDS = _ids(STUDY_MUTATORS)
ALL_IDS = _ids(ALL_MUTATORS)


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


async def _tender(db) -> Tender:
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="GO/2026",
        title="Manteniment de la xarxa de fibra",
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


@pytest.fixture
async def workspace(db, tenant_a):
    user, company = tenant_a
    tender = await _tender(db)
    outcome = await workspace_service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=user.id
    )
    return outcome.workspace


def _base(workspace) -> str:
    return f"/api/v1/companies/{workspace.company_id}/workspaces/{workspace.id}"


async def _decide(client, workspace, decision: str):
    response = await client.post(
        f"{_base(workspace)}/decisions",
        json={"decision": decision, "reason": "Decisio de prova amb motiu."},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _call(client, workspace, method: str, path: str, body: dict | None):
    url = f"{_base(workspace)}/{path.format(id=uuid.uuid4(), other=uuid.uuid4())}"
    return await client.request(method, url, json=body)


def _error_code(response) -> str | None:
    try:
        return response.json().get("error", {}).get("code")
    except ValueError:  # pragma: no cover - una resposta sense JSON no es un 403 nostre
        return None


# --------------------------------------------------------------------------- #
# Sense GO: 403 `decision_required` a cada mutador                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("method", "path", "body"), PREPARE_MUTATORS, ids=MUTATOR_IDS)
async def test_sense_cap_decisio_cada_mutador_respon_403_decision_required(
    client_a, workspace, method, path, body
):
    response = await _call(client_a, workspace, method, path, body)
    assert response.status_code == 403, response.text
    assert _error_code(response) == "decision_required"


@pytest.mark.parametrize(("method", "path", "body"), ALL_MUTATORS, ids=ALL_IDS)
async def test_amb_no_go_cada_mutador_respon_403_decision_required(
    client_a, workspace, method, path, body
):
    """Un NO-GO vigent ho tanca tot: tambe les notes i els dubtes."""
    await _decide(client_a, workspace, "no_go")
    response = await _call(client_a, workspace, method, path, body)
    assert response.status_code == 403, response.text
    assert _error_code(response) == "decision_required"


@pytest.mark.parametrize(("method", "path", "body"), ALL_MUTATORS, ids=ALL_IDS)
async def test_amb_go_vigent_cap_mutador_no_queda_bloquejat(
    client_a, workspace, method, path, body
):
    """Amb GO tot funciona com avui: la resposta es la que toqui (200, 201,
    404 per un id inventat, 409 per l'estat...), mai `decision_required`."""
    await _decide(client_a, workspace, "go")
    response = await _call(client_a, workspace, method, path, body)
    assert response.status_code != 403, response.text
    assert response.status_code < 500, response.text
    assert _error_code(response) != "decision_required"


# --------------------------------------------------------------------------- #
# Notes i dubtes: oberts en l'analisi, abans de decidir                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("method", "path", "body"), STUDY_MUTATORS, ids=STUDY_IDS)
async def test_en_analisi_les_notes_i_els_dubtes_no_demanen_cap_decisio(
    client_a, workspace, method, path, body
):
    """Sense cap decisio (estat `analysis`): la resposta es la que toqui (201,
    200, 404 per un id inventat...), mai `decision_required`."""
    assert workspace.status.value == "analysis"
    response = await _call(client_a, workspace, method, path, body)
    assert response.status_code != 403, response.text
    assert response.status_code < 500, response.text
    assert _error_code(response) != "decision_required"


@pytest.mark.parametrize(("method", "path", "body"), STUDY_MUTATORS, ids=STUDY_IDS)
async def test_amb_la_decisio_ajornada_les_notes_i_els_dubtes_continuen_oberts(
    client_a, workspace, method, path, body
):
    """Ajornar no es dir que no: l'estudi continua."""
    await _decide(client_a, workspace, "defer")
    response = await _call(client_a, workspace, method, path, body)
    assert response.status_code != 403, response.text
    assert response.status_code < 500, response.text
    assert _error_code(response) != "decision_required"


async def test_en_analisi_s_obre_i_es_resol_un_dubte_i_s_escriu_una_nota(client_a, workspace):
    """De punta a punta i sense cap decisio: el dubte neix, es resol i la nota
    queda escrita. La candidatura continua en `analysis` i sense decisions.
    Bloquejar la revisio final, en canvi, es una decisio d'oferta: demana GO."""
    base = _base(workspace)
    created = await client_a.post(
        f"{base}/issues",
        json={"issue_type": "contradiction", "title": "El PPT i el PCAP no diuen el mateix"},
    )
    assert created.status_code == 201, created.text
    issue_id = created.json()["id"]

    resolved = await client_a.post(
        f"{base}/issues/{issue_id}/resolve",
        json={"resolution": "Aclarit al tauler.", "resolution_source": "Resposta oficial"},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"

    note = await client_a.post(f"{base}/notes", json={"text": "Dimensionament: 131 linies."})
    assert note.status_code == 200, note.text
    texts = [n["text"] for n in (await client_a.get(f"{base}/notes")).json()]
    assert texts == ["Dimensionament: 131 linies."]

    blocked = await client_a.post(
        f"{base}/issues/{issue_id}/final-review-block", json={"blocks_final_review": True}
    )
    assert blocked.status_code == 403, blocked.text
    assert _error_code(blocked) == "decision_required"

    detail = (await client_a.get(base)).json()
    assert detail["status"] == "analysis"
    assert detail["human_decision"] is None
    assert (await client_a.get(f"{base}/decisions")).json() == []


async def test_un_ajornament_sense_go_tampoc_no_obre_preparar(client_a, workspace):
    """Ajornar deixa obertes les notes i els dubtes, pero no la preparacio."""
    await _decide(client_a, workspace, "defer")
    response = await client_a.post(f"{_base(workspace)}/tasks", json={"title": "Tasca"})
    assert response.status_code == 403
    assert _error_code(response) == "decision_required"


async def test_un_no_go_posterior_al_go_torna_a_bloquejar(client_a, workspace):
    """La decisio vigent es l'ULTIMA: canviar d'opinio tanca el pas de nou."""
    await _decide(client_a, workspace, "go")
    opened = await client_a.post(f"{_base(workspace)}/notes", json={"text": "Amb GO"})
    assert opened.status_code == 200, opened.text

    await _decide(client_a, workspace, "no_go")
    closed = await client_a.post(f"{_base(workspace)}/notes", json={"text": "Amb NO-GO"})
    assert closed.status_code == 403
    assert _error_code(closed) == "decision_required"

    # Un GO nou torna a obrir: la historia de decisions es conserva sencera.
    await _decide(client_a, workspace, "go")
    reopened = await client_a.post(f"{_base(workspace)}/notes", json={"text": "GO de nou"})
    assert reopened.status_code == 200, reopened.text
    decisions = (await client_a.get(f"{_base(workspace)}/decisions")).json()
    assert [d["decision"] for d in decisions] == ["go", "no_go", "go"]


async def test_el_403_explica_quina_decisio_hi_ha(client_a, workspace):
    response = await client_a.post(f"{_base(workspace)}/tasks", json={"title": "Tasca"})
    body = response.json()["error"]
    assert body["code"] == "decision_required"
    assert body["details"]["current_decision"] is None
    assert body["details"]["workspace_id"] == str(workspace.id)

    await _decide(client_a, workspace, "no_go")
    response = await client_a.post(f"{_base(workspace)}/tasks", json={"title": "Tasca"})
    assert response.json()["error"]["details"]["current_decision"] == "no_go"


# --------------------------------------------------------------------------- #
# Llegir no demana cap decisio; generar, si                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", PREPARE_READS)
async def test_els_get_continuen_responent_200_sense_decisio(client_a, workspace, path):
    response = await client_a.get(f"{_base(workspace)}/{path}".rstrip("/"))
    assert response.status_code == 200, response.text


async def test_sense_go_no_es_genera_res(db, client_a, workspace):
    """Ni dossier, ni oferta, ni paquet: les generacions tambe son mutacions."""
    for method, path in (
        ("POST", "dossier/sync"),
        ("POST", "economic-offer"),
        ("POST", "packages"),
        ("POST", "checklist/regenerate"),
    ):
        response = await _call(client_a, workspace, method, path, None)
        assert response.status_code == 403, (path, response.text)

    async def count(model) -> int:
        return await db.scalar(
            sa.select(sa.func.count()).select_from(model).where(model.workspace_id == workspace.id)
        )

    assert await count(WorkspaceEvidenceItem) == 0
    assert await count(EconomicOffer) == 0
    assert await count(SubmissionPackage) == 0


async def test_decidir_no_queda_guardat(client_a, workspace):
    """El pas Decidir i els anteriors no canvien: decidir no exigeix cap GO previ."""
    assert (await _decide(client_a, workspace, "go"))["human_decision"] == "go"


async def test_marcar_els_plecs_com_revisats_es_feina_de_decidir_i_no_demana_cap_go(
    client_a, workspace
):
    """«Marca com revisats» (`documents/review`) es llegir els plecs abans de
    decidir (ruling C3): sense cap decisio respon 2xx, mai `decision_required`."""
    response = await client_a.post(f"{_base(workspace)}/documents/review")
    assert 200 <= response.status_code < 300, response.text
    assert response.json()["documents"]["reviewed"] is True

    await _decide(client_a, workspace, "no_go")
    again = await client_a.post(f"{_base(workspace)}/documents/review")
    assert 200 <= again.status_code < 300, again.text


# --------------------------------------------------------------------------- #
# La llista explicita es completa                                              #
# --------------------------------------------------------------------------- #


def _normalize(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path.split("?", 1)[0])


def _workspace_mutators_in_app() -> set[tuple[str, str]]:
    """Enumeracio POSITIVA de les rutes: `app.routes` nomes porta un router
    mandros (`_IncludedRouter`) i recorrer-lo no veu cap endpoint; l'esquema
    OpenAPI els materialitza tots."""
    found: set[tuple[str, str]] = set()
    for path, operations in app.openapi()["paths"].items():
        if not path.startswith(WORKSPACE_PREFIX):
            continue
        for method in operations:
            if method.upper() in {"GET", "HEAD", "OPTIONS"}:
                continue
            found.add((method.upper(), _normalize(path[len(WORKSPACE_PREFIX) :].lstrip("/"))))
    assert found, "l'esquema OpenAPI no enumera cap mutador del workspace"
    return found


def test_la_llista_explicita_cobreix_tots_els_mutadors_del_workspace():
    """Un endpoint mutador nou sota el workspace ha d'entrar a `PREPARE_MUTATORS`
    o a `STUDY_MUTATORS` (i, per tant, portar el guard que li toca) o ser una
    excepcio documentada aqui."""
    prepare = {(method, _normalize(path)) for method, path, _ in PREPARE_MUTATORS}
    study = {(method, _normalize(path)) for method, path, _ in STUDY_MUTATORS}
    # Excepcions explicites: el pas Decidir, i «Marca com revisats», que es
    # llegir els plecs abans de decidir (ruling C3), no preparar.
    exempt = {("POST", "decisions"), ("POST", "documents/review")}
    assert prepare.isdisjoint(study)
    assert prepare.isdisjoint(exempt) and study.isdisjoint(exempt)
    assert _workspace_mutators_in_app() == prepare | study | exempt
