"""D'on surt un fet: la provinença canònica d'Sprint 3.

Una cita ha de poder dir, d'aqui a dos anys, **quins bytes exactes** sostenien
una afirmacio: quina versio del fitxer, i —quan n'hi ha text— quina pagina de
quina extraccio. I ho ha de poder dir encara que despres arribi una versio nova,
es torni a extreure amb un parser millor, o el document vigent ja sigui un altre.

Per aixo **no s'ancora a `TenderDocument`**: aquella fila es la ranura logica
—«el PCAP d'aquest expedient»— i el seu contingut canvia. L'evidencia immutable
es `TenderDocumentFile` (una versio concreta) o `TenderDocumentExtraction` +
`TenderDocumentPage` (una lectura concreta). `tender_document_id` hi es nomes
per poder ensenyar un nom, i per aixo es `SET NULL`: si desapareix, la cita
segueix sent reconstruible.

**Es immutable.** No hi ha PATCH, ni «mou-la a la versio vigent», ni cap
sincronitzacio. Si una entitat necessita citar una altra cosa, es crea una
referencia nova i es canvia el seu enllac; l'antiga conserva que es va fer
servir aquell dia. La distincio es tot el sentit d'aquesta taula:

    document vigent  !=  document que es va fer servir com a evidencia

`RequirementEvidence` no es toca: continua sent la provinenca dels requisits
EXTRETS, amb les seves garanties. Aixo es una via generica ADDICIONAL per a tot
el que alla no cap —un XLSX sense pagines, un requisit escrit a ma, una
dimensio, un dubte— i que arribara a S3-S5 i mes enlla.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import SourceRefKind

#: Els CHECK porten nom perque una fila impossible ha de ser rebutjable per la
#: base de dades, no nomes per Pydantic: qui escrigui per SQL cru tambe hi passa.
_ANCHORED = (
    "(ref_kind = 'extracted_page' AND extraction_id IS NOT NULL AND page_id IS NOT NULL)"
    " OR (ref_kind = 'document_file' AND tender_document_file_id IS NOT NULL)"
)
_PAGE_ONE_BASED = "page_number IS NULL OR page_number >= 1"
_CHAR_SPAN = (
    "(char_start IS NULL AND char_end IS NULL)"
    " OR (char_start IS NOT NULL AND char_end IS NOT NULL"
    " AND char_start >= 0 AND char_end > char_start)"
)


class TenderSourceRef(UUIDPrimaryKeyMixin, Base):
    """Una cita immutable a una font concreta d'una licitacio.

    No te `company_id`: hereta la visibilitat de la licitacio, exactament com
    els plecs. Posar-n'hi un obriria la porta a que una cita digues que es d'una
    empresa i el document d'una altra.

    Tampoc te `updated_at` ni `archived_at`: una fila que no canvia mai no en
    necessita, i tenir-los convidaria a modificar-la.
    """

    __tablename__ = "tender_source_refs"
    __table_args__ = (
        sa.CheckConstraint(_ANCHORED, name="source_ref_is_anchored"),
        sa.CheckConstraint(_PAGE_ONE_BASED, name="source_ref_page_is_one_based"),
        sa.CheckConstraint(_CHAR_SPAN, name="source_ref_char_span_is_coherent"),
        sa.Index("ix_tender_source_refs_tender_id", "tender_id"),
        sa.Index("ix_tender_source_refs_content_sha256", "content_sha256"),
    )

    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("tenders.id", ondelete="CASCADE"),
        nullable=False,
    )
    ref_kind: Mapped[SourceRefKind] = mapped_column(
        enum_column(SourceRefKind, "source_ref_kind"), nullable=False
    )

    # --- Ancoratge: una lectura concreta ------------------------------------ #
    #
    # RESTRICT i no CASCADE: l'evidencia citada no s'ha de poder esborrar per
    # accident. Si algu vol esborrar-la, que primer s'encarregui de les cites.
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_document_extractions.id",
            ondelete="RESTRICT",
            name="fk_tender_source_refs_extraction",
        ),
    )
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_document_pages.id",
            ondelete="RESTRICT",
            name="fk_tender_source_refs_page",
        ),
    )
    #: 1-based, com a tot arreu. Es desa encara que es pugui derivar: una cita
    #: ha de poder dir "pagina 27" sense haver de resoldre res.
    page_number: Mapped[int | None] = mapped_column(sa.Integer)

    # --- Ancoratge: una versio concreta ------------------------------------- #
    tender_document_file_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_document_files.id",
            ondelete="RESTRICT",
            name="fk_tender_source_refs_file",
        ),
    )
    #: NOMES per ensenyar un nom. No es l'ancora, i per aixo pot desapareixer
    #: sense que la cita deixi de ser reconstruible.
    tender_document_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_documents.id",
            ondelete="SET NULL",
            name="fk_tender_source_refs_document",
        ),
    )

    #: Desnormalitzat a proposit: els bytes exactes han de ser identificables
    #: sense dependre de que sigui vigent despres. El deriva el servidor del
    #: contingut real; el client no hi mana.
    content_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    #: On mirar, quan no hi ha pagina: "full Calculs, fila 42", "Annex A".
    location_label: Mapped[str | None] = mapped_column(sa.String(200))
    quote: Mapped[str | None] = mapped_column(sa.Text)
    #: Nomes per a `extracted_page`: el tros exacte dins del text de la pagina.
    char_start: Mapped[int | None] = mapped_column(sa.Integer)
    char_end: Mapped[int | None] = mapped_column(sa.Integer)

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="SET NULL", name="fk_tender_source_refs_author"),
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TenderSourceRef {self.ref_kind} {self.content_sha256[:12]}>"
