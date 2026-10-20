"""F-06e: el PBL sense IVA i el VEC de lot de la PSCP a la fitxa (`GET /tenders/{id}`).

Cada lot vigent porta, a mes del PBL amb IVA (`lots`), el PBL sense IVA
(`lots_pbl_without_tax`) i el VEC (`lots_estimated_value`), amb la mateixa regla de
seleccio. `lot_net_sum` / `lot_vec_sum` surten com a `lot_sum` (interna de la PSCP: mai
no culpa la PLACSP). L'expedient continua sent nomes el PBL amb IVA.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import sqlalchemy as sa

from app.models.enums import TenderAmountKind
from app.tenders import amount_observations
from app.tenders.amount_observations import OFFICIAL_ATTRIBUTION
from tests.test_tender_budget_observations_api import (
    ACCEPTED,
    CONFLICT,
    NO_VALUE,
    NOW,
    UID,
    _field,
    _obs,
    _tender,
)

NET = TenderAmountKind.PBL_WITHOUT_TAX
VEC = TenderAmountKind.ESTIMATED_VALUE


def _lot_obs(tender, lot, kind, **kwargs):
    values = {
        "tender_lot_id": lot.id,
        "amount_kind": kind,
        "source_entity_key": f"{UID}#lot={lot.lot_number}",
        "source_row_id": f"{UID}_{lot.lot_number}",
        "validation": {
            "v": 1,
            "level": "lot",
            "amount_kind": kind.value,
            "expedient_failed": [],
            "lot_failed": [],
        },
    }
    values.update(kwargs)
    return _obs(tender, **values)


async def test_cada_lot_porta_el_pbl_net_i_el_vec_de_la_pscp(db, client_a):
    tender, lots = await _tender(db, lots=2)
    first, second = lots
    db.add_all(
        [
            _lot_obs(tender, first, NET, amount=Decimal("36190.00")),
            _lot_obs(tender, first, VEC, amount=Decimal("72380.00")),
            _lot_obs(tender, second, VEC, amount=Decimal("0"), validation_status=NO_VALUE),
            _lot_obs(
                tender,
                second,
                NET,
                amount=Decimal("3300.00"),
                validation_status=CONFLICT,
                validation={
                    "v": 1,
                    "level": "lot",
                    "amount_kind": "pbl_without_tax",
                    "expedient_failed": [],
                    "lot_failed": ["lot_net_sum"],
                },
            ),
        ]
    )
    await db.commit()

    field = await _field(client_a, tender)
    assert field["expedient"] is None and field["lots"] == {}
    net = field["lots_pbl_without_tax"]
    vec = field["lots_estimated_value"]
    assert net[str(first.id)]["amount_kind"] == "pbl_without_tax"
    assert net[str(first.id)]["status"] == "accepted"
    assert net[str(first.id)]["amount"] == "36190.00"
    assert net[str(first.id)]["publication_url"].startswith("https://")
    assert vec[str(first.id)]["amount_kind"] == "estimated_value"
    assert vec[str(first.id)]["amount"] == "72380.00"
    assert net[str(second.id)]["status"] == "conflict"
    # La suma dels lots de la PSCP contra el seu total: el codi de sempre, `lot_sum`.
    assert net[str(second.id)]["failed_checks"] == ["lot_sum"]
    assert vec[str(second.id)]["status"] == "no_value"
    assert vec[str(second.id)]["published_zero"] is True
    # Nomes amb imports nous ja porta l'atribucio (la mateixa d'ara).
    assert field["attribution"] == OFFICIAL_ATTRIBUTION
    assert field["source_updated_at"] == "2026-10-16T06:30:00Z"


async def test_lot_vec_sum_i_el_contrast_amb_la_placsp(db, client_a):
    tender, (lot,) = await _tender(db, lots=1)
    db.add_all(
        [
            _lot_obs(
                tender,
                lot,
                VEC,
                validation_status=CONFLICT,
                validation={"v": 1, "level": "lot", "lot_failed": ["lot_vec_sum", "vec"]},
            ),
            _lot_obs(
                tender,
                lot,
                NET,
                validation_status=CONFLICT,
                validation={"v": 1, "level": "lot", "lot_failed": ["tax_order"]},
            ),
        ]
    )
    await db.commit()
    field = await _field(client_a, tender)
    assert field["lots_estimated_value"][str(lot.id)]["failed_checks"] == ["vec", "lot_sum"]
    # `tax_order` / `vec_order` no es llisten: coherencia interna de la font.
    assert field["lots_pbl_without_tax"][str(lot.id)]["failed_checks"] == []


async def test_la_mateixa_regla_de_seleccio_per_tipus(db, client_a):
    tender, (lot,) = await _tender(db, lots=1)
    later = NOW + timedelta(days=1)
    db.add_all(
        [
            _lot_obs(tender, lot, NET, validation_status=CONFLICT, source_row_id="a"),
            _lot_obs(
                tender,
                lot,
                NET,
                validation_status=NO_VALUE,
                amount=None,
                source_row_id="b",
                valid_from=later,
            ),
            _lot_obs(
                tender,
                lot,
                NET,
                validation_status=ACCEPTED,
                source_row_id="tancada",
                valid_to=NOW + timedelta(minutes=1),
            ),
        ]
    )
    await db.commit()
    field = await _field(client_a, tender)
    assert field["lots_pbl_without_tax"][str(lot.id)]["status"] == "conflict"
    assert field["lots_estimated_value"] == {}


async def test_els_tipus_nous_mai_no_son_l_expedient_ni_d_un_lot_retirat(db, client_a):
    tender, (lot, gone) = await _tender(db, lots=2)
    gone.removed_at = NOW
    db.add_all(
        [
            _obs(tender, amount_kind=NET, source_row_id="expedient-net"),
            _lot_obs(tender, gone, VEC),
            _lot_obs(tender, lot, VEC, tender_lot_id=None, source_row_id=f"{UID}_9"),
        ]
    )
    await db.commit()
    field = await _field(client_a, tender)
    assert field["expedient"] is None
    assert field["lots_estimated_value"] == {} and field["lots_pbl_without_tax"] == {}
    assert field["attribution"] is None


async def test_una_sola_consulta_amb_els_tres_tipus(db):
    tender, lots = await _tender(db, lots=3)
    db.add_all([_lot_obs(tender, lot, kind) for lot in lots for kind in (NET, VEC)])
    await db.commit()
    statements: list[str] = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    engine = db.bind.sync_engine if hasattr(db.bind, "sync_engine") else db.bind
    sa.event.listen(engine, "before_cursor_execute", count)
    try:
        result = await amount_observations.load(db, tender.id, {lot.id for lot in lots})
    finally:
        sa.event.remove(engine, "before_cursor_execute", count)
    assert len(result.lots_pbl_without_tax) == 3 and len(result.lots_estimated_value) == 3
    assert len([s for s in statements if "tender_amount_observations" in s]) == 1


async def test_el_motiu_d_un_conflicte_nou_es_el_que_l_ha_bloquejat(db, client_a):
    """Ronda 1: un VEC en conflict per la seva guarda no diu «PBL sense IVA», encara que la
    guarda `net` de l'expedient tambe falli (es desa, pero no el bloqueja)."""
    tender, (lot,) = await _tender(db, lots=1)
    db.add_all(
        [
            _lot_obs(
                tender,
                lot,
                VEC,
                validation_status=CONFLICT,
                validation={
                    "v": 1,
                    "level": "lot",
                    "amount_kind": "estimated_value",
                    "expedient_failed": ["net", "vec"],
                    "lot_failed": [],
                    "blocking_failed": ["expedient.vec"],
                },
            ),
            _lot_obs(
                tender,
                lot,
                NET,
                validation_status=CONFLICT,
                validation={
                    "v": 1,
                    "level": "lot",
                    "amount_kind": "pbl_without_tax",
                    "expedient_failed": ["lot_sum", "net"],
                    "lot_failed": ["lot_net_sum"],
                    "blocking_failed": ["expedient.net", "lot.lot_net_sum"],
                },
            ),
        ]
    )
    await db.commit()
    field = await _field(client_a, tender)
    assert field["lots_estimated_value"][str(lot.id)]["failed_checks"] == ["vec"]
    assert field["lots_pbl_without_tax"][str(lot.id)]["failed_checks"] == [
        "pbl_without_tax",
        "lot_sum",
    ]
