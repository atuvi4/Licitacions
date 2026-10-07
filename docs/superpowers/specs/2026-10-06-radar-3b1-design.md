# Phase 3B-1 — Radar foundation: ingestió pública, catàleg complet i cerca

**Estat:** aprovada amb decisions tancades (2026-10-06, v2). Base: `integration/phase3a-pilot` = pilot `49c0dd51bdb258f180c4a9bf6c57e4004f0e253d`, Alembic `0054_decision_analysis_v3`.
**Branca:** `engine/phase3b-radar-foundation` (worktree `Licitacions-radar`).
**Documents companys:** `2026-10-06-radar-3b1-sources.md` (investigació de fonts oficials, URLs i data de comprovació); auditories del repositori a `.superpowers/radar-3b1/audit-*.md` (fora de git; resum a §1).

## 0. Principi i objectiu

**El Radar prioritza. La cerca no censura.** 3B-1 és exactament això: **font → ingestió → catàleg complet → cerca**. Tuvi es connecta a una font pública oficial, ingereix de manera idempotent, normalitza al model intern conservant procedència i identificadors de font, detecta actualitzacions i revisions sense regressions, evita duplicats dins de la font, exposa el catàleg complet i permet buscar qualsevol licitació encara que sembli poc rellevant. Cap prefit, perfil ni rànquing controla la inclusió. No hi ha rànquing nou, descart humà, probabilitat de guanyar, GO/NO-GO automàtic ni enriquiment inventat de territoris.

## 1. Mapa: què existeix i què falta

### 1.1 Existeix i es reutilitza tal qual

| Peça | On |
|---|---|
| Connector PLACSP Atom/CODICE: client `httpx` (User-Agent amb contacte, timeouts, 3 reintents, pausa, límit de mida), parser `defusedxml` entrada per entrada (`None` si falta), normalitzador com a frontera canònica (enrichment només determinista) | `app/ingestion/placsp/{client,parser,normalizer,codes}.py` |
| Orquestració amb regles d'idempotència protegides per constraints (A/B/C, §4.1) | `app/ingestion/placsp/service.py` (`PlacspIngestionService.run` :110, `_process_entry` :238, `_apply` :354) |
| Provenance crua: `Source`, `IngestionRun`, `SourceRecord` (XML immutable a l'object storage, `raw_hash`, `parser_version`, `fetched_at`, `source_updated_at`, `external_version_id`) | `app/models/sources.py`; `uq_source_records_content`, `uq_source_records_revision` |
| Catàleg: `Buyer` (`uq_buyers_source_external`), `Tender` (`uq_tenders_source_external (source_id, external_id)`, `source_url`, `source_status_code`, imports, NUTS/lloc tal com els dona la font, `owner_company_id` NULL = pública), `TenderLot` (soft-delete `removed_at`), `TenderCPV`/`TenderLotCPV`, `TenderDocument` (`uq_tender_documents_tender_url`) + `TenderDocumentFile/Content` (sha256) | `app/models/tenders.py`, `tender_documents.py` |
| Revisions: `TenderRevision` (snapshot + `normalized_hash`, append-only) i `TenderChangeEvent` (`is_material`, `impacts`) via l'única porta `record_revision()`; `IMPACT_MATRIX` versionada `tender-changes-v1` amb impactes `informational / prefit_affecting / requirements_affecting / compliance_affecting / workspace_critical`; `GET /tenders/{id}/history` | `app/services/tender_lifecycle.py:217`, `app/tenders/changes.py:99,197` |
| Descàrrega de plecs sota demanda (guarda SSRF, 60 MB, sha256, versions) | `app/services/document_download.py`, `url_guard.py` |
| Cua `work_jobs` per feines **d'un tender** (`tender_id` NOT NULL; `process_tender_documents`, `analyze_tender_requirements`) | `app/jobs/*` |
| Contracte únic de filtres per a `GET /tenders` i `GET /companies/{c}/matches` | `app/api/v1/tender_filters.py:27` |
| Prefit per empresa: etiqueta, **mai filtra**; «Totes» ja fa `OUTER JOIN` | `app/matching/service.py`, `routers/matching.py:105-126` |
| Visibilitat única, import manual privat, `ImportTenderWizard`; pàgina Oportunitats i fitxa amb «Analitza»/«Obre candidatura» que no depenen del prefit | `app/tenders/visibility.py`, `apps/web/src/app/(app)/licitacions/*` |
| Tests i fixtures públiques del connector (feed 643, entrades reals retallades) | `tests/test_ingestion_placsp.py`, `tests/fixtures/placsp/` |

### 1.2 Falta o és parcial (3B-1 ho resol)

| Forat | Evidència | Resolució |
|---|---|---|
| El connector només llegeix `sindicacion_643` (perfils allotjats a PLACSP); la PSCP catalana només arriba per l'agregat 1044 | `client.py:32-33`; sources §2 | nova fila `Source` per al 1044 (§2) |
| Només CLI; sense execució programada ni lock; `work_jobs` és per tender | `__main__.py`; `models/jobs.py:95` | runner de sincronització de font, separat de `work_jobs` (§6) |
| Checkpoint = URL de fitxer; sense marca d'aigua ni solapament; sense ETag/`Retry-After`; URL del feed constant; cap salut observable de la font | `service.py`, `client.py` | salut/cursor a `Source`, incremental amb solapament (§6) |
| Làpides parsejades però no aplicades; cap guarda d'ordre; conflictes `updated` igual/hash diferent no detectats | `parser.py:452`, `service.py:218-236, 354-418` | presència de font i ordre (§4) |
| Documents desapareguts mai marcats; CPV sync delete+insert | `service.py:469-545` | absències només des d'una publicació completa posterior (§4.4) |
| `ted_uuid`, subtipus, codis crus parsejats i descartats | `parser.py:108-111` | bossa de provenance per font (§3) |
| L'anàlisi v3 caduca amb **qualsevol** revisió (`inputs_hash` inclou `tender_revision_id`) | `app/analysis/fingerprint.py:387`; `services/tender_analysis.py:263` | base = última revisió `analysis_affecting` (§4.5) |
| Cerca `LIKE` títol/expedient, sense accents, sense comprador/descripció/lots, CPV exacte sense índex, cap filtre de font, ordre per defecte `score` | `tender_filters.py:27-95`, `page.tsx` | representació normalitzada + índexs + filtres + ordre neutral (§7) |
| Copy que diu «N oportunitats obertes» (N = candidats) sobre el catàleg | `shell` cerca global | copy factual (§8) |

## 2. Font inicial: PLACSP sindicació agregada 1044

**Decisió (tancada):** primera font operacional = `https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/PlataformasAgregadasSinMenores.atom` (Atom + CODICE, especificació oficial «Formato de sindicación» v1.10), com a **nova fila `Source`** `placsp_agregadas` («PLACSP · plataformes agregades»), no com a comportament dins de la font `placsp` (643) actual, que queda registrada però no activa.

Per què: reutilitza el pipeline CODICE existent; el fitxer del feed es regenera aproximadament un cop al dia (cap a les 03:00, unes 7 h abans que el dataset obert de la PSCP, segons el spike del 2026-10-06); làpides `ANULADA`/`CERRADA`; plecs enllaçats (`https://contractaciopublica.cat/portal-api/descarrega-document/{id}/{hash}`); URL pública estable (`entry/link` = pàgina PSCP amb `id_intern`).

**No és «la font completa de Catalunya».** És una agregació d'un conjunt reduït de dades, de completesa no demostrada: la comparació d'una mostra ES512 contra la font oficial PSCP (dataset `ybgg-dgi6`) és **acceptance de la prova disposable** (§11, spike §2.1), i la salut/completesa de la font és observable (§6.1). La PSCP via Dades Obertes queda com a pla B / segona font, no activa a 3B-1. Limitacions acceptades del 1044 (confirmades pel spike §2.1 del 2026-10-06: 100 % dels anuncis no menors del 5/10 presents, ES512 66/66, camps essencials 98–100 %): òrgan identificat només per `ID_OC_PLAT` (sense NIF/DIR3), sense solvència ni criteris (són als plecs), CPV per lot només des del 15/12/2023, el primer CPV publicat és el principal només en ~83 % dels casos (es manté la convenció `is_main` = primer, documentada com a heurística), títol = objecte del contracte (12–19 % en castellà), només dos documents enllaçats (PCAP i PPT), i ~26 % dels expedients de Girona porten només `ES51` com a NUTS: el filtre de territori per prefix `ES512` no els inclou (decisió del propietari pendent: opció «inclou els d'àmbit Catalunya sense província»; mai s'infereix la província).

### 2.1 Spike de completesa PSCP (a la prova disposable, sense descàrrega massiva)
Mostra equivalent en temps i territori (un o dos dies, `codi_nuts` ES512 / entrades de `contractaciopublica.cat` amb NUTS ES512): quants expedients a cada banda (per `id_intern`), quins falten a cada banda, timestamps (`updated` vs `:updated_at`/dates de fase), camps essencials (títol, expedient, òrgan, termini, imports, CPV, NUTS) i documents quan siguin observables. No es corregeix cap diferència: l'objectiu és decidir si el 1044 és prou fiable com a primera font operacional. Resultat documentat a l'informe de la prova.

## 3. Contracte d'ingestió

Regla general: **cap camp que la font no proporcioni s'inventa; queda `NULL`/`unknown`** amb el codi cru conservat. `Tender` només creix amb conceptes canònics entre fonts; el que és específic de PLACSP va a la bossa de provenance.

| Camp del contracte | Font (CODICE/Atom) | Destí |
|---|---|---|
| source | fila `sources` (`code`) | `Tender.source_id` |
| source_tender_id | `atom:id` (opac) | `Tender.external_id`, `SourceRecord.external_id`; identitat `(source_id, external_id)` |
| URL pública | `atom:link[rel=alternate]` | `Tender.source_url` |
| timestamps de font | `atom:updated`; moment de baixada | `SourceRecord.source_updated_at`, `fetched_at`; **nou** `Tender.source_updated_at` (= `updated` de la publicació canònica vigent; canònic cross-source) |
| data de publicació | mínim `IssueDate` dels anuncis de convocatòria | `Tender.publication_date` |
| termini | `TenderSubmissionDeadlinePeriod/EndDate(+EndTime)` | `Tender.deadline_at` (Europe/Madrid; sense hora = 23:59:59); lot: NULL (la font no el dona) |
| comprador | `LocatedContractingParty/Party` | `Buyer` per font; `external_identifier` = ID_PLATAFORMA > DIR3 > NIF > **nou** `ID_OC_PLAT`; NIF/DIR3 NULL si no venen |
| expedient | `ContractFolderID` | `Tender.expediente_number` (no únic) |
| títol / objecte | `ProcurementProject/Name`; `atom:summary` | `Tender.title`; `Tender.description` (resum de l'anunci) |
| estat contractual | `ContractFolderStatusCode` (PRE/PUB/EV/ADJ/RES/ANUL) | `Tender.status` + `source_status_code`. **Només una dada explícita de la font canvia l'estat** (cancel·lat, adjudicat, resolt…) |
| presència a la font (**nou**) | `at:deleted-entry` / reaparició | `Tender.source_removed_at` (§4.3); l'estat contractual es conserva |
| tipus / procediment | `TypeCode`, `ProcedureCode` | `contract_type`, `procedure_type` (`unknown` si desconegut) |
| provenance específica (**nou**) | `TED` (uuid), `SubTypeCode`, codis crus de `TypeCode`/`ProcedureCode`, `ID_OC_PLAT`, `id_intern` de l'URL PSCP (regex `/detall-publicacio/([0-9a-f-]{36})/`) | `Tender.source_metadata` JSON, claus tancades per al parser `placsp`: `ted_uuid`, `contract_subtype_code`, `contract_type_code`, `procedure_code`, `placsp_organ_id`, `pscp_id_intern` (absents si no venen). Cap columna nova per a cap d'ells |
| CPV | `RequiredCommodityClassification` (expedient i lot) | `tender_cpvs` (`is_main` = primer publicat), `tender_lot_cpvs`; `cpv_codes` amb descripció provisional si el codi és nou |
| imports | `EstimatedOverallContractAmount`, `TotalAmount`, `TaxExclusiveAmount`, moneda | `estimated_value`, `base_budget` (amb IVA), `base_budget_without_tax`, `currency` (expedient i lot) |
| territori | `CountrySubentityCode` (NUTS), `CountrySubentity`, `Country` | `nuts_code`, `place_of_execution`, `country_code`; **mai** municipi/comarca/INE |
| lots | `ProcurementProjectLot` | `tender_lots` (upsert per `external_lot_id`; `removed_at`/restauració, §4.4) |
| documents / URLs | `Legal/Technical/AdditionalDocumentReference` | `tender_documents` (`url` única per tender, tipus, `published_at`, `source_record_id`); **nou** `removed_at` (§4.4); baixada sota demanda |
| provenance crua | XML de l'`entry` o de la làpida | `SourceRecord` (immutable) |
| fingerprints | — | `SourceRecord.raw_hash`, `TenderRevision.normalized_hash`, `TenderDocumentContent.sha256` |
| text de cerca (**nou**) | derivat | `Tender.search_text` (§7.2) |

`parser_version` passa a `placsp-v2` quan el parser canvia (Tasca 3).

## 4. Ordre, conflictes, presència a la font, absències i revisions

### 4.1 Regles d'idempotència (existents)
A. mateix `external_id` + mateix `raw_hash` → no-op (`last_seen_at`); B. mateix `external_id` + hash diferent → `SourceRecord` nou (`revision_number`+1) i `Tender` actualitzat; `record_revision()` decideix si hi ha `TenderRevision`; C. `external_id` nou → `Tender` nou.

### 4.2 Ordre i conflictes (nou, obligatori)
Sigui `U` l'`updated` de la publicació rebuda i `U*` = `Tender.source_updated_at` (publicació canònica vigent).
- `U < U*` → **mai regressa**: es conserva el `SourceRecord` (provenance, `processing_status = processed`, `error_message = "superseded"`), no s'aplica res al `Tender`, no hi ha revisió; `IngestionRun.records_superseded += 1`.
- `U = U*` i mateix hash → no-op (regla A).
- `U = U*` i hash diferent → **conflicte**: es conserven **tots dos** `SourceRecord`, no es tria cap com a «més nou», no s'aplica al `Tender`; `SourceRecord.processing_status = conflict` (**nou** valor de `ProcessingStatus`), `error_message` amb els dos hashes; `IngestionRun.records_conflicted += 1`; log `ingestion.conflict` amb `external_id`. Un humà/operador ho resol més tard (fora d'abast).
- `U > U*` → s'aplica (regles B/C).
- `source_updated_at` nul (tenders anteriors a 3B-1) → s'aplica i s'inicialitza.
Les correccions de PLACSP «sense canvi d'`updated`» cauen al cas conflicte quan el contingut difereix: observable, no destructiu.

### 4.3 Presència a la font (làpides) — sense estat `withdrawn`
Una `at:deleted-entry ref when` només demostra que l'entrada ja no és accessible a la font. Per tant:
- es desa com a `SourceRecord` (XML de la làpida, `external_version_id = when`);
- subjecta a ordre: si `when < U*` → superseded (una làpida antiga no oculta una publicació més nova); si `when ≥ U*` → `source_removed_at` conserva la primera data de retirada no restaurada (la data “des del” de la UI; si encara és nul, `= when`), `source_updated_at = when`, `record_revision(trigger="tombstone")` emet **`SOURCE_ENTRY_REMOVED`** (impacte `informational`; `is_material = false`);
- **`Tender.status` no canvia** (es conserva l'últim estat contractual conegut); cap inferència «deleted-entry = retirada/cancel·lada». Només `ContractFolderStatusCode = ANUL` en una publicació explícita porta a `cancelled` (com avui);
- republicació posterior (`U > source_removed_at`) → `source_removed_at = NULL` i **`SOURCE_ENTRY_RESTORED`** (informatiu);
- la fila mai s'esborra: continua al catàleg, a la cerca i a l'historial; a la UI es diu «Ja no disponible a la font (des del {data})», mai «retirada».
El snapshot de revisió inclou `source_removed: bool` perquè els dos codis surtin de `record_revision()` i no d'un camí paral·lel.

### 4.4 Absències: documents i lots (nou, obligatori)
Un document o lot només es marca `removed_at` quan la seva absència es dedueix d'**una publicació del mateix tender** que és (a) posterior (`U > U*`), (b) rebuda i parsejada completa (sense error de parseig de l'entrada), i (c) autoritativa per a aquell tender (la pròpia `entry` CODICE, que conté la llista completa de documents i lots). **Mai** per absència del tender en una pàgina, per una pàgina fallida, per una cadena `next` incompleta ni per un sync `partial`: aquests casos no apliquen res. Una publicació completa llegida en una pàgina obtinguda s'aplica atòmicament encara que el run acabi `partial` per una pàgina posterior; §4.6 #8 es refereix a les pàgines no obtingudes. Reaparició → `removed_at = NULL` (`DOCUMENT_ADDED` / `LOT_RESTORED`). **Cap delete físic** de documents ni lots; les versions baixades es conserven. CPV: diff (afegir/treure només el que canvia). El snapshot inclou `removed` als documents perquè `DOCUMENT_REMOVED` salti per la matriu existent.

### 4.5 Base de l'anàlisi = última revisió `analysis_affecting` (nou)
`record_revision()` no canvia (tota diferència és una revisió). Es defineix **`analysis_affecting`** = revisió amb almenys un `TenderChangeEvent` els `impacts` del qual contenen `requirements_affecting` **o** `compliance_affecting`. No fan caducar la v3 per si sols: `informational`, `prefit_affecting`, `workspace_critical`. La funció `latest_analysis_affecting_revision(session, tender_id)` (a `services/tender_lifecycle.py`; retorna la revisió 1 si cap ho és) substitueix `latest_revision` com a component `tender_revision_id` d'`inputs_hash` a `services/tender_analysis.py` (`analyze_tender` i `is_stale`). **`app/analysis/*` no es toca.**
Tests obligatoris: títol → mateix basis; només CPV → mateix basis; import → nou basis; termini → nou basis; document afegit/retirat/substituït → nou basis; cancel·lació sola (`TENDER_CANCELLED`, `workspace_critical`) → mateix basis (la candidatura pot deixar de ser actionable, però no obliga a recalcular la v3). Runs existents amb hash sobre la revisió absoluta caduquen una vegada (al pilot, 0 runs).

### 4.6 Acceptance d'idempotència i revisions
1. Ingerir dues vegades el mateix fitxer no crea cap `SourceRecord`, `Tender`, revisió, lot, document ni CPV; `records_unchanged = N`.
2. Mateix tender amb termini nou (`U > U*`) → `SourceRecord` 2, `Tender` actualitzat, `TenderRevision` 2, `DEADLINE_CHANGED` material; `GET /tenders/{id}/history` ho mostra.
3. Canvi no material (títol) → revisió + event informatiu; basis v3 inalterat.
4. Document nou → `DOCUMENT_ADDED`; document absent en publicació completa posterior → `removed_at` + `DOCUMENT_REMOVED`; reaparició → `DOCUMENT_ADDED` i `removed_at = NULL`.
5. Publicació antiga després d'una de nova → `superseded`, cap canvi al tender.
6. `U = U*` amb hash diferent → `conflict`, dos `SourceRecord`, cap canvi al tender.
7. Làpida → `source_removed_at`, `SOURCE_ENTRY_REMOVED`, `status` intacte; làpida antiga → superseded; republicació → `SOURCE_ENTRY_RESTORED`.
8. Sync `partial` (pàgina 2 falla) → cap tender, lot ni document desapareix; marca d'aigua no avança.

## 5. Deduplicació
Identitat exacta dins de la font: `(source_id, external_id)` (tender), `(source_id, external_identifier)` (buyer), `(tender_id, external_lot_id)`, `(tender_id, url)`, `sha256`. Tot existeix i s'enforça. **Cap deduplicació entre fonts a 3B-1** ni per similitud textual: si el mateix expedient arriba algun dia per 1044 i PSCP, coexisteixen fins que hi hagi una clau cross-source demostrable. Les claus candidates es conserven a `source_metadata`.

## 6. Execució: sincronització de font (separada de `work_jobs`)

Una ingestió de font és una feina **global**, no d'un tender: no es deforma `WorkJob` (`tender_id` continua NOT NULL). Mecanisme mínim i coherent a `app/ingestion/sync.py`:

### 6.1 Salut observable a `Source` (nou)
`last_feed_updated_at` (màxim `atom:updated` vist a la pàgina 1), `last_sync_started_at`, `last_sync_finished_at`, `last_sync_status` (`success`/`partial`/`failed`), `last_sync_pages`, `last_sync_entries`, `last_sync_tombstones`, `last_sync_error` (resum, sense dades personals), `updated_watermark` (cursor incremental), `sync_lease_owner`, `sync_lease_until`. `IngestionRun` guanya `pages_processed`, `records_superseded`, `records_conflicted`, `tombstones_applied`, `entries_restored`. Exposats per `GET /platform/sources` (operador) i al CLI `python -m app.ingestion.sync --status [--source X]` (només lectura, sense lease ni DSN).

### 6.2 Lock i execució
`run_source_sync(session_factory, source_code, *, mode, max_pages, backfill_until, resume)`: pren un **lease** sobre la fila `Source` (`UPDATE … WHERE sync_lease_until IS NULL OR sync_lease_until < now`, propietari = `hostname:pid:uuid`, durada 15 min renovada per pàgina); si no l'obté → `SyncAlreadyRunning` (no corren dos syncs de la mateixa font alhora); crea l'`IngestionRun`; executa `PlacspIngestionService.run(...)`; allibera el lease sempre (`finally`). Retry controlat: a nivell de pàgina, el client reintenta 3 cops amb espera creixent i `Retry-After`; una pàgina que falla després dels reintents acaba el run com a `partial` (no avança marca d'aigua ni checkpoint més enllà de l'última pàgina completa). Sense LLM.
CLI: `python -m app.ingestion.sync --source placsp_agregadas [--max-pages N] [--backfill-until YYYY-MM-DD] [--resume] [--every SEGONS]` (`--every` = bucle de procés propi, com el worker; el pilot l'engegarà amb `pilot.py` quan toqui). Resultat de sortida: codi 0 success, 2 partial, 1 failed.

### 6.3 Incremental amb solapament
El feed és paginat enrere en el temps per `rel=next`. Incremental: es llegeix des de la pàgina 1 fins que **totes** les entrades d'una pàgina tenen `updated < updated_watermark − OVERLAP` (`OVERLAP = 48 h`) o fins a `max_pages`. `updated > watermark` **no** és condició d'inclusió: totes les entrades de les pàgines visitades es processen per hash (regla A resol les conegudes). La marca d'aigua només avança quan el run acaba `success` i només fins al màxim `updated` **processat completament**. Una cadena `next` incompleta (sense `next` abans d'arribar al solapament) acaba `partial`.

### 6.4 Backfill acotat
`--backfill-until` recorre `next` fins que la pàgina només conté `updated` anteriors a la data o fins a `max_pages`; `partial` si s'atura per `max_pages`; `--resume` reprèn des del `checkpoint` (URL). Mai contra el pilot sense ordre expressa.

### 6.5 Prefit després del sync
Per als tenders creats/actualitzats, `recalculate` (`app/matching/service.py:192`) per a cada empresa amb perfil, acotat a aquests ids. Només etiqueta; cap efecte sobre la inclusió.

## 7. Catàleg complet («Totes») i cerca

### 7.1 Regla absoluta
«Totes» retorna **qualsevol tender públic visible ingerit**, inclosos sense match, `low_fit`, `insufficient_data`, sense CPV, sense import, sense territori conegut i ja no disponibles a la font. Cap `INNER JOIN` amb `company_tender_matches` ni `min_score` a «Totes» (test estructural sobre el SQL generat). Ordre per defecte **neutral**: `published_at desc nulls last, first_seen_at desc, id` (tie-break estable); `prefit_score` es mostra com a dada si existeix però mai controla la inclusió ni l'ordre per defecte. Els filtres s'apliquen només si l'usuari els fixa; un filtre d'import/termini exclou les files sense valor només aleshores, i la resposta ho quantifica (`excluded_without_amount`).

### 7.2 Representació de cerca normalitzada (determinista, a l'aplicació)
`normalize_search_text(s)`: NFKD → eliminació de marques diacrítiques (categoria `Mn`) → `casefold()` → només lletres/dígits i `/`-`.`-`-` conservats, la resta a espai → espais compactats. Ela geminada: abans del filtre de caràcters s'eliminen el punt volat (U+00B7) i el punt de guionatge (U+2027) i `l.l` es plega a `ll`, de manera que «instal·lació», `instal.lacio` i `INSTAL·LACIÓ` donen `installacio`; una falta amb una sola ela (`instalacio`) no hi coincideix, per disseny (`sql_fold` aplica el mateix plegat). `Tender.search_text` = normalització de `title ⊕ expediente_number ⊕ buyer.name ⊕ títols de lots` (descripció exclosa: al 1044 és text repetitiu i el títol ja porta l'objecte). Es manté a cada aplicació d'ingesta i a l'alta/edició manual; ordre `python -m app.tenders.search_backfill` (idempotent). La consulta `q` passa per **exactament** la mateixa funció i es parteix en fins a 6 termes; cada terme ha de coincidir (`search_text LIKE %terme%`). PostgreSQL: índex GIN trigram (`pg_trgm`) sobre `search_text`; ordre `relevance` = `similarity(search_text, q_normalitzat)` desc amb el tie-break neutral. SQLite: mateixa semàntica amb `LIKE` sense índex. **No es depèn d'`unaccent`.** Tests obligatoris: `telecomunicacio` troba «Telecomunicació»; `ESCALA`/`escala`/`l'Escala` equivalents.

### 7.3 Filtres (contracte únic `TenderFilters`)
`q`; `cpv` (llista, exacte o prefix, a tender i lots); `buyer_id`, `buyer_q` (text normalitzat sobre `buyers.name`); `nuts` (prefix) i `place_q`; `min_amount`/`max_amount` (sobre `coalesce(estimated_value, base_budget_without_tax, base_budget)`); `published_from/to`, `deadline_from/to`, `only_open` (opcional); `contract_type`, `procedure_type`, `status` (llistes); `source` (llista de `Source.code`); `source_removed` (`true`/`false`/absent); `has_lots`; `sort ∈ {published (defecte), relevance (només amb q), deadline, amount, first_seen, source_updated, score}`; `limit` 25 (màx. 100), `offset`; `total` exacte. Fila: camps actuals + `source_code`, `source_updated_at`, `source_removed_at`, `match` (nul·lable), `document_count`, `lot_count`. Facetes: `sources` i comptadors per `status`.

### 7.4 Índexs
`tenders(search_text)` GIN trgm (PG), `tender_cpvs(cpv_code)`, `tender_lot_cpvs(cpv_code)`, `tenders(first_seen_at)`, `tenders(source_updated_at)`.

## 8. UI mínima (3B-1)
Pàgina `/licitacions`: pestanya **«Totes»** (catàleg complet) amb caixa de cerca, filtres (CPV codi/prefix amb el cercador `/cpv-codes?q=`, organisme, territori, import, dates de publicació i termini, tipus, procediment, estat, font, «ja no disponible a la font»), resultats amb títol, organisme, termini, import, CPV principal, font, estat factual (etiquetes «Anul·lada» només si la font ho diu; «Ja no disponible a la font» per `source_removed_at`), prefit si existeix («Sense calcular» no és negatiu), i accions **Veure** (fitxa) i **Analitzar** (fitxa a la pestanya Anàlisi; el flux existent explica el pas previ si no hi ha requisits extrets). La vista actual (modes del prefit) es conserva com a pestanya «Per revisar» sense cap rànquing nou. Cerca global: placeholder «Cerca per títol, expedient, organisme o descripció» i «N licitacions al catàleg» (N = catàleg visible, no candidats). Mai s'amaga una licitació per rellevància baixa; mai «bloqueig», «exclosa», «no apta», «retirada».

## 9. Migracions
- **0055_radar_ingestion** (additiva, reversible): `sources` (+ les columnes de §6.1), `ingestion_runs` (+ `pages_processed`, `records_superseded`, `records_conflicted`, `tombstones_applied`, `entries_restored`, default 0), `tenders` (+ `source_updated_at` indexat, `source_removed_at`, `source_metadata` JSON/JSONB, `search_text` Text), `tender_documents` (+ `removed_at`), índexs `tender_cpvs(cpv_code)`, `tender_lot_cpvs(cpv_code)`, `tenders(first_seen_at)`. Enums Python: `ProcessingStatus.CONFLICT`, `ChangeCode.SOURCE_ENTRY_REMOVED/RESTORED`. **Cap canvi a `work_jobs`.** Cap taula de descart.
- **0056_radar_search** (PostgreSQL): `CREATE EXTENSION IF NOT EXISTS pg_trgm` + índex GIN trgm sobre `tenders.search_text`; a SQLite no fa res. **Prerequisit operacional:** es prova abans en una DB disposable amb el mateix rol i configuració del pilot; si el rol no pot crear l'extensió, la seva creació per superusuari és un prerequisit documentat del desplegament, no una sorpresa.
- Fila `sources` per a `placsp_agregadas` via el registre de fonts (`get_or_create_source(code)`), no via migració de dades.

## 10. Tests i fixtures públiques
Fixtures reals retallades del feed 1044 a `tests/fixtures/placsp/agregadas/` (entrades de `contractaciopublica.cat` amb lots i documents, làpides `ANULADA`/`CERRADA`, feed de dues pàgines amb `next`, variants: republicada amb `updated` anterior, mateix `updated` amb contingut diferent, document absent, lot absent, republicació després de làpida; sense blocs de contacte de persones; README amb URL, data i retalls). Tests de servei per a cada punt de §4.6; client amb `httpx.MockTransport` (reintents, `Retry-After`, 304, `next`, pàgina fallida → `partial`); sync (lease, dos syncs alhora, partial no avança marca d'aigua); anàlisi (§4.5, sis casos); cerca (SQLite semàntica + accents; PG `@pytest.mark.postgres` trigram, `relevance`, `EXPLAIN` amb índex, SQL de «Totes» sense `INNER JOIN`); web (pestanya Totes, filtres, accions, copy, 390 px). Gates: backend SQLite complet i PG (migracions **0054→0055→0056→0054 reals en PG disposable**, ingestió, lifecycle, cerca, sync, tenant_ops, anàlisi/gold, workspace, seguretat), ruff, web (vitest complet, tsc, lint, build), `git diff --check`, escaneig de secrets i dades. `LLM_PROVIDER=fake`.

## 11. Prova disposable abans del pilot (acotada i reproduïble)
1. DB disposable des del backup del pilot, migrada fins a 0056 (prova de `pg_trgm` amb el rol del pilot).
2. Sync limitat del 1044 (`--max-pages 2`), amb mostra catalana/ES512; recomptes i salut de `Source`.
3. Reexecució idèntica → zero duplicats, zero revisions.
4. Publicació modificada (fixture) → revisió i events correctes.
5. Publicació antiga després d'una nova → no regressió.
6. Làpida → `source_removed_at`, `SOURCE_ENTRY_REMOVED`, status intacte; làpida antiga → no regressió; republicació → `SOURCE_ENTRY_RESTORED`.
7. Sync `partial` (pàgina 2 forçada a fallar) → cap lot/document/tender desapareix; marca d'aigua no avança.
8. Cerca amb i sense accents; «Totes» inclou low-fit / sense match / sense fila de match.
9. Spike PSCP (§2.1) documentat.
10. UI «Totes» a 1440 i 390; «Analitzar» obre el flux existent.
11. DROP de la DB. Cap acció sobre el pilot; cap ingestió massiva.

## 12. Riscos
| Risc | Mitigació |
|---|---|
| 1044 incomplet per a Catalunya | spike §2.1 com a acceptance; PSCP pla B; salut observable |
| Correccions sense canvi d'`updated` | conflicte observable, no destructiu (§4.2) |
| Pàgina fallida / cadena `next` incompleta | `partial`, cap absència derivada, marca d'aigua no avança (§4.4, §6.3) |
| `pg_trgm` no permès al rol | prova prèvia amb el rol del pilot; prerequisit operacional documentat |
| Dades personals de contacte al CODICE | no es normalitzen; el cru resta a l'storage com a provenance; fixtures netes |
| Runs d'anàlisi existents caduquen una vegada pel canvi de basis | al pilot, 0 runs |
| Volum del backfill i `COUNT` | backfill acotat; índexs; es mesura a §11 |

## 13. Fora d'abast
Descart humà i rànquing del Radar (decisió humana reversible, separada de `CompanyTenderMatch`, en un bloc posterior); `withdrawn`; probabilitat de guanyar; GO/NO-GO automàtic; amagar tenders; inferir territoris/INE; identitat entre fonts; catàleg CPV oficial; PSCP/643/TED actius; descàrrega automàtica de plecs; alertes; canvis a `IMPACT_MATRIX` (tret dels dos codis informatius nous); desplegament; ingestió massiva.
