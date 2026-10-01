"""Abast i semantica del plec (0049): les regles del model i la via interna.

Via interna = `app.services.tender_scope` sense cap politica d'acces: aqui
s'exercita sobre una licitacio PUBLICA (cap empresa la podria editar pel router
de tenant), que es exactament el que haura de fer la carrega d'un gold. Tot via
l'ORM o el servei; els CHECK es proven amb SQL cru nomes on l'ORM ja refusaria.

Dades sintetiques.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.knowledge import vocabulary
from app.models.enums import (
    AnalysisRunStatus,
    AreaKind,
    ContractFactCode,
    NeedComparator,
    NeedType,
    RequirementCategory,
    RequirementOrigin,
    SourceType,
    TenderSiteRole,
    TerritoryMatchMode,
)
from app.models.requirements import AwardCriterion, Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tender_scope import (
    ContractFactBlock,
    RequirementAwardLink,
    RequirementCapabilityNeed,
    RequirementTerritory,
    TenderContractFact,
    TenderScopeItem,
)
from app.models.tender_structure import TenderBlock, TenderSite
from app.models.tenders import Tender, TenderLot
from app.services import tender_scope as scope

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _public_tender(db, title="Expedient public sintetic") -> Tender:
    source = Source(
        code=f"feed-{uuid.uuid4().hex[:8]}", name="Font sindicada", source_type=SourceType.API
    )
    db.add(source)
    await db.flush()
    tender = Tender(source_id=source.id, external_id=uuid.uuid4().hex, title=title)
    db.add(tender)
    await db.flush()
    return tender


async def _block(db, tender: Tender, code: str) -> TenderBlock:
    block = TenderBlock(tender_id=tender.id, code=code, name=f"Bloc {code}")
    db.add(block)
    await db.flush()
    return block


async def _site(db, tender: Tender, code: str, role=TenderSiteRole.REMOTE) -> TenderSite:
    site = TenderSite(tender_id=tender.id, code=code, name=f"Seu {code}", role=role)
    db.add(site)
    await db.flush()
    return site


async def _requirement(db, tender: Tender, title="R") -> Requirement:
    requirement = Requirement(
        tender_id=tender.id,
        origin=RequirementOrigin.MANUAL,
        category=RequirementCategory.INSURANCE,
        title=title,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.flush()
    return requirement


async def _criterion(db, tender: Tender, name="C", points=6.0) -> AwardCriterion:
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="v",
        prompt_version="v",
        schema_version="v",
        provider="fake",
        model="fake",
        is_current=False,
    )
    db.add(run)
    await db.flush()
    criterion = AwardCriterion(
        run_id=run.id, tender_id=tender.id, name=name, points=points, dedup_key=uuid.uuid4().hex
    )
    db.add(criterion)
    await db.flush()
    return criterion


async def _rejected_by_db(db, statement) -> None:
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(statement)


# --------------------------------------------------------------------------- #
# Elements d'abast                                                              #
# --------------------------------------------------------------------------- #


async def test_scope_items_follow_the_model_rules_and_cross_tender_protection(db):
    tender = await _public_tender(db)
    other = await _public_tender(db, title="Un altre")
    b1 = await _block(db, tender, "B1")
    foreign_block = await _block(db, other, "B1")
    lot = TenderLot(tender_id=tender.id, external_lot_id="L1", lot_number=1)
    foreign_lot = TenderLot(tender_id=other.id, external_lot_id="L1", lot_number=1)
    site = await _site(db, tender, "S1")
    foreign_site = await _site(db, other, "S1")
    db.add_all([lot, foreign_lot])
    await db.flush()

    # Via interna sobre una licitacio publica: persisteix, sense cap politica.
    item = await scope.create_scope_item(
        db,
        tender,
        {
            "service_line_code": "access_ftth",
            "block_id": b1.id,
            "quantity": Decimal("15"),
            "unit": "accessos",
        },
    )
    await db.flush()
    await db.refresh(item)
    assert (item.tender_id, item.block_id, item.quantity) == (tender.id, b1.id, Decimal("15"))
    assert isinstance(item.quantity, Decimal)
    metres = await scope.create_scope_item(
        db,
        tender,
        {
            "service_line_code": "fiber_deployment",
            "quantity": Decimal("4206.500"),
            "unit": "m",
            "unit_base_price_ex_vat": Decimal("12.50"),
        },
    )
    assert (metres.block_id, metres.tender_lot_id) == (None, None)  # tot el contracte

    # O bloc o lot, mai tots dos (servei i base).
    with pytest.raises(scope.ScopeBlockXorLotError):
        await scope.create_scope_item(
            db,
            tender,
            {"service_line_code": "voice_pbx", "block_id": b1.id, "tender_lot_id": lot.id},
        )
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(
                TenderScopeItem(
                    tender_id=tender.id,
                    service_line_code="voice_pbx",
                    block_id=b1.id,
                    tender_lot_id=lot.id,
                )
            )
            await db.flush()
    # Nomes del mateix expedient.
    for fields, error in (
        ({"block_id": foreign_block.id}, scope.BlockTenderMismatchError),
        ({"tender_lot_id": foreign_lot.id}, scope.LotTenderMismatchError),
        ({"site_id": foreign_site.id}, scope.SiteTenderMismatchError),
    ):
        with pytest.raises(error):
            await scope.create_scope_item(db, tender, {"service_line_code": "voice_pbx", **fields})
    assert await scope.update_scope_item(db, tender, item, {"site_id": site.id}) is True
    with pytest.raises(scope.SiteTenderMismatchError):
        await scope.update_scope_item(db, tender, item, {"site_id": foreign_site.id})
    # Especificacions sense versio, quantitats i preus negatius: la base ho refusa.
    with pytest.raises(scope.ScopeSpecsUnversionedError):
        await scope.create_scope_item(
            db, tender, {"service_line_code": "voice_pbx", "specs_json": {"ports": 24}}
        )
    for values in (
        {"quantity": -1},
        {"unit_base_price_ex_vat": -1},
        {"specs_json": {"a": 1}, "specs_schema": None},
    ):
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    TenderScopeItem(tender_id=tender.id, service_line_code="voice_pbx", **values)
                )
                await db.flush()
    # Una linia fora del vocabulari no entra (clau forana).
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(TenderScopeItem(tender_id=tender.id, service_line_code="teleport"))
            await db.flush()
    await db.rollback()


async def test_deleting_a_block_takes_its_scope_items_and_a_site_only_detaches(db):
    tender = await _public_tender(db)
    b1 = await _block(db, tender, "B1")
    site = await _site(db, tender, "S1")
    owned = await scope.create_scope_item(
        db, tender, {"service_line_code": "access_ftth", "block_id": b1.id, "site_id": site.id}
    )
    wide = await scope.create_scope_item(
        db, tender, {"service_line_code": "voice_pbx", "site_id": site.id}
    )
    await db.flush()
    await db.delete(await db.get(TenderSite, site.id))
    await db.flush()
    await db.refresh(wide)
    assert wide.site_id is None
    await db.delete(await db.get(TenderBlock, b1.id))
    await db.flush()
    remaining = set(await db.scalars(sa.select(TenderScopeItem.id)))
    assert owned.id not in remaining and wide.id in remaining


# --------------------------------------------------------------------------- #
# Territoris i proves                                                           #
# --------------------------------------------------------------------------- #


async def test_territories_are_stored_as_a_set_with_mode_and_same_tender_sites(db):
    tender = await _public_tender(db)
    other = await _public_tender(db, title="Un altre")
    sites = [await _site(db, tender, f"S{n}") for n in range(1, 4)]
    foreign_site = await _site(db, other, "S9")
    requirement = await _requirement(db, tender)
    await db.flush()
    await db.refresh(requirement)
    assert requirement.territory_match_mode == TerritoryMatchMode.ANY
    assert requirement.territories == []

    d = {
        "kind": AreaKind.MUNICIPALITY,
        "label": "Municipi D",
        "code": "17999",
        "code_system": "INE",
        "qualifier": "trama urbana",
    }
    assert await scope.set_territories(db, tender, requirement, [d, d]) is True
    assert await scope.set_territories(db, tender, requirement, [d]) is False  # mateix contingut
    assert scope.set_territory_match_mode(requirement, TerritoryMatchMode.ALL) is True
    by_sites = [{"kind": AreaKind.ADDRESS, "site_id": s.id} for s in sites]
    assert await scope.set_territories(db, tender, requirement, by_sites) is True
    await db.flush()
    await db.refresh(requirement)
    assert sorted(t.site_id for t in requirement.territories) == sorted(s.id for s in sites)
    assert requirement.territory_match_mode == TerritoryMatchMode.ALL
    with pytest.raises(scope.SiteTenderMismatchError):
        await scope.set_territories(
            db, tender, requirement, [{"kind": AreaKind.ADDRESS, "site_id": foreign_site.id}]
        )
    # Un territori ha d'apuntar a alguna cosa; i el mode nomes accepta l'enum.
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            db.add(RequirementTerritory(requirement_id=requirement.id, kind=AreaKind.CUSTOM))
            await db.flush()
    sqlite = db.bind.dialect.name == "sqlite"
    await _rejected_by_db(
        db,
        sa.text(
            "UPDATE requirements SET territory_match_mode = 'xyz' WHERE id = "
            + (":id" if sqlite else "CAST(:id AS uuid)")
        ).bindparams(id=requirement.id.hex if sqlite else str(requirement.id)),
    )
    # Un territori ancorat a una seu es l'adreca d'aquella seu: se'n va amb ella.
    await db.delete(await db.get(TenderSite, sites[0].id))
    await db.flush()
    await db.refresh(requirement, attribute_names=["territories"])
    assert sorted(t.site_id for t in requirement.territories) == sorted(s.id for s in sites[1:])


async def test_territories_are_a_set_with_a_canonical_identity(db):
    """Dues files semanticament iguals no existeixen; l'ordre no compta; un
    qualifier o una seu diferents si que fan un territori diferent."""
    tender = await _public_tender(db)
    s1 = await _site(db, tender, "S1")
    s2 = await _site(db, tender, "S2")
    requirement = await _requirement(db, tender)
    await db.flush()
    escala = {
        "kind": AreaKind.MUNICIPALITY,
        "label": "Municipi E",
        "code": "17062",
        "code_system": "INE",
    }
    same = {**escala, "label": "  Municipi   E ", "code_system": " INE "}
    with_qualifier = {**escala, "qualifier": "trama urbana"}
    spaced_qualifier = {**escala, "qualifier": "trama   urbana"}
    at_s1 = {"kind": AreaKind.ADDRESS, "site_id": s1.id}
    at_s2 = {"kind": AreaKind.ADDRESS, "site_id": s2.id}

    assert scope.territory_identity(escala) == scope.territory_identity(same)
    assert scope.territory_identity(with_qualifier) == scope.territory_identity(spaced_qualifier)
    assert scope.territory_identity(escala) != scope.territory_identity(with_qualifier)
    assert scope.territory_identity(at_s1) != scope.territory_identity(at_s2)
    # Dos noms diferents son dos territoris: cap deduccio geografica.
    assert scope.territory_identity(escala) != scope.territory_identity(
        {**escala, "label": "L'Escala"}
    )

    assert await scope.set_territories(db, tender, requirement, [escala, same, escala]) is True
    await db.refresh(requirement, attribute_names=["territories"])
    assert [(t.label, t.code_system) for t in requirement.territories] == [("Municipi E", "INE")]
    # Ordre diferent, mateix conjunt: cap canvi.
    assert (
        await scope.set_territories(db, tender, requirement, [with_qualifier, escala, at_s1, at_s2])
        is True
    )
    assert (
        await scope.set_territories(db, tender, requirement, [at_s2, same, at_s1, spaced_qualifier])
        is False
    )
    await db.refresh(requirement, attribute_names=["territories"])
    assert len(requirement.territories) == 4
    assert sorted(t.qualifier or "" for t in requirement.territories) == [
        "",
        "",
        "",
        "trama urbana",
    ]
    # Desduplicar no toca el mode.
    scope.set_territory_match_mode(requirement, TerritoryMatchMode.ALL)
    assert (
        await scope.set_territories(db, tender, requirement, [escala, escala, at_s1, at_s1]) is True
    )
    await db.refresh(requirement, attribute_names=["territories"])
    assert (
        len(requirement.territories) == 2
        and requirement.territory_match_mode == TerritoryMatchMode.ALL
    )
    # Una repeticio ja desada (per SQL cru, com una dada antiga) es neteja en la seguent escriptura.
    first = next(t for t in requirement.territories if t.label)
    db.add(
        RequirementTerritory(
            requirement_id=requirement.id,
            kind=first.kind,
            label=first.label,
            code=first.code,
            code_system=first.code_system,
        )
    )
    await db.flush()
    assert await scope.set_territories(db, tender, requirement, [escala, at_s1]) is True
    await db.refresh(requirement, attribute_names=["territories"])
    assert len(requirement.territories) == 2


async def test_contract_facts_are_unique_per_code_and_exact_block_scope(db):
    tender = await _public_tender(db)
    b1 = await _block(db, tender, "B1")
    b2 = await _block(db, tender, "B2")
    b3 = await _block(db, tender, "B3")
    await db.flush()
    create = lambda code, blocks, value=None: scope.create_fact(  # noqa: E731
        db,
        tender,
        fact_code=code,
        value_json=value or {"v": 1},
        value_schema="v1",
        block_ids=blocks,
    )
    p13 = await create(ContractFactCode.PENALTIES, [b1.id, b3.id])
    await create(ContractFactCode.PENALTIES, [b2.id])  # mateix codi, altre abast
    wide = await create(ContractFactCode.LIABILITY_INSURANCE, [])
    await create(ContractFactCode.SITE_VISIT, [b1.id, b3.id])  # altre codi, mateix abast
    billing = await create(ContractFactCode.BILLING_TERMS, [])
    await db.flush()

    with pytest.raises(scope.ContractFactScopeConflictError):
        await create(
            ContractFactCode.PENALTIES, [b3.id, b1.id]
        )  # el mateix conjunt, en un altre ordre
    with pytest.raises(scope.ContractFactScopeConflictError):
        await create(ContractFactCode.LIABILITY_INSURANCE, [])  # dos de tot el contracte
    with pytest.raises(scope.ContractFactScopeConflictError):
        await create(ContractFactCode.BILLING_TERMS, [])  # com a maxim un `billing_terms`
    with pytest.raises(scope.BillingTermsBlockLinkError):
        await create(ContractFactCode.BILLING_TERMS, [b1.id])

    # Un UPDATE que col·lisiona es refusa sencer: ni valor ni blocs canvien.
    with pytest.raises(scope.ContractFactScopeConflictError):
        await scope.update_fact(db, tender, p13, value_json={"v": 2}, block_ids=[b2.id])
    await db.refresh(p13)
    assert p13.value_json == {"v": 1} and set(await scope._fact_block_ids(db, p13)) == {
        b1.id,
        b3.id,
    }
    with pytest.raises(scope.ContractFactScopeConflictError):
        await scope.update_fact(db, tender, wide, fact_code=ContractFactCode.BILLING_TERMS)
    await db.refresh(wide)
    assert wide.fact_code == ContractFactCode.LIABILITY_INSURANCE
    # Canviar nomes el valor o moure's a un abast lliure si que es pot.
    assert await scope.update_fact(db, tender, p13, value_json={"v": 3}) is True
    assert await scope.update_fact(db, tender, p13, block_ids=[b1.id]) is True
    assert await scope.update_fact(db, tender, billing, value_json={"period": "monthly"}) is True
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderContractFact)) == 5


async def test_proof_is_versioned_and_null_differs_from_empty(db):
    tender = await _public_tender(db)
    requirement = await _requirement(db, tender)
    await db.flush()
    assert (requirement.proof_json, requirement.proof_schema) == (None, None)
    assert scope.set_proof(requirement, []) is True
    assert (requirement.proof_json, requirement.proof_schema) == ([], "proof_v1")
    assert scope.set_proof(requirement, []) is False
    items = [{"kind": "annual_accounts", "text": "Comptes anuals dels tres darrers exercicis"}]
    assert scope.set_proof(requirement, items) is True
    assert requirement.proof_json == [
        {
            "kind": "annual_accounts",
            "text": "Comptes anuals dels tres darrers exercicis",
            "mandatory": True,
        }
    ]
    with pytest.raises(ValueError):
        scope.set_proof(requirement, [{"kind": "blood_sample", "text": "x"}])
    assert scope.set_proof(requirement, None) is True
    assert (requirement.proof_json, requirement.proof_schema) == (None, None)
    await db.flush()
    # La base no deixa una prova sense versio (ni una versio sense prova).
    sqlite = db.bind.dialect.name == "sqlite"
    await _rejected_by_db(
        db,
        sa.text(
            "UPDATE requirements SET proof_json = '[]' WHERE id = "
            + (":id" if sqlite else "CAST(:id AS uuid)")
        ).bindparams(id=requirement.id.hex if sqlite else str(requirement.id)),
    )


# --------------------------------------------------------------------------- #
# Necessitats tecniques i requisit ↔ criteri                                    #
# --------------------------------------------------------------------------- #


async def test_capability_needs_are_mapped_xor_unmapped_with_a_closed_comparator(db):
    tender = await _public_tender(db)
    other = await _public_tender(db, title="Un altre")
    requirement = await _requirement(db, tender)
    criterion = await _criterion(db, tender, "Millores", 6.0)
    foreign = await _criterion(db, other, "Aliè", 3.0)
    await db.flush()

    minimum = await scope.create_need(
        db,
        tender,
        requirement,
        {
            "capability_code": "pbx.ivr_multilevel",
            "comparator": NeedComparator.PRESENT,
            "need_type": NeedType.MINIMUM,
        },
    )
    scored = await scope.create_need(
        db,
        tender,
        requirement,
        {
            "capability_code": "pbx.concurrent_calls",
            "comparator": NeedComparator.GTE,
            "value_json": {"number": 50},
            "value_schema": "need_value_v1",
            "need_type": NeedType.SCORED_IMPROVEMENT,
            "points": 2.0,
            "award_criterion_id": criterion.id,
        },
    )
    demo = await scope.create_need(
        db,
        tender,
        requirement,
        {
            "unmapped_capability": "queue_callback_keep_position",
            "comparator": NeedComparator.PRESENT,
            "need_type": NeedType.DEMO,
            "points": 2.0,
        },
    )
    await db.flush()
    assert (minimum.tender_id, scored.award_criterion_id, demo.capability_code) == (
        tender.id,
        criterion.id,
        None,
    )
    # El criteri no canvia de punts: els de la necessitat no se sumen enlloc.
    assert (await db.get(AwardCriterion, criterion.id)).points == 6.0
    # Cap capacitat global nova: el vocabulari es el que era.
    assert "queue_callback_keep_position" not in vocabulary.CAPABILITIES_BY_CODE

    base = {"comparator": NeedComparator.PRESENT, "need_type": NeedType.MINIMUM}
    for fields in (
        {},  # ni mapada ni sense mapar
        {"capability_code": "pbx.queues", "unmapped_capability": "cues"},  # totes dues
        {
            "capability_code": "pbx.queues",
            "value_json": {"x": 1},
            "value_schema": "v",
        },  # present amb valor
        {
            "capability_code": "pbx.concurrent_calls",
            "comparator": NeedComparator.GTE,
        },  # gte sense valor
        {
            "capability_code": "pbx.concurrent_calls",
            "comparator": NeedComparator.GTE,
            "value_json": {"n": 1},
        },
        {"capability_code": "pbx.queues", "points": -1},
    ):
        with pytest.raises(ValidationError):
            await scope.create_need(db, tender, requirement, {**base, **fields})
    with pytest.raises(scope.CriterionTenderMismatchError):
        await scope.create_need(
            db,
            tender,
            requirement,
            {**base, "capability_code": "pbx.queues", "award_criterion_id": foreign.id},
        )
    # La base tambe ho garanteix: XOR, punts i capacitat del vocabulari.
    for values in (
        {"comparator": NeedComparator.PRESENT, "need_type": NeedType.MINIMUM},
        {
            "capability_code": "pbx.queues",
            "unmapped_capability": "x",
            "comparator": NeedComparator.PRESENT,
            "need_type": NeedType.MINIMUM,
        },
        {
            "capability_code": "pbx.queues",
            "comparator": NeedComparator.PRESENT,
            "need_type": NeedType.MINIMUM,
            "points": -2,
        },
        {
            "capability_code": "pbx.teleport",
            "comparator": NeedComparator.PRESENT,
            "need_type": NeedType.MINIMUM,
        },
    ):
        with pytest.raises(IntegrityError):
            async with db.begin_nested():
                db.add(
                    RequirementCapabilityNeed(
                        requirement_id=requirement.id, tender_id=tender.id, **values
                    )
                )
                await db.flush()
    await db.rollback()


async def test_requirement_award_links_are_a_same_tender_set_without_copying_anything(db):
    tender = await _public_tender(db)
    other = await _public_tender(db, title="Un altre")
    requirement = await _requirement(db, tender)
    c1 = await _criterion(db, tender, "C1", 10.0)
    c2 = await _criterion(db, tender, "C2", 6.0)
    foreign = await _criterion(db, other, "Aliè", 3.0)
    await db.flush()
    assert await scope.set_award_links(db, tender, requirement, [c1.id, c2.id, c1.id]) is True
    assert await scope.set_award_links(db, tender, requirement, [c2.id, c1.id]) is False
    await db.flush()
    await db.refresh(requirement)
    await db.refresh(c1)
    assert set(requirement.award_criterion_ids) == {c1.id, c2.id}
    assert c1.requirement_ids == [requirement.id]
    assert (c1.points, c2.points) == (10.0, 6.0)
    with pytest.raises(scope.CriterionTenderMismatchError):
        await scope.set_award_links(db, tender, requirement, [foreign.id])
    assert await scope.set_award_links(db, tender, requirement, []) is True
    assert await db.scalar(sa.select(sa.func.count()).select_from(RequirementAwardLink)) == 0
    # Clau primaria composta.
    db.add(RequirementAwardLink(requirement_id=requirement.id, award_criterion_id=c1.id))
    await db.flush()
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                sa.insert(RequirementAwardLink).values(
                    requirement_id=requirement.id, award_criterion_id=c1.id
                )
            )


# --------------------------------------------------------------------------- #
# Fets contractuals                                                             #
# --------------------------------------------------------------------------- #


async def test_contract_facts_apply_to_blocks_except_billing_terms(db):
    tender = await _public_tender(db)
    other = await _public_tender(db, title="Un altre")
    b1 = await _block(db, tender, "B1")
    b3 = await _block(db, tender, "B3")
    foreign = await _block(db, other, "B1")
    await db.flush()

    insurance = await scope.create_fact(
        db,
        tender,
        fact_code=ContractFactCode.LIABILITY_INSURANCE,
        value_json={"amount_eur": 300000},
        value_schema="liability_insurance_v1",
        block_ids=[b1.id, b3.id, b1.id],
    )
    billing = await scope.create_fact(
        db,
        tender,
        fact_code=ContractFactCode.BILLING_TERMS,
        value_json={"period": "monthly"},
        value_schema="billing_terms_v1",
        block_ids=[],
    )
    await db.flush()
    assert set(await scope._fact_block_ids(db, insurance)) == {b1.id, b3.id}
    assert await scope._fact_block_ids(db, billing) == []

    # Invariant de servei: la facturacio general no s'aplica a cap bloc.
    with pytest.raises(scope.BillingTermsBlockLinkError):
        await scope.create_fact(
            db,
            tender,
            fact_code=ContractFactCode.BILLING_TERMS,
            value_json={"period": "quarterly"},
            value_schema="billing_terms_v1",
            block_ids=[b1.id],
        )
    with pytest.raises(scope.BillingTermsBlockLinkError):
        await scope.update_fact(db, tender, billing, block_ids=[b1.id])
    assert await db.scalar(sa.select(sa.func.count()).select_from(ContractFactBlock)) == 2
    with pytest.raises(scope.BlockTenderMismatchError):
        await scope.update_fact(db, tender, insurance, block_ids=[foreign.id])

    # Substituir el conjunt, i tornar a tot el contracte.
    assert await scope.update_fact(db, tender, insurance, block_ids=[b3.id]) is True
    assert await scope._fact_block_ids(db, insurance) == [b3.id]
    assert await scope.update_fact(db, tender, insurance, block_ids=[b3.id]) is False
    assert await scope.update_fact(db, tender, insurance, block_ids=[]) is True
    assert await scope._fact_block_ids(db, insurance) == []
    # Un codi fora de la llista no entra ni per SQL.
    sqlite = db.bind.dialect.name == "sqlite"
    await _rejected_by_db(
        db,
        sa.text(
            "UPDATE tender_contract_facts SET fact_code = 'moon_phase' WHERE id = "
            + (":id" if sqlite else "CAST(:id AS uuid)")
        ).bindparams(id=insurance.id.hex if sqlite else str(insurance.id)),
    )
    # Esborrar un bloc s'emporta l'enllac del fet, no el fet.
    await scope.update_fact(db, tender, insurance, block_ids=[b1.id])
    await db.flush()
    await db.delete(await db.get(TenderBlock, b1.id))
    await db.flush()
    assert await db.get(TenderContractFact, insurance.id) is not None
    assert await scope._fact_block_ids(db, insurance) == []
