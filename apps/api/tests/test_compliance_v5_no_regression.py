"""v5 = v4 per a tot el que no es un requisit `product-v2` de format lliure.

Decisio del controlador (ronda 1): el complement de lectura es estret. Aquesta
prova fixa la meitat que es pot fixar sense el codi antic: per a qualsevol perfil
que no sigui `product-v2`, i per a un `product-v2` amb `value_schema`, el motor
v5 fa EXACTAMENT el `_dispatch` de sempre i no hi deixa cap rastre.

L'altra meitat —que `_dispatch` i les regles donen el mateix `RuleOutcome` que el
codi de `21d5f9e`— la fa la prova diferencial de l'informe (enregistrament de
totes les avaluacions de les proves de compliment i reexecucio amb el codi antic).
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import pytest

from app.ai.contract_v3 import VALUE_SCHEMA_V3
from app.ai.value_schemas import VALUE_SCHEMA_VERSION
from app.compliance.engine import _dispatch, assess_requirement_traced
from app.compliance.profile import ProfileSnapshot
from app.models.enums import InsuranceLimitKind, InsuranceType, RegistrationType
from tests.test_compliance_normalize_product_v2 import ROWS, req

DEADLINE = date(2026, 10, 20)


@dataclass
class Year:
    year: int
    revenue: Decimal
    currency: str = "EUR"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Limit:
    kind: InsuranceLimitKind
    amount: Decimal
    currency: str = "EUR"


@dataclass
class Policy:
    limits: list
    type: InsuranceType = InsuranceType.CIVIL_LIABILITY
    coverage_amount: Decimal | None = Decimal("1000000")
    currency: str = "EUR"
    expires_at: date | None = None
    additional_insured: bool | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Cert:
    name: str
    expires_at: date | None = None
    verification_status: str = "verified"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class Reg:
    type: RegistrationType
    name: str
    expires_at: date | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


def snapshots() -> list[ProfileSnapshot]:
    complete = {
        key: 1.0 for key in ("certifications", "registrations", "insurances", "team", "experience")
    }
    return [
        ProfileSnapshot(company_id=uuid.uuid4()),
        ProfileSnapshot(company_id=uuid.uuid4(), section_ratio=complete),
        ProfileSnapshot(
            company_id=uuid.uuid4(),
            financial_years=[Year(y, Decimal("120000")) for y in (2025, 2024, 2023)],
            insurances=[
                Policy(
                    limits=[
                        Limit(InsuranceLimitKind.PER_CLAIM, Decimal("600000")),
                        Limit(InsuranceLimitKind.EMPLOYER_PER_VICTIM, Decimal("100000")),
                    ]
                )
            ],
            certifications=[Cert("ENS Mitjana"), Cert("ISO 27001")],
            registrations=[Reg(RegistrationType.SECTOR_OPERATOR_REGISTRY, "CNMC")],
            section_ratio=complete,
        ),
    ]


#: Valors sintetics v1 i v2 (els de les sondes del revisor i variants): els que
#: la v5 de la ronda 0 canviava.
SYNTHETIC = [
    ("certification", {"name": "ENS categoria Mitjana", "valid_at_deadline": True}),
    ("certification", {"name": "ENS categoria Mitjana", "equivalents_allowed": True}),
    ("certification", {"names": ["ENS categoria Mitjana", "ISO 27001"]}),
    ("certification", {"name": "ENS Alta"}),
    ("insurance", {"amount": "300.000 €", "unit": "sublímit per ocurrència"}),
    ("insurance", {"amount": 300000, "type": "civil_liability_per_victim"}),
    ("insurance", {"amount": "300.000", "unit": "per sinistre i any"}),
    ("experience", {"minimum_amount": "42.888,88 €", "minimum_projects": 3, "cpv_codes": ["1"]}),
    ("registration", {"name": "Inscripció al registre d'operadors de la CNMC"}),
    ("business_classification", None),
    ("economic_solvency", {"amount": "91.904,74", "period": "millor dels 3 últims exercicis"}),
]


def corpus():
    """Tots els requisits reals de run3 i els sintetics, cadascun com a fila."""
    for prefix in ROWS:
        yield req(prefix)
    base = next(iter(ROWS))
    for category, value in SYNTHETIC:
        from app.models.enums import RequirementCategory

        yield req(base, category=RequirementCategory(category), value_json=copy.deepcopy(value))


@pytest.mark.parametrize("profile", ["v1", "v2", "v3", None])
def test_fora_de_product_v2_el_motor_es_el_de_la_v4(profile):
    cases = 0
    for requirement in corpus():
        for snapshot in snapshots():
            outcome, applied = assess_requirement_traced(
                requirement, snapshot, deadline=DEADLINE, profile=profile
            )
            assert applied is None
            assert outcome == _dispatch(requirement, snapshot, deadline=DEADLINE)
            cases += 1
    assert cases == (len(ROWS) + len(SYNTHETIC)) * 3


@pytest.mark.parametrize("schema", [VALUE_SCHEMA_VERSION, VALUE_SCHEMA_V3])
def test_un_product_v2_amb_value_schema_es_el_de_la_v4(schema):
    for requirement in corpus():
        requirement.value_schema = schema
        for snapshot in snapshots():
            outcome, applied = assess_requirement_traced(
                requirement, snapshot, deadline=DEADLINE, profile="product-v2"
            )
            assert applied is None
            assert outcome == _dispatch(requirement, snapshot, deadline=DEADLINE)
