# Extracció de requisits amb evidència

Aquesta capa converteix el text dels plecs en requisits estructurats i
verificables. Respon **una** pregunta:

> Què exigeix el plec?

**No respon** si l'empresa ho compleix. Aquesta comparació necessita el perfil
corporatiu i és una fase posterior. Per això aquí no hi ha cap noció de
compliment, ni de blocador, ni de Go/No-Go, ni cap puntuació de l'empresa.

## Principi

```
TEXT FONT → EXTRACCIÓ ESTRUCTURADA → VALIDACIÓ DETERMINISTA → REQUIREMENT
```

No és «passa'm el PDF i fes-me'n un resum». Un resum lliure no es pot verificar,
i el que no es pot verificar no pot ser font de veritat per decidir si es
presenta una oferta.

## Arquitectura

| Mòdul | Responsabilitat | Toca la BD? | Toca el proveïdor? |
| --- | --- | --- | --- |
| `app/ai/provider.py` | Contracte `StructuredLLMProvider` | no | — |
| `app/ai/providers/anthropic.py` | **L'únic** fitxer que coneix el SDK | no | sí |
| `app/ai/providers/fake.py` | Proveïdor determinista per a tests | no | no |
| `app/ai/schemas.py` | Contracte de sortida (Pydantic) | no | no |
| `app/ai/prompts/requirements_v1.py` | Prompt versionat | no | no |
| `app/services/requirement_chunking.py` | Selecció, chunking, fingerprint | no | no |
| `app/services/evidence_validation.py` | Localització de cites i offsets | no | no |
| `app/services/requirement_analysis.py` | Orquestració | **sí** | sí |

Tot menys l'orquestrador és pur i es prova sense sessió ni xarxa.

## Entitats

Totes **globals**: cap porta `company_id`. Els requisits deriven de documents
públics i s'extreuen una sola vegada per licitació, les miri l'empresa que les
miri.

```
Tender ─ RequirementExtractionRun ─┬─ Requirement ──── RequirementEvidence
                                   └─ AwardCriterion ─ AwardCriterionEvidence
                                                             │
                        TenderDocumentExtraction (immutable) ─┘
                        TenderDocumentPage (immutable)
```

L'evidència apunta amb `RESTRICT` a l'extracció i a la pàgina: una cita no pot
quedar òrfena ni pot desaparèixer el text que la sosté.

## El Run és immutable

Igual que les extraccions de PDF (4A.1): un run completat no es sobreescriu.
Tornar a analitzar crea un run nou, i **només passa a `is_current` si acaba bé**.
Si falla, l'anàlisi anterior segueix sent la vigent i els seus requisits no es
toquen: una anàlisi fallida no pot deixar sense requisits una licitació que ja
en tenia.

Estats: `pending · running · completed · partial · failed`.

## Fingerprint i cache

`input_fingerprint` és el SHA-256 de les extraccions analitzades, ordenades:

```
sorted(extraction_id | revision_number | content_sha256 | parser_version) → SHA-256
```

Amb el mateix fingerprint **i** la mateixa configuració (`extractor_version`,
`prompt_version`, `schema_version`, proveïdor i model) es reutilitza el run i no
es gasta ni un token. `force=true` en crea un de nou.

Si un document es reextreu, la revisió canvia, el fingerprint canvia i l'anàlisi
queda marcada com a obsoleta.

## Què s'analitza

Només extraccions amb `status = extracted` **i** pàgines amb text. `is_current`
no és suficient: una extracció vigent pot ser `skipped_unsupported` (un OLE2, un
ZIP) o no tenir cap caràcter (un PDF escanejat).

Aquests documents queden registrats a `skipped_documents` amb el seu motiu
(`document_not_processed`, `document_not_analyzable`, `document_without_text`) i
es mostren a la interfície. **No se'n inventa el contingut.**

Selecció automàtica: PCAP, PPT i annexos. La interfície permet triar-ne d'altres.

## Chunking

Per pàgines senceres, mai partides, un sol document per chunk, amb solapament
petit perquè un requisit que travessa un salt de pàgina no es perdi. Els límits
són configuració (`REQUIREMENTS_CHUNK_PAGES`, `REQUIREMENTS_CHUNK_OVERLAP_PAGES`,
`REQUIREMENTS_MAX_PAGES_PER_RUN`), no números escampats pel codi.

Format que veu el model:

```
<DOCUMENT document_id="..." extraction_id="..." document_type="administrative_terms">
<PAGE number="27">…</PAGE>
<PAGE number="28">…</PAGE>
</DOCUMENT>
```

## Validació de l'evidència

**La regla crítica.** Un LLM pot retornar una pàgina plausible i un fragment
versemblant que no apareix enlloc. Sense verificar-ho, tot això seria una
al·lucinació ben formatada.

Es comprova, en aquest ordre:

1. `extraction_id` és el del chunk que se li ha ensenyat.
2. La pàgina era al context.
3. El quote apareix **literalment** al text d'aquella pàgina.

Es tolera només soroll tipogràfic: espais, salts de línia, tabuladors,
normalització Unicode, cometes tipogràfiques i guions llargs. **Cap similitud
semàntica.** Una paràfrasi no és una cita.

Si el quote no es localitza, es rebutja l'evidència. Si el requisit es queda
sense cap evidència vàlida, **no es persisteix**: només compta com a candidat
rebutjat a les mètriques del run.

`char_start` i `char_end` els calcula el backend sobre el text immutable de la
pàgina. El model no inventa offsets.

## Categories i obligatorietat

Enums tancats: el model no pot inventar categories ni operadors; Pydantic ho
rebutja. Categories a `RequirementCategory` (15 valors).

`obligation`: `mandatory · scored · informative · unknown`.

**`mandatory` NO és un no-go.** Només vol dir que el plec ho exigeix. Convertir
això en un blocador requereix comparar-ho amb l'empresa. Per això no existeix
cap columna `hard_blocker`, i hi ha un test que ho comprova.

«Se valorará» és `scored`, no `mandatory`. «Podrá» no és obligatori.

## Deduplicació

Determinista i **conservadora**. La clau canònica combina categoria, operador,
abast, etiquetes de lot, valor estructurat, títol normalitzat i pàgines citades.

Davant del dubte, es conserven tots dos: fusionar dues exigències diferents és
pitjor que mostrar un duplicat. Cap embedding, cap segon LLM.

## Lots i conflictes

Un lot només s'assigna si el mapping és **inequívoc**. Si no, el requisit queda
`needs_review` i les etiquetes crues es guarden a `raw_lot_labels`. No s'inventa
cap `lot_id`.

Si el PCAP i una correcció discrepen, es conserven tots dos requisits i es marca
`has_conflict` amb el seu `conflict_group`. No es resol automàticament: decidir
quin document preval és interpretació jurídica.

Els terminis extrets es comparen deterministament amb `Tender.deadline_at`. Si
divergeixen es marca el conflicte i es demana revisió; **no** se substitueix la
metadada.

## Revisió humana

`review_status`: `unreviewed · verified · needs_review · rejected`.

Corregir un requisit **no** sobreescriu l'output del model: la correcció va a
`manual_override_json`, amb `reviewed_by` i `reviewed_at`. Sense això no es
podria auditar mai si l'extractor encertava.

Rebutjar no és esborrar: el requisit segueix existint i consultable.

## Confiança

`high · medium · low`. És el que declara el model, **no** una probabilitat
calibrada. Per això no es mostra mai com a percentatge.

## Proveïdors

Configurable per entorn. Sense clau, l'aplicació arrenca igualment i només queda
no disponible la funció d'anàlisi.

```bash
LLM_PROVIDER=anthropic          # fake | anthropic
ANTHROPIC_API_KEY=...           # només backend, mai al client
LLM_MODEL=claude-sonnet-5       # configuració, no decisió de domini
LLM_TIMEOUT_SECONDS=120
LLM_MAX_RETRIES=2
LLM_REQUIREMENTS_MAX_OUTPUT_TOKENS=8000
```

La sortida estructurada es demana amb una *tool* amb `input_schema`, no
parsejant text lliure amb expressions regulars.

## Prompts versionats i perfils d'extracció

Un **perfil** (`app/ai/profiles.py`) agrupa prompt, esquema de sortida i versió
de l'extractor. Van junts perquè es desen junts a cada run: barrejar-los faria
que dos resultats incomparables semblessin comparables.

| Perfil | `prompt_version` | `schema_version` | `extractor_version` |
| --- | --- | --- | --- |
| **`V1`** (per defecte) | `requirements-v1` | `requirements-schema-v1` | `tender-requirements-v1` |
| `V2` (explícit) | `requirements-v2` | `requirements-schema-v2` | `tender-requirements-v2` |

**Implementada no vol dir promoguda.** El perfil per defecte
(`DEFAULT_PROFILE`) segueix sent la **v1**, i no és un descuit: la v2 està
implementada però la seva qualitat amb un model real encara no s'ha mesurat.
Posar-la per defecte ara faria que qualsevol extracció nova canviés de
comportament sense que ningú ho hagués demanat ni comprovat.

Qui la vulgui, la demana: `analyze_tender_requirements(..., profile=V2)`. És el
que fan els runs de benchmark, que s'hi refereixen per `BENCHMARK_PROFILE` en
comptes d'escriure la versió a mà. Quan **4D.2** mesuri la v2 i passi els
criteris d'acceptació, el defecte passarà a `V2` en un commit a part: la
promoció és una decisió, i ha de ser visible com a tal.

**No s'editen en silenci.** El `prompt_version` forma part de la clau de cache;
canviar-ne el text sense pujar la versió faria que resultats incomparables
semblessin comparables. Com que el perfil sencer entra a la clau, els runs v1
antics segueixen sent reproduibles i es reaprofiten en comptes de re-executar-se
amb regles noves.

## Requisits v2

La revisió humana del gold (`gold-v1-reviewed`) va trobar cinc coses que la v1
**no podia representar**. No es van resoldre amb condicionals per expedient sinó
canviant l'esquema, el prompt, la normalització i la validació.

### El valor deixa de ser lliure

`app/ai/value_schemas.py` dona forma tipada al `value_json`, i
`Requirement.value_schema` diu quin contracte segueix. `None` = la forma lliure
de la v1, que segueix sent llegible.

| Cas | Què faltava | Com es representa ara |
| --- | --- | --- |
| «el de major volum dels tres últims» | `years: 3` no distingia «cadascun» de «el millor» | `reference_period.years` + **`aggregation`** (`each_year` · `any_year` · `max` · `average` · `cumulative` · `unknown`) |
| «≥ 70 % de l'anualitat mitjana» | Un percentatge sense base no vol dir res | `percentage` + **`percentage_base`** (`contract_annuity`, `contract_value`…) |
| «RC 1.000.000 amb sublímit 300.000 per víctima» | S'aplanaven en un sol import | `limits[]` amb `kind` (`total`, `per_claim`, **`per_victim`**, `annual_aggregate`, `sublimit`) |
| «podrà acreditar-se mitjançant classificació…» | Es convertia en un requisit obligatori | `modality: alternative` + `alternative_group` |
| «ISO 9001 **o equivalent**» | Es llegia com «exactament ISO 9001» | `equivalence_allowed = true` |

**`unknown` no és un valor per defecte, és informació.** Si el plec no diu
l'agregació, s'hi queda: endevinar-la seria inventar el requisit.

### Modalitat i obligatorietat

`modality` (mandatory · optional · **alternative** · **conditional** ·
informational · unknown) és un camp nou **al costat** d'`obligation`, no un
substitut: 4C llegeix l'obligatorietat i canviar-ne el significat invalidaria
tot el compliment ja calculat.

`obligation` en guarda la conseqüència conservadora, i la derivació **només
baixa mai puja** (`app/ai/normalize_v2.py`):

* `alternative` o `conditional` + `mandatory` → **`unknown`**
* `optional` + `mandatory` → `scored`
* `informational` + `mandatory` → `informative`

«Podrà» tot sol no demostra obligatorietat. Una via d'acreditació marcada com a
exigència fabricaria un blocador fals, que és l'error més car del sistema.

### Criteris d'adjudicació

`formula` (l'expressió), `formula_text` (la frase literal que la conté),
`formula_variables` (què vol dir cada símbol, copiat) i `tranches`. **No
s'executa cap fórmula**: només es conserva.

### Evidència multi-pàgina

Un requisit pot estar descrit en diverses pàgines —l'exigència a una, l'import a
una altra, la forma d'acreditar-lo a una tercera— i les tres cites el sostenen.
La clau de dedup v2 **no inclou les pàgines**, de manera que les cites
s'acumulen al mateix requisit en comptes de duplicar-lo i duplicar-ne el
blocador. La v1 les hi incloïa i es conserva tal com era.

La regla d'evidència **no es relaxa**: sense cita literal verificable no hi ha
requisit, tampoc a la v2.

### Deduplicació

Segueix sent conservadora i ara distingeix també modalitat, grup d'alternatives i
equivalència. Una cobertura total i un sublímit per víctima tenen valors
diferents i **no es fusionen mai**.

## Compatibilitat amb 4C

`app/compliance/v2_adapter.py` és la frontera, escrita en un sol lloc i no
repartida en condicionals dins de cada regla. Fa dues coses:

* **Tradueix el que sí que és equivalent.** Un llindar v2 amb agregació
  `each_year` diu exactament el mateix que el valor lliure v1, i es resol igual.
  Sense això, activar la v2 hauria deixat el compliment sense veredictes.
* **Bloqueja el que cap regla sap resoldre** i retorna `needs_review` amb un codi:
  `V2_ALTERNATIVE_PATH` · `V2_CONDITIONAL` · `V2_EQUIVALENCE_ALLOWED` ·
  `V2_AGGREGATION_UNSUPPORTED` · `V2_PERCENTAGE_THRESHOLD` ·
  `V2_INSURANCE_SUBLIMITS` · `V2_VALUE_NOT_INTERPRETABLE`.

`needs_review` no genera blocador, així que **una alternativa no verificada no
pot descartar una licitació**. Hi ha un test que ho fixa per als cinc patrons.


## Prompt injection

El text del plec és **dada no confiable**. Ve d'un tercer i pot contenir una
frase que sembli una ordre («ignora les instruccions anteriors»).

Va delimitat entre `<UNTRUSTED_DOCUMENT_CONTENT>` i el sistema diu explícitament
que no s'obeeixi res del que hi hagi a dins. A més, el contracte de sortida ni
tan sols té un camp on dir «l'empresa compleix»: encara que el model volgués
obeir, no tindria on escriure-ho. Hi ha una pàgina fixture amb una injecció i un
test que ho comprova.

## Cap dada de l'empresa

4B és **completament independent del tenant**. Al proveïdor només hi va text
públic del plec. Hi ha un test que agafa una empresa real, executa l'anàlisi i
comprova que ni el seu identificador, ni el nom, ni el correu de l'usuari
apareixen enlloc del payload.

## Cost

Es registren tokens d'entrada i sortida, model, durada i identificadors de
petició quan el proveïdor els dona. No s'implementa facturació. **Mai es
loguegen claus d'API ni el text dels plecs**, només recomptes.

## Límits coneguts

* **Processament síncron.** L'endpoint bloqueja fins que acaba. La lògica ja viu
  separada d'HTTP, així que moure-la a un worker no requerirà tocar-la. No s'han
  fet servir `BackgroundTasks` com a falsa cua durable.
* **Sense OCR.** Un plec escanejat no té text i queda fora de l'anàlisi.
* **4C encara no resol les estructures v2 riques.** Agregacions (`max`,
  `average`, `cumulative`), llindars en percentatge, sublímits d'assegurança,
  alternatives i equivalències arriben com a `needs_review`. És deliberat i
  conservador, però vol dir que un plec amb aquests patrons demanarà més revisió
  humana fins que hi hagi regles noves.
* **Cap fórmula s'executa.** Es conserva; puntuar una oferta és una altra fase.
* **La qualitat del model amb la v2 no està mesurada.** Aquesta iteració fixa que
  la canonada pugui representar-ho; el benchmark real és la següent. Per això la
  v2 **no és el perfil per defecte**: s'ha de demanar explícitament.
* **Deduplicació entre runs.** La clau única és per run: dos runs diferents poden
  contenir requisits equivalents. És intencionat, perquè cada run és una
  fotografia immutable.
* **Qualitat del model no mesurada.** El dataset etiquetat comprova la canonada,
  no l'encert de l'extractor. Això requereix una execució amb credencial real.
