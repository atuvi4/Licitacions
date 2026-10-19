"""Imports d'expedient segons les dades obertes de la PSCP (F-06a, fase 1).

Adaptador de NOMES LECTURA de la font: dataset `ybgg-dgi6` del portal de dades obertes
de la Generalitat (SODA, GET sense clau). Llegeix el PBL amb IVA que la PSCP publica per
a cada expedient i per a cada lot, el contrasta amb el que ja tenim (organ, PBL sense
IVA, VEC, lots) i el desa a `tender_amount_observations` (0063) amb la procedencia
completa. **No toca mai `tenders`, `tender_lots` ni la ingesta de PLACSP.**

Moduls:

* `client`: GET a la font (nomes `analisi.transparenciacatalunya.cat`), pausa entre
  peticions, reintents limitats davant de 429 i 5xx, registre de cada peticio.
* `parser`: lectura exacta dels valors publicats (mai mes de 2 decimals), identificador
  de publicacio, ordre de fases i dates.
* `guards`: les guardes (pures, sense base ni xarxa) que decideixen l'estat de cada
  observacio: `accepted`, `conflict`, `no_value` o `superseded`.
* `service`: l'univers, la consulta per lots de 100 UUID, l'escriptura idempotent amb
  historial i l'informe.

Ordre: `python -m app.ingestion.pscp_open_data --out <dir> [--dry-run] [--limit N]
[--tender <uuid>]`.

Llicencia: Llicencia oberta d'us d'informacio - Catalunya. Cal citar la font i la data
de la darrera actualitzacio, i no es pot alterar el contingut: els imports es desen tal
com es publiquen i mai no es recalculen (ni l'IVA ni la suma dels lots).
"""

from __future__ import annotations

DATASET_ID = "ybgg-dgi6"
PORTAL = "https://analisi.transparenciacatalunya.cat"
RESOURCE_URL = f"{PORTAL}/resource/{DATASET_ID}.json"
METADATA_URL = f"{PORTAL}/api/views/{DATASET_ID}.json"

#: `source_code` de les observacions: font i dataset.
SOURCE_CODE = f"pscp_open_data:{DATASET_ID}"

LICENCE_NAME = "Llic\u00e8ncia oberta d'\u00fas d'informaci\u00f3 \u2013 Catalunya"
LICENCE_URL = (
    "https://administraciodigital.gencat.cat/ca/dades/dades-obertes/informacio-practica/llicencies/"
)
#: Text d'atribucio que exigeix la llicencia. S'hi afegeix sempre la data de la darrera
#: actualitzacio del dataset (`rowsUpdatedAt`): vegeu `attribution()`.
ATTRIBUTION = (
    "Font: Generalitat de Catalunya. Departament d'Economia i Finances \u2014 "
    "Contractaci\u00f3 p\u00fablica a Catalunya: publicacions a la PSCP (dades obertes, "
    "ybgg-dgi6). Llic\u00e8ncia oberta d'\u00fas d'informaci\u00f3 \u2013 Catalunya."
)


def attribution(rows_updated_at: str | None) -> str:
    """L'atribucio amb la data de la darrera actualitzacio del dataset."""
    when = rows_updated_at or "desconeguda"
    return f"{ATTRIBUTION} Darrera actualitzaci\u00f3 del dataset: {when}."
