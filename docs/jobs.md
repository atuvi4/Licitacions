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
| POST | `/tenders/{tid}/job-requests/analyze-requirements` | 202 `accepted`; 200 `already_requested`; 200 `succeeded` + `reused_existing`; 409 `operation_busy`; 422 `nothing_analyzable` |
| POST | `/tenders/{tid}/job-requests/process-documents` | 202 `accepted`; 200 `already_requested`; 404 si un document no és de la licitació |
| GET | `/job-requests/{rid}` | estat de la petició |
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

## Límits coneguts

* **Tokens d'un run perdut.** L'ús per chunk només es desa en tancar el run: si un
  worker mor a mig fer, els tokens dels chunks ja cobrats no queden registrats.
* **Garanties de concurrència reals només a PostgreSQL.** SQLite (tests) no té
  `SKIP LOCKED`.
* **Sense supervisor.** Cal mantenir el procés worker viu (i afegir-lo a
  `docker-compose`); la UI no avisa si no n'hi ha cap.
* **Retenció.** `work_jobs` i `job_requests` creixen sense purga.
* **Encara no hi ha** estimació prèvia, límits per tenant, ledger de tokens ni
  confirmació explícita de despesa. La separació execució/petició ho deixa preparat.
