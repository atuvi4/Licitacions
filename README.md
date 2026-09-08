# Plataforma de licitacions públiques — Iteració 3

Eina SaaS B2B per ajudar empreses a detectar, analitzar i gestionar licitacions
públiques. La proposta de valor no és tenir *més* licitacions, sinó
**FIT + EVIDÈNCIA + WORKFLOW**.

Estat actual: **perfil corporatiu complet + ingestió real de licitacions
públiques + pre-fit determinista sobre metadades**.

El pre-fit creua el perfil de l'empresa amb els CPV, el territori, l'import i
els terminis que publica la font. **No** és una probabilitat de guanyar ni un
Go/No-Go: encara no s'ha llegit ni un plec. Tampoc no hi intervé cap LLM, cap
embedding ni cap similitud semàntica — el motor és determinista i reproduïble.

---

## Què hi ha implementat

| Àmbit | Estat |
| --- | --- |
| Monòlit modular (web / api / workers separables) | ✅ |
| PostgreSQL + SQLAlchemy 2 + Alembic | ✅ |
| Multi-tenant (`User` ↔ `CompanyMembership` ↔ `Company`) | ✅ |
| Autenticació amb cookie httpOnly + Argon2id | ✅ |
| Perfil d'empresa (dades, serveis, territoris, solvència, certificacions) | ✅ |
| CPV de l'empresa i CPV per projecte | ✅ |
| Registres (ROLECE, RELI, classificació), assegurances i equip humà | ✅ |
| Experiència: projectes + serveis + CPV executats | ✅ |
| Evidència documental (`Document` + `DocumentLink`) sobre storage S3 | ✅ |
| Completitud del perfil: 11 àrees, determinista i sensible a l'evidència | ✅ |
| Deduplicació de documents per SHA-256 | ✅ |
| Validat sobre PostgreSQL 16 i MinIO reals (migracions, persistència, S3) | ✅ |
| Ingestió de PLACSP: raw store immutable, idempotència i revisions | ✅ |
| Model canònic públic: `Buyer`, `Tender`, `TenderLot`, CPV, documents | ✅ |
| Feed de licitacions amb filtres, ordenació i paginació server-side | ✅ |
| Pre-fit determinista: CPV, territori, import i terminis | ✅ |
| Puntuació per lot i resum per expedient (millor lot, no mitjana) | ✅ |
| Explicabilitat: motiu estructurat per dimensió, en català | ✅ |
| Feed personalitzat amb modes i mètriques al dashboard | ✅ |
| Aïllament per `company_id` + tests que ho verifiquen | ✅ |
| Smoke tests sobre tenant efímer, amb neteja verificada | ✅ |
| Seed idempotent que repara la deriva (`--reset` per reconstruir) | ✅ |
| Descàrrega de plecs amb protecció SSRF i límits centralitzats | ✅ |
| Original immutable a S3, deduplicat per SHA-256 | ✅ |
| Versionat de documents: contingut canviat = versió nova, l'antiga es conserva | ✅ |
| Extracció de text per pàgina (pypdf, 1-based) amb qualitat determinista | ✅ |
| Preview del text extret amb navegació de pàgina | ✅ |
| Extracció de requisits del plec amb IA i evidència citable | ✅ |
| Criteris d'adjudicació amb la seva font | ✅ |
| Validació determinista de cites: sense evidència no hi ha requisit | ✅ |
| Compliment empresa ↔ requisits, determinista i auditable | ✅ |
| Hard blockers (potencials i confirmats) i elegibilitat | ✅ |
| Benchmark reproduïble de l'extractor amb gold dataset anotat a mà | ✅ |
| Workspace de candidatura: checklist, tasques, responsables i notes | ✅ |
| Go/No-Go **humà**: signat, datat, motivat i immutable | ✅ |
| Recomanació automàtica i decisió humana sempre com a camps separats | ✅ |
| Generació d'ofertes, memòries, RAG, OCR | ❌ (fase següent) |

---

## Arquitectura

```
Navegador
   │  cookie httpOnly (same-origin)
   ▼
Next.js (apps/web)  ──rewrite /bff/* ──►  FastAPI (apps/api)
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                    PostgreSQL          S3 / MinIO            Redis
                  (font de veritat)    (binaris)         (reservat, sense ús)
```

* **El navegador no parla mai directament amb l'API.** Next.js reescriu
  `/bff/*` cap a FastAPI, així la cookie de sessió és `HttpOnly`, first-party i
  la URL interna de l'API no s'exposa.
* **PostgreSQL és la font de veritat**, no cap "memòria d'IA". Les dades
  corporatives estan normalitzades en taules, no en un JSON gegant.
* **Els binaris no viuen a la base de dades.** Van a un object storage
  compatible amb S3; a la BD només hi ha `storage_key`, `sha256`, `mime_type` i
  mida.

Més detall a [`docs/architecture.md`](docs/architecture.md),
[`docs/data-model.md`](docs/data-model.md), [`docs/matching.md`](docs/matching.md),
[`docs/documents.md`](docs/documents.md), [`docs/requirements.md`](docs/requirements.md)
[`docs/compliance.md`](docs/compliance.md), [`docs/evaluation.md`](docs/evaluation.md) i
[`docs/workspace.md`](docs/workspace.md).

---

## Estructura del repositori

```
.
├── apps/
│   ├── api/                    # FastAPI + SQLAlchemy + Alembic
│   │   ├── alembic/            # Migracions
│   │   ├── app/
│   │   │   ├── api/            # Dependències i routers HTTP
│   │   │   ├── core/           # Config, seguretat, errors, logging
│   │   │   ├── db/             # Base declarativa i sessions
│   │   │   ├── models/         # Model de dades (SQLAlchemy 2)
│   │   │   ├── schemas/        # Contractes d'entrada/sortida (Pydantic)
│   │   │   ├── services/       # Lògica de domini (storage, evidència, KPIs)
│   │   │   ├── main.py         # Aplicació ASGI
│   │   │   └── seed.py         # Dades de demostració
│   │   └── tests/              # Suite pytest (corre sobre SQLite)
│   └── web/                    # Next.js 15 (App Router) + TypeScript + Tailwind
│       └── src/
│           ├── app/            # Rutes
│           ├── components/     # Sistema d'UI i shell de l'aplicació
│           └── lib/            # Client HTTP, tipus, formats, hooks
├── docs/
├── infrastructure/docker/
├── scripts/
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Requisits

* **Camí recomanat**: Docker i Docker Compose → PostgreSQL + MinIO reals.
* **Sense Docker, amb infraestructura real**: `scripts/local_infra.ps1` aixeca
  PostgreSQL 16 i MinIO com a processos d'usuari (sense administrador). Vegeu
  més avall.
* **Sense res** (només per iterar de pressa): mode SQLite + fitxers locals amb
  `scripts/dev_local.ps1`. Requereix Python **3.12+** i Node **20+**.

---

## Com arrencar-lo (Docker)

```bash
cp .env.example .env          # a Windows: copy .env.example .env
docker compose up --build
```

Serveis disponibles:

| Servei | URL |
| --- | --- |
| Frontend | http://localhost:3000 |
| API (OpenAPI) | http://localhost:8000/docs |
| MinIO (consola) | http://localhost:9001 |
| PostgreSQL | localhost:5433 |

Les migracions s'apliquen soles quan arrenca el contenidor `api`.

### Dades de demostració

```bash
docker compose exec api python -m app.seed
```

Crea l'empresa fictícia **Empresa Demo Telecom SL** (serveis, territoris,
facturació, certificacions, projectes i documents d'evidència), i una segona
empresa d'un altre usuari per poder comprovar l'aïllament a mà.

```
Usuari:      demo@licitacions.dev
Contrasenya: Demo12345!
```

> Totes les dades del seed són inventades. No hi ha cap dada real de cap empresa.

El seed és **idempotent i reparador**: executar-lo dues vegades no duplica res, i
si alguna cosa del tenant demo ha derivat dels valors declarats (per exemple, un
nom comercial canviat a mà), la torna al seu lloc. El que no fa és esborrar files
que no hagi creat ell.

```bash
python -m app.seed --dry-run   # diu què faria, sense desar res
python -m app.seed --reset     # buida els tenants demo i els reconstrueix
```

`--reset` només toca dades de tenant: les licitacions, els organismes i el raw
store de PLACSP són públics i no es toquen mai.

---

## Com arrencar-lo (mode local, sense Docker)

Pensat per a desenvolupament quan no hi ha Docker disponible. Substitueix
PostgreSQL per **SQLite** i MinIO pel **sistema de fitxers**; la resta del codi
és exactament el mateix.

```bash
# 1. Entorn de Python (amb uv, o amb python -m venv si ho prefereixes)
cd apps/api
uv venv --python 3.12
uv pip install -e ".[dev]"

# 2. API  ·  crea l'esquema i el seed el primer cop, i arrenca uvicorn a :8000
../../scripts/dev_local.sh          # a Windows: ..\..\scripts\dev_local.ps1

# 3. Frontend, en una altra terminal
cd apps/web && npm install && npm run dev
```

Obre http://localhost:3000. La base de dades queda a `apps/api/var/dev.db` i els
fitxers a `apps/api/var/storage/` (tots dos ignorats per git); esborrar `var/`
reinicia l'entorn des de zero.

Limitacions conegudes d'aquest mode:

* **Alembic no s'hi utilitza**: l'esquema es crea amb `python -m app.dev_bootstrap`
  a partir dels models. Les migracions només es proven i s'apliquen sobre
  PostgreSQL, que és on han de funcionar.
* Concurrència d'escriptura limitada (SQLite) i sense URLs pre-signades.

---

## Infraestructura real sense Docker

Quan no es pot instal·lar Docker Desktop (cal administrador), aquest script
aixeca **els mateixos components, ports i credencials** que `docker-compose.yml`:

```powershell
.\scripts\local_infra.ps1 init     # descarrega binaris, crea el clúster i el bucket
.\scripts\local_infra.ps1 start    # PostgreSQL :5433 + MinIO :9000 (consola :9001)
.\scripts\local_infra.ps1 status
.\scripts\local_infra.ps1 stop
```

Després, amb l'API apuntant-hi:

```bash
cd apps/api
export DATABASE_URL="postgresql+asyncpg://licitacions:licitacions@localhost:5433/licitacions"
export STORAGE_BACKEND=s3 STORAGE_ENDPOINT_URL=http://localhost:9000
export STORAGE_BUCKET=licitacions-documents
export STORAGE_ACCESS_KEY=minioadmin STORAGE_SECRET_KEY=minioadmin
alembic upgrade head && python -m app.seed
uvicorn app.main:app --port 8000
```

Els binaris i les dades viuen fora del repositori
(`%USERPROFILE%\.local\licitacions-infra`).

---

## Ingestió de licitacions

```bash
cd apps/api
python -m app.ingestion.placsp --limit 300        # mostra del feed viu
python -m app.ingestion.placsp --resume --max-files 3
python -m app.ingestion.placsp --dry-run --limit 20
```

La font, el mapatge camp a camp, el mecanisme de checkpoint i les incerteses
conegudes estan documentats a [`docs/placsp.md`](docs/placsp.md).

Un cop carregades, les licitacions es veuen a **Licitacions → Oportunitats**,
ja amb el pre-fit al costat.

---

## Pre-fit

```bash
cd apps/api
python -m app.matching --all-companies             # recalcula-ho tot
python -m app.matching --company <uuid>            # només una empresa
python -m app.matching --tender <uuid>             # només una licitació
```

També hi ha el botó **Recalcula el pre-fit** al feed i
`POST /api/v1/companies/{company_id}/matches/recalculate`.

Les regles, els pesos i el raonament de cada decisió (inclòs com es tracta una
dada que la font no publica) són a [`docs/matching.md`](docs/matching.md).

---

## Smoke test de la infraestructura real

`pytest` corre sobre SQLite i storage local: és ràpid però no demostra que
PostgreSQL i S3 funcionin. Per això hi ha un smoke test separat que recorre el
camí real de punta a punta:

```bash
# amb la pila engegada (Docker o local_infra)
apps/api/.venv/Scripts/python scripts/smoke_test.py

# conserva el tenant efímer per poder comprovar la persistència
apps/api/.venv/Scripts/python scripts/smoke_test.py --keep-tenant
# ...reinicia-ho tot, i després:
apps/api/.venv/Scripts/python scripts/smoke_test.py --check-persistence

# el camí d'ingestió: PLACSP -> raw store -> model canònic -> feed
apps/api/.venv/Scripts/python scripts/smoke_test.py --check-ingestion

# el pre-fit sobre PostgreSQL real (motius JSON, aïllament, paritat de filtres)
apps/api/.venv/Scripts/python scripts/smoke_test.py --check-prefit
```

Comprova login → CRUD de les set entitats del perfil → pujada d'un PDF →
**objecte realment al bucket** → metadada a PostgreSQL → descàrrega byte a byte
→ SHA-256 → deduplicació → enllaç dada→evidència → esborrat → **l'objecte
desapareix del bucket**. Retorna codi 1 al primer error.

### Els smoke tests no toquen dades demo

Cada execució es **crea la seva pròpia empresa efímera** (`Smoke Test <uuid>`)
amb el seu usuari, hi fa tot el CRUD i la destrueix dins d'un `finally` — també
si una comprovació falla pel camí. En acabar verifica que no queda cap fila
òrfena a PostgreSQL ni cap objecte al bucket, i que el tenant demo ha sortit
exactament com hi va entrar.

Això no és higiene teòrica: la versió anterior treballava sobre l'empresa demo
i li va deixar el nom comercial canviat a «Smoke 9b235f49» més un servei, un
projecte i un document residuals.

```bash
# demostra-ho des de fora, amb un procés independent
apps/api/.venv/Scripts/python scripts/verify_demo_untouched.py

# eines de tenant (no s'exposen mai per HTTP)
cd apps/api
python -m app.tenant_ops list                    # tenants efímers penjats
python -m app.tenant_ops snapshot <company_id>   # radiografia de files
python -m app.tenant_ops destroy <company_id>    # esborra i verifica
```

**Protecció de producció.** Cap d'aquestes ordres muta res si
`ENVIRONMENT` és de producció, ni al procés local ni al backend que respon
`GET /config` — i si l'entorn del backend no es pot llegir, tampoc. El nom de
la base de dades no compta com a senyal: apuntar un `DATABASE_URL` que sembli
de desenvolupament cap a una instància real és precisament l'error que això
atura. Surt amb codi 2.

---

## Variables d'entorn

Totes documentades a [`.env.example`](.env.example). Les essencials:

| Variable | Descripció |
| --- | --- |
| `SECRET_KEY` | Clau de signatura dels tokens de sessió. **Canvia-la a producció.** |
| `POSTGRES_*` | Credencials i host de la base de dades. |
| `DATABASE_URL` | Opcional; si s'informa té prioritat sobre `POSTGRES_*`. |
| `STORAGE_BACKEND` | `s3` (MinIO/AWS/R2/Scaleway/OVH) o `local` (només dev/tests). |
| `STORAGE_BUCKET`, `STORAGE_ENDPOINT_URL`, `STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY` | Configuració de l'object storage. |
| `MAX_UPLOAD_SIZE_MB` | Límit de mida per fitxer (25 MB per defecte). **Font única**: el frontend el llegeix de `GET /api/v1/config`, no el duplica. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Durada de la sessió (480 min per defecte). |
| `COOKIE_SECURE` | `true` en desplegaments amb HTTPS. |
| `EXPIRY_WARNING_DAYS` | Dies per considerar una acreditació "caduca aviat" (60). |
| `API_INTERNAL_URL` | URL de l'API vista pel servidor de Next.js. |

Cap secret arriba mai al navegador: el frontend només coneix rutes `/bff/*`.

---

## Migracions

```bash
# Aplicar
docker compose exec api alembic upgrade head

# Crear-ne una de nova a partir dels canvis als models
docker compose exec api alembic revision --autogenerate -m "descripcio"

# Tornar enrere
docker compose exec api alembic downgrade -1
```

Migracions actuals:

| Revisió | Contingut |
| --- | --- |
| `0001_initial` | Esquema complet: identitat, perfil corporatiu, acreditacions, equip, experiència, CPV, documents, enllaços d'evidència i auditoria (21 taules). |
| `0002_cpv_active` | Flag `active` als CPV de l'empresa (diferent d'`excluded`). |
| `0003_tenders` | Ingestió (`sources`, `ingestion_runs`, `source_records`) i model canònic públic (`buyers`, `tenders`, `tender_lots`, `tender_cpvs`, `tender_lot_cpvs`, `tender_documents`). |

Validades sobre PostgreSQL 16.9 buit: `upgrade head`, `downgrade -1`,
`upgrade head` i el cicle complet `downgrade base` → `upgrade head`.

---

## Tests

La suite del backend corre sobre **SQLite en memòria**: no necessita ni Docker
ni PostgreSQL.

```bash
cd apps/api
pip install -e ".[dev]"     # o: uv pip install -e ".[dev]"
pytest -q                   # 132 tests
```

O dins de Docker:

```bash
docker compose exec api pytest -q
```

Cobreix: unitats de domini (estat temporal, hash, sanejament de noms de fitxer,
claus d'storage), autenticació, CRUD del perfil, CPV d'empresa i de projecte,
equip i titulacions, registres i assegurances, pujada i descàrrega de documents,
deduplicació per SHA-256, traçabilitat dada → evidència, completitud del perfil
i —el més important— **aïllament entre empreses**.

Frontend:

```bash
cd apps/web
npm install
npm run typecheck   # comprovació de tipus
npm run build
```

---

## API

Documentació interactiva a `http://localhost:8000/docs` (desactivada en
producció). Convenció: tot allò que són dades d'empresa penja de
`/api/v1/companies/{company_id}/…`, de manera que el tenant és explícit i la
comprovació de pertinença s'aplica sempre.

Errors amb format estable:

```json
{ "error": { "code": "not_found", "message": "Recurs no trobat.", "details": {} } }
```

---

## Fora d'abast en aquesta iteració

Descàrrega i parsing de plecs, OCR, extracció de requisits, hard blockers,
Go/No-Go, LLMs, RAG, embeddings, pgvector, generació de memòries, alertes,
correus, workspace de candidatura i probabilitat de guanyar.

TED i altres plataformes agregades tampoc: la ingestió continua sent només
PLACSP.
