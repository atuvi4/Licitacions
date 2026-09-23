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


def test_0037_encara_no_existeix():
    import pathlib

    versions = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
    assert not list(versions.glob("0037*")), "0037 no toca a S3-S8b"
