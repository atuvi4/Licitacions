"""Construeix el gold v2 candidat i el deixa a punt per a la revisio humana.

    python -m app.evaluation.gold_v2

Que fa, en ordre:

1. Llegeix `gold-v1-reviewed` **sense modificar-lo**.
2. Adapta mecanicament les anotacions existents a l'esquema v2 (`gold_v2_inherit`).
3. Hi substitueix les que la revisio va deixar en `needs_review` per les
   propostes corregides, i hi afegeix les anotacions noves que la v1 no podia
   representar (`gold_v2_proposals`). Afegir i corregir son coses diferents i
   viatgen per separat: una anotacio nova no toca cap anotacio verificada.
4. Comprova contra la base de dades que **cada cita es localitzable** a la
   pagina que diu. Si alguna no ho es, no escriu res: un gold amb una cita que
   no existeix es pitjor que no tenir-lo.
5. Desa `gold-v2-candidate.json`, genera la pagina de revisio humana i el report
   de cobertura.

El que NO fa: verificar cap anotacio, executar cap model, ni tocar el dataset
d'origen. Les propostes neixen sense signatura i esperen una persona.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.evaluation.dataset import (
    AnnotationStatus,
    GoldDataset,
    GoldRevision,
    load_dataset,
    save_dataset,
)
from app.evaluation.dataset_v2 import (
    GOLD_SCHEMA_VERSION,
    AnnotationOrigin,
    BenchmarkDimension,
    GoldCaseV2,
    GoldDatasetV2,
    GoldItemV2,
    dimensions_of,
    load_dataset_v2,
)
from app.evaluation.gold_v2_inherit import adapt_award_criterion, adapt_requirement
from app.evaluation.gold_v2_proposals import ADDITIONS, PROPOSALS, PROPOSED_BY
from app.services.evidence_validation import locate_quote

SOURCE_DATASET = "gold-v1-reviewed"
CANDIDATE_DATASET = "gold-v2-candidate"
DEFAULT_OUTPUT = Path("var/evaluation")

#: Que es diu quan una dimensio no te cap anotacio verificada que la fixi.
#: Ensenyar 0% seria pitjor que no ensenyar res: un zero es llegeix com una
#: mesura, i aqui no s'ha mesurat res.
NOT_MEASURABLE = "n/d"


@dataclass
class BuildResult:
    dataset: GoldDatasetV2
    #: (case_id::gold_id) de les correccions efectivament aplicades.
    applied: list[str] = field(default_factory=list)
    #: (case_id::gold_id) de les anotacions noves afegides.
    added: list[str] = field(default_factory=list)
    #: Propostes que apunten a una anotacio que no existeix al dataset origen,
    #: o anotacions noves que xocarien amb una que ja hi es. Totes dues coses
    #: son errors d'escriptura i han d'aturar la generacio.
    orphan: list[str] = field(default_factory=list)


def build_candidate(source: GoldDataset, *, today: date | None = None) -> BuildResult:
    """Genera el dataset candidat a partir del revisat. Funcio pura."""
    proposals: dict[tuple[str, str], Any] = {
        (case_id, item.gold_id): item for case_id, item in PROPOSALS
    }
    seen: set[tuple[str, str]] = set()
    added: set[tuple[str, str]] = set()
    collisions: list[str] = []
    cases: list[GoldCaseV2] = []

    for case in source.cases:
        requirements = []
        for item in case.requirements:
            key = (case.case_id, item.gold_id)
            proposal = proposals.get(key)
            if proposal is not None and proposal.__class__.__name__.endswith("RequirementV2"):
                seen.add(key)
                requirements.append(proposal.model_copy(deep=True))
            else:
                requirements.append(adapt_requirement(item))

        criteria = []
        for item in case.award_criteria:
            key = (case.case_id, item.gold_id)
            proposal = proposals.get(key)
            if proposal is not None and proposal.__class__.__name__.endswith("CriterionV2"):
                seen.add(key)
                criteria.append(proposal.model_copy(deep=True))
            else:
                criteria.append(adapt_award_criterion(item))

        # Anotacions noves: s'afegeixen al cas, no en substitueixen cap.
        existing = {item.gold_id for item in [*case.requirements, *case.award_criteria]}
        for case_id, addition in ADDITIONS:
            if case_id != case.case_id:
                continue
            if addition.gold_id in existing:
                collisions.append(f"{case_id}::{addition.gold_id}")
                continue
            added.add((case_id, addition.gold_id))
            target = (
                criteria
                if addition.__class__.__name__.endswith("CriterionV2")
                else requirements
            )
            target.append(addition.model_copy(deep=True))

        cases.append(
            GoldCaseV2(
                case_id=case.case_id,
                expediente_number=case.expediente_number,
                tender_external_id=case.tender_external_id,
                title=case.title,
                buyer=case.buyer,
                has_lots=case.has_lots,
                traits=list(case.traits),
                notes=case.notes,
                requirements=requirements,
                award_criteria=criteria,
            )
        )

    when = today or date.today()
    # Un canvi del gold no es mai silencios: cada proposta deixa constancia, tant
    # si corregeix una anotacio com si n'afegeix una.
    revisions = [
        *source.revisions,
        *[
            GoldRevision(
                date=when,
                reviewer=PROPOSED_BY,
                case_id=case_id,
                gold_id=item.gold_id,
                reason=item.proposal_reason or "Correccio proposada per a revisio humana.",
            )
            for case_id, item in (*PROPOSALS, *ADDITIONS)
            if (case_id, item.gold_id) in seen | added
        ],
    ]

    dataset = GoldDatasetV2(
        dataset_id=CANDIDATE_DATASET,
        gold_schema_version=GOLD_SCHEMA_VERSION,
        derived_from=source.dataset_id,
        version=source.version + 1,
        created_at=when,
        description=(
            f"Candidat v2 derivat de {source.dataset_id}, que queda immutable. Les "
            "anotacions heretades conserven el seu estat de revisio i NO s'han "
            "reinterpretat: el que la v1 no deia queda sense informar. Les correccions i "
            "les anotacions noves d'aquesta versio son PROPOSTES sense verificar; cap "
            "metrica oficial les pot fer servir fins que una persona les resolgui."
        ),
        revisions=revisions,
        cases=cases,
    )
    return BuildResult(
        dataset=dataset,
        applied=sorted(f"{case}::{gold}" for case, gold in seen),
        added=sorted(f"{case}::{gold}" for case, gold in added),
        # Un id repetit apareix per dues vies (no s'ha afegit I xoca amb un que
        # ja hi era): es el mateix error, i es diu una sola vegada.
        orphan=sorted(
            {
                *(
                    f"{case}::{item.gold_id}"
                    for case, item in PROPOSALS
                    if (case, item.gold_id) not in seen
                ),
                *(
                    f"{case}::{item.gold_id}"
                    for case, item in ADDITIONS
                    if (case, item.gold_id) not in added
                ),
                *collisions,
            }
        ),
    )


# --------------------------------------------------------------------------- #
# Validacio de cites contra el text real                                        #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QuoteCheck:
    item_id: str
    page_number: int
    quote: str
    located: bool
    #: Cert quan la pagina no es a la base de dades i, per tant, no s'ha pogut
    #: comprovar. No es el mateix que una cita que no hi es.
    page_missing: bool


def item_quotes(item: GoldItemV2) -> list[tuple[int, str]]:
    return [
        (item.page_number, item.quote),
        *[(evidence.page_number, evidence.quote) for evidence in item.extra_evidence],
    ]


def check_quotes(
    dataset: GoldDatasetV2, texts: dict[tuple[str, str, int], str]
) -> list[QuoteCheck]:
    """Cada cita, contra el text immutable de la seva pagina."""
    checks: list[QuoteCheck] = []
    for case, item in dataset.items:
        for page_number, quote in item_quotes(item):
            page_text = texts.get((case.case_id, item.document_name, page_number))
            checks.append(
                QuoteCheck(
                    item_id=f"{case.case_id}::{item.gold_id}",
                    page_number=page_number,
                    quote=quote,
                    located=bool(page_text) and locate_quote(page_text, quote) is not None,
                    page_missing=page_text is None,
                )
            )
    return checks


# --------------------------------------------------------------------------- #
# Report de cobertura                                                           #
# --------------------------------------------------------------------------- #


def coverage_payload(dataset: GoldDatasetV2, checks: list[QuoteCheck]) -> dict[str, Any]:
    status = dataset.status_counts()
    inherited = dataset.inherited()
    proposals = dataset.proposals()
    dimensions = dataset.dimension_coverage()

    return {
        "dataset_id": dataset.dataset_id,
        "gold_schema_version": dataset.gold_schema_version,
        "derived_from": dataset.derived_from,
        "created_at": dataset.created_at.isoformat(),
        "totals": {
            "annotations": len(dataset.items),
            "requirements": dataset.total_requirements,
            "award_criteria": dataset.total_award_criteria,
            "cases": len(dataset.cases),
        },
        "by_status": status,
        "by_origin": {
            "inherited": len(inherited),
            "inherited_verified": sum(1 for _, item in inherited if item.is_verified),
            "proposals": len(proposals),
            "proposals_verified": sum(1 for _, item in proposals if item.is_verified),
        },
        "refinements": [
            {
                "item": f"{case.case_id}::{item.gold_id}",
                "refines": f"{case.case_id}::{item.refines}",
                "status": item.annotation_status.value,
            }
            for case, item in dataset.items
            if item.refines
        ],
        # Buit mentre nomes una de les dues estigui verificada. Deixa de ser-ho
        # el dia que algu verifiqui l'afinada sense retirar l'original.
        "double_counted": [
            {"refined": refined, "refinement": refinement}
            for refined, refinement in dataset.double_counted()
        ],
        "verified_award_criteria": dataset.verified_award_criteria,
        "categories": dataset.categories_covered(),
        "dimensions": {
            name: {
                **counts,
                # El que un benchmark pot mesurar avui. Si es fals, la xifra a
                # ensenyar es `n/d`, mai 0%.
                "measurable": counts["verified"] > 0,
            }
            for name, counts in dimensions.items()
        },
        "quotes": {
            "checked": len(checks),
            "located": sum(1 for check in checks if check.located),
            "not_located": [
                {"item": check.item_id, "page": check.page_number, "quote": check.quote}
                for check in checks
                if not check.located and not check.page_missing
            ],
            "pages_unavailable": sorted(
                {check.item_id for check in checks if check.page_missing}
            ),
        },
        "proposal_ids": [f"{case.case_id}::{item.gold_id}" for case, item in proposals],
        "unresolved": [
            {"item": f"{case.case_id}::{item.gold_id}", "notes": item.unresolved}
            for case, item in dataset.items
            if item.unresolved
        ],
        # Explicit perque ningu llegeixi aquest fitxer com un resultat de model.
        "model_metrics": None,
        "model_metrics_note": (
            "Encara no hi ha metriques de model: aquesta versio nomes prepara el gold. "
            "Cap execucio de l'extractor forma part d'aquest report."
        ),
    }


def render_coverage_markdown(payload: dict[str, Any]) -> str:
    status = payload["by_status"]
    origin = payload["by_origin"]
    lines = [
        f"# Cobertura del gold — {payload['dataset_id']}",
        "",
        f"Derivat de `{payload['derived_from']}`, que queda immutable · "
        f"esquema `{payload['gold_schema_version']}` · {payload['created_at']}",
        "",
        "## Recompte",
        "",
        "| | |",
        "| --- | ---: |",
        f"| Total d'anotacions | {payload['totals']['annotations']} |",
        f"| Requisits | {payload['totals']['requirements']} |",
        f"| Criteris d'adjudicacio | {payload['totals']['award_criteria']} |",
        f"| Casos | {payload['totals']['cases']} |",
        "",
        "| Estat | Anotacions |",
        "| --- | ---: |",
        f"| verified | {status['verified']} |",
        f"| draft (propostes noves) | {status['draft']} |",
        f"| needs_review | {status['needs_review']} |",
        f"| rejected | {status['rejected']} |",
        "",
        "| Origen | Total | Verificades |",
        "| --- | ---: | ---: |",
        f"| Heretades de la v1 | {origin['inherited']} | {origin['inherited_verified']} |",
        f"| Propostes noves | {origin['proposals']} | {origin['proposals_verified']} |",
        "",
        f"Criteris d'adjudicacio verificats disponibles: **{payload['verified_award_criteria']}**",
        "",
        "## Categories cobertes",
        "",
        "| Categoria | Verificades | Pendents |",
        "| --- | ---: | ---: |",
    ]
    for name, counts in payload["categories"].items():
        lines.append(f"| {name} | {counts['verified']} | {counts['pending']} |")

    lines += [
        "",
        "## Dimensions que el benchmark podra puntuar",
        "",
        "Una dimensio nomes es mesurable si alguna anotacio **verificada** la fixa. "
        "Les pendents son el que hi haura si la revisio humana accepta les propostes; "
        "no son una mesura.",
        "",
        "| Dimensio | Gold verificat | Pendent de revisio | Mesurable avui |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, counts in payload["dimensions"].items():
        verified = counts["verified"] if counts["measurable"] else NOT_MEASURABLE
        lines.append(
            f"| {name} | {verified} | {counts['pending']} | "
            f"{'si' if counts['measurable'] else NOT_MEASURABLE} |"
        )

    quotes = payload["quotes"]
    lines += [
        "",
        "## Evidencia",
        "",
        f"{quotes['located']} de {quotes['checked']} cites localitzades al text real "
        "de la seva pagina.",
    ]
    if quotes["not_located"]:
        lines.append("")
        lines.append("**Cites NO localitzades:**")
        for entry in quotes["not_located"]:
            lines.append(f"- `{entry['item']}` p{entry['page']}: {entry['quote'][:120]}")
    if quotes["pages_unavailable"]:
        lines.append("")
        lines.append(
            f"{len(quotes['pages_unavailable'])} anotacio(ns) amb la pagina no disponible "
            "en aquesta base de dades: no s'han pogut comprovar."
        )

    if payload["refinements"]:
        lines += [
            "",
            "## Anotacions que n'afinen una altra",
            "",
            "Descriuen **la mateixa exigencia** amb mes detall que una anotacio ja "
            "verificada, que es queda intacta. Si totes dues acabessin verificades, el "
            "gold demanaria dues coses on el plec en demana una: decidir quina es queda "
            "forma part de la revisio.",
            "",
            "| Anotacio | Afina | Estat |",
            "| --- | --- | --- |",
        ]
        for entry in payload["refinements"]:
            lines.append(f"| {entry['item']} | {entry['refines']} | {entry['status']} |")
        if payload["double_counted"]:
            lines += [
                "",
                "**AVIS: hi ha parelles verificades totes dues.** El denominador del "
                "benchmark compta dues vegades la mateixa exigencia i un extractor "
                "correcte hi apareixeria com si se n'hagues deixat una.",
                "",
            ]
            for entry in payload["double_counted"]:
                lines.append(f"- `{entry['refined']}` i `{entry['refinement']}`")

    blind = [
        name
        for name, counts in payload["dimensions"].items()
        if not counts["measurable"] and not counts["pending"]
    ]
    if blind:
        lines += [
            "",
            "## Buits que aquesta versio NO tanca",
            "",
            "Dimensions sense cap anotacio que les fixi, ni verificada ni pendent. "
            "Verificar aquestes propostes no les fara mesurables: cal tornar a anotar "
            "algun cas contra la seva pagina.",
            "",
        ]
        lines += [f"- `{name}`" for name in blind]

    lines += [
        "",
        "## Que NO diu aquest report",
        "",
        payload["model_metrics_note"],
        "",
        "## Que el text no decideix",
        "",
        "Escrit expressament, perque ningu ho ompli despres per intuicio.",
        "",
        "### A les propostes noves",
        "",
    ]
    proposals = set(payload["proposal_ids"])
    for entry in payload["unresolved"]:
        if entry["item"] not in proposals:
            continue
        lines.append(f"**{entry['item']}**")
        lines += [f"- {note}" for note in entry["notes"]]
        lines.append("")

    # Les heretades repeteixen les mateixes limitacions una i altra vegada. Es
    # agrupen per motiu: son limitacions de l'esquema v1, no d'aquelles pagines,
    # i llistades una per una ofegarien el que si que es especific de cada cas.
    grouped: dict[str, list[str]] = {}
    for entry in payload["unresolved"]:
        if entry["item"] in proposals:
            continue
        for note in entry["notes"]:
            grouped.setdefault(note, []).append(entry["item"])
    if grouped:
        lines += ["### A les anotacions heretades", ""]
        for note, items in sorted(grouped.items(), key=lambda pair: -len(pair[1])):
            lines.append(f"- {note}")
            lines.append(f"  - Afecta {len(items)}: {', '.join(sorted(items))}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


async def _page_texts(dataset: GoldDatasetV2) -> dict[tuple[str, str, int], str]:
    """Text de cada pagina citada, incloses les de l'evidencia addicional."""
    from app.db.session import SessionFactory
    from app.evaluation.review_page import page_texts

    async with SessionFactory() as session:
        texts = await page_texts(session, dataset)

        import sqlalchemy as sa

        # `page_texts` nomes mira la pagina principal de cada anotacio; les
        # cites addicionals poden ser en una altra (el cas de la formula
        # repartida entre la 70 i la 71).
        for case, item in dataset.items:
            if not case.expediente_number:
                continue
            for evidence in item.extra_evidence:
                key = (case.case_id, evidence.document_name, evidence.page_number)
                if key in texts:
                    continue
                row = (
                    await session.execute(
                        sa.text(
                            """
                            SELECT p.text, e.revision_number
                            FROM tender_document_pages p
                            JOIN tender_document_extractions e
                              ON e.id = p.extraction_id AND e.is_current
                            JOIN tender_document_files f ON f.content_id = e.content_id
                            JOIN tender_documents td ON td.id = f.tender_document_id
                            JOIN tenders t ON t.id = td.tender_id
                            WHERE t.expediente_number = :exp
                              AND td.name = :doc
                              AND p.page_number = :page
                            LIMIT 1
                            """
                        ),
                        {
                            "exp": case.expediente_number,
                            "doc": evidence.document_name,
                            "page": evidence.page_number,
                        },
                    )
                ).first()
                if row:
                    texts[key] = row[0]
                    texts[(case.case_id, evidence.document_name, -evidence.page_number)] = str(
                        row[1]
                    )
    return texts


async def generate(args: argparse.Namespace) -> int:
    from app.evaluation.gold_v2_page import render_candidate_page, write_candidate_page

    source = load_dataset(args.source)
    result = build_candidate(source)
    dataset = result.dataset

    if result.orphan:
        print(f"ERROR: propostes sense anotacio d'origen: {', '.join(result.orphan)}")
        return 1

    texts = await _page_texts(dataset)
    checks = check_quotes(dataset, texts)
    broken = [check for check in checks if not check.located and not check.page_missing]
    if broken and not args.force:
        print("ERROR: hi ha cites que no es localitzen a la seva pagina. No s'escriu res.")
        for check in broken:
            print(f"  {check.item_id} p{check.page_number}: {check.quote[:110]}")
        return 1

    path = save_dataset(dataset)
    payload = coverage_payload(dataset, checks)

    args.output.mkdir(parents=True, exist_ok=True)
    report_json = args.output / f"coverage-{dataset.dataset_id}.json"
    report_md = args.output / f"coverage-{dataset.dataset_id}.md"
    report_json.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report_md.write_text(render_coverage_markdown(payload), encoding="utf-8")

    page = write_candidate_page(
        dataset, render_candidate_page(dataset, source, texts), args.output
    )

    status = payload["by_status"]
    print(f"Dataset candidat: {path}")
    print(f"  {payload['totals']['annotations']} anotacions · ", end="")
    print(", ".join(f"{key}={value}" for key, value in status.items() if value))
    print(f"  correccions aplicades: {len(result.applied)} ({', '.join(result.applied)})")
    if result.added:
        print(f"  anotacions noves: {len(result.added)} ({', '.join(result.added)})")
    print(f"  cites localitzades: {payload['quotes']['located']}/{payload['quotes']['checked']}")
    measurable = [name for name, entry in payload["dimensions"].items() if entry["measurable"]]
    print(f"  dimensions mesurables avui: {', '.join(measurable) or 'cap'}")
    print(f"Report: {report_md}")
    print(f"Pagina de revisio humana: {page}")
    print(f"\nObre-la al navegador:\n  {page.resolve()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.evaluation.gold_v2", description=__doc__)
    parser.add_argument("--source", default=SOURCE_DATASET)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--force",
        action="store_true",
        help="escriu encara que alguna cita no es localitzi (nomes per depurar)",
    )
    return parser


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(generate(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CANDIDATE_DATASET",
    "NOT_MEASURABLE",
    "SOURCE_DATASET",
    "AnnotationStatus",
    "BenchmarkDimension",
    "BuildResult",
    "QuoteCheck",
    "build_candidate",
    "check_quotes",
    "coverage_payload",
    "dimensions_of",
    "load_dataset_v2",
    "render_coverage_markdown",
    "AnnotationOrigin",
]
