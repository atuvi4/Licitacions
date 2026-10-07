/**
 * Canvis publicats d'una licitacio, en llenguatge que es pugui llegir.
 *
 * El backend envia codis (`DEADLINE_CHANGED`) i no frases fetes, perque els
 * codis son estables i les frases no. Aqui es on es tradueixen, i es aqui on es
 * decideix que un canvi de titol s'ensenya en gris i una substitucio de plec en
 * vermell: la persona ha de poder distingir d'un cop d'ull el que l'obliga a
 * tornar a mirar-s'ho del que nomes es soroll.
 */

export type ChangeImpact =
  | "informational"
  | "prefit_affecting"
  | "requirements_affecting"
  | "compliance_affecting"
  | "workspace_critical";

export interface TenderChangeEvent {
  change_code: string;
  subject: string | null;
  old_value: unknown;
  new_value: unknown;
  impacts: ChangeImpact[];
  is_material: boolean;
  detected_at: string;
}

export interface TenderHistoryRevision {
  id: string;
  revision_number: number;
  trigger: string;
  created_at: string;
  change_summary: { material?: boolean; counts?: Record<string, number> } | null;
  events: TenderChangeEvent[];
}

export const CHANGE_LABELS: Record<string, string> = {
  TITLE_CHANGED: "Títol modificat",
  DESCRIPTION_CHANGED: "Descripció modificada",
  BUYER_CHANGED: "Òrgan de contractació modificat",
  STATUS_CHANGED: "Estat modificat",
  PROCEDURE_TYPE_CHANGED: "Tipus de procediment modificat",
  CONTRACT_TYPE_CHANGED: "Tipus de contracte modificat",
  TENDER_CANCELLED: "Licitació anul·lada",
  PUBLICATION_DATE_CHANGED: "Data de publicació modificada",
  DEADLINE_CHANGED: "Termini de presentació modificat",
  DEADLINE_ADDED: "Termini de presentació publicat",
  DEADLINE_REMOVED: "Termini de presentació retirat",
  ESTIMATED_VALUE_CHANGED: "Valor estimat modificat",
  BASE_BUDGET_CHANGED: "Pressupost base modificat",
  CURRENCY_CHANGED: "Moneda modificada",
  PLACE_CHANGED: "Lloc d'execució modificat",
  CPV_ADDED: "CPV afegit",
  CPV_REMOVED: "CPV retirat",
  MAIN_CPV_CHANGED: "CPV principal modificat",
  LOT_ADDED: "Lot afegit",
  LOT_REMOVED: "Lot retirat de la licitació",
  LOT_RESTORED: "Lot restituït a la licitació",
  LOT_VALUE_CHANGED: "Import d'un lot modificat",
  LOT_CPV_CHANGED: "CPV d'un lot modificat",
  LOT_TITLE_CHANGED: "Títol d'un lot modificat",
  LOT_DEADLINE_CHANGED: "Termini d'un lot modificat",
  LOT_METADATA_CHANGED: "Dades d'un lot modificades",
  // Presència a la font (làpida), no un estat de la licitació: mai «retirada».
  SOURCE_ENTRY_REMOVED: "Ja no disponible a la font",
  SOURCE_ENTRY_RESTORED: "Torna a estar disponible a la font",
  DOCUMENT_ADDED: "Document nou",
  DOCUMENT_REMOVED: "Document retirat",
  DOCUMENT_REPLACED: "Document substituït",
  DOCUMENT_URL_CHANGED: "Adreça d'un document modificada",
  DOCUMENT_METADATA_CHANGED: "Dades d'un document modificades",
  // No diu "canviat": el document sempre havia sigut aquell i el que ha canviat
  // és que ara sabem què hi ha dins. Anomenar-ho substitució seria acusar
  // l'òrgan d'una modificació que no ha fet.
  DOCUMENT_CONTENT_IDENTIFIED: "Contingut d'un document conegut per primera vegada",
};

export const IMPACT_LABELS: Record<ChangeImpact, string> = {
  informational: "Informatiu",
  prefit_affecting: "Afecta l'encaix previ",
  requirements_affecting: "Afecta l'anàlisi del plec",
  compliance_affecting: "Afecta el compliment",
  workspace_critical: "Afecta la candidatura",
};

export function changeLabel(code: string): string {
  return CHANGE_LABELS[code] ?? code;
}

export function changeTone(event: TenderChangeEvent): "neutral" | "warning" | "danger" {
  if (!event.is_material) return "neutral";
  if (event.impacts.includes("workspace_critical")) return "danger";
  return "warning";
}

/** El disparador de la revisio, dit en paraules i no en identificador. */
export function triggerLabel(trigger: string): string {
  if (trigger === "document_processing") return "En descarregar un document";
  if (trigger === "ingestion") return "Actualització de la font";
  return trigger;
}

const HASH = /^[0-9a-f]{40,}$/i;
const ISO = /^\d{4}-\d{2}-\d{2}T/;

/**
 * Un valor tal com el pugui llegir una persona.
 *
 * Els hashos es trunquen: sencers omplen la fila i no diuen res mes que
 * truncats. Les dates es formaten. La resta es deixa tal com ve: inventar-se
 * una presentacio per a un valor que no s'espera amaga mes del que aclareix.
 */
export function formatChangeValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  const text = String(value);
  if (HASH.test(text)) return `${text.slice(0, 12)}…`;
  if (ISO.test(text)) {
    const parsed = new Date(text);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleString("ca-ES", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }
  }
  return text;
}

/** "Termini de presentació modificat: 20/09/2026 → 27/09/2026". */
export function describeChange(event: TenderChangeEvent): string {
  const label = changeLabel(event.change_code);
  // Presència a la font: la frase ja ho diu tot; mai booleans ni el camp intern.
  if (PRESENCE_CODES.has(event.change_code)) return label;
  // Documents afegits o retirats: el nom (mai la URL, que és el subject).
  if (DOCUMENT_NAME_CODES.has(event.change_code)) {
    const name = event.change_code === "DOCUMENT_ADDED" ? event.new_value : event.old_value;
    return `${label}: ${typeof name === "string" && name.trim() ? name : "Document"}`;
  }
  const before = formatChangeValue(event.old_value);
  const after = formatChangeValue(event.new_value);
  if (before === "—" && after === "—") return label;
  if (before === "—") return `${label}: ${after}`;
  if (after === "—") return `${label}: ${before}`;
  return `${label}: ${before} → ${after}`;
}

const PRESENCE_CODES = new Set(["SOURCE_ENTRY_REMOVED", "SOURCE_ENTRY_RESTORED"]);
const DOCUMENT_NAME_CODES = new Set(["DOCUMENT_ADDED", "DOCUMENT_REMOVED"]);

/** El `subject` d'aquests canvis és tècnic (camp intern o URL de descàrrega): no es mostra. */
export function hidesSubject(event: TenderChangeEvent): boolean {
  return PRESENCE_CODES.has(event.change_code) || DOCUMENT_NAME_CODES.has(event.change_code);
}

/** Estat d'una publicació rebuda de la font, en paraules. */
export function publicationStatusLabel(status: string): string {
  switch (status) {
    case "processed":
      return "Processada";
    case "failed":
      return "Amb error";
    case "conflict":
      return "En conflicte (mateix instant, contingut diferent)";
    case "superseded":
      return "Publicació anterior (no aplicada)";
    case "pending":
      return "Pendent";
    default:
      return "Estat desconegut";
  }
}
