import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api";
import { readinessFixture } from "@/test/readiness-fixture";
import { decisionFixture, workspaceFixture } from "@/test/workspace-fixture";

/**
 * Les notes amb la secció REAL (cap secció mockejada). Ajust del propietari
 * (22-10-2026): les notes s'escriuen en l'anàlisi, abans de decidir —sense cap
 * decisió, amb la decisió ajornada i amb GO—; només un NO-GO vigent les tanca.
 * Amb NO-GO el control existeix, es pot omplir, però és inert —el navegador no
 * entrega el clic a un control dins d'un `fieldset` desactivat— i cap crida
 * mutadora surt de la UI. El mateix gest en l'anàlisi sí que escriu: així es
 * demostra que el que atura la crida és el mode lectura, no el muntatge del test.
 */

const get = vi.fn();
const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...a: unknown[]) => get(...a), post: (...a: unknown[]) => post(...a) } };
});
vi.mock("@/components/SessionProvider", () => ({
  useSession: () => ({
    companyId: "c1",
    user: { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
    membership: { id: "m1", company_id: "c1", role: "owner", is_default: true },
  }),
}));
vi.mock("next/navigation", () => ({
  useParams: () => ({ workspaceId: "w1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/candidatures/w1",
  useSearchParams: () => new URLSearchParams("pas=preparar&area=pla"),
}));

import WorkspacePage from "./page";

const READ_ONLY = "Es pot consultar; s'obre a edició amb GO";
const STUDY_OPEN = "Les notes i els dubtes ja s'hi poden escriure";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const go = () => ({ status: "preparing_bid" as const, human_decision: "go" as const, latest_decision: decisionFixture(), decisions: [decisionFixture()] });
const deferred = () => ({ status: "awaiting_decision" as const, human_decision: "defer" as const, latest_decision: decisionFixture({ decision: "defer" }) });
const noGo = () => ({ status: "no_go_approved" as const, human_decision: "no_go" as const, latest_decision: decisionFixture({ decision: "no_go" }) });

function respond(workspace: ReturnType<typeof workspaceFixture>) {
  get.mockImplementation((path: string) => {
    if (path.includes("/final-readiness")) return Promise.resolve(readinessFixture());
    if (path === "/companies/c1/workspaces/w1") return Promise.resolve(workspace);
    return Promise.reject(new ApiError(404, "not_found", "No trobat"));
  });
}

async function typeANoteAndClick() {
  const textarea = screen.getByLabelText("Escriu una nota") as HTMLTextAreaElement;
  fireEvent.change(textarea, { target: { value: "Parlat amb el responsable." } });
  const button = screen.getByRole("button", { name: "Afegeix la nota" }) as HTMLButtonElement;
  // El clic nadiu, el que fa una persona: el navegador no l'entrega a un
  // control desactivat (també per `fieldset`), i jsdom ho respecta.
  button.click();
  await flush();
  return { textarea, button };
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
  post.mockResolvedValue({ id: "n1", text: "Parlat amb el responsable.", author_id: "u1", author_name: "Anna Puig", created_at: "2026-10-10T10:00:00Z" });
});

describe("Notes amb la secció real: s'escriuen en l'anàlisi; un NO-GO les tanca", () => {
  it("sense cap decisió (estat `analysis`): el gest escriu la nota; la resta de Preparar continua en lectura", async () => {
    respond(workspaceFixture());
    render(<WorkspacePage />);
    await flush();
    const { button } = await typeANoteAndClick();
    expect(button.closest("fieldset[disabled]")).toBeNull();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/notes", { text: "Parlat amb el responsable." });
    // Checklist i tasques continuen demanant GO, i la nota de lectura diu les dues coses.
    expect(document.querySelector("fieldset[data-prepare-mode='read-only']")).not.toBeNull();
    expect(screen.getByText(READ_ONLY)).toBeTruthy();
    expect(screen.getByText(STUDY_OPEN)).toBeTruthy();
  });

  it("amb la decisió ajornada: la nota també s'escriu", async () => {
    respond(workspaceFixture(deferred()));
    render(<WorkspacePage />);
    await flush();
    const { button } = await typeANoteAndClick();
    expect(button.closest("fieldset[disabled]")).toBeNull();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/notes", { text: "Parlat amb el responsable." });
  });

  it("amb NO-GO vigent: el camp i el botó pengen d'un fieldset desactivat, el clic és inert i no surt cap crida", async () => {
    respond(workspaceFixture(noGo()));
    render(<WorkspacePage />);
    await flush();
    const { textarea, button } = await typeANoteAndClick();
    expect(textarea.closest("fieldset[disabled]")).not.toBeNull();
    expect(button.closest("fieldset[disabled]")).not.toBeNull();
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByText(READ_ONLY)).toBeTruthy();
    expect(screen.queryByText(STUDY_OPEN)).toBeNull();
  });

  it("amb GO: el mateix gest escriu la nota", async () => {
    respond(workspaceFixture(go()));
    render(<WorkspacePage />);
    await flush();
    const { button } = await typeANoteAndClick();
    expect(button.closest("fieldset[disabled]")).toBeNull();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/notes", { text: "Parlat amb el responsable." });
  });
});
