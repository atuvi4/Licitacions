"""Extraccions immutables: cada passada de parser es una revisio nova.

Fins ara `UNIQUE (content_id, parser_version)` obligava a reutilitzar la fila i
a substituir-ne les pagines quan es reextreia amb el mateix parser. Aixo fa que
una cita a una pagina pugui canviar de contingut a posteriori, cosa que la fase
de requisits no es pot permetre: una evidencia citada ha de ser estable.

A partir d'aqui cada execucio real crea una fila nova amb el seu
`revision_number`, monoton per `content_id` i independent del parser. Les
pagines antigues no es toquen mai.

El backfill numera les extraccions existents per ordre de creacio dins de cada
contingut, de manera que un contingut amb dues `parser_version` no acabi amb
dues revisions 1.

Revision ID: 0006_extraction_rev
Revises: 0005_tender_docs
Create Date: 2026-09-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0006_extraction_rev'
down_revision: str | None = '0005_tender_docs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'tender_document_extractions',
        sa.Column('revision_number', sa.Integer(), nullable=True),
    )
    # Numeracio estable per contingut: la mes antiga es la revisio 1.
    op.execute(
        """
        UPDATE tender_document_extractions AS e
        SET revision_number = ordered.rn
        FROM (
            SELECT id,
                   row_number() OVER (PARTITION BY content_id ORDER BY created_at, id) AS rn
            FROM tender_document_extractions
        ) AS ordered
        WHERE e.id = ordered.id
        """
    )
    op.alter_column('tender_document_extractions', 'revision_number', nullable=False)

    op.drop_constraint(
        'uq_tender_document_extractions_content_parser',
        'tender_document_extractions',
        type_='unique',
    )
    op.create_unique_constraint(
        'uq_tender_document_extractions_content_revision',
        'tender_document_extractions',
        ['content_id', 'revision_number'],
    )


def downgrade() -> None:
    # Tornar enrere nomes es possible si cap contingut te mes d'una revisio per
    # parser: si n'hi ha, la restriccio antiga es inaplicable i cal decidir a ma
    # quina extraccio es conserva.
    duplicates = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM (SELECT content_id, parser_version "
            "FROM tender_document_extractions "
            "GROUP BY content_id, parser_version HAVING count(*) > 1) AS d"
        )
    ).scalar()
    if duplicates:
        raise RuntimeError(
            f"Hi ha {duplicates} combinacions (content_id, parser_version) amb mes d'una "
            "revisio. Restaurar la restriccio antiga destruiria evidencia: cal resoldre-ho "
            "a ma abans de fer el downgrade."
        )

    op.drop_constraint(
        'uq_tender_document_extractions_content_revision',
        'tender_document_extractions',
        type_='unique',
    )
    op.create_unique_constraint(
        'uq_tender_document_extractions_content_parser',
        'tender_document_extractions',
        ['content_id', 'parser_version'],
    )
    op.drop_column('tender_document_extractions', 'revision_number')
