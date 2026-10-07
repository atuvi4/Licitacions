# Ingestió de PLACSP

> Estat: iteració 2. Parser `placsp-v1`.
> Verificat contra el feed oficial del 2026-09-02/03 (176 i 500 entrades reals).

## 1. La font

**Plataforma de Contratación del Sector Público**, sindicació oficial de dades
obertes. No hi ha cap scraping: tot surt del mecanisme de reutilització publicat
per la Dirección General del Patrimonio del Estado.

| | |
| --- | --- |
| Feed | `https://contrataciondelestado.es/sindicacion/sindicacion_643/licitacionesPerfilesContratanteCompleto3.atom` |
| Format | ATOM (RFC 4287) amb càrrega útil **CODICE** (derivat d'UBL) |
| Especificació | *«Formato de sindicación y reutilización de datos sobre las publicaciones en la PLACSP»*, v1.10, 17/09/2024 |
| Contingut | Licitacions dels perfils de contractant allotjats a PLACSP, excloent contractes menors |

### Espais de noms

```
(default)      http://www.w3.org/2005/Atom
cbc            urn:dgpe:names:draft:codice:schema:xsd:CommonBasicComponents-2
cac            urn:dgpe:names:draft:codice:schema:xsd:CommonAggregateComponents-2
cac-place-ext  urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonAggregateComponents-2
cbc-place-ext  urn:dgpe:names:draft:codice-place-ext:schema:xsd:CommonBasicComponents-2
at             http://purl.org/atompub/tombstones/1.0
```

El parser cerca els elements CODICE amb comodí de namespace (`{*}Element`)
**a propòsit**: els prefixos han canviat entre versions de CODICE, però el nom
local és estable.

## 2. Checkpoint: com funciona realment la paginació

El fitxer `…Completo3.atom` és una **finestra mòbil** amb les entrades més
recents. L'històric es recorre pels enllaços del propi feed:

```xml
<link rel="self"  href="…Completo3.atom"/>
<link rel="first" href="…Completo3.atom"/>
<link rel="next"  href="…Completo3_20260902_201433.atom"/>
```

* `first` = el fitxer viu (el més recent).
* `next` = el **snapshot immediatament anterior**. Seguir `next` recorre
  l'històric cap enrere; els fitxers amb marca de temps al nom són immutables.

Per tant el checkpoint que guardem a `sources.checkpoint` és **l'URL de l'últim
fitxer processat**. Amb `--resume` la ingesta continua des d'allà. No hem
inventat cap cursor: és el mecanisme que ofereix la font.

> Conseqüència pràctica important: llançar dues vegades la ingesta contra el
> fitxer **viu** no dona 0 novetats si la font ha publicat un snapshot nou pel
> mig. Per provar idempotència cal fer servir un fitxer arxivat, que sí que és
> immutable.

## 3. Identificadors

| Concepte | Camp que fem servir | Per què |
| --- | --- | --- |
| Licitació | `atom:entry/id` | Únic i estable. Verificat: 176/176 únics al feed. |
| **No** la licitació | `cbc:ContractFolderID` | El número d'expedient **no** és únic globalment: `23/2026` apareix en dos organismes diferents al mateix feed. Es guarda com a `expediente_number` informatiu. |
| Organisme | `PartyIdentification/ID[@schemeName='ID_PLATAFORMA']` | Present al 100%. Identifica l'**òrgan** de contractació. |
| **No** l'organisme | NIF | Un mateix ajuntament té diversos òrgans amb el mateix NIF (Alcaldia, Junta de Govern…). Es guarda com a `tax_identifier`. |

## 4. Mapatge al model canònic

Verificat sobre entrades reals. Els percentatges són cobertura mesurada sobre
les 176 entrades del feed del 2026-09-02.

| Camp canònic | Origen a CODICE | Cobertura |
| --- | --- | --- |
| `expediente_number` | `ContractFolderStatus/ContractFolderID` | 100% |
| `status` | `ContractFolderStatusCode` | 100% |
| `title` | `ProcurementProject/Name` | 100% |
| `contract_type` | `ProcurementProject/TypeCode` | 100% |
| `estimated_value` | `BudgetAmount/EstimatedOverallContractAmount` | 100% |
| `base_budget` | `BudgetAmount/TotalAmount` | 100% |
| `base_budget_without_tax` | `BudgetAmount/TaxExclusiveAmount` | 100% |
| `nuts_code` | `RealizedLocation/CountrySubentityCode` | 100% |
| `place_of_execution` | `RealizedLocation/CountrySubentity` | 96% |
| `procedure_type` | `TenderingProcess/ProcedureCode` | 100% |
| `deadline_at` | `TenderSubmissionDeadlinePeriod/EndDate` + `EndTime` | 99% |
| CPV | `ProcurementProject/RequiredCommodityClassification/ItemClassificationCode` | 100% |
| Lots | `ProcurementProjectLot` | 33 de 176 expedients |
| Documents | `LegalDocumentReference`, `TechnicalDocumentReference`, `AdditionalDocumentReference`, `ValidNoticeInfo//Attachment` | 100% |

### Llistes de codis

No estan escrites de memòria: es van descarregar de les URL que el propi XML
indica a l'atribut `listURI`.

* `SyndicationContractFolderStatusCode-2.04.gc` → `PRE`, `PUB`, `EV`, `ADJ`, `RES`, `ANUL`
* `ContractCode-2.08.gc` → 1=Suministros, 2=Servicios, 3=Obras, 22=Concesión de servicios…
* `SyndicationTenderingProcessCode-2.07.gc` → 1=Abierto, 9=Abierto simplificado, 3=Negociado sin publicidad…

Un codi desconegut **no s'inventa**: el camp canònic queda `unknown` i el codi
original es conserva a `source_status_code`.

## 5. Decisions de mapatge que calia prendre

### Data de publicació

PLACSP **no publica** un camp «data de publicació de l'expedient». El que hi ha
són les dates d'emissió de cada anunci (`ValidNoticeInfo`). Es pren la més
antiga **d'entre els anuncis de convocatòria** (`DOC_CN`, `DOC_CD`, `DOC_PIN`) i,
si no n'hi ha cap, la més antiga de totes.

Sense aquest matís, un expedient ja adjudicat mostrava com a data de publicació
la de l'anunci d'adjudicació, **posterior al seu propi termini**. Ho vam veure
amb dades reals abans de fixar la regla.

### Nom dels documents

Les referències a plecs **mai** porten `FileName`: verificat, 0 de 697
referències del feed real en tenien. La URL és un identificador opac. Per tant
el nom es deriva del **tipus de document** («Plec de clàusules administratives»,
«Plec de prescripcions tècniques»…) i no de la URL.

### Termini i zona horària

`EndDate` + `EndTime` vénen sense zona horària. S'assumeix **Europe/Madrid**.
Sense hora, s'assumeix final del dia (23:59:59). Això és una assumpció, no una
dada de la font.

### CPV principal

CODICE no marca quin CPV és el principal. S'assumeix que **el primer que
publica la font** ho és (`is_main=true`). És l'ordre del document, no una
inferència.

## 6. Incerteses conegudes

Coses que hem decidit **no** endevinar:

| Tema | Situació |
| --- | --- |
| `TotalAmount = 0` | Alguns expedients (acords marc, contractes centralitzats) publiquen import base 0. Es guarda tal qual: no és un error nostre. |
| Termini dels lots | La font no publica termini per lot al feed de licitacions. `TenderLot.deadline_at` queda a null. |
| Descripció | S'omple amb el `summary` de l'ATOM, que és un resum generat, no l'objecte del contracte redactat. |
| `at:deleted-entry` | Presència a la font, no estat: vegeu «Presència a la font» a la secció 8. Mai marca `cancelled`. |
| Contractes menors i encàrrecs | Tenen feeds propis (`contratosMenores…`, encàrrecs a mitjà propi, consultes preliminars). No s'ingereixen. |
| Plataformes agregades | Les comunitats autònomes amb plataforma pròpia s'agreguen al feed 1044, que s'ingereix com a font `placsp_agregadas` (radar 3B-1). |
| Catàleg CPV | Els codis entren amb descripció provisional (`CPV 45314320`) perquè el feed només dona el codi. Falta importar el catàleg oficial. |

## 7. Seguretat del parser

* `defusedxml` per parsejar: sense DTD, sense entitats externes, sense
  expansió d'entitats. Hi ha un test que ho comprova amb una càrrega XXE.
* Límit dur de 80 MB per fitxer descarregat.
* Timeouts, User-Agent identificable amb contacte, 3 reintents amb espera
  creixent i pausa entre fitxers. Cap bucle infinit.
* Una entrada malmesa es marca `processing_status=failed`, es conserva el raw
  i la ingesta continua; l'execució queda com a `partial`.

## 8. Ús

Des d'`apps/api`:

```bash
# sincronització incremental (fins al solapament de 48 h, màxim 50 pàgines)
python -m app.ingestion.sync --source placsp_agregadas [--max-pages N]

# backfill acotat i represa des del checkpoint (--resume exigeix --backfill-until)
python -m app.ingestion.sync --source placsp_agregadas --backfill-until 2026-09-01 --max-pages 200 [--resume]

# bucle propi cada S segons (fins a SIGINT/SIGTERM)
python -m app.ingestion.sync --source placsp_agregadas --every 900

# salut de les fonts (només lectura, sense lease)
python -m app.ingestion.sync --status [--source placsp_agregadas]

# desenvolupament: un fitxer local, directe, sense lease ni salut
python -m app.ingestion.placsp --source placsp_agregadas --fixture tests/fixtures/placsp/agregadas/page1.atom

# després de desplegar 0056, o si cal refer la cerca (idempotent)
python -m app.tenders.search_backfill
```

Codi de sortida: **0** `success`, **2** `partial`, **1** `failed` (també si la font
està desactivada o un altre sync té el lease). La CLI antiga `app.ingestion.placsp`
contra el feed viu passa pel mateix `run_source_sync` (lease i salut); `--feed-url`
només s'admet amb `--dry-run`. No hi ha endpoint públic per llançar ingestes.

### Sincronització de font

* **Lease per font**: un UPDATE condicional sobre `sources` (15 min, renovat per un
  batec). Dos syncs de la mateixa font no corren mai alhora: el segon surt amb codi 1.
* **Salut** (`sources.last_sync_*`, `last_feed_updated_at`, `updated_watermark`): es
  veu amb `--status` i a `GET /platform/sources` (operador). `last_feed_updated_at`
  només avança.
* **Incremental**: es llegeix des de la pàgina 1 fins que una pàgina sencera és
  anterior a `marca d'aigua − 48 h`. Les entrades conegudes es resolen per hash.
* **`partial`**: hi ha una pàgina posterior a la 1 que falla o respon 304, la cadena
  `next` queda incompleta, o alguna entrada falla. També és `partial` quan s'esgota
  `--max-pages` amb pàgines pendents o `--limit` talla el run, si ja hi ha marca
  d'aigua o és un backfill; el primer run sense marca acaba `success`. La marca
  d'aigua només avança amb `success`, i mai després d'un tall per `--limit`.
  El checkpoint (només en backfill) no passa mai d'una pàgina llegida a mitges.
  `failed`: la pàgina 1 no es pot obtenir o el run peta.
* **Comptadors del run**: `records_fetched` = `new` + `changed` + `unchanged` +
  `failed` + `superseded` + `conflicted` + les làpides aplicades a una licitació ja
  coneguda (`tombstones_applied`). Una làpida d'un expedient encara desconegut
  compta a `new`.
* **Ordre**: una publicació amb `updated` anterior a la vigent queda `superseded`;
  si té el mateix `updated` i un hash diferent queda en conflicte. Totes dues es
  desen i no s'apliquen.
* **Organisme reanomenat**: si una publicació porta un nom nou per a un organisme
  ja conegut, el `search_text` de **totes** les seves licitacions es recalcula a la
  mateixa transacció (en bloc), no només el de l'entrada rebuda. No cal backfill.

#### En producció

* El sync incremental pagina **fins al límit temporal de solapament** (48 h abans de
  la marca d'aigua). `--max-pages` (per defecte **50**) és només un **topall de
  seguretat**, no el criteri d'aturada.
* Si el topall s'assoleix abans d'arribar al solapament, amb marca d'aigua ja
  existent, el run queda **`partial`** i la marca **no avança**: un catàleg
  incomplet no es pot fer passar per «sync complet». El run següent torna a
  començar per la pàgina 1 i reprocessa per hash el que ja tenia.
* El **primer** run (sense marca) fixa la marca a partir de les entrades que ha
  processat; el catàleg històric anterior es completa amb `--backfill-until
  YYYY-MM-DD` (amb `--resume` per continuar des del checkpoint).
* `--max-pages 2` (o qualsevol valor petit) és **només per a proves**.
* Recomanació per al procés de producció:
  `python -m app.ingestion.sync --source placsp_agregadas --every 1800 --max-pages 50`.

### Presència a la font

Una `at:deleted-entry` només diu que l'entrada ja no és accessible a la font. Es
desa com a `SourceRecord` i, si no és anterior a la publicació vigent, marca
`Tender.source_removed_at`: conserva la primera data no restaurada i emet
`SOURCE_ENTRY_REMOVED`, que és informatiu. **No hi ha cap estat `withdrawn`**:
`status` no canvia mai per una làpida, i només `ANUL` explícit porta a
`cancelled`. Una republicació posterior posa `source_removed_at` a nul
(`SOURCE_ENTRY_RESTORED`). Documents i lots absents d'una publicació completa
posterior es marquen amb `removed_at` i mai s'esborren. Una publicació completa
llegida en una pàgina obtinguda s'aplica sencera (també les absències) encara que el
run acabi `partial` per una pàgina posterior; el que mai s'aplica és el que hi hagi
a les pàgines no obtingudes. La fitxa i el processament
de plecs ignoren els documents retirats, i **cap input d'anàlisi nou** els llegeix
(extracció de requisits, classificació, revisió documental, cites, enllaços i
observacions de dimensionament nous: selector únic `app/tenders/documents.py`).
Mentre la run d'extracció (o la classificació) vigent hagi llegit un plec retirat,
`POST …/analysis/runs` es refusa (409 `analysis_prerequisites_missing`,
`missing: requirements_outdated` / `classification_outdated`) fins que es tornen a
analitzar els plecs. Les runs antigues i les seves cites es conserven tal com eren;
si el document torna en una publicació completa posterior, torna a ser elegible.
