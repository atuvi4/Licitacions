import { ScoreValue } from "@/components/PreFit";
import { Button, StatusPill } from "@/components/ui";
import { MATCH_STATUS_LABELS, shortSummary, type TenderMatch } from "@/lib/matching";

/**
 * L'únic avís de pre-fit sense calcular d'una llista (A9): a la capçalera, i no un
 * «Sense calcular» a cada fila. Diu el fet (no s'ha calculat) i ofereix el
 * recàlcul que ja existeix; no n'atribueix la causa al perfil, perquè la llista
 * no sap per què no s'ha calculat (revisió 3B-2, F3).
 */
export function PreFitUncalculatedNotice({
  count,
  onCalculate,
  calculating = false,
  error = null,
}: {
  count: number;
  onCalculate?: () => void;
  calculating?: boolean;
  error?: string | null;
}) {
  if (count === 0) return null;
  return (
    <div
      role="status"
      className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-subtle px-3.5 py-2 text-[13px] text-muted"
    >
      <p>
        El pre-fit no s&apos;ha calculat per a {count} {count === 1 ? "licitació" : "licitacions"} d&apos;aquesta pàgina.
        {error && <span className="block text-danger">{error}</span>}
      </p>
      {onCalculate && (
        <Button size="sm" variant="secondary" loading={calculating} onClick={onCalculate}>
          Calcula el pre-fit
        </Button>
      )}
    </div>
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
