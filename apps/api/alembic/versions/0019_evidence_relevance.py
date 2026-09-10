"""Que un document estigui triat no vol dir que acrediti res.

L'auditoria va assignar un titol universitari per acreditar una xifra anual de
negoci i un certificat de bona execucio per acreditar una polissa. El dossier va
dir "Cobert" a totes dues coses i la readiness va passar a "a punt": seleccionar
un document i confirmar que es el document adequat eren la mateixa afirmacio, i
son dues.

Ara la cobertura documental exigeix que la pertinença estigui acreditada —o be
perque el document esta enllaçat a la mateixa entitat corporativa que el
compliment va citar, o be perque una persona ho ha confirmat—. Aquestes columnes
guarden la segona.

Van lligades al **requisit i al document concrets**: `relevance_document_id`
diu sobre quin paper es va signar i `relevance_requirement_fingerprint` sobre
quina exigencia. Substituir el document o canviar materialment el requisit deixa
la confirmacio sense efecte sense haver d'esborrar res, que es el que permet
llegir despres que hi va haver una confirmacio i per que va deixar de valer.

Totes nullable: el que ja existeix es d'abans que hi hagues cap confirmacio, i
NULL vol dir "ningu no ho ha confirmat", que es exactament el que va passar.

Revision ID: 0019_evidence_relevance
Revises: 0018_insurance_actor_fks
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0019_evidence_relevance'
down_revision: str | None = '0018_insurance_actor_fks'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'workspace_evidence_items'


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column('relevance_confirmed_by', sa.Uuid(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column('relevance_confirmed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(_TABLE, sa.Column('relevance_note', sa.Text(), nullable=True))
    op.add_column(_TABLE, sa.Column('relevance_document_id', sa.Uuid(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column('relevance_requirement_fingerprint', sa.String(length=64), nullable=True),
    )
    op.create_foreign_key(
        'fk_workspace_evidence_items_relevance_confirmed_by_users',
        _TABLE,
        'users',
        ['relevance_confirmed_by'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_workspace_evidence_items_relevance_document_id_documents',
        _TABLE,
        'documents',
        ['relevance_document_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_workspace_evidence_items_relevance_document_id_documents',
        _TABLE,
        type_='foreignkey',
    )
    op.drop_constraint(
        'fk_workspace_evidence_items_relevance_confirmed_by_users',
        _TABLE,
        type_='foreignkey',
    )
    op.drop_column(_TABLE, 'relevance_requirement_fingerprint')
    op.drop_column(_TABLE, 'relevance_document_id')
    op.drop_column(_TABLE, 'relevance_note')
    op.drop_column(_TABLE, 'relevance_confirmed_at')
    op.drop_column(_TABLE, 'relevance_confirmed_by')
