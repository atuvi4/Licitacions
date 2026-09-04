"""Tests de l'aillament dels smoke tests.

El que es prova aqui no es una funcionalitat de producte: es la garantia que les
proves d'integracio no poden embrutar dades que no son seves. Va caldre perque
la versio anterior de `scripts/smoke_test.py` treballava sobre el tenant demo i
li va deixar el nom comercial canviat i tres files residuals.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.core.config import settings
from app.db.base import Base
from app.models.documents import Document, DocumentLink
from app.models.enums import DocumentCategory, LinkedEntityType, RelationshipType
from app.models.identity import Company, CompanyMembership, User
from app.models.profile import CompanyService
from app.services.storage import get_storage
from app.tenant_ops import (
    EPHEMERAL_PREFIX,
    INDIRECT_TABLES,
    PUBLIC_TABLES,
    ProductionGuardError,
    _assert_tables_classified,
    _direct_tables,
    assert_local_not_production,
    assert_mutations_allowed,
    create_ephemeral,
    destroy_tenant,
    list_ephemeral,
    snapshot,
)


class TestGuardaDeProduccio:
    """Cap mutacio pot arribar a produccio, ni per l'entorn local ni pel remot."""

    @pytest.mark.parametrize("value", ["production", "PRODUCTION", "prod", "live", "produccio"])
    def test_lentorn_local_de_produccio_bloqueja(self, value, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", value)
        with pytest.raises(ProductionGuardError):
            assert_local_not_production()

    @pytest.mark.parametrize("value", ["local", "test", "staging", "dev"])
    def test_la_resta_dentorns_passen(self, value, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", value)
        assert_local_not_production()

    def test_el_backend_de_produccio_bloqueja(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")
        with pytest.raises(ProductionGuardError):
            assert_mutations_allowed("production")

    def test_no_saber_contra_que_parlem_tambe_bloqueja(self, monkeypatch):
        """El silenci no es un si.

        Si no es pot llegir l'entorn del backend, l'opcio segura es no mutar
        res: pot ser produccio i no haver-ho pogut confirmar.
        """
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")
        with pytest.raises(ProductionGuardError):
            assert_mutations_allowed(None)

    def test_dos_entorns_no_productius_passen(self, monkeypatch):
        monkeypatch.setattr(settings, "ENVIRONMENT", "local")
        assert_mutations_allowed("local")

    def test_el_nom_de_la_base_de_dades_no_es_un_senyal(self, monkeypatch):
        """Un `DATABASE_URL` que sembli de desenvolupament no autoritza res.

        Es deliberat: apuntar una URL "dev" cap a una instancia real es
        exactament l'error que aquesta guarda ha d'aturar.
        """
        monkeypatch.setattr(settings, "ENVIRONMENT", "production")
        monkeypatch.setattr(settings, "DATABASE_URL", "postgresql+asyncpg://x/dev_local_test")
        with pytest.raises(ProductionGuardError):
            assert_local_not_production()


class TestClassificacioDeTaules:
    def test_cap_taula_queda_sense_classificar(self):
        """Si algu afegeix una taula de tenant i no la classifica, la neteja
        la deixaria enrere en silenci. Aquest test ho impedeix."""
        _assert_tables_classified()

    def test_les_taules_publiques_no_tenen_company_id(self):
        for name in PUBLIC_TABLES:
            table = Base.metadata.tables[name]
            if name in {"companies", "users"}:
                continue
            assert "company_id" not in table.c, f"{name} es de tenant, no publica"

    def test_les_taules_indirectes_arriben_a_una_de_tenant(self):
        direct = set(_direct_tables())
        for name, (_, parent, _) in INDIRECT_TABLES.items():
            assert name not in direct, f"{name} ja te company_id: no cal el cami indirecte"
            assert parent in direct, f"el pare de {name} hauria de ser una taula de tenant"


class TestTenantEfimer:
    async def test_es_crea_amb_nom_i_credencials_propies(self, db):
        tenant = await create_ephemeral(db, label="prova")
        assert tenant.legal_name.startswith(EPHEMERAL_PREFIX)
        assert tenant.email.endswith("@smoke.example")
        assert tenant.password

        company = (
            await db.execute(sa.select(Company).where(Company.id == uuid.UUID(tenant.company_id)))
        ).scalar_one()
        assert company.legal_name == tenant.legal_name

    async def test_cada_execucio_te_el_seu(self, db):
        first = await create_ephemeral(db)
        second = await create_ephemeral(db)
        assert first.company_id != second.company_id
        assert first.email != second.email

    async def test_es_destrueix_sense_deixar_orfes(self, db, storage):
        tenant = await create_ephemeral(db)
        company_id = uuid.UUID(tenant.company_id)

        # Una mica de tot: files directes, indirectes i un objecte a l'storage.
        service = CompanyService(company_id=company_id, name="Servei efimer")
        db.add(service)
        payload = b"%PDF-1.4 efimer\n"
        key = f"companies/{company_id}/2026/09/efimer.pdf"
        await get_storage().upload(key, payload, "application/pdf")
        document = Document(
            company_id=company_id,
            name="Document efimer",
            original_filename="efimer.pdf",
            storage_key=key,
            mime_type="application/pdf",
            file_size=len(payload),
            sha256="0" * 64,
            category=DocumentCategory.OTHER,
        )
        db.add(document)
        await db.flush()
        db.add(
            DocumentLink(
                company_id=company_id,
                document_id=document.id,
                entity_type=LinkedEntityType.COMPANY_PROJECT,
                entity_id=uuid.uuid4(),
                relationship_type=RelationshipType.OTHER,
            )
        )
        await db.commit()

        report = await destroy_tenant(db, company_id, user_id=uuid.UUID(tenant.user_id))

        assert report.clean, report.leftovers
        assert report.user_deleted
        assert report.storage_objects_deleted == 1
        assert not await get_storage().exists(key), "l'objecte hauria d'haver desaparegut"

        for model in (Company, CompanyService, Document, DocumentLink, CompanyMembership):
            column = Company.id if model is Company else model.company_id
            remaining = (
                await db.execute(
                    sa.select(sa.func.count()).select_from(model).where(column == company_id)
                )
            ).scalar_one()
            assert remaining == 0, f"{model.__tablename__} ha deixat files"

        assert (
            await db.execute(
                sa.select(sa.func.count()).select_from(User).where(
                    User.id == uuid.UUID(tenant.user_id)
                )
            )
        ).scalar_one() == 0

    async def test_es_nega_a_esborrar_un_tenant_que_no_sigui_efimer(self, db, tenant_a):
        """La xarxa de seguretat: `destroy` no accepta noms que no siguin d'un
        tenant efimer si no s'hi insisteix explicitament."""
        _, company = tenant_a
        with pytest.raises(ValueError, match="efimer"):
            await destroy_tenant(db, company.id)

        # Amb la insistencia explicita, si.
        report = await destroy_tenant(db, company.id, require_ephemeral=False)
        assert report.clean

    async def test_es_poden_trobar_els_que_hagin_quedat_penjats(self, db, tenant_a):
        """Si una execucio peta a mitges, ha de ser facil trobar-ne el rastre."""
        assert await list_ephemeral(db) == []
        tenant = await create_ephemeral(db)
        pending = await list_ephemeral(db)
        assert [str(company_id) for company_id, _ in pending] == [tenant.company_id]


class TestRadiografia:
    async def test_detecta_qualsevol_canvi(self, db, tenant_a):
        _, company = tenant_a
        before = await snapshot(db, company.id)
        assert before.diff(before) == []

        db.add(CompanyService(company_id=company.id, name="Servei de mes"))
        await db.commit()

        after = await snapshot(db, company.id)
        problems = before.diff(after)
        assert any("company_services" in problem for problem in problems)

    async def test_detecta_un_canvi_de_nom_comercial(self, db, tenant_a):
        """El cas real: un test va canviar el nom comercial del tenant demo."""
        _, company = tenant_a
        before = await snapshot(db, company.id)
        company.trade_name = "Smoke 9b235f49"
        await db.commit()

        problems = before.diff(await snapshot(db, company.id))
        assert any("nom comercial" in problem for problem in problems)

    async def test_compta_tambe_les_taules_indirectes(self, db, tenant_a):
        _, company = tenant_a
        data = await snapshot(db, company.id)
        for name in INDIRECT_TABLES:
            assert name in data.rows
