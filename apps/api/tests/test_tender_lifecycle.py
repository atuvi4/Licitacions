"""Cicle de vida d'una licitacio: que canvia, que deixa de valer i que no es toca.

Una licitacio no es una fotografia fixa: l'organ allarga terminis, substitueix
plecs, afegeix lots i de tant en tant anul.la l'expedient. El que aquests tests
sostenen no es la deteccio en si —aixo es pot mirar al modul— sino les dues
propietats de les quals depen que algu s'ho cregui:

**Que reingerir no faci soroll.** Si el mateix feed cent vegades genera cent
revisions i cent avisos, ningu mirara el cent-u, que sera el que importa. Per
aixo hi ha tants tests d'idempotencia com de deteccio.

**Que un canvi material no passi desapercebut.** Un termini que es mou o un plec
que se substitueix han de deixar caducada l'analisi i marcada la decisio humana,
sense esborrar-la: la decisio segueix dient qui va decidir que i sobre quin
estat. El que caduca es la seva vigencia, no el seu registre.

Cap crida a cap proveidor: la deteccio de canvis es purament determinista.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa

from app.ingestion.placsp.service import PlacspIngestionService
from app.matching.service import MatchingService
from app.models.compliance import CompanyComplianceRun
from app.models.cpv import CPVCode
from app.models.enums import (
    AnalysisRunStatus,
    ComplianceRunStatus,
    DownloadStatus,
    MembershipRole,
    SourceType,
    TenderDocumentType,
    TenderStatus,
    WorkspaceDecisionType,
)
from app.models.matching import CompanyTenderMatch
from app.models.profile import CompanyFinancialYear
from app.models.requirements import RequirementExtractionRun
from app.models.sources import Source
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import Tender, TenderCPV, TenderDocument, TenderLot, TenderLotCPV
from app.models.workspace import BidWorkspace
from app.services import bid_workspace as workspace_service
from app.services import tender_lifecycle as lifecycle
from app.services.compliance_analysis import analyze_company_compliance, is_run_stale
from app.services.submission_package import TenderNotActionableError, freeze_package
from app.services.tender_document_processing import process_tender_document
from app.services.tender_lifecycle import build_snapshot
from app.tenders.changes import (
    COMPARED_FIELDS,
    DOCUMENT_CONTRIBUTION_FIELDS,
    DOCUMENT_FIELDS,
    DOCUMENT_INTERNAL_FIELDS,
    DOCUMENT_PRESENCE_FIELDS,
    DOCUMENT_REVIEW_FIELDS,
    IMPACT_MATRIX,
    LOT_FIELDS,
    MANUAL_ONLY_FIELDS,
    SOURCE_PRESENCE_FIELDS,
    ChangeCode,
    ChangeImpact,
    canonical_instant,
    diff_snapshots,
    snapshot_hash,
    summarise,
)
from tests.fixtures.pdf_builder import build_pdf

FIXTURES = Path(__file__).parent / "fixtures" / "placsp"
FUTURE_DEADLINE = datetime.now(UTC) + timedelta(days=30)

PUBLIC_IP = "93.184.216.34"
PCAP = build_pdf(["Plec de clausules administratives particulars. " * 8] * 3)
PCAP_V2 = build_pdf(["Plec REVISAT de clausules administratives particulars. " * 8] * 4)


def resolver(host: str, port: int) -> list[str]:
    return [PUBLIC_IP]


def serving(payload: bytes, content_type: str = "application/pdf"):
    """Client fals que sempre respon amb aquests bytes."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-type": content_type})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


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
        title="Servei de neteja viaria",
        deadline_at=FUTURE_DEADLINE,
        estimated_value=Decimal("100000.00"),
    )
    db.add(tender)
    await db.commit()
    await db.refresh(tender)
    return tender


@pytest.fixture
async def extraction_run(db, tender) -> RequirementExtractionRun:
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
    await db.commit()
    await db.refresh(run)
    return run


async def revise(db, tender, **changes) -> lifecycle.RevisionOutcome:
    """Aplica un canvi a la licitacio i n'enregistra la revisio."""
    for field, value in changes.items():
        setattr(tender, field, value)
    outcome = await lifecycle.record_revision(db, tender)
    await db.commit()
    return outcome


async def events_of(db, tender) -> list[TenderChangeEvent]:
    return list(
        (
            await db.scalars(
                sa.select(TenderChangeEvent)
                .where(TenderChangeEvent.tender_id == tender.id)
                .order_by(TenderChangeEvent.change_code)
            )
        ).all()
    )


async def codes_of(db, tender) -> list[str]:
    return [event.change_code for event in await events_of(db, tender)]


async def count_revisions(db, tender) -> int:
    return (
        await db.execute(
            sa.select(sa.func.count())
            .select_from(TenderRevision)
            .where(TenderRevision.tender_id == tender.id)
        )
    ).scalar_one()


# --------------------------------------------------------------------------- #
# Deteccio pura: sense base de dades                                            #
# --------------------------------------------------------------------------- #


class TestDeteccioPura:
    def test_el_hash_no_depen_de_lordre_de_les_claus(self):
        a = {"title": "X", "deadline_at": "2026-09-20T00:00:00+00:00", "cpvs": []}
        b = {"cpvs": [], "deadline_at": "2026-09-20T00:00:00+00:00", "title": "X"}

        assert snapshot_hash(a) == snapshot_hash(b)

    async def test_tot_el_que_entra_al_hash_es_compara(self, db, tender):
        """Cap camp pot moure el hash sense generar un canvi que l'expliqui.

        Un camp que entra a la fotografia pero que ningu compara produiria una
        revisio nova amb zero events: el sistema diria "ha canviat alguna cosa"
        sense poder dir que. Aixo es exactament l'avis que la gent apren a
        ignorar, i el dia que canvies un plec ja no el miraria ningu.
        """
        snapshot = await build_snapshot(db, tender)

        assert set(snapshot) == COMPARED_FIELDS - MANUAL_ONLY_FIELDS - SOURCE_PRESENCE_FIELDS

        # Retirada de la font (lapida): la marca entra a la fotografia i es compara.
        tender.source_removed_at = datetime.now(UTC)
        removed = await build_snapshot(db, tender)

        assert set(removed) == COMPARED_FIELDS - MANUAL_ONLY_FIELDS
        assert removed["source_removed"] is True

    async def test_la_presencia_a_la_font_no_canvia_la_fotografia_de_les_que_ja_hi_ha(
        self, db, tender
    ):
        """Radar 3B-1 no pot fer semblar canviades les licitacions d'abans.

        `source_removed` i el `removed` dels documents nomes hi son quan valen
        `True`: una licitacio present amb documents presents dona exactament la
        mateixa fotografia que abans, i per tant cap revisio buida el dia del
        desplegament.
        """
        db.add(
            TenderDocument(
                tender_id=tender.id,
                url="https://x/pcap.pdf",
                name="PCAP",
                document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
            )
        )
        await db.commit()
        first = await lifecycle.record_revision(db, tender)

        snapshot = first.revision.normalized_snapshot
        assert "source_removed" not in snapshot
        assert "removed" not in snapshot["documents"][0]
        assert (await lifecycle.record_revision(db, tender)).unchanged is True

    def test_la_presencia_a_la_font_dona_els_dos_codis_informatius(self):
        removed = diff_snapshots({"title": "X"}, {"title": "X", "source_removed": True})
        restored = diff_snapshots({"title": "X", "source_removed": True}, {"title": "X"})

        assert [change.code for change in removed] == [ChangeCode.SOURCE_ENTRY_REMOVED]
        assert [change.code for change in restored] == [ChangeCode.SOURCE_ENTRY_RESTORED]
        for change in removed + restored:
            assert change.impacts == frozenset({ChangeImpact.INFORMATIONAL})
            assert change.is_material is False

    def test_un_document_marcat_com_a_retirat_dona_removed_i_added(self):
        present = {"url": "https://x/ppt.pdf", "name": "PPT", "sha256": None}
        gone = {**present, "removed": True}

        removed = diff_snapshots({"documents": [present]}, {"documents": [gone]})
        back = diff_snapshots({"documents": [gone]}, {"documents": [present]})
        still_gone = diff_snapshots(
            {"documents": [gone]}, {"documents": [{**gone, "name": "PPT v2"}]}
        )

        assert [change.code for change in removed] == [ChangeCode.DOCUMENT_REMOVED]
        assert [change.code for change in back] == [ChangeCode.DOCUMENT_ADDED]
        assert still_gone == []

    async def test_una_de_sindicada_no_canvia_gens_amb_els_camps_nous(self, db, tender):
        """Les que ja hi ha no poden semblar canviades perque hem ampliat res.

        `expediente_number` i `country_code` son a la fila d'aquesta licitacio,
        pero no a la seva fotografia. Si hi entressin, el dia que aquest codi
        arribes a produccio TOTES les licitacions sindicades donarien un hash
        diferent i es despertaria mig sistema per un canvi que no ha existit.
        """
        first = await lifecycle.record_revision(db, tender)
        second = await lifecycle.record_revision(db, tender)

        assert first.revision.revision_number == 1
        assert second.unchanged is True
        assert "expediente_number" not in first.revision.normalized_snapshot

    async def test_una_licitacio_oberta_a_ma_hi_aporta_dos_camps_mes(self, db, tenant_a):
        """La fotografia d'una manual ha de cobrir tot el que se'n pot corregir.

        Un camp editable que no hi entres no mouria el hash, i per tant no
        crearia revisio: dos clients que haguessin llegit la mateixa revisio
        podrien escriure'l tots dos sense que el segon s'assabentes que
        l'objecte ja havia canviat. El CAS protegeix la licitacio, no nomes
        els camps que publica un organ.
        """
        from app.services import manual_tender

        user, company = tenant_a
        tender, _created = await manual_tender.create_manual_tender(
            db,
            company_id=company.id,
            user=user,
            data=manual_tender.ManualTenderInput(
                client_token=uuid.uuid4(), title="Oberta a ma"
            ),
        )

        snapshot = await build_snapshot(db, tender)

        assert set(snapshot) == COMPARED_FIELDS - SOURCE_PRESENCE_FIELDS
        assert MANUAL_ONLY_FIELDS <= set(snapshot)

    async def test_tot_el_que_entra_al_hash_dun_lot_i_dun_document_es_compara(
        self, db, tender
    ):
        """El mateix, un nivell mes avall. Es on es va colar `currency`.

        Un lot i un document tambe aporten camps al hash, i un camp d'aquests
        que no es comparés donaria igualment una revisio que ningu podria
        explicar. La regla no canvia perque el camp estigui imbricat.
        """
        lot = TenderLot(
            tender_id=tender.id, external_lot_id="L1", title="Zona nord",
        )
        db.add(lot)
        db.add(
            TenderDocument(
                tender_id=tender.id,
                url="https://x/pcap.pdf",
                name="PCAP",
                document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
            )
        )
        db.add(
            TenderDocument(
                tender_id=tender.id,
                url="https://x/ppt.pdf",
                name="PPT",
                document_type=TenderDocumentType.TECHNICAL_TERMS,
                removed_at=datetime.now(UTC),
            )
        )
        await db.commit()
        pcap = await db.scalar(
            sa.select(TenderDocument).where(TenderDocument.url == "https://x/pcap.pdf")
        )
        # Alies (3B-2): mateixos bytes que el PCAP. Tambe aporta un camp al hash.
        db.add(
            TenderDocument(
                tender_id=tender.id,
                url="https://x/annex.pdf",
                name="PCAP (copia)",
                document_type=TenderDocumentType.ADDITIONAL,
                duplicate_of_document_id=pcap.id,
            )
        )
        # Aportacio de l'operador pendent de verificar (3B-2 §2.2): marca `contribution`.
        from app.models.enums import DocumentContributionStatus, TenderDocumentOrigin

        db.add(
            TenderDocument(
                tender_id=tender.id,
                url="https://x/zz-pendent.pdf",
                name="Annex aportat",
                document_type=TenderDocumentType.ADDITIONAL,
                origin=TenderDocumentOrigin.OPERATOR_URL,
                contribution_status=DocumentContributionStatus.PENDING_VERIFICATION,
            )
        )
        await db.commit()

        snapshot = await build_snapshot(db, tender)

        assert set(snapshot["lots"][0]) == LOT_FIELDS
        alias, present, removed, pending = snapshot["documents"]
        # `reviewed_role` (0059) nomes hi es amb una revisio de l'operador per a la
        # versio vigent: cap d'aquests documents en te (vegeu
        # `test_el_rol_revisat_entra_a_la_fotografia_nomes_si_n_hi_ha`).
        outside = DOCUMENT_INTERNAL_FIELDS | DOCUMENT_CONTRIBUTION_FIELDS | DOCUMENT_REVIEW_FIELDS
        shown = DOCUMENT_FIELDS - DOCUMENT_PRESENCE_FIELDS
        assert set(present) == shown - outside
        assert set(removed) == DOCUMENT_FIELDS - outside
        assert set(alias) == shown - DOCUMENT_CONTRIBUTION_FIELDS - DOCUMENT_REVIEW_FIELDS
        assert set(pending) == shown - DOCUMENT_INTERNAL_FIELDS - DOCUMENT_REVIEW_FIELDS
        assert removed["removed"] is True
        assert alias["duplicate_of"] == "https://x/pcap.pdf"
        assert pending["contribution"] == "pending_verification"

        # I cada camp que hi entra, si canvia, s'explica amb algun canvi.
        base = {"documents": [alias]}
        for name in sorted(DOCUMENT_FIELDS - {"url"}):
            other = dict(alias)
            if name == "removed":
                other[name] = True
            else:
                other[name] = None if alias.get(name) is not None else "valor-nou"
            assert diff_snapshots(base, {"documents": [other]}), name

    def test_dos_camps_amb_el_mateix_codi_es_distingeixen_pel_subjecte(self):
        """Un pressupost amb IVA i un sense son el mateix fet, camps diferents."""
        changes = diff_snapshots(
            {"base_budget": "100", "base_budget_without_tax": "80"},
            {"base_budget": "110", "base_budget_without_tax": "90"},
        )

        assert [change.code for change in changes] == [
            ChangeCode.BASE_BUDGET_CHANGED,
            ChangeCode.BASE_BUDGET_CHANGED,
        ]
        assert sorted(change.subject for change in changes) == [
            "base_budget",
            "base_budget_without_tax",
        ]

    def test_tot_codi_te_un_impacte_escrit(self):
        """La matriu es explicita: cap codi pot quedar sense classificar."""
        assert {code for code in ChangeCode} == set(IMPACT_MATRIX)

    def test_passar_a_alies_o_deixar_de_ser_ho_es_informatiu(self):
        """Mateix contingut, un altre document el representa: res a reobrir."""
        doc = {"url": "https://x/copia.pdf", "name": "C", "sha256": "s"}
        alias = {**doc, "duplicate_of": "https://x/pcap.pdf"}

        for before, after, old, new in (
            (doc, alias, None, "https://x/pcap.pdf"),
            (alias, doc, "https://x/pcap.pdf", None),
        ):
            changes = diff_snapshots({"documents": [before]}, {"documents": [after]})
            assert [c.code for c in changes] == [ChangeCode.DOCUMENT_ALIAS_CHANGED]
            assert (changes[0].old_value, changes[0].new_value) == (old, new)
            assert changes[0].subject == "https://x/copia.pdf"
            assert changes[0].impacts == frozenset({ChangeImpact.INFORMATIONAL})
            assert changes[0].is_material is False

    def test_una_aportacio_pendent_nomes_es_material_quan_es_verifica(self):
        """3B-2 §2.2: arribar pendent es informatiu; verificar dona `DOCUMENT_ADDED`."""
        url = "https://contractaciopublica.cat/annex.pdf"
        verified = {"url": url, "name": "Annex", "sha256": "s"}
        pending = {**verified, "contribution": "pending_verification"}
        rejected = {**verified, "contribution": "rejected"}

        def codes(before, after):
            return sorted(
                c.code.value
                for c in diff_snapshots({"documents": before}, {"documents": after})
            )

        arrival = diff_snapshots({"documents": []}, {"documents": [pending]})
        assert [c.code for c in arrival] == [ChangeCode.DOCUMENT_CONTRIBUTION_CHANGED]
        assert (arrival[0].old_value, arrival[0].new_value) == (None, "pending_verification")
        assert not any(c.is_material for c in arrival)

        assert codes([pending], [verified]) == [
            "DOCUMENT_ADDED",
            "DOCUMENT_CONTRIBUTION_CHANGED",
        ]
        assert codes([pending], [rejected]) == ["DOCUMENT_CONTRIBUTION_CHANGED"]
        assert codes([pending], [{**pending, "removed": True}]) == [
            "DOCUMENT_CONTRIBUTION_CHANGED"
        ]
        # Rebutjar-ne un de verificat el treu del material: es una retirada.
        assert codes([verified], [rejected]) == [
            "DOCUMENT_CONTRIBUTION_CHANGED",
            "DOCUMENT_REMOVED",
        ]
        assert codes([pending], [pending]) == []

    def test_el_feed_que_canvia_el_tipus_d_un_document_revisat_ho_diu(self):
        """3B-2 §3.2: el feed mai sobreescriu en silenci un rol revisat (informatiu)."""
        url = "https://x/ppt.pdf"
        reviewed = {
            "url": url,
            "name": "PPT",
            "sha256": "s",
            "document_type": "technical_terms",
            "reviewed_role": "characteristics_table",
        }
        retyped = {**reviewed, "document_type": "additional"}

        changes = diff_snapshots({"documents": [reviewed]}, {"documents": [retyped]})
        by_code = {c.code: c for c in changes}
        assert set(by_code) == {
            ChangeCode.DOCUMENT_METADATA_CHANGED,
            ChangeCode.SOURCE_TYPE_CHANGED_AFTER_REVIEW,
        }
        event = by_code[ChangeCode.SOURCE_TYPE_CHANGED_AFTER_REVIEW]
        assert (event.old_value, event.new_value, event.subject) == (
            "technical_terms",
            "additional",
            url,
        )
        assert event.impacts == frozenset({ChangeImpact.INFORMATIONAL})
        assert not any(c.is_material for c in changes)

        # Sense revisio, el mateix canvi es nomes metadada.
        plain = {k: v for k, v in reviewed.items() if k != "reviewed_role"}
        changes = diff_snapshots(
            {"documents": [plain]}, {"documents": [{**plain, "document_type": "additional"}]}
        )
        assert [c.code for c in changes] == [ChangeCode.DOCUMENT_METADATA_CHANGED]

    def test_revisar_el_rol_es_informatiu(self):
        doc = {"url": "https://x/pcap.pdf", "name": "PCAP", "sha256": "s"}
        reviewed = {**doc, "reviewed_role": "pcap"}
        for before, after, old, new in (
            (doc, reviewed, None, "pcap"),
            (reviewed, {**doc, "reviewed_role": "notice"}, "pcap", "notice"),
            (reviewed, doc, "pcap", None),
        ):
            changes = diff_snapshots({"documents": [before]}, {"documents": [after]})
            assert [c.code for c in changes] == [ChangeCode.DOCUMENT_ROLE_REVIEWED]
            assert (changes[0].old_value, changes[0].new_value) == (old, new)
            assert changes[0].is_material is False

    async def test_el_rol_revisat_entra_a_la_fotografia_nomes_si_n_hi_ha(self, db, tender):
        """Nomes amb una revisio per a la versio VIGENT: les fotografies d'abans no canvien."""
        from app.models.global_classification import DocumentRoleReview
        from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
        from tests.test_platform_onboarding import create_operator

        operator = await create_operator(db)
        document = TenderDocument(
            tender_id=tender.id,
            url="https://x/quadre.pdf",
            name="Quadre",
            document_type=TenderDocumentType.ADDITIONAL,
        )
        content = TenderDocumentContent(
            sha256="e" * 64,
            storage_key="tests/quadre.pdf",
            file_size=10,
            mime_type="application/pdf",
            is_text_extractable=True,
        )
        db.add_all([document, content])
        await db.flush()
        db.add(
            TenderDocumentFile(
                tender_document_id=document.id,
                content_id=content.id,
                is_current=True,
                download_status=DownloadStatus.DOWNLOADED,
            )
        )
        await db.commit()
        before = await build_snapshot(db, tender)
        assert "reviewed_role" not in before["documents"][0]

        # Una revisio d'una altra versio no hi entra.
        db.add(
            DocumentRoleReview(
                tender_document_id=document.id,
                content_sha256="f" * 64,
                role="pcap",
                authority="platform_operator",
                actor_id=operator.id,
                reason="Versio antiga",
            )
        )
        await db.commit()
        assert (await build_snapshot(db, tender)) == before

        db.add(
            DocumentRoleReview(
                tender_document_id=document.id,
                content_sha256="e" * 64,
                role="characteristics_table",
                authority="platform_operator",
                actor_id=operator.id,
                reason="Es el quadre",
            )
        )
        await db.commit()
        entry = (await build_snapshot(db, tender))["documents"][0]
        assert entry["reviewed_role"] == "characteristics_table"

    def test_dos_estats_iguals_no_donen_cap_canvi(self):
        snapshot = {"title": "X", "documents": [], "cpvs": [], "lots": []}

        assert diff_snapshots(snapshot, dict(snapshot)) == []

    def test_un_canvi_de_titol_no_invalida_res(self):
        changes = diff_snapshots({"title": "A"}, {"title": "B"})

        assert [change.code for change in changes] == [ChangeCode.TITLE_CHANGED]
        assert changes[0].is_material is False
        assert summarise(changes).is_material is False

    def test_mateixa_url_i_sha_nou_es_una_substitucio_de_plec(self):
        """El cas que justifica tot aixo: el plec canvia sense canviar d'adreça."""
        before = {"documents": [{"url": "https://x/plec.pdf", "sha256": "aa"}]}
        after = {"documents": [{"url": "https://x/plec.pdf", "sha256": "bb"}]}

        changes = diff_snapshots(before, after)

        assert [change.code for change in changes] == [ChangeCode.DOCUMENT_REPLACED]
        assert ChangeImpact.REQUIREMENTS_AFFECTING in changes[0].impacts
        assert changes[0].is_material is True

    def test_url_nova_amb_el_mateix_contingut_no_desperta_ningu(self):
        """Migrar l'allotjament d'un plec no es publicar-ne un de nou."""
        before = {"documents": [{"url": "https://vell/plec.pdf", "sha256": "aa"}]}
        after = {"documents": [{"url": "https://nou/plec.pdf", "sha256": "aa"}]}

        changes = diff_snapshots(before, after)

        assert [change.code for change in changes] == [ChangeCode.DOCUMENT_URL_CHANGED]
        assert changes[0].is_material is False

    def test_url_nova_amb_contingut_nou_es_un_document_afegit(self):
        before = {"documents": [{"url": "https://x/a.pdf", "sha256": "aa"}]}
        after = {
            "documents": [
                {"url": "https://x/a.pdf", "sha256": "aa"},
                {"url": "https://x/b.pdf", "sha256": "bb"},
            ]
        }

        changes = diff_snapshots(before, after)

        assert [change.code for change in changes] == [ChangeCode.DOCUMENT_ADDED]

    def test_un_document_sense_sha_no_sinventa_una_identitat(self):
        """Encara no s'ha baixat: no es pot dir si ha canviat, i no es diu."""
        before = {"documents": [{"url": "https://x/a.pdf", "sha256": None}]}
        after = {"documents": [{"url": "https://x/a.pdf", "sha256": None}]}

        assert diff_snapshots(before, after) == []


# --------------------------------------------------------------------------- #
# Revisions: idempotencia i deteccio contra la base de dades                    #
# --------------------------------------------------------------------------- #


class TestRevisions:
    async def test_enregistrar_dues_vegades_el_mateix_estat_no_crea_res(self, db, tender):
        first = await lifecycle.record_revision(db, tender)
        await db.commit()
        second = await lifecycle.record_revision(db, tender)
        await db.commit()

        assert first.unchanged is False
        assert second.unchanged is True
        assert second.revision.id == first.revision.id
        assert await count_revisions(db, tender) == 1
        assert await events_of(db, tender) == []

    async def test_la_primera_revisio_no_inventa_canvis(self, db, tender):
        """No hi ha res abans: dir que "ha canviat el titol" seria fals."""
        outcome = await lifecycle.record_revision(db, tender)
        await db.commit()

        assert outcome.revision.revision_number == 1
        assert outcome.revision.change_summary is None
        assert await events_of(db, tender) == []

    async def test_un_canvi_de_termini_crea_revisio_i_event(self, db, tender):
        await lifecycle.record_revision(db, tender)
        await db.commit()

        nou = FUTURE_DEADLINE + timedelta(days=7)
        outcome = await revise(db, tender, deadline_at=nou)

        assert outcome.unchanged is False
        assert outcome.revision.revision_number == 2
        assert await codes_of(db, tender) == [ChangeCode.DEADLINE_CHANGED.value]

        event = (await events_of(db, tender))[0]
        assert event.is_material is True
        assert ChangeImpact.WORKSPACE_CRITICAL.value in event.impacts
        assert event.new_value == nou.isoformat()

    async def test_un_import_reescalat_no_es_un_canvi_de_pressupost(self, db, tender):
        """`100000` i `100000.00` son el mateix import."""
        await lifecycle.record_revision(db, tender)
        await db.commit()

        outcome = await revise(db, tender, estimated_value=Decimal("100000.0000"))

        assert outcome.unchanged is True
        assert await count_revisions(db, tender) == 1

    async def test_un_canvi_de_cpv_afecta_el_prefit_i_no_els_requisits(self, db, tender):
        await lifecycle.record_revision(db, tender)
        await db.commit()
        db.add(CPVCode(code="90610000", description="Neteja de carrers"))
        await db.flush()
        db.add(TenderCPV(tender_id=tender.id, cpv_code="90610000", is_main=True))
        await db.commit()

        await lifecycle.record_revision(db, tender)
        await db.commit()

        event = (await events_of(db, tender))[0]
        assert event.change_code == ChangeCode.CPV_ADDED.value
        assert ChangeImpact.PREFIT_AFFECTING.value in event.impacts
        assert ChangeImpact.REQUIREMENTS_AFFECTING.value not in event.impacts

    async def test_canviar_nomes_metadata_no_invalida_els_requisits(self, db, tender):
        await lifecycle.record_revision(db, tender)
        await db.commit()

        await revise(db, tender, title="Servei de neteja viaria (correccio d'errades)")

        events = await events_of(db, tender)
        assert [event.impacts for event in events] == [[ChangeImpact.INFORMATIONAL.value]]

    async def test_els_events_es_desen_amb_els_impactes_del_moment(self, db, tender):
        """No es tornen a deduir: la matriu pot canviar i la historia no."""
        await lifecycle.record_revision(db, tender)
        await db.commit()
        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=3))

        event = (await events_of(db, tender))[0]
        assert sorted(event.impacts) == sorted(
            [
                ChangeImpact.PREFIT_AFFECTING.value,
                ChangeImpact.COMPLIANCE_AFFECTING.value,
                ChangeImpact.WORKSPACE_CRITICAL.value,
            ]
        )

    async def test_el_registre_no_afirma_res_quan_no_sap_de_quin_estat_es_parteix(
        self, db, tender
    ):
        """Sense base no hi ha resposta, i inventar-se'n una seria pitjor."""
        await lifecycle.record_revision(db, tender)
        await db.commit()
        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=3))

        log = await lifecycle.load_change_log(db, tender.id)

        assert log.since(None) == ()
        assert log.impacts_since(None) == frozenset()
        # Pero es diu clarament que no se sap, que no es el mateix que "res".
        assert log.basis_unknown(None) is True

    async def test_una_licitacio_anul_lada_deixa_de_ser_accionable(self, db, tender):
        await lifecycle.record_revision(db, tender)
        await db.commit()

        await revise(db, tender, status=TenderStatus.CANCELLED)

        assert lifecycle.is_actionable(tender) is False
        codes = await codes_of(db, tender)
        assert ChangeCode.TENDER_CANCELLED.value in codes
        event = next(
            e for e in await events_of(db, tender) if e.change_code == ChangeCode.TENDER_CANCELLED
        )
        assert event.is_material is True


# --------------------------------------------------------------------------- #
# Ingestio real: el feed de PLACSP                                              #
# --------------------------------------------------------------------------- #


class TestIngestioIncremental:
    async def test_reingerir_el_mateix_feed_no_crea_revisions_noves(self, db, storage):
        service = PlacspIngestionService(db, storage)
        await service.run(fixture=FIXTURES / "feed_sample.atom")
        after_first = (
            await db.execute(sa.select(sa.func.count()).select_from(TenderRevision))
        ).scalar_one()

        await service.run(fixture=FIXTURES / "feed_sample.atom")
        after_second = (
            await db.execute(sa.select(sa.func.count()).select_from(TenderRevision))
        ).scalar_one()

        assert after_first == 3  # una per licitacio
        assert after_second == after_first
        assert (
            await db.execute(sa.select(sa.func.count()).select_from(TenderChangeEvent))
        ).scalar_one() == 0

    async def test_reingerir_el_mateix_feed_no_mou_last_changed_at(self, db, storage):
        """La marca que el pre-fit consulta per saber si s'ha de recalcular.

        S'escrivia a cada passada del feed hagues canviat res o no, i per tant
        TOTS els pre-fits semblaven obsolets cada dia. Un avis que salta sempre
        no informa de res, i el dia que canvia el termini de debo es confon amb
        el soroll de la vigilia.
        """
        service = PlacspIngestionService(db, storage)
        await service.run(fixture=FIXTURES / "feed_sample.atom")
        before = sorted(
            (t.external_id, t.last_changed_at) for t in (await db.scalars(sa.select(Tender))).all()
        )

        await service.run(fixture=FIXTURES / "feed_sample.atom")

        after = sorted(
            (t.external_id, t.last_changed_at) for t in (await db.scalars(sa.select(Tender))).all()
        )
        assert after == before

    async def test_un_canvi_real_si_que_mou_last_changed_at(self, db, storage):
        service = PlacspIngestionService(db, storage)
        await service.run(fixture=FIXTURES / "feed_sample.atom")
        tender = (
            await db.execute(
                sa.select(Tender).where(Tender.expediente_number == "AMARCO/2026/MUSEO/607BAS")
            )
        ).scalar_one()
        before = tender.last_changed_at

        await service.run(fixture=FIXTURES / "feed_sample_modified.atom")

        await db.refresh(tender)
        assert tender.last_changed_at > before

    async def test_un_canvi_real_del_feed_queda_registrat_amb_el_seu_impacte(self, db, storage):
        service = PlacspIngestionService(db, storage)
        await service.run(fixture=FIXTURES / "feed_sample.atom")
        await service.run(fixture=FIXTURES / "feed_sample_modified.atom")

        tender = (
            await db.execute(
                sa.select(Tender).where(Tender.expediente_number == "AMARCO/2026/MUSEO/607BAS")
            )
        ).scalar_one()

        revisions = list(
            (
                await db.scalars(
                    sa.select(TenderRevision)
                    .where(TenderRevision.tender_id == tender.id)
                    .order_by(TenderRevision.revision_number)
                )
            ).all()
        )
        assert [r.revision_number for r in revisions] == [1, 2]
        assert revisions[0].normalized_hash != revisions[1].normalized_hash
        assert revisions[1].source_record_id is not None

        codes = await codes_of(db, tender)
        assert ChangeCode.DEADLINE_CHANGED.value in codes

        # Les altres dues licitacions del feed no s'han despertat.
        others = (
            await db.execute(
                sa.select(sa.func.count())
                .select_from(TenderChangeEvent)
                .where(TenderChangeEvent.tender_id != tender.id)
            )
        ).scalar_one()
        assert others == 0


# --------------------------------------------------------------------------- #
# Workspace: la decisio humana no es toca, la seva vigencia si                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def workspace_setup(db, tenant_a, tender):
    """Un workspace obert sobre una licitacio que ja te revisio enregistrada."""
    owner, company = tenant_a
    await lifecycle.record_revision(db, tender)
    await db.commit()
    outcome = await workspace_service.create_workspace(
        db, company_id=company.id, tender=tender, user_id=owner.id
    )
    await db.commit()
    return company, owner, outcome.workspace


class TestWorkspaceDavantUnCanvi:
    async def test_la_decisio_recorda_sobre_quin_estat_es_va_prendre(
        self, db, workspace_setup, tender
    ):
        _, owner, workspace = workspace_setup
        revision = await lifecycle.latest_revision(db, tender.id)

        decision = await workspace_service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=owner,
            role=MembershipRole.OWNER,
            reason="Encaixa amb la nostra solvencia.",
        )
        await db.commit()

        assert decision.tender_revision_id == revision.id

    async def test_un_canvi_material_marca_la_decisio_com_a_caducada(
        self, db, workspace_setup, tender
    ):
        _, owner, workspace = workspace_setup
        await workspace_service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=owner,
            role=MembershipRole.OWNER,
            reason="Encaixa amb la nostra solvencia.",
        )
        await db.commit()

        state = await workspace_service.load_state(db, workspace)
        assert state.decision_outdated is False

        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=7))

        state = await workspace_service.load_state(db, workspace)
        assert state.decision_outdated is True
        assert [e.change_code for e in state.changes_since_decision] == [
            ChangeCode.DEADLINE_CHANGED.value
        ]

    async def test_la_decisio_no_sesborra_ni_es_reescriu(self, db, workspace_setup, tender):
        """El que caduca es la vigencia, no el registre de qui va decidir que."""
        _, owner, workspace = workspace_setup
        decision = await workspace_service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=owner,
            role=MembershipRole.OWNER,
            reason="Encaixa amb la nostra solvencia.",
        )
        await db.commit()
        decision_id, decided_by, decided_type = (
            decision.id,
            decision.decided_by,
            decision.decision,
        )

        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=7))

        state = await workspace_service.load_state(db, workspace)
        assert state.latest_decision is not None
        assert state.latest_decision.id == decision_id
        assert state.latest_decision.decided_by == decided_by
        assert state.latest_decision.decision is decided_type

    async def test_un_canvi_informatiu_no_caduca_la_decisio(self, db, workspace_setup, tender):
        """Si un canvi de titol gastes l'avis, ningu el miraria el dia del plec."""
        _, owner, workspace = workspace_setup
        await workspace_service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=owner,
            role=MembershipRole.OWNER,
            reason="Encaixa amb la nostra solvencia.",
        )
        await db.commit()

        await revise(db, tender, title="Servei de neteja viaria (correccio d'errades)")

        state = await workspace_service.load_state(db, workspace)
        assert state.decision_outdated is False
        assert state.changes_since_decision == ()

    async def test_una_decisio_antiga_sense_revisio_mai_es_dona_per_vigent(
        self, db, workspace_setup, tender
    ):
        """Les decisions d'abans d'aquesta fase no tenen revisio: no ho sabem.

        I "no ho sabem" **no pot voler dir "vigent"**. Es l'unica decisio que no
        es pot recalcular sola: la va prendre una persona, i si no sabem sobre
        quin estat la va prendre, l'ha de tornar a mirar una persona. La decisio
        no es toca ni es torna a signar; nomes deixa de comptar com a vigent.
        """
        _, owner, workspace = workspace_setup
        decision = await workspace_service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=owner,
            role=MembershipRole.OWNER,
            reason="Encaixa amb la nostra solvencia.",
        )
        signed_at, signed_by = decision.decided_at, decision.decided_by
        decision.tender_revision_id = None
        await db.commit()

        state = await workspace_service.load_state(db, workspace)

        assert state.decision_basis_unknown is True
        assert state.decision_outdated is True
        assert state.decision_reasons == ("revision_basis_unknown",)
        # No s'inventa cap canvi: no n'hi ha cap de conegut, nomes falta la base.
        assert state.changes_since_decision == ()
        # I la decisio segueix intacta: qui, quan i que, exactament igual.
        await db.refresh(decision)
        assert (decision.decided_at, decision.decided_by) == (signed_at, signed_by)
        assert decision.decision == WorkspaceDecisionType.GO

    async def test_una_licitacio_anul_lada_deixa_el_workspace_desactualitzat(
        self, db, workspace_setup, tender
    ):
        _, _, workspace = workspace_setup

        await revise(db, tender, status=TenderStatus.CANCELLED)

        state = await workspace_service.load_state(db, workspace)
        assert state.tender_actionable is False
        assert "tender_not_actionable" in state.outdated_reasons

    async def test_un_canvi_de_termini_recalcula_els_dies_que_queden(
        self, db, workspace_setup, tender
    ):
        _, _, workspace = workspace_setup
        before = (await workspace_service.load_state(db, workspace)).days_remaining

        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=10))

        after = (await workspace_service.load_state(db, workspace)).days_remaining
        assert before is not None and after is not None
        assert after == before + 10


# --------------------------------------------------------------------------- #
# Documents: la identitat es el contingut, no l'adreca                          #
# --------------------------------------------------------------------------- #


async def add_document(db, tender, *, url: str, sha: str | None) -> TenderDocument:
    """Un document amb, opcionalment, una versio baixada i el seu SHA."""
    document = TenderDocument(
        tender_id=tender.id,
        name=url.rsplit("/", 1)[-1],
        document_type=TenderDocumentType.OTHER,
        url=url,
    )
    db.add(document)
    await db.flush()
    if sha is not None:
        await attach_content(db, document, sha=sha)
    await db.commit()
    return document


async def attach_content(db, document, *, sha: str) -> None:
    """Substitueix la versio vigent del document per una amb un SHA nou.

    Els blobs es dedupliquen per SHA, com a produccio: el mateix contingut
    servit dues vegades reaprofita la fila i no en crea una de nova.
    """
    content = await db.scalar(
        sa.select(TenderDocumentContent).where(TenderDocumentContent.sha256 == sha)
    )
    if content is None:
        content = TenderDocumentContent(
            sha256=sha,
            storage_key=f"documents/{sha}.pdf",
            file_size=1024,
            mime_type="application/pdf",
            is_text_extractable=True,
        )
        db.add(content)
        await db.flush()
    previous = (
        await db.scalars(
            sa.select(TenderDocumentFile).where(
                TenderDocumentFile.tender_document_id == document.id
            )
        )
    ).all()
    for file in previous:
        file.is_current = False
    db.add(
        TenderDocumentFile(
            tender_document_id=document.id,
            content_id=content.id,
            source_url=document.url,
            version_number=len(previous) + 1,
            is_current=True,
            download_status=DownloadStatus.DOWNLOADED,
            downloaded_at=datetime.now(UTC),
        )
    )
    await db.flush()


class TestIdentitatDeDocuments:
    async def test_el_mateix_plec_servit_de_nou_no_es_un_canvi(self, db, tender):
        """Rebaixar el mateix fitxer no ha d'invalidar cap analisi."""
        document = await add_document(db, tender, url="https://x/pcap.pdf", sha="a" * 64)
        await lifecycle.record_revision(db, tender)
        await db.commit()

        await attach_content(db, document, sha="a" * 64)
        await db.commit()
        outcome = await lifecycle.record_revision(db, tender)
        await db.commit()

        assert outcome.unchanged is True
        assert await count_revisions(db, tender) == 1

    async def test_la_mateixa_url_amb_un_sha_nou_invalida_els_requisits(self, db, tender):
        """El cas real: l'organ substitueix el PCAP sense canviar l'enllac."""
        document = await add_document(db, tender, url="https://x/pcap.pdf", sha="a" * 64)
        await lifecycle.record_revision(db, tender)
        await db.commit()

        await attach_content(db, document, sha="b" * 64)
        await db.commit()
        outcome = await lifecycle.record_revision(db, tender)
        await db.commit()

        assert outcome.unchanged is False
        assert await codes_of(db, tender) == [ChangeCode.DOCUMENT_REPLACED.value]
        assert ChangeImpact.REQUIREMENTS_AFFECTING in outcome.summary.impacts


class TestSubstitucioDePlecEndAEnd:
    """El camí real: es baixa el plec, canvia, i el cicle de vida se n'assabenta.

    La deteccio viu al processat de documents i no a la ingestió perque el SHA
    d'un plec nomes es coneix despres de baixar-lo. Si aquest punt desapareix,
    substituir un plec sota la mateixa URL torna a ser invisible, que es
    exactament el que aquesta fase existeix per evitar.
    """

    async def test_baixar_un_plec_nou_sota_la_mateixa_url_genera_revisio_i_event(
        self, session, tender_document
    ):
        async with serving(PCAP) as client:
            await process_tender_document(
                session, tender_document, client=client, resolver=resolver
            )
        tender = await session.get(Tender, tender_document.tender_id)
        assert await count_revisions(session, tender) == 1

        async with serving(PCAP_V2) as client:
            await process_tender_document(
                session,
                tender_document,
                client=client,
                resolver=resolver,
                force_download=True,
            )

        assert await count_revisions(session, tender) == 2
        assert await codes_of(session, tender) == [ChangeCode.DOCUMENT_REPLACED.value]
        revision = await lifecycle.latest_revision(session, tender.id)
        assert revision.trigger == "document_processing"

    async def test_rebaixar_el_mateix_plec_no_genera_res(self, session, tender_document):
        async with serving(PCAP) as client:
            await process_tender_document(
                session, tender_document, client=client, resolver=resolver
            )
        tender = await session.get(Tender, tender_document.tender_id)

        async with serving(PCAP) as client:
            await process_tender_document(
                session,
                tender_document,
                client=client,
                resolver=resolver,
                force_download=True,
            )

        assert await count_revisions(session, tender) == 1
        assert await events_of(session, tender) == []


# --------------------------------------------------------------------------- #
# El que NO ha de passar: res del que ja esta escrit es toca                    #
# --------------------------------------------------------------------------- #


class TestNoEsToquenElsRegistresAnteriors:
    async def test_un_canvi_no_esborra_ni_modifica_cap_run_de_requisits(
        self, db, tender, extraction_run
    ):
        """La invalidacio es derivada: es compara, no s'esborra."""
        before = (extraction_run.id, extraction_run.is_current, extraction_run.input_fingerprint)
        await lifecycle.record_revision(db, tender)
        await db.commit()

        document = await add_document(db, tender, url="https://x/pcap.pdf", sha="a" * 64)
        await attach_content(db, document, sha="b" * 64)
        await db.commit()
        await lifecycle.record_revision(db, tender)
        await db.commit()

        await db.refresh(extraction_run)
        assert (
            extraction_run.id,
            extraction_run.is_current,
            extraction_run.input_fingerprint,
        ) == before

    async def test_les_revisions_anteriors_no_es_reescriuen(self, db, tender):
        await lifecycle.record_revision(db, tender)
        await db.commit()
        first = await lifecycle.latest_revision(db, tender.id)
        snapshot_before, hash_before = dict(first.normalized_snapshot), first.normalized_hash

        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=7))

        await db.refresh(first)
        assert first.normalized_hash == hash_before
        assert first.normalized_snapshot == snapshot_before

    async def test_tornar_a_enregistrar_despres_dun_canvi_no_duplica_els_events(self, db, tender):
        await lifecycle.record_revision(db, tender)
        await db.commit()
        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=7))

        for _ in range(3):
            await lifecycle.record_revision(db, tender)
            await db.commit()

        assert await count_revisions(db, tender) == 2
        assert await codes_of(db, tender) == [ChangeCode.DEADLINE_CHANGED.value]


class TestFronteraEntreElPlecILEmpresa:
    """Canviar l'empresa i canviar la licitacio no invaliden el mateix.

    Es la frontera que separa "que demana el plec" de "que podem acreditar". Si
    es confonguessin, actualitzar una polissa obligaria a reanalitzar el plec, i
    ningu tornaria a actualitzar cap polissa.
    """

    async def test_un_canvi_de_lempresa_no_toca_lanalisi_del_plec(
        self, db, tenant_a, tender, extraction_run
    ):
        owner, company = tenant_a
        run = await analyze_company_compliance(db, company, tender, force=True)
        await db.commit()

        db.add(
            CompanyFinancialYear(
                company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
            )
        )
        await db.commit()

        # El compliment queda endarrerit...
        assert await is_run_stale(db, run.run, company) is True
        # ...i el run de requisits segueix sent el vigent, intacte.
        await db.refresh(extraction_run)
        assert extraction_run.is_current is True

    async def test_un_canvi_de_lempresa_no_genera_cap_revisio_de_la_licitacio(
        self, db, tenant_a, tender
    ):
        """La historia de l'expedient no es mou perque es mogui una empresa."""
        _, company = tenant_a
        await lifecycle.record_revision(db, tender)
        await db.commit()

        db.add(
            CompanyFinancialYear(
                company_id=company.id, year=2025, revenue=Decimal("2450000"), currency="EUR"
            )
        )
        await db.commit()
        await lifecycle.record_revision(db, tender)
        await db.commit()

        assert await count_revisions(db, tender) == 1


class TestPaquetDevantUnCanvi:
    async def test_no_es_pot_congelar_un_paquet_duna_licitacio_anul_lada(
        self, db, workspace_setup, tender
    ):
        """Preparar una oferta per a un expedient que ja no existeix."""
        _, owner, workspace = workspace_setup
        await revise(db, tender, status=TenderStatus.CANCELLED)

        with pytest.raises(TenderNotActionableError) as excinfo:
            await freeze_package(db, workspace=workspace, user=owner, role=MembershipRole.OWNER)

        assert excinfo.value.code == "tender_not_actionable"


# --------------------------------------------------------------------------- #
# Aillament multi-tenant                                                        #
# --------------------------------------------------------------------------- #


class TestAillament:
    async def test_el_canvi_afecta_cada_empresa_pel_seu_compte(
        self, db, tenant_a, tenant_b, tender
    ):
        """La historia de l'expedient es una; la caducitat de la decisio, de cadascu."""
        owner_a, company_a = tenant_a
        owner_b, company_b = tenant_b
        await lifecycle.record_revision(db, tender)
        await db.commit()

        ws_a = (
            await workspace_service.create_workspace(
                db, company_id=company_a.id, tender=tender, user_id=owner_a.id
            )
        ).workspace
        await workspace_service.record_decision(
            db,
            ws_a,
            decision=WorkspaceDecisionType.GO,
            user=owner_a,
            role=MembershipRole.OWNER,
            reason="Encaixa amb la nostra solvencia.",
        )
        await db.commit()

        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=7))

        # B decideix DESPRES del canvi: per a B no hi ha res caducat.
        ws_b = (
            await workspace_service.create_workspace(
                db, company_id=company_b.id, tender=tender, user_id=owner_b.id
            )
        ).workspace
        await workspace_service.record_decision(
            db,
            ws_b,
            decision=WorkspaceDecisionType.GO,
            user=owner_b,
            role=MembershipRole.OWNER,
            reason="Encaixa amb la nostra solvencia.",
        )
        await db.commit()

        state_a = await workspace_service.load_state(db, ws_a)
        state_b = await workspace_service.load_state(db, ws_b)

        assert state_a.decision_outdated is True
        assert state_b.decision_outdated is False

    async def test_les_revisions_no_porten_company_id(self):
        """La historia d'un expedient es publica, com l'expedient."""
        columns = set(TenderRevision.__table__.columns.keys())
        assert "company_id" not in columns
        assert "company_id" not in set(TenderChangeEvent.__table__.columns.keys())


# --------------------------------------------------------------------------- #
# API                                                                           #
# --------------------------------------------------------------------------- #


class TestApi:
    async def test_historial_i_canvis_son_llegibles(self, client_a, db, tender):
        await lifecycle.record_revision(db, tender)
        await db.commit()
        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=7))

        history = await client_a.get(f"/api/v1/tenders/{tender.id}/history")
        assert history.status_code == 200
        body = history.json()
        assert [item["revision_number"] for item in body] == [2, 1]
        assert body[0]["events"][0]["change_code"] == ChangeCode.DEADLINE_CHANGED.value
        assert "normalized_snapshot" not in body[0]

        changes = await client_a.get(f"/api/v1/tenders/{tender.id}/changes")
        assert changes.status_code == 200
        assert [item["change_code"] for item in changes.json()] == [
            ChangeCode.DEADLINE_CHANGED.value
        ]

    async def test_el_workspace_ensenya_els_canvis_posteriors_a_la_decisio(
        self, client_a, db, tenant_a, tender
    ):
        """La historia es publica; l'avis de "has de tornar-hi" es de cadascu."""
        owner, company = tenant_a
        await lifecycle.record_revision(db, tender)
        await db.commit()
        workspace = (
            await workspace_service.create_workspace(
                db, company_id=company.id, tender=tender, user_id=owner.id
            )
        ).workspace
        await workspace_service.record_decision(
            db,
            workspace,
            decision=WorkspaceDecisionType.GO,
            user=owner,
            role=MembershipRole.OWNER,
            reason="Encaixa amb la nostra solvencia.",
        )
        await db.commit()
        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=7))

        response = await client_a.get(
            f"/api/v1/companies/{company.id}/workspaces/{workspace.id}"
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision_outdated"] is True
        assert body["tender_actionable"] is True
        assert [item["change_code"] for item in body["changes_since_decision"]] == [
            ChangeCode.DEADLINE_CHANGED.value
        ]

    async def test_per_defecte_nomes_sensenyen_els_canvis_materials(
        self, client_a, db, tender
    ):
        await lifecycle.record_revision(db, tender)
        await db.commit()
        await revise(db, tender, title="Servei de neteja viaria (correccio)")

        material = await client_a.get(f"/api/v1/tenders/{tender.id}/changes")
        assert material.json() == []

        tots = await client_a.get(
            f"/api/v1/tenders/{tender.id}/changes?material_only=false"
        )
        assert [item["change_code"] for item in tots.json()] == [ChangeCode.TITLE_CHANGED.value]


# --------------------------------------------------------------------------- #
# Que queda obsolet i que no: la matriu aplicada a cada artefacte               #
# --------------------------------------------------------------------------- #


async def prefit_of(db, company_id, tender_id) -> CompanyTenderMatch:
    """Calcula el pre-fit real d'una empresa sobre una licitacio."""
    await MatchingService(db).recalculate(company_id, tender_ids=[tender_id])
    return await db.scalar(
        sa.select(CompanyTenderMatch).where(
            CompanyTenderMatch.company_id == company_id,
            CompanyTenderMatch.tender_id == tender_id,
        )
    )


async def prefit_for(db, company_id, tender) -> CompanyTenderMatch:
    return await prefit_of(db, company_id, tender.id)


class TestQueQuedaObsolet:
    """El termini mou el pre-fit; un import mou el compliment i no els requisits.

    Aixo no es una preferencia estetica: el pre-fit te component de calendari
    (`timing_score`) i 4C.2 resol els llindars percentuals contra el pressupost.
    Si la matriu no ho diu, el sistema recalcula el que no cal i deixa de
    recalcular el que si.
    """

    async def test_ajornar_el_termini_deixa_el_prefit_endarrerit(
        self, db, tenant_a, tender, workspace_setup
    ):
        _, _, workspace = workspace_setup
        match = await prefit_for(db, workspace.company_id, tender)
        assert match.tender_revision_id is not None

        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=15))

        state = await workspace_service.load_state(db, workspace)
        assert "prefit_outdated" in state.outdated_reasons

    async def test_avancar_el_termini_tambe_deixa_el_prefit_endarrerit(
        self, db, tenant_a, tender, workspace_setup
    ):
        """Un termini que s'escurça es exactament igual de rellevant."""
        _, _, workspace = workspace_setup
        await prefit_for(db, workspace.company_id, tender)

        await revise(db, tender, deadline_at=FUTURE_DEADLINE - timedelta(days=15))

        state = await workspace_service.load_state(db, workspace)
        assert "prefit_outdated" in state.outdated_reasons

    async def test_un_canvi_de_titol_no_endarrereix_el_prefit(
        self, db, tenant_a, tender, workspace_setup
    ):
        """El contrast: si tot ho endarrerís, l'avis no voldria dir res."""
        _, _, workspace = workspace_setup
        await prefit_for(db, workspace.company_id, tender)

        await revise(db, tender, title="Servei de neteja viaria (correccio d'errades)")

        state = await workspace_service.load_state(db, workspace)
        assert "prefit_outdated" not in state.outdated_reasons

    async def test_recalcular_el_prefit_el_torna_a_posar_al_dia(
        self, db, tenant_a, tender, workspace_setup
    ):
        _, _, workspace = workspace_setup
        await prefit_for(db, workspace.company_id, tender)
        await revise(db, tender, deadline_at=FUTURE_DEADLINE + timedelta(days=15))
        assert "prefit_outdated" in (
            await workspace_service.load_state(db, workspace)
        ).outdated_reasons

        await prefit_for(db, workspace.company_id, tender)

        state = await workspace_service.load_state(db, workspace)
        assert "prefit_outdated" not in state.outdated_reasons

    async def test_un_pressupost_nou_mou_el_compliment_i_no_els_requisits(
        self, db, tenant_a, tender, workspace_setup, extraction_run
    ):
        """El cas de 4C.2: "el 50% del pressupost base" no canvia; el 50% si.

        El requisit es literalment el mateix text del mateix plec, i per tant
        reextreure'l no donaria res de nou. El que canvia es el numero contra el
        qual es resol, i aixo viu al compliment.
        """
        _, company = tenant_a
        _, _, workspace = workspace_setup
        revision = await lifecycle.latest_revision(db, tender.id)
        extraction_run.tender_revision_id = revision.id
        compliance = CompanyComplianceRun(
            company_id=company.id,
            tender_id=tender.id,
            tender_revision_id=revision.id,
            requirement_extraction_run_id=extraction_run.id,
            status=ComplianceRunStatus.COMPLETED,
            rules_version="compliance-v1",
            company_profile_fingerprint="a" * 64,
            requirements_fingerprint="b" * 64,
            is_current=True,
        )
        db.add(compliance)
        await db.commit()

        await revise(db, tender, base_budget=Decimal("250000.00"))

        state = await workspace_service.load_state(db, workspace)
        assert "compliance_outdated" in state.outdated_reasons
        assert "requirements_outdated" not in state.outdated_reasons
        # I el run de requisits segueix sent el vigent, intacte.
        await db.refresh(extraction_run)
        assert extraction_run.is_current is True
        assert state.requirement_run.id == extraction_run.id

    async def test_una_moneda_nova_tambe_mou_el_compliment(self, db, tender):
        """100.000 no vol dir el mateix en dues monedes."""
        await lifecycle.record_revision(db, tender)
        await db.commit()

        outcome = await revise(db, tender, currency="USD")

        assert ChangeImpact.COMPLIANCE_AFFECTING in outcome.summary.impacts
        assert ChangeImpact.PREFIT_AFFECTING in outcome.summary.impacts
        assert ChangeImpact.REQUIREMENTS_AFFECTING not in outcome.summary.impacts

    async def test_un_plec_substituit_si_que_obliga_a_tornar_a_llegir(
        self, db, tender, extraction_run
    ):
        """El contrast del cas anterior: aixo si que son requisits nous."""
        document = await add_document(db, tender, url="https://x/pcap.pdf", sha="a" * 64)
        await lifecycle.record_revision(db, tender)
        await db.commit()
        extraction_run.tender_revision_id = (
            await lifecycle.latest_revision(db, tender.id)
        ).id
        await db.commit()

        await attach_content(db, document, sha="b" * 64)
        await db.commit()
        await lifecycle.record_revision(db, tender)
        await db.commit()

        log = await lifecycle.load_change_log(db, tender.id)
        basis = await lifecycle.revision_of(db, extraction_run.tender_revision_id)
        assert log.is_outdated(basis, ChangeImpact.REQUIREMENTS_AFFECTING) is True


# --------------------------------------------------------------------------- #
# El lot al qual ens presentavem                                                #
# --------------------------------------------------------------------------- #


async def add_lot(db, tender, *, external_id="L1", **fields) -> TenderLot:
    lot = TenderLot(tender_id=tender.id, external_lot_id=external_id, **fields)
    db.add(lot)
    await db.commit()
    await db.refresh(lot)
    return lot


class TestElLotDunaCandidatura:
    async def test_un_lot_que_desapareix_del_feed_no_esborra_la_candidatura(
        self, db, tenant_a, tender, storage
    ):
        """El pitjor final possible: la feina d'una empresa esborrada en silenci.

        `bid_workspaces.tender_lot_id` te ON DELETE CASCADE. Mentre la ingestió
        esborrava els lots que ja no eren al feed, una reorganitzacio de lots de
        l'organ s'enduia el workspace sencer amb les seves decisions i els seus
        paquets. Ara la fila del lot es marca i es queda.
        """
        owner, company = tenant_a
        lot = await add_lot(db, tender, title="Zona nord")
        workspace = (
            await workspace_service.create_workspace(
                db, company_id=company.id, tender=tender, user_id=owner.id, tender_lot_id=lot.id
            )
        ).workspace
        await db.commit()
        workspace_id = workspace.id

        lot.removed_at = datetime.now(UTC)
        await lifecycle.record_revision(db, tender)
        await db.commit()

        assert await db.get(BidWorkspace, workspace_id) is not None
        state = await workspace_service.load_state(db, workspace)
        assert state.lot_removed is True
        assert state.workspace_actionable is False
        assert "lot_removed" in state.outdated_reasons

    async def test_no_es_congela_cap_paquet_dun_lot_que_ja_no_hi_es(
        self, db, tenant_a, tender
    ):
        owner, company = tenant_a
        lot = await add_lot(db, tender, title="Zona nord")
        workspace = (
            await workspace_service.create_workspace(
                db, company_id=company.id, tender=tender, user_id=owner.id, tender_lot_id=lot.id
            )
        ).workspace
        await db.commit()

        lot.removed_at = datetime.now(UTC)
        await db.commit()

        with pytest.raises(TenderNotActionableError) as excinfo:
            await freeze_package(db, workspace=workspace, user=owner, role=MembershipRole.OWNER)

        assert excinfo.value.details["reason"] == "lot_removed"

    async def test_perdre_un_lot_es_un_canvi_material_amb_el_seu_event(self, db, tender):
        lot = await add_lot(db, tender, title="Zona nord")
        await lifecycle.record_revision(db, tender)
        await db.commit()

        lot.removed_at = datetime.now(UTC)
        outcome = await revise(db, tender)

        assert [c.code for c in outcome.summary.changes] == [ChangeCode.LOT_REMOVED]
        assert ChangeImpact.WORKSPACE_CRITICAL in outcome.summary.impacts

    async def test_un_lot_que_torna_tambe_es_un_canvi(self, db, tender):
        """Qui hi treballava ho havia donat per perdut: ho ha de tornar a mirar."""
        lot = await add_lot(db, tender, title="Zona nord")
        lot.removed_at = datetime.now(UTC)
        await lifecycle.record_revision(db, tender)
        await db.commit()

        lot.removed_at = None
        outcome = await revise(db, tender)

        assert [c.code for c in outcome.summary.changes] == [ChangeCode.LOT_RESTORED]

    async def test_limport_dun_lot_mou_el_prefit_i_el_compliment(self, db, tender):
        """Qui es presenta a un lot es mesura contra el lot, no contra el total."""
        lot = await add_lot(db, tender, title="Zona nord", estimated_value=Decimal("50000"))
        await lifecycle.record_revision(db, tender)
        await db.commit()

        lot.estimated_value = Decimal("90000")
        outcome = await revise(db, tender)

        assert [c.code for c in outcome.summary.changes] == [ChangeCode.LOT_VALUE_CHANGED]
        assert outcome.summary.impacts == frozenset(
            {ChangeImpact.PREFIT_AFFECTING, ChangeImpact.COMPLIANCE_AFFECTING}
        )

    async def test_el_cpv_dun_lot_mou_el_prefit_i_no_el_compliment(self, db, tender):
        lot = await add_lot(db, tender, title="Zona nord")
        db.add(CPVCode(code="90611000", description="Neteja de carrers"))
        await db.flush()
        db.add(TenderLotCPV(tender_lot_id=lot.id, cpv_code="90611000", is_main=True))
        await lifecycle.record_revision(db, tender)
        await db.commit()

        await db.execute(
            sa.delete(TenderLotCPV).where(TenderLotCPV.tender_lot_id == lot.id)
        )
        db.add(CPVCode(code="90612000", description="Escombrat de carrers"))
        await db.flush()
        db.add(TenderLotCPV(tender_lot_id=lot.id, cpv_code="90612000", is_main=True))
        outcome = await revise(db, tender)

        assert [c.code for c in outcome.summary.changes] == [ChangeCode.LOT_CPV_CHANGED]
        assert outcome.summary.impacts == frozenset({ChangeImpact.PREFIT_AFFECTING})

    async def test_el_termini_dun_lot_no_pot_ser_invisible(self, db, tender):
        """`load_state` fa servir el termini del lot abans que el de l'expedient."""
        lot = await add_lot(db, tender, title="Zona nord", deadline_at=FUTURE_DEADLINE)
        await lifecycle.record_revision(db, tender)
        await db.commit()

        lot.deadline_at = FUTURE_DEADLINE + timedelta(days=7)
        outcome = await revise(db, tender)

        assert [c.code for c in outcome.summary.changes] == [ChangeCode.LOT_DEADLINE_CHANGED]
        assert ChangeImpact.WORKSPACE_CRITICAL in outcome.summary.impacts

    async def test_un_lot_retirat_deixa_de_puntuar_al_prefit(self, db, tenant_a, tender):
        """Si no, seguiria sortint a la llista d'oportunitats com si existis."""
        _, company = tenant_a
        company_id, tender_id = company.id, tender.id
        lot = await add_lot(db, tender, title="Zona nord", estimated_value=Decimal("50000"))
        lot_id = lot.id
        await lifecycle.record_revision(db, tender)
        await db.commit()

        # La sessio del test tenia carregada la col.leccio de lots d'abans que
        # el lot existis. A produccio cada recalcul arriba amb sessio neta.
        db.expunge_all()
        assert (await prefit_of(db, company_id, tender_id)).total_lot_count == 1

        (await db.get(TenderLot, lot_id)).removed_at = datetime.now(UTC)
        await db.commit()
        db.expunge_all()

        assert (await prefit_of(db, company_id, tender_id)).total_lot_count == 0


# --------------------------------------------------------------------------- #
# Saber que hi ha dins d'un plec no es que el plec hagi canviat                  #
# --------------------------------------------------------------------------- #


class TestPrimerShaConegutContraSubstitucio:
    """Els dos casos es semblen —el SHA de la fotografia canvia— i son oposats.

    Al primer, el document sempre havia sigut aquell i l'unic que ha canviat es
    el que nosaltres en sabem. Al segon, l'organ n'ha publicat un altre. Si es
    tractessin igual, el dia que ens posem al dia amb les descarregues pendents
    s'invalidarien els requisits de mig cataleg i el motiu seria fals.
    """

    async def test_descobrir_el_sha_per_primer_cop_es_informatiu(self, db, tender):
        document = await add_document(db, tender, url="https://x/pcap.pdf", sha=None)
        await lifecycle.record_revision(db, tender)
        await db.commit()

        await attach_content(db, document, sha="a" * 64)
        await db.commit()
        outcome = await revise(db, tender)

        assert [c.code for c in outcome.summary.changes] == [
            ChangeCode.DOCUMENT_CONTENT_IDENTIFIED
        ]
        assert outcome.summary.impacts == frozenset({ChangeImpact.INFORMATIONAL})
        assert outcome.summary.is_material is False

    async def test_descobrir_el_sha_per_primer_cop_no_invalida_els_requisits(
        self, db, tender, extraction_run
    ):
        document = await add_document(db, tender, url="https://x/pcap.pdf", sha=None)
        await lifecycle.record_revision(db, tender)
        await db.commit()
        extraction_run.tender_revision_id = (
            await lifecycle.latest_revision(db, tender.id)
        ).id
        await db.commit()

        await attach_content(db, document, sha="a" * 64)
        await db.commit()
        await revise(db, tender)

        log = await lifecycle.load_change_log(db, tender.id)
        basis = await lifecycle.revision_of(db, extraction_run.tender_revision_id)
        assert log.is_outdated(basis, ChangeImpact.REQUIREMENTS_AFFECTING) is False
        assert log.since(basis) == ()

    async def test_un_sha_diferent_del_que_ja_sabiem_si_que_es_una_substitucio(
        self, db, tender, extraction_run
    ):
        """El contrast exacte: la mateixa columna, i el veredicte oposat."""
        document = await add_document(db, tender, url="https://x/pcap.pdf", sha="a" * 64)
        await lifecycle.record_revision(db, tender)
        await db.commit()
        extraction_run.tender_revision_id = (
            await lifecycle.latest_revision(db, tender.id)
        ).id
        await db.commit()

        await attach_content(db, document, sha="b" * 64)
        await db.commit()
        outcome = await revise(db, tender)

        assert [c.code for c in outcome.summary.changes] == [ChangeCode.DOCUMENT_REPLACED]
        assert outcome.summary.is_material is True
        log = await lifecycle.load_change_log(db, tender.id)
        basis = await lifecycle.revision_of(db, extraction_run.tender_revision_id)
        assert log.is_outdated(basis, ChangeImpact.REQUIREMENTS_AFFECTING) is True

    async def test_la_primera_descarrega_real_no_invalida_res(self, session, tender_document):
        """El mateix, pel cami de veritat: baixar-lo per primera vegada."""
        async with serving(PCAP) as client:
            await process_tender_document(
                session, tender_document, client=client, resolver=resolver
            )

        tender = await session.get(Tender, tender_document.tender_id)
        revision = await lifecycle.latest_revision(session, tender.id)
        # Nomes hi ha la revisio inicial: no hi havia cap estat anterior amb que
        # comparar, i per tant no hi ha cap canvi que inventar.
        assert revision.revision_number == 1
        log = await lifecycle.load_change_log(session, tender.id)
        assert log.entries == ()


# --------------------------------------------------------------------------- #
# Mateix instant = mateix valor                                                 #
# --------------------------------------------------------------------------- #


class TestMateixInstant:
    """Un moment escrit de dues maneres no es un canvi de termini.

    Va passar de veritat, i no en un cas rebuscat: a la PRIMERA licitacio que es
    tocava. En processar el primer plec, la fotografia es tornava a construir,
    el termini sortia amb un offset diferent del que s'havia desat, i la
    candidatura naixia amb un `DEADLINE_CHANGED` **material** que ningu havia
    fet —i, darrere seu, l'avis que tota l'analisi havia quedat endarrerida.

    Es exactament el fals positiu que la 0016 es va escriure per evitar: si
    l'avis salta sempre, deixa de voler dir res.
    """

    #: El mateix moment, escrit de tres maneres. 19:00 a Madrid a l'hivern son
    #: les 18:00 UTC; l'offset explicit i la zona nomenada han de coincidir.
    MADRID_HIVERN = datetime(2026, 11, 28, 19, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    UTC_HIVERN = datetime(2026, 11, 28, 18, 0, tzinfo=UTC)

    def test_el_mateix_instant_amb_offsets_diferents_no_es_cap_canvi(self):
        before = {"deadline_at": "2026-11-28T19:00:00+01:00"}
        after = {"deadline_at": "2026-11-28T18:00:00+00:00"}

        assert diff_snapshots(before, after) == []

    def test_el_hash_no_depen_de_com_sescriu_lhora(self):
        """Mateix instant, mateixa identitat: cap revisio nova."""
        assert canonical_instant(self.MADRID_HIVERN) == canonical_instant(self.UTC_HIVERN)
        assert snapshot_hash({"deadline_at": canonical_instant(self.MADRID_HIVERN)}) == (
            snapshot_hash({"deadline_at": canonical_instant(self.UTC_HIVERN)})
        )

    def test_una_data_sense_hora_es_la_seva_forma_iso(self):
        """`TenderDocument.published_at` es una data (el portal de la PSCP l'omple): es
        canonicalitza igual que el seu text ISO i no fa petar la fotografia."""
        assert canonical_instant(date(2026, 9, 14)) == canonical_instant("2026-09-14")
        assert canonical_instant(date(2026, 9, 14)) == "2026-09-14T00:00:00+00:00"

    def test_un_instant_realment_diferent_si_que_es_un_canvi(self):
        """La correccio no pot fer-nos cecs al canvi que si que compta."""
        before = {"deadline_at": "2026-11-28T19:00:00+01:00"}
        after = {"deadline_at": "2026-11-28T19:00:00+00:00"}  # una hora mes tard

        changes = diff_snapshots(before, after)

        assert [change.code for change in changes] == [ChangeCode.DEADLINE_CHANGED]
        assert changes[0].is_material is True

    def test_el_canvi_dhorari_destiu_no_inventa_cap_canvi(self):
        """A l'estiu Madrid es +02:00. Segueix sent el mateix moment."""
        estiu_madrid = datetime(2026, 7, 15, 20, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        estiu_utc = datetime(2026, 7, 15, 18, 0, tzinfo=UTC)

        assert estiu_madrid == estiu_utc  # premissa del test
        assert diff_snapshots(
            {"deadline_at": canonical_instant(estiu_madrid)},
            {"deadline_at": canonical_instant(estiu_utc)},
        ) == []

    def test_un_datetime_sense_zona_es_tracta_com_a_utc(self):
        naive = datetime(2026, 11, 28, 18, 0)

        assert canonical_instant(naive) == canonical_instant(self.UTC_HIVERN)

    def test_el_termini_dun_lot_te_la_mateixa_regla(self):
        """El termini del lot mana per a qui s'hi presenta: mateix criteri."""
        before = {
            "lots": [{"external_lot_id": "1", "deadline_at": "2026-11-28T19:00:00+01:00"}]
        }
        after = {
            "lots": [{"external_lot_id": "1", "deadline_at": "2026-11-28T18:00:00+00:00"}]
        }

        assert diff_snapshots(before, after) == []

    def test_el_termini_dun_lot_que_es_mou_de_veritat_si_que_es_veu(self):
        before = {
            "lots": [{"external_lot_id": "1", "deadline_at": "2026-11-28T19:00:00+01:00"}]
        }
        after = {
            "lots": [{"external_lot_id": "1", "deadline_at": "2026-11-29T19:00:00+01:00"}]
        }

        changes = diff_snapshots(before, after)

        assert [change.code for change in changes] == [ChangeCode.LOT_DEADLINE_CHANGED]

    def test_la_data_de_publicacio_dun_document_tampoc(self):
        document = {"url": "https://x/a.pdf", "sha256": "aa"}
        before = {"documents": [document | {"published_at": "2026-01-02T10:00:00+01:00"}]}
        after = {"documents": [document | {"published_at": "2026-01-02T09:00:00+00:00"}]}

        assert diff_snapshots(before, after) == []

    async def test_reescriure_el_termini_en_una_altra_zona_no_crea_revisio(self, db, tender):
        """Contra la base de dades: mateix moment, cap revisio i cap event."""
        tender.deadline_at = self.MADRID_HIVERN
        await lifecycle.record_revision(db, tender)
        await db.commit()

        outcome = await revise(db, tender, deadline_at=self.UTC_HIVERN)

        assert outcome.unchanged is True
        assert await count_revisions(db, tender) == 1
        assert await events_of(db, tender) == []

    async def test_coneixer_el_primer_document_no_mou_el_termini(self, db, tender):
        """La regressio E2E, reproduida.

        Processar el primer plec fa una revisio nova —ara sabem el SHA d'un
        document que abans no haviem baixat— i aixo es correcte i informatiu.
        El que no pot passar es que, de passada, el termini sembli que s'ha
        mogut nomes perque la fotografia s'ha tornat a construir.
        """
        tender.deadline_at = self.MADRID_HIVERN
        document = TenderDocument(
            tender_id=tender.id,
            url="https://x/pcap.pdf",
            name="PCAP",
            document_type=TenderDocumentType.ADMINISTRATIVE_TERMS,
        )
        db.add(document)
        await db.commit()
        await lifecycle.record_revision(db, tender)
        await db.commit()

        # El mateix instant, ara escrit en UTC (com el torna PostgreSQL), i el
        # document ja baixat.
        tender.deadline_at = self.UTC_HIVERN
        content = TenderDocumentContent(
            sha256="c" * 64, file_size=10, mime_type="application/pdf", storage_key="k/1"
        )
        db.add(content)
        await db.flush()
        db.add(
            TenderDocumentFile(
                tender_document_id=document.id,
                content_id=content.id,
                source_url=document.url,
                is_current=True,
                download_status=DownloadStatus.DOWNLOADED,
            )
        )
        await db.commit()

        outcome = await lifecycle.record_revision(db, tender)
        await db.commit()

        assert outcome.unchanged is False
        assert await codes_of(db, tender) == [ChangeCode.DOCUMENT_CONTENT_IDENTIFIED.value]
        assert outcome.summary.is_material is False
