"""Opcions de solucio, proveidors i cotitzacions (S3-S7).

Una taula i una idea: una **opcio** es una via possible —Orange o Sewan, Yealink
o Jabra— amb el seu proveidor, la seva configuracio, el seu cost i l'estat de la
seva cotitzacio. Quan una persona en tria una, queda escrit qui i quan; quan en
descarta una altra, queda escrit per que.

**No hi ha `unit_price`.** El preu de venda viu nomes a `economic_offer_lines`, i
aquesta migracio no la toca. Tenir el preu a dues taules voldria dir que un dia
dirien coses diferents i ningu no sabria quina mana. Aqui nomes hi ha cost.

**`Numeric`, mai `Float`.** `quantity` amb escala 4 i `unit_cost_ex_vat` amb
escala 6: un cost unitari pot portar centims de centim, i arrodonir-lo aqui es
multiplicaria per la quantitat mes avall.

**Els CHECK dels enums son explicits.** `enum_column()` es materialitza com un
VARCHAR i SQLAlchemy nomes en crea el CHECK amb `create_constraint=True`, aixi
que sense aquestes linies un INSERT que no passes per l'ORM hi podria escriure
qualsevol cadena. Es el mateix patro que `workspace_technical_solutions`.

La taula neix buida: cap backfill i cap dada existent tocada. El downgrade
l'esborra —0032 no coneix cap opcio i no hi ha res a preservar-hi que tingui
sentit fora d'aquesta feature.

Revision ID: 0033_workspace_solution_options
Revises: 0032_workspace_dimensions
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0033_workspace_solution_options'
down_revision: str | None = '0032_workspace_dimensions'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'workspace_solution_options'

_QUOTE_STATUS = ('draft', 'requested', 'pending', 'received', 'expired', 'declined')
_SELECTION = ('undecided', 'selected', 'rejected')
_PRICING_BASIS = ('one_off', 'monthly', 'annual', 'per_unit')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('technical_solution_id', sa.Uuid(), nullable=True),
        sa.Column('requirement_id', sa.Uuid(), nullable=True),
        sa.Column('supplier_name', sa.String(length=300), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('quantity', sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column('unit', sa.String(length=50), nullable=True),
        # Economia interna. Mai preu de venda.
        sa.Column('unit_cost_ex_vat', sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column(
            'pricing_basis',
            sa.Enum(*_PRICING_BASIS, name='pricing_basis', native_enum=False),
            nullable=True,
        ),
        sa.Column('duration_months_override', sa.Integer(), nullable=True),
        sa.Column(
            'quote_status',
            sa.Enum(*_QUOTE_STATUS, name='quote_status', native_enum=False),
            nullable=False,
        ),
        sa.Column('quote_date', sa.Date(), nullable=True),
        sa.Column('quote_reference', sa.String(length=200), nullable=True),
        sa.Column('quote_source', sa.String(length=1000), nullable=True),
        sa.Column(
            'selection',
            sa.Enum(*_SELECTION, name='option_selection', native_enum=False),
            nullable=False,
        ),
        sa.Column('selection_reason', sa.Text(), nullable=True),
        sa.Column('selected_by', sa.Uuid(), nullable=True),
        sa.Column('selected_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('internal_note', sa.Text(), nullable=True),
        sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['bid_workspaces.id'], ondelete='CASCADE'),
        # SET NULL a totes tres: si desapareix la solucio tecnica, el requisit o
        # la persona, l'opcio es queda. Que es va cotitzar i a qui continua sent
        # cert encara que el que la va motivar ja no hi sigui.
        sa.ForeignKeyConstraint(
            ['technical_solution_id'], ['workspace_technical_solutions.id'],
            name='fk_solution_options_technical_solution', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['requirement_id'], ['requirements.id'],
            name='fk_solution_options_requirement', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['selected_by'], ['users.id'],
            name='fk_solution_options_selector', ondelete='SET NULL',
        ),
        sa.CheckConstraint(_in_list('quote_status', _QUOTE_STATUS), name='quote_status_valid'),
        sa.CheckConstraint(_in_list('selection', _SELECTION), name='selection_valid'),
        sa.CheckConstraint(
            "selection <> 'rejected' OR selection_reason IS NOT NULL",
            name='rejected_has_reason',
        ),
    )
    op.create_index(
        'ix_workspace_solution_options_workspace', _TABLE, ['workspace_id', 'archived_at']
    )
    op.create_index(
        'ix_workspace_solution_options_solution', _TABLE, ['technical_solution_id']
    )


def downgrade() -> None:
    # Perdua de dades propia d'una feature nova: 0032 no coneix cap opcio i no
    # hi ha res a preservar que tingui sentit fora d'aqui. No es fabrica cap
    # linia economica per "salvar" el cost: seria inventar una oferta.
    op.drop_index('ix_workspace_solution_options_solution', table_name=_TABLE)
    op.drop_index('ix_workspace_solution_options_workspace', table_name=_TABLE)
    op.drop_table(_TABLE)
