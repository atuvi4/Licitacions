"""Mateix requisit, diverses files d'extracció: com es diu, sense endevinar.

Dues regles, NOMÉS a partir de l'evidència desada (mai del títol):

* **Duplicat d'extracció** (`duplicate_of`): les dues files citen el MATEIX
  tros de la MATEIXA pàgina (els trossos es trepitgen). Passa quan dos chunks
  comparteixen una pàgina i el model extreu la frase dues vegades.
* **Reiteració del plec** (`restatement_of`): el text citat és el mateix
  (normalitzat: espais i cometes tipogràfiques) però en una altra pàgina o un
  altre document. El plec ho diu dues vegades de debò.
* Qualsevol altra cosa és **diferent**: no es relaciona.

El canònic (on va la correcció) es tria amb un ordre fix i documentat: primer
la clàusula del document vinculant (PCAP, PPT), després la cita més completa,
després la que té secció, i només al final l'ordre de lectura (chunk, pàgina).
Mai per l'ordre dels identificadors.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.models.enums import TenderDocumentType

#: Documents que obliguen (els plecs: PCAP i PPT). Un informe o un annex els pot repetir,
#: no substituir. Surt de l'enum: un nom escrit a mà ja va fallar una vegada.
BINDING_DOCUMENT_TYPES = frozenset(
    {TenderDocumentType.ADMINISTRATIVE_TERMS.value, TenderDocumentType.TECHNICAL_TERMS.value}
)

_QUOTES = str.maketrans({"’": "'", "‘": "'", "´": "'", "“": '"', "”": '"', "«": '"', "»": '"'})


def normalise_quote(text: str) -> str:
    """Per comparar cites: espais col·lapsats i cometes tipogràfiques rectes. Res més."""
    return re.sub(r"\s+", " ", text.translate(_QUOTES)).strip()


@dataclass(frozen=True)
class EvidenceSpan:
    """Una fila de `requirement_evidence`, reduïda al que cal per comparar."""

    page_id: uuid.UUID
    document_type: str | None
    page_number: int
    char_start: int | None
    char_end: int | None
    section: str | None
    quote: str


@dataclass(frozen=True)
class Candidate:
    """Una fila extreta amb la seva evidència i on la va llegir la canonada."""

    item_id: uuid.UUID
    source_chunk_index: int | None
    evidence: tuple[EvidenceSpan, ...]


class PairKind(StrEnum):
    DUPLICATE = "duplicate"
    RESTATEMENT = "restatement"
    DISTINCT = "distinct"


def _overlap(a: EvidenceSpan, b: EvidenceSpan) -> bool:
    if a.page_id != b.page_id:
        return False
    if None in (a.char_start, a.char_end, b.char_start, b.char_end):
        # Sense posicions: el mateix text a la mateixa pàgina és el mateix tros.
        return normalise_quote(a.quote) == normalise_quote(b.quote)
    return a.char_start < b.char_end and b.char_start < a.char_end  # type: ignore[operator]


def classify(a: Candidate, b: Candidate) -> PairKind:
    """Duplicat si comparteixen un tros de pàgina; reiteració si diuen el mateix en un altre
    lloc."""
    if any(_overlap(x, y) for x in a.evidence for y in b.evidence):
        return PairKind.DUPLICATE
    quotes_a = {normalise_quote(e.quote) for e in a.evidence}
    if any(normalise_quote(e.quote) in quotes_a for e in b.evidence):
        return PairKind.RESTATEMENT
    return PairKind.DISTINCT


def _rank(candidate: Candidate) -> tuple:
    binding = any(e.document_type in BINDING_DOCUMENT_TYPES for e in candidate.evidence)
    longest = max((len(normalise_quote(e.quote)) for e in candidate.evidence), default=0)
    sectioned = any(e.section for e in candidate.evidence)
    first_page = min((e.page_number for e in candidate.evidence), default=10**6)
    chunk = candidate.source_chunk_index if candidate.source_chunk_index is not None else 10**6
    # Menor és millor. L'id només desempata si TOT el que diu l'evidència és igual.
    return (not binding, -longest, not sectioned, chunk, first_page, str(candidate.item_id))


def canonical(candidates: Iterable[Candidate]) -> Candidate:
    """El que rep la correcció: document vinculant > cita completa > secció > ordre de lectura."""
    return min(candidates, key=_rank)


@dataclass(frozen=True)
class PlannedRelation:
    from_item: uuid.UUID
    to_item: uuid.UUID
    relation_type: str  # `duplicate_of` o `restatement_of`


def plan_relations(candidates: Sequence[Candidate]) -> tuple[Candidate, list[PlannedRelation]]:
    """El canònic i les relacions mínimes perquè tota la resta hi arribi.

    Cada grup de duplicats d'extracció es plega en UN representant (el millor del
    grup, amb el mateix ordre que el canònic). Cada representant que no sigui el
    canònic és `restatement_of` del canònic, si el text ho prova; si no, no es
    relaciona (queda diferent).
    """
    items = list(candidates)
    groups: list[list[Candidate]] = []
    for candidate in sorted(items, key=_rank):
        for group in groups:
            if classify(group[0], candidate) is PairKind.DUPLICATE:
                group.append(candidate)
                break
        else:
            groups.append([candidate])
    chosen = canonical(items)
    relations: list[PlannedRelation] = []
    for group in groups:
        head = group[0]
        relations += [
            PlannedRelation(other.item_id, head.item_id, "duplicate_of") for other in group[1:]
        ]
        if head.item_id != chosen.item_id and classify(head, chosen) is PairKind.RESTATEMENT:
            relations.append(PlannedRelation(head.item_id, chosen.item_id, "restatement_of"))
    return chosen, relations
