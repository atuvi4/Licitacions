"""Contra quin estat de la licitacio es va calcular cada cosa.

La 0016 va donar historia a la licitacio. Aixo lliga cada calcul i cada paquet
a un punt d'aquella historia, que es el que permet respondre la pregunta que
importa: "aixo es d'abans o de despres del canvi?".

Sense aquests enllaços nomes hi havia dues respostes possibles, i totes dues
falses. La primera, comparar marques de temps: `tender.last_changed_at` s'escriu
a cada reingestió encara que no hagi canviat res, aixi que tot semblava obsolet
sempre i l'avis no volia dir res. La segona, no comparar res: llavors un
pressupost que es mou deixava un compliment calculat sobre el pressupost vell
fent-se passar per vigent, perque ni el perfil ni els requisits havien canviat i
els dos fingerprints seguien iguals.

Amb la revisio desada, cada artefacte pot mirar NOMES els canvis posteriors que
l'afecten segons la matriu d'impacte: el pre-fit els `prefit_affecting`, el
compliment els `compliance_affecting`, els requisits els
`requirements_affecting`. Un canvi de titol no desperta ningu.

`tender_lots.removed_at` es l'altra meitat. Fins ara un lot que desapareixia del
feed s'esborrava, i com que `bid_workspaces.tender_lot_id` te ON DELETE CASCADE,
se'n duia el workspace sencer: la feina, les decisions i els paquets d'una
empresa desapareixien perque l'organ havia reorganitzat els lots. Ara la fila es
marca i es queda, i qui hi treballava ho pot veure.

Totes les columnes son nullable: el que ja existeix es d'abans que hi hagues
revisions i inventar-los-en una seria escriure historia falsa. Que sigui NULL
vol dir "no ho sabem", i qui ho llegeix ho ha de tractar com a tal.

Revision ID: 0017_lifecycle_hardening
Revises: 0016_tender_lifecycle
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0017_lifecycle_hardening'
down_revision: str | None = '0016_tender_lifecycle'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Taula -> (columna, nom de la FK). Totes apunten a `tender_revisions.id` amb
#: SET NULL: si algun dia s'esborra una licitacio, el calcul es queda i perd la
#: referencia, que es preferible a desapareixer amb ella.
_REVISION_LINKS: tuple[tuple[str, str, str], ...] = (
    ("company_tender_matches", "tender_revision_id", "fk_company_tender_matches_tender_revision"),
    # Les dues taules de pre-fit comparteixen columnes (`_MatchColumns`): el
    # match de lot es puntua igual i queda obsolet igual.
    (
        "company_tender_lot_matches",
        "tender_revision_id",
        "fk_company_tender_lot_matches_tender_revision",
    ),
    ("company_compliance_runs", "tender_revision_id", "fk_company_compliance_runs_tender_revision"),
    (
        "requirement_extraction_runs",
        "tender_revision_id",
        "fk_requirement_extraction_runs_tender_revision",
    ),
    ("submission_packages", "tender_revision_id", "fk_submission_packages_tender_revision"),
)


def upgrade() -> None:
    for table, column, constraint in _REVISION_LINKS:
        op.add_column(table, sa.Column(column, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            constraint, table, 'tender_revisions', [column], ['id'], ondelete='SET NULL'
        )

    op.add_column(
        'submission_packages',
        sa.Column('tender_normalized_hash', sa.String(length=64), nullable=True),
    )
    op.add_column(
        'tender_lots', sa.Column('removed_at', sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('tender_lots', 'removed_at')
    op.drop_column('submission_packages', 'tender_normalized_hash')
    for table, column, constraint in reversed(_REVISION_LINKS):
        op.drop_constraint(constraint, table, type_='foreignkey')
        op.drop_column(table, column)
