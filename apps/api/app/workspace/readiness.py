"""La porta final: dossier, solucio tecnica i oferta economica, en un sol lloc.

Modul pur i **agregador**. No calcula cap dels tres dominis: els rep ja avaluats
i els posa junts. `app/workspace/dossier.py` segueix sent el dossier i no sap que
existeix aquest fitxer.

Tres regles que el sostenen:

**Una sola porta.** `ready_for_final_review` vol dir que cap dels tres dominis no
te res que ho impedeixi. Un dossier impecable amb l'oferta a mitges no es una
candidatura a punt, i deixar-ho passar seria tenir dues portes que diuen coses
diferents.

**Codis, no frases.** Com a la resta del projecte, el servidor envia codis i la
interficie en deriva el text. Aqui no hi ha missatges, i menys encara res que
soni a assessorament: el sistema constata fets —"el preu ofert supera el maxim
transcrit"— i qui decideix que fer-ne es una persona.

**Res del que es intern.** Cost, marge i markup no arriben mai en aquesta capa.
No perque siguin poc importants, sino perque son informacio de gestio i no
condicions per poder presentar; si hi fossin, la readiness s'hauria de redactar
per rol i tornariem a obrir el forat que l'oferta economica ja va tancar.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.models.enums import WorkspaceReadiness
from app.workspace.dossier import ReadinessResult

# --------------------------------------------------------------------------- #
# Vocabulari                                                                    #
# --------------------------------------------------------------------------- #

#: Impedeix arribar a la revisio final.
BLOCKER = "blocker"
#: Val la pena mirar-ho, pero no impedeix res.
WARNING = "warning"
#: Un fet que el sistema no sap. Mai vol dir "correcte".
INFO = "info"

DOMAIN_WORKSPACE = "workspace"
DOMAIN_DOSSIER = "dossier"
DOMAIN_TECHNICAL = "technical"
DOMAIN_ECONOMIC = "economic"

# --- Abast tecnic (C-14) ---------------------------------------------------- #

#: Ningu no ha dit encara quins requisits necessiten solucio tecnica. Zero
#: solucions no vol dir "cap en necessita" fins que algu ho afirma.
TECHNICAL_SCOPE_UNREVIEWED = "technical_scope_unreviewed"
#: Es va revisar, pero l'abast d'ara ja no es aquell.
TECHNICAL_SCOPE_STALE = "technical_scope_stale"

# --- Plecs: revisio humana (S3-S3) -------------------------------------------- #
#
# Tres preguntes diferents que abans no es podien distingir. Les dues primeres
# son DOCUMENTALS —parlen del que hi ha per llegir— i la tercera parla de
# l'analisi. Cap de les tres no diu que els requisits hagin canviat: aixo nomes
# es pot saber despres de tornar-los a extreure.

#: Ningu no ha confirmat mai haver mirat els plecs que hi ha. No vol dir que hi
#: hagi res malament: vol dir que no se n'ha dit res, i callar no es aprovar.
TENDER_DOCUMENTS_UNREVIEWED = "tender_documents_unreviewed"
#: Algu els va revisar, pero el conjunt d'ara ja no es aquell: hi ha documents
#: nous o versions noves des d'aleshores.
TENDER_DOCUMENTS_CHANGED = "tender_documents_changed"
#: L'analisi vigent es va fer sobre un corpus que ja no es l'actual. NO diu que
#: els requisits hagin canviat —aixo no es pot saber sense tornar a extreure—,
#: nomes que encara no cobreix el que hi ha.
REQUIREMENT_ANALYSIS_PREDATES_DOCUMENTS = "requirement_analysis_predates_documents"

# --- Solucions tecniques ----------------------------------------------------- #

TECHNICAL_SOLUTION_UNVALIDATED = "technical_solution_unvalidated"
TECHNICAL_SOLUTION_VALIDATION_STALE = "technical_solution_validation_stale"
#: La fila deia que resolia un requisit i aquell requisit ja no hi es. Avis i no
#: blocador: S3 conserva la fila amb `SET NULL` i no hi ha ni arxivat ni
#: reenllac, aixi que bloquejar per aixo crearia un estat que ningu no pot
#: resoldre. El control fort es la revisio de l'abast, que si que caduca.
TECHNICAL_SOLUTION_REQUIREMENT_MISSING = "technical_solution_requirement_missing"

# --- Dimensionament (S3-S6) --------------------------------------------------- #

#: Dues fonts diuen numeros diferents i ningu no ha dit quin val. Es AVIS i no
#: blocador: que hi hagi una discrepancia no impedeix presentar-se, i
#: convertir-ho en porta tancada obligaria a "resoldre" dimensions que potser
#: no afecten el que s'ofereix. Qui ho ha de veure, ho veu.
#:
#: Una dimensio NOMES `undecided` no genera res: no saber un numero que ningu
#: no ha contradit no es cap problema pendent.
DIMENSION_DISPUTED = "dimension_disputed"

# --- Oferta economica --------------------------------------------------------- #

#: Regla de producte de la V1 (C-15): tota candidatura que vulgui arribar a la
#: revisio final necessita oferta. L'absencia no vol dir mai "no cal".
ECONOMIC_OFFER_NOT_STARTED = "economic_offer_not_started"
ECONOMIC_BID_LOTS_NOT_SELECTED = "economic_bid_lots_not_selected"
ECONOMIC_LOT_EMPTY = "economic_lot_empty"
ECONOMIC_OFFER_PRICE_MISSING = "economic_offer_price_missing"
#: Base mensual o anual sense durada efectiva: el total es indeterminat i no es
#: pot presentar un preu que no se sap quant suma (C-3).
ECONOMIC_DURATION_MISSING = "economic_duration_missing"
ECONOMIC_UNIT_LIMIT_EXCEEDED = "economic_unit_limit_exceeded"
ECONOMIC_LOT_BUDGET_EXCEEDED = "economic_lot_budget_exceeded"
ECONOMIC_VAT_MISSING = "economic_vat_missing"
#: Desconegut, i per tant mai "dins del limit".
ECONOMIC_UNIT_LIMIT_UNKNOWN = "economic_unit_limit_unknown"
ECONOMIC_LOT_BUDGET_UNKNOWN = "economic_lot_budget_unknown"

#: Els dos blocadors del dossier que en realitat parlen de la candidatura
#: sencera. Es reclassifiquen, no es tornen a calcular.
_WORKSPACE_CODES = frozenset({"decision_not_go", "analysis_outdated"})


@dataclass(frozen=True, slots=True)
class Finding:
    """Un fet, amb el seu codi. Cap text i cap xifra interna."""

    code: str
    severity: str
    domain: str
    entity_id: uuid.UUID | None = None
    #: Context factual minim: comptadors i identificadors, mai contingut escrit.
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FinalReadiness:
    """El veredicte agregat i, sobretot, per que."""

    #: L'estat del dossier tal com el diu el dossier. No es sobreescriu.
    dossier_state: WorkspaceReadiness
    findings: tuple[Finding, ...]

    @property
    def ready(self) -> bool:
        """A punt vol dir que no hi ha CAP blocador. Els avisos no impedeixen res."""
        return not self.blockers

    @property
    def blockers(self) -> tuple[str, ...]:
        return tuple(f.code for f in self.findings if f.severity == BLOCKER)

    def by_domain(self, domain: str) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.domain == domain)

    def as_readiness_result(self) -> ReadinessResult:
        """El veredicte final amb la forma que ja consumeix la resta del sistema.

        Es el pont que evita una segona transicio: qui avui decideix si un
        workspace pot estar a `ready_for_final_review` segueix rebent un
        `ReadinessResult`, pero ara el que li arriba ja compta els tres dominis.
        """
        if self.ready:
            return ReadinessResult(WorkspaceReadiness.READY_FOR_FINAL_REVIEW, ())
        if self.dossier_state == WorkspaceReadiness.NOT_STARTED:
            state = WorkspaceReadiness.NOT_STARTED
        elif self.dossier_state == WorkspaceReadiness.NEEDS_REVIEW and set(
            self.blockers
        ) <= set(self.dossier_state_blockers()):
            state = WorkspaceReadiness.NEEDS_REVIEW
        else:
            state = WorkspaceReadiness.INCOMPLETE
        return ReadinessResult(state, self.blockers)

    def dossier_state_blockers(self) -> tuple[str, ...]:
        """Els blocadors que venen del dossier, per no perdre'n la semantica."""
        return tuple(
            f.code
            for f in self.findings
            if f.domain in (DOMAIN_DOSSIER, DOMAIN_WORKSPACE) and f.severity == BLOCKER
        )


def _dossier_findings(dossier: ReadinessResult) -> list[Finding]:
    """Els blocadors del dossier, reclassificats i comptats UNA sola vegada.

    `decision_not_go` i `analysis_outdated` els calcula el dossier pero parlen de
    la candidatura sencera: es marquen com a `workspace` per no fer creure que
    son problemes de papers. Cap dels dos es torna a avaluar aqui.
    """
    return [
        Finding(
            code=code,
            severity=BLOCKER,
            domain=DOMAIN_WORKSPACE if code in _WORKSPACE_CODES else DOMAIN_DOSSIER,
        )
        for code in dossier.blockers
    ]


def evaluate_final(
    *,
    dossier: ReadinessResult,
    technical: Sequence[Finding] = (),
    economic: Sequence[Finding] = (),
    workspace: Sequence[Finding] = (),
) -> FinalReadiness:
    """Ajunta els dominis. No en recalcula cap.

    L'ordre es fix —workspace i dossier, despres la resta de workspace, despres
    tecnic, despres economic— perque dues lectures iguals no donin dues
    respostes ordenades diferent.

    `workspace` son els fets que parlen de la candidatura sencera i que NO venen
    del dossier: la revisio dels plecs, per exemple, que no es cap paper nostre
    sino material de l'organ.
    """
    return FinalReadiness(
        dossier_state=dossier.state,
        findings=(*_dossier_findings(dossier), *workspace, *technical, *economic),
    )
