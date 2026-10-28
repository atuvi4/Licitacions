"""Bloc 2, etapa 1: origen `portal` dels documents i rol `clarification`.

Additiva i reversible. Nomes CHECK: cap columna nova i cap fila tocada.

* `tender_documents` admet l'origen `portal`: un document baixat pel connector del portal
  oficial (la PSCP) i verificat a l'origen (l'MD5 del fitxer es el que publica el portal).
  Com el feed, el governa una font (`governing_source_id`) i no passa per la verificacio de
  l'operador (`contribution_status = 'not_applicable'`). Canvien tres CHECK:
  `origin_valid`, `contribution_matches_origin` i `feed_has_governing_source` (el nom es
  conserva: ara diu "un origen governat te font").
* Els rols de document admeten `clarification` (aclariments oficials: les preguntes i
  respostes publicades) a les quatre taules que els validen amb `role_valid`:
  `tender_document_role_suggestions`, `tender_document_role_decisions`,
  `global_document_role_suggestions` i `document_role_reviews`. `notice` ja hi era (0044).

Nomes a PostgreSQL. A SQLite (nomes tests, que creen l'esquema amb els models) aquests
CHECK no es tornen a crear, com a 0058 i 0061.

Downgrade: a PostgreSQL refusa (RAISE abans de cap DROP) si hi ha algun document `portal`
o algun rol `clarification`: tornar enrere els deixaria fora dels CHECK, i aquesta migracio
no esborra ni coacciona res. Sense aquestes dades, torna els set CHECK d'abans.

Revision ID: 0065_portal_origin_roles
Revises: 0064_compliance_v6_notes
Create Date: 2026-10-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0065_portal_origin_roles"
down_revision: str | None = "0064_compliance_v6_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCUMENTS = "tender_documents"
# --- Copiat de l'aplicacio: la migracio no l'importa. ---------------------------
_ORIGINS_BEFORE = ("feed", "owner_upload", "operator_url", "operator_upload")
_ORIGINS = (*_ORIGINS_BEFORE, "portal")
_ROLES_BEFORE = (
    "pcap",
    "ppt",
    "characteristics_table",
    "offer_model",
    "inventory_annex",
    "justification_report",
    "approval_resolution",
    "administrative_form",
    "notice",
    "other",
)
_ROLES = (*_ROLES_BEFORE, "clarification")
_ROLE_TABLES = (
    "tender_document_role_suggestions",
    "tender_document_role_decisions",
    "global_document_role_suggestions",
    "document_role_reviews",
)


def _in_list(column: str, values: Sequence[str]) -> str:
    return "{} IN ({})".format(column, ", ".join(f"'{value}'" for value in values))


def _document_checks(*, portal: bool) -> tuple[tuple[str, str], ...]:
    """Els tres CHECK de `tender_documents` que depenen de l'origen, abans i despres."""
    if portal:
        return (
            ("origin_valid", _in_list("origin", _ORIGINS)),
            (
                "contribution_matches_origin",
                "(contribution_status = 'not_applicable') = "
                f"({_in_list('origin', ('feed', 'owner_upload', 'portal'))})",
            ),
            (
                "feed_has_governing_source",
                f"({_in_list('origin', ('feed', 'portal'))}) = (governing_source_id IS NOT NULL)",
            ),
        )
    return (
        ("origin_valid", _in_list("origin", _ORIGINS_BEFORE)),
        (
            "contribution_matches_origin",
            "(contribution_status = 'not_applicable') = (origin IN ('feed', 'owner_upload'))",
        ),
        ("feed_has_governing_source", "(origin = 'feed') = (governing_source_id IS NOT NULL)"),
    )


#: (condicio que impedeix tornar enrere, missatge) — mai s'esborra cap dada.
_DOWNGRADE_GUARDS = (
    (
        f"SELECT 1 FROM {_DOCUMENTS} WHERE origin = 'portal'",
        "0065: hi ha documents amb origen portal; tornar enrere els deixaria fora del CHECK",
    ),
    *(
        (
            f"SELECT 1 FROM {table} WHERE role = 'clarification'",
            f"0065: hi ha rols clarification a {table}; tornar enrere els deixaria fora del CHECK",
        )
        for table in _ROLE_TABLES
    ),
)


def _is_postgresql() -> bool:
    return op.get_context().dialect.name == "postgresql"


def _replace_checks(*, portal: bool) -> None:
    for name, condition in _document_checks(portal=portal):
        constraint = op.f(f"ck_{_DOCUMENTS}_{name}")
        op.drop_constraint(constraint, _DOCUMENTS, type_="check")
        op.create_check_constraint(constraint, _DOCUMENTS, condition)
    roles = _ROLES if portal else _ROLES_BEFORE
    for table in _ROLE_TABLES:
        constraint = op.f(f"ck_{table}_role_valid")
        op.drop_constraint(constraint, table, type_="check")
        op.create_check_constraint(constraint, table, _in_list("role", roles))


def upgrade() -> None:
    if _is_postgresql():
        _replace_checks(portal=True)


def downgrade() -> None:
    if not _is_postgresql():
        return
    checks = "\n".join(
        f"IF EXISTS ({query}) THEN RAISE EXCEPTION '{message}'; END IF;"
        for query, message in _DOWNGRADE_GUARDS
    )
    op.execute(f"DO $$\nBEGIN\n{checks}\nEND $$;")
    _replace_checks(portal=False)
