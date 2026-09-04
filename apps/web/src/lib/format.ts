import type {
  AvailabilityStatus,
  ClientType,
  DocumentCategory,
  InsuranceType,
  LinkedEntityType,
  ProjectStatus,
  QualificationType,
  RegistrationType,
  RelationshipType,
  SpecializationLevel,
  TemporalStatus,
  TerritoryType,
  VerificationStatus,
} from "@/lib/types";

const LOCALE = "ca-ES";

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE, { dateStyle: "medium" }).format(date);
}

export function formatMoney(value: string | number | null | undefined, currency = "EUR"): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = typeof value === "string" ? Number(value) : value;
  if (Number.isNaN(amount)) return "—";
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency,
    maximumFractionDigits: 0,
  }).format(amount);
}

export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export const TEMPORAL_STATUS_LABELS: Record<TemporalStatus, string> = {
  valid: "Vigent",
  expiring_soon: "Caduca aviat",
  expired: "Caducat",
  unknown: "Sense caducitat",
};

export const VERIFICATION_STATUS_LABELS: Record<VerificationStatus, string> = {
  pending_verification: "Pendent de verificar",
  verified: "Verificat",
};

export const SPECIALIZATION_LABELS: Record<SpecializationLevel, string> = {
  basic: "Basic",
  intermediate: "Intermedi",
  advanced: "Avançat",
  expert: "Expert",
};

export const TERRITORY_TYPE_LABELS: Record<TerritoryType, string> = {
  municipality: "Municipi",
  comarca: "Comarca",
  province: "Província",
  autonomous_region: "Comunitat autònoma",
  country: "País",
  nuts: "NUTS",
  other: "Altres",
};

export const CLIENT_TYPE_LABELS: Record<ClientType, string> = {
  public: "Públic",
  private: "Privat",
  mixed: "Mixt",
  other: "Altres",
};

export const PROJECT_STATUS_LABELS: Record<ProjectStatus, string> = {
  planned: "Planificat",
  in_progress: "En execució",
  completed: "Finalitzat",
  cancelled: "Cancel·lat",
};

export const DOCUMENT_CATEGORY_LABELS: Record<DocumentCategory, string> = {
  certification: "Certificació",
  insurance: "Assegurança",
  registration: "Registre",
  project_evidence: "Evidència de projecte",
  financial: "Econòmic",
  legal: "Legal",
  cv: "CV",
  technical: "Tècnic",
  commercial: "Comercial",
  other: "Altres",
};

export const RELATIONSHIP_LABELS: Record<RelationshipType, string> = {
  certificate: "Certificat",
  certificate_of_good_execution: "Certificat de bona execució",
  policy: "Pòlissa",
  contract: "Contracte",
  invoice: "Factura",
  financial_statement: "Comptes anuals",
  cv: "CV",
  diploma: "Titulació",
  accreditation: "Acreditació",
  technical_sheet: "Fitxa tècnica",
  reference_letter: "Carta de referència",
  supporting_document: "Document de suport",
  other: "Altres",
};

export const ENTITY_TYPE_LABELS: Record<LinkedEntityType, string> = {
  company: "Empresa",
  company_service: "Servei",
  company_financial_year: "Exercici econòmic",
  company_certification: "Certificació",
  company_registration: "Registre",
  company_insurance: "Assegurança",
  company_person: "Persona",
  person_qualification: "Titulació",
  company_project: "Projecte",
};

export function optionsFrom<T extends string>(labels: Record<T, string>) {
  return (Object.keys(labels) as T[]).map((value) => ({ value, label: labels[value] }));
}

export const REGISTRATION_TYPE_LABELS: Record<RegistrationType, string> = {
  rolece: "ROLECE",
  reli: "RELI",
  business_classification: "Classificació empresarial",
  professional_registry: "Registre professional",
  administrative_accreditation: "Acreditació administrativa",
  other: "Altres",
};

export const INSURANCE_TYPE_LABELS: Record<InsuranceType, string> = {
  civil_liability: "Responsabilitat civil",
  professional_indemnity: "Responsabilitat professional",
  workers_compensation: "Accidents de treball",
  surety_bond: "Aval / caució",
  all_risk: "Tot risc",
  other: "Altres",
};

export const AVAILABILITY_LABELS: Record<AvailabilityStatus, string> = {
  available: "Disponible",
  partially_available: "Parcialment disponible",
  unavailable: "No disponible",
  unknown: "Sense determinar",
};

export const QUALIFICATION_TYPE_LABELS: Record<QualificationType, string> = {
  degree: "Titulació acadèmica",
  professional_certification: "Certificació professional",
  training: "Formació",
  authorization: "Habilitació",
  other: "Altres",
};

/** Colors de badge segons l'estat temporal derivat de la data de caducitat. */
export const TEMPORAL_TONE: Record<TemporalStatus, "success" | "warning" | "danger" | "neutral"> = {
  valid: "success",
  expiring_soon: "warning",
  expired: "danger",
  unknown: "neutral",
};
