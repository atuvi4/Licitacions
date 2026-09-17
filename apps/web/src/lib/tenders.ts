/**
 * Domini de licitacions públiques.
 *
 * Va en un mòdul propi i no dins de `types.ts`/`format.ts` perquè és una
 * frontera real: això són dades **públiques i globals**, no del tenant. Cap
 * d'aquests tipus porta `company_id`.
 */

const LOCALE = "ca-ES";

export type TenderStatus =
  | "pre_announcement"
  | "open"
  | "pending_award"
  | "awarded"
  | "resolved"
  | "cancelled"
  | "unknown";

export type ProcedureType =
  | "open"
  | "open_simplified"
  | "restricted"
  | "negotiated_without_publicity"
  | "negotiated_with_publicity"
  | "competitive_dialogue"
  | "minor_contract"
  | "framework_derived"
  | "design_contest"
  | "innovation_partnership"
  | "innovation_partnership_derived"
  | "dynamic_purchasing_system"
  | "tender_with_negotiation"
  | "internal_rules"
  | "other"
  | "unknown";

export type ContractType =
  | "goods"
  | "services"
  | "works"
  | "public_services_management"
  | "services_concession"
  | "public_works_concession"
  | "works_concession"
  | "public_private_partnership"
  | "special_administrative"
  | "private"
  | "patrimonial"
  | "other"
  | "unknown";

export type TenderDocumentType =
  | "administrative_terms"
  | "technical_terms"
  | "notice"
  | "award"
  | "additional"
  | "other";

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface BuyerSummary {
  id: string;
  name: string;
  tax_identifier: string | null;
  city: string | null;
  nuts_code: string | null;
}

export interface Buyer extends BuyerSummary {
  external_identifier: string;
  registry_identifier: string | null;
  buyer_type: string | null;
  activity: string | null;
  website: string | null;
  buyer_profile_url: string | null;
  address: string | null;
  postal_code: string | null;
  country_code: string | null;
  hierarchy: string | null;
}

export interface TenderCPV {
  cpv_code: string;
  is_main: boolean;
  description: string | null;
}

export interface TenderLot {
  id: string;
  external_lot_id: string;
  lot_number: number | null;
  title: string | null;
  description: string | null;
  estimated_value: string | null;
  base_budget: string | null;
  base_budget_without_tax: string | null;
  currency: string;
  place_of_execution: string | null;
  nuts_code: string | null;
  deadline_at: string | null;
  cpvs: TenderCPV[];
}

export type DownloadStatus = "pending" | "downloading" | "downloaded" | "failed";

export type ExtractionStatus =
  | "pending"
  | "extracting"
  | "extracted"
  | "skipped_unsupported"
  | "failed";

export type ExtractionQuality = "good" | "low_text" | "empty" | "failed" | "not_applicable";

export interface TenderDocumentFileSummary {
  file_id: string;
  version_number: number;
  download_status: DownloadStatus;
  file_size: number | null;
  sha256: string | null;
  extraction_status: ExtractionStatus | null;
  quality: ExtractionQuality | null;
  page_count: number | null;
  text_char_count: number | null;
  ocr_candidate: boolean;
  extraction_id: string | null;
  processed_at: string | null;
  error_code: string | null;
}

export interface TenderDocumentMeta {
  id: string;
  name: string;
  document_type: TenderDocumentType;
  source_document_type_code: string | null;
  url: string;
  mime_type: string | null;
  published_at: string | null;
  /** Versió vigent descarregada. `null` = mai processat. */
  latest_file: TenderDocumentFileSummary | null;
}

export interface TenderListItem {
  id: string;
  external_id: string;
  expediente_number: string | null;
  title: string;
  status: TenderStatus;
  procedure_type: ProcedureType;
  contract_type: ContractType;
  estimated_value: string | null;
  base_budget: string | null;
  currency: string;
  publication_date: string | null;
  deadline_at: string | null;
  nuts_code: string | null;
  place_of_execution: string | null;
  buyer: BuyerSummary | null;
  cpvs: TenderCPV[];
  lot_count: number;
  /** Quantes vegades la FONT ha publicat aquest expedient. NO és el nombre de
   *  canvis ni el de revisions del cicle de vida: són històries diferents. */
  source_publication_count: number;
  source_code: string | null;
  is_modified: boolean;
}

export interface TenderRevision {
  id: string;
  revision_number: number;
  raw_hash: string;
  raw_size: number;
  fetched_at: string;
  source_updated_at: string | null;
  source_url: string | null;
  parser_version: string;
  processing_status: string;
  error_message: string | null;
}

export interface TenderTraceability {
  source_code: string;
  source_name: string;
  external_id: string;
  source_url: string | null;
  latest_source_record_id: string | null;
  fetched_at: string | null;
  parser_version: string | null;
  raw_hash: string | null;
}

export interface TenderDetail {
  id: string;
  external_id: string;
  expediente_number: string | null;
  title: string;
  description: string | null;
  status: TenderStatus;
  source_status_code: string | null;
  procedure_type: ProcedureType;
  contract_type: ContractType;
  estimated_value: string | null;
  base_budget: string | null;
  base_budget_without_tax: string | null;
  currency: string;
  publication_date: string | null;
  deadline_at: string | null;
  place_of_execution: string | null;
  nuts_code: string | null;
  country_code: string | null;
  source_url: string | null;
  first_seen_at: string;
  last_seen_at: string;
  last_changed_at: string | null;
  /** Quantes vegades la FONT ha publicat aquest expedient. NO és el nombre de
   *  canvis ni el de revisions del cicle de vida: són històries diferents. */
  source_publication_count: number;
  buyer: Buyer | null;
  cpvs: TenderCPV[];
  lots: TenderLot[];
  documents: TenderDocumentMeta[];
  traceability: TenderTraceability | null;
}

export interface TenderFacets {
  statuses: string[];
  procedure_types: string[];
  contract_types: string[];
  nuts_codes: string[];
  total_tenders: number;
}

/* -------------------------------------------------------------------------- */
/* Etiquetes                                                                   */
/* -------------------------------------------------------------------------- */

export const TENDER_STATUS_LABELS: Record<TenderStatus, string> = {
  pre_announcement: "Anunci previ",
  open: "En termini",
  pending_award: "Pendent d'adjudicació",
  awarded: "Adjudicada",
  resolved: "Resolta",
  cancelled: "Anul·lada",
  unknown: "Sense determinar",
};

export const TENDER_STATUS_TONE: Record<
  TenderStatus,
  "success" | "warning" | "danger" | "brand" | "neutral"
> = {
  pre_announcement: "neutral",
  open: "success",
  pending_award: "warning",
  awarded: "brand",
  resolved: "neutral",
  cancelled: "danger",
  unknown: "neutral",
};

export const PROCEDURE_TYPE_LABELS: Record<ProcedureType, string> = {
  open: "Obert",
  open_simplified: "Obert simplificat",
  restricted: "Restringit",
  negotiated_without_publicity: "Negociat sense publicitat",
  negotiated_with_publicity: "Negociat amb publicitat",
  competitive_dialogue: "Diàleg competitiu",
  minor_contract: "Contracte menor",
  framework_derived: "Derivat d'acord marc",
  design_contest: "Concurs de projectes",
  innovation_partnership: "Associació per a la innovació",
  innovation_partnership_derived: "Derivat d'associació per a la innovació",
  dynamic_purchasing_system: "Sistema dinàmic d'adquisició",
  tender_with_negotiation: "Licitació amb negociació",
  internal_rules: "Normes internes",
  other: "Altres",
  unknown: "Sense determinar",
};

export const CONTRACT_TYPE_LABELS: Record<ContractType, string> = {
  goods: "Subministraments",
  services: "Serveis",
  works: "Obres",
  public_services_management: "Gestió de serveis públics",
  services_concession: "Concessió de serveis",
  public_works_concession: "Concessió d'obres públiques",
  works_concession: "Concessió d'obres",
  public_private_partnership: "Col·laboració público-privada",
  special_administrative: "Administratiu especial",
  private: "Privat",
  patrimonial: "Patrimonial",
  other: "Altres",
  unknown: "Sense determinar",
};

export const TENDER_DOCUMENT_LABELS: Record<TenderDocumentType, string> = {
  administrative_terms: "Plec administratiu",
  technical_terms: "Plec tècnic",
  notice: "Anunci",
  award: "Adjudicació",
  additional: "Addicional",
  other: "Altres",
};

/* -------------------------------------------------------------------------- */
/* Ajudes de presentació                                                       */
/* -------------------------------------------------------------------------- */

/** Dies que falten per al termini. Negatiu si ja ha passat. */
export function daysUntilDeadline(value: string | null): number | null {
  if (!value) return null;
  const target = new Date(value).getTime();
  if (Number.isNaN(target)) return null;
  return Math.ceil((target - Date.now()) / (1000 * 60 * 60 * 24));
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

/** Construeix la query string del feed sense paràmetres buits. */
export function buildQuery(params: Record<string, string | number | boolean | undefined | null>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  return search.toString();
}


export type DocumentState =
  | "not_downloaded"
  | "downloaded"
  | "extracted"
  | "ocr_candidate"
  | "unsupported"
  | "error";

/**
 * Estat visible d'un document. Es deriva al frontend perquè és pura
 * presentació: el backend ja publica els estats crus i no li'n cal cap de
 * combinat.
 */
export function documentState(file: TenderDocumentFileSummary | null): DocumentState {
  if (!file) return "not_downloaded";
  if (file.download_status === "failed") return "error";
  if (file.download_status !== "downloaded") return "not_downloaded";
  if (file.extraction_status === "failed") return "error";
  if (file.extraction_status === "skipped_unsupported") return "unsupported";
  if (file.extraction_status !== "extracted") return "downloaded";
  return file.ocr_candidate ? "ocr_candidate" : "extracted";
}

export const DOCUMENT_STATE_LABELS: Record<DocumentState, string> = {
  not_downloaded: "No descarregat",
  downloaded: "Descarregat",
  extracted: "Text extret",
  ocr_candidate: "Poc text · possible escaneig",
  unsupported: "Sense extracció de text",
  error: "Error",
};

/** Mida llegible. `null` quan encara no s'ha descarregat res. */
export function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} kB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
