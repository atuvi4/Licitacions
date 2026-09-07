"""Construeix `gold-v1.json` a partir d'anotacions fetes a ma.

Les DECISIONS son humanes: categoria, obligatorietat, operador, valor i abast
els he assignat llegint el text del plec. El que fa aquest script es nomes
recuperar el **fragment literal** de la pagina real perque la cita sigui
verificable, i validar que cada quote es localitza de veritat.

Aixo NO es autoetiquetatge amb el model: cap sortida de l'extractor entra aqui.

    python scripts/build_gold_dataset.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa  # noqa: E402

from app.db.session import SessionFactory  # noqa: E402
from app.evaluation.dataset import (  # noqa: E402
    GoldAwardCriterion,
    GoldCase,
    GoldDataset,
    GoldRequirement,
    save_dataset,
)
from app.services.evidence_validation import locate_quote  # noqa: E402

# --------------------------------------------------------------------------- #
# Anotacions manuals. `anchor` es un fragment que apareix literalment a la      #
# pagina; l'script n'extreu el text exacte al voltant.                          #
# --------------------------------------------------------------------------- #

ANNOTATIONS: list[dict] = [
    # ---- 4249/2026 -------------------------------------------------------
    {
        "case": "4249-2026",
        "gold_id": "eco-1",
        "page": 10,
        "anchor": "Volumen anual de negocios, o bien volumen anual de negocios",
        "extend": 330,
        "category": "economic_solvency",
        "obligation": "mandatory",
        "operator": "gte",
        "value": {"amount": 60750, "currency": "EUR", "period": "annual", "years": 3},
        "notes": "L'import surt com a 1,5 x valor estimat, explicitat entre parentesis.",
    },
    {
        "case": "4249-2026",
        "gold_id": "exp-1",
        "page": 10,
        "anchor": "Una relaci",
        "extend": 300,
        "category": "experience",
        "obligation": "mandatory",
        "operator": None,
        "value": {"lookback_years": 3, "similarity_condition": "igual o similar naturaleza"},
        "notes": "Sense minim de projectes ni import: 4C no ho pot automatitzar.",
    },
    {
        "case": "4249-2026",
        "gold_id": "ins-1",
        "page": 17,
        "anchor": "Justificante de la p",
        "extend": 160,
        "category": "insurance",
        "obligation": "mandatory",
        "operator": "gte",
        "value": {
            "coverage_amount": 1000000,
            "currency": "EUR",
            "type": "responsabilidad civil",
        },
    },
    # ---- 422/2026 --------------------------------------------------------
    {
        "case": "422-2026",
        "gold_id": "ins-1",
        "page": 4,
        "anchor": "El contratista vendr",
        "extend": 220,
        "category": "insurance",
        "obligation": "mandatory",
        "operator": "gte",
        "value": {
            "coverage_amount": 500000,
            "currency": "EUR",
            "type": "responsabilidad civil",
        },
    },
    {
        "case": "422-2026",
        "gold_id": "eco-1",
        "page": 9,
        "anchor": "un volumen anua",
        "extend": 160,
        "category": "economic_solvency",
        "obligation": "mandatory",
        "operator": "gte",
        "value": {"amount": 7450, "currency": "EUR", "period": "annual", "years": 3},
    },
    {
        "case": "422-2026",
        "gold_id": "award-1",
        "page": 12,
        "anchor": "PROPUESTA ECON",
        "extend": 200,
        "kind": "award",
        "name": "Propuesta economica",
        "points": 10,
        "evaluation_type": "automatic",
    },
    # ---- 300/2025/03877 --------------------------------------------------
    {
        "case": "300-2025-03877",
        "gold_id": "exp-1",
        "page": 68,
        "anchor": "Una relaci",
        "extend": 300,
        "category": "experience",
        "obligation": "mandatory",
        "operator": None,
        "value": {"lookback_years": 3, "similarity_condition": "igual o similar naturaleza"},
    },
    {
        "case": "300-2025-03877",
        "gold_id": "award-1",
        "page": 70,
        "anchor": "A la oferta econ",
        "extend": 120,
        "kind": "award",
        "name": "Oferta economica",
        "points": 45,
        "evaluation_type": "automatic",
    },
    # ---- TSA0082096-040 --------------------------------------------------
    {
        "case": "TSA0082096-040",
        "gold_id": "ins-1",
        "page": 4,
        "anchor": "El ofertante tendr",
        "extend": 220,
        "category": "insurance",
        "obligation": "mandatory",
        "operator": "gte",
        "value": {
            "coverage_amount": 500000,
            "currency": "EUR",
            "type": "responsabilidad civil",
        },
    },
    # ---- 2025-04671 ------------------------------------------------------
    {
        "case": "2025-04671",
        "gold_id": "cert-1",
        "page": 16,
        "anchor": "- ISO 9001",
        "extend": 80,
        "category": "certification",
        "obligation": "mandatory",
        "operator": "one_of",
        "value": {"names": ["ISO 9001", "ISO 14001", "IRIS"], "equivalents_allowed": True},
        "notes": "El plec admet equivalents: 4C no pot decidir equivalencies.",
    },
    # ---- 2026SE00013 -----------------------------------------------------
    {
        "case": "2026SE00013",
        "gold_id": "class-1",
        "page": 11,
        "anchor": "grupo, subgrupo y categor",
        "extend": 200,
        "category": "business_classification",
        "obligation": "mandatory",
        "operator": None,
        "value": {"group": None, "subgroup": None, "category": None},
        "notes": "El plec remet a un apartat: grup i subgrup no son al text. 4C ha de demanar revisio.",
    },
    # ---- 1/2026 ----------------------------------------------------------
    {
        "case": "1-2026",
        "gold_id": "exp-1",
        "page": 5,
        "anchor": "Mediante la presentaci",
        "extend": 420,
        "category": "experience",
        "obligation": "mandatory",
        "operator": None,
        "value": {
            "lookback_years": 3,
            "cpv_codes": ["34928400", "45233293"],
            "similarity_condition": "contratos similares definits per CPV",
        },
        "notes": "Cas favorable: el plec defineix la similitud amb CPV explicits.",
    },
]

CASE_META = {
    "4249-2026": ("4249/2026", ["plec llarg", "sense lots"]),
    "422-2026": ("422/2026", ["PCAP + PPT", "sense lots"]),
    "300-2025-03877": ("300/2025/03877", ["plec molt llarg (95 pagines)", "sense lots"]),
    "TSA0082096-040": ("TSA0082096-040", ["PPT", "organisme diferent"]),
    "2025-04671": ("2025-04671", ["certificacions amb equivalents"]),
    "1-2026": ("1/2026", ["similitud definida per CPV"]),
    "2026SE00013": ("2026SE00013", ["classificacio empresarial per referencia"]),
}


async def exact_quote(session, expediente: str, page_number: int, anchor: str, extend: int):
    """Retorna (quote literal, nom del document) o (None, None)."""
    row = (
        await session.execute(
            sa.text(
                """
                SELECT p.text, td.name
                FROM tender_document_pages p
                JOIN tender_document_extractions e ON e.id = p.extraction_id AND e.is_current
                JOIN tender_document_files f ON f.content_id = e.content_id
                JOIN tender_documents td ON td.id = f.tender_document_id
                JOIN tenders t ON t.id = td.tender_id
                WHERE t.expediente_number = :exp AND p.page_number = :page
                  AND position(:anchor in p.text) > 0
                LIMIT 1
                """
            ),
            {"exp": expediente, "page": page_number, "anchor": anchor},
        )
    ).first()
    if row is None:
        return None, None
    text, document_name = row
    start = text.find(anchor)
    quote = " ".join(text[start : start + extend].split())
    # Es comprova que el fragment normalitzat es localitzi de veritat.
    if locate_quote(text, quote) is None:
        return None, None
    return quote, document_name


async def main() -> int:
    cases: dict[str, GoldCase] = {}
    problems: list[str] = []

    async with SessionFactory() as session:
        for item in ANNOTATIONS:
            case_id = item["case"]
            expediente, traits = CASE_META[case_id]
            quote, document_name = await exact_quote(
                session, expediente, item["page"], item["anchor"], item.get("extend", 240)
            )
            if quote is None:
                problems.append(f"{case_id}/{item['gold_id']}: no s'ha pogut ancorar la cita")
                continue

            if case_id not in cases:
                external = await session.scalar(
                    sa.text("SELECT external_id FROM tenders WHERE expediente_number = :e"),
                    {"e": expediente},
                )
                cases[case_id] = GoldCase(
                    case_id=case_id,
                    expediente_number=expediente,
                    tender_external_id=external,
                    traits=traits,
                    has_lots=False,
                )

            if item.get("kind") == "award":
                cases[case_id].award_criteria.append(
                    GoldAwardCriterion(
                        gold_id=item["gold_id"],
                        name=item["name"],
                        points=item.get("points"),
                        evaluation_type=item.get("evaluation_type", "unknown"),
                        formula=None,
                        document_name=document_name,
                        page_number=item["page"],
                        quote=quote,
                        notes=item.get("notes"),
                    )
                )
            else:
                cases[case_id].requirements.append(
                    GoldRequirement(
                        gold_id=item["gold_id"],
                        category=item["category"],
                        obligation=item["obligation"],
                        operator=item.get("operator"),
                        scope="tender",
                        value=item.get("value"),
                        document_name=document_name,
                        page_number=item["page"],
                        quote=quote,
                        notes=item.get("notes"),
                    )
                )

    dataset = GoldDataset(
        dataset_id="gold-v1",
        version=1,
        created_at=date.today(),
        description=(
            "Requisits anotats a ma sobre plecs reals de PLACSP ja processats. "
            "Les cites son fragments literals de la pagina; les etiquetes "
            "(categoria, obligatorietat, operador, valor) son decisio humana. "
            "Cap sortida del model s'ha fet servir com a referencia."
        ),
        cases=sorted(cases.values(), key=lambda c: c.case_id),
    )
    path = save_dataset(dataset)

    print(f"Gold desat a {path}")
    print(
        f"  {len(dataset.cases)} casos · {dataset.total_requirements} requisits "
        f"({dataset.total_critical} critics) · {dataset.total_award_criteria} criteris"
    )
    for problem in problems:
        print(f"  AVIS {problem}")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
