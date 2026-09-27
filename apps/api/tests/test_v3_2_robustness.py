"""Extraction Contract v3.2: sobre robust i classificacio per evidencia. Zero crides.

El que el canari v3.1 de Palamos (run `4087ac26`) va fer veure:

* **Sobre.** Una clau d'arrel inventada va fer caure el chunk 15 sencer abans de
  cap recuperacio element a element. Ara es descarta amb un rebuig; les llistes
  d'extraccio inservibles continuen fent caure el chunk.
* **Metadades auxiliars.** `document_notes` i `uncertainties` es validen element
  a element i mai no s'enduen requisits ni criteris.
* **Mecanismes.** La fila d'una taula de puntuacio ("... 2" sota "DESCRIPCIO
  PUNTS") i la instruccio "assenyalar amb una X ... SI/NO" son mecanismes, provats
  literalment i sense semblanca.

Els textos de pagina son SINTETICS amb la FORMA de les linies del PCAP de
Palamos (p15, p27): salts de linia del PDF, cometes tipografiques, files amb la
puntuacio al final. Les classes esperades surten de l'evidencia de cada cas, no
del nom del criteri: cap test no exigeix "les 8 files son formals".
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.ai import profiles
from app.ai.contract_v3 import (
    TABLE_HEADER_CONTEXT,
    AwardClass,
    AwardReason,
    MechanismCheck,
    adjudication_basis_json,
    check_mechanism,
    classify_award,
    parse_chunk_v3_result,
)
from app.ai.providers import fake
from app.ai.schemas_v3 import AdjudicationBasis, AwardCandidateOutV3, EvidenceOutV3
from app.core.config import settings
from app.models.enums import ReviewStatus, TenderDocumentType
from app.models.requirements import (
    AwardCriterion,
    Requirement,
    RequirementExtractionRun,
)
from app.models.tenders import Tender
from tests import test_v3_tender_probe as probe_tests

probe = probe_tests.probe
palamos_like = probe_tests.palamos_like
one_page_chunks = probe_tests.one_page_chunks
_clean_provider = probe_tests._clean_provider
_no_persistent_windows_env = probe_tests._no_persistent_windows_env


# --------------------------------------------------------------------------- #
# Forma de les pagines (sintetica)                                             #
# --------------------------------------------------------------------------- #

SCORE_TABLE = (
    "2. Que la puntuació que li correspongui pels criteris qualitatius sigui superior al\n"
    "90% de la puntuació total establerta en aquest plec.\n"
    "DESCRIPCIÓ PUNTS\n"
    "Ampliació bonus tarifaris de trànsit de fixos\n"
    "15.000 minuts a destinacions fixes nacionals 2\n"
    "Serveis de comunicacions de dades i accés a Internet\n"
    "Accés a Internet descentralitzat xDSL/GPON 1Gbps simètric als equipaments\n"
    "municipals\n"
    "5\n"
    "Serveis de quota de dades mòbils\n"
    "Tarifa 1 a 20 GB de dades 4\n"
    "Tarifa 2 a dades il·limitades 3\n"
)
ROW_FIXES = "15.000 minuts a destinacions fixes nacionals 2"
ROW_WRAPPED = (
    "Accés a Internet descentralitzat xDSL/GPON 1Gbps simètric als equipaments\n"
    "municipals\n5"
)
ROW_TARIFA_1 = "Tarifa 1 a 20 GB de dades 4"
TWO_ROWS = "Tarifa 1 a 20 GB de dades 4\nTarifa 2 a dades il·limitades 3"

QUANTITY_TABLE = (
    "DESCRIPCIÓ UNITATS\n"
    "Accés a Internet GPON a 600 Mbps simètrics pels equipaments municipals 26\n"
    "Adreçament IP (1 adreça) 26\n"
    "Trucades a numeracions 800/900 350\n"
)
ROW_GPON = "Accés a Internet GPON a 600 Mbps simètrics pels equipaments municipals 26"

OFFER_FORM = (
    "Així mateix, pel que fa als criteris qualitatius ofereix (cal assenyalar amb una X la opció\n"
    "corresponent “SI” o “NO”):\n"
    "DESCRIPCIÓ OFERTA\n"
    "Tarifa 1 a 20 GB de dades (  ) SI\n"
    "  (  ) NO\n"
    "Comptar amb centre de suport i atenció en català (  ) SI\n"
    "  (  ) NO\n"
)
OFFER_INSTRUCTION = (
    "pel que fa als criteris qualitatius ofereix (cal assenyalar amb una X la opció\n"
    "corresponent “SI” o “NO”):"
)
OFFER_CHECKBOX = "Tarifa 1 a 20 GB de dades (  ) SI"
OFFER_BARE_LABEL = "Comptar amb centre de suport i atenció en català"

DESIRABLE = "Es valorarà que els terminals disposin de pantalla tàctil."


def _candidate(evidence=(), **kwargs) -> AwardCandidateOutV3:
    base = {"name": "Criteri", "kind": "award_criterion"}
    return AwardCandidateOutV3(
        **{**base, **kwargs},
        evidence=[
            EvidenceOutV3(extraction_id="x", page_number=1, quote=q, id=eid, supports=list(s))
            for eid, q, s in evidence
        ],
    )


def _classify(candidate, page=None):
    quotes = {e.id: e.quote for e in candidate.evidence}
    return classify_award(candidate, quotes, {eid: page for eid in quotes} if page else None)


# --------------------------------------------------------------------------- #
# 1. Sobre: la frontera del chunk sencer                                       #
# --------------------------------------------------------------------------- #

REQ = {"category": "other", "title": "ok", "evidence": [
    {"id": "e1", "extraction_id": "x", "page_number": 1, "quote": "q", "supports": []}]}
AWARD = {"name": "Preu", "kind": "award_criterion", "evidence": [
    {"id": "e1", "extraction_id": "x", "page_number": 1, "quote": "q", "supports": []}]}


def _envelope(**overrides):
    return {"requirements": [REQ], "award_candidates": [AWARD], "document_notes": [],
            "uncertainties": [], **overrides}


def test_el_contracte_fa_obligatories_les_dues_llistes_d_extraccio():
    from app.ai.schemas_v3 import ChunkExtractionOutV3, chunk_output_json_schema_v3

    fields = ChunkExtractionOutV3.model_fields
    assert fields["requirements"].is_required()
    assert fields["award_candidates"].is_required()
    assert fields["requirements"].default_factory is None
    assert fields["award_candidates"].default_factory is None
    assert not fields["document_notes"].is_required()
    assert not fields["uncertainties"].is_required()
    schema = chunk_output_json_schema_v3()
    assert sorted(schema["required"]) == ["award_candidates", "requirements"]
    assert schema["additionalProperties"] is False
    for name in ("document_notes", "uncertainties"):
        with pytest.raises(ValidationError):
            ChunkExtractionOutV3.model_validate(
                {"requirements": [], "award_candidates": [], name: None})


INVENTED = {"resum": {"requirements": [REQ], "award_candidates": [AWARD]}}


def _missing(exc: ValidationError) -> set[str]:
    return {e["loc"][0] for e in exc.errors() if e["type"] == "missing"}


def test_a_les_dues_llistes_buides_son_una_extraccio_buida_valida():
    parsed = parse_chunk_v3_result({"requirements": [], "award_candidates": []})

    assert parsed.output.requirements == parsed.output.award_candidates == []
    assert parsed.schema_rejections.entries == []


@pytest.mark.parametrize(("case", "payload", "missing"), [
    ("B", {"requirements": []}, {"award_candidates"}),
    ("C", {"award_candidates": []}, {"requirements"}),
    ("D", {"document_notes": [], "uncertainties": []}, {"requirements", "award_candidates"}),
    ("E", {}, {"requirements", "award_candidates"}),
    ("G", INVENTED, {"requirements", "award_candidates"}),
])
def test_una_llista_d_extraccio_absent_fa_caure_el_chunk(case, payload, missing):
    # Mai no es completa amb []: una resposta sense les dues no es del contracte.
    with pytest.raises(ValidationError) as caught:
        parse_chunk_v3_result(payload)

    assert _missing(caught.value) == missing, case


def test_f_la_clau_inventada_es_descarta_i_l_extraccio_sobreviu():
    parsed = parse_chunk_v3_result({"requirements": [], "award_candidates": [], **INVENTED})

    assert parsed.output.requirements == parsed.output.award_candidates == []
    assert parsed.root_fields_rejected == 1
    assert parsed.schema_rejections.entries == [
        {"item": "root", "index": 0, "stage": "root", "reason": "schema_invalid",
         "detail": "extra_forbidden", "field": "resum"},
    ]


@pytest.mark.parametrize("payload", [
    [], "text", None, 5,
    {"requirements": None, "award_candidates": []},  # null explicit: no es buit
    {"requirements": {}, "award_candidates": []},
    {"requirements": "no-llista", "award_candidates": []},
    {"requirements": [], "award_candidates": None},
    {"requirements": [], "award_candidates": {}},
    {"requirements": [], "award_candidates": "no-llista"},
    {"requirements": [], "award_candidates": 3},
])
def test_un_sobre_inservible_fa_caure_el_chunk(payload):
    with pytest.raises(ValidationError):
        parse_chunk_v3_result(payload)


def test_una_clau_d_arrel_desconeguda_es_descarta_i_el_chunk_sobreviu():
    secret = "Text del plec copiat com a clau!"
    payload = _envelope(**{"resum_model": {"requirements": [REQ] * 5}, secret: [AWARD]})

    parsed = parse_chunk_v3_result(payload)

    assert [r.title for r in parsed.output.requirements] == ["ok"]
    assert [a.name for a in parsed.output.award_candidates] == ["Preu"]
    assert parsed.root_fields_rejected == 2
    root = [e for e in parsed.schema_rejections.entries if e["stage"] == "root"]
    assert root == [
        {"item": "root", "index": 0, "stage": "root", "reason": "schema_invalid",
         "detail": "extra_forbidden", "field": "resum_model"},
        {"item": "root", "index": 0, "stage": "root", "reason": "schema_invalid",
         "detail": "extra_forbidden", "field": f"<text,{len(secret)} chars>"},
    ]
    assert secret not in json.dumps(parsed.schema_rejections.entries)
    assert "resum_model" not in parsed.output.model_dump()


@pytest.mark.parametrize(("field", "item"), [
    ("document_notes", "document_note"), ("uncertainties", "uncertainty"),
])
def test_un_element_auxiliar_mal_format_es_descarta_sol(field, item):
    payload = _envelope(**{field: [{"note": "bona"}, {"note": 3}, "text", {"note": "x", "k": 1}]})

    parsed = parse_chunk_v3_result(payload)

    assert [n.note for n in getattr(parsed.output, field)] == ["bona"]
    assert len(parsed.output.requirements) == len(parsed.output.award_candidates) == 1
    assert parsed.auxiliary_rejected == 3
    entries = [e for e in parsed.schema_rejections.entries if e["stage"] == "auxiliary"]
    assert [(e["item"], e["index"]) for e in entries] == [(item, 1), (item, 2), (item, 3)]
    assert entries[0]["field"] == "note"
    assert "field" not in entries[2], "una clau inventada no es diu mai pel nom"


@pytest.mark.parametrize("field", ["document_notes", "uncertainties"])
@pytest.mark.parametrize("value", ["text", {"note": "x"}, 7, None])
def test_un_camp_auxiliar_inservible_es_descarta_sencer(field, value):
    parsed = parse_chunk_v3_result(_envelope(**{field: value}))

    assert getattr(parsed.output, field) == []
    assert len(parsed.output.requirements) == len(parsed.output.award_candidates) == 1
    assert parsed.auxiliary_rejected == 1
    [entry] = [e for e in parsed.schema_rejections.entries if e["stage"] == "auxiliary"]
    assert (entry["detail"], entry["field"]) == ("list_type", field)


FAILURE_MATRIX = [
    # (cas, payload, el chunk cau?, requisits, criteris)
    ("clau d'arrel extra", _envelope(extra=1), False, 1, 1),
    ("falta la llista d'extraccio", {"uncertainties": []}, True, None, None),
    ("falta award_candidates", {"requirements": [REQ]}, True, None, None),
    ("falta requirements", {"award_candidates": [AWARD]}, True, None, None),
    ("requirements de tipus erroni", _envelope(requirements={"a": 1}), True, None, None),
    ("camp exterior del requisit", _envelope(requirements=[REQ, {**REQ, "title": 5}]),
     False, 1, 1),
    ("claim del requisit", _envelope(requirements=[{**REQ, "claims": [
        {"id": "c1", "kind": "count", "value": "molts"}]}]), False, 1, 1),
    ("criteri mal format", _envelope(award_candidates=[AWARD, {**AWARD, "kind": "inventat"}]),
     False, 1, 1),
    ("mecanisme mal format", _envelope(award_candidates=[
        {**AWARD, "adjudication_basis": ["punts_magics"]}]), False, 1, 0),
    ("evidencia mal formada", _envelope(requirements=[{**REQ, "evidence": [{"id": "e1"}]}]),
     False, 0, 1),
    ("nota mal formada", _envelope(document_notes=[{"nota": 1}]), False, 1, 1),
    ("incertesa mal formada", _envelope(uncertainties=[{"note": None}]), False, 1, 1),
    ("document_notes inservible", _envelope(document_notes="text"), False, 1, 1),
    ("uncertainties inservible", _envelope(uncertainties={"note": "x"}), False, 1, 1),
]


@pytest.mark.parametrize(("case", "payload", "fails", "requirements", "awards"),
                         FAILURE_MATRIX, ids=[c[0] for c in FAILURE_MATRIX])
def test_la_matriu_de_fallades_v3_2(case, payload, fails, requirements, awards):
    if fails:
        with pytest.raises(ValidationError):
            parse_chunk_v3_result(payload)
        return
    parsed = parse_chunk_v3_result(payload)
    assert len(parsed.output.requirements) == requirements
    assert len(parsed.output.award_candidates) == awards
    assert parsed.schema_rejections.entries, f"{case}: el descart es registra"


# --------------------------------------------------------------------------- #
# 2. Model d'oferta: SI/NO amb una X                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("quote", [
    OFFER_INSTRUCTION,
    "cal assenyalar amb una X la opció corresponent \"SI\" o \"NO\"",
    "Marqueu amb una “X” l’opció escollida",
    "cal marcar\namb una X la casella",
    "Se deberá señalar con una X la opción «SÍ» o «NO»",
    "Indiqueu 'SI' o 'NO'",
    OFFER_CHECKBOX,
    "Tarifa 1 a 20 GB de dades ☐ SI ☐ NO",
])
def test_el_model_d_oferta_es_prova_literalment(quote):
    assert check_mechanism(AdjudicationBasis.OFFER_FORM, _candidate(), quote) == (
        MechanismCheck.VERIFIED
    )


@pytest.mark.parametrize("quote", [
    "cal assenyalar la opció que correspongui",           # sense X
    "connexió amb una xarxa de fibra",                     # "x" dins d'una paraula
    "Marqueu la resposta correcta",
    "si no ho fa, s'exclourà",                             # si no, sense barra ni cometes
    "l'oferta SI s'ha de signar",
    OFFER_BARE_LABEL,
    "Tarifa 1 a 20 GB de dades 4",
])
def test_quasi_models_d_oferta_no_proven_res(quote):
    assert check_mechanism(AdjudicationBasis.OFFER_FORM, _candidate(), quote) == (
        MechanismCheck.NOT_IN_QUOTE
    )


def test_un_candidat_que_cita_la_instruccio_si_no_es_formal():
    candidate = _candidate(adjudication_basis=["offer_form"],
                           evidence=[("e1", OFFER_INSTRUCTION, ("offer_form",))])

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.FORMAL_AWARD_CRITERION
    assert decision.mechanisms[AdjudicationBasis.OFFER_FORM] == (MechanismCheck.VERIFIED, ["e1"])


def test_una_casella_citada_fa_formal_encara_que_el_model_digui_desitjable():
    candidate = _candidate(kind="desirable_feature",
                           evidence=[("e1", OFFER_CHECKBOX, ())])

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.FORMAL_AWARD_CRITERION
    assert decision.reasons == [AwardReason.MECHANISM_VERIFIED,
                                AwardReason.KIND_OVERRIDDEN_BY_EVIDENCE]
    assert AdjudicationBasis.OFFER_FORM in decision.inferred


def test_l_etiqueta_nua_d_una_fila_del_model_no_fa_formal():
    # Com les files desitjables del chunk 5 v3.1: el nom de la fila, sense casella.
    candidate = _candidate(kind="desirable_feature", evidence=[("e1", OFFER_BARE_LABEL, ())])

    assert _classify(candidate).award_class == AwardClass.TECHNICAL_DESIRABLE


def test_esmentar_el_sobre_2_no_prova_cap_model_d_oferta_inferit():
    candidate = _candidate(kind="desirable_feature",
                           evidence=[("e1", "La documentació del sobre 2 inclourà el model", ())])

    assert _classify(candidate).award_class == AwardClass.TECHNICAL_DESIRABLE


# --------------------------------------------------------------------------- #
# 3. Taula de puntuacio: la fila, la seva puntuacio i la capcalera             #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("quote", "points"), [
    (ROW_FIXES, 2.0), (ROW_WRAPPED, 5.0), (ROW_TARIFA_1, 4.0),
    ("DESCRIPCIÓ PUNTS\nAmpliació bonus tarifaris de trànsit de fixos\n" + ROW_FIXES, 2.0),
])
def test_una_fila_amb_la_seva_puntuacio_sota_la_capcalera_es_formal(quote, points):
    candidate = _candidate(points=points, adjudication_basis=["criteria_table"],
                           evidence=[("e1", quote, ("criteria_table",))])

    decision = _classify(candidate, SCORE_TABLE)

    assert decision.award_class == AwardClass.FORMAL_AWARD_CRITERION
    assert decision.mechanisms[AdjudicationBasis.CRITERIA_TABLE] == (
        MechanismCheck.VERIFIED, ["e1"])
    assert decision.contexts[AdjudicationBasis.CRITERIA_TABLE] == TABLE_HEADER_CONTEXT


def test_la_fila_tambe_prova_els_punts_no_declarats():
    candidate = _candidate(points=4.0, evidence=[("e1", ROW_TARIFA_1, ())])

    decision = _classify(candidate, SCORE_TABLE)

    assert decision.award_class == AwardClass.FORMAL_AWARD_CRITERION
    assert AdjudicationBasis.POINTS in decision.inferred
    assert decision.contexts[AdjudicationBasis.POINTS] == TABLE_HEADER_CONTEXT


@pytest.mark.parametrize(("case", "quote", "points", "page"), [
    ("puntuacio d'una altra fila", ROW_TARIFA_1, 3.0, SCORE_TABLE),
    ("files amb puntuacions diferents", TWO_ROWS, 4.0, SCORE_TABLE),
    ("sense punts al candidat", ROW_TARIFA_1, None, SCORE_TABLE),
    ("sense text de pagina", ROW_TARIFA_1, 4.0, None),
    ("taula de quantitats", ROW_GPON, 26.0, QUANTITY_TABLE),
    ("cita que no es a la pagina", "Tarifa 9 a 90 GB 4", 4.0, SCORE_TABLE),
    ("capcalera massa lluny", ROW_TARIFA_1, 4.0,
     "DESCRIPCIÓ PUNTS\n" + "linia de text\n" * 40 + ROW_TARIFA_1 + "\n"),
    ("capcalera amb xifres", ROW_TARIFA_1, 4.0,
     "superior al 90% de la puntuació\n" + ROW_TARIFA_1 + "\n"),
    ("capcalera dins una frase", ROW_TARIFA_1, 4.0,
     "la seva puntuació no es modifica mai\n" + ROW_TARIFA_1 + "\n"),
])
def test_un_numero_nu_no_es_una_puntuacio(case, quote, points, page):
    candidate = _candidate(points=points, adjudication_basis=["criteria_table"],
                           evidence=[("e1", quote, ("criteria_table",))])

    decision = _classify(candidate, page)

    assert decision.award_class == AwardClass.CANDIDATE_AWARD_CRITERION, case
    assert decision.needs_review


def test_una_taula_de_quantitats_no_fa_formal_un_desitjable():
    candidate = _candidate(kind="desirable_feature", points=26.0,
                           evidence=[("e1", ROW_GPON, ())])

    assert _classify(candidate, QUANTITY_TABLE).award_class == AwardClass.TECHNICAL_DESIRABLE


def test_el_json_diu_com_s_ha_provat_i_si_era_inferit():
    candidate = _candidate(points=4.0, evidence=[("e1", ROW_TARIFA_1, ())])
    decision = _classify(candidate, SCORE_TABLE)

    body = adjudication_basis_json(candidate, decision, {"e1": "row-1"})

    assert body["mechanisms"] == [{"kind": "points", "check": "verified",
                                   "evidence_ids": ["row-1"],
                                   "context": "table_header_on_page", "inferred": True}]
    assert ROW_TARIFA_1 not in json.dumps(body)


# --------------------------------------------------------------------------- #
# 4. Classe per evidencia                                                      #
# --------------------------------------------------------------------------- #


def test_es_valorara_sense_mecanisme_es_desitjable():
    # Cas B: una caracteristica valorada, sense cap mecanisme.
    candidate = _candidate(kind="desirable_feature", evidence=[("e1", DESIRABLE, ())])

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.TECHNICAL_DESIRABLE
    assert decision.mechanisms == {}


def test_es_valorara_amb_els_seus_punts_citats_es_formal():
    candidate = _candidate(kind="desirable_feature", points=5.0,
                           evidence=[("e1", "Es valorarà la pantalla tàctil fins a 5 punts", ())])

    assert _classify(candidate).award_class == AwardClass.FORMAL_AWARD_CRITERION


def test_els_punts_d_un_altre_criteri_no_fan_formal_un_desitjable():
    candidate = _candidate(kind="desirable_feature",
                           evidence=[("e1", "Memoria tecnica: hasta 35 puntos", ())])

    assert _classify(candidate).award_class == AwardClass.TECHNICAL_DESIRABLE


def test_dir_criteri_sense_mecanisme_provat_va_a_revisio():
    candidate = _candidate(adjudication_basis=["points"], points=10.0,
                           evidence=[("e1", "Criteris de valoració de l'oferta", ("points",))])

    decision = _classify(candidate)

    assert decision.award_class == AwardClass.CANDIDATE_AWARD_CRITERION
    assert decision.needs_review


def test_la_clausula_de_criteris_no_s_infereix_mai():
    candidate = _candidate(kind="informative",
                           evidence=[("e1", "21. CRITERIS D'ADJUDICACIÓ", ())])

    assert _classify(candidate).award_class == AwardClass.INFORMATIVE


# --------------------------------------------------------------------------- #
# 5. De cap a cap                                                              #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def pcap_pages(session, tender_document, monkeypatch):
    """Una licitacio amb dues pagines de PCAP (taula de punts i model d'oferta)."""
    from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus
    from app.models.tender_documents import (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentFile,
        TenderDocumentPage,
    )

    pages = {15: SCORE_TABLE, 27: OFFER_FORM}
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 10)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    content = TenderDocumentContent(
        sha256="f" * 64, storage_key="tenders/documents/ff/ff/" + "f" * 64 + ".pdf",
        file_size=4096, mime_type="application/pdf", detected_extension="pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()
    extraction = TenderDocumentExtraction(
        content_id=content.id, parser_version="pypdf-v1", revision_number=1,
        status=ExtractionStatus.EXTRACTED, quality=ExtractionQuality.GOOD,
        page_count=len(pages), extracted_page_count=len(pages), empty_page_count=0,
        text_char_count=sum(len(t) for t in pages.values()), ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()
    for number, text in pages.items():
        session.add(TenderDocumentPage(
            extraction_id=extraction.id, page_number=number, text=text, char_count=len(text),
            text_hash="h" * 64, status=PageExtractionStatus.EXTRACTED,
        ))
    session.add(TenderDocumentFile(
        tender_document_id=tender_document.id, content_id=content.id,
        source_url=tender_document.url, version_number=1, is_current=True,
        download_status="downloaded",
    ))
    await session.commit()
    return {"document": tender_document, "extraction_id": str(extraction.id)}


def _ev(x, eid, page, quote, supports=()):
    return {"id": eid, "extraction_id": x, "page_number": page, "quote": quote,
            "supports": list(supports)}


async def _analyse(session, pcap_pages, payload):
    from app.services.requirement_analysis import analyze_tender_requirements

    tender = await session.get(Tender, pcap_pages["document"].tender_id)
    fake.queue_response(payload)
    return (await analyze_tender_requirements(session, tender, profile=profiles.V3)).run


async def test_fixture_clau_d_arrel_extra_el_chunk_sobreviu_i_no_es_desa_res_d_ella(
    session, pcap_pages
):
    x = pcap_pages["extraction_id"]
    payload = {
        "requirements": [{"category": "other", "title": "Requisit valid",
                          "evidence": [_ev(x, "e1", 15, ROW_TARIFA_1)]}],
        "award_candidates": [],
        "document_notes": [], "uncertainties": [],
        # SINTETIC: la clau d'arrel inventada, amb forma de requisit a dins.
        "extra_summary": [{"category": "other", "title": "NO S'HA DE DESAR",
                           "evidence": [_ev(x, "e1", 15, ROW_FIXES)]}],
    }

    run = await _analyse(session, pcap_pages, payload)

    assert (run.chunks_completed, run.chunks_failed) == (1, 0)
    titles = [r.title for r in (await session.scalars(sa.select(Requirement))).all()]
    assert titles == ["Requisit valid"]
    report = probe.chunk_report(run)
    assert report[0]["root_fields_rejected_schema"] == 1
    assert probe.canary_summary(report)["root_fields_rejected_schema"] == 1
    [chunk] = run.chunk_coverage["chunks"]
    assert {"item": "root", "index": 0, "stage": "root", "reason": "schema_invalid",
            "detail": "extra_forbidden", "field": "extra_summary"} in chunk["rejections"]


async def test_una_llista_d_extraccio_inservible_continua_fent_caure_el_chunk(
    session, pcap_pages
):
    run = await _analyse(session, pcap_pages, {"requirements": {"title": "x"}})

    assert run.chunks_failed == 1
    assert probe.chunk_report(run)[0]["schema_invalid"]
    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 0


async def test_de_cap_a_cap_la_classe_surt_de_l_evidencia(session, pcap_pages):
    x = pcap_pages["extraction_id"]
    run = await _analyse(session, pcap_pages, {"requirements": [], "award_candidates": [
        {"name": "Fila de taula", "kind": "award_criterion", "points": 4,
         "adjudication_basis": ["criteria_table"],
         "evidence": [_ev(x, "e1", 15, ROW_TARIFA_1, ["criteria_table"])]},
        {"name": "Fila amb puntuacio d'una altra", "kind": "award_criterion", "points": 3,
         "adjudication_basis": ["criteria_table"],
         "evidence": [_ev(x, "e1", 15, ROW_TARIFA_1, ["criteria_table"])]},
        {"name": "Model d'oferta", "kind": "award_criterion",
         "adjudication_basis": ["offer_form"],
         "evidence": [_ev(x, "e1", 27, OFFER_INSTRUCTION, ["offer_form"])]},
        {"name": "Etiqueta nua", "kind": "desirable_feature",
         "evidence": [_ev(x, "e1", 27, OFFER_BARE_LABEL)]},
    ], "document_notes": {"trencat": True}})

    rows = {a.name: a for a in (await session.scalars(sa.select(AwardCriterion))).all()}
    classes = {name: row.adjudication_basis_json["award_class"] for name, row in rows.items()}
    assert classes == {
        "Fila de taula": "formal_award_criterion",
        "Fila amb puntuacio d'una altra": "candidate_award_criterion",
        "Model d'oferta": "formal_award_criterion",
        "Etiqueta nua": "technical_desirable",
    }
    assert rows["Fila de taula"].adjudication_basis_json["mechanisms"][0]["context"] == (
        "table_header_on_page")
    assert rows["Fila amb puntuacio d'una altra"].review_status == ReviewStatus.NEEDS_REVIEW
    assert run.award_criteria_extracted == 2
    assert probe.chunk_report(run)[0]["auxiliary_rejected_schema"] == 1


# --------------------------------------------------------------------------- #
# 6. Versions i estimacio                                                      #
# --------------------------------------------------------------------------- #


def test_la_v3_2_puja_les_tres_versions_i_conserva_les_anteriors():
    from app.ai.prompts import requirements_v3, requirements_v3_1, requirements_v3_2

    assert (profiles.V3.extractor_version, profiles.V3.prompt_version,
            profiles.V3.schema_version) == (
        "tender-requirements-v3.2", "requirements-v3.2", "requirements-schema-v3.2")
    assert requirements_v3.PROMPT_VERSION_V3 == "requirements-v3"
    assert requirements_v3_1.PROMPT_VERSION_V3_1 == "requirements-v3.1"
    assert requirements_v3_1.SYSTEM_PROMPT_V3_1 != requirements_v3_2.SYSTEM_PROMPT_V3_2
    assert "NOMES aquestes claus" in requirements_v3_2.SYSTEM_PROMPT_V3_2
    assert "v3" not in profiles.PROFILES


@pytest.mark.parametrize("versions", [
    ("tender-requirements-v3", "requirements-v3", "requirements-schema-v3"),
    ("tender-requirements-v3.1", "requirements-v3.1", "requirements-schema-v3.1"),
])
async def test_un_run_v3_o_v3_1_no_es_repren_en_v3_2(
    session, palamos_like, one_page_chunks, versions
):
    canary, tender = await probe_tests._fake_canary(
        session, palamos_like, [{"requirements": [], "award_candidates": []}] * 3, {0, 2, 4}
    )
    canary.extractor_version, canary.prompt_version, canary.schema_version = versions
    await session.commit()

    with pytest.raises(probe.ProbeError, match="profile_mismatch"):
        await probe.preview_resume(session, tender, canary.id, provider_name="fake",
                                   model=settings.LLM_MODEL)
    assert await session.scalar(
        sa.select(RequirementExtractionRun.prompt_version).where(
            RequirementExtractionRun.id == canary.id)
    ) == versions[1], "el run antic no es toca"


async def test_l_historic_v3_1_no_compta_per_a_la_v3_2(
    session, session_factory, palamos_like, one_page_chunks, monkeypatch, capsys
):
    canary, tender = await probe_tests._fake_canary(
        session, palamos_like, [{"requirements": [], "award_candidates": []}] * 3, {0, 2, 4}
    )
    canary.prompt_version = "requirements-v3.1"
    tender.expediente_number = "2024-3137"
    await session.commit()
    provider = type("P", (), {"name": "fake", "model": settings.LLM_MODEL})()

    await probe_tests._cli(session_factory, probe_tests._cli_args("--chunks", "0,2,4",
                                                                  "--plan-only"),
                           provider, monkeypatch)
    line = next(line for line in capsys.readouterr().out.splitlines()
                if line.startswith("Estimacio de tokens"))

    assert line == ("Estimacio de tokens per a 3 crida(es): no disponible "
                    f"(sense historic de requirements-v3.2 amb {settings.LLM_MODEL}).")


# --------------------------------------------------------------------------- #
# 7. Diagnosi de la forma d'arrel quan falta el nucli (nomes observabilitat)   #
# --------------------------------------------------------------------------- #

SECRET_TEXT = "Text literal del plec que no s'ha de desar mai"


def _error_detail(run):
    [chunk] = run.chunk_coverage["chunks"]
    return chunk["status"], chunk["error"], chunk["error_detail"]


async def test_a_sense_nucli_es_desa_la_forma_de_l_arrel_i_cap_valor(session, pcap_pages):
    run = await _analyse(session, pcap_pages, {"summary": {"requirements": [SECRET_TEXT]}})

    status, error, detail = _error_detail(run)
    assert (status, error) == ("failed", "schema_invalid")
    assert {"path": "requirements", "type": "missing"} in detail
    assert {"path": "award_candidates", "type": "missing"} in detail
    assert detail[-1] == {"stage": "root", "reason": "core_missing",
                          "present_fields": [{"field": "summary", "type": "object"}],
                          "inner_fields": [{"field": "requirements", "type": "array"}]}
    assert SECRET_TEXT not in json.dumps(run.chunk_coverage, ensure_ascii=False)
    assert await session.scalar(sa.select(sa.func.count()).select_from(Requirement)) == 0


async def test_b_una_clau_d_arrel_insegura_es_diu_nomes_per_la_llargada(session, pcap_pages):
    unsafe = "Requisits del plec (resum)"
    run = await _analyse(session, pcap_pages, {unsafe: [SECRET_TEXT], "notes": SECRET_TEXT,
                                               "n": 3, "ok": True, "buit": None})

    _status, _error, detail = _error_detail(run)
    assert detail[-1]["present_fields"] == [
        {"field": f"<text,{len(unsafe)} chars>", "type": "array"},
        {"field": "notes", "type": "string"},
        {"field": "n", "type": "number"},
        {"field": "ok", "type": "boolean"},
        {"field": "buit", "type": "null"},
    ]
    stored = json.dumps(run.chunk_coverage, ensure_ascii=False)
    assert unsafe not in stored and SECRET_TEXT not in stored


async def test_c_una_clau_extra_amb_el_nucli_segueix_igual(session, pcap_pages):
    run = await _analyse(session, pcap_pages, {"requirements": [], "award_candidates": [],
                                               "summary": {"text": SECRET_TEXT}})

    status, error, detail = _error_detail(run)
    assert (status, error, detail) == ("completed", None, None)
    assert probe.chunk_report(run)[0]["root_fields_rejected_schema"] == 1
    assert SECRET_TEXT not in json.dumps(run.chunk_coverage, ensure_ascii=False)


async def test_d_una_resposta_valida_no_porta_cap_diagnosi(session, pcap_pages):
    run = await _analyse(session, pcap_pages, {"requirements": [], "award_candidates": []})

    assert _error_detail(run) == ("completed", None, None)


def test_la_forma_d_arrel_nomes_surt_si_falta_un_camp_d_arrel():
    from app.services.requirement_analysis import MAX_ROOT_SHAPE_FIELDS, root_shape_diagnostic

    nested = [{"path": "requirements.0.title", "type": "missing"}]
    assert root_shape_diagnostic({"requirements": [{}]}, nested) is None
    assert root_shape_diagnostic([1, 2], [{"path": "$", "type": "list_type"}]) is None
    many = {f"k{i}": i for i in range(MAX_ROOT_SHAPE_FIELDS + 5)}
    entry = root_shape_diagnostic(many, [{"path": "requirements", "type": "missing"}])
    assert len(entry["present_fields"]) == MAX_ROOT_SHAPE_FIELDS
    assert entry["present_fields_total"] == MAX_ROOT_SHAPE_FIELDS + 5


# --------------------------------------------------------------------------- #
# 8. Sobre embolcallat a l'arrel: nomes si el de dins es provadament el sobre  #
# --------------------------------------------------------------------------- #

EMPTY = {"requirements": [], "award_candidates": []}


def _normalise_and_parse(payload):
    from app.ai.output_normalization import normalise_output
    from app.ai.schemas_v3 import ChunkExtractionOutV3

    data, applied = normalise_output(payload, ChunkExtractionOutV3, context_extraction_ids=set())
    return data, applied


@pytest.mark.parametrize(("case", "payload", "marker"), [
    ("A", {"extraction": EMPTY}, "root:single_object_wrapper:extraction"),
    ("B", {"parameters": EMPTY}, "root:single_object_wrapper:parameters"),
    ("C", {"value": json.dumps(EMPTY)}, "root:json_string_object_wrapper:value"),
    ("clau insegura", {"Resposta completa": EMPTY}, "root:single_object_wrapper:<text,17 chars>"),
])
def test_un_sobre_embolcallat_i_provat_es_desembolcalla(case, payload, marker):
    data, applied = _normalise_and_parse(payload)

    assert applied == [marker], case
    parsed = parse_chunk_v3_result(data)
    assert parsed.output.requirements == parsed.output.award_candidates == []
    assert parsed.root_fields_rejected == 0


@pytest.mark.parametrize(("case", "payload"), [
    ("D falta award_candidates", {"extraction": {"requirements": []}}),
    ("E requirements objecte", {"extraction": {"requirements": {}, "award_candidates": []}}),
    ("E award_candidates text", {"extraction": {"requirements": [], "award_candidates": "[]"}}),
    ("F dues claus", {"extraction": EMPTY, "notes": []}),
    ("G doble embolcall", {"outer": {"extraction": EMPTY}}),
    ("H prosa", {"value": "Aquesta es la resposta en prosa"}),
    ("H llista", {"value": json.dumps([EMPTY])}),
    ("H escalar", {"value": "5"}),
    ("H JSON trencat", {"value": '{"requirements": [], "award_candidates": ['}),
    ("H doble text", {"value": json.dumps(json.dumps(EMPTY))}),
    ("clau que es un camp", {"requirements": EMPTY}),
    ("valor null", {"extraction": None}),
])
def test_un_embolcall_no_provat_no_es_toca_i_el_chunk_cau(case, payload):
    data, applied = _normalise_and_parse(payload)

    assert applied == [], case
    assert data == payload
    with pytest.raises(ValidationError):
        parse_chunk_v3_result(data)


def test_un_text_massa_llarg_no_es_desa():
    from app.ai.output_normalization import MAX_WRAPPED_JSON_CHARS

    huge = json.dumps({**EMPTY, "document_notes": [{"note": "x" * MAX_WRAPPED_JSON_CHARS}]})
    data, applied = _normalise_and_parse({"value": huge})

    assert applied == [] and data == {"value": huge}


def test_i_un_sobre_valid_no_canvia():
    payload = {"requirements": [REQ], "award_candidates": [AWARD]}

    data, applied = _normalise_and_parse(payload)

    assert applied == [] and data == payload


def test_j_la_llista_com_a_text_amb_el_seu_nom_segueix_igual():
    payload = {"requirements": json.dumps({"requirements": [REQ]}), "award_candidates": []}

    data, applied = _normalise_and_parse(payload)

    assert applied == ["requirements:json_string_wrapped_list"]
    assert [r.title for r in parse_chunk_v3_result(data).output.requirements] == ["ok"]


def test_k_una_clau_extra_amb_el_nucli_segueix_igual():
    data, applied = _normalise_and_parse({**EMPTY, "resum": {"a": 1}})

    assert applied == []
    assert parse_chunk_v3_result(data).root_fields_rejected == 1


def test_l_dins_l_embolcall_la_validacio_v3_2_es_la_mateixa():
    bad = {**REQ, "title": 5}
    data, applied = _normalise_and_parse(
        {"extraction": {"requirements": [REQ, bad], "award_candidates": [AWARD], "inventat": 1}}
    )

    assert applied == ["root:single_object_wrapper:extraction"]
    parsed = parse_chunk_v3_result(data)
    assert [r.title for r in parsed.output.requirements] == ["ok"]
    assert parsed.root_fields_rejected == 1
    assert [(e["item"], e["index"], e["stage"]) for e in parsed.schema_rejections.entries] == [
        ("root", 0, "root"), ("requirement", 1, "schema")]


async def test_de_cap_a_cap_un_embolcall_provat_es_desa_i_ho_diu(session, pcap_pages):
    x = pcap_pages["extraction_id"]
    run = await _analyse(session, pcap_pages, {"extraction": {
        "requirements": [{"category": "other", "title": "Requisit dins l'embolcall",
                          "evidence": [_ev(x, "e1", 15, ROW_TARIFA_1)]}],
        "award_candidates": []}})

    [chunk] = run.chunk_coverage["chunks"]
    assert (chunk["status"], chunk["normalised"]) == (
        "completed", ["root:single_object_wrapper:extraction"])
    titles = [r.title for r in (await session.scalars(sa.select(Requirement))).all()]
    assert titles == ["Requisit dins l'embolcall"]


async def test_de_cap_a_cap_un_embolcall_no_provat_diu_la_forma_de_dins(session, pcap_pages):
    run = await _analyse(session, pcap_pages, {"extraction": {"requisits": [SECRET_TEXT],
                                                              "resum": SECRET_TEXT}})

    status, error, detail = _error_detail(run)
    assert (status, error) == ("failed", "schema_invalid")
    assert detail[-1] == {
        "stage": "root", "reason": "core_missing",
        "present_fields": [{"field": "extraction", "type": "object"}],
        "inner_fields": [{"field": "requisits", "type": "array"},
                         {"field": "resum", "type": "string"}],
    }
    assert SECRET_TEXT not in json.dumps(run.chunk_coverage, ensure_ascii=False)


# Repeticio SINTETICA de les formes desades del run f7353279. Els payloads reals
# no es desen: aixo nomes mostra que faria cada regla amb cada FORMA possible.

@pytest.mark.parametrize(("chunk", "payload", "recovered"), [
    ("10 amb el sobre a dins", {"parameters": EMPTY}, True),
    ("10 sense el sobre a dins", {"parameters": {"input": EMPTY}}, False),
    ("12/13 amb el sobre a dins", {"extraction": EMPTY}, True),
    ("12/13 amb una llista absent", {"extraction": {"requirements": []}}, False),
    ("14 degenerat", {"<parametre-14c>": "sense res"}, False),
    ("6 requirements com a text {value:...}",
     {"requirements": json.dumps({"value": []}), "award_candidates": []}, False),
])
def test_repeticio_sintetica_de_les_formes_fallides(chunk, payload, recovered):
    data, applied = _normalise_and_parse(payload)

    if recovered:
        assert applied and parse_chunk_v3_result(data).output is not None, chunk
    else:
        with pytest.raises(ValidationError):
            parse_chunk_v3_result(data)
