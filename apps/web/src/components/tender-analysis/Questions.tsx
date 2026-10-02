import { Card, CardHeader } from "@/components/ui";
import type { AnalysisQuestion, AnalysisRun } from "@/lib/analysis";

import type { AnalysisLookup } from "./AnalysisTab";
import { criterionName, requirementTitle } from "./Citation";

/** L'àncora de la fila d'origen dins d'aquesta pestanya, només si la fila hi és. */
function originHref(q: AnalysisQuestion, run: AnalysisRun): string | null {
  if (q.requirement_id) return run.fit_assessments.some((f) => f.requirement_id === q.requirement_id) ? `#req-${q.requirement_id}` : null;
  if (q.criterion_id) return run.criterion_assessments.some((c) => c.award_criterion_id === q.criterion_id) ? `#crit-${q.criterion_id}` : null;
  return null;
}

function List({ title, items, lookup, run }: { title: string; items: AnalysisQuestion[]; lookup: AnalysisLookup; run: AnalysisRun }) {
  return (
    <section className="px-5 pb-4">
      <h3 className="text-[13px] font-semibold text-ink">{title}</h3>
      {items.length === 0 ? (
        <p className="text-body text-muted">Cap pregunta generada.</p>
      ) : (
        <ul className="mt-1 space-y-2">
          {items.map((q) => {
            const about = requirementTitle(lookup, q.requirement_id) ?? criterionName(lookup, q.criterion_id);
            const href = originHref(q, run);
            return (
              <li key={q.id} className="text-body text-ink">
                <p>{q.text}</p>
                {about && (
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
                )}
              </li>
            );
          })}
        </ul>
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
