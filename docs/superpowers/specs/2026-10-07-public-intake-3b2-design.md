# Phase 3B-2 — Public tender → Resolution intake (spec v1)

Auditoria: `docs/superpowers/specs/2026-10-07-public-intake-3b2-audit.md` (+ detall A–E al workspace SDD). Base de codi: `507ac57` (pilot, DB 0057). Cap canvi de codi en aquesta fase; cap crida real a cap model.

## 0. Principi i objectiu
Construir el pont **tender públic → paquet documental complet → classificació → estructura/abast → requisits → anàlisi v3 → workspace resoluble** amb tres regles absolutes:
1. **Res inventat.** `unknown` continua sent `unknown`; un valor conegut només s'escriu amb cita; un paquet incomplet es declara, no s'omple. L'oracle de L'Escala només valida: mai emplena el pilot.
2. **Tres capes separades i etiquetades**: dades públiques de font (feed), dades derivades globals (bytes, text, classificació automàtica, estructura factual, requisits extrets dels plecs públics) i dades d'empresa (coneixement, encaix, evidències, preguntes, decisions, solució, preus, proposta). Cap document privat ni cap paràmetre d'una empresa entra mai en un run global compartit.
3. **Human-first i autoritat explícita**: tota correcció d'una interpretació global té actor, motiu, versió del fitxer i run de base; l'output automàtic és immutable; les empreses proposen, l'operador de plataforma promou.

## 1. Mapa: existeix / falta
Vegeu l'auditoria §1–2. En una línia: existeix bytes globals + versions + descàrrega segura + processament idempotent + classificador determinista + estructura 0048–0050 + motor v3 pur + extracció global amb quota. Falta: origen i verificació dels documents públics, via d'escriptura de l'operador, classificació global amb revisió, provenance i propostes d'estructura, buits de paquet, base/identitat/punter dels runs, frontera de paràmetres, correccions d'UX.

## 2. Paquet documental públic
### 2.1 Model (migració `0058_public_documents`, additiva, reversible)
A `tender_documents`:
| columna | tipus | semàntica |
|---|---|---|
| `origin` | enum `tender_document_origin` NOT NULL, default `feed` (backfill: `feed` si `url` no comença per `upload://`, si no `owner_upload`) | `feed` (enumerat per la font), `owner_upload` (pujada/ZIP en tender manual propi: l'actual), `operator_url` (obtingut per l'operador d'una URL oficial), `operator_upload` (pujat per l'operador) |
| `contribution_status` | enum NOT NULL default `not_applicable` | `not_applicable` (feed/owner), `pending_verification`, `verified`, `rejected` |
| `contributed_by` / `contributed_at` | FK users (SET NULL) / tz | qui l'ha aportat |
| `contribution_source_url` | String(1000) NULL | pàgina oficial d'on s'ha tret (perfil del contractant, PSCP) |
| `verified_by` / `verified_at` / `verification_note` | FK users / tz / String(500) | verificació humana de l'operador (obligatòria per entrar a l'anàlisi) |
| `duplicate_of_document_id` | FK `tender_documents.id` NULL | àlies: mateixos bytes vigents que un altre document del mateix tender (dedup lògic per `(tender_id, sha256)`), mai se selecciona per a anàlisi |
| `retired_by` / `retired_reason` | FK users / String(200) | retirada per l'operador (la del feed continua sent `removed_at` sense actor) |
| `governing_source_id` | FK `sources.id` NULL (ON DELETE **RESTRICT**: una font amb documents governats no es pot esborrar) | **source ownership**: el connector que governa el document (el feed que l'ha enumerat); NULL per a `owner_upload`, `operator_url`, `operator_upload`. Backfill: `tenders.source_id` per als `feed`. |
CHECKs: `contribution_status = not_applicable` ⇔ `origin IN (feed, owner_upload)`; `verified_at IS NOT NULL` ⇔ `contribution_status = verified`; `origin = feed` ⇔ `governing_source_id IS NOT NULL`.
**Regla de source ownership (MUST):** un connector només pot retirar, restaurar o sobreescriure (nom, tipus, `source_record_id`) els documents amb `governing_source_id` = la seva font. Qualsevol altre document és invisible per a la sincronització d'aquella font.
Regla de dedup lògic: després de descarregar/pujar, si un altre document actiu del tender té el mateix `sha256` vigent, el nou queda `duplicate_of_document_id` apuntant al primer (ordre: feed > operator_url > operator_upload; empat → més antic). Reversible: si el canònic es retira, l'àlies més antic es promou (servei, amb revisió).
**Mai** es marca contribució ni verificació a `tender_document_contents` (compartit entre tenants per sha256).

Propostes de documents per part d'empreses (`tender_document_proposals`): **fora d'abast de 3B-2** (decisió 3 del propietari). En 3B-2 només l'operador de plataforma incorpora i promou documents públics oficials absents del feed. Cap empresa escriu a `tender_documents` d'un tender públic.

### 2.2 Prioritat d'obtenció i provenance
1. **Font pública oficial per URL, descarregada pel sistema**: `add_document_by_url(tender, url, source_page_url, actor)` reutilitza `download_document` + `get_or_create_content` + `_ensure_current_extraction`, amb **allowlist** `DOCUMENT_OFFICIAL_HOSTS` (default: `contractaciopublica.cat`, `contrataciondelestado.es`, `contrataciondelsectorpublico.gob.es`; sufix de domini; configurable) a més de la guarda SSRF. Es conserven `source_url`, `final_url`, `http_status`, `downloaded_at`, `sha256`, `contribution_source_url`, actor. Estat inicial `pending_verification`.
2. **Pujada per l'operador** només quan la URL oficial no és descarregable (SPA, sessió): `origin=operator_upload`, URL sintètica `operator-upload://<uuid>`, `contribution_source_url` obligatòria (la pàgina on és publicat), `pending_verification`.
3. **Verificació**: l'operador confirma (nota opcional) que el document és el publicat oficialment → `verified`. Només llavors el document és **analitzable**.
Cada acció grava `audit.record(company_id=None, action ∈ {create, update, verify, retire, restore})` i una revisió del tender amb trigger nou `document_intake` (origen `internal`, §7.1). `DOCUMENT_ADDED` continua sent `REQUIREMENTS_AFFECTING` **només quan el document passa a `verified`** (un `pending_verification` no canvia empremtes de ningú).

### 2.3 Selector únic
Nou predicat `ANALYZABLE_DOCUMENT = ACTIVE_DOCUMENT ∧ duplicate_of_document_id IS NULL ∧ (origin IN (feed, owner_upload) ∨ contribution_status = verified)` a `app/tenders/documents.py`, consumit per: processament (default), classificació global, selecció canònica d'extracció, `source_refs.assert_ref_citable`, empremtes. `ACTIVE_DOCUMENT` es manté per a llistats (amb badges d'estat).

### 2.4 Versions, retirades i restauracions
- Mateixa URL + bytes nous = nova versió de `TenderDocumentFile` (ja existent); es registra `DOCUMENT_REPLACED`. Re-descàrrega forçada (`force_download`) **només operador** en tenders públics.
- `_sync_documents` retira i restaura **només** els documents amb `governing_source_id` = la font que sincronitza (bug A §2.1; regla de source ownership §2.1). Un document d'operador el retira/restaura només l'operador (`retired_by`, `retired_reason`, revisió `document_intake`). Mai esborrat físic. **Prova obligatòria**: un document oficial suplementari verificat sobreviu intacte a un sync 1044 complet posterior.
- Índex únic parcial nou: un sol `is_current` per `tender_document_id` a `tender_document_files` (avui només per codi).

### 2.5 Completesa del paquet (global)
Taula global `tender_package_gaps` (`tender_id`, `code` ∈ {`required_role_missing`, `referenced_document_missing`, `no_text`, `format_not_analyzable`, `annex_missing`, `declared_by_operator`}, `detail` JSON (nom/referència citada), `referenced_from_source_ref_id` FK NULL, `would_provide` JSON (llista d'entitats/camps afectats), `producer` ∈ {`classification`, `operator`}, `producer_run_id` NULL, `resolved_by_document_id` FK NULL, `resolved_at`, `created_at`; únic `(tender_id, code, coalesce(detail->>'key',''))` mentre no resolt). Productor automàtic: les troballes de la classificació global (§3). L'operador pot declarar-ne i resoldre'n. `TenderStructureView` exposa `package_status ∈ {complete, incomplete, unknown}` i `gaps` (§4.4).

### 2.6 Qui pot fer què
| acció | operador de plataforma (`PlatformAdmin`: superusuari sense empresa) | empresa (owner/admin/bid_manager) |
|---|---|---|
| afegir per URL / pujar / verificar / retirar / restaurar en tender públic | sí (router nou `/platform/tenders/{id}/documents`) | no |
| proposar un document oficial | — | fora d'abast (3B-2 no té workflow de propostes tenant) |
| processar (descarregar/extreure) documents analitzables | sí | sí (ja avui) |
| `force_download` / `force_reextract` en tender públic | sí | **no** (avui sí: es tanca) |
| tender manual propi | — | com avui (`owner_upload`) |
Visibilitat: `tender_documents` d'un tender públic és global per a tothom; els `pending_verification` es veuen amb badge però no són analitzables.

### 2.7 L'Escala
Font 1044 = PCAP + PPT (verificats per SHA). Els altres 7 fitxers de l'oracle (DOC-01, DOC-03, DOC-04 definitiu, DOC-05, XLSX, acord JGL; el DOC-04 fals no cal) **no s'aporten en aquesta fase**. Quan el propietari ho decideixi, entraran per §2.2 des de la pàgina oficial del perfil del contractant, amb verificació, i el SHA es compararà amb l'oracle **només per validar** (§8). **Decisió 2 del propietari:** cap run real de L'Escala només amb PCAP+PPT; com a mínim DOC-03, DOC-04 i DOC-05 públics oficials verificats, idealment el paquet oficial complet.

## 3. Classificació global amb revisió autoritzada
### 3.1 Per què avui és manual-only
Tenancy: les taules d'estructura/requisits no tenen `company_id`; escriure-hi en un tender públic seria escriure el que veu tothom; la classificació escriu `document_type`, que és del feed. (B §1)

### 3.2 Model (migració `0059_global_classification`)
- Taules globals noves, mirall de les tenant però **sense `company_id`**: `global_document_classification_runs` (`tender_id`, `classifier_version`, `input_fingerprint`, `trigger ∈ {document_processing, document_intake, operator_request}`, `document_count`, `is_current` per tender (índex parcial), `created_at`; únic `(tender_id, input_fingerprint)`), `global_document_role_suggestions` (`run_id`, `tender_document_id`, `content_sha256`, `extraction_id` NULL, `role`, `confidence`, `document_type` derivat, `signals`), `global_document_classification_findings` (`run_id`, `code`, `severity`, `tender_document_id`, `related_document_id`, `detail`).
- **Fingerprint global** = sha256(`classifier_version` + ordenat de `tender_document_id|content_sha256|name|extraction_id`) només amb fets globals (cap ruta de recepció).
- **Productor**: el worker, després de `process_tender_documents` i després de cada `document_intake` verificat; determinista, sense cost. També a petició de l'operador.
- **Revisió humana global** `document_role_reviews` (append-only, PG trigger que prohibeix UPDATE/DELETE com `review_item_revisions`): `tender_document_id`, `content_sha256`, `role`, `authority ∈ {platform_operator}` (valor `promoted_from_company` reservat, no usat a 3B-2), `actor_id` NOT NULL RESTRICT, `reason` NOT NULL, `based_on_run_id`, `based_on_suggestion_id` NULL, `source_ref_id` NULL, `supersedes_id` NULL, `created_at`. Només l'operador escriu. Propostes de rol per part d'empreses: **fora d'abast de 3B-2**.
- **Tres capes separades i mai barrejades (MUST)**: (1) *tipus de font* = `TenderDocument.document_type` (l'escriu només el connector que governa el document, o el propietari en tender manual); (2) *classificació automàtica* = `global_document_role_suggestions` (immutable, per run); (3) *rol global revisat* = `document_role_reviews` (append-only, operador). El resolutor les combina a la lectura; cap capa escriu sobre una altra. **El feed mai sobreescriu silenciosament un rol revisat**: un sync que canvia el `document_type` d'un document amb revisió vigent del mateix `content_sha256` registra l'event `SOURCE_TYPE_CHANGED_AFTER_REVIEW` (informatiu, a la revisió del tender) i la revisió continua manant. **Prova obligatòria**: una revisió de rol sobreviu intacta a una reingesta completa del feed.
- **Override tenant** = `tender_document_role_decisions` existent + columna `content_sha256` NOT NULL (backfill des del fitxer vigent): ancorat a la versió; un fitxer nou invalida la decisió («decisió d'una versió anterior»). **Mai** escriu `document_type` fora del tender propi (la guarda `_owns` es manté).
- **Resolutor** `effective_document_roles(tender_id, company_id | None) → {document_id: (role, document_type_effectiu, source)}` amb ordre: override tenant (mateix sha) > revisió global (mateix sha) > suggeriment global vigent amb confiança `high` i no duplicat > `document_type` del feed. `RoleSource` amb valors nous `operator`, `global_classifier`, `feed`. La selecció canònica d'extracció (§6) usa `company_id=None`.
- **Decisió tancada (B §5.3)**: opció A. L'extracció global només llegeix els rols globals efectius; si l'override d'una empresa diverteix, l'anàlisi v3 d'aquella empresa rep la troballa `ROLE_OVERRIDE_DIVERGES` (informativa) en lloc d'una extracció pròpia.
- `inputs_hash` de v3: substitueix `classification_run_id` (tenant) per `global_classification_run_id` + hash de les decisions tenant vigents (ordenades).
- Les taules tenant de classificació existents es mantenen per als tenders manuals propis (recepcions ZIP), sense canvi de comportament.

### 3.3 Separació immutable / correcció
L'output automàtic (run + suggeriments + troballes) és immutable i conserva `classifier_version`; la revisió és una fila nova amb actor, motiu, versió del fitxer, run de base; la UI mostra sempre «proposat pel classificador» i «revisat per l'operador (motiu)» per separat.

## 4. Estructura i abast amb provenance
### 4.1 Provenance a les entitats (migració `0060_structure_provenance`)
A `tender_blocks`, `tender_sites`, `tender_scope_items`, `requirement_capability_needs`, `tender_contract_facts`, `requirement_territories`, `requirement_award_links` (i a `requirements.timing`/`territory_*` via una taula d'anotacions, §4.3): `origin` enum NOT NULL (`extracted`, `operator`, `promoted`, `owner_manual`, `gold`; backfill: `owner_manual`, i `gold` per als tenders de font `gold:*`), `producer_run_id` NULL, `proposal_id` FK NULL, `created_by`/`updated_by` NULL, `authority` enum NULL (`platform_operator`, `tender_owner`, `promoted_from_company`, `producer`). Les cites via interna deixen de tenir `created_by=NULL` sense actor: porten `producer_run_id` o actor.

### 4.2 Propostes immutables i promoció
`tender_structure_proposals` (global): `tender_id`, `producer` ∈ {`operator`, `extraction`} (`company_proposal` reservat, fora d'abast), `producer_run_id` NULL, `entity_kind` ∈ {block, site, scope_item, capability_need, award_link, contract_fact, territory, requirement_timing, criterion_citation}, `natural_key` (codi de bloc, identitat de seu, `dedup_key`…), `payload_json` en la forma exacta del servei intern, `source_ref_ids` JSON **NOT EMPTY** (CHECK), `status ∈ {proposed, promoted, rejected}`, `promoted_by/at`, `rejected_reason`, `created_at`. Promoure = cridar la via interna (`services/tender_structure.py`, `services/tender_scope.py`) amb `origin=promoted|operator`, `proposal_id` i `authority`; una proposta sense cita no es promou mai. Operador: escriu directament (origen `operator`) o promou. Tenders manuals propis: via actual (`owner_manual`).
**Productor automàtic en 3B-2: cap perfil d'LLM d'estructura** (no existeix i és una fase pròpia, 3B-3; decisió del propietari). El que 3B-2 aporta: la via de l'operador amb cites sobre tenders públics (**MUST**: l'operador de plataforma pot mantenir estructura/abast global de tenders públics amb provenance; els usuaris d'empresa no poden mutar-la, ni en tenders públics ni via cap endpoint tenant; prova de permisos obligatòria), el marc de propostes/promoció, les anotacions que sobreviuen, els buits explícits i l'honestedat de l'anàlisi. Cap estructura «perquè sembli completa».

### 4.3 Anotacions que sobreviuen la re-extracció
Les anotacions d'estructura sobre requisits extrets (timing, blocs, territoris, necessitats, enllaços a criteris) es registren amb `requirement_dedup_key` (a més de `requirement_id`). En acceptar un run nou (§6.5), un pas de reconciliació copia les anotacions als requisits del run nou amb el mateix `dedup_key` (log `structure_annotations.carried`/`orphaned`), model `review_reconciliations`. Cap escriptura in-place sobre la fila d'extracció.

### 4.4 `unknown` i «paquet incomplet»
- Només es pot escriure un valor conegut amb ≥ 1 cita; la promoció mai coacciona `unknown` a valor; tornar a `unknown` és una operació explícita (no un delete).
- `TenderStructureView` afegeix `package_status`, `gaps[]` i `section_status` per secció (blocs/seus/abast/territoris/criteris ∈ {`unknown`, `none_declared`, `present`}); entren al canònic de l'empremta **només si presents** (precedent `submission_deadline`), i per tant el gold i les empremtes existents no canvien.
- Motor v3: `package_status = incomplete` → troballa `MISSING_INFORMATION` amb `missing_from = tender` per cada gap amb `would_provide`, i un resum de run `package_incomplete`; `section_status = unknown` → cap avaluació positiva d'aquella secció. Prerequisit d'anàlisi: hi ha requisits efectius **o** l'usuari accepta explícitament analitzar un paquet declarat incomplet (bandera a la petició, registrada al run).
- Overrides d'estructura per empresa (`company_structure_overrides`): **dissenyats, fora d'abast 3B-2** (no els cal L'Escala).

## 5. Mapa global vs tenant (vinculant)
| GLOBAL (clau d'identitat) | TENANT |
|---|---|
| `tender_documents` (tender_id, url) + origen/source ownership/verificació; bytes `tender_document_contents` (sha256) | (propostes tenant de documents i rols: fora d'abast de 3B-2) |
| extracció de text (sha256, parser_version) → extractions/pages/issues | `tender_document_role_decisions` (override, ancorat a sha256) |
| classificació automàtica global (tender, fingerprint global) + `document_role_reviews` (operador) | recepcions i pujades privades (només tenders manuals propis) |
| run de requisits (tender, corpus_fingerprint, analysis_identity, provider/model) **sense requester**; `requirements`, `requirement_evidence`, `award_criteria` | `company_tender_requirement_runs` (punter al run acceptat), `review_items`/reconciliacions, requisits manuals (tender propi) |
| estructura factual 0048–0050 amb provenance; `tender_structure_proposals`; `tender_package_gaps` | `company_structure_overrides` (fora d'abast) |
| `tender_revisions` / `tender_change_events` | quotes, `job_requests` (atribució), `tender_analysis_runs` + 6 filles, workspaces, decisions (snapshot), solució, preus, proposta, coneixement, evidències, prefit |
Regles: (1) cap run global registra empresa; el requester viu a `job_requests`/`analysis_quotes`. (2) En tender públic, un tenant no tria conjunt de documents, perfil ni `force`. (3) Tota taula global nova a `PUBLIC_TABLES` de `tenant_ops`; tota taula tenant nova amb `company_id` NOT NULL. (4) Anàlisi tenant de documents privats sobre tenders públics: **fora d'abast**, requeriria una família de runs amb `company_id` i mai el slot global `is_current`.

## 6. Extracció de requisits i LLM
### 6.1 Prerequisits de quota (calculats a `build_preview`, persistits a `analysis_quotes.prerequisites`, part de la identitat)
1. `provider_basis = real` (`AnalysisBasis.for_provider`); amb `fake`/`fixture` la quota és `allowed=false`, `blocking_reason=provider_not_real`, excepte `ENVIRONMENT=test` o bandera explícita `ALLOW_SIMULATED_ANALYSIS=true` (default false; el pilot NO la posa).
2. Cap document analitzable de tipus per defecte a `skipped_documents`; cap parell de documents seleccionats amb el mateix `content_sha256`.
3. Classificació global vigent (`input_fingerprint` = documents actuals) sense troballes HIGH obertes de `required_role_missing`, `referenced_document_missing`, `duplicate_bytes`, `duplicate_text` sobre la selecció, **o** `package_status = incomplete` acceptat explícitament per qui confirma (`accept_incomplete_package=true`, registrat a la quota i al run). Per a L'Escala el PCAP referencia DOC-03/04/05: la quota ho mostrarà i exigirà l'acceptació.
4. Qualitat del text: cap extracció seleccionada amb `qa_status ∈ {unknown, poor}`; `warning` exigeix `acknowledge_quality=true`.
5. Elegibilitat canònica: selecció per defecte (resolutor global de rols) i `force=false`.

### 6.2 Identitat i obsolescència
- `corpus_fingerprint` = l'actual (`extraction_id|revision|sha256|parser`, ordenat).
- `analysis_identity` = sha256(JSON canònic de {`fp_version`, documents ordenats `[tender_document_id, document_type_efectiu, content_sha256, extraction_id, parser_version, pages_in_plan]`, {`pages_per_chunk`, `overlap`, `max_pages`}, {`extractor_version`, `prompt_version`, `schema_version`}, `provider`, `model`}). Es desa a la quota i al run.
- **Stale** = `corpus_fingerprint` canònic actual ≠ el del run, **o** el run inclou un document retirat. `assert_inputs_are_current` llança 409 `requirements_outdated` en tots dos casos (avui només el segon). Un canvi de paquet mai re-executa res: registra revisió, marca el run com a obsolet a la lectura i exigeix una quota nova.

### 6.3 Mai vigent si no és real (3 capes)
1. Quota/handler: §6.1.1; el handler refusa `provider_basis ≠ real` fora de test.
2. Promoció a `_finalise_run` (**MUST**: només `real` + `COMPLETED` + input actual pot ser `is_current`): nova columna `run_basis` NOT NULL (`real`, `simulation`, `unconfigured`, `gold`); un run no `real` **mai** és `is_current`; un PARTIAL **mai** és `is_current` (només visible pel punter de l'empresa que el va demanar); un run el `corpus_fingerprint` del qual ja no és l'actual en finalitzar no es promou (`input_changed`); un COMPLETED **real** amb 0 requisits i 0 criteris sobre ≥ 5 pàgines analitzades es desa amb status `completed_empty` i `needs_review=true`, **no** es promou i queda explícitament marcat per a revisió humana (visible a la UI com «anàlisi acabada sense resultats: cal revisar»).
3. Dades: migració `0061_requirement_run_basis` que fa backfill de `run_basis` per `provider` i **demota** (`is_current=false`) qualsevol run vigent `fake`/`fixture`. (Al pilot avui no n'hi ha cap; verificació de només lectura abans de migrar.)

### 6.4 Selecció canònica i frontera (tanca la contaminació D §4 c, e)
En tenders públics (`owner_company_id IS NULL`): `AnalysisQuoteIn` refusa `document_ids`, `profile`, `force` del tenant (422 `public_tender_canonical_only`); la selecció és la canònica server-side (`ANALYZABLE_DOCUMENT` ∧ tipus efectius per defecte via el resolutor global amb `company_id=None`); el perfil és el de producte (`REQUIREMENTS_PRODUCT_PROFILE`, default V1; **decisió 1**: el primer run real és V1); `force`/`document_ids` només via router d'operador i aquests runs mai són canònics. `dedupe_key` dels tenders públics = `analyze:{tender}:canonical:{analysis_identity}` (cap paràmetre de tenant). **MUST**: els inputs del run global són completament canònics i independents del tenant; **prova obligatòria A/B**: dues empreses demanen l'anàlisi del mateix tender públic amb opcions diferents → mateix `dedupe_key`, un sol run, cap paràmetre de cap de les dues al run, punters independents.

### 6.5 Run global compartit + punter per empresa
`company_tender_requirement_runs` (`company_id`, `tender_id`, `run_id`, `accepted_by`, `accepted_at`, PK (company_id, tender_id)). `effective_requirements(session, tender_id, company_id)` llegeix el punter i, si no n'hi ha, el canònic global. Un run canònic nou no substitueix el que una empresa té acceptat: l'empresa l'adopta (reconciliació per empresa existent + §4.3). Qui confirma paga; les altres obtenen `REUSED_EXISTING`. v3 ja inclou `extraction_run_id` a `inputs_hash`.

### 6.6 Reintents, idempotència, fallada parcial
- **Commit per chunk** (files de requisits/evidència + `chunk_coverage` + usage) mentre el run és RUNNING i no vigent: un worker mort no perd feina pagada.
- `plan_resume` accepta runs FAILED `worker_lost`/`internal_error` amb chunks completats (avui només PARTIAL).
- Job `max_attempts=1` + reconciliació (sense canvi); `LLM_MAX_RETRIES=0` al pilot i a les proves (re-enviament només via quota de resume); `LLM_TIMEOUT_SECONDS=300`.
- `max_output_tokens` 8000 (sense canvi); `output_truncated` i `schema_invalid` continuen fallant el chunk, mai el run sencer.

### 6.7 Proposta LLM (cap crida en aquesta fase)
- **Proveïdor/model (decisió 5)**: `anthropic`, model de la família Sonnet suportat i configurat, **pinat a un model ID exacte en el moment d'autoritzar** (`LLM_MODEL` ha de ser idèntic a una entrada de `LLM_ALLOWED_MODELS`, llista buida per defecte → cap model real admès); un sol proveïdor i model idèntics a API i worker (ja verificat pel handler). Preu: **no hi ha taula al repo**; `LLM_MODEL_PRICES` s'omple a la sessió amb el full de tarifes oficial del dia i és **obligatòria** per a un proveïdor real (`price_unknown` bloqueja la quota i el handler).
- **Caps de despesa (decisió 5)**: `LLM_BUDGET_CANARY_USD=1`, `LLM_BUDGET_RUN_USD=3`, `LLM_BUDGET_PILOT_TOTAL_USD=10`. El cost real de cada chunk es calcula de l'`Usage` × preus i s'acumula al run (`estimated_cost` passa a escriure's) i al llibre `llm_spend_ledger` (global: `run_id`, `chunk_index`, `provider`, `model`, `input_tokens`, `output_tokens`, `cost_usd`, `created_at`). Abans de confirmar: cost estimat del run ≤ cap de run i acumulat + estimat ≤ cap total; abans de cada chunk: acumulat ≤ cap total; en excedir qualsevol cap el run es tanca `PARTIAL` amb `budget_exceeded` i tota quota nova queda `allowed=false` (`budget_exhausted`) fins que l'operador registra una **nova autorització** (`llm_budget_authorizations`: `cap_usd`, `scope`, `authorized_by`, `note`, `created_at`; el cap vigent és el de l'última autorització).
- **Volum per a L'Escala (PCAP+PPT)**: 2 documents, 41 pàgines, 9 chunks (7 PCAP + 2 PPT), ≈150k caràcters d'usuari + prompt V1 (2,4k car.); estimació a 3,3 car./token: V1 ≈ 70k tokens d'entrada (cota 104k) i ≈ 39k de sortida (sostre 72k); V2 ≈ 90k / 45k. Cost il·lustratiu amb hipòtesi 3/15 $ per milió: V1 ≈ 0,80 $ (cota 1,40 $); V2 ≈ 0,95 $. Temps 9–15 min.
- **Retries/idempotència/desat/fallada parcial**: §6.6; `dedupe_key` canònic; `provider_metadata.request_ids` per chunk; cap clau d'idempotència de proveïdor (no cal amb commit per chunk + resume).
- **Prova disposable primer**: restaurar un `pg_dump` del pilot a `licitacions_v3_probe_lescala_<data>` (5433), `scripts/v3_tender_probe.py` amb `--profile v1|v2|v3` (nou) i `--plan-only` (esperat: 2 docs, 41 pàgines, 9 chunks, 0 omesos, QA coneguda); canary de 2 chunks (`--chunks`, `--paid-run`, clau només a la sessió, `max_retries 0`); lectura del `chunk_report`; resume de la resta; puntuació contra el gold **només sobre ítems citats a PCAP/PPT** (sostre ≈ 31 %); informe amb codis i números; DROP de la DB.
- **Criteri explícit d'autorització de la primera crida real** (tots): (1) aixecament escrit del propietari de l'aturada pressupostària amb cap de despesa (proposta: canary ≤ 1 $, run complet ≤ 3 $); (2) destinació = DB disposable, pilot intacte amb `fake`; (3) plan-only coincideix amb els números d'aquí; (4) perfil decidit i registrat (V1 producte per defecte); (5) criteris d'avortament pactats: qualsevol error fatal, `output_truncated` o `schema_invalid` en ≥ 1 dels 2 chunks canary atura la prova; (6) run complet només si el canary completa, cada chunk amb requisits en dona ≥ 1 amb evidència validada, `evidence_rejected` < 30 % i tokens d'entrada dins ±50 % de l'estimació; (7) les capes §6.3 desplegades abans que cap run real toqui el pilot.
- **Punt exacte d'activació futura al pilot**: el clic «Inicia l'anàlisi» (confirmació de la quota) d'un owner/admin/bid_manager d'Indaleccius sobre L'Escala, amb `LLM_PROVIDER=anthropic` i clau només a la sessió d'API i worker, **després** de: 3B-2 desplegada (§6.3 inclòs), prova disposable superada, acceptació explícita del paquet incomplet (§6.1.3) i aixecament escrit. Fins llavors, `fake`, i la quota queda `allowed=false` per `provider_not_real`.

## 7. UI / UX
### 7.1 MUST 3B-2
1. **Historial**: `TenderHistoryRevisionRead.origin ∈ {source, internal, manual}` derivat de `trigger` (`ingestion`, `tombstone` → source; `document_processing`, `document_intake` → internal; `manual_create`, `manual_edit` → manual; test que falla amb un trigger fora del mapa). UI: capçalera «Com ha evolucionat l'expedient»; seccions «Publicat per la font» i «Processament intern (Tuvi)»; mai «l'òrgan» en revisions `internal`; buit «la font no n'ha modificat cap dada» només si no hi ha revisions `source`.
2. **Objecte**: `normalize()` desa `description=None` si el `summary` és la plantilla («Id licitación…»); text cru a `source_metadata.feed_summary` (clau tancada nova); backfill dels existents (patró `search_backfill`); UI: si no hi ha descripció, «Objecte no publicat al feed» (mai el títol com a objecte inventat). Llegir `ProcurementProject/Description`: deute posterior.
3. **«0 de 0 requisits»**: `DossierCoverageRead.analysis_state ∈ {absent, current, stale}` i `mandatory_total: int | null` quan `absent`; blocador explícit `analysis_missing` al readiness (mai «Dossier complet» sense anàlisi); UI als 5 llocs: «Encara no hi ha cap anàlisi del plec» amb CTA.
4. **Pre-fit**: copy condicionada al perfil (`cpv_preferences`/territoris/contracte): sense perfil → «El pre-fit només es calcula quan el perfil té CPV, territoris o preferències de contracte»; amb perfil → «Encara no s'ha calculat» + botó «Calcula'l ara» (`POST /matches/recalculate?tender_id=`); eliminar «per a totes» també a Oportunitats.
5. **Plecs**: badge d'origen (`feed` / `aportat per l'operador` / `pujat per l'operador`) i d'estat de contribució (`pendent de verificació` / `verificat` / `rebutjat` / `àlies de…`); «Retirat de la font (des del …)» (declarar `removed_at` al tipus web); capçalera «N de M documents analitzables llegits» i avís «paquet incomplet: falten …» (gaps) abans de la quota; `AnalysisQuotePanel` mostra `provider_basis`, prerequisits i `blocking_reason` amb text humà; «Inicia l'anàlisi» desactivat si `allowed=false`; per a l'operador, pantalla `/plataforma/licitacions/{id}/documents` (afegir per URL, pujar, verificar, retirar, revisar rol amb motiu).
6. **Preparar pre-GO = visible / només lectura (decisió 4, MUST)**: el pas es pot obrir i llegir sense GO, però **tota mutació o generació** del pas Preparar (dossier, solució tècnica, oferta econòmica, paquet de presentació, tasques i notes de preparació) exigeix una decisió humana GO vigent, amb **guard backend** `assert_workspace_go(workspace)` (403 `decision_required`) a cada endpoint mutador, i la UI en mode lectura amb el copy «Es pot consultar; s'obre a edició amb GO».
### 7.2 SHOULD
7. Dashboard: «candidates» en lloc d'«oportunitats noves», amb `total_tenders` i causa del 0.
### 7.3 Deute posterior
Definició temporal de «nova» (`first_seen_at`), prefit lazy al detall, `ProcurementProject/Description`, descàrrega de la còpia emmagatzemada, auditoria de descàrregues.

## 8. Cas d'acceptació E2E de L'Escala (definit, NO executat)
Precondicions: 3B-2 desplegada al pilot; `fake` fins al pas 6; oracle només per comparar.
1. Tender real 2026/3964 visible a «Totes» (ja).
2. Paquet públic complet: l'operador afegeix els documents oficials que faltin des del perfil del contractant (URL o pujada) amb `contribution_source_url`; cap document de l'oracle copiat.
3. Verificació: cada document aportat passa a `verified`; **criteri**: SHA256 de cada contingut = SHA de l'oracle (9 esperats; el DOC-04 fals no hi és); diferències (un fitxer no publicat, un SHA diferent) es registren com a gap `declared_by_operator` o com a diferència explicada, mai es força.
4. Classificació global: run vigent amb rols `pcap`, `ppt`, `characteristics_table` (DOC-03), `inventory_annex` (DOC-04), `offer_model` (DOC-05), `justification_report` (DOC-01), `approval_resolution` (acord); comparació amb `lescala-2026.gold.json` (rols, confiança, `needs_review`); cap `document_type` del feed modificat; `tender_package_gaps` buit o explicat.
5. Estructura/abast: només el que l'operador escrigui amb cites (o propostes promogudes); comparació amb `lescala-2026.tender-gold.json` per entitat (blocs 4, seus 25, abast 25, criteris 28, requisits 34, territoris 64, fets 16) **com a informe de diferències**, no com a objectiu d'omplir; `unknown` es manté on no hi ha cita.
6. Extracció real (**punt d'activació §6.7**): quota amb prerequisits verds (o paquet incomplet acceptat), confirmació, run `real` canònic; mètriques: chunks, tokens, cost, `evidence_rejected`, requisits per document.
7. Anàlisi v3 (pur): run per a Indaleccius sobre el run acceptat; `package_status` i gaps reflectits.
8. Comparació amb l'oracle: cobertura de cites per document, requisits trobats/esperats per `dedup_key`, criteris (punts 100), seus/abast coincidents; diferències classificades (`not_in_source_docs`, `extraction_missed`, `structure_not_authored`, `oracle_outdated`).
9. Diferències explicades, mai forçades: cap edició per quadrar amb l'oracle.
10. Workspace/decisió human-first: cap decisió automàtica; Decidir mostra l'anàlisi amb els buits; `analysis_state` honest.

## 9. Migracions
`0058_public_documents`, `0059_global_classification`, `0060_structure_provenance`, `0061_requirement_run_basis` (totes additives i reversibles; downgrade de 0058 refusa si hi ha documents `verified` o àlies; downgrade de 0061 refusa si hi ha runs `completed_empty`). `tenant_ops.PUBLIC_TABLES` ampliat; test que falla si una taula nova no està classificada.

## 10. Tests i fixtures
Fixture de tender públic (font PLACSP-like, `owner_company_id NULL`) amb 2 documents i gold sintètic de classificació des de tender públic (no només via ZIP de tender manual); tests `tender_not_manual` als endpoints de classificació tenant; cas A/B d'empreses sobre el mateix tender públic (cap paràmetre d'A arriba al run; punters independents); run fake mai vigent; `completed_empty`; 409 per corpus canviat; `_sync_documents` no retira documents d'operador; allowlist; àlies per sha; reconciliació d'anotacions per `dedup_key`; historial amb `origin`; Objecte plantilla; `analysis_state`.

## 11. Riscos
Volum de migracions (4) i de superfície (operador nou); dependència d'un criteri de producte per a «Preparar»; el paquet complet de L'Escala depèn d'una acció manual de l'operador; cost real desconegut fins al full de tarifes; la cobertura amb només PCAP+PPT és ≈ 31 % del gold.

## 12. Fora d'abast
Perfil d'LLM que produeixi estructura (3B-3); overrides d'estructura per empresa; anàlisi tenant de documents privats sobre tenders públics; client PSCP que enumeri paquets (spike posterior); sync recurrent, rànquing, coneixement d'empresa; OCR; descàrrega de ZIP expandida en tenders públics.

## 13. Decisions del propietari (tancades el 2026-10-07)
1. Primer run real = **V1**, perfil de producte actual.
2. **Cap run real de L'Escala només amb PCAP+PPT**: esperar com a mínim DOC-03/04/05 públics oficials verificats; idealment el paquet oficial complet.
3. Workflow de propostes tenant **fora** de 3B-2; 3B-2 inclou incorporació/promoció per operador de plataforma de documents públics oficials absents del feed.
4. Preparar pre-GO = visible/read-only; tota mutació/generació requereix GO humà amb guard backend.
5. Primer LLM real: Sonnet suportat/configurat, pinat a model ID exacte en autoritzar; caps canary ≤ 1 $, run L'Escala ≤ 3 $, validació pilot acumulada ≤ 10 $; cap excedit = bloqueig fins nova autorització.
Fora d'abast confirmat: propostes tenant, LLM d'estructura, documents privats de tenant dins runs globals, rànquing Radar, recurrència 1044, activació d'LLM real durant la implementació.
