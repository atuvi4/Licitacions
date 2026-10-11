"""Model de la solucio tecnica: "com oferirem aquest requisit en aquesta candidatura?".

No respon si complim (aixo es `CompanyRequirementAssessment`), ni amb quin paper
ho acreditarem (`WorkspaceEvidenceItem`), ni quant costa (l'oferta economica).
Son quatre preguntes i el sistema no les ha de confondre mai.

Aquests tests son de MODEL: constraints, indexs i esborrats. La coherencia de
`validated` —que una solucio validada porti signatura, data, motiu i les dues
empremtes— es del servei (S3) i no s'imposa aqui: durant `drafting` aquests
camps han de poder ser buits, i una restriccio de base de dades ho impediria.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models import Base
from app.models.enums import (
    AnalysisRunStatus,
    ExtractionConfidence,
    RequirementCategory,
    RequirementObligation,
    RequirementScope,
    ReviewStatus,
    SourceType,
    TechnicalSolutionSource,
    TechnicalSolutionStatus,
)
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tenders import Tender
from app.models.workspace import BidWorkspace, WorkspaceTechnicalSolution

TABLE = "workspace_technical_solutions"


@pytest.fixture
async def tender(db):
    source = Source(
        code=f"c-{uuid.uuid4().hex[:8]}", name="Font", source_type=SourceType.SYNDICATION_ATOM
    )
    db.add(source)
    await db.flush()
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        expediente_number="1/2026",
        title="Servei de manteniment integral",
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


@pytest.fixture
async def workspace(db, tenant_a, tender):
    _user, company = tenant_a
    workspace = BidWorkspace(company_id=company.id, tender_id=tender.id)
    db.add(workspace)
    await db.commit()
    await db.refresh(workspace)
    return workspace


@pytest.fixture
async def requirement(db, tender):
    run = RequirementExtractionRun(
        tender_id=tender.id,
        status=AnalysisRunStatus.COMPLETED,
        input_fingerprint=uuid.uuid4().hex * 2,
        extractor_version="tender-requirements-v1",
        prompt_version="requirements-v1",
        schema_version="requirements-schema-v1",
        provider="fake",
        model="fake-model-v1",
        run_basis="real",  # fila sintetica vigent (0061: a PG nomes real/gold)
        is_current=True,
    )
    db.add(run)
    await db.flush()
    requirement = Requirement(
        run_id=run.id,
        tender_id=tender.id,
        category=RequirementCategory.TECHNICAL_REQUIREMENT,
        obligation=RequirementObligation.MANDATORY,
        scope=RequirementScope.TENDER,
        title="Centraleta VoIP redundant",
        confidence=ExtractionConfidence.HIGH,
        review_status=ReviewStatus.UNREVIEWED,
        dedup_key=uuid.uuid4().hex,
    )
    db.add(requirement)
    await db.commit()
    await db.refresh(requirement)
    return requirement


async def add_solution(db, workspace, **overrides) -> WorkspaceTechnicalSolution:
    # `source` no te valor per defecte al model a proposit: es provenance i
    # l'escriu qui crea la fila. Aqui el dedueix el constructor de proves a
    # partir de si hi ha requisit, i cada test el pot sobreescriure.
    default_source = (
        TechnicalSolutionSource.REQUIREMENT
        if overrides.get("requirement_id")
        else TechnicalSolutionSource.MANUAL
    )
    solution = WorkspaceTechnicalSolution(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        title=overrides.pop("title", "Centraleta amb dos nodes"),
        source=overrides.pop("source", default_source),
        **overrides,
    )
    db.add(solution)
    await db.commit()
    await db.refresh(solution)
    return solution


async def raw_insert(db, **values) -> None:
    """Insercio sense passar per l'ORM, per provar el que imposa la BASE DE DADES.

    Amb l'ORM, un enum invalid el rebutjaria SQLAlchemy abans d'arribar-hi, i
    llavors el test no diria res del CHECK que hi ha a l'esquema real.
    """
    values.setdefault("id", str(uuid.uuid4()))
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    await db.execute(sa.text(f"INSERT INTO {TABLE} ({columns}) VALUES ({params})"), values)
    await db.commit()


# --------------------------------------------------------------------------- #
# 1 i 2. Valors per defecte                                                     #
# --------------------------------------------------------------------------- #


async def test_una_solucio_neix_sense_definir(db, workspace):
    solution = await add_solution(db, workspace)

    assert solution.status == TechnicalSolutionStatus.NOT_STARTED


async def test_una_solucio_pot_venir_d_un_requisit(db, workspace):
    solution = await add_solution(db, workspace, source=TechnicalSolutionSource.REQUIREMENT)

    assert solution.source == TechnicalSolutionSource.REQUIREMENT


async def test_una_solucio_pot_ser_manual(db, workspace):
    solution = await add_solution(db, workspace, source=TechnicalSolutionSource.MANUAL)

    assert solution.source == TechnicalSolutionSource.MANUAL


def test_l_origen_no_te_cap_valor_per_defecte():
    """La procedencia s'escriu, no s'endevina.

    Amb un valor per defecte, una fila creada per un cami que s'oblides de
    dir-ho passaria per "surt d'un requisit del plec" sense que ningu ho hagues
    afirmat. Es informacio de provenance: la posa qui crea la fila (S3), i si no
    la posa, la insercio ha de fallar.
    """
    column = Base.metadata.tables[TABLE].c.source

    assert column.default is None
    assert column.server_default is None
    assert not column.nullable


async def test_una_solucio_sense_origen_no_es_pot_desar(db, workspace):
    with pytest.raises(IntegrityError):
        await raw_insert(
            db,
            company_id=str(workspace.company_id),
            workspace_id=str(workspace.id),
            title="Solucio",
            status=TechnicalSolutionStatus.NOT_STARTED.value,
            source=None,
        )


# --------------------------------------------------------------------------- #
# 3 i 4. Els enums els imposa l'esquema, no nomes Python                        #
# --------------------------------------------------------------------------- #


async def test_la_base_de_dades_rebutja_un_estat_que_no_existeix(db, workspace):
    with pytest.raises(IntegrityError):
        await raw_insert(
            db,
            company_id=str(workspace.company_id),
            workspace_id=str(workspace.id),
            title="Solucio",
            status="quasi_validada",
            source=TechnicalSolutionSource.REQUIREMENT.value,
        )


async def test_la_base_de_dades_rebutja_un_origen_que_no_existeix(db, workspace):
    with pytest.raises(IntegrityError):
        await raw_insert(
            db,
            company_id=str(workspace.company_id),
            workspace_id=str(workspace.id),
            title="Solucio",
            status=TechnicalSolutionStatus.NOT_STARTED.value,
            source="importada",
        )


# --------------------------------------------------------------------------- #
# 5 i 6. Multi-tenant i pertinenca                                              #
# --------------------------------------------------------------------------- #


async def test_una_solucio_sense_candidatura_no_es_pot_desar(db, workspace):
    with pytest.raises(IntegrityError):
        await raw_insert(
            db,
            company_id=str(workspace.company_id),
            workspace_id=None,
            title="Solucio",
            status=TechnicalSolutionStatus.NOT_STARTED.value,
            source=TechnicalSolutionSource.REQUIREMENT.value,
        )


async def test_una_solucio_sense_empresa_no_es_pot_desar(db, workspace):
    with pytest.raises(IntegrityError):
        await raw_insert(
            db,
            company_id=None,
            workspace_id=str(workspace.id),
            title="Solucio",
            status=TechnicalSolutionStatus.NOT_STARTED.value,
            source=TechnicalSolutionSource.REQUIREMENT.value,
        )


# --------------------------------------------------------------------------- #
# 7 i 8. Unicitat: una solucio per requisit, les manuals sense limit            #
# --------------------------------------------------------------------------- #


async def test_un_requisit_nomes_pot_tenir_una_solucio_dins_la_mateixa_candidatura(
    db, workspace, requirement
):
    """El que fa que sincronitzar dues vegades no dupliqui res."""
    await add_solution(db, workspace, requirement_id=requirement.id)

    with pytest.raises(IntegrityError):
        await add_solution(db, workspace, requirement_id=requirement.id, title="Una altra")


async def test_dues_solucions_manuals_conviuen_a_la_mateixa_candidatura(db, workspace):
    """Els items manuals porten `requirement_id` NULL i l'index parcial no els mira."""
    first = await add_solution(db, workspace, source=TechnicalSolutionSource.MANUAL)
    second = await add_solution(
        db, workspace, source=TechnicalSolutionSource.MANUAL, title="Segona"
    )

    assert first.id != second.id
    assert first.requirement_id is None and second.requirement_id is None


async def test_el_mateix_requisit_pot_tenir_solucio_a_dues_candidatures(
    db, tenant_a, tender, workspace, requirement
):
    """La unicitat es per candidatura, no per requisit: dos lots, dues solucions."""
    _user, company = tenant_a
    other_tender = Tender(
        source_id=tender.source_id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title="Un altre expedient",
    )
    db.add(other_tender)
    await db.flush()
    other = BidWorkspace(company_id=company.id, tender_id=other_tender.id)
    db.add(other)
    await db.commit()

    await add_solution(db, workspace, requirement_id=requirement.id)
    await add_solution(db, other, requirement_id=requirement.id)

    total = await db.scalar(
        sa.select(sa.func.count())
        .select_from(WorkspaceTechnicalSolution)
        .where(WorkspaceTechnicalSolution.requirement_id == requirement.id)
    )
    assert total == 2


# --------------------------------------------------------------------------- #
# 9 i 10. Que passa quan desapareix el que hi ha a l'altra banda                #
# --------------------------------------------------------------------------- #


async def test_si_el_requisit_desapareix_la_solucio_es_conserva_sense_vincle(
    db, workspace, requirement
):
    """Un run d'extraccio nou no pot endur-se per davant feina ja escrita."""
    solution = await add_solution(db, workspace, requirement_id=requirement.id)

    await db.execute(sa.delete(Requirement).where(Requirement.id == requirement.id))
    await db.commit()
    await db.refresh(solution)

    assert solution.id is not None
    assert solution.requirement_id is None


async def test_si_s_esborra_la_candidatura_les_solucions_se_n_van_amb_ella(db, workspace):
    solution = await add_solution(db, workspace)
    solution_id = solution.id

    await db.execute(sa.delete(BidWorkspace).where(BidWorkspace.id == workspace.id))
    await db.commit()

    survivor = await db.scalar(
        sa.select(WorkspaceTechnicalSolution.id).where(
            WorkspaceTechnicalSolution.id == solution_id
        )
    )
    assert survivor is None


# --------------------------------------------------------------------------- #
# 11 i 12. Els camps humans son buits mentre ningu no els ompli                 #
# --------------------------------------------------------------------------- #


async def test_una_solucio_en_esborrany_no_te_signatura(db, workspace):
    solution = await add_solution(db, workspace, status=TechnicalSolutionStatus.DRAFTING)

    assert solution.validated_by is None
    assert solution.validated_at is None
    assert solution.validated_note is None
    assert solution.validated_requirement_fingerprint is None
    assert solution.validated_content_fingerprint is None


async def test_una_solucio_pot_no_tenir_responsable(db, workspace):
    solution = await add_solution(db, workspace)

    assert solution.owner_id is None


async def test_la_signatura_apunta_a_una_persona_i_a_dues_empremtes(db, workspace, tenant_a):
    """S2 nomes guarda els camps; qui els omple i quan es cosa de S3."""
    user, _company = tenant_a
    fingerprint = "a" * 64

    solution = await add_solution(
        db,
        workspace,
        status=TechnicalSolutionStatus.VALIDATED,
        validated_by=user.id,
        validated_note="El requisit el cobreix la centraleta redundant del proveidor X.",
        validated_requirement_fingerprint=fingerprint,
        validated_content_fingerprint=fingerprint,
    )

    assert solution.validated_by == user.id
    assert solution.validated_requirement_fingerprint == fingerprint


# --------------------------------------------------------------------------- #
# 13 i 14. El proveidor es text, i no hi ha cap cataleg                         #
# --------------------------------------------------------------------------- #


async def test_el_proveidor_i_el_producte_son_text_lliure(db, workspace):
    solution = await add_solution(
        db, workspace, supplier_name="Distribuidora Exemple SL", product_name="Model 4200"
    )

    assert solution.supplier_name == "Distribuidora Exemple SL"
    assert solution.product_name == "Model 4200"


def test_ni_el_proveidor_ni_el_producte_son_claus_foranes():
    table = Base.metadata.tables[TABLE]

    assert not table.c.supplier_name.foreign_keys
    assert not table.c.product_name.foreign_keys
    assert isinstance(table.c.supplier_name.type, sa.String)


def test_no_existeix_cap_taula_de_proveidors():
    """C-10: el cataleg de productes i proveidors queda fora de l'MVP."""
    assert "suppliers" not in Base.metadata.tables


# --------------------------------------------------------------------------- #
# Empremtes: la mateixa amplada que les que ja existeixen                       #
# --------------------------------------------------------------------------- #


def test_les_empremtes_fan_la_mateixa_amplada_que_les_del_dossier():
    """SHA-256 en hexadecimal: 64 caracters, com `relevance_requirement_fingerprint`."""
    table = Base.metadata.tables[TABLE]
    existing = Base.metadata.tables["workspace_evidence_items"]
    expected = existing.c.relevance_requirement_fingerprint.type.length

    assert table.c.validated_requirement_fingerprint.type.length == expected
    assert table.c.validated_content_fingerprint.type.length == expected


def test_totes_les_claus_foranes_esborren_com_toca():
    """CASCADE nomes cap a la candidatura; tota la resta conserva la fila."""
    table = Base.metadata.tables[TABLE]
    actual = {
        tuple(column.name for column in constraint.columns): constraint.ondelete
        for constraint in table.foreign_key_constraints
    }

    assert actual == {
        ("company_id",): "CASCADE",
        ("workspace_id",): "CASCADE",
        ("requirement_id",): "SET NULL",
        ("owner_id",): "SET NULL",
        ("validated_by",): "SET NULL",
        ("created_by",): "SET NULL",
        ("updated_by",): "SET NULL",
    }
