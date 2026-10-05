# Pestanya «Anàlisi» de la licitació — disseny

- **Data:** 2026-10-02
- **Branca:** `engine/phase3a-analysis-workspace` (des de `5502ef5`)
- **Estat:** aprovada conceptualment, amb els ajustos de la revisió incorporats (v3: text canònic de les explicacions del backend)
- **Abast:** fase 3A. Mostra l'anàlisi 0051/0052 que ja existeix. No és la fase 3B: aquí no hi ha ProposalDraft ni FitScore.

## 1. Objectiu

Donar a l'usuari un espai de treball per **entendre i treballar una licitació** a partir de l'anàlisi determinista que ja fa el motor 0051/0052. Tuvi explica què sap, què no sap i per què, i acompanya l'usuari fins a una **decisió humana informada**. Tuvi **no recomana** presentar-s'hi o no, ni decideix el Go/No-Go.

**Criteris d'èxit:**

1. Des de la pàgina de la licitació, l'usuari veu en una sola pestanya:
   - un resum factual de l'anàlisi;
   - el que cal resoldre abans de decidir;
   - l'encaix per requisit;
   - els punts per criteri;
   - qui pot executar cada part;
   - les preguntes generades.
2. Totes les afirmacions es poden rastrejar: estat del motor → explicació humana → cita del plec quan n'hi ha → «On es resol» quan hi ha un destí inequívoc.
3. «No se sap» es presenta com a neutre i sempre amb el perquè.
4. L'usuari pot recalcular l'anàlisi i, des d'aquí, anar a la candidatura per prendre-hi la decisió.

## 2. Fora d'abast (v1)

- Cap score global, percentatge d'encaix, recomanació ni Go/No-Go automàtic.
- Cap edició de l'estructura del plec (blocs, seus, abast, fets del contracte, semàntica). L'estructura només es llegeix. No canvia la política actual d'edició, que només permet editar les licitacions manuals pròpies.
- Al bloc Execució: res de disseny de solució, selecció de proveïdors, BOM, dimensionament ni pricing.
- Cap flux persistent de respostes a les preguntes. No es parla de preguntes «obertes» ni «resoltes».
- Cap canvi a les regles del motor (`RULES_VERSION` no canvia), cap migració, cap canvi del text dels outputs del backend. Les plantilles de preguntes sense accents es deixen com estan: corregir-les serà un canvi explícit de copy amb versió pròpia.
- Cap desplegament al pilot dins d'aquest treball. Cap dada real.

## 3. Context (estat actual verificat)

- `/licitacions/[tenderId]` té quatre pestanyes a `?tab=`: `resum`, `plecs`, `requisits` i `historial`. Cap fa servir l'anàlisi 0051 ni l'estructura del plec.
- `GET /companies/{c}/tenders/{t}/analysis/current` retorna `AnalysisRunRead` (amb `stale`), o 404 si no n'hi ha cap. Qualsevol membre el pot llegir.
- No hi ha cap endpoint per executar l'anàlisi: només existeix la funció de servei `analyze_tender(session, company_id, tender_id, on=None)`. Aquesta funció no fa commit, és determinista, no fa servir LLM i reutilitza una anàlisi si les entrades són les mateixes (`AnalysisOutcome.reused`).
- `fit_assessments.requirement_id` apunta als requisits efectius del plec (extrets i manuals). Els títols, la categoria i les cites (`evidence`, amb `page_number`) surten de `GET /tenders/{t}/analysis/requirements`, la mateixa font que la pestanya Requisits.
- Els codis d'explicació (`ExplanationCode`), de regla (`RuleCode`) i els tipus de finding són un vocabulari tancat a `app/analysis/registry.py` i `app/models/enums.py`. El registre ja té **el text canònic de cada explicació** (`EXPLANATIONS: dict[str, str]`, «per a la interfície»). Com les preguntes, està escrit sense accents. Les preguntes arriben ja redactades: el camp de l'API és `QuestionRead.text`, que el router omple amb `registry.question_text(question_code, params_json)`.

## 4. Backend

### 4.1 `POST /companies/{c}/tenders/{t}/analysis/runs`

- Crida `analyze_tender`, fa commit i retorna **sempre l'anàlisi resultant** amb la mateixa forma que `GET /analysis/current`, més `reused: bool`.
- **Codi d'estat:** 201 si l'anàlisi és nova i 200 si s'ha reutilitzat. En tots dos casos el cos és igual.
- **Síncron.** Sense cua de jobs, sense pressupost ni previsualització: no hi ha cap crida a model.
- **Permisos:** fa servir la primitiva existent `review_actions.assert_can_review(role)`, que exigeix `REQUIREMENT_EDIT_ROLES` (propietari/ària, administració, responsable de candidatures, tècnic/a). Un lector rep 403 `permission_denied`. No es crea cap llista de rols nova.
- **Visibilitat:** la mateixa porta que la lectura (`_visible_tender`). Una licitació d'un altre tenant retorna 404.
- **Precondicions:** `analyze_tender` mai no falla per manca d'entrades: sense requisits produiria una anàlisi buida, que seria enganyosa. Per això la guarda és a l'endpoint, abans de cridar el motor.
  - **Única precondició v1:** la licitació ha de tenir com a mínim un requisit efectiu, sigui extret de l'extracció vigent o manual no arxivat. És el mateix conjunt que llegeix el motor (`manual_requirement.effective_requirements`).
  - Si no se satisfà, respon **409** amb el codi estable **`analysis_prerequisites_missing`** i `details.missing = ["requirements"]`.
  - `missing` és una llista tancada i documentada per poder-hi afegir altres codis en el futur. A la v1 només hi pot sortir `requirements`.
  - No es fa servir cap missatge lliure per decidir res.
  - No s'escriu cap anàlisi.
  - **Si ja hi ha una anàlisi** (per exemple, una de desactualitzada després d'arxivar tots els requisits manuals), el 409 no la toca: continua vigent i visible tal com era, i `GET /analysis/current` la segueix retornant amb `stale`.
- **Concurrència:** dues peticions simultànies amb les mateixes entrades no poden acabar en un 500.
  - La unicitat `uq_tender_analysis_runs_inputs` (empresa, licitació, `inputs_hash`) i l'índex únic parcial de l'anàlisi vigent ja garanteixen que no hi pugui haver dues anàlisis iguals.
  - **Només** un `IntegrityError` d'aquests dos índexs (`uq_tender_analysis_runs_inputs`, `uq_tender_analysis_runs_current`) es tracta com a cursa. Qualsevol altre `IntegrityError` no s'emmascara: es propaga tal com és.
  - En una cursa, l'endpoint fa rollback i torna a cridar `analyze_tender` **una sola vegada**. Aquest cop el motor troba l'anàlisi de l'altra petició, la reutilitza i la retorna com a vigent amb `reused: true`. Totes dues peticions retornen el mateix `id`.
  - Dues peticions normals simultànies **convergeixen** a la mateixa anàlisi. El 409 només és el tancament final: si el reintent topa **un altre cop amb la mateixa unicitat** (per exemple, entrades diferents que canvien enmig), respon **409 `analysis_run_conflict`**, estable, sense 500 i sense res a mitges. La UI diu «S'ha recalculat alhora des d'un altre lloc: torna-ho a provar».
- **Auditoria:** no s'escriu cap entrada a `audit_logs`. L'anàlisi de compliment, que és l'altre càlcul derivat, tampoc no n'escriu. El registre auditable és la mateixa anàlisi: és immutable i guarda `created_at`, les empremtes de les entrades i `rules_version`. La reutilització no deixa rastre propi; la UI la mostra amb `reused`.

### 4.2 Vocabulari generat per al frontend

- Un script `apps/api/scripts/export_analysis_vocabulary.py` escriu `apps/web/src/lib/analysis-vocabulary.generated.json` a partir de les fonts canòniques (`app/analysis/registry.py` i `app/models/enums.py`).
- El JSON separa dos grups:
  - **`explanations`**: per a cada `ExplanationCode`, el seu **text canònic** de `EXPLANATIONS`. La UI el mostra **tal com és**, igual que el `text` de les preguntes: el frontend no en té cap copy propi, i quan es corregeixi el copy amb versió pròpia, la UI l'heretarà en regenerar el fitxer.
  - **`rendered`**: els valors d'enum que la UI pinta i per als quals el backend no té text, i que per tant **necessiten copy al frontend**. Inclou `FindingType`, `FitResult`, `ProofStatus`, `AnalysisKnowledgeSource`, `AssessmentVerification`, `TerritoryVerdict`, `CriterionResult`, `PointsStatus`, `CandidateType`, `CandidateOrigin` i `CandidateReadiness`.
  - **`contract`**: vocabulari que l'API retorna però que la UI v1 **no pinta** com a text. Inclou `RuleCode` i `QuestionCode`; les preguntes ja arriben redactades a `text`. Serveix perquè els tipus del frontend reconeguin tots els valors possibles, sense exigir-ne copy.
- Si la UI comença a pintar un codi de `contract`, l'script l'ha de moure a `rendered`, i a partir de llavors el test n'exigeix el text.
- El fitxer és **determinista**: claus i valors ordenats, sense timestamps ni hash de la data, amb un salt de línia final.
- **Regeneració** (documentada al README de l'script i a `docs/analysis-engine.md`):

  ```
  apps/api/.venv/Scripts/python apps/api/scripts/export_analysis_vocabulary.py
  ```

  Des de l'arrel del repositori, amb la mateixa convenció que els scripts del README (`apps/api/.venv/Scripts/python …`). L'script afegeix `apps/api` al `sys.path`, com els altres scripts d'`apps/api/scripts`, i escriu el JSON en una ruta relativa al seu propi fitxer, no al directori actual.

- **Test del backend:** el JSON del repositori ha de coincidir exactament amb el que generaria l'script. Si algú afegeix un codi al motor sense regenerar, el test falla. A més, `EXPLANATIONS` ha de cobrir exactament tots els valors d'`ExplanationCode`, perquè cap codi pugui quedar sense text canònic.
- **Test del frontend:** cada valor del grup `rendered` ha de tenir text al diccionari de la UI. Si es regenera el JSON amb un codi nou d'aquest grup sense text, el test falla. Els valors de `contract` no exigeixen text, i els d'`explanations` porten el text del backend.

## 5. Frontend

### 5.1 Ubicació

- Una cinquena pestanya a `/licitacions/[tenderId]`: **«Anàlisi»** (`?tab=analisi`), entre Requisits i Historial.
- Al pas **Decidir** de `/candidatures/[workspaceId]`: `DecisionAnalysisCard`, un resum i un enllaç a la pestanya. El `DecisionPanel` existent no canvia.

### 5.2 Fitxers

- `lib/analysis.ts`:
  - tipus de l'API;
  - etiquetes dels valors d'enum (grup `rendered`);
  - accés al text canònic de les explicacions (grup `explanations`), sense reescriure'l;
  - taula de destins «On es resol» per `explanation_code` (alguns codis no en tenen cap);
  - funcions pures: ordre dels findings, agrupació per categoria, frases del resum derivades només de comptadors existents.
- `lib/analysis-vocabulary.generated.json`, generat (§4.2).
- `components/tender-analysis/`: `AnalysisTab`, `AnalysisSummary`, `ResolveBeforeDeciding`, `RequirementsFit`, `CriteriaPoints`, `Delivery`, `Questions`, `DecisionFooter`, `DecisionAnalysisCard`.
- `DecisionAnalysisCard` fa servir **exactament** les mateixes funcions de resum de `lib/analysis.ts`. No recalcula ni reinterpreta res.
  - **Mai no fa el POST de recàlcul:** ni té el botó, ni el pot provocar indirectament. Només llegeix `GET /analysis/current`.
  - **Anàlisi vigent:** el resum factual compacte (els mateixos comptadors i frases que §5.5.1, en versió curta), el nombre d'elements de «Què cal resoldre», la data i l'enllaç «Veure l'anàlisi» → `/licitacions/{t}?tab=analisi`.
  - **Desactualitzada:** el mateix resum, amb l'etiqueta «Desactualitzada» (text i no només color) i l'enllaç «Revisa i recalcula a la pestanya Anàlisi».
  - **Sense anàlisi (404):** «Encara no hi ha anàlisi d'aquesta licitació», amb l'enllaç a la pestanya. Es mostra com a estat normal, no com a error.
  - **Error de lectura:** una nota neutra («No s'ha pogut carregar l'anàlisi») i l'enllaç a la pestanya. No bloqueja el pas Decidir.

### 5.3 Dades

| Recurs | Per a què |
|---|---|
| `GET /companies/{c}/tenders/{t}/analysis/current` | L'anàlisi. **Un 404 és l'estat normal «sense anàlisi»**, no un error. |
| `GET /tenders/{t}/analysis/requirements` | Títols, categoria i cites dels requisits i dels criteris. Ja es carrega a la pàgina: es reutilitza la mateixa lectura. |
| `GET /companies/{c}/tenders/{t}/structure` | Noms dels elements d'abast, blocs i seus del bloc Execució. |
| Candidatura existent (`GET …/workspace`, ja es carrega a la pàgina) | El peu de decisió. |
| `GET /companies/{c}/knowledge/vocabulary` | Rètols de les línies de servei dels elements d'abast. |
| `GET /companies/{c}/services` i `GET /companies/{c}/knowledge/records` | El nom del servei propi (`company_service_id`) i el del partner i el seu paper (`partner_role_id`) als candidats d'execució. |

- **Degradació parcial:** si falla un recurs secundari (requisits o estructura), la pestanya es pinta igualment i mostra un avís. **No es mostren mai UUIDs ni identificadors interns.** Si una fila no té títol, es fa servir un codi humà si n'hi ha (per exemple el codi de la línia de servei de l'element d'abast, amb el seu rètol del vocabulari). Si no, un text neutre: «Requisit sense títol disponible» / «Element d'abast sense nom disponible».

### 5.4 Estats de la pestanya

1. **Carregant:** esquelet.
2. **Sense anàlisi (404):**
   - Explica què és l'anàlisi i què no és: «no decideix per tu».
   - Botó «Analitza». Si el rol no té permís, es mostra el botó desactivat amb un `PermissionNote`.
3. **Falten prerequisits (409 `analysis_prerequisites_missing`):**
   - **Sense anàlisi prèvia:** aquest estat substitueix l'estat «sense anàlisi».
   - **Amb una anàlisi existent (normalment desactualitzada):** l'anàlisi anterior **continua visible** amb el seu avís de desactualitzada, i al costat de «Recalcula» apareix un missatge en línia: «No es pot recalcular fins que es resolgui:» i la llista de `missing`. No es descarta ni s'amaga res del que ja hi havia.
   - En tots dos casos, un text per a cada codi de `missing`. Per a `requirements`: «Encara no hi ha requisits per analitzar: processa els plecs i analitza'n els requisits, o afegeix-ne de manuals», amb un enllaç a la pestanya Requisits.
4. **Amb anàlisi:** les seccions de §5.5.
   - Si `stale`: un avís que diu que alguna dada ha canviat des d'aquesta anàlisi (plec, revisió o coneixement de l'empresa), sense afirmar quina, perquè l'API no ho diu. Al costat, «Recalcula».
   - Després de recalcular, s'informa «Les dades no han canviat: és la mateixa anàlisi» **només si** `reused` és cert **i** l'`id` retornat és el de l'anàlisi que hi havia a la pantalla. Si no (una anàlisi que algú ja havia recalculat, una cursa perduda o unes entrades que han tornat a un estat anterior), s'informa «Anàlisi actualitzada». `reused` vol dir «no s'ha escrit cap anàlisi nova: ja n'hi havia una amb les mateixes entrades», no «és la que tens a la pantalla».
5. **Errors:**
   - 403: nota de permís.
   - Qualsevol altre error: `ErrorNotice` amb text humà, mai JSON.

**Permisos a la UI:**
- Per mostrar «Analitza» o «Recalcula» es fa servir el helper existent `canReview` de `lib/review.ts`, que és el mirall de `REQUIREMENT_EDIT_ROLES`. No es crea cap política nova.
- La lectura no té cap política al frontend: qualsevol membre que arriba a la pàgina veu el que l'API li retorna.
- El backend és l'autoritat. Si respon 403 igualment, la UI ho explica.

### 5.5 Seccions (estat «amb anàlisi»), en aquest ordre

**1. Resum factual**
- Data de l'anàlisi.
- Comptadors de `summary`:
  - requisits: compleix / no compleix / no se sap / no aplica;
  - punts: segurs / en risc / perduts / no avaluables, sobre el màxim;
  - execució: els comptadors de `summary.delivery` amb les seves etiquetes literals;
  - preguntes generades: per al comprador / per a l'empresa;
  - nombre de possibles bloqueigs i de revisions necessàries.
- Frases derivades **només** d'aquests comptadors i factuals. Per exemple: «N requisits no es poden avaluar amb la informació actual (no se sap): això no vol dir que no es compleixin», o «N requisits consten com a "No compleix" en l'anàlisi actual» (sense dir-ne la causa).
- Cap mètrica nova, score, percentatge ni recomanació.

**2. «Què cal resoldre abans de decidir»** (la secció més destacada)
- Els findings, ordenats per tipus:
  1. `blocker_potential`: «Possible bloqueig», sempre amb «no confirmat». **Només** aquest tipus porta «no confirmat»: la secció no diu que la resta no estiguin confirmats;
  2. `review_required`;
  3. `evidence_gap`;
  4. `missing_information`, rotulat com a informació de l'empresa que falta;
  5. `territory_gap`;
  6. `delivery_gap`;
  7. `warning`.
- Al final, el nombre de preguntes generades, amb un enllaç a la secció Preguntes.
- **Cada element** mostra:
  - què és i per què (`explanation_code` i `rule_code` → text);
  - el requisit, criteri o element d'abast afectat (§5.3);
  - la cita i la pàgina **només si existeixen**;
  - «On es resol» **només si el codi té un destí inequívoc** a la taula: Coneixement › Qualificació i la secció que toqui, Coneixement actual, la revisió de requisits o la secció Preguntes. Si no en té, no hi ha botó.

**3. Requisits**
- Agrupats per categoria.
- Cada fila té:
  - títol;
  - cita i pàgina si n'hi ha;
  - estat (`fit_result`): Compleix / No compleix / No se sap / No aplica;
  - **quatre dimensions separades, cadascuna amb la seva etiqueta i sense barrejar-se:**
    1. **Prova** (`proof_status`): suficient / parcial / falta / no se sap / no cal.
    2. **Font del coneixement** (`knowledge_source`): d'on surt la dada, és a dir el coneixement actual de l'empresa, l'històric, cap o mixta. `current_verified` i `current_declared` es mostren tots dos com a «Coneixement actual de l'empresa». **«Verificat» no és mai una font.**
    3. **Verificació** (`verification_status`): verificat / no verificat / no se sap / no aplica. És l'única dimensió on apareix «Verificat».
    4. **Territori** (`territory_verdict`): coincideix / no coincideix / no se sap / no aplica.
- En desplegar la fila: l'explicació canònica del backend (`EXPLANATIONS[explanation_code]`) tal com és, i «On es resol» si el codi en té un destí. En els «no se sap», el text canònic ja diu per què; el frontend no hi afegeix cap explicació pròpia.
- Filtres per estat.
- «No se sap» és neutre: mai en vermell.
- «No compleix» només apareix quan el motor l'emet.

**4. Criteris i punts**
- Una fila per criteri, amb:
  - nom;
  - punts màxims;
  - `points_status`;
  - punts segurs, en risc, perduts i no avaluables;
  - resultat;
  - explicació;
  - cita si n'hi ha.
- Una fila de totals, la de `summary`.

**5. Execució**
- Per a cada element d'abast (línia de servei, bloc i seu si en té), els `delivery_candidates` de l'anàlisi, traduïts **fidelment**:
  - `candidate_type`: `own_current` → «Propi», `partner_current` → «Partner» (amb el paper), `subcontract_unknown` → «Subcontractació: no se sap» (neutre), `own_future` → etiqueta literal d'hipòtesi;
  - `readiness`: l'etiqueta literal de cada valor que emet el motor;
  - territori.
- No s'hi afegeix cap semàntica nova de disponibilitat.
- **Elements d'abast sense candidat:** cada element d'abast de l'estructura apareix encara que l'anàlisi no n'hagi donat cap candidat, amb «Cap candidat d'execució en aquesta anàlisi» (en neutre). No s'inventa cap candidat. Es distingeix:
  - **no hi ha abast:** l'estructura no té cap element d'abast i no hi ha candidats → «El plec no té elements d'abast estructurats»;
  - **hi ha abast però cap candidat** per a un element: la fila de l'element amb el text de més amunt.
- Si l'estructura no s'ha pogut carregar, només es mostren els elements que surten als candidats, amb el nom neutre si no n'hi ha cap.

**6. Preguntes**
- Dues llistes: **«Per al comprador»** i **«Per a l'empresa»**.
- Es mostra el camp `text` de l'API **tal com arriba** (renderitzat al backend per `question_text`), sense reinterpretar-lo. La UI no té copy propi per a cap `question_code`.
- Cada pregunta enllaça al requisit o al criteri que l'origina, quan n'hi ha.
- Es parla de «preguntes generades» o «a tractar».

**7. Peu: la decisió**
- Text: «La decisió de presentar-s'hi és vostra: Tuvi no recomana sí ni no.»
- Si ja hi ha candidatura: «Continua la candidatura per decidir» → `/candidatures/{w}?pas=decidir`.
- Si no n'hi ha: «Obre la candidatura» amb el mecanisme existent (`WorkspaceLauncher`).

### 5.6 Accessibilitat (focal)

- **Cap estat només per color.** Cada estat (fit, prova, font, verificació, territori, punts, `readiness`, tipus de finding, desactualitzada) porta sempre el text de l'etiqueta. El color, si n'hi ha, és redundant. «No se sap» fa servir el to neutre.
- **Pestanya:** fa servir el `Tabs` existent (`role="tab"`, `aria-selected`). S'hi pot accedir amb el teclat, i en canviar de pestanya el focus queda a la pestanya activa.
- **Filtres de Requisits:** són botons de commutació (`aria-pressed`), en ordre de tabulació, amb el nombre de resultats anunciat (`aria-live="polite"`).
- **Expandibles** (files de requisit, detall de finding): un botó amb `aria-expanded` i `aria-controls`, accionable amb Intro i Espai. El focus es queda al botó.
- **Recalcular:** és un botó real. Mentre treballa té `aria-busy` i està desactivat, sense perdre el focus. El resultat («Anàlisi actualitzada», «és la mateixa anàlisi», o l'error) s'anuncia a una regió `aria-live="polite"`. Els errors fan servir el patró `role="alert"` existent.
- Tot el que es pot accionar té nom accessible i un focus visible (els estils de focus del kit). Les accions de fila fan 44 px a mòbil, com a la resta de l'app.

### 5.7 Llenguatge

- Mai es mostra cap codi cru. Les explicacions mostren el text canònic del backend; els valors d'enum, l'etiqueta del frontend.
- «No se sap» sempre amb el perquè.
- Res es mostra com a confirmat si no ho està.
- Una cita només surt quan existeix.

## 6. Proves

**Backend** (SQLite focal + PostgreSQL focal)
- `POST /analysis/runs`:
  - primera execució (201, `reused: false`);
  - repetició sense canvis (200, `reused: true`, el mateix id);
  - després d'un canvi d'entrada, una anàlisi nova;
  - 409 `analysis_prerequisites_missing` amb `details.missing = ["requirements"]` quan no hi ha cap requisit efectiu, sense cap anàlisi escrita;
  - el mateix 409 quan ja hi ha una anàlisi desactualitzada: l'anàlisi anterior no canvia (ni `is_current`, ni el contingut, ni el recompte d'anàlisis);
  - **concurrència:**
    - un test determinista a SQLite que simula la cursa. Abans que l'endpoint faci el commit, una altra sessió desa i confirma l'anàlisi idèntica (amb un monkeypatch del primer intent; el `persist_hook` existent és síncron i no ho pot fer). El commit de l'endpoint topa amb la unicitat, i l'endpoint retorna 200, `reused: true` i l'`id` de l'altra sessió, sense cap 500 i amb una sola anàlisi a la base.
    - un test `@pytest.mark.postgres` amb dues peticions reals simultànies (`asyncio.gather`): totes dues reben 2xx amb el mateix `id`, cap 500, i a la base hi ha una sola anàlisi vigent;
  - matriu de rols: lector 403, la resta 201 o 200;
  - una altra empresa: 404, i cap anàlisi creada.
- El vocabulari generat coincideix amb el registre.

**Frontend**
- Estats: carregant, sense anàlisi (404), prerequisits (409) sense anàlisi i **amb anàlisi desactualitzada (l'antiga continua visible)**, desactualitzada, amb dades, error, conflicte concurrent (409 `analysis_run_conflict`).
- Requisits: les quatre dimensions separades. «Verificat» només apareix com a verificació, mai com a font.
- `DecisionAnalysisCard`: vigent, desactualitzada, sense anàlisi i error. Mai no fa cap POST.
- Accessibilitat: cap estat sense text, `aria-expanded` als expandibles, `aria-pressed` als filtres, regió `aria-live` per al recàlcul, i navegació amb teclat dels filtres, els expandibles i el botó de recàlcul.
- Ordre de «Què cal resoldre» i etiqueta «no confirmat».
- Cap UUID ni codi cru visible, també en degradació parcial.
- «No se sap» neutre.
- Cites i «On es resol» només quan toca.
- Execució fidel als valors del motor.
- Preguntes amb el camp `text` tal com arriba, sense copy propi per `question_code`.
- Recàlcul (`reused` true i false) i permisos (`canReview`).
- `DecisionAnalysisCard` amb els mateixos helpers.
- Cobertura del vocabulari generat.
- Totes les dades són sintètiques.

**Gates:** tests focals del backend (SQLite i PostgreSQL), tests complets del frontend, typecheck, lint, build de producció, ruff als fitxers tocats, `git diff --check` i escaneig de secrets i dades.

**QA visual focal** a escriptori i a mòbil (390 px), en una base d'usar i llençar amb dades sintètiques.

## 7. Lliurament

- Branca `engine/phase3a-analysis-workspace` (worktree `Licitacions-analysis-ui`), des de `5502ef5`.
- Sense commit fins a l'aprovació explícita.
- Sense desplegament al pilot.

## 8. Riscos i decisions

- **El valor depèn de l'estructura del plec.** En licitacions públiques sense estructura, moltes files seran «no se sap». La UI ho explica com a manca d'informació, no com un incompliment.
- **«Desactualitzada» és un booleà.** No es diu quina entrada ha canviat fins que l'API ho exposi. Seria un canvi futur, no inclòs aquí.
- **Textos de preguntes sense accents.** Es mostren com arriben, fins a un canvi de copy amb versió pròpia.
