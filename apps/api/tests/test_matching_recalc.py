"""F-06b: el recalcul controlat del pre-fit (dry-run, registre, idempotencia i recuperacio)."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.matching import recalc
from app.matching.service import MatchingService
from app.models.matching import CompanyTenderLotMatch, CompanyTenderMatch
from tests.test_matching_api import add_lot, configure_company, make_source, make_tender


async def _prepare(db, company_id, *, capacity: bool = True):
    """Tres licitacions: amb VEC, sense VEC (nomes PBL sense IVA) i una amb lots."""
    source = await make_source(db)
    with_vec = await make_tender(db, source, title="Amb VEC", amount="300000")
    without_vec = await make_tender(db, source, title="Sense VEC", amount=None)
    without_vec.base_budget_without_tax = Decimal("300000")
    with_lots = await make_tender(db, source, title="Amb lots", amount="500000")
    await add_lot(db, with_lots, number=1, title="Lot 1", amount="200000")
    await add_lot(db, with_lots, number=2, title="Lot 2")
    await db.commit()
    await configure_company(db, company_id, capacity=capacity)
    # L'estat «v1» que hi ha avui al pilot: el factor economic tal com l'escrivia v1
    # (sense base; sense VEC, `AMOUNT_UNKNOWN`) i marcat amb les regles anteriors.
    await MatchingService(db).recalculate(company_id)
    for model in (CompanyTenderMatch, CompanyTenderLotMatch):
        for row in (await db.execute(sa.select(model))).scalars():
            row.factors = [_as_v1(factor) for factor in row.factors]
            row.rules_version = "metadata-match-v1"
    await db.commit()
    return with_vec.id, without_vec.id, with_lots.id


def _as_v1(factor: dict) -> dict:
    if factor["dimension"] != "economic":
        return factor
    detail = {k: v for k, v in factor["detail"].items() if k not in {"amount_basis", "lower_bound"}}
    if factor["reason_code"] == "AMOUNT_BUDGET_LOWER_BOUND":
        # v1 no mirava el PBL sense IVA: l'import era desconegut.
        return {
            **factor,
            "reason_code": "AMOUNT_UNKNOWN",
            "company_value": None,
            "source_value": None,
            "detail": {},
        }
    return {**factor, "detail": detail}


def _lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestRecalc:
    async def test_dry_run_no_escriu_res_i_ho_registra_tot(self, db, tenant_a, tmp_path):
        company_id = tenant_a[1].id
        await _prepare(db, company_id)
        before = await recalc.snapshot(db, company_id)

        result = await recalc.recalculate(db, company_id, tmp_path / "dry", dry_run=True)

        assert await recalc.snapshot(db, company_id) == before
        assert result["mode"] == "dry-run"
        assert result["persisted_equals_after"] is None
        matches = result["tables"]["company_tender_matches"]
        assert matches["rows_before"] == matches["rows_after"] == 3
        assert matches["updated"] == 3
        for name in ("before.json", "before.sha256", "after.json", "changes.jsonl", "summary.json"):
            assert (tmp_path / "dry" / name).exists()
        assert (tmp_path / "dry" / "before.sha256").read_text().strip() == recalc.digest(before)

    async def test_amb_vec_nomes_canvia_la_versio(self, db, tenant_a, tmp_path):
        company_id = tenant_a[1].id
        with_vec, without_vec, _ = await _prepare(db, company_id)

        result = await recalc.recalculate(db, company_id, tmp_path / "run", dry_run=False)

        assert result["persisted_equals_after"] is True
        by_tender = {
            change["tender_id"]: change
            for change in _lines(tmp_path / "run" / "changes.jsonl")
            if change["table"] == "company_tender_matches"
        }
        vec_fields = set(by_tender[str(with_vec)]["fields"])
        # Amb VEC el resultat es el de v1: nomes la versio, el detall de la base i la data.
        assert vec_fields <= {"rules_version", "factors", "calculated_at", "updated_at"}
        old, new = by_tender[str(with_vec)]["fields"]["factors"]
        old_economic = next(f for f in old if f["dimension"] == "economic")
        new_economic = next(f for f in new if f["dimension"] == "economic")
        assert new_economic["reason_code"] == old_economic["reason_code"]
        assert new_economic["detail"] == {"amount_basis": "vec"}
        # Sense VEC: el PBL dins la franja no es cap conclusio (abans tambe era desconegut).
        transitions = result["tables"]["company_tender_matches"]["economic_reason_transitions"]
        assert transitions == {"AMOUNT_UNKNOWN -> AMOUNT_BUDGET_LOWER_BOUND": 1}
        # Desconegut abans i despres: ni la puntuacio ni l'estat no es mouen.
        without_vec_fields = by_tender[str(without_vec)]["fields"]
        assert {"score", "status", "economic_score", "data_coverage"}.isdisjoint(without_vec_fields)

    async def test_sense_capacitat_no_canvia_cap_resultat(self, db, tenant_a, tmp_path):
        # El cas d'Indaleccius: sense preferencies d'import, nomes canvia la versio.
        company_id = tenant_a[1].id
        await _prepare(db, company_id, capacity=False)

        result = await recalc.recalculate(db, company_id, tmp_path / "run", dry_run=False)

        for name in recalc.TABLES:
            table = result["tables"][name]
            assert set(table["fields_changed"]) <= {"rules_version", "calculated_at", "updated_at"}
            assert table["status_transitions"] == {}
            assert table["economic_reason_transitions"] == {}
            assert table["rows_by_strongest_category"] == {"version": table["updated"]}
            assert table["created"] == table["deleted"] == 0

    async def test_es_idempotent(self, db, tenant_a, tmp_path):
        company_id = tenant_a[1].id
        await _prepare(db, company_id)
        await recalc.recalculate(db, company_id, tmp_path / "first", dry_run=False)
        after_first = await recalc.snapshot(db, company_id)

        second = await recalc.recalculate(db, company_id, tmp_path / "second", dry_run=False)

        assert await recalc.snapshot(db, company_id) == after_first
        for name in recalc.TABLES:
            assert second["tables"][name]["updated"] == 0
        assert (tmp_path / "second" / "changes.jsonl").read_text() == ""

    async def test_restaurar_torna_exactament_a_lestat_anterior(self, db, tenant_a, tmp_path):
        company_id = tenant_a[1].id
        await _prepare(db, company_id)
        before = await recalc.snapshot(db, company_id)
        await recalc.recalculate(db, company_id, tmp_path / "run", dry_run=False)
        assert await recalc.snapshot(db, company_id) != before

        dry = await recalc.restore(db, company_id, tmp_path / "run", dry_run=True)
        assert dry["restored_equals_export"] is True
        assert await recalc.snapshot(db, company_id) != before  # el dry-run no ho confirma

        applied = await recalc.restore(db, company_id, tmp_path / "run", dry_run=False)
        assert applied["mode"] == "applied"
        # Columna a columna, dates incloses.
        assert await recalc.snapshot(db, company_id) == before

    async def test_una_exportacio_manipulada_no_es_restaura(self, db, tenant_a, tmp_path):
        company_id = tenant_a[1].id
        await _prepare(db, company_id)
        await recalc.recalculate(db, company_id, tmp_path / "run", dry_run=False)
        current = await recalc.snapshot(db, company_id)
        export = tmp_path / "run" / "before.json"
        data = json.loads(export.read_text(encoding="utf-8"))
        data["company_tender_matches"][0]["score"] = 1
        export.write_text(json.dumps(data), encoding="utf-8")

        with pytest.raises(SystemExit):
            await recalc.restore(db, company_id, tmp_path / "run", dry_run=False)
        assert await recalc.snapshot(db, company_id) == current

    async def test_no_trepitja_una_evidencia_existent(self, db, tenant_a, tmp_path):
        company_id = tenant_a[1].id
        await _prepare(db, company_id)
        (tmp_path / "run").mkdir()
        with pytest.raises(FileExistsError):
            await recalc.recalculate(db, company_id, tmp_path / "run", dry_run=True)

    async def test_nomes_toca_lempresa_indicada(self, db, tenant_a, tenant_b, tmp_path):
        company_a_id = tenant_a[1].id
        company_b_id = tenant_b[1].id
        await _prepare(db, company_a_id)
        await configure_company(db, company_b_id)
        await MatchingService(db).recalculate(company_b_id)
        other = await recalc.snapshot(db, company_b_id)

        await recalc.recalculate(db, company_a_id, tmp_path / "run", dry_run=False)
        await recalc.restore(db, company_a_id, tmp_path / "run", dry_run=False)

        assert await recalc.snapshot(db, company_b_id) == other

    async def test_restaurar_conserva_el_text_exacte_del_json(self, db, tenant_a, tmp_path):
        # A PostgreSQL `factors` es `json` (desa el text tal com arriba): restaurar-lo
        # tornant-lo a serialitzar en canviaria l'ordre de les claus.
        company_id = tenant_a[1].id
        await _prepare(db, company_id)
        table = CompanyTenderMatch.__table__
        odd = '[ {"z": 1,  "dimension": "economic", "a": [2, 1]} ]'
        await db.execute(sa.update(table).values(factors=sa.type_coerce(sa.literal(odd), sa.Text)))
        await db.commit()

        async def raw_texts():
            rows = await db.execute(sa.select(sa.cast(table.c.factors, sa.Text)))
            return sorted(rows.scalars())

        before = await raw_texts()
        await recalc.recalculate(db, company_id, tmp_path / "run", dry_run=False)
        assert await raw_texts() != before
        await recalc.restore(db, company_id, tmp_path / "run", dry_run=False)
        assert await raw_texts() == before == [odd] * 3
