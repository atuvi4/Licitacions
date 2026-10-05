# F-4b: pla d'implementació

> Execució: subagents seqüencials, un implementador i una revisió independent per tasca, revisió de contracte després de les tasques de backend i revisió final de tota la branca. Sense commit fins que l'usuari ho aprovi. TDD: primer els tests, en vermell.

**Spec:** `docs/superpowers/specs/2026-10-05-decisions-v3-design.md`
**Worktree:** `C:\Users\tecni\Documents\Licitacions-decisions-v3`, branca `engine/phase3a-decisions-v3`, des de `d6a183b`.

## Restriccions globals

- No es toca el pilot, ni el motor v3 (`app/analysis/*`, `analyze_tender`), ni les taules o el model del compliance antic. Cap camp legacy s'esborra ni canvia de significat.
- Migració **additiva** única (`0054_decision_analysis_v3`): dues columnes nul·lables, cap backfill, `downgrade` reversible.
- Decisions existents immutables; es llegeixen com a legacy.
- Snapshot v1: schema Pydantic tancat i versionat (`extra="forbid"`, invariants absent vs current/stale); sense score, recomanació, GO/NO-GO ni `blocker_confirmed`. Les seves `warnings` són `AnalysisDecisionWarnings.codes()` del mateix objecte que alimenta l'API.
- Decisions v3 noves sense fotografia legacy: `compliance_run_id`, `requirement_run_id`, `automated_recommendation`, `open_warnings` = NULL; comptadors legacy = 0; es mantenen `prefit_score`, `deadline_passed`, `tender_revision_id`.
- Una sola font de `(run, stale)`: `tender_analysis.current_analysis`.
- `POST /decisions` atòmic: un sol commit per a decisió + canvi d'estat + audit log; `reconcile_readiness` després.
- `LLM_PROVIDER=fake`; dades sintètiques als tests; el gold públic de L'Escala per a l'acceptació.
- Codi i comentaris del backend sense accents (convenció); textos de la UI amb accents.

## Focus de revisió

1. El mateix `(run, stale)` a `/analysis/current` i a `record_decision` en el mateix instant (test que crida tots dos amb la mateixa sessió).
2. Un error després de construir el snapshot (p. ex. `set_status` que llança) no deixa cap decisió.
3. Un workspace de lot usa el run v3 del tender (no hi ha run per lot).
4. `_summary` (llistes) no crida `is_stale`.
5. Fila antiga sense `analysis_snapshot_json`: `DecisionRead` i historial idèntics a abans.

---

### Task 1: font canònica `current_analysis`
- **Fitxers:** `app/services/tender_analysis.py` (`AnalysisStatus`, `current_analysis`), `app/api/v1/routers/tender_analysis.py` (usa la funció), `tests/test_tender_analysis_write_api.py` o nou `tests/test_tender_analysis_status.py`.
- **Tests:** sense run → `(None, False)`; run vigent → `(run, False)`; després d'un canvi d'entrada (p. ex. requisit nou) → `(run, True)`; `GET /analysis/current` retorna el mateix `stale` que la funció en la mateixa sessió; el router continua donant 404 sense run. Res més canvia (gold i write_api verds).

### Task 2: migració 0054 + model + schema
- **Fitxers:** `alembic/versions/0054_decision_analysis_v3.py` (id ≤ 32 caràcters), `app/models/workspace.py` (`analysis_run_id` FK `fk_workspace_decisions_analysis_run` SET NULL + índex, `analysis_snapshot_json` JSON), `app/schemas/workspace.py` (`DecisionRead.analysis_run_id`, `analysis_snapshot_json`), `tests/test_migrations.py` (offline up/down `0053→0054`).
- **Tests:** columnes del model = columnes de les migracions (test existent); `downgrade` reversible; una fila antiga (sense les columnes informades) es llegeix amb `analysis_run_id = None` i `analysis_snapshot_json = None`; assaig `alembic upgrade` sobre PG de test amb files legacy sintètiques → intactes.

### Task 3: snapshot v1 + avisos v3 (pur)
- **Fitxers:** `app/workspace/decision.py` (`AnalysisDecisionWarnings` amb `codes()` en ordre fix, `any_open`; `requires_reason` acceptant qualsevol objecte amb `any_open`), nou `app/workspace/analysis_snapshot.py` (schema Pydantic `AnalysisDecisionSnapshotV1` amb `extra="forbid"`, `schema_version: Literal[1]`, `state: Literal[...]`, sub-models `fit` i `points` també `extra="forbid"`, `model_validator` d'invariants absent vs current/stale; `build_snapshot(status: AnalysisStatus, warnings: AnalysisDecisionWarnings) -> AnalysisDecisionSnapshotV1`; `parse_snapshot(data) -> AnalysisDecisionSnapshotV1`), `tests/test_workspace_decision_v3.py`.
- **Tests purs:** els quatre codis en l'ordre fix, inclòs un cas amb els quatre alhora i un amb tres (ordre estable); `any_open` i `requires_reason` per a GO / NO-GO / defer; snapshot amb run `current`, `stale` i `absent` (claus exactes); el schema rebutja claus extra (`score`, `recommendation`, `go`, `blocker_confirmed`, qualsevol), rebutja `absent` amb camps del run i `current` sense; `warnings` del snapshot == `warnings.codes()`; `blocker_potential_count` i `findings_count` copiats de `summary_of` i `len(findings)`; `model_dump()` és JSON-serialitzable (cap Decimal/datetime cru) i `parse_snapshot(model_dump())` és idempotent.

### Task 4: `load_state` i `record_decision` atòmic
- **Fitxers:** `app/services/bid_workspace.py` (`WorkspaceState.analysis: AnalysisStatus | None`, `analysis_warnings`, `load_state(..., with_analysis=True)`, `record_decision` en una transacció), `app/api/v1/routers/workspaces.py` (`_detail`: `decision_warnings`, `decision_warning_basis`, `go_requires_reason` v3; `_summary` sense `is_stale`; `DecisionReasonRequiredError.details` amb `basis`), `app/schemas/workspace.py` (`WorkspaceDetail` camps nous).
- **Comportament:** `record_decision(session, workspace, *, decision, reason, role, user_id, now=None)`: 1 `current_analysis` → 2 `AnalysisDecisionWarnings` → 3 `requires_reason` → 4 `build_snapshot` → 5 `WorkspaceDecision(analysis_run_id, analysis_snapshot_json=snapshot.model_dump(), compliance_run_id=None, requirement_run_id=None, automated_recommendation=None, open_warnings=None, comptadors legacy=0, prefit_score, deadline_passed, tender_revision_id)` → 6 `set_status` → 7 `audit.record(...)` (la crida es mou del router al servei, mateix `action`/`entity_type`/`summary`) → 8 un sol `commit`. Cap `commit` intermedi; en cas d'error, `rollback` i cap fila (decisió, estat ni audit). El router només crida `record_decision`, després `reconcile_readiness` i `_detail`.
- **Tests** (`tests/test_bid_workspace.py` + nou `tests/test_bid_workspace_v3.py`): acceptació §10 punts 2–12 i 11b/11c/11d; atomicitat amb `set_status` que llança i amb `audit.record` que llança (0 decisions, estat intacte, 0 files d'audit per a l'entitat); workspace de lot usa el run del tender; `_summary` no crida `is_stale` (spy); `details.basis == "analysis_v3"`; a les decisions noves `compliance_run_id`, `requirement_run_id`, `automated_recommendation` i `open_warnings` són NULL i els comptadors 0, mentre que `prefit_score`, `deadline_passed` i `tender_revision_id` s'omplen; variar el compliance antic (cap run / indeterminat / amb bloquejos confirmats / proveïdor no fiable) amb la mateixa v3 no canvia `decision_warnings` ni `go_requires_reason`; `snapshot["warnings"] == detail.decision_warnings` en el moment de decidir.
- **Adaptació explícita de tests existents** (llista a l'informe, cadascun amb el motiu): `test_a_go_with_a_blocker_requires_a_written_reason` (bloqueig legacy ja no obliga; passa a comprovar que NO obliga i que el motiu l'exigeix `analysis_missing` si no hi ha run); `test_un_go_sense_veredicte_exigeix_que_algu_ho_escrigui` i `test_the_api_refuses_a_go_without_reason_when_there_are_warnings` (vocabulari v3); `test_a_go_with_an_open_warning_needs_a_written_reason` (parametritzat sobre `AnalysisDecisionWarnings`); `test_a_go_records_who_when_and_why` (ja no assereix `compliance_run_id`/`requirement_run_id` a la decisió nova; assereix `analysis_run_id`/snapshot); `test_una_decisio_desa_els_avisos_que_hi_havia` i `test_la_fotografia_no_canvia_quan_canvia_lestat` passen a una fila legacy inserida directament (snapshot NULL, `open_warnings` legacy) i se n'afegeix la versió v3 amb `snapshot.warnings`; `test_una_decisio_antiga_diu_que_no_es_va_registrar` es manté (fila legacy amb `open_warnings` NULL) i s'hi afegeix que una decisió v3 nova amb `open_warnings` NULL NO es presenta com a «no registrat» (la UI branca pel snapshot). **Auditoria dels setups que fan GO sense motiu i sense run v3** (`test_workspace_dossier.py:229, 1108, 1452`; `test_workspace_issues.py:67, 1064`): o bé creen un run v3 vigent, o bé passen motiu; es documenta el que s'ha fet a cada un.

### Task 5: acceptació L'Escala (backend)
- **Fitxers:** `tests/test_bid_workspace_lescala.py`.
- **Test:** gold + bessó com al gold de l'anàlisi; workspace per l'empresa; `analyze_tender` → run; `record_decision(GO, reason=None)` → permès; `decision.analysis_run_id == run.id`; snapshot `fit` 4/1/19/10, `points` 0/27/5/68, `blocker_potential_count` 0, `state` `current`, `warnings == []`; C1 `lost_points == 5` al run; després un canvi de coneixement (p. ex. una àrea de cobertura nova) + `analyze_tender` → run nou vigent; la decisió antiga continua idèntica (snapshot i `analysis_run_id`); `GET /analysis/current` i `load_state` diuen `stale` igual abans de recalcular.

**Revisió de contracte (opus) després de T1–T5:** atomicitat, immutabilitat legacy, igualtat `(run, stale)`, claus del snapshot, semàntica additiva de l'API, tests adaptats justificats.

### Task 6: web — historial, panell i modal amb base v3
- **Fitxers:** `lib/workspace.ts` (`WorkspaceDecisionEntry.analysis_run_id`, `analysis_snapshot_json`; `WorkspaceDetail.decision_warnings`, `decision_warning_basis`; `ANALYSIS_WARNING_LABELS` tancat + `analysisWarningLabel()`), `components/WorkspaceDecision.tsx` (panell i modal llegeixen `decision_warnings`; copy «avisos de l'anàlisi del plec» a tots els llocs de F-4 §5; `DecisionHistory` branca per `analysis_snapshot_json`), `test/workspace-fixture.ts`, tests.
- **Tests:** decisió antiga (snapshot `null`) → «Registre del moment (compliment anterior)» idèntic, amb `open_warnings` legacy; decisió nova `current`/`stale`/`absent` → «Registre del moment (anàlisi del plec)…» amb els comptadors del snapshot i «Es va decidir sabent-ho: {etiqueta v3 de `snapshot.warnings`}» (amb `open_warnings` NULL, mai «no es van registrar»); mai dades legacy sota el títol v3 ni viceversa; la branca és només `analysis_snapshot_json`; panell/modal amb `decision_warnings` (4 codis + desconegut → text neutre); `go_requires_reason` governa el botó; POST `{decision, reason}` inalterat; cap GET nova; `open_warnings` ja no s'usa per decidir (grep al test).

### Task 7: gates, QA i revisió final
- **Backend:** suites d'anàlisi, workspace (`test_bid_workspace*`, `test_workspace_*`), migracions, tenant_ops, a SQLite i PG; ruff.
- **Web:** vitest complet, typecheck, lint, build; `git diff --check`; escaneig de secrets i dades.
- **QA aïllada** (DB disposable, `LLM_PROVIDER=fake`, ports 3600/8600/9333): L'Escala gold + bessó, run v3, workspace: GO sense motiu permès amb run vigent; després invalidar el run (canvi de coneixement) → `analysis_stale` i motiu obligatori amb el copy nou; historial amb les dues classes d'entrada (una legacy sintètica inserida abans de la migració simulada, o fila amb snapshot NULL); 1440 i 390; 0 escriptures fora de les decisions provades.
- **Revisió final** (opus), una onada si cal, aturada sense commit.
