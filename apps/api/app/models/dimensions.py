"""Quantes n'hi ha, de debo: dimensions i les fonts que les afirmen (S3-S6).

El problema real que resol: el plec diu 151 extensions, el full de calcul de
l'ajuntament en diu 181 i el proveidor tambe. El sistema **no ha de triar**. Ha
de saber dir que hi ha dues xifres, ensenyar d'on surt cadascuna, i deixar que
una persona fixi la que fara servir la candidatura explicant per que.

Dues taules i una sola idea:

* una **Dimension** es el CONCEPTE i el valor de treball decidit per algu;
* una **Observation** es una font que afirma un valor.

D'aqui surten dues regles que no es poden barrejar:

    observacio  !=  decisio
    dues fonts diferents  !=  error resolt automaticament

L'unic que automatitza Sprint 3 es **detectar** una contradiccio numerica un
cop les dades hi son. No inventa que es una dimensio, no dedueix numeros del
text i no decideix quin es bo. `undecided` no es `decided`.

No hi ha entitat `Site` i no n'hi haura: `scope_label` es text lliure
—«Policia Local», «Seu Placa Major»— perque muntar un graf d'ubicacions per
dir on va una antena seria construir un cataleg que ningu no manté.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CompanyOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import DimensionStatus, ObservationSourceKind


class WorkspaceDimension(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """Una magnitud que la candidatura necessita tenir clara.

    `revision` es el testimoni de CAS. Hi es perque aixo ho toquen diverses
    persones alhora —qui parla amb el proveidor i qui llegeix el plec— i dues
    escriptures simultanies no poden fer desapareixer una decisio en silenci.

    `working_value_num` es `Numeric(18,4)` i no `Float`: aqui hi acabara havent
    quantitats que multipliquen preus, i un binari que no sap representar 0,1
    no te res a fer en una xifra que anira a una oferta.
    """

    __tablename__ = "workspace_dimensions"
    __table_args__ = (
        # La clau es humana i viu DINS del workspace: no hi ha cap ontologia
        # global de dimensions, i no n'hi ha d'haver.
        sa.UniqueConstraint(
            "workspace_id", "key", "scope_label", name="uq_workspace_dimension_key_scope"
        ),
        # Decidir exigeix dir per que. Sense aixo, "decidit" seria nomes un
        # estat, i el que fa util una decisio es justament el motiu.
        sa.CheckConstraint(
            "status <> 'decided' OR decision_reason IS NOT NULL",
            name="dimension_decided_has_reason",
        ),
        sa.Index("ix_workspace_dimensions_workspace", "workspace_id", "archived_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: Slug lliure triat per la persona: `extensions`, `seus`, `punts_wifi`.
    key: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    label: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    #: `unitats`, `Mbps`, `linies`. Sense enum: inventar-ne un obligaria a
    #: mantenir un cataleg d'unitats que no ajuda ningu.
    unit: Mapped[str | None] = mapped_column(sa.String(50))
    #: A que es refereix, quan la mateixa magnitud es compta per llocs.
    scope_label: Mapped[str | None] = mapped_column(sa.String(300))

    #: El valor CANONIC, el que fara servir la candidatura. El fixa una persona.
    working_value_text: Mapped[str | None] = mapped_column(sa.String(500))
    working_value_num: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4))

    status: Mapped[DimensionStatus] = mapped_column(
        enum_column(DimensionStatus, "dimension_status"),
        nullable=False,
        default=DimensionStatus.UNDECIDED,
    )
    #: Per que s'ha triat aquest valor entre els que diuen les fonts.
    decision_reason: Mapped[str | None] = mapped_column(sa.Text)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_workspace_dimensions_decider"),
    )
    decided_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    #: Arxivar no es esborrar: les observacions es conserven i la dimensio surt
    #: de la llista activa i deixa de generar avisos.
    archived_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: Testimoni de CAS. Puja a cada escriptura real.
    revision: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    observations: Mapped[list[WorkspaceDimensionObservation]] = relationship(
        back_populates="dimension",
        cascade="all, delete-orphan",
        order_by="WorkspaceDimensionObservation.created_at",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceDimension {self.key} {self.status}>"


class WorkspaceDimensionObservation(UUIDPrimaryKeyMixin, Base):
    """Una font diu que aquesta magnitud val aixo.

    **Append-only.** No hi ha PATCH ni DELETE, i no es cap oblit: una
    observacio no diu "el valor es 151", diu "el dia tal, aquesta font deia
    151". Reescriure-la seria canviar el que algu va llegir, i llavors el
    registre deixaria de servir per entendre d'on ve una discrepancia. Si la
    dada era erronia, s'afegeix una observacio nova i una persona decideix.

    Per aixo tampoc no te `updated_at` ni `archived_at`.

    La provinenca documental **no es duplica aqui**: `source_ref_id` apunta a
    `tender_source_refs`, que ja porta la versio, el SHA, la pagina i la cita.
    El disseny antic amb `tender_document_id`, `page_number` i `quote` queda
    superat: tenir-ne dues copies voldria dir que un dia dirien coses
    diferents.
    """

    __tablename__ = "workspace_dimension_observations"
    __table_args__ = (
        sa.Index("ix_dimension_observations_dimension", "dimension_id"),
    )

    dimension_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_dimensions.id",
            ondelete="CASCADE",
            name="fk_dimension_observations_dimension",
        ),
        nullable=False,
    )
    #: QUI ho afirma. No es la provinenca: veure `ObservationSourceKind`.
    source_kind: Mapped[ObservationSourceKind] = mapped_column(
        enum_column(ObservationSourceKind, "observation_source_kind"), nullable=False
    )
    #: La cita, quan n'hi ha. `SET NULL` i no `RESTRICT`: aqui la cita es una
    #: ajuda per entendre l'observacio, no l'ancora de res, i el text del valor
    #: continua sent llegible sense ella.
    source_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_source_refs.id",
            ondelete="SET NULL",
            name="fk_dimension_observations_source_ref",
        ),
    )

    #: Sempre hi es: es el que la font diu, tal com ho diu.
    value_text: Mapped[str] = mapped_column(sa.String(500), nullable=False)
    #: Nomes quan el valor es realment un numero. `NULL` vol dir que no se sap
    #: comparar, i el sistema NO inventa que "cent vuitanta-una" es 181.
    value_num: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 4))
    note: Mapped[str | None] = mapped_column(sa.Text)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_dimension_observations_author"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    dimension: Mapped[WorkspaceDimension] = relationship(back_populates="observations")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceDimensionObservation {self.source_kind} {self.value_text[:20]}>"
