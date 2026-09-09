"""Quan podem confiar en el text que hem extret, i quan no.

La pregunta d'aquesta capa es estreta a proposit: no diu que exigeix el plec,
diu si el text que en tenim sembla sencer. La meitat dels tests son sobre no
avisar: un detector que crida al llop a cada pagina es un detector que ningu
mira, i llavors el dia que la formula falti de veritat tampoc el miraran.

El cas que ho motiva tot es real i esta al corpus: al plec de Madrid la formula
del criteri economic es una imatge, i el text nomes en te l'anunci i les
variables. Sense detectar-ho, el benchmark hauria comptat com a error del model
una cosa que el model no podia extreure.
"""

from __future__ import annotations

import pytest

from app.evaluation.taxonomy import ErrorCause, attribute, summarise_causes
from app.services.extraction_qa import (
    QA_VERSION,
    IssueCode,
    PageInput,
    QaStatus,
    analyse,
    combine_status,
    strip_signature_noise,
)

#: Una pagina de prosa normal, prou llarga per no disparar cap avis de densitat.
PROSE = (
    "El presente pliego tiene por objeto regular las condiciones que han de regir "
    "la contratacion del servicio, de conformidad con lo establecido en la Ley de "
    "Contratos del Sector Publico, y sin perjuicio de lo que dispongan las clausulas "
    "particulares que se detallan en los apartados siguientes de este documento. "
) * 3


def pages(*texts: str) -> list[PageInput]:
    return [PageInput(page_number=index, text=text) for index, text in enumerate(texts, start=1)]


# --------------------------------------------------------------------------- #
# Qualitat basica                                                               #
# --------------------------------------------------------------------------- #


def test_a_normal_document_is_good():
    qa = analyse(pages(PROSE, PROSE, PROSE, PROSE, PROSE))

    assert qa.status == QaStatus.GOOD
    assert qa.issues == ()
    assert qa.qa_version == QA_VERSION


def test_an_empty_page_is_reported():
    qa = analyse(pages(PROSE, PROSE, "", PROSE, PROSE))

    assert qa.status == QaStatus.WARNING
    assert qa.pages_with(IssueCode.EMPTY_PAGE) == [3]


def test_a_page_without_text_but_with_images_is_an_ocr_candidate():
    """Distingir-ho importa: una pagina sense res es una separadora, i una
    pagina amb objectes grafics i sense text es un escaneig."""
    items = pages(PROSE, PROSE, "", PROSE)
    items[2] = PageInput(page_number=3, text="", image_count=4)

    qa = analyse(items)

    assert qa.pages_with(IssueCode.IMAGE_ONLY_PAGE) == [3]
    assert qa.pages_with(IssueCode.EMPTY_PAGE) == []


def test_a_mostly_broken_document_is_poor():
    """Un avis en una pagina de cent no fa dolent un plec; la meitat, si."""
    qa = analyse(pages(PROSE, "", "", "", PROSE))

    assert qa.status == QaStatus.POOR


def test_a_page_the_parser_could_not_read_is_poor():
    items = pages(PROSE, PROSE, PROSE, PROSE)
    items[1] = PageInput(page_number=2, text="", failed=True)

    qa = analyse(items)

    assert qa.status == QaStatus.POOR
    assert qa.pages_with(IssueCode.PARSER_WARNING) == [2]


def test_replacement_characters_make_the_document_poor():
    """Codificacio mal llegida: el text hi es pero no vol dir el que sembla."""
    qa = analyse(pages(PROSE, "�" * 40 + PROSE[:200], PROSE, PROSE))

    assert qa.status == QaStatus.POOR
    assert qa.pages_with(IssueCode.UNREADABLE_CHARACTERS) == [2]


# --------------------------------------------------------------------------- #
# Formules                                                                      #
# --------------------------------------------------------------------------- #

ANNOUNCE = "La puntuacion se calculara mediante la siguiente formula:"


def test_an_announced_formula_that_is_present_does_not_warn():
    qa = analyse(
        pages(PROSE, ANNOUNCE + "\n\nPL=10 x Oferta mas baja / Oferta a valorar\n", PROSE, PROSE)
    )

    assert qa.pages_with(IssueCode.POSSIBLE_MISSING_FORMULA) == []


def test_a_formula_on_the_next_page_does_not_warn():
    """Una formula pot caure al salt de pagina i seguir sent-hi."""
    qa = analyse(pages(PROSE, PROSE + ANNOUNCE, "P = 75 x (OM/OF), siendo:\n" + PROSE, PROSE))

    assert qa.pages_with(IssueCode.POSSIBLE_MISSING_FORMULA) == []


def test_an_announced_formula_that_never_appears_is_reported():
    """El cas real: l'anunci, i despres nomes les definicions de les variables."""
    variables = (
        "P: es la puntuacion de la oferta a valorar\n"
        "PBL: es el presupuesto base de licitacion sin IVA\n"
        "OE: es la oferta correspondiente al licitador que se valora\n"
    )
    qa = analyse(pages(PROSE, PROSE + ANNOUNCE, variables + PROSE, PROSE))

    assert qa.pages_with(IssueCode.POSSIBLE_MISSING_FORMULA) == [2]
    assert qa.status == QaStatus.WARNING
    issue = next(i for i in qa.issues if i.code == IssueCode.POSSIBLE_MISSING_FORMULA)
    assert issue.detail["searched_pages"] == [2, 3, 4]


@pytest.mark.parametrize(
    "formula",
    [
        pytest.param("PL=10 x Oferta mas baja / Oferta a valorar", id="operands-en-paraules"),
        pytest.param("P = 75 x (OM/OF), siendo:", id="sigles-entre-parentesis"),
        pytest.param("P=(pm*O)/mo.", id="minuscules-dins-parentesi"),
        pytest.param("P = pm x ( 1 + log(mo/O))", id="amb-logaritme"),
        pytest.param(
            "\U0001d443 = \U0001d440\U0001d442/\U0001d442\U0001d43f x 49",
            id="lletres-matematiques-unicode",
        ),
    ],
)
def test_real_formula_shapes_are_recognised(formula):
    """Cada forma ve d'un plec del corpus. Si el detector no les reconeix,
    avisa que falta una formula que hi es, i l'avis deixa de valer res."""
    qa = analyse(pages(PROSE, ANNOUNCE + "\n" + formula + "\n" + PROSE, PROSE, PROSE))

    assert qa.pages_with(IssueCode.POSSIBLE_MISSING_FORMULA) == []


@pytest.mark.parametrize(
    "noise",
    [
        pytest.param("Expediente: 300/2025/3877", id="numero-expedient"),
        pytest.param("los servicios y/o suministros del contrato", id="conjuncio-y-o"),
        pytest.param("Fecha: 28-07-2026 13:24:38", id="data"),
        pytest.param(
            "Para la verificacion podra conectarse a "
            "http://www.munimadrid.es/CotejoCOVE.jsp?codigoVerificacion=PBB2C4OTB4VNGXJY",
            id="url-de-verificacio",
        ),
    ],
)
def test_page_furniture_is_not_mistaken_for_a_formula(noise):
    """El text que hi ha a TOTES les pagines no pot fer callar el detector.

    Es el que passava: el peu de signatura porta un signe igual seguit d'un codi
    llarg, i amb aixo la pagina de Madrid semblava que si que portava la formula.
    """
    variables = "P: es la puntuacion de la oferta a valorar\nPBL: es el presupuesto base\n"
    qa = analyse(pages(PROSE, PROSE + ANNOUNCE + "\n" + noise, variables + noise, PROSE))

    assert qa.pages_with(IssueCode.POSSIBLE_MISSING_FORMULA) == [2]


def test_the_signature_footer_is_stripped_before_any_heuristic():
    text = (
        "para ello se aplicara la siguiente formula:\n"
        "Firmado por: ARIADNA GOMEZ\n"
        "Codigo de verificacion : PBB2C4OTB4VNGXJY\n"
        "http://www-2.munimadrid.es/CotejoCOVE.jsp?codigoVerificacion=PBB2C4OTB4VNGXJY"
    )

    clean = strip_signature_noise(text)

    assert "munimadrid" not in clean
    assert "PBB2C4OTB4VNGXJY" not in clean
    assert "siguiente formula" in clean


# --------------------------------------------------------------------------- #
# Taules                                                                        #
# --------------------------------------------------------------------------- #


def test_an_announced_table_that_never_appears_is_reported():
    qa = analyse(pages(PROSE, PROSE + "se detallan a continuacion:", PROSE, PROSE))

    assert qa.pages_with(IssueCode.POSSIBLE_MISSING_TABLE) == [2]


def test_an_announced_table_with_rows_does_not_warn():
    table = "Perfil  Horas  Puntos\nAnalista  20  4\nProgramador  20  4\n"
    qa = analyse(pages(PROSE, PROSE + "se detallan a continuacion:", table + PROSE, PROSE))

    assert qa.pages_with(IssueCode.POSSIBLE_MISSING_TABLE) == []


# --------------------------------------------------------------------------- #
# Taxonomia de l'evaluator                                                      #
# --------------------------------------------------------------------------- #


def test_a_missing_formula_on_the_cited_page_is_a_source_problem():
    """La causa mana l'accio: aixo no s'arregla amb un prompt millor."""
    issues = [
        {"page_number": 70, "code": "POSSIBLE_MISSING_FORMULA", "document_name": "PCAP"}
    ]

    result = attribute(gold_page=70, source_issues=issues, document_name="PCAP")

    assert result.cause == ErrorCause.SOURCE_TEXT_QUALITY
    assert result.evidence_code == "POSSIBLE_MISSING_FORMULA"
    assert result.page_number == 70


def test_an_issue_on_another_page_does_not_excuse_the_model():
    """Sense comprovar la pagina, un sol avis justificaria tots els errors."""
    issues = [
        {"page_number": 70, "code": "POSSIBLE_MISSING_FORMULA", "document_name": "PCAP"}
    ]

    result = attribute(gold_page=27, source_issues=issues, document_name="PCAP")

    assert result.cause == ErrorCause.MODEL_ERROR


def test_an_issue_in_another_document_does_not_excuse_the_model():
    issues = [
        {"page_number": 70, "code": "POSSIBLE_MISSING_FORMULA", "document_name": "Annex"}
    ]

    result = attribute(gold_page=70, source_issues=issues, document_name="PCAP")

    assert result.cause == ErrorCause.MODEL_ERROR


def test_a_low_density_page_is_not_an_excuse_either():
    """Nomes els avisos que EXPLIQUEN una absencia poden atribuir-se a la font."""
    issues = [{"page_number": 70, "code": "DENSITY_DROP", "document_name": "PCAP"}]

    result = attribute(gold_page=70, source_issues=issues, document_name="PCAP")

    assert result.cause == ErrorCause.MODEL_ERROR


def test_the_source_is_checked_before_the_schema():
    """Si al text no hi era, no te sentit preguntar si l'esquema ho sabia desar."""
    issues = [
        {"page_number": 70, "code": "POSSIBLE_MISSING_FORMULA", "document_name": "PCAP"}
    ]

    result = attribute(
        gold_page=70,
        source_issues=issues,
        representation_unsupported="V2_AGGREGATION_UNSUPPORTED",
        document_name="PCAP",
    )

    assert result.cause == ErrorCause.SOURCE_TEXT_QUALITY


def test_a_schema_limit_is_a_representation_loss():
    result = attribute(gold_page=27, representation_unsupported="V2_AGGREGATION_UNSUPPORTED")

    assert result.cause == ErrorCause.REPRESENTATION_LOSS
    assert result.evidence_code == "V2_AGGREGATION_UNSUPPORTED"


def test_every_cause_is_always_present_in_the_summary():
    """Una fila que desapareix quan val zero fa pensar que la categoria no hi es."""
    counts = summarise_causes([attribute(gold_page=1)])

    assert set(counts) == {cause.value for cause in ErrorCause}
    assert counts["MODEL_ERROR"] == 1
    assert counts["SOURCE_TEXT_QUALITY"] == 0


# --------------------------------------------------------------------------- #
# QA no executada NO es QA bona                                                 #
# --------------------------------------------------------------------------- #


def test_no_sources_at_all_is_unknown():
    assert combine_status([]) == QaStatus.UNKNOWN


def test_a_source_without_qa_makes_the_whole_run_unknown():
    """El nucli d'aquest hardening.

    `NULL` no vol dir "cap problema": vol dir que ningu ho ha mirat. Amb un
    booleà, aixo hauria arribat al run com a `false`, indistingible de "avaluat
    i tot correcte", i el benchmark hauria atribuit al model errors que podien
    ser de la font.
    """
    assert combine_status([None]) == QaStatus.UNKNOWN
    assert combine_status(["good", None]) == QaStatus.UNKNOWN
    assert combine_status(["good", "good"]) == QaStatus.GOOD


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        pytest.param(["good"], QaStatus.GOOD, id="una-bona"),
        pytest.param(["good", "good"], QaStatus.GOOD, id="totes-bones"),
        pytest.param(["good", "warning"], QaStatus.WARNING, id="bona-i-avis"),
        pytest.param(["warning", "poor"], QaStatus.POOR, id="avis-i-pobra"),
        pytest.param(["good", "poor"], QaStatus.POOR, id="bona-i-pobra"),
        pytest.param(["warning", None], QaStatus.UNKNOWN, id="avis-i-sense-avaluar"),
        pytest.param(["poor", None], QaStatus.UNKNOWN, id="pobra-i-sense-avaluar"),
    ],
)
def test_the_worst_known_status_wins_unless_something_is_unevaluated(statuses, expected):
    """Mana la pitjor de les avaluades, i `unknown` mana sobre totes.

    Que `unknown` guanyi fins i tot a `poor` es deliberat: una etiqueta que digui
    `poor` fa pensar que la QA va cobrir totes les fonts. Si no ho va fer, la
    xifra no pot presentar-se com una avaluacio del conjunt. El detall del que
    si que s'ha trobat no es perd: viatja a la llista d'issues del run.
    """
    assert combine_status(statuses) == expected


def test_an_unrecognised_status_is_treated_as_unknown():
    """Un valor que no sabem llegir tampoc autoritza cap conclusio."""
    assert combine_status(["good", "excellent"]) == QaStatus.UNKNOWN


def test_the_evaluator_does_not_blame_the_model_when_qa_never_ran():
    """Culpar el model sense haver comprovat la font es la conclusio a evitar."""
    result = attribute(gold_page=70, source_quality_status="unknown")

    assert result.cause == ErrorCause.UNDETERMINED
    assert result.evidence_code == "SOURCE_QUALITY_NOT_EVALUATED"
    assert result.cause != ErrorCause.MODEL_ERROR


@pytest.mark.parametrize("status", ["good", "warning", "poor"])
def test_an_evaluated_source_can_still_blame_the_model(status):
    """Si la QA s'ha executat i a AQUESTA pagina no hi ha res, el model respon.

    Un avis en una altra pagina del mateix document no l'excusa: per aixo la
    comparacio es per pagina.
    """
    result = attribute(gold_page=70, source_quality_status=status)

    assert result.cause == ErrorCause.MODEL_ERROR


def test_a_schema_limit_is_attributable_even_without_qa():
    """Es demostrable amb el que hem desat: el model ho va trobar i no ho sabiem
    guardar. Aixo no depen de si la font s'ha avaluat."""
    result = attribute(
        gold_page=70,
        source_quality_status="unknown",
        representation_unsupported="V2_AGGREGATION_UNSUPPORTED",
    )

    assert result.cause == ErrorCause.REPRESENTATION_LOSS


# --------------------------------------------------------------------------- #
# Backfill i snapshot del run, contra base de dades                             #
# --------------------------------------------------------------------------- #

async def _extraction(session, *, texts: list[str], revision: int = 1, current: bool = True):
    """Una extraccio amb les seves pagines, sense QA calculada encara."""
    import hashlib
    import uuid

    from app.models.enums import ExtractionQuality, ExtractionStatus, PageExtractionStatus
    from app.models.tender_documents import (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentPage,
    )

    digest = hashlib.sha256(f"{uuid.uuid4()}".encode()).hexdigest()
    content = TenderDocumentContent(
        sha256=digest,
        storage_key=f"test/{digest[:16]}.pdf",
        file_size=len("".join(texts)),
        mime_type="application/pdf",
        is_text_extractable=True,
    )
    session.add(content)
    await session.flush()

    extraction = TenderDocumentExtraction(
        content_id=content.id,
        parser_version="test",
        revision_number=revision,
        status=ExtractionStatus.EXTRACTED,
        quality=ExtractionQuality.GOOD,
        page_count=len(texts),
        extracted_page_count=len(texts),
        empty_page_count=0,
        text_char_count=sum(len(t) for t in texts),
        ocr_candidate=False,
        is_current=current,
    )
    session.add(extraction)
    await session.flush()
    for number, text in enumerate(texts, start=1):
        session.add(
            TenderDocumentPage(
                extraction_id=extraction.id,
                page_number=number,
                text=text,
                char_count=len(text),
                text_hash="h" * 64,
                status=PageExtractionStatus.EXTRACTED,
            )
        )
    await session.flush()
    return content, extraction


@pytest.mark.anyio
async def test_the_backfill_only_touches_what_has_no_qa(session):
    from app.services.extraction_qa_backfill import backfill

    _content, pending = await _extraction(session, texts=[PROSE, PROSE, PROSE, PROSE])
    _content2, already = await _extraction(session, texts=[PROSE, PROSE, PROSE, PROSE])
    already.qa_status = "poor"
    already.qa_version = "una-versio-antiga"
    await session.commit()

    report = await backfill(session)

    assert report.processed == 1
    assert report.skipped == 0, "les ja avaluades ni s'arrepleguen"
    await session.refresh(pending)
    await session.refresh(already)
    assert pending.qa_status == "good"
    assert already.qa_status == "poor", "una avaluacio existent no es toca"
    assert already.qa_version == "una-versio-antiga"


@pytest.mark.anyio
async def test_running_the_backfill_twice_changes_nothing(session):
    """Idempotent: la segona passada no ha de trobar feina ni duplicar res."""
    import sqlalchemy as sa

    from app.models.tender_documents import TenderExtractionIssue
    from app.services.extraction_qa_backfill import backfill

    await _extraction(session, texts=[PROSE, PROSE, "", PROSE])
    await session.commit()

    first = await backfill(session)
    issues_after_first = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderExtractionIssue)
    )
    second = await backfill(session)
    issues_after_second = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderExtractionIssue)
    )

    assert first.processed == 1
    assert second.processed == 0
    assert issues_after_first == issues_after_second > 0


@pytest.mark.anyio
async def test_force_recalculates_without_duplicating_issues(session):
    import sqlalchemy as sa

    from app.models.tender_documents import TenderExtractionIssue
    from app.services.extraction_qa_backfill import backfill

    await _extraction(session, texts=[PROSE, PROSE, "", PROSE])
    await session.commit()
    await backfill(session)
    before = await session.scalar(
        sa.select(sa.func.count()).select_from(TenderExtractionIssue)
    )

    report = await backfill(session, force=True)
    after = await session.scalar(sa.select(sa.func.count()).select_from(TenderExtractionIssue))

    assert report.processed == 1
    assert after == before, "reescriure no pot acumular"


@pytest.mark.anyio
async def test_a_new_revision_gets_its_own_quality(session):
    """Re-extreure no reescriu la qualitat d'abans: son revisions diferents.

    Es el que despres permet dir si canviar de parser ha millorat res.
    """
    from app.services.extraction_qa_backfill import backfill

    _content, first = await _extraction(session, texts=[PROSE, "", "", ""], revision=1)
    await session.commit()
    await backfill(session)
    await session.refresh(first)

    first.is_current = False
    _content2, second = await _extraction(
        session, texts=[PROSE, PROSE, PROSE, PROSE], revision=2
    )
    await session.commit()
    await backfill(session)
    await session.refresh(first)
    await session.refresh(second)

    assert first.qa_status == "poor"
    assert second.qa_status == "good"
    assert first.revision_number != second.revision_number


@pytest.mark.anyio
async def test_a_dry_run_writes_nothing(session):
    import sqlalchemy as sa

    from app.models.tender_documents import TenderExtractionIssue
    from app.services.extraction_qa_backfill import backfill

    _content, extraction = await _extraction(session, texts=[PROSE, PROSE, "", PROSE])
    await session.commit()

    report = await backfill(session, dry_run=True)

    assert report.processed == 1
    await session.refresh(extraction)
    assert extraction.qa_status is None
    assert (
        await session.scalar(sa.select(sa.func.count()).select_from(TenderExtractionIssue))
    ) == 0


async def _analyzable(extraction, pages_text: list[str]):
    """La vista que el run te d'un document, sense tocar la base de dades."""
    from app.services.requirement_chunking import AnalyzableDocument, PageRef

    return AnalyzableDocument(
        tender_document_id="doc",
        document_name="PCAP",
        document_type="administrative_clauses",
        extraction_id=str(extraction.id),
        revision_number=extraction.revision_number,
        parser_version="test",
        content_sha256="x" * 64,
        pages=tuple(
            PageRef(page_id=f"p{number}", page_number=number, text=text)
            for number, text in enumerate(pages_text, start=1)
        ),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("qa_statuses", "expected"),
    [
        pytest.param([None], "unknown", id="sense-qa"),
        pytest.param(["good"], "good", id="bona"),
        pytest.param(["good", "warning"], "warning", id="bona-i-avis"),
        pytest.param(["warning", "poor"], "poor", id="avis-i-pobra"),
        pytest.param(["good", None], "unknown", id="bona-i-sense-qa"),
    ],
)
async def test_the_run_snapshot_records_the_source_quality(session, qa_statuses, expected):
    """El run ha de dir amb quina qualitat de font es va fer, sense ambiguitat.

    El cas que ho justifica es el primer: una font sense QA no pot arribar al run
    com si fos bona. Si hi arribes, el benchmark atribuiria al model errors que
    ningu ha comprovat que ho siguin.
    """
    from app.services.requirement_analysis import _source_quality_snapshot

    documents = []
    for status in qa_statuses:
        _content, extraction = await _extraction(session, texts=[PROSE, PROSE, PROSE, PROSE])
        extraction.qa_status = status
        documents.append(await _analyzable(extraction, [PROSE] * 4))
    await session.commit()

    status, _issues = await _source_quality_snapshot(session, documents)

    assert status == expected
    assert status != "good" or None not in qa_statuses, "sense QA no pot ser `good`"


@pytest.mark.anyio
async def test_the_run_snapshot_carries_the_issues_of_the_pages_it_reads(session):
    from app.models.tender_documents import TenderExtractionIssue
    from app.services.requirement_analysis import _source_quality_snapshot

    _content, extraction = await _extraction(session, texts=[PROSE, PROSE, PROSE, PROSE])
    extraction.qa_status = "warning"
    session.add(
        TenderExtractionIssue(
            extraction_id=extraction.id, page_number=2, code="POSSIBLE_MISSING_FORMULA"
        )
    )
    # Una pagina que el run NO llegeix: no ha d'aparèixer al snapshot.
    session.add(
        TenderExtractionIssue(
            extraction_id=extraction.id, page_number=9, code="POSSIBLE_MISSING_FORMULA"
        )
    )
    await session.commit()

    status, issues = await _source_quality_snapshot(
        session, [await _analyzable(extraction, [PROSE] * 4)]
    )

    assert status == "warning"
    assert [issue["page_number"] for issue in issues] == [2]
