import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SourceRefCitation, TenderDocumentMeta } from "@/lib/requirements";

/* ----- Estat dels mocks ---------------------------------------------------- */

let companyId = "c1";
const get = vi.fn();
const post = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: (...args: unknown[]) => get(...args),
      post: (...args: unknown[]) => post(...args),
    },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId,
    membership: { id: "m1", company_id: companyId, role: "technical", is_default: true },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));

import { CitationList, CitationPicker } from "./CitationPicker";

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

function document(overrides: Partial<TenderDocumentMeta> = {}): TenderDocumentMeta {
  return {
    id: "d-pdf",
    name: "PCAP.pdf",
    document_type: "administrative_terms",
    source_document_type_code: null,
    url: "upload://x",
    mime_type: "application/pdf",
    published_at: null,
    latest_file: {
      file_id: "f-pdf",
      version_number: 1,
      download_status: "downloaded",
      origin: "uploaded",
      file_size: 1000,
      sha256: "a".repeat(64),
      mime_type: "application/pdf",
      text_extraction_supported: true,
      analysis_status: "extracted",
      extraction_status: "extracted",
      quality: "good",
      page_count: 2,
      text_char_count: 900,
      ocr_candidate: false,
      extraction_id: "e-pdf",
      processed_at: null,
      error_code: null,
    },
    ...overrides,
  };
}

const spreadsheet = document({
  id: "d-xlsx",
  name: "Preus.xlsx",
  document_type: "other",
  mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  latest_file: {
    ...document().latest_file!,
    file_id: "f-xlsx",
    extraction_id: "e-xlsx",
    text_extraction_supported: false,
    analysis_status: "unsupported",
    extraction_status: "skipped_unsupported",
    quality: "not_applicable",
    page_count: 0,
  },
});

function citation(overrides: Partial<SourceRefCitation> = {}): SourceRefCitation {
  return {
    id: "ref-1",
    ref_kind: "extracted_page",
    document_id: "d-pdf",
    document_name: "PCAP.pdf",
    file_id: "f-pdf",
    version_number: 1,
    content_sha256: "a".repeat(64),
    extraction_id: "e-pdf",
    page_id: "p1",
    page_number: 1,
    location_label: null,
    quote: "Cal una assegurança",
    char_start: null,
    char_end: null,
    created_at: "2026-09-22T10:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  companyId = "c1";
  get.mockReset();
  post.mockReset();
  get.mockImplementation(async (path: string) => {
    if (path.includes("/documents")) return [document(), spreadsheet];
    if (path.includes("/pages")) {
      return { items: [{ id: "p1", page_number: 1 }, { id: "p2", page_number: 2 }], total: 2 };
    }
    throw new Error(`GET inesperat: ${path}`);
  });
  post.mockImplementation(async () => citation());
});

async function picker() {
  const onAdded = vi.fn();
  render(<CitationPicker tenderId="t1" onAdded={onAdded} />);
  await flush();
  return { onAdded };
}

/* ----- Cap UUID a la vista -------------------------------------------------- */

describe("triar evidència", () => {
  it("no demana cap identificador: només noms", async () => {
    const { container } = render(<CitationPicker tenderId="t1" onAdded={vi.fn()} />);
    await flush();

    const texts = [...container.querySelectorAll("option, label, input")].map(
      (node) => `${node.textContent ?? ""}${(node as HTMLInputElement).placeholder ?? ""}`,
    );
    const uuid = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}/i;
    expect(texts.some((text) => uuid.test(text))).toBe(false);
    expect(screen.getByLabelText("Document")).toBeTruthy();
  });

  it("d'un document llegit se'n pot citar la pàgina, pel seu número", async () => {
    await picker();
    fireEvent.change(screen.getByLabelText("Document"), { target: { value: "d-pdf" } });
    await flush();

    const pages = screen.getByLabelText("Pàgina") as HTMLSelectElement;
    expect([...pages.options].map((option) => option.textContent)).toContain("Pàgina 2");
  });

  it("crea la cita amb el que s'ha triat, sense enviar cap SHA", async () => {
    const { onAdded } = await picker();
    fireEvent.change(screen.getByLabelText("Document"), { target: { value: "d-pdf" } });
    await flush();
    fireEvent.change(screen.getByLabelText("Pàgina"), { target: { value: "p2" } });
    fireEvent.click(screen.getByRole("button", { name: /Afegeix la cita/ }));
    await flush();

    const [path, body] = post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/tenders/t1/source-refs/extracted-page");
    expect(body).toEqual({
      extraction_id: "e-pdf",
      page_id: "p2",
      location_label: null,
    });
    expect(body).not.toHaveProperty("content_sha256");
    expect(onAdded).toHaveBeenCalled();
  });

  it("d'un full de càlcul se'n diu on mirar, i no és cap error", async () => {
    await picker();
    fireEvent.change(screen.getByLabelText("Document"), { target: { value: "d-xlsx" } });
    await flush();

    expect(screen.getByText(/Text no extraïble/)).toBeTruthy();
    expect(screen.queryByLabelText("Pàgina")).toBeNull();

    fireEvent.change(screen.getByLabelText(/On, exactament/), {
      target: { value: "Full Càlculs, fila 42" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Afegeix la cita/ }));
    await flush();

    const [path, body] = post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/tenders/t1/source-refs/document-file");
    expect(body).toEqual({
      tender_document_file_id: "f-xlsx",
      location_label: "Full Càlculs, fila 42",
    });
  });

  it("no deixa citar fins que s'ha triat una pàgina", async () => {
    await picker();
    fireEvent.change(screen.getByLabelText("Document"), { target: { value: "d-pdf" } });
    await flush();

    const button = screen.getByRole("button", { name: /Afegeix la cita/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("si la creació falla, es diu i no s'afegeix res", async () => {
    post.mockRejectedValueOnce(new Error("Aquesta pàgina no és d'aquesta extracció."));
    const { onAdded } = await picker();
    fireEvent.change(screen.getByLabelText("Document"), { target: { value: "d-pdf" } });
    await flush();
    fireEvent.change(screen.getByLabelText("Pàgina"), { target: { value: "p1" } });
    fireEvent.click(screen.getByRole("button", { name: /Afegeix la cita/ }));
    await flush();

    expect(screen.getByText(/no és d'aquesta extracció/)).toBeTruthy();
    expect(onAdded).not.toHaveBeenCalled();
  });

  it("canviar d'empresa no llegeix els documents de l'anterior", async () => {
    companyId = "c2";
    await picker();

    expect(get.mock.calls.every(([path]) => !(path as string).includes("/companies/c1/"))).toBe(
      true,
    );
    expect(get).toHaveBeenCalledWith("/companies/c2/tenders/t1/documents");
  });
});

/* ----- Les cites que ja hi són ---------------------------------------------- */

describe("cites existents", () => {
  it("diuen document, versió, pàgina i text", async () => {
    render(<CitationList citations={[citation({ location_label: "Clàusula 7.2" })]} />);

    const row = screen.getByRole("listitem");
    expect(row.textContent).toContain("PCAP.pdf");
    expect(row.textContent).toContain("v1");
    expect(row.textContent).toContain("p. 1");
    expect(row.textContent).toContain("Clàusula 7.2");
    expect(row.textContent).toContain("Cal una assegurança");
  });

  it("no ensenyen el SHA: és provinença, no interfície", async () => {
    render(<CitationList citations={[citation()]} />);
    expect(screen.getByRole("listitem").textContent).not.toContain("a".repeat(16));
  });

  it("una cita d'un full de càlcul no s'inventa cap pàgina", async () => {
    render(
      <CitationList
        citations={[
          citation({
            ref_kind: "document_file",
            document_name: "Preus.xlsx",
            page_number: null,
            quote: null,
            location_label: "Full Càlculs, fila 42",
          }),
        ]}
      />,
    );
    const row = screen.getByRole("listitem");
    expect(row.textContent).toContain("Full Càlculs, fila 42");
    expect(row.textContent).not.toContain("p. ");
  });

  it("treure'n una desenganxa l'enllaç i prou", async () => {
    const onRemove = vi.fn();
    render(<CitationList citations={[citation()]} onRemove={onRemove} />);

    fireEvent.click(screen.getByRole("button", { name: "Treu" }));
    expect(onRemove).toHaveBeenCalledWith(expect.objectContaining({ id: "ref-1" }));
    // No hi ha cap acció d'esborrar ni d'editar la cita: és immutable.
    expect(screen.queryByRole("button", { name: /Esborra|Edita/ })).toBeNull();
  });

  it("sense cap evidència ho diu sense insinuar que sigui incorrecte", async () => {
    render(<CitationList citations={[]} />);
    const text = screen.getByText(/Cap evidència adjunta/).textContent ?? "";
    expect(text).toMatch(/no vol dir que sigui incorrecte/i);
  });
});
