"""El contracte comu de tots els canals de recepcio.

Un canal (ZIP, Drive, plataforma, API...) nomes ha de saber produir una
`Delivery`: la llista de fitxers que ha rebut, cadascun acceptat amb els seus
bytes o omes amb un motiu, i, si n'hi havia, el contenidor original (el ZIP) per
desar-lo tal com va arribar.

El que NO fa un canal: validar tipus contra el contracte de formats dels plecs,
desar res, crear documents o classificar. Aixo ho fa el servei de recepcio, una
sola vegada i igual per a tots.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import ReceptionChannel, ReceptionItemStatus, ReceptionSkipReason


@dataclass(slots=True)
class ReceivedFile:
    position: int
    #: On era el fitxer dins de l'entrega (carpeta inclosa), tal com l'ha vist
    #: el canal. Serveix per ensenyar-lo i per classificar pel nom.
    path: str
    filename: str
    size_bytes: int
    status: ReceptionItemStatus
    skip_reason: ReceptionSkipReason | None = None
    data: bytes | None = field(default=None, repr=False)
    #: El tipus que declara el canal, si en declara (Drive o una API si; dins
    #: d'un ZIP no n'hi ha i es dedueix de l'extensio). Els bytes hi manen igual.
    declared_mime_type: str | None = None
    #: Mida comprimida dins d'un contenidor, si n'hi ha.
    packed_size: int | None = None
    #: Identificador del fitxer a l'origen (id de Drive, URL...). Nul dins d'un ZIP.
    source_reference: str | None = None


@dataclass(slots=True)
class Delivery:
    channel: ReceptionChannel
    #: Nom per ensenyar (el del ZIP, el de la carpeta...).
    label: str
    files: list[ReceivedFile]
    #: Identificador de l'entrega a l'origen (id de carpeta, URL...).
    reference: str | None = None
    #: El contenidor original tal com va arribar, per desar-lo. Nul si el canal
    #: entrega fitxers solts.
    container: bytes | None = field(default=None, repr=False)
    container_mime_type: str | None = None
