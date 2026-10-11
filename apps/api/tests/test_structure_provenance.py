"""Provenance de l'estructura del plec (0060, 3B-2 §4.1).

El que es protegeix:

* la migracio 0060: paritat amb els models, SQL offline (PostgreSQL i SQLite),
  backfill `owner_manual` / `gold` (font `gold:*`) abans del NOT NULL i dels
  CHECK, taula de propostes immutable (trigger) i refus del downgrade, amb un
  round-trip real a PostgreSQL;
* cada via d'escriptura deixa la seva provenance: el propietari pel router de
  tenant (`owner_manual`/`tender_owner`/persona), el carregador del gold
  (`gold`/`producer`/run del gold) i l'operador per la via interna
  (`operator`/`platform_operator`/persona); una correccio torna a segellar el
  valor vigent sense tocar qui el va crear;
* una cita enllacada per la via interna porta persona o passada, mai cap;
* cap columna de provenance entra a l'empremta: el gold de l'Escala dona la
  mateixa empremta d'estructura que abans de 0060.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.evaluation import tender_gold
from app.knowledge import vocabulary
from app.models.enums import StructureAuthority, StructureOrigin
from app.models.provenance import RequirementSourceRefLink
from app.models.structure_proposals import PROVENANCE_TABLES
from app.models.tender_scope import (
    RequirementAwardLink,
    RequirementCapabilityNeed,
    RequirementTerritory,
    TenderContractFact,
    TenderScopeItem,
)
from app.models.tender_structure import (
    AwardCriterionSourceRefLink,
    TenderBlock,
    TenderBlockSourceRefLink,
    TenderSite,
    TenderSiteSourceRefLink,
)
from app.models.tenders import Tender
from app.services import manual_requirement
from app.services import tender_analysis as analysis
from app.services import tender_structure as structure
from app.services.structure_provenance import Provenance, ProvenanceError
from tests.test_tender_analysis_gold_lescala import R11A_STRUCTURE_FINGERPRINT
from tests.test_tender_gold_lescala import CASE

API_ROOT = Path(__file__).resolve().parents[1]
OFFLINE_URL = "postgresql+asyncpg://user:pass@localhost/db"
_UP = "0059_global_classification:0060_structure_provenance"
_DOWN = "0060_structure_provenance:0059_global_classification"
_LINKS = (
    "tender_block_source_ref_links",
    "tender_site_source_ref_links",
    "award_criterion_source_ref_links",
    "tender_scope_item_source_ref_links",
    "requirement_capability_need_source_ref_links",
    "tender_contract_fact_source_ref_links",
    "requirement_source_ref_links",
)
_ENTITY_MODELS = (
    TenderBlock,
    TenderSite,
    TenderScopeItem,
    RequirementCapabilityNeed,
    TenderContractFact,
    RequirementTerritory,
    RequirementAwardLink,
)


def _offline(*args: str, url: str = OFFLINE_URL) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args, "--sql"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": url},
    )
    assert result.returncode == 0, f"alembic ha fallat:\n{result.stderr[-2000:]}"
    return result.stdout


def _sqlite(*args: str) -> str:
    return _offline(*args, url="sqlite+aiosqlite:///:memory:")


# --------------------------------------------------------------------------- #
# Migracio 0060 (SQL offline i model)                                           #
# --------------------------------------------------------------------------- #


def test_0060_crea_la_taula_global_de_propostes_amb_tots_els_check():
    sql = _offline("upgrade", _UP)
    body = re.search(r"CREATE TABLE tender_structure_proposals \((.*?)\n\);", sql, re.S).group(1)
    assert "company_id" not in body
    for name in (
        "producer_valid",
        "entity_kind_valid",
        "status_valid",
        "natural_key_not_empty",
        "source_refs_not_empty",
        "promoted_has_date",
        "rejected_has_date",
        "rejected_has_reason",
    ):
        assert f"ck_tender_structure_proposals_{name}" in body, name
    assert "jsonb_array_length(source_ref_ids) > 0" in body
    assert "producer IN ('operator', 'extraction')" in body
    # `company_proposal` reservat: ni l'enum ni el CHECK l'accepten.
    assert "company_proposal" not in body
    assert "CREATE TRIGGER tender_structure_proposals_immutable" in sql
    assert "BEFORE UPDATE OR DELETE ON tender_structure_proposals" in sql
    # Els actors: qui la va crear mai; qui la va decidir, un cop decidida.
    for column in ("created_by", "promoted_by", "rejected_by"):
        assert (
            f"(NEW.{column} IS NOT NULL AND NEW.{column} IS DISTINCT FROM OLD.{column})" in sql
        ), column


def test_0060_el_trigger_del_model_i_el_de_la_migracio_son_identics():
    """`create_all` (tests, bases noves) i la migracio han d'instal·lar el mateix."""
    import importlib.util

    from app.models import structure_proposals as model

    path = API_ROOT / "alembic" / "versions" / "0060_structure_provenance.py"
    spec = importlib.util.spec_from_file_location("mig_0060_ddl", path)
    m60 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m60)
    assert m60._IMMUTABLE_FUNCTION == model.PROPOSALS_IMMUTABLE_FUNCTION
    assert m60._IMMUTABLE_TRIGGER == model.PROPOSALS_IMMUTABLE_TRIGGER


def test_0060_backfill_abans_del_not_null_i_dels_check_a_cada_taula():
    sql = _offline("upgrade", _UP)
    for table in PROVENANCE_TABLES:
        add = sql.index(f"ALTER TABLE {table} ADD COLUMN origin VARCHAR(12)")
        owner = sql.index(f"UPDATE {table} SET origin = 'owner_manual'")
        gold = sql.index(f"UPDATE {table} SET origin = 'gold' WHERE")
        not_null = sql.index(f"ALTER TABLE {table} ALTER COLUMN origin SET NOT NULL")
        check = sql.index(f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_origin_valid")
        assert add < owner < gold < not_null < check, table
        assert f"ck_{table}_authority_valid" in sql, table
        for column in ("authority", "producer_run_id", "proposal_id", "created_by", "updated_by"):
            assert f"ALTER TABLE {table} ADD COLUMN {column}" in sql, (table, column)
        assert f"fk_{table}_proposal_id" in sql and "ON DELETE SET NULL" in sql
    assert sql.count("sources.code LIKE 'gold:%'") == len(PROVENANCE_TABLES)
    # Cap autor ni autoritat inventats.
    assert "SET authority" not in sql and "SET created_by" not in sql
    for table in _LINKS:
        assert f"ALTER TABLE {table} ADD COLUMN producer_run_id UUID" in sql, table


def test_0060_downgrade_refusa_abans_de_tocar_res_i_treu_el_trigger_abans_de_la_taula():
    sql = _offline("downgrade", _DOWN)
    guard = sql.index("RAISE EXCEPTION")
    assert "0060" in sql[guard : guard + 300]
    assert "status = 'promoted'" in sql[:guard]
    assert "origin IN ('operator', 'promoted')" in sql[:guard]
    trigger = sql.index("DROP TRIGGER IF EXISTS tender_structure_proposals_immutable")
    function = sql.index("DROP FUNCTION IF EXISTS tender_structure_proposals_immutable()")
    table = sql.index("DROP TABLE tender_structure_proposals")
    assert guard < trigger < function < table
    for name in PROVENANCE_TABLES:
        # Les columnes (i la FK cap a la proposta) abans de la taula de propostes.
        assert sql.index(f"ALTER TABLE {name} DROP COLUMN proposal_id") < table, name
        assert f"ALTER TABLE {name} DROP COLUMN origin" in sql


def test_0060_a_sqlite_crea_la_taula_sense_trigger_ni_raise():
    up = _sqlite("upgrade", _UP).replace("alembic_version", "")
    down = _sqlite("downgrade", _DOWN).replace("alembic_version", "")
    assert "RAISE" not in up and "RAISE" not in down
    assert "TRIGGER" not in up and "TRIGGER" not in down
    assert "CREATE TABLE tender_structure_proposals" in up
    assert "json_array_length(source_ref_ids) > 0" in up
    for table in PROVENANCE_TABLES:
        assert f"ALTER TABLE {table} ADD COLUMN origin" in up, table
        assert f"UPDATE {table} SET origin = 'owner_manual'" in up, table
    assert "DROP TABLE tender_structure_proposals" in down


def test_0060_al_model():
    from app.models.structure_proposals import TenderStructureProposal
    from app.tenant_ops import PUBLIC_TABLES

    assert "tender_structure_proposals" in PUBLIC_TABLES
    assert "company_id" not in TenderStructureProposal.__table__.c
    assert {model.__tablename__ for model in _ENTITY_MODELS} == set(PROVENANCE_TABLES)
    for model in _ENTITY_MODELS:
        columns = model.__table__.c
        assert columns.origin.nullable is False, model
        assert columns.origin.server_default is None and columns.origin.default is None, model
        for name in ("authority", "producer_run_id", "proposal_id", "created_by", "updated_by"):
            assert columns[name].nullable is True, (model, name)
        (fk,) = columns.proposal_id.foreign_keys
        assert fk.ondelete == "SET NULL"
    for model in (TenderBlockSourceRefLink, TenderSiteSourceRefLink, AwardCriterionSourceRefLink):
        assert model.__table__.c.producer_run_id.nullable is True
    assert RequirementSourceRefLink.__table__.c.producer_run_id.nullable is True


# --------------------------------------------------------------------------- #
# Provenance de cada via                                                        #
# --------------------------------------------------------------------------- #


async def test_la_provenance_es_explicita_i_coherent():
    actor, run = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ProvenanceError):
        Provenance(StructureOrigin.OPERATOR, StructureAuthority.PLATFORM_OPERATOR)
    with pytest.raises(ProvenanceError):
        Provenance(StructureOrigin.OPERATOR, StructureAuthority.TENDER_OWNER, actor)
    with pytest.raises(ProvenanceError):
        Provenance(StructureOrigin.PROMOTED, StructureAuthority.PLATFORM_OPERATOR, actor)
    with pytest.raises(ProvenanceError):
        Provenance(StructureOrigin.GOLD, StructureAuthority.PRODUCER)
    with pytest.raises(ProvenanceError):
        Provenance(StructureOrigin.EXTRACTED, StructureAuthority.PRODUCER, actor)
    assert Provenance.gold(run).link_kwargs() == {"created_by": None, "producer_run_id": run}
    assert Provenance.operator(actor).link_kwargs() == {
        "created_by": actor,
        "producer_run_id": None,
    }
    # La via interna no te provenance per defecte: sense, ni s'arriba a cridar.
    with pytest.raises(TypeError):
        structure.create_block(None, None, {})  # type: ignore[call-arg]


async def test_el_propietari_escriu_owner_manual_amb_persona(client_a, tenant_a, db):
    from tests.test_tender_structure_api import BLOCKS, _block, _file_ref, _tender

    user, company = tenant_a
    tender_id = await _tender(client_a, company)
    created = await _block(client_a, company, tender_id, "B1")
    block = await db.get(TenderBlock, uuid.UUID(created["id"]))
    assert (block.origin, block.authority) == (
        StructureOrigin.OWNER_MANUAL,
        StructureAuthority.TENDER_OWNER,
    )
    assert (block.created_by, block.updated_by) == (user.id, user.id)
    assert (block.producer_run_id, block.proposal_id) == (None, None)

    ref = await _file_ref(client_a, company, tender_id)
    url = f"{BLOCKS.format(company=company.id, tender=tender_id)}/{block.id}/citations"
    assert (await client_a.post(url, json={"source_ref_id": ref})).status_code == 200
    link = await db.scalar(
        sa.select(TenderBlockSourceRefLink).where(TenderBlockSourceRefLink.block_id == block.id)
    )
    assert (link.created_by, link.producer_run_id) == (user.id, None)


async def test_una_correccio_segella_el_valor_vigent_i_conserva_qui_el_va_crear(db):
    from tests.test_platform_onboarding import create_operator
    from tests.test_tender_scope_models import _public_tender

    tender = await _public_tender(db)
    run, operator = uuid.uuid4(), (await create_operator(db)).id
    block = await structure.create_block(
        db, tender, {"code": "B1", "name": "Bloc"}, provenance=Provenance.gold(run)
    )
    assert (block.origin, block.producer_run_id, block.created_by) == (
        StructureOrigin.GOLD,
        run,
        None,
    )
    # Res no canvia: res no se segella.
    assert not await structure.update_block(
        db, tender, block, {"name": "Bloc"}, provenance=Provenance.operator(operator)
    )
    assert block.origin == StructureOrigin.GOLD
    # Un canvi de valor: el valor vigent es de l'operador.
    assert await structure.update_block(
        db, tender, block, {"separately_awardable": True}, provenance=Provenance.operator(operator)
    )
    assert (block.origin, block.authority) == (
        StructureOrigin.OPERATOR,
        StructureAuthority.PLATFORM_OPERATOR,
    )
    assert (block.updated_by, block.created_by, block.producer_run_id) == (operator, None, None)


async def test_cap_enllac_de_cita_sense_persona_ni_passada(db):
    from tests.test_tender_scope_models import _public_tender, _requirement

    tender = await _public_tender(db)
    requirement = await _requirement(db, tender)
    with pytest.raises(ValueError):
        await manual_requirement.link_source_ref(
            db, requirement=requirement, source_ref_id=uuid.uuid4()
        )


@pytest.fixture
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def test_el_gold_escriu_gold_amb_la_seva_run_i_l_empremta_no_canvia(db, seeded_vocabulary):
    """0060 no canvia «que demana el plec»: l'empremta d'estructura del gold de
    l'Escala es exactament la d'abans, i la provenance no hi entra."""
    loaded = await tender_gold.load_tender_gold(db, tender_gold.load_dataset(CASE))
    await db.commit()
    run_id = loaded.run.id
    for model in _ENTITY_MODELS:
        rows = list(await db.scalars(sa.select(model)))
        assert rows, model
        for row in rows:
            assert (row.origin, row.authority, row.producer_run_id) == (
                StructureOrigin.GOLD,
                StructureAuthority.PRODUCER,
                run_id,
            ), model
            assert (row.created_by, row.proposal_id) == (None, None)
    for link_model in (TenderBlockSourceRefLink, TenderSiteSourceRefLink, RequirementSourceRefLink):
        links = list(await db.scalars(sa.select(link_model)))
        assert links and all(
            (link.created_by, link.producer_run_id) == (None, run_id) for link in links
        ), link_model

    tender = await db.get(Tender, loaded.tender.id)
    view = (await analysis.load_structure_view(db, tender))[0]
    assert analysis.structure_fingerprint(view) == R11A_STRUCTURE_FINGERPRINT

    # Canviar nomes la provenance (un altre escriptor) no mou l'empremta.
    for block in loaded.blocks.values():
        Provenance.extracted(uuid.uuid4()).stamp_update(block)
    await db.flush()
    view = (await analysis.load_structure_view(db, tender))[0]
    assert analysis.structure_fingerprint(view) == R11A_STRUCTURE_FINGERPRINT


# --------------------------------------------------------------------------- #
# Round-trip real a PostgreSQL                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.postgres
async def test_0060_round_trip_real_a_postgres(engine):
    """0060 -> 0059 (dades d'abans) -> 0060 amb backfill, propostes immutables,
    refus del downgrade i tornada neta."""
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError, IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.enums import SourceType
    from app.models.identity import User
    from app.models.sources import Source
    from tests.test_tender_scope_models import _criterion, _requirement

    path = API_ROOT / "alembic" / "versions" / "0060_structure_provenance.py"
    spec = importlib.util.spec_from_file_location("mig_0060_structure_provenance", path)
    m60 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m60)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def has_proposals(conn) -> bool:
        return bool(
            (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM information_schema.tables WHERE "
                        "table_schema = current_schema() "
                        "AND table_name = 'tender_structure_proposals'"
                    )
                )
            ).scalar_one()
        )

    async def origins(conn, table: str) -> set[str | None]:
        return set((await conn.execute(sa.text(f"SELECT origin FROM {table}"))).scalars())

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    ids: dict[str, uuid.UUID] = {}
    async with factory() as session:
        await vocabulary.seed_vocabulary(session)
        for key, code in (("gold", f"gold:t60-{uuid.uuid4().hex[:6]}"), ("feed", "t60-feed")):
            source = Source(code=code, name=f"Font {key}", source_type=SourceType.MANUAL)
            session.add(source)
            await session.flush()
            tender = Tender(source_id=source.id, external_id=f"t60-{key}", title=f"Exp {key}")
            session.add(tender)
            await session.flush()
            requirement = await _requirement(session, tender)
            criterion = await _criterion(session, tender)
            ids[f"{key}_tender"] = tender.id
            ids[f"{key}_requirement"] = requirement.id
            ids[f"{key}_criterion"] = criterion.id
        for key in ("u1", "u2"):
            user = User(
                email=f"op60-{key}-{uuid.uuid4().hex[:6]}@tuvi-qa.com",
                hashed_password="x",
                full_name=f"Operador {key}",
                is_active=True,
            )
            session.add(user)
            await session.flush()
            ids[key] = user.id
        await session.commit()

    async with engine.begin() as conn:
        assert await has_proposals(conn)
        await conn.run_sync(run, m60.downgrade)  # -> 0059
        assert not await has_proposals(conn)

    async with engine.begin() as conn:
        for key in ("gold", "feed"):
            tender, requirement = ids[f"{key}_tender"], ids[f"{key}_requirement"]
            params = {"t": tender, "r": requirement, "c": ids[f"{key}_criterion"]}
            for statement in (
                "INSERT INTO tender_blocks (id, tender_id, code, name) "
                "VALUES (gen_random_uuid(), :t, 'B1', 'Bloc')",
                "INSERT INTO tender_sites (id, tender_id, code, role) "
                "VALUES (gen_random_uuid(), :t, 'S1', 'central')",
                "INSERT INTO tender_scope_items (id, tender_id, service_line_code) "
                "VALUES (gen_random_uuid(), :t, 'access_ftth')",
                "INSERT INTO requirement_capability_needs (id, requirement_id, tender_id, "
                "unmapped_capability, comparator, need_type) "
                "VALUES (gen_random_uuid(), :r, :t, 'Radioenllac', 'present', 'minimum')",
                "INSERT INTO tender_contract_facts (id, tender_id, fact_code, value_json, "
                "value_schema) VALUES (gen_random_uuid(), :t, 'envelopes', '{}', 'v1')",
                "INSERT INTO requirement_territories (id, requirement_id, kind, label) "
                "VALUES (gen_random_uuid(), :r, 'municipality', 'L Escala')",
                "INSERT INTO requirement_award_links (requirement_id, award_criterion_id) "
                "VALUES (:r, :c)",
            ):
                used = {k: v for k, v in params.items() if f":{k}" in statement}
                await conn.execute(sa.text(statement), used)
        await conn.run_sync(run, m60.upgrade)  # -> 0060 amb dades
        for table in PROVENANCE_TABLES:
            assert await origins(conn, table) == {"gold", "owner_manual"}, table
            gold_rows = (
                await conn.execute(
                    sa.text(
                        f"SELECT count(*) FROM {table} WHERE origin = 'gold' "
                        "AND authority IS NULL AND created_by IS NULL"
                    )
                )
            ).scalar_one()
            assert gold_rows == 1, table

    # NOT NULL i CHECK de l'origen.
    for value in ("NULL", "'invented'"):
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    sa.text(
                        "INSERT INTO tender_blocks (id, tender_id, code, name, origin) "
                        f"VALUES (gen_random_uuid(), :t, 'B9', 'x', {value})"
                    ),
                    {"t": ids["feed_tender"]},
                )

    # Una proposta sense cita no hi cap.
    insert = (
        "INSERT INTO tender_structure_proposals (id, tender_id, producer, entity_kind, "
        "natural_key, payload_json, source_ref_ids, status) VALUES "
        "(:id, :t, 'operator', 'block', 'B1', '{}', CAST(:refs AS jsonb), 'proposed')"
    )
    for refs in ("[]", "{}", '"x"'):
        with pytest.raises(IntegrityError):
            async with engine.begin() as conn:
                await conn.execute(
                    sa.text(insert), {"id": uuid.uuid4(), "t": ids["feed_tender"], "refs": refs}
                )
    proposal = uuid.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO tender_structure_proposals (id, tender_id, producer, entity_kind, "
                "natural_key, payload_json, source_ref_ids, status, created_by) VALUES "
                "(:id, :t, 'operator', 'block', 'B1', '{}', CAST(:refs AS jsonb), 'proposed', :u)"
            ),
            {
                "id": proposal,
                "t": ids["feed_tender"],
                "refs": f'["{uuid.uuid4()}"]',
                "u": ids["u1"],
            },
        )
    # Immutable: ni el contingut, ni qui la va crear, ni un DELETE directe.
    for statement in (
        "UPDATE tender_structure_proposals SET natural_key = 'B2' WHERE id = :id",
        "UPDATE tender_structure_proposals SET payload_json = '{\"a\": 1}' WHERE id = :id",
        "UPDATE tender_structure_proposals SET created_by = :u2 WHERE id = :id",
        "DELETE FROM tender_structure_proposals WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as caught:
            async with engine.begin() as conn:
                params = {"id": proposal}
                if ":u2" in statement:
                    params["u2"] = ids["u2"]
                await conn.execute(sa.text(statement), params)
        assert "immutable" in str(caught.value)
    # L'estat canvia una sola vegada.
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE tender_structure_proposals SET status = 'promoted', promoted_at = now(), "
                "promoted_by = :u WHERE id = :id"
            ),
            {"id": proposal, "u": ids["u1"]},
        )
    for statement in (
        "UPDATE tender_structure_proposals SET status = 'rejected', "
        "promoted_at = NULL, rejected_at = now(), rejected_reason = 'x' WHERE id = :id",
        # Decidida: qui la va promoure tampoc no es reescriu.
        "UPDATE tender_structure_proposals SET promoted_by = :u2 WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as caught:
            async with engine.begin() as conn:
                params = {"id": proposal}
                if ":u2" in statement:
                    params["u2"] = ids["u2"]
                await conn.execute(sa.text(statement), params)
        assert "immutable" in str(caught.value)
    # Un usuari esborrat deixa els actors a NULL (SET NULL): aixo si que passa.
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM users WHERE id = :u"), {"u": ids["u1"]})
        actors = (
            await conn.execute(
                sa.text(
                    "SELECT created_by, promoted_by FROM tender_structure_proposals WHERE id = :id"
                ),
                {"id": proposal},
            )
        ).one()
    assert tuple(actors) == (None, None)

    # Amb una proposta promoguda, el downgrade refusa i no toca res.
    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m60.downgrade)
    assert "0060" in str(caught.value)
    async with engine.begin() as conn:
        assert await has_proposals(conn)
        # La cascada de la licitacio s'emporta la proposta (el trigger la deixa passar).
        await conn.execute(
            sa.text("DELETE FROM tenders WHERE id = :id"), {"id": ids["feed_tender"]}
        )
        assert (
            await conn.execute(sa.text("SELECT count(*) FROM tender_structure_proposals"))
        ).scalar_one() == 0
        # Una fila escrita per l'operador tambe fa refusar el downgrade.
        await conn.execute(sa.text("UPDATE tender_blocks SET origin = 'operator'"))
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.run_sync(run, m60.downgrade)

    async with engine.begin() as conn:
        await conn.execute(sa.text("UPDATE tender_blocks SET origin = 'gold'"))
        await conn.run_sync(run, m60.downgrade)
        assert not await has_proposals(conn)
        functions = (
            await conn.execute(
                sa.text(
                    "SELECT count(*) FROM pg_proc "
                    "WHERE proname = 'tender_structure_proposals_immutable'"
                )
            )
        ).scalar_one()
        assert functions == 0
        await conn.run_sync(run, m60.upgrade)
        assert await has_proposals(conn)
        assert await origins(conn, "tender_blocks") == {"gold"}
