"""El fixture d'acceptació com a oracle independent (S3-S11).

Aquests tests no toquen la base de dades ni l'aplicació. Només miren el JSON, i
protegeixen la propietat que fa que l'acceptació serveixi de res:

    l'oracle s'ha d'haver escrit ABANS de mirar què respon el producte

Un fixture generat executant Tuvi no comprova res: diu que el producte fa el que
fa. Per això el fitxer és de només lectura, els imports hi són com a cadena
—cap `float`, que ja no és el mateix número que s'hi va escriure— i el total
canònic hi és **escrit**, no calculat.

I una segona, que ve del brief: el resultat real del concurs no és un oracle.
Que una oferta guanyés no diu si el workflow és correcte, i tenir-ho al fixture
convidaria a fer-lo servir com si ho digués.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "benchmarks" / "palamos_s3_benchmark.json"

#: Un decimal escrit com a text. `1e3` i `.5` no hi entren: el fixture és una
#: transcripció, i una transcripció no té notació científica.
DECIMAL_TEXT = re.compile(r"^-?\d+(\.\d+)?$")

#: Camps que parlen del resultat del concurs. Cap no ha d'existir enlloc.
FORBIDDEN_KEYS = {
    "outcome",
    "winner",
    "won",
    "lost",
    "awarded",
    "award_amount",
    "score",
    "points",
    "abnormality",
    "abnormal",
    "ranking",
    "competitors",
}

MONEY_FIELDS = (
    "unit_cost_ex_vat",
    "expected_offered_unit_price_ex_vat",
    "expected_line_total_ex_vat",
    "tender_max_unit_price_ex_vat",
    "capex_one_off_ex_vat",
    "vat_rate",
    "target_markup_pct",
    "quantity",
)


@pytest.fixture(scope="module")
def raw() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fixture(raw: str) -> dict:
    # `parse_float` esclata: si algú escriu un import sense cometes, aquí es veu.
    def refuse(value: str):
        raise AssertionError(f"el fixture porta un float JSON: {value}")

    return json.loads(raw, parse_float=refuse)


def _walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}" if path else key, key, value
            yield from _walk(value, f"{path}.{key}" if path else key)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _walk(value, f"{path}[{index}]")


# --------------------------------------------------------------------------- #
# A. Forma                                                                      #
# --------------------------------------------------------------------------- #


def test_the_fixture_is_where_the_repository_can_commit_it():
    """`var/` és per als artefactes locals i està fora del control de versions.

    Un oracle que no entra al commit no és un oracle: és un fitxer que tenia qui
    va escriure el test.
    """
    assert FIXTURE_PATH.exists()
    assert "var" not in FIXTURE_PATH.parts


def test_the_schema_is_declared(fixture):
    assert fixture["schema"] == "palamos-s3-benchmark-v1"
    assert fixture["id"]


def test_every_section_the_acceptance_needs_is_there(fixture):
    for section in (
        "tender",
        "clock",
        "oracles",
        "economic_lines",
        "not_offered",
        "dimension",
        "solution_options",
        "scenario",
        "source_ref",
        "requirements",
    ):
        assert section in fixture, section


def test_the_logical_keys_are_unique(fixture):
    keys = [line["key"] for line in fixture["economic_lines"]]
    assert len(keys) == len(set(keys))
    assert len(keys) == 21

    others = [row["key"] for row in fixture["not_offered"]]
    assert len(others) == len(set(others))


# --------------------------------------------------------------------------- #
# B. Imports                                                                    #
# --------------------------------------------------------------------------- #


def test_no_float_survives_anywhere(fixture):
    """`parse_float` ja ho hauria fet petar; això ho diu explícitament."""
    for path, _key, value in _walk(fixture):
        assert not isinstance(value, float), path


def test_every_amount_is_a_decimal_string(fixture):
    for line in fixture["economic_lines"]:
        for field in MONEY_FIELDS:
            if field not in line:
                continue
            value = line[field]
            assert isinstance(value, str), (line["key"], field)
            assert DECIMAL_TEXT.match(value), (line["key"], field, value)
            Decimal(value)  # i que sigui construïble de debò


def test_the_durations_are_whole_months(fixture):
    for line in fixture["economic_lines"]:
        assert line["duration_months"] == 48
        assert isinstance(line["duration_months"], int)


def test_the_unit_amounts_carry_the_scale_they_will_be_stored_with(fixture):
    """Quatre decimals: és l'escala de `Numeric(18,4)` a la línia d'oferta.

    Transcriure'n més seria escriure al fixture un número que la base no pot
    desar, i llavors l'oracle no seria comparable amb res.
    """
    for line in fixture["economic_lines"]:
        for field in ("unit_cost_ex_vat", "expected_offered_unit_price_ex_vat"):
            assert Decimal(line[field]).as_tuple().exponent == -4, (line["key"], field)


# --------------------------------------------------------------------------- #
# C. El doble oracle                                                            #
# --------------------------------------------------------------------------- #


def test_both_oracles_are_present_and_are_not_the_same_number(fixture):
    """D-06. Són dues coses diferents i el fixture no les confon."""
    oracles = fixture["oracles"]
    documented = Decimal(oracles["documented_total_ex_vat"])
    canonical = Decimal(oracles["canonical_total_ex_vat"])

    assert documented == Decimal("219351.55")
    assert canonical != documented
    assert canonical - documented == Decimal(oracles["delta_canonical_minus_documented"])


def test_the_canonical_rule_is_written_down(fixture):
    rule = fixture["oracles"]["canonical_rule"]
    assert "quantize-line-then-sum" in rule


def test_the_delta_is_explained_and_adds_up(fixture):
    """Un delta sense explicació seria un número que ningú no sap què vol dir."""
    explained = fixture["oracles"]["delta_explained"]
    parts = Decimal(explained["lines_not_enumerated_in_artifact"]) + Decimal(
        explained["unit_scale_and_quantization"]
    )
    total = Decimal(fixture["oracles"]["delta_canonical_minus_documented"])
    # Els components es donen a quatre decimals i el delta a dos: han de quadrar
    # a l'escala del delta.
    assert parts.quantize(Decimal("0.01")) == total


# --------------------------------------------------------------------------- #
# D. Independència                                                              #
# --------------------------------------------------------------------------- #


def test_the_canonical_total_is_the_sum_of_the_frozen_line_totals(fixture):
    """Suma dels totals de línia **escrits al fixture**, no calculats pel motor.

    Si algú substituís `canonical_total_ex_vat` pel resultat del motor sense
    tocar les línies, aquesta suma deixaria de quadrar.
    """
    total = sum(
        (Decimal(line["expected_line_total_ex_vat"]) for line in fixture["economic_lines"]),
        Decimal("0.00"),
    )
    assert total == Decimal(fixture["oracles"]["canonical_total_ex_vat"])


def test_each_frozen_line_total_follows_the_canonical_rule(fixture):
    """Aritmètica del fixture, sense importar `app.offer.pricing`.

    Deliberadament reimplementada en tres línies: si el test cridés el motor,
    l'oracle deixaria de ser independent i comprovaria que el producte és igual
    a si mateix.
    """
    from decimal import ROUND_HALF_UP

    for line in fixture["economic_lines"]:
        cost = Decimal(line["unit_cost_ex_vat"])
        markup = Decimal(line["target_markup_pct"])
        price = (cost * (1 + markup / 100)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP
        )
        assert price == Decimal(line["expected_offered_unit_price_ex_vat"]), line["key"]

        extended = price * Decimal(line["quantity"]) * Decimal(line["duration_months"])
        assert extended.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal(
            line["expected_line_total_ex_vat"]
        ), line["key"]


def test_the_fixture_never_imports_the_engine():
    """Aquest fitxer no pot dependre de `app`. Si ho fes, no provaria res."""
    lines = Path(__file__).read_text(encoding="utf-8").splitlines()
    imports = [line.strip() for line in lines if line.lstrip().startswith(("import ", "from "))]
    assert not [line for line in imports if line.split()[1].split(".")[0] == "app"], imports


def test_the_amortised_lines_keep_the_capex_they_come_from(fixture):
    """CAPEX → OPEX: el mateix diner vist de dues maneres, i totes dues escrites.

    El full amortitza el maquinari dins la quota mensual. Que la quota i la
    inversió quadrin a 48 mesos es comprova aquí, sobre el fixture, i no contra
    cap resposta del producte.
    """
    from decimal import ROUND_HALF_UP

    amortised = [line for line in fixture["economic_lines"] if "capex_one_off_ex_vat" in line]
    assert amortised, "el fixture hauria de tenir partides amortitzades"

    for line in amortised:
        capex = Decimal(line["capex_one_off_ex_vat"])
        monthly = Decimal(line["unit_cost_ex_vat"])
        months = Decimal(line["amortized_over_months"])
        assert (capex / months).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) == monthly, (
            line["key"]
        )


# --------------------------------------------------------------------------- #
# E. El resultat del concurs no hi és                                           #
# --------------------------------------------------------------------------- #


def test_nothing_in_the_fixture_talks_about_who_won(fixture):
    """§8 del brief: l'outcome no és oracle de producte.

    No hi és perquè si hi fos algú l'acabaria fent servir per decidir si una
    opció és bona, i això no és el que aquesta acceptació mesura.
    """
    for path, key, _value in _walk(fixture):
        assert key not in FORBIDDEN_KEYS, path


def test_no_scoring_and_no_abnormality_leak_into_it(raw: str):
    lowered = raw.lower()
    for word in ("anormal", "puntuaci", "adjudica", "guanya"):
        assert word not in lowered, word


def test_the_clock_is_frozen(fixture):
    """Cap data de l'acceptació surt del rellotge de la màquina."""
    clock = fixture["clock"]
    assert clock["publication_date"] == "2024-03-01"
    assert clock["deadline_at"].endswith("+00:00")


# --------------------------------------------------------------------------- #
# F. El que el recorregut necessita                                             #
# --------------------------------------------------------------------------- #


def test_the_dimension_carries_the_two_readings_and_the_decision(fixture):
    dimension = fixture["dimension"]
    assert Decimal(dimension["observation_a"]["value_num"]) == 151
    assert Decimal(dimension["observation_b"]["value_num"]) == 181
    assert Decimal(dimension["decision"]["working_value_num"]) == 181
    assert dimension["decision"]["reason"].strip()
    # La que confirma no ha de reobrir res; la que contradiu, sí.
    assert Decimal(dimension["confirming_observation"]["value_num"]) == 181
    assert Decimal(dimension["conflicting_observation"]["value_num"]) != 181


def test_the_two_sibling_options_are_both_written_down(fixture):
    options = fixture["solution_options"]
    assert options["selected"]["key"] != options["rejected"]["key"]
    assert options["rejected"]["reason"].strip()


def test_the_scenario_values_are_marked_as_scenario(fixture):
    """El que és coreografia del test no es pot confondre amb transcripció."""
    assert "COREOGRAFIA" in fixture["scenario"]["note"]
    assert (
        fixture["scenario"]["option_supplier_before"]
        != fixture["scenario"]["option_supplier_after"]
    )
