"""Registre de fonts d'ingesta.

La fila `Source` es crea des d'aqui (get_or_create), no per migracio. Cada
definicio diu quin feed es llegeix i quin parser s'aplica.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ingestion.placsp.client import PLACSP_LICITACIONES_FEED
from app.models.enums import SourceType

PLACSP_1044_FEED = (
    "https://contrataciondelsectorpublico.gob.es/sindicacion/sindicacion_1044/"
    "PlataformasAgregadasSinMenores.atom"
)


@dataclass(frozen=True)
class SourceDefinition:
    code: str
    name: str
    feed_url: str
    source_type: SourceType = SourceType.SYNDICATION_ATOM
    parser: str = "placsp"
    # Codi de la fila `Source` quan difereix de la clau del registre
    # (la font 643 historica es va crear com a "PLACSP").
    row_code: str | None = None

    @property
    def db_code(self) -> str:
        return self.row_code or self.code


SOURCES: dict[str, SourceDefinition] = {
    "placsp": SourceDefinition(
        code="placsp",
        name="Plataforma de Contratacion del Sector Publico",
        feed_url=PLACSP_LICITACIONES_FEED,
        row_code="PLACSP",
    ),
    "placsp_agregadas": SourceDefinition(
        code="placsp_agregadas",
        name="PLACSP · plataformes agregades",
        feed_url=PLACSP_1044_FEED,
    ),
}
