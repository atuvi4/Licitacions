"""Provenance dels tests que carreguen estructura per la via interna (0060).

Els tests que munten una licitacio cridant directament `tender_structure` i
`tender_scope` fan el paper d'un carregador d'avaluacio: escriuen amb origen
`gold` i una passada de productor fixa, perque la via interna no accepta cap
escriptura sense provenance.
"""

from __future__ import annotations

import uuid

from app.services.structure_provenance import Provenance

#: La «passada» dels fixtures de test: un identificador fix, sense cap fila.
FIXTURE_RUN_ID = uuid.UUID("00000000-0000-4000-8000-000000000060")
PROV = Provenance.gold(FIXTURE_RUN_ID)
