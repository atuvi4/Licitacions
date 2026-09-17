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
- **L5 · Avui.** `/avui` és la pantalla d'entrada: «Avui» del shell hi apunta,
  `/dashboard` hi redirigeix, i el login, `/` i el middleware hi porten (cap
  moment on el shell digui «Avui» i navegui al dashboard antic). Tres blocs
  compostos només amb dades existents: **Decisions pendents** (`/workspaces`
  amb la regla de decisió pendent de `shell/counts.ts`), **Bloquejos i avisos**
  (`eligibility === "no_go"`, `analysis_outdated`, `deadline_passed`; un termini
  nul no és un bloqueig) i **Noves per revisar** (`/matches?match_status=
  candidate&sort=deadline&only_open=true&limit=5`). Cap recompte es mostra com a
  zero mentre carrega ni quan una crida falla. La recomanació automàtica es
  mostra sempre com a tal, mai com a decisió humana. **C-3** (no hi ha llista
  de bloquejadors amb nom) i **C-4** («Només les meves») continuen documentats i
  no implementats. La completitud del perfil i les caducitats han sortit del
  dashboard i encara no tenen pantalla: **es recuperen a L8** al hub del perfil.

- **L8 · El nostre perfil.** `/empresa` és el hub del perfil: les **11 àrees**
  de `completeness.sections[]` en una `DataTable` agrupada pel seu `status`
  real («Sense dada» amb tint de bloqueig, «Amb dada incompleta», «Al dia»
  plegat), amb què hi ha (`detail` de l'API), què en depèn (text genèric per
  clau) i l'acció cap a cada subpàgina (`empresa/areas.ts`). El formulari de
  dades generals viu a **`/empresa/dades`** sense cap canvi de lògica
  (mateixes dades, mutacions, `cleanPayload` i `fieldErrors`).
  `completeness.score` només surt al peu de la taula i al recompte de la barra
  lateral; `expiry_alerts` es recuperen al hub («Caducitats que t'afecten»,
  StatusPill de `TEMPORAL_LEVEL`, data real i «en N dies» / «fa N dies»).
  Perfil a zero → targeta de benvinguda amb els tres primers passos (CPV,
  territoris, franja d'import a solvència). **C-5** continua documentat i no
  implementat: cap recompte de licitacions a «Què en depèn» ni cap «Què
  desbloquejaries avui». `PermissionNote` només apareix davant d'un 403 real
  de l'API (botó visible i desactivat), sense inventar permisos al client:
  l'API només comprova la pertinença, i amb el seed cap usuari rep un 403.
  `Badge` → `StatusPill` (nivells de `lib/labels.ts`) a totes les subpàgines
  del perfil, `documents`, `experiencia` i `EvidencePanel`.

- **L6 · Oportunitats.** `/licitacions` sobre `DataTable` amb **cinc
  columnes**: Encaix, Licitació, Termini, Import, Estat. La consulta és la
  mateixa d'abans: `feedQuery` (`licitacions/query.ts`) reprodueix
  literalment l'expressió antiga sobre `buildQuery` i està coberta per proves
  de regressió contra un oracle copiat del codi anterior (6 modes × 5 ordres ×
  10 jocs de filtres × 3 pàgines). Els **6 modes** són vistes (`StatusPill
  outline`, activa `action`; «Per revisar» = `candidate`) i els **5 ordres** es
  conserven. `?q=` de la cerca global i `?match_status=` de la URL s'apliquen
  de debò a la consulta (el formulari mostra el valor i el `<details>` s'obre).
  El formulari de filtres és el mateix, sencer, dins d'un `<details>` «Filtres
  · N actius» plegat per defecte: no se n'elimina cap. El pre-fit continua sent
  un encaix de metadades i no una probabilitat de guanyar (el disclaimer ho
  diu; `ScoreValue` com a pastilla per llindar de `scoreTone`). Buits B1/B4 i
  d'ingesta, `ErrorState` E1, bàner de pre-fits obsolets amb «Recalcula».
  Responsive: `stackBelow="md"`, cada fila és una targeta. El `select`
  d'ordenació té etiqueta: **Lighthouse accessibilitat = 100**.

- **L7 · Candidatures (llistat).** `/candidatures` sobre `DataTable` amb
  grups derivats de `stepOf(status)` («Per decidir», «Preparant», «Per
  tancar», «Tancades» plegat quan hi ha feina oberta) i ordre per termini dins
  del grup (`candidatures/groups.ts`). La **recomanació automàtica i la
  decisió humana van en columnes separades**: la decisió només surt si
  `human_decision` existeix («Pendent» en atenció si no; «Sense decisió» en
  una tancada). `submitted`, `withdrawn` i `no_go_approved` es mostren amb
  tres etiquetes diferents; `undetermined` no es mostra mai com a OK (cap
  avís, cap verd). Avisos només de camps reals (`deadline_passed`,
  `eligibility no_go`, `analysis_outdated`) i següent pas derivat només de
  l'estat. Fila navegable a `/candidatures/{id}` amb ratolí i Enter. Buit B2
  amb «Veure les N amb més encaix» (`matches/summary.candidates`) o «Veure
  Oportunitats». Responsive: `stackBelow="md"`, cada candidatura és una
  targeta.

- **L9 · Fitxa de licitació.** `/licitacions/{id}` té una **capçalera
  persistent de decisió** (breadcrumb, títol, procediment · tipus · lots ·
  publicació · font, estat, «Publicada N cops», i les metadades Termini ·
  Encaix · Requisits · Import · Candidatura, totes de camps reals;
  `licitacions/[tenderId]/header.ts`) i **quatre pestanyes Resum / Plecs /
  Requisits / Historial** amb `Tabs` i estat **persistent a `?tab=`**
  (navegació directa, clic i recàrrega). La **candidatura humana** (estat i
  `human_decision` de `WorkspaceDetail`, només si existeix) va separada de la
  recomanació i del pre-fit; un termini nul és desconegut i `undetermined`
  diu «encara no avaluats». **Plecs i Requisits només s'han reubicat** dins
  de les pestanyes amb els components i handlers actuals (processat,
  JobRequest, preview → confirm, revisions, cites, compliment): L10 i L11 els
  redissenyen. L'**Historial** només mostra dades existents (canvis
  detectats, publicacions de la font, traçabilitat). `WorkspaceLauncher`
  accepta la candidatura ja consultada per la pàgina («Obre candidatura» /
  «Continua la candidatura»); `useResource` exposa `status` per al 404 (E3).
  L'**exempció d'ESLint de la fitxa s'ha eliminat**; queden les de
  `ComplianceSection.tsx` i `RequirementAnalysis.tsx` fins a L11.

- **L10 · Plecs.** La pestanya Plecs de la fitxa és una `DataTable`
  (selecció · Document · Text extret · Lectura · Acció) amb `StatusPill` de
  `DOCUMENT_STATE_LEVEL` i el peu «N triats · Processa N documents»
  (`licitacions/[tenderId]/documents.ts`). La **selecció només governa el
  processament**; no limita quins documents utilitza l'anàlisi de requisits:
  **C-1 continua documentat** i la pantalla ho diu amb aquestes paraules.
  **JobRequest i polling preservats** (`useJobRequest` de
  `process_tender_documents`, mateix `onFinished`, `reloadLifecycle` en
  acabar, seguiment reprès en recarregar); **processament individual i
  múltiple preservats** (una sola feina per a N documents amb
  `requestProcessing`); **`partial` i `failed` preservats i visibles**
  (bàner d'atenció; `ErrorState` amb el títol del diccionari i el codi, l'id
  i l'hora només al detall tècnic). **Errors humanitzats**: els codis reals de
  descàrrega i extracció tenen frase i el codi va al `title`. **Permisos
  visibles i desactivats**: sense `can_process_documents`, botons desactivats
  i `PermissionNote`. **Text extret i versions coherents**: breadcrumb amb
  `?tab=plecs`, bàner de lectura (QA i escaneig), targeta de versió, llista
  de versions amb la revisió d'extracció de cada una i la revisió citada
  explicada quan no és la vigent; el model d'extracció no canvia. **C-6
  continua documentat**: cap columna «Què fixa».

- **L11 · Requisits + Compliment.** Els dos blocs de la pestanya Requisits
  amb el sistema aplicat, i **`RequirementAnalysisSection` i
  `ComplianceSection` continuen separats**: la **taula unificada del mockup 05
  queda ajornada a Sprint 2**. **Preview → confirm preservat** (cap botó que
  analitzi directament; `AnalysisQuotePanel` com a bloc de confirmació amb
  `PermissionNote` i `confirm_roles`). Cada requisit i cada avaluació porten
  la **cita amb document, pàgina i revisió d'extracció** (`EvidenceRef`, enllaç
  a la revisió citada) i **`Attribution` segons l'origen real**: `ai` (amb la
  confiança en text) per a l'extracció, `rule` per al càlcul determinista,
  `human` (nom i data) per a la revisió humana, amb el resultat automàtic
  original sempre a sota. **Bloquejos, per revisar i indeterminat
  diferenciats**: grups Bloquegen → Possibles → Per revisar → Sense dades →
  Acreditats (plegat); `undetermined` en neutre amb bàner i comptadors
  apagats, mai verd; «Sense dades» no és un no; la recomanació és del motor i
  la decisió no viu aquí. **`partial` i `failed` visibles** (parcial mai «a
  punt», fallada com a `ErrorState` del diccionari amb l'anàlisi anterior
  intacta i el codi al detall tècnic; en curs amb progrés real). Codis
  interns només al `title` o al detall tècnic. **Les dues últimes exempcions
  d'ESLint s'han retirat: `.eslintrc.json` queda sense cap override
  temporal.**

- **L12 · Candidatura · stepper + Decidir.** `/candidatures/{id}` com a
  **stepper Decidir → Preparar → Tancar** amb enllaços reals a `?pas=`
  (enllaç directe, refresc, enrere i endavant funcionen; sense `?pas=` la
  primera càrrega fixa a la URL el pas real). El **progrés del stepper es
  deriva del backend** (`stepOf(status)`, `human_decision`, `readiness_blockers`),
  mai d'un estat local. **Porta humana:** un **GO humà registrat** pel backend
  refresca el workspace i porta a `?pas=preparar`; la **recomanació automàtica
  mai avança el flux** (és text amb `Attribution rule`, mai un botó); **NO-GO i
  Ajornar no van a Preparar**; un POST fallit no canvia de pas. **Contracte real
  de `reason`** (`DecisionRequest`): opcional, màxim 4000 caràcters, obligatori
  només per a GO amb avisos oberts (`go_requires_reason`; 422
  `decision_reason_required` amb els codis); NO-GO i `defer` no en demanen
  mai; el backend fa la fotografia dels avisos en registrar-la. **Decisions
  append-only**: cap endpoint per editar ni revertir; l'historial es mostra en
  l'ordre del backend amb «Vigent» a la primera. **Un sol bàner** per pàgina:
  anul·lada > lot retirat > termini passat > decisió caducada > anàlisi
  endarrerida > bloqueigs confirmats > avisos, amb la resta de motius a dins.
  «Què bloqueja» només amb el contracte real (comptadors dels obligatoris,
  avisos oberts, termini); `undetermined` = «Cap requisit avaluat», mai verd.
  **C-3 continua**: el nom de cada bloqueig no és al workspace; enllaç a la
  pestanya Requisits. **`can_decide === false`** → els tres botons visibles i
  desactivats dins un `PermissionNote` amb els rols reals de `DECISION_ROLES`
  (propietari, administrador, responsable de candidatures). Preparar i Tancar
  conserven les seccions actuals fins a L13/L14.

Verificació en tancar L12: `npm test` (260 proves), `npm run typecheck`,
`npm run lint`, `npm run build` i `git diff --check` en verd.

## Accessibilitat

Lighthouse (accessibilitat) a `/licitacions`, sobre el build de producció:
**100** des de L6 (95 fins a L5; l'únic audit que fallava era el `select`
d'ordenació sense etiqueta).

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
| Barril amb `EmptyState` tolerant → estricte | L15 |
| Esborrar `*_TONES`, `deadlineTone`, `Badge` (ja sense usos a les pàgines del perfil), `AppShell.tsx` i `/ui-preview` | L15 |
| Captures `after/` i comparació amb `baseline/` | L15 |
