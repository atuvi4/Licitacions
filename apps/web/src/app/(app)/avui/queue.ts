import { isPendingDecision } from "@/components/shell/counts";
import { ELIGIBILITY_HINTS, ELIGIBILITY_LABELS, WARNING_LABELS, deadlineLabel, type Level } from "@/lib/labels";
import type { WorkspaceSummary } from "@/lib/workspace";

/**
 * La cua d'«Avui» (handoff L5.6), composta al client només amb els camps de
 * `WorkspaceSummary`. El nom de cada bloqueig no hi és (cas C-3): aquí es diu
 * per què una candidatura demana atenció, amb el text dels diccionaris.
 */

/** Termini ascendent; sense termini al final. Ordenació estable. */
function byDeadline(a: WorkspaceSummary, b: WorkspaceSummary): number {
  if (a.days_remaining === null && b.days_remaining === null) return 0;
  if (a.days_remaining === null) return 1;
  if (b.days_remaining === null) return -1;
  return a.days_remaining - b.days_remaining;
}

export function pendingDecisions(workspaces: WorkspaceSummary[]): WorkspaceSummary[] {
  return workspaces.filter(isPendingDecision).sort(byDeadline);
}

/** Un termini `null` no és cap bloqueig: només ho és `deadline_passed`. */
export function isOpenBlocker(workspace: WorkspaceSummary): boolean {
  return workspace.eligibility === "no_go" || workspace.analysis_outdated || workspace.deadline_passed;
}

export function openBlockers(workspaces: WorkspaceSummary[]): WorkspaceSummary[] {
  return workspaces.filter(isOpenBlocker).sort(byDeadline);
}

export interface BlockerReason {
  level: Level;
  label: string;
  detail: string;
}

export function blockerReasons(workspace: WorkspaceSummary): BlockerReason[] {
  const reasons: BlockerReason[] = [];
  if (workspace.deadline_passed) {
    reasons.push({
      level: "blocking",
      label: (workspace.days_remaining !== null && deadlineLabel(workspace.days_remaining)) || "Termini tancat",
      detail: WARNING_LABELS.deadline_passed,
    });
  }
  if (workspace.eligibility === "no_go") {
    reasons.push({ level: "blocking", label: ELIGIBILITY_LABELS.no_go, detail: ELIGIBILITY_HINTS.no_go });
  }
  if (workspace.analysis_outdated) {
    reasons.push({ level: "attention", label: "Anàlisi endarrerida", detail: WARNING_LABELS.outdated_analysis });
  }
  return reasons;
}

export interface QueueCounts {
  decisions?: number;
  warnings?: number;
  fresh?: number;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** «Dimecres 16 de setembre · 3 decisions pendents, 5 avisos, 12 oportunitats noves». */
export function todayLine(date: Date, counts: QueueCounts): string {
  const weekday = new Intl.DateTimeFormat("ca-ES", { weekday: "long" }).format(date);
  const dayMonth = new Intl.DateTimeFormat("ca-ES", { day: "numeric", month: "long" }).format(date);
  const day = `${weekday.charAt(0).toUpperCase()}${weekday.slice(1)} ${dayMonth}`;
  if (counts.decisions === undefined || counts.warnings === undefined || counts.fresh === undefined) {
    return `${day} · comptant la feina pendent…`;
  }
  return `${day} · ${plural(counts.decisions, "decisió pendent", "decisions pendents")}, ${plural(counts.warnings, "avís", "avisos")}, ${plural(counts.fresh, "oportunitat nova", "oportunitats noves")}`;
}

/** Quin buit toca quan no hi ha cap candidatura ni cap candidata (handoff L5.10). */
export function emptyKind(candidates: number | undefined, cpvPreferences: number | undefined): "profile" | "work" {
  return candidates === 0 && cpvPreferences === 0 ? "profile" : "work";
}
