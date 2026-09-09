"""Corregeix a quin Tender apunta un cas del gold, buscant-lo per l'evidencia.

    python -m app.evaluation.gold_provenance --dataset gold-v2 --case 1-2026 \\
        --promote gold-v2.1 --reviewer AlbertTuvi1

El Tender NO es tria pel numero d'expedient. L'expedient el posa cada organ de
contractacio pel seu compte i es repeteix entre organs; es precisament aixo el
que va lligar el gold al document equivocat. Aqui es busca el Tender que conte
**de veritat** l'evidencia: el document citat, la pagina citada i la cita
literal de cada anotacio del cas, mes els codis CPV quan l'anotacio en porta.

Si la cerca no dona exactament un Tender, no s'escriu res. Amb zero, el gold
apunta a un document que no existeix i cal mirar-s'ho; amb mes d'un, triar seria
arbitrari i el gold ha de dir per que apunta on apunta.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa

from app.evaluation.dataset import GoldCase, GoldDataset, save_dataset
from app.evaluation.dataset_v2 import load_gold
from app.evaluation.provenance import retarget_case
from app.services.evidence_validation import locate_quote

DEFAULT_REASON = (
    "L'expedient estava duplicat entre organs de contractacio i el gold havia "
    "quedat vinculat al Tender equivocat. Nomes canvia la proveniencia: cap "
    "anotacio, cita, pagina, valor ni estat de revisio s'ha tocat."
)


@dataclass(frozen=True, slots=True)
class Candidate:
    tender_id: object
    external_id: str
    expediente_number: str | None
    title: str | None
    #: Quantes de les cites del cas s'han localitzat en aquest Tender.
    located: int
    total: int
    cpv_ok: bool

    @property
    def is_exact(self) -> bool:
        return self.located == self.total and self.cpv_ok


async def find_by_evidence(session, case: GoldCase) -> list[Candidate]:
    """Tenders on TOTA l'evidencia del cas es localitza de veritat.

    Es mira contra qualsevol Tender del corpus, no nomes contra els que
    comparteixen expedient: el que ha de decidir es el text, no la numeracio.
    """
    items = [*case.requirements, *case.award_criteria]
    citations = [
        (item.document_name, item.page_number, item.quote) for item in items
    ] + [
        (evidence.document_name, evidence.page_number, evidence.quote)
        for item in items
        for evidence in getattr(item, "extra_evidence", [])
    ]
    cpv_codes = sorted(
        {
            code
            for item in case.requirements
            for code in ((item.value or {}).get("cpv_codes") or [])
        }
    )

    rows = (
        await session.execute(
            sa.text(
                """
                SELECT t.id, t.external_id, t.expediente_number, t.title,
                       td.name AS document_name, p.page_number, p.text
                FROM tenders t
                JOIN tender_documents td ON td.tender_id = t.id
                JOIN tender_document_files f ON f.tender_document_id = td.id
                JOIN tender_document_extractions e
                  ON e.content_id = f.content_id AND e.is_current
                JOIN tender_document_pages p ON p.extraction_id = e.id
                WHERE td.name = ANY(:docs) AND p.page_number = ANY(:pages)
                """
            ),
            {
                "docs": sorted({doc for doc, _, _ in citations}),
                "pages": sorted({page for _, page, _ in citations}),
            },
        )
    ).all()

    pages: dict[object, dict[tuple[str, int], str]] = {}
    meta: dict[object, tuple[str, str | None, str | None]] = {}
    for tender_id, external_id, expediente, title, document_name, page_number, text in rows:
        pages.setdefault(tender_id, {})[(document_name, page_number)] = text
        meta[tender_id] = (external_id, expediente, title)

    candidates: list[Candidate] = []
    for tender_id, by_page in pages.items():
        located = sum(
            1
            for document_name, page_number, quote in citations
            if (text := by_page.get((document_name, page_number))) is not None
            and locate_quote(text, quote) is not None
        )
        if not located:
            continue
        joined = "\n".join(by_page.values())
        external_id, expediente, title = meta[tender_id]
        candidates.append(
            Candidate(
                tender_id=tender_id,
                external_id=external_id,
                expediente_number=expediente,
                title=title,
                located=located,
                total=len(citations),
                cpv_ok=all(code in joined for code in cpv_codes),
            )
        )
    return sorted(candidates, key=lambda c: (-c.located, str(c.external_id)))


async def run(args: argparse.Namespace) -> int:
    from app.db.session import SessionFactory

    dataset: GoldDataset = load_gold(args.dataset)
    case = dataset.case(args.case)
    if case is None:
        print(f"El cas {args.case!r} no existeix a {args.dataset!r}.")
        return 1

    async with SessionFactory() as session:
        candidates = await find_by_evidence(session, case)

    exact = [candidate for candidate in candidates if candidate.is_exact]
    print(f"Cas {args.case} · {len(candidates)} tender(s) amb alguna cita localitzada:")
    for candidate in candidates:
        mark = "EXACTE" if candidate.is_exact else "parcial"
        print(f"  [{mark}] {candidate.located}/{candidate.total} cites · CPV={candidate.cpv_ok}")
        print(f"           exp {candidate.expediente_number} · {str(candidate.title)[:60]}")
        print(f"           {candidate.external_id}")

    if len(exact) != 1:
        print(
            f"\nNo s'escriu res: cal exactament 1 tender que compleixi tota l'evidencia, "
            f"i n'hi ha {len(exact)}. Triar-ne un seria arbitrari."
        )
        return 1

    target = exact[0]
    if target.external_id == case.tender_external_id:
        print("\nEl cas ja apunta al tender correcte: no hi ha res a corregir.")
        return 0

    corrected = retarget_case(
        dataset,
        new_dataset_id=args.promote,
        case_id=args.case,
        new_external_id=target.external_id,
        reviewer=args.reviewer,
        reason=args.reason,
        when=args.date or date.today(),
    )
    path = save_dataset(corrected)
    print(f"\n{corrected.dataset_id} desat a {path}")
    print(f"  derivat de {corrected.derived_from} · versio {corrected.version}")
    print(f"  {args.case}: {case.tender_external_id} -> {target.external_id}")
    print(f"  revisio signada per {args.reviewer} el {corrected.created_at}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation.gold_provenance", description=__doc__
    )
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--promote", required=True, help="identificador de la versio nova")
    parser.add_argument("--reviewer", required=True, help="qui signa la correccio")
    parser.add_argument("--reason", default=DEFAULT_REASON)
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["Candidate", "build_parser", "find_by_evidence", "main", "run"]
