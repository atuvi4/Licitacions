# Pestanya «Anàlisi» de la licitació — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Afegir a `/licitacions/{id}` una pestanya «Anàlisi» que explica l'anàlisi determinista 0051/0052 (què sabem, què no i per què), permet recalcular-la i porta l'usuari a la candidatura per decidir; més una targeta resum al pas Decidir.

**Architecture:** Backend petit: un `POST …/analysis/runs` síncron sobre `analyze_tender` (precondició, permís existent, `reused`, cursa segura) i un script que exporta el vocabulari del motor a un JSON determinista que consumeix el web. Frontend: `lib/analysis.ts` (tipus, etiquetes, destins, funcions pures) i components a `components/tender-analysis/`, integrats a la pàgina de la licitació i al pas Decidir.

**Tech Stack:** FastAPI + SQLAlchemy async (pytest, SQLite + PostgreSQL), Next.js 15 / React 19 (vitest + Testing Library), kit `components/ui`.

**Spec:** `docs/superpowers/specs/2026-10-02-tender-analysis-tab-design.md` (v3)

## Global Constraints

- Worktree `C:\Users\tecni\Documents\Licitacions-analysis-ui`, branca `engine/phase3a-analysis-workspace` des de `5502ef5`. **Cap commit** fins a l'aprovació explícita de l'usuari: cada tasca acaba amb un *checkpoint* (tests verds + `git status`), no amb `git commit`.
- Python canònic: `C:\Users\tecni\Documents\Licitacions\apps\api\.venv\Scripts\python.exe` (a la doc: `apps/api/.venv/Scripts/python`). Tests del backend des de `apps/api`, amb `LLM_PROVIDER=fake`.
- Cap migració, cap canvi a `RULES_VERSION`, a les regles del motor ni als textos d'output del backend (`EXPLANATIONS`, plantilles de preguntes). Es mostren tal com són.
- Cap score global, percentatge, recomanació ni Go/No-Go. «No se sap» sempre neutre (`level="neutral"`) i amb el perquè. «Possible bloqueig» sempre amb «no confirmat».
- Mai cap UUID ni codi cru a la UI. Cites i «On es resol» només quan existeixen.
- Preguntes: «generades» / «a tractar», mai «obertes/resoltes». Es mostra `question.text` tal com arriba.
- Execució: només `delivery_candidates`, etiquetes literals. Res de solució, proveïdors, BOM, dimensionament ni preus.
- Permís de recàlcul: backend `review_actions.assert_can_review`; frontend `canReview` de `lib/review.ts`. Cap llista de rols nova. Cap política de lectura al frontend.
- Tests i fixtures: només dades sintètiques. No desplegar al pilot.
- Accessibilitat: cap estat només per color; `aria-expanded`/`aria-controls` als expandibles, `aria-pressed` als filtres, `aria-live="polite"` per al recàlcul i el recompte de filtres; accions de fila `min-h-11` a mòbil.

## Review Focus

1. **Requisit de l'anàlisi que ja no és a la lectura de requisits** (p. ex. arxivat després de l'anàlisi): la fila ha de dir «Requisit sense títol disponible», sense UUID ni error → test a la Tasca 6.
2. **`summary` d'una anàlisi antiga sense alguna clau** (p. ex. sense `questions`): el resum no ha de petar ni mostrar `undefined`/`NaN` → test a la Tasca 4 (`summaryOf` amb objecte parcial).
3. **Explanation code nou que el JSON encara no coneix** (backend desplegat abans de regenerar el web): text neutre «Explicació no disponible», mai el codi → test a la Tasca 4.
4. **Doble clic a «Recalcula»** mentre la petició és en curs: un sol POST → test a la Tasca 5.
5. **Licitació amb 0 criteris o 0 elements d'abast**: les seccions diuen que no n'hi ha (text neutre), no queden buides ni amb taules sense files → test a la Tasca 6 i Tasca 7.

---

## File Structure

**Backend (`apps/api`)**
- Modify `app/core/errors.py` — dos errors de domini: `AnalysisPrerequisitesMissingError` (`analysis_prerequisites_missing`) i `AnalysisRunConflictError` (`analysis_run_conflict`).
- Modify `app/schemas/tender_analysis.py` — `AnalysisRunWriteRead(AnalysisRunRead)` amb `reused: bool`.
- Modify `app/api/v1/routers/tender_analysis.py` — `POST /runs` (precondició, permís, 201/200, reintent de cursa).
- Create `scripts/export_analysis_vocabulary.py` — exporta el vocabulari a `apps/web/src/lib/analysis-vocabulary.generated.json`.
- Create `tests/test_tender_analysis_write_api.py` — endpoint, rols, precondició, cursa, PG.
- Create `tests/test_analysis_vocabulary_export.py` — JSON = registre; `EXPLANATIONS` complet.

**Frontend (`apps/web/src`)**
- Create `lib/analysis-vocabulary.generated.json` (generat, no s'edita a mà).
- Create `lib/analysis.ts` + `lib/analysis.test.ts` — tipus, etiquetes, explicacions canòniques, destins «On es resol», ordre de findings, frases del resum, consultes de noms.
- Create `components/tender-analysis/AnalysisTab.tsx` — estats, recàlcul, composició.
- Create `components/tender-analysis/AnalysisSummary.tsx`, `ResolveBeforeDeciding.tsx`, `RequirementsFit.tsx`, `CriteriaPoints.tsx`, `Delivery.tsx`, `Questions.tsx`, `DecisionFooter.tsx`, `DecisionAnalysisCard.tsx`, `Citation.tsx`.
- Create `components/tender-analysis/AnalysisTab.test.tsx`, `sections.test.tsx`, `DecisionAnalysisCard.test.tsx`; `test/analysis-fixture.ts`.
- Modify `app/(app)/licitacions/[tenderId]/header.ts` (pestanya `analisi`) i `page.tsx` (render).
- Modify `app/(app)/candidatures/[workspaceId]/page.tsx` (targeta al pas Decidir).
- Modify `docs/analysis-engine.md` (regeneració del vocabulari i la pestanya).

---

### Task 1: `POST /analysis/runs` — precondició, permís i reutilització

**Files:**
- Modify: `apps/api/app/core/errors.py` (afegir dues classes al final del bloc de `ConflictError`)
- Modify: `apps/api/app/schemas/tender_analysis.py` (afegir `AnalysisRunWriteRead`)
- Modify: `apps/api/app/api/v1/routers/tender_analysis.py`
- Test: `apps/api/tests/test_tender_analysis_write_api.py`

**Interfaces:**
- Produces: `POST /api/v1/companies/{company_id}/tenders/{tender_id}/analysis/runs` → `AnalysisRunWriteRead` (forma de `AnalysisRunRead` + `reused: bool`), 201 nova / 200 reutilitzada; 409 `analysis_prerequisites_missing` amb `details={"missing": ["requirements"]}`; 403 `permission_denied`; 404 si la licitació no és visible.
- Produces (per a la Tasca 2): funció de mòdul `async def _analyze_and_commit(session, company_id, tender_id) -> tender_analysis.AnalysisOutcome` al router.

- [ ] **Step 1: Write the failing tests**

`apps/api/tests/test_tender_analysis_write_api.py`:

```python
"""Escriptura de l'analisi (pas 7): POST /analysis/runs.

Sincron, determinista, sense LLM. Torna sempre l'analisi resultant i diu si s'ha
reutilitzat. Sense requisits efectius no analitza (409 estable). Permis: el de la
revisio de requisits del plec. Dades sintetiques.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.models.tender_analysis import TenderAnalysisRun
from tests import test_tender_scope_models as m
from tests.test_company_knowledge_permissions_api import _role
from tests.test_tender_analysis_engine import _synthetic_tender, _twin

pytestmark = pytest.mark.asyncio


def _base(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis"


async def _runs(db, company_id, tender_id) -> int:
    return (
        await db.execute(
            sa.select(sa.func.count()).select_from(TenderAnalysisRun).where(
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


async def test_without_effective_requirements_it_refuses_with_a_stable_code(
    db, tenant_a, client_a
):
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
    from app.models.requirements import Requirement

    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _twin(db, company, client_a)
    url = f"{_base(company.id, tender.id)}/runs"
    first = (await client_a.post(url)).json()
    await db.execute(
        sa.update(Requirement).where(Requirement.tender_id == tender.id).values(archived_at=sa.func.now())
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
    [("viewer", False), ("technical", True), ("bid_manager", True), ("admin", True), ("owner", True)],
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (des de `apps/api`): `LLM_PROVIDER=fake C:\Users\tecni\Documents\Licitacions\apps\api\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_tender_analysis_write_api.py`
Expected: FAIL (405 Method Not Allowed a `POST /runs`).

- [ ] **Step 3: Add the domain errors**

A `apps/api/app/core/errors.py`, just després de la classe `ConflictError`:

```python
class AnalysisPrerequisitesMissingError(ConflictError):
    """No hi ha res que analitzar. `details["missing"]` diu que, amb codis tancats."""

    code = "analysis_prerequisites_missing"


class AnalysisRunConflictError(ConflictError):
    """Una altra peticio ha canviat l'analisi alhora: es pot tornar a provar."""

    code = "analysis_run_conflict"
```

- [ ] **Step 4: Add the write schema**

Al final de `apps/api/app/schemas/tender_analysis.py`:

```python
class AnalysisRunWriteRead(AnalysisRunRead):
    """La resposta de `POST /analysis/runs`: l'analisi resultant i si s'ha reutilitzat."""

    reused: bool
```

- [ ] **Step 5: Implement the endpoint**

A `apps/api/app/api/v1/routers/tender_analysis.py`, actualitza la docstring del mòdul (ja no és només lectura) i afegeix:

```python
from fastapi import APIRouter, Response, status

from app.core.errors import AnalysisPrerequisitesMissingError, NotFoundError
from app.schemas.tender_analysis import AnalysisRunRead, AnalysisRunWriteRead, QuestionRead
from app.services import manual_requirement, review_actions, tender_analysis

#: Codis tancats de `details.missing`. A la v1 nomes n'hi ha un.
MISSING_REQUIREMENTS = "requirements"


async def _analyze_and_commit(session, company_id: uuid.UUID, tender_id: uuid.UUID):
    """Una execucio del motor i el commit. Es una funcio a part per poder-la provar
    en una cursa (Tasca 2)."""
    outcome = await tender_analysis.analyze_tender(
        session, company_id=company_id, tender_id=tender_id
    )
    await session.commit()
    return outcome


@router.post(
    "/runs",
    response_model=AnalysisRunWriteRead,
    status_code=status.HTTP_201_CREATED,
    summary="Analitza (o reutilitza) i deixa vigent",
)
async def run_analysis(
    tender_id: uuid.UUID, response: Response, session: SessionDep, ctx: CompanyCtx
):
    # El mateix permis que revisar els requisits del plec: cap llista nova.
    review_actions.assert_can_review(ctx.membership.role)
    await _visible_tender(session, ctx.company_id, tender_id)
    effective = await manual_requirement.effective_requirements(session, tender_id)
    if not list(effective.all):
        # El motor no refusa mai: sense requisits faria una analisi buida i enganyosa.
        raise AnalysisPrerequisitesMissingError(
            "No hi ha requisits per analitzar.",
            details={"missing": [MISSING_REQUIREMENTS]},
        )
    outcome = await _analyze_and_commit(session, ctx.company_id, tender_id)
    if outcome.reused:
        response.status_code = status.HTTP_200_OK
    read = await run_read(session, outcome.run, stale=False)
    return AnalysisRunWriteRead(**read.model_dump(), reused=outcome.reused)
```

(Elimina els imports duplicats que ja hi havia: `APIRouter`, `NotFoundError`, `AnalysisRunRead`, `QuestionRead`, `tender_analysis`.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `LLM_PROVIDER=fake …python.exe -m pytest -q -p no:cacheprovider tests/test_tender_analysis_write_api.py tests/test_tender_analysis_engine.py`
Expected: PASS (el test existent `test_the_read_api_exposes_the_current_run_and_a_given_run` continua verd: `POST /current` segueix sent 404/405).

- [ ] **Step 7: Checkpoint (sense commit)**

`git -C C:\Users\tecni\Documents\Licitacions-analysis-ui status --short` — només els fitxers d'aquesta tasca.

---

### Task 2: Cursa segura (`IntegrityError` → reutilitza, o 409 estable)

**Files:**
- Modify: `apps/api/app/api/v1/routers/tender_analysis.py` (`run_analysis`)
- Test: `apps/api/tests/test_tender_analysis_write_api.py` (afegir dos tests)

**Interfaces:**
- Consumes: `_analyze_and_commit` (Tasca 1).
- Produces: el mateix contracte, sense 500 en cursa; 409 `analysis_run_conflict` només si el reintent topa un altre cop amb la mateixa unicitat. `def _is_run_race(exc: IntegrityError) -> bool` al router. Un `IntegrityError` no relacionat es propaga.

- [ ] **Step 1: Write the failing tests**

Afegeix a `tests/test_tender_analysis_write_api.py`:

```python
from sqlalchemy.exc import IntegrityError

from app.api.v1.routers import tender_analysis as router_module
from app.services import tender_analysis as analysis


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

    async def racing(session, company_id, tender_id):
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
        return await real(session, company_id, tender_id)

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

    async def always_conflicts(session, company_id, tender_id):
        raise IntegrityError(
            "INSERT", {}, Exception("UNIQUE constraint failed: index 'uq_tender_analysis_runs_current'")
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

    async def unrelated(session, company_id, tender_id):
        calls["n"] += 1
        raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed"))

    monkeypatch.setattr(router_module, "_analyze_and_commit", unrelated)
    with pytest.raises(IntegrityError):
        await client_a.post(f"{_base(company.id, tender.id)}/runs")
    assert calls["n"] == 1, "cap reintent per a un error que no es la cursa"


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
```

- [ ] **Step 2: Run to verify they fail**

Run: `…python.exe -m pytest -q -p no:cacheprovider tests/test_tender_analysis_write_api.py -k "race or conflict or unrelated"`
Expected: FAIL en els de cursa i conflicte (l'`IntegrityError` puja com a excepció); el d'`unrelated` ja passa (és el comportament que s'ha de conservar).

- [ ] **Step 3: Implement the retry**

Afegeix al router, a nivell de mòdul:

```python
#: Les unicitats d'una analisi: nomes un conflicte d'aquestes es una cursa entre peticions.
_RUN_RACE_MARKERS = (
    "uq_tender_analysis_runs_inputs",
    "uq_tender_analysis_runs_current",
    # SQLite no anomena la restriccio de taula: en dona les columnes.
    "tender_analysis_runs.company_id, tender_analysis_runs.tender_id, tender_analysis_runs.inputs_hash",
)


def _is_run_race(exc: IntegrityError) -> bool:
    """Cert nomes per a la unicitat esperada de les analisis. Res mes."""
    constraint = getattr(getattr(exc, "orig", None), "constraint_name", None)
    text = f"{constraint or ''} {exc.orig}"
    return any(marker in text for marker in _RUN_RACE_MARKERS)
```

A `run_analysis`, substitueix la línia `outcome = await _analyze_and_commit(...)` per:

```python
    try:
        outcome = await _analyze_and_commit(session, ctx.company_id, tender_id)
    except IntegrityError as exc:
        if not _is_run_race(exc):
            raise  # un altre error d'integritat no s'emmascara
        # Una altra peticio ha desat la mateixa analisi (o una de vigent) alhora. Un
        # sol reintent: el motor la trobara per `inputs_hash` i la reutilitzara.
        await session.rollback()
        try:
            outcome = await _analyze_and_commit(session, ctx.company_id, tender_id)
        except IntegrityError as again:
            await session.rollback()
            if not _is_run_race(again):
                raise
            raise AnalysisRunConflictError(
                "L'analisi ha canviat alhora des d'una altra peticio: torneu-ho a provar."
            ) from again
```

A la implementació, comprova amb el test PostgreSQL quin text porta `exc.orig` amb asyncpg (normalment «… violates unique constraint "uq_tender_analysis_runs_inputs"»). Si no conté el nom de la restricció, llegeix-lo de l'excepció original d'asyncpg (`exc.orig.__cause__.constraint_name`) dins `_is_run_race`, sense ampliar els marcadors a cap altra taula.

i importa `from sqlalchemy.exc import IntegrityError` i `AnalysisRunConflictError`.

- [ ] **Step 4: Run SQLite and PostgreSQL**

Run: `…python.exe -m pytest -q -p no:cacheprovider tests/test_tender_analysis_write_api.py` → PASS (el test `postgres` s'omet).
Run PG (PowerShell, sense imprimir la DSN):

```powershell
$u = [Environment]::GetEnvironmentVariable('BENCHMARK_DATABASE_URL','User'); $env:TEST_DATABASE_URL = ($u -replace '/[^/?]+(\?|$)', '/licitacions_test$1') -replace '^postgresql(\+psycopg)?://', 'postgresql+asyncpg://'; $env:LLM_PROVIDER='fake'; & 'C:\Users\tecni\Documents\Licitacions\apps\api\.venv\Scripts\python.exe' -m pytest -q -p no:cacheprovider --postgres tests/test_tender_analysis_write_api.py
```

Expected: PASS, inclòs `test_two_simultaneous_requests_get_the_same_run`.

- [ ] **Step 5: Checkpoint (sense commit)** — `git status --short`.

---

### Task 3: Exportació determinista del vocabulari

**Files:**
- Create: `apps/api/scripts/export_analysis_vocabulary.py`
- Create (generat): `apps/web/src/lib/analysis-vocabulary.generated.json`
- Test: `apps/api/tests/test_analysis_vocabulary_export.py`

**Interfaces:**
- Produces: JSON amb claus de primer nivell `contract`, `explanations`, `rendered`:
  - `contract`: `{"question_code": [str…], "rule_code": [str…]}`
  - `explanations`: `{EXPLANATION_CODE: text canònic}`
  - `rendered`: `{"analysis_knowledge_source"|"assessment_verification"|"candidate_origin"|"candidate_readiness"|"candidate_type"|"criterion_result"|"finding_type"|"fit_result"|"points_status"|"proof_status"|"territory_verdict": [valors…]}`
  - Ordenat (claus i llistes), `indent=2`, `ensure_ascii=False`, salt de línia final, cap timestamp.
- Produces (Python): `build_vocabulary() -> dict`, `render(vocabulary: dict) -> str`, `TARGET: Path`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/test_analysis_vocabulary_export.py`:

```python
"""El vocabulari del motor que consumeix el web: generat, determinista i complet."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from app.analysis.registry import EXPLANATIONS, ExplanationCode

API_ROOT = Path(__file__).resolve().parents[1]


def _script():
    path = API_ROOT / "scripts" / "export_analysis_vocabulary.py"
    spec = importlib.util.spec_from_file_location("export_analysis_vocabulary", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _codes(cls) -> set[str]:
    return {v for k, v in vars(cls).items() if k.isupper() and isinstance(v, str)}


def test_every_explanation_code_has_its_canonical_text():
    assert set(EXPLANATIONS) == _codes(ExplanationCode)


def test_the_committed_json_is_exactly_what_the_script_generates():
    script = _script()
    expected = script.render(script.build_vocabulary())
    assert script.TARGET.read_text(encoding="utf-8") == expected, (
        "Vocabulari desactualitzat. Regenera'l: "
        "apps/api/.venv/Scripts/python apps/api/scripts/export_analysis_vocabulary.py"
    )


def test_the_output_is_deterministic_and_has_no_timestamps():
    script = _script()
    first = script.render(script.build_vocabulary())
    assert first == script.render(script.build_vocabulary())
    assert first.endswith("\n") and "generated_at" not in first and "202" not in first.split('"explanations"')[0]
    vocabulary = script.build_vocabulary()
    assert set(vocabulary) == {"contract", "explanations", "rendered"}
    for values in (*vocabulary["contract"].values(), *vocabulary["rendered"].values()):
        assert values == sorted(values)
```

- [ ] **Step 2: Run to verify it fails**

Run: `…python.exe -m pytest -q -p no:cacheprovider tests/test_analysis_vocabulary_export.py`
Expected: el primer test PASS (el registre ja és complet); els altres FAIL (no existeix l'script).

- [ ] **Step 3: Write the script**

`apps/api/scripts/export_analysis_vocabulary.py`:

```python
"""Exporta el vocabulari del motor d'analisi (0051/0052) al web.

    apps/api/.venv/Scripts/python apps/api/scripts/export_analysis_vocabulary.py

Escriu `apps/web/src/lib/analysis-vocabulary.generated.json`: determinista (ordenat,
sense dates). `explanations` porta el text canonic de `EXPLANATIONS`, que la UI mostra
tal com es. `rendered` son els valors que la UI pinta amb copy propi. `contract` son
valors que l'API retorna pero la UI v1 no pinta com a text.
No l'editeu a ma: el test `test_analysis_vocabulary_export.py` el compara amb aquesta sortida.
"""

from __future__ import annotations

import json
import sys
from enum import StrEnum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.registry import EXPLANATIONS, QuestionCode, RuleCode  # noqa: E402
from app.models.enums import (  # noqa: E402
    AnalysisKnowledgeSource,
    AssessmentVerification,
    CandidateOrigin,
    CandidateReadiness,
    CandidateType,
    CriterionResult,
    FindingType,
    FitResult,
    PointsStatus,
    ProofStatus,
    TerritoryVerdict,
)

TARGET = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "analysis-vocabulary.generated.json"
)

RENDERED: dict[str, type[StrEnum]] = {
    "analysis_knowledge_source": AnalysisKnowledgeSource,
    "assessment_verification": AssessmentVerification,
    "candidate_origin": CandidateOrigin,
    "candidate_readiness": CandidateReadiness,
    "candidate_type": CandidateType,
    "criterion_result": CriterionResult,
    "finding_type": FindingType,
    "fit_result": FitResult,
    "points_status": PointsStatus,
    "proof_status": ProofStatus,
    "territory_verdict": TerritoryVerdict,
}


def _constants(cls) -> list[str]:
    return sorted(v for k, v in vars(cls).items() if k.isupper() and isinstance(v, str))


def build_vocabulary() -> dict:
    return {
        "contract": {"question_code": _constants(QuestionCode), "rule_code": _constants(RuleCode)},
        "explanations": {code: EXPLANATIONS[code] for code in sorted(EXPLANATIONS)},
        "rendered": {name: sorted(m.value for m in enum) for name, enum in sorted(RENDERED.items())},
    }


def render(vocabulary: dict) -> str:
    return json.dumps(vocabulary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    TARGET.write_text(render(build_vocabulary()), encoding="utf-8", newline="\n")
    print(f"escrit {TARGET}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Generate and run the tests**

Run (des de l'arrel del worktree): `C:\Users\tecni\Documents\Licitacions\apps\api\.venv\Scripts\python.exe apps/api/scripts/export_analysis_vocabulary.py`
Run: `…python.exe -m pytest -q -p no:cacheprovider tests/test_analysis_vocabulary_export.py` → PASS.
Run: `…python.exe -m ruff check scripts/export_analysis_vocabulary.py tests/test_analysis_vocabulary_export.py app/api/v1/routers/tender_analysis.py app/core/errors.py app/schemas/tender_analysis.py tests/test_tender_analysis_write_api.py` → net.

- [ ] **Step 5: Checkpoint (sense commit)**

---

### Task 4: `lib/analysis.ts` — tipus, etiquetes, explicacions, destins i resum

**Files:**
- Create: `apps/web/src/lib/analysis.ts`
- Test: `apps/web/src/lib/analysis.test.ts`
- Create: `apps/web/src/test/analysis-fixture.ts`

**Interfaces:**
- Consumes: `analysis-vocabulary.generated.json` (Tasca 3).
- Produces (exports exactes):
  - Tipus: `AnalysisRun`, `AnalysisWriteResult` (`AnalysisRun & { reused: boolean }`), `FitAssessment`, `CriterionAssessment`, `DeliveryCandidate`, `Finding`, `AnalysisQuestion`, `AnalysisSummary`, `RenderedGroup`, `ScopeItemView`, `TenderStructureView`.
  - `LABELS: Record<RenderedGroup, Record<string, string>>`, `LEVELS: Partial<Record<RenderedGroup, Record<string, Level>>>`
  - `label(group: RenderedGroup, value: string): string`, `levelOf(group: RenderedGroup, value: string): Level`
  - `explanation(code: string): string`
  - `RESOLVE: Record<string, ResolveDestination>` i `resolveFor(code: string): ResolveDestination | null`; `ResolveDestination = { href: string; label: string }`
  - `FINDING_ORDER: string[]`, `sortFindings(findings: Finding[]): Finding[]`
  - `summaryOf(run: Pick<AnalysisRun, "summary">): AnalysisSummary`, `summarySentences(summary: AnalysisSummary): string[]`
  - `MISSING_TEXT: Record<string, string>`
  - `analysisPath(companyId: string, tenderId: string): string`

- [ ] **Step 1: Write the fixture**

`apps/web/src/test/analysis-fixture.ts`:

```ts
import type { AnalysisRun, TenderStructureView } from "@/lib/analysis";
import type { RequirementAnalysis } from "@/lib/requirements";

/** Una anàlisi SINTÈTICA: cap dada real. */
export function analysisFixture(overrides: Partial<AnalysisRun> = {}): AnalysisRun {
  return {
    id: "run-1",
    tender_id: "t1",
    rules_version: "phase3a-analysis-v2",
    is_current: true,
    created_at: "2026-10-02T10:00:00Z",
    stale: false,
    summary: {
      fit: { satisfies: 1, does_not_satisfy: 1, unknown: 2, not_applicable: 0 },
      blocker_potential_count: 1,
      review_required_count: 1,
      secured_points: 2,
      at_risk_points: 3,
      lost_points: 0,
      not_assessable_points: 10,
      max_points: 15,
      delivery: { available: 1, pending: 1, unavailable: 0 },
      questions: { buyer: 1, company: 1 },
    },
    fit_assessments: [
      { id: "f1", requirement_id: "r-ok", fit_result: "satisfies", knowledge_source: "current_verified", proof_status: "sufficient", territory_verdict: "not_applicable", verification_status: "verified", rule_code: "fact_requirement_v1", explanation_code: "FACT_TRUE_VERIFIED", support_json: null },
      { id: "f2", requirement_id: "r-no", fit_result: "does_not_satisfy", knowledge_source: "current_declared", proof_status: "unknown", territory_verdict: "not_applicable", verification_status: "not_verified", rule_code: "fact_requirement_v1", explanation_code: "FACT_CONTRADICTS", support_json: null },
      { id: "f3", requirement_id: "r-turnover", fit_result: "unknown", knowledge_source: "none", proof_status: "missing", territory_verdict: "not_applicable", verification_status: "unknown", rule_code: "turnover_requirement_v1", explanation_code: "TURNOVER_UNKNOWN", support_json: null },
      { id: "f4", requirement_id: "r-gone", fit_result: "unknown", knowledge_source: "none", proof_status: "unknown", territory_verdict: "unknown", verification_status: "unknown", rule_code: "unstructured_requirement_v1", explanation_code: "NO_STRUCTURE_TO_COMPARE", support_json: null },
    ],
    need_assessments: [],
    criterion_assessments: [
      { id: "c1", award_criterion_id: "k-auto", criterion_result: "needs_review", knowledge_source: "current_declared", proof_status: "unknown", points_status: "at_risk", max_points: 5, secured_points: 2, at_risk_points: 3, lost_points: 0, not_assessable_points: 0, rule_code: "criterion_from_needs_v1", explanation_code: "CRITERION_AT_RISK", support_json: null },
      { id: "c2", award_criterion_id: "k-judgement", criterion_result: "not_assessable", knowledge_source: "not_applicable", proof_status: "not_required", points_status: "not_assessable", max_points: 10, secured_points: 0, at_risk_points: 0, lost_points: 0, not_assessable_points: 10, rule_code: "criterion_judgement_v1", explanation_code: "CRITERION_NOT_ASSESSABLE_JUDGEMENT", support_json: null },
    ],
    delivery_candidates: [
      { id: "d1", scope_item_id: "s-ftth", company_service_id: "svc-1", partner_role_id: null, candidate_type: "own_current", origin: "current_knowledge", readiness: "available", territory_verdict: "matched", area_verdicts_json: null, rule_code: "delivery_from_services_v1", explanation_code: "DELIVERY_OWN_CURRENT" },
      { id: "d2", scope_item_id: "s-voice", company_service_id: null, partner_role_id: null, candidate_type: "subcontract_unknown", origin: "hypothesis", readiness: "pending_partner", territory_verdict: "unknown", area_verdicts_json: null, rule_code: "delivery_from_services_v1", explanation_code: "DELIVERY_NO_PARTNER_ROLE" },
    ],
    findings: [
      { id: "g-warn", finding_type: "warning", rule_code: "demo_dependency_warning_v1", explanation_code: "DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES", requirement_id: null, criterion_id: "k-auto", scope_item_id: null, details_json: null },
      { id: "g-block", finding_type: "blocker_potential", rule_code: "blocker_potential_v1", explanation_code: "MANDATORY_DOES_NOT_SATISFY", requirement_id: "r-no", criterion_id: null, scope_item_id: null, details_json: null },
      { id: "g-info", finding_type: "missing_information", rule_code: "missing_information_v1", explanation_code: "TURNOVER_UNKNOWN", requirement_id: "r-turnover", criterion_id: null, scope_item_id: null, details_json: null },
    ],
    questions: [
      { id: "q1", audience: "buyer", question_code: "buyer.provide_territory_list", rule_code: "territory_identity_v1", requirement_id: "r-no", criterion_id: null, scope_item_id: null, params_json: {}, text: "Demaneu a l'organ la llista de territoris del requisit «Cobertura»." },
      { id: "q2", audience: "company", question_code: "company.confirm_fact", rule_code: "fact_requirement_v1", requirement_id: "r-turnover", criterion_id: null, scope_item_id: null, params_json: {}, text: "Confirmeu si l'empresa compleix «Suport 24x7» (requisit «Suport»)." },
    ],
    ...overrides,
  };
}

/** Requisits i criteris SINTÈTICS. `r-gone` no hi és a propòsit (arxivat després de l'anàlisi). */
export const requirementsFixture = {
  run: null,
  is_stale: false,
  award_candidates: [],
  requirements: [
    { id: "r-ok", title: "Suport 24x7", category: "technical", origin: "extracted", citations: [], evidence: [{ id: "e1", extraction_id: "x1", page_id: "p1", tender_document_id: "doc1", page_number: 7, quote: "Suport 24x7", char_start: null, char_end: null, section: null, evidence_role: "primary", extraction_revision: 1, document_name: "Plec tècnic.pdf" }] },
    { id: "r-no", title: "Cobertura al municipi", category: "technical", origin: "extracted", citations: [], evidence: [] },
    { id: "r-turnover", title: "Volum de negoci mínim", category: "economic_solvency", origin: "manual", evidence: [], citations: [{ id: "sr1", ref_kind: "extracted_page", document_id: "doc2", document_name: "Plec administratiu.pdf", page_number: 3, quote: null, file_id: null, version_number: null, content_sha256: "x", extraction_id: null, page_id: null, location_label: null, char_start: null, char_end: null, created_at: "2026-10-01T00:00:00Z" }] },
  ],
  award_criteria: [
    { id: "k-auto", name: "Millora de canals", points: 5, category: "improvement", evidence: [] },
    { id: "k-judgement", name: "Memòria tècnica", points: 10, category: "quality", evidence: [] },
  ],
} as unknown as RequirementAnalysis;

export const structureFixture: TenderStructureView = {
  blocks: [{ id: "b1", code: "B1", name: "Bloc de fibra" }],
  sites: [{ id: "site-1", code: "U01", name: "Seu sintètica" }],
  scope_items: [
    { id: "s-ftth", service_line_code: "access_ftth", block_id: "b1", site_id: "site-1" },
    { id: "s-voice", service_line_code: "voice_pbx", block_id: null, site_id: null },
  ],
};
```

- [ ] **Step 2: Write the failing tests**

`apps/web/src/lib/analysis.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import vocabulary from "./analysis-vocabulary.generated.json";
import {
  FINDING_ORDER,
  LABELS,
  RESOLVE,
  explanation,
  label,
  levelOf,
  resolveFor,
  sortFindings,
  summaryOf,
  summarySentences,
  type RenderedGroup,
} from "./analysis";
import { analysisFixture } from "@/test/analysis-fixture";

describe("vocabulari del motor", () => {
  it("cada valor que la UI pinta té text (cobertura contra el JSON generat del backend)", () => {
    for (const [group, values] of Object.entries(vocabulary.rendered)) {
      for (const value of values) {
        expect(LABELS[group as RenderedGroup]?.[value], `${group}.${value}`).toBeTruthy();
      }
    }
  });

  it("no hi ha etiquetes ni destins de valors que el backend ja no té", () => {
    for (const [group, labels] of Object.entries(LABELS)) {
      expect(Object.keys(labels).sort()).toEqual([...(vocabulary.rendered as Record<string, string[]>)[group]].sort());
    }
    for (const code of Object.keys(RESOLVE)) expect(vocabulary.explanations).toHaveProperty(code);
    expect([...FINDING_ORDER].sort()).toEqual([...vocabulary.rendered.finding_type].sort());
  });

  it("les explicacions són el text canònic del backend, tal com és", () => {
    expect(explanation("TURNOVER_UNKNOWN")).toBe(vocabulary.explanations.TURNOVER_UNKNOWN);
    // Un codi que el JSON encara no coneix: text neutre, mai el codi.
    expect(explanation("SOME_NEW_CODE")).toBe("Explicació no disponible.");
  });

  it("«no se sap» és neutre i «verificat» no és mai una font", () => {
    expect(levelOf("fit_result", "unknown")).toBe("neutral");
    expect(levelOf("candidate_type", "subcontract_unknown")).toBe("neutral");
    expect(label("analysis_knowledge_source", "current_verified")).toBe("Coneixement actual de l'empresa");
    expect(Object.values(LABELS.analysis_knowledge_source).join(" ")).not.toMatch(/Verificat/);
    expect(label("assessment_verification", "verified")).toBe("Verificat");
    expect(label("finding_type", "blocker_potential")).toMatch(/no confirmat/);
    expect(label("fit_result", "unexpected")).toBe("Valor no disponible");
  });

  it("«On es resol» només on el destí és inequívoc", () => {
    expect(resolveFor("TURNOVER_UNKNOWN")?.href).toBe("/empresa/coneixement?tab=qualificacio&seccio=solvencia");
    expect(resolveFor("NO_STRUCTURE_TO_COMPARE")).toBeNull();
    expect(resolveFor("CRITERION_NOT_ASSESSABLE_PRICE")).toBeNull();
  });
});

describe("resum i ordre", () => {
  it("els findings segueixen l'ordre de la spec", () => {
    const ordered = sortFindings(analysisFixture().findings).map((f) => f.finding_type);
    expect(ordered).toEqual(["blocker_potential", "missing_information", "warning"]);
  });

  it("les frases surten només dels comptadors i diuen que no se sap no és negatiu", () => {
    const sentences = summarySentences(summaryOf(analysisFixture()));
    expect(sentences.join(" ")).toContain("2 requisits no es poden avaluar amb la informació actual (no se sap): no vol dir que no es compleixin.");
    expect(sentences.join(" ")).toContain("1 possible bloqueig a revisar: cap no està confirmat.");
    expect(sentences.join(" ")).toContain("1 requisit consta com a «No compleix» en l'anàlisi actual.");
    expect(sentences.join(" ")).not.toMatch(/%|puntuació|recomana|dada actual/i);
  });

  it("un resum antic incomplet no peta ni mostra NaN", () => {
    const partial = summaryOf({ summary: { fit: { satisfies: 1 } } as never });
    expect(partial.questions).toEqual({ buyer: 0, company: 0 });
    expect(partial.fit.unknown).toBe(0);
    expect(summarySentences(partial).join(" ")).not.toMatch(/NaN|undefined/);
  });
});
```

- [ ] **Step 3: Run to verify it fails**

Run (des de `apps/web`): `npx vitest run src/lib/analysis.test.ts`
Expected: FAIL (no existeix `./analysis`).

- [ ] **Step 4: Implement `lib/analysis.ts`**

```ts
/**
 * L'anàlisi determinista d'un plec (motor 0051/0052), per a la pestanya «Anàlisi».
 *
 * - Els textos d'explicació són els CANÒNICS del backend (`EXPLANATIONS`), tal com són.
 * - Els valors d'enum els rotula el frontend (`LABELS`); la cobertura es prova contra el
 *   JSON generat des del registre (`analysis-vocabulary.generated.json`).
 * - Cap score, percentatge ni recomanació: només els comptadors que dona el motor.
 */

import type { Level } from "@/components/ui";

import vocabulary from "./analysis-vocabulary.generated.json";

export type RenderedGroup = keyof typeof vocabulary.rendered;

export interface FitAssessment {
  id: string;
  requirement_id: string;
  fit_result: string;
  knowledge_source: string;
  proof_status: string;
  territory_verdict: string;
  verification_status: string;
  rule_code: string;
  explanation_code: string;
  support_json: Record<string, unknown> | null;
}

export interface CriterionAssessment {
  id: string;
  award_criterion_id: string;
  criterion_result: string;
  knowledge_source: string;
  proof_status: string;
  points_status: string;
  max_points: number;
  secured_points: number;
  at_risk_points: number;
  lost_points: number;
  not_assessable_points: number;
  rule_code: string;
  explanation_code: string;
  support_json: Record<string, unknown> | null;
}

export interface DeliveryCandidate {
  id: string;
  scope_item_id: string;
  company_service_id: string | null;
  partner_role_id: string | null;
  candidate_type: string;
  origin: string;
  readiness: string;
  territory_verdict: string;
  area_verdicts_json: unknown[] | null;
  rule_code: string;
  explanation_code: string;
}

export interface Finding {
  id: string;
  finding_type: string;
  rule_code: string;
  explanation_code: string;
  requirement_id: string | null;
  criterion_id: string | null;
  scope_item_id: string | null;
  details_json: Record<string, unknown> | null;
}

export interface AnalysisQuestion {
  id: string;
  audience: "buyer" | "company";
  question_code: string;
  rule_code: string;
  requirement_id: string | null;
  criterion_id: string | null;
  scope_item_id: string | null;
  params_json: Record<string, unknown> | null;
  /** Ja redactada al backend (`question_text`): es mostra tal com arriba. */
  text: string;
}

export interface AnalysisSummary {
  fit: { satisfies: number; does_not_satisfy: number; unknown: number; not_applicable: number };
  blocker_potential_count: number;
  review_required_count: number;
  secured_points: number;
  at_risk_points: number;
  lost_points: number;
  not_assessable_points: number;
  max_points: number;
  delivery: { available: number; pending: number; unavailable: number };
  questions: { buyer: number; company: number };
}

export interface AnalysisRun {
  id: string;
  tender_id: string;
  rules_version: string;
  is_current: boolean;
  created_at: string;
  stale: boolean | null;
  summary: Partial<AnalysisSummary>;
  fit_assessments: FitAssessment[];
  need_assessments: unknown[];
  criterion_assessments: CriterionAssessment[];
  delivery_candidates: DeliveryCandidate[];
  findings: Finding[];
  questions: AnalysisQuestion[];
}

export type AnalysisWriteResult = AnalysisRun & { reused: boolean };

export interface ScopeItemView {
  id: string;
  service_line_code: string;
  block_id: string | null;
  site_id: string | null;
}

export interface TenderStructureView {
  blocks: Array<{ id: string; code: string; name: string }>;
  sites: Array<{ id: string; code: string | null; name: string | null }>;
  scope_items: ScopeItemView[];
}

export function analysisPath(companyId: string, tenderId: string): string {
  return `/companies/${companyId}/tenders/${tenderId}/analysis`;
}

/* ---------------------------------------------------------------- etiquetes */

export const LABELS: Record<RenderedGroup, Record<string, string>> = {
  fit_result: { satisfies: "Compleix", does_not_satisfy: "No compleix", unknown: "No se sap", not_applicable: "No aplica" },
  proof_status: { sufficient: "Prova suficient", partial: "Prova parcial", missing: "Falta la prova", unknown: "Prova: no se sap", not_required: "No cal prova" },
  analysis_knowledge_source: {
    current_verified: "Coneixement actual de l'empresa",
    current_declared: "Coneixement actual de l'empresa",
    historical: "Només històric",
    none: "Cap dada de l'empresa",
    mixed: "Fonts mixtes",
    not_applicable: "Font: no aplica",
  },
  assessment_verification: { verified: "Verificat", not_verified: "No verificat", unknown: "Verificació: no se sap", not_applicable: "Verificació: no aplica" },
  territory_verdict: { matched: "Territori coincident", unmatched: "Territori no coincident", unknown: "Territori: no se sap", not_applicable: "Sense territori" },
  criterion_result: { achievable: "Assolible", not_achievable: "No assolible", needs_review: "Cal revisar", not_assessable: "No avaluable" },
  points_status: { secured: "Assegurats", at_risk: "En risc", lost: "Perduts", mixed: "Mixtos", not_assessable: "No avaluables" },
  candidate_type: { own_current: "Propi", partner_current: "Partner", subcontract_unknown: "Subcontractació: no se sap", own_future: "Propi (hipòtesi)" },
  candidate_origin: { current_knowledge: "Coneixement actual", hypothesis: "Hipòtesi" },
  candidate_readiness: {
    available: "Disponible",
    pending_coverage: "Pendent de cobertura",
    pending_partner: "Pendent de partner",
    pending_validation: "Pendent de validació",
    unavailable: "No disponible",
  },
  finding_type: {
    blocker_potential: "Possible bloqueig (no confirmat)",
    review_required: "Cal revisar",
    evidence_gap: "Falta evidència",
    missing_information: "Falta informació de l'empresa",
    territory_gap: "Mancança de territori",
    delivery_gap: "Mancança d'execució",
    warning: "Avís",
  },
};

export const LEVELS: Partial<Record<RenderedGroup, Record<string, Level>>> = {
  fit_result: { satisfies: "ready", does_not_satisfy: "blocking", unknown: "neutral", not_applicable: "neutral" },
  points_status: { secured: "ready", at_risk: "attention", lost: "blocking", mixed: "attention", not_assessable: "neutral" },
  candidate_readiness: { available: "ready", pending_coverage: "attention", pending_partner: "attention", pending_validation: "attention", unavailable: "neutral" },
  candidate_type: { own_current: "ready", partner_current: "ready", subcontract_unknown: "neutral", own_future: "neutral" },
  finding_type: { blocker_potential: "attention", review_required: "attention", evidence_gap: "attention", missing_information: "neutral", territory_gap: "neutral", delivery_gap: "neutral", warning: "neutral" },
};

export function label(group: RenderedGroup, value: string): string {
  return LABELS[group]?.[value] ?? "Valor no disponible";
}

export function levelOf(group: RenderedGroup, value: string): Level {
  return LEVELS[group]?.[value] ?? "neutral";
}

/** El text canònic del backend, sense reescriure'l. Un codi desconegut: text neutre. */
export function explanation(code: string): string {
  return (vocabulary.explanations as Record<string, string>)[code] ?? "Explicació no disponible.";
}

/* ---------------------------------------------------------- on es resol */

export interface ResolveDestination {
  href: string;
  label: string;
}

const Q = (seccio: string, label: string): ResolveDestination => ({
  href: `/empresa/coneixement?tab=qualificacio&seccio=${seccio}`,
  label: `Coneixement › Qualificació › ${label}`,
});
const CURRENT: ResolveDestination = { href: "/empresa/coneixement?tab=actual", label: "Coneixement › Coneixement actual" };
const PENDING: ResolveDestination = { href: "/empresa/coneixement?tab=pendents", label: "Coneixement › Pendents de validar" };
const QUESTIONS: ResolveDestination = { href: "#preguntes", label: "Preguntes generades" };

/** Només codis amb un destí inequívoc. La resta no té botó. */
export const RESOLVE: Record<string, ResolveDestination> = {
  FACT_UNKNOWN: CURRENT,
  FACT_HISTORIC_ONLY: CURRENT,
  FACT_CONTRADICTS: CURRENT,
  FACT_TRUE_TERRITORY_UNMATCHED: CURRENT,
  CERT_UNKNOWN: CURRENT,
  CERT_HISTORIC_ONLY: CURRENT,
  CERT_LEVEL_BELOW: CURRENT,
  CAPABILITY_UNKNOWN: CURRENT,
  CAPABILITY_HISTORIC_ONLY: CURRENT,
  CAPABILITY_NOT_SUPPORTED: CURRENT,
  CAPABILITY_BELOW_REQUIRED: CURRENT,
  NEEDS_UNKNOWN: CURRENT,
  NEEDS_CONTRADICTED: CURRENT,
  DEMO_DEPENDS_ON_UNKNOWN_CAPABILITIES: CURRENT,
  DELIVERY_NO_SERVICE: CURRENT,
  DELIVERY_MODE_UNKNOWN: CURRENT,
  DELIVERY_NO_PARTNER_ROLE: CURRENT,
  NO_AVAILABLE_DELIVERY: CURRENT,
  TERRITORY_UNKNOWN: CURRENT,
  TERRITORY_UNMATCHED: CURRENT,
  FACT_TRUE_DECLARED: PENDING,
  CERT_CURRENT_DECLARED: PENDING,
  DECLARED_NOT_VERIFIED: PENDING,
  TURNOVER_UNKNOWN: Q("solvencia", "Solvència econòmica"),
  TURNOVER_BELOW: Q("solvencia", "Solvència econòmica"),
  EXECUTION_UNKNOWN: Q("experiencia", "Experiència"),
  EXECUTION_BELOW: Q("experiencia", "Experiència"),
  INSURANCE_UNKNOWN: Q("assegurances", "Assegurances"),
  INSURANCE_BELOW: Q("assegurances", "Assegurances"),
  INSURANCE_EXPIRED: Q("assegurances", "Assegurances"),
  REGISTRATION_UNKNOWN: Q("registres", "Registres"),
  REGISTRATION_SCOPE_UNKNOWN: Q("registres", "Registres"),
  REGISTRATION_SCOPE_NOT_COVERED: Q("registres", "Registres"),
  REGISTRATION_ONLY_OPERATOR_FACT: Q("registres", "Registres"),
  PROFILES_MISSING: Q("persones", "Persones"),
  EQUIPMENT_MISSING: Q("equips", "Equips"),
  EQUIPMENT_CALIBRATION_UNKNOWN: Q("equips", "Equips"),
  EQUIPMENT_CALIBRATION_EXPIRED: Q("equips", "Equips"),
  FACT_NUMBER_BELOW: Q("recursos", "Recursos quantitatius"),
  TERRITORY_LIST_UNAVAILABLE: QUESTIONS,
  CRITERION_TIMING_NOT_CONCLUSIVE: QUESTIONS,
};

export function resolveFor(code: string): ResolveDestination | null {
  return RESOLVE[code] ?? null;
}

/* ------------------------------------------------------- ordre i resum */

export const FINDING_ORDER = [
  "blocker_potential",
  "review_required",
  "evidence_gap",
  "missing_information",
  "territory_gap",
  "delivery_gap",
  "warning",
];

export function sortFindings(findings: Finding[]): Finding[] {
  const rank = (type: string) => {
    const index = FINDING_ORDER.indexOf(type);
    return index === -1 ? FINDING_ORDER.length : index;
  };
  return [...findings].sort((a, b) => rank(a.finding_type) - rank(b.finding_type));
}

const n = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : 0);

/** El resum de l'API amb zeros on falti una clau (anàlisis antigues). Cap càlcul nou. */
export function summaryOf(run: Pick<AnalysisRun, "summary">): AnalysisSummary {
  const s = (run.summary ?? {}) as Partial<AnalysisSummary>;
  return {
    fit: { satisfies: n(s.fit?.satisfies), does_not_satisfy: n(s.fit?.does_not_satisfy), unknown: n(s.fit?.unknown), not_applicable: n(s.fit?.not_applicable) },
    blocker_potential_count: n(s.blocker_potential_count),
    review_required_count: n(s.review_required_count),
    secured_points: n(s.secured_points),
    at_risk_points: n(s.at_risk_points),
    lost_points: n(s.lost_points),
    not_assessable_points: n(s.not_assessable_points),
    max_points: n(s.max_points),
    delivery: { available: n(s.delivery?.available), pending: n(s.delivery?.pending), unavailable: n(s.delivery?.unavailable) },
    questions: { buyer: n(s.questions?.buyer), company: n(s.questions?.company) },
  };
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;
const points = (value: number) => value.toLocaleString("ca-ES", { maximumFractionDigits: 2 });

/** Frases derivades NOMÉS dels comptadors existents. */
export function summarySentences(summary: AnalysisSummary): string[] {
  const out: string[] = [];
  if (summary.blocker_potential_count > 0) {
    out.push(`${plural(summary.blocker_potential_count, "possible bloqueig", "possibles bloqueigs")} a revisar: cap no està confirmat.`);
  }
  if (summary.fit.does_not_satisfy > 0) {
    out.push(`${plural(summary.fit.does_not_satisfy, "requisit consta", "requisits consten")} com a «No compleix» en l'anàlisi actual.`);
  }
  if (summary.fit.unknown > 0) {
    out.push(
      `${plural(summary.fit.unknown, "requisit no es pot", "requisits no es poden")} avaluar amb la informació actual (no se sap): no vol dir que no es compleixin.`,
    );
  }
  if (summary.not_assessable_points > 0) {
    out.push(`${points(summary.not_assessable_points)} punts no es poden avaluar en aquest pas.`);
  }
  return out;
}

/** Codis de `details.missing` del 409 `analysis_prerequisites_missing`. */
export const MISSING_TEXT: Record<string, string> = {
  requirements: "Encara no hi ha requisits per analitzar: processa els plecs i analitza'n els requisits, o afegeix-ne de manuals.",
};
```

Nota: si TypeScript es queixa de la importació JSON, comprova que `tsconfig.json` té `"resolveJsonModule": true` (Next.js el porta per defecte); no cal cap altre canvi.

- [ ] **Step 5: Run the tests**

Run: `npx vitest run src/lib/analysis.test.ts` → PASS. Run: `npx tsc --noEmit` → 0 errors.

- [ ] **Step 6: Checkpoint (sense commit)**

---

### Task 5: Pestanya, estats i recàlcul (`AnalysisTab` + `AnalysisSummary`)

**Files:**
- Create: `apps/web/src/components/tender-analysis/AnalysisTab.tsx`
- Create: `apps/web/src/components/tender-analysis/AnalysisSummary.tsx`
- Modify: `apps/web/src/app/(app)/licitacions/[tenderId]/header.ts` (`TabKey`, `TABS`)
- Modify: `apps/web/src/app/(app)/licitacions/[tenderId]/page.tsx` (render de la pestanya)
- Test: `apps/web/src/components/tender-analysis/AnalysisTab.test.tsx`

**Interfaces:**
- Consumes: `lib/analysis.ts` (Tasca 4); `canReview`, `whoCanReview` (`lib/review.ts`); `useResource` (`lib/hooks`), `api` (`lib/api`).
- Produces: `export function AnalysisTab(props: { companyId: string | null; tenderId: string; requirements: RequirementAnalysis | null; workspaceId: string | null; candidatureChecked: boolean })`. Les seccions de les Tasques 6-7 es munten dins `AnalysisTab` amb les props: `{ run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }`, on `AnalysisLookup` s'exporta d'`AnalysisTab.tsx`:

```ts
export interface AnalysisLookup {
  requirements: Map<string, Requirement>;
  criteria: Map<string, AwardCriterion>;
  structure: TenderStructureView | null;
  vocabulary: Vocabulary | null;
  services: Map<string, Service>;
  partnerRoles: Map<string, { role: string; partner: string | null }>;
}
```

- [ ] **Step 1: Write the failing tests**

`apps/web/src/components/tender-analysis/AnalysisTab.test.tsx`:

```tsx
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { analysisFixture, requirementsFixture, structureFixture } from "@/test/analysis-fixture";

const get = vi.fn();
const post = vi.fn();
let role: string | undefined = "owner";
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } };
});
vi.mock("@/components/SessionProvider", () => ({ useSession: () => ({ companyId: "c1", membership: role ? { role } : undefined }) }));
vi.mock("@/components/WorkspaceLauncher", () => ({ WorkspaceLauncher: () => <button type="button">Obre la candidatura</button> }));

import { ApiError } from "@/lib/api";

import { AnalysisTab } from "./AnalysisTab";

const BASE = "/companies/c1/tenders/t1/analysis";
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

function respond(current: unknown) {
  get.mockImplementation((path: string) => {
    if (path === `${BASE}/current`) return current instanceof Error ? Promise.reject(current) : Promise.resolve(current);
    if (path === "/companies/c1/tenders/t1/structure") return Promise.resolve(structureFixture);
    return Promise.resolve(undefined);
  });
}

async function renderTab() {
  render(<AnalysisTab companyId="c1" tenderId="t1" requirements={requirementsFixture} workspaceId={null} candidatureChecked />);
  await flush();
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  role = "owner";
});

describe("Pestanya Anàlisi: estats", () => {
  it("sense anàlisi (404) és l'estat normal, amb què és i què no és, i el botó Analitza", async () => {
    respond(new ApiError(404, "not_found", "Cap analisi vigent."));
    await renderTab();
    expect(screen.getByText(/Encara no hi ha cap anàlisi/)).toBeTruthy();
    expect(screen.getByText(/no decideix per tu/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Analitza" })).toBeTruthy();
    expect(screen.queryByText(/No s'ha pogut/)).toBeNull();
  });

  it("sense permís, el botó es veu desactivat amb qui ho pot fer", async () => {
    role = "viewer";
    respond(new ApiError(404, "not_found", "x"));
    await renderTab();
    expect((screen.getByRole("button", { name: "Analitza" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Només qui té el rol/)).toBeTruthy();
  });

  it("409 de prerequisits sense anàlisi: explica què falta a partir del codi", async () => {
    respond(new ApiError(404, "not_found", "x"));
    post.mockRejectedValue(new ApiError(409, "analysis_prerequisites_missing", "No hi ha requisits.", [], { missing: ["requirements"] }));
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Analitza" }));
    await flush();
    expect(screen.getByText(/Encara no hi ha requisits per analitzar/)).toBeTruthy();
    expect(screen.queryByText("No hi ha requisits.")).toBeNull();
  });

  it("409 amb una anàlisi desactualitzada: l'antiga continua visible i el motiu surt al costat de Recalcula", async () => {
    respond(analysisFixture({ stale: true }));
    post.mockRejectedValue(new ApiError(409, "analysis_prerequisites_missing", "x", [], { missing: ["requirements"] }));
    await renderTab();
    expect(screen.getByText("Desactualitzada")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Recalcula" }));
    await flush();
    expect(screen.getByText(/No es pot recalcular fins que es resolgui/)).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Què cal resoldre abans de decidir" })).toBeTruthy();
  });

  it("recalcular: un sol POST encara que es cliqui dues vegades, i anuncia si és la mateixa", async () => {
    respond(analysisFixture({ stale: true }));
    let resolvePost: (value: unknown) => void = () => {};
    post.mockImplementation(() => new Promise((resolve) => { resolvePost = resolve; }));
    await renderTab();
    const button = screen.getByRole("button", { name: "Recalcula" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(`${BASE}/runs`, {});
    await act(async () => resolvePost({ ...analysisFixture(), reused: true }));
    expect(screen.getByRole("status", { name: "Estat del recàlcul" }).textContent).toContain(
      "Les dades no han canviat: és la mateixa anàlisi.",
    );
  });

  it("reused amb un altre id (p. ex. ja recalculada des d'un altre lloc) anuncia «actualitzada», no «la mateixa»", async () => {
    respond(analysisFixture({ stale: true }));
    post.mockResolvedValue({ ...analysisFixture({ id: "run-2" }), reused: true });
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Recalcula" }));
    await flush();
    expect(screen.getByRole("status", { name: "Estat del recàlcul" }).textContent).toBe("Anàlisi actualitzada.");
  });

  it("conflicte concurrent i errors: text humà, mai JSON ni codi", async () => {
    respond(analysisFixture());
    post.mockRejectedValue(new ApiError(409, "analysis_run_conflict", "x"));
    await renderTab();
    fireEvent.click(screen.getByRole("button", { name: "Recalcula" }));
    await flush();
    expect(screen.getByText(/S'ha recalculat alhora des d'un altre lloc/)).toBeTruthy();
  });

  it("resum factual sense score ni percentatge, i cap codi ni UUID visible", async () => {
    respond(analysisFixture());
    const { container } = render(<AnalysisTab companyId="c1" tenderId="t1" requirements={requirementsFixture} workspaceId={null} candidatureChecked />);
    await flush();
    const summary = screen.getByRole("region", { name: "Resum de l'anàlisi" });
    expect(within(summary).getByText(/No se sap: 2/)).toBeTruthy();
    expect(summary.textContent).toMatch(/2 requisits no es poden avaluar/);
    expect(container.textContent).not.toMatch(/%|score|recomana|[0-9a-f]{8}-[0-9a-f]{4}-|FACT_|_v1|run-1/i);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npx vitest run src/components/tender-analysis/AnalysisTab.test.tsx` → FAIL (no existeix).

- [ ] **Step 3: Implement `AnalysisSummary.tsx`**

```tsx
import { StatusPill } from "@/components/ui";
import { label, levelOf, summaryOf, summarySentences, type AnalysisRun } from "@/lib/analysis";

const pts = (value: number) => value.toLocaleString("ca-ES", { maximumFractionDigits: 2 });

/** Resum factual: només els comptadors del motor. Cap score, percentatge ni recomanació. */
export function AnalysisSummary({ run, compact = false }: { run: AnalysisRun; compact?: boolean }) {
  const summary = summaryOf(run);
  const fits: Array<[string, number]> = [
    ["satisfies", summary.fit.satisfies],
    ["does_not_satisfy", summary.fit.does_not_satisfy],
    ["unknown", summary.fit.unknown],
    ["not_applicable", summary.fit.not_applicable],
  ];
  return (
    <section aria-label="Resum de l'anàlisi" className="space-y-3">
      <dl className={compact ? "grid grid-cols-1 gap-3" : "grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"}>
        <div>
          <dt className="text-caption font-medium text-muted">Requisits</dt>
          <dd className="mt-1 flex flex-wrap gap-1.5">
            {fits.map(([value, count]) => (
              <StatusPill key={value} level={levelOf("fit_result", value)} size="sm" outline>
                {label("fit_result", value)}: {count}
              </StatusPill>
            ))}
          </dd>
        </div>
        <div>
          <dt className="text-caption font-medium text-muted">Punts (de {pts(summary.max_points)})</dt>
          <dd className="mt-1 text-body text-ink tabular-nums">
            Assegurats {pts(summary.secured_points)} · En risc {pts(summary.at_risk_points)} · Perduts {pts(summary.lost_points)} · No avaluables{" "}
            {pts(summary.not_assessable_points)}
          </dd>
        </div>
        {!compact && (
          <div>
            <dt className="text-caption font-medium text-muted">Execució</dt>
            <dd className="mt-1 text-body text-ink tabular-nums">
              {label("candidate_readiness", "available")} {summary.delivery.available} · Pendent {summary.delivery.pending} ·{" "}
              {label("candidate_readiness", "unavailable")} {summary.delivery.unavailable}
            </dd>
          </div>
        )}
        <div>
          <dt className="text-caption font-medium text-muted">Preguntes generades</dt>
          <dd className="mt-1 text-body text-ink tabular-nums">
            Per al comprador {summary.questions.buyer} · Per a l&apos;empresa {summary.questions.company}
          </dd>
        </div>
      </dl>
      <ul className="space-y-1 text-body text-ink">
        {summarySentences(summary).map((sentence) => (
          <li key={sentence}>{sentence}</li>
        ))}
      </ul>
    </section>
  );
}
```

- [ ] **Step 4: Implement `AnalysisTab.tsx`**

```tsx
"use client";

import { useMemo, useRef, useState } from "react";

import { useSession } from "@/components/SessionProvider";
import { Banner, Button, Card, CardHeader, EmptyState, ErrorNotice, LoadingRows, PermissionNote } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { MISSING_TEXT, analysisPath, type AnalysisRun, type AnalysisWriteResult, type TenderStructureView } from "@/lib/analysis";
import { formatDateTime } from "@/lib/labels";
import { useResource } from "@/lib/hooks";
import type { KnowledgeRecords, Vocabulary } from "@/lib/knowledge";
import type { AwardCriterion, Requirement, RequirementAnalysis } from "@/lib/requirements";
import { canReview, whoCanReview } from "@/lib/review";
import type { Service } from "@/lib/types";

import { AnalysisSummary } from "./AnalysisSummary";
import { CriteriaPoints } from "./CriteriaPoints";
import { DecisionFooter } from "./DecisionFooter";
import { Delivery } from "./Delivery";
import { Questions } from "./Questions";
import { RequirementsFit } from "./RequirementsFit";
import { ResolveBeforeDeciding } from "./ResolveBeforeDeciding";

export interface AnalysisLookup {
  requirements: Map<string, Requirement>;
  criteria: Map<string, AwardCriterion>;
  structure: TenderStructureView | null;
  vocabulary: Vocabulary | null;
  services: Map<string, Service>;
  partnerRoles: Map<string, { role: string; partner: string | null }>;
}

function code(error: unknown): string | null {
  return error && typeof error === "object" && "code" in error ? String((error as { code: unknown }).code) : null;
}

function missingOf(error: unknown): string[] {
  const details = error instanceof ApiError ? error.details : (error as { details?: Record<string, unknown> })?.details;
  const missing = details?.missing;
  return Array.isArray(missing) ? missing.map(String) : [];
}

function writeErrorText(error: unknown): string | null {
  switch (code(error)) {
    case "analysis_prerequisites_missing":
      return null; // es pinta amb `missingOf`
    case "analysis_run_conflict":
      return "S'ha recalculat alhora des d'un altre lloc: torna-ho a provar.";
    case "permission_denied":
      return `El teu rol no pot recalcular l'anàlisi: ho fa ${whoCanReview()}.`;
    default:
      return "No s'ha pogut recalcular l'anàlisi. Torna-ho a provar.";
  }
}

export function AnalysisTab({
  companyId,
  tenderId,
  requirements,
  workspaceId,
  candidatureChecked,
}: {
  companyId: string | null;
  tenderId: string;
  requirements: RequirementAnalysis | null;
  workspaceId: string | null;
  candidatureChecked: boolean;
}) {
  const session = useSession() as { membership?: { role?: string } };
  const mayRecalculate = canReview(session.membership?.role);
  const base = companyId ? analysisPath(companyId, tenderId) : null;
  const current = useResource<AnalysisRun>(base ? `${base}/current` : null);
  const structure = useResource<TenderStructureView>(companyId ? `/companies/${companyId}/tenders/${tenderId}/structure` : null);
  const vocabulary = useResource<Vocabulary>(companyId ? `/companies/${companyId}/knowledge/vocabulary` : null);
  const services = useResource<Service[]>(companyId ? `/companies/${companyId}/services` : null);
  const records = useResource<KnowledgeRecords>(companyId ? `/companies/${companyId}/knowledge/records` : null);
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [writeError, setWriteError] = useState<unknown>(null);
  const [announcement, setAnnouncement] = useState("");

  const shown = run ?? current.data ?? null;
  const notFound = current.status === 404;

  const lookup = useMemo<AnalysisLookup>(() => {
    const roles = new Map<string, { role: string; partner: string | null }>();
    const partners = new Map((records.data?.partners ?? []).map((p) => [p.id, p.name]));
    for (const role of records.data?.partner_roles ?? []) roles.set(role.id, { role: role.role, partner: partners.get(role.partner_id) ?? null });
    return {
      requirements: new Map((requirements?.requirements ?? []).map((r) => [r.id, r])),
      criteria: new Map([...(requirements?.award_criteria ?? []), ...(requirements?.award_candidates ?? [])].map((c) => [c.id, c])),
      structure: structure.data ?? null,
      vocabulary: vocabulary.data ?? null,
      services: new Map((services.data ?? []).map((s) => [s.id, s])),
      partnerRoles: roles,
    };
  }, [requirements, structure.data, vocabulary.data, services.data, records.data]);

  async function recalculate() {
    if (!base || inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setWriteError(null);
    setAnnouncement("");
    try {
      const result = await api.post<AnalysisWriteResult>(`${base}/runs`, {});
      // `reused` vol dir «cap anàlisi nova escrita», no «la mateixa que hi havia a la pantalla».
      const same = result.reused && result.id === shown?.id;
      setRun(result);
      setAnnouncement(same ? "Les dades no han canviat: és la mateixa anàlisi." : "Anàlisi actualitzada.");
    } catch (error) {
      setWriteError(error);
      setAnnouncement("No s'ha pogut recalcular l'anàlisi.");
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const missing = code(writeError) === "analysis_prerequisites_missing" ? missingOf(writeError) : [];
  const otherError = writeError && missing.length === 0 ? writeErrorText(writeError) : null;
  const secondaryFailed = Boolean(structure.error || services.error || records.error || vocabulary.error || !requirements);

  const action = (text: string) =>
    mayRecalculate ? (
      <Button variant={shown ? "secondary" : "primary"} loading={busy} aria-busy={busy} disabled={busy} onClick={() => void recalculate()}>
        {text}
      </Button>
    ) : (
      <PermissionNote who={whoCanReview()} what="recalcular l'anàlisi">
        <Button variant={shown ? "secondary" : "primary"} disabled>
          {text}
        </Button>
      </PermissionNote>
    );

  const live = (
    <p role="status" aria-live="polite" aria-label="Estat del recàlcul" className="sr-only">
      {announcement}
    </p>
  );
  const missingBlock = missing.length > 0 && (
    <div role="alert" className="rounded-card border border-warning/25 bg-warning-soft px-4 py-3 text-body text-ink">
      {shown && <p className="font-medium">No es pot recalcular fins que es resolgui:</p>}
      <ul className="mt-1 list-disc pl-5">
        {missing.map((m) => (
          <li key={m}>{MISSING_TEXT[m] ?? "Falta una dada necessària per analitzar."}</li>
        ))}
      </ul>
    </div>
  );

  if (!companyId) return null;
  if (current.loading && !shown && !notFound && !current.error) {
    return <Card><LoadingRows rows={4} /></Card>;
  }
  if (!shown && current.error && !notFound) {
    return <ErrorNotice message="No s'ha pogut carregar l'anàlisi." onRetry={() => void current.reload()} />;
  }
  if (!shown) {
    return (
      <Card>
        {live}
        <EmptyState
          title="Encara no hi ha cap anàlisi d'aquesta licitació"
          description="L'anàlisi contrasta els requisits del plec amb el que se sap de l'empresa i explica què es compleix, què no i què no se sap. No decideix per tu: la decisió de presentar-s'hi és vostra."
          action={action("Analitza")}
        />
        <div className="px-5 pb-5 space-y-3">
          {missingBlock}
          {otherError && <ErrorNotice message={otherError} />}
        </div>
      </Card>
    );
  }

  const stale = run ? false : Boolean(shown.stale);
  return (
    <div className="flex min-w-0 flex-col gap-4">
      {live}
      <Card>
        <CardHeader
          title="Anàlisi del plec"
          description={`Feta el ${formatDateTime(shown.created_at)}. Tuvi explica què sap i què no: la decisió és vostra.`}
          action={action("Recalcula")}
        />
        <div className="space-y-3 px-5 pb-5">
          {stale && (
            <Banner level="attention" kicker="Desactualitzada">
              Alguna dada ha canviat des d&apos;aquesta anàlisi (el plec, la revisió o el coneixement de l&apos;empresa). Recalcula-la per veure-la al dia.
            </Banner>
          )}
          {missingBlock}
          {otherError && <ErrorNotice message={otherError} />}
          {secondaryFailed && (
            <p className="text-caption text-muted">Algunes dades complementàries no s&apos;han pogut carregar: alguns noms poden no aparèixer.</p>
          )}
          <AnalysisSummary run={shown} />
        </div>
      </Card>
      <ResolveBeforeDeciding run={shown} lookup={lookup} tenderId={tenderId} />
      <RequirementsFit run={shown} lookup={lookup} tenderId={tenderId} />
      <CriteriaPoints run={shown} lookup={lookup} tenderId={tenderId} />
      <Delivery run={shown} lookup={lookup} tenderId={tenderId} />
      <Questions run={shown} lookup={lookup} tenderId={tenderId} />
      <DecisionFooter companyId={companyId} tenderId={tenderId} workspaceId={workspaceId} checked={candidatureChecked} />
    </div>
  );
}
```

Per poder compilar abans de les Tasques 6-7, crea de moment els sis fitxers de secció amb un cos mínim que retorni `null` i la signatura definitiva, per exemple:

```tsx
import type { AnalysisRun } from "@/lib/analysis";
import type { AnalysisLookup } from "./AnalysisTab";

export function ResolveBeforeDeciding(_: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  return null;
}
```

(el mateix per a `RequirementsFit`, `CriteriaPoints`, `Delivery`, `Questions`; i `DecisionFooter(_: { companyId: string; tenderId: string; workspaceId: string | null; checked: boolean })`). Les Tasques 6-7 els substitueixen sencers. Perquè el test de 409 amb anàlisi vigent trobi l'encapçalament «Què cal resoldre abans de decidir», `ResolveBeforeDeciding` ha de pintar-lo ja en aquesta tasca:

```tsx
import { Card, CardHeader } from "@/components/ui";
export function ResolveBeforeDeciding(_: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  return <Card><CardHeader title="Què cal resoldre abans de decidir" /></Card>;
}
```

- [ ] **Step 5: Wire the tab into the tender page**

A `header.ts`:

```ts
export type TabKey = "resum" | "plecs" | "requisits" | "analisi" | "historial";

export const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "resum", label: "Resum" },
  { key: "plecs", label: "Plecs" },
  { key: "requisits", label: "Requisits extrets" },
  { key: "analisi", label: "Anàlisi" },
  { key: "historial", label: "Historial" },
];
```

A `page.tsx`, importa `import { AnalysisTab } from "@/components/tender-analysis/AnalysisTab";` i, just abans del bloc `{/* === Historial === */}`:

```tsx
      {/* ========================================================= Anàlisi === */}
      {tab === "analisi" && (
        <AnalysisTab
          companyId={companyId}
          tenderId={tenderId}
          requirements={analysis.data}
          workspaceId={candidature.workspace?.id ?? null}
          candidatureChecked={candidature.checked}
        />
      )}
```

Afegeix a `header.test.ts` un cas: `tabFromParam("analisi")` → `"analisi"` i `TABS` conté «Anàlisi» abans d'«Historial».

- [ ] **Step 6: Run the tests**

Run: `npx vitest run src/components/tender-analysis src/app/(app)/licitacions` → PASS (els tests existents de la pàgina continuen verds). Run: `npx tsc --noEmit`.

- [ ] **Step 7: Checkpoint (sense commit)**

---

### Task 6: «Què cal resoldre», Requisits i Criteris (+ cites)

**Files:**
- Create: `apps/web/src/components/tender-analysis/Citation.tsx`
- Replace: `ResolveBeforeDeciding.tsx`, `RequirementsFit.tsx`, `CriteriaPoints.tsx`
- Test: `apps/web/src/components/tender-analysis/sections.test.tsx`

**Interfaces:**
- Consumes: `AnalysisLookup` (Tasca 5), `lib/analysis.ts` (Tasca 4), `CATEGORY_LABELS` (`lib/requirements.ts`), `EvidenceRef` (`components/ui`).
- Produces: `export function Citation({ tenderId, requirement, criterion }: { tenderId: string; requirement?: Requirement; criterion?: AwardCriterion })` — retorna `null` si no hi ha cap cita real. `export function requirementTitle(lookup: AnalysisLookup, id: string | null): string | null` i `criterionName(...)` a `Citation.tsx`.

- [ ] **Step 1: Write the failing tests**

`apps/web/src/components/tender-analysis/sections.test.tsx`:

```tsx
import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { analysisFixture, requirementsFixture, structureFixture } from "@/test/analysis-fixture";

import type { AnalysisLookup } from "./AnalysisTab";
import { CriteriaPoints } from "./CriteriaPoints";
import { RequirementsFit } from "./RequirementsFit";
import { ResolveBeforeDeciding } from "./ResolveBeforeDeciding";

function lookup(overrides: Partial<AnalysisLookup> = {}): AnalysisLookup {
  return {
    requirements: new Map(requirementsFixture.requirements.map((r) => [r.id, r])),
    criteria: new Map(requirementsFixture.award_criteria.map((c) => [c.id, c])),
    structure: structureFixture,
    vocabulary: { service_lines: [{ code: "access_ftth", label: "Accessos FTTH" }, { code: "voice_pbx", label: "Centraleta i veu IP" }], capabilities: [], standards: [] },
    services: new Map([["svc-1", { id: "svc-1", name: "Desplegament sintètic" } as never]]),
    partnerRoles: new Map(),
    ...overrides,
  };
}

describe("Què cal resoldre abans de decidir", () => {
  it("ordre per tipus, «no confirmat», text canònic, cita i On es resol només quan existeixen", () => {
    render(<ResolveBeforeDeciding run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("Possible bloqueig (no confirmat)");
    expect(items[0].textContent).toContain("Cobertura al municipi");
    expect(within(items[0]).queryByRole("link", { name: /On es resol/ })).toBeNull(); // MANDATORY_DOES_NOT_SATISFY: sense destí
    expect(items[1].textContent).toContain("Falta informació de l'empresa");
    expect(items[1].textContent).not.toMatch(/no confirmat/);
    expect(items[2].textContent).not.toMatch(/no confirmat/);
    expect(within(items[1]).getByRole("link", { name: /On es resol/ }).getAttribute("href")).toBe("/empresa/coneixement?tab=qualificacio&seccio=solvencia");
    expect(items[1].textContent).toContain("Plec administratiu.pdf"); // cita manual amb pàgina
    expect(screen.getByText(/2 preguntes generades/)).toBeTruthy();
  });

  it("sense findings: ho diu en neutre", () => {
    render(<ResolveBeforeDeciding run={analysisFixture({ findings: [] })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText(/No hi ha cap element pendent de resoldre/)).toBeTruthy();
  });
});

describe("Requisits", () => {
  it("agrupats per categoria, quatre dimensions separades i «verificat» només com a verificació", () => {
    render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const ok = screen.getByText("Suport 24x7").closest("li") as HTMLElement;
    expect(within(ok).getByText("Compleix")).toBeTruthy();
    expect(within(ok).getByText("Prova suficient")).toBeTruthy();
    expect(within(ok).getByText("Coneixement actual de l'empresa")).toBeTruthy();
    expect(within(ok).getByText("Verificat")).toBeTruthy();
    expect(within(ok).getByText("Sense territori")).toBeTruthy();
    expect(within(ok).getByText(/p\. 7/)).toBeTruthy();
  });

  it("un requisit que la lectura ja no té no mostra cap UUID", () => {
    const { container } = render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText("Requisit sense títol disponible")).toBeTruthy();
    expect(container.textContent).not.toContain("r-gone");
  });

  it("expandible amb teclat i aria-expanded; el text del motor tal com és", () => {
    render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const toggle = screen.getAllByRole("button", { name: /Per què/ })[0];
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(document.getElementById(toggle.getAttribute("aria-controls") as string)).toBeTruthy();
  });

  it("filtres per estat amb aria-pressed i recompte anunciat", () => {
    render(<RequirementsFit run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    const filter = screen.getByRole("button", { name: /No se sap/ });
    fireEvent.click(filter);
    expect(filter.getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("status", { name: "Recompte de requisits" }).textContent).toMatch(/2 requisits/);
    expect(screen.queryByText("Suport 24x7")).toBeNull();
  });
});

describe("Criteris i punts", () => {
  it("una fila per criteri amb punts i totals; sense criteris, text neutre", () => {
    const { rerender } = render(<CriteriaPoints run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText("Millora de canals")).toBeTruthy();
    expect(screen.getByText("Memòria tècnica")).toBeTruthy();
    expect(screen.getByText(/Total/).closest("tr")?.textContent).toContain("15");
    rerender(<CriteriaPoints run={analysisFixture({ criterion_assessments: [] })} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText(/El plec no té criteris de puntuació analitzats/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npx vitest run src/components/tender-analysis/sections.test.tsx` → FAIL.

- [ ] **Step 3: Implement `Citation.tsx`**

```tsx
import { EvidenceRef } from "@/components/ui";
import type { AwardCriterion, Requirement } from "@/lib/requirements";

import type { AnalysisLookup } from "./AnalysisTab";

export function requirementTitle(lookup: AnalysisLookup, id: string | null): string | null {
  if (!id) return null;
  return lookup.requirements.get(id)?.title ?? "Requisit sense títol disponible";
}

export function criterionName(lookup: AnalysisLookup, id: string | null): string | null {
  if (!id) return null;
  return lookup.criteria.get(id)?.name ?? "Criteri sense nom disponible";
}

/** La cita del plec, només si existeix: extreta (`evidence`) o manual (`citations`). */
export function Citation({ tenderId, requirement, criterion }: { tenderId: string; requirement?: Requirement; criterion?: AwardCriterion }) {
  const evidence = (requirement?.evidence ?? criterion?.evidence ?? []).find((e) => e.evidence_role === "primary") ??
    (requirement?.evidence ?? criterion?.evidence ?? [])[0];
  if (evidence) {
    const href = evidence.tender_document_id
      ? `/licitacions/${tenderId}/documents/${evidence.tender_document_id}?page=${evidence.page_number}&extraction=${evidence.extraction_id}`
      : null;
    return <EvidenceRef compact documentName={evidence.document_name} pageNumber={evidence.page_number} href={href} />;
  }
  const manual = requirement?.citations?.find((c) => c.document_name || c.page_number);
  if (manual) return <EvidenceRef compact documentName={manual.document_name} pageNumber={manual.page_number} href={null} />;
  return null;
}
```

- [ ] **Step 4: Implement `ResolveBeforeDeciding.tsx`**

```tsx
import Link from "next/link";

import { Card, CardHeader, StatusPill } from "@/components/ui";
import { explanation, label, levelOf, resolveFor, sortFindings, summaryOf, type AnalysisRun } from "@/lib/analysis";

import type { AnalysisLookup } from "./AnalysisTab";
import { Citation, criterionName, requirementTitle } from "./Citation";

export function ResolveBeforeDeciding({ run, lookup, tenderId }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  const findings = sortFindings(run.findings);
  const questions = summaryOf(run).questions;
  const total = questions.buyer + questions.company;
  return (
    <Card id="que-cal-resoldre" className="border-2 border-brand/30">
      <CardHeader
        title="Què cal resoldre abans de decidir"
        description="Possibles bloqueigs, revisions, evidència i informació de l'empresa que falta, i les preguntes generades. Els possibles bloqueigs no estan confirmats."
      />
      {findings.length === 0 ? (
        <p className="px-5 pb-5 text-body text-muted">No hi ha cap element pendent de resoldre en aquesta anàlisi.</p>
      ) : (
        <ul className="divide-y divide-border">
          {findings.map((finding) => {
            const destination = resolveFor(finding.explanation_code);
            const subject = requirementTitle(lookup, finding.requirement_id) ?? criterionName(lookup, finding.criterion_id);
            const requirement = finding.requirement_id ? lookup.requirements.get(finding.requirement_id) : undefined;
            const criterion = finding.criterion_id ? lookup.criteria.get(finding.criterion_id) : undefined;
            return (
              <li key={finding.id} className="flex flex-col gap-2 px-5 py-3.5 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0 space-y-1">
                  <StatusPill level={levelOf("finding_type", finding.finding_type)} size="sm">
                    {label("finding_type", finding.finding_type)}
                  </StatusPill>
                  {subject && <p className="text-body font-medium text-ink">{subject}</p>}
                  <p className="text-body text-ink">{explanation(finding.explanation_code)}</p>
                  <Citation tenderId={tenderId} requirement={requirement} criterion={criterion} />
                </div>
                {destination && (
                  <Link href={destination.href} className="inline-flex min-h-11 shrink-0 items-center text-body font-medium text-brand hover:underline md:min-h-0">
                    On es resol: {destination.label}
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {total > 0 && (
        <p className="border-t border-border px-5 py-3 text-body text-ink">
          <a href="#preguntes" className="text-brand hover:underline">
            {total === 1 ? "1 pregunta generada" : `${total} preguntes generades`}
          </a>{" "}
          a tractar.
        </p>
      )}
    </Card>
  );
}
```

- [ ] **Step 5: Implement `RequirementsFit.tsx`**

```tsx
"use client";

import { useId, useState } from "react";

import { Card, CardHeader, StatusPill } from "@/components/ui";
import { LABELS, explanation, label, levelOf, resolveFor, type AnalysisRun, type FitAssessment } from "@/lib/analysis";
import { CATEGORY_LABELS } from "@/lib/requirements";

import type { AnalysisLookup } from "./AnalysisTab";
import { Citation, requirementTitle } from "./Citation";

const FILTERS = Object.keys(LABELS.fit_result);

function Row({ fit, lookup, tenderId }: { fit: FitAssessment; lookup: AnalysisLookup; tenderId: string }) {
  const [open, setOpen] = useState(false);
  const panel = useId();
  const destination = resolveFor(fit.explanation_code);
  const requirement = lookup.requirements.get(fit.requirement_id);
  return (
    <li className="space-y-2 px-5 py-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-body font-medium text-ink">{requirementTitle(lookup, fit.requirement_id)}</p>
        <StatusPill level={levelOf("fit_result", fit.fit_result)} size="sm">
          {label("fit_result", fit.fit_result)}
        </StatusPill>
      </div>
      <Citation tenderId={tenderId} requirement={requirement} />
      <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-caption sm:grid-cols-2 lg:grid-cols-4">
        <div><dt className="sr-only">Prova</dt><dd>{label("proof_status", fit.proof_status)}</dd></div>
        <div><dt className="sr-only">Font del coneixement</dt><dd>{label("analysis_knowledge_source", fit.knowledge_source)}</dd></div>
        <div><dt className="sr-only">Verificació</dt><dd>{label("assessment_verification", fit.verification_status)}</dd></div>
        <div><dt className="sr-only">Territori</dt><dd>{label("territory_verdict", fit.territory_verdict)}</dd></div>
      </dl>
      <button
        type="button"
        className="min-h-11 text-caption font-medium text-brand hover:underline md:min-h-0"
        aria-expanded={open}
        aria-controls={panel}
        onClick={() => setOpen(!open)}
      >
        Per què?
      </button>
      <div id={panel} hidden={!open} className="text-body text-ink">
        <p>{explanation(fit.explanation_code)}</p>
        {destination && (
          <a href={destination.href} className="text-brand hover:underline">
            On es resol: {destination.label}
          </a>
        )}
      </div>
    </li>
  );
}

export function RequirementsFit({ run, lookup, tenderId }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  const [filter, setFilter] = useState<string | null>(null);
  const shown = run.fit_assessments.filter((fit) => !filter || fit.fit_result === filter);
  const groups = new Map<string, FitAssessment[]>();
  for (const fit of shown) {
    const category = lookup.requirements.get(fit.requirement_id)?.category;
    const key = category ? (CATEGORY_LABELS[category] ?? "Altres") : "Sense categoria";
    groups.set(key, [...(groups.get(key) ?? []), fit]);
  }
  return (
    <Card id="requisits-analisi">
      <CardHeader title="Requisits" description="Cada requisit amb el seu estat i el perquè. «No se sap» vol dir que falta informació, no que no es compleixi." />
      <div className="flex flex-wrap gap-2 px-5" role="group" aria-label="Filtra per estat">
        {FILTERS.map((value) => (
          <button
            key={value}
            type="button"
            aria-pressed={filter === value}
            onClick={() => setFilter(filter === value ? null : value)}
            className="min-h-11 rounded-pill border border-border-strong px-3 text-caption md:min-h-0"
          >
            {label("fit_result", value)} ({run.fit_assessments.filter((f) => f.fit_result === value).length})
          </button>
        ))}
      </div>
      <p role="status" aria-live="polite" aria-label="Recompte de requisits" className="px-5 pt-2 text-caption text-muted">
        {shown.length === 1 ? "1 requisit" : `${shown.length} requisits`}
      </p>
      {shown.length === 0 ? (
        <p className="px-5 pb-5 text-body text-muted">Cap requisit amb aquest estat.</p>
      ) : (
        [...groups.entries()].map(([category, fits]) => (
          <section key={category} aria-label={category}>
            <h3 className="bg-subtle px-5 py-2 text-caption font-semibold text-muted uppercase">{category}</h3>
            <ul className="divide-y divide-border">
              {fits.map((fit) => (
                <Row key={fit.id} fit={fit} lookup={lookup} tenderId={tenderId} />
              ))}
            </ul>
          </section>
        ))
      )}
    </Card>
  );
}
```

- [ ] **Step 6: Implement `CriteriaPoints.tsx`**

```tsx
import { Card, CardHeader, StatusPill } from "@/components/ui";
import { explanation, label, levelOf, summaryOf, type AnalysisRun } from "@/lib/analysis";

import type { AnalysisLookup } from "./AnalysisTab";
import { Citation, criterionName } from "./Citation";

const pts = (value: number) => value.toLocaleString("ca-ES", { maximumFractionDigits: 2 });

export function CriteriaPoints({ run, lookup, tenderId }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  const summary = summaryOf(run);
  return (
    <Card id="criteris-analisi">
      <CardHeader title="Criteris i punts" description="Punts que les dades actuals asseguren, deixen en risc o fan perdre. Els no avaluables no s'avaluen en aquest pas." />
      {run.criterion_assessments.length === 0 ? (
        <p className="px-5 pb-5 text-body text-muted">El plec no té criteris de puntuació analitzats.</p>
      ) : (
        <div className="overflow-x-auto px-5 pb-5">
          <table className="w-full min-w-[40rem] text-left text-body">
            <thead className="text-caption text-muted">
              <tr>
                <th scope="col" className="py-2">Criteri</th>
                <th scope="col">Estat</th>
                <th scope="col" className="text-right">Màxim</th>
                <th scope="col" className="text-right">Assegurats</th>
                <th scope="col" className="text-right">En risc</th>
                <th scope="col" className="text-right">Perduts</th>
                <th scope="col" className="text-right">No avaluables</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {run.criterion_assessments.map((c) => (
                <tr key={c.id} className="align-top">
                  <td className="py-2.5 pr-3">
                    <p className="font-medium text-ink">{criterionName(lookup, c.award_criterion_id)}</p>
                    <p className="text-caption text-muted">{explanation(c.explanation_code)}</p>
                    <Citation tenderId={tenderId} criterion={lookup.criteria.get(c.award_criterion_id)} />
                  </td>
                  <td className="py-2.5 pr-3">
                    <StatusPill level={levelOf("points_status", c.points_status)} size="sm">
                      {label("points_status", c.points_status)}
                    </StatusPill>
                    <p className="mt-1 text-caption text-muted">{label("criterion_result", c.criterion_result)}</p>
                  </td>
                  <td className="text-right tabular-nums">{pts(c.max_points)}</td>
                  <td className="text-right tabular-nums">{pts(c.secured_points)}</td>
                  <td className="text-right tabular-nums">{pts(c.at_risk_points)}</td>
                  <td className="text-right tabular-nums">{pts(c.lost_points)}</td>
                  <td className="text-right tabular-nums">{pts(c.not_assessable_points)}</td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t-2 border-border font-medium">
                <td className="py-2.5">Total</td>
                <td />
                <td className="text-right tabular-nums">{pts(summary.max_points)}</td>
                <td className="text-right tabular-nums">{pts(summary.secured_points)}</td>
                <td className="text-right tabular-nums">{pts(summary.at_risk_points)}</td>
                <td className="text-right tabular-nums">{pts(summary.lost_points)}</td>
                <td className="text-right tabular-nums">{pts(summary.not_assessable_points)}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      )}
    </Card>
  );
}
```

- [ ] **Step 7: Run the tests** — `npx vitest run src/components/tender-analysis` → PASS; `npx tsc --noEmit`.

- [ ] **Step 8: Checkpoint (sense commit)**

---

### Task 7: Execució, Preguntes i peu de decisió

**Files:**
- Replace: `Delivery.tsx`, `Questions.tsx`, `DecisionFooter.tsx`
- Test: `apps/web/src/components/tender-analysis/sections.test.tsx` (afegir)

**Interfaces:**
- Consumes: `AnalysisLookup`, `lib/analysis.ts`, `serviceLineLabel` i `PARTNER_ROLE_LABELS` (`lib/knowledge.ts`), `WorkspaceLauncher`.
- Produces: `Delivery`, `Questions` (amb `id="preguntes"`), `DecisionFooter({ companyId, tenderId, workspaceId, checked })`.

- [ ] **Step 1: Write the failing tests** (afegeix a `sections.test.tsx`)

```tsx
import { vi } from "vitest";
vi.mock("@/components/WorkspaceLauncher", () => ({ WorkspaceLauncher: () => <button type="button">Obre la candidatura</button> }));

import { DecisionFooter } from "./DecisionFooter";
import { Delivery } from "./Delivery";
import { Questions } from "./Questions";

describe("Execució", () => {
  it("tradueix fidelment els candidats, amb noms i sense UUIDs; subcontractació desconeguda neutra", () => {
    const { container } = render(<Delivery run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByText("Accessos FTTH")).toBeTruthy();
    expect(screen.getByText(/Bloc de fibra/)).toBeTruthy();
    expect(screen.getByText("Propi")).toBeTruthy();
    expect(screen.getByText(/Desplegament sintètic/)).toBeTruthy();
    expect(screen.getByText("Subcontractació: no se sap")).toBeTruthy();
    expect(screen.getByText("Pendent de partner")).toBeTruthy();
    expect(container.textContent).not.toMatch(/s-ftth|svc-1|precio|preu|BOM/);
  });

  it("no hi ha abast: ho diu en neutre", () => {
    render(
      <Delivery
        run={analysisFixture({ delivery_candidates: [] })}
        lookup={lookup({ structure: { ...structureFixture, scope_items: [] } })}
        tenderId="t1"
      />,
    );
    expect(screen.getByText("El plec no té elements d'abast estructurats.")).toBeTruthy();
  });

  it("hi ha abast però cap candidat: es mostra l'element sense inventar-ne cap", () => {
    const structure = {
      ...structureFixture,
      scope_items: [...structureFixture.scope_items, { id: "s-extra", service_line_code: "civil_works", block_id: null, site_id: null }],
    };
    render(
      <Delivery
        run={analysisFixture()}
        lookup={lookup({
          structure,
          vocabulary: { service_lines: [{ code: "access_ftth", label: "Accessos FTTH" }, { code: "voice_pbx", label: "Centraleta i veu IP" }, { code: "civil_works", label: "Obra civil" }], capabilities: [], standards: [] },
        })}
        tenderId="t1"
      />,
    );
    const extra = screen.getByText("Obra civil").closest("li") as HTMLElement;
    expect(within(extra).getByText("Cap candidat d'execució en aquesta anàlisi.")).toBeTruthy();
    expect(within(extra).queryByText(/Propi|Partner|Subcontractació/)).toBeNull();
  });
});

describe("Preguntes", () => {
  it("dues llistes, text tal com arriba, «generades» i mai «obertes»", () => {
    const { container } = render(<Questions run={analysisFixture()} lookup={lookup()} tenderId="t1" />);
    expect(screen.getByRole("heading", { name: "Per al comprador" })).toBeTruthy();
    expect(screen.getByText("Demaneu a l'organ la llista de territoris del requisit «Cobertura».")).toBeTruthy();
    expect(container.textContent).toMatch(/generades/);
    expect(container.textContent).not.toMatch(/obertes|resoltes/i);
  });
});

describe("Peu de decisió", () => {
  it("la decisió és vostra; amb candidatura, enllaç al pas Decidir", () => {
    render(<DecisionFooter companyId="c1" tenderId="t1" workspaceId="w1" checked />);
    expect(screen.getByText(/Tuvi no recomana sí ni no/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Continua la candidatura per decidir" }).getAttribute("href")).toBe("/candidatures/w1?pas=decidir");
  });
  it("sense candidatura: el llançador existent", () => {
    render(<DecisionFooter companyId="c1" tenderId="t1" workspaceId={null} checked />);
    expect(screen.getByRole("button", { name: "Obre la candidatura" })).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify they fail** — `npx vitest run src/components/tender-analysis/sections.test.tsx`.

- [ ] **Step 3: Implement `Delivery.tsx`**

```tsx
import { Card, CardHeader, StatusPill } from "@/components/ui";
import { explanation, label, levelOf, type AnalysisRun, type DeliveryCandidate } from "@/lib/analysis";
import { PARTNER_ROLE_LABELS, serviceLineLabel } from "@/lib/knowledge";

import type { AnalysisLookup } from "./AnalysisTab";

function who(candidate: DeliveryCandidate, lookup: AnalysisLookup): string | null {
  if (candidate.company_service_id) return lookup.services.get(candidate.company_service_id)?.name ?? null;
  if (candidate.partner_role_id) {
    const role = lookup.partnerRoles.get(candidate.partner_role_id);
    if (!role) return null;
    return [role.partner, PARTNER_ROLE_LABELS[role.role] ?? null].filter(Boolean).join(" · ") || null;
  }
  return null;
}

export function Delivery({ run, lookup }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  const items = lookup.structure?.scope_items ?? [];
  const byItem = new Map<string, DeliveryCandidate[]>();
  for (const candidate of run.delivery_candidates) byItem.set(candidate.scope_item_id, [...(byItem.get(candidate.scope_item_id) ?? []), candidate]);
  // Tots els elements de l'estructura (també els que no tenen cap candidat) i, a més,
  // els que només surten als candidats (estructura no carregada o desfasada).
  const ids = [...items.map((s) => s.id), ...[...byItem.keys()].filter((id) => !items.some((s) => s.id === id))];
  return (
    <Card id="execucio-analisi">
      <CardHeader title="Execució" description="Qui podria executar cada part de l'abast segons el coneixement actual. No és cap disseny de solució ni cap selecció de proveïdors." />
      {ids.length === 0 ? (
        <p className="px-5 pb-5 text-body text-muted">El plec no té elements d&apos;abast estructurats.</p>
      ) : (
        <ul className="divide-y divide-border">
          {ids.map((id) => {
            const item = items.find((s) => s.id === id);
            const block = item?.block_id ? lookup.structure?.blocks.find((b) => b.id === item.block_id) : undefined;
            const site = item?.site_id ? lookup.structure?.sites.find((s) => s.id === item.site_id) : undefined;
            const title = item ? serviceLineLabel(item.service_line_code, lookup.vocabulary) : "Element d'abast sense nom disponible";
            return (
              <li key={id} className="space-y-2 px-5 py-3.5">
                <p className="text-body font-medium text-ink">{title}</p>
                {(block || site) && (
                  <p className="text-caption text-muted">{[block && `${block.code} · ${block.name}`, site && (site.name ?? site.code)].filter(Boolean).join(" — ")}</p>
                )}
                {(byItem.get(id) ?? []).length === 0 && (
                  <p className="text-body text-muted">Cap candidat d&apos;execució en aquesta anàlisi.</p>
                )}
                <ul className="space-y-1.5">
                  {(byItem.get(id) ?? []).map((c) => (
                    <li key={c.id} className="flex flex-wrap items-center gap-2 text-body">
                      <StatusPill level={levelOf("candidate_type", c.candidate_type)} size="sm">{label("candidate_type", c.candidate_type)}</StatusPill>
                      {who(c, lookup) && <span className="text-ink">{who(c, lookup)}</span>}
                      <StatusPill level={levelOf("candidate_readiness", c.readiness)} size="sm" outline>{label("candidate_readiness", c.readiness)}</StatusPill>
                      <span className="text-caption text-muted">{label("territory_verdict", c.territory_verdict)}</span>
                      <span className="w-full text-caption text-muted">{explanation(c.explanation_code)}</span>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
```

- [ ] **Step 4: Implement `Questions.tsx`**

```tsx
import { Card, CardHeader } from "@/components/ui";
import type { AnalysisQuestion, AnalysisRun } from "@/lib/analysis";

import type { AnalysisLookup } from "./AnalysisTab";
import { criterionName, requirementTitle } from "./Citation";

function List({ title, items, lookup }: { title: string; items: AnalysisQuestion[]; lookup: AnalysisLookup }) {
  return (
    <section className="px-5 pb-4">
      <h3 className="text-[13px] font-semibold text-ink">{title}</h3>
      {items.length === 0 ? (
        <p className="text-body text-muted">Cap pregunta generada.</p>
      ) : (
        <ul className="mt-1 space-y-2">
          {items.map((q) => {
            const about = requirementTitle(lookup, q.requirement_id) ?? criterionName(lookup, q.criterion_id);
            return (
              <li key={q.id} className="text-body text-ink">
                <p>{q.text}</p>
                {about && <p className="text-caption text-muted">Sobre: {about}</p>}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function Questions({ run, lookup }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  return (
    <Card id="preguntes">
      <CardHeader title="Preguntes" description="Preguntes generades per l'anàlisi, a tractar abans de decidir." />
      <List title="Per al comprador" items={run.questions.filter((q) => q.audience === "buyer")} lookup={lookup} />
      <List title="Per a l'empresa" items={run.questions.filter((q) => q.audience === "company")} lookup={lookup} />
    </Card>
  );
}
```

- [ ] **Step 5: Implement `DecisionFooter.tsx`**

```tsx
import { WorkspaceLauncher } from "@/components/WorkspaceLauncher";
import { Card, LinkButton } from "@/components/ui";

export function DecisionFooter({ companyId, tenderId, workspaceId, checked }: { companyId: string; tenderId: string; workspaceId: string | null; checked: boolean }) {
  return (
    <Card>
      <div className="flex flex-col gap-3 px-5 py-4 md:flex-row md:items-center md:justify-between">
        <p className="text-body text-ink">La decisió de presentar-s&apos;hi és vostra: Tuvi no recomana sí ni no.</p>
        {workspaceId ? (
          <LinkButton href={`/candidatures/${workspaceId}?pas=decidir`} variant="primary" className="min-h-11 md:min-h-0">
            Continua la candidatura per decidir
          </LinkButton>
        ) : (
          <WorkspaceLauncher companyId={companyId} tenderId={tenderId} existing={null} checked={checked} className="min-h-11 md:min-h-0" />
        )}
      </div>
    </Card>
  );
}
```

- [ ] **Step 6: Run** — `npx vitest run src/components/tender-analysis` → PASS; `npx tsc --noEmit`.

- [ ] **Step 7: Checkpoint (sense commit)**

---

### Task 8: `DecisionAnalysisCard` al pas Decidir

**Files:**
- Create: `apps/web/src/components/tender-analysis/DecisionAnalysisCard.tsx`
- Modify: `apps/web/src/app/(app)/candidatures/[workspaceId]/page.tsx` (columna dreta del pas `decidir`, abans de `BlockerCounters`)
- Test: `apps/web/src/components/tender-analysis/DecisionAnalysisCard.test.tsx`

**Interfaces:**
- Consumes: `AnalysisSummary` (Tasca 5), `summaryOf`, `sortFindings`, `analysisPath` (Tasca 4).
- Produces: `export function DecisionAnalysisCard({ companyId, tenderId }: { companyId: string; tenderId: string })`.

- [ ] **Step 1: Write the failing test**

```tsx
import { act, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { analysisFixture } from "@/test/analysis-fixture";

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } };
});

import { ApiError } from "@/lib/api";

import { DecisionAnalysisCard } from "./DecisionAnalysisCard";

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => { get.mockReset(); post.mockReset(); });

describe("Targeta d'anàlisi al pas Decidir", () => {
  it("vigent: el mateix resum factual i l'enllaç; mai recalcula", async () => {
    get.mockResolvedValue(analysisFixture());
    render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByRole("region", { name: "Resum de l'anàlisi" })).toBeTruthy();
    expect(screen.getByText(/3 elements a resoldre/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Veure l'anàlisi" }).getAttribute("href")).toBe("/licitacions/t1?tab=analisi");
    expect(screen.queryByRole("button", { name: /Recalcula|Analitza/ })).toBeNull();
    expect(post).not.toHaveBeenCalled();
  });

  it("desactualitzada: etiqueta amb text i enllaç a recalcular a la pestanya", async () => {
    get.mockResolvedValue(analysisFixture({ stale: true }));
    render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByText("Desactualitzada")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Revisa i recalcula a la pestanya Anàlisi" })).toBeTruthy();
  });

  it("sense anàlisi (404): estat normal; error: nota neutra; mai bloqueja", async () => {
    get.mockRejectedValue(new ApiError(404, "not_found", "x"));
    const { unmount } = render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByText("Encara no hi ha anàlisi d'aquesta licitació.")).toBeTruthy();
    unmount();
    get.mockRejectedValue(new ApiError(500, "error", "boom"));
    render(<DecisionAnalysisCard companyId="c1" tenderId="t1" />);
    await flush();
    expect(screen.getByText("No s'ha pogut carregar l'anàlisi.")).toBeTruthy();
    expect(screen.queryByText("boom")).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement**

```tsx
"use client";

import { AnalysisSummary } from "@/components/tender-analysis/AnalysisSummary";
import { Card, CardHeader, LinkButton, StatusPill } from "@/components/ui";
import { analysisPath, type AnalysisRun } from "@/lib/analysis";
import { useResource } from "@/lib/hooks";

/** Lectura de l'anàlisi al pas Decidir. Fa servir els mateixos helpers; mai fa cap POST. */
export function DecisionAnalysisCard({ companyId, tenderId }: { companyId: string; tenderId: string }) {
  const current = useResource<AnalysisRun>(`${analysisPath(companyId, tenderId)}/current`);
  const href = `/licitacions/${tenderId}?tab=analisi`;
  const body = (() => {
    if (current.status === 404) return <p className="text-body text-muted">Encara no hi ha anàlisi d&apos;aquesta licitació.</p>;
    if (current.error) return <p className="text-body text-muted">No s&apos;ha pogut carregar l&apos;anàlisi.</p>;
    if (!current.data) return null;
    const pending = current.data.findings.length;
    return (
      <div className="space-y-3">
        {current.data.stale && (
          <StatusPill level="attention" size="sm">
            Desactualitzada
          </StatusPill>
        )}
        <AnalysisSummary run={current.data} compact />
        <p className="text-body text-ink">{pending === 1 ? "1 element a resoldre" : `${pending} elements a resoldre`} abans de decidir.</p>
      </div>
    );
  })();
  const stale = Boolean(current.data?.stale);
  return (
    <Card>
      <CardHeader title="Anàlisi del plec" description="El que Tuvi sap i no sap d'aquesta licitació. La decisió és vostra." />
      <div className="space-y-3 px-5 pb-5">
        {body}
        <LinkButton href={href} variant="secondary" className="min-h-11 md:min-h-0">
          {stale ? "Revisa i recalcula a la pestanya Anàlisi" : "Veure l'anàlisi"}
        </LinkButton>
      </div>
    </Card>
  );
}
```

A `candidatures/[workspaceId]/page.tsx`, dins `{step === "decidir" && (...)}`, a la columna `lg:col-span-5` just abans de `<BlockerCounters workspace={data} />`:

```tsx
              {companyId && <DecisionAnalysisCard companyId={companyId} tenderId={data.tender_id} />}
```

(amb `import { DecisionAnalysisCard } from "@/components/tender-analysis/DecisionAnalysisCard";` i `companyId` de `useSession()`, que la pàgina ja fa servir; si no, `const { companyId } = useSession();`). Al test existent de la pàgina de candidatura, afegeix al mapa de respostes `"/companies/c1/tenders/t1/analysis/current": notFound()` perquè la targeta no faci soroll.

- [ ] **Step 4: Run** — `npx vitest run src/components/tender-analysis src/app/(app)/candidatures` → PASS.

- [ ] **Step 5: Checkpoint (sense commit)**

---

### Task 9: Documentació, gates i QA visual focal

**Files:**
- Modify: `docs/analysis-engine.md` (nova subsecció «UI: pestanya Anàlisi» i «Vocabulari per al web»)

- [ ] **Step 1: Documentation**

Afegeix a `docs/analysis-engine.md`, després de §6.2:

```markdown
### 6.2b UI: pestanya «Anàlisi» (fase 3A, pas 7)

- `/licitacions/{id}?tab=analisi` mostra l'anàlisi vigent: resum factual, «Què cal resoldre abans de
  decidir», requisits, criteris i punts, execució (només `delivery_candidates`) i preguntes generades.
  Cap score, percentatge ni recomanació: la decisió és humana, al pas Decidir de la candidatura.
- `POST /companies/{c}/tenders/{t}/analysis/runs` executa (o reutilitza) l'anàlisi: síncron, sense LLM,
  `reused` a la resposta, 201/200. Permís: `review_actions.assert_can_review`. 409
  `analysis_prerequisites_missing` (`details.missing = ["requirements"]`) si no hi ha requisits efectius;
  una anàlisi existent no es toca. Cursa: un reintent; si tampoc, 409 `analysis_run_conflict`.
- Els textos d'explicació són els canònics d'`EXPLANATIONS` (tal com són); els valors d'enum els rotula
  el web. Vocabulari exportat a `apps/web/src/lib/analysis-vocabulary.generated.json`. Regenerar:

      apps/api/.venv/Scripts/python apps/api/scripts/export_analysis_vocabulary.py
```

- [ ] **Step 2: Backend gates** (des de `apps/api`)

```
LLM_PROVIDER=fake …python.exe -m pytest -q -p no:cacheprovider tests/test_tender_analysis_write_api.py tests/test_analysis_vocabulary_export.py tests/test_tender_analysis_engine.py tests/test_tender_analysis_pure.py tests/test_tender_analysis_gold_lescala.py tests/test_company_qualification_api.py
```
PostgreSQL focal (PowerShell, com a la Tasca 2) amb `tests/test_tender_analysis_write_api.py tests/test_tender_analysis_engine.py`.
`…python.exe -m ruff check` i `ruff format --check` només als fitxers tocats/nous (no reformatar fitxers que ja eren sense format a `5502ef5`).

- [ ] **Step 3: Frontend gates** (des de `apps/web`)

`npx vitest run` (complet), `npm run typecheck`, `npm run lint`, `npm run build`.

- [ ] **Step 4: Repo gates** — `git diff --check`; escaneig de secrets i dades reals al diff (cap DSN, clau, IP, `indalecc`, `escala`, `cnmc` fora d'asserts negatius).

- [ ] **Step 5: QA visual focal** (base d'usar i llençar amb dades sintètiques; scripts del scratchpad `qa_step6.py` / `qa_shots.py` adaptats): captures a escriptori (1440) i mòbil (390) de la pestanya amb anàlisi, sense anàlisi i desactualitzada amb 409, i de la targeta al pas Decidir. Comprova: cap overflow horitzontal, cap estat només per color, focus visible a filtres/expandibles/recalcular, accions de 44 px a mòbil. Esborra la base en acabar.

- [ ] **Step 6: Informe i aturada** — informa de fitxers, tests, gates i QA. **Sense commit** fins que l'usuari ho aprovi.
