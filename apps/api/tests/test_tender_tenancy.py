"""Licitacions privades d'una empresa: ningu altre no les veu (S3-S0).

Fins ara totes les licitacions eren publiques i globals. Amb la ingesta manual
n'hi haura de privades, i la regla es una i prou:

    visible per a C  <=>  owner_company_id IS NULL  O  owner_company_id = C

Aquests tests no proven el helper: proven les vies per on una licitacio privada
podria escapar-se —la llista, el detall, les facetes, el pre-fit— perque filtrar
nomes la llista no es aillament.

El cas del pre-fit es el mes important: `MatchingService` enumerava TOTES les
licitacions sense cap filtre d'empresa, de manera que un recalcul de l'empresa B
hauria creat una fila de match sobre una licitacio privada de l'empresa A.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.matching.service import MatchingService
from app.models.enums import SourceType
from app.models.sources import Source
from app.models.tenders import Tender

pytestmark = pytest.mark.asyncio


async def _source(session) -> Source:
    source = Source(code=f"test-{uuid.uuid4().hex[:8]}", name="Font de prova")
    session.add(source)
    await session.flush()
    return source


async def _tender(session, source, *, title: str, owner=None, nuts: str | None = None) -> Tender:
    tender = Tender(
        source_id=source.id,
        external_id=f"exp-{uuid.uuid4().hex[:8]}",
        title=title,
        owner_company_id=owner,
        nuts_code=nuts,
    )
    session.add(tender)
    await session.flush()
    return tender


@pytest.fixture
async def scenario(db, tenant_a, tenant_b):
    """Tres licitacions: una global, una privada d'A i una privada de B."""
    _, company_a = tenant_a
    _, company_b = tenant_b
    source = await _source(db)
    globalt = await _tender(db, source, title="Licitacio global", nuts="ES511")
    private_a = await _tender(
        db, source, title="Privada A", owner=company_a.id, nuts="ES512"
    )
    private_b = await _tender(
        db, source, title="Privada B", owner=company_b.id, nuts="ES513"
    )
    await db.commit()
    return {
        "company_a": company_a,
        "company_b": company_b,
        "global": globalt,
        "a": private_a,
        "b": private_b,
    }


# --------------------------------------------------------------------------- #
# A. Model                                                                      #
# --------------------------------------------------------------------------- #


async def test_owner_company_id_is_nullable_and_means_public(db):
    """Sense propietari es publica. Es el valor per defecte i el de tot el que ja hi ha."""
    source = await _source(db)
    tender = await _tender(db, source, title="Publica")
    await db.commit()
    assert tender.owner_company_id is None


async def test_private_tender_keeps_its_owner(db, tenant_a):
    _, company = tenant_a
    source = await _source(db)
    tender = await _tender(db, source, title="Privada", owner=company.id)
    await db.commit()
    await db.refresh(tender)
    assert tender.owner_company_id == company.id


async def test_owner_company_id_must_reference_a_real_company(db):
    """La FK existeix: no s'hi pot posar una empresa inventada."""
    source = await _source(db)
    db.add(
        Tender(
            source_id=source.id,
            external_id=f"exp-{uuid.uuid4().hex[:8]}",
            title="Fantasma",
            owner_company_id=uuid.uuid4(),
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db.commit()
    await db.rollback()


# --------------------------------------------------------------------------- #
# B. Llista                                                                     #
# --------------------------------------------------------------------------- #


async def _titles(client) -> set[str]:
    response = await client.get("/api/v1/tenders", params={"limit": 100})
    assert response.status_code == 200
    return {item["title"] for item in response.json()["items"]}


async def test_company_a_sees_global_and_its_own(client_a, scenario):
    titles = await _titles(client_a)
    assert "Licitacio global" in titles
    assert "Privada A" in titles


async def test_company_a_does_not_see_b_private(client_a, scenario):
    assert "Privada B" not in await _titles(client_a)


async def test_company_b_sees_global_and_its_own(client_b, scenario):
    titles = await _titles(client_b)
    assert "Licitacio global" in titles
    assert "Privada B" in titles


async def test_company_b_does_not_see_a_private(client_b, scenario):
    assert "Privada A" not in await _titles(client_b)


# --------------------------------------------------------------------------- #
# C. Acces directe per UUID                                                     #
# --------------------------------------------------------------------------- #


async def test_b_cannot_open_a_private_tender_by_id(client_b, scenario):
    """404 i no 403: revelar que existeix ja seria una fuita."""
    response = await client_b.get(f"/api/v1/tenders/{scenario['a'].id}")
    assert response.status_code == 404


async def test_a_cannot_open_b_private_tender_by_id(client_a, scenario):
    response = await client_a.get(f"/api/v1/tenders/{scenario['b'].id}")
    assert response.status_code == 404


async def test_owner_can_open_its_own_private_tender(client_a, scenario):
    response = await client_a.get(f"/api/v1/tenders/{scenario['a'].id}")
    assert response.status_code == 200
    assert response.json()["title"] == "Privada A"


async def test_everyone_can_open_a_global_tender(client_a, client_b, scenario):
    for client in (client_a, client_b):
        response = await client.get(f"/api/v1/tenders/{scenario['global'].id}")
        assert response.status_code == 200


@pytest.mark.parametrize("suffix", ["lots", "documents", "revisions", "history"])
async def test_subresources_of_a_private_tender_are_closed_too(client_b, scenario, suffix):
    """Tancar la fitxa i deixar obertes les subrutes no seria tancar-la."""
    response = await client_b.get(f"/api/v1/tenders/{scenario['a'].id}/{suffix}")
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# D. Pre-fit                                                                    #
# --------------------------------------------------------------------------- #


async def _evaluated(db, company_id) -> set[uuid.UUID]:
    service = MatchingService(db)
    tenders = await service._tenders(None, company_id=company_id)
    return {tender.id for tender in tenders}


async def test_recalculation_for_a_ignores_b_private(db, scenario):
    evaluated = await _evaluated(db, scenario["company_a"].id)
    assert scenario["global"].id in evaluated
    assert scenario["a"].id in evaluated
    assert scenario["b"].id not in evaluated


async def test_recalculation_for_b_ignores_a_private(db, scenario):
    evaluated = await _evaluated(db, scenario["company_b"].id)
    assert scenario["global"].id in evaluated
    assert scenario["b"].id in evaluated
    assert scenario["a"].id not in evaluated


async def test_recalculation_never_writes_a_match_over_a_foreign_private_tender(db, scenario):
    """La prova que importa: cap fila de match de B sobre la privada d'A."""
    from app.models.matching import CompanyTenderMatch

    await MatchingService(db).recalculate(scenario["company_b"].id)
    await db.commit()
    rows = (
        await db.execute(
            sa.select(CompanyTenderMatch.tender_id).where(
                CompanyTenderMatch.company_id == scenario["company_b"].id
            )
        )
    ).scalars().all()
    assert scenario["a"].id not in set(rows)


# --------------------------------------------------------------------------- #
# E. Facetes: fuita per agregacio                                               #
# --------------------------------------------------------------------------- #


async def _facets(client) -> dict:
    response = await client.get("/api/v1/tenders/facets")
    assert response.status_code == 200
    return response.json()


async def test_facets_do_not_leak_a_private_nuts_to_b(client_b, scenario):
    assert "ES512" not in (await _facets(client_b))["nuts_codes"]


async def test_facets_show_own_private_nuts_to_its_owner(client_a, scenario):
    assert "ES512" in (await _facets(client_a))["nuts_codes"]


async def test_facet_total_counts_only_what_the_company_may_see(client_a, client_b, scenario):
    """El comptador tambe es una faceta: no pot delatar el que no es pot llistar."""
    for client in (client_a, client_b):
        facets = await _facets(client)
        titles = await _titles(client)
        assert facets["total_tenders"] == len(titles)


# --------------------------------------------------------------------------- #
# F. Regressio: el que ja hi havia no canvia                                    #
# --------------------------------------------------------------------------- #


async def test_existing_public_tenders_stay_visible_to_everyone(client_a, client_b, scenario):
    for client in (client_a, client_b):
        assert "Licitacio global" in await _titles(client)


async def test_source_type_manual_exists_for_later_phases(db):
    """S3-S1 la necessitara. Aqui nomes ha d'existir i persistir-se."""
    assert SourceType.MANUAL.value == "manual"
    source = Source(
        code=f"manual-{uuid.uuid4().hex[:8]}",
        name="Importacio manual",
        source_type=SourceType.MANUAL,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    assert source.source_type is SourceType.MANUAL


# --------------------------------------------------------------------------- #
# G. Identitat de les licitacions manuals                                       #
# --------------------------------------------------------------------------- #
#
# S3-S0 no crea cap endpoint d'importacio: aixo es S3-S1. El que si que ha de
# quedar demostrat aqui es la INVARIANT sobre la qual aquell endpoint es
# construira, perque viu a l'esquema i no al servei:
#
#     external_id = "manual:" + client_token,  amb UNIQUE(source_id, external_id)
#
# El `client_token` el genera el formulari d'importacio i es manté estable
# durant tots els reintents de la mateixa creacio. Es d'aqui que en surt la
# idempotencia: el segon intent xoca amb la unicitat, i el servei respondra amb
# la fila que ja hi havia en comptes de crear-ne una altra.


async def _manual_source(session) -> Source:
    """L'unica font MANUAL, global i compartida. Mai una per empresa."""
    source = Source(
        code=f"manual-{uuid.uuid4().hex[:8]}",
        name="Importacio manual",
        source_type=SourceType.MANUAL,
    )
    session.add(source)
    await session.flush()
    return source


async def _import(session, source, *, token: str, owner, expedient: str) -> Tender:
    tender = Tender(
        source_id=source.id,
        external_id=f"manual:{token}",
        owner_company_id=owner,
        expediente_number=expedient,
        title=f"Importacio {expedient}",
    )
    session.add(tender)
    await session.flush()
    return tender


async def test_manual_external_id_is_synthetic_and_not_the_expedient(db, tenant_a):
    """El numero d'expedient no entra mai a la clau."""
    _, company = tenant_a
    source = await _manual_source(db)
    token = str(uuid.uuid4())
    tender = await _import(db, source, token=token, owner=company.id, expedient="2024-3137")
    await db.commit()

    assert tender.external_id == f"manual:{token}"
    assert "2024-3137" not in tender.external_id
    assert tender.expediente_number == "2024-3137"


async def test_the_same_client_token_cannot_create_a_second_tender(db, tenant_a):
    """El reintent xoca amb UNIQUE(source_id, external_id). Aixo es el mecanisme."""
    _, company = tenant_a
    source = await _manual_source(db)
    token = str(uuid.uuid4())
    await _import(db, source, token=token, owner=company.id, expedient="2024-3137")
    await db.commit()

    with pytest.raises(sa.exc.IntegrityError):
        await _import(db, source, token=token, owner=company.id, expedient="2024-3137")
    await db.rollback()


async def test_a_retry_with_the_same_token_resolves_to_the_same_tender(db, tenant_a):
    """El que fara el servei a S3-S1: recuperar la que ja hi era, no crear-ne una."""
    _, company = tenant_a
    source = await _manual_source(db)
    token = str(uuid.uuid4())
    first = await _import(db, source, token=token, owner=company.id, expedient="2024-3137")
    await db.commit()
    original_id = first.id

    found = (
        await db.execute(
            sa.select(Tender).where(
                Tender.source_id == source.id, Tender.external_id == f"manual:{token}"
            )
        )
    ).scalars().one()

    assert found.id == original_id
    assert found.owner_company_id == company.id


async def test_two_companies_may_import_the_same_expedient(db, tenant_a, tenant_b):
    """Mateix expedient, dues licitacions diferents. Sense unicitat tenant-aware."""
    _, company_a = tenant_a
    _, company_b = tenant_b
    source = await _manual_source(db)

    expedient = "2024-3137"
    a = await _import(db, source, token=str(uuid.uuid4()), owner=company_a.id, expedient=expedient)
    b = await _import(db, source, token=str(uuid.uuid4()), owner=company_b.id, expedient=expedient)
    await db.commit()

    assert a.id != b.id
    assert a.external_id != b.external_id
    assert a.expediente_number == b.expediente_number == "2024-3137"
    assert a.owner_company_id == company_a.id
    assert b.owner_company_id == company_b.id


async def test_one_global_manual_source_serves_every_company(db, tenant_a, tenant_b):
    """Una sola font MANUAL: la tenancy viu a la licitacio, no a la font."""
    _, company_a = tenant_a
    _, company_b = tenant_b
    source = await _manual_source(db)

    a = await _import(db, source, token=str(uuid.uuid4()), owner=company_a.id, expedient="A-1")
    b = await _import(db, source, token=str(uuid.uuid4()), owner=company_b.id, expedient="B-1")
    await db.commit()

    assert a.source_id == b.source_id == source.id
    assert not hasattr(Source, "owner_company_id")


async def test_the_identifier_is_not_available_before_the_insert(db, tenant_a):
    """Per que hi ha `client_token` i no es fa servir `Tender.id`.

    `UUIDPrimaryKeyMixin` posa `default=uuid.uuid4`, que en vocabulari de
    SQLAlchemy es un default *client-side*: el genera el proces Python de l'API
    —no el navegador ni el servidor SQL— i ho fa **en el moment de l'INSERT**.
    Abans del flush l'objecte encara no te id, aixi que `external_id` no se'n
    pot derivar en construir-lo. D'aqui el token del formulari.
    """
    _, company = tenant_a
    source = await _manual_source(db)
    tender = Tender(
        source_id=source.id,
        external_id=f"manual:{uuid.uuid4()}",
        owner_company_id=company.id,
        title="Sense id encara",
    )
    assert tender.id is None

    db.add(tender)
    await db.flush()
    assert tender.id is not None


# --------------------------------------------------------------------------- #
# H. El filtre tambe als camins que ja existien                                 #
# --------------------------------------------------------------------------- #


async def test_the_existing_matches_endpoint_never_shows_a_foreign_private_tender(
    client_b, db, scenario
):
    """No n'hi ha prou amb el servei: la ruta que ja existia tambe ha de callar."""
    await client_b.post(f"/api/v1/companies/{scenario['company_b'].id}/matches/recalculate")
    response = await client_b.get(
        f"/api/v1/companies/{scenario['company_b'].id}/matches", params={"limit": 100}
    )
    assert response.status_code == 200
    seen = {item["tender"]["id"] for item in response.json()["items"]}
    assert str(scenario["a"].id) not in seen
    assert str(scenario["global"].id) in seen


async def test_recalculation_counts_only_visible_tenders(db, scenario):
    """El comptador que retorna el recalcul tambe delataria el que no es veu."""
    summary = await MatchingService(db).recalculate(scenario["company_a"].id)
    await db.commit()
    visible = (
        await db.execute(
            sa.select(sa.func.count()).select_from(Tender).where(
                sa.or_(
                    Tender.owner_company_id.is_(None),
                    Tender.owner_company_id == scenario["company_a"].id,
                )
            )
        )
    ).scalar_one()
    assert summary.tenders_evaluated == visible
