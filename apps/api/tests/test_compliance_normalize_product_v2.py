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
    assert outcome.params["side"] == "tender"
    assert outcome.params["normalization"]["route"] == "ens_certification"
    assert outcome.params["normalization"]["ens"]["level"] is None


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
                "certificació de conformitat amb l'Esquema Nacional de Seguretat categoria MITJANA"
            ),
        )
    ]
    result = norm_of(requirement)
    assert result.route == "ens_certification" and result.ens.level == "mitjana"
    profile = ProfileSnapshot(
        company_id=uuid.uuid4(), certifications=[Cert("ENS categoria Mitjana")]
    )
    outcome = assess_requirement(requirement, profile, profile=PV2)
    assert outcome.result == AssessmentResult.SATISFIES
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_FOUND_VALID
    # Una categoria superior sense «o superior» no casa sola: revisio, mai «no compleix».
    profile_alta = ProfileSnapshot(
        company_id=uuid.uuid4(), certifications=[Cert("ENS categoria Alta")]
    )
    outcome = assess_requirement(requirement, profile_alta, profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_LEVEL_HIGHER_REVIEW


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
        if prefix == "825ca93c":
            # La cita diu «mantenir vigent durant tota l'execució»: una condicio que
            # cap regla no comprova. Com un v2 tipat: revisio, no «compleix».
            assert after.result == AssessmentResult.NEEDS_REVIEW, prefix
            assert after.explanation_code == ExplanationCode.INSURANCE_CONDITION_NOT_CHECKED
            assert after.params["unchecked_conditions"] == ["valid_through_contract"]
        else:
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


@pytest.mark.parametrize("profile", ["v1", "v2", "v3", "gold", None])
def test_fora_de_product_v2_no_es_normalitza_mai(profile):
    for prefix in ROWS:
        assert normalize.normalize_requirement(req(prefix), profile) is None, prefix


def test_un_requisit_amb_value_schema_no_es_normalitza_ni_en_product_v2():
    from app.ai.contract_v3 import VALUE_SCHEMA_V3
    from app.ai.value_schemas import VALUE_SCHEMA_VERSION

    for schema in (VALUE_SCHEMA_VERSION, VALUE_SCHEMA_V3):
        for prefix in ROWS:
            assert normalize.normalize_requirement(req(prefix, value_schema=schema), PV2) is None


def test_un_manual_no_es_normalitza():
    assert normalize.normalize_requirement(req("8ce506d2", run_id=None), PV2) is None


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


# --------------------------------------------------------------------------- #
# Ronda 1 de correccions (review/CODE_REVIEW.md)                               #
# --------------------------------------------------------------------------- #


def mk(category, value, quotes, title="t", obligation=RequirementObligation.MANDATORY):
    return FakeRequirement(
        id=uuid.uuid4(),
        category=category,
        obligation=obligation,
        title=title,
        description=None,
        value_json=value,
        evidence=[FakeEvidence(id=f"q{i}", quote=q) for i, q in enumerate(quotes)],
    )


@dataclass
class EnsCert:
    name: str
    expires_at: date | None = None
    verification_status: str = "verified"
    id: uuid.UUID = field(default_factory=uuid.uuid4)


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("ENS Alta", "alta"),
        ("ENS Mitjana", "mitjana"),
        ("ENS Media", "mitjana"),
        ("ENS (categoria Mitjana)", "mitjana"),
        ("ENS (categoria: MEDIA)", "mitjana"),
        ("ENS - MEDIA", "mitjana"),
        ("Certificat ENS categoria MEDIA", "mitjana"),
        ("Certificat ENS nivell MITJÀ", "mitjana"),
        ("Esquema Nacional de Seguretat de nivell bàsic", "basica"),
        ("Esquema Nacional de Seguridad categoría BÁSICA", "basica"),
        # IR-1: una coma trenca el sintagma; cap categoria.
        ("Esquema Nacional de Seguridad, categoría BÁSICA", None),
        ("ENS (RD 311/2022)", None),
        ("Aplicació de l'ENS i encàrrec de tractament", None),
    ],
)
def test_c1_el_nivell_de_l_ens_amb_o_sense_categoria(text, level):
    assert normalize.ens_level(text) == (level, False)


def test_c1_dues_categories_son_ambigues():
    assert normalize.ens_level("ENS Alta o ENS Mitjana") == (None, True)


_ENS_CASES = [
    ("categoria MITJANA", "ENS Mitjana", AssessmentResult.SATISFIES, "CERTIFICATION_FOUND_VALID"),
    (
        "categoria ALTA",
        "ENS Mitjana",
        AssessmentResult.DOES_NOT_SATISFY,
        "CERTIFICATION_LEVEL_BELOW",
    ),
    (
        "categoria MITJANA",
        "ENS Alta",
        AssessmentResult.NEEDS_REVIEW,
        "CERTIFICATION_LEVEL_HIGHER_REVIEW",
    ),
    (
        "categoria MITJANA o superior",
        "ENS Alta",
        AssessmentResult.SATISFIES,
        "CERTIFICATION_FOUND_VALID",
    ),
    (
        "com a mínim de categoria MITJANA",
        "ENS Alta",
        AssessmentResult.SATISFIES,
        "CERTIFICATION_FOUND_VALID",
    ),
    # Una certificacio de l'empresa sense categoria llegible no casa amb cap.
    ("categoria MITJANA", "ENS", AssessmentResult.NEEDS_REVIEW, "CERTIFICATION_LEVEL_NOT_STATED"),
]


@pytest.mark.parametrize(("required", "company", "result", "code"), _ENS_CASES)
def test_c1_i4_comparacio_de_categories_de_l_ens(required, company, result, code):
    requirement = mk(
        RequirementCategory.TECHNICAL_REQUIREMENT,
        None,
        [f"Certificació de conformitat amb l'Esquema Nacional de Seguretat {required}"],
    )
    profile = ProfileSnapshot(
        company_id=uuid.uuid4(),
        certifications=[EnsCert(company)],
        section_ratio={"certifications": 1.0},
    )
    outcome = assess_requirement(requirement, profile, profile=PV2)
    assert (outcome.result, outcome.explanation_code) == (result, code)


def test_c1_un_v2_tipat_que_demana_ens_alta_es_llegeix_com_a_la_v4():
    """La comparacio de noms de sempre no es toca (cap `ens:` comu)."""
    from app.ai.value_schemas import VALUE_SCHEMA_VERSION
    from app.compliance.certifications import normalize_certification_name
    from app.compliance.engine import _dispatch

    assert normalize_certification_name("ENS Alta") != normalize_certification_name("ENS Mitjana")
    requirement = mk(
        RequirementCategory.CERTIFICATION, {"kind": "certification", "name": "ENS Alta"}, []
    )
    requirement.value_schema = VALUE_SCHEMA_VERSION
    profile = ProfileSnapshot(
        company_id=uuid.uuid4(),
        certifications=[EnsCert("ENS Mitjana")],
        section_ratio={"certifications": 1.0},
    )
    for name in ("v2", PV2, None):
        outcome = assess_requirement(requirement, profile, profile=name)
        assert outcome == _dispatch(requirement, profile)
        assert outcome.result != AssessmentResult.SATISFIES


def test_c2_l_ens_amb_condicions_que_no_es_saben_traslladar_no_es_normalitza():
    requirement = mk(
        RequirementCategory.CERTIFICATION, {"names": ["ENS categoria Mitjana", "ISO 27001"]}, []
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert (result.status, result.reason) == ("not_normalized", "unmapped_conditions")


def test_c2_l_ens_conserva_la_validesa_al_termini():
    requirement = mk(
        RequirementCategory.CERTIFICATION,
        {"name": "ENS categoria Mitjana", "valid_at_deadline": True},
        [],
    )
    profile = ProfileSnapshot(
        company_id=uuid.uuid4(),
        certifications=[EnsCert("ENS categoria Mitjana", expires_at=date(2025, 1, 1))],
        section_ratio={"certifications": 1.0},
    )
    outcome = assess_requirement(requirement, profile, deadline=DEADLINE, profile=PV2)
    assert outcome.result == AssessmentResult.DOES_NOT_SATISFY
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_FOUND_EXPIRED


@pytest.mark.parametrize(
    "unit", ["sublímit per ocurrència", "per assegurat", "sublímit", "per sinistre i per víctima"]
)
def test_c3_i3_un_sublimit_o_una_base_ambigua_no_es_compara_mai(unit):
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €", "unit": unit},
        ["pòlissa de responsabilitat civil amb una cobertura mínima de 300.000 €"],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert (result.status, result.reason) == ("not_normalized", "limit_kind_ambiguous")
    big = ProfileSnapshot(
        company_id=uuid.uuid4(),
        insurances=[Policy(limits=[], coverage_amount=Decimal("1000000"))],
        section_ratio={"insurances": 1.0},
    )
    outcome = assess_requirement(requirement, big, deadline=DEADLINE, profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW


def test_c3_cap_tipus_i_cap_marca_es_cobertura_contra_cobertura_com_la_v1():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €"},
        ["pòlissa de responsabilitat civil amb una cobertura mínima de 300.000 €"],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert result.value_json["limits"] == [
        {"kind": "unknown", "amount": "300000", "currency": "EUR"}
    ]


def test_i3_un_import_amb_dos_limits_diferents_a_la_cita_es_ambigu():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €"},
        ["responsabilitat civil de 300.000 € per sinistre, amb sublímit de 150.000 per víctima"],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert (result.status, result.reason) == ("not_normalized", "limit_kind_ambiguous")


def test_i2_un_import_unic_que_no_es_el_llindar_no_s_agafa():
    requirement = mk(
        RequirementCategory.ECONOMIC_SOLVENCY,
        {"multiple": "1,5 vegades l'anualitat mitjana"},
        [
            "Volum anual de negoci del millor dels 3 últims exercicis ≥ 1,5 vegades "
            "l’anualitat mitjana del contracte (61.269,83 €)"
        ],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert (result.status, result.reason) == (
        "not_normalized",
        "quote_amount_not_in_threshold_context",
    )


def test_i2_dos_imports_a_la_cita_encara_que_un_no_porti_euro_es_ambigu():
    requirement = mk(
        RequirementCategory.INSURANCE,
        None,
        ["cobertura mínima de 300.000 € per sinistre i un sublímit de 150.000 per víctima"],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert (result.status, result.reason) == ("not_normalized", "amount_ambiguous")


def test_i2_amb_marca_de_llindar_el_multiple_es_la_base_i_l_import_el_llindar():
    requirement = mk(
        RequirementCategory.ECONOMIC_SOLVENCY,
        None,
        [
            "Volum anual de negoci del millor dels 3 últims exercicis ≥ 1,5 vegades "
            "l’anualitat mitjana del contracte. És a dir, 91.904,74 €"
        ],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert result.value_json["threshold"]["amount"] == "91904.74"


def test_i1_l_experiencia_conserva_recompte_i_cpv():
    requirement = mk(
        RequirementCategory.EXPERIENCE,
        {
            "minimum_amount": "42.888,88 €",
            "minimum_projects": 3,
            "cpv_codes": ["32412000"],
            "lookback_years": 3,
        },
        ["un mínim de tres contractes de com a mínim 42.888,88 € cadascun"],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    value = result.value_json
    assert value["min_count"] == 3 and value["cpv_codes"] == ["32412000"]
    assert value["min_individual_amount"]["amount"] == "42888.88"
    assert value["reference_period"]["years"] == 3


def test_i1_l_assegurança_conserva_l_assegurat_addicional():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €", "unit": "per sinistre", "additional_insured": True},
        [],
    )
    policy = Policy(limits=[Limit(InsuranceLimitKind.PER_CLAIM, Decimal("600000"))])
    profile = ProfileSnapshot(company_id=uuid.uuid4(), insurances=[policy])
    outcome = assess_requirement(requirement, profile, deadline=DEADLINE, profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.INSURANCE_ADDITIONAL_INSURED_UNKNOWN


def test_i1_una_clau_que_no_se_sap_traslladar_impedeix_normalitzar():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €", "unit": "per sinistre", "franquicia": "3.000 €"},
        [],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert (result.status, result.reason) == ("not_normalized", "unmapped_conditions")
    assert result.detail == {"keys": ["franquicia"]}


def test_m1_un_import_nul_al_value_es_busca_a_la_cita():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": None, "unit": "per sinistre"},
        ["pòlissa de responsabilitat civil amb una cobertura mínima de 300.000 € per sinistre"],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert result.status == "normalized"
    assert result.value_json["limits"][0]["amount"] == "300000"


def test_m5_una_prima_anual_no_es_cap_limit():
    assert normalize.limit_kinds("prima anual") == (None, False)


def test_m7_igual_o_major_no_es_el_millor_exercici():
    assert normalize.parse_aggregation(
        "igual o major a 100.000 € en cadascun dels tres últims exercicis"
    ) == (AggregationType.EACH_YEAR, False)
    assert normalize.parse_aggregation("igual o major a 100.000 €") == (
        AggregationType.UNKNOWN,
        False,
    )


def test_m2_la_cnmc_nomes_compta_si_ho_diu_el_text_literal():
    requirement = req("1739a725", title="Inscripció al registre de la CNMC")
    outcome = assess_requirement(requirement, empty(), profile=PV2)
    assert outcome.explanation_code == ExplanationCode.RULE_NOT_AUTOMATABLE
    assert outcome.params["normalization"]["reason"] == "registry_not_named_explicitly"


# --------------------------------------------------------------------------- #
# Ronda 2 de correccions (review/FIX_ROUND_2.md)                               #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "level"),
    [
        # IR-1: la categoria ha d'anar LLIGADA a l'ENS en el mateix sintagma.
        ("El sistema complirà l'ENS, alta disponibilitat i còpies diàries.", None),
        ("El sistema complirà l'ENS alta disponibilitat i còpies diàries.", None),
        ("Compliment de l'ENS i alta seguretat de les dades.", None),
        ("Compliment de l'ENS. Incidències de categoria alta: 4 hores.", None),
        ("ENS i nivell alt de servei", None),
        # Positius:
        ("Certificació ENS de categoria MITJANA", "mitjana"),
        ("categoria alta de l'ENS", "alta"),
        ("nivel medio del Esquema Nacional de Seguridad", "mitjana"),
        ("ENS (categoria Alta) vigent", "alta"),
        ("ENS Alta.", "alta"),
    ],
)
def test_ir1_la_categoria_nomes_si_va_lligada_a_l_ens(text, level):
    assert normalize.ens_level(text) == (level, False)


def test_ir1_alta_disponibilitat_no_es_cap_categoria_ni_un_bloqueig():
    requirement = mk(
        RequirementCategory.TECHNICAL_REQUIREMENT,
        None,
        ["El sistema complirà l'ENS, alta disponibilitat i còpies diàries."],
    )
    profile = ProfileSnapshot(
        company_id=uuid.uuid4(),
        certifications=[EnsCert("ENS categoria Mitjana")],
        section_ratio={"certifications": 1.0},
    )
    outcome = assess_requirement(requirement, profile, profile=PV2)
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_LEVEL_NOT_STATED


@pytest.mark.parametrize(
    ("text", "or_higher"),
    [
        ("ENS de categoria MITJANA o superior", True),
        ("ENS com a mínim de categoria MITJANA", True),
        ("categoria mitjana de l'ENS o superior", True),
        # M-R1: un «mínim» alie a la categoria no compta.
        ("ENS de categoria MITJANA. El termini mínim de resposta és de 4 hores.", False),
        ("ENS de categoria MITJANA; superior a 4 hores de resposta.", False),
    ],
)
def test_o_superior_nomes_dins_del_sintagma_de_la_categoria(text, or_higher):
    level, ambiguous, found = normalize.ens_reading(text)
    assert (level, ambiguous, found) == ("mitjana", False, or_higher)


def test_un_minim_alie_no_converteix_una_categoria_superior_en_compleix():
    requirement = mk(
        RequirementCategory.TECHNICAL_REQUIREMENT,
        None,
        ["Certificació ENS de categoria MITJANA. El termini mínim de resposta és de 4 hores."],
    )
    profile = ProfileSnapshot(
        company_id=uuid.uuid4(),
        certifications=[EnsCert("ENS Alta")],
        section_ratio={"certifications": 1.0},
    )
    outcome = assess_requirement(requirement, profile, profile=PV2)
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_LEVEL_HIGHER_REVIEW


def test_categoria_inferior_amb_la_seccio_incompleta_es_sense_dades():
    requirement = mk(
        RequirementCategory.TECHNICAL_REQUIREMENT, None, ["Certificació ENS de categoria ALTA"]
    )
    incomplete = ProfileSnapshot(company_id=uuid.uuid4(), certifications=[EnsCert("ENS Mitjana")])
    outcome = assess_requirement(requirement, incomplete, profile=PV2)
    assert outcome.result == AssessmentResult.UNKNOWN
    assert outcome.explanation_code == ExplanationCode.CERTIFICATION_LEVEL_BELOW
    complete = ProfileSnapshot(
        company_id=uuid.uuid4(),
        certifications=[EnsCert("ENS Mitjana")],
        section_ratio={"certifications": 1.0},
    )
    assert assess_requirement(requirement, complete, profile=PV2).result == (
        AssessmentResult.DOES_NOT_SATISFY
    )


def test_ir2_value_i_cita_contradictoris_no_es_normalitzen():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €", "unit": "per sinistre"},
        [
            "pòlissa de responsabilitat civil amb una cobertura mínima de "
            "300.000 € per sinistre i any"
        ],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert (result.status, result.reason) == ("not_normalized", "limit_kind_ambiguous")
    policy = Policy(
        limits=[
            Limit(InsuranceLimitKind.PER_CLAIM, Decimal("300000")),
            Limit(InsuranceLimitKind.ANNUAL_AGGREGATE, Decimal("200000")),
        ]
    )
    outcome = assess_requirement(
        requirement, ProfileSnapshot(company_id=uuid.uuid4(), insurances=[policy]), profile=PV2
    )
    assert outcome.result == AssessmentResult.NEEDS_REVIEW
    assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_KIND_AMBIGUOUS


def test_ir2_value_i_cita_que_coincideixen_es_normalitzen():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €", "unit": "per sinistre i any"},
        [
            "pòlissa de responsabilitat civil amb una cobertura mínima de "
            "300.000 € per sinistre i any"
        ],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert [limit["kind"] for limit in result.value_json["limits"]] == [
        "per_claim",
        "annual_aggregate",
    ]


def test_ir2_la_cita_que_no_diu_la_base_no_contradiu_el_value():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €", "unit": "per sinistre"},
        ["pòlissa de responsabilitat civil amb una cobertura mínima de 300.000 €"],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    assert [limit["kind"] for limit in result.value_json["limits"]] == ["per_claim"]


def test_m_r4_un_sublimit_ambigu_diu_el_motiu_a_la_ui():
    requirement = mk(
        RequirementCategory.INSURANCE,
        {"amount": "300.000 €", "unit": "sublímit per ocurrència"},
        [],
    )
    outcome = assess_requirement(requirement, empty(), profile=PV2)
    assert outcome.explanation_code == ExplanationCode.INSURANCE_LIMIT_KIND_AMBIGUOUS


def test_la_marca_no_viu_al_value_i_un_value_desat_que_la_porti_no_activa_res():
    from app.ai.value_schemas import VALUE_SCHEMA_VERSION
    from app.compliance.engine import _dispatch

    result = normalize.normalize_requirement(req("8ce506d2"), PV2)
    assert "_normalizer" not in result.value_json
    # Un v2 desat amb una clau `_normalizer`: la regla de la v4, sense res de nou.
    requirement = mk(
        RequirementCategory.REGISTRATION,
        {"_normalizer": "x", "type": "sector_operator_registry", "name": "Registre"},
        [],
    )
    requirement.value_schema = VALUE_SCHEMA_VERSION
    complete = ProfileSnapshot(company_id=uuid.uuid4(), section_ratio={"registrations": 1.0})
    outcome = assess_requirement(requirement, complete, profile=PV2)
    assert outcome == _dispatch(requirement, complete)
    assert outcome.explanation_code == ExplanationCode.RULE_NOT_AUTOMATABLE
    insurance = mk(
        RequirementCategory.INSURANCE,
        {
            "_normalizer": "x",
            "kind": "insurance",
            "limits": [{"kind": "employer_per_victim", "amount": 150000}],
        },
        [],
    )
    insurance.value_schema = VALUE_SCHEMA_VERSION
    total = ProfileSnapshot(
        company_id=uuid.uuid4(),
        insurances=[Policy(limits=[], coverage_amount=Decimal("2000000"))],
    )
    assert assess_requirement(insurance, total, profile=PV2) == _dispatch(insurance, total)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("32412000", ["32412000"]),
        ("32412000-4; 64210000", ["32412000-4", "64210000"]),
        (["32412000", "64210000-1"], ["32412000", "64210000-1"]),
        ("telecomunicacions", None),
    ],
)
def test_cpv_codes_en_text_no_es_parteixen_per_caracters(raw, expected):
    requirement = mk(
        RequirementCategory.EXPERIENCE,
        {"minimum_amount": "42.888,88 €", "cpv_codes": raw},
        [],
    )
    result = normalize.normalize_requirement(requirement, PV2)
    if expected is None:
        assert (result.status, result.reason) == ("not_normalized", "unmapped_conditions")
    else:
        assert result.value_json["cpv_codes"] == expected
