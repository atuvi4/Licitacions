"""Documents generats a partir del portal (`pscp_portal.render`): deterministes i llegibles."""

from __future__ import annotations

import json
from dataclasses import replace

from app.ingestion.pscp_portal import NOTICE_RENDERER, QA_RENDERER
from app.ingestion.pscp_portal.parser import (
    parse_answer,
    parse_notice,
    parse_page,
    parse_publication,
)
from app.ingestion.pscp_portal.render import (
    QA_TITLE,
    Amendment,
    amendment_name,
    notice_name,
    render_amendment,
    render_answers,
    render_notice,
)
from tests.pscp_portal_support import (
    CORNELLA,
    FIXTURES,
    PINEDA,
    PINEDA_CURRENT,
    PINEDA_ORIGINAL,
)


def fixture(folder: str, name: str):
    return json.loads((FIXTURES / folder / f"{name}.json").read_text(encoding="utf-8"))


def pineda_answers():
    listed = parse_page(fixture("pineda", "respostes_p0")).items
    return [
        parse_answer(fixture("pineda", f"resposta_{item.id}"), item.id, PINEDA) for item in listed
    ]


def pineda_publication(publication_id: int = PINEDA_CURRENT):
    return parse_publication(fixture("pineda", f"publicacio_{publication_id}"))


def test_answers_render_in_publication_order_whatever_the_input_order():
    answers = pineda_answers()
    publication = pineda_publication()

    text = render_answers(publication, answers)

    assert text == render_answers(publication, list(reversed(answers)))
    decoded = text.decode("utf-8")
    assert decoded.startswith(f"{QA_TITLE}\n\nExpedient: 5125/2026\n")
    assert QA_RENDERER in decoded
    assert "Respostes publicades: 5" in decoded
    references = [line for line in decoded.splitlines() if "(referència " in line]
    assert [line.split("(referència ")[1].rstrip(")") for line in references] == [
        "300907155",
        "300907157",
        "300907165",
        "300907167",
        "300908253",
    ]
    assert references[0].startswith("1. Resposta publicada el 2026-10-13 14:42 (hora de Catalunya)")
    assert "Adjunts de la resposta:\n- Volumetria_voz_Ajuntament_Pineda_de_Mar.xlsx" in decoded
    assert decoded.endswith("\n") and not decoded.endswith("\n\n")
    assert "\r" not in decoded
    assert "No s'hi inclouen" not in decoded


def test_answers_of_earlier_phases_are_said_and_superseded_versions_are_marked():
    answers = pineda_answers()
    original, amended = (
        answers[0],
        replace(
            answers[1],
            is_amendment=True,
            amendment_description="Es corregeix la resposta anterior.",
            links=("Annex - https://exemple.cat/a",),
        ),
    )

    decoded = render_answers(
        pineda_publication(),
        [original, amended],
        excluded=6,
        superseded={original.id: amended.id},
    ).decode("utf-8")

    assert (
        "No s'hi inclouen 6 respostes publicades abans de l'anunci de licitació "
        "(fases anteriors de l'expedient)."
    ) in decoded
    assert f"(Versió substituïda: la vigent és la referència {amended.id}.)" in decoded
    assert "(Resposta esmenada pel mateix òrgan.)" in decoded
    assert "Esmena:\nEs corregeix la resposta anterior." in decoded
    assert "Enllaços d'interès:\n- Annex - https://exemple.cat/a" in decoded


def test_the_order_of_the_files_in_the_api_does_not_change_the_text():
    answer = next(item for item in pineda_answers() if item.files)
    files = (*answer.files, replace(answer.files[0], document_id=1, title="Annex A.pdf"))
    forwards = render_answers(pineda_publication(), [replace(answer, files=files)])
    backwards = render_answers(pineda_publication(), [replace(answer, files=files[::-1])])
    assert forwards == backwards


def test_every_question_and_answer_is_kept_verbatim():
    decoded = render_answers(pineda_publication(), pineda_answers()).decode("utf-8")
    for answer in pineda_answers():
        assert f"Pregunta:\n{answer.question}\n" in decoded
        assert f"Resposta:\n{answer.answer}" in decoded


def test_a_new_answer_changes_the_text_and_nothing_else_does():
    answers = pineda_answers()
    publication = pineda_publication()
    assert render_answers(publication, answers[:4]) != render_answers(publication, answers)
    assert render_answers(publication, answers) == render_answers(pineda_publication(), answers)


def test_a_notice_renders_category_date_text_and_name():
    notice = parse_notice(fixture("cornella", "avis_300913578"), 300913578, CORNELLA, 1008176)
    publication = parse_publication(fixture("cornella", "publicacio_300894911"))

    decoded = render_notice(publication, notice).decode("utf-8")

    assert decoded.startswith("Avís del tauler (PSCP): CONVOCAT")
    assert "Categoria: Avisos relacionats amb la mesa" in decoded
    assert "Publicat el 2026-10-20 12:24 (hora de Catalunya) (referència 300913578)" in decoded
    assert NOTICE_RENDERER in decoded
    assert notice.description in decoded
    assert notice_name(notice).startswith("Avís del tauler (PSCP): CONVOCAT")


def test_a_notice_without_title_date_or_known_category_still_reads_well():
    notice = parse_notice(fixture("cornella", "avis_300913578"), 300913578, CORNELLA, 1008176)
    bare = replace(notice, title="", published_at=None, category_id=1008177)
    publication = parse_publication(fixture("cornella", "publicacio_300894911"))

    decoded = render_notice(publication, bare).decode("utf-8")

    assert "Categoria: Altres avisos (codi 1008177)" in decoded
    assert "Sense data de publicació (referència 300913578)" in decoded
    assert "data no publicada" not in decoded
    assert notice_name(bare) == "Avís del tauler (PSCP) (referència 300913578)"


def test_an_amendment_says_what_changed_against_the_previous_version():
    amendment = Amendment(
        version=pineda_publication(PINEDA_CURRENT), previous=pineda_publication(PINEDA_ORIGINAL)
    )

    decoded = render_amendment(amendment).decode("utf-8")

    assert decoded.startswith("Esmena de la publicació (PSCP)\n")
    assert f"Publicació esmenada: {PINEDA_CURRENT}" in decoded
    assert f"(substitueix la {PINEDA_ORIGINAL})" in decoded
    assert "Motiu de l'esmena: Ampliació termini presentació ofertes." in decoded
    assert (
        "Termini de presentació: abans 2026-10-14 23:59 (hora de Catalunya); "
        "ara 2026-11-04 23:59 (hora de Catalunya)"
    ) in decoded
    # L'esmena torna a publicar els 10 fitxers amb identificadors nous i els mateixos bytes:
    # nomes la resolucio d'ampliacio del termini es nova.
    block = decoded.split("Documents nous en aquesta esmena:\n")[1].split("\n\n")[0]
    assert block.splitlines() == ["- Resolucio_ampliacio_termini.pdf"]
    assert "Documents retirats en aquesta esmena" not in decoded
    listed = decoded.split("Documents de la publicació en aquesta versió:\n")[1].splitlines()
    assert len(listed) == 11
    assert amendment_name(amendment) == (
        "Esmena de la publicació (PSCP): Ampliació termini presentació ofertes."
    )
