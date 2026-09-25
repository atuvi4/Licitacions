"""Es un criteri d'adjudicacio FORMAL, o nomes una cosa que el plec diu que valorara?

**Proposta, no connectada.** Aquest modul encara no el crida la persistencia ni
la interficie: existeix per poder mesurar la regla sobre runs reals abans de
decidir si s'aplica i com.

El problema (Palamos, run `a18fa4a6`): el PPT diu "Es valorara que els telefons
incorporin mecanismes d'eficiencia energetica" i el model ho va desar com a
`AwardCriterion`. La cita es literal, pero el sistema d'adjudicacio del plec no
dona cap punt per aixo. "El plec diu que es valorara" no vol dir "es un criteri
d'adjudicacio amb puntuacio".

La regla fa servir nomes SENYALS ESTRUCTURALS, cap llista de paraules:

* **puntuacio**: `points` o `weight_percent`;
* **formula**: `formula` o `formula_text`;
* **font**: alguna cita surt del plec de clausules administratives, que es
  on la llei situa els criteris d'adjudicacio. Aixo accepta les 8 caselles
  SI/NO del PCAP encara que la pagina del model d'oferta no en porti els punts.

Si no n'hi ha cap, el criteri es `unconfirmed`: no es descarta ni es perd la
cita, pero no s'ha de presentar com a criteri formal sense que una persona ho
revisi (o que un altre fragment del plec el confirmi).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.models.enums import TenderDocumentType

FORMAL = "formal"
UNCONFIRMED = "unconfirmed"

#: On la llei situa els criteris d'adjudicacio (art. 122 i 145 LCSP): el PCAP.
FORMAL_SOURCE_TYPES = frozenset({TenderDocumentType.ADMINISTRATIVE_TERMS})


@dataclass(frozen=True, slots=True)
class AwardFormality:
    status: str
    #: Quins senyals ho sostenen. Buit si `unconfirmed`.
    signals: tuple[str, ...]

    @property
    def is_formal(self) -> bool:
        return self.status == FORMAL


def award_formality(
    *,
    points: float | None,
    weight_percent: float | None,
    formula: str | None,
    formula_text: str | None = None,
    evidence_document_types: Iterable[str | TenderDocumentType | None] = (),
) -> AwardFormality:
    """Classifica un criteri extret. Pura: no llegeix ni escriu res."""
    signals: list[str] = []
    if points is not None or weight_percent is not None:
        signals.append("scoring")
    if (formula and formula.strip()) or (formula_text and formula_text.strip()):
        signals.append("formula")
    types = {_as_type(value) for value in evidence_document_types}
    if types & FORMAL_SOURCE_TYPES:
        signals.append("administrative_terms_source")
    return AwardFormality(status=FORMAL if signals else UNCONFIRMED, signals=tuple(signals))


def _as_type(value: str | TenderDocumentType | None) -> TenderDocumentType | None:
    try:
        return TenderDocumentType(value) if value is not None else None
    except ValueError:
        return None
