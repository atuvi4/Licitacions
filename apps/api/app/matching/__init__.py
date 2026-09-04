"""Pre-fit determinista entre el perfil d'una empresa i les licitacions.

    rules.py       pesos, llindars i codis de motiu (tots els numeros, en un lloc)
    profile.py     instantania del perfil de l'empresa
    cpv.py         dimensio CPV (jerarquia per prefix del codi)
    geography.py   dimensio territorial (NUTS i nom exacte)
    economic.py    dimensio economica (capacitat declarada, NO solvencia legal)
    timing.py      dimensio temporal (marge fins al termini)
    engine.py      combina les dimensions; pur, sense base de dades
    service.py     carrega, calcula i persisteix

Cap LLM, cap embedding, cap similitud semantica: el mateix input dona sempre el
mateix resultat.
"""

from app.matching.engine import MatchOutcome, evaluate
from app.matching.profile import CompanyMatchProfile, load_profile
from app.matching.rules import RULES_VERSION
from app.matching.service import MatchingService, is_stale

__all__ = [
    "RULES_VERSION",
    "CompanyMatchProfile",
    "MatchOutcome",
    "MatchingService",
    "evaluate",
    "is_stale",
    "load_profile",
]
