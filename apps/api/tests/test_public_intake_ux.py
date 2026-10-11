"""MUSTs d'UX de 3B-2 (§7.1, 1-5) que necessiten una dada del backend.

* Historial: `origin` de cada revisio derivat del `trigger` amb un mapa TANCAT. Un
  trigger que el codi escrigui i el mapa no conegui fa fallar
  `test_every_trigger_written_by_the_code_has_an_origin`.
* Objecte: el `summary` del feed agregat es una plantilla («Id licitación…»), no una
  descripcio. `normalize()` desa `description=None` i el text cru a
  `source_metadata.feed_summary`; el backfill (`app.tenders.description_backfill`)
  arregla els existents de manera idempotent.
* «0 de 0»: `DossierCoverageRead.analysis_state` (`absent`/`current`/`stale`) i
  `mandatory_total` nul nomes sense analisi; el blocador `analysis_missing` impedeix
  dir mai «Dossier complet» sense cap analisi.
* Plecs: cada document diu si es analitzable (`ANALYZABLE_DOCUMENT`), els retirats
  es veuen a part amb la data i el paquet (estat i forats) arriba a la fitxa.
* Pre-fit: el resum de l'empresa diu si hi ha preferencies de contracte.
* Registre: `PACKAGE_GAP` i `ROLE_OVERRIDE_DIVERGES` son codis d'explicacio de la v4.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.analysis import engine, registry
from app.analysis.fingerprint import PACKAGE_SECTIONS
from app.ingestion.placsp.normalizer import SOURCE_METADATA_KEYS, normalize
from app.ingestion.placsp.parser import ParsedEntry
from app.models.enums import (
    AnalysisRunStatus,
    DocumentContributionStatus,
    PackageGapCode,
    PackageGapProducer,
    SourceType,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.package import TenderPackageGap
from app.models.profile import CompanyContractPreferences
from app.models.requirements import RequirementExtractionRun
from app.models.sources import Source
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Tender, TenderDocument
from app.schemas.tenders import REVISION_ORIGINS, TenderPackageRead, revision_origin
from app.schemas.workspace import DossierCoverageRead
from app.services import bid_workspace as workspace_service
from app.services import workspace_dossier
from app.services.tender_lifecycle import (
    DOCUMENT_INTAKE_TRIGGER,
    NORMALIZATION_TRIGGER,
    record_revision,
)
from app.tenders.description_backfill import backfill_descriptions
from app.tenders.search import is_feed_summary_template
from app.workspace.dossier import (
    BLOCKER_ANALYSIS_MISSING,
    BLOCKER_ANALYSIS_OUTDATED,
    BLOCKER_DECISION_NOT_GO,
    ReadinessInput,
    evaluate_readiness,
)

API_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (
    "Id licitación: 138/2026; Órgano de Contratación: Ajuntament d'Odèn; "
    "Importe: 310913.73 EUR; Estado: EN PLAZO"
)


async def _source(db, source_type=SourceType.SYNDICATION_ATOM) -> Source:
    source = Source(code=f"s-{uuid.uuid4().hex[:8]}", name="Font", source_type=source_type)
    db.add(source)
    await db.flush()
    return source


async def _tender(db, *, description=None, source_type=SourceType.SYNDICATION_ATOM) -> Tender:
    source = await _source(db, source_type)
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title="Servei de manteniment de la xarxa de fibra",
        description=description,
        deadline_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


# --------------------------------------------------------------------------- #
# MUST 1 - Historial amb origen                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("trigger", "origin"),
    [
        ("ingestion", "source"),
        ("tombstone", "source"),
        ("document_processing", "internal"),
        (DOCUMENT_INTAKE_TRIGGER, "internal"),
        (NORMALIZATION_TRIGGER, "internal"),
        ("manual_create", "manual"),
        ("manual_edit", "manual"),
    ],
)
def test_each_known_trigger_has_its_origin(trigger, origin):
    assert revision_origin(trigger) == origin


def test_an_unknown_trigger_falls_back_to_internal_with_a_warning(caplog):
    """En execucio mai un 500 (fix round 1, I2): un trigger fora del mapa es llegeix
    com a `internal` i s'avisa. La guarda contra triggers nous sense origen es
    `test_every_trigger_written_by_the_code_has_an_origin`, no el runtime."""
    with caplog.at_level(logging.WARNING, logger="app.tenders.changes"):
        assert revision_origin("a_trigger_nobody_mapped") == "internal"
    assert any("a_trigger_nobody_mapped" in record.getMessage() for record in caplog.records)


async def test_history_api_never_fails_on_an_unknown_trigger(db, client_a):
    tender = await _tender(db)
    await record_revision(db, tender, trigger="a_trigger_nobody_mapped")
    await db.commit()

    response = await client_a.get(f"/api/v1/tenders/{tender.id}/history")

    assert response.status_code == 200, response.text
    assert [(r["trigger"], r["origin"]) for r in response.json()] == [
        ("a_trigger_nobody_mapped", "internal")
    ]


def _written_triggers() -> set[str]:
    """Cada valor de `trigger` que el codi (app i scripts) pot escriure."""
    found: set[str] = set()
    literal = re.compile(r"trigger(?:\s*:\s*str)?\s*=\s*\"([a-z_]+)\"")
    constant = re.compile(r"^[A-Z_]*TRIGGER\s*(?::\s*str)?\s*=\s*\"([a-z_]+)\"", re.MULTILINE)
    for folder in ("app", "scripts"):
        for path in (API_ROOT / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if (
                "record_revision" not in text
                and "TenderRevision" not in text
                and "TRIGGER" not in text
            ):
                continue
            found.update(literal.findall(text))
            found.update(constant.findall(text))
    return found


def test_every_trigger_written_by_the_code_has_an_origin():
    """Si algu escriu un trigger nou sense donar-li origen, aquest test falla."""
    written = _written_triggers()
    # El default de `record_revision` i el de la columna.
    assert {"ingestion", "tombstone", "document_processing", "document_intake"} <= written
    # Triggers de classificacio (`ClassificationTrigger`) no son de revisions.
    revision_like = {t for t in written if t not in {"reception", "request"}}
    assert revision_like <= set(REVISION_ORIGINS), sorted(revision_like - set(REVISION_ORIGINS))


async def test_history_api_returns_the_origin_of_each_revision(db, client_a):
    tender = await _tender(db)
    await record_revision(db, tender, trigger="ingestion")
    await db.commit()
    tender.title = "Servei de manteniment (modificat)"
    await record_revision(db, tender, trigger="ingestion")
    tender.title = "Servei de manteniment (intern)"
    await record_revision(db, tender, trigger=DOCUMENT_INTAKE_TRIGGER)
    await db.commit()

    body = (await client_a.get(f"/api/v1/tenders/{tender.id}/history")).json()

    assert [(r["trigger"], r["origin"]) for r in body] == [
        (DOCUMENT_INTAKE_TRIGGER, "internal"),
        ("ingestion", "source"),
        ("ingestion", "source"),
    ]


# --------------------------------------------------------------------------- #
# MUST 2 - Objecte                                                              #
# --------------------------------------------------------------------------- #


def _entry(summary: str | None) -> ParsedEntry:
    return ParsedEntry(
        external_id="https://contrataciondelestado.es/sindicacion/x/1",
        updated_at=None,
        raw_xml=b"<entry/>",
        title="Servei de neteja",
        summary=summary,
    )


def test_the_feed_template_is_detected():
    assert is_feed_summary_template(TEMPLATE)
    assert is_feed_summary_template(
        "Id licitación: P.A. 2024-0-9;Órgano de Contratación: Hospital;Importe: 1 EUR;"
    )
    assert not is_feed_summary_template("Servei de neteja de les escoles municipals.")
    assert not is_feed_summary_template(None)
    assert not is_feed_summary_template("")


def test_normalize_drops_the_template_and_keeps_the_raw_text():
    data = normalize(_entry(TEMPLATE))

    assert data.description is None, "el titol no es l'objecte, i la plantilla tampoc"
    assert data.source_metadata["feed_summary"] == TEMPLATE
    assert "feed_summary" in SOURCE_METADATA_KEYS


def test_normalize_keeps_a_real_description():
    data = normalize(_entry("Servei de neteja de les escoles municipals."))

    assert data.description == "Servei de neteja de les escoles municipals."
    assert "feed_summary" not in data.source_metadata


async def test_backfill_is_idempotent_over_three_tenders(db):
    templated = await _tender(db, description=TEMPLATE)
    real = await _tender(db, description="Servei de neteja de les escoles.")
    manual = await _tender(db, description=TEMPLATE, source_type=SourceType.MANUAL)
    for tender in (templated, real, manual):
        await record_revision(db, tender, trigger="ingestion")
    await db.commit()
    ids = {tender.id for tender in (templated, real, manual)}

    first = await backfill_descriptions(db, batch_size=2)

    assert first.updated == 1
    assert first.scanned >= 2, "les manuals no es revisen"
    rows = {
        t.id: t
        for t in (
            await db.scalars(
                sa.select(Tender)
                .where(Tender.id.in_(ids))
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    assert rows[templated.id].description is None
    assert rows[templated.id].source_metadata["feed_summary"] == TEMPLATE
    assert rows[real.id].description == "Servei de neteja de les escoles."
    assert rows[manual.id].description == TEMPLATE, "una licitacio manual no es toca"

    # La correccio queda a l'historial com a processament intern, mai de la font.
    revision = await db.scalar(
        sa.select(TenderRevision)
        .where(TenderRevision.tender_id == templated.id)
        .order_by(TenderRevision.revision_number.desc())
        .limit(1)
    )
    assert revision.trigger == NORMALIZATION_TRIGGER
    assert revision_origin(revision.trigger) == "internal"
    codes = (
        await db.scalars(
            sa.select(TenderChangeEvent.change_code).where(
                TenderChangeEvent.to_revision_id == revision.id
            )
        )
    ).all()
    assert codes == ["DESCRIPTION_CHANGED"]

    revisions_before = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TenderRevision)
        .where(TenderRevision.tender_id.in_(ids))
    )
    second = await backfill_descriptions(db, batch_size=2)

    assert second.updated == 0
    revisions_after = await db.scalar(
        sa.select(sa.func.count())
        .select_from(TenderRevision)
        .where(TenderRevision.tender_id.in_(ids))
    )
    assert revisions_after == revisions_before


# --------------------------------------------------------------------------- #
# MUST 3 - Sense analisi no hi ha «0 de 0»                                       #
# --------------------------------------------------------------------------- #


def test_readiness_without_analysis_is_never_complete():
    missing = evaluate_readiness(
        ReadinessInput(dossier_started=True, decision_is_go=True, analysis_missing=True)
    )
    assert missing.blockers == (BLOCKER_ANALYSIS_MISSING,)
    assert not missing.is_ready

    ready = evaluate_readiness(ReadinessInput(dossier_started=True, decision_is_go=True))
    assert ready.is_ready


def test_coverage_read_keeps_the_total_null_only_without_analysis():
    absent = DossierCoverageRead(analysis_state="absent", mandatory_total=None)
    assert absent.mandatory_total is None
    with pytest.raises(ValueError):
        DossierCoverageRead(analysis_state="absent", mandatory_total=0)
    with pytest.raises(ValueError):
        DossierCoverageRead(analysis_state="current", mandatory_total=None)
    assert DossierCoverageRead(analysis_state="current", mandatory_total=0).mandatory_total == 0


async def _run(db, tender, **extra) -> RequirementExtractionRun:
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="anthropic",
        model="claude-sonnet-5",
        is_current=True,
        **extra,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def _workspace(db, tenant, tender):
    user, company = tenant
    outcome = await workspace_service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=user.id
    )
    await db.commit()
    return outcome.workspace


async def test_dossier_without_any_analysis_is_absent(db, tenant_a, client_a):
    tender = await _tender(db)
    workspace = await _workspace(db, tenant_a, tender)

    dossier = await workspace_dossier.load_dossier(db, workspace)

    assert dossier.analysis_state == "absent"
    assert BLOCKER_ANALYSIS_MISSING in dossier.readiness.blockers
    assert dossier.readiness.blockers[:2] == (BLOCKER_DECISION_NOT_GO, BLOCKER_ANALYSIS_MISSING)

    _, company = tenant_a
    base = f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"
    body = (await client_a.get(f"{base}/dossier")).json()
    assert body["coverage"]["analysis_state"] == "absent"
    assert body["coverage"]["mandatory_total"] is None
    assert "analysis_missing" in body["readiness"]["blockers"]

    detail = (await client_a.get(base)).json()
    assert detail["dossier_coverage"]["analysis_state"] == "absent"
    assert detail["dossier_coverage"]["mandatory_total"] is None
    assert "analysis_missing" in detail["readiness_blockers"]


async def test_dossier_with_the_current_analysis_is_current(db, tenant_a, client_a):
    tender = await _tender(db)
    await _run(db, tender)
    workspace = await _workspace(db, tenant_a, tender)

    dossier = await workspace_dossier.load_dossier(db, workspace)

    assert dossier.analysis_state == "current"
    assert BLOCKER_ANALYSIS_MISSING not in dossier.readiness.blockers
    _, company = tenant_a
    body = (
        await client_a.get(f"/api/v1/companies/{company.id}/workspaces/{workspace.id}/dossier")
    ).json()
    assert body["coverage"]["analysis_state"] == "current"
    assert body["coverage"]["mandatory_total"] == 0


async def test_dossier_with_an_outdated_analysis_is_stale(db, tenant_a):
    tender = await _tender(db)
    removed = TenderDocument(
        tender_id=tender.id,
        name="PCAP.pdf",
        document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
        url="https://contrataciondelestado.es/feed/pcap.pdf",
        origin=TenderDocumentOrigin.FEED,
        governing_source_id=tender.source_id,
        removed_at=datetime.now(UTC),
    )
    db.add(removed)
    await db.commit()
    await _run(db, tender, chunk_coverage={"chunks": [{"tender_document_id": str(removed.id)}]})
    workspace = await _workspace(db, tenant_a, tender)

    dossier = await workspace_dossier.load_dossier(db, workspace)

    assert dossier.analysis_state == "stale"
    assert BLOCKER_ANALYSIS_MISSING not in dossier.readiness.blockers
    assert BLOCKER_ANALYSIS_OUTDATED in dossier.readiness.blockers


# --------------------------------------------------------------------------- #
# MUST 5 - Plecs                                                                #
# --------------------------------------------------------------------------- #


async def test_tender_detail_says_what_is_analyzable_removed_and_missing(db, client_a):
    tender = await _tender(db)

    def document(name, **fields) -> TenderDocument:
        return TenderDocument(
            tender_id=tender.id,
            name=name,
            document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
            url=f"https://contrataciondelestado.es/feed/{uuid.uuid4().hex[:6]}.pdf",
            **fields,
        )

    feed = document(
        "PCAP.pdf", origin=TenderDocumentOrigin.FEED, governing_source_id=tender.source_id
    )
    db.add(feed)
    await db.flush()
    alias = document(
        "PCAP (copia).pdf",
        origin=TenderDocumentOrigin.FEED,
        governing_source_id=tender.source_id,
        duplicate_of_document_id=feed.id,
    )
    pending = document(
        "Annex 3.pdf",
        origin=TenderDocumentOrigin.OPERATOR_URL,
        contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
    )
    gone = document(
        "PPT antic.pdf",
        origin=TenderDocumentOrigin.FEED,
        governing_source_id=tender.source_id,
        removed_at=datetime(2026, 9, 30, 10, 0, tzinfo=UTC),
    )
    db.add_all([alias, pending, gone])
    db.add_all(
        [
            TenderPackageGap(
                tender_id=tender.id,
                code=PackageGapCode.REQUIRED_ROLE_MISSING,
                detail={"role": "ppt", "key": "ppt"},
                would_provide=["requirements", "scope_items"],
                producer=PackageGapProducer.CLASSIFICATION,
            ),
            TenderPackageGap(
                tender_id=tender.id,
                code=PackageGapCode.REFERENCED_DOCUMENT_MISSING,
                detail={"code": "DOC-03", "key": "DOC-03"},
                would_provide=[],
                producer=PackageGapProducer.CLASSIFICATION,
            ),
            TenderPackageGap(
                tender_id=tender.id,
                code=PackageGapCode.ANNEX_MISSING,
                detail={"key": "resolt"},
                would_provide=[],
                producer=PackageGapProducer.OPERATOR,
                resolved_at=datetime.now(UTC),
            ),
        ]
    )
    await db.commit()

    body = (await client_a.get(f"/api/v1/tenders/{tender.id}")).json()

    analyzable = {d["name"]: d["analyzable"] for d in body["documents"]}
    assert analyzable == {"PCAP.pdf": True, "PCAP (copia).pdf": False, "Annex 3.pdf": False}
    assert [d["name"] for d in body["removed_documents"]] == ["PPT antic.pdf"]
    assert body["removed_documents"][0]["removed_at"].startswith("2026-09-30")
    assert body["removed_documents"][0]["analyzable"] is False

    package = body["package"]
    assert package["status"] == "incomplete"
    assert sorted((g["code"], g["key"], g["would_provide"]) for g in package["gaps"]) == [
        ("referenced_document_missing", "DOC-03", []),
        ("required_role_missing", "ppt", ["requirements", "scope_items"]),
    ], "nomes els oberts"
    # M2: l'estat de cada seccio viatja amb el paquet. Sense passada ni files, i
    # amb un forat que nomena `scope_items`, cap seccio es pot donar per declarada.
    assert set(package["section_status"]) == set(PACKAGE_SECTIONS)
    assert package["section_status"]["scope_items"] == "unknown"


async def test_tender_detail_without_gaps_nor_classification_is_unknown(db, client_a):
    tender = await _tender(db)

    body = (await client_a.get(f"/api/v1/tenders/{tender.id}")).json()

    assert body["package"]["status"] == "unknown"
    assert body["package"]["gaps"] == []
    assert body["package"]["section_status"] == dict.fromkeys(PACKAGE_SECTIONS, "unknown")
    assert body["removed_documents"] == []


def test_package_read_section_status_is_a_closed_vocabulary():
    read = TenderPackageRead(status="unknown", section_status={"blocks": "none_declared"})
    assert read.section_status == {"blocks": "none_declared"}
    assert TenderPackageRead(status="unknown").section_status == {}
    with pytest.raises(ValueError):
        TenderPackageRead(status="unknown", section_status={"blocks": "whatever"})


# --------------------------------------------------------------------------- #
# MUST 4 - Pre-fit: el perfil diu si hi ha preferencies de contracte              #
# --------------------------------------------------------------------------- #


async def test_company_summary_counts_contract_preferences(db, tenant_a, client_a):
    _, company = tenant_a
    path = f"/api/v1/companies/{company.id}/summary"
    assert (await client_a.get(path)).json()["counts"]["contract_preferences"] == 0

    db.add(CompanyContractPreferences(company_id=company.id))
    await db.commit()

    assert (await client_a.get(path)).json()["counts"]["contract_preferences"] == 1


# --------------------------------------------------------------------------- #
# Registre: codis de la v4                                                      #
# --------------------------------------------------------------------------- #


def test_package_codes_live_in_the_registry_v4_table():
    codes = registry.ExplanationCode
    assert codes.PACKAGE_GAP == "PACKAGE_GAP"
    assert codes.ROLE_OVERRIDE_DIVERGES == "ROLE_OVERRIDE_DIVERGES"
    v4 = registry.EXPLANATIONS_BY_VERSION[registry.RULES_VERSION_V4]
    v3 = registry.EXPLANATIONS_BY_VERSION[registry.RULES_VERSION_V3]
    for code in (codes.PACKAGE_GAP, codes.ROLE_OVERRIDE_DIVERGES):
        assert v4[code] and v4[code] != registry.UNKNOWN_EXPLANATION_TEXT
        assert code not in v3, "la v3 queda congelada"
    # El motor fa servir els del registre: cap constant propia.
    assert not hasattr(engine, "PACKAGE_GAP")
    assert not hasattr(engine, "ROLE_OVERRIDE_DIVERGES")
    source = (API_ROOT / "app" / "analysis" / "engine.py").read_text(encoding="utf-8")
    assert 'PACKAGE_GAP = "PACKAGE_GAP"' not in source
    assert 'ROLE_OVERRIDE_DIVERGES = "ROLE_OVERRIDE_DIVERGES"' not in source


# --------------------------------------------------------------------------- #
# Fix round 1 + lot 9A (addendum 2026-10-09)                                    #
# --------------------------------------------------------------------------- #

MANUAL_TENDERS = "/api/v1/companies/{company}/tenders"
MANUAL_REQUIREMENTS = "/api/v1/companies/{company}/tenders/{tender}/requirements"


async def _manual_tender(client, company) -> uuid.UUID:
    response = await client.post(
        MANUAL_TENDERS.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": "Licitacio oberta a ma"},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["tender"]["id"])


async def test_requirements_read_says_the_analysis_state_like_the_dossier(db, tenant_a, client_a):
    """M1: la fitxa llegeix `analysis_state` del mateix calcul que el dossier
    (`requirement_analysis_state`): una licitacio manual amb requisits escrits a ma i
    sense cap run es `current`, no «sense analisi»."""
    _, company = tenant_a
    tender = await _tender(db)
    path = f"/api/v1/tenders/{tender.id}/analysis/requirements?company_id={company.id}"
    assert (await client_a.get(path)).json()["analysis_state"] == "absent"

    manual_id = await _manual_tender(client_a, company)
    manual_path = f"/api/v1/tenders/{manual_id}/analysis/requirements?company_id={company.id}"
    assert (await client_a.get(manual_path)).json()["analysis_state"] == "absent"
    created = await client_a.post(
        MANUAL_REQUIREMENTS.format(company=company.id, tender=manual_id),
        json={
            "title": "Assegurança de responsabilitat civil",
            "category": "insurance",
            "obligation": "mandatory",
            "scope": "tender",
        },
    )
    assert created.status_code == 201, created.text
    body = (await client_a.get(manual_path)).json()
    assert body["run"] is None
    assert body["analysis_state"] == "current"

    await _run(db, tender)
    assert (await client_a.get(path)).json()["analysis_state"] == "current"


async def test_requirements_read_says_stale_when_the_run_is_outdated(db, tenant_a, client_a):
    _, company = tenant_a
    tender = await _tender(db)
    removed = TenderDocument(
        tender_id=tender.id,
        name="PCAP.pdf",
        document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
        url="https://contrataciondelestado.es/feed/pcap-stale.pdf",
        origin=TenderDocumentOrigin.FEED,
        governing_source_id=tender.source_id,
        removed_at=datetime.now(UTC),
    )
    db.add(removed)
    await db.commit()
    await _run(db, tender, chunk_coverage={"chunks": [{"tender_document_id": str(removed.id)}]})

    path = f"/api/v1/tenders/{tender.id}/analysis/requirements?company_id={company.id}"
    assert (await client_a.get(path)).json()["analysis_state"] == "stale"


async def test_compliance_without_any_requirement_is_refused_with_422(db, tenant_a, client_a):
    """A7: el guard existent del compliment, provat explicitament per l'API."""
    _, company = tenant_a
    tender = await _tender(db)

    response = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/compliance", json={}
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "no_requirements_to_assess"


async def test_analysis_v3_without_any_requirement_is_refused_with_409(db, tenant_a, client_a):
    """A7: el guard de l'analisi v3 (Task 5), provat explicitament per l'API."""
    _, company = tenant_a
    tender = await _tender(db)

    response = await client_a.post(
        f"/api/v1/companies/{company.id}/tenders/{tender.id}/analysis/runs", json={}
    )

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "analysis_prerequisites_missing"
    assert error["details"]["missing"] == ["requirements"]


async def test_workspace_list_carries_the_analysis_state(db, tenant_a, client_a):
    """A13: l'etiqueta del workspace depen d'`analysis_state`, tambe a la llista."""
    _, company = tenant_a
    tender = await _tender(db)
    workspace = await _workspace(db, tenant_a, tender)
    path = f"/api/v1/companies/{company.id}/workspaces"

    rows = (await client_a.get(path)).json()
    assert [r["analysis_state"] for r in rows if r["id"] == str(workspace.id)] == ["absent"]

    await _run(db, tender)
    rows = (await client_a.get(path)).json()
    assert [r["analysis_state"] for r in rows if r["id"] == str(workspace.id)] == ["current"]
