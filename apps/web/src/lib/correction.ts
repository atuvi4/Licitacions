/**
 * Editor de correccions d'un requisit canònic: model del formulari, validació,
 * càrrega diferencial i rebase després d'un 409. Tot pur, sense React.
 *
 * Dos principis:
 *
 * * **Els codis surten d'`editable`, mai de les etiquetes.** La resposta
 *   canònica porta `editable` (la instantània acceptada amb codis estables)
 *   precisament perquè «Obligatori» o «Requisit tècnic» no es poden
 *   desfer en codis sense endevinar.
 * * **Només s'envia el que canvia.** Un valor del document que el revisor no
 *   toca no s'envia: si s'enviés, el backend el passaria a `reviewer`.
 */

import type { CanonicalRequirement } from "@/lib/review";

// --------------------------------------------------------------------------- //
// Contracte (espill de l'API)                                                   //
// --------------------------------------------------------------------------- //

export type ScalarValue = string | number | boolean | null;

export interface EditableValue {
  key: string;
  operator: string | null;
  value: ScalarValue;
  unit_label: string | null;
  source: "document" | "reviewer";
}

export interface EditableRequirement {
  title: string;
  description: string | null;
  category: string;
  obligation: string;
  modality: string;
  values: EditableValue[];
}

/** Límits del backend (`app/schemas/review.py`). */
export const LIMITS = { title: 500, description: 5000, note: 2000, unit: 32, text: 500, values: 50 } as const;
export const VALUE_KEY = /^[a-z][a-z0-9_]{0,59}$/;

// --------------------------------------------------------------------------- //
// Opcions (etiquetes idèntiques a les de la revisió al backend)                 //
// --------------------------------------------------------------------------- //

export interface Option {
  value: string;
  label: string;
}

/** `review_view.CATEGORY_LABELS`: la mateixa paraula que ja surt a la llista. */
export const CATEGORY_OPTIONS: Option[] = [
  { value: "technical_requirement", label: "Requisit tècnic" },
  { value: "contract_condition", label: "Condició contractual" },
  { value: "deadline", label: "Termini" },
  { value: "economic_solvency", label: "Solvència econòmica" },
  { value: "technical_solvency", label: "Solvència tècnica" },
  { value: "experience", label: "Experiència" },
  { value: "certification", label: "Certificació" },
  { value: "registration", label: "Inscripció en registres" },
  { value: "business_classification", label: "Classificació empresarial" },
  { value: "insurance", label: "Assegurança" },
  { value: "personnel", label: "Personal" },
  { value: "professional_qualification", label: "Titulació professional" },
  { value: "equipment_or_resources", label: "Mitjans i equips" },
  { value: "administrative_documentation", label: "Documentació administrativa" },
  { value: "other", label: "Altres" },
];

/**
 * Obligació i modalitat van per separat: l'etiqueta combinada de la llista
 * («Condicional», «Obligatori»...) no és reversible, i editar-ne una sola no
 * ha de canviar l'altra.
 */
export const OBLIGATION_OPTIONS: Option[] = [
  { value: "mandatory", label: "Obligatori" },
  { value: "scored", label: "Valorable (dona punts)" },
  { value: "informative", label: "Informatiu" },
  { value: "unknown", label: "Per determinar" },
];

export const MODALITY_OPTIONS: Option[] = [
  { value: "mandatory", label: "Obliga sempre" },
  { value: "conditional", label: "Condicional (només si es dona un supòsit)" },
  { value: "alternative", label: "Alternativa (una de diverses vies)" },
  { value: "optional", label: "Opcional" },
  { value: "informational", label: "Informativa" },
  { value: "unknown", label: "Per determinar" },
];

/** Operadors que ofereix l'editor. `""` = sense operador. */
export const OPERATOR_OPTIONS: Option[] = [
  { value: "", label: "Sense operador" },
  { value: "=", label: "=" },
  { value: "!=", label: "≠" },
  { value: "<", label: "<" },
  { value: "<=", label: "≤" },
  { value: ">", label: ">" },
  { value: ">=", label: "≥" },
  { value: "exists", label: "existeix" },
];

/** Codis de l'extracció que tenen un símbol equivalent: `gte` i `>=` són el mateix. */
const EQUIVALENT: Record<string, string> = { gte: ">=", gt: ">", lte: "<=", lt: "<", eq: "=" };
/** Altres codis que poden arribar de l'extracció i s'han de poder conservar. */
const OTHER_OPERATOR_LABELS: Record<string, string> = {
  one_of: "un de",
  all_of: "tots",
  contains: "conté",
  date_before: "abans de",
  date_after: "després de",
  unknown: "per determinar",
};

export function operatorKey(operator: string | null): string {
  if (!operator) return "";
  return EQUIVALENT[operator] ?? operator;
}

export function sameOperator(a: string | null, b: string | null): boolean {
  return operatorKey(a) === operatorKey(b);
}

/** Les opcions del desplegable per a un valor: les estàndard i, si cal, la que ja té. */
export function operatorOptions(current: string | null): Option[] {
  const key = operatorKey(current);
  if (OPERATOR_OPTIONS.some((option) => option.value === key)) return OPERATOR_OPTIONS;
  return [...OPERATOR_OPTIONS, { value: key, label: OTHER_OPERATOR_LABELS[key] ?? "altre" }];
}

// --------------------------------------------------------------------------- //
// Formulari                                                                     //
// --------------------------------------------------------------------------- //

export type ValueType = "number" | "text" | "boolean" | "none";

export const VALUE_TYPE_OPTIONS: Option[] = [
  { value: "number", label: "Número" },
  { value: "text", label: "Text" },
  { value: "boolean", label: "Sí / No" },
  { value: "none", label: "Sense valor" },
];

export interface DraftValue {
  /** Intern: no s'ensenya mai. Els nous en reben un de lliure. */
  key: string;
  isNew: boolean;
  operator: string | null;
  type: ValueType;
  /** El que hi ha escrit (número o text), tal com l'escriu la persona. */
  text: string;
  bool: boolean;
  unit: string;
}

export interface Draft {
  title: string;
  description: string;
  category: string;
  obligation: string;
  modality: string;
  values: DraftValue[];
  /** Claus de valors existents que el revisor ha tret. */
  removed: string[];
}

function typeOf(value: ScalarValue): ValueType {
  if (value === null) return "none";
  if (typeof value === "boolean") return "boolean";
  if (typeof value === "number") return "number";
  return "text";
}

function textOf(value: ScalarValue): string {
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return value;
  return "";
}

export function draftValue(value: EditableValue): DraftValue {
  return {
    key: value.key,
    isNew: false,
    operator: value.operator,
    type: typeOf(value.value),
    text: textOf(value.value),
    bool: value.value === true,
    unit: value.unit_label ?? "",
  };
}

export function draftFrom(editable: EditableRequirement): Draft {
  return {
    title: editable.title,
    description: editable.description ?? "",
    category: editable.category,
    obligation: editable.obligation,
    modality: editable.modality,
    values: editable.values.map(draftValue),
    removed: [],
  };
}

/** La primera clau `reviewer_value_N` que no fa servir ningú (ni les tretes). */
export function nextKey(taken: Iterable<string>): string {
  const used = new Set(taken);
  for (let n = 1; ; n += 1) {
    const key = `reviewer_value_${n}`;
    if (!used.has(key)) return key;
  }
}

export function newValue(draft: Draft, base: EditableRequirement): DraftValue {
  const key = nextKey([...base.values.map((v) => v.key), ...draft.values.map((v) => v.key), ...draft.removed]);
  return { key, isNew: true, operator: null, type: "number", text: "", bool: true, unit: "" };
}

// --------------------------------------------------------------------------- //
// Validació i conversió                                                         //
// --------------------------------------------------------------------------- //

const NUMBER = /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/;

/** Número finit o `undefined`. Accepta la coma decimal; mai «NaN» ni «Infinity». */
export function parseNumber(text: string): number | undefined {
  const cleaned = text.trim().replace(/\s/g, "").replace(",", ".");
  if (!NUMBER.test(cleaned)) return undefined;
  const value = Number(cleaned);
  return Number.isFinite(value) ? value : undefined;
}

export function scalarOf(row: DraftValue): ScalarValue | undefined {
  if (row.type === "none") return null;
  if (row.type === "boolean") return row.bool;
  if (row.type === "number") return parseNumber(row.text);
  return row.text.trim();
}

const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

export interface DraftErrors {
  title?: string;
  description?: string;
  values: Record<string, string>;
  general?: string;
}

export function validate(draft: Draft): DraftErrors {
  const errors: DraftErrors = { values: {} };
  const title = draft.title.trim();
  if (!title) errors.title = "El títol no pot quedar buit.";
  else if (title.length > LIMITS.title) errors.title = `Com a molt ${LIMITS.title} caràcters.`;
  else if (CONTROL.test(title)) errors.title = "El títol té caràcters no vàlids.";
  if (draft.description.trim().length > LIMITS.description) errors.description = `Com a molt ${LIMITS.description} caràcters.`;
  if (draft.values.length > LIMITS.values) errors.general = `Com a molt ${LIMITS.values} valors.`;
  for (const row of draft.values) {
    const unit = row.unit.trim();
    if (!VALUE_KEY.test(row.key)) errors.values[row.key] = "Valor intern no vàlid.";
    else if (row.type === "number" && parseNumber(row.text) === undefined) {
      errors.values[row.key] = "Escriu un número (per exemple 70 o 99,5).";
    } else if (row.type === "text" && !row.text.trim()) {
      errors.values[row.key] = "Escriu el text del valor o tria «Sense valor».";
    } else if (row.type === "text" && row.text.trim().length > LIMITS.text) {
      errors.values[row.key] = `El text del valor pot tenir com a molt ${LIMITS.text} caràcters.`;
    } else if (unit.length > LIMITS.unit) {
      errors.values[row.key] = `La unitat pot tenir com a molt ${LIMITS.unit} caràcters.`;
    } else if (/[\n\t]/.test(row.unit) || CONTROL.test(row.unit)) {
      errors.values[row.key] = "La unitat ha de ser una sola línia.";
    }
  }
  return errors;
}

export function hasErrors(errors: DraftErrors): boolean {
  return Boolean(errors.title || errors.description || errors.general || Object.keys(errors.values).length);
}

// --------------------------------------------------------------------------- //
// Càrrega diferencial                                                           //
// --------------------------------------------------------------------------- //

function valueChanged(row: DraftValue, base: EditableValue | undefined): boolean {
  if (!base) return true;
  const value = scalarOf(row);
  return (
    !sameOperator(row.operator, base.operator) ||
    value !== base.value ||
    (row.unit.trim() || null) !== (base.unit_label ?? null)
  );
}

export interface ReviewerValuePayload {
  key: string;
  operator: string | null;
  value: ScalarValue;
  unit_label: string | null;
}

export interface CorrectionContent {
  title?: string;
  description?: string | null;
  category?: string;
  obligation?: string;
  modality?: string;
  values: ReviewerValuePayload[];
  remove_values: string[];
}

/** Només el que ha canviat respecte de `base`. Els valors intactes no hi surten. */
export function diff(base: EditableRequirement, draft: Draft): CorrectionContent {
  const content: CorrectionContent = { values: [], remove_values: [] };
  const title = draft.title.trim();
  if (title !== base.title) content.title = title;
  const description = draft.description.trim() || null;
  if (description !== (base.description ?? null)) content.description = description;
  for (const field of ["category", "obligation", "modality"] as const) {
    if (draft[field] !== base[field]) content[field] = draft[field];
  }
  const byKey = new Map(base.values.map((value) => [value.key, value]));
  for (const row of draft.values) {
    const original = byKey.get(row.key);
    if (!valueChanged(row, original)) continue;
    const value = scalarOf(row);
    content.values.push({
      key: row.key,
      // Un operador equivalent no es reescriu: `gte` continua sent `gte`.
      operator: original && sameOperator(row.operator, original.operator) ? original.operator : row.operator || null,
      value: value === undefined ? null : value,
      unit_label: row.unit.trim() || null,
    });
  }
  content.remove_values = draft.removed.filter((key) => byKey.has(key));
  return content;
}

export function isNoop(content: CorrectionContent): boolean {
  return (
    content.title === undefined &&
    content.description === undefined &&
    content.category === undefined &&
    content.obligation === undefined &&
    content.modality === undefined &&
    content.values.length === 0 &&
    content.remove_values.length === 0
  );
}

/** Estat després de corregir: el d'ara (per defecte) o verificat, i res més. */
export type StatusChoice = "keep" | "verified";

/** El backend demana nota per acabar en parcial o descartat. */
export function noteRequired(item: CanonicalRequirement, choice: StatusChoice): boolean {
  return choice === "keep" && (item.verification_status === "partial" || item.verification_status === "rejected");
}

export function correctionPayload(
  item: CanonicalRequirement,
  content: CorrectionContent,
  choice: StatusChoice,
  note: string,
): Record<string, unknown> {
  const payload: Record<string, unknown> = { ...content };
  if (content.values.length === 0) delete payload.values;
  if (content.remove_values.length === 0) delete payload.remove_values;
  // Explícit sempre: el backend, si no se li diu, verificaria.
  payload.verification_status = choice === "verified" ? "verified" : item.verification_status;
  if (note.trim()) payload.note = note.trim();
  return payload;
}

// --------------------------------------------------------------------------- //
// Després d'un 409: rebase explícit                                             //
// --------------------------------------------------------------------------- //

export const FIELD_LABELS: Record<string, string> = {
  title: "Títol",
  description: "Descripció",
  category: "Categoria",
  obligation: "Obligació",
  modality: "Modalitat",
};

export interface Rebased {
  draft: Draft;
  /** Què ha canviat a la versió nova respecte de quan es va obrir l'editor. */
  changedOnServer: string[];
  /** Camps que han canviat als dos llocs: la persona ha de mirar-los. */
  conflicts: string[];
}

function sameEditableValue(a: EditableValue | undefined, b: EditableValue | undefined): boolean {
  if (!a || !b) return a === b;
  return sameOperator(a.operator, b.operator) && a.value === b.value && (a.unit_label ?? null) === (b.unit_label ?? null);
}

/**
 * Tres versions: `base` (quan es va obrir), `current` (la del servidor ara) i
 * `draft` (el que la persona ha escrit). El que la persona no ha tocat passa a
 * ser el del servidor; el que ha tocat es conserva; si els dos han tocat el
 * mateix, es marca com a conflicte. No es barreja res en silenci i no s'envia
 * res: cal tornar a prémer «Desa».
 */
export function rebase(base: EditableRequirement, current: EditableRequirement, draft: Draft): Rebased {
  const changedOnServer: string[] = [];
  const conflicts: string[] = [];
  const next: Draft = { ...draft, values: [], removed: [] };
  const baseText: Record<string, string> = {
    title: base.title,
    description: base.description ?? "",
    category: base.category,
    obligation: base.obligation,
    modality: base.modality,
  };
  const currentText: Record<string, string> = {
    title: current.title,
    description: current.description ?? "",
    category: current.category,
    obligation: current.obligation,
    modality: current.modality,
  };
  for (const field of ["title", "description", "category", "obligation", "modality"] as const) {
    const mine = field === "title" || field === "description" ? draft[field].trim() : draft[field];
    const userChanged = mine !== baseText[field];
    const serverChanged = currentText[field] !== baseText[field];
    if (serverChanged) changedOnServer.push(FIELD_LABELS[field]);
    if (userChanged && serverChanged && mine !== currentText[field]) conflicts.push(FIELD_LABELS[field]);
    next[field] = userChanged ? draft[field] : currentText[field];
  }

  const baseValues = new Map(base.values.map((v) => [v.key, v]));
  const currentValues = new Map(current.values.map((v) => [v.key, v]));
  const draftValues = new Map(draft.values.map((v) => [v.key, v]));
  let valuesChangedOnServer = false;
  let valueConflict = false;
  const keys = [...new Set([...base.values.map((v) => v.key), ...current.values.map((v) => v.key)])];
  for (const key of keys) {
    const before = baseValues.get(key);
    const now = currentValues.get(key);
    const mine = draftValues.get(key);
    const serverChanged = !sameEditableValue(before, now);
    if (serverChanged) valuesChangedOnServer = true;
    const userRemoved = draft.removed.includes(key);
    const userEdited = !!mine && !mine.isNew && valueChanged(mine, before);
    if (userRemoved) {
      if (now) next.removed.push(key);
      if (serverChanged) valueConflict = true;
    } else if (userEdited && mine) {
      next.values.push(mine);
      if (serverChanged) valueConflict = true;
    } else if (now) {
      next.values.push(draftValue(now));
    }
  }
  const taken = new Set([...currentValues.keys(), ...next.values.map((v) => v.key), ...next.removed]);
  for (const row of draft.values.filter((v) => v.isNew)) {
    // Una clau nova no pot trepitjar-ne una que el servidor hagi creat entretant.
    const key = currentValues.has(row.key) ? nextKey(taken) : row.key;
    taken.add(key);
    next.values.push({ ...row, key });
  }
  if (valuesChangedOnServer) changedOnServer.push("Valors");
  if (valueConflict) conflicts.push("Valors");
  return { draft: next, changedOnServer, conflicts };
}

// --------------------------------------------------------------------------- //
// Historial                                                                     //
// --------------------------------------------------------------------------- //

/** «Títol · Obligació · 2 valors»: què va tocar una correcció, sense cap valor ni clau. */
export function correctionSummary(changes: Record<string, unknown> | undefined): string | null {
  if (!changes) return null;
  const fields = Array.isArray(changes.fields) ? (changes.fields as string[]) : [];
  const set = Array.isArray(changes.values_set) ? changes.values_set.length : 0;
  const removed = Array.isArray(changes.values_removed) ? changes.values_removed.length : 0;
  const parts = fields.map((field) => FIELD_LABELS[field]).filter(Boolean);
  if (set) parts.push(`${set} ${set === 1 ? "valor" : "valors"}`);
  if (removed) parts.push(`${removed} ${removed === 1 ? "valor tret" : "valors trets"}`);
  return parts.length ? parts.join(" · ") : null;
}
