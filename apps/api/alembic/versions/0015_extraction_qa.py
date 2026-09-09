"""Qualitat del text extret, lligada a la revisio que el va produir.

Fins ara nomes es guardava si una extraccio tenia poc text. Aixo no distingeix
el cas que importa: una pagina que anuncia "se aplicara la siguiente formula:" i
on la formula, al PDF, es una imatge. El text es abundant i tot i aixi hi falta
la peça que decideix un criteri d'adjudicacio.

Sense aquest senyal, el benchmark hauria comptat com a error del model una cosa
que el model no podia extreure. Distingir "el model ho ha fet malament" de "la
font ja venia incompleta" es tot el motiu d'aquesta taula.

La qualitat penja de `tender_document_extractions` i no de `tender_documents`
perque **es una propietat de com es va llegir**, no del document. Una
re-extraccio amb un parser millor crea una revisio nova amb els seus propis
issues i no toca els d'abans: es el que despres permet dir si l'extraccio ha
millorat.

`qa_status` i `qa_version` son nullable: les extraccions que ja existeixen no
s'han avaluat mai amb aquestes heuristiques, i posar-los `good` per defecte
seria afirmar una cosa que ningu ha comprovat.

Revision ID: 0015_extraction_qa
Revises: 0014_alt_group_complete
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0015_extraction_qa'
down_revision: str | None = '0014_alt_group_complete'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'tender_document_extractions', sa.Column('qa_status', sa.String(length=10), nullable=True)
    )
    op.add_column(
        'tender_document_extractions', sa.Column('qa_version', sa.String(length=40), nullable=True)
    )

    op.create_table(
        'tender_extraction_issues',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('extraction_id', sa.Uuid(), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=True),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('detail', sa.JSON(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            'page_number IS NULL OR page_number >= 1', name='issue_page_is_one_based'
        ),
        sa.ForeignKeyConstraint(
            ['extraction_id'],
            ['tender_document_extractions.id'],
            name='fk_extraction_issue_extraction',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_tender_extraction_issues_extraction_id',
        'tender_extraction_issues',
        ['extraction_id'],
    )
    op.create_index('ix_tender_extraction_issues_code', 'tender_extraction_issues', ['code'])

    # El run de requisits ha de poder dir amb quina qualitat de font es va fer.
    #
    # Es un estat de quatre valors i no un booleà a proposit. Amb un booleà,
    # "no s'ha avaluat la qualitat" i "s'ha avaluat i no hi ha res a dir"
    # comparteixen el valor `false`, i son coses oposades: la primera no
    # autoritza cap conclusio i la segona si. Aquesta ambiguitat acabaria
    # atribuint al model errors que ningu ha comprovat que ho siguin.
    #
    # El valor per defecte de les files existents es `unknown`, que es la
    # veritat sobre elles: es van fer abans que la QA existis.
    op.add_column(
        'requirement_extraction_runs',
        sa.Column(
            'source_quality_status', sa.String(length=10), nullable=False, server_default='unknown'
        ),
    )
    op.alter_column('requirement_extraction_runs', 'source_quality_status', server_default=None)
    op.add_column(
        'requirement_extraction_runs', sa.Column('source_quality_issues', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('requirement_extraction_runs', 'source_quality_issues')
    op.drop_column('requirement_extraction_runs', 'source_quality_status')
    op.drop_index('ix_tender_extraction_issues_code', table_name='tender_extraction_issues')
    op.drop_index(
        'ix_tender_extraction_issues_extraction_id', table_name='tender_extraction_issues'
    )
    op.drop_table('tender_extraction_issues')
    op.drop_column('tender_document_extractions', 'qa_version')
    op.drop_column('tender_document_extractions', 'qa_status')
