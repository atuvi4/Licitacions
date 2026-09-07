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
python -m app.evaluation.requirements --dataset gold-v1 --provider anthropic
```

Opcions: `--limit`, `--tender <case_id>`, `--force`, `--prompt-version`,
`--output`, `--list`.

Sense credencial l'ordre **no inventa cap resultat**: diu què falta i surt amb
codi 2, que un CI pot distingir d'un fracàs de qualitat.

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

## El benchmark no veu dades de l'empresa

4B és tenant-agnostic i això també. Al proveïdor només hi va text públic del
plec. Cap `CompanyProfile`, cap pre-fit, cap compliance.

## Límits coneguts

* **Cap anotació verificada encara.** Les 16 anotacions són esborranys escrits
  per l'assistent. Fins que una persona no les revisi, **el dataset no és gold
  truth** i les mètriques oficials no es poden calcular.
* **Mida del dataset.** 11 casos i 16 anotacions, dins de l'objectiu de 10-15
  expedients. L'arquitectura escala a 30-50; només s'anota el que es pot
  verificar literalment sobre pàgines ja processades.
* **La similitud d'aparellament és textual**, no semàntica. Un requisit
  reformulat molt lliurement pot comptar com a no detectat. És conservador a
  propòsit.
* **Cost estimat** requereix configurar preus per model; ara es registren tokens
  i temps.
