"""Gestio del cataleg CPV.

El cataleg oficial europeu no esta importat. Mentre no ho estigui, qualsevol
codi que s'utilitzi es dona d'alta automaticament al cataleg amb la descripcio
que informi l'usuari. Aixo evita que l'usuari hagi de mantenir dues coses
(cataleg i preferencies) i deixa la taula preparada per a la importacio massiva
posterior: quan s'importi l'oficial, nomes caldra completar les descripcions.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cpv import CPVCode

PLACEHOLDER_PREFIX = "CPV "


async def ensure_cpv_code(
    session: AsyncSession, code: str, description: str | None = None
) -> CPVCode:
    """Retorna el codi del cataleg, creant-lo si encara no hi es."""
    existing = (
        await session.execute(sa.select(CPVCode).where(CPVCode.code == code))
    ).scalar_one_or_none()

    if existing is None:
        entry = CPVCode(
            code=code,
            description=(description or f"{PLACEHOLDER_PREFIX}{code}").strip(),
            # El CPV es jerarquic per prefix; deixem preparat el pare de 2 digits.
            parent_code=code[:2] if len(code) >= 8 else None,
        )
        session.add(entry)
        await session.flush()
        return entry

    # Si el codi existia amb una descripcio provisional i ara ens en donen una
    # de bona, la completem. Mai la sobreescrivim si ja era significativa.
    if description and existing.description.startswith(PLACEHOLDER_PREFIX):
        existing.description = description.strip()

    return existing
