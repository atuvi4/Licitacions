"""Registre de models. Importar aquest modul carrega tot el metadata d'Alembic."""

from app.db.base import Base
from app.models.analysis_quotes import AnalysisQuote
from app.models.audit import AuditLog
from app.models.classification import (
    TenderDocumentClassificationFinding,
    TenderDocumentClassificationRun,
    TenderDocumentRoleDecision,
    TenderDocumentRoleSuggestion,
)
from app.models.compliance import (
    AssessmentEvidence,
    CompanyComplianceRun,
    CompanyRequirementAssessment,
)
from app.models.cpv import CompanyCPVPreference, CPVCode, ServiceCPV
from app.models.credentials import CompanyCertification, CompanyInsurance, CompanyRegistration
from app.models.dimensions import WorkspaceDimension, WorkspaceDimensionObservation
from app.models.documents import Document, DocumentLink
from app.models.identity import Company, CompanyMembership, User
from app.models.issues import WorkspaceIssue, WorkspaceIssueSourceRef
from app.models.jobs import JobRequest, WorkJob
from app.models.knowledge import (
    CapabilityDefinitionRow,
    CertificationStandardRow,
    CompanyOperatingFact,
    CompanyPartner,
    CompanyProduct,
    CompanySolution,
    HistoricBid,
    KnowledgeCoverageArea,
    KnowledgeObservation,
    PartnerRoleRow,
    ServiceLineRow,
    SolutionCapability,
    SolutionProduct,
)
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
from app.models.provenance import RequirementSourceRefLink, TenderSourceRef
from app.models.reception import TenderDocumentReception, TenderDocumentReceptionItem
from app.models.requirements import (
    AwardCriterion,
    AwardCriterionEvidence,
    Requirement,
    RequirementEvidence,
    RequirementExtractionRun,
)
from app.models.review import (
    ReviewItem,
    ReviewItemRelation,
    ReviewItemRevision,
    ReviewItemSource,
    ReviewReconciliation,
)
from app.models.solution_option_dimensions import WorkspaceSolutionOptionDimension
from app.models.solution_options import WorkspaceSolutionOption
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
from app.models.tender_scope import (
    ContractFactBlock,
    RequirementAwardLink,
    RequirementCapabilityNeed,
    RequirementCapabilityNeedSourceRefLink,
    RequirementTerritory,
    TenderContractFact,
    TenderContractFactSourceRefLink,
    TenderScopeItem,
    TenderScopeItemSourceRefLink,
)
from app.models.tender_structure import (
    AwardCriterionBlock,
    RequirementBlock,
    TenderBlock,
    TenderBlockSourceRefLink,
    TenderSite,
    TenderSiteSourceRefLink,
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
    WorkspaceEvidenceItem,
    WorkspaceNote,
    WorkspaceTask,
    WorkspaceTechnicalSolution,
)

__all__ = [
    "CapabilityDefinitionRow",
    "CertificationStandardRow",
    "CompanyOperatingFact",
    "CompanyPartner",
    "CompanyProduct",
    "CompanySolution",
    "HistoricBid",
    "KnowledgeCoverageArea",
    "KnowledgeObservation",
    "PartnerRoleRow",
    "ServiceLineRow",
    "SolutionCapability",
    "SolutionProduct",
    "ReviewItem",
    "ReviewItemRelation",
    "ReviewItemRevision",
    "ReviewItemSource",
    "ReviewReconciliation",
    "AnalysisQuote",
    "AssessmentEvidence",
    "AuditLog",
    "AwardCriterion",
    "AwardCriterionBlock",
    "ContractFactBlock",
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
    "RequirementAwardLink",
    "RequirementBlock",
    "RequirementCapabilityNeed",
    "RequirementCapabilityNeedSourceRefLink",
    "RequirementTerritory",
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
    "TenderDocumentClassificationFinding",
    "TenderDocumentClassificationRun",
    "TenderDocumentReception",
    "TenderDocumentReceptionItem",
    "TenderDocumentRoleDecision",
    "TenderDocumentRoleSuggestion",
    "TenderLot",
    "TenderBlock",
    "TenderBlockSourceRefLink",
    "TenderContractFact",
    "TenderContractFactSourceRefLink",
    "TenderScopeItem",
    "TenderScopeItemSourceRefLink",
    "TenderSite",
    "TenderSiteSourceRefLink",
    "TenderLotCPV",
    "TenderRevision",
    "TenderSourceRef",
    "RequirementSourceRefLink",
    "WorkspaceDimension",
    "WorkspaceDimensionObservation",
    "WorkspaceIssue",
    "WorkspaceIssueSourceRef",
    "WorkspaceSolutionOption",
    "WorkspaceSolutionOptionDimension",
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
