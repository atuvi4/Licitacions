"""Tests del motor de pre-fit.

El motor es pur: aquests tests no necessiten ni base de dades ni HTTP.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.matching import cpv, economic, engine, geography, timing
from app.matching.profile import (
    CPVPreference,
    CompanyMatchProfile,
    ContractCapacity,
    TerritoryPreference,
    normalize_place,
)
from app.matching.rules import RULES_VERSION, ReasonCode
from app.models.enums import DimensionResult, ExclusionKind, MatchStatus

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def territory(name: str, code: str | None, priority: int, willing: bool = True):
    return TerritoryPreference(
        name=name,
        normalized_name=normalize_place(name),
        territory_type="province",
        code=code,
        priority=priority,
        willing_to_work=willing,
    )


def make_profile(
    *,
    cpvs: list[tuple[str, int, bool, bool]] | None = None,
    territories: list[TerritoryPreference] | None = None,
    capacity: ContractCapacity | None = None,
) -> CompanyMatchProfile:
    return CompanyMatchProfile(
        company_id=uuid.uuid4(),
        cpv_preferences=[
            CPVPreference(code=code, priority=priority, active=active, excluded=excluded)
            for code, priority, active, excluded in (cpvs or [])
        ],
        territories=territories or [],
        capacity=capacity or ContractCapacity(),
    )


FULL_CAPACITY = ContractCapacity(
    minimum_interesting=Decimal("30000"),
    preferred_min=Decimal("80000"),
    preferred_max=Decimal("600000"),
    maximum_recommended=Decimal("900000"),
    maximum_assumable=Decimal("1200000"),
)


# ---------------------------------------------------------------------------
# CPV
# ---------------------------------------------------------------------------


class TestCPV:
    def test_coincidencia_exacta_amb_prioritat_alta(self):
        profile = make_profile(cpvs=[("45314320", 10, True, False)])
        outcome = cpv.evaluate(profile, ["45314320"])
        assert outcome.factor.reason_code == ReasonCode.CPV_EXACT_HIGH_PRIORITY
        assert outcome.factor.result is DimensionResult.MATCH
        assert outcome.score == 40  # tot el pes

    def test_coincidencia_exacta_amb_prioritat_baixa_puntua_menys(self):
        alta = cpv.evaluate(make_profile(cpvs=[("45314320", 10, True, False)]), ["45314320"])
        baixa = cpv.evaluate(make_profile(cpvs=[("45314320", 1, True, False)]), ["45314320"])
        assert baixa.score < alta.score
        assert baixa.factor.reason_code == ReasonCode.CPV_EXACT_LOW_PRIORITY

    @pytest.mark.parametrize(
        ("tender_code", "expected"),
        [
            ("45314320", ReasonCode.CPV_EXACT_HIGH_PRIORITY),
            ("45314399", ReasonCode.CPV_RELATED_CATEGORY),   # comparteix "453143"
            ("45314900", ReasonCode.CPV_RELATED_CATEGORY),   # comparteix "45314"
            ("45319000", ReasonCode.CPV_RELATED_CLASS),      # comparteix "4531"
            ("45350000", ReasonCode.CPV_RELATED_GROUP),      # comparteix "453"
            ("45900000", ReasonCode.CPV_RELATED_DIVISION),   # comparteix "45"
            ("77000000", ReasonCode.CPV_NO_OVERLAP),         # res en comu
        ],
    )
    def test_jerarquia_per_prefix_del_codi(self, tender_code, expected):
        """La jerarquia surt de l'estructura del codi, no de cap cataleg."""
        profile = make_profile(cpvs=[("45314320", 10, True, False)])
        assert cpv.evaluate(profile, [tender_code]).factor.reason_code == expected

    def test_com_mes_a_prop_de_lexacte_mes_punts(self):
        profile = make_profile(cpvs=[("45314320", 10, True, False)])
        scores = [
            cpv.evaluate(profile, [code]).score
            for code in ("45314320", "45314900", "45319000", "45350000", "45900000")
        ]
        assert scores == sorted(scores, reverse=True)

    def test_cpv_desactivat_puntua_poc_pero_no_exclou(self):
        profile = make_profile(cpvs=[("45314320", 10, False, False)])
        outcome = cpv.evaluate(profile, ["45314320"])
        assert outcome.excluded is False
        assert outcome.factor.reason_code == ReasonCode.CPV_INACTIVE_ONLY
        assert 0 < (outcome.score or 0) < 40

    def test_cpv_exclos_es_una_exclusio_de_negoci(self):
        profile = make_profile(cpvs=[("45000000", 0, True, True)])
        outcome = cpv.evaluate(profile, ["45000000"])
        assert outcome.excluded is True
        assert outcome.factor.reason_code == ReasonCode.CPV_EXCLUDED

    def test_un_cpv_exclos_no_tomba_una_coincidencia_bona(self):
        """Si el mateix expedient encaixa amb un CPV valid, l'exclos no mana."""
        profile = make_profile(
            cpvs=[("45000000", 0, True, True), ("45314320", 10, True, False)]
        )
        outcome = cpv.evaluate(profile, ["45000000", "45314320"])
        assert outcome.excluded is False
        assert outcome.factor.reason_code == ReasonCode.CPV_EXACT_HIGH_PRIORITY

    def test_sense_cpv_a_lempresa_es_desconegut_no_incompatible(self):
        outcome = cpv.evaluate(make_profile(), ["45314320"])
        assert outcome.is_unknown
        assert outcome.factor.reason_code == ReasonCode.CPV_NO_COMPANY_PREFERENCES

    def test_sense_cpv_a_la_licitacio_es_desconegut(self):
        profile = make_profile(cpvs=[("45314320", 10, True, False)])
        outcome = cpv.evaluate(profile, [])
        assert outcome.is_unknown
        assert outcome.factor.reason_code == ReasonCode.CPV_UNKNOWN

    def test_el_digit_de_control_no_afecta(self):
        profile = make_profile(cpvs=[("45314320", 10, True, False)])
        assert cpv.evaluate(profile, ["45314320-1"]).factor.reason_code == (
            ReasonCode.CPV_EXACT_HIGH_PRIORITY
        )


# ---------------------------------------------------------------------------
# Territori
# ---------------------------------------------------------------------------


class TestGeography:
    def test_territori_de_prioritat_alta(self):
        profile = make_profile(territories=[territory("Girona", "ES512", 10)])
        outcome = geography.evaluate(profile, "ES512", "Girona")
        assert outcome.factor.reason_code == ReasonCode.TERRITORY_HIGH_PRIORITY
        assert outcome.score == 25

    def test_el_nuts_es_jerarquic(self):
        """`ES51` (Catalunya) ha de cobrir `ES512` (Girona)."""
        profile = make_profile(territories=[territory("Catalunya", "ES51", 6)])
        outcome = geography.evaluate(profile, "ES512", "Girona")
        assert outcome.factor.result is DimensionResult.MATCH
        assert outcome.factor.detail["matched_by"] == "nuts"

    def test_coincidencia_per_nom_quan_no_hi_ha_codi(self):
        profile = make_profile(territories=[territory("Girona", None, 10)])
        outcome = geography.evaluate(profile, "ES512", "Girona")
        assert outcome.factor.detail["matched_by"] == "name"

    def test_nom_bilingue_de_la_font(self):
        profile = make_profile(territories=[territory("Valencia", None, 10)])
        outcome = geography.evaluate(profile, "ES523", "Valencia/València")
        assert outcome.factor.result is DimensionResult.MATCH

    def test_mana_el_territori_de_mes_prioritat(self):
        profile = make_profile(
            territories=[territory("Catalunya", "ES51", 6), territory("Girona", "ES512", 10)]
        )
        outcome = geography.evaluate(profile, "ES512", "Girona")
        assert outcome.factor.company_value == "Girona"

    def test_territori_rebutjat_explicitament_exclou(self):
        profile = make_profile(territories=[territory("Girona", "ES512", 0, willing=False)])
        outcome = geography.evaluate(profile, "ES512", "Girona")
        assert outcome.excluded is True
        assert outcome.factor.reason_code == ReasonCode.TERRITORY_EXCLUDED

    def test_territori_no_declarat_no_es_una_exclusio(self):
        """Declarar preferencies no es prohibir la resta."""
        profile = make_profile(territories=[territory("Girona", "ES512", 10)])
        outcome = geography.evaluate(profile, "ES618", "Sevilla")
        assert outcome.excluded is False
        assert outcome.factor.reason_code == ReasonCode.TERRITORY_NOT_DECLARED
        assert 0 < (outcome.score or 0) < 25

    def test_sense_dades_de_la_licitacio_es_desconegut(self):
        profile = make_profile(territories=[territory("Girona", "ES512", 10)])
        outcome = geography.evaluate(profile, None, None)
        assert outcome.is_unknown
        assert outcome.factor.reason_code == ReasonCode.TERRITORY_UNKNOWN

    def test_sense_territoris_a_lempresa_es_desconegut(self):
        outcome = geography.evaluate(make_profile(), "ES512", "Girona")
        assert outcome.is_unknown


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


class TestEconomic:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            ("10000", ReasonCode.AMOUNT_BELOW_MINIMUM),
            ("50000", ReasonCode.AMOUNT_INTERESTING),
            ("300000", ReasonCode.AMOUNT_PREFERRED),
            ("700000", ReasonCode.AMOUNT_ABOVE_PREFERRED),
            ("1000000", ReasonCode.AMOUNT_ABOVE_RECOMMENDED),
            ("2000000", ReasonCode.AMOUNT_ABOVE_MAXIMUM),
        ],
    )
    def test_trams(self, amount, expected):
        profile = make_profile(capacity=FULL_CAPACITY)
        assert economic.evaluate(profile, Decimal(amount)).factor.reason_code == expected

    def test_per_sobre_del_maxim_assumible_exclou(self):
        profile = make_profile(capacity=FULL_CAPACITY)
        outcome = economic.evaluate(profile, Decimal("2000000"))
        assert outcome.excluded is True
        # Es capacitat interna, no solvencia legal.
        assert outcome.factor.reason_code == ReasonCode.AMOUNT_ABOVE_MAXIMUM

    def test_els_punts_baixen_a_mesura_que_creix_limport(self):
        profile = make_profile(capacity=FULL_CAPACITY)
        scores = [
            economic.evaluate(profile, Decimal(value)).score
            for value in ("300000", "700000", "1000000")
        ]
        assert scores == sorted(scores, reverse=True)

    def test_import_desconegut_no_penalitza(self):
        outcome = economic.evaluate(make_profile(capacity=FULL_CAPACITY), None)
        assert outcome.is_unknown
        assert outcome.score is None

    def test_sense_capacitat_configurada_es_desconegut(self):
        outcome = economic.evaluate(make_profile(), Decimal("300000"))
        assert outcome.is_unknown


# ---------------------------------------------------------------------------
# Termini
# ---------------------------------------------------------------------------


class TestTiming:
    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (40, ReasonCode.DEADLINE_COMFORTABLE),
            (15, ReasonCode.DEADLINE_ADEQUATE),
            (7, ReasonCode.DEADLINE_SHORT),
            (2, ReasonCode.DEADLINE_CRITICAL),
        ],
    )
    def test_bandes(self, days, expected):
        outcome = timing.evaluate("open", NOW + timedelta(days=days, hours=1), NOW)
        assert outcome.factor.reason_code == expected
        assert outcome.excluded is False

    def test_termini_vencut_exclou(self):
        outcome = timing.evaluate("open", NOW - timedelta(days=1), NOW)
        assert outcome.excluded is True
        assert outcome.factor.reason_code == ReasonCode.DEADLINE_EXPIRED

    @pytest.mark.parametrize("status", ["awarded", "resolved", "cancelled"])
    def test_estats_no_accionables(self, status):
        outcome = timing.evaluate(status, NOW + timedelta(days=30), NOW)
        assert outcome.excluded is True
        assert outcome.factor.reason_code == ReasonCode.STATUS_NOT_OPEN

    def test_sense_termini_es_desconegut_i_compta_zero(self):
        # v3: la dada continua sent desconeguda, pero compta 0 de 10 (no surt del calcul).
        outcome = timing.evaluate("open", None, NOW)
        assert outcome.is_unknown
        assert outcome.counts_as_zero
        assert outcome.score == 0.0
        assert outcome.factor.reason_code == ReasonCode.DEADLINE_UNKNOWN

    def test_termini_curt_no_vol_dir_impossible(self):
        outcome = timing.evaluate("open", NOW + timedelta(days=2), NOW)
        assert outcome.excluded is False
        assert (outcome.score or 0) > 0


# ---------------------------------------------------------------------------
# Motor complet
# ---------------------------------------------------------------------------


def perfect_profile() -> CompanyMatchProfile:
    return make_profile(
        cpvs=[("45314320", 10, True, False)],
        territories=[territory("Girona", "ES512", 10)],
        capacity=FULL_CAPACITY,
    )


def evaluate_default(profile, **overrides):
    payload = {
        "cpv_codes": ["45314320"],
        "nuts_code": "ES512",
        "place_name": "Girona",
        "amount": Decimal("300000"),
        "tender_status": "open",
        "deadline": NOW + timedelta(days=30),
        "now": NOW,
    }
    payload.update(overrides)
    return engine.evaluate(profile, **payload)


class TestEngine:
    def test_encaix_perfecte(self):
        outcome = evaluate_default(perfect_profile())
        assert outcome.score == 100
        assert outcome.status is MatchStatus.CANDIDATE
        assert outcome.data_coverage == 1.0
        assert len(outcome.factors) == 4

    def test_els_factors_porten_reason_code_estructurat(self):
        outcome = evaluate_default(perfect_profile())
        for factor in outcome.factors:
            assert factor["reason_code"]
            assert factor["dimension"] in {"cpv", "geography", "economic", "timing"}
            assert factor["max_score"] > 0

    def test_una_dimensio_desconeguda_no_compta_com_a_zero(self):
        """Sense import, un encaix perfecte segueix sent perfecte."""
        outcome = evaluate_default(perfect_profile(), amount=None)
        assert outcome.score == 100
        assert outcome.data_coverage == 0.75
        assert outcome.economic_score is None

    def test_massa_dades_desconegudes_no_es_un_score_dolent(self):
        profile = make_profile(cpvs=[("45314320", 10, True, False)])
        outcome = evaluate_default(profile, amount=None, deadline=None)
        assert outcome.status is MatchStatus.INSUFFICIENT_DATA
        assert outcome.score is None

    def test_sense_cpv_afi_no_pot_ser_candidata(self):
        """Ubicacio i import perfectes no fan rellevant una licitacio d'un altre sector."""
        outcome = evaluate_default(perfect_profile(), cpv_codes=["77000000"])
        assert outcome.status is MatchStatus.LOW_FIT
        assert outcome.score is not None

    def test_coincidencia_de_divisio_tampoc_basta(self):
        outcome = evaluate_default(perfect_profile(), cpv_codes=["45900000"])
        assert outcome.status is MatchStatus.LOW_FIT

    def test_estat_tancat_mana_sobre_tot(self):
        outcome = evaluate_default(perfect_profile(), tender_status="awarded")
        assert outcome.status is MatchStatus.CLOSED
        assert outcome.exclusion_kind is ExclusionKind.STATUS

    def test_exclusio_de_negoci(self):
        profile = make_profile(
            cpvs=[("45314320", 10, True, False)],
            territories=[territory("Girona", "ES512", 10)],
            capacity=FULL_CAPACITY,
        )
        outcome = evaluate_default(profile, amount=Decimal("5000000"))
        assert outcome.status is MatchStatus.EXCLUDED
        assert outcome.exclusion_kind is ExclusionKind.BUSINESS
        assert outcome.exclusion_reason_code == ReasonCode.AMOUNT_ABOVE_MAXIMUM

    def test_lestat_mana_sobre_lexclusio_de_negoci(self):
        outcome = evaluate_default(
            perfect_profile(), tender_status="awarded", amount=Decimal("5000000")
        )
        assert outcome.status is MatchStatus.CLOSED

    def test_el_resultat_es_reproduible(self):
        """Mateix input, mateix resultat: cap aleatorietat, cap IA."""
        profile = perfect_profile()
        first = evaluate_default(profile)
        second = evaluate_default(profile)
        assert first.score == second.score
        assert first.status == second.status
        assert [f["reason_code"] for f in first.factors] == [
            f["reason_code"] for f in second.factors
        ]
        assert first.rules_version == RULES_VERSION

    def test_el_resum_dun_expedient_es_el_millor_lot(self):
        """Mai una mitjana: amagaria justament el lot que interessa."""
        profile = perfect_profile()
        bo = evaluate_default(profile)
        dolent = evaluate_default(profile, cpv_codes=["77000000"])
        assert engine.aggregate_lots([dolent, bo]).score == bo.score
        assert engine.aggregate_lots([dolent, bo]).score > (dolent.score or 0)

    def test_perfils_diferents_donen_resultats_diferents(self):
        catalana = perfect_profile()
        andalusa = make_profile(
            cpvs=[("80000000", 10, True, False)],
            territories=[territory("Sevilla", "ES618", 10)],
            capacity=FULL_CAPACITY,
        )
        alta = evaluate_default(catalana)
        baixa = evaluate_default(andalusa)
        assert alta.score is not None and baixa.score is not None
        assert alta.score > baixa.score + 40


class TestExplainability:
    """Cada codi de motiu ha de tenir una frase que un huma entengui.

    El valor del pre-fit no es el numero, es poder dir per que. Un codi sense
    etiqueta al frontend apareixeria en brut a la pantalla de l'usuari.
    """

    def test_tots_els_codis_tenen_etiqueta_al_frontend(self):
        from pathlib import Path

        from app.matching import rules

        web = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "matching.ts"
        if not web.exists():  # pragma: no cover - nomes si es prova l'API sola
            pytest.skip("frontend no disponible")

        labels = set(re.findall(r"^  ([A-Z][A-Z_]+):", web.read_text(encoding="utf-8"), re.M))
        codes = {value for name, value in vars(rules.ReasonCode).items() if name.isupper()}
        assert codes - labels == set(), "codis sense text llegible al frontend"

    def test_cap_terme_prohibit_en_aquesta_fase(self):
        """Ni 'probabilitat de guanyar' ni Go/No-Go: encara no hem llegit els plecs."""
        from pathlib import Path

        from app.models.enums import MatchStatus

        assert "go" not in {status.value for status in MatchStatus}
        assert "no_go" not in {status.value for status in MatchStatus}

        root = Path(__file__).resolve().parents[1] / "app" / "matching"
        forbidden = ("probabilitat de guanyar", "win_probability", "go_no_go")
        for path in root.glob("*.py"):
            text = path.read_text(encoding="utf-8").lower()
            for term in forbidden:
                assert term not in text, f"{path.name} conte '{term}'"


class TestCausaDeLesDadesInsuficients:
    """Contracte amb el web (auditoria UI P1-02): el pre-fit distingeix, pels motius dels
    factors, «el perfil no té la dada» de «la licitació no la publica». El web en treu la
    causa (`insufficientCause`); si aquests codis canvien, el web l'atribuiria malament."""

    # Els factors es desen serialitzats: codis (constants de text) i resultats en text.
    PROFILE_REASONS = {
        ReasonCode.CPV_NO_COMPANY_PREFERENCES,
        ReasonCode.TERRITORY_NO_COMPANY_DATA,
        ReasonCode.AMOUNT_NO_COMPANY_PREFERENCES,
    }
    SOURCE_REASONS = {
        ReasonCode.CPV_UNKNOWN,
        ReasonCode.TERRITORY_UNKNOWN,
        ReasonCode.AMOUNT_UNKNOWN,
    }

    def _unknown_reasons(self, outcome) -> set[str]:
        return {
            factor["reason_code"]
            for factor in outcome.factors
            if factor["result"] == DimensionResult.UNKNOWN.value
        }

    def test_perfil_buit_i_licitacio_completa_nomes_motius_de_perfil(self):
        outcome = evaluate_default(make_profile())
        assert outcome.status is MatchStatus.INSUFFICIENT_DATA
        assert self._unknown_reasons(outcome) == self.PROFILE_REASONS

    def test_perfil_complet_i_licitacio_sense_metadades_nomes_motius_de_font(self):
        outcome = evaluate_default(
            perfect_profile(), cpv_codes=[], nuts_code=None, place_name=None, amount=None
        )
        assert outcome.status is MatchStatus.INSUFFICIENT_DATA
        assert self._unknown_reasons(outcome) == self.SOURCE_REASONS
