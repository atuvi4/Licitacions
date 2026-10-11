"""Extraction Contract v3.1: el que el canari pagat de Palamos va fer veure. Zero crides.

Tres capes, i no es barregen:

* **Fixture derivat de la font.** Les pagines `PAGES` reprodueixen la FORMA de
  linies reals del PPT de Palamos (p27, p28, p30, p31): salts de linia del PDF,
  cometes tipografiques, etiquetes com "3G/4G" o "800/900". No son el plec sencer.
* **Comportament del parser i del validador.** Que fa la v3.1 amb aquestes
  formes: unitats, ancores, lligams, claims invalids.
* **Candidats SINTETICS.** Les respostes del proveidor fals son candidats amb
  forma de model escrits a ma per exercir cada cami. NO son el que el model va
  respondre al canari (aquella resposta no es desa mai). Que els 4 rebutjos del
  chunk 15 fossin IP/PoE, auriculars, interficies i tarifes es una INFERENCIA
  (temes sense cap requisit acceptat), no una observacio.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from pydantic import ValidationError

from app.ai import claim_matching as cm
from app.ai import profiles
from app.ai.contract_v3 import (
    ClaimCheck,
    ClaimStatus,
    check_claim,
    parse_chunk_v3_result,
    requirement_support,
)
from app.ai.providers import fake
from app.ai.schemas_v3 import ClaimOutV3, RequirementOutV3
from app.core.config import settings
from app.models.enums import ReviewStatus, TenderDocumentType
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.tenders import Tender
from tests import test_v3_tender_probe as probe_tests

probe = probe_tests.probe
palamos_like = probe_tests.palamos_like
one_page_chunks = probe_tests.one_page_chunks
_clean_provider = probe_tests._clean_provider
_no_persistent_windows_env = probe_tests._no_persistent_windows_env


def _claim(**kwargs) -> ClaimOutV3:
    return ClaimOutV3(**{"id": "c", "kind": "count", "value": 1, **kwargs})


# --------------------------------------------------------------------------- #
# Fixture derivat de la font (forma de linies reals del PPT de Palamos)        #
# --------------------------------------------------------------------------- #

GPON_ROW = "Accés a Internet GPON a 600 Mbps simètrics pels equipaments\nmunicipals* 26"
IP_ROW = "Adreçament IP (1 adreça) 26"
ROUTERS_ROW = (
    "Routers amb connexions 3G/4G per línia Backup i línia de tarifa de\n"
    "dades il·limitada 3"
)
TRUCADES_ROW = "Trucades a numeracions 800/900 350"
DISPLAY = " Display TFT color amb una mida superior a 2,5”"
AGENDA = (
    " Marcació per nom, disposant d’agenda corporativa integrada amb més de 500\n"
    "entrades"
)
POE = "- PoE: 802.3af o 802.3at., consum inferior a 6.5W"
ETHERNET = "- Interfície Ethernet IP 100/1000 Mbps i connector RJ-45."
TLS = "- Suport protocol TLS 1.2 o superior"
CODEC = "- Compressió: Com a mínim, G.711 i G729."
DBA = " limitació acústica que protegir sons que superin els 120 dBA"
PES = " Pes inferior a 100 grams"
CABLE = " Cable amb una mida superior a 1,5 m."
ADMIN = (
    "8.1.9 Interfícies d’administració. Aquestes interfícies d’administració "
    "han de permetre"
)
TARIFA_1 = "Tarifa 1: Tarifa plana de dades amb, com a mínim 10 GB 140"
TARIFA_2 = "Tarifa 2: Tarifa plana de dades amb, com a mínim 50 GB 41"

PAGES = {
    27: f"8.1.6 Terminals telefònics. Requeriments:\n{DISPLAY}\n{AGENDA}\n",
    28: f"Prestacions IP:\n{ETHERNET}\n{POE}\n{CODEC}\n{TLS}\n",
    30: f"2. Auriculars amb fil:\n{DBA}\n{PES}\n{CABLE}\n{ADMIN}\n",
    31: f"{GPON_ROW}\n{IP_ROW}\nPool de IPs 5\n{ROUTERS_ROW}\n{TRUCADES_ROW}\n"
        f"{TARIFA_1}\n{TARIFA_2}\n",
}


# --------------------------------------------------------------------------- #
# 3. Unitats fisiques i de dades                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("quote", "claim"),
    [
        (POE, {"kind": "quantity", "value": 6.5, "unit": "W", "anchor": "consum inferior a"}),
        (PES, {"kind": "quantity", "value": 100, "unit": "g", "anchor": "Pes inferior a"}),
        (CABLE, {"kind": "quantity", "value": 1.5, "unit": "m",
                 "anchor": "Cable amb una mida superior a"}),
        (CABLE, {"kind": "quantity", "value": 1500, "unit": "mm",
                 "anchor": "Cable amb una mida superior a"}),
        (DBA, {"kind": "quantity", "value": 120, "unit": "dBA", "anchor": "superin els"}),
        (TARIFA_1, {"kind": "quantity", "value": 10, "unit": "GB",
                    "anchor": "Tarifa 1: Tarifa plana de dades amb, com a mínim"}),
        (DISPLAY, {"kind": "quantity", "value": 2.5, "unit": "inch",
                   "anchor": 'Display TFT color amb una mida superior a 2,5"'}),
        ("Minuts mensuals per mòbil 20000 minuts", {
            "kind": "duration", "value": 20000, "unit": "minutes",
            "anchor": "Minuts mensuals per mòbil"}),
    ],
)
def test_magnituds_de_palamos_sostingudes(quote, claim):
    assert check_claim(_claim(**claim), quote) == ClaimCheck.SUPPORTED


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "quantity", "value": 6.5, "unit": "watts"},  # unitat fora del conjunt
        {"kind": "quantity", "value": 6.5},  # sense unitat
        {"kind": "quantity", "value": 6.5, "unit": "mbps"},  # unitat d'una altra familia
        {"kind": "count", "value": 100, "unit": "g"},  # un recompte no porta unitat
        {"kind": "quantity", "value": "6,5 W", "unit": "W"},  # valor com a text
        {"kind": "duration", "value": 5, "unit": "GB"},
    ],
)
def test_unitats_mal_formades_fallen_tancades(bad):
    with pytest.raises(ValidationError):
        _claim(**bad)


def test_families_diferents_no_es_comparen():
    grams = _claim(kind="quantity", value=100, unit="g", anchor="Pes")
    dba = _claim(kind="quantity", value=120, unit="dB", anchor="superin els")

    assert check_claim(grams, "Pes inferior a 100 mm") == ClaimCheck.UNIT_NOT_IN_QUOTE
    assert check_claim(dba, DBA) == ClaimCheck.UNIT_NOT_IN_QUOTE, "dBA no es dB"


def test_3g_no_son_grams():
    three_grams = _claim(kind="quantity", value=3, unit="g", anchor="Routers")

    assert check_claim(three_grams, ROUTERS_ROW) != ClaimCheck.SUPPORTED


# --------------------------------------------------------------------------- #
# 4. Ancores: format, no semblanca                                             #
# --------------------------------------------------------------------------- #


def test_ancores_amb_salts_de_linia_i_cometes_tipografiques():
    gpon = _claim(kind="count", value=26, anchor="pels equipaments municipals")
    agenda = _claim(kind="count", value=500,
                    anchor="agenda corporativa integrada amb més de 500 entrades")

    assert check_claim(gpon, GPON_ROW) == ClaimCheck.SUPPORTED
    assert check_claim(agenda, AGENDA) == ClaimCheck.SUPPORTED
    assert cm.contains_term("d’agenda corporativa", "d'agenda corporativa")
    assert cm.contains_term('2,5”', '2,5"')


@pytest.mark.parametrize(
    "anchor", ["pels equipaments socials", "equipament municipal", "FTTH", "equipamentsmunicipals"]
)
def test_una_ancora_diferent_continua_absent(anchor):
    claim = _claim(kind="count", value=26, anchor=anchor)

    assert check_claim(claim, GPON_ROW) == ClaimCheck.ANCHOR_NOT_IN_QUOTE


# --------------------------------------------------------------------------- #
# 5. Digits d'etiqueta vs valors rivals de debo                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("quote", "value", "anchor"),
    [
        (TRUCADES_ROW, 350, "Trucades a numeracions 800/900"),
        (IP_ROW, 26, "Adreçament IP (1 adreça)"),
        (ROUTERS_ROW, 3, "Routers amb connexions 3G/4G per línia Backup"),
        ("8.1.4 Solució d’enregistrament: 3 anys", 3, "enregistrament"),
    ],
)
def test_els_digits_d_etiqueta_no_son_rivals(quote, value, anchor):
    kind = "duration" if "anys" in quote else "count"
    extra = {"unit": "years"} if kind == "duration" else {}
    claim = _claim(kind=kind, value=value, anchor=anchor, **extra)

    assert check_claim(claim, quote) == ClaimCheck.SUPPORTED


@pytest.mark.parametrize(
    ("quote", "claim"),
    [
        # Dues files en una: el 26 i l'11 son rivals.
        ("GPON 26 FTTH 11", {"kind": "count", "value": 26, "anchor": "FTTH"}),
        ("GPON 26 FTTH 11", {"kind": "count", "value": 11, "anchor": "GPON"}),
        ("durada de 3 anys prorrogable 1 any",
         {"kind": "duration", "value": 3, "unit": "years", "anchor": "durada"}),
        (f"{TARIFA_1} i {TARIFA_2}",
         {"kind": "quantity", "value": 10, "unit": "GB", "anchor": "Tarifa plana de dades"}),
    ],
)
def test_els_rivals_de_debo_segueixen_sent_ambigus(quote, claim):
    assert check_claim(_claim(**claim), quote) == ClaimCheck.BINDING_AMBIGUOUS


def test_un_valor_que_nomes_es_dins_d_una_etiqueta_no_es_sosté():
    claim = _claim(kind="count", value=900, anchor="Trucades a numeracions")

    assert check_claim(claim, TRUCADES_ROW) == ClaimCheck.UNIT_NOT_IN_QUOTE


# --------------------------------------------------------------------------- #
# 2. Un claim invalid ja no s'endu el requisit                                 #
# --------------------------------------------------------------------------- #


def _req(claims, evidence=None, **extra):
    return {
        "category": "technical_requirement",
        "title": "Auriculars amb fil",
        "claims": claims,
        "claims_complete": "complete",
        "evidence": evidence or [
            {"id": "e1", "extraction_id": "x", "page_number": 30, "quote": PES,
             "supports": ["pes_max", "cable"]},
        ],
        **extra,
    }


def test_el_requisit_es_conserva_sense_el_claim_invalid():
    payload = {"award_candidates": [], "requirements": [_req([
        {"id": "pes_max", "kind": "quantity", "value": 100, "unit": "g",
         "anchor": "Pes inferior a"},
        {"id": "cable", "kind": "quantity", "value": 1.5, "unit": "metres"},  # unitat invalida
    ])]}

    parsed = parse_chunk_v3_result(payload)

    [requirement] = parsed.output.requirements
    assert [c.id for c in requirement.claims] == ["pes_max"]
    assert requirement.claims_complete == "partial", "se sap que en falta"
    assert (parsed.items_rejected, parsed.claims_rejected) == (0, 1)
    assert parsed.salvaged_requirements == frozenset({0})
    assert parsed.schema_rejected == 0, "un claim no es un requisit rebutjat"
    assert parsed.schema_rejections.entries == [
        {"item": "requirement", "index": 0, "stage": "claim", "reason": "schema_invalid",
         "claim": 1, "detail": "enum", "field": "unit"},
    ]


def test_el_claim_descartat_mai_no_queda_sostingut():
    payload = {"award_candidates": [], "requirements": [_req([
        {"id": "pes_max", "kind": "quantity", "value": 100, "unit": "g",
         "anchor": "Pes inferior a"},
        {"id": "cable", "kind": "quantity", "value": "1,5 m", "unit": "m"},
    ])]}
    [requirement] = parse_chunk_v3_result(payload).output.requirements

    support = requirement_support(requirement, {"e1": PES})

    assert support.per_claim == {"pes_max": ClaimStatus.SUPPORTED}
    assert support.unknown_refs == {"e1": ["cable"]}
    assert not support.requirement_supported, "parcial mai no es complet"


@pytest.mark.parametrize(
    "broken",
    [
        {"category": "inventada"},  # error fora dels claims
        {"evidence": [{"id": "cita-1", "extraction_id": "x", "page_number": 30,
                       "quote": PES}]},  # cita mal formada
        {"claims": [{"id": "a", "kind": "count", "value": 1},
                    {"id": "a", "kind": "count", "value": 2}]},  # ids repetits
        {"claims": [{"id": "a", "kind": "count", "value": 1}] * 31},  # massa claims
    ],
)
def test_si_l_error_no_es_d_un_claim_concret_es_descarta_com_abans(broken):
    base = _req([{"id": "cable", "kind": "quantity", "value": 1.5, "unit": "metres"}])

    parsed = parse_chunk_v3_result({"award_candidates": [], "requirements": [{**base, **broken}]})

    assert parsed.output.requirements == []
    assert parsed.items_rejected == 1
    assert parsed.schema_rejections.entries[0]["stage"] == "schema"


def test_sense_cap_claim_valid_queda_nomes_la_cita_i_va_a_revisio():
    parsed = parse_chunk_v3_result({"award_candidates": [], "requirements": [_req([
        {"id": "cable", "kind": "quantity", "value": 1.5, "unit": "metres"},
    ])]})

    [requirement] = parsed.output.requirements
    assert requirement.claims == [] and requirement.claims_complete == "partial"
    assert parsed.salvaged_requirements == frozenset({0})


def test_un_requisit_valid_no_canvia():
    parsed = parse_chunk_v3_result({"award_candidates": [], "requirements": [_req([
        {"id": "pes_max", "kind": "quantity", "value": 100, "unit": "g",
         "anchor": "Pes inferior a"},
    ])]})

    assert parsed.output.requirements[0].claims_complete == "complete"
    assert (parsed.items_rejected, parsed.claims_rejected) == (0, 0)
    assert parsed.salvaged_requirements == frozenset()


def test_l_esquema_accepta_ancores_de_fila_de_taula():
    long_anchor = "Routers amb connexions 3G/4G per línia Backup i línia de tarifa de dades"
    assert 80 < len(long_anchor * 2) <= 160
    RequirementOutV3(category="other", title="t",
                     claims=[{"id": "a", "kind": "count", "value": 3,
                              "anchor": long_anchor * 2}])
    with pytest.raises(ValidationError):
        _claim(anchor="x" * 161)


# --------------------------------------------------------------------------- #
# 7. De cap a cap: pagines de Palamos + candidats SINTETICS                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def palamos_tech(session, tender_document, monkeypatch):
    """Una licitacio amb les quatre pagines tecniques, en un sol chunk."""
    from app.models.enums import (
        ExtractionQuality,
        ExtractionStatus,
        PageExtractionStatus,
    )
    from app.models.tender_documents import (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentFile,
        TenderDocumentPage,
    )

    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_PAGES", 10)
    monkeypatch.setattr(settings, "REQUIREMENTS_CHUNK_OVERLAP_PAGES", 0)
    tender_document.document_type = TenderDocumentType.TECHNICAL_TERMS
    content = TenderDocumentContent(
        sha256="e" * 64, storage_key="tenders/documents/ee/ee/" + "e" * 64 + ".pdf",
        file_size=4096, mime_type="application/pdf", detected_extension="pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()
    extraction = TenderDocumentExtraction(
        content_id=content.id, parser_version="pypdf-v1", revision_number=1,
        status=ExtractionStatus.EXTRACTED, quality=ExtractionQuality.GOOD,
        page_count=len(PAGES), extracted_page_count=len(PAGES), empty_page_count=0,
        text_char_count=sum(len(t) for t in PAGES.values()), ocr_candidate=False,
        is_current=True,
    )
    session.add(extraction)
    await session.flush()
    for number, text in PAGES.items():
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


def _ev(x, eid, page, quote, supports):
    return {"id": eid, "extraction_id": x, "page_number": page, "quote": quote,
            "supports": list(supports)}


def _synthetic_candidates(x):
    """SINTETIC: forma de resposta de model escrita a ma. Cada requisit tecnic porta
    UN claim invalid, que a la v3 s'hauria endut el requisit sencer."""
    return {"award_candidates": [], "requirements": [
        {"category": "technical_requirement", "title": "Prestacions IP dels terminals",
         "claims_complete": "complete",
         "claims": [
             {"id": "poe_consum", "kind": "quantity", "value": 6.5, "unit": "W",
              "anchor": "consum inferior a"},
             {"id": "tls", "kind": "text", "value": "TLS 1.2"},
             {"id": "codec", "kind": "text", "value": "G.711"},
             {"id": "poe_std", "kind": "quantity", "value": "802.3at", "unit": "W"},  # malament
         ],
         "evidence": [_ev(x, "e1", 28, POE, ["poe_consum", "poe_std"]),
                      _ev(x, "e2", 28, TLS, ["tls"]), _ev(x, "e3", 28, CODEC, ["codec"])]},
        {"category": "technical_requirement", "title": "Auriculars amb fil",
         "claims_complete": "complete",
         "claims": [
             {"id": "so_max", "kind": "quantity", "value": 120, "unit": "dBA",
              "anchor": "superin els"},
             {"id": "pes_max", "kind": "quantity", "value": 100, "unit": "g",
              "anchor": "Pes inferior a"},
             {"id": "cable", "kind": "quantity", "value": 1.5, "unit": "metres"},  # malament
         ],
         "evidence": [_ev(x, "e1", 30, DBA, ["so_max"]), _ev(x, "e2", 30, PES, ["pes_max"]),
                      _ev(x, "e3", 30, CABLE, ["cable"])]},
        {"category": "technical_requirement", "title": "Interficies d'administracio",
         "claims": [], "evidence": [_ev(x, "e1", 30, ADMIN, [])]},
        {"category": "technical_requirement", "title": "Tarifes de dades mobils",
         "claims_complete": "complete",
         "claims": [
             {"id": "tarifa_1_linies", "kind": "count", "value": 140,
              "anchor": "Tarifa 1: Tarifa plana de dades amb, com a mínim 10 GB"},
             {"id": "tarifa_1_dades", "kind": "quantity", "value": 10, "unit": "GB",
              "anchor": "Tarifa 1: Tarifa plana de dades amb, com a mínim"},
             {"id": "tarifa_2_linies", "kind": "count", "value": 41, "unit": "GB"},  # malament
         ],
         "evidence": [_ev(x, "e1", 31, TARIFA_1, ["tarifa_1_linies", "tarifa_1_dades"]),
                      _ev(x, "e2", 31, TARIFA_2, ["tarifa_2_linies"])]},
        {"category": "technical_requirement", "title": "Terminal gamma estandard",
         "claims_complete": "complete",
         "claims": [
             {"id": "display", "kind": "quantity", "value": 2.5, "unit": "inch",
              "anchor": 'Display TFT color amb una mida superior a 2,5"'},
             {"id": "agenda", "kind": "count", "value": 500,
              "anchor": "agenda corporativa integrada amb més de 500 entrades"},
         ],
         "evidence": [_ev(x, "e1", 27, DISPLAY, ["display"]),
                      _ev(x, "e2", 27, AGENDA, ["agenda"])]},
        {"category": "technical_requirement", "title": "Dimensionament de dades",
         "claims_complete": "complete",
         "claims": [
             {"id": "gpon", "kind": "count", "value": 26, "anchor": "pels equipaments municipals"},
             {"id": "ips", "kind": "count", "value": 26, "anchor": "Adreçament IP (1 adreça)"},
             {"id": "routers", "kind": "count", "value": 3,
              "anchor": "Routers amb connexions 3G/4G per línia Backup"},
             {"id": "n800", "kind": "count", "value": 350,
              "anchor": "Trucades a numeracions 800/900"},
         ],
         "evidence": [_ev(x, "e1", 31, GPON_ROW, ["gpon"]), _ev(x, "e2", 31, IP_ROW, ["ips"]),
                      _ev(x, "e3", 31, ROUTERS_ROW, ["routers"]),
                      _ev(x, "e4", 31, TRUCADES_ROW, ["n800"])]},
    ]}


async def test_els_requisits_tecnics_de_palamos_ja_no_desapareixen(session, palamos_tech):
    from app.services.requirement_analysis import analyze_tender_requirements

    tender = await session.get(Tender, palamos_tech["document"].tender_id)
    fake.queue_response(_synthetic_candidates(palamos_tech["extraction_id"]))

    run = (await analyze_tender_requirements(session, tender, profile=profiles.V3)).run

    assert run.extractor_version == "tender-requirements-v3.2"
    assert run.requirements_extracted == 6
    assert run.requirements_rejected == 0, "cap requisit sencer perdut"
    requirements = {r.title: r for r in (await session.scalars(sa.select(Requirement))).all()}

    def support(title):
        merged = {}
        for cite in requirements[title].evidence:
            merged.update(cite.claim_support_json["claims"])
        return merged

    assert support("Prestacions IP dels terminals") == {
        "poe_consum": "supported", "tls": "supported", "codec": "supported",
    }
    assert support("Auriculars amb fil") == {"so_max": "supported", "pes_max": "supported"}
    assert support("Tarifes de dades mobils") == {
        "tarifa_1_linies": "supported", "tarifa_1_dades": "supported",
    }
    assert support("Terminal gamma estandard") == {"display": "supported", "agenda": "supported"}
    assert support("Dimensionament de dades") == {
        "gpon": "supported", "ips": "supported", "routers": "supported", "n800": "supported",
    }
    for salvaged in ("Prestacions IP dels terminals", "Auriculars amb fil",
                     "Tarifes de dades mobils"):
        assert requirements[salvaged].review_status == ReviewStatus.NEEDS_REVIEW
        assert requirements[salvaged].value_json["claims_complete"] == "partial"
    assert requirements["Terminal gamma estandard"].review_status == ReviewStatus.UNREVIEWED

    [chunk] = run.chunk_coverage["chunks"]
    claim_rejections = [r for r in chunk["rejections"]
                        if r["stage"] == "claim" and r["reason"] == "schema_invalid"]
    assert [(r["index"], r["claim"]) for r in claim_rejections] == [(0, 3), (1, 2), (3, 2)]
    counts = probe.canary_summary(probe.chunk_report(run))
    assert (counts["items_rejected_schema"], counts["claims_rejected_schema"],
            counts["items_salvaged"]) == (0, 3, 3)


# --------------------------------------------------------------------------- #
# 1. Estimacio de tokens: amb les crides PREVISTES                             #
# --------------------------------------------------------------------------- #


async def test_l_estimacio_multiplica_per_les_crides_previstes(
    session, session_factory, palamos_like, one_page_chunks, monkeypatch, capsys
):
    from app.ai.provider import OutputTruncatedError, Usage

    truncated = OutputTruncatedError(
        "limit", usage=Usage(input_tokens=100, output_tokens=50, stop_reason="max_tokens")
    )
    canary, tender = await probe_tests._fake_canary(
        session, palamos_like, [probe_tests.EMPTY_V3, truncated, probe_tests.EMPTY_V3], {0, 2, 4}
    )
    tender.expediente_number = "2024-3137"
    await session.commit()
    provider = type("P", (), {"name": "fake", "model": settings.LLM_MODEL})()
    # Historic: 3 peticions de 100 / 50 tokens (el proveidor fals en compta 100/50).

    async def run_plan(*extra):
        await probe_tests._cli(session_factory, probe_tests._cli_args(*extra, "--plan-only"),
                               provider, monkeypatch)
        return next(line for line in capsys.readouterr().out.splitlines()
                    if line.startswith("ESTIMACIO"))

    fresh = await run_plan("--chunks", "0,2,4")
    selective = await run_plan("--resume-from-run", str(canary.id), "--resume-chunks", "2")
    normal = await run_plan("--resume-from-run", str(canary.id))

    assert fresh.startswith("ESTIMACIO per a 3 crida(es) prevista(es): ~300 tokens d'entrada, "
                            "~150 de sortida")
    assert selective.startswith("ESTIMACIO per a 1 crida(es) prevista(es): ~100 tokens")
    assert normal.startswith("ESTIMACIO per a 3 crida(es) prevista(es): ~300 tokens")
    for line in (fresh, selective, normal):
        assert "(base: 3 peticio(ns) historica(ues) de requirements-v3.2)" in line


def test_sense_historic_l_estimacio_ho_diu_amb_les_crides():
    estimate = probe.estimate_tokens(None, 19, "requirements-v3.2", "m")

    assert estimate == {"available": False, "calls": 19,
                        "reason": "sense historic de requirements-v3.2 amb m"}


# --------------------------------------------------------------------------- #
# 6. Versions: un run v3 no es repren en v3.1                                  #
# --------------------------------------------------------------------------- #


def test_les_tres_versions_pugen_juntes_i_la_v3_es_conserva():
    from app.ai.prompts import requirements_v3, requirements_v3_1

    # v3.2 ha pujat les tres versions; els prompts v3 i v3.1 es conserven.
    assert (profiles.V3.extractor_version, profiles.V3.prompt_version,
            profiles.V3.schema_version) == (
        "tender-requirements-v3.2", "requirements-v3.2", "requirements-schema-v3.2")
    assert requirements_v3_1.PROMPT_VERSION_V3_1 == "requirements-v3.1"
    assert requirements_v3.PROMPT_VERSION_V3 == "requirements-v3"
    assert requirements_v3.SYSTEM_PROMPT_V3 != requirements_v3_1.SYSTEM_PROMPT_V3_1
    assert "quantity" in requirements_v3_1.SYSTEM_PROMPT_V3_1
    assert "v3" not in profiles.PROFILES


async def test_un_run_v3_no_es_pot_reprendre_en_v3_1(session, palamos_like, one_page_chunks):
    canary, tender = await probe_tests._fake_canary(
        session, palamos_like, [probe_tests.EMPTY_V3] * 3, {0, 2, 4}
    )
    # Com els runs pagats bed24c45 i 4df81a00: versions v3.
    canary.extractor_version = "tender-requirements-v3"
    canary.prompt_version = "requirements-v3"
    canary.schema_version = "requirements-schema-v3"
    await session.commit()

    with pytest.raises(probe.ProbeError, match="profile_mismatch"):
        await probe.preview_resume(session, tender, canary.id, provider_name="fake",
                                   model=settings.LLM_MODEL, profile=profiles.V3)
    assert await session.scalar(
        sa.select(RequirementExtractionRun.extractor_version).where(
            RequirementExtractionRun.id == canary.id)
    ) == "tender-requirements-v3", "el run antic no es toca"

