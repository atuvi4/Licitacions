"""Les anotacions d'estructura sobreviuen a una re-extraccio (3B-2 §4.3, C3).

Una run nova del mateix tender crea requisits i criteris nous. Les anotacions
(temporalitat, blocs, territoris i completesa, necessitats amb les seves cites,
enllacos amb criteris; i temporalitat, blocs i cites dels criteris) passen als de
la run nova amb el MATEIX `dedup_key`:

* es creen files noves amb la provenance de l'original; la run antiga no es toca;
* un `dedup_key` que ha desaparegut queda `orphaned` (i al log);
* un valor que la run nova ja porta guanya (`conflicts`);
* idempotent: la segona passada no crea res;
* el ganxo es `requirement_analysis._finalise_run` quan la run nova passa a vigent.
"""

from __future__ import annotations

import logging
import time
import uuid

import pytest
import sqlalchemy as sa

from app.models.enums import (
    AnalysisRunStatus,
    AreaKind,
    ExtractionConfidence,
    NeedComparator,
    NeedType,
    RequirementRunBasis,
    RequirementTiming,
    StructureOrigin,
    TerritoryCompleteness,
)
from app.models.requirements import AwardCriterion, Requirement, RequirementExtractionRun
from app.models.tender_scope import (
    RequirementAwardLink,
    RequirementCapabilityNeed,
    RequirementCapabilityNeedSourceRefLink,
    RequirementTerritory,
)
from app.models.tender_structure import AwardCriterionSourceRefLink, RequirementBlock
from app.services import requirement_analysis
from app.services import tender_scope as scope
from app.services import tender_structure as structure
from app.services.structure_annotations import carry_over_annotations
from app.services.structure_provenance import Provenance
from tests.test_platform_onboarding import create_operator
from tests.test_structure_proposals import extracted_requirement, public_tender_with_ref


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    from app.knowledge import vocabulary

    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _run(db, tender, *, current: bool, status=AnalysisRunStatus.COMPLETED):
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=status,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="v",
        prompt_version="v",
        schema_version="v",
        provider="fake",
        model="fake",
        # Una vigent ha de ser real/gold (CHECK de 0061 a PostgreSQL).
        run_basis="real" if current else "simulation",
        is_current=current,
    )
    db.add(run)
    await db.flush()
    return run


async def _criterion(db, tender, run, key="c1") -> AwardCriterion:
    criterion = AwardCriterion(
        run_id=run.id,
        tender_id=tender.id,
        name="Millora del temps de resposta",
        points=6.0,
        confidence=ExtractionConfidence.HIGH,
        dedup_key=key,
    )
    db.add(criterion)
    await db.flush()
    return criterion


async def _annotated_run(db):
    """Run 1 vigent amb requisits `a` i `b` i el criteri `c1`, tots anotats."""
    tender, ref, _ = await public_tender_with_ref(db)
    operator = await create_operator(db)
    prov = Provenance.operator(operator.id)
    run1 = await _run(db, tender, current=True)
    a = await extracted_requirement(db, tender, dedup_key="a", run=run1)
    b = await extracted_requirement(db, tender, dedup_key="b", run=run1)
    c1 = await _criterion(db, tender, run1)
    block = await structure.create_block(
        db, tender, {"code": "B1", "name": "Bloc"}, provenance=prov
    )

    structure.set_timing(a, RequirementTiming.AT_SUBMISSION)
    structure.set_timing(b, RequirementTiming.AT_EXECUTION)
    await structure.set_requirement_blocks(db, tender, a, [block.id])
    await scope.set_territory_semantics(
        db,
        tender,
        a,
        completeness=TerritoryCompleteness.COMPLETE,
        territories=[{"kind": AreaKind.MUNICIPALITY, "label": "L'Escala"}],
        provenance=prov,
    )
    await scope.set_award_links(db, tender, a, [c1.id], provenance=prov)
    need = await scope.create_need(
        db,
        tender,
        a,
        {
            "unmapped_capability": "Radioenllac",
            "comparator": NeedComparator.PRESENT,
            "need_type": NeedType.SCORED_IMPROVEMENT,
            "points": 2.0,
            "award_criterion_id": c1.id,
        },
        provenance=prov,
    )
    await scope.link_source_ref(db, entity=need, source_ref_id=ref.id, provenance=prov)
    structure.set_timing(c1, RequirementTiming.AT_SUBMISSION)
    await structure.set_criterion_blocks(db, tender, c1, [block.id])
    await structure.link_source_ref(db, entity=c1, source_ref_id=ref.id, provenance=prov)
    await db.commit()
    return tender, ref, operator, run1, a, b, c1, block


async def _snapshot_old(db, requirement: Requirement) -> dict:
    await db.refresh(
        requirement,
        attribute_names=["block_links", "territories", "capability_needs", "award_links"],
    )
    return {
        "timing": requirement.timing,
        "completeness": requirement.territory_completeness,
        "blocks": sorted(str(link.block_id) for link in requirement.block_links),
        "territories": sorted(str(t.id) for t in requirement.territories),
        "needs": sorted(str(n.id) for n in requirement.capability_needs),
        "links": sorted(str(link.award_criterion_id) for link in requirement.award_links),
    }


async def test_un_run_nou_hereta_les_anotacions_pel_dedup_key(db, caplog):
    tender, ref, operator, run1, a, b, c1, block = await _annotated_run(db)
    before = await _snapshot_old(db, a)
    run2 = await _run(db, tender, current=False)
    a2 = await extracted_requirement(db, tender, dedup_key="a", run=run2)
    c2 = await extracted_requirement(db, tender, dedup_key="c", run=run2)
    c1_new = await _criterion(db, tender, run2)
    await db.commit()

    with caplog.at_level(logging.INFO, logger="app.services.structure_annotations"):
        report = await carry_over_annotations(db, run1.id, run2.id)
    await db.commit()

    assert report.carried == ["a", "criterion:c1"]
    assert report.orphaned == ["b"]
    assert report.conflicts == []
    assert any(r.message == "structure_annotations.orphaned" for r in caplog.records)
    assert any(r.message == "structure_annotations.carried" for r in caplog.records)

    await db.refresh(
        a2, attribute_names=["block_links", "territories", "capability_needs", "award_links"]
    )
    assert a2.timing == RequirementTiming.AT_SUBMISSION
    assert a2.territory_completeness == TerritoryCompleteness.COMPLETE
    assert a2.block_ids == [block.id]
    (territory,) = a2.territories
    assert (territory.label, territory.origin, territory.created_by) == (
        "L'Escala",
        StructureOrigin.OPERATOR,
        operator.id,
    )
    assert a2.award_criterion_ids == [c1_new.id]
    (need,) = a2.capability_needs
    assert (need.unmapped_capability, need.award_criterion_id, need.origin) == (
        "Radioenllac",
        c1_new.id,
        StructureOrigin.OPERATOR,
    )
    need_link = await db.scalar(
        sa.select(RequirementCapabilityNeedSourceRefLink).where(
            RequirementCapabilityNeedSourceRefLink.need_id == need.id
        )
    )
    assert (need_link.source_ref_id, need_link.created_by) == (ref.id, operator.id)
    # Un requisit nou sense parella no hereta res.
    await db.refresh(c2, attribute_names=["territories"])
    assert (c2.timing, c2.territories) == (RequirementTiming.UNKNOWN, [])
    # El criteri tambe.
    await db.refresh(c1_new, attribute_names=["block_links"])
    assert (c1_new.timing, c1_new.block_ids) == (RequirementTiming.AT_SUBMISSION, [block.id])
    assert (
        await db.scalar(
            sa.select(AwardCriterionSourceRefLink.source_ref_id).where(
                AwardCriterionSourceRefLink.award_criterion_id == c1_new.id
            )
        )
        == ref.id
    )

    # Cap escriptura sobre la run antiga.
    assert await _snapshot_old(db, a) == before
    await db.refresh(b)
    assert b.timing == RequirementTiming.AT_EXECUTION

    # Idempotent.
    again = await carry_over_annotations(db, run1.id, run2.id)
    assert again.rows_created == 0
    for model in (RequirementTerritory, RequirementCapabilityNeed, RequirementAwardLink):
        count = await db.scalar(sa.select(sa.func.count()).select_from(model))
        assert count == 2, model  # l'original i la copia
    assert await db.scalar(sa.select(sa.func.count()).select_from(RequirementBlock)) == 2


async def test_un_valor_de_la_run_nova_guanya_i_es_registra_com_a_conflicte(db):
    tender, _ref, _operator, run1, _a, _b, _c1, _block = await _annotated_run(db)
    run2 = await _run(db, tender, current=False)
    a2 = await extracted_requirement(db, tender, dedup_key="a", run=run2)
    structure.set_timing(a2, RequirementTiming.AT_EXECUTION)
    await db.commit()
    report = await carry_over_annotations(db, run1.id, run2.id)
    assert "a:timing" in report.conflicts
    assert "criterion:c1" in report.orphaned  # la run nova no te el criteri
    # Una sola entrada per orfe (l'enllac i la necessitat en parlen tots dos).
    assert report.orphaned.count("a->criterion:c1") == 1
    assert len(report.orphaned) == len(set(report.orphaned))
    await db.refresh(a2, attribute_names=["award_links", "capability_needs"])
    assert a2.timing == RequirementTiming.AT_EXECUTION
    assert a2.award_links == []
    # La necessitat es copia; el criteri que ja no existeix no s'inventa.
    (need,) = a2.capability_needs
    assert need.award_criterion_id is None
    await db.commit()

    # Idempotent tambe amb el criteri orfe: la segona passada troba la copia.
    needs_before = await db.scalar(
        sa.select(sa.func.count()).select_from(RequirementCapabilityNeed)
    )
    again = await carry_over_annotations(db, run1.id, run2.id)
    assert again.rows_created == 0
    assert (
        await db.scalar(sa.select(sa.func.count()).select_from(RequirementCapabilityNeed))
        == needs_before
    )


async def test_una_necessitat_amb_la_mateixa_clau_a_la_run_nova_guanya(db):
    """Clau natural de necessitat (dedup|capacitat|tipus|comparador): si la run nova
    ja en te una, no es copia l'antiga encara que el valor sigui un altre."""
    tender, _ref, operator, run1, _a, _b, _c1, _block = await _annotated_run(db)
    run2 = await _run(db, tender, current=False)
    a2 = await extracted_requirement(db, tender, dedup_key="a", run=run2)
    await _criterion(db, tender, run2)
    await scope.create_need(
        db,
        tender,
        a2,
        {
            "unmapped_capability": "Radioenllac",
            "comparator": NeedComparator.PRESENT,
            "need_type": NeedType.SCORED_IMPROVEMENT,
            "points": 5.0,
        },
        provenance=Provenance.operator(operator.id),
    )
    await db.commit()
    report = await carry_over_annotations(db, run1.id, run2.id)
    await db.refresh(a2, attribute_names=["capability_needs"])
    (need,) = a2.capability_needs
    assert need.points == 5.0
    assert any(c.startswith("a:need:") for c in report.conflicts), report.conflicts


async def _closing_run(db, tender, *, provider: str, chunks_failed: int = 0):
    """Una run en curs a punt de tancar, amb l'entrada canonica actual del tender."""
    run2 = await _run(db, tender, current=False, status=AnalysisRunStatus.RUNNING)
    run2.provider = provider
    run2.run_basis = RequirementRunBasis.for_provider(provider)
    run2.input_fingerprint = (await requirement_analysis.analysis_input(db, tender.id)).fingerprint
    run2.chunks_total = 1 + chunks_failed
    run2.chunks_completed = 1
    run2.chunks_failed = chunks_failed
    a2 = await extracted_requirement(db, tender, dedup_key="a", run=run2)
    await db.commit()
    return run2, a2


async def _finalise(db, run) -> None:
    await requirement_analysis._finalise_run(
        session=db,
        run=run,
        tally=requirement_analysis._ChunkTally(requirements=1),
        conflicts=0,
        input_tokens=0,
        output_tokens=0,
        request_ids=[],
        started=time.monotonic(),
        last_error=None,
    )


async def test_el_tancament_d_una_run_nova_vigent_hi_porta_les_anotacions(db):
    tender, _ref, _operator, run1, _a, _b, _c1, _block = await _annotated_run(db)
    run2, a2 = await _closing_run(db, tender, provider="anthropic")

    await _finalise(db, run2)
    await db.refresh(run1)
    await db.refresh(a2, attribute_names=["territories"])
    assert (run2.is_current, run1.is_current) == (True, False)
    assert a2.timing == RequirementTiming.AT_SUBMISSION
    assert [t.label for t in a2.territories] == ["L'Escala"]


@pytest.mark.parametrize(
    ("provider", "chunks_failed"),
    [("fake", 0), ("anthropic", 1)],
    ids=["simulacio", "parcial"],
)
async def test_una_run_que_no_esdeve_vigent_no_s_emporta_cap_anotacio(db, provider, chunks_failed):
    """3B-2 §6.3: les anotacions nomes es copien quan la run nova esdeve vigent; una
    simulacio o un parcial no ho son mai, i la run antiga continua sent la base."""
    tender, _ref, _operator, run1, _a, _b, _c1, _block = await _annotated_run(db)
    run2, a2 = await _closing_run(db, tender, provider=provider, chunks_failed=chunks_failed)

    await _finalise(db, run2)
    await db.refresh(run1)
    await db.refresh(a2, attribute_names=["territories"])
    assert (run2.is_current, run1.is_current) == (False, True)
    assert a2.timing == RequirementTiming.UNKNOWN
    assert a2.territories == []
