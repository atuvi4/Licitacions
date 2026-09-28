"""Reconciliacio persistent d'una revisio amb una run nova (S-R9).

Additiva i minima:

* `review_reconciliations`: el cicle de vida (empresa, licitacio, run desti),
  amb la base d'origen per seccio i el pla aplicat.
* `review_item_revisions.action` accepta `reconcile`: el reenllaç d'una font
  primaria a la fila equivalent de la run nova queda a l'historial.

L'extraccio no es toca. Les fonts velles no s'esborren: es marquen
`unlinked_at` i continuen protegint la seva run (`NO ACTION`).

**Downgrade.** Treu la taula i torna el CHECK antic. Si ja hi ha revisions
`reconcile`, el CHECK antic no les admet i esborrar-les seria perdre historial
(el trigger d'append-only tampoc ho deixa fer): el downgrade es REFUSA amb un
error explicit. No s'esborra mai cap revisio.

Revision ID: 0041_review_reconciliation
Revises: 0040_review_layer
Create Date: 2026-09-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0041_review_reconciliation'
down_revision: str | None = '0040_review_layer'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'review_reconciliations'
_REVISIONS = 'review_item_revisions'
_ACTION_CHECK = 'ck_review_item_revisions_action_valid'
_STATES = ('in_progress', 'needs_review', 'completed')
_OLD_ACTIONS = (
    'create', 'confirm', 'correct', 'reject', 'mark_partial', 'note',
    'set_status', 'set_award_role', 'link_source', 'archive',
)
_NEW_ACTIONS = (*_OLD_ACTIONS, 'reconcile')

# Funciona igual en linia i en mode `--sql`: la comprovacio la fa la base.
_REFUSE_IF_RECONCILED = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM review_item_revisions WHERE action = 'reconcile') THEN
        RAISE EXCEPTION 'downgrade refusat: hi ha revisions reconcile (no s''esborra historial)'
            USING ERRCODE = 'restrict_violation';
    END IF;
END
$$
"""


def _json() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(postgresql.JSONB(), 'postgresql')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == 'postgresql'


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('target_run_id', sa.Uuid(), nullable=False),
        sa.Column('requirement_source_run_ids', _json(), nullable=False),
        sa.Column('award_source_run_ids', _json(), nullable=False),
        sa.Column(
            'state',
            sa.Enum(*_STATES, name='review_reconciliation_state', native_enum=False),
            nullable=False,
        ),
        sa.Column('applied_plan_hash', sa.String(length=64), nullable=False),
        sa.Column('exact_applied_count', sa.Integer(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.CheckConstraint(_in_list('state', _STATES), name='state_valid'),
        sa.CheckConstraint('exact_applied_count >= 0', name='exact_applied_count_positive'),
        sa.UniqueConstraint('company_id', 'tender_id', 'target_run_id',
                            name='uq_review_reconciliations_target'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['target_run_id'], ['requirement_extraction_runs.id'],
                                ondelete='NO ACTION'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
    )
    op.create_index('ix_review_reconciliations_scope', _TABLE,
                    ['company_id', 'tender_id', 'state'])

    op.drop_constraint(op.f(_ACTION_CHECK), _REVISIONS, type_='check')
    op.create_check_constraint(op.f(_ACTION_CHECK), _REVISIONS,
                               _in_list('action', _NEW_ACTIONS))


def downgrade() -> None:
    if _is_postgresql():
        op.execute(_REFUSE_IF_RECONCILED)
    op.drop_constraint(op.f(_ACTION_CHECK), _REVISIONS, type_='check')
    op.create_check_constraint(op.f(_ACTION_CHECK), _REVISIONS,
                               _in_list('action', _OLD_ACTIONS))
    op.drop_index('ix_review_reconciliations_scope', table_name=_TABLE)
    op.drop_table(_TABLE)
