"""Imports d'un expedient obtinguts d'una font externa acreditada (F-06a, 0063).

`tender_amount_observations` desa, nomes afegint, cada import que una font externa
(p. ex. les dades obertes de la PSCP) publica per a un expedient o per a un lot, amb la
procedencia completa: quina font, quina fila, quan es va consultar, la consulta exacta i
el hash del cos original. **No toca mai `tenders.base_budget`** ni cap altre import de la
licitacio: la font de la licitacio continua sent la que era, i aixo n'es una observacio
a part.

Global (cap `company_id`): l'import es de l'expedient, el mateix per a totes les
empreses.

Vigencia: `valid_to` NULL = vigent. Una observacio nova no edita l'anterior: la TANCA
(`valid_to`) i se n'afegeix una altra. Es l'unic canvi admes; a PostgreSQL un trigger
refusa qualsevol altre UPDATE, i tambe un DELETE directe (una cascada des de la
licitacio o des del lot si que passa), igual que a `review_item_revisions`.

Unicitat: una sola observacio `accepted` vigent per (expedient, lot o expedient, tipus
d'import, font); una `conflict`, `no_value`, `superseded` o una de tancada no hi compten.
I una sola observacio vigent (de qualsevol estat) per a cada fila de la font i cada
objectiu (expedient, lot o expedient, tipus, font, `source_row_id`): un refresc que porta
una lectura diferent tanca l'anterior abans d'afegir-ne una, i mai no se n'acumulen dues
de vigents de la mateixa fila.

Import i estat son independents: `amount` es el que es publica i l'estat, el resultat
de la validacio. `accepted` sempre amb `amount > 0`; `no_value` nomes amb NULL o el 0
publicat; `superseded` i `conflict` admeten NULL (la fila no publica import, o el que
publica no es pot desar sense alterar-lo: mes de 2 decimals, que `Numeric(18,2)`
arrodoniria sense avisar i la llicencia no permet) o qualsevol import >= 0.

Evidencia: `evidence_url` es la consulta curta i exacta de la fila (o files) d'aquella
observacio, que es pot tornar a demanar (com a molt 1024 caracters); la peticio per lots
d'on surt queda referenciada per `evidence_sha256` i `raw_storage_key`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin, enum_column
from app.models.enums import AmountObservationStatus, TenderAmountKind
from app.models.review import JSONDocument, in_values

TABLE = "tender_amount_observations"

_PROVENANCE_JSON = sa.JSON(none_as_null=True).with_variant(
    postgresql.JSONB(none_as_null=True), "postgresql"
)

#: L'expedient sencer (lot NULL) ocupa la unicitat amb aquest valor. El literal sense
#: `::uuid` es portable: PostgreSQL l'infereix de `tender_lot_id` (l'index queda amb el
#: cast), i a SQLite (UUID en hex sense guions) no pot coincidir mai amb cap lot real.
NIL_LOT = "'00000000-0000-0000-0000-000000000000'"
CURRENT_ACCEPTED = "valid_to IS NULL AND validation_status = 'accepted'"
CURRENT = "valid_to IS NULL"
#: Regles de l'import (el mateix text que la migracio 0063).
ACCEPTED_AMOUNT_POSITIVE = "validation_status <> 'accepted' OR (amount IS NOT NULL AND amount > 0)"
NO_VALUE_AMOUNT = "validation_status <> 'no_value' OR amount IS NULL OR amount = 0"
EVIDENCE_URL_MAX = 1024
AMOUNT_NON_NEGATIVE = "amount IS NULL OR amount >= 0"

#: Nomes es pot TANCAR una observacio vigent (`valid_to` de NULL a un valor), sense
#: tocar cap altra columna. `to_jsonb(NEW) - 'valid_to'` compara totes les altres,
#: tambe les que afegeixi una migracio futura. Un DELETE directe es refusa; el que
#: arriba per una cascada (licitacio o lot) s'executa dins del trigger d'integritat
#: referencial, a `pg_trigger_depth() >= 2`, i passa.
APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION tender_amount_observations_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF pg_trigger_depth() < 2 THEN
            RAISE EXCEPTION 'tender_amount_observations es nomes d''afegir (%)', TG_OP
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL
       AND (to_jsonb(NEW) - 'valid_to') = (to_jsonb(OLD) - 'valid_to') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'tender_amount_observations nomes admet tancar valid_to (%)', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql
"""
APPEND_ONLY_TRIGGER = """
CREATE TRIGGER tender_amount_observations_append_only
BEFORE UPDATE OR DELETE ON tender_amount_observations
FOR EACH ROW EXECUTE FUNCTION tender_amount_observations_append_only()
"""


class TenderAmountObservation(UUIDPrimaryKeyMixin, Base):
    """Un import publicat per una font externa, per a un expedient o un lot."""

    __tablename__ = TABLE
    __table_args__ = (
        sa.Index("ix_tender_amount_observations_tender_id", "tender_id"),
        sa.Index(
            "uq_tender_amount_observations_current",
            "tender_id",
            sa.text(f"coalesce(tender_lot_id, {NIL_LOT})"),
            "amount_kind",
            "source_code",
            unique=True,
            postgresql_where=sa.text(CURRENT_ACCEPTED),
            sqlite_where=sa.text(CURRENT_ACCEPTED),
        ),
        sa.Index(
            "uq_tender_amount_observations_current_row",
            "tender_id",
            sa.text(f"coalesce(tender_lot_id, {NIL_LOT})"),
            "amount_kind",
            "source_code",
            "source_row_id",
            unique=True,
            postgresql_where=sa.text(CURRENT),
            sqlite_where=sa.text(CURRENT),
        ),
        sa.CheckConstraint(in_values("amount_kind", TenderAmountKind), name="amount_kind_valid"),
        sa.CheckConstraint(
            in_values("validation_status", AmountObservationStatus),
            name="validation_status_valid",
        ),
        sa.CheckConstraint(AMOUNT_NON_NEGATIVE, name="amount_non_negative"),
        sa.CheckConstraint(ACCEPTED_AMOUNT_POSITIVE, name="accepted_amount_positive"),
        sa.CheckConstraint(NO_VALUE_AMOUNT, name="no_value_amount"),
        sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="validity_ordered"),
        # `length` i no `char_length`: SQLite no te `char_length`, i a PostgreSQL sobre
        # text son el mateix (caracters, no bytes).
        sa.CheckConstraint("length(evidence_sha256) = 64", name="evidence_sha256_length"),
        sa.CheckConstraint("length(currency) = 3", name="currency_length"),
        sa.CheckConstraint(
            f"length(evidence_url) <= {EVIDENCE_URL_MAX}", name="evidence_url_length"
        ),
    )

    # Noms de FK explicits, els mateixos que la migracio 0063.
    tender_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tenders.id", ondelete="CASCADE", name="fk_tender_amount_observations_tender_id"
        ),
        nullable=False,
    )
    #: NULL = l'expedient sencer.
    tender_lot_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "tender_lots.id",
            ondelete="CASCADE",
            name="fk_tender_amount_observations_tender_lot_id",
        ),
    )
    amount_kind: Mapped[TenderAmountKind] = mapped_column(
        enum_column(TenderAmountKind, "tender_amount_kind"), nullable=False
    )
    #: Tal com el publica la font, al centim. NULL si no es publica (o no es pot desar
    #: sense alterar-lo); l'estat diu el resultat de la validacio, no si hi ha import.
    amount: Mapped[Decimal | None] = mapped_column(sa.Numeric(18, 2))
    currency: Mapped[str] = mapped_column(
        sa.String(3), nullable=False, default="EUR", server_default="EUR"
    )
    #: Font i dataset (`pscp_open_data:ybgg-dgi6`).
    source_code: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    #: L'expedient o el lot a la font (`<id_intern>` o `<id_intern>#lot=<numero_lot>`;
    #: tambe un lot de la font sense lot a la base, amb `tender_lot_id` NULL).
    source_entity_key: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: La fila concreta de la font (`id_intern`). Un import d'expedient llegit de les
    #: files de lot porta `<id_intern>_*`, i la llista completa de files va a `provenance`.
    source_row_id: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: Fase de publicacio de la fila (`fase_publicacio`), si la font en dona.
    source_phase: Mapped[str | None] = mapped_column(sa.String(80))
    #: Darrera actualitzacio del dataset en llegir-lo (`rowsUpdatedAt`).
    source_dataset_updated_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    #: La consulta curta i exacta de la fila (o files) d'aquesta observacio a la font, que
    #: es pot tornar a demanar. La peticio per lots d'on surt: `evidence_sha256` i
    #: `raw_storage_key` (i la seva URL completa, al registre del run).
    evidence_url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    #: SHA-256 (hex) del cos original de la peticio per lots, abans de redactar-lo.
    evidence_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: Resposta desada (redactada) a l'emmagatzematge d'objectes, si n'hi ha.
    raw_storage_key: Mapped[str | None] = mapped_column(sa.String(512))
    validation_status: Mapped[AmountObservationStatus] = mapped_column(
        enum_column(AmountObservationStatus, "amount_observation_status"), nullable=False
    )
    #: Resultat de cada guarda (organ, net, vec, lots...). Determinista: dues lectures
    #: amb el mateix resultat donen el mateix document (l'adaptador ho compara).
    validation: Mapped[dict[str, Any]] = mapped_column(
        JSONDocument, nullable=False, default=dict, server_default=sa.text("'{}'")
    )
    #: D'on surt l'import: camp d'origen, valor publicat en text, fila(es) (`:id`,
    #: `:updated_at`, organ, fase, enllacos), dataset i `rowsUpdatedAt`, llicencia i
    #: atribucio. Sense valor per defecte: qui escriu l'ha de donar (`none_as_null`: un
    #: `None` de Python es un NULL de SQL i el NOT NULL el refusa, no un `null` JSON).
    provenance: Mapped[dict[str, Any]] = mapped_column(_PROVENANCE_JSON, nullable=False)
    valid_from: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


# La migracio 0063 crea el mateix; aixo cobreix les bases creades des dels models
# (`metadata.create_all`, el gate PostgreSQL dels tests).
_observations = TenderAmountObservation.__table__
sa.event.listen(
    _observations,
    "after_create",
    # `DDL` aplica format `%`: el `%` del missatge de RAISE s'ha d'escapar.
    sa.DDL(APPEND_ONLY_FUNCTION.replace("%", "%%")).execute_if(dialect="postgresql"),
)
sa.event.listen(
    _observations,
    "after_create",
    sa.DDL(APPEND_ONLY_TRIGGER).execute_if(dialect="postgresql"),
)
