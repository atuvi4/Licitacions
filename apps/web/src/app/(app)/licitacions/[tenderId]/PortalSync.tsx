"use client";

/**
 * «Actualitza documents de la PSCP» (Bloc 2, etapa 3): demana la sincronització de la
 * licitació amb el portal de la PSCP i, quan el worker acaba, torna a carregar la fitxa
 * perquè s'hi vegin de seguida els annexos, les esmenes o les preguntes i respostes nous.
 *
 * L'espera entre dues sincronitzacions (5 minuts, la de l'etapa 2) la decideix el
 * servidor: la fitxa porta quant en falta (`retry_after_seconds`) i, si una altra sessió
 * s'hi ha avançat, el 429 de la petició també. Mentre dura, el botó no demana res.
 *
 * Tenant-safe: de la sincronització d'una altra empresa no se'n diu ni quan ni qui; la
 * data que s'ensenya és la de l'última que ha demanat aquesta empresa (les seves peticions).
 *
 * L'estat viu a `usePortalSync`, que la fitxa crida sempre: el resultat no es perd si es
 * canvia de pestanya mentre la feina corre.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { Button, ErrorNotice, PermissionNote, ResultNotice, StatusPill } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { type JobRequest, requestPortalSync, useJobRequest } from "@/lib/jobs";
import { formatDateTime, type TenderPortalSync } from "@/lib/tenders";

import { isQueued } from "./documents";
import {
  PORTAL_SYNC_ACTION,
  portalSyncOutcome,
  portalSyncReasonText,
  portalSyncRequestErrorText,
  portalSyncWaitText,
} from "./portalSyncText";

/** Cada quant es torna a dir quants minuts falten. */
const TICK_MS = 15_000;
/** Quantes peticions pròpies es miren per trobar l'última acabada bé (el màxim de l'API). */
const OWN_HISTORY = 100;

export interface PortalSyncState {
  /** La petició seguida (la d'aquesta sessió, o l'activa que s'ha trobat en carregar). */
  request: JobRequest | null;
  active: boolean;
  submitting: boolean;
  error: string | null;
  notice: string | null;
  /** Quan va acabar bé l'última sincronització que ha demanat aquesta empresa, o `null`. */
  lastOwnSyncAt: string | null;
  /** Quant falta (ms) per poder-la tornar a demanar; `null` si ja es pot. */
  remainingMs: number | null;
  sync: () => Promise<void>;
}

/**
 * La sincronització d'una licitació de la PSCP. `portalSync` és el de la fitxa (`null`
 * mentre carrega o si no ve de la PSCP: llavors no es consulta res).
 */
export function usePortalSync(
  companyId: string | null,
  tenderId: string,
  portalSync: TenderPortalSync | null,
  onChanged: () => Promise<unknown> | void,
): PortalSyncState {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [tooSoonUntil, setTooSoonUntil] = useState<number | null>(null);
  const [lastOwnSyncAt, setLastOwnSyncAt] = useState<string | null>(null);
  const [, setTick] = useState(0);
  const company = portalSync ? companyId : null;

  // Ha acabat, bé o malament: la fitxa nova porta els documents i l'espera nova.
  const job = useJobRequest(company, tenderId, "sync_portal_documents", async (done) => {
    if (done.status === "succeeded" && done.finished_at) setLastOwnSyncAt(done.finished_at);
    await onChanged();
  });

  // L'última que ha demanat aquesta empresa: les seves peticions, mai les d'una altra.
  useEffect(() => {
    if (!company) return;
    let cancelled = false;
    api
      .get<JobRequest[]>(
        `/companies/${company}/tenders/${tenderId}/job-requests?job_type=sync_portal_documents&limit=${OWN_HISTORY}`,
      )
      .then((requests) => {
        if (cancelled) return;
        const last = requests.find((request) => request.status === "succeeded" && request.finished_at);
        setLastOwnSyncAt((current) => current ?? last?.finished_at ?? null);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [company, tenderId]);

  // L'espera que diu la fitxa, comptada des que s'ha llegit (cada lectura és un objecte nou).
  // Es calcula en pintar: el botó no queda mai actiu ni un instant amb una espera vigent.
  const serverUntil = useMemo(
    () =>
      portalSync && portalSync.retry_after_seconds > 0 ? Date.now() + portalSync.retry_after_seconds * 1000 : null,
    [portalSync],
  );
  const until = Math.max(serverUntil ?? 0, tooSoonUntil ?? 0) || null;
  const now = Date.now();
  const remainingMs = until !== null && until > now ? until - now : null;

  // Torna a pintar cada `TICK_MS` mentre dura l'espera, i just quan s'acaba.
  useEffect(() => {
    if (until === null) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      const left = until - Date.now();
      if (left <= 0) return;
      timer = setTimeout(() => {
        setTick((count) => count + 1);
        schedule();
      }, Math.min(TICK_MS, left + 50));
    };
    schedule();
    return () => clearTimeout(timer);
  }, [until]);

  const { track } = job;
  const sync = useCallback(async () => {
    if (!companyId) return;
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      track(await requestPortalSync(companyId, tenderId));
    } catch (caught) {
      const code = caught instanceof ApiError ? caught.code : null;
      if (caught instanceof ApiError && code === "portal_sync_too_soon") {
        // Una altra sessió (o una altra empresa) l'acaba de sincronitzar: s'espera el que
        // diu el servidor i es porta el que ha trobat.
        const seconds = Number(caught.details.retry_after_seconds);
        setTooSoonUntil(Date.now() + (Number.isFinite(seconds) && seconds > 0 ? seconds : 60) * 1000);
        await onChanged();
        setNotice("Aquesta licitació ja s'ha sincronitzat amb el portal fa poc: s'ha tornat a carregar la llista de documents.");
      } else {
        setError(portalSyncRequestErrorText(code));
        // El servidor diu que ara no es pot: la fitxa nova en dirà el motiu.
        if (code === "portal_sync_disabled" || code === "portal_sync_not_available") await onChanged();
      }
    } finally {
      setSubmitting(false);
    }
  }, [companyId, tenderId, track, onChanged]);

  return { request: job.request, active: job.active, submitting, error, notice, lastOwnSyncAt, remainingMs, sync };
}

export function PortalSyncBar({
  companyId,
  portalSync,
  state,
  canRequest,
  permissionKnown,
}: {
  companyId: string | null;
  portalSync: TenderPortalSync;
  state: PortalSyncState;
  /** L'empresa la pot demanar (`analysis-limits.can_process_documents`: el mateix permís que processar). */
  canRequest: boolean;
  /** `analysis-limits` ja ha respost: fins llavors no es diu res del permís. */
  permissionKnown: boolean;
}) {
  const { request, active, submitting, error, notice, lastOwnSyncAt, remainingMs } = state;
  const waiting = remainingMs !== null;
  const outcome = portalSyncOutcome(request);
  const hasResult = Boolean(error || notice || outcome);
  const disabled = !companyId || !portalSync.available || !canRequest || submitting || active || waiting;

  let status: React.ReactNode;
  if (!portalSync.available) {
    status = <p className="text-[13px] text-muted">{portalSyncReasonText(portalSync.reason)}</p>;
  } else if (active) {
    const queued = isQueued(request);
    status = (
      <StatusPill level={queued ? "neutral" : "running"} size="sm">
        {queued ? "En cua · pendent d'inici" : "Llegint el portal de la PSCP"}
      </StatusPill>
    );
  } else if (waiting) {
    status = (
      <p className="text-[13px] text-ink" data-portal-sync-wait>
        Aquesta licitació s&apos;ha sincronitzat amb el portal fa poc. Es podrà tornar a demanar{" "}
        {portalSyncWaitText(remainingMs ?? 0)}.
      </p>
    );
  } else {
    status = (
      <p className="text-[13px] text-muted">
        {lastOwnSyncAt
          ? `Última actualització demanada per la vostra empresa: ${formatDateTime(lastOwnSyncAt)}.`
          : "Encara no consta cap actualització de la vostra empresa que hagi acabat bé."}
      </p>
    );
  }

  const button = (
    <Button
      variant="secondary"
      size="sm"
      className="min-h-11 w-full sm:min-h-0 sm:w-auto"
      onClick={() => void state.sync()}
      disabled={disabled}
      loading={submitting || active}
    >
      {PORTAL_SYNC_ACTION}
    </Button>
  );

  return (
    <div data-portal-sync className="border-b border-border px-5 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1">
          <p className="text-[13px] font-medium text-ink">Portal de la PSCP</p>
          <p className="text-caption text-muted">
            Afegeix a la llista els annexos, les esmenes i les preguntes i respostes que l&apos;òrgan publica al portal i
            que la font no inclou.
          </p>
          {status}
        </div>
        <div className="sm:shrink-0">
          {permissionKnown && !canRequest && portalSync.available ? (
            <PermissionNote
              who="qui té permís per processar plecs"
              what="actualitzar els documents de la PSCP"
              action={<span className="text-muted">Demana-ho a qui administra l&apos;empresa.</span>}
            >
              {button}
            </PermissionNote>
          ) : (
            button
          )}
        </div>
      </div>
      {/* Només els canvis d'estat s'anuncien: el compte enrere queda fora d'aquesta regió. La regió
          hi és sempre (buida, sense alçada): una que apareix amb el missatge no s'anuncia. */}
      <div role="status" aria-live="polite" data-portal-sync-result className={hasResult ? "mt-2" : undefined}>
        {error ? (
          <ErrorNotice message={error} />
        ) : notice ? (
          <ResultNotice message={notice} />
        ) : (
          outcome &&
          (outcome.tone === "error" ? (
            <ErrorNotice message={outcome.message} />
          ) : (
            <ResultNotice tone={outcome.tone} message={outcome.message} detail={outcome.detail} />
          ))
        )}
      </div>
    </div>
  );
}
