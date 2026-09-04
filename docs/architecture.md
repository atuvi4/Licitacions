# Arquitectura

> Estat: iteració 2 (perfil complet + ingestió real de PLACSP).
> Data: 2026-09-03.

## 1. Principi rector

El valor del producte no és el volum d'oportunitats, sinó poder respondre amb
traçabilitat a tres preguntes:

1. **Encaixa** aquesta licitació amb l'empresa?
2. **Amb què ho acredito?**
3. **Qui fa què** per presentar-s'hi?

L'iteració 1 construeix el substrat de la pregunta 2: dades corporatives
estructurades i verificables, cadascuna enllaçable amb el document que la
justifica.

## 2. Forma del sistema

Monòlit modular. Un sol desplegament de backend, amb fronteres internes clares
i separables:

```
apps/web        Next.js. Presentació. Cap regla de negoci, cap secret.
apps/api        FastAPI. Tota la lògica de negoci i l'accés a dades.
  app/api       Transport HTTP: routers i dependències.
  app/schemas   Contracte extern (Pydantic). Valida i formata.
  app/services  Domini: storage, evidència, completitud, auditoria.
  app/models    Persistència (SQLAlchemy 2).
  app/core      Config, seguretat, errors, logging.
```

No hi ha microserveis. Quan calgui, els workers (ingestió, parsing, IA)
sortiran com a procés separat que importa `app.models` i `app.services` — per
això aquestes capes no depenen mai de FastAPI.

## 3. Decisions preses

### 3.1 Enllaç polimòrfic per a l'evidència

`document_links(document_id, entity_type, entity_id, relationship_type)` en
comptes d'una taula pont per tipus.

* **A favor**: afegir `Tender`, `Requirement` o `BidWorkspace` no toca
  l'esquema; una sola API i una sola UI per gestionar evidències; consultes
  uniformes per empresa.
* **En contra**: sense clau forana cap a l'entitat.
* **Mitigacions**: `entity_type` és un enum tancat; el servei
  `app/services/entities.py` verifica que l'entitat existeix **i pertany a la
  mateixa empresa** abans d'inserir; en esborrar una entitat s'esborren els
  seus enllaços explícitament; `company_id` està denormalitzat al link amb FK
  real; unicitat sobre `(document_id, entity_type, entity_id, relationship_type)`.
* **Reversibilitat**: qualsevol tipus concret es pot promoure a taula pont
  dedicada sense trencar la resta.

### 3.2 Els estats temporals no es persisteixen

Es guarda `verification_status` (decisió humana: pendent / verificat) i mai
`valid | expiring_soon | expired`. Aquest darrer es deriva sempre de
`expires_at` a `app/schemas/common.py`. Conseqüència: és impossible tenir una
certificació marcada com a vigent i caducada alhora, i no cal cap procés
nocturn que "actualitzi estats".

### 3.3 Autenticació

JWT HS256 signat pel backend, transportat en una cookie `HttpOnly` +
`SameSite=Lax` (+ `Secure` fora de local). Contrasenyes amb **Argon2id**.
El frontend no veu mai el token. També s'accepta `Authorization: Bearer` per a
clients d'API i tests.

* Es rebutja OAuth/SSO en aquesta fase: complexitat sense valor encara.
* Login amb missatge d'error únic i verificació d'un hash fictici quan l'email
  no existeix, per no permetre enumerar usuaris.
* `mfa_enabled` / `mfa_secret` existeixen a `users` però cap flux els utilitza.

### 3.4 Tipus portables i tests sense Docker

El model només fa servir tipus SQLAlchemy portables (`Uuid`, `Numeric`, `Date`,
`DateTime(timezone=True)`), sense `JSONB` ni `ARRAY`. Això permet que tota la
suite corri sobre SQLite en memòria mentre producció és PostgreSQL. Quan
s'introdueixi `pgvector`, quedarà aïllat i els seus tests requeriran Postgres.

### 3.5 Enums com a VARCHAR

`sa.Enum(..., native_enum=False)` sense `CHECK`: el domini encara creix i no
volem una migració cada cop que s'afegeix un valor. La validesa la garanteixen
Pydantic (entrada) i SQLAlchemy (escriptura).

### 3.6 Multi-tenant

Tota ruta de negoci penja de `/companies/{company_id}/…` i passa per
`get_company_context`, que exigeix una `CompanyMembership`. La fàbrica
`build_crud_router` centralitza el filtre per `company_id` perquè cap endpoint
se'l pugui deixar. Quan un recurs és d'una altra empresa es retorna **404, no
403**: no confirmem l'existència de recursos aliens.

Els rols (`owner`, `admin`, `bid_manager`, `technical`, `viewer`) estan
definits i persistits, però encara **no hi ha matriu de permisos**: qualsevol
membre pot llegir i escriure dins de la seva empresa.

### 3.7 Storage

Interfície **`StorageService`** amb quatre operacions —`upload`, `download`,
`delete`, `exists`— i dues implementacions: S3 (val per MinIO, AWS, Cloudflare
R2, Scaleway, OVH) i sistema de fitxers local (dev/tests).

`boto3` s'importa **només** dins de `app/services/storage.py`: cap altre mòdul
del backend sap que hi ha S3 al darrere, i canviar de proveïdor és canviar
variables d'entorn. Hi ha un test que ho fixa (`test_config_and_storage.py`).

Les claus les genera el servidor
(`companies/{id}/{any}/{mes}/{uuid}.{ext}`), mai deriven del nom que envia
l'usuari, i es validen contra path traversal.

Les descàrregues passen per l'API en comptes d'URL pre-signades: manté el
control d'autorització i l'auditoria a canvi d'amplada de banda. Quan els
fitxers creixin, es passarà a pre-signades amb caducitat curta.

### 3.8 Completitud del perfil

Determinista, explicable i escrita en un sol fitxer
(`app/services/completeness.py`): **onze àrees** amb pes fix que sumen 100 i una
regla verificable a mà per cadascuna. Cap IA, cap heurística oculta.

Dos principis afegits a la iteració 1.5:

1. **Cap àrea pot dominar el resultat.** Les dades generals valen 12 punts sobre
   100: omplir-les totes no pot fer semblar que el perfil està gairebé complet.
2. **Dada sense evidència = parcial.** Certificacions, registres, assegurances,
   titulacions i projectes només arriben al 100% de la seva àrea quan tenen
   document justificatiu. Sense ell no són acreditables davant d'un òrgan de
   contractació, i el dashboard ho ha de dir.

| Àrea | Pes | Regla |
| --- | --- | --- |
| Dades generals | 12 | proporció de 12 camps clau informats |
| Serveis | 10 | 3 serveis actius |
| Àmbit geogràfic | 8 | 2 territoris on es vol treballar |
| CPV | 8 | 3 CPV actius i no exclosos |
| Solvència econòmica | 12 | 0,6 × 3 exercicis + 0,4 × 5 llindars de contracte |
| Certificacions | 10 | 0,5 per tenir-ne + 0,5 × proporció amb evidència |
| Registres | 8 | idem |
| Assegurances | 8 | idem |
| Equip humà | 8 | 0,4 persones + 0,3 titulacions + 0,3 titulacions amb evidència |
| Experiència | 10 | 0,5 × 3 projectes + 0,5 × proporció amb evidència |
| Documents | 6 | 5 documents a la biblioteca |

### 3.9 CPV: catàleg que es va omplint sol

`cpv_codes` és un catàleg global compartit i el catàleg europeu oficial **no**
està importat. En comptes d'obligar l'usuari a mantenir dues coses (catàleg i
CPV de l'empresa), qualsevol codi que s'utilitzi es dona d'alta automàticament
amb la descripció que informi l'usuari (`app/services/cpv.py`). Quan s'importi
l'oficial, només caldrà completar descripcions provisionals.

`active` i `excluded` són dimensions **diferents** i deliberadament separades:
`active=false` vol dir "ara no ens interessa" (reversible, operatiu);
`excluded=true` vol dir "no ens hi presentarem mai" (decisió de negoci). El
matching futur les farà servir de manera diferent: la primera filtra, la segona
descarta.

### 3.10 Deduplicació de documents per SHA-256

En pujar un fitxer es calcula el SHA-256 **abans** d'escriure res a l'storage. Si
ja existeix un document amb el mateix hash dins de la mateixa empresa, la pujada
es rebutja amb `409 duplicate_document` i el cos de l'error inclou
`existing_document_id`, de manera que la interfície pot oferir vincular el
document existent en comptes de crear-ne una còpia. El paràmetre
`allow_duplicate=true` permet forçar-ho conscientment.

La comparació és **per empresa**: dues empreses poden tenir el mateix fitxer
sense interferir-se.

### 3.12 Ingestió: la font no és el model

PLACSP és **una font de dades**, no el nostre domini. Entre l'una i l'altre hi
ha quatre peces amb responsabilitats separades (`app/ingestion/placsp/`):

```
client.py      obtenir bytes de la sindicació oficial
parser.py      XML ATOM+CODICE -> representació intermèdia (vocabulari de PLACSP)
normalizer.py  representació de PLACSP -> model canònic (vocabulari nostre)
service.py     orquestració, idempotència i persistència
```

Cap mòdul de fora d'aquest paquet sap què és un `ContractFolderStatus`. Quan
arribi TED només caldrà un altre `parser` + `normalizer` que produeixin les
mateixes estructures normalitzades.

El detall de la font, el mapatge camp a camp i les incerteses conegudes són a
[`placsp.md`](placsp.md).

### 3.13 Raw store immutable

Abans de normalitzar res, el XML original de cada entrada es desa a l'object
storage amb el **mateix `StorageService`** que fan servir els documents
corporatius —no hi ha una segona abstracció d'storage— però en un espai de noms
separat:

```
companies/<company_id>/...   documents de tenant, privats
sources/placsp/AAAA/MM/...   raw públic de la font
tenders/...                  reservat per a plecs descarregats (encara no s'usa)
```

Un `SourceRecord` no es modifica mai. Si demà la mateixa licitació arriba amb
un contingut diferent, es crea una revisió nova i el raw anterior es queda on
és. Això és el que permet respondre «què sabia el sistema el dia X» i
reprocessar amb un parser millor sense tornar a demanar res a la font.

### 3.14 Idempotència garantida per la base de dades

Tres regles, i totes tres protegides per constraints, no només per codi Python:

| Cas | Condició | Resultat |
| --- | --- | --- |
| A | mateix `external_id`, mateix `raw_hash` | UNCHANGED: no es crea res |
| B | mateix `external_id`, hash diferent | revisió nova, **mateix** `Tender` actualitzat |
| C | `external_id` nou | `Tender` nou |

`uq_source_records_content (source_id, external_id, raw_hash)` i
`uq_tenders_source_external (source_id, external_id)` fan que un error de
programació no pugui duplicar res.

Verificat amb dades reals: 300 entrades d'un fitxer arxivat ingerides dues
vegades → segona execució 300 UNCHANGED, 0 creades.

### 3.15 Les dades públiques no es dupliquen per tenant

`Tender`, `TenderLot`, `Buyer`, `TenderDocument` i `SourceRecord` **no tenen
`company_id`**. Són fets públics: la mateixa licitació és la mateixa per a tots
els clients. La relació amb una empresa (match, guardat, workspace) serà una
taula pont en una fase posterior. Hi ha un test que impedeix que algú hi afegeixi
`company_id` sense adonar-se'n.

### 3.16 El pre-fit sí que és dada de tenant

La relació empresa ↔ licitació que anunciava §3.15 ja existeix:
`company_tender_matches` i `company_tender_lot_matches`. Aquestes taules **sí**
que porten `company_id`, i els seus endpoints pengen de
`/companies/{company_id}/matches` amb validació de pertinença. La licitació
continua sent global; el que és privat és la lectura que en fa cada empresa.

Són dues taules i no una perquè un `UNIQUE(company_id, tender_id, lot_id)` amb
`lot_id` nul·lable no restringeix res a PostgreSQL (NULL mai no és igual a NULL),
i perquè la fila d'expedient és un **resum** mentre que la de lot és un
**candidat puntuat**: barrejar-les obligaria a filtrar per `lot_id IS NULL` a
cada consulta.

### 3.17 El motor de pre-fit no toca la base de dades

`app/matching/engine.py` és una funció pura: rep una instantània del perfil i
les metadades d'una licitació, i retorna el resultat. Tota la persistència viu a
`service.py`. Això no és purisme: permet provar les regles de puntuació sense
Postgres ni HTTP (59 tests en 0,3 s) i fa que el motor sigui reproduïble per
definició — mateixa entrada, mateixa sortida, sense LLM ni aleatorietat.

El perfil es carrega **una sola vegada per recàlcul** (4 consultes) i no es torna
a tocar: puntuar 1.188 licitacions faria si no milers de consultes.

El detall de les regles i el seu perquè és a [`matching.md`](matching.md).

### 3.18 Els criteris de cerca no es dupliquen entre feeds

Hi ha dos feeds sobre les mateixes licitacions: el catàleg públic (`/tenders`) i
el mateix catàleg amb el pre-fit al costat (`/companies/{id}/matches`). Els
criteris (text, CPV, NUTS, import, dates, lots) viuen a
`app/api/v1/tender_filters.py` i els fan servir tots dos com a dependència de
FastAPI. Si es duplicaven, un dia divergirien i l'usuari veuria desaparèixer
resultats en canviar de vista sense cap motiu. Hi ha un test que compara el
recompte dels dos feeds amb els mateixos criteris.

## 4. Seguretat aplicada

* Aïllament per `company_id` a totes les consultes; tests dedicats.
* Argon2id + rehash automàtic quan canvien els paràmetres.
* Secrets només al backend; el navegador només veu `/bff/*`.
* Uploads: límit de mida real (lectura per trossos, no es confia en
  `Content-Length`), whitelist de MIME, nom de fitxer sanejat, clau interna
  generada pel servidor.
* Capçaleres `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
  `Cache-Control: no-store`.
* Logs sense contrasenyes, tokens ni contingut de documents.
* Auditoria append-only (`audit_logs`) amb només identificadors.

### 3.11 Una sola font per als límits d'upload

El límit de mida i la llista de tipus MIME els decideix el backend (variables
d'entorn) i el frontend els necessita per pre-validar. Duplicar-los al codi del
client garanteix que un dia divergiran, així que el backend els publica a
`GET /api/v1/config` i el frontend els llegeix en iniciar sessió.

El backend continua sent l'autoritat (respon 413 / 415). La validació al
navegador és només UX, però resol un problema concret: quan el backend rebutja
un cos massa gran, el proxy de Next talla la connexió i el navegador acabaria
veient un 500 genèric en comptes del missatge real.

## 4bis. Validació sobre infraestructura real

La suite de `pytest` corre sobre SQLite i storage de fitxers: és ràpida i no
demana Docker, però **no** demostra que PostgreSQL i S3 funcionin. Aquesta
distinció és deliberada i el buit es cobreix amb dues coses:

1. `tests/test_migrations.py` — compara l'SQL que genera Alembic amb els models,
   sense necessitat de cap base de dades engegada.
2. `scripts/smoke_test.py` — recorre el camí real (API + PostgreSQL + bucket S3)
   i verifica també allò que l'API no exposa: que l'objecte hi és de debò al
   bucket i que desapareix quan s'esborra el document.

### 4ter. Els tests no poden embrutar dades que no són seves

Els smoke tests fan CRUD real contra PostgreSQL i l'object storage. Abans ho
feien **sobre el tenant demo**, i el resultat va ser el previsible: el nom
comercial de l'empresa demo va acabar sent «Smoke 9b235f49» i hi van quedar un
servei, un projecte i un document residuals.

Ara cada execució es crea la seva pròpia empresa efímera (`Smoke Test <uuid>`)
amb el seu usuari, hi treballa i la destrueix dins d'un `finally`.
`app/tenant_ops.py` és l'única peça que sap fer-ho, i no s'exposa per HTTP:
crear empreses i usuaris des de fora és funcionalitat que encara no toca, i
obrir un endpoint per comoditat dels tests seria la mena de porta que després
ningú no tanca.

Tres decisions concretes:

* **La classificació de taules es llegeix de la metadata**, no d'una llista a
  mà: qualsevol taula amb `company_id` es neteja automàticament. Les quatre que
  pengen d'una altra (`person_qualifications`, `project_cpvs`,
  `project_services`, `service_cpvs`) es declaren explícitament, i un test peta
  si algun dia apareix una taula sense classificar.
* **El `CASCADE` esborra les files, però no els objectes de l'storage.** Les
  claus es llegeixen abans del `DELETE` — després ja no es podrien saber — i
  s'esborren al final. Un objecte que no es pugui esborrar queda registrat com a
  fallada: val més un orfe conegut que un de silenciós.
* **Els tests corren amb `PRAGMA foreign_keys=ON`.** SQLite ignora les claus
  foranes per defecte, així que sense això el `ON DELETE CASCADE` no faria res i
  la suite donaria per bo un esborrat que a PostgreSQL es comporta diferent.

La comprovació d'integritat és doble: l'smoke test radiografia el tenant demo
abans i després i falla si ha canviat res, i `scripts/verify_demo_untouched.py`
ho torna a comprovar des d'un procés independent, per no dependre que el test ho
faci bé.

### 4quater. La guarda de producció no es fia del nom de la base de dades

Cap operació que muti dades s'executa si l'entorn és de producció. Es comproven
**dues bandes**: el `ENVIRONMENT` del procés que escriu a la base de dades, i el
que declara el backend a `GET /config`. Si el segon no es pot llegir, tampoc no
es muta res — el silenci no és un sí.

El `DATABASE_URL` no compta com a senyal, per molt que digui «dev»: apuntar-lo
cap a una instància real és precisament l'error que això ha d'aturar. Per això
`environment` s'ha afegit a `/config`: no és cap secret (el desplegament ja és
públic) i és l'únic senyal fiable que té una eina externa.

## 5. Decisions pendents

| Tema | Pendent |
| --- | --- |
| RBAC | Definir la matriu rol × acció i aplicar-la. |
| CSRF | Avui la protecció és `SameSite=Lax` + JSON. Cal token si s'afegeixen formularis cross-site. |
| Descàrregues | Passar a URLs pre-signades i streaming quan els fitxers creixin. |
| Rate limiting | No n'hi ha. Cal com a mínim al login. |
| Paginació | Els llistats retornen tot; cal cursor quan creixin. |
| Pesos del pre-fit | Són globals per a totes les empreses. Personalitzar-los per tenant és una decisió de producte, no una limitació tècnica. |
| Recàlcul del pre-fit | S'engega a mà (botó al feed o CLI). No es dispara automàticament quan canvia el perfil o arriba una revisió: la interfície només marca la fila com a desactualitzada. |
| CPV | El catàleg europeu oficial no està importat (els codis es creen sota demanda amb descripció provisional). |
| Ingestió | Només el feed de licitacions de perfils allotjats a PLACSP. Falten contractes menors, encàrrecs, consultes preliminars i plataformes agregades autonòmiques. |
| Planificació | La ingesta s'engega a mà per CLI. No hi ha scheduler ni cua: arribaran amb els workers. |
| Plecs | Es guarda URL i metadada, mai el fitxer. Baixar-los massivament només tindrà sentit després d'un pre-filtre. |
| Deduplicació | Es detecta dins d'una empresa; no hi ha desduplicació de binaris a l'storage (dos documents idèntics de dues empreses ocupen dos objectes). |
| Docker Compose | El fitxer està escrit i revisat, però `docker compose up` no s'ha pogut executar mai en aquesta màquina (cal administrador). La resta de la pila sí que s'ha validat amb PostgreSQL 16 i MinIO reals. |
| Esborrat d'objectes | S'esborra de l'storage **després** del commit: si la crida a S3 falla, queda un objecte orfe (inofensiu) en comptes d'una fila sense fitxer. No hi ha recol·lector d'orfes. |
| Consistència d'UI | La pantalla de certificacions encara no fa servir `ResourceTable` com la resta; és el mateix comportament amb codi propi. |
| Auditoria | No registra el diff, només l'acció. |

## 6. Preparació per a fases futures

No s'ha implementat res, però s'han evitat decisions que ho dificultin:

* `document_links` ja accepta tipus d'entitat nous (`tender`, `requirement`…)
  només afegint un valor a l'enum.
* `Document.sha256` permetrà cachejar parsing/OCR i evitar reprocessar.
* Els projectes tenen `amount`, `end_date` i serveis indexats: la consulta
  "quants projectes de servei X > 150.000 € els últims 3 anys" ja és directa.
* Redis està declarat a `docker-compose` però **cap procés l'utilitza**; quan
  arribin els workers, no caldrà tocar la infraestructura.
* Les capes `models`/`services` no depenen de FastAPI, així un worker les pot
  importar tal qual.

Pipeline previst (encara **no** implementat):

```
Fetch → Raw Store → Normalize → Deduplicate → Enrich → Documents → Match → Deep Analysis
```
