import { Card, CardHeader, StatusPill } from "@/components/ui";
import { explanation, formatPoints as pts, label, levelOf, summaryOf, type AnalysisRun } from "@/lib/analysis";

import type { AnalysisLookup } from "./AnalysisTab";
import { Citation, criterionName } from "./Citation";

export function CriteriaPoints({ run, lookup, tenderId }: { run: AnalysisRun; lookup: AnalysisLookup; tenderId: string }) {
  const summary = summaryOf(run);
  return (
    <Card id="criteris-analisi">
      <CardHeader
        title="Criteris i punts"
        description="Punts segons el que les dades actuals asseguren, deixen en risc o fan perdre; els que aquest pas no pot avaluar es compten a part, com a no avaluables."
      />
      {run.criterion_assessments.length === 0 ? (
        <p className="px-5 pb-5 text-body text-muted">El plec no té criteris de puntuació analitzats.</p>
      ) : (
        <>
          <p className="px-5 pt-3 text-caption text-muted md:hidden">Desplaça horitzontalment per veure totes les columnes.</p>
          {/* A mòbil la taula és més ampla que la pantalla: la regió es pot enfocar i desplaçar amb el teclat. */}
          <div
            role="region"
            aria-label="Criteris i punts (desplaçable)"
            tabIndex={0}
            className="overflow-x-auto px-5 pb-5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
          >
            <table aria-label="Criteris i punts" className="w-full min-w-[40rem] text-left text-body">
              <thead className="text-caption text-muted">
                <tr>
                  <th scope="col" className="py-2">Criteri</th>
                  <th scope="col">Estat</th>
                  <th scope="col" className="whitespace-nowrap px-3 text-right">Màxim</th>
                  <th scope="col" className="whitespace-nowrap px-3 text-right">Assegurats</th>
                  <th scope="col" className="whitespace-nowrap px-3 text-right">En risc</th>
                  <th scope="col" className="whitespace-nowrap px-3 text-right">Perduts</th>
                  <th scope="col" className="whitespace-nowrap px-3 text-right">No avaluables</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {run.criterion_assessments.map((c) => (
                  <tr key={c.id} id={`crit-${c.award_criterion_id}`} className="scroll-mt-4 align-top">
                    <td className="py-2.5 pr-3">
                      <p className="font-medium text-ink">{criterionName(lookup, c.award_criterion_id)}</p>
                      <p className="text-caption text-muted">{explanation(c.explanation_code)}</p>
                      <Citation tenderId={tenderId} criterion={lookup.criteria.get(c.award_criterion_id)} />
                    </td>
                    <td className="py-2.5 pr-3">
                      <StatusPill level={levelOf("points_status", c.points_status)} size="sm">
                        {label("points_status", c.points_status)}
                      </StatusPill>
                      <p className="mt-1 text-caption text-muted">{label("criterion_result", c.criterion_result)}</p>
                    </td>
                    <td className="px-3 text-right tabular-nums">{pts(c.max_points)}</td>
                    <td className="px-3 text-right tabular-nums">{pts(c.secured_points)}</td>
                    <td className="px-3 text-right tabular-nums">{pts(c.at_risk_points)}</td>
                    <td className="px-3 text-right tabular-nums">{pts(c.lost_points)}</td>
                    <td className="px-3 text-right tabular-nums">{pts(c.not_assessable_points)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-border font-medium">
                  <td className="py-2.5">Total</td>
                  <td />
                  <td className="px-3 text-right tabular-nums">{pts(summary.max_points)}</td>
                  <td className="px-3 text-right tabular-nums">{pts(summary.secured_points)}</td>
                  <td className="px-3 text-right tabular-nums">{pts(summary.at_risk_points)}</td>
                  <td className="px-3 text-right tabular-nums">{pts(summary.lost_points)}</td>
                  <td className="px-3 text-right tabular-nums">{pts(summary.not_assessable_points)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </>
      )}
    </Card>
  );
}
