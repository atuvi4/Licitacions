"""Extraction Contract v3: el disseny, en codi provable. **No connectat.**

Cap perfil (`app/ai/profiles.py`), ni la persistencia, ni la interficie no fan
servir aquest modul. Existeix per fixar el contracte amb tests abans de crear
`requirements-v3` (prompt, esquema i extractor nous: v1 no s'edita).

Dues coses que v1 no pot dir, i que els runs reals de Palamos han fet veure:

1. **Una cita valida no prova cada valor.** El requisit `33a917b0` diu
   `fibres_FTTH_600Mbps = 11`, pero la seva unica cita nomes parla de GPON.
   Aqui cada cita declara QUINS valors sosté (`supports`), i el backend ho
   comprova: `claim_support`.
2. **"Es valorara" no fa un criteri d'adjudicacio.** El PPT de Palamos diu que
   es valorara l'eficiencia energetica dels telefons; el model ho va desar com a
   criteri. Aqui el model ha de dir QUIN mecanisme d'adjudicacio el fa formal i
   citar-lo: `classify_award`. Sense mecanisme provat, el criteri va a revisio.
   Mai no es descarta.

Nivells d'evidencia:

* `QUOTE_VALID`: la cita existeix literalment a la pagina (ja existeix a v1:
  qualsevol `RequirementEvidence` desada ha passat `locate_quote`).
* `CLAIM_SUPPORTED`: una cita valida declara i conte un valor concret.
* `REQUIREMENT_SUPPORTED`: tots els valors materials del requisit ho son.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import Field

from app.ai.schemas import EvidenceOut, StrictModel

# --------------------------------------------------------------------------- #
# Claim / evidence                                                             #
# --------------------------------------------------------------------------- #


class EvidenceOutV3(EvidenceOut):
    """Una cita v3: a mes d'on es, diu quins valors del requisit sosté.

    `supports` son claus de `value` (o `adjudication_basis` en un criteri). Una
    cita pot sostenir-ne diverses; un valor pot tenir diverses cites.
    """

    supports: list[str] = Field(default_factory=list, max_length=20)


class ClaimStatus(StrEnum):
    #: Una cita valida el declara i, si es un numero, el numero hi es.
    SUPPORTED = "supported"
    #: Una cita el declara, pero el numero no hi apareix.
    DECLARED_NOT_FOUND = "declared_not_found"
    #: Cap cita no el declara.
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class ClaimSupport:
    per_claim: dict[str, ClaimStatus]

    @property
    def requirement_supported(self) -> bool:
        """REQUIREMENT_SUPPORTED: tots els valors, sostinguts. Sense valors, cert."""
        return all(status == ClaimStatus.SUPPORTED for status in self.per_claim.values())

    @property
    def unsupported(self) -> list[str]:
        return [k for k, status in self.per_claim.items() if status != ClaimStatus.SUPPORTED]


@dataclass(frozen=True, slots=True)
class CitedQuote:
    """Una cita JA validada literalment (QUOTE_VALID) i el que declara sostenir."""

    quote: str
    supports: tuple[str, ...]


_NUMBER = re.compile(r"\d[\d.,]*")


def _numbers(text: str) -> set[float]:
    found: set[float] = set()
    for raw in _NUMBER.findall(text or ""):
        raw = raw.rstrip(".,")
        if "," in raw:  # 155.239,16
            raw = raw.replace(".", "").replace(",", ".")
        elif "." in raw and all(len(part) == 3 for part in raw.split(".")[1:]):
            raw = raw.replace(".", "")  # 500.000
        try:
            found.add(float(raw))
        except ValueError:
            continue
    return found


def claim_support(value: Mapping[str, Any] | None, quotes: Iterable[CitedQuote]) -> ClaimSupport:
    """Per a cada clau de `value`, si alguna cita valida la sosté.

    Els numeros (`11`, `"155.239,16 €"`) s'han de trobar al text de la cita que
    els declara. Un text no numeric nomes s'ha de declarar: aqui no es fa cap
    comparacio semantica, i per aixo `supports` es obligatori a v3.
    """
    quotes = list(quotes)
    per_claim: dict[str, ClaimStatus] = {}
    for key, claim in (value or {}).items():
        declaring = [q for q in quotes if key in q.supports]
        if not declaring:
            per_claim[key] = ClaimStatus.UNSUPPORTED
            continue
        wanted = _numbers(str(claim)) if not isinstance(claim, bool) else set()
        if isinstance(claim, int | float) and not isinstance(claim, bool):
            wanted = {float(claim)}
        if not wanted or any(wanted <= _numbers(q.quote) for q in declaring):
            per_claim[key] = ClaimStatus.SUPPORTED
        else:
            per_claim[key] = ClaimStatus.DECLARED_NOT_FOUND
    return ClaimSupport(per_claim=per_claim)


# --------------------------------------------------------------------------- #
# Award criteria                                                               #
# --------------------------------------------------------------------------- #


class AwardCandidateKind(StrEnum):
    """El que el MODEL diu que es. El backend decideix la classe final."""

    AWARD_CRITERION = "award_criterion"
    DESIRABLE_FEATURE = "desirable_feature"
    INFORMATIVE = "informative"


class AdjudicationBasis(StrEnum):
    """El mecanisme que fa que un criteri participi a l'adjudicacio."""

    POINTS = "points"
    WEIGHT = "weight"
    FORMULA = "formula"
    CRITERIA_TABLE = "criteria_table"
    OFFER_FORM = "offer_form"  # model d'oferta del Sobre 2 / caselles
    CRITERIA_SECTION = "criteria_section"  # clausula formal de criteris


class AwardCandidateOutV3(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    kind: AwardCandidateKind
    adjudication_basis: list[AdjudicationBasis] = Field(default_factory=list)
    points: float | None = None
    weight_percent: float | None = None
    formula: str | None = None
    evidence: list[EvidenceOutV3] = Field(default_factory=list)


class AwardClass(StrEnum):
    FORMAL_AWARD_CRITERION = "formal_award_criterion"
    #: El model diu que es criteri, pero no ha provat cap mecanisme: a revisio.
    CANDIDATE_AWARD_CRITERION = "candidate_award_criterion"
    TECHNICAL_DESIRABLE = "technical_desirable"
    INFORMATIVE = "informative"


BASIS_KEY = "adjudication_basis"


def classify_award(
    candidate: AwardCandidateOutV3, *, valid_quotes: Iterable[CitedQuote]
) -> AwardClass:
    """Classe final d'un candidat. Mai no el descarta: com a molt, a revisio.

    Formal nomes si hi ha un mecanisme d'adjudicacio PROVAT: punts, pes o
    formula presents, o un `adjudication_basis` declarat i sostingut per una
    cita valida (`supports` hi inclou `adjudication_basis`). Aixo accepta les
    caselles SI/NO del model d'oferta sense punts a la mateixa pagina.
    """
    if candidate.kind == AwardCandidateKind.INFORMATIVE:
        return AwardClass.INFORMATIVE
    if candidate.kind == AwardCandidateKind.DESIRABLE_FEATURE:
        return AwardClass.TECHNICAL_DESIRABLE
    has_scoring = (
        candidate.points is not None
        or candidate.weight_percent is not None
        or bool(candidate.formula and candidate.formula.strip())
    )
    basis_cited = bool(candidate.adjudication_basis) and any(
        BASIS_KEY in quote.supports for quote in valid_quotes
    )
    if has_scoring or basis_cited:
        return AwardClass.FORMAL_AWARD_CRITERION
    return AwardClass.CANDIDATE_AWARD_CRITERION
