"""El gold v2 candidat: que hereta, que proposa i que es nega a inventar.

La pregunta que aquests tests contesten no es "el codi funciona". Es una altra:
**es pot confiar en el que aquest dataset afirma?** Un gold que diu mes del que
una persona ha verificat converteix qualsevol benchmark posterior en teatre, i
la manera com falla es silenciosa: les xifres surten i semblen bones.

Per aixo la major part d'aquests tests son sobre el que NO hi ha d'haver.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.evaluation.dataset import AnnotationStatus, dataset_path, load_dataset
from app.evaluation.dataset_v2 import (
    GOLD_SCHEMA_VERSION,
    AnnotationOrigin,
    BenchmarkDimension,
    GoldDatasetV2,
    GoldRequirementV2,
    dimensions_of,
    load_dataset_v2,
    load_gold,
)
from app.evaluation.gold_v2 import (
    CANDIDATE_DATASET,
    NOT_MEASURABLE,
    SOURCE_DATASET,
    build_candidate,
    check_quotes,
    coverage_payload,
    render_coverage_markdown,
)
from app.evaluation.gold_v2_proposals import ADDITIONS, PROPOSALS
from app.evaluation.refinements import (
    SUPERSEDE_KEY,
    RefinementConflict,
    check_no_double_counting,
)
from app.evaluation.review_page import apply_decisions
from app.models.enums import (
    AggregationType,
    InsuranceLimitKind,
    RequirementModality,
    RequirementObligation,
)
from app.services.evidence_validation import locate_quote
from tests.fixtures.gold_v2_pages import GOLD_V2_PAGES


@pytest.fixture
def source():
    return load_dataset(SOURCE_DATASET)


@pytest.fixture
def candidate(source):
    return build_candidate(source).dataset


@pytest.fixture
def committed():
    """El fitxer que hi ha al repositori, no el que es genera al vol."""
    return load_dataset_v2(CANDIDATE_DATASET)


def _texts(dataset: GoldDatasetV2) -> dict[tuple[str, str, int], str]:
    """Els textos congelats, amb la forma que espera `check_quotes`."""
    return dict(GOLD_V2_PAGES)


def _item(dataset: GoldDatasetV2, item_id: str):
    case_id, gold_id = item_id.split("::")
    case = dataset.case(case_id)
    assert case is not None, f"cas inexistent: {case_id}"
    for item in [*case.requirements, *case.award_criteria]:
        if item.gold_id == gold_id:
            return item
    raise AssertionError(f"anotacio inexistent: {item_id}")


# --------------------------------------------------------------------------- #
# El gold d'origen no es toca                                                   #
# --------------------------------------------------------------------------- #


def test_the_reviewed_v1_dataset_is_never_modified(source):
    """Una versio antiga del gold ha de poder-se citar per sempre.

    Un benchmark diu "mesurat contra gold-v1-reviewed". Si el fitxer canvia
    despres, aquella frase deixa de voler dir res.
    """
    before = dataset_path(SOURCE_DATASET).read_text(encoding="utf-8")
    in_memory = source.model_dump(mode="json")

    result = build_candidate(source)

    after = dataset_path(SOURCE_DATASET).read_text(encoding="utf-8")
    assert before == after
    # Ni el fitxer ni l'objecte: construir el candidat no ha de deixar cap
    # rastre a l'origen, ni tan sols una llista compartida per referencia.
    assert source.model_dump(mode="json") == in_memory
    assert result.dataset is not source
    raw = json.loads(after)
    assert raw["dataset_id"] == SOURCE_DATASET
    assert "gold_schema_version" not in raw, "el v1 no s'ha de convertir en v2"


def test_the_source_still_holds_eleven_verified_and_five_needs_review(source):
    """El punt de partida, fixat. Si algu el canvia, tota la resta menteix."""
    statuses = [
        item.annotation_status
        for case in source.cases
        for item in [*case.requirements, *case.award_criteria]
    ]
    assert statuses.count(AnnotationStatus.VERIFIED) == 11
    assert statuses.count(AnnotationStatus.NEEDS_REVIEW) == 5
    assert len(statuses) == 16


def test_the_v1_reader_refuses_a_v2_file():
    """Contractes diferents, lectors diferents. Fallar es la resposta correcta."""
    with pytest.raises(ValidationError):
        load_dataset(CANDIDATE_DATASET)

    assert isinstance(load_gold(CANDIDATE_DATASET), GoldDatasetV2)
    assert load_gold(SOURCE_DATASET).dataset_id == SOURCE_DATASET


# --------------------------------------------------------------------------- #
# Les 11 verificades es conserven                                               #
# --------------------------------------------------------------------------- #


def test_the_eleven_verified_annotations_survive_intact(source, candidate):
    """Mateixa evidencia, mateix significat, mateixa signatura.

    Es el que fa que la v2 no obligui a repetir una revisio ja feta.
    """
    verified_source = {
        f"{case.case_id}::{item.gold_id}": item
        for case in source.cases
        for item in [*case.requirements, *case.award_criteria]
        if item.annotation_status == AnnotationStatus.VERIFIED
    }
    assert len(verified_source) == 11

    for item_id, original in verified_source.items():
        adapted = _item(candidate, item_id)
        assert adapted.annotation_status == AnnotationStatus.VERIFIED
        assert adapted.origin == AnnotationOrigin.INHERITED
        assert adapted.reviewed_by == original.reviewed_by
        assert adapted.reviewed_at == original.reviewed_at
        # L'evidencia es el que sosté la verificacio: ha de ser identica.
        assert adapted.document_name == original.document_name
        assert adapted.page_number == original.page_number
        assert adapted.quote == original.quote
        if isinstance(adapted, GoldRequirementV2):
            assert adapted.category == original.category
            assert adapted.obligation == original.obligation
            assert adapted.operator == original.operator


def test_an_inherited_annotation_never_gains_a_v2_property_it_did_not_have(candidate):
    """El nucli de tot: adaptar no es reinterpretar.

    El quote de `422-2026::eco-1` diu "referido al de mayor volumen de los tres
    ultimos": l'agregacio es llegeix a simple vista. Posar-hi `max` seria
    correcte i seria una afirmacio que ningu ha verificat, signada per algu que
    no la va mirar.
    """
    for _, item in candidate.inherited():
        if not isinstance(item, GoldRequirementV2):
            continue
        assert item.modality == RequirementModality.UNKNOWN
        assert item.alternative_group is None
        value = item.value or {}
        if value.get("aggregation") is not None:
            assert value["aggregation"] == AggregationType.UNKNOWN.value

    eco = _item(candidate, "422-2026::eco-1")
    assert "mayor volumen" in eco.quote, "el quote segueix contenint la pista..."
    assert eco.value["aggregation"] == AggregationType.UNKNOWN.value, "...i no s'ha llegit"


def test_a_flattened_insurance_amount_does_not_become_a_total_limit(candidate):
    """`coverage_amount` no diu de quin limit parla, i suposar-ho seria fals.

    `26-095-3` ho demostra: el seu quote diu "por siniestro y año", que no es el
    limit total. Marcar-lo `total` faria semblar coberta una polissa que potser
    no ho esta.
    """
    for _, item in candidate.inherited():
        if (item.value or {}).get("kind") != "insurance":
            continue
        limits = item.value["limits"]
        assert len(limits) == 1
        assert limits[0]["kind"] == InsuranceLimitKind.UNKNOWN.value

    per_claim = _item(candidate, "26-095-3::ins-1")
    assert "por siniestro" in per_claim.quote
    assert per_claim.value["limits"][0]["kind"] == InsuranceLimitKind.UNKNOWN.value


# --------------------------------------------------------------------------- #
# Les 5 propostes: existeixen i NO estan verificades                            #
# --------------------------------------------------------------------------- #


def test_the_corrections_replace_exactly_the_five_needs_review(source, candidate):
    result = build_candidate(source)
    assert not result.orphan, "una proposta apunta a una anotacio inexistent"
    assert len(result.applied) == 5

    replaced = {
        f"{case.case_id}::{item.gold_id}"
        for case in source.cases
        for item in [*case.requirements, *case.award_criteria]
        if item.annotation_status == AnnotationStatus.NEEDS_REVIEW
    }
    assert set(result.applied) == replaced


def test_an_addition_is_added_and_replaces_nothing(source, candidate):
    """Afegir i corregir son coses diferents i no s'han de barrejar.

    `TSA0082096-040::ins-2` no existia: entra com a anotacio nova. Si entres com
    a correccio, hauria substituit alguna cosa que ningu volia substituir.
    """
    result = build_candidate(source)
    assert result.added == ["TSA0082096-040::ins-2"]
    assert set(result.added) & set(result.applied) == set()

    case = candidate.case("TSA0082096-040")
    assert [item.gold_id for item in case.requirements] == ["ins-1", "ins-2"]
    assert len(candidate.proposals()) == 6
    assert candidate.total_requirements == 14


def test_no_proposal_is_verified_automatically(candidate):
    """Escriure una correccio no la converteix en veritat de referencia.

    Es tota la premissa del gold: nomes una persona que ha comparat l'anotacio
    amb la pagina pot dir `verified`.
    """
    for _, item in candidate.proposals():
        assert item.annotation_status in (
            AnnotationStatus.DRAFT,
            AnnotationStatus.NEEDS_REVIEW,
        )
        assert not item.is_verified
        assert item.reviewed_by is None
        assert item.reviewed_at is None

    assert candidate.verified_requirements == 11
    assert candidate.verified_award_criteria == 0


def test_every_correction_is_recorded_as_a_gold_revision(source, candidate):
    """Una correccio del gold mai es silenciosa."""
    new = candidate.revisions[len(source.revisions) :]
    assert len(new) == 6, "tambe l'anotacio afegida ha de deixar constancia"
    assert {revision.gold_id for revision in new} == {
        "class-1",
        "exp-1",
        "award-1",
        "ins-2",
    }
    assert all(revision.reason for revision in new)


# --------------------------------------------------------------------------- #
# Evidencia: cada cita es al text real                                          #
# --------------------------------------------------------------------------- #


def test_every_proposal_quote_is_locatable_in_the_real_page(candidate):
    """Sense aixo, el gold seria una col·leccio d'afirmacions no comprovables."""
    checks = [
        check
        for check in check_quotes(candidate, _texts(candidate))
        if not check.page_missing
    ]
    assert checks, "cap cita comprovada: la fixture de pagines no s'ha carregat"
    not_located = [check for check in checks if not check.located]
    assert not_located == [], f"cites que no son a la pagina: {not_located}"


def test_multi_page_evidence_is_checked_too(candidate):
    """La formula de Madrid es reparteix entre la 70 i la 71.

    Amb una sola cita per anotacio s'hauria de triar quina meitat es perd.
    """
    award = _item(candidate, "300-2025-03877::award-1")
    pages = {evidence.page_number for evidence in award.extra_evidence}
    assert 71 in pages

    checked = {
        (check.page_number, check.located)
        for check in check_quotes(candidate, _texts(candidate))
        if check.item_id == "300-2025-03877::award-1"
    }
    assert checked == {(70, True), (71, True)}


def test_a_quote_that_is_not_in_the_page_is_reported(candidate):
    """Comprovacio de la comprovacio: si es trenca, ha de posar-se vermell."""
    item = _item(candidate, "422-2026::award-1")
    item.quote = "El licitador debera presentar una declaracion que no diu enlloc el plec"

    broken = [
        check
        for check in check_quotes(candidate, _texts(candidate))
        if not check.located and not check.page_missing
    ]
    assert [check.item_id for check in broken] == ["422-2026::award-1"]


# --------------------------------------------------------------------------- #
# Les formules es conserven literals, i no se n'inventa cap                     #
# --------------------------------------------------------------------------- #


def test_the_literal_formulas_are_preserved_exactly(candidate):
    """Literal vol dir literal: amb la `x`, sense espais i sense embellir.

    Si el gold "arregla" la formula, el benchmark demanaria al model que
    l'arreglés igual, cosa que ningu li ha demanat.
    """
    molar = _item(candidate, "422-2026::award-1")
    assert molar.formula == "PL=10 x Oferta más baja / Oferta a valorar"

    pilas = _item(candidate, "P4107500C-2026-65::award-1")
    assert pilas.formula == "P = 75 x (OM/OF)"
    assert [variable.symbol for variable in pilas.formula_variables] == ["P", "OF", "OM"]
    assert pilas.formula_variables[2].meaning == "oferta económica más ventajosa"

    for item in (molar, pilas):
        page_text = GOLD_V2_PAGES[
            (
                "422-2026" if item is molar else "P4107500C-2026-65",
                item.document_name,
                item.page_number,
            )
        ]
        assert locate_quote(page_text, item.formula) is not None, (
            "la formula anotada ha de ser al text de la pagina"
        )


def test_a_formula_that_is_not_in_the_text_is_not_invented(candidate):
    """El cas incomode: la pagina anuncia la formula i l'expressio no hi es.

    Escriure-hi la formula "obvia" a partir de les variables faria que el
    benchmark mesures la nostra imaginacio en comptes de l'extractor.
    """
    award = _item(candidate, "300-2025-03877::award-1")
    assert award.formula is None
    assert award.formula_text is not None, "la frase que l'anuncia si que hi es"
    assert len(award.formula_variables) == 4, "les variables si que estan definides"
    assert any("EXPRESSIO" in note for note in award.unresolved), (
        "el buit ha d'estar escrit, no nomes absent"
    )

    page = GOLD_V2_PAGES[("300-2025-03877", award.document_name, 70)]
    following = GOLD_V2_PAGES[("300-2025-03877", award.document_name, 71)]
    for candidate_formula in ("P = 75", "PBL - OE", "OB / OE", "P="):
        assert candidate_formula not in page + following, (
            "si l'expressio aparegues al text, aquesta anotacio hauria de portar-la"
        )


def test_the_award_criteria_keep_their_maximum_points_and_type(candidate):
    for item_id, points in (
        ("300-2025-03877::award-1", 45.0),
        ("422-2026::award-1", 10.0),
        ("P4107500C-2026-65::award-1", 75.0),
    ):
        item = _item(candidate, item_id)
        assert item.points == points
        assert item.criterion_type.value == "price"
        assert item.evaluation_type.value == "automatic"


# --------------------------------------------------------------------------- #
# Experiencia i classificacio: els dos casos que la v1 representava malament    #
# --------------------------------------------------------------------------- #


def test_the_experience_threshold_keeps_percentage_base_and_amount(candidate):
    """70% de l'anualitat mitjana I 21.406,71 EUR: el plec dona les dues coses.

    Quedar-se nomes amb l'import perdria la regla; quedar-se nomes amb el
    percentatge perdria la xifra que el plec ja ha calculat.
    """
    item = _item(candidate, "300-2025-03877::exp-1")
    value = item.value
    assert value["kind"] == "experience"

    accumulated = value["min_accumulated_amount"]
    assert accumulated["percentage"] == 70
    assert accumulated["percentage_base"] == "contract_annuity"
    assert accumulated["amount"] == 21406.71
    assert accumulated["currency"] == "EUR"

    # "del año de mayor ejecucion": no es cada any.
    assert value["aggregation"] == AggregationType.MAX.value
    assert value["reference_period"] == {"type": "last_n_years", "years": 3}
    assert item.obligation == RequirementObligation.MANDATORY
    assert "min_individual_amount" not in value, "el llindar es sobre l'acumulat"


def test_the_classification_alternative_is_not_a_mandatory_requirement(candidate):
    """"Podra presentar" no obliga a res, i tractar-ho com si obligues exclou.

    Es el blocador fals mes car que el sistema pot cometre: dir a una empresa
    que no es pot presentar a una licitacio a la qual si que pot.
    """
    item = _item(candidate, "2026SE00013::class-1")
    assert item.modality == RequirementModality.ALTERNATIVE
    assert item.obligation != RequirementObligation.MANDATORY
    assert item.obligation == RequirementObligation.UNKNOWN
    assert item.alternative_group
    assert not item.is_critical, "una via alternativa no pot ser un requisit critic"

    # El grup i el subgrup es remeten a un altre apartat: no s'inventen.
    assert item.value.get("group") is None
    assert item.value.get("subgroup") is None
    assert item.unresolved


def test_the_schema_rejects_an_alternative_that_claims_to_be_mandatory():
    """La incoherencia no depen de qui escriu l'anotacio: la rebutja el model."""
    base = _proposal("2026SE00013", "class-1").model_dump()
    base["obligation"] = RequirementObligation.MANDATORY.value

    with pytest.raises(ValidationError, match="alternativa"):
        GoldRequirementV2.model_validate(base)


def _proposal(case_id: str, gold_id: str):
    for case, item in PROPOSALS:
        if case == case_id and item.gold_id == gold_id:
            return item
    raise AssertionError(f"proposta inexistent: {case_id}::{gold_id}")


# --------------------------------------------------------------------------- #
# Els dos limits de la mateixa polissa                                          #
# --------------------------------------------------------------------------- #


def test_the_sublimit_proposal_keeps_both_limits_with_their_kind(candidate):
    """Cobertura total i sublimit per victima son condicions diferents.

    Aplanar-les en un sol import perd la mes restrictiva, que es precisament la
    que decideix si una empresa esta coberta o no.
    """
    item = _item(candidate, "TSA0082096-040::ins-2")
    limits = item.value["limits"]

    assert [(limit["kind"], limit["amount"]) for limit in limits] == [
        ("total", 500000),
        ("per_victim", 300000),
    ]
    assert {limit["currency"] for limit in limits} == {"EUR"}
    assert item.value["insurance_type"] == "civil_liability"
    assert item.value["insurance_type_note"] == "Responsabilidad Civil de Explotación"

    # Un sol requisit, no dos: es la mateixa polissa.
    assert item.operator.value == "gte"
    assert BenchmarkDimension.INSURANCE_LIMITS in dimensions_of(item)


def test_the_sublimit_proposal_is_a_draft_and_cites_the_page(candidate):
    item = _item(candidate, "TSA0082096-040::ins-2")

    assert item.annotation_status == AnnotationStatus.DRAFT
    assert item.origin == AnnotationOrigin.PROPOSAL
    assert item.reviewed_by is None
    assert "sublímite por víctima de 300.000" in item.quote

    page = GOLD_V2_PAGES[("TSA0082096-040", item.document_name, item.page_number)]
    assert locate_quote(page, item.quote) is not None


def test_the_verified_insurance_annotation_is_untouched_by_the_refinement(source, candidate):
    """El refinament s'afegeix AL COSTAT, no a sobre.

    Reobrir una anotacio que una persona ja va signar per encabir-hi el sublimit
    seria rebaixar la seva verificacio sense demanar-li permis.
    """
    original = next(
        item
        for case in source.cases
        if case.case_id == "TSA0082096-040"
        for item in case.requirements
        if item.gold_id == "ins-1"
    )
    inherited = _item(candidate, "TSA0082096-040::ins-1")

    assert inherited.annotation_status == AnnotationStatus.VERIFIED
    assert inherited.origin == AnnotationOrigin.INHERITED
    assert inherited.quote == original.quote
    assert inherited.reviewed_by == original.reviewed_by
    # Segueix amb un sol import i sense tipus: el sublimit no se li ha injectat.
    assert inherited.value["limits"] == [{"kind": "unknown", "amount": 500000, "currency": "EUR"}]
    assert BenchmarkDimension.INSURANCE_LIMITS not in dimensions_of(inherited)


def test_verifying_both_annotations_of_the_same_policy_is_flagged(candidate):
    """Dues anotacions verificades per una sola exigencia inflarien el gold.

    El benchmark demanaria dues assegurances on el plec en demana una, i un
    extractor correcte apareixeria com si se n'hagues deixat una. Quina de les
    dues es queda ho decideix una persona; que el problema es vegi, no.
    """
    assert candidate.double_counted() == [], "avui nomes n'hi ha una de verificada"

    _item(candidate, "TSA0082096-040::ins-2").annotation_status = AnnotationStatus.VERIFIED

    assert candidate.double_counted() == [
        ("TSA0082096-040::ins-1", "TSA0082096-040::ins-2")
    ]
    markdown = render_coverage_markdown(
        coverage_payload(candidate, check_quotes(candidate, _texts(candidate)))
    )
    assert "AVIS: hi ha parelles verificades totes dues" in markdown


def test_an_addition_that_collides_with_an_existing_gold_id_is_refused(source, monkeypatch):
    """Un `gold_id` repetit seria una anotacio duplicada, no una de nova."""
    import app.evaluation.gold_v2 as module

    collision = _proposal_or_addition("TSA0082096-040", "ins-2").model_copy(
        deep=True, update={"gold_id": "ins-1"}
    )
    monkeypatch.setattr(module, "ADDITIONS", (("TSA0082096-040", collision),))

    result = module.build_candidate(source)

    assert result.orphan == ["TSA0082096-040::ins-1"]
    assert result.added == []


def _proposal_or_addition(case_id: str, gold_id: str):
    for case, item in (*PROPOSALS, *ADDITIONS):
        if case == case_id and item.gold_id == gold_id:
            return item
    raise AssertionError(f"proposta inexistent: {case_id}::{gold_id}")


# --------------------------------------------------------------------------- #
# Verificar un refinament: que passa amb l'anotacio que afina                   #
# --------------------------------------------------------------------------- #


REFINEMENT = "TSA0082096-040::ins-2"
REFINED = "TSA0082096-040::ins-1"


def _decide(dataset, decisions, *, reviewer="AlbertTuvi1", when=date(2026, 9, 9)):
    return apply_decisions(dataset, decisions, reviewer=reviewer, reviewed_on=when)


def test_verifying_a_refinement_without_resolving_the_original_is_refused(candidate):
    """Un avis al report seria una nota que algu llegeix massa tard.

    Si les dues comptessin, el gold demanaria dues assegurances on el plec en
    demana una i un extractor correcte sortiria com si se n'hagues deixat una.
    Per aixo aixo atura l'aplicacio de decisions, no la comenta.
    """
    with pytest.raises(RefinementConflict) as error:
        _decide(candidate, {REFINEMENT: {"status": "verified"}})

    problem = error.value.problems[0]
    assert REFINED in problem
    assert SUPERSEDE_KEY in problem, "l'error ha de dir com resoldre-ho"

    # I no ha canviat res: la persona pot tornar a la pagina i decidir.
    assert _item(candidate, REFINEMENT).annotation_status == AnnotationStatus.DRAFT
    assert not _item(candidate, REFINED).is_superseded


def test_keeping_both_verified_is_refused_even_when_asked_explicitly(candidate):
    """`supersede_refined: false` no es una sortida per la porta del darrere."""
    with pytest.raises(RefinementConflict, match="dues vegades la mateixa"):
        _decide(candidate, {REFINEMENT: {"status": "verified", SUPERSEDE_KEY: False}})


def test_a_refinement_verified_with_supersede_leaves_one_annotation_counting(candidate):
    """Nomes l'afinada entra al denominador. L'original es conserva sencera."""
    _decide(candidate, {REFINEMENT: {"status": "verified", SUPERSEDE_KEY: True}})

    refinement = _item(candidate, REFINEMENT)
    refined = _item(candidate, REFINED)

    assert refinement.counts_for_metrics
    assert not refined.counts_for_metrics
    assert refined.superseded_by == "ins-2"
    assert refined.superseded_at == date(2026, 9, 9)

    # El denominador que fa servir el benchmark (`runner.py`).
    case = candidate.case("TSA0082096-040")
    assert [item.gold_id for item in case.verified_requirements] == ["ins-2"]
    assert candidate.verified_requirements == 11
    assert candidate.double_counted() == []


def test_a_superseded_annotation_stays_fully_auditable(source, candidate):
    """Substituida no vol dir esborrada.

    El que va passar ha de continuar sent consultable: qui la va verificar, quan,
    amb quina cita. El que canvia es contra que es compara el model.
    """
    original = next(
        item
        for case in source.cases
        if case.case_id == "TSA0082096-040"
        for item in case.requirements
        if item.gold_id == "ins-1"
    )
    _decide(candidate, {REFINEMENT: {"status": "verified", SUPERSEDE_KEY: True}})
    refined = _item(candidate, REFINED)

    assert refined.annotation_status == AnnotationStatus.VERIFIED, "segueix dient-ho"
    assert refined.reviewed_by == original.reviewed_by == "AlbertTuvi1"
    assert refined.reviewed_at == original.reviewed_at
    assert refined.quote == original.quote
    assert refined.value["limits"] == [{"kind": "unknown", "amount": 500000, "currency": "EUR"}]

    # I queda escrit qui la va substituir i per que.
    revision = candidate.revisions[-1]
    assert revision.gold_id == "ins-1"
    assert revision.reviewer == "AlbertTuvi1"
    assert "ins-2" in revision.reason


def test_superseding_does_not_touch_the_v1_dataset(candidate):
    """La substitucio val NOMES dins del gold v2."""
    before = dataset_path(SOURCE_DATASET).read_text(encoding="utf-8")

    _decide(candidate, {REFINEMENT: {"status": "verified", SUPERSEDE_KEY: True}})

    assert dataset_path(SOURCE_DATASET).read_text(encoding="utf-8") == before
    v1 = load_dataset(SOURCE_DATASET)
    original = next(
        item
        for case in v1.cases
        if case.case_id == "TSA0082096-040"
        for item in case.requirements
    )
    assert original.annotation_status == AnnotationStatus.VERIFIED
    assert not hasattr(original, "superseded_by")


@pytest.mark.parametrize("status", ["rejected", "needs_review"])
def test_a_refinement_that_is_not_verified_leaves_the_original_alone(candidate, status):
    """Si la proposta no s'accepta, l'anotacio de sempre segueix sent el gold."""
    _decide(candidate, {REFINEMENT: {"status": status}})

    assert _item(candidate, REFINEMENT).annotation_status == AnnotationStatus(status)
    refined = _item(candidate, REFINED)
    assert refined.annotation_status == AnnotationStatus.VERIFIED
    assert refined.counts_for_metrics
    assert not refined.is_superseded


def test_rejecting_the_original_is_also_an_explicit_resolution(candidate):
    """Substituir no es l'unica sortida: retirar l'original tambe val."""
    _decide(
        candidate,
        {
            REFINEMENT: {"status": "verified", SUPERSEDE_KEY: False},
            REFINED: {"status": "rejected", "note": "substituida per ins-2"},
        },
    )

    assert _item(candidate, REFINEMENT).counts_for_metrics
    refined = _item(candidate, REFINED)
    assert refined.annotation_status == AnnotationStatus.REJECTED
    assert not refined.counts_for_metrics
    assert candidate.double_counted() == []


def test_insurance_limits_becomes_measurable_once_the_refinement_is_verified(candidate):
    """El motiu de tot plegat: la dimensio passa de `n/d` a mesurable."""
    payload = coverage_payload(candidate, check_quotes(candidate, _texts(candidate)))
    assert payload["dimensions"]["insurance_limits"] == {
        "verified": 0,
        "pending": 1,
        "measurable": False,
    }

    _decide(candidate, {REFINEMENT: {"status": "verified", SUPERSEDE_KEY: True}})

    payload = coverage_payload(candidate, check_quotes(candidate, _texts(candidate)))
    assert payload["dimensions"]["insurance_limits"] == {
        "verified": 1,
        "pending": 0,
        "measurable": True,
    }

    markdown = render_coverage_markdown(payload)
    row = next(
        line for line in markdown.splitlines() if line.startswith("| insurance_limits ")
    )
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    assert cells[1] == "1" and cells[3] == "si"

    # I la superada surt del recompte efectiu sense desapareixer del fitxer.
    coverage = candidate.coverage()
    assert coverage["verified"] == 12
    assert coverage["superseded"] == 1
    assert coverage["counts_for_metrics"] == 11


def test_the_last_line_of_defence_catches_a_hand_edited_dataset(candidate):
    """Si algu edita el JSON o obre un cami nou, aixo ha de seguir aturant-ho."""
    _item(candidate, REFINEMENT).annotation_status = AnnotationStatus.VERIFIED

    with pytest.raises(RefinementConflict, match="mateixa exigencia"):
        check_no_double_counting(candidate)


# --------------------------------------------------------------------------- #
# Cobertura i report: `n/d` no es 0%                                            #
# --------------------------------------------------------------------------- #


def test_dimensions_are_derived_from_content_not_declared(candidate):
    """Una llista escrita a ma podria dir que una anotacio mesura coses que no.

    Derivar-ho del contingut fa que la cobertura no pugui mentir: si el camp es
    `unknown`, la dimensio no compta, digui el que digui ningu.
    """
    assert dimensions_of(_item(candidate, "300-2025-03877::exp-1")) == {
        BenchmarkDimension.OBLIGATION_MODALITY,
        BenchmarkDimension.AGGREGATION,
        BenchmarkDimension.PERCENTAGE_BASE,
    }
    assert dimensions_of(_item(candidate, "2026SE00013::class-1")) == {
        BenchmarkDimension.OBLIGATION_MODALITY,
        BenchmarkDimension.ALTERNATIVES,
    }
    # Heretada amb tot a `unknown`: no fixa cap dimensio de la v2.
    assert dimensions_of(_item(candidate, "4249-2026::eco-1")) == set()


def test_a_dimension_without_verified_gold_is_reported_as_nd(candidate):
    """Un 0% es llegeix com una mesura. Aqui no s'ha mesurat res.

    Es la mateixa regla que ja governa les metriques de criteris d'adjudicacio:
    sense denominador no hi ha percentatge, hi ha absencia.
    """
    payload = coverage_payload(candidate, check_quotes(candidate, _texts(candidate)))
    dimensions = payload["dimensions"]

    for name in ("aggregation", "alternatives", "award_formula", "insurance_limits"):
        assert dimensions[name]["verified"] == 0
        assert dimensions[name]["measurable"] is False

    markdown = render_coverage_markdown(payload)
    for name in ("aggregation", "alternatives", "award_formula", "insurance_limits"):
        row = next(line for line in markdown.splitlines() if line.startswith(f"| {name} "))
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        # La columna del gold verificat, no una `n/d` qualsevol de la fila: un
        # `0` aqui es llegiria com "s'ha mesurat i ha donat zero".
        assert cells[1] == NOT_MEASURABLE, row
        assert cells[3] == NOT_MEASURABLE, row
    assert "0%" not in markdown

    # I la fila d'una dimensio que SI que es mesurable ha de dir la xifra.
    equivalence = next(
        line for line in markdown.splitlines() if line.startswith("| equivalence ")
    )
    assert [cell.strip() for cell in equivalence.strip("|").split("|")][1] == "1"


def test_the_report_says_out_loud_that_there_are_no_model_metrics(candidate):
    payload = coverage_payload(candidate, check_quotes(candidate, _texts(candidate)))
    assert payload["model_metrics"] is None
    assert "metriques de model" in render_coverage_markdown(payload)


def test_insurance_limits_is_pending_review_and_not_a_dead_end(candidate):
    """La dimensio que abans no tenia res a que aspirar.

    Hi ha 5 anotacions d'assegurança verificades i cap no pot mesurar sublimits,
    perque la v1 els va aplanar. Amb `ins-2` la dimensio deixa de ser un cul de
    sac: segueix sent `n/d` avui —ningu l'ha verificada— pero ja te alguna cosa
    que una persona pot acceptar.
    """
    payload = coverage_payload(candidate, check_quotes(candidate, _texts(candidate)))
    limits = payload["dimensions"]["insurance_limits"]

    assert limits["verified"] == 0, "cap anotacio verificada la fixa: avui es n/d"
    assert limits["measurable"] is False
    assert limits["pending"] == 1, "i n'hi ha exactament una esperant revisio"

    markdown = render_coverage_markdown(payload)
    row = next(
        line for line in markdown.splitlines() if line.startswith("| insurance_limits ")
    )
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    assert cells[1] == NOT_MEASURABLE
    assert cells[2] == "1"

    # Ja no hi ha cap dimensio sense res: la seccio de buits desapareix.
    assert "Buits que aquesta versio NO tanca" not in markdown


def test_the_gap_section_appears_when_a_dimension_has_nothing_at_all(candidate):
    """Comprovacio de l'avis: si torna a haver-hi un cul de sac, s'ha de veure.

    Sense aquest test, la seccio hauria pogut deixar de funcionar el mateix dia
    que ha deixat de fer falta, i ningu se n'hauria adonat.
    """
    sublimit = _item(candidate, "TSA0082096-040::ins-2")
    sublimit.value = {**sublimit.value, "limits": []}

    payload = coverage_payload(candidate, check_quotes(candidate, _texts(candidate)))
    assert payload["dimensions"]["insurance_limits"]["pending"] == 0

    markdown = render_coverage_markdown(payload)
    assert "Buits que aquesta versio NO tanca" in markdown
    assert "insurance_limits" in markdown.split("Buits que aquesta versio NO tanca")[1]


def test_drafts_and_needs_review_stay_out_of_the_official_metrics(candidate):
    """Les propostes existeixen al fitxer i NO al denominador.

    Si hi entressin, el benchmark es compararia amb etiquetes que ningu ha
    comprovat i el resultat semblaria una mesura.
    """
    counts = candidate.coverage()
    assert counts["verified"] == 11
    assert counts["draft"] + counts["needs_review"] == 6

    for case in candidate.cases:
        assert all(item.is_verified for item in case.verified_requirements)
        assert all(item.is_verified for item in case.verified_award_criteria)

    assert candidate.verified_requirements == 11
    assert candidate.verified_award_criteria == 0

    payload = coverage_payload(candidate, check_quotes(candidate, _texts(candidate)))
    assert payload["by_origin"]["proposals_verified"] == 0
    assert payload["by_origin"]["inherited_verified"] == 11


# --------------------------------------------------------------------------- #
# El fitxer que hi ha al repositori                                             #
# --------------------------------------------------------------------------- #


def test_the_committed_candidate_is_reproducible(source, committed):
    """Regenerar-lo ha de donar exactament el mateix.

    Si no, el fitxer del repositori porta alguna cosa que no surt del codi ni
    del gold d'origen, i ningu podria dir d'on ha sortit.
    """
    fresh = build_candidate(source, today=committed.created_at)
    assert fresh.dataset.model_dump(mode="json") == committed.model_dump(mode="json")


def test_the_committed_candidate_declares_its_schema_and_origin(committed):
    assert committed.gold_schema_version == GOLD_SCHEMA_VERSION
    assert committed.derived_from == SOURCE_DATASET
    assert committed.dataset_id == CANDIDATE_DATASET
    assert committed.version > load_dataset(SOURCE_DATASET).version
