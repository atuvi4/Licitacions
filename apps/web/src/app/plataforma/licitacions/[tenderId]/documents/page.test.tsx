import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import type { PlatformClassification, PlatformTenderDocument } from "@/lib/platform-documents";

const get = vi.fn();
const post = vi.fn();
const upload = vi.fn();
const replace = vi.fn();
const push = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: (...a: unknown[]) => get(...a),
      post: (...a: unknown[]) => post(...a),
      upload: (...a: unknown[]) => upload(...a),
    },
  };
});
const router = { replace, push, refresh: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  usePathname: () => "/plataforma/licitacions/t1/documents",
  useSearchParams: () => new URLSearchParams(""),
  useParams: () => ({ tenderId: "t1" }),
  redirect: vi.fn(),
}));

import PlatformLayout from "../../../layout";
import TenderDocumentsPage from "./page";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const operator = { id: "op", email: "admin@tuvi.app", full_name: "Albert", is_active: true, last_login_at: null, must_change_password: false, is_platform_admin: true, memberships: [] };
const SHA_A = "a".repeat(64);
const SHA_B = "b".repeat(64);

function doc(overrides: Partial<PlatformTenderDocument> = {}): PlatformTenderDocument {
  return {
    id: "d1",
    name: "PCAP.pdf",
    document_type: "administrative_terms",
    source_document_type_code: null,
    url: "https://contractaciopublica.cat/pcap.pdf",
    mime_type: "application/pdf",
    published_at: null,
    removed_at: null,
    latest_file: {
      file_id: "f1",
      version_number: 1,
      download_status: "downloaded",
      origin: "fetched",
      file_size: 100,
      sha256: SHA_A,
      mime_type: "application/pdf",
      text_extraction_supported: true,
      analysis_status: "extracted",
      extraction_status: "extracted",
      quality: "good",
      page_count: 3,
      text_char_count: 1200,
      ocr_candidate: false,
      extraction_id: "e1",
      processed_at: "2026-10-01T10:00:00Z",
      error_code: null,
    },
    origin: "feed",
    contribution_status: "not_applicable",
    duplicate_of_document_id: null,
    analyzable: true,
    governing_source_id: "s1",
    contributed_by: null,
    contributed_at: null,
    contribution_source_url: null,
    verified_by: null,
    verified_at: null,
    verification_note: null,
    retired_by: null,
    retired_reason: null,
    created_at: "2026-10-01T09:00:00Z",
    ...overrides,
  };
}

const documents: PlatformTenderDocument[] = [
  doc(),
  doc({
    id: "d2",
    name: "Annex operador.pdf",
    document_type: "additional",
    url: "https://contractaciopublica.cat/annex.pdf",
    origin: "operator_url",
    contribution_status: "pending_verification",
    analyzable: false,
    governing_source_id: null,
    contributed_by: "op",
    contributed_at: "2026-10-03T10:00:00Z",
    contribution_source_url: "https://contractaciopublica.cat/perfil",
    latest_file: { ...doc().latest_file!, file_id: "f2", sha256: SHA_B, analysis_status: "pending", extraction_status: null },
  }),
  doc({
    id: "d3",
    name: "DOC-03.pdf",
    document_type: "other",
    url: "https://contractaciopublica.cat/doc03.pdf",
    origin: "operator_url",
    contribution_status: "verified",
    analyzable: true,
    governing_source_id: null,
    contributed_by: "op",
    contributed_at: "2026-10-03T10:00:00Z",
    contribution_source_url: "https://contractaciopublica.cat/perfil",
    verified_by: "op",
    verified_at: "2026-10-04T10:00:00Z",
    verification_note: "Coincideix amb el perfil del contractant.",
    latest_file: { ...doc().latest_file!, file_id: "f3", sha256: SHA_B },
  }),
  doc({
    id: "d4",
    name: "Antic.pdf",
    document_type: "other",
    url: "operator-upload://d4",
    origin: "operator_upload",
    contribution_status: "verified",
    analyzable: false,
    governing_source_id: null,
    removed_at: "2026-10-05T10:00:00Z",
    retired_by: "op",
    retired_reason: "Substituit per la versio definitiva.",
    latest_file: null,
  }),
];

const classification: PlatformClassification = {
  run: null,
  suggestions: [],
  findings: [],
  reviews: [],
  effective_roles: [
    { tender_document_id: "d1", role: "pcap", document_type: "administrative_terms", source: "global_classifier", content_sha256: SHA_A, fallback_source: null },
    { tender_document_id: "d3", role: null, document_type: "other", source: "feed", content_sha256: SHA_B, fallback_source: null },
  ],
  gaps: [],
};

const tender = { id: "t1", title: "Servei de manteniment de la xarxa", expediente_number: "2026/3964", buyer: { id: "b1", name: "Ajuntament de l'Escala", tax_identifier: null, city: null, nuts_code: null } };

function respond(overrides: Record<string, unknown> = {}) {
  const routes: Record<string, unknown> = {
    "/auth/me": operator,
    "/config": { environment: "pilot", max_upload_size_mb: 25, allowed_mime_types: ["application/pdf"], expiry_warning_days: 30 },
    "/tenders/t1": tender,
    "/platform/tenders/t1/documents": documents,
    "/platform/tenders/t1/classification": classification,
    ...overrides,
  };
  get.mockImplementation((path: string) => {
    const value = routes[path];
    if (value instanceof Error) return Promise.reject(value);
    return value === undefined ? Promise.reject(new ApiError(404, "not_found", "No trobat")) : Promise.resolve(value);
  });
}

async function show() {
  respond();
  render(
    <PlatformLayout>
      <TenderDocumentsPage />
    </PlatformLayout>,
  );
  await flush();
  await flush();
  return screen.getByRole("table", { name: "Documents públics de la licitació" });
}

function rowOf(table: HTMLElement, name: string): HTMLElement {
  return within(table).getByText(name).closest("tr")!;
}

beforeEach(() => {
  for (const mock of [get, post, upload, replace, push]) mock.mockReset();
});

describe("Administració Tuvi · documents públics d'una licitació", () => {
  it("llista tots els documents (retirats inclosos) amb els badges de Plecs reutilitzats: origen, aportació i retirada", async () => {
    const table = await show();
    expect(screen.getByRole("heading", { level: 1, name: "Servei de manteniment de la xarxa" })).toBeTruthy();
    expect(rowOf(table, "PCAP.pdf").textContent).toContain("feed");
    const annex = rowOf(table, "Annex operador.pdf");
    expect(annex.textContent).toContain("aportat per l'operador");
    expect(annex.textContent).toContain("pendent de verificació");
    const doc03 = rowOf(table, "DOC-03.pdf");
    expect(doc03.textContent).toContain("verificat");
    expect(doc03.textContent).toContain("Coincideix amb el perfil del contractant.");
    const antic = rowOf(table, "Antic.pdf");
    expect(antic.textContent).toContain("pujat per l'operador");
    expect(antic.textContent).toMatch(/Retirat per l'operador \(des del/);
    expect(antic.textContent).toContain("Substituit per la versio definitiva.");
    // El rol efectiu es diu en humà, mai el codi.
    expect(rowOf(table, "PCAP.pdf").textContent).toContain("plec de clàusules administratives (PCAP)");
    expect(document.body.textContent).not.toMatch(/global_classifier|operator_url|pending_verification/);
  });

  it("les accions depenen de l'estat: el feed no s'opera; pendent → verifica/rebutja/retira; verificada per URL → retira/cedeix; retirada → restaura", async () => {
    const table = await show();
    expect(within(rowOf(table, "PCAP.pdf")).queryByRole("button", { name: /Verifica|Rebutja|Retira|Cedeix/ })).toBeNull();
    const annex = rowOf(table, "Annex operador.pdf");
    expect(within(annex).getByRole("button", { name: "Verifica" })).toBeTruthy();
    expect(within(annex).getByRole("button", { name: "Rebutja" })).toBeTruthy();
    expect(within(annex).getByRole("button", { name: "Retira" })).toBeTruthy();
    const doc03 = rowOf(table, "DOC-03.pdf");
    expect(within(doc03).queryByRole("button", { name: "Verifica" })).toBeNull();
    expect(within(doc03).getByRole("button", { name: "Cedeix al feed" })).toBeTruthy();
    expect(within(rowOf(table, "Antic.pdf")).getByRole("button", { name: "Restaura" })).toBeTruthy();
  });

  it("afegir per URL: un host no oficial mostra l'error del servidor tal qual", async () => {
    await show();
    post.mockRejectedValue(new ApiError(422, "host_not_official", "El domini evil.example no és cap plataforma oficial de contractació."));
    fireEvent.change(screen.getByLabelText(/URL del document/), { target: { value: "https://evil.example/plec.pdf" } });
    fireEvent.click(screen.getByRole("button", { name: "Afegeix per URL" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/platform/tenders/t1/documents/by-url", expect.objectContaining({ url: "https://evil.example/plec.pdf" }));
    expect(await screen.findByText("El domini evil.example no és cap plataforma oficial de contractació.")).toBeTruthy();
  });

  it("verificar exigeix confirmació explícita: cap POST fins que es confirma", async () => {
    const table = await show();
    post.mockResolvedValue({ ...documents[1], contribution_status: "verified", analyzable: true });
    fireEvent.click(within(rowOf(table, "Annex operador.pdf")).getByRole("button", { name: "Verifica" }));
    await flush();
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("Annex operador.pdf");
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Verifica" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/platform/tenders/t1/documents/d2/verify", expect.anything());
  });

  it("rebutjar i retirar exigeixen motiu", async () => {
    const table = await show();
    post.mockResolvedValue(documents[1]);
    fireEvent.click(within(rowOf(table, "Annex operador.pdf")).getByRole("button", { name: "Rebutja" }));
    await flush();
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", { name: "Rebutja" }) as HTMLButtonElement;
    expect(confirm.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Motiu/), { target: { value: "No és el document publicat." } });
    expect(confirm.disabled).toBe(false);
    fireEvent.click(confirm);
    await flush();
    expect(post).toHaveBeenCalledWith("/platform/tenders/t1/documents/d2/reject", { reason: "No és el document publicat." });
  });

  it("revisar el rol exigeix motiu i ancora la versió vista (content_sha256)", async () => {
    const table = await show();
    post.mockResolvedValue(classification);
    fireEvent.click(within(rowOf(table, "PCAP.pdf")).getByRole("button", { name: "Revisa el rol" }));
    await flush();
    const dialog = screen.getByRole("dialog");
    const save = within(dialog).getByRole("button", { name: "Desa la revisió" }) as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Rol/), { target: { value: "characteristics_table" } });
    expect(save.disabled).toBe(true);
    fireEvent.change(within(dialog).getByLabelText(/Motiu/), { target: { value: "És el quadre de característiques, no el PCAP." } });
    expect(save.disabled).toBe(false);
    fireEvent.click(save);
    await flush();
    expect(post).toHaveBeenCalledWith("/platform/tenders/t1/documents/d1/role-review", {
      role: "characteristics_table",
      reason: "És el quadre de característiques, no el PCAP.",
      content_sha256: SHA_A,
    });
  });

  it("un document sense fitxer vigent no es pot revisar", async () => {
    const table = await show();
    expect(within(rowOf(table, "Antic.pdf")).queryByRole("button", { name: "Revisa el rol" })).toBeNull();
  });

  it("cedir al feed demana motiu, i un 409 del servidor es mostra en humà", async () => {
    const table = await show();
    post.mockRejectedValue(new ApiError(409, "feed_does_not_publish_url", "La font no publica aquesta URL a la darrera entrada: no es pot cedir."));
    fireEvent.click(within(rowOf(table, "DOC-03.pdf")).getByRole("button", { name: "Cedeix al feed" }));
    await flush();
    const dialog = screen.getByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText(/Motiu/), { target: { value: "El feed ja la publica." } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Cedeix al feed" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/platform/tenders/t1/documents/d3/hand-over-to-feed", { reason: "El feed ja la publica." });
    expect(await screen.findByText("La font no publica aquesta URL a la darrera entrada: no es pot cedir.")).toBeTruthy();
  });

  it("pujar un fitxer exigeix la pàgina oficial d'origen i envia multipart", async () => {
    await show();
    upload.mockResolvedValue(documents[1]);
    const file = new File([new Uint8Array([37, 80, 68, 70])], "DOC-04.pdf", { type: "application/pdf" });
    const input = screen.getByLabelText(/Fitxer/) as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    const submit = screen.getByRole("button", { name: "Puja el fitxer" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText(/Pàgina oficial on es publica \(pujada\)/), { target: { value: "https://contractaciopublica.cat/perfil" } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await flush();
    expect(upload).toHaveBeenCalledTimes(1);
    const [path, form] = upload.mock.calls[0] as [string, FormData];
    expect(path).toBe("/platform/tenders/t1/documents/upload");
    expect(form.get("contribution_source_url")).toBe("https://contractaciopublica.cat/perfil");
    expect((form.get("file") as File).name).toBe("DOC-04.pdf");
  });

  it("els forats de la classificació es compten per separat: documents que falten i documents presents sense processar", async () => {
    respond({
      "/platform/tenders/t1/classification": {
        ...classification,
        gaps: [
          { id: "g1", code: "referenced_document_missing", detail: { key: "DOC-07" }, would_provide: [] },
          { id: "g2", code: "format_not_analyzable", detail: { key: "d3" }, would_provide: [] },
          { id: "g3", code: "no_text", detail: { key: "d1" }, would_provide: ["requirements"] },
        ],
      },
    });
    render(
      <PlatformLayout>
        <TenderDocumentsPage />
      </PlatformLayout>,
    );
    await flush();
    await flush();
    expect(document.body.textContent).toContain("Documents que falten al paquet segons la classificació vigent: 1.");
    expect(document.body.textContent).toContain(
      "Documents presents que Tuvi encara no pot processar completament: 2. No fan el paquet incomplet.",
    );
  });

  it("una licitació no pública es diu amb l'error del servidor i sense cap formulari", async () => {
    respond({ "/platform/tenders/t1/documents": new ApiError(422, "tender_not_public", "Aquesta licitacio es d'una empresa: l'operador no hi entra.") });
    render(
      <PlatformLayout>
        <TenderDocumentsPage />
      </PlatformLayout>,
    );
    await flush();
    await flush();
    expect(screen.getByText(/Aquesta licitacio es d'una empresa/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Afegeix per URL" })).toBeNull();
  });
});
