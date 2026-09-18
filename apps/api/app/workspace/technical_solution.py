"""Que s'ha validat d'una solucio tecnica, i si aquella validacio encara val.

Modul pur: no hi entra SQLAlchemy, ni FastAPI, ni cap sessio. La mateixa
separacio que `app.workspace.relevance` i `app.workspace.dossier`, i pel mateix
motiu: aixo s'ha de poder provar sol.

Dues idees, i cap de les dues es "l'estat".

**L'empremta del contingut** cobreix NOMES el que una persona te davant quan
signa: el titol, la proposta, el proveidor i el producte. Ni les notes, ni el
responsable, ni l'estat, ni els actors, ni les dates. Si hi entressin, canviar
de responsable invalidaria una validacio que ningu no ha contradit.

**La vigencia** no s'escriu enlloc: es deriva en llegir. Una validacio no
s'esborra mai —es historia, amb qui la va fer i per que—, aixi que la pregunta
util no es "segueix validada?" sino "segueix valent el que es va validar?".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime

#: El contingut signat ja no es el que hi ha.
CONTENT_CHANGED = "content_changed"
#: El requisit segueix existint, pero demana una altra cosa.
REQUIREMENT_CHANGED = "requirement_changed"
#: La fila deia que resolia un requisit i aquell requisit ja no es resoluble.
REQUIREMENT_MISSING = "requirement_missing"

#: Les files que no venen d'un requisit no poden caducar per un requisit.
_FROM_REQUIREMENT = "requirement"


def _text(value: str | None) -> str:
    """`None`, `""` i `"   "` son el mateix fet: el camp no diu res.

    Distingir-los faria que netejar un camp de dues maneres equivalents dones
    dues empremtes diferents, i una de les dues caducaria una validacio sense
    que hagues canviat res que una persona pugui veure.
    """
    return (value or "").strip()


def content_fingerprint(
    *,
    title: str | None,
    proposed_solution: str | None,
    supplier_name: str | None,
    product_name: str | None,
) -> str:
    """Identitat material de la proposta. SHA-256 en hexadecimal, 64 caracters.

    Es serialitza com un objecte amb claus, no com una concatenacio: aixi moure
    text d'un camp a un altre canvia l'empremta, que es el que ha de passar.
    Mateix calcul i mateixa amplada que `requirement_fingerprint()`.
    """
    payload = json.dumps(
        {
            "title": _text(title),
            "proposed_solution": _text(proposed_solution),
            "supplier_name": _text(supplier_name),
            "product_name": _text(product_name),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StaleInput:
    """Tot el que cal per decidir la vigencia, ja resolt contra la base de dades."""

    #: Sense data no hi ha validacio, i sense validacio no hi ha res a caducar.
    validated_at: datetime | None
    #: `requirement` o `manual`. Un `str` i no l'enum: aixo no ha de dependre dels models.
    source: str
    current_content_fingerprint: str
    validated_content_fingerprint: str | None
    #: Cert quan la fila segueix lligada a un requisit que existeix.
    requirement_resolvable: bool
    current_requirement_fingerprint: str | None
    validated_requirement_fingerprint: str | None


def validation_stale_reasons(data: StaleInput) -> list[str]:
    """Per que la validacio ja no val. Llista buida vol dir que val.

    L'ordre es fix perque dues lectures iguals no donin dues respostes diferents.
    """
    if data.validated_at is None:
        return []

    reasons: list[str] = []
    if data.current_content_fingerprint != data.validated_content_fingerprint:
        reasons.append(CONTENT_CHANGED)

    if data.source != _FROM_REQUIREMENT:
        return reasons

    if not data.requirement_resolvable:
        # Sense requisit no hi ha res amb que comparar: dir tambe que ha canviat
        # seria soroll sobre el mateix fet.
        reasons.append(REQUIREMENT_MISSING)
    elif data.current_requirement_fingerprint != data.validated_requirement_fingerprint:
        reasons.append(REQUIREMENT_CHANGED)
    return reasons
