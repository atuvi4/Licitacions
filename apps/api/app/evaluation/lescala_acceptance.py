"""Eina d'acceptacio de L'Escala (3B-2 §8.3-8.5 i §8.8): comparador de NOMES LECTURA.

Compara una fotografia (`TenderSnapshot`) d'UNA licitacio de la base amb l'oracle
public del repositori:

* documents per SHA-256 (`datasets/analysis/<cas>.tender-gold.json`,
  `documents[].sha256`): 9 esperats a L'Escala; mai cap fitxer real;
* rols efectius per SHA (`datasets/classification/<cas>.gold.json`);
* estructura per clau natural: blocs (`code`), seus (`code` o `name`), elements
  d'abast (bloc, seu, linia de servei, quantitat, unitat) i fets (`fact_code` + blocs);
* criteris i requisits per `dedup_key` (el del gold: `tender_gold.dedup_key`) i, si
  no coincideix, per la clau de titol normalitzat de l'extractor
  (`title_keys.normalized_title`, la MATEIXA funcio que entra a la clau de
  deduplicacio d'un run real i d'un requisit manual); l'informe diu per quina via
  (`matched_by`);
* cites per document: files de `tender_source_refs` mes l'evidencia dels requisits
  i dels criteris (`requirement_evidence`, `award_criterion_evidence`), com a
  recompte trobades/esperades.

La sortida son diferencies CLASSIFICADES, mai una ordre d'omplir res (§8.5, §8.9):

    not_in_source_docs      el que el gold cita nomes en documents que la base no te
    extraction_missed       el que un document present hauria d'haver donat (requisits,
                            criteris, rols) i no hi es, havent-hi un run o una
                            classificacio que ho podia donar
    structure_not_authored  el que un document present permetia escriure amb cites
                            (blocs, seus, abast, fets) i ningu ha escrit
    oracle_outdated         el que la base te i l'oracle no coneix (una entitat de
                            mes, el mateix fitxer amb un altre SHA): es registra,
                            no es força
    unverifiable            el que no s'ha pogut comprovar: un rol sense classificacio
                            global sobre aquest fitxer, un document sense fitxer
                            vigent, un requisit o criteri sense cap run d'extraccio.
                            Mai es compta com a `extraction_missed`.

Dues peces, separades a proposit:

* `compare(snapshot, oracle)` es una funcio PURA: cap sessio, cap E/S; el mateix
  snapshot i el mateix oracle donen sempre el mateix informe;
* `load_snapshot(session, tender_id)` nomes fa SELECT, i `read_only_session(url)`
  obre una sessio que no pot escriure: a PostgreSQL amb
  `default_transaction_read_only=on` al servidor (cada transaccio) i
  `SET TRANSACTION READ ONLY`; a SQLite amb `PRAGMA query_only` a cada connexio; i a
  totes dues amb el `commit` refusat (`ReadOnlyViolation`).

En aquesta fase NO s'executa contra el pilot ni cap base real: es prova sobre el
gold sintetic carregat a la base de test. Quan s'executi (§8), la base sera una
copia i la DSN no portara mai contrasenya a la linia d'ordres:

    python -m app.evaluation.lescala_acceptance --database-url <dsn sense contrasenya> \\
        (--tender-id <uuid> | --external-id <id>) [--case lescala-2026] [--json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.evaluation.tender_gold import ANALYSIS_DATASETS, dedup_key, load_dataset
from app.models.provenance import TenderSourceRef
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.tender_documents import TenderDocumentFile
from app.models.tender_scope import ContractFactBlock, TenderContractFact, TenderScopeItem
from app.models.tender_structure import TenderBlock, TenderSite
from app.models.tenders import Tender
from app.services.document_roles import current_shas, effective_document_roles
from app.services.global_document_classification import current_global_run, run_suggestions
from app.services.title_keys import normalized_title
from app.tenders.documents import analyzable_documents

CLASSIFICATION_DATASETS = ANALYSIS_DATASETS.parent / "classification"
DEFAULT_CASE = "lescala-2026"

NOT_IN_SOURCE_DOCS = "not_in_source_docs"
EXTRACTION_MISSED = "extraction_missed"
STRUCTURE_NOT_AUTHORED = "structure_not_authored"
ORACLE_OUTDATED = "oracle_outdated"
UNVERIFIABLE = "unverifiable"
CLASSIFICATIONS = (
    NOT_IN_SOURCE_DOCS,
    EXTRACTION_MISSED,
    STRUCTURE_NOT_AUTHORED,
    ORACLE_OUTDATED,
    UNVERIFIABLE,
)

#: Vies de coincidencia (`matched_by`).
MATCH_NATURAL_KEY = "natural_key"
MATCH_DEDUP_KEY = "dedup_key"
MATCH_TITLE = "title"

#: Seccions del gold i que vol dir que una entitat hi falti quan algun document que
#: la cita SI que es a la base. L'estructura l'escriu una persona amb cites; els
#: criteris i els requisits els dona una extraccio.
MISSING_KIND: dict[str, str] = {
    "blocks": STRUCTURE_NOT_AUTHORED,
    "sites": STRUCTURE_NOT_AUTHORED,
    "scope_items": STRUCTURE_NOT_AUTHORED,
    "contract_facts": STRUCTURE_NOT_AUTHORED,
    "award_criteria": EXTRACTION_MISSED,
    "requirements": EXTRACTION_MISSED,
}
SECTIONS = tuple(MISSING_KIND)
#: Seccions que surten d'una extraccio: es comparen per `dedup_key` i per titol.
EXTRACTED_SECTIONS = ("award_criteria", "requirements")
#: Seccio del gold -> atribut del snapshot amb les claus que la base te.
_SNAPSHOT_ATTR = {
    "blocks": "blocks",
    "sites": "sites",
    "scope_items": "scope_items",
    "contract_facts": "facts",
    "award_criteria": "criteria",
    "requirements": "requirements",
}
_DRIVERS = ("postgresql+asyncpg", "sqlite+aiosqlite")

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_INPUT = 3


class AcceptanceError(RuntimeError):
    """Falta alguna cosa o no es inequivoc. Sempre abans de llegir res."""


class ReadOnlyViolation(AcceptanceError):
    """Algu ha intentat escriure per una sessio de nomes lectura."""


# --------------------------------------------------------------------------- #
# Claus naturals                                                                #
# --------------------------------------------------------------------------- #


def _value(raw: Any) -> Any:
    """El valor d'un enum (StrEnum o no), o el mateix objecte."""
    return getattr(raw, "value", raw)


def site_key(code: str | None, name: str | None) -> str:
    """Una seu s'identifica pel codi del plec i, si no en te, pel nom."""
    return (code or name or "").strip()


def quantity_key(value: Any) -> str | None:
    """`10`, `10.0` i `Decimal("10.00")` son la mateixa quantitat."""
    if value is None:
        return None
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except InvalidOperation:
        return str(value)


def scope_key(
    block: str | None, site: str | None, service_line_code: str, quantity: Any, unit: str | None
) -> tuple[str | None, str | None, str, str | None, str | None]:
    return (
        block or None,
        site or None,
        str(_value(service_line_code)),
        quantity_key(quantity),
        unit,
    )


def fact_key(fact_code: Any, blocks: Iterable[str]) -> tuple[str, tuple[str, ...]]:
    """`(codi, blocs)`: unic per expedient (`contract_fact_scope_conflict`, 0049)."""
    return (str(_value(fact_code)), tuple(sorted(blocks)))


# --------------------------------------------------------------------------- #
# L'oracle (nomes lectura dels JSON publics)                                    #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Oracle:
    case: str
    tender_gold: dict[str, Any]
    classification_gold: dict[str, Any]

    @property
    def documents(self) -> list[dict[str, Any]]:
        return list(self.tender_gold["documents"])

    @property
    def roles(self) -> dict[str, str]:
        """SHA-256 -> rol esperat, del gold de classificacio del paquet inicial."""
        return {d["sha256"]: d["role"] for d in self.classification_gold["documents"]}

    @property
    def expected_counts(self) -> dict[str, int]:
        return {section: len(self.tender_gold[section]) for section in SECTIONS}


def load_oracle(case: str = DEFAULT_CASE) -> Oracle:
    classification = json.loads(
        (CLASSIFICATION_DATASETS / f"{case}.gold.json").read_text(encoding="utf-8")
    )
    return Oracle(case=case, tender_gold=load_dataset(case), classification_gold=classification)


@dataclass(frozen=True, slots=True)
class GoldEntity:
    section: str
    #: El codi del gold, per anomenar-la a l'informe.
    label: str
    #: La clau natural (o el `dedup_key`) amb que es busca a la base.
    key: Any
    #: Claus dels documents del gold que la citen.
    docs: frozenset[str]
    #: Clau de titol normalitzat (nomes requisits i criteris): la segona via.
    title_key: str | None = None


def gold_entities(oracle: Oracle, section: str) -> list[GoldEntity]:
    dataset = oracle.tender_gold
    sites_by_key = {s["key"]: site_key(s.get("code"), s.get("name")) for s in dataset["sites"]}
    entities: list[GoldEntity] = []
    for item in dataset[section]:
        title_key = None
        if section == "blocks":
            label, key = item["code"], item["code"]
        elif section == "sites":
            label, key = item["key"], site_key(item.get("code"), item.get("name"))
        elif section == "scope_items":
            label = item["code"]
            key = scope_key(
                item.get("block"),
                sites_by_key.get(item["site"]) if item.get("site") else None,
                item["service_line_code"],
                item.get("quantity"),
                item.get("unit"),
            )
        elif section == "award_criteria":
            label, key = item["code"], dedup_key(oracle.case, "criterion", item["code"])
            title_key = normalized_title(item["name"])
        elif section == "requirements":
            label, key = item["code"], dedup_key(oracle.case, "requirement", item["code"])
            title_key = normalized_title(item["title"])
        else:
            label, key = item["code"], fact_key(item["fact_code"], item.get("blocks", []))
        docs = frozenset(source["doc"] for source in item.get("sources", []))
        entities.append(
            GoldEntity(section=section, label=label, key=key, docs=docs, title_key=title_key)
        )
    return entities


def expected_citations(oracle: Oracle) -> dict[str, int]:
    """Cites UNIQUES (document, apartat, frase) que el gold fa a cada document: es el
    nombre de files que el carregador del gold crea a `tender_source_refs`."""
    unique: set[tuple[str, str, str | None]] = set()
    for section in SECTIONS:
        for item in oracle.tender_gold[section]:
            for source in item.get("sources", []):
                unique.add((source["doc"], source["where"], source.get("quote")))
    return dict(Counter(doc for doc, _where, _quote in unique))


# --------------------------------------------------------------------------- #
# La fotografia de la base                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SnapshotDocument:
    document_id: str
    name: str
    #: Nom del fitxer vigent (`original_filename`), si s'ha baixat o pujat.
    path: str | None
    #: SHA-256 del fitxer vigent; `None` si el document no te bytes.
    sha256: str | None
    #: Cert si la classificacio global vigent ha vist aquest document.
    classified: bool
    #: Rol efectiu (classificacio global amb confianca alta o revisio de l'operador).
    role: str | None
    role_source: str | None
    #: Cites sobre aquest document: `tender_source_refs` + evidencia de requisits i
    #: criteris.
    citations: int


@dataclass(frozen=True, slots=True)
class TenderSnapshot:
    tender_id: str
    external_id: str
    expediente: str | None
    documents: tuple[SnapshotDocument, ...]
    blocks: frozenset[str]
    sites: frozenset[str]
    scope_items: frozenset[tuple[str | None, str | None, str, str | None, str | None]]
    #: `dedup_key` -> clau de titol normalitzat (`normalized_title`).
    criteria: dict[str, str]
    requirements: dict[str, str]
    facts: frozenset[tuple[str, tuple[str, ...]]]
    #: Runs d'extraccio vigents: 0 vol dir que res no s'ha pogut extreure encara.
    extraction_runs: int


async def _citations_by_document(
    session: AsyncSession, tender_id: uuid.UUID, current_runs: Any
) -> Counter[uuid.UUID]:
    """Cites per document: refs + evidencia dels requisits i criteris considerats."""
    cited: Counter[uuid.UUID] = Counter()
    queries = (
        sa.select(TenderSourceRef.tender_document_id, sa.func.count())
        .where(TenderSourceRef.tender_id == tender_id)
        .group_by(TenderSourceRef.tender_document_id),
        sa.select(RequirementEvidence.tender_document_id, sa.func.count())
        .join(Requirement, Requirement.id == RequirementEvidence.requirement_id)
        .where(
            Requirement.tender_id == tender_id,
            Requirement.archived_at.is_(None),
            sa.or_(Requirement.run_id.is_(None), Requirement.run_id.in_(current_runs)),
        )
        .group_by(RequirementEvidence.tender_document_id),
        sa.select(AwardCriterionEvidence.tender_document_id, sa.func.count())
        .join(AwardCriterion, AwardCriterion.id == AwardCriterionEvidence.award_criterion_id)
        .where(AwardCriterion.tender_id == tender_id, AwardCriterion.run_id.in_(current_runs))
        .group_by(AwardCriterionEvidence.tender_document_id),
    )
    for query in queries:
        for document_id, total in (await session.execute(query)).all():
            if document_id is not None:
                cited[document_id] += int(total)
    return cited


async def load_snapshot(session: AsyncSession, tender_id: uuid.UUID) -> TenderSnapshot:
    """Nomes SELECT. Cap `add`, cap `flush`, cap `commit`."""
    tender = await session.get(Tender, tender_id)
    if tender is None:
        raise AcceptanceError(f"Licitacio {tender_id} no trobada.")

    documents = await analyzable_documents(session, tender_id)
    ids = [document.id for document in documents]
    shas = await current_shas(session, ids)
    roles = await effective_document_roles(session, tender_id, None)
    classified = set(await run_suggestions(session, await current_global_run(session, tender_id)))
    paths: dict[uuid.UUID, str | None] = {}
    if ids:
        rows = await session.execute(
            sa.select(TenderDocumentFile.tender_document_id, TenderDocumentFile.original_filename)
            .where(TenderDocumentFile.tender_document_id.in_(ids))
            .where(TenderDocumentFile.is_current.is_(True))
        )
        paths = dict(rows.all())
    current_runs = sa.select(RequirementExtractionRun.id).where(
        RequirementExtractionRun.tender_id == tender_id,
        RequirementExtractionRun.is_current.is_(True),
    )
    cited = await _citations_by_document(session, tender_id, current_runs)
    snapshot_documents = tuple(
        SnapshotDocument(
            document_id=str(document.id),
            name=document.name,
            path=paths.get(document.id),
            sha256=shas.get(document.id),
            classified=document.id in classified,
            role=(
                str(_value(roles[document.id].role))
                if document.id in roles and roles[document.id].role is not None
                else None
            ),
            role_source=(str(_value(roles[document.id].source)) if document.id in roles else None),
            citations=int(cited.get(document.id, 0)),
        )
        for document in documents
    )

    blocks = {
        block.id: block.code
        for block in await session.scalars(
            sa.select(TenderBlock).where(TenderBlock.tender_id == tender_id)
        )
    }
    sites = {
        site.id: site_key(site.code, site.name)
        for site in await session.scalars(
            sa.select(TenderSite).where(TenderSite.tender_id == tender_id)
        )
    }
    scope_items = frozenset(
        scope_key(
            blocks.get(item.block_id),
            sites.get(item.site_id),
            item.service_line_code,
            item.quantity,
            item.unit,
        )
        for item in await session.scalars(
            sa.select(TenderScopeItem).where(TenderScopeItem.tender_id == tender_id)
        )
    )
    extraction_runs = int(
        await session.scalar(sa.select(sa.func.count()).select_from(current_runs.subquery())) or 0
    )
    criteria = {
        key: normalized_title(name)
        for key, name in (
            await session.execute(
                sa.select(AwardCriterion.dedup_key, AwardCriterion.name).where(
                    AwardCriterion.tender_id == tender_id, AwardCriterion.run_id.in_(current_runs)
                )
            )
        ).all()
    }
    requirements = {
        key: normalized_title(title)
        for key, title in (
            await session.execute(
                sa.select(Requirement.dedup_key, Requirement.title).where(
                    Requirement.tender_id == tender_id,
                    Requirement.archived_at.is_(None),
                    sa.or_(Requirement.run_id.is_(None), Requirement.run_id.in_(current_runs)),
                )
            )
        ).all()
    }
    fact_rows = (
        await session.execute(
            sa.select(TenderContractFact.id, TenderContractFact.fact_code).where(
                TenderContractFact.tender_id == tender_id
            )
        )
    ).all()
    fact_blocks: dict[uuid.UUID, list[str]] = {fact_id: [] for fact_id, _code in fact_rows}
    if fact_rows:
        links = await session.execute(
            sa.select(ContractFactBlock.fact_id, ContractFactBlock.block_id).where(
                ContractFactBlock.fact_id.in_(list(fact_blocks))
            )
        )
        for fact_id, block_id in links.all():
            fact_blocks[fact_id].append(blocks.get(block_id, str(block_id)))
    facts = frozenset(fact_key(code, fact_blocks[fact_id]) for fact_id, code in fact_rows)

    return TenderSnapshot(
        tender_id=str(tender.id),
        external_id=tender.external_id,
        expediente=tender.expediente_number,
        documents=snapshot_documents,
        blocks=frozenset(blocks.values()),
        sites=frozenset(sites.values()),
        scope_items=scope_items,
        criteria=criteria,
        requirements=requirements,
        facts=facts,
        extraction_runs=extraction_runs,
    )


# --------------------------------------------------------------------------- #
# Comparacio (pura)                                                             #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Difference:
    section: str
    key: str
    classification: str
    detail: str


@dataclass(frozen=True, slots=True)
class Match:
    section: str
    key: str
    #: `natural_key` | `dedup_key` | `title`.
    matched_by: str


@dataclass(frozen=True, slots=True)
class SectionCount:
    expected: int
    found: int
    matched: int
    #: Coincidencies per via (`matched_by`); buit si cap.
    matched_by: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CitationCount:
    expected: int
    found: int


@dataclass(slots=True)
class AcceptanceReport:
    case: str
    tender_id: str
    external_id: str
    documents_expected: int
    documents_present: int
    #: Documents de la base sense fitxer vigent: no es poden comparar amb cap SHA.
    unverifiable_documents: int
    roles: SectionCount
    sections: dict[str, SectionCount] = field(default_factory=dict)
    citations: dict[str, CitationCount] = field(default_factory=dict)
    matches: list[Match] = field(default_factory=list)
    differences: list[Difference] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Diferencies per classificacio, sempre amb les cinc claus."""
        counts = dict.fromkeys(CLASSIFICATIONS, 0)
        counts.update(Counter(d.classification for d in self.differences))
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "tender_id": self.tender_id,
            "external_id": self.external_id,
            "documents": {
                "present": self.documents_present,
                "expected": self.documents_expected,
                "unverifiable": self.unverifiable_documents,
            },
            "roles": asdict(self.roles),
            "sections": {name: asdict(count) for name, count in self.sections.items()},
            "citations": {name: asdict(count) for name, count in self.citations.items()},
            "counts": self.counts(),
            "matches": [asdict(m) for m in self.matches],
            "differences": [asdict(d) for d in self.differences],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def render_table(self) -> str:
        def vias(count: SectionCount) -> str:
            return " ".join(f"{via}={n}" for via, n in count.matched_by.items()) or "-"

        lines = [
            f"ACCEPTACIO {self.case} · licitacio {self.tender_id} ({self.external_id})",
            f"Documents presents: {self.documents_present}/{self.documents_expected} "
            "(per SHA-256 de l'oracle; mai cap fitxer real) · sense fitxer vigent: "
            f"{self.unverifiable_documents}",
            "",
            f"{'seccio':<16} {'esperats':>8} {'trobats':>8} {'coincideixen':>12}  via",
            f"{'roles':<16} {self.roles.expected:>8} {self.roles.found:>8} "
            f"{self.roles.matched:>12}  {vias(self.roles)}",
        ]
        lines += [
            f"{name:<16} {count.expected:>8} {count.found:>8} {count.matched:>12}  {vias(count)}"
            for name, count in self.sections.items()
        ]
        lines += ["", "Cites per document (trobades/esperades):"]
        lines += [
            f"  {name:<10} {count.found}/{count.expected}" for name, count in self.citations.items()
        ]
        lines += ["", "Diferencies per classificacio:"]
        lines += [f"  {name:<24} {total}" for name, total in self.counts().items()]
        lines += ["", f"Diferencies ({len(self.differences)}):"]
        lines += [
            f"  {d.section:<16} {d.classification:<24} {d.key:<16} {d.detail}"
            for d in self.differences
        ]
        return "\n".join(lines)


def _label(key: Any) -> str:
    if isinstance(key, tuple):
        return " / ".join(_label(part) for part in key)
    text = str(key)
    # Un `dedup_key` es un SHA-256: no cal sencer per reconeixer-lo.
    return (
        text[:12] + "..."
        if len(text) == 64 and all(c in "0123456789abcdef" for c in text)
        else text
    )


def _missing_detail(entity: GoldEntity, present: set[str], kind: str) -> str:
    cited = ", ".join(sorted(entity.docs)) or "cap document"
    here = sorted(entity.docs & present)
    if not here:
        return f"citat a {cited}; cap d'aquests documents es a la base"
    if kind == UNVERIFIABLE:
        return f"citat a {cited}; presents: {', '.join(here)}; cap run d'extraccio vigent"
    return f"citat a {cited}; presents: {', '.join(here)}"


def _same_file(found: SnapshotDocument, expected: dict[str, Any]) -> bool:
    """El mateix fitxer pel nom (ruta del gold o nom visible), sense mirar el SHA."""
    return found.path == expected["path"] or found.name in (expected["name"], expected["path"])


def _compare_documents(
    snapshot: TenderSnapshot, gold_documents: list[dict[str, Any]], differences: list[Difference]
) -> tuple[set[str], int]:
    """Presents per SHA; el mateix fitxer amb un altre SHA es diu, no es força; sense
    fitxer vigent no es pot comparar (`unverifiable`)."""
    with_bytes = [d for d in snapshot.documents if d.sha256]
    by_sha = {d.sha256: d for d in with_bytes}
    present: set[str] = set()
    for expected in gold_documents:
        if expected["sha256"] in by_sha:
            present.add(expected["key"])
            continue
        same = next((d for d in with_bytes if _same_file(d, expected)), None)
        if same is not None:
            differences.append(
                Difference(
                    "documents",
                    expected["key"],
                    ORACLE_OUTDATED,
                    f"el mateix fitxer {expected['path']!r} amb un altre SHA: oracle "
                    f"{expected['sha256']}, base {same.sha256}",
                )
            )
        else:
            differences.append(
                Difference(
                    "documents",
                    expected["key"],
                    NOT_IN_SOURCE_DOCS,
                    f"{expected['path']!r} ({expected['sha256'][:12]}...) no es a la base",
                )
            )
    gold_shas = {d["sha256"] for d in gold_documents}
    unverifiable = 0
    for found in snapshot.documents:
        if found.sha256 is None:
            unverifiable += 1
            differences.append(
                Difference(
                    "documents",
                    found.name,
                    UNVERIFIABLE,
                    "sense fitxer vigent: no es pot comparar amb cap SHA de l'oracle",
                )
            )
            continue
        if found.sha256 in gold_shas:
            continue
        if any(_same_file(found, expected) for expected in gold_documents):
            continue  # ja dit com a `oracle_outdated` pel mateix fitxer
        differences.append(
            Difference(
                "documents",
                found.name,
                ORACLE_OUTDATED,
                f"document a la base que l'oracle no coneix ({found.sha256[:12]}...)",
            )
        )
    return present, unverifiable


def _compare_roles(
    snapshot: TenderSnapshot,
    oracle: Oracle,
    key_by_sha: dict[str, str],
    differences: list[Difference],
) -> SectionCount:
    by_sha = {d.sha256: d for d in snapshot.documents if d.sha256}
    found = matched = 0
    for sha, role in oracle.roles.items():
        label = key_by_sha.get(sha, sha[:12] + "...")
        document = by_sha.get(sha)
        if document is None:
            differences.append(
                Difference(
                    "roles",
                    label,
                    NOT_IN_SOURCE_DOCS,
                    f"rol {role!r} no comparable: el document no es a la base",
                )
            )
            continue
        found += 1
        if not document.classified:
            differences.append(
                Difference(
                    "roles",
                    label,
                    UNVERIFIABLE,
                    f"rol {role!r} no verificable: cap classificacio global vigent sobre "
                    "aquest fitxer",
                )
            )
        elif document.role == role:
            matched += 1
        else:
            differences.append(
                Difference(
                    "roles",
                    label,
                    EXTRACTION_MISSED,
                    f"rol esperat {role!r}; efectiu {document.role!r} ({document.role_source})",
                )
            )
    return SectionCount(
        expected=len(oracle.roles),
        found=found,
        matched=matched,
        matched_by={MATCH_NATURAL_KEY: matched} if matched else {},
    )


def _compare_section(
    section: str,
    entities: list[GoldEntity],
    snapshot: TenderSnapshot,
    present: set[str],
    matches: list[Match],
    differences: list[Difference],
) -> SectionCount:
    have = getattr(snapshot, _SNAPSHOT_ATTR[section])
    extracted = section in EXTRACTED_SECTIONS
    consumed: set[Any] = set()
    via_of: dict[str, str] = {}

    # Primera via: la clau del gold (natural o `dedup_key`).
    for entity in entities:
        if entity.key in have and entity.key not in consumed:
            consumed.add(entity.key)
            via_of[entity.label] = MATCH_DEDUP_KEY if extracted else MATCH_NATURAL_KEY
    # Segona via (nomes extrets): el titol normalitzat de l'extractor.
    if extracted:
        by_title: dict[str, list[str]] = {}
        for key, title in have.items():
            if key not in consumed:
                by_title.setdefault(title, []).append(key)
        for entity in entities:
            if entity.label in via_of or entity.title_key is None:
                continue
            candidates = by_title.get(entity.title_key)
            if candidates:
                consumed.add(candidates.pop(0))
                via_of[entity.label] = MATCH_TITLE

    for entity in entities:
        via = via_of.get(entity.label)
        if via is not None:
            matches.append(Match(section, entity.label, via))
            continue
        if not entity.docs & present:
            kind = NOT_IN_SOURCE_DOCS
        elif extracted and snapshot.extraction_runs == 0:
            kind = UNVERIFIABLE
        else:
            kind = MISSING_KIND[section]
        differences.append(
            Difference(section, entity.label, kind, _missing_detail(entity, present, kind))
        )
    for key in sorted(set(have) - consumed, key=_label):
        detail = "a la base, no a l'oracle"
        if extracted:
            detail += f": titol {have[key]!r}"
        differences.append(Difference(section, _label(key), ORACLE_OUTDATED, detail))
    return SectionCount(
        expected=len(entities),
        found=len(have),
        matched=len(via_of),
        matched_by=dict(Counter(via_of.values())),
    )


def compare(snapshot: TenderSnapshot, oracle: Oracle) -> AcceptanceReport:
    """Pura: cap sessio, cap E/S, cap mutacio del snapshot ni de l'oracle."""
    differences: list[Difference] = []
    matches: list[Match] = []
    gold_documents = oracle.documents

    present, unverifiable_documents = _compare_documents(snapshot, gold_documents, differences)
    key_by_sha = {d["sha256"]: d["key"] for d in gold_documents}
    roles = _compare_roles(snapshot, oracle, key_by_sha, differences)
    sections = {
        section: _compare_section(
            section, gold_entities(oracle, section), snapshot, present, matches, differences
        )
        for section in SECTIONS
    }

    # Cites per document: cobertura, com a recompte.
    expected_refs = expected_citations(oracle)
    by_sha = {d.sha256: d for d in snapshot.documents if d.sha256}
    citations = {
        expected["key"]: CitationCount(
            expected=expected_refs.get(expected["key"], 0),
            found=by_sha[expected["sha256"]].citations if expected["sha256"] in by_sha else 0,
        )
        for expected in gold_documents
    }

    return AcceptanceReport(
        case=oracle.case,
        tender_id=snapshot.tender_id,
        external_id=snapshot.external_id,
        documents_expected=len(gold_documents),
        documents_present=len(present),
        unverifiable_documents=unverifiable_documents,
        roles=roles,
        sections=sections,
        citations=citations,
        matches=matches,
        differences=differences,
    )


# --------------------------------------------------------------------------- #
# Nomes lectura                                                                 #
# --------------------------------------------------------------------------- #


def validate_database_url(raw: str | None, *, allow_password: bool = False) -> URL:
    """La DSN, comprovada abans de connectar. Res d'ambigu."""
    if not raw:
        raise AcceptanceError(
            "Falta la base: --database-url (sense contrasenya) o DATABASE_URL a la sessio."
        )
    try:
        url = make_url(raw)
    except (ArgumentError, ValueError):
        raise AcceptanceError("La DSN no es valida.") from None
    if url.drivername not in _DRIVERS:
        raise AcceptanceError(
            "La DSN ha de fer servir postgresql+asyncpg (o sqlite+aiosqlite per a proves)."
        )
    if url.query:
        raise AcceptanceError("La DSN no pot portar parametres: podrien canviar la base.")
    if url.password and not allow_password:
        raise AcceptanceError(
            "La DSN no pot portar contrasenya a la linia d'ordres (quedaria a l'historial): "
            "posa-la a PGPASSWORD o la DSN sencera a DATABASE_URL, nomes a la sessio."
        )
    return url


def resolve_database_url(cli_value: str | None, environ: dict[str, str] | None = None) -> URL:
    environ = os.environ if environ is None else environ
    if cli_value:
        return validate_database_url(cli_value)
    return validate_database_url(environ.get("DATABASE_URL"), allow_password=True)


async def open_read_only(session: AsyncSession) -> None:
    """Marca la transaccio en curs com a NOMES lectura (PostgreSQL).

    Ha de ser la primera sentencia de la transaccio. A SQLite no hi ha cap
    equivalent per transaccio: la garantia es `read_only_session` (`PRAGMA
    query_only`) i que ningu no fa commit.
    """
    if session.bind is not None and session.bind.dialect.name == "postgresql":
        await session.execute(sa.text("SET TRANSACTION READ ONLY"))


async def _refuse_commit() -> None:
    raise ReadOnlyViolation("Sessio de nomes lectura: cap commit.")


@asynccontextmanager
async def read_only_session(
    url: str | URL, *, search_path: str | None = None
) -> AsyncIterator[AsyncSession]:
    """Una sessio que no pot escriure, sobre un motor propi que es tanca en sortir.

    La garantia va a CADA connexio, no nomes a la primera transaccio (amb
    `NullPool`, un rollback tanca la connexio i la seguent sentencia n'obre una
    altra): a PostgreSQL, `default_transaction_read_only=on` com a parametre del
    servidor; a SQLite, `PRAGMA query_only = ON` en l'esdeveniment `connect`.
    `SET TRANSACTION READ ONLY` i el `commit` refusat s'hi afegeixen.

    `search_path` (nomes PostgreSQL) serveix per llegir un schema que no es el per
    defecte: el cas de la suite de proves, que aïlla cada test en un schema propi.
    """
    parsed = url if isinstance(url, URL) else validate_database_url(url, allow_password=True)
    connect_args: dict[str, Any] = {}
    if parsed.drivername == "postgresql+asyncpg":
        server_settings = {"default_transaction_read_only": "on"}
        if search_path:
            server_settings["search_path"] = search_path
        connect_args = {"server_settings": server_settings}
    engine = create_async_engine(parsed, poolclass=NullPool, connect_args=connect_args)
    if parsed.drivername.startswith("sqlite"):

        @sa.event.listens_for(engine.sync_engine, "connect")
        def _query_only(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only = ON")
            cursor.close()

    try:
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await open_read_only(session)
            session.commit = _refuse_commit  # type: ignore[method-assign]
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


async def resolve_tender(
    session: AsyncSession, *, tender_id: uuid.UUID | None, external_id: str | None
) -> uuid.UUID:
    if tender_id is not None:
        if await session.get(Tender, tender_id) is None:
            raise AcceptanceError(f"Licitacio {tender_id} no trobada.")
        return tender_id
    ids = list(await session.scalars(sa.select(Tender.id).where(Tender.external_id == external_id)))
    if len(ids) != 1:
        raise AcceptanceError(
            f"L'external_id {external_id!r} resol a {len(ids)} licitacions; n'ha de ser una."
        )
    return ids[0]


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.lescala_acceptance",
        description="Compara una licitacio de la base amb l'oracle public de L'Escala. "
        "Nomes llegeix.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="DSN SENSE contrasenya (per defecte, DATABASE_URL de la sessio)",
    )
    parser.add_argument("--case", default=DEFAULT_CASE, help="cas del gold (lescala-2026)")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--tender-id", type=uuid.UUID, default=None, help="id de la licitacio")
    which.add_argument("--external-id", default=None, help="external_id EXACTE de la licitacio")
    parser.add_argument("--json", action="store_true", help="informe en JSON (per defecte, taula)")
    return parser


def _redact(text: str, url: URL) -> str:
    if url.password:
        text = text.replace(str(url.password), "***")
    return text.replace(url.render_as_string(hide_password=False), url.render_as_string())


async def run(args: argparse.Namespace) -> int:
    try:
        url = resolve_database_url(args.database_url)
        oracle = load_oracle(args.case)
    except (AcceptanceError, FileNotFoundError) as exc:
        print(f"No es pot executar: {exc}")
        return EXIT_CONFIG
    try:
        async with read_only_session(url) as session:
            tender_id = await resolve_tender(
                session, tender_id=args.tender_id, external_id=args.external_id
            )
            snapshot = await load_snapshot(session, tender_id)
    except AcceptanceError as exc:
        print(f"No es pot executar: {exc}")
        return EXIT_INPUT
    except Exception as exc:  # noqa: BLE001 - l'error surt, pero sense credencials
        print(f"Error ({type(exc).__name__}): {_redact(str(exc), url)}")
        return 1
    report = compare(snapshot, oracle)
    print(report.to_json() if args.json else report.render_table())
    return EXIT_OK


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
