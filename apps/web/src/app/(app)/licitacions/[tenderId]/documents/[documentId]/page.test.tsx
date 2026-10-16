import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const get = vi.fn();
const replace = vi.fn();
let search = "";
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args) } };
});
let companyId = "c1";
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    get companyId() {
      return companyId;
    },
    membership: { id: "m1", company_id: companyId, role: "owner", is_default: true },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ tenderId: "t1", documentId: "d3" }),
  useRouter: () => ({ push: vi.fn(), replace, refresh: vi.fn() }),
  usePathname: () => "/licitacions/t1/documents/d3",
  useSearchParams: () => new URLSearchParams(search),
}));

import DocumentTextPage from "./page";

const files = [
  {
    id: "f2",
    version_number: 2,
    is_current: true,
    download_status: "downloaded",
    source_url: "https://example.org/d3",
    original_filename: "GetDocumentByIdServlet",
    downloaded_at: "2026-09-15T10:00:00Z",
    error_code: null,
    content: { sha256: "abcdef0123456789", file_size: 120000, mime_type: "application/pdf" },
    extraction: { id: "e2", parser_version: "pdf-v1", revision_number: 2, status: "extracted", quality: "low_text", page_count: 3, text_char_count: 900, ocr_candidate: true, error_code: null, qa_status: "warning", qa_issues: [{ page_number: 2, code: "EMPTY_PAGE" }] },
  },
  {
    id: "f1",
    version_number: 1,
    is_current: false,
    download_status: "downloaded",
    source_url: "https://example.org/d3",
    original_filename: "GetDocumentByIdServlet",
    downloaded_at: "2026-09-01T10:00:00Z",
    error_code: null,
    content: { sha256: "0123456789abcdef", file_size: 110000, mime_type: "application/pdf" },
    extraction: { id: "e1", parser_version: "pdf-v1", revision_number: 1, status: "extracted", quality: "good", page_count: 3, text_char_count: 4000, ocr_candidate: false, error_code: null, qa_status: "good", qa_issues: [] },
  },
];
const tender = { id: "t1", title: "Desplegament de xarxa", expediente_number: "EXP-1", buyer: { name: "Ajuntament Demo" }, documents: [{ id: "d3", name: "Plec de prescripcions tècniques" }] };

function respondWith(map: Record<string, unknown>) {
  get.mockImplementation((path: string) => {
    const key = path in map ? path : Object.keys(map).find((prefix) => path.startsWith(prefix));
    const value = key ? map[key] : undefined;
    if (value instanceof Error) return Promise.reject(value);
    return Promise.resolve(value);
  });
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });

function base() {
  return {
    // Els plecs es llegeixen dins d'una empresa: les rutes hi pengen.
    "/companies/c1/tenders/t1/documents/d3/files": files,
    "/tenders/t1": tender,
    "/companies/c1/tender-document-extractions/e2/pages/1": { page_number: 1, text: "Text de la pàgina 1", char_count: 19, status: "extracted" },
    "/companies/c1/tender-document-extractions/e2/pages/2": { page_number: 2, text: "", char_count: 0, status: "extracted" },
    "/companies/c1/tender-document-extractions/e1/pages/1": { page_number: 1, text: "Text antic de la revisió 1", char_count: 26, status: "extracted" },
  };
}

describe("Text extret d'un plec", () => {
  beforeEach(() => {
    get.mockReset();
    replace.mockReset();
    search = "";
    companyId = "c1";
  });

  it("context de la licitació, nom del document, estat i versions reals; avisos de lectura com a bàner", async () => {
    respondWith(base());
    const { container } = render(<DocumentTextPage />);
    expect(await screen.findByRole("heading", { level: 1, name: "Plec de prescripcions tècniques" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "Desplegament de xarxa" }).getAttribute("href")).toBe("/licitacions/t1?tab=plecs");
    const pills = [...container.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`);
    expect(pills).toContain("Poc text · possible escaneig[attention]");
    expect(pills).toContain("Avisos[attention]");
    // Versions en paraules (D6): «Versió 2 de 2», mai una «v2» críptica.
    expect(container.textContent).toContain("Versió 2 de 2");
    expect(container.textContent).not.toMatch(/(^|\s)v[12]\b/);
    expect(container.textContent).not.toContain("GetDocumentByIdServlet");
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("Pàgina 2: pàgina sense text");
    expect(banner.textContent).toContain("escanejat");
    expect(await screen.findByText("Text de la pàgina 1")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Obre l'original/ }).getAttribute("href")).toBe("/bff/companies/c1/tender-document-files/f2/original");
    // Versions: la vigent i l'anterior, amb data.
    const versions = screen.getByText("Versions").closest("section")!;
    expect(versions.textContent).toContain("Versió 2");
    expect(versions.textContent).toContain("Versió 1");
    expect(versions.textContent).toContain("vigent");
    expect(versions.textContent).toContain("Cada descàrrega del document és una versió");
  });

  it("la navegació de pàgines demana la pàgina següent i mostra el buit d'una pàgina sense text", async () => {
    respondWith(base());
    render(<DocumentTextPage />);
    await screen.findByText("Text de la pàgina 1");
    fireEvent.click(screen.getByRole("button", { name: "Següent" }));
    await flush();
    expect(get).toHaveBeenCalledWith("/companies/c1/tender-document-extractions/e2/pages/2");
    expect(await screen.findByText(/pàgina sense text extret/)).toBeTruthy();
    expect(screen.getByText("Pàgina 2 de 3")).toBeTruthy();
  });

  it("?extraction= obre la revisió citada i diu que no és la vigent", async () => {
    search = "extraction=e1&page=1";
    respondWith(base());
    const { container } = render(<DocumentTextPage />);
    expect(await screen.findByText("Text antic de la revisió 1")).toBeTruthy();
    expect(container.textContent).toContain("revisió 1");
    expect(container.textContent).toContain("Ja no és la revisió vigent");
  });

  // P2-07: la pàgina viu a l'URL.
  it("navegar escriu ?page= a l'URL (replace, sense scroll) i conserva la revisió citada", async () => {
    search = "extraction=e2";
    respondWith(base());
    render(<DocumentTextPage />);
    await screen.findByText("Text de la pàgina 1");
    fireEvent.click(screen.getByRole("button", { name: "Següent" }));
    await flush();
    expect(replace).toHaveBeenCalledWith("/licitacions/t1/documents/d3?extraction=e2&page=2", { scroll: false });
  });

  it("un enllaç amb ?page=2 obre directament la pàgina 2", async () => {
    search = "page=2";
    respondWith(base());
    render(<DocumentTextPage />);
    await flush();
    expect(get).toHaveBeenCalledWith("/companies/c1/tender-document-extractions/e2/pages/2");
    expect(await screen.findByText("Pàgina 2 de 3")).toBeTruthy();
  });

  it("fora de rang: es mostra l'última pàgina, es diu, i mai es demana la que no existeix", async () => {
    search = "page=9";
    respondWith({ ...base(), "/companies/c1/tender-document-extractions/e2/pages/3": { page_number: 3, text: "Text de la pàgina 3", char_count: 19, status: "extracted" } });
    render(<DocumentTextPage />);
    expect(await screen.findByText("Text de la pàgina 3")).toBeTruthy();
    expect(screen.getByText(/La pàgina 9 no existeix: aquest document en té 3/)).toBeTruthy();
    expect(get).not.toHaveBeenCalledWith("/companies/c1/tender-document-extractions/e2/pages/9");
  });

  it("un ?page= no vàlid obre la pàgina 1 i ho diu", async () => {
    search = "page=abc";
    respondWith(base());
    render(<DocumentTextPage />);
    expect(await screen.findByText("Text de la pàgina 1")).toBeTruthy();
    expect(screen.getByText(/no és vàlida \(«abc»\)/)).toBeTruthy();
  });

  it("el número escrit s'aplica en confirmar, no a cada tecla", async () => {
    respondWith(base());
    render(<DocumentTextPage />);
    await screen.findByText("Text de la pàgina 1");
    const input = screen.getByRole("spinbutton", { name: "Número de pàgina" });
    fireEvent.change(input, { target: { value: "2" } });
    expect(replace).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: "Enter" });
    await flush();
    expect(replace).toHaveBeenCalledWith("/licitacions/t1/documents/d3?page=2", { scroll: false });
    // Un número fora de rang no navega i torna al valor vigent.
    fireEvent.change(input, { target: { value: "7" } });
    fireEvent.blur(input);
    expect(replace).toHaveBeenCalledTimes(1);
  });

  it("«Copia l'enllaç» copia l'URL de la pàgina que es veu", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    search = "page=2";
    respondWith(base());
    render(<DocumentTextPage />);
    await screen.findByText("Pàgina 2 de 3");
    fireEvent.click(screen.getByRole("button", { name: "Copia l'enllaç a aquesta pàgina" }));
    await flush();
    expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/licitacions/t1/documents/d3?page=2`);
    expect(screen.getByText("Enllaç copiat.")).toBeTruthy();
  });

  it("un PDF no té vista de taula", async () => {
    respondWith(base());
    render(<DocumentTextPage />);
    await screen.findByText("Text de la pàgina 1");
    expect(screen.queryByRole("button", { name: "Taula" })).toBeNull();
  });

  it("un full de càlcul: taula amb el valor net i el valor sense format, i el text original a un clic", async () => {
    const sheet = [{ ...files[0], content: { ...files[0].content, mime_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" }, extraction: { ...files[0].extraction, parser_version: "xlsx-v1", page_count: 2, ocr_candidate: false, qa_issues: [] } }];
    respondWith({
      ...base(),
      "/companies/c1/tenders/t1/documents/d3/files": sheet,
      "/companies/c1/tender-document-extractions/e2/pages/1": {
        page_number: 1,
        text: "# Full: Inputs\nFila 9 | A9: PL2 | B9: Bloc 2 | C9: =48*798.46 → 38326.080000000002 | D9: 64200000",
        char_count: 90,
        status: "extracted",
      },
    });
    const { container } = render(<DocumentTextPage />);
    expect(await screen.findByText("38.326,08")).toBeTruthy();
    expect(screen.getByText("Full 1 de 2")).toBeTruthy();
    expect(container.querySelector("[data-raw-value]")?.textContent).toBe("sense format: 38326.080000000002");
    // Els codis i els enters no es toquen.
    expect(screen.getByText("64200000")).toBeTruthy();
    expect(screen.queryByText("64.200.000")).toBeNull();
    expect(screen.getByRole("button", { name: "Copia l'enllaç a aquest full" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Text original" }));
    expect(container.querySelector("pre")?.textContent).toContain("C9: =48*798.46 → 38326.080000000002");
  });

  it("sense cap fitxer → buit amb tornada a la pestanya Plecs", async () => {
    respondWith({ ...base(), "/companies/c1/tenders/t1/documents/d3/files": [] });
    render(<DocumentTextPage />);
    expect(await screen.findByRole("heading", { name: "Aquest document encara no s'ha processat" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Torna als plecs/ }).getAttribute("href")).toBe("/licitacions/t1?tab=plecs");
  });
});

describe("canvi d'empresa", () => {
  it("cap petició continua llegint el document amb l'empresa anterior", async () => {
    get.mockReset();
    companyId = "c2";
    respondWith(base());
    render(<DocumentTextPage />);
    await flush();

    const asked = get.mock.calls.map(([path]) => path as string);
    const scoped = asked.filter((path) => path.startsWith("/companies/"));
    expect(scoped.length).toBeGreaterThan(0);
    expect(scoped.every((path) => path.startsWith("/companies/c2/"))).toBe(true);
    expect(asked.some((path) => path.includes("/companies/c1/"))).toBe(false);
  });
});
