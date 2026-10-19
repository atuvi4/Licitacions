/**
 * F-06a: el PBL amb IVA segons la PSCP a la fitxa. Només informatiu: no canvia cap import
 * de la licitació, ni el catàleg, els filtres, l'ordenació, el pre-fit o l'oferta.
 *
 * Regles (proposta de visualització, revisada el 19-10-2026):
 * - `accepted`: l'import, amb cèntims, i la línia de font (PSCP i data d'actualització).
 * - `no_value` publicat a 0: un text que diu què és, mai un import.
 * - `no_value` nul o sense observació: com abans («No consta a les dades de la font»).
 * - `conflict`: «Pendent de verificació…» com a valor; l'import de la PSCP només dins de l'avís.
 * - Si la PLACSP ja publica el PBL amb IVA (`base_budget`), el fet (o el lot) continua sent el seu. Un
 *   `base_budget` a 0,00 € no compta com a publicat (regla única dels imports, `lib/amounts`).
 * - Un lot amb observació mostrada diu «Sense import a la PLACSP», no «No consta…», a la part de la PLACSP.
 * - L'atribució és el text de l'API, amb la data del dataset.
 */

import { usefulAmount } from "@/lib/amounts";
import { formatAmount, formatDate } from "@/lib/format";
import type { AmountFailedCheck, AmountObservation, BudgetWithTaxObservations, TenderDetail, TenderLot } from "@/lib/tenders";

export const PSCP_PENDING_TEXT = "Pendent de verificació (discrepància entre fonts)";
export const PSCP_ZERO_TEXT =
  "Publicat a 0,00 € a la PSCP (sense pressupost base de despesa; no s'utilitza com a import econòmic)";
export const PSCP_LINK_TEXT = "Veure la publicació ↗";
export const PSCP_NOT_USED_TEXT = "No es fa servir fins que es verifiqui.";
/** La part de la PLACSP d'un lot sense import quan la PSCP sí que en diu alguna cosa. */
export const PLACSP_NO_AMOUNT_TEXT = "Sense import a la PLACSP";

/** Les comprovacions contra la PLACSP, en català i en l'ordre de l'API. */
export const FAILED_CHECK_LABELS: Record<Exclude<AmountFailedCheck, "lot_sum">, string> = {
  organ: "òrgan",
  pbl_without_tax: "PBL sense IVA",
  vec: "VEC",
  lots: "lots",
};

/** `lot_sum`: la PSCP no quadra amb ella mateixa; mai no es diu que és culpa de la PLACSP. */
export const PSCP_LOT_SUM_TEXT =
  "La suma dels imports dels lots publicats a la PSCP no coincideix amb l'import total de l'expedient publicat per la mateixa PSCP.";
/** `evaluation_error`: no s'ha pogut avaluar; no vol dir que la PSCP no publiqui cap import. */
export const PSCP_UNVERIFIED_TEXT = "No s'ha pogut verificar l'import publicat a la PSCP.";

/** «a», «a i b», «a, b i c». */
export function joinCatalan(items: string[]): string {
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} i ${items[items.length - 1]}`;
}

/** Només les comprovacions contra la PLACSP (`lot_sum` va a part). */
export function failedChecksText(checks: AmountFailedCheck[]): string {
  return joinCatalan(checks.filter((check) => check !== "lot_sum").map((check) => FAILED_CHECK_LABELS[check as Exclude<AmountFailedCheck, "lot_sum">] ?? check));
}

/** «Segons la PSCP (…), actualitzat el <data>»: la font sempre a la vista. */
export function pscpSourceLine(updatedAt: string | null | undefined): string {
  const base = "Segons la PSCP (dades obertes de la Generalitat de Catalunya)";
  return updatedAt ? `${base}, actualitzat el ${formatDate(updatedAt)}` : base;
}

/**
 * L'avís d'un `conflict`: l'import de la PSCP només surt aquí, mai com a valor del fet.
 * - Lectura que no s'ha pogut avaluar: «No s'ha pogut verificar…» (mai «no hi publica cap import»).
 * - Només diu «no coincideix amb les dades de la PLACSP» si ha fallat alguna comprovació contra
 *   la PLACSP (òrgan, PBL sense IVA, VEC o lots).
 * - `lot_sum` (la suma dels lots de la PSCP contra el seu total) es diu a part, sense culpar la PLACSP.
 * - Si només n'han fallat d'altres internes de la font (ordre de l'IVA, coherència entre files,
 *   files sense explicar…), diu que no ha superat les comprovacions de coherència de la font.
 */
export function pscpConflictNotice(observation: AmountObservation): string {
  if (observation.evaluation_error) return `${PSCP_UNVERIFIED_TEXT} ${PSCP_NOT_USED_TEXT}`;
  const checks = failedChecksText(observation.failed_checks);
  const lotSum = observation.failed_checks.includes("lot_sum");
  if (observation.amount === null || observation.amount === "") {
    const mismatch = checks ? `, i les seves dades no coincideixen amb les de la PLACSP (${checks})` : "";
    return `La PSCP no hi publica cap import vàlid com a pressupost base amb IVA${mismatch}. ${PSCP_NOT_USED_TEXT}`;
  }
  const amount = formatAmount(observation.amount, observation.currency);
  if (checks && lotSum) {
    return `La PSCP publica ${amount} com a pressupost base amb IVA, però no coincideix amb les dades de la PLACSP (${checks}). A més, l${PSCP_LOT_SUM_TEXT.slice(1)} ${PSCP_NOT_USED_TEXT}`;
  }
  if (lotSum) return `${PSCP_LOT_SUM_TEXT} ${PSCP_NOT_USED_TEXT}`;
  const why = checks
    ? `no coincideix amb les dades de la PLACSP (${checks})`
    : "no ha superat les comprovacions de coherència de la font";
  return `La PSCP publica ${amount} com a pressupost base amb IVA, però ${why}. ${PSCP_NOT_USED_TEXT}`;
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

/**
 * L'atribució que exigeix la llicència (sense logotips): el text oficial que dona l'API (una
 * sola font de veritat) i, a continuació, la data de la darrera actualització del dataset.
 */
export function pscpAttribution(text: string, updatedAt: string | null | undefined): string {
  const when = updatedAt ? formatDate(updatedAt) : "desconeguda";
  return `${text} Darrera actualització: ${when}.`;
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

/**
 * Si la PLACSP publica un PBL amb IVA ÚTIL (> 0, regla única de `lib/amounts`). Un 0,00 € de la PLACSP
 * no és un import: no amaga l'observació de la PSCP (ni al fet ni a la cel·la del lot).
 */
const placspPublishes = (value: string | null | undefined) => usefulAmount(value) !== null;

/**
 * Què es mostra a la fitxa, a partir de la resposta de l'API. Si la PLACSP ja publica el PBL
 * amb IVA (`base_budget`), de l'expedient o d'un lot, aquest continua tenint prioritat i la
 * PSCP no s'hi mostra (ni com a segon «PBL amb IVA»).
 */
export function budgetWithTaxView(
  detail: Pick<TenderDetail, "base_budget"> & {
    lots: Pick<TenderLot, "id" | "base_budget">[];
    budget_with_tax_observations?: BudgetWithTaxObservations | null;
  },
): BudgetWithTaxView {
  const observations = detail.budget_with_tax_observations;
  const expedient = !placspPublishes(detail.base_budget) && isShownObservation(observations?.expedient) ? observations!.expedient : null;
  const lots: Record<string, AmountObservation> = {};
  for (const lot of detail.lots) {
    const observation = observations?.lots?.[lot.id];
    if (!placspPublishes(lot.base_budget) && isShownObservation(observation)) lots[lot.id] = observation;
  }
  const shown = [...(expedient ? [expedient] : []), ...Object.values(lots)];
  const date = latest(shown.map((o) => o.source_updated_at)) ?? observations?.source_updated_at ?? null;
  return {
    expedient,
    lots,
    attribution: shown.length && observations?.attribution ? pscpAttribution(observations.attribution, date) : null,
  };
}
