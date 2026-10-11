"""Regla unica de dies fins al termini (3B-2, revisio final B5).

La mateixa que `apps/web/src/lib/deadline.ts` (`deadlineCountdown`): dies naturals
sencers que queden a Europe/Madrid; l'hora no hi compta, ni el canvi d'hora. Els casos
son els de `deadline.test.ts`, perque el web i el backend no tornin a discrepar d'1 dia.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.matching import timing
from app.services import bid_workspace
from app.tenders.deadline import days_until_deadline

NOW = datetime(2026, 10, 9, 16, 0, tzinfo=UTC)  # 18:00 a Madrid
PILOT_DEADLINE = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)  # 12:00 a Madrid


@pytest.mark.parametrize(
    ("deadline", "now", "expected"),
    [
        # El cas del pilot: 11 dies i 18 hores -> 12 (el web deia 12, el servidor 11).
        (PILOT_DEADLINE, NOW, 12),
        # 00:30 del 22 a Madrid: ja es un dia mes.
        (datetime(2026, 10, 21, 22, 30, tzinfo=UTC), NOW, 13),
        # El mateix dia natural, encara que faltin minuts: 0.
        (datetime(2026, 10, 9, 21, 59, tzinfo=UTC), NOW, 0),
        (datetime(2026, 10, 8, 10, 0, tzinfo=UTC), NOW, -1),
        # El canvi d'hora (25 d'octubre de 2026) no mou el recompte.
        (datetime(2026, 10, 26, 11, 0, tzinfo=UTC), datetime(2026, 10, 24, 11, 30, tzinfo=UTC), 2),
    ],
)
def test_paritat_amb_el_web(deadline, now, expected):
    assert days_until_deadline(deadline, now) == expected


def test_sense_termini_es_none():
    assert days_until_deadline(None, NOW) is None


def test_un_termini_ingenu_es_utc():
    assert days_until_deadline(PILOT_DEADLINE.replace(tzinfo=None), NOW) == 12


def test_el_servei_de_workspace_i_el_matching_fan_servir_la_regla_unica():
    assert bid_workspace._days_remaining(PILOT_DEADLINE, NOW) == 12
    assert timing.days_left(PILOT_DEADLINE, NOW) == 12


def test_cap_altre_calcul_de_dies_de_termini():
    """Cap altre calcul de `.days` / `total_seconds` sobre un termini a bid_workspace
    ni a matching/timing: tots passen pel helper."""
    for module in (bid_workspace, timing):
        source = Path(inspect.getsourcefile(module)).read_text(encoding="utf-8")
        assert ".days" not in source.replace("days_until_deadline", "")
        assert "total_seconds" not in source


def test_un_termini_passat_avui_mateix_continua_excloent_la_licitacio():
    """0 dies naturals no vol dir viu: si l'instant ja ha passat, el termini ha vencut."""
    deadline = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)  # 10:00 a Madrid, ja passat a les 18:00
    assert days_until_deadline(deadline, NOW) == 0
    outcome = timing.evaluate("open", deadline, NOW)
    assert outcome.excluded is True
    assert outcome.factor.reason_code == timing.ReasonCode.DEADLINE_EXPIRED
