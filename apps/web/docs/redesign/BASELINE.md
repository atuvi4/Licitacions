# Baseline visual · abans del redisseny (Sprint 1, lot L0)

Captures de les pantalles **actuals** d'`apps/web` abans de tocar cap token ni
cap component. Serveixen per comparar, lot a lot, amb els mockups del handoff
(`design_handoff_sprint1/` al projecte de Claude Design) i amb les captures
`after/` que es faran a L15.

- **Data:** 16 de setembre de 2026.
- **Branca:** `redesign/sprint-1` (creada en local; sense commit ni push).
- **Commit de partida:** `0b751c7` (master).
- **Viewport:** 1440 × 900, escala 1, locale `ca-ES`. De cada pantalla hi ha la
  vista del viewport (`NN-nom.png`) i la pàgina sencera (`NN-nom-full.png`).
- **Navegador:** Google Chrome instal·lat a la màquina, pilotat amb
  `playwright-core` (`channel: "chrome"`, sense descarregar cap navegador).
  L'script és `capture-baseline.js`, en aquesta mateixa carpeta.

## Entorn (demo local, sense cap proveïdor extern)

| Peça | Valor |
| --- | --- |
| API | `uvicorn app.main:app` a `127.0.0.1:8000`, mode local (`scripts/dev_local.ps1`) |
| Base de dades | `apps/api/var/dev.db` (SQLite) amb el seed i l'escenari `scripts/demo_scenario.py` ja aplicats |
| Storage | `STORAGE_BACKEND=local` (`apps/api/var/storage`) |
| Motor d'anàlisi | **`LLM_PROVIDER=fake` i `ANTHROPIC_API_KEY` buida, forçats a l'ordre d'arrencada.** Cap crida a cap proveïdor. |
| Web | `next dev` amb `API_INTERNAL_URL=http://127.0.0.1:8000` |
| Usuari | `demo@licitacions.dev` (empresa **Empresa Demo Telecom SL**). La contrasenya és la del seed (`app/seed.py`, documentada al README de l'arrel) i l'script la llegeix de `DEMO_PASSWORD`: no és a cap fitxer del repositori. |

## Les 11 pantalles

| # | Fitxer | Ruta | Què és avui |
| --- | --- | --- | --- |
| 01 | `01-login.png` | `/login` | Inici de sessió |
| 02 | `02-dashboard.png` | `/dashboard` | Dashboard de completitud (serà «Avui» a L5; la completitud passa a L8) |
| 03 | `03-licitacions.png` | `/licitacions` | Oportunitats: modes, filtres oberts i taula |
| 04 | `04-licitacio-fitxa.png` | `/licitacions/{tenderId}` | Fitxa de licitació sencera (resum, documents, anàlisi, compliment, historial) |
| 05 | `05-licitacio-plec-text.png` | `/licitacions/{tenderId}/documents/{documentId}` | Text extret d'un plec |
| 06 | `06-candidatures.png` | `/candidatures` | Llistat de candidatures |
| 07 | `07-candidatura.png` | `/candidatures/{workspaceId}` | Candidatura: decisió, dossier, checklist, tasques, paquet |
| 08 | `08-empresa.png` | `/empresa` | Dades generals de l'empresa (formulari) |
| 09 | `09-empresa-solvencia.png` | `/empresa/solvencia` | Una subpàgina representativa del perfil |
| 10 | `10-experiencia.png` | `/experiencia` | Projectes |
| 11 | `11-documents.png` | `/documents` | Biblioteca de documents |

Els identificadors concrets (empresa, licitació, candidatura, document) són a
`baseline/manifest.json`; corresponen a la licitació estrella `DEMO-2026-001`
de l'escenari demo.

## Com repetir-ho (per a `after/` a L15)

```powershell
# API: scripts/dev_local.ps1 ja fixa SQLite, storage local i la resta de variables;
# abans d'executar-lo, força el proveïdor sense cost (la sessió d'usuari pot tenir
# LLM_PROVIDER=anthropic i una clau al perfil).
$env:LLM_PROVIDER='fake'; $env:ANTHROPIC_API_KEY=''
.\scripts\dev_local.ps1

# Web, des d'apps/web
$env:API_INTERNAL_URL='http://127.0.0.1:8000'; npm run dev

# Captures, des d'una carpeta fora del repositori amb `npm i playwright-core`.
# DEMO_PASSWORD és obligatòria (la del seed); DEMO_EMAIL és opcional.
$env:DEMO_PASSWORD='<contrasenya del seed>'
$env:OUT_DIR='<ruta absoluta>/apps/web/docs/redesign/after'; node capture-baseline.js
edesignfter'; node capture-baseline.js
```

## Estat de les comprovacions abans de tocar res

- `npm run typecheck`: verd.
- `npm run lint`: verd (3 avisos preexistents: una variable sense ús a
  `candidatures/[workspaceId]/page.tsx` i dues dependències de `useCallback` a
  `licitacions/[tenderId]/page.tsx`; no es toquen a L0).
