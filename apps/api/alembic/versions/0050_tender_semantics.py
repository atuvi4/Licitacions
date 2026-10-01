"""Semantica del plec abans de l'analisi (fase 3A, preparacio de 0051).

El gold de l'Escala (representabilitat de 0048 + 0049) ha ensenyat quatre coses
que el model no sabia dir sense inventar. Aquesta migracio les afegeix i res mes:

* `timing` a `requirements` i `award_criteria` admet `before_formalization`:
  una acreditacio exigida despres de l'adjudicacio i abans de formalitzar
  (assegurança, garantia definitiva). La columna s'eixampla (VARCHAR(13) ->
  VARCHAR(20)) i el CHECK es refa. **Cap fila es reinterpreta**: el que era
  `unknown` continua `unknown`.
* `tender_sites.is_critical` passa a tri-estat: `true` la font ho diu, `false`
  la font diu que no, NULL no se sap. Sense valor per defecte. Les files que ja
  tenien `false` continuen `false`: la migracio no sap si era «no» o «no se sap».
* `tender_sites.code` i `name` nul·lables, amb el CHECK `site_has_identity`
  (almenys un dels dos). UNIQUE(tender_id, code) es queda: dos NULL no
  col·lisionen.
* `requirements.territory_completeness` (`not_applicable` · `complete` ·
  `partial` · `unknown`), `unknown` per a tot el que ja hi ha: 0051 no podra
  llegir mai «cap territori» com a «cobert».
* `award_criterion_source_ref_links`: cites dels criteris d'adjudicacio, amb el
  mateix patro que `requirement_source_ref_links` (RESTRICT cap a la cita).

**Neutralitat de l'upgrade.** Cap INSERT, UPDATE ni DELETE; cap gold.

**Downgrade.** 0049 no pot guardar el que 0050 permet, i el downgrade **no ho
reinterpreta ni ho esborra**: `unknown` no es `false`, una dada coneguda no es
desconeguda i una dada no representable no es una dada eliminada. Abans de tocar
l'esquema comprova que no hi hagi cap fila amb `timing = before_formalization`
(requisits o criteris), cap seu amb `is_critical` NULL ni cap seu sense codi o
sense nom; si n'hi ha, s'atura amb `downgrade_0050_incompatible_rows` i no fa
res. Qui de debo hagi de tornar a 0049 resol aquestes files a ma abans. El que
si que es perd es l'estructura nomes de 0050 (`territory_completeness` i la
taula de cites dels criteris): es la naturalesa de tornar a l'esquema anterior.
Al SQL offline la precondicio hi es com un bloc `DO` que llanca l'excepcio.

Revision ID: 0050_tender_semantics
Revises: 0049_tender_scope
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = '0050_tender_semantics'
down_revision: str | None = '0049_tender_scope'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_TIMING_BEFORE = ('at_submission', 'at_execution', 'ambiguous', 'unknown')
_TIMING = ('at_submission', 'before_formalization', 'at_execution', 'ambiguous', 'unknown')
_COMPLETENESS = ('not_applicable', 'complete', 'partial', 'unknown')
_TIMED_TABLES = ('requirements', 'award_criteria')

#: Files que 0049 no pot representar fidelment: (taula, condicio, que vol dir).
INCOMPATIBLE_WITH_0049 = (
    ('requirements', "timing = 'before_formalization'",
     'requisits amb timing before_formalization'),
    ('award_criteria', "timing = 'before_formalization'",
     'criteris amb timing before_formalization'),
    ('tender_sites', 'is_critical IS NULL', 'seus sense saber si son critiques'),
    ('tender_sites', 'code IS NULL OR name IS NULL', 'seus sense codi o sense nom'),
)
DOWNGRADE_BLOCKED = 'downgrade_0050_incompatible_rows'


class DowngradeBlockedError(RuntimeError):
    """El downgrade s'atura perque hi ha files que 0049 no pot guardar."""

    code = DOWNGRADE_BLOCKED


def incompatible_rows(connection) -> dict[str, int]:
    """Quantes files hi ha de cada cas. Nomes llegeix."""
    return {
        meaning: connection.execute(
            sa.text(f'SELECT count(*) FROM {table} WHERE {condition}')
        ).scalar()
        for table, condition, meaning in INCOMPATIBLE_WITH_0049
    }


def refuse_unless_representable_in_0049(connection) -> None:
    """La precondicio del downgrade: cap fila incompatible, o cap canvi."""
    found = {meaning: n for meaning, n in incompatible_rows(connection).items() if n}
    if found:
        detail = '; '.join(f'{n} {meaning}' for meaning, n in found.items())
        raise DowngradeBlockedError(
            f'{DOWNGRADE_BLOCKED}: {detail}. 0049 no ho pot representar i el downgrade no '
            'reinterpreta ni esborra res: resoleu aquestes files a ma abans de tornar enrere.'
        )


def _offline_guard() -> str:
    """La mateixa precondicio, com a SQL de PostgreSQL per a la revisio offline."""
    checks = '\n'.join(
        f"  SELECT count(*) INTO n FROM {table} WHERE {condition};\n"
        f"  IF n > 0 THEN RAISE EXCEPTION '{DOWNGRADE_BLOCKED} - % {meaning}', n; END IF;"
        for table, condition, meaning in INCOMPATIBLE_WITH_0049
    )
    return f'DO $$\nDECLARE n integer;\nBEGIN\n{checks}\nEND $$'


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def upgrade() -> None:
    # 1. Temporalitat: un valor mes. Cap fila canvia.
    for table in _TIMED_TABLES:
        op.drop_constraint(op.f(f'ck_{table}_timing_valid'), table, type_='check')
        op.alter_column(
            table, 'timing',
            existing_type=_enum(_TIMING_BEFORE, 'requirement_timing'),
            type_=_enum(_TIMING, 'requirement_timing'),
            existing_nullable=False, existing_server_default='unknown',
        )
        op.create_check_constraint('timing_valid', table, _in_list('timing', _TIMING))

    # 2. Seus: critica en tri-estat, i identitat parcial (codi o nom).
    op.alter_column('tender_sites', 'is_critical', existing_type=sa.Boolean(), nullable=True)
    op.alter_column('tender_sites', 'code', existing_type=sa.String(length=40), nullable=True)
    op.alter_column('tender_sites', 'name', existing_type=sa.String(length=500), nullable=True)
    op.create_check_constraint(
        'site_has_identity', 'tender_sites', 'code IS NOT NULL OR name IS NOT NULL'
    )

    # 3. Completesa territorial: `unknown` per a tot el que ja hi ha.
    op.add_column('requirements', sa.Column(
        'territory_completeness', _enum(_COMPLETENESS, 'territory_completeness'),
        server_default='unknown', nullable=False,
    ))
    op.create_check_constraint(
        'territory_completeness_valid', 'requirements',
        _in_list('territory_completeness', _COMPLETENESS),
    )

    # 4. Cites dels criteris d'adjudicacio: el patro de `requirement_source_ref_links`.
    op.create_table(
        'award_criterion_source_ref_links',
        sa.Column('award_criterion_id', sa.Uuid(), nullable=False),
        sa.Column('source_ref_id', sa.Uuid(), nullable=False),
        sa.Column('created_by', sa.Uuid(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(['award_criterion_id'], ['award_criteria.id'],
                                ondelete='CASCADE', name='fk_criterion_source_ref_criterion'),
        sa.ForeignKeyConstraint(['source_ref_id'], ['tender_source_refs.id'],
                                ondelete='RESTRICT', name='fk_criterion_source_ref_source'),
        sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL',
                                name='fk_criterion_source_ref_author'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('award_criterion_id', 'source_ref_id',
                            name='uq_award_criterion_source_ref'),
    )
    op.create_index('ix_award_criterion_source_ref_links_criterion',
                    'award_criterion_source_ref_links', ['award_criterion_id'])
    op.create_index('ix_award_criterion_source_ref_links_source_ref',
                    'award_criterion_source_ref_links', ['source_ref_id'])


def downgrade() -> None:
    # Precondicio de dades, abans de qualsevol DDL: res de parcial si falla.
    if context.is_offline_mode():
        op.execute(_offline_guard())
    else:
        refuse_unless_representable_in_0049(op.get_bind())

    op.drop_index('ix_award_criterion_source_ref_links_source_ref',
                  table_name='award_criterion_source_ref_links')
    op.drop_index('ix_award_criterion_source_ref_links_criterion',
                  table_name='award_criterion_source_ref_links')
    op.drop_table('award_criterion_source_ref_links')

    op.drop_constraint(op.f('ck_requirements_territory_completeness_valid'), 'requirements',
                       type_='check')
    op.drop_column('requirements', 'territory_completeness')

    # Cap fila te codi o nom NULL ni critica NULL (precondicio): nomes esquema.
    op.drop_constraint(op.f('ck_tender_sites_site_has_identity'), 'tender_sites', type_='check')
    op.alter_column('tender_sites', 'name', existing_type=sa.String(length=500), nullable=False)
    op.alter_column('tender_sites', 'code', existing_type=sa.String(length=40), nullable=False)
    op.alter_column('tender_sites', 'is_critical', existing_type=sa.Boolean(), nullable=False)

    # Cap fila te before_formalization (precondicio): nomes esquema.
    for table in _TIMED_TABLES:
        op.drop_constraint(op.f(f'ck_{table}_timing_valid'), table, type_='check')
        op.alter_column(
            table, 'timing',
            existing_type=_enum(_TIMING, 'requirement_timing'),
            type_=_enum(_TIMING_BEFORE, 'requirement_timing'),
            existing_nullable=False, existing_server_default='unknown',
        )
        op.create_check_constraint('timing_valid', table, _in_list('timing', _TIMING_BEFORE))
