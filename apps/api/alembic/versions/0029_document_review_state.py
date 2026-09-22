"""Fins on arriba la revisio HUMANA dels plecs (S3-S3).

Dues columnes a `bid_workspaces` i res mes. Segueixen exactament el patro de
`technical_scope_reviewed_at` / `technical_scope_fingerprint`, i pel mateix
motiu: que hi hagi plecs no vol dir que algu se'ls hagi mirat.

**NULL vol dir NO revisat.** Cap workspace existent no queda revisat en silenci,
i per aixo no hi ha cap backfill: afirmar que algu ha revisat el que hi ha ara
seria justament la mentida que aquestes columnes existeixen per evitar.

**No hi ha cap columna "caducat".** Si el conjunt de plecs d'ara no coincideix
amb el que es va revisar, es deriva comparant les dues empremtes en llegir.
Guardar-ho voldria dir tenir dues versions del mateix fet, i la desada quedaria
enrere el primer cop que arribes un document sense passar per aqui.

**Tampoc hi ha `reviewed_by`.** L'abast tecnic el te perque la seva revisio es
una afirmacio tecnica que algu signa; aixo es una constatacio de fins on ha
arribat la lectura, i qui la va fer ja queda a l'auditoria. Afegir la columna
"per si de cas" seria un camp mes a mantenir sense cap lector.

Revision ID: 0029_document_review_state
Revises: 0028_manual_ingestion_support
Create Date: 2026-09-22
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0029_document_review_state'
down_revision: str | None = '0028_manual_ingestion_support'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKSPACES = 'bid_workspaces'


def upgrade() -> None:
    op.add_column(
        _WORKSPACES,
        sa.Column('documents_reviewed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        _WORKSPACES,
        sa.Column('documents_reviewed_fingerprint', sa.String(length=64), nullable=True),
    )
    # Cap index: no es busca mai cap workspace per aquests camps. Es llegeixen
    # amb la fila que ja s'ha carregat, i un index seria pes sense lector.


def downgrade() -> None:
    op.drop_column(_WORKSPACES, 'documents_reviewed_fingerprint')
    op.drop_column(_WORKSPACES, 'documents_reviewed_at')
