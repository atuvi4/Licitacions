"""Duplicats i reiteracions d'un mateix requisit (assegurança de Palamós). Zero crides externes.

Regles provades a partir de l'evidència, mai dels títols: mateix tros de pàgina =
duplicat d'extracció; mateix text en un altre lloc = reiteració; la resta, diferents.
I la resolució real (script guardat) sobre una extracció sintètica amb la forma de
Palamós: només el canònic rep el valor, res no es verifica sol i l'extracció no canvia.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models.enums import ReviewRelationType
from app.models.requirements import Requirement, RequirementEvidence
from app.models.review import ReviewItem, ReviewItemRelation, ReviewItemRevision, ReviewItemSource
from app.services import review_view
from app.services.review_duplicates import (
    Candidate,
    EvidenceSpan,
    PairKind,
    canonical,
    classify,
    normalise_quote,
    plan_relations,
)
from tests import test_requirement_analysis as base
from tests.test_requirement_analysis import PAGE_27, PAGE_28
from tests.test_requirement_analysis_v3 import _evidence, _requirement, _run_v3_real

analyzable = base.analyzable
_clean_provider = base._clean_provider

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("import_palamos_review")
resolution = _load("resolve_palamos_insurance")

CLAUSE = (
    "Subscriure i mantenir vigent durant tota la durada del contracte una pòlissa\n"
    "d’assegurança de responsabilitat civil amb un import mínim de 500.000 euros que\n"
    "cobreixi els riscos derivats de la prestació dels serveis objecte d’aquest plec."
)

# --------------------------------------------------------------------------- #
# Regles pures, amb les dades reals de Palamós                                  #
# --------------------------------------------------------------------------- #

PCAP_PAGE, INFORME_PAGE = uuid.uuid4(), uuid.uuid4()
PCAP = Candidate(
    uuid.UUID("51253be2-f8aa-4a2c-93c9-c3ee4d0c1f7f"),
    1,
    (EvidenceSpan(PCAP_PAGE, "administrative_terms", 7, 732, 969, None, CLAUSE),),
)
INFORME = Candidate(
    uuid.UUID("a1e0def8-0ae3-4228-bb03-08d9ab9e0fbc"),
    19,
    (EvidenceSpan(INFORME_PAGE, "additional", 6, 418, 655, None, CLAUSE),),
)
INFORME_AGAIN = Candidate(
    uuid.UUID("1fca3109-b7f3-45de-9549-fd8d848a3565"),
    20,
    (EvidenceSpan(INFORME_PAGE, "additional", 6, 418, 655, None, CLAUSE),),
)


def test_el_mateix_tros_de_la_mateixa_pagina_es_un_duplicat_d_extraccio():
    assert classify(INFORME, INFORME_AGAIN) is PairKind.DUPLICATE


def test_el_mateix_text_en_un_altre_document_es_una_reiteracio():
    assert classify(PCAP, INFORME) is PairKind.RESTATEMENT
    assert classify(PCAP, INFORME_AGAIN) is PairKind.RESTATEMENT


def test_el_ppt_tambe_es_document_vinculant():
    """Els tipus vinculants són valors reals de l'enum (PCAP i PPT), no noms escrits a mà."""
    from app.models.enums import TenderDocumentType
    from app.services.review_duplicates import BINDING_DOCUMENT_TYPES

    assert BINDING_DOCUMENT_TYPES == {
        TenderDocumentType.ADMINISTRATIVE_TERMS.value,
        TenderDocumentType.TECHNICAL_TERMS.value,
    }
    ppt = Candidate(
        uuid.uuid4(), 9, (EvidenceSpan(uuid.uuid4(), "technical_terms", 22, 0, 10, None, CLAUSE),)
    )
    assert canonical([INFORME, ppt]).item_id == ppt.item_id


def test_obligacions_diferents_no_es_relacionen():
    other = Candidate(
        uuid.uuid4(),
        3,
        (
            EvidenceSpan(
                uuid.uuid4(),
                "administrative_terms",
                9,
                10,
                90,
                None,
                "Pòlissa d'accidents laborals amb un import mínim de 500.000 euros.",
            ),
        ),
    )
    assert classify(PCAP, other) is PairKind.DISTINCT
    chosen, relations = plan_relations([PCAP, other])
    assert relations == []


def test_la_normalitzacio_nomes_iguala_espais_i_cometes():
    assert normalise_quote("d’assegurança\nde  500.000") == "d'assegurança de 500.000"
    assert normalise_quote("mínim de 500.000") != normalise_quote("mínim de 50.000")


@pytest.mark.parametrize("order", list(itertools.permutations([PCAP, INFORME, INFORME_AGAIN])))
def test_el_pla_de_palamos_es_deterministic(order):
    chosen, relations = plan_relations(order)

    assert chosen.item_id == PCAP.item_id
    assert {(r.from_item, r.to_item, r.relation_type) for r in relations} == {
        (INFORME_AGAIN.item_id, INFORME.item_id, "duplicate_of"),
        (INFORME.item_id, PCAP.item_id, "restatement_of"),
    }
    assert {
        (r.from_item, r.to_item, r.relation_type) for r in relations
    } == resolution.EXPECTED_RELATIONS


def test_el_canonic_no_depen_de_l_ordre_dels_identificadors():
    """El PCAP guanya pel document vinculant encara que el seu id fos el més gran."""
    pcap_last = Candidate(uuid.UUID("ffffffff-ffff-4fff-bfff-ffffffffffff"), 1, PCAP.evidence)
    informe_first = Candidate(
        uuid.UUID("00000000-0000-4000-8000-000000000000"), 19, INFORME.evidence
    )
    assert canonical([informe_first, pcap_last]).item_id == pcap_last.item_id


def test_el_pla_de_palamos_comprova_la_frase_vinculant():
    relations = list(resolution.EXPECTED_RELATIONS)
    resolution.assert_palamos_plan(resolution.PCAP_ITEM, relations, CLAUSE)
    with pytest.raises(resolution.ResolutionError, match="import mínim"):
        resolution.assert_palamos_plan(
            resolution.PCAP_ITEM, relations, CLAUSE.replace("mínim", "màxim")
        )
    with pytest.raises(resolution.ResolutionError, match="canònic"):
        resolution.assert_palamos_plan(resolution.INFORME_ITEM, relations, CLAUSE)


# --------------------------------------------------------------------------- #
# Resolució real sobre una extracció sintètica amb forma de Palamós             #
# --------------------------------------------------------------------------- #

LINE = "Subscriure una pòlissa de responsabilitat civil amb un import mínim de 500.000 euros."
TITLES = ("Assegurança RC (plec)", "Assegurança RC (informe)", "Assegurança RC (informe, bis)")


@pytest.fixture
async def insurance(session, analyzable):
    """SINTÈTIC: la frase a la p. 27 i a la p. 28; la de la p. 28, extreta dues vegades."""
    first, second = analyzable["pages"]
    first.text = PAGE_27 + LINE + "\n"
    second.text = PAGE_28 + LINE + "\n"
    await session.commit()
    x = analyzable["extraction"].id

    def row(title, page, eid):
        return _requirement(
            x,
            category="insurance",
            operator="gte",
            title=title,
            claims=[],
            claims_complete="partial",
            evidence=[_evidence(x, eid=eid, page=page, quote=LINE)],
        )

    await _run_v3_real(
        session,
        analyzable,
        {
            "requirements": [
                row(TITLES[0], 27, "e1"),
                row(TITLES[1], 28, "e2"),
                row(TITLES[2], 28, "e3"),
            ],
            "award_candidates": [],
        },
    )
    rows = {r.title: r for r in (await session.scalars(sa.select(Requirement))).all()}
    assert set(rows) == set(TITLES)
    return {"tender_id": analyzable["document"].tender_id, "rows": rows}


async def _raw(session):
    return [
        [dict(r) for r in (await session.execute(sa.select(t).order_by(t.c.id))).mappings()]
        for t in (Requirement.__table__, RequirementEvidence.__table__)
    ]


async def _item(session, company_id, requirement):
    return await session.scalar(
        sa.select(ReviewItem)
        .join(ReviewItemSource, ReviewItemSource.item_id == ReviewItem.id)
        .where(
            ReviewItem.company_id == company_id, ReviewItemSource.requirement_id == requirement.id
        )
    )


async def test_la_resolucio_relaciona_i_corregeix_nomes_el_canonic(
    session, insurance, tenant_a, tenant_b
):
    (user_a, company_a), (user_b, company_b) = tenant_a, tenant_b
    tid = insurance["tender_id"]
    company_a_id, company_b_id = company_a.id, company_b.id
    for user, company in ((user_a, company_a), (user_b, company_b)):
        await review_view.start_review(
            session, company_id=company.id, tender_id=tid, actor_id=user.id
        )
    await session.commit()
    items = {t: await _item(session, company_a_id, insurance["rows"][t]) for t in TITLES}
    ids = {t: i.id for t, i in items.items()}
    before_status = {t: i.verification_status for t, i in items.items()}
    before_snapshot = {t: dict(i.accepted_json) for t, i in items.items()}
    item_b = await _item(session, company_b_id, insurance["rows"][TITLES[0]])
    item_b_id, b_snapshot = item_b.id, dict(item_b.accepted_json)
    raw_before = await _raw(session)

    result = await resolution.resolve(
        session,
        company_id=company_a_id,
        tender_id=tid,
        item_ids=list(ids.values()),
        actor_id=user_a.id,
        batch_id=uuid.uuid4(),
        value=resolution.VALUE,
        note="prova",
    )
    await session.commit()

    plec, informe, bis = (ids[t] for t in TITLES)
    assert result["canonical"] == plec
    relations = {
        (r.from_item_id, r.to_item_id, r.relation_type)
        for r in (await session.scalars(sa.select(ReviewItemRelation))).all()
    }
    [duplicate] = [r for r in relations if r[2] == ReviewRelationType.DUPLICATE_OF]
    [restated] = [r for r in relations if r[2] == ReviewRelationType.RESTATEMENT_OF]
    assert {duplicate[0], duplicate[1]} == {informe, bis}
    assert restated[1] == plec and restated[0] == duplicate[1]
    assert len(relations) == 2

    session.expire_all()
    canonical_item = await session.get(ReviewItem, plec)
    assert canonical_item.accepted_json["values"] == [
        {
            "key": "reviewer_value_1",
            "operator": ">=",
            "value": 500000,
            "unit_label": "EUR",
            "source": "reviewer",
        }
    ]
    # Cap verificació automàtica: l'estat és el d'abans.
    assert canonical_item.verification_status == before_status[TITLES[0]]
    # Les altres files no reben cap valor: una sola correcció.
    for title in TITLES[1:]:
        assert (await session.get(ReviewItem, ids[title])).accepted_json == before_snapshot[title]
    actions = (
        await session.scalars(
            sa.select(ReviewItemRevision.action).where(ReviewItemRevision.action == "correct")
        )
    ).all()
    assert len(actions) == 1
    read = await review_view.get_requirement(
        session, company_id=company_a_id, tender_id=tid, item_id=plec
    )
    assert (read.provenance, read.provenance_label) == ("automated_corrected", "Extret i corregit")
    assert [(r.relation_label, r.other_item_id) for r in read.visible_relations] == [
        ("Reiterat a", restated[0])
    ]
    duplicate_read = await review_view.get_requirement(
        session, company_id=company_a_id, tender_id=tid, item_id=duplicate[0]
    )
    assert [r.relation_label for r in duplicate_read.folded_relations] == ["Duplicat de"]
    # L'extracció i l'altra empresa, intactes.
    assert await _raw(session) == raw_before
    assert (await session.get(ReviewItem, item_b_id)).accepted_json == b_snapshot
    assert (
        await session.scalar(
            sa.select(sa.func.count())
            .select_from(ReviewItemRelation)
            .where(ReviewItemRelation.company_id == company_b_id)
        )
        == 0
    )

    # Tornar-hi: l'estat ja és complet.
    expected = {(a, b, t.value) for a, b, t in relations}
    assert await resolution.status(session, company_a_id, tid, plec, expected) == "complete"
