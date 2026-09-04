/** Tipus que reflecteixen els esquemes de l'API (app/schemas). */

export type TemporalStatus = "valid" | "expiring_soon" | "expired" | "unknown";
export type VerificationStatus = "pending_verification" | "verified";
export type SpecializationLevel = "basic" | "intermediate" | "advanced" | "expert";
export type TerritoryType =
  | "municipality"
  | "comarca"
  | "province"
  | "autonomous_region"
  | "country"
  | "nuts"
  | "other";
export type ClientType = "public" | "private" | "mixed" | "other";
export type RegistrationType =
  | "rolece"
  | "reli"
  | "business_classification"
  | "professional_registry"
  | "administrative_accreditation"
  | "other";
export type InsuranceType =
  | "civil_liability"
  | "professional_indemnity"
  | "workers_compensation"
  | "surety_bond"
  | "all_risk"
  | "other";
export type AvailabilityStatus =
  | "available"
  | "partially_available"
  | "unavailable"
  | "unknown";
export type QualificationType =
  | "degree"
  | "professional_certification"
  | "training"
  | "authorization"
  | "other";
export type ProjectStatus = "planned" | "in_progress" | "completed" | "cancelled";
export type DocumentCategory =
  | "certification"
  | "insurance"
  | "registration"
  | "project_evidence"
  | "financial"
  | "legal"
  | "cv"
  | "technical"
  | "commercial"
  | "other";
export type LinkedEntityType =
  | "company"
  | "company_service"
  | "company_financial_year"
  | "company_certification"
  | "company_registration"
  | "company_insurance"
  | "company_person"
  | "person_qualification"
  | "company_project";
export type RelationshipType =
  | "certificate"
  | "certificate_of_good_execution"
  | "policy"
  | "contract"
  | "invoice"
  | "financial_statement"
  | "cv"
  | "diploma"
  | "accreditation"
  | "technical_sheet"
  | "reference_letter"
  | "supporting_document"
  | "other";

export interface Audited {
  id: string;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
}

export interface ClientConfig {
  max_upload_size_mb: number;
  allowed_mime_types: string[];
  expiry_warning_days: number;
  /** Entorn del backend. No és un secret: el fan servir les eines externes
   *  (smoke tests) per negar-se a mutar dades en producció. */
  environment: string;
}

export interface CompanySummary {
  id: string;
  legal_name: string;
  trade_name: string | null;
}

export interface Membership {
  id: string;
  company_id: string;
  role: string;
  is_default: boolean;
  company: CompanySummary;
}

export interface CurrentUser {
  id: string;
  email: string;
  full_name: string | null;
  is_active: boolean;
  last_login_at: string | null;
  memberships: Membership[];
}

export interface Company extends Audited {
  legal_name: string;
  trade_name: string | null;
  tax_id: string | null;
  legal_form: string | null;
  incorporation_date: string | null;
  website: string | null;
  email: string | null;
  phone: string | null;
  country: string | null;
  autonomous_region: string | null;
  province: string | null;
  city: string | null;
  postal_code: string | null;
  address: string | null;
  description: string | null;
  employee_count: number | null;
}

export interface Service extends Audited {
  company_id: string;
  name: string;
  category: string | null;
  description: string | null;
  specialization_level: SpecializationLevel | null;
  active: boolean;
  notes: string | null;
}

export interface Territory extends Audited {
  name: string;
  territory_type: TerritoryType;
  code: string | null;
  priority: number;
  willing_to_work: boolean;
  notes: string | null;
}

export interface FinancialYear extends Audited {
  year: number;
  revenue: string | null;
  currency: string;
  notes: string | null;
}

export interface ContractPreferences extends Audited {
  company_id: string;
  minimum_interesting_contract: string | null;
  preferred_contract_min: string | null;
  preferred_contract_max: string | null;
  maximum_recommended_contract: string | null;
  maximum_assumable_contract: string | null;
  currency: string;
  notes: string | null;
}

export interface Certification extends Audited {
  name: string;
  issuer: string | null;
  certificate_number: string | null;
  scope: string | null;
  issued_at: string | null;
  expires_at: string | null;
  temporal_status: TemporalStatus;
  verification_status: VerificationStatus;
  notes: string | null;
}

export interface CPVPreference {
  id: string;
  company_id: string;
  cpv_code: string;
  description: string | null;
  priority: number;
  active: boolean;
  excluded: boolean;
  notes: string | null;
}

export interface ProjectCPV {
  cpv_code: string;
  description: string | null;
}

export interface Registration extends Audited {
  name: string;
  type: RegistrationType;
  registration_number: string | null;
  issuer: string | null;
  issued_at: string | null;
  expires_at: string | null;
  temporal_status: TemporalStatus;
  notes: string | null;
}

export interface Insurance extends Audited {
  type: InsuranceType;
  provider: string | null;
  policy_number: string | null;
  coverage_description: string | null;
  coverage_amount: string | null;
  currency: string;
  starts_at: string | null;
  expires_at: string | null;
  temporal_status: TemporalStatus;
  notes: string | null;
}

export interface Person extends Audited {
  first_name: string;
  last_name: string | null;
  role: string | null;
  department: string | null;
  professional_profile: string | null;
  years_experience: number | null;
  availability_status: AvailabilityStatus;
  notes: string | null;
}

export interface Qualification extends Audited {
  person_id: string;
  type: QualificationType;
  name: string;
  issuer: string | null;
  credential_number: string | null;
  issued_at: string | null;
  expires_at: string | null;
  temporal_status: TemporalStatus;
  notes: string | null;
}

export interface ProjectServiceRef {
  id: string;
  name: string;
  category: string | null;
}

export interface Project extends Audited {
  company_id: string;
  name: string;
  client_name: string | null;
  client_type: ClientType;
  sector: string | null;
  description: string | null;
  amount: string | null;
  currency: string;
  start_date: string | null;
  end_date: string | null;
  location: string | null;
  status: ProjectStatus;
  reference_contact: string | null;
  notes: string | null;
  services: ProjectServiceRef[];
  cpvs: ProjectCPV[];
}

export interface DocumentLink {
  id: string;
  document_id: string;
  entity_type: LinkedEntityType;
  entity_id: string;
  relationship_type: RelationshipType;
  notes: string | null;
  created_at: string;
}

export interface DocumentItem extends Audited {
  company_id: string;
  name: string;
  original_filename: string;
  mime_type: string;
  file_size: number;
  sha256: string;
  category: DocumentCategory;
  description: string | null;
  issued_at: string | null;
  expires_at: string | null;
  temporal_status: TemporalStatus;
  uploaded_at: string;
  links: DocumentLink[];
}

export interface Evidence {
  link_id: string;
  relationship_type: RelationshipType;
  notes: string | null;
  document: DocumentItem;
}

export interface CompletenessSection {
  key: string;
  label: string;
  weight: number;
  ratio: number;
  points: number;
  status: "complete" | "partial" | "empty";
  detail: string;
  requires_evidence: boolean;
}

export interface ExpiryAlert {
  entity_type: string;
  entity_id: string;
  label: string;
  expires_at: string;
  days_left: number;
  temporal_status: TemporalStatus;
}

export interface DashboardSummary {
  company: CompanySummary;
  completeness: { score: number; sections: CompletenessSection[] };
  counts: {
    services: number;
    territories: number;
    financial_years: number;
    certifications: number;
    registrations: number;
    insurances: number;
    people: number;
    qualifications: number;
    cpv_preferences: number;
    projects: number;
    documents: number;
    document_links: number;
  };
  expiry_alerts: ExpiryAlert[];
}
