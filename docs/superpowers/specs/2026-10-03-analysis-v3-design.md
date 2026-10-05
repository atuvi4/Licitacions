# Anàlisi v3: correccions de la validació de L'Escala

**Branca:** `engine/phase3a-analysis-v3`, creada des de `7513e3a`, al worktree `Licitacions-analysis-v3`.
**Origen:** la validació controlada de L'Escala real del 2026-10-02 (entorn aïllat). Aquesta validació bloqueja el desplegament de 7513e3a al pilot.
**Estat:** spec per revisar. No hi ha codi ni commit.

## 1. Objectiu

Que l'anàlisi no digui res fals ni enganyós amb dades reals, i que tot el que es mostra a la persona estigui escrit en català correcte i sense codis interns. El motor continua sense decidir res: cap veredicte nou, cap score i cap bloqueig nou.

## 2. Principis que no canvien

- Els runs són immutables. Els runs v2 no es reescriuen ni es migren.
- **Els codis són contracte.** Si una explicació ha de dir una cosa diferent, rep un codi nou. El text d'un codi existent només pot canviar de forma (accents, etiquetes), mai de significat.
- **El copy es versiona per `rules_version`.** Un run v2 continua mostrant el copy v2 (explicacions i `QuestionRead.text`), i un run v3, el copy v3. Desplegar la v3 no canvia el text de cap run antic (§3.11). Un run v2 surt com a «Desactualitzada», perquè `RULES_VERSION` forma part de l'`inputs_hash`.
- Cap migració d'esquema. Cap valor d'enum nou a la base de dades (`FindingType` no canvia).
- Cap dada real a git. El dataset de L'Escala és la lectura pública del plec que ja hi ha al repo.

## 3. Canvis

### 3.1 C-E1: causa real dels criteris en risc (motor)

Ara mateix, `criterion_findings` (`engine.py:1966`) emet `DECLARED_NOT_VERIFIED` («Es compleix amb dades declarades») per a qualsevol criteri `at_risk`.

A la v3 cada finding diu la seva causa, a partir de les necessitats i del requisit enllaçat que realment deixen punts en risc:

| Causa | finding_type | Explicació |
|---|---|---|
| Alguna necessitat compleix amb dada declarada no verificada (`satisfies` + `current_declared`) | `review_required` | `DECLARED_NOT_VERIFIED` (es manté) |
| Alguna necessitat és `unknown` (no consta cap dada actual) | `missing_information` | **nova** `CRITERION_NEEDS_UNKNOWN`: «Hi ha punts en risc perquè no consta cap dada actual de l'empresa sobre el que demana el criteri.» |
| Alguna necessitat és `unknown` però té dada actual (`current_declared`/`current_verified`) que no es pot comparar (revisió final, I1) | `review_required` | capacitat sense nombre o nivell: `CAPABILITY_UNKNOWN` (la seva); fet quantitatiu sense nombre: **nova** (només v3) `CRITERION_NEEDS_NOT_COMPARABLE`: «Hi ha punts en risc perquè la dada actual de l'empresa no es pot comparar amb el que demana el criteri.» |
| El requisit enllaçat té el moment de compliment no concloent | (ja existeix) | `CRITERION_TIMING_NOT_CONCLUSIVE` |
| Compromís sense estructura | `review_required` | `CRITERION_OPEN_COMMITMENT` (ja existeix com a explicació del criteri) |

Regles:
- Si hi ha diverses causes, s'emet un finding per causa. Els `details` porten els `need_id` afectats.
- **Expectativa del gold, fixada pels inputs abans d'implementar** (dataset + bessó, no la primera execució):
  - **T1** (`pbx.concurrent_calls`; el bessó té la centraleta declarada sense capacitats), **C2b** (capacitat sense mapar `ftth_1gbps_simetric`), **C4a** i **C4b** (`ops.sla_*`, que el bessó no té): totes les necessitats són `unknown`. Surten només amb `CRITERION_NEEDS_UNKNOWN`.
  - **C3** (IPv4: el bessó declara 20 adreces): surt només amb `DECLARED_NOT_VERIFIED`.
  - Si el motor no dona aquestes causes de necessitat, és una troballa que s'informa. No s'ajusta l'expectativa.
- Invariant que es comprova amb tests: **mai** `DECLARED_NOT_VERIFIED` en un criteri si cap necessitat té `satisfies` + `current_declared`.
- Invariant que es comprova amb tests (I1): **mai** `CRITERION_NEEDS_UNKNOWN` amb una necessitat de font actual.
- Els punts no canvien: es mantenen 0/27/5/68.

### 3.2 C-E2: R1/R2 sense data de referència (motor)

**Avui:**
- `QUALIFICATION_REFERENCE_DATE_MISSING` emet `missing_information` / `DATA_MISSING` («Falta una dada de l'empresa»).
- També emet `evidence_gap` amb preguntes de prova a l'empresa.

**v3:**
- **El fit no canvia:** continua `unknown` amb `QUALIFICATION_REFERENCE_DATE_MISSING`.
- **Finding:** `missing_information` amb l'explicació nova `TENDER_REFERENCE_DATE_MISSING`: «El plec no fixa el termini de presentació, que és la data de referència per calcular aquest requisit. Cal demanar-lo a l'òrgan.» Els `details` porten `missing_from: "tender"`.
- **`missing_from` no té valor per defecte.** S'auditen tots els productors de `missing_information` a `engine.py`. Cadascun rep `"company"` o `"tender"` només si es pot demostrar d'on falta la dada. Si no es pot demostrar, el camp no es posa. La taula de productors i el valor de cadascun queden a l'informe de la tasca.
- **Pregunta nova al comprador:** `buyer.provide_submission_deadline`, «Demaneu a l'òrgan el termini de presentació d'ofertes (cal per calcular «{title}»).». Es fa una per requisit afectat. No s'agrupen: no tenen `scope_item_id` i cada una porta el seu requisit (§3.8 és estricte).
- **Prova:** si no se sap quins exercicis o anys compten, la prova no es pot avaluar. Per tant `proof_status = unknown`, sense `evidence_gap` ni pregunta de prova en aquest cas concret. *Aprovat.*
- **No és un bloqueig:** cap `blocker_potential`.
- **UI:** l'etiqueta de `missing_information` passa a dependre de `details.missing_from`:
  - `"tender"` → «Falta informació del plec»;
  - `"company"` → «Falta informació de l'empresa»;
  - absent (també als runs v2) → «Falta informació».
  No es crea cap `FindingType` nou.

### 3.3 F1: textos humans

- **Accents:** tots els `EXPLANATIONS` i les plantilles de `QUESTIONS` es reescriuen en català correcte (`solució`, `òrgan`, `prestació`…), sense canviar-ne el significat.
- **Paràmetres de les preguntes:** `params_json` continua guardant codis, perquè forma part de la clau de deduplicació i del run immutable. `question_text` els tradueix a etiquetes en renderitzar:

| Paràmetre | D'on surt l'etiqueta |
|---|---|
| `service_line` | `SERVICE_LINES` |
| `capability` | `CAPABILITIES` |
| `standard` | `CERTIFICATION_STANDARDS` |
| `proof_kind` | mapa nou al backend |
| `category` | mapa nou al backend |
| `registration_type` | mapa nou al backend |
| `fact` | mapa nou al backend |

- **Codi sense etiqueta:** surt una expressió neutra sense el codi (p. ex. «una línia de servei del plec»), mai el codi.
- **Etiquetes del vocabulari interpolades:** també es corregeixen d'accents (p. ex. «numeració»). Són etiquetes, no claus.
  - Les pantalles de Coneixement les reben de `/knowledge/vocabulary`, que es construeix a partir de `vocabulary.py`. Les taules `service_lines`, `capabilities` i `certification_standards` (sembrades per les migracions 0045/0046) conserven les etiquetes antigues sense accents, perquè no hi ha migració: cap pantalla ni cap codi no les llegeix, i `seed_vocabulary` (merge) les actualitzaria.
  - Hi haurà proves que cap codi les usa com a clau, i un smoke test d'aquestes pantalles.
- **Oracle principal:** un test per a **cada** `QuestionCode` (no només els que genera el gold) i per a cada `ExplanationCode` v3. Cadascun compara amb el text esperat exacte, escrit a mà, amb paràmetres coneguts i amb paràmetres desconeguts.
- **Regressió addicional:** escaneig de tokens `snake_case` / `a.b_c` i de la llista d'«accents prohibits».
- **Vocabulari del web:** es regenera `analysis-vocabulary.generated.json`, ara versionat (§3.11).

### 3.4 `RULES_VERSION`

- Passa de `phase3a-analysis-v2` a `phase3a-analysis-v3`.
- Conseqüències que ja es donen avui:
  - un run v2 no es reutilitza mai en una anàlisi v3 (`inputs_hash` diferent);
  - el run v2 surt `stale` fins que es recalcula;
  - dues anàlisis v3 amb les mateixes entrades reutilitzen el mateix run.
- Tot això queda cobert amb tests.

### 3.5 C-E3: empremta numèricament canònica

- **Canvi:** `fingerprint.jsonable` passa a normalitzar tots els nombres (`int`, `float`, `Decimal`; no `bool`) a una mateixa forma decimal canònica. Per exemple, `20`, `20.0` i `Decimal("20.00")` donen `"20"`. `NaN` i `inf` es rebutgen.
- **Conseqüència:** canvien les empremtes d'estructura de tots els plecs. És acceptable perquè la v3 ja deixa tots els runs `stale`.
- **Round-trip a PostgreSQL:**
  1. Carregar el gold.
  2. Calcular l'empremta a la sessió del carregador.
  3. Calcular-la de nou des d'una sessió nova que rellegeixi de la base.
  4. Han de ser iguals.
- **Empremta de coneixement** (`snapshot.py`, amb canonicalització pròpia): s'audita amb el mateix round-trip. Només es toca si es demostra divergència. Tocar-la canviaria l'empremta de referència del pilot, i això es documentaria abans de desplegar.

### 3.6 C-P1: execució

Si un candidat té servei i soci alhora, es mostren tots dos: «Accessos FTTH · Majorista X · Subcontractista». Avui `who()` (`Delivery.tsx:8-15`) en mostra només el servei.

### 3.7 C-P4 i punt 11: ordre i obligatorietat

**Auditoria:** l'obligatorietat i el moment de compliment ja existeixen estructurats a `requirements.obligation` (`mandatory | scored | informative | unknown`) i `requirements.timing` (`at_submission | before_formalization | at_execution | ambiguous | unknown`). L'API ja els retorna i el gold els té informats als 34 requisits.

**Gaps documentats, que no s'infereixen:**
- No hi ha cap camp d'admissibilitat o exclusió. La combinació `mandatory` + `at_submission` **no** equival a exclusió. Aquí només serveix per prioritzar i per a l'etiqueta factual.
- `modality` i `alternative_group` no arriben al motor.
- El tipus `Requirement` del web no declara `timing`. S'hi afegeix.

**Ordre de «Què cal resoldre»** (només presentació, no canvia cap tipus):
1. `blocker_potential`;
2. **negatius demostrats**: findings d'un requisit amb `does_not_satisfy`, o d'un criteri amb `lost_points > 0`;
3. la resta, per tipus com fins ara.

Dins de cada grup, primer van els requisits `mandatory` amb `at_submission`, després `before_formalization`, després la resta, i `unknown` al final. L'ordre és estable.

**Etiqueta factual:** a cada element, «Obligatori · a la presentació» o «Obligatori · abans de formalitzar», presa del camp i no deduïda. Cap «obligatori pendent» com a veredicte nou.

**Execució:** el resum diu «Vies d'execució (candidats): disponibles N · pendents N · no disponibles N». `summary.delivery` compta candidats, no elements.
- **Nombre d'elements d'abast:** a la pestanya surt de `GET …/structure` (`structure.scope_items.length`), amb el text «N elements d'abast al plec». Si l'estructura no s'ha carregat, no es mostra.
- **`DecisionAnalysisCard`** no carrega l'estructura i **no** mostra aquest nombre. No hi ha cap mètrica implícita ni deduïda dels candidats.
- **Run desactualitzat amb un requisit que ja no surt a la lectura actual:** ordre estable, el text «Requisit sense títol disponible» i cap identificador cru.

### 3.8 C-P2: preguntes repetides

- **Quan s'agrupen:** preguntes amb el mateix `audience`, el mateix `question_code` i els mateixos `params_json` **canonicalitzats** (claus ordenades en profunditat, de manera que un ordre de claus diferent no separa grups equivalents), i amb `scope_item_id` informat (vinculat a una seu estable).
- **Com es mostren:** el text surt un cop, amb la llista de **totes** les seus o elements afectats (codi i nom de la seu).
- **Què no s'agrupa:** les que no tenen `scope_item_id`, ni les que difereixen en qualsevol paràmetre. Mai es dedupliquen per text.
- **Recompte:** es conserva el total real de preguntes, per exemple «21 preguntes (1 grup)».

### 3.9 C-D1: cita de R11a

Al dataset, la cita «no donen dret a cap excés» passa del DOC-05 §6 al PPT, a la clàusula real de la p. 7.
- Abans d'editar, la clàusula exacta es verifica localment contra el text del PPT del ZIP real.
- Les cites no entren a l'empremta.
- El test del gold afegeix un assert sobre la font de R11a.

### 3.10 C-P3: cites reals

- **Ubicació de la cita:** `Citation` mostra `location_label` (p. ex. «PCAP cl. 7.A») i la pàgina, només quan existeixen. Si no hi ha ni document ni ubicació, no es mostra res. Mai s'inventa una cita.
- **Criteris:** avui no en tenen cap a la UI. L'API de lectura que fa servir el web (`award_criteria` de l'anàlisi de requisits) afegeix `citations` des de `award_criterion_source_ref_links`.
  - És un canvi additiu i només de lectura.
  - `Field(default_factory=list)`.
  - Es carrega en bloc: una sola consulta per a tots els criteris, sense N+1.

### 3.11 Copy versionat per `rules_version`

- **Backend:** el registre guarda les taules de text per versió: `EXPLANATIONS_BY_VERSION` i `QUESTIONS_BY_VERSION`.
  - **v2:** còpia literal congelada dels textos actuals, amb interpolació crua com avui.
  - **v3:** textos nous, amb resolució d'etiquetes.
  - `explanation_text(code, rules_version)` i `question_text(code, params, rules_version)` fan servir la taula del run. El router passa `run.rules_version`.
  - **Versió desconeguda:** text neutre «Explicació no disponible.» / «Pregunta no disponible.». Mai la taula d'una altra versió.
- **Web:** el JSON generat porta `explanations` per versió. `explanation(code, rulesVersion)` fa servir `run.rules_version`.
- **Tests:**
  - un run v2 renderitza, explicació per explicació i pregunta per pregunta, exactament el mateix text abans i després del canvi;
  - un run v3 renderitza el copy v3;
  - la taula v2 congelada és idèntica a la de 7513e3a (test contra un hash).

## 4. Acceptació (imprescindible)

1. El gold semàntic continua passant, a SQLite i a PG.
2. C1 continua sent l'únic `does_not_satisfy` i perd 5 punts. El vector és 4/1/19/10 i els punts 0/27/5/68.
3. R1/R2 continuen `unknown` sense termini, ara amb pregunta al comprador i sense «falta dada de l'empresa».
4. 0 `blocker_confirmed` i 0 `blocker_potential` en aquest cas.
5. L'històric no satisfà cap requisit actual.
6. 0 `own_current` FTTH a L'Escala.
7. Mateixa estructura després de passar per la base, mateixa empremta (test de round-trip a PG).
8. Recalcular v3 no reutilitza un run v2, però una segona v3 idèntica sí.
9. Cap output humà v3 amb codis interns (oracle: texts esperats per a cada codi; regressió: escaneig + revisió humana a la QA).
10. Un run v2 mostra exactament el mateix copy que a 7513e3a.
11. Cap dada real a git.
12. C-E1: cap criteri amb `DECLARED_NOT_VERIFIED` sense una necessitat declarada. T1, C2b, C4a i C4b porten `CRITERION_NEEDS_UNKNOWN`; C3, `DECLARED_NOT_VERIFIED`.
13. Revalidació de L'Escala en un entorn aïllat: oracle PASS, i C-E1, C-E2, C-P1, C-P2, C-P3, C-P4 i F1 resolts a la UI. La passada amb coneixement real es fa sobre una **restauració en una DB disposable d'un backup verificat pre-0053**, mai sobre la base viva.

## 5. Fora d'abast

- La decisió sobre el motor de compliment antic.
- Admissibilitat estructurada (gap documentat).
- Fase 3B.
- El pilot. Encara no es desplega.
