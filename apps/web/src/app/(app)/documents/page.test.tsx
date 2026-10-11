import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { DocumentItem } from "@/lib/types";

const get = vi.fn();
const remove = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, get: (...args: unknown[]) => get(...args), delete: (...args: unknown[]) => remove(...args) },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({ companyId: "c1", config: { max_upload_size_mb: 20, allowed_mime_types: ["application/pdf"] } }),
  useCompanyPath: () => (suffix: string) => `/companies/c1${suffix}`,
}));

import DocumentsPage from "./page";

function document(overrides: Partial<DocumentItem> = {}): DocumentItem {
  return {
    id: "doc-1",
    company_id: "c1",
    name: "Certificat ISO 9001.pdf",
    original_filename: "iso9001.pdf",
    mime_type: "application/pdf",
    file_size: 120000,
    sha256: "abcdef0123456789abcdef0123456789",
    category: "certification",
    description: null,
    issued_at: null,
    expires_at: null,
    temporal_status: "unknown",
    uploaded_at: "2026-09-01T10:00:00Z",
    links: [],
    created_at: "2026-09-01T10:00:00Z",
    updated_at: "2026-09-01T10:00:00Z",
    created_by: null,
    updated_by: null,
    ...overrides,
  } as DocumentItem;
}

const linked = document({
  links: [
    { id: "l1", document_id: "doc-1", entity_type: "company_certification", entity_id: "cert-1", relationship_type: "certificate", notes: null, created_at: "2026-09-02T10:00:00Z" },
    { id: "l2", document_id: "doc-1", entity_type: "company_project", entity_id: "prj-1", relationship_type: "certificate_of_good_execution", notes: null, created_at: "2026-09-03T10:00:00Z" },
  ],
});

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

describe("Biblioteca de documents (D6/D7)", () => {
  beforeEach(() => {
    get.mockReset();
    remove.mockReset();
  });

  it("eliminar un document vinculat avisa amb el nombre de vincles abans de res (D7)", async () => {
    get.mockResolvedValue([linked]);
    render(<DocumentsPage />);
    await screen.findByText("Certificat ISO 9001.pdf");
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Elimina" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("és evidència de 2 dades");
    expect(dialog.textContent).toContain("2 vincles");
    expect(dialog.textContent).toMatch(/perdran aquesta evidència/);
    expect(dialog.textContent).not.toMatch(/company_certification|certificate_of_good_execution/);
    expect(remove).not.toHaveBeenCalled();
  });

  it("un document sense vincles s'elimina amb l'avís de sempre, sense comptar res", async () => {
    get.mockResolvedValue([document()]);
    render(<DocumentsPage />);
    await screen.findByText("Certificat ISO 9001.pdf");
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Elimina" }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).not.toMatch(/és evidència de/);
    expect(dialog.textContent).toContain("S'esborrarà el fitxer");
  });

  it("«Vincula» explica què fa (D6)", async () => {
    get.mockResolvedValue([document()]);
    render(<DocumentsPage />);
    await screen.findByText("Certificat ISO 9001.pdf");
    await flush();
    const link = screen.getByRole("button", { name: "Vincula" });
    expect(link.getAttribute("title")).toMatch(/evidència/);
  });
});
