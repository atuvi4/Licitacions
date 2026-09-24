"""Fonament d'esquema per a la ingesta manual (S3-S1).

Dues coses que no comparteixen taula pero si motiu: totes dues existeixen
perque una persona pugui introduir una licitacio que cap font sindicada no
porta.

**Organismes privats.** `buyers.owner_company_id` segueix exactament la regla de
`tenders.owner_company_id`: `NULL` es public i canonic, amb valor es privat de
l'empresa que el va escriure. Un organisme public es informacio publica, pero el
que una empresa tecleja al seu wizard no ho es fins que algu ho valida, i
publicar-ho al cataleg global en silenci seria decidir-ho per ella.

`ondelete='CASCADE'`, com a `tenders`. La cadena s'ha comprovat i no en deixa cap
de penjada: `tenders.buyer_id` ja va amb `SET NULL`, aixi que esborrar una
empresa se'n pot emportar els seus organismes privats sense deixar cap licitacio
impossible. Els organismes globals tenen la columna a `NULL` i `NULL` no
coincideix mai amb l'empresa esborrada, de manera que el cascade no els toca.

**Fitxers pujats.** `tender_document_files.source_url` passa a ser nul·lable
perque un fitxer que puja una persona no ve de cap URL, i s'hi afegeix `origin`
per no haver de deduir-ho: sense aquest camp, `source_url IS NULL` voldria dir
alhora "pujat" i "descarrega mal registrada". `uploaded_by` va amb `SET NULL`:
si l'usuari es dona de baixa, el fitxer segueix sent valid i el que es perd es
qui el va pujar.

Les files existents queden a `origin='fetched'`, que es el que son: totes venen
de la ingesta. El `server_default` hi es nomes per poder omplir-les; el model no
en te cap, de manera que qui escrigui una fila nova ha de dir d'on surt.

**No es migra res mes.** Cap backfill de propietaris: ni les licitacions ni els
organismes existents en guanyen cap.

Aquesta revisio NO crea cap endpoint ni cap font MANUAL. Nomes l'esquema.

Revision ID: 0028_manual_ingestion_support
Revises: 0027_tender_tenancy
Create Date: 2026-09-21
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0028_manual_ingestion_support'
down_revision: str | None = '0027_tender_tenancy'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUYERS = 'buyers'
_BUYER_FK = 'fk_buyers_owner_company'
_BUYER_INDEX = 'ix_buyers_owner_company_id'

_FILES = 'tender_document_files'
_FILES_FK = 'fk_tender_document_files_uploaded_by'
_ORIGIN = sa.Enum('fetched', 'uploaded', name='document_file_origin', native_enum=False)


def upgrade() -> None:
    op.add_column(_BUYERS, sa.Column('owner_company_id', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f(_BUYER_FK), _BUYERS, 'companies', ['owner_company_id'], ['id'], ondelete='CASCADE'
    )
    op.create_index(_BUYER_INDEX, _BUYERS, ['owner_company_id'])

    op.alter_column(_FILES, 'source_url', existing_type=sa.String(length=1000), nullable=True)
    op.add_column(
        _FILES,
        sa.Column('origin', _ORIGIN, nullable=False, server_default='fetched'),
    )
    # El default de servidor nomes servia per omplir les files que ja hi havia:
    # a partir d'ara, qui insereix ha de dir d'on surt el fitxer.
    op.alter_column(_FILES, 'origin', server_default=None)
    op.add_column(_FILES, sa.Column('uploaded_by', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        op.f(_FILES_FK), _FILES, 'users', ['uploaded_by'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_FILES_FK), _FILES, type_='foreignkey')
    op.drop_column(_FILES, 'uploaded_by')
    op.drop_column(_FILES, 'origin')
    # Tornar a NOT NULL exigeix que no hi hagi cap fitxer pujat: si n'hi ha, la
    # baixada ha de fallar aqui i no deixar una URL inventada.
    op.alter_column(_FILES, 'source_url', existing_type=sa.String(length=1000), nullable=False)

    op.drop_index(_BUYER_INDEX, table_name=_BUYERS)
    op.drop_constraint(op.f(_BUYER_FK), _BUYERS, type_='foreignkey')
    op.drop_column(_BUYERS, 'owner_company_id')
