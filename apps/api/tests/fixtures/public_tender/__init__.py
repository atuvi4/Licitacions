"""Fixture d'un tender PUBLIC sintetic amb dos plecs (3B-2 §10, Task 11).

Cap dada real: l'expedient, l'organ, les URL (`*.example.test`) i el text dels
documents son inventats (vegeu `README.md`). Els PDF es generen aqui mateix a
partir dels `.txt` amb `pdf_builder`, de manera determinista: el SHA-256 de cada
document es estable entre execucions i maquines.

El client de descarrega es un `MockTransport` que nomes coneix les dues URL de
la fixture: qualsevol altra URL respon 404, i cap peticio surt mai a la xarxa.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

import httpx

from tests.fixtures.pdf_builder import build_pdf

ROOT = Path(__file__).resolve().parent
#: L'Atom de l'unica entrada, per a `PlacspIngestionService.run(fixture=FEED, ...)`.
FEED = ROOT / "feed.atom"
#: La font publica del feed agregat: `owner_company_id` queda NULL.
SOURCE_CODE = "placsp_agregadas"

EXTERNAL_ID = "https://feed.example.test/sindicacion/PlataformasAgregadas/90000001"
EXPEDIENTE = "EXP-2026/0042"
BUYER_NAME = "Ajuntament de Vilaquimera"
PCAP_URL = "https://docs.example.test/public-tender/EXP-2026-0042/PCAP.pdf"
PPT_URL = "https://docs.example.test/public-tender/EXP-2026-0042/PPT.pdf"

#: Adreca publica a la qual «resolen» els hosts de la fixture (la mateixa que la
#: resta de tests de descarrega): la guarda SSRF la deixa passar sense DNS.
PUBLIC_IP = "93.184.216.34"

_PAGES = {
    PCAP_URL: ("pcap-p1.txt", "pcap-p2.txt"),
    PPT_URL: ("ppt-p1.txt", "ppt-p2.txt"),
}


def pages_of(url: str) -> list[str]:
    """El text de cada pagina del document, tal com es al `.txt`."""
    return [(ROOT / name).read_text(encoding="utf-8").strip() for name in _PAGES[url]]


@cache
def pdf_of(url: str) -> bytes:
    return build_pdf(pages_of(url))


def documents() -> dict[str, bytes]:
    """URL -> bytes del PDF, per al client fals de descarrega."""
    return {url: pdf_of(url) for url in _PAGES}


def sha256_of(url: str) -> str:
    return hashlib.sha256(pdf_of(url)).hexdigest()


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def serving() -> httpx.AsyncClient:
    """Client fals que serveix NOMES els dos PDF de la fixture (404 per a la resta)."""
    served = documents()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = served.get(str(request.url))
        if payload is None:
            return httpx.Response(404)
        return httpx.Response(200, content=payload, headers={"content-type": "application/pdf"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
