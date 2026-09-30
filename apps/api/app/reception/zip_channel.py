"""Canal ZIP: d'un paquet pujat a una `Delivery`.

L'unic que sap que hi ha un ZIP. Obre el paquet amb `archive.scan_archive`
(que el rebutja sencer si es hostil) i tradueix les entrades al contracte comu.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from app.models.enums import ReceptionChannel
from app.reception.archive import ArchiveLimits, scan_archive
from app.reception.delivery import Delivery, ReceivedFile

ZIP_MIME_TYPE = "application/zip"


def delivery_from_zip(data: bytes, filename: str | None, limits: ArchiveLimits) -> Delivery:
    """`ArchiveRejected` si el paquet no s'accepta."""
    scan = scan_archive(data, limits)
    label = PurePosixPath((filename or "").replace("\\", "/")).name or "paquet.zip"
    return Delivery(
        channel=ReceptionChannel.ZIP_UPLOAD,
        label=label[:255],
        container=data,
        container_mime_type=ZIP_MIME_TYPE,
        files=[
            ReceivedFile(
                position=entry.position,
                path=entry.path,
                filename=entry.filename,
                size_bytes=entry.declared_size,
                packed_size=entry.compressed_size,
                status=entry.status,
                skip_reason=entry.skip_reason,
                data=entry.data,
            )
            for entry in scan.entries
        ],
    )
