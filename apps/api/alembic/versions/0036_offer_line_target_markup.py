"""L'objectiu de markup d'una linia (S3-S8b).

Un sol camp: `economic_offer_lines.target_markup_pct`. Es **markup sobre cost**,
no marge sobre venda, i el nom ho diu:

    preu proposat = cost unitari x (1 + markup / 100)

**No es cap preu.** El preu ofert continua sent nomes
`offered_unit_price_ex_vat`, i canviar l'objectiu no el toca mai. Escriure el
preu proposat al preu ofert es una accio a part que ha de demanar una persona.

El que NO hi ha, i es deliberat: ni `synced_at`, ni `diverged`, ni cap hash de
l'opcio, ni el preu proposat, ni si el preu coincideix amb l'objectiu. Tot aixo
es **derivat**: desar-ho voldria dir tenir una copia que envelleix i que un dia
diria una cosa diferent de la que surt del calcul.

`Numeric(9,4)` i cap CHECK de rang: una oferta pot anar deliberadament a cost, o
per sota. Prohibir el markup negatiu seria prohibir una decisio comercial
legitima.

Zero backfill: les linies que ja hi havia no tenen cap objectiu, i `NULL` es
exactament aixo.

Revision ID: 0036_offer_line_target_markup
Revises: 0035_offer_line_solution_option
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0036_offer_line_target_markup'
down_revision: str | None = '0035_offer_line_solution_option'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'economic_offer_lines'


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column('target_markup_pct', sa.Numeric(precision=9, scale=4), nullable=True),
    )


def downgrade() -> None:
    # Nomes es perd l'objectiu comercial. Cap preu ni cap cost es toca: el que
    # s'oferia continua sent exactament el que s'oferia.
    op.drop_column(_TABLE, 'target_markup_pct')
