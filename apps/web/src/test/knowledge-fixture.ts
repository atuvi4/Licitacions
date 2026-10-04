import type {
  KnowledgeClaim,
  KnowledgeRecords,
  KnowledgeSnapshot,
  Observation,
  PendingItem,
  Vocabulary,
} from "@/lib/knowledge";
import type { Certification } from "@/lib/types";

/** Dades sintètiques del coneixement d'una empresa. Cap dada real. */

export const vocabularyFixture: Vocabulary = {
  service_lines: [
    { code: "voice_pbx", label: "Centraleta i veu IP" },
    { code: "civil_works", label: "Obra civil" },
    { code: "access_ftth", label: "Accessos FTTH" },
  ],
  capabilities: [
    { code: "pbx.concurrent_calls", service_line_code: "voice_pbx", label: "Trucades simultànies", value_type: "number", comparator: "gte", unit: "canals", ordinal_scale: null },
    { code: "pbx.queues", service_line_code: "voice_pbx", label: "Cues de trucades", value_type: "boolean", comparator: "is_true", unit: null, ordinal_scale: null },
    { code: "switch.poe_standard", service_line_code: "network_switching", label: "Estàndard PoE", value_type: "ordinal", comparator: "gte", unit: null, ordinal_scale: ["af", "at", "bt"] },
  ],
  standards: [
    { code: "ENS", label: "Esquema Nacional de Seguretat", levels: ["basic", "medium", "high"] },
    { code: "ISO_9001", label: "ISO 9001 (gestió de la qualitat)", levels: null },
  ],
};

/**
 * El vocabulari tal com el retorna l'API des del copy v3: els rètols porten accents
 * (`apps/api/app/knowledge/vocabulary.py`). A més, una línia, una capacitat i una norma
 * que la web no coneix: per a aquestes, la pantalla mostra el rètol de l'API tal com és.
 */
export const accentedVocabularyFixture: Vocabulary = {
  service_lines: [
    { code: "voice_pbx", label: "Centraleta i veu IP" },
    { code: "voice_trunk", label: "Troncals de veu i numeració" },
    { code: "access_ftth", label: "Accessos FTTH" },
    { code: "dedicated_network", label: "Xarxa dedicada entre seus" },
    { code: "radio_link", label: "Radioenllaços" },
    { code: "network_switching", label: "Electrònica de xarxa" },
    { code: "fiber_deployment", label: "Desplegament i certificació de fibra" },
    { code: "civil_works", label: "Obra civil" },
    { code: "support_ops", label: "Suport, operació i nivells de servei" },
    { code: "mobile_telephony", label: "Telefonia mòbil" },
    { code: "satellite_backhaul", label: "Retorn per satèl·lit" },
  ],
  capabilities: [
    { code: "pbx.concurrent_calls", service_line_code: "voice_pbx", label: "Trucades simultànies", value_type: "number", comparator: "gte", unit: "canals", ordinal_scale: null },
    { code: "pbx.blf", service_line_code: "voice_pbx", label: "Estat de línia (BLF)", value_type: "boolean", comparator: "is_true", unit: null, ordinal_scale: null },
    { code: "pbx.voicemail_email", service_line_code: "voice_pbx", label: "Bústia amb avís per correu", value_type: "boolean", comparator: "is_true", unit: null, ordinal_scale: null },
    { code: "switch.poe_standard", service_line_code: "network_switching", label: "Estàndard PoE", value_type: "ordinal", comparator: "gte", unit: null, ordinal_scale: ["af", "at", "bt"] },
    { code: "pbx.priority_transfer", service_line_code: "voice_pbx", label: "Transferència prioritària", value_type: "boolean", comparator: "is_true", unit: null, ordinal_scale: null },
  ],
  standards: [
    { code: "ENS", label: "Esquema Nacional de Seguretat", levels: ["basic", "medium", "high"] },
    { code: "ISO_9001", label: "ISO 9001 (gestió de la qualitat)", levels: null },
    { code: "ISO_14001", label: "ISO 14001 (gestió ambiental)", levels: null },
    { code: "ISO_27001", label: "ISO/IEC 27001 (seguretat de la informació)", levels: null },
    { code: "ISO_45001", label: "ISO 45001 (seguretat i salut a la feina)", levels: null },
  ],
};

/** Formes sense accent que la v3 ha corregit: cap pantalla no les ha de mostrar. */
export const UNACCENTED_LABELS = /numeracio\b|Radioenllacos|Electronica de xarxa|certificacio de fibra|operacio i nivells|Telefonia mobil\b|simultanies|Estat de linia|Bustia amb avis|Estandard PoE|gestio de la qualitat|gestio ambiental|informacio\)|satel·lit|Transferencia prioritaria/;

export function claimFixture(overrides: Partial<KnowledgeClaim> = {}): KnowledgeClaim {
  return {
    record_id: "r1",
    source: "current_declared",
    value: null,
    as_of: "2026-09-29",
    valid_until: null,
    accreditation_pending: true,
    has_evidence: false,
    scope_service_lines: null,
    scope_status: null,
    ...overrides,
  };
}

function pending(overrides: Partial<PendingItem>): PendingItem {
  return { kind: "accreditation_pending", subject: "certification", key: "ENS", detail: "", record_id: null, observation_id: null, superseded_by_current: false, ...overrides };
}

/**
 * Una empresa a mig validar: ENS declarat sense evidència ni abast, un fet
 * verificat, un partner amb evidència pendent de verificar, i quatre
 * suggeriments de l'històric (un de superat i un de negatiu).
 */
export function snapshotFixture(): KnowledgeSnapshot {
  return {
    company_id: "c1",
    evaluated_on: "2026-09-30",
    fingerprint: "f".repeat(64),
    certifications: [
      {
        key: "ENS",
        source: "current_declared",
        accreditation_pending: true,
        claims: [claimFixture({ record_id: "cert-ens", value: "medium", scope_status: "scope_unknown" })],
        hints: [{ observation_id: "o-ens", statement: "Es va descartar una licitació perquè no tenien ENS", observed_on: "2025-06-01", source_ref: "Ajuntament de Mostra" }],
      },
      {
        key: "ISO_9001",
        source: "historic",
        accreditation_pending: false,
        claims: [],
        hints: [{ observation_id: "o-iso", statement: "No en tenien: es va descartar una licitació", observed_on: null, source_ref: null }],
      },
    ],
    facts: [
      {
        key: "datacenter_tier3",
        source: "current_verified",
        accreditation_pending: false,
        claims: [claimFixture({ record_id: "fact-tier3", source: "current_verified", accreditation_pending: false, has_evidence: true, value: true })],
        hints: [],
      },
      {
        key: "support_24x7",
        source: "historic",
        accreditation_pending: false,
        claims: [],
        hints: [{ observation_id: "o-24x7", statement: "Declaraven suport 24x7 amb resposta en menys de 2 h", observed_on: null, source_ref: "Licitació de mostra" }],
      },
    ],
    services: [],
    solutions: [
      {
        key: "Centraleta Demo",
        source: "historic",
        accreditation_pending: false,
        claims: [],
        hints: [{ observation_id: "o-sol", statement: "Van oferir la Centraleta Demo allotjada", observed_on: null, source_ref: null }],
      },
    ],
    capabilities: [],
    products: [],
    partners: [
      {
        key: "Soci Demo",
        source: "current_declared",
        accreditation_pending: true,
        claims: [claimFixture({ record_id: "partner-1", has_evidence: true, value: { roles: [{ role: "subcontractor", line: "civil_works", territory: "Girona" }] } })],
        hints: [],
      },
    ],
    pending_validation: [
      pending({ kind: "accreditation_pending", record_id: "cert-ens", detail: "Marcat verificat pero sense cap evidencia enllacada" }),
      pending({ kind: "accreditation_pending", subject: "partner", key: "Soci Demo", record_id: "partner-1", detail: "Amb evidencia enllacada, pendent de verificar" }),
      pending({ kind: "accreditation_pending", subject: "partner_role", key: "Soci Demo / subcontractor", record_id: "role-1" }),
      pending({ kind: "scope_unknown", record_id: "cert-ens", detail: "Abast sense estructurar" }),
      pending({ kind: "historic_candidate", observation_id: "o-ens", superseded_by_current: true }),
      pending({ kind: "historic_candidate", key: "ISO_9001", observation_id: "o-iso" }),
      pending({ kind: "historic_candidate", subject: "operating_fact", key: "support_24x7", observation_id: "o-24x7" }),
      pending({ kind: "historic_candidate", subject: "solution", key: "Centraleta Demo", observation_id: "o-sol" }),
    ],
  };
}

export function emptySnapshotFixture(): KnowledgeSnapshot {
  return { ...snapshotFixture(), certifications: [], facts: [], solutions: [], partners: [], pending_validation: [] };
}

export const observationsFixture: Observation[] = [
  { id: "o-ens", subject: "certification", subject_key: "ENS", statement: "Es va descartar una licitació perquè no tenien ENS", proposal: { standard_code: "ENS", held: false }, observed_on: "2025-06-01", source_ref: "Ajuntament de Mostra", status: "candidate" },
  { id: "o-iso", subject: "certification", subject_key: "ISO_9001", statement: "No en tenien: es va descartar una licitació", proposal: { standard_code: "ISO_9001", held: false }, observed_on: null, source_ref: null, status: "candidate" },
  { id: "o-24x7", subject: "operating_fact", subject_key: "support_24x7", statement: "Declaraven suport 24x7 amb resposta en menys de 2 h", proposal: { key: "support_24x7", value: true }, observed_on: null, source_ref: "Licitació de mostra", status: "candidate" },
  { id: "o-sol", subject: "solution", subject_key: "Centraleta Demo", statement: "Van oferir la Centraleta Demo allotjada", proposal: { name: "Centraleta Demo", service_line_code: "voice_pbx", vendor: "Fabricant Demo" }, observed_on: null, source_ref: null, status: "candidate" },
];

const provenance = { knowledge_origin: "declared" as const, as_of: "2026-09-29", valid_until: null, lifecycle: "current" as const };

export function recordsFixture(): KnowledgeRecords {
  return {
    operating_facts: [{ id: "fact-tier3", key: "datacenter_tier3", value: true, detail: "CPD en un centre Tier-3", ...provenance }],
    solutions: [],
    solution_capabilities: [],
    products: [],
    solution_products: [],
    partners: [{ id: "partner-1", name: "Soci Demo", tax_id: null, ...provenance }],
    partner_roles: [{ id: "role-1", partner_id: "partner-1", role: "subcontractor", service_line_code: "civil_works", territory: "Girona", ...provenance }],
  };
}

/** La fila diu `verified`, però no té cap document: la fotografia la dona per declarada. */
export const legacyCertificationFixture: Certification = {
  id: "cert-ens",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
  created_by: null,
  updated_by: null,
  name: "ENS categoria mitjana",
  issuer: null,
  certificate_number: null,
  scope: "Cobreix la centraleta i la xarxa dedicada",
  issued_at: null,
  expires_at: null,
  temporal_status: "unknown",
  verification_status: "verified",
  notes: null,
  standard_code: "ENS",
  level: "medium",
  scope_service_lines: null,
  lifecycle: "current",
};
