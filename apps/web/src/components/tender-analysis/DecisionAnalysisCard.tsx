"use client";

import { AnalysisSummary } from "@/components/tender-analysis/AnalysisSummary";
import { Card, CardHeader, LinkButton, StatusPill } from "@/components/ui";
import { analysisPath, type AnalysisRun } from "@/lib/analysis";
import { useResource } from "@/lib/hooks";
import { formatDateTime } from "@/lib/labels";

/** Lectura de l'anàlisi al pas Decidir. Fa servir els mateixos helpers; mai fa cap POST. */
export function DecisionAnalysisCard({ companyId, tenderId }: { companyId: string; tenderId: string }) {
  const current = useResource<AnalysisRun>(`${analysisPath(companyId, tenderId)}/current`);
  const href = `/licitacions/${tenderId}?tab=analisi`;
  const body = (() => {
    if (current.status === 404) return <p className="text-body text-muted">Encara no hi ha anàlisi d&apos;aquesta licitació.</p>;
    if (current.error) return <p className="text-body text-muted">No s&apos;ha pogut carregar l&apos;anàlisi.</p>;
    if (!current.data) return null;
    const pending = current.data.findings.length;
    return (
      <div className="space-y-3">
        <p className="text-caption text-muted">Feta el {formatDateTime(current.data.created_at)}.</p>
        {current.data.stale && (
          <StatusPill level="attention" size="sm">
            Desactualitzada
          </StatusPill>
        )}
        <AnalysisSummary run={current.data} compact />
        <p className="text-body text-ink">
          {pending === 0
            ? "No hi ha cap element pendent de resoldre en aquesta anàlisi."
            : `${pending === 1 ? "1 element a resoldre" : `${pending} elements a resoldre`} abans de decidir.`}
        </p>
      </div>
    );
  })();
  const stale = Boolean(current.data?.stale);
  return (
    <Card>
      <CardHeader title="Anàlisi del plec" description="El que Tuvi sap i no sap d'aquesta licitació. La decisió és vostra." />
      <div className="space-y-3 px-5 pb-5">
        {body}
        <LinkButton href={href} variant="secondary" className="min-h-11 md:min-h-0">
          {stale ? "Revisa i recalcula a la pestanya Anàlisi" : "Veure l'anàlisi"}
        </LinkButton>
      </div>
    </Card>
  );
}
