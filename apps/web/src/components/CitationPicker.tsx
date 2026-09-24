"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import { useSession } from "@/components/SessionProvider";
import { Banner, Button, Field, Input, Select, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import type { SourceRefCitation, TenderDocumentMeta } from "@/lib/requirements";

/**
 * Triar l'evidència d'un requisit sense tocar cap identificador.
 *
 * Tot el que es veu són noms: el document, la versió i la pàgina. Els
 * identificadors els resol aquest component a partir del que la persona tria, i
 * és tota la raó per la qual existeix: el servei de provinença ja hi era des
 * d'S3-S4, i el que faltava era que ningú hagués de copiar un UUID.
 *
 * Dues menes de cita, perquè són dues coses diferents:
 *
 * * d'un PDF o un TXT se'n pot citar **la pàgina** —i, si es vol, el tros
 *   exacte, que el servidor treu del text real i no del que escrigui ningú;
 * * d'un XLSX o un DOCX només se'n pot dir **on mirar**. Que no en sapiguem
 *   llegir el text és una limitació nostra, no un fet del document, i
 *   convertir-lo en pàgines seria inventar-se una precisió que no tenim.
 */
export function CitationPicker({
  tenderId,
  onAdded,
}: {
  tenderId: string;
  onAdded: (citation: SourceRefCitation) => void;
}) {
  const { companyId } = useSession();
  const [documents, setDocuments] = useState<TenderDocumentMeta[]>([]);
  const [documentId, setDocumentId] = useState("");
  const [pages, setPages] = useState<Array<{ id: string; page_number: number }>>([]);
  const [pageId, setPageId] = useState("");
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!companyId) return;
    let cancelled = false;
    void api
      .get<TenderDocumentMeta[]>(`/companies/${companyId}/tenders/${tenderId}/documents`)
      .then((rows) => {
        if (!cancelled) setDocuments(Array.isArray(rows) ? rows : []);
      })
      .catch(() => {
        if (!cancelled) setDocuments([]);
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, tenderId]);

  const document = useMemo(
    () => documents.find((item) => item.id === documentId) ?? null,
    [documentId, documents],
  );
  const file = document?.latest_file ?? null;
  // Es pot citar una pàgina només si d'aquest document n'hem llegit text.
  const readable = !!file && file.analysis_status === "extracted";

  // Les pàgines d'aquell document, pel seu número. Cap identificador a la vista.
  useEffect(() => {
    setPageId("");
    setPages([]);
    if (!companyId || !file || !readable || !file.extraction_id) return;
    let cancelled = false;
    void api
      .get<{ items: Array<{ id: string; page_number: number }> }>(
        `/companies/${companyId}/tender-document-extractions/${file.extraction_id}/pages?limit=100`,
      )
      .then((page) => {
        if (!cancelled) setPages(page.items ?? []);
      })
      .catch(() => {
        if (!cancelled) setPages([]);
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, file, readable]);

  const add = useCallback(async () => {
    if (!companyId || !document || !file) return;
    setSaving(true);
    setError(null);
    const root = `/companies/${companyId}/tenders/${tenderId}/source-refs`;
    try {
      const citation = readable
        ? await api.post<SourceRefCitation>(`${root}/extracted-page`, {
            extraction_id: file.extraction_id,
            page_id: pageId,
            location_label: label.trim() || null,
          })
        : await api.post<SourceRefCitation>(`${root}/document-file`, {
            tender_document_file_id: file.file_id,
            location_label: label.trim() || null,
          });
      onAdded(citation);
      setLabel("");
      setPageId("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No s'ha pogut afegir la cita.");
    } finally {
      setSaving(false);
    }
  }, [companyId, document, file, label, onAdded, pageId, readable, tenderId]);

  const incomplete = !document || !file || (readable && !pageId);

  return (
    <div className="grid gap-3 rounded-card border border-border px-4 py-3">
      {error && (
        <Banner level="blocking" kicker="Cita" action={null}>
          {error}
        </Banner>
      )}

      <Field label="Document">
        <Select
          aria-label="Document"
          value={documentId}
          onChange={(event) => setDocumentId(event.target.value)}
          placeholder="Tria un document"
          options={documents.map((item) => ({
            value: item.id,
            label: item.latest_file
              ? `${item.name} · v${item.latest_file.version_number}`
              : item.name,
          }))}
        />
      </Field>

      {document && !readable && (
        // No és cap error: el fitxer hi és i està guardat. El que no tenim és
        // el seu text, i per tant la persona ha de dir on mirar.
        <p className="text-caption text-muted">
          Text no extraïble — indica on trobar l&apos;evidència.
        </p>
      )}

      {readable && (
        <Field label="Pàgina">
          <Select
            aria-label="Pàgina"
            value={pageId}
            onChange={(event) => setPageId(event.target.value)}
            placeholder="Tria una pàgina"
            options={pages.map((page) => ({
              value: page.id,
              label: `Pàgina ${page.page_number}`,
            }))}
          />
        </Field>
      )}

      <Field
        label="On, exactament"
        hint={readable ? "Opcional." : "Per exemple: «Full Càlculs, fila 42»."}
      >
        <Input
          value={label}
          onChange={(event) => setLabel(event.target.value)}
          placeholder={readable ? "Clàusula 7.2" : "Full Càlculs, fila 42"}
        />
      </Field>

      <div>
        <Button
          variant="secondary"
          size="sm"
          loading={saving}
          disabled={incomplete}
          onClick={() => void add()}
        >
          Afegeix la cita
        </Button>
      </div>
    </div>
  );
}

/**
 * Les cites d'un requisit, i com treure'n una.
 *
 * Treure-la desenganxa l'enllaç: la cita **no** s'esborra, perquè pot
 * sostenir-ne d'altres i era certa el dia que es va crear. I no s'edita mai:
 * si cal corregir-la, se'n crea una de nova i es desenganxa l'anterior.
 */
export function CitationList({
  citations,
  onRemove,
}: {
  citations: SourceRefCitation[];
  onRemove?: (citation: SourceRefCitation) => void;
}) {
  if (citations.length === 0) {
    return (
      <p className="text-caption text-muted">
        Cap evidència adjunta. No vol dir que sigui incorrecte: vol dir que
        ningú no n&apos;ha dit la font.
      </p>
    );
  }
  return (
    <ul className="grid gap-2">
      {citations.map((citation) => (
        <li
          key={citation.id}
          className="flex flex-wrap items-center justify-between gap-2 rounded-card border border-border px-3 py-2"
        >
          <span className="min-w-0 text-caption text-muted">
            <span className="text-ink">{citation.document_name ?? "Document"}</span>
            {citation.version_number ? ` · v${citation.version_number}` : ""}
            {citation.page_number ? ` · p. ${citation.page_number}` : ""}
            {citation.location_label ? ` · ${citation.location_label}` : ""}
            {citation.quote ? ` · «${citation.quote}»` : ""}
          </span>
          {onRemove ? (
            <Button variant="ghost" size="sm" onClick={() => onRemove(citation)}>
              Treu
            </Button>
          ) : (
            <StatusPill level="neutral" size="sm">
              Citat
            </StatusPill>
          )}
        </li>
      ))}
    </ul>
  );
}
