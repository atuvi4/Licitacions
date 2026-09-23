"""D'on ve una linia economica (S3-S8).

Un sol camp nou: `economic_offer_lines.solution_option_id`. Diu que aquesta
linia es va crear adoptant una opcio de solucio concreta.

**L'opcio es la configuracio viva; la linia es el snapshot adoptat.** Quan mes
endavant canvia el proveidor, la quantitat o el cost de l'opcio, la linia NO es
mou: la xifra que va a l'oferta la va adoptar una persona un dia concret, i
moure-la sola voldria dir canviar una oferta sense que ningu ho hagues decidit.

**L'index unic es PARCIAL a proposit.** Nomes hi entren les linies vives
(`archived_at IS NULL`) que venen d'una opcio. Aixi:

* una opcio no pot tenir dues linies vives alhora —l'index es l'ultima defensa
  contra dues peticions simultanies, no nomes contra un doble clic—;
* una linia arxivada conserva el seu `solution_option_id` com a historia i, a
  la vegada, deixa l'opcio lliure per tornar a convertir-se en una linia nova
  amb el snapshot d'aquell moment.

`SET NULL` i no `CASCADE`: si algun dia desapareix l'opcio, es perd la traca,
no la feina. El preu escrit es conserva.

Cap backfill: les linies que ja hi havia no venen de cap opcio i es queden amb
`NULL`, que es exactament el que son.

L'identificador de revisio es curt perque `alembic_version.version_num` es un
`VARCHAR(32)`.

Revision ID: 0035_offer_line_solution_option
Revises: 0034_solution_option_dimensions
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0035_offer_line_solution_option'
down_revision: str | None = '0034_solution_option_dimensions'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'economic_offer_lines'
_INDEX = 'uq_economic_offer_lines_solution_option'


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column('solution_option_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_economic_offer_lines_solution_option',
        _TABLE,
        'workspace_solution_options',
        ['solution_option_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(
        _INDEX,
        _TABLE,
        ['solution_option_id'],
        unique=True,
        postgresql_where=sa.text('solution_option_id IS NOT NULL AND archived_at IS NULL'),
        sqlite_where=sa.text('solution_option_id IS NOT NULL AND archived_at IS NULL'),
    )


def downgrade() -> None:
    # Nomes es perd la traca de quina opcio va originar cada linia. Les linies
    # i els seus imports es queden sencers: el downgrade no toca cap xifra.
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_constraint(
        'fk_economic_offer_lines_solution_option', _TABLE, type_='foreignkey'
    )
    op.drop_column(_TABLE, 'solution_option_id')
