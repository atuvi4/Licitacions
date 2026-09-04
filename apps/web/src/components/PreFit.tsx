"use client";

/**
 * Presentació del pre-fit.
 *
 * Regla que travessa tot el fitxer: **un número sol no val res**. Cada
 * puntuació que es mostra va acompanyada del motiu, i les dimensions que no
 * s'han pogut avaluar es diuen explícitament en comptes de comptar com a zero.
 * Si algun dia algú vol ensenyar només la xifra, aquest és el lloc on s'ha de
 * discutir.
 */

import { Badge, Card, cn } from "@/components/ui";
import {
  DIMENSION_LABELS,
  DIMENSION_ORDER,
  MATCH_STATUS_HELP,
  MATCH_STATUS_LABELS,
  MATCH_STATUS_TONE,
  PREFIT_DISCLAIMER,
  isUnknown,
  lotSummary,
  reasonLabel,
  scoreTone,
  shortSummary,
  type MatchFactor,
  type TenderMatch,
} from "@/lib/matching";
import { formatDateTime } from "@/lib/tenders";

const SCORE_TEXT = {
  success: "text-success",
  warning: "text-warning",
  neutral: "text-muted",
} as const;

/** El número gran, o un guió si no n'hi ha. Mai un 0 inventat. */
export function ScoreValue({
  score,
  size = "md",
}: {
  score: number | null;
  size?: "sm" | "md" | "lg";
}) {
  const classes = { sm: "text-[15px]", md: "text-xl", lg: "text-3xl" }[size];
  if (score === null) {
    return <span className={cn(classes, "font-semibold text-faint tabular-nums")}>—</span>;
  }
  return (
    <span className={cn(classes, "font-semibold tabular-nums", SCORE_TEXT[scoreTone(score)])}>
      {score}
    </span>
  );
}

/** Distintiu compacte per a una fila del feed. */
export function PreFitBadge({ match }: { match: TenderMatch | null }) {
  if (!match) {
    return <span className="text-[12px] text-faint">Sense calcular</span>;
  }
  return (
    <div className="flex items-center gap-2 whitespace-nowrap">
      <ScoreValue score={match.score} />
      <Badge tone={MATCH_STATUS_TONE[match.status]}>{MATCH_STATUS_LABELS[match.status]}</Badge>
      {match.is_stale && (
        <span title="El perfil o la licitació han canviat des de l'últim càlcul.">
          <Badge tone="warning">Desactualitzat</Badge>
        </span>
      )}
    </div>
  );
}

/** Una línia amb el motiu que més pesa i, si n'hi ha, el resum de lots. */
export function PreFitSummary({ match }: { match: TenderMatch | null }) {
  if (!match) return null;
  const lots = lotSummary(match);
  return (
    <p className="mt-1 text-[12px] text-muted">
      {shortSummary(match)}
      {lots && <span className="text-faint"> · {lots}</span>}
    </p>
  );
}

/** Avís obligatori: això no ha llegit cap plec. */
export function PreFitDisclaimer({ className }: { className?: string }) {
  return (
    <p
      className={cn(
        "rounded-md border border-warning-soft bg-warning-soft/40 px-3 py-2 text-[12px] text-warning",
        className,
      )}
    >
      {PREFIT_DISCLAIMER}
    </p>
  );
}

function FactorRow({ factor }: { factor: MatchFactor }) {
  const unknown = isUnknown(factor);
  const ratio = unknown ? 0 : (factor.score ?? 0) / factor.max_score;
  const bar =
    ratio >= 0.75 ? "bg-success" : ratio >= 0.4 ? "bg-warning" : ratio > 0 ? "bg-danger" : "bg-border-strong";

  return (
    <div className="border-t border-border px-5 py-3 first:border-t-0">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] font-medium text-ink">
          {DIMENSION_LABELS[factor.dimension]}
        </span>
        <span className="whitespace-nowrap text-[13px] tabular-nums">
          {unknown ? (
            <span className="text-faint">no avaluable</span>
          ) : (
            <>
              <span className="font-medium text-ink">{Math.round(factor.score ?? 0)}</span>
              <span className="text-faint"> / {factor.max_score}</span>
            </>
          )}
        </span>
      </div>

      <div className="mt-1.5 h-1 w-full overflow-hidden rounded-full bg-subtle">
        {!unknown && (
          <div className={cn("h-full rounded-full", bar)} style={{ width: `${ratio * 100}%` }} />
        )}
      </div>

      <p className={cn("mt-1.5 text-[12px]", unknown ? "text-faint" : "text-muted")}>
        {reasonLabel(factor.reason_code)}
      </p>

      {(factor.company_value || factor.source_value) && (
        <p className="mt-0.5 text-[11px] text-faint">
          {factor.company_value && <>Perfil: {factor.company_value}</>}
          {factor.company_value && factor.source_value && " · "}
          {factor.source_value && <>Licitació: {factor.source_value}</>}
        </p>
      )}
    </div>
  );
}

/** Desglossament complet per a la fitxa d'una licitació. */
export function PreFitPanel({ match }: { match: TenderMatch | null }) {
  if (!match) {
    return (
      <Card>
        <div className="px-5 py-6 text-center">
          <p className="text-[13px] text-muted">Aquesta licitació encara no té pre-fit calculat.</p>
          <p className="mt-1 text-[12px] text-faint">
            Es calcula per a totes les licitacions ingerides; recalcula&apos;l des del feed.
          </p>
        </div>
      </Card>
    );
  }

  const ordered = DIMENSION_ORDER.map((dimension) =>
    match.factors.find((factor) => factor.dimension === dimension),
  ).filter((factor): factor is MatchFactor => Boolean(factor));

  const unknowns = ordered.filter(isUnknown);
  const coverage = Math.round(match.data_coverage * 100);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="text-[15px] font-semibold text-ink">Pre-fit amb el vostre perfil</h2>
          <p className="mt-0.5 text-[12px] text-muted">{MATCH_STATUS_HELP[match.status]}</p>
        </div>
        <div className="text-right">
          <ScoreValue score={match.score} size="lg" />
          <div className="mt-1">
            <Badge tone={MATCH_STATUS_TONE[match.status]}>
              {MATCH_STATUS_LABELS[match.status]}
            </Badge>
          </div>
        </div>
      </div>

      {match.exclusion_reason_code && (
        <div className="border-b border-border bg-subtle px-5 py-3">
          <p className="text-[13px] font-medium text-ink">
            {match.exclusion_kind === "status"
              ? "Fora de termini"
              : "Descartada per una preferència vostra"}
          </p>
          <p className="mt-0.5 text-[12px] text-muted">
            {reasonLabel(match.exclusion_reason_code)}
            {match.exclusion_kind === "business" && (
              <span className="text-faint">
                {" "}
                · Ho podeu canviar al perfil d&apos;empresa; no és cap impediment legal.
              </span>
            )}
          </p>
        </div>
      )}

      <div>
        {ordered.map((factor) => (
          <FactorRow key={factor.dimension} factor={factor} />
        ))}
      </div>

      {unknowns.length > 0 && (
        <div className="border-t border-border bg-subtle px-5 py-3">
          <p className="text-[12px] font-medium text-ink">
            {unknowns.length === 1 ? "Dada no disponible" : "Dades no disponibles"}
          </p>
          <p className="mt-0.5 text-[12px] text-muted">
            {unknowns.map((factor) => DIMENSION_LABELS[factor.dimension]).join(", ")}. No compta
            com a desencaix: senzillament no s&apos;ha pogut valorar. La puntuació surt del{" "}
            {coverage} % del pes que sí que s&apos;ha pogut avaluar.
          </p>
        </div>
      )}

      {match.lot_matches.length > 0 && (
        <div className="border-t border-border px-5 py-4">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-[13px] font-medium text-ink">Per lots</h3>
            <span className="text-[12px] text-muted">{lotSummary(match)}</span>
          </div>
          <p className="mt-0.5 text-[12px] text-faint">
            La puntuació de l&apos;expedient és la del millor lot, no la mitjana.
          </p>
          <ul className="mt-2 space-y-1.5">
            {match.lot_matches.map((lot) => (
              <li
                key={lot.id}
                className={cn(
                  "flex items-baseline justify-between gap-3 rounded-md px-2.5 py-1.5",
                  lot.tender_lot_id === match.best_lot_id ? "bg-brand-soft" : "bg-subtle",
                )}
              >
                <div className="min-w-0">
                  <p className="truncate text-[13px] text-ink">
                    {lot.lot_number !== null && (
                      <span className="text-muted">Lot {lot.lot_number} · </span>
                    )}
                    {lot.lot_title ?? "Sense títol"}
                  </p>
                  <p className="truncate text-[11px] text-muted">{shortSummary(lot)}</p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <ScoreValue score={lot.score} size="sm" />
                  <Badge tone={MATCH_STATUS_TONE[lot.status]}>
                    {MATCH_STATUS_LABELS[lot.status]}
                  </Badge>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="border-t border-border px-5 py-3">
        <PreFitDisclaimer />
        <p className="mt-2 text-[11px] text-faint">
          Calculat el {formatDateTime(match.calculated_at)} · regles{" "}
          <span className="font-mono">{match.rules_version}</span>
          {match.is_stale && " · el perfil o la licitació han canviat des de llavors"}
        </p>
      </div>
    </Card>
  );
}
