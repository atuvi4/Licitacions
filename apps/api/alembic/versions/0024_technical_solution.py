"""Com oferirem cada requisit: la baula que faltava entre complir i acreditar.

El sistema ja sabia respondre "podem complir-ho?" (`company_requirement_assessments`)
i "amb quin paper ho acreditem?" (`workspace_evidence_items`). No sabia respondre
la pregunta que hi ha entremig i que es la feina real despres d'un GO: **com ho
oferirem concretament**. Amb quin equip, de quin proveidor, qui se n'encarrega i
si algu ho ha donat per bo.

Son preguntes diferents i per aixo es una taula diferent. Una empresa pot complir
de sobres un requisit i no haver decidit encara com el resoldra, i pot tenir la
solucio claríssima i no complir-lo. Si visquessin a la mateixa fila, una de les
dues respostes taparia l'altra.

`status = validated` NOMES l'escriu una persona. Queda lligat al requisit i al
contingut concrets amb dues empremtes SHA-256: si el plec canvia el que demana o
algu reescriu la proposta, la validacio deixa de valer sense haver d'esborrar
res, que es el que permet llegir despres que hi va haver una validacio i per que
va deixar de servir. Es el mateix mecanisme que `relevance_*` al dossier.

Els camps de validacio son nullable a proposit: mentre la solucio es un
esborrany no en te cap, i una restriccio d'esquema impediria l'estat normal de
treball. Que una fila `validated` porti signatura, data, motiu i les dues
empremtes ho garanteix el servei (S3).

`supplier_name` i `product_name` son text i no claus foranes: el cataleg de
productes i proveidors queda fora de l'MVP, i una FK obligaria a inventar
entitats nomes per poder escriure un nom.

Revision ID: 0024_technical_solution
Revises: 0023_analysis_quotes
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0024_technical_solution'
down_revision: str | None = '0023_analysis_quotes'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'workspace_technical_solutions'
_STATUSES = ('not_started', 'drafting', 'needs_validation', 'validated')
_SOURCES = ('requirement', 'manual')
#: Les files manuals porten `requirement_id` NULL i no entren a la unicitat:
#: a SQL NULL no es igual a NULL, aixi que no xoquen mai entre elles.
_FROM_REQUIREMENT = 'requirement_id IS NOT NULL'


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('workspace_id', sa.Uuid(), nullable=False),
        sa.Column('requirement_id', sa.Uuid(), nullable=True),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('proposed_solution', sa.Text(), nullable=True),
        sa.Column('supplier_name', sa.String(length=300), nullable=True),
        sa.Column('product_name', sa.String(length=300), nullable=True),
        sa.Column('owner_id', sa.Uuid(), nullable=True),
        sa.Column(
            'status',
            sa.Enum(*_STATUSES, name='technical_solution_status', native_enum=False),
            nullable=False,
        ),
        sa.Column(
            'source',
            sa.Enum(*_SOURCES, name='technical_solution_source', native_enum=False),
            nullable=False,
        ),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('validated_by', sa.Uuid(), nullable=True),
        sa.Column('validated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('validated_note', sa.Text(), nullable=True),
        sa.Column('validated_requirement_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('validated_content_fingerprint', sa.String(length=64), nullable=True),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('updated_by', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ['company_id'], ['companies.id'],
            name='fk_workspace_technical_solutions_company_id_companies',
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['bid_workspaces.id'],
            name='fk_workspace_technical_solutions_workspace_id_bid_workspaces',
            ondelete='CASCADE',
        ),
        # La solucio sobreviu a una extraccio nova: el que es perd es el vincle,
        # no la feina escrita.
        sa.ForeignKeyConstraint(
            ['requirement_id'], ['requirements.id'],
            name='fk_workspace_technical_solutions_requirement_id_requirements',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['owner_id'], ['users.id'],
            name='fk_workspace_technical_solutions_owner_id_users',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['validated_by'], ['users.id'],
            name='fk_workspace_technical_solutions_validated_by_users',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'],
            name='fk_workspace_technical_solutions_created_by_users',
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['updated_by'], ['users.id'],
            name='fk_workspace_technical_solutions_updated_by_users',
            ondelete='SET NULL',
        ),
        # Els valors dels enums els ha d'imposar la base. `sa.Enum(native_enum=False)`
        # nomes genera el VARCHAR: el CHECK, SQLAlchemy nomes el crea amb
        # `create_constraint=True`. Aqui son explicits, amb `op.f()` perque el nom
        # sigui exactament aquest i no depengui de cap convencio.
        sa.CheckConstraint(
            "status IN ({})".format(", ".join(f"'{value}'" for value in _STATUSES)),
            name=op.f('ck_workspace_technical_solutions_status_valid'),
        ),
        sa.CheckConstraint(
            "source IN ({})".format(", ".join(f"'{value}'" for value in _SOURCES)),
            name=op.f('ck_workspace_technical_solutions_source_valid'),
        ),
        sa.PrimaryKeyConstraint('id', name='pk_workspace_technical_solutions'),
    )
    op.create_index('ix_workspace_technical_solutions_company_id', _TABLE, ['company_id'])
    op.create_index('ix_workspace_technical_solutions_workspace', _TABLE, ['workspace_id'])
    op.create_index('ix_workspace_technical_solutions_owner', _TABLE, ['owner_id'])
    # Una solucio per requisit i candidatura: es el que fa que sincronitzar dues
    # vegades no dupliqui res.
    op.create_index(
        'uq_workspace_technical_solutions_requirement',
        _TABLE,
        ['workspace_id', 'requirement_id'],
        unique=True,
        postgresql_where=sa.text(_FROM_REQUIREMENT),
        sqlite_where=sa.text(_FROM_REQUIREMENT),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_workspace_technical_solutions_requirement',
        table_name=_TABLE,
        postgresql_where=sa.text(_FROM_REQUIREMENT),
        sqlite_where=sa.text(_FROM_REQUIREMENT),
    )
    op.drop_index('ix_workspace_technical_solutions_owner', table_name=_TABLE)
    op.drop_index('ix_workspace_technical_solutions_workspace', table_name=_TABLE)
    op.drop_index('ix_workspace_technical_solutions_company_id', table_name=_TABLE)
    op.drop_table(_TABLE)
