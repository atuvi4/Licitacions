"""Compliment v6 (Bloc 1): estat del pre-fit al run, notes editables i ISO 20000.

Additiva i reversible. Tres canvis petits:

* `company_compliance_runs.prefit_status` (VARCHAR(17), nul·lable): la classificacio
  del pre-fit (`candidate`, `low_fit`, `excluded`, `closed`, `insufficient_data`)
  quan es va calcular el compliment. Com `prefit_score`, no es fusiona amb
  l'elegibilitat, pero decideix si una licitacio elegible es candidata: un `low_fit`
  amb 60 punts o mes ja no surt «Candidata» (D2). Els runs anteriors queden a NULL
  (es llegeixen com abans, per la puntuacio). A PostgreSQL, CHECK dels estats tancats
  (`ck_company_compliance_runs_prefit_status_valid`; NULL hi passa).
* `workspace_notes.updated_at` (TIMESTAMPTZ, nul·lable): darrera edicio del text
  d'una nota. NULL = no s'ha editat mai. Qui l'edita o l'esborra queda al registre
  d'auditoria (sense el text).
* Vocabulari global: `certification_standards` guanya `ISO_20000` (ISO/IEC 20000-1,
  gestio de serveis de TI), sense nivells. Nomes si no hi es.

Cap backfill: cap fila existent no canvia.

Downgrade: a PostgreSQL refusa (RAISE abans de cap DROP) si hi ha alguna
certificacio d'empresa amb `ISO_20000`, algun run amb `prefit_status` o alguna nota
editada: tornar enrere ho esborraria, i aquesta migracio no esborra res. Sense
aquestes dades, treu el CHECK, la norma i les dues columnes.

Revision ID: 0064_compliance_v6_notes
Revises: 0063_tender_amount_observations
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0064_compliance_v6_notes"
down_revision: str | None = "0063_tender_amount_observations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNS = "company_compliance_runs"
_NOTES = "workspace_notes"
# --- Copiat de l'aplicacio (`MatchStatus`): la migracio no l'importa. ------------
_PREFIT_STATUSES = ("candidate", "low_fit", "excluded", "closed", "insufficient_data")
_PREFIT_CHECK = "ck_company_compliance_runs_prefit_status_valid"
_ISO_20000 = ("ISO_20000", "ISO/IEC 20000-1 (gestio de serveis de TI)")


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    op.add_column(_RUNS, sa.Column("prefit_status", sa.String(length=17), nullable=True))
    if _is_postgresql():
        values = ", ".join(f"'{status}'" for status in _PREFIT_STATUSES)
        op.create_check_constraint(
            op.f(_PREFIT_CHECK), _RUNS, f"prefit_status IN ({values})"
        )
    op.add_column(_NOTES, sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    code, label = _ISO_20000
    op.execute(
        "INSERT INTO certification_standards (code, label, levels) "
        f"SELECT '{code}', '{label}', NULL "
        f"WHERE NOT EXISTS (SELECT 1 FROM certification_standards WHERE code = '{code}')"
    )


#: (condicio que impedeix tornar enrere, missatge) — mai s'esborra cap dada.
_DOWNGRADE_GUARDS = (
    (
        f"SELECT 1 FROM company_certifications WHERE standard_code = '{_ISO_20000[0]}'",
        f"0064: hi ha certificacions amb la norma {_ISO_20000[0]}; "
        "tornar enrere les deixaria sense norma",
    ),
    (
        f"SELECT 1 FROM {_RUNS} WHERE prefit_status IS NOT NULL",
        "0064: hi ha runs de compliment amb prefit_status; tornar enrere l''esborraria",
    ),
    (
        f"SELECT 1 FROM {_NOTES} WHERE updated_at IS NOT NULL",
        "0064: hi ha notes editades (updated_at); tornar enrere ho esborraria",
    ),
)


def downgrade() -> None:
    code, _label = _ISO_20000
    if _is_postgresql():
        checks = "\n".join(
            f"IF EXISTS ({query}) THEN RAISE EXCEPTION '{message}'; END IF;"
            for query, message in _DOWNGRADE_GUARDS
        )
        op.execute(f"DO $$\nBEGIN\n{checks}\nEND $$;")
        op.drop_constraint(op.f(_PREFIT_CHECK), _RUNS, type_="check")
    op.execute(f"DELETE FROM certification_standards WHERE code = '{code}'")
    op.drop_column(_NOTES, "updated_at")
    op.drop_column(_RUNS, "prefit_status")
