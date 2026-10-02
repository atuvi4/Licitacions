"""Gold estructurat del COSTAT PLEC: carregador per la via interna de 0048 i 0049.

Un dataset de `datasets/analysis/<cas>.tender-gold.json` descriu, com a dades
declaratives i nomes amb el que diuen els documents de la licitacio, el que el
model ha de poder representar: expedient, blocs, seus, elements d'abast,
criteris, requisits atomics (territoris, proves, necessitats, enllacos amb
criteris), fets contractuals i les cites de cada cosa.

El carregador NO es cap segona implementacio de persistencia: crea el tender
public (sense cap empresa propietaria) i despres crida els serveis interns
`tender_structure`, `tender_scope`, `source_refs` i `manual_requirement`, els
mateixos que fan servir els routers, sense cap politica de tenant. Res del que
fa llegeix coneixement de cap empresa ni crea cap resultat d'analisi.

Els fitxers reals no son al repositori: cada document es dona d'alta amb el
seu nom i el SHA-256 del gold de classificacio, sense bytes (`file_size = 0`,
no extraible). Les cites son `document_file` amb la localitzacio (document +
apartat) i, nomes quan la lectura la va registrar literal, una frase curta.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import (
    AnalysisRunStatus,
    AreaKind,
    AwardCriterionType,
    AwardEvaluationType,
    ContractFactCode,
    ContractType,
    DocumentFileOrigin,
    DownloadStatus,
    ExtractionConfidence,
    NeedComparator,
    NeedType,
    ProcedureType,
    RequirementCategory,
    RequirementObligation,
    RequirementOperator,
    RequirementOrigin,
    RequirementScope,
    RequirementTiming,
    ReviewStatus,
    SourceType,
    TenderDocumentType,
    TenderSiteRole,
    TenderStatus,
    TerritoryCompleteness,
    TerritoryMatchMode,
)
from app.models.provenance import TenderSourceRef
from app.models.requirements import AwardCriterion, Requirement, RequirementExtractionRun
from app.models.sources import Source
from app.models.tender_documents import TenderDocumentContent, TenderDocumentFile
from app.models.tender_scope import RequirementCapabilityNeed, TenderContractFact, TenderScopeItem
from app.models.tender_structure import TenderBlock, TenderSite
from app.models.tenders import Buyer, Tender, TenderCPV, TenderDocument
from app.services import manual_requirement, source_refs, tender_structure
from app.services import tender_scope as scope
from app.services.cpv import ensure_cpv_code

ANALYSIS_DATASETS = Path(__file__).resolve().parents[2] / "datasets" / "analysis"

#: Com s'anomena cada document a la localitzacio d'una cita.
_DOCUMENT_LABELS = {
    "PCAP": "PCAP",
    "PPT": "PPT (DOC-02)",
    "DOC03": "DOC-03",
    "DOC04": "DOC-04",
    "DOC05": "DOC-05",
    "DOC01": "DOC-01",
    "XLSX06": "Plantilla XLSX",
    "ACORD": "Acord JGL",
}


class TenderGoldError(ValueError):
    """El dataset diu una cosa que el carregador no pot fer sense inventar."""


def load_dataset(case: str) -> dict[str, Any]:
    return json.loads((ANALYSIS_DATASETS / f"{case}.tender-gold.json").read_text(encoding="utf-8"))


def dataset_fingerprint(dataset: dict[str, Any]) -> str:
    canonical = json.dumps(dataset, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def dedup_key(case: str, kind: str, code: str) -> str:
    """Clau estable d'un requisit o criteri del gold: el mateix codi, la mateixa clau."""
    return hashlib.sha256(f"{case}:{kind}:{code}".encode()).hexdigest()


def _money(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _date(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


@dataclass
class LoadedTenderGold:
    case: str
    tender: Tender
    run: RequirementExtractionRun
    documents: dict[str, TenderDocument] = field(default_factory=dict)
    files: dict[str, TenderDocumentFile] = field(default_factory=dict)
    source_refs: dict[tuple[str, str, str | None], TenderSourceRef] = field(default_factory=dict)
    blocks: dict[str, TenderBlock] = field(default_factory=dict)
    sites: dict[str, TenderSite] = field(default_factory=dict)
    scope_items: dict[str, TenderScopeItem] = field(default_factory=dict)
    criteria: dict[str, AwardCriterion] = field(default_factory=dict)
    requirements: dict[str, Requirement] = field(default_factory=dict)
    needs: dict[str, list[RequirementCapabilityNeed]] = field(default_factory=dict)
    facts: dict[str, TenderContractFact] = field(default_factory=dict)


class _Loader:
    def __init__(self, session: AsyncSession, dataset: dict[str, Any], user: Any | None) -> None:
        self.session = session
        self.dataset = dataset
        self.user = user
        self.case = dataset["case"]
        self.loaded: LoadedTenderGold | None = None

    # ------------------------------------------------------------------ #
    # Expedient                                                           #
    # ------------------------------------------------------------------ #

    async def tender(self) -> tuple[Tender, RequirementExtractionRun]:
        spec = self.dataset["tender"]
        if spec.get("lots"):
            raise TenderGoldError("Aquest carregador no crea lots: el gold de l'Escala no en te.")
        source = Source(
            code=f"gold:{self.case}",
            name="Gold manual del costat plec",
            source_type=SourceType.MANUAL,
        )
        self.session.add(source)
        await self.session.flush()
        buyer = Buyer(
            source_id=source.id,
            external_identifier=spec["buyer"]["external_identifier"],
            name=spec["buyer"]["name"],
        )
        self.session.add(buyer)
        await self.session.flush()
        tender = Tender(
            source_id=source.id,
            external_id=spec["external_id"],
            expediente_number=spec.get("expediente_number"),
            title=spec["title"],
            description=spec.get("description"),
            buyer_id=buyer.id,
            procedure_type=ProcedureType(spec["procedure_type"]),
            contract_type=ContractType(spec["contract_type"]),
            status=TenderStatus(spec["status"]),
            currency=spec.get("currency", "EUR"),
            base_budget_without_tax=_money(spec.get("base_budget_without_tax")),
            base_budget=_money(spec.get("base_budget")),
            estimated_value=_money(spec.get("estimated_value")),
            publication_date=_date(spec.get("publication_date")),
            deadline_at=spec.get("deadline_at"),
            place_of_execution=spec.get("place_of_execution"),
        )
        self.session.add(tender)
        await self.session.flush()
        tender_structure.update_structure(
            tender,
            {
                "duration_months": spec.get("duration_months"),
                "extension_months": spec.get("extension_months"),
                "regulated_harmonised": spec.get("regulated_harmonised"),
                "planned_start_date": _date(spec.get("planned_start_date")),
            },
        )
        cpv = spec.get("cpv") or {}
        codes = ([cpv["main"]] if cpv.get("main") else []) + list(cpv.get("others", []))
        for code in codes:
            await ensure_cpv_code(self.session, code)
            self.session.add(
                TenderCPV(tender_id=tender.id, cpv_code=code, is_main=code == cpv.get("main"))
            )
        await self.session.flush()

        run = RequirementExtractionRun(
            tender_id=tender.id,
            status=AnalysisRunStatus.COMPLETED,
            input_fingerprint=dataset_fingerprint(self.dataset),
            extractor_version="tender-gold-v3",
            prompt_version="none",
            schema_version=self.dataset["schema"],
            provider="gold",
            model="manual",
            is_current=True,
        )
        self.session.add(run)
        await self.session.flush()
        return tender, run

    # ------------------------------------------------------------------ #
    # Documents i cites                                                   #
    # ------------------------------------------------------------------ #

    async def documents(self) -> None:
        loaded = self.loaded
        assert loaded is not None
        for spec in self.dataset["documents"]:
            document = TenderDocument(
                tender_id=loaded.tender.id,
                name=spec["name"],
                document_type=TenderDocumentType(spec["document_type"]),
                url=f"gold://{self.case}/{spec['path']}",
                mime_type=spec["mime_type"],
            )
            self.session.add(document)
            await self.session.flush()
            content = TenderDocumentContent(
                sha256=spec["sha256"],
                storage_key=f"gold/{self.case}/{spec['sha256']}",
                file_size=0,
                mime_type=spec["mime_type"],
                is_text_extractable=False,
            )
            self.session.add(content)
            await self.session.flush()
            file = TenderDocumentFile(
                tender_document_id=document.id,
                content_id=content.id,
                origin=DocumentFileOrigin.UPLOADED,
                download_status=DownloadStatus.DOWNLOADED,
                original_filename=spec["path"],
                declared_mime_type=spec["mime_type"],
            )
            self.session.add(file)
            await self.session.flush()
            loaded.documents[spec["key"]] = document
            loaded.files[spec["key"]] = file

    async def ref(self, citation: dict[str, Any]) -> TenderSourceRef:
        """La cita d'un (document, apartat, frase): una sola fila, compartida per
        tothom qui la faci servir."""
        loaded = self.loaded
        assert loaded is not None
        key = (citation["doc"], citation["where"], citation.get("quote"))
        existing = loaded.source_refs.get(key)
        if existing is not None:
            return existing
        document = next(d for d in self.dataset["documents"] if d["key"] == citation["doc"])
        if not document.get("citable", True):
            raise TenderGoldError(
                f"El document {citation['doc']} no es citable: {document.get('note')}"
            )
        ref = await source_refs.create_document_file_ref(
            self.session,
            tender_id=loaded.tender.id,
            file_id=loaded.files[citation["doc"]].id,
            user=self.user,
            location_label=f"{_DOCUMENT_LABELS[citation['doc']]} {citation['where']}",
            quote=citation.get("quote"),
        )
        loaded.source_refs[key] = ref
        return ref

    async def cite(self, entity: Any, citations: list[dict[str, Any]], *, link) -> None:
        for citation in citations:
            ref = await self.ref(citation)
            await link(entity, ref)

    async def _link_structure(self, entity: Any, ref: TenderSourceRef) -> None:
        await tender_structure.link_source_ref(
            self.session, entity=entity, source_ref_id=ref.id, user=self.user
        )

    async def _link_scope(self, entity: Any, ref: TenderSourceRef) -> None:
        await scope.link_source_ref(
            self.session, entity=entity, source_ref_id=ref.id, user=self.user
        )

    async def _link_requirement(self, requirement: Requirement, ref: TenderSourceRef) -> None:
        await manual_requirement.link_source_ref(
            self.session, requirement=requirement, source_ref_id=ref.id, user=self.user
        )

    # ------------------------------------------------------------------ #
    # Estructura (0048)                                                   #
    # ------------------------------------------------------------------ #

    async def blocks(self) -> None:
        loaded = self.loaded
        assert loaded is not None
        for spec in self.dataset["blocks"]:
            block = await tender_structure.create_block(
                self.session,
                loaded.tender,
                {
                    "code": spec["code"],
                    "name": spec["name"],
                    "tender_lot_id": None,
                    "base_price_ex_vat": _money(spec.get("base_price_ex_vat")),
                    "price_points": spec.get("price_points"),
                    "separately_awardable": spec.get("separately_awardable"),
                    "billing_terms_json": spec.get("billing_terms_json"),
                    "billing_schema": spec.get("billing_schema"),
                },
            )
            loaded.blocks[spec["code"]] = block
            await self.cite(block, spec.get("sources", []), link=self._link_structure)

    async def sites(self) -> None:
        loaded = self.loaded
        assert loaded is not None
        for spec in self.dataset["sites"]:
            site = await tender_structure.create_site(
                self.session,
                loaded.tender,
                {
                    "code": spec.get("code"),
                    "name": spec.get("name"),
                    "role": TenderSiteRole(spec["role"]),
                    "is_critical": spec.get("is_critical"),
                    "address": spec.get("address"),
                    "municipality_code": spec.get("municipality_code"),
                    "municipality_code_system": spec.get("municipality_code_system"),
                    # Indicatives (DOC-04): es desen, pero cap regla les fa servir.
                    "latitude": _money(spec.get("latitude")),
                    "longitude": _money(spec.get("longitude")),
                },
            )
            loaded.sites[spec["key"]] = site
            await self.cite(site, spec.get("sources", []), link=self._link_structure)

    def _block_ids(self, codes: list[str]) -> list[uuid.UUID]:
        assert self.loaded is not None
        return [self.loaded.blocks[code].id for code in codes]

    async def criteria(self) -> None:
        loaded = self.loaded
        assert loaded is not None
        for spec in self.dataset["award_criteria"]:
            criterion = AwardCriterion(
                run_id=loaded.run.id,
                tender_id=loaded.tender.id,
                name=spec["name"],
                description=spec.get("description"),
                points=spec.get("points"),
                formula_text=spec.get("formula_text"),
                formula_variables=spec.get("formula_variables"),
                tranches=spec.get("tranches"),
                criterion_type=AwardCriterionType(spec["criterion_type"]),
                evaluation_type=AwardEvaluationType(spec["evaluation_type"]),
                confidence=ExtractionConfidence.HIGH,
                review_status=ReviewStatus.VERIFIED,
                dedup_key=dedup_key(self.case, "criterion", spec["code"]),
                timing=RequirementTiming(spec.get("timing", "unknown")),
            )
            self.session.add(criterion)
            await self.session.flush()
            await tender_structure.set_criterion_blocks(
                self.session, loaded.tender, criterion, self._block_ids(spec.get("blocks", []))
            )
            loaded.criteria[spec["code"]] = criterion
            await self.cite(criterion, spec.get("sources", []), link=self._link_structure)

    # ------------------------------------------------------------------ #
    # Abast i semantica (0049)                                            #
    # ------------------------------------------------------------------ #

    async def scope_items(self) -> None:
        loaded = self.loaded
        assert loaded is not None
        for spec in self.dataset["scope_items"]:
            item = await scope.create_scope_item(
                self.session,
                loaded.tender,
                {
                    "service_line_code": spec["service_line_code"],
                    "block_id": loaded.blocks[spec["block"]].id if spec.get("block") else None,
                    "tender_lot_id": None,
                    "site_id": loaded.sites[spec["site"]].id if spec.get("site") else None,
                    "quantity": None if spec.get("quantity") is None else Decimal(spec["quantity"]),
                    "unit": spec.get("unit"),
                    "specs_json": spec.get("specs_json"),
                    "specs_schema": spec.get("specs_schema"),
                    "unit_base_price_ex_vat": _money(spec.get("unit_base_price_ex_vat")),
                },
            )
            loaded.scope_items[spec["code"]] = item
            await self.cite(item, spec.get("sources", []), link=self._link_scope)

    async def requirements(self) -> None:
        loaded = self.loaded
        assert loaded is not None
        for spec in self.dataset["requirements"]:
            requirement = Requirement(
                tender_id=loaded.tender.id,
                origin=RequirementOrigin.MANUAL,
                category=RequirementCategory(spec["category"]),
                obligation=RequirementObligation(spec["obligation"]),
                operator=RequirementOperator(spec["operator"]) if spec.get("operator") else None,
                scope=RequirementScope.TENDER,
                title=spec["title"],
                description=spec.get("description"),
                value_json=spec.get("value_json"),
                confidence=ExtractionConfidence.HIGH,
                review_status=ReviewStatus.VERIFIED,
                dedup_key=dedup_key(self.case, "requirement", spec["code"]),
                timing=RequirementTiming(spec.get("timing", "unknown")),
            )
            self.session.add(requirement)
            await self.session.flush()
            loaded.requirements[spec["code"]] = requirement

            await tender_structure.set_requirement_blocks(
                self.session, loaded.tender, requirement, self._block_ids(spec.get("blocks", []))
            )
            scope.set_territory_match_mode(
                requirement, TerritoryMatchMode(spec.get("territory_match_mode", "any"))
            )
            await scope.set_territory_semantics(
                self.session,
                loaded.tender,
                requirement,
                completeness=TerritoryCompleteness(spec.get("territory_completeness", "unknown")),
                territories=[
                    {
                        "kind": AreaKind(t["kind"]),
                        "label": t.get("label"),
                        "code": t.get("code"),
                        "code_system": t.get("code_system"),
                        "qualifier": t.get("qualifier"),
                        "site_id": loaded.sites[t["site"]].id if t.get("site") else None,
                    }
                    for t in spec.get("territories", [])
                ],
            )
            scope.set_proof(requirement, spec.get("proof", []))
            await scope.set_award_links(
                self.session,
                loaded.tender,
                requirement,
                [loaded.criteria[code].id for code in spec.get("criteria", [])],
            )
            await self.session.flush()

            citations = spec.get("sources", [])
            await self.cite(requirement, citations, link=self._link_requirement)

            loaded.needs[spec["code"]] = []
            for need_spec in spec.get("needs", []):
                need = await scope.create_need(
                    self.session,
                    loaded.tender,
                    requirement,
                    {
                        "capability_code": need_spec.get("capability_code"),
                        "unmapped_capability": need_spec.get("unmapped_capability"),
                        "comparator": NeedComparator(need_spec["comparator"]),
                        "value_json": need_spec.get("value_json"),
                        "value_schema": "gold_v1"
                        if need_spec.get("value_json") is not None
                        else None,
                        "need_type": NeedType(need_spec["need_type"]),
                        "points": need_spec.get("points"),
                        "award_criterion_id": (
                            loaded.criteria[need_spec["criterion"]].id
                            if need_spec.get("criterion")
                            else None
                        ),
                    },
                )
                loaded.needs[spec["code"]].append(need)
                await self.cite(need, citations, link=self._link_scope)

    async def facts(self) -> None:
        loaded = self.loaded
        assert loaded is not None
        for spec in self.dataset["contract_facts"]:
            fact = await scope.create_fact(
                self.session,
                loaded.tender,
                fact_code=ContractFactCode(spec["fact_code"]),
                value_json=spec["value_json"],
                value_schema=spec["value_schema"],
                block_ids=self._block_ids(spec.get("blocks", [])),
            )
            loaded.facts[spec["code"]] = fact
            await self.cite(fact, spec.get("sources", []), link=self._link_scope)

    async def run(self) -> LoadedTenderGold:
        tender, run = await self.tender()
        self.loaded = LoadedTenderGold(case=self.case, tender=tender, run=run)
        await self.documents()
        await self.blocks()
        await self.sites()
        await self.criteria()
        await self.scope_items()
        await self.requirements()
        await self.facts()
        await self.session.flush()
        return self.loaded


async def load_tender_gold(
    session: AsyncSession, dataset: dict[str, Any], *, user: Any | None = None
) -> LoadedTenderGold:
    """Carrega el gold en una base on el vocabulari ja hi es. No fa commit."""
    return await _Loader(session, dataset, user).run()
