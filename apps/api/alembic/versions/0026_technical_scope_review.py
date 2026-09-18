"""Qui ha revisat l'abast tecnic d'una candidatura, i sobre que (C-14).

Sense aquests tres camps, zero solucions tecniques vol dir dues coses alhora:
"cap requisit no en necessita" i "ningu no s'ho ha mirat encara". El sistema no
pot distingir-les perque C-11 li prohibeix deduir quins requisits necessiten
solucio: ho decideix una persona. Aixo ho arregla registrant la decisio.

Els tres son NULL i **no es migra res**: tots els workspaces existents queden
com a NO revisats, que es el que exigeix C-5. Cap candidatura no arriba a la
revisio final per inercia, i la del seed s'adaptara a S8.

`technical_scope_fingerprint` diu SOBRE QUE es va confirmar. Si l'abast d'ara no
hi coincideix —canvia un requisit, n'apareix un de nou, es tria un altre lot o
algu declara que un requisit mes necessita solucio— la revisio deixa de ser
vigent. Aixo no s'escriu enlloc: es deriva comparant en llegir, com ja fa la
validacio de la solucio tecnica.

`reviewed_by` va amb SET NULL: si l'usuari es dona de baixa, la revisio segueix
havent passat; el que es perd es qui la va fer, i no al reves.

Revision ID: 0026_technical_scope_review
Revises: 0025_economic_offer
Create Date: 2026-09-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0026_technical_scope_review'
down_revision: str | None = '0025_economic_offer'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'bid_workspaces'
_FK = 'fk_bid_workspaces_technical_scope_reviewer'


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column('technical_scope_reviewed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(_TABLE, sa.Column('technical_scope_reviewed_by', sa.Uuid(), nullable=True))
    op.add_column(
        _TABLE, sa.Column('technical_scope_fingerprint', sa.String(length=64), nullable=True)
    )
    op.create_foreign_key(
        op.f(_FK),
        _TABLE,
        'users',
        ['technical_scope_reviewed_by'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_FK), _TABLE, type_='foreignkey')
    op.drop_column(_TABLE, 'technical_scope_fingerprint')
    op.drop_column(_TABLE, 'technical_scope_reviewed_by')
    op.drop_column(_TABLE, 'technical_scope_reviewed_at')
