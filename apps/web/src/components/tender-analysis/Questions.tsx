import { useId } from "react";

import { Card, CardHeader } from "@/components/ui";
import { groupQuestions, type AnalysisQuestion, type AnalysisRun } from "@/lib/analysis";

import type { AnalysisLookup } from "./AnalysisTab";
import { criterionName, requirementTitle, scopeItemLabel } from "./Citation";

/** L'àncora de la fila d'origen dins d'aquesta pestanya, només si la fila hi és. */
function originHref(q: AnalysisQuestion, run: AnalysisRun): string | null {
  if (q.requirement_id) return run.fit_assessments.some((f) => f.requirement_id === q.requirement_id) ? `#req-${q.requirement_id}` : null;
  if (q.criterion_id) return run.criterion_assessments.some((c) => c.award_criterion_id === q.criterion_id) ? `#crit-${q.criterion_id}` : null;
  return null;
}

function About({ q, lookup, run }: { q: AnalysisQuestion; lookup: AnalysisLookup; run: AnalysisRun }) {
  const about = requirementTitle(lookup, q.requirement_id) ?? criterionName(lookup, q.criterion_id);
  if (!about) return null;
  const href = originHref(q, run);
  return (
    <p className="text-caption text-muted">
      Sobre:{" "}
      {href ? (
        <a href={href} className="inline-flex min-h-11 items-center text-brand hover:underline md:min-h-0">
          {about}
        </a>
      ) : (
        about
      )}
    </p>
  );
}

/** Element d'abast i, si n'hi ha, la seu (codi i nom). Mai un identificador. */
function affectedLabel(q: AnalysisQuestion, lookup: AnalysisLookup): string {
  const label = scopeItemLabel(lookup, q.scope_item_id) ?? "Element d'abast sense nom disponible";
  const item = lookup.structure?.scope_items.find((s) => s.id === q.scope_item_id);
  const site = item?.site_id ? lookup.structure?.sites.find((s) => s.id === item.site_id) : undefined;
  const siteText = [site?.code, site?.name].filter(Boolean).join(" · ");
  return siteText ? `${label} — ${siteText}` : label;
}

function GroupItem({ items, lookup, run }: { items: AnalysisQuestion[]; lookup: AnalysisLookup; run: AnalysisRun }) {
  const listId = useId();
  const first = items[0];
  // Un mateix origen («Sobre») només un cop dins del grup.
  const seen = new Set<string>();
  const origins = items.filter((q) => {
    const k = `${q.requirement_id ?? ""}|${q.criterion_id ?? ""}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
  // Els elements d'abast afectats, cadascun un cop: «Afecta N» compta elements diferents, no
  // preguntes (el total de preguntes surt a la capçalera de la llista).
  const seenItems = new Set<string>();
  const affected = items.filter((q) => {
    if (!q.scope_item_id || seenItems.has(q.scope_item_id)) return false;
    seenItems.add(q.scope_item_id);
    return true;
  });
  return (
    <li className="text-body text-ink">
      <p>{first.text}</p>
      {affected.length === 1 && <p className="text-caption text-muted">Afecta: {affectedLabel(affected[0], lookup)}</p>}
      {affected.length > 1 && (
        <>
          <p id={listId} className="text-caption text-muted">
            Afecta {affected.length} elements o seus:
          </p>
          <ul aria-labelledby={listId} className="ml-4 list-disc text-caption text-muted">
            {affected.map((q) => (
              <li key={q.id}>{affectedLabel(q, lookup)}</li>
            ))}
          </ul>
        </>
      )}
      {origins.map((q) => (
        <About key={q.id} q={q} lookup={lookup} run={run} />
      ))}
    </li>
  );
}

function List({ title, items, lookup, run }: { title: string; items: AnalysisQuestion[]; lookup: AnalysisLookup; run: AnalysisRun }) {
  const groups = groupQuestions(items);
  const grouped = groups.filter((g) => g.items.length > 1).length;
  return (
    <section className="px-5 pb-4">
      <h3 className="text-[13px] font-semibold text-ink">{title}</h3>
      {items.length === 0 ? (
        <p className="text-body text-muted">Cap pregunta generada.</p>
      ) : (
        <>
          {grouped > 0 && (
            <p className="text-caption text-muted">
              {items.length} preguntes ({grouped} {grouped === 1 ? "grup" : "grups"})
            </p>
          )}
          <ul className="mt-1 space-y-2">
            {groups.map((g) => (
              <GroupItem key={g.key} items={g.items} lookup={lookup} run={run} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

export function Questions({ run, lookup }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  return (
    <Card id="preguntes">
      <CardHeader title="Preguntes" description="Preguntes generades per l'anàlisi, a tractar abans de decidir." />
      <List title="Per al comprador" items={run.questions.filter((q) => q.audience === "buyer")} lookup={lookup} run={run} />
      <List title="Per a l'empresa" items={run.questions.filter((q) => q.audience === "company")} lookup={lookup} run={run} />
    </Card>
  );
}
