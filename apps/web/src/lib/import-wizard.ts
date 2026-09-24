/**
 * Lògica de l'assistent «Importa licitació» (S3-S2).
 *
 * Tot el que es pot decidir sense tocar el DOM viu aquí, i sobretot les dues
 * coses que, si es fan malament, no es veuen fins que ja hi ha dades brutes:
 *
 * **Els tokens es generen en obrir l'assistent i no canvien mai.** La identitat
 * d'una licitació manual és `(empresa, client_token)`: si el token es tornés a
 * generar a cada intent, un reintent després d'un error de xarxa crearia una
 * segona licitació en comptes de recuperar la que ja existeix. L'organisme té el
 * seu token propi, independent, pel mateix motiu i perquè són dues entitats.
 *
 * **Guardar i llegir són dos fets separats.** Un DOCX es guarda perfectament i
 * no se'n llegeix el text. Pintar-hi una creu vermella diria que la pujada ha
 * fallat, que és fals; pintar-hi un tic sense dir res més faria creure que el
 * document ja s'ha analitzat, que també és fals.
 */

import { DECISION_ROLES } from "@/lib/labels";
import type { DocumentAnalysisStatus, TenderDocumentFileSummary } from "@/lib/tenders";

/** Qui pot obrir un expedient a mà. Els mateixos que decideixen. */
export function canImportTender(role: string | null | undefined): boolean {
  return !!role && (DECISION_ROLES as readonly string[]).includes(role);
}

/** Què es fa amb l'organisme: res, triar-ne un, o escriure'n un de nou. */
export type BuyerChoice = "unknown" | "existing" | "manual";

export interface WizardTokens {
  /** Identifica la licitació durant tots els reintents. */
  tender: string;
  /** Identifica l'organisme manual. Independent del de la licitació. */
  buyer: string;
}

export function newTokens(): WizardTokens {
  return { tender: crypto.randomUUID(), buyer: crypto.randomUUID() };
}

export interface Step1Form {
  title: string;
  expediente_number: string;
  buyerChoice: BuyerChoice;
  buyerId: string;
  buyerName: string;
}

export const EMPTY_STEP1: Step1Form = {
  title: "",
  expediente_number: "",
  buyerChoice: "unknown",
  buyerId: "",
  buyerName: "",
};

/** El títol és l'únic obligatori. La resta es pot corregir després. */
export function step1Error(form: Step1Form): string | null {
  if (!form.title.trim()) return "El títol és obligatori.";
  if (form.buyerChoice === "existing" && !form.buyerId) return "Tria un organisme.";
  if (form.buyerChoice === "manual" && !form.buyerName.trim()) {
    return "Escriu el nom de l'organisme.";
  }
  return null;
}

/**
 * El cos del POST de creació.
 *
 * `buyer_id` i `buyer_manual` són excloents al backend, i aquí només se n'envia
 * un: enviar-los tots dos seria demanar-li que triï per nosaltres.
 */
export function createPayload(form: Step1Form, tokens: WizardTokens): Record<string, unknown> {
  const body: Record<string, unknown> = {
    client_token: tokens.tender,
    title: form.title.trim(),
  };
  if (form.expediente_number.trim()) body.expediente_number = form.expediente_number.trim();
  if (form.buyerChoice === "existing" && form.buyerId) body.buyer_id = form.buyerId;
  if (form.buyerChoice === "manual" && form.buyerName.trim()) {
    body.buyer_manual = { client_token: tokens.buyer, name: form.buyerName.trim() };
  }
  return body;
}

/**
 * El cos del PATCH quan algú torna enrere i corregeix.
 *
 * Porta `expected_revision` perquè el backend pugui dir que no si la licitació
 * ha canviat des que es va llegir, i **mai** cap camp d'identitat: `id`,
 * `source_id`, `external_id` i `owner_company_id` no s'editen, i el backend
 * rebutja la petició sencera si hi arriben.
 *
 * `expediente_number` s'hi envia sempre —encara que sigui buit— perquè ometre'l
 * vol dir «no el toquis» i buidar-lo ha de poder-se fer.
 */
export function updatePayload(
  form: Step1Form,
  tokens: WizardTokens,
  expectedRevision: number,
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    expected_revision: expectedRevision,
    title: form.title.trim(),
    expediente_number: form.expediente_number.trim() || null,
  };
  if (form.buyerChoice === "unknown") body.buyer_id = null;
  if (form.buyerChoice === "existing" && form.buyerId) body.buyer_id = form.buyerId;
  if (form.buyerChoice === "manual" && form.buyerName.trim()) {
    body.buyer_manual = { client_token: tokens.buyer, name: form.buyerName.trim() };
  }
  return body;
}

/* ----- El tipus d'un plec --------------------------------------------------- */

/**
 * Què és cada document, declarat per qui el puja.
 *
 * No és cosmètic: l'anàlisi automàtica de requisits només tria el plec
 * administratiu, el tècnic i els annexos. Un document sense classificar es
 * guarda i es revisa igual, però **no hi entra sol**, i això és deliberat.
 *
 * Els valors són els de `TenderDocumentType` del servidor. Aquí no se n'inventa
 * cap ni se'n dedueix cap del nom del fitxer: un PCAP anomenat «doc1.pdf»
 * seguiria sent un PCAP i un «PCAP-plantilla.pdf» podria no ser-ho. Ho diu la
 * persona o no ho diu ningú.
 */
export const UPLOAD_DOCUMENT_TYPES = [
  { value: "administrative_terms", label: "Plec administratiu (PCAP)" },
  { value: "technical_terms", label: "Plec tècnic (PPT)" },
  { value: "additional", label: "Annex o correcció" },
  { value: "notice", label: "Anunci de licitació" },
  { value: "other", label: "Altres / sense classificar" },
] as const;

export type UploadDocumentType = (typeof UPLOAD_DOCUMENT_TYPES)[number]["value"];

/** El valor honest per defecte: no se sap fins que algú ho diu. */
export const DEFAULT_DOCUMENT_TYPE: UploadDocumentType = "other";

/** Els tipus que l'anàlisi automàtica tria (`DEFAULT_DOCUMENT_TYPES` al servidor). */
export const ANALYSED_DOCUMENT_TYPES: readonly UploadDocumentType[] = [
  "administrative_terms",
  "technical_terms",
  "additional",
];

export function entersAnalysis(type: UploadDocumentType): boolean {
  return ANALYSED_DOCUMENT_TYPES.includes(type);
}

export function documentTypeLabel(type: UploadDocumentType): string {
  return UPLOAD_DOCUMENT_TYPES.find((option) => option.value === type)?.label ?? type;
}

/* ----- Estat d'un fitxer a Step 2 ------------------------------------------ */

export type UploadTone = "ok" | "info" | "warning" | "error";

export interface UploadRowState {
  tone: UploadTone;
  /** Què ha passat amb el FITXER. */
  stored: string;
  /** Què ha passat amb el TEXT. Buit quan encara no se'n sap res. */
  analysis: string;
}

const ANALYSIS: Record<DocumentAnalysisStatus, { tone: UploadTone; text: string }> = {
  pending: { tone: "info", text: "Pendent de llegir" },
  extracted: { tone: "ok", text: "Text llegit" },
  // No és cap error i tampoc no és una anàlisi: el fitxer hi és i no en surt
  // text aprofitable. No s'hi fa OCR.
  no_usable_text: { tone: "warning", text: "Sense text llegible" },
  unsupported: { tone: "info", text: "Extracció de text no suportada" },
  failed: { tone: "warning", text: "No s'ha pogut llegir el text" },
};

/**
 * Les dues frases d'una fila: què ha passat amb el fitxer i què amb el text.
 *
 * Van separades expressament. «Guardat» i «Text llegit» són dos fets, i el dia
 * que es fusionin en un sol semàfor algú llegirà «error» d'un DOCX que està
 * perfectament guardat.
 */
export function uploadRowState(file: TenderDocumentFileSummary): UploadRowState {
  const analysis = ANALYSIS[file.analysis_status] ?? ANALYSIS.pending;
  return { tone: analysis.tone, stored: "Guardat", analysis: analysis.text };
}

/** Una pujada que no ha arribat a guardar-se. Això sí que és un error. */
export function failedRowState(message: string): UploadRowState {
  return { tone: "error", stored: "No s'ha pujat", analysis: message };
}

/** Mida llegible. Sense decimals inventats: els bytes són exactes. */
export function fileSize(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
