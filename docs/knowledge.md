# Coneixement de l'empresa

> Fase 2 del [motor d'anàlisi](analysis-engine.md). Data: 2026-09-30.

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
| `service_lines` | `voice_pbx`, `voice_trunk`, `access_ftth`, `dedicated_network`, `radio_link`, `network_switching`, `fiber_deployment`, `civil_works`, `support_ops` |
| `capability_definitions` | 19 capacitats per al pilot de l'Escala. Cadascuna té tipus (`boolean` · `number` · `ordinal`), comparador (`is_true` · `gte` · `lte`), unitat i escala |
| `certification_standards` | `ENS` (nivells `basic < medium < high`), `ISO_9001`, `ISO_14001`, `ISO_27001` |

* **La font de veritat és `app/knowledge/vocabulary.py`.** La migració `0045` en
  copia les dades, i un test comprova que coincideixen.
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

Les filles referencien el pare per `(id, company_id)`: res d'una empresa no pot
penjar d'una fila d'una altra. Tots els enums porten CHECK.

Certificacions i serveis es declaren amb els CRUD que ja existien. Ara accepten
norma i nivell (validats contra el vocabulari), abast estructurat i línia de
servei.

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

Totes les entitats de coneixement nou (fets, solucions, capacitats, productes,
tercers, papers) són ara `LinkedEntityType`, i s'hi poden enllaçar documents amb
l'API de documents que ja existia.

### 3.2 Abast d'una certificació

`scope_service_lines` és la llista de línies de servei que cobreix, validada
contra el vocabulari. El text `scope` es conserva com a nota, però **no es llegeix
mai per deduir-ne cobertura**. Sense abast estructurat, la certificació surt amb
`scope_status = scope_unknown` i com a pendent (`scope_unknown`).

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
* **canvia** si canvia el coneixement actual (nivell, verificació, vigència…);
* **no inclou l'històric:** canviar una observació no canvia el que l'empresa
  pot acreditar.

## 6. API

Totes les rutes pengen de `/companies/{company_id}/knowledge`:

| Ruta | Qui |
| --- | --- |
| `GET /snapshot?on=AAAA-MM-DD` | Qualsevol membre. Inclou `pending_validation`: què falta validar. |
| `GET /observations?status=` | Qualsevol membre |
| `POST /observations/{id}/promote` | `owner` · `admin` · `bid_manager` |
| `POST /observations/{id}/dismiss` (amb nota) | Ídem |
| `POST /records/{kind}/{id}/verify` | `owner` · `admin` |

Una fila o una observació d'una altra empresa dona `404`. L'auditoria porta
identificadors i codis, mai el contingut.

## 7. Dades confidencials

* **No hi ha cap camp** de cost, marge, tarifa, percentatge ni condició
  comercial.
* **`import_knowledge` refusa qualsevol document** que en porti (per nom de
  camp).
* **Els tests fan servir dades sintètiques.**
* **El gold amb dades reals és fora de git.** El test
  `test_knowledge_gold_indaleccius.py` busca el fitxer a
  `KNOWLEDGE_GOLD_INDALECCIUS` o a `var/gold/indaleccius-knowledge.json`, i se
  salta si no hi és.

## 8. Límits coneguts

* **Declarar solucions, productes, tercers, fets i capacitats** encara no té
  API: avui entren promovent observacions o amb el servei. Certificacions i
  serveis sí que tenen CRUD.
* **N'hi ha prou amb qualsevol document enllaçat.** L'evidència no es
  classifica per tipus de relació ni es comprova que el document sigui vigent.
* **Especificacions de producte** (PoE, ports…): les capacitats es declaren a
  nivell de solució, no de producte.
* **No hi ha preus ni polítiques comercials**: la taula `company_policies` i
  `product_prices` queden per a quan calgui calcular economia.
