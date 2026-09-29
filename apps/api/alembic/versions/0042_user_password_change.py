"""Canvi de contrasenya obligatori al primer acces (alta d'empresa i propietari).

Additiva i minima: `users.must_change_password`. Un propietari donat d'alta per
la plataforma rep una contrasenya temporal i l'ha de canviar abans de fer res
mes; el backend ho imposa (no nomes la interficie). Tots els usuaris existents
neixen amb `false`: cap sessio actual queda bloquejada.

**Downgrade.** Treu la columna. No hi ha dades que es perdin mes enlla del
senyal: un usuari amb un canvi pendent podria continuar amb la temporal.

Revision ID: 0042_user_password_change
Revises: 0041_review_reconciliation
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0042_user_password_change'
down_revision: str | None = '0041_review_reconciliation'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    # Treu el senyal; cap altra dada no es toca.
    op.drop_column('users', 'must_change_password')
