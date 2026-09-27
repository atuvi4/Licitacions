"""Importador guardat de la revisio de Palamos: guardes i pla en DRY-RUN. Zero crides.

Les dades son SINTETIQUES (la licitacio amb forma de Palamos de
`test_review_actions`); l'annex de 44 files es genera aqui amb els estats reals
(38/5/1) i les contraparts que el pla ha de resoldre.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core.security import verify_password
from app.models.audit import AuditLog
from app.models.identity import Company, CompanyMembership, User
from app.models.provenance import TenderSourceRef
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.review import ReviewItem, ReviewItemRelation, ReviewItemRevision, ReviewItemSource
from tests import test_review_actions as actions_tests
from tests.test_requirement_analysis import PAGE_27

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "import_palamos_review.py"
_spec = importlib.util.spec_from_file_location("import_palamos_review", _SCRIPT)
importer = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("import_palamos_review", importer)
_spec.loader.exec_module(importer)

analyzable = actions_tests.analyzable
_clean_provider = actions_tests._clean_provider
palamos_like = actions_tests.palamos_like
ORDINARY, INSURANCE, LATENCY = (
    actions_tests.ORDINARY, actions_tests.INSURANCE, actions_tests.LATENCY)
QUOTE = "El volumen anual de negocios del licitador debera ser al menos de 450.000 euros"
PARTIALS = ("M06", "M13", "M18", "M39", "M40")


def _annex_rows(quote=QUOTE, page="27"):
    rows = []
    for n in range(1, 45):
        manual_id = f"M{n:02d}"
        status, related = "MANUAL_GAP", ""
        if manual_id in PARTIALS:
            status, related = "MANUAL_PARTIAL", INSURANCE
        elif manual_id == "M09":
            status, related = "MANUAL_ALREADY_REPRESENTED", ORDINARY
        elif manual_id == "M21":
            related = LATENCY
        numeric = importer.MANUAL_VALUES.get(manual_id, ("", []))[0]
        rows.append({
            "manual_id": manual_id, "page": page, "section": f"8.{n}", "title": f"T{n}",
            "requirement": "r", "numeric_value_unit": numeric, "status": status,
            "related_automated_requirement": related, "evidence_quote": quote,
            "review_note": "", "mandatory": "yes", "material": "no",
        })
    return rows


def _write(path: Path, rows) -> Path:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


async def _set_migration(session, version):
    await session.execute(sa.text(
        "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)"))
    await session.execute(sa.text("DELETE FROM alembic_version"))
    await session.execute(sa.text("INSERT INTO alembic_version VALUES (:v)"), {"v": version})
    await session.commit()


async def _run_id(session, tender_id):
    return await session.scalar(sa.select(Requirement.run_id).where(
        Requirement.tender_id == tender_id).limit(1))


# --------------------------------------------------------------------------- #
# Guardes                                                                       #
# --------------------------------------------------------------------------- #

GOOD = "postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_review_palamos"


@pytest.mark.parametrize(("url", "reason"), [
    (None, "DATABASE_URL"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions", "protegida"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5432/licitacions_review_palamos", "5432"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_review_s3", "protegida"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_v3_probe_palamos", "protegida"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5433/una_altra", "Nomes la base"),
    ("postgresql+asyncpg://postgres@127.0.0.1:5434/licitacions_review_palamos", "Nomes el port"),
])
def test_nomes_la_base_dedicada_al_5433(url, reason):
    with pytest.raises(importer.ImportGuardError, match=reason):
        importer.validate_target_url(url)


def test_la_base_dedicada_passa():
    assert importer.validate_target_url(GOOD).database == "licitacions_review_palamos"


def test_les_constants_son_les_de_palamos():
    assert str(importer.TENDER_ID) == "f37d7031-6d32-404d-8785-b2da7cf6a78e"
    assert str(importer.RUN_ID) == "2941fbec-cefc-4011-8026-79110d9d1938"
    assert importer.EXPECTED_MIGRATION == "0040_review_layer"


async def test_estat_correcte_passa(session, palamos_like):
    tid = palamos_like["tender_id"]
    await _set_migration(session, "0040_review_layer")

    await importer.check_state(session, tender_id=tid, run_id=await _run_id(session, tid))


@pytest.mark.parametrize(("case", "reason"), [
    ("migration", "Esquema"), ("tender", "licitacio"), ("run", "run final no hi es"),
])
async def test_estat_incorrecte_es_refusa(session, palamos_like, case, reason):
    tid = palamos_like["tender_id"]
    run_id = await _run_id(session, tid)
    await _set_migration(session, "0039_award_adjudication_basis" if case == "migration"
                         else "0040_review_layer")
    kwargs = {"tender_id": tid, "run_id": run_id}
    if case == "tender":
        kwargs["tender_id"] = uuid.uuid4()
    if case == "run":
        kwargs["run_id"] = uuid.uuid4()

    with pytest.raises(importer.ImportGuardError, match=reason):
        await importer.check_state(session, **kwargs)


# --------------------------------------------------------------------------- #
# Annex                                                                         #
# --------------------------------------------------------------------------- #


def test_l_annex_valid_passa(tmp_path):
    rows = importer.load_annex(_write(tmp_path / "a.csv", _annex_rows()))
    assert len(rows) == 44


@pytest.mark.parametrize("mutate", ["less", "status", "optional", "duplicate"])
def test_un_annex_que_no_quadra_s_atura(tmp_path, mutate):
    rows = _annex_rows()
    if mutate == "less":
        rows = rows[:-1]
    elif mutate == "status":
        rows[0]["status"] = "MANUAL_PARTIAL"
    elif mutate == "optional":
        rows[3]["mandatory"] = "no"
    else:
        rows[1]["manual_id"] = rows[0]["manual_id"]

    with pytest.raises(importer.ImportGuardError):
        importer.load_annex(_write(tmp_path / "a.csv", rows))


@pytest.mark.parametrize(("value", "pages"), [("22", [22]), ("22-23", [22, 23]),
                                              ("24-25", [24, 25])])
def test_pagines_de_l_annex(value, pages):
    assert importer.pages_of(value) == pages


@pytest.mark.parametrize("value", ["", "p22", "23-22", "22-"])
def test_una_pagina_no_valida_s_atura(value):
    with pytest.raises(importer.ImportGuardError):
        importer.pages_of(value)


# --------------------------------------------------------------------------- #
# Ancoratge                                                                     #
# --------------------------------------------------------------------------- #

PAGE = "7. L’Ajuntament podrà sol·licitar\nl’activació del servei de dades.\nFi."


def test_cita_exacta():
    anchor = importer.anchor_quote(PAGE, "sol·licitar")
    assert (anchor.exact, PAGE[anchor.start:anchor.end]) == (True, "sol·licitar")


def test_cita_amb_salt_de_linia_i_apostrof_tipografic():
    anchor = importer.anchor_quote(PAGE, "sol·licitar l'activació del servei")

    assert anchor.resolved and not anchor.exact
    # Les posicions son de l'ORIGINAL: el tros es el text real de la pagina.
    assert PAGE[anchor.start:anchor.end] == "sol·licitar\nl’activació del servei"


@pytest.mark.parametrize(("quote", "diagnostic"), [
    ("una frase que no hi es", "no es a la pagina"),
    ("", "cita buida"),
])
def test_cita_no_trobada_queda_sense_resoldre(quote, diagnostic):
    anchor = importer.anchor_quote(PAGE, quote)
    assert (anchor.resolved, anchor.start, anchor.end) == (False, None, None)
    assert anchor.diagnostic == diagnostic


def test_cita_ambigua_queda_sense_resoldre():
    anchor = importer.anchor_quote("abc. abc.", "abc")
    assert not anchor.resolved and anchor.diagnostic.startswith("ambigua")


# --------------------------------------------------------------------------- #
# Pla                                                                           #
# --------------------------------------------------------------------------- #


async def _plan(session, palamos_like, rows):
    tid = palamos_like["tender_id"]
    return await importer.build_plan(session, rows, tender_id=tid,
                                     run_id=await _run_id(session, tid),
                                     document_name="PCAP.pdf")


async def _review_counts(session):
    return [await session.scalar(sa.select(sa.func.count()).select_from(model))
            for model in (ReviewItem, ReviewItemSource, ReviewItemRevision, ReviewItemRelation,
                          Requirement)]


async def test_pla_38_5_1_amb_43_creacions_m09_sense_crear_i_m21_related_to(
    session, palamos_like, tmp_path
):
    rows = importer.load_annex(_write(tmp_path / "a.csv", _annex_rows()))
    before = await _review_counts(session)

    plan = await _plan(session, palamos_like, rows)
    summary = importer.summarise(plan, automated_requirements=3)

    assert await _review_counts(session) == before, "el dry-run no escriu res"
    assert summary["status"] == {"MANUAL_GAP": 38, "MANUAL_PARTIAL": 5,
                                 "MANUAL_ALREADY_REPRESENTED": 1}
    assert (summary["manual_creates"], summary["confirms"]) == (43, 1)
    assert summary["relations"] == {"partial_match_of": 5, "related_to": 1}
    assert summary["citations_resolved"] == 44 and summary["citations_exact"] == 44
    assert (summary["mappings_required"], summary["mappings_resolved"]) == (7, 7)
    assert summary["expected_canonical_requirements"] == 3 + 43
    by_id = {p.manual_id: p for p in plan}
    assert by_id["M09"].planned_action.startswith("no_create")
    assert by_id["M21"].relation_type == "related_to"
    assert by_id["M21"].related_requirement_title == LATENCY
    assert all(by_id[m].relation_type == "partial_match_of" for m in PARTIALS)
    assert importer.ready(summary)
    start = PAGE_27.index(QUOTE)
    assert (by_id["M01"].char_start, by_id["M01"].char_end) == (start, start + len(QUOTE))


async def test_una_cita_que_no_hi_es_bloqueja_la_importacio(session, palamos_like):
    rows = _annex_rows()
    rows[4]["evidence_quote"] = "Aquesta frase no surt enlloc"

    summary = importer.summarise(await _plan(session, palamos_like, rows), 3)

    assert summary["citations_unresolved"] == ["M05"]
    assert not importer.ready(summary)


async def test_una_pagina_que_no_existeix_no_s_endevina(session, palamos_like):
    rows = _annex_rows()
    rows[0]["page"] = "99"

    [first, *_] = await _plan(session, palamos_like, rows)

    assert (first.citation_resolved, first.page) == (False, None)
    assert "pagina no trobada" in first.diagnostic


async def test_una_contrapart_ambigua_falla_tancada(session, palamos_like):
    original = palamos_like["rows"][ORDINARY]
    session.add(Requirement(
        run_id=original.run_id, tender_id=original.tender_id, category=original.category,
        title=ORDINARY, dedup_key="duplicat-" + uuid.uuid4().hex[:8]))
    await session.commit()

    summary = importer.summarise(await _plan(session, palamos_like, _annex_rows()), 3)

    assert summary["mappings_unresolved"] == ["M09"]
    assert not importer.ready(summary)


async def test_una_contrapart_que_no_existeix_falla_tancada(session, palamos_like):
    rows = _annex_rows()
    rows[5]["related_automated_requirement"] = "Un titol que no es de cap requisit"

    plan = await _plan(session, palamos_like, rows)

    assert {p.manual_id: p.mapping_status for p in plan}["M06"] == "unresolved_none"


async def test_escriure_tambe_refusa_una_base_protegida_sense_connectar(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_v3_probe_palamos")
    args = importer.build_parser().parse_args(
        ["--annex", str(_write(tmp_path / "a.csv", _annex_rows())), "--out-dir", str(tmp_path),
         "--write"])

    assert await importer.run(args) == 2


def test_sense_la_bandera_no_s_escriu():
    args = importer.build_parser().parse_args(["--annex", "a.csv", "--out-dir", "o"])
    assert args.write is False


async def test_la_cli_refusa_una_base_protegida_sense_connectar(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://postgres@127.0.0.1:5433/licitacions_review_s3")
    args = importer.build_parser().parse_args(
        ["--annex", str(_write(tmp_path / "a.csv", _annex_rows())), "--out-dir", str(tmp_path)])

    assert await importer.run(args) == 2
    assert "protegida" in capsys.readouterr().out
    assert not (tmp_path / "palamos_review_import_plan.csv").exists()


# --------------------------------------------------------------------------- #
# Escriptura (dades sintetiques; la base de test es d'usar i llencar)          #
# --------------------------------------------------------------------------- #


async def _raw_extraction(session):
    snapshot = {}
    for model in (Requirement, RequirementEvidence, AwardCriterion, AwardCriterionEvidence,
                  RequirementExtractionRun):
        table = model.__table__
        snapshot[table.name] = [dict(r) for r in (await session.execute(
            sa.select(table).order_by(table.c.id))).mappings()]
    return snapshot


async def _import(session, palamos_like, rows=None, tenant=None):
    rows = rows or _annex_rows()
    tid = palamos_like["tender_id"]
    run_id = await _run_id(session, tid)
    plan = await importer.build_plan(session, rows, tender_id=tid, run_id=run_id,
                                     document_name="PCAP.pdf")
    tenant = tenant or await importer.ensure_qa_tenant(session)
    await session.commit()
    results = await importer.execute_import(
        session, plan=plan, rows=rows, tenant=tenant, tender_id=tid, run_id=run_id,
        batch_id=uuid.uuid4(), annex_name="annex.csv", annex_sha256="0" * 64)
    await session.commit()
    return tenant, plan, results


async def _counts(session):
    return {model.__tablename__: await session.scalar(
        sa.select(sa.func.count()).select_from(model))
        for model in (ReviewItem, ReviewItemSource, ReviewItemRevision, ReviewItemRelation,
                      TenderSourceRef)}


EMPTY_COUNTS = {name: 0 for name in (
    "review_items", "review_item_sources", "review_item_revisions",
    "review_item_relations", "tender_source_refs")}


async def test_la_importacio_completa_te_la_forma_esperada(session, palamos_like):
    tid = palamos_like["tender_id"]
    before = await _raw_extraction(session)
    rows_before = await session.scalar(sa.select(sa.func.count()).select_from(Requirement))
    m09_source = palamos_like["rows"][ORDINARY]
    m09_status = m09_source.review_status.value

    tenant, _plan_rows, results = await _import(session, palamos_like)

    counts = await _counts(session)
    assert counts == {"review_items": 3 + 43, "review_item_sources": 3 + 43 + 1,
                      "review_item_revisions": 3 + 43 + 1, "review_item_relations": 6,
                      "tender_source_refs": 44}
    checks = await importer.verify_import(session, tenant.company.id, tid, results, None)
    assert checks["canonical_requirements"] == 3 + 43
    assert checks["provenance"] == {"automated": 3, "manual": 43}
    assert checks["manual_all_unreviewed"] and checks["manual_all_manual"]
    assert checks["relations"] == {"partial_match_of": 5, "related_to": 1}
    assert checks["M15"]["values"] == [(64, "trucades")]
    assert checks["M06"]["relations"][0][:2] == ("partial_match_of", "Cobert parcialment per")
    assert checks["M21"]["relations"][0][:2] == ("related_to", "Relacionat amb")
    m09 = checks["M09"]
    assert m09["provenance"] == "automated"
    assert [label for _page, label in m09["evidence"]][-1] == "Citat manualment"
    assert len(m09["evidence"]) == 2
    item_m09 = await session.scalar(sa.select(ReviewItem).join(
        ReviewItemSource, ReviewItemSource.item_id == ReviewItem.id).where(
        ReviewItemSource.requirement_id == m09_source.id))
    actions = (await session.scalars(sa.select(ReviewItemRevision.action).where(
        ReviewItemRevision.item_id == item_m09.id).order_by(ReviewItemRevision.revision_no)
    )).all()
    assert [a.value for a in actions] == ["create", "link_source"]
    assert item_m09.verification_status.value == m09_status
    assert await session.scalar(sa.select(sa.func.count()).select_from(ReviewItem).where(
        ReviewItem.verification_status == "verified")) == 0
    assert await session.scalar(
        sa.select(sa.func.count()).select_from(Requirement)) == rows_before
    session.expire_all()
    assert await _raw_extraction(session) == before
    payloads = [r.payload_json for r in (await session.scalars(sa.select(ReviewItemRevision)
                .where(ReviewItemRevision.action == "create"))).all()]
    manual = [p for p in payloads if p.get("manual_id")]
    assert len(manual) == 43
    assert {p["package"] for p in manual} == {"palamos_final_review"}
    assert len({p["import_batch_id"] for p in payloads}) == 1


async def test_una_fallada_a_la_fila_n_ho_desfa_tot(session, palamos_like, monkeypatch):
    from app.services import review_actions

    original = review_actions.create_manual_requirement
    calls = {"n": 0}

    async def failing(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 10:
            raise RuntimeError("fallada simulada a la fila 10")
        return await original(*args, **kwargs)

    monkeypatch.setattr(review_actions, "create_manual_requirement", failing)
    tid = palamos_like["tender_id"]
    run_id = await _run_id(session, tid)
    rows = _annex_rows()
    plan = await importer.build_plan(session, rows, tender_id=tid, run_id=run_id,
                                     document_name="PCAP.pdf")
    tenant = await importer.ensure_qa_tenant(session)
    await session.commit()
    before = await _raw_extraction(session)

    with pytest.raises(RuntimeError, match="fila 10"):
        await importer.execute_import(
            session, plan=plan, rows=rows, tenant=tenant, tender_id=tid, run_id=run_id,
            batch_id=uuid.uuid4(), annex_name="annex.csv", annex_sha256="0" * 64)
    await session.rollback()

    assert await _counts(session) == EMPTY_COUNTS
    assert await session.scalar(sa.select(sa.func.count()).select_from(AuditLog).where(
        AuditLog.summary.contains("lot="))) == 0
    assert await _raw_extraction(session) == before


async def test_una_segona_importacio_no_escriu_res(session, palamos_like):
    tenant, plan, _results = await _import(session, palamos_like)
    counts = await _counts(session)
    tid = palamos_like["tender_id"]
    expected_ids = {p.manual_id for p in plan if p.planned_action.startswith("create")}

    assert await importer.import_status(session, tenant.company.id, tid,
                                        expected_manual_ids=expected_ids,
                                        expected_items=3 + 43) == "complete"
    with pytest.raises(importer.AlreadyImportedError):
        await importer.execute_import(
            session, plan=plan, rows=_annex_rows(), tenant=tenant, tender_id=tid,
            run_id=await _run_id(session, tid), batch_id=uuid.uuid4(),
            annex_name="annex.csv", annex_sha256="0" * 64)
    await session.rollback()
    assert await _counts(session) == counts


async def test_un_estat_de_revisio_desconegut_atura(session, palamos_like):
    from app.services import review_view

    tid = palamos_like["tender_id"]
    tenant = await importer.ensure_qa_tenant(session)
    await review_view.start_review(session, company_id=tenant.company.id, tender_id=tid,
                                   actor_id=tenant.actor.id)
    await session.commit()
    counts = await _counts(session)

    with pytest.raises(importer.ImportGuardError, match="no es la importacio"):
        await _import(session, palamos_like, tenant=tenant)
    await session.rollback()
    assert await _counts(session) == counts


async def test_nomes_l_empresa_qa_pot_rebre_la_importacio(session, palamos_like, tenant_a):
    user, company = tenant_a
    membership = await session.scalar(sa.select(CompanyMembership).where(
        CompanyMembership.company_id == company.id))
    fake = importer.QaTenant(company, user, membership, created=False)

    with pytest.raises(importer.ImportGuardError, match="empresa QA"):
        await _import(session, palamos_like, tenant=fake)
    await session.rollback()
    assert (await _counts(session))["review_items"] == 0


async def test_l_empresa_i_l_actor_qa_son_idempotents_i_sense_acces(session):
    first = await importer.ensure_qa_tenant(session)
    await session.commit()
    second = await importer.ensure_qa_tenant(session)

    assert (first.created, second.created) == (True, False)
    assert (first.company.id, first.actor.id) == (second.company.id, second.actor.id)
    assert first.membership.role.value == "technical"
    assert first.actor.is_active is False and first.actor.is_superuser is False
    assert not verify_password("qualsevol", first.actor.hashed_password)
    assert await session.scalar(sa.select(sa.func.count()).select_from(Company).where(
        Company.legal_name == importer.QA_COMPANY_NAME)) == 1


async def test_una_identitat_qa_en_conflicte_atura(session):
    session.add(User(email=importer.QA_ACTOR_EMAIL, hashed_password="x", is_active=True))
    await session.commit()

    with pytest.raises(importer.ImportGuardError, match="ambigua"):
        await importer.ensure_qa_tenant(session)


def test_els_valors_de_l_annex_es_mapegen_sense_interpretar():
    rows = {r["manual_id"]: r for r in _annex_rows()}
    assert importer.manual_values(rows["M15"])[0]["value"] == 64
    assert [v["value"] for v in importer.manual_values(rows["M43"])] == [200, 25]
    assert importer.manual_values(rows["M01"]) == []
    changed = dict(rows["M15"], numeric_value_unit="65 trucades")
    with pytest.raises(importer.ImportGuardError, match="ha canviat"):
        importer.manual_values(changed)
    unmapped = dict(rows["M02"], numeric_value_unit="10 GB")
    with pytest.raises(importer.ImportGuardError, match="sense mapatge"):
        importer.manual_values(unmapped)


async def test_els_invariants_d_escriptura(session, palamos_like):
    plan = await _plan(session, palamos_like, _annex_rows())
    summary = importer.summarise(plan, automated_requirements=214)
    importer.assert_write_invariants(plan, summary, expected_automated=214)

    for key, value in (("manual_creates", 42), ("relations", {"partial_match_of": 5}),
                       ("citations_resolved", 43), ("automated_requirements", 213)):
        broken = dict(summary, **{key: value})
        with pytest.raises(importer.ImportGuardError):
            importer.assert_write_invariants(plan, broken, expected_automated=214)
