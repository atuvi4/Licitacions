"""Importador guardat de la revisio manual de Palamos (2024-3137). DRY-RUN per defecte.

Llegeix l'annex manual (44 unitats de PPT p22-25) i, contra la base de revisio
dedicada `licitacions_review_palamos`, en construeix el PLA d'importacio:

* cada cita s'ancora a la pagina extreta amb posicions de caracter exactes
  (o, si nomes difereix en espais o cometes tipografiques, amb un mapatge
  deterministic a les posicions originals; mai no s'inventen posicions);
* cada contrapart automatica es resol a EXACTAMENT un requisit extret de la
  run final pel seu titol immutable, o es queda sense resoldre;
* cada fila rep l'operacio de revisio que li tocaria (crear element manual,
  relacio, confirmacio...).

**Per defecte no escriu res.** El mode per defecte es connecta amb
`default_transaction_read_only = on`: la base mateixa rebutjaria qualsevol
escriptura, i a mes diu si la importacio ja es completa.

Amb `--write` (i nomes aixi): revalida el pla, crea o resol l'empresa QA i el
seu actor tecnic (sense acces), i fa TOTA la importacio en UNA transaccio:
materialitzar els requisits automatics, crear els 43 manuals amb la seva cita,
les 6 relacions i afegir la cita de p22 a M09. Si res falla, no queda res.
Tornar-ho a executar un cop feta la importacio no escriu res.

Guardes (totes han de passar, o no fa res):

* port 5433, base exactament `licitacions_review_palamos`;
* refusa explicitament `licitacions_review_s3`, `licitacions_v3_probe_palamos` i
  el port 5432;
* licitacio `f37d7031-...`, run `2941fbec-...` vigent d'aquella licitacio;
* esquema a `0040_review_layer`.

La DSN surt de `DATABASE_URL` (entorn del proces); no es mostra mai.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import re
import sys
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.enums import RequirementOrigin
from app.models.requirements import Requirement, RequirementExtractionRun
from app.models.tender_documents import (
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import Tender, TenderDocument

TARGET_DATABASE = "licitacions_review_palamos"
TARGET_PORT = 5433
TENDER_ID = uuid.UUID("f37d7031-6d32-404d-8785-b2da7cf6a78e")
RUN_ID = uuid.UUID("2941fbec-cefc-4011-8026-79110d9d1938")
EXPECTED_MIGRATION = "0040_review_layer"
PPT_DOCUMENT = "PPT. PLEC DE PRESCRIPCIONS TÈCNIQUES.pdf"
REFUSED_DATABASES = frozenset({
    "licitacions", "licitacions_review_s3", "licitacions_v3_probe_palamos",
    "licitacions_benchmark", "licitacions_test",
})
REFUSED_PORTS = frozenset({5432})

GAP, PARTIAL, REPRESENTED = "MANUAL_GAP", "MANUAL_PARTIAL", "MANUAL_ALREADY_REPRESENTED"
EXPECTED_STATUS = {GAP: 38, PARTIAL: 5, REPRESENTED: 1}
EXPECTED_ROWS = 44
#: Contraparts que el pla ha de relacionar: parcials, la ja representada i M21.
RELATED_TO_IDS = frozenset({"M21"})

_QUOTES = {"’": "'", "‘": "'", "´": "'", "“": '"', "”": '"', "«": '"', "»": '"'}


class ImportGuardError(RuntimeError):
    """Una guarda no passa: no es fa res."""


# --------------------------------------------------------------------------- #
# Guardes                                                                       #
# --------------------------------------------------------------------------- #


def validate_target_url(raw: str | None) -> URL:
    """Nomes la base dedicada, al 5433. Mai res protegit ni el 5432."""
    if not raw:
        raise ImportGuardError("Cal DATABASE_URL a l'entorn del proces.")
    url = make_url(raw)
    if url.port in REFUSED_PORTS:
        raise ImportGuardError(f"El port {url.port} esta prohibit.")
    if url.port != TARGET_PORT:
        raise ImportGuardError(f"Nomes el port {TARGET_PORT}.")
    if url.database in REFUSED_DATABASES:
        raise ImportGuardError(f"La base {url.database!r} esta protegida.")
    if url.database != TARGET_DATABASE:
        raise ImportGuardError(f"Nomes la base {TARGET_DATABASE!r}.")
    return url


async def check_state(
    session: AsyncSession,
    *,
    tender_id: uuid.UUID = TENDER_ID,
    run_id: uuid.UUID = RUN_ID,
    expected_migration: str = EXPECTED_MIGRATION,
) -> None:
    """Esquema, licitacio i run exactes; la run ha de ser la vigent de la licitacio."""
    alembic = sa.table("alembic_version", sa.column("version_num"))
    migration = await session.scalar(sa.select(alembic.c.version_num))
    if migration != expected_migration:
        raise ImportGuardError(f"Esquema a {migration!r}; cal {expected_migration!r}.")
    if await session.get(Tender, tender_id) is None:
        raise ImportGuardError("La licitacio de Palamos no hi es.")
    run = await session.get(RequirementExtractionRun, run_id)
    if run is None:
        raise ImportGuardError("La run final no hi es.")
    if run.tender_id != tender_id:
        raise ImportGuardError("La run no es d'aquesta licitacio.")
    if not run.is_current:
        raise ImportGuardError("La run final no es la vigent.")


# --------------------------------------------------------------------------- #
# Annex                                                                         #
# --------------------------------------------------------------------------- #


def load_annex(path: Path) -> list[dict[str, str]]:
    """Les 44 unitats, amb els invariants comprovats. Si no quadren, s'atura."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != EXPECTED_ROWS:
        raise ImportGuardError(f"L'annex te {len(rows)} files; se n'esperen {EXPECTED_ROWS}.")
    counts = Counter(row["status"] for row in rows)
    if dict(counts) != EXPECTED_STATUS:
        raise ImportGuardError(f"Estats de l'annex inesperats: {dict(counts)}.")
    if any(row["mandatory"] != "yes" for row in rows):
        raise ImportGuardError("Totes les unitats han de ser obligatories.")
    ids = [row["manual_id"] for row in rows]
    if len(set(ids)) != len(ids):
        raise ImportGuardError("Identificadors manuals repetits.")
    return rows


def pages_of(value: str) -> list[int]:
    """`22` o `22-23`: les pagines on pot ser la cita, sense inventar-ne cap altra."""
    if re.fullmatch(r"\d+", value):
        return [int(value)]
    match = re.fullmatch(r"(\d+)-(\d+)", value)
    if match and int(match.group(1)) < int(match.group(2)):
        return list(range(int(match.group(1)), int(match.group(2)) + 1))
    raise ImportGuardError(f"Pagina de l'annex no valida: {value!r}.")


# --------------------------------------------------------------------------- #
# Ancoratge de cites                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Anchor:
    exact: bool
    start: int | None
    end: int | None
    diagnostic: str

    @property
    def resolved(self) -> bool:
        return self.start is not None


def _normalised(text: str) -> tuple[str, list[int]]:
    """Text amb espais col·lapsats i cometes tipografiques planes, i l'index original
    de cada caracter del resultat."""
    out: list[str] = []
    index: list[int] = []
    in_space = False
    for position, char in enumerate(text):
        char = _QUOTES.get(char, char)
        if char.isspace():
            if in_space or not out:
                continue
            char, in_space = " ", True
        else:
            in_space = False
        out.append(char)
        index.append(position)
    while out and out[-1] == " ":
        out.pop()
        index.pop()
    return "".join(out), index


def anchor_quote(page_text: str, quote: str) -> Anchor:
    """Posicions EXACTES de la cita a la pagina, o res.

    1. Coincidencia exacta i unica: posicions directes.
    2. Si no, coincidencia unica despres de col·lapsar espais i aplanar cometes
       tipografiques: les posicions es tradueixen a l'ORIGINAL, i el tros citat es
       el text real de la pagina (amb els seus salts de linia i cometes).
    Qualsevol altra cosa (cap coincidencia, o mes d'una) queda sense resoldre.
    """
    quote = quote.strip()
    if not quote:
        return Anchor(False, None, None, "cita buida")
    exact_hits = [m.start() for m in re.finditer(re.escape(quote), page_text)]
    if len(exact_hits) == 1:
        return Anchor(True, exact_hits[0], exact_hits[0] + len(quote), "exacta")
    if len(exact_hits) > 1:
        return Anchor(False, None, None, f"ambigua: {len(exact_hits)} coincidencies exactes")
    norm_page, index = _normalised(page_text)
    norm_quote, _ = _normalised(quote)
    hits = [m.start() for m in re.finditer(re.escape(norm_quote), norm_page)]
    if len(hits) == 1:
        start = index[hits[0]]
        end = index[hits[0] + len(norm_quote) - 1] + 1
        return Anchor(False, start, end, "espais/cometes normalitzats; posicions originals")
    if len(hits) > 1:
        return Anchor(False, None, None, f"ambigua: {len(hits)} coincidencies normalitzades")
    return Anchor(False, None, None, "no es a la pagina")


# --------------------------------------------------------------------------- #
# Resolucio contra la base                                                      #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PageRef:
    extraction_id: uuid.UUID
    page_id: uuid.UUID
    page_number: int
    text: str


async def load_ppt_pages(
    session: AsyncSession, tender_id: uuid.UUID, document_name: str, numbers: set[int]
) -> dict[int, PageRef]:
    """Les pagines de l'extraccio VIGENT del fitxer vigent del PPT d'aquesta licitacio."""
    rows = await session.execute(
        sa.select(
            TenderDocumentPage.extraction_id,
            TenderDocumentPage.id,
            TenderDocumentPage.page_number,
            TenderDocumentPage.text,
        )
        .join(TenderDocumentExtraction,
              TenderDocumentExtraction.id == TenderDocumentPage.extraction_id)
        .join(TenderDocumentFile,
              TenderDocumentFile.content_id == TenderDocumentExtraction.content_id)
        .join(TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id)
        .where(
            TenderDocument.tender_id == tender_id,
            TenderDocument.name == document_name,
            TenderDocumentFile.is_current.is_(True),
            TenderDocumentExtraction.is_current.is_(True),
        )
    )
    pages: dict[int, PageRef] = {}
    for extraction_id, page_id, number, text in rows:
        if number in numbers:
            if number in pages:
                raise ImportGuardError(f"Mes d'una pagina {number} vigent al PPT.")
            pages[number] = PageRef(uuid.UUID(str(extraction_id)), uuid.UUID(str(page_id)),
                                    number, text or "")
    return pages


async def resolve_requirement(
    session: AsyncSession, run_id: uuid.UUID, title: str
) -> tuple[str, uuid.UUID | None, int]:
    """Exactament un requisit EXTRET de la run amb aquest titol immutable."""
    rows = (
        await session.scalars(
            sa.select(Requirement.id).where(
                Requirement.run_id == run_id,
                Requirement.origin == RequirementOrigin.EXTRACTED,
                Requirement.title == title,
            )
        )
    ).all()
    if len(rows) == 1:
        return "resolved", rows[0], 1
    return ("unresolved_none" if not rows else "unresolved_ambiguous"), None, len(rows)


# --------------------------------------------------------------------------- #
# Pla                                                                           #
# --------------------------------------------------------------------------- #


PLAN_COLUMNS = (
    "manual_id", "status", "page", "section", "planned_action", "citation_resolved",
    "exact_quote_match", "char_start", "char_end", "related_requirement_id",
    "related_requirement_title", "relation_type", "mapping_status", "diagnostic",
)


@dataclass
class PlanRow:
    manual_id: str
    status: str
    page: int | None
    section: str
    planned_action: str
    citation_resolved: bool
    exact_quote_match: bool
    char_start: int | None
    char_end: int | None
    related_requirement_id: str | None
    related_requirement_title: str | None
    relation_type: str | None
    mapping_status: str
    diagnostic: str
    extraction_id: str | None = field(default=None, repr=False)
    page_id: str | None = field(default=None, repr=False)


def _action(row: dict[str, str]) -> tuple[str, str | None, bool]:
    """(accio prevista, tipus de relacio, cal contrapart automatica)."""
    if row["status"] == REPRESENTED:
        return "no_create; confirm automated item + note", None, True
    if row["status"] == PARTIAL:
        return "create_manual_item + citation + partial_match_of", "partial_match_of", True
    if row["manual_id"] in RELATED_TO_IDS:
        return "create_manual_item + citation + related_to", "related_to", True
    return "create_manual_item + citation", None, False


async def build_plan(
    session: AsyncSession,
    rows: list[dict[str, str]],
    *,
    tender_id: uuid.UUID = TENDER_ID,
    run_id: uuid.UUID = RUN_ID,
    document_name: str = PPT_DOCUMENT,
) -> list[PlanRow]:
    wanted = {page for row in rows for page in pages_of(row["page"])}
    pages = await load_ppt_pages(session, tender_id, document_name, wanted)
    plan: list[PlanRow] = []
    for row in rows:
        action, relation, needs_counterpart = _action(row)
        candidates = pages_of(row["page"])
        found = [(n, anchor_quote(pages[n].text, row["evidence_quote"]))
                 for n in candidates if n in pages]
        located = [(n, a) for n, a in found if a.resolved]
        missing_pages = [n for n in candidates if n not in pages]
        if len(located) == 1:
            page_number, anchor = located[0]
            diagnostic = anchor.diagnostic
        elif len(located) > 1:
            page_number, anchor = None, Anchor(False, None, None, "")
            diagnostic = f"ambigua entre pagines {[n for n, _ in located]}"
        else:
            page_number, anchor = None, Anchor(False, None, None, "")
            reasons = [f"p{n}: {a.diagnostic}" for n, a in found]
            reasons += [f"p{n}: pagina no trobada" for n in missing_pages]
            diagnostic = "; ".join(reasons)

        mapping_status, related_id, related_title = "not_required", None, None
        title = row["related_automated_requirement"].strip()
        if needs_counterpart:
            if not title:
                mapping_status = "unresolved_missing_title"
            else:
                mapping_status, found_id, _count = await resolve_requirement(
                    session, run_id, title)
                related_id = str(found_id) if found_id else None
                related_title = title
        elif title:
            informative, found_id, _count = await resolve_requirement(session, run_id, title)
            mapping_status = f"informative_only ({informative})"
            related_title = title
            related_id = str(found_id) if found_id else None

        page_ref = pages.get(page_number) if page_number else None
        plan.append(PlanRow(
            manual_id=row["manual_id"],
            status=row["status"],
            page=page_number,
            section=row["section"],
            planned_action=action,
            citation_resolved=anchor.resolved,
            exact_quote_match=anchor.exact,
            char_start=anchor.start,
            char_end=anchor.end,
            related_requirement_id=related_id,
            related_requirement_title=related_title,
            relation_type=relation,
            mapping_status=mapping_status,
            diagnostic=diagnostic,
            extraction_id=str(page_ref.extraction_id) if page_ref else None,
            page_id=str(page_ref.page_id) if page_ref else None,
        ))
    return plan


def summarise(plan: list[PlanRow], automated_requirements: int) -> dict[str, Any]:
    creates = sum(1 for p in plan if p.planned_action.startswith("create_manual_item"))
    required = [p for p in plan if p.mapping_status not in ("not_required",)
                and not p.mapping_status.startswith("informative_only")]
    return {
        "rows": len(plan),
        "status": dict(Counter(p.status for p in plan)),
        "citations_resolved": sum(p.citation_resolved for p in plan),
        "citations_exact": sum(p.exact_quote_match for p in plan),
        "citations_normalised": sum(p.citation_resolved and not p.exact_quote_match for p in plan),
        "citations_unresolved": [p.manual_id for p in plan if not p.citation_resolved],
        "manual_creates": creates,
        "relations": dict(Counter(p.relation_type for p in plan if p.relation_type)),
        "confirms": sum(p.status == REPRESENTED for p in plan),
        "mappings_required": len(required),
        "mappings_resolved": sum(p.mapping_status == "resolved" for p in required),
        "mappings_unresolved": [p.manual_id for p in required if p.mapping_status != "resolved"],
        "automated_requirements": automated_requirements,
        "expected_canonical_requirements": automated_requirements + creates,
    }


def ready(summary: dict[str, Any]) -> bool:
    return (
        summary["citations_resolved"] == EXPECTED_ROWS
        and not summary["mappings_unresolved"]
        and summary["manual_creates"] == EXPECTED_STATUS[GAP] + EXPECTED_STATUS[PARTIAL]
    )


# --------------------------------------------------------------------------- #
# Sortides                                                                      #
# --------------------------------------------------------------------------- #


def write_outputs(out_dir: Path, plan: list[PlanRow], summary: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "palamos_review_import_plan.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=PLAN_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in plan:
            writer.writerow({k: v for k, v in asdict(row).items() if k in PLAN_COLUMNS})
    lines = [
        "# Palamós 2024-3137 · pla d'importació de la revisió manual (DRY-RUN)",
        "",
        f"- Files de l'annex: {summary['rows']} · estats: {summary['status']}",
        f"- Cites ancorades: {summary['citations_resolved']}/{EXPECTED_ROWS} "
        f"(exactes {summary['citations_exact']}, normalitzades {summary['citations_normalised']})",
        f"- Cites sense resoldre: {summary['citations_unresolved'] or 'cap'}",
        f"- Elements manuals a crear: {summary['manual_creates']}",
        f"- Relacions previstes: {summary['relations']}",
        f"- Confirmacions previstes: {summary['confirms']}",
        f"- Contraparts automàtiques: {summary['mappings_resolved']}/"
        f"{summary['mappings_required']} resoltes · sense resoldre: "
        f"{summary['mappings_unresolved'] or 'cap'}",
        f"- Requisits automàtics (run final): {summary['automated_requirements']}",
        f"- Requisits canònics esperats després de la importació: "
        f"{summary['expected_canonical_requirements']} (sense criteris d'adjudicació)",
        f"- Llest per importar: {'SÍ' if ready(summary) else 'NO'}",
        "",
        "Cap escriptura: la connexió es de només lectura.",
    ]
    (out_dir / "palamos_review_import_dry_run_summary.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------- #
# Escriptura (nomes amb --write)                                                #
# --------------------------------------------------------------------------- #

QA_COMPANY_NAME = "Tuvi QA Review"
QA_ACTOR_EMAIL = "qa-import@licitacions.dev"
QA_ACTOR_NAME = "Tuvi QA import (actor tecnic, no huma)"
#: No es cap hash valid: `verify_password` torna False. L'actor no pot entrar mai.
UNUSABLE_PASSWORD = "!no-login:service-actor"
QA_ROLE_VALUE = "technical"
PACKAGE = "palamos_final_review"
EXPECTED_AUTOMATED = 214
EXPECTED_RELATIONS = {"partial_match_of": 5, "related_to": 1}
EXPECTED_CREATES = EXPECTED_STATUS[GAP] + EXPECTED_STATUS[PARTIAL]
#: Tots els requisits de l'annex son del PPT (prescripcions tecniques).
MANUAL_CATEGORY = "technical_requirement"
#: Valors de l'annex, llegits un a un. El text de l'annex ha de coincidir exactament;
#: si canvia, s'atura (no s'interpreta cap text nou).
MANUAL_VALUES: dict[str, tuple[str, list[dict[str, Any]]]] = {
    "M14": ("2x extensions actuals", [
        {"key": "capacitat_extensions", "operator": ">=", "value": 2,
         "unit_label": "x extensions actuals"}]),
    "M15": ("64 trucades", [
        {"key": "trucades_simultanies", "operator": ">=", "value": 64,
         "unit_label": "trucades"}]),
    "M41": ("720p", [
        {"key": "resolucio_video", "operator": ">=", "value": 720, "unit_label": "p"}]),
    "M43": ("200 usuaris; 25 en pantalla", [
        {"key": "usuaris_videoconferencia", "operator": ">=", "value": 200,
         "unit_label": "usuaris"},
        {"key": "usuaris_visibles", "operator": ">=", "value": 25,
         "unit_label": "usuaris en pantalla"}]),
}


class AlreadyImportedError(RuntimeError):
    """La importacio ja es completa: no cal (ni es pot) tornar-la a fer."""


@dataclass(frozen=True)
class QaTenant:
    company: Any
    actor: Any
    membership: Any
    created: bool


def manual_values(row: dict[str, str]) -> list[dict[str, Any]]:
    """Els valors del revisor per a una fila. Mai no s'inventen ni s'interpreten."""
    text = row["numeric_value_unit"].strip()
    known = MANUAL_VALUES.get(row["manual_id"])
    if known is None:
        if text:
            raise ImportGuardError(f"{row['manual_id']}: valor sense mapatge ({text!r}).")
        return []
    if text != known[0]:
        raise ImportGuardError(f"{row['manual_id']}: el valor de l'annex ha canviat.")
    return known[1]


def assert_write_invariants(
    plan: list[PlanRow], summary: dict[str, Any], *, expected_automated: int | None
) -> None:
    """Revalidacio just abans d'escriure. Qualsevol diferencia atura."""
    problems = []
    if summary["status"] != EXPECTED_STATUS:
        problems.append(f"estats {summary['status']}")
    if summary["citations_resolved"] != EXPECTED_ROWS:
        problems.append(f"cites {summary['citations_resolved']}/{EXPECTED_ROWS}")
    if summary["mappings_unresolved"] or summary["mappings_resolved"] != 7:
        problems.append(f"contraparts {summary['mappings_resolved']}/7")
    if summary["manual_creates"] != EXPECTED_CREATES:
        problems.append(f"creacions {summary['manual_creates']}")
    if summary["relations"] != EXPECTED_RELATIONS:
        problems.append(f"relacions {summary['relations']}")
    m09 = [p for p in plan if p.status == REPRESENTED]
    if len(m09) != 1 or not m09[0].planned_action.startswith("no_create"):
        problems.append("M09 no es un no_create")
    if expected_automated is not None and summary["automated_requirements"] != expected_automated:
        problems.append(f"automatics {summary['automated_requirements']}")
    if problems:
        raise ImportGuardError("Invariants del pla: " + "; ".join(problems))


async def ensure_qa_tenant(session: AsyncSession) -> QaTenant:
    """L'empresa QA i el seu actor tecnic. Idempotent; qualsevol conflicte atura."""
    from app.models.enums import MembershipRole
    from app.models.identity import Company, CompanyMembership, User
    from app.services.manual_requirement import REQUIREMENT_EDIT_ROLES

    companies = (await session.scalars(
        sa.select(Company).where(Company.legal_name == QA_COMPANY_NAME))).all()
    actors = (await session.scalars(
        sa.select(User).where(User.email == QA_ACTOR_EMAIL))).all()
    if len(companies) > 1:
        raise ImportGuardError("Hi ha mes d'una empresa QA amb aquest nom.")
    if not companies and not actors:
        company = Company(legal_name=QA_COMPANY_NAME, country="ES",
                          description="Empresa de QA per a la revisio importada de Palamos.")
        actor = User(email=QA_ACTOR_EMAIL, hashed_password=UNUSABLE_PASSWORD,
                     full_name=QA_ACTOR_NAME, is_active=False, is_superuser=False)
        session.add_all([company, actor])
        await session.flush()
        membership = CompanyMembership(user_id=actor.id, company_id=company.id,
                                       role=MembershipRole(QA_ROLE_VALUE), is_default=True)
        session.add(membership)
        await session.flush()
        return QaTenant(company, actor, membership, created=True)
    if not (companies and actors):
        raise ImportGuardError("Nomes existeix l'empresa QA o nomes l'actor: identitat ambigua.")
    company, actor = companies[0], actors[0]
    if actor.hashed_password != UNUSABLE_PASSWORD or actor.is_active or actor.is_superuser:
        raise ImportGuardError("L'actor QA existent no es un actor tecnic sense acces.")
    membership = await session.scalar(sa.select(CompanyMembership).where(
        CompanyMembership.user_id == actor.id, CompanyMembership.company_id == company.id))
    if membership is None or membership.role not in REQUIREMENT_EDIT_ROLES:
        raise ImportGuardError("L'actor QA no te una pertinenca d'edicio a l'empresa QA.")
    return QaTenant(company, actor, membership, created=False)


async def find_qa_tenant(session: AsyncSession) -> tuple[Any, Any] | None:
    """Lectura: l'empresa i l'actor QA si existeixen (sense crear res)."""
    from app.models.identity import Company, User

    company = await session.scalar(sa.select(Company).where(Company.legal_name == QA_COMPANY_NAME))
    actor = await session.scalar(sa.select(User).where(User.email == QA_ACTOR_EMAIL))
    return (company, actor) if company and actor else None


async def import_status(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID,
    *, expected_manual_ids: set[str], expected_items: int,
) -> str:
    """`none` (res), `complete` (importacio feta) o `unknown` (estat que no es el nostre)."""
    from app.models.review import ReviewItem, ReviewItemRelation, ReviewItemRevision

    items = await session.scalar(sa.select(sa.func.count()).select_from(ReviewItem).where(
        ReviewItem.company_id == company_id, ReviewItem.tender_id == tender_id))
    if not items:
        return "none"
    revisions = (await session.scalars(sa.select(ReviewItemRevision).join(
        ReviewItem, ReviewItem.id == ReviewItemRevision.item_id).where(
        ReviewItem.company_id == company_id, ReviewItem.tender_id == tender_id))).all()
    created = {
        (r.payload_json or {}).get("manual_id")
        for r in revisions
        if r.action.value == "create" and (r.payload_json or {}).get("package") == PACKAGE
        and (r.payload_json or {}).get("manual_id")
    }
    linked = any(
        r.action.value == "link_source" and (r.payload_json or {}).get("package") == PACKAGE
        for r in revisions
    )
    relations = await session.scalar(sa.select(sa.func.count()).select_from(
        ReviewItemRelation).where(
        ReviewItemRelation.company_id == company_id, ReviewItemRelation.tender_id == tender_id,
        ReviewItemRelation.archived_at.is_(None)))
    if (items == expected_items and created == expected_manual_ids and linked
            and relations == sum(EXPECTED_RELATIONS.values())):
        return "complete"
    return "unknown"


RESULT_COLUMNS = (
    "manual_id", "canonical_item_id", "operation", "verification_status", "citation_id",
    "related_item_id", "relation_id", "result",
)


async def execute_import(
    session: AsyncSession,
    *,
    plan: list[PlanRow],
    rows: list[dict[str, str]],
    tenant: QaTenant,
    tender_id: uuid.UUID,
    run_id: uuid.UUID,
    batch_id: uuid.UUID,
    annex_name: str,
    annex_sha256: str,
) -> list[dict[str, Any]]:
    """Tota la importacio de requisits. NO fa commit: tot o res ho decideix qui crida."""
    from app.models.enums import AuditAction, ReviewRelationType, ReviewSourceRole
    from app.models.review import ReviewItem, ReviewItemSource
    from app.schemas.review import ExtractedPageCitation, ManualRequirementCreate
    from app.services import audit, review_actions, review_relations, review_view

    if tenant.company.legal_name != QA_COMPANY_NAME:
        raise ImportGuardError("Nomes l'empresa QA pot rebre aquesta importacio.")
    review_actions.assert_can_review(tenant.membership.role)
    company_id, actor = tenant.company.id, tenant.actor
    by_id = {row["manual_id"]: row for row in rows}
    expected_ids = {p.manual_id for p in plan if p.planned_action.startswith("create")}
    extracted = await session.scalar(sa.select(sa.func.count()).select_from(Requirement).where(
        Requirement.run_id == run_id, Requirement.origin == RequirementOrigin.EXTRACTED))
    status = await import_status(session, company_id, tender_id,
                                 expected_manual_ids=expected_ids,
                                 expected_items=extracted + len(expected_ids))
    if status == "complete":
        raise AlreadyImportedError("La importacio ja esta feta.")
    if status != "none":
        raise ImportGuardError("Ja hi ha revisio d'aquesta empresa que no es la importacio.")

    base = {"package": PACKAGE, "import_batch_id": str(batch_id),
            "source_file": annex_name, "source_file_sha256": annex_sha256}

    async def audit_row(entity_type: str, entity_id: uuid.UUID, summary: str) -> None:
        await audit.record(session, company_id=company_id, user_id=actor.id,
                           action=AuditAction.CREATE, entity_type=entity_type,
                           entity_id=entity_id, summary=f"lot={batch_id} {summary}")

    started = await review_view.start_review(
        session, company_id=company_id, tender_id=tender_id, actor_id=actor.id,
        provenance=base)
    if started.created != extracted:
        raise ImportGuardError(f"S'han materialitzat {started.created} de {extracted}.")
    await audit_row("review", tender_id, f"inici_revisio elements_creats={started.created}")
    automated = dict((await session.execute(
        sa.select(ReviewItemSource.requirement_id, ReviewItemSource.item_id).join(
            ReviewItem, ReviewItem.id == ReviewItemSource.item_id).where(
            ReviewItem.company_id == company_id,
            ReviewItemSource.link_role == ReviewSourceRole.PRIMARY,
            ReviewItemSource.unlinked_at.is_(None)))).all())

    async def latest_citation(item_id: uuid.UUID) -> uuid.UUID:
        return await session.scalar(sa.select(ReviewItemSource.source_ref_id).where(
            ReviewItemSource.item_id == item_id, ReviewItemSource.source_ref_id.is_not(None)
        ).order_by(ReviewItemSource.created_at.desc(), ReviewItemSource.id.desc()).limit(1))

    def citation(p: PlanRow) -> ExtractedPageCitation:
        return ExtractedPageCitation(kind="extracted_page", extraction_id=p.extraction_id,
                                     page_id=p.page_id, char_start=p.char_start,
                                     char_end=p.char_end, location_label=p.section or None)

    results: list[dict[str, Any]] = []
    for p in sorted(plan, key=lambda x: x.manual_id):
        row = by_id[p.manual_id]
        provenance = {**base, "manual_id": p.manual_id}
        if p.status == REPRESENTED:
            target = automated[uuid.UUID(p.related_requirement_id)]
            current = await session.get(ReviewItem, target)
            status_before = current.verification_status
            item = await review_actions.apply_review_action(
                session, company_id=company_id, tender_id=tender_id, item_id=target,
                actor_id=actor.id, expected_revision=current.revision, action="link_source",
                payload={"citation": citation(p).model_dump(mode="json"),
                         "note": f"També ho diu explícitament el PPT p{p.page} "
                                 f"§{p.section} (annex {p.manual_id})."},
                user=actor, provenance=provenance)
            if item.verification_status != status_before:
                raise ImportGuardError("M09 no pot canviar d'estat.")
            await audit_row("review_item", item.id, f"accio=link_source element={item.id}")
            results.append({"manual_id": p.manual_id, "canonical_item_id": str(item.id),
                            "operation": "link_source (sense crear)",
                            "verification_status": item.verification_status.value,
                            "citation_id": str(await latest_citation(item.id)),
                            "related_item_id": "", "relation_id": "", "result": "ok"})
            continue
        data = ManualRequirementCreate(
            title=row["title"], description=row["requirement"] or None,
            category=MANUAL_CATEGORY, obligation="mandatory", modality="mandatory",
            values=manual_values(row), verification_status="unreviewed",
            note=(row["review_note"] or None), citations=[citation(p)])
        item = await review_actions.create_manual_requirement(
            session, company_id=company_id, tender_id=tender_id, user=actor, data=data,
            provenance=provenance)
        await audit_row("review_item", item.id, f"accio=create_manual element={item.id}")
        result = {"manual_id": p.manual_id, "canonical_item_id": str(item.id),
                  "operation": "create_manual_item + citation",
                  "verification_status": item.verification_status.value,
                  "citation_id": str(await latest_citation(item.id)),
                  "related_item_id": "", "relation_id": "", "result": "ok"}
        if p.relation_type:
            target = automated[uuid.UUID(p.related_requirement_id)]
            note = ("Coincidencia parcial amb l'element automatic (annex "
                    f"{p.manual_id})." if p.relation_type == "partial_match_of"
                    else f"Relacionats, no equivalents (annex {p.manual_id}).")
            relation = await review_relations.create_relation(
                session, company_id=company_id, tender_id=tender_id, from_item_id=item.id,
                to_item_id=target, relation_type=ReviewRelationType(p.relation_type),
                note=note, actor_id=actor.id)
            await audit_row("review_relation", relation.id,
                            f"relacio={relation.id} tipus={p.relation_type}")
            result.update(operation=f"create_manual_item + citation + {p.relation_type}",
                          related_item_id=str(target), relation_id=str(relation.id))
        results.append(result)
    return results


async def verify_import(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID,
    results: list[dict[str, Any]], m09_status_before: str | None,
) -> dict[str, Any]:
    """Lectura canonica despres d'escriure. Torna comprovacions i recomptes."""
    from app.models.provenance import TenderSourceRef
    from app.models.review import (
        ReviewItem,
        ReviewItemRelation,
        ReviewItemRevision,
        ReviewItemSource,
    )
    from app.services import review_view

    by_id = {r["manual_id"]: r for r in results}
    listed = await review_view.list_requirements(session, company_id=company_id,
                                                 tender_id=tender_id)
    canonical = {str(c.canonical_id): c for c in listed}
    manual = [canonical[by_id[m]["canonical_item_id"]] for m in by_id if m != "M09"]
    checks: dict[str, Any] = {
        "canonical_requirements": len(listed),
        "provenance": dict(Counter(c.provenance for c in listed)),
        "manual_all_unreviewed": all(c.verification_status == "unreviewed" for c in manual),
        "manual_all_manual": all(c.provenance == "manual" for c in manual),
        "review_items": await session.scalar(sa.select(sa.func.count()).select_from(
            ReviewItem).where(ReviewItem.company_id == company_id)),
        "review_revisions": await session.scalar(sa.select(sa.func.count()).select_from(
            ReviewItemRevision).where(ReviewItemRevision.company_id == company_id)),
        "relations": dict(Counter(r.relation_type.value for r in (await session.scalars(
            sa.select(ReviewItemRelation).where(
                ReviewItemRelation.company_id == company_id))).all())),
        "citations_linked": await session.scalar(sa.select(sa.func.count()).select_from(
            ReviewItemSource).where(ReviewItemSource.company_id == company_id,
                                    ReviewItemSource.source_ref_id.is_not(None))),
        "source_refs": await session.scalar(sa.select(sa.func.count()).select_from(
            TenderSourceRef).where(TenderSourceRef.tender_id == tender_id)),
    }
    if "M15" in by_id:
        m15 = canonical[by_id["M15"]["canonical_item_id"]]
        checks["M15"] = {
            "provenance": m15.provenance, "status": m15.verification_status,
            "values": [(v.value, v.unit_label) for v in m15.values],
            "citation_pages": [e.page for e in m15.evidence],
        }
    for key in ("M06", "M21"):
        if key in by_id:
            item = canonical[by_id[key]["canonical_item_id"]]
            checks[key] = {
                "provenance": item.provenance, "status": item.verification_status,
                "relations": [(r.relation_type, r.relation_label, str(r.other_item_id))
                              for r in item.visible_relations],
            }
    if "M09" in by_id:
        m09 = canonical[by_id["M09"]["canonical_item_id"]]
        checks["M09"] = {
            "provenance": m09.provenance, "status": m09.verification_status,
            "status_unchanged": m09_status_before is None
            or m09.verification_status == m09_status_before,
            "evidence": [(e.page, e.status_label) for e in m09.evidence],
        }
    return checks


def write_import_outputs(out_dir: Path, results: list[dict[str, Any]],
                         checks: dict[str, Any], batch_id: uuid.UUID) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "palamos_review_import_result.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(results)
    lines = [
        "# Palamós 2024-3137 · importació de la revisió de requisits",
        "",
        f"- Lot d'importació: `{batch_id}`",
        f"- Operacions: {len(results)} (manuals creats: "
        f"{sum(r['operation'].startswith('create') for r in results)}, "
        f"M09 amb evidència afegida: {sum(r['operation'].startswith('link') for r in results)})",
        f"- Requisits canònics: {checks['canonical_requirements']} "
        f"· provinença: {checks['provenance']}",
        f"- Relacions: {checks['relations']}",
        f"- Revisions de revisió: {checks['review_revisions']}",
        f"- Cites enllaçades: {checks['citations_linked']}",
        f"- Tots els manuals pendents de revisar: {checks['manual_all_unreviewed']}",
        "",
        "Cap element s'ha verificat automàticament.",
    ]
    (out_dir / "palamos_review_import_summary.md").write_text("\n".join(lines) + "\n",
                                                              encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annex", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--write", action="store_true",
                        help="Escriu la importacio (una sola transaccio, tot o res).")
    return parser


async def _plan_and_summary(session, rows):
    plan = await build_plan(session, rows)
    automated = await session.scalar(
        sa.select(sa.func.count()).select_from(Requirement).where(Requirement.run_id == RUN_ID))
    return plan, summarise(plan, int(automated or 0))


async def run(args: argparse.Namespace) -> int:
    import hashlib

    try:
        url = validate_target_url(os.environ.get("DATABASE_URL"))
        rows = load_annex(args.annex)
    except ImportGuardError as exc:
        print(f"Refusat: {exc}")
        return 2
    connect_args = {} if args.write else {
        "server_settings": {"default_transaction_read_only": "on"}}
    engine = create_async_engine(url, connect_args=connect_args)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                await check_state(session)
            except ImportGuardError as exc:
                print(f"Refusat: {exc}")
                return 2
            plan, summary = await _plan_and_summary(session, rows)
            if not args.write:
                return await _dry_run(session, args, plan, summary)
            return await _write(session, args, rows, plan, summary,
                                hashlib.sha256(args.annex.read_bytes()).hexdigest())
    finally:
        await engine.dispose()


async def _dry_run(session, args, plan, summary) -> int:
    write_outputs(args.out_dir, plan, summary)
    for key, value in summary.items():
        print(f"{key}: {value}")
    found = await find_qa_tenant(session)
    if found:
        company, _actor = found
        expected_ids = {p.manual_id for p in plan if p.planned_action.startswith("create")}
        status = await import_status(
            session, company.id, TENDER_ID, expected_manual_ids=expected_ids,
            expected_items=summary["automated_requirements"] + len(expected_ids))
        print(f"IMPORT STATUS: {status}")
        if status == "complete":
            print("Escriptures proposades: 0 (la importacio ja es completa).")
    else:
        print("IMPORT STATUS: none (encara no hi ha empresa QA)")
    await session.rollback()
    print(f"READY: {'YES' if ready(summary) else 'NO'} (dry-run, cap escriptura)")
    return 0


async def _write(session, args, rows, plan, summary, annex_sha256) -> int:
    try:
        assert_write_invariants(plan, summary, expected_automated=EXPECTED_AUTOMATED)
        tenant = await ensure_qa_tenant(session)
        await session.commit()
    except ImportGuardError as exc:
        await session.rollback()
        print(f"Aturat abans d'escriure: {exc}")
        return 3
    print(f"Empresa QA: {tenant.company.id} (creada ara: {tenant.created})")
    print(f"Actor QA: {tenant.actor.id} rol={tenant.membership.role.value}")
    batch_id = uuid.uuid4()
    try:
        results = await execute_import(
            session, plan=plan, rows=rows, tenant=tenant, tender_id=TENDER_ID, run_id=RUN_ID,
            batch_id=batch_id, annex_name=args.annex.name, annex_sha256=annex_sha256)
        await session.commit()
    except AlreadyImportedError:
        await session.rollback()
        print("Ja importat: no s'ha escrit res.")
        return 0
    except Exception as exc:  # noqa: BLE001 - tot o res, i el motiu surt sense secrets
        await session.rollback()
        print(f"Importacio desfeta sencera ({type(exc).__name__}): {exc}")
        return 1
    checks = await verify_import(session, tenant.company.id, TENDER_ID, results, None)
    write_import_outputs(args.out_dir, results, checks, batch_id)
    print(f"Lot: {batch_id}")
    for key, value in checks.items():
        print(f"{key}: {value}")
    return 0


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
