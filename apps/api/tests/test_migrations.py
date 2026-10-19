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
import sqlalchemy as sa

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


def test_els_identificadors_de_revisio_caben_a_alembic_version():
    """`alembic_version.version_num` es VARCHAR(32): un id mes llarg trenca l'upgrade."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    too_long = [r.revision for r in script.walk_revisions() if len(r.revision) > 32]
    assert not too_long, too_long


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


_REVIEW = "0028_manual_ingestion_support:0029_document_review_state"


def test_la_revisio_documental_es_pot_aplicar_i_desfer():
    """0028 -> 0029 -> 0028, amb les dues columnes."""
    up = _offline_sql("upgrade", _REVIEW)
    down = _offline_sql("downgrade", "0029_document_review_state:0028_manual_ingestion_support")

    for fragment in ("ADD COLUMN documents_reviewed_at",
                     "ADD COLUMN documents_reviewed_fingerprint"):
        assert fragment in up, fragment
    for fragment in ("DROP COLUMN documents_reviewed_at",
                     "DROP COLUMN documents_reviewed_fingerprint"):
        assert fragment in down, fragment


def test_cap_candidatura_existent_no_queda_revisada():
    """NULL vol dir NO revisat, i cap workspace no en guanya cap valor.

    Un backfill aqui afirmaria que algu ha mirat uns plecs que no ha mirat
    ningu, que es exactament el que aquestes columnes existeixen per evitar.
    """
    up = _offline_sql("upgrade", _REVIEW)

    assert "UPDATE bid_workspaces" not in up
    assert "SET NOT NULL" not in up
    assert "DEFAULT" not in up


def test_la_revisio_documental_no_afegeix_cap_index_ni_cap_clau():
    """No es busca mai cap workspace per aquests camps: un index seria pes."""
    up = _offline_sql("upgrade", _REVIEW)

    assert "CREATE INDEX" not in up
    assert "FOREIGN KEY" not in up


_REFS = "0029_document_review_state:0030_tender_source_refs"


def test_les_cites_es_poden_aplicar_i_desfer():
    """0029 -> 0030 -> 0029, amb la taula sencera."""
    up = _offline_sql("upgrade", _REFS)
    down = _offline_sql("downgrade", "0030_tender_source_refs:0029_document_review_state")

    assert "CREATE TABLE tender_source_refs" in up
    assert "DROP TABLE tender_source_refs" in down


def test_les_ancores_dune_cita_no_es_poden_esborrar_per_accident():
    """RESTRICT a les tres: l'evidencia citada s'ha de protegir.

    CASCADE hauria fet desapareixer la prova amb el document, i SET NULL hauria
    deixat una cita que no apunta enlloc pero que segueix semblant una cita.
    """
    up = _offline_sql("upgrade", _REFS)

    for fk in ("fk_tender_source_refs_extraction", "fk_tender_source_refs_page",
               "fk_tender_source_refs_file"):
        line = [row for row in up.splitlines() if fk in row]
        assert line, fk
        assert "ON DELETE RESTRICT" in line[0], fk


def test_el_document_logic_nomes_serveix_per_ensenyar_un_nom():
    """SET NULL: si desapareix, la cita segueix reconstruible pel fitxer i el SHA."""
    up = _offline_sql("upgrade", _REFS)

    line = [row for row in up.splitlines() if "fk_tender_source_refs_document" in row]
    assert line
    assert "ON DELETE SET NULL" in line[0]


def test_una_cita_impossible_la_rebutja_la_base_de_dades():
    """Els tres CHECK, amb nom, al DDL. No nomes a Pydantic."""
    up = _offline_sql("upgrade", _REFS)

    for check in ("source_ref_is_anchored", "source_ref_page_is_one_based",
                  "source_ref_char_span_is_coherent"):
        assert check in up, check


def test_lenum_de_cita_no_es_un_tipus_natiu():
    """`native_enum=False`, com tots els enums del projecte: VARCHAR.

    El helper no genera CHECK de valors —es un fet conegut de tot el projecte i
    aqui no es canvia—, pero en aquesta taula el valor queda igualment acotat:
    `source_ref_is_anchored` nomes es cert per a `extracted_page` o
    `document_file`, aixi que qualsevol altra cadena fa fallar la fila.
    """
    up = _offline_sql("upgrade", _REFS)

    assert "ref_kind VARCHAR" in up
    assert "CREATE TYPE" not in up
    anchored = [row for row in up.splitlines() if "source_ref_is_anchored" in row]
    assert anchored
    assert "'extracted_page'" in anchored[0]
    assert "'document_file'" in anchored[0]


def test_les_cites_neixen_sense_cap_fila():
    """Cap backfill: no hi ha cap cita que ningu hagi escrit."""
    up = _offline_sql("upgrade", _REFS)

    assert "INSERT INTO tender_source_refs" not in up


_MANUAL_REQ = "0030_tender_source_refs:0031_manual_requirements"


def test_els_requisits_manuals_es_poden_aplicar_i_desfer():
    up = _offline_sql("upgrade", _MANUAL_REQ)
    down = _offline_sql("downgrade", "0031_manual_requirements:0030_tender_source_refs")

    for fragment in ("ADD COLUMN origin", "ADD COLUMN archived_at",
                     "ADD COLUMN created_by", "ADD COLUMN updated_by",
                     "CREATE TABLE requirement_source_ref_links"):
        assert fragment in up, fragment
    for fragment in ("DROP COLUMN origin", "DROP COLUMN archived_at",
                     "DROP TABLE requirement_source_ref_links"):
        assert fragment in down, fragment


def test_els_requisits_que_ja_hi_havia_son_extrets():
    """Backfill honest: totes les files antigues venen d'una run.

    El `server_default` nomes serveix per omplir-les i es retira tot seguit:
    deixar-lo permanent faria que una fila nova pogues dir-se extreta sense
    ser-ho.
    """
    up = _offline_sql("upgrade", _MANUAL_REQ)

    assert "'extracted'" in up
    assert "DROP DEFAULT" in up


def test_no_sinventa_cap_autor_historic():
    """`created_by` i `updated_by` neixen NULL: no sabem qui va crear les files
    antigues i inventar-ho seria pitjor que no dir-ho."""
    up = _offline_sql("upgrade", _MANUAL_REQ)

    assert "UPDATE requirements SET created_by" not in up
    assert "UPDATE requirements SET updated_by" not in up


def test_un_requisit_extret_sempre_te_run_i_un_de_manual_mai():
    """El CHECK ho garanteix la base de dades, no nomes el servei."""
    up = _offline_sql("upgrade", _MANUAL_REQ)

    assert "requirement_origin_matches_run" in up
    assert "origin = 'extracted' AND run_id IS NOT NULL" in up
    assert "origin = 'manual' AND run_id IS NULL" in up


def test_una_cita_enllacada_no_es_pot_esborrar_per_accident():
    """RESTRICT cap a la cita; CASCADE cap al requisit."""
    up = _offline_sql("upgrade", _MANUAL_REQ)

    source = [row for row in up.splitlines() if "fk_requirement_source_ref_source" in row]
    assert source and "ON DELETE RESTRICT" in source[0]
    requirement = [
        row for row in up.splitlines() if "fk_requirement_source_ref_requirement" in row
    ]
    assert requirement and "ON DELETE CASCADE" in requirement[0]


def test_el_downgrade_esborra_els_manuals_en_comptes_dinventar_runs():
    """0030 no sap representar un requisit sense run.

    L'unica alternativa a esborrar-los seria fabricar-los una
    `RequirementExtractionRun`, que es exactament la mentida que 0031 existeix
    per evitar. Es perdua de dades, i es deliberada.
    """
    down = _offline_sql("downgrade", "0031_manual_requirements:0030_tender_source_refs")

    assert "DELETE FROM requirements WHERE origin = 'manual'" in down
    assert "DELETE FROM requirement_source_ref_links" in down
    assert "INSERT INTO requirement_extraction_runs" not in down
    # I l'esborrat va ABANS de tornar la columna a NOT NULL.
    assert down.index("DELETE FROM requirements") < down.index("SET NOT NULL")


_DIMENSIONS = "0031_manual_requirements:0032_workspace_dimensions"


def test_el_dimensionament_es_pot_aplicar_i_desfer():
    up = _offline_sql("upgrade", _DIMENSIONS)
    down = _offline_sql("downgrade", "0032_workspace_dimensions:0031_manual_requirements")

    for fragment in (
        "CREATE TABLE workspace_dimensions",
        "CREATE TABLE workspace_dimension_observations",
    ):
        assert fragment in up, fragment
    for fragment in (
        "DROP TABLE workspace_dimension_observations",
        "DROP TABLE workspace_dimensions",
    ):
        assert fragment in down, fragment
    # Les observacions se'n van primer: si no, la taula filla es quedaria
    # apuntant a una taula que ja no hi es.
    assert down.index("DROP TABLE workspace_dimension_observations") < down.index(
        "DROP TABLE workspace_dimensions"
    )


def test_nomes_hi_ha_aquestes_dues_taules():
    """0032 no toca res mes. Cap columna afegida a res que ja existia."""
    up = _offline_sql("upgrade", _DIMENSIONS)

    created = {
        row.split("CREATE TABLE ")[1].split(" ")[0].strip()
        for row in up.splitlines()
        if row.startswith("CREATE TABLE ")
    }
    assert created == {"workspace_dimensions", "workspace_dimension_observations"}
    assert "ALTER TABLE" not in up.replace("ALTER TABLE ONLY", "")


def test_les_xifres_son_numeric_i_no_double():
    """`Float` no sap representar 0,1, i aquestes xifres acaben en una oferta."""
    up = _offline_sql("upgrade", _DIMENSIONS)

    assert "NUMERIC(18, 4)" in up
    assert "DOUBLE PRECISION" not in up
    assert "FLOAT" not in up.upper().replace("DEFAULT", "")


def test_una_decisio_sense_motiu_no_hi_cap():
    """El CHECK, no nomes el servei: el motiu es el que fa util la decisio."""
    up = _offline_sql("upgrade", _DIMENSIONS)

    assert "dimension_decided_has_reason" in up
    assert "status <> 'decided' OR decision_reason IS NOT NULL" in up


def test_no_es_pot_repetir_una_magnitud_dins_del_mateix_ambit():
    up = _offline_sql("upgrade", _DIMENSIONS)

    assert "uq_workspace_dimension_key_scope" in up


def test_perdre_una_cita_no_perd_el_que_la_font_deia():
    """SET NULL cap a la cita; CASCADE cap a la dimensio.

    Si fos RESTRICT, esborrar un document deixaria la cita, i amb ella tot el
    dimensionament, impossible de netejar. I si fos CASCADE, perdre la cita
    esborraria l'observacio: el valor que la font deia seguiria sent cert
    encara que ja no en sapiguem el full exacte.
    """
    up = _offline_sql("upgrade", _DIMENSIONS)

    ref = [row for row in up.splitlines() if "fk_dimension_observations_source_ref" in row]
    assert ref and "ON DELETE SET NULL" in ref[0]
    parent = [
        row for row in up.splitlines() if "fk_dimension_observations_dimension" in row
    ]
    assert parent and "ON DELETE CASCADE" in parent[0]


def test_qui_va_decidir_pot_marxar_de_lempresa():
    """SET NULL: esborrar un usuari no ha d'esborrar la decisio que va prendre."""
    up = _offline_sql("upgrade", _DIMENSIONS)

    for name in ("fk_workspace_dimensions_decider", "fk_dimension_observations_author"):
        row = [line for line in up.splitlines() if name in line]
        assert row and "ON DELETE SET NULL" in row[0], name


def test_no_sinventa_cap_dimensio():
    """Les dues taules neixen buides. Cap backfill, cap dimensio automatica.

    Que es una dimensio ho diu una persona. Deduir-les dels numeros que
    apareguin als plecs seria justament el que aquesta fase evita.
    """
    up = _offline_sql("upgrade", _DIMENSIONS)

    assert "INSERT INTO workspace_dimensions" not in up
    assert "INSERT INTO workspace_dimension_observations" not in up


def test_el_downgrade_no_deixa_les_observacions_orfes():
    """Es perdua de dades, i es deliberada: 0031 no coneix cap dimensio."""
    down = _offline_sql("downgrade", "0032_workspace_dimensions:0031_manual_requirements")

    assert "workspace_dimension_observations" in down
    assert "INSERT INTO" not in down


_OPTIONS = "0032_workspace_dimensions:0033_workspace_solution_options"


def test_les_opcions_es_poden_aplicar_i_desfer():
    up = _offline_sql("upgrade", _OPTIONS)
    down = _offline_sql(
        "downgrade", "0033_workspace_solution_options:0032_workspace_dimensions"
    )

    assert "CREATE TABLE workspace_solution_options" in up
    assert "DROP TABLE workspace_solution_options" in down


def test_nomes_hi_ha_aquesta_taula():
    """0033 no toca res mes. Cap columna afegida a res que ja existia."""
    up = _offline_sql("upgrade", _OPTIONS)

    created = {
        row.split("CREATE TABLE ")[1].split(" ")[0].strip()
        for row in up.splitlines()
        if row.startswith("CREATE TABLE ")
    }
    assert created == {"workspace_solution_options"}
    assert "ALTER TABLE" not in up.replace("ALTER TABLE ONLY", "")


def test_loferta_economica_no_es_toca():
    """El vincle amb la linia arriba mes tard; aqui no existeix."""
    up = _offline_sql("upgrade", _OPTIONS)

    assert "economic_offer_lines" not in up
    assert "solution_option_id" not in up
    assert "target_markup_pct" not in up


def test_no_hi_ha_preu_de_venda():
    """Nomes cost. El preu viu a `economic_offer_lines` i prou."""
    up = _offline_sql("upgrade", _OPTIONS)

    assert "unit_cost_ex_vat" in up
    for forbidden in ("unit_price", "sale_price", "margin", "markup"):
        assert forbidden not in up, forbidden


def test_les_xifres_son_numeric_amb_la_seva_escala():
    """Quantitat amb 4 decimals i cost amb 6: un cost unitari pot portar
    centims de centim, i arrodonir-lo es multiplicaria per la quantitat."""
    up = _offline_sql("upgrade", _OPTIONS)

    assert "NUMERIC(18, 4)" in up
    assert "NUMERIC(18, 6)" in up
    assert "DOUBLE PRECISION" not in up


def test_els_enums_tenen_check_de_debo():
    """`enum_column()` no en crea cap: sense aquests, un INSERT que no passes
    per l'ORM hi podria escriure qualsevol cadena."""
    up = _offline_sql("upgrade", _OPTIONS)

    assert "quote_status_valid" in up
    assert "selection_valid" in up
    for value in ("draft", "requested", "pending", "received", "expired", "declined"):
        assert f"'{value}'" in up, value
    for value in ("undecided", "selected", "rejected"):
        assert f"'{value}'" in up, value


def test_descartar_sense_motiu_no_hi_cap():
    up = _offline_sql("upgrade", _OPTIONS)

    assert "rejected_has_reason" in up
    assert "selection <> 'rejected' OR selection_reason IS NOT NULL" in up


def test_perdre_la_solucio_o_el_requisit_no_perd_lopcio():
    """SET NULL a les tres referencies toves; CASCADE nomes cap a la
    candidatura i l'empresa."""
    up = _offline_sql("upgrade", _OPTIONS)

    for name in (
        "fk_solution_options_technical_solution",
        "fk_solution_options_requirement",
        "fk_solution_options_selector",
    ):
        row = [line for line in up.splitlines() if name in line]
        assert row and "ON DELETE SET NULL" in row[0], name


def test_no_sinventa_cap_opcio():
    """Neix buida: cap backfill, cap opcio deduida de res."""
    up = _offline_sql("upgrade", _OPTIONS)

    assert "INSERT INTO workspace_solution_options" not in up


def test_el_downgrade_no_fabrica_cap_linia_economica():
    """Es perdua de dades, i es deliberada: 0032 no coneix cap opcio.

    L'alternativa seria convertir el cost en una linia d'oferta, que es
    exactament la confusio que aquesta fase evita.
    """
    down = _offline_sql(
        "downgrade", "0033_workspace_solution_options:0032_workspace_dimensions"
    )

    assert "INSERT INTO" not in down
    assert "economic_offer_lines" not in down


def test_la_taula_dopcions_i_dimensions_encara_no_existeix():
    """0034. Aquesta fase prova les opcions soles."""
    up = _offline_sql("upgrade", _OPTIONS)

    assert "workspace_solution_option_dimensions" not in up


_LINKS = "0033_workspace_solution_options:0034_solution_option_dimensions"
_LINKS_DOWN = "0034_solution_option_dimensions:0033_workspace_solution_options"


def test_les_dependencies_es_poden_aplicar_i_desfer():
    up = _offline_sql("upgrade", _LINKS)
    down = _offline_sql("downgrade", _LINKS_DOWN)

    assert "CREATE TABLE workspace_solution_option_dimensions" in up
    assert "DROP TABLE workspace_solution_option_dimensions" in down


def test_nomes_hi_ha_aquesta_taula_pont():
    """0034 no toca res mes: ni les opcions, ni les dimensions, ni l'oferta."""
    up = _offline_sql("upgrade", _LINKS)

    created = {
        row.split("CREATE TABLE ")[1].split(" ")[0].strip()
        for row in up.splitlines()
        if row.startswith("CREATE TABLE ")
    }
    assert created == {"workspace_solution_option_dimensions"}
    assert "ALTER TABLE" not in up.replace("ALTER TABLE ONLY", "")
    assert "economic_offer_lines" not in up


def test_nomes_hi_ha_la_parella():
    """Cap snapshot i cap factor: la relacio diu de que depen una opcio i prou.

    Amb una copia del valor aqui hi hauria dues respostes a la mateixa pregunta,
    i un dia no coincidirien sense que ningu sabes quina manava.
    """
    up = _offline_sql("upgrade", _LINKS)

    for forbidden in (
        "working_value_num",
        "working_value_text",
        "snapshot_value_num",
        "snapshot_taken_at",
        "dimension_revision",
        "quantity_factor",
        "multiplier",
    ):
        assert forbidden not in up, forbidden


def test_la_parella_es_unica():
    up = _offline_sql("upgrade", _LINKS)

    assert "uq_solution_option_dimension" in up


def test_els_dos_costats_cascaden():
    """Una dependencia sense cap dels seus dos extrems no li serveix a ningu."""
    up = _offline_sql("upgrade", _LINKS)

    for name in (
        "fk_solution_option_dimension_option",
        "fk_solution_option_dimension_dimension",
    ):
        row = [line for line in up.splitlines() if name in line]
        assert row and "ON DELETE CASCADE" in row[0], name

    author = [
        line for line in up.splitlines() if "fk_solution_option_dimension_author" in line
    ]
    assert author and "ON DELETE SET NULL" in author[0]


def test_hi_ha_index_als_dos_costats():
    """Es consulta en tots dos sentits: de quines depen una opcio, i quines
    opcions fan servir una magnitud."""
    up = _offline_sql("upgrade", _LINKS)

    assert "ix_solution_option_dimensions_option" in up
    assert "ix_solution_option_dimensions_dimension" in up


def test_no_sinventa_cap_dependencia():
    up = _offline_sql("upgrade", _LINKS)

    assert "INSERT INTO workspace_solution_option_dimensions" not in up


def test_el_downgrade_no_toca_ni_opcions_ni_dimensions():
    """Nomes es perden les relacions. Totes dues taules existien abans i totes
    dues continuen sent completes sense aquesta."""
    down = _offline_sql("downgrade", _LINKS_DOWN)

    # El que importa es que no hi hagi cap operacio sobre les dues taules
    # reals. Buscar-hi el nom a pel no serveix: l'identificador de la revisio
    # que s'escriu a `alembic_version` conte "workspace_solution_options".
    touched = [
        line
        for line in down.splitlines()
        if line.startswith(("DROP TABLE", "ALTER TABLE", "DROP INDEX"))
        and "solution_option_dimensions" not in line
    ]
    assert touched == [], touched
    assert "INSERT INTO" not in down
    assert "DELETE FROM" not in down


_PROVENANCE = "0034_solution_option_dimensions:0035_offer_line_solution_option"
_PROVENANCE_DOWN = "0035_offer_line_solution_option:0034_solution_option_dimensions"


def test_la_provinenca_de_la_linia_es_pot_aplicar_i_desfer():
    up = _offline_sql("upgrade", _PROVENANCE)
    down = _offline_sql("downgrade", _PROVENANCE_DOWN)

    assert "ADD COLUMN solution_option_id" in up
    assert "DROP COLUMN solution_option_id" in down


def test_nomes_hi_ha_una_columna_nova():
    """Un sol camp. Ni markup, ni snapshot de cotitzacio, ni res mes."""
    up = _offline_sql("upgrade", _PROVENANCE)

    added = [row for row in up.splitlines() if "ADD COLUMN" in row]
    assert len(added) == 1, added
    for forbidden in (
        "target_markup_pct",
        "synced_at",
        "option_cost_hash",
        "diverged",
        "snapshot",
    ):
        assert forbidden not in up, forbidden
    assert "CREATE TABLE" not in up


def test_la_columna_es_nullable_i_no_hi_ha_backfill():
    """Les linies que ja hi havia no venen de cap opcio: es queden a NULL."""
    up = _offline_sql("upgrade", _PROVENANCE)

    assert "NOT NULL" not in up.split("ADD COLUMN solution_option_id")[1].split("\n")[0]
    assert "UPDATE economic_offer_lines" not in up
    assert "INSERT INTO" not in up


def test_perdre_lopcio_no_perd_la_linia():
    """SET NULL: es perd la traca, no la feina. El preu escrit es conserva."""
    up = _offline_sql("upgrade", _PROVENANCE)

    row = [line for line in up.splitlines() if "fk_economic_offer_lines_solution_option" in line]
    assert row and "ON DELETE SET NULL" in row[0], row


def test_una_sola_linia_viva_per_opcio():
    """L'index es PARCIAL: nomes hi entren les linies vives que venen d'una
    opcio, de manera que una d'arxivada conserva la seva provinenca com a
    historia i alhora deixa l'opcio lliure per tornar-hi."""
    up = _offline_sql("upgrade", _PROVENANCE)

    row = [
        line
        for line in up.splitlines()
        if "uq_economic_offer_lines_solution_option" in line and "CREATE" in line
    ]
    assert row, "falta l'index"
    assert "UNIQUE" in row[0]
    assert "WHERE solution_option_id IS NOT NULL AND archived_at IS NULL" in row[0], row[0]


def test_el_downgrade_no_toca_cap_xifra():
    down = _offline_sql("downgrade", _PROVENANCE_DOWN)

    assert "DELETE FROM" not in down
    assert "INSERT INTO" not in down
    for money in ("unit_cost_ex_vat", "offered_unit_price_ex_vat", "quantity"):
        assert money not in down, money


def test_0036_nomes_porta_lobjectiu_de_markup():
    """0036 es l'amo de `target_markup_pct`, i 0035 no en sabia res.

    `sync-from-option` i el finding de divergencia arriben amb ell, pero sense
    cap columna: son comportament i estat derivat, no esquema.
    """
    up = _offline_sql("upgrade", _PROVENANCE)

    assert "target_markup_pct" not in up, "0035 no el porta"


_TARGET = "0035_offer_line_solution_option:0036_offer_line_target_markup"
_TARGET_DOWN = "0036_offer_line_target_markup:0035_offer_line_solution_option"


def test_lobjectiu_de_markup_es_pot_aplicar_i_desfer():
    up = _offline_sql("upgrade", _TARGET)
    down = _offline_sql("downgrade", _TARGET_DOWN)

    assert "ADD COLUMN target_markup_pct" in up
    assert "DROP COLUMN target_markup_pct" in down


def test_nomes_hi_ha_una_columna_i_es_numeric_9_4():
    """Ni preu proposat, ni coincidencia, ni hash: tot aixo es derivat."""
    up = _offline_sql("upgrade", _TARGET)

    added = [row for row in up.splitlines() if "ADD COLUMN" in row]
    assert len(added) == 1, added
    assert "NUMERIC(9, 4)" in added[0], added[0]
    assert "NOT NULL" not in added[0]
    assert "CREATE TABLE" not in up
    for derived in (
        "proposed_unit_price",
        "price_matches_target",
        "synced_at",
        "diverged",
        "option_hash",
        "last_option_revision",
    ):
        assert derived not in up, derived


def test_cap_rang_al_markup():
    """Una oferta pot anar deliberadament a cost, o per sota."""
    up = _offline_sql("upgrade", _TARGET)

    assert "target_markup_pct >= 0" not in up
    assert "CHECK" not in up


def test_zero_backfill_del_markup():
    up = _offline_sql("upgrade", _TARGET)

    assert "UPDATE economic_offer_lines" not in up
    assert "INSERT INTO" not in up


def test_el_downgrade_no_toca_cap_preu():
    down = _offline_sql("downgrade", _TARGET_DOWN)

    for money in ("offered_unit_price_ex_vat", "unit_cost_ex_vat", "quantity"):
        assert money not in down, money
    assert "DELETE FROM" not in down


def test_0036_no_porta_cap_dubte():
    """Els dubtes son de 0037. 0036 nomes porta l'objectiu de markup."""
    up = _offline_sql("upgrade", _TARGET)

    assert "workspace_issues" not in up
    assert "blocks_final_review" not in up


_ISSUES = "0036_offer_line_target_markup:0037_workspace_issues"
_ISSUES_DOWN = "0037_workspace_issues:0036_offer_line_target_markup"


def test_els_dubtes_es_poden_aplicar_i_desfer():
    up = _offline_sql("upgrade", _ISSUES)
    down = _offline_sql("downgrade", _ISSUES_DOWN)

    for fragment in ("CREATE TABLE workspace_issues", "CREATE TABLE workspace_issue_source_refs"):
        assert fragment in up, fragment
    # Els enllacos se'n van primer: si no, la taula filla quedaria apuntant a
    # una taula que ja no hi es.
    assert down.index("DROP TABLE workspace_issue_source_refs") < down.index(
        "DROP TABLE workspace_issues"
    )


def test_0037_nomes_crea_les_dues_taules_de_dubtes():
    up = _offline_sql("upgrade", _ISSUES)

    created = {
        row.split("CREATE TABLE ")[1].split(" ")[0].strip()
        for row in up.splitlines()
        if row.startswith("CREATE TABLE ")
    }
    assert created == {"workspace_issues", "workspace_issue_source_refs"}
    assert "ALTER TABLE" not in up.replace("ALTER TABLE ONLY", "")


def test_no_hi_ha_source_refs_json():
    """El disseny antic queda superat: la provinenca va a una taula relacional."""
    up = _offline_sql("upgrade", _ISSUES)

    assert "source_refs_json" not in up
    assert "JSON" not in up.upper().replace("JSONB", "")


def test_els_enums_dels_dubtes_tenen_check():
    up = _offline_sql("upgrade", _ISSUES)

    assert "issue_type_valid" in up
    assert "issue_status_valid" in up
    for value in ("internal_question", "authority_question", "supplier_question",
                  "contradiction", "missing_information"):
        assert f"'{value}'" in up, value
    for value in ("open", "waiting", "resolved", "dismissed"):
        assert f"'{value}'" in up, value


def test_resolt_implica_resposta_escrita():
    up = _offline_sql("upgrade", _ISSUES)

    assert "resolved_has_resolution" in up
    assert "status <> 'resolved' OR resolution IS NOT NULL" in up


def test_bloquejar_neix_a_fals():
    up = _offline_sql("upgrade", _ISSUES)

    row = [line for line in up.splitlines() if "blocks_final_review" in line]
    assert row and "NOT NULL" in row[0], row
    assert "false" in row[0].lower(), row[0]


def test_els_vincles_del_context_son_set_null():
    """Si l'entitat desapareix, la pregunta i la resposta continuen sent certes."""
    up = _offline_sql("upgrade", _ISSUES)

    for name in (
        "fk_issue_requirement",
        "fk_issue_criterion",
        "fk_issue_technical_solution",
        "fk_issue_option",
        "fk_issue_offer_line",
        "fk_issue_dimension",
        "fk_issue_owner",
        "fk_issue_resolver",
    ):
        row = [line for line in up.splitlines() if name in line]
        assert row and "ON DELETE SET NULL" in row[0], name


def test_la_cita_no_es_pot_esborrar_per_accident():
    up = _offline_sql("upgrade", _ISSUES)

    source = [line for line in up.splitlines() if "fk_issue_ref_source" in line]
    assert source and "ON DELETE RESTRICT" in source[0], source
    issue = [line for line in up.splitlines() if "fk_issue_ref_issue" in line]
    assert issue and "ON DELETE CASCADE" in issue[0], issue


def test_una_cita_no_es_repeteix_dins_del_mateix_dubte():
    up = _offline_sql("upgrade", _ISSUES)

    assert "uq_workspace_issue_source_ref" in up


def test_els_index_congelats_hi_son():
    up = _offline_sql("upgrade", _ISSUES)

    assert "ix_workspace_issues_status" in up
    assert "ix_workspace_issues_type" in up


def test_no_sinventa_cap_dubte():
    """Les dues taules neixen buides: cap issue automatic, tampoc a la migracio."""
    up = _offline_sql("upgrade", _ISSUES)

    assert "INSERT INTO workspace_issues" not in up
    assert "INSERT INTO workspace_issue_source_refs" not in up


def test_el_downgrade_no_toca_res_mes():
    down = _offline_sql("downgrade", _ISSUES_DOWN)

    touched = [
        line
        for line in down.splitlines()
        if line.startswith(("DROP TABLE", "ALTER TABLE", "DROP INDEX"))
        and "workspace_issue" not in line
    ]
    assert touched == [], touched
    assert "INSERT INTO" not in down
    assert "DELETE FROM" not in down


def test_0037_no_porta_res_del_contracte_v3():
    """S3-S10 acaba a 0037. El contracte v3 comenca a 0038, en revisions propies."""
    up = _offline_sql("upgrade", _ISSUES)

    assert "claim_support_json" not in up
    assert "adjudication_basis_json" not in up


# --- Extraction Contract v3 (0038, 0039) --------------------------------------

_CLAIM_SUPPORT = "0037_workspace_issues:0038_evidence_claim_support"
_CLAIM_SUPPORT_DOWN = "0038_evidence_claim_support:0037_workspace_issues"
_ADJUDICATION = "0038_evidence_claim_support:0039_award_adjudication_basis"
_ADJUDICATION_DOWN = "0039_award_adjudication_basis:0038_evidence_claim_support"


@pytest.mark.parametrize(
    ("up_range", "down_range", "table", "column"),
    [
        (_CLAIM_SUPPORT, _CLAIM_SUPPORT_DOWN, "requirement_evidence", "claim_support_json"),
        (_ADJUDICATION, _ADJUDICATION_DOWN, "award_criteria", "adjudication_basis_json"),
    ],
)
def test_el_contracte_v3_es_pot_aplicar_i_desfer(up_range, down_range, table, column):
    """Una sola columna JSON nullable per revisio, sense default ni backfill.

    `NULL` es "fila anterior a la v3": suport o base desconeguts. Omplir-la per
    a les files antigues seria afirmar una comprovacio que no es va fer.
    """
    def domain(sql: str) -> str:
        # La fila d'`alembic_version` l'escriu el propi Alembic a cada revisio.
        return "\n".join(line for line in sql.splitlines() if "alembic_version" not in line)

    up = domain(_offline_sql("upgrade", up_range))
    down = domain(_offline_sql("downgrade", down_range))

    added = [row for row in up.splitlines() if "ADD COLUMN" in row]
    assert added == [f"ALTER TABLE {table} ADD COLUMN {column} JSON;"], added
    for forbidden in ("NOT NULL", "DEFAULT", "UPDATE ", "INSERT INTO", "CREATE TABLE",
                      "CREATE INDEX", "CHECK", "DROP "):
        assert forbidden not in up, forbidden

    dropped = [row for row in down.splitlines() if row.startswith(("ALTER TABLE", "DROP "))]
    assert dropped == [f"ALTER TABLE {table} DROP COLUMN {column};"], dropped
    assert "DELETE FROM" not in down
    assert "UPDATE " not in down


def test_0038_nomes_toca_les_cites_de_requisit():
    up = _offline_sql("upgrade", _CLAIM_SUPPORT)

    assert "award_criteria" not in up
    assert "award_criterion_evidence" not in up


def test_0039_nomes_toca_els_criteris():
    up = _offline_sql("upgrade", _ADJUDICATION)

    assert "requirement_evidence" not in up
    assert "award_criterion_evidence" not in up


_REVIEW_TABLES = (
    "review_items", "review_item_sources", "review_item_revisions", "review_item_relations",
)


def test_la_capa_de_revisio_es_pot_aplicar_i_desfer():
    """0039 -> 0040 -> 0039: quatre taules noves i res mes; el downgrade ho treu tot."""
    up = _offline_sql("upgrade", "0039_award_adjudication_basis:0040_review_layer")
    down = _offline_sql("downgrade", "0040_review_layer:0039_award_adjudication_basis")

    for table in _REVIEW_TABLES:
        assert f"CREATE TABLE {table}" in up
        assert f"DROP TABLE {table}" in down
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    dropped = set(re.findall(r"DROP INDEX (\w+)", down))
    assert created and created == dropped
    # Cap taula d'extraccio no es toca.
    for table in ("requirements", "requirement_evidence", "award_criteria",
                  "award_criterion_evidence", "requirement_extraction_runs"):
        assert f"ALTER TABLE {table}" not in up
        assert f"DROP TABLE {table}" not in down
    # L'historial nomes d'afegir, a PostgreSQL, i el downgrade el treu.
    assert "CREATE TRIGGER review_item_revisions_append_only" in up
    assert "DROP TRIGGER IF EXISTS review_item_revisions_append_only" in down
    assert "DROP FUNCTION IF EXISTS review_item_revisions_append_only()" in down


def test_els_enums_i_les_regles_de_revisio_els_imposa_la_base():
    up = _offline_sql("upgrade", "0039_award_adjudication_basis:0040_review_layer")

    for name in (
        "ck_review_items_kind_valid", "ck_review_items_verification_valid",
        "ck_review_items_award_role_valid", "ck_review_items_origin_hint_valid",
        "ck_review_item_sources_exactly_one_source", "ck_review_item_sources_link_role_valid",
        "ck_review_item_sources_source_ref_is_extra_evidence",
        "ck_review_item_revisions_action_valid",
        "ck_review_item_revisions_verification_after_valid",
        "ck_review_item_relations_relation_type_valid", "ck_review_item_relations_not_self",
    ):
        assert name in up, name
    assert "uq_review_item_sources_company_requirement" in up
    assert "WHERE requirement_id IS NOT NULL AND link_role = 'primary'" in up
    # Una font amb historial no s'esborra sola, i la tenancy la lliga la clau composta.
    assert "REFERENCES requirements (id) ON DELETE NO ACTION" in up
    assert "REFERENCES review_items (id, company_id) ON DELETE CASCADE" in up
    assert "REFERENCES review_items (id, company_id, tender_id) ON DELETE CASCADE" in up


def test_la_reconciliacio_es_pot_aplicar_i_desfer():
    """0040 -> 0041 -> 0040: taula nova, CHECK d'accions ampliat i tornat enrere.

    El downgrade es refusa (a la base, abans de tocar res) si hi ha revisions
    `reconcile`: el CHECK antic no les admet i esborrar-les seria perdre historial.
    """
    up = _offline_sql("upgrade", "0040_review_layer:0041_review_reconciliation")
    down = _offline_sql("downgrade", "0041_review_reconciliation:0040_review_layer")

    assert "CREATE TABLE review_reconciliations" in up
    assert "uq_review_reconciliations_target UNIQUE (company_id, tender_id, target_run_id)" in up
    new_check = up.split("ADD CONSTRAINT ck_review_item_revisions_action_valid")[1]
    assert "'reconcile'" in new_check.split(";")[0]
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    assert created == set(re.findall(r"DROP INDEX (\w+)", down))
    assert "DROP TABLE review_reconciliations" in down
    refusal = down.index("RAISE EXCEPTION")
    assert refusal < down.index("DROP CONSTRAINT ck_review_item_revisions_action_valid")
    old_check = down.split("ADD CONSTRAINT ck_review_item_revisions_action_valid")[1]
    assert "'reconcile'" not in old_check.split(";")[0]
    assert "DELETE" not in down.upper().replace("ON DELETE", "")


def test_l_estat_del_client_es_pot_aplicar_i_desfer():
    """0042 -> 0043 -> 0042: l'estat del client (consola Tuvi), amb les seves regles.

    Les empreses existents queden `active` (server default), l'estat nomes
    admet els tres valors, un pilot sempre te fi, i el downgrade ho treu tot
    sense esborrar cap fila.
    """
    up = _offline_sql("upgrade", "0042_user_password_change:0043_company_platform_status")
    down = _offline_sql("downgrade", "0043_company_platform_status:0042_user_password_change")

    for column in ("platform_status", "pilot_started_at", "pilot_ends_at", "status_changed_at",
                   "onboarded_by"):
        assert f"ALTER TABLE companies ADD COLUMN {column}" in up
        assert f"ALTER TABLE companies DROP COLUMN {column}" in down
    assert "platform_status VARCHAR(6) DEFAULT 'active' NOT NULL" in up
    for name in ("ck_companies_platform_status_valid", "ck_companies_pilot_has_end",
                 "ck_companies_pilot_dates_ordered", "fk_companies_onboarded_by_users"):
        assert f"ADD CONSTRAINT {name}" in up
        assert f"DROP CONSTRAINT {name}" in down
    status_check = up.split("ADD CONSTRAINT ck_companies_platform_status_valid")[1].split(";")[0]
    assert "'pilot'" in status_check and "'active'" in status_check and "'paused'" in status_check
    assert "ON DELETE SET NULL" in up
    assert "DELETE" not in down.upper().replace("ON DELETE", "")


def test_la_recepcio_i_la_classificacio_es_poden_aplicar_i_desfer():
    """0043 -> 0044 -> 0043: sis taules noves en dues capes, i res mes.

    Els enums porten CHECK (la resta del projecte encara no), la tenancy de les
    filles la lliga la clau composta, el contenidor original no es pot esborrar
    mentre una recepcio hi apunti, i l'unic lligam entre la recepcio i la
    classificacio es informatiu (`SET NULL`).
    """
    up = _offline_sql("upgrade", "0043_company_platform_status:0044_reception_classification")
    down = _offline_sql("downgrade", "0044_reception_classification:0043_company_platform_status")

    tables = (
        "tender_document_receptions", "tender_document_reception_items",
        "tender_document_classification_runs", "tender_document_role_suggestions",
        "tender_document_classification_findings", "tender_document_role_decisions",
    )
    for table in tables:
        assert f"CREATE TABLE {table}" in up, table
        assert f"DROP TABLE {table}" in down, table
    for name in (
        "ck_tender_document_receptions_channel_valid",
        "ck_tender_document_reception_items_skip_reason_iff_skipped",
        "ck_tender_document_reception_items_document_only_if_accepted",
        "ck_tender_document_classification_runs_trigger_valid",
        "ck_tender_document_role_suggestions_role_valid",
        "ck_tender_document_role_suggestions_document_type_valid",
        "ck_tender_document_classification_findings_code_valid",
        "ck_tender_document_role_decisions_role_valid",
    ):
        assert name in up, name
    assert "REFERENCES tender_document_receptions (id, company_id) ON DELETE CASCADE" in up
    assert (
        "REFERENCES tender_document_classification_runs (id, company_id) ON DELETE CASCADE" in up
    )
    assert "REFERENCES tender_document_contents (id) ON DELETE RESTRICT" in up
    assert "REFERENCES tender_document_receptions (id) ON DELETE SET NULL" in up
    assert "uq_tender_document_classification_runs_current" in up
    assert "WHERE is_current" in up
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    assert created == set(re.findall(r"DROP INDEX (\w+)", down))
    # Cap taula existent no es toca.
    assert "ALTER TABLE" not in up.replace("ALTER TABLE alembic_version", "")


def test_la_telefonia_mobil_entra_al_vocabulari_i_es_pot_desfer():
    """0045 -> 0046 -> 0045: una linia de servei mes, i res mes.

    El downgrade treu la fila; si alguna empresa ja la fa servir, la clau forana
    ho impedeix, que es el que toca: no s'esborra coneixement d'amagat.
    """
    up = _offline_sql("upgrade", "0045_company_knowledge:0046_mobile_telephony_line")
    down = _offline_sql("downgrade", "0046_mobile_telephony_line:0045_company_knowledge")

    assert up.count("INSERT INTO service_lines") == 1
    assert "VALUES ('mobile_telephony', 'Telefonia mobil')" in up
    for forbidden in ("CREATE TABLE", "ALTER TABLE company", "DROP", "capability_definitions",
                      "certification_standards"):
        assert forbidden not in up, forbidden
    assert "DELETE FROM service_lines WHERE code = 'mobile_telephony'" in down
    assert down.count("DELETE") == 1
    for forbidden in ("DROP TABLE", "ALTER TABLE company", "company_services", "partner_roles"):
        assert forbidden not in down, forbidden


def test_el_coneixement_de_l_empresa_es_pot_aplicar_i_desfer():
    """0044 -> 0045 -> 0044: vocabularis globals omplerts, estat actual, historic.

    Les taules existents (serveis i certificacions) nomes guanyen columnes, i el
    downgrade les hi treu sense esborrar cap fila. Els vocabularis s'omplen a la
    mateixa migracio i no porten `company_id`.
    """
    up = _offline_sql("upgrade", "0044_reception_classification:0045_company_knowledge")
    down = _offline_sql("downgrade", "0045_company_knowledge:0044_reception_classification")

    new_tables = (
        "service_lines", "capability_definitions", "certification_standards",
        "company_operating_facts", "company_solutions", "solution_capabilities",
        "company_products", "solution_products", "company_partners", "partner_roles",
        "historic_bids", "knowledge_observations",
    )
    for table in new_tables:
        assert f"CREATE TABLE {table}" in up, table
        assert f"DROP TABLE {table}" in down, table
    vocabularies = up.split("CREATE TABLE company_operating_facts")[0]
    assert "company_id" not in vocabularies
    assert up.count("INSERT INTO service_lines") == 9
    assert up.count("INSERT INTO capability_definitions") == 19
    assert up.count("INSERT INTO certification_standards") == 4
    assert """'["basic", "medium", "high"]'""" in up

    for column in ("service_line_code", "delivery_mode", "knowledge_origin", "as_of",
                   "valid_until", "verification_status"):
        assert f"ALTER TABLE company_services ADD COLUMN {column}" in up, column
        assert f"ALTER TABLE company_services DROP COLUMN {column}" in down, column
    for column in ("standard_code", "level", "scope_service_lines", "knowledge_origin", "as_of",
                   "lifecycle"):
        assert f"ALTER TABLE company_certifications ADD COLUMN {column}" in up, column
        assert f"ALTER TABLE company_certifications DROP COLUMN {column}" in down, column
    assert "UPDATE company_services SET as_of = CAST(created_at AS DATE)" in up
    assert "UPDATE company_certifications SET as_of = CAST(created_at AS DATE)" in up

    for name in (
        "ck_company_operating_facts_lifecycle_valid", "ck_solution_capabilities_status_valid",
        "ck_partner_roles_role_valid", "ck_knowledge_observations_promoted_has_target",
        "ck_company_certifications_level_needs_standard", "ck_company_services_verification_valid",
    ):
        assert name in up, name
    assert "REFERENCES company_solutions (id, company_id) ON DELETE CASCADE" in up
    assert "REFERENCES company_partners (id, company_id) ON DELETE CASCADE" in up
    assert "REFERENCES historic_bids (id) ON DELETE SET NULL" in up
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    assert created == set(re.findall(r"DROP INDEX (\w+)", down))
    assert "DELETE" not in down.upper().replace("ON DELETE", "")


def test_la_cobertura_territorial_es_pot_aplicar_i_desfer_sense_tocar_cap_dada():
    """0046 -> 0047 -> 0046: una taula d'arees i una columna de completesa a
    tres taules, amb `unknown` per defecte. Cap INSERT, UPDATE ni DELETE: la
    migracio no crea cap area ni transforma cap text (`detail`, `territory`).
    """
    up = _offline_sql("upgrade", "0046_mobile_telephony_line:0047_knowledge_coverage")
    down = _offline_sql("downgrade", "0047_knowledge_coverage:0046_mobile_telephony_line")

    assert "CREATE TABLE knowledge_coverage_areas" in up
    table = up.split("CREATE TABLE knowledge_coverage_areas")[1].split("\n);")[0]
    for column in (
        "company_id UUID NOT NULL",
        "entity_type VARCHAR(14) NOT NULL",
        "entity_id UUID NOT NULL",
        "kind VARCHAR(12) NOT NULL",
        "label VARCHAR(200) NOT NULL",
        "code VARCHAR(40)",
        "code_system VARCHAR(20)",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "id UUID NOT NULL",
    ):
        assert column in table, column
    assert "entity_type IN ('operating_fact', 'service', 'partner_role')" in table
    assert (
        "kind IN ('municipality', 'comarca', 'province', 'region', 'country', 'address', 'custom')"
    ) in table
    assert "REFERENCES companies (id) ON DELETE CASCADE" in table
    assert "CREATE INDEX ix_knowledge_coverage_areas_entity" in up
    # Les arees son un conjunt: la base ho garanteix, amb els NULL com a cadena buida.
    assert (
        "CREATE UNIQUE INDEX uq_knowledge_coverage_areas_identity ON knowledge_coverage_areas "
        "(entity_type, entity_id, kind, coalesce(code_system, ''), coalesce(code, ''), label)"
    ) in up
    assert "DROP INDEX uq_knowledge_coverage_areas_identity" in down
    for covered in ("company_operating_facts", "company_services", "partner_roles"):
        assert (
            f"ALTER TABLE {covered} ADD COLUMN coverage_completeness VARCHAR(8) "
            "DEFAULT 'unknown' NOT NULL"
        ) in up, covered
        assert (
            f"ALTER TABLE {covered} ADD CONSTRAINT ck_{covered}_coverage_completeness_valid "
            "CHECK (coverage_completeness IN ('complete', 'partial', 'unknown'))"
        ) in up, covered
        assert f"ALTER TABLE {covered} DROP COLUMN coverage_completeness" in down, covered
        assert f"DROP CONSTRAINT ck_{covered}_coverage_completeness_valid" in down, covered
    statements = up.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP"):
        assert forbidden not in statements, forbidden
    assert "DROP TABLE knowledge_coverage_areas" in down
    assert "DROP INDEX ix_knowledge_coverage_areas_entity" in down
    assert "INSERT INTO" not in down and "DELETE FROM" not in down


def test_l_estructura_del_plec_es_pot_aplicar_i_desfer_sense_tocar_cap_fila():
    """0047 -> 0048 -> 0047: columnes nul·lables a `tenders`, `timing = unknown` a
    requisits i criteris, i sis taules noves buides. Cap INSERT, UPDATE ni DELETE.
    """
    up = _offline_sql("upgrade", "0047_knowledge_coverage:0048_tender_structure")
    down = _offline_sql("downgrade", "0048_tender_structure:0047_knowledge_coverage")

    for column in ("duration_months INTEGER", "extension_months INTEGER",
                   "regulated_harmonised BOOLEAN", "planned_start_date DATE"):
        assert f"ALTER TABLE tenders ADD COLUMN {column};" in up, column
    assert "DEFAULT" not in up.split("CREATE TABLE tender_blocks")[0].replace(
        "DEFAULT 'unknown'", "")  # cap valor per defecte a les quatre columnes
    assert "CHECK (duration_months IS NULL OR duration_months >= 0)" in up
    assert "CHECK (extension_months IS NULL OR extension_months >= 0)" in up
    for table in ("tender_blocks", "tender_sites", "requirement_blocks", "award_criterion_blocks",
                  "tender_block_source_ref_links", "tender_site_source_ref_links"):
        assert f"CREATE TABLE {table} (" in up, table
        assert f"DROP TABLE {table};" in down, table
    for timed in ("requirements", "award_criteria"):
        assert (
            f"ALTER TABLE {timed} ADD COLUMN timing VARCHAR(13) DEFAULT 'unknown' NOT NULL"
        ) in up
        assert (f"CONSTRAINT ck_{timed}_timing_valid CHECK (timing IN ('at_submission', "
                "'at_execution', 'ambiguous', 'unknown'))") in up
        assert f"ALTER TABLE {timed} DROP COLUMN timing;" in down
    assert "CONSTRAINT uq_tender_blocks_tender_code UNIQUE (tender_id, code)" in up
    assert "CONSTRAINT uq_tender_sites_tender_code UNIQUE (tender_id, code)" in up
    assert "CONSTRAINT ck_tender_sites_role_valid CHECK (role IN ('central', 'remote'))" in up
    sites = up.split("CREATE TABLE tender_sites (")[1].split("\n);")[0]
    assert "municipality_code VARCHAR(10)," in sites
    # El sistema del codi municipal: lliure i nul·lable, sense cap valor per defecte.
    assert "municipality_code_system VARCHAR(20)," in sites and "DEFAULT" not in sites.split(
        "created_at"
    )[0]
    assert "CONSTRAINT pk_requirement_blocks PRIMARY KEY (requirement_id, block_id)" in up
    assert "CONSTRAINT pk_award_criterion_blocks PRIMARY KEY (award_criterion_id, block_id)" in up
    assert "REFERENCES tender_source_refs (id) ON DELETE RESTRICT" in up
    assert up.count("REFERENCES tenders (id) ON DELETE CASCADE") == 2
    statements = up.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP"):
        assert forbidden not in statements, forbidden
    assert "INSERT INTO" not in down and "DELETE FROM" not in down
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    assert created == set(re.findall(r"DROP INDEX (\w+)", down))


def test_l_abast_del_plec_es_pot_aplicar_i_desfer_sense_tocar_cap_fila():
    """0048 -> 0049 -> 0048: nou taules noves buides i tres columnes neutres a
    `requirements` (`territory_match_mode = 'any'`, `proof_json`/`proof_schema`
    NULL). Cap INSERT, UPDATE ni DELETE; cap gold carregat.
    """
    up = _offline_sql("upgrade", "0048_tender_structure:0049_tender_scope")
    down = _offline_sql("downgrade", "0049_tender_scope:0048_tender_structure")

    tables = (
        "tender_scope_items", "tender_scope_item_source_ref_links", "requirement_territories",
        "requirement_capability_needs", "requirement_capability_need_source_ref_links",
        "requirement_award_links", "tender_contract_facts", "contract_fact_blocks",
        "tender_contract_fact_source_ref_links",
    )
    for table in tables:
        assert f"CREATE TABLE {table} (" in up, table
        assert f"DROP TABLE {table};" in down, table
    assert (
        "ALTER TABLE requirements ADD COLUMN territory_match_mode VARCHAR(3) "
        "DEFAULT 'any' NOT NULL"
    ) in up
    assert "ALTER TABLE requirements ADD COLUMN proof_json JSON;" in up
    assert "ALTER TABLE requirements ADD COLUMN proof_schema VARCHAR(50);" in up
    assert "CHECK (territory_match_mode IN ('all', 'any'))" in up
    assert "CONSTRAINT ck_requirements_proof_json_versioned CHECK" in up
    scope = up.split("CREATE TABLE tender_scope_items (")[1].split("\
);")[0]
    assert "quantity NUMERIC(14, 3)" in scope and "service_line_code VARCHAR(40) NOT NULL" in scope
    assert (
        "CONSTRAINT ck_tender_scope_items_scope_block_xor_lot "
        "CHECK (block_id IS NULL OR tender_lot_id IS NULL)"
    ) in scope
    assert "REFERENCES service_lines (code) ON DELETE RESTRICT" in scope
    assert "REFERENCES tender_blocks (id) ON DELETE CASCADE" in scope
    needs = up.split("CREATE TABLE requirement_capability_needs (")[1].split("\
);")[0]
    assert "CHECK ((capability_code IS NULL) <> (unmapped_capability IS NULL))" in needs
    assert "REFERENCES capability_definitions (code) ON DELETE RESTRICT" in needs
    assert (
        "CONSTRAINT pk_requirement_award_links PRIMARY KEY (requirement_id, award_criterion_id)"
    ) in up
    assert "CONSTRAINT pk_contract_fact_blocks PRIMARY KEY (fact_id, block_id)" in up
    assert up.count("REFERENCES tender_source_refs (id) ON DELETE RESTRICT") == 3
    for timed in ("territory_match_mode", "proof_json", "proof_schema"):
        assert f"ALTER TABLE requirements DROP COLUMN {timed};" in down
    statements = up.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP"):
        assert forbidden not in statements, forbidden
    assert "INSERT INTO" not in down and "DELETE FROM" not in down
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\\w+)", up))
    assert created == set(re.findall(r"DROP INDEX (\\w+)", down))


def test_la_semantica_del_plec_es_pot_aplicar_i_desfer_sense_reinterpretar_cap_fila():
    """0049 -> 0050 -> 0049: `before_formalization` a `timing`, seus amb critica en
    tri-estat i identitat parcial, `territory_completeness` i les cites dels criteris.
    L'upgrade no fa cap INSERT, UPDATE ni DELETE; el downgrade tampoc: si hi ha files
    que 0049 no pot guardar, s'atura abans de cap DDL (la precondicio es un bloc DO al
    SQL offline).
    """
    up = _offline_sql("upgrade", "0049_tender_scope:0050_tender_semantics")
    down = _offline_sql("downgrade", "0050_tender_semantics:0049_tender_scope")

    for timed in ("requirements", "award_criteria"):
        assert f"ALTER TABLE {timed} DROP CONSTRAINT ck_{timed}_timing_valid;" in up
        assert f"ALTER TABLE {timed} ALTER COLUMN timing TYPE VARCHAR(20);" in up
        assert (
            f"CONSTRAINT ck_{timed}_timing_valid CHECK (timing IN ('at_submission', "
            "'before_formalization', 'at_execution', 'ambiguous', 'unknown'))"
        ) in up
        assert f"ALTER TABLE {timed} ALTER COLUMN timing TYPE VARCHAR(13);" in down
        assert f"UPDATE {timed}" not in down, "cap reinterpretacio de timing"
    for column in ("is_critical", "code", "name"):
        assert f"ALTER TABLE tender_sites ALTER COLUMN {column} DROP NOT NULL;" in up
        assert f"ALTER TABLE tender_sites ALTER COLUMN {column} SET NOT NULL;" in down
    assert (
        "CONSTRAINT ck_tender_sites_site_has_identity CHECK (code IS NOT NULL OR name IS NOT NULL)"
    ) in up
    assert "DEFAULT false" not in up.lower() and "default 'false'" not in up.lower()
    assert (
        "ALTER TABLE requirements ADD COLUMN territory_completeness VARCHAR(14) "
        "DEFAULT 'unknown' NOT NULL"
    ) in up
    assert (
        "CHECK (territory_completeness IN ('not_applicable', 'complete', 'partial', 'unknown'))"
    ) in up
    assert "ALTER TABLE requirements DROP COLUMN territory_completeness;" in down
    links = up.split("CREATE TABLE award_criterion_source_ref_links (")[1].split("\n);")[0]
    assert "REFERENCES award_criteria (id) ON DELETE CASCADE" in links
    assert "REFERENCES tender_source_refs (id) ON DELETE RESTRICT" in links
    assert "REFERENCES users (id) ON DELETE SET NULL" in links
    assert (
        "CONSTRAINT uq_award_criterion_source_ref UNIQUE (award_criterion_id, source_ref_id)"
    ) in links
    assert "DROP TABLE award_criterion_source_ref_links;" in down
    statements = up.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE", "DROP COLUMN"):
        assert forbidden not in statements, forbidden
    # El downgrade no reinterpreta ni esborra: refusa. La precondicio es al SQL mateix,
    # abans de qualsevol DDL.
    for forbidden in ("INSERT INTO", "UPDATE tender_sites", "DELETE FROM", "COALESCE"):
        assert forbidden not in down, forbidden
    guard = down.split("DO $$")[1].split("END $$")[0]
    assert down.index("DO $$") < down.index("DROP INDEX"), "la guarda va abans de cap DDL"
    assert guard.count("RAISE EXCEPTION 'downgrade_0050_incompatible_rows") == 4
    for condition in (
        "FROM requirements WHERE timing = 'before_formalization'",
        "FROM award_criteria WHERE timing = 'before_formalization'",
        "FROM tender_sites WHERE is_critical IS NULL",
        "FROM tender_sites WHERE code IS NULL OR name IS NULL",
    ):
        assert condition in guard, condition
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    assert created == set(re.findall(r"DROP INDEX (\w+)", down))


def test_l_analisi_es_pot_aplicar_i_desfer_perque_son_dades_derivades():
    """0050 -> 0051 -> 0050: set taules noves, buides, que pengen de la run amb
    CASCADE. Cap INSERT ni backfill. El downgrade les elimina: a diferencia de 0050,
    aqui no hi ha cap font de veritat, nomes resultats reproduibles.
    """
    up = _offline_sql("upgrade", "0050_tender_semantics:0051_tender_analysis")
    down = _offline_sql("downgrade", "0051_tender_analysis:0050_tender_semantics")

    tables = (
        "tender_analysis_runs", "tender_fit_assessments", "capability_need_assessments",
        "criterion_assessments", "delivery_candidates", "analysis_findings",
        "analysis_questions",
    )
    for table in tables:
        assert f"CREATE TABLE {table} (" in up, table
        assert f"DROP TABLE {table};" in down, table
    runs = up.split("CREATE TABLE tender_analysis_runs (")[1].split("\n);")[0]
    for column in ("tender_structure_fingerprint VARCHAR(64) NOT NULL",
                   "knowledge_fingerprint VARCHAR(64) NOT NULL",
                   "rules_version VARCHAR(40) NOT NULL",
                   "inputs_hash VARCHAR(64) NOT NULL", "review_revision_hash VARCHAR(64),",
                   "is_current BOOLEAN NOT NULL"):
        assert column in runs, column
    assert "DEFAULT" not in runs.split("created_at")[0], "cap valor per defecte"
    assert (
        "CONSTRAINT uq_tender_analysis_runs_inputs UNIQUE (company_id, tender_id, inputs_hash)"
    ) in runs
    assert (
        "CREATE UNIQUE INDEX uq_tender_analysis_runs_current ON tender_analysis_runs "
        "(company_id, tender_id) WHERE is_current"
    ) in up
    assert up.count("REFERENCES tender_analysis_runs (id) ON DELETE CASCADE") == 6
    assert "REFERENCES companies (id) ON DELETE CASCADE" in runs
    assert "REFERENCES tenders (id) ON DELETE CASCADE" in runs
    for optional in ("tender_revisions", "requirement_extraction_runs",
                     "tender_document_classification_runs"):
        assert f"REFERENCES {optional} (id) ON DELETE SET NULL" in runs, optional
    findings = up.split("CREATE TABLE analysis_findings (")[1].split("\n);")[0]
    assert "blocker_confirmed" not in findings, "el motor no pot escriure cap bloqueig confirmat"
    assert ("CHECK (finding_type IN ('blocker_potential', 'review_required', "
            "'missing_information', 'evidence_gap', 'territory_gap', 'delivery_gap', "
            "'warning'))") in findings
    criteria = up.split("CREATE TABLE criterion_assessments (")[1].split("\n);")[0]
    assert "max_points FLOAT NOT NULL" in criteria
    assert "not_assessable_points FLOAT NOT NULL" in criteria
    candidates = up.split("CREATE TABLE delivery_candidates (")[1].split("\n);")[0]
    assert "CHECK (candidate_type <> 'own_future' OR origin = 'hypothesis')" in candidates
    assert "CONSTRAINT uq_analysis_questions_dedupe UNIQUE (run_id, dedupe_key)" in up
    statements = up.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP", "ALTER TABLE"):
        assert forbidden not in statements, forbidden
    assert "INSERT INTO" not in down and "UPDATE " not in down.replace("UPDATE alembic_version", "")
    created = set(re.findall(r"CREATE (?:UNIQUE )?INDEX (\w+)", up))
    assert created == set(re.findall(r"DROP INDEX (\w+)", down))


def test_la_qualificacio_s_aplica_neutra_i_el_downgrade_refusa_en_comptes_de_reinterpretar():
    """0051 -> 0052 -> 0051. L'upgrade afegeix origen a cinc taules del perfil (les files
    que hi ha queden declarades, vigents, pendents i datades el dia de creacio), abast
    als registres, perfils a les persones, un nombre als fets quantitatius, el limit de
    RC patronal, els imports executats per any i la taula d'equips. Cap INSERT, UPDATE ni
    DELETE (`as_of` es explicit i neix nul: cap backfill). El downgrade comprova primer
    que no hi hagi res que 0051 no pugui guardar, per valors explicits i sense cap
    comparacio de dates (cap zona horaria), i no reinterpreta ni esborra res.
    """
    up = _offline_sql("upgrade", "0051_tender_analysis:0052_company_qualification")
    down = _offline_sql("downgrade", "0052_company_qualification:0051_tender_analysis")

    tables = ("company_financial_years", "company_projects", "company_insurances",
              "company_registrations", "company_people")  # fmt: skip
    for table in tables:
        assert (
            f"ALTER TABLE {table} ADD COLUMN knowledge_origin VARCHAR(20) "
            "DEFAULT 'declared' NOT NULL"
        ) in up, table
        assert (
            f"ALTER TABLE {table} ADD COLUMN verification_status VARCHAR(20) "
            "DEFAULT 'pending_verification' NOT NULL"
        ) in up, table
        assert (
            f"ALTER TABLE {table} ADD COLUMN lifecycle VARCHAR(11) DEFAULT 'current' NOT NULL"
        ) in up, table
        assert f"ALTER TABLE {table} ADD COLUMN as_of DATE;" in up, table
        assert f"ALTER TABLE {table} DROP COLUMN knowledge_origin;" in down, table
    assert "ALTER TABLE company_registrations ADD COLUMN scope_service_lines JSONB" in up
    assert "ALTER TABLE company_people ADD COLUMN profile_codes JSONB" in up
    assert "ALTER TABLE company_operating_facts ADD COLUMN value_number NUMERIC(18, 4)" in up
    assert (
        "CHECK (value_number IS NULL OR key IN ('ipv4_assignable_count'))" in up
        and "CHECK (value_number IS NULL OR value_number >= 0)" in up
    )
    assert "ALTER TABLE company_insurance_limits ALTER COLUMN kind TYPE VARCHAR(19);" in up
    assert "ALTER TABLE company_insurance_limits ALTER COLUMN kind TYPE VARCHAR(16);" in down
    equipment = up.split("CREATE TABLE company_equipment (")[1].split("\n);")[0]
    for clause in (
        "REFERENCES companies (id) ON DELETE CASCADE",
        "CHECK (calibration_valid_until IS NULL OR calibration_required IS TRUE)",
        "CHECK (quantity >= 1)",
        "CONSTRAINT uq_company_equipment_serial UNIQUE (company_id, serial_reference)",
        "CHECK (valid_until IS NULL OR valid_until >= as_of)",
    ):
        assert clause in equipment, clause
    assert "price" not in equipment and "cost" not in equipment, "un equip no te preu"
    annual = up.split("CREATE TABLE company_project_annual_amounts (")[1].split("\n);")[0]
    for clause in (
        "REFERENCES company_projects (id) ON DELETE CASCADE",
        "CONSTRAINT uq_company_project_annual_amounts_year UNIQUE (project_id, year)",
        "CHECK (amount >= 0)",
    ):
        assert clause in annual, clause
    # Neutralitat: cap fila es toca en pujar.
    statements = up.replace("UPDATE alembic_version", "")
    assert "UPDATE " not in statements
    for forbidden in ("INSERT INTO", "DELETE FROM", "DROP TABLE", "DROP COLUMN"):
        assert forbidden not in statements, forbidden
    # Downgrade: la guarda va abans de cap DDL i no reinterpreta res.
    guard = down.split("DO $$")[1].split("END $$")[0]
    assert down.index("DO $$") < down.index("DROP TABLE company_equipment")
    assert guard.count("RAISE EXCEPTION 'downgrade_0052_incompatible_rows") == len(tables) + 6
    assert "created_at" not in guard and "TIME ZONE" not in guard.upper(), "cap data a la guarda"
    for condition in (
        "FROM company_registrations WHERE scope_service_lines IS NOT NULL",
        "FROM company_people WHERE profile_codes IS NOT NULL",
        "FROM company_operating_facts WHERE value_number IS NOT NULL",
        "FROM company_insurance_limits WHERE kind = 'employer_per_victim'",
        "FROM company_equipment WHERE TRUE",
        "FROM company_project_annual_amounts WHERE TRUE",
        "as_of IS NOT NULL",
        "verification_status <> 'pending_verification'",
    ):
        assert condition in guard, condition
    for forbidden in ("INSERT INTO", "DELETE FROM", "COALESCE"):
        assert forbidden not in down, forbidden
    assert "UPDATE " not in down.replace("UPDATE alembic_version", "")


def test_les_linies_directes_dels_projectes_es_copien_i_el_downgrade_no_inventa_res():
    """0052 -> 0053 -> 0052. L'upgrade crea `company_project_service_lines` (PK projecte +
    linia, FK al projecte en CASCADE i al vocabulari en RESTRICT, sense `company_id`) i hi
    copia, sense duplicats, les linies que 0052 llegia dels serveis vinculats. No toca
    `project_services` (legacy). El downgrade nomes passa si, a cada projecte, les linies
    directes i les dels enllacos legacy existents son el mateix conjunt, i aleshores
    nomes elimina l'estructura de 0053: mai crea, canvia ni esborra cap enllac.
    """
    up = _offline_sql("upgrade", "0052_company_qualification:0053_project_service_lines")
    down = _offline_sql("downgrade", "0053_project_service_lines:0052_company_qualification")

    table = up.split("CREATE TABLE company_project_service_lines (")[1].split("\n);")[0]
    for clause in (
        "CONSTRAINT pk_company_project_service_lines PRIMARY KEY (project_id, service_line_code)",
        "REFERENCES company_projects (id) ON DELETE CASCADE",
        "REFERENCES service_lines (code) ON DELETE RESTRICT",
    ):
        assert clause in table, clause
    assert "company_id" not in table
    backfill = (
        "INSERT INTO company_project_service_lines (project_id, service_line_code, created_at) "
        "SELECT DISTINCT ps.project_id, s.service_line_code"
    )
    assert backfill in up and "WHERE s.service_line_code IS NOT NULL" in up
    statements = up.replace("UPDATE alembic_version", "")
    for forbidden in ("UPDATE ", "DELETE FROM", "DROP ", "project_services ADD", "ALTER TABLE"):
        assert forbidden not in statements, forbidden

    # Downgrade: guarda de conjunts exactes (A - B i B - A) abans del primer DDL, i
    # despres nomes cau l'estructura de 0053. Res no es crea, canvia ni esborra.
    guard = down.split("DO $$")[1].split("END $$")[0]
    assert guard.count("RAISE EXCEPTION 'downgrade_0053_incompatible_rows") == 2
    body = down.split("Running downgrade 0053_project_service_lines")[1]
    assert body.index("DO $$") < body.index("DROP TABLE company_project_service_lines")
    for ddl in ("DROP ", "ALTER TABLE", "CREATE "):
        assert body.split("DO $$")[0].count(ddl) == 0, ddl
    a_minus_b = (
        "FROM company_project_service_lines l WHERE NOT EXISTS (SELECT 1 FROM project_services"
    )
    assert a_minus_b in guard
    assert "FROM project_services ps WHERE EXISTS" in guard
    statements = down.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "company_services ADD"):
        assert forbidden not in statements, forbidden


def test_les_columnes_d_analisi_de_la_decisio_es_poden_aplicar_i_desfer():
    """0053 -> 0054 -> 0053. Dues columnes nul·lables a `workspace_decisions`, la FK
    SET NULL cap als runs d'analisi i el seu index; cap backfill. El downgrade les
    treu en ordre invers (index, FK, columnes) i no toca cap fila.
    """
    up = _offline_sql("upgrade", "0053_project_service_lines:0054_decision_analysis_v3")
    down = _offline_sql("downgrade", "0054_decision_analysis_v3:0053_project_service_lines")

    assert "ALTER TABLE workspace_decisions ADD COLUMN analysis_run_id UUID" in up
    assert "ALTER TABLE workspace_decisions ADD COLUMN analysis_snapshot_json JSONB" in up
    assert "NOT NULL" not in up
    assert (
        "ALTER TABLE workspace_decisions ADD CONSTRAINT fk_workspace_decisions_analysis_run "
        "FOREIGN KEY(analysis_run_id) REFERENCES tender_analysis_runs (id) ON DELETE SET NULL"
    ) in up
    assert (
        "CREATE INDEX ix_workspace_decisions_analysis_run "
        "ON workspace_decisions (analysis_run_id)"
    ) in up
    statements = up.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert forbidden not in statements, forbidden

    order = [
        "DROP INDEX ix_workspace_decisions_analysis_run",
        "DROP CONSTRAINT fk_workspace_decisions_analysis_run",
        "DROP COLUMN analysis_snapshot_json",
        "DROP COLUMN analysis_run_id",
    ]
    positions = [down.index(piece) for piece in order]
    assert positions == sorted(positions)
    statements = down.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert forbidden not in statements, forbidden


def test_0055_afegeix_salut_de_font_provenance_i_cerca():
    """0054 -> 0055. Salut de font, comptadors d'ingesta, provenance canonica, presencia
    a la font, cerca i indexs. Additiva: cap backfill; res a `work_jobs` ni cap taula de
    descart."""
    sql = _offline_sql("upgrade", "0054_decision_analysis_v3:0055_radar_ingestion")
    for fragment in (
        "ALTER TABLE sources ADD COLUMN last_feed_updated_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE sources ADD COLUMN last_sync_started_at",
        "ALTER TABLE sources ADD COLUMN last_sync_finished_at",
        "ALTER TABLE sources ADD COLUMN last_sync_status VARCHAR(20)",
        "ALTER TABLE sources ADD COLUMN last_sync_pages INTEGER DEFAULT '0' NOT NULL",
        "ALTER TABLE sources ADD COLUMN last_sync_entries INTEGER DEFAULT '0' NOT NULL",
        "ALTER TABLE sources ADD COLUMN last_sync_tombstones INTEGER DEFAULT '0' NOT NULL",
        "ALTER TABLE sources ADD COLUMN last_sync_error TEXT",
        "ALTER TABLE sources ADD COLUMN updated_watermark",
        "ALTER TABLE sources ADD COLUMN sync_lease_owner VARCHAR(120)",
        "ALTER TABLE sources ADD COLUMN sync_lease_until",
        "ALTER TABLE ingestion_runs ADD COLUMN pages_processed INTEGER DEFAULT '0' NOT NULL",
        "ALTER TABLE ingestion_runs ADD COLUMN records_superseded",
        "ALTER TABLE ingestion_runs ADD COLUMN records_conflicted",
        "ALTER TABLE ingestion_runs ADD COLUMN tombstones_applied",
        "ALTER TABLE ingestion_runs ADD COLUMN entries_restored",
        "ALTER TABLE tenders ADD COLUMN source_updated_at TIMESTAMP WITH TIME ZONE",
        "ALTER TABLE tenders ADD COLUMN source_removed_at",
        "ALTER TABLE tenders ADD COLUMN source_metadata JSONB",
        "ALTER TABLE tenders ADD COLUMN search_text TEXT",
        "ALTER TABLE tender_documents ADD COLUMN removed_at",
        "CREATE INDEX ix_tender_cpvs_cpv_code ON tender_cpvs (cpv_code)",
        "CREATE INDEX ix_tender_lot_cpvs_cpv_code ON tender_lot_cpvs (cpv_code)",
        "CREATE INDEX ix_tenders_first_seen_at ON tenders (first_seen_at)",
        "CREATE INDEX ix_tenders_source_updated_at ON tenders (source_updated_at)",
    ):
        assert fragment in sql, fragment
    assert "work_jobs" not in sql and "company_tender_dismissals" not in sql
    statements = sql.replace("UPDATE alembic_version", "")
    for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE TABLE"):
        assert forbidden not in statements, forbidden


def test_0055_es_reversible():
    sql = _offline_sql("downgrade", "0055_radar_ingestion:0054_decision_analysis_v3")
    for fragment in (
        "DROP COLUMN search_text",
        "DROP COLUMN source_metadata",
        "DROP COLUMN source_removed_at",
        "DROP COLUMN source_updated_at",
        "DROP COLUMN removed_at",
        "DROP COLUMN sync_lease_until",
        "DROP COLUMN last_sync_status",
        "DROP COLUMN records_conflicted",
        "DROP INDEX ix_tender_cpvs_cpv_code",
        "DROP INDEX ix_tender_lot_cpvs_cpv_code",
        "DROP INDEX ix_tenders_first_seen_at",
        "DROP INDEX ix_tenders_source_updated_at",
    ):
        assert fragment in sql, fragment
    # Els indexs cauen abans que les columnes que indexen.
    assert sql.index("DROP INDEX ix_tenders_source_updated_at") < sql.index(
        "DROP COLUMN source_updated_at"
    )


def _offline_sql_sqlite(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args, "--sql"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": "sqlite+aiosqlite:///:memory:"},
    )
    assert result.returncode == 0, f"alembic ha fallat:\n{result.stderr[-2000:]}"
    return result.stdout


def test_0056_crea_extensio_i_index_trigram_a_postgres():
    sql = _offline_sql("upgrade", "0055_radar_ingestion:0056_radar_search")
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in sql
    assert (
        "CREATE INDEX ix_tenders_search_text_trgm ON tenders USING gin "
        "(search_text gin_trgm_ops)"
    ) in sql
    assert sql.index("CREATE EXTENSION") < sql.index("CREATE INDEX ix_tenders_search_text_trgm")


def test_0056_no_emet_res_a_sqlite():
    up = _offline_sql_sqlite("upgrade", "0055_radar_ingestion:0056_radar_search")
    down = _offline_sql_sqlite("downgrade", "0056_radar_search:0055_radar_ingestion")
    for sql in (up, down):
        statements = sql.replace("alembic_version", "")
        for forbidden in ("pg_trgm", "CREATE INDEX", "DROP INDEX", "CREATE EXTENSION"):
            assert forbidden not in statements, forbidden


def test_0056_es_reversible_i_mai_esborra_l_extensio():
    sql = _offline_sql("downgrade", "0056_radar_search:0055_radar_ingestion")
    assert "DROP INDEX ix_tenders_search_text_trgm" in sql
    assert "DROP EXTENSION" not in sql.upper()
    assert "pg_trgm" not in sql


def test_0063_es_l_unic_cap_i_penja_de_0062():
    """0062 penjava de 0061; 0063 (imports d'una font externa, F-06a) es el nou cap unic."""
    from app.db.revision import SCHEMA_REVISION

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"],
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env={**os.environ, "DATABASE_URL": OFFLINE_URL},
    )
    assert result.returncode == 0, result.stderr[-1000:]
    assert [line.split()[0] for line in result.stdout.splitlines() if line.strip()] == [
        "0063_tender_amount_observations"
    ]
    assert SCHEMA_REVISION == "0063_tender_amount_observations"
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    assert (
        script.get_revision("0063_tender_amount_observations").down_revision
        == "0062_requirement_uncertainties"
    )
    assert (
        script.get_revision("0062_requirement_uncertainties").down_revision
        == "0061_requirement_run_basis"
    )
    assert (
        script.get_revision("0061_requirement_run_basis").down_revision
        == "0060_structure_provenance"
    )
    assert (
        script.get_revision("0060_structure_provenance").down_revision
        == "0059_global_classification"
    )
    assert (
        script.get_revision("0059_global_classification").down_revision
        == "0058_public_documents"
    )
    assert script.get_revision("0058_public_documents").down_revision == "0057_lot_title_text"
    assert script.get_revision("0057_lot_title_text").down_revision == "0056_radar_search"
    assert script.get_revision("0056_radar_search").down_revision == "0055_radar_ingestion"


def _load_0056():
    import importlib.util

    path = API_ROOT / "alembic" / "versions" / "0056_radar_search.py"
    spec = importlib.util.spec_from_file_location("mig_0056_radar_search_unit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeOrig(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"sqlstate {sqlstate}")
        self.sqlstate = sqlstate
        self.pgcode = sqlstate


class _FakeBind:
    """`CREATE EXTENSION` falla amb el SQLSTATE donat; `pg_extension` no la te."""

    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate

    def execute(self, statement):
        from sqlalchemy.exc import ProgrammingError

        text = str(statement)
        if text.startswith("SELECT 1 FROM pg_extension"):
            return type("R", (), {"scalar": staticmethod(lambda: None)})()
        raise ProgrammingError(text, None, _FakeOrig(self.sqlstate))

    def begin_nested(self):
        import contextlib

        return contextlib.nullcontext()


@pytest.mark.parametrize(
    ("sqlstate", "expected"),
    [("42501", RuntimeError), ("58P01", "ProgrammingError"), ("42P01", "ProgrammingError")],
)
def test_0056_nomes_l_error_de_permisos_es_el_missatge_de_prerequisit(
    monkeypatch, sqlstate, expected
):
    """Permisos (42501) -> missatge de prerequisit; qualsevol altre error es propaga tal qual."""
    from sqlalchemy.exc import ProgrammingError

    module = _load_0056()
    context = type("Ctx", (), {"as_sql": False})()
    fake_op = type(
        "Op",
        (),
        {
            "get_context": staticmethod(lambda: context),
            "get_bind": staticmethod(lambda: _FakeBind(sqlstate)),
        },
    )
    monkeypatch.setattr(module, "op", fake_op)
    if expected is RuntimeError:
        with pytest.raises(RuntimeError, match="CREATE EXTENSION pg_trgm") as caught:
            module._ensure_pg_trgm()
        assert isinstance(caught.value.__cause__, ProgrammingError)
    else:
        with pytest.raises(ProgrammingError):
            module._ensure_pg_trgm()


@pytest.mark.postgres
async def test_0056_round_trip_real_a_postgres(engine):
    """Cadena real 0056 -> 0055 -> 0054 i de tornada, sobre l'schema aillat de PG.

    El harness crea l'schema amb `create_all` (estat del cap, sense l'index trigram);
    s'executen les funcions reals `upgrade()`/`downgrade()` de 0056, 0055 i 0054 amb
    `Operations` sobre la connexio. L'extensio no s'esborra mai.
    """
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    versions = API_ROOT / "alembic" / "versions"

    def load(name: str):
        spec = importlib.util.spec_from_file_location(f"mig_{name}", versions / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    m54 = load("0054_decision_analysis_v3")
    m55 = load("0055_radar_ingestion")
    m56 = load("0056_radar_search")

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def index_exists(conn) -> bool:
        res = await conn.execute(
            sa.text(
                "SELECT 1 FROM pg_indexes WHERE schemaname = current_schema() "
                "AND indexname = 'ix_tenders_search_text_trgm'"
            )
        )
        return res.scalar() is not None

    async def has_extension(conn) -> bool:
        res = await conn.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'"))
        return res.scalar() is not None

    async with engine.begin() as conn:
        # Si l'extensio ja existeix en un altre schema, ha de ser visible per als operadors.
        await conn.execute(
            sa.text("SELECT set_config('search_path', current_schema() || ',public', true)")
        )
        assert not await index_exists(conn)
        await conn.run_sync(run, m56.upgrade)
        assert await index_exists(conn)
        assert await has_extension(conn)
        # Idempotencia del guard: l'extensio ja hi es.
        await conn.run_sync(run, m56.downgrade)
        assert not await index_exists(conn)
        assert await has_extension(conn)  # mai s'esborra
        await conn.run_sync(run, m55.downgrade)
        await conn.run_sync(run, m54.downgrade)
        await conn.run_sync(run, m54.upgrade)
        await conn.run_sync(run, m55.upgrade)
        await conn.run_sync(run, m56.upgrade)
        assert await index_exists(conn)
        await conn.run_sync(run, m56.downgrade)


# --------------------------------------------------------------------------- #
# 0057: titols de la font sense limit (hotfix radar, pagina 9 del pilot)       #
# --------------------------------------------------------------------------- #

_TITLES_UP = "0056_radar_search:0057_lot_title_text"
_TITLES_DOWN = "0057_lot_title_text:0056_radar_search"


_FAILED_REVISION_ADD = (
    "ALTER TABLE source_records ADD COLUMN failed_schema_revision VARCHAR(64)"
)
_FAILED_KNOWN_ADD = (
    "ALTER TABLE ingestion_runs ADD COLUMN records_failed_known INTEGER DEFAULT '0' NOT NULL"
)


def test_0057_passa_els_dos_titols_a_text_sense_cap_cast():
    sql = _offline_sql("upgrade", _TITLES_UP)
    assert "ALTER TABLE tender_lots ALTER COLUMN title TYPE TEXT" in sql
    assert "ALTER TABLE tenders ALTER COLUMN title TYPE TEXT" in sql
    body = sql.replace("alembic_version", "")
    # Nomes aquestes dues columnes: cap altra ampliacio.
    assert len(re.findall(r"ALTER TABLE \w+ ALTER COLUMN \w+ TYPE", body)) == 2
    assert "USING" not in body and "DROP" not in body.upper()


def test_0057_afegeix_les_columnes_de_fallades_conegudes():
    """Fix round 1: revisio d'esquema de la fallada i comptador de fallades conegudes."""
    sql = _offline_sql("upgrade", _TITLES_UP)
    assert _FAILED_REVISION_ADD in sql
    assert _FAILED_KNOWN_ADD in sql
    body = sql.replace("alembic_version", "")
    assert len(re.findall(r"ADD COLUMN", body)) == 2
    # Cap backfill: neixen NULL / 0.
    assert "UPDATE source_records" not in body and "UPDATE ingestion_runs" not in body


def test_0057_downgrade_refusa_en_comptes_de_truncar():
    """Tornar a VARCHAR(1000) amb titols mes llargs fallaria o truncaria: es refusa abans."""
    sql = _offline_sql("downgrade", _TITLES_DOWN)
    assert "ALTER TABLE tender_lots ALTER COLUMN title TYPE VARCHAR(1000)" in sql
    assert "ALTER TABLE tenders ALTER COLUMN title TYPE VARCHAR(1000)" in sql
    guard = sql.index("RAISE EXCEPTION")
    assert guard < sql.index("TYPE VARCHAR(1000)")
    # El refus va abans de qualsevol canvi, tambe abans d'esborrar les columnes noves.
    assert guard < sql.index("ALTER TABLE ingestion_runs DROP COLUMN records_failed_known")
    assert "ALTER TABLE source_records DROP COLUMN failed_schema_revision" in sql
    assert "char_length(title) > 1000" in sql
    lowered = sql.lower()
    # Cap cast explicit (`USING title::varchar(1000)` trunca en silenci) ni retall.
    assert "using" not in lowered and "substr" not in lowered and "left(" not in lowered


def test_0057_a_sqlite_nomes_afegeix_i_treu_les_columnes_noves():
    """A SQLite VARCHAR(n) i TEXT son el mateix (sense limit): els titols no es toquen."""
    up = _offline_sql_sqlite("upgrade", _TITLES_UP).replace("alembic_version", "")
    down = _offline_sql_sqlite("downgrade", _TITLES_DOWN).replace("alembic_version", "")
    for sql in (up, down):
        for forbidden in ("ALTER COLUMN", "RAISE", "CREATE TABLE", "COLUMN title"):
            assert forbidden not in sql, forbidden
    assert "ADD COLUMN failed_schema_revision VARCHAR(64)" in up
    assert "ADD COLUMN records_failed_known INTEGER DEFAULT '0' NOT NULL" in up
    assert "DROP COLUMN failed_schema_revision" in down
    assert "DROP COLUMN records_failed_known" in down


def test_les_columnes_de_fallades_conegudes_al_model():
    from app.models.sources import IngestionRun, SourceRecord

    column = SourceRecord.__table__.c.failed_schema_revision
    assert isinstance(column.type, sa.String) and column.type.length == 64
    assert column.nullable is True
    known = IngestionRun.__table__.c.records_failed_known
    assert isinstance(known.type, sa.Integer) and known.nullable is False
    assert known.server_default.arg == "0"


def test_la_revisio_d_esquema_del_codi_es_el_cap_d_alembic():
    """`SCHEMA_REVISION` (desada a les fallades) no pot divergir de `alembic heads`."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from app.db.revision import SCHEMA_REVISION

    script = ScriptDirectory.from_config(Config(str(API_ROOT / "alembic.ini")))
    assert script.get_heads() == [SCHEMA_REVISION]


def test_els_titols_de_la_font_son_text_al_model():
    from app.models.tenders import Tender, TenderLot

    assert isinstance(TenderLot.__table__.c.title.type, sa.Text)
    assert isinstance(Tender.__table__.c.title.type, sa.Text)
    assert Tender.__table__.c.title.nullable is False
    assert TenderLot.__table__.c.title.nullable is True


@pytest.mark.postgres
async def test_0057_round_trip_real_a_postgres(engine):
    """0056 -> 0057 -> 0056 -> 0057 amb les funcions reals, i el downgrade que refusa.

    El harness crea l'schema amb `create_all` (estat del cap = 0057). El primer
    `downgrade()` el porta a l'estat 0056 (VARCHAR(1000)).
    """
    import importlib.util
    import uuid as uuid_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.sources import Source
    from app.models.tenders import Tender, TenderLot

    path = API_ROOT / "alembic" / "versions" / "0057_lot_title_text.py"
    spec = importlib.util.spec_from_file_location("mig_0057_lot_title_text", path)
    m57 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m57)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def types(conn) -> dict[str, tuple[str, int | None]]:
        rows = await conn.execute(
            sa.text(
                "SELECT table_name, data_type, character_maximum_length "
                "FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND column_name = 'title' AND table_name IN ('tenders', 'tender_lots')"
            )
        )
        return {row[0]: (row[1], row[2]) for row in rows}

    async def new_columns(conn) -> set[tuple[str, str]]:
        rows = await conn.execute(
            sa.text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND column_name IN "
                "('failed_schema_revision', 'records_failed_known')"
            )
        )
        return {(row[0], row[1]) for row in rows}

    text_types = {"tenders": ("text", None), "tender_lots": ("text", None)}
    varchar = {"tenders": ("character varying", 1000), "tender_lots": ("character varying", 1000)}
    added = {
        ("source_records", "failed_schema_revision"),
        ("ingestion_runs", "records_failed_known"),
    }

    async with engine.begin() as conn:
        assert await types(conn) == text_types  # cap (create_all)
        assert await new_columns(conn) == added
        await conn.run_sync(run, m57.downgrade)  # -> 0056
        assert await types(conn) == varchar and await new_columns(conn) == set()
        await conn.run_sync(run, m57.upgrade)  # -> 0057
        assert await types(conn) == text_types and await new_columns(conn) == added
        await conn.run_sync(run, m57.downgrade)  # -> 0056
        assert await types(conn) == varchar and await new_columns(conn) == set()
        await conn.run_sync(run, m57.upgrade)  # -> 0057
        assert await types(conn) == text_types and await new_columns(conn) == added

    # Amb un titol de lot de mes de 1.000 caracters, el downgrade refusa i no toca res.
    long_title = "Agujas venosas para fístula, calibre 14G, con conexión luer-lock. " * 16
    assert len(long_title) > 1000
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        source = Source(code=f"t57-{uuid_module.uuid4().hex[:6]}", name="Font 0057")
        session.add(source)
        await session.flush()
        tender = Tender(source_id=source.id, external_id="t57", title="Expedient curt")
        session.add(tender)
        await session.flush()
        session.add(TenderLot(tender_id=tender.id, external_lot_id="1", title=long_title))
        await session.commit()

    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m57.downgrade)
    assert "0057" in str(caught.value) and "1000" in str(caught.value)
    async with engine.begin() as conn:
        assert await types(conn) == text_types
        assert await new_columns(conn) == added  # el refus no ha esborrat res
        stored = (
            await conn.execute(sa.text("SELECT title FROM tender_lots WHERE external_lot_id = '1'"))
        ).scalar_one()
    assert stored == long_title


# --------------------------------------------------------------------------- #
# 0058: documents publics (origen, source ownership, verificacio, alies, gaps) #
# --------------------------------------------------------------------------- #

_PUBDOC_UP = "0057_lot_title_text:0058_public_documents"
_PUBDOC_DOWN = "0058_public_documents:0057_lot_title_text"

_PUBDOC_COLUMNS = (
    "origin",
    "governing_source_id",
    "contribution_status",
    "contributed_by",
    "contributed_at",
    "contribution_source_url",
    "verified_by",
    "verified_at",
    "verification_note",
    "duplicate_of_document_id",
    "retired_by",
    "retired_reason",
)


def test_0058_afegeix_les_dotze_columnes_a_tender_documents_i_la_taula_de_gaps():
    sql = _offline_sql("upgrade", _PUBDOC_UP)
    added = re.findall(r"ALTER TABLE tender_documents ADD COLUMN (\w+)", sql)
    assert tuple(added) == _PUBDOC_COLUMNS
    assert "CREATE TABLE tender_package_gaps" in sql
    assert "tender_document_proposals" not in sql
    # origin neix `feed` i la contribucio `not_applicable`: additiva, sense apagar res
    assert "origin VARCHAR(15) DEFAULT 'feed' NOT NULL" in sql
    assert "contribution_status VARCHAR(20) DEFAULT 'not_applicable' NOT NULL" in sql


def test_0058_fa_el_backfill_abans_dels_check():
    sql = _offline_sql("upgrade", _PUBDOC_UP)
    upload = sql.index("SET origin = 'owner_upload'")
    governing = sql.index("SET governing_source_id")
    first_check = sql.index("ck_tender_documents_")
    assert "url LIKE 'upload://%'" in sql
    assert "FROM tenders" in sql[governing:first_check]
    assert upload < first_check and governing < first_check
    # el backfill del governing nomes toca els `feed`
    assert "origin = 'feed'" in sql[governing:first_check]


def test_0058_crea_els_check_de_coherencia():
    sql = _offline_sql("upgrade", _PUBDOC_UP)
    for name in (
        "ck_tender_documents_origin_valid",
        "ck_tender_documents_contribution_status_valid",
        "ck_tender_documents_contribution_matches_origin",
        "ck_tender_documents_verified_at_matches_status",
        "ck_tender_documents_feed_has_governing_source",
    ):
        assert name in sql, name
    assert "governing_source_id IS NOT NULL" in sql
    assert "REFERENCES sources (id) ON DELETE RESTRICT" in sql
    assert "ON DELETE SET NULL" in sql  # usuaris i alies continuen sent SET NULL


def test_0058_downgrade_refusa_abans_de_cap_drop():
    sql = _offline_sql("downgrade", _PUBDOC_DOWN)
    guard = sql.index("RAISE EXCEPTION")
    assert "contribution_status = 'verified'" in sql
    assert "duplicate_of_document_id IS NOT NULL" in sql
    assert "0058" in sql[guard : guard + 400]
    assert guard < sql.index("DROP TABLE tender_package_gaps")
    assert guard < sql.index("DROP COLUMN origin")
    for column in _PUBDOC_COLUMNS:
        assert f"DROP COLUMN {column}" in sql, column


def test_0058_a_sqlite_nomes_afegeix_i_treu_columnes_sense_check_ni_raise():
    up = _offline_sql_sqlite("upgrade", _PUBDOC_UP).replace("alembic_version", "")
    down = _offline_sql_sqlite("downgrade", _PUBDOC_DOWN).replace("alembic_version", "")
    assert "RAISE" not in up and "RAISE" not in down
    assert "ADD CONSTRAINT" not in up
    added = re.findall(r"ALTER TABLE tender_documents ADD COLUMN (\w+)", up)
    assert tuple(added) == _PUBDOC_COLUMNS
    assert "CREATE TABLE tender_package_gaps" in up
    assert "DROP TABLE tender_package_gaps" in down


def test_els_gaps_i_els_documents_nous_al_model():
    from app.models.package import TenderPackageGap
    from app.models.tenders import TenderDocument

    table = TenderDocument.__table__
    for column in _PUBDOC_COLUMNS:
        assert column in table.c, column
    assert table.c.origin.nullable is False and table.c.contribution_status.nullable is False
    assert table.c.governing_source_id.nullable is True
    gaps = TenderPackageGap.__table__
    assert {c.name for c in gaps.c} >= {
        "tender_id",
        "code",
        "detail",
        "referenced_from_source_ref_id",
        "would_provide",
        "producer",
        "producer_run_id",
        "resolved_by_document_id",
        "resolved_at",
        "created_at",
    }


@pytest.mark.postgres
async def test_0058_round_trip_real_a_postgres(engine):
    """0057 -> 0058 -> 0057 -> 0058 amb les funcions reals, backfill i refusos.

    El harness crea l'schema amb `create_all` (estat del cap = 0058). El primer
    `downgrade()` el porta a 0057; els documents s'hi insereixen amb SQL cru (sense
    les columnes noves) i el segon `upgrade()` les ha de reomplir.
    """
    import importlib.util
    import uuid as uuid_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.sources import Source
    from app.models.tenders import Tender

    path = API_ROOT / "alembic" / "versions" / "0058_public_documents.py"
    spec = importlib.util.spec_from_file_location("mig_0058_public_documents", path)
    m58 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m58)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def columns(conn) -> set[str]:
        rows = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'tender_documents'"
            )
        )
        return {row[0] for row in rows}

    async def gaps_table(conn) -> bool:
        return bool(
            (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = current_schema() "
                        "AND table_name = 'tender_package_gaps'"
                    )
                )
            ).scalar_one()
        )

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        source = Source(code=f"t58-{uuid_module.uuid4().hex[:6]}", name="Font 0058")
        session.add(source)
        await session.flush()
        tender = Tender(source_id=source.id, external_id="t58", title="Expedient 0058")
        session.add(tender)
        await session.commit()
        source_id, tender_id = source.id, tender.id

    async with engine.begin() as conn:
        assert set(_PUBDOC_COLUMNS) <= await columns(conn) and await gaps_table(conn)
        await conn.run_sync(run, m58.downgrade)  # -> 0057
        assert not set(_PUBDOC_COLUMNS) & await columns(conn)
        assert not await gaps_table(conn)
        await conn.run_sync(run, m58.upgrade)  # -> 0058 (buit)
        await conn.run_sync(run, m58.downgrade)  # -> 0057

    feed_id, owner_id = uuid_module.uuid4(), uuid_module.uuid4()
    async with engine.begin() as conn:
        for doc_id, name, url in (
            (feed_id, "pcap.pdf", "https://example.com/pcap.pdf"),
            (owner_id, "pujat.pdf", f"upload://{uuid_module.uuid4()}"),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO tender_documents (id, tender_id, name, document_type, url) "
                    "VALUES (:id, :tender, :name, 'other', :url)"
                ),
                {"id": doc_id, "tender": tender_id, "name": name, "url": url},
            )
        await conn.run_sync(run, m58.upgrade)  # -> 0058 amb dades

    async with engine.begin() as conn:
        rows = {
            row[0]: tuple(row[1:])
            for row in await conn.execute(
                sa.text(
                    "SELECT id, origin, governing_source_id, contribution_status, verified_at "
                    "FROM tender_documents"
                )
            )
        }
    assert rows[feed_id] == ("feed", source_id, "not_applicable", None)
    assert rows[owner_id] == ("owner_upload", None, "not_applicable", None)

    # Els CHECK els imposa la base: un feed sense font governant no entra.
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE tender_documents SET governing_source_id = NULL WHERE id = :id"),
                {"id": feed_id},
            )

    # Amb un document verificat el downgrade refusa i no toca res.
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE tender_documents SET origin = 'operator_url', governing_source_id = NULL, "
                "contribution_status = 'verified', verified_at = now() WHERE id = :id"
            ),
            {"id": owner_id},
        )
    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m58.downgrade)
    assert "0058" in str(caught.value)
    async with engine.begin() as conn:
        assert set(_PUBDOC_COLUMNS) <= await columns(conn) and await gaps_table(conn)
        # Sense verificats pero amb un alies, tambe refusa.
        await conn.execute(
            sa.text(
                "UPDATE tender_documents SET origin = 'owner_upload', "
                "contribution_status = 'not_applicable', verified_at = NULL WHERE id = :id"
            ),
            {"id": owner_id},
        )
        await conn.execute(
            sa.text("UPDATE tender_documents SET duplicate_of_document_id = :a WHERE id = :b"),
            {"a": feed_id, "b": owner_id},
        )
    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m58.downgrade)
    assert "0058" in str(caught.value)

    # Sense verificats ni alies, el downgrade es neta.
    async with engine.begin() as conn:
        await conn.execute(sa.text("UPDATE tender_documents SET duplicate_of_document_id = NULL"))
        await conn.run_sync(run, m58.downgrade)
        assert not set(_PUBDOC_COLUMNS) & await columns(conn)
        await conn.run_sync(run, m58.upgrade)
        assert set(_PUBDOC_COLUMNS) <= await columns(conn) and await gaps_table(conn)


@pytest.mark.postgres
async def test_0058_no_deixa_esborrar_una_font_amb_documents_governats(engine):
    import uuid as uuid_module

    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.sources import Source
    from app.models.tenders import Tender, TenderDocument

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        source = Source(code=f"r58-{uuid_module.uuid4().hex[:6]}", name="Font RESTRICT")
        session.add(source)
        await session.flush()
        tender = Tender(source_id=source.id, external_id="r58", title="T")
        session.add(tender)
        await session.flush()
        session.add(
            TenderDocument(
                tender_id=tender.id, name="a.pdf", url="https://example.com/a.pdf",
                governing_source_id=source.id,
            )
        )
        await session.commit()
        source_id = source.id
    with pytest.raises(IntegrityError):
        async with factory() as session:
            await session.execute(sa.text("DELETE FROM sources WHERE id = :i"), {"i": source_id})
            await session.commit()


# 0058: un sol fitxer vigent per document (3B-2 §2.4).

_CURRENT_FILE_INDEX = (
    "CREATE UNIQUE INDEX uq_tender_document_files_current "
    "ON tender_document_files (tender_document_id) WHERE is_current"
)


def test_0058_deixa_un_sol_vigent_abans_de_crear_l_index_parcial():
    sql = _offline_sql("upgrade", _PUBDOC_UP)
    dedup = sql.index("UPDATE tender_document_files SET is_current = false")
    index = sql.index(_CURRENT_FILE_INDEX)
    assert dedup < index
    # Es queda la versio mes alta de les vigents (la que ja llegeix `current_file_for`).
    assert "newer.version_number > tender_document_files.version_number" in sql[dedup:index]


def test_0058_downgrade_treu_l_index_de_fitxer_vigent_despres_del_refus():
    sql = _offline_sql("downgrade", _PUBDOC_DOWN)
    drop = sql.index("DROP INDEX uq_tender_document_files_current")
    assert sql.index("RAISE EXCEPTION") < drop


def test_0058_a_sqlite_tambe_crea_l_index_de_fitxer_vigent():
    up = _offline_sql_sqlite("upgrade", _PUBDOC_UP)
    down = _offline_sql_sqlite("downgrade", _PUBDOC_DOWN)
    assert (
        "CREATE UNIQUE INDEX uq_tender_document_files_current "
        "ON tender_document_files (tender_document_id) WHERE is_current = 1"
    ) in up
    assert "DROP INDEX uq_tender_document_files_current" in down


def test_l_index_de_fitxer_vigent_es_al_model():
    from app.models.tender_documents import TenderDocumentFile

    indexes = {i.name: i for i in TenderDocumentFile.__table__.indexes}
    index = indexes["uq_tender_document_files_current"]
    assert index.unique is True
    assert [c.name for c in index.columns] == ["tender_document_id"]
    assert str(index.dialect_options["postgresql"]["where"]) == "is_current"
    assert str(index.dialect_options["sqlite"]["where"]) == "is_current = 1"


@pytest.mark.postgres
async def test_0058_index_de_fitxer_vigent_round_trip_real_a_postgres(engine):
    """Dades d'abans amb dos vigents (fallada + bona): l'upgrade en deixa un i crea l'index."""
    import importlib.util
    import uuid as uuid_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.sources import Source
    from app.models.tenders import Tender

    path = API_ROOT / "alembic" / "versions" / "0058_public_documents.py"
    spec = importlib.util.spec_from_file_location("mig_0058_current_file", path)
    m58 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m58)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def has_index(conn) -> bool:
        return bool(
            (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM pg_indexes WHERE schemaname = current_schema() "
                        "AND indexname = 'uq_tender_document_files_current'"
                    )
                )
            ).scalar_one()
        )

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        source = Source(code=f"c58-{uuid_module.uuid4().hex[:6]}", name="Font 0058 vigent")
        session.add(source)
        await session.flush()
        tender = Tender(source_id=source.id, external_id="c58", title="Expedient vigent")
        session.add(tender)
        await session.commit()
        tender_id = tender.id

    doc_id = uuid_module.uuid4()
    file_ids = {version: uuid_module.uuid4() for version in (1, 2, 3)}
    async with engine.begin() as conn:
        assert await has_index(conn)
        await conn.run_sync(run, m58.downgrade)  # -> 0057, sense l'index
        assert not await has_index(conn)
        await conn.execute(
            sa.text(
                "INSERT INTO tender_documents (id, tender_id, name, document_type, url) "
                "VALUES (:id, :tender, 'pcap.pdf', 'other', 'https://example.com/pcap.pdf')"
            ),
            {"id": doc_id, "tender": tender_id},
        )
        # v1 fallada i vigent (no n'hi havia cap de bona), v2 bona i vigent, v3 antiga.
        for version, current, status in (
            (1, True, "failed"),
            (2, True, "downloaded"),
            (3, False, "failed"),
        ):
            await conn.execute(
                sa.text(
                    "INSERT INTO tender_document_files (id, tender_document_id, version_number, "
                    "is_current, download_status, origin, redirect_count) "
                    "VALUES (:id, :doc, :v, :c, :s, 'fetched', 0)"
                ),
                {"id": file_ids[version], "doc": doc_id, "v": version, "c": current, "s": status},
            )
        await conn.run_sync(run, m58.upgrade)  # -> 0058
        assert await has_index(conn)
        rows = dict(
            (
                await conn.execute(
                    sa.text(
                        "SELECT version_number, is_current FROM tender_document_files "
                        "WHERE tender_document_id = :doc"
                    ),
                    {"doc": doc_id},
                )
            ).all()
        )
    assert rows == {1: False, 2: True, 3: False}

    with pytest.raises(IntegrityError):
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE tender_document_files SET is_current = true WHERE id = :id"),
                {"id": file_ids[3]},
            )


# --------------------------------------------------------------------------- #
# 0059: classificacio global, revisions de rol i override ancorat a la versio  #
# --------------------------------------------------------------------------- #

_GLOBAL_UP = "0058_public_documents:0059_global_classification"
_GLOBAL_DOWN = "0059_global_classification:0058_public_documents"
_GLOBAL_TABLES = (
    "global_document_classification_runs",
    "global_document_role_suggestions",
    "global_document_classification_findings",
    "document_role_reviews",
)


def _create_body(sql: str, table: str) -> str:
    return re.search(rf"CREATE TABLE {table} \((.*?)\n\);", sql, re.S).group(1)


def test_0059_crea_les_quatre_taules_globals_sense_company_id():
    sql = _offline_sql("upgrade", _GLOBAL_UP)
    for table in _GLOBAL_TABLES:
        assert f"CREATE TABLE {table}" in sql, table
        assert "company_id" not in _create_body(sql, table), table
    # Una sola vigent per tender i unica per empremta.
    assert (
        "CREATE UNIQUE INDEX uq_global_document_classification_runs_current "
        "ON global_document_classification_runs (tender_id) WHERE is_current"
    ) in sql
    assert "uq_global_document_classification_runs_fingerprint" in sql
    assert "UNIQUE (tender_id, input_fingerprint)" in sql


def test_0059_tots_els_enums_porten_check():
    sql = _offline_sql("upgrade", _GLOBAL_UP)
    for name in (
        "ck_global_document_classification_runs_trigger_valid",
        "ck_global_document_role_suggestions_role_valid",
        "ck_global_document_role_suggestions_confidence_valid",
        "ck_global_document_role_suggestions_document_type_valid",
        "ck_global_document_classification_findings_code_valid",
        "ck_global_document_classification_findings_severity_valid",
        "ck_document_role_reviews_role_valid",
        "ck_document_role_reviews_authority_valid",
        "ck_document_role_reviews_reason_not_empty",
    ):
        assert name in sql, name
    assert "'document_processing', 'document_intake', 'operator_request'" in sql
    assert "'platform_operator', 'promoted_from_company'" in sql


def test_0059_la_revisio_de_rol_es_nomes_d_afegir_i_l_actor_restringeix():
    sql = _offline_sql("upgrade", _GLOBAL_UP)
    assert "CREATE TRIGGER document_role_reviews_append_only" in sql
    assert "BEFORE UPDATE OR DELETE ON document_role_reviews" in sql
    body = _create_body(sql, "document_role_reviews")
    assert "actor_id UUID NOT NULL" in body
    assert "reason VARCHAR(500) NOT NULL" in body
    assert "REFERENCES users (id) ON DELETE RESTRICT" in body
    # Cap SET NULL: el trigger prohibeix qualsevol UPDATE, tambe el d'una FK.
    assert "SET NULL" not in body


def test_0059_backfill_del_sha_abans_del_not_null_i_esborra_les_decisions_sense_fitxer():
    sql = _offline_sql("upgrade", _GLOBAL_UP)
    add = sql.index("ALTER TABLE tender_document_role_decisions ADD COLUMN content_sha256")
    backfill = sql.index("UPDATE tender_document_role_decisions SET content_sha256")
    delete = sql.index("DELETE FROM tender_document_role_decisions WHERE content_sha256 IS NULL")
    not_null = sql.index(
        "ALTER TABLE tender_document_role_decisions ALTER COLUMN content_sha256 SET NOT NULL"
    )
    assert add < backfill < delete < not_null
    assert "tender_document_files.is_current" in sql[backfill:delete]
    assert "RAISE NOTICE" in sql[delete:not_null]


def test_0059_downgrade_refusa_i_treu_el_trigger_abans_de_la_taula():
    sql = _offline_sql("downgrade", _GLOBAL_DOWN)
    guard = sql.index("RAISE EXCEPTION")
    assert "0059" in sql[guard : guard + 300]
    trigger = sql.index("DROP TRIGGER IF EXISTS document_role_reviews_append_only")
    function = sql.index("DROP FUNCTION IF EXISTS document_role_reviews_append_only()")
    table = sql.index("DROP TABLE document_role_reviews")
    assert guard < trigger < function < table
    assert "DROP COLUMN content_sha256" in sql
    for table_name in _GLOBAL_TABLES:
        assert f"DROP TABLE {table_name}" in sql, table_name


def test_0059_a_sqlite_crea_les_taules_sense_trigger_ni_raise():
    up = _offline_sql_sqlite("upgrade", _GLOBAL_UP).replace("alembic_version", "")
    down = _offline_sql_sqlite("downgrade", _GLOBAL_DOWN).replace("alembic_version", "")
    assert "RAISE" not in up and "RAISE" not in down
    assert "TRIGGER" not in up and "TRIGGER" not in down
    for table in _GLOBAL_TABLES:
        assert f"CREATE TABLE {table}" in up, table
        assert f"DROP TABLE {table}" in down, table
    assert "ALTER TABLE tender_document_role_decisions ADD COLUMN content_sha256" in up


def test_0059_afegeix_l_acceptacio_del_paquet_incomplet_a_les_runs_d_analisi():
    """3B-2 §4.4: boolea nullable, sense valor per defecte (les runs existents queden
    a nul), i el downgrade el treu. A PostgreSQL i a SQLite."""
    for compile_sql in (_offline_sql, _offline_sql_sqlite):
        up = compile_sql("upgrade", _GLOBAL_UP)
        down = compile_sql("downgrade", _GLOBAL_DOWN)
        assert (
            "ALTER TABLE tender_analysis_runs ADD COLUMN accepted_incomplete_package BOOLEAN"
        ) in up
        added = next(line for line in up.splitlines() if "accepted_incomplete_package" in line)
        assert "NOT NULL" not in added and "DEFAULT" not in added
        assert "ALTER TABLE tender_analysis_runs DROP COLUMN accepted_incomplete_package" in down
    from app.models.tender_analysis import TenderAnalysisRun

    column = TenderAnalysisRun.__table__.c.accepted_incomplete_package
    assert column.nullable is True and column.server_default is None


def test_0059_al_model():
    from app.models.classification import TenderDocumentRoleDecision
    from app.models.global_classification import DocumentRoleReview
    from app.tenant_ops import PUBLIC_TABLES

    assert set(_GLOBAL_TABLES) <= PUBLIC_TABLES
    assert TenderDocumentRoleDecision.__table__.c.content_sha256.nullable is False
    reviews = DocumentRoleReview.__table__
    assert reviews.c.actor_id.nullable is False and reviews.c.reason.nullable is False
    assert "company_id" not in reviews.c


@pytest.mark.postgres
async def test_0059_round_trip_real_a_postgres_amb_trigger(engine):
    """0058 -> 0059 -> 0058 -> 0059 amb backfill, trigger nomes d'afegir i refus.

    El harness crea l'schema amb `create_all` (cap = 0059). Les dades d'abans
    (una decisio amb fitxer vigent i una sense) s'insereixen a 0058 amb SQL cru.
    """
    import importlib.util
    import uuid as uuid_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.identity import Company, User
    from app.models.sources import Source
    from app.models.tenders import Tender

    path = API_ROOT / "alembic" / "versions" / "0059_global_classification.py"
    spec = importlib.util.spec_from_file_location("mig_0059_global_classification", path)
    m59 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m59)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def tables(conn) -> set[str]:
        rows = await conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()"
            )
        )
        return {row[0] for row in rows} & set(_GLOBAL_TABLES)

    async def has_column(conn, table: str, column: str) -> bool:
        return bool(
            (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = current_schema() "
                        "AND table_name = :table AND column_name = :column"
                    ),
                    {"table": table, "column": column},
                )
            ).scalar_one()
        )

    async def has_sha_column(conn) -> bool:
        # Les dues columnes de 0059 van juntes: el sha de l'override i l'acceptacio
        # del paquet incomplet a les runs d'analisi.
        sha = await has_column(conn, "tender_document_role_decisions", "content_sha256")
        accepted = await has_column(conn, "tender_analysis_runs", "accepted_incomplete_package")
        assert sha == accepted
        return sha

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        source = Source(code=f"t59-{uuid_module.uuid4().hex[:6]}", name="Font 0059")
        company = Company(legal_name="Empresa 0059")
        operator = User(
            email=f"op59-{uuid_module.uuid4().hex[:6]}@tuvi-qa.com",
            hashed_password="x",
            full_name="Operador",
            is_active=True,
        )
        session.add_all([source, company, operator])
        await session.flush()
        tender = Tender(source_id=source.id, external_id="t59", title="Expedient 0059")
        session.add(tender)
        await session.commit()
        source_id, company_id, tender_id = source.id, company.id, tender.id
        operator_id = operator.id

    async with engine.begin() as conn:
        assert await tables(conn) == set(_GLOBAL_TABLES) and await has_sha_column(conn)
        await conn.run_sync(run, m59.downgrade)  # -> 0058
        assert not await tables(conn) and not await has_sha_column(conn)

    with_file, without_file = uuid_module.uuid4(), uuid_module.uuid4()
    content_id = uuid_module.uuid4()
    async with engine.begin() as conn:
        for doc_id, url in ((with_file, "https://e.cat/a.pdf"), (without_file, "https://e.cat/b")):
            await conn.execute(
                sa.text(
                    "INSERT INTO tender_documents (id, tender_id, name, document_type, url, "
                    "origin, governing_source_id) "
                    "VALUES (:id, :tender, 'doc.pdf', 'other', :url, 'feed', :source)"
                ),
                {"id": doc_id, "tender": tender_id, "url": url, "source": source_id},
            )
        await conn.execute(
            sa.text(
                "INSERT INTO tender_document_contents (id, sha256, storage_key, file_size, "
                "mime_type, is_text_extractable) "
                "VALUES (:id, :sha, 'k/a.pdf', 10, 'application/pdf', true)"
            ),
            {"id": content_id, "sha": "c" * 64},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO tender_document_files (id, tender_document_id, content_id, "
                "version_number, is_current, download_status, origin, redirect_count) "
                "VALUES (:id, :doc, :content, 1, true, 'downloaded', 'fetched', 0)"
            ),
            {"id": uuid_module.uuid4(), "doc": with_file, "content": content_id},
        )
        for doc_id in (with_file, without_file):
            await conn.execute(
                sa.text(
                    "INSERT INTO tender_document_role_decisions "
                    "(id, company_id, tender_document_id, role, decided_at) "
                    "VALUES (:id, :company, :doc, 'pcap', now())"
                ),
                {"id": uuid_module.uuid4(), "company": company_id, "doc": doc_id},
            )
        await conn.run_sync(run, m59.upgrade)  # -> 0059 amb dades
        rows = dict(
            (
                await conn.execute(
                    sa.text(
                        "SELECT tender_document_id, content_sha256 "
                        "FROM tender_document_role_decisions"
                    )
                )
            ).all()
        )
    # La decisio amb fitxer queda ancorada al sha vigent; la de sense fitxer, fora.
    assert rows == {with_file: "c" * 64}

    review_id = uuid_module.uuid4()
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO document_role_reviews (id, tender_document_id, content_sha256, "
                "role, authority, actor_id, reason) VALUES (:id, :doc, :sha, "
                "'characteristics_table', 'platform_operator', :actor, 'Es el quadre')"
            ),
            {"id": review_id, "doc": with_file, "sha": "c" * 64, "actor": operator_id},
        )
    # Nomes d'afegir: ni UPDATE ni DELETE directe.
    for statement in (
        "UPDATE document_role_reviews SET role = 'pcap' WHERE id = :id",
        "DELETE FROM document_role_reviews WHERE id = :id",
    ):
        with pytest.raises(DBAPIError) as caught:
            async with engine.begin() as conn:
                await conn.execute(sa.text(statement), {"id": review_id})
        assert "nomes d'afegir" in str(caught.value)
    # L'actor no es pot esborrar mentre hi hagi revisions seves.
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": operator_id})

    # Amb una revisio, el downgrade refusa i no toca res.
    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m59.downgrade)
    assert "0059" in str(caught.value)
    async with engine.begin() as conn:
        assert await tables(conn) == set(_GLOBAL_TABLES)

    # Una cascada (desapareix la licitacio) si que s'emporta la revisio.
    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM tenders WHERE id = :id"), {"id": tender_id})
        remaining = (
            await conn.execute(sa.text("SELECT count(*) FROM document_role_reviews"))
        ).scalar_one()
    assert remaining == 0

    # Sense revisions, el downgrade es net (trigger inclos) i l'upgrade el refa.
    async with engine.begin() as conn:
        await conn.run_sync(run, m59.downgrade)
        assert not await tables(conn) and not await has_sha_column(conn)
        functions = (
            await conn.execute(
                sa.text(
                    "SELECT count(*) FROM pg_proc p "
                    "JOIN pg_namespace n ON n.oid = p.pronamespace "
                    "WHERE n.nspname = current_schema() "
                    "AND p.proname = 'document_role_reviews_append_only'"
                )
            )
        ).scalar_one()
        assert functions == 0
        await conn.run_sync(run, m59.upgrade)
        assert await tables(conn) == set(_GLOBAL_TABLES) and await has_sha_column(conn)


# --------------------------------------------------------------------------- #
# 0061: base del run de requisits, identitat d'analisi i punter per empresa    #
# --------------------------------------------------------------------------- #

_BASIS_UP = "0060_structure_provenance:0061_requirement_run_basis"
_BASIS_DOWN = "0061_requirement_run_basis:0060_structure_provenance"
_POINTER = "company_tender_requirement_runs"


def test_0061_afegeix_la_base_el_backfill_per_proveidor_i_la_degradacio_abans_del_not_null():
    sql = _offline_sql("upgrade", _BASIS_UP)
    add = sql.index("ALTER TABLE requirement_extraction_runs ADD COLUMN run_basis VARCHAR(12)")
    backfill = sql.index("UPDATE requirement_extraction_runs SET run_basis = CASE")
    # M3: es degrada tot vigent que no sigui un COMPLETED real/gold (fake/fixture,
    # `unconfigured`, PARTIAL llegats), abans del NOT NULL i de cap CHECK.
    demote = sql.index(
        "UPDATE requirement_extraction_runs SET is_current = false WHERE is_current "
        "AND (status <> 'completed' OR run_basis NOT IN ('real', 'gold'))"
    )
    not_null = sql.index(
        "ALTER TABLE requirement_extraction_runs ALTER COLUMN run_basis SET NOT NULL"
    )
    check = sql.index("ck_requirement_extraction_runs_run_basis_valid")
    current_check = sql.index("ck_requirement_extraction_runs_current_is_real_or_gold")
    status_check = sql.index("ck_requirement_extraction_runs_status_valid")
    assert add < backfill < demote < not_null < check
    assert demote < current_check and demote < status_check
    # I4: nomes un run real o gold pot ser vigent; M6: estats tancats.
    assert "is_current = false OR run_basis IN ('real', 'gold')" in sql
    assert (
        "status IN ('pending', 'running', 'completed', 'partial', 'failed', "
        "'completed_empty')"
    ) in sql
    case = sql[backfill:demote]
    for fragment in (
        "IN ('fake', 'fixture') THEN 'simulation'",
        "THEN 'unconfigured'",
        "substr(lower(trim(provider)), 1, 5) = 'gold:' THEN 'gold'",
        "ELSE 'real'",
    ):
        assert fragment in case, fragment
    assert "'real', 'simulation', 'unconfigured', 'gold'" in sql


def test_0061_amplia_l_estat_i_afegeix_revisio_i_identitat():
    sql = _offline_sql("upgrade", _BASIS_UP)
    assert (
        "ALTER TABLE requirement_extraction_runs ALTER COLUMN status TYPE VARCHAR(15)"
    ) in sql
    assert (
        "ALTER TABLE requirement_extraction_runs ADD COLUMN needs_review BOOLEAN "
        "DEFAULT false NOT NULL"
    ) in sql
    assert "ALTER TABLE requirement_extraction_runs ADD COLUMN analysis_identity VARCHAR(64)" in sql
    assert "ALTER TABLE analysis_quotes ADD COLUMN analysis_identity VARCHAR(64)" in sql
    # Un `completed_empty` sempre demana revisio.
    assert "ck_requirement_extraction_runs_completed_empty_needs_review" in sql


def test_0061_crea_el_punter_per_empresa_amb_pk_composta():
    sql = _offline_sql("upgrade", _BASIS_UP)
    body = _create_body(sql, _POINTER)
    for column in (
        "company_id UUID NOT NULL",
        "tender_id UUID NOT NULL",
        "run_id UUID NOT NULL",
        "accepted_by UUID",
        "accepted_at TIMESTAMP WITH TIME ZONE",
    ):
        assert column in body, column
    assert "PRIMARY KEY (company_id, tender_id)" in body
    assert "REFERENCES companies (id) ON DELETE CASCADE" in body
    assert "REFERENCES requirement_extraction_runs (id) ON DELETE CASCADE" in body
    assert "REFERENCES users (id) ON DELETE SET NULL" in body


def test_0061_downgrade_refusa_si_hi_ha_completed_empty_abans_de_cap_drop():
    sql = _offline_sql("downgrade", _BASIS_DOWN)
    guard = sql.index("RAISE EXCEPTION")
    assert "completed_empty" in sql[sql.rindex("DO $$", 0, guard) : guard]
    assert "0061" in sql[guard : guard + 300]
    for drop in (
        f"DROP TABLE {_POINTER}",
        "ALTER TABLE requirement_extraction_runs DROP COLUMN run_basis",
        "ALTER TABLE requirement_extraction_runs DROP COLUMN needs_review",
        "ALTER TABLE requirement_extraction_runs DROP COLUMN analysis_identity",
        "ALTER TABLE analysis_quotes DROP COLUMN analysis_identity",
        "ALTER TABLE requirement_extraction_runs ALTER COLUMN status TYPE VARCHAR(9)",
        "DROP CONSTRAINT ck_requirement_extraction_runs_current_is_real_or_gold",
        "DROP CONSTRAINT ck_requirement_extraction_runs_status_valid",
    ):
        assert guard < sql.index(drop), drop


def test_0061_a_sqlite_sense_raise_ni_alter_type():
    up = _offline_sql_sqlite("upgrade", _BASIS_UP).replace("alembic_version", "")
    down = _offline_sql_sqlite("downgrade", _BASIS_DOWN).replace("alembic_version", "")
    assert "RAISE" not in up and "RAISE" not in down
    assert "TYPE VARCHAR" not in up and "TYPE VARCHAR" not in down
    assert "current_is_real_or_gold" not in up and "status_valid" not in up
    assert f"CREATE TABLE {_POINTER}" in up and f"DROP TABLE {_POINTER}" in down
    assert "ADD COLUMN run_basis" in up


def test_0061_al_model():
    from app.models.analysis_quotes import AnalysisQuote
    from app.models.company_runs import CompanyTenderRequirementRun
    from app.models.enums import AnalysisRunStatus
    from app.models.requirements import RequirementExtractionRun
    from app.tenant_ops import PUBLIC_TABLES

    runs = RequirementExtractionRun.__table__
    assert runs.c.run_basis.nullable is False
    assert runs.c.needs_review.nullable is False
    assert runs.c.analysis_identity.nullable is True
    assert AnalysisQuote.__table__.c.analysis_identity.nullable is True
    assert AnalysisRunStatus.COMPLETED_EMPTY.value == "completed_empty"
    pointer = CompanyTenderRequirementRun.__table__
    assert [c.name for c in pointer.primary_key.columns] == ["company_id", "tender_id"]
    assert pointer.c.company_id.nullable is False
    assert _POINTER not in PUBLIC_TABLES

    # I4 + M6: els dos CHECK nomes es creen a PostgreSQL; SQLite no canvia.
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateTable

    pg = str(CreateTable(runs).compile(dialect=postgresql.dialect()))
    lite = str(CreateTable(runs).compile(dialect=sqlite.dialect()))
    for name in ("current_is_real_or_gold", "status_valid"):
        assert f"ck_requirement_extraction_runs_{name}" in pg, name
        assert f"ck_requirement_extraction_runs_{name}" not in lite, name


@pytest.mark.postgres
async def test_0061_round_trip_real_a_postgres(engine):
    """0061 -> 0060 -> 0061 amb backfill i degradacio dels fake vigents, i refus del
    downgrade amb un `completed_empty`. L'esquema el crea el harness (cap = 0061)."""
    import importlib.util
    import uuid as uuid_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.sources import Source
    from app.models.tenders import Tender

    path = API_ROOT / "alembic" / "versions" / "0061_requirement_run_basis.py"
    spec = importlib.util.spec_from_file_location("mig_0061_requirement_run_basis", path)
    m61 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m61)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def has_column(conn, table: str, column: str) -> bool:
        return bool(
            (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema = current_schema() "
                        "AND table_name = :table AND column_name = :column"
                    ),
                    {"table": table, "column": column},
                )
            ).scalar_one()
        )

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        source = Source(code=f"t61-{uuid_module.uuid4().hex[:6]}", name="Font 0061")
        session.add(source)
        await session.flush()
        tenders = [
            Tender(source_id=source.id, external_id=f"t61-{n}", title=f"Expedient 0061 {n}")
            for n in range(5)
        ]
        session.add_all(tenders)
        await session.commit()
        tender_ids = [t.id for t in tenders]

    async with engine.begin() as conn:
        assert await has_column(conn, "requirement_extraction_runs", "run_basis")
        await conn.run_sync(run, m61.downgrade)  # -> 0060
        assert not await has_column(conn, "requirement_extraction_runs", "run_basis")
        assert not await has_column(conn, "analysis_quotes", "analysis_identity")
        assert not await has_column(conn, _POINTER, "run_id")

    # A 0060, tots vigents (un per tender): fake, real, gold, sense proveidor i un
    # PARTIAL real llegat (M3: nomes un COMPLETED real/gold continua vigent).
    rows = {
        "fake": (tender_ids[0], "fake", True, "completed"),
        "anthropic": (tender_ids[1], "anthropic", True, "completed"),
        "gold": (tender_ids[2], "gold", True, "completed"),
        "": (tender_ids[3], "", True, "completed"),
        "partial": (tender_ids[4], "anthropic", True, "partial"),
    }
    run_ids = {}
    async with engine.begin() as conn:
        for key, (tender_id, provider, current, status) in rows.items():
            run_ids[key] = uuid_module.uuid4()
            await conn.execute(
                sa.text(
                    "INSERT INTO requirement_extraction_runs (id, tender_id, status, "
                    "input_fingerprint, extractor_version, prompt_version, schema_version, "
                    "provider, model, started_at, documents_analyzed, pages_analyzed, "
                    "chunks_total, chunks_completed, chunks_failed, requirements_extracted, "
                    "requirements_rejected, award_criteria_extracted, validation_errors, "
                    "evidence_rejected, source_quality_status, is_current) VALUES (:id, "
                    ":tender, :status, :fp, 'v', 'v', 'v', :provider, 'm', now(), "
                    "0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 'unknown', :current)"
                ),
                {
                    "id": run_ids[key],
                    "tender": tender_id,
                    "status": status,
                    "fp": "f" * 64,
                    "provider": provider,
                    "current": current,
                },
            )
        await conn.run_sync(run, m61.upgrade)  # -> 0061 amb dades
        result = {
            row.id: (row.run_basis, row.is_current, row.needs_review)
            for row in await conn.execute(
                sa.text(
                    "SELECT id, run_basis, is_current, needs_review "
                    "FROM requirement_extraction_runs WHERE id = ANY(:ids)"
                ),
                {"ids": list(run_ids.values())},
            )
        }
    assert result == {
        run_ids["fake"]: ("simulation", False, False),
        run_ids["anthropic"]: ("real", True, False),
        run_ids["gold"]: ("gold", True, False),
        run_ids[""]: ("unconfigured", False, False),
        run_ids["partial"]: ("real", False, False),
    }

    # I4: cap run vigent que no sigui real o gold, ni amb SQL cru; M6: estats tancats.
    for statement in (
        "UPDATE requirement_extraction_runs SET is_current = true WHERE id = :id",
        "UPDATE requirement_extraction_runs SET status = 'bogus' WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as conn:
                await conn.execute(sa.text(statement), {"id": run_ids["fake"]})

    # Una base fora de la llista no entra; un `completed_empty` sense revisio tampoc.
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(
                sa.text("UPDATE requirement_extraction_runs SET run_basis = 'x' WHERE id = :id"),
                {"id": run_ids["anthropic"]},
            )
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "UPDATE requirement_extraction_runs SET status = 'completed_empty', "
                    "is_current = false WHERE id = :id"
                ),
                {"id": run_ids["anthropic"]},
            )

    # Amb un `completed_empty`, el downgrade refusa i no toca res.
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "UPDATE requirement_extraction_runs SET status = 'completed_empty', "
                "needs_review = true, is_current = false WHERE id = :id"
            ),
            {"id": run_ids["anthropic"]},
        )
    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m61.downgrade)
    assert "0061" in str(caught.value)
    async with engine.begin() as conn:
        assert await has_column(conn, "requirement_extraction_runs", "run_basis")

    # Sense cap `completed_empty`, el downgrade es net i l'upgrade el refa.
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("DELETE FROM requirement_extraction_runs WHERE id = ANY(:ids)"),
            {"ids": list(run_ids.values())},
        )
        await conn.run_sync(run, m61.downgrade)
        assert not await has_column(conn, "requirement_extraction_runs", "needs_review")
        await conn.run_sync(run, m61.upgrade)
        assert await has_column(conn, _POINTER, "accepted_at")
        assert await has_column(conn, "requirement_extraction_runs", "analysis_identity")


# --------------------------------------------------------------------------- #
# 0061 (Task 8): prerequisits de quota, llibre de despesa i autoritzacions      #
# --------------------------------------------------------------------------- #

_LEDGER = "llm_spend_ledger"
_AUTHORIZATIONS = "llm_budget_authorizations"


def test_0061_afegeix_els_prerequisits_i_el_cost_estimat_a_la_quota():
    sql = _offline_sql("upgrade", _BASIS_UP)
    assert "ALTER TABLE analysis_quotes ADD COLUMN prerequisites JSON" in sql
    assert (
        "ALTER TABLE analysis_quotes ADD COLUMN accept_incomplete_package BOOLEAN "
        "DEFAULT false NOT NULL"
    ) in sql
    assert "ALTER TABLE analysis_quotes ADD COLUMN estimated_cost_usd NUMERIC(12, 6)" in sql


def test_0061_crea_el_llibre_de_despesa_global_que_sobreviu_al_run():
    sql = _offline_sql("upgrade", _BASIS_UP)
    body = _create_body(sql, _LEDGER)
    for column in (
        "run_id UUID,",
        "chunk_index INTEGER NOT NULL",
        "provider VARCHAR(50) NOT NULL",
        "model VARCHAR(120) NOT NULL",
        "input_tokens INTEGER NOT NULL",
        "output_tokens INTEGER NOT NULL",
        # Un run real sempre te preu (porta unica, fix round 1 I2): mai NUL.
        "cost_usd NUMERIC(12, 6) NOT NULL",
        # L'scope del run que l'ha pagat (fix round 1 M3): canari o analisi.
        "budget_scope VARCHAR(20) NOT NULL",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
    ):
        assert column in body, column
    assert "company_id" not in body
    # La despesa no s'esborra amb el run: el run passa a NUL.
    assert "REFERENCES requirement_extraction_runs (id) ON DELETE SET NULL" in body
    assert "CHECK (cost_usd >= 0)" in body
    assert "CHECK (budget_scope IN ('canary', 'run'))" in body
    assert "CHECK (input_tokens >= 0 AND output_tokens >= 0)" in body
    assert "UNIQUE (run_id, chunk_index)" in body


def test_0061_crea_les_autoritzacions_amb_scope_tancat():
    sql = _offline_sql("upgrade", _BASIS_UP)
    body = _create_body(sql, _AUTHORIZATIONS)
    for column in (
        "scope VARCHAR(20) NOT NULL",
        "cap_usd NUMERIC(12, 6) NOT NULL",
        "authorized_by UUID",
        "note VARCHAR(500) NOT NULL",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
    ):
        assert column in body, column
    assert "company_id" not in body
    assert "CHECK (scope IN ('canary', 'run', 'pilot_total'))" in body
    assert "CHECK (cap_usd > 0)" in body
    assert "REFERENCES users (id) ON DELETE SET NULL" in body


def test_0061_downgrade_refusa_amb_despesa_al_llibre_abans_de_cap_drop():
    sql = _offline_sql("downgrade", _BASIS_DOWN)
    guard = sql.index(f"SELECT 1 FROM {_LEDGER}")
    raise_ = sql.index("RAISE EXCEPTION", guard)
    for drop in (
        f"DROP TABLE {_AUTHORIZATIONS}",
        f"DROP TABLE {_LEDGER}",
        "ALTER TABLE analysis_quotes DROP COLUMN prerequisites",
        "ALTER TABLE analysis_quotes DROP COLUMN accept_incomplete_package",
        "ALTER TABLE analysis_quotes DROP COLUMN estimated_cost_usd",
        f"DROP TABLE {_POINTER}",
    ):
        assert raise_ < sql.index(drop), drop
    # En ordre invers: primer el que 0061 crea l'ultim.
    assert sql.index(f"DROP TABLE {_AUTHORIZATIONS}") < sql.index(f"DROP TABLE {_LEDGER}")
    assert sql.index(f"DROP TABLE {_LEDGER}") < sql.index(f"DROP TABLE {_POINTER}")


def test_0061_a_sqlite_tambe_crea_el_llibre_i_les_autoritzacions():
    up = _offline_sql_sqlite("upgrade", _BASIS_UP).replace("alembic_version", "")
    down = _offline_sql_sqlite("downgrade", _BASIS_DOWN).replace("alembic_version", "")
    assert "RAISE" not in up and "RAISE" not in down
    for table in (_LEDGER, _AUTHORIZATIONS):
        assert f"CREATE TABLE {table}" in up and f"DROP TABLE {table}" in down


def test_0061_llibre_i_autoritzacions_al_model():
    from app.models.analysis_quotes import AnalysisQuote
    from app.models.llm_budget import LlmBudgetAuthorization, LlmSpendLedger
    from app.tenant_ops import PUBLIC_TABLES

    quotes = AnalysisQuote.__table__
    assert quotes.c.prerequisites.nullable is True
    assert quotes.c.accept_incomplete_package.nullable is False
    assert quotes.c.estimated_cost_usd.nullable is True
    ledger = LlmSpendLedger.__table__
    assert ledger.c.run_id.nullable is True
    assert ledger.c.cost_usd.nullable is False
    assert ledger.c.budget_scope.nullable is False
    assert "company_id" not in ledger.c and "company_id" not in LlmBudgetAuthorization.__table__.c
    assert {_LEDGER, _AUTHORIZATIONS} <= PUBLIC_TABLES


@pytest.mark.postgres
async def test_0061_round_trip_real_a_postgres_amb_el_llibre(engine):
    """0061 -> 0060 -> 0061: el downgrade refusa amb despesa al llibre i no toca res;
    sense despesa, desfa llibre, autoritzacions i columnes de la quota."""
    import importlib.util
    import uuid as uuid_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError

    path = API_ROOT / "alembic" / "versions" / "0061_requirement_run_basis.py"
    spec = importlib.util.spec_from_file_location("mig_0061_ledger", path)
    m61 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m61)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def tables(conn) -> set[str]:
        rows = await conn.execute(
            sa.text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ANY(:names)"
            ),
            {"names": [_LEDGER, _AUTHORIZATIONS]},
        )
        return {row[0] for row in rows}

    async def quote_columns(conn) -> set[str]:
        rows = await conn.execute(
            sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'analysis_quotes' "
                "AND column_name = ANY(:names)"
            ),
            {"names": ["prerequisites", "accept_incomplete_package", "estimated_cost_usd"]},
        )
        return {row[0] for row in rows}

    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO llm_spend_ledger (id, chunk_index, provider, model, input_tokens, "
                "output_tokens, cost_usd, budget_scope) "
                "VALUES (:id, 0, 'anthropic', 'm', 10, 5, 0.000105, 'run')"
            ),
            {"id": uuid_module.uuid4()},
        )
        await conn.execute(
            sa.text(
                "INSERT INTO llm_budget_authorizations (id, scope, cap_usd, note) "
                "VALUES (:id, 'pilot_total', 12.5, 'ampliacio')"
            ),
            {"id": uuid_module.uuid4()},
        )

    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m61.downgrade)
    assert "0061" in str(caught.value)
    async with engine.begin() as conn:
        assert await tables(conn) == {_LEDGER, _AUTHORIZATIONS}
        cost = await conn.scalar(sa.text("SELECT cost_usd FROM llm_spend_ledger"))
        assert str(cost) == "0.000105"

    async with engine.begin() as conn:
        await conn.execute(sa.text("DELETE FROM llm_spend_ledger"))
        await conn.run_sync(run, m61.downgrade)
        assert await tables(conn) == set()
        assert await quote_columns(conn) == set()
        await conn.run_sync(run, m61.upgrade)
        assert await tables(conn) == {_LEDGER, _AUTHORIZATIONS}
        assert await quote_columns(conn) == {
            "prerequisites", "accept_incomplete_package", "estimated_cost_usd"
        }


# --------------------------------------------------------------------------- #
# 0062: incerteses materials de l'extraccio de requisits (product-v2)         #
# --------------------------------------------------------------------------- #

_UNCERTAINTIES = "requirement_uncertainties"
_UNCERTAINTY_UP = "0061_requirement_run_basis:0062_requirement_uncertainties"
_UNCERTAINTY_DOWN = "0062_requirement_uncertainties:0061_requirement_run_basis"


def test_0062_crea_la_taula_global_amb_les_restriccions():
    sql = _offline_sql("upgrade", _UNCERTAINTY_UP)
    create = sql[sql.index(f"CREATE TABLE {_UNCERTAINTIES}"):]
    create = create[: create.index(";")]
    assert "company_id" not in create  # global: cap tenant
    for fragment in (
        "run_id UUID NOT NULL",
        "page_number INTEGER NOT NULL",
        "quote TEXT",
        "dedup_key VARCHAR(64) NOT NULL",
        "UNIQUE (run_id, dedup_key)",
        "CHECK (page_number >= 1)",
        "(quote_status = 'literal') = (quote IS NOT NULL)",
        "(quote_status = 'literal') = (page_id IS NOT NULL)",
        "(quote_status = 'literal') = (char_start IS NOT NULL AND char_end IS NOT NULL)",
        "scope IN ('admissibility', 'solvency', 'contract_obligation', 'scoring', 'sizing', "
        "'cost', 'submission_documentation', 'deadline', 'liability')",
        "quote_status IN ('literal', 'not_provided', 'rejected')",
        "REFERENCES requirement_extraction_runs (id) ON DELETE CASCADE",
        "REFERENCES tender_document_extractions (id) ON DELETE RESTRICT",
        "REFERENCES tender_document_pages (id) ON DELETE RESTRICT",
        "REFERENCES tender_documents (id) ON DELETE SET NULL",
        "diagnosis VARCHAR(80)",
        "(quote_status <> 'rejected' OR diagnosis IS NOT NULL) "
        "AND (quote_status <> 'literal' OR diagnosis IS NULL)",
        "char_start IS NULL OR (char_start >= 0 AND char_end > char_start)",
        "length(trim(note)) > 0",
        # Els mateixos noms de FK que el model (una base migrada = una de creada).
        "CONSTRAINT fk_requirement_uncertainties_run_id FOREIGN KEY",
        "CONSTRAINT fk_requirement_uncertainties_tender_id FOREIGN KEY",
        "CONSTRAINT fk_requirement_uncertainties_extraction_id FOREIGN KEY",
        "CONSTRAINT fk_requirement_uncertainties_tender_document_id FOREIGN KEY",
        "CONSTRAINT fk_requirement_uncertainties_page_id FOREIGN KEY",
    ):
        assert fragment in create, fragment
    # Additiva: cap altra taula no canvia.
    assert "ALTER TABLE" not in sql.replace("alembic_version", "")


def test_0062_downgrade_refusa_si_hi_ha_incerteses_abans_del_drop():
    sql = _offline_sql("downgrade", _UNCERTAINTY_DOWN)
    guard = sql.index("RAISE EXCEPTION")
    assert f"SELECT 1 FROM {_UNCERTAINTIES}" in sql[sql.rindex("DO $$", 0, guard) : guard]
    assert "0062" in sql[guard : guard + 200]
    assert guard < sql.index(f"DROP TABLE {_UNCERTAINTIES}")


def test_0062_a_sqlite_sense_raise():
    up = _offline_sql_sqlite("upgrade", _UNCERTAINTY_UP).replace("alembic_version", "")
    down = _offline_sql_sqlite("downgrade", _UNCERTAINTY_DOWN).replace("alembic_version", "")
    assert "RAISE" not in up and "RAISE" not in down
    assert f"CREATE TABLE {_UNCERTAINTIES}" in up and f"DROP TABLE {_UNCERTAINTIES}" in down


def test_0062_al_model():
    from app.models.requirements import RequirementUncertainty
    from app.tenant_ops import PUBLIC_TABLES

    table = RequirementUncertainty.__table__
    assert "company_id" not in table.c
    assert table.c.quote.nullable is True and table.c.page_id.nullable is True
    assert table.c.note.nullable is False and table.c.scope.nullable is False
    # Global, com el run de requisits: mai a les taules d'un tenant.
    assert _UNCERTAINTIES in PUBLIC_TABLES and "requirement_extraction_runs" in PUBLIC_TABLES
    # Noms de FK explicits i identics als de la migracio (cap nom derivat de la convencio).
    assert {c.name for c in table.foreign_key_constraints} == {
        f"fk_{_UNCERTAINTIES}_{column}"
        for column in ("run_id", "tender_id", "extraction_id", "tender_document_id", "page_id")
    }


@pytest.mark.postgres
async def test_0062_round_trip_real_a_postgres(engine):
    """0062 -> 0061 -> 0062 sense dades; amb una incertesa, el downgrade refusa i no toca
    res; les CHECK refusen una cita sense estat literal i un ambit fora del vocabulari."""
    import importlib.util
    import uuid as uuid_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError

    path = API_ROOT / "alembic" / "versions" / "0062_requirement_uncertainties.py"
    spec = importlib.util.spec_from_file_location("mig_0062_requirement_uncertainties", path)
    m62 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m62)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    async def has_table(conn) -> bool:
        return bool(
            (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = current_schema() AND table_name = :t"
                    ),
                    {"t": _UNCERTAINTIES},
                )
            ).scalar_one()
        )

    async def columns(conn) -> list[tuple]:
        rows = await conn.execute(
            sa.text(
                "SELECT column_name, data_type, is_nullable, character_maximum_length "
                "FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = :t ORDER BY column_name"
            ),
            {"t": _UNCERTAINTIES},
        )
        return [tuple(r) for r in rows]

    async def constraints(conn) -> list[tuple]:
        """Nom, tipus i definicio de cada restriccio, i cada index: tot el que una
        migracio futura pot referenciar pel nom."""
        rows = await conn.execute(
            sa.text(
                "SELECT conname, contype::text, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = current_schema() AND t.relname = :t ORDER BY conname"
            ),
            {"t": _UNCERTAINTIES},
        )
        indexes = await conn.execute(
            sa.text(
                "SELECT indexname, 'index', indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = :t ORDER BY indexname"
            ),
            {"t": _UNCERTAINTIES},
        )
        return [tuple(r) for r in rows] + [tuple(r) for r in indexes]

    # L'esquema el crea el harness amb els models: el comparem amb el de la migracio.
    async with engine.begin() as conn:
        from_models = (await columns(conn), await constraints(conn))
        await conn.run_sync(run, m62.downgrade)  # -> 0061, sense dades
        assert not await has_table(conn)
        await conn.run_sync(run, m62.upgrade)  # -> 0062
        from_migration = (await columns(conn), await constraints(conn))
        await conn.run_sync(run, m62.downgrade)  # -> 0061 de nou
        await conn.run_sync(run, m62.upgrade)  # -> 0062 de nou
        assert (await columns(conn), await constraints(conn)) == from_migration
    # Columnes, restriccions (noms i definicions, FK incloses) i index: identics.
    assert from_migration == from_models
    assert {
        "ck_requirement_uncertainties_scope_valid",
        "ck_requirement_uncertainties_quote_if_literal",
        "ck_requirement_uncertainties_quote_status_valid",
        "ck_requirement_uncertainties_diagnosis_consistent",
        "ck_requirement_uncertainties_offsets_valid",
        "ck_requirement_uncertainties_note_not_blank",
        "uq_requirement_uncertainties_run_key",
        "fk_requirement_uncertainties_run_id",
        "fk_requirement_uncertainties_page_id",
    } <= {name for name, *_ in from_migration[1]}

    # Una incertesa real (sense cita) sobre un run. El context, amb l'ORM.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.enums import AnalysisRunStatus, ExtractionStatus, RequirementRunBasis
    from app.models.requirements import RequirementExtractionRun
    from app.models.sources import Source
    from app.models.tender_documents import TenderDocumentContent, TenderDocumentExtraction
    from app.models.tenders import Tender

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        source = Source(code=f"t62-{uuid_module.uuid4().hex[:6]}", name="Font 0062")
        session.add(source)
        await session.flush()
        tender = Tender(source_id=source.id, external_id=f"t62-{uuid_module.uuid4().hex[:6]}",
                        title="Expedient 0062")
        content = TenderDocumentContent(
            sha256=uuid_module.uuid4().hex * 2, storage_key="k", file_size=1,
            mime_type="application/pdf", is_text_extractable=True,
        )
        session.add_all([tender, content])
        await session.flush()
        extraction = TenderDocumentExtraction(
            content_id=content.id, parser_version="pypdf-v1", revision_number=1,
            status=ExtractionStatus.EXTRACTED, page_count=1, extracted_page_count=1,
            empty_page_count=0, text_char_count=1, ocr_candidate=False, is_current=True,
        )
        run_row = RequirementExtractionRun(
            tender_id=tender.id, status=AnalysisRunStatus.COMPLETED, input_fingerprint="f" * 64,
            extractor_version="v", prompt_version="v", schema_version="v", provider="fake",
            model="m", run_basis=RequirementRunBasis.SIMULATION, is_current=False,
        )
        session.add_all([extraction, run_row])
        await session.commit()
        tender_id, run_id, extraction_id = tender.id, run_row.id, extraction.id

    async with engine.begin() as conn:
        insert = (
            "INSERT INTO requirement_uncertainties (id, run_id, tender_id, source_chunk_index, "
            "scope, note, extraction_id, page_number, quote, quote_status, diagnosis, "
            "dedup_key, created_at) VALUES (gen_random_uuid(), :run, :tender, 0, :scope, :note, "
            ":extraction, 5, :quote, :status, :diagnosis, :key, now())"
        )
        params = {"run": run_id, "tender": tender_id, "extraction": extraction_id}
        ok = {"scope": "solvency", "note": "nota", "quote": None, "status": "not_provided",
              "diagnosis": None}
        await conn.execute(sa.text(insert), {**params, **ok, "key": "a" * 64})

    # Les CHECK: cita sense estat literal, estat literal sense cita, ambit desconegut,
    # cita rebutjada sense diagnostic, nota buida.
    for bad in (
        {"quote": "text"},
        {"status": "literal"},
        {"scope": "altres"},
        {"status": "rejected"},
        {"note": "   "},
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as conn:
                await conn.execute(sa.text(insert), {**params, **ok, **bad, "key": "b" * 64})
    # La unicitat (run, clau).
    with pytest.raises(DBAPIError):
        async with engine.begin() as conn:
            await conn.execute(sa.text(insert), {**params, **ok, "scope": "cost", "key": "a" * 64})

    # Amb dades, el downgrade refusa i no toca res.
    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m62.downgrade)
    assert "0062" in str(caught.value)
    async with engine.begin() as conn:
        assert await has_table(conn)
        count = sa.text(f"SELECT count(*) FROM {_UNCERTAINTIES}")
        assert (await conn.execute(count)).scalar_one() == 1
        # El run s'esborra en cascada amb les seves incerteses; llavors el downgrade passa.
        await conn.execute(sa.text("DELETE FROM requirement_extraction_runs WHERE id = :id"),
                           {"id": run_id})
        assert (await conn.execute(count)).scalar_one() == 0
        await conn.run_sync(run, m62.downgrade)
        assert not await has_table(conn)
        await conn.run_sync(run, m62.upgrade)
        assert await has_table(conn)



# --------------------------------------------------------------------------- #
# 0063: imports d'un expedient segons una font externa acreditada (F-06a)      #
# --------------------------------------------------------------------------- #

_AMOUNTS = "tender_amount_observations"
_AMOUNTS_UP = "0062_requirement_uncertainties:0063_tender_amount_observations"
_AMOUNTS_DOWN = "0063_tender_amount_observations:0062_requirement_uncertainties"
_AMOUNTS_OPT_IN = "drop_amount_observations=yes"


def test_0063_crea_la_taula_global_amb_les_restriccions():
    sql = _offline_sql("upgrade", _AMOUNTS_UP)
    create = sql[sql.index(f"CREATE TABLE {_AMOUNTS}"):]
    create = create[: create.index(";")]
    assert "company_id" not in create  # global: cap tenant
    for fragment in (
        "tender_id UUID NOT NULL",
        "tender_lot_id UUID,",
        "amount_kind VARCHAR(15) NOT NULL",
        "amount NUMERIC(18, 2),",
        "currency VARCHAR(3) DEFAULT 'EUR' NOT NULL",
        "source_code VARCHAR(60) NOT NULL",
        "source_entity_key VARCHAR(200) NOT NULL",
        "source_row_id VARCHAR(200) NOT NULL",
        "source_phase VARCHAR(80),",
        "source_dataset_updated_at TIMESTAMP WITH TIME ZONE,",
        "retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL",
        "evidence_url TEXT NOT NULL",
        "evidence_sha256 VARCHAR(64) NOT NULL",
        "raw_storage_key VARCHAR(512),",
        "validation_status VARCHAR(10) NOT NULL",
        "validation JSONB DEFAULT '{}' NOT NULL",
        "provenance JSONB NOT NULL",
        "valid_from TIMESTAMP WITH TIME ZONE NOT NULL",
        "valid_to TIMESTAMP WITH TIME ZONE,",
        "created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL",
        "amount_kind IN ('pbl_with_tax', 'pbl_without_tax', 'estimated_value')",
        "validation_status IN ('accepted', 'conflict', 'no_value', 'superseded')",
        "CHECK (amount IS NULL OR amount >= 0)",
        "CHECK (validation_status <> 'accepted' OR (amount IS NOT NULL AND amount > 0))",
        "CHECK (validation_status <> 'no_value' OR amount IS NULL OR amount = 0)",
        "CHECK (length(evidence_url) <= 1024)",
        "CHECK (length(currency) = 3)",
        "CHECK (valid_to IS NULL OR valid_to >= valid_from)",
        "CHECK (length(evidence_sha256) = 64)",
        "CONSTRAINT fk_tender_amount_observations_tender_id FOREIGN KEY",
        "CONSTRAINT fk_tender_amount_observations_tender_lot_id FOREIGN KEY",
        "REFERENCES tenders (id) ON DELETE CASCADE",
        "REFERENCES tender_lots (id) ON DELETE CASCADE",
    ):
        assert fragment in create, fragment
    assert (
        f"CREATE UNIQUE INDEX uq_{_AMOUNTS}_current ON {_AMOUNTS} (tender_id, "
        "coalesce(tender_lot_id, '00000000-0000-0000-0000-000000000000'), amount_kind, "
        "source_code) WHERE valid_to IS NULL AND validation_status = 'accepted'"
    ) in sql
    # Una sola vigent (de qualsevol estat) per fila de la font i objectiu.
    assert (
        f"CREATE UNIQUE INDEX uq_{_AMOUNTS}_current_row ON {_AMOUNTS} (tender_id, "
        "coalesce(tender_lot_id, '00000000-0000-0000-0000-000000000000'), amount_kind, "
        "source_code, source_row_id) WHERE valid_to IS NULL"
    ) in sql
    assert f"CREATE INDEX ix_{_AMOUNTS}_tender_id ON {_AMOUNTS} (tender_id)" in sql
    # Nomes d'afegir: el trigger refusa UPDATE (excepte tancar) i DELETE directe.
    assert "BEFORE UPDATE OR DELETE ON tender_amount_observations" in sql
    assert "pg_trigger_depth() < 2" in sql
    assert "(to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to')" in sql
    # Additiva: cap altra taula no canvia (ni `tenders.base_budget`).
    assert "ALTER TABLE" not in sql.replace("alembic_version", "")
    assert "UPDATE tenders" not in sql and "INSERT INTO tenders" not in sql


def test_0063_downgrade_refusa_si_hi_ha_observacions_abans_del_drop():
    sql = _offline_sql("downgrade", _AMOUNTS_DOWN)
    guard = sql.index("RAISE EXCEPTION '0063")
    assert f"SELECT 1 FROM {_AMOUNTS}" in sql[sql.rindex("DO $$", 0, guard) : guard]
    assert "drop_amount_observations=yes" in sql[guard : guard + 300]
    assert guard < sql.index("DROP TRIGGER IF EXISTS tender_amount_observations_append_only")
    assert guard < sql.index(f"DROP TABLE {_AMOUNTS}")
    assert guard < sql.index(f"DROP INDEX uq_{_AMOUNTS}_current_row")
    assert "DROP FUNCTION IF EXISTS tender_amount_observations_append_only()" in sql


def test_0063_downgrade_amb_la_marca_explicita_no_refusa():
    sql = _offline_sql("-x", _AMOUNTS_OPT_IN, "downgrade", _AMOUNTS_DOWN)
    assert "RAISE EXCEPTION '0063" not in sql
    assert f"DROP TABLE {_AMOUNTS}" in sql
    # Qualsevol altre valor no es la marca.
    sql = _offline_sql("-x", "drop_amount_observations=1", "downgrade", _AMOUNTS_DOWN)
    assert "RAISE EXCEPTION '0063" in sql


def test_0063_a_sqlite_sense_raise_ni_trigger():
    up = _offline_sql_sqlite("upgrade", _AMOUNTS_UP).replace("alembic_version", "")
    down = _offline_sql_sqlite("downgrade", _AMOUNTS_DOWN).replace("alembic_version", "")
    assert "RAISE" not in up and "RAISE" not in down
    assert "TRIGGER" not in up and "TRIGGER" not in down
    assert f"CREATE TABLE {_AMOUNTS}" in up and f"DROP TABLE {_AMOUNTS}" in down


def test_0063_al_model():
    from app.models.tender_amounts import (
        APPEND_ONLY_FUNCTION,
        APPEND_ONLY_TRIGGER,
        TenderAmountObservation,
    )
    from app.tenant_ops import PUBLIC_TABLES

    table = TenderAmountObservation.__table__
    assert "company_id" not in table.c
    assert _AMOUNTS in PUBLIC_TABLES
    assert {c.name for c in table.foreign_key_constraints} == {
        f"fk_{_AMOUNTS}_tender_id",
        f"fk_{_AMOUNTS}_tender_lot_id",
    }
    # El trigger del model (create_all) i el de la migracio son el mateix text.
    path = API_ROOT / "alembic" / "versions" / "0063_tender_amount_observations.py"
    source = path.read_text(encoding="utf-8")
    assert APPEND_ONLY_FUNCTION in source
    assert APPEND_ONLY_TRIGGER in source


@pytest.mark.postgres
async def test_0063_round_trip_real_a_postgres(engine):
    """0063 -> 0062 -> 0063 sense dades, esquema identic al dels models (trigger i funcio
    inclosos); amb una observacio, el downgrade refusa i no toca res; amb la marca
    explicita, baixa; i torna a pujar igual."""
    import importlib.util
    import uuid as uuid_module

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy.exc import DBAPIError

    path = API_ROOT / "alembic" / "versions" / "0063_tender_amount_observations.py"
    spec = importlib.util.spec_from_file_location("mig_0063_tender_amount_observations", path)
    m63 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m63)

    def run(connection, step):
        with Operations.context(MigrationContext.configure(connection)):
            step()

    def drop_anyway() -> None:
        m63.apply_downgrade(drop_rows=True)

    async def has_table(conn) -> bool:
        return bool(
            (
                await conn.execute(
                    sa.text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = current_schema() AND table_name = :t"
                    ),
                    {"t": _AMOUNTS},
                )
            ).scalar_one()
        )

    async def functions(conn) -> list[tuple]:
        rows = await conn.execute(
            sa.text(
                "SELECT p.proname, p.prosrc FROM pg_proc p "
                "JOIN pg_namespace n ON n.oid = p.pronamespace "
                "WHERE n.nspname = current_schema() "
                "AND p.proname = 'tender_amount_observations_append_only'"
            )
        )
        return [tuple(r) for r in rows]

    async def schema(conn) -> tuple:
        """Columnes, restriccions, index, trigger i funcio: tot el que la migracio crea."""
        columns = await conn.execute(
            sa.text(
                "SELECT column_name, data_type, is_nullable, character_maximum_length, "
                "numeric_precision, numeric_scale, column_default "
                "FROM information_schema.columns WHERE table_schema = current_schema() "
                "AND table_name = :t ORDER BY column_name"
            ),
            {"t": _AMOUNTS},
        )
        constraints = await conn.execute(
            sa.text(
                "SELECT conname, contype::text, pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = current_schema() AND t.relname = :t ORDER BY conname"
            ),
            {"t": _AMOUNTS},
        )
        indexes = await conn.execute(
            sa.text(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() AND tablename = :t ORDER BY indexname"
            ),
            {"t": _AMOUNTS},
        )
        triggers = await conn.execute(
            sa.text(
                "SELECT tg.tgname, pg_get_triggerdef(tg.oid) FROM pg_trigger tg "
                "JOIN pg_class t ON t.oid = tg.tgrelid "
                "JOIN pg_namespace n ON n.oid = t.relnamespace "
                "WHERE n.nspname = current_schema() AND t.relname = :t "
                "AND NOT tg.tgisinternal ORDER BY tg.tgname"
            ),
            {"t": _AMOUNTS},
        )
        return (
            [tuple(r) for r in columns],
            [tuple(r) for r in constraints],
            [tuple(r) for r in indexes],
            [tuple(r) for r in triggers],
            await functions(conn),
        )

    # L'esquema el crea el harness amb els models: el comparem amb el de la migracio.
    async with engine.begin() as conn:
        from_models = await schema(conn)
        await conn.run_sync(run, m63.downgrade)  # -> 0062, sense dades
        assert not await has_table(conn)
        assert await functions(conn) == []
        await conn.run_sync(run, m63.upgrade)  # -> 0063
        from_migration = await schema(conn)
        await conn.run_sync(run, m63.downgrade)  # -> 0062 de nou
        await conn.run_sync(run, m63.upgrade)  # -> 0063 de nou
        assert await schema(conn) == from_migration
    # Columnes, restriccions, index, trigger i funcio: identics.
    assert from_migration == from_models
    assert {
        f"ck_{_AMOUNTS}_amount_kind_valid",
        f"ck_{_AMOUNTS}_validation_status_valid",
        f"ck_{_AMOUNTS}_amount_non_negative",
        f"ck_{_AMOUNTS}_accepted_amount_positive",
        f"ck_{_AMOUNTS}_no_value_amount",
        f"ck_{_AMOUNTS}_evidence_url_length",
        f"ck_{_AMOUNTS}_validity_ordered",
        f"ck_{_AMOUNTS}_evidence_sha256_length",
        f"ck_{_AMOUNTS}_currency_length",
        f"fk_{_AMOUNTS}_tender_id",
        f"fk_{_AMOUNTS}_tender_lot_id",
        f"pk_{_AMOUNTS}",
    } <= {name for name, *_ in from_migration[1]}
    assert {name for name, _ in from_migration[2]} == {
        f"pk_{_AMOUNTS}",
        f"ix_{_AMOUNTS}_tender_id",
        f"uq_{_AMOUNTS}_current",
        f"uq_{_AMOUNTS}_current_row",
    }
    assert [name for name, _ in from_migration[3]] == ["tender_amount_observations_append_only"]

    # Una observacio real sobre una licitacio. El context, amb l'ORM.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.sources import Source
    from app.models.tenders import Tender

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        source = Source(code=f"m63-{uuid_module.uuid4().hex[:8]}", name="Font")
        session.add(source)
        await session.flush()
        tender = Tender(source_id=source.id, external_id="m63", title="T")
        session.add(tender)
        await session.commit()
        tender_id = tender.id

    count = sa.text(f"SELECT count(*) FROM {_AMOUNTS}")
    async with engine.begin() as conn:
        await conn.execute(
            sa.text(
                f"INSERT INTO {_AMOUNTS} (id, tender_id, amount_kind, amount, source_code, "
                "source_entity_key, source_row_id, retrieved_at, evidence_url, evidence_sha256, "
                "validation_status, valid_from, provenance) VALUES (gen_random_uuid(), :tender, "
                "'pbl_with_tax', 247121.63, 'pscp_open_data:ybgg-dgi6', 'k', 'r', now(), "
                "'https://x', :sha, 'accepted', now(), '{}')"
            ),
            {"tender": tender_id, "sha": "a" * 64},
        )

    # Amb dades, el downgrade refusa i no toca res: ni la taula, ni el trigger, ni la fila.
    with pytest.raises(DBAPIError) as caught:
        async with engine.begin() as conn:
            await conn.run_sync(run, m63.downgrade)
    assert "0063 downgrade refusat" in str(caught.value)
    async with engine.begin() as conn:
        assert await has_table(conn)
        assert (await conn.execute(count)).scalar_one() == 1
        assert await schema(conn) == from_migration

    # Amb la marca explicita, baixa encara que hi hagi files; i torna a pujar igual.
    async with engine.begin() as conn:
        await conn.run_sync(run, drop_anyway)
        assert not await has_table(conn)
        assert await functions(conn) == []
        await conn.run_sync(run, m63.upgrade)
        assert await schema(conn) == from_migration
        assert (await conn.execute(count)).scalar_one() == 0

    # Sobre l'esquema de la MIGRACIO (no el dels models): les cascades, les dues
    # unicitats i les regles de l'import.
    insert = sa.text(
        f"INSERT INTO {_AMOUNTS} (id, tender_id, tender_lot_id, amount_kind, amount, "
        "source_code, source_entity_key, source_row_id, retrieved_at, evidence_url, "
        "evidence_sha256, validation_status, valid_from, provenance) VALUES "
        "(gen_random_uuid(), :tender, :lot, 'pbl_with_tax', :amount, 'pscp_open_data:ybgg-dgi6', "
        "'k', :row, now(), 'https://x', :sha, :status, now(), '{}')"
    )
    async with engine.begin() as conn:
        lot_id = (
            await conn.execute(
                sa.text(
                    "INSERT INTO tender_lots (id, tender_id, external_lot_id, lot_number, "
                    "currency, created_at, updated_at) VALUES (gen_random_uuid(), :t, '1', 1, "
                    "'EUR', now(), now()) RETURNING id"
                ),
                {"t": tender_id},
            )
        ).scalar_one()
    base = {"tender": tender_id, "lot": None, "sha": "a" * 64}
    rows = [
        {**base, "amount": 10, "row": "r", "status": "accepted"},
        {**base, "amount": None, "row": "old", "status": "no_value"},
        {**base, "amount": 0, "row": "zero", "status": "no_value"},
        {**base, "amount": 5, "row": "pre", "status": "superseded"},
        {**base, "amount": None, "row": "pre_null", "status": "superseded"},
        {**base, "amount": None, "row": "bad", "status": "conflict"},
        {**base, "lot": lot_id, "amount": 7, "row": "r_1", "status": "accepted"},
    ]
    async with engine.begin() as conn:
        for row in rows:
            await conn.execute(insert, row)
    for bad in (
        {**base, "amount": 11, "row": "r2", "status": "accepted"},  # 2a accepted vigent
        {**base, "amount": 3, "row": "r", "status": "conflict"},  # 2a vigent de la fila
        {**base, "amount": 0, "row": "z", "status": "accepted"},
        {**base, "amount": -1, "row": "z", "status": "conflict"},
        {**base, "amount": 1, "row": "z", "status": "no_value"},
    ):
        with pytest.raises(DBAPIError):
            async with engine.begin() as conn:
                await conn.execute(insert, bad)
    async with engine.begin() as conn:
        assert (await conn.execute(count)).scalar_one() == len(rows)
        await conn.execute(sa.text("DELETE FROM tender_lots WHERE id = :id"), {"id": lot_id})
        assert (await conn.execute(count)).scalar_one() == len(rows) - 1
        await conn.execute(sa.text("DELETE FROM tenders WHERE id = :id"), {"id": tender_id})
        assert (await conn.execute(count)).scalar_one() == 0
