# Sprint 3 — Ingesta manual i configuració (Design Freeze)

Data: 2026-09-21
Estat: **disseny congelat**. Cap model, cap migració i cap endpoint implementats.
Font de veritat d'Sprint 3.

---

## 0. Per què existeix aquest document

Sprint 2 va deixar el motor de l'oferta econòmica complet i correcte. El benchmark
de Palamós 2024 va demostrar que el producte **no hi pot arribar**: la licitació no
hi pot entrar, els documents que apareixen durant la licitació no tenen on anar, i
les decisions que precedeixen el preu —quin proveïdor, quina quantitat quan les
fonts no coincideixen, quins dubtes queden oberts— no tenen representació.

Aquest document fixa l'arquitectura d'Sprint 3 perquè la implementació no hagi de
tornar a obrir cap decisió de producte. Les seccions **D-01 … D-09** són decisions
literals i no es reinterpreten. Tota la resta és enginyeria derivada, i això sí que
es pot discutir sense reobrir la decisió.

Sprint 3 **no és «fer que Palamós funcioni»**: és generalitzar els workflows que
Palamós va demostrar. **El codi de producció no conté cap dada de Palamós.**

---

## 1. Goal

> Que una licitació real pugui entrar manualment amb els seus documents, créixer
> amb documents posteriors, i recórrer el camí fins a una oferta econòmica
> **reproduïble**, amb les decisions intermèdies registrades i atribuïbles a una
> persona.

---

## 2. Evidence from Palamós

Cada capacitat d'Sprint 3 té una evidència dels tres benchmarks congelats
(`var/benchmarks/PALAMOS-2024-{BLIND-GOLD,REALITY,OUTCOME}-v1.md`).

| Evidència | Capacitat que exigeix |
|---|---|
| La licitació va entrar per una alerta comercial de correu, no per PLACSP | Ingesta manual |
| Un document de dimensionament es va publicar **durant** la licitació i la va canviar | Documents sobrevinguts + staleness |
| 4 divergències de dimensionament detectades a mà; 14 no vistes | Dimensions multi-font |
| Es van costejar 181 línies mòbils i se'n van desplegar 151 | Dimensions multi-font (impacte econòmic provat) |
| Un requisit obligatori no costejat va acabar en excepció contractual escrita | Readiness sobre dependències vives |
| L'operador d'accés i el mòbil van canviar entre preparació i execució | Cotitzacions amb estat i data |
| Proveïdors pendents, seleccionats i descartats vivien en columnes lliures | `SolutionOption` amb `quote_status` i `selection` |
| 12 multiplicadors comercials escrits a mà, `preu = cost × factor` | Factor objectiu com a **entrada** |
| 15 dubtes en text lliure; 4 encara oberts en presentar | Issues amb cicle de vida |
| El preu preparat va arribar intacte a l'adjudicació | El motor econòmic **no es toca** |

---

## 3. Scope (IN)

Tenancy de licitacions privades i tancament de la fuita de matching · creació i
edició manual de licitacions amb `TenderRevision` i CAS · camí real a Oportunitats
via `CompanyTenderMatch` · pujada manual de documents amb contracte de formats
explícit · versions i documents sobrevinguts amb staleness separada ·
`tender_source_refs` (provinença tender-level amb FK reals) · requisits manuals
sense LLM de pagament · dimensions multi-font · `SolutionOption` amb cotitzacions i
redacció econòmica estricta · dependències opció↔dimensió · opció→línia idempotent ·
`target_markup_pct` + `sync-from-option` explícita · issues amb blocatge humà ·
findings nous de FinalReadiness · oracle econòmic independent.

## 4. Non-goals (OUT)

Criteris d'adjudicació, scoring i anormalitat (tornen amb manifest v3) · manifest v3 ·
migració `Float→Numeric` d'`award_criteria` · extracció de text de DOCX, XLSX i HTML ·
OCR · deduplicació automàtica amb PLACSP · escenaris de preu · master/CRM de
proveïdors · compartició de licitacions manuals · presentació automàtica d'ofertes ·
probabilitat de victòria · historial complet de `Requirement` · integracions de correu
o ERP.

---

## 5. Human-in-the-loop invariants

| Invariant | Com es fa complir |
|---|---|
| `unknown ≠ no` | `OptionSelection.undecided` ≠ `rejected`; `DimensionStatus.undecided` ≠ `decided` |
| `partial ≠ complete` | Els findings distingeixen «desconegut» de «correcte»; mai s'assumeix zero |
| recomanació ≠ decisió | `sync-from-option` i `apply-target` són accions explícites; mai recàlcul implícit |
| requisit sense solució és neutral | **No es toca.** Cap opció ni cap issue es crea automàticament per requisit |
| opció seleccionada ≠ solució validada | `selection=selected` no altera `TechnicalSolutionStatus` |
| document seleccionat ≠ evidència acreditada | El dossier no canvia |
| **la IA mai presenta** | Cap endpoint nou envia res enlloc |
| cap càlcul econòmic toca un LLM | `pricing.py` i els serveis nous són deterministes i purs |

**Decideix la persona:** el valor de treball quan les fonts discrepen · el proveïdor
i el producte · el factor comercial · què bloqueja la revisió final · el GO/NO-GO ·
el freeze · l'aprovació.

---

## 6. Manual Tender identity — invariant tancada

### Camps reals

| Camp | Tipus | Rol |
|---|---|---|
| `tenders.id` | `UUID` PK, `default=uuid.uuid4` | **Identitat interna canònica.** Vegeu la nota de sota |
| `tenders.owner_company_id` | `UUID NULL` FK companies CASCADE | **Boundary de tenancy.** `NULL` ⇔ pública/global |
| `tenders.source_id` | `UUID NOT NULL` FK sources | La `Source` **MANUAL global i única** |
| `tenders.external_id` | `String(500) NOT NULL` | Identificador sintètic immutable |
| `tenders.expediente_number` | `String(200) NULL` | **El número d'expedient que escriu la persona.** El model ja documenta que **no és únic**: dos organismes poden compartir-lo |

### Format de l'`external_id` manual

```
external_id           = "manual:<company_id>:<client_token>"        (Tender)
external_identifier   = "manual:<company_id>:<buyer_client_token>"  (Buyer)
```

**El prefix d'empresa no és decoratiu.** La `Source` MANUAL és una i global, i
amb ella ho és `UNIQUE(source_id, external_id)`. Sense el prefix, que l'empresa A
fes servir un token impediria a B fer servir el mateix, i l'espai de claus d'una
limitaria el de l'altra. La clau d'idempotència conceptual és
**`(company_id, client_token)`**, i així s'escriu. 80 caràcters, dins de
`tenders.external_id` (500) i de `buyers.external_identifier` (200).

Coherent amb la convenció existent: PLACSP fa servir l'`<id>` d'ATOM, un
identificador **opac i natiu de la font**. `manual:<uuid>` és el mateix patró per a
la font manual. Longitud 43 caràcters, molt per sota de `String(500)`.

> **On es genera cada UUID, exactament.** Són dos i no s'han de confondre.
>
> `UUIDPrimaryKeyMixin` declara `default=uuid.uuid4`. En vocabulari de SQLAlchemy
> això és un default *client-side*, on «client» vol dir **el procés Python de
> l'API** — ni el navegador ni el servidor SQL — i s'avalua **en el moment de
> l'INSERT**, no en construir l'objecte. Verificat: `Tender(...).id` és `None`
> fins al `flush()`, i la columna no té `server_default`.
>
> D'aquí que `external_id` **no es derivi de `Tender.id`**: quan cal escriure'l,
> l'id encara no existeix. El **`client_token` sí que el genera el navegador**,
> una sola vegada en obrir l'assistent d'importació, i es manté estable durant
> tots els reintents de la mateixa creació. És això el que fa idempotent el
> reintent, no el PK.

| Qüestió | Resposta |
|---|---|
| **Qui el genera** | **El servidor, sempre.** Mai l'usuari. Si el cos porta `client_token` (UUID), s'hi fa servir; si no, `uuid.uuid4()` |
| **És editable** | **No.** `source_id` i `external_id` són immutables després de la creació. `expediente_number` **sí** que és editable |
| **Constraint final** | `UNIQUE(source_id, external_id)` **sense cap canvi**. No cal unicitat tenant-aware |
| **Comportament al POST** | `201` amb la licitació creada, `owner_company_id` = l'empresa del path, `source_id` = Source MANUAL global |
| **Retry / idempotency** | L'assistent genera un `client_token` en obrir el formulari i l'envia a cada intent. Un reintent col·lideix amb `UNIQUE(source_id, external_id)`; el servei captura la col·lisió i retorna **`200`** amb la licitació existent si és de la mateixa empresa, o **`409 tender_external_id_conflict`** si és d'una altra. Sense `client_token`, un reintent crea un esborrany separat, visible i arxivable |

### Organismes de contractació — delta descobert a S3-S1

`Buyer` no tenia tenancy. Com que el wizard ha de poder escriure un organisme que
cap font sindicada no porta, **`buyers.owner_company_id` segueix exactament la
mateixa regla que `tenders.owner_company_id`**: `NULL` és públic i canònic, amb
valor és privat de l'empresa que el va escriure. Una importació privada **no pot
publicar en silenci** al catàleg global.

| Regla | |
|---|---|
| Identitat | `UNIQUE(source_id, external_identifier)`; el manual és `manual:<company_id>:<buyer_client_token>`, amb un token **propi i diferent del de la licitació** |
| Reintent amb el mateix token | Material = `name`, `tax_identifier`, `nuts_code`. Igual → **es reutilitza**. Diferent → **`409 manual_buyer_payload_conflict`**. Com que no hi ha PATCH de `Buyer` a Sprint 3, acceptar-ho seria triar en silenci quina versió es queda |
| Lectura | `GET /companies/{c}/buyers` i `/buyers/{buyer_id}`, **company-scoped**. Les rutes globals `/buyers*` **s'eliminen**: no tenien cap consumidor i filtraven malament |
| Reutilitzar-ne un | Global o de la mateixa empresa. D'una altra: **404** |
| Modificar-ne un | **Mai** des del wizard. No hi ha PATCH de `Buyer` a Sprint 3 |
| `buyer_id` i `buyer_manual` | **Com a molt un** (`at most one`, no `exactly one`): cap dels dos és vàlid, un dels dos és vàlid, tots dos donen `422 buyer_choice_ambiguous`. Intencionat: *unknown ≠ no* |
| Selecció a la UI | **Company-scoped**, mai una ruta user-global que barregi privats de dues empreses del mateix usuari |
| Esborrat | `ON DELETE CASCADE`, auditat: `tenders.buyer_id` ja va amb `SET NULL`, així que no queda cap licitació impossible, i `NULL` no coincideix mai amb l'empresa esborrada |
| Deduplicació | **Cap.** Ni fuzzy matching de noms ni automatismes |

**Migració 0028 s'amplia a «manual ingestion support»** — organismes privats **i**
el fonament d'esquema de pujada de documents (`source_url` nul·lable, `origin`,
`uploaded_by`) — per no haver de crear una revisió extra i **no renumerar 0029–0037**.

### Per què queda resolt

Dues empreses que importen l'expedient `2024-3137` obtenen `external_id` diferents
(`manual:<uuid-A>`, `manual:<uuid-B>`), el mateix `expediente_number`, i
`owner_company_id` diferents. **Cap col·lisió, cap unicitat tenant-aware, cap
`Source` per empresa.** La tenancy viu a `Tender`; la `Source` continua sent
**provinença pura**.

---

## 7. Tenant isolation

- `Tender.owner_company_id NULL` ⇔ pública. Font MANUAL ⇒ `owner_company_id NOT NULL`.
- Un únic helper **`visible_tenders(company_id)`** obligatori a **totes** les consultes:
  `GET /tenders`, `/tenders/facets`, `/tenders/{id}`, `/tenders/{id}/documents*`,
  `/buyers*`, `/companies/{c}/matches*`, `/matches/recalculate`, lectura d'anàlisi de
  requisits i `job-requests`.
- **`MatchingService._tenders()` fa avui `select(Tender)` sense cap filtre d'empresa.**
  És una fuita entre tenants **ja existent**, i es tanca a 0027. No és opcional.
- Esborrar una empresa: `ON DELETE CASCADE` sobre `owner_company_id` esborra només
  les seves licitacions privades. Les globals no s'hi veuen afectades.
- `TenderCompanyAccess` queda documentat com el camí d'ampliació si mai s'obre la
  compartició (D-04: no a V1).

---

## 8. Oportunitats / Match path

Camí real verificat: la pàgina `/licitacions` (etiquetada «Oportunitats») llegeix
`/companies/{c}/matches`, **no `/tenders`**.

> Una licitació creada **no apareix sola** a Oportunitats. El feed llegeix
> `CompanyTenderMatch`.

**Disseny:** `POST /companies/{c}/tenders` executa **síncronament**
`MatchingService.recalculate(company_id, tender_ids=[nou_id])`. La signatura ja
accepta `tender_ids`; és una sola licitació, sense fan-out. Si el pre-fit surt
`insufficient_data` —probable en una licitació acabada d'escriure— apareix al mode
**«Sense dades suficients»**, que ja existeix. Apareix, i honestament etiquetada.

---

## 9. Manual document lifecycle

Es reutilitza **íntegrament** el pipeline existent:
`TenderDocument` → `TenderDocumentFile` (versions, `is_current`) →
`TenderDocumentContent` (**dedup per SHA-256**, storage) → `TenderDocumentExtraction`
→ `TenderDocumentPage`.

Pujar és crear un `TenderDocumentFile` amb `origin=uploaded`, `source_url=NULL` i
`uploaded_by`. El contingut es dedupica pel `sha256` que ja existeix. L'original es
preserva sempre: `TenderDocumentContent` és immutable per contracte.

Un **document sobrevingut** és un `TenderDocument` nou, o una versió nova d'un
existent, amb `created_at` posterior a la creació de la candidatura.

---

## 10. Format support contract

> **Pujar ≠ analitzable.** Cada document exposa
> `text_extraction ∈ {supported, unsupported, pending, failed}` i el frontend l'ha de
> mostrar. Un XLSX pujat és **preservat i citable**, mai «analitzat».

| Format | Upload | Guardat | Text | Pàgines | Requisits | OCR |
|---|---|---|---|---|---|---|
| PDF amb text | ✅ | ✅ | ✅ `pypdf` | ✅ | ✅ | n/a |
| PDF escanejat | ✅ | ✅ | ⚠️ `low_text`, `ocr_candidate` | ✅ buides | ❌ | ❌ |
| DOCX · DOC | ✅ | ✅ | ❌ `skipped_unsupported` | ❌ | ❌ | ❌ |
| XLSX · XLS | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| XML · ZIP | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| TXT | ✅ | ✅ | ✅ **(s'afegeix a Sprint 3, zero dependències)** | ✅ 1 | ✅ | n/a |
| HTML · PPT/PPTX | ❌ no admès com a document de licitació | — | — | — | — | — |

`DOCUMENT_ALLOWED_MIME_TYPES` (documents de licitació) i `ALLOWED_UPLOAD_MIME_TYPES`
(evidència d'empresa) són **llistes diferents** i no s'han de confondre.

---

## 11. Document review / staleness

Tres eixos **separats**. `technical_scope_fingerprint` conté `lot_mode`,
`tender_lot_ids`, `requirements` i `selected_requirement_ids` — **no conté documents**,
i per tant un document nou no el pot tocar.

| Eix | Mecanisme |
|---|---|
| Conjunt documental revisat | `bid_workspaces.documents_reviewed_at` + `documents_reviewed_fingerprint` = SHA-256 sobre `(tender_document_id, current_file_id, content_sha256)` ordenats |
| Anàlisi vigent/obsoleta | `RequirementExtractionRun.is_current` + `input_fingerprint` (existent) |
| Abast tècnic | `technical_scope_stale` (existent, **sense canvis**) |

**Regla dura:** un document nou **mai** invalida una `TechnicalSolution` validada ni
un paquet congelat. Només diu que ha arribat material. Si la persona reextreu i els
requisits canvien, `technical_scope_stale` s'encén **pel mecanisme que ja existeix**.

---

## 12. Tender-level provenance

`Requirement` és **tender-level** i pot existir sense `BidWorkspace`. La provinença
també ho ha de ser.

### `tender_source_refs`

| Camp | Tipus | Null | Nota |
|---|---|---|---|
| `tender_id` | FK tenders **CASCADE** | no | índex. **Cap `company_id`**: hereta la visibilitat de la licitació |
| `ref_kind` | enum `{extracted_page, document_file}` | no | |
| `extraction_id` | FK tender_document_extractions **RESTRICT** | sí | obligatori si `extracted_page` |
| `page_id` | FK tender_document_pages **RESTRICT** | sí | obligatori si `extracted_page` |
| `page_number` | Integer | sí | CHECK ≥ 1 quan no és null |
| `tender_document_file_id` | FK tender_document_files **RESTRICT** | sí | obligatori si `document_file`; porta `version_number` i `content_id` |
| `tender_document_id` | FK **SET NULL** | sí | només per mostrar |
| `content_sha256` | String(64) | **no** | desnormalitzat: els bytes exactes sempre identificables |
| `location_label` | String(200) | sí | «p. 27», «full Càlculs, fila 42» |
| `quote` | Text | sí | |
| `char_start` · `char_end` | Integer | sí | només `extracted_page` |
| `created_by` · `created_at` | | | |

`CHECK`: `(ref_kind='extracted_page' AND extraction_id IS NOT NULL AND page_id IS NOT NULL) OR (ref_kind='document_file' AND tender_document_file_id IS NOT NULL)`

Consumidors amb integritat referencial, **sense JSON d'UUIDs**:
`requirement_source_ref_links` (N:N) · `workspace_dimension_observations.source_ref_id`
(1, SET NULL) · `workspace_issue_source_ref_links` (N:N).

**`RequirementEvidence` no es toca.** Continua sent la provinença dels requisits
extrets, amb `char_start`/`char_end` calculats pel backend i àncores `RESTRICT`. El
read model projecta **una sola llista `citations[]`** que fusiona les dues formes:
un vocabulari a l'API, dos emmagatzematges amb garanties diferents, zero risc per a
Sprint 2.

---

## 13. Manual Requirement semantics

### Per què `RequirementExtractionRun` no serveix

Modela una **execució d'anàlisi**: `provider`, `model`, `prompt_version`,
`extractor_version`, tokens, cost, `chunk_coverage`. Una persona que escriu un
requisit no en té cap. `input_fingerprint` és, verificat al codi,
`SHA-256(sorted("{extraction_id}|{revision_number}|{content_sha256}|{parser_version}"))`
— **identitat del corpus d'entrada d'una extracció real**. Omplir-lo per un requisit
manual el falsejaria.

### Alternativa mínima

```
Requirement.run_id      → NULLABLE
Requirement.origin      → enum {extracted, manual} NOT NULL default 'extracted'
Requirement.archived_at → TZ datetime NULL
Requirement             → hereta ActorMixin (created_by, updated_by)

CHECK: (origin='extracted' AND run_id IS NOT NULL)
    OR (origin='manual'    AND run_id IS NULL)
```

| Qüestió | Resolució |
|---|---|
| Historial | Els extrets, per run. Els manuals: `created_by`, `updated_by`, `updated_at` i els camps de revisió. **D-08: això NO és historial complet de versions** |
| Edició | `PATCH /requirements/{id}` només si `origin='manual'` → `403 requirement_not_manual` |
| Current | Els manuals no tenen `is_current`: són efectius fins que s'arxiven |
| Archive / delete | **Només arxivat.** Esborrar és impossible: `requirement_source_ref_links.source_ref_id` és RESTRICT |
| Effective set | `requirements(run_id = current_run.id)` ∪ `requirements(tender_id, origin='manual', archived_at IS NULL)` |
| `technical_scope_fingerprint` | **Signatura intacta.** Se li passa la unió. Crear, editar o arxivar un manual canvia el fingerprint → `technical_scope_stale` s'encén sol |
| `is_stale` del router | **Intacte.** Descriu només la run extreta. Una afirmació humana no caduca perquè s'hagi reextret un PDF |

---

## 14. Dimensions

`workspace_dimensions`: `key` (slug lliure, **sense ontologia**), `label`, `unit`,
`scope_label`, `working_value_text`, `working_value_num` (`Numeric(18,4)`),
`status ∈ {undecided, decided, disputed}`, `decision_reason`, `decided_by`,
`decided_at`, `archived_at`, `revision` (CAS).
`UNIQUE(workspace_id, key, scope_label)` ·
`CHECK(status <> 'decided' OR decision_reason IS NOT NULL)`

`workspace_dimension_observations`: `dimension_id` (CASCADE),
`source_kind ∈ {tender_document, supplier, internal, other}`, `source_ref_id`
(SET NULL), `value_text`, `value_num`, `note`, `created_by`.

**Regla determinista, sense IA:** dues observacions amb `value_num` diferent i no nul
→ el servei posa `status = disputed`. Sortir-ne exigeix **persona + motiu**.

Deliberadament **no** hi ha entitat `Site`, ni agenda d'adreces, ni graf de coneixement.

---

## 15. SolutionOptions

`workspace_solution_options`: `technical_solution_id` (SET NULL), `requirement_id`
(SET NULL), `supplier_name` i `product_name` (**text lliure, sense master**),
`description`, `quantity`, `unit`, **`unit_cost_ex_vat`** (`Numeric(18,6)`),
`pricing_basis`, `duration_months_override`,
`quote_status ∈ {draft, requested, pending, received, expired, declined}`,
`quote_date`, `quote_reference`, `quote_source`,
`selection ∈ {undecided, selected, rejected}`, `selection_reason`, `selected_by`,
`selected_at`, `internal_note`, `archived_at`, `revision` (CAS).

**No hi ha `unit_price`.** El preu viu només a `EconomicOfferLine`.

Casos reals coberts sense CRM: proveïdor pendent de cotitzar (`quote_status=pending`) ·
operador pendent (`requested`) · producte seleccionat i alternativa descartada amb
motiu, totes dues conservades · dues opcions germanes `undecided` fins que algú decideix.

---

## 16. Dimension dependencies

`workspace_solution_option_dimensions` (**N:N**):

| Camp | Tipus | Nota |
|---|---|---|
| `solution_option_id` | FK **CASCADE** | |
| `dimension_id` | FK **RESTRICT** | no s'esborra una dimensió de què depèn una opció |
| `role` | enum `{quantity_driver, scope_reference}` | |
| `quantity_factor` | `Numeric(18,4)` NULL, default 1 | només `quantity_driver` |
| `snapshot_value_num` | `Numeric(18,4)` NULL | valor de treball en vincular |
| `snapshot_taken_at` | TZ datetime | |

`UNIQUE(solution_option_id, dimension_id)`

- **`quantity_driver`**: s'**espera** `option.quantity == dimension.working_value_num × quantity_factor`. **Mai s'escriu automàticament.**
- **`scope_reference`**: informa l'opció sense governar-ne la quantitat.
- **Staleness determinista**: `working_value_num != snapshot_value_num` en un enllaç
  `quantity_driver` d'una opció viva. Refrescar el snapshot és una acció explícita.
- **Cap mutació automàtica de quantitat, en cap cas.**

El `quantity_factor` és el que permet repartir una mateixa dimensió entre dues
opcions de proveïdors diferents sense duplicar-la.

---

## 17. Option vs EconomicOfferLine source-of-truth

| Camp | Significat | Muta |
|---|---|---|
| `WorkspaceSolutionOption.unit_cost_ex_vat` | **Cost/cotització viva de l'opció** | Sí, quan arriba o caduca una cotització |
| `EconomicOfferLine.unit_cost_ex_vat` | **Snapshot econòmic adoptat** (Sprint 2, sense canvis) | Només per edició humana explícita |
| `EconomicOfferLine.supplier_name` · `product_name` | Snapshot en crear la línia | No segueix l'opció |
| `WorkspaceTechnicalSolution.supplier_name` · `product_name` | Snapshot de l'opció triada; **ja és contingut del manifest v2** | No automàticament; acció explícita «copia a la solució tècnica» |

**Cap sincronització automàtica, mai.**

`option_line_diverged` — **calculat, no emmagatzemat**: la línia té opció viva i
`(cost, supplier, product)` difereixen. S'exposa com
`option_divergence: {supplier, product}` **públic** i el component `cost` **dins
`internal_economics`**.

Quan una opció `selected` canvia, **res cascadeja**: si la solució era `validated`,
segueix validada — el `validated_content_fingerprint` del manifest ja la protegeix.
Les opcions **no són** contingut del manifest a Sprint 3 i per tant no poden fer
obsolet cap paquet congelat.

---

## 18. Cost redaction / write schemas

| Schema | Rols | Contingut |
|---|---|---|
| `SolutionOptionPublicRead` | tots | Tot excepte el cost. **El camp no hi és: no és `null`, és absent del model** |
| `SolutionOptionPublicWrite` | `SOLUTION_EDIT_ROLES` (inclou `technical`) | Subconjunt editable. **`extra="forbid"`** → enviar `unit_cost_ex_vat` dona **`422`** |
| `SolutionOptionInternalRead` | `ECONOMIC_INTERNAL_ROLES` | Public + `internal_economics: { unit_cost_ex_vat }` |
| `SolutionOptionInternalWrite` | `ECONOMIC_INTERNAL_ROLES` | Public write + `unit_cost_ex_vat: Decimal \| None \| UNSET` amb el **sentinella `UNSET` existent** |

**Preservació per construcció, no per condicional.** La signatura del servei és
`unit_cost_ex_vat: object = UNSET`. El router públic **no passa mai l'argument**, de
manera que el valor ocult es preserva sense que cap desenvolupador ho hagi de recordar.

Un `null` explícit només té efecte des del write schema intern.

---

## 19. `to-offer-line` idempotency

```
POST .../solution-options/{id}/to-offer-line
body: { section_id, expected_revision }
```

| Aspecte | Decisió |
|---|---|
| Unicitat | Índex únic **parcial** sobre `economic_offer_lines(solution_option_id)` WHERE `solution_option_id IS NOT NULL AND archived_at IS NULL` |
| Idempotència | Si ja hi ha línia viva per a l'opció → **`200`** amb la línia existent. **No `409`.** Doble clic i reintent convergeixen |
| CAS | `expected_revision` de l'**`EconomicOffer`**; conflicte → `409 economic_offer_revision_conflict` |
| Opció arxivada | `409 solution_option_archived` |
| Opció no seleccionada | **`422 solution_option_not_selected`** (D-03) |
| Arxivar la línia | Allibera l'opció. Recrear produeix una línia **nova**; l'arxivada conserva `solution_option_id` com a història |

---

## 20. Target markup

```
unit_cost_ex_vat × (1 + target_markup_pct/100) → proposed_unit_price   [assist, efímer]
offered_unit_price_ex_vat                                              [autoritatiu, persistit]
```

`target_markup_pct` (`Numeric(9,4)`, dins `internal_economics`) és **markup sobre
cost**, i el camp es diu així. El **multiplicador** és `1 + markup/100`; el **marge
sobre venda** és `markup/(100+markup)`. Els tres noms es mantenen separats, com ja fa
el docstring de `app/offer/pricing.py`. `Decimal` a tot arreu, **cap `Float`**.

`apply-target` és una acció explícita sobre línies triades. Mai massiva, mai automàtica.
El preu es pot editar sempre a mà; `price_matches_target` només informa.

---

## 21. Explicit `sync-from-option` (D-07)

```
POST .../economic-offer/lines/{id}/sync-from-option
body: { expected_revision, fields: ["unit_cost","supplier_name","product_name"] }
```

Rol `ECONOMIC_EDIT_ROLES` · **per línia i per camps** · mai bulk · mai trigger · mai
disparada perquè l'opció canviï.
`422 line_has_no_option` · `409 solution_option_archived` ·
`409 economic_offer_revision_conflict`.

---

## 22. Issues

`workspace_issues`: `issue_type ∈ {internal_question, authority_question,
supplier_question, contradiction, missing_information}` ·
`status ∈ {open, waiting, resolved, dismissed}` · `title` · `description` ·
FK nul·lables **totes `SET NULL`** cap a requirement, technical_solution,
solution_option, economic_offer_line i dimension · `supplier_name` lliure ·
`owner_id` · `due_at` · `resolution` · `resolution_source` · `resolved_by` ·
`resolved_at` · **`blocks_final_review: bool` default `false`** ·
`created_by` · `created_at`.

Provinença per `workspace_issue_source_ref_links` → `tender_source_refs`.

**`blocks_final_review` només el pot escriure `DECISION_ROLES`**: marcar que una cosa
impedeix presentar és una decisió d'oferta. No es fa un sistema de tickets generalista:
una tasca és *feina per fer* (`WorkspaceTask`, ja existeix), un issue és *coneixement
que falta*.

---

## 23. FinalReadiness findings

Contracte objectiu. **`open issue` no és blocador per defecte.**

| Codi | Sev. | Domini | Disparador objectiu | S'apaga quan |
|---|---|---|---|---|
| `tender_documents_unreviewed` | info | workspace | mai revisat, ≥1 document | revisió marcada |
| `tender_documents_changed` | warning | workspace | fingerprint del conjunt ≠ el revisat | revisió marcada |
| `requirement_analysis_predates_documents` | warning | technical | fitxer vigent amb `created_at` > `completed_at` de la run vigent | run nova o revisió |
| `dimension_disputed` | warning | technical | ≥1 dimensió `disputed` | decidides o arxivades |
| **`dimension_undecided_in_use`** | **blocker** | technical | dimensió `undecided` enllaçada a una opció viva que té línia econòmica viva | decidida |
| `dimension_snapshot_outdated` | warning | technical | `working_value_num ≠ snapshot_value_num` en enllaç `quantity_driver` d'opció viva | snapshot refrescat |
| `solution_option_quote_pending` | warning | technical | opció `selected` amb `quote_status ∈ {requested, pending}` | rebuda o decidida |
| **`solution_option_quote_expired`** | **blocker** | economic | opció `selected`, `quote_status=expired`, i línia viva que en depèn | cotització actualitzada |
| `economic_line_option_diverged` | info | economic | la línia i la seva opció ja no coincideixen | sincronitzada o acceptada |
| **`issue_blocking_open`** | **blocker** | workspace | issue amb `blocks_final_review=true` i `status ∈ {open, waiting}` | resolt o desmarcat |
| `issue_open` | info | workspace | comptador d'issues obertes sense marca | totes tancades |

Els blocadors automàtics **exigeixen una dependència viva**, de manera que mai poden
quedar en un estat irresoluble.

---

## 24. C-12 semantics

**El backend no bloqueja mai** l'edició d'una licitació manual per l'existència d'un
paquet aprovat. **No hi ha `409 tender_has_approved_package` ni
`acknowledge_package_impact`** (D-05).

Mecanisme, tot existent:

1. Un PATCH material escriu un `TenderRevision` nou amb un `normalized_hash`
   diferent. **Material = allò que canvia `normalized_hash`**, la mateixa definició
   que ja fa servir la ingesta: edició manual i actualització PLACSP comparteixen una
   sola regla.
2. `evaluate_staleness()` carrega `load_change_log(tender_id)` i
   `revision_of(package.tender_revision_id)`.
3. `log.since(frozen_on)` → motiu **`tender_changed`** → `StaleReport` no buit.
4. Si es va congelar abans que la licitació tingués historial: `log.basis_unknown()` →
   **`tender_revision_unknown`**.
5. Un paquet obsolet **no es pot aprovar**; el remei és congelar una revisió nova.
6. **L'historial d'aprovació és immutable**: les files de `SubmissionPackage` ho són.

El frontend pot mostrar un `ConfirmDialog`. **És presentació, no contracte de servidor.**

Camps editables: títol, descripció, organisme, expedient, tipus de procediment i de
contracte, valor estimat, pressupostos, moneda, dates, lloc, NUTS, país, `source_url`,
CPVs, lots. **No editables:** `id`, `source_id`, `external_id`, `owner_company_id`.
CAS per `expected_revision_count` → `409 tender_revision_conflict`.

---

## 25. Palamós benchmark oracle (D-06)

`tests/fixtures/palamos_s3_benchmark.json` amb **inputs independents transcrits de
REALITY-v1** i **outputs esperats fixos**:

```jsonc
{
  "currency": "EUR",
  "duration_months": 48,
  "sections": [ { "title": "...", "lines": [
      { "concept": "...", "quantity": "2", "pricing_basis": "monthly",
        "unit_cost_ex_vat": "35.0000", "target_markup_pct": "50.0000",
        "offered_unit_price_ex_vat": "52.5000" } ] } ],
  "expected": {
    "documented_total_ex_vat": "219351.55",   // REALITY-v1, arrodonit al final
    "canonical_total_ex_vat":  "<derivat a mà línia a línia>",
    "canonical_note": "El motor quantitza cada línia a 2 decimals i després suma.",
    "total_cost": "...", "markup_on_cost_pct": "...", "margin_on_sales_pct": "..."
  }
}
```

**El test afirma el valor canònic.** Mai es deriva l'expected del mateix motor que
s'està provant.

**Condició verificada:** el motor **suma els imports ja quantitzats de cada línia**,
mentre que el full de càlcul va multiplicar en precisió plena i va arrodonir al final.
Amb una vintena de línies **les dues regles poden diferir en cèntims**. Per això el
fixture desa **totes dues xifres** amb nota explícita.

**El codi de producció i la llavor de demo contenen ZERO dades de Palamós.**

---

## 26. Migrations 0027–0037

```
0026_technical_scope_review                       ← head actual
0027  Tender tenancy + manual tender foundation + matching isolation
0028  manual ingestion support (organismes privats + fonament de pujada)
0029  document review / staleness
0030  tender_source_refs
0031  manual requirements
0032  dimensions
0033  solution options
0034  solution option ↔ dimensions
0035  option → economic offer line
0036  target markup + explicit sync-from-option
0037  issues + readiness
```

**No es renumera.** Totes additives o relaxants. Cap migració `Float → Numeric`.
Cap canvi al manifest.

---

## 27. Fases S3-S0 … S3-S11

| Fase | Objectiu | Migr. | Depèn | Exit criteria |
|---|---|---|---|---|
| **S3-S0** | Tenancy + `visible_tenders()` + **fix de la fuita de `MatchingService`** | 0027 | — | Cap ruta ni cap job veu una licitació privada d'una altra empresa |
| **S3-S1** | Licitació manual + camí a Oportunitats | — | S0 | Apareix a `/licitacions` via match recalculat; retry idempotent |
| **S3-S1b** | **Edició de licitació manual**: només la pròpia i manual · CAS per `TenderRevision.revision_number` (veure Delta S3-S1b) · `TenderRevision` a cada canvi material · staleness canònic · **C-12: cap bloqueig per paquet aprovat** · `id`, `source_id`, `external_id` i `owner_company_id` immutables | — | S1 | Editar no bloqueja; `evaluate_staleness` → `tender_changed` |
| **S3-S2** | Pujada manual + contracte de formats | 0028 | S1b | `text_extraction` exposat; dedup per SHA-256 |
| **S3-S3** | Actualitzacions + staleness | 0029 | S2 | Els 3 findings; **cap validació tècnica invalidada** |
| **S3-S4** | `tender_source_refs` | 0030 | S2 | Citació reconstruïble després d'una versió nova |
| **S3-S5** | Requisits manuals | 0031 | S4 | Funciona amb `LLM_PROVIDER=fake`; **auditoria completa dels usos de `Requirement.run_id`** (D-09) |
| **S3-S6** | Dimensions | 0032 | S4 | `disputed` automàtic; decidir exigeix motiu |
| **S3-S7** | Opcions i cotitzacions | 0033 | S6 | `technical` no veu ni pot escriure el cost |
| **S3-S8** | Opció ↔ dimensions | 0034 | S7 | `dimension_undecided_in_use` determinista |
| **S3-S9** | Opció → línia idempotent | 0035 | S8 | Doble POST = una línia, `200` |
| **S3-S10** | Target markup + `sync-from-option` | 0036 | S9 | Preu manual mai sobreescrit; sync per camps |
| **S3-S11** | Issues + readiness + E2E Palamós | 0037 | totes | §28 en verd |

---

## 28. Tests i gates

Obligatoris a **cada fase amb migració**:

- Suite normal (SQLite en memòria).
- Test de migració marcat PostgreSQL contra **`licitacions_test`**.
- **`current_database()` ha de ser `licitacions_test`** abans de qualsevol test destructiu.
- **Mai el port 5432 / 3CX.**
- **Alembic amb un sol head.**
- `downgrade` i `upgrade` de la migració que s'introdueix.
- Tests d'aïllament entre tenants.
- Tests de fuita d'economia interna.
- Cap contrasenya als informes.

**Tests obligatoris de redacció de cost** (S3-S7): `technical` GET sense la clau ·
`technical` PATCH amb cost → `422` i valor intacte · `technical` PATCH amb `null` →
`422` i valor intacte · `technical` PATCH d'un camp públic → `200`, cost intacte ·
`bid_manager` ometent el camp → intacte · `bid_manager` amb `null` explícit → NULL ·
`viewer` escriptura → `403` · `technical` no rep el component `cost` de la divergència.

**Nota sobre enums:** `enum_column` fa `native_enum=False` **sense
`create_constraint=True`**, de manera que els enums es materialitzen com a VARCHAR
**sense CHECK**. Els enums nous s'han d'exercitar **via ORM, mai amb SQL cru**, que
donaria fals verd. El helper **no es toca**: canviar-lo afectaria 26 migracions.

### Test d'acceptació E2E congelat

Licitació manual creada → **visible només per a l'empresa A** → apareix a Oportunitats
via Match → **l'empresa B no la veu** (ni a `/matches`, ni a `/tenders`, ni a
`/facets`) → pujada de PDF → **pujada duplicada dedupicada per SHA-256** → DOCX i XLSX
**preservats amb extracció `unsupported` i 0 pàgines** → document posterior produeix
**`tender_documents_changed`** sense invalidar cap solució validada → **requisits
manuals amb `LLM_PROVIDER=fake`, sense cap run falsa** → **citació immutable**
reconstruïble després d'una versió nova → dimensió amb observacions discrepants →
**`disputed`** → **decisió humana del valor de treball amb motiu obligatori** → dues
opcions de proveïdor → una `selected`, una `rejected` amb motiu, **totes dues
conservades** → **`technical` no pot llegir ni escriure el cost** → **opció → línia
idempotent** (doble POST = una línia) → **snapshot de cost a la línia** → target markup
→ **sync explícita per camps** → issue amb `blocks_final_review` → **`issue_blocking_open`**
→ **benchmark econòmic amb fixture independent i valors esperats fixos**.

---

## 29. Decisions congelades D-01 … D-09

| ID | Decisió |
|---|---|
| **D-01** | **Parser DOCX fora del core d'Sprint 3.** Els DOCX/XLSX es preserven i se citen per `tender_source_refs` amb `ref_kind=document_file` |
| **D-02** | **Criteris d'adjudicació, scoring i anormalitat fora d'Sprint 3.** Quan tornin, amb **manifest v3** |
| **D-03** | **Opció `undecided` → línia econòmica = `422`** |
| **D-04** | **Licitacions manuals privades i no compartibles a V1.** `TenderCompanyAccess` és el camí futur |
| **D-05** | **Cap acknowledgement de servidor** per editar una licitació amb paquets aprovats. **C-12 mana:** edició permesa → canònic obsolet → historial d'aprovació immutable. La UX pot advertir |
| **D-06** | **Palamós té oracle independent al fixture.** Valor documental `219351.55 EUR` ex IVA. Si la quantització canònica línia a línia en produeix un de diferent, el fixture **desa tots dos** i documenta per què. **Mai es deriva l'expected del motor que s'està provant** |
| **D-07** | **`sync-from-option` és una acció EXPLÍCITA.** Mai trigger, mai sincronització automàtica |
| **D-08** | **Per V1, `Requirement` mutable** amb `created_by`, `updated_by`, `archived_at`, **sense historial complet de versions**. ⚠️ **Això NO equival a historial.** Historial/versionat de `Requirement` queda **LATER** |
| **D-09** | **`Requirement.run_id` nullable.** `origin` distingeix `extracted`/`manual`. Un requisit manual **NO crea cap `RequirementExtractionRun` falsa**. `input_fingerprint` continua significant **exclusivament** el fingerprint del corpus d'entrada d'una extracció real |

---

## 30. Riscos R-1 … R-6

| # | Risc | Sev. | Fase que el tanca | Mitigació |
|---|---|---|---|---|
| **R-1** | Faltaven les migracions 0027–0029 a l'ordre previst | 🔴 | **Resolt** | Afegides al davant; 0030–0037 conserven la numeració |
| **R-2** | `MatchingService._tenders()` fa `select(Tender)` **sense filtre d'empresa** — fuita entre tenants **ja existent avui** | 🔴 | **S3-S0 / 0027** | `visible_tenders()` obligatori + test que cap ruta ni cap job creui el límit |
| **R-3** | `enum_column` sense `create_constraint=True` → VARCHAR **sense CHECK** | 🟠 | Totes | Enums nous exercitats via ORM, mai amb SQL cru. El helper no es toca |
| **R-4** | 136 referències a `run_id`; el subconjunt de `Requirement` assumeix NOT NULL | 🟠 | **S3-S5 / 0031** | Auditoria enumerada com a **exit criterion** (D-09) |
| **R-5** | Tests per defecte a SQLite; migracions només exercitades amb `--postgres` | 🟡 | Totes | Gate `--postgres` contra `licitacions_test` a cada fase amb migració |
| **R-6** | Dev local via `dev_bootstrap`/`create_all`, no Alembic | 🟡 | Totes | El `dev.db` es regenera després de cada fase; no és font de veritat |

---

# UX Design Freeze

Sistema visual d'Sprint 1/2 intacte. **Cap redisseny global. Les 4 pestanyes de
Preparar no es toquen: Solució tècnica · Oferta econòmica · Dossier · Pla de treball.**

Dos drawers nous des de la capçalera de Preparar (mateix patró que Final Readiness) i
dues subseccions dins de pestanyes existents. **Zero pestanyes noves.**

### A. Oportunitats

| | |
|---|---|
| Acció | CTA **«Importa licitació»** a la capçalera, al costat del recàlcul de pre-fit |
| Buit | El missatge de feed buit existent, amb el CTA visible |
| Càrrega | Skeleton de llista existent |
| Permisos | El CTA només per a `owner`, `admin`, `bid_manager`; ocult per a `technical` i `viewer` |
| Estat obsolet | Sense canvis |

### B. Import wizard

| | Pas 1 — Metadades | Pas 2 — Documents |
|---|---|---|
| Acció | Expedient, títol, organisme, CPV, dates, pressupostos, lots | Arrossegar o triar fitxers |
| Buit | Formulari net; només títol obligatori | «Encara no hi ha documents» amb CTA de pujada |
| Càrrega | Botó amb `loading`; el `client_token` es genera en obrir el pas 1 | Barra per fitxer |
| Validació | Camps monetaris amb `Decimal`; dates coherents; CPV existent | Mida i MIME contra `DOCUMENT_ALLOWED_MIME_TYPES` |
| Conflicte/CAS | Reintent amb el mateix `client_token` → es reprèn la mateixa licitació, no se'n crea una altra | Fitxer idèntic → s'informa que ja hi era (dedup SHA-256) |
| Permisos | Si el rol no pot, no s'arriba a l'assistent | Igual |
| Dades sensibles | — | — |
| Estat | Pas 2 mostra per fitxer: **Text disponible** / **Es conserva, sense text** / **Processant** / **Ha fallat** |

Finalitzar l'assistent recalcula el match i navega a la fitxa de la licitació.

### C. Tender detail

| | |
|---|---|
| Acció | Llista de documents amb versió vigent; desplegable d'històric; secció **«Actualitzacions»** amb el que ha arribat després; botó **«He revisat les actualitzacions»** |
| Buit | «Cap actualització des de l'última revisió» |
| Càrrega | Skeleton de secció |
| Validació | — |
| Conflicte | Si algú altre marca la revisió, es refresca el fingerprint |
| Permisos | Marcar com a revisat: `owner`, `admin`, `bid_manager` |
| Sensibles | — |
| Estat | Xip **«Sense revisar»** (info) o **«Hi ha material nou»** (warning), amb enllaç a Final Readiness |

### D. Manual Requirements

| | |
|---|---|
| Acció | Crear, editar, arxivar; adjuntar citacions |
| Buit | «Cap requisit manual. Els extrets es mostren a sota» |
| Càrrega | Skeleton de fila |
| Validació | Títol obligatori; categoria i obligació obligatòries; arxivar demana confirmació |
| Conflicte | `updated_at` divergent → avís de recàrrega |
| Permisos | Editar/arxivar només `origin=manual`; els extrets són de només lectura excepte la revisió existent |
| Sensibles | — |
| Estat | **Distintiu visual explícit** entre `Extret` i `Manual`. Citacions: pàgina i cita per a documents amb text; etiqueta d'ubicació lliure per a DOCX/XLSX |

### E. Solució tècnica

**Subsecció «Dimensionament»**

| | |
|---|---|
| Acció | Crear dimensió; afegir observacions amb font i cita; decidir el valor de treball |
| Buit | «Cap dimensió. Afegeix-ne una quan dues fonts no coincideixin» |
| Validació | **Decidir exigeix motiu**; el botó resta desactivat sense ell |
| Conflicte | CAS per `revision` → `409` amb el que hi ha ara; el que s'ha escrit es conserva |
| Permisos | Escriptura `SOLUTION_EDIT_ROLES` |
| Estat | Xip **`En disputa`** (warning) / **`Decidida`** / **`Sense decidir`** |

**Panell «Opcions i cotitzacions»** (expansible per fila de solució)

| | |
|---|---|
| Acció | Afegir opció; marcar estat de cotització; seleccionar o descartar amb motiu; arxivar |
| Buit | «Cap opció encara» |
| Validació | Descartar exigeix motiu |
| Conflicte | CAS per `revision` |
| Permisos | `technical` pot editar camps públics |
| **Sensibles** | **El cost unitari no apareix per a `technical` ni `viewer`: el camp no hi és, no surt en blanc** |
| Estat | Xips de `quote_status` i de `selection`; les arxivades només amb «Mostra els arxivats» |

### F. Oferta econòmica

| | |
|---|---|
| Acció | Per línia lligada a opció: veure el snapshot, editar `target_markup_pct`, aplicar-lo, editar el preu, sincronitzar des de l'opció |
| Buit | Sense canvis respecte d'Sprint 2 |
| Validació | `Decimal`, mai `Float`; markup ≥ 0 |
| Conflicte | `expected_revision` de l'oferta → `409 economic_offer_revision_conflict`; el que s'ha escrit es conserva |
| Permisos | `ECONOMIC_EDIT_ROLES` |
| **Sensibles** | Cost, markup, preu proposat i el component `cost` de la divergència, tots dins `internal_economics` |
| Estat | Xip **«Difereix de l'opció»** amb el detall del que difereix; **`sync-from-option` és un diàleg amb caselles per camp**, mai un botó que ho aplica tot |

### G. Issues — drawer

| | |
|---|---|
| Acció | Obrir des de la capçalera o des d'un requisit, opció, línia o dimensió amb context precarregat |
| Buit | «Cap dubte obert» |
| Validació | Resoldre exigeix `resolution` |
| Conflicte | Edició concurrent → avís de recàrrega |
| Permisos | Escriptura per a tots els editors; **`blocks_final_review` només `DECISION_ROLES`** |
| Estat | Agrupat per `status`; **`blocks_final_review` amb marca visual inequívoca**; comptador al botó del drawer |

### H. Final Readiness

Sense canvis estructurals: mateix drawer, **agrupació per severitat** (Bloquegen /
Avisos / Per informació) i ordre estable de dominis dossier → technical → economic →
workspace. S'hi afegeixen els 11 findings de §23, cadascun amb el seu camí de
resolució: revisar actualitzacions, decidir dimensió, refrescar snapshot, actualitzar
cotització, sincronitzar o acceptar la divergència, resoldre o desmarcar l'issue.

---

## Apèndix — Convencions que no canvien

CAS per `expected_revision` amb `409 *_revision_conflict` · arxivat en comptes
d'esborrat · `Decimal` per a tot el que sigui diners o percentatges · economia interna
dins `internal_economics` amb el sentinella `UNSET` per distingir «no toquis» de «posa
NULL» · findings sense text ni xifres internes, només codi, severitat, domini, entitat
i context · rutes sota `/companies/{company_id}/workspaces/{workspace_id}/…`.

---

## Delta S3-S1b — el que la implementació ha corregit del disseny

Cinc punts que el codi real desmenteix o precisa. Es documenten aquí perquè la
fila d'S3-S1b de §27 deia coses que no es podien sostenir.

**1. El testimoni de CAS no és `revision_count`.** `Tender.revision_count`
compta **publicacions de la font** —s'assigna des de `SourceRecord.revision_number`
i s'exposa com a `source_publication_count`—, no correccions. Fer-lo servir com a
testimoni voldria dir que una edició manual no el mou i que dues correccions
simultànies no xocarien mai. El CAS va contra
**`TenderRevision.revision_number`**, que és el que sí que avança a cada revisió.
**No cal cap canvi d'esquema**: la columna ja hi era.

**2. Una licitació manual neix amb revisió 1.** Abans d'S3-S1b, l'alta no
escrivia cap `TenderRevision`, i sense fotografia inicial el primer PATCH no
tenia amb què comparar-se ni el paquet base de què penjar
(`evaluate_staleness` hauria dit `tender_revision_unknown`).

**3. Dos disparadors, no un.** La revisió base es desa amb
`trigger="manual_create"` i cada correcció amb `trigger="manual_edit"`. Obrir un
expedient no és corregir-lo, i dir-ne `manual_edit` faria semblar que algú ja
l'havia tocat abans que ningú l'hagués tocat. `trigger` és `String(40)`: no
necessita esquema.

**4. Tota escriptura real crea revisió.** Aquest és l'invariant, i el primer
intent no el complia: `expediente_number` i `country_code` es desaven sense
crear-ne cap perquè eren fora de la fotografia canònica, i això obria un forat
al CAS —dos clients que havien llegit la revisió 1 podien escriure tots dos, i
el segon no tenia manera d'assabentar-se que l'objecte ja havia canviat. La
fotografia d'una licitació **oberta a mà** ara inclou els dos camps
(`MANUAL_ONLY_FIELDS`). La d'una **sindicada no**: els hi fixa la font, ningú no
els corregeix, i afegir-los-hi globalment faria semblar canviades totes les que
ja hi ha. Mateixa taula, mateix `record_revision`, mateixa classificació, mateix
`evaluate_staleness`: cap segon sistema de revisions.

Per tant:

| | revisió | `revised` |
|---|---|---|
| Cap valor diferent | no | `false` |
| Qualsevol valor diferent, material o informatiu | **sí, +1** | `true` |

`revised` vol dir **s'ha creat una `TenderRevision`**, i res més.

**5. Corregir ≠ invalidar.** Que hi hagi revisió nova no vol dir que el paquet
quedi obsolet: això ho decideix la **taxonomia que ja existia**, i és una
pregunta separada. `TITLE_CHANGED`, `EXPEDIENTE_NUMBER_CHANGED` i
`COUNTRY_CHANGED` són `INFORMATIONAL` —el motor de pre-fit només llegeix
`nuts_code` i `place_of_execution`, així que classificar el país com a
`PREFIT_AFFECTING` faria caducar paquets per un camp que ningú calcula. El que
sí que dona `tender_changed` és un canvi material: termini, imports, tipus, CPV,
lots, documents. C-12 es compleix igualment: el paquet aprovat no bloqueja mai
l'edició.

**Recàlcul de pre-fit.** Va lligat a **si hi ha hagut canvi**, no a la
materialitat: el motor llegeix camps que la taxonomia classifica per a una altra
pregunta, i mantenir dues classificacions paral·leles acabaria amb un pre-fit
calculat sobre dades velles. És una sola licitació
(`recalculate(company_id, tender_ids=[tender.id])`). Un no-op no recalcula res.

**Codis d'error.** Editar una licitació d'una altra empresa és `404`, com arreu.
Editar-ne una de **pública** és `422 tender_not_manual` i no `404`: una licitació
publicada existeix per a tothom i dir que no s'edita per aquesta via no revela
res.

---

## Delta S3-S2 — el que la implementació ha precisat

Quatre punts que el codi real obliga a concretar. No canvia cap decisió
congelada: DOCX fora d'extracció, OCR fora, XLSX fora, criteris fora.

**1. Ruta d'upload.** `POST /companies/{company_id}/tenders/{tender_id}/documents`,
`multipart/form-data`, camp `file`. Company-scoped i amb els rols que decideixen
(`EDIT_ROLES`). Una licitació d'una altra empresa és `404`; una de **pública** és
`422 tender_not_manual`, perquè a Sprint 3 els plecs d'una licitació publicada
els porta la font.

**2. Identitat del document lògic.** `TenderDocument` té `url` NOT NULL i
`UNIQUE(tender_id, url)`, i un fitxer pujat no ve de cap URL. Se li'n dona una de
sintètica i opaca, `upload://<uuid4>`, que no resol enlloc. Conseqüència, i és
deliberada: **cada pujada és un document lògic nou**. Pujar dues vegades els
mateixos bytes dona DOS documents que comparteixen UN sol contingut; la resposta
ho diu amb `content_reused`. A S3-S2 no hi ha versions —arriben a S3-S3— i fer
que la segona pujada es convertís en silenci en la primera seria decidir per
l'usuari què volia dir.

**3. Read-model de l'extracció.** No hi ha cap columna nova. `analysis_status`
es calcula des de `ExtractionStatus` i `ExtractionQuality`:

| valor | què vol dir |
|---|---|
| `pending` | encara no s'ha intentat llegir |
| `extracted` | s'ha llegit i hi ha text aprofitable |
| `no_usable_text` | s'ha llegit i no n'hi ha. El cas típic és un PDF escanejat. **No s'hi fa OCR** |
| `unsupported` | d'aquest format no en llegim el text. El fitxer és vàlid i està guardat |
| `failed` | s'ha intentat i el parser no se n'ha sortit |

Va al costat de `stored` i de `text_extraction_supported`, que és una propietat
del **format**. Els tres camps són separats perquè guardar bé un fitxer i
poder-ne llegir el text són fets independents, i col·lapsar-los obligaria la
interfície a dir una de les dues mentides.

**4. TXT i versions de parser.** `text/plain` entra a
`DOCUMENT_TEXT_EXTRACTION_MIME_TYPES`: llegir-lo és descodificar-lo, sense cap
dependència nova ni cap model. Es representa amb **una sola pàgina lògica**, que
és una frontera nostra i no del document, posada perquè la fase de requisits cita
pàgines i necessita on apuntar; inventar-ne més faria que una cita digués
«pàgina 4» d'una cosa que no en té. No se li aplica el llindar de caràcters per
pàgina: aquest llindar existeix per detectar una pàgina de PDF a la qual li falta
la capa de text, i un fitxer de text no en té cap que pugui faltar. La
`parser_version` passa a ser **per tipus** (`pypdf-v1`, `plaintext-v1`): una de
global faria caducar un TXT cada cop que canviés el parser de PDF.

**Dedup.** El nivell són els BYTES, com abans: mateix SHA-256, un sol objecte a
l'storage i una sola extracció per versió de parser, vinguin d'una descàrrega o
d'una pujada. No s'hi ha introduït cap dedup per títol ni difús.

**Un plec que arriba és un canvi de la licitació.** Pujar-lo passa per la mateixa
porta que la sindicació (`record_revision`, trigger `document_processing`) i
genera el mateix `DOCUMENT_ADDED`, que ja era material. Per tant un paquet
aprovat queda `tender_changed`. **No és una regla nova d'S3-S2** i no és
l'obsolescència documental de S3-S3 (`documents_reviewed_*`), que aquí no
existeix.

---

## Delta S3-S2.1 — arribada ≠ obsolescència, i lectura dins d'una empresa

Dos invariants que el primer intent d'S3-S2 no complia.

### A. Un document nou NO caduca el que s'enviaria

La cadena era: pujar → `record_revision` → `DOCUMENT_ADDED` → qualsevol event
material → `tender_changed`. Això confonia dues preguntes diferents.

**Causa exacta**, en dues peces:

1. `DOCUMENT_ADDED` portava `{REQUIREMENTS_AFFECTING, COMPLIANCE_AFFECTING}`.
2. `evaluate_staleness` derivava `tender_changed` de **qualsevol** event
   material (`log.since`), sense mirar quin impacte tenia.

**Correcció final**, amb la taxonomia que ja existia i sense cap de nova:

- `DOCUMENT_ADDED` passa a `{REQUIREMENTS_AFFECTING}`. Continua sent **material**
  i continua generant revisió i event: la traçabilitat no es toca. El que deixa
  d'afirmar és res sobre el compliment, que es calcula sobre els requisits —i
  mentre ningú no hagi llegit el document, els requisits vigents són els d'abans.
- `evaluate_staleness` deriva `tender_changed` de `PACKAGE_STALE_IMPACTS`
  = `{PREFIT_AFFECTING, COMPLIANCE_AFFECTING, WORKSPACE_CRITICAL}`.
  `REQUIREMENTS_AFFECTING` no hi és.

| Fet | Revisió | `tender_changed` | Qui hi reacciona |
|---|---|---|---|
| Plec **nou** | sí | **no** | `requirements_outdated` (ja existia) |
| Plec **substituït** o **retirat** | sí | sí | també `workspace_critical` |
| Termini, imports, tipus, CPV, lots | sí | sí | pre-fit / compliment |
| URL d'un document que canvia | sí | no | és informatiu |

Quan algú llegeixi el document i l'extracció doni requisits diferents, hi haurà
un `RequirementExtractionRun` nou i el paquet ho sabrà per
`requirement_run_changed`, que compara runs i no events. La revisió documental
(`documents_reviewed_*`) és **S3-S3** i aquí no existeix.

**La regla és conceptual, no del canal**: no hi ha cap `if origin == uploaded`.
Un plec sindicat i un de pujat a mà es comporten igual, perquè l'arribada vol dir
el mateix en tots dos casos.

**TechnicalSolution**: una validació es lliga a l'empremta del contingut i a la
del requisit, i l'abast tècnic a una empremta feta de requisits. Cap de les tres
no la mou un document que ningú no ha llegit, així que l'invariant congelat es
compleix pel mecanisme que ja hi havia.

### B. Els plecs es llegeixen dins d'una empresa

`visible_to_user` no n'hi havia prou: qui és membre d'A i de B treballa sempre
**dins** d'una, i en context B podia llegir un plec d'A només perquè també era
d'A. El límit d'un tenant no és qui ets, és on ets.

Els set camins de lectura passen a penjar de `/companies/{company_id}` i filtren
amb `visible_to_company(company_id)`:

| Ruta final |
|---|
| `GET /companies/{c}/tenders/{t}/documents` |
| `GET /companies/{c}/tenders/{t}/documents/{d}/files` |
| `GET /companies/{c}/tender-document-files/{f}` |
| `GET /companies/{c}/tender-document-files/{f}/extractions` |
| `GET /companies/{c}/tender-document-files/{f}/pages` |
| `GET /companies/{c}/tender-document-files/{f}/original` |
| `GET /companies/{c}/tender-document-extractions/{e}/pages[/{n}]` |

**No queda cap ruta user-global de lectura documental.** No calia cap excepció
per als plecs públics: `visible_to_company` ja inclou les licitacions sense
propietari, així que un plec publicat es llegeix des de qualsevol empresa,
exactament com abans.

**Contingut compartit**: els mateixos bytes donen un sol `TenderDocumentContent`
i una sola extracció. Per tant el dret sobre una extracció **no** pot dependre de
qui la va crear —la segona empresa que puja el mateix plec es quedaria sense
poder llegir el text del seu propi document. La pregunta és si aquell contingut
el referencia algun `TenderDocument` visible **en aquesta empresa**; si només el
referencia l'altra, és un `404`.

**Superfície que queda fora d'aquesta passada**: `GET /tenders/{tender_id}` (la
fitxa) continua resolent-se amb `visible_to_user` i el seu cos inclou la
metadata dels documents. És el contracte del feed, decidit a S3-S0, i
company-scoparlo tocaria Oportunitats i la fitxa senceres.

---

## Delta S3-S3 — revisió documental, versions i els tres findings

### Migració 0029

`bid_workspaces.documents_reviewed_at` (timestamptz, NULL) i
`documents_reviewed_fingerprint` (varchar(64), NULL). Cap índex, cap FK, cap
`reviewed_by`, cap backfill. **NULL vol dir NO revisat.** No hi ha cap columna
«caducat»: es deriva comparant empremtes en llegir, igual que l'abast tècnic.

### Empremta documental canònica

`document_review.fingerprint_of(session, tender_id)` → SHA-256 de les línies
ordenades `document_id|file_id|version_number|content_sha256`, una per document
lògic, amb `-` on no hi ha fitxer vigent.

Hi entra **tot document admès**, inclosos els formats dels quals no sabem llegir
el text: un XLSX l'ha de revisar una persona igual que un PDF, i que el nostre
parser el sàpiga obrir o no és una limitació nostra, no un fet del document.
**No** hi entra res de l'extracció —ni estat, ni qualitat, ni pàgines— ni cap
sortida de model. És documental.

**Zero documents**: cap finding. «Cap document per revisar» no és un pendent, és
que no hi ha res; demanar que algú confirmi el buit seria feina inventada.

### Versions

`POST /companies/{c}/tenders/{t}/documents/{d}/versions`, multipart. Mateix
`TenderDocument.id`, `TenderDocumentFile` nou amb `version_number` anterior + 1,
l'anterior passa a `is_current=false` i **no es toca mai**: conserva el seu
contingut, la seva extracció i les seves pàgines, que són l'evidència que una
citació pot haver referenciat.

**Bytes idèntics als de la versió vigent → cap versió nova**, `200` en comptes de
`201`, l'empremta no es mou i la revisió no caduca. Tornar a pujar el mateix
fitxer passa, i convertir-ho en «els plecs han canviat» gastaria el senyal.

### Revisió humana

`POST /companies/{c}/workspaces/{w}/documents/review`. Desa l'empremta d'ara i la
data. Roles de decisió. **Idempotent**: si l'empremta ja és aquella, no mou ni la
data ni l'auditoria. No extreu requisits, no valida cap solució, no aprova cap
paquet i no afirma que no hi hagi canvis.

### Els tres findings

| Codi | Sev. | Domini | Predicat exacte |
|---|---|---|---|
| `tender_documents_unreviewed` | info | workspace | `document_count > 0` i `documents_reviewed_fingerprint IS NULL` |
| `tender_documents_changed` | warning | workspace | `documents_reviewed_fingerprint IS NOT NULL` i `≠ fingerprint_of(ara)` |
| `requirement_analysis_predates_documents` | warning | technical | hi ha run vigent, el corpus analitzable d'ara no és buit, i `analysis_input(ara).fingerprint ≠ run.input_fingerprint` |

**Delta sobre §23**: el disparador del tercer ja no és `created_at > completed_at`.
Es compara el **corpus**, no dates: `input_fingerprint` diu sobre quines
extraccions es va fer la run i `analysis_input` recalcula la mateixa empremta amb
el que hi ha ara. Dues dates poden ordenar-se bé i parlar de coses diferents —un
document reextret amb un parser nou no mou cap `created_at`—, i aquesta
comparació no té aquest problema. **No calia cap columna nova.**

Sense run vigent no es diu res per aquí: que no s'hagi analitzat mai ja té el seu
finding al dossier. Amb un corpus analitzable buit tampoc: un XLSX nou s'ha de
revisar, però l'anàlisi no el cobria abans ni ara.

**D-09**: el finding es limita a comparar runs reals. Un requisit manual no té
run i **no** el dispara; quan `origin` existeixi (0031), el predicat no s'ha de
tocar, perquè mai mira `run_id IS NULL`.

### Camins de resolució

A i B → fitxa de la licitació, pestanya **Plecs**, amb «Marca com revisats».
C → fitxa de la licitació, pestanya **Requisits**, on viu `RequirementAnalysisSection`
i on es pot tornar a executar l'anàlisi. **No** a Decidir: allà no hi ha el botó
que cal.

Marcar la revisió **no** apaga C: dir que has mirat els plecs no fa que l'anàlisi
els cobreixi.

### Tipus de document en una pujada manual

`POST …/documents` accepta `document_type` (opcional al servidor, per defecte
`other`). No és cosmètic: l'anàlisi automàtica només tria PCAP, PPT i annexos
(`DEFAULT_DOCUMENT_TYPES`), així que un plec pujat sense classificar **no hi
entra sol** — es guarda, es revisa i es pot triar a mà, però no hi entra.

**És una declaració humana, per document.** Al pas 2 de l'assistent, triar
fitxers ja no els puja: els posa en espera amb un selector de tipus per fitxer, i
s'envien quan qui els puja ha dit què són. Un reintent conserva el tipus ja
triat. El valor per defecte és `other` i **no s'endevina res del nom del
fitxer**: un «PCAP-administratiu.pdf» surt com a `other` si ningú no diu el
contrari.

El tipus és del `TenderDocument` **lògic**, no del fitxer: pujar una versió nova
**no** el reclassifica.

### Versió nova i paquet — **congelat**

`DOCUMENT_ADDED` i `DOCUMENT_REPLACED` **no** volen dir el mateix, i la
taxonomia actual ja ho expressa bé:

| | `DOCUMENT_ADDED` | `DOCUMENT_REPLACED` |
|---|---|---|
| Revisió i event | sí | sí |
| Empremta documental | canvia | canvia |
| `tender_documents_changed` | sí | sí |
| L'anàlisi pot quedar desfasada | sí | sí |
| Impactes | `REQUIREMENTS_AFFECTING` | + `COMPLIANCE_AFFECTING`, `WORKSPACE_CRITICAL` |
| **Paquet existent `tender_changed`** | **no** | **sí** |
| TechnicalSolution | no invalida | no invalida |

El motiu de la diferència no és que un afirmi que els requisits han canviat.
Cap dels dos ho afirma. És que **la font vigent que formava part de la base
documental del paquet ha estat substituïda**: el que es va aprovar penjava d'uns
bytes concrets i aquells bytes ja no són els vigents. Un document nou, en canvi,
no toca cap font que el paquet citi.

Val igual per a una pujada manual i per a una substitució d'una font sindicada:
**cap cas especial per `origin`**.

**C-12 intacte**: pujar una versió amb un paquet aprovat està permès, l'aprovació
queda immutable, el paquet no es degrada ni es desbloqueja res, i no hi ha cap
`409` ni cap acknowledge. Obsolet **no** és revocat.
