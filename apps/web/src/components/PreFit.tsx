"use client";

/**
 * Presentació del pre-fit.
 *
 * Regla que travessa tot el fitxer: **un número sol no val res**. Cada
 * puntuació que es mostra va acompanyada del motiu, i les dimensions que no
 * s'han pogut avaluar es diuen explícitament en comptes de comptar com a zero.
 * Si algun dia algú vol ensenyar només la xifra, aquest és el lloc on s'ha de
 * discutir. El pre-fit no és cap probabilitat de guanyar, i cap component
 * d'aquí l'anomena així.
 */

import Link from "next/link";

import { Button, Card, EmptyState, LinkButton, StatusPill, cn } from "@/components/ui";
import { MATCH_STATUS_LEVEL } from "@/lib/labels";
import {
  DIMENSION_LABELS,
  DIMENSION_ORDER,
  MATCH_STATUS_LABELS,
  PREFIT_DISCLAIMER,
  isUnknown,
  lotSummary,
  profileGaps,
  reasonLabel,
  scoreTone,
  shortSummary,
  sourceGaps,
  statusHelp,
  type MatchFactor,
  type TenderMatch,
} from "@/lib/matching";
import { formatDateTime } from "@/lib/tenders";

/**
 * Fons de la xifra (handoff L6.12, B): el llindar de candidatura (60) i el
 * d'encaix alt (80) surten de `scoreTone`; ≥ 80 va sobre acer sòlid (brand,
 * AA amb blanc), 60–79 sobre accent-300 i < 60 sobre accent-200.
 */
const SCORE_FILL = {
  success: "bg-brand text-white",
  warning: "bg-accent-300 text-accent-900",
  neutral: "bg-accent-200 text-accent-900",
} as const;

const SCORE_SIZE = {
  sm: "min-w-8 px-1.5 text-[13px] leading-5",
  md: "min-w-9 px-2 text-[15px] leading-6",
  lg: "min-w-12 px-2.5 text-[22px] leading-8",
} as const;

/** El número, o un guió si no n'hi ha. Mai un 0 inventat. */
export function ScoreValue({
  score,
  size = "md",
}: {
  score: number | null;
  size?: "sm" | "md" | "lg";
}) {
  if (score === null) {
    return (
      <span className={cn("inline-flex justify-center font-semibold text-faint tabular-nums", SCORE_SIZE[size])}>
        —
      </span>
    );
  }
  const tone = scoreTone(score);
  return (
    <span
      data-score-tone={tone}
      className={cn(
        "inline-flex items-center justify-center font-semibold tabular-nums",
        SCORE_SIZE[size],
        SCORE_FILL[tone],
      )}
    >
      {score}
    </span>
  );
}

/** Una línia amb el motiu que més pesa i, si n'hi ha, el resum de lots. */
export function PreFitSummary({ match }: { match: TenderMatch | null }) {
  if (!match) return null;
  const lots = lotSummary(match);
  return (
    <p className="mt-1 text-caption text-muted">
      {shortSummary(match)}
      {lots && <span className="text-faint"> · {lots}</span>}
    </p>
  );
}

/** Avís obligatori: això no ha llegit cap plec i no és cap probabilitat de guanyar. */
export function PreFitDisclaimer({ className }: { className?: string }) {
  return (
    <p className={cn("border border-border-strong bg-subtle px-3 py-2 text-caption text-muted", className)}>
      {PREFIT_DISCLAIMER} L&apos;encaix mesura la coincidència de CPV, territori, import i termini amb
      el perfil: no és una probabilitat de guanyar.
    </p>
  );
}

function FactorRow({ factor }: { factor: MatchFactor }) {
  const unknown = isUnknown(factor);
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

      <p className={cn("mt-1.5 text-caption", unknown ? "text-faint" : "text-muted")}>
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

/**
 * Una licitació sense pre-fit (3B-2 §7.1.4). No es calcula per a totes: només per
 * a les empreses amb CPV, territoris o preferències de contracte al perfil. Sense
 * perfil es diu això; amb perfil, es pot calcular per a aquesta sola licitació.
 * `profileReady = null`: encara no se sap el perfil, i no s'afirma res.
 */
export function PreFitMissing({
  profileReady,
  calculating,
  error,
  onCalculate,
}: {
  profileReady: boolean | null;
  calculating: boolean;
  error: string | null;
  onCalculate: () => void;
}) {
  if (profileReady === false) {
    return (
      <Card>
        <EmptyState
          title="Sense pre-fit per a aquesta licitació"
          description="El pre-fit només es calcula quan el perfil té CPV, territoris o preferències de contracte"
          action={
            <LinkButton href="/empresa/radar" variant="primary" size="md">
              Preferències del Radar
            </LinkButton>
          }
        />
      </Card>
    );
  }
  return (
    <Card>
      <EmptyState
        title="Encara no s'ha calculat"
        description="El pre-fit d'aquesta licitació encara no s'ha calculat amb el vostre perfil."
        action={
          profileReady ? (
            <Button variant="primary" onClick={onCalculate} loading={calculating} disabled={calculating}>
              Calcula&apos;l ara
            </Button>
          ) : undefined
        }
      />
      {error && <p className="px-5 pb-4 text-center text-caption text-danger">{error}</p>}
    </Card>
  );
}

/** Desglossament complet per a la fitxa d'una licitació. */
export function PreFitPanel({ match }: { match: TenderMatch | null }) {
  if (!match) {
    return (
      <Card>
        <div className="px-5 py-6 text-center">
          <p className="text-[13px] text-muted">Aquesta licitació encara no té pre-fit calculat.</p>
          <p className="mt-1 text-caption text-faint">
            Només es calcula amb un perfil que tingui CPV, territoris o preferències de contracte.
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
  // Qui no té la dada: el perfil de l'empresa o la licitació (motius del motor).
  const missingInProfile = profileGaps(match);
  const missingInSource = sourceGaps(match);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="font-heading text-[17px] font-semibold text-ink">Pre-fit amb el vostre perfil</h2>
          <p className="mt-0.5 text-caption text-muted">{statusHelp(match)}</p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <ScoreValue score={match.score} size="lg" />
          <StatusPill level={MATCH_STATUS_LEVEL[match.status]} size="sm">
            {MATCH_STATUS_LABELS[match.status]}
          </StatusPill>
        </div>
      </div>

      {match.exclusion_reason_code && (
        <div className="border-b border-border bg-subtle px-5 py-3">
          <p className="text-[13px] font-medium text-ink">
            {match.exclusion_kind === "status"
              ? "Fora de termini"
              : "Descartada per una preferència vostra"}
          </p>
          <p className="mt-0.5 text-caption text-muted">
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
          <p className="text-caption font-medium text-ink">
            {unknowns.length === 1 ? "Dada no disponible" : "Dades no disponibles"}
          </p>
          <p className="mt-0.5 text-caption text-muted">
            {unknowns.map((factor) => DIMENSION_LABELS[factor.dimension]).join(", ")}. No compta
            com a desencaix: senzillament no s&apos;ha pogut valorar. La puntuació surt del{" "}
            {coverage} % del pes que sí que s&apos;ha pogut avaluar.
          </p>
          {missingInProfile.length > 0 && (
            <p className="mt-1.5 text-caption text-ink" data-gap="profile">
              Falta al perfil de l&apos;empresa:{" "}
              {missingInProfile.map((dimension) => DIMENSION_LABELS[dimension]).join(", ")}.{" "}
              <Link href="/empresa/radar" className="text-brand-strong underline hover:text-accent-800">
                Preferències del Radar →
              </Link>
            </p>
          )}
          {missingInSource.length > 0 && (
            <p className="mt-1 text-caption text-ink" data-gap="source">
              La licitació no publica:{" "}
              {missingInSource.map((dimension) => DIMENSION_LABELS[dimension]).join(", ")}.
            </p>
          )}
        </div>
      )}

      {match.lot_matches.length > 0 && (
        <div className="border-t border-border px-5 py-4">
          <div className="flex items-baseline justify-between gap-3">
            <h3 className="text-[13px] font-medium text-ink">Per lots</h3>
            <span className="text-caption text-muted">{lotSummary(match)}</span>
          </div>
          <p className="mt-0.5 text-caption text-faint">
            La puntuació de l&apos;expedient és la del millor lot, no la mitjana.
          </p>
          <ul className="mt-2 space-y-1.5">
            {match.lot_matches.map((lot) => (
              <li
                key={lot.id}
                className={cn(
                  "flex items-baseline justify-between gap-3 px-2.5 py-1.5",
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
                  <StatusPill level={MATCH_STATUS_LEVEL[lot.status]} size="sm">
                    {MATCH_STATUS_LABELS[lot.status]}
                  </StatusPill>
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
