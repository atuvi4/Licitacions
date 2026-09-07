"""Capa documental: original immutable i text per pagina.

Afegeix quatre taules GLOBALS (cap porta `company_id`):

* `tender_document_contents`: el blob, adrecat pel SHA-256. Unitat de
  deduplicacio: els mateixos bytes son un sol objecte a l'storage.
* `tender_document_extractions`: una passada de parser sobre un blob. Canviar
  `parser_version` crea una fila nova i conserva l'anterior.
* `tender_document_pages`: el text, 1-based, penjant de l'extraccio.
* `tender_document_files`: la versio, que lliga un `TenderDocument` amb un
  contingut concret.

NO altera cap taula existent. Els 1.188 Tenders, els SourceRecords, els
pre-fits i les dades del tenant Demo queden intactes.

Revision ID: 0005_tender_docs
Revises: 0004_prefit
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0005_tender_docs'
down_revision: str | None = '0004_prefit'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'tender_document_contents',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('sha256', sa.String(length=64), nullable=False),
        sa.Column('storage_key', sa.String(length=512), nullable=False),
        sa.Column('file_size', sa.BigInteger(), nullable=False),
        sa.Column('mime_type', sa.String(length=160), nullable=False),
        sa.Column('detected_extension', sa.String(length=10), nullable=True),
        sa.Column('is_text_extractable', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tender_document_contents')),
        sa.UniqueConstraint('sha256', name='uq_tender_document_contents_sha256'),
    )

    op.create_table(
        'tender_document_extractions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('content_id', sa.Uuid(), nullable=False),
        sa.Column('parser_version', sa.String(length=50), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'pending',
                'extracting',
                'extracted',
                'skipped_unsupported',
                'failed',
                name='extraction_status',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            'quality',
            sa.Enum(
                'good',
                'low_text',
                'empty',
                'failed',
                'not_applicable',
                name='extraction_quality',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('page_count', sa.Integer(), nullable=False),
        sa.Column('extracted_page_count', sa.Integer(), nullable=False),
        sa.Column('empty_page_count', sa.Integer(), nullable=False),
        sa.Column('text_char_count', sa.Integer(), nullable=False),
        sa.Column('ocr_candidate', sa.Boolean(), nullable=False),
        sa.Column('is_current', sa.Boolean(), nullable=False),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('parse_ms', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['content_id'],
            ['tender_document_contents.id'],
            name=op.f('fk_tender_document_extractions_content_id_tender_document_contents'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tender_document_extractions')),
        sa.UniqueConstraint(
            'content_id', 'parser_version', name='uq_tender_document_extractions_content_parser'
        ),
    )
    op.create_index(
        'ix_tender_document_extractions_content_id',
        'tender_document_extractions',
        ['content_id'],
        unique=False,
    )
    # Index unic parcial: nomes una extraccio vigent per contingut.
    op.create_index(
        'uq_tender_document_extractions_current',
        'tender_document_extractions',
        ['content_id'],
        unique=True,
        postgresql_where=sa.text('is_current'),
        sqlite_where=sa.text('is_current = 1'),
    )

    op.create_table(
        'tender_document_pages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('extraction_id', sa.Uuid(), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('char_count', sa.Integer(), nullable=False),
        sa.Column('text_hash', sa.String(length=64), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'extracted',
                'empty',
                'failed',
                name='page_extraction_status',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            'page_number >= 1', name=op.f('ck_tender_document_pages_page_number_is_one_based')
        ),
        sa.ForeignKeyConstraint(
            ['extraction_id'],
            ['tender_document_extractions.id'],
            name=op.f('fk_tender_document_pages_extraction_id_tender_document_extractions'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tender_document_pages')),
        sa.UniqueConstraint(
            'extraction_id', 'page_number', name='uq_tender_document_pages_extraction_page'
        ),
    )
    op.create_index(
        'ix_tender_document_pages_extraction_id',
        'tender_document_pages',
        ['extraction_id'],
        unique=False,
    )

    op.create_table(
        'tender_document_files',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('tender_document_id', sa.Uuid(), nullable=False),
        sa.Column('content_id', sa.Uuid(), nullable=True),
        sa.Column('source_record_id', sa.Uuid(), nullable=True),
        sa.Column('source_url', sa.String(length=1000), nullable=False),
        sa.Column('final_url', sa.String(length=1000), nullable=True),
        sa.Column('original_filename', sa.String(length=255), nullable=True),
        sa.Column('detected_filename', sa.String(length=255), nullable=True),
        sa.Column('declared_mime_type', sa.String(length=160), nullable=True),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('is_current', sa.Boolean(), nullable=False),
        sa.Column(
            'download_status',
            sa.Enum(
                'pending',
                'downloading',
                'downloaded',
                'failed',
                name='download_status',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column('http_status', sa.Integer(), nullable=True),
        sa.Column('redirect_count', sa.Integer(), nullable=False),
        sa.Column('download_ms', sa.Integer(), nullable=True),
        sa.Column('downloaded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'started_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column('last_checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_code', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['content_id'],
            ['tender_document_contents.id'],
            name=op.f('fk_tender_document_files_content_id_tender_document_contents'),
            ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['source_record_id'],
            ['source_records.id'],
            name=op.f('fk_tender_document_files_source_record_id_source_records'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['tender_document_id'],
            ['tender_documents.id'],
            name=op.f('fk_tender_document_files_tender_document_id_tender_documents'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tender_document_files')),
        sa.UniqueConstraint(
            'tender_document_id',
            'version_number',
            name='uq_tender_document_files_document_version',
        ),
    )
    op.create_index(
        'ix_tender_document_files_content_id',
        'tender_document_files',
        ['content_id'],
        unique=False,
    )
    op.create_index(
        'ix_tender_document_files_tender_document_id',
        'tender_document_files',
        ['tender_document_id'],
        unique=False,
    )
    # Index unic parcial: fa de lock de concurrencia. Nomes un processat en curs
    # per document; el segon request rep un 409 en comptes de duplicar la feina.
    op.create_index(
        'uq_tender_document_files_in_flight',
        'tender_document_files',
        ['tender_document_id'],
        unique=True,
        postgresql_where=sa.text("download_status IN ('pending', 'downloading')"),
        sqlite_where=sa.text("download_status IN ('pending', 'downloading')"),
    )


def downgrade() -> None:
    op.drop_table('tender_document_pages')
    op.drop_table('tender_document_files')
    op.drop_table('tender_document_extractions')
    op.drop_table('tender_document_contents')
