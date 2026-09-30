"""Qui pot escriure coneixement de l'empresa.

* **Llegir** (fotografia, files, vocabulari, observacions): qualsevol membre.
* **Declarar, editar, retirar, i posar o treure evidencia**: propietari,
  administracio, responsable de licitacions i tecnic.
* **Eliminar fisicament**: propietari i administracio.
* **Confirmar o descartar l'historic**: propietari, administracio i responsable
  de licitacions (`routers/knowledge.py`).
* **Verificar**: propietari i administracio (`routers/knowledge.py`).

Qui nomes consulta (`viewer`) no modifica res, tampoc per les rutes que ja
existien (certificacions, serveis i els enllacos d'evidencia).

Es respon `403` i no `404`: qui pregunta es membre i pot llegir la dada; el que
no te es potestat per canviar-la.
"""

from __future__ import annotations

from app.core.errors import PermissionDeniedError
from app.models.enums import MembershipRole

KNOWLEDGE_EDIT_ROLES = frozenset(
    {
        MembershipRole.OWNER,
        MembershipRole.ADMIN,
        MembershipRole.BID_MANAGER,
        MembershipRole.TECHNICAL,
    }
)
KNOWLEDGE_DELETE_ROLES = frozenset({MembershipRole.OWNER, MembershipRole.ADMIN})


def assert_can_edit(role: MembershipRole) -> None:
    """Declarar, editar o retirar coneixement, i tocar-ne l'evidencia."""
    if role not in KNOWLEDGE_EDIT_ROLES:
        raise PermissionDeniedError("El teu rol no pot modificar el coneixement de l'empresa.")


def assert_can_delete(role: MembershipRole) -> None:
    """Eliminar fisicament una dada de coneixement."""
    if role not in KNOWLEDGE_DELETE_ROLES:
        raise PermissionDeniedError(
            "El teu rol no pot eliminar coneixement de l'empresa. Es pot retirar sense esborrar-lo."
        )
