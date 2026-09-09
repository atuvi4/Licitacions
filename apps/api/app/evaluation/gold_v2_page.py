"""Pagina de revisio humana del gold v2 candidat.

Mateixa idea que la de la v1: un fitxer HTML que s'obre amb doble clic, sense
servidor ni xarxa, amb el text complet de la pagina al costat de l'anotacio.
Aqui s'hi afegeix el que aquesta iteracio necessita que una persona pugui jutjar:

* **la diferencia respecte de l'anotacio anterior**, camp a camp, quan el que
  es revisa es la correccio d'una anotacio que ja existia. Quan es una anotacio
  NOVA es diu explicitament, en comptes d'ensenyar una taula amb la columna
  "abans" buida que faria semblar que substitueix alguna cosa;
* **el que el text no decideix** (`unresolved`), en un lloc visible, perque el
  revisor no hagi de deduir que hi falta;
* **les cites addicionals**, quan una anotacio es reparteix en mes d'una pagina.

Les anotacions heretades es mostren, pero **sense botons**. Ja estan verificades
i el que s'afirma d'elles no ha canviat: donar-hi botons convidaria a redecidir
per accident una cosa que aquesta iteracio no ha tocat.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from app.evaluation.dataset import GoldDataset
from app.evaluation.dataset_v2 import (
    GoldAwardCriterionV2,
    GoldDatasetV2,
    GoldItemV2,
    dimensions_of,
)
from app.services.evidence_validation import locate_quote

#: Camps que es comparen amb l'anotacio anterior. Es la llista del que aquesta
#: iteracio pot haver canviat; la resta es copia i no cal ensenyar-la.
DIFFED_REQUIREMENT_FIELDS = (
    "obligation",
    "modality",
    "operator",
    "alternative_group",
    "equivalence_allowed",
    "condition_note",
    "value",
    "page_number",
    "quote",
)
DIFFED_AWARD_FIELDS = (
    "criterion_type",
    "points",
    "evaluation_type",
    "formula",
    "formula_text",
    "formula_variables",
    "page_number",
    "quote",
)


def _esc(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    return html.escape(str(value))


def _highlight(page_text: str, quote: str) -> str:
    match = locate_quote(page_text, quote)
    if match is None:
        return html.escape(page_text)
    before = html.escape(page_text[: match.char_start])
    hit = html.escape(page_text[match.char_start : match.char_end])
    after = html.escape(page_text[match.char_end :])
    return f"{before}<mark>{hit}</mark>{after}"


def _plain(value: Any) -> Any:
    """Forma comparable d'un camp, sigui d'un model v1 o v2."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_plain(entry) for entry in value]
    if hasattr(value, "value"):  # enums
        return value.value
    return value


def diff_fields(previous: Any, current: GoldItemV2) -> list[tuple[str, Any, Any]]:
    """Els camps que han canviat respecte de l'anotacio anterior.

    Si `previous` es `None` (no hi havia anotacio), tot el que estigui informat
    compta com a nou: el revisor ha de veure que no esta comparant amb res.
    """
    fields = (
        DIFFED_AWARD_FIELDS
        if isinstance(current, GoldAwardCriterionV2)
        else DIFFED_REQUIREMENT_FIELDS
    )
    changes: list[tuple[str, Any, Any]] = []
    for name in fields:
        after = _plain(getattr(current, name, None))
        before = _plain(getattr(previous, name, None)) if previous is not None else None
        if before != after:
            changes.append((name, before, after))
    return changes


def _quote_block(page_text: str | None, quote: str, revision: str, page_number: int) -> str:
    if page_text is None:
        return (
            "<pre class='page'><em>Pagina no disponible en aquesta base de dades: "
            "la cita no s'ha pogut comprovar.</em></pre>"
        )
    located = locate_quote(page_text, quote) is not None
    badge = (
        "<span class='ok'>cita localitzada</span>"
        if located
        else "<span class='ko'>CITA NO LOCALITZADA</span>"
    )
    return (
        f"<p class='meta'>Pagina {page_number} · revisio {html.escape(revision)} · {badge}</p>"
        f"<pre class='page'>{_highlight(page_text, quote)}</pre>"
    )


def _item_fields(item: GoldItemV2) -> str:
    if isinstance(item, GoldAwardCriterionV2):
        variables = (
            "<ul>"
            + "".join(
                f"<li><code>{_esc(v.symbol)}</code> — {_esc(v.meaning)}</li>"
                for v in item.formula_variables
            )
            + "</ul>"
            if item.formula_variables
            else "—"
        )
        return (
            f"<dt>Nom</dt><dd>{_esc(item.name)}</dd>"
            f"<dt>Tipus</dt><dd>{_esc(item.criterion_type)}</dd>"
            f"<dt>Punts (maxim)</dt><dd>{_esc(item.points)}</dd>"
            f"<dt>Avaluacio</dt><dd>{_esc(item.evaluation_type)}</dd>"
            f"<dt>Formula</dt><dd><code>{_esc(item.formula)}</code></dd>"
            f"<dt>Frase literal</dt><dd>{_esc(item.formula_text)}</dd>"
            f"<dt>Variables</dt><dd>{variables}</dd>"
        )
    return (
        f"<dt>Categoria</dt><dd>{_esc(item.category)}</dd>"
        f"<dt>Obligatorietat</dt><dd>{_esc(item.obligation)}</dd>"
        f"<dt>Modalitat</dt><dd><strong>{_esc(item.modality)}</strong></dd>"
        f"<dt>Grup d'alternatives</dt><dd>{_esc(item.alternative_group)}</dd>"
        f"<dt>Equivalencia</dt><dd>{_esc(item.equivalence_allowed)}</dd>"
        f"<dt>Operador</dt><dd>{_esc(item.operator)}</dd>"
        f"<dt>Condicio</dt><dd>{_esc(item.condition_note)}</dd>"
        f"<dt>value</dt><dd><pre class='value'>{_esc(item.value)}</pre></dd>"
        f"<dt>value_schema</dt><dd><code>{_esc(item.value_schema)}</code></dd>"
    )


def _diff_block(previous: Any, item: GoldItemV2) -> str:
    """La comparacio amb l'anotacio anterior, o l'avis que no n'hi ha cap.

    Ensenyar una taula de diferencies amb tota la columna "abans" buida faria
    semblar que aquesta anotacio en substitueix una altra. No en substitueix
    cap: s'afegeix, i el revisor ha de saber-ho abans de decidir.
    """
    if previous is None:
        refined = (
            f" Afina <code>{_esc(item.refines)}</code>, que es queda com esta."
            if item.refines
            else ""
        )
        return (
            "<h3>Anotacio nova</h3>"
            "<p class='meta'>No substitueix cap anotacio: el <code>gold_id</code> "
            f"<code>{_esc(item.gold_id)}</code> no existia al gold anterior.{refined}</p>"
        )

    changes = diff_fields(previous, item)
    if not changes:
        return "<h3>Que canvia</h3><p class='meta'>Cap canvi respecte de l'anterior.</p>"
    rows = "".join(
        f"<tr><td><code>{_esc(name)}</code></td>"
        f"<td class='was'><pre>{_esc(before)}</pre></td>"
        f"<td class='now'><pre>{_esc(after)}</pre></td></tr>"
        for name, before, after in changes
    )
    return (
        "<h3>Que canvia</h3>"
        "<table class='diff'><thead><tr><th>Camp</th><th>Abans (v1 revisada)</th>"
        f"<th>Proposta v2</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def _supersede_block(case: Any, item: GoldItemV2) -> str:
    """La decisio sobre l'anotacio que aquest refinament afina.

    No es informativa: sense marcar-la, el boto Verifica no desa res, igual que
    `apply_decisions` no aplica res. Es la mateixa regla als dos costats, i s'ha
    de veure abans de decidir, no descobrir-se despres en un error de consola.
    """
    if not item.refines:
        return ""
    refined = f"{case.case_id}::{item.refines}"
    return (
        "<div class='supersede-box'>"
        "<label><input type='checkbox' class='supersede'> "
        f"En verificar, <strong>substituir <code>{_esc(refined)}</code></strong>"
        "</label>"
        "<p class='meta'>Nomes dins del gold v2, i nomes a efectes de metriques. "
        f"<code>{_esc(refined)}</code> no s'esborra: conserva el text, la cita, el "
        "revisor i la data, i queda registrat que aquesta anotacio la substitueix. "
        "<code>gold-v1-reviewed</code> no es toca.</p>"
        "<p class='meta'>Si no la marques, Verifica no desara la decisio: les dues "
        "anotacions descriuen la mateixa exigencia i el gold no pot comptar-la dues "
        "vegades.</p>"
        "</div>"
    )


def _unresolved_block(item: GoldItemV2) -> str:
    if not item.unresolved:
        return ""
    entries = "".join(f"<li>{_esc(note)}</li>" for note in item.unresolved)
    return f"<div class='unresolved'><h4>El text no decideix</h4><ul>{entries}</ul></div>"


def render_candidate_page(
    dataset: GoldDatasetV2,
    source: GoldDataset,
    texts: dict[tuple[str, str, int], str],
) -> str:
    """Pagina autonoma: no necessita servidor ni xarxa."""
    previous_index = {
        f"{case.case_id}::{item.gold_id}": item
        for case in source.cases
        for item in [*case.requirements, *case.award_criteria]
    }

    proposal_blocks: list[str] = []
    inherited_rows: list[str] = []

    for case, item in dataset.items:
        item_id = f"{case.case_id}::{item.gold_id}"
        page_text = texts.get((case.case_id, item.document_name, item.page_number))
        revision = str(texts.get((case.case_id, item.document_name, -item.page_number), "?"))

        if not item.is_proposal:
            inherited_rows.append(
                f"<tr><td>{_esc(item_id)}</td>"
                f"<td><span class='badge s-{_esc(item.annotation_status)}'>"
                f"{_esc(item.annotation_status)}</span></td>"
                f"<td>{_esc(getattr(item, 'category', None) or item.__class__.__name__)}</td>"
                f"<td>{_esc(item.reviewed_by)}</td>"
                f"<td>{_esc(sorted(d.value for d in dimensions_of(item)) or '—')}</td></tr>"
            )
            continue

        extra = "".join(
            "<div class='extra'>"
            f"<h4>Cita addicional · {_esc(evidence.role)}</h4>"
            f"<blockquote>{_esc(evidence.quote)}</blockquote>"
            + _quote_block(
                texts.get((case.case_id, evidence.document_name, evidence.page_number)),
                evidence.quote,
                str(
                    texts.get(
                        (case.case_id, evidence.document_name, -evidence.page_number), "?"
                    )
                ),
                evidence.page_number,
            )
            + "</div>"
            for evidence in item.extra_evidence
        )

        proposal_blocks.append(
            f"""
<section class="item" data-id="{_esc(item_id)}">
  <header>
    <h2>{_esc(case.case_id)} · {_esc(item.gold_id)}
      <span class="badge s-{_esc(item.annotation_status)}">{_esc(item.annotation_status)}</span>
      <span class="badge proposal">proposta</span>
    </h2>
    <p class="meta">Expedient {_esc(case.expediente_number)} · {_esc(item.document_name)}
      · proposada per {_esc(item.annotated_by)}
      · mesura: {_esc(sorted(d.value for d in dimensions_of(item)) or "res")}</p>
    <p class="why">{_esc(item.proposal_reason)}</p>
    {
                '<p class="refines">Afina <code>'
                f"{_esc(case.case_id)}::{_esc(item.refines)}</code>, que esta verificada i "
                "no s'ha tocat. Si aquesta es verifica sense retirar l'altra, el gold "
                "demanara dues vegades la mateixa exigencia.</p>"
                if item.refines
                else ""
            }
  </header>
  <div class="cols">
    <div>
      <h3>Proposta v2</h3>
      <dl>{_item_fields(item)}<dt>Notes</dt><dd>{_esc(item.notes)}</dd></dl>
      {_unresolved_block(item)}
      {_diff_block(previous_index.get(item_id), item)}
      <div class="actions">
        <button data-act="verified">Verifica</button>
        <button data-act="rejected">Rebutja</button>
        <button data-act="needs_review">Necessita revisio</button>
        <input type="text" placeholder="nota de revisio (opcional)" class="note">
        <span class="state"></span>
      </div>
      {_supersede_block(case, item)}
    </div>
    <div>
      <h3>Cita proposada</h3>
      <blockquote>{_esc(item.quote)}</blockquote>
      {_quote_block(page_text, item.quote, revision, item.page_number)}
      {extra}
    </div>
  </div>
</section>"""
        )

    status = dataset.status_counts()
    dimensions = dataset.dimension_coverage()
    dimension_rows = "".join(
        f"<tr><td>{_esc(name)}</td><td>{counts['verified'] or 'n/d'}</td>"
        f"<td>{counts['pending']}</td></tr>"
        for name, counts in dimensions.items()
    )

    return f"""<!doctype html>
<meta charset="utf-8">
<title>Revisio del gold v2 · {_esc(dataset.dataset_id)}</title>
<style>
 body{{font:14px/1.55 system-ui,sans-serif;margin:1.5rem;color:#111;max-width:1600px}}
 h1{{margin-bottom:.25rem}}
 .item{{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}}
 .cols{{display:grid;grid-template-columns:minmax(380px,1fr) 1.3fr;gap:1.5rem}}
 .meta{{color:#666;font-size:12px;margin:.25rem 0 0}}
 .why{{background:#f4f7ff;border-left:3px solid #6b8cff;padding:.4rem .7rem;margin:.5rem 0 0;
   font-size:13px}}
 .refines{{background:#fff4f4;border-left:3px solid #e0787c;padding:.4rem .7rem;
   margin:.4rem 0 0;font-size:13px}}
 .supersede-box{{border:1px solid #e0787c;background:#fff8f8;border-radius:6px;
   padding:.5rem .75rem;margin:.6rem 0 0}}
 .supersede-box label{{font-size:13px;cursor:pointer}}
 .supersede-box p{{margin:.35rem 0 0}}
 dl{{display:grid;grid-template-columns:auto 1fr;gap:.15rem .75rem;margin:.5rem 0}}
 dt{{color:#666}} dd{{margin:0}}
 blockquote{{border-left:3px solid #bbb;margin:.5rem 0;padding:.25rem .75rem;
   font-style:italic;background:#fafafa}}
 pre.page{{white-space:pre-wrap;background:#fbfbfb;border:1px solid #eee;padding:.75rem;
   max-height:420px;overflow:auto;font:12px/1.5 ui-monospace,monospace}}
 pre.value{{white-space:pre-wrap;background:#fbfbfb;border:1px solid #eee;padding:.4rem;
   margin:0;font:12px/1.45 ui-monospace,monospace;max-height:260px;overflow:auto}}
 mark{{background:#ffe58a}}
 table{{border-collapse:collapse;width:100%;font-size:12px}}
 th,td{{border:1px solid #e3e3e3;padding:.3rem .45rem;text-align:left;vertical-align:top}}
 th{{background:#f6f6f6}}
 table.diff pre{{margin:0;white-space:pre-wrap;font:12px/1.45 ui-monospace,monospace}}
 td.was{{background:#fff6f6}} td.now{{background:#f3fbf3}}
 .unresolved{{background:#fffaf0;border:1px solid #ffe0a3;border-radius:6px;
   padding:.5rem .75rem;margin:.75rem 0}}
 .unresolved h4{{margin:0 0 .3rem;font-size:12px;text-transform:uppercase;color:#8a6d1f}}
 .unresolved ul{{margin:0;padding-left:1.1rem}}
 .extra{{margin-top:1rem;border-top:1px dashed #ddd;padding-top:.75rem}}
 .extra h4{{margin:0 0 .3rem;font-size:12px;color:#666}}
 .badge{{font-size:11px;padding:.1rem .45rem;border-radius:10px;border:1px solid #ccc;
   vertical-align:middle}}
 .s-draft{{background:#eee}} .s-verified{{background:#d8f5d8}}
 .s-rejected{{background:#ffd9d9}} .s-needs_review{{background:#ffeccc}}
 .proposal{{background:#e8eeff;border-color:#b9c8ff}}
 .ok{{color:#1a7f37}} .ko{{color:#b42318;font-weight:600}}
 .actions{{margin-top:.75rem;display:flex;gap:.4rem;align-items:center;flex-wrap:wrap}}
 button{{cursor:pointer;padding:.35rem .7rem;border:1px solid #bbb;border-radius:5px;
   background:#fff}}
 button.on{{background:#111;color:#fff;border-color:#111}}
 .note{{padding:.3rem;border:1px solid #ccc;border-radius:5px;min-width:220px}}
 .state{{font-size:12px;color:#555}}
 #bar{{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;
   padding:.75rem 0;display:flex;gap:1rem;align-items:center;z-index:5}}
 details{{margin:1.5rem 0}} summary{{cursor:pointer;font-weight:600}}
</style>
<h1>Revisio del gold v2 — {_esc(dataset.dataset_id)}</h1>
<p class="meta">Derivat de <code>{_esc(dataset.derived_from)}</code>, que no s'ha
 modificat · verified={status["verified"]} · draft={status["draft"]} ·
 needs_review={status["needs_review"]} · rejected={status["rejected"]}</p>
<p>Aqui nomes es decideixen les <strong>{len(proposal_blocks)} propostes</strong> d'aquesta
 versio. Les anotacions heretades ja estan verificades i es mostren al final nomes per
 consultar-les: el que s'hi afirma no ha canviat.</p>
<div id="bar">
  <strong id="count">0 decisions</strong>
  <button id="save">Descarrega decisions.json</button>
  <span class="state">Despres:
    <code>python -m app.evaluation.review --apply decisions.json --dataset
    {_esc(dataset.dataset_id)} --reviewer NOM --promote gold-v2</code></span>
</div>
{"".join(proposal_blocks)}
<details>
  <summary>Dimensions que el benchmark podra puntuar</summary>
  <p class="meta">Nomes una anotacio <strong>verificada</strong> fa mesurable una dimensio.
   <code>n/d</code> vol dir que no hi ha res contra que comparar: no es un 0%.</p>
  <table><thead><tr><th>Dimensio</th><th>Gold verificat</th>
   <th>Pendent de revisio</th></tr></thead><tbody>{dimension_rows}</tbody></table>
</details>
<details>
  <summary>Anotacions heretades ({len(inherited_rows)}) — nomes consulta</summary>
  <table><thead><tr><th>Anotacio</th><th>Estat</th><th>Categoria</th><th>Revisada per</th>
   <th>Dimensions que fixa</th></tr></thead><tbody>{"".join(inherited_rows)}</tbody></table>
</details>
<script>
// Les decisions viuen al navegador fins que es descarreguen. Res s'envia enlloc.
const KEY = "gold-review-{_esc(dataset.dataset_id)}";
const decisions = JSON.parse(localStorage.getItem(KEY) || "{{}}");

function paint() {{
  document.getElementById("count").textContent =
    Object.keys(decisions).length + " decisions";
  document.querySelectorAll(".item").forEach(section => {{
    const id = section.dataset.id;
    const current = decisions[id];
    section.querySelectorAll("button[data-act]").forEach(button => {{
      button.classList.toggle("on", Boolean(current) && current.status === button.dataset.act);
    }});
    section.querySelector(".state").textContent = current ? "desat localment" : "";
    if (current && current.note) section.querySelector(".note").value = current.note;
    const supersede = section.querySelector(".supersede");
    if (supersede && current) supersede.checked = Boolean(current.supersede_refined);
  }});
}}

document.querySelectorAll(".item").forEach(section => {{
  const id = section.dataset.id;
  section.querySelectorAll("button[data-act]").forEach(button => {{
    button.addEventListener("click", () => {{
      const supersede = section.querySelector(".supersede");
      if (button.dataset.act === "verified" && supersede && !supersede.checked) {{
        // Mateixa regla que al backend: verificar un refinament sense dir que
        // passa amb l'original seria comptar dues vegades la mateixa exigencia.
        section.querySelector(".state").textContent =
          "Marca la casella: cal dir que passa amb l'anotacio que aquesta afina.";
        return;
      }}
      decisions[id] = {{
        status: button.dataset.act,
        note: section.querySelector(".note").value || null
      }};
      if (supersede && button.dataset.act === "verified") {{
        decisions[id].supersede_refined = true;
      }}
      localStorage.setItem(KEY, JSON.stringify(decisions));
      paint();
    }});
  }});
  section.querySelector(".note").addEventListener("input", event => {{
    if (decisions[id]) {{
      decisions[id].note = event.target.value || null;
      localStorage.setItem(KEY, JSON.stringify(decisions));
    }}
  }});
}});

document.getElementById("save").addEventListener("click", () => {{
  const blob = new Blob([JSON.stringify({{
    dataset_id: "{_esc(dataset.dataset_id)}",
    decisions: decisions
  }}, null, 2)], {{type: "application/json"}});
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "decisions.json";
  link.click();
  URL.revokeObjectURL(link.href);
}});

paint();
</script>
"""


def write_candidate_page(dataset: GoldDatasetV2, page_html: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"review-{dataset.dataset_id}.html"
    path.write_text(page_html, encoding="utf-8")
    return path
