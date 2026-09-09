"""Com un cas del gold es converteix en el Tender real que s'analitzara.

Existeix perque aixo estava escrit DUES vegades i les dues versions havien
divergit: el benchmark resolia per `tender_external_id` i, si no, per
expedient; la pagina de revisio humana resolia nomes per expedient.

Amb expedients unics la diferencia no es nota. Amb un expedient repetit —i
`1/2026` ho esta, el fan servir tres organs diferents— cada versio anava a un
tender distint: la persona verificava la cita contra un document i el benchmark
n'hauria mesurat un altre. La metrica hauria semblat una mesura del model quan
en realitat mesurava una resolucio incorrecta del corpus.

Per aixo la regla viu aqui i en un sol lloc. Qui necessiti saber quin document
mira un cas del gold, ho ha de preguntar a aquesta funcio.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation.dataset import GoldCase
from app.models.tenders import Tender


async def resolve_tender(session: AsyncSession, case: GoldCase) -> Tender | None:
    """El Tender d'un cas del gold.

    `tender_external_id` mana: es l'identificador a la font i es unic. L'expedient
    nomes s'hi fa servir de reserva, perque **no es unic**: organs diferents
    numeren els seus expedients per separat i "1/2026" existeix diverses vegades.
    """
    if case.tender_external_id:
        tender = await session.scalar(
            sa.select(Tender).where(Tender.external_id == case.tender_external_id)
        )
        if tender is not None:
            return tender
    if case.expediente_number:
        return await session.scalar(
            sa.select(Tender)
            .where(Tender.expediente_number == case.expediente_number)
            # Sense ordre explicit, dues execucions podrien triar tenders
            # diferents del mateix expedient i donar metriques diferents amb la
            # mateixa entrada.
            .order_by(Tender.created_at.asc(), Tender.id.asc())
        )
    return None
