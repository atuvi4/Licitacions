"""Canviar el nom d'un organisme propaga `search_text` (spec 3B-1 §7.2).

`Tender.search_text` porta `buyer.name`. Si una publicacio posterior canvia el
nom de l'organisme, totes les licitacions d'aquell organisme s'han de poder
trobar pel nom nou **immediatament**, no nomes la que ha arribat al feed i no
nomes despres d'un backfill. Es fa a la mateixa transaccio de la ingesta, en
bloc (cap N+1).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.ingestion.placsp.service import PlacspIngestionService
from app.models.enums import IngestionStatus, SourceType
from app.models.sources import Source
from app.models.tenders import Buyer, Tender, TenderLot
from app.tenders.search import build_search_text, refresh_search_text_for_buyer
from app.tenders.search_backfill import backfill_search_text

pytestmark = pytest.mark.asyncio

AGREGADAS = Path(__file__).parent / "fixtures" / "placsp" / "agregadas"
BASE_ID = "https://contrataciondelestado.es/sindicacion/PlataformasAgregadasSinMenores/20529505"
ORIGINAL_NAME = "<cbc:Name>Ajuntament de Sant Vicenç dels Horts</cbc:Name>"
ORIGINAL_UPDATED = "<updated>2026-10-05T20:00:34.510+02:00</updated>"


def _feed(*, name: str, updated: str | None = None, with_twin: bool) -> str:
    """Feed en memoria: l'entrada base i, si cal, una bessona del mateix organisme."""
    text = (AGREGADAS / "entry_lots.atom").read_text(encoding="utf-8")
    header = text[: text.index("<entry>")]
    entry = re.search(r"<entry>.*?</entry>", text, flags=re.DOTALL).group(0)
    assert entry.count(ORIGINAL_NAME) == 1
    entry = entry.replace(ORIGINAL_NAME, f"<cbc:Name>{name}</cbc:Name>")
    if updated is not None:
        assert entry.count(ORIGINAL_UPDATED) == 1
        entry = entry.replace(ORIGINAL_UPDATED, f"<updated>{updated}</updated>")
    entries = [entry]
    if with_twin:
        twin = entry.replace(f"<id>{BASE_ID}</id>", f"<id>{BASE_ID}-bis</id>").replace(
            "<cbc:ContractFolderID>CTON2026000028</cbc:ContractFolderID>",
            "<cbc:ContractFolderID>CTON2026000028-BIS</cbc:ContractFolderID>",
        )
        assert twin != entry
        entries.append(twin)
    return header + "\n".join(entries) + "\n</feed>\n"


async def _ingest(db, tmp_path: Path, xml: str) -> None:
    path = tmp_path / f"feed-{uuid.uuid4().hex[:8]}.atom"
    path.write_text(xml, encoding="utf-8")
    run = await PlacspIngestionService(db).run(fixture=path, source_code="placsp_agregadas")
    assert run.status == IngestionStatus.SUCCESS, run.error_summary


async def test_el_nom_nou_de_l_organisme_es_troba_sense_backfill(db, client_a, tmp_path):
    await _ingest(db, tmp_path, _feed(name="Organisme Antic", with_twin=True))
    tenders = {
        t.external_id: str(t.id)
        for t in (await db.execute(sa.select(Tender).where(Tender.external_id.like(f"{BASE_ID}%"))))
        .scalars()
        .all()
    }
    assert len(tenders) == 2
    both = set(tenders.values())

    async def found(q: str) -> set[str]:
        response = await client_a.get("/api/v1/tenders", params={"q": q, "limit": 100})
        assert response.status_code == 200, response.text
        return {row["id"] for row in response.json()["items"]} & both

    assert await found("antic") == both
    assert await found("nou") == set()

    # Nomes l'entrada base torna a sortir al feed, amb el nom nou de l'organisme.
    await _ingest(
        db,
        tmp_path,
        _feed(name="Organisme Nou", updated="2026-10-06T20:00:34.510+02:00", with_twin=False),
    )

    assert await found("nou") == both  # tambe la bessona, que no ha tornat a sortir
    assert await found("antic") == set()  # cap altre camp el conte
    assert await found("ORGANISME") == both
    assert await found("organisme nóu") == both

    result = await backfill_search_text(db)
    assert result.updated == 0  # ja estava al dia: el backfill es un no-op


async def test_refresh_per_organisme_es_en_bloc(db, engine):
    """Una SELECT de licitacions, una de lots i un sol UPDATE, siguin quantes siguin.

    Amb mes de 100 licitacions: cap sentencia porta una llista d'ids que creixi
    amb l'organisme (un `IN` amb tots els ids toparia amb el limit de parametres
    del motor en un organisme gran).
    """
    total = 120
    source = Source(code=f"s-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.MANUAL)
    db.add(source)
    await db.flush()
    buyer = Buyer(source_id=source.id, external_identifier="org-1", name="Organisme Antic")
    other = Buyer(source_id=source.id, external_identifier="org-2", name="Altre Antic")
    db.add_all([buyer, other])
    await db.flush()
    mine = [
        Tender(
            id=uuid.uuid4(),
            source_id=source.id,
            external_id=f"t-{n}",
            title=f"Servei {n}",
            expediente_number=f"EXP-{n}",
            buyer_id=buyer.id,
        )
        for n in range(total)
    ]
    db.add_all(mine)
    await db.flush()
    db.add_all(
        TenderLot(tender_id=t.id, external_lot_id="1", lot_number=1, title=f"Lot {n}")
        for n, t in enumerate(mine)
    )
    stranger = Tender(source_id=source.id, external_id="t-x", title="Servei x", buyer_id=other.id)
    db.add(stranger)
    await db.flush()
    for tender in [*mine, stranger]:
        tender.search_text = build_search_text(tender, "Organisme Antic", [])
    await db.commit()

    buyer.name = "Organisme Nou"
    await db.flush()
    statements: list[tuple[str, int]] = []

    def count(conn, cursor, statement, parameters, context, executemany):
        if executemany:
            width = max((len(p) for p in parameters), default=0)
        else:
            width = len(parameters or ())
        statements.append((statement, width))

    sa.event.listen(engine.sync_engine, "before_cursor_execute", count)
    try:
        updated = await refresh_search_text_for_buyer(db, buyer.id)
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", count)
    await db.commit()

    assert updated == total
    assert len(statements) == 3, [s for s, _ in statements]
    # Cap sentencia porta mes de 10 parametres: ni la SELECT de lots ni l'UPDATE.
    assert max(width for _, width in statements) <= 10, statements
    rows = dict((await db.execute(sa.select(Tender.external_id, Tender.search_text))).all())
    for n in range(total):
        assert rows[f"t-{n}"] == f"servei {n} exp-{n} organisme nou lot {n}"
    assert "antic" in rows["t-x"]  # un altre organisme no es toca

    # Idempotent: una segona crida no escriu res.
    assert await refresh_search_text_for_buyer(db, buyer.id) == 0
