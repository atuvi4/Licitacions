"""Registre de models. Importar aquest modul carrega tot el metadata d'Alembic."""

from app.db.base import Base
from app.models.analysis_quotes import AnalysisQuote
from app.models.audit import AuditLog
from app.models.compliance import (
    AssessmentEvidence,
    CompanyComplianceRun,
    CompanyRequirementAssessment,
)
from app.models.cpv import CompanyCPVPreference, CPVCode, ServiceCPV
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.documents import Document, DocumentLink
from app.models.identity import Company, CompanyMembership, User
from app.models.jobs import JobRequest, WorkJob
from app.models.matching import CompanyTenderLotMatch, CompanyTenderMatch
from app.models.offer import (
    EconomicOffer,
    EconomicOfferLine,
    EconomicOfferLot,
    EconomicOfferSection,
)
from app.models.people import CompanyPerson, PersonQualification
from app.models.profile import (
    CompanyContractPreferences,
    CompanyFinancialYear,
    CompanyService,
    CompanyTerritory,
)
from app.models.projects import CompanyProject, ProjectCPV, project_services
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.sources import IngestionRun, Source, SourceRecord
from app.models.submission import (
    SubmissionPackage,
    SubmissionPackageApproval,
    SubmissionPackageItem,
)
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
from app.models.tender_lifecycle import TenderChangeEvent, TenderRevision
from app.models.tenders import (
    Buyer,
    Tender,
    TenderCPV,
    TenderDocument,
    TenderLot,
    TenderLotCPV,
)
from app.models.workspace import (
    BidWorkspace,
    WorkspaceChecklistItem,
    WorkspaceDecision,
    WorkspaceEvidenceItem,
    WorkspaceNote,
    WorkspaceTask,
    WorkspaceTechnicalSolution,
)

__all__ = [
    "AnalysisQuote",
    "AssessmentEvidence",
    "AuditLog",
    "AwardCriterion",
    "AwardCriterionEvidence",
    "Base",
    "BidWorkspace",
    "Buyer",
    "CPVCode",
    "Company",
    "CompanyCPVPreference",
    "CompanyCertification",
    "CompanyComplianceRun",
    "CompanyContractPreferences",
    "CompanyFinancialYear",
    "CompanyInsurance",
    "CompanyMembership",
    "CompanyPerson",
    "CompanyProject",
    "CompanyRequirementAssessment",
    "CompanyRegistration",
    "CompanyService",
    "CompanyTenderLotMatch",
    "CompanyTenderMatch",
    "CompanyTerritory",
    "Document",
    "DocumentLink",
    "EconomicOffer",
    "EconomicOfferLine",
    "EconomicOfferLot",
    "EconomicOfferSection",
    "IngestionRun",
    "JobRequest",
    "PersonQualification",
    "ProjectCPV",
    "Requirement",
    "RequirementEvidence",
    "RequirementExtractionRun",
    "ServiceCPV",
    "Source",
    "SourceRecord",
    "SubmissionPackage",
    "SubmissionPackageApproval",
    "SubmissionPackageItem",
    "Tender",
    "TenderCPV",
    "TenderChangeEvent",
    "TenderDocument",
    "TenderDocumentContent",
    "TenderDocumentExtraction",
    "TenderDocumentFile",
    "TenderDocumentPage",
    "TenderLot",
    "TenderLotCPV",
    "TenderRevision",
    "User",
    "WorkJob",
    "WorkspaceChecklistItem",
    "WorkspaceDecision",
    "WorkspaceEvidenceItem",
    "WorkspaceNote",
    "WorkspaceTask",
    "WorkspaceTechnicalSolution",
    "project_services",
]
