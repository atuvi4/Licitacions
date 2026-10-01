"""Cobertura territorial estructurada del coneixement (fase 3A, pas 1).

Una dada actual de l'empresa (un fet operatiu, un servei, un paper d'un partner)
pot dir ON es certa i si la llista es tota la que hi ha:

* `knowledge_coverage_areas`: una fila per area (municipi, comarca, adreca...),
  penjada de l'entitat per `entity_type` + `entity_id`, sense FK, com els
  enllacos d'evidencia. `code` i `code_system` (INE, NUTS) son opcionals. Les
  arees d'una dada son un conjunt: index unic per `(entity_type, entity_id,
  kind, code_system, code, label)`, amb els NULL com a cadena buida.
* `coverage_completeness` a `company_operating_facts`, `company_services` i
  `partner_roles`: `complete`, `partial` o `unknown`.

**Neutralitat semantica.** Aquesta migracio no crea cap area ni toca cap text
existent (`detail`, `territory`): tot queda amb `unknown` i sense arees, que es
exactament el que les dades afirmaven abans. La fotografia del coneixement omet
aquest estat neutre del seu payload canonic, de manera que el fingerprint de
cada empresa es el mateix abans i despres.

**Downgrade.** Treu les columnes i la taula. Es perd la cobertura que s'hagi
declarat; la resta de la dada queda intacta.

Revision ID: 0047_knowledge_coverage
Revises: 0046_mobile_telephony_line
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0047_knowledge_coverage'
down_revision: str | None = '0046_mobile_telephony_line'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_COMPLETENESS = ('complete', 'partial', 'unknown')
_ENTITY_TYPES = ('operating_fact', 'service', 'partner_role')
_AREA_KINDS = ('municipality', 'comarca', 'province', 'region', 'country', 'address', 'custom')
_COVERED_TABLES = ('company_operating_facts', 'company_services', 'partner_roles')


def _in_list(column: str, values: Sequence[str]) -> str:
    return '{} IN ({})'.format(column, ', '.join(f"'{value}'" for value in values))


def _enum(values: Sequence[str], name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False)


def upgrade() -> None:
    op.create_table(
        'knowledge_coverage_areas',
        sa.Column('company_id', sa.Uuid(), nullable=False),
        sa.Column('entity_type', _enum(_ENTITY_TYPES, 'coverage_entity_type'), nullable=False),
        sa.Column('entity_id', sa.Uuid(), nullable=False),
        sa.Column('kind', _enum(_AREA_KINDS, 'area_kind'), nullable=False),
        sa.Column('label', sa.String(length=200), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=True),
        sa.Column('code_system', sa.String(length=20), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.CheckConstraint(_in_list('entity_type', _ENTITY_TYPES), name='entity_type_valid'),
        sa.CheckConstraint(_in_list('kind', _AREA_KINDS), name='kind_valid'),
        sa.ForeignKeyConstraint(['company_id'], ['companies.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_knowledge_coverage_areas_entity', 'knowledge_coverage_areas',
                    ['company_id', 'entity_type', 'entity_id'])
    op.create_index('uq_knowledge_coverage_areas_identity', 'knowledge_coverage_areas',
                    ['entity_type', 'entity_id', 'kind', sa.text("coalesce(code_system, '')"),
                     sa.text("coalesce(code, '')"), 'label'], unique=True)
    # `unknown` per a tot el que ja existeix: no afirma res de nou. Cap fila es
    # transforma; `detail` i `territory` es queden com estan.
    for table in _COVERED_TABLES:
        op.add_column(table, sa.Column(
            'coverage_completeness', _enum(_COMPLETENESS, 'coverage_completeness'),
            server_default='unknown', nullable=False,
        ))
        op.create_check_constraint(
            'coverage_completeness_valid', table,
            _in_list('coverage_completeness', _COMPLETENESS),
        )


def downgrade() -> None:
    for table in reversed(_COVERED_TABLES):
        op.drop_constraint(op.f(f'ck_{table}_coverage_completeness_valid'), table, type_='check')
        op.drop_column(table, 'coverage_completeness')
    op.drop_index('uq_knowledge_coverage_areas_identity', table_name='knowledge_coverage_areas')
    op.drop_index('ix_knowledge_coverage_areas_entity', table_name='knowledge_coverage_areas')
    op.drop_table('knowledge_coverage_areas')
