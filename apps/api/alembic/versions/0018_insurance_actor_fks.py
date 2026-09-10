"""Esborrar un usuari no podia bloquejar-se per un limit d'asseguranca seu.

`ActorMixin` declara `created_by` i `updated_by` amb `ondelete="SET NULL"`: qui
va escriure una fila es rastre d'auditoria, no una dependencia. Si algun dia
l'usuari marxa, la fila s'ha de quedar i perdre l'autor, que es l'unica cosa que
deixa de ser certa.

La 0013 va crear aquestes dues FK sense clausula `ON DELETE`, i a PostgreSQL
aixo vol dir `NO ACTION`. L'efecte no es que el limit perdes l'autor: es que
esborrar l'usuari fallava amb violacio de clau forana, i una taula del perfil
corporatiu acabava decidint si una persona es pot donar de baixa.

No es va veure perque la suite corre sobre SQLite creant les taules des dels
models, on la clausula hi era; nomes PostgreSQL, que construeix l'esquema des
de les migracions, ensenya la diferencia. `test_migrations.py` ara la compara
FK a FK sobre l'SQL de les migracions, aixi que no es pot tornar a amagar.

Es conserven els noms que va posar la 0013 (`fk_ins_limit_*`): son els que
tenen les altres FK de la taula i el que cal corregir es el comportament, no
com es diuen. Les columnes segueixen sent nullable i no es toca cap fila.

Revision ID: 0018_insurance_actor_fks
Revises: 0017_lifecycle_hardening
Create Date: 2026-09-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0018_insurance_actor_fks'
down_revision: str | None = '0017_lifecycle_hardening'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = 'company_insurance_limits'
#: Columna -> nom de la FK, tal com la va crear la 0013.
_ACTOR_FKS: tuple[tuple[str, str], ...] = (
    ('created_by', 'fk_ins_limit_created_by'),
    ('updated_by', 'fk_ins_limit_updated_by'),
)


def upgrade() -> None:
    for column, constraint in _ACTOR_FKS:
        op.drop_constraint(constraint, _TABLE, type_='foreignkey')
        op.create_foreign_key(
            constraint, _TABLE, 'users', [column], ['id'], ondelete='SET NULL'
        )


def downgrade() -> None:
    """Torna a deixar-les sense `ON DELETE`, que es com les va crear la 0013."""
    for column, constraint in reversed(_ACTOR_FKS):
        op.drop_constraint(constraint, _TABLE, type_='foreignkey')
        op.create_foreign_key(constraint, _TABLE, 'users', [column], ['id'])
