"""Regles `metadata-match-v3` (precisio del Radar, 21-10-2026).

Quatre canvis, cadascun amb el cas real que el va motivar (diagnosi del Radar d'Indaleccius):

* B: per ser candidata cal un minim de CPV. Vic i El Vendrell (enllumenat i urbanitzacio)
  eren candidates per una coincidencia de CLASSE «4531» amb el cablejat de telecomunicacions.
* C: un termini desconegut compta 0 de 10. Dos expedients de Navarra pujaven de 65 a 72.
* D: un lot sense CPV propis hereta nomes el principal. Els 29 lots d'un acord marc (vehicles,
  carburants...) encaixaven exactament amb «equips de xarxa», un dels ~300 CPV de l'expedient.
* F: `pending_award` (PLACSP EV) es tancada: les ofertes ja s'han presentat.

El motor es pur: cap base de dades ni HTTP.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.matching import engine, timing
from app.matching.profile import (
    CompanyMatchProfile,
    ContractCapacity,
    CPVPreference,
    TerritoryPreference,
    normalize_place,
)
from app.matching.rules import (
    CPV_CANDIDATE_MIN_SCORE,
    NON_ACTIONABLE_STATUSES,
    RULES_VERSION,
    ReasonCode,
)
from app.matching.service import best_lot, evaluate_tender, inherited_lot_cpvs
from app.models.enums import ExclusionKind, MatchStatus

NOW = datetime(2026, 10, 21, 9, 48, tzinfo=UTC)
CAPACITY = ContractCapacity(
    minimum_interesting=Decimal("10000"),
    preferred_min=Decimal("15000"),
    preferred_max=Decimal("350000"),
    maximum_recommended=Decimal("600000"),
    maximum_assumable=Decimal("1200000"),
)


def profile(cpvs: list[tuple[str, int]], *, active: bool = True) -> CompanyMatchProfile:
    return CompanyMatchProfile(
        company_id=uuid.uuid4(),
        cpv_preferences=[
            CPVPreference(code=code, priority=priority, active=active, excluded=False)
            for code, priority in cpvs
        ],
        territories=[
            TerritoryPreference(
                name="Girona", normalized_name=normalize_place("Girona"), territory_type="province",
                code="ES512", priority=10, willing_to_work=True,
            )
        ],
        capacity=CAPACITY,
    )


def evaluate(prof, *, cpvs, status="open", deadline=NOW + timedelta(days=30), nuts="ES512",
             amount=Decimal("100000")):
    return engine.evaluate(
        prof, cpv_codes=cpvs, nuts_code=nuts, place_name=None, amount=amount,
        tender_status=status, deadline=deadline, now=NOW,
    )


def cpv_factor(outcome) -> dict:
    return next(f for f in outcome.factors if f["dimension"] == "cpv")


def test_la_versio_de_les_regles_es_v3():
    assert RULES_VERSION == "metadata-match-v3"


# --------------------------------------------------------------------------- #
# B · Minim de CPV per ser candidata                                          #
# --------------------------------------------------------------------------- #


class TestMinimDeCPV:
    def test_el_minim_es_la_meitat_del_pes_del_cpv(self):
        assert CPV_CANDIDATE_MIN_SCORE == 20.0

    def test_una_classe_amb_prioritat_mitjana_no_fa_candidata_encara_que_sumi(self):
        # Com Vic i El Vendrell (18,7 de CPV). Aqui la resta es perfecta (79), i tampoc.
        outcome = evaluate(profile([("45314300", 5)]), cpvs=["45316100"])
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_RELATED_CLASS
        assert outcome.cpv_score == pytest.approx(18.7)
        assert outcome.score >= 60
        assert outcome.status is MatchStatus.LOW_FIT
        assert cpv_factor(outcome)["detail"]["below_candidate_minimum"] is True
        assert cpv_factor(outcome)["detail"]["candidate_minimum"] == 20.0

    def test_una_classe_amb_prioritat_alta_si_que_hi_arriba(self):
        outcome = evaluate(profile([("50330000", 10)]), cpvs=["50332000"])
        assert outcome.cpv_score == pytest.approx(22.0)
        assert outcome.status is MatchStatus.CANDIDATE
        assert "below_candidate_minimum" not in cpv_factor(outcome)["detail"]

    def test_una_categoria_amb_prioritat_baixa_hi_arriba(self):
        outcome = evaluate(profile([("45314300", 3)]), cpvs=["45314310"])
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_RELATED_CATEGORY
        assert outcome.cpv_score == pytest.approx(21.0)
        assert outcome.status is MatchStatus.CANDIDATE

    def test_una_licitacio_sense_cpv_no_pot_ser_candidata(self):
        # Abans: 25 + 25 + 10 sobre 60 = 100 i candidata, sense saber que es el contracte.
        outcome = evaluate(profile([("64200000", 10)]), cpvs=[])
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_UNKNOWN
        assert outcome.score == 100
        assert outcome.status is MatchStatus.LOW_FIT
        assert cpv_factor(outcome)["detail"]["below_candidate_minimum"] is True

    def test_els_bloquejadors_d_abans_continuen_i_s_expliquen_sols(self):
        # Nomes divisio: el motiu de sempre ja diu per que no es candidata; no cal la marca.
        outcome = evaluate(profile([("64200000", 10)]), cpvs=["64900000"])
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_RELATED_DIVISION
        assert outcome.score >= 60
        assert outcome.status is MatchStatus.LOW_FIT
        assert "below_candidate_minimum" not in (cpv_factor(outcome)["detail"] or {})

    def test_sense_cpv_al_perfil_no_pot_ser_candidata_i_ho_diu(self):
        outcome = evaluate(profile([]), cpvs=["64200000"])
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_NO_COMPANY_PREFERENCES
        assert outcome.score == 100
        assert outcome.status is MatchStatus.LOW_FIT
        assert cpv_factor(outcome)["detail"]["below_candidate_minimum"] is True

    @pytest.mark.parametrize(
        ("cpvs", "active", "reason"),
        [
            (["85200000"], True, ReasonCode.CPV_NO_OVERLAP),
            (["64200000"], False, ReasonCode.CPV_INACTIVE_ONLY),
        ],
    )
    def test_els_altres_bloquejadors_tampoc_no_porten_la_marca(self, cpvs, active, reason):
        outcome = evaluate(profile([("64200000", 10)], active=active), cpvs=cpvs)
        assert cpv_factor(outcome)["reason_code"] == reason
        assert outcome.score >= 60
        assert outcome.status is MatchStatus.LOW_FIT
        assert "below_candidate_minimum" not in (cpv_factor(outcome)["detail"] or {})

    def test_el_minim_i_el_termini_desconegut_alhora(self):
        # 18,7 + 25 + 25 + 0 (termini desconegut) = 68,7: sobre 100, 69; baix encaix pel minim.
        outcome = evaluate(profile([("45314300", 5)]), cpvs=["45316100"], deadline=None)
        assert outcome.score == 69
        assert outcome.status is MatchStatus.LOW_FIT
        assert cpv_factor(outcome)["detail"]["below_candidate_minimum"] is True
        timing_factor = next(f for f in outcome.factors if f["dimension"] == "timing")
        assert timing_factor["detail"] == {"counts_as_zero": True}

    def test_sota_del_llindar_la_marca_no_hi_es(self):
        # Amb 55 no seria candidata igualment: el motiu es la puntuacio, no el minim.
        outcome = evaluate(
            profile([("45314300", 5)]), cpvs=["45316100"], nuts="ES300",
            deadline=NOW + timedelta(days=2), amount=Decimal("5000"),
        )
        assert outcome.score < 60
        assert outcome.status is MatchStatus.LOW_FIT
        assert "below_candidate_minimum" not in (cpv_factor(outcome)["detail"] or {})


# --------------------------------------------------------------------------- #
# C · Termini desconegut                                                       #
# --------------------------------------------------------------------------- #


class TestTerminiDesconegut:
    def test_compta_zero_i_no_reescala_sobre_90(self):
        # Els dos expedients de Navarra: 34 + 6,25 + 25 = 65,25. Abans, sobre 90: 72.
        outcome = evaluate(
            profile([("32510000", 6)]), cpvs=["32510000"], deadline=None, nuts="ES220"
        )
        assert outcome.cpv_score == pytest.approx(34.0)
        assert outcome.timing_score == 0.0
        assert outcome.score == 65

    def test_continua_sent_una_dada_desconeguda(self):
        outcome = evaluate(profile([("32510000", 6)]), cpvs=["32510000"], deadline=None)
        timing_factor = next(f for f in outcome.factors if f["dimension"] == "timing")
        assert timing_factor["result"] == "unknown"
        assert timing_factor["reason_code"] == ReasonCode.DEADLINE_UNKNOWN
        assert timing_factor["detail"] == {"counts_as_zero": True}
        # La cobertura diu quines dades tenim: el termini no hi es.
        assert outcome.data_coverage == pytest.approx(0.9)

    def test_amb_termini_conegut_res_no_canvia(self):
        outcome = evaluate(profile([("64200000", 10)]), cpvs=["64200000"])
        assert outcome.score == 100
        assert outcome.status is MatchStatus.CANDIDATE

    def test_amb_una_altra_dada_desconeguda_aquella_si_que_surt_del_calcul(self):
        # Sense import (25) i sense termini: 40 + 25 de 75 (el termini hi compta amb 0) = 87.
        outcome = evaluate(
            profile([("64200000", 10)]), cpvs=["64200000"], deadline=None, amount=None
        )
        assert outcome.score == 87
        assert outcome.data_coverage == pytest.approx(0.65)


# --------------------------------------------------------------------------- #
# D · CPV dels lots sense CPV propi                                            #
# --------------------------------------------------------------------------- #


def tender(cpvs, lots, *, status="open"):
    return SimpleNamespace(
        cpvs=cpvs, estimated_value=Decimal("100000"), base_budget_without_tax=None,
        base_budget=None, status=SimpleNamespace(value=status), lots=list(lots),
        nuts_code="ES512", place_of_execution="Girona", deadline_at=NOW + timedelta(days=30),
    )


def lot(cpvs=(), amount=Decimal("50000")):
    return SimpleNamespace(
        id=uuid.uuid4(), removed_at=None, cpvs=list(cpvs), nuts_code=None,
        place_of_execution=None, estimated_value=amount, base_budget_without_tax=None,
        base_budget=None, deadline_at=None,
    )


def cpv(code, main=False):
    return SimpleNamespace(cpv_code=code, is_main=main)


class TestCPVDelsLots:
    def test_un_lot_sense_cpv_hereta_nomes_el_principal(self):
        # L'acord marc: principal «vaixella» i, entre centenars de secundaris, «equips de xarxa».
        marc = tender([cpv("39221130", True), cpv("32420000", False)], [lot()])
        _summary, lots = evaluate_tender(profile([("32420000", 7)]), marc, NOW)
        (_, outcome), = lots
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_NO_OVERLAP
        assert outcome.status is MatchStatus.LOW_FIT
        # El factor diu que el CPV es el de l'expedient, perque la fitxa ho pugui explicar.
        assert cpv_factor(outcome)["detail"]["inherited_from_tender"] is True

    def test_el_principal_es_el_primer_que_publica_la_font(self):
        # L'ordre real del feed de L'Escala: 32562100 primer, 64200000 (el del plec) tercer.
        # Sense lots compten tots; amb lots sense CPV propis, nomes el primer.
        feed = [cpv("32562100", True), cpv("45314300", False), cpv("64200000", False)]
        prof = profile([("64200000", 10)])
        sense_lots, _ = evaluate_tender(prof, tender(feed, []), NOW)
        amb_lots, lots = evaluate_tender(prof, tender(feed, [lot(), lot()]), NOW)
        assert sense_lots.status is MatchStatus.CANDIDATE
        assert amb_lots.status is MatchStatus.LOW_FIT
        assert all(cpv_factor(o)["reason_code"] == ReasonCode.CPV_NO_OVERLAP for _, o in lots)
        assert all(cpv_factor(o)["detail"]["inherited_from_tender"] is True for _, o in lots)

    def test_l_expedient_sense_lots_continua_mirant_tots_els_seus_cpv(self):
        sense_lots = tender([cpv("39221130", True), cpv("32420000", False)], [])
        outcome, lots = evaluate_tender(profile([("32420000", 7)]), sense_lots, NOW)
        assert lots == []
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_EXACT_MEDIUM_PRIORITY

    def test_el_cpv_propi_del_lot_mana(self):
        marc = tender([cpv("39221130", True)], [lot([cpv("32420000")])])
        _summary, ((_, outcome),) = evaluate_tender(profile([("32420000", 7)]), marc, NOW)
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_EXACT_MEDIUM_PRIORITY
        assert "inherited_from_tender" not in cpv_factor(outcome)["detail"]

    def test_un_lot_que_no_arriba_al_minim_no_amaga_un_lot_candidat(self):
        # Lot 1: classe mitjana (18,7), 79, baix encaix pel minim. Lot 2: exacte, 73, candidat.
        prof = profile([("45314300", 5), ("64200000", 6)])
        lot_1 = lot([cpv("45316100")], amount=Decimal("100000"))
        lot_2 = lot([cpv("64200000")], amount=Decimal("5000"))
        summary, lots = evaluate_tender(prof, tender([cpv("45316100", True)], [lot_1, lot_2]), NOW)
        (first, outcome_1), (second, outcome_2) = lots
        assert (outcome_1.score, outcome_1.status) == (79, MatchStatus.LOW_FIT)
        assert (outcome_2.score, outcome_2.status) == (73, MatchStatus.CANDIDATE)
        # L'expedient es el del millor lot CANDIDAT, i el lot destacat es el mateix.
        assert summary is outcome_2
        assert best_lot(summary, lots) is second

    def test_sense_cpv_al_perfil_el_lot_no_porta_la_marca_d_herencia(self):
        # No s'ha comparat amb cap CPV: dir que «s'ha avaluat amb el primer» seria fals.
        marc = tender([cpv("64200000", True)], [lot()])
        _summary, ((_, outcome),) = evaluate_tender(profile([]), marc, NOW)
        assert cpv_factor(outcome)["reason_code"] == ReasonCode.CPV_NO_COMPANY_PREFERENCES
        assert "inherited_from_tender" not in cpv_factor(outcome)["detail"]

    def test_sense_cap_lot_puntuat_no_hi_ha_lot_destacat(self):
        marc = tender([cpv("64200000", True)], [lot(), lot()], status="pending_award")
        summary, lots = evaluate_tender(profile([("64200000", 10)]), marc, NOW)
        assert summary.status is MatchStatus.CLOSED
        assert summary.score is None
        assert best_lot(summary, lots) is None

    def test_sense_cap_lot_candidat_l_expedient_es_el_lot_de_mes_puntuacio(self):
        prof = profile([("45314300", 5)])
        lot_1 = lot([cpv("45316100")], amount=Decimal("100000"))
        lot_2 = lot([cpv("45316100")], amount=Decimal("5000"))
        summary, lots = evaluate_tender(prof, tender([cpv("45316100", True)], [lot_1, lot_2]), NOW)
        assert summary is lots[0][1]
        assert summary.status is MatchStatus.LOW_FIT
        assert best_lot(summary, lots) is lots[0][0]

    def test_sense_principal_i_un_sol_cpv_l_hereta(self):
        assert inherited_lot_cpvs(tender([cpv("64200000")], [])) == ["64200000"]

    def test_sense_principal_i_diversos_cpv_no_n_hereta_cap(self):
        assert inherited_lot_cpvs(tender([cpv("64200000"), cpv("32420000")], [])) == []

    def test_amb_principal_nomes_el_principal(self):
        marc = tender([cpv("32420000", False), cpv("64200000", True)], [])
        assert inherited_lot_cpvs(marc) == ["64200000"]


# --------------------------------------------------------------------------- #
# F i E · Estats de la font                                                    #
# --------------------------------------------------------------------------- #


class TestEstatsDeLaFont:
    def test_pendent_d_adjudicacio_no_es_accionable(self):
        assert "pending_award" in NON_ACTIONABLE_STATUSES
        outcome = timing.evaluate("pending_award", None, NOW)
        assert outcome.excluded is True
        assert outcome.factor.reason_code == ReasonCode.STATUS_NOT_OPEN

    def test_pendent_d_adjudicacio_queda_tancada_encara_que_encaixi(self):
        outcome = evaluate(
            profile([("64200000", 10)]), cpvs=["64200000"], status="pending_award", deadline=None
        )
        assert outcome.status is MatchStatus.CLOSED
        assert outcome.exclusion_kind is ExclusionKind.STATUS
        assert outcome.exclusion_reason_code == ReasonCode.STATUS_NOT_OPEN

    def test_un_anunci_previ_continua_sent_avaluable_amb_el_termini_a_zero(self):
        # Una convocatoria futura (el cas de Dipsalut): avaluable, pero ja no puja a 100.
        outcome = evaluate(profile([("64212000", 8)]), cpvs=["64212000"], status="pre_announcement",
                           deadline=None, amount=Decimal("25468.99"))
        assert outcome.status is MatchStatus.CANDIDATE
        assert outcome.score == 90
