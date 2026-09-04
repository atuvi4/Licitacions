"""Protegeix les pantalles de pre-fit encara que el tenant demo no tingui candidates.

Sobre les dades reals ingerides, el perfil demo dona **zero candidates**, i aixo
es una resposta correcta (vegeu `docs/matching.md`). Pero vol dir que el cami mes
important de la interficie — el d'una licitacio que SI encaixa — no s'exercitaria
mai sol.

Aquest fitxer munta, nomes dins del test, una licitacio de cada estat possible i
comprova que l'API retorna tot el que la pantalla necessita per pintar-la. Son
fixtures: no toquen cap tenant real i desapareixen amb la base de dades del test.
La temptacio a evitar es la contraria — inflar el perfil demo amb CPV inventats
perque surtin candidates a la demo.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.matching.rules import CANDIDATE_THRESHOLD, HIGH_FIT_THRESHOLD
from app.matching.service import MatchingService
from app.models.enums import TenderStatus
from app.models.tenders import Tender
from tests.test_matching_api import (
    SANITAT,
    TELECOM,
    add_lot,
    configure_company,
    make_source,
    make_tender,
)

NETEJA = "90910000"  # serveis de neteja: cap relacio amb el perfil


@pytest.fixture
async def scenarios(db, tenant_a):
    """Una licitacio per a cada estat que la interficie ha de saber pintar."""
    _, company = tenant_a
    source = await make_source(db)

    # Encaix perfecte: CPV exacte, territori prioritari, import a la franja
    # preferida i termini folgat.
    await make_tender(db, source, title="Encaix alt: fibra a Girona")

    # Puntua, pero sense cap CPV en comu: el sostre per CPV no la deixa passar.
    await make_tender(db, source, title="Encaix baix: obra a Girona", cpv_codes=(NETEJA,))

    # Exclusio de negoci: supera el maxim assumible configurat.
    await make_tender(db, source, title="Descartada: massa gran", amount="5000000")

    # Exclusio d'estat.
    await make_tender(db, source, title="Fora de termini", status=TenderStatus.AWARDED)

    # Sense CPV, sense lloc i sense import: nomes es coneix el termini.
    await make_tender(
        db, source, title="Sense dades suficients", cpv_codes=(), nuts_code=None,
        place=None, amount=None,
    )

    # Expedient amb lots on nomes un encaixa.
    multi = await make_tender(db, source, title="Contracte mixt amb lots", cpv_codes=(SANITAT,))
    await add_lot(db, multi, number=1, title="Lot sanitari", cpv_codes=(SANITAT,))
    await add_lot(db, multi, number=2, title="Lot de xarxa", cpv_codes=(TELECOM,))
    await add_lot(db, multi, number=3, title="Lot de neteja", cpv_codes=(NETEJA,))

    await db.commit()
    await configure_company(db, company.id)
    await MatchingService(db).recalculate(company.id)
    return company


async def feed(client, company, **params):
    query = "&".join(f"{key}={value}" for key, value in params.items())
    response = await client.get(f"/api/v1/companies/{company.id}/matches?{query}")
    assert response.status_code == 200, response.text
    return response.json()


class TestFeedModes:
    """Cada mode del feed ha de tenir alguna cosa a ensenyar."""

    async def test_candidates(self, client_a, scenarios):
        body = await feed(client_a, scenarios, match_status="candidate", sort="score")
        titles = [item["tender"]["title"] for item in body["items"]]
        assert "Encaix alt: fibra a Girona" in titles
        top = body["items"][0]["match"]
        assert top["score"] >= HIGH_FIT_THRESHOLD
        assert top["status"] == "candidate"

    async def test_encaix_baix(self, client_a, scenarios):
        body = await feed(client_a, scenarios, match_status="low_fit")
        assert body["total"] >= 1
        for item in body["items"]:
            match = item["match"]
            assert match["status"] == "low_fit"
            # Te puntuacio: es el que distingeix "encaixa poc" de "descartada".
            assert match["score"] is not None
            assert match["score"] < CANDIDATE_THRESHOLD or match["cpv_score"] == 0

    async def test_descartades(self, client_a, scenarios):
        body = await feed(client_a, scenarios, match_status="excluded")
        assert body["total"] == 1
        match = body["items"][0]["match"]
        assert match["score"] is None, "una exclusio no ha d'inventar cap numero"
        assert match["exclusion_kind"] == "business"
        assert match["exclusion_reason_code"] == "AMOUNT_ABOVE_MAXIMUM"

    async def test_fora_de_termini(self, client_a, scenarios):
        body = await feed(client_a, scenarios, match_status="closed")
        assert body["total"] == 1
        match = body["items"][0]["match"]
        assert match["exclusion_kind"] == "status"
        assert match["exclusion_reason_code"] == "STATUS_NOT_OPEN"

    async def test_sense_dades_suficients(self, client_a, scenarios):
        body = await feed(client_a, scenarios, match_status="insufficient_data")
        assert body["total"] == 1
        match = body["items"][0]["match"]
        assert match["score"] is None
        assert match["data_coverage"] < 0.5
        # Les dimensions no avaluables han de sortir marcades com a tals perque
        # la pantalla pugui dir "no tenim la dada" i no "no encaixa".
        unknown = [f for f in match["factors"] if f["result"] == "unknown"]
        assert len(unknown) >= 3
        assert all(factor["score"] is None for factor in unknown)

    async def test_el_mode_totes_no_amaga_res(self, client_a, scenarios):
        body = await feed(client_a, scenarios, limit=50)
        assert body["total"] == 6
        assert all(item["match"] is not None for item in body["items"])


class TestExplicabilitatALaUI:
    async def test_cada_fila_porta_les_quatre_dimensions(self, client_a, scenarios):
        body = await feed(client_a, scenarios, limit=50)
        for item in body["items"]:
            factors = item["match"]["factors"]
            assert {factor["dimension"] for factor in factors} == {
                "cpv",
                "geography",
                "economic",
                "timing",
            }
            for factor in factors:
                assert factor["reason_code"], "sense motiu no es pot explicar la puntuacio"
                assert factor["max_score"] > 0

    async def test_el_candidat_compara_perfil_i_licitacio(self, client_a, scenarios):
        body = await feed(client_a, scenarios, match_status="candidate")
        factors = {f["dimension"]: f for f in body["items"][0]["match"]["factors"]}
        assert factors["cpv"]["company_value"]
        assert factors["cpv"]["source_value"]
        assert factors["geography"]["company_value"] == "Girona"


class TestLots:
    async def test_lexpedient_mostra_el_millor_lot(self, client_a, db, scenarios):
        tender_id = (
            await db.execute(
                sa.select(Tender.id).where(Tender.title == "Contracte mixt amb lots")
            )
        ).scalar_one()
        response = await client_a.get(
            f"/api/v1/companies/{scenarios.id}/matches/by-tender/{tender_id}"
        )
        assert response.status_code == 200
        match = response.json()

        assert match["total_lot_count"] == 3
        assert match["candidate_lot_count"] == 1
        assert match["best_lot_id"] is not None

        lots = match["lot_matches"]
        assert len(lots) == 3
        # La pantalla els pinta en aquest ordre: el millor a dalt.
        scores = [lot["score"] for lot in lots]
        assert scores == sorted(scores, key=lambda value: -(value or 0))
        assert lots[0]["tender_lot_id"] == match["best_lot_id"]
        assert lots[0]["lot_title"] == "Lot de xarxa"
        assert lots[0]["status"] == "candidate"
        # El resum de l'expedient es el del millor lot, no la mitjana.
        assert match["score"] == lots[0]["score"]


class TestMetriquesDelDashboard:
    async def test_el_resum_quadra_amb_el_feed(self, client_a, scenarios):
        response = await client_a.get(f"/api/v1/companies/{scenarios.id}/matches/summary")
        summary = response.json()
        # Dues: l'encaix directe i l'expedient mixt, que hi entra perque un dels
        # seus lots encaixa. Es precisament el cas que justifica puntuar per lot:
        # amb una mitjana dels tres lots no hauria sortit.
        assert summary["candidates"] == 2
        assert summary["high_fit"] == 2
        assert summary["excluded"] == 1
        assert summary["closed"] == 1
        assert summary["insufficient_data"] == 1
        assert summary["actionable"] == summary["candidates"] + summary["low_fit"]
        assert (
            summary["candidates"]
            + summary["low_fit"]
            + summary["excluded"]
            + summary["closed"]
            + summary["insufficient_data"]
            == summary["total_tenders"]
        )

    async def test_zero_candidates_es_un_estat_valid(self, db, client_a, tenant_a):
        """El cas real del tenant demo: res no encaixa, i el resum ho ha de dir.

        La pantalla ensenya un buit explicat, no un error.
        """
        _, company = tenant_a
        source = await make_source(db)
        await make_tender(db, source, title="Res a veure", cpv_codes=(NETEJA,))
        await db.commit()
        await configure_company(db, company.id)
        await MatchingService(db).recalculate(company.id)

        summary = (
            await client_a.get(f"/api/v1/companies/{company.id}/matches/summary")
        ).json()
        assert summary["candidates"] == 0
        assert summary["total_tenders"] == 1
        body = await feed(client_a, company, match_status="candidate")
        assert body["total"] == 0
        assert body["items"] == []


class TestOrdenacio:
    async def test_per_puntuacio_els_sense_numero_van_al_final(self, client_a, scenarios):
        body = await feed(client_a, scenarios, sort="score", limit=50)
        scores = [item["match"]["score"] for item in body["items"]]
        with_score = [value for value in scores if value is not None]
        assert with_score == sorted(with_score, reverse=True)
        assert scores.index(None) > len(with_score) - 1

    async def test_per_termini(self, client_a, scenarios):
        body = await feed(client_a, scenarios, sort="deadline", limit=50)
        deadlines = [
            item["tender"]["deadline_at"]
            for item in body["items"]
            if item["tender"]["deadline_at"]
        ]
        assert deadlines == sorted(deadlines)


class TestFrescor:
    async def test_una_licitacio_nova_no_te_pre_fit_fins_que_es_recalcula(
        self, db, client_a, scenarios
    ):
        """La UI ha de saber distingir "encara no calculat" de "no encaixa"."""
        source = await make_source(db)
        tender = await make_tender(
            db, source, title="Arribada despres del calcul",
            days_left=40,
        )
        await db.commit()

        response = await client_a.get(
            f"/api/v1/companies/{scenarios.id}/matches/by-tender/{tender.id}"
        )
        assert response.status_code == 404

        await client_a.post(f"/api/v1/companies/{scenarios.id}/matches/recalculate")
        response = await client_a.get(
            f"/api/v1/companies/{scenarios.id}/matches/by-tender/{tender.id}"
        )
        assert response.status_code == 200
        assert response.json()["status"] == "candidate"


class TestDadesDeTest:
    async def test_els_escenaris_no_toquen_cap_tenant_real(self, db, scenarios):
        """Aquest fitxer nomes crea dades dins de la seva propia base de dades.

        La comprovacio es barata i deixa constancia de la regla: si algun dia
        algu apunta aquests tests a una base de dades compartida, ho sabra.
        """
        from app.models.identity import Company

        names = (
            await db.execute(sa.select(Company.legal_name))
        ).scalars().all()
        assert "Empresa Demo Telecom SL" not in names
        assert all(not name.startswith("Smoke Test") for name in names)


class TestDeterminisme:
    async def test_recalcular_no_canvia_res(self, db, client_a, scenarios):
        before = await feed(client_a, scenarios, sort="score", limit=50)
        await client_a.post(f"/api/v1/companies/{scenarios.id}/matches/recalculate")
        after = await feed(client_a, scenarios, sort="score", limit=50)

        assert [item["match"]["score"] for item in before["items"]] == [
            item["match"]["score"] for item in after["items"]
        ]
        assert [item["match"]["status"] for item in before["items"]] == [
            item["match"]["status"] for item in after["items"]
        ]
