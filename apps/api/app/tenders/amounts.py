"""Quan un import economic de la font es fa servir com a import.

Regla unica (decisio del propietari, 19-10-2026): un import (`estimated_value`,
`base_budget_without_tax`, `base_budget`, de la licitacio o d'un lot) nomes es
**util** si es estrictament mes gran que 0.

La PLACSP publica `0.00` en acords marc, contractes centralitzats i altres casos
sense pressupost propi. Aquest zero es conserva tal com ve a la base (tracabilitat
amb l'XML) i a les respostes crues de l'API, pero mai no es fa servir com a import
valid: ni en calculs, ni en filtres, ni en l'ordenacio, ni com a maxim d'una oferta,
ni com a base d'un llindar percentual. Un negatiu (no n'hi hauria d'haver) tampoc.

Hi ha un sol ajudant per a Python (`useful_amount`) i un per a SQL
(`useful_amount_sql`), amb la mateixa regla. La web en te l'equivalent a
`apps/web/src/lib/amounts.ts` (`usefulAmount`).

NO s'aplica als camins que han de conservar el literal: ingestio, instantanies del
cicle de vida, deteccio de canvis i les guardes de l'adaptador PSCP (F-06a), que
comparen al centim el que publica la font.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

import sqlalchemy as sa


def useful_amount(value: Any) -> Decimal | None:
    """L'import si es util (estrictament > 0); si no, `None` (com si no hi fos).

    Accepta `Decimal`, `int`, `float` o text numeric; qualsevol altra cosa (o un
    valor no finit) es `None`. Mai no inventa ni arrodoneix res.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        amount = value
    else:
        try:
            amount = Decimal(str(value).strip())
        except (InvalidOperation, ValueError):
            return None
    if not amount.is_finite():
        return None
    return amount if amount > 0 else None


def useful_amount_sql(column: sa.ColumnElement) -> sa.ColumnElement:
    """La mateixa regla en SQL: `CASE WHEN x > 0 THEN x END` (zero, negatiu o NULL -> NULL)."""
    return sa.case((column > 0, column), else_=sa.null())
