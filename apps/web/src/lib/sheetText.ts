/**
 * Lectura del text que desen els parsers de full de càlcul (`xlsx-v1`, `xlsx-v2`) per
 * mostrar-lo com a taula al visor (P2-07). Només és presentació: el text desat, les
 * cites i les empremtes no canvien, i el text original sempre continua disponible.
 *
 * Format (`apps/api/app/services/office_text.py`):
 *   `# Full: Inputs`
 *   `Cel·les combinades: A1:D1 (visible A1)`
 *   `Fila 4 | A4: PG0 | B4: Preu màxim | C4: =C8*0.507 → 38326.080000000002`
 * Una línia que no s'entén es conserva tal com és (`notes`): mai es perd res.
 */

export interface SheetCell {
  ref: string;
  /** El rang combinat d'una cel·la àncora (xlsx-v2: `A1 [A1:D1]`). */
  range: string | null;
  /** El valor tal com és al text desat, sense cap canvi. */
  raw: string;
  /** L'expressió, si la cel·la és una fórmula. */
  formula: string | null;
  /** El valor desat (el de la fórmula, o el de la cel·la). Nul si la fórmula no en té. */
  value: string | null;
}

export interface SheetRow {
  row: string;
  cells: SheetCell[];
}

export interface SheetPage {
  /** El nom del full, si el text el diu (`# Full: …`). */
  name: string | null;
  /** Línies que no són files (combinades, taules, avisos): es mostren tal com són. */
  notes: string[];
  rows: SheetRow[];
}

const REF = /^([A-Z]{1,3}\d{1,7})(?: \[([A-Z]{1,3}\d{1,7}:[A-Z]{1,3}\d{1,7})\])?: ?(.*)$/;
const ROW = /^Fila (\S+) \| (.*)$/;
const SHEET = /^# Full: (.*)$/;
const FORMULA = /^=(.*?)(?: → (.*)| \(sense valor desat\))$/;

/** Si el parser d'aquesta extracció és d'un full de càlcul (i, per tant, el text té aquest format). */
export function isSpreadsheetParser(parserVersion: string | null | undefined): boolean {
  return typeof parserVersion === "string" && parserVersion.startsWith("xlsx-");
}

function cell(ref: string, range: string | null, raw: string): SheetCell {
  const formula = raw.startsWith("=") ? FORMULA.exec(raw) : null;
  if (formula) return { ref, range, raw, formula: formula[1], value: formula[2] ?? null };
  return { ref, range, raw, formula: null, value: raw };
}

export function parseSheetPage(text: string): SheetPage {
  const page: SheetPage = { name: null, notes: [], rows: [] };
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const sheet = SHEET.exec(line);
    if (sheet && page.name === null) {
      page.name = sheet[1].trim();
      continue;
    }
    const row = ROW.exec(line);
    if (!row) {
      page.notes.push(line);
      continue;
    }
    const cells: SheetCell[] = [];
    // Les parts se separen amb « | »; si una part no comença per una referència, és
    // la continuació del valor anterior (un text que contenia « | »).
    for (const part of row[2].split(" | ")) {
      const match = REF.exec(part);
      if (match) cells.push(cell(match[1], match[2] ?? null, match[3]));
      else if (cells.length > 0) {
        const last = cells[cells.length - 1];
        cells[cells.length - 1] = cell(last.ref, last.range, `${last.raw} | ${part}`);
      } else page.notes.push(line);
    }
    if (cells.length > 0) page.rows.push({ row: row[1], cells });
  }
  return page;
}

const DECIMAL = /^-?\d+\.(\d+)$/;

/**
 * El valor net d'un decimal amb un error evident de representació en coma flotant
 * (p. ex. `38326.080000000002` → `38326.08`), o `null` si no n'hi ha cap.
 *
 * Conservador a propòsit: només si el literal té 9 o més decimals i, llegit amb 15
 * xifres significatives (la precisió segura d'un doble), queda més curt. Mai toca
 * enters, codis, identificadors, dates ni decimals que són precisos de debò.
 */
export function floatArtifactClean(raw: string): string | null {
  const match = DECIMAL.exec(raw.trim());
  if (!match || match[1].length < 9) return null;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return null;
  const clean = String(Number(parsed.toPrecision(15)));
  if (clean.includes("e") || clean.length >= raw.trim().length) return null;
  return clean;
}

const CA = new Intl.NumberFormat("ca-ES", { maximumFractionDigits: 15, useGrouping: true });

/** El valor net en format català (separador de milers i coma decimal), sense arrodonir res més. */
export function formatCleanNumber(clean: string): string {
  return CA.format(Number(clean));
}
