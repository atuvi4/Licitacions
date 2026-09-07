"""Identitat HTTP de la plataforma davant de fonts externes.

Viu a `core` i no a `ingestion` perque hi ha dos consumidors: la sindicacio de
PLACSP i la descarrega de plecs. Les dues capes s'han de presentar igual davant
d'un servei public.
"""

from __future__ import annotations

from app.core.config import settings


def user_agent() -> str:
    """User-Agent identificable, amb contacte, tal com demana el bon us de dades obertes."""
    contact = settings.INGESTION_CONTACT_EMAIL or "sense-contacte"
    return f"LicitacionsBot/0.1 (+ingesta de dades obertes; contacte: {contact})"
