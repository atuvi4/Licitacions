"use client";

/**
 * Administració Tuvi · Empreses: llistat de clients amb estat, propietari,
 * usuaris, candidatures i última activitat; pestanyes per estat, cerca,
 * ordenació i accions per fila. «Nova empresa» obre el calaix d'alta.
 */

import { MoreHorizontal, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { usePlatformSession } from "@/components/platform/PlatformSession";
import { NewCompanyDrawer } from "@/components/platform/NewCompanyDrawer";
import { CompanyStatusPill, StatusChangeDialog, type StatusChangeTarget } from "@/components/platform/StatusChange";
import { Button, Card, ErrorNotice, PageHeader, PageSkeleton, Tabs } from "@/components/ui";
import {
  type ColumnDef,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
  SortableTable,
} from "@/components/ui/shadcn";
import { useResource } from "@/lib/hooks";
import {
  displayName,
  initials,
  PLATFORM_HOME,
  PLATFORM_STATUS_LABELS,
  type PlatformCompanyList,
  type PlatformCompanyRow,
  type PlatformStatus,
  relativeTime,
} from "@/lib/platform";

type Tab = "totes" | PlatformStatus;
const TAB_TO_STATUS: Record<Tab, PlatformStatus | null> = { totes: null, active: "active", pilot: "pilot", paused: "paused" };
const STATUSES: PlatformStatus[] = ["pilot", "active", "paused"];

function RowActions({ row, onChange }: { row: PlatformCompanyRow; onChange: (target: StatusChangeTarget) => void }) {
  const router = useRouter();
  const company = { id: row.id, name: displayName(row), status: row.platform_status, pilot_ends_at: row.pilot_ends_at };
  return (
    <div className="flex items-center justify-end gap-2">
      <Link
        href={`${PLATFORM_HOME}/${row.id}`}
        className="font-heading text-[13px] font-semibold text-brand-strong hover:text-accent-800"
      >
        Obrir
      </Link>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" variant="secondary" aria-label={`Accions de ${company.name}`} className="px-2">
            <MoreHorizontal size={16} aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem onSelect={() => router.push(`${PLATFORM_HOME}/${row.id}`)}>Obrir fitxa</DropdownMenuItem>
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>Canviar estat</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              {STATUSES.filter((status) => status !== row.platform_status && status !== "paused").map((status) => (
                <DropdownMenuItem key={status} onSelect={() => onChange({ company, to: status })}>
                  {status === "active" ? "Convertir en activa" : "Tornar a pilot"}
                </DropdownMenuItem>
              ))}
              {row.platform_status === "pilot" && (
                <DropdownMenuItem onSelect={() => onChange({ company, to: "extend" })}>Allargar el pilot</DropdownMenuItem>
              )}
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuItem
            disabled={!row.tax_id}
            onSelect={() => {
              if (row.tax_id) void navigator.clipboard?.writeText(row.tax_id).catch(() => undefined);
            }}
          >
            Copiar NIF
          </DropdownMenuItem>
          {row.platform_status !== "paused" && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem destructive onSelect={() => onChange({ company, to: "paused" })}>
                Pausar empresa
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

function CompaniesContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { setCompaniesCount } = usePlatformSession();
  const companies = useResource<PlatformCompanyList>("/platform/companies");
  const [target, setTarget] = useState<StatusChangeTarget | null>(null);
  const tab = (searchParams.get("estat") as Tab | null) ?? "totes";
  const query = searchParams.get("q") ?? "";
  const drawerOpen = searchParams.get("nova") === "1";

  useEffect(() => {
    if (companies.data) setCompaniesCount(companies.data.counts.all);
  }, [companies.data, setCompaniesCount]);

  function setParam(key: string, value: string | null) {
    const params = new URLSearchParams(searchParams.toString());
    if (value === null) params.delete(key);
    else params.set(key, value);
    const qs = params.toString();
    router.replace(qs ? `${PLATFORM_HOME}?${qs}` : PLATFORM_HOME, { scroll: false });
  }

  const columns = useMemo<Array<ColumnDef<PlatformCompanyRow, unknown>>>(
    () => [
      {
        id: "empresa",
        header: "Empresa",
        // El text de cerca inclou el NIF i el nom legal, encara que no es vegin tots dos.
        accessorFn: (row) => `${displayName(row)} ${row.legal_name} ${row.tax_id ?? ""}`,
        sortingFn: (a, b) => displayName(a.original).localeCompare(displayName(b.original), "ca"),
        cell: ({ row }) => (
          <span className="flex items-center gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-control bg-subtle font-heading text-[11px] font-semibold text-muted">
              {initials(displayName(row.original))}
            </span>
            <span className="min-w-0">
              <span className="block font-medium text-ink">{displayName(row.original)}</span>
              <span className="block text-caption text-muted">NIF {row.original.tax_id ?? "—"}</span>
            </span>
          </span>
        ),
      },
      {
        id: "estat",
        header: "Estat",
        accessorFn: (row) => PLATFORM_STATUS_LABELS[row.platform_status],
        cell: ({ row }) => <CompanyStatusPill status={row.original.platform_status} />,
      },
      {
        id: "propietari",
        header: "Propietari",
        accessorFn: (row) => `${row.owner?.full_name ?? ""} ${row.owner?.email ?? ""}`,
        sortingFn: (a, b) => (a.original.owner?.full_name ?? "").localeCompare(b.original.owner?.full_name ?? "", "ca"),
        cell: ({ row }) => row.original.owner?.full_name ?? row.original.owner?.email ?? "—",
      },
      { id: "usuaris", header: "Usuaris", accessorKey: "users_count", enableGlobalFilter: false, meta: { align: "right" } },
      { id: "licitacions", header: "Licitacions", accessorKey: "tenders_count", enableGlobalFilter: false, meta: { align: "right" } },
      {
        id: "activitat",
        header: "Última activitat",
        accessorFn: (row) => (row.last_activity_at ? Date.parse(row.last_activity_at) : 0),
        enableGlobalFilter: false,
        cell: ({ row }) => <span className="text-muted">{relativeTime(row.original.last_activity_at)}</span>,
      },
      {
        id: "accions",
        header: "Accions",
        enableSorting: false,
        meta: { align: "right" },
        enableGlobalFilter: false,
        cell: ({ row }) => <RowActions row={row.original} onChange={setTarget} />,
      },
    ],
    [],
  );

  const counts = companies.data?.counts;
  const status = TAB_TO_STATUS[tab] ?? null;
  const rows = (companies.data?.items ?? []).filter((row) => status === null || row.platform_status === status);

  return (
    <>
      <PageHeader
        title="Empreses"
        description="Empreses clients de Tuvi. Cada client administra la seva empresa des de la seva pròpia administració."
        actions={
          <Button variant="primary" onClick={() => setParam("nova", "1")}>
            <Plus size={16} aria-hidden />
            Nova empresa
          </Button>
        }
      />
      {companies.error && <ErrorNotice message={companies.error} onRetry={() => void companies.reload()} />}
      <Card className="mt-5 overflow-hidden">
        <Tabs
          param="estat"
          value={tab}
          onChange={() => undefined}
          items={[
            { key: "totes", label: "Totes", count: counts?.all },
            { key: "active", label: "Actives", count: counts?.active },
            { key: "pilot", label: "Pilot", count: counts?.pilot },
            { key: "paused", label: "Pausades", count: counts?.paused },
          ]}
          className="px-2"
        />
        {companies.loading && !companies.data ? (
          <PageSkeleton variant="table" title="" context="carregant les empreses…" />
        ) : (
          <SortableTable
            // La cerca de la barra superior arriba per `?q=`: es torna a muntar amb el text nou.
            key={query}
            label="Empreses clients"
            columns={columns}
            data={rows}
            getRowId={(row) => row.id}
            searchPlaceholder="Nom, NIF o propietari"
            initialSearch={query}
            initialSorting={[{ id: "activitat", desc: true }]}
            pageSize={10}
            emptyState={counts?.all === 0 ? "Encara no hi ha cap empresa client. Dona'n d'alta la primera." : "Cap empresa amb aquest filtre."}
          />
        )}
      </Card>

      <NewCompanyDrawer
        open={drawerOpen}
        onClose={() => setParam("nova", null)}
        onCreated={() => void companies.reload()}
      />
      <StatusChangeDialog target={target} onClose={() => setTarget(null)} onChanged={() => void companies.reload()} />
    </>
  );
}

export default function CompaniesPage() {
  return (
    <Suspense fallback={<PageSkeleton variant="table" title="Empreses" context="carregant…" />}>
      <CompaniesContent />
    </Suspense>
  );
}
