"""Normalitzador de lectura de format lliure (v1, product-v2), amb el text real de run3.

Les fixtures son l'exportacio de lectura dels requisits de `run3` (6bc58f4b,
product-v2) de L'Escala 2026/3964: el text public del plec, tal com el va
escriure l'extractor (`value_json`) i les seves cites verificades.

Que es demostra:

* cada format real -> el valor que llegeixen les regles, amb la procedencia
  (`value_json` o `quote` + id de la cita);
* l'ambiguitat (dos imports, un format numeric que admet dues lectures, cap font)
  no es normalitza i diu per que;
* res del requisit no canvia (lectura pura);
* l'ENS sense categoria i la documentacio RELI/ROLECE no es converteixen en una
  exigencia inventada;
* abans (sense perfil, com `company-compliance-v4`) -> despres (v5) del motor.
"""

from __future__ import annotations

import copy
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from app.compliance import normalize
from app.compliance.engine import assess_requirement, assess_requirement_traced
from app.compliance.profile import ProfileSnapshot
from app.compliance.rules import ExplanationCode, RuleCode
from app.models.enums import (
    AggregationType,
    AssessmentResult,
    InsuranceLimitKind,
    InsuranceType,
    ProjectStatus,
    RegistrationType,
    RequirementCategory,
    RequirementModality,
    RequirementObligation,
    RequirementScope,
    ReviewStatus,
)

FIXTURE = Path(__file__).parent / "fixtures" / "lescala_run3_requirements.json"
PV2 = "product-v2"


@dataclass
class FakeEvidence:
    id: str
    quote: str
    page_number: int = 1
    char_start: int | None = 0


@dataclass
class FakeRequirement:
    """La forma d'un `Requirement` que llegeixen el normalitzador i les regles."""

    id: uuid.UUID
    category: RequirementCategory
    obligation: RequirementObligation
    title: str
    description: str | None
    value_json: dict[str, Any] | None
    evidence: list[FakeEvidence]
    scope: RequirementScope = RequirementScope.TENDER
    modality: RequirementModality = RequirementModality.UNKNOWN
    value_schema: str | None = None
    operator: Any = None
    equivalence_allowed: bool = False
    alternative_group: str | None = None
    alternative_group_complete: bool | None = None
    tender_lot_id: Any = None
    review_status: ReviewStatus = ReviewStatus.UNREVIEWED
    run_id: uuid.UUID | None = field(default_factory=lambda: RUN_ID)
    created_at: datetime | None = None


RUN_ID = uuid.UUID("6bc58f4b-0000-4000-8000-000000000000")


def _load() -> dict[str, dict[str, Any]]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return {row["id"][:8]: row for row in data["requirements"]}


ROWS = _load()


def req(prefix: str, **overrides: Any) -> FakeRequirement:
    row = copy.deepcopy(ROWS[prefix])
    built = FakeRequirement(
        id=uuid.UUID(row["id"]),
        category=RequirementCategory(row["category"]),
        obligation=RequirementObligation(row["obligation"]),
        title=row["title"],
        description=row["description"],
        value_json=row["value_json"],
        evidence=[
            FakeEvidence(id=e["id"], quote=e["quote"], page_number=e["page"])
            for e in row["evidence"]
        ],
        scope=RequirementScope(row["scope"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )
    for key, value in overrides.items():
        setattr(built, key, value)
    return built


def empty() -> ProfileSnapshot:
    return ProfileSnapshot(company_id=uuid.uuid4())


def norm(prefix: str, **overrides: Any) -> normalize.Normalization:
    result = normalize.normalize_requirement(req(prefix, **overrides), PV2)
    assert result is not None
    return result


def sources(result: normalize.Normalization) -> dict[str, tuple[str, str | None]]:
    return {p.field: (p.source, p.evidence_id) for p in result.provenance}


# --------------------------------------------------------------------------- #
# Formats d'import                                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "amount", "currency"),
    [
        ("91.904,74", Decimal("91904.74"), None),
        ("91.904,74 €", Decimal("91904.74"), "EUR"),
        ("300.000 €", Decimal("300000"), "EUR"),
        ("42.888,88", Decimal("42888.88"), None),
        ("42.888,88 euros", Decimal("42888.88"), "EUR"),
        ("24954,67", Decimal("24954.67"), None),
        (61, Decimal("61"), None),
    ],
)
def test_imports_en_format_local(raw, amount, currency):
    parsed, found_currency, reason = normalize.parse_amount(raw)
    assert (parsed, found_currency, reason) == (amount, currency, None)


@pytest.mark.parametrize(
    "raw",
    [
        "1,5 vegades l'anualitat mitjana",  # un multiple, no un import
        "38326.08",  # punt decimal: en format local el punt es de milers
        "1.5",
        "300.000 USD",
        "",
    ],
)
def test_el_que_no_es_un_import_inequivoc_no_es_llegeix(raw):
    parsed, _, reason = normalize.parse_amount(raw)
    assert parsed is None and reason == normalize.REASON_AMOUNT_UNPARSEABLE


def test_un_multiple_es_un_percentatge_amb_base_i_no_un_import():
    assert normalize.parse_multiple("1,5 vegades l'anualitat mitjana") == Decimal("1.5")
    assert normalize.parse_percentage("70%") == Decimal("70")
    assert normalize.parse_percentage("5") == Decimal("5")


@pytest.mark.parametrize(
    ("text", "years", "aggregation"),
    [
        ("millor dels 3 últims exercicis", 3, AggregationType.MAX),
        ("millor exercici dins dels tres (3) últims disponibles", 3, AggregationType.MAX),
        ("millor exercici dels tres (3) últims disponibles", 3, AggregationType.MAX),
        ("darrers 3 anys", 3, AggregationType.UNKNOWN),
        ("darrers tres (3) anys, any de major execució", 3, AggregationType.MAX),
        ("cadascun dels tres últims exercicis", 3, AggregationType.EACH_YEAR),
    ],
)
def test_periodes(text, years, aggregation):
    assert normalize.parse_years(text) == (years, False)
    assert normalize.parse_aggregation(text) == (aggregation, False)


def test_un_periode_contradictori_es_ambigu():
    assert normalize.parse_years("darrers tres (2) anys") == (None, True)
    assert normalize.parse_years("darrers 3 anys o darrers 5 anys") == (None, True)
    # «l'anualitat mitjana» del contracte no es la mitjana dels exercicis.
    assert normalize.parse_aggregation("70% de l'anualitat mitjana") == (
        AggregationType.UNKNOWN,
        False,
    )


@pytest.mark.parametrize(
    ("text", "kinds"),
    [
        ("per sinistre i any", (InsuranceLimitKind.PER_CLAIM, InsuranceLimitKind.ANNUAL_AGGREGATE)),
        ("per sinistre", (InsuranceLimitKind.PER_CLAIM,)),
        ("per víctima", (InsuranceLimitKind.PER_VICTIM,)),
        (
            "responsabilitat civil patronal amb un sublímit mínim de 150.000 euros per víctima",
            (InsuranceLimitKind.EMPLOYER_PER_VICTIM,),
        ),
    ],
)
def test_bases_d_assegurança(text, kinds):
    assert normalize.limit_kinds(text) == (kinds, False)


# --------------------------------------------------------------------------- #
# Requisits reals de run3                                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prefix", ["29d298c5", "7e78ecdf", "dc7f1e98"])
def test_solvencia_economica_dels_tres_duplicats(prefix):
    result = norm(prefix)
    assert result.status == "normalized"
    value = result.value_json
    assert value["threshold"]["amount"] == "91904.74"
    assert value["threshold"]["currency"] == "EUR"
    assert value["metric"] == "annual_turnover"
    assert value["reference_period"] == {"type": "last_n_years", "years": 3}
    assert value["aggregation"] == "max"
    assert sources(result)["threshold.amount"] == ("value_json", None)
    assert sources(result)["metric"][0] == "quote"
    # Els tres diuen la mateixa exigencia decisiva.
    assert result.consolidation_value() == norm("29d298c5").consolidation_value()


def test_el_multiple_es_conserva_pero_mana_l_import():
    value = norm("29d298c5").value_json
    assert value["threshold"]["percentage"] == "150.0"
    assert value["threshold"]["percentage_base"] == "contract_annuity"


def test_sense_import_el_multiple_queda_com_a_percentatge_sense_base_resoluble():
    """«1,5 vegades l'anualitat mitjana» tot sol: la base (durada) no consta."""
    requirement = req("29d298c5")
    requirement.value_json = {"multiple": "1,5 vegades l'anualitat mitjana"}
    requirement.evidence = [
        FakeEvidence(id="e1", quote="Volum anual de negoci del millor dels 3 últims exercicis")
    ]
    outcome = assess_requirement(requirement, empty(), profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.PERCENTAGE_BASE_UNAVAILABLE
    assert outcome.params["unresolved_reason"] == "contract_duration_unknown"


def test_rc_per_sinistre_i_any_del_value_json():
    result = norm("8ce506d2")
    assert result.value_json["limits"] == [
        {"kind": "per_claim", "amount": "300000", "currency": "EUR"},
        {"kind": "annual_aggregate", "amount": "300000", "currency": "EUR"},
    ]
    assert result.value_json["insurance_type"] == "civil_liability"
    assert sources(result)["limits.amount"] == ("value_json", None)


def test_rc_sense_value_json_surt_de_les_cites_verificades():
    """825ca93c no te `value_json`: les dues cites diuen el mateix import."""
    result = norm("825ca93c")
    assert result.status == "normalized"
    assert result.value_json["limits"] == [
        {"kind": "per_claim", "amount": "300000", "currency": "EUR"},
        {"kind": "annual_aggregate", "amount": "300000", "currency": "EUR"},
    ]
    source, evidence_id = sources(result)["limits.amount"]
    assert source == "quote" and evidence_id.startswith("70ce2c93")
    assert result.consolidation_value() == norm("8ce506d2").consolidation_value()


@pytest.mark.parametrize("prefix", ["667efae4", "fdaecc32"])
def test_rc_patronal_per_victima(prefix):
    result = norm(prefix)
    assert result.value_json["limits"] == [
        {"kind": "employer_per_victim", "amount": "150000", "currency": "EUR"}
    ]
    assert result.value_json["insurance_type"] == "civil_liability"
    assert result.consolidation_value() == norm("667efae4").consolidation_value()


def test_dos_imports_diferents_a_les_cites_no_es_normalitzen():
    requirement = req("825ca93c")
    requirement.evidence = [
        FakeEvidence(id="a", quote="cobertura mínima de 300.000 euros per sinistre i any"),
        FakeEvidence(id="b", quote="un sublímit mínim de 150.000 euros per víctima"),
    ]
    result = normalize.normalize_requirement(requirement, PV2)
    assert (result.status, result.reason) == ("not_normalized", normalize.REASON_AMOUNT_AMBIGUOUS)
    outcome = assess_requirement(requirement, empty(), profile=PV2)
    assert outcome.explanation_code == ExplanationCode.REQUIREMENT_VALUE_MISSING
    assert outcome.params["normalization"]["reason"] == "amount_ambiguous"


def test_una_cita_no_verificada_no_es_font():
    requirement = req("825ca93c")
    for evidence in requirement.evidence:
        evidence.char_start = None
    assert norm_of(requirement).reason == normalize.REASON_NO_SOURCE


def norm_of(requirement):
    return normalize.normalize_requirement(requirement, PV2)


@pytest.mark.parametrize("prefix", ["29c336e8", "d5ea6b03"])
def test_solvencia_tecnica_import_anual_acumulat_del_millor_any(prefix):
    result = norm(prefix)
    value = result.value_json
    assert value["min_accumulated_amount"]["amount"] == "42888.88"
    assert value["min_individual_amount"] is None
    assert value["reference_period"] == {"type": "last_n_years", "years": 3}
    assert value["aggregation"] == "max"
    # «serveis similars»: marca de prudencia (revisio), mai un valor.
    assert value["similarity_note"]
    assert result.consolidation_value() == norm("29c336e8").consolidation_value()


def test_la_similitud_del_titol_nomes_fa_mes_prudent_el_resultat():
    result = norm("d5ea6b03")
    assert sources(result)["similarity_note"] == ("title", None)
    assert sources(result)["amount"] == ("value_json", None)
    outcome = assess_requirement(req("d5ea6b03"), empty(), profile=PV2)
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_SIMILARITY_REVIEW


def test_una_relacio_tallada_sense_import_no_es_normalitza():
    result = norm("5b830e1d")
    assert (result.status, result.reason) == ("not_normalized", normalize.REASON_NO_SOURCE)


@pytest.mark.parametrize("prefix", ["0a860458", "2501fdf1", "5f5d78d4", "7d830438"])
def test_una_llista_de_perfils_no_es_un_recompte(prefix):
    result = norm(prefix)
    assert (result.status, result.reason) == ("not_normalized", normalize.REASON_NO_COUNT)
    outcome = assess_requirement(req(prefix), empty(), profile=PV2)
    assert outcome.explanation_code == ExplanationCode.REQUIREMENT_VALUE_MISSING


# --------------------------------------------------------------------------- #
# ENS, CNMC, RELI/ROLECE                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("prefix", ["8710a5e9", "bda10f82", "e48bb40a"])
def test_l_ens_sense_categoria_va_a_revisio_i_no_se_suposa_mitjana(prefix):

    @dataclass
    class Cert:
        name: str
        id: uuid.UUID = field(default_factory=uuid.uuid4)

    profile = ProfileSnapshot(
        company_id=uuid.uuid4(), certifications=[Cert("ENS categoria Mitjana")]
    )
    outcome = assess_requirement(req(prefix), profile, profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.rule_code == RuleCode.CERTIFICATION_EXACT
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_LEVEL_NOT_STATED
    assert outcome.params["company_certifications"] == ["ENS categoria Mitjana"]
    assert outcome.params["normalization"]["route"] == "ens_level_not_stated"


def test_l_ens_amb_categoria_literal_s_avalua_amb_la_regla_de_certificacio():
    @dataclass
    class Cert:
        name: str
        expires_at: date | None = None
        verification_status: str = "pending_verification"
        id: uuid.UUID = field(default_factory=uuid.uuid4)

    requirement = req("bda10f82")
    requirement.evidence = [
        FakeEvidence(
            id="q",
            quote=(
                "certificació de conformitat amb l'Esquema Nacional de Seguretat, "
                "categoria MITJANA"
            ),
        )
    ]
    result = norm_of(requirement)
    assert result.route == "ens_certification" and result.detail["level"] == "mitjana"
    profile = ProfileSnapshot(
        company_id=uuid.uuid4(), certifications=[Cert("ENS categoria Mitjana")]
    )
    outcome = assess_requirement(requirement, profile, profile=PV2)
    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_FOUND_VALID
    # Una categoria diferent no casa: alta no es mitjana.
    profile_alta = ProfileSnapshot(
        company_id=uuid.uuid4(), certifications=[Cert("ENS categoria Alta")]
    )
    assert (
        assess_requirement(requirement, profile_alta, profile=PV2).result
        == AssessmentResult.UNKNOWN
    )


def test_l_ens_informatiu_no_es_recategoritza():
    requirement = req("bda10f82", obligation=RequirementObligation.INFORMATIVE)
    assert normalize.normalize_requirement(requirement, PV2) is None
    outcome = assess_requirement(requirement, empty(), profile=PV2)
    assert outcome.explanation_code == ExplanationCode.RULE_NOT_AUTOMATABLE


def test_la_documentacio_reli_rolece_no_exigeix_estar_inscrit():
    outcome = assess_requirement(req("81bf423c"), empty(), profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.REGISTRATION_DOCUMENTATION_ONLY
    before = assess_requirement(req("81bf423c"), empty())
    assert before.explanation_code == ExplanationCode.REGISTRATION_DATA_INCOMPLETE


@pytest.mark.parametrize("prefix", ["0a70dd0c", "1739a725", "73800ff7", "fb1fc201"])
def test_la_capacitat_d_operador_sense_nomenar_el_registre_no_s_automatitza(prefix):
    outcome = assess_requirement(req(prefix), empty(), profile=PV2)
    assert outcome.explanation_code == ExplanationCode.RULE_NOT_AUTOMATABLE
    assert outcome.params["normalization"]["reason"] == "registry_not_named_explicitly"


def test_el_registre_d_operadors_nomenat_s_avalua():
    requirement = req("1739a725")
    requirement.evidence = [
        FakeEvidence(id="c", quote="inscripció al Registre d'operadors de la CNMC")
    ]

    @dataclass
    class Reg:
        type: RegistrationType
        name: str
        expires_at: date | None = None
        id: uuid.UUID = field(default_factory=uuid.uuid4)

    profile = ProfileSnapshot(
        company_id=uuid.uuid4(),
        registrations=[Reg(RegistrationType.SECTOR_OPERATOR_REGISTRY, "CNMC")],
    )
    outcome = assess_requirement(requirement, profile, profile=PV2)
    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.REGISTRATION_FOUND_VALID


# --------------------------------------------------------------------------- #
# Motor: abans i despres                                                        #
# --------------------------------------------------------------------------- #


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
    coverage_amount: Decimal | None = None
    currency: str = "EUR"
    expires_at: date | None = None
    additional_insured: bool | None = None
    id: uuid.UUID = field(default_factory=uuid.uuid4)


DEADLINE = date(2026, 10, 20)


@pytest.mark.parametrize("prefix", ["29d298c5", "7e78ecdf", "dc7f1e98"])
def test_economic_abans_revisio_despres_compleix_o_no(prefix):
    before = assess_requirement(req(prefix), empty(), deadline=DEADLINE)
    assert before.explanation_code == ExplanationCode.REQUIREMENT_VALUE_MISSING

    rich = ProfileSnapshot(company_id=uuid.uuid4(), financial_years=[Year(2024, Decimal("120000"))])
    after = assess_requirement(req(prefix), rich, deadline=DEADLINE, profile=PV2)
    assert after.result == AssessmentResult.SATISFIES
    assert after.params["required"] == 91904.74
    assert after.params["normalization"]["status"] == "normalized"

    poor = ProfileSnapshot(
        company_id=uuid.uuid4(),
        financial_years=[Year(y, Decimal("50000")) for y in (2025, 2024, 2023)],
    )
    assert (
        assess_requirement(req(prefix), poor, deadline=DEADLINE, profile=PV2).result
        == AssessmentResult.DOES_NOT_SATISFY
    )
    # Sense exercicis: «sense dades», no «no compleix».
    assert assess_requirement(req(prefix), empty(), deadline=DEADLINE, profile=PV2).result == (
        AssessmentResult.UNKNOWN
    )


def test_rc_abans_revisio_despres_compara_cada_limit():
    covered = ProfileSnapshot(
        company_id=uuid.uuid4(),
        insurances=[
            Policy(
                limits=[
                    Limit(InsuranceLimitKind.PER_CLAIM, Decimal("600000")),
                    Limit(InsuranceLimitKind.ANNUAL_AGGREGATE, Decimal("600000")),
                    Limit(InsuranceLimitKind.EMPLOYER_PER_VICTIM, Decimal("150000")),
                ]
            )
        ],
    )
    for prefix in ("8ce506d2", "825ca93c", "667efae4", "fdaecc32"):
        before = assess_requirement(req(prefix), covered, deadline=DEADLINE)
        assert before.explanation_code == ExplanationCode.REQUIREMENT_VALUE_MISSING, prefix
        after = assess_requirement(req(prefix), covered, deadline=DEADLINE, profile=PV2)
        assert after.result == AssessmentResult.SATISFIES, prefix


def test_el_sublimit_patronal_no_el_respon_la_xifra_global():
    """v5: abans un `employer_per_victim` exigit es comparava amb el total."""
    only_total = ProfileSnapshot(
        company_id=uuid.uuid4(),
        insurances=[Policy(limits=[], coverage_amount=Decimal("2000000"))],
    )
    outcome = assess_requirement(req("667efae4"), only_total, deadline=DEADLINE, profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_UNKNOWN


def test_un_import_inferior_es_un_incompliment_demostrat():
    low = ProfileSnapshot(
        company_id=uuid.uuid4(),
        insurances=[Policy(limits=[Limit(InsuranceLimitKind.PER_CLAIM, Decimal("100000"))])],
    )
    outcome = assess_requirement(req("8ce506d2"), low, deadline=DEADLINE, profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_BELOW


def test_la_normalitzacio_no_modifica_el_requisit():
    for prefix in ROWS:
        requirement = req(prefix)
        snapshot_before = copy.deepcopy(
            (
                requirement.value_json,
                requirement.category,
                requirement.title,
                requirement.obligation,
            )
        )
        assess_requirement_traced(requirement, empty(), deadline=DEADLINE, profile=PV2)
        assert (
            requirement.value_json,
            requirement.category,
            requirement.title,
            requirement.obligation,
        ) == snapshot_before, prefix


def test_un_perfil_tipat_no_es_normalitza():
    for prefix in ROWS:
        assert normalize.normalize_requirement(req(prefix), "v2") is None
        assert normalize.normalize_requirement(req(prefix), None) is None


def test_un_v1_que_ja_es_llegia_es_llegeix_igual():
    requirement = req("7e78ecdf", value_json={"amount": 91904.74, "currency": "EUR"})
    outcome, applied = assess_requirement_traced(requirement, empty(), profile="v1")
    assert applied is None and "normalization" not in outcome.params
    # Pero un import en text (que `to_decimal` llegiria com 300 i no 300.000) si.
    requirement = req("8ce506d2", value_json={"amount": "300.000", "unit": "per sinistre i any"})
    _, applied = assess_requirement_traced(requirement, empty(), profile="v1")
    assert applied is not None and applied.value_json["limits"][0]["amount"] == "300000"


def test_cada_valor_porta_la_procedencia_als_params():
    outcome = assess_requirement(req("825ca93c"), empty(), deadline=DEADLINE, profile=PV2)
    provenance = outcome.params["normalization"]["provenance"]
    assert {"field": "limits.amount", "source": "quote"}.items() <= provenance[0].items()
    assert provenance[0]["evidence_id"].startswith("70ce2c93")
    assert outcome.params["normalization"]["version"] == normalize.NORMALIZER_VERSION


def test_experiencia_amb_projectes_simulats_va_a_revisio_per_similitud():
    """Sense CPV ni categories al plec, la similitud la jutja una persona."""

    @dataclass
    class Project:
        name: str
        amount: Decimal
        end_date: date
        currency: str = "EUR"
        status: ProjectStatus = ProjectStatus.COMPLETED
        start_date: date | None = None
        cpvs: list = field(default_factory=list)
        services: list = field(default_factory=list)
        id: uuid.UUID = field(default_factory=uuid.uuid4)

    profile = ProfileSnapshot(
        company_id=uuid.uuid4(),
        projects=[Project("Xarxa FTTH", Decimal("60000"), date(2025, 6, 1))],
    )
    outcome = assess_requirement(req("29c336e8"), profile, deadline=DEADLINE, profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.EXPERIENCE_SIMILARITY_REVIEW


@pytest.mark.parametrize("prefix", ["3662d496", "40d49f88", "bceceeca"])
def test_la_classificacio_que_el_plec_diu_que_no_s_exigeix_no_aplica(prefix):
    """Abans: «sense dades» (i, amb el perfil complet, «no compleix»)."""
    before = assess_requirement(req(prefix), empty())
    assert before.explanation_code == ExplanationCode.REGISTRATION_DATA_INCOMPLETE
    complete = ProfileSnapshot(company_id=uuid.uuid4(), section_ratio={"registrations": 1.0})
    assert assess_requirement(req(prefix), complete).result == AssessmentResult.DOES_NOT_SATISFY

    after = assess_requirement(req(prefix), complete, profile=PV2)
    assert after.result == AssessmentResult.NOT_APPLICABLE
    assert after.explanation_code == ExplanationCode.REQUIREMENT_NOT_REQUIRED
    assert after.params["normalization"]["provenance"][0]["source"] == "quote"


def test_un_obligatori_que_diu_no_s_exigeix_no_es_resol_sol():
    requirement = req("40d49f88", obligation=RequirementObligation.MANDATORY)
    outcome = assess_requirement(requirement, empty(), profile=PV2)
    assert outcome.explanation_code == ExplanationCode.REGISTRATION_DATA_INCOMPLETE
