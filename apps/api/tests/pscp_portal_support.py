"""El portal de la PSCP en memoria per als tests del connector `pscp_portal`.

Serveix les respostes enregistrades de `tests/fixtures/pscp_portal/<licitacio>/` (Pineda de
Mar, Parc Tauli i, per als avisos, Cornella) i, per a cada fitxer publicat, uns bytes
sintetics. Com que els bytes no son els reals, en carregar-les es reescriu l'MD5 de cada
document (`hash`) amb el dels bytes que se'n serviran: la descarrega es pot verificar igual
que contra el portal de debo. Cap peticio surt de la maquina.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx

from app.ingestion.pscp_portal import PORTAL_API
from app.ingestion.pscp_portal.parser import EMPTY_MD5, download_url
from tests.fixtures.pdf_builder import build_pdf

FIXTURES = Path(__file__).parent / "fixtures" / "pscp_portal"
PUBLIC_IP = "93.184.216.34"

PINEDA = "7224a2cc-30a6-4234-b023-66744c2ea6f3"
PINEDA_ORIGINAL = 300866869
PINEDA_CURRENT = 300907178
PARC_TAULI = "32b20054-4b92-4c38-a406-a087426de02a"
PARC_TAULI_CURRENT = 300899451
CORNELLA = "2c72bffe-9ba0-47ee-8687-57ffa72c8451"
CORNELLA_CURRENT = 300894911
#: Cornella: la versio original no es va desar; el fals la deriva de la vigent (vegeu `cornella`).
CORNELLA_ORIGINAL = 300880876

_ROUTES = (
    ("board", re.compile(r"^detall-publicacio-expedient/tauler-avisos/(?P<exp>[0-9a-f-]{36})$")),
    (
        "publication",
        re.compile(r"^detall-publicacio-expedient/(?P<exp>[0-9a-f-]{36})/(?P<id>\d+)$"),
    ),
    ("answers", re.compile(r"^respostes/(?P<exp>[0-9a-f-]{36})$")),
    ("answer", re.compile(r"^detall-avis/resposta/(?P<id>\d+)$")),
    ("notice", re.compile(r"^detall-avis/avis-recurs/(?P<id>\d+)$")),
    ("notices", re.compile(r"^avisos-tauler/(?P<cat>\d+)/(?P<exp>[0-9a-f-]{36})$")),
    ("download", re.compile(r"^descarrega-document/(?P<id>\d+)/(?P<md5>[0-9A-Fa-f]{32})$")),
)


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def file_bytes(document_id: int, title: str) -> bytes:
    """Un PDF petit i estable per a cada document (text propi, per poder-lo llegir)."""
    return build_pdf([f"Document {document_id} del portal de prova: {title}. " * 6])


class FakePortal:
    """Respostes del portal per cami, fitxers per id i el registre de les peticions."""

    def __init__(self) -> None:
        self.json: dict[str, Any] = {}
        self.files: dict[int, bytes] = {}
        self._by_md5: dict[str, bytes] = {}
        self.calls: list[str] = []
        #: Respostes forcades per cami (`status`, o una sequencia per intent).
        self.status: dict[str, list[int]] = {}
        #: Tipus que es declara per a cada fitxer (per defecte, PDF).
        self.content_types: dict[int, str] = {}
        #: Respostes JSON per a les properes peticions d'una clau, en ordre (despres, la fixa).
        self.next_json: dict[str, list[Any]] = {}

    # ---------------------------------------------------------------- carrega
    def load(self, folder: str) -> FakePortal:
        for path in sorted((FIXTURES / folder).glob("*.json")):
            self._register(path.stem, json.loads(path.read_text(encoding="utf-8")))
        return self

    def _register(self, stem: str, data: Any) -> None:
        self._rehash(data)
        expedient = _expedient_of(data)
        if stem.startswith("publicacio_"):
            key = f"detall-publicacio-expedient/{expedient}/{stem.split('_', 1)[1]}"
        elif stem == "tauler":
            key = "board"
        elif stem.startswith("respostes_p"):
            key = f"answers:{stem.removeprefix('respostes_p')}"
        elif stem.startswith("resposta_"):
            key = f"detall-avis/resposta/{stem.split('_', 1)[1]}"
        elif stem.startswith("avis_"):
            key = f"detall-avis/avis-recurs/{stem.split('_', 1)[1]}"
        elif stem.startswith("avisos_"):
            _, category, page = stem.split("_")
            key = f"notices:{category}:{page.removeprefix('p')}"
        else:  # pragma: no cover - fitxer inesperat
            raise AssertionError(stem)
        self.json[key] = data

    def _rehash(self, node: Any) -> None:
        """MD5 de cada document = el dels bytes que el fals en servira.

        Els bytes sintetics van per l'MD5 real: dos identificadors amb el mateix contingut
        publicat (una esmena que torna a publicar el mateix fitxer) tenen els mateixos bytes
        de prova. Un fitxer que el portal publica buit (Pineda: «Memoria castellano.pdf») es
        continua servint buit.
        """
        if isinstance(node, dict):
            if {"id", "hash", "titol"} <= node.keys() and isinstance(node.get("id"), int):
                real = str(node["hash"]).upper()
                if node["id"] not in self.files:
                    if real not in self._by_md5:
                        empty = real == EMPTY_MD5 or node.get("mida") == 0
                        self._by_md5[real] = b"" if empty else file_bytes(node["id"], node["titol"])
                    self.files[node["id"]] = self._by_md5[real]
                data = self.files[node["id"]]
                node["hash"] = hashlib.md5(data).hexdigest().upper()
                node["mida"] = len(data)
            for value in node.values():
                self._rehash(value)
        elif isinstance(node, list):
            for item in node:
                self._rehash(item)

    # ------------------------------------------------------------- consultes
    def publication(self, expedient: str, publication_id: int) -> dict[str, Any]:
        return self.json[f"detall-publicacio-expedient/{expedient}/{publication_id}"]

    def add_publication(self, data: dict[str, Any]) -> None:
        data = copy.deepcopy(data)
        self._rehash(data)
        self.json[f"detall-publicacio-expedient/{data['expedientId']}/{data['publicacioId']}"] = (
            data
        )

    def replace_file(self, document_id: int, data: bytes, content_type: str | None = None) -> None:
        """Uns altres bytes per a un fitxer, amb l'MD5 publicat al dia a totes les respostes."""
        self.files[document_id] = data
        if content_type:
            self.content_types[document_id] = content_type
        md5 = hashlib.md5(data).hexdigest().upper()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("id") == document_id and {"hash", "titol"} <= node.keys():
                    node["hash"], node["mida"] = md5, len(data)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(self.json)

    def url(self, document_id: int) -> str:
        """La URL de descarrega d'un document tal com la publica el portal (i el feed)."""
        md5 = hashlib.md5(self.files[document_id]).hexdigest().upper()
        return download_url(document_id, md5)

    def downloads(self) -> list[str]:
        return [call for call in self.calls if call.startswith("descarrega-document/")]

    def downloads_of(self, document_id: int) -> list[str]:
        return [call for call in self.downloads() if call.split("/")[1] == str(document_id)]

    def json_calls(self) -> list[str]:
        return [call for call in self.calls if not call.startswith("descarrega-document/")]

    # --------------------------------------------------------------- servidor
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert url.startswith(PORTAL_API + "/"), url
        path = request.url.path.removeprefix("/portal-api/")
        self.calls.append(path)
        forced = self.status.get(path)
        if forced:
            code = forced.pop(0) if len(forced) > 1 else forced[0]
            if code != 200:
                return httpx.Response(code, json={"error": "forcat"})
        page = request.url.params.get("page", "0")
        for name, pattern in _ROUTES:
            match = pattern.match(path)
            if match is None:
                continue
            if name == "download":
                data = self.files.get(int(match.group("id")))
                if data is None:
                    return httpx.Response(404)
                content_type = self.content_types.get(int(match.group("id")), "application/pdf")
                return httpx.Response(200, content=data, headers={"content-type": content_type})
            key = {
                "board": "board",
                "answers": f"answers:{page}",
                "notices": f"notices:{match.groupdict().get('cat')}:{page}",
            }.get(name, path)
            if self.next_json.get(key):
                return httpx.Response(200, json=self.next_json[key].pop(0))
            if key not in self.json:
                return httpx.Response(404, json={"error": "no trobat"})
            return httpx.Response(200, json=self.json[key])
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), follow_redirects=False
        )


def _expedient_of(data: Any) -> str | None:
    if isinstance(data, dict):
        if isinstance(data.get("expedientId"), str):
            return data["expedientId"]
        for item in data.get("content") or []:
            if isinstance(item, dict) and isinstance(item.get("expedientId"), str):
                return item["expedientId"]
    return None


def pineda() -> FakePortal:
    return FakePortal().load("pineda")


def parc_tauli() -> FakePortal:
    return FakePortal().load("parc_tauli")


def cornella() -> FakePortal:
    """Cornella amb una versio original sintetica (la vigent sense esmena): els avisos del
    tauler i els detalls de les respostes son els reals (amb el text substituit)."""
    fake = FakePortal().load("cornella")
    current = fake.publication(CORNELLA, CORNELLA_CURRENT)
    original = copy.deepcopy(current)
    original["publicacioId"] = CORNELLA_ORIGINAL
    original["motiuEsmena"] = None
    fake.add_publication(original)
    return fake


def source_url(expedient: str, publication_id: int) -> str:
    return f"https://contractaciopublica.cat/ca/detall-publicacio/{expedient}/{publication_id}"
