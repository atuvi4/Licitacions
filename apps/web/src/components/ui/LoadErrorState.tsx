import type { ReactNode } from "react";

import { Button } from "./Button";
import { ErrorState } from "./ErrorState";

export interface LoadErrorStateProps {
  /** Què no s'ha pogut carregar, en minúscula: «les pòlisses», «el projecte». */
  what: string;
  message: string;
  /** Codi HTTP, si l'API n'ha donat un: decideix entre E1, E3 i E4. */
  status?: number | null;
  onRetry: () => void;
  /** Una sortida alternativa (tornar a una llista, obrir Oportunitats…). */
  secondary?: ReactNode;
  className?: string;
}

/**
 * Error de càrrega d'un recurs (handoff, Estats transversals E1/E3/E4): una
 * frase, què no s'ha perdut, i el reintent. El codi només al detall tècnic.
 */
export function LoadErrorState({ what, message, status = null, onRetry, secondary, className }: LoadErrorStateProps) {
  const gone = status === 404;
  const ours = status !== null && status >= 500;
  const title = gone
    ? `${what.charAt(0).toUpperCase()}${what.slice(1)} ja no hi ${what.startsWith("les ") || what.startsWith("els ") ? "són" : "és"}`
    : ours
      ? "Alguna cosa ha fallat pel nostre costat"
      : `No s'han pogut carregar ${what}`;
  const description = gone
    ? "L'enllaç és antic o el registre s'ha eliminat. El que hi havia fet continua a l'historial de l'empresa."
    : ours
      ? "No és cosa teva. La resta de l'aplicació funciona: pots continuar per una altra banda mentre ho mirem."
      : "La connexió amb el servei ha fallat. No s'ha perdut res del que ja hi havia.";
  return (
    <ErrorState
      className={className}
      title={title}
      description={description}
      action={
        <Button variant="primary" size="md" onClick={onRetry}>
          Torna-ho a provar
        </Button>
      }
      secondary={secondary}
      technical={{ code: status !== null ? String(status) : message, method: "GET" }}
    />
  );
}
