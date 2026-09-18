"""Model de l'oferta economica: estructura, no aritmetica.

Aquesta capa nomes guarda **el que ha escrit una persona**. Cap total, cap marge,
cap equivalent mensual i cap import d'IVA hi viuen: els calcula `app.offers.pricing`
(S1) a partir d'aixo, i persistir-los voldria dir tenir dues versions de la mateixa
xifra que poden discrepar.

Les tres regles que aquests tests protegeixen:

* **Cap derivat.** Hi ha un test que recorre les columnes i falla si algu hi
  afegeix `total_cost` o `margin_pct`.
* **No s'esborren dades economiques** (C-9). Lots, seccions i linies s'arxiven
  amb `archived_at`; els `CASCADE` de les claus foranes hi son per integritat de
  l'agregat, no com a operacio d'usuari.
* **Els rangs els imposa la BASE.** Quantitats i imports no negatius, IVA entre 0
  i 100, durades d'almenys un mes i `pricing_basis` amb CHECK explicit. El gate
  d'S2 ja va demostrar que confiar en `enum_column()` per a aixo dona fals verd.

Les insercions per SQL cru passen els UUID com a parametres TIPATS. Amb
`str(uuid)` cru, SQLite no els fa casar amb els pares i tot salta per la clau
forana, que es exactament com un test d'aquests pot semblar verd sense provar res.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models import Base
from app.models.enums import (
    AnalysisRunStatus,
    ExtractionConfidence,
    PricingBasis,
    RequirementCategory,
    RequirementObligation,
    RequirementScope,
    ReviewStatus,
    SourceType,
    TechnicalSolutionSource,
)
from app.models.offer import (
    EconomicOffer,
    EconomicOfferLine,
    EconomicOfferLot,
    EconomicOfferSection,
)
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tenders import Tender, TenderLot
from app.models.workspace import BidWorkspace, WorkspaceTechnicalSolution

LINES = "economic_offer_lines"


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


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
        expediente_number="7/2026",
        title="Renovacio de la xarxa",
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
async def offer(db, workspace):
    offer = EconomicOffer(company_id=workspace.company_id, workspace_id=workspace.id)
    db.add(offer)
    await db.commit()
    await db.refresh(offer)
    return offer


@pytest.fixture
async def tender_lot(db, tender):
    lot = TenderLot(tender_id=tender.id, external_lot_id="1", lot_number=1, title="Lot 1")
    db.add(lot)
    await db.commit()
    await db.refresh(lot)
    return lot


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


@pytest.fixture
async def solution(db, workspace):
    solution = WorkspaceTechnicalSolution(
        company_id=workspace.company_id,
        workspace_id=workspace.id,
        title="Centraleta amb dos nodes",
        source=TechnicalSolutionSource.MANUAL,
    )
    db.add(solution)
    await db.commit()
    await db.refresh(solution)
    return solution


async def add_lot(db, offer, **overrides) -> EconomicOfferLot:
    lot = EconomicOfferLot(
        company_id=offer.company_id,
        offer_id=offer.id,
        label=overrides.pop("label", "Lot unic"),
        position=overrides.pop("position", 0),
        **overrides,
    )
    db.add(lot)
    await db.commit()
    await db.refresh(lot)
    return lot


async def add_section(db, lot, **overrides) -> EconomicOfferSection:
    section = EconomicOfferSection(
        company_id=lot.company_id,
        offer_lot_id=lot.id,
        title=overrides.pop("title", "Inversio inicial"),
        position=overrides.pop("position", 0),
        **overrides,
    )
    db.add(section)
    await db.commit()
    await db.refresh(section)
    return section


async def add_line(db, section, **overrides) -> EconomicOfferLine:
    line = EconomicOfferLine(
        company_id=section.company_id,
        section_id=section.id,
        concept=overrides.pop("concept", "Servidor"),
        quantity=overrides.pop("quantity", Decimal("1")),
        pricing_basis=overrides.pop("pricing_basis", PricingBasis.ONE_OFF),
        position=overrides.pop("position", 0),
        **overrides,
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


async def raw_insert(db, table: str, **values) -> None:
    """Insercio sense passar per l'ORM, per provar el que imposa la BASE.

    Els UUID van com a parametres TIPATS, no com a text: cada dialecte els
    guarda a la seva manera i, amb la cadena crua, a SQLite no casarien amb cap
    pare. Llavors saltaria la clau forana i el test semblaria verd sense haver
    arribat mai al CHECK que volia provar.
    """
    columns = ", ".join(values)
    params = ", ".join(f":{name}" for name in values)
    statement = sa.text(f"INSERT INTO {table} ({columns}) VALUES ({params})").bindparams(
        *[
            sa.bindparam(name, value, type_=sa.Uuid(as_uuid=True))
            if isinstance(value, uuid.UUID)
            else sa.bindparam(name, value)
            for name, value in values.items()
        ]
    )
    await db.execute(statement)
    await db.commit()


async def raw_delete(db, table: str, row_id: uuid.UUID) -> None:
    """Esborra sense ORM, per provar el CASCADE que imposa la BASE.

    Amb `sa.delete(Model)` qui decideix es el `cascade` de la relacio de
    SQLAlchemy, que a mes carrega els fills; aixo provaria l'ORM, no l'esquema.
    """
    await db.execute(
        sa.text(f"DELETE FROM {table} WHERE id = :id").bindparams(
            sa.bindparam("id", row_id, type_=sa.Uuid(as_uuid=True))
        )
    )
    await db.commit()
    db.expire_all()


async def raw_line(db, section, **overrides) -> None:
    values = {
        "id": uuid.uuid4(),
        "company_id": section.company_id,
        "section_id": section.id,
        "concept": "Linia",
        "quantity": 1,
        "pricing_basis": PricingBasis.ONE_OFF.value,
        "position": 0,
    }
    values.update(overrides)
    await raw_insert(db, LINES, **values)


# --------------------------------------------------------------------------- #
# 1-4. EconomicOffer                                                            #
# --------------------------------------------------------------------------- #


async def test_una_candidatura_nomes_pot_tenir_una_oferta(db, workspace, offer):
    """L'oferta es l'arrel unica de preus del workspace: dues serien dos preus."""
    duplicada = EconomicOffer(company_id=workspace.company_id, workspace_id=workspace.id)
    db.add(duplicada)

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_una_oferta_neix_a_la_revisio_zero(offer):
    assert offer.revision == 0


async def test_una_revisio_negativa_no_es_pot_desar(db, workspace):
    offer = EconomicOffer(
        company_id=workspace.company_id, workspace_id=workspace.id, revision=-1
    )
    db.add(offer)

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_una_oferta_neix_en_euros(offer):
    assert offer.currency == "EUR"


# --------------------------------------------------------------------------- #
# 5-13. EconomicOfferLot                                                        #
# --------------------------------------------------------------------------- #


async def test_un_lot_pot_no_dir_quants_mesos_dura(db, offer):
    """C-3: la durada no s'infereix mai. Desconeguda es un estat legitim."""
    lot = await add_lot(db, offer, duration_months=None)

    assert lot.duration_months is None


async def test_un_lot_pot_durar_un_mes(db, offer):
    lot = await add_lot(db, offer, duration_months=1)

    assert lot.duration_months == 1


async def test_un_lot_no_pot_durar_zero_mesos(db, offer):
    """Zero mesos no es "no ho sabem": es un contracte que no existeix."""
    lot = EconomicOfferLot(
        company_id=offer.company_id,
        offer_id=offer.id,
        label="Lot",
        position=0,
        duration_months=0,
    )
    db.add(lot)

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_un_lot_no_pot_tenir_una_posicio_negativa(db, offer):
    lot = EconomicOfferLot(
        company_id=offer.company_id, offer_id=offer.id, label="Lot", position=-1
    )
    db.add(lot)

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_un_lot_pot_apuntar_a_un_lot_del_plec(db, offer, tender_lot):
    lot = await add_lot(db, offer, tender_lot_id=tender_lot.id)

    assert lot.tender_lot_id == tender_lot.id


async def test_no_hi_pot_haver_dos_lots_actius_per_al_mateix_lot_del_plec(
    db, offer, tender_lot
):
    await add_lot(db, offer, tender_lot_id=tender_lot.id)
    duplicat = EconomicOfferLot(
        company_id=offer.company_id,
        offer_id=offer.id,
        tender_lot_id=tender_lot.id,
        label="Un altre",
        position=1,
    )
    db.add(duplicat)

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_un_lot_arxivat_deixa_lloc_a_un_de_nou(db, offer, tender_lot):
    """Arxivar i tornar a comencar no ha de obligar a reescriure la historia."""
    vell = await add_lot(db, offer, tender_lot_id=tender_lot.id)
    vell.archived_at = datetime.now(UTC)
    await db.commit()

    nou = await add_lot(db, offer, tender_lot_id=tender_lot.id, label="Refet", position=1)

    assert nou.id != vell.id
    assert vell.archived_at is not None


async def test_no_hi_pot_haver_dos_lots_actius_sense_lot_del_plec(db, offer):
    """Sense lots al plec hi ha UNA oferta: dos blocs globals serien dos preus."""
    await add_lot(db, offer)
    segon = EconomicOfferLot(
        company_id=offer.company_id, offer_id=offer.id, label="Un altre", position=1
    )
    db.add(segon)

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_un_lot_global_arxivat_deixa_lloc_a_un_de_nou(db, offer):
    vell = await add_lot(db, offer)
    vell.archived_at = datetime.now(UTC)
    await db.commit()

    nou = await add_lot(db, offer, label="Refet", position=1)

    assert nou.id != vell.id


# --------------------------------------------------------------------------- #
# 14-16. EconomicOfferSection                                                   #
# --------------------------------------------------------------------------- #


async def test_un_lot_pot_tenir_diverses_seccions(db, offer):
    """Els titols son dades, no un enum: cada oferta s'estructura com cal."""
    lot = await add_lot(db, offer)

    await add_section(db, lot, title="Inversio inicial", position=0)
    await add_section(db, lot, title="Serveis recurrents", position=1)
    await add_section(db, lot, title="Equipament", position=2)

    seccions = await db.scalars(
        sa.select(EconomicOfferSection).where(EconomicOfferSection.offer_lot_id == lot.id)
    )
    assert len(list(seccions.all())) == 3


async def test_una_seccio_no_pot_tenir_una_posicio_negativa(db, offer):
    lot = await add_lot(db, offer)
    section = EconomicOfferSection(
        company_id=lot.company_id, offer_lot_id=lot.id, title="Seccio", position=-1
    )
    db.add(section)

    with pytest.raises(IntegrityError):
        await db.commit()


async def test_una_seccio_es_pot_arxivar(db, offer):
    lot = await add_lot(db, offer)
    section = await add_section(db, lot)

    section.archived_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(section)

    assert section.archived_at is not None


# --------------------------------------------------------------------------- #
# 17-33. EconomicOfferLine                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def section(db, offer):
    lot = await add_lot(db, offer)
    return await add_section(db, lot)


@pytest.mark.parametrize("basis", list(PricingBasis))
async def test_una_linia_admet_les_quatre_bases_de_facturacio(db, section, basis):
    line = await add_line(db, section, pricing_basis=basis)

    assert line.pricing_basis == basis


async def test_la_base_de_dades_rebutja_una_base_de_facturacio_inventada(db, section):
    """El CHECK ha de ser a l'esquema, no nomes a Python.

    `enum_column()` genera un VARCHAR sense CHECK: el gate d'S2 ho va demostrar
    amb la solucio tecnica, i per aixo aqui el CHECK es explicit.

    El valor de prova ha de CABRE al VARCHAR(8) que genera l'enum. Amb un de mes
    llarg, PostgreSQL el rebutjaria per llargada abans d'arribar al CHECK
    —i amb una excepcio d'una altra classe—, mentre que SQLite, que no imposa
    la llargada, si que arribaria al CHECK: el test passaria a les dues bandes
    provant dues coses diferents, i cap de les dues seria la que diu el nom.
    """
    assert len("weekly") <= 8

    with pytest.raises(IntegrityError):
        await raw_line(db, section, pricing_basis="weekly")


async def test_una_quantitat_guarda_quatre_decimals(db, section):
    line = await add_line(db, section, quantity=Decimal("2.5000"))

    assert Decimal(str(line.quantity)) == Decimal("2.5")


async def test_una_quantitat_negativa_no_es_pot_desar(db, section):
    with pytest.raises(IntegrityError):
        await add_line(db, section, quantity=Decimal("-1"))


async def test_una_quantitat_zero_si(db, section):
    """Zero es un estat normal mentre es prepara una linia, no un error."""
    line = await add_line(db, section, quantity=Decimal("0"))

    assert Decimal(str(line.quantity)) == Decimal("0")


async def test_una_durada_propia_de_zero_mesos_no_es_pot_desar(db, section):
    with pytest.raises(IntegrityError):
        await add_line(db, section, duration_months_override=0)


async def test_una_durada_propia_substitueix_la_del_lot(db, section):
    """S4 nomes la guarda: qui la resol es S5, i el motor nomes rep la efectiva."""
    line = await add_line(db, section, duration_months_override=12)

    assert line.duration_months_override == 12


async def test_un_cost_negatiu_no_es_pot_desar(db, section):
    with pytest.raises(IntegrityError):
        await add_line(db, section, unit_cost_ex_vat=Decimal("-0.01"))


async def test_un_preu_ofert_negatiu_no_es_pot_desar(db, section):
    with pytest.raises(IntegrityError):
        await add_line(db, section, offered_unit_price_ex_vat=Decimal("-0.01"))


async def test_un_maxim_de_plec_negatiu_no_es_pot_desar(db, section):
    with pytest.raises(IntegrityError):
        await add_line(db, section, tender_max_unit_price_ex_vat=Decimal("-0.01"))


async def test_un_iva_negatiu_no_es_pot_desar(db, section):
    with pytest.raises(IntegrityError):
        await add_line(db, section, vat_rate=Decimal("-1"))


async def test_un_iva_de_mes_de_cent_no_es_pot_desar(db, section):
    with pytest.raises(IntegrityError):
        await add_line(db, section, vat_rate=Decimal("101"))


@pytest.mark.parametrize("vat", [Decimal("0"), Decimal("100")])
async def test_els_extrems_de_l_iva_son_valids(db, section, vat):
    line = await add_line(db, section, vat_rate=vat)

    assert Decimal(str(line.vat_rate)) == vat


async def test_una_linia_pot_no_tenir_encara_ni_cost_ni_preu(db, section):
    """`None` no es `0`: una linia sense cost no costa zero, no se'n sap el cost."""
    line = await add_line(db, section)

    assert line.unit_cost_ex_vat is None
    assert line.offered_unit_price_ex_vat is None
    assert line.tender_max_unit_price_ex_vat is None


async def test_si_el_requisit_desapareix_la_linia_es_conserva(db, section, requirement):
    line = await add_line(db, section, requirement_id=requirement.id)

    await db.execute(sa.delete(Requirement).where(Requirement.id == requirement.id))
    await db.commit()
    await db.refresh(line)

    assert line.requirement_id is None
    assert line.concept == "Servidor"


async def test_si_la_solucio_tecnica_desapareix_la_linia_es_conserva(db, section, solution):
    line = await add_line(db, section, technical_solution_id=solution.id)

    await db.execute(
        sa.delete(WorkspaceTechnicalSolution).where(
            WorkspaceTechnicalSolution.id == solution.id
        )
    )
    await db.commit()
    await db.refresh(line)

    assert line.technical_solution_id is None
    assert line.concept == "Servidor"


async def test_el_proveidor_i_el_producte_son_text_lliure(db, section):
    line = await add_line(db, section, supplier_name="Proveidor SL", product_name="Model X")

    assert line.supplier_name == "Proveidor SL"
    columns = EconomicOfferLine.__table__.c
    assert not columns["supplier_name"].foreign_keys
    assert not columns["product_name"].foreign_keys


def test_no_existeix_cap_taula_de_proveidors():
    """C-10: el cataleg de proveidors queda fora de l'MVP."""
    assert not [name for name in Base.metadata.tables if "supplier" in name]


# --------------------------------------------------------------------------- #
# 34-36. Arxivat                                                                #
# --------------------------------------------------------------------------- #


async def test_una_linia_arxivada_segueix_existint(db, section):
    """C-9: les dades economiques no s'esborren. Arxivar es deixar de comptar."""
    line = await add_line(db, section)

    line.archived_at = datetime.now(UTC)
    await db.commit()

    assert await db.get(EconomicOfferLine, line.id) is not None


async def test_una_seccio_arxivada_segueix_existint(db, offer):
    lot = await add_lot(db, offer)
    section = await add_section(db, lot)

    section.archived_at = datetime.now(UTC)
    await db.commit()

    assert await db.get(EconomicOfferSection, section.id) is not None


async def test_un_lot_arxivat_segueix_existint(db, offer):
    lot = await add_lot(db, offer)

    lot.archived_at = datetime.now(UTC)
    await db.commit()

    assert await db.get(EconomicOfferLot, lot.id) is not None


# --------------------------------------------------------------------------- #
# 37-39. Integritat de l'agregat                                                #
# --------------------------------------------------------------------------- #


async def test_esborrar_la_candidatura_se_n_enduu_l_oferta(db, workspace, offer):
    """CASCADE d'integritat, NO una operacio d'usuari: no hi ha cap esborrat exposat."""
    lot = await add_lot(db, offer)
    section = await add_section(db, lot)
    line = await add_line(db, section)
    # Els identificadors, abans d'esborrar: despres la sessio els te expirats i
    # llegir-los tornaria a anar a una base on ja no hi son.
    ids = (offer.id, lot.id, section.id, line.id)

    await raw_delete(db, "bid_workspaces", workspace.id)

    assert await db.get(EconomicOffer, ids[0]) is None
    assert await db.get(EconomicOfferLot, ids[1]) is None
    assert await db.get(EconomicOfferSection, ids[2]) is None
    assert await db.get(EconomicOfferLine, ids[3]) is None


async def test_esborrar_l_oferta_se_n_enduu_els_lots_i_el_que_pengi(db, offer):
    lot = await add_lot(db, offer)
    section = await add_section(db, lot)
    line = await add_line(db, section)
    ids = (lot.id, section.id, line.id)

    await raw_delete(db, "economic_offers", offer.id)

    assert await db.get(EconomicOfferLot, ids[0]) is None
    assert await db.get(EconomicOfferSection, ids[1]) is None
    assert await db.get(EconomicOfferLine, ids[2]) is None


async def test_esborrar_una_seccio_se_n_enduu_les_linies(db, offer):
    lot = await add_lot(db, offer)
    section = await add_section(db, lot)
    line = await add_line(db, section)
    line_id = line.id

    await raw_delete(db, "economic_offer_sections", section.id)

    assert await db.get(EconomicOfferLine, line_id) is None


# --------------------------------------------------------------------------- #
# 40. Cap derivat persistit                                                     #
# --------------------------------------------------------------------------- #


def test_l_esquema_no_guarda_cap_xifra_calculada():
    """Si un derivat es guarda, hi ha dues versions de la mateixa xifra.

    I quan discrepen, no hi ha cap manera de saber quina mana. Els totals,
    marges, equivalents i imports d'IVA els calcula `app.offers.pricing` (S1)
    a partir d'aquestes columnes, cada vegada.
    """
    prohibits = {
        "periods",
        "effective_duration",
        "effective_duration_months",
        "total_cost",
        "total_revenue",
        "margin_amount",
        "margin_on_sales_pct",
        "markup_on_cost_pct",
        "monthly_equivalent",
        "monthly_equivalent_cost",
        "vat_amount",
        "total_with_vat",
        "difference_to_maximum",
        "exceeds_maximum",
    }
    taules = (
        EconomicOffer.__table__,
        EconomicOfferLot.__table__,
        EconomicOfferSection.__table__,
        EconomicOfferLine.__table__,
    )

    for taula in taules:
        assert not prohibits & set(taula.c.keys()), taula.name
