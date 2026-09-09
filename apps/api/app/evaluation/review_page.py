"""Revisio humana del gold: pagina HTML local + aplicacio de decisions.

No es cap plataforma d'anotacio. Es una pagina que es pot obrir amb doble clic,
que ensenya cada anotacio **al costat del text complet de la pagina original**
—que es l'unica manera honesta de verificar-la— i tres botons. Les decisions
s'acumulen al navegador i es descarreguen com un JSON que despres s'aplica al
dataset amb una ordre.

Per que en dos passos i no una aplicacio amb servidor: perque el resultat de la
revisio ha de ser un fitxer versionable a Git, revisable en un pull request. Una
base de dades amagaria qui va verificar que i quan.
"""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.corpus import resolve_tender
from app.evaluation.dataset import (
    AnnotationStatus,
    GoldDataset,
    save_dataset,
)
from app.evaluation.refinements import (
    apply_supersessions,
    check_decisions,
    check_no_double_counting,
)
from app.models.tender_documents import (
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tenders import TenderDocument
from app.services.evidence_validation import locate_quote


async def page_texts(
    session: AsyncSession, dataset: GoldDataset
) -> dict[tuple[str, str, int], str]:
    """Text complet de cada pagina citada, per (case_id, document, pagina).

    El tender es resol amb la MATEIXA regla que fa servir el benchmark
    (`corpus.resolve_tender`). Abans es buscava nomes per expedient, i com que
    l'expedient no es unic, una persona podia estar verificant una cita contra
    un document i el benchmark mesurar-ne un altre. Si el cas apunta a un tender
    sense pagines processades, aqui no hi ha text i es veu: es preferible que
    salti a la vista que no pas omplir-ho amb el document d'un altre expedient
    homonim.
    """
    texts: dict[tuple[str, str, int], str] = {}
    for case in dataset.cases:
        tender = await resolve_tender(session, case)
        if tender is None:
            continue
        wanted = {
            (item.document_name, item.page_number)
            for item in [*case.requirements, *case.award_criteria]
        }
        for document_name, page_number in wanted:
            row = (
                await session.execute(
                    page_query(tender.id, document_name, page_number)
                )
            ).first()
            if row:
                texts[(case.case_id, document_name, page_number)] = row[0]
                texts[(case.case_id, document_name, -page_number)] = str(row[1])
    return texts


def page_query(tender_id: Any, document_name: str, page_number: int) -> sa.Select:
    """El text i la revisio d'una pagina concreta d'un tender concret.

    Escrit amb l'ORM i no amb SQL cru perque el `tender_id` es un UUID i cada
    dialecte el representa a la seva manera; deixar-ho al dialecte fa que la
    mateixa consulta funcioni contra PostgreSQL i contra la base de dades dels
    tests.
    """
    return (
        sa.select(TenderDocumentPage.text, TenderDocumentExtraction.revision_number)
        .join(
            TenderDocumentExtraction,
            TenderDocumentExtraction.id == TenderDocumentPage.extraction_id,
        )
        .join(
            TenderDocumentFile,
            TenderDocumentFile.content_id == TenderDocumentExtraction.content_id,
        )
        .join(TenderDocument, TenderDocument.id == TenderDocumentFile.tender_document_id)
        .where(
            TenderDocumentExtraction.is_current.is_(True),
            TenderDocument.tender_id == tender_id,
            TenderDocument.name == document_name,
            TenderDocumentPage.page_number == page_number,
        )
        .limit(1)
    )


def _highlight(page_text: str, quote: str) -> str:
    """Marca el quote dins del text complet, si s'hi localitza."""
    match = locate_quote(page_text, quote)
    if match is None:
        return html.escape(page_text)
    before = html.escape(page_text[: match.char_start])
    hit = html.escape(page_text[match.char_start : match.char_end])
    after = html.escape(page_text[match.char_end :])
    return f"{before}<mark>{hit}</mark>{after}"


def render_review_page(
    dataset: GoldDataset, texts: dict[tuple[str, str, int], str]
) -> str:
    """Pagina autonoma: no necessita servidor ni xarxa."""

    def esc(value: Any) -> str:
        return html.escape(str(value)) if value is not None else "—"

    blocks: list[str] = []
    for case in dataset.cases:
        items: list[tuple[str, Any]] = [("requirement", r) for r in case.requirements]
        items += [("award", c) for c in case.award_criteria]

        for kind, item in items:
            key = (case.case_id, item.document_name, item.page_number)
            page_text = texts.get(key)
            revision = texts.get((case.case_id, item.document_name, -item.page_number), "?")
            item_id = f"{case.case_id}::{item.gold_id}"

            if kind == "requirement":
                fields = (
                    f"<dt>Categoria</dt><dd>{esc(item.category)}</dd>"
                    f"<dt>Obligatorietat</dt><dd>{esc(item.obligation)}</dd>"
                    f"<dt>Operador</dt><dd>{esc(item.operator)}</dd>"
                    f"<dt>Abast</dt><dd>{esc(item.scope)}</dd>"
                    "<dt>value_json</dt><dd><code>"
                    f"{esc(json.dumps(item.value, ensure_ascii=False))}</code></dd>"
                    f"<dt>Critic</dt><dd>{esc(item.is_critical)}</dd>"
                )
            else:
                fields = (
                    f"<dt>Nom</dt><dd>{esc(item.name)}</dd>"
                    f"<dt>Punts</dt><dd>{esc(item.points)}</dd>"
                    f"<dt>Avaluacio</dt><dd>{esc(item.evaluation_type)}</dd>"
                    f"<dt>Formula</dt><dd>{esc(item.formula)}</dd>"
                )

            body = (
                _highlight(page_text, item.quote)
                if page_text
                else (
                    "<em>Pagina no disponible: el document no esta processat "
                    "en aquesta base de dades.</em>"
                )
            )

            blocks.append(
                f"""
<section class="item" data-id="{esc(item_id)}">
  <header>
    <h2>{esc(case.case_id)} · {esc(item.gold_id)}
      <span class="badge s-{esc(item.annotation_status)}">{esc(item.annotation_status)}</span>
    </h2>
    <p class="meta">Expedient {esc(case.expediente_number)} · {esc(item.document_name)}
       · revisio {esc(revision)} · pagina {esc(item.page_number)}
       · anotat per {esc(item.annotated_by)}</p>
  </header>
  <div class="cols">
    <div>
      <h3>Anotacio</h3>
      <dl>{fields}<dt>Notes</dt><dd>{esc(item.notes)}</dd></dl>
      <h3>Cita anotada</h3>
      <blockquote>{esc(item.quote)}</blockquote>
      <div class="actions">
        <button data-act="verified">Verifica</button>
        <button data-act="rejected">Rebutja</button>
        <button data-act="needs_review">Necessita revisio</button>
        <input type="text" placeholder="nota de revisio (opcional)" class="note">
        <span class="state"></span>
      </div>
    </div>
    <div>
      <h3>Text complet de la pagina</h3>
      <pre class="page">{body}</pre>
    </div>
  </div>
</section>"""
            )

    coverage = dataset.coverage()
    return f"""<!doctype html>
<meta charset="utf-8">
<title>Revisio del gold · {esc(dataset.dataset_id)}</title>
<style>
 body{{font:14px/1.55 system-ui,sans-serif;margin:1.5rem;color:#111;max-width:1500px}}
 h1{{margin-bottom:.25rem}}
 .item{{border:1px solid #ddd;border-radius:8px;padding:1rem;margin:1rem 0}}
 .cols{{display:grid;grid-template-columns:minmax(320px,1fr) 1.4fr;gap:1.5rem}}
 .meta{{color:#666;font-size:12px;margin:.25rem 0 0}}
 dl{{display:grid;grid-template-columns:auto 1fr;gap:.15rem .75rem;
   margin:.5rem 0}}
 dt{{color:#666}} dd{{margin:0}}
 blockquote{{border-left:3px solid #bbb;margin:.5rem 0;padding:.25rem .75rem;
   font-style:italic;background:#fafafa}}
 pre.page{{white-space:pre-wrap;background:#fbfbfb;border:1px solid #eee;padding:.75rem;
   max-height:460px;overflow:auto;font:12px/1.5 ui-monospace,monospace}}
 mark{{background:#ffe58a}}
 .badge{{font-size:11px;padding:.1rem .45rem;border-radius:10px;
   border:1px solid #ccc;vertical-align:middle}}
 .s-draft{{background:#eee}} .s-verified{{background:#d8f5d8}}
 .s-rejected{{background:#ffd9d9}} .s-needs_review{{background:#ffeccc}}
 .actions{{margin-top:.75rem;display:flex;gap:.4rem;align-items:center;
   flex-wrap:wrap}}
 button{{cursor:pointer;padding:.35rem .7rem;border:1px solid #bbb;
   border-radius:5px;background:#fff}}
 button.on{{background:#111;color:#fff;border-color:#111}}
 .note{{padding:.3rem;border:1px solid #ccc;border-radius:5px;min-width:220px}}
 .state{{font-size:12px;color:#555}}
 #bar{{position:sticky;top:0;background:#fff;border-bottom:1px solid #ddd;padding:.75rem 0;
   display:flex;gap:1rem;align-items:center;z-index:5}}
</style>
<h1>Revisio del gold — {esc(dataset.dataset_id)}</h1>
<p class="meta">Estat actual: {esc(coverage)}</p>
<div id="bar">
  <strong id="count">0 decisions</strong>
  <button id="save">Descarrega decisions.json</button>
  <span class="state">Despres:
    <code>python -m app.evaluation.review --apply decisions.json --reviewer NOM</code></span>
</div>
{''.join(blocks)}
<script>
// Les decisions viuen al navegador fins que es descarreguen. Res s'envia enlloc.
const KEY = "gold-review-{esc(dataset.dataset_id)}";
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
  }});
}}

document.querySelectorAll(".item").forEach(section => {{
  const id = section.dataset.id;
  section.querySelectorAll("button[data-act]").forEach(button => {{
    button.addEventListener("click", () => {{
      decisions[id] = {{
        status: button.dataset.act,
        note: section.querySelector(".note").value || null
      }};
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
    dataset_id: "{esc(dataset.dataset_id)}",
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


def apply_decisions(
    dataset: GoldDataset,
    decisions: dict[str, Any],
    *,
    reviewer: str,
    reviewed_on: date | None = None,
) -> tuple[GoldDataset, dict[str, int]]:
    """Aplica les decisions humanes i deixa constancia de qui i quan.

    No toca les etiquetes: canviar una categoria o un import es una correccio
    del gold i ha de passar per `revisions`, no per la revisio d'estat.

    L'excepcio son els refinaments: verificar una anotacio que n'afina una altra
    obliga a dir que passa amb l'original. Es comprova ABANS de tocar res i
    aixeca `RefinementConflict` si no s'ha resolt, perque el resultat d'aplicar
    decisions a mitges seria un gold que compta dues vegades el mateix.
    """
    check_decisions(dataset, decisions or {})

    when = reviewed_on or date.today()
    applied = dict.fromkeys((s.value for s in AnnotationStatus), 0)
    applied["unknown_id"] = 0
    applied["superseded"] = 0

    index = {
        f"{case.case_id}::{item.gold_id}": item
        for case in dataset.cases
        for item in [*case.requirements, *case.award_criteria]
    }

    for item_id, decision in (decisions or {}).items():
        item = index.get(item_id)
        if item is None:
            applied["unknown_id"] += 1
            continue
        status = AnnotationStatus(decision["status"])
        item.annotation_status = status
        item.reviewed_by = reviewer
        item.reviewed_at = when
        item.review_note = decision.get("note")
        applied[status.value] += 1

    outcome = apply_supersessions(dataset, decisions or {}, reviewer=reviewer, when=when)
    dataset.revisions.extend(outcome.revisions)
    applied["superseded"] = len(outcome.superseded)

    # Ultima comprovacio abans de retornar: encara que algu afegeixi un cami nou
    # per aplicar decisions, aixo ha de seguir sent el que no deixa passar un
    # gold que compta dues vegades la mateixa exigencia.
    check_no_double_counting(dataset)

    return dataset, applied


def write_review_page(dataset: GoldDataset, page_html: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"review-{dataset.dataset_id}.html"
    path.write_text(page_html, encoding="utf-8")
    return path


def promote(dataset: GoldDataset, new_id: str) -> GoldDataset:
    """Crea una versio explicita del dataset revisat.

    Un benchmark ha de guardar exactament quina versio va fer servir; per aixo
    la revisio no sobreescriu `gold-v1`, en crea una de nova.
    """
    promoted = dataset.model_copy(deep=True)
    promoted.dataset_id = new_id
    promoted.version = dataset.version + 1
    return promoted


def save_promoted(dataset: GoldDataset, base_dir: Path | None = None) -> Path:
    return save_dataset(dataset, base_dir=base_dir)
