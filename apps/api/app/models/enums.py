"""Enumeracions del domini.

Es guarden com a text (VARCHAR + CHECK) per facilitar migracions futures.
"""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - conveniencia
        return self.value


class MembershipRole(StrEnum):
    """Rols preparats. En aquesta iteracio nomes es comprova la pertinenca, no el rol."""

    OWNER = "owner"
    ADMIN = "admin"
    BID_MANAGER = "bid_manager"
    TECHNICAL = "technical"
    VIEWER = "viewer"


class SpecializationLevel(StrEnum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class TerritoryType(StrEnum):
    MUNICIPALITY = "municipality"
    COMARCA = "comarca"
    PROVINCE = "province"
    AUTONOMOUS_REGION = "autonomous_region"
    COUNTRY = "country"
    NUTS = "nuts"
    OTHER = "other"


class VerificationStatus(StrEnum):
    """Estat de verificacio documental. NO barreja informacio temporal."""

    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"


class TemporalStatus(StrEnum):
    """Estat derivat de `expires_at`. Mai es persisteix."""

    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class RegistrationType(StrEnum):
    ROLECE = "rolece"
    RELI = "reli"
    BUSINESS_CLASSIFICATION = "business_classification"
    PROFESSIONAL_REGISTRY = "professional_registry"
    ADMINISTRATIVE_ACCREDITATION = "administrative_accreditation"
    OTHER = "other"


class InsuranceType(StrEnum):
    CIVIL_LIABILITY = "civil_liability"
    PROFESSIONAL_INDEMNITY = "professional_indemnity"
    WORKERS_COMPENSATION = "workers_compensation"
    SURETY_BOND = "surety_bond"
    ALL_RISK = "all_risk"
    OTHER = "other"


class AvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    PARTIALLY_AVAILABLE = "partially_available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class QualificationType(StrEnum):
    DEGREE = "degree"
    PROFESSIONAL_CERTIFICATION = "professional_certification"
    TRAINING = "training"
    AUTHORIZATION = "authorization"
    OTHER = "other"


class ClientType(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"
    MIXED = "mixed"
    OTHER = "other"


class ProjectStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DocumentCategory(StrEnum):
    CERTIFICATION = "certification"
    INSURANCE = "insurance"
    REGISTRATION = "registration"
    PROJECT_EVIDENCE = "project_evidence"
    FINANCIAL = "financial"
    LEGAL = "legal"
    CV = "cv"
    TECHNICAL = "technical"
    COMMERCIAL = "commercial"
    OTHER = "other"


class LinkedEntityType(StrEnum):
    """Tipus d'entitat que pot ser justificada per un document.

    Ampliar aquesta llista (p. ex. amb `tender` o `requirement`) no requereix
    canvis d'esquema, nomes una migracio del CHECK constraint.
    """

    COMPANY = "company"
    COMPANY_SERVICE = "company_service"
    COMPANY_FINANCIAL_YEAR = "company_financial_year"
    COMPANY_CERTIFICATION = "company_certification"
    COMPANY_REGISTRATION = "company_registration"
    COMPANY_INSURANCE = "company_insurance"
    COMPANY_PERSON = "company_person"
    PERSON_QUALIFICATION = "person_qualification"
    COMPANY_PROJECT = "company_project"


class RelationshipType(StrEnum):
    """Que aporta el document respecte de l'entitat enllacada."""

    CERTIFICATE = "certificate"
    CERTIFICATE_OF_GOOD_EXECUTION = "certificate_of_good_execution"
    POLICY = "policy"
    CONTRACT = "contract"
    INVOICE = "invoice"
    FINANCIAL_STATEMENT = "financial_statement"
    CV = "cv"
    DIPLOMA = "diploma"
    ACCREDITATION = "accreditation"
    TECHNICAL_SHEET = "technical_sheet"
    REFERENCE_LETTER = "reference_letter"
    SUPPORTING_DOCUMENT = "supporting_document"
    OTHER = "other"


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    LOGIN = "login"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    DOWNLOAD = "download"


# ---------------------------------------------------------------------------
# Ingestio de fonts externes
# ---------------------------------------------------------------------------


class SourceType(StrEnum):
    SYNDICATION_ATOM = "syndication_atom"
    API = "api"
    FILE = "file"


class IngestionStatus(StrEnum):
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


class ProcessingStatus(StrEnum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Model canonic de licitacions
#
# Els codis de la font es tradueixen a aquests valors. Sempre hi ha `UNKNOWN` i
# `OTHER`: preferim una dada buida i honesta a una d'inventada.
# ---------------------------------------------------------------------------


class TenderStatus(StrEnum):
    PRE_ANNOUNCEMENT = "pre_announcement"   # PLACSP: PRE
    OPEN = "open"                           # PLACSP: PUB (en termini)
    PENDING_AWARD = "pending_award"         # PLACSP: EV
    AWARDED = "awarded"                     # PLACSP: ADJ
    RESOLVED = "resolved"                   # PLACSP: RES
    CANCELLED = "cancelled"                 # PLACSP: ANUL
    UNKNOWN = "unknown"


class ProcedureType(StrEnum):
    OPEN = "open"
    OPEN_SIMPLIFIED = "open_simplified"
    RESTRICTED = "restricted"
    NEGOTIATED_WITHOUT_PUBLICITY = "negotiated_without_publicity"
    NEGOTIATED_WITH_PUBLICITY = "negotiated_with_publicity"
    COMPETITIVE_DIALOGUE = "competitive_dialogue"
    MINOR_CONTRACT = "minor_contract"
    FRAMEWORK_DERIVED = "framework_derived"
    DESIGN_CONTEST = "design_contest"
    INNOVATION_PARTNERSHIP = "innovation_partnership"
    INNOVATION_PARTNERSHIP_DERIVED = "innovation_partnership_derived"
    DYNAMIC_PURCHASING_SYSTEM = "dynamic_purchasing_system"
    TENDER_WITH_NEGOTIATION = "tender_with_negotiation"
    INTERNAL_RULES = "internal_rules"
    OTHER = "other"
    UNKNOWN = "unknown"


class ContractType(StrEnum):
    GOODS = "goods"
    SERVICES = "services"
    WORKS = "works"
    PUBLIC_SERVICES_MANAGEMENT = "public_services_management"
    SERVICES_CONCESSION = "services_concession"
    PUBLIC_WORKS_CONCESSION = "public_works_concession"
    WORKS_CONCESSION = "works_concession"
    PUBLIC_PRIVATE_PARTNERSHIP = "public_private_partnership"
    SPECIAL_ADMINISTRATIVE = "special_administrative"
    PRIVATE = "private"
    PATRIMONIAL = "patrimonial"
    OTHER = "other"
    UNKNOWN = "unknown"


class TenderDocumentType(StrEnum):
    ADMINISTRATIVE_TERMS = "administrative_terms"   # plec de clausules administratives
    TECHNICAL_TERMS = "technical_terms"             # plec de prescripcions tecniques
    NOTICE = "notice"                               # anunci de licitacio
    AWARD = "award"                                 # acta o resolucio d'adjudicacio
    ADDITIONAL = "additional"
    OTHER = "other"


# ---------------------------------------------------------------------------
# Pre-fit: encaix calculat NOMES amb metadades publiques i preferencies de
# l'empresa. No es una validacio del plec ni un Go/No-Go.
# ---------------------------------------------------------------------------


class MatchStatus(StrEnum):
    """Estat d'un pre-fit.

    Deliberadament NO existeixen GO/NO_GO: encara no s'han llegit els plecs.
    """

    CANDIDATE = "candidate"                  # val la pena revisar-la
    LOW_FIT = "low_fit"                      # encaixa poc segons metadades
    EXCLUDED = "excluded"                    # descartada (negoci o estat)
    CLOSED = "closed"                        # ja no es accionable
    INSUFFICIENT_DATA = "insufficient_data"  # no hi ha prou metadades per opinar


class ExclusionKind(StrEnum):
    """D'on ve una exclusio. Els blockers LEGALS encara no existeixen."""

    BUSINESS = "business"  # decisio de l'empresa (CPV exclos, territori, capacitat)
    STATUS = "status"      # la licitacio ja no es accionable (termini, adjudicada)


class DimensionResult(StrEnum):
    """Resultat d'una dimensio.

    UNKNOWN NO es NO_MATCH: que ens falti una dada no vol dir que no encaixi.
    """

    MATCH = "match"
    PARTIAL = "partial"
    NO_MATCH = "no_match"
    UNKNOWN = "unknown"
