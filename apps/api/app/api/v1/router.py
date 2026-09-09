"""Agregacio de tots els routers de la v1 de l'API.

Convencio: tot el que es dades d'empresa penja de `/companies/{company_id}/...`.
Aixo fa que el tenant sigui explicit a la URL i que la dependencia
`get_company_context` s'apliqui sempre.
"""

from fastapi import APIRouter

from app.api.v1.routers import (
    auth,
    companies,
    compliance,
    config,
    cpv,
    credentials,
    documents,
    dossier,
    insurance_limits,
    matching,
    people,
    profile,
    projects,
    requirements,
    submission,
    tender_documents,
    tenders,
    workspaces,
)

company_scoped = APIRouter(prefix="/companies/{company_id}")
company_scoped.include_router(profile.services_router)
company_scoped.include_router(profile.territories_router)
company_scoped.include_router(profile.financial_years_router)
company_scoped.include_router(credentials.certifications_router)
company_scoped.include_router(credentials.registrations_router)
company_scoped.include_router(credentials.insurances_router)
company_scoped.include_router(insurance_limits.router)
company_scoped.include_router(people.people_router)
company_scoped.include_router(people.qualifications_router)
company_scoped.include_router(projects.router)
company_scoped.include_router(documents.router)
company_scoped.include_router(documents.evidence_router)
company_scoped.include_router(cpv.preferences_router)
# Compliment: dada DE TENANT, penja de /companies/{company_id}.
company_scoped.include_router(compliance.router)
company_scoped.include_router(compliance.assessments_router)
# Candidatura: reuneix pre-fit, requisits i compliment i hi afegeix la decisio
# humana. Tambe dada de tenant.
company_scoped.include_router(workspaces.tender_router)
company_scoped.include_router(workspaces.router)
# Dossier documental: penja del workspace, que ja es de tenant.
company_scoped.include_router(dossier.router)
# Paquet de presentacio i aprovacio final humana.
company_scoped.include_router(submission.router)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(config.router)
api_router.include_router(companies.router)
api_router.include_router(cpv.catalog_router)
# Licitacions: dades publiques i globals, no pengen de cap empresa.
api_router.include_router(tenders.router)
# Plecs: tambe publics i globals. El processat es manual des de la fitxa.
api_router.include_router(tender_documents.router)
# Analisi de plecs: derivada de documents publics, tambe global.
api_router.include_router(requirements.router)
# Pre-fit: dada de tenant, penja de /companies/{company_id}.
api_router.include_router(matching.router)
api_router.include_router(company_scoped)
