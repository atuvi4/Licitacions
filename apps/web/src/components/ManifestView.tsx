"use client";

import { useEffect, useState } from "react";

import { Modal } from "@/components/Modal";
import { Button, LoadingRows, StatusPill, cn } from "@/components/ui";
import { api } from "@/lib/api";
import { formatAmount, formatQuantity } from "@/lib/economic";
import { formatDate } from "@/lib/format";
import {
  MANIFEST_FORMAT_LABELS,
  hasFullContent,
  manifestDocuments,
  manifestFormat,
  manifestLines,
  manifestOffer,
  manifestRelevance,
  manifestSolutions,
  manifestValidations,
  pricingBasisLabel,
  readManifest,
  type Manifest,
} from "@/lib/submission";

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

function Section({ title, count, children }: { title: string; count?: number; children: React.ReactNode }) {
  return (
    <section className="border-t border-border pt-3 first:border-t-0 first:pt-0">
      <h3 className={cn(KICKER, "flex items-center gap-2")}>
        {title}
        {count !== undefined && <span className="tabular-nums">{count}</span>}
      </h3>
      <div className="mt-1.5">{children}</div>
    </section>
  );
}

/** Una empremta llarga no és per llegir-la: es mostra escurçada i sencera al títol. */
function Fingerprint({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-muted">no consta</span>;
  return (
    <code className="font-mono text-[12.5px] break-all text-muted" title={value}>
      {value.length > 16 ? `${value.slice(0, 16)}…` : value}
    </code>
  );
}

/**
 * El manifest congelat, en paraules.
 *
 * Es llegeix el JSON que va desar el servidor i es pinta el que hi ha: ni es
 * recalcula cap hash, ni es completa cap camp que hi falti. El JSON cru queda
 * al detall tècnic, com a la resta de l'aplicació, perquè la vista principal
 * l'ha de poder entendre qui aprova, no qui depura.
 */
export function ManifestView({
  open,
  root,
  packageId,
  revisionNumber,
  manifestHash,
  onClose,
}: {
  open: boolean;
  root: string;
  packageId: string;
  revisionNumber: number;
  manifestHash: string | null;
  onClose: () => void;
}) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [raw, setRaw] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const payload = await api.get<unknown>(`${root}/packages/${packageId}/manifest`);
        if (cancelled) return;
        setRaw(payload);
        setManifest(readManifest(payload));
      } catch (caught) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "Error desconegut");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, root, packageId]);

  const format = manifestFormat(manifest?.manifest_version);
  const documents = manifestDocuments(manifest);
  const solutions = manifestSolutions(manifest);
  const offer = manifestOffer(manifest);
  const lines = manifestLines(offer);
  const validations = manifestValidations(manifest);
  const relevance = manifestRelevance(manifest);
  const coverage = manifest?.content?.coverage;
  const currency = offer?.currency ?? "EUR";
  // El manifest tal com va quedar desat. Única excepció a la regla de lint: viu
  // plegat al detall tècnic, per a qui l'hagi de comparar o reportar.
  const rawText = raw === null ? "" : JSON.stringify(raw, null, 2);

  return (
    <Modal
      open={open}
      title={`Manifest de la revisió ${revisionNumber}`}
      description="El que es va congelar: la identitat, les metadades i el hash dels documents, juntament amb el contingut tècnic i econòmic."
      size="lg"
      onClose={onClose}
      footer={
        <Button variant="secondary" onClick={onClose}>
          Tanca
        </Button>
      }
    >
      {loading && (
        <div aria-busy="true" aria-label="Es carrega el manifest">
          <LoadingRows rows={4} />
        </div>
      )}

      {error && !loading && (
        <div role="alert" className="border border-danger bg-danger-soft px-3.5 py-3">
          <p className="text-body font-semibold text-danger">No s&apos;ha pogut carregar el manifest</p>
          <p className="mt-1 text-body text-ink">{error}</p>
        </div>
      )}

      {!loading && !error && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill level={format === "current" ? "ready" : "neutral"} size="sm" outline>
              {MANIFEST_FORMAT_LABELS[format]}
            </StatusPill>
            {!hasFullContent(manifest) && (
              <span className="text-caption text-muted">
                Aquest format no descrivia ni la solució tècnica ni l&apos;oferta econòmica.
              </span>
            )}
          </div>

          {coverage && (
            <Section title="Cobertura">
              <p className="text-body text-ink">
                {coverage.mandatory_covered ?? 0} de {coverage.mandatory_total ?? 0} requisits obligatoris coberts ·{" "}
                {coverage.document_count ?? documents.length} documents
              </p>
            </Section>
          )}

          <Section title="Documents" count={documents.length}>
            {documents.length === 0 ? (
              <p className="text-body text-muted">El manifest no porta cap document.</p>
            ) : (
              <ul className="divide-y divide-border border-y border-border">
                {documents.map((document) => (
                  <li key={`${document.document_id ?? document.position}`} className="py-1.5">
                    <p className="text-body text-ink">{document.filename}</p>
                    <p className="text-caption text-muted">
                      {document.requirement_title ?? "Sense requisit associat"}
                      {document.expires_at ? ` · caduca el ${formatDate(document.expires_at)}` : ""}
                      {" · "}
                      <Fingerprint value={document.sha256} />
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {hasFullContent(manifest) && (
            <Section title="Solució tècnica" count={solutions.length}>
              {solutions.length === 0 ? (
                <p className="text-body text-muted">Cap solució tècnica congelada.</p>
              ) : (
                <ul className="divide-y divide-border border-y border-border">
                  {solutions.map((solution) => (
                    <li key={solution.solution_id} className="py-1.5">
                      <p className="text-body text-ink">{solution.title ?? "Sense títol"}</p>
                      <p className="text-caption text-muted">
                        {[solution.supplier_name, solution.product_name].filter(Boolean).join(" · ") || "Sense proveïdor"}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </Section>
          )}

          {hasFullContent(manifest) && (
            <Section title="Oferta econòmica" count={lines.length}>
              {!offer ? (
                <p className="text-body text-muted">Cap oferta econòmica congelada.</p>
              ) : (
                <>
                  <table className="w-full border-collapse text-body">
                    <thead>
                      <tr>
                        <th scope="col" className={cn("border-b border-border-strong bg-subtle px-2 py-1 text-left", KICKER)}>
                          Concepte
                        </th>
                        <th scope="col" className={cn("border-b border-border-strong bg-subtle px-2 py-1 text-right", KICKER)}>
                          Quantitat
                        </th>
                        <th scope="col" className={cn("border-b border-border-strong bg-subtle px-2 py-1 text-left", KICKER)}>
                          Base
                        </th>
                        <th scope="col" className={cn("border-b border-border-strong bg-subtle px-2 py-1 text-right", KICKER)}>
                          Preu ofert
                        </th>
                        <th scope="col" className={cn("border-b border-border-strong bg-subtle px-2 py-1 text-right", KICKER)}>
                          Subtotal
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {lines.map(({ lot, section, line }) => (
                        <tr key={line.line_id} className="border-b border-border">
                          <td className="px-2 py-1.5 align-top">
                            <p className="text-ink">{line.concept}</p>
                            <p className="text-caption text-muted">
                              {lot.label ?? "Lot"} · {section.title}
                            </p>
                          </td>
                          <td className="px-2 py-1.5 text-right align-top tabular-nums">
                            {line.quantity === null ? "—" : formatQuantity(line.quantity)}
                          </td>
                          <td className="px-2 py-1.5 align-top">{pricingBasisLabel(line.pricing_basis)}</td>
                          <td className="px-2 py-1.5 text-right align-top tabular-nums">
                            {formatAmount(line.offered_unit_price_ex_vat, currency)}
                          </td>
                          <td className="px-2 py-1.5 text-right align-top font-medium tabular-nums">
                            {formatAmount(line.amounts?.revenue ?? null, currency)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="mt-1.5 flex justify-between text-body">
                    <span className="text-muted">Total ofert (sense IVA)</span>
                    <span className="font-semibold text-ink tabular-nums">{formatAmount(offer.amounts?.revenue ?? null, currency)}</span>
                  </p>
                </>
              )}
            </Section>
          )}

          <Section title="Base d'aprovació">
            <p className="text-body text-ink">
              Sobre què es va decidir: l&apos;anàlisi vigent, la pertinença confirmada de {relevance.length}{" "}
              {relevance.length === 1 ? "evidència" : "evidències"}
              {hasFullContent(manifest) && (
                <>
                  , l&apos;abast tècnic revisat i {validations.length} {validations.length === 1 ? "validació" : "validacions"} tècniques
                </>
              )}
              .
            </p>
            <p className="mt-1 text-caption text-muted">
              Si això canvia, qui va aprovar ja no aprovaria el mateix, encara que el text ofert fos idèntic.
            </p>
          </Section>

          <details className="border-t border-border pt-3">
            <summary className="cursor-pointer text-caption text-muted">Detall tècnic</summary>
            <dl className="mt-1.5 space-y-1 text-caption">
              <div className="flex justify-between gap-3">
                <dt className="text-muted">Versió del manifest</dt>
                <dd>
                  <code className="font-mono text-[12.5px]">{manifest?.manifest_version ?? "no consta"}</code>
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted">Hash del manifest</dt>
                <dd>
                  <Fingerprint value={manifestHash} />
                </dd>
              </div>
              <div className="flex justify-between gap-3">
                <dt className="text-muted">Empremta de l&apos;abast tècnic</dt>
                <dd>
                  <Fingerprint value={manifest?.approval_basis?.technical_scope_fingerprint} />
                </dd>
              </div>
            </dl>
            <pre className="scroll-thin mt-2 max-h-64 overflow-auto border border-border bg-subtle p-2 font-mono text-[11.5px] break-all whitespace-pre-wrap">
              {rawText}
            </pre>
          </details>
        </div>
      )}
    </Modal>
  );
}
