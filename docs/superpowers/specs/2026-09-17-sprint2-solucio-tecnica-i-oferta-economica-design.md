# Sprint 2 — Solució tècnica i oferta econòmica (S0: decisions tancades)

Data: 2026-09-17
Estat: decisions de producte **tancades**. Cap model, cap migració i cap
endpoint implementats encara.

---

## 0. Per què existeix aquest document

L'auditoria prèvia del Sprint 2 va deixar deu punts que no es podien resoldre
llegint el codi, perquè no eren preguntes tècniques: eren decisions de producte.
Aquest document les fixa perquè la implementació no les hagi de tornar a obrir
ni les hagi d'endevinar lot a lot.

Les decisions (seccions C-1 … C-10 i «Estat de la solució tècnica») són
**literals**: no s'hi reinterpreten. Tot el que hi ha sota l'epígraf
**Conseqüència** és enginyeria derivada d'aquella decisió, i això sí que es pot
discutir sense reobrir la decisió.

### 0.1 Abast

El pas **Preparar** d'una candidatura passa a cobrir la cadena real:

```
Plec → Requisit → Com ho solucionarem → Quant ens costa → Quin preu ofertarem
     → Amb quin marge → Amb quins documents ho acreditarem → Revisió final
```

Conceptualment, quatre dominis dins de Preparar:

| Domini | Estat |
| --- | --- |
| Pla de treball | **ja existeix**: `WorkspaceChecklistItem`, `WorkspaceTask`, `WorkspaceNote` |
| Solució tècnica | **nou** |
| Oferta econòmica | **nou** |
| Dossier | **ja existeix**: `WorkspaceEvidenceItem` |

### 0.2 La invariant que sosté tot el domini

**`compliance` no és `technical solution`.** Són dues preguntes i no es fusionen
mai, ni al model, ni al servei, ni a la pantalla:

| Pregunta | Qui la respon | On viu |
| --- | --- | --- |
| «Podem complir i acreditar-ho?» | motor 4C + revisió humana | `CompanyRequirementAssessment` |
| «Com ho oferirem concretament?» | les persones que preparen l'oferta | `WorkspaceTechnicalSolution` (nou) |

### 0.3 Fora de l'MVP

Escenaris múltiples de preu, simulador de puntuació, catàleg global de
productes i costos, integracions automàtiques amb proveïdors i preus de
competidors. El model pot deixar-hi espai; no se'n construeix res.

---

## C-1 · Lots

**Decisió**

- `EconomicOfferLot` existeix **sempre**.
- Workspace amb `tender_lot_id`: exactament **un** `EconomicOfferLot`, amb
  aquell mateix lot.
- Workspace global sobre una licitació amb lots: l'oferta conté **només els
  lots als quals realment ens presentem**.
- Licitació sense lots: un únic `EconomicOfferLot` amb `tender_lot_id` nul.
- `base_budget_without_tax` nul vol dir **«màxim no disponible»**. No és cap
  blocador i **mai** es presenta com «dins del pressupost».

**Conseqüència**

`BidWorkspace` ja té unicitat `(company_id, tender_id, tender_lot_id)` amb dos
índexs parcials, perquè a SQL `NULL ≠ NULL`. La coherència entre el lot del
workspace i els lots de l'oferta es valida al servei i es rebutja amb un error
propi (`offer_lot_mismatch`, 422), no amb una restricció de base de dades: una
restricció no pot llegir el workspace des de la taula de lots de l'oferta.

El tercer cas —workspace global amb lots— és el que fa que el subconjunt sigui
significatiu: que un lot **no** hi sigui és informació, no una dada que falti.

«Màxim no disponible» és un tercer valor i no es pot col·lapsar a cap dels dos
booleans: ni `exceeds = false` ni `dins de pressupost`. Veure C-5 i la regla de
límits del motor (S1, invariant 5).

---

## C-2 · Preu unitari màxim del plec

**Decisió**

- V1: **input manual i opcional**.
- **No** s'amplia `Requirement` ni l'extracció encara.
- La UI ha de poder explicar que és una **dada manual**.
- Si la línia està vinculada a un requisit, es conserva l'enllaç i la cita
  disponibles.
- No s'inventen fonts.

**Conseqüència**

El camp viu a la línia d'oferta (`tender_max_unit_price_ex_vat`), no al model
canònic de licitacions: el model canònic només conté el que publica la font, i
aquest valor surt de llegir el plec a mà.

Perquè la UI pugui dir-ho, la procedència ha de ser llegible, no deduïda del
fet que el camp estigui ple. La cita segueix sent la que ja penja del requisit
(`RequirementEvidence`), que és immutable: no se'n fa cap còpia.

---

## C-3 · Durada

**Decisió**

- **Mai inferida.**
- `duration_months` manual, per defecte **al lot**.
- `EconomicOfferLine` pot tenir `duration_months_override` nul·lable.
- `effective_duration` = l'override si existeix; si no, la durada del lot.
- `monthly` o `annual` sense durada efectiva: **totals temporals
  indeterminats**. Mai assumir 12.

**Conseqüència**

No hi ha cap camp de durada de contracte a `Tender` ni a `TenderLot`
(`CONTRACT_DURATION` de `ReferencePeriodType` és una altra cosa: el període de
referència d'un requisit de solvència). Per tant no hi ha res d'on inferir-la, i
aquesta decisió no renuncia a cap dada existent.

La resolució lot → línia és **responsabilitat de la capa de servei**. El motor
pur de S1 rep la durada efectiva **ja resolta** i no decideix cap herència.

---

## C-4 · Manifest

**Decisió**

- Sí, cal **manifest v2**.
- L'oferta econòmica i les solucions tècniques **validades** formen part del que
  es congela.
- Modificar una dada material després del freeze fa el paquet **stale**.
- Una aprovació anterior continua sent **històrica**.
- **No s'implementa encara**: serà **S7**.

**Conseqüència**

`MANIFEST_VERSION` passarà de `submission-manifest-v1` a `…-v2` i la
serialització canònica hi afegirà dos blocs. Com que `evaluate_staleness()`
recalcula el manifest amb el codi d'avui i el compara amb el hash guardat, tot
paquet congelat abans de S7 passarà a `manifest_changed`. Això és el
comportament correcte i volgut: aquells paquets es van congelar sense saber res
de l'oferta.

«L'aprovació anterior continua històrica» vol dir que no es toca cap fila de
`SubmissionPackageApproval`: la revisió queda `superseded`, que és el mecanisme
que ja existeix.

---

## C-5 · Readiness

**Decisió**

- Una **única** readiness final.
- Tres dominis independents alimenten la porta: solució tècnica, oferta
  econòmica i dossier.
- Una candidatura **no** està `ready_for_final_review` si falta una oferta
  requerida.
- **No hi ha dues portes finals diferents.**
- No s'inventen valors per fer passar workspaces existents.
- El seed s'adaptarà a **S8** amb dades explícites.

**Conseqüència**

Els blocadors nous entren a `ReadinessInput` i `evaluate_readiness()`
(`app/workspace/dossier.py`), que és un mòdul pur i s'hi manté. «Independents»
vol dir que cada domini aporta els seus codis i que un domini no emmascara els
altres: els blocadors s'acumulen, no es substitueixen.

L'abast dels blocadors de solució tècnica el limita **C-11**: no n'hi haurà cap
per als requisits que no tenen fila de solució.

«No inventar valors» té una conseqüència directa i acceptada: els workspaces
que existeixen avui **no** tindran oferta i, per tant, no arribaran a
`ready_for_final_review` fins que algú n'hi posi una o fins que quedi explícit
que no en requereixen. No s'hi aplicarà cap valor per defecte silenciós.

---

## C-6 · Visibilitat econòmica

**Decisió**

- `owner` / `admin` / `bid_manager`: poden **llegir i editar** cost, preu, marge
  i markup.
- `technical`: pot treballar la solució tècnica però **no** veure cost intern ni
  marges.
- `viewer`: tampoc.
- Protecció **a l'API**, no només al frontend.
- Constants explícites per a lectura i escriptura de preus.
- **No retornar** camps sensibles a rols no autoritzats.

**Conseqüència**

Dues constants noves al costat de `DECISION_ROLES`:

```
PRICING_READ_ROLES  = {owner, admin, bid_manager}
PRICING_WRITE_ROLES = {owner, admin, bid_manager}
```

Es defineixen **separades des del primer dia** encara que avui tinguin el mateix
contingut: el dia que divergeixin, el canvi ha de ser d'una constant i no d'una
recerca per tot el codi.

«No retornar» és més fort que «no mostrar»: els camps sensibles no poden sortir
del serialitzador. Això vol dir **dos esquemes de lectura** per a l'oferta —un
de complet i un de sense cost ni marge— i no un sol esquema amb valors a nul,
que no distingiria «no ho pots veure» de «no hi ha dada». Serà el primer permís
a nivell de camp del projecte.

---

## C-7 · Precisió

**Decisió**

- `unit_cost_ex_vat` i `offered_unit_price_ex_vat`: `Numeric(18,4)`.
- Imports totals: `Decimal` quantitzat a **2 decimals** en presentació i càlcul
  final.
- **Mai `Float`.**

**Conseqüència**

El `MONEY = Numeric(18,2)` que ja existeix a `models/tenders.py` continua sent
el tipus dels imports; els preus i costos unitaris necessiten un tipus propi de
quatre decimals. La política completa de quantització del motor és a S1
(«Política de Decimal»).

---

## C-8 · `annual`

**Decisió**

- Prorrateig matemàtic: `periods = duration_months / 12`.
- 30 mesos = **2,5** anualitats.
- **Cap avís** pel sol fet de no ser múltiple de 12.
- Si un proveïdor factura períodes sencers, s'ha de modelar **explícitament com
  una altra estructura**, no alterant `annual`.

**Conseqüència**

Queda descartada la proposta de l'auditoria de marcar les durades no múltiples
de 12 com a cas a revisar. `annual` és una base de facturació prorratejable i
prou; la facturació per períodes sencers és un concepte diferent que, si arriba,
serà una base nova.

---

## C-9 · Esborrat

**Decisió**

- **Cap hard delete** de dades econòmiques.
- Línies i seccions poden quedar **arxivades** (`deleted_at`) mentre són
  esborrany.
- El que està arxivat **no entra als càlculs**.
- Audit trail obligatori.
- Els snapshots congelats continuen **immutables**.

**Conseqüència**

`deleted_at` nul·lable a `EconomicOfferLine` i `EconomicOfferSection`. Els
endpoints de `DELETE` arxiven; no esborren. Totes les consultes de càlcul
filtren `deleted_at IS NULL`, i aquesta condició també viatja als índexs
parcials, com ja fa la resta del projecte.

«Els snapshots congelats continuen immutables» vol dir que arxivar una línia
després d'un freeze **no** en canvia el manifest: el canvia el fet que el
contingut viu ja no coincideix, i això és `stale` (C-4), no una reescriptura.

---

## C-10 · Proveïdor

**Decisió**

- **Cap taula `Supplier` a la V1.**
- Camps de text opcionals: `supplier_name`, `product_name`.
- El catàleg global de productes i proveïdors queda **fora de l'MVP**.

**Conseqüència**

Decau la proposta de l'auditoria d'una taula mínima de proveïdors de tenant. Els
dos camps viuen tant a `WorkspaceTechnicalSolution` com a `EconomicOfferLine`;
no es normalitzen ni es dedupliquen, i cap pantalla els ha de presentar com si
fossin entitats.

---

## Estat de la solució tècnica

**Decisió**

| Intern | UI |
| --- | --- |
| `not_started` | Sense definir |
| `drafting` | En preparació |
| `needs_validation` | Pendent de validar |
| `validated` | Validada |

`validated` continua sent **exclusivament humà**.

**Conseqüència**

El valor inicial és `not_started` (l'auditoria l'havia anomenat provisionalment
`undefined`; mana aquest). Com a la resta del projecte, l'enum es materialitza
amb `enum_column()` —VARCHAR + CHECK, no un tipus natiu de PostgreSQL— i els
textos de la UI surten del diccionari de `apps/web/src/lib/labels.ts`, mai d'un
`if` al JSX.

«Exclusivament humà» vol dir que `validated` no és accessible des del `PATCH`
genèric: hi ha un endpoint propi de validació, amb qui signa, quan i per què.
Cap IA escriu res en aquesta capa.

---

## C-11 · Qui decideix que un requisit necessita solució tècnica

**Decisió**

- **No** hi haurà cap `/sync` automàtic de requisits a solucions tècniques.
- **No** s'inferirà per categoria quins requisits necessiten solució tècnica.
- La selecció és **humana**. A la UI, un requisit tindrà una acció equivalent a
  «Defineix una solució tècnica», que crea explícitament una
  `WorkspaceTechnicalSolution` amb `source = requirement`, el `requirement_id`
  del requisit escollit i `status = not_started`.

**Conseqüència**

Hi ha **dos camins de creació diferenciats**, i el client no tria mai el
`source`: el servidor el fixa. La creació manual lliure deixa `requirement_id`
NULL i `source = manual`; la creació des d'un requisit és un endpoint propi
(`.../technical-solutions/from-requirement`) i és l'únic camí que escriu
`source = requirement`.

La conseqüència important és per a la **readiness de S6** (veure C-5): a la V1
**no** s'implementarà cap blocador `technical_solution_missing` sobre requisits
que no tenen fila de solució. Ningú no ha declarat que en necessitin, i el
sistema no té cap base factual per afirmar que n'hi falta una: inferir-ho seria
inventar una obligació. Es preserva així la distinció que sosté tot el domini,
`unknown` ≠ `missing`.

Sí que podran ser blocadors, perquè parlen de files que **existeixen** i que per
tant algú ha declarat necessàries:

| Codi | Quan |
| --- | --- |
| `technical_solution_unvalidated` | hi ha solució i ningú no l'ha validada |
| `technical_solution_validation_stale` | hi ha validació, però ja no és vigent |

---

## C-12 · Política d'edició després d'un freeze o en estats terminals

**Pendent de decidir.** Aquí no s'hi fixa cap regla: es documenta el que hi ha
avui i es deixa per a S6/S7, perquè és transversal i no pot decidir-la un lot.

**Què fa el sistema avui** (auditat el 2026-09-18 sobre `WorkspaceChecklistItem`,
`WorkspaceTask`, `WorkspaceNote` i el dossier / `WorkspaceEvidenceItem`):

- **No hi ha cap regla transversal de «workspace encara editable».** Cap dels
  camins d'escriptura de Preparar consulta `workspace.status`. Ni
  `no_go_approved`, ni `withdrawn`, ni `submitted`, ni
  `ready_for_final_review`, ni `approved_for_submission` bloquegen res.
- `is_submitted()` (`app/workspace/transitions.py`) existeix, però **només
  s'utilitza com a indicador de lectura** a dos llocs
  (`routers/dossier.py`, `routers/submission.py`): pinta l'estat, no barra el pas.
- **La immutabilitat viu al paquet, no a la candidatura.** Des de `frozen`, la
  revisió del paquet és una fotografia immutable; el que es fa amb els canvis
  posteriors no és impedir-los, sinó **detectar-los**: en aprovar,
  `evaluate_staleness()` compara i, si el que hi ha ara ja no és el que es va
  congelar, el paquet passa a `superseded` i l'aprovació es rebutja
  (`services/submission_package.py`).

O sigui que el patró real del projecte és **«Preparar continua editable i la
fotografia se n'assabenta»**, no «després del freeze no es toca res».

**Conseqüència per a S3.** `WorkspaceTechnicalSolution` es comporta igual que la
resta de Preparar: es pot editar i validar en qualsevol estat. **No s'hi ha posat
cap política pròpia**, perquè una regla exclusiva per a la solució tècnica faria
que el pas Preparar es comportés de dues maneres diferents segons la pestanya.

**Què cal decidir, i on:**

| Pregunta | Lot |
| --- | --- |
| Hi ha d'haver un estat a partir del qual Preparar deixi de ser editable, per a **tots** els dominis? | S6 |
| La solució tècnica ha d'entrar a `evaluate_staleness()`, perquè editar-la després d'un freeze superi el paquet com ja fa el dossier? | S7 (amb C-4) |

Mentre no es decideixi, val el comportament d'avui, que és el mateix per a tots
els dominis de Preparar.

---

## Ordre d'implementació

| Lot | Contingut | Estat |
| --- | --- | --- |
| **S0** | Aquest document | **fet** |
| **S1** | Motor econòmic pur (`Decimal`, sense ORM ni HTTP) | en curs |
| **S2** | Models + migració de la solució tècnica | pendent |
| **S3** | Servei i endpoints de solució tècnica, validació humana | pendent |
| **S4** | Models + migració de l'oferta | pendent |
| **S5** | Servei i endpoints d'oferta, coherència de lots (C-1), permisos (C-6) | pendent |
| **S6** | Blocadors nous de readiness (C-5) | pendent |
| **S7** | Manifest v2 i staleness (C-4) | pendent |
| **S8** | Seed demo amb dades econòmiques explícites | pendent |
| **S9** | Frontend: Preparar amb els quatre dominis | pendent |
| **S10** | Verificació: aïllament per `company_id`, permisos per rol | pendent |
