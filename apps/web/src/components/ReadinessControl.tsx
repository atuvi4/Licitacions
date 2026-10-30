"use client";

import { AlertTriangle, Check, ChevronRight, CircleHelp, Info } from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Button, Drawer, Skeleton, StatusPill, cn } from "@/components/ui";
import { useReadiness } from "@/components/ReadinessProvider";
import {
  DOMAIN_LABELS,
  UNKNOWN_FINDING_LABEL,
  blockerCount,
  blockerCountLabel,
  destinationHref,
  findingCopy,
  groupFindings,
  type FinalReadiness,
  type FindingDestination,
  type FindingSeverity,
  type ReadinessFinding,
} from "@/lib/readiness";

const SEVERITY_ICON: Record<FindingSeverity, typeof AlertTriangle> = {
  blocker: AlertTriangle,
  warning: Info,
  info: Check,
};

const SEVERITY_CLASS: Record<FindingSeverity, string> = {
  blocker: "text-danger",
  warning: "text-warning",
  info: "text-faint",
};

const SEVERITY_LABEL: Record<FindingSeverity, string> = {
  blocker: "Pendent",
  warning: "Avís",
  info: "Informació",
};

/**
 * L'indicador de preparació final del capçal i el calaix que obre.
 *
 * El veredicte és del servidor: aquí no es recalcula res ni es dedueix res a
 * partir de les dades de la candidatura. I si no es pot llegir, **no diu que
 * estigui a punt**: diu que no ho sap i ofereix tornar-ho a provar. Convertir
 * una fallada de xarxa en un «A punt» seria la pitjor mentida possible en
 * aquesta pantalla.
 */
export function ReadinessControl() {
  const [open, setOpen] = useState(false);
  // La lectura viu al context: el distintiu i el calaix són la mateixa dada,
  // i qualsevol mutació que la canviï la invalida des d'on sigui.
  const { data, error, loading, invalidate } = useReadiness();

  if (loading && !data && !error) {
    // Anunciat, no només dibuixat: un lector de pantalla ha de saber que això
    // encara no diu res, en comptes de llegir un capçal mut.
    return (
      <div role="status" aria-busy="true" aria-label="Es comprova la preparació final">
        <Skeleton className="h-7 w-28" />
      </div>
    );
  }

  // Una resposta que no porti `findings` no es pot interpretar, i el que NO es
  // pot fer és donar-la per bona: desconegut no és «a punt».
  const findings = data && Array.isArray(data.findings) ? data.findings : null;
  const unknown = findings === null;
  const blockers = findings ? blockerCount(findings) : 0;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-control focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-500"
      >
        {unknown ? (
          <StatusPill level="neutral" size="sm">
            <CircleHelp size={13} strokeWidth={1.5} aria-hidden className="mr-1 inline align-[-2px]" />
            Preparació desconeguda
          </StatusPill>
        ) : data?.ready_for_final_review ? (
          <StatusPill level="ready" size="sm">
            A punt
          </StatusPill>
        ) : (
          <StatusPill level="blocking" size="sm">
            {blockerCountLabel(blockers)}
          </StatusPill>
        )}
        <ChevronRight size={14} strokeWidth={1.5} aria-hidden className="text-muted" />
      </button>

      <Drawer
        open={open}
        title="Preparació final"
        description="Què falta per poder congelar la proposta."
        onClose={() => setOpen(false)}
        footer={<DrawerFooter readiness={data} />}
      >
        {unknown ? (
          <div className="px-4 py-5">
            <p className="text-body text-ink">No s&apos;ha pogut llegir la preparació final.</p>
            <p className="mt-1 text-caption text-muted">
              {error ?? "Sense resposta del servidor."} Mentre no es pugui llegir, no es pot dir que la candidatura
              estigui a punt.
            </p>
            <Button variant="secondary" size="sm" className="mt-3" onClick={() => void invalidate()}>
              Torna-ho a provar
            </Button>
          </div>
        ) : (
          <FindingList
            findings={findings}
            ready={data?.ready_for_final_review === true}
            tenderId={data?.tender_id ?? null}
          />
        )}
      </Drawer>
    </>
  );
}

function FindingList({
  findings,
  ready,
  tenderId,
}: {
  findings: ReadinessFinding[];
  ready: boolean;
  tenderId: string | null;
}) {
  const groups = groupFindings(findings);

  if (groups.length === 0) {
    return (
      <p className="px-4 py-5 text-body text-muted">
        {ready
          ? "Cap pendent, cap avís: la candidatura es pot congelar."
          : "El servidor no ha donat cap detall d'aquesta preparació."}
      </p>
    );
  }

  return (
    <div>
      {groups.map((group) => (
        <section key={group.severity}>
          <h3 className="flex items-center gap-2 border-b border-border bg-subtle px-4 py-1.5 font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">
            {group.label}
            <span className="ml-auto tabular-nums">{group.findings.length}</span>
          </h3>
          <ul>
            {group.findings.map((finding, index) => (
              <FindingRow
                key={`${finding.code}-${finding.entity_id ?? index}`}
                finding={finding}
                tenderId={tenderId}
              />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function FindingRow({ finding, tenderId }: { finding: ReadinessFinding; tenderId: string | null }) {
  const copy = findingCopy(finding.code);
  const detail = `${finding.code}${finding.entity_id ? ` · ${finding.entity_id}` : ""}`;
  const Icon = SEVERITY_ICON[finding.severity] ?? Info;
  const href = useDestinationHref(copy?.destination, finding.entity_id, tenderId);

  return (
    <li className="flex items-start gap-2.5 border-b border-border px-4 py-2.5 last:border-b-0">
      <Icon
        size={14}
        strokeWidth={2}
        aria-hidden
        className={cn("mt-0.5 shrink-0", SEVERITY_CLASS[finding.severity])}
      />
      <div className="min-w-0 flex-1">
        <p className="text-body font-medium text-ink">
          {copy?.label ?? UNKNOWN_FINDING_LABEL}
          {/* La severitat no pot dependre només del color. */}
          <span className="sr-only"> ({SEVERITY_LABEL[finding.severity] ?? finding.severity})</span>
        </p>
        {/* El domini viatja a la fila: els grups ara són els tres nivells de
            gravetat, i sense això no se sabria d'on surt cada problema. */}
        <p className="mt-0.5 text-caption text-muted">
          {DOMAIN_LABELS[finding.domain] ?? finding.domain}
          {copy?.hint ? ` · ${copy.hint}` : ""}
        </p>
        {/* El codi no s'amaga, però tampoc no es fa passar per una frase: va
            plegat al detall tècnic, com la resta de codis interns de l'app. */}
        {!copy && (
          <details className="mt-0.5 text-caption text-muted">
            <summary className="cursor-pointer">Detall tècnic</summary>
            <p className="mt-0.5 font-mono text-[11.5px] text-faint">{detail}</p>
          </details>
        )}
      </div>
      {href && copy?.action && (
        <Link
          href={href}
          className="shrink-0 text-caption text-brand hover:underline"
          onClick={(event) => event.stopPropagation()}
        >
          {copy.action} →
        </Link>
      )}
    </li>
  );
}

/**
 * On porta un problema. `entity` viatja a la URL quan el servidor diu de quina
 * fila parla; S9-B i S9-C el faran servir per obrir-la. Sense entitat, l'enllaç
 * porta a l'àrea i prou: inventar-hi una destinació seria pitjor que no tenir-ne.
 */
function useDestinationHref(
  destination: FindingDestination | undefined,
  entityId: string | null,
  tenderId: string | null,
): string | null {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  if (!destination) return null;
  // Els plecs són de la licitació, no de la candidatura: l'enllaç surt de
  // Preparar i porta a la fitxa, que és on es poden mirar i marcar.
  return destinationHref(destination, { pathname, search: searchParams?.toString() ?? "", tenderId, entityId });
}

function DrawerFooter({ readiness }: { readiness: FinalReadiness | null }) {
  const pathname = usePathname();
  const searchParams = useSearchParams();
  if (!readiness) return null;

  if (!readiness.ready_for_final_review) {
    return (
      <p className="text-caption text-muted">
        <strong className="text-ink">Resol els pendents marcats per poder congelar.</strong> Els avisos no ho impedeixen.
      </p>
    );
  }

  const next = new URLSearchParams(searchParams?.toString() ?? "");
  next.set("pas", "tancar");
  next.delete("area");
  next.delete("entity");
  return (
    <div className="flex items-center justify-between gap-3">
      <p className="text-caption text-muted">Cap pendent per congelar.</p>
      {/* Congelar viu al pas Tancar: aquí només s'hi porta. */}
      <Link href={`${pathname}?${next.toString()}`} className="text-body text-brand hover:underline">
        Ves a Tancar →
      </Link>
    </div>
  );
}
