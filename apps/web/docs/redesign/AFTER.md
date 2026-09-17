# Captures `after/` · Sprint 1 tancat (lot L15)

Les mateixes 11 pantalles de `BASELINE.md`, capturades amb el build de
producció del redisseny a **1440 × 900** i a **390 × 844**, més sis estats
transversals. Serveixen per comparar amb `baseline/` (la interfície antiga) i,
sobretot, amb els `.dc.html` del handoff (`design_handoff_sprint1/` al projecte
de Claude Design), que són la font visual del redisseny. `baseline/` és el
punt de partida, no la referència.

- **Data:** 17 de setembre de 2026.
- **Branca:** `redesign/sprint-1` (local; L15 sense commit en el moment de capturar).
- **Entorn:** API `uvicorn` a `127.0.0.1:8000` amb `LLM_PROVIDER=fake` i
  `ANTHROPIC_API_KEY` buida, SQLite `apps/api/var/dev.db` amb el seed i
  l'escenari demo; web `next build && next start` amb
  `API_INTERNAL_URL=http://127.0.0.1:8000`. Cap crida a cap proveïdor extern;
  cap dada modificada.
- **Scripts:** `capture-baseline.js` (ara accepta `VIEWPORT=390x844` i
  `PREFIX=m-`) i `capture-states.js` (estats). `DEMO_PASSWORD` i
  `OTHER_PASSWORD` només per entorn.
- **Fitxers:** `NN-nom.png` (viewport) i `NN-nom-full.png` (pàgina sencera) a
  1440 × 900; `m-NN-nom.png` i `m-NN-nom-full.png` a 390 × 844; `sN-*.png` i
  `m-sN-*.png` per als estats. Els identificadors són a `manifest.json`,
  `m-manifest.json` i `states-manifest.json`.

## Les 11 pantalles

| # | Fitxer | Ruta | Què hi ha ara | Mockup de referència |
| --- | --- | --- | --- | --- |
| 01 | `01-login` | `/login` | Inici de sessió amb els tokens del sistema (cantonades 0, sense ombra) | `Sistema visual i UX` |
| 02 | `02-dashboard` | `/dashboard` → `/avui` | «Avui»: decisions pendents, bloqueigs, cua d'oportunitats; buit B1/B2 quan toca | `Mockups` 01 |
| 03 | `03-licitacions` | `/licitacions` | Oportunitats: modes com a pastilles, filtres plegats, `DataTable` | `Mockups` 02 |
| 04 | `04-licitacio-fitxa` | `/licitacions/{tenderId}` | Capçalera de decisió persistent i pestanyes Resum/Plecs/Requisits/Historial | `Mockups` 03–05 |
| 05 | `05-licitacio-plec-text` | `/licitacions/{tenderId}/documents/{documentId}` | Text extret amb revisió i pàgina | `Pantalles restants` 08 |
| 06 | `06-candidatures` | `/candidatures` | Llistat per pas amb recomanació i decisió humana separades | `Pantalles restants` 09 |
| 07 | `07-candidatura` | `/candidatures/{workspaceId}` | Stepper Decidir → Preparar → Tancar; el seed obre a Tancar (revisió final) | `Mockups` 06, `Pantalles restants` 10–11 |
| 08 | `08-empresa` | `/empresa` | Hub del perfil: 11 àrees agrupades, completitud només al peu | `Pantalles restants` 12 |
| 09 | `09-empresa-solvencia` | `/empresa/solvencia` | Subpàgina representativa: `DataTable` d'exercicis i capacitat contractual | `Pantalles restants` 12 |
| 10 | `10-experiencia` | `/experiencia` | Projectes en `DataTable` amb filtres | `Pantalles restants` 12 |
| 11 | `11-documents` | `/documents` | Biblioteca en `DataTable`; «Millor des de l'escriptori» sota 768 px | `Pantalles restants` 12 |

## Els estats transversals

| Fitxer | Estat del handoff | Com s'ha obtingut |
| --- | --- | --- |
| `s1-loading` | C1 · càrrega | Fitxa de licitació amb les respostes de `/bff/tenders/**` retingudes 8 s des del navegador (`page.route`); cap canvi al backend. `PageSkeleton variant="detail"`. |
| `s2-404` | E3 · ja no existeix | `/candidatures/00000000-…` amb `demo@`: «Aquesta candidatura ja no hi és» amb sortida a Candidatures. |
| `s3-disabled` | E2 · acció visible i desactivada | Pas Tancar del seed: `simulation_only` deixa «Aprova per presentar» al 45 % amb la raó escrita. |
| `s4-empty-filter` | B4 · el filtre és el problema | `/licitacions?q=zzzz-cap-resultat-zzzz`. |
| `s5-empty-work` | B2 · falta la feina | `/candidatures` amb `altre@`, que no té cap candidatura. |
| `s6-empty-config` | B1 · falta configuració | `/avui` amb `altre@`, sense perfil. |

Cada estat també a 390 × 844 (`m-sN-*`).

## Estats que només tenen cobertura unitària

El seed no els conté i no s'ha modificat cap dada productiva per fabricar-los:

- Permís real denegat (`can_decide === false`, `can_sign === false`, 403 en
  desar): els dos usuaris del seed són propietaris. `WorkspaceDecision.test.tsx`,
  `WorkspaceDossier.test.tsx`, `SubmissionPackage.test.tsx`.
- Dossier incomplet, document que caduca abans del termini, pertinença sense
  confirmar, «Passa a revisió final»: `WorkspaceDossier.test.tsx`.
- Paquet obsolet, congelació de la revisió N+1, aprovació registrada, revisió
  superada amb aprovació històrica: `SubmissionPackage.test.tsx`.
- Anàlisi en curs, parcial o fallada; preview → confirm: `RequirementAnalysis.test.tsx`.
- Sessió sense empresa i error de sessió: `SessionProvider` (buit amb «Tanca
  la sessió»); sense usuari al seed que ho provoqui.
- Error de càrrega E1/E4 de cada pantalla: `LoadErrorState` + proves de pàgina
  (`page.test.tsx` de la fitxa, la candidatura i les llistes).

## Diferències deliberades respecte dels mockups

- `02-dashboard` és `/avui`: el `/dashboard` antic redirigeix.
- No s'ha implementat la barra inferior de 4 destins del mòbil: el calaix del
  shell és usable i la barra hi afegiria una segona navegació redundant.
- La taula unificada de Requisits + Compliment (`Mockups` 05) queda per a
  Sprint 2: continuen com a dos blocs.
- Els casos C-1 … C-10 continuen documentats a `SPRINT1.md` i no s'han
  implementat (noms de bloqueigs, submission, diff de revisions, etc.).
- `#` del manifest mostra `position` tal com arriba (0-based).

## Problemes visuals trobats i resolts a L15

- Botons de plegar grups del `DataTable` a 18 px en mòbil → 44 px.
- Accions primàries a 32–36 px en mòbil → 44 px (48 px el sòlid) des del
  component `Button`; controls de formulari a 44 px sota 640 px.
- Enllaços dins de text distingits només pel color (Lighthouse
  `link-in-text-block`) → subratllat fi a `EvidenceRef`, capçalera de la fitxa
  i enllaços del manifest; objectiu tàctil de les cites (`target-size`).
- `<dl>` de l'anàlisi amb el detall tècnic a dins (`definition-list`) → el
  detall surt del `<dl>`.
- Etiqueta d'evidència del compliment amb codis del backend
  (`civil_liability: unknown 1000000.00 EUR`) → `evidenceLabel` la tradueix
  amb els diccionaris i deixa el text cru al `title`.
- Columna «Encaix» de Candidatures amagada a tauleta (`hideBelow="lg"`) per
  evitar el desplaçament intern de la taula.
- Cantonades i ombra antigues a l'inici de sessió i al formulari de CPV.

## Lighthouse (accessibilitat, build de producció, sessió `demo@`)

| Ruta | Puntuació |
| --- | --- |
| `/licitacions` | 100 |
| `/avui` | 100 |
| `/licitacions/{id}?tab=requisits` | 100 |
| `/candidatures/{id}?pas=decidir` | 100 |
| `/candidatures/{id}?pas=preparar` | 100 |
| `/candidatures/{id}?pas=tancar` | 100 |
| `/empresa` | 100 |
| `/documents` | 100 |

## Responsive

Cap desplaçament horitzontal de pàgina a 1440 × 900, 1024 × 768, 768 × 1024
ni 390 × 844 a les 17 rutes auditades (les 11 pantalles més les pestanyes de
la fitxa i els tres passos de la candidatura). Sota 768 px totes les taules
són targetes (`stackBelow="md"`); a tauleta les columnes secundàries s'amaguen
amb `hideBelow="lg"`.
