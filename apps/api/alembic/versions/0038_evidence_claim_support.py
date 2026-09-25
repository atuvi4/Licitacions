"""Quins valors sosté cada cita (Extraction Contract v3).

Un sol camp: `requirement_evidence.claim_support_json`. Una cita valida
(QUOTE_VALID) no prova tots els valors del requisit: el requisit `33a917b0` de
Palamos deia `fibres_FTTH_600Mbps = 11` amb una unica cita que nomes parlava de
GPON. A la v3 cada cita declara quins claims sosté, i el backend ho comprova de
manera determinista. Aquest camp en guarda el resultat:

    {"contract": "claim-support-v1",
     "ref": "e1",
     "claims": {"<claim_id>": "<ClaimCheck>"}}

**Nomes identificadors i codis.** Cap text del plec: la cita ja es a `quote`, i
el valor ja es al requisit. Tornar-los a copiar aqui seria tenir dues versions
que un dia dirien coses diferents.

`NULL` vol dir "fila anterior a la v3: suport desconegut". No vol dir "no sosté
res". Zero backfill: inventar-lo per a les files antigues seria afirmar una
comprovacio que no es va fer.

Revision ID: 0038_evidence_claim_support
Revises: 0037_workspace_issues
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0038_evidence_claim_support'
down_revision: str | None = '0037_workspace_issues'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'requirement_evidence'


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column('claim_support_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Nomes es perd el resultat de la comprovacio. La cita, la pagina i els
    # offsets no es toquen: tornen a ser exactament una cita v1.
    op.drop_column(_TABLE, 'claim_support_json')
