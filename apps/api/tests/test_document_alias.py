"""Dedup logic per sha (3B-2 §2.1) i un sol fitxer vigent per document (§2.4).

Dos documents d'una mateixa licitacio amb els mateixos bytes vigents son un sol
document per a l'analisi: el de menys prioritat queda com a alies
(`duplicate_of_document_id`) del canonic i no se selecciona mai. Prioritat:
feed > operator_url > operator_upload; empat -> el mes antic. Si el canonic es
retira, l'alies es promou i queda una revisio `document_intake`.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.enums import (
    DocumentContributionStatus,
    DownloadStatus,
    TenderDocumentOrigin,
    TenderDocumentType,
)
from app.models.tender_documents import TenderDocumentFile
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import TenderDocument
from app.services.document_alias import (
    assign_duplicate_alias,
    promote_alias_if_canonical_retired,
)
from app.services.tender_document_processing import process_tender_document
from app.services.tender_lifecycle import DOCUMENT_INTAKE_TRIGGER
from app.tenders.documents import analyzable_documents
from tests.fixtures.pdf_builder import build_pdf

PUBLIC_IP = "93.184.216.34"
PCAP = build_pdf(["Plec de clausules administratives particulars. " * 8] * 3)
PCAP_V2 = build_pdf(["Plec REVISAT de clausules administratives particulars. " * 8] * 4)
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def serving(payload: bytes, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=payload, headers={"content-type": "application/pdf"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def process(session, document, payload=PCAP, status=200, **kwargs):
    async with serving(payload, status) as client:
        return await process_tender_document(
            session, document, client=client, resolver=resolver, **kwargs
        )


async def operator_document(
    session,
    tender_id,
    *,
    origin=TenderDocumentOrigin.OPERATOR_URL,
    created_at: datetime | None = None,
    verified: bool = True,
) -> TenderDocument:
    suffix = uuid.uuid4().hex[:8]
    document = TenderDocument(
        tender_id=tender_id,
        name=f"operador-{suffix}.pdf",
        document_type=TenderDocumentType.OTHER,
        url=f"https://contractaciopublica.cat/perfil/{suffix}.pdf",
        origin=origin,
        governing_source_id=None,
        contribution_status=(
            DocumentContributionStatus.VERIFIED
            if verified
            else DocumentContributionStatus.PENDING_VERIFICATION
        ),
        verified_at=T0 if verified else None,
        contribution_source_url="https://contractaciopublica.cat/perfil",
    )
    if created_at is not None:
        document.created_at = created_at
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


@pytest.fixture
async def ordered_pair(session, tender_document, second_tender_document):
    """Els dos documents de les fixtures, amb `created_at` diferent i conegut.

    A SQLite `CURRENT_TIMESTAMP` te resolucio de segon: dos documents creats seguits
    empatarien i l'ordre dependria de l'id.
    """
    tender_document.created_at = T0
    second_tender_document.created_at = T0 + timedelta(minutes=1)
    await session.commit()
    return tender_document, second_tender_document


async def reload(session, document: TenderDocument) -> TenderDocument:
    return (
        await session.execute(
            sa.select(TenderDocument)
            .where(TenderDocument.id == document.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def latest_revision(session, tender_id) -> TenderRevision:
    return await session.scalar(
        sa.select(TenderRevision)
        .where(TenderRevision.tender_id == tender_id)
        .order_by(TenderRevision.revision_number.desc())
        .limit(1)
    )


async def current_files(session, document_id) -> list[TenderDocumentFile]:
    return list(
        (
            await session.scalars(
                sa.select(TenderDocumentFile)
                .where(
                    TenderDocumentFile.tender_document_id == document_id,
                    TenderDocumentFile.is_current.is_(True),
                )
                .execution_options(populate_existing=True)
            )
        ).all()
    )


# --------------------------------------------------------------------------- #
# Alies en processar                                                            #
# --------------------------------------------------------------------------- #


async def test_dues_url_amb_els_mateixos_bytes_el_segon_es_alies_i_mai_es_selecciona(
    session, tender_document, second_tender_document, ordered_pair
):
    await process(session, tender_document)
    await process(session, second_tender_document)

    first = await reload(session, tender_document)
    second = await reload(session, second_tender_document)
    assert first.duplicate_of_document_id is None
    assert second.duplicate_of_document_id == first.id

    selected = await analyzable_documents(session, first.tender_id)
    assert [d.id for d in selected] == [first.id]


async def test_bytes_diferents_no_fan_cap_alies(session, tender_document, second_tender_document):
    await process(session, tender_document)
    await process(session, second_tender_document, payload=PCAP_V2)

    assert (await reload(session, tender_document)).duplicate_of_document_id is None
    assert (await reload(session, second_tender_document)).duplicate_of_document_id is None


async def test_el_feed_mana_sobre_l_operador_encara_que_arribi_despres(session, tender_document):
    """Prioritat feed > operator_url: el document d'operador, mes antic, passa a alies."""
    operator = await operator_document(session, tender_document.tender_id)
    await process(session, operator)
    assert (await reload(session, operator)).duplicate_of_document_id is None

    await process(session, tender_document)

    assert (await reload(session, tender_document)).duplicate_of_document_id is None
    assert (await reload(session, operator)).duplicate_of_document_id == tender_document.id
    selected = await analyzable_documents(session, tender_document.tender_id)
    assert [d.id for d in selected] == [tender_document.id]


async def test_operator_url_mana_sobre_operator_upload(session, tender_document):
    tender_id = tender_document.tender_id
    upload = await operator_document(
        session, tender_id, origin=TenderDocumentOrigin.OPERATOR_UPLOAD, created_at=T0
    )
    by_url = await operator_document(session, tender_id, created_at=T0 + timedelta(days=1))
    await process(session, upload)
    await process(session, by_url)

    assert (await reload(session, by_url)).duplicate_of_document_id is None
    assert (await reload(session, upload)).duplicate_of_document_id == by_url.id


async def portal_document(session, tender_document, *, created_at: datetime) -> TenderDocument:
    """Un document del portal oficial (0065): governat per una font, sense verificacio."""
    suffix = uuid.uuid4().hex[:8]
    document = TenderDocument(
        tender_id=tender_document.tender_id,
        name=f"portal-{suffix}.pdf",
        document_type=TenderDocumentType.OTHER,
        url=f"https://contractaciopublica.cat/portal-api/descarrega-document/{suffix}/x",
        origin=TenderDocumentOrigin.PORTAL,
        governing_source_id=tender_document.governing_source_id,
        contribution_status=DocumentContributionStatus.NOT_APPLICABLE,
    )
    document.created_at = created_at
    session.add(document)
    await session.commit()
    await session.refresh(document)
    return document


async def test_el_portal_mana_sobre_l_operador_encara_que_sigui_mes_nou(
    session, tender_document
):
    """Prioritat portal > operator_url: el portal el verifica l'origen, no una persona."""
    operator = await operator_document(session, tender_document.tender_id, created_at=T0)
    portal = await portal_document(session, tender_document, created_at=T0 + timedelta(days=1))
    await process(session, operator)
    await process(session, portal)

    assert (await reload(session, portal)).duplicate_of_document_id is None
    assert (await reload(session, operator)).duplicate_of_document_id == portal.id
    selected = {d.id for d in await analyzable_documents(session, tender_document.tender_id)}
    assert portal.id in selected and operator.id not in selected


async def test_el_feed_mana_sobre_el_portal(session, tender_document):
    """Prioritat feed > portal: el feed es la font de la licitacio."""
    tender_document.created_at = T0 + timedelta(days=1)
    await session.commit()
    portal = await portal_document(session, tender_document, created_at=T0)
    await process(session, portal)
    await process(session, tender_document)

    assert (await reload(session, tender_document)).duplicate_of_document_id is None
    assert (await reload(session, portal)).duplicate_of_document_id == tender_document.id


async def test_empat_de_prioritat_mana_el_mes_antic(session, tender_document):
    tender_id = tender_document.tender_id
    newer = await operator_document(session, tender_id, created_at=T0 + timedelta(days=2))
    older = await operator_document(session, tender_id, created_at=T0)
    await process(session, newer)
    await process(session, older)

    assert (await reload(session, older)).duplicate_of_document_id is None
    assert (await reload(session, newer)).duplicate_of_document_id == older.id


async def test_un_pendent_no_pot_fer_d_alies_canonic_a_un_verificat(session, tender_document):
    """Un canonic que no es pot analitzar deixaria el contingut fora de l'analisi."""
    tender_id = tender_document.tender_id
    pending = await operator_document(session, tender_id, created_at=T0, verified=False)
    verified = await operator_document(session, tender_id, created_at=T0 + timedelta(days=1))
    await process(session, pending)
    await process(session, verified)

    assert (await reload(session, verified)).duplicate_of_document_id is None
    assert (await reload(session, pending)).duplicate_of_document_id == verified.id


async def test_un_alies_amb_bytes_nous_deixa_de_ser_alies(
    session, tender_document, second_tender_document, ordered_pair
):
    await process(session, tender_document)
    await process(session, second_tender_document)
    assert (await reload(session, second_tender_document)).duplicate_of_document_id is not None

    await process(session, second_tender_document, payload=PCAP_V2, force_download=True)

    assert (await reload(session, second_tender_document)).duplicate_of_document_id is None


async def test_un_canonic_amb_bytes_nous_allibera_els_seus_alies(
    session, tender_document, second_tender_document, ordered_pair
):
    await process(session, tender_document)
    await process(session, second_tender_document)

    await process(session, tender_document, payload=PCAP_V2, force_download=True)

    assert (await reload(session, tender_document)).duplicate_of_document_id is None
    assert (await reload(session, second_tender_document)).duplicate_of_document_id is None


async def test_assign_sense_bytes_vigents_no_fa_res(session, tender_document):
    assert await assign_duplicate_alias(session, tender_document.tender_id, tender_document) is None
    assert (await reload(session, tender_document)).duplicate_of_document_id is None


# --------------------------------------------------------------------------- #
# Promocio quan el canonic es retira                                            #
# --------------------------------------------------------------------------- #


async def test_si_el_canonic_es_retira_l_alies_es_promou_amb_revisio_document_intake(
    session, tender_document, second_tender_document, ordered_pair
):
    await process(session, tender_document)
    await process(session, second_tender_document)
    before = await latest_revision(session, tender_document.tender_id)

    canonical = await reload(session, tender_document)
    canonical.removed_at = datetime.now(UTC)
    promoted = await promote_alias_if_canonical_retired(session, canonical)
    await session.commit()

    assert promoted is not None and promoted.id == second_tender_document.id
    assert (await reload(session, second_tender_document)).duplicate_of_document_id is None
    selected = await analyzable_documents(session, tender_document.tender_id)
    assert [d.id for d in selected] == [second_tender_document.id]

    revision = await latest_revision(session, tender_document.tender_id)
    assert revision.revision_number == before.revision_number + 1
    assert revision.trigger == DOCUMENT_INTAKE_TRIGGER == "document_intake"
    # El contingut continua disponible sota l'alies promogut: cap retirada de plec.
    events = (
        await session.scalars(
            sa.select(TenderChangeEvent.change_code).where(
                TenderChangeEvent.to_revision_id == revision.id
            )
        )
    ).all()
    # `_document_changes` indexa per sha: el canonic retirat amb un alies que
    # conserva els bytes no dona `DOCUMENT_REMOVED`. La promocio si que s'explica:
    # un canvi informatiu, no material (cap revisio sense events).
    assert list(events) == ["DOCUMENT_ALIAS_CHANGED"]
    assert revision.change_summary["material"] is False
    assert revision.change_summary["impacts"] == ["informational"]


async def test_el_processat_que_crea_un_alies_ho_explica_a_la_revisio(
    session, tender_document, second_tender_document, ordered_pair
):
    await process(session, tender_document)
    await process(session, second_tender_document)

    revision = await latest_revision(session, tender_document.tender_id)
    events = (
        await session.execute(
            sa.select(TenderChangeEvent.change_code, TenderChangeEvent.new_value).where(
                TenderChangeEvent.to_revision_id == revision.id,
                TenderChangeEvent.subject == second_tender_document.url,
            )
        )
    ).all()
    codes = {code: value for code, value in events}
    assert codes["DOCUMENT_ALIAS_CHANGED"] == tender_document.url
    assert "DOCUMENT_CONTENT_IDENTIFIED" in codes


async def test_mateixos_bytes_el_pcap_es_canonic_davant_d_un_other_mes_antic(
    session, tender_document, second_tender_document
):
    """Desempat per tipus: un plec (PCAP/PPT/annex) mana sobre anunci/acta/altres."""
    tender_document.created_at = T0  # OTHER, mes antic
    second_tender_document.created_at = T0 + timedelta(days=1)
    second_tender_document.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    await session.commit()

    await process(session, tender_document)
    await process(session, second_tender_document)

    assert (await reload(session, second_tender_document)).duplicate_of_document_id is None
    assert (
        await reload(session, tender_document)
    ).duplicate_of_document_id == second_tender_document.id
    selected = await analyzable_documents(session, tender_document.tender_id)
    assert [d.id for d in selected] == [second_tender_document.id]


async def test_l_origen_mana_sobre_el_tipus(session, tender_document):
    """Un operator_url de tipus PCAP no desplaça un feed de tipus OTHER."""
    operator = await operator_document(session, tender_document.tender_id)
    operator.document_type = TenderDocumentType.ADMINISTRATIVE_TERMS
    await session.commit()
    await process(session, operator)
    await process(session, tender_document)

    assert (await reload(session, tender_document)).duplicate_of_document_id is None
    assert (await reload(session, operator)).duplicate_of_document_id == tender_document.id


async def test_l_alies_es_calcula_encara_que_el_contingut_guanyi_una_carrera(
    session, tender_document, second_tender_document, ordered_pair, monkeypatch
):
    """`get_or_create_content` per la via de la carrera fa rollback i caduca la sessio.

    Un altre processat crea el mateix contingut entre la consulta i l'INSERT: el
    nostre INSERT xoca, es fa rollback (tots els objectes caducats) i es pren el
    guanyador. Llegir `file`/`document` caducats amb AsyncSession petaria
    (MissingGreenlet); l'alies s'ha de calcular igualment.
    """
    from app.models.tender_documents import TenderDocumentContent
    from app.services.storage import build_tender_document_key

    await process(session, tender_document, payload=PCAP_V2)
    sha = hashlib.sha256(PCAP).hexdigest()
    original = session.scalar
    raced = {"done": False}

    async def racing_scalar(statement, *args, **kwargs):
        descriptions = getattr(statement, "column_descriptions", None) or []
        if (
            not raced["done"]
            and descriptions
            and descriptions[0].get("type") is TenderDocumentContent
        ):
            raced["done"] = True
            # L'altre processat guanya: el contingut ja hi es quan fem l'INSERT.
            await session.execute(
                sa.insert(TenderDocumentContent.__table__).values(
                    id=uuid.uuid4(),
                    sha256=sha,
                    storage_key=build_tender_document_key(sha, "pdf"),
                    file_size=len(PCAP),
                    mime_type="application/pdf",
                    detected_extension="pdf",
                    is_text_extractable=True,
                )
            )
            await session.commit()
            return None
        return await original(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", racing_scalar)
    # El primer document passa a tenir els bytes del segon (versio nova).
    await process(session, tender_document, force_download=True)
    monkeypatch.setattr(session, "scalar", original)
    assert raced["done"] is True

    await process(session, second_tender_document)

    assert len(await current_files(session, tender_document.id)) == 1
    assert (await reload(session, tender_document)).duplicate_of_document_id is None
    assert (
        await reload(session, second_tender_document)
    ).duplicate_of_document_id == tender_document.id


async def test_l_alies_d_un_document_que_guanya_la_carrera_al_mateix_processat(
    session, tender_document, second_tender_document, ordered_pair, monkeypatch
):
    """La carrera al processat del SEGON document: l'alies s'assigna en aquell moment."""
    from app.models.tender_documents import TenderDocumentContent

    await process(session, tender_document, payload=PCAP_V2)
    original = session.scalar
    raced = {"done": False}

    async def racing_scalar(statement, *args, **kwargs):
        descriptions = getattr(statement, "column_descriptions", None) or []
        if (
            not raced["done"]
            and descriptions
            and descriptions[0].get("type") is TenderDocumentContent
        ):
            # La consulta no el veu (encara no hi era quan l'altre ha mirat), pero
            # l'INSERT xocara amb el del primer document: via de la carrera.
            raced["done"] = True
            return None
        return await original(statement, *args, **kwargs)

    monkeypatch.setattr(session, "scalar", racing_scalar)
    await process(session, second_tender_document, payload=PCAP_V2)
    monkeypatch.setattr(session, "scalar", original)
    assert raced["done"] is True

    assert (
        await reload(session, second_tender_document)
    ).duplicate_of_document_id == tender_document.id


async def test_la_promocio_tria_per_prioritat_i_despres_el_mes_antic(session, tender_document):
    tender_id = tender_document.tender_id
    await process(session, tender_document)
    upload = await operator_document(
        session, tender_id, origin=TenderDocumentOrigin.OPERATOR_UPLOAD, created_at=T0
    )
    newer = await operator_document(session, tender_id, created_at=T0 + timedelta(days=2))
    older = await operator_document(session, tender_id, created_at=T0 + timedelta(days=1))
    for document in (upload, newer, older):
        await process(session, document)
        assert (await reload(session, document)).duplicate_of_document_id == tender_document.id

    canonical = await reload(session, tender_document)
    canonical.removed_at = datetime.now(UTC)
    promoted = await promote_alias_if_canonical_retired(session, canonical)
    await session.commit()

    assert promoted.id == older.id
    assert (await reload(session, older)).duplicate_of_document_id is None
    assert (await reload(session, newer)).duplicate_of_document_id == older.id
    assert (await reload(session, upload)).duplicate_of_document_id == older.id


async def test_un_canonic_actiu_o_sense_alies_no_promou_res(
    session, tender_document, second_tender_document, ordered_pair
):
    await process(session, tender_document)
    await process(session, second_tender_document)
    before = await latest_revision(session, tender_document.tender_id)

    # Encara actiu: res a promoure.
    assert await promote_alias_if_canonical_retired(session, tender_document) is None
    # Retirar l'alies (no el canonic) tampoc no promou res.
    alias = await reload(session, second_tender_document)
    alias.removed_at = datetime.now(UTC)
    assert await promote_alias_if_canonical_retired(session, alias) is None
    await session.commit()

    after = await latest_revision(session, tender_document.tender_id)
    assert after.id == before.id


# --------------------------------------------------------------------------- #
# Un sol fitxer vigent per document                                             #
# --------------------------------------------------------------------------- #


async def test_l_index_parcial_impedeix_dos_fitxers_vigents(session, tender_document):
    for version in (1, 2):
        session.add(
            TenderDocumentFile(
                tender_document_id=tender_document.id,
                version_number=version,
                is_current=True,
                download_status=DownloadStatus.FAILED,
            )
        )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_l_index_admet_versions_antigues_no_vigents(session, tender_document):
    for version, current in ((1, False), (2, False), (3, True)):
        session.add(
            TenderDocumentFile(
                tender_document_id=tender_document.id,
                version_number=version,
                is_current=current,
                download_status=DownloadStatus.DOWNLOADED,
            )
        )
    await session.commit()
    assert len(await current_files(session, tender_document.id)) == 1


async def test_una_descarrega_bona_despres_d_una_fallada_deixa_un_sol_vigent(
    session, tender_document
):
    """Abans la fallada (vigent perque no n'hi havia cap de bona) es quedava vigent."""
    failed = await process(session, tender_document, payload=b"", status=500)
    assert failed.file.download_status == DownloadStatus.FAILED
    assert failed.file.is_current is True

    ok = await process(session, tender_document)

    currents = await current_files(session, tender_document.id)
    assert [f.id for f in currents] == [ok.file.id]


async def test_dues_fallades_seguides_deixen_un_sol_vigent(session, tender_document):
    await process(session, tender_document, payload=b"", status=500)
    second = await process(session, tender_document, payload=b"", status=500)

    currents = await current_files(session, tender_document.id)
    assert [f.id for f in currents] == [second.file.id]


async def test_una_versio_nova_canvia_el_vigent_sense_trencar_l_index(session, tender_document):
    """A PostgreSQL l'ordre dels UPDATE importa: primer es destrona l'anterior."""
    first = await process(session, tender_document)
    second = await process(session, tender_document, payload=PCAP_V2, force_download=True)

    currents = await current_files(session, tender_document.id)
    assert [f.id for f in currents] == [second.file.id]
    assert second.file.id != first.file.id
