"use client";

import { useState } from "react";

import { Modal } from "@/components/Modal";
import { useCompanyPath, useSession } from "@/components/SessionProvider";
import {
  Badge,
  Button,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  LoadingRows,
  Select,
  cn,
} from "@/components/ui";
import { api } from "@/lib/api";
import {
  DOCUMENT_CATEGORY_LABELS,
  formatDate,
  formatFileSize,
  optionsFrom,
  RELATIONSHIP_LABELS,
} from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import { fileAccept, uploadHint, validateFile } from "@/lib/uploads";
import type {
  DocumentCategory,
  DocumentItem,
  Evidence,
  LinkedEntityType,
  RelationshipType,
} from "@/lib/types";

interface Props {
  entityType: LinkedEntityType;
  entityId: string;
  defaultRelationship: RelationshipType;
  defaultCategory: DocumentCategory;
  title?: string;
  description?: string;
}

/**
 * Panell "DADA -> EVIDENCIA": mostra i gestiona els documents que justifiquen
 * una fila concreta (una certificacio, un projecte, una polissa...).
 */
export function EvidencePanel({
  entityType,
  entityId,
  defaultRelationship,
  defaultCategory,
  title = "Evidències",
  description = "Documents que justifiquen aquesta dada davant d'un òrgan de contractació.",
}: Props) {
  const path = useCompanyPath();
  const { config } = useSession();
  const evidenceUrl = path(`/evidence/${entityType}/${entityId}`);
  const { data, loading, error, reload } = useResource<Evidence[]>(evidenceUrl);
  const mutation = useMutation();

  const [mode, setMode] = useState<"upload" | "library" | null>(null);
  const [relationship, setRelationship] = useState<string>(defaultRelationship);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  // Quan el fitxer ja existeix a la biblioteca (mateix SHA-256), l'API el
  // rebutja i ens diu quin es. Aleshores oferim vincular-lo en comptes de
  // guardar-ne una copia.
  const duplicate =
    mutation.apiError?.code === "duplicate_document"
      ? {
          id: String(mutation.apiError.details.existing_document_id ?? ""),
          name: String(mutation.apiError.details.existing_document_name ?? "document"),
        }
      : null;

  const library = useResource<DocumentItem[]>(mode === "library" ? path("/documents") : null);
  const evidence = data ?? [];

  function close() {
    setMode(null);
    setFile(null);
    setFileError(null);
    setSelected(null);
    setSearch("");
    setRelationship(defaultRelationship);
    mutation.reset();
  }

  async function submitUpload(event: React.FormEvent) {
    event.preventDefault();
    if (!file || fileError) return;
    const form = new FormData();
    form.append("file", file);
    form.append("name", file.name);
    form.append("category", defaultCategory);
    form.append("entity_type", entityType);
    form.append("entity_id", entityId);
    form.append("relationship_type", relationship);

    const ok = await mutation.run(async () => {
      await api.upload(path("/documents"), form);
      await reload();
    });
    if (ok) close();
  }

  async function linkDocument(documentId: string) {
    const ok = await mutation.run(async () => {
      await api.post(path(`/documents/${documentId}/links`), {
        entity_type: entityType,
        entity_id: entityId,
        relationship_type: relationship,
      });
      await reload();
    });
    if (ok) close();
  }

  async function submitLink(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;
    await linkDocument(selected);
  }

  async function unlink(item: Evidence) {
    await mutation.run(async () => {
      await api.delete(path(`/documents/${item.document.id}/links/${item.link_id}`));
      await reload();
    });
  }

  const filteredLibrary = (library.data ?? []).filter((document) =>
    search ? document.name.toLowerCase().includes(search.toLowerCase()) : true,
  );

  return (
    <div>
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div>
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          <p className="mt-0.5 text-[13px] text-muted">{description}</p>
        </div>
        <div className="flex gap-2">
          <Button size="sm" variant="secondary" onClick={() => setMode("library")}>
            Vincula existent
          </Button>
          <Button size="sm" variant="primary" onClick={() => setMode("upload")}>
            Puja document
          </Button>
        </div>
      </div>

      {error && (
        <div className="p-4">
          <ErrorNotice message={error} onRetry={() => void reload()} />
        </div>
      )}

      {loading ? (
        <LoadingRows rows={2} />
      ) : evidence.length === 0 ? (
        <EmptyState
          title="Sense evidència documental"
          description="Sense document justificatiu, aquesta dada no es podrà acreditar en una licitació."
        />
      ) : (
        <ul className="divide-y divide-border">
          {evidence.map((item) => (
            <li key={item.link_id} className="flex items-center justify-between gap-4 px-5 py-3">
              <div className="min-w-0">
                <p className="truncate text-[13px] font-medium text-ink">{item.document.name}</p>
                <p className="mt-0.5 text-[12px] text-muted">
                  {formatFileSize(item.document.file_size)} · Pujat el{" "}
                  {formatDate(item.document.uploaded_at)}
                  {item.document.expires_at && ` · Caduca el ${formatDate(item.document.expires_at)}`}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <Badge tone="brand">{RELATIONSHIP_LABELS[item.relationship_type]}</Badge>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void api.download(
                      path(`/documents/${item.document.id}/download`),
                      item.document.original_filename,
                    )
                  }
                >
                  Descarrega
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void unlink(item)}>
                  Desvincula
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <Modal
        open={mode === "upload"}
        title="Puja un document com a evidència"
        onClose={close}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.saving}>
              Cancel·la
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="evidence-upload"
              loading={mutation.saving}
              disabled={!file || fileError !== null}
            >
              Puja i vincula
            </Button>
          </>
        }
      >
        <form id="evidence-upload" onSubmit={submitUpload} className="space-y-4">
          {mutation.error && !duplicate && <ErrorNotice message={mutation.error} />}
          {duplicate && (
            <div className="rounded-md border border-warning/30 bg-warning-soft px-4 py-3">
              <p className="text-[13px] text-warning">
                Aquest fitxer ja és a la biblioteca com a «{duplicate.name}». No cal pujar-lo
                dues vegades.
              </p>
              <Button
                size="sm"
                variant="secondary"
                className="mt-2"
                onClick={() => void linkDocument(duplicate.id)}
              >
                Vincula el document existent
              </Button>
            </div>
          )}
          <Field label="Fitxer" required hint={uploadHint(config)} error={fileError ?? undefined}>
            <Input
              type="file"
              required
              accept={fileAccept(config)}
              onChange={(event) => {
                const selected = event.target.files?.[0] ?? null;
                setFile(selected);
                setFileError(validateFile(selected, config));
              }}
              className="h-auto py-1.5 file:mr-3 file:rounded file:border-0 file:bg-subtle file:px-2 file:py-1 file:text-[12px]"
            />
          </Field>
          <Field label="Tipus de relació" hint="Què acredita aquest document?">
            <Select
              value={relationship}
              options={optionsFrom(RELATIONSHIP_LABELS)}
              onChange={(event) => setRelationship(event.target.value)}
            />
          </Field>
          <p className="text-[12px] text-faint">
            El document quedarà també a la biblioteca documental amb la categoria «
            {DOCUMENT_CATEGORY_LABELS[defaultCategory]}».
          </p>
        </form>
      </Modal>

      <Modal
        open={mode === "library"}
        title="Vincula un document existent"
        onClose={close}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.saving}>
              Cancel·la
            </Button>
            <Button
              variant="primary"
              type="submit"
              form="evidence-link"
              loading={mutation.saving}
              disabled={!selected}
            >
              Vincula
            </Button>
          </>
        }
      >
        <form id="evidence-link" onSubmit={submitLink} className="space-y-4">
          {mutation.error && <ErrorNotice message={mutation.error} />}
          <Field label="Tipus de relació">
            <Select
              value={relationship}
              options={optionsFrom(RELATIONSHIP_LABELS)}
              onChange={(event) => setRelationship(event.target.value)}
            />
          </Field>
          <Field label="Cerca a la biblioteca">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Nom del document"
            />
          </Field>

          {library.loading ? (
            <LoadingRows rows={3} />
          ) : filteredLibrary.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-muted">
              No hi ha documents disponibles a la biblioteca.
            </p>
          ) : (
            <ul className="max-h-72 divide-y divide-border overflow-y-auto rounded-md border border-border scroll-thin">
              {filteredLibrary.map((document) => (
                <li key={document.id}>
                  <button
                    type="button"
                    onClick={() => setSelected(document.id)}
                    className={cn(
                      "flex w-full items-center justify-between gap-3 px-3 py-2.5 text-left transition-colors",
                      selected === document.id ? "bg-brand-soft" : "hover:bg-subtle",
                    )}
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium text-ink">
                        {document.name}
                      </span>
                      <span className="block text-[12px] text-muted">
                        {DOCUMENT_CATEGORY_LABELS[document.category]} ·{" "}
                        {formatFileSize(document.file_size)}
                      </span>
                    </span>
                    {selected === document.id && <Badge tone="brand">Seleccionat</Badge>}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </form>
      </Modal>
    </div>
  );
}
