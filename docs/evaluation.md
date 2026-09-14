# Benchmark d'extracció de requisits

Aquesta capa respon una pregunta que fins ara no teníem manera de contestar:

> Amb un model real, fins a quin punt extraiem correctament els requisits
> crítics dels plecs?

No hi ha cap decisió de producte que depengui d'una intuïció sobre això. O es
mesura, o no se sap.

## Gold dataset

Un fitxer JSON versionat a Git: `apps/api/datasets/requirements/gold-v1.json`.

No és una taula, i el motiu no és simplicitat: una persona ha de poder obrir-lo,
llegir-lo, discutir-lo en una revisió de codi i veure'n l'historial.

Dues regles el governen:

* **L'output del model no és gold.** Anotar amb el que diu l'extractor mesuraria
  si el model és coherent amb ell mateix, no si encerta.
* **El gold no es canvia perquè el model falli.** Només es corregeix quan la
  revisió humana determina que l'etiqueta era errònia, i la correcció queda a
  `revisions` amb motiu, revisor i data.

### Format

```jsonc
{
  "dataset_id": "gold-v1",
  "version": 1,
  "revisions": [],                 // correccions del gold, mai silencioses
  "cases": [{
    "case_id": "4249-2026",
    "expediente_number": "4249/2026",
    "tender_external_id": "...",   // resol el Tender real
    "traits": ["plec llarg", "sense lots"],
    "requirements": [{
      "gold_id": "eco-1",
      "category": "economic_solvency",
      "obligation": "mandatory",
      "operator": "gte",
      "value": {"amount": 60750, "currency": "EUR", "period": "annual", "years": 3},
      "document_name": "Plec de clausules administratives",
      "page_number": 10,
      "quote": "…fragment literal de la pàgina…"
    }],
    "award_criteria": [...]
  }]
}
```

Les cites són **fragments literals** de les pàgines reals, extrets per
`scripts/build_gold_dataset.py` i validats amb el mateix localitzador que fa
servir la producció. Les **etiquetes** (categoria, obligatorietat, operador,
valor) són decisió humana.

## Draft vs verified: què és gold truth

Una anotació acabada d'escriure és un **esborrany**, per ben feta que estigui.
Només després que una persona l'hagi comparada amb la pàgina original passa a
`verified` i entra a les mètriques oficials.

Cada anotació porta `annotation_status` (`draft` · `verified` · `rejected` ·
`needs_review`), `annotated_by`, `reviewed_by`, `reviewed_at` i `review_note`.

**Només `verified` compta al denominador de precision i recall.** Un esborrany
pot ser correcte, però ningú no l'ha comprovat, i posar-lo a la mètrica
convertiria una intuïció en una xifra oficial. Els esborranys es veuen al report
—a la taula de cobertura— però no a les mètriques.

Si no hi ha cap anotació verificada, el benchmark **no calcula mètriques**: surt
amb codi 3 i diu què cal fer. `--include-draft` permet explorar, i llavors el
report queda marcat com a **no oficial**.

### Revisió humana

```bash
# 1. Genera la pàgina de revisió
python -m app.evaluation.review --dataset gold-v1

# 2. Obre-la, revisa cada anotació contra el text complet de la pàgina,
#    prem [Verifica] / [Rebutja] / [Necessita revisió] i descarrega decisions.json

# 3. Aplica les decisions i publica la versió revisada
python -m app.evaluation.review --apply decisions.json     --reviewer "Nom Cognom" --promote gold-v1-reviewed
```

La pàgina és un fitxer HTML local: s'obre amb doble clic, no necessita servidor
ni xarxa, i mostra cada anotació **al costat del text complet de la pàgina
original amb la cita ressaltada** —que és l'única manera honesta de verificar-la.
Les decisions s'acumulen al navegador i es descarreguen com un JSON.

Per què en dos passos i no una aplicació amb servidor: perquè el resultat de la
revisió ha de ser un fitxer versionable a Git, revisable en un pull request. Una
base de dades amagaria qui va verificar què i quan.

`--reviewer` és obligatori: una revisió sense signatura no és una revisió.

### Versionat

La revisió **no sobreescriu** `gold-v1`: en crea una versió nova
(`gold-v1-reviewed`, versió 2). Un benchmark ha de poder dir exactament amb
quina versió del dataset es va mesurar, i el resum en guarda `dataset_id` i
`dataset_version`.

## Com s'aparellen predicció i gold

Comparar dos conjunts de requisits té una trampa: decidir si dos enunciats són
«el mateix requisit» és el tipus de judici que aquesta iteració vol mesurar, no
delegar. Amb un LLM al matcher, el benchmark mesuraria el matcher.

Per això l'aparellament és explícit:

1. Candidat només si comparteixen **categoria** i **pàgina** (±1).
2. Score = 0,45 · coincidència de valors numèrics + 0,35 · solapament textual + 0,20.
3. Assignació voraç un a un, llindar 0,45.

El que no casa compta com a fals positiu o fals negatiu. Un recompte pessimista
i explicable val més que un d'optimista i opac.

## Mètriques

### Detecció

Precision, recall i F1 per: **global**, **obligatoris**, **crítics** i
**criteris d'adjudicació**, més el desglossament per categoria.

**El recall crític no s'amaga dins del F1 global.** Un plec amb deu blocadors
del qual el model en perd dos té un recall crític del 80 %, i això s'ha de veure
sol.

### Estructura

Detectar que un requisit existeix **no** és encertar-lo: un llindar de 450.000
extret com a 45.000 és una detecció correcta i un compliment equivocat. Es mesura
per separat categoria, obligatorietat, operador, import, moneda, anys de mirada
enrere, mínim de projectes i abast, més `fully_correct` (tot alhora).

### Evidència

Cita localitzable, pàgina correcta i document correcte. **Un requisit sense
evidència vàlida no compta com a true positive**, encara que el text s'assembli.

### `automatable_rate`

Quants requisits crítics correctes pot avaluar sol el motor de 4C.

No s'estima: **s'executa el motor de veritat** sobre el requisit predit amb un
perfil buit però complet, i es mira si el motiu que retorna és un de la llista
«no automatitzable» (`REQUIREMENT_VALUE_MISSING`, `RULE_NOT_AUTOMATABLE`,
`EXPERIENCE_SIMILARITY_REVIEW`…). Es mira el motiu i no el mode perquè amb el
perfil buit moltes regles diuen «falten dades de l'empresa», i això no diu res
sobre si el requisit està prou estructurat.

### Errors

Taxonomia tancada, no un «incorrecte» genèric: `missed_requirement`,
`hallucinated_requirement`, `wrong_obligation`, `wrong_category`,
`wrong_numeric_value`, `wrong_operator`, `wrong_scope`, `wrong_evidence`,
`incomplete_structure`, `duplicate`, `document_not_processed`, `parser_quality`.

### Llistes prioritàries

* **CRITICAL MISSES** — cada requisit obligatori no detectat, amb document,
  pàgina, cita del gold i causa probable (`document_not_processed`,
  `page_not_analysed`, `model_miss`).
* **FALSE BLOCKERS** — el model diu `mandatory` i el gold no. Són els més
  perillosos: poden provocar un NO-GO fals.

## Execució

```bash
# 1. Que s'enviaria: no construeix ni crida cap proveidor, i no escriu res
python -m app.evaluation.benchmark --dataset gold-v2.1 --profile v2 --plan-only

# 2. L'execucio de pagament, confirmada expressament
python -m app.evaluation.benchmark --dataset gold-v2.1 --profile v2 --paid-run
```

Opcions: `--limit`, `--tender <case_id>`, `--force`, `--output`, `--list`,
`--include-draft`, `--database-url`. El CLI d'un sol perfil
(`python -m app.evaluation.requirements`) accepta les mateixes i
`--prompt-version`.

### Protecció de pressupost

Un run es paga per chunk, i per això cap dels dos CLI no crida el proveidor real
per accident:

* **`--plan-only`** llegeix la base del benchmark (transacció `READ ONLY` a
  PostgreSQL) i mostra, per cas, els documents, les pàgines, els caràcters i els
  chunks que s'enviarien; quin servidor respon; el gold que cita pàgines que el
  pla no enviaria mai (no mesurable d'entrada), i els casos que reaprofitarien
  un run existent (0 chunks). No construeix cap proveidor.
* L'**estimació de volum** surt de tokens registrats en runs anteriors del mateix
  prompt i model, a la mateixa base (tokens per petició × chunks a enviar). Es
  marca com a ESTIMACIÓ: el real depèn del text de cada chunk. Sense històric no
  s'estima res. El **cost en diners** només surt si `LLM_MODEL_PRICES` té preu
  per a aquell model: el codi no porta cap preu.
* **Sense `--paid-run`**, una execució amb proveidor real s'atura abans de
  connectar a la base i surt amb codi 5. `--plan-only` i `--paid-run` són
  excloents.

Sense credencial l'ordre **no inventa cap resultat**: diu què falta i surt amb
codi 2, que un CI pot distingir d'un fracàs de qualitat.

### Tokens de respostes invàlides

Si el proveidor respon però la resposta no serveix (per exemple, sense la crida a
la tool de sortida), aquells tokens **es paguen igual** i compten al cost del run.
Cada chunk desa a `chunk_coverage` els seus tokens i el `stop_reason`, que
distingeix una resposta mal formada d'una tallada pel límit de sortida
(`max_tokens`). Un error sense resposta (xarxa, HTTP) deixa els tokens a `None`,
no a zero.

### Per què falla un chunk

Una resposta que no es pot fer servir queda classificada a `chunk_coverage` amb
un codi concret, i no amb un `invalid_structured_output` genèric que no permet
saber què ha passat:

| Codi | Quan | Què es desa |
| --- | --- | --- |
| `output_truncated` | `stop_reason == "max_tokens"`: el model ha topat amb el límit de sortida | tokens i `stop_reason` |
| `no_tool_call` | el proveidor respon sense cridar la tool `record_extraction` | tokens i `stop_reason` |
| `schema_invalid` | hi ha crida a la tool, però el contracte del perfil (Pydantic) la rebutja | tokens, `stop_reason` i `error_detail` |

* **`stop_reason=max_tokens` invalida sempre el chunk**, encara que el tros de
  JSON que ha arribat validi. Una extracció tallada pot semblar completa i haver
  perdut la meitat dels requisits: no es persisteix res i el chunk compta com a
  fallat (i els seus tokens, com a pagats).
* **`error_detail`** porta com a molt 5 errors, i de cadascun només
  `{path, type}`: cap valor rebut ni cap missatge d'error. Del path només es
  conserven els noms de camp que declara l'esquema i els índexs de llista;
  qualsevol altre segment (una clau que el model s'hagi inventat, que pot contenir
  text del plec) es desa com a `<key>`.
* `invalid_structured_output` es manté per a sortides que no es poden llegir de
  cap manera (per exemple, JSON trencat).

Els runs anteriors a aquesta classificació només diuen `invalid_structured_output`,
i per a ells la causa concreta de cada chunk és desconeguda.

### Sense reintents automàtics en un run de pagament

El SDK d'Anthropic reintenta sol en timeouts, 429 i 5xx, i un intent que el
proveidor arriba a processar es pot pagar encara que la resposta no arribi. Els
dos CLI de benchmark (`--paid-run`) construeixen el proveidor amb
**`max_retries=0`** (`PAID_RUN_MAX_RETRIES`): un chunk fallit queda fallit i es
veu a `chunk_coverage`, i repetir-lo és una decisió, no un efecte secundari.

La política de producció no canvia: l'anàlisi de plecs del producte continua fent
servir `LLM_MAX_RETRIES`.

### `strict` tool use: ajornat

La tool de sortida es crida amb `tool_choice` forçat, però **sense `strict`**:
Anthropic no garanteix que l'entrada compleixi l'esquema, i per això existeix
`schema_invalid`. Activar-lo queda ajornat expressament, per dos motius
comprovats offline sobre els esquemes reals (SDK `anthropic` 1.5.0):

* **L'esquema conté restriccions que el mode strict no admet.** V1: 3 `minLength`,
  2 `maxLength`, 1 `minimum`. V2: 5 `minLength`, 9 `maxLength`, 1 `minimum`
  (a `title`, `name`, `quote`, `formula`, `condition_note`, `alternative_group`,
  `page_number`…). A més, 18 (V1) i 28 (V2) `default` que el SDK no conserva.
* **`value: dict[str, Any]` no es pot transformar de manera segura.** La via
  oficial del SDK (`anthropic.transform_schema`) deixa l'esquema sintàcticament
  compatible però converteix `value` en un objecte sense propietats i amb
  `additionalProperties: false`: en mode strict el model només podria escriure
  `{}`. Es perdrien els imports de V1 i tot el valor tipat de V2. A més, les
  longituds passen a la descripció com a text: Pydantic les continuaria validant
  i un excés continuaria sent `schema_invalid`.

Per reprendre-ho caldria, com a mínim, donar a `value` propietats explícites (per
exemple, unions tipades a partir de `app/ai/value_schemas.py`) i decidir què es
fa amb les restriccions de longitud, i verificar-ho abans de cap run de pagament.

### Cache

El benchmark reutilitza la canonada real de 4B, així que hereta la seva cache:
amb el mateix `input_fingerprint`, prompt i model no es gasta ni un token.
`--force` la salta.

## Informes

* **JSON** — machine-readable, per comparar execucions.
* **Markdown** — el resum que es llegeix.
* **HTML** — inspector gold vs predicted amb document, pàgina i cita de costat.
  És l'única cosa que cap mètrica no substitueix: obrir un cas i entendre per
  què ha fallat.

## Comparar versions i models

`prompt_version`, `extractor_version`, `schema_version`, `provider` i `model`
formen part del resum i del nom del fitxer. Per comparar dues versions de prompt
s'executen totes dues sobre el mateix gold i es comparen els JSON.

**`requirements-v1` no s'edita.** Si cal millorar-lo es crea
`requirements-v2` i es comparen. Cap canvi s'accepta «perquè sembla millor»:
només si les mètriques milloren o hi ha un trade-off justificat.

## Avisos de producció

El report marca explícitament, i són **blockers** per passar a producció:

| Senyal | Llindar |
| --- | --- |
| Recall crític baix | < 90 % |
| Falsos blocadors | > 0 |
| Cites no localitzables | < 95 % |
| `automatable_rate` baixa | < 70 % |

No són una porta de qualitat automàtica: no fan fallar el procés. Primer es
mesura; posar un número abans de tenir dades seria arbitrari.

El report també avisa del que **queda fora** de les mètriques, perquè caure del
denominador en silenci és la manera més fàcil que una xifra sembli millor del
que és:

* anotacions en `draft` o en `needs_review` pendents, amb el nombre exacte;
* menys de 10 casos amb anotacions verificades.

I quan no hi ha cap criteri d'adjudicació verificat al gold, la fila de criteris
no mostra `0,0 %` sinó `n/d`, amb el missatge
`Award criteria metrics unavailable: no verified gold annotations`. Un zero es
llegeix com una mesura; aquí no s'ha mesurat res. El JSON ho porta explícit a
`award_criteria_metrics_available`.

## Cobertura parcial: «no mesurable» no és «error del model»

Un run pot acabar `partial`: alguns chunks han fallat i les seves pàgines no han
arribat mai al model. Una anotació del gold que només viu en aquestes pàgines
**no és un fals negatiu**: és **no mesurable per cobertura**, i surt del
denominador d'aquell run. Comptar-la com a `model_miss` culparia el model d'una
pàgina que no va veure; ignorar-la en silenci faria que el run semblés complet.

Per això el report separa sempre:

* **errors de cobertura** — anotacions amb `reason: page_not_analysed`, amb el
  chunk que les contenia i el seu estat;
* **errors de model** — `missed_requirement`, `wrong_operator`,
  `hallucinated_requirement`… només sobre pàgines que sí es van analitzar.

I una mesura parcial **no es compara amb una de completa**. Per comparar dos
perfils quan un és parcial, l'altre es retalla als mateixos chunks completats.

Els runs amb `chunk_coverage` porten l'estat de cada chunk. Els anteriors a
aquesta instrumentació es poden reconstruir, en mode només lectura, amb
`scripts/reconstruct_run_coverage.py`: la inferència és exacta quan el nombre de
chunks amb rastre coincideix amb `chunks_completed`. El resultat **no** es desa a
`chunk_coverage`, perquè és una deducció i no una observació.

### Precedent: smoke 422-2026 (2026-09-14), lectura acceptada

| | V1 `9e608835` | V2 `bc2abade` |
| --- | --- | --- |
| Estat | `completed`, 8/8 chunks | **`partial`**, 5/8 chunks (`invalid_structured_output`) |
| Chunks fallits | — | #1 (PCAP p.6-11), #4 (p.21-26), #6 (p.31-36) |
| Pàgines analitzades | 41/41 | 28/41 |

`eco-1` (PCAP p.9) només és al chunk #1, que va fallar a V2. La reconstrucció és
exacta (5 chunks amb rastre = 5 completats) i V1 el va detectar al mateix chunk.
**Classificació acceptada per AlbertTuvi1: `eco-1` és no mesurable per cobertura
a V2, no un error de V2.** L'informe original el marcava com a `model_miss`; no
s'ha reescrit.

Mètriques corregides (precisió / recall / F1, %):

| Conjunt | V2 corregit (ins-1, award-1) | V1 als mateixos 5 chunks | V1 complet |
| --- | --- | --- | --- |
| Global | 2,3 / 100 / 4,4 | 1,9 / 100 / 3,8 | 2,3 / 100 / 4,5 |
| Obligatoris | 2,7 / 100 / 5,3 | 2,0 / 100 / 3,9 | 2,4 / 100 / 4,7 |
| Crítics | 100 / 100 / 100 | 100 / 100 / 100 | 100 / 100 / 100 |
| Criteris d'adjudicació | 100 / 100 / 100 | 100 / 100 / 100 | 100 / 100 / 100 |
| Estructura tota correcta | 0 | 100 | 100 |

Errors de V2 separats: **cobertura 1** (`eco-1`); **model**: 0 no detectats,
1 `wrong_operator` (`ins-1`), 43 prediccions sense parella al gold. Amb un gold de
2 requisits, aquestes 43 no són necessàriament al·lucinacions.

Dues coses que aquest smoke deixa obertes:

* **`invalid_structured_output` no és exclusiu de V2.** Un segon run V1 sobre el
  mateix expedient (`2579990f`) també va quedar `partial`, amb 6/8 chunks. En
  total, 5 dels 24 chunks enviats en aquests tres runs van fallar. Fins que no
  s'entengui, cada mostra ha de portar `chunk_coverage` i s'ha de llegir amb
  aquesta separació.
* **El recall de V2 a solvència econòmica continua sense mesurar.**

## Mostra mínima següent (proposta, sense executar)

Calculat el 2026-09-14 amb el pla de chunks real (`collect_analyzable_documents`,
`_cap_pages`, `build_chunks`; 6 pàgines per chunk i 1 de solapament) i la QA
d'extracció de `licitacions_benchmark`, en mode només lectura. Els documents,
pàgines i chunks de cada cas es tornen a obtenir amb `--plan-only --tender <cas>`.

Criteris de «cobertura suficient» per a un cas:

1. té almenys una anotació que compta per a les mètriques (`verified` i no
   substituïda);
2. la pàgina del gold és dins del pla de chunks i té text;
3. la QA de les fonts és coneguda (no `unknown`) i no té cap avís a la pàgina del
   gold, perquè altrament un error no es pot atribuir al model;
4. és prou petit per no fer la mostra cara: menys chunks vol dir menys cost i
   menys exposició a chunks fallits.

| Cas | Gold que compta | Pàgina → chunk | Chunks | QA fonts | Documents saltats | Veredicte |
| --- | --- | --- | ---: | --- | --- | --- |
| 1-2026 | exp-1 | PCAP p.5 → #0 | 4 | good | — | **apte** |
| TSA0082096-040 | ins-2 | PPT p.4 → #13 | 15 | good | — | **apte** |
| SER-2026-93411 | eco-1 | PCAP p.7 → #1 | 18 | warning (cap a la pàgina) | — | **apte** |
| 2025-04671 | cert-1 | PCAP p.16 → #2, #3 | 9 | warning (cap a la pàgina) | PPT i annex sense text | apte, soroll a precisió |
| 422-2026 | ins-1, eco-1, award-1 | — | 8 | warning (cap a la pàgina) | — | ja mesurat; eco-1 pendent a V2 |
| 4249-2026 | eco-1, exp-1, ins-1 | PCAP p.10 → #1; p.17 → #3 | 16 | **unknown** | — | no: QA pendent i possible document duplicat (vegeu incidència) |
| 300-2025-03877 | exp-1 | PCAP p.68 → #13 | 23 | warning (cap a la pàgina) | — | apte, però car |
| 26-095-3 | ins-1 | PCAP p.67 → #13 | 25 | warning (cap a la pàgina) | — | apte, però car |
| LIC-10495 | ins-1 | PCAP p.15 → #2 | 26 | warning (6 DENSITY_DROP) | 1 annex | apte, però car |
| P4107500C-2026-65 | award-1 | PCAP p.13 → #2 | 80 (400/570 pàg.) | warning | — | no: massa car |
| 2026SE00013 | — (només `needs_review`) | — | 12 | warning | 6 annexos | no: res mesurable |

Proposta de tres casos:

1. **Solvència econòmica → SER-2026-93411.** És l'únic `eco-1` verificat, fora de
   422-2026, amb QA coneguda. És preferible a 4249-2026, que cobriria solvència,
   experiència i assegurança alhora amb 16 chunks, però té la QA `unknown`: tot
   error hi sortiria `UNDETERMINED`. A més, té una incidència de corpus oberta
   (vegeu més avall).
2. **Assegurances → TSA0082096-040.** `ins-2` és l'única anotació verificada amb
   `insurance_limits` (i `obligation_modality`): justament on V2 va fallar
   l'estructura a 422-2026. QA `good` i cap document saltat.
3. **Experiència/criteris → 1-2026.** És el cas més barat del gold (4 chunks, QA
   `good`) i mesura la detecció d'`exp-1`. No mesura cap dimensió nova de V2.
   Si es vol provar `aggregation` i `percentage_base`, el cas és
   300-2025-03877, amb 23 chunks. Criteris d'adjudicació verificats: només n'hi
   ha a 422-2026 (ja mesurat) i a P4107500C-2026-65 (80 chunks), així que no n'hi
   ha cap de barat per ampliar.

### Incidència de corpus oberta: 4249-2026, possible document duplicat

Detectada el 2026-09-14 amb `--plan-only` sobre `licitacions_benchmark`. El
«Plec de prescripcions tecniques» i el «Plec de clausules administratives»
d'aquest expedient tenen exactament les mateixes pàgines (39) i els mateixos
caràcters (143.767). Molt probablement és el mateix fitxer ingerit dues vegades
amb tipus diferents. Si és així:

* la meitat dels 16 chunks del cas es pagarien dues vegades;
* el model veuria el text del PCAP com si fos el PPT, i els requisits en sortirien
  duplicats o amb el document equivocat, cosa que falsejaria precisió i evidència.

**No s'ha corregit ni verificat** (cal comparar el hash del contingut i la font
de cada document). Fins que no es revisi, **4249-2026 no s'executa amb el
proveïdor real**, ni sol ni dins d'una mostra.

La mostra suma 37 chunks per perfil. Extrapolant dels tokens registrats a
422-2026 (V1 ≈ 11,5 k d'entrada i 4,3 k de sortida per chunk; V2 ≈ 14,8 k i 5,0 k),
són uns 425 k + 159 k tokens per a V1 i uns 547 k + 187 k per a V2. És una
estimació, no una mesura: el cost real depèn del text de cada chunk.

## El benchmark no veu dades de l'empresa

4B és tenant-agnostic i això també. Al proveïdor només hi va text públic del
plec. Cap `CompanyProfile`, cap pre-fit, cap compliance.

## Límits coneguts

* **Gold verificat parcial.** `gold-v1-reviewed` (v2) té 11 anotacions
  `verified` i 5 `needs_review` de 16, revisades per AlbertTuvi1 el 2026-09-08.
  `gold-v1` es manté intacte com a esborrany. Les 5 pendents queden fora de les
  mètriques fins que es resolguin.
* **Cap criteri d'adjudicació verificat.** Els 3 del dataset han quedat
  `needs_review` (falta la fórmula de puntuació), o sigui que les mètriques de
  criteris encara **no són calculables**.
* **Mida del dataset.** 11 casos i 16 anotacions, dins de l'objectiu de 10-15
  expedients. L'arquitectura escala a 30-50; només s'anota el que es pot
  verificar literalment sobre pàgines ja processades.
* **La similitud d'aparellament és textual**, no semàntica. Un requisit
  reformulat molt lliurement pot comptar com a no detectat. És conservador a
  propòsit.
* **Cost estimat** requereix configurar preus per model; ara es registren tokens
  i temps.
