"use client";

/**
 * Canvi d'estat d'una empresa client: pilot, activa, pausada, o allargar el
 * pilot. Sempre amb confirmació i sempre queda a l'auditoria del backend.
 *
 * «Pausada», de moment, només es registra: encara no bloqueja l'accés dels
 * usuaris del client (fase posterior). El text de la confirmació ho diu.
 */

import { useState } from "react";

import { Button, Field, Input, StatusPill } from "@/components/ui";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/shadcn";
import { ApiError, api } from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  DEFAULT_PILOT_DAYS,
  isoDatePlus,
  PLATFORM_STATUS_LABELS,
  PLATFORM_STATUS_LEVEL,
  type PlatformCompanyDetail,
  type PlatformStatus,
} from "@/lib/platform";

export function CompanyStatusPill({ status }: { status: PlatformStatus }) {
  return (
    <StatusPill level={PLATFORM_STATUS_LEVEL[status]} size="sm">
      {PLATFORM_STATUS_LABELS[status]}
    </StatusPill>
  );
}

export interface StatusChangeTarget {
  company: { id: string; name: string; status: PlatformStatus; pilot_ends_at: string | null };
  /** Estat nou, o `extend` per allargar el pilot. */
  to: PlatformStatus | "extend";
}

const COPY: Record<PlatformStatus, { title: (name: string) => string; body: string; action: string }> = {
  active: {
    title: (name) => `Convertir ${name} en activa?`,
    body: "Deixa de ser un pilot i passa a accés complet. Quedarà registrat a l'auditoria.",
    action: "Convertir en activa",
  },
  paused: {
    title: (name) => `Pausar ${name}?`,
    body: "Queda registrada com a pausada. En aquesta fase encara no bloqueja l'accés dels usuaris del client.",
    action: "Pausar empresa",
  },
  pilot: {
    title: (name) => `Tornar ${name} a pilot?`,
    body: `Comença un pilot nou de ${DEFAULT_PILOT_DAYS} dies a partir d'avui. Quedarà registrat a l'auditoria.`,
    action: "Tornar a pilot",
  },
};

export function StatusChangeDialog({
  target,
  onClose,
  onChanged,
}: {
  target: StatusChangeTarget | null;
  onClose: () => void;
  onChanged: (detail: PlatformCompanyDetail) => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [endDate, setEndDate] = useState("");

  async function submit(body: { status: PlatformStatus; pilot_ends_at?: string }) {
    if (!target) return;
    setPending(true);
    setError(null);
    try {
      const detail = await api.patch<PlatformCompanyDetail>(`/platform/companies/${target.company.id}/status`, body);
      onChanged(detail);
      onClose();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "No s'ha pogut connectar amb el servidor.");
    } finally {
      setPending(false);
    }
  }

  function close(open: boolean) {
    if (open) return;
    setError(null);
    setEndDate("");
    onClose();
  }

  if (target?.to === "extend") {
    const current = target.company.pilot_ends_at;
    const value = endDate || (current ? isoDatePlus(30, new Date(current)) : isoDatePlus(DEFAULT_PILOT_DAYS));
    return (
      <Dialog open onOpenChange={close}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Allargar el pilot</DialogTitle>
            <DialogDescription>
              {target.company.name} · ara acaba el {formatDate(current)}.
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <Field label="Nova data de fi" required>
              <Input type="date" value={value} min={isoDatePlus(0)} onChange={(event) => setEndDate(event.target.value)} />
            </Field>
            {error && (
              <p role="alert" className="mt-3 text-[13px] text-danger">
                {error}
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button variant="secondary" onClick={() => close(false)} disabled={pending}>
              Cancel·la
            </Button>
            <Button variant="primary" loading={pending} onClick={() => void submit({ status: "pilot", pilot_ends_at: value })}>
              Allarga el pilot
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }

  const to = target?.to as PlatformStatus | undefined;
  const copy = to ? COPY[to] : null;
  return (
    <AlertDialog open={Boolean(target && copy)} onOpenChange={close}>
      <AlertDialogContent>
        {target && copy && to && (
          <>
            <AlertDialogHeader>
              <AlertDialogTitle>{copy.title(target.company.name)}</AlertDialogTitle>
              <AlertDialogDescription>
                <span className="mb-2 flex items-center gap-2 text-caption text-muted">
                  <CompanyStatusPill status={target.company.status} /> → <CompanyStatusPill status={to} />
                </span>
                {copy.body}
              </AlertDialogDescription>
              {error && (
                <p role="alert" className="mt-3 text-[13px] text-danger">
                  {error}
                </p>
              )}
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel />
              <AlertDialogAction
                onClick={(event) => {
                  // Es tanca quan respon el servidor, no abans.
                  event.preventDefault();
                  void submit({ status: to });
                }}
                disabled={pending}
              >
                {copy.action}
              </AlertDialogAction>
            </AlertDialogFooter>
          </>
        )}
      </AlertDialogContent>
    </AlertDialog>
  );
}
