# Feina asíncrona: processar plecs i analitzar requisits

Processar un plec i analitzar-ne els requisits pot durar minuts. El proxy de Next
(`/bff`) talla qualsevol petició als 30 s, i el backend continuava treballant, i
pagant, sense que ningú no ho veiés. Des de la fase 7A:

* **una petició HTTP només crea o consulta**; no executa mai la feina llarga;
* la feina la fa un **worker**, un procés independent que llegeix la cua de
  PostgreSQL (`python -m app.jobs worker`);
* **sense Redis, Celery ni `BackgroundTasks`**.

Els endpoints síncrons antics (`POST /tenders/{id}/analysis/requirements` i
`POST /tenders/{id}/documents/{doc}/process`) responen **410**.

## Dues taules: execució global i petició de tenant

| | `work_jobs` | `job_requests` |
| --- | --- | --- |
| Què és | L'execució tècnica | La petició d'una empresa |
| De qui | Global: cap `requested_by` | `company_id`, `user_id` |
| Per a què | Cua, lease, progrés, resultat | Auditoria, permisos i, més endavant, cost |

Una execució pot satisfer peticions de diverses empreses. Els plecs i els
requisits no són de ningú: pagar dues vegades la mateixa anàlisi seria absurd.

L'enllaç **canònic** entre una execució d'anàlisi i el seu resultat és
`requirement_extraction_runs.work_job_id` (únic quan hi és). S'escriu a la mateixa
transacció que crea el run. No hi ha cap `work_jobs.result_run_id` que el dupliqui.
`job_requests.result_run_id` sí que existeix: també representa un resultat
reaprofitat sense cap execució.

## Identitat d'una anàlisi

La `dedupe_key` d'una anàlisi inclou licitació, empremta dels plecs
(`input_fingerprint`), perfil, proveïdor, model, documents triats i si és `force`.
Només comparteixen execució peticions amb la mateixa identitat.

* Una petició **normal** pot aprofitar una execució `force` compatible en curs.
* Una petició **`force`** no aprofita mai una execució normal.
* Si en crear la petició ja hi ha una anàlisi vigent reutilitzable, no s'encua res:
  la petició neix `succeeded` amb `reused_existing`.

**Input immutable.** Just abans de reservar el run i de fer cap crida al proveïdor,
el servei recalcula `analysis_input()`. Si l'empremta no és la del job, s'atura amb
**`input_changed`**, no crida el proveïdor i propaga l'estat a les peticions. No
s'analitza en silenci una versió diferent de la confirmada.

Si el worker no té configurat el proveïdor i el model del job, el job acaba en
`provider_changed` sense cap crida.

## Estats

```
queued ──claim──────────────────────► running
queued ──última petició cancel·lada─► cancelled
running ──tancament amb fencing─────► succeeded | failed
running ──lease vençut (documents)──► queued (run_after), o failed(lease_expired)
running ──lease vençut (anàlisi)────► orphaned
orphaned ──reconciliació────────────► succeeded | failed
```

Terminals (`succeeded`, `failed`, `cancelled`): no canvien mai. Una petició nova
crea una execució nova.

## Concurrència

* **Reclamar** és atòmic: `SELECT … FOR UPDATE SKIP LOCKED` i un `UPDATE`
  condicional a la mateixa transacció.
* **`uq_work_jobs_busy_tender`**: com a molt un job `running` o `orphaned` per
  licitació. Ni dues anàlisis alhora, ni analitzar mentre es tornen a baixar plecs.
* **`uq_work_jobs_active_dedupe`**: una sola execució activa per identitat.
* **Fencing**: tota escriptura del worker (heartbeat, progrés, tancament) porta el
  seu `lease_token`. Si el job ha canviat de mans, no toca res.
* **Dues empreses alhora**: la segona inserció xoca amb la primera i espera; després
  llegeix l'execució activa, la bloqueja en mode compartit i hi vincula la petició.

## Lease i reconciliació

El worker renova el lease cada `JOB_HEARTBEAT_SECONDS` i **abans de cada chunk**.

* Renovació rebutjada per la base de dades → **pèrdua definitiva**.
* Error de connexió → **transitori**: es continua només mentre el lease que sabem
  renovat no hagi vençut; després no es pot verificar i també s'atura.

En tots dos casos d'aturada, una crida al proveïdor que ja és en vol pot acabar,
però **no s'inicia cap chunk nou** i **el run no es tanca**. No hi ha reexecució
automàtica.

El reaper deixa l'anàlisi `orphaned`, i el reconciliador la tanca segons l'estat
real del run (`work_job_id`):

| Run | Dins `JOB_ORPHAN_GRACE_SECONDS` | Passada la gràcia |
| --- | --- | --- |
| `completed` / `partial` | `succeeded` | `succeeded` |
| `failed` | `failed` (codi del run) | `failed` |
| `running` | espera | run `worker_lost` (UPDATE condicional) + job `failed` |
| cap | espera | `reused` si ara n'hi ha un de vigent; si no, `worker_lost_before_start` |

`_finalise_run` bloqueja la fila del run i refusa tancar-lo si ja no és `running`
(`AnalysisRunReclaimedError`): un worker zombi no pot convertir en vigent un run
que el reconciliador ha donat per perdut. `_reclaim_stale` no reclama per antiguitat
un run que té el job `queued`, `running` o `orphaned`: una anàlisi llarga amb
heartbeat és legítima.

Els jobs de documents són idempotents i sense cost de proveïdor: es tornen a encuar
fins a `JOB_DOCUMENT_MAX_ATTEMPTS`.

## Tenant-safe

La vista que rep una empresa no distingeix si la feina l'ha creat ella o s'ha
vinculat a la d'una altra:

* estat `accepted` mentre no acaba, sigui a la cua o executant-se;
* cap `work_job_id`, ni `linked_existing`, ni recompte de peticions;
* el progrés es compta des del moment de la petició (`progress_baseline`) i només
  es mostra quan ha avançat.

No hi ha cap endpoint global d'activitat. Una empresa no pot llegir ni cancel·lar
la petició d'una altra (404). `operation_busy` (409) només es dóna per causes que no
depenen de cap tenant (un run d'operador en curs fora de la cua).

Canal residual conegut: el resultat pot arribar abans del que caldria esperar si
algú altre ja l'havia demanat. Els resultats d'anàlisi ja són públics a la fitxa de
la licitació.

## Endpoints

Tots sota `/api/v1/companies/{company_id}`:

| Mètode | Ruta | Resposta |
| --- | --- | --- |
| POST | `/tenders/{tid}/analysis-quotes` | 201 preview; 422 `nothing_analyzable` |
| POST | `/analysis-quotes/{qid}/confirm` | 202 `accepted`; 200 `already_requested`; 200 `succeeded` + `reused_existing`; 409 `preview_changed` / `quote_expired` / `quote_not_open` / `operation_busy`; 422 `analysis_too_large`; 429 `company_active_limit`; 403 |
| POST | `/tenders/{tid}/job-requests/analyze-requirements` | 409 `analysis_confirmation_required` (no executa res) |
| POST | `/tenders/{tid}/job-requests/process-documents` | 202 `accepted`; 200 `already_requested`; 404 si un document no és de la licitació; 403 segons `DOCUMENT_PROCESS_ROLES` |
| GET | `/job-requests/{rid}` | estat de la petició |
| GET | `/analysis-limits` | límits del pilot i si l'usuari pot confirmar o processar |
| GET | `/tenders/{tid}/job-requests?job_type=&active=` | peticions de l'empresa (la UI les fa servir per reprendre el seguiment) |
| POST | `/job-requests/{rid}/cancel` | cancel·la la petició; l'execució només si és `queued` i ja no la necessita ningú |

La interfície fa polling cada 2,5 s (`apps/web/src/lib/jobs.ts`).

## Operació

```bash
python -m app.jobs worker            # bucle; Ctrl+C acaba el job en curs
python -m app.jobs worker --once     # un sol job
python -m app.jobs maintenance       # reaper + reconciliació
python -m app.jobs list --status orphaned
```

Cap job no pren decisions humanes: el registre de handlers només coneix
`process_tender_documents` i `analyze_tender_requirements`. La política de reintents
del proveïdor al worker és la de producció (`LLM_MAX_RETRIES`); el benchmark de
pagament continua amb `max_retries=0`.

## Control de cost de l'anàlisi (7B)

Cap anàlisi es demana sense que l'usuari n'hagi vist el volum:

1. **Preview** — `POST /companies/{cid}/tenders/{tid}/analysis-quotes`. Calcula,
   sense cap proveïdor, els documents, les pàgines (`pages_in_plan` de
   `pages_total`, amb `pages_truncated` si el sostre de pàgines talla), els chunks
   i els que s'enviarien (`chunks_to_send`, 0 si ja hi ha una anàlisi vigent
   reaprofitable), el fingerprint, el perfil i el model, i una **estimació de
   tokens** si hi ha històric del mateix proveïdor, model i prompt. Cap import en
   diners. Es desa com a `AnalysisQuote` de l'empresa, amb `planning_version`.
   El calcula `app/services/analysis_planning.py`, que també fa servir el benchmark.
2. **Confirmació** — `POST /companies/{cid}/analysis-quotes/{qid}/confirm`. Torna a
   calcular el preview i el compara amb el desat (versió del càlcul, fingerprint,
   chunking, pàgines, chunks, perfil, proveïdor i model). Si ha canviat o ha
   caducat, respon 409 (`preview_changed` / `quote_expired`) amb el preview nou a
   `error.details.quote`, i **no crea res**. Si no, crea la `JobRequest` de 7A amb el
   fingerprint confirmat (`job_requests.analysis_quote_id`) i marca el preview
   `confirmed` a la mateixa transacció.

El `POST …/job-requests/analyze-requirements` directe respon **409
`analysis_confirmation_required`** amb les rutes del preview i de la confirmació, i
no executa res.

Límits del pilot, tots de configuració:

| Setting | Defecte | Efecte |
| --- | --- | --- |
| `ANALYSIS_MAX_CHUNKS_PER_RUN` | 40 | preview `allowed: false`; confirmació 422 `analysis_too_large`; el worker també l'aplica abans de reservar el run |
| `ANALYSIS_MAX_ACTIVE_PER_COMPANY` | 2 | 429 `company_active_limit`; compta només peticions pendents de la mateixa empresa |
| `ANALYSIS_QUOTE_TTL_SECONDS` | 900 | un preview caducat es torna a ensenyar |
| `ANALYSIS_CONFIRM_ROLES` | owner, admin, bid_manager | qui pot confirmar (403 si no); `technical` fora per defecte |
| `DOCUMENT_PROCESS_ROLES` | owner, admin, bid_manager | qui pot demanar el processat |

Qualsevol membre pot veure el preview. `GET /companies/{cid}/analysis-limits` diu a
la interfície els límits i si l'usuari pot confirmar o processar. El límit
d'actives es comprova amb la fila de l'empresa bloquejada, de manera que dues
confirmacions simultànies no el poden superar.

Tenant-safe: el preview no diu mai si una altra empresa està analitzant el mateix
plec.

## Licitacions públiques: run canònic, commit per chunk i pressupost (3B-2)

Operació: `docs/ops/public-intake-runbook.md`. Aquí, el que canvia a la cua.

**Dedupe canònic.** En una licitació pública (`owner_company_id` nul) la `dedupe_key`
d'una anàlisi és `analyze:{tender}:canonical:{analysis_identity}`, sense cap paràmetre
de tenant (`canonical_analysis_dedupe_key` a `app/jobs/params.py`). Dues empreses que
demanen la mateixa anàlisi comparteixen una sola execució i un sol run; cada empresa
conserva el seu punter (`company_tender_requirement_runs`). Una empresa no pot passar
`document_ids`, `profile` ni `force` (422 `public_tender_canonical_only`). Prova:
`tests/test_public_tender_canonical_quota.py`
(`test_a_b_mateix_tender_public_una_sola_feina_canonica_sense_parametres_de_tenant`) i
`tests/test_requirement_run_basis.py`.

**`force` només de l'operador, mai canònic.** `POST /api/v1/platform/tenders/{id}/analysis/force`
(`PlatformAdmin`, 202) encua una anàlisi forçada, opcionalment amb una selecció de
documents i un perfil. El run es desa però **no passa mai a vigent**
(`forced_not_canonical`) i passa per la mateixa porta de despesa. Prova:
`test_l_operador_encua_un_run_forcat_que_mai_es_canonic` i
`test_la_ruta_de_force_es_nomes_de_l_operador`.

**Commit per chunk.** Cada chunk respost (també un de caigut que el proveïdor va
arribar a respondre) es desa en un commit propi mentre el run és `running` i no vigent:
requisits i evidències, `chunk_coverage` (els pendents com a `pending`), tokens, cost
i, per a un run `real`, la línia del llibre `llm_spend_ledger`. Un worker que mor a mig
run no perd la feina pagada. Prova: `tests/test_chunk_commits.py`.

**Represa des de `worker_lost`.** `plan_resume` accepta un run `partial` i també un
`failed` amb `error_code` `worker_lost` o `internal_error` que tingui algun chunk
completat; envia només els chunks que falten, i només si és exactament la mateixa
anàlisi (mateix fingerprint, perfil, proveïdor i model; si no, `ResumeNotAllowedError`
amb el motiu). Un parcial ja no és mai vigent. Prova:
`test_la_represa_des_de_worker_lost_envia_nomes_el_chunk_3` i
`test_un_run_fallit_per_worker_lost_amb_chunks_completats_es_pot_reprendre`.

**`budget_exceeded`.** Abans de cada chunk d'un run `real`, el gastat total ha de ser
≤ `LLM_BUDGET_PILOT_TOTAL_USD` i el de la cadena de represa ≤ el cap de l'scope
(`canary` per a `only_chunks` de com a molt 2 chunks, `run` per a la resta). Si se
supera, no s'envia res més: el run es tanca `partial` (o `failed` si cap chunk s'havia
completat) amb `error_code=budget_exceeded` i l'scope a
`provider_metadata.budget_exceeded_scope`. Mentre no hi hagi una autorització
posterior d'aquell scope o de `pilot_total`, tota quota que hagi de gastar queda
`allowed=false` (`budget_exhausted`). Altres codis de la porta, abans de reservar el
run: `provider_not_real`, `model_not_pinned`, `price_unknown`. Prova:
`tests/test_llm_budget.py`.

**Prerequisits de quota.** La quota d'una licitació pública calcula i desa a
`analysis_quotes.prerequisites` (part de la identitat): proveïdor real, classificació
global sense troballes HIGH obertes (o `accept_incomplete_package`), qualitat del text
(`acknowledge_quality` si és `warning`) i selecció canònica. Prova:
`tests/test_quote_prerequisites.py`.

## Límits coneguts

* **Tokens d'un run perdut (resolt a 3B-2).** Cada chunk respost es desa en un commit
  propi, amb els seus tokens, el cost i la línia del llibre de despesa: un worker que
  mor a mig fer no perd la feina pagada (vegeu «Commit per chunk»).
* **Garanties de concurrència reals només a PostgreSQL.** SQLite (tests) no té
  `SKIP LOCKED`.
* **Sense supervisor.** Cal mantenir el procés worker viu (i afegir-lo a
  `docker-compose`); la UI no avisa si no n'hi ha cap.
* **Retenció.** `work_jobs` i `job_requests` creixen sense purga.
* **Despesa en diners.** Des de 3B-2 hi ha llibre de despesa global
  (`llm_spend_ledger`) i caps per scope, però l'empresa no veu mai cap import global:
  la quota només en rep booleans. Els preus no són al repositori (`LLM_MODEL_PRICES`
  s'omple a la sessió).
