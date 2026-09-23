"""La porta final: recull els tres dominis i els passa a l'agregador pur.

Cap dels tres es recalcula aqui. El dossier el dona `workspace_dossier`, els
numeros de l'oferta els dona el mateix `build_view()` que veu l'API, i la
vigencia de cada validacio la dona el servei de solucio tecnica. Aquesta capa
nomes tradueix fets a codis.

**El que no hi entra, i es deliberat**: cost, marge, markup i els comptadors que
en parlen. Son informacio de gestio, no condicions per poder presentar. Si hi
fossin, la readiness s'hauria de redactar per rol i tornariem a obrir el forat
que l'oferta economica ja va tancar; i, a mes, dirien que una candidatura no es
pot presentar per un motiu que no te res a veure amb presentar-la.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.enums import PricingBasis, TechnicalSolutionSource, TechnicalSolutionStatus
from app.models.workspace import BidWorkspace
from app.services import dimensions as dimension_service
from app.services import document_review, requirement_analysis
from app.services import economic_offer as offer_service
from app.services import solution_options as option_service
from app.services import technical_scope as scope_service
from app.services import technical_solution as solution_service
from app.services import workspace_dossier as dossier_service
from app.workspace.readiness import (
    BLOCKER,
    DIMENSION_DISPUTED,
    DOMAIN_ECONOMIC,
    DOMAIN_TECHNICAL,
    DOMAIN_WORKSPACE,
    ECONOMIC_BID_LOTS_NOT_SELECTED,
    ECONOMIC_DURATION_MISSING,
    ECONOMIC_LOT_BUDGET_EXCEEDED,
    ECONOMIC_LOT_BUDGET_UNKNOWN,
    ECONOMIC_LOT_EMPTY,
    ECONOMIC_OFFER_NOT_STARTED,
    ECONOMIC_OFFER_PRICE_MISSING,
    ECONOMIC_UNIT_LIMIT_EXCEEDED,
    ECONOMIC_UNIT_LIMIT_UNKNOWN,
    ECONOMIC_VAT_MISSING,
    INFO,
    REQUIREMENT_ANALYSIS_PREDATES_DOCUMENTS,
    SOLUTION_OPTION_QUOTE_PENDING,
    TECHNICAL_SCOPE_STALE,
    TECHNICAL_SCOPE_UNREVIEWED,
    TECHNICAL_SOLUTION_REQUIREMENT_MISSING,
    TECHNICAL_SOLUTION_UNVALIDATED,
    TECHNICAL_SOLUTION_VALIDATION_STALE,
    TENDER_DOCUMENTS_CHANGED,
    TENDER_DOCUMENTS_UNREVIEWED,
    WARNING,
    FinalReadiness,
    Finding,
    evaluate_final,
)
from app.workspace.technical_solution import CONTENT_CHANGED, REQUIREMENT_CHANGED

#: Les bases que depenen de la durada. Amb `one_off` i `per_unit` no fa falta.
_TIME_BASED = frozenset({PricingBasis.MONTHLY, PricingBasis.ANNUAL})

#: Motius de caducitat que parlen de la feina, no d'una fila impossible de
#: reparar. `requirement_missing` no hi es a proposit: veure el seu avis.
_STALE_BLOCKING = frozenset({CONTENT_CHANGED, REQUIREMENT_CHANGED})


# --------------------------------------------------------------------------- #
# Solucio tecnica                                                               #
# --------------------------------------------------------------------------- #


async def technical_findings(
    session: AsyncSession,
    workspace: BidWorkspace,
    scope: scope_service.TechnicalScope | None = None,
) -> list[Finding]:
    """Que falta de la solucio tecnica perque la candidatura pugui tancar-se.

    La primera pregunta no es "estan validades les solucions?" sino "algu ha
    mirat quines calen?" (C-14). Sense aquesta revisio, zero solucions no vol dir
    res, i per tant no pot voler dir "tot correcte".
    """
    scope = scope or await scope_service.resolve(session, workspace)
    findings: list[Finding] = []

    # Una discrepancia de dimensionament es tecnica: parla de com es dimensiona
    # el que s'oferira, no dels papers ni del preu.
    for dimension in await dimension_service.disputed_dimensions(session, workspace):
        findings.append(
            Finding(
                code=DIMENSION_DISPUTED,
                severity=WARNING,
                domain=DOMAIN_TECHNICAL,
                entity_id=dimension.id,
                context={"key": dimension.key, "scope_label": dimension.scope_label},
            )
        )

    # Una opcio triada que espera cotitzacio es una gestio pendent, no un
    # error: avisa i prou.
    for option in await option_service.options_awaiting_quote(session, workspace):
        findings.append(
            Finding(
                code=SOLUTION_OPTION_QUOTE_PENDING,
                severity=WARNING,
                domain=DOMAIN_TECHNICAL,
                entity_id=option.id,
                context={
                    "supplier_name": option.supplier_name,
                    "product_name": option.product_name,
                    "quote_status": option.quote_status.value,
                },
            )
        )

    review = await scope_service.review_state(session, workspace, scope)
    if not review.reviewed:
        findings.append(
            Finding(
                code=TECHNICAL_SCOPE_UNREVIEWED,
                severity=BLOCKER,
                domain=DOMAIN_TECHNICAL,
                context={"requirements_in_scope": len(scope.requirements)},
            )
        )
    elif review.stale:
        findings.append(
            Finding(
                code=TECHNICAL_SCOPE_STALE,
                severity=BLOCKER,
                domain=DOMAIN_TECHNICAL,
                context={"requirements_in_scope": len(scope.requirements)},
            )
        )

    solutions = await solution_service.list_solutions(session, workspace)
    stale = await solution_service.stale_reasons_for(session, solutions)
    in_scope = {requirement.id for requirement in scope.requirements}

    for solution in solutions:
        if solution.source == TechnicalSolutionSource.REQUIREMENT:
            if solution.requirement_id is None:
                # La fila deia que resolia un requisit que ja no hi es. No es pot
                # reparar —no hi ha ni arxivat ni reenllac— i per aixo avisa en
                # comptes de bloquejar: qui vigila aquest cas de debo es la
                # caducitat de l'abast, que si que es resoluble.
                findings.append(
                    Finding(
                        code=TECHNICAL_SOLUTION_REQUIREMENT_MISSING,
                        severity=WARNING,
                        domain=DOMAIN_TECHNICAL,
                        entity_id=solution.id,
                    )
                )
                continue
            if solution.requirement_id not in in_scope:
                # Fora de l'abast d'ara: es historia i no demana res.
                continue

        if solution.status != TechnicalSolutionStatus.VALIDATED:
            findings.append(
                Finding(
                    code=TECHNICAL_SOLUTION_UNVALIDATED,
                    severity=BLOCKER,
                    domain=DOMAIN_TECHNICAL,
                    entity_id=solution.id,
                    context={"status": str(solution.status)},
                )
            )
            continue

        motius = [m for m in stale.get(solution.id, ()) if m in _STALE_BLOCKING]
        if motius:
            findings.append(
                Finding(
                    code=TECHNICAL_SOLUTION_VALIDATION_STALE,
                    severity=BLOCKER,
                    domain=DOMAIN_TECHNICAL,
                    entity_id=solution.id,
                    context={"reasons": sorted(motius)},
                )
            )

    return findings


# --------------------------------------------------------------------------- #
# Oferta economica                                                              #
# --------------------------------------------------------------------------- #


async def economic_findings(
    session: AsyncSession, workspace: BidWorkspace
) -> list[Finding]:
    """Que falta de l'oferta per poder presentar-la.

    Nomes fets de presentacio. Que el marge sigui dolent o que no s'hagi escrit
    cap cost intern no impedeix presentar res: aixo ho mira qui pot veure-ho, a
    la propia oferta.
    """
    try:
        offer = await offer_service.get_offer(session, workspace)
    except NotFoundError:
        # C-15: a la V1, tota candidatura que vulgui arribar a la revisio final
        # necessita oferta. L'absencia no vol dir mai "no cal".
        return [
            Finding(
                code=ECONOMIC_OFFER_NOT_STARTED, severity=BLOCKER, domain=DOMAIN_ECONOMIC
            )
        ]

    view = await offer_service.build_view(session, workspace, offer)
    lots = offer_service.active_views(view.lots)

    if view.shape.selectable and not lots:
        return [
            Finding(
                code=ECONOMIC_BID_LOTS_NOT_SELECTED,
                severity=BLOCKER,
                domain=DOMAIN_ECONOMIC,
            )
        ]

    findings: list[Finding] = []
    for lot_view in lots:
        findings.extend(_lot_findings(lot_view))
    return findings


def _lot_findings(lot_view) -> list[Finding]:
    findings: list[Finding] = []
    lot_id = lot_view.lot.id

    line_views = [
        line
        for section in offer_service.active_views(lot_view.sections)
        for line in offer_service.active_views(section.lines)
    ]
    if not line_views:
        return [
            Finding(
                code=ECONOMIC_LOT_EMPTY,
                severity=BLOCKER,
                domain=DOMAIN_ECONOMIC,
                entity_id=lot_id,
            )
        ]

    sense_maxim = 0
    for view in line_views:
        line = view.line
        if line.offered_unit_price_ex_vat is None:
            findings.append(
                Finding(
                    code=ECONOMIC_OFFER_PRICE_MISSING,
                    severity=BLOCKER,
                    domain=DOMAIN_ECONOMIC,
                    entity_id=line.id,
                )
            )
        if line.pricing_basis in _TIME_BASED and view.effective_duration_months is None:
            findings.append(
                Finding(
                    code=ECONOMIC_DURATION_MISSING,
                    severity=BLOCKER,
                    domain=DOMAIN_ECONOMIC,
                    entity_id=line.id,
                    context={"pricing_basis": str(line.pricing_basis)},
                )
            )
        if view.totals.exceeds_unit_max is True:
            findings.append(
                Finding(
                    code=ECONOMIC_UNIT_LIMIT_EXCEEDED,
                    severity=BLOCKER,
                    domain=DOMAIN_ECONOMIC,
                    entity_id=line.id,
                )
            )
        elif view.totals.exceeds_unit_max is None:
            sense_maxim += 1
        if line.vat_rate is None:
            findings.append(
                Finding(
                    code=ECONOMIC_VAT_MISSING,
                    severity=WARNING,
                    domain=DOMAIN_ECONOMIC,
                    entity_id=line.id,
                )
            )

    if sense_maxim:
        # Agregat al lot i no una vegada per linia: es un fet del plec —no en
        # tenim el maxim— i repetir-lo cinquanta vegades ofegaria la llista.
        findings.append(
            Finding(
                code=ECONOMIC_UNIT_LIMIT_UNKNOWN,
                severity=INFO,
                domain=DOMAIN_ECONOMIC,
                entity_id=lot_id,
                context={"lines": sense_maxim},
            )
        )

    if lot_view.limit.exceeds_lot_max is True:
        findings.append(
            Finding(
                code=ECONOMIC_LOT_BUDGET_EXCEEDED,
                severity=BLOCKER,
                domain=DOMAIN_ECONOMIC,
                entity_id=lot_id,
            )
        )
    elif lot_view.limit.tender_max is None:
        # Desconegut. Mai "dins del pressupost".
        findings.append(
            Finding(
                code=ECONOMIC_LOT_BUDGET_UNKNOWN,
                severity=INFO,
                domain=DOMAIN_ECONOMIC,
                entity_id=lot_id,
            )
        )

    return findings


# --------------------------------------------------------------------------- #
# Els plecs (S3-S3)                                                             #
# --------------------------------------------------------------------------- #


async def document_findings(
    session: AsyncSession, workspace: BidWorkspace
) -> list[Finding]:
    """Que se sap dels plecs: si algu se'ls ha mirat i si l'analisi els cobreix.

    Son TRES preguntes separades i cap d'elles no diu que els requisits hagin
    canviat. Un document que acaba d'arribar nomes diu que hi ha material per
    llegir; saber si el que demana l'organ es un altre exigeix tornar a
    extreure'l, i aixo es una accio a part que ningu no fa sol.

    Sense plecs no hi ha cap finding: "cap document per revisar" no es un
    problema pendent, es que no hi ha res. Dir-hi "pendent de revisio" seria
    demanar a algu que confirmi el buit.
    """
    findings: list[Finding] = []
    review = await document_review.review_state(session, workspace)

    if review.document_count == 0:
        return findings

    if not review.reviewed:
        findings.append(
            Finding(
                code=TENDER_DOCUMENTS_UNREVIEWED,
                severity=INFO,
                domain=DOMAIN_WORKSPACE,
                context={"documents": review.document_count},
            )
        )
    elif review.stale:
        findings.append(
            Finding(
                code=TENDER_DOCUMENTS_CHANGED,
                severity=WARNING,
                domain=DOMAIN_WORKSPACE,
                context={"documents": review.document_count},
            )
        )

    if await _analysis_predates_documents(session, workspace):
        findings.append(
            Finding(
                code=REQUIREMENT_ANALYSIS_PREDATES_DOCUMENTS,
                severity=WARNING,
                domain=DOMAIN_TECHNICAL,
                context={"documents": review.document_count},
            )
        )
    return findings


async def _analysis_predates_documents(
    session: AsyncSession, workspace: BidWorkspace
) -> bool:
    """Si l'analisi vigent no cobreix el corpus documental d'ara.

    **No es compara cap data.** Es compara el corpus: `input_fingerprint` d'una
    run diu exactament sobre quines extraccions es va fer, i `analysis_input`
    recalcula la mateixa empremta amb el que hi ha ara. Dues dates poden
    ordenar-se be i parlar de coses diferents —un document reextret amb un
    parser nou no mou cap `created_at` de la run—, i aquesta comparacio no te
    aquest problema.

    Si no hi ha cap run vigent NO es diu res per aqui: que no s'hagi analitzat
    mai ja te el seu propi finding al dossier, i repetir-ho seria soroll sobre
    el mateix fet.

    Si ara mateix no hi ha res analitzable —nomes XLSX, per exemple— tampoc:
    l'analisi vigent no queda desfasada perque hagi arribat un document del qual
    no sabem llegir el text.
    """
    run = await requirement_analysis.current_run_for(session, workspace.tender_id)
    if run is None:
        return False
    current = await requirement_analysis.analysis_input(session, workspace.tender_id)
    if not current.documents:
        return False
    return current.fingerprint != run.input_fingerprint


# --------------------------------------------------------------------------- #
# El veredicte                                                                  #
# --------------------------------------------------------------------------- #


async def final_readiness(
    session: AsyncSession, workspace: BidWorkspace
) -> FinalReadiness:
    """L'unic veredicte que val per arribar a la revisio final.

    El dossier es consumeix tal com el diu el dossier: els seus blocadors
    —inclosos `decision_not_go` i `analysis_outdated`— hi entren una sola vegada
    i no es tornen a avaluar aqui.
    """
    dossier = await dossier_service.evaluate_workspace_readiness(session, workspace)
    scope = await scope_service.resolve(session, workspace)
    # Els findings dels plecs es reparteixen entre dos dominis a proposit: que
    # ningu no els hagi revisat parla de la candidatura sencera, i que l'analisi
    # no cobreixi el corpus d'ara parla del tecnic.
    documents = await document_findings(session, workspace)
    return evaluate_final(
        dossier=dossier,
        technical=[
            *(f for f in documents if f.domain == DOMAIN_TECHNICAL),
            *await technical_findings(session, workspace, scope),
        ],
        economic=await economic_findings(session, workspace),
        workspace=[f for f in documents if f.domain == DOMAIN_WORKSPACE],
    )
