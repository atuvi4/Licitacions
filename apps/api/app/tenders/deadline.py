"""Dies fins al termini de presentacio: UNA sola regla per a tot el backend.

La mateixa que `apps/web/src/lib/deadline.ts` (`deadlineCountdown`): **dies naturals
sencers que queden a Europe/Madrid**, l'hora oficial de les plataformes de
contractacio. Falten tants dies com dies de calendari separen avui del dia del
termini; l'hora no hi compta, ni el canvi d'hora. 0 vol dir «avui»; negatiu, dies
des que va vencer.

Abans hi havia dos arrodoniments cap avall sobre blocs de 24 h (`bid_workspace` i
`matching/timing`) i el web en feia un altre cap amunt: la mateixa licitacio deia
«12 dies» a la fitxa i «11» a Candidatures.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

DEADLINE_TIME_ZONE = ZoneInfo("Europe/Madrid")


def _calendar_day(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(DEADLINE_TIME_ZONE).date()


def days_until_deadline(deadline: datetime | None, now: datetime | None = None) -> int | None:
    """Dies naturals sencers fins al termini a Europe/Madrid. `None` sense termini."""
    if deadline is None:
        return None
    reference = now or datetime.now(UTC)
    return (_calendar_day(deadline) - _calendar_day(reference)).days


def deadline_has_passed(deadline: datetime | None, now: datetime | None = None) -> bool:
    """L'instant del termini ja ha passat (mateixa regla que `isDeadlinePassed` del web).

    Independent dels dies naturals: un termini d'avui a les 10:00 ja ha vençut a les
    12:00 encara que `days_until_deadline` doni 0. Sense termini, mai."""
    if deadline is None:
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    return deadline < (now or datetime.now(UTC))
