"""Revisio d'esquema que espera aquest codi (el cap d'Alembic).

Es una constant, no una lectura de `alembic/` en temps d'execucio: el proces que
ingereix no ha de dependre de trobar `alembic.ini`. `tests/test_migrations.py`
comprova que coincideix amb l'unic cap de `alembic heads`; cada migracio nova
l'ha d'actualitzar.

La ingesta la desa a `source_records.failed_schema_revision` quan una publicacio
falla: un error determinista (dades, integritat, parseig) nomes es torna a
provar si la revisio o el `parser_version` han canviat.
"""

SCHEMA_REVISION = "0061_requirement_run_basis"
