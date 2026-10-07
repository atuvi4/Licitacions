"""Tests del pre-fit sobre base de dades i HTTP.

Aqui es comprova el que el motor pur no pot comprovar: persistencia, lots,
recalcul, obsolescencia i sobretot **aillament multi-tenant** — el pre-fit es
una dada de tenant i un usuari d'Empresa A no ha de poder veure el d'Empresa B.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.matching.service import MatchingService
from app.models.cpv import CPVCode, CompanyCPVPreference
from app.models.enums import (
    ContractType,
    MatchStatus,
    ProcedureType,
    SourceType,
    TenderStatus,
    TerritoryType,
)
from app.models.matching import CompanyTenderLotMatch, CompanyTenderMatch
from app.models.profile import CompanyContractPreferences, CompanyTerritory
from app.models.sources import Source
from app.models.tenders import Buyer, Tender, TenderCPV, TenderLot, TenderLotCPV
from app.tenders.search import build_search_text

TELECOM = "45314320"   # Instal.lacio de cablejat informatic
SANITAT = "85100000"   # Serveis de salut


async def ensure_cpv_codes(db, *codes: str) -> None:
    existing = set(
        (await db.execute(sa.select(CPVCode.code).where(CPVCode.code.in_(codes)))).scalars().all()
    )
    for code in codes:
        if code not in existing:
            db.add(CPVCode(code=code, description=f"CPV {code}", parent_code=code[:2]))
    await db.flush()


async def make_source(db) -> Source:
    source = Source(
        code=f"TEST-{uuid.uuid4().hex[:6]}",
        name="Font de proves",
        source_type=SourceType.SYNDICATION_ATOM,
        enabled=True,
    )
    db.add(source)
    await db.flush()
    return source


async def make_tender(
    db,
    source: Source,
    *,
    title: str = "Licitacio de proves",
    cpv_codes: tuple[str, ...] = (TELECOM,),
    nuts_code: str | None = "ES512",
    place: str | None = "Girona",
    amount: str | None = "300000",
    status: TenderStatus = TenderStatus.OPEN,
    days_left: int | None = 30,
    buyer: Buyer | None = None,
) -> Tender:
    await ensure_cpv_codes(db, *cpv_codes)
    now = datetime.now(UTC)
    tender = Tender(
        source_id=source.id,
        external_id=f"urn:test:{uuid.uuid4()}",
        expediente_number=f"EXP-{uuid.uuid4().hex[:6]}",
        title=title,
        buyer_id=buyer.id if buyer else None,
        procedure_type=ProcedureType.OPEN,
        contract_type=ContractType.SERVICES,
        status=status,
        estimated_value=Decimal(amount) if amount else None,
        base_budget=Decimal(amount) if amount else None,
        currency="EUR",
        deadline_at=now + timedelta(days=days_left) if days_left is not None else None,
        place_of_execution=place,
        nuts_code=nuts_code,
        first_seen_at=now,
        last_seen_at=now,
    )
    # Com ho fan els camins d'escriptura reals (ingesta, alta manual): `q` cerca
    # sobre `search_text` normalitzat (spec 3B-1 §7.2).
    tender.search_text = build_search_text(tender, buyer.name if buyer else None, [])
    db.add(tender)
    await db.flush()
    for index, code in enumerate(cpv_codes):
        db.add(TenderCPV(tender_id=tender.id, cpv_code=code, is_main=index == 0))
    await db.flush()
    return tender


async def add_lot(
    db,
    tender: Tender,
    *,
    number: int,
    title: str,
    cpv_codes: tuple[str, ...] | None = None,
    amount: str | None = None,
    nuts_code: str | None = None,
) -> TenderLot:
    lot = TenderLot(
        tender_id=tender.id,
        external_lot_id=str(number),
        lot_number=number,
        title=title,
        estimated_value=Decimal(amount) if amount else None,
        base_budget=Decimal(amount) if amount else None,
        currency="EUR",
        nuts_code=nuts_code,
    )
    db.add(lot)
    await db.flush()
    if cpv_codes:
        await ensure_cpv_codes(db, *cpv_codes)
        for index, code in enumerate(cpv_codes):
            db.add(TenderLotCPV(tender_lot_id=lot.id, cpv_code=code, is_main=index == 0))
        await db.flush()
    return lot


async def configure_company(
    db,
    company_id: uuid.UUID,
    *,
    cpv: tuple[str, ...] = (TELECOM,),
    territory: tuple[str, str] = ("Girona", "ES512"),
    capacity: bool = True,
) -> None:
    await ensure_cpv_codes(db, *cpv)
    for code in cpv:
        db.add(
            CompanyCPVPreference(
                company_id=company_id, cpv_code=code, priority=10, active=True, excluded=False
            )
        )
    name, code = territory
    db.add(
        CompanyTerritory(
            company_id=company_id,
            name=name,
            territory_type=TerritoryType.PROVINCE,
            code=code,
            priority=10,
            willing_to_work=True,
        )
    )
    if capacity:
        db.add(
            CompanyContractPreferences(
                company_id=company_id,
                minimum_interesting_contract=Decimal("30000"),
                preferred_contract_min=Decimal("80000"),
                preferred_contract_max=Decimal("600000"),
                maximum_recommended_contract=Decimal("900000"),
                maximum_assumable_contract=Decimal("1200000"),
                currency="EUR",
            )
        )
    await db.commit()


# ---------------------------------------------------------------------------
# Servei: persistencia i lots
# ---------------------------------------------------------------------------


class TestMatchingService:
    async def test_calcula_i_desa_el_pre_fit(self, db, tenant_a):
        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source)
        await db.commit()
        await configure_company(db, company.id)

        summary = await MatchingService(db).recalculate(company.id)
        assert summary.tenders_evaluated == 1
        assert summary.created == 1

        match = (
            await db.execute(
                sa.select(CompanyTenderMatch).where(CompanyTenderMatch.company_id == company.id)
            )
        ).scalar_one()
        assert match.score == 100
        assert match.status is MatchStatus.CANDIDATE
        assert len(match.factors) == 4

    async def test_recalcular_dues_vegades_actualitza_no_duplica(self, db, tenant_a):
        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source)
        await db.commit()
        await configure_company(db, company.id)

        service = MatchingService(db)
        await service.recalculate(company.id)
        second = await service.recalculate(company.id)

        assert second.created == 0
        assert second.updated == 1
        total = (
            await db.execute(sa.select(sa.func.count()).select_from(CompanyTenderMatch))
        ).scalar_one()
        assert total == 1

    async def test_el_resum_dun_expedient_es_el_seu_millor_lot(self, db, tenant_a):
        _, company = tenant_a
        source = await make_source(db)
        # L'expedient global no te CPV: cada lot es d'un sector diferent.
        tender = await make_tender(db, source, cpv_codes=(SANITAT,), title="Contracte mixt")
        await add_lot(db, tender, number=1, title="Lot sanitari", cpv_codes=(SANITAT,))
        await add_lot(db, tender, number=2, title="Lot xarxa", cpv_codes=(TELECOM,))
        await add_lot(db, tender, number=3, title="Lot neteja", cpv_codes=("90910000",))
        await db.commit()
        await configure_company(db, company.id)

        summary = await MatchingService(db).recalculate(company.id)
        assert summary.lots_evaluated == 3

        match = (
            await db.execute(
                sa.select(CompanyTenderMatch).where(CompanyTenderMatch.company_id == company.id)
            )
        ).scalar_one()
        lots = (
            await db.execute(
                sa.select(CompanyTenderLotMatch).where(
                    CompanyTenderLotMatch.tender_match_id == match.id
                )
            )
        ).scalars().all()

        best = max(lots, key=lambda item: item.score or 0)
        assert match.score == best.score
        assert match.best_lot_id == best.tender_lot_id
        assert match.total_lot_count == 3
        assert match.candidate_lot_count == 1  # nomes el de xarxa
        # El lot bo no queda amagat darrere la mitjana dels tres.
        assert match.score > round(sum(item.score or 0 for item in lots) / 3)

    async def test_limport_del_lot_no_hereta_mai_el_de_lexpedient(self, db, tenant_a):
        """Un lot de 50.000 EUR dins un expedient de 5M no es un contracte de 5M."""
        _, company = tenant_a
        source = await make_source(db)
        tender = await make_tender(db, source, amount="5000000")
        await add_lot(db, tender, number=1, title="Lot petit", amount="200000")
        await db.commit()
        await configure_company(db, company.id)

        await MatchingService(db).recalculate(company.id)
        lot_match = (
            await db.execute(sa.select(CompanyTenderLotMatch))
        ).scalar_one()
        # Si hagues heretat els 5M seria EXCLUDED per superar el maxim assumible.
        assert lot_match.status is MatchStatus.CANDIDATE

    async def test_les_licitacions_tancades_es_marquen_pero_no_es_perden(self, db, tenant_a):
        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source, status=TenderStatus.AWARDED, title="Ja adjudicada")
        await db.commit()
        await configure_company(db, company.id)

        await MatchingService(db).recalculate(company.id)
        match = (await db.execute(sa.select(CompanyTenderMatch))).scalar_one()
        assert match.status is MatchStatus.CLOSED
        assert match.score is None
        # El motiu queda registrat: es pot explicar per que no apareix.
        assert match.exclusion_reason_code == "STATUS_NOT_OPEN"


# ---------------------------------------------------------------------------
# Aillament multi-tenant
# ---------------------------------------------------------------------------


class TestMultiTenant:
    @pytest.fixture
    async def two_companies(self, db, tenant_a, tenant_b):
        """Mateixa licitacio, dos perfils oposats."""
        _, company_a = tenant_a
        _, company_b = tenant_b
        source = await make_source(db)
        tender = await make_tender(db, source, title="Xarxa de fibra a Girona")
        await db.commit()

        # A: telecom a Girona -> encaixa.
        await configure_company(db, company_a.id)
        # B: sanitat a Sevilla -> no encaixa.
        await configure_company(
            db, company_b.id, cpv=(SANITAT,), territory=("Sevilla", "ES618")
        )

        service = MatchingService(db)
        await service.recalculate(company_a.id)
        await service.recalculate(company_b.id)
        return company_a, company_b, tender

    async def test_la_mateixa_licitacio_puntua_diferent_per_a_cada_empresa(
        self, db, two_companies
    ):
        company_a, company_b, tender = two_companies
        rows = {
            row.company_id: row
            for row in (
                await db.execute(
                    sa.select(CompanyTenderMatch).where(CompanyTenderMatch.tender_id == tender.id)
                )
            ).scalars().all()
        }
        assert rows[company_a.id].status is MatchStatus.CANDIDATE
        assert rows[company_a.id].score == 100
        assert rows[company_b.id].status is MatchStatus.LOW_FIT
        # B suma nomes import i termini (i un territori no declarat): cap punt
        # de CPV. Encara que el numero no sigui zero, el sostre per CPV impedeix
        # que arribi mai a candidata.
        assert rows[company_b.id].score < rows[company_a.id].score - 50

    async def test_el_feed_nomes_retorna_el_pre_fit_propi(
        self, client_a, client_b, two_companies
    ):
        company_a, company_b, _ = two_companies
        response_a = await client_a.get(f"/api/v1/companies/{company_a.id}/matches")
        response_b = await client_b.get(f"/api/v1/companies/{company_b.id}/matches")
        assert response_a.status_code == 200
        assert response_b.status_code == 200
        score_a = response_a.json()["items"][0]["match"]["score"]
        score_b = response_b.json()["items"][0]["match"]["score"]
        assert score_a == 100
        assert score_b != score_a

    async def test_empresa_a_no_pot_llegir_el_feed_dempresa_b(self, client_a, two_companies):
        _, company_b, _ = two_companies
        response = await client_a.get(f"/api/v1/companies/{company_b.id}/matches")
        # 404, no 403: no confirmem ni tan sols que l'empresa existeixi.
        assert response.status_code == 404

    async def test_coneixer_luuid_dun_match_ali_no_serveix_de_res(
        self, db, client_a, two_companies
    ):
        company_a, company_b, _ = two_companies
        foreign = (
            await db.execute(
                sa.select(CompanyTenderMatch).where(
                    CompanyTenderMatch.company_id == company_b.id
                )
            )
        ).scalar_one()
        # Ni pel seu propi camí...
        response = await client_a.get(
            f"/api/v1/companies/{company_a.id}/matches/{foreign.id}"
        )
        assert response.status_code == 404
        # ...ni suplantant l'empresa a la ruta.
        response = await client_a.get(
            f"/api/v1/companies/{company_b.id}/matches/{foreign.id}"
        )
        assert response.status_code == 404

    async def test_empresa_a_no_pot_recalcular_el_pre_fit_dempresa_b(
        self, client_a, two_companies
    ):
        _, company_b, _ = two_companies
        response = await client_a.post(f"/api/v1/companies/{company_b.id}/matches/recalculate")
        assert response.status_code == 404

    async def test_el_resum_no_barreja_empreses(self, client_a, client_b, two_companies):
        company_a, company_b, _ = two_companies
        summary_a = (
            await client_a.get(f"/api/v1/companies/{company_a.id}/matches/summary")
        ).json()
        summary_b = (
            await client_b.get(f"/api/v1/companies/{company_b.id}/matches/summary")
        ).json()
        assert summary_a["candidates"] == 1
        assert summary_b["candidates"] == 0
        # El total de licitacions SI que es compartit: la licitacio es publica.
        assert summary_a["total_tenders"] == summary_b["total_tenders"] == 1


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestMatchingAPI:
    @pytest.fixture
    async def prepared(self, db, tenant_a):
        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source, title="Fibra optica a Girona")
        await make_tender(db, source, title="Servei de psiquiatria", cpv_codes=(SANITAT,))
        await make_tender(
            db, source, title="Manteniment adjudicat", status=TenderStatus.AWARDED
        )
        await db.commit()
        await configure_company(db, company.id)
        await MatchingService(db).recalculate(company.id)
        return company

    async def test_el_feed_requereix_sessio(self, client, prepared):
        response = await client.get(f"/api/v1/companies/{prepared.id}/matches")
        assert response.status_code == 401

    async def test_cada_fila_explica_la_seva_puntuacio(self, client_a, prepared):
        response = await client_a.get(f"/api/v1/companies/{prepared.id}/matches?sort=score")
        top = response.json()["items"][0]["match"]
        assert top["score"] == 100
        dimensions = {factor["dimension"] for factor in top["factors"]}
        assert dimensions == {"cpv", "geography", "economic", "timing"}
        for factor in top["factors"]:
            assert factor["reason_code"]
        assert top["rules_version"] == "metadata-match-v1"

    async def test_filtre_per_estat(self, client_a, prepared):
        response = await client_a.get(
            f"/api/v1/companies/{prepared.id}/matches?match_status=candidate"
        )
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["tender"]["title"] == "Fibra optica a Girona"

    async def test_filtre_per_puntuacio_minima(self, client_a, prepared):
        body = (
            await client_a.get(f"/api/v1/companies/{prepared.id}/matches?min_score=90")
        ).json()
        assert body["total"] == 1

    async def test_la_fitxa_dune_licitacio_porta_el_seu_pre_fit(self, client_a, db, prepared):
        tender_id = (
            await db.execute(
                sa.select(Tender.id).where(Tender.title == "Fibra optica a Girona")
            )
        ).scalar_one()
        response = await client_a.get(
            f"/api/v1/companies/{prepared.id}/matches/by-tender/{tender_id}"
        )
        assert response.status_code == 200
        assert response.json()["score"] == 100

    async def test_licitacio_sense_pre_fit_calculat(self, client_a, prepared):
        response = await client_a.get(
            f"/api/v1/companies/{prepared.id}/matches/by-tender/{uuid.uuid4()}"
        )
        assert response.status_code == 404

    async def test_recalcular_des_de_lapi(self, client_a, prepared):
        response = await client_a.post(f"/api/v1/companies/{prepared.id}/matches/recalculate")
        assert response.status_code == 200
        body = response.json()
        assert body["tenders_evaluated"] == 3
        assert body["rules_version"] == "metadata-match-v1"


# ---------------------------------------------------------------------------
# Obsolescencia
# ---------------------------------------------------------------------------


class TestStaleness:
    async def test_canviar_el_perfil_marca_el_pre_fit_com_a_obsolet(self, db, tenant_a):
        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source, cpv_codes=(SANITAT,), title="Servei sanitari")
        await db.commit()
        await configure_company(db, company.id)
        await MatchingService(db).recalculate(company.id)

        before = (await db.execute(sa.select(CompanyTenderMatch))).scalar_one()
        assert before.status is MatchStatus.LOW_FIT

        # L'empresa amplia la seva activitat.
        await ensure_cpv_codes(db, SANITAT)
        db.add(
            CompanyCPVPreference(
                company_id=company.id, cpv_code=SANITAT, priority=10, active=True, excluded=False
            )
        )
        await db.commit()

        await MatchingService(db).recalculate(company.id)
        await db.refresh(before)
        assert before.status is MatchStatus.CANDIDATE
        assert before.score == 100

    async def test_lapi_marca_els_pre_fits_obsolets(self, db, client_a, tenant_a):
        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source)
        await db.commit()
        await configure_company(db, company.id)
        await MatchingService(db).recalculate(company.id)

        fresh = (await client_a.get(f"/api/v1/companies/{company.id}/matches")).json()
        assert fresh["items"][0]["match"]["is_stale"] is False

        # `updated_at` explicit: SQLite nomes te resolucio de segon i el canvi
        # cauria dins el mateix tic que el calcul anterior. A PostgreSQL (i en
        # us real, on passen segons) no cal.
        db.add(
            CompanyTerritory(
                company_id=company.id,
                name="Tarragona",
                territory_type=TerritoryType.PROVINCE,
                code="ES514",
                priority=5,
                willing_to_work=True,
                updated_at=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
        await db.commit()

        stale = (await client_a.get(f"/api/v1/companies/{company.id}/matches")).json()
        assert stale["items"][0]["match"]["is_stale"] is True
        summary = (await client_a.get(f"/api/v1/companies/{company.id}/matches/summary")).json()
        assert summary["stale"] == 1


class TestSharedFilters:
    """El feed personalitzat ha d'acceptar els mateixos criteris que el public."""

    @pytest.fixture
    async def prepared(self, db, tenant_a):
        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source, title="Fibra a Girona", nuts_code="ES512")
        await make_tender(
            db, source, title="Fibra a Sevilla", nuts_code="ES618", place="Sevilla"
        )
        await db.commit()
        await configure_company(db, company.id)
        await MatchingService(db).recalculate(company.id)
        return company

    async def test_filtre_per_nuts(self, client_a, prepared):
        body = (
            await client_a.get(f"/api/v1/companies/{prepared.id}/matches?nuts=ES51")
        ).json()
        assert body["total"] == 1
        assert body["items"][0]["tender"]["title"] == "Fibra a Girona"

    async def test_filtre_per_cpv(self, client_a, prepared):
        body = (
            await client_a.get(f"/api/v1/companies/{prepared.id}/matches?cpv={TELECOM}")
        ).json()
        assert body["total"] == 2

    async def test_filtre_de_text(self, client_a, prepared):
        body = (
            await client_a.get(f"/api/v1/companies/{prepared.id}/matches?q=sevilla")
        ).json()
        assert body["total"] == 1

    async def test_els_mateixos_criteris_donen_el_mateix_recompte_als_dos_feeds(
        self, client_a, prepared
    ):
        query = f"nuts=ES51&cpv={TELECOM}"
        public = (await client_a.get(f"/api/v1/tenders?{query}")).json()
        personal = (
            await client_a.get(f"/api/v1/companies/{prepared.id}/matches?{query}")
        ).json()
        assert public["total"] == personal["total"]


class TestLogging:
    async def test_el_resum_es_pot_registrar_al_log(self, db, tenant_a, caplog):
        """`LogRecord` ja te camps propis: un `extra` que els repeteixi peta.

        Als tests el nivell per defecte amaga els INFO, aixi que cal forcar-lo
        o aquest error nomes apareix en execucio real.
        """
        import logging

        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source)
        await db.commit()
        await configure_company(db, company.id)

        with caplog.at_level(logging.INFO, logger="app.matching"):
            await MatchingService(db).recalculate(company.id)
        assert any(record.name == "app.matching" for record in caplog.records)
