"""Lectura de les respostes del portal de la PSCP (`pscp_portal.parser`), sense xarxa.

Les respostes son les enregistrades de Pineda de Mar, Parc Tauli i Cornella
(`tests/fixtures/pscp_portal`), amb els textos lliures substituits per text de prova.
"""

from __future__ import annotations

import copy
import json

import pytest

from app.ingestion.pscp_portal import LICITACIO_PHASE
from app.ingestion.pscp_portal.parser import (
    EMPTY_MD5,
    PortalParseError,
    PublicationRef,
    canonical_download_url,
    clean_text,
    download_url,
    expected_md5,
    generated_url,
    is_generated_url,
    parse_answer,
    parse_board,
    parse_download_url,
    parse_navigation,
    parse_notice,
    parse_page,
    parse_publication,
    parse_source_url,
    sync_target,
)
from tests.pscp_portal_support import (
    CORNELLA,
    FIXTURES,
    PARC_TAULI,
    PINEDA,
    PINEDA_CURRENT,
    PINEDA_ORIGINAL,
)

MD5 = "E4639365A4DC1E0A61335A121F885D16"


def fixture(folder: str, name: str):
    return json.loads((FIXTURES / folder / f"{name}.json").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# URL                                                                          #
# --------------------------------------------------------------------------- #


def test_the_source_url_gives_the_expedient_and_the_publication():
    url = f"https://contractaciopublica.cat/ca/detall-publicacio/{PINEDA}/{PINEDA_CURRENT}"
    assert parse_source_url(url) == PublicationRef(PINEDA, PINEDA_CURRENT)
    assert parse_source_url(url.replace("/ca/", "/es/") + "?x=1") == PublicationRef(
        PINEDA, PINEDA_CURRENT
    )
    assert parse_source_url(url.replace(PINEDA, PINEDA.upper())) == PublicationRef(
        PINEDA, PINEDA_CURRENT
    )
    assert parse_source_url(url.replace("https://", "http://")) is None
    assert parse_source_url("https://evilcontractaciopublica.cat/ca/detall-publicacio/x/1") is None
    assert parse_source_url(None) is None


def test_download_urls_carry_the_published_md5_and_compare_canonically():
    url = download_url(302674550, MD5.lower())
    assert url == f"https://contractaciopublica.cat/portal-api/descarrega-document/302674550/{MD5}"
    assert parse_download_url(url) == (302674550, MD5)
    assert expected_md5(url.replace(MD5, MD5.lower())) == MD5
    written_otherwise = url.replace("://", "://www.").replace(MD5, MD5.lower())
    assert canonical_download_url(written_otherwise) == url
    without_md5 = "https://contractaciopublica.cat/portal-api/descarrega-document/1/x"
    assert expected_md5(without_md5) is None
    assert expected_md5("operator-upload://abc") is None


def test_generated_urls_are_never_download_urls():
    url = generated_url(PINEDA, "preguntes-respostes")
    assert url == f"pscp-portal://{PINEDA}/preguntes-respostes"
    assert generated_url(PINEDA, "avis", 7) == f"pscp-portal://{PINEDA}/avis/7"
    assert is_generated_url(url) and not is_generated_url(download_url(1, MD5))
    assert expected_md5(url) is None


def test_only_a_public_tender_with_its_pscp_publication_can_be_synced():
    url = f"https://contractaciopublica.cat/ca/detall-publicacio/{PINEDA}/{PINEDA_CURRENT}"
    assert sync_target(None, url, PINEDA) == (PublicationRef(PINEDA, PINEDA_CURRENT), None)
    assert sync_target(None, url, PINEDA.upper()) == (PublicationRef(PINEDA, PINEDA_CURRENT), None)
    assert sync_target("empresa", url, PINEDA) == (None, "private_tender")
    assert sync_target(None, "https://contrataciondelestado.es/x", PINEDA) == (
        None,
        "no_pscp_publication",
    )
    assert sync_target(None, url, None) == (None, "no_pscp_publication")
    assert sync_target(None, url, PARC_TAULI) == (None, "pscp_id_mismatch")


# --------------------------------------------------------------------------- #
# Publicacio                                                                   #
# --------------------------------------------------------------------------- #


def test_pineda_current_publication_lists_every_group_of_documents():
    publication = parse_publication(fixture("pineda", f"publicacio_{PINEDA_CURRENT}"))

    assert publication.expedient_id == PINEDA
    assert publication.publication_id == PINEDA_CURRENT
    assert publication.phase == LICITACIO_PHASE
    assert publication.code == "5125/2026"
    assert publication.amendment_reason.startswith("Ampliaci")
    assert publication.deadline.isoformat() == "2026-11-04T22:59:59+00:00"
    assert publication.phase_publication(LICITACIO_PHASE) == PINEDA_CURRENT
    assert [a.publication_id for a in publication.amendments] == [PINEDA_ORIGINAL, PINEDA_CURRENT]
    assert [a.kind for a in publication.amendments] == [None, 1008173]
    groups = [file.group for file in publication.files]
    assert groups[:4] == [
        "plecsDeClausulesAdministratives",
        "plecsDePrescripcionsTecniques",
        "memoriaJustificativaContracte",
        "documentsAprovacio",
    ]
    assert groups.count("altresDocuments") == 7
    assert len(publication.files) == 11
    assert all(len(file.md5) == 32 and file.url.endswith(file.md5) for file in publication.files)
    assert publication.files[0].title == "PCAP segellat.pdf"
    assert publication.unknown_groups == ()


def test_parc_tauli_reaches_the_licitacio_phase_among_several():
    publication = parse_publication(fixture("parc_tauli", "publicacio_300899451"))

    assert [(p.phase, p.active) for p in publication.phases] == [(1000040, True), (1000038, False)]
    assert publication.phase_publication(LICITACIO_PHASE) == 300899451
    assert publication.phase_publication(1000038) == 300789814
    assert publication.amendment_reason is None
    assert len(publication.files) == 5


def test_documents_outside_the_known_groups_are_read_as_well():
    data = fixture("pineda", f"publicacio_{PINEDA_CURRENT}")
    publicacio = data["dades"]["publicacio"]
    extra = copy.deepcopy(publicacio["dadesPublicacio"]["altresDocuments"]["docs"][0])
    extra["id"] = 999
    publicacio["lots"] = [{"dadesPublicacio": {"annexosLot": {"docs": [extra]}}}]

    publication = parse_publication(data)

    assert publication.unknown_groups == ("annexosLot",)
    assert publication.files[-1].document_id == 999


def test_the_same_document_in_two_places_is_one_file():
    data = fixture("pineda", f"publicacio_{PINEDA_CURRENT}")
    groups = data["dades"]["publicacio"]["dadesPublicacio"]
    groups["altresDocuments"]["docs"].append(groups["plecsDeClausulesAdministratives"]["docs"][0])

    assert len(parse_publication(data).files) == 11


@pytest.mark.parametrize(
    "breaking",
    [
        lambda data: data.pop("expedientId"),
        lambda data: data.update(publicacioId="x"),
        lambda data: data["dades"].pop("publicacio"),
        lambda data: data["dades"]["publicacio"]["dadesPublicacio"][
            "plecsDeClausulesAdministratives"
        ]["docs"][0].update(hash=None),
        lambda data: data["dades"]["publicacio"]["dadesPublicacio"]["altresDocuments"].update(
            docs="no es una llista"
        ),
        lambda data: data.update(navegacioFases=[{"fase": "x"}]),
    ],
)
def test_a_publication_that_is_not_complete_is_refused(breaking):
    data = fixture("pineda", f"publicacio_{PINEDA_CURRENT}")
    breaking(data)
    with pytest.raises(PortalParseError):
        parse_publication(data)


def test_pineda_publishes_one_empty_file():
    files = parse_publication(fixture("pineda", f"publicacio_{PINEDA_CURRENT}")).files
    empty = [file for file in files if file.is_empty]
    assert [file.title for file in empty] == ["Memoria castellano.pdf"]
    assert empty[0].md5 == EMPTY_MD5 and empty[0].size == 0


# --------------------------------------------------------------------------- #
# Tauler i llistes                                                             #
# --------------------------------------------------------------------------- #


def test_pineda_board_has_only_answers():
    board = parse_board(fixture("pineda", "tauler"))
    assert board.qa_count == 5
    assert [entry.count for entry in board.entries] == [0, 0, 0, 0, 0]


def test_cornella_board_points_at_each_single_notice():
    board = parse_board(fixture("cornella", "tauler"))
    direct = {entry.category.key: (entry.count, entry.direct_id) for entry in board.entries}
    assert board.qa_count == 9
    assert direct["avisos"] == (1, 300894146)
    assert direct["esmenes"] == (1, 300894878)
    assert direct["mesa"] == (1, 300913578)
    assert direct["recursos"] == (0, None)


def test_a_board_without_a_counter_is_refused():
    data = fixture("pineda", "tauler")
    data.pop("comptadorAvisos")
    with pytest.raises(PortalParseError):
        parse_board(data)


def test_a_page_lists_the_items_and_the_totals():
    page = parse_page(fixture("parc_tauli", "respostes_p0"))
    assert page.total_elements == 10 and page.total_pages == 1 and page.last
    assert len(page.items) == 10
    assert page.items[0].id == 300914414
    assert page.items[0].published_at.isoformat().startswith("2026-10-21T10:14:08")


# --------------------------------------------------------------------------- #
# Preguntes i respostes, avisos                                                #
# --------------------------------------------------------------------------- #


def test_an_answer_carries_its_question_answer_and_attachments():
    answer = parse_answer(fixture("pineda", "resposta_300908253"), 300908253, PINEDA)

    assert answer.question.startswith("Bon dia,\nPregunta de prova 1")
    assert answer.answer.startswith("Resposta oficial de prova 1")
    assert [file.title for file in answer.files] == ["Volumetria_voz_Ajuntament_Pineda_de_Mar.xlsx"]
    assert answer.files[0].group == "resposta"
    assert not answer.unpublished and not answer.is_amendment


def test_an_answer_of_another_expedient_is_refused():
    with pytest.raises(PortalParseError):
        parse_answer(fixture("pineda", "resposta_300908253"), 300908253, CORNELLA)


def test_a_notice_takes_its_real_category_from_the_detail():
    notice = parse_notice(fixture("cornella", "avis_300913578"), 300913578, CORNELLA, 1008176)
    assert notice.category_id == 1008175
    assert notice.title.startswith("CONVOCAT")
    assert notice.description.startswith("Text de prova")
    assert notice.files == ()


def test_clean_text_keeps_plain_text_and_strips_html():
    assert clean_text("Bon dia,\r\nPrimera linia  \r\n\r\n\r\n\r\nSegona") == (
        "Bon dia,\nPrimera linia\n\nSegona"
    )
    assert clean_text("<p>Primer &amp; segon</p><ul><li>u</li><li>dos</li></ul>") == (
        "Primer & segon\n\n- u\n- dos"
    )
    assert clean_text("a<br>b<p>c</p>d") == "a\nb\nc\nd"
    assert clean_text("<table><tr><td>Lot</td><td>1</td></tr></table>") == "Lot 1"
    assert clean_text("2 < 3 i 5 > 4") == "2 < 3 i 5 > 4"
    assert clean_text(None) == ""


def test_text_that_only_looks_like_html_is_kept():
    """Nomes les etiquetes HTML conegudes activen la neteja: el text publicat es el publicat."""
    assert clean_text("Cal indicar <CIF> al sobre B & signar-ho") == (
        "Cal indicar <CIF> al sobre B & signar-ho"
    )
    assert clean_text("Vegeu <Article 3> del PCAP") == "Vegeu <Article 3> del PCAP"


def test_numbers_must_be_plain_decimal_digits():
    data = fixture("pineda", f"publicacio_{PINEDA_CURRENT}")
    data["publicacioId"] = "²"
    with pytest.raises(PortalParseError):
        parse_publication(data)


def test_any_phase_publication_gives_its_navigation():
    """La publicacio de la fitxa pot ser d'una altra fase: nomes se'n llegeix la navegacio."""
    data = fixture("parc_tauli", "publicacio_300899451")
    data["publicacioId"] = 300789814
    data["fase"] = 1000038
    data["dades"] = {}

    navigation = parse_navigation(data)

    assert navigation.phase == 1000038
    assert navigation.phase_publication(LICITACIO_PHASE) == 300899451
    with pytest.raises(PortalParseError):
        parse_publication(data)


def test_an_amended_answer_keeps_its_amendment_links_and_versions():
    data = fixture("pineda", "resposta_300908253")
    data["dades"]["descripcioEsmena"] = "<p>Es corregeix la xifra de linies.</p>"
    data["dades"]["linkInteres"] = [{"url": "https://exemple.cat/a", "descripcio": "Annex"}]
    data["navegacioEsmenes"] = [{"publicacioId": 300907155}, {"publicacioId": 300908253}]

    answer = parse_answer(data, 300908253, PINEDA)

    assert answer.is_amendment is True
    assert answer.amendment_description == "Es corregeix la xifra de linies."
    assert answer.links == ("Annex - https://exemple.cat/a",)
    assert answer.versions == (300907155, 300908253)


def test_appeal_details_are_dated_in_catalonia_and_keep_numbers():
    data = fixture("cornella", "avis_300913578")
    data["dades"].update(
        dataResolucio="2026-10-19T22:30:00.000Z",
        suspenProcediment=True,
        sancio=1500,
        tipusRecurs=7,
    )

    notice = parse_notice(data, 300913578, CORNELLA, 1008174)

    assert dict(notice.details) == {
        "Data de resolució": "2026-10-20",
        "Suspèn el procediment": "Sí",
        "Sanció": "1500",
    }
