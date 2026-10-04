import { StatusPill } from "@/components/ui";
import { formatPoints as pts, label, levelOf, summaryOf, summarySentences, type AnalysisRun } from "@/lib/analysis";

/** Resum factual: només els comptadors del motor. Cap score, percentatge ni recomanació. */
export function AnalysisSummary({ run, compact = false, scopeItemCount = null }: { run: AnalysisRun; compact?: boolean; scopeItemCount?: number | null }) {
  const summary = summaryOf(run);
  const fits: Array<[string, number]> = [
    ["satisfies", summary.fit.satisfies],
    ["does_not_satisfy", summary.fit.does_not_satisfy],
    ["unknown", summary.fit.unknown],
    ["not_applicable", summary.fit.not_applicable],
  ];
  return (
    <section aria-label="Resum de l'anàlisi" className="space-y-3">
      <dl className={compact ? "grid grid-cols-1 gap-3" : "grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"}>
        <div>
          <dt className="text-caption font-medium text-muted">Requisits</dt>
          <dd className="mt-1 flex flex-wrap gap-1.5">
            {fits.map(([value, count]) => (
              <StatusPill key={value} level={levelOf("fit_result", value)} size="sm" outline>
                {label("fit_result", value)}: {count}
              </StatusPill>
            ))}
          </dd>
        </div>
        <div>
          <dt className="text-caption font-medium text-muted">Punts (de {pts(summary.max_points)})</dt>
          <dd className="mt-1 text-body text-ink tabular-nums">
            Assegurats {pts(summary.secured_points)} · En risc {pts(summary.at_risk_points)} · Perduts {pts(summary.lost_points)} · No avaluables{" "}
            {pts(summary.not_assessable_points)}
          </dd>
        </div>
        {!compact && (
          <div>
            <dt className="text-caption font-medium text-muted">Vies d&apos;execució (candidats)</dt>
            <dd className="mt-1 text-body text-ink tabular-nums">
              disponibles {summary.delivery.available} · pendents {summary.delivery.pending} · no disponibles {summary.delivery.unavailable}
              {scopeItemCount !== null && (
                <span className="block text-caption text-muted">
                  {scopeItemCount === 1 ? "1 element d'abast al plec" : `${scopeItemCount} elements d'abast al plec`}
                </span>
              )}
            </dd>
          </div>
        )}
        <div>
          <dt className="text-caption font-medium text-muted">Preguntes generades</dt>
          <dd className="mt-1 text-body text-ink tabular-nums">
            Per al comprador {summary.questions.buyer} · Per a l&apos;empresa {summary.questions.company}
          </dd>
        </div>
      </dl>
      <ul className="space-y-1 text-body text-ink">
        {summarySentences(summary).map((sentence) => (
          <li key={sentence}>{sentence}</li>
        ))}
      </ul>
    </section>
  );
}
