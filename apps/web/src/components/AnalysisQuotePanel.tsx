"use client";

/**
 * Panell de confirmació d'una anàlisi: el volum abans de fer despesa.
 *
 * Mostra el que el backend ha desat com a preview (documents, pàgines, chunks,
 * perfil i model, estimació de tokens si n'hi ha). No hi ha cap import en
 * diners: encara no hi ha billing. Si el preview ja no val, el backend en torna
 * un de nou i el panell el substitueix: l'usuari l'ha de tornar a confirmar.
 */

import { Button, Spinner } from "@/components/ui";
import type { AnalysisQuote } from "@/lib/jobs";

const SKIPPED_REASONS: Record<string, string> = {
  document_not_processed: "no processat",
  document_not_analyzable: "sense text analitzable",
  document_without_text: "sense text",
};

function formatTokens(value: number | null): string {
  return value === null ? "—" : value.toLocaleString("ca-ES");
}

export function AnalysisQuotePanel({
  quote,
  notice,
  canConfirm,
  confirming,
  onConfirm,
  onCancel,
}: {
  quote: AnalysisQuote;
  notice: string | null;
  canConfirm: boolean;
  confirming: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const reused = quote.reusable_run_id !== null && quote.chunks_to_send === 0;
  const blocked = !quote.allowed;

  return (
    <div className="border-b border-border px-5 py-4 text-[13px]">
      <p className="font-medium text-ink">Abans d&apos;analitzar: aquest és el volum</p>
      {notice && <p className="mt-1 text-warning">{notice}</p>}

      <ul className="mt-3 space-y-1">
        {quote.documents.map((document) => (
          <li key={document.tender_document_id} className="flex justify-between gap-4">
            <span className="truncate">{document.name}</span>
            <span className="shrink-0 text-muted">{document.pages} pàgines</span>
          </li>
        ))}
        {quote.skipped_documents.map((document) => (
          <li key={document.tender_document_id} className="flex justify-between gap-4 text-muted">
            <span className="truncate">{document.name}</span>
            <span className="shrink-0">
              fora ({SKIPPED_REASONS[document.reason] ?? document.reason})
            </span>
          </li>
        ))}
      </ul>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-1">
        <dt className="text-muted">Pàgines</dt>
        <dd>
          {quote.pages_in_plan} de {quote.pages_total}
        </dd>
        <dt className="text-muted">Chunks a enviar</dt>
        <dd>
          {quote.chunks_to_send}
          {quote.chunks_to_send !== quote.chunks ? ` (de ${quote.chunks})` : ""} · màxim{" "}
          {quote.max_chunks}
        </dd>
        <dt className="text-muted">Estimació de tokens</dt>
        <dd>
          {quote.estimated_input_tokens === null
            ? "Sense històric per estimar"
            : `~${formatTokens(quote.estimated_input_tokens)} entrada · ~${formatTokens(
                quote.estimated_output_tokens,
              )} sortida`}
        </dd>
        <dt className="text-muted">Perfil i model</dt>
        <dd>
          {quote.profile} · {quote.model}
        </dd>
      </dl>

      {quote.pages_truncated && (
        <p className="mt-2 text-warning">
          El plec supera el màxim de pàgines per anàlisi: només se n&apos;analitzaran{" "}
          {quote.pages_in_plan}.
        </p>
      )}
      {reused && (
        <p className="mt-2 text-muted">
          Ja hi ha una anàlisi vigent d&apos;aquests plecs: no es tornarà a executar.
        </p>
      )}
      {blocked && (
        <p className="mt-2 text-danger">
          Aquesta anàlisi supera el límit del pilot ({quote.max_chunks} chunks) i no es pot llançar.
        </p>
      )}
      {!canConfirm && (
        <p className="mt-2 text-muted">
          El teu rol pot veure el volum, però no confirmar l&apos;anàlisi.
        </p>
      )}

      <div className="mt-3 flex gap-2">
        <Button
          variant="primary"
          onClick={onConfirm}
          disabled={confirming || blocked || !canConfirm}
        >
          {confirming ? <Spinner /> : "Confirma i analitza"}
        </Button>
        <Button variant="ghost" onClick={onCancel} disabled={confirming}>
          Cancel·la
        </Button>
      </div>
    </div>
  );
}
