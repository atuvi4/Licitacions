"""Parser i normalitzador de les entrades del feed agregat 1044 (PSCP).

Les fixtures son entrades reals retallades (vegeu
`tests/fixtures/placsp/agregadas/README.md`).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.ingestion.placsp import parser
from app.ingestion.placsp.normalizer import normalize

FIXTURES = Path(__file__).parent / "fixtures" / "placsp"

ID_INTERN_FIXTURE = "da9975db-b778-4f73-886c-7042daa395f7"
ORGAN_FIXTURE = "2250729"
ALLOWED_KEYS = {
    "ted_uuid",
    "contract_subtype_code",
    "contract_type_code",
    "procedure_code",
    "placsp_organ_id",
    "pscp_id_intern",
    # 3B-2 §7.1.2: el `summary` cru quan es la plantilla del feed (no es descripcio).
    "feed_summary",
}


@pytest.fixture
def fixture_dir():
    def _dir(relative: str) -> Path:
        return FIXTURES / relative

    return _dir


@pytest.fixture
def fixture():
    def _read(relative: str) -> bytes:
        return (FIXTURES / relative).read_bytes()

    return _read


def entries_of(content: bytes):
    return parser.parse_feed(content).entries


def first_entry(content: bytes):
    return entries_of(content)[0]


def test_entrada_pscp_conserva_provenance_en_metadata(fixture):
    data = normalize(parser.parse_entry(first_entry(fixture("agregadas/entry_lots.atom"))))
    assert set(data.source_metadata) <= ALLOWED_KEYS
    assert data.source_metadata["pscp_id_intern"] == ID_INTERN_FIXTURE
    assert data.source_metadata["placsp_organ_id"] == ORGAN_FIXTURE
    assert data.source_metadata["contract_type_code"] == "3"
    assert data.source_metadata["procedure_code"] == "1"
    assert data.buyer.external_identifier == ORGAN_FIXTURE
    assert data.buyer.tax_identifier is None
    assert data.buyer.registry_identifier is None
    assert data.source_updated_at.tzinfo is not None
    assert data.source_updated_at.isoformat() == "2026-10-05T20:00:34.510000+02:00"
    assert not hasattr(data, "ted_uuid")  # no es un camp canonic


def test_ted_uuid_es_conserva_i_les_claus_absents_no_hi_son(fixture):
    entries = [
        normalize(parser.parse_entry(e)) for e in entries_of(fixture("agregadas/entry_lots.atom"))
    ]
    with_ted = [d for d in entries if "ted_uuid" in d.source_metadata]
    assert with_ted, "cap entrada de la fixture porta TED"
    assert len(with_ted[0].source_metadata["ted_uuid"]) >= 8
    assert "ted_uuid" not in entries[0].source_metadata
    for data in entries:
        assert set(data.source_metadata) <= ALLOWED_KEYS
        assert all(isinstance(v, str) and v for v in data.source_metadata.values())


def test_lots_documents_i_cpv_de_lot(fixture):
    data = normalize(parser.parse_entry(first_entry(fixture("agregadas/entry_lots.atom"))))
    assert len(data.lots) == 2 and any(lot.cpvs for lot in data.lots)
    assert any(
        d.url.startswith("https://contractaciopublica.cat/portal-api/descarrega-document/")
        for d in data.documents
    )


def test_pscp_id_intern_nomes_amb_regex_estricta():
    assert (
        parser.extract_pscp_id_intern(
            "https://contractaciopublica.cat/ca/detall-publicacio/"
            + ID_INTERN_FIXTURE
            + "/300900469"
        )
        == ID_INTERN_FIXTURE
    )
    assert parser.extract_pscp_id_intern("https://example.org/altra/cosa") is None
    assert parser.extract_pscp_id_intern(None) is None


def test_parser_version_v2():
    assert parser.PARSER_VERSION == "placsp-v2"


def test_643_no_canvia_comportament(fixture):
    feed = parser.parse_feed(fixture("feed_sample.atom"))
    for element in feed.entries:
        data = normalize(parser.parse_entry(element))
        assert "placsp_organ_id" not in data.source_metadata
        assert "pscp_id_intern" not in data.source_metadata
        assert data.buyer is not None and data.buyer.external_identifier
        assert data.source_updated_at is None or data.source_updated_at.tzinfo is not None


def test_variants_canvien_updated_i_contingut(fixture):
    def norm(name):
        return normalize(parser.parse_entry(first_entry(fixture(f"agregadas/{name}"))))

    base = norm("entry_lots.atom")
    older = norm("entry_lots_older.atom")
    assert older.external_id == base.external_id
    assert older.source_updated_at < base.source_updated_at
    assert older.deadline_at < base.deadline_at
    assert (
        norm("entry_lots_same_updated_other_hash.atom").source_updated_at == base.source_updated_at
    )
    assert norm("entry_lots_same_updated_other_hash.atom").title != base.title
    assert len(norm("entry_lots_doc_removed.atom").documents) == len(base.documents) - 1
    assert len(norm("entry_lots_lot_removed.atom").lots) == 1
    assert (
        norm("entry_lots_republished.atom").source_updated_at
        > norm("entry_lots_doc_removed.atom").source_updated_at
    )


def test_lapides(fixture):
    kinds = {}
    for name in ("tombstone_anulada", "tombstone_cerrada", "tombstone_old"):
        feed = parser.parse_feed(fixture(f"agregadas/{name}.atom"))
        assert feed.entries == [] and len(feed.deleted_entries) == 1
        element = feed.deleted_entries[0]
        parsed = parser.parse_deleted_entry(element)
        assert parsed is not None and parsed.deleted and parsed.updated_at is not None
        kinds[name] = element[0].get("type")
    assert kinds["tombstone_anulada"] == "ANULADA" and kinds["tombstone_cerrada"] == "CERRADA"
    base = parser.parse_entry(first_entry(fixture("agregadas/entry_lots.atom")))
    old = parser.parse_deleted_entry(
        parser.parse_feed(fixture("agregadas/tombstone_old.atom")).deleted_entries[0]
    )
    assert old.external_id == base.external_id and old.updated_at < base.updated_at

    # Ordre narratiu per a Task 4/5: old < entrada < lapides < republicada.
    republished = parser.parse_entry(first_entry(fixture("agregadas/entry_lots_republished.atom")))
    for name in ("tombstone_anulada", "tombstone_cerrada"):
        tomb = parser.parse_deleted_entry(
            parser.parse_feed(fixture(f"agregadas/{name}.atom")).deleted_entries[0]
        )
        assert tomb.external_id == base.external_id
        assert old.updated_at < base.updated_at < tomb.updated_at < republished.updated_at


def test_pagines_enllacades_per_next(fixture):
    page1 = parser.parse_feed(fixture("agregadas/page1.atom"))
    page2 = parser.parse_feed(fixture("agregadas/page2.atom"))
    assert page1.next_url == page2.self_url and page2.next_url is None
    newest2 = max(parser.parse_entry(e).updated_at for e in page2.entries)
    oldest1 = min(parser.parse_entry(e).updated_at for e in page1.entries)
    assert (oldest1 - newest2).total_seconds() >= 3 * 86400


def _base_text(fixture) -> str:
    return fixture("agregadas/entry_lots.atom").decode("utf-8")


def _normalize_text(text: str):
    return normalize(parser.parse_entry(first_entry(text.encode("utf-8"))))


def test_contract_subtype_code_a_source_metadata(fixture):
    text = _base_text(fixture)
    type_tag = re.search(r"<cbc:TypeCode[^>]*>3</cbc:TypeCode>", text).group(0)
    assert "SubTypeCode" not in text
    with_subtype = text.replace(type_tag, type_tag + "<cbc:SubTypeCode>7</cbc:SubTypeCode>", 1)
    assert _normalize_text(with_subtype).source_metadata["contract_subtype_code"] == "7"
    assert "contract_subtype_code" not in _normalize_text(text).source_metadata


def test_source_updated_at_naive_s_assumeix_europe_madrid(fixture):
    text = _base_text(fixture)
    original = "2026-10-05T20:00:34.510+02:00"
    assert original in text
    # Estiu (CEST, +02:00) i hivern (CET, +01:00): l'offset depen de la data.
    summer = _normalize_text(text.replace(original, "2026-10-05T20:00:34", 1))
    winter = _normalize_text(text.replace(original, "2026-12-05T20:00:34", 1))
    assert str(summer.source_updated_at.tzinfo) == "Europe/Madrid"
    assert summer.source_updated_at.utcoffset() == timedelta(hours=2)
    assert winter.source_updated_at.utcoffset() == timedelta(hours=1)
    assert summer.source_updated_at.replace(tzinfo=None) == datetime(2026, 10, 5, 20, 0, 34)


def test_source_updated_at_amb_offset_explicit_es_conserva(fixture):
    text = _base_text(fixture)
    original = "2026-10-05T20:00:34.510+02:00"
    data = _normalize_text(text.replace(original, "2026-10-05T20:00:34.510+05:00", 1))
    assert data.source_updated_at.utcoffset() == timedelta(hours=5)
    assert data.source_updated_at.isoformat() == "2026-10-05T20:00:34.510000+05:00"


def test_fixtures_sense_dades_personals(fixture_dir):
    paths = list(fixture_dir("agregadas").glob("*.atom"))
    assert len(paths) >= 11
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for needle in ("ElectronicMail", "Telephone", "Telefax", "cac:Contact"):
            assert needle not in text, (path.name, needle)
        assert len(text.encode("utf-8")) <= 150 * 1024, path.name
