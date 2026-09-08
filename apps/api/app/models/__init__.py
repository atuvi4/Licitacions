"""Registre de models. Importar aquest modul carrega tot el metadata d'Alembic."""

from app.db.base import Base
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
from app.models.matching import CompanyTenderLotMatch, CompanyTenderMatch
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
from app.models.tender_documents import (
    TenderDocumentContent,
    TenderDocumentExtraction,
    TenderDocumentFile,
    TenderDocumentPage,
)
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
    WorkspaceNote,
    WorkspaceTask,
)

__all__ = [
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
    "IngestionRun",
    "PersonQualification",
    "ProjectCPV",
    "Requirement",
    "RequirementEvidence",
    "RequirementExtractionRun",
    "ServiceCPV",
    "Source",
    "SourceRecord",
    "Tender",
    "TenderCPV",
    "TenderDocument",
    "TenderDocumentContent",
    "TenderDocumentExtraction",
    "TenderDocumentFile",
    "TenderDocumentPage",
    "TenderLot",
    "TenderLotCPV",
    "User",
    "WorkspaceChecklistItem",
    "WorkspaceDecision",
    "WorkspaceNote",
    "WorkspaceTask",
    "project_services",
]
