"""De quines magnituds depen una opcio (S3-S7b).

Una taula pont i prou. Una opcio de servei mobil depen del nombre de linies,
del de terminals i del de SIM de dades; i el nombre de linies el fan servir
totes les opcions que s'hi comparen. Per aixo es **N:N** i no un camp a cap de
les dues taules.

**Nomes la parella.** Cap `quantity_factor`, cap `role`, cap
`snapshot_value_num` i cap `snapshot_taken_at`. La relacio diu "aquesta opcio
depen d'aquesta magnitud" i res mes; l'estat d'ara el diu `workspace_dimensions`,
que n'es l'unica font de veritat. Guardar-ne una copia aqui voldria dir tenir
dues respostes a la mateixa pregunta, i un dia no coincidirien.

**Divergencia deliberada amb el Design Freeze §16**, autoritzada per la fase:
alli `dimension_id` era `RESTRICT` per no deixar esborrar una magnitud de que
depen una opcio. Aqui es `CASCADE`. El producte no esborra cap magnitud —les
arxiva—, de manera que aquella proteccio no arribaria a actuar mai; en canvi,
amb `RESTRICT` la destruccio d'un tenant fallaria i deixaria files que ningu no
pot netejar. El §16 tambe preveia `role`, `quantity_factor` i el snapshot: tot
aixo queda ajornat, no descartat.

La taula neix buida: cap backfill, cap dependencia deduida de res. El downgrade
l'esborra i no toca ni les opcions ni les dimensions.

L'identificador de revisio es curt a proposit: `alembic_version.version_num`
es un `VARCHAR(32)`, i un de mes llarg fa que la migracio no es pugui aplicar a
PostgreSQL. A SQLite hi passaria sense dir res.

Revision ID: 0034_solution_option_dimensions
Revises: 0033_workspace_solution_options
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0034_solution_option_dimensions'
down_revision: str | None = '0033_workspace_solution_options'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'workspace_solution_option_dimensions'


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('solution_option_id', sa.Uuid(), nullable=False),
        sa.Column('dimension_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        # CASCADE als dos costats: una dependencia sense cap dels seus dos
        # extrems no li serveix a ningu. Arxivar, en canvi, no esborra res.
        sa.ForeignKeyConstraint(
            ['solution_option_id'], ['workspace_solution_options.id'],
            name='fk_solution_option_dimension_option', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['dimension_id'], ['workspace_dimensions.id'],
            name='fk_solution_option_dimension_dimension', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'],
            name='fk_solution_option_dimension_author', ondelete='SET NULL',
        ),
        # La mateixa opcio no depen dues vegades de la mateixa magnitud.
        sa.UniqueConstraint(
            'solution_option_id', 'dimension_id', name='uq_solution_option_dimension'
        ),
    )
    op.create_index('ix_solution_option_dimensions_option', _TABLE, ['solution_option_id'])
    op.create_index('ix_solution_option_dimensions_dimension', _TABLE, ['dimension_id'])


def downgrade() -> None:
    # Nomes es perden les dependencies declarades en aquesta fase. Ni les
    # opcions ni les dimensions es toquen: totes dues existien abans i totes
    # dues continuen sent completes sense aquesta taula.
    op.drop_index('ix_solution_option_dimensions_dimension', table_name=_TABLE)
    op.drop_index('ix_solution_option_dimensions_option', table_name=_TABLE)
    op.drop_table(_TABLE)
