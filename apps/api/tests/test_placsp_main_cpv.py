"""CPV «principal» d'una entrada PLACSP sense principal explicit (auditoria UI P1-05).

El feed PLACSP llista els CPV (`RequiredCommodityClassification`) sense marcar-ne cap de
principal. La ingesta marca `is_main` al PRIMER de la llista i conserva tots els altres; el
web ho diu «primer a la font», no «principal». Aquests tests fixen el comportament que el
web descriu: cap reordenacio, cap CPV perdut, i el Radar els fa servir tots (regles v3:
un lot sense CPV propis nomes hereta el primer si l'expedient en te mes de 5).

Cas real: L'Escala 2026/3964, feed amb 32562100 primer; el PSCP i el DOC-03 declaren
64200000 com a principal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.ingestion.placsp.normalizer import _cpvs
from app.matching import engine
from app.matching.profile import CompanyMatchProfile, ContractCapacity, CPVPreference
from app.matching.rules import ReasonCode

FEED_ORDER = ["32562100", "45314300", "64200000", "64215000", "64210000", "32420000"]


def test_el_primer_cpv_del_feed_es_l_unic_marcat_i_cap_es_perd_ni_es_reordena():
    cpvs = _cpvs(FEED_ORDER)
    assert [cpv.code for cpv in cpvs] == FEED_ORDER
    assert [cpv.is_main for cpv in cpvs] == [True, False, False, False, False, False]


def test_una_llista_buida_no_inventa_cap_principal():
    assert _cpvs([]) == []


def test_el_radar_fa_servir_tots_els_cpv_no_nomes_el_primer():
    """Una empresa amb el CPV del plec (64200000) encaixa encara que el feed el posi tercer."""
    now = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    profile = CompanyMatchProfile(
        company_id=uuid.uuid4(),
        cpv_preferences=[CPVPreference(code="64200000", priority=10, active=True, excluded=False)],
        territories=[],
        capacity=ContractCapacity(),
    )
    outcome = engine.evaluate(
        profile,
        cpv_codes=FEED_ORDER,
        nuts_code=None,
        place_name=None,
        amount=Decimal("204232.75"),
        tender_status="open",
        deadline=now + timedelta(days=6),
        now=now,
    )
    cpv_factor = next(factor for factor in outcome.factors if factor["dimension"] == "cpv")
    assert cpv_factor["reason_code"] == ReasonCode.CPV_EXACT_HIGH_PRIORITY
