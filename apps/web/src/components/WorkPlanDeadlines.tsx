"use client";

import { Card, CardHeader } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import { OBLIGATION_LABELS, type RequirementAnalysis } from "@/lib/requirements";
import { requirementsRoutedTo } from "@/lib/technical";
import type { WorkspaceDetail } from "@/lib/workspace";

interface Props {
  workspace: WorkspaceDetail;
}

/**
 * Els terminis del plec, al Pla de treball.
 *
 * Són requisits que el servidor encamina a `work_plan`: no demanen una solució
 * tècnica, però sí que marquen el calendari de la candidatura i del contracte.
 * Només es llegeixen; les tasques i la checklist continuen sent on eren.
 */
export function WorkPlanDeadlines({ workspace }: Props) {
  const analysis = useResource<RequirementAnalysis>(`/tenders/${workspace.tender_id}/analysis/requirements`);
  // Candidatura global amb lots: sense oferta no se sap on anem, i aquí només
  // es mostren els generals (la mateixa lectura prudent que Solució tècnica).
  const deadlines = analysis.data ? requirementsRoutedTo(analysis.data.requirements, "work_plan", workspace, new Set()) : [];
  if (deadlines.length === 0) return null;

  return (
    <Card>
      <CardHeader
        title={`Terminis del plec · ${deadlines.length}`}
        description="Dates i durades que el plec fixa. No demanen solució tècnica; serveixen per planificar."
      />
      <ul aria-label="Terminis del plec" className="divide-y divide-border">
        {deadlines.map((requirement) => {
          const source = requirement.evidence[0];
          return (
            <li key={requirement.id} className="px-3.5 py-2.5">
              <p className="text-[13.5px] font-medium text-ink">{requirement.title}</p>
              <p className="text-caption text-muted">
                {OBLIGATION_LABELS[requirement.obligation]}
                {requirement.description ? ` · ${requirement.description}` : ""}
                {source ? ` · ${source.document_name ?? "Plec"} · p. ${source.page_number}` : ""}
              </p>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}
