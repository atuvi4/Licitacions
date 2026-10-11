"use client";

/**
 * Administració Tuvi · Fitxa d'una empresa client.
 *
 * Dos blocs que no es barregen:
 * - «Gestionat per Tuvi»: el que controla l'operador (dades de l'alta, estat
 *   del client, propietari inicial).
 * - «Espai del client · vista de seguiment»: el que el client gestiona des de
 *   la seva administració. Aquí només se'n veuen resums: cap document, cap
 *   edició, i sense passar per les rutes de l'empresa.
 */

import { ChevronDown, ChevronRight, Copy, Lock } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { type ReactNode, useState } from "react";

import { CompanyStatusPill, StatusChangeDialog, type StatusChangeTarget } from "@/components/platform/StatusChange";
import { Button, Card, CardHeader, cn, LoadErrorState, PageSkeleton, StatusPill } from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/shadcn";
import { formatDate } from "@/lib/format";
import { WORKSPACE_STATUS_LEVEL } from "@/lib/labels";
import { useResource } from "@/lib/hooks";
import {
  displayName,
  initials,
  PLATFORM_HOME,
  type PlatformCompanyDetail,
  pilotProgress,
  relativeTime,
} from "@/lib/platform";
import { workspaceStatusLabel } from "@/lib/workspace";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className={KICKER}>{label}</dt>
      <dd className="mt-0.5 text-body text-ink">{children}</dd>
    </div>
  );
}

function SectionLabel({ title, note, icon }: { title: string; note: string; icon?: ReactNode }) {
  return (
    <p className="mb-3 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px]">
      <span className="inline-flex items-center gap-1.5 font-heading text-kicker font-semibold tracking-[0.1em] text-brand-strong uppercase">
        {icon}
        {title}
      </span>
      <span className="text-muted">{note}</span>
    </p>
  );
}

function StatusCard({ company, onChange }: { company: PlatformCompanyDetail; onChange: (target: StatusChangeTarget) => void }) {
  const { platform_status: status, pilot_started_at, pilot_ends_at } = company.status;
  const progress = status === "pilot" ? pilotProgress(pilot_started_at, pilot_ends_at) : null;
  const target = { id: company.id, name: displayName(company), status, pilot_ends_at };
  return (
    <Card>
      <CardHeader title="Estat del client" />
      <div className="space-y-3 px-5 py-4">
        <div className="flex items-center justify-between">
          <CompanyStatusPill status={status} />
          {progress && (
            <span className="text-caption text-muted">
              Dia {progress.day} de {progress.total}
            </span>
          )}
        </div>
        {progress && (
          <>
            <div
              role="progressbar"
              aria-label="Progrés del pilot"
              aria-valuenow={progress.day}
              aria-valuemin={1}
              aria-valuemax={progress.total}
              className="h-1.5 overflow-hidden rounded-full bg-neutral-200"
            >
              <div className="h-full bg-brand" style={{ width: `${progress.percent}%` }} />
            </div>
            <div className="flex justify-between text-caption text-muted">
              <span>{formatDate(pilot_started_at)}</span>
              <span>{formatDate(pilot_ends_at)}</span>
            </div>
          </>
        )}
        {status !== "pilot" && company.status.status_changed_at && (
          <p className="text-caption text-muted">Des del {formatDate(company.status.status_changed_at)}</p>
        )}
        {status === "paused" && (
          <p className="text-caption text-muted">De moment només queda registrat: l&apos;accés del client no es bloqueja.</p>
        )}
        <div className="flex flex-col gap-2 pt-1">
          {status !== "active" && (
            <Button variant="primary" onClick={() => onChange({ company: target, to: "active" })}>
              {status === "paused" ? "Reactivar com a activa" : "Convertir en activa"}
            </Button>
          )}
          {status === "pilot" && (
            <Button variant="secondary" onClick={() => onChange({ company: target, to: "extend" })}>
              Allargar el pilot
            </Button>
          )}
          {status === "paused" && (
            <Button variant="secondary" onClick={() => onChange({ company: target, to: "pilot" })}>
              Tornar a pilot
            </Button>
          )}
        </div>
      </div>
    </Card>
  );
}

function ManagedByTuvi({ company, onChange }: { company: PlatformCompanyDetail; onChange: (target: StatusChangeTarget) => void }) {
  const [copied, setCopied] = useState(false);
  const owner = company.owner;
  return (
    <section aria-label="Gestionat per Tuvi">
      <SectionLabel title="Gestionat per Tuvi" note="L'operador de plataforma controla aquestes dades." />
      {/* minmax(0, …): cap columna no s'eixampla més del que hi cap. */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)_minmax(0,1fr)]">
        <Card className="md:col-span-2 xl:col-span-1">
          <CardHeader title="Informació general" />
          <dl className="grid gap-x-6 gap-y-4 px-5 py-4 sm:grid-cols-2">
            <Fact label="Nom legal">{company.legal_name}</Fact>
            <Fact label="Nom comercial">{company.trade_name ?? "—"}</Fact>
            <Fact label="NIF">{company.tax_id ?? "—"}</Fact>
            <Fact label="ID intern">
              <span className="inline-flex items-center gap-1.5 font-mono text-[13px]">
                {company.id.slice(0, 8)}
                <button
                  type="button"
                  aria-label="Copia l'ID intern"
                  title={copied ? "Copiat" : "Copia l'ID complet"}
                  onClick={() => void navigator.clipboard?.writeText(company.id).then(() => setCopied(true), () => undefined)}
                  className="text-muted hover:text-ink"
                >
                  <Copy size={13} aria-hidden />
                </button>
              </span>
            </Fact>
            <Fact label="Data d'alta">{formatDate(company.created_at)}</Fact>
            <Fact label="Alta feta per">
              {company.onboarded_by ? `${company.onboarded_by.full_name ?? company.onboarded_by.email} · operador Tuvi` : "—"}
            </Fact>
          </dl>
        </Card>
        <StatusCard company={company} onChange={onChange} />
        <Card>
          <CardHeader title="Propietari" />
          <div className="space-y-3 px-5 py-4 text-[13px]">
            {owner ? (
              <>
                <div className="flex items-center gap-2.5">
                  <span className="flex h-8 w-8 items-center justify-center rounded-control border border-brand bg-brand-soft font-heading text-[11px] font-semibold text-brand-strong">
                    {initials(owner.full_name ?? owner.email)}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium text-ink">{owner.full_name ?? "—"}</span>
                    <span className="block truncate text-brand-strong">{owner.email}</span>
                  </span>
                </div>
                <dl className="space-y-1.5">
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted">Assignat</dt>
                    <dd className="text-ink">{formatDate(owner.assigned_at)}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted">Últim accés</dt>
                    <dd className="text-ink">{owner.last_login_at ? relativeTime(owner.last_login_at) : "Encara no ha entrat"}</dd>
                  </div>
                </dl>
              </>
            ) : (
              <p className="text-muted">Sense propietari assignat.</p>
            )}
            <p className="border-t border-border pt-3 text-muted">
              Administra l&apos;empresa des de l&apos;administració del client, dins de l&apos;app Tuvi.
            </p>
          </div>
        </Card>
      </div>
    </section>
  );
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <div className="border-r border-border px-5 py-3 last:border-r-0">
      <p className={KICKER}>{label}</p>
      <p className="mt-0.5 font-heading text-[22px] font-semibold text-ink tabular-nums">{value}</p>
    </div>
  );
}

function ClientSpace({ company }: { company: PlatformCompanyDetail }) {
  const space = company.client_space;
  return (
    <section aria-label="Espai del client · vista de seguiment">
      <SectionLabel
        title="Espai del client · vista de seguiment"
        note="El client ho gestiona des de la seva pròpia administració. Des d'aquí en fas el seguiment."
        icon={<Lock size={12} aria-hidden />}
      />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader title="Licitacions" description="Candidatures de l'empresa." />
          <div className="grid grid-cols-3 border-b border-border">
            <Counter label="Actives" value={space.tenders.active} />
            <Counter label="En preparació" value={space.tenders.preparing} />
            <Counter label="Presentades" value={space.tenders.submitted} />
          </div>
          {space.recent_tenders.length === 0 ? (
            <p className="px-5 py-4 text-[13px] text-muted">Encara no ha obert cap candidatura.</p>
          ) : (
            <div className="scroll-thin overflow-x-auto">
              <table className="w-full border-collapse text-body" aria-label="Candidatures recents">
                <thead>
                  <tr>
                    {["Licitació", "Organisme", "Estat", "Termini"].map((label) => (
                      <th key={label} scope="col" className={cn("border-b border-border bg-subtle px-4 py-2.5 text-left", KICKER)}>
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {space.recent_tenders.map((tender, index) => (
                    <tr key={`${tender.title}-${index}`} className="border-b border-border last:border-b-0">
                      <td className="max-w-[18rem] truncate px-4 py-3 text-ink" title={tender.title}>
                        {tender.title}
                      </td>
                      <td className="max-w-[12rem] truncate px-4 py-3 text-muted">{tender.buyer_name ?? "—"}</td>
                      <td className="px-4 py-3">
                        <StatusPill level={WORKSPACE_STATUS_LEVEL[tender.status]} size="sm">
                          {workspaceStatusLabel(tender)}
                        </StatusPill>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-muted">{formatDate(tender.deadline_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="border-t border-border px-5 py-2.5 text-caption text-muted">
            {space.tenders.total} {space.tenders.total === 1 ? "licitació" : "licitacions"}
            {space.tenders.total > space.recent_tenders.length && ` · es mostren les ${space.recent_tenders.length} més recents`}
          </p>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Activitat" />
            {space.recent_activity.length === 0 ? (
              <p className="px-5 py-4 text-[13px] text-muted">Sense activitat registrada.</p>
            ) : (
              <ul className="divide-y divide-border" aria-label="Activitat recent">
                {space.recent_activity.map((entry, index) => (
                  <li key={`${entry.at}-${index}`} className="px-5 py-3 text-[13px]">
                    <p className="text-ink">
                      <span className="font-semibold">{entry.actor?.full_name ?? entry.actor?.email ?? "Sistema"}</span>{" "}
                      {entry.summary ?? entry.action}
                    </p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-caption text-muted">
                      {entry.by_platform && (
                        <StatusPill level="running" size="sm" outline>
                          Operador Tuvi
                        </StatusPill>
                      )}
                      {relativeTime(entry.at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <div className="rounded-card border border-dashed border-border-strong bg-surface px-5 py-4">
            <div className="flex items-center justify-between">
              <h2 className="font-heading text-[17px] font-semibold text-ink">Usuaris</h2>
              <StatusPill level="neutral" size="sm" outline>
                Fase posterior
              </StatusPill>
            </div>
            <p className="mt-2 text-[13px] text-ink">
              <span className="font-heading text-[22px] font-semibold tabular-nums">{space.users.total}</span>{" "}
              {space.users.total === 1 ? "usuari" : "usuaris"} · {space.users.active_last_7_days}{" "}
              {space.users.active_last_7_days === 1 ? "actiu" : "actius"} els últims 7 dies
            </p>
            <p className="mt-2 text-caption text-muted">
              Els usuaris els gestiona el client. La vista detallada des de Tuvi queda preparada per a més endavant.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}

export default function CompanyDetailPage() {
  const { companyId } = useParams<{ companyId: string }>();
  const detail = useResource<PlatformCompanyDetail>(`/platform/companies/${companyId}`);
  const [target, setTarget] = useState<StatusChangeTarget | null>(null);

  if (detail.loading && !detail.data) return <PageSkeleton variant="detail" title="Empresa" context="carregant la fitxa…" />;
  if (!detail.data) {
    return (
      <LoadErrorState
        what="la fitxa de l'empresa"
        message={detail.error ?? ""}
        status={detail.status}
        onRetry={() => void detail.reload()}
      />
    );
  }

  const company = detail.data;
  const status = company.status.platform_status;
  const statusTarget = { id: company.id, name: displayName(company), status, pilot_ends_at: company.status.pilot_ends_at };
  return (
    <div className="space-y-6">
      <nav aria-label="Ruta" className="flex items-center gap-1.5 text-[13px] text-muted">
        <Link href={PLATFORM_HOME} className="text-brand-strong hover:text-accent-800">
          Empreses
        </Link>
        <ChevronRight size={13} aria-hidden />
        <span className="text-ink">{displayName(company)}</span>
      </nav>

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex items-center gap-4">
          <span className="flex h-12 w-12 items-center justify-center rounded-card border border-border bg-subtle font-heading text-[15px] font-semibold text-muted">
            {initials(displayName(company))}
          </span>
          <div>
            <h1 className="flex flex-wrap items-center gap-2.5 font-heading text-page font-semibold text-ink">
              {displayName(company)} <CompanyStatusPill status={status} />
            </h1>
            <p className="text-body text-muted">
              {company.legal_name} · NIF {company.tax_id ?? "—"} · client des del {formatDate(company.created_at)}
            </p>
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="secondary">
              Canviar estat <ChevronDown size={15} aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            {status !== "active" && (
              <DropdownMenuItem onSelect={() => setTarget({ company: statusTarget, to: "active" })}>Convertir en activa</DropdownMenuItem>
            )}
            {status === "pilot" && (
              <DropdownMenuItem onSelect={() => setTarget({ company: statusTarget, to: "extend" })}>Allargar el pilot</DropdownMenuItem>
            )}
            {status !== "pilot" && (
              <DropdownMenuItem onSelect={() => setTarget({ company: statusTarget, to: "pilot" })}>Tornar a pilot</DropdownMenuItem>
            )}
            {status !== "paused" && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem destructive onSelect={() => setTarget({ company: statusTarget, to: "paused" })}>
                  Pausar empresa
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      <div role="tablist" aria-label="Seccions de la fitxa" className="flex border-b border-border">
        <span role="tab" aria-selected="true" className="-mb-px border-b-2 border-brand px-4 py-2.5 text-body font-semibold text-ink">
          Resum
        </span>
        {["Licitacions", "Activitat", "Usuaris"].map((label) => (
          <span key={label} role="tab" aria-selected="false" aria-disabled="true" className="flex items-center gap-1.5 px-4 py-2.5 text-body text-disabled">
            {label}
            <span className="rounded-pill bg-neutral-100 px-1.5 text-[11px] text-neutral-600">Aviat</span>
          </span>
        ))}
      </div>

      <ManagedByTuvi company={company} onChange={setTarget} />
      <ClientSpace company={company} />

      <StatusChangeDialog target={target} onClose={() => setTarget(null)} onChanged={(next) => detail.setData(next)} />
    </div>
  );
}
