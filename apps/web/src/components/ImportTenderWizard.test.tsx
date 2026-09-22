import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/* ----- Estat dels mocks ---------------------------------------------------- */

let companyId = "c1";
const post = vi.fn();
const patch = vi.fn();
const upload = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: async (path: string) => {
        if (path.includes("/buyers")) {
          return [{ id: "b-existent", name: "Ajuntament de prova" }];
        }
        throw new Error(`GET inesperat: ${path}`);
      },
      post: (...args: unknown[]) => post(...args),
      patch: (...args: unknown[]) => patch(...args),
      upload: (...args: unknown[]) => upload(...args),
    },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId,
    membership: { id: "m1", company_id: companyId, role: "owner", is_default: true },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));

import { ImportTenderWizard } from "./ImportTenderWizard";

/* ----- Ajudes -------------------------------------------------------------- */

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

function tenderPayload(id = "t-1") {
  return { id, title: "Serveis de comunicacions", documents: [] };
}

function filePayload(overrides: Record<string, unknown> = {}) {
  return {
    file_id: "f-1",
    version_number: 1,
    download_status: "downloaded",
    origin: "uploaded",
    file_size: 4096,
    sha256: "a".repeat(64),
    mime_type: "application/pdf",
    text_extraction_supported: true,
    analysis_status: "extracted",
    extraction_status: "extracted",
    quality: "good",
    page_count: 3,
    text_char_count: 900,
    ocr_candidate: false,
    extraction_id: "e-1",
    processed_at: null,
    error_code: null,
    ...overrides,
  };
}

async function open() {
  const onClose = vi.fn();
  render(<ImportTenderWizard open onClose={onClose} />);
  await flush();
  return { onClose };
}

async function fillTitleAndContinue(title = "Serveis de comunicacions") {
  fireEvent.change(screen.getByLabelText(/^Títol/), { target: { value: title } });
  fireEvent.click(screen.getByRole("button", { name: /Crea i continua|Desa i continua/ }));
  await flush();
}

function pickFile(name: string, type: string) {
  const input = screen.getByLabelText("Fitxers");
  const file = new File(["contingut"], name, { type });
  fireEvent.change(input, { target: { files: [file] } });
  return flush();
}

beforeEach(() => {
  companyId = "c1";
  post.mockReset();
  patch.mockReset();
  upload.mockReset();
  post.mockResolvedValue({ created: true, tender: tenderPayload() });
  patch.mockResolvedValue({ revised: true, revision: 2, tender: tenderPayload() });
  upload.mockResolvedValue({
    id: "d-1",
    name: "PCAP.pdf",
    stored: true,
    content_reused: false,
    file: filePayload(),
  });
});

/* ----- Pas 1 --------------------------------------------------------------- */

describe("pas 1", () => {
  it("no crea res sense títol", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: /Crea i continua/ }));
    await flush();

    expect(post).not.toHaveBeenCalled();
    expect(screen.getAllByText(/títol és obligatori/i).length).toBeGreaterThan(0);
  });

  it("crea la licitació amb el token del formulari", async () => {
    await open();
    await fillTitleAndContinue();

    expect(post).toHaveBeenCalledTimes(1);
    const [path, body] = post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/tenders");
    expect(body.title).toBe("Serveis de comunicacions");
    expect(typeof body.client_token).toBe("string");
  });

  it("l'organisme és opcional", async () => {
    await open();
    await fillTitleAndContinue();

    const [, body] = post.mock.calls[0] as [string, Record<string, unknown>];
    expect(body).not.toHaveProperty("buyer_id");
    expect(body).not.toHaveProperty("buyer_manual");
  });

  it("el selector d'organismes és el de l'empresa", async () => {
    await open();
    fireEvent.change(screen.getByLabelText("Organisme"), { target: { value: "existing" } });
    await flush();

    const select = screen.getByLabelText("Organisme del catàleg") as HTMLSelectElement;
    expect([...select.options].map((option) => option.textContent)).toContain(
      "Ajuntament de prova",
    );
  });

  it("un reintent després d'un error NO crea una segona licitació", async () => {
    await open();
    post.mockRejectedValueOnce(new Error("la xarxa ha fallat"));
    await fillTitleAndContinue();
    expect(screen.getByText(/la xarxa ha fallat/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Crea i continua/ }));
    await flush();

    const tokens = post.mock.calls.map(([, body]) => (body as { client_token: string }).client_token);
    expect(tokens).toHaveLength(2);
    expect(tokens[0]).toBe(tokens[1]);
  });

  it("tornar enrere del pas 2 corregeix la MATEIXA licitació", async () => {
    await open();
    await fillTitleAndContinue();
    fireEvent.click(screen.getByRole("button", { name: "Enrere" }));
    await flush();

    fireEvent.change(screen.getByLabelText(/^Títol/), { target: { value: "Títol corregit" } });
    fireEvent.click(screen.getByRole("button", { name: /Desa i continua/ }));
    await flush();

    expect(post).toHaveBeenCalledTimes(1);
    expect(patch).toHaveBeenCalledTimes(1);
    const [path, body] = patch.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/companies/c1/tenders/t-1");
    expect(body.title).toBe("Títol corregit");
    expect(body.expected_revision).toBe(1);
  });
});

/* ----- Pas 2 --------------------------------------------------------------- */

describe("pas 2", () => {
  it("puja el fitxer a la licitació creada", async () => {
    await open();
    await fillTitleAndContinue();
    await pickFile("PCAP.pdf", "application/pdf");

    expect(upload).toHaveBeenCalledTimes(1);
    const [path, body] = upload.mock.calls[0] as [string, FormData];
    expect(path).toBe("/companies/c1/tenders/t-1/documents");
    expect(body.get("file")).toBeInstanceOf(File);
  });

  it("un PDF llegit ho diu", async () => {
    await open();
    await fillTitleAndContinue();
    await pickFile("PCAP.pdf", "application/pdf");

    expect(screen.getByText("Guardat")).toBeTruthy();
    expect(screen.getByText("Text llegit")).toBeTruthy();
  });

  it("un DOCX es guarda i NO es presenta com un error", async () => {
    upload.mockResolvedValueOnce({
      id: "d-2",
      name: "plec.docx",
      stored: true,
      content_reused: false,
      file: filePayload({
        analysis_status: "unsupported",
        text_extraction_supported: false,
        extraction_status: "skipped_unsupported",
        quality: "not_applicable",
        page_count: 0,
      }),
    });
    await open();
    await fillTitleAndContinue();
    await pickFile("plec.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document");

    expect(screen.getByText("Guardat")).toBeTruthy();
    expect(screen.getByText(/no suportada/i)).toBeTruthy();
    expect(screen.queryByText(/No s'ha pujat/)).toBeNull();
  });

  it("un fitxer que falla no s'endú els que han anat bé", async () => {
    await open();
    await fillTitleAndContinue();
    await pickFile("PCAP.pdf", "application/pdf");

    upload.mockRejectedValueOnce(new Error("Tipus de fitxer no permès."));
    await pickFile("pagina.html", "text/html");

    expect(screen.getByText("Guardat")).toBeTruthy();
    expect(screen.getByText("No s'ha pujat")).toBeTruthy();
    expect(screen.getByText(/no permès/i)).toBeTruthy();
  });

  it("acabar no crea cap candidatura ni decideix res", async () => {
    const { onClose } = await open();
    await fillTitleAndContinue();
    await pickFile("PCAP.pdf", "application/pdf");

    fireEvent.click(screen.getByRole("button", { name: /Veure a Oportunitats/ }));
    await flush();

    expect(onClose).toHaveBeenCalled();
    const touched = [...post.mock.calls, ...patch.mock.calls].map(([path]) => path as string);
    expect(touched.some((path) => path.includes("workspace"))).toBe(false);
    expect(touched.some((path) => path.includes("decision"))).toBe(false);
  });
});

/* ----- Canvi d'empresa ----------------------------------------------------- */

describe("canvi d'empresa", () => {
  it("una obertura nova no arrossega la licitació de l'anterior", async () => {
    const { rerender } = render(<ImportTenderWizard open onClose={vi.fn()} />);
    await flush();
    await fillTitleAndContinue();
    expect(screen.getByRole("button", { name: /Veure a Oportunitats/ })).toBeTruthy();

    // Es tanca, es canvia d'empresa i es torna a obrir.
    rerender(<ImportTenderWizard open={false} onClose={vi.fn()} />);
    await flush();
    companyId = "c2";
    rerender(<ImportTenderWizard open onClose={vi.fn()} />);
    await flush();

    expect(screen.getByRole("button", { name: /Crea i continua/ })).toBeTruthy();
    expect((screen.getByLabelText(/^Títol/) as HTMLInputElement).value).toBe("");

    await fillTitleAndContinue("Una altra");
    expect(post.mock.calls.at(-1)?.[0]).toBe("/companies/c2/tenders");
  });
});
