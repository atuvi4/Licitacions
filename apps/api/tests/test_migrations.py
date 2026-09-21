"""Les migracions d'Alembic no poden divergir dels models.

La primera migracio esta escrita a ma (no hi havia base de dades on fer
`--autogenerate`), aixi que aquest test es la xarxa de seguretat: genera l'SQL
en mode offline —no cal cap PostgreSQL engegat— i el compara columna a columna
amb el que declaren els models.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app.models import Base

API_ROOT = Path(__file__).resolve().parents[1]
# Mode offline: Alembic nomes compila SQL per a aquest dialecte, no s'hi connecta.
OFFLINE_URL = "postgresql+asyncpg://user:pass@localhost/db"

# Es compara el PRIMER TOKEN exacte, no un prefix: una columna anomenada
# `checkpoint` comenca per "CHECK" i es descartaria com si fos un constraint.
STRUCTURAL_TOKENS = {"CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "CHECK"}


@pytest.fixture(scope="module")
def migration_sql() -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": OFFLINE_URL},
    )
    assert result.returncode == 0, f"alembic ha fallat:\n{result.stderr[-2000:]}"
    return result.stdout


def _offline_sql(*args: str) -> str:
    """SQL d'un tram concret de migracions, sense connectar-se enlloc."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args, "--sql"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": OFFLINE_URL},
    )
    assert result.returncode == 0, f"alembic ha fallat:\n{result.stderr[-2000:]}"
    return result.stdout


def test_la_solucio_tecnica_es_pot_aplicar_i_desfer():
    """0023 -> 0024 -> 0023: el que una revisio crea, el seu downgrade ho treu.

    No n'hi ha prou que existeixi un `downgrade()`: ha de desfer exactament el
    que s'ha aplicat. Una revisio que crea una taula i en deixa els indexs
    enrere fa que tornar enrere i reaplicar falli, i aixo nomes es veu quan ja
    ha passat en un desplegament.
    """
    table = "workspace_technical_solutions"
    up = _offline_sql("upgrade", "0023_analysis_quotes:0024_technical_solution")
    down = _offline_sql("downgrade", "0024_technical_solution:0023_analysis_quotes")

    assert f"CREATE TABLE {table}" in up
    assert "uq_workspace_technical_solutions_requirement" in up
    assert "WHERE requirement_id IS NOT NULL" in up

    created_indexes = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    dropped_indexes = set(re.findall(r"DROP INDEX (\w+)", down))
    assert created_indexes
    assert created_indexes == dropped_indexes
    assert f"DROP TABLE {table}" in down


_OFFER_TABLES = (
    "economic_offers",
    "economic_offer_lots",
    "economic_offer_sections",
    "economic_offer_lines",
)


def test_l_oferta_economica_es_pot_aplicar_i_desfer():
    """0024 -> 0025 -> 0024, amb les quatre taules i el que les protegeix.

    Es mira l'SQL i no nomes que la revisio existeixi: una migracio que crea
    indexs i no els treu fa que tornar enrere i reaplicar falli, i aixo nomes es
    veu quan ja ha passat en un desplegament.
    """
    up = _offline_sql("upgrade", "0024_technical_solution:0025_economic_offer")
    down = _offline_sql("downgrade", "0025_economic_offer:0024_technical_solution")

    for table in _OFFER_TABLES:
        assert f"CREATE TABLE {table}" in up
        assert f"DROP TABLE {table}" in down

    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    dropped = set(re.findall(r"DROP INDEX (\w+)", down))
    assert created
    assert created == dropped


def test_els_rangs_de_l_oferta_els_imposa_la_base():
    """Els CHECK han de ser a l'esquema.

    `sa.Enum(native_enum=False)` nomes genera un VARCHAR: si `pricing_basis`
    depengues d'aixo, la base acceptaria qualsevol cadena que hi capigues.
    """
    up = _offline_sql("upgrade", "0024_technical_solution:0025_economic_offer")

    esperats = {
        "ck_economic_offers_revision_non_negative",
        "ck_economic_offer_lots_duration_at_least_one",
        "ck_economic_offer_lots_position_non_negative",
        "ck_economic_offer_sections_position_non_negative",
        "ck_economic_offer_lines_basis_valid",
        "ck_economic_offer_lines_quantity_non_negative",
        "ck_economic_offer_lines_duration_override_at_least_one",
        "ck_economic_offer_lines_unit_cost_non_negative",
        "ck_economic_offer_lines_offered_price_non_negative",
        "ck_economic_offer_lines_tender_max_non_negative",
        "ck_economic_offer_lines_vat_rate_range",
        "ck_economic_offer_lines_position_non_negative",
    }

    assert esperats <= set(re.findall(r"CONSTRAINT (\w+) CHECK", up))
    assert "pricing_basis IN ('one_off', 'monthly', 'annual', 'per_unit')" in up


def test_l_oferta_no_perd_de_quin_lot_son_els_preus():
    """RESTRICT al lot del plec, CASCADE dins de l'agregat, SET NULL als vincles."""
    up = _offline_sql("upgrade", "0024_technical_solution:0025_economic_offer")

    esperat = {
        "fk_economic_offers_workspace_id_bid_workspaces": "CASCADE",
        "fk_economic_offer_lots_offer_id_economic_offers": "CASCADE",
        "fk_economic_offer_lots_tender_lot_id_tender_lots": "RESTRICT",
        "fk_economic_offer_sections_offer_lot_id_economic_offer_lots": "CASCADE",
        "fk_economic_offer_lines_section_id_economic_offer_sections": "CASCADE",
        "fk_economic_offer_lines_requirement_id_requirements": "SET NULL",
        "fk_economic_offer_lines_technical_solution": "SET NULL",
    }

    clauses = dict(
        re.findall(r"CONSTRAINT (fk_economic\w+) FOREIGN KEY.*?ON DELETE ([A-Z ]+)", up)
    )
    for name, action in esperat.items():
        assert clauses.get(name, "").strip() == action, name


def test_un_lot_arxivat_no_ocupa_la_unicitat():
    """Les dues unicitats de lot son parcials, i el `WHERE` ho ha de dir.

    Sense `archived_at IS NULL` a la condicio, arxivar un bloc de preus i tornar
    a comencar seria impossible sense esborrar historia.
    """
    up = _offline_sql("upgrade", "0024_technical_solution:0025_economic_offer")

    assert (
        "CREATE UNIQUE INDEX uq_economic_offer_lots_tender_lot "
        "ON economic_offer_lots (offer_id, tender_lot_id) "
        "WHERE tender_lot_id IS NOT NULL AND archived_at IS NULL" in up
    )
    assert (
        "CREATE UNIQUE INDEX uq_economic_offer_lots_global "
        "ON economic_offer_lots (offer_id) "
        "WHERE tender_lot_id IS NULL AND archived_at IS NULL" in up
    )


_SCOPE_COLUMNS = (
    "technical_scope_reviewed_at",
    "technical_scope_reviewed_by",
    "technical_scope_fingerprint",
)


def test_la_revisio_de_l_abast_tecnic_es_pot_aplicar_i_desfer():
    """0025 -> 0026 -> 0025, amb els tres camps i la seva clau forana."""
    up = _offline_sql("upgrade", "0025_economic_offer:0026_technical_scope_review")
    down = _offline_sql("downgrade", "0026_technical_scope_review:0025_economic_offer")

    for column in _SCOPE_COLUMNS:
        assert f"ADD COLUMN {column}" in up
        assert f"DROP COLUMN {column}" in down
    assert "fk_bid_workspaces_technical_scope_reviewer" in up
    assert "fk_bid_workspaces_technical_scope_reviewer" in down


def test_els_tres_camps_neixen_buits():
    """Cap candidatura existent no queda revisada en silenci (C-5).

    Si la migracio omplis res —una data, un usuari, una empremta— tots els
    workspaces d'avui passarien per revisats sense que ningu ho hagues mirat.
    """
    up = _offline_sql("upgrade", "0025_economic_offer:0026_technical_scope_review")

    for column in _SCOPE_COLUMNS:
        assert f"ADD COLUMN {column}" in up
        assert f"ALTER COLUMN {column} SET NOT NULL" not in up
        assert f"ALTER COLUMN {column} SET DEFAULT" not in up
    assert "UPDATE bid_workspaces" not in up


def test_qui_va_revisar_es_pot_donar_de_baixa_sense_esborrar_la_revisio():
    """SET NULL: la revisio segueix havent passat encara que l'usuari marxi."""
    up = _offline_sql("upgrade", "0025_economic_offer:0026_technical_scope_review")

    fk = [line for line in up.splitlines() if "technical_scope_reviewer" in line]
    assert fk
    assert any("ON DELETE SET NULL" in line for line in fk)


_TENANCY = "0026_technical_scope_review:0027_tender_tenancy"


def test_la_tenancy_de_licitacions_es_pot_aplicar_i_desfer():
    """0026 -> 0027 -> 0026, amb la columna, la seva FK i el seu index."""
    up = _offline_sql("upgrade", _TENANCY)
    down = _offline_sql("downgrade", "0027_tender_tenancy:0026_technical_scope_review")

    assert "ADD COLUMN owner_company_id" in up
    assert "fk_tenders_owner_company" in up
    assert "ix_tenders_owner_company_id" in up

    assert "DROP COLUMN owner_company_id" in down
    assert "fk_tenders_owner_company" in down
    assert "ix_tenders_owner_company_id" in down


def test_cap_licitacio_existent_no_es_torna_privada():
    """Sense backfill: tot el que ja hi ha queda public, que es el que era.

    Si la migracio escrivis un propietari a alguna fila, licitacions que avui
    veu tothom deixarien de veure's sense que ningu ho hagues decidit.
    """
    up = _offline_sql("upgrade", _TENANCY)

    assert "ADD COLUMN owner_company_id" in up
    assert "ALTER COLUMN owner_company_id SET NOT NULL" not in up
    assert "ALTER COLUMN owner_company_id SET DEFAULT" not in up
    assert "UPDATE tenders" not in up


def test_esborrar_una_empresa_nomes_s_emporta_les_seves_licitacions():
    """CASCADE acotat: la privada se'n va amb l'empresa, la publica es queda."""
    up = _offline_sql("upgrade", _TENANCY)

    fk = [line for line in up.splitlines() if "fk_tenders_owner_company" in line]
    assert fk
    assert any("ON DELETE CASCADE" in line for line in fk)


_MANUAL = "0027_tender_tenancy:0028_manual_ingestion_support"


def test_la_ingesta_manual_es_pot_aplicar_i_desfer():
    """0027 -> 0028 -> 0027, amb les dues meitats de la revisio."""
    up = _offline_sql("upgrade", _MANUAL)
    down = _offline_sql("downgrade", "0028_manual_ingestion_support:0027_tender_tenancy")

    for fragment in ("ADD COLUMN owner_company_id", "fk_buyers_owner_company",
                     "ix_buyers_owner_company_id", "ADD COLUMN origin",
                     "ADD COLUMN uploaded_by"):
        assert fragment in up, fragment
    for fragment in ("DROP COLUMN owner_company_id", "fk_buyers_owner_company",
                     "DROP COLUMN origin", "DROP COLUMN uploaded_by"):
        assert fragment in down, fragment


def test_cap_organisme_existent_no_es_torna_privat():
    """Els organismes de les fonts sindicades queden publics, que es el que son."""
    up = _offline_sql("upgrade", _MANUAL)

    assert "ALTER COLUMN owner_company_id SET NOT NULL" not in up
    assert "UPDATE buyers" not in up


def test_els_fitxers_que_ja_hi_havia_queden_com_a_descarregats():
    """`origin` neix ple, i nomes per a les files antigues.

    Sense el default de servidor la columna NOT NULL no es podria afegir. Amb
    ell permanent, una fila nova podria dir-se descarregada sense ser-ho, aixi
    que es retira just despres.
    """
    up = _offline_sql("upgrade", _MANUAL)

    assert "ADD COLUMN origin" in up
    assert "'fetched'" in up
    assert "DROP DEFAULT" in up


def test_un_fitxer_pujat_no_necessita_url():
    """La nul·labilitat es el que permet que existeixi un fitxer sense origen web."""
    up = _offline_sql("upgrade", _MANUAL)
    down = _offline_sql("downgrade", "0028_manual_ingestion_support:0027_tender_tenancy")

    assert "ALTER COLUMN source_url DROP NOT NULL" in up
    assert "ALTER COLUMN source_url SET NOT NULL" in down


def test_esborrar_una_empresa_no_toca_els_organismes_globals():
    """CASCADE acotat: NULL no coincideix mai amb l'empresa esborrada."""
    up = _offline_sql("upgrade", _MANUAL)

    fk = [line for line in up.splitlines() if "fk_buyers_owner_company" in line]
    assert fk
    assert any("ON DELETE CASCADE" in line for line in fk)


def test_qui_puja_un_fitxer_es_pot_donar_de_baixa():
    """SET NULL: el fitxer segueix sent valid; el que es perd es qui el va pujar."""
    up = _offline_sql("upgrade", _MANUAL)

    fk = [line for line in up.splitlines() if "uploaded_by" in line and "FOREIGN KEY" in line]
    assert fk
    assert any("ON DELETE SET NULL" in line for line in fk)


def _columns_created_by_migrations(ddl: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S):
        name, body = match.group(1), match.group(2)
        columns = set()
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            if not line:
                continue
            # Les columnes que xoquen amb una paraula reservada de PostgreSQL
            # poden sortir entre cometes.
            first = line.split(" ")[0].strip('"')
            if first.upper() not in STRUCTURAL_TOKENS:
                columns.add(first)
        tables[name] = columns
    for name, column in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", ddl):
        tables.setdefault(name, set()).add(column)
    return tables


def test_les_migracions_creen_totes_les_taules_dels_models(migration_sql):
    from_migrations = _columns_created_by_migrations(migration_sql)
    from_models = {table.name for table in Base.metadata.sorted_tables}

    # `alembic_version` la crea el propi Alembic i no es del domini.
    assert from_models <= set(from_migrations)
    assert set(from_migrations) - from_models <= {"alembic_version"}


def test_cada_taula_te_les_mateixes_columnes_que_el_model(migration_sql):
    from_migrations = _columns_created_by_migrations(migration_sql)

    for table in Base.metadata.sorted_tables:
        expected = {column.name for column in table.columns}
        actual = from_migrations[table.name]
        assert expected == actual, (
            f"{table.name}: al model i no a la migracio {sorted(expected - actual)}; "
            f"a la migracio i no al model {sorted(actual - expected)}"
        )


def test_les_migracions_son_reversibles(migration_sql):
    """Cada revisio ha de tenir downgrade; si no, un desplegament no es pot desfer."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    revisions = list(script.walk_revisions())
    assert revisions, "no hi ha cap migracio"

    for revision in revisions:
        source = Path(revision.path).read_text(encoding="utf-8")
        body = source.split("def downgrade()")[1]
        assert "pass" not in body.split("\n")[1], f"{revision.revision} no te downgrade real"


def _varchar_widths(ddl: str) -> dict[tuple[str, str], int]:
    """Amplada final de cada VARCHAR segons les migracions, en ordre d'aplicacio.

    Cal llegir-ho en ordre: una revisio posterior pot eixamplar una columna, i el
    que val es l'ultima amplada.
    """
    widths: dict[tuple[str, str], int] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S):
        table, body = match.group(1), match.group(2)
        for raw in body.splitlines():
            line = raw.strip().rstrip(",")
            parts = line.split()
            if len(parts) < 2:
                continue
            column = parts[0].strip('"')
            if column.upper() in STRUCTURAL_TOKENS:
                continue
            size = re.match(r"VARCHAR\((\d+)\)", parts[1], re.I)
            if size:
                widths[(table, column)] = int(size.group(1))
    for table, column, size in re.findall(
        r"ALTER TABLE (\w+) ADD COLUMN (\w+) VARCHAR\((\d+)\)", ddl
    ):
        widths[(table, column)] = int(size)
    for table, column, size in re.findall(
        r"ALTER TABLE (\w+) ALTER COLUMN (\w+) TYPE VARCHAR\((\d+)\)", ddl
    ):
        widths[(table, column)] = int(size)
    return widths


def test_cap_varchar_no_es_mes_estret_que_el_del_model(migration_sql):
    """Una columna mes estreta que el model rebutjaria valors valids.

    Va passar de veritat: afegir `approved_for_submission` (23 caracters) a un
    enum la columna del qual s'havia creat com a VARCHAR(22). El test de columnes
    no ho veia perque el NOM de la columna no havia canviat, i l'error nomes
    hauria sortit contra un PostgreSQL real, en intentar desar el valor.
    """
    import sqlalchemy as sa

    widths = _varchar_widths(migration_sql)
    narrow: list[str] = []

    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            expected = getattr(column.type, "length", None)
            if not isinstance(column.type, sa.String) or not expected:
                continue
            actual = widths.get((table.name, column.name))
            if actual is not None and actual < expected:
                narrow.append(f"{table.name}.{column.name}: model {expected}, migracio {actual}")

    assert not narrow, "columnes mes estretes que el model: " + "; ".join(narrow)


#: Un FK sense clausula `ON DELETE` es comporta com `NO ACTION` a PostgreSQL.
_DEFAULT_ON_DELETE = "NO ACTION"
_FOREIGN_KEY = re.compile(
    r"CONSTRAINT (?P<name>\w+) FOREIGN\s*KEY\s*\((?P<columns>[^)]*)\)\s*"
    r"REFERENCES (?P<referred>\w+)\s*\([^)]*\)(?P<rest>[^,\n]*)",
    re.I,
)


def _on_delete(clause: str) -> str:
    action = re.search(
        r"ON DELETE (CASCADE|SET NULL|SET DEFAULT|RESTRICT|NO ACTION)", clause, re.I
    )
    return action.group(1).upper() if action else _DEFAULT_ON_DELETE


def _foreign_keys(ddl: str) -> dict[tuple[str, tuple[str, ...]], tuple[str, str]]:
    """Estat final de cada FK segons les migracions: `(taula, columnes) -> (nom, ON DELETE)`.

    Cal llegir-ho en ordre i no com un recompte: una revisio posterior pot
    esborrar una FK i tornar-la a crear amb un comportament diferent, i el que
    val es l'ultima.
    """
    state: dict[tuple[str, tuple[str, ...]], tuple[str, str]] = {}

    def remember(table: str, match: re.Match[str]) -> None:
        columns = tuple(c.strip().strip('"') for c in match.group("columns").split(","))
        state[(table, columns)] = (match.group("name"), _on_delete(match.group("rest")))

    for created in re.finditer(r"CREATE TABLE (\w+) \((.*?)\n\);", ddl, re.S):
        for match in _FOREIGN_KEY.finditer(created.group(2)):
            remember(created.group(1), match)

    for table, operation, tail in re.findall(
        r"ALTER TABLE (\w+) (ADD|DROP) CONSTRAINT (.*?);", ddl, re.S
    ):
        if operation.upper() == "ADD":
            match = _FOREIGN_KEY.search(f"CONSTRAINT {tail}")
            if match:
                remember(table, match)
            continue
        name = tail.split()[0]
        for key, value in list(state.items()):
            if key[0] == table and value[0] == name:
                del state[key]

    return state


def test_cada_fk_esborra_igual_que_al_model(migration_sql):
    """El `ON DELETE` de les migracions ha de ser el que declara el model.

    Va passar de veritat: la 0013 va crear `company_insurance_limits.created_by`
    i `updated_by` sense `ON DELETE`, quan `ActorMixin` les declara `SET NULL`.
    A PostgreSQL aixo vol dir `NO ACTION`, i esborrar un usuari fallava amb
    violacio de clau forana en comptes de deixar la fila sense autor.

    La suite corre sobre SQLite creant les taules des dels MODELS, on la
    clausula hi era sempre; l'esquema real es construeix des de les MIGRACIONS.
    Mentre l'unic que es comparaven eren noms de columna, aquesta diferencia no
    la podia veure ningu fins a tenir un PostgreSQL al davant.
    """
    from_migrations = _foreign_keys(migration_sql)
    divergent: list[str] = []

    for table in Base.metadata.sorted_tables:
        for constraint in table.foreign_key_constraints:
            columns = tuple(column.name for column in constraint.columns)
            expected = (constraint.ondelete or _DEFAULT_ON_DELETE).upper()
            found = from_migrations.get((table.name, columns))
            if found is None:
                divergent.append(f"{table.name}({', '.join(columns)}): no hi es a la migracio")
            elif found[1] != expected:
                divergent.append(
                    f"{table.name}({', '.join(columns)}) [{found[0]}]: "
                    f"model ON DELETE {expected}, migracio ON DELETE {found[1]}"
                )

    assert not divergent, "FK que no esborren com el model: " + "; ".join(divergent)
