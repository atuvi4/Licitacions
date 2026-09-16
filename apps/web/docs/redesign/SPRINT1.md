# Sprint 1 · Redisseny del frontend · estat

Registre breu del que s'ha fet, de les decisions que s'aparten del handoff i
del que queda pendent. El handoff (`design_handoff_sprint1/README.md` al
projecte de Claude Design) continua sent la font funcional; els seus `.dc.html`
i `_ds/styles.css`, la font visual.

## Lots completats

- **L0 · Preparació i guardarraïls** (16 de setembre de 2026). Branca
  `redesign/sprint-1`, `lucide-react`, vitest + Testing Library, regla de lint
  3.4, baseline de les 11 pantalles a `baseline/` (vegeu `BASELINE.md`).
- **L1 · Tokens i fonaments visuals.** Valors nous de `@theme` a `globals.css`
  (cap nom de token canvia), Barlow i Barlow Condensed amb `next/font`.
- **L2 · UI base.** `src/components/ui/` (Button, StatusPill, Attribution,
  EvidenceRef, Banner, Card, EmptyState, ErrorState, PermissionNote,
  PageSkeleton, Tabs, DataTable, formulari, taula, Badge antic) amb un test per
  component; `ui.tsx` és el barril de compatibilitat; galeria temporal a
  `/ui-preview`.
- **L3 · Diccionari i lint.** `src/lib/labels.ts` (nivells de StatusPill per a
  cada enum, accions per codi d'error, `ANALYSIS_STATUS_TEXT`, `stepOf`,
  `deadlineLevel`, `ROLE_LABELS`) amb proves d'exhaustivitat i dels cinc
  invariants de la secció 0.4.
- **L4 · AppShell, navegació i capçalera global.** `shell/AppFrame.tsx` és el
  shell actiu (`(app)/layout.tsx` ja no usa `AppShell.tsx`, que s'esborra a
  L15). Barra lateral de 232 px amb els **quatre destins** (Avui, Oportunitats,
  Candidatures, El nostre perfil) i recomptes derivats de `/workspaces`,
  `/matches/summary` i `/summary` (mai un zero per error); `ProfileSubnav` a
  `empresa`, `experiencia` i `documents`; capçalera de 56 px amb empresa activa
  sempre visible, `GlobalSearch` que porta a `/licitacions?q=`, `JobCenter`
  basat en `analysis-limits.active_now` i usuari amb rol humanitzat. Icones
  Lucide a tot el shell; drawer sota 1024 px. **C-2**: sense llista de tasques
  per empresa, el centre de feina enllaça a Candidatures. **«Avui» continua
  temporalment a `/dashboard` fins que L5 creï `/avui`** (middleware i
  `app/page.tsx` es canvien a L5, en el mateix lot).

Verificació en tancar L4: `npm test` (94 proves), `npm run typecheck`,
`npm run lint`, `npm run build` i `git diff --check` en verd.

## Accessibilitat

Lighthouse (accessibilitat) a `/licitacions`, sobre el build de producció: **95**
(també després de L4). L'únic audit que falla és el `select` d'ordenació sense
etiqueta, preexistent, que es resol a **L6**.

## Decisions que s'aparten del handoff

- **`--color-brand` és accent-700 (`#416180`)**, no accent-500: el botó primari
  sòlid i qualsevol text blanc a sobre compleixen WCAG AA (6,5:1). Accent-500
  (`#749dc4`) queda com a `--color-accent-500`, només per a línies, subratllats
  i marques. `--color-faint` és `#6c6c6f` (4,7:1 sobre canvas).
- **`shots/` del projecte de Design són el frontend antic**: les imatges
  extretes de `Web licitacions.pdf`. Cap `.dc.html` en referencia cap. La
  referència visual del disseny nou són només els `.dc.html`, el README del
  handoff i `_ds/styles.css`.
- La galeria de components és `/ui-preview` (Next no serveix carpetes `_ui`).
- El barril `@/components/ui` exporta un `EmptyState` tolerant (acció opcional)
  perquè cinc pàgines antigues no en passen; l'estricte és
  `@/components/ui/EmptyState`.
- El peu de la barra lateral diu quantes àrees del perfil no estan al dia
  (derivable de `/summary`), no «l'àrea que bloqueja candidatures» (C-5).

## Pendent, amb el lot on es resol

| Què | Lot |
| --- | --- |
| Crear `/avui`, apuntar-hi «Avui», `middleware.ts`, `app/page.tsx` i la redirecció de `/dashboard` | L5 |
| Etiqueta del `select` d'ordenació a Oportunitats; llegir `?q=` de la cerca global | L6 |
| Retirar les tres exempcions de la regla de lint (`licitacions/[tenderId]/page.tsx`, `ComplianceSection.tsx`, `RequirementAnalysis.tsx`) | L9, L10 i L11 |
| Barril amb `EmptyState` tolerant → estricte | L15 |
| Esborrar `*_TONES`, `deadlineTone`, `Badge`, `AppShell.tsx` i `/ui-preview` | L15 |
| Captures `after/` i comparació amb `baseline/` | L15 |
