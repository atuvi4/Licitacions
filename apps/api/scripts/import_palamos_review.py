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

**No escriu res.** El mode per defecte es connecta amb
`default_transaction_read_only = on`: la base mateixa rebutjaria qualsevol
escriptura. El mode d'escriptura necessita `--write` i en aquesta versio es
refusa: s'habilitara en un pas posterior, un cop aprovat el pla.

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
# CLI                                                                           #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--annex", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--write", action="store_true",
                        help="Escriure (DESHABILITAT en aquesta versio).")
    return parser


async def run(args: argparse.Namespace) -> int:
    if args.write:
        print("El mode d'escriptura no esta habilitat en aquesta versio. No s'ha fet res.")
        return 2
    try:
        url = validate_target_url(os.environ.get("DATABASE_URL"))
        rows = load_annex(args.annex)
    except ImportGuardError as exc:
        print(f"Refusat: {exc}")
        return 2
    engine = create_async_engine(
        url, connect_args={"server_settings": {"default_transaction_read_only": "on"}}
    )
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                await check_state(session)
            except ImportGuardError as exc:
                print(f"Refusat: {exc}")
                return 2
            plan = await build_plan(session, rows)
            automated = await session.scalar(
                sa.select(sa.func.count()).select_from(Requirement).where(
                    Requirement.run_id == RUN_ID)
            )
            await session.rollback()
    finally:
        await engine.dispose()
    summary = summarise(plan, int(automated or 0))
    write_outputs(args.out_dir, plan, summary)
    for key, value in summary.items():
        print(f"{key}: {value}")
    print(f"READY: {'YES' if ready(summary) else 'NO'} (dry-run, cap escriptura)")
    return 0


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
