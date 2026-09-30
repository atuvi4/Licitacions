"""Telefonia mobil: una linia de servei mes al vocabulari global.

Nomes una fila a `service_lines`. Es la manera d'ampliar un vocabulari tancat:
el codi s'afegeix a `app/knowledge/vocabulary.py` i a una migracio, i
`test_knowledge_vocabulary` comprova que coincideixen. Cap taula ni cap columna
no canvia: els serveis, els papers dels partners, les solucions, els productes
i l'abast de les certificacions ja referencien `service_lines.code`.

**Downgrade.** Treu la fila. Si alguna empresa ja la fa servir, la clau forana
ho impedeix: no s'esborra coneixement d'amagat.

Revision ID: 0046_mobile_telephony_line
Revises: 0045_company_knowledge
Create Date: 2026-09-30
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0046_mobile_telephony_line'
down_revision: str | None = '0045_company_knowledge'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_SERVICE_LINES = (
    ('mobile_telephony', 'Telefonia mobil'),
)


def upgrade() -> None:
    for code, label in _SERVICE_LINES:
        op.execute(f"INSERT INTO service_lines (code, label) VALUES ('{code}', '{label}')")


def downgrade() -> None:
    for code, _ in _SERVICE_LINES:
        op.execute(f"DELETE FROM service_lines WHERE code = '{code}'")
