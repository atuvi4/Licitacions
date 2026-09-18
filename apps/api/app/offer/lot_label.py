"""Com s'anomena un lot dins d'una oferta economica (C-13).

Modul pur, com `pricing.py`: no hi entra cap model ni cap sessio. El lot hi
arriba com qualsevol objecte que tingui `lot_number`, `external_lot_id` i
`title`, que es el que en fa servir la resta del projecte.

El label es una **fotografia**: es calcula un sol cop, quan es crea la fila
economica, i es desa. No es recalcula en llegir. Si despres el plec canvia el
titol del lot, la fila conserva el que deia el dia que algu la va triar; aixo es
el que permet llegir mes tard una oferta i veure-hi el que es va oferir, i no el
que el plec diu ara.

La logica viu aqui i no repartida pels endpoints perque els tres camins de
creacio de C-1 —el workspace lligat a un lot, el workspace global sense lots del
plec i la seleccio explicita— han de produir exactament el mateix text.
"""

from __future__ import annotations

from typing import Protocol

#: Sense lots al plec, l'oferta es una de sola i te aquest nom.
GLOBAL_LABEL = "Oferta global"

#: Quan la font no dona ni numero, ni codi, ni titol. Es diu que falta, i no
#: s'hi posa l'UUID intern: no es una informacio que cap persona pugui fer servir.
UNIDENTIFIED_LABEL = "Lot sense identificador"

#: L'amplada de `EconomicOfferLot.label`.
LABEL_MAX_LENGTH = 300


class TenderLotLike(Protocol):
    """El minim d'un lot del plec per poder-lo anomenar."""

    lot_number: int | None
    external_lot_id: str | None
    title: str | None


def _clean(value: object) -> str | None:
    """El text util, o res. `""` i `"   "` son el mateix que no tenir-ne."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def economic_offer_lot_label(lot: TenderLotLike | None) -> str:
    """El nom humà d'un bloc de preus. `None` vol dir oferta global.

    Sense identificador no s'escriu «Lot» davant del titol: seria inventar-ne un
    que la font no dona. I no es mira si el titol ja diu «Lot», perque endevinar
    el que ja hi ha escrit fa mes mal que repetir-ho.
    """
    if lot is None:
        return GLOBAL_LABEL

    # `lot_number = 0` es un lot. Comprovar-ne la veracitat el faria desapareixer.
    number = lot.lot_number
    identifier = str(number) if number is not None else _clean(lot.external_lot_id)
    title = _clean(lot.title)

    if identifier and title:
        label = f"Lot {identifier} · {title}"
    elif identifier:
        label = f"Lot {identifier}"
    elif title:
        label = title
    else:
        label = UNIDENTIFIED_LABEL

    # Qui decideix que hi cap es el domini, no un error de la base per llargada.
    return label[:LABEL_MAX_LENGTH]
