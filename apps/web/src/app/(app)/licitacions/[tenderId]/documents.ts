import { formatDate } from "@/lib/format";
import type { JobRequest } from "@/lib/jobs";
import { jobLevel, type Level } from "@/lib/labels";
import type { TenderDocumentFileSummary } from "@/lib/tenders";

/**
 * Pestanya Plecs (handoff L10): què és cada document, si s'ha llegit i amb
 * quina qualitat, i des d'on es processa. Tot surt de `latest_file` i de la
 * `JobRequest` de processat; cap codi intern es mostra com a text.
 *
 * C-1: la selecció de documents només governa el PROCESSAMENT; no limita quins
 * documents utilitza l'anàlisi de requisits (l'API d'`analysis-quotes` només
 * accepta `{ force }`), i aquí no es diu res que ho contradigui.
 */

const NUMBER = new Intl.NumberFormat("ca-ES");

/** Codis reals de `document_download.py` i `pdf_text.py`, en llenguatge humà. */
export const FILE_ERROR_MESSAGES: Record<string, string> = {
  http_error: "La font ha respost amb un error en demanar el document.",
  too_large: "El document supera la mida màxima que es pot descarregar.",
  timeout: "La descàrrega ha esgotat el temps d'espera.",
  network_error: "No s'ha pogut connectar amb la font del document.",
  not_found: "El document ja no existeix a la font.",
  unsupported_for_text_extraction: "Aquest format no permet extreure'n el text.",
  corrupt_pdf: "El PDF no es pot llegir: sembla malmès.",
  encrypted_pdf: "El PDF està protegit amb contrasenya i no se'n pot extreure el text.",
  stale: "El document ha canviat des que es va processar: cal reprocessar-lo.",
};

const UNKNOWN_FILE_ERROR = "No s'ha pogut processar aquest document.";

export function fileErrorText(code: string | null | undefined): string {
  return (code && FILE_ERROR_MESSAGES[code]) || UNKNOWN_FILE_ERROR;
}

/** «12 pàg. · 34.567 caràcters», o «—» si no hi ha cap extracció. */
export function extractedText(file: TenderDocumentFileSummary | null): string {
  if (!file || !file.extraction_id || file.page_count === null) return "—";
  const pages = `${file.page_count} pàg.`;
  if (file.text_char_count === null) return pages;
  return `${pages} · ${NUMBER.format(file.text_char_count)} caràcters`;
}

/** Sota la pastilla de lectura: només quan el text extret no és de fiar. */
export function readingSubtext(file: TenderDocumentFileSummary | null): string | null {
  if (!file) return null;
  if (file.ocr_candidate) return "possible escaneig · sense OCR";
  if (file.quality === "low_text") return "poc text extret";
  return null;
}

/** «v2 · 15 de set. 2026»: la versió descarregada i quan es va processar. */
export function versionText(file: TenderDocumentFileSummary | null): string | null {
  if (!file) return null;
  const version = `v${file.version_number}`;
  return file.processed_at ? `${version} · ${formatDate(file.processed_at)}` : version;
}

export interface Pill {
  level: Level;
  label: string;
}

/** Acceptada però encara a la cua: cap worker l'ha començada. */
export function isQueued(request: JobRequest | null): boolean {
  return request?.status === "accepted" && request.started === false && !request.progress;
}

/** La feina de processat, tal com està: un 200 en demanar-la no és «processat». */
export function jobPill(request: JobRequest | null): Pill | null {
  if (!request) return null;
  const level = jobLevel(request);
  // «accepted» vol dir a la cua o executant-se: no es diu «Processant» si no ha començat.
  if (isQueued(request)) return { level: "neutral", label: "En cua · pendent d'inici" };
  if (request.status === "accepted") {
    return {
      level,
      label: request.progress ? `Processant · ${request.progress.done} de ${request.progress.total}` : "Processant",
    };
  }
  if (request.status === "failed") return { level, label: "No s'ha pogut processar" };
  if (request.status === "cancelled") return { level, label: "Cancel·lada" };
  return { level, label: request.outcome === "partial" ? "Processat amb avisos" : "Processat" };
}

export function selectionLabel(count: number): { count: string; action: string } {
  return {
    count: `${count} ${count === 1 ? "triat" : "triats"}`,
    action: `Processa ${count} ${count === 1 ? "document" : "documents"}`,
  };
}
