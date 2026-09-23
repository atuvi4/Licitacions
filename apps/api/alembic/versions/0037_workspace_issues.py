"""Dubtes i discrepancies (S3-S10).

Dues taules. Un **issue** es coneixement que falta —una pregunta sense resposta,
dues fonts que es contradiuen, una dada que no te ningu—, i no feina per fer:
aixo ja es `workspace_tasks`. Una tasca es tanca fent-la; un dubte es tanca
sabent alguna cosa que abans no se sabia.

**Cap issue neix sol.** La base no en genera, i el servei tampoc: els avisos
automatics ja viuen a `FinalReadiness`. Omplir aquesta taula de preguntes que
ningu no s'ha fet hi enterraria les que si.

**La provinenca va a `tender_source_refs`**, per `workspace_issue_source_refs`.
El disseny antic amb un `source_refs_json` queda superat: un JSON
d'identificadors no pot garantir que la cita existeixi ni impedir que algu
l'esborri sense adonar-se'n. `RESTRICT` cap a la cita i `CASCADE` cap al dubte,
igual que `requirement_source_ref_links`.

**`blocks_final_review` neix a `false`** i nomes el canvia `DECISION_ROLES`: que
una pregunta impedeixi presentar-se es una decisio d'oferta, no una propietat de
la pregunta.

**El CHECK de la resolucio** fa que "resolt" impliqui resposta escrita. Sense
ell, resolt seria un estat mes i la resposta es podria perdre. Descartar, en
canvi, NO omple `resolved_by`/`resolved_at`: descartar no es resoldre.

Les dues taules neixen buides: cap backfill. El downgrade les esborra sense
tocar ni les cites, ni les dimensions, ni les opcions, ni les linies.

Revision ID: 0037_workspace_issues
Revises: 0036_offer_line_target_markup
Create Date: 2026-09-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0037_workspace_issues'
down_revision: str | None = '0036_offer_line_target_markup'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ISSUES = 'workspace_issues'
_REFS = 'workspace_issue_source_refs'

_TYPES = (
    'internal_question',
    'authority_question',
    'supplier_question',
    'contradiction',
    'missing_information',
)
_STATUSES = ('open', 'waiting', 'resolved', 'dismissed')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def upgrade() -> None:
    op.create_table(
        _ISSUES,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column(
            'issue_type',
            sa.Enum(*_TYPES, name='issue_type', native_enum=False),
            nullable=False,
        ),
        sa.Column(
            'status',
            sa.Enum(*_STATUSES, name='issue_status', native_enum=False),
            nullable=False,
        ),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        # De que parla el dubte. Totes SET NULL: si l'entitat desapareix, la
        # pregunta i la seva resposta continuen sent certes.
        sa.Column('requirement_id', sa.Uuid(), nullable=True),
        sa.Column('award_criterion_id', sa.Uuid(), nullable=True),
        sa.Column('technical_solution_id', sa.Uuid(), nullable=True),
        sa.Column('solution_option_id', sa.Uuid(), nullable=True),
        sa.Column('economic_offer_line_id', sa.Uuid(), nullable=True),
        sa.Column('dimension_id', sa.Uuid(), nullable=True),
        sa.Column('supplier_name', sa.String(length=300), nullable=True),
        sa.Column('owner_id', sa.Uuid(), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolution', sa.Text(), nullable=True),
        sa.Column('resolution_source', sa.Text(), nullable=True),
        sa.Column('resolved_by', sa.Uuid(), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'blocks_final_review',
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ['requirement_id'], ['requirements.id'],
            name='fk_issue_requirement', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['award_criterion_id'], ['award_criteria.id'],
            name='fk_issue_criterion', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['technical_solution_id'], ['workspace_technical_solutions.id'],
            name='fk_issue_technical_solution', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['solution_option_id'], ['workspace_solution_options.id'],
            name='fk_issue_option', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['economic_offer_line_id'], ['economic_offer_lines.id'],
            name='fk_issue_offer_line', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['dimension_id'], ['workspace_dimensions.id'],
            name='fk_issue_dimension', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['owner_id'], ['users.id'], name='fk_issue_owner', ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['resolved_by'], ['users.id'], name='fk_issue_resolver', ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id'], ondelete='SET NULL'),
        sa.CheckConstraint(_in_list('issue_type', _TYPES), name='issue_type_valid'),
        sa.CheckConstraint(_in_list('status', _STATUSES), name='issue_status_valid'),
        sa.CheckConstraint(
            "status <> 'resolved' OR resolution IS NOT NULL",
            name='resolved_has_resolution',
        ),
    )
    op.create_index(op.f('ix_workspace_issues_company_id'), _ISSUES, ['company_id'])
    op.create_index('ix_workspace_issues_status', _ISSUES, ['workspace_id', 'status'])
    op.create_index('ix_workspace_issues_type', _ISSUES, ['workspace_id', 'issue_type'])

    op.create_table(
        _REFS,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('issue_id', sa.Uuid(), nullable=False),
        sa.Column('source_ref_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['issue_id'], ['workspace_issues.id'],
            name='fk_issue_ref_issue', ondelete='CASCADE',
        ),
        # RESTRICT com a tota la cadena de provinenca: una font citada no s'ha
        # de poder fer desapareixer sense adonar-se'n.
        sa.ForeignKeyConstraint(
            ['source_ref_id'], ['tender_source_refs.id'],
            name='fk_issue_ref_source', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'], name='fk_issue_ref_author', ondelete='SET NULL'
        ),
        sa.UniqueConstraint('issue_id', 'source_ref_id', name='uq_workspace_issue_source_ref'),
    )
    op.create_index('ix_workspace_issue_source_refs_issue', _REFS, ['issue_id'])
    op.create_index('ix_workspace_issue_source_refs_ref', _REFS, ['source_ref_id'])


def downgrade() -> None:
    # Els enllacos primer: si no, la taula filla es quedaria apuntant a una que
    # ja no hi es. Es perd nomes la feature d'aquesta fase; les cites, les
    # dimensions, les opcions i les linies no es toquen.
    op.drop_index('ix_workspace_issue_source_refs_ref', table_name=_REFS)
    op.drop_index('ix_workspace_issue_source_refs_issue', table_name=_REFS)
    op.drop_table(_REFS)
    op.drop_index('ix_workspace_issues_type', table_name=_ISSUES)
    op.drop_index('ix_workspace_issues_status', table_name=_ISSUES)
    op.drop_index(op.f('ix_workspace_issues_company_id'), table_name=_ISSUES)
    op.drop_table(_ISSUES)
