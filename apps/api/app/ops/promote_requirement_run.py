"""Promocio AUDITADA d'un run experimental (`product-v2`) a base vigent.

    python -m app.ops.promote_requirement_run --run <uuid> --actor <email> \\
        --reason "<text>" [--release <commit>] [--apply]

Per defecte es una prova (`dry-run`): diu que faria i no escriu res. Amb
`--apply`, ho fa en una sola transaccio.

Per que existeix. Un run d'un perfil experimental (`EXPERIMENTAL_PROFILES`) no
passa mai sol a vigent: `_may_become_current` el refusa per disseny i aixo no
canvia. Aquesta ordre es l'UNIC cami per fer-ho, i nomes per a UN run concret,
amb el nom de qui ho decideix i el motiu escrits al registre d'auditoria.

Condicions, totes obligatories (si en falla una, refusa i no toca res):

* el run es `completed`, `run_basis = real` i d'un perfil experimental (un run
  d'un perfil public ja el promou la canonada; aqui no s'hi pot forcar res);
* el seu `error_code` es el d'un run experimental no promogut (o cap): un run que
  porta un altre motiu de no-vigencia (`input_changed`...) no es promou;
* l'empremta d'entrada del run es l'empremta d'ara (`analysis_input`, amb la seva
  seleccio) i no cita cap document retirat (`run_includes_removed_document`);
* `--reason` te de `MIN_REASON_CHARS` a `MAX_REASON_CHARS` caracters, i el
  resum d'auditoria sencer (motiu inclos) cap a `audit_logs.summary` sense tallar;
* `--actor` es un usuari actiu amb rol d'operador de plataforma (`is_superuser`);
* el run no es va llancar amb `force` en una licitacio publica (ho diu la feina
  que el va executar; sense feina, `forced` surt com a desconegut al resultat i a
  l'auditoria);
* no hi ha cap analisi en curs de la licitacio: ni un run `pending`/`running` ni
  una feina activa (analisi o processament de plecs, que canviaria l'empremta).

Concurrencia: es bloqueja la fila del run (`FOR UPDATE`). Una feina que comenci
entre la comprovacio i el commit no es veu; l'index unic parcial de `is_current`
impedeix dues vigents, pero aquell conflicte sortiria com a excepcio i no com a
codi de refus. Acceptable per a una ordre d'operador manual.

Efecte (`--apply`), el mateix que fa `_finalise_run` quan un run passa a vigent i
res mes: el vigent anterior (si n'hi ha) deixa de ser-ho i se'n porten les
anotacions d'estructura (`carry_over_in_savepoint`); el run passa a
`is_current = true` i se'n netegen `error_code`/`error_message` (el codi original
queda a l'auditoria). NO es toca cap fila d'extraccio del run (requisits,
criteris, incerteses, cites) ni cap candidatura: el que el producte fa a la
candidatura quan hi ha una analisi vigent nova (regenerar la checklist, que
actualitza `current_requirement_run_id`) continua sent una accio de l'usuari.

Idempotent: si el run ja es el vigent, no fa res i ho diu.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AnalysisRunStatus, AuditAction, RequirementRunBasis, WorkJobType
from app.models.identity import User
from app.models.jobs import ACTIVE_WORK_JOB_STATUSES, WorkJob
from app.models.requirements import RequirementExtractionRun
from app.models.tenders import Tender
from app.services import audit
from app.services.requirement_analysis import (
    EXPERIMENTAL_PROFILE_NOT_CURRENT,
    analysis_input,
    carry_over_in_savepoint,
    current_run_for,
    experimental_profile_of,
    run_document_selection,
    run_includes_removed_document,
)

#: Entitat de l'auditoria.
ENTITY_TYPE = "requirement_extraction_run"
#: Un motiu ha de dir alguna cosa: «ok» no es un motiu.
MIN_REASON_CHARS = 20
MAX_REASON_CHARS = 200
#: Mida maxima de `audit_logs.summary` (`services.audit` la talla en silenci).
AUDIT_SUMMARY_MAX = 500

#: Codis de refus. Tancats: l'informe i els tests en depenen.
RUN_NOT_FOUND = "run_not_found"
RUN_NOT_COMPLETED = "run_not_completed"
RUN_NOT_REAL = "run_not_real"
RUN_NOT_EXPERIMENTAL = "run_not_experimental"
RUN_UNEXPECTED_ERROR_CODE = "run_unexpected_error_code"
INPUT_FINGERPRINT_MISMATCH = "input_fingerprint_mismatch"
RUN_CITES_REMOVED_DOCUMENT = "run_cites_removed_document"
REASON_TOO_SHORT = "reason_too_short"
REASON_TOO_LONG = "reason_too_long"
ACTOR_NOT_OPERATOR = "actor_not_operator"
ANALYSIS_IN_PROGRESS = "analysis_in_progress"
#: Run llancat amb `force` en una licitacio publica: mai no es canonic
#: (`_may_become_current` en diria `forced_not_canonical`).
RUN_FORCED_NOT_CANONICAL = "run_forced_not_canonical"
#: L'auditoria no hi cabria sencera (motiu + release massa llargs).
AUDIT_SUMMARY_TOO_LONG = "audit_summary_too_long"

#: Resultats sense refus.
WOULD_PROMOTE = "would_promote"
PROMOTED = "promoted"
ALREADY_CURRENT = "already_current"

#: Les feines que, en curs, fan que l'empremta d'ara no sigui fiable.
_BUSY_JOB_TYPES = (
    WorkJobType.ANALYZE_TENDER_REQUIREMENTS,
    WorkJobType.PROCESS_TENDER_DOCUMENTS,
)


@dataclass
class PromotionResult:
    """El que diu l'ordre. `refused` = cap escriptura."""

    status: str
    run_id: str
    applied: bool = False
    tender_id: str | None = None
    profile: str | None = None
    run_fingerprint: str | None = None
    current_fingerprint: str | None = None
    previous_current_run_id: str | None = None
    original_error_code: str | None = None
    release: str | None = None
    #: Si el run es va llancar amb `force` (de la feina que el va executar).
    #: `None` = no se sap (run sense feina: canari o script d'operador).
    forced: bool | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return self.status not in (WOULD_PROMOTE, PROMOTED, ALREADY_CURRENT)

    def as_json(self) -> str:
        return json.dumps({**asdict(self), "refused": self.refused}, ensure_ascii=False, indent=2)


async def _was_forced(session: AsyncSession, run: RequirementExtractionRun) -> bool | None:
    """`force` de la feina que va executar el run; `None` si no en va tenir cap."""
    if run.work_job_id is None:
        return None
    parameters = await session.scalar(
        sa.select(WorkJob.parameters).where(WorkJob.id == run.work_job_id)
    )
    if parameters is None:
        return None
    return bool((parameters or {}).get("force"))


async def _analysis_in_progress(session: AsyncSession, tender_id: uuid.UUID) -> dict[str, Any]:
    runs = (
        await session.scalars(
            sa.select(RequirementExtractionRun.id).where(
                RequirementExtractionRun.tender_id == tender_id,
                RequirementExtractionRun.status.in_(
                    [AnalysisRunStatus.PENDING, AnalysisRunStatus.RUNNING]
                ),
            )
        )
    ).all()
    jobs = (
        await session.scalars(
            sa.select(WorkJob.id).where(
                WorkJob.tender_id == tender_id,
                WorkJob.job_type.in_(_BUSY_JOB_TYPES),
                WorkJob.status.in_(ACTIVE_WORK_JOB_STATUSES),
            )
        )
    ).all()
    if not runs and not jobs:
        return {}
    return {
        "running_runs": [str(item) for item in runs],
        "active_jobs": [str(item) for item in jobs],
    }


async def promote_requirement_run(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    reason: str,
    actor_email: str,
    release: str | None,
    apply: bool = False,
) -> PromotionResult:
    """Comprova les condicions i, amb `apply`, promou el run. Fa el commit."""
    result = PromotionResult(status=RUN_NOT_FOUND, run_id=str(run_id), release=release)
    run = await session.scalar(
        sa.select(RequirementExtractionRun)
        .where(RequirementExtractionRun.id == run_id)
        .with_for_update()
    )
    if run is None:
        return result
    profile = experimental_profile_of(run)
    result.tender_id = str(run.tender_id)
    result.profile = profile.name if profile is not None else None
    result.run_fingerprint = run.input_fingerprint
    result.original_error_code = run.error_code

    def refuse(code: str, **detail: Any) -> PromotionResult:
        result.status = code
        result.detail.update(detail)
        return result

    if run.is_current:
        result.status = ALREADY_CURRENT
        return result

    text = " ".join((reason or "").split())
    if len(text) < MIN_REASON_CHARS:
        return refuse(REASON_TOO_SHORT, minimum=MIN_REASON_CHARS)
    if len(text) > MAX_REASON_CHARS:
        return refuse(REASON_TOO_LONG, maximum=MAX_REASON_CHARS)
    actor = await session.scalar(sa.select(User).where(User.email == actor_email))
    if actor is None or not actor.is_active or not actor.is_superuser:
        return refuse(ACTOR_NOT_OPERATOR, actor=actor_email)

    if run.status != AnalysisRunStatus.COMPLETED:
        return refuse(RUN_NOT_COMPLETED, run_status=str(run.status))
    if run.run_basis != RequirementRunBasis.REAL:
        return refuse(RUN_NOT_REAL, run_basis=str(run.run_basis))
    if profile is None:
        # Un run d'un perfil public el promou la canonada (`_may_become_current`);
        # aquesta ordre no es una porta del darrere per a ells.
        return refuse(RUN_NOT_EXPERIMENTAL, extractor_version=run.extractor_version)
    if run.error_code not in (None, EXPERIMENTAL_PROFILE_NOT_CURRENT):
        return refuse(RUN_UNEXPECTED_ERROR_CODE, error_code=run.error_code)

    result.forced = await _was_forced(session, run)
    owner = await session.scalar(
        sa.select(Tender.owner_company_id).where(Tender.id == run.tender_id)
    )
    if result.forced and owner is None:
        return refuse(RUN_FORCED_NOT_CANONICAL)

    busy = await _analysis_in_progress(session, run.tender_id)
    if busy:
        return refuse(ANALYSIS_IN_PROGRESS, **busy)

    selection = await run_document_selection(session, run)
    current = await analysis_input(session, run.tender_id, document_ids=selection)
    result.current_fingerprint = current.fingerprint
    if current.fingerprint != run.input_fingerprint:
        return refuse(INPUT_FINGERPRINT_MISMATCH)
    if await run_includes_removed_document(session, run):
        return refuse(RUN_CITES_REMOVED_DOCUMENT)

    previous = await current_run_for(session, run.tender_id)
    result.previous_current_run_id = str(previous.id) if previous is not None else None
    summary = (
        f"promote_requirement_run profile={profile.name} tender={run.tender_id} "
        f"fingerprint={run.input_fingerprint} previous={result.previous_current_run_id or '-'} "
        f"original_error={result.original_error_code or '-'} "
        f"forced={'-' if result.forced is None else str(result.forced).lower()} "
        f"release={release or '-'} reason={text}"
    )
    if len(summary) > AUDIT_SUMMARY_MAX:
        # Mai un motiu tallat en silenci a l'auditoria.
        return refuse(AUDIT_SUMMARY_TOO_LONG, length=len(summary), maximum=AUDIT_SUMMARY_MAX)
    if not apply:
        # Cap escriptura: la sessio no ha modificat res i qui la tanca la descarta.
        result.status = WOULD_PROMOTE
        return result

    # El mateix que fa `_finalise_run` quan un run passa a vigent.
    if previous is not None and previous.id != run.id:
        previous.is_current = False
        await session.flush()
        await carry_over_in_savepoint(session, previous.id, run.id)
    run.is_current = True
    run.error_code = None
    run.error_message = None
    await audit.record(
        session,
        action=AuditAction.UPDATE,
        user_id=actor.id,
        entity_type=ENTITY_TYPE,
        entity_id=run.id,
        summary=summary,
    )
    await session.commit()
    result.status = PROMOTED
    result.applied = True
    return result


def _default_release() -> str | None:
    """El commit del codi que executa l'ordre, si es pot saber."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


async def _main(args: argparse.Namespace) -> int:
    from app.tenant_ops import session_scope

    async with session_scope() as session:
        result = await promote_requirement_run(
            session,
            uuid.UUID(args.run),
            reason=args.reason,
            actor_email=args.actor,
            release=args.release or _default_release(),
            apply=args.apply,
        )
    print(result.as_json())
    return 2 if result.refused else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Promou, auditat, un run experimental (product-v2) a base vigent."
    )
    parser.add_argument("--run", required=True, help="Identificador del run.")
    parser.add_argument("--actor", required=True, help="Email de l'operador (is_superuser).")
    parser.add_argument("--reason", required=True, help="Motiu (queda a l'auditoria).")
    parser.add_argument("--release", default=None, help="Release/commit; per defecte, git HEAD.")
    parser.add_argument(
        "--apply", action="store_true", help="Escriu. Sense, nomes diu que faria (dry-run)."
    )
    return asyncio.run(_main(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
