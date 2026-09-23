import type { Level } from "@/components/ui";
import type { SourceRefCitation } from "@/lib/requirements";

/**
 * Dubtes i discrepàncies.
 *
 * Un **dubte** és coneixement que falta: una pregunta sense resposta, dues fonts
 * que es contradiuen, una dada que no té ningú. No és feina per fer —això ja són
 * les tasques—, i la distinció no és cosmètica: una tasca es tanca fent-la i un
 * dubte es tanca sabent alguna cosa.
 *
 * **Els obre una persona, sempre.** Res del sistema n'obre cap: els avisos
 * automàtics ja viuen a la preparació.
 */

export type IssueType =
  | "internal_question"
  | "authority_question"
  | "supplier_question"
  | "contradiction"
  | "missing_information";

export type IssueStatus = "open" | "waiting" | "resolved" | "dismissed";

export interface Issue {
  id: string;
  workspace_id: string;
  issue_type: IssueType;
  status: IssueStatus;
  title: string;
  description: string | null;

  /** Només identificadors: el context el resol la pantalla amb el que ja té. */
  requirement_id: string | null;
  award_criterion_id: string | null;
  technical_solution_id: string | null;
  solution_option_id: string | null;
  economic_offer_line_id: string | null;
  dimension_id: string | null;

  supplier_name: string | null;
  owner_id: string | null;
  due_at: string | null;

  resolution: string | null;
  resolution_source: string | null;
  /** Només els omple RESOLDRE. Descartar els deixa buits. */
  resolved_by: string | null;
  resolved_at: string | null;

  /** Decisió d'oferta. Només els rols de decisió la canvien. */
  blocks_final_review: boolean;

  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;

  source_refs: SourceRefCitation[];
}

/**
 * Els tipus classifiquen **a qui cal preguntar**, no la gravetat.
 *
 * La urgència ja la diuen la data límit i la marca de bloqueig; una taxonomia
 * de risc convidaria a inventar-ne la severitat.
 */
export const ISSUE_TYPE_LABELS: Record<IssueType, string> = {
  internal_question: "Pregunta interna",
  authority_question: "Pregunta a l'òrgan",
  supplier_question: "Pregunta a proveïdor",
  contradiction: "Contradicció",
  missing_information: "Falta informació",
};

export const ISSUE_TYPES: IssueType[] = [
  "internal_question",
  "authority_question",
  "supplier_question",
  "contradiction",
  "missing_information",
];

export const ISSUE_STATUS_LABELS: Record<IssueStatus, string> = {
  open: "Obert",
  waiting: "En espera",
  resolved: "Resolt",
  dismissed: "Descartat",
};

/**
 * Cap dubte obert es pinta com un error.
 *
 * Obrir preguntes és exactament el que ha de passar mentre es prepara una
 * oferta. El que sí que destaca és la marca de bloqueig, i perquè algú l'ha
 * posada.
 */
export const ISSUE_STATUS_LEVEL: Record<IssueStatus, Level> = {
  open: "running",
  waiting: "neutral",
  resolved: "ready",
  dismissed: "neutral",
};

/** Els estats en què la pregunta encara no té resposta. */
export function isOpen(issue: Issue): boolean {
  return issue.status === "open" || issue.status === "waiting";
}

/** Qui pot obrir i treballar dubtes: tothom qui prepara la candidatura. */
export function canEditIssues(role: string | null | undefined): boolean {
  return role === "owner" || role === "admin" || role === "bid_manager" || role === "technical";
}

/**
 * Qui decideix si un dubte impedeix la revisió final.
 *
 * No tots els editors: marcar que una cosa impedeix presentar-se és una decisió
 * d'oferta, i la pren qui en respon.
 */
export function canBlockFinalReview(role: string | null | undefined): boolean {
  return role === "owner" || role === "admin" || role === "bid_manager";
}

/** Quants n'hi ha d'oberts, per al comptador de la capçalera. */
export function openCount(issues: Issue[]): number {
  return issues.filter(isOpen).length;
}
