# Model de dades

30 taules, en **dos móns que no s'han de barrejar**:

* **Dades de tenant** — tot allò que és corporatiu porta `company_id`; mai
  s'assumeix que hi haurà una sola empresa.
* **Dades públiques** — licitacions, organismes i registres de font. Són
  globals i compartides: **cap d'aquestes taules té `company_id`**, perquè
  duplicar dades públiques per tenant seria un error de disseny. La relació
  empresa ↔ licitació (match, guardat, workspace) serà una taula pont a part.

## Mapa general

```
users ──< company_memberships >── companies
                                     │
      ┌──────────────────────────────┼──────────────────────────────┐
      │                              │                              │
 company_services            company_certifications          company_projects
 company_territories         company_registrations                 │  │
 company_financial_years     company_insurances          project_services
 company_contract_prefs      company_people                   project_cpvs
 company_cpv_preferences        └─ person_qualifications
 service_cpvs
                                     │
                                documents ──< document_links >── (qualsevol de les anteriors)
                                     │
                                 audit_logs
```

## Identitat i tenants

| Taula | Notes |
| --- | --- |
| `users` | Email únic, `hashed_password` (Argon2id), `mfa_enabled`/`mfa_secret` reservats. |
| `companies` | Dades registrals. Cap `created_by`: les crea el sistema. |
| `company_memberships` | N:M usuari↔empresa amb `role` i `is_default`. Únic `(user_id, company_id)`. |

Un usuari pot pertànyer a diverses empreses. La resolució del tenant es fa
sempre a partir d'aquesta taula, mai d'un valor guardat a la sessió.

## Perfil corporatiu

| Taula | Claus i restriccions |
| --- | --- |
| `company_services` | Únic `(company_id, name)`. `specialization_level`, `active`. |
| `company_territories` | Únic `(company_id, territory_type, name)`. `code` lliure (INE, NUTS…), `priority`, `willing_to_work`. |
| `company_financial_years` | Únic `(company_id, year)` — el mateix any no es pot duplicar. `CHECK` de rang d'any. |
| `company_contract_preferences` | Una fila per empresa (índex únic sobre `company_id`). Cinc llindars d'import. |

## Acreditacions

| Taula | Notes |
| --- | --- |
| `company_certifications` | `verification_status` ∈ {pending_verification, verified}. **L'estat de vigència NO es guarda**: es deriva de `expires_at`. `CHECK` que `expires_at >= issued_at`. |
| `company_registrations` | ROLECE, RELI, classificacions, registres professionals, acreditacions administratives. |
| `company_insurances` | Tipus, proveïdor, número de pòlissa, cobertura i vigència. |

## Equip

| Taula | Notes |
| --- | --- |
| `company_people` | Només informació professional: rol, departament, perfil, anys d'experiència, disponibilitat. Cap dada personal innecessària. |
| `person_qualifications` | Penja de la persona (no té `company_id`; el tenant es deriva). Titulacions, certificacions professionals, formació, habilitacions. |

## Experiència

| Taula | Notes |
| --- | --- |
| `company_projects` | `amount`, `currency`, `start_date`, `end_date`, `client_type`, `status`, `reference_contact`. Índexs `(company_id, end_date)` i `(company_id, amount)`. |
| `project_services` | N:M projecte↔servei. PK composta. |
| `project_cpvs` | N:M projecte↔CPV. És una taula pont amb entitat pròpia (no un `secondary`) perquè hi haurà d'anar informació addicional: CPV principal o secundari, import imputat, etc. |

Aquesta forma permet respondre directament la consulta que motiva el mòdul:

```sql
SELECT count(*)
FROM company_projects p
JOIN project_services ps ON ps.project_id = p.id
WHERE p.company_id = :company
  AND p.amount > 150000
  AND p.end_date >= current_date - interval '3 years'
  AND ps.service_id = :service;
```

## CPV

| Taula | Notes |
| --- | --- |
| `cpv_codes` | Catàleg global, PK = codi. El catàleg europeu **no** està importat; s'hi poden donar d'alta els codis necessaris. |
| `company_cpv_preferences` | Únic `(company_id, cpv_code)`, amb `priority`, `active` i `excluded`. `active` i `excluded` són dimensions diferents: la primera és operativa i reversible, la segona és una decisió de negoci. |
| `service_cpvs` | CPVs típics d'un servei. |

## Evidència documental

| Taula | Notes |
| --- | --- |
| `documents` | Metadada pròpia (`name`, `category`, `description`, `issued_at`, `expires_at`) **independent del nom del fitxer**. `storage_key` únic, `sha256` indexat, `mime_type`, `file_size`. El binari viu a l'object storage. L'índex `(company_id, sha256)` sosté la detecció de duplicats en pujar. |
| `document_links` | `(document_id, entity_type, entity_id, relationship_type)` únic. `company_id` denormalitzat amb FK real. Índexs per `(entity_type, entity_id)` i `(company_id, entity_type, entity_id)`. |

`entity_type` admet avui: `company`, `company_service`,
`company_financial_year`, `company_certification`, `company_registration`,
`company_insurance`, `company_person`, `person_qualification`,
`company_project`. Afegir-ne un de nou no requereix taules noves.

`relationship_type` diu **què aporta** el document: `certificate`,
`certificate_of_good_execution`, `policy`, `contract`, `invoice`,
`financial_statement`, `cv`, `diploma`, `accreditation`, `technical_sheet`,
`reference_letter`, `supporting_document`, `other`.

Exemple de traçabilitat completa:

```
company_projects: "Desplegament FTTH nucli urbà"  281.430 €  2025
        ▲
document_links: entity_type=company_project
                relationship_type=certificate_of_good_execution
        ▼
documents: "Certificat de bona execució - Ajuntament Demo.pdf"
           sha256=…  storage_key=companies/…/a1b2.pdf
```

## Ingestió de fonts externes (públic)

| Taula | Notes |
| --- | --- |
| `sources` | Una fila per font (`PLACSP`). Guarda el `checkpoint`: per a PLACSP, l'URL de l'últim fitxer ATOM processat. |
| `ingestion_runs` | Auditoria de cada execució: comptadors, durada, checkpoint abans/després, errors. Respon «què va passar a la ingesta de les 06:00?». |
| `source_records` | **Raw store immutable.** Una fila per versió rebuda. Únic `(source_id, external_id, raw_hash)` → el mateix contingut no s'insereix dues vegades; únic `(source_id, external_id, revision_number)` → les revisions són consecutives. El XML viu a l'object storage (`sources/placsp/AAAA/MM/<id>.xml`); aquí només hi ha el hash i la clau. |

Les **revisions no tenen taula pròpia**: `SourceRecord` ja *és* la revisió
(hash, dates, punter al raw). `Tender.latest_source_record_id` apunta a la
vigent i `SourceRecord.tender_id` permet llistar-ne l'historial.

## Model canònic de licitacions (públic)

| Taula | Notes |
| --- | --- |
| `buyers` | Òrgan de contractació. Únic `(source_id, external_identifier)`. Per a PLACSP, l'identificador és l'ID_PLATAFORMA: el NIF no serveix com a identitat perquè un mateix ajuntament té diversos òrgans amb el mateix NIF. Sense fuzzy matching de noms. |
| `tenders` | Únic `(source_id, external_id)` — això és el que garanteix que reingerir no dupliqui. `expediente_number` és informatiu: **no és únic globalment** (verificat amb dades reals). Índexs per publicació, termini, comprador, estat, procediment, tipus, import i NUTS. |
| `tender_lots` | Únic `(tender_id, external_lot_id)`. Entitats consultables, no un JSON dins de `Tender`: el matching futur sovint serà per lot. |
| `tender_cpvs` / `tender_lot_cpvs` | Què **és** una licitació. No confondre amb `company_cpv_preferences`, que és què **interessa** a una empresa. Reutilitzen el catàleg `cpv_codes` existent. |
| `tender_documents` | Únic `(tender_id, url)`. Només metadada i enllaç: en aquesta fase no es descarrega cap plec. |

## Recepció de documents i classificació documental (tenant)

Fase 1 del motor d'anàlisi. Són dues capes que no es coneixen: la recepció diu
per on han arribat els documents; la classificació diu què és cada document de
la licitació, vingui del canal que vingui. El detall és a
[`intake.md`](intake.md).

| Taula | Notes |
| --- | --- |
| `tender_document_receptions` | Una entrega: `channel` (avui `zip_upload`), etiqueta, referència a l'origen, contenidor original (`container_content_id` → `tender_document_contents`, `RESTRICT`), estat i comptadors. |
| `tender_document_reception_items` | Un fitxer de l'entrega: camí, mida, SHA-256, estat, `skip_reason` (CHECK: omès ⇔ amb motiu) i el `TenderDocument` creat (`SET NULL`). Únic `(reception_id, position)` i `(reception_id, path)`. |
| `tender_document_classification_runs` | Una passada sobre **tots** els documents de la licitació: versió, empremta, disparador (`reception` · `request`) i `trigger_reception_id` (`SET NULL`, informatiu). Una de vigent per (empresa, licitació), per índex únic parcial. |
| `tender_document_role_suggestions` | Rol, confiança, `document_type` proposat i senyals, per document i passada. |
| `tender_document_classification_findings` | Incoherències del conjunt: codi, gravetat, document, document relacionat i detall. |
| `tender_document_role_decisions` | El rol fixat per una persona. Únic `(company_id, tender_document_id)`: penja del document, no de la passada, i sobreviu a les reclassificacions. |

Les filles referencien el pare per `(id, company_id)`. Tots els enums d'aquestes
taules porten CHECK a la base.

## Coneixement de l'empresa (fase 2)

El detall és a [`knowledge.md`](knowledge.md).

| Taula | Notes |
| --- | --- |
| `service_lines`, `capability_definitions`, `certification_standards` | **Globals** (sense `company_id`). Els omple la migració `0045` a partir de `app/knowledge/vocabulary.py`; cap API no hi escriu. |
| `company_operating_facts` | Fets clau tancats (`key`), un per empresa i clau, amb valor booleà i procedència. |
| `company_solutions`, `solution_capabilities` | Com es dona un servei i què en fa cada capacitat del vocabulari. Sense fila = desconegut. |
| `company_products`, `solution_products` | Productes (sense preus) i quins van amb cada solució. |
| `company_partners`, `partner_roles` | Tercers i els seus papers (sense condicions econòmiques). |
| `company_services` (ampliada) | `service_line_code`, `delivery_mode` i procedència; `active` fa de cicle de vida. |
| `company_certifications` (ampliada) | `standard_code`, `level`, `scope_service_lines` (abast estructurat), `knowledge_origin`, `as_of`, `lifecycle`; `verification_status` i `expires_at` ja hi eren. `verified` només amb evidència enllaçada. |
| `historic_bids` | Licitacions passades: només dades públiques i la decisió. |
| `knowledge_observations` | Suggeriments de l'històric amb la proposta de fila; `candidate` → `promoted` / `dismissed`, sempre per una persona. |

Totes les taules d'estat actual porten `knowledge_origin`, `as_of`,
`valid_until`, `verification_status` i `lifecycle` (o l'equivalent que ja hi
havia). Les filles referencien el pare per `(id, company_id)`.

## Pre-fit (tenant)

Aquí és on es creuen les dues meitats del model: el perfil corporatiu (privat) i
el catàleg de licitacions (públic). Les taules **sí** que porten `company_id`.

| Taula | Notes |
| --- | --- |
| `company_tender_matches` | Un resum per `(company_id, tender_id)` — únic. Puntuació total, estat, puntuació per dimensió, `data_coverage`, `factors` (JSON amb el motiu de cada dimensió), `rules_version` i les marques de temps del perfil i de la licitació amb què es va calcular. `best_lot_id`, `candidate_lot_count` i `total_lot_count` resumeixen els lots. Índexs `(company_id, status)` i `(company_id, score)`: el feed ordena i pagina per puntuació dins la base de dades. |
| `company_tender_lot_matches` | Un candidat puntuat per `(company_id, tender_lot_id)` — únic. Mateixes columnes de puntuació. |

Dues taules i no una: un `UNIQUE` que inclogui una columna nul·lable
(`lot_id`) no restringeix res a PostgreSQL, i les dues files signifiquen coses
diferents (resum ↔ candidat). El detall de les regles és a
[`matching.md`](matching.md).

L'estat del pre-fit (`match_status`) **no té ni `GO` ni `NO_GO`**: aquests
valors requereixen haver llegit els plecs, cosa que encara no es fa.

## Auditoria

`audit_logs`: append-only, amb `company_id`, `user_id`, `action`,
`entity_type`, `entity_id` i un `summary` curt no sensible. No és event
sourcing i no guarda el diff.

## Convencions

* PK `UUID` a totes les entitats (excepte `cpv_codes`, on la PK és el codi, i
  les taules pont, amb PK composta).
* `created_at` / `updated_at` a totes les taules; `created_by` / `updated_by`
  allà on hi ha una acció humana.
* Enums com a `VARCHAR` (sense tipus natiu ni `CHECK`) per poder créixer sense
  migracions.
* Imports com a `NUMERIC(18,2)` + camp `currency` de 3 lletres.
* Esborrat: `ON DELETE CASCADE` cap avall des de `companies`; els enllaços
  d'evidència es netegen a la capa de servei perquè són polimòrfics.
