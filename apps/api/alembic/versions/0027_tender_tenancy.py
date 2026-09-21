"""Licitacions privades d'una empresa, i la font MANUAL que les portara (S3-S0).

Fins ara totes les licitacions eren publiques i globals. La ingesta manual
n'introdueix de privades, i la pregunta «qui la pot veure» necessita viure en
algun lloc concret.

Viu a `tenders.owner_company_id` i **no** a `sources`. La font es PROVINENCA
—d'on han sortit les dades—; l'autoritzacio es una altra cosa. Barrejar-les
tindria dues consequencies lletges: qualsevol consulta que fes join amb
`sources` hauria de recordar el filtre, i esborrar una empresa arrossegaria la
seva font i, amb ella, licitacions que no son seves.

`NULL` vol dir publica. **No es migra res**: totes les licitacions existents
queden a NULL i continuen sent visibles per a tothom exactament com abans. No hi
ha backfill ni n'hi ha d'haver.

`ondelete='CASCADE'`: si s'esborra l'empresa, les seves licitacions privades
se'n van amb ella. Es el que toca —ningu mes no les podia veure— i l'abast
queda acotat a les files que tenien aquella empresa com a propietaria.

L'index acompanya el predicat de visibilitat, que entra a TOTA consulta de
licitacions: llista, fitxa, facetes i recalcul del pre-fit.

`SourceType` guanya el valor `manual`. Els enums d'aquest repo son VARCHAR amb
`native_enum=False`, aixi que afegir-ne un valor no toca cap tipus de la base de
dades: nomes cal que el model i l'aplicacio el coneguin. Aqui no es crea cap
font: nomes la capacitat semantica perque S3-S1 la pugui fer servir.

Revision ID: 0027_tender_tenancy
Revises: 0026_technical_scope_review
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0027_tender_tenancy'
down_revision: str | None = '0026_technical_scope_review'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'tenders'
_COLUMN = 'owner_company_id'
_FK = 'fk_tenders_owner_company'
_INDEX = 'ix_tenders_owner_company_id'


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f(_FK),
        _TABLE,
        'companies',
        [_COLUMN],
        ['id'],
        ondelete='CASCADE',
    )
    op.create_index(_INDEX, _TABLE, [_COLUMN])


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_constraint(op.f(_FK), _TABLE, type_='foreignkey')
    op.drop_column(_TABLE, _COLUMN)
