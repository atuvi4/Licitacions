"""El paquet documental a l'analisi v3 (3B-2 §4.4 i §3.2).

* Cada forat obert del paquet es una `missing_information` amb `missing_from=tender`
  (el codi, el detall i les seccions afectades, o «impacte desconegut»), i el resum
  de la run diu `package_incomplete`.
* Una seccio `unknown` per culpa d'un forat no dona cap avaluacio positiva: cap via de
  prestacio disponible, cap territori coincident, cap punt puntuat. El motor no
  inventa cap seu ni cap estructura.
* Sense passada de classificacio global vigent: `package_status = unknown`, cap
  troballa de forat, i el prerequisit continua exigint requisits.
* Prerequisit: sense requisits efectius es refusa (409), llevat que el paquet consti
  com a incomplet i qui ho demana ho accepti (`accept_incomplete_package=true`), que
  queda registrat a la run.
* `ROLE_OVERRIDE_DIVERGES`: informativa quan els rols de l'empresa difereixen dels
  globals per algun document analitzable.

Dades sintetiques.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.analysis import engine as pure
from app.analysis import territory as terr
from app.analysis.fingerprint import (
    PACKAGE_COMPLETE,
    PACKAGE_INCOMPLETE,
    PACKAGE_UNKNOWN,
    SECTION_PRESENT,
    SECTION_UNKNOWN,
    CriterionView,
    NeedView,
    PackageGapView,
    RequirementView,
    ScopeItemView,
    SiteView,
    TenderStructureView,
    TerritoryView,
    gap_section_status,
    structure_fingerprint,
)
from app.analysis.registry import ExplanationCode as X
from app.knowledge import vocabulary
from app.knowledge.vocabulary import CAPABILITIES_BY_CODE
from app.models.enums import (
    DocumentRole,
    GlobalClassificationTrigger,
    PackageGapCode,
    PackageGapProducer,
)
from app.models.global_classification import GlobalDocumentClassificationRun
from app.models.package import TenderPackageGap
from app.models.tender_analysis import TenderAnalysisRun
from app.models.tenders import Tender
from app.services import document_roles
from app.services import global_document_classification as global_classification
from app.services import tender_analysis as analysis
from tests import test_tender_scope_models as m
from tests.test_document_roles_resolver import current_sha, decide
from tests.test_global_classification import make_public_tender, public_package
from tests.test_platform_onboarding import create_operator
from tests.test_tender_analysis_engine import _snapshot_company, _synthetic_tender

CAPABILITY = "pbx.concurrent_calls"
LINE = CAPABILITIES_BY_CODE[CAPABILITY].service_line_code

SITES_GAP = PackageGapView(
    code="referenced_document_missing",
    detail={"key": "DOC-04", "declared_codes": ["DOC-04"]},
    would_provide=("sites",),
    producer="classification",
)
UNKNOWN_IMPACT_GAP = PackageGapView(
    code="referenced_document_missing",
    detail={"key": "DOC-03"},
    would_provide=(),
    producer="classification",
)


# --------------------------------------------------------------------------- #
# Motor pur                                                                    #
# --------------------------------------------------------------------------- #


def _need() -> NeedView:
    return NeedView(
        id="n1", capability_code=CAPABILITY, unmapped_capability=None, comparator="exists",
        value_json=None, value_schema=None, need_type="minimum", points=None, criterion_key=None,
    )  # fmt: skip


def _requirement(**overrides) -> RequirementView:
    base = RequirementView(
        id=str(uuid.uuid4()), key="k-r1", title="Centraleta", category="technical_requirement",
        obligation="mandatory", timing="at_execution", territory_completeness="not_applicable",
        needs=(_need(),),
    )  # fmt: skip
    return replace(base, **overrides)


def _view(*, gaps=(), **overrides) -> TenderStructureView:
    overrides.setdefault("requirements", (_requirement(),))
    overrides.setdefault(
        "scope_items", (ScopeItemView(id=str(uuid.uuid4()), service_line_code=LINE),)
    )
    view = TenderStructureView(tender_id="t", **overrides)
    return replace(
        view, gaps=tuple(gaps), package_status=PACKAGE_INCOMPLETE if gaps else PACKAGE_COMPLETE
    )


def _company(coverage: terr.Coverage | None = None, **overrides) -> pure.CompanyView:
    service = pure.ServiceClaimView(
        service_id=str(uuid.uuid4()),
        name="Centraleta propia",
        service_line_code=LINE,
        delivery_mode="own",
        source="current_declared",
        coverage=coverage,
    )
    return replace(_snapshot_company(), services=(service,), **overrides)


def _gap_findings(result: pure.AnalysisResult) -> list[pure.FindingOut]:
    return [f for f in result.findings if f.explanation_code == X.PACKAGE_GAP]


def test_without_gaps_the_delivery_is_available_and_there_is_no_gap_finding():
    view = _view()
    result = pure.analyze(view, _company())
    assert [c.readiness for c in result.candidates] == ["available"]
    assert _gap_findings(result) == []
    assert pure.summary(result, view)["package_incomplete"] is False


def test_a_gap_that_would_provide_sites_is_missing_information_and_blocks_delivery():
    view = _view(gaps=[SITES_GAP])
    result = pure.analyze(view, _company())

    findings = _gap_findings(result)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_type == "missing_information"
    assert finding.details["missing_from"] == pure.MISSING_FROM_TENDER == "tender"
    assert finding.details["gap_code"] == "referenced_document_missing"
    assert finding.details["gap_detail"] == {"key": "DOC-04", "declared_codes": ["DOC-04"]}
    assert finding.details["would_provide"] == ["sites"]
    assert finding.details["impact_unknown"] is False
    assert finding.requirement_id is None and finding.scope_item_id is None

    summary = pure.summary(result, view)
    assert summary["package_incomplete"] is True
    # Cap seu inventada; la seccio es `unknown` i cap via de prestacio es disponible.
    assert view.sites == ()
    assert gap_section_status(view)["sites"] == SECTION_UNKNOWN
    assert not [c for c in result.candidates if c.readiness == "available"]
    assert summary["delivery"]["available"] == 0
    assert {c.territory_verdict for c in result.candidates} == {"unknown"}


def test_a_gap_with_unknown_impact_is_generic_and_every_empty_section_is_unknown():
    view = _view(gaps=[UNKNOWN_IMPACT_GAP])
    result = pure.analyze(view, _company())
    findings = _gap_findings(result)
    assert len(findings) == 1
    assert findings[0].details["would_provide"] == []
    assert findings[0].details["impact_unknown"] is True
    status = gap_section_status(view)
    # Buides: blocs, seus, territoris, criteris i fets. L'abast te files.
    assert {s for s, st in status.items() if st == SECTION_UNKNOWN} == {
        "blocks",
        "sites",
        "territories",
        "award_criteria",
        "contract_facts",
    }
    assert status["scope_items"] == "present"
    assert not [c for c in result.candidates if c.readiness == "available"]


def test_one_finding_per_gap_in_a_stable_order():
    view = _view(gaps=[SITES_GAP, UNKNOWN_IMPACT_GAP])
    company = _company()
    result = pure.analyze(view, company)
    assert [f.details["gap_detail"]["key"] for f in _gap_findings(result)] == ["DOC-03", "DOC-04"]
    assert pure.analyze(replace(view, gaps=tuple(reversed(view.gaps))), company) == result


TERRITORIES_GAP = replace(SITES_GAP, detail={"key": "DOC-09"}, would_provide=("territories",))


def test_an_unknown_territories_section_is_attributed_to_the_package_gap():
    """Cap requisit porta territoris i un forat diu que el document que falta els
    aportaria: la seccio es `unknown`, i un requisit territorial (completesa diferent de
    `not_applicable`) queda `unknown` per culpa del forat."""
    company = _company()
    requirement = _requirement(territory_completeness="complete")
    view = _view(requirements=(requirement,), gaps=[TERRITORIES_GAP])
    assert gap_section_status(view)["territories"] == SECTION_UNKNOWN
    fit = pure.analyze(view, company).fit_assessments[0]
    assert fit.territory_verdict == "unknown"
    assert fit.support["territory"]["reason"] == pure.SECTION_UNKNOWN_REASON


def test_a_non_territorial_requirement_keeps_not_applicable_with_the_gap():
    """Fix 2 (M1): un requisit que el plec diu que no es territorial (p. ex. una
    certificacio) continua `not_applicable` encara que la seccio de territoris sigui
    `unknown`; i el context d'abast `not_applicable` no suprimeix la via de prestacio."""
    company = _company()
    view = _view(gaps=[TERRITORIES_GAP])  # requisit `not_applicable`
    assert gap_section_status(view)["territories"] == SECTION_UNKNOWN
    result = pure.analyze(view, company)
    assert result.fit_assessments[0].territory_verdict == "not_applicable"
    assert [c.readiness for c in result.candidates] == ["available"]


def test_a_named_section_with_rows_is_present_and_keeps_its_assessments():
    """Fix 1 (ruling 2): els territoris citats son fets. Amb un forat que nomena la
    seccio, la comparacio continua (`matched`) i la troballa del forat hi es."""
    where = TerritoryView(kind="municipality", label="L'Escala")
    requirement = _requirement(territory_completeness="complete", territories=(where,))
    coverage = terr.Coverage(
        completeness="complete", areas=(terr.Area("municipality", "L'Escala"),)
    )
    view = _view(requirements=(requirement,), gaps=[TERRITORIES_GAP])
    assert gap_section_status(view)["territories"] == SECTION_PRESENT
    result = pure.analyze(view, _company(coverage))
    assert result.fit_assessments[0].territory_verdict == "matched"
    assert len(_gap_findings(result)) == 1


def test_operator_sites_and_a_doc04_gap_still_give_delivery_candidates():
    """Fix 1 (ruling 2): seus aportades per l'operador + forat DOC-04 (`[sites]`): la
    seccio es `present`, hi ha via disponible, i la troballa i `package_incomplete` hi
    son."""
    site = SiteView(code="U01", name="Ajuntament", role="delivery")
    view = _view(sites=(site,), gaps=[SITES_GAP])
    assert gap_section_status(view)["sites"] == SECTION_PRESENT
    result = pure.analyze(view, _company())
    assert [c.readiness for c in result.candidates] == ["available"]
    assert len(_gap_findings(result)) == 1
    assert pure.summary(result, view)["package_incomplete"] is True


def test_named_criteria_with_rows_are_still_scored():
    criterion = CriterionView(
        id=str(uuid.uuid4()), key="k-c1", name="Millora", points=10.0,
        criterion_type="improvement", evaluation_type="automatic", timing="at_submission",
    )  # fmt: skip
    gap = replace(SITES_GAP, code="required_role_missing", detail={"key": "pcap"},
                  would_provide=("requirements", "award_criteria"))  # fmt: skip
    baseline = pure.analyze(_view(criteria=(criterion,)), _company())
    gapped = pure.analyze(_view(criteria=(criterion,), gaps=[gap]), _company())
    assert gapped.criterion_assessments == baseline.criterion_assessments
    assert len(_gap_findings(gapped)) == 1


def test_an_unreadable_annex_alone_is_a_processing_warning_not_an_incomplete_package():
    """Un annex present que no sabem llegir: la troballa hi es, cap seccio `unknown` i
    cap avaluacio suprimida, pero el paquet NO es incomplet (no hi falta cap document):
    es un avis de processament."""
    gap = PackageGapView(
        code="format_not_analyzable",
        detail={"key": "annex.docx"},
        would_provide=(),
        producer="classification",
    )
    view = _view(gaps=[gap])
    assert SECTION_UNKNOWN not in set(gap_section_status(view).values())
    result = pure.analyze(view, _company())
    assert [c.readiness for c in result.candidates] == ["available"]
    (finding,) = _gap_findings(result)
    assert finding.details["impact_unknown"] is True
    summary = pure.summary(result, view)
    assert summary["package_incomplete"] is False
    assert summary["processing_incomplete"] is True


def test_role_override_divergence_is_informational_and_lists_the_documents():
    divergence = pure.RoleDivergenceView(
        document_id="d1",
        document_name="PCAP.pdf",
        company_role="characteristics_table",
        company_document_type="administrative_terms",
        global_role="pcap",
        global_document_type="administrative_terms",
        global_source="global_classifier",
    )
    result = pure.analyze(_view(), _company(role_divergences=(divergence,)))
    found = [f for f in result.findings if f.explanation_code == X.ROLE_OVERRIDE_DIVERGES]
    assert len(found) == 1
    assert found[0].finding_type == "warning"
    assert found[0].details["documents"] == [
        {
            "document_id": "d1",
            "document_name": "PCAP.pdf",
            "company_role": "characteristics_table",
            "company_document_type": "administrative_terms",
            "global_role": "pcap",
            "global_document_type": "administrative_terms",
            "global_source": "global_classifier",
        }
    ]
    assert not [
        f
        for f in pure.analyze(_view(), _company()).findings
        if f.explanation_code == X.ROLE_OVERRIDE_DIVERGES
    ]


# --------------------------------------------------------------------------- #
# Vista del plec i analisi (base de dades)                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    await vocabulary.seed_vocabulary(db)
    await db.flush()


async def _global_run(db, tender: Tender) -> GlobalDocumentClassificationRun:
    run = GlobalDocumentClassificationRun(
        tender_id=tender.id,
        classifier_version="test",
        input_fingerprint=uuid.uuid4().hex,
        trigger=GlobalClassificationTrigger.OPERATOR_REQUEST,
        document_count=0,
        is_current=True,
    )
    db.add(run)
    await db.flush()
    return run


async def _gap(
    db,
    tender: Tender,
    key: str,
    would_provide,
    *,
    resolved=False,
    code=PackageGapCode.REFERENCED_DOCUMENT_MISSING,
) -> TenderPackageGap:
    gap = TenderPackageGap(
        tender_id=tender.id,
        code=code,
        detail={"key": key},
        would_provide=list(would_provide),
        producer=PackageGapProducer.CLASSIFICATION,
        resolved_at=datetime.now(UTC) if resolved else None,
    )
    db.add(gap)
    await db.flush()
    return gap


async def test_the_view_carries_the_open_gaps_and_the_section_status(db):
    tender = await m._public_tender(db, title="Plec sintetic amb forats")
    await _global_run(db, tender)
    await _gap(db, tender, "DOC-04", ["sites"])
    await _gap(db, tender, "DOC-05", ["award_criteria"], resolved=True)
    await db.commit()

    view, _ = await analysis.load_structure_view(db, tender)
    assert view.package_status == PACKAGE_INCOMPLETE
    # Nomes els forats oberts (fix 2, M7: la vista no porta cap camp `resolved`).
    assert [(g.code, g.key, g.would_provide, g.producer) for g in view.gaps] == [
        ("referenced_document_missing", "DOC-04", ("sites",), "classification")
    ]
    assert "resolved" not in PackageGapView.__dataclass_fields__
    assert view.section_status["sites"] == SECTION_UNKNOWN
    # Un forat resolt no compta: amb passada i sense cap altre forat, criteris buits =
    # `none_declared`.
    assert view.section_status["award_criteria"] == "none_declared"


async def test_an_unreadable_annex_alone_is_complete_with_a_processing_warning(db):
    """Test obligatori 1: tots els documents presents i un format no processable:
    paquet `complete`, avis de processament i cap seccio `unknown`."""
    tender = await m._public_tender(db, title="Plec sintetic amb un annex illegible")
    await _global_run(db, tender)
    await _gap(db, tender, "annex.bin", [], code=PackageGapCode.FORMAT_NOT_ANALYZABLE)
    await db.commit()
    view, _ = await analysis.load_structure_view(db, tender)
    assert view.package_status == PACKAGE_COMPLETE
    assert [g.code for g in view.gaps] == ["format_not_analyzable"]
    assert set(view.section_status.values()) == {"none_declared"}


async def test_a_resolved_gap_changes_nothing(db):
    tender = await m._public_tender(db, title="Plec sintetic complet")
    await _global_run(db, tender)
    before = structure_fingerprint((await analysis.load_structure_view(db, tender))[0])
    await _gap(db, tender, "DOC-04", ["sites"], resolved=True)
    await db.commit()
    view, _ = await analysis.load_structure_view(db, tender)
    assert view.package_status == PACKAGE_COMPLETE and view.gaps == ()
    assert structure_fingerprint(view) == before


async def test_without_a_current_run_the_package_is_unknown_and_no_gap_finding(
    db, tenant_a, client_a
):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await db.commit()
    view, _ = await analysis.load_structure_view(db, tender)
    assert view.package_status == PACKAGE_UNKNOWN and view.gaps == ()
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    assert not [f for f in run.findings if f.explanation_code == X.PACKAGE_GAP]
    assert analysis.summary_of(run)["package_incomplete"] is False
    assert run.accepted_incomplete_package is None


async def test_an_open_gap_reaches_the_persisted_run(db, tenant_a, client_a):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _global_run(db, tender)
    await db.commit()
    first = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    assert analysis.summary_of(first)["package_incomplete"] is False

    await _gap(db, tender, "DOC-04", ["sites"])
    await db.commit()
    status = await analysis.current_analysis(db, company.id, tender.id)
    assert status.stale is True  # el forat canvia l'empremta del plec
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    assert run.id != first.id
    gap_findings = [f for f in run.findings if f.explanation_code == X.PACKAGE_GAP]
    assert len(gap_findings) == 1
    assert gap_findings[0].finding_type == "missing_information"
    assert gap_findings[0].details_json["missing_from"] == "tender"
    assert gap_findings[0].details_json["would_provide"] == ["sites"]
    assert analysis.summary_of(run)["package_incomplete"] is True
    assert not [c for c in run.delivery_candidates if c.readiness == "available"]


# --------------------------------------------------------------------------- #
# Prerequisit del router                                                       #
# --------------------------------------------------------------------------- #


def _runs_url(company_id, tender_id) -> str:
    return f"/api/v1/companies/{company_id}/tenders/{tender_id}/analysis/runs"


async def _count_runs(db, company_id, tender_id) -> int:
    return (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderAnalysisRun)
            .where(
                TenderAnalysisRun.company_id == company_id,
                TenderAnalysisRun.tender_id == tender_id,
            )
        )
    ).scalar_one()


async def test_without_requirements_an_incomplete_package_needs_explicit_acceptance(
    db, tenant_a, client_a
):
    _, company = tenant_a
    tender = await m._public_tender(db, title="Plec sintetic sense requisits")
    await _global_run(db, tender)
    await _gap(db, tender, "DOC-04", ["sites"])
    await db.commit()
    url = _runs_url(company.id, tender.id)

    refused = await client_a.post(url)
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "analysis_prerequisites_missing"
    assert error["details"]["missing"] == ["requirements"]
    assert error["details"]["package_status"] == PACKAGE_INCOMPLETE
    refused = await client_a.post(url, json={"accept_incomplete_package": False})
    assert refused.status_code == 409
    assert await _count_runs(db, company.id, tender.id) == 0

    accepted = await client_a.post(url, json={"accept_incomplete_package": True})
    assert accepted.status_code == 201, accepted.text
    body = accepted.json()
    assert body["accepted_incomplete_package"] is True
    assert body["summary"]["package_incomplete"] is True
    assert [f["details_json"]["gap_code"] for f in body["findings"]] == [
        "referenced_document_missing"
    ]
    current = await client_a.get(url.replace("/runs", "/current"))
    assert current.json()["accepted_incomplete_package"] is True


@pytest.mark.parametrize("with_run", [False, True])
async def test_acceptance_is_not_a_bypass_when_the_package_is_not_declared_incomplete(
    db, tenant_a, client_a, with_run
):
    """Sense passada (`unknown`) o amb el paquet complet, acceptar no serveix de res:
    sense requisits no hi ha res a analitzar."""
    _, company = tenant_a
    tender = await m._public_tender(db, title="Plec sintetic sense requisits")
    if with_run:
        await _global_run(db, tender)
    await db.commit()
    refused = await client_a.post(
        _runs_url(company.id, tender.id), json={"accept_incomplete_package": True}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["details"]["missing"] == ["requirements"]
    assert refused.json()["error"]["details"]["package_status"] == (
        PACKAGE_COMPLETE if with_run else PACKAGE_UNKNOWN
    )
    assert await _count_runs(db, company.id, tender.id) == 0


async def test_a_manual_tender_with_a_declared_gap_can_be_analysed_on_acceptance(
    db, tenant_a, client_a
):
    """Fix 2 (M3): els forats son fets, independents de la passada global. Una
    licitacio manual no te mai passada global; amb un forat declarat per l'operador el
    paquet es `incomplete` i l'acceptacio explicita hi val."""
    from app.services.manual_tender import get_or_create_manual_source

    _, company = tenant_a
    source = await get_or_create_manual_source(db)
    tender = Tender(
        source_id=source.id,
        external_id=f"man-{uuid.uuid4().hex[:6]}",
        title="Licitacio manual sintetica",
        owner_company_id=company.id,
    )
    db.add(tender)
    await db.flush()
    gap = TenderPackageGap(
        tender_id=tender.id,
        code=PackageGapCode.DECLARED_BY_OPERATOR,
        detail={"key": "plec-tecnic"},
        would_provide=[],
        producer=PackageGapProducer.OPERATOR,
    )
    db.add(gap)
    await db.commit()

    view, _ = await analysis.load_structure_view(db, tender)
    assert view.package_status == PACKAGE_INCOMPLETE
    # Sense passada, les seccions buides continuen `unknown` (cap `none_declared`).
    assert set(view.section_status.values()) == {SECTION_UNKNOWN}

    url = _runs_url(company.id, tender.id)
    refused = await client_a.post(url)
    assert refused.status_code == 409
    assert refused.json()["error"]["details"]["package_status"] == PACKAGE_INCOMPLETE
    accepted = await client_a.post(url, json={"accept_incomplete_package": True})
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["accepted_incomplete_package"] is True
    assert accepted.json()["summary"]["package_incomplete"] is True


async def test_with_requirements_an_incomplete_package_records_whether_it_was_accepted(
    db, tenant_a, client_a
):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _global_run(db, tender)
    await _gap(db, tender, "DOC-04", ["sites"])
    await db.commit()
    first = await client_a.post(_runs_url(company.id, tender.id))
    assert first.status_code == 201, first.text
    assert first.json()["accepted_incomplete_package"] is False
    assert first.json()["summary"]["package_incomplete"] is True


async def test_a_complete_package_records_nothing(db, tenant_a, client_a):
    _, company = tenant_a
    tender, *_ = await _synthetic_tender(db)
    await _global_run(db, tender)
    await db.commit()
    first = await client_a.post(
        _runs_url(company.id, tender.id), json={"accept_incomplete_package": True}
    )
    assert first.status_code == 201, first.text
    assert first.json()["accepted_incomplete_package"] is None
    assert first.json()["summary"]["package_incomplete"] is False


# --------------------------------------------------------------------------- #
# ROLE_OVERRIDE_DIVERGES                                                       #
# --------------------------------------------------------------------------- #


async def test_a_company_override_that_diverges_from_the_global_role_is_reported(
    db, tenant_a, tenant_b
):
    _, company = tenant_a
    tender = await make_public_tender(db)
    pcap, _ppt = await public_package(db, tender)
    await global_classification.classify_tender_globally(
        db, tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    await db.commit()

    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    assert not [f for f in run.findings if f.explanation_code == X.ROLE_OVERRIDE_DIVERGES]

    await decide(
        db, company, pcap, DocumentRole.CHARACTERISTICS_TABLE, await current_sha(db, pcap.id)
    )
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    found = [f for f in run.findings if f.explanation_code == X.ROLE_OVERRIDE_DIVERGES]
    assert len(found) == 1 and found[0].finding_type == "warning"
    (document,) = found[0].details_json["documents"]
    assert document["document_id"] == str(pcap.id)
    assert document["document_name"] == "PCAP.pdf"
    assert document["company_role"] == "characteristics_table"
    assert document["global_role"] == "pcap"
    assert document["global_source"] == "global_classifier"

    # Un override que coincideix amb el rol global no diverteix.
    _, other_company = tenant_b
    await decide(db, other_company, pcap, DocumentRole.PCAP, await current_sha(db, pcap.id))
    other = (
        await analysis.analyze_tender(db, company_id=other_company.id, tender_id=tender.id)
    ).run
    assert not [f for f in other.findings if f.explanation_code == X.ROLE_OVERRIDE_DIVERGES]


async def test_an_operator_review_that_ends_the_divergence_makes_the_analysis_stale(db, tenant_a):
    """La revisio de l'operador es informativa (no canvia la revisio del plec ni la
    passada global), pero canvia la troballa: l'analisi ha de constar com a caducada."""
    _, company = tenant_a
    operator = await create_operator(db)
    tender = await make_public_tender(db)
    pcap, _ppt = await public_package(db, tender)
    global_run = await global_classification.classify_tender_globally(
        db, tender.id, GlobalClassificationTrigger.OPERATOR_REQUEST
    )
    await db.commit()
    sha = await current_sha(db, pcap.id)
    await decide(db, company, pcap, DocumentRole.CHARACTERISTICS_TABLE, sha)
    run = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    assert [f for f in run.findings if f.explanation_code == X.ROLE_OVERRIDE_DIVERGES]
    assert (await analysis.current_analysis(db, company.id, tender.id)).stale is False

    await document_roles.record_role_review(
        db,
        document_id=pcap.id,
        content_sha256=sha,
        role=DocumentRole.CHARACTERISTICS_TABLE,
        actor=operator,
        reason="La primera pagina es el quadre de caracteristiques.",
        based_on_run_id=global_run.id,
    )
    await db.commit()
    assert (await analysis.current_analysis(db, company.id, tender.id)).stale is True
    again = (await analysis.analyze_tender(db, company_id=company.id, tender_id=tender.id)).run
    assert again.id != run.id
    assert not [f for f in again.findings if f.explanation_code == X.ROLE_OVERRIDE_DIVERGES]
