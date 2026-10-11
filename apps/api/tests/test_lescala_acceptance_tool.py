"""Eina d'acceptacio de L'Escala (3B-2 §8.3-8.5 i §8.8): comparador PUR i carregador
de NOMES LECTURA.

Es prova sobre el gold public del repositori (`datasets/analysis/lescala-2026.tender-gold.json`
i `datasets/classification/lescala-2026.gold.json`), carregat a la base de test pel
carregador intern (`tender_gold`): cap fitxer real, cap base real, cap pilot. Els SHA
que es comparen son els de l'oracle, mai d'un fitxer.

El que es protegeix:

* amb NOMES el PCAP i el PPT, el comparador diu 2/9 documents presents i 7
  `not_in_source_docs`, i classifica cada entitat del gold segons si els documents que
  la citen hi son (`structure_not_authored` / `extraction_missed`) o no
  (`not_in_source_docs`);
* el gold sencer es compara amb ell mateix sense cap diferencia d'estructura;
* el que la base te i l'oracle no coneix es `oracle_outdated`, mai es força;
* el que no s'ha pogut verificar (rols sense classificacio global, documents sense
  fitxer vigent, requisits sense cap run d'extraccio) es `unverifiable`, mai
  `extraction_missed`;
* un run d'extraccio REAL (`RealNamedFake`, v1) coincideix amb el gold per la clau de
  titol normalitzat de l'extractor quan el `dedup_key` no coincideix, i l'informe
  diu per quina via (`matched_by`); les cites sumen `tender_source_refs` i
  l'evidencia dels requisits i criteris;
* el carregador no escriu mai: a SQLite cap commit i cap fila nova, i la sessio de
  nomes lectura refusa un INSERT real (`PRAGMA query_only` a cada connexio); a
  PostgreSQL la transaccio es de nomes lectura (`SET TRANSACTION READ ONLY` sobre la
  sessio de test; `default_transaction_read_only=on` al servidor amb
  `read_only_session`) i un INSERT es refusa.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai import profiles
from app.ai.providers import fake
from app.db.base import Base
from app.evaluation import lescala_acceptance as tool
from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.enums import (
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
    RequirementRunBasis,
    SourceType,
)
from app.models.sources import Source
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentPage,
)
from app.models.tenders import Tender
from app.services import tender_structure
from app.services.requirement_analysis import analyze_tender_requirements
from app.services.title_keys import normalized_title
from tests.real_provider import RealNamedFake
from tests.structure_support import PROV
from tests.test_tender_gold_lescala import _rows, _table_names

CASE = "lescala-2026"
ONLY_PCAP_PPT = ("PCAP", "PPT")
STRUCTURE = {
    "blocks": tool.STRUCTURE_NOT_AUTHORED,
    "sites": tool.STRUCTURE_NOT_AUTHORED,
    "scope_items": tool.STRUCTURE_NOT_AUTHORED,
    "contract_facts": tool.STRUCTURE_NOT_AUTHORED,
    "award_criteria": tool.EXTRACTION_MISSED,
    "requirements": tool.EXTRACTION_MISSED,
}
EXPECTED_COUNTS = {
    "blocks": 4,
    "sites": 25,
    "scope_items": 25,
    "award_criteria": 28,
    "requirements": 34,
    "contract_facts": 16,
}


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


@pytest.fixture
def dataset() -> dict:
    return tender_gold.load_dataset(CASE)


@pytest.fixture
def oracle() -> tool.Oracle:
    return tool.load_oracle(CASE)


def _only_documents(dataset: dict, keys: tuple[str, ...]) -> dict:
    """El mateix expedient amb nomes aquests documents i cap entitat: un tender
    sintetic amb els SHA del gold i res mes."""
    return {
        **dataset,
        "documents": [d for d in dataset["documents"] if d["key"] in keys],
        "blocks": [],
        "sites": [],
        "scope_items": [],
        "award_criteria": [],
        "requirements": [],
        "contract_facts": [],
    }


async def _load(session: AsyncSession, dataset: dict) -> uuid.UUID:
    loaded = await tender_gold.load_tender_gold(session, dataset)
    await session.commit()
    return loaded.tender.id


def _entity_key(section: str, item: dict) -> str:
    return item["code"] if section != "sites" else item["key"]


def _expected_classification(dataset: dict, present: set[str]) -> dict[tuple[str, str], str]:
    """La regla, derivada del dataset: una entitat que cap document present cita es
    `not_in_source_docs`; si no, el que li toca a la seccio."""
    expected: dict[tuple[str, str], str] = {}
    for section, kind in STRUCTURE.items():
        for item in dataset[section]:
            cited_here = any(source["doc"] in present for source in item["sources"])
            expected[(section, _entity_key(section, item))] = (
                kind if cited_here else tool.NOT_IN_SOURCE_DOCS
            )
    return expected


# --------------------------------------------------------------------------- #
# L'oracle                                                                      #
# --------------------------------------------------------------------------- #


def test_the_oracle_is_the_public_gold(oracle, dataset):
    assert oracle.case == CASE
    assert len(oracle.documents) == 9
    assert all(len(d["sha256"]) == 64 for d in oracle.documents)
    assert oracle.expected_counts == EXPECTED_COUNTS
    # Els rols venen del gold de classificacio del paquet inicial (8 fitxers), per SHA.
    assert len(oracle.roles) == 8
    assert oracle.roles[dataset["documents"][0]["sha256"]] == "pcap"
    assert oracle.roles[dataset["documents"][1]["sha256"]] == "ppt"
    assert set(tool.CLASSIFICATIONS) == {
        "not_in_source_docs",
        "extraction_missed",
        "structure_not_authored",
        "oracle_outdated",
        "unverifiable",
    }


# --------------------------------------------------------------------------- #
# Nomes PCAP i PPT                                                              #
# --------------------------------------------------------------------------- #


async def test_only_pcap_and_ppt_gives_two_of_nine_and_seven_not_in_source_docs(
    db, dataset, oracle
):
    tender_id = await _load(db, _only_documents(dataset, ONLY_PCAP_PPT))

    snapshot = await tool.load_snapshot(db, tender_id)
    report = tool.compare(snapshot, oracle)

    assert (report.documents_present, report.documents_expected) == (2, 9)
    documents = [d for d in report.differences if d.section == "documents"]
    assert len(documents) == 7
    assert {d.classification for d in documents} == {tool.NOT_IN_SOURCE_DOCS}
    assert {d.key for d in documents} == {
        "DOC03",
        "DOC04FILE",
        "DOC04",
        "DOC05",
        "XLSX06",
        "DOC01",
        "ACORD",
    }
    # Cada entitat del gold falta, i la classificacio depen de qui la cita.
    found = {
        (d.section, d.key): d.classification for d in report.differences if d.section in STRUCTURE
    }
    assert found == _expected_classification(dataset, set(ONLY_PCAP_PPT))
    for section, expected in EXPECTED_COUNTS.items():
        count = report.sections[section]
        assert (count.expected, count.found, count.matched) == (expected, 0, 0), section
    # Les dues classes de «falta» hi son totes dues, i cap `oracle_outdated`.
    counts = report.counts()
    assert counts[tool.NOT_IN_SOURCE_DOCS] >= 7 + 1
    assert counts[tool.STRUCTURE_NOT_AUTHORED] > 0 and counts[tool.EXTRACTION_MISSED] > 0
    assert counts[tool.ORACLE_OUTDATED] == 0
    # Rols: cap classificacio global (fitxers sense bytes) -> els 2 presents no es poden
    # verificar (`unverifiable`, mai `extraction_missed`); els 6 absents del gold de
    # classificacio, no hi son.
    roles = Counter(d.classification for d in report.differences if d.section == "roles")
    assert roles == {tool.NOT_IN_SOURCE_DOCS: 6, tool.UNVERIFIABLE: 2}
    assert counts[tool.UNVERIFIABLE] == 2
    assert (report.roles.expected, report.roles.found, report.roles.matched) == (8, 2, 0)
    # Cites per document: les esperades del gold, cap trobada.
    assert report.citations["PCAP"].expected > 0 and report.citations["PCAP"].found == 0
    assert report.citations["DOC04"].found == 0
    assert report.matches == []

    payload = json.loads(report.to_json())
    assert payload["case"] == CASE and payload["tender_id"] == str(tender_id)
    assert payload["documents"] == {"present": 2, "expected": 9, "unverifiable": 0}
    assert payload["counts"][tool.NOT_IN_SOURCE_DOCS] == counts[tool.NOT_IN_SOURCE_DOCS]
    assert len(payload["differences"]) == len(report.differences)
    assert payload["matches"] == []
    table = report.render_table()
    assert "2/9" in table and tool.NOT_IN_SOURCE_DOCS in table and "requirements" in table


# --------------------------------------------------------------------------- #
# El gold sencer, i el que l'oracle no coneix                                   #
# --------------------------------------------------------------------------- #


async def test_the_full_gold_matches_itself_without_structure_differences(db, dataset, oracle):
    tender_id = await _load(db, dataset)

    report = tool.compare(await tool.load_snapshot(db, tender_id), oracle)

    assert (report.documents_present, report.documents_expected) == (9, 9)
    assert [d for d in report.differences if d.section == "documents"] == []
    for section, expected in EXPECTED_COUNTS.items():
        count = report.sections[section]
        assert (count.expected, count.found, count.matched) == (expected, expected, expected)
        assert [d for d in report.differences if d.section == section] == [], section
    for key, citation in report.citations.items():
        assert citation.found == citation.expected, key
    assert report.counts()[tool.ORACLE_OUTDATED] == 0
    # Tot el gold coincideix per la seva propia clau; cap via de titol ha calgut.
    assert Counter(m.matched_by for m in report.matches) == {
        "natural_key": 4 + 25 + 25 + 16,
        "dedup_key": 28 + 34,
    }
    for section in ("award_criteria", "requirements"):
        assert report.sections[section].matched_by == {"dedup_key": EXPECTED_COUNTS[section]}
    # Sense cap classificacio global (fitxers sense bytes), els rols no es poden
    # verificar: N `unverifiable` i 0 `extraction_missed`.
    assert {d.classification for d in report.differences} == {tool.UNVERIFIABLE}
    assert all(d.section == "roles" for d in report.differences)
    assert report.counts()[tool.EXTRACTION_MISSED] == 0
    assert report.counts()[tool.UNVERIFIABLE] == 8
    assert (report.roles.found, report.roles.matched) == (8, 0)


async def test_what_the_oracle_does_not_know_is_oracle_outdated(db, dataset, oracle):
    tender_id = await _load(db, dataset)
    tender = await db.get(Tender, tender_id)
    await tender_structure.create_block(
        db, tender, {"code": "B9", "name": "Bloc que l'oracle no coneix"}, provenance=PROV
    )
    await db.commit()

    report = tool.compare(await tool.load_snapshot(db, tender_id), oracle)

    extra = [d for d in report.differences if d.section == "blocks"]
    assert [(d.key, d.classification) for d in extra] == [("B9", tool.ORACLE_OUTDATED)]
    assert (report.sections["blocks"].found, report.sections["blocks"].matched) == (5, 4)


async def test_a_published_file_with_another_sha_is_oracle_outdated_not_forced(db, dataset, oracle):
    """§8.3: un SHA diferent es registra com a diferencia explicada, mai es força."""
    changed = _only_documents(dataset, ONLY_PCAP_PPT)
    changed["documents"] = [
        {**d, "sha256": "f" * 64} if d["key"] == "PCAP" else d for d in changed["documents"]
    ]
    tender_id = await _load(db, changed)

    report = tool.compare(await tool.load_snapshot(db, tender_id), oracle)

    assert (report.documents_present, report.documents_expected) == (1, 9)
    pcap = next(d for d in report.differences if d.section == "documents" and d.key == "PCAP")
    assert pcap.classification == tool.ORACLE_OUTDATED
    assert "f" * 64 in pcap.detail and dataset["documents"][0]["sha256"] in pcap.detail


def _empty_snapshot(**overrides) -> tool.TenderSnapshot:
    values = {
        "tender_id": "t",
        "external_id": "x",
        "expediente": None,
        "documents": (),
        "blocks": frozenset(),
        "sites": frozenset(),
        "scope_items": frozenset(),
        "criteria": {},
        "requirements": {},
        "facts": frozenset(),
        "extraction_runs": 0,
    }
    values.update(overrides)
    return tool.TenderSnapshot(**values)


def _document(sha256: str | None, *, classified: bool = False, role=None) -> tool.SnapshotDocument:
    return tool.SnapshotDocument(
        document_id="d",
        name="PCAP (1).pdf",
        path="PCAP (1).pdf",
        sha256=sha256,
        classified=classified,
        role=role,
        role_source="feed" if role is None else "global_classifier",
        citations=0,
    )


def test_compare_is_pure(oracle):
    snapshot = _empty_snapshot()
    before = json.dumps(oracle.tender_gold, sort_keys=True)

    first = tool.compare(snapshot, oracle)
    second = tool.compare(snapshot, oracle)

    assert first.to_dict() == second.to_dict()
    assert json.dumps(oracle.tender_gold, sort_keys=True) == before
    assert (first.documents_present, first.documents_expected) == (0, 9)
    assert first.counts()[tool.NOT_IN_SOURCE_DOCS] == 9 + 8 + sum(EXPECTED_COUNTS.values())


# --------------------------------------------------------------------------- #
# El que no es pot verificar no es cap error d'extraccio                        #
# --------------------------------------------------------------------------- #


def test_without_any_extraction_run_cited_requirements_are_unverifiable(oracle, dataset):
    """PCAP present i classificat, pero cap run d'extraccio: el que el PCAP cita no
    s'ha pogut extreure (`unverifiable`); l'estructura continua sent de qui l'escriu."""
    pcap_sha = dataset["documents"][0]["sha256"]
    snapshot = _empty_snapshot(
        documents=(_document(pcap_sha, classified=True, role="pcap"),), extraction_runs=0
    )

    report = tool.compare(snapshot, oracle)

    by = {(d.section, d.key): d.classification for d in report.differences}
    cited_in_pcap = [
        r["code"] for r in dataset["requirements"] if any(s["doc"] == "PCAP" for s in r["sources"])
    ]
    assert cited_in_pcap, "el gold cita requisits al PCAP"
    assert {by[("requirements", code)] for code in cited_in_pcap} == {tool.UNVERIFIABLE}
    assert report.counts()[tool.EXTRACTION_MISSED] == 0
    assert by[("blocks", "B1")] == tool.STRUCTURE_NOT_AUTHORED
    assert ("roles", "PCAP") not in by, "el rol efectiu coincideix amb el gold"
    assert (report.roles.found, report.roles.matched) == (1, 1)

    # Amb un run i el document classificat amb un altre rol, si que es `extraction_missed`.
    report = tool.compare(
        _empty_snapshot(
            documents=(_document(pcap_sha, classified=True, role="ppt"),), extraction_runs=1
        ),
        oracle,
    )
    by = {(d.section, d.key): d.classification for d in report.differences}
    assert by[("roles", "PCAP")] == tool.EXTRACTION_MISSED
    assert {by[("requirements", code)] for code in cited_in_pcap} == {tool.EXTRACTION_MISSED}


def test_a_document_without_a_current_file_is_unverifiable(oracle):
    snapshot = _empty_snapshot(documents=(_document(None),))

    report = tool.compare(snapshot, oracle)

    assert report.unverifiable_documents == 1
    assert (report.documents_present, report.documents_expected) == (0, 9)
    docs = [d for d in report.differences if d.section == "documents"]
    assert Counter(d.classification for d in docs) == {
        tool.NOT_IN_SOURCE_DOCS: 9,
        tool.UNVERIFIABLE: 1,
    }
    unverifiable = next(d for d in docs if d.classification == tool.UNVERIFIABLE)
    assert unverifiable.key == "PCAP (1).pdf" and "sense fitxer vigent" in unverifiable.detail
    assert report.to_dict()["documents"] == {"present": 0, "expected": 9, "unverifiable": 1}


# --------------------------------------------------------------------------- #
# Un run d'extraccio REAL: coincidencia per titol i cites de l'evidencia        #
# --------------------------------------------------------------------------- #

PAGE_TEXT = (
    "Clausula 9. Solvencia. El licitador ha d'acreditar la solvencia economica i tecnica.\n"
    "Clausula 10. Criteris d'adjudicacio. L'oferta economica del bloc 1 es valora amb 8 punts.\n"
)


async def _extracted_pcap(session: AsyncSession, tender_id: uuid.UUID) -> TenderDocumentExtraction:
    """Dona text al PCAP del gold (que no te bytes) perque l'extractor el pugui llegir."""
    pcap = next(
        d
        for d in (await tool.load_snapshot(session, tender_id)).documents
        if d.name.startswith("Plec de cl")
    )
    content = await session.scalar(
        sa.select(TenderDocumentContent).where(TenderDocumentContent.sha256 == pcap.sha256)
    )
    content.is_text_extractable = True
    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="pypdf-v1",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=1,
        extracted_page_count=1,
        empty_page_count=0,
        text_char_count=len(PAGE_TEXT),
        ocr_candidate=False,
        is_current=True,
        qa_status="good",
    )
    session.add(extraction)
    await session.flush()
    session.add(
        TenderDocumentPage(
            extraction_id=extraction.id,
            page_number=1,
            text=PAGE_TEXT,
            char_count=len(PAGE_TEXT),
            text_hash="h" * 64,
            status=PageExtractionStatus.EXTRACTED,
        )
    )
    await session.commit()
    return extraction


def _evidence(extraction_id: uuid.UUID, quote: str) -> list[dict]:
    return [{"extraction_id": str(extraction_id), "page_number": 1, "quote": quote}]


async def test_a_real_run_matches_the_gold_by_title_and_its_evidence_counts_as_citations(
    db, dataset, oracle
):
    fake.reset()
    tender_id = await _load(db, _only_documents(dataset, ONLY_PCAP_PPT))
    extraction = await _extracted_pcap(db, tender_id)
    tender = await db.get(Tender, tender_id)
    r1, r2 = dataset["requirements"][0], dataset["requirements"][1]
    p1 = dataset["award_criteria"][0]
    solvency = _evidence(extraction.id, "El licitador ha d'acreditar la solvencia economica")
    payload = {
        "requirements": [
            {
                "category": r1["category"],
                "obligation": r1["obligation"],
                "title": f"  {r1['title'].upper()}  ",  # espais i majuscules: el mateix titol
                "confidence": "high",
                "evidence": solvency,
            },
            {
                "category": r2["category"],
                "obligation": r2["obligation"],
                "title": r2["title"],
                "confidence": "high",
                "evidence": solvency,
            },
        ],
        "award_criteria": [
            {
                "name": p1["name"],
                "points": p1["points"],
                "evaluation_type": p1["evaluation_type"],
                "confidence": "high",
                "evidence": _evidence(extraction.id, "es valora amb 8 punts"),
            }
        ],
    }

    outcome = await analyze_tender_requirements(
        db, tender, provider=RealNamedFake([payload]), profile=profiles.V1
    )
    await db.commit()

    run = outcome.run
    assert (run.run_basis, run.is_current) == (RequirementRunBasis.REAL, True)
    assert (run.requirements_extracted, run.award_criteria_extracted) == (2, 1)
    snapshot = await tool.load_snapshot(db, tender_id)
    # Les claus de l'extractor NO son les del gold; els titols normalitzats, si.
    assert tender_gold.dedup_key(CASE, "requirement", r1["code"]) not in snapshot.requirements
    assert set(snapshot.requirements.values()) == {
        normalized_title(r1["title"]),
        normalized_title(r2["title"]),
    }
    assert set(snapshot.criteria.values()) == {normalized_title(p1["name"])}
    assert snapshot.extraction_runs >= 1

    report = tool.compare(snapshot, oracle)

    matched = {(m.section, m.key): m.matched_by for m in report.matches}
    assert matched[("requirements", r1["code"])] == "title"
    assert matched[("requirements", r2["code"])] == "title"
    assert matched[("award_criteria", p1["code"])] == "title"
    assert report.sections["requirements"].matched == 2
    assert report.sections["requirements"].matched_by == {"title": 2}
    assert report.sections["award_criteria"].matched_by == {"title": 1}
    assert report.sections["requirements"].found == 2
    missing = {d.key for d in report.differences if d.section == "requirements"}
    assert r1["code"] not in missing and r2["code"] not in missing
    assert len(missing) == EXPECTED_COUNTS["requirements"] - 2
    assert report.counts()[tool.ORACLE_OUTDATED] == 0, "res de mes: tot coincideix per titol"
    # Les tres evidencies del run son cites del PCAP (cap `tender_source_refs`).
    assert report.citations["PCAP"].found == 3
    payload_out = json.loads(report.to_json())
    assert {m["matched_by"] for m in payload_out["matches"]} == {"title"}
    assert "title" in report.render_table()


# --------------------------------------------------------------------------- #
# Mai escriu                                                                    #
# --------------------------------------------------------------------------- #


async def test_the_loader_never_writes_on_sqlite(db, dataset, monkeypatch):
    tender_id = await _load(db, _only_documents(dataset, ONLY_PCAP_PPT))
    tables = await _table_names(db)
    before = {table: await _rows(db, table) for table in tables}

    async def _no_commit():
        raise AssertionError("el carregador no fa commit")

    monkeypatch.setattr(db, "commit", _no_commit)
    snapshot = await tool.load_snapshot(db, tender_id)

    assert len(snapshot.documents) == 2
    assert not db.new and not db.dirty and not db.deleted
    assert {table: await _rows(db, table) for table in tables} == before


@pytest.mark.postgres
async def test_the_read_only_transaction_refuses_an_insert_on_postgres(db, dataset):
    tender_id = await _load(db, _only_documents(dataset, ONLY_PCAP_PPT))

    await tool.open_read_only(db)
    db.add(Source(code="no-s-escriu", name="x", source_type=SourceType.MANUAL))
    with pytest.raises(sa.exc.DBAPIError, match="read-only"):
        await db.flush()
    await db.rollback()

    await tool.open_read_only(db)
    snapshot = await tool.load_snapshot(db, tender_id)
    assert len(snapshot.documents) == 2
    await db.rollback()
    assert await db.scalar(sa.select(sa.func.count()).where(Source.code == "no-s-escriu")) == 0


async def _temporary_database(tmp_path, dataset) -> tuple[str, uuid.UUID]:
    """Una base SQLite en fitxer amb el tender sintetic (PCAP + PPT) ja carregat."""
    url = f"sqlite+aiosqlite:///{(tmp_path / 'acceptance.db').as_posix()}"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await vocabulary.seed_vocabulary(session)
            await session.flush()
            tender_id = await _load(session, _only_documents(dataset, ONLY_PCAP_PPT))
    finally:
        await engine.dispose()
    return url, tender_id


async def test_the_read_only_session_refuses_a_real_insert_on_sqlite(tmp_path, dataset):
    """`PRAGMA query_only` a CADA connexio: un INSERT real falla, tambe despres d'un
    rollback (connexio nova amb `NullPool`); i el `commit` es refusa abans de cap SQL."""
    url, tender_id = await _temporary_database(tmp_path, dataset)

    async with tool.read_only_session(url) as session:
        assert await session.scalar(sa.text("PRAGMA query_only")) == 1
        snapshot = await tool.load_snapshot(session, tender_id)
        session.add(Source(code="no-s-escriu", name="x", source_type=SourceType.MANUAL))
        with pytest.raises(sa.exc.OperationalError, match="readonly"):
            await session.flush()
        await session.rollback()
        # Transaccio (i connexio) nova despres del rollback: continua sense poder escriure.
        assert await session.scalar(sa.text("PRAGMA query_only")) == 1
        session.add(Source(code="no-s-escriu-2", name="x", source_type=SourceType.MANUAL))
        with pytest.raises(sa.exc.OperationalError, match="readonly"):
            await session.flush()
        await session.rollback()
        session.add(Source(code="no-s-escriu-3", name="x", source_type=SourceType.MANUAL))
        with pytest.raises(tool.ReadOnlyViolation):
            await session.commit()

    assert len(snapshot.documents) == 2
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            count = await connection.scalar(
                sa.text("SELECT count(*) FROM sources WHERE code LIKE 'no-s-escriu%'")
            )
    finally:
        await engine.dispose()
    assert count == 0


@pytest.mark.postgres
async def test_the_read_only_session_applies_default_transaction_read_only_on_postgres(
    db, dataset, request
):
    """`read_only_session` sobre PostgreSQL: `default_transaction_read_only=on` al
    servidor (cada transaccio, tambe despres d'un rollback, sense cap `SET TRANSACTION`)
    i un INSERT real falla. Sobre el schema aillat de la suite (`search_path`)."""
    from tests.conftest import PG_TEST_URL

    tender_id = await _load(db, _only_documents(dataset, ONLY_PCAP_PPT))
    schema = await db.scalar(sa.text("select current_schema()"))
    url, _expected = request.config.stash[PG_TEST_URL]

    async with tool.read_only_session(url, search_path=schema) as session:
        assert await session.scalar(sa.text("show default_transaction_read_only")) == "on"
        assert await session.scalar(sa.text("select current_schema()")) == schema
        snapshot = await tool.load_snapshot(session, tender_id)
        assert len(snapshot.documents) == 2
        session.add(Source(code="no-s-escriu", name="x", source_type=SourceType.MANUAL))
        with pytest.raises(sa.exc.DBAPIError, match="read-only"):
            await session.flush()
        await session.rollback()
        # Transaccio nova, sense `SET TRANSACTION`: el servidor ja la fa de nomes lectura.
        assert await session.scalar(sa.text("show transaction_read_only")) == "on"
        session.add(Source(code="no-s-escriu-2", name="x", source_type=SourceType.MANUAL))
        with pytest.raises(sa.exc.DBAPIError, match="read-only"):
            await session.flush()

    assert await db.scalar(sa.select(sa.func.count()).where(Source.code.like("no-s-escriu%"))) == 0


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("postgresql+asyncpg://u:p@127.0.0.1:5433/licitacions_x", "contrasenya"),
        ("postgresql+asyncpg://u@127.0.0.1:5433/licitacions_x?options=-c", "parametres"),
        ("postgresql://u@127.0.0.1:5433/licitacions_x", "asyncpg"),
        ("mysql://u@h/x", "asyncpg"),
    ],
)
def test_the_cli_refuses_an_ambiguous_dsn(url, why):
    with pytest.raises(tool.AcceptanceError, match=why):
        tool.validate_database_url(url)


def test_the_cli_needs_a_way_to_find_the_tender():
    with pytest.raises(SystemExit):
        tool.build_parser().parse_args(["--database-url", "sqlite+aiosqlite:///x.db"])


async def test_the_cli_prints_the_report_without_writing(tmp_path, dataset, capsys):
    url, tender_id = await _temporary_database(tmp_path, dataset)
    args = tool.build_parser().parse_args(
        ["--database-url", url, "--tender-id", str(tender_id), "--json"]
    )

    assert await tool.run(args) == tool.EXIT_OK
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["documents"] == {"present": 2, "expected": 9, "unverifiable": 0}
    assert payload["counts"][tool.NOT_IN_SOURCE_DOCS] >= 7
    assert url not in out, "la DSN no s'imprimeix"

    by_external = tool.build_parser().parse_args(
        ["--database-url", url, "--external-id", dataset["tender"]["external_id"]]
    )
    assert await tool.run(by_external) == tool.EXIT_OK
    table = capsys.readouterr().out
    assert "2/9" in table and tool.NOT_IN_SOURCE_DOCS in table

    missing = tool.build_parser().parse_args(
        ["--database-url", url, "--tender-id", str(uuid.uuid4())]
    )
    assert await tool.run(missing) == tool.EXIT_INPUT
