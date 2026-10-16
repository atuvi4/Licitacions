/**
 * Lectura del text que desen els parsers de full de càlcul (`xlsx-v1`, `xlsx-v2`) per
 * mostrar-lo com a taula al visor (P2-07). Només és presentació: el text desat, les
 * cites i les empremtes no canvien, i el text original sempre continua disponible.
 *
 * Format (`apps/api/app/services/office_text.py`):
 *   `# Full: Inputs`
 *   `Cel·les combinades: A1:D1 (visible A1)`
 *   `Fila 4 | A4: PG0 | B4: Preu màxim | C4: =C8*0.507 → 38326.080000000002`
 * Una línia que no s'entén es conserva tal com és (`notes`): mai es perd res. Un text de
 * cel·la amb salts de línia continua a les línies següents: després de la primera fila el
 * parser només escriu files, i qualsevol altra línia és la continuació de l'anterior.
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
  /** El nom del full, si el text el diu (`# Full: …`), sense la marca d'ocult. */
  name: string | null;
  /** Si el parser ha marcat el full com a ocult (`# Full: X (ocult)`). */
  hidden: boolean;
  /** Línies que no són files (combinades, taules, avisos): es mostren tal com són. */
  notes: string[];
  rows: SheetRow[];
}

const REF = /^([A-Z]{1,3}\d{1,7})(?: \[([A-Z]{1,3}\d{1,7}:[A-Z]{1,3}\d{1,7})\])?: ?([\s\S]*)$/;
const ROW = /^Fila (\S+) \| (.*)$/;
const SHEET = /^# Full: (.*)$/;
const FORMULA = /^=([\s\S]*?)(?: → ([\s\S]*)| \(sense valor desat\))$/;

/** Si el parser d'aquesta extracció és d'un full de càlcul (i, per tant, el text té aquest format). */
export function isSpreadsheetParser(parserVersion: string | null | undefined): boolean {
  return typeof parserVersion === "string" && parserVersion.startsWith("xlsx-");
}

function cell(ref: string, range: string | null, raw: string): SheetCell {
  const formula = raw.startsWith("=") ? FORMULA.exec(raw) : null;
  if (formula) return { ref, range, raw, formula: formula[1], value: formula[2] ?? null };
  return { ref, range, raw, formula: null, value: raw };
}

function rowCells(rest: string, line: string, notes: string[]): SheetCell[] {
  const cells: SheetCell[] = [];
  // Les parts se separen amb « | »; si una part no comença per una referència, és
  // la continuació del valor anterior (un text que contenia « | »).
  for (const part of rest.split(" | ")) {
    const match = REF.exec(part);
    if (match) cells.push(cell(match[1], match[2] ?? null, match[3]));
    else if (cells.length > 0) {
      const last = cells[cells.length - 1];
      cells[cells.length - 1] = cell(last.ref, last.range, `${last.raw} | ${part}`);
    } else notes.push(line);
  }
  return cells;
}

export function parseSheetPage(text: string): SheetPage {
  const page: SheetPage = { name: null, hidden: false, notes: [], rows: [] };
  const rows: { row: string; rest: string; line: string }[] = [];
  for (const line of text.split("\n")) {
    const row = ROW.exec(line);
    if (row) {
      rows.push({ row: row[1], rest: row[2], line });
      continue;
    }
    if (rows.length > 0) {
      // Després de la primera fila, una línia que no és fila és el salt de línia d'un text.
      const last = rows[rows.length - 1];
      last.rest = `${last.rest}\n${line}`;
      last.line = `${last.line}\n${line}`;
      continue;
    }
    if (!line.trim()) continue;
    const sheet = SHEET.exec(line);
    if (sheet && page.name === null) {
      const name = sheet[1].trim();
      page.hidden = name.endsWith(" (ocult)");
      page.name = page.hidden ? name.slice(0, -" (ocult)".length) : name;
      continue;
    }
    page.notes.push(line);
  }
  for (const { row, rest, line } of rows) {
    const cells = rowCells(rest, line, page.notes);
    if (cells.length > 0) page.rows.push({ row, cells });
  }
  return page;
}

const DECIMAL = /^-?\d+\.(\d+)$/;
/** La signatura d'un error de coma flotant: 6 o més zeros o nous seguits i 1-3 xifres al final. */
const ARTIFACT_TAIL = /(?:0{6,}|9{6,})\d{1,3}$/;

/**
 * El valor net d'un decimal amb un error evident de representació en coma flotant
 * (p. ex. `38326.080000000002` → `38326.08`), o `null` si no n'hi ha cap.
 *
 * Només amb la signatura d'un error de coma flotant. El literal ha de tenir 9 o més decimals,
 * i la part decimal no pot acabar en 0, perquè la representació d'un doble mai no hi acaba:
 * un zero final és una dada escrita així. Abans de les 1-3 xifres finals hi ha d'haver una
 * tirada de 6 o més zeros o nous. I, llegit amb 15 xifres significatives, el valor ha de
 * quedar més curt. Mai toca enters, codis, identificadors, dates, zeros finals ni decimals
 * precisos com π o 2/3. El valor net es dona en la mateixa notació que el desat (punt decimal).
 */
export function floatArtifactClean(raw: string): string | null {
  const literal = raw.trim();
  const match = DECIMAL.exec(literal);
  if (!match || match[1].length < 9 || match[1].endsWith("0") || !ARTIFACT_TAIL.test(match[1])) return null;
  const parsed = Number(literal);
  if (!Number.isFinite(parsed)) return null;
  const clean = String(Number(parsed.toPrecision(15)));
  if (clean.includes("e") || clean.length >= literal.length) return null;
  return clean;
}
