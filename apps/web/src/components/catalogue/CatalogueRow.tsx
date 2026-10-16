import Link from "next/link";

import { cpvMatch, cpvMatchText, type CpvMatch } from "@/components/catalogue/cpvMatch";
import { PreFitSummary } from "@/components/catalogue/PreFitSummary";
import { LinkButton, StatusPill } from "@/components/ui";
import { formatAmount, formatDate } from "@/lib/format";
import { deadlineCountdownView, tenderStatusView } from "@/lib/labels";
import type { TenderMatch } from "@/lib/matching";
import {
  AMOUNT_BASIS_LABEL,
  AMOUNT_BASIS_SHORT,
  AMOUNT_HELP,
  AMOUNT_NOT_PUBLISHED,
  AMOUNT_VEC_FACT_LABEL,
  CONTRACT_TYPE_LABELS,
  PROCEDURE_TYPE_LABELS,
  SOURCE_FIRST_CPV_HELP,
  SOURCE_FIRST_CPV_SHORT_HELP,
  cpvDescription,
  labelOf,
  sourceLabel,
  tenderAmounts,
  type SourceFacet,
  type TenderListItem,
} from "@/lib/tenders";

/** Quin CPV de la fila ha fet coincidir el filtre (C4), quan n'hi ha un d'aplicat. */
export function CpvMatchLine({ match }: { match: CpvMatch }) {
  return (
    <p data-cpv-match={match.kind} className={`mt-0.5 text-caption ${match.kind === "main" ? "text-success" : "text-brand-strong"}`}>
      {cpvMatchText(match)}
    </p>
  );
}

/** El comptador de dies de `lib/deadline` (A11): el mateix que a la fitxa i al workspace. */
export function DeadlineCell({ value }: { value: string | null }) {
  const countdown = deadlineCountdownView(value);
  if (!value || countdown.days === null) {
    return (
      <StatusPill level="neutral" size="sm">
        Sense termini
      </StatusPill>
    );
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <StatusPill level={countdown.level} size="sm">
        {countdown.text}
      </StatusPill>
      <span className="text-caption whitespace-nowrap text-muted tabular-nums">{formatDate(value)}</span>
    </div>
  );
}

/** L'estat vigent (A12) i, si el termini mana, l'estat de la font a part. */
export function TenderStatusPills({ tender }: { tender: { status: string; deadline_at: string | null } }) {
  const view = tenderStatusView(tender);
  return (
    <>
      <StatusPill level={view.level} size="sm">
        {view.label}
      </StatusPill>
      {view.sourceStatus && <span className="text-caption text-muted">Estat a la font: {view.sourceStatus}</span>}
    </>
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
  cpvFilter,
}: {
  tender: TenderListItem;
  match: TenderMatch | null;
  sources?: SourceFacet[];
  /** El filtre de CPV aplicat, si n'hi ha: la fila diu per quin CPV hi entra (C4). */
  cpvFilter?: string | null;
}) {
  // `is_main` és el primer CPV que publica la font. Sense cap de marcat, el que es mostra és un
  // qualsevol de la llista (l'API ordena la resta per codi): no es pot dir «primer de la font».
  const markedCpv = tender.cpvs.find((cpv) => cpv.is_main) ?? null;
  const mainCpv = markedCpv ?? tender.cpvs[0] ?? null;
  const mainDescription = mainCpv ? cpvDescription(mainCpv) : null;
  const matched = cpvMatch(tender.cpvs, cpvFilter);
  const source = sourceLabel(tender.source_code, sources);
  // P2-06: la xifra principal és la mateixa que filtra i ordena (VEC → PBL), sempre amb la seva base.
  const amounts = tenderAmounts(tender);
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
          <Fact label={amounts.primary ? (amounts.primary.basis === "vec" ? AMOUNT_VEC_FACT_LABEL : AMOUNT_BASIS_SHORT[amounts.primary.basis]) : "Import"}>
            {amounts.primary ? (
              <span className="whitespace-nowrap tabular-nums" title={`${AMOUNT_BASIS_LABEL[amounts.primary.basis]}. ${AMOUNT_HELP}`}>
                {formatAmount(amounts.primary.value, tender.currency)}
              </span>
            ) : (
              <span className="text-muted">{AMOUNT_NOT_PUBLISHED}</span>
            )}
            {amounts.budget && (
              <p className="mt-0.5 text-caption text-muted" data-amount-budget>
                {AMOUNT_BASIS_SHORT[amounts.budget.basis]}:{" "}
                <span className="whitespace-nowrap tabular-nums">{formatAmount(amounts.budget.value, tender.currency)}</span>
              </p>
            )}
          </Fact>
          <Fact label={markedCpv ? "Primer CPV de la font" : "CPV"}>
            {mainCpv ? (
              <span className="break-words" title={markedCpv ? SOURCE_FIRST_CPV_HELP : undefined}>
                <span className="tabular-nums">{mainCpv.cpv_code}</span>
                {mainDescription && <span className="text-muted"> · {mainDescription}</span>}
              </span>
            ) : (
              <span className="text-muted">Sense CPV</span>
            )}
            {markedCpv && (
              // Visible, no només al `title` (P2-04): un mòbil o el teclat no hi arriben.
              <p className="mt-0.5 text-caption text-muted" data-cpv-provenance>
                {SOURCE_FIRST_CPV_SHORT_HELP}
              </p>
            )}
            {matched && <CpvMatchLine match={matched} />}
          </Fact>
          <Fact label="Font">{source ?? <span className="text-muted">Sense font</span>}</Fact>
        </dl>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <TenderStatusPills tender={tender} />
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
          {/* Una sola acció (addendum 3B-2, D3): «Analitzar» prometia una anàlisi que la targeta no fa. */}
          <LinkButton href={detail} variant="primary" size="sm" className="min-h-11 md:min-h-0">
            Obre la fitxa
          </LinkButton>
        </div>
      </div>
    </article>
  );
}
