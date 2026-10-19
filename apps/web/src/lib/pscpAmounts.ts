/**
 * F-06a: el PBL amb IVA segons la PSCP a la fitxa. Només informatiu: no canvia cap import
 * de la licitació, ni el catàleg, els filtres, l'ordenació, el pre-fit o l'oferta.
 *
 * Regles (proposta de visualització, revisada el 19-10-2026):
 * - `accepted`: l'import, amb cèntims, i la línia de font (PSCP i data d'actualització).
 * - `no_value` publicat a 0: un text que diu què és, mai un import.
 * - `no_value` nul o sense observació: com abans («No consta a les dades de la font»).
 * - `conflict`: «Pendent de verificació…» com a valor; l'import de la PSCP només dins de l'avís.
 * - Si la PLACSP ja publica el PBL amb IVA (`base_budget`), el fet continua sent el seu.
 */

import { formatAmount, formatDate } from "@/lib/format";
import type { AmountFailedCheck, AmountObservation, BudgetWithTaxObservations, TenderDetail } from "@/lib/tenders";

export const PSCP_PENDING_TEXT = "Pendent de verificació (discrepància entre fonts)";
export const PSCP_ZERO_TEXT =
  "Publicat a 0,00 € a la PSCP (sense pressupost base de despesa; no s'utilitza com a import econòmic)";
export const PSCP_LINK_TEXT = "Veure la publicació ↗";
export const PSCP_NOT_USED_TEXT = "No es fa servir fins que es verifiqui.";

/** Les comprovacions, en català i en l'ordre de l'API. */
export const FAILED_CHECK_LABELS: Record<AmountFailedCheck, string> = {
  organ: "òrgan",
  pbl_without_tax: "PBL sense IVA",
  vec: "VEC",
  lots: "lots",
};

/** «a», «a i b», «a, b i c». */
export function joinCatalan(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} i ${items[items.length - 1]}`;
}

export function failedChecksText(checks: AmountFailedCheck[]): string {
  return joinCatalan(checks.map((check) => FAILED_CHECK_LABELS[check] ?? check));
}

/** «Segons la PSCP (…), actualitzat el <data>»: la font sempre a la vista. */
export function pscpSourceLine(updatedAt: string | null | undefined): string {
  const base = "Segons la PSCP (dades obertes de la Generalitat de Catalunya)";
  return updatedAt ? `${base}, actualitzat el ${formatDate(updatedAt)}` : base;
}

/** L'avís d'un `conflict`: l'import de la PSCP només surt aquí, mai com a valor del fet. */
export function pscpConflictNotice(observation: AmountObservation): string {
  const checks = failedChecksText(observation.failed_checks);
  if (observation.amount === null || observation.amount === "") {
    const mismatch = checks ? `, i les seves dades no coincideixen amb les de la PLACSP (${checks})` : "";
    return `La PSCP no hi publica cap import vàlid com a pressupost base amb IVA${mismatch}. ${PSCP_NOT_USED_TEXT}`;
  }
  const amount = formatAmount(observation.amount, observation.currency);
  const detail = checks ? ` (${checks})` : "";
  return `La PSCP publica ${amount} com a pressupost base amb IVA, però no coincideix amb les dades de la PLACSP${detail}. ${PSCP_NOT_USED_TEXT}`;
}

/** Text de la línia de lot, o `null` si aquest lot no n'ha de mostrar cap. */
export function pscpLotLine(observation: AmountObservation | null | undefined): string | null {
  if (!observation) return null;
  if (observation.status === "accepted" && observation.amount) {
    return `PBL amb IVA: ${formatAmount(observation.amount, observation.currency)} · PSCP`;
  }
  if (observation.status === "conflict") return "PBL amb IVA: pendent de verificació (discrepància entre fonts)";
  if (observation.status === "no_value" && observation.published_zero) return "PBL amb IVA: publicat a 0,00 € a la PSCP";
  return null;
}

/** Si l'observació es mostra a la pàgina (un `no_value` nul es queda com «No consta…»). */
export function isShownObservation(observation: AmountObservation | null | undefined): observation is AmountObservation {
  if (!observation) return false;
  if (observation.status === "accepted") return Boolean(observation.amount);
  if (observation.status === "conflict") return true;
  return observation.status === "no_value" && observation.published_zero;
}

export interface BudgetWithTaxView {
  /** L'observació d'expedient que substitueix el fet, o `null` (el fet es queda com abans). */
  expedient: AmountObservation | null;
  /** Observacions de lot que es mostren, per `TenderLot.id`. */
  lots: Record<string, AmountObservation>;
  /** Text d'atribució, només si es mostra alguna observació de la PSCP. */
  attribution: string | null;
}

/** L'atribució que exigeix la llicència (sense logotips). */
export function pscpAttribution(updatedAt: string | null | undefined): string {
  const when = updatedAt ? formatDate(updatedAt) : "desconeguda";
  return (
    "Font: Generalitat de Catalunya. Departament d'Economia i Finances — Contractació pública a Catalunya: " +
    "publicacions a la Plataforma de serveis de contractació pública (dades obertes). " +
    `Llicència oberta d'ús d'informació – Catalunya. Darrera actualització: ${when}.`
  );
}

function latest(dates: (string | null)[]): string | null {
  let best: string | null = null;
  let bestTime = -Infinity;
  for (const value of dates) {
    if (!value) continue;
    const time = new Date(value).getTime();
    if (Number.isFinite(time) && time > bestTime) {
      best = value;
      bestTime = time;
    }
  }
  return best;
}

/** Què es mostra a la fitxa, a partir de la resposta de l'API. */
export function budgetWithTaxView(
  detail: Pick<TenderDetail, "base_budget" | "lots"> & { budget_with_tax_observations?: BudgetWithTaxObservations | null },
): BudgetWithTaxView {
  const observations = detail.budget_with_tax_observations;
  const placspBudget = detail.base_budget !== null && detail.base_budget !== "";
  const expedient = !placspBudget && isShownObservation(observations?.expedient) ? observations!.expedient : null;
  const lots: Record<string, AmountObservation> = {};
  for (const lot of detail.lots) {
    const observation = observations?.lots?.[lot.id];
    if (isShownObservation(observation)) lots[lot.id] = observation;
  }
  const shown = [...(expedient ? [expedient] : []), ...Object.values(lots)];
  return {
    expedient,
    lots,
    attribution: shown.length ? pscpAttribution(latest(shown.map((o) => o.source_updated_at))) : null,
  };
}
