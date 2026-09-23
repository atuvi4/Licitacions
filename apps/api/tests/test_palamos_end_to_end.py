"""Acceptació reproduïble d'Sprint 3, d'extrem a extrem (S3-S11).

Catorze tests independents que passen per casualitat no demostren res. El que
aquest fitxer demostra és una **història**:

    licitació a mà → plecs → candidatura → provinença → requisits → magnituds
    → opcions → línies d'oferta → preu objectiu → dubtes → readiness

i que, feta dues vegades, dona el mateix número.

Tres regles que el sostenen, i que són el motiu que existeixi:

**El recorregut passa per l'API real.** Escriure a la base per saltar-se una
transició que ja té endpoint provaria el model de dades, no el producte. La
base només s'hi fa servir per mirar el que l'API no exposa i per muntar
l'usuari inicial.

**El fixture és un oracle independent.** Els imports esperats es van escriure
abans de mirar què responia el motor, i no es tornen a derivar aquí. Veure
`test_palamos_fixture.py`, que ho protegeix.

**El resultat del concurs no és un oracle.** Que una oferta real guanyés no diu
si aquest workflow és correcte. Això mesura reproductibilitat i invariants, no
probabilitat de victòria.

Zero model de pagament, zero xarxa, zero dades de producció: `LLM_PROVIDER` no
s'hi toca perquè no hi ha cap anàlisi automàtica —els requisits d'aquesta
acceptació els escriu una persona— i tot el que es crea viu dins la base de
test.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models.enums import MembershipRole
from app.models.offer import EconomicOfferLine
from tests.fixtures.pdf_builder import build_pdf

pytestmark = pytest.mark.asyncio

# --------------------------------------------------------------------------- #
# Rutes                                                                         #
# --------------------------------------------------------------------------- #

TENDERS = "/api/v1/companies/{company}/tenders"
FACETS = TENDERS + "/facets"
UPLOAD = TENDERS + "/{tender}/documents"
VERSIONS = UPLOAD + "/{document}/versions"
REQUIREMENTS = TENDERS + "/{tender}/requirements"
CITATIONS = REQUIREMENTS + "/{requirement}/citations"
FILE_REF = TENDERS + "/{tender}/source-refs/document-file"
ANALYSIS = "/api/v1/tenders/{tender}/analysis/requirements"
MATCHES = "/api/v1/companies/{company}/matches"

OPEN_WORKSPACE = TENDERS + "/{tender}/workspace"
WORKSPACE = "/api/v1/companies/{company}/workspaces/{workspace}"
DECISIONS = WORKSPACE + "/decisions"
DIMENSIONS = WORKSPACE + "/dimensions"
OPTIONS = WORKSPACE + "/solution-options"
SOLUTIONS = WORKSPACE + "/technical-solutions"
OFFER = WORKSPACE + "/economic-offer"
ISSUES = WORKSPACE + "/issues"
READINESS = WORKSPACE + "/final-readiness"
REVIEW_DOCUMENTS = WORKSPACE + "/documents/review"
REVIEW_SCOPE = WORKSPACE + "/technical-scope/review"

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: Un DOCX i un XLSX són ZIP: la firma només diu «PK» i el tipus fi el declara
#: qui puja. Bytes diferents per a v1 i v2 perquè la citació els pugui distingir.
OFFICE = b"PK\x03\x04" + b"\x00" * 64
XLSX_V1 = b"PK\x03\x04" + b"\x01" * 64
XLSX_V2 = b"PK\x03\x04" + b"\x02" * 64

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "benchmarks" / "palamos_s3_benchmark.json"


@pytest.fixture(scope="module")
def palamos() -> dict:
    """El fixture, en només lectura. Cap test l'escriu ni el regenera."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Ajudants                                                                      #
# --------------------------------------------------------------------------- #


def _codes(readiness: dict) -> set[str]:
    return {finding["code"] for finding in readiness["findings"]}


def _finding(readiness: dict, code: str) -> dict | None:
    for finding in readiness["findings"]:
        if finding["code"] == code:
            return finding
    return None


async def _set_role(db, user_id, company_id, role: MembershipRole) -> None:
    """El mateix usuari amb un altre rol. El rol es llegeix a cada petició."""
    from app.models.identity import CompanyMembership

    await db.execute(
        sa.update(CompanyMembership)
        .where(
            CompanyMembership.user_id == user_id,
            CompanyMembership.company_id == company_id,
        )
        .values(role=role)
    )
    await db.commit()


def _line_of(offer: dict, line_id: str) -> dict:
    for lot in offer["lots"]:
        for section in lot["sections"]:
            for line in section["lines"]:
                if line["id"] == line_id:
                    return line
    raise AssertionError(f"la línia {line_id} no és a l'oferta")


def _live_lines(offer: dict) -> list[dict]:
    return [
        line
        for lot in offer["lots"]
        for section in lot["sections"]
        for line in section["lines"]
        if line["archived_at"] is None
    ]


# --------------------------------------------------------------------------- #
# El recorregut                                                                 #
# --------------------------------------------------------------------------- #


async def test_palamos_sprint3_end_to_end(
    client_a, client_b, client_factory, db, tenant_a, tenant_b, palamos
):
    """L'acceptació d'Sprint 3 sencera, en ordre, amb punts de control.

    Cada bloc és un pas del recorregut real i deixa escrit què s'hi comprova.
    Si el fitxer creix, creix per aquí: partir-lo en tests independents tornaria
    a donar catorze proves que no demostren cap workflow.
    """
    user_a, company_a = tenant_a
    _user_b, company_b = tenant_b
    # Identificadors com a valors, no com a atributs d'objectes ORM: el
    # recorregut expira la sessio moltes vegades i tocar-los despres seria una
    # carrega mandrosa des de codi sincron.
    company = company_a.id
    other_company = company_b.id
    user_id, user_email = user_a.id, user_a.email
    workspace = None

    # ------------------------------------------------------------------ #
    # 1. Licitació a mà, i el mateix `client_token` dues vegades          #
    # ------------------------------------------------------------------ #
    token = str(uuid.uuid4())
    body = {
        "client_token": token,
        "title": palamos["tender"]["title"],
        "expediente_number": palamos["tender"]["expediente_number"],
    }
    created = await client_a.post(TENDERS.format(company=company), json=body)
    assert created.status_code == 201, created.text
    assert created.json()["created"] is True
    tender = created.json()["tender"]["id"]

    retry = await client_a.post(TENDERS.format(company=company), json=body)
    assert retry.status_code == 200, retry.text
    assert retry.json()["created"] is False
    # Reintentar no crea una segona licitació ni n'obre una de bessona.
    assert retry.json()["tender"]["id"] == tender

    # El pressupost del plec, que és el que fa comparables les línies amb el
    # màxim. S'escriu per l'API, com ho faria una persona.
    from app.services import manual_tender

    db.expire_all()
    detail = await client_a.patch(
        f"{TENDERS.format(company=company)}/{tender}",
        json={
            "expected_revision": await manual_tender.current_revision_number(
                db, uuid.UUID(tender)
            ),
            "base_budget_without_tax": palamos["tender"]["base_budget_without_tax"],
            "currency": palamos["tender"]["currency"],
            "publication_date": palamos["clock"]["publication_date"],
            "deadline_at": palamos["clock"]["deadline_at"],
        },
    )
    assert detail.status_code == 200, detail.text

    # ------------------------------------------------------------------ #
    # 2. Tenancy: existeix per a A i no existeix per a B                  #
    # ------------------------------------------------------------------ #
    mine = await client_a.get(MATCHES.format(company=company))
    assert mine.status_code == 200, mine.text
    assert tender in {row["tender"]["id"] for row in mine.json()["items"]}

    facets = await client_a.get(FACETS.format(company=company))
    assert facets.status_code == 200, facets.text
    mine_total = facets.json()["total_tenders"]
    assert mine_total >= 1

    theirs = await client_b.get(MATCHES.format(company=other_company))
    assert theirs.status_code == 200, theirs.text
    assert tender not in {row["tender"]["id"] for row in theirs.json()["items"]}

    # El comptador també és una faceta: no pot delatar el que no es pot llistar.
    their_facets = await client_b.get(FACETS.format(company=other_company))
    assert their_facets.status_code == 200, their_facets.text
    assert their_facets.json()["total_tenders"] == 0

    # Amb l'UUID d'A al context de B: 404 a tot arreu, i no 403. Dir «no hi tens
    # accés» ja seria confirmar que existeix.
    for url in (
        UPLOAD.format(company=other_company, tender=tender),
        OPEN_WORKSPACE.format(company=other_company, tender=tender),
    ):
        refused = await client_b.get(url)
        assert refused.status_code == 404, (url, refused.status_code)

    # I tampoc no s'hi pot escriure. Dos endpoints d'escriptura amb cos valid:
    # el 404 ha d'arribar abans que res, no despres de validar-lo.
    refused = await client_b.post(
        UPLOAD.format(company=other_company, tender=tender),
        files={"file": ("x.pdf", build_pdf(["X"]), PDF)},
    )
    assert refused.status_code == 404, refused.text
    refused = await client_b.post(
        REQUIREMENTS.format(company=other_company, tender=tender),
        json={"title": "No hi arriba", "category": "technical_requirement"},
    )
    assert refused.status_code == 404, refused.text
    refused = await client_b.post(
        OPEN_WORKSPACE.format(company=other_company, tender=tender), json={}
    )
    assert refused.status_code == 404, refused.text
    refused = await client_b.patch(
        f"{TENDERS.format(company=other_company)}/{tender}",
        json={"expected_revision": 1, "title": "Robat"},
    )
    assert refused.status_code == 404, refused.text

    # I un usuari que és de totes dues empreses tampoc no hi arriba des de B:
    # el que decideix és el context de la URL, no de qui l'obre.
    from app.models.identity import CompanyMembership
    from tests.conftest import login

    db.add(
        CompanyMembership(
            user_id=user_id, company_id=other_company, role=MembershipRole.OWNER
        )
    )
    await db.commit()
    both = client_factory()
    await login(both, user_email)
    crossed = await both.get(UPLOAD.format(company=other_company, tender=tender))
    assert crossed.status_code == 404, crossed.text
    crossed = await both.post(
        OPEN_WORKSPACE.format(company=other_company, tender=tender), json={}
    )
    assert crossed.status_code == 404, crossed.text
    # I des del seu propi context, el mateix usuari hi arriba perfectament.
    assert (
        await both.get(UPLOAD.format(company=company, tender=tender))
    ).status_code == 200

    # ------------------------------------------------------------------ #
    # 3. Candidatura per la ruta real                                     #
    # ------------------------------------------------------------------ #
    opened = await client_a.post(
        OPEN_WORKSPACE.format(company=company, tender=tender), json={}
    )
    assert opened.status_code in (200, 201), opened.text
    workspace = opened.json()["workspace"]["id"]

    decided = await client_a.post(
        DECISIONS.format(company=company, workspace=workspace),
        json={"decision": "go", "reason": "Ens hi presentem."},
    )
    assert decided.status_code in (200, 201), decided.text

    # ------------------------------------------------------------------ #
    # 4. Plecs: dos documents lògics, un sol contingut                    #
    # ------------------------------------------------------------------ #
    pcap_bytes = build_pdf(
        ["Clausula 9.3. Es migren 181 linies mobils. " * 8, "Annex IV. 151 linies. " * 8]
    )
    first = await client_a.post(
        UPLOAD.format(company=company, tender=tender),
        files={"file": ("PCAP.pdf", pcap_bytes, PDF)},
    )
    assert first.status_code == 201, first.text
    assert first.json()["file"]["analysis_status"] == "extracted"
    assert first.json()["file"]["page_count"] == 2

    second = await client_a.post(
        UPLOAD.format(company=company, tender=tender),
        files={"file": ("PCAP-copia.pdf", pcap_bytes, PDF)},
    )
    assert second.status_code == 201, second.text
    # Deduplicació de BYTES, no de document lògic: dos documents, un contingut.
    assert second.json()["id"] != first.json()["id"]
    assert second.json()["file"]["sha256"] == first.json()["file"]["sha256"]
    # I la resposta no ho diu enlloc: la deduplicació és global i explicar-la
    # seria respondre per tots els tenants alhora.
    assert "content_reused" not in second.json()

    from app.models.tender_documents import (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentPage,
    )
    from app.models.tenders import TenderDocument

    db.expire_all()
    documents = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocument)
            .where(TenderDocument.tender_id == uuid.UUID(tender))
        )
    ).scalar_one()
    sha = first.json()["file"]["sha256"]
    content_id = (
        await db.execute(
            sa.select(TenderDocumentContent.id).where(TenderDocumentContent.sha256 == sha)
        )
    ).scalar_one()
    extractions = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocumentExtraction)
            .where(TenderDocumentExtraction.content_id == content_id)
        )
    ).scalar_one()
    pages = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocumentPage)
            .where(
                TenderDocumentPage.extraction_id
                == uuid.UUID(first.json()["file"]["extraction_id"])
            )
        )
    ).scalar_one()
    assert (documents, extractions, pages) == (2, 1, 2)

    # ------------------------------------------------------------------ #
    # 5. DOCX i XLSX: guardats, no llegits                                #
    # ------------------------------------------------------------------ #
    for name, mime, data in (
        ("Plec-tecnic.docx", DOCX, OFFICE),
        (palamos["source_ref"]["document_name"], XLSX, XLSX_V1),
    ):
        stored = await client_a.post(
            UPLOAD.format(company=company, tender=tender),
            files={"file": (name, data, mime)},
        )
        assert stored.status_code == 201, stored.text
        assert stored.json()["stored"] is True
        assert stored.json()["file"]["text_extraction_supported"] is False
        assert stored.json()["file"]["analysis_status"] == "unsupported"
        assert stored.json()["file"]["page_count"] == 0
        if mime == XLSX:
            spreadsheet = stored.json()

    # ------------------------------------------------------------------ #
    # 6. Revisió documental, i què la mou                                 #
    # ------------------------------------------------------------------ #
    from app.services import document_review

    reviewed = await client_a.post(REVIEW_DOCUMENTS.format(company=company, workspace=workspace))
    assert reviewed.status_code == 200, reviewed.text
    assert "tender_documents_unreviewed" not in _codes(reviewed.json())
    assert "tender_documents_changed" not in _codes(reviewed.json())

    db.expire_all()
    before_fingerprint = await document_review.fingerprint_of(db, uuid.UUID(tender))

    # Una solució tècnica validada, per poder afirmar què NO la invalida.
    solution = await client_a.post(
        SOLUTIONS.format(company=company, workspace=workspace),
        json={
            "title": "Migració de les línies mòbils",
            "proposed_solution": "Portabilitat de les línies i terminals nous per a operadora.",
            "supplier_name": "Yealink",
        },
    )
    assert solution.status_code in (200, 201), solution.text
    solution_id = solution.json()["id"]
    validated = await client_a.post(
        f"{SOLUTIONS.format(company=company, workspace=workspace)}/{solution_id}/validation",
        json={"note": "Revisada amb el proveïdor."},
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["validation_stale"] is False

    added = await client_a.post(
        UPLOAD.format(company=company, tender=tender),
        files={"file": ("Annex-IV.pdf", build_pdf(["Annex IV. " * 20]), PDF)},
    )
    assert added.status_code == 201, added.text

    db.expire_all()
    after_fingerprint = await document_review.fingerprint_of(db, uuid.UUID(tender))
    assert after_fingerprint.fingerprint != before_fingerprint.fingerprint

    readiness = (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    assert "tender_documents_changed" in _codes(readiness)

    # DOCUMENT_ADDED diu que hi ha material per llegir. No diu que el que
    # s'enviaria hagi canviat, i per això no invalida la solució tècnica.
    solutions = await client_a.get(SOLUTIONS.format(company=company, workspace=workspace))
    assert solutions.json()[0]["validation_stale"] is False

    # I les dues semàntiques no es barregen: un plec NOU no caduca cap paquet
    # congelat; una versió NOVA d'un que ja hi era, sí. Ho diu el contracte, i
    # es comprova aquí perquè el recorregut fa totes dues coses.
    from app.tenders.changes import IMPACT_MATRIX, PACKAGE_STALE_IMPACTS, ChangeCode

    assert not (IMPACT_MATRIX[ChangeCode.DOCUMENT_ADDED] & PACKAGE_STALE_IMPACTS)
    assert IMPACT_MATRIX[ChangeCode.DOCUMENT_REPLACED] & PACKAGE_STALE_IMPACTS

    # ------------------------------------------------------------------ #
    # 7. Requisits escrits a mà                                           #
    # ------------------------------------------------------------------ #
    from app.services import bid_workspace as workspace_service
    from app.services import technical_scope

    db.expire_all()
    workspace_row = await workspace_service.get_workspace(db, company, uuid.UUID(workspace))
    scope_before = await technical_scope.scope_fingerprint(db, workspace_row)

    requirement_ids = {}
    for spec in palamos["requirements"]:
        written = await client_a.post(
            REQUIREMENTS.format(company=company, tender=tender),
            json={
                "title": spec["title"],
                "category": spec["category"],
                "obligation": spec["obligation"],
            },
        )
        assert written.status_code == 201, written.text
        assert written.json()["origin"] == "manual"
        assert written.json()["run_id"] is None
        requirement_ids[spec["key"]] = written.json()["id"]

    effective = await client_a.get(ANALYSIS.format(tender=tender))
    assert effective.status_code == 200, effective.text
    assert set(requirement_ids.values()) <= {r["id"] for r in effective.json()["requirements"]}

    db.expire_all()
    workspace_row = await workspace_service.get_workspace(db, company, uuid.UUID(workspace))
    scope_after = await technical_scope.scope_fingerprint(db, workspace_row)
    assert scope_after != scope_before

    # ------------------------------------------------------------------ #
    # 8. Una citació que no es mou amb la versió vigent                   #
    # ------------------------------------------------------------------ #
    cited = await client_a.post(
        FILE_REF.format(company=company, tender=tender),
        json={
            "tender_document_file_id": spreadsheet["file"]["file_id"],
            "location_label": palamos["source_ref"]["location_label"],
        },
    )
    assert cited.status_code == 201, cited.text
    source_ref_id = cited.json()["id"]
    sha_v1 = spreadsheet["file"]["sha256"]

    linked = await client_a.post(
        CITATIONS.format(
            company=company, tender=tender, requirement=requirement_ids["mobils"]
        ),
        json={"source_ref_id": source_ref_id},
    )
    assert linked.status_code in (200, 201), linked.text

    v2 = await client_a.post(
        VERSIONS.format(company=company, tender=tender, document=spreadsheet["id"]),
        files={"file": (palamos["source_ref"]["document_name"], XLSX_V2, XLSX)},
    )
    assert v2.status_code == 201, v2.text
    assert v2.json()["file"]["version_number"] == 2
    assert v2.json()["file"]["sha256"] != sha_v1

    from app.services import source_refs

    db.expire_all()
    resolved = await source_refs.resolve_source_ref(
        db,
        await source_refs.get_source_ref(db, uuid.UUID(tender), uuid.UUID(source_ref_id)),
    )
    assert resolved.version_number == 1
    assert resolved.content_sha256 == sha_v1
    assert str(resolved.file_id) == spreadsheet["file"]["file_id"]
    assert resolved.ref.location_label == palamos["source_ref"]["location_label"]
    # I sobretot: no la vigent.
    assert resolved.content_sha256 != v2.json()["file"]["sha256"]

    # ------------------------------------------------------------------ #
    # 9. Magnitud: 151 contra 181, i qui decideix                         #
    # ------------------------------------------------------------------ #
    spec = palamos["dimension"]
    dimension = await client_a.post(
        DIMENSIONS.format(company=company, workspace=workspace),
        json={"key": spec["key"], "label": spec["label"], "unit": spec["unit"]},
    )
    assert dimension.status_code == 201, dimension.text
    dimension_id = dimension.json()["id"]
    revision = dimension.json()["revision"]
    assert dimension.json()["status"] == "undecided"

    observations_url = (
        f"{DIMENSIONS.format(company=company, workspace=workspace)}"
        f"/{dimension_id}/observations"
    )
    for key in ("observation_a", "observation_b"):
        note = spec[key]
        recorded = await client_a.post(
            observations_url,
            json={
                "expected_revision": revision,
                "source_kind": note["source_kind"],
                "value_text": note["value_text"],
                "value_num": note["value_num"],
            },
        )
        assert recorded.status_code in (200, 201), recorded.text
        revision = recorded.json()["revision"]

    # Dues lectures que no diuen el mateix: discrepància, sense que ningú la marqui.
    assert recorded.json()["status"] == "disputed"
    readiness = (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    assert "dimension_disputed" in _codes(readiness)

    decide_url = (
        f"{DIMENSIONS.format(company=company, workspace=workspace)}/{dimension_id}/decide"
    )
    decision = spec["decision"]
    decided = await client_a.post(
        decide_url,
        json={
            "expected_revision": revision,
            "working_value_text": decision["working_value_text"],
            "working_value_num": decision["working_value_num"],
            "reason": decision["reason"],
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "decided"
    assert Decimal(decided.json()["working_value_num"]) == Decimal(decision["working_value_num"])
    # Les dues observacions continuen existint: decidir no esborra el desacord.
    assert len(decided.json()["observations"]) == 2
    revision = decided.json()["revision"]

    # ------------------------------------------------------------------ #
    # 10. Després de decidir: el que concorda no reobre; el que no, sí    #
    # ------------------------------------------------------------------ #
    confirming = spec["confirming_observation"]
    same = await client_a.post(
        observations_url,
        json={
            "expected_revision": revision,
            "source_kind": confirming["source_kind"],
            "value_text": confirming["value_text"],
            "value_num": confirming["value_num"],
        },
    )
    assert same.status_code in (200, 201), same.text
    assert same.json()["status"] == "decided", "una observació que concorda no reobre res"
    revision = same.json()["revision"]

    conflicting = spec["conflicting_observation"]
    clash = await client_a.post(
        observations_url,
        json={
            "expected_revision": revision,
            "source_kind": conflicting["source_kind"],
            "value_text": conflicting["value_text"],
            "value_num": conflicting["value_num"],
        },
    )
    assert clash.status_code in (200, 201), clash.text
    assert clash.json()["status"] == "disputed", "evidència nova i conflictiva sí que reobre"
    revision = clash.json()["revision"]

    decided = await client_a.post(
        decide_url,
        json={
            "expected_revision": revision,
            "working_value_text": decision["working_value_text"],
            "working_value_num": decision["working_value_num"],
            "reason": "Es manté la clàusula 9.3 després de revisar l'inventari.",
        },
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "decided"
    dimension_revision = decided.json()["revision"]

    # ------------------------------------------------------------------ #
    # 11. Dues opcions germanes                                           #
    # ------------------------------------------------------------------ #
    chosen_spec = palamos["solution_options"]["selected"]
    rejected_spec = palamos["solution_options"]["rejected"]
    scenario = palamos["scenario"]

    chosen = await client_a.post(
        OPTIONS.format(company=company, workspace=workspace),
        json={
            "technical_solution_id": solution_id,
            "requirement_id": requirement_ids["mobils"],
            "supplier_name": scenario["option_supplier_before"],
            "product_name": scenario["option_product_before"],
            "quantity": chosen_spec["quantity"],
            "unit": chosen_spec["unit"],
            "pricing_basis": chosen_spec["pricing_basis"],
            "quote_status": "received",
            "quote_date": palamos["clock"]["quote_date"],
            "internal_economics": {
                "unit_cost_ex_vat": chosen_spec["capex_unit_cost_ex_vat"]
            },
        },
    )
    assert chosen.status_code == 201, chosen.text
    chosen_id = chosen.json()["id"]

    sibling = await client_a.post(
        OPTIONS.format(company=company, workspace=workspace),
        json={
            "technical_solution_id": solution_id,
            "supplier_name": rejected_spec["supplier_name"],
            "product_name": rejected_spec["product_name"],
            "quantity": rejected_spec["quantity"],
            "pricing_basis": chosen_spec["pricing_basis"],
            "internal_economics": {"unit_cost_ex_vat": rejected_spec["unit_cost_ex_vat"]},
        },
    )
    assert sibling.status_code == 201, sibling.text
    sibling_id = sibling.json()["id"]

    one = f"{OPTIONS.format(company=company, workspace=workspace)}/{chosen_id}"
    other = f"{OPTIONS.format(company=company, workspace=workspace)}/{sibling_id}"

    selected = await client_a.post(
        f"{one}/selection",
        json={"expected_revision": chosen.json()["revision"], "selection": "selected"},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["selection"] == "selected"
    assert selected.json()["selected_by"] is not None
    assert selected.json()["selected_at"] is not None

    # Triar-ne una NO en rebutja cap altra: rebutjar és una decisió pròpia.
    still = await client_a.get(other)
    assert still.status_code == 200, still.text
    assert still.json()["selection"] == "undecided"

    turned_down = await client_a.post(
        f"{other}/selection",
        json={
            "expected_revision": still.json()["revision"],
            "selection": "rejected",
            "reason": rejected_spec["reason"],
        },
    )
    assert turned_down.status_code == 200, turned_down.text
    assert turned_down.json()["selection"] == "rejected"
    assert turned_down.json()["selection_reason"] == rejected_spec["reason"]

    # Totes dues continuen existint. Rebutjar no arxiva ni esborra.
    listed = await client_a.get(OPTIONS.format(company=company, workspace=workspace))
    assert {row["id"] for row in listed.json()["items"]} >= {chosen_id, sibling_id}

    # ------------------------------------------------------------------ #
    # 12. Opció ↔ magnitud: relació sí, propagació no                     #
    # ------------------------------------------------------------------ #
    option_revision = selected.json()["revision"]
    linked = await client_a.put(
        f"{one}/dimensions/{dimension_id}", json={"expected_revision": option_revision}
    )
    assert linked.status_code in (200, 201), linked.text

    dependencies = await client_a.get(f"{one}/dimensions")
    assert dimension_id in {row["id"] for row in dependencies.json()}

    quantity_before = Decimal((await client_a.get(one)).json()["quantity"])
    moved = await client_a.post(
        decide_url,
        json={
            "expected_revision": dimension_revision,
            "working_value_text": "209 línies",
            "working_value_num": "209",
            "reason": "Prova de propagació: la magnitud es mou.",
        },
    )
    assert moved.status_code == 200, moved.text
    dimension_revision = moved.json()["revision"]

    assert Decimal((await client_a.get(one)).json()["quantity"]) == quantity_before, (
        "canviar la magnitud NO ha de moure la quantitat de l'opció"
    )

    back = await client_a.post(
        decide_url,
        json={
            "expected_revision": dimension_revision,
            "working_value_text": decision["working_value_text"],
            "working_value_num": decision["working_value_num"],
            "reason": decision["reason"],
        },
    )
    assert back.status_code == 200, back.text

    # ------------------------------------------------------------------ #
    # 13. El cost és intern, i el PATCH públic no se l'emporta            #
    # ------------------------------------------------------------------ #
    economic = (await client_a.get(one)).json()
    assert economic["internal_economics"] is not None
    assert Decimal(economic["internal_economics"]["unit_cost_ex_vat"]) == Decimal(
        chosen_spec["capex_unit_cost_ex_vat"]
    )

    await _set_role(db, user_id, company, MembershipRole.TECHNICAL)
    hidden = await client_a.get(one)
    assert hidden.status_code == 200, hidden.text
    # El bloc existeix o no existeix. Mai `null` a dins, i mai zero.
    assert hidden.json()["internal_economics"] is None

    public_patch = await client_a.patch(
        one,
        json={
            "expected_revision": hidden.json()["revision"],
            "description": "Auriculars sense fil per a les posicions d'operadora.",
        },
    )
    assert public_patch.status_code == 200, public_patch.text
    assert public_patch.json()["internal_economics"] is None

    await _set_role(db, user_id, company, MembershipRole.VIEWER)
    assert (await client_a.get(one)).json()["internal_economics"] is None

    await _set_role(db, user_id, company, MembershipRole.OWNER)
    kept = await client_a.get(one)
    assert Decimal(kept.json()["internal_economics"]["unit_cost_ex_vat"]) == Decimal(
        chosen_spec["capex_unit_cost_ex_vat"]
    ), "un PATCH de camps públics no pot esborrar el cost"

    # ------------------------------------------------------------------ #
    # 14. Adoptar l'opció com a línia. Tres vegades.                      #
    # ------------------------------------------------------------------ #
    offer_created = await client_a.post(OFFER.format(company=company, workspace=workspace))
    assert offer_created.status_code in (200, 201), offer_created.text
    offer = offer_created.json()["offer"]
    lot_id = offer["lots"][0]["id"]

    months = palamos["tender"]["duration_months"]
    lot_set = await client_a.patch(
        f"{OFFER.format(company=company, workspace=workspace)}/lots/{lot_id}",
        json={"expected_revision": offer["revision"], "duration_months": months},
    )
    assert lot_set.status_code == 200, lot_set.text
    offer = lot_set.json()

    section_created = await client_a.post(
        f"{OFFER.format(company=company, workspace=workspace)}/lots/{lot_id}/sections",
        json={"expected_revision": offer["revision"], "title": "Oferta econòmica"},
    )
    assert section_created.status_code in (200, 201), section_created.text
    offer = section_created.json()
    section_id = offer["lots"][0]["sections"][0]["id"]

    adopt_url = f"{one}/to-offer-line"
    stale_revision = offer["revision"]
    adopted = await client_a.post(
        adopt_url,
        json={
            "section_id": section_id,
            "expected_revision": stale_revision,
            "concept": "Auriculars sense fil per a operadora",
        },
    )
    assert adopted.status_code in (200, 201), adopted.text
    # La ruta retorna la LINIA, no l'oferta: adoptar es una accio sobre l'opcio.
    assert adopted.json()["solution_option_id"] == chosen_id
    wh62_line_id = adopted.json()["id"]

    # Un reintent amb una revisió ja antiga no és un canvi: 200 i la mateixa línia.
    again = await client_a.post(
        adopt_url,
        json={
            "section_id": section_id,
            "expected_revision": stale_revision,
            "concept": "Un altre concepte que no s'ha d'aplicar",
        },
    )
    assert again.status_code == 200, again.text
    assert again.json()["id"] == wh62_line_id
    # I el concepte del reintent no s'aplica: no era un canvi.
    assert again.json()["concept"] == adopted.json()["concept"]

    # I amb la revisió d'ARA tampoc: la idempotència no depèn de tenir el CAS
    # caducat. Sense la comprovació prèvia, aquesta crida intentaria un segon
    # INSERT de debò i xocaria amb l'índex únic parcial.
    offer = (await client_a.get(OFFER.format(company=company, workspace=workspace))).json()
    fresh_revision_before = offer["revision"]
    fresh = await client_a.post(
        adopt_url,
        json={
            "section_id": section_id,
            "expected_revision": offer["revision"],
            "concept": "Tampoc no s'ha d'aplicar",
        },
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["id"] == wh62_line_id
    assert fresh.json()["concept"] == adopted.json()["concept"]

    offer = (await client_a.get(OFFER.format(company=company, workspace=workspace))).json()
    assert offer["revision"] == fresh_revision_before, "un reintent no mou la revisió"
    assert [
        line["id"] for line in _live_lines(offer) if line["solution_option_id"] == chosen_id
    ] == [wh62_line_id]

    db.expire_all()
    live = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(EconomicOfferLine)
            .where(
                EconomicOfferLine.solution_option_id == uuid.UUID(chosen_id),
                EconomicOfferLine.archived_at.is_(None),
            )
        )
    ).scalar_one()
    assert live == 1, "exactament una línia viva per opció"

    # ------------------------------------------------------------------ #
    # 15. El snapshot no es mou sol                                       #
    # ------------------------------------------------------------------ #
    line = _line_of(offer, wh62_line_id)
    assert line["supplier_name"] == scenario["option_supplier_before"]
    assert line["product_name"] == scenario["option_product_before"]
    assert Decimal(line["internal_economics"]["unit_cost_ex_vat"]) == Decimal(
        chosen_spec["capex_unit_cost_ex_vat"]
    )
    assert line["diverged_fields"] == []

    option_now = (await client_a.get(one)).json()
    revised = await client_a.patch(
        one,
        json={
            "expected_revision": option_now["revision"],
            "supplier_name": scenario["option_supplier_after"],
            "product_name": scenario["option_product_after"],
        },
    )
    assert revised.status_code == 200, revised.text
    cost_fixed = await client_a.patch(
        f"{one}/internal-economics",
        json={
            "expected_revision": revised.json()["revision"],
            "unit_cost_ex_vat": chosen_spec["documented_unit_cost_ex_vat"],
        },
    )
    assert cost_fixed.status_code == 200, cost_fixed.text

    offer = (await client_a.get(OFFER.format(company=company, workspace=workspace))).json()
    line = _line_of(offer, wh62_line_id)
    assert line["supplier_name"] == scenario["option_supplier_before"], (
        "res no s'ha sincronitzat sol"
    )
    assert Decimal(line["internal_economics"]["unit_cost_ex_vat"]) == Decimal(
        chosen_spec["capex_unit_cost_ex_vat"]
    )
    assert set(line["diverged_fields"]) == {"supplier_name", "product_name", "unit_cost_ex_vat"}

    readiness = (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    diverged = _finding(readiness, "economic_line_option_diverged")
    assert diverged is not None
    assert diverged["severity"] == "info"
    # La readiness no porta economia interna: ni valors ni noms de camps.
    assert "unit_cost_ex_vat" not in json.dumps(readiness)

    # ------------------------------------------------------------------ #
    # 16. Sincronització explícita, camp a camp                           #
    # ------------------------------------------------------------------ #
    price_before = line["offered_unit_price_ex_vat"]
    lines_url = f"{OFFER.format(company=company, workspace=workspace)}/lines"
    sync_url = f"{lines_url}/{wh62_line_id}/sync-from-option"

    one_field = await client_a.post(
        sync_url, json={"expected_revision": offer["revision"], "fields": ["supplier_name"]}
    )
    assert one_field.status_code == 200, one_field.text
    offer = one_field.json()
    line = _line_of(offer, wh62_line_id)
    assert line["supplier_name"] == scenario["option_supplier_after"]
    assert line["product_name"] == scenario["option_product_before"], "només aquell camp"
    assert set(line["diverged_fields"]) == {"product_name", "unit_cost_ex_vat"}
    assert "economic_line_option_diverged" in _codes(
        (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    )

    rest = await client_a.post(
        sync_url,
        json={
            "expected_revision": offer["revision"],
            "fields": ["product_name", "unit_cost_ex_vat"],
        },
    )
    assert rest.status_code == 200, rest.text
    offer = rest.json()
    line = _line_of(offer, wh62_line_id)
    assert line["diverged_fields"] == []
    assert line["offered_unit_price_ex_vat"] == price_before, (
        "sincronitzar no toca mai el preu ofert"
    )
    # La precisió no és una divergència: l'opció desa sis decimals i la línia
    # quatre, i el mateix número a dues escales continua sent el mateix número.
    assert Decimal(line["internal_economics"]["unit_cost_ex_vat"]) == Decimal(
        chosen_spec["documented_unit_cost_ex_vat"]
    ).quantize(Decimal("0.0001"))
    assert "economic_line_option_diverged" not in _codes(
        (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    )

    # ------------------------------------------------------------------ #
    # 17. Objectiu de marge i `apply-target`                              #
    # ------------------------------------------------------------------ #
    wh62 = next(row for row in palamos["economic_lines"] if row["key"] == "wh62")
    manual_price = "3.0000"
    priced = await client_a.patch(
        f"{OFFER.format(company=company, workspace=workspace)}/lines/{wh62_line_id}",
        json={
            "expected_revision": offer["revision"],
            "offered_unit_price_ex_vat": manual_price,
            "tender_max_unit_price_ex_vat": wh62["tender_max_unit_price_ex_vat"],
            "vat_rate": wh62["vat_rate"],
        },
    )
    assert priced.status_code == 200, priced.text
    offer = priced.json()

    targeted = await client_a.patch(
        f"{OFFER.format(company=company, workspace=workspace)}/lines/{wh62_line_id}",
        json={
            "expected_revision": offer["revision"],
            "target_markup_pct": wh62["target_markup_pct"],
        },
    )
    assert targeted.status_code == 200, targeted.text
    offer = targeted.json()
    line = _line_of(offer, wh62_line_id)
    internal = line["internal_economics"]
    assert Decimal(internal["proposed_unit_price_ex_vat"]) == Decimal(
        wh62["expected_offered_unit_price_ex_vat"]
    )
    assert Decimal(line["offered_unit_price_ex_vat"]) == Decimal(manual_price), (
        "posar un objectiu no mou el preu ofert"
    )
    assert internal["price_matches_target"] is False

    applied = await client_a.post(
        f"{OFFER.format(company=company, workspace=workspace)}/lines/{wh62_line_id}/apply-target",
        json={"expected_revision": offer["revision"]},
    )
    assert applied.status_code == 200, applied.text
    offer = applied.json()
    line = _line_of(offer, wh62_line_id)
    assert Decimal(line["offered_unit_price_ex_vat"]) == Decimal(
        wh62["expected_offered_unit_price_ex_vat"]
    )
    assert line["internal_economics"]["price_matches_target"] is True

    # ------------------------------------------------------------------ #
    # 18. Totes les línies, i el total contra l'oracle canònic            #
    # ------------------------------------------------------------------ #
    for spec in palamos["economic_lines"]:
        if spec["key"] == "wh62":
            continue
        payload = {
            "expected_revision": offer["revision"],
            "concept": spec["concept"],
            "quantity": spec["quantity"],
            "pricing_basis": spec["pricing_basis"],
            "unit_cost_ex_vat": spec["unit_cost_ex_vat"],
            "target_markup_pct": spec["target_markup_pct"],
            "vat_rate": spec["vat_rate"],
        }
        if "tender_max_unit_price_ex_vat" in spec:
            payload["tender_max_unit_price_ex_vat"] = spec["tender_max_unit_price_ex_vat"]
        written = await client_a.post(
            f"{OFFER.format(company=company, workspace=workspace)}/sections/{section_id}/lines",
            json=payload,
        )
        assert written.status_code in (200, 201), (spec["key"], written.text)
        offer = written.json()

        created_line = next(
            row for row in _live_lines(offer) if row["concept"] == spec["concept"]
        )
        applied = await client_a.post(
            f"{OFFER.format(company=company, workspace=workspace)}"
            f"/lines/{created_line['id']}/apply-target",
            json={"expected_revision": offer["revision"]},
        )
        assert applied.status_code == 200, (spec["key"], applied.text)
        offer = applied.json()

    # Cada línia, una per una, contra el preu i el total congelats al fixture.
    by_concept = {row["concept"]: row for row in _live_lines(offer)}
    assert len(by_concept) == len(palamos["economic_lines"])
    for spec in palamos["economic_lines"]:
        concept = (
            "Auriculars sense fil per a operadora" if spec["key"] == "wh62" else spec["concept"]
        )
        row = by_concept[concept]
        assert Decimal(row["offered_unit_price_ex_vat"]) == Decimal(
            spec["expected_offered_unit_price_ex_vat"]
        ), spec["key"]
        assert row["effective_duration_months"] == months, spec["key"]
        assert Decimal(row["totals"]["total_revenue"]) == Decimal(
            spec["expected_line_total_ex_vat"]
        ), spec["key"]
        assert row["exceeds_unit_max"] in (False, None), spec["key"]

    engine_total = Decimal(offer["totals"]["total_revenue"])
    canonical = Decimal(palamos["oracles"]["canonical_total_ex_vat"])
    documented = Decimal(palamos["oracles"]["documented_total_ex_vat"])

    assert engine_total == canonical, (
        "el motor ha de reproduir l'oracle CANÒNIC, no el documental: "
        f"motor={engine_total} canònic={canonical} documental={documented}"
    )
    assert engine_total != documented
    assert engine_total - documented == Decimal(
        palamos["oracles"]["delta_canonical_minus_documented"]
    )
    assert Decimal(offer["internal_economics"]["total_cost"]) == Decimal(
        palamos["oracles"]["canonical_total_cost_ex_vat"]
    )

    # ------------------------------------------------------------------ #
    # 18 bis. Punts de control de la readiness: encendre i apagar         #
    # ------------------------------------------------------------------ #
    #
    # Un avís que s'encén i no s'apaga mai no serveix per res: el que fa que la
    # readiness es pugui fer servir és que digui quan una cosa ja està feta.

    # a) L'abast tècnic, i què el fa caducar.
    confirmed = await client_a.post(REVIEW_SCOPE.format(company=company, workspace=workspace))
    assert confirmed.status_code == 200, confirmed.text
    assert "technical_scope_unreviewed" not in _codes(confirmed.json())
    assert "technical_scope_stale" not in _codes(confirmed.json())

    extra = await client_a.post(
        REQUIREMENTS.format(company=company, tender=tender),
        json={
            "title": "Suport en català",
            "category": "contract_condition",
            "obligation": "scored",
        },
    )
    assert extra.status_code == 201, extra.text
    readiness = (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    assert "technical_scope_stale" in _codes(readiness)

    confirmed = await client_a.post(REVIEW_SCOPE.format(company=company, workspace=workspace))
    assert confirmed.status_code == 200, confirmed.text
    assert "technical_scope_stale" not in _codes(confirmed.json())

    # b) Una cotització que encara no ha arribat, sobre una opció TRIADA.
    option_now = (await client_a.get(one)).json()
    pending = await client_a.patch(
        one,
        json={"expected_revision": option_now["revision"], "quote_status": "pending"},
    )
    assert pending.status_code == 200, pending.text
    quote = _finding(
        (await client_a.get(READINESS.format(company=company, workspace=workspace))).json(),
        "solution_option_quote_pending",
    )
    assert quote is not None and quote["severity"] == "warning"

    arrived = await client_a.patch(
        one,
        json={"expected_revision": pending.json()["revision"], "quote_status": "received"},
    )
    assert arrived.status_code == 200, arrived.text
    assert "solution_option_quote_pending" not in _codes(
        (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    )

    # c) Una magnitud sense decidir de la qual depèn una línia VIVA.
    #    La cadena sencera: línia viva → opció → dependència → magnitud.
    undecided = await client_a.post(
        DIMENSIONS.format(company=company, workspace=workspace),
        json={"key": "seus_amb_gpon", "label": "Seus amb accés GPON", "unit": "seus"},
    )
    assert undecided.status_code == 201, undecided.text
    undecided_id = undecided.json()["id"]
    assert undecided.json()["status"] == "undecided"
    # Una magnitud només `undecided` no genera res per si sola.
    assert "dimension_undecided_in_use" not in _codes(
        (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    )

    attached = await client_a.put(
        f"{one}/dimensions/{undecided_id}",
        json={"expected_revision": arrived.json()["revision"]},
    )
    assert attached.status_code in (200, 201), attached.text
    in_use = _finding(
        (await client_a.get(READINESS.format(company=company, workspace=workspace))).json(),
        "dimension_undecided_in_use",
    )
    assert in_use is not None and in_use["severity"] == "blocker"

    settled = await client_a.post(
        f"{DIMENSIONS.format(company=company, workspace=workspace)}/{undecided_id}/decide",
        json={
            "expected_revision": undecided.json()["revision"],
            "working_value_text": "26 seus",
            "working_value_num": "26",
            "reason": "Repartides 13 + 13 entre dos operadors.",
        },
    )
    assert settled.status_code == 200, settled.text
    assert "dimension_undecided_in_use" not in _codes(
        (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    )

    # L'oferta no s'ha mogut per res de tot això.
    offer = (await client_a.get(OFFER.format(company=company, workspace=workspace))).json()
    assert Decimal(offer["totals"]["total_revenue"]) == canonical

    # ------------------------------------------------------------------ #
    # 19. Dubtes: obrir, marcar, resoldre                                 #
    # ------------------------------------------------------------------ #
    readiness = (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    assert "issue_open" not in _codes(readiness), "el sistema no obre cap dubte sol"
    assert "issue_blocking_open" not in _codes(readiness)

    issue = await client_a.post(
        ISSUES.format(company=company, workspace=workspace),
        json={
            "issue_type": "contradiction",
            "title": scenario["issue_title"],
            "dimension_id": dimension_id,
        },
    )
    assert issue.status_code == 201, issue.text
    issue_id = issue.json()["id"]
    assert issue.json()["blocks_final_review"] is False

    second_ref = await client_a.post(
        FILE_REF.format(company=company, tender=tender),
        json={
            "tender_document_file_id": v2.json()["file"]["file_id"],
            "location_label": "Full Càlculs v2, fila 42",
        },
    )
    assert second_ref.status_code == 201, second_ref.text
    for ref_id in (source_ref_id, second_ref.json()["id"]):
        attached = await client_a.post(
            f"{ISSUES.format(company=company, workspace=workspace)}/{issue_id}/source-refs",
            json={"source_ref_id": ref_id},
        )
        assert attached.status_code in (200, 201), attached.text
    assert len(attached.json()["source_refs"]) == 2

    readiness = (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    open_issues = _finding(readiness, "issue_open")
    assert open_issues is not None and open_issues["severity"] == "info"
    assert open_issues["context"]["count"] == 1
    assert "issue_blocking_open" not in _codes(readiness)

    blocked = await client_a.post(
        f"{ISSUES.format(company=company, workspace=workspace)}/{issue_id}/final-review-block",
        json={"blocks_final_review": True},
    )
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["blocks_final_review"] is True
    assert blocked.json()["status"] == "open", "marcar no canvia l'estat"

    readiness = (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    blocker = _finding(readiness, "issue_blocking_open")
    assert blocker is not None and blocker["severity"] == "blocker"
    assert readiness["ready_for_final_review"] is False
    # Un dubte marcat no es compta dues vegades.
    assert "issue_open" not in _codes(readiness)

    dimension_before = (
        await client_a.get(
            f"{DIMENSIONS.format(company=company, workspace=workspace)}"
        )
    ).json()
    resolved_issue = await client_a.post(
        f"{ISSUES.format(company=company, workspace=workspace)}/{issue_id}/resolve",
        json={"resolution": scenario["issue_resolution"]},
    )
    assert resolved_issue.status_code == 200, resolved_issue.text
    assert resolved_issue.json()["status"] == "resolved"
    assert resolved_issue.json()["resolved_by"] is not None
    assert resolved_issue.json()["resolved_at"] is not None

    readiness = (await client_a.get(READINESS.format(company=company, workspace=workspace))).json()
    assert "issue_blocking_open" not in _codes(readiness)

    # Resoldre un dubte no decideix cap magnitud: no hi ha cap cascada.
    dimension_after = (
        await client_a.get(
            f"{DIMENSIONS.format(company=company, workspace=workspace)}"
        )
    ).json()
    assert dimension_after == dimension_before

    # ------------------------------------------------------------------ #
    # 20. Descartar no és resoldre                                        #
    # ------------------------------------------------------------------ #
    from app.models.audit import AuditLog

    minor = await client_a.post(
        ISSUES.format(company=company, workspace=workspace),
        json={"issue_type": "internal_question", "title": scenario["second_issue_title"]},
    )
    assert minor.status_code == 201, minor.text
    minor_id = minor.json()["id"]

    dismissed = await client_a.post(
        f"{ISSUES.format(company=company, workspace=workspace)}/{minor_id}/dismiss", json={}
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "dismissed"
    assert dismissed.json()["resolved_by"] is None
    assert dismissed.json()["resolved_at"] is None
    assert dismissed.json()["resolution"] is None

    db.expire_all()
    logged = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_id == uuid.UUID(minor_id))
        )
    ).scalar_one()
    assert logged >= 1, "descartar deixa rastre a l'auditoria"

    # ------------------------------------------------------------------ #
    # 21. Manifest: què el mou i què no                                   #
    # ------------------------------------------------------------------ #
    from app.services import workspace_dossier
    from app.workspace.manifest import MANIFEST_VERSION

    assert MANIFEST_VERSION == "submission-manifest-v2"

    db.expire_all()
    workspace_row = await workspace_service.get_workspace(db, company, uuid.UUID(workspace))
    _rows, payload, digest_before = await workspace_dossier.current_manifest(
        db, workspace_row, revision_number=1
    )
    assert payload["manifest_version"] == MANIFEST_VERSION
    # Els dubtes no entren al manifest v2. No és un oblit: el manifest diu què
    # s'envia, i una pregunta oberta no s'envia.
    assert "issue" not in json.dumps(payload)

    option_only = await client_a.patch(
        one,
        json={
            "expected_revision": (await client_a.get(one)).json()["revision"],
            "description": "Nota interna sobre l'opció, que no va enlloc.",
        },
    )
    assert option_only.status_code == 200, option_only.text

    db.expire_all()
    workspace_row = await workspace_service.get_workspace(db, company, uuid.UUID(workspace))
    _rows, _payload, digest_after_option = await workspace_dossier.current_manifest(
        db, workspace_row, revision_number=1
    )
    assert digest_after_option == digest_before, "editar una opció no toca el manifest"

    offer = (await client_a.get(OFFER.format(company=company, workspace=workspace))).json()
    # Una nota interna NO entra al manifest: el manifest diu què s'envia.
    annotated = await client_a.patch(
        f"{OFFER.format(company=company, workspace=workspace)}/lines/{wh62_line_id}",
        json={"expected_revision": offer["revision"], "notes": "Revisat amb el proveïdor."},
    )
    assert annotated.status_code == 200, annotated.text

    db.expire_all()
    workspace_row = await workspace_service.get_workspace(db, company, uuid.UUID(workspace))
    _rows, _payload, digest_after_note = await workspace_dossier.current_manifest(
        db, workspace_row, revision_number=1
    )
    assert digest_after_note == digest_before, "una nota interna no toca el manifest"

    offer = annotated.json()
    moved_line = await client_a.patch(
        f"{OFFER.format(company=company, workspace=workspace)}/lines/{wh62_line_id}",
        json={
            "expected_revision": offer["revision"],
            "concept": "Auriculars sense fil per a operadora (revisat)",
        },
    )
    assert moved_line.status_code == 200, moved_line.text

    db.expire_all()
    workspace_row = await workspace_service.get_workspace(db, company, uuid.UUID(workspace))
    _rows, _payload, digest_after_line = await workspace_dossier.current_manifest(
        db, workspace_row, revision_number=1
    )
    assert digest_after_line != digest_before, "una línia econòmica sí que mou el manifest"

    # ------------------------------------------------------------------ #
    # 22. El recorregut no ha tocat res de B                              #
    # ------------------------------------------------------------------ #
    from app.models.workspace import BidWorkspace

    db.expire_all()
    theirs = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(BidWorkspace)
            .where(BidWorkspace.company_id == other_company)
        )
    ).scalar_one()
    assert theirs == 0


# --------------------------------------------------------------------------- #
# Auxiliars                                                                     #
# --------------------------------------------------------------------------- #


async def test_the_engine_reads_the_same_total_twice(client_a, tenant_a, palamos):
    """Reproductibilitat: els mateixos inputs, el mateix total. Dos cops.

    No comprova cap workflow —això ho fa el recorregut— sinó que el motor no
    depèn de res que canviï entre dues execucions.
    """
    from app.offer import pricing

    totals = []
    for _ in range(2):
        parts = []
        for spec in palamos["economic_lines"]:
            parts.append(
                pricing.line_totals(
                    pricing.LineInput(
                        pricing_basis=__import__(
                            "app.models.enums", fromlist=["PricingBasis"]
                        ).PricingBasis(spec["pricing_basis"]),
                        quantity=Decimal(spec["quantity"]),
                        unit_cost_ex_vat=Decimal(spec["unit_cost_ex_vat"]),
                        offered_unit_price_ex_vat=Decimal(
                            spec["expected_offered_unit_price_ex_vat"]
                        ),
                        effective_duration_months=spec["duration_months"],
                        vat_rate=Decimal(spec["vat_rate"]),
                    )
                )
            )
        totals.append(pricing.aggregate(parts).total_revenue)

    assert totals[0] == totals[1]
    assert totals[0] == Decimal(palamos["oracles"]["canonical_total_ex_vat"])


async def test_capex_and_opex_are_the_same_money_over_48_months(palamos):
    """§30: la mateixa entrada dona el mateix total a 48 mesos.

    La partida d'implantació és una inversió que el full amortitza dins la quota.
    Vista com a `ONE_OFF` de l'import sencer o com a `MONTHLY` de la quota, el
    total dels 48 mesos ha de ser exactament el mateix. Si no ho fos, transcriure
    una cosa o l'altra canviaria l'oferta.
    """
    from app.models.enums import PricingBasis
    from app.offer import pricing

    spec = next(
        row for row in palamos["economic_lines"] if row["key"] == "implantacio"
    )
    markup = Decimal(spec["target_markup_pct"])

    monthly = pricing.line_totals(
        pricing.LineInput(
            pricing_basis=PricingBasis.MONTHLY,
            quantity=Decimal(spec["quantity"]),
            offered_unit_price_ex_vat=Decimal(spec["expected_offered_unit_price_ex_vat"]),
            effective_duration_months=spec["duration_months"],
        )
    )
    capex_price = pricing.unit_amount(
        Decimal(spec["capex_one_off_ex_vat"]) * (1 + markup / 100)
    )
    one_off = pricing.line_totals(
        pricing.LineInput(
            pricing_basis=PricingBasis.ONE_OFF,
            quantity=Decimal(spec["quantity"]),
            offered_unit_price_ex_vat=capex_price,
            effective_duration_months=spec["duration_months"],
        )
    )

    assert monthly.total_revenue == one_off.total_revenue
    assert monthly.total_revenue == Decimal(spec["expected_line_total_ex_vat"])
    # I la lectura mensual d'una inversió no entra en cap suma: és una lectura.
    assert one_off.monthly_equivalent_revenue is not None


# --------------------------------------------------------------------------- #
# Zero model de pagament                                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def no_paid_model(monkeypatch):
    """Cap crida a cap proveïdor de pagament, i no per confiança.

    `get_provider` importa el SDK **dins** de la funció, de manera que la classe
    es resol al mòdul en el moment de construir-la. Substituir-la aquí tanca
    totes les vies alhora, incloses les que van passar `get_provider` com a
    valor per defecte d'un argument.

    Si alguna cosa del recorregut intentés analitzar un plec amb un model, el
    test cauria amb aquest missatge en comptes de gastar diners en silenci.
    """

    def refuse(*_args, **_kwargs):
        raise AssertionError(
            "l'acceptació d'Sprint 3 no pot cridar cap proveïdor de pagament"
        )

    monkeypatch.setattr("app.ai.providers.anthropic.AnthropicProvider", refuse)


async def test_the_acceptance_asks_no_model_anything(client_a, db, tenant_a, palamos):
    """Els requisits del recorregut els escriu una persona, i es nota.

    Zero `RequirementExtractionRun`: cap anàlisi automàtica, cap crida, cap
    credencial necessària. Un plec pujat no analitza res tot sol.
    """
    from app.models.requirements import RequirementExtractionRun

    _, company = tenant_a
    created = await client_a.post(
        TENDERS.format(company=company.id),
        json={"client_token": str(uuid.uuid4()), "title": palamos["tender"]["title"]},
    )
    tender = created.json()["tender"]["id"]
    uploaded = await client_a.post(
        UPLOAD.format(company=company.id, tender=tender),
        files={"file": ("PCAP.pdf", build_pdf(["Clausula primera. " * 30]), PDF)},
    )
    assert uploaded.status_code == 201, uploaded.text
    written = await client_a.post(
        REQUIREMENTS.format(company=company.id, tender=tender),
        json={"title": "Requisit escrit a mà", "category": "technical_requirement"},
    )
    assert written.status_code == 201, written.text

    db.expire_all()
    runs = (
        await db.execute(sa.select(sa.func.count()).select_from(RequirementExtractionRun))
    ).scalar_one()
    assert runs == 0


# --------------------------------------------------------------------------- #
# La deduplicació és física i es queda a dins                                   #
# --------------------------------------------------------------------------- #


async def test_the_same_bytes_in_two_companies_leave_no_trace_in_either_answer(
    client_a, client_b, db, tenant_a, tenant_b
):
    """El contracte extern no diu mai si uns bytes ja existien.

    `TenderDocumentContent` es dedupa per SHA-256 a **tot el sistema**, i això
    es queda. El que no pot passar és que la resposta HTTP ho digui: "aquests
    bytes ja hi eren" és una resposta sobre tots els tenants alhora, i qui
    sospiti que un competidor ha pujat un fitxer concret només hauria de
    pujar-lo ell per confirmar-ho.

    A dins, doncs, un sol contingut; a fora, dues pujades que no saben l'una de
    l'altra.
    """
    from app.models.tender_documents import (
        TenderDocumentContent,
        TenderDocumentExtraction,
        TenderDocumentFile,
    )

    # Els identificadors com a valors: el test expira la sessio pel mig.
    company_a, company_b = tenant_a[1].id, tenant_b[1].id
    secret = build_pdf(["Pressupost intern que no s'ha de poder endevinar. " * 6])

    a_tender = (
        await client_a.post(
            TENDERS.format(company=company_a),
            json={"client_token": str(uuid.uuid4()), "title": "Privada d'A"},
        )
    ).json()["tender"]["id"]
    mine = await client_a.post(
        UPLOAD.format(company=company_a, tender=a_tender),
        files={"file": ("intern.pdf", secret, PDF)},
    )
    assert mine.status_code == 201, mine.text

    b_tender = (
        await client_b.post(
            TENDERS.format(company=company_b),
            json={"client_token": str(uuid.uuid4()), "title": "Privada de B"},
        )
    ).json()["tender"]["id"]
    theirs = await client_b.post(
        UPLOAD.format(company=company_b, tender=b_tender),
        files={"file": ("provatura.pdf", secret, PDF)},
    )
    assert theirs.status_code == 201, theirs.text

    # a) A dins: un sol contingut i una sola extracció per a dos fitxers.
    db.expire_all()
    sha = mine.json()["file"]["sha256"]
    assert theirs.json()["file"]["sha256"] == sha
    content_id = (
        await db.execute(
            sa.select(TenderDocumentContent.id).where(TenderDocumentContent.sha256 == sha)
        )
    ).scalar_one()
    files = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocumentFile)
            .where(TenderDocumentFile.content_id == content_id)
        )
    ).scalar_one()
    extractions = (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderDocumentExtraction)
            .where(TenderDocumentExtraction.content_id == content_id)
        )
    ).scalar_one()
    assert (files, extractions) == (2, 1)

    # b) A fora: cap senyal, ni amb aquest nom ni amb cap altre. La llista no
    #    és cosmètica: qualsevol booleà equivalent tornaria a obrir el forat.
    for body in (mine.json(), theirs.json()):
        for name in (
            "content_reused",
            "content_exists",
            "globally_reused",
            "deduplicated",
            "already_known",
            "reused",
        ):
            assert name not in body, name
        # I res que soni a possessió global en cap nivell de la resposta.
        assert "reused" not in json.dumps(body)

    # c) I B continua sense veure res d'A.
    body = json.dumps(theirs.json())
    assert a_tender not in body
    assert "intern.pdf" not in body
    assert mine.json()["id"] not in body
    assert mine.json()["file"]["file_id"] not in body
    assert (
        await client_b.get(UPLOAD.format(company=company_b, tender=a_tender))
    ).status_code == 404


async def test_a_new_version_with_the_same_bytes_says_so_with_its_status_code(
    client_a, tenant_a
):
    """Dins de la mateixa empresa tampoc cal explicar l'storage.

    Que tornar a pujar el mateix fitxer no hagi fet res ja ho diuen el `200` i
    el número de versió, que no s'ha mogut. Un camp que ho repetís hauria de
    decidir si parla d'aquest document o de tot el sistema, i és precisament
    aquesta ambigüitat la que va obrir el forat.
    """
    _, company = tenant_a
    tender = (
        await client_a.post(
            TENDERS.format(company=company.id),
            json={"client_token": str(uuid.uuid4()), "title": "Amb versions"},
        )
    ).json()["tender"]["id"]
    payload = build_pdf(["Clausula primera. " * 30])

    first = await client_a.post(
        UPLOAD.format(company=company.id, tender=tender),
        files={"file": ("PCAP.pdf", payload, PDF)},
    )
    assert first.status_code == 201, first.text

    same = await client_a.post(
        VERSIONS.format(company=company.id, tender=tender, document=first.json()["id"]),
        files={"file": ("PCAP.pdf", payload, PDF)},
    )
    assert same.status_code == 200, same.text
    assert same.json()["file"]["version_number"] == 1
    assert "content_reused" not in same.json()

    other = await client_a.post(
        VERSIONS.format(company=company.id, tender=tender, document=first.json()["id"]),
        files={"file": ("PCAP.pdf", build_pdf(["Clausula primera REVISADA. " * 30]), PDF)},
    )
    assert other.status_code == 201, other.text
    assert other.json()["file"]["version_number"] == 2
    assert "content_reused" not in other.json()
