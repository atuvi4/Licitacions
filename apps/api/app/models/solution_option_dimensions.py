"""De quines magnituds depèn una opció (S3-S7b).

Una opció de servei mòbil depèn del nombre de línies, del de terminals i del de
SIM de dades. I el nombre de línies el fan servir totes les opcions que s'hi
comparen. Per això és **N:N** i no un camp a cap de les dues taules.

**La relació diu una sola cosa: «aquesta opció depèn d'aquesta magnitud».** No
diu que `option.quantity` surti de `dimension.working_value_num`, ni que el
valor estigués decidit el dia que es va enllaçar, ni que la magnitud sigui el
preu. És una dependència declarada per una persona, i prou.

**No hi ha snapshot.** Ni `working_value_num`, ni `dimension_revision`, ni
`snapshot_taken_at`. L'estat d'ara el diu `WorkspaceDimension`, que n'és l'única
font de veritat; guardar-ne una còpia aquí voldria dir tenir dues respostes a la
mateixa pregunta. Quan més endavant una línia econòmica adopti una quantitat,
serà la línia qui en farà el snapshot econòmic.

**I no hi ha cap propagació.** Enllaçar no copia res, i canviar la magnitud
després no toca l'opció. Veure `services/solution_option_dimensions.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class WorkspaceSolutionOptionDimension(UUIDPrimaryKeyMixin, Base):
    """Una dependència declarada entre una opció i una magnitud."""

    __tablename__ = "workspace_solution_option_dimensions"
    __table_args__ = (
        # La mateixa opció no depèn dues vegades de la mateixa magnitud.
        sa.UniqueConstraint(
            "solution_option_id", "dimension_id", name="uq_solution_option_dimension"
        ),
        sa.Index("ix_solution_option_dimensions_option", "solution_option_id"),
        sa.Index("ix_solution_option_dimensions_dimension", "dimension_id"),
    )

    #: `CASCADE`: si l'opció deixa d'existir, les seves dependències no tenen
    #: ningú a qui servir. **Arxivar-la, en canvi, NO les toca**: arxivar no és
    #: esborrar, i una opció arxivada conserva de què depenia.
    solution_option_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_solution_options.id",
            ondelete="CASCADE",
            name="fk_solution_option_dimension_option",
        ),
        nullable=False,
    )
    #: També `CASCADE`. El disseny congelat deia `RESTRICT` per no deixar
    #: esborrar una magnitud de què depèn una opció; a la pràctica el producte
    #: no esborra cap magnitud —s'arxiven—, o sigui que aquella protecció mai
    #: no arribaria a actuar, i en canvi faria fallar la destrucció d'un tenant
    #: deixant files que ningú no pot netejar.
    dimension_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_dimensions.id",
            ondelete="CASCADE",
            name="fk_solution_option_dimension_dimension",
        ),
        nullable=False,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "users.id", ondelete="SET NULL", name="fk_solution_option_dimension_author"
        ),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WorkspaceSolutionOptionDimension {self.solution_option_id}"
            f" -> {self.dimension_id}>"
        )
