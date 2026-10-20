"""Assegurances: cada limit es una condicio, i totes han de complir-se.

Una polissa que cobreix 1.000.000 en total pero 300.000 per victima no compleix
un plec que exigeix 500.000 per victima, encara que la xifra grossa impressioni.
La v1 aplanava els dos imports en un i es quedava amb el gran, que es el error
mes car possible: fa semblar coberta una empresa que no ho esta.

La regla d'or d'aquest modul es la que decideix els casos frontera:

* limit declarat i suficient -> compleix;
* limit declarat i insuficient -> **no compleix**, i aixo si que es un
  incompliment demostrat;
* limit **no declarat** -> `needs_review`. No saber quin sublimit te una polissa
  no vol dir que no en tingui. Aqui no s'assumeix ni que compleix ni que no.

Un limit de tipus `unknown` —el que produeix una fitxa v1, que nomes tenia un
import— pot respondre el limit total, perque es el que aquell camp sempre ha
volgut dir. No pot respondre cap sublimit: dir que un import global cobreix la
condicio "per victima" seria exactament l'aplanament que s'esta corregint.

Com sempre, sense conversio de divisa.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from typing import Any

from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import (
    EvidenceRef,
    ExplanationCode,
    RuleCode,
    RuleOutcome,
    needs_review,
    unknown,
)
from app.compliance.specs import InsuranceSpec, LimitSpec, read_insurance
from app.models.enums import (
    AssessmentMode,
    AssessmentResult,
    CompanyEvidenceKind,
    DocumentCategory,
    EvidenceStrength,
    InsuranceLimitKind,
    InsuranceType,
    LinkedEntityType,
)

#: Com anomena el plec cada tipus de polissa. Nomes families inequivoques.
_TYPE_HINTS: tuple[tuple[re.Pattern[str], InsuranceType], ...] = (
    (re.compile(r"responsabilidad\s+civil|responsabilitat\s+civil|civil\s+liability", re.I),
     InsuranceType.CIVIL_LIABILITY),
    (re.compile(r"indemnizaci[oó]n\s+profesional|professional\s+indemnity", re.I),
     InsuranceType.PROFESSIONAL_INDEMNITY),
    (re.compile(r"todo\s+riesgo|all\s+risk", re.I), InsuranceType.ALL_RISK),
    (re.compile(r"aval|surety|caucion|cauci[oó]n", re.I), InsuranceType.SURETY_BOND),
)

#: El limit sense tipus d'una fitxa v1 nomes pot respondre el total: es el que
#: `coverage_amount` sempre ha volgut dir. Mai un sublimit.
_ANSWERABLE_BY_UNTYPED = frozenset({InsuranceLimitKind.TOTAL})

#: Limits que NO pot respondre mai un import sense tipus. Existeix com a llista
#: explicita i no com a "tot el que no sigui total" perque afegir un tipus de
#: sublimit nou sense decidir que se'n fa el deixaria caure al costat permissiu.
_SUBLIMIT_KINDS = frozenset(
    {
        InsuranceLimitKind.PER_VICTIM,
        InsuranceLimitKind.PER_CLAIM,
        InsuranceLimitKind.ANNUAL_AGGREGATE,
        # v5: el sublimit patronal per victima (0052) i un sublimit generic
        # tampoc no els respon mai la xifra global. Abans queien a la branca de
        # «cobertura contra cobertura» i es comparaven amb el total.
        InsuranceLimitKind.EMPLOYER_PER_VICTIM,
        InsuranceLimitKind.SUBLIMIT,
    }
)


def infer_insurance_type(raw: str | None) -> InsuranceType | None:
    if not raw:
        return None
    for pattern, kind in _TYPE_HINTS:
        if pattern.search(raw):
            return kind
    return None


def _company_limits(policy: Any) -> dict[InsuranceLimitKind, tuple[Decimal, str]]:
    """Els limits declarats d'una polissa, indexats pel seu tipus.

    `coverage_amount` hi entra com a limit de tipus desconegut si no hi ha cap
    fila que ja el reculli: es la dada que existeix a les fitxes d'abans i no es
    pot ignorar sense perdre el compliment que ja funcionava.
    """
    limits: dict[InsuranceLimitKind, tuple[Decimal, str]] = {}
    for limit in getattr(policy, "limits", []) or []:
        if limit.amount is None:
            continue
        limits[limit.kind] = (Decimal(limit.amount), (limit.currency or "EUR").upper())
    if policy.coverage_amount is not None and not limits:
        limits[InsuranceLimitKind.UNKNOWN] = (
            Decimal(policy.coverage_amount),
            (policy.currency or "EUR").upper(),
        )
    return limits


class _Match:
    """El limit declarat que respon —o no— a un limit exigit."""

    __slots__ = ("amount", "currency")

    def __init__(self, amount: Decimal | None, currency: str = "EUR") -> None:
        self.amount = amount
        self.currency = currency


def _find(
    declared: dict[InsuranceLimitKind, tuple[Decimal, str]], required: LimitSpec
) -> _Match:
    """El limit declarat que respon a un limit exigit, si n'hi ha cap.

    Quatre regles, i totes son la mateixa idea:

    * un tipus exigit el respon **el mateix tipus declarat**;
    * un **sublimit** exigit no el respon mai un import global, digui el que
      digui la xifra;
    * el **total** exigit si que el respon la xifra unica d'una fitxa antiga:
      `coverage_amount` sempre ha volgut dir aixo;
    * quan el PLEC tampoc diu de quin limit parla, la xifra global de la polissa
      el respon. Aqui **no** s'esta comparant un total contra un sublimit: el
      cas perillos es un sublimit disfressat de xifra global, i aquell no arriba
      mai fins aqui —`read_limit_kind` el marca com a ambigu i el requisit acaba
      en revisio abans de comparar cap import—. El que queda es cobertura contra
      cobertura, que es l'unica forma que tenia la v1 i segueix sent el cas
      d'assegurança mes frequent: degradar-lo a revisio seria soroll.
    """
    if required.kind != InsuranceLimitKind.UNKNOWN and required.kind in declared:
        # Els dos costats diuen el mateix tipus. `unknown` queda fora a
        # proposit: coincidir en "no ho sabem" no es coincidir.
        amount, currency = declared[required.kind]
        return _Match(amount, currency)
    if required.kind in _SUBLIMIT_KINDS:
        # Un sublimit exigit no el respon mai un import global.
        return _Match(None)
    if required.kind in _ANSWERABLE_BY_UNTYPED:
        # El total exigit si que el respon la xifra unica d'una fitxa antiga:
        # es exactament el que aquell camp ha volgut dir sempre.
        found = declared.get(InsuranceLimitKind.UNKNOWN) or declared.get(
            InsuranceLimitKind.TOTAL
        )
        if found is not None:
            return _Match(found[0], found[1])
        return _Match(None)
    # Queda el cas en que el PLEC tampoc diu de quin limit parla: cobertura
    # contra cobertura (veure el docstring).
    found = declared.get(InsuranceLimitKind.TOTAL) or declared.get(
        InsuranceLimitKind.UNKNOWN
    )
    if found is not None:
        return _Match(found[0], found[1])
    return _Match(None)


def assess_insurance(
    requirement: Any,
    snapshot: ProfileSnapshot,
    deadline: date | None = None,
    tender: Any = None,
) -> RuleOutcome:
    spec: InsuranceSpec = read_insurance(requirement)
    if spec.is_empty:
        return needs_review(
            RuleCode.INSURANCE_LIMITS, ExplanationCode.REQUIREMENT_VALUE_MISSING
        )
    if spec.ambiguous_limit:
        # El plec anuncia un sublimit i no diu de quin. Comparar-lo amb el que
        # sigui seria inventar-se quina condicio s'esta comprovant.
        return needs_review(
            RuleCode.INSURANCE_LIMITS,
            ExplanationCode.INSURANCE_LIMIT_KIND_AMBIGUOUS,
            declared_kind=spec.ambiguous_limit,
        )

    required_type = spec.insurance_type or infer_insurance_type(
        spec.type_note or getattr(requirement, "title", None)
    )
    candidates = list(snapshot.insurances)
    if required_type is not None:
        candidates = [ins for ins in candidates if ins.type == required_type]

    if not candidates:
        if snapshot.is_section_complete("insurances"):
            return RuleOutcome(
                result=AssessmentResult.DOES_NOT_SATISFY,
                rule_code=RuleCode.INSURANCE_LIMITS,
                explanation_code=ExplanationCode.INSURANCE_NOT_FOUND_PROFILE_COMPLETE,
                params={
                    "required_limits": _describe(spec.limits),
                    "required_type": str(required_type) if required_type else None,
                },
                mode=AssessmentMode.AUTOMATIC,
            )
        return unknown(
            RuleCode.INSURANCE_LIMITS,
            ExplanationCode.INSURANCE_DATA_INCOMPLETE,
            required_limits=_describe(spec.limits),
        )

    # La millor polissa es la que resol mes condicions; en cap cas es combinen
    # limits de polisses diferents, que serien contractes diferents.
    evaluations = [(policy, _evaluate(policy, spec, deadline)) for policy in candidates]
    evaluations.sort(key=lambda pair: pair[1].rank)
    policy, verdict = evaluations[0]
    return _to_outcome(snapshot, policy, spec, verdict)


class _Verdict:
    """El resultat de comparar una polissa amb totes les condicions del plec."""

    __slots__ = (
        "result", "explanation", "checked", "missing", "below", "expired",
        "mismatched", "unchecked", "additional_insured",
    )

    def __init__(self) -> None:
        self.result = AssessmentResult.SATISFIES
        self.explanation = ExplanationCode.INSURANCE_LIMITS_MET
        self.checked: list[dict[str, Any]] = []
        self.missing: list[str] = []
        self.below: list[str] = []
        self.mismatched: list[str] = []
        self.unchecked: list[str] = []
        #: `None` no comprovat, `True` compleix, `False` incompleix.
        self.additional_insured: bool | None = None
        self.expired: bool = False

    @property
    def rank(self) -> int:
        """Ordre de preferencia: complir, no saber, no complir."""
        return {
            AssessmentResult.SATISFIES: 0,
            AssessmentResult.NEEDS_REVIEW: 1,
            AssessmentResult.DOES_NOT_SATISFY: 2,
        }[self.result]


def _evaluate(policy: Any, spec: InsuranceSpec, deadline: date | None) -> _Verdict:
    declared = _company_limits(policy)
    verdict = _Verdict()

    for required in spec.limits:
        found = _find(declared, required)
        entry: dict[str, Any] = {
            "kind": str(required.kind),
            "required": float(required.amount),
            "currency": required.currency,
        }
        if found.amount is None:
            entry["declared"] = None
            verdict.missing.append(str(required.kind))
            verdict.checked.append(entry)
            continue
        amount, currency = found.amount, found.currency
        entry["declared"] = float(amount)
        entry["declared_currency"] = currency
        if currency != required.currency:
            # Sense conversio de divisa. Aixo no es "no ho sabem": es una
            # polissa en una altra moneda, i qui ho resolgui ho ha de saber.
            entry["mismatch"] = True
            verdict.mismatched.append(str(required.kind))
            verdict.checked.append(entry)
            continue
        entry["meets"] = amount >= required.amount
        if amount < required.amount:
            verdict.below.append(str(required.kind))
        verdict.checked.append(entry)

    if spec.additional_insured_required:
        declared_flag = getattr(policy, "additional_insured", None)
        verdict.additional_insured = declared_flag
        verdict.checked.append(
            {"kind": "additional_insured", "required": True, "declared": declared_flag}
        )

    verdict.unchecked = list(spec.unchecked_conditions)

    # L'ordre importa i es el de la certesa. Un incompliment demostrat mana
    # sobre qualsevol dada que falti: cap informacio futura pot desfer-lo.
    if verdict.below:
        verdict.result = AssessmentResult.DOES_NOT_SATISFY
        verdict.explanation = ExplanationCode.INSURANCE_LIMIT_BELOW
        return verdict
    if verdict.additional_insured is False:
        # La polissa diu explicitament que no inclou l'organ com a assegurat.
        verdict.result = AssessmentResult.DOES_NOT_SATISFY
        verdict.explanation = ExplanationCode.INSURANCE_ADDITIONAL_INSURED_MISSING
        return verdict
    if policy.expires_at is not None and deadline is not None and policy.expires_at < deadline:
        verdict.result = AssessmentResult.DOES_NOT_SATISFY
        verdict.explanation = ExplanationCode.INSURANCE_EXPIRED
        verdict.expired = True
        return verdict

    # A partir d'aqui no hi ha cap incompliment demostrat, pero pot faltar
    # informacio. Res d'aixo pot acabar en `satisfies`.
    if verdict.mismatched:
        verdict.result = AssessmentResult.NEEDS_REVIEW
        verdict.explanation = ExplanationCode.INSURANCE_CURRENCY_MISMATCH
        return verdict
    if verdict.missing:
        verdict.result = AssessmentResult.NEEDS_REVIEW
        verdict.explanation = ExplanationCode.INSURANCE_LIMIT_UNKNOWN
        return verdict
    if spec.additional_insured_required and verdict.additional_insured is None:
        # No consta si la polissa inclou l'organ. No consta no es "no".
        verdict.result = AssessmentResult.NEEDS_REVIEW
        verdict.explanation = ExplanationCode.INSURANCE_ADDITIONAL_INSURED_UNKNOWN
        return verdict
    if verdict.unchecked:
        # Els imports encaixen, pero el plec imposa alguna condicio que aquesta
        # capa no comprova. Dir `satisfies` seria afirmar mes del que s'ha mirat.
        verdict.result = AssessmentResult.NEEDS_REVIEW
        verdict.explanation = ExplanationCode.INSURANCE_CONDITION_NOT_CHECKED
        return verdict
    return verdict


def _describe(limits: tuple[LimitSpec, ...]) -> list[dict[str, Any]]:
    return [
        {"kind": str(limit.kind), "amount": float(limit.amount), "currency": limit.currency}
        for limit in limits
    ]


def _to_outcome(
    snapshot: ProfileSnapshot, policy: Any, spec: InsuranceSpec, verdict: _Verdict
) -> RuleOutcome:
    documents = snapshot.documents_for(LinkedEntityType.COMPANY_INSURANCE, policy.id)
    certificate = next((d for d in documents if d.category == DocumentCategory.INSURANCE), None)
    best_document = certificate or (documents[0] if documents else None)
    strength = (
        EvidenceStrength.STRONG
        if certificate
        else (EvidenceStrength.SUPPORTING if best_document else EvidenceStrength.MISSING)
    )
    declared = _company_limits(policy)
    evidence = (
        EvidenceRef(
            kind=CompanyEvidenceKind.INSURANCE,
            entity_id=policy.id,
            label=f"{policy.type}: "
            + ", ".join(
                f"{kind} {amount:.2f} {currency}"
                for kind, (amount, currency) in declared.items()
            )
            or str(policy.type),
            strength=strength,
            document_id=best_document.id if best_document else None,
        ),
    )
    company_value = {
        "type": str(policy.type),
        "limits": [
            {"kind": str(kind), "amount": float(amount), "currency": currency}
            for kind, (amount, currency) in declared.items()
        ],
        "expires_at": str(policy.expires_at) if policy.expires_at else None,
        "additional_insured": policy.additional_insured,
    }
    params: dict[str, Any] = {
        "required_limits": _describe(spec.limits),
        "checked": verdict.checked,
    }
    if verdict.missing:
        params["undeclared_limits"] = verdict.missing
    if verdict.mismatched:
        params["currency_mismatch_limits"] = verdict.mismatched
    if verdict.below:
        params["insufficient_limits"] = verdict.below
    if verdict.expired:
        params["expires_at"] = str(policy.expires_at)
    if verdict.unchecked:
        params["unchecked_conditions"] = verdict.unchecked
    if spec.additional_insured_required:
        params["additional_insured_required"] = True
        params["additional_insured_declared"] = verdict.additional_insured

    return RuleOutcome(
        result=verdict.result,
        rule_code=RuleCode.INSURANCE_LIMITS,
        explanation_code=verdict.explanation,
        params=params,
        mode=(
            AssessmentMode.REVIEW_REQUIRED
            if verdict.result == AssessmentResult.NEEDS_REVIEW
            else AssessmentMode.AUTOMATIC
        ),
        company_value=company_value,
        evidence=evidence,
        strength=strength,
    )
