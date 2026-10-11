"""Semantica del plec abans de l'analisi (0050).

El gold de l'Escala va ensenyar quatre coses que el model no sabia dir sense
inventar: una acreditacio «abans de formalitzar», una seu de la qual no se sap
si es critica, un ramal del qual nomes es coneix el codi (o una seu nomes amb
nom), i un requisit territorial del qual no tenim la llista perque falta un
annex. Aqui es comprova que ara es poden dir tal com son, i que les cites
arriben als criteris d'adjudicacio amb el mateix patro que blocs i seus.
"""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    AreaKind,
    RequirementTiming,
    TenderSiteRole,
    TerritoryCompleteness,
)
from app.models.provenance import TenderSourceRef
from app.models.requirements import AwardCriterion, Requirement
from app.models.tender_structure import AwardCriterionSourceRefLink, TenderSite
from app.services import tender_scope as scope
from app.services import tender_structure as structure
from tests import test_tender_scope_models as m
from tests import test_tender_structure_api as api
from tests.structure_support import PROV
from tests.test_tender_structure_api import _code, _count

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def seeded_vocabulary(db):
    from app.knowledge import vocabulary

    await vocabulary.seed_vocabulary(db)
    await db.flush()


def _sql_id(db, value: uuid.UUID) -> tuple[str, str]:
    """Com es passa un UUID per SQL cru: hex a SQLite, `uuid` a PostgreSQL."""
    if db.bind.dialect.name == "postgresql":
        return "CAST(:id AS uuid)", str(value)
    return ":id", value.hex


# --------------------------------------------------------------------------- #
# Seus: identitat parcial i critica en tri-estat                                #
# --------------------------------------------------------------------------- #


async def test_a_site_needs_a_code_or_a_name_and_critical_is_never_coerced(db):
    tender = await m._public_tender(db)
    remote = TenderSiteRole.REMOTE
    code_only = await structure.create_site(
        db, tender, {"code": "D02", "role": remote}, provenance=PROV
    )
    name_only = await structure.create_site(
        db, tender, {"name": "Can Maset", "role": remote}, provenance=PROV
    )
    assert (code_only.code, code_only.name, code_only.is_critical) == ("D02", None, None)
    assert (name_only.code, name_only.name, name_only.is_critical) == (None, "Can Maset", None)
    with pytest.raises(structure.SiteIdentityError):
        await structure.create_site(db, tender, {"role": remote}, provenance=PROV)
    # Dues seus sense codi no col·lisionen: el NULL no es cap codi.
    critical = await structure.create_site(
        db, tender, {"name": "Policia Local", "role": remote, "is_critical": True}, provenance=PROV
    )
    not_critical = await structure.create_site(
        db,
        tender,
        {"name": "Piscina Municipal", "role": remote, "is_critical": False},
        provenance=PROV,
    )
    assert (critical.is_critical, not_critical.is_critical) == (True, False)
    await db.flush()
    for site in (code_only, name_only, critical, not_critical):
        await db.refresh(site)
    assert [s.is_critical for s in (code_only, name_only, critical, not_critical)] == [
        None,
        None,
        True,
        False,
    ]
    # `None` es un valor que s'aplica («ja no ho sabem»), no una omissio.
    assert (
        await structure.update_site(db, tender, critical, {"is_critical": None}, provenance=PROV)
        is True
    )
    await db.refresh(critical)
    assert critical.is_critical is None
    # Treure l'unica identitat que te es refusa; canviar-la per l'altra, no.
    with pytest.raises(structure.SiteIdentityError):
        await structure.update_site(db, tender, code_only, {"code": None}, provenance=PROV)
    assert await structure.update_site(
        db, tender, code_only, {"name": "Seu del ramal", "code": None}, provenance=PROV
    )
    await db.refresh(code_only)
    assert (code_only.code, code_only.name) == (None, "Seu del ramal")
    # La base tambe ho imposa: cap fila sense codi ni nom.
    await m._rejected_by_db(
        db,
        sa.insert(TenderSite).values(
            id=uuid.uuid4(), tender_id=tender.id, role=TenderSiteRole.REMOTE.value, origin="gold"
        ),
    )
    assert await _count(db, TenderSite) == 4


async def test_sites_api_accepts_partial_identity_and_tristate_critical(client_a, tenant_a):
    _, company = tenant_a
    tender_id = await api._tender(client_a, company)
    url = api.SITES.format(company=company.id, tender=tender_id)

    code_only = await client_a.post(url, json={"code": "D02", "role": "remote"})
    assert code_only.status_code == 201, code_only.text
    assert (code_only.json()["name"], code_only.json()["is_critical"]) == (None, None)
    name_only = await client_a.post(
        url, json={"name": "Can Maset", "role": "remote", "is_critical": True}
    )
    assert (name_only.status_code, name_only.json()["code"]) == (201, None)
    assert name_only.json()["is_critical"] is True
    another_nameless_code = await client_a.post(
        url, json={"name": "Policia Local", "role": "remote"}
    )
    assert another_nameless_code.status_code == 201, "dos NULL no col·lisionen"
    assert (await client_a.post(url, json={"role": "remote"})).status_code == 422
    explicit_no = await client_a.post(
        url, json={"name": "Piscina Municipal", "role": "remote", "is_critical": False}
    )
    assert explicit_no.json()["is_critical"] is False

    # Un `null` explicit torna a «no se sap»; no es cap omissio.
    unknown_again = await client_a.patch(
        f"{url}/{name_only.json()['id']}", json={"is_critical": None}
    )
    assert (unknown_again.status_code, unknown_again.json()["is_critical"]) == (200, None)
    stripped = await client_a.patch(f"{url}/{code_only.json()['id']}", json={"code": None})
    assert (stripped.status_code, _code(stripped)) == (422, "tender_site_identity_required")

    structure_read = (
        await client_a.get(api.STRUCTURE.format(company=company.id, tender=tender_id))
    ).json()
    assert sorted(
        (s["code"] or "", s["name"] or "", s["is_critical"]) for s in structure_read["sites"]
    ) == [
        ("", "Can Maset", None),
        ("", "Piscina Municipal", False),
        ("", "Policia Local", None),
        ("D02", "", None),
    ]


# --------------------------------------------------------------------------- #
# Temporalitat: abans de formalitzar                                            #
# --------------------------------------------------------------------------- #


async def test_before_formalization_round_trips_and_legacy_unknown_is_untouched(db):
    assert [t.value for t in RequirementTiming] == [
        "at_submission",
        "before_formalization",
        "at_execution",
        "ambiguous",
        "unknown",
    ]
    tender = await m._public_tender(db)
    accreditation = await m._requirement(db, tender, title="RC: polissa i rebut")
    criterion = await m._criterion(db, tender)
    assert accreditation.timing == RequirementTiming.UNKNOWN == criterion.timing
    assert structure.set_timing(accreditation, RequirementTiming.BEFORE_FORMALIZATION) is True
    assert structure.set_timing(criterion, RequirementTiming.BEFORE_FORMALIZATION) is True
    await db.flush()
    await db.refresh(accreditation)
    await db.refresh(criterion)
    assert accreditation.timing == RequirementTiming.BEFORE_FORMALIZATION == criterion.timing
    # Dues fases, dos requisits atomics: cap llista de temporalitats.
    maintenance = await m._requirement(db, tender, title="RC: mantenir la polissa")
    structure.set_timing(maintenance, RequirementTiming.AT_EXECUTION)
    legacy = await m._requirement(db, tender, title="Sense dir quan")
    await db.flush()
    await db.refresh(legacy)
    assert legacy.timing == RequirementTiming.UNKNOWN
    # La base accepta el valor nou tal qual i continua rebutjant el que no es de l'enum.
    expr, param = _sql_id(db, maintenance.id)
    await db.execute(
        sa.text(
            f"UPDATE requirements SET timing = 'before_formalization' WHERE id = {expr}"
        ).bindparams(id=param)
    )
    expr, param = _sql_id(db, criterion.id)
    await m._rejected_by_db(
        db,
        sa.text(f"UPDATE award_criteria SET timing = 'xyz' WHERE id = {expr}").bindparams(id=param),
    )


async def test_before_formalization_through_the_structure_api(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await api._tender(client_a, company)
    requirement = await api._requirement(client_a, company, tender_id)
    base = api.T.format(company=company.id, tender=tender_id)
    moved = await client_a.patch(
        f"{base}/requirements/{requirement['id']}/structure",
        json={"timing": "before_formalization"},
    )
    assert (moved.status_code, moved.json()["timing"]) == (200, "before_formalization")
    run = await api._run(db, tender_id)
    criterion = await api._criterion(db, run, "Garantia", 1.0)
    moved = await client_a.patch(
        f"{base}/award-criteria/{criterion.id}/structure", json={"timing": "before_formalization"}
    )
    assert (moved.status_code, moved.json()["timing"]) == (200, "before_formalization")


# --------------------------------------------------------------------------- #
# Completesa territorial                                                        #
# --------------------------------------------------------------------------- #


async def test_territory_completeness_rules_at_the_service(db):
    tender = await m._public_tender(db)
    requirement = await m._requirement(db, tender)
    assert requirement.territory_completeness == TerritoryCompleteness.UNKNOWN, "per defecte"
    escala = {"kind": AreaKind.MUNICIPALITY, "label": "L'Escala", "qualifier": "trama urbana"}
    # unknown: 0..N.
    assert await scope.set_territories(db, tender, requirement, [escala], provenance=PROV) is True
    # not_applicable amb files: no, en cap ordre.
    with pytest.raises(scope.TerritoryCompletenessConflictError):
        await scope.set_territory_completeness(
            db, requirement, TerritoryCompleteness.NOT_APPLICABLE
        )
    # complete i partial: almenys una.
    assert await scope.set_territory_completeness(db, requirement, TerritoryCompleteness.COMPLETE)
    with pytest.raises(scope.TerritoryCompletenessConflictError):
        await scope.set_territories(db, tender, requirement, [], provenance=PROV)
    assert await scope.set_territory_completeness(db, requirement, TerritoryCompleteness.PARTIAL)
    with pytest.raises(scope.TerritoryCompletenessConflictError):
        await scope.set_territories(db, tender, requirement, [], provenance=PROV)
    # L'estat final es el que compta: buidar i declarar «no aplica» alhora es valid.
    touched = await scope.set_territory_semantics(
        db,
        tender,
        requirement,
        completeness=TerritoryCompleteness.NOT_APPLICABLE,
        territories=[],
        provenance=PROV,
    )
    assert touched == ["completesa=not_applicable", "territoris=0"]
    await db.refresh(requirement, attribute_names=["territories"])
    assert requirement.territories == []
    with pytest.raises(scope.TerritoryCompletenessConflictError):
        await scope.set_territories(db, tender, requirement, [escala], provenance=PROV)
    # Un conflicte no deixa res a mitges.
    with pytest.raises(scope.TerritoryCompletenessConflictError):
        await scope.set_territory_semantics(
            db,
            tender,
            requirement,
            completeness=TerritoryCompleteness.COMPLETE,
            territories=[],
            provenance=PROV,
        )
    await db.refresh(requirement)
    assert requirement.territory_completeness == TerritoryCompleteness.NOT_APPLICABLE
    # Conjunt, ordre i desduplicacio: igual que abans.
    other = await m._requirement(db, tender, title="C1")
    touched = await scope.set_territory_semantics(
        db,
        tender,
        other,
        completeness=TerritoryCompleteness.COMPLETE,
        territories=[escala, {**escala, "label": "  L'Escala "}],
        provenance=PROV,
    )
    assert touched == ["completesa=complete", "territoris=1"]
    assert await scope.set_territories(db, tender, other, [escala], provenance=PROV) is False
    assert (
        await scope.set_territory_semantics(
            db, tender, other, territories=[escala], provenance=PROV
        )
        == []
    )
    # Tornar a «no se sap» sempre es pot, amb o sense files.
    assert await scope.set_territory_completeness(db, other, TerritoryCompleteness.UNKNOWN)
    assert await scope.set_territory_completeness(db, requirement, TerritoryCompleteness.UNKNOWN)
    # La base rebutja valors fora de l'enum.
    expr, param = _sql_id(db, other.id)
    await m._rejected_by_db(
        db,
        sa.text(
            f"UPDATE requirements SET territory_completeness = 'xyz' WHERE id = {expr}"
        ).bindparams(id=param),
    )


async def test_semantics_api_carries_completeness_and_rejects_conflicts(client_a, tenant_a):
    _, company = tenant_a
    tender_id = await api._tender(client_a, company)
    requirement = await api._requirement(client_a, company, tender_id)
    url = api.T.format(company=company.id, tender=tender_id) + (
        f"/requirements/{requirement['id']}/semantics"
    )
    assert (await client_a.get(url)).json()["territory_completeness"] == "unknown"
    conflict = await client_a.patch(url, json={"territory_completeness": "complete"})
    assert (conflict.status_code, _code(conflict)) == (422, "territory_completeness_conflict")
    body = (
        await client_a.patch(
            url,
            json={
                "territory_completeness": "complete",
                "territories": [{"kind": "custom", "label": "Can Maset"}],
            },
        )
    ).json()
    assert (body["territory_completeness"], len(body["territories"])) == ("complete", 1)
    body = (
        await client_a.patch(
            url, json={"territory_completeness": "not_applicable", "territories": []}
        )
    ).json()
    assert (body["territory_completeness"], body["territories"]) == ("not_applicable", [])
    rows = await client_a.patch(url, json={"territories": [{"kind": "custom", "label": "x"}]})
    assert (rows.status_code, _code(rows)) == (422, "territory_completeness_conflict")
    unknown = (await client_a.patch(url, json={"territory_completeness": "unknown"})).json()
    assert (unknown["territory_completeness"], unknown["territories"]) == ("unknown", [])


# --------------------------------------------------------------------------- #
# Cites dels criteris d'adjudicacio                                             #
# --------------------------------------------------------------------------- #


async def test_award_criteria_cite_sources_with_the_existing_pattern(client_a, tenant_a, db):
    _, company = tenant_a
    tender_id = await api._tender(client_a, company)
    run = await api._run(db, tender_id)
    criterion = await api._criterion(db, run, "C1 FTTH propia", 5.0)
    ref = await api._file_ref(client_a, company, tender_id, label="DOC-03 E.2")
    base = api.T.format(company=company.id, tender=tender_id)
    url = f"{base}/award-criteria/{criterion.id}/citations"

    linked = await client_a.post(url, json={"source_ref_id": ref})
    assert linked.status_code == 200, linked.text
    assert [c["id"] for c in linked.json()["citations"]] == [ref]
    assert linked.json()["citations"][0]["location_label"] == "DOC-03 E.2"
    # Idempotent: tornar a enllacar la mateixa cita no en fa dues.
    again = await client_a.post(url, json={"source_ref_id": ref})
    assert [c["id"] for c in again.json()["citations"]] == [ref]
    assert await _count(db, AwardCriterionSourceRefLink) == 1
    # Mateix expedient obligatori; una cita desconeguda es 404.
    other_tender = await api._tender(client_a, company, title="Una altra licitacio")
    foreign = await api._file_ref(client_a, company, other_tender, label="Altre")
    wrong = await client_a.post(url, json={"source_ref_id": foreign})
    assert (wrong.status_code, _code(wrong)) == (422, "source_ref_tender_mismatch")
    assert (await client_a.post(url, json={"source_ref_id": str(uuid.uuid4())})).status_code == 404
    # La cita enllacada no es pot esborrar (RESTRICT); l'enllac si, i la cita es queda.
    with pytest.raises(IntegrityError):
        async with db.begin_nested():
            await db.execute(sa.delete(TenderSourceRef).where(TenderSourceRef.id == uuid.UUID(ref)))
    unlinked = await client_a.delete(f"{url}/{ref}")
    assert (unlinked.status_code, unlinked.json()["citations"]) == (200, [])
    assert await _count(db, TenderSourceRef) == 2
    # Esborrar el criteri s'emporta l'enllac, no la cita.
    await client_a.post(url, json={"source_ref_id": ref})
    assert await _count(db, AwardCriterionSourceRefLink) == 1
    await db.execute(sa.delete(AwardCriterion).where(AwardCriterion.id == criterion.id))
    await db.commit()
    assert await _count(db, AwardCriterionSourceRefLink) == 0
    assert await _count(db, TenderSourceRef) == 2
    assert await _count(db, Requirement) == 0


# --------------------------------------------------------------------------- #
# Downgrade 0050 -> 0049: refusa, no reinterpreta                               #
# --------------------------------------------------------------------------- #

MIGRATION_0050 = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0050_tender_semantics.py"
)


def _migration_0050():
    spec = importlib.util.spec_from_file_location(MIGRATION_0050.stem, MIGRATION_0050)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


async def _guard(db, migration) -> dict[str, int] | None:
    """Executa la precondicio del downgrade sobre la connexio d'aquest test."""
    return await db.run_sync(
        lambda s: migration.refuse_unless_representable_in_0049(s.connection())
    )


async def _rows(db, migration) -> dict[str, int]:
    return await db.run_sync(lambda s: migration.incompatible_rows(s.connection()))


async def test_the_downgrade_to_0049_passes_only_when_every_row_fits_there(db):
    migration = _migration_0050()
    tender = await m._public_tender(db)
    requirement = await m._requirement(db, tender)
    criterion = await m._criterion(db, tender)
    site = await structure.create_site(
        db,
        tender,
        {"code": "S1", "name": "Seu", "role": TenderSiteRole.REMOTE, "is_critical": False},
        provenance=PROV,
    )
    await db.flush()
    # Tot representable a 0049: cap fila incompatible, cap error.
    assert set(await _rows(db, migration)) == {
        "requisits amb timing before_formalization",
        "criteris amb timing before_formalization",
        "seus sense saber si son critiques",
        "seus sense codi o sense nom",
    }
    assert not any((await _rows(db, migration)).values())
    assert await _guard(db, migration) is None

    # Cada cas incompatible atura el downgrade i la fila queda tal com era.
    structure.set_timing(requirement, RequirementTiming.BEFORE_FORMALIZATION)
    await db.flush()
    with pytest.raises(migration.DowngradeBlockedError, match="downgrade_0050_incompatible_rows"):
        await _guard(db, migration)
    await db.refresh(requirement)
    assert requirement.timing == RequirementTiming.BEFORE_FORMALIZATION
    structure.set_timing(requirement, RequirementTiming.AT_SUBMISSION)
    await db.flush()
    assert await _guard(db, migration) is None

    structure.set_timing(criterion, RequirementTiming.BEFORE_FORMALIZATION)
    await db.flush()
    with pytest.raises(migration.DowngradeBlockedError, match="criteris amb timing"):
        await _guard(db, migration)
    await db.refresh(criterion)
    assert criterion.timing == RequirementTiming.BEFORE_FORMALIZATION
    structure.set_timing(criterion, RequirementTiming.UNKNOWN)
    await db.flush()
    assert await _guard(db, migration) is None

    await structure.update_site(db, tender, site, {"is_critical": None}, provenance=PROV)
    with pytest.raises(migration.DowngradeBlockedError, match="seus sense saber"):
        await _guard(db, migration)
    await db.refresh(site)
    assert site.is_critical is None, "la guarda no coacciona res"
    await structure.update_site(db, tender, site, {"is_critical": False}, provenance=PROV)
    assert await _guard(db, migration) is None

    code_only = await structure.create_site(
        db,
        tender,
        {"code": "D02", "role": TenderSiteRole.REMOTE, "is_critical": False},
        provenance=PROV,
    )
    await db.flush()
    with pytest.raises(migration.DowngradeBlockedError, match="seus sense codi o sense nom"):
        await _guard(db, migration)
    await db.refresh(code_only)
    assert (code_only.code, code_only.name) == ("D02", None), "cap nom sintetic"
    await structure.delete_site(db, code_only)
    assert await _guard(db, migration) is None

    name_only = await structure.create_site(
        db,
        tender,
        {"name": "Can Maset", "role": TenderSiteRole.REMOTE, "is_critical": False},
        provenance=PROV,
    )
    await db.flush()
    with pytest.raises(migration.DowngradeBlockedError, match="1 seus sense codi o sense nom"):
        await _guard(db, migration)
    await db.refresh(name_only)
    assert (name_only.code, name_only.name) == (None, "Can Maset"), "cap codi sintetic"
    assert await _count(db, TenderSite) == 2
