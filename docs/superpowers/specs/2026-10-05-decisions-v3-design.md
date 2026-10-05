# F-4b: decisions conscients de l'anàlisi v3

**Branca:** `engine/phase3a-decisions-v3`, des de `d6a183b` (v3 + F-4), al worktree `Licitacions-decisions-v3`.
**Estat:** aprovada amb els 8 ajustos de la revisió del 2026-10-05, incorporats. No es toca el pilot.

## 1. Objectiu

El backend deixa de decidir `go_requires_reason` i la fotografia d'una decisió nova a partir del compliance antic. La decisió continua sent 100% humana: F-4b només determina quins fets de l'anàlisi vigent (v3) mereixen quedar registrats si una persona fa GO, i quins d'aquests fets exigeixen que escrigui un motiu.

## 2. Estat actual (auditat)

- `record_decision` (`bid_workspace.py:809-872`): `load_state` → `state.warnings` (`DecisionWarnings`, nou codis del motor antic) → `requires_reason` → `WorkspaceDecision(...)` amb la fotografia legacy (`compliance_run_id`, `requirement_run_id`, 4 comptadors, `automated_recommendation`, `open_warnings`, `deadline_passed`, `tender_revision_id`) → `set_status` → **commit**. L'auditoria i `reconcile_readiness` es fan després, al router, amb commits propis.
- `load_state` no llegeix mai `TenderAnalysisRun`. No hi ha cap enllaç entre decisions i anàlisi v3.
- `GET …/analysis/current` calcula `run = current_run(...)` i `stale = is_stale(session, run)` **en línia al router** (`tender_analysis.py:60-68`). No hi ha cap funció reutilitzable que retorni `(run, stale)`.
- `deadline_passed` (`load_state`:510) = `days is not None and days < 0`, amb el termini del lot o de l'expedient; independent del motor antic.
- `WorkspaceDecision` és append-only (sense `updated_at`). `open_warnings` NULL vol dir «no es va registrar» (test existent).

## 3. Esquema: migració additiva `0054_decision_analysis_v3`

Dues columnes noves a `workspace_decisions`, totes dues nul·lables; cap canvi a les existents; cap backfill.

| Columna | Tipus | Semàntica |
|---|---|---|
| `analysis_run_id` | Uuid, FK `tender_analysis_runs.id` `ON DELETE SET NULL`, nom `fk_workspace_decisions_analysis_run`, índex | El run v3 que la persona tenia com a anàlisi vigent en decidir. NULL si no n'hi havia (o fila antiga). |
| `analysis_snapshot_json` | JSON | Fotografia v1 (§4). NULL = fila antiga = fotografia legacy. |

Regles:
- **Fila antiga:** `analysis_snapshot_json IS NULL` → es llegeix com a `legacy_compliance`, exactament com avui. No es migra ni es reescriu cap decisió.
- **Decisió nova amb anàlisi v3:** `analysis_snapshot_json` v1 amb `state` `current` o `stale`, i `analysis_run_id` = aquell run.
- **Decisió nova sense anàlisi:** `analysis_snapshot_json` v1 amb `state: "absent"` i `analysis_run_id = NULL`.
- Les columnes legacy (`compliance_run_id`, `requirement_run_id`, els 4 comptadors, `automated_recommendation`, `open_warnings`) **no es reinterpreten** mai com a v3.
- **Una decisió nova v3 no escriu fotografia legacy:** `compliance_run_id = NULL`, `requirement_run_id = NULL`, `automated_recommendation = NULL`, `open_warnings = NULL`; els 4 comptadors legacy = `0` només per la restricció `NOT NULL`. Es mantenen `prefit_score`, `deadline_passed` i `tender_revision_id`, que són fets, no fotografia del motor antic.
- La branca és única: `analysis_snapshot_json IS NULL` → legacy; `analysis_snapshot_json IS NOT NULL` → v3. Cap lector no infereix res d'`open_warnings` ni dels comptadors per decidir de quina classe és una fila.
- `downgrade`: elimina la FK i les dues columnes; cap dada legacy afectada.

## 4. Snapshot v1 (contracte tancat i versionat)

```json
{
  "schema_version": 1,
  "state": "current" | "stale" | "absent",
  "rules_version": "phase3a-analysis-v3",        // absent quan state = absent
  "run_created_at": "2026-10-05T08:17:00+00:00",   // absent quan state = absent
  "fit": {"satisfies": 4, "does_not_satisfy": 1, "unknown": 19, "not_applicable": 10},
  "points": {"secured": 0, "at_risk": 27, "lost": 5, "not_assessable": 68, "max": 100},
  "blocker_potential_count": 0,
  "findings_count": 38,
  "warnings": ["analysis_stale"]                   // els codis de §5 que hi havia en decidir
}
```

- Només informació factual **ja calculada** per la v3 (`summary_of(run)` i `len(run.findings)`), copiada en el moment de decidir. Amb `state: "absent"` només hi ha `schema_version`, `state`, `warnings`.
- **No hi ha** score global, `recommendation`, GO/NO-GO ni `blocker_confirmed`. Cap clau fora d'aquestes.
- **Implementació:** schema Pydantic tancat i versionat (`AnalysisDecisionSnapshotV1`, `extra="forbid"`, `schema_version: Literal[1]`, `state: Literal["current","stale","absent"]`), amb invariants validats: amb `absent` els camps del run (`rules_version`, `run_created_at`, `fit`, `points`, `blocker_potential_count`, `findings_count`) són absents; amb `current`/`stale` són tots obligatoris. El que es desa a `analysis_snapshot_json` és `model_dump()`; el que es llegeix es revalida amb el mateix schema.
- **`warnings`** és exactament `AnalysisDecisionWarnings.codes()` del mateix objecte que alimenta `decision_warnings` i `go_requires_reason` en aquell instant (§5): mateixa font, mateix ordre estable.
- Ha de correspondre **exactament** al run que la persona tenia com a vigent: el mateix `(run, stale)` que retornaria `GET /analysis/current` en aquell instant (§6).
- Immutable: recalcular l'anàlisi després no modifica cap decisió ja desada (test).

## 5. Avisos que obliguen motiu (base `analysis_v3`)

Vocabulari **tancat** de codis v3, nou dataclass `AnalysisDecisionWarnings` a `app/workspace/decision.py`, sense tocar el `DecisionWarnings` legacy:

| Codi | Quan | Obliga motiu per a GO |
|---|---|---|
| `analysis_missing` | no hi ha run v3 vigent (`absent`) | sí |
| `analysis_stale` | el run vigent està desactualitzat (`stale`) | sí |
| `blocker_potential` | `summary.blocker_potential_count ≥ 1` | sí |
| `deadline_passed` | `state.deadline_passed` (fet del plec) | sí |

`requires_reason(GO, warnings) = any_open`. NO-GO i ajornar mai exigeixen motiu. L'ordre de `codes()` és fix (el de la taula) i es fixa amb un test amb diversos avisos alhora.

**Independència del compliance antic:** variar el compliance antic (cap run, run indeterminat, run amb bloquejos confirmats, proveïdor no fiable) **no altera** `decision_warnings` ni `go_requires_reason` quan l'estat v3 és el mateix. Test explícit.

**No exigeixen motiu per si sols** (són feina i risc a gestionar, no una certificació que tot està resolt): requisits `unknown`, dades declarades o no verificades, punts `at_risk` o `lost`, criteris `not_assessable`, preguntes pendents, i l'absència o l'estat del compliance antic (`missing_compliance`, `undetermined_eligibility`, `unreliable_analysis`, `outdated_analysis`, comptadors legacy). Cap d'aquests entra al càlcul de `go_requires_reason`.

**Auditoria contra L'Escala** (run v3 validat: vector 4/1/19/10, punts 0/27/5/68, `blocker_potential_count = 0`; el gold té `deadline_at = null`, així que `deadline_passed = False`):
- run vigent i no desactualitzat → **cap avís** → GO sense motiu permès, malgrat 19 `unknown`, 27 punts en risc, 5 perduts, 68 no avaluables i 48 preguntes;
- run desactualitzat (p. ex. després de canviar coneixement sense recalcular) → `analysis_stale`;
- sense run → `analysis_missing`;
- si el plec tingués termini i hagués passat → `deadline_passed`.
Un test del gold fixa exactament aquests quatre resultats.

## 6. Font canònica de l'estat de l'anàlisi

- Nova funció al servei `tender_analysis`: `current_analysis(session, company_id, tender_id, *, on=None) -> AnalysisStatus`, amb `AnalysisStatus(run: TenderAnalysisRun | None, stale: bool)` (`stale = False` si `run` és `None`). Internament: `current_run` + `is_stale`.
- `GET …/analysis/current` passa a usar-la (mateix contracte HTTP: 404 si `run is None`, `stale` al cos).
- `bid_workspace` **no reimplementa** res: `load_state` crida `current_analysis` i exposa `state.analysis` (`AnalysisStatus`). Així `_detail`, `record_decision` i el router de l'anàlisi conclouen el mateix sobre run vigent, `stale` i absència.
- `is_stale` és car (recarrega vista d'estructura i coneixement): `_summary` (llistes) **no** el crida; `load_state` rep `with_analysis: bool` (cert a `_detail` i `record_decision`, fals a `_summary` i on no calgui).

## 7. API del workspace (additiu, sense canviar significats)

`WorkspaceDetail` afegeix:
- `decision_warnings: list[str]` — codis de §5;
- `decision_warning_basis: "analysis_v3"` — literal fix, perquè el client sàpiga quina base ha produït els avisos;
- `go_requires_reason: bool` — **passa a derivar-se dels avisos v3** (canvi de comportament documentat; el nom es manté, sense cap camp paral·lel).
- `open_warnings` es manté amb els codis **legacy** (compatibilitat), sense canviar de significat; la UI activa ja no el fa servir per decidir.

`DecisionRead` afegeix `analysis_run_id: UUID | None` i `analysis_snapshot_json: dict | None`.

`DecisionReasonRequiredError.details` passa a `{"warnings": [codis v3], "basis": "analysis_v3"}`.

## 8. Historial i UI activa (web)

- **Decisió antiga** (`analysis_snapshot_json === null`): «Registre del moment (compliment anterior): …» i «Es va decidir sabent-ho: {legacy}» — exactament com després de F-4.
- **Decisió nova** (snapshot v1): «Registre del moment (anàlisi del plec): {state} · compleix N · no compleix N · no se sap N · no aplica N · punts assegurats/en risc/perduts/no avaluables · N possibles bloqueigs (no confirmats) · N elements a resoldre», i «Es va decidir sabent-ho: {etiqueta del codi v3}». Amb `absent`: «Registre del moment (anàlisi del plec): sense anàlisi vigent».
- Mai es presenten dades legacy com a v3 ni viceversa: la branca és només `analysis_snapshot_json`.
- **Panell i modal** (F-4 §5 reescrit per a la base v3): el text del motiu obligatori, el títol del bloc d'avisos, l'etiqueta i el placeholder del motiu, el zero-state i l'error `decision_reason_required` passen a dir «avisos de l'anàlisi del plec» i llegeixen `decision_warnings`. Etiquetes humanes del vocabulari tancat: `analysis_missing` → «Encara no hi ha anàlisi del plec»; `analysis_stale` → «L'anàlisi del plec està desactualitzada»; `blocker_potential` → «L'anàlisi detecta possibles bloqueigs (no confirmats)»; `deadline_passed` → «El termini de presentació ja ha passat». Codi desconegut → «Avís de l'anàlisi del plec».
- La descripció del modal deixa de parlar del «compliment anterior» com a cosa que es desa per a la decisió nova; diu que es registra la decisió, qui, quan, el motiu i la fotografia de l'anàlisi del plec vigent.

## 9. Atomicitat del `POST /decisions`

En **una sola transacció** (un sol `commit`, cap decisió parcial):
1. carregar l'estat v3 autoritatiu (`current_analysis`, §6);
2. calcular `AnalysisDecisionWarnings`;
3. validar el motiu (`requires_reason`);
4. construir el snapshot immutable (§4);
5. inserir la decisió (amb `analysis_run_id`, `analysis_snapshot_json`, i sense fotografia legacy, §3);
6. actualitzar l'estat del workspace (`set_status`);
7. registrar l'**audit log** de la decisió (`audit.record`, dins de la mateixa transacció: `record_decision` rep `user_id` i l'escriu abans del commit);
8. `commit`.
Si falla qualsevol pas (inclòs `audit.record`), cap fila queda escrita: ni decisió, ni canvi d'estat, ni auditoria parcial (test amb `audit.record` que llança). `reconcile_readiness` queda després, al router, com avui.

## 10. Acceptació

1. Decisió antiga (`analysis_snapshot_json` NULL) es llegeix exactament com abans, columna per columna i a l'historial.
2. Decisió nova v3 no necessita cap compliance run.
3. Compliance antic absent (o indeterminat, o no fiable) **no** obliga motiu.
4. v3 `current` + 0 `blocker_potential` + termini obert → GO sense motiu permès.
5. v3 `current` + `blocker_potential ≥ 1` → GO sense motiu → `decision_reason_required` amb `warnings = ["blocker_potential"]`, `basis = "analysis_v3"`.
6. v3 `stale` → motiu obligatori (`analysis_stale`).
7. v3 absent → motiu obligatori (`analysis_missing`).
8. Termini passat → motiu obligatori (`deadline_passed`).
9. NO-GO i ajornar continuen sense motiu obligatori.
10. 403 i tenancy igual que ara (`assert_can_decide`, 404 d'un altre tenant).
11. El snapshot no canvia si després es recalcula l'anàlisi (run nou, decisió antiga intacta, `analysis_run_id` apunta al run antic).
11b. Variar el compliance antic amb la mateixa v3 no altera `decision_warnings` ni `go_requires_reason`.
11c. Si `audit.record` falla, no queda cap decisió, cap canvi d'estat ni cap auditoria parcial.
11d. `snapshot.warnings == decision_warnings` en el moment de decidir, amb ordre estable, també amb diversos avisos alhora.
12. `GET /analysis/current` i `record_decision` conclouen el mateix `(run, stale)`.
13. **L'Escala** (gold + bessó, run v3 per `analyze_tender`): C1 perd 5 punts; 0 `blocker_potential`; amb run `current` i sense termini, GO sense motiu permès; la decisió desada referencia exactament aquell `analysis_run_id`; el snapshot conserva 4/1/19/10 i 0/27/5/68; recalcular després (canvi de coneixement) crea un run nou i no modifica la decisió.
14. Cap score, recomanació, GO/NO-GO ni `blocker_confirmed` al snapshot ni a l'API nova (test de claus tancades).
15. Tests existents de decisions: els que asserten la fotografia legacy continuen verds; els que depenien dels avisos legacy per exigir motiu s'adapten explícitament al vocabulari v3 (llista al pla).

## 11. Fora d'abast

- No retirar taules ni model del compliance antic; no esborrar camps legacy. Les columnes legacy **es conserven i es llegeixen** (files antigues), però **no s'alimenten** a les decisions v3 noves (§3).
- No tocar el motor v3 (`app/analysis/*`, `analyze_tender`).
- No integrar amb la branca del pilot; no desplegar. Release gate: v3 + F-4 + F-4b es despleguen junts, després de la branca d'integració des de `d92746f`.

## 12. Decisions preses (revisió del 2026-10-05)

1. Decisions v3 noves sense fotografia legacy (§3).
2. `warnings` del snapshot = `AnalysisDecisionWarnings.codes()` del mateix objecte (§4, §5).
3. `go_requires_reason` es manté, amb base v3, sense camp paral·lel (§7).
4. Audit log dins de la transacció de la decisió; `reconcile_readiness` després (§9).
5. Snapshot com a schema Pydantic tancat i versionat (§4).
