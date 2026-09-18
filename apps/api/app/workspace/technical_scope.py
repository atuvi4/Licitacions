"""Sobre QUE es va revisar l'abast tecnic d'una candidatura (C-14).

Modul pur, com `manifest.py`, i pel mateix motiu: una empremta nomes val si la
serialitzacio es canonica —mateixa entrada, mateixos bytes, sempre— i aixo s'ha
de poder provar sense base de dades.

**Que hi entra, i per que.** L'abast tecnic es "quins requisits hi ha davant" i
"quins ha decidit una persona que necessiten solucio". Per aixo hi ha:

* el mode de lots i els lots que ara formen la candidatura (C-16): canviar de
  lots canvia els requisits que apliquen;
* cada requisit aplicable, amb la seva empremta de contingut: aixi una correccio
  humana d'un requisit caduca la revisio, cosa que `requirements_fingerprint(run)`
  no detectaria, perque el run no canvia quan algu corregeix una fila;
* els requisits que algu ha declarat que necessiten solucio. Sense aixo, es
  podria confirmar "ho he revisat tot", assenyalar despres un requisit mes, i la
  confirmacio seguiria semblant vigent.

**Que NO hi entra.** La proposta escrita, el proveidor, el producte, el
responsable, les notes, l'estat de validacio i les dates de les solucions. Tot
aixo ja te el seu propi mecanisme de vigencia a la validacio de la solucio (S3),
i barrejar-ho voldria dir que reescriure una frase obliga a tornar a revisar tot
l'abast. Les solucions manuals tampoc hi son: no diuen res sobre quins requisits
del plec necessiten resposta.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable

#: Versio del format. Si algun dia canvia el que cobreix l'empremta, les
#: confirmacions velles han de deixar de coincidir en comptes de quedar
#: silenciosament com a vigents.
SCOPE_VERSION = 1

#: La candidatura es d'un lot concret: l'abast es el seu lot i els requisits generals.
LOT_BOUND = "lot_bound"
#: El plec no te lots: l'abast son els requisits de la licitacio.
NO_LOTS = "no_lots"
#: Plec amb lots i candidatura global: l'abast el fixen els lots oferts (C-16).
SELECTED_LOTS = "selected_lots"


def _sorted_ids(values: Iterable[uuid.UUID | str]) -> list[str]:
    """Conjunt ordenat: ni l'ordre de la base ni els duplicats poden comptar."""
    return sorted({str(value) for value in values})


def technical_scope_fingerprint(
    *,
    lot_mode: str,
    tender_lot_ids: Iterable[uuid.UUID | str],
    requirements: Iterable[tuple[uuid.UUID | str, str]],
    selected_requirement_ids: Iterable[uuid.UUID | str],
) -> str:
    """Identitat material de l'abast tecnic. SHA-256 en hexadecimal.

    Es serialitza com un objecte amb claus i no com una llista plana: aixi moure
    un identificador de la llista de requisits a la de seleccionats canvia
    l'empremta, que es el que ha de passar.
    """
    payload = json.dumps(
        {
            "version": SCOPE_VERSION,
            "lot_mode": lot_mode,
            "tender_lot_ids": _sorted_ids(tender_lot_ids),
            "requirements": sorted(
                [str(rid), fingerprint] for rid, fingerprint in requirements
            ),
            "selected_requirement_ids": _sorted_ids(selected_requirement_ids),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
