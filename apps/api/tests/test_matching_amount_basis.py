"""F-06b (regles v2): quin import fa servir el pre-fit i amb quina base.

El VEC (art. 101 LCSP) i el PBL (art. 100) no son el mateix. Sense VEC, el PBL
sense IVA nomes es una cota inferior del VEC: serveix per dir que el contracte
SUPERA un llindar, mai per dir que hi cap. Unes xifres incoherents (VEC < PBL)
no donen cap conclusio. Tot sense BD: el motor es pur.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.matching import economic
from app.matching.economic import AmountBasis, AmountReference, reference_amount
from app.matching.rules import RULES_VERSION, ReasonCode
from app.matching.service import evaluate_tender
from app.models.enums import DimensionResult, ExclusionKind, MatchStatus
from tests.test_matching_engine import (
    FULL_CAPACITY,
    evaluate_default,
    make_profile,
    perfect_profile,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
D = Decimal


def test_la_versio_de_les_regles_es_v3():
    # v3 (precisio del Radar) no canvia res de la base de l'import de v2.
    assert RULES_VERSION == "metadata-match-v3"


class TestReferenceAmount:
    def test_amb_vec_mana_el_vec(self):
        ref = reference_amount(D("245079.30"), D("204232.75"), None)
        assert ref == AmountReference(D("245079.30"), AmountBasis.VEC)

    def test_vec_igual_al_pbl_es_coherent(self):
        assert reference_amount(D("1000"), D("1000.00"), None).incoherent_budget is None

    def test_sense_vec_el_pbl_sense_iva_identificat(self):
        ref = reference_amount(None, D("67500"), None)
        assert ref == AmountReference(D("67500"), AmountBasis.PBL_NET)

    def test_un_vec_zero_es_absent_com_a_v1(self):
        # No canvia el tractament general dels zeros: el pre-fit ja els tractava com a absents.
        assert reference_amount(D("0"), D("67500"), None).basis is AmountBasis.PBL_NET

    def test_nomes_pbl_amb_iva(self):
        assert reference_amount(None, None, D("121000")) == AmountReference(
            D("121000"), AmountBasis.PBL_GROSS
        )

    def test_el_pbl_sense_iva_mana_sobre_el_pbl_amb_iva(self):
        assert reference_amount(None, D("100000"), D("121000")).basis is AmountBasis.PBL_NET

    @pytest.mark.parametrize(
        "values", [(None, None, None), (D("0"), D("0"), None), (D("0"), None, D("0"))]
    )
    def test_sense_cap_import_util(self, values):
        assert reference_amount(*values) == AmountReference(None, None)

    def test_vec_inferior_al_pbl_es_incoherent(self):
        ref = reference_amount(D("3719.07"), D("45000"), None)
        assert ref.basis is AmountBasis.VEC
        assert ref.incoherent_budget == D("45000")


def _economic(amount):
    return economic.evaluate(make_profile(capacity=FULL_CAPACITY), amount)


class TestEconomicV2:
    # FULL_CAPACITY: minim 30.000, franja 80.000-600.000, recomanat 900.000, assumible 1.200.000.

    @pytest.mark.parametrize(
        ("value", "reason"),
        [
            ("10000", ReasonCode.AMOUNT_BELOW_MINIMUM),
            ("300000", ReasonCode.AMOUNT_PREFERRED),
            ("1000000", ReasonCode.AMOUNT_ABOVE_RECOMMENDED),
            ("2000000", ReasonCode.AMOUNT_ABOVE_MAXIMUM),
        ],
    )
    def test_amb_vec_coherent_el_tram_de_v1_i_la_base(self, value, reason):
        outcome = _economic(reference_amount(D(value), D(value), None))
        assert outcome.factor.reason_code == reason
        assert outcome.factor.detail["amount_basis"] == "vec"
        assert "lower_bound" not in outcome.factor.detail

    def test_un_decimal_sol_es_un_vec_com_a_v1(self):
        assert _economic(D("300000")).factor.reason_code == ReasonCode.AMOUNT_PREFERRED

    def test_pbl_per_sobre_del_maxim_exclou_amb_la_base(self):
        outcome = _economic(reference_amount(None, D("1500000"), None))
        assert outcome.excluded is True
        assert outcome.factor.reason_code == ReasonCode.AMOUNT_ABOVE_MAXIMUM
        assert outcome.factor.detail["amount_basis"] == "pbl_net"
        assert outcome.factor.detail["lower_bound"] is True
        assert outcome.factor.source_value == "1.500.000"

    @pytest.mark.parametrize(
        ("value", "reason"),
        [
            ("1000000", ReasonCode.AMOUNT_ABOVE_RECOMMENDED),
            ("700000", ReasonCode.AMOUNT_ABOVE_PREFERRED),
        ],
    )
    def test_pbl_que_supera_un_llindar_es_una_conclusio_valida(self, value, reason):
        outcome = _economic(reference_amount(D("0"), D(value), None))
        assert outcome.factor.reason_code == reason
        assert outcome.factor.result is DimensionResult.PARTIAL
        assert outcome.factor.detail == {"amount_basis": "pbl_net", "lower_bound": True}

    @pytest.mark.parametrize("value", ["300000", "50000", "10000", "600000"])
    def test_pbl_que_no_supera_cap_llindar_es_desconegut(self, value):
        # Dins la franja, per sobre del minim o per sota del minim: el VEC podria ser mes alt.
        outcome = _economic(reference_amount(None, D(value), None))
        assert outcome.is_unknown
        assert outcome.score is None
        assert outcome.excluded is False
        assert outcome.factor.reason_code == ReasonCode.AMOUNT_BUDGET_LOWER_BOUND
        assert outcome.factor.detail == {"amount_basis": "pbl_net", "lower_bound": True}

    @pytest.mark.parametrize("value", ["2000000", "1300000", "300000", "10000"])
    def test_pbl_amb_iva_no_es_cota_de_res(self, value):
        # El VEC es sense IVA: un PBL amb IVA de 1.300.000 pot ser un VEC de 1.074.380,
        # per sota del maxim assumible (1.200.000). Ni exclusio ni tram, mai.
        outcome = _economic(reference_amount(None, None, D(value)))
        assert outcome.is_unknown
        assert outcome.excluded is False
        assert outcome.factor.reason_code == ReasonCode.AMOUNT_BUDGET_WITH_TAX_ONLY
        assert outcome.factor.detail == {"amount_basis": "pbl_gross"}

    def test_un_centim_de_diferencia_es_arrodoniment_no_incoherencia(self):
        assert reference_amount(D("166300.99"), D("166301.00"), None).incoherent_budget is None
        assert reference_amount(D("166300.99"), D("166301.01"), None).incoherent_budget == D(
            "166301.01"
        )

    @pytest.mark.parametrize("values", [(D("-5"), D("300000"), None), (None, D("-300000"), None)])
    def test_els_imports_negatius_son_absents(self, values):
        ref = reference_amount(*values)
        assert ref.basis in (AmountBasis.PBL_NET, None)
        assert ref.value is None or ref.value > 0

    @pytest.mark.parametrize("vec", ["3719.07", "2000000"])
    def test_imports_incoherents_no_donen_cap_conclusio(self, vec):
        # Ni dins de la franja ni exclusio: amb VEC < PBL, una de les dues xifres falla.
        outcome = _economic(reference_amount(D(vec), D("2500000"), None))
        assert outcome.is_unknown
        assert outcome.excluded is False
        assert outcome.factor.reason_code == ReasonCode.AMOUNT_INCOHERENT
        assert outcome.factor.detail["base_budget_without_tax"] == "2500000"

    @pytest.mark.parametrize(
        "ref",
        [
            AmountReference(D("300000"), AmountBasis.VEC),
            AmountReference(D("67500"), AmountBasis.PBL_NET),
            AmountReference(D("3719.07"), AmountBasis.VEC, incoherent_budget=D("45000")),
            AmountReference(None, None),
        ],
    )
    def test_sense_capacitat_configurada_el_factor_es_identic_a_v1(self, ref):
        # El cas d'Indaleccius avui: cap preferencia d'import. Ni el motiu ni el detall canvien.
        factor = economic.evaluate(make_profile(), ref).factor.as_dict()
        assert factor["reason_code"] == ReasonCode.AMOUNT_NO_COMPANY_PREFERENCES
        assert factor["detail"] == {}
        assert factor["source_value"] is None
        assert factor["score"] is None


class TestEngineV2:
    def test_sense_vec_i_pbl_dins_la_franja_no_suma_ni_resta(self):
        # La dimensio economica queda fora del calcul: el score es el de les altres tres.
        outcome = evaluate_default(
            perfect_profile(), amount=reference_amount(None, D("300000"), None)
        )
        assert outcome.economic_score is None
        assert outcome.score == 100
        assert outcome.data_coverage == pytest.approx(0.75)

    def test_sense_vec_i_pbl_per_sobre_del_maxim_es_exclusio_de_negoci(self):
        outcome = evaluate_default(
            perfect_profile(), amount=reference_amount(None, D("5000000"), None)
        )
        assert outcome.status is MatchStatus.EXCLUDED
        assert outcome.exclusion_kind is ExclusionKind.BUSINESS
        assert outcome.exclusion_reason_code == ReasonCode.AMOUNT_ABOVE_MAXIMUM

    def test_imports_incoherents_no_exclouen(self):
        outcome = evaluate_default(
            perfect_profile(), amount=reference_amount(D("1000"), D("5000000"), None)
        )
        assert outcome.status is not MatchStatus.EXCLUDED
        assert outcome.economic_score is None

    def test_el_resultat_porta_la_versio_vigent(self):
        assert evaluate_default(perfect_profile()).rules_version == "metadata-match-v3"


def _tender(*, vec=None, net=None, gross=None, lots=()):
    return SimpleNamespace(
        cpvs=[SimpleNamespace(cpv_code="45314320", is_main=True)],
        estimated_value=vec,
        base_budget_without_tax=net,
        base_budget=gross,
        status=SimpleNamespace(value="open"),
        lots=list(lots),
        nuts_code="ES512",
        place_of_execution="Girona",
        deadline_at=NOW + timedelta(days=30),
    )


def _lot(*, vec=None, net=None, gross=None):
    return SimpleNamespace(
        removed_at=None,
        cpvs=[],
        nuts_code=None,
        place_of_execution=None,
        estimated_value=vec,
        base_budget_without_tax=net,
        base_budget=gross,
        deadline_at=None,
    )


def _economic_factor(outcome):
    return next(factor for factor in outcome.factors if factor["dimension"] == "economic")


class TestEvaluateTender:
    def test_la_licitacio_sense_vec_fa_servir_el_seu_pbl(self):
        outcome, lots = evaluate_tender(perfect_profile(), _tender(net=D("1500000")), NOW)
        assert lots == []
        assert outcome.status is MatchStatus.EXCLUDED
        assert _economic_factor(outcome)["detail"]["amount_basis"] == "pbl_net"

    def test_amb_vec_res_no_canvia(self):
        outcome, _ = evaluate_tender(
            perfect_profile(), _tender(vec=D("300000"), net=D("250000")), NOW
        )
        assert outcome.score == 100
        assert _economic_factor(outcome)["reason_code"] == ReasonCode.AMOUNT_PREFERRED

    def test_el_lot_fa_servir_el_seu_pbl_mai_el_de_lexpedient(self):
        tender = _tender(vec=D("5000000"), lots=[_lot(net=D("300000")), _lot()])
        _, lots = evaluate_tender(perfect_profile(), tender, NOW)
        first, second = (_economic_factor(outcome) for _, outcome in lots)
        assert first["reason_code"] == ReasonCode.AMOUNT_BUDGET_LOWER_BOUND
        assert first["detail"]["amount_basis"] == "pbl_net"
        # Un lot sense imports no hereta el VEC de l'expedient (5.000.000, que l'exclouria).
        assert second["reason_code"] == ReasonCode.AMOUNT_UNKNOWN

    def test_un_lot_incoherent_no_exclou_ni_hereta(self):
        # Lot amb VEC < PBL: desconegut al lot, sense mirar els imports de l'expedient.
        tender = _tender(vec=D("100000"), lots=[_lot(vec=D("1000"), net=D("5000000"))])
        _, lots = evaluate_tender(perfect_profile(), tender, NOW)
        ((_, outcome),) = lots
        assert outcome.status is not MatchStatus.EXCLUDED
        assert _economic_factor(outcome)["reason_code"] == ReasonCode.AMOUNT_INCOHERENT
