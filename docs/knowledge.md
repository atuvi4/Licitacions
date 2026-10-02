# Coneixement de l'empresa

> Fase 2 del [motor d'anàlisi](analysis-engine.md): 2A, model i fotografia;
> 2B, declaració directa i pantalla de validació. Data: 2026-09-30.

Aquesta capa respon una pregunta:

> **Què és i què pot fer avui una empresa**, i amb quina garantia ho sabem?

No compara res amb cap plec (això és la fase 4) i no calcula cap oferta. Deixa el
coneixement de l'empresa estructurat, amb procedència, i diu què falta validar.

## 1. Dues naturaleses que no es barregen

| | **Estat actual** | **Històric** |
| --- | --- | --- |
| Què és | El que l'empresa és i pot fer avui | El que va fer o dir en licitacions passades |
| On viu | Taules de coneixement amb procedència | `historic_bids` i `knowledge_observations` |
| Qui l'escriu | Una persona (declara, o valida una observació) | Una importació |
| Pot satisfer un requisit? | Sí | **Mai** |

L'històric suggereix i genera preguntes. Només una persona el pot convertir en
coneixement actual, i queda la traça en tots dos sentits. No hi ha cap promoció
automàtica.

## 2. Vocabularis globals

Són catàlegs compartits, sense `company_id`, com `cpv_codes`:

| Taula | Contingut |
| --- | --- |
| `service_lines` | `voice_pbx`, `voice_trunk`, `access_ftth`, `dedicated_network`, `radio_link`, `network_switching`, `fiber_deployment`, `civil_works`, `support_ops`, `mobile_telephony` (afegida a la `0046`) |
| `capability_definitions` | 19 capacitats per al pilot de l'Escala. Cadascuna té tipus (`boolean` · `number` · `ordinal`), comparador (`is_true` · `gte` · `lte`), unitat i escala |
| `certification_standards` | `ENS` (nivells `basic < medium < high`), `ISO_9001`, `ISO_14001`, `ISO_27001` |

* **La font de veritat és `app/knowledge/vocabulary.py`.** La migració `0045` en
  copia les dades; una ampliació posterior és una migració que només afegeix files
  (`0046`: telefonia mòbil). Un test comprova que, sumades, coincideixen amb el codi.
* **Codis tancats.** Cap API no hi escriu, i un model de llenguatge no pot
  inventar codis. Un codi nou és un canvi de codi i de migració.
* **No porten el nom de cap empresa.** Un test ho vigila.
* **Esborrar una empresa no els toca.**

## 3. Estat actual

### Procedència

Totes les files de coneixement actual porten aquests camps:

| Camp | Valors | Per a què |
| --- | --- | --- |
| `knowledge_origin` | `declared` · `promoted_observation` | Qui ho afirma |
| `as_of` | data | De quan és la dada |
| `valid_until` | data o nul | Fins quan val |
| `verification_status` | `pending_verification` · `verified` | `verified` **només amb evidència enllaçada** (§3.1) |
| `lifecycle` | `current` · `in_progress` · `retired` | Només `current` compta |

### Taules

| Concepte | Taula | Nou o ampliat |
| --- | --- | --- |
| Fets clau (operador CNMC, NRN, CPD Tier-3, FTTH pròpia, 24x7) | `company_operating_facts` | Nova. Una fila per clau (`key` tancada) i `value` booleà. |
| Serveis | `company_services` | Ampliada: `service_line_code`, `delivery_mode` i procedència. **`active` fa de cicle de vida.** |
| Certificacions | `company_certifications` | Ampliada: `standard_code`, `level`, `scope_service_lines` (abast estructurat, §3.2), `knowledge_origin`, `as_of` i `lifecycle`. **`verification_status` i `expires_at` ja hi eren i fan de verificació i vigència.** El text de `scope` es queda com a nota. |
| Solucions | `company_solutions` | Nova (línia, nom, fabricant, edició) |
| Capacitats d'una solució | `solution_capabilities` | Nova. `status` (`supported` · `supported_with_limits` · `not_supported`) i valor numèric o de l'escala. **Sense fila = desconegut, no «no».** |
| Productes | `company_products` | Nova. **Sense preus.** |
| Productes d'una solució | `solution_products` | Nova |
| Tercers | `company_partners` | Nova. Una sola taula per a proveïdors, operadors majoristes, subcontractistes i socis. **Sense condicions econòmiques.** |
| Papers d'un tercer | `partner_roles` | Nova (paper, línia, territori) |
| Cobertura territorial d'un fet, d'un servei o d'un paper | `knowledge_coverage_areas` + `coverage_completeness` | `0047` (§3.3). Una fila per àrea, penjada de la dada per `entity_type` + `entity_id`. |
| Solvència econòmica (volum de negoci per exercici) | `company_financial_years` | Ampliada a `0052` (§3.4): origen, `as_of`, verificació i cicle de vida. Import `Decimal` i moneda; l'any fa de vigència. |
| Referències d'execució | `company_projects` + `company_project_annual_amounts` + `company_project_service_lines` | Ampliada a `0052`. Un projecte `completed` amb import i data. Les línies de servei que acredita són les de `company_project_service_lines` (`0053`, directes del vocabulari): no depenen dels serveis que l'empresa ofereix avui. `project_services` és **legacy** (§3.5). **No és cap `historic_bid`**: una oferta presentada no acredita execució. Els imports **executats per any** (una fila per projecte i any, nova a `0052`) són el que es compara amb «l'any de major execució»; sense desglossament, l'import d'un projecte plurianual no s'imputa a cap any. |
| Assegurances | `company_insurances` + `company_insurance_limits` | Ampliada a `0052`: origen i verificació; `expires_at` fa de vigència. La fitxa (pòlissa + conjunt complet de límits) es desa en una sola transacció (§3.5). Límits estructurats per tipus (`per_claim`, `annual_aggregate`, `per_victim`, `employer_per_victim` = RC patronal per víctima, …). Cap número de compte ni dada bancària. |
| Registres i habilitacions | `company_registrations` | Ampliada a `0052`: origen, verificació, tipus `sector_operator_registry` (l'emissor és a `issuer`, cap registre concret al codi) i **abast estructurat** `scope_service_lines`. Sense abast estructurat, `scope_unknown`. |
| Persones i perfils | `company_people` | Ampliada a `0052`: origen, verificació i `profile_codes` (perfils del vocabulari, una persona en pot cobrir diversos). Dades personals mínimes; la fotografia no porta noms. **Disponible no vol dir adscrit.** |
| Equips i instruments | `company_equipment` | Nova a `0052`: tipus tancat (`otdr`, `olts`, `fusion_splicer`, …), fabricant, model, sèrie, mode d'accés, unitats, `calibration_required` (nul = no consta) i `calibration_valid_until`, amb procedència completa. **No és cap producte que es vengui.** |
| Fets quantitatius | `company_operating_facts.value_number` | `0052`: només a les claus numèriques del vocabulari (`ipv4_assignable_count`); el CHECK ho imposa. Un fet booleà no porta nombre. |

Les filles referencien el pare per `(id, company_id)`: res d'una empresa no pot
penjar d'una fila d'una altra. Tots els enums porten CHECK.

Certificacions i serveis es declaren amb els CRUD que ja existien. Ara accepten
norma i nivell (validats contra el vocabulari), abast estructurat i línia de
servei.

### 3.4 Qualificació i mitjans (fase 3A, pas 5: `0052`)

El que un plec demana per qualificar una empresa ja tenia taula al perfil, però
fora del coneixement actual. A `0052` en forma part, amb el mínim de canvis:

* **Mateixa procedència.** Exercicis, projectes, pòlisses, registres i persones
  porten `knowledge_origin`, `as_of`, `verification_status` i `lifecycle`; els
  equips, també `valid_until`. Una alta neix `declared` i pendent; `verified` només
  amb evidència enllaçada (§3.1), als CRUD de sempre (`/financial-years`,
  `/projects`, `/insurances`, `/registrations`, `/people`) i, per als equips i els
  fets, a `/knowledge/records/{kind}`. Tots tenen `/verify`.
* **Una edició material desverifica.** També quan canvia una filla que forma part
  del que es va verificar: un límit d'una pòlissa o els serveis d'un projecte.
* **Vocabularis tancats.** Perfils professionals i tipus d'equip viuen a
  `app/knowledge/vocabulary.py` (`PROFESSIONAL_PROFILES`, `EquipmentType`) i es
  llegeixen a `/knowledge/vocabulary`. El tipus de valor de cada fet operatiu
  (booleà o numèric) també (`OPERATING_FACT_VALUE_TYPES`).
* **Què no s'hi guarda.** Ni imports de pòlisses que no siguin límits, ni números
  de compte, ni dades personals que no calguin (DNI, adreça, salut), ni preus
  d'equips.

Les files que ja hi havia queden `declared`, `current` i pendents de verificar.
`as_of` és **explícit** en aquestes cinc taules: nul vol dir que ningú no ha dit
de quan és la dada (totes les files anteriors i les que no la informen), i la
fotografia hi posa la data de creació en UTC. Cap backfill ni cap valor per
defecte: així el downgrade decideix per valors explícits, sense comparar dates
ni dependre de cap zona horària. **Entren a la fotografia**: una empresa que ja
tenia exercicis, projectes, pòlisses, registres o persones canvia d'empremta;
una que no en tenia, no.

### 3.5 La fitxa sencera, d'un sol cop (fase 3A, pas 6: `0053`)

**Assegurança = una transacció.** L'alta i la modificació d'una pòlissa
(`POST`/`PATCH /insurances`) accepten `limits`, el CONJUNT COMPLET de límits
(`kind`, `amount`, `currency`, `notes`; un tipus només una vegada). El CRUD aplica
la pòlissa i el conjunt (tipus per tipus: el que hi és s'actualitza, el que falta
es treu, el que és nou s'afegeix) i fa **un sol commit**: si qualsevol límit o la
pòlissa falla, no en queda res (ni pòlissa nova, ni conjunt parcial). Un canvi
del conjunt d'una pòlissa verificada la torna a pendent; el mateix conjunt no és
cap canvi. No enviar `limits` els deixa com estan. La lectura porta `limits`.
L'API granular `/insurances/{id}/limits` continua existint per a la pantalla
antiga; la pantalla nova no la fa servir.

**Línies directes de les referències (`0053`).** `company_project_service_lines`
(projecte + línia del vocabulari, PK composta, CASCADE amb el projecte, RESTRICT
amb el vocabulari) és la **font de veritat** de les línies que acredita una
referència: la fotografia, l'empremta i l'anàlisi (R2) només llegeixen aquesta
taula. L'API de projectes hi escriu amb `service_line_codes` (conjunt complet,
codis del vocabulari; canviar-lo en una referència verificada la desverifica).
Una referència de veu IP continua acreditant veu IP encara que avui l'empresa no
tingui cap servei de veu IP.

* **Upgrade:** backfill determinista des de `project_services` JOIN
  `company_services` (línies no nul·les, sense duplicats): exactament el que
  `0052` llegia, i cap empremta canvia.
* **`project_services` queda LEGACY**, només per compatibilitat d'API
  (`service_ids` a l'alta i al PATCH de projectes, `services` a la lectura, i
  `PUT`/`DELETE /projects/{id}/services/{service_id}`). No s'esborra i no hi ha
  sincronització en cap sentit: canviar els serveis **no** canvia les línies que
  acredita la referència ni en desfà la verificació, i un projecte creat només amb
  `service_ids` no acredita cap línia. La UI nova mai l'envia, i la pantalla antiga
  `/experiencia` redirigeix a la secció nova.
* **Downgrade: conjunts exactes.** Per a cada projecte, A = línies de
  `company_project_service_lines` i B = línies dels serveis dels enllaços
  `project_services` que JA EXISTEIXEN. Només passa si A == B a tots els projectes;
  si A − B no és buit (`0052` perdria una línia, encara que hi hagi un servei
  compatible no vinculat) o B − A no és buit (`0052` tornaria a afirmar una línia
  treta), refusa amb `downgrade_0053_incompatible_rows` abans de cap DDL. Si passa,
  només elimina la taula: cap INSERT, UPDATE ni DELETE a `project_services`.

### 3.1 Verificat vol dir amb evidència

`verification_status = verified` **només pot existir si el registre té almenys un
document enllaçat** (`document_links`). Una revisió humana sense evidència
continua sent `pending_verification`.

Com que els enllaços són polimòrfics i la base no ho pot imposar, la regla és a
`app/services/knowledge_evidence.py` i s'aplica a totes les portes:

* **`POST /knowledge/records/{kind}/{id}/verify`** retorna
  `422 evidence_required` si no hi ha cap document enllaçat. La dada no canvia.
* **Els CRUD de certificacions i serveis** refusen desar un `verified` sense
  evidència (`build_crud_router(..., validate=...)`). Un registre nou no pot
  néixer verificat.
* **`import_knowledge`** refusa qualsevol dada verificada: un document importat
  no porta evidència.
* **Treure l'última evidència** (desenllaçar-la o esborrar el document) torna el
  registre a `pending_verification`.
* **El snapshot** tampoc no es creu un `verified` sense evidència (dades
  anteriors a la regla). El compta com a declarat i ho diu a
  `pending_validation`.
* **Una fila antiga `verified` sense evidència continua sent editable.** Si es
  modifica per una altra cosa (l'abast, una data), el canvi es desa i la fila
  torna a `pending_verification`, que és el que el snapshot ja en deia. Només
  es refusa (`422`) quan la petició demana explícitament `verified`.

Cada afirmació del snapshot porta `has_evidence`. Així es distingeix una dada
declarada **sense evidència** d'una que **ja en té i només falta verificar**.

#### Una verificació val per al que es va verificar

```text
verificada + evidència  ──edició material──▶  pendent de verificar + evidència
```

**Modificar materialment una dada verificada n'invalida la verificació.** Torna
a `pending_verification` i **l'evidència es conserva**: queda «declarada,
pendent de verificar», no «sense evidència». Per tornar-la a verificar cal
`/verify`.

* **Val per a tot el que es pot editar**: certificacions, serveis, fets,
  solucions, capacitats, productes, tercers i els seus papers.
* **És material qualsevol camp**, tret de `notes`. Canviar el nivell, l'abast,
  la vigència, el valor o el nom desverifica; retocar una nota interna, no.
* **Desar el mateix valor no és un canvi.** Un formulari que envia tots els
  camps sense tocar-ne cap no desverifica res.
* **Editar i demanar `verified` a la mateixa petició** no salta el pas: la dada
  queda pendent.

La regla és a `knowledge_evidence.unverify_if_materially_changed`, i la fan
servir tant els CRUD de certificacions i serveis com `update_record`.

Totes les entitats de coneixement nou (fets, solucions, capacitats, productes,
tercers, papers) són ara `LinkedEntityType`, i s'hi poden enllaçar documents amb
l'API de documents que ja existia.

### 3.2 Abast d'una certificació

`scope_service_lines` és la llista de línies de servei que cobreix, validada
contra el vocabulari. El text `scope` es conserva com a nota, però **no es llegeix
mai per deduir-ne cobertura**. Sense abast estructurat, la certificació surt amb
`scope_status = scope_unknown` i com a pendent (`scope_unknown`).

### 3.3 Cobertura territorial (fase 3A, pas 1: `0047`)

Un fet operatiu, un servei o un paper d'un tercer poden dir **on** són certs i
**si la llista és tota**:

* `coverage_completeness` a la fila: `complete` («aquestes són totes les
  àrees»), `partial` («n'hi ha més que no consten») o `unknown`;
* `knowledge_coverage_areas`: una fila per àrea, amb `kind` (`municipality`,
  `comarca`, `province`, `region`, `country`, `address`, `custom`), el `label`
  tal com el diu l'empresa i, opcionalment, un `code` i el seu `code_system`
  (INE, NUTS…). Sense FK a la dada, com els enllaços d'evidència: hi penja per
  `entity_type` (`operating_fact` · `service` · `partner_role`) + `entity_id`, i
  esborrar la dada (o el tercer, per als seus papers) la neteja.

**L'estat neutre no afirma res.** `unknown` sense cap àrea és el que té tota
dada d'abans de la `0047`: la migració hi posa `unknown` a tot, no crea cap
àrea i **no llegeix ni transforma cap text** (`detail` del fet, `territory` del
paper es queden com estan). La fotografia omet aquest estat del seu payload
canònic, de manera que **migrar no canvia cap empremta**. Qualsevol altre estat
sí que afirma alguna cosa i hi entra, també `complete` sense àrees («enlloc»)
i `partial` sense àrees («en algun lloc que no consta»).

**Les àrees són un conjunt.** La identitat d'una àrea és `(kind, code_system,
code, label)` amb el nom normalitzat (espais; majúscules i accents compten).
Un payload amb la mateixa àrea dues vegades **no es refusa: es desduplica
determinísticament** (la primera es queda), com `scope_service_lines` d'una
certificació; la base ho garanteix amb un índex únic per dada (els NULL de
codi compten com a cadena buida). Un duplicat no crea cap fila, no surt dues
vegades, no canvia l'empremta i no és cap canvi material. No s'hi resol cap
equivalència geogràfica: «Banyoles» i «17015/INE» són dues àrees.

**Municipis = INE (convenció de la UI).** L'editor mostra, per a un municipi,
«Tipus | Nom | Codi INE» i cap camp de sistema: en enviar, `code_system` és
sempre `"INE"` (amb codi o sense; el codi continua sent opcional). Un municipi
antic sense sistema es mostra igual i només es normalitza a `INE` quan es desa
un canvi material. Canviar el tipus d'una fila buida el codi i el sistema (un
codi INE no és cap NUTS); la resta de tipus conserven el sistema lliure.
L'API no ho imposa: és la UI qui ho fa, i el model continua acceptant qualsevol
`code_system`.

**Un canvi material de cobertura és un canvi material de la dada** (§3.1):
canviar la completesa o afegir, treure o modificar una àrea (nom, codi o
sistema de codis) torna una dada verificada a `pending_verification` i
conserva l'evidència. El mateix contingut —en un altre ordre o amb espais de
més— no és cap canvi i no desverifica res.

**Conservar no és comparar.** La fotografia guarda la cobertura tal qual i no
en dedueix res: que un municipi no hi sigui no vol dir que no es cobreixi. El
comparador territorial (plec ↔ empresa) és d'una fase posterior.

## 4. Històric

* **`historic_bids`** guarda les licitacions analitzades: referència,
  comprador, data, línies de servei, PBL (públic), decisió i motiu, i resultat.
  **No guarda oferta, baixa, marge ni preus de competidors.**
* **`knowledge_observations`** guarda què diu l'històric de l'empresa, amb una
  `proposal`: la fila exacta que en naixeria si una persona la promogués.
  Estats: `candidate` → `promoted` o `dismissed`, sempre per una persona.
  * **Promoure-la** crea coneixement actual amb `knowledge_origin =
    promoted_observation` i **pendent de verificar**: validar el que deia
    l'històric no és aportar-ne evidència. L'observació apunta a la fila creada.
  * **Una afirmació negativa no es pot promoure a certificació.** Per exemple,
    «no tenien ENS»: el perfil no guarda certificacions que no es tenen. Es
    descarta.
  * **Si ja hi ha coneixement actual sobre la mateixa clau, la promoció es
    refusa** (`409 knowledge_already_declared`): mana el que s'ha declarat.

## 5. KnowledgeSnapshot

`app/knowledge/snapshot.py` és pur: rep les files d'una empresa i una data, i
retorna la fotografia efectiva.

### Precedència

Per a cada clau (una norma, un fet, una capacitat…):

1. **actual verificat**: `lifecycle = current`, vigent a la data i `verified`;
2. **actual declarat**: igual, però no verificat. Pot satisfer, amb
   `accreditation_pending = true`;
3. **històric**: només observacions `candidate`. Surten com a pistes (`hints`) i
   com a preguntes, i **no poden satisfer res**;
4. **desconegut**: no hi ha res. **L'absència no és «no»: és `unknown`.**

Una fila `in_progress`, `retired` o caducada (`valid_until` anterior a la data)
no compta. Les `in_progress` i les caducades surten com a pendents. Si un soci o
una solució no compta, tampoc no hi compten els seus papers ni les seves
capacitats.

### Avaluacions

`evaluate_certification`, `evaluate_fact` i `evaluate_capability` retornen
`satisfies` · `does_not_satisfy` · `unknown`, amb la font, el motiu, les files
que ho sostenen i les pistes de l'històric:

* **Certificacions.** Si alguna fila verificada compleix el nivell, `satisfies`
  verificat. Si no, si en compleix alguna de declarada, `satisfies` declarat amb
  acreditació pendent. Si n'hi ha d'actuals però amb un nivell inferior,
  `does_not_satisfy`. Si no n'hi ha cap, `unknown`. ENS `medium` satisfà
  `medium` i `basic`, però no `high`.
* **Abast.** Amb `service_lines`, cada línia ha de ser a l'abast estructurat:
  * si hi és: satisfà;
  * si falta alguna línia: `does_not_satisfy` (`scope_not_covered`);
  * si l'abast no s'ha estructurat: `unknown` (`scope_unknown`).

  Primer es mira el nivell i després l'abast.
* **Fets.** Un fet actual negatiu (`own_ftth_infrastructure = false`) respon
  `does_not_satisfy`. És diferent de no saber-ho.
* **Capacitats.** Es compara amb el comparador del vocabulari:
  * `number` amb `gte` o `lte`;
  * `ordinal` amb la posició a l'escala;
  * `not_supported` = no compleix;
  * suportada sense valor = `unknown`;
  * `supported_with_limits` satisfà, però ho diu.

### Empremta

`fingerprint` és l'SHA-256 de les files actuals que compten, amb els seus valors
i la seva procedència, en ordre canònic:

* **és estable:** no depèn de l'ordre ni de les marques de temps;
* **canvia** si canvia el coneixement actual (nivell, verificació, vigència…),
  també la cobertura territorial quan en diu alguna cosa (§3.3): hi entren
  `completeness` i, per àrea, `kind`, `label` (espais normalitzats), `code` i
  `code_system`, ordenades per (kind, code_system, code, label);
* **omet l'estat neutre de cobertura** (`unknown` sense àrees): la `0047` no
  mou cap empremta;
* **no inclou l'històric:** canviar una observació no canvia el que l'empresa
  pot acreditar;
* **inclou la qualificació** (`0052`): cada exercici, referència, pòlissa (amb
  els límits), registre (amb l'abast), persona (amb els perfils, sense el nom) i
  equip (amb el calibratge) que compta. Un fet booleà sense nombre deixa la
  mateixa empremta que abans de `0052`.

Les avaluacions de qualificació (`evaluate_turnover`, `evaluate_execution`,
`evaluate_insurance`, `evaluate_registration`, `evaluate_profiles`,
`evaluate_equipment`, `evaluate_numeric_fact`) segueixen la mateixa regla:
`does_not_satisfy` només quan la dada actual és completa i no hi arriba (tots
els exercicis de la finestra per sota, un límit declarat per sota, un calibratge
caducat, un fet quantitatiu inferior). Un exercici que falta, un límit que no
consta, un abast sense estructurar o que no cobreix tots els serveis (ampliar
una inscripció és un tràmit), una persona que no hi és (es pot contractar) o una
pòlissa caducada són `unknown`.

**Cap finestra temporal depèn del rellotge.**

* **Volum de negoci («el millor dels 3 últims exercicis»).** «Últims» és relatiu
  al plec: sense termini de presentació (`tenders.deadline_at`), `unknown`. Mai
  «els més recents que hi ha a la base»: un plec antic no es pot satisfer amb
  exercicis posteriors. Un exercici és una fila de `company_financial_years`;
  compten els 3 més recents ANTERIORS a l'any del termini, consecutius. Si no n'hi
  ha 3 de consecutius, `unknown` encara que algun hi arribi. Un exercici antic,
  fora d'aquests 3, no satisfà mai.
* **Execució («l'any de major execució dels darrers 3 anys»).** La finestra són
  els 3 anys naturals anteriors a l'any del termini de presentació del plec.
  Sense termini, `unknown`. La unitat és l'import EXECUTAT en un any, no l'estat
  del projecte: un projecte en curs compta pels anys que ja ha executat. Per any se
  sumen els imports executats d'aquell any dels projectes rellevants (el
  desglossament anual i, si un projecte acabat cau sencer dins d'un sol any i no
  té desglossament, el seu import). No compten l'import contractat d'un projecte
  en curs sense execució anual, ni un projecte `planned`. Si cap any
  hi arriba però hi ha imports plurianuals sense desglossar que podrien tocar la
  finestra, `unknown`; només amb el conjunt complet és `does_not_satisfy`.

## 6. API

Totes les rutes pengen de `/companies/{company_id}/knowledge`:

| Ruta | Qui |
| --- | --- |
| `GET /snapshot?on=AAAA-MM-DD` | Qualsevol membre. Inclou `pending_validation`: què falta validar. |
| `GET /observations?status=` | Qualsevol membre |
| `POST /observations/{id}/promote` | `owner` · `admin` · `bid_manager` |
| `POST /observations/{id}/dismiss` (amb nota) | Ídem |
| `POST /records/{kind}/{id}/verify` | `owner` · `admin` |
| `GET /vocabulary` | Qualsevol membre. Línies de servei, capacitats i normes, només lectura. |
| `GET /records` | Qualsevol membre. Les files declarables, per editar-les. **No porten `verification_status`**: l'estat efectiu el diu el snapshot. |
| `POST /records/{kind}` | `owner` · `admin` · `bid_manager` · `technical`. Declara coneixement actual. |
| `PATCH /records/{kind}/{id}` | Ídem. Editar i retirar. |
| `DELETE /records/{kind}/{id}` | `owner` · `admin` |

Una fila o una observació d'una altra empresa dona `404`. L'auditoria porta
identificadors i codis, mai el contingut.

### Qui pot modificar el coneixement

La política és a `app/knowledge/access.py`:

| Acció | Rols |
| --- | --- |
| Llegir | Qualsevol membre |
| Declarar, editar, retirar, i posar o treure evidència | `owner` · `admin` · `bid_manager` · `technical` |
| Eliminar físicament | `owner` · `admin` |
| Confirmar o descartar l'històric | `owner` · `admin` · `bid_manager` |
| Verificar | `owner` · `admin` |

**Qui només consulta (`viewer`) no modifica res**, tampoc per les rutes que ja
existien: els CRUD de certificacions i serveis i els enllaços d'evidència de
qualsevol dada de coneixement apliquen la mateixa política. Treure l'última
evidència desverifica una dada, i per això també compta com a modificar-la.

Una escriptura sense permís respon `403 permission_denied`: qui pregunta és
membre i pot llegir la dada. Confirmar, descartar i verificar continuen responent
`404`, com a la fase 2A.

### Declaració directa

`{kind}` és `operating_fact`, `solution`, `solution_capability`, `product`,
`solution_product`, `partner` o `partner_role`. Les certificacions i els serveis
es declaren amb els seus CRUD.

* **Tot el que es declara neix `declared` i `pending_verification`.** Cap
  payload no accepta `verification_status` ni `knowledge_origin`.
* **Una alta és la mateixa proposta que portaria una observació**, més la
  procedència (`as_of`, `valid_until`, `lifecycle`). Promoure i declarar passen
  pel mateix constructor (`_new_entity`): només canvia l'origen.
* **El vocabulari mana.** Una línia, una capacitat o un valor fora del
  vocabulari dona `422`: una capacitat booleana no porta valor, una numèrica
  porta un nombre i una ordinal, un nivell de la seva escala.
* **Les filles pengen del pare de la mateixa empresa** (capacitats d'una
  solució, papers d'un tercer). Si el pare és d'una altra empresa, `404`.
* **Declarar dues vegades la mateixa clau** dona `409 knowledge_already_declared`.
* **Modificar** no toca l'origen. Si la dada era verificada i el canvi és
  material, torna a pendent de verificar (§3.1). **Retirar** una dada
  (`lifecycle = retired`) la treu del snapshot sense esborrar-la.
* **Esborrar** una dada neteja també l'evidència enllaçada, la seva i la de les
  seves filles, i la cobertura territorial.
* **`coverage` és opcional** a l'alta i a la modificació de fets, papers i
  serveis (també al CRUD de serveis). Sense enviar-la, la dada es comporta com
  abans de la `0047`; enviar-la substitueix tota la cobertura (completesa i
  àrees). A la lectura hi és sempre, en ordre canònic, també quan és neutra.

## 7. Pantalla «Coneixement de l'empresa»

`/empresa/coneixement`, dins de la secció Empresa. Quatre vistes sobre les
mateixes dades:

| Vista | Què hi ha |
| --- | --- |
| **Pendents de validar** | Les dades actuals que encara no es poden acreditar (què hi falta i com resoldre-ho) i els suggeriments de l'històric, per categoria. Els que només es poden descartar van al final. |
| **Coneixement actual** | Certificacions, serveis, fets operatius, solucions, capacitats, productes i partners. A cada categoria hi ha el que és actual, el que només diu l'històric i el que no se sap. |
| **Qualificació i mitjans** (`?tab=qualificacio&seccio=…`) | L'entrada de solvència, experiència, assegurances, registres, persones, equips i recursos quantitatius (IPv4). Llista compacta per secció i un calaix d'alta i edició; cada fitxa amb el seu estat, l'evidència i «Verifica» (només propietari i administració). Cap càlcul de compliment. |
| **Històric detectat** | Tots els candidats, per categoria. |

**L'estat que es pinta surt sempre del snapshot**, mai del
`verification_status` d'una fila (`apps/web/src/lib/knowledge.ts`):

| Es mostra | Quan |
| --- | --- |
| Verificat | El snapshot diu `current_verified` (hi ha evidència) |
| Declarat · Falta evidència | `current_declared` sense cap document |
| Declarat · Pendent de verificar | `current_declared` amb evidència enllaçada |
| Abast desconegut | Certificació sense `scope_service_lines` |
| En tràmit · Caducat · Retirat | La fila existeix però el snapshot no la compta |
| Històric detectat | Només en parla una observació candidata |
| Desconegut | No hi ha res. Només per a llistes tancades: normes, fets i línies |

Des de la pantalla es pot confirmar o descartar un candidat, declarar i editar
coneixement, estructurar l'abast d'una certificació, enllaçar evidència (el
mateix panell de documents de tota l'app) i verificar. Verificar sense evidència
mostra el `422 evidence_required` com una explicació i obre el panell.

La pantalla segueix la mateixa política de rols: qui només consulta ho veu tot,
amb les accions desactivades i l'evidència en mode de lectura; «Elimina» només
surt a propietari i administració. Abans d'editar una dada verificada, el diàleg
avisa que tornarà a pendent de verificar.

La pantalla de certificacions que ja existia mostra el mateix estat efectiu i ja
no deixa triar «Verificat» a mà; la de serveis demana la línia de servei.

**Qualificació i mitjans: una sola UI d'edició.** La UI canònica de la
qualificació és `/empresa/coneixement?tab=qualificacio`. Les pantalles 4C antigues
que gestionen exactament les mateixes entitats **redirigeixen** (307, `next.config.ts`,
llista a `apps/web/src/lib/legacy-redirects.ts`) a la secció corresponent:
`/experiencia` i `/experiencia/{id}` → Experiència, `/empresa/assegurances` →
Assegurances, `/empresa/registres` → Registres. Els components antics queden com a
codi legacy i no s'esborra cap dada. **No** redirigeixen `/empresa/solvencia`
(també preferències de contractació) ni `/empresa/equip` (també titulacions de cada
persona): gestionen conceptes que la secció nova no té, i hi continuen editant els
exercicis i les persones amb la mateixa representació. `/experiencia/{id}` porta
`&project={id}` i la pestanya obre el calaix d'aquell projecte (a qui pot editar).

El calaix d'una referència d'execució és la UI canònica de tot el projecte: dades,
línies de servei acreditades, imports executats per any, **CPV del projecte**,
evidència i verificació. Els CPV són metadada de l'experiència, separats de les
línies: no entren a la fotografia ni a cap regla (R2 llegeix només les línies).
Cada alta o baixa de CPV es desa al moment per `POST`/`DELETE
/projects/{id}/cpvs`, amb la política d'edició del coneixement (treure un CPV
modifica el projecte, no l'esborra: no cal ser propietari), desverifica una
referència verificada, deixa un registre d'auditoria (`cpv afegit` / `cpv retirat`)
en la mateixa transacció, i un duplicat (409) o un CPV inexistent (404) no canvien
res.

* La «Data de vigència de la informació» és opcional i no s'omple mai per
  defecte: a les cinc taules del perfil s'envia nul·la. Als equips i als fets
  quantitatius, l'API hi posa la data d'alta si no se n'indica cap, i la pantalla
  ho diu.
* Una persona sense cap perfil no entra a la fotografia per perfil: es mostra
  «Sense perfil de qualificació», amb què cal fer, i sense «Verifica».

## 8. Dades confidencials

* **No hi ha cap camp** de cost, marge, tarifa, percentatge ni condició
  comercial.
* **`import_knowledge` refusa qualsevol document** que en porti (per nom de
  camp).
* **Els tests fan servir dades sintètiques.**
* **El gold amb dades reals és fora de git.** El test
  `test_knowledge_gold_indaleccius.py` busca el fitxer a
  `KNOWLEDGE_GOLD_INDALECCIUS` o a `var/gold/indaleccius-knowledge.json`, i se
  salta si no hi és.

## 9. Límits coneguts

* **N'hi ha prou amb qualsevol document enllaçat.** L'evidència no es
  classifica per tipus de relació ni es comprova que el document sigui vigent.
* **Afegir un paper a un tercer verificat, o una capacitat a una solució
  verificada, no desverifica el pare.** El paper o la capacitat són una dada
  pròpia, que neix declarada.
* **Les pantalles de certificacions i serveis que ja existien** no desactiven
  els botons segons el rol: l'API respon `403` i el formulari ho mostra.
* **Els papers d'un tercer i els productes d'una solució** no tenen evidència
  ni verificació pròpies a la pantalla: s'editen dins del seu pare.
* **L'històric d'una licitació concreta** (`historic_bids`) no es consulta des
  de la pantalla: només se'n veuen les observacions.
* **No hi ha importació des de la interfície**: l'històric hi arriba per
  `import_knowledge`.
* **Especificacions de producte** (PoE, ports…): les capacitats es declaren a
  nivell de solució, no de producte.
* **Qualificació (`0052`)**: les pantalles 4C antigues no mostren l'origen, la
  verificació, els perfils estructurats ni l'abast dels registres; ho fa la
  pestanya «Qualificació i mitjans».
* **Doble camí legacy (`0053`).** `project_services` i les rutes granulars de
  límits es mantenen per a les pantalles antigues, sense sincronització amb el
  camí nou. Una pòlissa d'un tipus es compara per
  tipus, no per objecte assegurat. La «similitud» d'una referència d'execució
  només es llegeix per línies de servei. Les finestres són per anys naturals
  (l'exercici és l'any de la fila), no per dates exactes, i la d'execució
  necessita el termini de presentació del plec.
* **Permisos (anterior a `0052`).** Els CRUD de coneixement que ja existien
  (certificacions, serveis) accepten `verification_status = verified` amb
  evidència a qualsevol rol d'edició, mentre que `/verify` només el permet a
  propietari i administració: és un deute anterior a `0052`. Les cinc taules de
  `0052` NO hi afegeixen cap via: els seus CRUD no accepten `verification_status`
  (ni a l'alta ni al PATCH) i verificar-les només es pot per `/verify`.
* **L'empremta del coneixement porta els identificadors de les files**: és
  estable dins d'una mateixa base, però no es pot comparar entre dues bases
  independents amb les mateixes dades. Deute anterior a `0052`.
* **No hi ha preus ni polítiques comercials**: la taula `company_policies` i
  `product_prices` queden per a quan calgui calcular economia.
