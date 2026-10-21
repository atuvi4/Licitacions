"""Imports a zero (`0.00`): una sola regla a tot arreu (decisio del propietari, 19-10-2026).

Un import economic (`estimated_value`, `base_budget_without_tax`, `base_budget`, de
la licitacio o d'un lot) nomes es util si es estrictament mes gran que 0. El `0.00`
que publica la PLACSP es conserva a la base i a la resposta crua de l'API, pero no
es fa servir mai com a import: ni per filtrar, ni per ordenar, ni com a maxim d'una
oferta, ni com a base d'un llindar percentual. El pre-fit ja ho feia aixi i no canvia.

Els quatre patrons reals del pilot (diagnosi del 19-10-2026):

* VEC > 0 i PBL sense IVA = 0 (228): es filtra pel VEC, sense incoherencia.
* VEC = 0 i PBL sense IVA > 0 (46): es filtra i s'ordena pel PBL sense IVA.
* VEC = 0 i PBL sense IVA = 0 (38): sense import conegut.
* VEC NULL i PBL sense IVA = 0 (7): sense import conegut.

Els tests de cataleg corren a SQLite i, amb `--postgres`, a PostgreSQL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.api.v1.tender_filters import amount_expression
from app.compliance.percentages import resolve_threshold
from app.compliance.specs import Threshold
from app.matching.economic import AmountBasis, AmountReference, reference_amount
from app.matching.rules import RULES_VERSION, ReasonCode
from app.matching.service import evaluate_tender
from app.models.enums import (
    ContractType,
    MatchStatus,
    PercentageBase,
    ProcedureType,
    SourceType,
    TenderStatus,
)
from app.models.sources import Source
from app.models.tenders import Tender
from app.offer.pricing import exceeds_lot_max, lot_limit
from app.tenders.amounts import useful_amount, useful_amount_sql
from tests.test_matching_engine import perfect_profile

D = Decimal
NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# L'ajudant                                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (D("1"), D("1")),
        (D("0.01"), D("0.01")),
        (D("204232.75"), D("204232.75")),
        ("204232.75", D("204232.75")),
        (1500, D("1500")),
        (D("0"), None),
        (D("0.00"), None),
        ("0.00", None),
        (0, None),
        (D("-5"), None),
        (None, None),
        ("", None),
        ("no", None),
        (True, None),
        (D("NaN"), None),
        (D("Infinity"), None),
    ],
)
def test_useful_amount(value, expected):
    assert useful_amount(value) == expected


async def test_useful_amount_sql_es_la_mateixa_regla(db):
    expr = useful_amount_sql(sa.literal(D("0.00"), sa.Numeric(18, 2)))
    assert (await db.execute(sa.select(expr))).scalar_one() is None
    expr = useful_amount_sql(sa.literal(D("-3"), sa.Numeric(18, 2)))
    assert (await db.execute(sa.select(expr))).scalar_one() is None
    expr = useful_amount_sql(sa.literal(D("12.50"), sa.Numeric(18, 2)))
    assert D(str((await db.execute(sa.select(expr))).scalar_one())) == D("12.50")
    expr = useful_amount_sql(sa.null())
    assert (await db.execute(sa.select(expr))).scalar_one() is None


# --------------------------------------------------------------------------- #
# Cataleg: filtre, ordre i «sense import conegut»                               #
# --------------------------------------------------------------------------- #


async def _amount_tender(db, source, title, *, vec=None, net=None, gross=None, days=0):
    tender = Tender(
        source_id=source.id,
        external_id=f"urn:zero:{uuid.uuid4().hex[:10]}",
        title=title,
        procedure_type=ProcedureType.OPEN,
        contract_type=ContractType.SERVICES,
        status=TenderStatus.OPEN,
        estimated_value=D(vec) if vec is not None else None,
        base_budget_without_tax=D(net) if net is not None else None,
        base_budget=D(gross) if gross is not None else None,
        currency="EUR",
        publication_date=date(2026, 10, 1) - timedelta(days=days),
        first_seen_at=NOW - timedelta(days=days),
        last_seen_at=NOW - timedelta(days=days),
    )
    db.add(tender)
    await db.flush()
    return tender


@pytest.fixture
async def zeros(db):
    source = Source(
        code="PLACSP",
        name="PLACSP",
        source_type=SourceType.SYNDICATION_ATOM,
        enabled=True,
    )
    db.add(source)
    await db.flush()
    rows = {
        # VEC > 0 i PBL 0: el VEC, sense incoherencia.
        "vec_pbl0": await _amount_tender(
            db, source, "VEC i PBL zero", vec="50000", net="0", days=1
        ),
        # VEC 0 i PBL > 0: el PBL sense IVA.
        "vec0_pbl": await _amount_tender(
            db, source, "VEC zero i PBL", vec="0", net="30000", days=2
        ),
        # Tots dos a 0: sense import.
        "vec0_pbl0": await _amount_tender(db, source, "Tot a zero", vec="0", net="0", days=3),
        # VEC NULL i PBL 0: sense import.
        "null_pbl0": await _amount_tender(db, source, "Nul i zero", vec=None, net="0", days=4),
        # Nomes PBL amb IVA a zero i un PBL sense IVA util: el PBL sense IVA.
        "gross0": await _amount_tender(db, source, "Brut zero", net="10000", gross="0", days=5),
        # Referencia: sense cap import.
        "cap": await _amount_tender(db, source, "Sense cap import", days=6),
    }
    await db.commit()
    return SimpleNamespace(**{key: str(value.id) for key, value in rows.items()})


async def _page(client, **params) -> dict:
    response = await client.get("/api/v1/tenders", params={"limit": 100, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def _ids(client, **params) -> list[str]:
    return [row["id"] for row in (await _page(client, **params))["items"]]


async def test_l_expressio_d_import_salta_els_zeros(db, zeros):
    rows = (
        await db.execute(sa.select(Tender.id, amount_expression()).order_by(Tender.title))
    ).all()
    got = {
        str(tender_id): (D(str(amount)) if amount is not None else None)
        for tender_id, amount in rows
    }
    assert got[zeros.vec_pbl0] == D("50000")
    assert got[zeros.vec0_pbl] == D("30000")
    assert got[zeros.vec0_pbl0] is None
    assert got[zeros.null_pbl0] is None
    assert got[zeros.gross0] == D("10000")
    assert got[zeros.cap] is None


async def test_el_filtre_d_import_salta_els_zeros(client_a, zeros):
    # VEC 0 i PBL 30.000: abans valia 0 i quedava fora de qualsevol minim.
    assert set(await _ids(client_a, min_amount=20000)) == {zeros.vec_pbl0, zeros.vec0_pbl}
    # Un maxim no fa entrar mai els zeros com si fossin «barats».
    assert set(await _ids(client_a, max_amount=40000)) == {zeros.vec0_pbl, zeros.gross0}
    assert await _ids(client_a, min_amount=0, max_amount=0) == []


async def test_el_recompte_sense_import_conegut_compta_els_zeros(client_a, zeros):
    body = await _page(client_a, min_amount=1)
    assert body["excluded_without_amount"] == 3  # tot a zero, nul i zero, cap import
    assert {row["id"] for row in body["items"]} == {zeros.vec_pbl0, zeros.vec0_pbl, zeros.gross0}
    assert body["total"] == 3
    # Sense filtre d'import no s'exclou res i les dades crues es conserven.
    unfiltered = await _page(client_a)
    assert unfiltered["excluded_without_amount"] is None
    assert unfiltered["total"] == 6
    raw = {row["id"]: row for row in unfiltered["items"]}
    assert D(raw[zeros.vec0_pbl0]["estimated_value"]) == D("0")
    assert D(raw[zeros.vec0_pbl0]["base_budget_without_tax"]) == D("0")


async def test_el_radar_compta_els_zeros_com_a_sense_import_conegut(client_a, tenant_a, zeros):
    """`/companies/{id}/matches` (Radar) comparteix el recompte i l'ordre del cataleg."""
    _, company = tenant_a
    url = f"/api/v1/companies/{company.id}/matches"
    response = await client_a.get(url, params={"limit": 100, "min_amount": 1})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["excluded_without_amount"] == 3  # tot a zero, nul i zero, cap import
    assert {item["tender"]["id"] for item in body["items"]} == {
        zeros.vec_pbl0,
        zeros.vec0_pbl,
        zeros.gross0,
    }
    unfiltered = (await client_a.get(url, params={"limit": 100})).json()
    assert unfiltered["excluded_without_amount"] is None
    ordered = (await client_a.get(url, params={"limit": 100, "sort": "amount"})).json()
    ids = [item["tender"]["id"] for item in ordered["items"]]
    assert ids[:3] == [zeros.vec_pbl0, zeros.vec0_pbl, zeros.gross0]


async def test_l_ordre_per_import_salta_els_zeros(client_a, zeros):
    ordered = await _ids(client_a, sort="amount")
    # Amb import util, de mes gran a mes petit; sense, al final per l'ordre neutral.
    assert ordered[:3] == [zeros.vec_pbl0, zeros.vec0_pbl, zeros.gross0]
    assert ordered[3:] == [zeros.vec0_pbl0, zeros.null_pbl0, zeros.cap]


# --------------------------------------------------------------------------- #
# Pre-fit: cap canvi de regla                                                   #
# --------------------------------------------------------------------------- #


def _prefit_tender(*, vec=None, net=None, gross=None):
    return SimpleNamespace(
        cpvs=[SimpleNamespace(cpv_code="45314320", is_main=True)],
        estimated_value=vec,
        base_budget_without_tax=net,
        base_budget=gross,
        status=SimpleNamespace(value="open"),
        lots=[],
        nuts_code="ES512",
        place_of_execution="Girona",
        deadline_at=NOW + timedelta(days=30),
    )


def _economic(outcome):
    return next(factor for factor in outcome.factors if factor["dimension"] == "economic")


class TestPrefitAmbZeros:
    def test_la_versio_de_les_regles_es_la_vigent(self):
        # Els imports zero no la van pujar; la v3 (precisio del Radar) si.
        assert RULES_VERSION == "metadata-match-v3"

    def test_vec_zero_i_pbl_es_la_cota_inferior(self):
        assert reference_amount(D("0.00"), D("300000.00"), None) == AmountReference(
            D("300000.00"), AmountBasis.PBL_NET
        )
        outcome, _ = evaluate_tender(
            perfect_profile(), _prefit_tender(vec=D("0.00"), net=D("300000.00")), NOW
        )
        factor = _economic(outcome)
        assert factor["reason_code"] == ReasonCode.AMOUNT_BUDGET_LOWER_BOUND
        assert factor["detail"]["amount_basis"] == "pbl_net"

    def test_vec_zero_i_pbl_per_sobre_del_maxim_exclou_amb_la_base(self):
        outcome, _ = evaluate_tender(
            perfect_profile(), _prefit_tender(vec=D("0.00"), net=D("5000000.00")), NOW
        )
        assert outcome.status is MatchStatus.EXCLUDED
        assert _economic(outcome)["detail"]["amount_basis"] == "pbl_net"

    @pytest.mark.parametrize(
        ("vec", "net"), [(D("0.00"), D("0.00")), (None, D("0.00")), (D("0.00"), None)]
    )
    def test_sense_cap_import_util_es_desconegut(self, vec, net):
        assert reference_amount(vec, net, None) == AmountReference(None, None)
        outcome, _ = evaluate_tender(perfect_profile(), _prefit_tender(vec=vec, net=net), NOW)
        assert _economic(outcome)["reason_code"] == ReasonCode.AMOUNT_UNKNOWN

    def test_vec_i_pbl_zero_es_el_vec_sense_incoherencia(self):
        ref = reference_amount(D("300000.00"), D("0.00"), None)
        assert ref == AmountReference(D("300000.00"), AmountBasis.VEC)
        assert ref.incoherent_budget is None
        outcome, _ = evaluate_tender(
            perfect_profile(), _prefit_tender(vec=D("300000.00"), net=D("0.00")), NOW
        )
        factor = _economic(outcome)
        assert factor["reason_code"] == ReasonCode.AMOUNT_PREFERRED
        assert factor["detail"]["amount_basis"] == "vec"


# --------------------------------------------------------------------------- #
# Oferta: un maxim de 0 no es un maxim                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("maximum", [D("0"), D("0.00"), D("-1")])
def test_un_maxim_zero_queda_indeterminat(maximum):
    assert exceeds_lot_max(D("1000"), maximum) is None
    limit = lot_limit(D("1000"), maximum)
    assert limit.tender_max is None
    assert limit.difference_to_maximum is None
    assert limit.exceeds_lot_max is None


def test_un_maxim_util_continua_igual():
    limit = lot_limit(D("1000"), D("800"))
    assert limit.tender_max == D("800")
    assert limit.exceeds_lot_max is True
    assert lot_limit(None, D("800")).exceeds_lot_max is None


# --------------------------------------------------------------------------- #
# Compliment: un llindar percentual sobre una base de 0                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("base", "field"),
    [
        (PercentageBase.ESTIMATED_VALUE, "estimated_value"),
        (PercentageBase.BASE_BUDGET, "base_budget"),
    ],
)
def test_un_llindar_sobre_una_base_zero_no_es_resol(base, field):
    threshold = Threshold(percentage=D("70"), percentage_base=base)
    zero = SimpleNamespace(estimated_value=D("0.00"), base_budget=D("0.00"), currency="EUR")
    resolution = resolve_threshold(threshold, zero)
    assert resolution.amount is None
    assert resolution.reason == f"{field}_not_available"
    useful = SimpleNamespace(estimated_value=D("1000"), base_budget=D("1000"), currency="EUR")
    assert resolve_threshold(threshold, useful).amount == D("700")
