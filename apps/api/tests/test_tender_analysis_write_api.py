"""Escriptura de l'analisi (pas 7): POST /analysis/runs.

Sincron, determinista, sense LLM. Torna sempre l'analisi resultant i diu si s'ha
reutilitzat. Sense requisits efectius no analitza (409 estable). Permis: el de la
revisio de requisits del plec. Dades sintetiques.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.analysis import registry
from app.api.v1.routers import tender_analysis as router_module
from app.knowledge import vocabulary
from app.models.enums import RequirementCategory
from app.models.requirements import Requirement
from app.models.tender_analysis import TenderAnalysisRun
from app.models.tenders import Tender
from app.services import tender_analysis as analysis
from tests import test_tender_scope_models as m
from tests.test_company_knowledge_permissions_api import _role
from tests.test_tender_analysis_engine import _requirement, _synthetic_tender, _twin

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


def _base(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis"


async def _runs(db, company_id, tender_id) -> int:
    return (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderAnalysisRun)
            .where(
                TenderAnalysisRun.company_id == company_id,
                TenderAnalysisRun.tender_id == tender_id,
            )
        )
    ).scalar_one()


async def test_first_run_is_created_and_an_identical_one_is_reused(db, tenant_a, client_a):
    _, company = tenant_a
    tender, reqs, _, _, _ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    url = f"{_base(company.id, tender.id)}/runs"
    first = await client_a.post(url)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["reused"] is False and body["is_current"] is True
    assert len(body["fit_assessments"]) == len(reqs)
    assert "score" not in body["summary"] and "recommendation" not in body
    again = await client_a.post(url)
    assert again.status_code == 200, again.text
    assert again.json()["reused"] is True and again.json()["id"] == body["id"]
    assert await _runs(db, company.id, tender.id) == 1
    current = await client_a.get(f"{_base(company.id, tender.id)}/current")
    assert current.json()["id"] == body["id"] and current.json()["stale"] is False


async def test_without_effective_requirements_it_refuses_with_a_stable_code(db, tenant_a, client_a):
    _, company = tenant_a
    tender = await m._public_tender(db, title="Plec sintetic sense requisits")
    await db.commit()
    refused = await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "analysis_prerequisites_missing"
    assert error["details"]["missing"] == ["requirements"]
    assert await _runs(db, company.id, tender.id) == 0


async def test_a_409_leaves_an_existing_stale_run_untouched(db, tenant_a, client_a):
    """Si els requisits deixen de ser efectius despres d'una analisi, el 409 no la toca.
    Els requisits del plec sintetic son manuals: arxivar-los els treu del conjunt efectiu."""
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    url = f"{_base(company.id, tender.id)}/runs"
    first = (await client_a.post(url)).json()
    await db.execute(
        sa.update(Requirement)
        .where(Requirement.tender_id == tender.id)
        .values(archived_at=sa.func.now())
    )
    await db.commit()
    refused = await client_a.post(url)
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "analysis_prerequisites_missing"
    assert await _runs(db, company.id, tender.id) == 1
    current = await client_a.get(f"{_base(company.id, tender.id)}/current")
    assert current.status_code == 200 and current.json()["id"] == first["id"]
    assert current.json()["is_current"] is True


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("viewer", False),
        ("technical", True),
        ("bid_manager", True),
        ("admin", True),
        ("owner", True),
    ],
)
async def test_recalculating_follows_the_requirement_review_policy(
    db, tenant_a, client_a, role, allowed
):
    user, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    await _role(db, user, company, role)
    response = await client_a.post(f"{_base(company.id, tender.id)}/runs")
    if allowed:
        assert response.status_code == 201, response.text
    else:
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"
        assert await _runs(db, company.id, tender.id) == 0
    # Llegir, tothom.
    current = await client_a.get(f"{_base(company.id, tender.id)}/current")
    assert current.status_code == (200 if allowed else 404)


async def test_another_company_cannot_analyse_through_this_route(
    db, tenant_a, tenant_b, client_a, client_b
):
    _, company_a = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await db.commit()
    # Ruta d'una altra empresa: qui no n'es membre rep 404 (`get_company_context`).
    hijack = await client_b.post(f"{_base(company_a.id, tender.id)}/runs")
    assert hijack.status_code == 404
    assert await _runs(db, company_a.id, tender.id) == 0
    # Una licitacio inexistent: 404, cap analisi.
    missing = await client_a.post(f"{_base(company_a.id, uuid.uuid4())}/runs")
    assert missing.status_code == 404


async def test_a_changed_input_produces_a_new_current_run(db, tenant_a, client_a):
    """Spec 6: una entrada material nova no reutilitza l'analisi anterior."""
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    await db.commit()
    company_id, tender_id = company.id, tender.id
    url = f"{_base(company_id, tender_id)}/runs"
    first = (await client_a.post(url)).json()
    await _requirement(
        db,
        await db.get(Tender, tender_id),
        "Requisit manual nou",
        RequirementCategory.TECHNICAL_REQUIREMENT,
    )
    await db.commit()
    second = await client_a.post(url)
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["reused"] is False and body["stale"] is False and body["is_current"] is True
    assert body["id"] != first["id"]
    assert await _runs(db, company_id, tender_id) == 2
    db.expire_all()
    old = await db.get(TenderAnalysisRun, uuid.UUID(first["id"]))
    assert old.is_current is False
    current = await client_a.get(f"{_base(company_id, tender_id)}/current")
    assert current.json()["id"] == body["id"]


async def test_a_member_cannot_analyse_another_companys_private_tender(
    db, tenant_a, tenant_b, client_a
):
    _, company_a = tenant_a
    _, company_b = tenant_b
    tender, *_ = await _synthetic_tender(db)
    tender.owner_company_id = company_b.id  # licitacio manual privada de B
    await db.commit()
    response = await client_a.post(f"{_base(company_a.id, tender.id)}/runs")
    assert response.status_code == 404, response.text
    assert await _runs(db, company_a.id, tender.id) == 0
    assert await _runs(db, company_b.id, tender.id) == 0


async def test_a_lost_race_returns_the_other_run_instead_of_a_500(
    db, tenant_a, client_a, session_factory, monkeypatch
):
    """L'altra peticio desa la mateixa analisi abans que el nostre commit: el commit
    topa amb la unicitat i l'endpoint torna la de l'altra, reutilitzada."""
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    real = router_module._analyze_and_commit
    calls = {"n": 0}

    async def racing(session, company_id, tender_id, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            async with session_factory() as other:
                await analysis.analyze_tender(other, company_id=company_id, tender_id=tender_id)
                await other.commit()
            # El missatge real de SQLite per a la unicitat de les entrades.
            raise IntegrityError(
                "INSERT",
                {},
                Exception(
                    "UNIQUE constraint failed: tender_analysis_runs.company_id, "
                    "tender_analysis_runs.tender_id, tender_analysis_runs.inputs_hash"
                ),
            )
        return await real(session, company_id, tender_id, **kwargs)

    monkeypatch.setattr(router_module, "_analyze_and_commit", racing)
    response = await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert response.status_code == 200, response.text
    assert response.json()["reused"] is True
    assert await _runs(db, company.id, tender.id) == 1
    assert calls["n"] == 2


async def test_a_second_conflict_is_a_stable_409(db, tenant_a, client_a, monkeypatch):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)

    async def always_conflicts(session, company_id, tender_id, **kwargs):
        raise IntegrityError(
            "INSERT",
            {},
            Exception(
                "UNIQUE constraint failed: tender_analysis_runs.company_id, "
                "tender_analysis_runs.tender_id"
            ),
        )

    monkeypatch.setattr(router_module, "_analyze_and_commit", always_conflicts)
    response = await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "analysis_run_conflict"
    assert await _runs(db, company.id, tender.id) == 0


async def test_an_unrelated_integrity_error_is_not_masked_as_a_race(
    db, tenant_a, client_a, monkeypatch
):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    calls = {"n": 0}

    async def unrelated(session, company_id, tender_id, **kwargs):
        calls["n"] += 1
        raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

    monkeypatch.setattr(router_module, "_analyze_and_commit", unrelated)
    with pytest.raises(IntegrityError):
        await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert calls["n"] == 1, "cap reintent per a un error que no es la cursa"


async def test_a_retry_that_hits_an_unrelated_integrity_error_propagates(
    db, tenant_a, client_a, monkeypatch
):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    calls = {"n": 0}

    async def race_then_unrelated(session, company_id, tender_id, **kwargs):
        calls["n"] += 1
        message = (
            "UNIQUE constraint failed: tender_analysis_runs.company_id, "
            "tender_analysis_runs.tender_id, tender_analysis_runs.inputs_hash"
            if calls["n"] == 1
            else "FOREIGN KEY constraint failed"
        )
        raise IntegrityError("INSERT", {}, Exception(message))

    monkeypatch.setattr(router_module, "_analyze_and_commit", race_then_unrelated)
    with pytest.raises(IntegrityError):
        await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert calls["n"] == 2


@pytest.mark.postgres
async def test_two_simultaneous_requests_get_the_same_run(db, tenant_a, client_factory):
    import asyncio

    from tests.conftest import login

    user, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company)
    clients = [client_factory(), client_factory()]
    for client in clients:
        await login(client, user.email)
    url = f"{_base(company.id, tender.id)}/runs"
    first, second = await asyncio.gather(*(client.post(url) for client in clients))
    assert {first.status_code, second.status_code} <= {200, 201}, (first.text, second.text)
    assert first.json()["id"] == second.json()["id"]
    assert await _runs(db, company.id, tender.id) == 1


# --------------------------------------------------------------------------- #
# RULES_VERSION vigent (v4) i copy versionat (spec 3.4 i 3.11)                   #
# --------------------------------------------------------------------------- #

V2 = "phase3a-analysis-v2"
V3 = "phase3a-analysis-v3"
#: La versio vigent. 3B-2 la puja a `phase3b-analysis-v4` (forats del paquet i
#: divergencia de rols); el copy de la v4 es el de la v3.
CURRENT = "phase3b-analysis-v4"
_EXPLAINED = (
    "fit_assessments",
    "need_assessments",
    "criterion_assessments",
    "delivery_candidates",
    "findings",
)
_WEB_VOCABULARY = (
    Path(__file__).resolve().parents[2]
    / "web"
    / "src"
    / "lib"
    / "analysis-vocabulary.generated.json"
)


def _rendered(body: dict) -> dict[str, dict[str, str]]:
    """Tot el text que veu l'usuari d'un run: les preguntes tal com les retorna l'API i
    les explicacions renderitzades amb la versio del run (com fa el web)."""
    version = body["rules_version"]
    return {
        "questions": {q["id"]: q["text"] for q in body["questions"]},
        "explanations": {
            f"{kind}:{row['id']}": registry.explanation_text(row["explanation_code"], version)
            for kind in _EXPLAINED
            for row in body[kind]
        },
    }


async def _v2_run(client, monkeypatch, url: str) -> dict:
    with monkeypatch.context() as patch:
        patch.setattr(analysis, "RULES_VERSION", V2)
        created = await client.post(f"{url}/runs")
        assert created.status_code == 201, created.text
        assert created.json()["rules_version"] == V2
        current = await client.get(f"{url}/current")
        assert current.status_code == 200 and current.json()["stale"] is False
        return current.json()


async def test_a_v2_run_keeps_rendering_its_v2_copy_text_for_text(
    db, tenant_a, client_a, monkeypatch
):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    url = _base(company.id, tender.id)
    before = await _v2_run(client_a, monkeypatch, url)
    saved = _rendered(before)
    assert saved["questions"] and saved["explanations"], "el plec sintetic ha de tenir text"
    assert all(saved["questions"].values()) and all(saved["explanations"].values())
    # El JSON del web renderitza el mateix copy v2.
    web = json.loads(_WEB_VOCABULARY.read_text(encoding="utf-8"))["explanations"][V2]
    for kind in _EXPLAINED:
        for row in before[kind]:
            code = row["explanation_code"]
            if code in registry.EXPLANATIONS_BY_VERSION[V2]:
                assert web[code] == registry.explanation_text(code, V2)
            else:
                # Aquest run «v2» el fabrica el motor actual: un codi nou nomes de la v3
                # (que un run v2 real no pot contenir) no te text v2 i surt neutre.
                assert code not in web
                assert registry.explanation_text(code, V2) == registry.UNKNOWN_EXPLANATION_TEXT
    # La versio vigent (v4 des de 3B-2: el copy es el de la v3) activa, i amb un copy
    # que ja diu una altra cosa per a tots els codis.
    monkeypatch.setitem(
        registry.EXPLANATIONS_BY_VERSION,
        CURRENT,
        {
            code: f"vigent · {text}"
            for code, text in registry.EXPLANATIONS_BY_VERSION[CURRENT].items()
        },
    )
    monkeypatch.setitem(
        registry.QUESTIONS_BY_VERSION,
        CURRENT,
        {code: f"vigent · {text}" for code, text in registry.QUESTIONS_BY_VERSION[CURRENT].items()},
    )
    assert analysis.RULES_VERSION == CURRENT
    after = await client_a.get(f"{url}/current")
    assert after.status_code == 200 and after.json()["id"] == before["id"]
    assert after.json()["rules_version"] == V2
    assert _rendered(after.json()) == saved
    by_id = await client_a.get(f"{url}/runs/{before['id']}")
    assert _rendered(by_id.json()) == saved
    # Un run de la versio vigent renderitza el copy de la versio vigent.
    recalculated = await client_a.post(f"{url}/runs")
    assert recalculated.status_code == 201, recalculated.text
    fresh = _rendered(recalculated.json())
    assert fresh["questions"] and all(
        t.startswith("vigent · ") for t in fresh["questions"].values()
    )
    assert all(t.startswith("vigent · ") for t in fresh["explanations"].values())


async def test_v3_never_reuses_a_v2_run_and_leaves_it_intact(db, tenant_a, client_a, monkeypatch):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    company_id, tender_id = company.id, tender.id
    url = _base(company_id, tender_id)
    v2 = await _v2_run(client_a, monkeypatch, url)
    # Abans de recalcular, la v2 surt desactualitzada.
    stale = await client_a.get(f"{url}/current")
    assert stale.json()["id"] == v2["id"] and stale.json()["stale"] is True
    first = await client_a.post(f"{url}/runs")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["reused"] is False and body["id"] != v2["id"]
    assert body["rules_version"] == CURRENT and body["is_current"] is True
    assert body["inputs_hash"] != v2["inputs_hash"]
    again = await client_a.post(f"{url}/runs")
    assert again.status_code == 200, again.text
    assert again.json()["reused"] is True and again.json()["id"] == body["id"]
    assert await _runs(db, company_id, tender_id) == 2
    # El run v2 continua a la base intacte (nomes deixa de ser el vigent).
    kept = await client_a.get(f"{url}/runs/{v2['id']}")
    assert kept.status_code == 200
    ignore = {"is_current", "stale"}
    assert {k: v for k, v in kept.json().items() if k not in ignore} == {
        k: v for k, v in v2.items() if k not in ignore
    }
    db.expire_all()
    old = await db.get(TenderAnalysisRun, uuid.UUID(v2["id"]))
    assert old.rules_version == V2 and old.inputs_hash == v2["inputs_hash"]
    assert old.is_current is False
