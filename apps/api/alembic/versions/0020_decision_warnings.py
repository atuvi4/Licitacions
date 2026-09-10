"""Que sabia qui va decidir, i no que sabem nosaltres ara.

L'historial de decisions deia "amb 0 bloquejos confirmats, 0 possibles i 0
obligatoris sense resoldre". Tot cert, i tot insuficient: la decisio d'aquella
pantalla es va prendre amb un avis obert que deia que l'analisi era una
SIMULACIO, i aixo no quedava enlloc. D'aqui a sis mesos, qui llegis l'historial
veuria tres zeros i cap rastre del que de veritat pesava.

`open_warnings` guarda els codis dels avisos que hi havia oberts en el moment de
signar. Es una fotografia i no una consulta: reconstruir-la a partir de l'estat
d'avui explicaria la decisio amb informacio que qui la va prendre no tenia.

Nullable a proposit. Les decisions anteriors a aquesta columna no en tenen, i
`NULL` hi vol dir "no es va registrar" —que no es el mateix que "no hi havia cap
avis"—. Convertir-les en llista buida seria escriure historia falsa.

Revision ID: 0020_decision_warnings
Revises: 0019_evidence_relevance
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0020_decision_warnings'
down_revision: str | None = '0019_evidence_relevance'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'workspace_decisions', sa.Column('open_warnings', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('workspace_decisions', 'open_warnings')
