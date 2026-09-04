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
| `at:deleted-entry` | Implementat i testat, però no s'ha vist cap làpida a les mostres reals descarregades. El comportament (marcar `cancelled`) està cobert per fixtures. |
| Contractes menors i encàrrecs | Tenen feeds propis (`contratosMenores…`, encàrrecs a mitjà propi, consultes preliminars). No s'ingereixen. |
| Plataformes agregades | Les comunitats autònomes amb plataforma pròpia s'agreguen en un altre feed. No s'ingereix encara. |
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

```bash
# mostra controlada del feed viu
python -m app.ingestion.placsp --limit 300

# recorre 3 fitxers encadenats cap enrere en el temps
python -m app.ingestion.placsp --max-files 3 --limit 500

# continua des de l'últim checkpoint
python -m app.ingestion.placsp --resume --max-files 2

# sense escriure res
python -m app.ingestion.placsp --dry-run --limit 20

# des d'un fitxer local (tests, reproduccions)
python -m app.ingestion.placsp --fixture tests/fixtures/placsp/feed_sample.atom
```

No hi ha planificador ni endpoint públic per llançar ingestes: s'engeguen a mà.
