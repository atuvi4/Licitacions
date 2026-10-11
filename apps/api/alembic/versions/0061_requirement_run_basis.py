"""Base del run de requisits, identitat d'analisi i punter per empresa.

Fase 3B-2 (spec §6.2-§6.5). Additiva i reversible.

`requirement_extraction_runs` guanya:

* `run_basis` (NOT NULL, CHECK `real|simulation|unconfigured|gold`): sobre que
  s'ha construit el run. Backfill per `provider` amb la mateixa regla que
  `RequirementRunBasis.for_provider`: buit -> `unconfigured`; `fake`/`fixture` ->
  `simulation`; `gold`/`gold:*` -> `gold`; la resta `real`.
* `needs_review` (NOT NULL, per defecte fals) i l'estat nou `completed_empty`
  (l'estat passa de VARCHAR(9) a VARCHAR(15)); CHECK: un `completed_empty` sempre
  porta `needs_review`.
* `analysis_identity` (VARCHAR(64), nul·lable): sha256 del JSON canonic de
  l'analisi. Tambe a `analysis_quotes`.

Despres del backfill, i ABANS del NOT NULL i de cap CHECK, es DEGRADA
(`is_current = false`) tot run vigent que no sigui un COMPLETED `real` o `gold`:
`fake`/`fixture` (`simulation`), proveidor buit (`unconfigured`) i els PARTIAL
llegats (abans de 3B-2 un parcial es promovia; ara mai, §6.3).

Nomes a PostgreSQL, dos CHECK mes (capa de dades de §6.3):
`current_is_real_or_gold` (`is_current = false OR run_basis IN ('real','gold')`)
i `status_valid` (estats tancats, `completed_empty` inclos). Al model, amb
`ddl_if(dialect='postgresql')`: SQLite no canvia.

Verificacio de NOMES LECTURA abans de migrar (pilot, Task 12 al runbook):

    SELECT provider, status, is_current, count(*)
      FROM requirement_extraction_runs GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;
    -- vigents que es degradaran (provider fake/fixture/buit o status no completed):
    SELECT id, tender_id, provider, status FROM requirement_extraction_runs
     WHERE is_current AND (status <> 'completed'
           OR lower(trim(coalesce(provider, ''))) IN ('', 'fake', 'fixture'));
    -- cap estat fora de la llista (el CHECK nou fallaria):
    SELECT DISTINCT status FROM requirement_extraction_runs
     WHERE status NOT IN ('pending', 'running', 'completed', 'partial', 'failed');

Resultat esperat al pilot el 2026-10-08: cap run de requisits (0 files a les
tres consultes); qualsevol fila de la segona es una analisi simulada o parcial que
deixara de ser vigent, i s'ha de saber abans.

Taula nova de tenant `company_tender_requirement_runs` (PK `company_id`,
`tender_id`): el run de requisits que ha acceptat cada empresa.

Prerequisits de quota i despesa d'LLM (Task 8, §6.1 i §6.7), dins aquesta mateixa
revisio (cap 0062):

* `analysis_quotes` guanya `prerequisites` (JSON), `accept_incomplete_package`
  (NOT NULL, per defecte fals) i `estimated_cost_usd` (NUMERIC(12, 6)).
* Taula global `llm_spend_ledger`: una linia per chunk respost (`run_id`,
  `chunk_index`, `provider`, `model`, tokens, `cost_usd` NOT NULL, `budget_scope`
  `canary|run`, `created_at`). La FK al run es `SET NULL`: la despesa sobreviu al
  run. CHECK de cost i tokens no negatius i d'scope tancat.
* Taula global `llm_budget_authorizations` (`scope`, `cap_usd`, `authorized_by`,
  `note`, `created_at`): CHECK de scope tancat (`canary|run|pilot_total`) i cap > 0.

SQLite (nomes tests, que creen l'esquema amb els models): s'afegeixen les
columnes i les taules sense NOT NULL nou a runs, CHECK de runs, canvi de tipus ni
RAISE.

Downgrade: refusa de manera explicita (RAISE abans de cap DROP) si hi ha algun
run `completed_empty` (tornar enrere l'hauria de coaccionar a un altre estat o
esborrar-lo) o alguna linia al llibre de despesa (mai s'esborra despesa real). Si
no, desfa en ordre invers: autoritzacions, llibre, columnes de la quota, punter i
columnes dels runs. No s'esborra ni es coacciona mai res.

Revision ID: 0061_requirement_run_basis
Revises: 0060_structure_provenance
Create Date: 2026-10-08
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0061_requirement_run_basis"
down_revision: str | None = "0060_structure_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNS = "requirement_extraction_runs"
_QUOTES = "analysis_quotes"
_POINTER = "company_tender_requirement_runs"

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_BASES = ("real", "simulation", "unconfigured", "gold")

_PROVIDER = "lower(trim(provider))"
_BACKFILL = (
    f"UPDATE {_RUNS} SET run_basis = CASE "
    f"WHEN trim(coalesce(provider, '')) = '' THEN 'unconfigured' "
    f"WHEN {_PROVIDER} IN ('fake', 'fixture') THEN 'simulation' "
    f"WHEN {_PROVIDER} = 'gold' THEN 'gold' "
    f"WHEN substr({_PROVIDER}, 1, 5) = 'gold:' THEN 'gold' "
    f"ELSE 'real' END"
)
_STATUSES = ("pending", "running", "completed", "partial", "failed", "completed_empty")
#: Nomes un COMPLETED real/gold continua vigent (§6.3; M3 de la revisio).
_DEMOTE = (
    f"UPDATE {_RUNS} SET is_current = false WHERE is_current "
    "AND (status <> 'completed' OR run_basis NOT IN ('real', 'gold'))"
)

# Sense `:`, `%` ni cometes al missatge: `op.execute` el compila com a `text()`.
_REFUSAL = (
    "0061 downgrade refusat - hi ha runs de requisits completed_empty i tornar "
    "enrere els hauria de coaccionar o esborrar"
)
_REFUSE_DOWNGRADE = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM {_RUNS} WHERE status = 'completed_empty') THEN
        RAISE EXCEPTION '{_REFUSAL}';
    END IF;
END
$$
"""

_LEDGER = "llm_spend_ledger"
_AUTHORIZATIONS = "llm_budget_authorizations"
_SCOPES = ("canary", "run", "pilot_total")
#: L'scope d'una linia del llibre: el del run que l'ha pagat (mai el total).
_LEDGER_SCOPES = ("canary", "run")
_USD = sa.Numeric(precision=12, scale=6)

_LEDGER_REFUSAL = (
    "0061 downgrade refusat - hi ha despesa d'LLM al llibre llm_spend_ledger i "
    "tornar enrere l'hauria d'esborrar"
).replace("'", "")
_REFUSE_LEDGER_DOWNGRADE = f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM {_LEDGER}) THEN
        RAISE EXCEPTION '{_LEDGER_REFUSAL}';
    END IF;
END
$$
"""


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def upgrade() -> None:
    postgresql_ = _is_postgresql()

    op.add_column(_RUNS, sa.Column("run_basis", sa.String(length=12), nullable=True))
    op.add_column(
        _RUNS,
        sa.Column("needs_review", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(_RUNS, sa.Column("analysis_identity", sa.String(length=64), nullable=True))
    op.add_column(_QUOTES, sa.Column("analysis_identity", sa.String(length=64), nullable=True))

    # Backfill i degradacio ABANS del NOT NULL i dels CHECK.
    op.execute(sa.text(_BACKFILL))
    op.execute(sa.text(_DEMOTE))
    if postgresql_:
        op.alter_column(_RUNS, "run_basis", nullable=False)
        op.create_check_constraint(
            op.f(f"ck_{_RUNS}_run_basis_valid"), _RUNS, _in_list("run_basis", _BASES)
        )
        op.alter_column(
            _RUNS,
            "status",
            type_=sa.String(length=15),
            existing_type=sa.String(length=9),
            existing_nullable=False,
        )
        op.create_check_constraint(
            op.f(f"ck_{_RUNS}_completed_empty_needs_review"),
            _RUNS,
            "status <> 'completed_empty' OR needs_review",
        )
        op.create_check_constraint(
            op.f(f"ck_{_RUNS}_current_is_real_or_gold"),
            _RUNS,
            "is_current = false OR run_basis IN ('real', 'gold')",
        )
        op.create_check_constraint(
            op.f(f"ck_{_RUNS}_status_valid"), _RUNS, _in_list("status", _STATUSES)
        )

    op.create_table(
        _POINTER,
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("tender_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_by", sa.Uuid(), nullable=True),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("company_id", "tender_id", name=op.f(f"pk_{_POINTER}")),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f(f"fk_{_POINTER}_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tender_id"],
            ["tenders.id"],
            name=op.f(f"fk_{_POINTER}_tender_id_tenders"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{_RUNS}.id"],
            name="fk_company_tender_requirement_runs_run_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name="fk_company_tender_requirement_runs_accepted_by",
            ondelete="SET NULL",
        ),
    )
    op.create_index(f"ix_{_POINTER}_run_id", _POINTER, ["run_id"])

    # --- Task 8: prerequisits de quota, llibre de despesa i autoritzacions -------
    op.add_column(_QUOTES, sa.Column("prerequisites", sa.JSON(), nullable=True))
    op.add_column(
        _QUOTES,
        sa.Column(
            "accept_incomplete_package", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
    )
    op.add_column(_QUOTES, sa.Column("estimated_cost_usd", _USD, nullable=True))

    op.create_table(
        _LEDGER,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", _USD, nullable=False),
        sa.Column("budget_scope", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_LEDGER}")),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{_RUNS}.id"],
            name="fk_llm_spend_ledger_run_id",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("run_id", "chunk_index", name="uq_llm_spend_ledger_run_chunk"),
        sa.CheckConstraint("cost_usd >= 0", name=op.f(f"ck_{_LEDGER}_cost_valid")),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0", name=op.f(f"ck_{_LEDGER}_tokens_valid")
        ),
        sa.CheckConstraint(
            _in_list("budget_scope", _LEDGER_SCOPES),
            name=op.f(f"ck_{_LEDGER}_budget_scope_valid"),
        ),
    )
    op.create_index(f"ix_{_LEDGER}_created_at", _LEDGER, ["created_at"])

    op.create_table(
        _AUTHORIZATIONS,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("cap_usd", _USD, nullable=False),
        sa.Column("authorized_by", sa.Uuid(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{_AUTHORIZATIONS}")),
        sa.ForeignKeyConstraint(
            ["authorized_by"],
            ["users.id"],
            name="fk_llm_budget_authorizations_authorized_by",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            _in_list("scope", _SCOPES), name=op.f(f"ck_{_AUTHORIZATIONS}_scope_valid")
        ),
        sa.CheckConstraint("cap_usd > 0", name=op.f(f"ck_{_AUTHORIZATIONS}_cap_valid")),
    )
    op.create_index(f"ix_{_AUTHORIZATIONS}_scope_created", _AUTHORIZATIONS, ["scope", "created_at"])


def downgrade() -> None:
    postgresql_ = _is_postgresql()
    if postgresql_:
        # Primer els refusos: amb un `completed_empty` o amb despesa al llibre no es
        # toca res.
        op.execute(_REFUSE_DOWNGRADE)
        op.execute(_REFUSE_LEDGER_DOWNGRADE)

    # --- Task 8, en ordre invers --------------------------------------------------
    op.drop_index(f"ix_{_AUTHORIZATIONS}_scope_created", table_name=_AUTHORIZATIONS)
    op.drop_table(_AUTHORIZATIONS)
    op.drop_index(f"ix_{_LEDGER}_created_at", table_name=_LEDGER)
    op.drop_table(_LEDGER)
    op.drop_column(_QUOTES, "estimated_cost_usd")
    op.drop_column(_QUOTES, "accept_incomplete_package")
    op.drop_column(_QUOTES, "prerequisites")

    op.drop_index(f"ix_{_POINTER}_run_id", table_name=_POINTER)
    op.drop_table(_POINTER)
    if postgresql_:
        op.drop_constraint(op.f(f"ck_{_RUNS}_status_valid"), _RUNS, type_="check")
        op.drop_constraint(op.f(f"ck_{_RUNS}_current_is_real_or_gold"), _RUNS, type_="check")
        op.drop_constraint(op.f(f"ck_{_RUNS}_completed_empty_needs_review"), _RUNS, type_="check")
        op.alter_column(
            _RUNS,
            "status",
            type_=sa.String(length=9),
            existing_type=sa.String(length=15),
            existing_nullable=False,
        )
        op.drop_constraint(op.f(f"ck_{_RUNS}_run_basis_valid"), _RUNS, type_="check")
    op.drop_column(_QUOTES, "analysis_identity")
    op.drop_column(_RUNS, "analysis_identity")
    op.drop_column(_RUNS, "needs_review")
    op.drop_column(_RUNS, "run_basis")
