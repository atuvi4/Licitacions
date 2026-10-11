"""Provenance d'una escriptura d'estructura o d'abast del plec (0060, 3B-2 §4.1).

Les funcions d'escriptura de la via interna (`tender_structure`, `tender_scope`)
reben una `Provenance` EXPLICITA: qui escriu, amb quina autoritat i des de quina
passada o proposta. No hi ha valor per defecte: una fila d'estructura sense
origen diria una cosa que no ha passat.

Regles que imposa el constructor:

* tota escriptura es atribuible: hi ha actor (`actor_id`) o passada de productor
  (`producer_run_id`), mai cap dels dos absent (les cites que es creen per la via
  interna porten el mateix: `created_by` o `producer_run_id`);
* `owner_manual` = el propietari d'una licitacio oberta a ma (`tender_owner`, amb
  actor); `operator` = l'operador de plataforma (`platform_operator`, amb actor);
  `promoted` = una proposta promoguda (`proposal_id` obligatori, amb actor);
  `gold` i `extracted` = un productor (`producer`).

`origin` descriu el valor VIGENT: una correccio posterior la torna a segellar
(`updated_by`), mentre que `created_by` es queda amb qui la va crear.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.models.enums import StructureAuthority, StructureOrigin


class ProvenanceError(ValueError):
    """Una provenance incoherent: es un error de programacio, no de l'usuari."""


#: Autoritat per origen (la que s'espera; `promoted` en pot tenir dues).
_AUTHORITY_BY_ORIGIN: dict[StructureOrigin, frozenset[StructureAuthority]] = {
    StructureOrigin.OWNER_MANUAL: frozenset({StructureAuthority.TENDER_OWNER}),
    StructureOrigin.OPERATOR: frozenset({StructureAuthority.PLATFORM_OPERATOR}),
    StructureOrigin.PROMOTED: frozenset(
        {StructureAuthority.PLATFORM_OPERATOR, StructureAuthority.PROMOTED_FROM_COMPANY}
    ),
    StructureOrigin.GOLD: frozenset({StructureAuthority.PRODUCER}),
    StructureOrigin.EXTRACTED: frozenset({StructureAuthority.PRODUCER}),
}


@dataclass(frozen=True, slots=True)
class Provenance:
    origin: StructureOrigin
    authority: StructureAuthority
    actor_id: uuid.UUID | None = None
    producer_run_id: uuid.UUID | None = None
    proposal_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        origin = StructureOrigin(self.origin)
        authority = StructureAuthority(self.authority)
        if authority not in _AUTHORITY_BY_ORIGIN[origin]:
            raise ProvenanceError(f"L'origen {origin} no s'escriu amb l'autoritat {authority}.")
        if self.actor_id is None and self.producer_run_id is None:
            raise ProvenanceError("Cap escriptura sense actor ni passada de productor.")
        if origin in (StructureOrigin.OWNER_MANUAL, StructureOrigin.OPERATOR) and (
            self.actor_id is None
        ):
            raise ProvenanceError(f"L'origen {origin} necessita una persona.")
        if origin == StructureOrigin.PROMOTED and (
            self.proposal_id is None or self.actor_id is None
        ):
            raise ProvenanceError("Una promocio necessita la proposta i qui la promou.")
        if origin == StructureOrigin.EXTRACTED and self.producer_run_id is None:
            raise ProvenanceError("Un valor extret necessita la passada que el va produir.")

    # ------------------------------------------------------------------ #
    # Constructors                                                        #
    # ------------------------------------------------------------------ #

    @classmethod
    def owner_manual(cls, actor_id: uuid.UUID) -> Provenance:
        return cls(StructureOrigin.OWNER_MANUAL, StructureAuthority.TENDER_OWNER, actor_id)

    @classmethod
    def operator(cls, actor_id: uuid.UUID, *, proposal_id: uuid.UUID | None = None) -> Provenance:
        """Escriptura directa de l'operador (amb `proposal_id` si la cita viu a
        una proposta seva, p. ex. territoris o temporalitat sense taula de cites)."""
        return cls(
            StructureOrigin.OPERATOR,
            StructureAuthority.PLATFORM_OPERATOR,
            actor_id,
            proposal_id=proposal_id,
        )

    @classmethod
    def promoted(
        cls,
        actor_id: uuid.UUID,
        proposal_id: uuid.UUID,
        *,
        producer_run_id: uuid.UUID | None = None,
    ) -> Provenance:
        return cls(
            StructureOrigin.PROMOTED,
            StructureAuthority.PLATFORM_OPERATOR,
            actor_id,
            producer_run_id=producer_run_id,
            proposal_id=proposal_id,
        )

    @classmethod
    def gold(cls, producer_run_id: uuid.UUID, *, actor_id: uuid.UUID | None = None) -> Provenance:
        return cls(
            StructureOrigin.GOLD,
            StructureAuthority.PRODUCER,
            actor_id,
            producer_run_id=producer_run_id,
        )

    @classmethod
    def extracted(cls, producer_run_id: uuid.UUID) -> Provenance:
        return cls(
            StructureOrigin.EXTRACTED, StructureAuthority.PRODUCER, producer_run_id=producer_run_id
        )

    # ------------------------------------------------------------------ #
    # Segells                                                             #
    # ------------------------------------------------------------------ #

    def _stamp(self, row: Any) -> None:
        row.origin = self.origin
        row.authority = self.authority
        row.producer_run_id = self.producer_run_id
        row.proposal_id = self.proposal_id
        row.updated_by = self.actor_id

    def stamp_create(self, row: Any) -> Any:
        """Una fila nova: tota la provenance, i `created_by` = l'actor."""
        self._stamp(row)
        row.created_by = self.actor_id
        return row

    def stamp_update(self, row: Any) -> Any:
        """Una fila que canvia de valor: el valor vigent es d'aquest escriptor.
        `created_by` es queda amb qui la va crear."""
        self._stamp(row)
        return row

    def create_kwargs(self) -> dict[str, Any]:
        """Els mateixos camps que `stamp_create`, per al constructor del model."""
        return {
            "origin": self.origin,
            "authority": self.authority,
            "producer_run_id": self.producer_run_id,
            "proposal_id": self.proposal_id,
            "created_by": self.actor_id,
            "updated_by": self.actor_id,
        }

    def link_kwargs(self) -> dict[str, Any]:
        """Els camps d'autoria d'un enllac de cita nou: actor o passada, mai cap."""
        return {"created_by": self.actor_id, "producer_run_id": self.producer_run_id}
