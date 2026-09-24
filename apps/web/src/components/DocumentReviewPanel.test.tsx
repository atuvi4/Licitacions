import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readinessFixture } from "@/test/readiness-fixture";
import type { DocumentReviewState } from "@/lib/readiness";

/* ----- Estat dels mocks ---------------------------------------------------- */

let role = "owner";
let companyId = "c1";
let documents: DocumentReviewState = {
  reviewed: false,
  stale: false,
  reviewed_at: null,
  documents: 2,
};

const get = vi.fn();
const post = vi.fn();
const uploadRaw = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      get: (...args: unknown[]) => get(...args),
      post: (...args: unknown[]) => post(...args),
      uploadRaw: (...args: unknown[]) => uploadRaw(...args),
    },
  };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId,
    membership: { id: "m1", company_id: companyId, role, is_default: true },
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  }),
}));

import { DocumentReviewPanel, NewVersionButton } from "./DocumentReviewPanel";

const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

beforeEach(() => {
  role = "owner";
  companyId = "c1";
  documents = { reviewed: false, stale: false, reviewed_at: null, documents: 2 };
  get.mockReset();
  post.mockReset();
  uploadRaw.mockReset();
  get.mockImplementation(async () => readinessFixture({ documents }));
  post.mockImplementation(async () => readinessFixture({ documents }));
  uploadRaw.mockResolvedValue({ status: 201, body: {} });
});

async function panel(workspaceId: string | null = "w1", count = 2) {
  const onReviewed = vi.fn();
  render(
    <DocumentReviewPanel workspaceId={workspaceId} documents={count} onReviewed={onReviewed} />,
  );
  await flush();
  return { onReviewed };
}

/* ----- Estat de la revisió ------------------------------------------------- */

describe("estat de la revisió", () => {
  it("diu que els documents estan pendents quan ningú no els ha mirat", async () => {
    await panel();
    expect(screen.getByText(/pendents de revisió/i)).toBeTruthy();
  });

  it("no diu mai que hagin canviat els requisits", async () => {
    documents = { reviewed: true, stale: true, reviewed_at: "2026-09-20T10:00:00Z", documents: 3 };
    await panel();

    expect(screen.getByText(/canvis des de l'última revisió/i)).toBeTruthy();
    expect(screen.queryByText(/requisits han canviat/i)).toBeNull();
  });

  it("quan estan revisats ho diu amb la data i sense demanar res", async () => {
    documents = { reviewed: true, stale: false, reviewed_at: "2026-09-20T10:00:00Z", documents: 3 };
    await panel();

    expect(screen.getByText(/Documents revisats/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Marca com revisats/ })).toBeNull();
  });

  it("sense documents no demana revisar res", async () => {
    await panel("w1", 0);
    expect(screen.queryByText(/pendents de revisió/i)).toBeNull();
  });

  it("sense candidatura no hi ha revisió: és una afirmació sobre una candidatura", async () => {
    await panel(null);
    expect(get).not.toHaveBeenCalled();
    expect(screen.queryByText(/pendents de revisió/i)).toBeNull();
  });
});

/* ----- Marcar com revisats ------------------------------------------------- */

describe("marcar com revisats", () => {
  it("crida l'endpoint de revisió de l'empresa activa", async () => {
    await panel();
    fireEvent.click(screen.getByRole("button", { name: /Marca com revisats/ }));
    await flush();

    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/documents/review", {});
  });

  it("no analitza res: només marca", async () => {
    const { onReviewed } = await panel();
    fireEvent.click(screen.getByRole("button", { name: /Marca com revisats/ }));
    await flush();

    const called = [...post.mock.calls, ...get.mock.calls].map(([path]) => path as string);
    expect(called.some((path) => path.includes("analysis") || path.includes("requirements"))).toBe(
      false,
    );
    expect(onReviewed).toHaveBeenCalled();
  });

  it("qui no decideix no pot marcar-ho", async () => {
    role = "viewer";
    await panel();

    expect(screen.getByText(/pendents de revisió/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Marca com revisats/ })).toBeNull();
  });

  it("canviar d'empresa no llegeix la revisió de l'anterior", async () => {
    companyId = "c2";
    await panel();

    expect(get).toHaveBeenCalledWith("/companies/c2/workspaces/w1/final-readiness");
    expect(get.mock.calls.some(([path]) => (path as string).includes("/companies/c1/"))).toBe(false);
  });
});

/* ----- Nova versió --------------------------------------------------------- */

describe("nova versió d'un plec", () => {
  async function versionButton() {
    const onUploaded = vi.fn();
    render(<NewVersionButton tenderId="t1" documentId="d1" onUploaded={onUploaded} />);
    await flush();
    return { onUploaded };
  }

  function pick(name = "PCAP.pdf") {
    const input = screen.getByLabelText(/Nova versió del document/);
    fireEvent.change(input, {
      target: { files: [new File(["bytes"], name, { type: "application/pdf" })] },
    });
    return flush();
  }

  it("puja contra la licitació i el document de la ruta", async () => {
    await versionButton();
    await pick();

    const [path] = uploadRaw.mock.calls[0] as [string, FormData];
    expect(path).toBe("/companies/c1/tenders/t1/documents/d1/versions");
  });

  it("torna a pujar el mateix fitxer i ho diu sense inventar-se cap canvi", async () => {
    uploadRaw.mockResolvedValueOnce({ status: 200, body: {} });
    const { onUploaded } = await versionButton();
    await pick();

    expect(screen.getByText(/ja era la versió vigent/i)).toBeTruthy();
    expect(onUploaded).toHaveBeenCalledWith(false);
  });

  it("qui no decideix no hi pot pujar res", async () => {
    role = "technical";
    await versionButton();

    expect(screen.queryByRole("button", { name: /Nova versió/ })).toBeNull();
  });
});
