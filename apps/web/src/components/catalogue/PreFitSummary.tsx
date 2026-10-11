import { ScoreValue } from "@/components/PreFit";
import { StatusPill } from "@/components/ui";
import { MATCH_STATUS_LABELS, shortSummary, type TenderMatch } from "@/lib/matching";

/**
 * L'únic avís de pre-fit sense calcular d'una llista (A9): a la capçalera, amb la
 * condició del perfil, i no un «Sense calcular» a cada fila.
 */
export function PreFitUncalculatedNotice({ count }: { count: number }) {
  if (count === 0) return null;
  return (
    <p role="status" className="border-b border-border bg-subtle px-3.5 py-2 text-[13px] text-muted">
      El pre-fit no s&apos;ha calculat per a {count} {count === 1 ? "licitació" : "licitacions"} d&apos;aquesta pàgina: només es
      calcula quan el perfil té CPV, territoris o preferències de contracte.
    </p>
  );
}

/**
 * El pre-fit com a dada, no com a veredicte: amb càlcul la xifra va sempre amb el
 * motiu; sense càlcul la fila no en diu res (A9): l'avís és un de sol, a la
 * capçalera de la llista, amb la condició del perfil.
 */
export function PreFitSummary({ match }: { match: TenderMatch | null }) {
  if (!match) return null;
  return (
    <div className="flex flex-col items-start gap-1 md:items-end">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-caption text-muted">Pre-fit</span>
        <ScoreValue score={match.score} size="sm" />
        {match.is_stale && (
          <StatusPill level="attention" size="sm">
            Desactualitzat
          </StatusPill>
        )}
      </div>
      <p className="text-caption text-muted">{shortSummary(match)}</p>
      {match.status !== "candidate" && <p className="text-caption text-faint">{MATCH_STATUS_LABELS[match.status]}</p>}
    </div>
  );
}
