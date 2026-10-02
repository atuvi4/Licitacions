/**
 * Ajudes de formulari de «Qualificació i mitjans»: imports en format europeu,
 * dates opcionals i els errors de l'API en paraules. Funcions pures: es proven
 * soles.
 */

/** Un import escrit a l'europea («1.234,56», «1234,5», «1234.5») com el vol l'API («1234.56»).
 * `null` si està buit; `undefined` si no és un import vàlid (negatiu, més de dos decimals…). */
export function parseAmount(text: string): string | null | undefined {
  const cleaned = text.trim().replace(/[\s €]/g, "");
  if (cleaned === "") return null;
  let normal: string;
  if (cleaned.includes(",")) {
    // La coma és el decimal; els punts, separadors de milers.
    if (!/^\d{1,3}(\.\d{3})*(,\d{1,2})?$|^\d+(,\d{1,2})?$/.test(cleaned)) return undefined;
    normal = cleaned.replace(/\./g, "").replace(",", ".");
  } else if (/^\d{1,3}(\.\d{3})+$/.test(cleaned)) {
    // «1.234» o «12.500.000»: milers, com s'escriu aquí.
    normal = cleaned.replace(/\./g, "");
  } else if (/^\d+(\.\d{1,2})?$/.test(cleaned)) {
    normal = cleaned;
  } else {
    return undefined;
  }
  const [whole, decimals] = normal.split(".");
  const integer = whole.replace(/^0+(?=\d)/, "");
  return decimals ? `${integer}.${decimals}` : integer;
}

/** Un import de l'API («1234.50») com s'escriu al camp («1234,50»). */
export function amountText(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const text = String(value);
  const [whole, decimals] = text.split(".");
  const trimmed = decimals?.replace(/0+$/, "");
  return trimmed ? `${whole},${trimmed}` : whole;
}

/** Un enter no negatiu (quantitats: IPv4, unitats). `null` si és buit, `undefined` si no val. */
export function parseCount(text: string): number | null | undefined {
  const cleaned = text.trim().replace(/[\s .]/g, "");
  if (cleaned === "") return null;
  if (!/^\d+$/.test(cleaned)) return undefined;
  const value = Number(cleaned);
  return Number.isSafeInteger(value) ? value : undefined;
}

/** Un camp de data opcional: buit és `null` (mai «avui»). */
export function dayOrNull(text: string): string | null {
  return text.trim() === "" ? null : text.trim();
}

export function textOrNull(text: string): string | null {
  return text.trim() === "" ? null : text.trim();
}

/* ----- Imports executats per exercici --------------------------------------- */

export interface AnnualRow {
  /** Clau estable de la fila (no és l'any: l'any es pot editar). */
  key: string;
  year: string;
  amount: string;
}

export interface AnnualCheck {
  /** Error de cada fila, per clau. */
  rows: Record<string, { year?: string; amount?: string }>;
  /** Error del conjunt (la suma). */
  total?: string;
  /** Les files bones, com les vol l'API. Només si no hi ha cap error. */
  value?: Array<{ year: number; amount: string }>;
}

/**
 * Les mateixes regles que l'API (`_check_annual_amounts` i l'esquema): un any
 * per fila i cap de repetit, dins de les dates del projecte, imports no
 * negatius i una suma que no passi de l'import total si se sap.
 */
export function checkAnnualAmounts(
  rows: AnnualRow[],
  project: { startDate: string | null; endDate: string | null; total: string | null },
): AnnualCheck {
  const errors: AnnualCheck["rows"] = {};
  const first = project.startDate ? Number(project.startDate.slice(0, 4)) : null;
  const last = project.endDate ? Number(project.endDate.slice(0, 4)) : null;
  const seen = new Map<number, string>();
  const value: Array<{ year: number; amount: string }> = [];
  let sum = 0;
  for (const row of rows) {
    const rowErrors: { year?: string; amount?: string } = {};
    const year = /^\d{4}$/.test(row.year.trim()) ? Number(row.year.trim()) : null;
    if (year === null) rowErrors.year = "Escriu l'any amb quatre xifres.";
    else if (seen.has(year)) rowErrors.year = `L'any ${year} ja hi és: un any només pot tenir un import executat.`;
    else if ((first !== null && year < first) || (last !== null && year > last))
      rowErrors.year = `L'any ${year} cau fora de les dates del projecte${first !== null || last !== null ? ` (${first ?? "…"}–${last ?? "…"})` : ""}.`;
    const amount = parseAmount(row.amount);
    if (amount === null) rowErrors.amount = "Indica l'import executat aquest any.";
    else if (amount === undefined) rowErrors.amount = "Import no vàlid. Escriu-lo sense signe i amb dos decimals com a màxim (p. ex. 12.500,00).";
    if (year !== null && !seen.has(year)) seen.set(year, row.key);
    if (rowErrors.year || rowErrors.amount) errors[row.key] = rowErrors;
    else {
      value.push({ year: year as number, amount: amount as string });
      sum += Number(amount);
    }
  }
  const check: AnnualCheck = { rows: errors };
  const total = project.total !== null ? Number(project.total) : null;
  if (total !== null && Object.keys(errors).length === 0 && sum - total > 0.004) {
    check.total = "Els imports executats sumen més que l'import total del projecte.";
  }
  if (Object.keys(errors).length === 0 && !check.total) {
    check.value = value.sort((a, b) => a.year - b.year);
  }
  return check;
}

/* ----- Errors de l'API --------------------------------------------------------- */

const PYDANTIC_PREFIX = /^(Value error|Assertion failed),\s*/i;

/**
 * El que es diu a la persona quan l'API refusa: el missatge i, si n'hi ha, les
 * regles del conjunt (les que no són de cap camp). Mai el JSON.
 */
export function errorText(error: string | null, fieldErrors: Record<string, string>, shownFields: string[] = []): string | null {
  if (!error) return null;
  const extra = Object.entries(fieldErrors)
    .filter(([field]) => !shownFields.includes(field))
    .map(([, message]) => message.replace(PYDANTIC_PREFIX, ""))
    .filter((message) => message && !/^\s*[[{]/.test(message));
  return [error, ...new Set(extra)].join(" ");
}

/** L'error d'un camp, sense el prefix tècnic. */
export function fieldError(fieldErrors: Record<string, string>, field: string): string | undefined {
  const message = fieldErrors[field];
  return message ? message.replace(PYDANTIC_PREFIX, "") : undefined;
}
