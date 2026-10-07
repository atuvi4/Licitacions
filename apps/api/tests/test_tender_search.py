"""Cerca normalitzada, filtres i ordre neutral del cataleg (spec 3B-1 §7).

«Totes» es qualsevol licitacio publica visible: amb pre-fit o sense, `low_fit`,
`excluded`, sense CPV, sense import, sense territori i ja no disponible a la
font. Cap `INNER JOIN` amb els pre-fits ni cap `min_score` hi decideix res, i
l'ordre per defecte es neutral (publicacio desc nuls al final, `first_seen_at`
desc, id).

La cerca es normalitza a l'aplicacio amb una sola funcio (NFKD, sense
diacritics, casefold), la mateixa per a `search_text` i per a `q`: per aixo
`telecomunicacio` troba «Telecomunicació» sense dependre d'`unaccent`.

Els dos feeds (`/tenders` i `/companies/{c}/matches`) parlen el mateix contracte
de filtres: els tests de comportament s'executen contra tots dos.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa

from app.ingestion.placsp.service import PlacspIngestionService
from app.matching.rules import RULES_VERSION
from app.models.enums import (
    ContractType,
    MatchStatus,
    ProcedureType,
    SourceType,
    TenderStatus,
)
from app.models.matching import CompanyTenderMatch
from app.models.sources import Source
from app.models.tenders import (
    Buyer,
    Tender,
    TenderCPV,
    TenderDocument,
    TenderLot,
    TenderLotCPV,
)
from app.tenders.search import (
    build_search_text,
    normalize_search_text,
    ordered_lot_titles,
    refresh_search_text,
    search_terms,
    sql_fold,
)
from app.tenders.search_backfill import backfill_search_text
from tests.test_matching_api import ensure_cpv_codes

FIXTURES = Path(__file__).parent / "fixtures" / "placsp"

XARXES = "32412100"
NETEJA = "90911200"
PAPER = "30197630"
INTERNET = "72400000"
OBRES = "45000000"

EXPEDIENT_ESCALA = "EXP-2026/01-ESC"
BASE = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Normalitzacio                                                                 #
# --------------------------------------------------------------------------- #


def test_normalitzacio_determinista():
    assert (
        normalize_search_text("Telecomunicació", "l'Escala  EXP-2026/01")
        == "telecomunicacio l escala exp-2026/01"
    )
    assert normalize_search_text("Ñandú É") == "nandu e"
    assert search_terms(" ".join(f"t{i}" for i in range(9))) == [f"t{i}" for i in range(6)]


def test_normalitzacio_ignora_buits_i_es_idempotent():
    assert normalize_search_text(None, "", "  ") == ""
    for raw in ("Col·legi de l'Àrea  Metropolitana", "ŒUVRE Straße", "Ajuntament_de%Girona"):
        once = normalize_search_text(raw)
        assert normalize_search_text(once) == once
    # `_` i `%` no sobreviuen: un terme mai porta comodins de LIKE.
    assert normalize_search_text("a_b%c") == "a b c"


def test_termes_de_q():
    assert search_terms("  L'ESCALA   escala ") == ["l", "escala"]  # sense repetits
    assert search_terms("") == [] and search_terms(None) == [] and search_terms("'' ,;") == []
    assert search_terms("Telecomunicació") == ["telecomunicacio"]


def test_build_search_text_porta_tots_els_camps():
    tender = SimpleNamespace(
        title="Servei de Telecomunicació",
        expediente_number="EXP-1/2026",
        description="Id licitación: EXP-1/2026; Órgano de Contratación: X; Importe: 1 EUR",
    )
    # La descripcio no hi entra: al 1044 es un text fix (spec §7.2).
    assert (
        build_search_text(tender, "Ajuntament de l'Escala", ["Lot 1: Enllaços", None])
        == "servei de telecomunicacio exp-1/2026 ajuntament de l escala lot 1 enllacos"
    )
    empty = SimpleNamespace(title="", expediente_number=None, description="Només descripció")
    assert build_search_text(empty, None, []) is None


def test_els_titols_de_lot_tenen_un_ordre_determinista():
    lots = [
        SimpleNamespace(lot_number=None, external_lot_id="Z", title="sense numero"),
        SimpleNamespace(lot_number=10, external_lot_id="10", title="deu"),
        SimpleNamespace(lot_number=2, external_lot_id="2b", title="dos b"),
        SimpleNamespace(lot_number=2, external_lot_id="2a", title="dos a"),
        SimpleNamespace(lot_number=1, external_lot_id="1", title="u"),
    ]
    expected = ["u", "dos a", "dos b", "deu", "sense numero"]
    assert ordered_lot_titles(lots) == expected
    assert ordered_lot_titles(list(reversed(lots))) == expected


@pytest.mark.parametrize(
    "variant",
    ["instal·lació", "installacio", "instal.lacio", "INSTAL·LACIÓ", "Instal‧lació", "INSTAL.LACIÓ"],
)
def test_la_ela_geminada_es_plega_a_ll(variant):
    assert normalize_search_text(variant) == "installacio"
    assert search_terms(variant) == ["installacio"]


def test_la_ela_geminada_no_toca_la_resta():
    # La ela simple (falta d'ortografia) no es la geminada: el plegat no endevina.
    assert normalize_search_text("instalacio") == "instalacio"
    # Nomes `l.l` (ela, punt, ela); la resta de punts es conserven.
    assert normalize_search_text("Col·legi") == "collegi"
    assert normalize_search_text("Exp. 1.2/2026") == "exp. 1.2/2026"
    assert normalize_search_text("al. la") == "al. la"
    assert normalize_search_text("Telecomunicació · Xarxes") == "telecomunicacio xarxes"


# --------------------------------------------------------------------------- #
# Cataleg de prova                                                              #
# --------------------------------------------------------------------------- #


async def _source(db, code: str, name: str, kind=SourceType.SYNDICATION_ATOM) -> Source:
    source = Source(code=code, name=name, source_type=kind, enabled=True)
    db.add(source)
    await db.flush()
    return source


async def _tender(
    db,
    source: Source,
    title: str,
    *,
    published: date | None,
    first_seen: datetime,
    amount: str | None,
    cpvs: tuple[str, ...] = (),
    buyer: Buyer | None = None,
    description: str | None = None,
    expediente: str | None = None,
    owner=None,
    place: str | None = None,
    nuts: str | None = None,
    source_updated_at: datetime | None = None,
    source_removed_at: datetime | None = None,
) -> Tender:
    tender = Tender(
        source_id=source.id,
        external_id=f"urn:test:{title}",
        expediente_number=expediente,
        title=title,
        description=description,
        buyer_id=buyer.id if buyer else None,
        owner_company_id=owner,
        procedure_type=ProcedureType.OPEN,
        contract_type=ContractType.SERVICES,
        status=TenderStatus.OPEN,
        estimated_value=Decimal(amount) if amount else None,
        currency="EUR",
        publication_date=published,
        place_of_execution=place,
        nuts_code=nuts,
        first_seen_at=first_seen,
        last_seen_at=first_seen,
        source_updated_at=source_updated_at,
        source_removed_at=source_removed_at,
    )
    db.add(tender)
    await db.flush()
    for index, code in enumerate(cpvs):
        db.add(TenderCPV(tender_id=tender.id, cpv_code=code, is_main=index == 0))
    await db.flush()
    return tender


async def _lot(db, tender: Tender, number: int, title: str, *, cpv=None, removed=False, place=None):
    lot = TenderLot(
        tender_id=tender.id,
        external_lot_id=str(number),
        lot_number=number,
        title=title,
        currency="EUR",
        place_of_execution=place,
        removed_at=BASE if removed else None,
    )
    db.add(lot)
    await db.flush()
    if cpv:
        db.add(TenderLotCPV(tender_lot_id=lot.id, cpv_code=cpv, is_main=True))
        await db.flush()
    return lot


def _match(company_id, tender: Tender, status: MatchStatus, score: int | None):
    return CompanyTenderMatch(
        company_id=company_id,
        tender_id=tender.id,
        status=status,
        score=score,
        data_coverage=1.0,
        factors=[],
        rules_version=RULES_VERSION,
    )


@pytest.fixture
async def cat(db, tenant_a, tenant_b):
    """Set licitacions visibles per a A (una de cada cas de §7.1) i una privada de B."""
    _, company = tenant_a
    _, other = tenant_b
    await ensure_cpv_codes(db, XARXES, NETEJA, PAPER, INTERNET, OBRES)
    agregadas = await _source(db, "placsp_agregadas", "PLACSP · plataformes agregades")
    placsp = await _source(db, "PLACSP", "Plataforma de Contratacion del Sector Publico")
    manual = await _source(db, "manual", "Importacio manual", SourceType.MANUAL)
    ajuntament = Buyer(
        source_id=agregadas.id,
        external_identifier="L01170660",
        name="Ajuntament de l'Escala",
    )
    db.add(ajuntament)
    await db.flush()

    escala = await _tender(
        db,
        agregadas,
        "Servei de telecomunicació i fibra òptica",
        expediente=EXPEDIENT_ESCALA,
        buyer=ajuntament,
        description="Manteniment de la xarxa municipal",
        published=date(2026, 9, 10),
        first_seen=BASE - timedelta(days=5),
        amount="50000",
        cpvs=(XARXES,),
        place="L'Escala",
        nuts="ES512",
        source_updated_at=BASE - timedelta(hours=1),
    )
    await _lot(db, escala, 1, "Lot 1: Enllaços sense fil", cpv=XARXES)
    await _lot(db, escala, 2, "Lot retirat: radioenllaç antic", removed=True)
    db.add_all(
        [
            TenderDocument(tender_id=escala.id, name="PCAP", url="https://example.test/pcap"),
            TenderDocument(
                tender_id=escala.id, name="Vell", url="https://example.test/vell", removed_at=BASE
            ),
        ]
    )
    excluded = await _tender(
        db,
        placsp,
        "Neteja d'edificis municipals",
        published=date(2026, 9, 9),
        first_seen=BASE - timedelta(days=4),
        amount="20000",
        cpvs=(NETEJA,),
    )
    low = await _tender(
        db,
        placsp,
        "Subministrament de paper",
        published=date(2026, 9, 8),
        first_seen=BASE - timedelta(days=6),
        amount="3000",
        cpvs=(PAPER,),
    )
    await _lot(db, low, 1, "Lot únic: serveis d'internet", cpv=INTERNET, place="Figueres")
    no_cpv = await _tender(
        db,
        placsp,
        "Assessorament jurídic",
        published=date(2026, 9, 8),
        first_seen=BASE - timedelta(days=2),
        amount="9000",
    )
    removed = await _tender(
        db,
        placsp,
        "Obres al port",
        published=date(2026, 9, 5),
        first_seen=BASE - timedelta(days=7),
        amount="100000",
        cpvs=(OBRES,),
        source_updated_at=BASE - timedelta(hours=2),
        source_removed_at=BASE - timedelta(days=1),
    )
    manual_tender = await _tender(
        db,
        manual,
        "Licitació oberta a mà",
        published=None,
        first_seen=BASE - timedelta(days=1),
        amount="15000",
        owner=company.id,
    )
    sense_import = await _tender(
        db,
        agregadas,
        "Servei sense import conegut",
        published=None,
        first_seen=BASE - timedelta(days=3),
        amount=None,
    )
    private_b = await _tender(
        db,
        manual,
        "Privada de B: telecomunicació a l'Escala",
        published=date(2026, 9, 11),
        first_seen=BASE,
        amount="70000",
        owner=other.id,
        cpvs=(XARXES,),
    )
    db.add_all(
        [
            _match(company.id, escala, MatchStatus.CANDIDATE, 80),
            _match(company.id, excluded, MatchStatus.EXCLUDED, 0),
            _match(company.id, low, MatchStatus.LOW_FIT, 35),
            _match(company.id, removed, MatchStatus.INSUFFICIENT_DATA, None),
            # El pre-fit de B no ha d'arribar mai al feed d'A.
            _match(other.id, escala, MatchStatus.EXCLUDED, 1),
        ]
    )
    await db.flush()
    tenders = (escala, excluded, low, no_cpv, removed, manual_tender, sense_import, private_b)
    for tender in tenders:
        await refresh_search_text(db, tender)
    await db.commit()

    def sid(tender: Tender) -> str:
        return str(tender.id)

    return SimpleNamespace(
        company=company,
        escala=sid(escala),
        excluded=sid(excluded),
        low=sid(low),
        no_cpv=sid(no_cpv),
        removed=sid(removed),
        manual=sid(manual_tender),
        sense_import=sid(sense_import),
        private_b=sid(private_b),
        neutral=[
            sid(escala),
            sid(excluded),
            sid(no_cpv),  # mateixa data que `low`, vista mes tard
            sid(low),
            sid(removed),
            sid(manual_tender),  # sense data de publicacio: al final, per first_seen desc
            sid(sense_import),
        ],
    )


@pytest.fixture(params=["matches", "tenders"])
def page(request, client_a, cat):
    """El mateix contracte als dos feeds. Torna el cos amb les files aplanades."""
    kind = request.param

    async def _page(**params):
        url = (
            f"/api/v1/companies/{cat.company.id}/matches"
            if kind == "matches"
            else "/api/v1/tenders"
        )
        response = await client_a.get(url, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        if kind == "matches":
            body["items"] = [{**item["tender"], "match": item["match"]} for item in body["items"]]
        return body

    _page.kind = kind
    return _page


async def ids(page, **params) -> list[str]:
    return [row["id"] for row in (await page(limit=100, **params))["items"]]


# --------------------------------------------------------------------------- #
# Cerca                                                                         #
# --------------------------------------------------------------------------- #


async def test_accents_i_majuscules_no_importen(page, cat):
    for q in (
        "telecomunicacio",
        "TELECOMUNICACIÓ",
        "Telecomunicació",
        "escala",
        "l'Escala",
        "ESCALA",
    ):
        assert await ids(page, q=q) == [cat.escala], q


async def test_la_fila_exposa_el_pressupost_sense_iva(page, cat, db):
    """El filtre d'import usa `coalesce(estimated, base_sense_iva, base)`: la fila el porta."""
    await db.execute(
        sa.update(Tender)
        .where(Tender.id == uuid.UUID(cat.escala))
        .values(base_budget_without_tax=Decimal("41322.31"), base_budget=Decimal("50000.00"))
    )
    await db.commit()
    rows = {row["id"]: row for row in (await page(limit=100))["items"]}
    assert Decimal(rows[cat.escala]["base_budget_without_tax"]) == Decimal("41322.31")
    assert all("base_budget_without_tax" in row for row in rows.values())


async def test_text_troba_titol_expedient_organisme_i_lots_pero_no_la_descripcio(page, cat):
    assert await ids(page, q=EXPEDIENT_ESCALA) == [cat.escala]
    assert await ids(page, q="exp-2026/01") == [cat.escala]
    assert await ids(page, q="ajuntament escala") == [cat.escala]
    assert await ids(page, q="xarxa municipal") == []  # descripcio: exclosa (spec §7.2)
    assert await ids(page, q="enllacos") == [cat.escala]  # titol de lot vigent
    assert await ids(page, q="radioenllac") == []  # un lot retirat ja no compta
    assert await ids(page, q="internet") == [cat.low]


async def test_cada_terme_ha_de_coincidir(page, cat):
    assert await ids(page, q="escala neteja") == []
    # «municipal» nomes es a la descripcio de l'Escala (exclosa) i al titol d'una altra.
    assert await ids(page, q="municipal") == [cat.excluded]


async def test_q_nomes_usa_els_sis_primers_termes(page, cat):
    six = "servei telecomunicacio fibra optica ajuntament escala"
    assert await ids(page, q=f"{six} inexistent") == [cat.escala]
    assert await ids(page, q="inexistent " + six) == []


async def test_una_q_sense_cap_terme_no_filtra(page, cat):
    assert await ids(page, q="'' ;") == cat.neutral


# --------------------------------------------------------------------------- #
# «Totes»                                                                       #
# --------------------------------------------------------------------------- #


async def test_totes_inclou_tot(page, cat):
    body = await page(limit=100)
    assert body["total"] == 7
    assert {row["id"] for row in body["items"]} == set(cat.neutral)
    assert cat.private_b not in {row["id"] for row in body["items"]}


async def test_totes_porta_el_pre_fit_al_costat_sense_filtrar(client_a, cat):
    body = (
        await client_a.get(f"/api/v1/companies/{cat.company.id}/matches", params={"limit": 100})
    ).json()
    matches = {item["tender"]["id"]: item["match"] for item in body["items"]}
    assert matches[cat.escala]["status"] == "candidate"
    assert matches[cat.excluded]["status"] == "excluded"
    assert matches[cat.low]["status"] == "low_fit"
    assert matches[cat.removed]["status"] == "insufficient_data"
    assert matches[cat.manual] is None and matches[cat.sense_import] is None
    assert matches[cat.no_cpv] is None


@pytest.fixture
async def compiled_sql_for_mode_all(engine, client_a, cat):
    """El SQL real que s'executa per a «Totes» (sense filtre de pre-fit)."""
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(" ".join(statement.split()))

    sa.event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        response = await client_a.get(
            f"/api/v1/companies/{cat.company.id}/matches",
            params={"q": "escala", "sort": "score", "limit": 25},
        )
        assert response.status_code == 200, response.text
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", capture)
    return "\n".join(s for s in statements if "company_tender_matches" in s)


def test_sql_de_totes_no_fa_inner_join_amb_matches(compiled_sql_for_mode_all):
    sql = compiled_sql_for_mode_all
    assert "LEFT OUTER JOIN company_tender_matches" in sql  # el pre-fit hi va al costat
    assert "JOIN company_tender_matches" not in sql.replace(
        "LEFT OUTER JOIN company_tender_matches", ""
    )
    assert "company_tender_matches.score >=" not in sql  # cap min_score


async def test_la_vista_per_estat_si_que_parteix_del_pre_fit(engine, client_a, cat):
    statements: list[str] = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(" ".join(statement.split()))

    sa.event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        body = (
            await client_a.get(
                f"/api/v1/companies/{cat.company.id}/matches",
                params={"match_status": "candidate"},
            )
        ).json()
    finally:
        sa.event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert [item["tender"]["id"] for item in body["items"]] == [cat.escala]
    sql = "\n".join(statements)
    # El test estructural de «Totes» sap detectar un INNER JOIN quan n'hi ha.
    assert "JOIN company_tender_matches" in sql.replace(
        "LEFT OUTER JOIN company_tender_matches", ""
    )


# --------------------------------------------------------------------------- #
# Ordre                                                                         #
# --------------------------------------------------------------------------- #


async def test_ordre_per_defecte_es_neutral(page, cat):
    items = (await page())["items"]
    assert [row["id"] for row in items] == cat.neutral
    assert (await page())["limit"] == 25


async def test_relevance_a_sqlite_o_sense_q_cau_a_l_ordre_neutral(page, cat, engine):
    assert await ids(page, sort="relevance") == cat.neutral
    if engine.dialect.name != "postgresql":
        assert await ids(page, q="servei", sort="relevance") == [
            cat.escala,
            cat.low,
            cat.sense_import,
        ]


async def test_ordenacions(page, cat):
    assert await ids(page, sort="first_seen") == [
        cat.manual,
        cat.no_cpv,
        cat.sense_import,
        cat.excluded,
        cat.escala,
        cat.low,
        cat.removed,
    ]
    assert await ids(page, sort="recent") == await ids(page, sort="first_seen")
    assert await ids(page, sort="source_updated") == [
        cat.escala,
        cat.removed,
        cat.excluded,
        cat.no_cpv,
        cat.low,
        cat.manual,
        cat.sense_import,
    ]
    assert await ids(page, sort="amount") == [
        cat.removed,
        cat.escala,
        cat.excluded,
        cat.manual,
        cat.no_cpv,
        cat.low,
        cat.sense_import,
    ]
    by_score = await ids(page, sort="score")
    if page.kind == "matches":
        assert by_score == [
            cat.escala,
            cat.low,
            cat.excluded,
            cat.no_cpv,
            cat.removed,
            cat.manual,
            cat.sense_import,
        ]
    else:  # sense empresa no hi ha pre-fit: ordre neutral
        assert by_score == cat.neutral


async def test_paginacio_estable(page, cat):
    first = await page(limit=3)
    rest = await page(limit=3, offset=3)
    tail = await page(limit=3, offset=6)
    got = [r["id"] for r in first["items"] + rest["items"] + tail["items"]]
    assert got == cat.neutral and first["total"] == 7


async def test_limit_maxim_100(client_a, cat):
    assert (await client_a.get("/api/v1/tenders", params={"limit": 101})).status_code == 422
    url = f"/api/v1/companies/{cat.company.id}/matches"
    assert (await client_a.get(url, params={"limit": 101})).status_code == 422


async def test_sort_desconegut_es_422(client_a, cat):
    assert (await client_a.get("/api/v1/tenders", params={"sort": "fit"})).status_code == 422


# --------------------------------------------------------------------------- #
# Filtres                                                                       #
# --------------------------------------------------------------------------- #


async def test_filtre_import_no_fixat_no_exclou(page, cat):
    assert (await page())["excluded_without_amount"] is None
    body = await page(min_amount=1000, limit=100)
    assert body["excluded_without_amount"] == 1
    assert cat.sense_import not in {row["id"] for row in body["items"]}
    assert body["total"] == 6
    capped = await page(max_amount=10000, limit=100)
    assert {row["id"] for row in capped["items"]} == {cat.no_cpv, cat.low}
    assert capped["excluded_without_amount"] == 1
    # Comptat amb la resta de filtres aplicats: amb q no hi ha cap sense import.
    assert (await page(min_amount=1, q="escala"))["excluded_without_amount"] == 0


async def test_cpv_prefix_font_i_source_removed(page, cat):
    assert await ids(page, cpv=["3241"]) == [cat.escala]
    assert await ids(page, cpv=[XARXES]) == [cat.escala]
    assert await ids(page, cpv=["724"]) == [cat.low]  # nomes al lot
    assert await ids(page, cpv=["3241", "909"]) == [cat.escala, cat.excluded]
    assert await ids(page, source=["manual"]) == [cat.manual]
    assert await ids(page, source=["placsp_agregadas", "PLACSP"]) == [
        r for r in cat.neutral if r != cat.manual
    ]
    assert await ids(page, source_removed=True) == [cat.removed]
    assert await ids(page, source_removed=False) == [r for r in cat.neutral if r != cat.removed]


async def test_cpv_invalid_no_ho_retorna_tot(page, cat):
    assert await ids(page, cpv=["abc"]) == []


async def test_organisme_i_lloc_amb_text_normalitzat(page, cat):
    for buyer_q in ("escala", "L'ESCALA", "ajuntament  l'escala"):
        assert await ids(page, buyer_q=buyer_q) == [cat.escala], buyer_q
    assert await ids(page, buyer_q="girona") == []
    for place_q in ("escala", "L'Escala", "ESCALA"):
        assert await ids(page, place_q=place_q) == [cat.escala], place_q
    assert await ids(page, place_q="figueres") == [cat.low]  # lloc del lot


async def test_els_filtres_existents_continuen(page, cat):
    assert await ids(page, nuts="es51") == [cat.escala]
    assert await ids(page, has_lots=True) == [cat.escala, cat.low]
    assert await ids(page, published_from="2026-09-09") == [cat.escala, cat.excluded]


async def test_has_lots_nomes_compta_lots_vigents(db, page, cat):
    """Mateixa regla que `lot_count`: un lot retirat de la font ja no hi es."""
    source = (await db.execute(sa.select(Source).where(Source.code == "PLACSP"))).scalar_one()
    retirats = await _tender(
        db,
        source,
        "Lots tots retirats",
        published=date(2026, 9, 1),
        first_seen=BASE - timedelta(days=8),
        amount="1000",
    )
    await _lot(db, retirats, 1, "Lot retirat 1", removed=True)
    await _lot(db, retirats, 2, "Lot retirat 2", removed=True)
    await refresh_search_text(db, retirats)
    await db.commit()
    retirats_id = str(retirats.id)

    assert await ids(page, has_lots=True) == [cat.escala, cat.low]
    sense = await ids(page, has_lots=False)
    assert retirats_id in sense and cat.escala not in sense and cat.low not in sense
    row = next(r for r in (await page(limit=100))["items"] if r["id"] == retirats_id)
    assert row["lot_count"] == 0


# --------------------------------------------------------------------------- #
# Fila i facetes                                                                #
# --------------------------------------------------------------------------- #


async def test_la_fila_porta_font_presencia_documents_i_lots(page, cat):
    rows = {row["id"]: row for row in (await page(limit=100))["items"]}
    escala = rows[cat.escala]
    assert escala["source_code"] == "placsp_agregadas"
    assert escala["source_updated_at"] is not None
    assert escala["source_removed_at"] is None
    assert escala["document_count"] == 1  # el retirat no compta
    assert escala["lot_count"] == 1  # el lot retirat no compta
    assert rows[cat.removed]["source_removed_at"] is not None
    assert rows[cat.manual]["source_code"] == "manual"
    assert rows[cat.manual]["document_count"] == 0
    if page.kind == "tenders":
        assert "match" not in escala  # el cataleg public no parla de pre-fit


async def test_facetes_fonts_i_comptadors_per_estat(client_a, cat):
    facets = (await client_a.get(f"/api/v1/companies/{cat.company.id}/tenders/facets")).json()
    assert facets["total_tenders"] == 7
    assert facets["sources"] == [
        {"code": "PLACSP", "name": "Plataforma de Contratacion del Sector Publico", "count": 4},
        {"code": "manual", "name": "Importacio manual", "count": 1},
        {"code": "placsp_agregadas", "name": "PLACSP · plataformes agregades", "count": 2},
    ]
    assert facets["status_counts"] == {"open": 7}
    assert facets["statuses"] == ["open"]


# --------------------------------------------------------------------------- #
# Manteniment de `search_text`                                                  #
# --------------------------------------------------------------------------- #


async def test_la_ingesta_mante_search_text(db, storage):
    await PlacspIngestionService(db, storage).run(fixture=FIXTURES / "feed_sample.atom")
    tenders = (await db.execute(sa.select(Tender))).scalars().unique().all()
    assert tenders
    for tender in tenders:
        buyer = await db.get(Buyer, tender.buyer_id) if tender.buyer_id else None
        lots = [lot.title for lot in tender.lots if lot.removed_at is None]
        expected = build_search_text(tender, buyer.name if buyer else None, lots)
        assert tender.search_text == expected
        assert tender.search_text and tender.search_text == normalize_search_text(
            tender.search_text
        )


async def test_l_alta_i_l_edicio_manual_mantenen_search_text(client_a, tenant_a):
    _, company = tenant_a
    created = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders",
        json={
            "client_token": str(uuid.uuid4()),
            "title": "Telecomunicació del Port",
            "expediente_number": "MAN-1",
            "buyer_manual": {
                "client_token": str(uuid.uuid4()),
                "name": "Consell Comarcal de l'Alt Empordà",
            },
        },
    )
    assert created.status_code == 201, created.text
    tender_id = created.json()["tender"]["id"]

    async def found(q: str) -> bool:
        body = (await client_a.get("/api/v1/tenders", params={"q": q})).json()
        return tender_id in {row["id"] for row in body["items"]}

    assert await found("telecomunicacio port") and await found("emporda") and await found("man-1")

    patched = await client_a.patch(
        f"/api/v1/companies/{company.id}/tenders/{tender_id}",
        json={"expected_revision": 1, "title": "Enllumenat públic", "description": "Fanals LED"},
    )
    assert patched.status_code == 200, patched.text
    assert await found("enllumenat public")
    assert not await found("fanals")  # la descripcio no entra a la cerca
    assert not await found("telecomunicacio")
    assert await found("emporda")  # l'organisme no s'ha tocat


async def test_el_backfill_despres_d_una_ingesta_es_un_no_op(db, storage, tmp_path):
    """Lots publicats en un ordre que no es el seu: ingesta i backfill coincideixen."""
    text = (FIXTURES / "agregadas" / "entry_lots.atom").read_text(encoding="utf-8")
    pattern = re.compile(
        r"(<cac:ProcurementProjectLot>.*?</cac:ProcurementProjectLot>)(\s*)"
        r"(<cac:ProcurementProjectLot>.*?</cac:ProcurementProjectLot>)",
        re.DOTALL,
    )
    swapped, replaced = pattern.subn(r"\3\2\1", text)
    assert replaced == 2, "dues entrades amb 2 lots cadascuna"
    variant = tmp_path / "entry_lots_reversed.atom"
    variant.write_text(swapped, encoding="utf-8")

    run = await PlacspIngestionService(db, storage).run(
        fixture=variant, source_code="placsp_agregadas"
    )
    assert run.tenders_created == 4
    result = await backfill_search_text(db)
    assert result.scanned == 4 and result.updated == 0
    lots_first = await db.scalar(
        sa.select(Tender.search_text).where(Tender.expediente_number == "CTON2026000028")
    )
    assert lots_first.index("lot 1. fase 1") < lots_first.index("lot 2. fase 4")


async def test_backfill_recalcula_i_es_idempotent(db, cat):
    await db.execute(sa.update(Tender).values(search_text=None))
    await db.execute(
        sa.update(Tender).where(Tender.id == uuid.UUID(cat.low)).values(search_text="brut")
    )
    await db.commit()

    first = await backfill_search_text(db, batch_size=3)
    assert first.scanned == 8 and first.updated == 8
    again = await backfill_search_text(db, batch_size=3)
    assert again.scanned == 8 and again.updated == 0

    escala = await db.get(Tender, uuid.UUID(cat.escala))
    await db.refresh(escala)
    assert "ajuntament de l escala" in escala.search_text
    assert "enllacos" in escala.search_text and "radioenllac" not in escala.search_text


# --------------------------------------------------------------------------- #
# Plegat SQL de noms (buyer_q / place_q)                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        "Ajuntament de l'Escala",
        "AJUNTAMENT DE L'ESCALA",
        "Col·legi Oficial d'Enginyers",
        "COŀLEGI",
        "Diputació de València — Àrea de Serveis",
        "Concello de A Coruña",
        "ÑANDÚ Œuvre Ærø",
        "Udal Bilbo-Getxo / Área 3.1",
        "Instal.lacions Esportives",
        "INSTAL‧LACIONS",
    ],
)
async def test_sql_fold_coincideix_amb_la_normalitzacio(db, raw):
    folded = (await db.execute(sa.select(sql_fold(sa.literal(raw))))).scalar_one()
    assert normalize_search_text(folded) == normalize_search_text(raw)
    # Tot caracter alfanumeric del plegat SQL ja es en la forma normalitzada: les
    # tirades de lletres/digits (que es on cau un terme) son identiques.
    for char in folded:
        if char.isalnum():
            assert normalize_search_text(char) == char, (raw, char, unicodedata.name(char))


@pytest.mark.parametrize(
    ("raw", "term"),
    [
        ("Col·legi Oficial", "collegi"),
        ("COŀLEGI", "collegi"),
        ("Instal.lacions Esportives", "installacions"),
        ("INSTAL‧LACIONS", "installacions"),
        ("Instal·lació", "installacio"),
    ],
)
async def test_sql_fold_plega_l_ela_geminada(db, raw, term):
    assert search_terms(raw)[0] == term
    matched = (
        await db.execute(sa.select(sql_fold(sa.literal(raw)).like(f"%{term}%")))
    ).scalar_one()
    assert matched
