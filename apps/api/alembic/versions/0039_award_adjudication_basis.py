"""Per que un criteri participa a l'adjudicacio (Extraction Contract v3).

Un sol camp: `award_criteria.adjudication_basis_json`. "Es valorara" no fa un
criteri d'adjudicacio, i venir del PCAP tampoc. El que el fa formal es un
mecanisme —punts, pes, formula, taula de criteris, model d'oferta del Sobre 2,
clausula formal de criteris— provat per una cita. Aquest camp en guarda el
resultat:

    {"contract": "adjudication-basis-v1",
     "declared_kind": "award_criterion",
     "award_class": "formal_award_criterion",
     "mechanisms": [{"kind": "points", "check": "verified",
                     "evidence_ids": ["<award_criterion_evidence.id>"]}]}

Les cites s'hi referencien per `award_criterion_evidence.id`; el text no es
copia. `award_class` es la classe final que decideix el backend, no el model.

`NULL` vol dir "criteri anterior a la v3: base desconeguda", que no es "sense
base". Zero backfill: classificar ara els criteris antics seria afirmar una
comprovacio que no es va fer.

Revision ID: 0039_award_adjudication_basis
Revises: 0038_evidence_claim_support
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0039_award_adjudication_basis'
down_revision: str | None = '0038_evidence_claim_support'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'award_criteria'


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column('adjudication_basis_json', sa.JSON(), nullable=True))


def downgrade() -> None:
    # Nomes es perd la classificacio v3. Punts, pes, formula i cites no es
    # toquen: el criteri torna a ser exactament un criteri v1/v2.
    op.drop_column(_TABLE, 'adjudication_basis_json')
