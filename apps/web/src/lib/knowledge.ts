import type { Level } from "@/components/ui/StatusPill";
import type { AreaKind, Coverage, CoverageArea, CoverageCompleteness, DocumentCategory, LinkedEntityType, RelationshipType } from "@/lib/types";

export type { AreaKind, Coverage, CoverageArea, CoverageCompleteness } from "@/lib/types";

/**
 * Coneixement de l'empresa (fase 2 del motor): tipus de l'API, rètols i
 * l'ESTAT EFECTIU que es pinta.
 *
 * Una regla travessa tot el fitxer: l'estat que veu la persona surt sempre de
 * la fotografia (`/knowledge/snapshot`), mai del `verification_status` d'una
 * fila. La fotografia ja sap si hi ha evidència enllaçada, si la dada ha
 * caducat i si l'abast s'ha estructurat; una fila antiga marcada «verificada»
 * sense cap document hi surt com a declarada, i així es mostra.
 */

/* ----- Tipus de l'API ------------------------------------------------------ */

export type KnowledgeSource = "current_verified" | "current_declared" | "historic" | "unknown";
export type PendingKind = "accreditation_pending" | "scope_unknown" | "in_progress" | "expired" | "historic_candidate";
export type KnowledgeLifecycle = "current" | "in_progress" | "retired";
export type ObservationSubject = "operating_fact" | "certification" | "service" | "solution" | "product" | "partner";

/** Tipus de dada de coneixement, tal com els anomena l'API (`/records/{kind}`, `pending.subject`). */
export type RecordKind =
  | "certification"
  | "service"
  | "operating_fact"
  | "solution"
  | "solution_capability"
  | "product"
  | "solution_product"
  | "partner"
  | "partner_role";

export interface KnowledgeClaim {
  record_id: string;
  source: KnowledgeSource;
  value: unknown;
  as_of: string;
  valid_until: string | null;
  accreditation_pending: boolean;
  has_evidence: boolean;
  scope_service_lines: string[] | null;
  scope_status: "structured" | "scope_unknown" | null;
  /** Cobertura territorial (fets, serveis). `null` o absent: no en diu res. */
  coverage?: Coverage | null;
}

export interface KnowledgeHint {
  observation_id: string;
  statement: string;
  observed_on: string | null;
  source_ref: string | null;
}

export interface KnowledgeEntry {
  key: string;
  source: KnowledgeSource;
  accreditation_pending: boolean;
  claims: KnowledgeClaim[];
  hints: KnowledgeHint[];
}

export interface PendingItem {
  kind: PendingKind;
  subject: string;
  key: string;
  detail: string;
  record_id: string | null;
  observation_id: string | null;
  superseded_by_current: boolean;
}

export type SnapshotSection = "certifications" | "facts" | "services" | "solutions" | "capabilities" | "products" | "partners";

export interface KnowledgeSnapshot {
  company_id: string;
  evaluated_on: string;
  fingerprint: string;
  certifications: KnowledgeEntry[];
  facts: KnowledgeEntry[];
  services: KnowledgeEntry[];
  solutions: KnowledgeEntry[];
  capabilities: KnowledgeEntry[];
  products: KnowledgeEntry[];
  partners: KnowledgeEntry[];
  pending_validation: PendingItem[];
}

export interface Observation {
  id: string;
  subject: ObservationSubject;
  subject_key: string;
  statement: string;
  proposal: Record<string, unknown>;
  observed_on: string | null;
  source_ref: string | null;
  status: "candidate" | "promoted" | "dismissed";
}

export interface CapabilityDefinition {
  code: string;
  service_line_code: string;
  label: string;
  value_type: "boolean" | "number" | "ordinal";
  comparator: string;
  unit: string | null;
  ordinal_scale: string[] | null;
}

export interface Vocabulary {
  service_lines: Array<{ code: string; label: string }>;
  capabilities: CapabilityDefinition[];
  standards: Array<{ code: string; label: string; levels: string[] | null }>;
}

interface RecordBase {
  id: string;
  knowledge_origin: "declared" | "promoted_observation";
  as_of: string;
  valid_until: string | null;
  lifecycle: KnowledgeLifecycle;
}

export interface OperatingFactRecord extends RecordBase {
  key: string;
  value: boolean;
  detail: string | null;
  coverage?: Coverage;
}
export interface SolutionRecord extends RecordBase {
  name: string;
  service_line_code: string;
  vendor: string | null;
  edition: string | null;
}
export interface CapabilityRecord extends RecordBase {
  solution_id: string;
  capability_code: string;
  status: "supported" | "supported_with_limits" | "not_supported";
  value_number: string | null;
  value_level: string | null;
  limits_note: string | null;
}
export interface ProductRecord extends RecordBase {
  manufacturer: string;
  model: string;
  service_line_code: string | null;
}
export interface PartnerRecord extends RecordBase {
  name: string;
  tax_id: string | null;
}
export interface PartnerRoleRecord extends RecordBase {
  partner_id: string;
  role: string;
  service_line_code: string | null;
  territory: string | null;
  coverage?: Coverage;
}

export interface KnowledgeRecords {
  operating_facts: OperatingFactRecord[];
  solutions: SolutionRecord[];
  solution_capabilities: CapabilityRecord[];
  products: ProductRecord[];
  solution_products: Array<RecordBase & { solution_id: string; product_id: string }>;
  partners: PartnerRecord[];
  partner_roles: PartnerRoleRecord[];
}

/* ----- Rètols -------------------------------------------------------------- */

/** L'API envia els rètols del vocabulari sense accents: aquí hi ha els que es mostren. */
export const SERVICE_LINE_LABELS: Record<string, string> = {
  voice_pbx: "Centraleta i veu IP",
  voice_trunk: "Troncals de veu i numeració",
  access_ftth: "Accessos FTTH",
  dedicated_network: "Xarxa dedicada entre seus",
  radio_link: "Radioenllaços",
  network_switching: "Electrònica de xarxa",
  fiber_deployment: "Desplegament i certificació de fibra",
  civil_works: "Obra civil",
  support_ops: "Suport, operació i nivells de servei",
  mobile_telephony: "Telefonia mòbil",
};

export const CAPABILITY_LABELS: Record<string, string> = {
  "pbx.concurrent_calls": "Trucades simultànies",
  "pbx.ivr_multilevel": "IVR multinivell",
  "pbx.queues": "Cues de trucades",
  "pbx.blf": "Estat de línia (BLF)",
  "pbx.call_park": "Aparcament de trucades",
  "pbx.voicemail_email": "Bústia amb avís per correu",
  "pbx.cdr_export": "CDR exportable",
  "pbx.recording_policy": "Gravació configurable per política",
  "pbx.tls_srtp": "Xifratge TLS i SRTP",
  "pbx.fraud_block": "Bloqueig contra frau",
  "pbx.outbound_number_select": "Números de sortida seleccionables per usuari",
  "pbx.extra_softphones_free": "Softphones addicionals sense cost",
  "pbx.queue_callback_keep_position": "Retorn de trucada conservant la posició a la cua",
  "pbx.hot_desking": "Hot desking",
  "switch.poe_standard": "Estàndard PoE",
  "switch.mlag": "Agregació redundant (MLAG o equivalent)",
  "fiber.otdr_level": "Nivell de certificació de fibra (1 = OLTS, 2 = OTDR)",
  "ops.sla_response_minutes": "Temps de resposta",
  "ops.sla_restore_hours": "Temps de restabliment",
};

const UNIT_LABELS: Record<string, string> = { numeros: "números", llicencies: "llicències" };

export const STANDARD_LABELS: Record<string, string> = {
  ENS: "ENS",
  ISO_9001: "ISO 9001",
  ISO_14001: "ISO 14001",
  ISO_27001: "ISO/IEC 27001",
};

export const STANDARD_DESCRIPTIONS: Record<string, string> = {
  ENS: "Esquema Nacional de Seguretat",
  ISO_9001: "Gestió de la qualitat",
  ISO_14001: "Gestió ambiental",
  ISO_27001: "Seguretat de la informació",
};

export const LEVEL_LABELS: Record<string, string> = { basic: "Bàsic", medium: "Mitjà", high: "Alt" };

export const FACT_KEYS = [
  "telecom_operator_cnmc",
  "own_number_range_nrn",
  "datacenter_tier3",
  "support_24x7",
  "own_ftth_infrastructure",
] as const;

export const FACT_LABELS: Record<string, string> = {
  telecom_operator_cnmc: "Operador inscrit a la CNMC",
  own_number_range_nrn: "Rang de numeració propi (NRN)",
  datacenter_tier3: "Centre de dades Tier-3",
  support_24x7: "Suport 24x7",
  own_ftth_infrastructure: "Infraestructura FTTH pròpia",
};

export const PARTNER_ROLE_LABELS: Record<string, string> = {
  supplier: "Proveïdor",
  wholesale_operator: "Operador majorista",
  subcontractor: "Subcontractista",
  commercial_partner: "Soci comercial",
  bid_writer: "Redacció d'ofertes",
  advisor: "Assessor",
};

export const CAPABILITY_STATUS_LABELS: Record<string, string> = {
  supported: "Suportada",
  supported_with_limits: "Suportada amb límits",
  not_supported: "No suportada",
};

export const DELIVERY_MODE_LABELS: Record<string, string> = {
  own: "Mitjans propis",
  partner: "Amb un soci",
  subcontract: "Subcontractat",
  resale: "Revenda",
};

export const LIFECYCLE_LABELS: Record<KnowledgeLifecycle, string> = {
  current: "Vigent",
  in_progress: "En tràmit",
  retired: "Retirat",
};

/* ----- Cobertura territorial (0047) ---------------------------------------- */

export const NEUTRAL_COVERAGE: Coverage = { completeness: "unknown", areas: [] };

export const COMPLETENESS_LABELS: Record<CoverageCompleteness, string> = {
  unknown: "Sense especificar",
  complete: "Completa: aquestes són totes les àrees",
  partial: "Parcial: n'hi ha més que no consten",
};

export const AREA_KIND_LABELS: Record<AreaKind, string> = {
  municipality: "Municipi",
  comarca: "Comarca",
  province: "Província",
  region: "Comunitat autònoma",
  country: "País",
  address: "Adreça",
  custom: "Altres",
};

export const AREA_KINDS = Object.keys(AREA_KIND_LABELS) as AreaKind[];

/** Una àrea tal com s'envia: espais normalitzats, codis buits com a `null`. */
export function cleanArea(area: CoverageArea): CoverageArea {
  const text = (value: string | null | undefined) => {
    const trimmed = (value ?? "").trim().replace(/\s+/g, " ");
    return trimmed === "" ? null : trimmed;
  };
  return { kind: area.kind, label: text(area.label) ?? "", code: text(area.code), code_system: text(area.code_system) };
}

/** El que fa que dues àrees siguin la mateixa: tipus, sistema de codis, codi i nom normalitzat. */
export function areaIdentity(area: CoverageArea): string {
  const clean = cleanArea(area);
  return [clean.kind, clean.code_system ?? "", clean.code ?? "", clean.label].join("\u0000");
}

/** La cobertura tal com s'envia: sense files sense nom i sense repeticions (la primera es queda). */
export function cleanCoverage(coverage: Coverage): Coverage {
  const seen = new Set<string>();
  const areas: CoverageArea[] = [];
  for (const raw of coverage.areas) {
    const area = cleanArea(raw);
    const identity = areaIdentity(area);
    if (area.label === "" || seen.has(identity)) continue;
    seen.add(identity);
    areas.push(area);
  }
  return { completeness: coverage.completeness, areas };
}

/** La mateixa cobertura, tant se val l'ordre de les àrees (el mateix criteri que l'API). */
export function sameCoverage(a: Coverage | null | undefined, b: Coverage | null | undefined): boolean {
  const key = (coverage: Coverage | null | undefined) => {
    const clean = cleanCoverage(coverage ?? NEUTRAL_COVERAGE);
    const areas = clean.areas.map(areaIdentity).sort();
    return [clean.completeness, ...areas].join("\u0001");
  };
  return key(a) === key(b);
}

export function isNeutralCoverage(coverage: Coverage | null | undefined): boolean {
  return sameCoverage(coverage, NEUTRAL_COVERAGE);
}

/** Una línia curta per a llistes: «Cobertura completa: Girona, Palamós». `null` si no diu res. */
export function coverageSummary(coverage: Coverage | null | undefined): string | null {
  if (!coverage || isNeutralCoverage(coverage)) return null;
  const head = coverage.completeness === "complete" ? "Cobertura completa" : coverage.completeness === "partial" ? "Cobertura parcial" : "Cobertura";
  const names = cleanCoverage(coverage).areas.map((area) => area.label);
  if (names.length === 0) return `${head}: cap àrea`;
  const shown = names.slice(0, 4).join(", ");
  return names.length > 4 ? `${head}: ${shown} i ${names.length - 4} més` : `${head}: ${shown}`;
}

export const SUBJECT_LABELS: Record<ObservationSubject, string> = {
  certification: "Certificacions",
  operating_fact: "Fets operatius",
  service: "Serveis",
  solution: "Solucions",
  product: "Productes",
  partner: "Partners",
};

/** El nom en singular de cada tipus, per a títols i accions («Declara un fet operatiu»). */
export const KIND_LABELS: Record<RecordKind, string> = {
  certification: "certificació",
  service: "servei",
  operating_fact: "fet operatiu",
  solution: "solució",
  solution_capability: "capacitat",
  product: "producte",
  solution_product: "producte d'una solució",
  partner: "partner",
  partner_role: "paper del partner",
};

export function serviceLineLabel(code: string | null | undefined, vocabulary?: Vocabulary | null): string {
  if (!code) return "—";
  return SERVICE_LINE_LABELS[code] ?? vocabulary?.service_lines.find((line) => line.code === code)?.label ?? code;
}

export function capabilityLabel(code: string, vocabulary?: Vocabulary | null): string {
  return CAPABILITY_LABELS[code] ?? vocabulary?.capabilities.find((item) => item.code === code)?.label ?? code;
}

export function standardLabel(code: string | null | undefined, vocabulary?: Vocabulary | null): string {
  if (!code) return "—";
  return STANDARD_LABELS[code] ?? vocabulary?.standards.find((item) => item.code === code)?.label ?? code;
}

export function levelLabel(level: string | null | undefined): string {
  if (!level) return "";
  return LEVEL_LABELS[level] ?? level;
}

/** «ENS · Mitjà», «ISO 9001» o, si no té norma, el nom que s'hi va posar. */
export function certificationTitle(
  certification: { name: string; standard_code?: string | null; level?: string | null },
  vocabulary?: Vocabulary | null,
): string {
  if (!certification.standard_code) return certification.name;
  const standard = standardLabel(certification.standard_code, vocabulary);
  return certification.level ? `${standard} · ${levelLabel(certification.level)}` : standard;
}

/** Les claus de certificació sense norma arriben com a `name:<nom>`. */
export function certificationKeyTitle(key: string, vocabulary?: Vocabulary | null): string {
  return key.startsWith("name:") ? key.slice(5) : standardLabel(key, vocabulary);
}

function trimNumber(value: string): string {
  return value.includes(".") ? value.replace(/\.?0+$/, "") : value;
}

/** «60 canals», «PoE at», o res si la capacitat és de sí o no. */
export function capabilityValue(
  record: Pick<CapabilityRecord, "capability_code" | "value_number" | "value_level">,
  vocabulary?: Vocabulary | null,
): string {
  const definition = vocabulary?.capabilities.find((item) => item.code === record.capability_code);
  if (record.value_number !== null && record.value_number !== undefined) {
    const unit = definition?.unit ? ` ${UNIT_LABELS[definition.unit] ?? definition.unit}` : "";
    return `${trimNumber(String(record.value_number))}${unit}`;
  }
  return record.value_level ?? "";
}

/* ----- Estat efectiu ------------------------------------------------------- */

export type KnowledgeState = "verified" | "declared" | "in_progress" | "expired" | "retired" | "inactive" | "historic" | "unknown";
export type KnowledgeFlag = "evidence_missing" | "to_verify" | "scope_unknown";

export interface EffectiveState {
  state: KnowledgeState;
  flags: KnowledgeFlag[];
}

export const STATE_LABELS: Record<KnowledgeState, string> = {
  verified: "Verificat",
  declared: "Declarat",
  in_progress: "En tràmit",
  expired: "Caducat",
  retired: "Retirat",
  inactive: "No compta",
  historic: "Històric detectat",
  unknown: "Desconegut",
};

export const STATE_LEVEL: Record<KnowledgeState, Level> = {
  verified: "ready",
  declared: "neutral",
  in_progress: "running",
  expired: "blocking",
  retired: "neutral",
  inactive: "neutral",
  historic: "attention",
  unknown: "neutral",
};

export const FLAG_LABELS: Record<KnowledgeFlag, string> = {
  evidence_missing: "Falta evidència",
  to_verify: "Pendent de verificar",
  scope_unknown: "Abast desconegut",
};

/** Què vol dir cada pendent, en una frase, per a la vista «Pendents de validar». */
export const FLAG_EXPLANATIONS: Record<KnowledgeFlag, string> = {
  evidence_missing: "Encara no hi ha cap document que ho acrediti.",
  to_verify: "Ja té evidència enllaçada: falta que algú la doni per bona.",
  scope_unknown: "No se sap quines línies de servei cobreix. El text de l'abast no es fa servir per deduir-ho.",
};

export interface SnapshotIndex {
  claims: Map<string, KnowledgeClaim>;
  pending: Map<string, PendingItem[]>;
}

const SECTIONS: SnapshotSection[] = ["certifications", "facts", "services", "solutions", "capabilities", "products", "partners"];

export function indexSnapshot(snapshot: KnowledgeSnapshot | null): SnapshotIndex {
  const claims = new Map<string, KnowledgeClaim>();
  const pending = new Map<string, PendingItem[]>();
  if (!snapshot) return { claims, pending };
  for (const section of SECTIONS) {
    for (const entry of snapshot[section]) {
      for (const claim of entry.claims) claims.set(claim.record_id, claim);
    }
  }
  for (const item of snapshot.pending_validation) {
    if (!item.record_id) continue;
    pending.set(item.record_id, [...(pending.get(item.record_id) ?? []), item]);
  }
  return { claims, pending };
}

/** L'estat d'una afirmació que compta. Verificat NOMÉS si la fotografia ho diu. */
export function claimState(claim: KnowledgeClaim): EffectiveState {
  const flags: KnowledgeFlag[] = [];
  if (claim.source !== "current_verified") flags.push(claim.has_evidence ? "to_verify" : "evidence_missing");
  if (claim.scope_status === "scope_unknown") flags.push("scope_unknown");
  return { state: claim.source === "current_verified" ? "verified" : "declared", flags };
}

/**
 * L'estat d'una fila concreta. Si la fotografia la compta, mana la fotografia;
 * si no, és que ha caducat, està en tràmit, s'ha retirat o penja d'una dada
 * que no compta.
 */
export function recordState(recordId: string, lifecycle: KnowledgeLifecycle, index: SnapshotIndex): EffectiveState {
  const claim = index.claims.get(recordId);
  if (claim) return claimState(claim);
  const kinds = (index.pending.get(recordId) ?? []).map((item) => item.kind);
  if (kinds.includes("expired")) return { state: "expired", flags: [] };
  if (kinds.includes("in_progress") || lifecycle === "in_progress") return { state: "in_progress", flags: [] };
  if (lifecycle === "retired") return { state: "retired", flags: [] };
  return { state: "inactive", flags: [] };
}

/** L'estat d'una clau de la fotografia: actual, només històric o desconegut. */
export function entryState(entry: KnowledgeEntry | undefined): EffectiveState {
  if (!entry) return { state: "unknown", flags: [] };
  if (entry.claims.length > 0) return claimState(entry.claims[0]);
  return { state: entry.hints.length > 0 ? "historic" : "unknown", flags: [] };
}

export function findEntry(snapshot: KnowledgeSnapshot | null, section: SnapshotSection, key: string): KnowledgeEntry | undefined {
  return snapshot?.[section].find((entry) => entry.key === key);
}

/* ----- Pendents de validar -------------------------------------------------- */

/** Els papers d'un partner i els productes d'una solució no tenen pantalla pròpia: no es llisten sols. */
const NESTED_SUBJECTS = new Set(["partner_role", "solution_product"]);

export interface PendingGroup {
  id: string;
  subject: RecordKind;
  key: string;
  recordId: string;
  kinds: PendingKind[];
}

/** Un bloc per dada: «ENS: falta evidència i abast desconegut», no dues files. */
export function groupPendingRecords(snapshot: KnowledgeSnapshot | null): PendingGroup[] {
  const groups = new Map<string, PendingGroup>();
  for (const item of snapshot?.pending_validation ?? []) {
    if (item.kind === "historic_candidate" || !item.record_id || NESTED_SUBJECTS.has(item.subject)) continue;
    const id = `${item.subject}:${item.record_id}`;
    const group = groups.get(id) ?? { id, subject: item.subject as RecordKind, key: item.key, recordId: item.record_id, kinds: [] };
    group.kinds.push(item.kind);
    groups.set(id, group);
  }
  return [...groups.values()];
}

export function pendingCandidates(snapshot: KnowledgeSnapshot | null): PendingItem[] {
  return (snapshot?.pending_validation ?? []).filter((item) => item.kind === "historic_candidate");
}

export interface KnowledgeSummary {
  verified: number;
  declared: number;
  pending: number;
  historic: number;
  unknown: number;
}

/**
 * El resum de dalt. «Desconegut» només compta el que té una llista tancada
 * (normes, fets operatius i línies de servei): del que és obert (solucions,
 * productes, partners) no es pot dir què falta.
 */
export function summarize(snapshot: KnowledgeSnapshot | null, vocabulary: Vocabulary | null): KnowledgeSummary {
  const summary: KnowledgeSummary = { verified: 0, declared: 0, pending: 0, historic: 0, unknown: 0 };
  if (!snapshot) return summary;
  for (const section of SECTIONS) {
    for (const entry of snapshot[section]) {
      for (const claim of entry.claims) {
        if (claim.source === "current_verified") summary.verified += 1;
        else summary.declared += 1;
      }
    }
  }
  summary.pending = groupPendingRecords(snapshot).length;
  summary.historic = pendingCandidates(snapshot).length;
  const closed: Array<[SnapshotSection, string[]]> = [
    ["certifications", vocabulary?.standards.map((item) => item.code) ?? []],
    ["facts", [...FACT_KEYS]],
    ["services", vocabulary?.service_lines.map((item) => item.code) ?? []],
  ];
  for (const [section, keys] of closed) {
    summary.unknown += keys.filter((key) => !findEntry(snapshot, section, key)).length;
  }
  return summary;
}

/* ----- Candidats de l'històric ---------------------------------------------- */

export function candidateTitle(subject: string, key: string, vocabulary?: Vocabulary | null): string {
  if (subject === "operating_fact") return FACT_LABELS[key] ?? key;
  if (subject === "certification") return certificationKeyTitle(key, vocabulary);
  if (subject === "service") return serviceLineLabel(key, vocabulary);
  return key;
}

/** «No tenien ENS» no es pot confirmar com a actual: només es pot descartar. */
export function isPromotable(observation: Pick<Observation, "subject" | "proposal">): boolean {
  return !(observation.subject === "certification" && observation.proposal.held === false);
}

/** L'ordre de les categories a tota l'àrea: el mateix a «actual», «pendents» i «històric». */
export const SUBJECT_ORDER: ObservationSubject[] = ["certification", "service", "operating_fact", "solution", "product", "partner"];

export interface CandidateGroup {
  key: string;
  label: string;
  items: Observation[];
}

/**
 * Els candidats per categoria. Amb `dismissOnlyLast`, els que no es poden
 * confirmar (una observació negativa, o una clau que ja té dada actual) van
 * plegats al final: primer la feina que fa avançar el coneixement.
 */
export function groupCandidates(observations: Observation[], superseded: Set<string>, dismissOnlyLast = false): CandidateGroup[] {
  const confirmable = (item: Observation) => isPromotable(item) && !superseded.has(item.id);
  const main = dismissOnlyLast ? observations.filter(confirmable) : observations;
  const groups: CandidateGroup[] = SUBJECT_ORDER.map((subject) => ({
    key: subject,
    label: SUBJECT_LABELS[subject],
    items: main.filter((item) => item.subject === subject),
  }));
  if (dismissOnlyLast) {
    groups.push({ key: "dismiss_only", label: "Només es poden descartar", items: observations.filter((item) => !confirmable(item)) });
  }
  return groups.filter((group) => group.items.length > 0);
}

/** Un camp de la dada que es crearia: «Línia de servei», «Centraleta i veu IP». */
export interface ProposalField {
  label: string;
  value: string;
}

/**
 * La proposta d'una observació, tal com es desaria si es confirma. Surt només
 * de la `proposal` estructurada —la fila exacta que crearia el servidor—, mai
 * del text de l'històric, que és una cita i pot ser ambigu sobre si diu que sí
 * o que no.
 */
export interface ProposalDescription {
  /** De què és la dada: el fet o la norma, o el tipus («Solució», «Partner»…). */
  label: string;
  /** El que es desaria: «SÍ», «NO» o el nom. */
  value: string;
  /** Una proposta en negatiu: confirmar-la és deixar escrit un NO. */
  negative: boolean;
  /** La resta de camps que es crearien amb la dada. */
  fields: ProposalField[];
  /** Tot plegat en una línia, per a la fila del candidat. */
  sentence: string;
}

/** Com es diu cada fet operatiu quan es té i quan no. Sense frase, es mostra «Fet: SÍ» o «Fet: NO». */
const FACT_SENTENCES: Record<string, { yes: string; no: string }> = {
  telecom_operator_cnmc: { yes: "És operador inscrit a la CNMC", no: "No és operador inscrit a la CNMC" },
  own_number_range_nrn: { yes: "Disposa de rang de numeració propi (NRN)", no: "No disposa de rang de numeració propi (NRN)" },
  datacenter_tier3: { yes: "Disposa de centre de dades Tier-3", no: "No disposa de centre de dades Tier-3" },
  support_24x7: { yes: "Ofereix suport 24x7", no: "No ofereix suport 24x7" },
  own_ftth_infrastructure: { yes: "Disposa d'infraestructura FTTH pròpia", no: "No disposa d'infraestructura FTTH pròpia" },
};

/** «Subcontractista · Obra civil · Girona»: el paper, la línia i el territori, si n'hi ha. */
function partnerRoleText(role: Record<string, unknown>, vocabulary?: Vocabulary | null): string {
  return [
    PARTNER_ROLE_LABELS[String(role.role)] ?? String(role.role),
    typeof role.service_line_code === "string" ? serviceLineLabel(role.service_line_code, vocabulary) : null,
    typeof role.territory === "string" ? role.territory : null,
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Què naixeria si es confirma: la persona ho ha de veure abans de dir que sí. */
export function describeProposal(observation: Pick<Observation, "subject" | "proposal">, vocabulary?: Vocabulary | null): ProposalDescription {
  const proposal = observation.proposal;
  const text = (name: string) => (typeof proposal[name] === "string" ? (proposal[name] as string) : "");
  const line = text("service_line_code") ? serviceLineLabel(text("service_line_code"), vocabulary) : "";
  const lineField: ProposalField[] = line ? [{ label: "Línia de servei", value: line }] : [];
  const named = (label: string, value: string, fields: ProposalField[], sentence: string): ProposalDescription => ({
    label,
    value,
    negative: false,
    fields,
    sentence,
  });

  switch (observation.subject) {
    case "operating_fact": {
      const key = text("key");
      const label = FACT_LABELS[key] ?? key;
      const negative = proposal.value === false;
      const value = negative ? "NO" : proposal.value === true ? "SÍ" : "—";
      const sentences = FACT_SENTENCES[key];
      return {
        label,
        value,
        negative,
        fields: text("detail") ? [{ label: "Aclariment", value: text("detail") }] : [],
        sentence: sentences && value !== "—" ? (negative ? sentences.no : sentences.yes) : `${label}: ${value}`,
      };
    }
    case "certification": {
      const title = certificationTitle({ name: text("name"), standard_code: text("standard_code"), level: text("level") || null }, vocabulary);
      if (proposal.held === false) {
        return { label: title, value: "NO", negative: true, fields: [], sentence: `No disposava de la certificació ${title}` };
      }
      const scope = Array.isArray(proposal.scope_service_lines)
        ? (proposal.scope_service_lines as string[]).map((code) => serviceLineLabel(code, vocabulary)).join(", ")
        : "Sense estructurar";
      return named("Certificació", title, [{ label: "Abast", value: scope }], `Disposava de la certificació ${title}`);
    }
    case "service": {
      const mode = text("delivery_mode") ? (DELIVERY_MODE_LABELS[text("delivery_mode")] ?? text("delivery_mode")) : "";
      return named(
        "Servei",
        text("name"),
        [...lineField, ...(mode ? [{ label: "Modalitat", value: mode }] : [])],
        [`Servei «${text("name")}»`, line, mode].filter(Boolean).join(" · "),
      );
    }
    case "solution":
      return named(
        "Solució",
        text("name"),
        [
          ...lineField,
          ...(text("vendor") ? [{ label: "Fabricant", value: text("vendor") }] : []),
          ...(text("edition") ? [{ label: "Edició", value: text("edition") }] : []),
        ],
        [`Solució ${text("name")}`, line, text("vendor") && `fabricant ${text("vendor")}`, text("edition") && `edició ${text("edition")}`]
          .filter(Boolean)
          .join(" · "),
      );
    case "product": {
      const name = `${text("manufacturer")} ${text("model")}`.trim();
      return named(
        "Producte",
        name,
        [{ label: "Fabricant", value: text("manufacturer") }, { label: "Model", value: text("model") }, ...lineField],
        [`Producte ${name}`, line].filter(Boolean).join(" · "),
      );
    }
    case "partner": {
      const roles = (Array.isArray(proposal.roles) ? (proposal.roles as Array<Record<string, unknown>>) : []).map((role) =>
        partnerRoleText(role, vocabulary),
      );
      return named(
        "Partner",
        text("name"),
        roles.map((role) => ({ label: "Paper", value: role })),
        roles.length > 0 ? `Partner ${text("name")}: ${roles.join("; ")}` : `Partner ${text("name")}`,
      );
    }
    default:
      return named("", "", [], "");
  }
}

/** «Infraestructura FTTH pròpia: NO», «Solució: 3CX»: la dada que es desaria, en una línia. */
export function proposalHeadline(description: Pick<ProposalDescription, "label" | "value">): string {
  return `${description.label}: ${description.value}`;
}

/** Tot el text visible d'un candidat de l'històric: on busca el cercador de la pantalla. */
export function candidateSearchText(observation: Observation, vocabulary?: Vocabulary | null): string[] {
  return [
    candidateTitle(observation.subject, observation.subject_key, vocabulary),
    describeProposal(observation, vocabulary).sentence,
    observation.statement,
    observation.source_ref ?? "",
  ];
}

/* ----- Evidència i rols ------------------------------------------------------ */

export interface EvidenceTarget {
  entityType: LinkedEntityType;
  relationship: RelationshipType;
  category: DocumentCategory;
}

/** On s'enllaça l'evidència de cada tipus de dada (Documents + DocumentLink). */
export const EVIDENCE_TARGETS: Partial<Record<RecordKind, EvidenceTarget>> = {
  certification: { entityType: "company_certification", relationship: "certificate", category: "certification" },
  service: { entityType: "company_service", relationship: "supporting_document", category: "commercial" },
  operating_fact: { entityType: "company_operating_fact", relationship: "accreditation", category: "legal" },
  solution: { entityType: "company_solution", relationship: "technical_sheet", category: "technical" },
  solution_capability: { entityType: "solution_capability", relationship: "technical_sheet", category: "technical" },
  product: { entityType: "company_product", relationship: "technical_sheet", category: "technical" },
  partner: { entityType: "company_partner", relationship: "contract", category: "legal" },
};

const EDIT_ROLES = new Set(["owner", "admin", "bid_manager", "technical"]);
const DELETE_ROLES = new Set(["owner", "admin"]);
const DECIDE_ROLES = new Set(["owner", "admin", "bid_manager"]);
const VERIFY_ROLES = new Set(["owner", "admin"]);

/** Declarar, editar, retirar i tocar l'evidència: tothom menys qui només consulta. */
export function canEditKnowledge(role: string | null | undefined): boolean {
  return Boolean(role && EDIT_ROLES.has(role));
}

/** Eliminar definitivament una dada. */
export function canDeleteKnowledge(role: string | null | undefined): boolean {
  return Boolean(role && DELETE_ROLES.has(role));
}

/** Confirmar o descartar el que diu l'històric (`DECIDE_ROLES` de l'API). */
export function canDecideKnowledge(role: string | null | undefined): boolean {
  return Boolean(role && DECIDE_ROLES.has(role));
}

/** Donar per bona una evidència (`VERIFY_ROLES` de l'API). */
export function canVerifyKnowledge(role: string | null | undefined): boolean {
  return Boolean(role && VERIFY_ROLES.has(role));
}

export const WHO_CAN_EDIT_KNOWLEDGE = "qui té el rol de Propietari/ària, Administrador/a, Responsable de candidatures o Tècnic/a";
export const WHO_CAN_DELETE_KNOWLEDGE = "qui té el rol de Propietari/ària o Administrador/a";
export const WHO_CAN_DECIDE_KNOWLEDGE = "qui té el rol de Propietari/ària, Administrador/a o Responsable de candidatures";
export const WHO_CAN_VERIFY_KNOWLEDGE = "qui té el rol de Propietari/ària o Administrador/a";
