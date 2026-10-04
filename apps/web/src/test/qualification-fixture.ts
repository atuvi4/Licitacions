import type { EquipmentRecord, KnowledgeEntry, KnowledgeSnapshot, OperatingFactRecord } from "@/lib/knowledge";
import type { FinancialYear, Insurance, InsuranceLimit, Person, Project, Registration, Service } from "@/lib/types";

import { claimFixture, emptySnapshotFixture, vocabularyFixture } from "./knowledge-fixture";

/**
 * «Qualificació i mitjans» d'una empresa INVENTADA. Cap import, persona, pòlissa,
 * registre, número de sèrie ni recompte d'adreces és real: són xifres rodones
 * de prova.
 */

const audit = { created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-01T00:00:00Z", created_by: null, updated_by: null };
const origin = { knowledge_origin: "declared" as const, verification_status: "pending_verification" as const, lifecycle: "current" as const };

export const qualificationVocabularyFixture = {
  ...vocabularyFixture,
  professional_profiles: [
    { code: "project_manager", label: "Cap de projecte" },
    { code: "network_engineer", label: "Enginyer de xarxa" },
    { code: "field_technician", label: "Tècnic de camp" },
  ],
  equipment_types: [
    { code: "otdr", label: "Reflectòmetre òptic (OTDR)" },
    { code: "fusion_splicer", label: "Fusionadora de fibra" },
    { code: "other", label: "Altres" },
  ],
};

export const financialYearsFixture: FinancialYear[] = [
  { id: "fy-2024", ...audit, ...origin, year: 2024, revenue: "100000.00", currency: "EUR", notes: null, as_of: null },
  { id: "fy-2025", ...audit, ...origin, year: 2025, revenue: "200000.00", currency: "EUR", notes: null, as_of: "2026-03-31" },
];

export const servicesFixture: Service[] = [
  { id: "svc-ftth", ...audit, company_id: "c1", name: "Desplegament de fibra", category: null, description: null, specialization_level: null, active: true, notes: null, service_line_code: "access_ftth" },
  { id: "svc-pbx", ...audit, company_id: "c1", name: "Centraleta gestionada", category: null, description: null, specialization_level: null, active: true, notes: null, service_line_code: "voice_pbx" },
];

export function projectFixture(overrides: Partial<Project> = {}): Project {
  return {
    id: "prj-1",
    ...audit,
    ...origin,
    company_id: "c1",
    name: "Manteniment de xarxa de prova",
    client_name: "Client Exemple",
    client_type: "public",
    sector: null,
    description: "Contracte sintètic per a proves",
    amount: "90000.00",
    currency: "EUR",
    start_date: "2024-01-01",
    end_date: "2026-12-31",
    location: null,
    status: "in_progress",
    reference_contact: null,
    notes: null,
    // Legacy: el servei del catàleg no és el que llegeix la qualificació.
    services: [{ id: "svc-ftth", name: "Desplegament de fibra", category: null }],
    service_line_codes: ["access_ftth"],
    cpvs: [{ cpv_code: "64200000", description: "Serveis de telecomunicacions" }],
    annual_amounts: [
      { year: 2024, amount: "30000.00" },
      { year: 2025, amount: "20000.00" },
    ],
    as_of: null,
    ...overrides,
  } as Project;
}

export const insurancesFixture: Insurance[] = [
  {
    id: "ins-rc",
    ...audit,
    ...origin,
    type: "civil_liability",
    provider: "Asseguradora Exemple",
    policy_number: null,
    coverage_description: null,
    coverage_amount: null,
    currency: "EUR",
    starts_at: "2026-01-01",
    expires_at: "2026-12-31",
    temporal_status: "valid",
    additional_insured: null,
    notes: null,
    as_of: null,
    limits: [],
  } as Insurance,
];

export const limitsFixture: InsuranceLimit[] = [
  { id: "lim-total", ...audit, insurance_id: "ins-rc", kind: "total", amount: "600000.00", currency: "EUR", notes: null },
  { id: "lim-emp", ...audit, insurance_id: "ins-rc", kind: "employer_per_victim", amount: "150000.00", currency: "EUR", notes: null },
];

export const registrationsFixture: Registration[] = [
  {
    id: "reg-1",
    ...audit,
    ...origin,
    name: "Registre sectorial de prova",
    type: "sector_operator_registry",
    registration_number: "REG-TEST-001",
    issuer: "Organisme Exemple",
    issued_at: "2020-05-01",
    expires_at: null,
    temporal_status: "valid",
    notes: null,
    scope_service_lines: null,
    as_of: null,
  } as Registration,
];

export const peopleFixture: Person[] = [
  {
    id: "person-1",
    ...audit,
    ...origin,
    first_name: "Persona",
    last_name: "Prova",
    role: "Coordinació tècnica",
    department: null,
    professional_profile: null,
    years_experience: null,
    availability_status: "unknown",
    notes: null,
    profile_codes: ["project_manager"],
    as_of: null,
  } as Person,
];

const provenance = { knowledge_origin: "declared" as const, as_of: "2026-09-29", valid_until: null, lifecycle: "current" as const };

export const equipmentFixture: EquipmentRecord[] = [
  {
    id: "eq-1",
    ...provenance,
    equipment_type: "otdr",
    manufacturer: "Fabricant Demo",
    model: "Model X",
    serial_reference: "SN-TEST-0001",
    access_mode: "owned",
    quantity: 1,
    calibration_required: true,
    calibration_valid_until: "2027-01-15",
    notes: null,
  },
];

export const ipv4FactFixture: OperatingFactRecord = {
  id: "fact-ipv4",
  key: "ipv4_assignable_count",
  value: true,
  value_number: "256",
  detail: null,
  ...provenance,
} as OperatingFactRecord;

function entry(key: string, claims: KnowledgeEntry["claims"]): KnowledgeEntry {
  return { key, source: claims[0]?.source === "current_verified" ? "current_verified" : "current_declared", accreditation_pending: true, claims, hints: [] };
}

/** La fotografia d'aquestes fitxes: l'exercici 2025 verificat, la resta declarat. */
export function qualificationSnapshotFixture(): KnowledgeSnapshot {
  return {
    ...emptySnapshotFixture(),
    financial_years: [
      entry("2024", [claimFixture({ record_id: "fy-2024" })]),
      entry("2025", [claimFixture({ record_id: "fy-2025", source: "current_verified", accreditation_pending: false, has_evidence: true })]),
    ],
    execution_references: [entry("prj-1", [claimFixture({ record_id: "prj-1", value: { name: "Manteniment de xarxa de prova" }, has_evidence: true })])],
    insurances: [entry("civil_liability", [claimFixture({ record_id: "ins-rc" })])],
    registrations: [entry("sector_operator_registry", [claimFixture({ record_id: "reg-1", scope_status: "scope_unknown" })])],
    profiles: [entry("project_manager", [claimFixture({ record_id: "person-1" })])],
    equipment: [entry("otdr", [claimFixture({ record_id: "eq-1" })])],
    facts: [entry("ipv4_assignable_count", [claimFixture({ record_id: "fact-ipv4", value: true })])],
  };
}

// La pòlissa porta els seus límits (es llegeixen i es desen amb la fitxa).
insurancesFixture[0].limits = limitsFixture;
