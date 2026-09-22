"""D'on surt un fet: la provinença canònica d'Sprint 3 (S3-S4).

Una taula i res mes. El que la fa util no son les columnes sino a QUE
s'ancoren: mai a `TenderDocument` —la ranura logica, que canvia de contingut—
sino a una versio concreta (`tender_document_files`) o a una lectura concreta
(`tender_document_extractions` + `tender_document_pages`). Aixi una cita segueix
dient el mateix quan arriba una versio nova o es torna a extreure amb un parser
millor.

**`RESTRICT` a les tres ancores.** L'evidencia citada no s'ha de poder esborrar
per accident: si algu la vol treure, primer ha d'encarregar-se de les cites.
`tender_document_id`, en canvi, va amb `SET NULL` perque nomes serveix per
ensenyar un nom; si desapareix, la cita continua sent reconstruible amb el
fitxer i el `content_sha256`.

**`CASCADE` nomes cap a `tenders`.** Si s'esborra una licitacio se'n va tot el
que en depen, plecs inclosos, i mantenir-ne les cites deixaria files que apunten
a un expedient que ja no hi es.

**Tres CHECK amb nom.** Una fila impossible l'ha de rebutjar la base de dades i
no nomes Pydantic: qui escrigui per SQL cru tambe hi ha de passar. Es el mateix
motiu pel qual el gate de PostgreSQL d'aquesta fase els prova un per un.

Cap backfill i cap dada existent no es toca: la taula neix buida.

Revision ID: 0030_tender_source_refs
Revises: 0029_document_review_state
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0030_tender_source_refs'
down_revision: str | None = '0029_document_review_state'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'tender_source_refs'

#: VARCHAR + CHECK, com tots els enums del projecte (`enum_column`): modificar
#: un ENUM natiu de PostgreSQL en una migracio es incomode i poc reversible.
_REF_KIND = sa.Enum('extracted_page', 'document_file', name='source_ref_kind', native_enum=False)

_ANCHORED = (
    "(ref_kind = 'extracted_page' AND extraction_id IS NOT NULL AND page_id IS NOT NULL)"
    " OR (ref_kind = 'document_file' AND tender_document_file_id IS NOT NULL)"
)
_PAGE_ONE_BASED = 'page_number IS NULL OR page_number >= 1'
_CHAR_SPAN = (
    '(char_start IS NULL AND char_end IS NULL)'
    ' OR (char_start IS NOT NULL AND char_end IS NOT NULL'
    ' AND char_start >= 0 AND char_end > char_start)'
)


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('ref_kind', _REF_KIND, nullable=False),
        sa.Column('extraction_id', sa.Uuid(), nullable=True),
        sa.Column('page_id', sa.Uuid(), nullable=True),
        sa.Column('page_number', sa.Integer(), nullable=True),
        sa.Column('tender_document_file_id', sa.Uuid(), nullable=True),
        sa.Column('tender_document_id', sa.Uuid(), nullable=True),
        sa.Column('content_sha256', sa.String(length=64), nullable=False),
        sa.Column('location_label', sa.String(length=200), nullable=True),
        sa.Column('quote', sa.Text(), nullable=True),
        sa.Column('char_start', sa.Integer(), nullable=True),
        sa.Column('char_end', sa.Integer(), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['tender_id'], ['tenders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(
            ['extraction_id'],
            ['tender_document_extractions.id'],
            name='fk_tender_source_refs_extraction',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['page_id'],
            ['tender_document_pages.id'],
            name='fk_tender_source_refs_page',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['tender_document_file_id'],
            ['tender_document_files.id'],
            name='fk_tender_source_refs_file',
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['tender_document_id'],
            ['tender_documents.id'],
            name='fk_tender_source_refs_document',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'],
            ['users.id'],
            name='fk_tender_source_refs_author',
            ondelete='SET NULL',
        ),
        sa.CheckConstraint(_ANCHORED, name='source_ref_is_anchored'),
        sa.CheckConstraint(_PAGE_ONE_BASED, name='source_ref_page_is_one_based'),
        sa.CheckConstraint(_CHAR_SPAN, name='source_ref_char_span_is_coherent'),
    )
    op.create_index('ix_tender_source_refs_tender_id', _TABLE, ['tender_id'])
    # Per poder respondre "on mes es fan servir aquests bytes" sense escanejar
    # la taula sencera el dia que hi hagi cites de debo.
    op.create_index('ix_tender_source_refs_content_sha256', _TABLE, ['content_sha256'])


def downgrade() -> None:
    op.drop_index('ix_tender_source_refs_content_sha256', table_name=_TABLE)
    op.drop_index('ix_tender_source_refs_tender_id', table_name=_TABLE)
    op.drop_table(_TABLE)
