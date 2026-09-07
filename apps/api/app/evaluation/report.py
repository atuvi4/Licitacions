"""Informes del benchmark: un per a maquines i un per a persones.

El JSON serveix per comparar execucions i per posar-hi una porta de qualitat el
dia que en vulguem una. L'HTML serveix per a l'unica cosa que cap metrica no
substitueix: obrir un cas, veure el gold i la prediccio de costat amb la seva
cita, i entendre per que ha fallat.

Els avisos del final no son decoratius. Un `critical recall` baix, un fals
blocador o una `automatable_rate` baixa son motius per NO passar a produccio,
i el report ho ha de dir sense que ningu hagi d'interpretar taules.
"""

from __future__ import annotations

import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.evaluation.metrics import EvaluationSummary

#: Llindars d'avis. NO son una porta de qualitat: son el que fa que el report
#: digui "aixo encara no esta llest", que es diferent de fer fallar el proces.
CRITICAL_RECALL_WARN = 0.90
EVIDENCE_WARN = 0.95
AUTOMATABLE_WARN = 0.70


def build_payload(summary: EvaluationSummary, details: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary.as_dict(),
        "cases": details,
        "warnings": warnings_for(summary),
    }


def warnings_for(summary: EvaluationSummary) -> list[str]:
    """Els senyals que impedeixen posar aixo davant d'un usuari."""
    alerts: list[str] = []

    if summary.critical.true_positives + summary.critical.false_negatives == 0:
        alerts.append(
            "No hi ha cap requisit critic al gold dataset: el recall critic no es mesurable."
        )
    elif summary.critical.recall < CRITICAL_RECALL_WARN:
        alerts.append(
            f"Recall critic {summary.critical.recall:.1%} per sota de "
            f"{CRITICAL_RECALL_WARN:.0%}: es perden requisits que poden canviar un Go/No-Go."
        )

    if summary.false_blockers:
        alerts.append(
            f"{len(summary.false_blockers)} fals(os) blocador(s): el model marca com a "
            "obligatori el que el gold no. Pot provocar un NO-GO fals."
        )

    if summary.evidence.total and (
        summary.evidence.quote_locatable / summary.evidence.total < EVIDENCE_WARN
    ):
        alerts.append(
            "Precisio d'evidencia insuficient: hi ha cites que no es localitzen a la pagina."
        )

    if summary.critical_correct and summary.automatable_rate < AUTOMATABLE_WARN:
        alerts.append(
            f"automatable_rate {summary.automatable_rate:.1%} per sota de "
            f"{AUTOMATABLE_WARN:.0%}: el motor de 4C no podra avaluar sol la majoria de "
            "requisits critics."
        )

    if summary.skipped_cases:
        alerts.append(f"{len(summary.skipped_cases)} cas(os) no analitzat(s).")

    return alerts


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _table(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def render_markdown(summary: EvaluationSummary, details: list[dict[str, Any]]) -> str:
    data = summary.as_dict()
    detection = data["detection"]
    alerts = warnings_for(summary)

    parts: list[str] = [
        f"# Benchmark d'extraccio de requisits — `{summary.dataset_id}`",
        "",
        f"- Proveidor: `{summary.provider}` · model `{summary.model}`",
        f"- Prompt: `{summary.prompt_version}` · extractor `{summary.extractor_version}` "
        f"· schema `{summary.schema_version}`",
        f"- Generat: {datetime.now(UTC).isoformat(timespec='seconds')}",
        "",
    ]

    if alerts:
        parts += ["## Avisos", ""]
        parts += [f"- ⚠️ {alert}" for alert in alerts]
        parts.append("")

    parts += [
        "## Deteccio",
        "",
        _table(
            [
                (
                    name,
                    str(block["true_positives"]),
                    str(block["false_positives"]),
                    str(block["false_negatives"]),
                    f"{block['precision']:.1%}",
                    f"{block['recall']:.1%}",
                    f"{block['f1']:.1%}",
                )
                for name, block in (
                    ("Global", detection["overall"]),
                    ("Obligatoris", detection["mandatory"]),
                    ("**Critics**", detection["critical"]),
                    ("Criteris d'adjudicacio", detection["award_criteria"]),
                )
            ],
            ("Conjunt", "TP", "FP", "FN", "Precision", "Recall", "F1"),
        ),
        "",
        "## Per categoria",
        "",
        _table(
            [
                (
                    category,
                    str(block["true_positives"]),
                    str(block["false_positives"]),
                    str(block["false_negatives"]),
                    f"{block['precision']:.1%}",
                    f"{block['recall']:.1%}",
                )
                for category, block in detection["by_category"].items()
            ]
            or [("(cap)", "-", "-", "-", "-", "-")],
            ("Categoria", "TP", "FP", "FN", "Precision", "Recall"),
        ),
        "",
        "## Estructura (sobre els detectats)",
        "",
    ]

    structure = data["structure"]
    parts += [
        _table(
            [
                (label, f"{structure.get(f'{key}_rate', 0):.1%}")
                for label, key in (
                    ("Categoria", "category_ok"),
                    ("Obligatorietat", "obligation_ok"),
                    ("Operador", "operator_ok"),
                    ("Import", "amount_ok"),
                    ("Moneda", "currency_ok"),
                    ("Anys de mirada enrere", "lookback_ok"),
                    ("Mínim de projectes", "minimum_projects_ok"),
                    ("Abast (lot)", "scope_ok"),
                    ("**Tot correcte alhora**", "fully_correct"),
                )
            ],
            ("Camp", "Encert"),
        ),
        "",
        "## Evidencia",
        "",
        _table(
            [
                ("Cita localitzable", f"{data['evidence']['quote_locatable_rate']:.1%}"),
                ("Pagina correcta", f"{data['evidence']['page_correct_rate']:.1%}"),
                ("Document correcte", f"{data['evidence']['document_correct_rate']:.1%}"),
            ],
            ("Mesura", "Valor"),
        ),
        "",
        "## Compatibilitat amb el motor de compliment (4C)",
        "",
        f"- Requisits critics correctes: **{data['automatable']['critical_correct']}**",
        f"- Avaluables automaticament per 4C: **{data['automatable']['automatable_correct']}**",
        f"- `automatable_rate`: **{data['automatable']['automatable_rate']:.1%}**",
        "",
    ]

    if summary.critical_misses:
        parts += ["## CRITICAL MISSES", ""]
        parts.append(
            _table(
                [
                    (
                        miss.case_id,
                        miss.category,
                        miss.document_name,
                        str(miss.page_number),
                        miss.likely_cause,
                        miss.quote[:90].replace("|", "/").replace("\n", " "),
                    )
                    for miss in summary.critical_misses
                ],
                ("Cas", "Categoria", "Document", "Pag.", "Causa probable", "Cita del gold"),
            )
        )
        parts.append("")

    if summary.false_blockers:
        parts += ["## FALSE BLOCKERS", ""]
        parts.append(
            _table(
                [
                    (
                        blocker.case_id,
                        blocker.predicted_category,
                        blocker.gold_obligation or "(cap requisit al gold)",
                        blocker.predicted_title[:90].replace("|", "/").replace("\n", " "),
                    )
                    for blocker in summary.false_blockers
                ],
                ("Cas", "Categoria", "Gold diu", "El model diu obligatori"),
            )
        )
        parts.append("")

    if summary.errors:
        parts += [
            "## Taxonomia d'errors",
            "",
            _table(
                [(kind, str(count)) for kind, count in sorted(summary.errors.items())],
                ("Tipus", "Recompte"),
            ),
            "",
        ]

    cost = data["cost"]
    parts += [
        "## Cost",
        "",
        _table(
            [
                ("Expedients", str(cost["tenders"])),
                ("Peticions", str(cost["requests"])),
                ("Tokens entrada", str(cost["input_tokens"])),
                ("Tokens sortida", str(cost["output_tokens"])),
                ("Durada", f"{cost['duration_ms'] / 1000:.1f} s"),
                ("Cost estimat", str(cost["estimated_cost"] or "—")),
                ("Cost per expedient", str(cost["cost_per_tender"] or "—")),
                ("Cost per requisit correcte", str(cost["cost_per_correct_requirement"] or "—")),
            ],
            ("Mesura", "Valor"),
        ),
        "",
    ]

    if summary.skipped_cases:
        parts += ["## Casos no analitzats", ""]
        parts += [f"- `{item['case_id']}`: {item['reason']}" for item in summary.skipped_cases]
        parts.append("")

    parts += [f"_Casos avaluats: {len(details)}._", ""]
    return "\n".join(parts)


def render_html(summary: EvaluationSummary, details: list[dict[str, Any]]) -> str:
    """Inspector gold vs predicted. Prou per revisar a ma, sense muntar res."""
    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else "—"))

    blocks: list[str] = []
    for case in details:
        rows = []
        for pair in case["pairs"]:
            ok = "✅" if pair["evidence_valid"] else "⚠️"
            rows.append(
                f"<tr><td>{ok} {esc(pair['gold_id'])}</td>"
                f"<td><div class=q>{esc(pair['gold_quote'])}</div>"
                f"<code>{esc(pair['gold_value'])}</code><br><small>{esc(pair['gold_obligation'])}</small></td>"
                f"<td><div class=q>{esc(pair['predicted_title'])}</div>"
                f"<code>{esc(pair['predicted_value'])}</code><br>"
                f"<small>{esc(pair['predicted_obligation'])}</small></td>"
                f"<td>{esc(pair['score'])}</td></tr>"
            )
        for missed in case["missed"]:
            rows.append(
                f"<tr class=miss><td>❌ {esc(missed['gold_id'])}</td>"
                f"<td><div class=q>{esc(missed['quote'])}</div>"
                f"<small>{esc(missed['category'])}</small></td>"
                "<td><em>no detectat</em></td><td>—</td></tr>"
            )
        for spurious in case["spurious"]:
            rows.append(
                "<tr class=extra><td>➕</td><td><em>no hi es al gold</em></td>"
                f"<td><div class=q>{esc(spurious['title'])}</div>"
                f"<small>{esc(spurious['category'])}</small></td><td>—</td></tr>"
            )
        blocks.append(
            f"<section><h2>{esc(case['case_id'])} "
            f"<small>{esc(case.get('expediente_number'))}</small></h2>"
            f"<p>gold {case['gold_requirements']} · predits {case['predicted_requirements']} "
            f"· aparellats {case['matched']}</p>"
            "<table><thead><tr><th>Id</th><th>Gold</th><th>Predit</th><th>Score</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table></section>"
        )

    alerts = "".join(f"<li>{esc(a)}</li>" for a in warnings_for(summary))
    data = summary.as_dict()
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Benchmark {esc(summary.dataset_id)}</title>
<style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;color:#111;max-width:1200px}}
 table{{border-collapse:collapse;width:100%;margin:.5rem 0 2rem}}
 th,td{{border:1px solid #ddd;padding:.5rem;text-align:left;vertical-align:top}}
 th{{background:#f6f6f6}}
 .q{{font-style:italic;color:#333;margin-bottom:.25rem}}
 code{{font-size:11px;color:#555;word-break:break-all}}
 tr.miss{{background:#fff4f4}} tr.extra{{background:#fffbf0}}
 .warn{{background:#fff4f4;border:1px solid #f5c2c2;padding:.75rem 1rem;border-radius:6px}}
 .kpi{{display:inline-block;margin-right:2rem}}
 .kpi b{{display:block;font-size:1.4rem}}
</style>
<h1>Benchmark d'extraccio — {esc(summary.dataset_id)}</h1>
<p>{esc(summary.provider)} · {esc(summary.model)} · prompt {esc(summary.prompt_version)}</p>
{f'<div class=warn><strong>Avisos</strong><ul>{alerts}</ul></div>' if alerts else ''}
<p>
 <span class=kpi><b>{data['detection']['overall']['precision']:.1%}</b>Precision</span>
 <span class=kpi><b>{data['detection']['overall']['recall']:.1%}</b>Recall</span>
 <span class=kpi><b>{data['detection']['critical']['recall']:.1%}</b>Recall critic</span>
 <span class=kpi><b>{data['evidence']['quote_locatable_rate']:.1%}</b>Cites valides</span>
 <span class=kpi><b>{data['automatable']['automatable_rate']:.1%}</b>Automatitzable 4C</span>
</p>
{''.join(blocks)}
"""


def write_reports(
    summary: EvaluationSummary, details: list[dict[str, Any]], output_dir: Path
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{summary.dataset_id}-{summary.provider}-{summary.prompt_version}"
    paths = {
        "json": write_json(output_dir / f"{stem}.json", build_payload(summary, details)),
        "markdown": output_dir / f"{stem}.md",
        "html": output_dir / f"{stem}.html",
    }
    paths["markdown"].write_text(render_markdown(summary, details), encoding="utf-8")
    paths["html"].write_text(render_html(summary, details), encoding="utf-8")
    return paths
