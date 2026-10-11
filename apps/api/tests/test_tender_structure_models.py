"""Estructura del plec (0048): el que la base de dades garanteix sola.

Tot via l'ORM (cap SQL cru amb UUID: a SQLite donaria fals verd). Dades
sintetiques: cap expedient real.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    AnalysisRunStatus,
    RequirementCategory,
    RequirementOrigin,
    RequirementTiming,
    SourceType,
    TenderSiteRole,
)
from app.models.requirements import AwardCriterion, Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tender_structure import (
    AwardCriterionBlock,
    RequirementBlock,
    TenderBlock,
    TenderSite,
)
from app.models.tenders import Tender
from tests.structure_support import PROV

pytestmark = pytest.mark.asyncio


async def _source(db) -> Source:
    source = Source(
        code=f"test-{uuid.uuid4().hex[:8]}", name="Font de prova", source_type=SourceType.API
    )
    db.add(source)
    await db.flush()
    return source


async def _tender(db, **fields) -> Tender:
    source = await _source(db)
    tender = Tender(
        source_id=source.id, external_id=uuid.uuid4().hex, title="Expedient sintetic", **fields
    )
    db.add(tender)
    await db.flush()
    return tender


async def _rejects(db, row) -> None:
    """La base refusa la fila. En un savepoint, perque la resta de la sessio
    continui carregada."""
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(row)
            await db.flush()


# --------------------------------------------------------------------------- #
# tenders: quatre columnes tri-estat                                           #
# --------------------------------------------------------------------------- #


async def test_the_new_tender_columns_are_null_by_default_and_tristate(db):
    tender = await _tender(db)
    await db.commit()
    await db.refresh(tender)
    assert (
        tender.duration_months,
        tender.extension_months,
        tender.regulated_harmonised,
        tender.planned_start_date,
    ) == (None, None, None, None)

    # SARA es tri-estat: `False` es una afirmacio, no el valor per defecte.
    for value in (True, False, None):
        tender.regulated_harmonised = value
        await db.flush()
        assert (
            await db.scalar(sa.select(Tender.regulated_harmonised).where(Tender.id == tender.id))
            is value
        )

    tender.duration_months, tender.extension_months = 48, 0
    tender.planned_start_date = date(2027, 1, 1)
    await db.commit()
    await db.refresh(tender)
    assert (tender.duration_months, tender.extension_months) == (48, 0)
    assert tender.planned_start_date == date(2027, 1, 1)


async def test_negative_months_are_rejected_by_the_database(db):
    source = await _source(db)
    await db.commit()
    for field in ("duration_months", "extension_months"):
        await _rejects(
            db,
            Tender(source_id=source.id, external_id=uuid.uuid4().hex, title="x", **{field: -1}),
        )


# --------------------------------------------------------------------------- #
# Blocs i seus                                                                 #
# --------------------------------------------------------------------------- #


async def test_block_code_is_unique_within_the_tender_and_money_is_decimal(db):
    tender = await _tender(db)
    other = await _tender(db)
    db.add(
        TenderBlock(
            tender_id=tender.id,
            code="B1",
            name="Bloc 1",
            base_price_ex_vat=Decimal("1234.50"),
            **PROV.create_kwargs(),
        )
    )
    # El mateix codi en un altre expedient es legitim.
    db.add(TenderBlock(tender_id=other.id, code="B1", name="Un altre B1", **PROV.create_kwargs()))
    await db.commit()
    stored = await db.scalar(sa.select(TenderBlock).where(TenderBlock.tender_id == tender.id))
    assert stored.base_price_ex_vat == Decimal("1234.50")
    assert isinstance(stored.base_price_ex_vat, Decimal)
    # Tri-estat: no hi ha cap `False` per defecte.
    assert stored.separately_awardable is None
    await _rejects(
        db, TenderBlock(tender_id=tender.id, code="B1", name="Repetit", **PROV.create_kwargs())
    )


async def test_block_money_and_points_cannot_be_negative(db):
    tender = await _tender(db)
    await db.commit()
    await _rejects(
        db,
        TenderBlock(
            tender_id=tender.id,
            code="B1",
            name="x",
            base_price_ex_vat=Decimal("-1"),
            **PROV.create_kwargs(),
        ),
    )
    await _rejects(
        db,
        TenderBlock(
            tender_id=tender.id, code="B2", name="x", price_points=-0.5, **PROV.create_kwargs()
        ),
    )


async def test_site_role_and_critical_are_independent_and_location_is_optional(db):
    tender = await _tender(db)
    critical_remote = TenderSite(
        tender_id=tender.id,
        code="S2",
        name="Seu remota critica",
        role=TenderSiteRole.REMOTE,
        is_critical=True,
        **PROV.create_kwargs(),
    )
    central = TenderSite(
        tender_id=tender.id,
        code="S1",
        name="Seu central",
        role=TenderSiteRole.CENTRAL,
        **PROV.create_kwargs(),
    )
    db.add_all([central, critical_remote])
    await db.commit()
    await db.refresh(critical_remote)
    await db.refresh(central)
    assert (critical_remote.role, critical_remote.is_critical) == (TenderSiteRole.REMOTE, True)
    # Tri-estat (0050): sense font que ho digui, `None`.
    assert (central.role, central.is_critical) == (TenderSiteRole.CENTRAL, None)
    # Ubicacio desconeguda: cap deduccio, cap geocodificacio.
    assert central.municipality_code_system is None
    assert (central.address, central.municipality_code, central.latitude, central.longitude) == (
        None,
        None,
        None,
        None,
    )
    await _rejects(
        db,
        TenderSite(
            tender_id=tender.id,
            code="S1",
            name="Repetida",
            role=TenderSiteRole.REMOTE,
            **PROV.create_kwargs(),
        ),
    )
    await _rejects(
        db,
        TenderSite(
            tender_id=tender.id,
            code="S3",
            name="x",
            role=TenderSiteRole.REMOTE,
            latitude=Decimal("91"),
            **PROV.create_kwargs(),
        ),
    )
    await _rejects(
        db,
        TenderSite(
            tender_id=tender.id,
            code="S4",
            name="x",
            role=TenderSiteRole.REMOTE,
            longitude=Decimal("-181"),
            **PROV.create_kwargs(),
        ),
    )


async def test_deleting_the_tender_takes_blocks_sites_and_links_with_it(db):
    tender = await _tender(db)
    block = TenderBlock(tender_id=tender.id, code="B1", name="Bloc", **PROV.create_kwargs())
    site = TenderSite(
        tender_id=tender.id,
        code="S1",
        name="Seu",
        role=TenderSiteRole.CENTRAL,
        **PROV.create_kwargs(),
    )
    requirement = Requirement(
        tender_id=tender.id,
        origin=RequirementOrigin.MANUAL,
        category=RequirementCategory.INSURANCE,
        title="R",
        dedup_key=uuid.uuid4().hex,
    )
    db.add_all([block, site, requirement])
    await db.flush()
    db.add(RequirementBlock(requirement_id=requirement.id, block_id=block.id))
    await db.commit()
    await db.delete(tender)
    await db.commit()
    for model in (TenderBlock, TenderSite, RequirementBlock, Requirement):
        assert await db.scalar(sa.select(sa.func.count()).select_from(model)) == 0


# --------------------------------------------------------------------------- #
# Temporalitat i blocs N:M                                                     #
# --------------------------------------------------------------------------- #


async def _run(db, tender: Tender) -> RequirementExtractionRun:
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="v",
        prompt_version="v",
        schema_version="v",
        provider="fake",
        model="fake",
        run_basis="real",  # fila sintetica vigent (0061: a PG nomes real/gold)
        is_current=True,
    )
    db.add(run)
    await db.flush()
    return run


async def test_timing_defaults_to_unknown_and_only_accepts_the_enum(db):
    tender = await _tender(db)
    run = await _run(db, tender)
    requirement = Requirement(
        tender_id=tender.id,
        run_id=run.id,
        category=RequirementCategory.INSURANCE,
        title="R",
        dedup_key=uuid.uuid4().hex,
    )
    criterion = AwardCriterion(
        run_id=run.id, tender_id=tender.id, name="C", points=6.0, dedup_key=uuid.uuid4().hex
    )
    db.add_all([requirement, criterion])
    await db.commit()
    await db.refresh(requirement)
    await db.refresh(criterion)
    assert requirement.timing == RequirementTiming.UNKNOWN
    assert criterion.timing == RequirementTiming.UNKNOWN
    # La columna te el seu CHECK: un valor fora de l'enum no entra ni per SQL cru
    # (l'ORM ja el refusaria abans; aqui es prova la base). A SQLite els UUID es
    # guarden com a hex sense guions; a PostgreSQL, com a uuid.
    sqlite = db.bind.dialect.name == "sqlite"
    statement = sa.text(
        "UPDATE requirements SET timing = 'whenever' WHERE id = "
        + (":id" if sqlite else "CAST(:id AS uuid)")
    )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                statement, {"id": requirement.id.hex if sqlite else str(requirement.id)}
            )


async def test_blocks_are_a_set_per_requirement_and_points_are_not_split(db):
    tender = await _tender(db)
    run = await _run(db, tender)
    b1 = TenderBlock(tender_id=tender.id, code="B1", name="Bloc 1", **PROV.create_kwargs())
    b2 = TenderBlock(tender_id=tender.id, code="B2", name="Bloc 2", **PROV.create_kwargs())
    requirement = Requirement(
        tender_id=tender.id,
        run_id=run.id,
        category=RequirementCategory.INSURANCE,
        title="R",
        dedup_key=uuid.uuid4().hex,
    )
    criterion = AwardCriterion(
        run_id=run.id, tender_id=tender.id, name="C2", points=6.0, dedup_key=uuid.uuid4().hex
    )
    db.add_all([b1, b2, requirement, criterion])
    await db.flush()
    db.add_all(
        [
            RequirementBlock(requirement_id=requirement.id, block_id=b1.id),
            AwardCriterionBlock(award_criterion_id=criterion.id, block_id=b1.id),
            AwardCriterionBlock(award_criterion_id=criterion.id, block_id=b2.id),
        ]
    )
    await db.commit()
    await db.refresh(requirement)
    await db.refresh(criterion)
    assert requirement.block_ids == [b1.id]
    assert set(criterion.block_ids) == {b1.id, b2.id}
    # Sis punts sobre dos blocs continuen sent sis punts.
    assert criterion.points == 6.0
    # La clau primaria composta: el mateix enllac no es repeteix (insercio directa,
    # sense passar per la sessio, que ja en coneix la identitat).
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                sa.insert(RequirementBlock).values(requirement_id=requirement.id, block_id=b1.id)
            )
    # Esborrar un bloc s'emporta els seus enllacos, no el requisit ni el criteri.
    await db.delete(await db.get(TenderBlock, b1.id))
    await db.commit()
    await db.refresh(requirement)
    await db.refresh(criterion)
    assert requirement.block_ids == []
    assert criterion.block_ids == [b2.id]
    assert await db.get(Requirement, requirement.id) is not None
