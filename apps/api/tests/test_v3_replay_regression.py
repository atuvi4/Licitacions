"""Regressio del replay offline v3 sobre Palamos. Proveidor fals, zero crides.

Les pagines d'aqui reprodueixen la FORMA exacta del text real de Palamos que el
replay va fer servir (salts de linia del PDF, `(  )` amb dos espais, apostrof
tipografic), sense copiar-ne el plec. Tres d'aquestes formes feien fallar la v3
abans del replay:

* la fila GPON partida en dues linies pel PDF ("...pels equipaments" /
  "municipals* 26"): el terme i el valor quedaven en trossos diferents;
* l'apostrof tipografic ("d’adjudicació"): cap marcador no el reconeixia;
* una mencio de procediment ("d'acord amb els criteris d'adjudicació") no es
  el titol de la clausula de criteris, i no pot fer formal res.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.ai import claim_matching as cm
from app.ai import profiles
from app.ai.contract_v3 import (
    AdjudicationBasis,
    ClaimCheck,
    MechanismCheck,
    check_claim,
    check_mechanism,
    stored_requirement_support,
)
from app.ai.providers import fake
from app.ai.schemas_v3 import AwardCandidateOutV3, ClaimOutV3
from app.core.config import settings
from app.models.enums import (
    ExtractionQuality,
    ExtractionStatus,
    PageExtractionStatus,
    ReviewStatus,
    TenderDocumentType,
)
from app.models.requirements import AwardCriterion, Requirement
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.services.requirement_analysis import analyze_tender_requirements

# Forma de PPT p31: fila GPON partida pel PDF, fila FTTH sencera.
GPON_ROW = "Accés a Internet GPON a 600 Mbps simètrics pels equipaments\nmunicipals* 26"
FTTH_ROW = "Fibres individuals FTTH a 600 Mbps per diversos serveis 11"
TABLE = (
    "Internet descentralitzat Unitats\n"
    f"{GPON_ROW}\n"
    "Adreçament IP (1 adreça) 26\n"
    "Pool de IPs 5\n"
    f"{FTTH_ROW}\n"
)
# Forma de PPT p27: "es valorarà" dins d'una llista tecnica.
VALORARA = (
    "7. Es valorarà que els telèfons incorporin mecanismes que millorin la seva eficiència\n"
    "energètica"
)
# Forma de PCAP p27: model d'oferta del Sobre 2, `(  )` amb dos espais.
SOBRE2 = "Comptar amb centre de suport i atenció en català (  ) SI"
# Forma de PCAP p14: titol numerat en majuscules amb apostrof tipografic.
HEADING = "21. CRITERIS A TENIR EN COMPTE EN L’ADJUDICACIÓ"
PREU = "21.1 Criteri preu: fins a un màxim de 80 punts. S’atribuiran mitjançant la fórmula"
# Forma de PCAP p17: mencio de procediment, no titol.
MENCIO = (
    "atribuirà les puntuacions corresponents d’acord amb els\n"
    "criteris d’adjudicació susceptibles de ser aplicats"
)

PAGES = {
    14: f"{HEADING}\nEls criteris objectius que serviran de base:\n{PREU}\n",
    17: f"23. OBERTURA DEL SOBRE 2\nLa Mesa {MENCIO} mitjançant fórmules.\n",
    27: f"MODEL OFERTA (SOBRE 2)\n{SOBRE2}\n  (  ) NO\n",
    30: f"6. Tots els telèfons del mateix fabricant.\n{VALORARA}\n",
    31: TABLE,
}


@pytest.fixture(autouse=True)
def _clean_provider(monkeypatch):
    fake.reset()
    # Un sol chunk: les cinc pagines juntes, sense solapament.
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 10)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    yield
    fake.reset()


@pytest.fixture
async def palamos_like(session, tender_document):
    tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    content = TenderDocumentContent(
        sha256="d" * 64,
        storage_key="tenders/documents/dd/dd/" + "d" * 64 + ".pdf",
        file_size=4096,
        mime_type="application/pdf",
        detected_extension="pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()
    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="pypdf-v1",
        revision_number=1,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=len(PAGES),
        extracted_page_count=len(PAGES),
        empty_page_count=0,
        text_char_count=sum(len(t) for t in PAGES.values()),
        ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()
    for number, text in PAGES.items():
        session.add(TenderDocumentPage(
            extraction_id=extraction.id, page_number=number, text=text,
            char_count=len(text), text_hash="h" * 64, status=PageExtractionStatus.EXTRACTED,
        ))
    session.add(TenderDocumentFile(
        tender_document_id=tender_document.id, content_id=content.id,
        source_url=tender_document.url, version_number=1, is_current=True,
        download_status="downloaded",
    ))
    await session.commit()
    await session.refresh(extraction)
    return {"document": tender_document, "extraction_id": str(extraction.id)}


def _ev(extraction_id, eid, page, quote, supports):
    return {"id": eid, "extraction_id": extraction_id, "page_number": page, "quote": quote,
            "supports": list(supports)}


CLAIMS = [
    {"id": "accessos_gpon", "kind": "count", "value": 26, "anchor": "GPON"},
    {"id": "fibres_ftth", "kind": "count", "value": 11, "anchor": "FTTH"},
]


def _replay_payload(x):
    return {
        "requirements": [
            {"category": "technical_requirement", "title": "A1 GPON i FTTH amb una cita",
             "claims": CLAIMS, "claims_complete": "complete",
             "evidence": [_ev(x, "e1", 31, GPON_ROW, ["accessos_gpon", "fibres_ftth"])]},
            {"category": "technical_requirement", "title": "A2 GPON i FTTH ben citats",
             "claims": CLAIMS, "claims_complete": "complete",
             "evidence": [_ev(x, "e1", 31, GPON_ROW, ["accessos_gpon"]),
                          _ev(x, "e2", 31, FTTH_ROW, ["fibres_ftth"])]},
        ],
        "award_candidates": [
            {"name": "B Eficiencia energetica", "kind": "award_criterion",
             "adjudication_basis": ["criteria_section"],
             "evidence": [_ev(x, "e1", 30, VALORARA, ["criteria_section"])]},
            {"name": "C Suport en catala", "kind": "award_criterion",
             "adjudication_basis": ["offer_form"],
             "evidence": [_ev(x, "e1", 27, SOBRE2, ["offer_form"])]},
            {"name": "D Criteri preu", "kind": "award_criterion", "points": 80,
             "adjudication_basis": ["points", "criteria_section"],
             "evidence": [_ev(x, "e1", 14, PREU, ["points"]),
                          _ev(x, "e2", 14, HEADING, ["criteria_section"])]},
            {"name": "D- Mencio de procediment", "kind": "award_criterion",
             "adjudication_basis": ["criteria_section"],
             "evidence": [_ev(x, "e1", 17, MENCIO, ["criteria_section"])]},
        ],
    }


async def _replay(session, palamos_like):
    from app.models.tenders import Tender
    from tests.real_provider import RealNamedFake

    fake.queue_response(_replay_payload(palamos_like["extraction_id"]))
    tender = await session.get(Tender, palamos_like["document"].tender_id)
    # Es la reproduccio d'un run real: proveidor de nom real (cap xarxa), perque la
    # lectura de l'API nomes ensenya la run vigent (3B-2 §6.3).
    return (
        await analyze_tender_requirements(
            session, tender, profile=profiles.V3, provider=RealNamedFake()
        )
    ).run


# --------------------------------------------------------------------------- #
# De cap a cap: pipeline, files i API                                          #
# --------------------------------------------------------------------------- #


async def test_el_replay_desa_el_que_s_espera(session, palamos_like):
    run = await _replay(session, palamos_like)

    assert run.requirements_extracted == 2
    assert run.requirements_rejected == 0
    assert run.evidence_rejected == 0
    assert run.award_criteria_extracted == 2, "nomes C i D son formals"
    assert await session.scalar(sa.select(sa.func.count()).select_from(AwardCriterion)) == 4

    requirements = {
        r.title[:2]: r for r in (await session.scalars(sa.select(Requirement))).all()
    }
    [a1_cite] = requirements["A1"].evidence
    assert a1_cite.claim_support_json["claims"] == {
        "accessos_gpon": "supported", "fibres_ftth": "value_not_in_quote",
    }
    assert requirements["A1"].review_status == ReviewStatus.NEEDS_REVIEW
    assert requirements["A2"].review_status == ReviewStatus.UNREVIEWED

    classes = {
        c.name[:2].strip(): c.adjudication_basis_json["award_class"]
        for c in (await session.scalars(sa.select(AwardCriterion))).all()
    }
    assert classes == {
        "B": "candidate_award_criterion",
        "C": "formal_award_criterion",
        "D": "formal_award_criterion",
        "D-": "candidate_award_criterion",
    }


async def test_l_api_del_replay_separa_i_explica(session, palamos_like, client_a):
    await _replay(session, palamos_like)
    tender_id = palamos_like["document"].tender_id

    body = (await client_a.get(f"/api/v1/tenders/{tender_id}/analysis/requirements")).json()

    reqs = {r["title"][:2]: r for r in body["requirements"]}
    assert reqs["A1"]["claim_status"] == {
        "accessos_gpon": "supported", "fibres_ftth": "not_supported",
    }
    assert reqs["A1"]["support_level"] == "claim_supported"
    assert reqs["A1"]["support_status"] == "not_supported"
    assert reqs["A1"]["review_reasons"] == ["claim_not_supported"]
    assert reqs["A2"]["support_level"] == "requirement_supported"
    assert reqs["A2"]["review_reasons"] == []

    assert sorted(c["name"][:2].strip() for c in body["award_criteria"]) == ["C", "D"]
    assert all(c["review_reasons"] == [] for c in body["award_criteria"])
    candidates = {c["name"][:2].strip(): c for c in body["award_candidates"]}
    assert set(candidates) == {"B", "D-"}
    for candidate in candidates.values():
        assert candidate["award_class"] == "candidate_award_criterion"
        assert candidate["review_reasons"] == [
            "no_mechanism_verified", "criteria_section:not_in_quote",
        ]


# --------------------------------------------------------------------------- #
# Les tres formes reals que fallaven                                           #
# --------------------------------------------------------------------------- #


def test_una_fila_partida_pel_pdf_es_un_sol_tros():
    assert cm.segments(GPON_ROW) == [cm.normalise(GPON_ROW).replace("\n", " ")]
    claim = ClaimOutV3(id="accessos_gpon", kind="count", value=26, anchor="GPON")

    assert check_claim(claim, GPON_ROW) == ClaimCheck.SUPPORTED


def test_ajuntar_linies_no_fa_sostenir_res_per_casualitat():
    """A la taula sencera cada fila es tanca en la seva xifra: FTTH no pren el 26."""
    ftth_26 = ClaimOutV3(id="f", kind="count", value=26, anchor="FTTH")
    ftth_11 = ClaimOutV3(id="f", kind="count", value=11, anchor="FTTH")
    gpon_26 = ClaimOutV3(id="g", kind="count", value=26, anchor="GPON")

    assert check_claim(ftth_26, TABLE) == ClaimCheck.BINDING_AMBIGUOUS
    assert check_claim(ftth_11, TABLE) == ClaimCheck.SUPPORTED
    assert check_claim(gpon_26, TABLE) == ClaimCheck.SUPPORTED


def test_una_fila_tallada_sense_el_seu_valor_no_pren_el_de_la_fila_anterior():
    quote = GPON_ROW + "\nFibres individuals FTTH a 600 Mbps"
    ftth_26 = ClaimOutV3(id="f", kind="count", value=26, anchor="FTTH")

    assert check_claim(ftth_26, quote) == ClaimCheck.BINDING_AMBIGUOUS


def _mechanism(basis, quote, **kwargs):
    candidate = AwardCandidateOutV3(name="x", kind="award_criterion", **kwargs)
    return check_mechanism(AdjudicationBasis(basis), candidate, quote)


def test_l_apostrof_tipografic_i_el_titol_real_de_la_clausula():
    assert cm.normalise("d’adjudicació") == "d'adjudicacio"
    assert _mechanism("criteria_section", HEADING) == MechanismCheck.VERIFIED
    assert _mechanism("points", PREU, points=80) == MechanismCheck.VERIFIED


@pytest.mark.parametrize(
    "quote",
    [
        MENCIO,
        "ha obtingut puntuació en base a criteris d’adjudicació.",
        "Es valorarà l'eficiència energètica dels terminals",
    ],
)
def test_una_mencio_no_es_el_titol_de_la_clausula(quote):
    assert _mechanism("criteria_section", quote) == MechanismCheck.NOT_IN_QUOTE


@pytest.mark.parametrize(
    "quote",
    [
        "Clàusula 12. Criteris d'adjudicació",
        "CRITERIOS DE VALORACIÓN",
        "8.2 Criterios de adjudicación evaluables mediante fórmulas",
    ],
)
def test_titols_de_clausula_reconeguts(quote):
    assert _mechanism("criteria_section", quote) == MechanismCheck.VERIFIED


def test_el_model_d_oferta_amb_dos_espais():
    assert _mechanism("offer_form", SOBRE2) == MechanismCheck.VERIFIED


# --------------------------------------------------------------------------- #
# Lectura del suport desat                                                     #
# --------------------------------------------------------------------------- #


def test_el_suport_desat_es_llegeix_igual_que_es_va_decidir():
    value = {"claims": CLAIMS, "claims_complete": "complete"}
    supports = [{"contract": "claim-support-v1", "ref": "e1",
                 "claims": {"accessos_gpon": "supported", "fibres_ftth": "value_not_in_quote"}}]

    stored = stored_requirement_support("requirements-claims-v3", value, supports)

    assert stored.per_claim == {"accessos_gpon": "supported", "fibres_ftth": "not_supported"}
    assert (stored.level, stored.status) == ("claim_supported", "not_supported")
    assert stored.review_reasons == ["claim_not_supported"]


def test_la_lectura_no_s_inventa_res_per_a_v1_ni_per_a_codis_estranys():
    assert stored_requirement_support(None, {"amount": 450000}, []) is None
    value = {"claims": [{"id": "a", "kind": "count", "value": 1}], "claims_complete": "raro"}
    stored = stored_requirement_support(
        "requirements-claims-v3", value, [{"claims": {"a": "inventat"}}, None]
    )
    assert stored.per_claim == {"a": "not_supported"}
    assert stored.review_reasons == ["claim_not_supported", "claims_completeness_unknown"]
