# F-4: una sola anàlisi vigent al pas Decidir

**Branca:** `engine/phase3a-decidir-f4`, des de `2cd9d44` (v3), al worktree `Licitacions-decidir-f4`.
**Estat:** aprovada conceptualment amb els ajustos de la revisió del 2026-10-05, incorporats. No es toca el pilot.

## 1. Problema

Al pas Decidir conviuen tres comptadors de «bloquejos» que es diuen igual i signifiquen coses diferents:

1. **Capçal, «N bloquejos».** Portes de preparació final per congelar la proposta: decisió GO, abast tècnic, oferta i dossier.
2. **«Què bloqueja», bàner, modal i historial.** Comptadors del motor de compliment antic (`app/compliance`), que deriven del run de requisits antic.
3. **`DecisionAnalysisCard`.** L'anàlisi v3: «possibles bloqueigs», que mai no són confirmats.

A L'Escala surt «3 bloquejos» al costat de «0 bloquejos» i «Anàlisi del plec» en verd, encara que l'anàlisi vigent és la v3. També hi ha textos de «recomanació automàtica» del motor antic.

## 2. Objectiu i límits

- **Font única.** L'anàlisi v3 és l'única anàlisi vigent del plec a Decidir: requisits, punts, findings i possibles bloqueigs.
- **Preparació.** Els pendents de preparació es mantenen, però amb el nom «Pendents de preparació». No són conclusions sobre l'encaix amb el plec.
- **Cap recomanació.** Ni recomanació automàtica ni GO/NO-GO presentats com a vigents.
- **El motor antic no es presenta mai com a conclusió vigent.** Pot quedar com a registre del moment d'una decisió ja presa (historial) i com a «avisos del compliment anterior» que el backend encara desa.
- **Fora d'abast:**
  - no es toca el motor v3 ni el run v3;
  - no es toca l'esquema ni s'esborren dades del motor antic;
  - no es fa cap canvi de backend;
  - no es renombra cap camp, enum ni codi: només copy i UI.
- **Historial.** Les decisions antigues es conserven com a registre històric, mai com a conclusió vigent.

## 3. Taula d'elements

| Element actual | Font | Significat real | Acció F-4 | Visible després? |
|---|---|---|---|---|
| Píndola del capçal «N bloquejos» / «A punt» / «Preparació desconeguda» (`ReadinessControl.tsx:80-93`) | `GET …/workspaces/{w}/final-readiness`, findings amb `severity=blocker` | Portes per congelar la proposta: decisió GO, abast tècnic, oferta, dossier | Reetiquetar a «N pendent(s) de preparació». «A punt» i «Preparació desconeguda» no canvien. Només copy: `severity`, `blockerCount` i l'endpoint no canvien | Sí, a tots els passos |
| Calaix de preparació (títol, grup «Bloqueig», peu «Resol els bloquejos…») (`ReadinessControl.tsx:95-125`) | El mateix endpoint | Detall de les portes | El grup «Bloqueig» passa a «Cal resoldre per congelar». El peu deixa de dir «bloquejos» | Sí |
| Tancar: «Queden N bloquejos per resoldre.» (`SubmissionPackage.tsx:327`) | El mateix endpoint | El mateix que el capçal | «Queden N pendents de preparació per resoldre.», per coherència | Sí (pas Tancar) |
| Stepper: «N coses per resoldre» (`steps.ts:81`) | `readiness_blockers` del dossier | Pendents del dossier | Cap canvi: no diu «bloquejos» | Sí |
| Bàner: candidats «Bloqueig» (bloqueigs confirmats), «Avisos» (`open_warnings`) i «Anàlisi endarrerida», amb l'acció «Veure el compliment detallat →» (`steps.ts:124-266`) | `workspace.counters`, `open_warnings` i `outdated_reasons` del motor antic | Conclusions del motor de compliment antic | Es retiren aquests tres candidats. Es mantenen els basats en fets del plec: anul·lada, lot retirat, termini passat, decisió caducada perquè el plec ha canviat | Només els de fets del plec |
| `DecisionPanel`: capçalera, «Pendent», «Decisió vigent» | `human_decision`, `latest_decision` | Decisió humana | Cap canvi | Sí |
| «Motiu · obligatori si aproves: hi ha N avisos oberts…» (`WorkspaceDecision.tsx:225-239`) | `go_requires_reason` i `open_warnings` del backend antic, que **continua exigint** el motiu | El backend demana motiu si hi ha avisos del motor antic | Copy nou: «Motiu · obligatori si aproves: el registre de la decisió inclou N avisos del compliment anterior.» El comportament no canvia | Sí |
| Modal: fila «Recomanació automàtica» | `workspace.recommendation` (compliance run antic) | Recomanació del motor antic | **Es retira** | No |
| Modal: fila «Bloqueigs confirmats» / «Cap requisit avaluat» i caixa «Decidiràs sense saber si l'empresa compleix» | `counters`, `eligibility` antics | Conclusions del motor antic | **Es retiren** | No |
| Modal: fila «Anàlisi» (`analysis_basis`) i caixa d'avís de simulació / sense motor | Proveïdor del run de requisits antic | Base del motor antic | **Es retiren** | No |
| Modal: anàlisi v3 | El mateix estat de `GET …/tenders/{t}/analysis/current` que la targeta (§4) | Anàlisi vigent | **Nova fila «Anàlisi del plec»** amb els cinc estats de §4. Només lectura | Sí |
| Modal: Decisió, Signada per, Expedient, Termini, Pre-fit | Workspace | Fets i pre-fit de metadades | Cap canvi | Sí |
| Modal: llista d'avisos oberts | `open_warnings` | El backend els desa amb la decisió | Es mantenen, amb el títol «Avisos del compliment anterior que quedaran registrats» (§5) | Sí |
| Modal: descripció «Això és exactament el que quedarà registrat…» | — | Deixaria de ser exacta si s'amaguen camps desats | Nova: «Quedarà registrada la decisió, qui la signa, quan i el motiu, amb els avisos del compliment anterior. El sistema també hi desa, com a registre intern, l'estat d'aquell compliment: no és una conclusió vigent.» | Sí |
| `ContextPanel` Encaix | `prefit_score` (pre-fit de metadades) | «Ens interessa?» | Cap canvi: continua derivant només de `prefit_score` | Sí |
| `ContextPanel` Lot | `lot_*` | Fet | Cap canvi | Sí |
| `ContextPanel` Elegibilitat | `eligibility` (compliance run antic) | Conclusió del motor antic | **Es retira**; `eligibility` deixa d'afectar el panell | No |
| `ContextPanel` Anàlisi (`analysis_basis`, «Anàlisi del plec» en verd) | Proveïdor del run de requisits antic | Base del motor antic, que es pot confondre amb la v3 | **Es retira**; `analysis_basis` deixa d'afectar el panell | No |
| «Què bloqueja» (`BlockerCounters`, `WorkspaceWork.tsx:419-546`) | `counters`, `eligibility`, `open_warnings` antics | Compliment del motor antic | **Es retira del pas Decidir.** Comprovació estàtica feta: només es munta a `page.tsx:434`; s'elimina el component i els imports morts | No |
| `DecisionAnalysisCard` | Anàlisi v3 (estat compartit, §4) | Font única vigent | Cap canvi de contingut. Només lectura, zero POST | Sí |
| Historial: entrada, «Vigent», motiu | `workspace_decisions` | Registre humà | Cap canvi. «Vigent» es refereix a la decisió humana vigent | Sí |
| Historial: «Amb N bloquejos confirmats… · pre-fit · el sistema deia X» | Columnes desades a `workspace_decisions` | Fotografia del motor antic en el moment de decidir | Es mostra com a registre: «Registre del moment (compliment anterior): N bloquejos confirmats, N possibles i N obligatoris sense resoldre · pre-fit X · el compliment anterior suggeria Y». Pot dir «bloquejos confirmats» perquè és registre, no conclusió vigent | Sí, com a registre |
| Historial: «Es va decidir sabent-ho: …» / «no es van registrar» | `open_warnings` desats | Registre | Cap canvi de comportament; etiquetes amb fallback neutre (§6) | Sí |

## 4. Una sola lectura de `/analysis/current`

- **Propietari únic:** la pàgina de candidatura, i només al pas Decidir (`step === "decidir"`), llegeix `GET /companies/{c}/tenders/{t}/analysis/current` una sola vegada i en guarda l'estat.
- **Estats explícits**, tots gestionats pel propietari i passats tal com són:
  - `loading`: encara no hi ha resposta;
  - `current`: run vigent (`stale: false`);
  - `stale`: run vigent però desactualitzat (`stale: true`);
  - `absent`: 404 (no hi ha anàlisi);
  - `error`: qualsevol altre error de lectura.
- **Consumidors:** `DecisionAnalysisCard` i el `DecisionPanel` (fila «Anàlisi del plec» del modal) reben **exactament el mateix estat i el mateix run**. Cap dels dos no fa cap GET pel seu compte al pas Decidir.
- **Cap GET nova** en obrir o tancar el modal, ni en canviar el text del motiu.
- **Preparar i Tancar no fan aquesta GET.**
- **Textos de la fila del modal per estat:** `loading` → «Es carrega l'anàlisi…»; `current` → «Feta el {data} · N elements a resoldre» (o «Cap element pendent de resoldre»); `stale` → el mateix amb « · desactualitzada»; `absent` → «Encara no hi ha anàlisi d'aquesta licitació.»; `error` → «No s'ha pogut carregar l'anàlisi.»

## 5. Copy dels avisos del compliment anterior

Fins a F-4b, tot el copy que surti de `open_warnings` o de `decision_reason_required` ha de dir inequívocament «avisos del compliment anterior»:

| Lloc | Copy |
|---|---|
| Text sota el panell (motiu obligatori) | «Motiu · obligatori si aproves: el registre de la decisió inclou N avís / avisos del compliment anterior. NO-GO i ajornar no en demanen.» |
| Text sota el panell (motiu opcional) | sense canvis («Motiu · opcional. …») |
| Títol del bloc del modal | «Avisos del compliment anterior que quedaran registrats» |
| Zero-state del bloc | «Cap avís del compliment anterior.» + «El sistema en fa la fotografia en signar; ara mateix no n'hi ha cap.» |
| Etiqueta del camp Motiu (obligatori) | «Motiu (obligatori: hi ha avisos del compliment anterior)» |
| Placeholder (obligatori) | «Per què es presenta l'empresa malgrat els avisos del compliment anterior?» |
| Error `decision_reason_required` | «Cal un motiu: el registre inclou avisos del compliment anterior: {llista}.» |

## 6. Fallback sense codis crus

- Un codi d'avís sense etiqueta a `WARNING_LABELS` es mostra com a «Avís del compliment anterior», mai el codi. S'aplica a tots els llocs que avui fan `WARNING_LABELS[code] ?? code`: el modal, l'error de motiu, l'historial i el bàner (si el candidat «Avisos» desapareix, allà deixa d'aplicar).
- Una recomanació històrica sense etiqueta a `RECOMMENDATION_LABELS` es mostra com a «el compliment anterior va fer un suggeriment (valor històric)», mai el valor.
- Cap `missing_compliance`, cap valor d'enum ni cap UUID no és visible al pas Decidir. Es verifica amb tests que injecten codis i valors desconeguts.

## 7. Acceptació

1. Amb l'anàlisi v3 de L'Escala, el pas Decidir no mostra cap contradicció entre «0» i «3 bloquejos». El capçal diu «N pendents de preparació», i cap bloqueig del motor antic no es presenta com a conclusió vigent.
2. **F-4 no altera el run v3.** C1 continua sent l'únic `does_not_satisfy`. `DecisionAnalysisCard` i el modal consumeixen exactament el mateix run (mateix `id`), llegit un sol cop.
3. Els pendents de preparació continuen visibles (capçal, calaix i Tancar) i no es diuen «bloquejos».
4. Al pas Decidir no hi ha cap «Recomanació del sistema» ni «Recomanació automàtica» vigent. A l'historial només surt com a «el compliment anterior suggeria …».
5. `DecisionAnalysisCard` continua sent de només lectura i fa zero POST. Obrir i tancar el modal no fa cap GET. El POST de la decisió no canvia: `{decision, reason}`.
6. El motor v3 no es toca, ni l'esquema, ni el backend, ni les dades del motor antic. Els tests de backend de workspace i decisions passen sense cap canvi.
7. Les decisions continuen funcionant igual: GO amb motiu obligatori quan el backend el demana, NO-GO i ajornar sense motiu, i 403 per falta de permís.
8. Cap codi cru visible amb avisos o recomanacions desconeguts.
9. QA a 1440 i 390 del pas Decidir, Preparar i Tancar: cap bloqueig del motor antic presentat com a conclusió vigent.

## 8. Release gate i integració

- **F-4b és release gate.** Queda fora de l'abast de F-4 (cap canvi de backend aquí). Però **v3 + F-4 no es poden desplegar al pilot fins que F-4b (avisos i fotografia de la decisió conscients de la v3) estigui resolt i validat.** Mentrestant el backend exigirà motiu de GO gairebé sempre, pels avisos del motor antic, i el copy de §5 ho explica honestament.
- **No es reescriu `2cd9d44`.** La v3 queda tal com està pujada.
- **Integració futura:** després de F-4 i F-4b es farà una branca d'integració/release des de la línia de hardening basada en `d92746f` (que porta el hotfix `a888784` i tenant_ops). En el conflicte previsible a `apps/api/app/api/v1/routers/requirements.py` han de sobreviure **tots dos** canvis: les guardes de visibilitat del hotfix (`_visible_requirement`, `_visible_criterion`, POST legacy → 410) i les cites de criteris de la v3 (`AwardCriterionRead.citations`, càrrega en bloc).
