"""Quines pagines va llegir el model, i no nomes quantes.

El run del perfil v2 sobre el tender 422-2026 va completar 5 de 8 chunks: tres
blocs del plec no es van analitzar mai. El benchmark ho va comptar com un
requisit no detectat pel model, i el model no havia vist la pagina. La mesura
deia una cosa i en mesurava una altra.

`chunks_completed`/`chunks_failed` diuen QUANTS chunks van caure; per decidir si
un requisit del gold era mesurable cal saber QUINS, perque el que importa es la
pagina. `pages_analyzed` tampoc no ho resol: compta pagines amb text, no pagines
que van arribar al model.

`chunk_coverage` desa la fotografia dels chunks del run amb l'estat de cadascun.
Fotografia i no consulta, com `source_quality_issues`: una re-extraccio del
document canviaria el pla de chunks, i aquest run es va fer amb el d'aleshores.

Nullable a proposit. Els runs anteriors no en tenen, i `NULL` vol dir "cobertura
desconeguda". Omplir-los amb la llista completa de pagines seria afirmar que es
van llegir totes, que es exactament l'error que aquesta columna ve a evitar.

Revision ID: 0021_chunk_coverage
Revises: 0020_decision_warnings
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0021_chunk_coverage'
down_revision: str | None = '0020_decision_warnings'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'requirement_extraction_runs', sa.Column('chunk_coverage', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('requirement_extraction_runs', 'chunk_coverage')
