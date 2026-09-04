"""Adaptador de PLACSP (Plataforma de Contratacion del Sector Publico).

Separacio de responsabilitats:

    client.py      obtenir bytes de la sindicacio oficial
    parser.py      XML ATOM+CODICE -> representacio intermedia
    normalizer.py  representacio de PLACSP -> model canonic
    service.py     orquestracio, idempotencia i persistencia
    codes.py       traduccio dels codis oficials

Res d'aquest paquet no ha de coneixer el model canonic excepte el
normalitzador, i res de fora d'aquest paquet ha de coneixer PLACSP.
"""

from app.ingestion.placsp.client import PLACSP_LICITACIONES_FEED, PlacspClient
from app.ingestion.placsp.parser import PARSER_VERSION
from app.ingestion.placsp.service import PlacspIngestionService

__all__ = [
    "PARSER_VERSION",
    "PLACSP_LICITACIONES_FEED",
    "PlacspClient",
    "PlacspIngestionService",
]
