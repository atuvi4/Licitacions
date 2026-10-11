"""Propostes d'estructura immutables i promocio per la via interna (3B-2 §4.2, §4.4).

El que es protegeix:

* cap proposta sense cita: ni el servei (422), ni el model, ni la base (CHECK);
  les cites han de ser de la licitacio i citables (cap document retirat);
* `company_proposal` reservat: cap productor fora de `operator`/`extraction`;
* promoure crida la via interna amb `origin=promoted`, `proposal_id` i
  `authority=platform_operator`, i enllaca les cites amb l'actor; la proposta
  queda `promoted` i ja no es pot tornar a decidir ni canviar;
* `unknown` mai es coacciona: una proposta amb `timing=unknown`,
  `is_critical=None`, `separately_awardable=None` o
  `territory_completeness=unknown` deixa el desconegut tal qual en una entitat
  nova i no rebaixa cap valor conegut d'una que ja hi era;
* «tornar a desconegut» es l'operacio explicita `reset_to_unknown`;
* rebutjar deixa el motiu i tanca la proposta.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.core.errors import ValidationError
from app.knowledge import vocabulary
from app.models.enums import (
    AnalysisRunStatus,
    AreaKind,
    DocumentContributionStatus,
    DocumentFileOrigin,
    DownloadStatus,
    ExtractionConfidence,
    RequirementCategory,
    RequirementOrigin,
    RequirementTiming,
    SourceType,
    StructureAuthority,
    StructureEntityKind,
    StructureOrigin,
    StructureProposalProducer,
    StructureProposalStatus,
    TenderDocumentOrigin,
    TenderDocumentType,
    TenderSiteRole,
    TerritoryCompleteness,
)
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.structure_proposals import (
    EmptyProposalCitationsError,
    ProposalImmutableError,
    TenderStructureProposal,
)
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tender_structure import TenderBlock, TenderBlockSourceRefLink, TenderSite
from app.models.tenders import Tender, TenderDocument
from app.services import source_refs
from app.services import structure_proposals as proposals
from app.services import tender_scope as scope
from app.services import tender_structure as structure
from app.services.structure_provenance import Provenance
from tests.test_platform_onboarding import create_operator


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def public_tender_with_ref(db, title="Servei public sintetic"):
    """Una licitacio publica (font del feed, sense propietari) amb un document
    analitzable i una cita al seu fitxer."""
    source = Source(
        code=f"feed-{uuid.uuid4().hex[:8]}",
        name="Font sindicada",
        source_type=SourceType.SYNDICATION_ATOM,
    )
    db.add(source)
    await db.flush()
    tender = Tender(source_id=source.id, external_id=uuid.uuid4().hex, title=title)
    db.add(tender)
    await db.flush()
    document = TenderDocument(
        tender_id=tender.id,
        name="PPT.pdf",
        document_type=TenderDocumentType.TECHNICAL_TERMS,
        url=f"https://contractaciopublica.cat/docs/{uuid.uuid4().hex}.pdf",
        mime_type="application/pdf",
        origin=TenderDocumentOrigin.FEED,
        governing_source_id=source.id,
        contribution_status=DocumentContributionStatus.NOT_APPLICABLE,
    )
    db.add(document)
    await db.flush()
    sha = uuid.uuid4().hex * 2
    content = TenderDocumentContent(
        sha256=sha,
        storage_key=f"t/{sha}",
        file_size=10,
        mime_type="application/pdf",
        is_text_extractable=False,
    )
    db.add(content)
    await db.flush()
    file = TenderDocumentFile(
        tender_document_id=document.id,
        content_id=content.id,
        origin=DocumentFileOrigin.FETCHED,
        download_status=DownloadStatus.DOWNLOADED,
    )
    db.add(file)
    await db.flush()
    ref = await source_refs.create_document_file_ref(
        db, tender_id=tender.id, file_id=file.id, location_label="PPT §3"
    )
    await db.commit()
    return tender, ref, document


async def extracted_requirement(db, tender, dedup_key="req-ftth", run=None) -> Requirement:
    if run is None:
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
    requirement = Requirement(
        tender_id=tender.id,
        run_id=run.id,
        origin=RequirementOrigin.EXTRACTED,
        category=RequirementCategory.INSURANCE,
        title="Accessos FTTH",
        confidence=ExtractionConfidence.HIGH,
        dedup_key=dedup_key,
    )
    db.add(requirement)
    await db.flush()
    return requirement


@pytest.fixture
async def operator(db):
    return await create_operator(db)


async def _propose(db, tender, ref, kind, payload, actor):
    return await proposals.create_proposal(
        db,
        tender,
        entity_kind=kind,
        payload=payload,
        source_ref_ids=[ref.id],
        producer=StructureProposalProducer.OPERATOR,
        actor=actor,
    )


# --------------------------------------------------------------------------- #
# Sense cita, no                                                                #
# --------------------------------------------------------------------------- #


async def test_una_proposta_sense_cita_no_existeix_enlloc(db, operator):
    tender, _ref, _doc = await public_tender_with_ref(db)
    with pytest.raises(proposals.ProposalCitationRequiredError) as caught:
        await proposals.create_proposal(
            db,
            tender,
            entity_kind=StructureEntityKind.BLOCK,
            payload={"code": "B1", "name": "Bloc"},
            source_ref_ids=[],
            producer=StructureProposalProducer.OPERATOR,
            actor=operator,
        )
    assert caught.value.status_code == 422
    # El model tampoc l'accepta...
    with pytest.raises(EmptyProposalCitationsError):
        TenderStructureProposal(source_ref_ids=[])
    # ...ni la base (CHECK a les dues bases).
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(
                sa.insert(TenderStructureProposal.__table__).values(
                    id=uuid.uuid4(),
                    tender_id=tender.id,
                    producer="operator",
                    entity_kind="block",
                    natural_key="B1",
                    payload_json={},
                    source_ref_ids=[],
                    status="proposed",
                )
            )
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderStructureProposal)) == 0


async def test_les_cites_han_de_ser_de_la_licitacio_i_citables(db, operator):
    tender, ref, document = await public_tender_with_ref(db)
    other, foreign_ref, _ = await public_tender_with_ref(db, title="Una altra")
    with pytest.raises(proposals.SourceRefTenderMismatchError):
        await _propose(db, tender, foreign_ref, "block", {"code": "B1", "name": "x"}, operator)
    # Una cita d'un document retirat no sosté cap proposta nova.
    document.removed_at = datetime.now(UTC)
    await db.flush()
    with pytest.raises(source_refs.SourceRefDocumentRemovedError):
        await _propose(db, tender, ref, "block", {"code": "B1", "name": "x"}, operator)


async def test_cap_productor_fora_dels_de_3b2(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    with pytest.raises(proposals.ProposalProducerNotAllowedError):
        await proposals.create_proposal(
            db,
            tender,
            entity_kind=StructureEntityKind.BLOCK,
            payload={"code": "B1", "name": "x"},
            source_ref_ids=[ref.id],
            producer="company_proposal",
            actor=operator,
        )
    # Un productor automatic necessita la seva passada.
    with pytest.raises(proposals.ProposalPayloadError):
        await proposals.create_proposal(
            db,
            tender,
            entity_kind=StructureEntityKind.BLOCK,
            payload={"code": "B1", "name": "x"},
            source_ref_ids=[ref.id],
            producer=StructureProposalProducer.EXTRACTION,
        )


async def test_el_payload_te_la_forma_del_servei_intern_i_la_clau_natural(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    requirement = await extracted_requirement(db, tender, dedup_key="r-011")
    with pytest.raises(proposals.ProposalPayloadError):
        await _propose(db, tender, ref, "block", {"code": "B1", "invented": 1}, operator)
    block = await _propose(db, tender, ref, "block", {"code": "B1", "name": "Bloc 1"}, operator)
    site = await _propose(
        db, tender, ref, "site", {"name": "Can  Maset", "role": "remote"}, operator
    )
    timing = await _propose(
        db,
        tender,
        ref,
        "requirement_timing",
        {"requirement_id": str(requirement.id), "timing": "at_submission"},
        operator,
    )
    fact = await _propose(
        db,
        tender,
        ref,
        "contract_fact",
        {"fact_code": "envelopes", "value_json": {"count": 3}, "value_schema": "v1"},
        operator,
    )
    assert block.natural_key == "B1"
    assert site.natural_key == "name:can maset"
    assert timing.natural_key == "r-011"
    assert fact.natural_key == "envelopes|blocks="
    assert block.source_ref_ids == [str(ref.id)]
    assert block.payload_json["code"] == "B1" and "separately_awardable" in block.payload_json
    assert (block.producer, block.status, block.created_by) == (
        StructureProposalProducer.OPERATOR,
        StructureProposalStatus.PROPOSED,
        operator.id,
    )
    # Un requisit d'una altra licitacio no hi entra.
    other, _other_ref, _ = await public_tender_with_ref(db, title="Una altra")
    foreign = await extracted_requirement(db, other, dedup_key="r-x")
    with pytest.raises(Exception) as caught:
        await _propose(
            db,
            tender,
            ref,
            "requirement_timing",
            {"requirement_id": str(foreign.id), "timing": "at_submission"},
            operator,
        )
    assert getattr(caught.value, "status_code", None) == 404


# --------------------------------------------------------------------------- #
# Promoure                                                                      #
# --------------------------------------------------------------------------- #


async def test_promoure_crida_la_via_interna_amb_provenance_i_cites(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    proposal = await _propose(
        db, tender, ref, "block", {"code": "B1", "name": "Bloc 1", "price_points": 6}, operator
    )
    result = await proposals.promote_proposal(db, tender.id, proposal.id, actor=operator)
    await db.commit()

    block = await db.get(TenderBlock, result.entity_id)
    assert (block.code, block.price_points) == ("B1", 6)
    assert (block.origin, block.authority, block.proposal_id) == (
        StructureOrigin.PROMOTED,
        StructureAuthority.PLATFORM_OPERATOR,
        proposal.id,
    )
    assert (block.created_by, block.updated_by) == (operator.id, operator.id)
    link = await db.scalar(
        sa.select(TenderBlockSourceRefLink).where(TenderBlockSourceRefLink.block_id == block.id)
    )
    assert (link.source_ref_id, link.created_by) == (ref.id, operator.id)
    await db.refresh(proposal)
    assert (proposal.status, proposal.promoted_by) == (
        StructureProposalStatus.PROMOTED,
        operator.id,
    )
    assert proposal.promoted_at is not None

    # Decidida: ni es torna a promoure ni es rebutja (409), ni se'n toca el contingut.
    with pytest.raises(proposals.ProposalNotPendingError) as caught:
        await proposals.promote_proposal(db, tender.id, proposal.id, actor=operator)
    assert caught.value.status_code == 409
    with pytest.raises(proposals.ProposalNotPendingError):
        await proposals.reject_proposal(db, tender.id, proposal.id, actor=operator, reason="x")
    proposal.payload_json = {"code": "B2", "name": "x"}
    with pytest.raises(ProposalImmutableError):
        await db.flush()
    await db.rollback()


async def test_els_actors_d_una_proposta_no_es_reescriuen_ni_s_esborra(db, operator):
    """Paritat amb el trigger de PostgreSQL a les bases sense trigger (SQLite)."""
    tender, ref, _ = await public_tender_with_ref(db)
    intruder = await create_operator(db)
    proposal = await _propose(db, tender, ref, "block", {"code": "B1", "name": "x"}, operator)
    await db.commit()
    proposal_id, tender_id, intruder_id = proposal.id, tender.id, intruder.id

    async def fresh() -> TenderStructureProposal:
        return await db.get(TenderStructureProposal, proposal_id, populate_existing=True)

    # Qui la va crear no canvia per cap altre usuari.
    (await fresh()).created_by = intruder_id
    with pytest.raises(ProposalImmutableError):
        await db.flush()
    await db.rollback()

    await db.refresh(operator)  # el rollback ho ha caducat tot
    await proposals.promote_proposal(db, tender_id, proposal_id, actor=operator)
    await db.commit()
    # Decidida: qui la va promoure tampoc.
    (await fresh()).promoted_by = intruder_id
    with pytest.raises(ProposalImmutableError):
        await db.flush()
    await db.rollback()
    # (Un usuari esborrat la deixa a NULL: aixo si que passa.)
    (await fresh()).promoted_by = None
    await db.flush()
    await db.rollback()

    # Un DELETE directe de l'ORM, no.
    await db.delete(await fresh())
    with pytest.raises(ProposalImmutableError):
        await db.flush()
    await db.rollback()
    assert await fresh() is not None


async def test_una_segona_proposta_del_mateix_bloc_el_corregeix_sense_duplicar(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    first = await _propose(db, tender, ref, "block", {"code": "B1", "name": "Bloc"}, operator)
    await proposals.promote_proposal(db, tender.id, first.id, actor=operator)
    second = await _propose(
        db, tender, ref, "block", {"code": "B1", "name": "Bloc", "base_price_ex_vat": "100.50"},
        operator,
    )  # fmt: skip
    result = await proposals.promote_proposal(db, tender.id, second.id, actor=operator)
    blocks = list(
        await db.scalars(sa.select(TenderBlock).where(TenderBlock.tender_id == tender.id))
    )
    assert [b.id for b in blocks] == [result.entity_id]
    assert str(blocks[0].base_price_ex_vat) == "100.50"
    assert blocks[0].proposal_id == second.id


async def test_una_cita_retirada_despres_de_proposar_bloqueja_la_promocio(db, operator):
    tender, ref, document = await public_tender_with_ref(db)
    proposal = await _propose(db, tender, ref, "block", {"code": "B1", "name": "x"}, operator)
    document.removed_at = datetime.now(UTC)
    await db.flush()
    with pytest.raises(source_refs.SourceRefDocumentRemovedError):
        await proposals.promote_proposal(db, tender.id, proposal.id, actor=operator)
    await db.refresh(proposal)
    assert proposal.status == StructureProposalStatus.PROPOSED
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderBlock)) == 0


async def test_els_territoris_i_la_temporalitat_guarden_la_cita_a_la_proposta(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    requirement = await extracted_requirement(db, tender)
    proposal = await _propose(
        db,
        tender,
        ref,
        "territory",
        {
            "requirement_id": str(requirement.id),
            "territory": {"kind": "municipality", "label": "L'Escala"},
            "territory_completeness": "complete",
        },
        operator,
    )
    await proposals.promote_proposal(db, tender.id, proposal.id, actor=operator)
    await db.refresh(requirement, attribute_names=["territories"])
    (territory,) = requirement.territories
    assert requirement.territory_completeness == TerritoryCompleteness.COMPLETE
    assert (territory.label, territory.origin, territory.proposal_id) == (
        "L'Escala",
        StructureOrigin.PROMOTED,
        proposal.id,
    )
    assert territory.created_by == operator.id

    timing = await _propose(
        db,
        tender,
        ref,
        "requirement_timing",
        {"requirement_id": str(requirement.id), "timing": "at_submission"},
        operator,
    )
    await proposals.promote_proposal(db, tender.id, timing.id, actor=operator)
    assert requirement.timing == RequirementTiming.AT_SUBMISSION
    # La temporalitat no te provenance a la fila: la tracabilitat es la proposta.
    await db.refresh(timing)
    assert (timing.status, timing.natural_key, timing.promoted_by) == (
        StructureProposalStatus.PROMOTED,
        requirement.dedup_key,
        operator.id,
    )
    assert timing.promoted_at is not None
    assert timing.source_ref_ids == [str(ref.id)]


# --------------------------------------------------------------------------- #
# `unknown` mai es coacciona                                                    #
# --------------------------------------------------------------------------- #


async def test_promoure_un_desconegut_el_deixa_desconegut(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    requirement = await extracted_requirement(db, tender)
    timing = await _propose(
        db,
        tender,
        ref,
        "requirement_timing",
        {"requirement_id": str(requirement.id), "timing": "unknown"},
        operator,
    )
    site = await _propose(
        db, tender, ref, "site", {"code": "S1", "role": "remote", "is_critical": None}, operator
    )
    block = await _propose(
        db, tender, ref, "block", {"code": "B1", "name": "x", "separately_awardable": None},
        operator,
    )  # fmt: skip
    completeness = await _propose(
        db,
        tender,
        ref,
        "territory",
        {"requirement_id": str(requirement.id), "territory_completeness": "unknown"},
        operator,
    )
    for proposal in (timing, site, block, completeness):
        await proposals.promote_proposal(db, tender.id, proposal.id, actor=operator)
    await db.flush()
    assert requirement.timing == RequirementTiming.UNKNOWN
    assert requirement.territory_completeness == TerritoryCompleteness.UNKNOWN
    new_site = await db.scalar(sa.select(TenderSite).where(TenderSite.tender_id == tender.id))
    assert new_site.is_critical is None
    new_block = await db.scalar(sa.select(TenderBlock).where(TenderBlock.tender_id == tender.id))
    assert new_block.separately_awardable is None


async def test_un_desconegut_no_rebaixa_cap_valor_conegut(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    requirement = await extracted_requirement(db, tender)
    owner = Provenance.operator(operator.id)
    structure.set_timing(requirement, RequirementTiming.AT_SUBMISSION)
    await scope.set_territory_semantics(
        db,
        tender,
        requirement,
        completeness=TerritoryCompleteness.COMPLETE,
        territories=[{"kind": AreaKind.MUNICIPALITY, "label": "L'Escala"}],
        provenance=owner,
    )
    site = await structure.create_site(
        db,
        tender,
        {"code": "S1", "role": TenderSiteRole.REMOTE, "is_critical": True},
        provenance=owner,
    )
    block = await structure.create_block(
        db, tender, {"code": "B1", "name": "x", "separately_awardable": False}, provenance=owner
    )
    for kind, payload in (
        ("requirement_timing", {"requirement_id": str(requirement.id), "timing": "unknown"}),
        (
            "territory",
            {
                "requirement_id": str(requirement.id),
                "territory": {"kind": "comarca", "label": "Alt Emporda"},
                "territory_completeness": "unknown",
            },
        ),
        ("site", {"code": "S1", "role": "remote", "is_critical": None, "address": "C/ Major 1"}),
        ("block", {"code": "B1", "name": "x", "separately_awardable": None}),
    ):
        proposal = await _propose(db, tender, ref, kind, payload, operator)
        await proposals.promote_proposal(db, tender.id, proposal.id, actor=operator)
    await db.flush()
    await db.refresh(requirement, attribute_names=["territories"])
    assert requirement.timing == RequirementTiming.AT_SUBMISSION
    assert requirement.territory_completeness == TerritoryCompleteness.COMPLETE
    assert sorted(t.label for t in requirement.territories) == ["Alt Emporda", "L'Escala"]
    # El que la proposta si que afirma, s'aplica; el desconegut, no.
    assert (site.is_critical, site.address) == (True, "C/ Major 1")
    assert block.separately_awardable is False


async def test_tornar_a_desconegut_es_una_operacio_explicita(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    requirement = await extracted_requirement(db, tender)
    gold = Provenance.gold(uuid.uuid4())
    site = await structure.create_site(
        db, tender, {"code": "S1", "role": TenderSiteRole.REMOTE, "is_critical": True},
        provenance=gold,
    )  # fmt: skip
    structure.set_timing(requirement, RequirementTiming.AT_EXECUTION)
    await db.flush()

    operator_prov = Provenance.operator(operator.id)
    changed = await proposals.reset_to_unknown(
        db,
        tender,
        entity_kind=StructureEntityKind.SITE,
        entity_id=site.id,
        fields=["is_critical"],
        provenance=operator_prov,
    )
    assert changed == ["is_critical"]
    assert site.is_critical is None
    assert (site.origin, site.updated_by) == (StructureOrigin.OPERATOR, operator.id)
    # La seu (i les seves dades conegudes) es queda: no es cap esborrat.
    assert await db.get(TenderSite, site.id) is not None
    assert await proposals.reset_to_unknown(
        db,
        tender,
        entity_kind=StructureEntityKind.REQUIREMENT_TIMING,
        entity_id=requirement.id,
        fields=["timing"],
        provenance=operator_prov,
    ) == ["timing"]
    assert requirement.timing == RequirementTiming.UNKNOWN
    # Idempotent: res mes a canviar.
    assert (
        await proposals.reset_to_unknown(
            db,
            tender,
            entity_kind=StructureEntityKind.SITE,
            entity_id=site.id,
            fields=["is_critical"],
            provenance=operator_prov,
        )
        == []
    )
    # Nomes els camps amb «desconegut».
    for kind, fields in (
        (StructureEntityKind.SITE, ["code"]),
        (StructureEntityKind.CONTRACT_FACT, ["value_json"]),
    ):
        with pytest.raises(proposals.ResetUnknownNotAllowedError) as caught:
            await proposals.reset_to_unknown(
                db,
                tender,
                entity_kind=kind,
                entity_id=site.id,
                fields=fields,
                provenance=operator_prov,
            )
        assert caught.value.status_code == 422


# --------------------------------------------------------------------------- #
# Rebutjar                                                                      #
# --------------------------------------------------------------------------- #


async def test_rebutjar_deixa_el_motiu_i_tanca_la_proposta(db, operator):
    tender, ref, _ = await public_tender_with_ref(db)
    proposal = await _propose(db, tender, ref, "block", {"code": "B1", "name": "x"}, operator)
    with pytest.raises(ValidationError):
        await proposals.reject_proposal(db, tender.id, proposal.id, actor=operator, reason="  ")
    rejected = await proposals.reject_proposal(
        db, tender.id, proposal.id, actor=operator, reason="El bloc no consta al PPT."
    )
    await db.commit()
    assert (rejected.status, rejected.rejected_by, rejected.rejected_reason) == (
        StructureProposalStatus.REJECTED,
        operator.id,
        "El bloc no consta al PPT.",
    )
    with pytest.raises(proposals.ProposalNotPendingError):
        await proposals.promote_proposal(db, tender.id, proposal.id, actor=operator)
    assert await db.scalar(sa.select(sa.func.count()).select_from(TenderBlock)) == 0
    assert [p.id for p in await proposals.list_proposals(db, tender.id)] == [proposal.id]
    assert (
        await proposals.list_proposals(db, tender.id, status=StructureProposalStatus.PROPOSED) == []
    )
