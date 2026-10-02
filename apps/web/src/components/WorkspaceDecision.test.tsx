import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { decisionFixture, workspaceFixture } from "@/test/workspace-fixture";

const post = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, post: (...args: unknown[]) => post(...args) } };
});

import { ApiError } from "@/lib/api";

import { DecisionHistory, DecisionPanel } from "./WorkspaceDecision";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const onDecided = vi.fn(async () => undefined);

function renderPanel(overrides: Parameters<typeof workspaceFixture>[0] = {}) {
  return render(<DecisionPanel workspace={workspaceFixture(overrides)} onDecided={onDecided} signerName="Anna Puig" />);
}

const button = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;
const dialog = () => screen.getByRole("dialog");
const submitButton = () => [...dialog().querySelectorAll("button")].find((b) => /^(Signa|Registra)/.test(b.textContent ?? ""))! as HTMLButtonElement;

describe("DecisionPanel · l'acte de decidir", () => {
  beforeEach(() => {
    post.mockReset();
    onDecided.mockClear();
  });

  it("tres sortides reals i cap recomanació del sistema: només la decisió humana", () => {
    const { container } = renderPanel({ recommendation: "review", eligibility: "review_required" });
    expect(button(/Presentar-se · GO/).disabled).toBe(false);
    expect(button(/No presentar-se · NO-GO/).disabled).toBe(false);
    expect(button(/^Ajornar/).disabled).toBe(false);
    expect(container.textContent).toContain("Quedarà signada per Anna Puig");
    expect(screen.queryByText(/Recomanació del sistema/)).toBeNull();
    expect(container.querySelector("[data-kind='rule']")).toBeNull();
    expect(container.textContent).not.toMatch(/Accepta la recomanació|Aplica la recomanació|El sistema suggereix/);
    expect(screen.queryByRole("button", { name: /recomanaci/i })).toBeNull();
    // Cap decisió encara: es diu, no s'omple amb la recomanació.
    expect(container.textContent).toContain("Encara no s'ha pres cap decisió");
    expect(container.textContent).toContain("Només compta com a decisió la que registra explícitament una persona.");
    expect(container.textContent).not.toContain("La recomanació automàtica");
  });

  it("sense recomanació tampoc no hi surt cap bloc de recomanació", () => {
    renderPanel({ recommendation: null });
    expect(screen.queryByText(/Recomanació del sistema/)).toBeNull();
    expect(screen.queryByText(/El sistema no pot suggerir/)).toBeNull();
  });

  it("GO amb avisos oberts: el modal ensenya què quedarà registrat i exigeix el motiu; el POST és el de sempre", async () => {
    post.mockResolvedValue({});
    renderPanel({ go_requires_reason: true, open_warnings: ["mandatory_unknown", "mandatory_needs_review"] });
    expect(screen.getByText(/Motiu · obligatori si aproves/).textContent).toContain("2 avisos oberts");
    fireEvent.click(button(/Presentar-se · GO/));
    const modal = dialog();
    expect(modal.textContent).toContain("Signada per");
    expect(modal.textContent).toContain("Anna Puig");
    expect(modal.textContent).toContain("Hi ha requisits obligatoris sense dades al perfil");
    expect(modal.textContent).toContain("Hi ha requisits obligatoris que una persona ha de revisar");
    expect(modal.textContent).toContain("no es podrà editar");
    const submit = submitButton();
    expect(submit.disabled).toBe(true);
    const textarea = modal.querySelector("textarea")!;
    expect(textarea.getAttribute("maxlength")).toBe("4000");
    fireEvent.change(textarea, { target: { value: "  Anem en UTE amb Obres Vallès SL.  " } });
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/decisions", {
      decision: "go",
      reason: "Anem en UTE amb Obres Vallès SL.",
    });
    expect(onDecided).toHaveBeenCalledTimes(1);
    expect(onDecided).toHaveBeenCalledWith("go");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("GO sense avisos: el motiu és opcional i s'envia null si és buit", async () => {
    post.mockResolvedValue({});
    renderPanel();
    expect(screen.getByText(/^Motiu · opcional/)).toBeTruthy();
    fireEvent.click(button(/Presentar-se · GO/));
    expect(submitButton().disabled).toBe(false);
    fireEvent.click(submitButton());
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/decisions", { decision: "go", reason: null });
  });

  it("NO-GO i Ajornar mai exigeixen motiu, ni amb avisos oberts", async () => {
    post.mockResolvedValue({});
    renderPanel({ go_requires_reason: true, open_warnings: ["confirmed_blockers"], counters: { ...workspaceFixture().counters, confirmed_blockers: 3 } });
    fireEvent.click(button(/No presentar-se · NO-GO/));
    expect(dialog().textContent).toContain("Motiu (opcional)");
    expect(submitButton().disabled).toBe(false);
    fireEvent.click(submitButton());
    await flush();
    expect(post).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/decisions", { decision: "no_go", reason: null });
    expect(onDecided).toHaveBeenLastCalledWith("no_go");

    fireEvent.click(button(/^Ajornar/));
    expect(dialog().textContent).toContain("Ajornar");
    expect(submitButton().disabled).toBe(false);
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "Esperem la UTE." } });
    fireEvent.click(submitButton());
    await flush();
    expect(post).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/decisions", { decision: "defer", reason: "Esperem la UTE." });
    expect(onDecided).toHaveBeenLastCalledWith("defer");
  });

  it("si el backend diu que cal motiu, l'error surt al camp i el modal no es tanca", async () => {
    post.mockRejectedValue(
      new ApiError(422, "decision_reason_required", "Cal escriure el motiu: hi ha avisos oberts sobre aquesta candidatura.", [], { warnings: ["deadline_passed"] }),
    );
    renderPanel();
    fireEvent.click(button(/Presentar-se · GO/));
    fireEvent.click(submitButton());
    await flush();
    expect(screen.getByRole("dialog").textContent).toContain("Cal escriure el motiu");
    expect(screen.getByRole("dialog").textContent).toContain("El termini de presentació ja ha passat.");
    expect(onDecided).not.toHaveBeenCalled();
  });

  it("un 403 s'explica com a permís, i un altre error es pot reintentar", async () => {
    post.mockRejectedValueOnce(new ApiError(403, "permission_denied", "El teu rol no pot aprovar ni descartar candidatures."));
    renderPanel();
    fireEvent.click(button(/No presentar-se · NO-GO/));
    fireEvent.click(submitButton());
    await flush();
    expect(dialog().textContent).toContain("El teu rol no pot aprovar ni descartar candidatures.");
    expect(dialog().textContent).toContain("Només");
    post.mockRejectedValueOnce(new ApiError(500, "internal_error", "Error intern"));
    fireEvent.click(submitButton());
    await flush();
    expect(dialog().textContent).toContain("No s'ha pogut registrar la decisió");
    expect(submitButton().disabled).toBe(false);
  });

  it("sense permís: els tres botons es veuen, desactivats, amb qui sí que pot", () => {
    const { container } = renderPanel({ can_decide: false });
    expect(button(/Presentar-se · GO/).disabled).toBe(true);
    expect(button(/No presentar-se · NO-GO/).disabled).toBe(true);
    expect(button(/^Ajornar/).disabled).toBe(true);
    expect(container.textContent).toMatch(/Només .* pot signar la decisió\./);
    expect(container.textContent).toContain("Responsable de candidatures");
  });

  it("indeterminat: «Cap requisit avaluat», mai un zero verd ni una recomanació GO", () => {
    const { container } = renderPanel({
      eligibility: "undetermined",
      recommendation: "undetermined",
      go_requires_reason: true,
      open_warnings: ["undetermined_eligibility", "missing_compliance"],
      counters: { ...workspaceFixture().counters, total: 0, mandatory_total: 0, mandatory_satisfies: 0, mandatory_unknown: 0, mandatory_needs_review: 0 },
    });
    expect(container.textContent).not.toContain("El sistema no pot suggerir");
    expect(container.querySelector("[data-level='ready']")).toBeNull();
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).toContain("Cap requisit avaluat");
    expect(dialog().textContent).toContain("Decidiràs sense saber si l'empresa compleix");
  });

  it("amb decisió vigent: qui, quan i per què; caducada es diu sense amagar-la; se'n pot prendre una de nova", () => {
    const decision = decisionFixture();
    const { container } = renderPanel({
      status: "go_approved",
      human_decision: "go",
      latest_decision: decision,
      decisions: [decision],
      decision_outdated: true,
      decision_reasons: ["tender_changed_since_decision"],
    });
    const current = screen.getByText(/Decisió vigent/).closest("div")!;
    expect(current.textContent).toContain("GO");
    expect(current.textContent).toContain("Anna Puig");
    expect(current.textContent).toContain("2026");
    expect(current.textContent).toContain("Anem en UTE amb Obres Vallès SL.");
    expect(current.querySelector("[data-kind='human']")).not.toBeNull();
    expect(container.textContent).toContain("el plec ha canviat");
    expect(button(/Presentar-se · GO/).disabled).toBe(false);
    expect(container.textContent).toContain("decisió nova");
  });

  it("l'anàlisi poc fiable i el termini passat surten al modal tal com quedaran registrats", () => {
    renderPanel({
      analysis_basis: "simulation",
      deadline_passed: true,
      days_remaining: -2,
      go_requires_reason: true,
      open_warnings: ["deadline_passed", "unreliable_analysis"],
    });
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).toContain("Simulació de desenvolupament");
    expect(dialog().textContent).toContain("El termini de presentació ja ha passat.");
  });
});

describe("DecisionHistory", () => {
  it("buit: ho diu i no inventa res", () => {
    render(<DecisionHistory workspace={workspaceFixture()} />);
    expect(screen.getByText(/Encara no s'ha pres cap decisió/)).toBeTruthy();
    expect(screen.getByText(/Només compta com a decisió la que registra explícitament una persona\./)).toBeTruthy();
    expect(screen.queryByText(/La recomanació automàtica/)).toBeNull();
  });

  it("append-only en ordre, «Vigent» només a la primera, qui/quan/motiu i els avisos d'aleshores", () => {
    const newer = decisionFixture({ id: "d2", decision: "no_go", decided_at: "2026-09-14T08:00:00Z", decided_by_name: "Pere Soler", reason: null, open_warnings: null });
    const older = decisionFixture();
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [newer, older] })} />);
    const rows = [...container.querySelectorAll("li")];
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("NO-GO");
    expect(rows[0].textContent).toContain("Pere Soler");
    expect(rows[0].textContent).toContain("Vigent");
    expect(rows[0].textContent).toContain("no es van registrar");
    expect(rows[1].textContent).toContain("GO");
    expect(rows[1].textContent).toContain("Anna Puig");
    expect(rows[1].textContent).toContain("Anem en UTE amb Obres Vallès SL.");
    expect(rows[1].textContent).not.toContain("Vigent");
    expect(rows[1].textContent).toContain("Hi ha requisits obligatoris sense dades al perfil");
    expect(container.querySelectorAll("[data-kind='human']")).toHaveLength(2);
  });
});
