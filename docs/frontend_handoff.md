# Traspàs per al redisseny frontend

Estat de referència: **commit `c85a8a1`** (fases 7A i 7B tancades i publicades).

Aquest document descriu el que hi ha, el que no es pot trencar i els problemes
d'experiència coneguts. **No proposa canvis de codi**: és el punt de partida del
redisseny.

---

## 1. Producte

### Què és Licitacions

SaaS B2B per detectar, analitzar i preparar candidatures a licitacions públiques.
La proposta de valor no és "més licitacions", sinó **FIT + EVIDÈNCIA + WORKFLOW**:

* **Fit**: quines licitacions encaixen amb l'empresa (pre-fit determinista sobre
  CPV, territori, import i terminis).
* **Evidència**: què exigeix el plec, amb cita i pàgina, i si l'empresa ho pot
  acreditar amb els seus documents.
* **Workflow**: de l'oportunitat a la decisió humana Go/No-Go, al dossier
  documental i a l'aprovació final del paquet.

### Usuari objectiu

Empreses que es presenten regularment a licitacions públiques (pilot: una empresa
real, Indaleccius). Perfils dins de l'empresa:

| Rol (`MembershipRole`) | Què fa a l'eina |
| --- | --- |
| `owner`, `admin` | Tot, incloent-hi decidir, aprovar i fer despesa d'anàlisi |
| `bid_manager` | Porta la candidatura: decideix Go/No-Go, aprova el paquet, llança anàlisis |
| `technical` | Prepara la candidatura (checklist, tasques, dossier); per defecte no decideix ni fa despesa |
| `viewer` | Llegeix |

Els rols que poden decidir, aprovar, analitzar o processar venen del backend (i en
part de configuració). **La UI no els ha de codificar**: ho demana a l'API.

### Problema que resol

Preparar una candidatura avui vol dir llegir plecs de desenes o centenars de
pàgines, extreure'n a mà la solvència, les assegurances, l'experiència i els
criteris, contrastar-ho amb la documentació de l'empresa, i decidir si val la pena
presentar-se abans del termini. És lent, propens a errors i poc traçable. L'eina
automatitza la lectura i el contrast **sense amagar-ne mai la font ni prendre la
decisió per l'usuari**.

---

## 2. Estat actual

### Funcionalitats implementades

* **Perfil d'empresa i evidències**: dades generals, serveis, àmbit geogràfic, CPV,
  solvència, certificacions, registres, assegurances (amb límits), equip humà,
  experiència (projectes) i documents acreditatius amb vincles a cada element.
  Completitud del perfil per àrees.
* **Ingestió PLACSP** i feed d'oportunitats amb filtres, modes i ordenació.
* **Pre-fit determinista** per licitació i per lot, explicable per dimensió.
* **Documents de licitació**: processat (descàrrega + extracció de text per
  pàgina), versions, QA d'extracció i visor del text.
* **Anàlisi de requisits amb IA** (requisits i criteris d'adjudicació), sempre amb
  cita i pàgina; revisió humana per requisit.
* **Control de cost (7B)**: preview del volum (documents, pàgines, chunks,
  estimació de tokens) i confirmació explícita abans d'analitzar; límits de pilot
  i permisos.
* **Feina en segon pla (7A)**: processar i analitzar no bloquegen cap petició; la
  UI en fa seguiment amb polling.
* **Compliment determinista** empresa ↔ requisits: elegibilitat, bloquejos
  potencials i confirmats, revisió humana per avaluació.
* **Cicle de vida de la licitació**: canvis publicats (materials i informatius),
  anul·lació, `analysis_outdated` quan el plec ha canviat.
* **Candidatura (workspace)**: checklist, tasques, notes, comptadors de bloquejos.
* **Go/No-Go humà**: signat, datat, motivat, immutable, amb historial.
* **Dossier documental** i **readiness** (què falta, sense puntuació opaca).
* **Paquet de presentació** congelat amb manifest i **aprovació final humana**.

### Flux principal complet

```
Perfil d'empresa + documents
        │
        ▼
Oportunitats (feed + pre-fit) ──► Fitxa de licitació
                                     │
                    ┌────────────────┼──────────────────────────┐
                    ▼                ▼                          ▼
          Processar documents   Preview + confirmació    Obre candidatura
          (JobRequest, polling) → Anàlisi de requisits   (workspace)
                                   (JobRequest, polling)        │
                                     │                          │
                                     ▼                          ▼
                              Compliment empresa ─────► Go/No-Go humà
                                                               │
                                                               ▼
                                                  Dossier + readiness
                                                               │
                                                               ▼
                                              Paquet congelat + aprovació final
```

La presentació a la plataforma pública **no** és a l'eina: acaba a l'aprovació
final.

### Què està estable

* **Contractes d'API** de totes les pantalles existents, amb tests backend (SQLite
  i PostgreSQL) i suites verdes a `c85a8a1`.
* **Model de permisos i multi-tenant**, amb tests d'aïllament.
* **Fluxos de feina llarga** (7A) i **confirmació de cost** (7B).
* **Semàntica dels estats** (pre-fit, elegibilitat, bloquejos, readiness,
  workspace, jobs): els textos i els tons es poden canviar; el significat no.

Menys madur, i per tant més obert al redisseny: la presentació visual, la
jerarquia de cada pantalla i la navegació.

---

## 3. Arquitectura frontend

### Stack

* **Next.js 15 (App Router)**, React 19, TypeScript, **Tailwind CSS 4** amb tokens a
  `src/app/globals.css` (`@theme`: `canvas`, `surface`, `ink`, `muted`, `brand`,
  `success`, `warning`, `danger`, `sidebar`…). Cap llibreria de components externa.
* Totes les pàgines de l'app són **client components** (`"use client"`).
* Idioma de la interfície: **català**.

### Accés a l'API

* El navegador **mai** parla amb FastAPI directament: `next.config.ts` reescriu
  `/bff/*` cap a `API_INTERNAL_URL/api/v1/*`. La sessió és una **cookie httpOnly**
  first-party (`licitacions_session`).
* `src/middleware.ts`: barrera de primera línia (sense cookie → `/login`). La
  validació real és al backend.
* `src/lib/api.ts`: `api.get/post/patch/put/delete/upload/download` i `ApiError`
  (`status`, `code`, `message`, `fields`, `details`). **Els errors porten un `code`
  estable** i sovint `details` útils (per exemple, el preview nou a 7B).

### Estat i dades

* `SessionProvider` (`src/components/SessionProvider.tsx`): usuari, empresa activa
  (`companyId`, desada a `localStorage`), `membership` (rol), llista d'empreses,
  `selectCompany`, `logout`. `useCompanyPath()` construeix rutes
  `/companies/{companyId}/…`.
* `src/lib/hooks.ts`:
  * `useResource<T>(path)` — GET amb `data`, `loading`, `error`, `reload`
    (`path = null` = no carregar).
  * `useMutation()` — `run(action)` amb `saving`, `error`, `fieldErrors`, `apiError`.
* `src/lib/jobs.ts`: `useJobRequest(companyId, tenderId, jobType, onFinished)` —
  seguiment amb polling cada 2,5 s i represa en recarregar; `previewAnalysis`,
  `confirmAnalysis`, `requestProcessing`, `jobErrorMessage`.
* Cap store global (ni Redux ni React Query). Cada secció carrega el que necessita.

### Rutes

| Ruta | Pantalla |
| --- | --- |
| `/login` | Accés |
| `/dashboard` | Resum de l'empresa: completitud, pre-fit, caducitats |
| `/licitacions` | Feed d'oportunitats |
| `/licitacions/[tenderId]` | Fitxa de licitació (documents, anàlisi, compliment, canvis…) |
| `/licitacions/[tenderId]/documents/[documentId]` | Text extret d'un plec, per pàgina |
| `/candidatures` | Llistat de candidatures |
| `/candidatures/[workspaceId]` | Workspace: Go/No-Go, dossier, readiness, paquet, checklist… |
| `/empresa` (+ `serveis`, `territoris`, `cpv`, `solvencia`, `certificacions`, `registres`, `assegurances`, `equip`, `equip/[personId]`) | Perfil d'empresa |
| `/experiencia`, `/experiencia/[projectId]` | Projectes d'experiència |
| `/documents` | Documents acreditatius de l'empresa |

### Components principals

| Fitxer | Paper |
| --- | --- |
| `components/AppShell.tsx` | Barra lateral (grups General, Empresa, Licitacions, Acreditació), selector d'empresa, logout, menú mòbil |
| `components/ui.tsx` | Kit propi: `Button`, `LinkButton`, `Input`, `Textarea`, `Select`, `Field`, `Checkbox`, `Card`, `CardHeader`, `PageHeader`, `Badge`, `Spinner`, `Skeleton`, `LoadingRows`, `EmptyState`, `ErrorNotice`, `ResultNotice`, `Table/Th/Td/Tr`, `cn` |
| `components/Modal.tsx` | `Modal`, `ConfirmDialog` |
| `components/ResourceTable.tsx` | Taula CRUD genèrica del perfil |
| `components/EvidencePanel.tsx` | Vincles document ↔ element del perfil |
| `components/PreFit.tsx` | `PreFitBadge`, `PreFitSummary`, `PreFitPanel`, `PreFitDisclaimer` |
| `components/RequirementAnalysis.tsx` | Secció d'anàlisi del plec: requisits per categoria amb cita, revisió, criteris |
| `components/AnalysisQuotePanel.tsx` | Panell de confirmació del volum (7B, funcional, sense disseny) |
| `components/ComplianceSection.tsx` | Compliment: elegibilitat, bloquejos, avaluacions i revisió |
| `components/TenderChangeHistory.tsx` | Canvis publicats (materials vs informatius) |
| `components/WorkspaceLauncher.tsx` | Obre / accedeix a la candidatura (idempotent) |
| `components/WorkspaceDecision.tsx` | `DecisionPanel` (Go/No-Go) i `DecisionHistory` |
| `components/WorkspaceWork.tsx` | `ChecklistSection`, `TasksSection`, `NotesSection`, `BlockerCounters` |
| `components/WorkspaceDossier.tsx` | Dossier documental, readiness i avanç a revisió final |
| `components/SubmissionPackage.tsx` | Paquet congelat, manifest i aprovació final |

Etiquetes i tons dels estats: `lib/tenders.ts`, `lib/matching.ts`,
`lib/requirements.ts`, `lib/compliance.ts`, `lib/workspace.ts`,
`lib/tender-changes.ts`, `lib/format.ts`.

### Patrons actuals

* **Pàgina = columna de `Card`s** apilades, amb `CardHeader` (títol, descripció,
  acció).
* **Una secció, una crida**: cada component fa el seu `useResource` i el seu
  `reload`; les accions d'una secció recarreguen les dependents (per exemple,
  una anàlisi nova recarrega compliment).
* **Primera càrrega amb `Skeleton`**, recàrregues sense desmuntar (evita
  parpelleig i conserva missatges locals).
* **Errors**: `ErrorNotice` amb el missatge del backend i, a vegades, "Torna-ho a
  provar"; errors d'acció dins la targeta.
* **Estats com a `Badge`** amb mapes `*_LABELS` / `*_TONES`.
* **Textos d'honestedat** incrustats: disclaimers de pre-fit, "Possible bloqueig"
  vs "Bloqueig confirmat", "Simulació de desenvolupament" quan l'anàlisi no és real.

---

## 4. Pantalles existents

### 4.1 Dashboard

* **Ruta**: `/dashboard`
* **Objectiu**: saber d'un cop d'ull l'estat del perfil i de les oportunitats.
* **Components**: `PageHeader` (nom de l'empresa), `CountCard`, targeta
  "Completitud del perfil" (per àrees, amb enllaç a cada una), targeta "Pre-fit
  sobre les licitacions ingerides" + `PreFitDisclaimer`, targeta "Avisos de
  caducitat" (`Table`).
* **Dades**: `/companies/{id}/summary`, `/companies/{id}/matches/summary`.
* **Problemes UX detectats**:
  * No hi ha cap "què he de fer avui": ni candidatures amb termini proper, ni
    decisions pendents, ni anàlisis en marxa.
  * La completitud del perfil ocupa el lloc principal, però per a l'usuari recurrent
    el que importa són les oportunitats i les candidatures.
  * Estats buits ("Pre-fit encara no calculat") sense una acció clara.

### 4.2 Feed d'oportunitats

* **Ruta**: `/licitacions`
* **Objectiu**: trobar les licitacions que val la pena mirar.
* **Components**: `PageHeader` "Oportunitats", xips de mode (Totes, Candidates,
  Encaix baix, Descartades, Sense dades suficients, Fora de termini) amb
  recomptes, botó de recàlcul del pre-fit, targeta de filtres (cerca, CPV, NUTS,
  estat, procediment, tipus de contracte, lots, import mínim/màxim, publicació,
  termini, només obertes), ordenació, `Table` (Pre-fit, Licitació, Organisme,
  Import, Publicació, Termini, Estat), paginació Anterior/Següent (25 per pàgina),
  `PreFitBadge`, `DeadlineCell` (colors per dies restants).
* **Dades**: `/companies/{id}/matches?…`, `/tenders/facets`, `/matches/summary`,
  `POST /matches/recalculate`.
* **Problemes UX detectats**:
  * Onze filtres sempre desplegats: molta càrrega abans de veure cap resultat.
  * La taula té set columnes denses; el títol de la licitació (el que més informa) competeix amb metadades.
  * El pre-fit és una xifra i un badge; el motiu només es veu entrant a la fitxa.
  * Paginació només endavant/enrere, sense total visible al peu.
  * El recàlcul del pre-fit és manual i la fila "desactualitzada" no destaca.
  * No hi ha vistes desades ni cap manera de marcar "no m'interessa".

### 4.3 Fitxa de licitació

* **Ruta**: `/licitacions/[tenderId]`
* **Objectiu**: entendre una licitació i decidir si obrir-hi candidatura.
* **Components** (en aquest ordre, tot en una sola columna): `PageHeader` amb
  títol i `WorkspaceLauncher`; `PreFitPanel`; Resum; Organisme contractant; CPV;
  Lots (`Table`); **Documents**; **Anàlisi del plec** (`RequirementAnalysisSection`);
  **Compliment de l'empresa** (`ComplianceSection`); Canvis publicats
  (`TenderChangeHistory`); Publicacions de la font (revisions); Traçabilitat.
* **Dades**: `/tenders/{id}`, `/tenders/{id}/revisions`, `/tenders/{id}/history`,
  `/companies/{id}/matches/by-tender/{id}`, `/tenders/{id}/analysis/requirements`,
  `/companies/{id}/tenders/{id}/compliance`, `/companies/{id}/analysis-limits`,
  job-requests.
* **Problemes UX detectats**:
  * **Pàgina molt llarga** (una dotzena de targetes) sense pestanyes, índex ni
    àncores. El que decideix (anàlisi i compliment) queda sota metadades.
  * Tres nocions d'"estat" conviuen sense jerarquia: estat de la licitació,
    pre-fit, estat de l'anàlisi, elegibilitat, canvis publicats.
  * "Publicacions de la font" i "Traçabilitat" són informació tècnica al mateix
    nivell que la resta.
  * `processing_status` de les revisions es mostra com a codi cru.

### 4.4 Documents de la licitació

* **Ruta**: secció "Documents" de la fitxa, i visor
  `/licitacions/[tenderId]/documents/[documentId]`.
* **Objectiu**: processar els plecs (descàrrega i text) i poder verificar a ull el
  text extret.
* **Components**: llista amb `Checkbox` (selecció múltiple), tipus, mida, pàgines,
  data, `Badge` d'estat documental, enllaç "Text", "Origen ↗", botó
  Processa/Reprocessa; al visor: "Versió actual" (metadades, QA i avisos per
  pàgina) i "Text extret" per pàgina (`?page=` i `?extraction=`).
* **Feina en segon pla**: `requestProcessing` + `useJobRequest`; un sol job per
  a tota la selecció; `partial` s'avisa amb un missatge general.
* **Problemes UX detectats**:
  * L'error d'un document es mostra com `Error: {error_code}` (codi cru).
  * Mentre el job corre, els botons queden desactivats però el progrés per document
    no es veu; després de recarregar no se sap quins documents s'estaven processant.
  * Si el rol no pot processar, els botons es desactiven amb una línia de text poc visible.
  * El visor és una comprovació tècnica (versions, SHA, parser) més que una lectura
    útil; els avisos de QA no connecten amb l'impacte a l'anàlisi.

### 4.5 Anàlisi de requisits

* **Ruta**: secció "Anàlisi del plec" de la fitxa.
* **Objectiu**: saber què exigeix el plec, amb cita i pàgina verificables.
* **Components**: `CardHeader` amb `Badge` d'estat (No analitzat, Analitzant,
  Complet, Parcial, Error, Obsolet) o de base de l'anàlisi (Simulació, Motor no
  configurat); text "Analitzant X/Y"; botó "Analitza requisits" / "Torna a
  analitzar"; **`AnalysisQuotePanel`** (preview i confirmació); llista de requisits
  per categoria amb obligació, valor, cita (`blockquote`), "Veure font ↗"
  (a la pàgina i extracció exactes) i revisió (verificat / cal revisió / rebutjat);
  criteris d'adjudicació; resum del run (chunks completats/total…).
* **Problemes UX detectats**:
  * Els requisits són una llista llarga i uniforme: un bloquejant i un requisit
    informatiu tenen el mateix pes visual.
  * La confiança de l'IA no es comunica: no es distingeix verificat per una persona
    de proposat pel model a primera vista, ni hi ha agregats (quants revisats).
  * `partial` / `failed` es veuen com a badge; el detall (`error_code: error_message`)
    surt com a text tècnic cru.
  * El panell de confirmació és funcional però pla: volum, límit, reutilització i
    permisos llegits com a text corrent.
  * Els codis de 7B (`preview_changed`, `quote_expired`, `company_active_limit`…)
    tenen missatge, però no una presentació pròpia.

### 4.6 Compliment de l'empresa

* **Ruta**: secció "Compliment de l'empresa" de la fitxa.
* **Objectiu**: saber si l'empresa pot acreditar cada requisit i si hi ha bloquejos.
* **Components**: botó "Analitza compliment" / "Recalcula"; elegibilitat global
  (`Badge`), comptadors (cal revisió, bloquejos potencials, confirmats); avís de
  compliment endarrerit; "Requisits avaluats (N)" amb resultat, obligació,
  "Possible bloqueig" / "Bloqueig confirmat", "Revisat per una persona" i revisió.
* **Problemes UX detectats**:
  * Dues seccions correlatives (anàlisi i compliment) parlen dels mateixos requisits
    en dos llocs; l'usuari ha de creuar-les mentalment.
  * Els motius (`EXPLANATION_LABELS`) són textos curts; què cal fer per resoldre un
    bloqueig (quin document falta, on pujar-lo) no s'enllaça.
  * El recàlcul és manual i fàcil d'oblidar després d'una anàlisi nova.

### 4.7 Candidatures (llistat)

* **Ruta**: `/candidatures`
* **Objectiu**: veure totes les candidatures obertes.
* **Components**: `PageHeader`, `Table` (Licitació, Termini, Pre-fit,
  Recomanació, Decisió, Estat), `EmptyState` "Cap candidatura oberta".
* **Problemes UX detectats**:
  * Sense filtres ni agrupació per fase (pendent de decisió, preparant, a revisió
    final…); deu estats de workspace en una sola columna.
  * El termini, que és el que més urgeix, no mana l'ordre ni la visualització.

### 4.8 Candidatura (workspace): Go/No-Go

* **Ruta**: `/candidatures/[workspaceId]`
* **Objectiu**: decidir si presentar-se, amb la informació al davant.
* **Components**: enllaç de tornada, `PageHeader` (títol, lot, estat), avisos
  (termini passat, licitació anul·lada, `analysis_outdated`), targeta de mètriques
  (Termini, Recomanació, Elegibilitat, Decisió humana, Dossier), **`DecisionPanel`**
  (GO / NO-GO amb motiu; motiu obligatori si hi ha avisos oberts; avís si
  l'elegibilitat és indeterminada o l'anàlisi no és real), `BlockerCounters`, i al
  final `DecisionHistory`.
* **Permisos**: el backend diu si l'usuari pot decidir (rols de decisió).
* **Problemes UX detectats**:
  * La recomanació automàtica i la decisió humana són a prop visualment; cal deixar
    molt clar que només la humana és oficial (ja ho diu el text, però no la jerarquia).
  * Per decidir cal pujar i baixar per la pàgina (bloquejos, dossier i anàlisi són en
    seccions separades o a la fitxa de licitació).
  * L'historial de decisions és al final de tot.

### 4.9 Dossier documental

* **Ruta**: secció `DossierSection` del workspace.
* **Objectiu**: quin document acredita cada requisit, i si és vigent al termini.
* **Components**: botó de sincronització; cobertura; llista d'ítems amb cobertura,
  validesa (caducitat mesurada contra el termini), pertinença (proposta o confirmada
  per una persona, amb nota), accions "Confirma pertinença" i "Crea tasca".
* **Problemes UX detectats**:
  * Molts badges per ítem (cobertura, validesa, pertinença, superats) sense un
    resum de "què falta".
  * La sincronització manual no explica què canviarà abans de fer-la.
  * Els ítems a l'històric (`superseded_count`) es mencionen però no es fan visibles fàcilment.

### 4.10 Readiness

* **Ruta**: dins de `DossierSection` (estat, bloquejos) i avanç a revisió final.
* **Objectiu**: saber si la candidatura està a punt per a la revisió final, sense
  cap puntuació opaca.
* **Components**: `Badge` d'estat de readiness, llista de bloquejos
  (`READINESS_BLOCKER_LABELS`), botó "avança a revisió final" quan
  `ready_for_final_review`.
* **Problemes UX detectats**:
  * La readiness viu dins del dossier, i el paquet la torna a mostrar: dos llocs per
    al mateix concepte.
  * Els bloquejos són una llista de frases; no enllacen a on es resol cadascun.

### 4.11 Paquet de presentació i aprovació final

* **Ruta**: secció `SubmissionPackageSection` ("Revisió final") del workspace.
* **Objectiu**: congelar una revisió concreta de la candidatura i que una persona
  autoritzada l'aprovi.
* **Components**: botó de congelar paquet, detall del paquet (estat, bloquejos de
  readiness al moment de congelar, manifest), aprovació final amb signatura
  (`can_sign` segons rol), historial d'aprovacions.
* **Problemes UX detectats**:
  * "Congelar" i "aprovar" són conceptes nous per a l'usuari; falta explicar en
    context què vol dir cadascun i què no es podrà canviar.
  * El manifest es presenta de manera tècnica.
  * No hi ha ZIP descarregable (limitació coneguda del backend).

### 4.12 Altres pantalles (fora del focus, però afectades pel redisseny)

* **Perfil d'empresa** (`/empresa/*`, 9 pantalles), **Experiència**, **Documents**:
  formularis i taules CRUD (`ResourceTable`, `EvidencePanel`, `Modal`). Coherents
  entre si; certificacions no fa servir `ResourceTable` (deute conegut).
* **Login**: formulari simple.
* El `<title>` global encara diu "Licitacions · Perfil d'empresa".

---

## 5. Invariants que NO es poden trencar

### Multi-tenant

* Tota dada d'empresa es demana a `/companies/{companyId}/…` amb l'empresa activa
  de `SessionProvider`. **Mai** barrejar dades de dues empreses a la mateixa vista.
* Canviar d'empresa ha de recarregar totes les dades dependents.
* Licitacions, documents de licitació, anàlisi de requisits i canvis publicats són
  **globals** (`/tenders/…`); pre-fit, compliment, candidatures, peticions de feina,
  previews i límits són **de l'empresa**.
* Una empresa no ha de poder inferir activitat d'una altra: la UI no ha d'afegir
  missatges tipus "algú ja ho està analitzant". L'API ja respon igual en tots dos casos.

### Permisos

* Els permisos els decideix el backend. La UI **els llegeix**, no els replica:
  * `GET /companies/{id}/analysis-limits` → `can_confirm`, `can_process_documents`,
    límits.
  * Workspace → si l'usuari pot decidir; paquet → `can_sign`.
* Amagar o desactivar un botó és comoditat; el 403 de l'API continua sent la
  garantia. Una acció denegada ha d'explicar-se, no fallar en silenci.

### JobRequest

* Processar documents i analitzar requisits **no** s'executen en la petició HTTP.
  La UI crea una `JobRequest` i en segueix l'estat.
* Els POST síncrons antics responen **410** i el POST directe d'anàlisi respon
  **409 `analysis_confirmation_required`**: no s'han de tornar a fer servir.
* Estats de la petició vistos pel tenant: `accepted` (a la cua o executant-se),
  `succeeded`, `failed`, `cancelled`; `outcome`: `executed`, `reused`,
  `reused_existing`, `partial`. **No** hi ha distinció visible entre "a la cua" i
  "executant-se", a propòsit.
* No s'exposen ni s'han de mostrar identificadors interns de l'execució global.

### Polling

* `useJobRequest` consulta cada 2,5 s mentre l'estat és `accepted`, i **reprèn el
  seguiment en recarregar la pàgina** (peticions actives de l'empresa).
* El progrés (`progress.done/total`) és relatiu a la petició i pot ser `null`: la UI
  no ha de mostrar un 0 % fals ni inventar-se una barra.
* Un error de xarxa puntual no atura el seguiment.

### Estats `partial` i `failed`

* **`partial` no és `completed`**: una anàlisi parcial té chunks fallits i part del
  plec no llegit. S'ha de veure com a tal, mai com un èxit verd.
* **`failed` porta un `error_code` estable**: `input_changed`, `analysis_too_large`,
  `worker_lost`, `all_chunks_failed`… (vegeu `lib/jobs.ts`). El missatge ha de dir
  què pot fer l'usuari.
* Anàlisi no real (`simulation`, `provider_not_configured`) **no** es pot presentar
  com a "Complet".
* "Possible bloqueig" (requisit no verificat) no es pot presentar com a bloqueig
  confirmat, ni el pre-fit com a elegibilitat.

### Confirmació abans d'analitzar

* Cap anàlisi es llança sense **preview** (`POST …/analysis-quotes`) i
  **confirmació** (`POST …/analysis-quotes/{id}/confirm`).
* El preview s'ha de mostrar abans de confirmar: documents, pàgines (i avís si es
  talla), chunks a enviar i màxim, estimació de tokens o "sense històric", perfil i
  model, reutilització d'un resultat vigent.
* Si la confirmació respon **409 `preview_changed` o `quote_expired`**, el preview
  nou arriba a `error.details.quote` i **s'ha de tornar a mostrar abans de confirmar**.
  Mai reconfirmar automàticament.
* `allowed: false` bloqueja la confirmació. Cap import en diners a la UI (encara no
  hi ha billing).

### APIs existents

* El redisseny és **només de frontend**: cap endpoint, cap camp ni cap codi d'error
  canvia. Si una pantalla nova necessita dades que avui no hi són, s'ha d'acordar
  abans com a canvi de backend separat.
* Rutes del BFF (`/bff/*`), cookie httpOnly i `ApiError` (`code`, `details`) es
  mantenen.

### Altres principis del producte que la UI ha de preservar

* Cada requisit visible porta la seva **cita i pàgina** i l'enllaç a la font.
* La **decisió humana** Go/No-Go i l'**aprovació final** són sempre accions explícites
  d'una persona, separades de qualsevol recomanació automàtica.
* Els canvis publicats d'una licitació i `analysis_outdated` no es poden amagar.

---

## 6. Objectius del redisseny

* **SaaS B2B enterprise**: aparença i comportament d'eina professional de feina
  diària (consistència, navegació previsible, estats clars, accessibilitat), no de
  prototip intern.
* **Més confiança en l'IA**: que es vegi sempre d'on surt cada afirmació, què ha
  verificat una persona, què és parcial i què no s'ha llegit; que la confirmació de
  cost sigui clara i no alarmant.
* **Menys càrrega cognitiva**: menys informació simultània, divulgació progressiva,
  una acció principal per pantalla, i textos que diguin què fer.
* **Millor jerarquia d'informació**: el que decideix (termini, bloquejos,
  elegibilitat, decisió pendent) per damunt del que és context (metadades,
  traçabilitat, historial tècnic).

---

## 7. Problemes UX coneguts (transversals)

### Taules

* Taules denses (13 px) amb moltes columnes a feed, lots, candidatures i perfil.
* Sense ordenació per columna ni columnes configurables; ordenació només al feed
  amb un `Select`.
* Paginació mínima (Anterior/Següent) i només al feed; la resta de llistats
  retornen tot.
* Sense accions per fila consistents ni selecció múltiple, excepte documents de licitació.

### Densitat

* Pàgines de detall (fitxa de licitació, workspace) com a columnes llargues de
  targetes sense navegació interna.
* Molts `Badge` per element (estat, obligació, bloqueig, revisió, validesa,
  pertinença) que competeixen entre si.
* Metadades tècniques (SHA, parser, revisions de la font) al mateix nivell que la
  informació de decisió.

### Estats

* Diversos sistemes d'estat que conviuen: estat de licitació, pre-fit (6 valors),
  anàlisi (6), base de l'anàlisi (3), elegibilitat, bloquejos (potencial/confirmat),
  workspace (10), readiness, dossier (cobertura, validesa, pertinença), jobs.
* Tons reutilitzats amb significats diferents segons la secció (warning = parcial,
  pendent de decisió, possible bloqueig…).
* Feina en segon pla visible només dins de la secció que l'ha llançada; no hi ha cap
  lloc global per veure què està en marxa.

### Errors

* `ErrorNotice` mostra el missatge del backend tal com arriba; alguns punts mostren
  codis crus (`Error: {error_code}`, `{error_code}: {error_message}`,
  `processing_status`).
* Els errors d'acció apareixen dins la targeta, sovint lluny del botó que els ha
  provocat.
* Els 403 i els límits del pilot (429) no tenen una presentació diferenciada.

### Empty states

* `EmptyState` sovint amb només un títol ("Sense documents publicats", "Cap
  requisit avaluat", "Pre-fit encara no calculat"), sense explicar per què ni quina
  és l'acció següent.
* Una licitació sense documents processats no guia cap a processar-los abans
  d'analitzar.

### Loading states

* Primera càrrega amb `Skeleton` d'un sol bloc gran (`h-72`, `h-96`) que no reflecteix
  l'estructura de la pàgina.
* Moltes seccions carreguen de manera independent: la pàgina "es va construint" amb
  salts de disseny.
* Accions amb `Spinner` dins del botó, sense estat intermedi a la resta de la targeta.
* Jobs llargs: només text ("Analitzant en segon pla…", "Analitzant 2/5"); sense
  indicació de temps ni de què passa si es tanca la pàgina (el seguiment es reprèn,
  però l'usuari no ho sap).
