"""Historia d'una licitacio: revisions, canvis i sobre que es va decidir.

Fins ara una licitacio era una fotografia que s'anava sobreescrivint. Amb aixo,
quan l'organ publica una modificacio nomes hi havia dues sortides, i totes dues
dolentes: invalidar-ho tot a cada reingestió —i llavors ningu es creuria cap
avis— o no invalidar res, i llavors algu prepararia una oferta contra un plec
que ja no existeix.

`tender_revisions` guarda l'estat normalitzat sencer amb el seu hash. El hash es
el que fa que reingerir la mateixa entrada no generi res: mateix contingut,
mateixa revisio, cap event i cap avis. Es calcula sobre l'estat NORMALITZAT i no
sobre el XML de la font, de manera que un canvi de format que no altera cap dada
no desperta mig sistema.

`tender_change_events` guarda cada canvi per separat, amb els seus impactes
desats i no deduits. Si algun dia canviem la matriu d'impacte, els events antics
han de seguir explicant per que en aquell moment es va invalidar el que es va
invalidar.

`workspace_decisions.tender_revision_id` diu sobre quin estat es va decidir. Es
nullable perque les decisions que ja existeixen es van prendre abans que hi
hagues revisions, i inventar-los-en una seria escriure historia falsa.

Cap taula porta `company_id`: la historia d'un expedient es publica, com
l'expedient. Nomes l'enllaç des de la decisio es de tenant, i aquella taula ja
ho es.

Revision ID: 0016_tender_lifecycle
Revises: 0015_extraction_qa
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0016_tender_lifecycle'
down_revision: str | None = '0015_extraction_qa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'tender_revisions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('revision_number', sa.Integer(), nullable=False),
        sa.Column('source_record_id', sa.Uuid(), nullable=True),
        sa.Column('normalized_snapshot', sa.JSON(), nullable=False),
        sa.Column('normalized_hash', sa.String(length=64), nullable=False),
        sa.Column('change_summary', sa.JSON(), nullable=True),
        sa.Column('trigger', sa.String(length=40), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint('revision_number >= 1', name='tender_revision_is_one_based'),
        sa.ForeignKeyConstraint(
            ['tender_id'], ['tenders.id'], name='fk_tender_revision_tender', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['source_record_id'],
            ['source_records.id'],
            name='fk_tender_revision_source_record',
            ondelete='SET NULL',
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'tender_id', 'revision_number', name='uq_tender_revisions_tender_number'
        ),
    )
    op.create_index('ix_tender_revisions_tender_id', 'tender_revisions', ['tender_id'])
    op.create_index('ix_tender_revisions_hash', 'tender_revisions', ['normalized_hash'])

    op.create_table(
        'tender_change_events',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('tender_id', sa.Uuid(), nullable=False),
        sa.Column('from_revision_id', sa.Uuid(), nullable=True),
        sa.Column('to_revision_id', sa.Uuid(), nullable=False),
        sa.Column('change_code', sa.String(length=40), nullable=False),
        sa.Column('subject', sa.String(length=500), nullable=True),
        sa.Column('old_value', sa.JSON(), nullable=True),
        sa.Column('new_value', sa.JSON(), nullable=True),
        sa.Column('impacts', sa.JSON(), nullable=False),
        sa.Column('is_material', sa.Boolean(), nullable=False),
        sa.Column(
            'detected_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['tender_id'], ['tenders.id'], name='fk_change_event_tender', ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['from_revision_id'],
            ['tender_revisions.id'],
            name='fk_change_event_from_revision',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['to_revision_id'],
            ['tender_revisions.id'],
            name='fk_change_event_to_revision',
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tender_change_events_tender_id', 'tender_change_events', ['tender_id'])
    op.create_index(
        'ix_tender_change_events_to_revision_id', 'tender_change_events', ['to_revision_id']
    )
    op.create_index('ix_tender_change_events_code', 'tender_change_events', ['change_code'])

    op.add_column(
        'workspace_decisions', sa.Column('tender_revision_id', sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        'fk_workspace_decision_tender_revision',
        'workspace_decisions',
        'tender_revisions',
        ['tender_revision_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(
        'fk_workspace_decision_tender_revision', 'workspace_decisions', type_='foreignkey'
    )
    op.drop_column('workspace_decisions', 'tender_revision_id')
    op.drop_index('ix_tender_change_events_code', table_name='tender_change_events')
    op.drop_index('ix_tender_change_events_to_revision_id', table_name='tender_change_events')
    op.drop_index('ix_tender_change_events_tender_id', table_name='tender_change_events')
    op.drop_table('tender_change_events')
    op.drop_index('ix_tender_revisions_hash', table_name='tender_revisions')
    op.drop_index('ix_tender_revisions_tender_id', table_name='tender_revisions')
    op.drop_table('tender_revisions')
