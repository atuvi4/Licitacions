"""F-06e: el resolutor comu dels imports de lot i els seus dos consumidors de calcul.

* Resolutor (`app.tenders.lot_amounts`): PLACSP > 0, si no l'observacio de la PSCP vigent
  i `accepted` del mateix lot i tipus amb import > 0, si no res. Mai no escriu; una
  consulta per a N lots.
* Oferta economica (`_lot_maximums`): amb lot del plec, el maxim es el PBL sense IVA
  resolt; `conflict` / `no_value` -> indeterminat (mai «supera»).
* Pre-fit per lot (F-06e no va canviar `RULES_VERSION`; ara es `metadata-match-v3`): els imports
  resolts del lot amb la mateixa `reference_amount` / `evaluate`.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.ingestion.pscp_open_data import SOURCE_CODE
from app.ingestion.pscp_open_data import guards as g
from app.matching.rules import RULES_VERSION, ReasonCode
from app.matching.service import MatchingService, evaluate_tender
from app.models.enums import AmountObservationStatus as S
from app.models.enums import MatchStatus
from app.models.enums import TenderAmountKind as K
from app.models.matching import CompanyTenderLotMatch
from app.models.tender_amounts import TenderAmountObservation
from app.models.tenders import TenderLot
from app.tenders import lot_amounts
from app.tenders.lot_amounts import (
    NO_AMOUNT,
    LotAmounts,
    ResolvedAmount,
    load_lot_amounts,
    resolve,
)
from tests.test_economic_offer_api import (
    _lots,
    _tender,
    _workspace,
    a_workspace_with_one_line,
    only_lot,
    read,
)
from tests.test_matching_amount_basis import _economic_factor
from tests.test_matching_amount_basis import _lot as _pure_lot
from tests.test_matching_amount_basis import _tender as _pure_tender
from tests.test_matching_api import add_lot, configure_company, make_source, make_tender
from tests.test_matching_engine import make_profile, perfect_profile
from tests.test_pscp_open_data import CASES, FakeSoda, _run, _seed, _snapshot, _source_rows

D = Decimal
NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
COLUMN = {
    K.ESTIMATED_VALUE: "estimated_value",
    K.PBL_WITHOUT_TAX: "base_budget_without_tax",
    K.PBL_WITH_TAX: "base_budget",
}


async def observe(
    db,
    lot: TenderLot,
    kind: K,
    amount: str | None,
    *,
    status: S = S.ACCEPTED,
    closed: bool = False,
    source_code: str = SOURCE_CODE,
    row: str | None = None,
) -> TenderAmountObservation:
    """Una observacio de la PSCP d'un lot, com les que escriu l'adaptador."""
    moment = datetime.now(UTC)
    observation = TenderAmountObservation(
        tender_id=lot.tender_id,
        tender_lot_id=lot.id,
        amount_kind=kind,
        amount=None if amount is None else Decimal(amount),
        source_code=source_code,
        source_entity_key=f"uuid#lot={lot.lot_number}",
        source_row_id=row or f"uuid_{lot.lot_number}",
        retrieved_at=moment,
        evidence_url="https://analisi.transparenciacatalunya.cat/resource/ybgg-dgi6.json",
        evidence_sha256="0" * 64,
        validation_status=status,
        validation={"level": "lot", "amount_kind": kind.value},
        provenance={"rows": []},
        valid_from=moment - timedelta(days=1),
        valid_to=moment if closed else None,
    )
    db.add(observation)
    await db.commit()
    return observation


# --------------------------------------------------------------------------- #
# Resolutor                                                                     #
# --------------------------------------------------------------------------- #


def _obs(kind, amount, *, status=S.ACCEPTED, valid_to=None, valid_from=NOW):
    return SimpleNamespace(
        id=uuid.uuid4(),
        amount_kind=kind,
        amount=None if amount is None else D(amount),
        validation_status=status,
        valid_to=valid_to,
        valid_from=valid_from,
    )


def _lot(**columns):
    values = {"id": uuid.uuid4(), "estimated_value": None, "base_budget_without_tax": None}
    values["base_budget"] = None
    values.update(columns)
    return SimpleNamespace(**values)


class TestResolve:
    def test_la_placsp_util_guanya(self):
        vec = _obs(K.ESTIMATED_VALUE, "500")
        resolved = resolve(_lot(estimated_value=D("100")), {K.ESTIMATED_VALUE: [vec]})
        assert resolved.estimated_value == ResolvedAmount(D("100"), "placsp")

    def test_sense_placsp_la_pscp_accepted_amb_l_observacio(self):
        net = _obs(K.PBL_WITHOUT_TAX, "36190")
        resolved = resolve(_lot(base_budget_without_tax=D("0.00")), {K.PBL_WITHOUT_TAX: [net]})
        assert resolved.base_budget_without_tax == ResolvedAmount(D("36190"), "pscp", net.id)
        assert resolved.estimated_value == NO_AMOUNT and resolved.base_budget == NO_AMOUNT

    @pytest.mark.parametrize(
        "observation",
        [
            _obs(K.ESTIMATED_VALUE, "500", status=S.CONFLICT),
            _obs(K.ESTIMATED_VALUE, "0", status=S.NO_VALUE),
            _obs(K.ESTIMATED_VALUE, None, status=S.NO_VALUE),
            _obs(K.ESTIMATED_VALUE, "500", status=S.SUPERSEDED),
            _obs(K.ESTIMATED_VALUE, "500", valid_to=NOW),
            _obs(K.ESTIMATED_VALUE, "0"),
        ],
    )
    def test_cap_altra_observacio_no_es_un_import(self, observation):
        assert resolve(_lot(), {K.ESTIMATED_VALUE: [observation]}) == LotAmounts()

    def test_cada_import_del_seu_tipus(self):
        gross = _obs(K.PBL_WITH_TAX, "121")
        resolved = resolve(_lot(), {K.PBL_WITH_TAX: [gross]})
        assert resolved.base_budget.value == D("121")
        assert resolved.estimated_value == NO_AMOUNT
        assert resolved.base_budget_without_tax == NO_AMOUNT


async def test_load_lot_amounts_una_consulta_i_cap_escriptura(db, engine):
    tender = await _tender(db)
    lots = await _lots(db, tender, budgets=(None, None, D("7000")))
    vec = await observe(db, lots[0], K.ESTIMATED_VALUE, "1000")
    net = await observe(db, lots[0], K.PBL_WITHOUT_TAX, "800")
    await observe(db, lots[1], K.PBL_WITHOUT_TAX, "900", status=S.CONFLICT)
    await observe(db, lots[1], K.ESTIMATED_VALUE, "0", status=S.NO_VALUE)
    await observe(db, lots[1], K.PBL_WITH_TAX, "1210", closed=True, row="antiga")
    await observe(db, lots[1], K.PBL_WITH_TAX, "999", source_code="altra-font")
    await observe(db, lots[2], K.PBL_WITHOUT_TAX, "6500")  # la PLACSP (7000) guanya
    count = await db.scalar(sa.select(sa.func.count()).select_from(TenderAmountObservation))

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    sa.event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        resolved = await load_lot_amounts(db, lots)
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert [s for s in statements if "tender_amount_observations" in s] == statements
    assert len(statements) == 1  # una sola consulta per als tres lots
    assert not any(
        s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements
    )
    first, second, third = (resolved[lot.id] for lot in lots)
    assert first.estimated_value == ResolvedAmount(D("1000.00"), "pscp", vec.id)
    assert first.base_budget_without_tax == ResolvedAmount(D("800.00"), "pscp", net.id)
    assert first.base_budget == NO_AMOUNT
    assert second == LotAmounts()
    assert third.base_budget_without_tax == ResolvedAmount(D("7000.00"), "placsp")
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderAmountObservation)) == (
        count
    )


async def test_load_lot_amounts_per_trams(db, monkeypatch):
    monkeypatch.setattr(lot_amounts, "CHUNK_SIZE", 2)
    tender = await _tender(db)
    lots = await _lots(db, tender, budgets=(None,) * 5)
    for lot in lots:
        await observe(db, lot, K.ESTIMATED_VALUE, str(1000 + (lot.lot_number or 0)))
    resolved = await load_lot_amounts(db, lots)
    assert [resolved[lot.id].estimated_value.value for lot in lots] == [
        D(1000 + n) for n in range(1, 6)
    ]


# --------------------------------------------------------------------------- #
# Oferta economica per lot                                                      #
# --------------------------------------------------------------------------- #


async def _lot_offer(db, company, *, lot_budget=None, tender_budget=D("999999")):
    tender = await _tender(db, budget=tender_budget)
    lots = await _lots(db, tender, budgets=(lot_budget, None))
    workspace = await _workspace(db, company, tender, tender_lot_id=lots[0].id)
    return workspace, lots[0]


async def test_el_maxim_del_lot_es_el_pbl_net_de_la_pscp_accepted(client_a, db, tenant_a):
    _user, company = tenant_a
    workspace, lot = await _lot_offer(db, company)
    await observe(db, lot, K.PBL_WITHOUT_TAX, "5000")
    await observe(db, lot, K.ESTIMATED_VALUE, "90000")  # mai el VEC
    await observe(db, lot, K.PBL_WITH_TAX, "6050")  # ni el PBL amb IVA
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")
    limit = only_lot(await read(client_a, workspace))["limit"]
    assert Decimal(limit["tender_max"]) == Decimal("5000")
    assert limit["exceeds_lot_max"] is False
    assert Decimal(limit["difference_to_maximum"]) == Decimal("4000")


async def test_una_oferta_per_sobre_del_pbl_net_de_la_pscp_el_supera(client_a, db, tenant_a):
    _user, company = tenant_a
    workspace, lot = await _lot_offer(db, company)
    await observe(db, lot, K.PBL_WITHOUT_TAX, "500")
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")
    limit = only_lot(await read(client_a, workspace))["limit"]
    assert limit["exceeds_lot_max"] is True
    assert Decimal(limit["difference_to_maximum"]) == Decimal("-500")


async def test_la_placsp_del_lot_guanya_a_la_pscp(client_a, db, tenant_a):
    _user, company = tenant_a
    workspace, lot = await _lot_offer(db, company, lot_budget=D("7000"))
    await observe(db, lot, K.PBL_WITHOUT_TAX, "5000")
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")
    limit = only_lot(await read(client_a, workspace))["limit"]
    assert Decimal(limit["tender_max"]) == Decimal("7000")


@pytest.mark.parametrize(
    ("amount", "status"), [("5000", S.CONFLICT), ("0", S.NO_VALUE), (None, S.NO_VALUE)]
)
async def test_conflict_o_no_value_deixen_el_maxim_indeterminat(
    client_a, db, tenant_a, amount, status
):
    _user, company = tenant_a
    workspace, lot = await _lot_offer(db, company, lot_budget=D("0.00"))
    await observe(db, lot, K.PBL_WITHOUT_TAX, amount, status=status)
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")
    limit = only_lot(await read(client_a, workspace))["limit"]
    # Ni el PBL de l'expedient (999.999) ni la xifra en conflicte: indeterminat.
    assert limit["tender_max"] is None
    assert limit["exceeds_lot_max"] is None
    assert limit["difference_to_maximum"] is None


async def test_sense_lot_del_plec_el_maxim_es_el_de_la_licitacio(client_a, db, tenant_a):
    _user, company = tenant_a
    tender = await _tender(db, budget=D("8000"))
    workspace = await _workspace(db, company, tender)
    await a_workspace_with_one_line(client_a, workspace, offered_unit_price_ex_vat="1000")
    limit = only_lot(await read(client_a, workspace))["limit"]
    assert Decimal(limit["tender_max"]) == Decimal("8000")


# --------------------------------------------------------------------------- #
# Pre-fit per lot                                                               #
# --------------------------------------------------------------------------- #


def test_la_versio_de_les_regles_es_la_vigent():
    # F-06e no la va pujar; la v3 (precisio del Radar) si.
    assert RULES_VERSION == "metadata-match-v3"


def _resolved(**values):
    return LotAmounts(
        **{
            column: ResolvedAmount(D(value), "pscp", uuid.uuid4())
            for column, value in values.items()
        }
    )


def _lot_factor(profile, lot, resolved):
    tender = _pure_tender(vec=D("5000000"), lots=[lot])
    _, lots = evaluate_tender(profile, tender, NOW, {lot.id: resolved})
    ((_, outcome),) = lots
    return outcome, _economic_factor(outcome)


def _plain_lot(**values):
    lot = _pure_lot(**values)
    lot.id = uuid.uuid4()
    return lot


class TestPrefitPerLot:
    def test_vec_pscp_accepted_dona_el_tram(self):
        outcome, factor = _lot_factor(
            perfect_profile(), _plain_lot(), _resolved(estimated_value="300000")
        )
        assert factor["reason_code"] == ReasonCode.AMOUNT_PREFERRED
        assert factor["detail"]["amount_basis"] == "vec"

    def test_nomes_pbl_net_pscp_es_cota_inferior(self):
        _, factor = _lot_factor(
            perfect_profile(), _plain_lot(), _resolved(base_budget_without_tax="300000")
        )
        assert factor["reason_code"] == ReasonCode.AMOUNT_BUDGET_LOWER_BOUND
        assert factor["detail"]["amount_basis"] == "pbl_net"
        outcome, factor = _lot_factor(
            perfect_profile(), _plain_lot(), _resolved(base_budget_without_tax="1500000")
        )
        assert factor["reason_code"] == ReasonCode.AMOUNT_ABOVE_MAXIMUM
        assert outcome.status is MatchStatus.EXCLUDED

    def test_un_vec_en_conflict_per_vec_order_es_incoherent_mai_cota_inferior(self):
        """M-5: la draft real de l'adaptador (VEC < PBL net -> `conflict` per `vec_order`)
        dona `AMOUNT_INCOHERENT`, com a l'expedient; mai el net com a cota inferior."""
        rows = copy.deepcopy(CASES["lots"]["rows"])
        lot_rows = [row for row in rows if len(row["id_intern"]) > 36]
        lot_rows[2]["pressupost_licitacio_sense"] = "1500000"  # net del lot: 1.500.000
        lot_rows[2]["valor_estimat_contracte"] = "1000"  # VEC < net
        ev = g.evaluate(_snapshot("lots"), rows)
        [vec] = [
            d
            for d in ev.lot_amount_drafts
            if d.amount_kind == K.ESTIMATED_VALUE and d.source_row_id == lot_rows[2]["id_intern"]
        ]
        assert vec.status == S.CONFLICT and "lot.vec_order" in vec.validation["blocking_failed"]
        observation = SimpleNamespace(
            id=uuid.uuid4(),
            amount_kind=K.ESTIMATED_VALUE,
            amount=vec.amount,
            validation_status=vec.status,
            validation=json.loads(json.dumps(vec.validation)),
            valid_to=None,
            valid_from=NOW,
        )
        net = _obs(K.PBL_WITHOUT_TAX, "1500000")
        resolved = resolve(_lot(), {K.ESTIMATED_VALUE: [observation], K.PBL_WITHOUT_TAX: [net]})
        assert resolved.estimated_value == NO_AMOUNT
        assert resolved.vec_incoherent == (D("1000.00"), D("1500000.00"))
        outcome, factor = _lot_factor(perfect_profile(), _plain_lot(), resolved)
        assert factor["reason_code"] == ReasonCode.AMOUNT_INCOHERENT
        assert outcome.status is not MatchStatus.EXCLUDED
        # Sense la incoherencia, el mateix net (1.500.000) excloiria com a cota inferior.
        _, without = _lot_factor(
            perfect_profile(), _plain_lot(), _resolved(base_budget_without_tax="1500000")
        )
        assert without["reason_code"] == ReasonCode.AMOUNT_ABOVE_MAXIMUM

    def test_un_vec_de_la_placsp_o_accepted_mana_sobre_la_incoherencia(self):
        lot = _lot(estimated_value=D("300000"))
        conflict = SimpleNamespace(
            id=uuid.uuid4(),
            amount_kind=K.ESTIMATED_VALUE,
            amount=D("1000"),
            validation_status=S.CONFLICT,
            validation={
                "blocking_failed": ["lot.vec_order"],
                "lot_guards": {"vec_order": {"ok": False, "high": "1000.00", "low": "5000.00"}},
            },
            valid_to=None,
            valid_from=NOW,
        )
        assert resolve(lot, {K.ESTIMATED_VALUE: [conflict]}).vec_incoherent is None

    def test_nomes_pbl_amb_iva_no_es_cota_de_res(self):
        _, factor = _lot_factor(perfect_profile(), _plain_lot(), _resolved(base_budget="300000"))
        assert factor["reason_code"] == ReasonCode.AMOUNT_BUDGET_WITH_TAX_ONLY

    def test_sense_cap_import_resolt_es_desconegut_i_no_hereta_l_expedient(self):
        _, factor = _lot_factor(perfect_profile(), _plain_lot(), LotAmounts())
        assert factor["reason_code"] == ReasonCode.AMOUNT_UNKNOWN

    def test_sense_capacitat_res_no_canvia(self):
        _, factor = _lot_factor(make_profile(), _plain_lot(), _resolved(estimated_value="300000"))
        assert factor["reason_code"] == ReasonCode.AMOUNT_NO_COMPANY_PREFERENCES

    def test_sense_mapa_el_lot_es_resol_amb_les_seves_columnes(self):
        lot = _plain_lot(net=D("300000"))
        tender = _pure_tender(vec=D("5000000"), lots=[lot])
        _, lots = evaluate_tender(perfect_profile(), tender, NOW)
        ((_, outcome),) = lots
        assert _economic_factor(outcome)["reason_code"] == ReasonCode.AMOUNT_BUDGET_LOWER_BOUND


async def _lot_matches(db, company_id) -> dict[uuid.UUID, CompanyTenderLotMatch]:
    db.expire_all()
    rows = (
        await db.execute(
            sa.select(CompanyTenderLotMatch).where(CompanyTenderLotMatch.company_id == company_id)
        )
    ).scalars()
    return {row.tender_lot_id: row for row in rows}


def _reason(match: CompanyTenderLotMatch) -> str:
    return next(f for f in match.factors if f["dimension"] == "economic")["reason_code"]


async def test_el_recalcul_fa_servir_els_imports_resolts_del_lot(db, tenant_a):
    _user, company = tenant_a
    source = await make_source(db)
    tender = await make_tender(db, source, title="Amb lots", amount="5000000")
    vec_lot = await add_lot(db, tender, number=1, title="VEC PSCP")
    net_lot = await add_lot(db, tender, number=2, title="Net PSCP")
    conflict_lot = await add_lot(db, tender, number=4, title="Conflicte")
    empty_lot = await add_lot(db, tender, number=5, title="Publicat a 0")
    placsp_lot = await add_lot(db, tender, number=6, title="PLACSP", amount="300000")
    await db.commit()
    await observe(db, vec_lot, K.ESTIMATED_VALUE, "300000")
    await observe(db, net_lot, K.PBL_WITHOUT_TAX, "1500000")
    await observe(db, conflict_lot, K.ESTIMATED_VALUE, "300000", status=S.CONFLICT)
    await observe(db, empty_lot, K.ESTIMATED_VALUE, "0", status=S.NO_VALUE)
    await observe(db, placsp_lot, K.ESTIMATED_VALUE, "2000000")  # la PLACSP (300.000) guanya

    await configure_company(db, company.id, capacity=True)
    await MatchingService(db).recalculate(company.id, tender_ids=[tender.id])
    matches = await _lot_matches(db, company.id)
    assert _reason(matches[vec_lot.id]) == ReasonCode.AMOUNT_PREFERRED
    assert _reason(matches[net_lot.id]) == ReasonCode.AMOUNT_ABOVE_MAXIMUM
    assert _reason(matches[conflict_lot.id]) == ReasonCode.AMOUNT_UNKNOWN
    assert _reason(matches[empty_lot.id]) == ReasonCode.AMOUNT_UNKNOWN
    assert _reason(matches[placsp_lot.id]) == ReasonCode.AMOUNT_PREFERRED
    assert {m.rules_version for m in matches.values()} == {"metadata-match-v3"}


async def test_sense_capacitat_el_recalcul_no_canvia(db, tenant_a):
    _user, company = tenant_a
    source = await make_source(db)
    tender = await make_tender(db, source, title="Amb lots", amount="5000000")
    lot = await add_lot(db, tender, number=1, title="VEC PSCP")
    await db.commit()
    await observe(db, lot, K.ESTIMATED_VALUE, "300000")
    await configure_company(db, company.id, capacity=False)
    await MatchingService(db).recalculate(company.id, tender_ids=[tender.id])
    matches = await _lot_matches(db, company.id)
    assert _reason(matches[lot.id]) == ReasonCode.AMOUNT_NO_COMPANY_PREFERENCES


async def test_el_pre_fit_d_un_lot_amb_vec_incoherent_surt_de_l_adaptador(
    db, session_factory, tenant_a, tmp_path
):
    """M-5 de punta a punta: l'adaptador desa el VEC en `conflict` (`vec_order`) i el net
    `accepted`; el recalcul del pre-fit en dona `AMOUNT_INCOHERENT`."""
    _user, company = tenant_a
    ids = await _seed(db, ["lots"])
    source = _source_rows(["lots"])
    uid = CASES["lots"]["ours"]["pscp_id_intern"]
    lot_rows = [row for row in source[uid] if len(row["id_intern"]) > 36]
    lot_rows[2]["valor_estimat_contracte"] = "20000"  # PBL net del lot: 20.020
    lot_rows[0]["valor_estimat_contracte"] = "36210"  # la suma del VEC no canvia
    code, report = await _run(session_factory, FakeSoda(source), tmp_path)
    assert code == 0, report.get("error")
    lots = {
        lot.lot_number: lot
        for lot in (
            await db.execute(sa.select(TenderLot).where(TenderLot.tender_id == ids["lots"]))
        ).scalars()
    }
    number = int(lot_rows[2]["numero_lot"])
    resolved = await load_lot_amounts(db, list(lots.values()))
    assert resolved[lots[number].id].vec_incoherent == (D("20000.00"), D("20020.00"))
    assert resolved[lots[number].id].base_budget_without_tax.value == D("20020.00")
    await configure_company(db, company.id, capacity=True)
    await MatchingService(db).recalculate(company.id, tender_ids=[ids["lots"]])
    matches = await _lot_matches(db, company.id)
    assert _reason(matches[lots[number].id]) == ReasonCode.AMOUNT_INCOHERENT
    others = [lot for n, lot in lots.items() if n != number]
    assert all(_reason(matches[lot.id]) != ReasonCode.AMOUNT_INCOHERENT for lot in others)
