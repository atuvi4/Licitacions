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

/** Pàgina del catàleg (`/tenders` i `/companies/{c}/matches`). */
export interface CataloguePage<T> extends Page<T> {
  /** Només amb filtre d'import: files que no es mostren per no tenir import conegut. */
  excluded_without_amount?: number | null;
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
  /**
   * El PRIMER CPV que publica la font, no un «principal» declarat: el feed PLACSP
   * no marca cap CPV com a principal i la ingesta pren el primer de la llista
   * (`placsp/normalizer.py`). El principal del plec o del PSCP pot ser un altre
   * dels CPV de la llista (auditoria UI P1-05: L'Escala 2026/3964, 32562100 primer
   * al feed i 64200000 principal al PSCP i al DOC-03). Tots compten igual al Radar.
   */
  is_main: boolean;
  description: string | null;
}

/** Etiqueta del CPV `is_main`: el que és, no el que no sabem (auditoria UI P1-05). */
export const SOURCE_FIRST_CPV_LABEL = "primer a la font";

/** Per què no es diu «principal»: la font no ho indica. */
export const SOURCE_FIRST_CPV_HELP =
  "La font (feed PLACSP) no indica quin CPV és el principal: es marca el primer que publica. El CPV principal declarat al plec o al PSCP pot ser un altre dels d'aquesta llista. Al Radar, tots els CPV compten igual.";

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

/** D'on venen els bytes: descarregats d'una font o pujats per una persona. */
export type DocumentFileOrigin = "fetched" | "uploaded";

/**
 * Què se sap del TEXT d'un document. Mai què se sap del fitxer: un DOCX es
 * guarda perfectament i té `unsupported`, que no és cap error de pujada.
 *
 * El calcula el backend i arriba fet. La interfície no l'ha de deduir de
 * l'estat i la qualitat de l'extracció, perquè deduir-lo en dos llocs acabaria
 * amb dues pantalles dient coses diferents del mateix fitxer.
 */
export type DocumentAnalysisStatus =
  | "pending"
  | "extracted"
  | "no_usable_text"
  | "unsupported"
  | "failed";

export interface TenderDocumentFileSummary {
  file_id: string;
  version_number: number;
  download_status: DownloadStatus;
  origin: DocumentFileOrigin;
  file_size: number | null;
  sha256: string | null;
  mime_type: string | null;
  /** Si d'aquest FORMAT en sabem llegir el text. */
  text_extraction_supported: boolean;
  analysis_status: DocumentAnalysisStatus;
  extraction_status: ExtractionStatus | null;
  quality: ExtractionQuality | null;
  page_count: number | null;
  text_char_count: number | null;
  ocr_candidate: boolean;
  extraction_id: string | null;
  processed_at: string | null;
  error_code: string | null;
}

/**
 * El mateix veredicte que calcula el backend, per a les pantalles que només
 * tenen l'extracció crua a mà (el detall d'un document).
 *
 * És una traducció, no una segona font de veritat: el resum d'un document
 * l'envia el backend ja calculat i aquesta funció n'ha de dir exactament el
 * mateix.
 */
export function analysisStatusOf(
  extraction: { status: string; quality: string } | null,
): DocumentAnalysisStatus {
  if (!extraction || extraction.status === "pending" || extraction.status === "extracting") {
    return "pending";
  }
  if (extraction.status === "skipped_unsupported") return "unsupported";
  if (extraction.status === "failed") return "failed";
  if (extraction.quality === "empty" || extraction.quality === "low_text") return "no_usable_text";
  return "extracted";
}

/** D'on surt un document del paquet (3B-2 §2.1). */
export type TenderDocumentOrigin = "feed" | "owner_upload" | "operator_url" | "operator_upload";

/** Verificació d'una aportació de l'operador. `not_applicable` = feed o pujada pròpia. */
export type DocumentContributionStatus = "not_applicable" | "pending_verification" | "verified" | "rejected";

export interface TenderDocumentMeta {
  id: string;
  name: string;
  document_type: TenderDocumentType;
  source_document_type_code: string | null;
  url: string;
  mime_type: string | null;
  published_at: string | null;
  /** Data en què la font el va deixar de publicar. `null` = vigent a la font. */
  removed_at: string | null;
  /** Versió vigent descarregada. `null` = mai processat. */
  latest_file: TenderDocumentFileSummary | null;
  origin: TenderDocumentOrigin;
  contribution_status: DocumentContributionStatus;
  /** Àlies: mateixos bytes que aquest altre document del mateix expedient. */
  duplicate_of_document_id: string | null;
  /**
   * Si pot entrar a l'anàlisi (`ANALYZABLE_DOCUMENT` del servidor): vigent, no
   * àlies i del feed, d'una pujada pròpia o aportat i verificat. Ho calcula el
   * backend: aquí no es dedueix.
   */
  analyzable: boolean;
}

/** Estat del paquet documental (3B-2 §2.5, §4.4). */
export type PackageStatus = "complete" | "incomplete" | "unknown";

export type PackageGapCode =
  | "required_role_missing"
  | "referenced_document_missing"
  | "no_text"
  | "format_not_analyzable"
  | "annex_missing"
  | "declared_by_operator";

/** Estat d'una secció de l'estructura del plec (`TenderStructureView.section_status`). */
export type SectionStatus = "unknown" | "none_declared" | "present";

export interface TenderPackageGap {
  code: PackageGapCode;
  /** El rol que falta, el codi citat (`DOC-03`) o el document afectat. */
  key: string | null;
  /** Seccions que aportaria el document que falta. Buit = impacte desconegut. */
  would_provide: string[];
}

export interface TenderPackage {
  /** Cobertura: `incomplete` només si sabem que hi falta un document del paquet públic. */
  status: PackageStatus;
  /** Només els forats de COBERTURA oberts: documents que falten. */
  gaps: TenderPackageGap[];
  /**
   * Processabilitat: documents PRESENTS que Tuvi encara no pot llegir (format, extracció
   * fallida, sense text). No fan mai el paquet incomplet. Absent en respostes antigues.
   */
  processing?: TenderPackageGap[];
  /** Secció de l'estructura → estat (M2): la regla és `section_status_for` del motor. */
  section_status: Record<string, SectionStatus>;
}

/**
 * Motius de la quota (`prerequisites.package.issues`) que només diuen que el
 * paquet NO S'HA COMPROVAT, no que hi falti res (`analysis_planning._package_issues`).
 */
const UNVERIFIED_PACKAGE_ISSUES: ReadonlySet<string> = new Set(["classification_not_current"]);

/**
 * La cobertura del paquet, UNA sola semàntica per a la quota i per a Plecs (F10):
 * - `incomplete` només amb forats explícits (els `gaps` de la fitxa o un motiu de
 *   la quota que no sigui «sense comprovar»);
 * - `unknown` quan no s'ha comprovat (classificació no vigent) o no se sap: mai
 *   es converteix en «falten documents»;
 * - `complete` quan la classificació vigent no hi troba cap forat.
 * `issueCodes`: els codis de la quota, quan n'hi ha; manen sobre la fitxa.
 */
export function packageCoverageState(pkg: TenderPackage | null | undefined, issueCodes?: string[]): PackageStatus {
  if (issueCodes && issueCodes.length > 0) {
    return issueCodes.some((code) => !UNVERIFIED_PACKAGE_ISSUES.has(code)) ? "incomplete" : "unknown";
  }
  if (!pkg) return "unknown";
  if (pkg.status === "incomplete" && pkg.gaps.length === 0) return "unknown";
  return pkg.status;
}

/**
 * Forats de PROCESSABILITAT (el document hi és però Tuvi encara no en pot llegir el
 * text): mai fan el paquet incomplet. La mateixa regla que `PROCESSING_GAP_CODES` del
 * servidor.
 */
export const PROCESSING_GAP_CODES: ReadonlySet<string> = new Set(["no_text", "format_not_analyzable"]);

export function isProcessingGap(gap: { code: string }): boolean {
  return PROCESSING_GAP_CODES.has(gap.code);
}

/** El text de cada estat de cobertura: el mateix a la quota i a Plecs. */
export const PACKAGE_COVERAGE_TEXT: Record<PackageStatus, string> = {
  complete: "Paquet verificat: segons la classificació vigent no falta cap document del plec.",
  incomplete: "Falten documents",
  unknown: "Encara no s'ha verificat si el paquet documental és complet.",
};

/** D'on surt el rol efectiu d'un document (`RoleSource` del servidor). */
export type RoleSource = "classifier" | "person" | "operator" | "global_classifier" | "feed" | "stale_override";

export const ROLE_SOURCE_LABELS: Record<RoleSource, string> = {
  classifier: "Classificació automàtica",
  person: "Fixat per l'empresa",
  operator: "Revisat per l'operador de la plataforma",
  global_classifier: "Classificació automàtica de la plataforma",
  feed: "Tipus publicat per la font",
  // 3B-2 §3.2: l'empresa l'havia fixat per a una versió anterior del fitxer.
  stale_override: "Fixat per l'empresa per a una versió anterior del fitxer: no s'aplica",
};

export function roleSourceLabel(source: string | null | undefined): string {
  return source && Object.hasOwn(ROLE_SOURCE_LABELS, source)
    ? ROLE_SOURCE_LABELS[source as RoleSource]
    : "Origen del tipus desconegut";
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
  /** Opcional: el filtre d'import coalesceix estimat, base sense IVA i base. */
  base_budget_without_tax?: string | null;
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
  /** El `updated` de la font de l'última entrada aplicada. */
  source_updated_at?: string | null;
  /** Ja no disponible a la font (làpida). NO és un estat: `status` no canvia. */
  source_removed_at?: string | null;
  /** Documents vigents. */
  document_count?: number;
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
  /** Ja no disponible a la font (làpida), si el servei ho publica. NO és un estat. */
  source_removed_at?: string | null;
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
  /** Retirats de la font, a part: `documents` és només la publicació vigent. */
  removed_documents: TenderDocumentMeta[];
  /** Estat del paquet documental i forats oberts (global, com la licitació). */
  package: TenderPackage | null;
  traceability: TenderTraceability | null;
}

export interface TenderFacets {
  statuses: string[];
  procedure_types: string[];
  contract_types: string[];
  nuts_codes: string[];
  total_tenders: number;
  sources?: SourceFacet[];
  status_counts?: Record<string, number>;
}

export interface SourceFacet {
  /** Codi tal com està desat: el que accepta el filtre `source`. */
  code: string;
  name: string;
  count: number;
}

const SOURCE_LABELS: Record<string, string> = {
  PLACSP: "PLACSP · perfils de contractant",
  placsp_agregadas: "PLACSP · plataformes agregades (PSCP)",
  manual: "Importació manual",
};

/** Etiqueta d'un diccionari: només claus pròpies, i mai el codi cru si no hi és. */
export function labelOf(map: Record<string, string>, code: string | null | undefined, fallback: string): string {
  return code != null && Object.hasOwn(map, code) ? map[code] : fallback;
}

/** Nom de la font per mostrar: primer el diccionari català, si no el de les facetes, si no «Font desconeguda» (mai el codi cru). */
export function sourceLabel(code: string | null | undefined, sources?: SourceFacet[]): string | null {
  if (!code) return null;
  const facet = sources?.find((source) => source.code === code);
  return labelOf(SOURCE_LABELS, code, facet?.name || "Font desconeguda");
}

/* -------------------------------------------------------------------------- */
/* Etiquetes                                                                   */
/* -------------------------------------------------------------------------- */

export const TENDER_STATUS_LABELS: Record<TenderStatus, string> = {
  pre_announcement: "Anunci previ",
  // Estat CRU de la font (PLACSP `PUB`): un fet, no un judici temporal. «En
  // termini» és l'estat accionable derivat del termini (`tenderStatusView`, F2).
  open: "Publicada",
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

/** Un tipus de document que el diccionari no conegui es diu en humà, mai el codi. */
export function documentTypeLabel(value: unknown): string {
  return typeof value === "string" && Object.hasOwn(TENDER_DOCUMENT_LABELS, value)
    ? TENDER_DOCUMENT_LABELS[value as TenderDocumentType]
    : "tipus de document desconegut";
}

/** Rols de document (`DocumentRole` del servidor): quin falta, quin s'ha revisat. */
export const DOCUMENT_ROLE_LABELS: Record<string, string> = {
  pcap: "plec de clàusules administratives (PCAP)",
  ppt: "plec de prescripcions tècniques (PPT)",
  characteristics_table: "quadre de característiques",
  offer_model: "model d'oferta",
  inventory_annex: "annex d'inventari",
  justification_report: "memòria justificativa",
  approval_resolution: "resolució d'aprovació",
  administrative_form: "formulari administratiu",
  notice: "anunci de licitació",
  other: "document",
};

export function documentRoleLabel(value: unknown): string {
  return typeof value === "string" && Object.hasOwn(DOCUMENT_ROLE_LABELS, value)
    ? DOCUMENT_ROLE_LABELS[value]
    : "tipus de document desconegut";
}

/* -------------------------------------------------------------------------- */
/* Ajudes de presentació                                                       */
/* -------------------------------------------------------------------------- */

// El comptador de dies viu a `lib/deadline` (una sola regla per a totes les pantalles).

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

/**
 * La descripció d'un CPV només si diu alguna cosa (addendum 3B-2, D4). El
 * catàleg (`cpv_codes`) desa «CPV <codi>» com a descripció provisional quan
 * no en coneix cap: pintar-la al costat del codi és repetir el codi.
 */
export function cpvDescription(cpv: { cpv_code: string; description?: string | null }): string | null {
  const text = cpv.description?.trim();
  if (!text) return null;
  const onlyCode = /^(cpv\s*)?[\d\s-]+$/i.test(text);
  // Els 8 dígits del codi, sense el de control (45314320-6 i 45314320 són el mateix codi).
  const digits = (value: string) => value.replace(/\D/g, "").slice(0, 8);
  return onlyCode && digits(text) === digits(cpv.cpv_code) ? null : text;
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
