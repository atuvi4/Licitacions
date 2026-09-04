"""El seed ha de ser idempotent i ha de reparar la deriva.

Abans nomes era idempotent per la via de no fer res: si l'usuari demo existia,
sortia. Aixo evitava duplicats pero no corregia res, i per aixo el nom comercial
del tenant demo va poder quedar-se com "Smoke 9b235f49" indefinidament.
"""

from __future__ import annotations

import sqlalchemy as sa

from app.models.cpv import CompanyCPVPreference
from app.models.documents import Document
from app.models.identity import Company
from app.models.profile import CompanyService, CompanyTerritory
from app.models.projects import CompanyProject
from app.seed import DEMO_COMPANY, seed_into


async def counts(session) -> dict[str, int]:
    result = {}
    for label, model in (
        ("serveis", CompanyService),
        ("territoris", CompanyTerritory),
        ("cpv", CompanyCPVPreference),
        ("projectes", CompanyProject),
        ("documents", Document),
        ("empreses", Company),
    ):
        result[label] = (
            await session.execute(sa.select(sa.func.count()).select_from(model))
        ).scalar_one()
    return result


async def demo_company(session) -> Company:
    return (
        await session.execute(sa.select(Company).where(Company.legal_name == DEMO_COMPANY))
    ).scalar_one()


class TestIdempotencia:
    async def test_la_primera_passada_crea(self, db, storage):
        report = await seed_into(db)
        assert not report.clean
        assert report.created["servei"] == 4
        assert report.created["document"] == 5

    async def test_la_segona_passada_no_fa_res(self, db, storage):
        await seed_into(db)
        report = await seed_into(db)
        assert report.clean, f"creat={report.created} restaurat={report.restored}"

    async def test_ni_la_tercera(self, db, storage):
        await seed_into(db)
        await seed_into(db)
        assert (await seed_into(db)).clean

    async def test_no_duplica_res(self, db, storage):
        await seed_into(db)
        first = await counts(db)
        await seed_into(db)
        await seed_into(db)
        assert await counts(db) == first

    async def test_no_puja_documents_repetits(self, db, storage):
        """El SHA-256 es la clau natural: el mateix contingut no es torna a pujar."""
        await seed_into(db)
        keys = set(
            (await db.execute(sa.select(Document.storage_key))).scalars().all()
        )
        await seed_into(db)
        assert set((await db.execute(sa.select(Document.storage_key))).scalars().all()) == keys


class TestReparacioDeLaDeriva:
    async def test_restaura_el_nom_comercial(self, db, storage):
        """El cas que va passar de debo."""
        await seed_into(db)
        company = await demo_company(db)
        company.trade_name = "Smoke 9b235f49"
        await db.commit()

        report = await seed_into(db)
        assert report.restored.get("empresa") == 1
        await db.refresh(company)
        assert company.trade_name == "Demo Telecom"

    async def test_torna_a_crear_una_fila_esborrada(self, db, storage):
        await seed_into(db)
        company = await demo_company(db)
        await db.execute(
            sa.delete(CompanyService).where(
                CompanyService.company_id == company.id,
                CompanyService.name == "Fibra optica",
            )
        )
        await db.commit()

        report = await seed_into(db)
        assert report.created.get("servei") == 1
        assert (await counts(db))["serveis"] == 4

    async def test_restaura_una_prioritat_de_cpv_canviada(self, db, storage):
        await seed_into(db)
        company = await demo_company(db)
        preference = (
            await db.execute(
                sa.select(CompanyCPVPreference).where(
                    CompanyCPVPreference.company_id == company.id,
                    CompanyCPVPreference.cpv_code == "45314320",
                )
            )
        ).scalar_one()
        preference.priority = 1
        await db.commit()

        await seed_into(db)
        await db.refresh(preference)
        assert preference.priority == 10

    async def test_no_esborra_el_que_no_ha_creat_ell(self, db, storage):
        """Un servei de mes no el treu el seed: no sap d'on surt.

        Es deliberat — esborrar files alienes seria pitjor que deixar-les. Per a
        aixo hi ha `--reset`, que buida el tenant sencer i el reconstrueix.
        """
        await seed_into(db)
        company = await demo_company(db)
        db.add(CompanyService(company_id=company.id, name="Servei smoke 9b235f49"))
        await db.commit()

        report = await seed_into(db)
        assert report.clean
        assert (await counts(db))["serveis"] == 5


class TestDadesFictícies:
    async def test_cap_dada_sembla_real(self, db, storage):
        """El seed no pot portar dades d'una empresa real: son totes inventades."""
        await seed_into(db)
        company = await demo_company(db)
        assert company.tax_id == "B00000000"
        assert "demo" in (company.website or "").lower()
        assert company.email.endswith(".example")
        assert "demostracio" in (company.description or "").lower()

    async def test_la_segona_empresa_serveix_per_provar_laillament(self, db, storage):
        await seed_into(db)
        names = (await db.execute(sa.select(Company.legal_name))).scalars().all()
        assert len(names) == 2, "cal una segona empresa per poder provar el multi-tenant a ma"
