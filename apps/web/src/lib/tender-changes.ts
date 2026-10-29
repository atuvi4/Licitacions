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

import { documentRoleLabel, documentTypeLabel } from "@/lib/tenders";

/**
 * D'on surt una revisió: el que ha publicat la font, el que Tuvi ha processat per
 * dins o el que una persona ha escrit en una licitació oberta a mà. Ho deriva el
 * backend amb un mapa tancat; aquí no es dedueix res del `trigger`.
 */
export type RevisionOrigin = "source" | "internal" | "manual";

export function isKnownOrigin(origin: unknown): origin is RevisionOrigin {
  return origin === "source" || origin === "internal" || origin === "manual";
}

export interface TenderHistoryRevision {
  id: string;
  revision_number: number;
  trigger: string;
  origin: RevisionOrigin;
  created_at: string;
  change_summary: { material?: boolean; counts?: Record<string, number> } | null;
  events: TenderChangeEvent[];
}

/** Títol de cada secció de l'historial. Cap parla de «l'òrgan» si no és la font. */
export const ORIGIN_SECTION_TITLES: Record<RevisionOrigin, string> = {
  source: "Publicat per la font",
  internal: "Processament intern (Tuvi)",
  manual: "Editat a mà",
};

export const ORIGIN_SECTION_HINTS: Record<RevisionOrigin, string> = {
  source: "El que la plataforma de contractació ha publicat o modificat de l'expedient.",
  internal:
    "El que hem sabut en descarregar i ordenar els documents. No és cap modificació publicada per la font.",
  manual: "Canvis fets a mà en aquesta licitació oberta per l'empresa.",
};

export const ORIGIN_ORDER: RevisionOrigin[] = ["source", "internal", "manual"];

/**
 * Les revisions de cada origen, en l'ordre en què arriben (la més recent primer).
 * Una revisió sense `origin` (API anterior) o amb un de desconegut no desapareix:
 * va al processament intern, que és l'únic cubell que no atribueix res a la font
 * ni a cap persona. `unclassifiedRevisions` diu quines són.
 */
export function revisionsByOrigin(
  revisions: TenderHistoryRevision[],
): Record<RevisionOrigin, TenderHistoryRevision[]> {
  const grouped: Record<RevisionOrigin, TenderHistoryRevision[]> = { source: [], internal: [], manual: [] };
  for (const revision of revisions) grouped[isKnownOrigin(revision.origin) ? revision.origin : "internal"].push(revision);
  return grouped;
}

/** Les revisions que no s'han pogut classificar: amb elles no es pot afirmar res de la font. */
export function unclassifiedRevisions(revisions: TenderHistoryRevision[]): TenderHistoryRevision[] {
  return revisions.filter((revision) => !isKnownOrigin(revision.origin));
}

/**
 * «Versió N» només compta el que la font ha publicat (A3): id de revisió → número
 * de versió oficial, 1 per a la primera revisió `source`. Les internes i les
 * manuals no en tenen: numerar-les faria passar una descàrrega per una versió
 * de l'expedient.
 */
export function sourceVersionNumbers(revisions: TenderHistoryRevision[]): Map<string, number> {
  const ordered = revisions
    .filter((revision) => revision.origin === "source")
    .sort((a, b) => a.revision_number - b.revision_number);
  return new Map(ordered.map((revision, index) => [revision.id, index + 1]));
}

/** L'etiqueta d'una revisió a la seva fila: la versió oficial, o què és si no n'és cap. */
export function revisionOriginLabel(revision: TenderHistoryRevision, versions: Map<string, number>): string {
  if (!isKnownOrigin(revision.origin)) return "Origen no informat";
  if (revision.origin === "source") {
    const version = versions.get(revision.id);
    return version === undefined ? "Publicat per la font" : `Versió ${version}`;
  }
  return revision.origin === "internal" ? "Processament intern" : "Editat a mà";
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
  MAIN_CPV_CHANGED: "Primer CPV de la font modificat",
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
  EXPEDIENTE_NUMBER_CHANGED: "Número d'expedient modificat",
  COUNTRY_CHANGED: "País modificat",
  // 3B-2: canvis interns del paquet documental. Cap és una modificació de la font.
  DOCUMENT_ALIAS_CHANGED: "Document agrupat amb un altre amb el mateix contingut",
  // El text sencer depèn de l'estat destí (pendent, verificat, rebutjat, retirat):
  // el posa `describeChange`. Aquí només el subjecte de la frase.
  DOCUMENT_CONTRIBUTION_CHANGED: "Aportació de l'operador",
  DOCUMENT_ROLE_REVIEWED: "Tipus de document revisat per l'operador",
  SOURCE_TYPE_CHANGED_AFTER_REVIEW: "La font ha canviat el tipus d'un document ja revisat (mana la revisió)",
};

/** Estats d'una aportació de l'operador tal com els escriu `_contribution_state` del servidor. */
const CONTRIBUTION_STATE_LABELS: Record<string, string> = {
  pending_verification: "pendent de verificació",
  verified: "verificat",
  // Sense marca d'aportació: el document ja és material de la licitació.
  analyzable: "verificat",
  rejected: "rebutjat",
  removed: "retirat",
  not_applicable: "sense verificació",
};

function contributionStateLabel(value: unknown): string {
  return typeof value === "string" && Object.hasOwn(CONTRIBUTION_STATE_LABELS, value)
    ? CONTRIBUTION_STATE_LABELS[value]
    : "estat desconegut";
}

export const IMPACT_LABELS: Record<ChangeImpact, string> = {
  informational: "Informatiu",
  prefit_affecting: "Afecta l'encaix previ",
  requirements_affecting: "Afecta l'anàlisi del plec",
  compliance_affecting: "Afecta el compliment",
  workspace_critical: "Afecta la candidatura",
};

/** Un codi que el diccionari no conegui es diu en humà; mai el codi cru. */
export function changeLabel(code: string): string {
  return Object.hasOwn(CHANGE_LABELS, code) ? CHANGE_LABELS[code] : "Canvi no reconegut";
}

export function changeTone(event: TenderChangeEvent): "neutral" | "warning" | "danger" {
  if (!event.is_material) return "neutral";
  if (event.impacts.includes("workspace_critical")) return "danger";
  return "warning";
}

const TRIGGER_LABELS: Record<string, string> = {
  ingestion: "Actualització de la font",
  tombstone: "La font ha retirat l'entrada",
  document_processing: "En descarregar un document",
  document_intake: "En incorporar o revisar documents del paquet",
  portal_sync: "Actualització del portal de la PSCP",
  normalization: "Correcció interna de dades ja desades",
  manual_create: "Alta manual",
  manual_edit: "Edició manual",
};

/** El disparador de la revisió, dit en paraules i no en identificador. */
export function triggerLabel(trigger: string): string {
  return Object.hasOwn(TRIGGER_LABELS, trigger) ? TRIGGER_LABELS[trigger] : "Revisió de l'estat";
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
  // Un objecte (dades d'un lot, p. ex.): només els valors, mai les claus internes.
  if (Array.isArray(value)) return value.map(formatChangeValue).join(", ");
  if (typeof value === "object") {
    return Object.values(value as Record<string, unknown>)
      .filter((item) => item !== null && item !== undefined && item !== "")
      .map(formatChangeValue)
      .join(" · ");
  }
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

/** «abans → després» amb un traductor de valors; només un dels dos si l'altre és buit. */
function transition(old: unknown, next: unknown, label: (value: unknown) => string): string | null {
  const hasOld = old !== null && old !== undefined && old !== "";
  const hasNew = next !== null && next !== undefined && next !== "";
  if (!hasOld && !hasNew) return null;
  if (!hasOld) return label(next);
  if (!hasNew) return label(old);
  return `${label(old)} → ${label(next)}`;
}

const NO_NAMES: ReadonlyMap<string, string> = new Map();

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/**
 * Les metadades d'un document (`name`, `document_type`, `published_at`) com a
 * frases, sense cap clau interna: «PCAP.pdf → PCAP definitiu.pdf · Plec
 * administratiu → Plec tècnic».
 */
function documentMetadataDiff(oldValue: unknown, newValue: unknown): string | null {
  const before = asRecord(oldValue) ?? {};
  const after = asRecord(newValue) ?? {};
  const parts = [
    before.name !== after.name ? transition(before.name, after.name, formatChangeValue) : null,
    before.document_type !== after.document_type
      ? transition(before.document_type, after.document_type, documentTypeLabel)
      : null,
    before.published_at !== after.published_at
      ? transition(before.published_at, after.published_at, (value) => `publicat el ${formatChangeValue(value)}`)
      : null,
  ].filter((part): part is string => Boolean(part));
  return parts.length > 0 ? parts.join(" · ") : null;
}

/**
 * "Termini de presentació modificat: 20/09/2026 → 27/09/2026".
 *
 * Els canvis d'un document porten el seu NOM al final (`names`: URL → nom), mai la
 * URL que fa de `subject`; els valors dels codis del paquet (estats d'aportació,
 * rols, tipus) es tradueixen amb els diccionaris i un valor desconegut no surt cru.
 */
export function describeChange(event: TenderChangeEvent, names: ReadonlyMap<string, string> = NO_NAMES): string {
  const code = event.change_code;
  const label = changeLabel(code);
  // Presència a la font: la frase ja ho diu tot; mai booleans ni el camp intern.
  if (PRESENCE_CODES.has(code)) return label;
  // Documents afegits o retirats: el nom (mai la URL, que és el subject).
  if (DOCUMENT_NAME_CODES.has(code)) {
    const name = code === "DOCUMENT_ADDED" ? event.new_value : event.old_value;
    return `${label}: ${typeof name === "string" && name.trim() ? name : "Document"}`;
  }
  const documentName = typeof event.subject === "string" ? names.get(event.subject) : undefined;
  const suffix = DOCUMENT_CODES.has(code) && documentName ? ` · ${documentName}` : "";
  const withValues = (values: string | null) => (values ? `${label}: ${values}${suffix}` : `${label}${suffix}`);

  switch (code) {
    case "DOCUMENT_CONTRIBUTION_CHANGED":
      return withValues(transition(event.old_value, event.new_value, contributionStateLabel));
    case "DOCUMENT_ROLE_REVIEWED":
      return withValues(transition(event.old_value, event.new_value, documentRoleLabel));
    case "SOURCE_TYPE_CHANGED_AFTER_REVIEW":
      return withValues(transition(event.old_value, event.new_value, documentTypeLabel));
    case "DOCUMENT_ALIAS_CHANGED": {
      const canonicalOf = (url: unknown) => (typeof url === "string" && names.get(url)) || null;
      if (!event.new_value && !event.old_value) return withValues(null);
      if (event.new_value) {
        const canonical = canonicalOf(event.new_value);
        return `${label}: ${canonical ? `àlies de ${canonical}` : "àlies d'un altre document"}${suffix}`;
      }
      const canonical = canonicalOf(event.old_value);
      return `Document desagrupat: ${canonical ? `deixa de ser àlies de ${canonical}` : "deixa de ser àlies d'un altre document"}${suffix}`;
    }
    case "DOCUMENT_URL_CHANGED":
      // Els valors són adreces: no es mostren.
      return withValues(null);
    case "DOCUMENT_METADATA_CHANGED":
      return withValues(documentMetadataDiff(event.old_value, event.new_value));
    default:
      return withValues(transition(event.old_value, event.new_value, formatChangeValue));
  }
}

const PRESENCE_CODES = new Set(["SOURCE_ENTRY_REMOVED", "SOURCE_ENTRY_RESTORED"]);
const DOCUMENT_NAME_CODES = new Set(["DOCUMENT_ADDED", "DOCUMENT_REMOVED"]);
/** Canvis que parlen d'un document: el seu `subject` és la URL de descàrrega. */
const DOCUMENT_CODES = new Set([
  ...DOCUMENT_NAME_CODES,
  "DOCUMENT_REPLACED",
  "DOCUMENT_URL_CHANGED",
  "DOCUMENT_METADATA_CHANGED",
  "DOCUMENT_CONTENT_IDENTIFIED",
  "DOCUMENT_ALIAS_CHANGED",
  "DOCUMENT_CONTRIBUTION_CHANGED",
  "DOCUMENT_ROLE_REVIEWED",
  "SOURCE_TYPE_CHANGED_AFTER_REVIEW",
]);
const URL_LIKE = /^https?:/i;

/**
 * El `subject` d'aquests canvis és tècnic (camp intern o URL de descàrrega): no es
 * mostra. Un document es diu pel nom dins de `describeChange`; una URL mai.
 */
export function hidesSubject(event: TenderChangeEvent): boolean {
  return (
    PRESENCE_CODES.has(event.change_code) ||
    DOCUMENT_CODES.has(event.change_code) ||
    (typeof event.subject === "string" && URL_LIKE.test(event.subject))
  );
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
