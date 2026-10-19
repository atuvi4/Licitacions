"""Observacions d'imports d'una font externa acreditada (F-06a, 0063).

El que es prova aqui es el CONTRACTE del model i de la base: valors per defecte,
les dues unicitats parcials (una sola `accepted` vigent per objectiu i font; una sola
vigent, de qualsevol estat, per fila de la font), els CHECK (estats, regles de l'import
amb `no_value`), cascades i, a PostgreSQL, el trigger que nomes deixa TANCAR una
observacio (`valid_to` de NULL a un valor) i refusa un DELETE directe.

Cap adaptador ni cap dada real: nomes la taula (l'adaptador es a
`test_pscp_open_data.py`). La migracio (round trip 0062 <-> 0063) es prova a
`test_migrations.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.models.enums import AmountObservationStatus, TenderAmountKind
from app.models.sources import Source
from app.models.tender_amounts import TenderAmountObservation
from app.models.tenders import Tender, TenderLot
from app.tenant_ops import PUBLIC_TABLES

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
SOURCE = "pscp_open_data:ybgg-dgi6"
SHA = "a" * 64
TABLE = "tender_amount_observations"
PROVENANCE = {"dataset": "ybgg-dgi6", "field": "pressupost_licitacio_amb_1"}


async def _tender(session, lots: int = 0) -> tuple[Tender, list[TenderLot]]:
    source = Source(code=f"am-{uuid.uuid4().hex[:8]}", name="Font de prova")
    session.add(source)
    await session.flush()
    tender = Tender(source_id=source.id, external_id=f"exp-{uuid.uuid4().hex[:8]}", title="T")
    session.add(tender)
    await session.flush()
    created = [
        TenderLot(tender_id=tender.id, external_lot_id=str(n), lot_number=n)
        for n in range(1, lots + 1)
    ]
    session.add_all(created)
    await session.flush()
    return tender, created


def _observation(tender: Tender | uuid.UUID, **kwargs) -> TenderAmountObservation:
    # Un id tambe: despres d'un rollback l'objecte caduca i no es pot llegir sense IO.
    values = {
        "tender_id": tender if isinstance(tender, uuid.UUID) else tender.id,
        "amount_kind": TenderAmountKind.PBL_WITH_TAX,
        "amount": Decimal("247121.63"),
        "source_code": SOURCE,
        "source_entity_key": "exp-1",
        "source_row_id": "exp-1",
        "retrieved_at": NOW,
        "evidence_url": "https://analisi.transparenciacatalunya.cat/resource/ybgg-dgi6.json",
        "evidence_sha256": SHA,
        "validation_status": AmountObservationStatus.ACCEPTED,
        "valid_from": NOW,
        "provenance": dict(PROVENANCE),
    }
    values.update(kwargs)
    return TenderAmountObservation(**values)


async def _count(session, tender_id: uuid.UUID) -> int:
    return (
        await session.execute(
            sa.select(sa.func.count())
            .select_from(TenderAmountObservation)
            .where(TenderAmountObservation.tender_id == tender_id)
        )
    ).scalar_one()


def test_els_valors_dels_enums_son_els_de_l_especificacio():
    assert {m.value for m in TenderAmountKind} == {
        "pbl_with_tax",
        "pbl_without_tax",
        "estimated_value",
    }
    assert {m.value for m in AmountObservationStatus} == {
        "accepted",
        "conflict",
        "no_value",
        "superseded",
    }


def test_la_taula_es_global_i_no_toca_la_licitacio():
    table = TenderAmountObservation.__table__
    assert "company_id" not in table.c
    assert TABLE in PUBLIC_TABLES
    assert table.c.tender_lot_id.nullable is True
    assert table.c.valid_to.nullable is True
    # Noms de FK explicits, els mateixos que la migracio.
    assert {c.name for c in table.foreign_key_constraints} == {
        f"fk_{TABLE}_tender_id",
        f"fk_{TABLE}_tender_lot_id",
    }
    assert {fk.ondelete for fk in table.foreign_key_constraints} == {"CASCADE"}


async def test_expedient_i_lot_amb_els_valors_per_defecte(db):
    tender, (lot,) = await _tender(db, lots=1)
    whole = _observation(tender)
    per_lot = _observation(
        tender,
        tender_lot_id=lot.id,
        amount=Decimal("1000.00"),
        source_entity_key="exp-1#lot=1",
        source_row_id="exp-1_1",
        source_phase="Adjudicacio",
        source_dataset_updated_at=NOW - timedelta(days=1),
        raw_storage_key="pscp/exp-1.json",
    )
    db.add_all([whole, per_lot])
    await db.commit()
    await db.refresh(whole)
    await db.refresh(per_lot)

    assert whole.tender_lot_id is None and per_lot.tender_lot_id == lot.id
    assert whole.currency == "EUR" and per_lot.currency == "EUR"
    assert whole.validation == {} and per_lot.validation == {}
    assert whole.valid_to is None
    assert whole.created_at is not None
    assert whole.amount == Decimal("247121.63")
    assert per_lot.raw_storage_key == "pscp/exp-1.json"


async def test_els_defectes_de_la_base_sense_l_orm(db):
    """Un INSERT que no passa per l'ORM tambe rep `EUR`, `{}` i `created_at`."""
    tender, _ = await _tender(db)
    await db.commit()
    await db.execute(
        sa.text(
            f"INSERT INTO {TABLE} (id, tender_id, amount_kind, amount, source_code, "
            "source_entity_key, source_row_id, retrieved_at, evidence_url, evidence_sha256, "
            "validation_status, valid_from, provenance) VALUES (:id, :tender, "
            "'estimated_value', 10, :source, 'k', 'r', CURRENT_TIMESTAMP, 'https://x', :sha, "
            "'conflict', CURRENT_TIMESTAMP, :provenance)"
        ),
        # `.hex`: SQLite guarda els UUID sense guions, i PostgreSQL els accepta igual.
        {
            "id": uuid.uuid4().hex,
            "tender": tender.id.hex,
            "source": SOURCE,
            "sha": SHA,
            "provenance": '{"field": "x"}',
        },
    )
    await db.commit()
    row = (
        await db.execute(
            sa.select(TenderAmountObservation).where(TenderAmountObservation.tender_id == tender.id)
        )
    ).scalar_one()
    assert row.currency == "EUR"
    assert row.validation == {}
    assert row.created_at is not None
    assert row.validation_status == AmountObservationStatus.CONFLICT


async def test_una_sola_observacio_acceptada_vigent_per_base_i_font(db):
    created, (lot,) = await _tender(db, lots=1)
    tender, lot_id = created.id, lot.id
    db.add(_observation(tender))
    db.add(_observation(tender, tender_lot_id=lot_id))
    await db.commit()

    # La mateixa base (expedient), tipus i font, acceptada i vigent: no hi cap.
    db.add(_observation(tender, amount=Decimal("1.00")))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()
    # El mateix per al lot.
    db.add(_observation(tender, tender_lot_id=lot_id, amount=Decimal("1.00")))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()

    # Una `conflict` d'una altra fila, una de tancada, un altre tipus o una altra font: si.
    db.add_all(
        [
            _observation(
                tender,
                amount=Decimal("2.00"),
                source_row_id="exp-1_*",
                validation_status=AmountObservationStatus.CONFLICT,
                validation={"organ": "mismatch"},
            ),
            _observation(tender, amount=Decimal("3.00"), valid_to=NOW + timedelta(hours=1)),
            _observation(tender, amount_kind=TenderAmountKind.PBL_WITHOUT_TAX),
            _observation(tender, source_code="una_altra_font"),
            _observation(
                tender,
                tender_lot_id=lot_id,
                source_row_id="exp-1_2",
                validation_status=AmountObservationStatus.CONFLICT,
            ),
        ]
    )
    await db.commit()
    assert await _count(db, tender) == 7


async def test_una_sola_observacio_vigent_per_fila_de_la_font(db):
    """Qualsevol estat: dues vigents de la mateixa fila i objectiu no hi caben (m-4)."""
    created, (lot,) = await _tender(db, lots=1)
    tender, lot_id = created.id, lot.id
    conflict = {"validation_status": AmountObservationStatus.CONFLICT}
    db.add(_observation(tender, **conflict))
    db.add(_observation(tender, tender_lot_id=lot_id, source_row_id="exp-1_1", **conflict))
    await db.commit()

    for duplicate in (
        _observation(tender, amount=Decimal("5.00"), **conflict),
        _observation(tender, amount=None, validation_status=AmountObservationStatus.NO_VALUE),
        _observation(tender, amount=Decimal("5.00")),  # accepted de la mateixa fila
        _observation(tender, tender_lot_id=lot_id, source_row_id="exp-1_1", **conflict),
    ):
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()

    # Una de tancada de la mateixa fila, una altra fila, o el mateix id de fila per a un
    # altre objectiu (expedient vs lot) o un altre tipus: si.
    db.add_all(
        [
            _observation(tender, valid_to=NOW, **conflict),
            _observation(tender, source_row_id="exp-1_*", **conflict),
            _observation(tender, tender_lot_id=lot_id, **conflict),
            _observation(tender, amount_kind=TenderAmountKind.ESTIMATED_VALUE, **conflict),
        ]
    )
    await db.commit()
    assert await _count(db, tender) == 6


async def test_l_expedient_i_els_lots_no_comparteixen_unicitat(db):
    tender, (first, second) = await _tender(db, lots=2)
    db.add_all(
        [
            _observation(tender),
            _observation(tender, tender_lot_id=first.id),
            _observation(tender, tender_lot_id=second.id),
        ]
    )
    await db.commit()
    assert await _count(db, tender.id) == 3


@pytest.mark.parametrize(
    ("override", "why"),
    [
        ({"amount": Decimal("-0.01")}, "import negatiu"),
        ({"amount": Decimal("0.00")}, "accepted amb import 0"),
        ({"amount": None}, "accepted sense import"),
        (
            {"amount": Decimal("1.00"), "validation_status": AmountObservationStatus.NO_VALUE},
            "no_value amb un import",
        ),
        (
            {"amount": Decimal("-1.00"), "validation_status": AmountObservationStatus.CONFLICT},
            "conflict negatiu",
        ),
        ({"currency": "EU"}, "moneda de 2"),
        ({"evidence_url": "https://x/" + "a" * 1015}, "evidence_url de mes de 1024"),
        ({"evidence_sha256": "a" * 63}, "sha massa curt"),
        ({"evidence_sha256": "a" * 64 + "b"}, "sha massa llarg"),
        ({"valid_to": NOW - timedelta(seconds=1)}, "tancada abans de comencar"),
    ],
)
async def test_els_check_refusen_el_que_no_te_sentit(db, override, why):
    tender, _ = await _tender(db)
    await db.commit()
    db.add(_observation(tender, **override))
    with pytest.raises((IntegrityError, DBAPIError)):
        await db.flush()
    await db.rollback()


async def test_els_imports_valids_per_estat(db):
    """`no_value` amb NULL o amb el 0 publicat; `conflict` i `superseded` amb NULL (la font
    no publica import, o no es pot desar sense alterar-lo) o amb 0 o mes; `accepted` > 0;
    una `evidence_url` de 1024 i `valid_to = valid_from` tambe son valids."""
    tender, _ = await _tender(db)
    rows = [
        ("r1", AmountObservationStatus.NO_VALUE, None),
        ("r2", AmountObservationStatus.NO_VALUE, Decimal("0.00")),
        ("r3", AmountObservationStatus.CONFLICT, Decimal("0.00")),
        ("r4", AmountObservationStatus.SUPERSEDED, Decimal("10.00")),
        ("r5", AmountObservationStatus.ACCEPTED, Decimal("0.01")),
        ("r7", AmountObservationStatus.SUPERSEDED, None),
        ("r8", AmountObservationStatus.CONFLICT, None),
        ("r9", AmountObservationStatus.SUPERSEDED, Decimal("0.00")),
    ]
    db.add_all(
        _observation(tender, source_row_id=row, validation_status=status, amount=amount)
        for row, status, amount in rows
    )
    db.add(_observation(tender, source_row_id="r6", amount=Decimal("1.00"), valid_to=NOW))
    db.add(
        _observation(
            tender,
            source_row_id="r10",
            evidence_url="https://x/" + "a" * 1014,
            validation_status=AmountObservationStatus.CONFLICT,
        )
    )
    db.add(_observation(tender, source_row_id="r10", valid_to=NOW, amount=Decimal("2.00")))
    await db.commit()
    assert await _count(db, tender.id) == 11


async def test_la_procedencia_es_obligatoria(db):
    tender, _ = await _tender(db)
    await db.commit()
    db.add(_observation(tender, provenance=None))
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_esborrar_la_licitacio_s_emporta_les_observacions(db):
    tender, (lot,) = await _tender(db, lots=1)
    keep, _ = await _tender(db)
    db.add_all(
        [
            _observation(tender),
            _observation(tender, tender_lot_id=lot.id),
            _observation(keep),
        ]
    )
    await db.commit()

    await db.execute(sa.delete(Tender).where(Tender.id == tender.id))
    await db.commit()
    assert await _count(db, tender.id) == 0
    assert await _count(db, keep.id) == 1


async def test_esborrar_un_lot_s_emporta_nomes_les_seves(db):
    tender, (lot,) = await _tender(db, lots=1)
    db.add_all([_observation(tender), _observation(tender, tender_lot_id=lot.id)])
    await db.commit()

    await db.execute(sa.delete(TenderLot).where(TenderLot.id == lot.id))
    await db.commit()
    rows = (
        (
            await db.execute(
                sa.select(TenderAmountObservation.tender_lot_id).where(
                    TenderAmountObservation.tender_id == tender.id
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == [None]


# --------------------------------------------------------------------------- #
# Nomes d'afegir (trigger de PostgreSQL)                                        #
# --------------------------------------------------------------------------- #


async def _one(db) -> tuple[uuid.UUID, uuid.UUID]:
    """Ids, no objectes: cada refus fa rollback i els objectes caduquen."""
    tender, _ = await _tender(db)
    observation = _observation(tender)
    db.add(observation)
    await db.commit()
    return tender.id, observation.id


async def _refused(db, statement) -> None:
    with pytest.raises(DBAPIError) as caught:
        await db.execute(statement)
    await db.rollback()
    assert "tender_amount_observations" in str(caught.value)


def _update(observation_id: uuid.UUID, **values):
    return (
        sa.update(TenderAmountObservation)
        .where(TenderAmountObservation.id == observation_id)
        .values(**values)
    )


@pytest.mark.postgres
async def test_cap_columna_no_es_pot_canviar_excepte_tancar_valid_to(db):
    tender_id, obs_id = await _one(db)

    for values in (
        {"amount": Decimal("1.00")},
        {"source_code": "una_altra_font"},
        {"validation_status": AmountObservationStatus.CONFLICT},
        {"validation": {"organ": "ok"}},
        {"evidence_sha256": "b" * 64},
        {"valid_from": NOW - timedelta(days=1)},
        # Tancar i alhora canviar una altra cosa: tampoc.
        {"valid_to": NOW + timedelta(days=1), "amount": Decimal("1.00")},
        # Una actualitzacio que no canvia res tampoc no es un tancament.
        {"valid_to": None},
        {"provenance": {"field": "un altre"}},
    ):
        await _refused(db, _update(obs_id, **values))

    # Tancar-la (NULL -> valor) si.
    closed_at = NOW + timedelta(days=1)
    await db.execute(_update(obs_id, valid_to=closed_at))
    await db.commit()

    # Un cop tancada: ni reobrir-la ni moure el tancament.
    await _refused(db, _update(obs_id, valid_to=None))
    await _refused(db, _update(obs_id, valid_to=closed_at + timedelta(days=1)))

    row = (
        await db.execute(
            sa.select(TenderAmountObservation).where(TenderAmountObservation.id == obs_id)
        )
    ).scalar_one()
    await db.refresh(row)
    assert row.valid_to == closed_at
    assert row.amount == Decimal("247121.63")
    assert row.validation_status == AmountObservationStatus.ACCEPTED
    assert row.tender_id == tender_id


@pytest.mark.postgres
async def test_tancar_l_anterior_allibera_la_unicitat_per_a_una_de_nova(db):
    tender_id, obs_id = await _one(db)
    await db.execute(_update(obs_id, valid_to=NOW + timedelta(days=1)))
    db.add(_observation(tender_id, amount=Decimal("1.00"), valid_from=NOW + timedelta(days=1)))
    await db.commit()
    assert await _count(db, tender_id) == 2


@pytest.mark.postgres
async def test_un_delete_directe_es_refusa_i_la_cascada_passa(db):
    tender_id, obs_id = await _one(db)
    await _refused(
        db, sa.delete(TenderAmountObservation).where(TenderAmountObservation.id == obs_id)
    )
    assert await _count(db, tender_id) == 1
    # Per cascada (la licitacio), si.
    await db.execute(sa.delete(Tender).where(Tender.id == tender_id))
    await db.commit()
    assert await _count(db, tender_id) == 0


@pytest.mark.postgres
async def test_els_enums_tenen_check_a_la_base(db):
    """Un INSERT que no passa per l'ORM no hi pot escriure un valor fora de l'enum."""
    names = {
        name
        for (name,) in (
            await db.execute(
                sa.text(
                    "SELECT conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE n.nspname = current_schema() AND t.relname = :t AND c.contype = 'c'"
                ),
                {"t": TABLE},
            )
        ).all()
    }
    assert {
        f"ck_{TABLE}_amount_kind_valid",
        f"ck_{TABLE}_validation_status_valid",
        f"ck_{TABLE}_amount_non_negative",
        f"ck_{TABLE}_accepted_amount_positive",
        f"ck_{TABLE}_no_value_amount",
        f"ck_{TABLE}_validity_ordered",
        f"ck_{TABLE}_evidence_sha256_length",
        f"ck_{TABLE}_currency_length",
        f"ck_{TABLE}_evidence_url_length",
    } <= names
    assert f"ck_{TABLE}_null_amount_is_no_value" not in names

    tender, _ = await _tender(db)
    tender_id = tender.id
    await db.commit()
    insert = sa.text(
        f"INSERT INTO {TABLE} (id, tender_id, amount_kind, amount, source_code, "
        "source_entity_key, source_row_id, retrieved_at, evidence_url, evidence_sha256, "
        "validation_status, valid_from, provenance) VALUES (gen_random_uuid(), :tender, "
        ":kind, :amount, :source, 'k', :row, now(), 'https://x', :sha, :status, now(), '{}')"
    )
    ok = {"tender": tender_id, "source": SOURCE, "sha": SHA, "amount": 10, "row": "r"}
    for bad in (
        {"kind": "pbl", "status": "accepted"},
        {"kind": "pbl_with_tax", "status": "pending"},
        # Les regles de l'import tambe sense l'ORM.
        {"kind": "pbl_with_tax", "status": "accepted", "amount": 0},
        {"kind": "pbl_with_tax", "status": "accepted", "amount": None},
        {"kind": "pbl_with_tax", "status": "no_value", "amount": 5},
        {"kind": "pbl_with_tax", "status": "conflict", "amount": -1},
    ):
        with pytest.raises(IntegrityError):
            await db.execute(insert, {**ok, **bad})
        await db.rollback()
    await db.execute(insert, {**ok, "kind": "pbl_with_tax", "status": "accepted"})
    for row, status in (("n", "no_value"), ("s", "superseded"), ("c", "conflict")):
        await db.execute(
            insert, {**ok, "kind": "pbl_with_tax", "status": status, "amount": None, "row": row}
        )
    await db.commit()
    assert await _count(db, tender_id) == 4


# --------------------------------------------------------------------------- #
# SCHEMA_REVISION a 0063: el reintent nomes toca registres FALLITS              #
# --------------------------------------------------------------------------- #


def test_pujar_a_0063_nomes_reintenta_registres_fallits():
    """Al pilot (lectura feta pel controlador el 19-10-2026) els 24.586 `source_records`
    son `processed` amb `failed_schema_revision` NULL: el conjunt de reintents es buit.
    Aqui es demostra la regla: amb la revisio nova, un registre processat continua
    `unchanged`; nomes un FALLIT determinista d'una revisio anterior es reintenta."""
    from app.db.revision import SCHEMA_REVISION
    from app.ingestion.placsp import parser as placsp_parser
    from app.ingestion.placsp.service import KNOWN_FAILURE, RETRY, UNCHANGED, _existing_verdict
    from app.models.enums import ProcessingStatus
    from app.models.sources import SourceRecord

    def record(status, *, error=None, revision=None):
        return SourceRecord(
            processing_status=status,
            error_message=error,
            parser_version=placsp_parser.PARSER_VERSION,
            failed_schema_revision=revision,
        )

    assert SCHEMA_REVISION == "0063_tender_amount_observations"
    # Processat (com tots els del pilot): mai no es reintenta, sigui quina sigui la revisio.
    assert _existing_verdict(record(ProcessingStatus.PROCESSED)) == UNCHANGED
    assert (
        _existing_verdict(
            record(ProcessingStatus.PROCESSED, revision="0062_requirement_uncertainties")
        )
        == UNCHANGED
    )
    # Fallit determinista amb 0062: es reintenta una vegada amb 0063.
    failed_62 = record(
        ProcessingStatus.FAILED,
        error="db_data: StringDataRightTruncationError (22001) tender_lots.title",
        revision="0062_requirement_uncertainties",
    )
    assert _existing_verdict(failed_62) == RETRY
    # Fallit determinista ja amb 0063: fallada coneguda, no es reintenta.
    failed_63 = record(
        ProcessingStatus.FAILED,
        error="db_data: StringDataRightTruncationError (22001) tender_lots.title",
        revision=SCHEMA_REVISION,
    )
    assert _existing_verdict(failed_63) == KNOWN_FAILURE
