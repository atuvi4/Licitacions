"""Paquet de presentacio: la fotografia exacta que una persona aprova.

    BidWorkspace  ->  SubmissionPackage (revisio N)
                        |- SubmissionPackageItem      (document + SHA d'aquell dia)
                        |- SubmissionPackageApproval  (immutable, append-only)

El problema que resol: aprovar "el workspace" no vol dir res. El workspace canvia
—s'hi afegeixen documents, es renoven polisses, es recalcula el compliment— i una
aprovacio que apunti a un objecte mutable no diu QUE es va aprovar. Per aixo
l'aprovacio apunta a una **revisio congelada**, amb els documents i els seus
hashos d'aquell moment.

Tres decisions que sostenen la capa:

* **Un paquet congelat no es toca.** No hi ha cap endpoint que hi afegeixi o en
  tregui un document. Canviar qualsevol cosa vol dir `revision_number + 1`;
  l'anterior queda intacta i consultable.
* **El hash es dels BYTES, no de l'identificador.** `document_sha256` es copia a
  l'item. Si algu substitueix el fitxer conservant el mateix `document_id`, el
  paquet ho pot detectar; confiar en l'id sol no ho permetria.
* **No es dupliquen fitxers.** Els items referencien els documents corporatius
  que ja hi ha. La immutabilitat la garanteix el parell id + SHA-256, no una
  segona copia a l'storage. A canvi, aquests documents queden **retinguts**: no
  es poden esborrar mentre un paquet els referenciï, perque llavors el manifest
  diria quin SHA-256 hi havia i ningu no podria tornar als bytes.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CompanyOwnedMixin, TimestampMixin, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import (
    PackageApprovalDecision,
    RequirementCategory,
    SubmissionPackageStatus,
    WorkspaceReadiness,
)


class SubmissionPackage(UUIDPrimaryKeyMixin, CompanyOwnedMixin, TimestampMixin, Base):
    """Una revisio congelada del que es presentaria.

    `manifest_json` es guarda sencer i no nomes el seu hash: el hash diu si una
    cosa ha canviat, pero no QUE hi havia. Per poder respondre "exactament que va
    aprovar aquesta persona" d'aqui a dos anys cal el contingut, i ha de ser el
    d'aquell dia encara que despres s'esborri un document.
    """

    __tablename__ = "submission_packages"
    __table_args__ = (
        sa.UniqueConstraint(
            "workspace_id", "revision_number", name="uq_submission_packages_revision"
        ),
        sa.Index("ix_submission_packages_workspace", "workspace_id", "revision_number"),
        sa.Index("ix_submission_packages_company_status", "company_id", "status"),
        sa.CheckConstraint("revision_number >= 1", name="package_revision_positive"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("bid_workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[SubmissionPackageStatus] = mapped_column(
        enum_column(SubmissionPackageStatus, "submission_package_status"),
        nullable=False,
        default=SubmissionPackageStatus.DRAFT,
    )

    #: SHA-256 de la serialitzacio canonica del manifest. Mateix paquet, mateix
    #: hash; qualsevol canvi real, hash diferent.
    manifest_hash: Mapped[str | None] = mapped_column(sa.String(64), index=True)
    manifest_json: Mapped[dict[str, Any] | None] = mapped_column(sa.JSON)

    #: Contra quins calculs es va congelar. Si el vigent ja no es aquest, el
    #: paquet ha quedat endarrerit i no es pot aprovar.
    compliance_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "company_compliance_runs.id",
            ondelete="SET NULL",
            name="fk_submission_packages_compliance_run",
        ),
    )
    requirement_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "requirement_extraction_runs.id",
            ondelete="SET NULL",
            name="fk_submission_packages_requirement_run",
        ),
    )
    prefit_score: Mapped[int | None] = mapped_column(sa.Integer)
    deadline_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    #: Recompte de cobertura en el moment de congelar. Es guarda perque el
    #: report d'auditoria no depengui de recalcular res.
    mandatory_total: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    mandatory_covered: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    document_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )
    frozen_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    frozen_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
    )

    # `selectin` i no `lazy` per defecte: un paquet sense els seus documents no
    # serveix per a res, i una carrega mandrosa dins d'una funcio asincrona no
    # es pot resoldre (`MissingGreenlet`). Son col·leccions petites i acotades.
    items: Mapped[list[SubmissionPackageItem]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
        order_by="SubmissionPackageItem.position",
        lazy="selectin",
    )
    approvals: Mapped[list[SubmissionPackageApproval]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
        order_by="SubmissionPackageApproval.approved_at.desc()",
        lazy="selectin",
    )

    @property
    def is_immutable(self) -> bool:
        """Congelat, aprovat o superat: la fotografia ja no es pot retocar."""
        return self.status != SubmissionPackageStatus.DRAFT

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SubmissionPackage r{self.revision_number} {self.status}>"


class SubmissionPackageItem(UUIDPrimaryKeyMixin, CompanyOwnedMixin, Base):
    """Un document del paquet, amb el seu hash del dia que es va congelar.

    `document_sha256`, `file_size` i `storage_key` son copies deliberades del
    document corporatiu: si algu el modifica despres, la comparacio entre el que
    hi ha aqui i el que hi ha ara es exactament el que detecta el canvi.
    """

    __tablename__ = "submission_package_items"
    __table_args__ = (
        sa.Index("ix_submission_package_items_package", "package_id", "position"),
        sa.Index("ix_submission_package_items_document", "document_id"),
        sa.UniqueConstraint(
            "package_id",
            "document_id",
            "requirement_id",
            name="uq_submission_package_items_document_requirement",
        ),
    )

    package_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("submission_packages.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: D'on ve la peca al dossier. `SET NULL` perque esborrar el dossier no ha
    #: de fer desapareixer el que es va aprovar.
    workspace_evidence_item_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "workspace_evidence_items.id",
            ondelete="SET NULL",
            name="fk_submission_package_items_evidence",
        ),
    )
    #: `RESTRICT` i no `SET NULL`: un paquet congelat ha de seguir sent
    #: REPRODUIBLE, no nomes auditable. Si el document es pogues esborrar, el
    #: manifest continuaria dient quin SHA-256 hi havia i no hi hauria manera de
    #: tornar als bytes. La base de dades ho impedeix; el servei hi afegeix un
    #: error llegible en comptes d'un `IntegrityError`.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("documents.id", ondelete="RESTRICT")
    )
    requirement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("requirements.id", ondelete="SET NULL")
    )

    #: La fotografia del fitxer. Es el que fa que el paquet segueixi dient la
    #: veritat encara que despres s'esborri el document.
    document_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(sa.String(512), nullable=False)
    filename: Mapped[str] = mapped_column(sa.String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    file_size: Mapped[int] = mapped_column(sa.BigInteger, nullable=False)

    requirement_title: Mapped[str | None] = mapped_column(sa.String(500))
    category: Mapped[RequirementCategory] = mapped_column(
        enum_column(RequirementCategory, "requirement_category"),
        nullable=False,
        default=RequirementCategory.OTHER,
    )
    #: Ordre estable dins del paquet. El manifest s'hi basa, i per aixo el hash
    #: no depen de com la base de dades retorni les files.
    position: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    package: Mapped[SubmissionPackage] = relationship(back_populates="items")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SubmissionPackageItem {self.filename} {self.document_sha256[:8]}>"


class SubmissionPackageApproval(UUIDPrimaryKeyMixin, CompanyOwnedMixin, Base):
    """L'aprovacio humana d'UNA revisio concreta. **Immutable i append-only.**

    Guarda el `manifest_hash` que es va aprovar, no nomes l'identificador del
    paquet. Aixi, si algun dia el paquet i l'aprovacio deixessin de quadrar,
    seria detectable en comptes de silencios.

    No te `updated_at`: aquesta fila no es modifica mai. Rebutjar despres
    d'aprovar afegeix una entrada nova.
    """

    __tablename__ = "submission_package_approvals"
    __table_args__ = (
        sa.Index("ix_submission_package_approvals_package", "package_id", "approved_at"),
    )

    package_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("submission_packages.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: El hash aprovat. Si el paquet en tingues un altre, no serien el mateix.
    package_manifest_hash: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    decision: Mapped[PackageApprovalDecision] = mapped_column(
        enum_column(PackageApprovalDecision, "package_approval_decision"), nullable=False
    )
    approved_by: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    comment: Mapped[str | None] = mapped_column(sa.Text)

    #: Que es veia a la pantalla en aquell moment. Sense aixo, l'historial no diu
    #: si la persona va aprovar amb la informacio a la vista.
    readiness_snapshot: Mapped[WorkspaceReadiness] = mapped_column(
        enum_column(WorkspaceReadiness, "workspace_readiness"),
        nullable=False,
        default=WorkspaceReadiness.NOT_STARTED,
    )
    blockers_snapshot: Mapped[list[Any] | None] = mapped_column(sa.JSON)

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    package: Mapped[SubmissionPackage] = relationship(back_populates="approvals")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SubmissionPackageApproval {self.decision} by={self.approved_by}>"
