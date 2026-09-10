"""Quines escriptures arriben a la base de dades mentre es fa una cosa.

Serveix per demostrar absencies: que un GET no escriu res, o que una operacio
nomes toca el que li pertoca. Escolta el motor, no la sessio: veu tambe el que
fan les sessions que obre l'API per a cada peticio.
"""

from __future__ import annotations

import sqlalchemy as sa

DML = frozenset({"INSERT", "UPDATE", "DELETE"})


class DmlRecorder:
    """Cada INSERT/UPDATE/DELETE que arriba al motor mentre esta obert."""

    def __init__(self, engine):
        self.engine = engine.sync_engine
        self.statements: list[str] = []

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().split(None, 1)[0].upper() in DML:
            self.statements.append(" ".join(statement.split())[:120])

    def __enter__(self):
        sa.event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        sa.event.remove(self.engine, "before_cursor_execute", self._record)
