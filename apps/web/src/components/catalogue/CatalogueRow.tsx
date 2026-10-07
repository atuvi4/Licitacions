import Link from "next/link";

import { PreFitSummary } from "@/components/catalogue/PreFitSummary";
import { LinkButton, StatusPill } from "@/components/ui";
import { formatDate, formatMoney } from "@/lib/format";
import { TENDER_STATUS_LEVEL, deadlineLevel } from "@/lib/labels";
import type { TenderMatch } from "@/lib/matching";
import {
  CONTRACT_TYPE_LABELS,
  PROCEDURE_TYPE_LABELS,
  TENDER_STATUS_LABELS,
  daysUntilDeadline,
  labelOf,
  sourceLabel,
  type SourceFacet,
  type TenderListItem,
} from "@/lib/tenders";

export function DeadlineCell({ value }: { value: string | null }) {
  const days = daysUntilDeadline(value);
  if (!value || days === null) {
    return (
      <StatusPill level="neutral" size="sm">
        Sense termini
      </StatusPill>
    );
  }
  const text = days < 0 ? "Tancat" : days === 0 ? "Avui" : `${days} ${days === 1 ? "dia" : "dies"}`;
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <StatusPill level={deadlineLevel(days)} size="sm">
        {text}
      </StatusPill>
      <span className="text-caption whitespace-nowrap text-muted tabular-nums">{formatDate(value)}</span>
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase">{label}</dt>
      <dd className="mt-0.5 text-[13.5px] text-ink">{children}</dd>
    </div>
  );
}

/**
 * Una licitació del catàleg. Sempre és una targeta (una columna al mòbil, una
 * graella a partir de `md`): mai una taula que s'hagi de desplaçar de costat.
 *
 * L'estat és el que diu la font. «Anul·lada» només surt amb `status ===
 * "cancelled"`; «Ja no disponible a la font» és presència a la font, no un
 * estat de la licitació.
 */
export function CatalogueRow({
  tender,
  match,
  sources,
}: {
  tender: TenderListItem;
  match: TenderMatch | null;
  sources?: SourceFacet[];
}) {
  const mainCpv = tender.cpvs.find((cpv) => cpv.is_main) ?? tender.cpvs[0] ?? null;
  const source = sourceLabel(tender.source_code, sources);
  const amount = tender.estimated_value ?? tender.base_budget_without_tax ?? tender.base_budget;
  const detail = `/licitacions/${tender.id}`;
  return (
    <article
      data-catalogue-row
      className="flex min-w-0 flex-col gap-3 border-b border-border px-4 py-3.5 last:border-b-0 md:grid md:grid-cols-[minmax(0,1fr)_auto] md:gap-x-6"
    >
      <div className="min-w-0">
        <h3 className="text-[15px] leading-snug font-medium text-ink">
          <Link href={detail} className="hover:text-brand-strong">
            {tender.title}
          </Link>
        </h3>
        <p className="mt-0.5 text-caption text-muted">
          {[tender.buyer?.name, tender.expediente_number].filter(Boolean).join(" · ") || "—"}
        </p>
        <p className="text-caption text-muted">
          {labelOf(PROCEDURE_TYPE_LABELS, tender.procedure_type, "Tipus desconegut")} ·{" "}
          {labelOf(CONTRACT_TYPE_LABELS, tender.contract_type, "Tipus desconegut")}
          {tender.lot_count > 0 && ` · ${tender.lot_count} ${tender.lot_count === 1 ? "lot" : "lots"}`}
        </p>

        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5 lg:grid-cols-4">
          <Fact label="Termini">
            <DeadlineCell value={tender.deadline_at} />
          </Fact>
          <Fact label="Import">
            {amount ? (
              <span className="whitespace-nowrap tabular-nums">{formatMoney(amount, tender.currency)}</span>
            ) : (
              <span className="text-muted">Import no informat</span>
            )}
          </Fact>
          <Fact label="CPV principal">
            {mainCpv ? (
              <span className="break-words">
                <span className="tabular-nums">{mainCpv.cpv_code}</span>
                {mainCpv.description && <span className="text-muted"> · {mainCpv.description}</span>}
              </span>
            ) : (
              <span className="text-muted">Sense CPV</span>
            )}
          </Fact>
          <Fact label="Font">{source ?? <span className="text-muted">Sense font</span>}</Fact>
        </dl>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <StatusPill level={Object.hasOwn(TENDER_STATUS_LEVEL, tender.status) ? TENDER_STATUS_LEVEL[tender.status] : "neutral"} size="sm">
            {labelOf(TENDER_STATUS_LABELS, tender.status, "Estat desconegut")}
          </StatusPill>
          {tender.source_removed_at && (
            <StatusPill level="neutral" size="sm">
              Ja no disponible a la font
            </StatusPill>
          )}
          {tender.is_modified && tender.source_publication_count > 1 && (
            <StatusPill level="attention" size="sm">
              Publicada {tender.source_publication_count} cops
            </StatusPill>
          )}
        </div>
      </div>

      <div className="flex min-w-0 flex-col gap-3 md:w-56 md:items-end">
        <div className="md:text-right">
          <PreFitSummary match={match} />
        </div>
        <div className="flex flex-wrap gap-2">
          <LinkButton href={detail} variant="secondary" size="sm" className="min-h-11 md:min-h-0">
            Veure
          </LinkButton>
          <LinkButton href={`${detail}?tab=analisi`} variant="primary" size="sm" className="min-h-11 md:min-h-0">
            Analitzar
          </LinkButton>
        </div>
      </div>
    </article>
  );
}
