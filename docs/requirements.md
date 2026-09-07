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

## Prompts versionats

`app/ai/prompts/requirements_v1.py`:

```
PROMPT_VERSION    = "requirements-v1"
EXTRACTOR_VERSION = "tender-requirements-v1"
SCHEMA_VERSION    = "requirements-schema-v1"
```

**No s'editen en silenci.** El `prompt_version` es desa a cada run i forma part
de la clau de cache: canviar el text sense pujar la versió faria que resultats
incomparables semblessin comparables. Si canvia el significat, es crea `v2`.

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
* **Valor estructurat lliure.** `value_json` es valida per esquema quan la
  categoria ho permet; no s'ha perseguit una ontologia perfecta a l'MVP.
* **Deduplicació entre runs.** La clau única és per run: dos runs diferents poden
  contenir requisits equivalents. És intencionat, perquè cada run és una
  fotografia immutable.
* **Qualitat del model no mesurada.** El dataset etiquetat comprova la canonada,
  no l'encert de l'extractor. Això requereix una execució amb credencial real.
