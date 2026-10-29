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
 * L'estat viu a `usePortalSync`, que la fitxa crida sempre: tornar-la a carregar la pinta
 * un moment com a esquelet i desmunta la barra, i el resultat de la feina no s'ha de perdre.
 */

import { useCallback, useEffect, useState } from "react";

import { Button, ErrorNotice, PermissionNote, ResultNotice, StatusPill } from "@/components/ui";
import { ApiError } from "@/lib/api";
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

export interface PortalSyncState {
  /** La petició seguida (la d'aquesta sessió, o l'activa que s'ha trobat en carregar). */
  request: JobRequest | null;
  active: boolean;
  submitting: boolean;
  error: string | null;
  notice: string | null;
  /** Fins quan (ms) no es pot tornar a demanar; `null` si ja es pot. */
  waitUntil: number | null;
  now: number;
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
  const [waitUntil, setWaitUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Ha acabat, bé o malament: la fitxa nova porta els documents i l'espera nova.
  const job = useJobRequest(portalSync ? companyId : null, tenderId, "sync_portal_documents", async () => {
    await onChanged();
  });

  // L'espera que diu la fitxa, comptada des que s'ha llegit.
  const retryAfter = portalSync?.retry_after_seconds ?? 0;
  const lastSyncedAt = portalSync?.last_synced_at ?? null;
  useEffect(() => {
    const at = Date.now();
    setNow(at);
    setWaitUntil(retryAfter > 0 ? at + retryAfter * 1000 : null);
  }, [retryAfter, lastSyncedAt]);

  useEffect(() => {
    if (waitUntil === null) return;
    const tick = setInterval(() => setNow(Date.now()), TICK_MS);
    const end = setTimeout(() => setWaitUntil(null), Math.max(0, waitUntil - Date.now()));
    return () => {
      clearInterval(tick);
      clearTimeout(end);
    };
  }, [waitUntil]);

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
        const at = Date.now();
        setNow(at);
        setWaitUntil(at + (Number.isFinite(seconds) && seconds > 0 ? seconds : 60) * 1000);
        setNotice("Aquesta licitació ja s'ha sincronitzat amb el portal fa poc: s'ha tornat a carregar la llista de documents.");
        await onChanged();
      } else {
        setError(portalSyncRequestErrorText(code));
        // El servidor diu que ara no es pot: la fitxa nova en dirà el motiu.
        if (code === "portal_sync_disabled" || code === "portal_sync_not_available") await onChanged();
      }
    } finally {
      setSubmitting(false);
    }
  }, [companyId, tenderId, track, onChanged]);

  return { request: job.request, active: job.active, submitting, error, notice, waitUntil, now, sync };
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
  const { request, active, submitting, error, notice, waitUntil, now } = state;
  const lastSyncedAt = portalSync.last_synced_at;
  const waiting = waitUntil !== null;
  const outcome = portalSyncOutcome(request);
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
        S&apos;ha sincronitzat amb el portal fa poc{lastSyncedAt ? ` (${formatDateTime(lastSyncedAt)})` : ""}. Es podrà
        tornar a demanar {portalSyncWaitText(waitUntil - now)}.
      </p>
    );
  } else {
    status = (
      <p className="text-[13px] text-muted">
        {lastSyncedAt
          ? `Última sincronització amb el portal: ${formatDateTime(lastSyncedAt)}.`
          : "Encara no s'ha sincronitzat amb el portal."}
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
    <div data-portal-sync className="space-y-2 border-b border-border px-5 py-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-1" aria-live="polite">
          <p className="text-[13px] font-medium text-ink">Portal de la PSCP</p>
          <p className="text-caption text-muted">
            Hi afegeix els annexos, les esmenes i les preguntes i respostes que l&apos;òrgan publica al portal i que el feed
            no porta.
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
  );
}
