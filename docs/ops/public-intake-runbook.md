# Runbook: recepció pública de licitacions (Phase 3B-2)

Guia operativa per a l'operador de plataforma de Tuvi. Cobreix el paquet documental
públic, la classificació i l'estructura globals, el backfill de descripcions, la
migració 0058–0061, el pressupost d'LLM, la prova disposable i el punt d'activació
al pilot. Font de veritat del disseny:
`docs/superpowers/specs/2026-10-07-public-intake-3b2-design.md` i l'addendum
`docs/superpowers/specs/2026-10-09-public-intake-3b2-pilot-review-addendum.md`.

Regles que valen per a tot el document:

* **Cap crida a cap model** mentre no s'hagi complert la secció 9. Tot corre amb
  `LLM_PROVIDER=fake`.
* **Cap credencial als documents, a les ordres ni a un `.env`.** Les DSN i la clau del
  proveïdor viuen només a la sessió del procés (variables d'entorn de la sessió).
  Les ordres d'aquest document no porten mai contrasenya.
* **Cap dada real a git** (còpies del pilot, fitxers de plecs, informes amb text de
  plec).
* Les ordres de Python s'executen amb el directori de treball a `apps/api` i amb
  `python -m app...` (mai per ruta de fitxer, perquè el paquet `app` es resol des
  del directori actual).

## 1. Rols i frontera (operador vs empresa)

Hi ha dues classes de dades:

* **Globals**: pertanyen a la licitació pública, no a cap empresa. Es calculen o
  s'escriuen una sola vegada i tots els tenants les veuen igual.
* **De tenant**: pertanyen a una empresa (`company_id` obligatori).

| Global (la porta l'operador o el sistema) | De tenant (la porta l'empresa) |
| --- | --- |
| `tender_documents` d'una licitació pública, amb origen i verificació; bytes per `sha256` | decisions de rol per empresa (`tender_document_role_decisions`, ancorades a un `sha256`) |
| text extret (per `sha256` i versió de parser) | recepcions i pujades privades (només licitacions manuals pròpies) |
| classificació automàtica global i `document_role_reviews` (revisions de l'operador) | `company_tender_requirement_runs`: el punter de l'empresa al run de requisits que ha acceptat |
| run de requisits (sense requester), `requirements`, `award_criteria`, evidències | reconciliacions, requisits manuals (licitació pròpia) |
| estructura i abast del plec amb provenance, `tender_structure_proposals`, `tender_package_gaps` | quotes, `job_requests`, anàlisis, workspaces, decisions, coneixement |
| `llm_spend_ledger` i `llm_budget_authorizations` | — |

Qui pot fer què:

* **Operador de plataforma** (`PlatformAdmin`: superusuari sense empresa): tots els
  endpoints `/api/v1/platform/...` d'aquest document. Una empresa hi rep 403. Tota
  acció de l'operador deixa auditoria amb `company_id` nul.
* **Empresa** (owner, admin, bid_manager): no afegeix, verifica, retira ni restaura
  documents d'una licitació pública; no tria documents, perfil ni `force` en una
  licitació pública (422 `public_tender_canonical_only`); pot demanar el processament
  dels documents analitzables i confirmar la quota canònica. Les seves escriptures
  d'estructura i de classificació sobre una licitació pública es refusen (`tender_not_manual`).
* Els endpoints `/platform/...` només accepten licitacions **públiques** (font, sense
  propietari): una licitació manual retorna 422 `tender_not_public`; una que no
  existeix, 404.

## 2. Paquet documental públic

El feed 1044 no enumera sempre tot el paquet (a L'Escala porta el PCAP i el PPT; els
annexos són al perfil del contractant). L'operador pot incorporar-ne els que falten.
Tots els endpoints són sota `/api/v1/platform/tenders/{tender_id}/documents`.

| Mètode | Ruta | Què fa |
| --- | --- | --- |
| GET | `` | tots els documents, retirats inclosos, amb provenance i la versió vigent |
| POST | `/by-url` | afegeix un document oficial per URL (`url`, opcionals `source_page_url`, `name` i `document_type`) |
| POST | `/upload` | puja un fitxer quan la URL oficial no es pot descarregar (multipart: `file`, `contribution_source_url`, opcionals `name`, `document_type`) |
| POST | `/{document_id}/verify` | verifica que és el document publicat oficialment (nota opcional) |
| POST | `/{document_id}/reject` | rebutja l'aportació (motiu) |
| POST | `/{document_id}/retire` | retira un document de l'operador (motiu; mai s'esborra) |
| POST | `/{document_id}/restore` | restaura un document de l'operador retirat |
| POST | `/{document_id}/hand-over-to-feed` | cedeix al feed un document per URL que la font ja publica (motiu) |

Procediment:

1. **Afegir.** Per URL (`/by-url`) si és descarregable; sinó, pujada (`/upload`).
   * El host de la URL, i de cada redirecció, ha de ser a l'allowlist
     `DOCUMENT_OFFICIAL_HOSTS` (coincidència per sufix de domini; valors per defecte
     `contractaciopublica.cat`, `contrataciondelestado.es`,
     `contrataciondelsectorpublico.gob.es`; com a variable d'entorn, una llista JSON).
     Un altre host dona 422 `host_not_official`. La guarda SSRF s'aplica a més.
   * La pàgina d'origen (`source_page_url` a `/by-url`, `contribution_source_url` a
     `/upload`) és **obligatòria només a `/upload`**; a `/by-url` és opcional però
     recomanada. Quan s'informa, ha de ser http(s), sense credencials
     (`invalid_page_url`) i d'un host oficial.
   * Una pujada rep una URL sintètica `operator-upload://<uuid>`. Mida màxima
     `DOCUMENT_MAX_SIZE_MB` (413 si es supera).
   * Una URL ja existent a la licitació dona 409 `document_url_exists`.
   * El document neix **pendent de verificació**: es veu al llistat però no és
     analitzable i no mou cap empremta ni cap anàlisi.
2. **Verificar amb SHA.** Abans de verificar, compara el `content_sha256` que torna
   l'endpoint amb el SHA de l'oracle (o amb el del document publicat). `/verify` el fa
   analitzable i registra `DOCUMENT_ADDED` a la revisió. Si el SHA no coincideix amb
   l'esperat, **no verifiquis**: rebutja amb motiu (`/reject`) o registra el buit
   com a `declared_by_operator` o com a diferència explicada; mai es força. Un document
   ja verificat dona 409 `document_already_verified`; un rebutjat es pot verificar
   si l'operador rectifica; un retirat, no (cal restaurar-lo).
   Si els bytes són idèntics als d'un document del feed, queda com a àlies (el canònic
   és el del feed) i no entra dues vegades a l'anàlisi.
3. **Retirar i restaurar.** Només documents d'origen operador (`operator_url`,
   `operator_upload`); els del feed els governa el sync (409
   `document_not_operator_managed`). Mai s'esborra: es marca. Restaurar un document que
   no és retirat dona 409 `document_not_retired`.
4. **Cedir al feed (`hand-over-to-feed`).** Només si el document és un `operator_url`
   **verificat** i la darrera entrada de la font del tender publica exactament aquesta
   URL. Codis d'error: `document_not_operator_url` (una pujada no té URL que la font
   pugui publicar), `document_not_verified`, `feed_does_not_publish_url`. Després el
   document passa a origen `feed`, governat per la font; el contingut, l'historial i la
   provenance de l'aportació es conserven, i la prova (l'entrada de la font usada)
   queda a l'auditoria.
5. **Què passa en un sync 1044 posterior.** El sync només retira i actualitza els
   documents que governa (origen `feed` amb la mateixa font). **Un document d'operador
   sobreviu al sync**. Si el feed comença a publicar una URL que l'operador ja havia
   incorporat, el sync no se l'apropia: la deixa intacta i registra l'avís
   `placsp.document.not_governed`; la resolució és de l'operador amb `hand-over-to-feed`.
   Cada acció de l'operador deixa una revisió `document_intake` (historial «Processament
   intern») i, si canvia el conjunt analitzable, dispara una classificació global.

Estat del paquet: `tender_package_gaps` registra el que falta (codis
`required_role_missing`, `referenced_document_missing`, `no_text`,
`format_not_analyzable`, `annex_missing`, `declared_by_operator`). La fitxa mostra
`package_status` (`complete`, `incomplete`, `unknown`).

## 3. Classificació global i revisió de rol

Endpoints sota `/api/v1/platform/tenders/{tender_id}`:

| Mètode | Ruta | Què fa |
| --- | --- | --- |
| GET | `/classification` | passada vigent, suggeriments, troballes, revisions, rols efectius i buits |
| POST | `/classification/run` | torna a classificar els documents analitzables (si el conjunt ha canviat) |
| POST | `/documents/{document_id}/role-review` | revisa el rol global d'un document |

* La classificació global es dispara sola quan canvia el conjunt de documents
  analitzables (verificar, retirar, restaurar, cedir, sync). `/classification/run` la
  força amb l'activador `operator_request`; és idempotent per empremta.
* **Revisar un rol** (`role-review`): cos amb `role`, `reason` (obligatori, 1–500
  caràcters) i `content_sha256` (64 caràcters: la versió del fitxer que has mirat).
  Si el fitxer vigent ja no és aquell `sha256`: 409 `document_version_changed`. Només
  d'afegir: les revisions són immutables (un trigger de PostgreSQL rebutja UPDATE i
  DELETE); una revisió nova substitueix l'anterior a la lectura, mai l'esborra.
* **Si el feed canvia el tipus d'un document revisat**, el feed mai sobreescriu el rol
  revisat. La revisió de la licitació registra `SOURCE_TYPE_CHANGED_AFTER_REVIEW`
  (informatiu) i el rol efectiu continua sent el revisat. Revisa el cas i, si cal,
  afegeix una revisió nova.
* El `document_type` que ve del feed no es modifica mai; la classificació global i la
  revisió són una capa a part.

## 4. Estructura i abast globals amb cites

Endpoints sota `/api/v1/platform/tenders/{tender_id}/structure`. Tot passa per la via
interna amb `origin=operator` i `authority=platform_operator`.

* `GET` : l'estructura amb provenance (blocs, seus, elements d'abast, necessitats,
  fets contractuals, territoris, criteris).
* Alta, correcció i baixa de: `/blocks`, `/sites`, `/scope-items`,
  `/requirements/{requirement_id}/capability-needs`, `/contract-facts`; territoris d'un
  requisit a `/requirements/{requirement_id}/territories`.
* Cites: `POST /citations` (enllaça) i `DELETE /citations/{entity_kind}/{entity_id}/{source_ref_id}`
  (treu l'enllaç; la cita no s'esborra). La darrera cita d'una entitat no es pot treure
  (422 `last_citation`).
* Propostes immutables: `POST /proposals`, `GET /proposals?status=`,
  `POST /proposals/{proposal_id}/promote` i `POST /proposals/{proposal_id}/reject` (amb
  motiu). Una proposta sense cita es refusa (`structure_proposal_citation_required`);
  una ja decidida, 409 `structure_proposal_not_pending`.
* **`unknown` honest.** Tota alta o correcció porta **almenys una cita** de la mateixa
  licitació. Un camp sense cita es queda «no se sap»: no s'escriu cap valor per
  quadrar amb l'oracle. Per tornar un camp a «no se sap» s'usa l'operació explícita
  `POST /reset-unknown` (`entity_kind`, `entity_id`, `fields`), no un `null`
  (que es refusa).
* Aquestes escriptures no creen cap revisió de la licitació (no són el que publica
  l'òrgan) i deixen auditoria amb `company_id` nul.
* Les anotacions de l'operador sobreviuen a una re-extracció: passen al run nou només
  quan aquest esdevé vigent.

## 5. Backfill de descripcions

La ingesta ja no desa a `Tender.description` el resum fix del feed agregat («Id
licitación…; Órgano de Contratación…; Importe…; Estado…»): `description` queda nul i el
text cru va a `source_metadata.feed_summary`. El CLI fa el mateix amb les licitacions
que ja hi eren:

```bash
cd apps/api
python -m app.tenders.description_backfill [--batch-size 500]
```

* **Executa'l ABANS del primer sync amb el normalitzador nou**: si no, el sync
  presentaria el canvi de descripció com un canvi publicat per la font.
* **Idempotent**: només toca les files on `description` encara és la plantilla; una
  segona passada no canvia res ni deixa cap revisió. Per lots en ordre d'id, amb
  `commit` a cada lot: un tall a mig camí deixa fet el que s'ha fet i tornar-lo a
  llançar continua.
* No toca licitacions obertes a mà (font manual).
* Cada licitació corregida deixa una revisió amb activador `normalization` (origen
  `internal` a l'historial).
* **No té mode `--dry-run`.** La lectura prèvia és la còpia: restaura una còpia
  disposable, executa-hi el CLI i compara `scanned`/`updated` (la sortida és
  `description: N licitacions revisades, M corregides`); després, executa'l al pilot
  amb la mateixa expectativa. Fes-ho després de la còpia de la secció 6.
* Usa la base de la sessió (`DATABASE_URL` de la sessió, mai a la línia d'ordres).

## 6. Democió de runs simulats (migració 0061) i resta de la cadena 0058–0061

Les quatre migracions són additives i reversibles, i s'apliquen amb `alembic upgrade
head` sobre una base que és a `0057`. **Còpia prèvia obligatòria**: fes una còpia
completa (`pg_dump`) de la base abans de migrar, i guarda-la fora del repositori.

### 6.1 Consulta de verificació prèvia (només lectura)

Abans de `0061`, executa aquestes consultes a la base (són de **només lectura**) i
anota el resultat:

```sql
SELECT provider, status, is_current, count(*)
  FROM requirement_extraction_runs GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
-- vigents que es degradaran (provider fake/fixture/buit o status no completed):
SELECT id, tender_id, provider, status FROM requirement_extraction_runs
 WHERE is_current AND (status <> 'completed'
       OR lower(trim(coalesce(provider, ''))) IN ('', 'fake', 'fixture'));
-- cap estat fora de la llista (el CHECK nou fallaria):
SELECT DISTINCT status FROM requirement_extraction_runs
 WHERE status NOT IN ('pending', 'running', 'completed', 'partial', 'failed');
```

**Resultat esperat al pilot (verificat el 2026-10-08): 0 runs de requisits (0 files a
les tres consultes).** Si surt alguna fila, atura't: és una anàlisi simulada o parcial
que deixarà de ser vigent, i s'ha de saber abans de migrar.

Què fa la migració: afegeix `run_basis` (`real`, `simulation`, `unconfigured`, `gold`)
amb backfill pel nom del proveïdor, i **demota** (`is_current = false`) tot run vigent
que no sigui un COMPLETED `real` o `gold`: runs `fake`/`fixture`, de proveïdor buit
(`unconfigured`) i els PARTIAL llegats. No esborra ni coacciona res. Crea també les
taules `company_tender_requirement_runs`, `llm_spend_ledger` i
`llm_budget_authorizations`.

Consulta addicional per a `0059` (només lectura): la migració **esborra** les decisions
de rol d'empresa que no tenen cap fitxer vigent amb contingut on ancorar-se (i en diu
el nombre amb un `RAISE NOTICE` a PostgreSQL, `0059 - N decisions de rol sense fitxer
vigent esborrades`). El nombre esperat és zero:

```sql
SELECT count(*) FROM tender_document_role_decisions d
 WHERE NOT EXISTS (
   SELECT 1 FROM tender_document_files f
   JOIN tender_document_contents c ON c.id = f.content_id
   WHERE f.tender_document_id = d.tender_document_id AND f.is_current);
```

### 6.2 Refusos de downgrade

Tornar enrere **refusa de manera explícita** (abans de cap `DROP`) en aquests casos;
el missatge és el de la migració:

| Migració | Es refusa si hi ha… | Missatge |
| --- | --- | --- |
| 0061 | algun run `completed_empty` | `0061 downgrade refusat - hi ha runs de requisits completed_empty i tornar enrere els hauria de coaccionar o esborrar` |
| 0061 | alguna línia al llibre `llm_spend_ledger` | `0061 downgrade refusat - hi ha despesa d'LLM al llibre llm_spend_ledger i tornar enrere l'hauria d'esborrar` (la migració en treu els apòstrofs) |
| 0060 | propostes d'estructura `promoted` o files amb `origin` `operator`/`promoted` | `0060 downgrade refusat - hi ha propostes d estructura promogudes o files escrites per l operador i tornar enrere en perdria la provenance` |
| 0059 | revisions de rol a `document_role_reviews` | `0059 downgrade refusat - hi ha revisions de rol de l operador (document_role_reviews) i tornar enrere les perdria` |
| 0058 | documents `verified` o àlies | `0058 downgrade refusat - hi ha documents verificats o alies (tender_documents.contribution_status verified o duplicate_of_document_id) i tornar enrere en perdria la verificacio o l enllac a l original` |

Un downgrade mai és una via normal una vegada hi ha dades d'operador: la recuperació és
la còpia prèvia. Els downgrades de 0059 no restauren les decisions esborrades per
l'upgrade.

## 7. Pressupost d'LLM

Tot el cost passa per una única porta (`app/services/llm_budget.py`), que fan servir
el worker, la sonda `--paid-run` i el benchmark.

**Configuració** (variables d'entorn; valors del pilot):

| Variable | Valor | Efecte |
| --- | --- | --- |
| `LLM_PROVIDER` | `fake` fins a la secció 9 | amb `fake`/`fixture` la quota queda `allowed=false` (`provider_not_real`) |
| `ALLOW_SIMULATED_ANALYSIS` | `false` (defecte; el pilot **no** la posa) | permet una anàlisi simulada fora de `ENVIRONMENT=test` |
| `REQUIREMENTS_PRODUCT_PROFILE` | `"v1"` (defecte) | perfil de producte; el primer run real és V1 |
| `LLM_ALLOWED_MODELS` | `[]` (defecte) | models reals admesos; `LLM_MODEL` ha de ser **idèntic** a una entrada, sinó `model_not_pinned` |
| `LLM_MODEL_PRICES` | obligatòria per a un proveïdor real | `{"<model>": {"input": <USD per 1M>, "output": <USD per 1M>}}`; sense preu, `price_unknown`. No hi ha cap taula de preus al repositori: s'omple a la sessió amb el full de tarifes oficial del dia |
| `LLM_BUDGET_CANARY_USD` | `1` | cap d'un canari (com a molt 2 chunks) |
| `LLM_BUDGET_RUN_USD` | `3` | cap d'un run (sobre tota la cadena de represa) |
| `LLM_BUDGET_PILOT_TOTAL_USD` | `10` | cap de tot el que s'ha gastat (el llibre sencer) |
| `LLM_TIMEOUT_SECONDS` | `300` | |
| `LLM_MAX_RETRIES` | `0` | cap reintent automàtic; el reenviament només passa per una quota de represa |

**Llibre i autoritzacions.** Cada chunk respost d'un run `real` escriu una línia a
`llm_spend_ledger` (run, chunk, proveïdor, model, tokens, `cost_usd`, scope). La
despesa sobreviu al run. Les autoritzacions són a `llm_budget_authorizations`; el cap
vigent d'un scope és el de la darrera autorització, i sense cap, el de configuració.

Endpoints de l'operador:

* `GET /api/v1/platform/llm-budget`: per scope (`canary`, `run`, `pilot_total`) el cap
  vigent, d'on surt (`config` o `authorization`), el gastat segons el llibre i si el
  pressupost és `exhausted`.
* `POST /api/v1/platform/llm-budget/authorizations`: cos `scope`, `cap_usd` (> 0) i
  `note` (obligatòria, 1–500 caràcters; per exemple la referència a l'aixecament
  escrit del propietari; mai un secret). Retorna 201.

**Quan s'excedeix un cap.** Abans de confirmar, el cost estimat ha de cabre al cap del
run i al total; abans de cada chunk, el gastat ha de cabre al cap total. En excedir-ne
un, no s'envia res més: el run es tanca `PARTIAL` (o `FAILED` si no havia completat cap
chunk) amb `error_code=budget_exceeded` i desa l'scope excedit a
`provider_metadata.budget_exceeded_scope`. Des d'aleshores **tota quota nova que hagi
de gastar queda `allowed=false` amb `budget_exhausted`**.

**Com es reobre.** Només amb una autorització **posterior** de l'scope excedit o de
`pilot_total`. Registra-la amb el `POST` d'autoritzacions (amb el cap nou i la nota),
comprova-ho amb el `GET` i demana una quota nova de represa. No s'edita mai la
configuració per saltar-se el cap.

Altres codis de la porta, tots abans de reservar el run i de cap crida:
`provider_not_real`, `model_not_pinned`, `price_unknown`, `budget_exhausted`,
`budget_exceeded`.

## 8. Prova disposable pas a pas (spec §6.7)

Objectiu: veure l'extracció v3/V1 de cap a cap sobre L'Escala (PCAP + PPT) amb un model
real, en una base d'usar i llençar, **sense tocar el pilot**. És el requisit previ de la
secció 9. Aquest procediment **no s'ha executat** en aquesta fase.

### 8.0 Condicions

* **`ENVIRONMENT` diferent de `test`** i **sense `ALLOW_SIMULATED_ANALYSIS`**: la porta
  `provider_not_real` ha d'actuar com al pilot. (Amb `ENVIRONMENT=test` o amb la
  bandera, una simulació passaria i la prova no seria representativa.) A la sessió:
  `ENVIRONMENT=local` (o el que correspongui), `ALLOW_SIMULATED_ANALYSIS` sense definir.
* La sonda (`apps/api/scripts/v3_tender_probe.py`) només accepta bases
  `licitacions_v3_probe_*`, un sol host explícit, mai el port 5432, cap paràmetre a la
  DSN, i **refusa per nom les bases canòniques** (`check_database`). La base ha de ser
  al `head` d'Alembic: la sonda no migra res.
* Restaurar una còpia del pilot conté dades reals: només amb autorització explícita del
  propietari (criteri 2 de la secció 8.8).

### 8.1 Restaurar la còpia a una base disposable

1. Restaura un `pg_dump` del pilot (de la còpia prèvia de la secció 6) a una base nova
   `licitacions_v3_probe_lescala_<data>` al PostgreSQL local (port 5433).
2. Migra **només la còpia**, a la sessió i sense imprimir la DSN:
   `DATABASE_URL=<dsn de la còpia> alembic upgrade head` des d'`apps/api`.
3. Defineix a la sessió `DATABASE_URL` (DSN **sense** contrasenya, a la base de la
   còpia) i `PGPASSWORD`. No els escriguis a cap fitxer.
4. Si la còpia no té encara els documents de L'Escala amb text extret, el paquet
   documental s'hi construeix per la secció 2 (sobre la còpia, mai al pilot).

### 8.2 `--plan-only` (sense cost)

```bash
cd apps/api
python scripts/v3_tender_probe.py --expediente 2026/3964 --expect-buyer <fragment del nom de l'òrgan> --profile v1 --plan-only
```

No construeix cap proveïdor i obre una transacció `READ ONLY`. **Números esperats**:
2 documents, **41 pàgines**, **9 chunks** (7 del PCAP i 2 del PPT), **0 omesos**, QA
coneguda. A més, ≈150.000 caràcters d'usuari més el prompt V1; estimació V1 ≈ 70.000
tokens d'entrada (cota 104.000) i ≈ 39.000 de sortida (sostre 72.000). Si el pla no
coincideix (documents, pàgines o chunks), **atura't**: és el criteri 3 de la secció 8.8.

### 8.3 Canary (≤ 2 chunks, cap 1 $)

Configura a la sessió el model pinat (`LLM_MODEL` idèntic a una entrada de
`LLM_ALLOWED_MODELS`), `LLM_MODEL_PRICES`, `LLM_PROVIDER=anthropic`,
`LLM_MAX_RETRIES=0` i la clau del proveïdor (`ANTHROPIC_API_KEY`, **només a la
sessió**). Tria els dos chunks (la sonda ajuda a trobar-los amb
`--locate ETIQUETA=tipus_document:pàgina`; deixa'n constància) i executa:

```bash
python scripts/v3_tender_probe.py --expediente 2026/3964 --expect-buyer <fragment> --profile v1 --chunks <i>,<j> --paid-run
```

La porta unica aplica l'scope `canary` (cap `LLM_BUDGET_CANARY_USD`). Si la porta
refusa (`provider_not_real`, `model_not_pinned`, `price_unknown`, `budget_exhausted`,
`budget_exceeded`), la sonda surt amb codi 4 sense cap crida. El run de la sonda és
`force` i en una licitació pública **mai és canònic** (`forced_not_canonical`): no
passa a vigent i no toca res del que veu cap empresa.

### 8.4 Llegir el `chunk_report`

La sortida és JSON amb codis i xifres (cap text de plec, cap resposta del model):
`run_id`, `status`, `error_code`, `requirements_extracted`, `requirements_rejected`,
`award_criteria_extracted`, `evidence_rejected`, `input_tokens`, `output_tokens` i, per
chunk, `status`, `error_code`, `output_truncated`, `stop_reason`, tokens, `rejections`
per motiu i els recomptes de rebuigs d'esquema. L'`RUN_ID=` surt sol a la seva línia.
Resum `canary`: `attempted`, `succeeded`, `actual_failures`, `schema_invalid`,
`output_truncated`.

### 8.5 Resume de la resta

Només si el canary compleix els criteris de la secció 8.8:

```bash
python scripts/v3_tender_probe.py ... --resume-from-run <RUN_ID> --plan-only
python scripts/v3_tender_probe.py ... --resume-from-run <RUN_ID> --paid-run
```

(`--resume-chunks` limita la represa a chunks pendents concrets; un chunk ja completat
no es torna a pagar mai.) La represa és exactament la mateixa anàlisi: si els plecs,
el perfil, el proveïdor o el model han canviat, es refusa abans de cap crida. El cap
`LLM_BUDGET_RUN_USD` es compta sobre tota la cadena de represa.

### 8.6 Puntuació

Compara amb l'oracle públic **només sobre els ítems citats al PCAP/PPT** (sostre ≈ 31 %
del gold, perquè la resta s'aguanta en documents que aquesta prova no té). Per al
paquet documental i l'estructura, fes servir l'eina de només lectura:

```bash
python -m app.evaluation.lescala_acceptance --database-url <dsn sense contrasenya> --tender-id <uuid> --case lescala-2026 --json
```

L'eina no escriu res (sessió de només lectura) i classifica les diferències com
`not_in_source_docs`, `extraction_missed`, `structure_not_authored`,
`oracle_outdated` o `unverifiable`. Cap diferència es força: no s'edita res per quadrar
amb l'oracle.

L'informe que en surt: codis i números (chunks, tokens, cost, `evidence_rejected`,
requisits per document, diferències classificades), sense text de plec ni credencials.

### 8.7 DROP

Acabada la prova, esborra la base `licitacions_v3_probe_lescala_<data>` i esborra la
clau de la sessió. Verifica que el llibre de despesa de la còpia no es confon amb el
del pilot (són bases diferents).

### 8.8 Criteris d'autorització i d'avortament

**Criteris d'autorització de la primera crida real (tots set):**

1. Aixecament escrit del propietari de l'aturada pressupostària, amb cap de despesa
   (proposta: canary ≤ 1 $, run complet ≤ 3 $).
2. Destinació = base disposable; pilot intacte amb `fake`.
3. `--plan-only` coincideix amb els números de 8.2.
4. Perfil decidit i registrat (V1 de producte per defecte).
5. Criteris d'avortament pactats (vegeu sota).
6. Run complet només si el canary completa, cada chunk amb requisits en dona ≥ 1 amb
   evidència validada, `evidence_rejected` < 30 % i els tokens d'entrada són dins de
   ±50 % de l'estimació.
7. Les capes de «mai vigent si no és real» (spec §6.3: quota/handler, promoció a
   `_finalise_run` amb `run_basis`, i dades amb la migració 0061) estan desplegades
   abans que cap run real toqui el pilot.

**Criteris d'avortament:** qualsevol error fatal, o `output_truncated`, o
`schema_invalid` en ≥ 1 dels 2 chunks del canary atura la prova. També s'atura si un
cap de despesa es dispara (`budget_exceeded`) o si els tokens queden fora de la
tolerància del criteri 6.

## 9. Punt exacte d'activació al pilot i què queda prohibit fins llavors

**Punt d'activació.** El clic «Inicia l'anàlisi» (confirmació de la quota) d'un
owner/admin/bid_manager de l'empresa pilot sobre L'Escala, amb `LLM_PROVIDER=anthropic` i la
clau **només a la sessió** de l'API i del worker (mateix proveïdor i model als dos),
**després** de tot això:

1. 3B-2 desplegada al pilot (migracions 0058–0061 incloses, secció 6), amb la còpia
   prèvia feta.
2. Prova disposable superada (secció 8).
3. Acceptació explícita del paquet incomplet (`accept_incomplete_package`, si el paquet
   consta com a incomplet; per a L'Escala el PCAP referencia DOC-03/04/05, que la quota
   mostrarà).
4. Aixecament escrit del propietari. Decisió 2 del propietari: cap run real de L'Escala
   només amb PCAP + PPT; com a mínim els documents oficials públics DOC-03, DOC-04 i
   DOC-05 verificats, i idealment el paquet oficial complet.

**Prohibit fins llavors:**

* Cap crida a cap model real al pilot; el pilot s'executa amb `LLM_PROVIDER=fake` i la
  quota queda `allowed=false` per `provider_not_real`.
* Posar `ALLOW_SIMULATED_ANALYSIS=true` al pilot.
* Restaurar o tocar la base del pilot per a proves; la prova va a la còpia disposable.
* Forçar un run (`force`, selecció de documents o perfil) per una empresa: només
  l'operador, via `POST /api/v1/platform/tenders/{tender_id}/analysis/force`, i aquest
  run es desa però mai és canònic (`forced_not_canonical`) i passa per la mateixa porta
  de despesa.
* Omplir l'estructura o verificar documents per quadrar amb l'oracle.

## 10. Residuals coneguts

* **La demo s'atura a compliment.** `scripts/demo_scenario.py` s'atura al pas de
  compliment (422 `no_requirements_to_assess`): una simulació (`fixture`) no és mai
  vigent (§6.3) i l'adopció refusa simulacions (`requirement_run_not_adoptable`). La
  demo ja no arriba a compliment/candidatura/GO sense un proveïdor que passi per real;
  és una decisió pendent del propietari.
* **`completed_empty` no es reutilitza.** Un run real acabat sense cap requisit ni
  criteri sobre ≥ 5 pàgines queda `completed_empty` amb `needs_review`: no es promou i
  no s'aprofita com a anàlisi vigent; una persona l'ha de revisar. El downgrade de 0061
  el refusa.
* **El motor de compliment antic llegeix el canònic global.** No distingeix el punter
  de l'empresa (`company_tender_requirement_runs`); deute del motor antic.
* **Runs llegats sense selecció desada.** La selecció explícita de documents d'un run
  es desa a `provider_metadata` en tancar-lo; per als runs anteriors es dedueix dels
  paràmetres de la feina que el va executar. Un run llançat fora de la cua sense cap de
  les dues dades es tracta com a canònic: la seva obsolescència es calcula sobre
  l'empremta canònica, no sobre la selecció real que hagi pogut tenir.
* **Un benchmark de més de 3 $ requereix autorització.** El benchmark crida la mateixa
  anàlisi i passa per la mateixa porta de despesa (cap per run `LLM_BUDGET_RUN_USD`,
  3 $); per superar-lo cal una autorització nova (secció 7).
* Altres residuals de la revisió del pilot (addendum): C2 (cap mapping oficial
  NUTS → nom: es mostra el codi), B7 (cap API de `historic_bids`), C6 (ordenació per
  rellevància només a PostgreSQL), N1 (l'estat «Analitzat» s'infereix sense saber quins
  documents va llegir el run) i, de rendiment, les consultes addicionals per a
  `section_status` a la fitxa i `analysis_state` per fila a la llista de workspaces.
