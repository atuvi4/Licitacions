"""Documents de text per al que el portal publica com a dades i no com a fitxer.

Les preguntes i respostes oficials, els avisos del tauler i les esmenes de l'anunci no son
cap fitxer descarregable: son camps de les respostes JSON. Aqui es converteixen en un text
pla que la capa documental llegeix com qualsevol altre document (una sola pagina,
`plaintext-v1`) i que l'analisi pot citar.

**Deterministes.** El mateix contingut publicat dona sempre els mateixos bytes: res de l'hora
de la lectura, ordre estable (per data i referencia, i els fitxers per grup, titol i id, sigui
quin sigui l'ordre de l'API), salts de linia normalitzats. Aixi una sincronitzacio sense
canvis no crea cap versio nova del document, i una pregunta nova en crea exactament una.
Els textos de les preguntes, respostes i avisos son els publicats (nomes s'hi treuen les
etiquetes HTML); el que hi afegeix Tuvi es la capcalera i les etiquetes de cada bloc.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.ingestion.pscp_portal import NOTICE_RENDERER, QA_RENDERER
from app.ingestion.pscp_portal.parser import (
    CATEGORY_BY_ID,
    PUBLICATION_GROUPS,
    Answer,
    Notice,
    PortalFile,
    Publication,
)
from app.tenders.deadline import DEADLINE_TIME_ZONE

QA_TITLE = "Preguntes i respostes (PSCP)"
NOTICE_TITLE = "Avís del tauler (PSCP)"
AMENDMENT_TITLE = "Esmena de la publicació (PSCP)"
SEPARATOR = "-" * 40
_SOURCE = "Font: Plataforma de Serveis de Contractació Pública (contractaciopublica.cat)"
_NAME_MAX = 500
_GROUP_ORDER = {group: index for index, group in enumerate(PUBLICATION_GROUPS)}


def _when(moment: datetime) -> str:
    """Data i hora oficials (Europe/Madrid)."""
    return moment.astimezone(DEADLINE_TIME_ZONE).strftime("%Y-%m-%d %H:%M") + " (hora de Catalunya)"


def _day(moment: datetime | None) -> str:
    return moment.astimezone(DEADLINE_TIME_ZONE).date().isoformat() if moment else "sense data"


def _encode(lines: Iterable[str]) -> bytes:
    text = "\n".join(lines).strip() + "\n"
    return text.encode("utf-8")


def _header(title: str, publication: Publication, origin: str, renderer: str) -> list[str]:
    lines = [title, ""]
    if publication.code:
        lines.append(f"Expedient: {publication.code}")
    if publication.title:
        lines.append(f"Licitació: {' '.join(publication.title.split())}")
    lines.append(f"{_SOURCE}, {origin}.")
    lines.append(
        f"Document generat per Tuvi ({renderer}) a partir de les dades publicades al portal; "
        "el text de cada bloc és el publicat."
    )
    return lines


def _file_order(file: PortalFile) -> tuple:
    return (_GROUP_ORDER.get(file.group, len(_GROUP_ORDER)), file.title, file.document_id)


def _files_block(title: str, files: Iterable[PortalFile]) -> list[str]:
    names = [f"- {file.title}" for file in sorted(files, key=_file_order)]
    return ["", f"{title}:", *names] if names else []


def _links_block(links: Iterable[str]) -> list[str]:
    items = [f"- {link}" for link in links]
    return ["", "Enllaços d'interès:", *items] if items else []


def _clipped(text: str) -> str:
    return text if len(text) <= _NAME_MAX else text[: _NAME_MAX - 1].rstrip() + "…"


def _order(moment: datetime | None, ident: int) -> tuple:
    return (moment is None, moment or datetime.min, ident)


# --------------------------------------------------------------------------- #
# Preguntes i respostes (`pscp-qa-v1`)                                         #
# --------------------------------------------------------------------------- #


def render_answers(
    publication: Publication,
    answers: Iterable[Answer],
    *,
    excluded: int = 0,
    superseded: Mapping[int, int] | None = None,
) -> bytes:
    """«Preguntes i respostes (PSCP)»: les respostes de la licitacio, de la mes antiga ençà.

    `excluded`: respostes publicades abans de l'anunci de licitacio (fases anteriors de
    l'expedient), que no hi son i es diu. `superseded`: resposta -> la que la substitueix.
    """
    ordered = sorted(answers, key=lambda answer: _order(answer.published_at, answer.id))
    replaced_by = dict(superseded or {})
    lines = _header(
        QA_TITLE,
        publication,
        "preguntes i respostes oficials del tauler de la licitació",
        QA_RENDERER,
    )
    lines.append(f"Respostes publicades: {len(ordered)}")
    if excluded:
        lines.append(
            f"No s'hi inclouen {excluded} respostes publicades abans de l'anunci de licitació "
            "(fases anteriors de l'expedient)."
        )
    for number, answer in enumerate(ordered, start=1):
        when = (
            f"publicada el {_when(answer.published_at)}"
            if answer.published_at
            else "sense data de publicació"
        )
        lines += ["", SEPARATOR, "", f"{number}. Resposta {when} (referència {answer.id})"]
        if answer.id in replaced_by:
            lines.append(
                f"(Versió substituïda: la vigent és la referència {replaced_by[answer.id]}.)"
            )
        if answer.is_amendment:
            lines.append("(Resposta esmenada pel mateix òrgan.)")
        lines += ["", "Pregunta:", answer.question or "(sense text)"]
        lines += ["", "Resposta:", answer.answer or "(sense resposta publicada)"]
        if answer.amendment_description:
            lines += ["", "Esmena:", answer.amendment_description]
        lines += _links_block(answer.links)
        lines += _files_block("Adjunts de la resposta", answer.files)
    return _encode(lines)


# --------------------------------------------------------------------------- #
# Avisos del tauler i esmenes (`pscp-avis-v1`)                                 #
# --------------------------------------------------------------------------- #


def notice_name(notice: Notice) -> str:
    if notice.title:
        return _clipped(f"{NOTICE_TITLE}: {notice.title}")
    return f"{NOTICE_TITLE} (referència {notice.id})"


def _category_label(category_id: int) -> str:
    category = CATEGORY_BY_ID.get(category_id)
    return category.label if category else f"Altres avisos (codi {category_id})"


def render_notice(publication: Publication, notice: Notice) -> bytes:
    """Un avis del tauler: categoria, data, text publicat, dades, enllacos i adjunts."""
    lines = _header(
        f"{NOTICE_TITLE}: {notice.title}" if notice.title else NOTICE_TITLE,
        publication,
        "tauler d'avisos de la licitació",
        NOTICE_RENDERER,
    )
    lines.append(f"Categoria: {_category_label(notice.category_id)}")
    if notice.published_at:
        lines.append(f"Publicat el {_when(notice.published_at)} (referència {notice.id})")
    else:
        lines.append(f"Sense data de publicació (referència {notice.id})")
    lines += ["", notice.description or "(sense text publicat)"]
    if notice.amendment_description:
        lines += ["", "Esmena:", notice.amendment_description]
    if notice.details:
        lines += ["", "Dades:", *(f"- {label}: {value}" for label, value in notice.details)]
    lines += _links_block(notice.links)
    lines += _files_block("Adjunts de l'avís", notice.files)
    return _encode(lines)


@dataclass(frozen=True, slots=True)
class Amendment:
    """Una versio esmenada de l'anunci de licitacio, amb la versio que substitueix."""

    version: Publication
    previous: Publication


def amendment_name(amendment: Amendment) -> str:
    reason = amendment.version.amendment_reason
    tail = " ".join(reason.split()) if reason else _day(amendment.version.published_at)
    return _clipped(f"{AMENDMENT_TITLE}: {tail}")


def _deadline(moment: datetime | None) -> str:
    return _when(moment) if moment else "no publicat"


def render_amendment(amendment: Amendment) -> bytes:
    """Una esmena: motiu, termini i documents nous o retirats respecte de l'anterior."""
    version, previous = amendment.version, amendment.previous
    lines = _header(
        AMENDMENT_TITLE,
        version,
        "versions de l'anunci de licitació",
        NOTICE_RENDERER,
    )
    when = f"publicada el {_when(version.published_at)}" if version.published_at else "sense data"
    lines.append(
        f"Publicació esmenada: {version.publication_id}, {when} "
        f"(substitueix la {previous.publication_id})"
    )
    lines += ["", f"Motiu de l'esmena: {version.amendment_reason or '(no publicat)'}"]
    if version.deadline != previous.deadline:
        lines.append(
            "Termini de presentació: abans "
            f"{_deadline(previous.deadline)}; ara {_deadline(version.deadline)}"
        )
    elif version.deadline is not None:
        lines.append(f"Termini de presentació (sense canvis): {_when(version.deadline)}")
    # Per contingut (l'MD5 publicat) i no per identificador: en esmenar, el portal torna a
    # publicar els mateixos fitxers amb identificadors nous.
    before = {file.md5 for file in previous.files}
    after = {file.md5 for file in version.files}
    lines += _files_block(
        "Documents nous en aquesta esmena",
        (file for file in version.files if file.md5 not in before),
    )
    lines += _files_block(
        "Documents retirats en aquesta esmena",
        (file for file in previous.files if file.md5 not in after),
    )
    lines += _files_block("Documents de la publicació en aquesta versió", version.files)
    return _encode(lines)
