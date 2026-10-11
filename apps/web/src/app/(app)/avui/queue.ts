import { isPendingDecision } from "@/components/shell/counts";
import { ELIGIBILITY_HINTS, ELIGIBILITY_LABELS, WARNING_LABELS, type Level } from "@/lib/labels";
import type { DashboardSummary } from "@/lib/types";
import type { WorkspaceSummary } from "@/lib/workspace";

/**
 * La cua d'«Avui» (handoff L5.6), composta al client només amb els camps de
 * `WorkspaceSummary`. El nom de cada bloqueig no hi és (cas C-3): aquí es diu
 * per què una candidatura demana atenció, amb el text dels diccionaris.
 */

/** Termini ascendent; sense termini al final. Ordenació estable. */
export function byDeadline(a: WorkspaceSummary, b: WorkspaceSummary): number {
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
    // El mateix text que a totes les pantalles (A12); el detall diu què implica.
    reasons.push({ level: "blocking", label: "Fora de termini", detail: WARNING_LABELS.deadline_passed });
  }
  if (workspace.eligibility === "no_go") {
    reasons.push({ level: "blocking", label: ELIGIBILITY_LABELS.no_go, detail: ELIGIBILITY_HINTS.no_go });
  }
  if (workspace.analysis_outdated) {
    reasons.push({ level: "attention", label: "Anàlisi endarrerida", detail: WARNING_LABELS.outdated_analysis });
  }
  return reasons;
}

/* ------------------------------------------------------------------------- */
/* Candidates del Radar (3B-2 §7.2, addendum A17)                             */
/* ------------------------------------------------------------------------- */

/**
 * Les «candidates» són el que el Radar ha prioritzat amb el perfil, no les
 * licitacions ingerides: sense CPV, territoris ni preferències de contracte
 * el Radar no pot prioritzar res i el 0 s'explica per aquí; amb perfil, el 0
 * vol dir que cap licitació hi coincideix. El total (`summary.total_tenders`)
 * és l'univers visible per a l'empresa: el mateix «Totes» d'Oportunitats.
 */
export const RADAR_NOTE = "candidates prioritzades pel Radar, no licitacions ingerides";

export type CandidatesCause = "profile" | "no_match";

export const CANDIDATES_CAUSE_TEXT: Record<CandidatesCause, string> = {
  profile: "el perfil no té CPV/territoris",
  no_match: "cap coincidència",
};

type RadarProfileCounts = Pick<DashboardSummary["counts"], "cpv_preferences" | "territories" | "contract_preferences">;

const COUNT = new Intl.NumberFormat("ca-ES");

export function formatCount(value: number): string {
  return COUNT.format(value);
}

function plural(count: number, one: string, many: string): string {
  return `${formatCount(count)} ${count === 1 ? one : many}`;
}

/** «0 candidates de 17.336 licitacions». */
export function candidatesLine(candidates: number, totalTenders: number): string {
  return `${plural(candidates, "candidata", "candidates")} de ${plural(totalTenders, "licitació", "licitacions")}`;
}

/** Per què hi ha 0 candidates; `null` mentre el resum del perfil no ha arribat. */
export function candidatesCause(counts: RadarProfileCounts | null | undefined): CandidatesCause | null {
  if (!counts) return null;
  const empty = counts.cpv_preferences === 0 && counts.territories === 0 && counts.contract_preferences === 0;
  return empty ? "profile" : "no_match";
}

/** «0 candidates de 17.336 licitacions: el perfil no té CPV/territoris». Sense causa coneguda, no s'afirma res. */
export function candidatesEmptyLine(totalTenders: number, cause: CandidatesCause | null): string {
  const line = candidatesLine(0, totalTenders);
  return cause ? `${line}: ${CANDIDATES_CAUSE_TEXT[cause]}` : line;
}

export interface QueueCounts {
  decisions?: number;
  warnings?: number;
  candidates?: number;
  totalTenders?: number;
}

/** «Dimecres 16 de setembre · 3 decisions pendents, 5 avisos, 12 candidates de 17.336 licitacions». */
export function todayLine(date: Date, counts: QueueCounts): string {
  const weekday = new Intl.DateTimeFormat("ca-ES", { weekday: "long" }).format(date);
  const dayMonth = new Intl.DateTimeFormat("ca-ES", { day: "numeric", month: "long" }).format(date);
  const day = `${weekday.charAt(0).toUpperCase()}${weekday.slice(1)} ${dayMonth}`;
  if (
    counts.decisions === undefined ||
    counts.warnings === undefined ||
    counts.candidates === undefined ||
    counts.totalTenders === undefined
  ) {
    return `${day} · comptant la feina pendent…`;
  }
  return `${day} · ${plural(counts.decisions, "decisió pendent", "decisions pendents")}, ${plural(counts.warnings, "avís", "avisos")}, ${candidatesLine(counts.candidates, counts.totalTenders)}`;
}
