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
  seves filles.

## 7. Pantalla «Coneixement de l'empresa»

`/empresa/coneixement`, dins de la secció Empresa. Tres vistes sobre les
mateixes dades:

| Vista | Què hi ha |
| --- | --- |
| **Pendents de validar** | Les dades actuals que encara no es poden acreditar (què hi falta i com resoldre-ho) i els suggeriments de l'històric, per categoria. Els que només es poden descartar van al final. |
| **Coneixement actual** | Certificacions, serveis, fets operatius, solucions, capacitats, productes i partners. A cada categoria hi ha el que és actual, el que només diu l'històric i el que no se sap. |
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
* **No hi ha preus ni polítiques comercials**: la taula `company_policies` i
  `product_prices` queden per a quan calgui calcular economia.
