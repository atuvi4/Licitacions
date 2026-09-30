"""Canal ZIP, part pura: obrir un paquet de licitacio sense confiar-hi.

Un ZIP el pot haver fet qualsevol, i el que arriba per la plataforma de
contractacio sovint ha passat per tres eines diferents abans d'arribar aqui.
Aquest modul el llegeix **en memoria**: mai no escriu res al disc, aixi que un
nom com `../../etc/passwd` no pot sortir enlloc. Tot i aixi es rebutja, perque
un paquet que el porta no es un paquet de licitacio honest.

Dues menes de problema, i cadascuna te una resposta diferent:

* **El paquet es hostil o esta trencat** (camins que surten del paquet, enllacos
  simbolics, xifrat, bomba de compressio, massa fitxers, entrades repetides,
  corrupcio). Es rebutja **sencer** amb `ArchiveRejected`. Acceptar-ne la meitat
  voldria dir analitzar un expedient que no es el que es va publicar.
* **Un fitxer concret no es pot fer servir** (buit, massa gran, fitxer de
  sistema, ZIP massa niat). Es registra com a omes, amb el motiu, i la resta
  continua. La persona ha de veure que falta, no descobrir-ho a l'analisi.

**Les mides declarades son un sostre fiable.** `zipfile` no retorna mai mes
bytes dels que diu la capcalera (i valida el CRC en acabar), de manera que les
comprovacions de mida es fan ABANS de descomprimir, sobre la declaracio.

**Els noms.** Si el ZIP no marca UTF-8, `zipfile` els llegeix com a CP437. Moltes
eines de Windows hi escriuen UTF-8 sense la marca: es prova de recuperar-lo i,
si no es UTF-8 valid, es queda la lectura CP437, que es la de la norma.
"""

from __future__ import annotations

import io
import re
import stat
import zipfile
from dataclasses import dataclass, field

from app.models.enums import ReceptionItemStatus, ReceptionSkipReason

#: Bit 0 del "general purpose flag": el membre va xifrat.
_FLAG_ENCRYPTED = 0x1
#: Bit 11: el nom va en UTF-8.
_FLAG_UTF8 = 0x800
#: `create_system` de Unix: nomes aleshores els bits alts d'`external_attr` son
#: un mode de fitxer.
_UNIX = 3
#: Per sota d'aquesta mida no s'aplica la ratio: un fitxer petit de zeros no es
#: cap amenaca i una ratio alta en un TXT curt es normal.
_RATIO_FLOOR_BYTES = 1024 * 1024

_SYSTEM_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})
_SYSTEM_DIRS = frozenset({"__macosx"})
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_DRIVE = re.compile(r"^[A-Za-z]:")

#: Un contenidor d'Office tambe es un ZIP. El que el delata es aquest fitxer.
_OFFICE_MARKER = "[Content_Types].xml"


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_members: int
    max_member_bytes: int
    max_total_bytes: int
    max_ratio: int
    #: Quants nivells de ZIP dins de ZIP s'obren. 1 = el paquet i els ZIP que
    #: porti a dins; els que hi hagi dins d'aquests, no.
    max_depth: int = 1


class ArchiveRejected(Exception):
    """El paquet no s'accepta. `reason` es un codi estable per a l'API."""

    def __init__(self, reason: str, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.path = path


@dataclass(slots=True)
class ArchiveEntry:
    """Un fitxer del paquet, s'hagi acceptat o no."""

    position: int
    #: Cami dins del paquet, amb `/`. Per als fitxers d'un ZIP intern, el cami
    #: del ZIP intern + `/` + el cami de dins.
    path: str
    filename: str
    declared_size: int
    compressed_size: int
    status: ReceptionItemStatus
    skip_reason: ReceptionSkipReason | None = None
    data: bytes | None = field(default=None, repr=False)
    depth: int = 0


@dataclass(slots=True)
class ArchiveScan:
    entries: list[ArchiveEntry]

    @property
    def accepted(self) -> list[ArchiveEntry]:
        return [e for e in self.entries if e.status is ReceptionItemStatus.ACCEPTED]


def is_zip(data: bytes) -> bool:
    # PK\x05\x06 es un ZIP buit (nomes el directori central).
    return data[:4] in (b"PK\x03\x04", b"PK\x05\x06")


def is_office_container(data: bytes) -> bool:
    """Cert si els bytes son un DOCX, XLSX o PPTX (i per tant no s'han d'obrir)."""
    if not is_zip(data):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return _OFFICE_MARKER in zf.namelist()
    except zipfile.BadZipFile:
        return False


def decode_name(info: zipfile.ZipInfo) -> str:
    if info.flag_bits & _FLAG_UTF8:
        return info.filename
    try:
        return info.filename.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return info.filename


def _safe_path(raw: str) -> str:
    """El cami normalitzat, o `ArchiveRejected` si surt del paquet."""
    if _CONTROL_CHARS.search(raw):
        raise ArchiveRejected("unsafe_path", "Un nom del paquet porta caracters de control.")
    path = raw.replace("\\", "/")
    if path.startswith("/") or _DRIVE.match(path):
        raise ArchiveRejected("unsafe_path", "El paquet conte un cami absolut.", path=raw)
    parts = [p for p in path.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ArchiveRejected(
            "unsafe_path", "El paquet conte un cami que en surt ('..').", path=raw
        )
    return "/".join(parts)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return info.create_system == _UNIX and stat.S_ISLNK(info.external_attr >> 16)


def _is_system_file(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1].lower()
    return (
        name in _SYSTEM_NAMES
        or name.startswith("~$")
        or name.startswith("._")
        or any(p.lower() in _SYSTEM_DIRS for p in parts[:-1])
    )


class _Walker:
    def __init__(self, limits: ArchiveLimits) -> None:
        self.limits = limits
        self.entries: list[ArchiveEntry] = []
        self.member_count = 0
        self.total_bytes = 0

    def _add(self, **kwargs) -> ArchiveEntry:
        entry = ArchiveEntry(position=len(self.entries), **kwargs)
        self.entries.append(entry)
        return entry

    def walk(self, zf: zipfile.ZipFile, prefix: str, depth: int) -> None:
        seen: set[str] = set()
        for info in zf.infolist():
            path = _safe_path(decode_name(info))
            if not path:
                continue
            if info.flag_bits & _FLAG_ENCRYPTED:
                raise ArchiveRejected(
                    "encrypted", "El paquet conte fitxers xifrats.", path=prefix + path
                )
            if _is_symlink(info):
                raise ArchiveRejected(
                    "symlink", "El paquet conte enllacos simbolics.", path=prefix + path
                )
            if info.is_dir():
                continue
            if path in seen:
                raise ArchiveRejected(
                    "duplicate_entry",
                    "El paquet conte dues entrades amb el mateix nom.",
                    path=prefix + path,
                )
            seen.add(path)

            self.member_count += 1
            if self.member_count > self.limits.max_members:
                raise ArchiveRejected(
                    "too_many_members",
                    f"El paquet te mes de {self.limits.max_members} fitxers.",
                )

            full = prefix + path
            base = dict(
                path=full,
                filename=full.rsplit("/", 1)[-1],
                declared_size=info.file_size,
                compressed_size=info.compress_size,
                depth=depth,
            )
            if _is_system_file(path):
                self._add(
                    status=ReceptionItemStatus.SKIPPED,
                    skip_reason=ReceptionSkipReason.SYSTEM_FILE,
                    **base,
                )
                continue
            if info.file_size == 0:
                self._add(
                    status=ReceptionItemStatus.SKIPPED,
                    skip_reason=ReceptionSkipReason.EMPTY,
                    **base,
                )
                continue

            if (
                info.file_size >= _RATIO_FLOOR_BYTES
                and info.file_size > self.limits.max_ratio * max(info.compress_size, 1)
            ):
                raise ArchiveRejected(
                    "compression_ratio",
                    "Un fitxer del paquet te una ratio de compressio impossible en un plec.",
                    path=full,
                )
            if info.file_size > self.limits.max_member_bytes:
                self._add(
                    status=ReceptionItemStatus.SKIPPED,
                    skip_reason=ReceptionSkipReason.TOO_LARGE,
                    **base,
                )
                continue
            self.total_bytes += info.file_size
            if self.total_bytes > self.limits.max_total_bytes:
                raise ArchiveRejected(
                    "too_large_uncompressed",
                    "El contingut descomprimit del paquet supera el maxim permes.",
                )

            try:
                data = zf.read(info)
            except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, OSError) as exc:
                raise ArchiveRejected(
                    "corrupt", "El paquet esta malmes i no es pot llegir.", path=full
                ) from exc

            if is_zip(data) and not is_office_container(data):
                self._nested(data, base, full, depth)
                continue

            self._add(status=ReceptionItemStatus.ACCEPTED, data=data, **base)

    def _nested(self, data: bytes, base: dict, full: str, depth: int) -> None:
        if depth + 1 > self.limits.max_depth:
            self._add(
                status=ReceptionItemStatus.SKIPPED,
                skip_reason=ReceptionSkipReason.NESTED_TOO_DEEP,
                **base,
            )
            return
        try:
            inner = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            self._add(
                status=ReceptionItemStatus.SKIPPED,
                skip_reason=ReceptionSkipReason.UNREADABLE_ARCHIVE,
                **base,
            )
            return
        # El ZIP intern queda registrat com a obert, i els seus fitxers compten
        # als mateixos limits que la resta: niar no es una manera d'esquivar-los.
        self._add(status=ReceptionItemStatus.EXPANDED, **base)
        with inner:
            self.walk(inner, prefix=full + "/", depth=depth + 1)


def scan_archive(data: bytes, limits: ArchiveLimits) -> ArchiveScan:
    """Llegeix el paquet sencer o el rebutja sencer.

    Retorna les entrades en l'ordre del directori central, que es l'ordre en
    que les va posar qui va fer el ZIP.
    """
    if not is_zip(data):
        raise ArchiveRejected("not_a_zip", "El fitxer no es un paquet ZIP.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveRejected("corrupt", "El paquet esta malmes i no es pot llegir.") from exc
    with zf:
        if _OFFICE_MARKER in zf.namelist():
            raise ArchiveRejected(
                "office_document",
                "Aixo es un document d'Office, no un paquet: puja'l com a document.",
            )
        walker = _Walker(limits)
        walker.walk(zf, prefix="", depth=0)
    return ArchiveScan(entries=walker.entries)
