"""Regles de compliment: deterministes, sense sessio i sense IA.

Cada regla es una funcio pura sobre una fotografia del perfil, aixi que aqui es
proven totes les vores sense tocar la base de dades.

El fil conductor de tot el fitxer es la distincio entre **no complir** i **no
saber**. Confondre-les es el que fabrica NO-GOs falsos, i es exactament el que
aquests tests impedeixen.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from app.compliance.certifications import assess_certification, normalize_certification_name
from app.compliance.economic import assess_economic_solvency
from app.compliance.experience import assess_experience
from app.compliance.insurance import assess_insurance
from app.compliance.personnel import assess_qualification, assess_registration
from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import ExplanationCode
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    DocumentCategory,
    EvidenceStrength,
    InsuranceType,
    LinkedEntityType,
    ProjectStatus,
    QualificationType,
    RegistrationType,
    RequirementCategory,
    RequirementOperator,
    VerificationStatus,
)

DEADLINE = date(2026, 10, 15)


# --------------------------------------------------------------------------- #
# Dobles minims: les regles nomes llegeixen atributs                            #
# --------------------------------------------------------------------------- #


@dataclass
class FakeRequirement:
    category: RequirementCategory = RequirementCategory.OTHER
    value_json: dict[str, Any] | None = None
    operator: RequirementOperator | None = None
    title: str = "Requisit"


@dataclass
class FakeYear:
    year: int
    revenue: Decimal | None
    currency: str = "EUR"
    id: uuid.UUID = None

    def __post_init__(self):
        self.id = self.id or uuid.uuid4()


@dataclass
class FakeCert:
    name: str
    expires_at: date | None = None
    verification_status: VerificationStatus = VerificationStatus.VERIFIED
    id: uuid.UUID = None

    def __post_init__(self):
        self.id = self.id or uuid.uuid4()


@dataclass
class FakeInsurance:
    type: InsuranceType
    coverage_amount: Decimal | None
    currency: str = "EUR"
    expires_at: date | None = None
    id: uuid.UUID = None

    def __post_init__(self):
        self.id = self.id or uuid.uuid4()


@dataclass
class FakeRegistration:
    type: RegistrationType
    name: str
    expires_at: date | None = None
    id: uuid.UUID = None

    def __post_init__(self):
        self.id = self.id or uuid.uuid4()


@dataclass
class FakeCPV:
    cpv_code: str


@dataclass
class FakeService:
    name: str


@dataclass
class FakeProject:
    name: str
    amount: Decimal | None = None
    currency: str = "EUR"
    status: ProjectStatus = ProjectStatus.COMPLETED
    start_date: date | None = None
    end_date: date | None = None
    cpvs: list = None
    services: list = None
    id: uuid.UUID = None

    def __post_init__(self):
        self.id = self.id or uuid.uuid4()
        self.cpvs = self.cpvs or []
        self.services = self.services or []


@dataclass
class FakeQualification:
    name: str
    person_id: uuid.UUID
    type: QualificationType = QualificationType.DEGREE
    expires_at: date | None = None
    id: uuid.UUID = None

    def __post_init__(self):
        self.id = self.id or uuid.uuid4()


@dataclass
class FakeDoc:
    id: uuid.UUID
    name: str
    category: DocumentCategory
    expires_at: date | None = None


def snapshot(**kwargs) -> ProfileSnapshot:
    """Perfil buit amb totes les seccions completes tret que es digui el contrari."""
    sections = kwargs.pop("sections", None) or {
        "certifications": 1.0,
        "insurances": 1.0,
        "registrations": 1.0,
        "experience": 1.0,
        "team": 1.0,
    }
    return ProfileSnapshot(company_id=uuid.uuid4(), section_ratio=sections, **kwargs)


# --------------------------------------------------------------------------- #
# Solvencia economica                                                           #
# --------------------------------------------------------------------------- #


def economic_requirement(**value) -> FakeRequirement:
    base = {"amount": 450000, "currency": "EUR", "period": "annual"}
    base.update(value)
    return FakeRequirement(
        category=RequirementCategory.ECONOMIC_SOLVENCY,
        value_json=base,
        operator=RequirementOperator.GTE,
    )


def test_revenue_above_the_threshold_satisfies():
    profile = snapshot(financial_years=[FakeYear(2025, Decimal("2450000"))])

    outcome = assess_economic_solvency(economic_requirement(), profile)

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.rule_code == "economic_revenue_gte_v1"
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_THRESHOLD_MET
    assert outcome.params["required"] == 450000
    assert outcome.params["actual"] == 2450000
    assert outcome.mode == AssessmentMode.AUTOMATIC


def test_revenue_exactly_at_the_threshold_satisfies():
    profile = snapshot(financial_years=[FakeYear(2025, Decimal("450000"))])

    assert assess_economic_solvency(economic_requirement(), profile).result == (
        AssessmentResult.SATISFIES
    )


def test_revenue_below_the_threshold_does_not_satisfy():
    profile = snapshot(financial_years=[FakeYear(2025, Decimal("120000"))])

    outcome = assess_economic_solvency(economic_requirement(), profile)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_THRESHOLD_NOT_MET


def test_the_best_year_within_the_lookback_counts():
    profile = snapshot(
        financial_years=[
            FakeYear(2025, Decimal("300000")),
            FakeYear(2024, Decimal("900000")),
            FakeYear(2023, Decimal("100000")),
        ]
    )

    outcome = assess_economic_solvency(economic_requirement(years=2), profile)

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.params["year"] == 2024, "2023 queda fora de la finestra"


def test_a_different_currency_is_never_converted():
    """Sense tipus de canvi inventat: aixo ho mira una persona."""
    profile = snapshot(financial_years=[FakeYear(2025, Decimal("5000000"), currency="USD")])

    outcome = assess_economic_solvency(economic_requirement(), profile)

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_CURRENCY_MISMATCH


def test_no_financial_data_is_unknown_not_a_breach():
    outcome = assess_economic_solvency(economic_requirement(), snapshot())

    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_DATA_INCOMPLETE


def test_an_ambiguous_period_is_review_not_a_guess():
    """"La mitjana dels tres ultims exercicis" es una regla legal, no una resta."""
    profile = snapshot(financial_years=[FakeYear(2025, Decimal("2450000"))])

    outcome = assess_economic_solvency(
        economic_requirement(period="average_last_three"), profile
    )

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.ECONOMIC_PERIOD_AMBIGUOUS


def test_a_requirement_without_amount_is_review():
    profile = snapshot(financial_years=[FakeYear(2025, Decimal("2450000"))])
    requirement = FakeRequirement(
        category=RequirementCategory.ECONOMIC_SOLVENCY, value_json={"currency": "EUR"}
    )

    outcome = assess_economic_solvency(requirement, profile)

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.REQUIREMENT_VALUE_MISSING


# --------------------------------------------------------------------------- #
# Certificacions                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ISO 9001", "ISO 9001:2015"),
        ("ISO 27001", "ISO/IEC 27001"),
        ("ISO 14001", "UNE-EN ISO 14001"),
        ("iso  9001", "ISO-9001"),
    ],
)
def test_iso_names_normalise_to_the_same_key(a: str, b: str):
    assert normalize_certification_name(a) == normalize_certification_name(b)


def test_different_iso_standards_never_collide():
    assert normalize_certification_name("ISO 9001") != normalize_certification_name("ISO 27001")


def cert_requirement(name: str, valid_at_deadline: bool = False) -> FakeRequirement:
    return FakeRequirement(
        category=RequirementCategory.CERTIFICATION,
        value_json={"name": name, "valid_at_deadline": valid_at_deadline},
    )


def test_a_present_certification_satisfies():
    profile = snapshot(certifications=[FakeCert("ISO 9001:2015")])

    outcome = assess_certification(cert_requirement("ISO 9001"), profile, DEADLINE)

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_FOUND_VALID


def test_a_certification_valid_past_the_deadline_satisfies():
    profile = snapshot(certifications=[FakeCert("ISO 27001", expires_at=date(2027, 1, 1))])

    outcome = assess_certification(cert_requirement("ISO 27001", True), profile, DEADLINE)

    assert outcome.result == AssessmentResult.SATISFIES


def test_a_certification_expiring_before_the_deadline_does_not_satisfy():
    profile = snapshot(certifications=[FakeCert("ISO 27001", expires_at=date(2026, 6, 1))])

    outcome = assess_certification(cert_requirement("ISO 27001", True), profile, DEADLINE)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_FOUND_EXPIRED


def test_a_certification_without_expiry_needs_review_when_validity_matters():
    profile = snapshot(certifications=[FakeCert("ISO 27001", expires_at=None)])

    outcome = assess_certification(cert_requirement("ISO 27001", True), profile, DEADLINE)

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_FOUND_NO_EXPIRY


def test_a_missing_certification_with_a_complete_profile_does_not_satisfy():
    """El perfil declara totes les certificacions: l'absencia si que informa."""
    profile = snapshot(certifications=[FakeCert("ISO 9001")])

    outcome = assess_certification(cert_requirement("ISO 27001"), profile, DEADLINE)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_NOT_FOUND_PROFILE_COMPLETE


def test_a_missing_certification_with_an_incomplete_profile_is_unknown():
    """La distincio critica: no consta NO vol dir que no la tinguin."""
    profile = snapshot(certifications=[], sections={"certifications": 0.4})

    outcome = assess_certification(cert_requirement("ISO 27001"), profile, DEADLINE)

    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_DATA_INCOMPLETE


def test_no_semantic_equivalence_between_standards():
    """Tenir ENS Alt no acredita ISO 27001, per molt que se citin juntes."""
    profile = snapshot(certifications=[FakeCert("Esquema Nacional de Seguridad Alto")])

    outcome = assess_certification(cert_requirement("ISO 27001"), profile, DEADLINE)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_a_certificate_document_makes_the_evidence_strong():
    cert = FakeCert("ISO 9001")
    document = FakeDoc(uuid.uuid4(), "Certificat ISO", DocumentCategory.CERTIFICATION)
    profile = snapshot(
        certifications=[cert],
        documents_by_entity={
            (LinkedEntityType.COMPANY_CERTIFICATION.value, cert.id): [document]
        },
    )

    outcome = assess_certification(cert_requirement("ISO 9001"), profile, DEADLINE)

    assert outcome.strength == EvidenceStrength.STRONG
    assert outcome.evidence[0].document_id == document.id


# --------------------------------------------------------------------------- #
# Assegurances                                                                  #
# --------------------------------------------------------------------------- #


def insurance_requirement(amount: int, **extra) -> FakeRequirement:
    value = {"coverage_amount": amount, "currency": "EUR", "type": "responsabilidad civil"}
    value.update(extra)
    return FakeRequirement(category=RequirementCategory.INSURANCE, value_json=value)


def test_coverage_above_the_threshold_satisfies():
    profile = snapshot(
        insurances=[
            FakeInsurance(InsuranceType.CIVIL_LIABILITY, Decimal("1000000"),
                          expires_at=date(2027, 1, 1))
        ]
    )

    outcome = assess_insurance(insurance_requirement(600000), profile, DEADLINE)

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.INSURANCE_COVERAGE_MET


def test_coverage_below_the_threshold_does_not_satisfy():
    profile = snapshot(
        insurances=[FakeInsurance(InsuranceType.CIVIL_LIABILITY, Decimal("300000"))]
    )

    outcome = assess_insurance(insurance_requirement(600000), profile, DEADLINE)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.INSURANCE_COVERAGE_NOT_MET


def test_an_expired_policy_does_not_satisfy_even_with_enough_coverage():
    profile = snapshot(
        insurances=[
            FakeInsurance(InsuranceType.CIVIL_LIABILITY, Decimal("1000000"),
                          expires_at=date(2026, 1, 1))
        ]
    )

    outcome = assess_insurance(insurance_requirement(600000), profile, DEADLINE)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.INSURANCE_EXPIRED


def test_an_insurance_in_another_currency_needs_review():
    profile = snapshot(
        insurances=[
            FakeInsurance(InsuranceType.CIVIL_LIABILITY, Decimal("2000000"), currency="USD")
        ]
    )

    outcome = assess_insurance(insurance_requirement(600000), profile, DEADLINE)

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.INSURANCE_CURRENCY_MISMATCH


def test_no_policy_with_a_complete_profile_does_not_satisfy():
    profile = snapshot(insurances=[])

    outcome = assess_insurance(insurance_requirement(600000), profile, DEADLINE)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_no_policy_with_an_incomplete_profile_is_unknown():
    profile = snapshot(insurances=[], sections={"insurances": 0.0})

    outcome = assess_insurance(insurance_requirement(600000), profile, DEADLINE)

    assert outcome.result == AssessmentResult.UNKNOWN


# --------------------------------------------------------------------------- #
# Experiencia                                                                   #
# --------------------------------------------------------------------------- #


def experience_requirement(**value) -> FakeRequirement:
    base = {"minimum_projects": 3, "minimum_amount_each": 100000, "lookback_years": 5}
    base.update(value)
    return FakeRequirement(category=RequirementCategory.EXPERIENCE, value_json=base)


def accredited_projects(count: int, amount: str = "150000") -> tuple[list, dict]:
    projects, documents = [], {}
    for index in range(count):
        project = FakeProject(
            name=f"Projecte {index}",
            amount=Decimal(amount),
            end_date=date(2025, 6, 1),
        )
        projects.append(project)
        documents[(LinkedEntityType.COMPANY_PROJECT.value, project.id)] = [
            FakeDoc(uuid.uuid4(), "Certificat de bona execucio", DocumentCategory.PROJECT_EVIDENCE)
        ]
    return projects, documents


def test_enough_accredited_projects_satisfy():
    projects, documents = accredited_projects(3)
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(experience_requirement(), profile, today=date(2026, 9, 1))

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_COUNT_MET
    assert outcome.strength == EvidenceStrength.STRONG


def test_too_few_projects_with_a_complete_profile_does_not_satisfy():
    projects, documents = accredited_projects(2)
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(experience_requirement(), profile, today=date(2026, 9, 1))

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_INSUFFICIENT


def test_too_few_projects_with_an_incomplete_profile_is_unknown():
    projects, documents = accredited_projects(2)
    profile = snapshot(
        projects=projects, documents_by_entity=documents, sections={"experience": 0.3}
    )

    outcome = assess_experience(experience_requirement(), profile, today=date(2026, 9, 1))

    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_DATA_INCOMPLETE


def test_projects_below_the_amount_do_not_count():
    projects, documents = accredited_projects(3, amount="50000")
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(experience_requirement(), profile, today=date(2026, 9, 1))

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_projects_outside_the_lookback_window_do_not_count():
    projects, documents = accredited_projects(3)
    for project in projects:
        project.end_date = date(2015, 1, 1)
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(experience_requirement(), profile, today=date(2026, 9, 1))

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_unfinished_projects_do_not_count():
    projects, documents = accredited_projects(3)
    for project in projects:
        project.status = ProjectStatus.IN_PROGRESS
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(experience_requirement(), profile, today=date(2026, 9, 1))

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_projects_without_evidence_are_unknown_not_a_breach():
    """Hi son, pero no s'han acreditat: es una carpeta incompleta, no un incompliment."""
    projects, _ = accredited_projects(3)
    profile = snapshot(projects=projects, documents_by_entity={})

    outcome = assess_experience(experience_requirement(), profile, today=date(2026, 9, 1))

    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_EVIDENCE_MISSING


def test_free_text_similarity_is_never_decided_automatically():
    """La regla mes important d'aquesta iteracio: no s'inventa cap similitud."""
    projects, documents = accredited_projects(5)
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(
        experience_requirement(similarity_condition="trabajos de naturaleza similar"),
        profile,
        today=date(2026, 9, 1),
    )

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_SIMILARITY_REVIEW
    assert outcome.mode == AssessmentMode.REVIEW_REQUIRED


def test_cpv_gives_an_objective_similarity_criterion():
    projects, documents = accredited_projects(3)
    for project in projects:
        project.cpvs = [FakeCPV("45314320")]
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(
        experience_requirement(cpv_codes=["45314320"], similarity_condition="similars"),
        profile,
        today=date(2026, 9, 1),
    )

    assert outcome.result == AssessmentResult.SATISFIES


def test_a_non_matching_cpv_excludes_the_project():
    projects, documents = accredited_projects(3)
    for project in projects:
        project.cpvs = [FakeCPV("79000000")]
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(
        experience_requirement(cpv_codes=["45314320"]), profile, today=date(2026, 9, 1)
    )

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_a_contract_document_is_supporting_not_strong():
    projects, _ = accredited_projects(3)
    documents = {
        (LinkedEntityType.COMPANY_PROJECT.value, p.id): [
            FakeDoc(uuid.uuid4(), "Contracte", DocumentCategory.LEGAL)
        ]
        for p in projects
    }
    profile = snapshot(projects=projects, documents_by_entity=documents)

    outcome = assess_experience(experience_requirement(), profile, today=date(2026, 9, 1))

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.strength == EvidenceStrength.SUPPORTING


# --------------------------------------------------------------------------- #
# Titulacions i registres                                                       #
# --------------------------------------------------------------------------- #


def test_an_exact_qualification_satisfies():
    person = uuid.uuid4()
    profile = snapshot(qualifications=[FakeQualification("Enginyer de Telecomunicacions", person)])
    requirement = FakeRequirement(
        category=RequirementCategory.PROFESSIONAL_QUALIFICATION,
        value_json={"name": "enginyer de telecomunicacions", "minimum_people": 1},
    )

    outcome = assess_qualification(requirement, profile, DEADLINE)

    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.QUALIFICATION_FOUND


def test_an_expired_qualification_does_not_count_towards_the_minimum():
    person = uuid.uuid4()
    profile = snapshot(
        qualifications=[
            FakeQualification("Enginyer", person, expires_at=date(2026, 1, 1))
        ]
    )
    requirement = FakeRequirement(
        category=RequirementCategory.PROFESSIONAL_QUALIFICATION,
        value_json={"name": "enginyer", "minimum_people": 1},
    )

    outcome = assess_qualification(requirement, profile, DEADLINE)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_a_missing_qualification_with_an_incomplete_team_is_unknown():
    profile = snapshot(qualifications=[], sections={"team": 0.2})
    requirement = FakeRequirement(
        category=RequirementCategory.PROFESSIONAL_QUALIFICATION,
        value_json={"name": "enginyer industrial"},
    )

    outcome = assess_qualification(requirement, profile, DEADLINE)

    assert outcome.result == AssessmentResult.UNKNOWN


def test_two_people_are_required_and_only_one_holds_it():
    person = uuid.uuid4()
    profile = snapshot(qualifications=[FakeQualification("Enginyer", person)])
    requirement = FakeRequirement(
        category=RequirementCategory.PROFESSIONAL_QUALIFICATION,
        value_json={"name": "enginyer", "minimum_people": 2},
    )

    outcome = assess_qualification(requirement, profile, DEADLINE)

    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY


def test_a_present_registration_satisfies():
    profile = snapshot(
        registrations=[FakeRegistration(RegistrationType.ROLECE, "ROLECE", date(2027, 1, 1))]
    )
    requirement = FakeRequirement(
        category=RequirementCategory.REGISTRATION,
        value_json={"name": "Inscripcion en el ROLECE"},
        title="ROLECE",
    )

    outcome = assess_registration(requirement, profile, DEADLINE)

    assert outcome.result == AssessmentResult.SATISFIES


def test_a_business_classification_with_group_needs_review():
    """Grup i subgrup no es poden comparar amb text lliure."""
    profile = snapshot(registrations=[])
    requirement = FakeRequirement(
        category=RequirementCategory.BUSINESS_CLASSIFICATION,
        value_json={"name": "Clasificacion", "group": "U", "subgroup": "1", "category": "B"},
    )

    outcome = assess_registration(requirement, profile, DEADLINE)

    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.REGISTRATION_CLASSIFICATION_REVIEW
