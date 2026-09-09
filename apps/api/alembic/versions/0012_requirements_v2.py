"""Requisits v2: modalitat, alternatives, equivalencies i formules.

Nomes AFEGEIX columnes; cap dada existent no canvia de significat. Els runs v1
segueixen sent interpretables: `value_schema` a NULL vol dir "valor amb la forma
lliure de la v1", que es exactament el que hi ha ara.

A `requirements`:

* `value_schema`: quin contracte segueix `value_json`.
* `modality`: com obliga el plec, amb el matis que `obligation` no te. Es a banda
  i no la substitueix perque 4C llegeix l'obligatorietat i canviar-ne el
  significat invalidaria tot el compliment ja calculat.
* `alternative_group`: dues vies de la mateixa exigencia comparteixen grup, de
  manera que "A o B" no siguin dos blocadors independents.
* `equivalence_allowed`: "ISO 9001 o equivalent". Qui decideix que hi equival es
  el compliment o una persona, mai l'extractor.
* `condition_note`: la condicio literal que activa el requisit.

A `award_criteria`:

* `formula_text`, `formula_variables`, `tranches`, `criterion_type`: la formula
  de puntuacio es conservava nomes com a expressio i sovint es perdia. Ara es
  guarda tambe la frase que la conte i el significat dels simbols, copiats.

Els valors per defecte de les files existents son els conservadors: `unknown` a
les dues enumeracions i `false` a l'equivalencia.

Revision ID: 0012_requirements_v2
Revises: 0011_submission
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0012_requirements_v2'
down_revision: str | None = '0011_submission'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MODALITY = sa.Enum(
    'mandatory', 'optional', 'alternative', 'conditional', 'informational', 'unknown',
    name='requirement_modality', native_enum=False,
)
_CRITERION_TYPE = sa.Enum(
    'price', 'quality', 'technical', 'environmental', 'social', 'delivery_time',
    'warranty', 'improvement', 'other', 'unknown',
    name='award_criterion_type', native_enum=False,
)


def upgrade() -> None:
    # Els `server_default` son nomes per omplir les files que ja existeixen; es
    # retiren tot seguit perque el model no en te i, si hi quedessin, model i
    # base de dades divergirien.
    op.add_column(
        'requirements', sa.Column('value_schema', sa.String(length=50), nullable=True)
    )
    op.add_column(
        'requirements',
        sa.Column('modality', _MODALITY, nullable=False, server_default='unknown'),
    )
    op.alter_column('requirements', 'modality', server_default=None)
    op.add_column(
        'requirements', sa.Column('alternative_group', sa.String(length=80), nullable=True)
    )
    op.create_index(
        'ix_requirements_alternative_group', 'requirements', ['alternative_group'], unique=False
    )
    op.add_column(
        'requirements',
        sa.Column(
            'equivalence_allowed', sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.alter_column('requirements', 'equivalence_allowed', server_default=None)
    op.add_column(
        'requirements', sa.Column('condition_note', sa.String(length=500), nullable=True)
    )

    op.add_column('award_criteria', sa.Column('formula_text', sa.Text(), nullable=True))
    op.add_column('award_criteria', sa.Column('formula_variables', sa.JSON(), nullable=True))
    op.add_column('award_criteria', sa.Column('tranches', sa.JSON(), nullable=True))
    op.add_column(
        'award_criteria',
        sa.Column('criterion_type', _CRITERION_TYPE, nullable=False, server_default='unknown'),
    )
    op.alter_column('award_criteria', 'criterion_type', server_default=None)


def downgrade() -> None:
    op.drop_column('award_criteria', 'criterion_type')
    op.drop_column('award_criteria', 'tranches')
    op.drop_column('award_criteria', 'formula_variables')
    op.drop_column('award_criteria', 'formula_text')

    op.drop_column('requirements', 'condition_note')
    op.drop_column('requirements', 'equivalence_allowed')
    op.drop_index('ix_requirements_alternative_group', table_name='requirements')
    op.drop_column('requirements', 'alternative_group')
    op.drop_column('requirements', 'modality')
    op.drop_column('requirements', 'value_schema')
