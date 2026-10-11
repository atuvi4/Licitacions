import { isPendingDecision } from "@/components/shell/counts";
import { ELIGIBILITY_HINTS, ELIGIBILITY_LABELS, WARNING_LABELS, type Level } from "@/lib/labels";
import { candidatesCause, type CandidatesCause } from "@/lib/matching";
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
 * licitacions ingerides. El 0 té quatre lectures diferents i la causa surt
 * del resum del Radar (`evaluated_count` i els recomptes per estat), mai només
 * del perfil (revisió 3B-2, F1): sense cap licitació avaluada, el Radar
 * encara no s'ha calculat (unknown ≠ no). El total (`summary.total_tenders`)
 * és l'univers visible per a l'empresa: el mateix «Totes» d'Oportunitats.
 */
export const RADAR_NOTE = "candidates prioritzades pel Radar, no licitacions ingerides";

// La causa del 0 és UNA sola funció compartida amb Oportunitats › Radar (`lib/matching`).
export { candidatesCause, type CandidatesCause };

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

function evaluatedPhrase(evaluated: number): string {
  return evaluated === 1 ? "l'única avaluada" : `les ${formatCount(evaluated)} avaluades`;
}

/** El text de cada causa, amb les avaluades quan la causa en parla. */
export function candidatesCauseText(cause: CandidatesCause, evaluated: number): string {
  switch (cause) {
    case "not_evaluated":
      return "el Radar encara no s'ha calculat";
    case "profile":
      return "el perfil no té CPV/territoris";
    case "insufficient_data":
      return `dades insuficients a ${evaluatedPhrase(evaluated)}`;
    case "no_match":
      return `cap coincidència entre ${evaluatedPhrase(evaluated)}`;
  }
}

/** «0 candidates de 46 licitacions: el Radar encara no s'ha calculat». Sense causa coneguda, no s'afirma res. */
export function candidatesEmptyLine(totalTenders: number, evaluated: number, cause: CandidatesCause | null): string {
  const line = candidatesLine(0, totalTenders);
  return cause ? `${line}: ${candidatesCauseText(cause, evaluated)}` : line;
}

export interface QueueCounts {
  decisions?: number;
  warnings?: number;
  candidates?: number;
  totalTenders?: number;
  /** Licitacions amb pre-fit calculat; amb 0 la línia no compta candidates. */
  evaluated?: number;
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
  const radar =
    counts.evaluated === 0
      ? `Radar encara no calculat (${plural(counts.totalTenders, "licitació", "licitacions")})`
      : candidatesLine(counts.candidates, counts.totalTenders);
  return `${day} · ${plural(counts.decisions, "decisió pendent", "decisions pendents")}, ${plural(counts.warnings, "avís", "avisos")}, ${radar}`;
}
