"""Dubtes i discrepancies d'una candidatura (S3-S10).

Un **issue** es coneixement que falta: una pregunta sense resposta, dues fonts
que es contradiuen, una dada que no te ningu. No es feina per fer —aixo ja es
`WorkspaceTask`— i la distincio no es cosmetica: una tasca es tanca fent-la, i
un dubte es tanca sabent alguna cosa que abans no se sabia.

**Els crea una persona, sempre.** Res del sistema n'obre cap: ni una dimensio en
disputa, ni una cotitzacio pendent, ni una linia divergida, ni un document nou.
Tot aixo ja te els seus avisos a `FinalReadiness`, i convertir-los en issues
voldria dir omplir la llista de preguntes que ningu no s'ha fet i enterrar-hi les
que si.

**`blocks_final_review` es una decisio d'oferta**, no una propietat del dubte:
nomes `DECISION_ROLES` la pot escriure, i neix sempre a fals.

La provinenca va per `workspace_issue_source_refs` cap a `tender_source_refs`,
que es la cadena de cites d'S3-S4. **No hi ha cap `source_refs_json`**: un JSON
d'identificadors no pot garantir que la cita existeixi ni impedir que algu
l'esborri sense adonar-se'n.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    ActorMixin,
    Base,
    CompanyOwnedMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    enum_column,
)
from app.models.enums import IssueStatus, IssueType


class WorkspaceIssue(
    UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, ActorMixin, Base
):
    """Una pregunta oberta sobre aquesta candidatura."""

    __tablename__ = "workspace_issues"
    __table_args__ = (
        # Els valors dels enums els imposa la BASE i no nomes Python:
        # `enum_column()` es materialitza com un VARCHAR sense CHECK, i sense
        # aixo un INSERT que no passes per l'ORM hi podria escriure qualsevol
        # cadena. Mateix patro que `workspace_technical_solutions`.
        sa.CheckConstraint(
            "issue_type IN ({})".format(
                ", ".join(f"'{member.value}'" for member in IssueType)
            ),
            name="issue_type_valid",
        ),
        sa.CheckConstraint(
            "status IN ({})".format(
                ", ".join(f"'{member.value}'" for member in IssueStatus)
            ),
            name="issue_status_valid",
        ),
        # Resolt vol dir que hi ha una resposta ESCRITA. Sense aixo, "resolt"
        # seria un estat mes i la resposta es podria perdre.
        sa.CheckConstraint(
            "status <> 'resolved' OR resolution IS NOT NULL",
            name="resolved_has_resolution",
        ),
        sa.Index("ix_workspace_issues_status", "workspace_id", "status"),
        sa.Index("ix_workspace_issues_type", "workspace_id", "issue_type"),
    )

    #: `company_id` ve de `CompanyOwnedMixin`, com a totes les taules que
    #: pengen d'una candidatura: es el que fa que la destruccio d'un tenant
    #: se les emporti sense haver de recorrer cap cadena.
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    issue_type: Mapped[IssueType] = mapped_column(
        enum_column(IssueType, "issue_type"), nullable=False
    )
    status: Mapped[IssueStatus] = mapped_column(
        enum_column(IssueStatus, "issue_status"), nullable=False
    )

    title: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.Text)

    # De que parla el dubte. Totes `SET NULL`: si l'entitat desapareix, la
    # pregunta i la seva resposta continuen sent certes. El que es perd es el
    # vincle, no el coneixement.
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("requirements.id", ondelete="SET NULL", name="fk_issue_requirement"),
    )
    award_criterion_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("award_criteria.id", ondelete="SET NULL", name="fk_issue_criterion"),
    )
    technical_solution_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_technical_solutions.id",
            ondelete="SET NULL",
            name="fk_issue_technical_solution",
        ),
    )
    solution_option_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_solution_options.id", ondelete="SET NULL", name="fk_issue_option"
        ),
    )
    economic_offer_line_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "economic_offer_lines.id", ondelete="SET NULL", name="fk_issue_offer_line"
        ),
    )
    dimension_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_dimensions.id", ondelete="SET NULL", name="fk_issue_dimension"
        ),
    )

    #: Text lliure, com arreu: no hi ha cataleg de proveidors.
    supplier_name: Mapped[str | None] = mapped_column(sa.String(300))

    #: Qui se n'ocupa. `SET NULL`: si marxa de l'empresa, el dubte es queda.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_issue_owner"),
    )
    due_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: La resposta, quan n'hi ha. Obligatoria per a `resolved` i imposada per un
    #: CHECK: un dubte "resolt" sense resposta escrita no seria resolt, seria
    #: oblidat.
    resolution: Mapped[str | None] = mapped_column(sa.Text)
    #: D'on surt la resposta: un correu, una acta, una trucada. Text lliure.
    resolution_source: Mapped[str | None] = mapped_column(sa.Text)
    #: Nomes els omple RESOLDRE. **Descartar els deixa buits**: descartar no es
    #: resoldre, i reutilitzar-los faria que `resolved_at IS NOT NULL` volgues
    #: dir dues coses diferents. Qui descarta i quan viu a `AuditLog` i a
    #: `updated_by`/`updated_at`.
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_issue_resolver"),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: **Decisio d'oferta, no propietat del dubte.** Nomes `DECISION_ROLES` la
    #: pot escriure, i neix sempre a fals: que una pregunta impedeixi presentar-se
    #: ho diu qui respon de l'oferta, no qui la fa.
    blocks_final_review: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceIssue {self.issue_type} {self.status} {self.title[:30]}>"


class WorkspaceIssueSourceRef(UUIDPrimaryKeyMixin, Base):
    """Quines cites sosten un dubte.

    Taula relacional i no un JSON d'identificadors: aixi la base pot garantir
    que la cita existeix, i esborrar-la per accident deixa de ser possible.

    `RESTRICT` cap a la cita, com a `requirement_source_ref_links`: una font
    citada no s'ha de poder fer desapareixer sense adonar-se'n. `CASCADE` cap al
    dubte: si el dubte deixa d'existir, els seus enllacos no serveixen a ningu.
    **Desenllacar NO esborra la cita**: deixa de sostenir aquest dubte i prou.
    """

    __tablename__ = "workspace_issue_source_refs"
    __table_args__ = (
        sa.UniqueConstraint("issue_id", "source_ref_id", name="uq_workspace_issue_source_ref"),
        sa.Index("ix_workspace_issue_source_refs_issue", "issue_id"),
        sa.Index("ix_workspace_issue_source_refs_ref", "source_ref_id"),
    )

    issue_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("workspace_issues.id", ondelete="CASCADE", name="fk_issue_ref_issue"),
        nullable=False,
    )
    source_ref_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_source_refs.id", ondelete="RESTRICT", name="fk_issue_ref_source"
        ),
        nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_issue_ref_author"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WorkspaceIssueSourceRef {self.issue_id} -> {self.source_ref_id}>"
