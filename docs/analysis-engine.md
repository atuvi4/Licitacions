# Motor d'anàlisi de licitacions noves

> Estat: **disseny v0 aprovat**. Fase 1 implementada ([intake.md](intake.md)).
> Fase 2 implementada, amb la pantalla de validació ([knowledge.md](knowledge.md)).
> La resta, sense implementar. Data: 2026-09-30.
> Primer client: Indaleccius. Cas pilot: Ajuntament de l'Escala, *Telecomunicacions
> 2027* (fitxa del pilot a `var/knowledge/indaleccius/pilot_lescala_2026.md`,
> fora de git perquè creua el plec amb dades internes de l'empresa).

## 1. Què ha de respondre

Donat un ZIP d'una licitació (PCAP, PPT, annexos i Excel), el motor ha de
lliurar a l'equip de licitacions, en una sola lectura:

1. **Encaix**: ens hi podem presentar i ens interessa?
2. **Punts forts**: on som millors que la mitjana, amb la prova al perfil.
3. **Mancances**: què no complim o no podem acreditar, i quant ens costa.
4. **Preguntes pendents**: què no sabem, i qui ho ha de respondre.
5. **Línia de solució**: amb quins serveis, solucions, productes i socis
   ho faríem, bloc per bloc.

**No és un Go/No-Go automàtic.** El motor proposa i una persona decideix. Els
fonaments ja existeixen i no es toquen:

* un requisit sense cita literal verificable no existeix
  ([requirements.md](requirements.md));
* `mandatory` no és un blocador, `unknown` no bloqueja mai i no hi ha mitjanes
  que amaguin un bloqueig ([compliance.md](compliance.md));
* les decisions són de les persones i queden registrades
  ([workspace.md](workspace.md)).

## 2. Principis nous

### 2.1 L'estat actual mana sobre l'històric

El coneixement de l'empresa té dues naturaleses que no es barregen mai:

| | **Estat actual** | **Històric** |
| --- | --- | --- |
| Què és | El que l'empresa **és i pot fer avui** | El que l'empresa **va fer o va dir** en licitacions passades |
| Exemple | «Tenim ENS Mitjà» | «Crevillent 2025: NO ANEM, NO TENIM ENS» |
| Qui l'escriu | Una persona de l'empresa, o una importació que ella valida | La importació de fulls i carpetes antigues |
| Pot donar `satisfies`? | Sí | **Mai** |
| Caducitat | `valid_until` o vigència del document | Envelleix: pes decreixent amb l'antiguitat |

L'històric serveix per **suggerir** (productes que s'han fet servir, baixes
habituals, compradors coneguts) i per **preguntar** («a l'històric no teníeu
ENS; segueix sent així?»). No pot acreditar res davant d'un plec.

Si una dada actual contradiu l'històric, guanya l'actual i la contradicció
queda registrada com a traça. Cas real: l'ENS. L'històric diu «no en tenen»
(2025) i l'estat actual diu «ENS Mitjà» (2026-09-29), de manera que el motor
treballa amb ENS Mitjà.

### 2.2 Ordre de precedència d'una dada

Per a cada clau de coneixement (per exemple `certification:ENS`,
`capability:pbx.hot_desking` o `supplier:ftth:<operador>`), el valor efectiu és el
primer que existeix d'aquesta llista:

```
1. actual + verificat (amb evidència)            → pot satisfer
2. actual + declarat (sense evidència encara)    → pot satisfer, amb avís «acreditació pendent»
3. històric observat (el més recent)             → com a màxim «probable»; genera pregunta
4. res                                           → unknown; genera pregunta
```

Un negatiu actual declarat («no tenim ISO 9001») val com a `does_not_satisfy`
**només si l'àrea del perfil està marcada com a completa**, igual que ja fa
`completeness.py`. Sense aquesta marca, l'absència no demostra res.

### 2.3 IA on hi ha text, regles on hi ha comparació

| Pas | Qui el fa | Per què |
| --- | --- | --- |
| Llegir el plec i estructurar-lo | **LLM**, amb cita obligatòria | És llenguatge natural |
| Mapar un requisit tècnic a una capacitat del catàleg | **LLM**, restringit a codis tancats i amb cita | Traducció de text a vocabulari |
| Decidir si complim | **Regles deterministes** | És una comparació: ha de ser reproduïble i auditable |
| Calcular punts assolibles, llindar de temeritat i marge implícit | **Regles deterministes** | És aritmètica |
| Redactar l'informe final | **LLM**, que només pot citar troballes ja calculades | Síntesi, sense fets nous |

Durant la congelació de pressupost (des del 2026-09-14), tots els passos amb
LLM corren amb `LLM_PROVIDER=fake`. El pilot es valida amb un gold fet a mà
(vegeu §8).

## 3. Arquitectura

```
                         ┌──────────────────────── CONEIXEMENT (tenant) ───────────────────────┐
                         │  ESTAT ACTUAL (mana)                     HISTÒRIC (suggereix)        │
                         │  perfil · serveis · solucions ·          historic_bids ·             │
                         │  capacitats · productes · proveïdors ·   knowledge_observations      │
                         │  socis · certificacions · polítiques                                 │
                         └───────────────┬─────────────────────────────────┬────────────────────┘
                                         │ KnowledgeSnapshot (fingerprint)  │ HistoricContext
                                         ▼                                  ▼
Canal ─► A. Recepció ─► B. Classificació ─► C. Extracció ─► D. Abast i ─► E. Comparació ─► F. Riscos i ─► G. Síntesi ─► Revisió
        (ZIP · Drive ·  documental          (4B v2 +       capacitats     (compliance +    economia       (informe)     humana
                                          metadades)    (LLM tancat)    regles noves)   (regles)                    │
                                                                                                                    ▼
                                                                                             ProposalDraft ─► BidWorkspace (existent)
```

| Etapa | Mòdul (proposat) | Reutilitza | Nou |
| --- | --- | --- | --- |
| A. Recepció ✔ | `app/reception/*`, `app/services/document_reception.py` | `store_uploaded_document`, capa documental | Canals com a adaptadors (avui ZIP) i un sol servei de recepció |
| B. Classificació ✔ | `app/classification/*`, `app/services/document_classification.py` | `TenderDocumentType` | Rols fins (quadre, model d'oferta, inventari…), duplicats i títols enganyosos |
| C. Extracció | `requirement_analysis` (4B v2) | Requisits, criteris i evidència | Perfil de metadades del contracte i parser d'Excel |
| D. Abast | `app/analysis/scope.py` + prompt `scope_v1` | Chunking i validació d'evidència | `TenderScopeItem`, `RequirementCapabilityNeed` |
| E. Comparació | `app/compliance/*` + `app/analysis/capability_rules.py` | Motor de compliment 4C | Regles de capacitat, nivell de certificació i canal |
| F. Riscos i economia | `app/analysis/risk_rules.py`, `app/analysis/economics.py` | `AwardCriterion` (fórmules conservades) | Punts assolibles, temeritat, marge implícit |
| G. Síntesi | `app/analysis/report.py` + prompt `report_v1` | Proveïdor LLM | Informe amb referències obligatòries a troballes |

Tot és **pur** llevat dels orquestradors, com ja passa a `matching` i
`compliance`: cada etapa rep una fotografia i retorna un resultat, i es prova
sense base de dades ni xarxa.

### 3.1 L'entrada no condiciona l'anàlisi

Avui l'única entrada és un ZIP pujat a mà. Després n'hi haurà d'altres:
plataformes de contractació, Google Drive, APIs i connectors. Per això hi ha
quatre capes, i cap no coneix el mecanisme de la de sobre:

| # | Capa | Què sap | Què no sap |
| --- | --- | --- | --- |
| 1 | **Origen de la licitació** (`Tender`, `Source`) | D'on ve l'expedient | Res dels documents |
| 2 | **Documents rebuts** (recepció) | Per quin canal han arribat, què s'ha acceptat i què no | Què és cada document |
| 3 | **Classificació documental** | Què és cada document de la licitació i què no quadra del conjunt | Per quin canal va arribar |
| 4 | **Motor d'anàlisi** (etapes C–G) | `TenderDocument` + `document_type` | Ni la recepció ni la classificació |

Un canal nou és un adaptador que produeix una `Delivery`, i res més. El detall
és a [intake.md](intake.md).

## 4. Model de coneixement de l'empresa

### 4.1 Columnes de procedència comunes

Totes les taules de coneixement **actual** porten aquestes columnes:

| Columna | Valors | Per a què |
| --- | --- | --- |
| `knowledge_origin` | `declared` · `imported_validated` · `derived` | Qui ho afirma |
| `as_of` | data | De quan és la dada |
| `valid_until` | data, nul·lable | Caducitat explícita (tarifes, preus, certificats) |
| `verification_status` | `pending_verification` · `verified` | Ja existeix a certificacions: s'estén a la resta |
| `lifecycle` | `current` · `planned` · `in_progress` · `retired` | Una solució retirada no compta; una certificació `in_progress` genera una pregunta, no un `satisfies` |

L'evidència continua passant per `document_links`, que ja és polimòrfic: n'hi
ha prou d'afegir els `entity_type` nous.

### 4.2 Vocabularis globals (sense `company_id`)

Són catàlegs compartits, igual que `cpv_codes`. Permeten que el plec i
l'empresa parlin el mateix idioma.

| Taula | Contingut | Exemple |
| --- | --- | --- |
| `service_lines` | Taxonomia **tancada** de línies de servei | `voice_pbx`, `voice_trunk`, `mobile`, `access_ftth`, `dedicated_network`, `radio_link`, `network_switching`, `wifi`, `fiber_deployment`, `civil_works`, `cctv`, `alarm_cra`, `it_licensing`, `support_ops` |
| `capability_definitions` | Capacitat comparable: `code`, `service_line`, `value_type` (`bool` · `number` · `ordinal` · `enum`), `unit`, `comparator` (`gte` · `lte` · `eq` · `includes`), `ordinal_scale` | `pbx.concurrent_calls` (number, gte) · `pbx.queue_callback_keep_position` (bool) · `switch.poe_standard` (ordinal af<at<bt) · `switch.mlag` (bool) · `fiber.otdr_level` (ordinal) · `ops.sla_restore_hours` (number, lte) |
| `certification_standards` | Norma i escala de nivells | `ENS` amb nivells `basic<medium<high` · `ISO_9001` · `ISO_27001` |

Les capacitats creixen amb cada licitació nova: un requisit que no encaixa amb
cap codi queda com a `needs_review` i proposa un codi nou, que una persona
aprova. **El model no pot crear codis.**

### 4.3 Estat actual (tenant)

| Entitat demanada | Taula | Estat | Contingut clau |
| --- | --- | --- | --- |
| **CompanyProfile** | `companies` + taules de perfil | Existeix | Identitat, registres, solvència, territori, preferències |
| | `company_operating_facts` | **Nou** | Fets clau-valor tipats que avui no tenen lloc: `telecom_operator_cnmc`, `own_number_range_nrn`, `datacenter_tier3`, `own_ftth_infrastructure`, `support_24x7`, `base_location`. Clau única per empresa, amb la procedència de §4.1. |
| | `company_policies` | **Nou** | Polítiques de preu **vigents** i versionades: multiplicador per `service_line`, marge mínim, cost hora, imprevistos, tasa d'èxit per soci, desplaçament per km. `effective_from`/`effective_to`: mai se sobreescriu. **Confidencial.** |
| **Services** | `company_services` | Existeix; s'estén | S'hi afegeix `service_line` (FK a la taxonomia), `delivery_mode` (`own` · `partner` · `subcontract` · `resale`) i `lifecycle`. «No ho volem fer» = `excluded`. |
| **Solutions** | `company_solutions` | **Nou** | Com es dona un servei: «Centraleta cloud 3CX a Templus». `service_line`, `name`, `vendor`, `edition` (la llicència importa: una funció pot ser només Pro), `hosting`, `lifecycle`. |
| | `solution_capabilities` | **Nou** | `(solution_id, capability_code)` → `status` (`supported` · `supported_with_limits` · `not_supported` · `unknown`), `value`, `limits_note`, evidència (fitxa o manual via `document_links`). |
| **Products** | `company_products` | **Nou** | `manufacturer`, `model`, `category`, `lifecycle`, `specs` (JSON **tipat** per categoria, com `value_schemas.py`), `datasheet` via `document_links`. |
| | `product_prices` | **Nou** | `(product_id, supplier_id)` → `unit_cost`, `currency`, `as_of`, `valid_until`, `lead_time_days`. Els preus caduquen. **Confidencial.** Font natural: Odoo. |
| | `solution_products` | **Nou** | Productes compatibles o homologats per a una solució (terminals per a 3CX…). |
| **Suppliers** i **Partners** | `company_partners` | **Nou** | **Una sola taula** per a tercers: `name`, `tax_id`, `kind` (`distributor` · `wholesale_operator` · `subcontractor` · `commercial_partner` · `advisor` · `software_vendor`), `territories`, `notes`. |
| | `partner_roles` | **Nou** | Un tercer pot tenir diversos rols (InaTIC: soci comercial **i** redactor de memòries **i** obra civil). `role`, `service_line`, `territory`, `fee_model` (`success_pct` · `fixed` · `per_hour`), `fee_value`, `can_lend_solvency`. |
| | `partner_offerings` | **Nou** | Què ofereix un operador majorista: `service_line`, `product_name`, `bandwidth`, `monthly_cost`, `coverage_ref`, `as_of`, `valid_until`. **Confidencial.** |
| **Certifications** | `company_certifications` | Existeix; s'estén | S'hi afegeix `standard_code` (FK), `level`, `scope` estructurat (`service_lines` coberts) i `lifecycle` (`in_progress` amb `expected_at`). Aquí és on viu **ENS Mitjà**. |
| **Experience** | `company_projects` + `project_services` + evidència | Existeix; s'estén | Contractes **executats** que acrediten solvència. S'hi afegeix `service_line`, `volumes` (extensions, línies, seus, metres de fibra) i `annual_amounts` per any (la solvència es demana «any de major execució»). |

Per què una sola taula de tercers: el mateix tercer fa sovint de proveïdor i de
soci. Si fossin dues taules, InaTIC hi apareixeria dues vegades i les tasques de
cada rol no quedarien lligades.

### 4.4 Històric (tenant)

| Taula | Contingut | Regla |
| --- | --- | --- |
| `historic_bids` | Una fila per licitació o lot analitzat en el passat: comprador, data, `service_lines`, PBL, durada, pesos dels criteris, `decision` + `decision_reason`, oferta, baixa, marge, `result` (`won` · `lost` · `unknown` · `not_submitted`), adjudicatari i preu guanyador si es coneixen, `source_ref`. Carrega directa de `historic_licitacions.csv`. | Només es llegeix. Mai alimenta una regla d'elegibilitat. |
| `historic_bid_lines` | Línies de càlcul (producte, cost, multiplicador, proveïdor) quan el full les té | Font de suggeriments i de preus de referència **datats** |
| `knowledge_observations` | Afirmacions extretes de l'històric: «va fer servir Yealink T46U (FCRI 2025)», «no tenia ENS (Crevillent 2025)» | Són **candidates**. Una persona les pot **promoure** a l'estat actual (i passen a `imported_validated`) o **descartar**. Mai no es promouen soles. |

`historic_bids` no és `company_projects`: una oferta no és un contracte
executat. Quan una oferta es guanya i s'executa, el projecte nou hi apunta
(`project.historic_bid_id`).

### 4.5 Fotografia i fingerprint

L'anàlisi no llegeix el perfil viu. Construeix un `KnowledgeSnapshot`, que és
el valor efectiu de cada clau segons §2.2, i en calcula el fingerprint, com ja
fa `compliance/profile.py`. Canviar el perfil marca l'anàlisi com a
desactualitzada; no la modifica.

## 5. Flux de l'anàlisi

### A. Recepció (determinista) ✔

Cada canal tradueix el que rep a una `Delivery` comuna. El canal ZIP obre el
paquet en memòria amb límits (fitxers, mida, ràtio de compressió, camins,
enllaços, xifrat; ZIPs niats, un nivell). Després, igual per a tots els canals:

1. Es desa el contenidor original, si n'hi ha, al magatzem de continguts adreçat
   per SHA-256 (`RESTRICT`).
2. Cada fitxer acceptat esdevé un `TenderDocument` normal, amb l'extracció de
   sempre. Els omesos queden registrats amb el motiu.
3. Es demana la classificació de la licitació sencera.

Els escanejats queden sense text i generen una troballa. No s'inventa el
contingut.

**Pendent:** l'extracció citable de XLSX/DOCX (fulls, cel·les i fórmules). Ara
se'n llegeix el text només per classificar-los.

### B. Classificació de documents (híbrida)

| Rol | Senyals |
| --- | --- |
| `pcap`, `ppt` | Nom del fitxer; capçalera «Plec de clàusules…» o «…prescripcions tècniques» |
| `characteristics_table` | «Quadre de característiques» |
| `offer_model` | «Model d'oferta», caselles `____`, Excel amb fórmules de puntuació |
| `inventory_annex` | Taules d'ubicacions, línies o extensions |
| `justification_report` | «Memòria justificativa», «Informe de necessitat» |
| `other` | Resta |

Primer s'apliquen regles sobre el nom i la primera pàgina. L'LLM només
intervé si les regles no decideixen. Dos controls són obligatoris perquè en el
pilot n'hi ha un cas real:

* **Títol enganyós.** Si el codi intern del document (`Codi DOC-03`) o el hash
  del text coincideix amb un altre fitxer, el fitxer no té el rol que diu el seu
  nom. A l'Escala, `04_Annex_Mestre_Inventaris…pdf` conté el text sencer del
  DOC-03. Resultat: una troballa de gravetat alta i una pregunta a l'òrgan de
  contractació.
* **Document referenciat i absent.** Si el plec cita un document (el DOC-04,
  «inventaris vinculats») que no és al ZIP, es genera una troballa i una
  pregunta.

### C. Extracció (LLM amb evidència)

* **Requisits i criteris d'adjudicació**: la 4B v2 tal com és (modalitat,
  alternatives, equivalències, fórmules i trams).
* **Metadades del contracte**: perfil d'extracció nou que omple l'equivalent de
  la pestanya «Resum» del full de preparació.
  * Camps: òrgan, objecte, procediment, SARA, durada, pròrroga, inici,
    blocs/lots i si són adjudicables per separat, PBL i VEC per bloc, garanties,
    termini, sobres i límit de pàgines, visita o demostració, subcontractació,
    modificacions previstes, temeritat i penalitats.
  * **Control aritmètic determinista** després de l'extracció: suma dels blocs =
    PBL, VEC = PBL + modificacions + pròrrogues, fórmules del model d'oferta
    coherents amb el PCAP. Una discrepància és una troballa, no una correcció.

### D. Abast i capacitats (LLM restringit)

Dues sortides noves, **globals** perquè deriven del plec públic:

* `TenderScopeItem`: què es compra, per bloc o lot.
  * Camps: `service_line`, descripció, quantitat, unitat, `specs` tipades, preu
    de sortida unitari si n'hi ha, i evidència.
  * Exemple: «Bloc 3 · `voice_pbx` · 103 terminals normals · 10,00 €/mes».
* `RequirementCapabilityNeed`: requisit tècnic → `capability_code` + valor
  exigit + `need_type`.
  * `need_type`: `minimum` · `scored_improvement` · `demo`.
  * Exemple: «20 canals simultanis mínims» → `pbx.concurrent_calls ≥ 20`
    (`minimum`); «≥50 canals = 2 punts» → `pbx.concurrent_calls ≥ 50`
    (`scored_improvement`).
  * El model només pot triar codis del catàleg. Si no n'hi ha cap que encaixi,
    retorna `unmapped` amb una proposta de codi nou.

### E. Comparació amb l'empresa (determinista)

1. **Compliment administratiu**: el motor 4C tal com és (solvència,
   certificacions, assegurances, experiència, personal), alimentat amb el
   `KnowledgeSnapshot`.
2. **Capacitat tècnica**: per a cada `RequirementCapabilityNeed`, es busquen les
   solucions `current` de la mateixa `service_line` i es compara el valor.
   * Resultat per necessitat: `covered` · `covered_with_limits` · `gap` ·
     `unknown`, amb la solució o el producte que ho cobreix.
3. **Canal de lliurament**: per a cada `TenderScopeItem`, qui ho faria.
   * `own`: servei propi.
   * `partner:<id>`: soci o subcontractista amb aquell `service_line` i territori.
   * `none`: ningú, i això és una mancança.
4. **Criteris d'adjudicació**: per a cada criteri automàtic, si és
   **assolible** amb el coneixement actual i quin cost té.
   * `achievable` · `achievable_at_cost` · `not_achievable` · `unknown`.

### F. Riscos i economia (determinista)

* **Punts**: màxim teòric, punts assolibles segurs, punts perduts segurs, punts
  en dubte i pes del judici de valor i de la demostració.
* **Temeritat**: es llegeixen els paràmetres del PCAP i es comparen amb la baixa
  històrica mediana de la mateixa `service_line`. Si la baixa habitual de
  l'empresa ja cau en zona de temeritat, es genera un avís.
* **Marge implícit**: a partir de `TenderScopeItem` × `product_prices` /
  `partner_offerings` vigents × política → cost estimat, i es compara amb el PBL
  per bloc.
  * Si hi falten preus vigents, el resultat és «no calculable» i es llisten les
    tarifes que calen.
  * L'històric només es fa servir com a referència, marcat com a tal.
* **Riscos contractuals** (regles sobre les metadades):
  * preu tancat amb obra civil inclosa;
  * penalitats sobre la facturació;
  * contracte sense pròrroga amb inversió que cal amortitzar;
  * termini agressiu que depèn de tercers;
  * demostració en directe;
  * límits de pàgines;
  * SARA, amb el termini que comporta;
  * reversibilitat i portabilitat.
* **Context**: licitacions anteriors del mateix comprador i de la mateixa
  `service_line`, amb decisió, baixa i resultat.

### G. Síntesi (LLM fonamentat)

L'LLM rep **només** les troballes calculades (cadascuna amb id) i escriu
l'informe. Cada frase ha de citar almenys una troballa.

Un validador rebutja qualsevol afirmació sense referència, igual que la 4B
rebutja les cites que no es localitzen. Si el proveïdor és `fake` o la síntesi
falla, l'informe es genera amb **plantilla determinista** a partir de les
mateixes troballes. L'informe mai no depèn de l'LLM per existir.

### Sortida

`TenderAnalysisReport`, la mateixa estructura per a totes les licitacions:

```
fit:            recomanació + vector de dimensions (§6.3), mai una sola xifra
strengths[]:    troballa → per què és un avantatge → evidència al perfil
gaps[]:         troballa → impacte (bloqueig / punts perduts / cost) → remeis possibles
questions[]:    pregunta → destinatari (empresa / òrgan de contractació) → què desbloqueja
solution_line:  per bloc/lot: service_line → solució → productes → socis → criteris a oferir
risks[]:        risc → gravetat → mitigació
economics:      PBL per bloc, marge implícit o «no calculable», llindar de temeritat
context:        comprador i històric similar
```

## 6. Entitats de l'anàlisi

### 6.1 Requisits i abast (globals)

| Entitat demanada | Taula | Estat |
| --- | --- | --- |
| **TenderRequirements** | `requirements`, `requirement_evidence`, `award_criteria` | Existeix (4B v2) |
| | `tender_scope_items` + `tender_scope_item_evidence` | **Nou** |
| | `requirement_capability_needs` | **Nou**. Revisable per una persona, amb `automatic_*` / `reviewed_*` com a 4C. |
| | `tender_contract_facts` | **Nou**: metadades extretes per run (§5.C), amb evidència |
| | `tender_document_receptions` + `_items` | **Fet** (capa 2): l'entrega, el canal i cada fitxer |
| | `tender_document_classification_runs`, `_role_suggestions`, `_classification_findings`, `_role_decisions` | **Fet** (capa 3): rols, troballes i decisions humanes, per document |

### 6.1.1 Estructura del plec (`0048`, fase 3A pas 2)

Abans de comparar res, el plec ha de poder dir **què** es contracta i **on**:

* **Dades estructurals amb columna pròpia** a `tenders`: durada, pròrrogues,
  regulació harmonitzada (SARA, tri-estat) i inici previst. `null` = no se sap.
  Una dada, un lloc: no es dupliquen a cap taula de fets (`tender_contract_facts`
  arriba a 0049 només per a fets heterogenis).
* **Blocs** (`tender_blocks`): unitats econòmiques o tècniques que no són
  necessàriament lots. L'Escala en té quatre i un sol adjudicatari.
* **Seus** (`tender_sites`): les ubicacions del plec, amb `role` i `is_critical`
  separats. El municipi porta codi **i sistema** (`municipality_code_system`,
  lliure, nul·lable, mai inferit): 0049 compara codis per `(code, code_system)`.
  Sense GIS ni deducció de municipis.
* **A què aplica** cada requisit o criteri: N:M amb blocs
  (`requirement_blocks`, `award_criterion_blocks`). Cap enllaç = tot el
  contracte; N enllaços = aquells N blocs. Un criteri de 6 punts sobre dos blocs
  continua sent un criteri de 6 punts.
* **Quan** s'ha de complir: `timing` a requisits i criteris
  (`at_submission` · `at_execution` · `ambiguous` · `unknown`).
* **Cites**: blocs i seus són citables amb el patró de `tender_source_refs`
  (taules d'enllaç pròpies), com els requisits escrits a mà.

L'extracció automàtica **no** escriu res d'això encara; s'escriu amb l'API
d'estructura (`/companies/{c}/tenders/{t}/structure`, `/blocks`, `/sites`,
`/requirements/{r}/structure`, `/award-criteria/{a}/structure`) amb les
mateixes autoritzacions que la resta de dades d'una licitació oberta a mà.
Cap d'aquestes dades crea abast, necessitats, fets de contracte ni resultats:
això és 0049/0050.

### 6.1.2 Abast i semàntica del plec (`0049`, fase 3A pas 3)

Completa la representació del **costat plec**, sense respondre encara res de
l'empresa:

* **Què es compra** (`tender_scope_items`): línia del vocabulari, quantitat
  decimal, unitat, especificacions versionades i preu unitari; de tot el
  contracte, d'un bloc o d'un lot (mai de tots dos), i opcionalment d'una seu.
* **Requisits territorials** (`requirement_territories` + `territory_match_mode`):
  el mateix vocabulari d'àrea que la cobertura d'una empresa (0047), amb un
  matís literal i, si cal, la seu del plec. `any` = n'hi ha prou amb un, `all` =
  calen tots. **Són un conjunt**: identitat `(kind, site_id, code_system, code,
  label, qualifier)` amb els textos normalitzats; el servei desdublica (cap
  índex únic: `site_id` i els NULL ho complicarien sense guanyar res) i un
  duplicat no compta dues vegades a `all`/`any`. **Només s'emmagatzema**: cap
  *matched*, cap GIS, cap deducció que dos noms són el mateix municipi.
* **Proves exigides** (`proof_json`/`proof_schema`): la llista `proof_v1`;
  `null` = no estructurat, `[]` = cap prova identificada. Cap `proof_status`.
* **Necessitats tècniques** (`requirement_capability_needs`): capacitat del
  vocabulari o nom sense mapar (mai se'n crea cap de global), comparador tancat,
  valor versionat, tipus (`minimum` · `scored_improvement` · `demo`) i punts
  que **no se sumen ni puntuen**.
* **Requisit ↔ criteri** (`requirement_award_links`): "el plec demana X i X és
  la cara exigible del criteri C". Res no es copia.
* **Fets contractuals** (`tender_contract_facts` + `contract_fact_blocks`): el
  que no té columna pròpia, versionat; `billing_terms` és només facturació
  general (invariant de servei: cap enllaç amb blocs). **Una font per abast
  exacte** (invariant de servei, no UNIQUE SQL): per expedient, un mateix
  `fact_code` amb exactament el mateix conjunt de blocs només existeix una
  vegada (`contract_fact_scope_conflict`); `penalties → B1` i `penalties → B3`
  conviuen, `penalties → B1+B3` i `penalties → B3+B1` no; `billing_terms`, com
  que no té blocs, és com a màxim un per expedient.
* **Cites** per a elements d'abast, necessitats i fets: el patró de
  `tender_source_refs`.

**No confondre nivells.** Un element d'abast no crea cap servei, producte ni
línia de proposta; una necessitat no crea cap capacitat d'empresa ni de
fabricant; un territori no llegeix cap cobertura; una prova no busca cap
document d'empresa; un fet no crea cap blocador, troballa ni avaluació.

**Via interna d'escriptura.** `app.services.tender_scope` (i
`tender_structure`) persisteixen sense cap política d'accés; els routers de
tenant hi apliquen la política de la licitació oberta a mà. L'extracció, la
revisió i les fixtures que carreguin una licitació pública faran servir
directament el servei; cap empresa tenant hi arriba.

**Decisió tancada: qui escriu estructura.** L'API de tenant edita només la
**licitació oberta a mà i pròpia**, amb els permisos que ja existien. Una
licitació **pública o global** es llegeix però **no s'edita per aquesta via**:
blocs i seus són globals, i una empresa tenant no pot modificar el que veuen
les altres. No s'amplien permisos. L'extracció, la revisió i 0049 tindran una
**via interna separada** per escriure estructura global; el gold de l'Escala es
carrega per fixture o servei intern durant el desenvolupament.

**Invariant obligatòria per a 0050 (`tender_analysis_runs`).** Editar durada,
pròrrogues, SARA, inici previst, blocs, seus, `timing` o enllaços amb blocs
**no crea cap `TenderRevision`** (no són el que publica l'òrgan). Perquè això
no sigui una font de canvis invisibles, cada run d'anàlisi haurà de fixar un
`tender_structure_fingerprint` canònic (com a mínim l'estructura de 0048 i la
de 0049 que afecti l'anàlisi), i el seu `inputs_hash` haurà d'incloure:
`tender_revision_id`, `extraction_run_id`, `classification_run_id`,
`review_revision_hash`, `tender_structure_fingerprint`,
`knowledge_fingerprint` i `rules_version`. Canviar un bloc, una seu, un
`timing` o una metadada estructural ⇒ fingerprint diferent ⇒ run obsolet
(stale). El fingerprint **no** s'implementa a 0048: és feina de 0050.

**Payload canònic del `tender_structure_fingerprint` (per a 0050).** Com a
mínim, i en forma canònica (ordre estable, ids substituïts per claus naturals
on n'hi hagi):

* 0048: `duration_months`, `extension_months`, `regulated_harmonised`,
  `planned_start_date`; blocs (codi, nom, lot, preu base, punts, adjudicable a
  part, facturació del bloc); seus (codi, nom, paper, crítica, adreça, municipi
  + sistema, coordenades); `timing` de cada requisit i criteri; enllaços
  requisit ↔ bloc i criteri ↔ bloc.
* 0049: elements d'abast (línia, bloc/lot/seu, quantitat, unitat,
  especificacions + esquema, preu unitari); territoris de cada requisit +
  `territory_match_mode`; `proof_json` + `proof_schema`; necessitats
  (capacitat o nom sense mapar, comparador, valor + esquema, tipus, punts,
  criteri); `requirement_award_links`; fets contractuals (codi, valor +
  esquema) + els seus blocs.

**Forma canònica, no llista.** Els conjunts (blocs d'un requisit o d'un
criteri, territoris, enllaços requisit ↔ criteri, blocs d'un fet) entren al
fingerprint com a conjunts: **l'ordre no l'altera** i **un duplicat semàntic
tampoc** (un territori repetit, un enllaç repetit). Els textos hi entren amb
els espais normalitzats. Per això els serveis ja desen aquests conjunts sense
repeticions: territoris per identitat `(kind, site, code_system, code, label,
qualifier)`, fets per `(fact_code + conjunt exacte de blocs)` únic per
expedient.

**La provenance queda fora del fingerprint tècnic.** Canviar només una cita
(una altra pàgina, un altre fitxer) amb el mateix valor estructurat no canvia
el significat i no ha de fer obsoleta cap anàlisi. Una run conserva, a part,
els ids de les cites i les revisions de document que va veure, per poder
auditar-la; però no entren al hash semàntic.

### 6.2 Anàlisi (tenant)

| Entitat demanada | Taula | Contingut |
| --- | --- | --- |
| **TenderAnalysis** | `tender_analysis_runs` | Una fila per execució (empresa, licitació). Fixa les versions de tot el que ha llegit: `extraction_run_id`, `compliance_run_id`, `knowledge_fingerprint`, `rules_version`, `prompt_versions`, `provider`/`model`. Estats `pending · running · completed · partial · failed`. **Immutable**, amb `is_current` protegit per un índex únic parcial, com la resta de runs. |
| | `analysis_findings` | La unitat de tot l'informe. `kind` (`strength` · `gap` · `risk` · `question` · `assumption` · `info`), `severity`, `category`, `rule_code`, `explanation_code`, `tender_value`, `company_value`, `knowledge_source` (actual verificat / actual declarat / històric / cap), `points_impact`, `cost_impact`, i enllaços a requisit, criteri, abast, evidència i clau de coneixement. |
| | `analysis_questions` | `finding_id`, `addressee` (`company` · `contracting_body`), `text`, `status` (`open` · `answered` · `withdrawn`), `answer`, `answered_by/at`. Una resposta de l'empresa que canvia el coneixement es desa **al perfil**, no a la pregunta, i això marca l'anàlisi com a desactualitzada. |
| **FitScore** | `tender_fit_assessments` | Una per run. Vector de dimensions (§6.3) + `recommendation` + `explanation`. |
| **ProposalDraft** | `proposal_drafts` | Una per run, editable. Línia de solució per bloc (`service_line` → `solution_id` → `product_ids` → `partner_ids`), criteris automàtics proposats (C1=No, C2a=Sí…) amb el cost, esquelet de càlcul Inversió/Recurrent amb la política aplicada i supòsits. **Confidencial.** |

### 6.3 FitScore: un vector, no una mitjana

| Dimensió | Valor | Font |
| --- | --- | --- |
| `eligibility` | `eligible` · `review_required` · `no_go` | Motor 4C (existeix) |
| `prefit` | 0–100 | Pre-fit de metadades (existeix) |
| `technical_coverage` | Per bloc: nombre de necessitats `covered` / `limits` / `gap` / `unknown` | §5.E.2 |
| `delivery_coverage` | Per bloc: `own` / `partner` / `none` | §5.E.3 |
| `points` | assolibles segurs / en dubte / perduts, sobre 100 | §5.E.4 i §5.F |
| `economics` | marge implícit (banda) o `not_computable` | §5.F |
| `risk` | `low` · `medium` · `high`, amb la llista de riscos | §5.F |
| `relationship` | comprador conegut o nou, ofertes anteriors, resultats | Històric |

Recomanació (regles, i el bloqueig sempre domina):

```
eligibility = no_go                                         → no_go
eligibility = review_required                               → review
qualsevol bloc amb delivery_coverage = none                  → review  (mancança de capacitat, no descart)
points.perduts_segurs ≥ llindar_empresa                      → eligible_low_fit
economics = marge < marge_mínim_política                     → eligible_low_fit
altrament                                                    → go_candidate
```

Per ordenar llistats es pot calcular un índex compost, però **mai** no es
mostra sense el vector al costat. Un 80 amb un bloqueig continua sent un NO-GO.

### 6.4 Relació amb el workspace existent

La `ProposalDraft` és una **proposta del sistema**. No crea res al
`BidWorkspace` pel seu compte, perquè la decisió C-11 ho prohibeix: «algú ho ha
decidit» i «ho ha deduït el sistema» han de ser distingibles.

L'acció *Adoptar* la fa una persona, element per element:

| Element de la proposta | Es converteix en |
| --- | --- |
| Solució per bloc | `WorkspaceTechnicalSolution` |
| Alternatives | `WorkspaceSolutionOption` |
| Esquelet de càlcul | Esborrany d'`EconomicOffer` |
| Pregunta | `WorkspaceTask` |

En tots els casos es desa `source = system_suggested` i l'autor que l'ha
adoptat.

## 7. Lògica de decisió

### 7.1 Regles per família

| Si el plec demana… | El motor fa… | Resultat possible |
| --- | --- | --- |
| **Certificació amb nivell** (ENS Mitjà) | Busca `standard_code = ENS` al snapshot. Compara el nivell amb l'escala ordinal (`high ⊇ medium ⊇ basic`). Comprova la vigència **al termini** i **durant tot el contracte**. Comprova que l'**abast** cobreix les `service_lines` del contracte. Distingeix si es demana com a **solvència** (s'ha de tenir en presentar) o com a **condició d'execució** (s'ha de tenir en executar). | Nivell ≥ i abast OK → `satisfies`. Abast desconegut → `satisfies` + pregunta. Nivell inferior → `does_not_satisfy` si és solvència; si és d'execució, `gap` remeiable (certificar abans de l'inici). |
| **ENS sense nivell** («complir l'ENS») | Tracta el requisit com a condició d'execució. | Amb qualsevol ENS vigent → `satisfies` + pregunta sobre la categoria del sistema. Sense ENS → `gap` remeiable. |
| **Normes diferents** (ISO 27001 quan tenim ENS) | **Cap equivalència semàntica.** Només si el plec admet «o equivalent» (`equivalence_allowed`) hi ha `needs_review`. | `does_not_satisfy` / `needs_review` |
| **Centraleta IP** | Mapa cada funció del plec a codis `pbx.*` i la compara amb cada solució `current` de `voice_pbx` (3CX, FreePBX…), **amb l'edició** que es faria servir. Suma les llicències necessàries al cost. Si hi ha demostració en directe, cada funció `unknown` o `with_limits` que es puntua a la demo es converteix en risc. | Taula funció × solució, i solució recomanada = la que cobreix més necessitats `minimum` i menys `gap` |
| **Terminals de referència «o equivalent»** | Compara les specs del model de referència amb les dels productes homologats per a la solució. | Producte equivalent o `gap` de catàleg |
| **Fibra: accés (FTTH)** | Busca `partner_offerings` d'`access_ftth` amb cobertura a les adreces de l'inventari i la velocitat demanada (mínim i millora). | `covered` per adreça / `unknown` sense cobertura / `gap` |
| **Fibra: infraestructura pròpia** (criteri puntuable) | Llegeix `own_ftth_infrastructure` del snapshot. | `not_achievable` → punts perduts |
| **Fibra: desplegament i obra** | Busca `fiber_deployment` i `civil_works` amb `delivery_mode` `own` o un soci amb aquest rol al territori. Comprova el personal i l'equipament exigits (OTDR/OLTS calibrats). | `own` / `partner` / `none` (mancança de capacitat) |
| **Solvència econòmica** | Regla 4C existent (`economic_revenue_gte_v1`), amb l'agregació v2 (`max` dels 3 últims exercicis). | `satisfies` / `does_not_satisfy` / `unknown` |
| **Solvència tècnica per experiència** | Regla 4C amb `service_line` i import anual de l'any de major execució. Sense certificat de bona execució → `unknown` (carpeta incompleta). | |
| **Registre d'operador** | Fet `telecom_operator_cnmc`. | |
| **Personal adscrit** | `company_people` + titulacions; si no hi és, un soci amb `can_lend_solvency`. | |

### 7.2 Quan no complim: mai no es descarta sol

Cada `gap` es classifica en un d'aquests tipus, i el tipus decideix què se'n fa:

| Tipus | Exemple | Què fa el motor |
| --- | --- | --- |
| **Bloqueig a la presentació, no remeiable a temps** | ISO 9001 exigida com a solvència i no la tenim | Bloqueig `potential`. Passa a `confirmed` quan una persona ho revisa → `no_go`. Mostra els remeis que la LCSP pot permetre (UTE o mitjans externs) **només si el plec no els exclou**, i com a pregunta, no com a solució. |
| **Remeiable abans de l'execució** | Certificació d'execució que està en tràmit | `review`, amb una pregunta sobre la data |
| **Remeiable amb un tercer** | Desplegament de fibra sense equip propi | Proposa els socis amb aquell rol. Si no n'hi ha, la mancança queda oberta. |
| **Només costa punts** | C1 FTTH pròpia (5 punts) | Resta punts assolibles; mai no bloqueja |
| **Desconegut** | Volum de negoci no informat | Pregunta a l'empresa; mai no bloqueja |

### 7.3 Dades d'origen històric

* Poden **proposar** una solució o un producte («a FCRI vau fer servir
  Yealink T46U»).
* Poden **advertir** («a Crevillent no teníeu ENS»), però l'estat actual les
  supera.
* **No poden** posar un requisit a `satisfies`.
* **No poden** posar un criteri a `achievable` si no hi ha una dada actual que
  el sostingui.

## 8. Pilot: Ajuntament de l'Escala

La licitació *Telecomunicacions 2027* és una bona prova perquè toca gairebé tot
el motor en un sol expedient:

* quatre blocs en un sol contracte (no són lots);
* connectivitat, electrònica, centraleta IP i obra de fibra;
* un criteri de «FTTH pròpia»;
* una demostració en directe de 7 punts;
* ENS com a condició d'execució;
* un annex mal pujat (el DOC-04 conté el DOC-03).

L'anàlisi feta a mà, que servirà de **gold** del pilot, és a
`var/knowledge/indaleccius/pilot_lescala_2026.md`.

Criteris d'acceptació del pilot:

1. El motor detecta el DOC-04 absent o duplicat **sense ajuda**.
2. Extreu PBL i punts per bloc i quadra la suma (204.232,75 €; 40 + 32 + 28 = 100).
3. Resol l'ENS amb l'**estat actual** (Mitjà) i no amb l'històric («no en tenen»).
4. Marca C1 («FTTH pròpia», 5 punts) sense bloquejar:
   * sense dada actual → punts **en dubte**, amb la pista històrica «no podem
     ser propietaris de les fibres» i una pregunta a l'empresa;
   * quan l'empresa declari `own_ftth_infrastructure = false` →
     `not_achievable`.
5. Marca el Bloc 4 com a `delivery_coverage = partner | none` segons el que
   declari l'empresa, i no com a descart.
6. Avisa que la baixa mediana històrica de l'empresa és a prop del llindar de
   temeritat del PCAP.
7. Totes les troballes tenen cita al plec o referència al perfil, i l'informe
   no conté cap afirmació sense troballa.

## 9. Ordre d'implementació proposat

| Fase | Contingut | Depèn de |
| --- | --- | --- |
| 1 ✔ | Recepció (canal ZIP) i classificació documental independent del canal (inclosos duplicats i títols enganyosos). **Fet**: [intake.md](intake.md). El parser XLSX citable queda pendent: ara un XLSX es classifica però no s'analitza. | — |
| 2 ✔ | Coneixement actual: vocabularis, fets, serveis, certificacions, solucions, capacitats, productes i tercers, amb procedència. Històric mínim (licitacions i observacions) i promoció manual. KnowledgeSnapshot. **Fet**: [knowledge.md](knowledge.md). Queden fora: preus i polítiques (`company_policies`, `product_prices`), `historic_bid_lines`. | Dades d'Indaleccius (§10) |
| 3 | Metadades del contracte i abast (prompts nous, amb `fake` fins que es desbloquegi el pressupost). | 1 |
| 4 | Regles de capacitat, canal, criteris assolibles, temeritat i riscos. FitScore. | 2, 3 |
| 5 | Informe (plantilla determinista primer; LLM després) i ProposalDraft → *Adoptar* al workspace. | 4 |

## 10. Decisions pendents

| # | Tema | Pregunta | Proposta per defecte |
| --- | --- | --- | --- |
| D1 | **Font del catàleg de productes i preus** | Odoo com a font de veritat (sincronització) o càrrega manual/CSV a Tuvi? | Importació periòdica d'Odoo cap a `company_products`/`product_prices`, amb `as_of`. Tuvi no edita preus. |
| D2 | **Qui valida el coneixement actual** | Qualsevol membre o només `owner`/`admin`? | Declarar: qualsevol; verificar: `admin`. Cal la matriu RBAC pendent a [architecture.md](architecture.md). |
| D3 | **Promoció de l'històric** | Revisar les observacions una per una o per lots? | Per lots per tipus (productes, proveïdors), amb descart massiu. |
| D4 | **Taxonomia de capacitats** | Qui la manté i amb quina granularitat? | La manté Tuvi (global). S'hi afegeixen codis nous quan una licitació els necessita i una persona els aprova. Es comença amb `voice_pbx`, `network_switching`, `access_ftth` i `fiber_deployment` (els del pilot). |
| D5 | **Llindar de punts perduts** per a `eligible_low_fit` | Fix o per empresa? | Per empresa, dins `company_policies`. Valor inicial: 15 punts. |
| D6 | **Confidencialitat** | Les polítiques, preus i marges poden anar a la mateixa base que la resta del tenant? | Sí, amb columnes marcades com a sensibles. Mai als logs ni a l'LLM. La síntesi rep bandes («marge dins/fora de política»), no imports. |
| D7 | **Què veu l'LLM de l'empresa** | Pot veure el perfil per mapar capacitats? | No. L'LLM només llegeix el **plec** (com ara). La comparació amb l'empresa és 100 % determinista. |
| D8 | **Parser d'Excel** | Llegir només valors, o també fórmules? | Tots dos: les fórmules del model d'oferta són informació (§5.A). |
| D9 | **Remeis legals** (UTE, mitjans externs) | Fins on els proposa el motor? | Només com a pregunta, i només si el plec no els exclou. Mai com a garantia de compliment. |
| D10 | **Preguntes a l'òrgan de contractació** | El motor només les redacta, o també en controla el termini? | Les redacta i les data. La presentació a la plataforma és humana. |
| D11 | **Resultats de licitacions** | Com arriben (adjudicacions de PLACSP, entrada manual)? | Entrada manual ara. Més endavant, `award` de la ingesta. Sense resultats, l'històric no pot aprendre què guanya. |
| D12 | **Pressupost d'LLM** | Quan es desbloqueja la crida real per al pilot? | Fins llavors, tot amb `fake` i gold manual. El primer run real, amb un límit explícit i el pilot com a únic expedient. |
