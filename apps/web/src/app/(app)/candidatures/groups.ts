import { isPendingDecision } from "@/components/shell/counts";
import {
  DECISION_LABELS,
  DECISION_LEVEL,
  DECISION_PENDING_LEVEL,
  ELIGIBILITY_LABELS,
  ELIGIBILITY_LEVEL,
  STEP_LABELS,
  stepOf,
  type Level,
  type WorkspaceStep,
} from "@/lib/labels";
import type { WorkspaceStatus, WorkspaceSummary } from "@/lib/workspace";

import { byDeadline } from "../avui/queue";

/**
 * Llistat de candidatures (handoff L7): cada candidatura cau en un sol grup
 * segons `stepOf(status)`, i tot el que es mostra surt dels camps de
 * `WorkspaceSummary`. La recomanació automàtica i la decisió humana són dues
 * coses; `undetermined` no és cap «ok»; els estats tancats continuen sent
 * diferents entre ells.
 */

export interface StepGroup {
  key: WorkspaceStep;
  label: string;
  rows: WorkspaceSummary[];
  collapsed: boolean;
}

const STEP_ORDER: WorkspaceStep[] = ["decidir", "preparar", "tancar", "tancada"];

export function groupWorkspaces(workspaces: WorkspaceSummary[]): StepGroup[] {
  const groups = STEP_ORDER.map((key) => ({
    key,
    label: STEP_LABELS[key],
    rows: workspaces.filter((workspace) => stepOf(workspace.status) === key).sort(byDeadline),
    collapsed: false,
  })).filter((group) => group.rows.length > 0);
  // Les tancades es pleguen només si hi ha feina oberta a sobre.
  return groups.map((group) => (group.key === "tancada" && groups.length > 1 ? { ...group, collapsed: true } : group));
}

export interface Pill {
  level: Level;
  label: string;
}

/** La decisió humana, o la seva absència dita clarament. */
export function decisionPill(workspace: WorkspaceSummary): Pill {
  if (workspace.human_decision) {
    return { level: DECISION_LEVEL[workspace.human_decision], label: DECISION_LABELS[workspace.human_decision] };
  }
  if (stepOf(workspace.status) === "tancada") return { level: "neutral", label: "Sense decisió" };
  return { level: DECISION_PENDING_LEVEL, label: "Pendent" };
}

/** Què demana atenció, només a partir de camps reals. `undetermined` no hi entra. */
export function attentionPills(workspace: WorkspaceSummary): Pill[] {
  const pills: Pill[] = [];
  if (workspace.deadline_passed) pills.push({ level: "blocking", label: "Termini tancat" });
  if (workspace.eligibility === "no_go") {
    pills.push({ level: ELIGIBILITY_LEVEL.no_go, label: `Elegibilitat: ${ELIGIBILITY_LABELS.no_go}` });
  }
  if (workspace.analysis_outdated) pills.push({ level: "attention", label: "Anàlisi endarrerida" });
  return pills;
}

const NEXT_STEP: Record<WorkspaceStatus, string> = {
  analysis: "Obre",
  needs_review: "Revisa",
  awaiting_decision: "Decideix",
  go_approved: "Prepara",
  preparing_bid: "Prepara",
  ready_for_final_review: "Revisa el dossier",
  approved_for_submission: "Obre",
  submitted: "Obre",
  no_go_approved: "Obre",
  withdrawn: "Obre",
};

/** El següent pas disponible, derivat només de l'estat del backend. */
export function nextStep(workspace: WorkspaceSummary): string {
  return NEXT_STEP[workspace.status];
}

export function isOpen(workspace: WorkspaceSummary): boolean {
  return stepOf(workspace.status) !== "tancada";
}

export function summaryLine(workspaces: WorkspaceSummary[]): string {
  const open = workspaces.filter(isOpen).length;
  const closed = workspaces.length - open;
  const total = `${workspaces.length} ${workspaces.length === 1 ? "candidatura" : "candidatures"}`;
  return `${total} · ${open} ${open === 1 ? "oberta" : "obertes"} · ${closed} ${closed === 1 ? "tancada" : "tancades"}`;
}

export { isPendingDecision };
