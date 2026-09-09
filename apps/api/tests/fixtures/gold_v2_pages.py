"""Text real de les pagines que sostenen les propostes del gold v2.

Congelat al costat (`gold_v2_pages.json`) perque la comprovacio de cites no
depengui d'una base de dades concreta: el test ha de poder dir "aquesta cita es
a la pagina" a qualsevol maquina, tambe a CI.

Es el text **tal com el va extreure el pipeline**, amb els talls de paraula i
els artefactes del PDF inclosos ("se val orará", "valorar án"). No s'ha netejat,
i aixo es el punt: una cita del gold ha de ser localitzable al text que el
sistema veu de veritat, no a una versio embellida que ningu processara mai.

Viu en un JSON i no en un literal de Python perque son dades: sis pagines de
plecs publics de PLACSP, els mateixos que el gold ja cita.
"""

from __future__ import annotations

import json
from pathlib import Path

_SOURCE = Path(__file__).with_suffix(".json")

#: (case_id, document, pagina) -> text complet de la pagina.
GOLD_V2_PAGES: dict[tuple[str, str, int], str] = {
    (entry["case_id"], entry["document_name"], entry["page_number"]): entry["text"]
    for entry in json.loads(_SOURCE.read_text(encoding="utf-8"))
}
