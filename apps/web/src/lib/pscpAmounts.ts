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
 * - (Fins a F-06e, un lot amb observació mostrada deia «Sense import a la PLACSP» a la seva cel·la; ara
 *   la taula «Lots» ho diu una sola vegada: vegeu més avall.)
 * - L'atribució és el text de l'API, amb la data del dataset.
 *
 * F-06e (taula «Lots»): per a cada lot, VEC, PBL sense IVA i PBL amb IVA, cadascun a la seva
 * columna: el de la PLACSP si n'hi ha (> 0); si no, el de la PSCP `accepted` amb « · PSCP» i
 * l'enllaç a la publicació; un `conflict`, «Pendent de verificació…» amb el motiu; un `no_value` a
 * 0, «Publicat a 0,00 € a la PSCP»; un `no_value` nul, res. Una sola indicació per a la taula que
 * aquests imports no són de la PLACSP (mai «Sense import a la PLACSP» a cada cel·la) i l'atribució
 * de sempre, una sola vegada.
 */

import { usefulAmount } from "@/lib/amounts";
import { formatAmount, formatDate } from "@/lib/format";
import type { AmountBasis, AmountFailedCheck, AmountObservation, BudgetWithTaxObservations, TenderDetail, TenderLot } from "@/lib/tenders";

export const PSCP_PENDING_TEXT = "Pendent de verificació (discrepància entre fonts)";
export const PSCP_ZERO_TEXT =
  "Publicat a 0,00 € a la PSCP (sense pressupost base de despesa; no s'utilitza com a import econòmic)";
export const PSCP_LINK_TEXT = "Veure la publicació ↗";
export const PSCP_NOT_USED_TEXT = "No es fa servir fins que es verifiqui.";

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

/** Si l'observació es mostra a la pàgina (un `no_value` nul es queda com «No consta…»). */
export function isShownObservation(observation: AmountObservation | null | undefined): observation is AmountObservation {
  if (!observation) return false;
  if (observation.status === "accepted") return Boolean(observation.amount);
  if (observation.status === "conflict") return true;
  return observation.status === "no_value" && observation.published_zero;
}

/** F-06e: les tres xifres d'un lot a la taula «Lots», en l'ordre de les columnes. */
export const LOT_AMOUNT_COLUMNS = [
  { key: "estimated_value", basis: "vec", field: "lots_estimated_value" },
  { key: "base_budget_without_tax", basis: "pbl_net", field: "lots_pbl_without_tax" },
  { key: "base_budget", basis: "pbl_gross", field: "lots" },
] as const satisfies readonly { key: keyof TenderLot; basis: AmountBasis; field: keyof BudgetWithTaxObservations }[];

export type LotAmountKey = (typeof LOT_AMOUNT_COLUMNS)[number]["key"];

/** Una cel·la d'import de lot: de la PLACSP, de la PSCP (accepted, conflict o 0 publicat) o res. */
export type LotAmountCellView =
  | { source: "placsp"; value: string }
  | { source: "pscp"; observation: AmountObservation }
  | { source: "none" };

export type LotAmountCells = Record<LotAmountKey, LotAmountCellView>;

/** La marca de font d'un import de lot de la PSCP (l'enllaç a la publicació, si n'hi ha). */
export const PSCP_LOT_SOURCE_TEXT = "PSCP";
/** El zero publicat d'un import de lot (la versió curta del text aprovat). */
export const PSCP_LOT_ZERO_TEXT = "Publicat a 0,00 € a la PSCP";
/** Una sola indicació per a la taula: cap lot no té cap import a la PLACSP. */
export const PLACSP_NO_LOT_AMOUNTS_TEXT = "Cap d'aquests lots no té imports a la PLACSP: els que es mostren són de la PSCP.";
/** Una sola indicació per a la taula: alguns imports són de la PSCP i d'altres de la PLACSP. */
export const PLACSP_SOME_LOT_AMOUNTS_TEXT = "Els imports marcats «PSCP» són els que la PLACSP no publica per al lot.";
export const PSCP_INCOHERENT_TEXT = "No ha superat les comprovacions de coherència de la font.";

/**
 * El motiu d'un `conflict` d'un import de lot (la columna ja diu quin import és). Sense culpar mai la
 * PLACSP si el que no quadra és la suma dels lots de la PSCP (`lot_sum`, també `lot_net_sum` i
 * `lot_vec_sum` de l'API).
 */
export function pscpLotConflictReason(observation: AmountObservation): string {
  if (observation.evaluation_error) return PSCP_UNVERIFIED_TEXT;
  const checks = failedChecksText(observation.failed_checks);
  const parts: string[] = [];
  if (checks) parts.push(`No coincideix amb les dades de la PLACSP (${checks}).`);
  if (observation.failed_checks.includes("lot_sum")) parts.push(PSCP_LOT_SUM_TEXT);
  return parts.length ? parts.join(" ") : PSCP_INCOHERENT_TEXT;
}

/** La cel·la d'un import de lot: la PLACSP útil mana; si no, l'observació de la PSCP que es mostra. */
export function lotAmountCell(value: string | null | undefined, observation: AmountObservation | null | undefined): LotAmountCellView {
  const placsp = usefulAmount(value);
  if (placsp !== null) return { source: "placsp", value: placsp };
  if (isShownObservation(observation)) return { source: "pscp", observation };
  return { source: "none" };
}

export interface BudgetWithTaxView {
  /** L'observació d'expedient que substitueix el fet, o `null` (el fet es queda com abans). */
  expedient: AmountObservation | null;
  /** Observacions de lot (PBL amb IVA) que es mostren, per `TenderLot.id`. */
  lots: Record<string, AmountObservation>;
  /** F-06e: les tres cel·les d'import de cada lot, per `TenderLot.id`. */
  lotCells: Record<string, LotAmountCells>;
  /** F-06e: la indicació única de la taula si algun import de lot és de la PSCP. */
  lotsNote: string | null;
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
    lots: (Pick<TenderLot, "id" | "base_budget"> & Partial<Pick<TenderLot, "estimated_value" | "base_budget_without_tax">>)[];
    budget_with_tax_observations?: BudgetWithTaxObservations | null;
  },
): BudgetWithTaxView {
  const observations = detail.budget_with_tax_observations;
  const expedient = !placspPublishes(detail.base_budget) && isShownObservation(observations?.expedient) ? observations!.expedient : null;
  const lots: Record<string, AmountObservation> = {};
  const lotCells: Record<string, LotAmountCells> = {};
  const shownInLots: AmountObservation[] = [];
  let rowsWithPscp = 0;
  let rowsWithPlacsp = 0;
  for (const lot of detail.lots) {
    const observation = observations?.lots?.[lot.id];
    if (!placspPublishes(lot.base_budget) && isShownObservation(observation)) lots[lot.id] = observation;
    const cells = {} as LotAmountCells;
    for (const column of LOT_AMOUNT_COLUMNS) {
      const byLot = observations?.[column.field] as Record<string, AmountObservation> | undefined;
      cells[column.key] = lotAmountCell(lot[column.key], byLot?.[lot.id]);
    }
    lotCells[lot.id] = cells;
    const values = Object.values(cells);
    for (const cell of values) if (cell.source === "pscp") shownInLots.push(cell.observation);
    if (values.some((cell) => cell.source === "pscp")) rowsWithPscp += 1;
    if (values.some((cell) => cell.source === "placsp")) rowsWithPlacsp += 1;
  }
  const lotsNote = rowsWithPscp ? (rowsWithPlacsp ? PLACSP_SOME_LOT_AMOUNTS_TEXT : PLACSP_NO_LOT_AMOUNTS_TEXT) : null;
  const shown = [...(expedient ? [expedient] : []), ...Object.values(lots), ...shownInLots];
  const date = latest(shown.map((o) => o.source_updated_at)) ?? observations?.source_updated_at ?? null;
  return {
    expedient,
    lots,
    lotCells,
    lotsNote,
    attribution: shown.length && observations?.attribution ? pscpAttribution(observations.attribution, date) : null,
  };
}
