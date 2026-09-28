"use client";

import { useEffect, useRef, useState } from "react";

import { useResource } from "@/lib/hooks";
import { type ReviewHistoryEntry, reviewPath } from "@/lib/review";
import { formatDateTime } from "@/lib/tenders";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";
const PREVIEW = 3;

/**
 * Historial de revisió d'un element, només lectura: acció, estat resultant,
 * nota i data. Es torna a llegir quan canvia la revisió. Cap càrrega interna.
 */
export function ReviewHistory({
  companyId,
  tenderId,
  itemId,
  revision,
  currentUserId,
}: {
  companyId: string;
  tenderId: string;
  itemId: string;
  /** Només per tornar a llegir quan l'element canvia. */
  revision: number;
  currentUserId: string | null;
}) {
  const [all, setAll] = useState(false);
  const history = useResource<ReviewHistoryEntry[]>(reviewPath(companyId, tenderId, `/items/${itemId}/history`));
  // Una acció nova (la revisió puja): es torna a llegir. La primera lectura ja la fa `useResource`.
  const seen = useRef(revision);
  const { reload } = history;
  useEffect(() => {
    if (seen.current === revision) return;
    seen.current = revision;
    void reload();
  }, [revision, reload]);
  const entries = [...(history.data ?? [])].reverse();
  const shown = all ? entries : entries.slice(0, PREVIEW);

  return (
    <section aria-labelledby="detall-historial">
      <h3 id="detall-historial" className={KICKER}>
        Historial de revisió
      </h3>
      {history.loading && !history.data && <p className="mt-1.5 text-[13px] text-muted">Carregant l&apos;historial…</p>}
      {history.error && !history.loading && (
        <p className="mt-1.5 text-[13px] text-muted">
          No s&apos;ha pogut llegir l&apos;historial.{" "}
          <button type="button" className="text-brand-strong underline" onClick={() => void history.reload()}>
            Torna-ho a provar
          </button>
        </p>
      )}
      {history.data && (
        <>
          <ol className="mt-1.5 divide-y divide-border border-y border-border">
            {shown.map((entry) => (
              <li key={entry.revision_no} className="py-2">
                <p className="flex flex-wrap items-baseline justify-between gap-x-3 text-[13px]">
                  <span className="font-medium text-ink">
                    {entry.action_label} <span className="font-normal text-muted">→ {entry.status_label}</span>
                  </span>
                  <span className="text-caption text-faint">
                    {formatDateTime(entry.created_at)}
                    {currentUserId && entry.actor_id === currentUserId ? " · tu" : ""}
                  </span>
                </p>
                {entry.note && <p className="mt-0.5 text-[13px] leading-5 text-ink">{entry.note}</p>}
              </li>
            ))}
          </ol>
          {entries.length > PREVIEW && (
            <button
              type="button"
              className="mt-1.5 text-[13px] text-brand-strong underline decoration-1 underline-offset-2 hover:text-accent-800"
              aria-expanded={all}
              onClick={() => setAll((value) => !value)}
            >
              {all ? "Mostra només les últimes" : `Veure tot l'historial (${entries.length})`}
            </button>
          )}
        </>
      )}
    </section>
  );
}
