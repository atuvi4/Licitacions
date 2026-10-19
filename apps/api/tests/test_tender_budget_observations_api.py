"""El PBL amb IVA de la PSCP a la fitxa (F-06a, fase 3): `GET /tenders/{id}`.

Es prova el camp `budget_with_tax_observations` de la fitxa: quina observacio vigent
s'ensenya per a l'expedient i per a cada lot, que `superseded` no surt mai, la regla de
precedencia (accepted > conflict > no_value, despres `valid_from` mes recent, despres
`id`), que una fila de lot de la font sense lot nostre no es l'expedient ni cap lot, el
mapatge de les comprovacions que fallen i que la resta de la fitxa no canvia. Nomes
lectura: cap import de `tenders` no es toca.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.ingestion.pscp_open_data import ATTRIBUTION as ADAPTER_ATTRIBUTION
from app.ingestion.pscp_open_data import SOURCE_CODE
from app.models.enums import AmountObservationStatus, TenderAmountKind
from app.models.sources import Source
from app.models.tender_amounts import TenderAmountObservation
from app.models.tenders import Tender, TenderLot
from app.tenders import amount_observations
from app.tenders.amount_observations import OFFICIAL_ATTRIBUTION

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
DATASET_UPDATED = datetime(2026, 10, 16, 6, 30, tzinfo=UTC)
SHA = "b" * 64
UID = "0f1e2d3c-4b5a-4968-8776-655443322110"
LINK = "https://contractaciopublica.cat/ca/detall-publicacio/" + UID + "/300001"

ACCEPTED = AmountObservationStatus.ACCEPTED
CONFLICT = AmountObservationStatus.CONFLICT
NO_VALUE = AmountObservationStatus.NO_VALUE
SUPERSEDED = AmountObservationStatus.SUPERSEDED


def _provenance(link: str | None = LINK, rows_updated_at: str | None = None) -> dict:
    """La forma que desa l'adaptador (`service._provenance`), retallada."""
    return {
        "dataset": "ybgg-dgi6",
        "rows_updated_at": rows_updated_at,
        "field": "pressupost_licitacio_amb_1",
        "published_value": "247121.63",
        "rows": [
            {
                "id_intern": UID,
                "codi_organ": "12345",
                "fase_publicacio": "Anunci de licitacio",
                "numero_lot": None,
                "enllac_publicacio": link,
                "publication_id": "300001",
            }
        ],
        "licence": {"name": "Llicencia", "attribution": "Font: ..."},
    }


async def _tender(db, lots: int = 0, **kwargs) -> tuple[Tender, list[TenderLot]]:
    source = Source(code=f"bo-{uuid.uuid4().hex[:8]}", name="Font de prova")
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id, external_id=f"exp-{uuid.uuid4().hex[:8]}", title="T", **kwargs
    )
    db.add(tender)
    await db.flush()
    created = [
        TenderLot(tender_id=tender.id, external_lot_id=str(n), lot_number=n)
        for n in range(1, lots + 1)
    ]
    db.add_all(created)
    await db.flush()
    return tender, created


def _obs(tender: Tender, **kwargs) -> TenderAmountObservation:
    values = {
        "id": uuid.uuid4(),
        "tender_id": tender.id,
        "amount_kind": TenderAmountKind.PBL_WITH_TAX,
        "amount": Decimal("247121.63"),
        "source_code": SOURCE_CODE,
        "source_entity_key": UID,
        "source_row_id": UID,
        "source_dataset_updated_at": DATASET_UPDATED,
        "retrieved_at": NOW,
        "evidence_url": "https://analisi.transparenciacatalunya.cat/resource/ybgg-dgi6.json",
        "evidence_sha256": SHA,
        "validation_status": ACCEPTED,
        "validation": {"v": 1, "level": "expedient", "failed": []},
        "valid_from": NOW,
        "provenance": _provenance(),
    }
    values.update(kwargs)
    return TenderAmountObservation(**values)


async def _detail(client, tender: Tender) -> dict:
    response = await client.get(f"/api/v1/tenders/{tender.id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _field(client, tender: Tender) -> dict:
    return (await _detail(client, tender))["budget_with_tax_observations"]


# --------------------------------------------------------------------------- #
# Expedient                                                                     #
# --------------------------------------------------------------------------- #


async def test_sense_observacions_el_camp_es_buit(db, client_a):
    tender, _ = await _tender(db, lots=1)
    await db.commit()
    assert await _field(client_a, tender) == {
        "expedient": None,
        "lots": {},
        "attribution": None,
        "source_updated_at": None,
    }


async def test_accepted_d_expedient(db, client_a):
    tender, _ = await _tender(db)
    db.add(_obs(tender))
    await db.commit()

    field = await _field(client_a, tender)
    assert field["expedient"] == {
        "status": "accepted",
        "amount": "247121.63",
        "currency": "EUR",
        "published_zero": False,
        "failed_checks": [],
        "evaluation_error": False,
        "publication_url": LINK,
        "source_updated_at": "2026-10-16T06:30:00Z",
        "retrieved_at": "2026-10-16T12:00:00Z",
        "source_code": SOURCE_CODE,
    }
    assert field["lots"] == {}
    assert field["attribution"] == OFFICIAL_ATTRIBUTION
    assert field["source_updated_at"] == "2026-10-16T06:30:00Z"


async def test_no_value_publicat_a_0_i_nul(db, client_a):
    zero, _ = await _tender(db)
    null, _ = await _tender(db)
    db.add_all(
        [
            _obs(zero, validation_status=NO_VALUE, amount=Decimal("0")),
            _obs(null, validation_status=NO_VALUE, amount=None, provenance=_provenance(None)),
        ]
    )
    await db.commit()

    published_zero = (await _field(client_a, zero))["expedient"]
    assert published_zero["status"] == "no_value"
    assert Decimal(published_zero["amount"]) == 0
    assert published_zero["published_zero"] is True
    assert published_zero["failed_checks"] == []

    published_null = await _field(client_a, null)
    assert published_null["expedient"]["status"] == "no_value"
    assert published_null["expedient"]["amount"] is None
    assert published_null["expedient"]["published_zero"] is False
    assert published_null["expedient"]["publication_url"] is None
    # Una observacio ensenyada (tambe un `no_value` nul) porta l'atribucio.
    assert published_null["attribution"] == OFFICIAL_ATTRIBUTION


async def test_conflict_d_expedient_amb_les_comprovacions_que_fallen(db, client_a):
    tender, _ = await _tender(db)
    db.add(
        _obs(
            tender,
            validation_status=CONFLICT,
            amount=Decimal("98600.00"),
            validation={
                "v": 1,
                "level": "expedient",
                # `net` -> pbl_without_tax, `lot_sum` -> lot_sum (interna de la PSCP, no
                # `lots`); `tax_order` i `rows` no es llisten; l'ordre de sortida es fix.
                "failed": ["lot_sum", "net", "organ", "rows", "tax_order", "vec"],
            },
        )
    )
    await db.commit()

    expedient = (await _field(client_a, tender))["expedient"]
    assert expedient["status"] == "conflict"
    assert expedient["amount"] == "98600.00"
    assert expedient["failed_checks"] == ["organ", "pbl_without_tax", "vec", "lot_sum"]
    assert expedient["evaluation_error"] is False
    assert expedient["publication_url"] == LINK


async def test_lot_sum_es_un_codi_propi_i_no_culpa_la_placsp(db, client_a):
    """`lot_sum` compara la PSCP amb ella mateixa: no es `lots` (nombre de lots, PLACSP)."""
    only_sum, (lot,) = await _tender(db, lots=1)
    both, _ = await _tender(db)
    db.add_all(
        [
            _obs(
                only_sum,
                source_row_id=f"{UID}_*",
                validation_status=CONFLICT,
                validation={"v": 1, "level": "expedient", "failed": ["lot_sum"]},
            ),
            _obs(
                only_sum,
                tender_lot_id=lot.id,
                source_entity_key=f"{UID}#lot=1",
                source_row_id=f"{UID}_1",
                validation_status=CONFLICT,
                amount=Decimal("100.00"),
                validation={
                    "v": 1,
                    "level": "lot",
                    "lot_failed": [],
                    "expedient_failed": ["lot_sum"],
                },
            ),
            _obs(
                both,
                validation_status=CONFLICT,
                validation={"v": 1, "level": "expedient", "failed": ["lot_sum", "lots"]},
            ),
        ]
    )
    await db.commit()
    field = await _field(client_a, only_sum)
    assert field["expedient"]["failed_checks"] == ["lot_sum"]
    assert field["lots"][str(lot.id)]["failed_checks"] == ["lot_sum"]
    assert (await _field(client_a, both))["expedient"]["failed_checks"] == ["lots", "lot_sum"]


async def test_conflict_sense_import_i_sense_comprovacions_mapejables(db, client_a):
    """L'error d'avaluacio de l'adaptador: `conflict`, import NULL, `evaluation_error`."""
    tender, _ = await _tender(db)
    db.add(
        _obs(
            tender,
            validation_status=CONFLICT,
            amount=None,
            source_row_id=f"{UID}_*",
            validation={"v": 1, "level": "expedient", "failed": ["evaluation_error"]},
        )
    )
    await db.commit()
    expedient = (await _field(client_a, tender))["expedient"]
    assert expedient["status"] == "conflict"
    assert expedient["amount"] is None
    assert expedient["failed_checks"] == []
    # La web ha de dir que no s'ha pogut verificar, no que la PSCP no publica cap import.
    assert expedient["evaluation_error"] is True


async def test_evaluation_error_tambe_pel_rol(db, client_a):
    tender, _ = await _tender(db)
    db.add(
        _obs(
            tender,
            validation_status=CONFLICT,
            amount=None,
            validation={"v": 1, "level": "expedient", "role": "evaluation_error"},
        )
    )
    await db.commit()
    assert (await _field(client_a, tender))["expedient"]["evaluation_error"] is True


async def test_superseded_no_surt_mai(db, client_a):
    tender, _ = await _tender(db)
    db.add(_obs(tender, validation_status=SUPERSEDED, amount=Decimal("100.00")))
    await db.commit()
    assert await _field(client_a, tender) == {
        "expedient": None,
        "lots": {},
        "attribution": None,
        "source_updated_at": None,
    }


async def test_superseded_i_accepted_vigents_guanya_accepted(db, client_a):
    tender, _ = await _tender(db)
    db.add_all(
        [
            # La fila antiga d'un `mixed`, mes recent i tot: mai no s'ensenya.
            _obs(
                tender,
                validation_status=SUPERSEDED,
                amount=Decimal("1.00"),
                valid_from=NOW + timedelta(hours=1),
            ),
            _obs(tender, source_row_id=f"{UID}_*"),
        ]
    )
    await db.commit()
    expedient = (await _field(client_a, tender))["expedient"]
    assert expedient["status"] == "accepted"
    assert expedient["amount"] == "247121.63"


async def test_tancades_i_altres_tipus_o_fonts_no_hi_compten(db, client_a):
    tender, _ = await _tender(db)
    db.add_all(
        [
            _obs(tender, source_row_id="closed", valid_to=NOW + timedelta(minutes=1)),
            _obs(
                tender,
                source_row_id="net",
                amount_kind=TenderAmountKind.PBL_WITHOUT_TAX,
            ),
            _obs(tender, source_row_id="other", source_code="altra_font:x"),
        ]
    )
    await db.commit()
    assert (await _field(client_a, tender))["expedient"] is None


async def test_precedencia_entre_no_superseded(db, client_a):
    """accepted > conflict > no_value, encara que la de menys rang sigui mes recent."""
    tender, _ = await _tender(db)
    later = NOW + timedelta(days=1)
    db.add_all(
        [
            _obs(tender, source_row_id="a", validation_status=CONFLICT, valid_from=later),
            _obs(
                tender,
                source_row_id="b",
                validation_status=NO_VALUE,
                amount=Decimal("0"),
                valid_from=later,
            ),
            _obs(tender, source_row_id="c", amount=Decimal("5.00")),
        ]
    )
    await db.commit()
    expedient = (await _field(client_a, tender))["expedient"]
    assert (expedient["status"], expedient["amount"]) == ("accepted", "5.00")


async def test_a_igual_estat_guanya_la_mes_recent_i_despres_l_id(db, client_a):
    tender, _ = await _tender(db)
    other, _ = await _tender(db)
    low, high = sorted([uuid.uuid4(), uuid.uuid4()], key=str)
    db.add_all(
        [
            _obs(tender, source_row_id="old", validation_status=CONFLICT, amount=Decimal("1")),
            _obs(
                tender,
                source_row_id="new",
                validation_status=CONFLICT,
                amount=Decimal("2"),
                valid_from=NOW + timedelta(seconds=1),
            ),
            _obs(
                other, id=high, source_row_id="x", validation_status=CONFLICT, amount=Decimal("3")
            ),
            _obs(other, id=low, source_row_id="y", validation_status=CONFLICT, amount=Decimal("4")),
        ]
    )
    await db.commit()
    assert (await _field(client_a, tender))["expedient"]["amount"] == "2.00"
    assert (await _field(client_a, other))["expedient"]["amount"] == "4.00"


async def test_files_de_lot_sense_lot_nostre_no_son_l_expedient(db, client_a):
    """La forma real de db0e460a: tres `conflict` vigents amb `tender_lot_id` NULL.

    Dues son files de LOT de la font sense lot a la base (`<uuid>#lot=<n>`,
    `validation.level = lot`); nomes una es l'observacio de l'expedient. Les de lot no
    poden passar per l'expedient (ni guanyar-li per data) ni enganxar-se a cap lot.
    """
    tender, (lot,) = await _tender(db, lots=1)
    later = NOW + timedelta(hours=2)
    lot_validation = {
        "v": 1,
        "level": "lot",
        "lot_guards": {"match": {"ok": False, "problem": "no_lot_in_ours"}},
        "lot_failed": ["match"],
        "expedient_failed": ["lots"],
    }
    db.add_all(
        [
            _obs(
                tender,
                tender_lot_id=None,
                source_entity_key=f"{UID}#lot=2",
                source_row_id=f"{UID}_2",
                validation_status=CONFLICT,
                amount=Decimal("4472.16"),
                validation=dict(lot_validation, numero_lot="2"),
                valid_from=later,
            ),
            _obs(
                tender,
                tender_lot_id=None,
                source_entity_key=f"{UID}#lot=3",
                source_row_id=f"{UID}_3",
                validation_status=CONFLICT,
                amount=Decimal("18033.84"),
                validation=dict(lot_validation, numero_lot="3"),
                valid_from=later,
            ),
            _obs(
                tender,
                source_row_id=f"{UID}_*",
                validation_status=CONFLICT,
                amount=Decimal("22506.00"),
                validation={"v": 1, "level": "expedient", "failed": ["lots"]},
            ),
        ]
    )
    await db.commit()

    field = await _field(client_a, tender)
    assert field["expedient"]["amount"] == "22506.00"
    assert field["expedient"]["failed_checks"] == ["lots"]
    assert field["lots"] == {}
    assert str(lot.id) not in field["lots"]


async def test_una_fila_de_lot_es_reconeix_tambe_nomes_per_la_clau(db, client_a):
    tender, _ = await _tender(db)
    db.add(
        _obs(
            tender,
            source_entity_key=f"{UID}#lot=null",
            source_row_id=f"{UID}_x",
            validation_status=CONFLICT,
            validation={"v": 1},
        )
    )
    await db.commit()
    assert await _field(client_a, tender) == {
        "expedient": None,
        "lots": {},
        "attribution": None,
        "source_updated_at": None,
    }


# --------------------------------------------------------------------------- #
# Lots                                                                          #
# --------------------------------------------------------------------------- #


async def test_lots_accepted_conflict_no_value_i_lot_retirat(db, client_a):
    tender, (one, two, three, removed) = await _tender(db, lots=4)
    removed.removed_at = NOW
    lot_kwargs = {"validation": {"v": 1, "level": "lot", "lot_failed": [], "expedient_failed": []}}
    db.add_all(
        [
            _obs(
                tender,
                tender_lot_id=one.id,
                source_entity_key=f"{UID}#lot=1",
                source_row_id=f"{UID}_1",
                amount=Decimal("32670.00"),
                **lot_kwargs,
            ),
            _obs(
                tender,
                tender_lot_id=two.id,
                source_entity_key=f"{UID}#lot=2",
                source_row_id=f"{UID}_2",
                validation_status=CONFLICT,
                amount=Decimal("1000.00"),
                validation={
                    "v": 1,
                    "level": "lot",
                    "lot_failed": ["net", "tax_order"],
                    "expedient_failed": ["organ"],
                },
            ),
            _obs(
                tender,
                tender_lot_id=three.id,
                source_entity_key=f"{UID}#lot=3",
                source_row_id=f"{UID}_3",
                validation_status=NO_VALUE,
                amount=Decimal("0"),
                **lot_kwargs,
            ),
            _obs(
                tender,
                tender_lot_id=removed.id,
                source_entity_key=f"{UID}#lot=4",
                source_row_id=f"{UID}_4",
                **lot_kwargs,
            ),
            # Superseded d'un lot: no surt, i no tapa l'accepted del mateix lot.
            _obs(
                tender,
                tender_lot_id=one.id,
                source_entity_key=f"{UID}#lot=1",
                source_row_id=f"{UID}_1_old",
                validation_status=SUPERSEDED,
                amount=Decimal("1.00"),
                valid_from=NOW + timedelta(days=1),
                **lot_kwargs,
            ),
        ]
    )
    await db.commit()

    field = await _field(client_a, tender)
    assert field["expedient"] is None
    lots = field["lots"]
    assert set(lots) == {str(one.id), str(two.id), str(three.id)}
    assert (lots[str(one.id)]["status"], lots[str(one.id)]["amount"]) == ("accepted", "32670.00")
    assert lots[str(two.id)]["status"] == "conflict"
    assert lots[str(two.id)]["failed_checks"] == ["organ", "pbl_without_tax"]
    assert lots[str(three.id)]["status"] == "no_value"
    assert lots[str(three.id)]["published_zero"] is True
    assert field["attribution"] == OFFICIAL_ATTRIBUTION


async def test_la_data_del_dataset_cau_a_la_procedencia_si_la_columna_es_buida(db, client_a):
    tender, _ = await _tender(db)
    db.add(
        _obs(
            tender,
            source_dataset_updated_at=None,
            provenance=_provenance(rows_updated_at="2026-10-15T22:00:00+00:00"),
        )
    )
    await db.commit()
    field = await _field(client_a, tender)
    assert field["expedient"]["source_updated_at"] == "2026-10-15T22:00:00Z"
    assert field["source_updated_at"] == "2026-10-15T22:00:00Z"


async def test_un_enllac_que_no_es_http_no_s_exposa(db, client_a):
    tender, _ = await _tender(db)
    db.add(_obs(tender, provenance=_provenance("javascript:alert(1)")))
    await db.commit()
    assert (await _field(client_a, tender))["expedient"]["publication_url"] is None


# --------------------------------------------------------------------------- #
# La resta de la fitxa i la visibilitat                                         #
# --------------------------------------------------------------------------- #


async def test_la_resta_de_la_fitxa_no_canvia(db, client_a):
    tender, _ = await _tender(
        db,
        estimated_value=Decimal("245079.30"),
        base_budget_without_tax=Decimal("204232.75"),
    )
    await db.commit()
    before = await _detail(client_a, tender)
    db.add(_obs(tender))
    await db.commit()
    after = await _detail(client_a, tender)

    assert set(after) == set(before)
    for key in set(before) - {"budget_with_tax_observations"}:
        assert after[key] == before[key], key
    # L'import de la licitacio continua sent el de la seva font: no s'hi copia res.
    assert after["base_budget"] is None
    stored = (
        await db.execute(sa.select(Tender.base_budget).where(Tender.id == tender.id))
    ).scalar_one()
    assert stored is None


async def test_una_privada_d_una_altra_empresa_continua_sent_404(db, client_a, tenant_b):
    _, company_b = tenant_b
    tender, _ = await _tender(db, owner_company_id=company_b.id)
    db.add(_obs(tender))
    await db.commit()
    assert (await client_a.get(f"/api/v1/tenders/{tender.id}")).status_code == 404


async def test_una_sola_consulta_per_a_les_observacions(db):
    """`load` fa una sola consulta, sigui quin sigui el nombre de lots."""
    tender, lots = await _tender(db, lots=3)
    db.add_all(
        [
            _obs(
                tender,
                tender_lot_id=lot.id,
                source_entity_key=f"{UID}#lot={lot.lot_number}",
                source_row_id=f"{UID}_{lot.lot_number}",
                validation={"v": 1, "level": "lot"},
            )
            for lot in lots
        ]
    )
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
    assert len(result.lots) == 3
    assert len([s for s in statements if "tender_amount_observations" in s]) == 1


# --------------------------------------------------------------------------- #
# PostgreSQL: la consulta amb la procedencia en JSONB                           #
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
async def test_a_postgresql_la_consulta_llegeix_el_jsonb(db, client_a):
    tender, (lot,) = await _tender(db, lots=1)
    db.add_all(
        [
            _obs(
                tender,
                validation_status=CONFLICT,
                validation={"v": 1, "level": "expedient", "failed": ["net", "vec"]},
            ),
            _obs(
                tender,
                tender_lot_id=lot.id,
                source_entity_key=f"{UID}#lot=1",
                source_row_id=f"{UID}_1",
                amount=Decimal("32670.00"),
                validation={"v": 1, "level": "lot"},
            ),
            _obs(
                tender,
                tender_lot_id=None,
                source_entity_key=f"{UID}#lot=9",
                source_row_id=f"{UID}_9",
                validation_status=CONFLICT,
                validation={"v": 1, "level": "lot"},
                valid_from=NOW + timedelta(hours=1),
            ),
        ]
    )
    await db.commit()
    assert db.bind.dialect.name == "postgresql"

    field = await _field(client_a, tender)
    assert field["expedient"]["status"] == "conflict"
    assert field["expedient"]["failed_checks"] == ["pbl_without_tax", "vec"]
    assert field["expedient"]["publication_url"] == LINK
    assert field["lots"][str(lot.id)]["amount"] == "32670.00"
    assert field["lots"][str(lot.id)]["publication_url"] == LINK
    assert field["attribution"] == OFFICIAL_ATTRIBUTION


# --------------------------------------------------------------------------- #
# Revisio F-06a3: canonica, estat abans que data, atribucio                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("canonical_first", [True, False])
async def test_mixed_amb_fila_exacta_no_explicada_guanya_la_canonica(db, client_a, canonical_first):
    """Dues `conflict` d'expedient del mateix run (mateix `valid_from`): la canonica.

    La forma `mixed` amb la fila exacta no explicada (`guards.py`): la de l'expedient,
    `<uuid>_*`, i la de la fila antiga, `<uuid>` amb `role = unexplained_row` i
    `failed = ["rows"]`. Sigui quin sigui l'ordre dels `id`, s'ensenya la canonica: mai
    l'import, l'enllac ni les comprovacions de la fila antiga.
    """
    tender, _ = await _tender(db)
    low, high = sorted([uuid.uuid4(), uuid.uuid4()], key=str)
    canonical_id, old_id = (low, high) if canonical_first else (high, low)
    old_link = "https://contractaciopublica.cat/ca/detall-publicacio/" + UID + "/100001"
    db.add_all(
        [
            _obs(
                tender,
                id=canonical_id,
                source_row_id=f"{UID}_*",
                validation_status=CONFLICT,
                amount=Decimal("22506.00"),
                validation={"v": 1, "level": "expedient", "failed": ["net", "rows"]},
            ),
            _obs(
                tender,
                id=old_id,
                source_row_id=UID,
                validation_status=CONFLICT,
                amount=Decimal("11111.11"),
                validation={
                    "v": 1,
                    "level": "expedient",
                    "role": "unexplained_row",
                    "failed": ["rows"],
                },
                provenance=_provenance(old_link),
            ),
        ]
    )
    await db.commit()

    expedient = (await _field(client_a, tender))["expedient"]
    assert expedient["amount"] == "22506.00"
    assert expedient["failed_checks"] == ["pbl_without_tax"]
    assert expedient["publication_url"] == LINK


async def test_una_conflict_vigent_guanya_a_una_no_value_mes_nova(db, client_a):
    """L'estat passa per davant de la data: una discrepancia no s'amaga per una lectura buida."""
    tender, _ = await _tender(db)
    db.add_all(
        [
            _obs(
                tender,
                source_row_id="disappeared",
                validation_status=CONFLICT,
                amount=Decimal("98600.00"),
                validation={"v": 1, "level": "expedient", "failed": ["organ"]},
            ),
            _obs(
                tender,
                source_row_id="new",
                validation_status=NO_VALUE,
                amount=Decimal("0"),
                valid_from=NOW + timedelta(days=2),
            ),
        ]
    )
    await db.commit()
    expedient = (await _field(client_a, tender))["expedient"]
    assert (expedient["status"], expedient["amount"]) == ("conflict", "98600.00")


async def test_l_atribucio_es_el_text_oficial_de_la_fitxa_sense_data(db, client_a):
    tender, _ = await _tender(db)
    db.add(_obs(tender))
    await db.commit()
    field = await _field(client_a, tender)
    text = field["attribution"]
    assert text == OFFICIAL_ATTRIBUTION
    assert text.startswith("Font: Generalitat de Catalunya. Departament d'Economia i Finances ")
    assert "publicacions a la Plataforma de serveis de contractaci" in text
    assert "(dades obertes). Llic" in text
    assert text.endswith(" Catalunya.")
    # La constant de l'adaptador (procedencia ja escrita) no es la que s'exposa, i la
    # data va a part.
    assert text != ADAPTER_ATTRIBUTION
    assert "ybgg-dgi6" not in text and "Darrera" not in text
    assert field["source_updated_at"] == "2026-10-16T06:30:00Z"
