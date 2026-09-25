"""On es treballa cada requisit dins d'una candidatura. **Una sola regla.**

Un requisit del plec no demana sempre la mateixa feina. El DEUC o la capacitat
d'obrar s'acrediten amb documents (Dossier, Compliment); un SLA o un pla de
migracio demanen una solucio tecnica; un termini es una data del Pla de treball.
Tractar-los tots com a "Defineix una solucio" fa que la pantalla tecnica demani
una cosa que no te sentit, i que la revisio d'abast signi coses que no son abast.

La regla es deterministica i nomes llegeix camps que ja existeixen
(`category`, `obligation`). No amaga res: la destinacio diu on es treballa el
requisit, no si es veu. Requisits i Compliment els continuen mostrant tots, i el
Dossier segueix fent servir els seus grups (`app/workspace/dossier.py`).

El frontend NO la reimplementa: la rep ja calculada a `RequirementRead.routing`.
El backend la fa servir a l'abast tecnic (`app/services/technical_scope.py`),
que es d'on surten la creacio de solucions, la revisio d'abast i la preparacio.

**Conservadora a proposit.** Tot el que no encaixa clarament va a
`review_routing`: es mostra com a pendent de classificar i no s'hi exigeix cap
solucio automaticament, pero tampoc no desapareix.
"""

from __future__ import annotations

from typing import Literal, Protocol

from app.models.enums import RequirementCategory, RequirementObligation

RequirementRouting = Literal[
    "compliance_dossier", "technical_solution", "work_plan", "review_routing"
]

COMPLIANCE_DOSSIER: RequirementRouting = "compliance_dossier"
TECHNICAL_SOLUTION: RequirementRouting = "technical_solution"
WORK_PLAN: RequirementRouting = "work_plan"
REVIEW_ROUTING: RequirementRouting = "review_routing"

#: S'acrediten amb documents o dades de l'empresa: Dossier i Compliment.
COMPLIANCE_CATEGORIES = frozenset(
    {
        RequirementCategory.ADMINISTRATIVE_DOCUMENTATION,
        RequirementCategory.ECONOMIC_SOLVENCY,
        RequirementCategory.TECHNICAL_SOLVENCY,
        RequirementCategory.EXPERIENCE,
        RequirementCategory.CERTIFICATION,
        RequirementCategory.REGISTRATION,
        RequirementCategory.BUSINESS_CLASSIFICATION,
        RequirementCategory.INSURANCE,
        RequirementCategory.PROFESSIONAL_QUALIFICATION,
    }
)

#: Demanen explicar com es resoldra: Solucio tecnica.
TECHNICAL_CATEGORIES = frozenset(
    {
        RequirementCategory.TECHNICAL_REQUIREMENT,
        RequirementCategory.EQUIPMENT_OR_RESOURCES,
        RequirementCategory.PERSONNEL,
    }
)

#: Una obligacio clara. `informative` no demana res i `unknown` es ambigu.
_SOLUTION_OBLIGATIONS = frozenset(
    {RequirementObligation.MANDATORY, RequirementObligation.SCORED}
)


class RoutableRequirement(Protocol):
    category: RequirementCategory | str
    obligation: RequirementObligation | str


def route_requirement(requirement: RoutableRequirement) -> RequirementRouting:
    """La destinacio efectiva d'un requisit. Pura: no llegeix ni escriu res."""
    category = _as_enum(RequirementCategory, requirement.category)
    obligation = _as_enum(RequirementObligation, requirement.obligation)
    if category in COMPLIANCE_CATEGORIES:
        return COMPLIANCE_DOSSIER
    if category == RequirementCategory.DEADLINE:
        return WORK_PLAN
    if category in TECHNICAL_CATEGORIES and obligation in _SOLUTION_OBLIGATIONS:
        return TECHNICAL_SOLUTION
    # contract_condition, other, un tecnic informatiu o d'obligacio desconeguda,
    # o qualsevol valor que no es reconegui.
    return REVIEW_ROUTING


def _as_enum(enum_type, value):
    try:
        return enum_type(value)
    except ValueError:
        return None
