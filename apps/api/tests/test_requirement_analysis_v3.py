"""Extraction Contract v3 dins de la canonada, amb el proveidor fals. Zero crides externes.

La v3 s'executa NOMES si el codi passa `profile=V3`. Aqui es prova:

* que no es el per defecte ni es pot demanar per nom (jobs, pressupostos);
* que la branca v3 escriu `claim_support_json`, `adjudication_basis_json` i
  `value_schema = requirements-claims-v3`, sense cap text del plec;
* que la v1 continua exactament igual (columnes noves a NULL, codis v1).
"""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa

from app.ai import profiles
from app.ai.contract_v3 import VALUE_SCHEMA_V3
from app.ai.prompts.requirements_v3 import SYSTEM_PROMPT_V3
from app.ai.providers import fake
from app.ai.schemas_v3 import SCHEMA_VERSION_V3
from app.models.enums import AnalysisRunStatus, ReviewStatus
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
)
from app.services.requirement_analysis import analyze_tender_requirements
from tests import test_requirement_analysis as base
from tests.test_requirement_analysis import (
    PAGE_27,
    PAGE_28,
    QUOTE_27,
    QUOTE_28,
    requirement_payload,
    tender_of,
)

# Els mateixos fixtures que la v1: el mateix plec, el mateix proveidor fals.
analyzable = base.analyzable
_clean_provider = base._clean_provider

V3 = profiles.V3


def _evidence(extraction_id, *, eid="e1", page=27, quote=QUOTE_27, supports=()):
    return {
        "id": eid,
        "extraction_id": str(extraction_id),
        "page_number": page,
        "quote": quote,
        "supports": list(supports),
    }


def _requirement(extraction_id, **overrides):
    payload = {
        "category": "economic_solvency",
        "obligation": "mandatory",
        "modality": "mandatory",
        "operator": "gte",
        "scope": "tender",
        "title": "Volum anual de negoci minim",
        "claims": [
            {"id": "volum_minim", "kind": "amount", "value": 450000, "unit": "EUR",
             "anchor": "volumen anual de negocios"},
        ],
        "claims_complete": "complete",
        "confidence": "high",
        "evidence": [_evidence(extraction_id, supports=["volum_minim"])],
    }
    payload.update(overrides)
    return payload


def _candidate(extraction_id, **overrides):
    payload = {
        "name": "Oferta economica",
        "kind": "award_criterion",
        "adjudication_basis": ["points"],
        "points": 45,
        "evidence": [_evidence(extraction_id, page=28, quote=QUOTE_28, supports=["points"])],
    }
    payload.update(overrides)
    return payload


async def _run_v3(session, analyzable, response):
    fake.queue_response(response)
    tender = await tender_of(session, analyzable["document"])
    return (await analyze_tender_requirements(session, tender, profile=V3)).run


# --------------------------------------------------------------------------- #
# Perfil                                                                       #
# --------------------------------------------------------------------------- #


def test_v3_no_es_el_per_defecte_ni_es_pot_demanar_per_nom():
    assert profiles.DEFAULT_PROFILE is profiles.V1
    assert "v3" not in profiles.PROFILES
    assert profiles.INTERNAL_PROFILES == {"v3": V3}
    assert profiles.BENCHMARK_PROFILE is profiles.V2


def test_el_perfil_v3_porta_el_seu_contracte():
    assert V3.schema_version == SCHEMA_VERSION_V3 == "requirements-schema-v3"
    assert V3.prompt_version == "requirements-v3"
    assert V3.extractor_version == "tender-requirements-v3"
    assert V3.claims_contract is True
    assert not profiles.V1.claims_contract and not profiles.V2.claims_contract
    assert profiles.profile_for_run("tender-requirements-v3") is V3


def test_els_jobs_no_poden_executar_v3():
    """Els handlers resolen el nom a `PROFILES`: `v3` hi es desconegut."""
    from app.jobs import handlers, queue, quotes

    for module in (handlers, queue, quotes):
        assert module.PROFILES is profiles.PROFILES
    assert profiles.PROFILES.get("v3") is None


def test_el_prompt_v3_ensenya_el_contracte():
    for concept in (
        "claims", "anchor", "supports", "claims_complete", "partial", "unknown",
        "award_candidates", "award_criterion", "desirable_feature", "informative",
        "adjudication_basis", "points", "weight", "formula", "criteria_table",
        "offer_form", "criteria_section", "uncertainties", "UNTRUSTED_DOCUMENT_CONTENT",
    ):
        assert concept in SYSTEM_PROMPT_V3, concept
    assert "value: null" in SYSTEM_PROMPT_V3


async def test_la_crida_v3_envia_el_prompt_i_l_esquema_v3(session, analyzable):
    fake.SENT_PAYLOADS.clear()
    await _run_v3(session, analyzable, {"requirements": []})

    [sent] = fake.SENT_PAYLOADS
    assert sent["system"] == SYSTEM_PROMPT_V3
    assert "award_candidates" in sent["schema"]["properties"]
    assert "award_criteria" not in sent["schema"]["properties"]


# --------------------------------------------------------------------------- #
# Requisits                                                                    #
# --------------------------------------------------------------------------- #


async def test_un_requisit_sostingut_escriu_el_suport_i_el_value_schema(session, analyzable):
    extraction_id = analyzable["extraction"].id
    run = await _run_v3(session, analyzable, {"requirements": [_requirement(extraction_id)]})

    assert run.status == AnalysisRunStatus.COMPLETED
    assert run.schema_version == "requirements-schema-v3"
    assert run.requirements_extracted == 1

    requirement = await session.scalar(sa.select(Requirement))
    assert requirement.value_schema == VALUE_SCHEMA_V3
    assert requirement.value_json == {
        "claims": [{"id": "volum_minim", "kind": "amount", "value": 450000, "unit": "EUR",
                    "anchor": "volumen anual de negocios", "certainty": "stated"}],
        "claims_complete": "complete",
    }
    assert requirement.review_status == ReviewStatus.UNREVIEWED

    [cite] = (await session.scalars(sa.select(RequirementEvidence))).all()
    assert cite.claim_support_json == {
        "contract": "claim-support-v1", "ref": "e1", "claims": {"volum_minim": "supported"},
    }
    assert PAGE_27[cite.char_start:cite.char_end] == QUOTE_27


async def test_un_claim_no_sostingut_no_es_descarta_i_va_a_revisio(session, analyzable):
    """El cas FTTH: el valor hi era, la cita no el sostenia."""
    extraction_id = analyzable["extraction"].id
    claims = [
        {"id": "volum_minim", "kind": "amount", "value": 450000, "unit": "EUR",
         "anchor": "volumen anual de negocios"},
        {"id": "exercicis", "kind": "count", "value": 3, "anchor": "ejercicios"},
    ]
    run = await _run_v3(session, analyzable, {"requirements": [_requirement(
        extraction_id, claims=claims,
        evidence=[_evidence(extraction_id, supports=["volum_minim", "exercicis"])],
    )]})

    requirement = await session.scalar(sa.select(Requirement))
    assert requirement.review_status == ReviewStatus.NEEDS_REVIEW
    assert [c["id"] for c in requirement.value_json["claims"]] == ["volum_minim", "exercicis"]
    [cite] = requirement.evidence
    assert cite.claim_support_json["claims"] == {
        "volum_minim": "supported", "exercicis": "value_not_in_quote",
    }
    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["rejections"] == [
        {"item": "requirement", "index": 0, "stage": "claim", "reason": "claim_not_supported",
         "claim": 1, "detail": "value_not_in_quote"},
    ]
    assert run.requirements_rejected == 0, "un claim no sostingut no rebutja el requisit"


async def test_claims_parcials_van_a_revisio(session, analyzable):
    extraction_id = analyzable["extraction"].id
    await _run_v3(session, analyzable, {"requirements": [
        _requirement(extraction_id, claims_complete="partial")
    ]})

    requirement = await session.scalar(sa.select(Requirement))
    assert requirement.review_status == ReviewStatus.NEEDS_REVIEW


async def test_rebutjos_v3_amb_el_vocabulari_v3(session, analyzable):
    extraction_id = analyzable["extraction"].id
    secret = "Frase inventada que no surt al plec 999.999 euros"
    run = await _run_v3(session, analyzable, {"requirements": [
        _requirement(extraction_id),  # 0: bo
        _requirement(extraction_id, title="x", inventat=secret),  # 1: esquema
        _requirement(extraction_id, title="Inventat",
                     evidence=[_evidence(extraction_id, quote=secret)]),  # 2
        _requirement(extraction_id, title="Fora",
                     evidence=[_evidence(extraction_id, page=99)]),  # 3
        _requirement(extraction_id, title="Altre doc",
                     evidence=[_evidence(uuid.uuid4())]),  # 4
        _requirement(extraction_id),  # 5: duplicat exacte del 0
    ]})

    [chunk] = run.chunk_coverage["chunks"]
    # Els index son els de la RESPOSTA: descartar l'1 per esquema no els mou.
    assert chunk["rejections"] == [
        {"item": "requirement", "index": 1, "stage": "schema", "reason": "schema_invalid",
         "detail": "extra_forbidden"},
        {"item": "requirement", "index": 2, "stage": "evidence",
         "reason": "evidence_not_found"},
        {"item": "requirement", "index": 2, "stage": "item", "reason": "no_valid_evidence"},
        {"item": "requirement", "index": 3, "stage": "evidence", "reason": "page_not_in_chunk"},
        {"item": "requirement", "index": 3, "stage": "item", "reason": "no_valid_evidence"},
        {"item": "requirement", "index": 4, "stage": "evidence",
         "reason": "extraction_mismatch"},
        {"item": "requirement", "index": 4, "stage": "item", "reason": "no_valid_evidence"},
        {"item": "requirement", "index": 5, "stage": "dedup", "reason": "duplicate_key"},
    ]
    assert run.requirements_extracted == 1
    assert run.requirements_rejected == 4  # 1 d'esquema + 3 sense cita valida
    assert secret not in json.dumps(run.chunk_coverage, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Criteris                                                                     #
# --------------------------------------------------------------------------- #


async def test_candidats_classificats_sense_descartar_cap(session, analyzable):
    extraction_id = analyzable["extraction"].id
    memoria = "Memoria tecnica: hasta 35 puntos"
    run = await _run_v3(session, analyzable, {"award_candidates": [
        _candidate(extraction_id),  # formal: punts citats
        _candidate(extraction_id, name="Memoria", adjudication_basis=[], points=None,
                   evidence=[_evidence(extraction_id, page=28, quote=memoria)]),
        _candidate(extraction_id, name="Capcalera", kind="informative", adjudication_basis=[],
                   points=None,
                   evidence=[_evidence(extraction_id, page=28, quote="Criterios de adjudicacion")]),
    ]})

    # Es desen totes tres; el comptador del run nomes compta criteris formals.
    assert run.award_criteria_extracted == 1
    assert await session.scalar(sa.select(sa.func.count()).select_from(AwardCriterion)) == 3
    rows = {
        c.name: c
        for c in (await session.scalars(sa.select(AwardCriterion))).all()
    }
    assert rows["Oferta economica"].adjudication_basis_json["award_class"] == (
        "formal_award_criterion"
    )
    assert rows["Oferta economica"].review_status == ReviewStatus.UNREVIEWED
    assert rows["Memoria"].adjudication_basis_json["award_class"] == "candidate_award_criterion"
    assert rows["Memoria"].adjudication_basis_json["reasons"] == ["no_mechanism_declared"]
    assert rows["Memoria"].review_status == ReviewStatus.NEEDS_REVIEW
    assert rows["Capcalera"].adjudication_basis_json["award_class"] == "informative"


async def test_la_base_d_adjudicacio_cita_la_fila_d_evidencia(session, analyzable):
    extraction_id = analyzable["extraction"].id
    await _run_v3(session, analyzable, {"award_candidates": [_candidate(extraction_id)]})

    criterion = await session.scalar(sa.select(AwardCriterion))
    [evidence] = (await session.scalars(sa.select(AwardCriterionEvidence))).all()
    assert criterion.adjudication_basis_json == {
        "contract": "adjudication-basis-v1",
        "declared_kind": "award_criterion",
        "award_class": "formal_award_criterion",
        "reasons": ["mechanism_verified"],
        "mechanisms": [
            {"kind": "points", "check": "verified", "evidence_ids": [str(evidence.id)]},
        ],
    }


async def test_un_mecanisme_no_provat_queda_registrat(session, analyzable):
    extraction_id = analyzable["extraction"].id
    run = await _run_v3(session, analyzable, {"award_candidates": [
        _candidate(extraction_id, points=80),  # la cita diu 45
    ]})

    criterion = await session.scalar(sa.select(AwardCriterion))
    assert criterion.adjudication_basis_json["award_class"] == "candidate_award_criterion"
    assert criterion.review_status == ReviewStatus.NEEDS_REVIEW
    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["rejections"] == [
        {"item": "award_candidate", "index": 0, "stage": "claim",
         "reason": "claim_not_supported", "detail": "not_in_quote"},
    ]


# --------------------------------------------------------------------------- #
# Sense text i aillament                                                       #
# --------------------------------------------------------------------------- #


async def test_els_camps_de_suport_no_porten_text_del_plec(session, analyzable):
    extraction_id = analyzable["extraction"].id
    await _run_v3(session, analyzable, {
        "requirements": [_requirement(extraction_id)],
        "award_candidates": [_candidate(extraction_id)],
    })

    stored = [
        cite.claim_support_json
        for cite in (await session.scalars(sa.select(RequirementEvidence))).all()
    ] + [c.adjudication_basis_json for c in (await session.scalars(sa.select(AwardCriterion)))]
    dumped = json.dumps(stored, ensure_ascii=False).casefold()
    for fragment in (QUOTE_27, QUOTE_28, "volumen", "oferta economica", "450.000", "puntos"):
        assert fragment.casefold() not in dumped, fragment
    for page in (PAGE_27, PAGE_28):
        for line in filter(None, page.splitlines()):
            assert line.casefold() not in dumped


async def test_la_v1_no_escriu_res_de_la_v3(session, analyzable):
    extraction_id = analyzable["extraction"].id
    fake.queue_response({
        "requirements": [requirement_payload(extraction_id)],
        "award_criteria": [{
            "name": "Oferta economica", "points": 45,
            "evidence": [{"extraction_id": str(extraction_id), "page_number": 28,
                          "quote": QUOTE_28}],
        }],
    })
    tender = await tender_of(session, analyzable["document"])
    run = (await analyze_tender_requirements(session, tender)).run

    assert run.schema_version == "requirements-schema-v1"
    requirement = await session.scalar(sa.select(Requirement))
    assert requirement.value_schema is None
    assert requirement.value_json["amount"] == 450000
    assert all(c.claim_support_json is None for c in requirement.evidence)
    criterion = await session.scalar(sa.select(AwardCriterion))
    assert criterion.adjudication_basis_json is None


async def test_un_run_v3_no_reaprofita_un_run_v1(session, analyzable):
    extraction_id = analyzable["extraction"].id
    fake.queue_response({"requirements": [requirement_payload(extraction_id)]})
    tender = await tender_of(session, analyzable["document"])
    first = await analyze_tender_requirements(session, tender)

    second = await _run_v3(session, analyzable, {"requirements": [_requirement(extraction_id)]})

    assert second.id != first.run.id
    assert second.schema_version == "requirements-schema-v3"


async def test_la_represa_v3_reapunta_la_base_a_les_cites_copiades(
    session, analyzable, monkeypatch
):
    """Un criteri v3 copiat per represa no pot citar les files del run d'origen."""
    from app.ai.provider import SchemaInvalidError, Usage
    from app.core.config import settings

    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 1)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    extraction_id = analyzable["extraction"].id
    tender = await tender_of(session, analyzable["document"])

    fake.queue_response(SchemaInvalidError("x", usage=Usage(input_tokens=1, output_tokens=1)))
    fake.queue_response({"award_candidates": [_candidate(extraction_id)]})
    source = (
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider(), profile=V3
        )
    ).run
    assert (source.status, source.chunks_completed) == (AnalysisRunStatus.PARTIAL, 1)

    fake.queue_response({"requirements": [_requirement(extraction_id)]})
    resumed = (
        await analyze_tender_requirements(
            session, tender, provider=fake.FakeProvider(), profile=V3,
            resume_from_run_id=source.id,
        )
    ).run

    assert resumed.id != source.id
    rows = {}
    for run in (source, resumed):
        criterion = await session.scalar(
            sa.select(AwardCriterion).where(AwardCriterion.run_id == run.id)
        )
        [evidence] = (
            await session.scalars(
                sa.select(AwardCriterionEvidence).where(
                    AwardCriterionEvidence.award_criterion_id == criterion.id
                )
            )
        ).all()
        [mechanism] = criterion.adjudication_basis_json["mechanisms"]
        rows[run.id] = (mechanism["evidence_ids"], str(evidence.id))
    for evidence_ids, own_evidence in rows.values():
        assert evidence_ids == [own_evidence]
    assert rows[source.id][1] != rows[resumed.id][1]


# --------------------------------------------------------------------------- #
# Lectors: cap candidat no pot passar per criteri formal                       #
# --------------------------------------------------------------------------- #


def _three_candidates(extraction_id):
    return {"award_candidates": [
        _candidate(extraction_id),  # formal
        _candidate(extraction_id, name="Memoria", adjudication_basis=[], points=None,
                   evidence=[_evidence(extraction_id, page=28,
                                       quote="Memoria tecnica: hasta 35 puntos")]),
        _candidate(extraction_id, name="Eficiencia", kind="desirable_feature",
                   adjudication_basis=[], points=None,
                   evidence=[_evidence(extraction_id, page=28,
                                       quote="Memoria tecnica: hasta 35 puntos")]),
        _candidate(extraction_id, name="Capcalera", kind="informative", adjudication_basis=[],
                   points=None,
                   evidence=[_evidence(extraction_id, page=28, quote="Criterios de adjudicacion")]),
    ]}


async def test_l_api_nomes_ensenya_criteris_formals_com_a_criteris(
    session, analyzable, client_a
):
    extraction_id = analyzable["extraction"].id
    await _run_v3(session, analyzable, _three_candidates(extraction_id))
    tender_id = analyzable["document"].tender_id

    body = (await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/requirements")).json()

    assert [c["name"] for c in body["award_criteria"]] == ["Oferta economica"]
    assert body["award_criteria"][0]["award_class"] == "formal_award_criterion"
    assert {c["name"]: c["award_class"] for c in body["award_candidates"]} == {
        "Memoria": "candidate_award_criterion",
        "Eficiencia": "technical_desirable",
        "Capcalera": "informative",
    }
    assert body["run"]["award_criteria_extracted"] == 1

    candidate_id = next(c["id"] for c in body["award_candidates"] if c["name"] == "Capcalera")
    detail = (await client_a.get(f"/api/v1/award-criteria/{candidate_id}")).json()
    assert detail["award_class"] == "informative"


async def test_l_api_v1_no_canvia(session, analyzable, client_a):
    extraction_id = analyzable["extraction"].id
    fake.queue_response({"award_criteria": [{
        "name": "Oferta economica", "points": 45,
        "evidence": [{"extraction_id": str(extraction_id), "page_number": 28,
                      "quote": QUOTE_28}],
    }]})
    tender = await tender_of(session, analyzable["document"])
    await analyze_tender_requirements(session, tender)

    body = (
        await client_a.get(f"/api/v1/tenders/{tender.id}/analysis/requirements")
    ).json()

    assert [c["name"] for c in body["award_criteria"]] == ["Oferta economica"]
    assert body["award_criteria"][0]["award_class"] is None
    assert body["award_candidates"] == []


async def test_el_benchmark_no_compta_candidats_com_a_prediccions(session, analyzable):
    from app.evaluation.runner import load_predictions

    extraction_id = analyzable["extraction"].id
    run = await _run_v3(session, analyzable, _three_candidates(extraction_id))

    _, awards = await load_predictions(session, run)

    assert [a.name for a in awards] == ["Oferta economica"]


async def test_el_motor_de_compliment_no_avalua_un_valor_v3(session, analyzable):
    """Un llindar v3 no el llegeix cap regla: revisio, mai un veredicte."""
    from app.compliance.engine import assess_requirement
    from app.compliance.profile import ProfileSnapshot
    from app.models.enums import AssessmentResult

    extraction_id = analyzable["extraction"].id
    await _run_v3(session, analyzable, {"requirements": [_requirement(extraction_id)]})
    requirement = await session.scalar(sa.select(Requirement))

    outcome = assess_requirement(requirement, ProfileSnapshot(company_id=uuid.uuid4()))

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.params["value_schema"] == VALUE_SCHEMA_V3


# --------------------------------------------------------------------------- #
# Deduplicacio de candidats                                                    #
# --------------------------------------------------------------------------- #


async def test_un_criteri_i_un_text_informatiu_amb_el_mateix_nom_no_es_fusionen(
    session, analyzable
):
    extraction_id = analyzable["extraction"].id
    same = {"name": "Criterios de adjudicacion", "adjudication_basis": [], "points": None,
            "evidence": [_evidence(extraction_id, page=28, quote="Criterios de adjudicacion")]}
    run = await _run_v3(session, analyzable, {"award_candidates": [
        _candidate(extraction_id, kind="informative", **same),
        _candidate(extraction_id, kind="award_criterion", **same),
    ]})

    kinds = sorted(
        c.adjudication_basis_json["declared_kind"]
        for c in (await session.scalars(sa.select(AwardCriterion))).all()
    )
    assert kinds == ["award_criterion", "informative"]
    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["rejections"] is None or all(
        r["reason"] != "duplicate_key" for r in chunk["rejections"]
    )


async def test_el_mateix_candidat_repetit_si_que_es_fusiona(session, analyzable):
    extraction_id = analyzable["extraction"].id
    run = await _run_v3(session, analyzable, {"award_candidates": [
        _candidate(extraction_id), _candidate(extraction_id),
    ]})

    assert await session.scalar(sa.select(sa.func.count()).select_from(AwardCriterion)) == 1
    [chunk] = run.chunk_coverage["chunks"]
    assert chunk["rejections"] == [
        {"item": "award_candidate", "index": 1, "stage": "dedup", "reason": "duplicate_key"},
    ]


def test_la_clau_v1_v2_de_criteri_no_canvia():
    """Cap run antic canvia de clau: la v3 afegeix el `kind` per damunt."""
    import hashlib
    from types import SimpleNamespace

    from app.services.requirement_analysis import award_dedup_key, award_dedup_key_v3

    item = SimpleNamespace(name="Preu  Oferta", points=45.0, weight_percent=None,
                           evaluation_type="automatic", lot_labels=["Lot 1"])
    # La recepta de sempre, reescrita aqui: si algu toca la clau v1/v2, falla.
    recipe = "␟".join(["preu oferta", "45.0", "", "automatic", "lot 1"])
    assert award_dedup_key(item) == hashlib.sha256(recipe.encode("utf-8")).hexdigest()[:64]

    formal = award_dedup_key_v3(SimpleNamespace(**vars(item), kind="award_criterion"))
    informative = award_dedup_key_v3(SimpleNamespace(**vars(item), kind="informative"))
    assert formal != informative
    assert award_dedup_key(item) not in (formal, informative)
