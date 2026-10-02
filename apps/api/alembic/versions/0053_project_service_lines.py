"""Linies de servei directes de les referencies d'execucio (fase 3A, pas 6).

Fins a 0052, les linies que acredita un projecte sortien dels serveis del cataleg
ACTUAL de l'empresa (`project_services` -> `company_services.service_line_code`).
Aixo lligava una referencia HISTORICA amb el cataleg d'avui: un contracte de veu IP
deixava d'acreditar veu IP si l'empresa treia o canviava aquell servei.

* `company_project_service_lines` (nova): (projecte, linia del vocabulari), clau
  primaria composta, FK al projecte (CASCADE) i al vocabulari (RESTRICT). Sense
  `company_id`: el projecte ja el porta.
* **Backfill determinista** des de `project_services` JOIN `company_services`:
  cada linia no nul·la dels serveis vinculats, sense duplicats. Es exactament el
  conjunt que 0052 llegia, aixi que cap empremta canvia en pujar.
* `project_services` NO s'esborra: queda LEGACY per a la UI 4C antiga. A partir
  d'aqui la qualificacio, la fotografia i l'analisi nomes llegeixen la taula nova, i
  la UI nova no escriu la legacy. No hi ha sincronitzacio entre les dues.

**Downgrade: conjunts exactes, mai inventar.** 0052 nomes expressa les linies d'un
projecte INDIRECTAMENT, pels enllacos `project_services` que JA EXISTEIXEN. Per a
cada projecte, siguin

* A = linies de `company_project_service_lines` (la veritat de 0053);
* B = linies dels `company_services` dels enllacos legacy existents.

El downgrade nomes es fidel si A == B a tots els projectes:

1. A - B no buit: 0052 perdria una linia -> refusa. Tambe si existeix un servei de
   l'empresa amb aquella linia pero no estava vinculat: crear-ne l'enllac seria
   triar una associacio projecte -> servei que mai no hi va ser;
2. B - A no buit: 0052 tornaria a afirmar una linia treta a 0053 -> refusa.

Si cal refusar, s'atura amb `downgrade_0053_incompatible_rows` abans de cap DDL (al
SQL offline, com a bloc `DO`) i no toca cap fila. Si els conjunts coincideixen,
nomes elimina l'estructura de 0053: cap INSERT, UPDATE ni DELETE a `project_services`
(diversos serveis de la mateixa linia donen el mateix conjunt i no cal tocar-los).

Revision ID: 0053_project_service_lines
Revises: 0052_company_qualification
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

revision: str = '0053_project_service_lines'
down_revision: str | None = '0052_company_qualification'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BACKFILL = (
    'INSERT INTO company_project_service_lines (project_id, service_line_code, created_at) '
    'SELECT DISTINCT ps.project_id, s.service_line_code, CURRENT_TIMESTAMP '
    'FROM project_services ps JOIN company_services s ON s.id = ps.service_id '
    'WHERE s.service_line_code IS NOT NULL'
)

#: Files que 0052 no pot representar fidelment: (taula, condicio, que vol dir).
INCOMPATIBLE_WITH_0052 = (
    # A - B: una linia directa que cap enllac legacy existent no expressa.
    ('company_project_service_lines l',
     'NOT EXISTS (SELECT 1 FROM project_services ps '
     'JOIN company_services s ON s.id = ps.service_id '
     'WHERE ps.project_id = l.project_id AND s.service_line_code = l.service_line_code)',
     'linies directes que cap enllac legacy existent no expressa'),
    # B - A: un enllac legacy amb una linia que el projecte ja no acredita.
    ('project_services ps',
     'EXISTS (SELECT 1 FROM company_services s WHERE s.id = ps.service_id '
     'AND s.service_line_code IS NOT NULL AND NOT EXISTS ('
     'SELECT 1 FROM company_project_service_lines l '
     'WHERE l.project_id = ps.project_id AND l.service_line_code = s.service_line_code))',
     'enllacos legacy amb una linia que el projecte ja no acredita'),
)
DOWNGRADE_BLOCKED = 'downgrade_0053_incompatible_rows'


class DowngradeBlockedError(RuntimeError):
    """El downgrade s'atura perque hi ha files que 0052 no pot guardar."""

    code = DOWNGRADE_BLOCKED


def incompatible_rows(connection) -> dict[str, int]:
    """Quantes files hi ha de cada cas. Nomes llegeix."""
    return {
        meaning: connection.execute(
            sa.text(f'SELECT count(*) FROM {table} WHERE {condition}')
        ).scalar()
        for table, condition, meaning in INCOMPATIBLE_WITH_0052
    }


def refuse_unless_representable_in_0052(connection) -> None:
    found = {meaning: n for meaning, n in incompatible_rows(connection).items() if n}
    if found:
        detail = '; '.join(f'{n} {meaning}' for meaning, n in found.items())
        raise DowngradeBlockedError(
            f'{DOWNGRADE_BLOCKED}: {detail}. 0052 no ho pot representar i el downgrade no '
            'crea cap enllac ni esborra res: resoleu aquestes files a ma abans de tornar enrere.'
        )


def _offline_guard() -> str:
    checks = '\n'.join(
        f"  SELECT count(*) INTO n FROM {table} WHERE {condition};\n"
        f"  IF n > 0 THEN RAISE EXCEPTION '{DOWNGRADE_BLOCKED} - % {meaning}', n; END IF;"
        for table, condition, meaning in INCOMPATIBLE_WITH_0052
    )
    return f'DO $$\nDECLARE n integer;\nBEGIN\n{checks}\nEND $$'


def upgrade() -> None:
    op.create_table(
        'company_project_service_lines',
        sa.Column('project_id', sa.Uuid(), nullable=False),
        sa.Column('service_line_code', sa.String(length=40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False),
        sa.ForeignKeyConstraint(['project_id'], ['company_projects.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['service_line_code'], ['service_lines.code'],
                                ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('project_id', 'service_line_code'),
    )
    op.execute(BACKFILL)


def downgrade() -> None:
    # Primer la precondicio; si falla, no s'ha tocat res.
    if context.is_offline_mode():
        op.execute(_offline_guard())
    else:
        refuse_unless_representable_in_0052(op.get_bind())
    # Conjunts identics: 0052 ja ho expressa tot. Nomes cau l'estructura de 0053.
    op.drop_table('company_project_service_lines')
