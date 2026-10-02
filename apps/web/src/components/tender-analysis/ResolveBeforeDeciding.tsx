import Link from "next/link";

import { Card, CardHeader, StatusPill } from "@/components/ui";
import { explanation, label, levelOf, resolveFor, sortFindings, summaryOf, type AnalysisRun } from "@/lib/analysis";

import type { AnalysisLookup } from "./AnalysisTab";
import { Citation, criterionName, requirementTitle, scopeItemLabel } from "./Citation";

export function ResolveBeforeDeciding({ run, lookup, tenderId }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  const findings = sortFindings(run.findings);
  const questions = summaryOf(run).questions;
  const total = questions.buyer + questions.company;
  return (
    <Card id="que-cal-resoldre" className="border-2 border-brand/30">
      <CardHeader
        title="Què cal resoldre abans de decidir"
        description="Possibles bloqueigs, revisions, evidència i informació de l'empresa que falta, i les preguntes generades. Els possibles bloqueigs no estan confirmats."
      />
      {findings.length === 0 ? (
        <p className="px-5 pb-5 text-body text-muted">No hi ha cap element pendent de resoldre en aquesta anàlisi.</p>
      ) : (
        <ul className="divide-y divide-border">
          {findings.map((finding) => {
            const destination = resolveFor(finding.explanation_code);
            const subject = requirementTitle(lookup, finding.requirement_id) ?? criterionName(lookup, finding.criterion_id) ?? scopeItemLabel(lookup, finding.scope_item_id);
            const requirement = finding.requirement_id ? lookup.requirements.get(finding.requirement_id) : undefined;
            const criterion = finding.criterion_id ? lookup.criteria.get(finding.criterion_id) : undefined;
            return (
              <li key={finding.id} className="flex flex-col gap-2 px-5 py-3.5 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0 space-y-1">
                  <StatusPill level={levelOf("finding_type", finding.finding_type)} size="sm">
                    {label("finding_type", finding.finding_type)}
                  </StatusPill>
                  {subject && <p className="text-body font-medium text-ink">{subject}</p>}
                  <p className="text-body text-ink">{explanation(finding.explanation_code)}</p>
                  <Citation tenderId={tenderId} requirement={requirement} criterion={criterion} />
                </div>
                {destination && (
                  <Link href={destination.href} className="inline-flex min-h-11 shrink-0 items-center text-body font-medium text-brand hover:underline md:min-h-0">
                    On es resol: {destination.label}
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {total > 0 && (
        <p className="border-t border-border px-5 py-3 text-body text-ink">
          <a href="#preguntes" className="text-brand hover:underline">
            {total === 1 ? "1 pregunta generada" : `${total} preguntes generades`}
          </a>{" "}
          a tractar.
        </p>
      )}
    </Card>
  );
}
