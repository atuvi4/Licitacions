import { formatDate } from "@/lib/format";
import type { JobRequest } from "@/lib/jobs";
import { DOCUMENT_STATE_LEVEL, jobLevel, type Level } from "@/lib/labels";
import { requirementAnalysisState, type RequirementAnalysis } from "@/lib/requirements";
import {
  DOCUMENT_STATE_LABELS,
  documentState,
  type TenderDocumentFileSummary,
  type TenderDocumentMeta,
} from "@/lib/tenders";

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

/** Codis reals de `document_download.py`, `pdf_text.py` i `doc_text.py`, en llenguatge humà. */
export const FILE_ERROR_MESSAGES: Record<string, string> = {
  http_error: "La font ha respost amb un error en demanar el document.",
  too_large: "El document supera la mida màxima que es pot descarregar.",
  timeout: "La descàrrega ha esgotat el temps d'espera.",
  network_error: "No s'ha pogut connectar amb la font del document.",
  not_found: "El document ja no existeix a la font.",
  checksum_mismatch:
    "Els bytes baixats no coincideixen amb l'MD5 que publica el portal de la PSCP: es tornarà a provar més endavant.",
  host_not_official: "La descàrrega ha intentat sortir del lloc oficial de la font.",
  invalid_content: "El fitxer publicat és buit o d'un tipus que no es pot desar.",
  unsupported_for_text_extraction: "Aquest format no permet extreure'n el text.",
  corrupt_pdf: "El PDF no es pot llegir: sembla malmès.",
  encrypted_pdf: "El PDF està protegit amb contrasenya i no se'n pot extreure el text.",
  pdf_crypto_unavailable:
    "El PDF està xifrat i el servidor encara no el pot obrir: es tornarà a provar en el pròxim processament.",
  doc_encrypted: "El document de Word està protegit amb contrasenya i no se'n pot extreure el text.",
  doc_unsupported_version: "És un document de Word 6/95 o anterior: d'aquesta versió no se n'extreu el text.",
  doc_unreadable: "El document de Word no es pot llegir: sembla malmès.",
  doc_too_large: "El document de Word té més text del que es pot extreure d'un sol document.",
  too_many_pages: "El document supera el nombre màxim de pàgines que es poden extreure.",
  stale: "El document ha canviat des que es va processar: cal reprocessar-lo.",
};

const UNKNOWN_FILE_ERROR = "No s'ha pogut processar aquest document.";

export function fileErrorText(code: string | null | undefined): string {
  return (code && FILE_ERROR_MESSAGES[code]) || UNKNOWN_FILE_ERROR;
}

/**
 * On porta «Origen ↗». Un document generat pel connector del portal (`pscp-portal://`) no té
 * cap fitxer a l'origen: l'enllaç és la fitxa de la licitació a la PSCP. Una pujada
 * (`upload://`, `operator-upload://`) no té origen web: cap enllaç.
 */
export function originHref(url: string, tenderSourceUrl: string | null | undefined): string | null {
  if (url.startsWith("pscp-portal://")) return tenderSourceUrl || null;
  return /^https?:\/\//i.test(url) ? url : null;
}

/** «12 pàg. · 34.567 caràcters», o «—» si no hi ha cap extracció. */
export function extractedText(file: TenderDocumentFileSummary | null): string {
  if (!file || !file.extraction_id || file.page_count === null) return "—";
  const pages = `${file.page_count} pàg.`;
  if (file.text_char_count === null) return pages;
  return `${pages} · ${NUMBER.format(file.text_char_count)} caràcters`;
}

/**
 * L'estat d'un document a la pestanya Plecs (A5): «Sense text extret» → «Text
 * extret · requisits encara no analitzats» → «Analitzat». Un document llegit no
 * és un document analitzat: fins que una anàlisi vigent l'hagi llegit, es diu
 * que els requisits encara no s'han analitzat. Un format que no sabem llegir o
 * un error es diuen tal com són.
 */
export function readingLabel(
  document: Pick<TenderDocumentMeta, "id" | "latest_file">,
  analysis: RequirementAnalysis | null,
): { label: string; level: Level } {
  const state = documentState(document.latest_file);
  if (state === "error") return { label: DOCUMENT_STATE_LABELS.error, level: DOCUMENT_STATE_LEVEL.error };
  if (state === "unsupported") return { label: DOCUMENT_STATE_LABELS.unsupported, level: DOCUMENT_STATE_LEVEL.unsupported };
  if (state === "not_downloaded" || state === "downloaded") return { label: "Sense text extret", level: "neutral" };
  const analysisState = requirementAnalysisState(analysis);
  const run = analysis?.run ?? null;
  const skipped = run?.skipped_documents?.some((item) => item.tender_document_id === document.id) ?? false;
  if (run && !skipped && analysisState === "current") return { label: "Analitzat", level: "ready" };
  if (run && !skipped && analysisState === "stale") return { label: "Analitzat · anàlisi obsoleta", level: "attention" };
  return { label: "Text extret · requisits encara no analitzats", level: "attention" };
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
