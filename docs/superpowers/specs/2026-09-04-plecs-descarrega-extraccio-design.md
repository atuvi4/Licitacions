# Iteració 4A — Descàrrega de plecs, versionat i extracció de text per pàgina

Data: 2026-09-04
Estat: aprovat, pendent de pla d'implementació

---

## 1. Objectiu i abast

Construir la base documental determinista sobre la qual s'aixecarà l'extracció
de requisits amb IA de la Iteració 4B. La cadena que ha de funcionar de punta a
punta és:

```
TenderDocument (URL) → DOWNLOAD → VALIDATE → HASH → RAW FILE IMMUTABLE
                     → PDF PARSER → PAGE TEXT → TRACEABILITY
```

L'usuari obre una licitació, veu els seus documents públics, prem **Processa** i
el sistema descarrega, valida, guarda l'original, calcula el SHA-256, extreu el
text preservant la separació per pàgina i li permet navegar-lo.

### Fora d'abast (explícitament NO)

Claude/OpenAI/Gemini, qualsevol LLM, embeddings, pgvector, RAG, extracció de
requisits, detecció de solvència, certificacions requerides, hard blockers,
Go/No-Go, generació de memòries, resums, i **OCR**. Tampoc parsers complexos de
DOCX/XLSX/ZIP. Tampoc descàrrega massiva dels 1.454 documents coneguts.

Aquesta fase és **determinista i reproduïble**: mateixos bytes + mateixa
`parser_version` = mateix text.

---

## 2. Principis de disseny

1. **Els plecs públics són globals.** Cap taula nova té `company_id`. Si 100
   empreses analitzen la mateixa licitació, el PDF es baixa i es parseja una
   vegada. La relació empresa ↔ requisit serà tenant-specific, però això és 4B.
2. **Els bytes són immutables.** Un original descarregat no es sobreescriu ni
   s'esborra mai. Les versions s'acumulen.
3. **El text és versionat pel parser.** Canviar de parser no destrueix el text
   anterior; crea una extracció nova sobre els mateixos bytes.
4. **`StorageService` és l'única porta al binari.** Cap `boto3` directe, cap
   filesystem local com a font de veritat, cap binari a PostgreSQL.
5. **Cap URL de la base de dades és de confiança.** El downloader no pot
   convertir-se en una primitiva SSRF.
6. **Backend = autoritat.** Cap número màgic al frontend ni repartit pel codi.

---

## 3. Model de dades

Quatre taules noves. `TenderDocument` no es modifica.

### 3.1 `tender_document_contents` — el blob, adreçat pel contingut

Fets immutables sobre una seqüència de bytes concreta. Res sobre com s'ha
obtingut ni sobre com s'ha parsejat.

| Camp | Tipus | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `sha256` | `String(64)` NOT NULL **UNIQUE** | clau de deduplicació global |
| `storage_key` | `String(512)` NOT NULL | mai exposat per l'API |
| `file_size` | `BigInteger` NOT NULL | bytes reals descarregats |
| `mime_type` | `String(160)` NOT NULL | **detectat**, no el declarat |
| `detected_extension` | `String(10)` | derivada del MIME detectat |
| `is_text_extractable` | `Boolean` NOT NULL | cert només si el MIME és a `DOCUMENT_TEXT_EXTRACTION_MIME_TYPES` |
| `created_at` / `updated_at` | timestamps | |

Índex: `UNIQUE (sha256)`.

Un `Content` no té estat d'extracció. L'extracció viu a la seva pròpia taula.

### 3.2 `tender_document_extractions` — una passada de parser sobre un blob

| Camp | Tipus | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `content_id` | UUID FK → `tender_document_contents.id` ON DELETE CASCADE | |
| `parser_version` | `String(50)` NOT NULL | p. ex. `pypdf-v1` |
| `status` | enum `extraction_status` NOT NULL | |
| `quality` | enum `extraction_quality` NOT NULL | |
| `page_count` | `Integer` NOT NULL default 0 | pàgines detectades al document |
| `extracted_page_count` | `Integer` NOT NULL default 0 | pàgines amb extracció correcta |
| `text_char_count` | `Integer` NOT NULL default 0 | suma de `char_count` de les pàgines |
| `empty_page_count` | `Integer` NOT NULL default 0 | |
| `ocr_candidate` | `Boolean` NOT NULL default false | vegeu §7.3 |
| `is_current` | `Boolean` NOT NULL default true | l'extracció que serveix l'API per defecte |
| `error_code` | `String(50)` | |
| `error_message` | `Text` | |
| `parse_ms` | `Integer` | durada del parseig |
| `started_at` / `completed_at` | timestamptz | |
| `created_at` / `updated_at` | timestamps | |

Índexs:

* `UNIQUE (content_id, parser_version)` — una extracció per versió de parser i blob.
* `UNIQUE (content_id) WHERE is_current` — índex únic parcial.
* `INDEX (content_id)`.

**Semàntica de versionat del parser.** Quan `PARSER_VERSION` canvia, processar
el document crea una **extracció nova** i marca l'anterior `is_current=false`.
Les extraccions anteriors i les seves pàgines es conserven íntegres. Un
`force_reextract` amb la **mateixa** `parser_version` substitueix les pàgines
d'aquella extracció de manera atòmica: el parser és determinista, de manera que
un reintent del mateix parser només té sentit per recuperar-se d'un error
anterior, no per generar història.

### 3.3 `tender_document_pages` — el text, pàgina a pàgina

| Camp | Tipus | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `extraction_id` | UUID FK → `tender_document_extractions.id` ON DELETE CASCADE | |
| `page_number` | `Integer` NOT NULL | **1-based** |
| `text` | `Text` NOT NULL | text normalitzat, mai resumit |
| `char_count` | `Integer` NOT NULL | `len(text)` |
| `text_hash` | `String(64)` NOT NULL | SHA-256 del text normalitzat |
| `status` | enum `page_extraction_status` NOT NULL | `extracted` \| `empty` \| `failed` |
| `created_at` | timestamp | |

Índexs: `UNIQUE (extraction_id, page_number)`, `CHECK (page_number >= 1)`.

**Numeració 1-based, a la base de dades i a la UI.** La pàgina 27 desada és la
pàgina 27 que veu l'usuari i la que citarà la IA a 4B. No hi ha cap capa de
traducció on es pugui colar un off-by-one. Documentat al docstring del model i a
`docs/documents.md`.

### 3.4 `tender_document_files` — la versió: què s'ha baixat d'on i quan

Lliga un `TenderDocument` (metadata/enllaç de PLACSP) amb un `Content`.

| Camp | Tipus | Notes |
| --- | --- | --- |
| `id` | UUID PK | |
| `tender_document_id` | UUID FK → `tender_documents.id` ON DELETE CASCADE | |
| `content_id` | UUID FK → `tender_document_contents.id` ON DELETE RESTRICT, **nullable** | null mentre es baixa o si falla |
| `source_record_id` | UUID FK → `source_records.id` ON DELETE SET NULL | traçabilitat cap a la ingesta |
| `source_url` | `String(1000)` NOT NULL | URL efectivament sol·licitada |
| `final_url` | `String(1000)` | després de redireccions |
| `original_filename` | `String(255)` | derivat de la URL |
| `detected_filename` | `String(255)` | de `Content-Disposition`, si n'hi ha |
| `declared_mime_type` | `String(160)` | capçalera `Content-Type` |
| `version_number` | `Integer` NOT NULL | 1, 2, 3… per `tender_document_id` |
| `is_current` | `Boolean` NOT NULL | |
| `download_status` | enum `download_status` NOT NULL | |
| `http_status` | `Integer` | |
| `redirect_count` | `Integer` NOT NULL default 0 | |
| `download_ms` | `Integer` | |
| `downloaded_at` | timestamptz | |
| `started_at` | timestamptz NOT NULL | per detectar processos encallats |
| `last_checked_at` | timestamptz | actualitzat quan un re-download confirma el mateix hash |
| `error_code` | `String(50)` | |
| `error_message` | `Text` | |
| `created_at` / `updated_at` | timestamps | |

Índexs:

* `UNIQUE (tender_document_id, version_number)`
* `UNIQUE (tender_document_id) WHERE download_status IN ('pending','downloading')` — índex únic parcial, el lock de concurrència (§6).
* `INDEX (tender_document_id)`, `INDEX (content_id)`.

`ON DELETE RESTRICT` sobre `content_id`: un blob referenciat no es pot esborrar
per accident.

**Versions fallides.** Cada intent de processat crea una fila de `File` amb el
seu `version_number`, també els que fallen: la història d'errors és informació
diagnostica que no es llença. Ara bé, un `File` amb `download_status='failed'`
**no pren `is_current`**; el `File` correcte anterior el conserva, de manera que
un error de xarxa transitori no fa desaparèixer de la UI un document ja
processat. Si no hi ha cap `File` correcte previ, el fallit sí que és el corrent.

### 3.5 Cadena de traçabilitat

```
Tender
  └─ TenderDocument                    metadata i URL de PLACSP
       └─ TenderDocumentFile           versió: quan, d'on, amb quin resultat HTTP
            └─ TenderDocumentContent   SHA-256, bytes a S3
                 └─ TenderDocumentExtraction   parser_version, qualitat
                      └─ TenderDocumentPage    page_number 1-based, text, text_hash
```

Cap baula és opcional per a una cita. Donada una pàgina, es pot reconstruir tota
la cadena cap amunt amb JOINs, sense heurístiques.

---

## 4. Deduplicació

**Nivell exacte de deduplicació:** els mateixos bytes (mateix SHA-256) produeixen
**un sol objecte a l'storage i una sola extracció per `parser_version`**, encara
que hi apuntin N `TenderDocument` de N licitacions diferents.

El que **no** es dedupliqua és la petició HTTP: per conèixer el SHA-256 cal
baixar els bytes. La deduplicació s'aplica després de descarregar i abans de
pujar a l'storage i de parsejar.

Conseqüència pràctica: si el mateix PCAP apareix a 40 expedients, es fan 40 GET
però només 1 `PUT` a MinIO i 1 parseig.

---

## 5. Versionat i idempotència

### 5.1 Els tres casos

| Cas | Condició | Acció |
| --- | --- | --- |
| **A** | mateixa URL, mateix SHA-256 | Cap versió nova. S'actualitza `last_checked_at` del `File` actual. Resposta amb `reused: true`. |
| **B** | mateixa URL, SHA-256 diferent | `File` nou amb `version_number + 1` i `is_current=true`; l'anterior passa a `is_current=false`. **Els dos originals es conserven.** |
| **C** | URL/document diferent, SHA-256 ja existent | `File` nou apuntant al `Content` existent. Ni `PUT` a l'storage ni parseig. |

Cap original processat es perd mai. No hi ha cap camí de codi que esborri un
`Content` ni un `File`.

### 5.2 `POST .../process` és idempotent per defecte

Sense flags, si ja hi ha un `File` actual amb `download_status = downloaded` i
una extracció actual en estat terminal amb la `PARSER_VERSION` vigent, l'endpoint
**no baixa res** i retorna l'existent amb `reused: true`.

| Flag | Efecte |
| --- | --- |
| `force_download: true` | Torna a demanar la URL i aplica els casos A/B/C. |
| `force_reextract: true` | Re-parseja des dels bytes ja guardats, **sense tornar a baixar**. |
| *(automàtic)* | Si l'extracció actual del contingut té una `parser_version` diferent de `PARSER_VERSION`, es crea una extracció nova sense re-descarregar. |

`force_reextract` sobre un document que mai s'ha descarregat correctament (sense
`Content`) no té bytes sobre els quals treballar: respon **422
`no_content_to_extract`**. `force_download` i `force_reextract` alhora és
redundant però vàlid: mana `force_download`, i el parseig es fa igualment.

Re-download i re-extract són operacions separades.

---

## 6. Concurrència

Sense Redis, sense workers, sense cua.

L'índex únic parcial `UNIQUE (tender_document_id) WHERE download_status IN
('pending','downloading')` és el lock. Tant PostgreSQL com SQLite ≥ 3.8 suporten
índexs parcials, de manera que els tests el verifiquen de debò i no només en
producció.

Seqüència:

1. **Transacció curta.** Comprova si es pot reutilitzar (§5.2). Si no, insereix
   el `File` amb `download_status='downloading'` i `started_at=now()`. Commit.
2. Si l'INSERT dona `IntegrityError` → **409 `document_processing_in_progress`**.
3. Descàrrega i parseig **fora de transacció** (poden trigar desenes de segons).
4. **Transacció curta final** amb l'estat definitiu, el `content_id` i les
   pàgines.

**Registres encallats.** Si el procés mor entre 1 i 4, el `File` queda en
`downloading` i bloquejaria el document per sempre. Mitigació: al pas 1, si hi ha
un `File` en curs amb `started_at` més antic que
`DOCUMENT_PROCESSING_STALE_SECONDS` (300 s), es marca `failed` amb
`error_code='stale'` i el nou intent continua.

El processat és **síncron**: el `POST` fa la feina i retorna el resultat final.
Tot i així, la lògica viu en un servei pur (`tender_document_processing.py`) que
no sap res d'HTTP, de manera que a una iteració futura un worker Celery/RQ el
pugui cridar sense modificar-lo.

---

## 7. Descàrrega, validació i extracció

### 7.1 Descàrrega

* `httpx.AsyncClient` amb `follow_redirects=False` i bucle de redireccions manual.
* **Streaming** (`client.stream("GET", ...)`): el SHA-256 es calcula chunk a
  chunk i la descàrrega s'avorta en el moment que se supera
  `DOCUMENT_MAX_SIZE_MB`. Un PDF gran mai es carrega sencer a RAM abans de
  comprovar-ne la mida.
* Reintents només per a estats transitoris (`408, 425, 429, 500, 502, 503, 504`)
  amb espera creixent. Un 404 o un 403 no es reintenten.
* Timeout per petició i timeout total de l'operació.
* User-Agent identificable, compartit amb la ingesta: `user_agent()` es puja de
  `app/ingestion/placsp/client.py` a un mòdul comú perquè les dues capes
  presentin la mateixa identitat. Descàrregues no agressives.

### 7.2 Validació de contingut

L'ordre de confiança és: **magic bytes > MIME detectat > `Content-Type` declarat
> extensió de la URL**. Mai al revés.

1. `http_status` ha de ser 200.
2. Mida > 0 i ≤ `DOCUMENT_MAX_SIZE_MB`.
3. Sniffing sobre el primer chunk:
   * `%PDF-` dins dels primers 1024 bytes, tal com tolera la norma PDF → `application/pdf`
   * `PK\x03\x04` → ZIP/OOXML
   * `<?xml` → XML
   * `<!DOCTYPE html`, `<html` → HTML
4. Si el MIME detectat no és a `DOCUMENT_ALLOWED_MIME_TYPES` →
   `error_code='invalid_content'`, no es guarda res.
5. Una URL o un nom que anuncia `.pdf` però que retorna HTML →
   `error_code='invalid_content'`. **No s'intenta parsejar com a PDF.**

**Validació estructural del PDF.** L'autoritat és **`pypdf`**: si obre el
document i en reporta les pàgines, el PDF és vàlid. Els magic bytes, la mida i el
MIME detectat són els filtres previs barats. La presència de `%%EOF` **no és un
criteri de rebuig**: PDFs perfectament llegibles amb bytes de farciment al final,
actualitzacions incrementals o servits amb la cua retallada donarien falsos
negatius. Si `%%EOF` no apareix, es registra com a senyal informatiu al log i
prou.

### 7.3 Extracció de text

* `pypdf`, `PARSER_VERSION = "pypdf-v1"`, executat amb
  `anyio.to_thread.run_sync` perquè és síncron i CPU-bound i no ha de bloquejar
  el bucle d'esdeveniments.
* PDF xifrat: es prova `decrypt("")`; si no funciona → `error_code='encrypted_pdf'`.
* `page_count > DOCUMENT_MAX_PAGES` → `error_code='too_many_pages'`.
* **Una pàgina que peta no tomba el document.** Es desa amb `status='failed'` i
  `text=''`, i l'extracció continua.

**Normalització**, mínima i conservadora:

* Unicode NFC.
* `\r\n` i `\r` → `\n`.
* Espais i tabuladors al final de línia, fora.
* Tres o més línies buides consecutives → dues.
* Caràcters de control fora, excepte `\n` i `\t`.
* **No** es col·lapsen espais interns: perdria l'estructura de columnes i taules,
  que és informació útil per a 4B.
* Cap resum, cap reinterpretació, cap reordenació.

**Qualitat, determinista:**

| Valor | Regla |
| --- | --- |
| `failed` | L'extracció ha fallat globalment. |
| `empty` | `text_char_count == 0`. |
| `low_text` | Mitjana de caràcters per pàgina < `DOCUMENT_LOW_TEXT_CHARS_PER_PAGE` (100) **o** `empty_page_count / page_count > 0.5`. |
| `good` | La resta. |
| `not_applicable` | El MIME no és extraïble (no-PDF). |

**`ocr_candidate`**, no `ocr_required`: `quality ∈ {empty, low_text}` **i** és PDF
**i** `page_count > 0`. El nom és deliberat: poc text és un **indici**, no una
prova, que calgui OCR — un plec pot ser legítimament curt, o ser una caràtula amb
quatre camps. La decisió d'OCR és posterior, més cara i separada. **En aquesta
iteració no s'implementa cap OCR.** Si les proves reals mostren molts documents
escanejats, s'informa i prou.

### 7.4 Formats no-PDF

PLACSP publica PDF, XML, ZIP i altres. En aquesta iteració:

* Es **descarrega i es guarda l'original** de tot el que sigui a
  `DOCUMENT_ALLOWED_MIME_TYPES`.
* Només s'extreu text dels MIME de `DOCUMENT_TEXT_EXTRACTION_MIME_TYPES`
  (`application/pdf`).
* La resta genera una extracció amb `status='skipped_unsupported'`,
  `error_code='unsupported_for_text_extraction'` i `quality='not_applicable'`.

No s'implementen parsers de DOCX, XLSX ni ZIP.

---

## 8. Storage

Adreçament per contingut, esclafat per no crear un prefix únic amb milers
d'objectes:

```
tenders/documents/<sha256[0:2]>/<sha256[2:4]>/<sha256>.<ext>
```

Separat de `companies/…` i de `sources/placsp/…`. La clau passa el `_SAFE_KEY`
existent sense modificar-lo; s'afegeix `build_tender_document_key(sha256, ext)`
al costat de `build_storage_key()`.

Tot l'accés va per `StorageService`. Cap `boto3` fora de `S3Storage`.

L'original es serveix per un endpoint autoritzat de l'API amb
`StreamingResponse`; **MinIO no s'exposa** i `storage_key` no apareix a cap
schema de resposta.

---

## 9. Seguretat: SSRF

`TenderDocument.url` és una URL externa desada a la base de dades. No és de
confiança.

Mòdul nou `app/services/url_guard.py`, aplicat **abans de cada salt de xarxa**,
no només a la URL inicial:

* Esquema: només `http` i `https`.
* Sense credencials incrustades (`user:pass@host`).
* Ports restringits a `DOCUMENT_DOWNLOAD_ALLOWED_PORTS` (per defecte `80,443`).
* Resolució amb `getaddrinfo` i validació de **totes** les IP resoltes contra:
  loopback, privada, link-local, reservada, multicast, unspecified, CGNAT
  `100.64.0.0/10`, ULA `fc00::/7`, `fe80::/10`, IPv4-mapped-IPv6, i explícitament
  els endpoints de metadades cloud `169.254.169.254` i `fd00:ec2::254`.
* `follow_redirects=False` + bucle manual que **re-valida cada `Location`**, amb
  un màxim de `DOCUMENT_MAX_REDIRECTS` (5).
* Flag `DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS`, per a tests, amb un
  `model_validator` a `Settings` que el **força a `False` en producció**.

**Deute tècnic reconegut i documentat:** queda una finestra TOCTOU de DNS
rebinding — es valida la IP resolta, però `httpx` torna a resoldre en connectar.
La solució completa és fixar la IP validada i sobreescriure la capçalera `Host`.
No s'implementa ara; queda registrat a `docs/documents.md`.

---

## 10. Configuració centralitzada

Tots els límits a `app/core/config.py`. Cap número màgic repartit.

```
DOCUMENT_DOWNLOAD_TIMEOUT_SECONDS = 30.0
DOCUMENT_DOWNLOAD_TOTAL_TIMEOUT_SECONDS = 120.0
DOCUMENT_MAX_SIZE_MB = 60
DOCUMENT_MAX_REDIRECTS = 5
DOCUMENT_DOWNLOAD_MAX_ATTEMPTS = 3
DOCUMENT_DOWNLOAD_BACKOFF_SECONDS = 2.0
DOCUMENT_DOWNLOAD_ALLOWED_PORTS = "80,443"
DOCUMENT_DOWNLOAD_ALLOW_PRIVATE_HOSTS = False
DOCUMENT_ALLOWED_MIME_TYPES = "application/pdf,application/xml,text/xml,application/zip,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/plain"
DOCUMENT_TEXT_EXTRACTION_MIME_TYPES = "application/pdf"
DOCUMENT_MAX_PAGES = 2000
DOCUMENT_LOW_TEXT_CHARS_PER_PAGE = 100
DOCUMENT_PROCESSING_STALE_SECONDS = 300
```

---

## 11. Enums

```python
class DownloadStatus(StrEnum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    DOWNLOADED = "downloaded"
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    SKIPPED_UNSUPPORTED = "skipped_unsupported"
    FAILED = "failed"


class ExtractionQuality(StrEnum):
    GOOD = "good"
    LOW_TEXT = "low_text"
    EMPTY = "empty"
    FAILED = "failed"
    NOT_APPLICABLE = "not_applicable"


class PageExtractionStatus(StrEnum):
    EXTRACTED = "extracted"
    EMPTY = "empty"
    FAILED = "failed"
```

Codis d'error (`String(50)`, no enum, per no obligar a una migració cada cop que
n'aparegui un de nou): `blocked_url`, `invalid_scheme`, `blocked_port`,
`too_many_redirects`, `dns_error`, `network_error`, `timeout`, `http_error`,
`not_found`, `too_large`, `invalid_content`, `unsupported_mime`, `storage_error`,
`corrupt_pdf`, `encrypted_pdf`, `too_many_pages`, `extraction_failed`,
`unsupported_for_text_extraction`, `stale`.

---

## 12. API

Endpoints públics globals: requereixen sessió iniciada però **no** pertinença a
cap empresa, igual que el router `tenders` actual.

| Mètode | Ruta | Descripció |
| --- | --- | --- |
| `POST` | `/tenders/{tender_id}/documents/{document_id}/process` | Processa. Cos: `{force_download?, force_reextract?}`. |
| `GET` | `/tenders/{tender_id}/documents/{document_id}/files` | Versions del document, més recent primer. |
| `GET` | `/tender-document-files/{file_id}` | Detall d'una versió, amb resum de contingut i extracció actual. |
| `GET` | `/tender-document-files/{file_id}/extractions` | Extraccions del contingut d'aquesta versió. |
| `GET` | `/tender-document-files/{file_id}/pages?offset&limit` | Pàgines de l'extracció **actual** (conveniència). |
| `GET` | `/tender-document-extractions/{extraction_id}/pages?offset&limit` | Pàgines d'una extracció concreta. |
| `GET` | `/tender-document-extractions/{extraction_id}/pages/{page_number}` | Una pàgina, 1-based. |
| `GET` | `/tender-document-files/{file_id}/original` | L'original, `StreamingResponse`. |

Codis: `200` processat o reutilitzat, `409 document_processing_in_progress`,
`404` document o fitxer inexistent, `422` validació.

`/original` respon amb `Content-Disposition: inline` per a PDF (perquè el
navegador el pugui mostrar) i `attachment` per a la resta, amb el nom original
sanejat.

Ordenacions fixes: les pàgines sempre per `page_number` ascendent, les versions
per `version_number` descendent, les extraccions per `created_at` descendent.

**Cap schema de resposta conté `storage_key`.**

`TenderDetail.documents` guanya un resum de l'última versió: `file_id`,
`download_status`, `extraction_status`, `quality`, `file_size`, `page_count`,
`ocr_candidate`, `processed_at`, `error_code`.

---

## 13. Frontend

* **Fitxa de licitació** (`licitacions/[tenderId]`): la secció Documents mostra
  per fila nom/tipus, URL, estat (no descarregat · descarregant · descarregat ·
  text extret · possible OCR · error), mida, pàgines i data de processament, amb
  un botó `[Processa]`. Selecció múltiple amb `[Processa seleccionats]`
  (seqüencial, no batch global).
* **Vista de document** (`licitacions/[tenderId]/documents/[documentId]`):
  llista de versions, i per a la versió actual un navegador de pàgines amb el
  text extret (anar a pàgina N, anterior/següent) i un enllaç a l'original. No es
  construeix cap lector de PDF avançat.

---

## 14. Migració

`alembic/versions/0005_tender_document_files.py`.

**Només crea taules noves.** No altera `tenders`, `tender_documents`,
`tender_lots`, `source_records`, `prefit_matches` ni cap taula de perfil. Per
tant no hi ha risc per als 1.188 Tenders, els SourceRecords, els pre-fits ni les
dades del tenant Demo.

`downgrade()` elimina les quatre taules i els seus índexs. Upgrade i downgrade
s'han de provar sobre PostgreSQL real.

El tenant Demo **no es toca**: no es modifiquen pesos, CPV, territoris ni el
perfil per fabricar candidates. El processat és manual des de qualsevol fitxa de
Tender.

---

## 15. Observabilitat

Logs estructurats amb `extra`, seguint el patró de `placsp/client.py`:

```
document_id, file_id, content_id, extraction_id, url_host, http_status,
download_ms, bytes, sha256, parse_ms, pages, chars, quality, status, error_code
```

**Mai es loguega el text dels plecs**, ni sencer ni per fragments. Ni tan sols la
URL completa amb query string: només el host.

---

## 16. Estratègia de proves

### 16.1 Unit i integració sense Internet

El downloader rep un `resolver` i un `transport` injectables. Els tests de SSRF
injecten un resolver fals (una IP pública per al cas permès, `127.0.0.1` i
`169.254.169.254` per als blocats) i els de bytes un `httpx.MockTransport`. **Cap
test de la suite depèn d'Internet.**

Un únic test aixeca un servidor HTTP local real per validar que el tall per mida
en streaming funciona de debò contra un socket, no només contra un mock.

Cobertura obligatòria: PDF vàlid · 404 · timeout · 500 amb retry · mida excessiva
· MIME incorrecte · HTML disfressat de PDF · redirect vàlid · redirect cap a IP
privada blocat · hash duplicat · mateixa URL amb contingut canviat · doble `POST`
concurrent → 409.

### 16.2 Fixtures de PDF sintètics

`tests/fixtures/pdf_builder.py` genera PDFs mínims escrivint sintaxi PDF crua.
**Cap dependència nova.** Cobreix: text normal · multi-pàgina · pàgina buida ·
sense text (simula escanejat) · corrupte (truncat) · accents catalans i
castellans (`àèéíòóúïüçñ`, WinAnsiEncoding) · document gran dins del límit.

Es verifica `page_count`, `page_number`, text, `text_hash`, `status` i `quality`.

### 16.3 Test de deduplicació

El mateix PDF referenciat per dues URL diferents ha de produir dos `File` i **un
sol** `Content` amb **una sola** extracció. El test comprova explícitament que la
segona crida no fa cap `upload` a l'storage ni cap parseig.

### 16.4 Prova d'integració amb PDFs reals — obligatòria

Els fixtures sintètics no substitueixen la realitat: un PDF generat per nosaltres
no té les rareses de fonts, encodings ni generadors dels plecs reals. Cal, sobre
PostgreSQL i MinIO reals, processar **3-5 documents reals de PLACSP** (PCAP, PPT
i un annex, de 3-5 tenders representatius) i informar de tender, tipus, mida,
SHA-256, pàgines, caràcters extrets, qualitat, ms de descàrrega i ms de parseig.
No es reprodueix el contingut dels plecs a l'informe.

Script: `scripts/process_real_documents.py`.

**No es descarreguen els 1.454 documents.**

### 16.5 Persistència

Reinici complet de PostgreSQL, MinIO i l'API. Després: l'original segueix
disponible, les pàgines segueixen disponibles, el hash coincideix, i tornar a
prémer Processa **no reprocessa res**.

### 16.6 UI real

Obrir els documents processats al navegador i verificar estat, pàgines, preview,
errors i original. Que l'API respongui 200 no és evidència que la UI funcioni.

---

## 17. Fitxers

**Backend nou**

```
app/models/tender_documents.py                  Content, Extraction, Page, File
app/services/url_guard.py                       validació SSRF
app/services/document_download.py               descàrrega streaming + validació + sniffing
app/services/pdf_text.py                        pypdf + normalització + qualitat
app/services/tender_document_processing.py      orquestració: dedup, versionat, transaccions
app/schemas/tender_documents.py
app/api/v1/routers/tender_documents.py
alembic/versions/0005_tender_document_files.py
scripts/process_real_documents.py
docs/documents.md
```

**Backend modificat**

```
app/core/config.py               paràmetres DOCUMENT_*
app/models/enums.py              4 enums nous
app/services/storage.py          build_tender_document_key()
app/api/v1/router.py             registre del router nou
app/schemas/tenders.py           resum de fitxer a TenderDocumentRead
app/api/v1/routers/tenders.py    carrega el resum a la fitxa
app/ingestion/placsp/client.py   user_agent() extret a un mòdul comú
pyproject.toml                   dependència pypdf
```

**Tests**

```
tests/fixtures/pdf_builder.py
tests/test_url_guard.py
tests/test_document_download.py
tests/test_pdf_text.py
tests/test_tender_document_processing.py
tests/test_tender_documents_api.py
```

**Frontend**

```
apps/web/src/app/(app)/licitacions/[tenderId]/page.tsx
apps/web/src/app/(app)/licitacions/[tenderId]/documents/[documentId]/page.tsx
apps/web/src/lib/types.ts, apps/web/src/lib/tenders.ts
```

**Dependència nova única: `pypdf`.**

---

## 18. Elecció de la biblioteca PDF

`pypdf`, llicència **BSD-3-Clause**.

* **Llicència.** És el criteri decisiu. PyMuPDF és AGPL-3.0: en un SaaS, l'ús per
  xarxa obliga a publicar el codi del servei sencer o a comprar llicència
  comercial a Artifex. Inacceptable per a un producte B2B propietari.
* **Zero dependències de sistema.** Python pur: el `Dockerfile` no canvia i el
  desenvolupament a Windows funciona sense wheels compilats.
* **API per pàgina neta.** `reader.pages[i].extract_text()` encaixa directament
  amb el requisit de preservar la separació per pàgina.
* **Manteniment.** Projecte actiu i àmpliament desplegat.
* **Qualitat suficient per al cas real.** El gruix de PCAP i PPT de PLACSP són
  PDFs digitals generats des de Word o LibreOffice, que `pypdf` extreu bé. Els
  escanejats no els salvaria cap biblioteca sense OCR.

Contrapartida acceptada: `pypdf` és més lent que PyMuPDF i pitjor en layouts molt
complexos. La capa `TenderDocumentExtraction` amb `parser_version` existeix
precisament perquè canviar-ho més endavant (per exemple a `pdfminer-v1`) sigui
una extracció nova sobre els mateixos bytes, sense re-descarregar i sense
destruir el text anterior.

---

## 19. Criteri d'acceptació

L'iteració està acabada quan, sobre PostgreSQL i MinIO reals, es pot:

1. Obrir un Tender real i veure'n els Documents amb el seu estat.
2. Prémer **Processa** i que el document es descarregui de PLACSP.
3. Veure l'original guardat a S3 amb el seu SHA-256.
4. Veure el PDF parsejat amb el text desat per pàgina.
5. Veure el nombre de pàgines i obrir el preview.
6. Anar a una pàgina concreta.
7. Tornar a processar el mateix document sense que es baixi ni es parsegi res.
8. Amb contingut canviat a l'origen: versió nova, original antic conservat.
9. Reiniciar-ho tot i que res s'hagi perdut ni calgui reprocessar.

I quan `pytest`, `pyflakes`, `tsc`, `eslint` i `next build` passen nets.

---

## 20. Condició d'aturada

Quan la cadena `TenderDocument → original immutable → SHA-256 → text per pàgina →
preview` funcioni de manera fiable, **aturar-se**.

No s'implementen Requirements ni IA. La Iteració 4B requereix autorització
explícita.
