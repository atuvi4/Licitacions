# Workspace de candidatura (5A)

El primer workflow operatiu complet: **detecció → anàlisi → compliment → revisió
→ decisió humana**. Reuneix el que ja han calculat 4A, 4B i 4C i hi afegeix
l'única cosa que un sistema no pot posar: la decisió.

Aquesta iteració **no fa servir cap LLM**. Zero tokens.

## El principi

L'automatització no pren la decisió final.

| Camp | Qui l'escriu | Què vol dir |
| --- | --- | --- |
| `prefit_score` | Motor 4A | Ens interessa? (metadades de la licitació) |
| `eligibility` | Motor 4C | Podem acreditar que complim? (requisits del plec) |
| `recommendation` | Motor 4C | El que suggereix el sistema |
| `human_decision` | **Una persona** | El que s'ha decidit. L'única oficial |

Són quatre camps diferents a l'API i quatre blocs diferents a la pantalla. No hi
ha cap camp que els resumeixi, perquè resumir-los seria decidir per l'usuari.

Exemple real i perfectament normal:

```
Pre-fit: 91
Elegibilitat: Cal revisió
Recomanació: REVIEW
Decisió humana: PENDENT
```

## Models

```
Company + Tender (+ Lot)  ->  BidWorkspace
                                |- WorkspaceDecision        (immutable, append-only)
                                |- WorkspaceChecklistItem   (derivat dels Requirements)
                                |- WorkspaceTask            (feina manual)
                                |- WorkspaceNote            (text pla)
```

Tot porta `company_id`: un workspace és dada de tenant, com el compliment.

### Unicitat

`company + tender + lot`. Com que `tender_lot_id` pot ser NULL i a SQL
`NULL != NULL`, una sola restricció única no evitaria dos workspaces "de tot
l'expedient". Hi ha **dos índexs únics parcials**: un per al cas amb lot i un
altre per al cas sense.

El `POST` de creació és **idempotent**: si ja existeix, retorna el que hi ha amb
`created: false`. El botó de la fitxa de licitació no pot generar duplicats ni
que es premi dues vegades.

## Estats

`analysis` · `needs_review` · `awaiting_decision` · `go_approved` ·
`no_go_approved` · `preparing_bid` · `ready_for_final_review` · `submitted` ·
`withdrawn`

### Les transicions estan centralitzades

Totes viuen a `app/workspace/transitions.py`, que és pur: no toca base de dades
ni HTTP i es pot provar sol. Cada canvi d'estat passa per
`apply_transition(current, target, actor=…)` i es comproven **dues coses**:

1. **Reachability.** `ALLOWED_TRANSITIONS` diu quins estats s'assoleixen des de
   quin. El que no hi és, no passa. Abans, l'única cosa que impedia una
   transició impossible era que ningú no n'hagués escrit el codi; això no és una
   garantia, és una absència.
2. **Autoritat.** `TransitionActor` diu qui ho intenta: `SYSTEM` (recalcular
   compliment, regenerar checklist, una recomanació nova — tot el que passa
   sense que ningú ho premi), `HUMAN_DECISION` o `HUMAN_FINAL_APPROVAL`.

**`go_approved`, `no_go_approved` i `submitted` no els pot escriure cap procés
automàtic.** `set_status()` és l'única porta cap a `BidWorkspace.status` a tot el
servei, i un test estructural (`test_the_service_writes_status_through_a_single_gate`)
falla si algú n'obre una altra: la porta humana no pot acabar sent un camp més
d'un `PATCH` genèric.

Una transició rebutjada surt com a `409 workspace_transition_rejected` amb
`current`, `target` i `actor` als detalls.

### `submitted` és inaccessible avui

`submitted` va més enllà de «només humans»: **no el pot escriure ni tan sols una
decisió GO**. Exigeix `HUMAN_FINAL_APPROVAL`, un actor que **cap funció del
sistema no fa servir encara**, i només és assolible des de
`ready_for_final_review`.

Això és deliberat. El dia que s'implementi la presentació, el camí natural serà
afegir codi a prop de la decisió de GO; si `HUMAN_DECISION` ja pogués escriure
`submitted`, aquell codi compilaria i passaria els tests. Amb un actor separat,
no: cinc tests el protegeixen, inclosos dos d'estructurals que comproven que
`SUBMITTED` només s'anomena a `enums.py` i `transitions.py`, i que
`HUMAN_FINAL_APPROVAL` no està connectat enlloc.

## La porta humana

Per registrar una decisió calen les quatre coses alhora:

1. usuari autenticat (`get_current_user`);
2. pertinença a l'empresa (`get_company_context`);
3. rol amb potestat — `owner`, `admin` o `bid_manager`; un `viewer` ho veu tot i
   no decideix res, i un `technical` prepara però no compromet l'empresa;
4. acció explícita al seu propi endpoint: `POST .../workspaces/{id}/decisions`.

Qui i quan queden a la fila, que **no s'actualitza mai**.

## Decisions: append-only

`WorkspaceDecision` no té `updated_at` a propòsit. Canviar de GO a NO-GO afegeix
una fila nova i conserva l'anterior amb el seu motiu. La vigent és l'última per
`decided_at`.

Cada decisió guarda **contra què** es va decidir: `compliance_run_id`,
`requirement_run_id`, `prefit_score` i el recompte de blocadors d'aquell moment.
Si després es recalcula qualsevol cosa, la decisió segueix dient el que se sabia
el dia que es va prendre.

## Blocadors: friccio, no prohibició

Abans de decidir es mostren, desglossats: bloqueigs confirmats, possibles,
obligatoris sense dades i obligatoris a revisar.

El sistema **no impedeix** aprovar un GO amb avisos oberts: una persona pot
tenir-hi una resposta que el motor no coneix (una pòlissa que es signa dilluns,
una UTE, una interpretació del plec). Prohibir-ho empenyeria la decisió fora de
l'eina, que és on deixa de quedar registrada.

El que sí que fa és **exigir `reason` per escrit** i deixar-lo a l'historial al
costat dels avisos que hi havia a la pantalla. Un NO-GO no necessita justificar-se
davant d'un avís: descartar-se davant d'un problema és el que l'avís ja suggereix.

## Checklist: determinista

De requisit a tasca acreditativa amb una **plantilla per categoria**
(`app/workspace/checklist.py`), no amb una redacció:

| Requisit | Item de checklist |
| --- | --- |
| `certification` · `{"name": "ISO 27001"}` | Acreditar ISO 27001 |
| `insurance` · `{"coverage_amount": 1000000}` | Aportar pòlissa RC ≥ 1.000.000 € |
| `experience` · `{"minimum_projects": 3}` | Acreditar 3 contractes similars |

Mateixa entrada, mateixa sortida, sempre. Si el requisit no porta prou dades
estructurades, es cau al títol que va extreure 4B: preferible a inventar un text.

Cada item generat conserva `requirement_id`, que és el que permet «Veure
requisit» i saltar a la cita del plec.

### Regenerar no duplica ni trepitja

L'índex únic parcial `(workspace_id, requirement_id)` fa que regenerar només
**afegeixi el que falta**. Un item ja completat, assignat o marcat com a no
aplicable no es toca mai, i els items manuals (amb `requirement_id` NULL) no hi
entren.

Quan apareix un `RequirementExtractionRun` nou —o un `CompanyComplianceRun`
nou— el workspace es marca com a **`analysis_outdated`** amb el motiu concret.
No se substitueix res en silenci: la persona decideix quan incorporar-ho.

## Termini

`days_remaining` es **calcula, no es persisteix**: demà seria mentida. El termini
autoritatiu és el del lot si el workspace n'és d'un, i si no el de l'expedient;
el `deadline_at` del workspace només és la fotografia de quan es va obrir.

Si el termini ha passat **no s'amaga**: surt un avís vermell prominent i
`deadline_passed` entra a la llista d'avisos oberts, de manera que un GO a partir
d'aquell moment exigeix motiu escrit. No es bloqueja el workspace: pot caldre
deixar constància del que es va decidir.

## Multi-tenant

Cada taula porta `company_id` i cada consulta hi filtra. Un workspace d'una altra
empresa retorna **404 i no 403**: confirmar-ne l'existència ja seria una fuita.

Els responsables surten dels `CompanyMembership` existents —no hi ha cap sistema
de persones a part— i assignar feina a algú de fora de l'empresa retorna
`assignee_not_in_company`.

## Aprovació final: preparat, no implementat

L'arquitectura ja preveu `ready_for_final_review` → aprovació humana →
`submitted`. **`submitted` no podrà venir mai d'una IA**: està a
`FINAL_APPROVAL_STATUSES`, i l'únic actor que hi arriba
(`HUMAN_FINAL_APPROVAL`) no el fa servir cap codi. Vegeu «`submitted` és
inaccessible avui».

Aquesta iteració **no** genera oferta ni memòria tècnica. S'atura a la decisió.

## API

Tot penja de `/companies/{company_id}`.

```
POST   /tenders/{tender_id}/workspace          obre (idempotent)
GET    /tenders/{tender_id}/workspace          la candidatura d'una licitació
GET    /workspaces                             llistat
GET    /workspaces/{id}                        detall complet
POST   /workspaces/{id}/decisions              decisió humana
GET    /workspaces/{id}/decisions              historial (append-only)
POST   /workspaces/{id}/checklist/regenerate   reconcilia amb l'anàlisi vigent
POST   /workspaces/{id}/checklist              item manual
PATCH  /workspaces/{id}/checklist/{item_id}
POST   /workspaces/{id}/tasks
PATCH  /workspaces/{id}/tasks/{task_id}
POST   /workspaces/{id}/notes
GET    /workspaces/{id}/notes
```

No hi ha cap `PATCH /workspaces/{id}`: no existeix cap manera d'escriure
`status` que no passi per una decisió signada.

## Dossier documental (5B)

Després d'un GO, el workspace es converteix en un dossier operatiu:

    Requirement -> què s'ha d'acreditar -> quina evidència corporativa tenim
                -> quin document s'aportarà -> què falta -> estat de preparació

### Cobertura NO és compliment

És la distinció que sosté tota la iteració, i la més fàcil de perdre:

| | Pregunta | Qui la respon |
| --- | --- | --- |
| **Compliment** (4C) | Podem acreditar que complim? | El motor de regles |
| **Cobertura del dossier** | Tenim el document preparat? | Les peces seleccionades |
| **Readiness** | El dossier està a punt per a la revisió final? | La derivació de 5B |

Un requisit pot estar en `satisfies` amb el dossier en `missing_document`. No és
una contradicció: saber que facturem prou no vol dir tenir els comptes anuals
signats a la carpeta. La pantalla ensenya les dues columnes de costat.

### `WorkspaceEvidenceItem`

Una peça = "amb aquest document acreditarem aquest requisit".

* **Un document pot cobrir N requisits** i **un requisit pot necessitar N
  documents** — per això és una taula d'items i no un camp. El fitxer **no es
  copia mai**: `document_id` apunta a la biblioteca corporativa que ja existeix.
  No hi ha cap segona biblioteca.
* **`suggested` no és `selected`.** El motor proposa el document que ell mateix
  va fer servir per avaluar el requisit (reutilitza `AssessmentEvidence`, no
  torna a buscar res). Només una persona el tria, i llavors s'escriuen
  `selected_by` i `selected_at`.
* Dos índexs únics parcials: un document no es proposa dues vegades per al
  mateix requisit, i hi ha un sol marcador «encara no tenim document» per
  requisit. Sincronitzar N vegades no duplica res ni trepitja cap tria humana.

### Una sola font de veritat per a la caducitat

`EvidenceItemStatus` **no té cap `expired`**. La caducitat es deriva sempre del
document i del termini; un estat manual paral·lel podria quedar-se enrere i dir
el contrari que les dates.

### Vigència respecte del termini

`document_validity()` és determinista i mira **el dia de presentació**, no avui:

| Cas | Estat |
| --- | --- |
| Caduca després del termini | `valid` |
| Vigent avui, **caduca abans del termini** | `expires_before_deadline` |
| Ja caducat | `expired` |
| Sense data | `no_expiry` |
| Sense fitxer | `no_document` |

`expires_before_deadline` és el cas que no es veu sol i el que fa perdre
licitacions. Un document seleccionat que hi cau **no cobreix**: compta com a
forat, perquè dir el contrari seria el pitjor error possible aquí.

### Readiness

Quatre estats — `not_started`, `incomplete`, `needs_review`,
`ready_for_final_review` — i **cap puntuació**. Un 73 % no diu a ningú què ha de
fer; «falten dos documents obligatoris i una tasca» sí. L'API retorna sempre la
llista de blocadors amb codis:

`decision_not_go` · `mandatory_unresolved` · `mandatory_checklist_blocked` ·
`mandatory_evidence_missing` · `mandatory_document_expired` ·
`mandatory_tasks_open` · `evidence_needs_review`

`ready_for_final_review` només si: hi ha decisió humana de **GO**, cap requisit
obligatori sense resoldre, cap item obligatori de checklist bloquejat, cap
evidència obligatòria que falti, cap document obligatori caducat per a aquest
plec i cap tasca obligatòria oberta. Un item de checklist `pending` **no**
bloqueja: el senyal fiable de si el document hi és el dona el dossier.

`evidence_needs_review` és l'únic blocador tou: si és l'únic, l'estat és
`needs_review` i no `incomplete`. `analysis_outdated` també bloqueja: si hi ha
una anàlisi o un càlcul més recents, el dossier ja no descriu el que el sistema
sap ara.

### Invalidació centralitzada

Un workspace **no es pot quedar etiquetat com a `ready_for_final_review` quan ja
no ho està**. `reconcile_readiness()` és l'únic lloc que el treu d'aquell estat:
recalcula, compara i, si cal, el fa retrocedir a `preparing_bid` amb
`set_status()` i l'actor `SYSTEM`. Cap mutació té la seva pròpia regla.

El retrocés és a `preparing_bid` i no a `analysis`: la feina feta no desapareix,
només deixa d'estar certificada com a completa.

Es reconcilia després de cada mutació (evidència, tasca, checklist) **i també en
llegir** el dossier, la readiness o el detall del workspace, de manera que la
pantalla es corregeix sola sense que ningú hagi de recordar-se de prémer res.

Casos coberts, tots pel mateix camí: es desselecciona un document obligatori ·
un document seleccionat deixa de ser vàlid per al termini · una evidència passa
a `missing` o a `needs_review` · es crea o es reobre una tasca obligatòria ·
apareix un mandatory unresolved nou · hi ha una anàlisi o un compliment nous. Si
la decisió humana deixa de ser GO, el camí és un altre i més directe: la pròpia
decisió escriu `no_go_approved`.

### L'estat guardat no és una credencial

`reconcile_readiness()` només actua quan algú mira o toca el workspace. Un canvi
extern que ningú no hagi consultat encara pot deixar l'etiqueta enrere fins a la
següent lectura.

Per això **cap codi es pot fiar de `workspace.status == ready_for_final_review`
per presentar**. `advance_to_final_review()` ja recalcula en el moment, i
l'aprovació final futura haurà de fer el mateix. Hi ha dos tests que ho fixen
ara: un demostra que l'etiqueta pot quedar obsoleta mentre el càlcul ja diu que
no, i un altre és estructural sobre el gate.

### Ready no és submitted

`POST /readiness/advance` porta `preparing_bid → ready_for_final_review` i **no
es pot forçar**: si el dossier no està a punt, retorna `409 workspace_not_ready`
amb la llista del que falta. Arribar-hi vol dir «el dossier sembla complet
perquè una persona el revisi», res més. La resposta porta `is_submitted` explícit
perquè la interfície no ho hagi de deduir.

L'invariant de 5A segueix intacte: `submitted` exigeix `HUMAN_FINAL_APPROVAL` i
cap codi el fa servir. El nom `SUBMITTED` només s'anomena a `enums.py` i
`transitions.py`; qui vol saber si una candidatura s'ha presentat crida
`transitions.is_submitted()`.

### Del forat a la tasca

`POST /dossier/{item_id}/task` crea la feina que tanca el forat. Títol per
plantilla —reaprofita el de la checklist i només en canvia el verb: «Acreditar
ISO 27001» → «Obtenir el document que acredita ISO 27001»—, **sense IA**. Si ja
hi ha una tasca oberta amb el mateix títol no se'n crea una altra. Les que
neixen d'un forat sobre un requisit obligatori surten amb
`WorkspaceTask.mandatory = true`, i mentre estiguin obertes bloquegen la
readiness.

### API del dossier

```
GET    /workspaces/{id}/dossier              vista agrupada + cobertura + readiness
POST   /workspaces/{id}/dossier/sync         proposa (idempotent, no tria)
POST   /workspaces/{id}/dossier              afegeix una evidència a mà
PATCH  /workspaces/{id}/dossier/{item_id}    selecciona / substitueix / descarta
POST   /workspaces/{id}/dossier/{item_id}/task
GET    /workspaces/{id}/readiness
POST   /workspaces/{id}/readiness/advance    -> ready_for_final_review
```

## Límits coneguts

* **No hi ha aprovació final ni presentació.** `preparing_bid`,
  `ready_for_final_review` i `submitted` existeixen al graf de transicions però
  cap flux hi porta encara. És intencional: el graf ja diu com serà el camí.
* **`analysis_outdated` és una marca calculada, no un estat.** Es deriva
  comparant les referències del workspace amb el que és vigent; no s'escriu a
  `status` perquè la decisió humana no s'ha de veure alterada per un recàlcul.
* **La checklist no es reordena ni es fusiona.** Si una anàlisi nova reformula un
  requisit, apareix un item nou al costat de l'antic. Fusionar-los demanaria
  decidir que són el mateix, i això encara no és automatitzable amb garanties.
