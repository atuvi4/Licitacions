"""Rejoc d'una captura anterior de l'adaptador PSCP, sense xarxa (F-06e, `--replay`).

`ReplayClient` te la mateixa interficie que `SodaClient` (context manager asincron,
`get(url, purpose=...)`, `requests_made`, `log`), pero **no fa mai cap peticio**: respon
amb les respostes que un run anterior va desar al seu directori d'informe (`--out`):
`raw_manifest.json` i `pscp_open_data/<stamp>/{metadata_start.json, metadata_end.json,
batch_*.json}`.

Abans de res (`__aenter__`) verifica la captura: cada fitxer del manifest hi es i el seu
SHA-256 es el `sha256_stored` del manifest. Qualsevol discrepancia -> `ReplayError` i no
es llegeix ni s'escriu res.

Que serveix:

* les metadades: la primera consulta, `metadata_start`; la segona, `metadata_end`;
* les files: per a cada consulta per lots d'UUID, les files desades de TOTS els lots
  originals amb `id_intern` d'aquells UUID (no cal que els lots coincideixin amb els
  del run original: un `--batch-size` diferent dona el mateix resultat), ordenades per
  `id_intern, :id` i paginades amb `$offset`/`$limit`. Una UUID que el run original va
  demanar i no va tornar files continua sense files; una que el run original NO va
  demanar -> `ReplayError` (no es pot rejugar el que no es va capturar). El `$select` ha
  de ser el mateix que el de la captura.

Cada resposta porta `Fetched.origins`: per a cada fila, l'entrada del manifest original
d'on surt. El servei en fa la procedencia (clau d'emmagatzematge, SHA-256 del cos
original, `retrieved_at`) i hi afegeix la marca `replay` (directori i SHA-256 del
`raw_manifest.json`), perque quedi clar que no es una lectura nova. `requests_made` es
sempre 0: cap peticio de xarxa.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from app.ingestion.pscp_open_data import METADATA_URL, RESOURCE_URL
from app.ingestion.pscp_open_data.client import Fetched

MANIFEST = "raw_manifest.json"
_IN = re.compile(r"id_intern IN \(([^)]*)\)")
_QUOTED = re.compile(r"'([^']*)'")
_BATCH_NAME = re.compile(r"^batch_(\d+)_off(\d+)\.json$")


class ReplayError(Exception):
    """La captura no es pot rejugar (fitxer absent o alterat, consulta no capturada...)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _requested_uuids(url: str) -> tuple[set[str], str | None]:
    """Els UUID d'una consulta per lots (`id_intern IN (...)`) i el seu `$select`."""
    params = httpx.URL(url).params
    where = params.get("$where") or ""
    found: set[str] = set()
    for listed in _IN.findall(where):
        found |= {value.lower() for value in _QUOTED.findall(listed)}
    return found, params.get("$select")


def _row_sort_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("id_intern")), str(row.get(":id")))


class ReplayClient:
    """Client de rejoc: la mateixa interficie que `SodaClient`, cap peticio de xarxa."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        #: Peticions de XARXA: sempre 0 (el servei ho posa a l'informe).
        self.requests_made = 0
        #: Consultes servides des de la captura.
        self.replayed = 0
        self.log: list[dict[str, Any]] = []
        #: Directori i SHA-256 del manifest, un cop verificat (`load`).
        self.replay_info: dict[str, Any] | None = None
        self._metadata: list[tuple[dict[str, Any], bytes]] = []
        self._metadata_served = 0
        self._requested: set[str] = set()
        self._select: str | None = None
        self._rows: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}

    async def __aenter__(self) -> ReplayClient:
        self.load()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    # ------------------------------------------------------------------ #
    # Verificacio i carrega de la captura                                  #
    # ------------------------------------------------------------------ #

    def _file(self, key: Any) -> Path:
        if not isinstance(key, str) or not key:
            raise ReplayError("Una entrada del manifest no te `key`.")
        parts = PurePosixPath(key).parts
        if PurePosixPath(key).is_absolute() or ".." in parts or "\\" in key:
            raise ReplayError(f"Clau d'emmagatzematge no admesa al manifest: {key!r}.")
        return self.directory.joinpath(*parts)

    def load(self) -> None:
        """Verifica tota la captura (SHA-256 de cada fitxer) i la carrega. Idempotent."""
        if self.replay_info is not None:
            return
        manifest_path = self.directory / MANIFEST
        if not manifest_path.is_file():
            raise ReplayError(f"No hi ha {MANIFEST} a {self.directory}.")
        manifest_bytes = manifest_path.read_bytes()
        try:
            manifest = json.loads(manifest_bytes)
        except ValueError as exc:
            raise ReplayError(f"{MANIFEST} no es JSON valid.") from exc
        if not isinstance(manifest, list) or not manifest:
            raise ReplayError(f"{MANIFEST} ha de ser una llista no buida.")

        # 1) Tots els fitxers hi son i el seu SHA-256 es el del manifest (abans de res).
        bodies: list[tuple[dict[str, Any], bytes]] = []
        problems: list[str] = []
        for entry in manifest:
            if not isinstance(entry, dict):
                raise ReplayError(f"{MANIFEST}: una entrada no es un objecte.")
            path = self._file(entry.get("key"))
            if not path.is_file():
                problems.append(f"absent: {entry['key']}")
                continue
            body = path.read_bytes()
            if _sha256(body) != entry.get("sha256_stored"):
                problems.append(f"sha256 diferent: {entry['key']}")
                continue
            bodies.append((entry, body))
        if problems:
            shown = "; ".join(problems[:5])
            more = f" (i {len(problems) - 5} mes)" if len(problems) > 5 else ""
            raise ReplayError(f"La captura no es la del manifest: {shown}{more}.")

        # 2) Metadades i files, amb l'origen de cada fila.
        metadata: dict[str, tuple[dict[str, Any], bytes]] = {}
        rows: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        requested: set[str] = set()
        selects: set[str | None] = set()
        for entry, body in bodies:
            name = PurePosixPath(entry["key"]).name
            if name in ("metadata_start.json", "metadata_end.json"):
                if name in metadata:
                    raise ReplayError(f"{MANIFEST}: {name} repetit.")
                metadata[name] = (self._origin(entry), body)
                continue
            match = _BATCH_NAME.match(name)
            url = entry.get("url")
            if match is None or not isinstance(url, str) or not url.startswith(RESOURCE_URL):
                raise ReplayError(f"{MANIFEST}: entrada no reconeguda: {entry['key']}.")
            wanted, select = _requested_uuids(url)
            if not wanted:
                raise ReplayError(f"{MANIFEST}: consulta sense UUID: {entry['key']}.")
            requested |= wanted
            selects.add(select)
            try:
                document = json.loads(body)
            except ValueError as exc:
                raise ReplayError(f"Resposta desada no llegible: {entry['key']}.") from exc
            if not isinstance(document, list):
                raise ReplayError(f"La resposta desada no es una llista: {entry['key']}.")
            origin = self._origin(
                entry, batch=int(match.group(1)), offset=int(match.group(2)), rows=len(document)
            )
            for row in document:
                if not isinstance(row, dict):
                    raise ReplayError(f"Una fila desada no es un objecte: {entry['key']}.")
                uid = str(row.get("id_intern") or "")[:36].lower()
                # Una fila que no es de cap UUID demanat (el servei la compta com a
                # inesperada) no es serveix: no canvia cap avaluacio.
                if uid in wanted:
                    rows.setdefault(uid, []).append((row, origin))
        if set(metadata) != {"metadata_start.json", "metadata_end.json"}:
            raise ReplayError(f"{MANIFEST}: calen metadata_start.json i metadata_end.json.")
        if len(selects) > 1:
            raise ReplayError(f"{MANIFEST}: les consultes no tenen el mateix $select.")
        self._metadata = [metadata["metadata_start.json"], metadata["metadata_end.json"]]
        self._requested = requested
        self._select = selects.pop() if selects else None
        self._rows = rows
        self.replay_info = {
            "dir": str(self.directory.resolve()),
            "raw_manifest_sha256": _sha256(manifest_bytes),
            "files_verified": len(bodies),
            "uuids_captured": len(requested),
            "rows_captured": sum(len(items) for items in rows.values()),
        }

    @staticmethod
    def _origin(entry: dict[str, Any], **extra: Any) -> dict[str, Any]:
        origin = {
            "key": entry["key"],
            "sha256_original": entry.get("sha256_original"),
            "sha256_stored": entry.get("sha256_stored"),
            "url": entry.get("url"),
            "retrieved_at": entry.get("retrieved_at"),
            **extra,
        }
        original = origin["sha256_original"]
        if not (isinstance(original, str) and len(original) == 64):
            raise ReplayError(f"{MANIFEST}: sha256_original absent: {entry['key']}.")
        try:
            datetime.fromisoformat(str(origin["retrieved_at"]))
        except ValueError as exc:
            raise ReplayError(f"{MANIFEST}: retrieved_at no llegible: {entry['key']}.") from exc
        return origin

    # ------------------------------------------------------------------ #
    # La interficie de `SodaClient`                                        #
    # ------------------------------------------------------------------ #

    async def get(self, url: str, *, purpose: str = "") -> Fetched:
        if self.replay_info is None:  # pragma: no cover - us fora del context manager
            raise RuntimeError("ReplayClient s'ha d'utilitzar com a context manager.")
        if url == METADATA_URL:
            if self._metadata_served >= len(self._metadata):
                raise ReplayError("La captura nomes te les metadades d'inici i de final.")
            origin, body = self._metadata[self._metadata_served]
            self._metadata_served += 1
            return self._served(url, purpose, body, (origin,))
        if not url.startswith(RESOURCE_URL + "?"):
            raise ReplayError(f"Consulta no capturada: {url[:200]}")
        wanted, select = _requested_uuids(url)
        if not wanted:
            raise ReplayError("Consulta sense UUID: no es pot rejugar.")
        missing = sorted(wanted - self._requested)
        if missing:
            raise ReplayError(
                f"{len(missing)} UUID que el run original no va demanar (p. ex. {missing[0]}):"
                " no es pot rejugar el que no es va capturar."
            )
        if select != self._select:
            raise ReplayError("El $select no es el de la captura.")
        params = httpx.URL(url).params
        offset = int(params.get("$offset") or 0)
        limit = int(params.get("$limit") or 1000)
        items = sorted(
            (item for uid in wanted for item in self._rows.get(uid, [])),
            key=lambda item: _row_sort_key(item[0]),
        )[offset : offset + limit]
        body = json.dumps([row for row, _ in items], ensure_ascii=False).encode("utf-8")
        return self._served(url, purpose, body, tuple(origin for _, origin in items))

    def _served(
        self, url: str, purpose: str, body: bytes, origins: tuple[dict[str, Any], ...]
    ) -> Fetched:
        self.replayed += 1
        retrieved = [datetime.fromisoformat(str(o["retrieved_at"])) for o in origins]
        retrieved_at = min(retrieved) if retrieved else datetime.now(UTC)
        self.log.append(
            {
                "n": self.replayed,
                "method": "REPLAY",
                "network": False,
                "purpose": purpose,
                "url": url,
                "rows_or_documents": len(origins),
                "origins": sorted({o["key"] for o in origins}),
            }
        )
        return Fetched(
            url=url,
            status=200,
            body=body,
            sha256=_sha256(body),
            retrieved_at=retrieved_at,
            attempts=0,
            origins=origins,
        )
