"""Palamós (2024-3137): les tres files de l'assegurança de 500.000 EUR. DRY-RUN per defecte.

Per què n'hi ha tres (demostrat amb l'evidència desada, no amb els títols):

* **PCAP p. 7**, clàusula 13 b) «Obligacions essencials»: la clàusula del plec vinculant.
* **Informe de necessitats p. 6**, apartat 14 b): el mateix text, en un altre
  document. És una REITERACIÓ del PCAP (`restatement_of`).
* Una segona fila de l'Informe amb EXACTAMENT el mateix tros de la mateixa
  pàgina: la pàgina 6 és a dos chunks (19 i 20) i el model la va extreure dues
  vegades. És un DUPLICAT d'extracció (`duplicate_of`).

El canònic és la fila del PCAP (clàusula del document vinculant; les cites són
idèntiques). Només ell rep el valor: `≥ 500000 EUR`, perquè la clàusula diu
«import mínim de 500.000 euros». L'estat de verificació NO canvia.

Amb `--write`: les 2 relacions i 1 correcció, en UNA transacció, amb l'actor QA
de la importació. Tornar-ho a executar un cop fet no escriu res.
La DSN surt de `DATABASE_URL` (entorn del procés); no es mostra mai.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_palamos_review as base  # noqa: E402

PACKAGE = "palamos_insurance_resolution"
#: Elements de revisió de l'empresa QA (ids canònics).
PCAP_ITEM = uuid.UUID("51253be2-f8aa-4a2c-93c9-c3ee4d0c1f7f")
INFORME_ITEM = uuid.UUID("a1e0def8-0ae3-4228-bb03-08d9ab9e0fbc")
INFORME_DUPLICATE = uuid.UUID("1fca3109-b7f3-45de-9549-fd8d848a3565")
ITEMS = (PCAP_ITEM, INFORME_ITEM, INFORME_DUPLICATE)
EXPECTED_RELATIONS = {
    (INFORME_DUPLICATE, INFORME_ITEM, "duplicate_of"),
    (INFORME_ITEM, PCAP_ITEM, "restatement_of"),
}
#: La frase de la clàusula que justifica l'operador «≥».
BINDING_PHRASE = "import mínim de 500.000 euros"
VALUE = {"operator": ">=", "value": 500000, "unit_label": "EUR"}
NOTE = "Import mínim de la clàusula 13 b) del PCAP (p. 7): «import mínim de 500.000 euros»."


class ResolutionError(RuntimeError):
    """El que diu l'evidència no és el que s'esperava: no es fa res."""


async def load_candidates(session: AsyncSession, company_id: uuid.UUID, item_ids) -> list[Any]:
    """Cada element amb la seva font primària i l'evidència, tal com és a la base."""
    from app.models.requirements import Requirement, RequirementEvidence
    from app.models.review import ReviewItemSource
    from app.models.tenders import TenderDocument
    from app.services.review_duplicates import Candidate, EvidenceSpan

    candidates = []
    for item_id in item_ids:
        requirement_id = await session.scalar(
            sa.select(ReviewItemSource.requirement_id).where(
                ReviewItemSource.item_id == item_id,
                ReviewItemSource.company_id == company_id,
                ReviewItemSource.link_role == "primary",
                ReviewItemSource.unlinked_at.is_(None),
            )
        )
        if requirement_id is None:
            raise ResolutionError(f"L'element {item_id} no té font extreta en aquesta empresa.")
        requirement = await session.get(Requirement, requirement_id)
        rows = (
            await session.execute(
                sa.select(RequirementEvidence, TenderDocument.document_type)
                .outerjoin(
                    TenderDocument, TenderDocument.id == RequirementEvidence.tender_document_id
                )
                .where(RequirementEvidence.requirement_id == requirement_id)
            )
        ).all()
        candidates.append(
            Candidate(
                item_id=item_id,
                source_chunk_index=requirement.source_chunk_index,
                evidence=tuple(
                    EvidenceSpan(
                        page_id=e.page_id,
                        document_type=getattr(kind, "value", kind),
                        page_number=e.page_number,
                        char_start=e.char_start,
                        char_end=e.char_end,
                        section=e.section,
                        quote=e.quote,
                    )
                    for e, kind in rows
                ),
            )
        )
    return candidates


async def status(
    session: AsyncSession,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    canonical_id: uuid.UUID,
    relations: set[tuple],
) -> str:
    """`none`, `complete` (relacions i valor ja hi són) o `unknown` (un altre estat)."""
    from app.models.review import ReviewItem, ReviewItemRelation

    rows = (
        await session.execute(
            sa.select(
                ReviewItemRelation.from_item_id,
                ReviewItemRelation.to_item_id,
                ReviewItemRelation.relation_type,
            ).where(
                ReviewItemRelation.company_id == company_id,
                ReviewItemRelation.tender_id == tender_id,
                ReviewItemRelation.archived_at.is_(None),
                sa.or_(*[ReviewItemRelation.from_item_id == a for a, _b, _t in relations]),
            )
        )
    ).all()
    present = {(a, b, t.value) for a, b, t in rows}
    item = await session.get(ReviewItem, canonical_id)
    values = (item.accepted_json or {}).get("values") or []
    corrected = any(
        v.get("value") == VALUE["value"] and v.get("unit_label") == VALUE["unit_label"]
        for v in values
    )
    if not present and not corrected:
        return "none"
    if present == relations and corrected:
        return "complete"
    return "unknown"


async def resolve(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    tender_id: uuid.UUID,
    item_ids,
    actor_id: uuid.UUID | None,
    batch_id: uuid.UUID,
    value: dict[str, Any],
    note: str,
) -> dict[str, Any]:
    """Relaciona segons l'evidència i corregeix NOMÉS el canònic. Sense commit."""
    from app.models.enums import AuditAction, ReviewRelationType
    from app.models.review import ReviewItem
    from app.schemas.review import RequirementSnapshot
    from app.services import audit, review_actions, review_relations
    from app.services.review_duplicates import plan_relations

    candidates = await load_candidates(session, company_id, item_ids)
    chosen, planned = plan_relations(candidates)
    provenance = {"package": PACKAGE, "import_batch_id": str(batch_id)}

    async def audit_row(action, entity_type, entity_id, summary):
        await audit.record(
            session,
            company_id=company_id,
            user_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=f"lot={batch_id} {summary}",
        )

    created = []
    for relation in planned:
        row = await review_relations.create_relation(
            session,
            company_id=company_id,
            tender_id=tender_id,
            from_item_id=relation.from_item,
            to_item_id=relation.to_item,
            relation_type=ReviewRelationType(relation.relation_type),
            note="Mateixa obligació segons l'evidència (resolució de l'assegurança).",
            actor_id=actor_id,
        )
        created.append(row)
        await audit_row(
            AuditAction.CREATE,
            "review_relation",
            row.id,
            f"relacio={row.id} tipus={relation.relation_type} de={relation.from_item} "
            f"a={relation.to_item}",
        )

    item = await session.get(ReviewItem, chosen.item_id)
    snapshot = RequirementSnapshot.model_validate(item.accepted_json)
    taken = {v.key for v in snapshot.values}
    key = next(f"reviewer_value_{n}" for n in range(1, 1000) if f"reviewer_value_{n}" not in taken)
    status_before = item.verification_status.value
    updated = await review_actions.apply_review_action(
        session,
        company_id=company_id,
        tender_id=tender_id,
        item_id=chosen.item_id,
        actor_id=actor_id,
        expected_revision=item.revision,
        action="correct",
        payload={
            "values": [{"key": key, **value}],
            "verification_status": status_before,
            "note": note,
        },
        provenance=provenance,
    )
    await audit_row(
        AuditAction.UPDATE,
        "review_item",
        updated.id,
        f"element={updated.id} accio=correct revisio={updated.revision} "
        f"estat={updated.verification_status.value}",
    )
    await session.flush()
    return {
        "canonical": chosen.item_id,
        "relations": [(r.from_item, r.to_item, r.relation_type) for r in planned],
        "status_before": status_before,
        "status_after": updated.verification_status.value,
        "revision": updated.revision,
    }


def assert_palamos_plan(chosen_id, relations, canonical_quote: str) -> None:
    """La decisió concreta de Palamós, comprovada abans d'escriure res."""
    from app.services.review_duplicates import normalise_quote

    if chosen_id != PCAP_ITEM:
        raise ResolutionError("El canònic calculat no és la fila del PCAP.")
    if {(a, b, t) for a, b, t in relations} != EXPECTED_RELATIONS:
        raise ResolutionError("Les relacions calculades no són les esperades.")
    if BINDING_PHRASE not in normalise_quote(canonical_quote):
        raise ResolutionError("La cita canònica no diu «import mínim de 500.000 euros».")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="Aplica (una transacció, tot o res).")
    return parser


async def run(args: argparse.Namespace) -> int:
    from app.models.identity import Company, User
    from app.services.review_duplicates import plan_relations

    try:
        url = base.validate_target_url(os.environ.get("DATABASE_URL"))
    except base.ImportGuardError as exc:
        print(f"Refusat: {exc}")
        return 2
    connect_args = (
        {} if args.write else {"server_settings": {"default_transaction_read_only": "on"}}
    )
    engine = create_async_engine(url, connect_args=connect_args)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                await base.check_state(session)
                company = await session.scalar(
                    sa.select(Company).where(Company.legal_name == base.QA_COMPANY_NAME)
                )
                actor = await session.scalar(
                    sa.select(User).where(User.email == base.QA_ACTOR_EMAIL)
                )
                if company is None or actor is None:
                    raise ResolutionError("No hi ha l'empresa o l'actor QA de la importació.")
                candidates = await load_candidates(session, company.id, ITEMS)
                chosen, planned = plan_relations(candidates)
                relations = [(r.from_item, r.to_item, r.relation_type) for r in planned]
                assert_palamos_plan(chosen.item_id, relations, chosen.evidence[0].quote)
            except (base.ImportGuardError, ResolutionError) as exc:
                print(f"Refusat: {exc}")
                return 2
            state = await status(
                session, company.id, base.TENDER_ID, chosen.item_id, EXPECTED_RELATIONS
            )
            print(f"canonical={chosen.item_id}")
            for relation in relations:
                print(f"relation {relation[2]}: {relation[0]} -> {relation[1]}")
            print(f"value: {VALUE}")
            print(f"STATUS: {state}")
            if not args.write:
                await session.rollback()
                print("Dry-run: cap escriptura.")
                return 0
            if state == "complete":
                print("Ja resolt: no s'ha escrit res.")
                return 0
            if state != "none":
                print("Refusat: hi ha un estat parcial que no és el d'aquesta resolució.")
                return 3
            batch_id = uuid.uuid4()
            try:
                result = await resolve(
                    session,
                    company_id=company.id,
                    tender_id=base.TENDER_ID,
                    item_ids=ITEMS,
                    actor_id=actor.id,
                    batch_id=batch_id,
                    value=VALUE,
                    note=NOTE,
                )
                await session.commit()
            except Exception as exc:  # noqa: BLE001 - tot o res, i el motiu surt sense secrets
                await session.rollback()
                print(f"Desfet sencer ({type(exc).__name__}): {exc}")
                return 1
            print(f"Lot: {batch_id}")
            for key, value in result.items():
                print(f"{key}: {value}")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
