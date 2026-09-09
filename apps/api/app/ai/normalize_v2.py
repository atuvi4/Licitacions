"""De la sortida del model als camps que es desen. **Deterministic i prudent.**

Dues feines, totes dues sense tocar base de dades:

* **Derivar l'obligatorietat a partir de la modalitat.** El model pot dir alhora
  `modality: alternative` i `obligation: mandatory` —li passa quan el paragraf
  barreja les dues idees—. Aqui es resol sempre cap al costat segur: una via
  alternativa o una condicio NO son una exigencia, i marcar-les com a tal
  fabricaria un bloqueig fals a 4C.
* **Donar una clau estable al grup d'alternatives**, perque "A o B" es puguin
  reconeixer com a dues opcions de la mateixa disjuncio i no com dos requisits.

Res d'aqui interpreta el contingut: nomes tradueix formes.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.ai.value_schemas import VALUE_SCHEMA_VERSION, dump_value, parse_value
from app.models.enums import (
    RequirementModality,
    RequirementObligation,
)

#: Modalitats que MAI poden acabar sent `mandatory`.
NEVER_MANDATORY = frozenset(
    {
        RequirementModality.ALTERNATIVE,
        RequirementModality.CONDITIONAL,
        RequirementModality.OPTIONAL,
        RequirementModality.INFORMATIONAL,
    }
)


def derive_obligation(
    declared: RequirementObligation, modality: RequirementModality
) -> RequirementObligation:
    """L'obligatorietat que es desa, resolta cap al costat segur.

    La taula, i el per que de cada fila:

    * `alternative` / `conditional` + `mandatory` -> `unknown`. Una de dues vies
      d'acreditacio no es una exigencia; una condicio que potser no s'activa,
      tampoc. `unknown` demana mirar-ho; `mandatory` produiria un blocador.
    * `optional` + `mandatory` -> `scored`. "Es valorara" no obliga.
    * `informational` + `mandatory` -> `informative`.
    * La resta es respecta tal com ve: si el model NO ha dit `mandatory`, aqui no
      s'hi puja mai.
    """
    if declared != RequirementObligation.MANDATORY:
        return declared
    if modality == RequirementModality.OPTIONAL:
        return RequirementObligation.SCORED
    if modality == RequirementModality.INFORMATIONAL:
        return RequirementObligation.INFORMATIVE
    if modality in NEVER_MANDATORY:
        return RequirementObligation.UNKNOWN
    return RequirementObligation.MANDATORY


_SLUG = re.compile(r"[^a-z0-9]+")


def alternative_group_key(raw: str | None, *, run_id: Any, chunk_index: int) -> str | None:
    """Clau estable del grup d'alternatives.

    L'etiqueta que dona el model es LOCAL al chunk i pot repetir-se entre chunks
    diferents volent dir coses diferents. S'hi barreja el run i el chunk perque
    dues disjuncions independents no acabin al mateix grup.
    """
    if not raw or not raw.strip():
        return None
    slug = _SLUG.sub("-", raw.strip().casefold()).strip("-")[:40] or "grup"
    digest = hashlib.sha256(f"{run_id}:{chunk_index}:{slug}".encode()).hexdigest()[:16]
    return f"{slug}-{digest}"[:80]


@dataclass(frozen=True, slots=True)
class NormalisedRequirement:
    """Els camps ja resolts, a punt per persistir."""

    obligation: RequirementObligation
    modality: RequirementModality
    value_json: dict[str, Any] | None
    value_schema: str | None
    alternative_group: str | None
    equivalence_allowed: bool
    condition_note: str | None


def normalise_requirement(item: Any, *, run_id: Any, chunk_index: int) -> NormalisedRequirement:
    """Resol un `RequirementOutV2` als camps que es desen.

    El valor passa pel contracte tipat: si no encaixa, arriba com a generic en
    comptes de perdre's. L'equivalencia es el `or` del camp explicit i del que
    digui el propi valor, perque el model la pot posar a qualsevol dels dos i
    perdre-la seria llegir "ISO 9001 o equivalent" com "exactament ISO 9001".
    """
    modality = getattr(item, "modality", RequirementModality.UNKNOWN)
    value = parse_value(getattr(item, "value", None))
    value_json = dump_value(value)

    equivalence = bool(getattr(item, "equivalence_allowed", False))
    if value is not None:
        equivalence = equivalence or bool(getattr(value, "equivalence_allowed", False))

    return NormalisedRequirement(
        obligation=derive_obligation(item.obligation, modality),
        modality=modality,
        value_json=value_json,
        value_schema=VALUE_SCHEMA_VERSION if value_json is not None else None,
        alternative_group=alternative_group_key(
            getattr(item, "alternative_group", None), run_id=run_id, chunk_index=chunk_index
        ),
        equivalence_allowed=equivalence,
        condition_note=(getattr(item, "condition_note", None) or None),
    )
