"""Requisits escrits per una persona (S3-S5).

El problema que resol: `RequirementExtractionRun` modela una EXECUCIO
d'analisi —proveidor, model, prompt, tokens, cost— i una persona que escriu un
requisit no en te cap. El seu `input_fingerprint` es la identitat del corpus
documental d'entrada d'una extraccio real; omplir-lo per un requisit manual el
falsejaria i faria que tot el que s'hi recolza digues una cosa que no ha passat
(D-09).

Per tant `run_id` passa a ser nul·lable i s'hi afegeix `origin`, amb un CHECK
que lliga les dues coses: un requisit EXTRET te run i un de MANUAL no en te.
Ho garanteix la base de dades i no nomes el servei, perque el dia que algu
escrigui una fila per SQL cru el conjunt efectiu no pot comptar-la dues
vegades.

`archived_at` nomes te sentit per als manuals: un d'extret surt del conjunt
efectiu quan la seva run deixa de ser vigent, i no li cal cap marca. I no hi ha
cap taula d'historial (D-08): un requisit manual es mutable i el rastre son
`created_by`, `updated_by`, `updated_at` i l'auditoria.

**Backfill honest.** Les files existents passen a `origin='extracted'`, que es
el que son: totes venen d'una run. `created_by` i `updated_by` queden a NULL
perque no sabem qui les va crear i inventar-ho seria pitjor que no dir-ho. El
`server_default` d'`origin` nomes serveix per omplir-les i es retira tot seguit:
deixar-lo permanent faria que una fila nova pogues dir-se extreta sense ser-ho.

**El downgrade perd dades, i ho ha de fer.** 0030 no sap representar un
requisit sense run: no hi ha cap manera honesta de conservar-los. S'esborren
els manuals i els seus enllacos de provinenca ABANS de tornar `run_id` a NOT
NULL. L'alternativa —inventar-los una `RequirementExtractionRun`— seria
exactament la mentida que aquesta migracio existeix per evitar. Els extrets no
es toquen.

Revision ID: 0031_manual_requirements
Revises: 0030_tender_source_refs
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0031_manual_requirements'
down_revision: str | None = '0030_tender_source_refs'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REQUIREMENTS = 'requirements'
_LINKS = 'requirement_source_ref_links'

#: VARCHAR + CHECK, com tots els enums del projecte. El helper `enum_column` no
#: genera CHECK de valors —deute conegut—, aixi que el de sota, que lliga
#: `origin` amb `run_id`, es qui acota tambe els valors possibles.
_ORIGIN = sa.Enum('extracted', 'manual', name='requirement_origin', native_enum=False)

_ORIGIN_MATCHES_RUN = (
    "(origin = 'extracted' AND run_id IS NOT NULL)"
    " OR (origin = 'manual' AND run_id IS NULL)"
)


def upgrade() -> None:
    op.add_column(
        _REQUIREMENTS,
        sa.Column('origin', _ORIGIN, nullable=False, server_default='extracted'),
    )
    # El default de servidor nomes omplia les files que ja hi havia: a partir
    # d'ara, qui insereix ha de dir qui ha escrit el requisit.
    op.alter_column(_REQUIREMENTS, 'origin', server_default=None)
    op.add_column(
        _REQUIREMENTS, sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(_REQUIREMENTS, sa.Column('created_by', sa.Uuid(), nullable=True))
    op.add_column(_REQUIREMENTS, sa.Column('updated_by', sa.Uuid(), nullable=True))
    op.create_foreign_key(
        'fk_requirements_created_by', _REQUIREMENTS, 'users', ['created_by'], ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_requirements_updated_by', _REQUIREMENTS, 'users', ['updated_by'], ['id'],
        ondelete='SET NULL',
    )

    op.alter_column(_REQUIREMENTS, 'run_id', existing_type=sa.Uuid(), nullable=True)
    op.create_check_constraint(
        'requirement_origin_matches_run', _REQUIREMENTS, _ORIGIN_MATCHES_RUN
    )
    # Llegir el conjunt efectiu es la consulta mes freqüent que hi haura:
    # "els manuals vius d'aquesta licitacio".
    op.create_index(
        'ix_requirements_origin_archived',
        _REQUIREMENTS,
        ['tender_id', 'origin', 'archived_at'],
    )

    op.create_table(
        _LINKS,
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('requirement_id', sa.Uuid(), nullable=False),
        sa.Column('source_ref_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(
            ['requirement_id'], ['requirements.id'],
            name='fk_requirement_source_ref_requirement', ondelete='CASCADE',
        ),
        # RESTRICT: una font citada no s'ha de poder fer desapareixer sense
        # adonar-se'n, igual que a tota la cadena de provinenca.
        sa.ForeignKeyConstraint(
            ['source_ref_id'], ['tender_source_refs.id'],
            name='fk_requirement_source_ref_source', ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['created_by'], ['users.id'],
            name='fk_requirement_source_ref_author', ondelete='SET NULL',
        ),
        sa.UniqueConstraint('requirement_id', 'source_ref_id', name='uq_requirement_source_ref'),
    )
    op.create_index('ix_requirement_source_ref_links_requirement', _LINKS, ['requirement_id'])
    op.create_index('ix_requirement_source_ref_links_source_ref', _LINKS, ['source_ref_id'])


def downgrade() -> None:
    # PRIMER les dades, i en aquest ordre: els enllacos mentre la taula encara
    # existeix, i els requisits manuals abans de tornar `run_id` a NOT NULL.
    # 0030 no sap representar un requisit sense run, i inventar-li una
    # `RequirementExtractionRun` seria la mentida que 0031 existeix per evitar.
    # Es perdua de dades, i es deliberada.
    op.execute(
        sa.text(
            "DELETE FROM requirement_source_ref_links WHERE requirement_id IN "
            "(SELECT id FROM requirements WHERE origin = 'manual')"
        )
    )
    op.execute(sa.text("DELETE FROM requirements WHERE origin = 'manual'"))

    op.drop_index('ix_requirement_source_ref_links_source_ref', table_name=_LINKS)
    op.drop_index('ix_requirement_source_ref_links_requirement', table_name=_LINKS)
    op.drop_table(_LINKS)

    op.drop_index('ix_requirements_origin_archived', table_name=_REQUIREMENTS)
    op.drop_constraint('ck_requirements_requirement_origin_matches_run', _REQUIREMENTS)
    op.alter_column(_REQUIREMENTS, 'run_id', existing_type=sa.Uuid(), nullable=False)

    op.drop_constraint('fk_requirements_updated_by', _REQUIREMENTS, type_='foreignkey')
    op.drop_constraint('fk_requirements_created_by', _REQUIREMENTS, type_='foreignkey')
    op.drop_column(_REQUIREMENTS, 'updated_by')
    op.drop_column(_REQUIREMENTS, 'created_by')
    op.drop_column(_REQUIREMENTS, 'archived_at')
    op.drop_column(_REQUIREMENTS, 'origin')
