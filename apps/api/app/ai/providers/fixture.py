"""Proveidor de SIMULACIO: el plec de `fixture_corpus`, i nomes aquell.

Existeix per un motiu concret i acotat: **poder recorrer el flux sencer**
—requisits, compliment, dossier, readiness i paquet— en desenvolupament, sense
gastar API ni dependre de la xarxa. Sense requisits no hi ha dossier, sense
dossier no hi ha readiness i sense readiness no es pot congelar cap paquet: la
meitat del producte era inauditable.

**No es una analisi**, i el sistema ho diu a tot arreu: el seu `name` es
"fixture", `AnalysisBasis.SIMULATION` viatja amb el run fins a la interficie, i
un paquet sostingut per un run aixi **no pot rebre l'aprovacio final**.

**Cada requisit va lligat a una frase concreta del corpus.** La primera versio
d'aquest proveidor agafava qualsevol frase prou llarga del document que li
toques i la feia servir com a cita: passava la validacio d'evidencia —la frase
hi era— pero la cita no tenia res a veure amb el requisit. Una simulacio pot
inventar-se el contingut; el que no pot es inventar-se la relacio entre una
exigencia i el text que la sosté, perque llavors el que es prova ja no es el
producte.

Per aixo aqui nomes hi ha `str.find`: cap coincidencia difusa, cap model, cap
heuristica. La frase hi es tal qual o aquell requisit no existeix. I si no n'hi
ha cap, es que no s'esta executant sobre el corpus i el proveidor es nega a
respondre en comptes de fabricar-se una analisi sobre un document qualsevol.
"""

from __future__ import annotations

import re
from typing import Any

from app.ai.provider import ProviderError, StructuredResult, Usage
from app.ai.providers.fixture_corpus import (
    AWARD_CRITERIA,
    CORPUS_ID,
    REQUIREMENTS,
    Anchored,
)

#: Nom que es desa a cada Run. `AnalysisBasis` el llegeix per saber que aixo no
#: es una lectura del plec.
PROVIDER_NAME = "fixture"
MODEL_NAME = "fixture-plec-v1"

#: Codi de l'error quan el document no es el corpus. No es una fallada del
#: proveidor: es una negativa deliberada.
CORPUS_MISSING = "fixture_corpus_not_found"

_PAGE = re.compile(r'<PAGE number="(\d+)">\n(.*?)\n</PAGE>', re.S)
_EXTRACTION_ID = re.compile(r'extraction_id="([^"]+)"')


class FixtureCorpusMissingError(ProviderError):
    """S'ha demanat la simulacio sobre un document que no es el corpus.

    Es un error de domini i no una avaria: el proveidor sap respondre una cosa i
    una sola, i qualsevol altra entrada l'ha de fer callar. Inventar-se requisits
    sobre un plec real seria exactament el que aquest modul existeix per evitar.
    """

    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            message
            or (
                "La simulacio nomes sap respondre sobre el plec de desenvolupament "
                f"'{CORPUS_ID}'. Aquest document no el conte."
            ),
            CORPUS_MISSING,
        )


def _normalise(text: str) -> str:
    """Espais col·lapsats: un salt de linia del PDF no ha de trencar la cita.

    L'extractor parteix les linies on el PDF les parteix, i el corpus les escriu
    on toca gramaticalment. Comparar amb els espais normalitzats es l'unica
    llibertat que es pren aquest modul, i no permet cap coincidencia parcial:
    la frase segueix havent-hi de ser sencera.
    """
    return re.sub(r"\s+", " ", text).strip()


def _locate(anchor: Anchored, pages: list[tuple[int, str]]) -> tuple[int, str] | None:
    """La pagina i el text literal on hi ha la frase, o `None` si no hi es.

    Es torna el fragment **tal com apareix a la pagina** —amb els seus salts de
    linia— i no el del corpus: el que el backend validara despres es el text
    real de la pagina.
    """
    needle = _normalise(anchor.quote)
    for number, text in pages:
        flat = _normalise(text)
        start = flat.find(needle)
        if start < 0:
            continue
        return number, _exact_span(text, needle)
    return None


def _exact_span(page_text: str, needle: str) -> str:
    """El tros de la pagina que correspon a la frase normalitzada.

    Es recorre la pagina acumulant caracters i comparant la seva forma
    normalitzada: aixi la cita que es retorna es literal del document, salts de
    linia inclosos, i el validador d'evidencia la trobara igual.
    """
    words = needle.split(" ")
    pattern = r"\s+".join(re.escape(word) for word in words)
    match = re.search(pattern, page_text)
    return match.group(0) if match else needle


class FixtureProvider:
    """Compleix `StructuredLLMProvider` amb el plec simulat i estable."""

    name = PROVIDER_NAME

    def __init__(self, model: str = MODEL_NAME) -> None:
        self._model = model
        #: Nomes el primer chunk amb ancoratges aporta requisits. Repetir-los a
        #: cada chunk nomes exercitaria la deduplicacio.
        self._served = False

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        return True

    async def extract_structured(
        self,
        *,
        system: str,
        user_content: str,
        json_schema: dict[str, Any],
        max_output_tokens: int,
    ) -> StructuredResult:
        extraction = _EXTRACTION_ID.search(user_content)
        pages = [(int(number), text) for number, text in _PAGE.findall(user_content)]

        located_requirements = [
            (anchor, found)
            for anchor in REQUIREMENTS
            if (found := _locate(anchor, pages)) is not None
        ]
        located_criteria = [
            (anchor, found)
            for anchor in AWARD_CRITERIA
            if (found := _locate(anchor, pages)) is not None
        ]

        if not located_requirements and not located_criteria:
            if self._served:
                # Un chunk posterior sense cap frase es normal: el corpus ja s'ha
                # servit i la resta del document no en te cap.
                return StructuredResult(data=_empty(), usage=Usage(), model=self._model)
            raise FixtureCorpusMissingError()

        if extraction is None:  # pragma: no cover - el render sempre el posa
            raise FixtureCorpusMissingError(
                "El fragment no porta `extraction_id`: no s'hi pot ancorar cap cita."
            )

        if self._served:
            return StructuredResult(data=_empty(), usage=Usage(), model=self._model)
        self._served = True

        return StructuredResult(
            data=_response(extraction.group(1), located_requirements, located_criteria),
            usage=Usage(input_tokens=0, output_tokens=0),
            model=self._model,
        )


def _empty() -> dict[str, Any]:
    return {
        "requirements": [],
        "award_criteria": [],
        "document_notes": [],
        "uncertainties": [],
    }


def _evidence(extraction_id: str, found: tuple[int, str]) -> list[dict[str, Any]]:
    page_number, quote = found
    return [
        {
            "extraction_id": extraction_id,
            "page_number": page_number,
            "quote": quote,
            "section": None,
            "evidence_role": "primary",
        }
    ]


def _response(
    extraction_id: str,
    requirements: list[tuple[Anchored, tuple[int, str]]],
    criteria: list[tuple[Anchored, tuple[int, str]]],
) -> dict[str, Any]:
    return {
        "requirements": [
            {**anchor.payload, "evidence": _evidence(extraction_id, found)}
            for anchor, found in requirements
        ],
        "award_criteria": [
            {**anchor.payload, "evidence": _evidence(extraction_id, found)}
            for anchor, found in criteria
        ],
        "document_notes": [
            {
                "note": (
                    f"Contingut simulat sobre el corpus '{CORPUS_ID}'. No s'ha llegit "
                    "cap plec real: nomes serveix per provar el flux en "
                    "desenvolupament."
                ),
                "page_number": None,
            }
        ],
        "uncertainties": [],
    }
