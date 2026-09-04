"""Instantania del perfil d'una empresa, carregada una sola vegada.

El motor rep aquest objecte i no torna a tocar la base de dades: sense aixo,
puntuar 300 licitacions faria centenars de consultes (N+1). Es carrega tot el
que necessita el pre-fit en quatre consultes i prou.
"""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cpv import CompanyCPVPreference
from app.models.profile import CompanyContractPreferences, CompanyTerritory


@dataclass(slots=True)
class CPVPreference:
    code: str
    priority: int
    active: bool
    excluded: bool


@dataclass(slots=True)
class TerritoryPreference:
    name: str
    normalized_name: str
    territory_type: str
    code: str | None
    priority: int
    willing_to_work: bool


@dataclass(slots=True)
class ContractCapacity:
    minimum_interesting: Decimal | None = None
    preferred_min: Decimal | None = None
    preferred_max: Decimal | None = None
    maximum_recommended: Decimal | None = None
    maximum_assumable: Decimal | None = None

    @property
    def is_configured(self) -> bool:
        return any(
            value is not None
            for value in (
                self.minimum_interesting,
                self.preferred_min,
                self.preferred_max,
                self.maximum_recommended,
                self.maximum_assumable,
            )
        )


def normalize_place(value: str | None) -> str:
    """Normalitza un nom de lloc per poder comparar-lo de manera exacta.

    Minuscules, sense accents i sense signes. NO es una comparacio difusa: o
    els noms son iguals un cop normalitzats, o no ho son.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return "".join(character for character in text.lower() if character.isalnum() or character == " ").strip()


@dataclass(slots=True)
class CompanyMatchProfile:
    """Tot el que el motor necessita saber d'una empresa."""

    company_id: uuid.UUID
    cpv_preferences: list[CPVPreference] = field(default_factory=list)
    territories: list[TerritoryPreference] = field(default_factory=list)
    capacity: ContractCapacity = field(default_factory=ContractCapacity)
    # Ultim canvi conegut a les dades que afecten el pre-fit. Serveix per saber
    # si un match ha quedat obsolet, sense muntar cap sistema d'esdeveniments.
    profile_changed_at: datetime | None = None

    @property
    def has_cpv_preferences(self) -> bool:
        return bool(self.cpv_preferences)

    @property
    def has_territories(self) -> bool:
        return bool(self.territories)


async def load_profile(session: AsyncSession, company_id: uuid.UUID) -> CompanyMatchProfile:
    cpv_rows = (
        await session.execute(
            sa.select(CompanyCPVPreference).where(CompanyCPVPreference.company_id == company_id)
        )
    ).scalars().all()
    territory_rows = (
        await session.execute(
            sa.select(CompanyTerritory).where(CompanyTerritory.company_id == company_id)
        )
    ).scalars().all()
    preferences = (
        await session.execute(
            sa.select(CompanyContractPreferences).where(
                CompanyContractPreferences.company_id == company_id
            )
        )
    ).scalar_one_or_none()

    timestamps = [row.updated_at for row in cpv_rows]
    timestamps += [row.updated_at for row in territory_rows]
    if preferences is not None:
        timestamps.append(preferences.updated_at)

    return CompanyMatchProfile(
        company_id=company_id,
        cpv_preferences=[
            CPVPreference(
                code=row.cpv_code,
                priority=row.priority,
                active=row.active,
                excluded=row.excluded,
            )
            for row in cpv_rows
        ],
        territories=[
            TerritoryPreference(
                name=row.name,
                normalized_name=normalize_place(row.name),
                territory_type=row.territory_type.value,
                code=(row.code or "").strip().upper() or None,
                priority=row.priority,
                willing_to_work=row.willing_to_work,
            )
            for row in territory_rows
        ],
        capacity=ContractCapacity(
            minimum_interesting=preferences.minimum_interesting_contract if preferences else None,
            preferred_min=preferences.preferred_contract_min if preferences else None,
            preferred_max=preferences.preferred_contract_max if preferences else None,
            maximum_recommended=preferences.maximum_recommended_contract if preferences else None,
            maximum_assumable=preferences.maximum_assumable_contract if preferences else None,
        ),
        profile_changed_at=max(timestamps) if timestamps else None,
    )
