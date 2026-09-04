"""Base declarativa i mixins compartits.

Decisio: s'utilitzen nomes tipus SQLAlchemy portables (Uuid, Numeric, Date...).
Aixo permet executar la suite de tests sobre SQLite en memoria mentre que
produccio corre sobre PostgreSQL. Quan calguin tipus especifics de PG
(pgvector, JSONB, tsvector) s'introduiran de forma aillada i els seus tests
requeriran Postgres.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Convencio de noms perque Alembic pugui generar migracions estables
# (constraints amb nom deterministic en comptes de noms generats per la BD).
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


class ActorMixin:
    """Auditoria basica: qui va crear i qui va modificar per ultim cop.

    No es event sourcing. Nomes deixa rastre de l'ultim autor conegut.
    """

    @declared_attr
    @classmethod
    def created_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )

    @declared_attr
    @classmethod
    def updated_by(cls) -> Mapped[uuid.UUID | None]:
        return mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        )


class CompanyOwnedMixin:
    """Tota entitat corporativa penja d'una Company. Mai s'assumeix un unic tenant."""

    @declared_attr
    @classmethod
    def company_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


def enum_column(values: type, name: str, **kwargs) -> sa.Enum:
    """Enum portable: es materialitza com a VARCHAR + CHECK, no com a tipus natiu de PG.

    Motiu: modificar un ENUM natiu de PostgreSQL en una migracio es incomode i
    poc reversible; un CHECK constraint es trivial de canviar.
    """
    return sa.Enum(
        values,
        name=name,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda enum_cls: [member.value for member in enum_cls],
        **kwargs,
    )
