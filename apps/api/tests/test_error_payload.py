"""El cos d'un error ha de poder sortir per JSON sempre.

Els `details` son dades del domini —identificadors, dates, imports, enums— i han
d'arribar al client amb el seu significat intacte. El que no pot passar es que
un tipus perfectament valid converteixi un error llegible en un 500.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from app.core.errors import error_payload
from app.models.enums import WorkspaceStatus


def test_els_detalls_surten_sempre_per_json():
    """Estructures niades, amb tots els tipus que el domini fa servir."""
    detalls = {
        "issues": [{"document_id": uuid.UUID(int=7), "reason": "size_mismatch"}],
        "quan": datetime(2026, 9, 18, 10, 30, tzinfo=UTC),
        "dia": date(2026, 9, 18),
        "estat": WorkspaceStatus.PREPARING_BID,
        "niat": {"llista": [uuid.UUID(int=1), {"import": Decimal("1234.50")}]},
    }

    payload = error_payload("package_integrity_failed", "No quadra.", detalls)

    # Ha de poder passar pel serialitzador sense excepcions...
    json.dumps(payload)
    details = payload["error"]["details"]
    # ...i conservar el significat, no convertir-se en text opac.
    assert details["issues"][0]["document_id"] == str(uuid.UUID(int=7))
    assert details["issues"][0]["reason"] == "size_mismatch"
    assert details["quan"].startswith("2026-09-18T10:30")
    assert details["dia"] == "2026-09-18"
    assert details["estat"] == WorkspaceStatus.PREPARING_BID.value
    assert details["niat"]["llista"][0] == str(uuid.UUID(int=1))


def test_un_import_no_es_converteix_en_coma_flotant():
    """La resta de l'API serialitza `Decimal` com a cadena.

    Amb l'encoder per defecte seria un `float`, i un import que passa per coma
    flotant pot perdre decimals just quan es parla de diners.
    """
    payload = error_payload("x", "y", {"total": Decimal("1234.50")})

    assert payload["error"]["details"]["total"] == "1234.50"


def test_un_error_sense_detalls_segueix_tenint_la_mateixa_forma():
    payload = error_payload("not_found", "No trobat.")

    assert payload == {
        "error": {"code": "not_found", "message": "No trobat.", "details": {}}
    }
