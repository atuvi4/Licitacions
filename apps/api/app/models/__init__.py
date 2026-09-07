"""Registre de models. Importar aquest modul carrega tot el metadata d'Alembic."""

from app.db.base import Base
from app.models.audit import AuditLog
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

__all__ = [
    "AuditLog",
    "Base",
    "Buyer",
    "CPVCode",
    "Company",
    "CompanyCPVPreference",
    "CompanyCertification",
    "CompanyContractPreferences",
    "CompanyFinancialYear",
    "CompanyInsurance",
    "CompanyMembership",
    "CompanyPerson",
    "CompanyProject",
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
    "project_services",
]
