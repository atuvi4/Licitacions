"use client";

import { useState } from "react";

import { ConfirmDialog, Modal } from "@/components/Modal";
import { useCompanyPath, useSession } from "@/components/SessionProvider";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorNotice,
  Field,
  Input,
  LoadingRows,
  PageHeader,
  Select,
  Table,
  Td,
  Textarea,
  Th,
  Tr,
  cn,
} from "@/components/ui";
import { api, cleanPayload } from "@/lib/api";
import {
  DOCUMENT_CATEGORY_LABELS,
  ENTITY_TYPE_LABELS,
  formatDate,
  formatFileSize,
  optionsFrom,
  RELATIONSHIP_LABELS,
  TEMPORAL_STATUS_LABELS,
  TEMPORAL_TONE,
} from "@/lib/format";
import { useMutation, useResource } from "@/lib/hooks";
import { fileAccept, uploadHint, validateFile } from "@/lib/uploads";
import type { DocumentItem, LinkedEntityType } from "@/lib/types";

/** Entitats que es poden justificar amb un document, i on trobar-les. */
const LINKABLE: Array<{ type: LinkedEntityType; endpoint: string; label: (row: Record<string, unknown>) => string }> = [
  { type: "company_certification", endpoint: "/certifications", label: (row) => String(row.name) },
  { type: "company_project", endpoint: "/projects", label: (row) => String(row.name) },
  {
    type: "company_insurance",
    endpoint: "/insurances",
    label: (row) => [row.type, row.provider].filter(Boolean).join(" · "),
  },
  { type: "company_registration", endpoint: "/registrations", label: (row) => String(row.name) },
  { type: "company_service", endpoint: "/services", label: (row) => String(row.name) },
  {
    type: "company_financial_year",
    endpoint: "/financial-years",
    label: (row) => `Exercici ${row.year}`,
  },
  {
    type: "company_person",
    endpoint: "/people",
    label: (row) => [row.first_name, row.last_name].filter(Boolean).join(" "),
  },
];

const EMPTY_UPLOAD = {
  name: "",
  category: "other",
  description: "",
  issued_at: "",
  expires_at: "",
};

export default function DocumentsPage() {
  const path = useCompanyPath();
  const { config } = useSession();
  const url = path("/documents");
  const { data, loading, error, reload } = useResource<DocumentItem[]>(url);
  const { saving, error: saveError, fieldErrors, run, reset } = useMutation();

  const [search, setSearch] = useState("");
  const [category, setCategory] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [uploadForm, setUploadForm] = useState(EMPTY_UPLOAD);
  const [editing, setEditing] = useState<DocumentItem | null>(null);
  const [toDelete, setToDelete] = useState<DocumentItem | null>(null);

  const [linking, setLinking] = useState<DocumentItem | null>(null);
  const [linkType, setLinkType] = useState<LinkedEntityType>("company_certification");
  const [linkRelationship, setLinkRelationship] = useState("supporting_document");
  const [linkTarget, setLinkTarget] = useState<string | null>(null);

  const linkEndpoint = LINKABLE.find((item) => item.type === linkType)?.endpoint ?? "/certifications";
  const linkOptions = useResource<Array<Record<string, unknown>>>(
    linking ? path(linkEndpoint) : null,
  );

  const documents = (data ?? []).filter((document) => {
    const matchesSearch = search
      ? document.name.toLowerCase().includes(search.toLowerCase())
      : true;
    const matchesCategory = category ? document.category === category : true;
    return matchesSearch && matchesCategory;
  });

  async function upload(event: React.FormEvent) {
    event.preventDefault();
    if (!file || fileError) return;
    const form = new FormData();
    form.append("file", file);
    form.append("name", uploadForm.name || file.name);
    form.append("category", uploadForm.category);
    if (uploadForm.description) form.append("description", uploadForm.description);
    if (uploadForm.issued_at) form.append("issued_at", uploadForm.issued_at);
    if (uploadForm.expires_at) form.append("expires_at", uploadForm.expires_at);

    const ok = await run(async () => {
      await api.upload(url, form);
      await reload();
    });
    if (ok) {
      setUploadOpen(false);
      setFile(null);
      setUploadForm(EMPTY_UPLOAD);
    }
  }

  async function saveMetadata(event: React.FormEvent) {
    event.preventDefault();
    if (!editing) return;
    const ok = await run(async () => {
      await api.patch(`${url}/${editing.id}`, cleanPayload(uploadForm));
      await reload();
    });
    if (ok) setEditing(null);
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const ok = await run(async () => {
      await api.delete(`${url}/${toDelete.id}`);
      await reload();
    });
    if (ok) setToDelete(null);
  }

  async function createLink(event: React.FormEvent) {
    event.preventDefault();
    if (!linking || !linkTarget) return;
    const ok = await run(async () => {
      await api.post(`${url}/${linking.id}/links`, {
        entity_type: linkType,
        entity_id: linkTarget,
        relationship_type: linkRelationship,
      });
      await reload();
    });
    if (ok) {
      setLinking(null);
      setLinkTarget(null);
    }
  }

  async function removeLink(document: DocumentItem, linkId: string) {
    await run(async () => {
      await api.delete(`${url}/${document.id}/links/${linkId}`);
      await reload();
    });
  }

  return (
    <>
      <PageHeader
        title="Documents"
        description="Biblioteca documental de l'empresa. Cada document té metadada pròpia i pot justificar una o més dades."
        actions={
          <Button
            variant="primary"
            onClick={() => {
              reset();
              setUploadForm(EMPTY_UPLOAD);
              setFile(null);
              setFileError(null);
              setUploadOpen(true);
            }}
          >
            Puja document
          </Button>
        }
      />

      {error && <ErrorNotice message={error} onRetry={() => void reload()} />}

      <Card>
        <div className="flex flex-wrap items-end gap-3 border-b border-border px-5 py-4">
          <Field label="Cerca" className="min-w-[220px] flex-1">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Nom del document"
            />
          </Field>
          <Field label="Categoria" className="w-56">
            <Select
              value={category}
              placeholder="Totes"
              options={optionsFrom(DOCUMENT_CATEGORY_LABELS)}
              onChange={(event) => setCategory(event.target.value)}
            />
          </Field>
        </div>

        {loading ? (
          <LoadingRows />
        ) : documents.length === 0 ? (
          <EmptyState
            title={(data ?? []).length === 0 ? "La biblioteca és buida" : "Cap resultat"}
            description={
              (data ?? []).length === 0
                ? "Puja certificats, pòlisses, títols o certificats de bona execució. Després els podràs vincular a les dades que justifiquen."
                : "Prova de canviar els filtres."
            }
          />
        ) : (
          <Table>
            <thead>
              <tr>
                <Th>Document</Th>
                <Th>Categoria</Th>
                <Th>Caducitat</Th>
                <Th>Evidència de</Th>
                <Th className="text-right">Accions</Th>
              </tr>
            </thead>
            <tbody>
              {documents.map((document) => (
                <Tr key={document.id}>
                  <Td>
                    <p className="font-medium">{document.name}</p>
                    <p className="mt-0.5 text-[12px] text-muted">
                      {document.original_filename} · {formatFileSize(document.file_size)} ·{" "}
                      {formatDate(document.uploaded_at)}
                    </p>
                    <p className="mt-0.5 font-mono text-[11px] text-faint" title={document.sha256}>
                      sha256:{document.sha256.slice(0, 12)}…
                    </p>
                  </Td>
                  <Td className="text-muted">{DOCUMENT_CATEGORY_LABELS[document.category]}</Td>
                  <Td>
                    {document.expires_at ? (
                      <>
                        <span className="tabular-nums">{formatDate(document.expires_at)}</span>
                        <Badge
                          tone={TEMPORAL_TONE[document.temporal_status]}
                          className="ml-2"
                        >
                          {TEMPORAL_STATUS_LABELS[document.temporal_status]}
                        </Badge>
                      </>
                    ) : (
                      <span className="text-faint">—</span>
                    )}
                  </Td>
                  <Td>
                    {document.links.length === 0 ? (
                      <span className="text-[12px] text-faint">Sense vincular</span>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {document.links.map((link) => (
                          <button
                            key={link.id}
                            type="button"
                            title="Elimina aquest vincle"
                            onClick={() => void removeLink(document, link.id)}
                            className={cn(
                              "rounded-full bg-brand-soft px-2 py-0.5 text-[11px] font-medium text-brand-strong",
                              "hover:bg-danger-soft hover:text-danger",
                            )}
                          >
                            {ENTITY_TYPE_LABELS[link.entity_type]} ·{" "}
                            {RELATIONSHIP_LABELS[link.relationship_type]} ×
                          </button>
                        ))}
                      </div>
                    )}
                  </Td>
                  <Td className="text-right whitespace-nowrap">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        void api.download(
                          `${url}/${document.id}/download`,
                          document.original_filename,
                        )
                      }
                    >
                      Descarrega
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        reset();
                        setLinkTarget(null);
                        setLinking(document);
                      }}
                    >
                      Vincula
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        reset();
                        setUploadForm({
                          name: document.name,
                          category: document.category,
                          description: document.description ?? "",
                          issued_at: document.issued_at ?? "",
                          expires_at: document.expires_at ?? "",
                        });
                        setEditing(document);
                      }}
                    >
                      Edita
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => setToDelete(document)}>
                      Elimina
                    </Button>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>

      {/* Pujada */}
      <Modal
        open={uploadOpen}
        title="Puja un document"
        onClose={() => setUploadOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setUploadOpen(false)} disabled={saving}>
              Cancel·la
            </Button>
            <Button
              variant="primary"
              form="upload-form"
              type="submit"
              loading={saving}
              disabled={!file || fileError !== null}
            >
              Puja
            </Button>
          </>
        }
      >
        <form id="upload-form" onSubmit={upload} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
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
          <Field label="Nom" hint="Si el deixes buit s'utilitzarà el nom del fitxer.">
            <Input
              value={uploadForm.name}
              onChange={(event) => setUploadForm({ ...uploadForm, name: event.target.value })}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Categoria">
              <Select
                value={uploadForm.category}
                options={optionsFrom(DOCUMENT_CATEGORY_LABELS)}
                onChange={(event) =>
                  setUploadForm({ ...uploadForm, category: event.target.value })
                }
              />
            </Field>
            <Field label="Data d'emissió">
              <Input
                type="date"
                value={uploadForm.issued_at}
                onChange={(event) =>
                  setUploadForm({ ...uploadForm, issued_at: event.target.value })
                }
              />
            </Field>
            <Field label="Caducitat">
              <Input
                type="date"
                value={uploadForm.expires_at}
                onChange={(event) =>
                  setUploadForm({ ...uploadForm, expires_at: event.target.value })
                }
              />
            </Field>
          </div>
          <Field label="Descripció">
            <Textarea
              rows={2}
              value={uploadForm.description}
              onChange={(event) =>
                setUploadForm({ ...uploadForm, description: event.target.value })
              }
            />
          </Field>
        </form>
      </Modal>

      {/* Edicio de metadata */}
      <Modal
        open={editing !== null}
        title="Edita la metadada"
        description="El fitxer, la mida i el hash no es poden modificar."
        onClose={() => setEditing(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setEditing(null)} disabled={saving}>
              Cancel·la
            </Button>
            <Button variant="primary" form="metadata-form" type="submit" loading={saving}>
              Desa
            </Button>
          </>
        }
      >
        <form id="metadata-form" onSubmit={saveMetadata} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <Field label="Nom" required error={fieldErrors.name}>
            <Input
              value={uploadForm.name}
              required
              onChange={(event) => setUploadForm({ ...uploadForm, name: event.target.value })}
            />
          </Field>
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Categoria">
              <Select
                value={uploadForm.category}
                options={optionsFrom(DOCUMENT_CATEGORY_LABELS)}
                onChange={(event) =>
                  setUploadForm({ ...uploadForm, category: event.target.value })
                }
              />
            </Field>
            <Field label="Data d'emissió">
              <Input
                type="date"
                value={uploadForm.issued_at}
                onChange={(event) =>
                  setUploadForm({ ...uploadForm, issued_at: event.target.value })
                }
              />
            </Field>
            <Field label="Caducitat">
              <Input
                type="date"
                value={uploadForm.expires_at}
                onChange={(event) =>
                  setUploadForm({ ...uploadForm, expires_at: event.target.value })
                }
              />
            </Field>
          </div>
          <Field label="Descripció">
            <Textarea
              rows={3}
              value={uploadForm.description}
              onChange={(event) =>
                setUploadForm({ ...uploadForm, description: event.target.value })
              }
            />
          </Field>
        </form>
      </Modal>

      {/* Vinculacio amb una entitat */}
      <Modal
        open={linking !== null}
        title="Vincula el document"
        description={`«${linking?.name ?? ""}» passarà a ser evidència de la dada que triïs.`}
        onClose={() => setLinking(null)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setLinking(null)} disabled={saving}>
              Cancel·la
            </Button>
            <Button
              variant="primary"
              form="link-form"
              type="submit"
              loading={saving}
              disabled={!linkTarget}
            >
              Vincula
            </Button>
          </>
        }
      >
        <form id="link-form" onSubmit={createLink} className="space-y-4">
          {saveError && <ErrorNotice message={saveError} />}
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Tipus de dada">
              <Select
                value={linkType}
                options={LINKABLE.map((item) => ({
                  value: item.type,
                  label: ENTITY_TYPE_LABELS[item.type],
                }))}
                onChange={(event) => {
                  setLinkType(event.target.value as LinkedEntityType);
                  setLinkTarget(null);
                }}
              />
            </Field>
            <Field label="Tipus de relació">
              <Select
                value={linkRelationship}
                options={optionsFrom(RELATIONSHIP_LABELS)}
                onChange={(event) => setLinkRelationship(event.target.value)}
              />
            </Field>
          </div>

          <Field label="Registre concret" required>
            {linkOptions.loading ? (
              <LoadingRows rows={1} />
            ) : (
              <Select
                value={linkTarget ?? ""}
                placeholder="Selecciona…"
                options={(linkOptions.data ?? []).map((row) => ({
                  value: String(row.id),
                  label:
                    LINKABLE.find((item) => item.type === linkType)?.label(row) ?? String(row.id),
                }))}
                onChange={(event) => setLinkTarget(event.target.value || null)}
              />
            )}
          </Field>
        </form>
      </Modal>

      <ConfirmDialog
        open={toDelete !== null}
        title="Elimina el document"
        message={`Segur que vols eliminar «${toDelete?.name}»? S'esborrarà el fitxer i tots els seus vincles d'evidència.`}
        loading={saving}
        onConfirm={() => void confirmDelete()}
        onCancel={() => setToDelete(null)}
      />
    </>
  );
}
