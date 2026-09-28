"""Estructura canonica de puntuacio de Palamos (2024-3137) per a l'empresa QA. DRY-RUN per defecte.

Sobre la base dedicada `licitacions_review_palamos` i NOMES per a l'empresa
"Tuvi QA Review" (la de la importacio de requisits):

1. materialitza els 23 criteris persistits de la run final com a elements
   `award_criterion` (una font primaria cadascun, extraccio intacta);
2. assigna a cadascun el paper del pla (`set_award_role`, una revisio cadascun);
3. crea les 12 relacions estructurades del pla.

Resultat esperat a la vista de puntuacio: 9 criteris, 100 punts (preu 80 + 8
files qualitatives que sumen 20). Assignar papers NO verifica cap criteri: tots
continuen en l'estat en que neixen, i la formula del preu continua "pendent de
verificar".

**Decisions del pla.**

* El bloc "Criteris qualitatius" (20) es `parent_total` i `parent_of` de les 8
  files: el total del bloc ja es la suma dels fills i no es compta.
* Les dues files de l'Informe que repeteixen una fila puntuada del PCAP (minuts
  de mobil, suport en catala) conserven el paper `scored` que tenen al plec, pero
  son `restatement_of` de la fila del PCAP: la vista de puntuacio les plega i
  les assenyala (`restatement_excluded`) en lloc d'amagar-les.
* El bloc de l'Informe "fins a 20 punts" es `parent_total` (es el mateix bloc) i
  `restatement_of` del bloc del PCAP: plegat, no compta dues vegades.
* El model d'oferta del Sobre 2 (SI/NO) es `offer_form_view` i `restatement_of`
  del bloc qualitatiu. Entre les relacions existents es la que millor ho diu: es
  el MATEIX bloc repetit en un altre lloc del plec (el formulari), no un duplicat
  a fusionar (`duplicate_of`) ni una relacio nomes informativa (`related_to`,
  que no plegaria). El paper ja l'exclou del total; el plegament ho reforca.
* Informatius (7) -> `not_scored`; desitjables tecnics (2) -> `desirable`. Cap
  paper no surt de "es valorara": surt d'aquest pla, revisat a ma.

Tot en UNA transaccio; qualsevol error ho desfa sencer. Tornar-ho a executar un
cop aplicat no escriu res (`complete`); un estat que no es el del pla atura.
La DSN surt de `DATABASE_URL` (entorn del proces); no es mostra mai.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parent))
import import_palamos_review as base  # noqa: E402

PACKAGE = "palamos_award_structure"
EXPECTED_CRITERIA = 23
EXPECTED_REQUIREMENT_ITEMS = 257
EXPECTED_SCORED = 9
EXPECTED_POINTS = 100.0
#: Avisos que ha de donar, i nomes aquests: formula del preu i les 2 reiteracions.
EXPECTED_CHECKS = frozenset({"formula_not_evidence_verified", "restatement_excluded"})

PRICE = "Criteri preu"
QUALITATIVE = "Criteris qualitatius"
INFORME_QUALITATIVE = "Criteris qualitatius (fins a 20 punts)"
OFFER_FORM = "Criteris qualitatius del model d'oferta (Sobre 2) - opcions SI/NO"
MOBILE = "Minuts mensuals per mòbil 20000 minuts"
CATALAN = "Comptar amb centre de suport i atenció en català"
INFORME_MOBILE = "Servei de trànsit de veu de mòbils - Minuts mensuals per mòbil 20000 minuts"
INFORME_CATALAN = "Suport - Comptar amb centre de suport i atenció en català"

#: Les 8 files puntuades del bloc qualitatiu (PCAP): 5+4+3+2+2+2+1+1 = 20.
CHILDREN: dict[str, float] = {
    "Accés a Internet descentralitzat xDSL/GPON 1Gbps simètric als equipaments municipals": 5.0,
    "Tarifa 1 a 20 GB de dades": 4.0,
    "Tarifa 2 a dades il·limitades": 3.0,
    "Accés a Internet descentralitzat xDSL/GPON 1Gbps a les fibres individuals": 2.0,
    "Ampliació bonus tarifaris de trànsit de fixos - 15.000 minuts a destinacions fixes "
    "nacionals": 2.0,
    "Ampliació bonus tarifaris de trànsit de fixos - 15.000 minuts a destinacions mòbils "
    "nacionals": 2.0,
    MOBILE: 1.0,
    CATALAN: 1.0,
}
INFORMATIVE = (
    "Pluralitat de criteris d'adjudicació: preu i criteris qualitatius",
    "Compliment de prestacions de caràcter qualitatiu puntuades a l'oferta",
    "Preferència en cas d'empat per treballadors amb discapacitat",
    "Criteris d'adjudicació (component preu i criteris qualitatius) - baixa anormal",
    "Criteris d'adjudicació susceptibles de ser aplicats mitjançant fórmules",
    "Backup 4G/5G per serveis de veu",
    "Cost total del servei (estudi econòmic)",
)
DESIRABLE = ("Auriculars biaurals amb connexió jack", "Eficiència energètica dels telèfons")

#: nom exacte -> (punts persistits, paper).
ROLE_PLAN: dict[str, tuple[float | None, str]] = {
    PRICE: (80.0, "scored"),
    QUALITATIVE: (20.0, "parent_total"),
    **{name: (points, "scored") for name, points in CHILDREN.items()},
    INFORME_QUALITATIVE: (20.0, "parent_total"),
    INFORME_MOBILE: (1.0, "scored"),
    INFORME_CATALAN: (1.0, "scored"),
    OFFER_FORM: (None, "offer_form_view"),
    **{name: (None, "not_scored") for name in INFORMATIVE},
    **{name: (None, "desirable") for name in DESIRABLE},
}
#: (de, a, tipus, nota). Llegiu-ho "de <tipus> a".
RELATION_PLAN: list[tuple[str, str, str, str | None]] = [
    *[(QUALITATIVE, child, "parent_of", None) for child in CHILDREN],
    (INFORME_QUALITATIVE, QUALITATIVE, "restatement_of", "Informe de necessitats"),
    (INFORME_MOBILE, MOBILE, "restatement_of", "Informe de necessitats"),
    (INFORME_CATALAN, CATALAN, "restatement_of", "Informe de necessitats"),
    (OFFER_FORM, QUALITATIVE, "restatement_of", "Model d'oferta del Sobre 2 (SI/NO)"),
]


class AlreadyAppliedError(RuntimeError):
    """L'estructura ja hi es: no s'escriu res."""


def validate_plan() -> None:
    """El pla mateix: 23 criteris, 9 que compten, 100 punts, 20 = suma dels fills."""
    problems = []
    if len(ROLE_PLAN) != EXPECTED_CRITERIA:
        problems.append(f"{len(ROLE_PLAN)} criteris al pla")
    folded = {a for a, _b, kind, _n in RELATION_PLAN if kind == "restatement_of"}
    counting = [n for n, (_p, role) in ROLE_PLAN.items() if role == "scored" and n not in folded]
    points = sum(ROLE_PLAN[n][0] or 0 for n in counting)
    if (len(counting), points) != (EXPECTED_SCORED, EXPECTED_POINTS):
        problems.append(f"el pla compta {len(counting)} criteris i {points} punts")
    if sum(CHILDREN.values()) != ROLE_PLAN[QUALITATIVE][0]:
        problems.append("el bloc qualitatiu no es la suma dels fills")
    if problems:
        raise base.ImportGuardError("Pla incoherent: " + "; ".join(problems))


async def resolve_plan(session: AsyncSession, tender_id: uuid.UUID) -> dict[str, Any]:
    """Cada nom del pla -> EXACTAMENT un criteri de la run vigent, amb els punts esperats."""
    from app.services.review_awards import current_award_criteria

    criteria = await current_award_criteria(session, tender_id)
    by_name: dict[str, list[Any]] = {}
    for criterion in criteria:
        by_name.setdefault(criterion.name, []).append(criterion)
    problems = []
    if len(criteria) != EXPECTED_CRITERIA:
        problems.append(f"la run vigent te {len(criteria)} criteris")
    resolved = {}
    for name, (points, _role) in ROLE_PLAN.items():
        found = by_name.get(name, [])
        if len(found) != 1:
            problems.append(f"{len(found)} criteris amb el nom d'un element del pla")
            continue
        if found[0].points != points:
            problems.append("punts diferents dels esperats en un criteri del pla")
            continue
        resolved[name] = found[0]
    if set(by_name) - set(ROLE_PLAN):
        problems.append("hi ha criteris a la run que el pla no cobreix")
    if problems:
        raise base.ImportGuardError("Resolucio del pla: " + "; ".join(problems))
    return resolved


async def award_status(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> str:
    """`none`, `complete` (el pla ja aplicat) o `unknown` (un estat que no es el del pla)."""
    from app.models.enums import ReviewItemKind
    from app.models.review import ReviewItem, ReviewItemRelation

    items = (await session.scalars(sa.select(ReviewItem).where(
        ReviewItem.company_id == company_id, ReviewItem.tender_id == tender_id,
        ReviewItem.kind == ReviewItemKind.AWARD_CRITERION))).all()
    if not items:
        return "none"
    names = {i.id: (i.accepted_json or {}).get("name") for i in items}
    roles = {names[i.id]: (i.award_role.value if i.award_role else None) for i in items}
    relations = (await session.execute(
        sa.select(ReviewItemRelation.from_item_id, ReviewItemRelation.to_item_id,
                  ReviewItemRelation.relation_type)
        .join(ReviewItem, ReviewItem.id == ReviewItemRelation.from_item_id)
        .where(ReviewItem.company_id == company_id, ReviewItem.tender_id == tender_id,
               ReviewItem.kind == ReviewItemKind.AWARD_CRITERION,
               ReviewItemRelation.archived_at.is_(None)))).all()
    edges = Counter((names.get(a), names.get(b), t.value) for a, b, t in relations)
    expected = Counter((a, b, kind) for a, b, kind, _n in RELATION_PLAN)
    if (len(items) == EXPECTED_CRITERIA
            and roles == {n: role for n, (_p, role) in ROLE_PLAN.items()}
            and edges == expected):
        return "complete"
    return "unknown"


async def execute_award_review(
    session: AsyncSession,
    *,
    company_id: uuid.UUID,
    actor_id: uuid.UUID,
    tender_id: uuid.UUID,
    batch_id: uuid.UUID,
    expected_requirements: int = EXPECTED_REQUIREMENT_ITEMS,
) -> dict[str, Any]:
    """Materialitza, assigna papers i relaciona. NO fa commit: tot o res el decideix qui crida."""
    from app.models.enums import AuditAction, ReviewItemKind, ReviewRelationType
    from app.models.review import ReviewItem, ReviewItemSource
    from app.services import audit, review_actions, review_awards, review_relations

    validate_plan()
    status = await award_status(session, company_id, tender_id)
    if status == "complete":
        raise AlreadyAppliedError("L'estructura de criteris ja hi es.")
    if status != "none":
        raise base.ImportGuardError("Hi ha criteris de revisio que no son els del pla.")
    requirements = await session.scalar(sa.select(sa.func.count()).select_from(ReviewItem).where(
        ReviewItem.company_id == company_id, ReviewItem.tender_id == tender_id,
        ReviewItem.kind == ReviewItemKind.REQUIREMENT))
    if requirements != expected_requirements:
        raise base.ImportGuardError(
            f"L'empresa te {requirements} requisits de revisio; se n'esperen "
            f"{expected_requirements}.")
    resolved = await resolve_plan(session, tender_id)
    provenance = {"package": PACKAGE, "import_batch_id": str(batch_id)}

    async def audit_row(action, entity_type, entity_id, summary):
        await audit.record(session, company_id=company_id, user_id=actor_id, action=action,
                           entity_type=entity_type, entity_id=entity_id,
                           summary=f"lot={batch_id} {summary}")

    started = await review_awards.start_award_review(
        session, company_id=company_id, tender_id=tender_id, actor_id=actor_id,
        provenance=provenance)
    if started.created != EXPECTED_CRITERIA:
        raise base.ImportGuardError(f"S'han materialitzat {started.created} criteris.")
    await audit_row(AuditAction.CREATE, "review", tender_id,
                    f"inici_revisio_criteris elements_creats={started.created}")
    item_of = dict((await session.execute(
        sa.select(ReviewItemSource.award_criterion_id, ReviewItemSource.item_id).where(
            ReviewItemSource.company_id == company_id,
            ReviewItemSource.award_criterion_id.in_([c.id for c in resolved.values()]))
    )).all())
    items = {name: item_of[criterion.id] for name, criterion in resolved.items()}

    for name, (_points, role) in ROLE_PLAN.items():
        item = await review_actions.apply_review_action(
            session, company_id=company_id, tender_id=tender_id, item_id=items[name],
            actor_id=actor_id, expected_revision=1, action="set_award_role",
            payload={"role": role}, provenance=provenance)
        await audit_row(AuditAction.UPDATE, "review_item", item.id,
                        f"element={item.id} accio=set_award_role revisio={item.revision} "
                        f"paper={role}")
    relations = []
    for source, target, kind, note in RELATION_PLAN:
        relation = await review_relations.create_relation(
            session, company_id=company_id, tender_id=tender_id, from_item_id=items[source],
            to_item_id=items[target], relation_type=ReviewRelationType(kind), note=note,
            actor_id=actor_id)
        relations.append(relation)
        await audit_row(AuditAction.CREATE, "review_relation", relation.id,
                        f"relacio={relation.id} tipus={kind} de={items[source]} "
                        f"a={items[target]}")
    await session.flush()
    return {"items": items, "relations": [r.id for r in relations], "created": started.created}


async def verify_award_review(
    session: AsyncSession, company_id: uuid.UUID, tender_id: uuid.UUID
) -> dict[str, Any]:
    """Llegeix el que ha quedat, amb el mateix servei que la API."""
    from app.services.review_awards import list_award_criteria

    scoring = await list_award_criteria(
        session, company_id=company_id, tender_id=tender_id, view="scoring")
    everything = await list_award_criteria(
        session, company_id=company_id, tender_id=tender_id, view="all")
    price = next(c for c in everything.criteria if c.name == PRICE)
    parent = next(c for c in everything.criteria if c.name == QUALITATIVE)
    return {
        "award_items": len(everything.criteria),
        "scored_count": scoring.scored_count,
        "total_points": scoring.total_points,
        "scored_points": sorted((c.points for c in scoring.criteria), reverse=True),
        "children_sum": sum(c.points or 0 for c in parent.children),
        "roles": dict(Counter(c.role for c in everything.criteria)),
        "counting": sum(c.counts_in_total for c in everything.criteria),
        "checks": sorted(c.code for c in scoring.checks),
        "price_formula_status": price.formula_status,
        "price_formula_label": price.formula_label,
        "verified": sum(c.verification_status == "verified" for c in everything.criteria),
        "statuses": dict(Counter(c.verification_status for c in everything.criteria)),
    }


def assert_verified(checks: dict[str, Any]) -> None:
    problems = []
    if (checks["award_items"], checks["scored_count"], checks["total_points"]) != (
            EXPECTED_CRITERIA, EXPECTED_SCORED, EXPECTED_POINTS):
        problems.append("recomptes de puntuacio")
    if checks["children_sum"] != ROLE_PLAN[QUALITATIVE][0]:
        problems.append("suma dels fills")
    if set(checks["checks"]) != EXPECTED_CHECKS:
        problems.append(f"avisos {checks['checks']}")
    if checks["price_formula_status"] != "not_evidence_verified" or checks["verified"]:
        problems.append("estat de verificacio")
    if problems:
        raise base.ImportGuardError("Verificacio: " + "; ".join(problems))


def write_outputs(out_dir: Path, view: Any) -> None:
    """Nomes identificadors, noms, punts i papers: cap cita del plec."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "palamos_award_structure.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["canonical_id", "name", "points", "role", "counts_in_total",
                         "parent", "folded_into", "formula_status"])
        for c in view.criteria:
            writer.writerow([c.canonical_id, c.name, c.points, c.role, c.counts_in_total,
                             c.parent.name if c.parent else "",
                             c.folded_into.name if c.folded_into else "", c.formula_status or ""])


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--write", action="store_true",
                        help="Aplica l'estructura (una sola transaccio, tot o res).")
    return parser


async def run(args: argparse.Namespace) -> int:
    try:
        url = base.validate_target_url(os.environ.get("DATABASE_URL"))
        validate_plan()
    except base.ImportGuardError as exc:
        print(f"Refusat: {exc}")
        return 2
    connect_args = {} if args.write else {
        "server_settings": {"default_transaction_read_only": "on"}}
    engine = create_async_engine(url, connect_args=connect_args)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with factory() as session:
            try:
                await base.check_state(session)
                found = await base.find_qa_tenant(session)
                if found is None:
                    raise base.ImportGuardError("No hi ha l'empresa QA de la importacio.")
                company, actor = found
                await resolve_plan(session, base.TENDER_ID)
            except base.ImportGuardError as exc:
                print(f"Refusat: {exc}")
                return 2
            status = await award_status(session, company.id, base.TENDER_ID)
            print(f"AWARD STATUS: {status}")
            if not args.write:
                await session.rollback()
                print("Pla resolt: 23/23 criteris; 12 relacions. Dry-run: cap escriptura.")
                return 0
            return await _write(session, args, company, actor)
    finally:
        await engine.dispose()


async def _write(session, args, company, actor) -> int:
    batch_id = uuid.uuid4()
    try:
        await execute_award_review(session, company_id=company.id, actor_id=actor.id,
                                   tender_id=base.TENDER_ID, batch_id=batch_id)
        checks = await verify_award_review(session, company.id, base.TENDER_ID)
        assert_verified(checks)
        await session.commit()
    except AlreadyAppliedError:
        await session.rollback()
        print("Ja aplicat: no s'ha escrit res.")
        return 0
    except Exception as exc:  # noqa: BLE001 - tot o res, i el motiu surt sense secrets
        await session.rollback()
        print(f"Aplicacio desfeta sencera ({type(exc).__name__}): {exc}")
        return 1
    if args.out_dir:
        from app.services.review_awards import list_award_criteria

        write_outputs(args.out_dir, await list_award_criteria(
            session, company_id=company.id, tender_id=base.TENDER_ID, view="all"))
    print(f"Empresa QA: {company.id}")
    print(f"Lot: {batch_id}")
    for key, value in checks.items():
        print(f"{key}: {value}")
    return 0


def main() -> int:
    return asyncio.run(run(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
