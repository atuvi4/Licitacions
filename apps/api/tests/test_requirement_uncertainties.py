"""Perfil `product-v2` i incerteses materials (0062).

Tot offline: proveidor fals, PDF sintetic passat pel parser de producte (l'oracle es
el text EXTRET, no el text font). Cap dada real.

Que es protegeix:
* cites literals: s'accepten; alterades (accent, puntuacio, parafrasi) o d'una altra
  pagina es rebutgen amb diagnostic de codis;
* atomicitat: dues obligacions de la mateixa pagina, dos requisits;
* incerteses: es persisteixen amb provenance; la cita nomes si es literal; mai son
  requisit, criteri ni forat; no sumen a cap comptador;
* contracte: una incertesa mal formada es descarta sola, mai el chunk;
* procedencia: amb cita o sense, l'extraccio i la pagina es verifiquen; el que no
  quadra queda diagnosticat, mai en silenci;
* solapament: la mateixa incertesa es desa una vegada, amb la millor procedencia
  (`literal > rejected > not_provided`), sigui quin sigui l'ordre dels chunks;
* represa: es copien sense duplicar-les ni perdre-les;
* V1 intacte i identitats separades: V1 no crea incerteses, no comparteix run, ni
  reutilitzacio, ni represa amb `product-v2`.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.ai.profiles import DEFAULT_PROFILE, PRODUCT_V2, PROFILES, V1
from app.ai.providers import fake
from app.core.config import settings
from app.core.errors import ResumeNotAllowedError
from app.models.enums import (
    AnalysisRunStatus,
    TenderDocumentType,
    UncertaintyQuoteStatus,
    UncertaintyScope,
)
from app.models.package import TenderPackageGap
from app.models.requirements import (
    AwardCriterion,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
    RequirementUncertainty,
)
from app.models.tender_documents import TenderDocumentPage
from app.services.requirement_analysis import (
    UncertaintyLedger,
    analysis_identity,
    analysis_input,
    analyze_tender_requirements,
    uncertainty_identity,
)
from tests.fixtures.canary_product_v2 import (
    CRITERION,
    INDETERMINATE,
    MANDATORY,
    PCAP_PAGES,
    oracle_sentence,
)
from tests.fixtures.pdf_builder import build_pdf
from tests.test_global_classification import make_public_tender, processed_feed_document

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_provider():
    fake.reset()
    yield
    fake.reset()


@pytest.fixture
async def canary(db):
    """Licitacio publica amb el PCAP sintetic processat (7 pagines, 2 chunks)."""
    tender = await make_public_tender(db)
    document = await processed_feed_document(
        db,
        tender,
        "PCAP-sintetic.pdf",
        TenderDocumentType.ADMINISTRATIVE_TERMS,
        build_pdf(PCAP_PAGES),
    )
    current = await analysis_input(db, tender.id)
    (doc,) = current.documents
    pages = {page.page_number: page.text for page in doc.pages}
    return {
        "tender": tender,
        "document": document,
        "extraction_id": doc.extraction_id,
        "pages": pages,
    }


def _quote(canary, page: int, marker: str) -> str:
    return oracle_sentence(canary["pages"][page], marker)


def _req(
    canary,
    page,
    marker,
    *,
    title,
    category="technical_requirement",
    obligation="mandatory",
    quote=None,
    cited_page=None,
) -> dict:
    return {
        "category": category,
        "obligation": obligation,
        "title": title,
        "confidence": "high",
        "evidence": [
            {
                "extraction_id": canary["extraction_id"],
                "page_number": cited_page or page,
                "quote": quote if quote is not None else _quote(canary, page, marker),
            }
        ],
    }


def _uncertainty(canary, page, marker, *, scope="solvency", quote=None, note=None) -> dict:
    return {
        "scope": scope,
        "extraction_id": canary["extraction_id"],
        "page_number": page,
        "quote": quote if quote is not None else _quote(canary, page, marker),
        "note": note or "La classificacio empresarial exigible no es concreta.",
    }


def ideal_responses(canary) -> list[dict]:
    """El que hauria de retornar un model que compleix product-v2 (chunks 0 i 1)."""
    name, page, marker = CRITERION
    return [
        {
            "requirements": [
                _req(
                    canary,
                    2,
                    "ISO 9001",
                    title="Certificat ISO 9001 vigent",
                    category="certification",
                ),
                _req(
                    canary,
                    2,
                    "garantia definitiva",
                    title="Garantia definitiva del 5 %",
                    category="contract_condition",
                ),
                _req(
                    canary,
                    3,
                    "4 hores des de",
                    title="Atencio d'avaries critiques en 4 h",
                    category="contract_condition",
                ),
                _req(
                    canary,
                    3,
                    "600.000",
                    title="Asseguranca de RC de 600.000 EUR",
                    category="insurance",
                ),
            ],
            "award_criteria": [
                {
                    "name": "Preu",
                    "evidence": [
                        {
                            "extraction_id": canary["extraction_id"],
                            "page_number": page,
                            "quote": _quote(canary, page, marker),
                        }
                    ],
                }
            ],
            "uncertainties": [_uncertainty(canary, 5, "determinarà")],
        },
        {
            "requirements": [
                _req(
                    canary,
                    7,
                    "sobre",
                    title="Presentacio en sobre digital",
                    category="administrative_documentation",
                ),
            ],
        },
    ]


def v1_responses(canary) -> list[dict]:
    """La mateixa sortida en el contracte V1 (incerteses `{note, page_number}`)."""
    responses = ideal_responses(canary)
    responses[0]["uncertainties"] = [{"note": "Classificacio per determinar", "page_number": 5}]
    return responses


async def _run(db, canary, responses, *, profile=PRODUCT_V2, **kwargs):
    fake.queue_many(responses)
    return (await analyze_tender_requirements(db, canary["tender"], profile=profile, **kwargs)).run


async def _rows(db, model, run_id):
    return list((await db.scalars(sa.select(model).where(model.run_id == run_id))).all())


async def acceptance(db, run) -> dict[str, bool]:
    """Les condicions de l'Attempt 3, calculades sobre el que s'ha DESAT."""
    reqs = await _rows(db, Requirement, run.id)
    evidence = {
        e.requirement_id: e
        for e in await db.scalars(
            sa.select(RequirementEvidence).where(
                RequirementEvidence.requirement_id.in_([r.id for r in reqs])
            )
        )
    }
    pages = (
        {
            p.id: p.text
            for p in await db.scalars(
                sa.select(TenderDocumentPage).where(
                    TenderDocumentPage.id.in_([e.page_id for e in evidence.values()])
                )
            )
        }
        if evidence
        else {}
    )
    uncertainties = await _rows(db, RequirementUncertainty, run.id)
    criteria = list(
        (await db.scalars(sa.select(AwardCriterion).where(AwardCriterion.run_id == run.id))).all()
    )
    out = {}
    for fact, page, marker in MANDATORY:
        out[fact] = any(
            r.obligation == "mandatory"
            and evidence[r.id].page_number == page
            and marker.lower() in evidence[r.id].quote.lower()
            for r in reqs
            if r.id in evidence
        )
    out["criterion_p4"] = bool(criteria)
    out["citations_literal"] = all(e.quote in pages[e.page_id] for e in evidence.values())
    _, page5, _ = INDETERMINATE
    out["p5_not_requirement"] = not any(
        evidence[r.id].page_number == page5 for r in reqs if r.id in evidence
    )
    out["p5_uncertainty"] = any(u.page_number == page5 for u in uncertainties)
    return out


# --------------------------------------------------------------------------- #
# Acceptacio de l'Attempt 3 (offline)                                           #
# --------------------------------------------------------------------------- #


async def test_una_sortida_que_compleix_product_v2_passa_l_acceptacio(db, canary):
    run = await _run(db, canary, ideal_responses(canary))
    assert run.status == AnalysisRunStatus.COMPLETED
    assert all((await acceptance(db, run)).values()), await acceptance(db, run)


async def test_perdre_la_p3_o_fer_certa_la_p5_no_passa_l_acceptacio(db, canary):
    responses = ideal_responses(canary)
    reqs = responses[0]["requirements"]
    # p3 amb cites alterades (accent tret i parafrasi) i p5 com a requisit cert.
    reqs[2]["evidence"][0]["quote"] = reqs[2]["evidence"][0]["quote"].replace("màxim", "maxim")
    reqs[3]["evidence"][0]["quote"] = "Cal una asseguranca de 600.000 euros."
    reqs.append(
        _req(
            canary,
            5,
            "determinarà",
            title="Classificacio obligatoria",
            category="business_classification",
        )
    )
    responses[0]["uncertainties"] = []
    run = await _run(db, canary, responses)
    result = await acceptance(db, run)
    assert result["p3_termini_4h"] is False and result["p3_rc_600000"] is False
    assert result["p5_not_requirement"] is False and result["p5_uncertainty"] is False


# --------------------------------------------------------------------------- #
# Cites                                                                         #
# --------------------------------------------------------------------------- #


async def test_les_cites_alterades_o_de_pagina_equivocada_es_rebutgen_amb_diagnostic(db, canary):
    quote3 = _quote(canary, 3, "4 hores des de")
    responses = [
        {
            "requirements": [
                _req(canary, 3, "", title="Literal", quote=quote3),
                _req(canary, 3, "", title="Sense accent", quote=quote3.replace("màxim", "maxim")),
                _req(
                    canary, 3, "", title="Sense coma", quote=quote3.replace(", les 24", " les 24")
                ),
                _req(
                    canary, 3, "", title="Parafrasi", quote="Les avaries s'atenen en quatre hores."
                ),
                _req(canary, 3, "", title="Pagina equivocada", quote=quote3, cited_page=2),
            ],
        },
        {},
    ]
    run = await _run(db, canary, responses)
    assert run.requirements_extracted == 1
    (chunk0, _chunk1) = run.chunk_coverage["chunks"]
    evidence = [r for r in chunk0["rejections"] if r["stage"] == "evidence"]
    assert [(r["index"], r["reason"], r["cited_page"], r["diagnosis"]) for r in evidence] == [
        (1, "quote_not_found", 3, "accent_variant"),
        (2, "quote_not_found", 3, "absent"),
        (3, "quote_not_found", 3, "absent"),
        (4, "quote_not_found", 2, "on_other_page:3"),
    ]
    items = [r for r in chunk0["rejections"] if r["stage"] == "item"]
    assert {(r["obligation"], r["category"]) for r in items} == {
        ("mandatory", "technical_requirement")
    }


async def test_dues_obligacions_de_la_mateixa_pagina_son_dos_requisits(db, canary):
    run = await _run(db, canary, ideal_responses(canary))
    reqs = await _rows(db, Requirement, run.id)
    page3 = [
        r
        for r in reqs
        if (
            await db.scalar(
                sa.select(RequirementEvidence.page_number).where(
                    RequirementEvidence.requirement_id == r.id
                )
            )
        )
        == 3
    ]
    assert len(page3) == 2
    assert {r.category for r in page3} == {"contract_condition", "insurance"}


# --------------------------------------------------------------------------- #
# Incerteses                                                                    #
# --------------------------------------------------------------------------- #


async def test_una_incertesa_material_amb_cita_literal_es_desa_amb_provenance(db, canary):
    run = await _run(db, canary, ideal_responses(canary))
    (u,) = await _rows(db, RequirementUncertainty, run.id)
    assert u.scope == UncertaintyScope.SOLVENCY
    assert u.quote_status == UncertaintyQuoteStatus.LITERAL
    assert u.page_number == 5 and u.page_id is not None and u.source_chunk_index == 0
    assert str(u.extraction_id) == canary["extraction_id"]
    assert u.tender_document_id == canary["document"].id and u.tender_id == canary["tender"].id
    page = await db.get(TenderDocumentPage, u.page_id)
    assert page.page_number == 5 and page.text[u.char_start : u.char_end] == u.quote
    assert u.diagnosis is None


async def test_una_incertesa_amb_cita_no_literal_es_conserva_sense_cita(db, canary):
    responses = ideal_responses(canary)
    altered = _quote(canary, 5, "determinarà").replace("determinarà", "determinara")
    responses[0]["uncertainties"] = [_uncertainty(canary, 5, "", quote=altered)]
    run = await _run(db, canary, responses)
    (u,) = await _rows(db, RequirementUncertainty, run.id)
    assert u.quote_status == UncertaintyQuoteStatus.REJECTED
    assert u.quote is None and u.page_id is None and u.char_start is None
    assert u.page_number == 5 and u.note
    assert u.diagnosis == "quote_not_found:accent_variant"
    chunk0 = run.chunk_coverage["chunks"][0]
    assert {
        "item": "uncertainty",
        "index": 0,
        "stage": "evidence",
        "reason": "quote_not_found",
        "cited_page": 5,
        "diagnosis": "accent_variant",
    } in chunk0["rejections"]


async def test_una_incertesa_sense_cita_es_conserva(db, canary):
    responses = ideal_responses(canary)
    item = _uncertainty(canary, 5, "")
    item["quote"] = None
    responses[0]["uncertainties"] = [item]
    run = await _run(db, canary, responses)
    (u,) = await _rows(db, RequirementUncertainty, run.id)
    assert u.quote_status == UncertaintyQuoteStatus.NOT_PROVIDED and u.quote is None


async def test_una_incertesa_no_es_cap_requisit_criteri_ni_forat(db, canary):
    without = ideal_responses(canary)
    without[0]["uncertainties"] = []
    baseline = await _run(db, canary, without, force=True)
    run = await _run(db, canary, ideal_responses(canary), force=True)
    assert len(await _rows(db, RequirementUncertainty, run.id)) == 1
    assert len(await _rows(db, RequirementUncertainty, baseline.id)) == 0
    for field in ("requirements_extracted", "requirements_rejected", "award_criteria_extracted"):
        assert getattr(run, field) == getattr(baseline, field), field
    assert len(await _rows(db, Requirement, run.id)) == len(
        await _rows(db, Requirement, baseline.id)
    )
    mandatory = await db.scalar(
        sa.select(sa.func.count(Requirement.id)).where(
            Requirement.run_id == run.id, Requirement.obligation == "mandatory"
        )
    )
    assert mandatory == 5
    assert await db.scalar(sa.select(sa.func.count(TenderPackageGap.id))) == 0


async def test_la_mateixa_incertesa_de_dos_chunks_solapats_es_desa_una_vegada(db, canary):
    # La pagina 6 es a tots dos chunks (solapament): el model la pot repetir.
    responses = ideal_responses(canary)
    repeated = _uncertainty(
        canary, 6, "penalitat", scope="cost", note="El maxim de penalitats depen del preu final."
    )
    responses[0]["uncertainties"].append(repeated)
    responses[1]["uncertainties"] = [dict(repeated)]
    run = await _run(db, canary, responses)
    rows = await _rows(db, RequirementUncertainty, run.id)
    assert sorted(u.page_number for u in rows) == [5, 6]
    assert {"item": "uncertainty", "index": 0, "stage": "dedup", "reason": "duplicate_key"} in (
        run.chunk_coverage["chunks"][1]["rejections"]
    )


# --------------------------------------------------------------------------- #
# Represa i immutabilitat                                                       #
# --------------------------------------------------------------------------- #


async def test_una_represa_conserva_les_incerteses_sense_duplicar_ni_tocar_l_origen(db, canary):
    responses = ideal_responses(canary)
    source = await _run(db, canary, responses[:1], only_chunks=frozenset({0}))
    assert source.status == AnalysisRunStatus.PARTIAL
    (original,) = await _rows(db, RequirementUncertainty, source.id)

    resumed = await _run(db, canary, responses[1:], resume_from_run_id=source.id)
    assert resumed.status == AnalysisRunStatus.COMPLETED
    (copy,) = await _rows(db, RequirementUncertainty, resumed.id)
    assert copy.id != original.id and copy.run_id == resumed.id
    for field in (
        "scope",
        "note",
        "extraction_id",
        "tender_document_id",
        "page_number",
        "page_id",
        "quote",
        "char_start",
        "char_end",
        "quote_status",
        "dedup_key",
        "source_chunk_index",
    ):
        assert getattr(copy, field) == getattr(original, field), field
    still = await _rows(db, RequirementUncertainty, source.id)
    assert [u.id for u in still] == [original.id]


# --------------------------------------------------------------------------- #
# V1 i identitat                                                                #
# --------------------------------------------------------------------------- #


async def test_v1_no_crea_incerteses_i_continua_igual(db, canary):
    assert DEFAULT_PROFILE is V1 and V1.material_uncertainties is False
    assert "VERBATIM" not in V1.system_prompt and "VERBATIM" in PRODUCT_V2.system_prompt
    # product-v2 = V1 amb NOMES les dues linies documentades substituides i els blocs nous.
    from app.ai.prompts.requirements_product_v2 import _V1_OVERRIDES

    v1_head = V1.system_prompt.split("MATERIAL NO CONFIABLE")[0]
    for line, replacement in _V1_OVERRIDES:
        v1_head = v1_head.replace(line, replacement)
    assert v1_head in PRODUCT_V2.system_prompt
    assert (V1.prompt_version, V1.extractor_version, V1.schema_version) == (
        "requirements-v1",
        "tender-requirements-v1",
        "requirements-schema-v1",
    )
    # product-v2 es intern: no es cap perfil public.
    assert set(PROFILES) == {"v1", "v2"} and PROFILES["v2"].name == "v2"
    run = await _run(db, canary, v1_responses(canary), profile=V1)
    assert run.status == AnalysisRunStatus.COMPLETED
    assert await db.scalar(sa.select(sa.func.count(RequirementUncertainty.id))) == 0
    # Les pagines de la incertesa queden a la cobertura (b3d7a02), sense text.
    assert run.chunk_coverage["chunks"][0]["notes"] == {
        "uncertainty_pages": [5],
        "document_note_pages": [],
    }


async def test_v1_i_product_v2_tenen_identitats_i_runs_separats(db, canary):
    current = await analysis_input(db, canary["tender"].id)
    provider = fake.FakeProvider()
    v1_identity = analysis_identity(current.documents, profile=V1, provider=provider)
    pv2_identity = analysis_identity(current.documents, profile=PRODUCT_V2, provider=provider)
    assert v1_identity != pv2_identity

    v1_run = await _run(db, canary, v1_responses(canary), profile=V1)
    assert v1_run.status == AnalysisRunStatus.COMPLETED
    pv2_run = await _run(db, canary, ideal_responses(canary))
    assert pv2_run.id != v1_run.id  # no es reaprofita el V1
    assert pv2_run.input_fingerprint == v1_run.input_fingerprint  # mateixos documents
    assert (pv2_run.extractor_version, pv2_run.prompt_version, pv2_run.schema_version) == (
        "tender-requirements-product-v2",
        "requirements-product-v2.1",
        "requirements-schema-product-v2.1",
    )
    assert pv2_run.analysis_identity == pv2_identity and v1_run.analysis_identity == v1_identity
    # El mateix perfil si que reaprofita el seu run (cap crida nova al proveidor).
    sent = len(fake.SENT_PAYLOADS)
    again = (await analyze_tender_requirements(db, canary["tender"], profile=PRODUCT_V2)).run
    assert again.id == pv2_run.id and len(fake.SENT_PAYLOADS) == sent


async def test_una_represa_product_v2_no_pot_reprendre_un_run_v1(db, canary):
    v1_partial = await _run(
        db, canary, v1_responses(canary)[:1], profile=V1, only_chunks=frozenset({0})
    )
    assert v1_partial.status == AnalysisRunStatus.PARTIAL
    sent = len(fake.SENT_PAYLOADS)
    with pytest.raises(ResumeNotAllowedError):
        await analyze_tender_requirements(
            db, canary["tender"], profile=PRODUCT_V2, resume_from_run_id=v1_partial.id
        )
    assert len(fake.SENT_PAYLOADS) == sent
    runs = await db.scalar(sa.select(sa.func.count(RequirementExtractionRun.id)))
    assert runs == 1


async def test_v1_rebutja_el_format_d_incertesa_de_product_v2(db, canary):
    """Cada contracte es el seu: una incertesa product-v2 no es V1 valid."""
    run = await _run(db, canary, ideal_responses(canary), profile=V1)
    assert run.chunk_coverage["chunks"][0]["error"] == "schema_invalid"
    assert await db.scalar(sa.select(sa.func.count(RequirementUncertainty.id))) == 0


async def test_el_contracte_product_v2_rebutja_ambits_fora_del_vocabulari():
    from pydantic import ValidationError

    from app.ai.schemas_product_v2 import ChunkExtractionOutProductV2

    payload = {
        "uncertainties": [
            {"scope": "altres", "extraction_id": str(uuid.uuid4()), "page_number": 1, "note": "x"}
        ]
    }
    with pytest.raises(ValidationError):
        ChunkExtractionOutProductV2.model_validate(payload)
    ok = ChunkExtractionOutProductV2.model_validate(
        {"uncertainties": [{"scope": "cost", "extraction_id": "e", "page_number": 1, "note": "x"}]}
    )
    assert ok.uncertainties[0].quote is None


# --------------------------------------------------------------------------- #
# Contracte element a element                                                   #
# --------------------------------------------------------------------------- #


def _subset(entry: dict, entries: list[dict]) -> bool:
    return any(all(e.get(k) == v for k, v in entry.items()) for e in entries)


async def test_una_incertesa_mal_formada_es_descarta_sola_i_el_chunk_es_conserva(db, canary):
    responses = ideal_responses(canary)
    good = responses[0]["uncertainties"][0]
    other = _quote(canary, 6, "penalitat").replace("màxim", "maxim")
    responses[0]["uncertainties"] = [
        {**good, "scope": "technical"},  # 0: fora del vocabulari
        good,  # 1: valida
        {**good, "note": "x" * 501},  # 2: nota massa llarga
        {**good, "note": "   "},  # 3: nota nomes d'espais
        {**good, "page_number": 6, "quote": other, "scope": "cost", "note": "Una altra"},  # 4
        {**good, "inventat": 1},  # 5: clau desconeguda
    ]
    # Un requisit rebutjat: els seus diagnostics han d'anar abans que els de les incerteses.
    responses[0]["requirements"].append(_req(canary, 3, "", title="Inventat", quote="No hi es."))
    run = await _run(db, canary, responses)
    assert run.status == AnalysisRunStatus.COMPLETED
    assert run.requirements_extracted == 5 and run.award_criteria_extracted == 1
    rows = sorted(await _rows(db, RequirementUncertainty, run.id), key=lambda u: u.page_number)
    assert [(u.page_number, u.quote_status) for u in rows] == [
        (5, UncertaintyQuoteStatus.LITERAL),
        (6, UncertaintyQuoteStatus.REJECTED),
    ]
    rejections = run.chunk_coverage["chunks"][0]["rejections"]
    schema = [r for r in rejections if r["item"] == "uncertainty" and r["stage"] == "schema"]
    assert [(r["index"], r["reason"], r["detail"], r.get("field")) for r in schema] == [
        (0, "schema_invalid", "enum", "scope"),
        (2, "schema_invalid", "string_too_long", "note"),
        (3, "schema_invalid", "string_too_short", "note"),
        (5, "schema_invalid", "extra_forbidden", None),
    ]
    # La posicio que es reporta es l'ORIGINAL de la resposta, no la de la llista filtrada.
    assert _subset(
        {"item": "uncertainty", "index": 4, "stage": "evidence", "reason": "quote_not_found"},
        rejections,
    )
    # Mai el valor: cap text del model a la cobertura.
    assert "'technical'" not in str(rejections) and "xxxx" not in str(rejections)
    # Els diagnostics dels requisits van abans que els de les incerteses.
    items = [r["item"] for r in rejections]
    assert "requirement" in items
    assert "requirement" not in items[items.index("uncertainty") :]


async def test_unes_incerteses_que_no_son_una_llista_no_fan_perdre_el_chunk(db, canary):
    responses = ideal_responses(canary)
    responses[0]["uncertainties"] = {"note": "no es una llista"}
    run = await _run(db, canary, responses)
    assert run.status == AnalysisRunStatus.COMPLETED and run.requirements_extracted == 5
    assert await _rows(db, RequirementUncertainty, run.id) == []
    assert _subset(
        {"item": "uncertainty", "index": -1, "stage": "schema", "field": "uncertainties"},
        run.chunk_coverage["chunks"][0]["rejections"],
    )


# --------------------------------------------------------------------------- #
# Procedencia                                                                   #
# --------------------------------------------------------------------------- #


async def test_la_procedencia_es_verifica_amb_cita_o_sense_i_mai_es_desa_en_silenci(db, canary):
    responses = ideal_responses(canary)
    base = _uncertainty(canary, 5, "determinarà")
    alien = str(uuid.uuid4())
    responses[0]["uncertainties"] = [
        {**base, "quote": None, "page_number": 99, "note": "Fora del chunk"},
        {**base, "quote": None, "extraction_id": alien, "note": "Extraccio aliena"},
        {**base, "extraction_id": alien, "note": "Cita amb extraccio aliena"},
        {**_uncertainty(canary, 7, "sobre"), "note": "Pagina d'un altre chunk"},
    ]
    run = await _run(db, canary, responses)
    rows = {u.note: u for u in await _rows(db, RequirementUncertainty, run.id)}
    assert {note: (u.page_number, u.quote_status, u.diagnosis) for note, u in rows.items()} == {
        "Fora del chunk": (99, UncertaintyQuoteStatus.NOT_PROVIDED, "page_not_in_chunk"),
        "Extraccio aliena": (5, UncertaintyQuoteStatus.NOT_PROVIDED, "extraction_mismatch"),
        "Cita amb extraccio aliena": (5, UncertaintyQuoteStatus.REJECTED, "extraction_mismatch"),
        "Pagina d'un altre chunk": (
            7,
            UncertaintyQuoteStatus.REJECTED,
            "page_not_in_chunk:absent",
        ),
    }
    # L'extraccio i el document son SEMPRE els del chunk, mai els que declara el model.
    assert {str(u.extraction_id) for u in rows.values()} == {canary["extraction_id"]}
    assert {u.tender_document_id for u in rows.values()} == {canary["document"].id}
    found = [
        (r["index"], r["stage"], r["reason"], r["cited_page"])
        for r in run.chunk_coverage["chunks"][0]["rejections"]
        if r["item"] == "uncertainty"
    ]
    assert found == [
        (0, "provenance", "page_not_in_chunk", 99),
        (1, "provenance", "extraction_mismatch", 5),
        (2, "evidence", "extraction_mismatch", 5),
        (3, "evidence", "page_not_in_chunk", 7),
    ]


async def test_una_cita_massa_llarga_es_retalla_i_es_desa_exactament_el_tros_de_pagina(
    db, canary, monkeypatch
):
    monkeypatch.setattr(settings, "REQUIREMENTS_MAX_QUOTE_CHARS", 40)
    run = await _run(db, canary, ideal_responses(canary))
    (u,) = await _rows(db, RequirementUncertainty, run.id)
    full = _quote(canary, 5, "determinarà")
    assert len(full) > 40
    assert u.quote_status == UncertaintyQuoteStatus.LITERAL and len(u.quote) <= 40
    page = await db.get(TenderDocumentPage, u.page_id)
    assert page.text[u.char_start : u.char_end] == u.quote and full.startswith(u.quote)


# --------------------------------------------------------------------------- #
# Deduplicacio                                                                  #
# --------------------------------------------------------------------------- #

_P6_NOTE = "El maxim de penalitats depen del preu final."


def _p6(canary, **overrides) -> dict:
    return {**_uncertainty(canary, 6, "penalitat", scope="cost", note=_P6_NOTE), **overrides}


_ALIEN = str(uuid.UUID(int=7))

#: Formes de la mateixa incertesa real (pagina 6, solapament dels dos chunks).
_FORMS = {
    "literal": {},
    "rejected": {"quote": "Les penalitats depenen del preu del contracte."},
    "none": {"quote": None},
    "unverified": {"quote": None, "extraction_id": _ALIEN},
}


def _overlap_responses(canary, first: str, second: str) -> list[dict]:
    responses = ideal_responses(canary)
    responses[0]["uncertainties"].append(_p6(canary, **_FORMS[first]))
    responses[1]["uncertainties"] = [_p6(canary, **_FORMS[second])]
    return responses


def _shape(rows) -> list[tuple]:
    return sorted(
        (
            u.page_number,
            u.source_chunk_index,
            str(u.quote_status),
            u.diagnosis,
            u.dedup_key,
            u.quote,
            u.page_id,
            u.char_start,
            u.char_end,
            u.note,
        )
        for u in rows
    )


#: (chunk 0, chunk 1) -> (estat conservat, chunk de la fila, motiu al chunk 1).
_OVERLAP_CASES = [
    ("none", "literal", UncertaintyQuoteStatus.LITERAL, 1, "replaced_lower_provenance"),
    ("literal", "none", UncertaintyQuoteStatus.LITERAL, 0, "duplicate_key"),
    ("rejected", "literal", UncertaintyQuoteStatus.LITERAL, 1, "replaced_lower_provenance"),
    ("literal", "rejected", UncertaintyQuoteStatus.LITERAL, 0, "duplicate_key"),
    ("literal", "literal", UncertaintyQuoteStatus.LITERAL, 0, "duplicate_key"),
    ("none", "rejected", UncertaintyQuoteStatus.REJECTED, 1, "replaced_lower_provenance"),
    ("rejected", "none", UncertaintyQuoteStatus.REJECTED, 0, "duplicate_key"),
    ("none", "none", UncertaintyQuoteStatus.NOT_PROVIDED, 0, "duplicate_key"),
    ("unverified", "none", UncertaintyQuoteStatus.NOT_PROVIDED, 1, "replaced_lower_provenance"),
    ("none", "unverified", UncertaintyQuoteStatus.NOT_PROVIDED, 0, "duplicate_key"),
]


@pytest.mark.parametrize(("first", "second", "kept", "chunk", "reason"), _OVERLAP_CASES)
async def test_la_mateixa_incertesa_en_el_solapament_conserva_la_millor_procedencia(
    db, canary, first, second, kept, chunk, reason
):
    run = await _run(db, canary, _overlap_responses(canary, first, second))
    page6 = [u for u in await _rows(db, RequirementUncertainty, run.id) if u.page_number == 6]
    assert [(u.quote_status, u.source_chunk_index) for u in page6] == [(kept, chunk)]
    if kept == UncertaintyQuoteStatus.LITERAL:
        page = await db.get(TenderDocumentPage, page6[0].page_id)
        assert page.text[page6[0].char_start : page6[0].char_end] == page6[0].quote
    else:
        assert page6[0].diagnosis is None or page6[0].quote is None
    assert _subset(
        {"item": "uncertainty", "index": 0, "stage": "dedup", "reason": reason},
        run.chunk_coverage["chunks"][1]["rejections"],
    )


@pytest.mark.parametrize(("first", "second", "kept", "chunk", "reason"), _OVERLAP_CASES)
async def test_el_resultat_no_depen_de_l_ordre_dels_chunks(
    db, canary, first, second, kept, chunk, reason
):
    """Run sencer (chunk 0 i despres 1) = run que nomes fa el chunk 1 + represa que fa el
    chunk 0 DESPRES: el mateix conjunt de files, sense duplicats."""
    responses = _overlap_responses(canary, first, second)
    full = await _run(db, canary, responses, force=True)
    source = await _run(db, canary, responses[1:], only_chunks=frozenset({1}), force=True)
    assert source.status == AnalysisRunStatus.PARTIAL
    reversed_order = await _run(db, canary, responses[:1], resume_from_run_id=source.id)
    assert reversed_order.status == AnalysisRunStatus.COMPLETED
    assert _shape(await _rows(db, RequirementUncertainty, reversed_order.id)) == _shape(
        await _rows(db, RequirementUncertainty, full.id)
    )
    # L'origen de la represa no es toca mai.
    assert len(await _rows(db, RequirementUncertainty, source.id)) == 1


async def test_dues_cites_literals_diferents_amb_la_mateixa_nota_no_es_fusionen(db, canary):
    responses = ideal_responses(canary)
    responses[0]["uncertainties"] += [
        _p6(canary),
        _p6(canary, quote=_quote(canary, 6, "farciment")),
    ]
    run = await _run(db, canary, responses)
    page6 = [u for u in await _rows(db, RequirementUncertainty, run.id) if u.page_number == 6]
    assert len(page6) == 2
    assert {u.quote_status for u in page6} == {UncertaintyQuoteStatus.LITERAL}


async def test_la_mateixa_nota_en_pagines_o_documents_diferents_no_col_lisiona(db, canary):
    responses = ideal_responses(canary)
    note = "No es concreta."
    responses[0]["uncertainties"] = [
        {**_uncertainty(canary, 5, "", note=note), "quote": None},
        {**_uncertainty(canary, 6, "", note=note), "quote": None},
        {**_uncertainty(canary, 5, "", note=note, scope="cost"), "quote": None},
    ]
    run = await _run(db, canary, responses)
    rows = await _rows(db, RequirementUncertainty, run.id)
    assert sorted((u.page_number, str(u.scope)) for u in rows) == [
        (5, "cost"),
        (5, "solvency"),
        (6, "solvency"),
    ]
    doc_a, doc_b = str(uuid.uuid4()), str(uuid.uuid4())
    assert uncertainty_identity(doc_a, 5, "cost", note=note, literal_quote=None) != (
        uncertainty_identity(doc_b, 5, "cost", note=note, literal_quote=None)
    )


async def test_la_identitat_separa_cita_i_nota_i_normalitza_nomes_el_format():
    doc = str(uuid.uuid4())
    group, note_key = uncertainty_identity(
        doc, 5, "cost", note="Es determinara.", literal_quote=None
    )
    same_group, quote_key = uncertainty_identity(
        doc, 5, "cost", note="  ES   determinara. ", literal_quote="Es determinara."
    )
    assert group == same_group  # format de la nota: la mateixa incertesa
    assert note_key != quote_key  # una cita literal i la nota no comparteixen clau
    other_quote = uncertainty_identity(
        doc, 5, "cost", note="Es determinara.", literal_quote="Altre"
    )
    assert other_quote[0] == group and other_quote[1] != quote_key


async def test_la_tria_es_una_funcio_del_conjunt_de_deteccions_i_no_de_l_ordre():
    """Totes les permutacions d'un grup amb sense cita, rebutjada, no verificada i dues
    cites literals diferents (una repetida) acaben en les mateixes files."""
    import itertools

    doc, extraction, page_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    note = "El maxim depen del preu."

    def candidate(chunk, status, *, quote=None, start=None, diagnosis=None):
        return dict(
            run_id=uuid.uuid4(), tender_id=uuid.uuid4(), source_chunk_index=chunk,
            scope=UncertaintyScope.COST, note=note, extraction_id=extraction,
            tender_document_id=doc, page_number=6,
            page_id=page_id if quote else None, quote=quote,
            char_start=start, char_end=None if start is None else start + len(quote),
            quote_status=status, diagnosis=diagnosis,
        )  # fmt: skip

    detections = [
        candidate(0, UncertaintyQuoteStatus.NOT_PROVIDED),
        candidate(1, UncertaintyQuoteStatus.REJECTED, diagnosis="quote_not_found:absent"),
        candidate(0, UncertaintyQuoteStatus.NOT_PROVIDED, diagnosis="extraction_mismatch"),
        candidate(1, UncertaintyQuoteStatus.LITERAL, quote="Penalitat A.", start=10),
        candidate(0, UncertaintyQuoteStatus.LITERAL, quote="Penalitat A.", start=10),
        candidate(1, UncertaintyQuoteStatus.LITERAL, quote="Penalitat B.", start=40),
    ]

    class _Session:
        def __init__(self):
            self.added = []

        def add(self, row):
            self.added.append(row)

    def final(order):
        session = _Session()
        ledger = UncertaintyLedger(session)
        for values in order:
            ledger.offer(RequirementUncertainty(**values))
        return sorted(
            (u.quote, u.source_chunk_index, str(u.quote_status), u.dedup_key) for u in session.added
        )

    results = {tuple(final(order)) for order in itertools.permutations(detections)}
    assert len(results) == 1
    (only,) = results
    assert [(quote, chunk) for quote, chunk, *_ in only] == [
        ("Penalitat A.", 0),
        ("Penalitat B.", 1),
    ]


async def test_una_represa_deduplica_contra_les_incerteses_copiades(db, canary):
    responses = ideal_responses(canary)
    responses[0]["uncertainties"].append(_p6(canary))
    responses[1]["uncertainties"] = [_p6(canary, quote=None)]
    source = await _run(db, canary, responses[:1], only_chunks=frozenset({0}))
    assert source.status == AnalysisRunStatus.PARTIAL
    resumed = await _run(db, canary, responses[1:], resume_from_run_id=source.id)
    assert resumed.status == AnalysisRunStatus.COMPLETED
    rows = await _rows(db, RequirementUncertainty, resumed.id)
    assert sorted((u.page_number, u.quote_status) for u in rows) == [
        (5, UncertaintyQuoteStatus.LITERAL),
        (6, UncertaintyQuoteStatus.LITERAL),
    ]
    assert {"item": "uncertainty", "index": 0, "stage": "dedup", "reason": "duplicate_key"} in (
        resumed.chunk_coverage["chunks"][1]["rejections"]
    )
    assert len(await _rows(db, RequirementUncertainty, source.id)) == 2


# --------------------------------------------------------------------------- #
# Prompt, perfils i separacio                                                   #
# --------------------------------------------------------------------------- #


async def test_el_prompt_product_v2_no_hereta_les_linies_de_v1_que_el_contradiuen():
    omit = "Si no pots citar literalment on ho diu, NO incloguis l'element."
    low = "Afegeix-ho tambe a `uncertainties`."
    assert omit in V1.system_prompt and low in V1.system_prompt  # V1 intacte
    assert omit not in PRODUCT_V2.system_prompt and low not in PRODUCT_V2.system_prompt
    assert "500 caracters" in PRODUCT_V2.system_prompt
    for scope in UncertaintyScope:
        assert f"`{scope.value}`" in PRODUCT_V2.system_prompt


async def test_cap_altre_perfil_crea_incerteses():
    from app.ai.profiles import INTERNAL_PROFILES

    others = [p for p in (*PROFILES.values(), *INTERNAL_PROFILES.values()) if p is not PRODUCT_V2]
    assert {p.name for p in others} == {"v1", "v2", "v3"}
    assert not any(p.material_uncertainties for p in others)


async def test_cap_modul_d_analisi_compliment_decisio_o_paquet_llegeix_les_incerteses():
    """Separacio estructural: nomes el model, l'extraccio i la classificacio de taules
    coneixen la taula. Cap recompte de requisits, criteris, bloquejadors, forats,
    compliment ni GO/NO-GO la pot llegir sense passar per aqui."""
    app_root = Path(__file__).resolve().parents[1] / "app"
    users = {
        path.relative_to(app_root).as_posix()
        for path in app_root.rglob("*.py")
        if "RequirementUncertainty" in (text := path.read_text(encoding="utf-8"))
        or '"requirement_uncertainties"' in text
    }
    assert users == {
        "models/__init__.py",
        "models/requirements.py",
        "services/requirement_analysis.py",
        "tenant_ops.py",
    }


async def test_una_pagina_desmesurada_es_rebuig_d_element_i_no_fa_caure_el_run(db, canary):
    responses = ideal_responses(canary)
    responses[0]["uncertainties"].append(
        {**_uncertainty(canary, 5, ""), "quote": None, "page_number": 2**63, "note": "Enorme"}
    )
    run = await _run(db, canary, responses)
    assert run.status == AnalysisRunStatus.COMPLETED and run.requirements_extracted == 5
    assert [u.page_number for u in await _rows(db, RequirementUncertainty, run.id)] == [5]
    assert _subset(
        {"item": "uncertainty", "index": 1, "stage": "schema", "field": "page_number"},
        run.chunk_coverage["chunks"][0]["rejections"],
    )


async def test_una_procedencia_no_verificada_cedeix_davant_de_la_verificada(db, canary):
    responses = ideal_responses(canary)
    responses[0]["uncertainties"].append(_p6(canary, extraction_id=str(uuid.uuid4())))
    responses[1]["uncertainties"] = [_p6(canary), _p6(canary, extraction_id=str(uuid.uuid4()))]
    run = await _run(db, canary, responses)
    page6 = [
        (u.source_chunk_index, u.quote_status, u.diagnosis)
        for u in await _rows(db, RequirementUncertainty, run.id)
        if u.page_number == 6
    ]
    # La verificada del chunk 1 substitueix la no verificada del chunk 0 (mateixa fila
    # del run); la repeticio no verificada del chunk 1 es descarta.
    assert page6 == [(1, UncertaintyQuoteStatus.LITERAL, None)]
    rejections = run.chunk_coverage["chunks"][1]["rejections"]
    assert _subset(
        {"item": "uncertainty", "index": 0, "reason": "replaced_lower_provenance",
         "replaced_chunk": 0},
        rejections,
    )  # fmt: skip
    assert {"item": "uncertainty", "index": 1, "stage": "dedup", "reason": "duplicate_key"} in (
        rejections
    )


async def test_una_represa_amb_una_procedencia_no_verificada_es_igual_que_un_run_sencer(db, canary):
    """La represa dedueix de `diagnosis` si la fila copiada era verificada: ha de donar
    les mateixes files i els mateixos rebutjos que el run sencer."""
    alien = str(uuid.uuid4())

    def responses():
        out = ideal_responses(canary)
        out[0]["uncertainties"].append(_p6(canary, extraction_id=alien))
        out[1]["uncertainties"] = [_p6(canary), _p6(canary, extraction_id=alien)]
        return out

    def shape(rows):
        return sorted(
            (u.page_number, u.source_chunk_index, u.quote_status, u.diagnosis, u.dedup_key)
            for u in rows
        )

    full = await _run(db, canary, responses(), force=True)
    source = await _run(db, canary, responses()[:1], only_chunks=frozenset({0}), force=True)
    resumed = await _run(db, canary, responses()[1:], resume_from_run_id=source.id)
    assert resumed.status == AnalysisRunStatus.COMPLETED
    assert shape(await _rows(db, RequirementUncertainty, resumed.id)) == shape(
        await _rows(db, RequirementUncertainty, full.id)
    )
    assert (
        resumed.chunk_coverage["chunks"][1]["rejections"]
        == full.chunk_coverage["chunks"][1]["rejections"]
    )
