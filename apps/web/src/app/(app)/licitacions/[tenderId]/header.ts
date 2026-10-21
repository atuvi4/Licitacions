import type { TabItem } from "@/components/ui/Tabs";
import type { Compliance } from "@/lib/compliance";
import { MANUAL_ZERO_AMOUNT_TEXT, PLACSP_ZERO_AMOUNT_TEXT, isPublishedZero, usefulAmount } from "@/lib/amounts";
import { formatAmount } from "@/lib/format";
import { AMOUNT_NOT_PUBLISHED, tenderAmounts, type AmountBasis } from "@/lib/tenders";
import {
  ANALYSIS_STATUS_TEXT,
  DECISION_LABELS,
  DECISION_LEVEL,
  WORKSPACE_STATUS_LEVEL,
  deadlineCountdownView,
  formatDateTime,
  type Level,
} from "@/lib/labels";
import type { RequirementAnalysis } from "@/lib/requirements";
import { workspaceStatusLabel, type WorkspaceDetail } from "@/lib/workspace";

/**
 * Capçalera de decisió i pestanyes de la fitxa (handoff L9): «termini ·
 * encaix · requisits · què fer ara», només amb dades que ja arriben. Cap
 * recompte inventat: si no hi ha anàlisi ni compliment, es diu.
 */

export type TabKey = "resum" | "plecs" | "requisits" | "analisi" | "historial";

export const TABS: Array<{ key: TabKey; label: string }> = [
  { key: "resum", label: "Resum" },
  { key: "plecs", label: "Plecs" },
  { key: "requisits", label: "Requisits extrets" },
  { key: "analisi", label: "Anàlisi" },
  { key: "historial", label: "Historial" },
];

export function tabFromParam(value: string | null): TabKey {
  return TABS.some((tab) => tab.key === value) ? (value as TabKey) : "resum";
}

export function tabItems({
  documents,
  analysis,
  compliance,
}: {
  documents: number;
  analysis: RequirementAnalysis | null;
  compliance: Compliance | null;
}): TabItem[] {
  const extracted = analysis?.run?.requirements_extracted ?? null;
  const blockers = compliance?.run?.confirmed_blockers_count ?? 0;
  return TABS.map((tab) => {
    if (tab.key === "plecs") return { ...tab, count: documents };
    if (tab.key === "requisits" && extracted !== null) {
      return blockers > 0 ? { ...tab, count: extracted, countLevel: "blocking" as const } : { ...tab, count: extracted };
    }
    return { ...tab };
  });
}

export interface RequirementsLine {
  text: string;
  detail: string | null;
  level: Level;
}

/** «Requisits» de la capçalera: estat de l'anàlisi + què diu el compliment, si s'ha avaluat. */
export function requirementsLine(analysis: RequirementAnalysis | null, compliance: Compliance | null): RequirementsLine {
  const text = ANALYSIS_STATUS_TEXT(analysis, null);
  const run = compliance?.run;
  if (!run) return { text, detail: null, level: "neutral" };
  if (run.overall_result === "undetermined") return { text, detail: "encara no avaluats", level: "neutral" };
  // Confirmats i possibles es diuen per separat: la secció de compliment els
  // agrupa igual, i un «0 bloquegen» al costat de «Possibles bloquejos · 3» es
  // contradiria.
  const confirmed = run.confirmed_blockers_count;
  const potential = run.potential_blockers_count;
  const parts = [`${confirmed} ${confirmed === 1 ? "bloqueig confirmat" : "bloquejos confirmats"}`];
  if (potential > 0) parts.push(`${potential} ${potential === 1 ? "possible bloqueig" : "possibles bloquejos"}`);
  parts.push(`${run.needs_review_count} per revisar`);
  return {
    text,
    detail: parts.join(" · "),
    level: confirmed > 0 ? "blocking" : potential > 0 ? "attention" : "neutral",
  };
}

export interface DeadlineMeta {
  level: Level;
  text: string;
  date: string | null;
}

/** Un termini nul és desconegut, mai un bloqueig. El recompte és el de `lib/deadline` (A11). */
export function deadlineMeta(deadlineAt: string | null, now: Date = new Date()): DeadlineMeta {
  const countdown = deadlineCountdownView(deadlineAt, now);
  if (!deadlineAt || countdown.days === null) return { level: "neutral", text: "Sense termini", date: null };
  return { level: countdown.level, text: countdown.text, date: formatDateTime(deadlineAt) };
}

/** Nota de la capçalera per base (P2-06): curta, però diu exactament què és la xifra. */
const AMOUNT_NOTE: Record<AmountBasis, string> = {
  vec: "valor estimat del contracte (VEC)",
  pbl_net: "pressupost base de licitació sense IVA",
  pbl_gross: "pressupost base de licitació amb IVA",
};

export function amountMeta(tender: {
  estimated_value: string | null;
  base_budget: string | null;
  base_budget_without_tax?: string | null;
  currency: string;
}): { text: string; note: string | null; budget: { text: string; note: string } | null } {
  // La mateixa ordre que el filtre i l'ordenació; abans s'ignorava el PBL sense IVA i
  // les licitacions que només el tenien mostraven «—».
  const amounts = tenderAmounts(tender);
  if (!amounts.primary) return { text: AMOUNT_NOT_PUBLISHED, note: null, budget: null };
  // Amb cèntims: el PBL és l'import màxim de l'oferta, i arrodonir-lo en canviaria el sentit.
  return {
    text: formatAmount(amounts.primary.value, tender.currency),
    note: AMOUNT_NOTE[amounts.primary.basis],
    budget: amounts.budget
      ? { text: formatAmount(amounts.budget.value, tender.currency), note: AMOUNT_NOTE[amounts.budget.basis] }
      : null,
  };
}

/**
 * Un fet d'import del Resum (P2-06), d'una base concreta: l'import exacte si és útil (> 0); si la font
 * l'ha publicat a 0,00 €, el text aprovat (mai «0,00 €» com si fos un import); si no hi és, «no consta».
 * Un negatiu (la font no n'hauria de publicar) es mostra tal com ve, però tampoc no s'utilitza.
 */
export function amountFact(
  value: string | null,
  currency: string,
  { manual = false }: { manual?: boolean } = {},
): { kind: "amount" | "zero" | "missing" | "not_used"; text: string } {
  if (usefulAmount(value) !== null) return { kind: "amount", text: formatAmount(value, currency) };
  if (isPublishedZero(value)) return { kind: "zero", text: manual ? MANUAL_ZERO_AMOUNT_TEXT : PLACSP_ZERO_AMOUNT_TEXT };
  if (value === null || value.trim() === "") return { kind: "missing", text: AMOUNT_NOT_PUBLISHED };
  return { kind: "not_used", text: `${formatAmount(value, currency)} (no s'utilitza com a import econòmic)` };
}

export interface Pill {
  level: Level;
  label: string;
}

/** La candidatura, si existeix; la decisió humana, només si és real. */
export function candidatureMeta(workspace: WorkspaceDetail | null): { status: Pill; decision: Pill | null } | null {
  if (!workspace) return null;
  return {
    // L'etiqueta de l'estat `analysis` depèn de si hi ha anàlisi de debò (A13).
    status: { level: WORKSPACE_STATUS_LEVEL[workspace.status], label: workspaceStatusLabel(workspace) },
    decision: workspace.human_decision
      ? { level: DECISION_LEVEL[workspace.human_decision], label: DECISION_LABELS[workspace.human_decision] }
      : null,
  };
}
