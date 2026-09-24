"""Qui pot veure quina licitacio. Una regla, escrita una sola vegada.

    visible per a l'empresa C  <=>  owner_company_id IS NULL  O  owner_company_id = C

`NULL` vol dir publica: es el que son totes les licitacions que arriben d'una
font sindicada, i el que continuaran sent. Amb propietari, nomes la veu qui la
va introduir.

Hi ha dues variants perque hi ha dos tipus de peticio, no dues regles:

* les rutes que pengen de `/companies/{company_id}` saben de quina empresa
  parlen i fan servir `visible_to_company`;
* el feed de licitacions no en penja —no hi ha `company_id` enlloc de la ruta— i
  l'unic que en sap es l'usuari. Alla es fa servir `visible_to_user`, que admet
  el que es public i el que pertany a alguna empresa de la qual l'usuari es
  membre. **No s'hi inventa cap empresa**: es consulta la pertinenca real.

La mateixa regla val per a `Buyer`: un organisme introduit a ma per una
empresa es seu fins que algu no el publiqui, i publicar-lo en silenci al
cataleg global seria decidir-ho per ella.

Aquest modul NO es una capa de permisos generica. Nomes sap d'aquesta regla, i
existeix perque no acabi escrita a ma a dotze consultes, que es com es perden
els aillaments: no a la que s'escriu malament, sino a la que algu oblida.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from app.models.identity import CompanyMembership
from app.models.tenders import Buyer, Tender


def visible_to_company(company_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """Condicio `WHERE` per a una empresa concreta."""
    return sa.or_(
        Tender.owner_company_id.is_(None),
        Tender.owner_company_id == company_id,
    )


def visible_to_user(user_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """Condicio `WHERE` per a un usuari, sigui de les empreses que sigui.

    Es una subconsulta i no un `JOIN` perque la condicio s'afegeix a consultes
    que ja porten els seus propis filtres, ordres i paginacio: un `JOIN` hi
    podria duplicar files segons les pertinences de l'usuari, i el recompte de
    les facetes deixaria de quadrar amb la llista.
    """
    companies = sa.select(CompanyMembership.company_id).where(
        CompanyMembership.user_id == user_id
    )
    return sa.or_(
        Tender.owner_company_id.is_(None),
        Tender.owner_company_id.in_(companies),
    )


def buyer_visible_to_company(company_id: uuid.UUID) -> sa.ColumnElement[bool]:
    """La mateixa regla, per als organismes de contractacio.

    Els que arriben d'una font sindicada tenen `owner_company_id` a `NULL` i
    son de tothom. Els que escriu una empresa al seu wizard son nomes seus, i
    dues empreses poden tenir cadascuna el seu «Ajuntament de Palamos» sense
    que cap de les dues vegi el de l'altra: la identitat d'un Buyer es
    `(source_id, external_identifier)`, i el manual porta un token propi.
    """
    return sa.or_(
        Buyer.owner_company_id.is_(None),
        Buyer.owner_company_id == company_id,
    )
