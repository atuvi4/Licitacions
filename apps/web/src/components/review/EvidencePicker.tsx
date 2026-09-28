"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Button, Field, Input, Select } from "@/components/ui";
import { api } from "@/lib/api";
import {
  type CitationDraft,
  type EvidenceDocument,
  type EvidencePage,
  previewQuote,
  spanFromFragment,
  spanFromSelection,
} from "@/lib/evidence";
import { shortDocument } from "@/lib/review";
import type { TenderDocumentMeta } from "@/lib/tenders";

interface PageList {
  items: EvidencePage[];
  total: number;
}

/**
 * Triar una cita: document → pàgina → text desat → selecció. Cap identificador
 * a la vista; les posicions surten del text DESAT de la pàgina, i qualsevol
 * cas que no quadri exactament falla tancat.
 *
 * Dues maneres de triar el tros, totes dues deterministes:
 *
 * * seleccionar-lo amb el ratolí sobre el text de la pàgina;
 * * escriure'n un fragment (amb el teclat) que hi aparegui UNA sola vegada.
 */
export function EvidencePicker({
  companyId,
  tenderId,
  onChoose,
  onCancel,
}: {
  companyId: string;
  tenderId: string;
  onChoose: (draft: CitationDraft) => void;
  onCancel: () => void;
}) {
  const [documents, setDocuments] = useState<EvidenceDocument[] | null>(null);
  const [documentId, setDocumentId] = useState("");
  const [pages, setPages] = useState<EvidencePage[] | null>(null);
  const [pageId, setPageId] = useState("");
  const [fragment, setFragment] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const textRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let cancelled = false;
    // La fitxa de la licitació porta, per a cada document, la versió vigent amb la seva
    // extracció. (La llista `/companies/.../documents` no n'omple `latest_file`.)
    api
      .get<{ documents?: TenderDocumentMeta[] }>(`/tenders/${tenderId}`)
      .then((tender) => {
        if (cancelled) return;
        const rows = tender?.documents;
        // Només els documents dels quals hi ha text llegit: d'altres no se'n pot citar cap tros.
        const readable = (Array.isArray(rows) ? rows : []).flatMap((row) => {
          const file = row.latest_file;
          if (!file || file.analysis_status !== "extracted" || !file.extraction_id) return [];
          return [{
            documentId: row.id,
            name: row.name,
            label: `${shortDocument(row.name)} · v${file.version_number}`,
            extractionId: file.extraction_id,
          }];
        });
        setDocuments(readable);
      })
      .catch(() => {
        if (!cancelled) setDocuments([]);
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, tenderId]);

  const document = useMemo(() => documents?.find((d) => d.documentId === documentId) ?? null, [documents, documentId]);

  useEffect(() => {
    setPages(null);
    setPageId("");
    setProblem(null);
    if (!document) return;
    let cancelled = false;
    async function load(extractionId: string) {
      const all: EvidencePage[] = [];
      for (let offset = 0; ; offset += 100) {
        const page = await api.get<PageList>(
          `/companies/${companyId}/tender-document-extractions/${extractionId}/pages?limit=100&offset=${offset}`,
        );
        all.push(...(page.items ?? []));
        if (all.length >= page.total || (page.items ?? []).length === 0) break;
      }
      return all.sort((a, b) => a.page_number - b.page_number);
    }
    load(document.extractionId)
      .then((rows) => {
        if (!cancelled) setPages(rows);
      })
      .catch(() => {
        if (!cancelled) {
          setPages([]);
          setProblem("No s'han pogut llegir les pàgines d'aquest document.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [companyId, document]);

  const page = pages?.find((p) => p.id === pageId) ?? null;

  function choose(span: ReturnType<typeof spanFromSelection>) {
    if (!document || !page) return;
    if (!span.ok) {
      setProblem(span.reason);
      return;
    }
    setProblem(null);
    onChoose({
      extractionId: document.extractionId,
      pageId: page.id,
      pageNumber: page.page_number,
      documentName: document.name,
      charStart: span.charStart,
      charEnd: span.charEnd,
      quote: span.quote,
    });
  }

  function takeSelection() {
    const node = textRef.current?.firstChild;
    const selection = typeof window !== "undefined" ? window.getSelection() : null;
    // Falla tancat: la selecció ha de ser dins del text de la pàgina, i aquest text ha de ser el desat.
    if (!page || !node || !(node instanceof Text) || node.data !== page.text) {
      setProblem("No s'ha pogut llegir el text de la pàgina. Torna a triar-la.");
      return;
    }
    if (!selection || selection.rangeCount === 0 || selection.isCollapsed) {
      setProblem("Selecciona el text que vols citar.");
      return;
    }
    const range = selection.getRangeAt(0);
    if (range.startContainer !== node || range.endContainer !== node) {
      setProblem("Selecciona només text de dins de la pàgina.");
      return;
    }
    choose(spanFromSelection(page.text, range.startOffset, range.endOffset));
  }

  return (
    <div role="group" aria-label="Triar una cita" className="space-y-3">
      {documents === null ? (
        <p className="text-[13px] text-muted">Carregant els documents…</p>
      ) : documents.length === 0 ? (
        <p className="text-[13px] text-muted">Aquesta licitació no té cap document amb text llegit per citar.</p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_8rem]">
          <Field label="Document">
            <Select
              value={documentId}
              placeholder="Tria un document"
              options={documents.map((d) => ({ value: d.documentId, label: d.label }))}
              onChange={(event) => setDocumentId(event.target.value)}
            />
          </Field>
          <Field label="Pàgina">
            <Select
              value={pageId}
              placeholder={pages === null ? "…" : "Tria"}
              disabled={!pages || pages.length === 0}
              options={(pages ?? []).map((p) => ({ value: p.id, label: `p. ${p.page_number}` }))}
              onChange={(event) => {
                setPageId(event.target.value);
                setProblem(null);
              }}
            />
          </Field>
        </div>
      )}

      {page && (
        <>
          {page.text ? (
            <pre
              ref={textRef}
              tabIndex={0}
              aria-label={`Text de la pàgina ${page.page_number}`}
              className="max-h-64 overflow-auto border border-border bg-surface px-3 py-2 font-sans text-[13px] leading-relaxed whitespace-pre-wrap text-ink"
            >
              {page.text}
            </pre>
          ) : (
            <p className="text-[13px] text-muted">Aquesta pàgina no té text llegit.</p>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="secondary" disabled={!page.text} onClick={takeSelection}>
              Usa el text seleccionat
            </Button>
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <Field label="O escriu un fragment exacte de la pàgina" className="min-w-0 flex-1">
              <Input value={fragment} onChange={(event) => setFragment(event.target.value)} />
            </Field>
            <Button size="sm" variant="secondary" disabled={!page.text} onClick={() => choose(spanFromFragment(page.text, fragment))}>
              Cerca el fragment
            </Button>
          </div>
        </>
      )}

      {problem && (
        <p role="alert" className="text-[13px] text-danger">
          {problem}
        </p>
      )}
      <Button size="sm" variant="ghost" onClick={onCancel}>
        Cancel·la la cita
      </Button>
    </div>
  );
}

/** La cita triada, tal com es veurà: document, pàgina i text. Només previsualització. */
export function CitationPreview({ draft }: { draft: CitationDraft }) {
  return (
    <div className="border-l-2 border-accent-500 pl-2.5">
      <p className="flex flex-wrap items-center gap-x-2 text-caption text-muted">
        <span className="font-medium text-ink">{draft.documentName}</span>
        <span>p. {draft.pageNumber}</span>
      </p>
      <blockquote className="mt-1 text-[13px] leading-relaxed text-ink italic">«{previewQuote(draft.quote)}»</blockquote>
    </div>
  );
}
