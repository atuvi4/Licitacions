"""Cites dels criteris d'adjudicacio a la lectura de l'analisi (v3, T7).

Les cites del 0050 viuen a `award_criterion_source_ref_links`. La lectura
`GET /tenders/{id}/analysis/requirements` les ha de portar per a TOTS els
criteris (formals i candidats) amb el mateix vocabulari que els requisits, en
una consulta en bloc i respectant la visibilitat de la licitacio.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.requirements import AwardCriterion
from app.services import tender_structure
from tests.test_tender_gold_lescala import CASE


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


def _url(tender_id) -> str:
    return f"/api/v1/tenders/{tender_id}/analysis/requirements"


def _all_criteria(body: dict) -> dict[str, dict]:
    items = [*body["award_criteria"], *body["award_candidates"]]
    return {item["name"]: item for item in items}


class _Select:
    """Compta els SELECT que arriben al motor."""

    def __init__(self, engine):
        self.engine = engine.sync_engine
        self.count = 0

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            self.count += 1

    def __enter__(self):
        sa.event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        sa.event.remove(self.engine, "before_cursor_execute", self._record)


async def test_criteria_return_their_citations_and_uncited_ones_return_empty(db, client_a):
    dataset = tender_gold.load_dataset(CASE)
    loaded = await tender_gold.load_tender_gold(db, dataset)
    c1 = loaded.criteria["C1"]
    p1 = loaded.criteria["P1"]
    # Un criteri sense cap cita: se li treu l'enllac (la cita no s'esborra).
    for ref in (await tender_structure.source_refs_of(db, AwardCriterion, [p1.id]))[p1.id]:
        await tender_structure.unlink_source_ref(db, entity=p1, source_ref_id=ref.id)
    await db.commit()

    response = await client_a.get(_url(loaded.tender.id))
    assert response.status_code == 200, response.text
    body = response.json()
    by_id = {item["id"]: item for item in [*body["award_criteria"], *body["award_candidates"]]}
    assert len(by_id) == len(loaded.criteria)

    citations = by_id[str(c1.id)]["citations"]
    assert len(citations) == 1
    cite = citations[0]
    assert cite["document_name"]
    assert cite["location_label"] and "E.2" in cite["location_label"]
    assert cite["quote"] == (
        "la mera disponibilitat majorista o una carta d'intencions de tercer no puntuen"
    )
    assert cite["page_number"] is None
    assert cite["tender_id"] == str(loaded.tender.id)

    assert by_id[str(p1.id)]["citations"] == []
    # La resta, amb la seva cita.
    others = [v for k, v in by_id.items() if k not in {str(c1.id), str(p1.id)}]
    assert others and all(item["citations"] for item in others)


async def test_citation_statements_do_not_grow_with_the_number_of_criteria(db, client_a, engine):
    loaded = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    await db.commit()
    url = _url(loaded.tender.id)
    assert (await client_a.get(url)).status_code == 200  # escalfa

    with _Select(engine) as before:
        assert (await client_a.get(url)).status_code == 200

    # Mes criteris, cadascun amb les cites d'un criteri existent.
    template = loaded.criteria["C1"]
    refs = (await tender_structure.source_refs_of(db, AwardCriterion, [template.id]))[template.id]
    columns = [c.key for c in sa.inspect(AwardCriterion).mapper.column_attrs if c.key not in {"id"}]
    for index in range(12):
        clone = AwardCriterion(**{key: getattr(template, key) for key in columns})
        clone.name = f"Extra {index}"
        clone.dedup_key = f"extra-{index}"
        db.add(clone)
        await db.flush()
        for ref in refs:
            await tender_structure.link_source_ref(db, entity=clone, source_ref_id=ref.id)
    await db.commit()

    with _Select(engine) as after:
        response = await client_a.get(url)
    assert response.status_code == 200
    names = _all_criteria(response.json())
    assert len(names) == len(loaded.criteria) + 12
    assert all(names[f"Extra {i}"]["citations"] for i in range(12))
    assert after.count == before.count


async def test_private_tender_citations_are_invisible_to_other_companies(
    db, client_a, client_b, tenant_a
):
    _, company_a = tenant_a
    loaded = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    loaded.tender.owner_company_id = company_a.id
    await db.commit()
    url = _url(loaded.tender.id)

    own = await client_a.get(url)
    assert own.status_code == 200
    assert any(item["citations"] for item in own.json()["award_criteria"])

    foreign = await client_b.get(url)
    assert foreign.status_code == 404
    assert "citations" not in foreign.text
