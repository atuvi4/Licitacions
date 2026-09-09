"""Saber si una disjuncio esta sencera, en comptes de deduir-ho del recompte.

La primera versio de la logica de vies alternatives donava un grup per complet
quan hi havia dues branques o mes. Era una heuristica, i una de perillosa: haver
extret dues vies no demostra que el plec no en tingui una tercera. Amb aquella
regla, dues branques que fallen produien un blocador confirmat i el sistema
podia dir a una empresa que no es pot presentar a una licitacio que potser podia
acreditar per una via que l'extractor no havia vist.

`alternative_group_complete` ho fa explicit i el `NULL` es la resposta honesta
per defecte: no se sap. Nomes pot ser `True` quan l'estructura o l'evidencia
demostren que totes les vies estan representades, i mentre no ho sigui cap
branca del grup pot bloquejar.

Les files que ja existeixen queden a `NULL`, que es exactament el que sabem
d'elles. Aixo manté `2026SE00013::class-1` sense poder generar cap blocador, que
es el comportament correcte i el que el gold mateix demana.

Revision ID: 0014_alt_group_complete
Revises: 0013_insurance_limits
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0014_alt_group_complete'
down_revision: str | None = '0013_insurance_limits'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable i sense `server_default`: el valor per defecte ha de ser
    # "no se sap", no "no esta complet" ni "esta complet".
    op.add_column(
        'requirements',
        sa.Column('alternative_group_complete', sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('requirements', 'alternative_group_complete')
