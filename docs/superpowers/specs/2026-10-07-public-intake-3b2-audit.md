# Phase 3B-2 — Auditoria consolidada: tender públic → intake de resolució

Base: pilot `507ac57` (DB 0057) amb L'Escala real (2026/3964) ingerida via `placsp_agregadas`, PCAP + PPT descarregats i extrets (SHA = oracle), 1 workspace d'Indaleccius, 0 requisits, 0 runs, 0 decisions, `LLM_PROVIDER=fake`. Només lectura; cap dada tocada. Detall amb `fitxer:línia` a `.superpowers/sdd/2026-10-07-phase3b2/audit/{A-documents,B-classification-structure,C-requirements-llm,D-global-vs-tenant,E-web-ux}.md` (worktree `Licitacions-intake`, no versionat).

## 1. Què existeix i serveix
- **Bytes globals adreçats per contingut**: `tender_document_contents` únic per `sha256`, sense `company_id`, storage `tenders/documents/aa/bb/<sha>.<ext>`; `get_or_create_content` dedueix. Extraccions, pàgines i issues pengen del contingut (una vigent per contingut). (A §1.3–1.6)
- **Versions de fitxer**: `tender_document_files` amb `origin fetched|uploaded`, `uploaded_by`, `source_url/final_url`, `http_status`, `last_checked_at`, lock d'un sol fitxer en vol per document. (A §1.2)
- **Descarregador** sòlid: SSRF per salt, 5 redireccions, 60 MB, sniffing de magic bytes, HTML refusat (`invalid_content`), ZIP opac. Sense allowlist d'amfitrions. (A §2.4)
- **Processament** idempotent (`process_tender_documents`): mateix SHA = no-op; bytes nous = nova versió; extracció immutable per revisió; `ACTIVE_DOCUMENT` (`removed_at IS NULL`) és el filtre únic de tots els lectors. (A §2.5)
- **Classificador determinista** `document-roles-v1` (pur, sense sessió): rol, confiança, senyals, troballes de conjunt (`required_role_missing`, `referenced_document_missing`, duplicats…). (B §2.2)
- **Estructura/abast 0048–0050** amb semàntica `unknown` honesta (timing, `is_critical`, `separately_awardable`, `territory_completeness`), cites immutables `tender_source_refs` ancorades a fitxer/pàgina amb `content_sha256`, downgrade que refusa. (B §3)
- **Motor v3 pur** (cap import d'LLM), run per (empresa, licitació) amb `inputs_hash` de 7 entrades; 6 taules filles sense `company_id` pengen del run. (D §3)
- **Extracció de requisits** global (sense `company_id`, «el plec s'analitza una vegada»), quota + confirmació humana, un sol lloc de crida a l'LLM, lease/heartbeat, cobertura per chunk, evidència validada obligatòria. (C §1–3)
- **Patrons reutilitzables**: `SourceRecord` immutable, `record_revision(trigger)`, `audit.record(company_id=None)`, `knowledge_observations` (candidat → promogut), `workspace_decisions` (immutable, actor obligatori), `review_item_revisions` (append-only), `tenant_ops` (classifica totes les taules i falla amb les no classificades), `v3_tender_probe.py` (DB disposable obligatòria). (A §5, B §5, C §4, D §2)

## 2. Què falta o és defectuós (per bloc)
### Paquet documental públic
- `TenderDocument` no té origen lògic, aportador, verificació ni àlies; identitat = `(tender_id, url)`; `upload://<uuid>` per a pujades. (A §1.1)
- **Risc directe**: `_sync_documents` retira (`removed_at`) tot document del tender amb URL absent del feed, sense mirar l'origen, i en restaurar sobreescriu nom/tipus. Un document oficial aportat seria retirat a la següent publicació completa. (A §2.1)
- Cap via d'escriptura global: pujada/ZIP només en tender MANUAL propi; l'operador de plataforma no entra a `/companies/*`; `/platform` només onboarding i salut de fonts. Cap client PSCP; la pàgina de detall és una SPA (HTML buit, `portal-api` 401). (A §2.2–2.3, §4 f–g)
- Dedup només de bytes: dues URL amb els mateixos bytes = dos documents lògics. Cap «completesa de paquet». (A §4 d–e)
- Afegir un document públic és `DOCUMENT_ADDED` (`REQUIREMENTS_AFFECTING`) i canvia les empremtes de tothom. (A §4)

### Classificació
- 100 % per empresa (`company_id` NOT NULL a les 4 taules); l'únic efecte global és `TenderDocument.document_type`, només en tenders propis; l'extracció llegeix els rols **només** per aquesta columna, que el feed sobreescriu a cada sync i que és dins la foto de revisions. (B §2.3–2.4)
- Fingerprint de classificació inclou la ruta de recepció de l'empresa (depèn del tenant). Decisions de rol lligades al document, no a la versió de fitxer. Troballes de classificació no arriben a cap consumidor. Cap test que els endpoints refusin tender públic. (B §2, §5.0, §5.6)

### Estructura i abast
- Cap productor automàtic: blocs, seus, abast, necessitats, enllaços a criteris, fets, territoris, timing, cites de criteris són 100 % manuals (routers de tender manual propi + carregador del gold). Cap perfil d'extracció (V1/V2/V3.2) en produeix. (B §3.7, §4)
- Files d'entitat sense provenance (ni origen, ni run, ni autor, ni autoritat); les anotacions d'estructura pengen de `requirement_id` i es perden en re-extreure; la via interna escriu cites amb `created_by=NULL`. (B §3.2, §5.0 C3, §5.6)
- Un conjunt buit de blocs/seus/abast és indistingible de «el plec no en té»; `scope_unknown` només existeix al costat empresa. El gold té `missing_documents` (would_provide/consequence) que el carregador ignora. (B §3.3, §3.7)
- v3: `review_revision_hash` llegeix els camps legacy globals, no la capa de revisió per empresa; cap criteri manual; dos «és manual» divergents. (B §5.6)

### Frontera global vs tenant
- 122 taules: 9 global-font, 30 global-derivades, 9 plataforma, 68 tenant, 6 mixtes (classificació i recepcions amb `company_id` tot i derivar de documents públics). (D §1)
- **Contaminació REAL avui**: `requirement_extraction_runs` té un sol `is_current` per tender sense requester; `document_ids`, `profile`, `force` i `resume` vénen de la quota d'una empresa; el dedupe global de `work_jobs` enllaça l'empresa B al job d'A amb els paràmetres d'A. Un `bid_manager` d'A pot substituir els requisits que veu tothom en un tender públic. `force_download`/`force_reextract` creen revisions globals (cost). (D §4 c, e)
- No possibles: pujada privada en públic, decisions de rol a l'extracció, revisions amb dades d'empresa. (D §4 a, b, d)

### Extracció de requisits i LLM
- **Un run fake que no troba res esdevé vigent** (COMPLETED, 0 requisits, `is_current`); un PARTIAL també es promou; la guarda fake/fixture només actua amb `ENVIRONMENT=prod`; `AnalysisBasis` només afecta la UI. (C §0.1–0.2, §3)
- Quotes sempre V1 (sense camp `profile`); fingerprint = `extraction_id|revision|sha256|parser` (sense rol, perfil, model); 409 `requirements_outdated` només per documents retirats, no per contingut canviat. (C §0.3, §1, §3)
- Feina pagada es perd si el worker mor (commit només a `_finalise_run`); resume només des de PARTIAL. Cap taula de preus; la primera quota real dirà «Sense històric per estimar». (C §0.6–0.7)
- L'Escala amb PCAP+PPT: 9 chunks (7+2), ≈150k car. d'usuari, V1 ≈70k tokens d'entrada / ≈39k de sortida (3,3 car./token), cost il·lustratiu ≈0,80 $ (cota 1,40 $) a 3/15 $ per milió, 9–15 min; sostre de cobertura ≈31 % de les cites del gold (DOC-03/04/05 no són a la font). (C §5)

### UX (smoke real)
1. Historial: capçalera fixa «Què ha modificat l'òrgan…» sobre revisions `document_processing` internes; `trigger` és `str` lliure i `triggerLabel` només en cobreix 2. **MUST.**
2. «Objecte» = `summary` de l'Atom (plantilla fixa «Id licitación… Órgano… Importe… Estado…»), ja detectada a `search.py:111`. **MUST.**
3. «0 de 0 requisits obligatoris» al dossier (i 4 llocs més) sense anàlisi; cap blocador explícit `analysis_missing`. **MUST.**
4. Preparar navegable abans de GO (stepper amb enllaços lliures; copy «s'obre amb GO»). SHOULD (decisió de producte).
5. «0 oportunitats noves» = matches `candidate` obertes, sense finestra temporal; 17k licitacions. SHOULD.
6. Copy del pre-fit «es calcula per a totes» fals: només a la ingesta i només amb preferències legacy (CPV/territori/contracte). **MUST (copy)** + recalc per licitació (API existent).
7. Plecs: cap estat «retirat» a la UI (`removed_at` no declarat al tipus), cap «N de M llegits», cap avís de paquet incomplet; «Inicia l'anàlisi» és el botó que confirma la despesa i no mostra base del proveïdor. **MUST (estats honestos).**

## 3. Conclusió
El pont existeix fins a «bytes + text + workspace». Falten quatre peces de model (origen/verificació de documents públics, classificació global amb revisió autoritzada, provenance i propostes d'estructura amb buits explícits, i base/identitat/punter per empresa dels runs de requisits), una regla de frontera (cap paràmetre de tenant a un run global) i les correccions d'UX. L'única crida a l'LLM continua sent la confirmació d'una quota; el punt d'activació es fixa a la spec.
