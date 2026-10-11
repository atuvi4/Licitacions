import fs from "node:fs";
import path from "node:path";

import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { analysisFixture } from "@/test/analysis-fixture";
import { decisionFixture, workspaceFixture } from "@/test/workspace-fixture";
import type { DecisionAnalysisState } from "@/lib/analysis";
import type { AnalysisDecisionSnapshotV1 } from "@/lib/workspace";

function snapshot(overrides: Partial<AnalysisDecisionSnapshotV1> = {}): AnalysisDecisionSnapshotV1 {
  return {
    schema_version: 1,
    state: "current",
    rules_version: "v3.1",
    run_created_at: "2026-09-12T09:00:00Z",
    fit: { satisfies: 4, does_not_satisfy: 1, unknown: 19, not_applicable: 10 },
    points: { secured: 0, at_risk: 27.0, lost: 5, not_assessable: 68, max: 100 },
    blocker_potential_count: 2,
    findings_count: 3,
    warnings: [],
    ...overrides,
  };
}

const post = vi.fn();
const get = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: { ...actual.api, post: (...args: unknown[]) => post(...args), get: (...args: unknown[]) => get(...args) },
  };
});

import { ApiError } from "@/lib/api";

import { DecisionHistory, DecisionPanel } from "./WorkspaceDecision";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const onDecided = vi.fn(async () => undefined);
const onDecisionConflict = vi.fn(async () => undefined);

function renderPanel(overrides: Parameters<typeof workspaceFixture>[0] = {}, analysis?: DecisionAnalysisState) {
  return render(
    <DecisionPanel
      workspace={workspaceFixture(overrides)}
      onDecided={onDecided}
      onDecisionConflict={onDecisionConflict}
      signerName="Anna Puig"
      analysis={analysis}
    />,
  );
}

const button = (name: RegExp) => screen.getByRole("button", { name }) as HTMLButtonElement;
const dialog = () => screen.getByRole("dialog");
const submitButton = () => [...dialog().querySelectorAll("button")].find((b) => /^(Signa|Registra)/.test(b.textContent ?? ""))! as HTMLButtonElement;

describe("DecisionPanel · l'acte de decidir", () => {
  beforeEach(() => {
    post.mockReset();
    get.mockReset();
    onDecided.mockClear();
    onDecisionConflict.mockReset();
    onDecisionConflict.mockImplementation(async () => undefined);
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
    renderPanel({ go_requires_reason: true, decision_warnings: ["blocker_potential", "analysis_stale"] });
    expect(screen.getByText(/Motiu · obligatori si aproves/).textContent).toContain("el registre de la decisió inclou 2 avisos de l'anàlisi del plec");
    fireEvent.click(button(/Presentar-se · GO/));
    const modal = dialog();
    expect(modal.textContent).toContain("Signada per");
    expect(modal.textContent).toContain("Anna Puig");
    expect(modal.textContent).toContain("L'anàlisi detecta possibles bloqueigs (no confirmats)");
    expect(modal.textContent).toContain("L'anàlisi del plec està desactualitzada");
    expect(modal.textContent).not.toContain("no és una conclusió vigent");
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
    renderPanel({ go_requires_reason: true, decision_warnings: ["blocker_potential"], counters: { ...workspaceFixture().counters, confirmed_blockers: 3 } });
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
      new ApiError(422, "decision_reason_required", "Cal escriure el motiu: hi ha avisos oberts sobre aquesta candidatura.", [], { warnings: ["deadline_passed"], basis: "analysis_v3" }),
    );
    renderPanel();
    fireEvent.click(button(/Presentar-se · GO/));
    fireEvent.click(submitButton());
    await flush();
    expect(screen.getByRole("dialog").textContent).toContain(
      "Cal un motiu: el registre inclou avisos de l'anàlisi del plec: El termini de presentació ja ha passat.",
    );
    expect(screen.getByRole("dialog").textContent).not.toContain("Cal escriure el motiu");
    expect(onDecided).not.toHaveBeenCalled();
  });

  it("després d'un 422 el modal passa a mode obligatori i mostra els avisos del backend", async () => {
    post.mockRejectedValueOnce(
      new ApiError(422, "decision_reason_required", "x", [], { warnings: ["analysis_stale"], basis: "analysis_v3" }),
    );
    post.mockResolvedValueOnce({});
    renderPanel({ go_requires_reason: false, decision_warnings: [] });
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).toContain("Motiu (opcional)");
    expect(submitButton().disabled).toBe(false);
    fireEvent.click(submitButton());
    await flush();
    const text = dialog().textContent ?? "";
    expect(text).toContain("Cal un motiu: el registre inclou avisos de l'anàlisi del plec: L'anàlisi del plec està desactualitzada.");
    expect(text).toContain("Motiu (obligatori: hi ha avisos de l'anàlisi del plec)");
    expect(text).not.toContain("Motiu (opcional)");
    expect(text).toContain("Avisos de l'anàlisi del plec que quedaran registrats");
    expect(text).not.toContain("Cap avís de l'anàlisi del plec.");
    expect(dialog().querySelector("textarea")!.getAttribute("placeholder")).toContain("malgrat els avisos");
    expect(submitButton().disabled).toBe(true);
    expect(post).toHaveBeenCalledTimes(1);
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "Tot i la caducitat." } });
    expect(submitButton().disabled).toBe(false);
    fireEvent.click(submitButton());
    await flush();
    expect(post).toHaveBeenCalledTimes(2);
    expect(post).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/decisions", {
      decision: "go",
      reason: "Tot i la caducitat.",
    });
  });

  describe("refresc del workspace després de decision_reason_required", () => {
    const settle = flush;
    const reasonRequired = () =>
      new ApiError(422, "decision_reason_required", "x", [], { warnings: ["analysis_stale"], basis: "analysis_v3" });

    it("un 422 decision_reason_required rellegeix el workspace un cop, sense registrar la decisió", async () => {
      post.mockRejectedValueOnce(reasonRequired());
      renderPanel({ go_requires_reason: false, decision_warnings: [] });
      fireEvent.click(button(/Presentar-se · GO/));
      fireEvent.click(submitButton());
      await settle();
      expect(onDecisionConflict).toHaveBeenCalledTimes(1);
      expect(onDecided).not.toHaveBeenCalled();
      expect(dialog().textContent).toContain("Cal un motiu: ");
    });

    it("el modal mostra l'error i el workspace es rellegeix un sol cop", async () => {
      post.mockRejectedValueOnce(reasonRequired());
      renderPanel({ go_requires_reason: false, decision_warnings: [] });
      fireEvent.click(button(/Presentar-se · GO/));
      fireEvent.click(submitButton());
      await settle();
      expect(dialog().textContent).toContain("Cal un motiu: ");
      expect(onDecisionConflict).toHaveBeenCalledTimes(1);
    });

    it("després del conflicte, la fila «Anàlisi del plec» segueix el nou estat amb el modal obert", async () => {
      post.mockRejectedValueOnce(reasonRequired());
      const ws = workspaceFixture({ go_requires_reason: false, decision_warnings: [] });
      const props = { onDecided, onDecisionConflict, signerName: "Anna Puig" };
      const { rerender } = render(<DecisionPanel workspace={ws} analysis={{ kind: "current", run: analysisFixture() }} {...props} />);
      fireEvent.click(button(/Presentar-se · GO/));
      fireEvent.click(submitButton());
      await settle();
      expect(onDecisionConflict).toHaveBeenCalledTimes(1);
      rerender(<DecisionPanel workspace={ws} analysis={{ kind: "stale", run: analysisFixture() }} {...props} />);
      expect(dialog().textContent).toContain("desactualitzada");
      rerender(<DecisionPanel workspace={ws} analysis={{ kind: "absent" }} {...props} />);
      expect(dialog().textContent).toContain("Encara no hi ha anàlisi d'aquesta licitació.");
      expect(screen.getByRole("dialog")).toBeTruthy();
    });

    it("un POST correcte no rellegeix res: només onDecided", async () => {
      post.mockResolvedValueOnce({});
      renderPanel();
      fireEvent.click(button(/Presentar-se · GO/));
      fireEvent.click(submitButton());
      await settle();
      expect(onDecisionConflict).not.toHaveBeenCalled();
      expect(onDecided).toHaveBeenCalledTimes(1);
    });

    it.each([
      ["403", new ApiError(403, "permission_denied", "Sense permís")],
      ["500", new ApiError(500, "internal_error", "Error intern")],
      ["xarxa", new Error("fallada de xarxa")],
    ])("un error %s no rellegeix el workspace", async (_name, error) => {
      post.mockRejectedValueOnce(error);
      renderPanel();
      fireEvent.click(button(/Presentar-se · GO/));
      fireEvent.click(submitButton());
      await settle();
      expect(onDecisionConflict).not.toHaveBeenCalled();
      expect(onDecided).not.toHaveBeenCalled();
    });

    it("amb el workspace refrescat, en reobrir el modal el mode obligatori surt de l'estat, sense l'error antic ni avisos repetits", async () => {
      post.mockRejectedValueOnce(reasonRequired());
      const { rerender } = renderPanel({ go_requires_reason: false, decision_warnings: [] });
      fireEvent.click(button(/Presentar-se · GO/));
      fireEvent.click(submitButton());
      await settle();
      rerender(
        <DecisionPanel
          workspace={workspaceFixture({ go_requires_reason: true, decision_warnings: ["analysis_stale"] })}
          onDecided={onDecided}
          onDecisionConflict={onDecisionConflict}
          signerName="Anna Puig"
        />,
      );
      // Encara obert: continua en mode obligatori amb l'error del backend.
      expect(dialog().textContent).toContain("Cal un motiu: ");
      fireEvent.click(screen.getByRole("button", { name: /Cancel·la/ }));
      fireEvent.click(button(/Presentar-se · GO/));
      const text = dialog().textContent ?? "";
      expect(text).toContain("Motiu (obligatori: hi ha avisos de l'anàlisi del plec)");
      expect(text.split("L'anàlisi del plec està desactualitzada").length - 1).toBe(1);
      expect(text).not.toContain("Cal un motiu");
      expect(submitButton().disabled).toBe(true);
      fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "Motiu." } });
      expect(submitButton().disabled).toBe(false);
    });
  });

  describe("descripció del modal segons l'anàlisi", () => {
    const CURRENT_TEXT =
      "Quedarà registrada la decisió, qui la signa, quan i el motiu, amb la fotografia de l'anàlisi del plec vigent i els seus avisos.";
    const ABSENT_TEXT =
      "Quedarà registrada la decisió, qui la signa, quan i el motiu, i que en signar no hi havia cap anàlisi del plec vigent.";

    it("sense anàlisi: no promet cap fotografia", () => {
      renderPanel({}, { kind: "absent" });
      fireEvent.click(button(/Presentar-se · GO/));
      expect(dialog().textContent).toContain(ABSENT_TEXT);
      expect(screen.getByText(ABSENT_TEXT).textContent).not.toContain("fotografia");
      expect(dialog().textContent).not.toContain(CURRENT_TEXT);
    });

    it.each<[string, DecisionAnalysisState | undefined]>([
      ["current", { kind: "current", run: analysisFixture() }],
      ["stale", { kind: "stale", run: analysisFixture() }],
      ["loading", { kind: "loading" }],
      ["error", { kind: "error" }],
      ["prop absent", undefined],
    ])("%s: el text de sempre", (_name, state) => {
      renderPanel({}, state);
      fireEvent.click(button(/Presentar-se · GO/));
      expect(dialog().textContent).toContain(CURRENT_TEXT);
      expect(dialog().textContent).not.toContain("no hi havia cap anàlisi");
    });
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

  it("indeterminat: el motor antic no es presenta com a conclusió vigent", () => {
    const { container } = renderPanel({
      eligibility: "undetermined",
      recommendation: "undetermined",
      go_requires_reason: true,
      open_warnings: ["undetermined_eligibility", "missing_compliance"],
      decision_warnings: ["analysis_missing"],
    });
    expect(container.textContent).not.toContain("El sistema no pot suggerir");
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).not.toContain("Cap requisit avaluat");
    expect(dialog().textContent).not.toContain("Decidiràs sense saber");
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

  it("el termini passat surt al modal i la simulació del motor antic no", () => {
    renderPanel({
      analysis_basis: "simulation",
      deadline_passed: true,
      days_remaining: -2,
      go_requires_reason: true,
      open_warnings: ["unreliable_analysis"],
      decision_warnings: ["deadline_passed"],
    });
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).not.toContain("Simulació");
    expect(dialog().textContent).not.toContain("Motor d'anàlisi no configurat");
    expect(dialog().textContent).toContain("El termini de presentació ja ha passat");
  });

  it("la fila «Termini» del modal diu «Fora de termini» amb el comptador únic, mai «Ja ha passat» (N2)", () => {
    renderPanel({
      deadline_at: "2026-09-01T10:00:00Z",
      deadline_passed: true,
      days_remaining: -38,
      go_requires_reason: true,
      decision_warnings: ["deadline_passed"],
    });
    fireEvent.click(button(/Presentar-se · GO/));
    const row = [...dialog().querySelectorAll("dt")].find((dt) => dt.textContent === "Termini")!.nextElementSibling!;
    expect(row.textContent).toContain("Fora de termini");
    expect(row.textContent).toMatch(/2026/);
    expect(dialog().textContent).not.toContain("Ja ha passat");
  });

  it("sense data de termini la fila «Termini» diu «No consta» (N2)", () => {
    renderPanel({ deadline_at: null, deadline_passed: false, days_remaining: null });
    fireEvent.click(button(/Presentar-se · GO/));
    const row = [...dialog().querySelectorAll("dt")].find((dt) => dt.textContent === "Termini")!.nextElementSibling!;
    expect(row.textContent).toBe("No consta");
  });

  it("amb recommendation, counters, eligibility i analysis_basis informats, el modal no en mostra res", () => {
    const base = workspaceFixture();
    renderPanel({
      recommendation: "go_candidate",
      eligibility: "undetermined",
      analysis_basis: "provider_not_configured",
      analysis_outdated: true,
      counters: { ...base.counters, confirmed_blockers: 4 },
    });
    fireEvent.click(button(/Presentar-se · GO/));
    const text = dialog().textContent ?? "";
    for (const forbidden of [
      "Recomanació",
      "recomana",
      "Bloqueigs confirmats",
      "Cap requisit avaluat",
      "Decidiràs sense saber",
      "Simulació",
      "Motor d'anàlisi no configurat",
      "endarrerida",
    ]) {
      expect(text).not.toContain(forbidden);
    }
  });

  it("la fila «Anàlisi del plec» segueix els cinc estats amb el mateix run que la targeta", () => {
    const run = analysisFixture();
    const row = () => screen.getByText("Anàlisi del plec").closest("div")!.textContent ?? "";
    const states: Array<[DecisionAnalysisState | undefined, string]> = [
      [undefined, "Es carrega l'anàlisi…"],
      [{ kind: "loading" }, "Es carrega l'anàlisi…"],
      [{ kind: "current", run }, "3 elements a resoldre"],
      [{ kind: "current", run: { ...run, findings: [] } }, "Cap element pendent de resoldre"],
      [{ kind: "current", run: { ...run, findings: run.findings.slice(0, 1) } }, "1 element a resoldre"],
      [{ kind: "stale", run }, "3 elements a resoldre · desactualitzada"],
      [{ kind: "absent" }, "Encara no hi ha anàlisi d'aquesta licitació."],
      [{ kind: "error" }, "No s'ha pogut carregar l'anàlisi."],
    ];
    for (const [state, text] of states) {
      const { unmount } = renderPanel({}, state);
      fireEvent.click(button(/Presentar-se · GO/));
      expect(row()).toContain(text);
      if (state && (state.kind === "current" || state.kind === "stale")) {
        expect(row()).toContain("Feta el ");
        expect(row()).toContain("2026");
      }
      unmount();
    }
    expect(get).not.toHaveBeenCalled();
  });

  it("obrir, teclejar i tancar el modal no fa cap GET", () => {
    renderPanel({ go_requires_reason: true, decision_warnings: ["deadline_passed"] }, { kind: "current", run: analysisFixture() });
    fireEvent.click(button(/Presentar-se · GO/));
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "Motiu" } });
    fireEvent.click(screen.getByRole("button", { name: /Cancel·la/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(get).not.toHaveBeenCalled();
  });

  it("el copy dels avisos de l'anàlisi del plec (F-4 §5 sobre base v3) és als seus llocs", () => {
    renderPanel({ go_requires_reason: true, decision_warnings: ["deadline_passed"] });
    expect(document.body.textContent).toContain(
      "Motiu · obligatori si aproves: el registre de la decisió inclou 1 avís de l'anàlisi del plec. NO-GO i ajornar no en demanen.",
    );
    fireEvent.click(button(/Presentar-se · GO/));
    const text = dialog().textContent ?? "";
    expect(text).toContain("Avisos de l'anàlisi del plec que quedaran registrats");
    expect(text).toContain("Motiu (obligatori: hi ha avisos de l'anàlisi del plec)");
    expect(dialog().querySelector("textarea")!.getAttribute("placeholder")).toBe(
      "Per què es presenta l'empresa malgrat els avisos de l'anàlisi del plec?",
    );
    expect(text).toContain(
      "Quedarà registrada la decisió, qui la signa, quan i el motiu, amb la fotografia de l'anàlisi del plec vigent i els seus avisos.",
    );
    expect(document.body.textContent).not.toContain("compliment anterior");
  });

  it("F11: sense cap anàlisi, el motiu obligatori ho diu tal qual i mai «avís de l'anàlisi»", async () => {
    post.mockRejectedValue(new ApiError(422, "decision_reason_required", "x", [], { warnings: ["analysis_missing"], basis: "analysis_v3" }));
    renderPanel({ go_requires_reason: true, decision_warnings: ["analysis_missing"] }, { kind: "absent" } as DecisionAnalysisState);
    expect(document.body.textContent).toContain(
      "Motiu · obligatori si aproves: encara no hi ha cap anàlisi del plec. NO-GO i ajornar no en demanen.",
    );
    expect(document.body.textContent).not.toMatch(/avís de l'anàlisi|avisos de l'anàlisi/);
    fireEvent.click(button(/Presentar-se · GO/));
    const text = dialog().textContent ?? "";
    expect(text).toContain("Motiu (obligatori: encara no hi ha cap anàlisi del plec)");
    expect(text).toContain("Encara no hi ha cap anàlisi del plec");
    expect(text).not.toMatch(/avís de l'anàlisi|avisos de l'anàlisi/);
    expect(dialog().querySelector("textarea")!.getAttribute("placeholder")).toBe(
      "Per què es presenta l'empresa sense cap anàlisi del plec?",
    );
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "m" } });
    fireEvent.click(submitButton());
    await flush();
    expect(dialog().textContent).toContain("Cal un motiu: encara no hi ha cap anàlisi del plec.");
    expect(dialog().textContent).not.toMatch(/avís de l'anàlisi|avisos de l'anàlisi/);
  });

  it("F11: sense anàlisi i amb un altre avís, se separen: l'absència d'anàlisi i l'avís", () => {
    renderPanel({ go_requires_reason: true, decision_warnings: ["analysis_missing", "deadline_passed"] });
    expect(document.body.textContent).toContain(
      "Motiu · obligatori si aproves: encara no hi ha cap anàlisi del plec, i el registre de la decisió inclou 1 avís més. NO-GO i ajornar no en demanen.",
    );
    fireEvent.click(button(/Presentar-se · GO/));
    const text = dialog().textContent ?? "";
    expect(text).toContain("Motiu (obligatori: encara no hi ha cap anàlisi del plec i hi ha avisos)");
    expect(text).toContain("El termini de presentació ja ha passat");
  });

  it("zero-state del bloc d'avisos", () => {
    renderPanel({ decision_warnings: [] });
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).toContain("Cap avís de l'anàlisi del plec.");
    expect(dialog().textContent).toContain("El sistema en fa la fotografia en signar; ara mateix no n'hi ha cap.");
  });

  it("els quatre codis v3 i un de desconegut: etiquetes humanes, mai el codi cru", async () => {
    post.mockRejectedValue(new ApiError(422, "decision_reason_required", "x", [], { warnings: ["codi_desconegut_backend"], basis: "analysis_v3" }));
    renderPanel({
      go_requires_reason: true,
      decision_warnings: ["analysis_missing", "analysis_stale", "blocker_potential", "deadline_passed", "codi_desconegut_modal"],
    });
    fireEvent.click(button(/Presentar-se · GO/));
    const text = dialog().textContent ?? "";
    for (const label of [
      "Encara no hi ha cap anàlisi del plec",
      "L'anàlisi del plec està desactualitzada",
      "L'anàlisi detecta possibles bloqueigs (no confirmats)",
      "El termini de presentació ja ha passat",
      "Avís de l'anàlisi del plec",
    ]) {
      expect(text).toContain(label);
    }
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "m" } });
    fireEvent.click(submitButton());
    await flush();
    expect(dialog().textContent).toContain("Cal un motiu: el registre inclou avisos de l'anàlisi del plec: Avís de l'anàlisi del plec.");
    expect(document.body.textContent).not.toContain("codi_desconegut_modal");
    expect(document.body.textContent).not.toContain("codi_desconegut_backend");
  });

  it("l'error decision_reason_required amb diversos codis v3 els uneix amb un espai", async () => {
    post.mockRejectedValue(new ApiError(422, "decision_reason_required", "x", [], { warnings: ["analysis_stale", "deadline_passed"], basis: "analysis_v3" }));
    renderPanel({ go_requires_reason: true, decision_warnings: ["analysis_stale", "deadline_passed"] });
    fireEvent.click(button(/Presentar-se · GO/));
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "m" } });
    fireEvent.click(submitButton());
    await flush();
    expect(dialog().textContent).toContain(
      "Cal un motiu: el registre inclou avisos de l'anàlisi del plec: L'anàlisi del plec està desactualitzada. El termini de presentació ja ha passat.",
    );
  });

  it("go_requires_reason governa el botó; open_warnings legacy no decideix res", () => {
    const { unmount } = renderPanel({ go_requires_reason: false, open_warnings: ["confirmed_blockers", "deadline_passed"], decision_warnings: [] });
    expect(screen.getByText(/^Motiu · opcional/)).toBeTruthy();
    fireEvent.click(button(/Presentar-se · GO/));
    expect(submitButton().disabled).toBe(false);
    expect(dialog().textContent).toContain("Cap avís de l'anàlisi del plec.");
    expect(dialog().textContent).not.toContain("Hi ha bloqueigs confirmats");
    expect(dialog().textContent).not.toContain("El termini de presentació ja ha passat");
    unmount();
    renderPanel({ go_requires_reason: true, open_warnings: [], decision_warnings: ["analysis_missing"] });
    fireEvent.click(button(/Presentar-se · GO/));
    expect(submitButton().disabled).toBe(true);
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "ok" } });
    expect(submitButton().disabled).toBe(false);
  });

  it("el component no llegeix open_warnings del workspace per decidir (font)", () => {
    const source = fs.readFileSync(path.join(__dirname, "WorkspaceDecision.tsx"), "utf8");
    expect(source).not.toContain("workspace.open_warnings");
  });
});

describe("DecisionHistory", () => {
  it("buit: ho diu i no inventa res", () => {
    render(<DecisionHistory workspace={workspaceFixture()} />);
    expect(screen.getByText(/Encara no s'ha pres cap decisió/)).toBeTruthy();
    expect(screen.getByText(/Només compta com a decisió la que registra explícitament una persona\./)).toBeTruthy();
    expect(screen.queryByText(/La recomanació automàtica/)).toBeNull();
  });

  it("un avís desconegut a l'historial surt amb l'etiqueta neutra, mai el codi", () => {
    const entry = decisionFixture({ open_warnings: ["codi_desconegut_historial"] });
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    expect(container.textContent).toContain("Es va decidir sabent-ho: Avís del compliment anterior");
    expect(container.textContent).not.toContain("codi_desconegut_historial");
  });

  it("la línia d'evidència és un registre del compliment anterior, mai «el sistema deia»", () => {
    const entry = decisionFixture({ automated_recommendation: "go_candidate", deadline_passed: true });
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Registre del moment (compliment anterior): ");
    expect(text).toContain("el compliment anterior suggeria Candidata");
    expect(text).toContain("amb el termini ja tancat");
    expect(text).not.toContain("el sistema deia");
  });

  it("recomanació històrica desconeguda: text neutre, mai el valor cru", () => {
    const entry = decisionFixture({ automated_recommendation: "weird_value" });
    render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    expect(document.body.textContent).toContain("el compliment anterior va fer un suggeriment (valor històric)");
    expect(document.body.textContent).not.toContain("weird_value");
  });

  it("una recomanació històrica que coincideix amb una clau heretada també és desconeguda", () => {
    const entry = decisionFixture({ automated_recommendation: "toString" });
    render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    expect(document.body.textContent).toContain("el compliment anterior va fer un suggeriment (valor històric)");
    expect(document.body.textContent).not.toContain("undefined");
    expect(document.body.textContent).not.toContain("suggeria toString");
  });

  it("sense recomanació ni termini tancat: cap de les dues frases", () => {
    const entry = decisionFixture({ automated_recommendation: null, deadline_passed: false });
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Registre del moment (compliment anterior)");
    expect(text).not.toContain("suggeria");
    expect(text).not.toContain("suggeriment");
    expect(text).not.toContain("termini ja tancat");
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

  it("decisió v3 current: només el snapshot, cap text legacy, decimals formatats", () => {
    const entry = decisionFixture({
      open_warnings: null,
      automated_recommendation: null,
      compliance_run_id: null,
      requirement_run_id: null,
      confirmed_blockers: 0,
      potential_blockers: 0,
      mandatory_unknown: 0,
      mandatory_needs_review: 0,
      deadline_passed: true,
      prefit_score: 81,
      analysis_run_id: "ar1",
      analysis_snapshot_json: snapshot({ warnings: ["blocker_potential", "deadline_passed"] }),
    });
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    const text = container.textContent ?? "";
    expect(text).toContain(
      "Registre del moment (anàlisi del plec): vigent · compleix 4 · no compleix 1 · no se sap 19 · no aplica 10 · punts assegurats 0 / en risc 27 / perduts 5 / no avaluables 68 · 2 possibles bloqueigs (no confirmats) · 3 elements a resoldre · pre-fit 81",
    );
    expect(text).toContain("Es va decidir sabent-ho: L'anàlisi detecta possibles bloqueigs (no confirmats)");
    expect(text).toContain("Es va decidir sabent-ho: El termini de presentació ja ha passat");
    expect(text.match(/termini de presentació ja ha passat/g)).toHaveLength(1);
    expect(text).not.toContain("compliment anterior");
    expect(text).not.toContain("no es van registrar");
    expect(text).not.toContain("bloqueigs confirmats");
    expect(text).not.toContain("termini ja tancat");
    expect(text).not.toContain("27.0");
  });

  it("decisió v3 stale amb decimals, sense pre-fit i sense avisos", () => {
    const entry = decisionFixture({
      open_warnings: null,
      prefit_score: null,
      analysis_snapshot_json: snapshot({
        state: "stale",
        points: { secured: 1.5, at_risk: 27.0, lost: 5.25, not_assessable: 68, max: 100 },
        blocker_potential_count: 1,
        findings_count: 1,
        warnings: [],
      }),
    });
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Registre del moment (anàlisi del plec): desactualitzada · ");
    expect(text).toContain("punts assegurats 1,5 / en risc 27 / perduts 5,25 / no avaluables 68");
    expect(text).toContain("1 possible bloqueig (no confirmat) · 1 element a resoldre");
    expect(text).not.toContain("pre-fit");
    expect(text).toContain("Sense cap avís de l'anàlisi del plec.");
    expect(text).not.toContain("Sense cap avís obert");
  });

  it("decisió v3 absent: «sense anàlisi vigent» i l'avís analysis_missing, amb open_warnings null", () => {
    const entry = decisionFixture({
      open_warnings: null,
      prefit_score: null,
      analysis_run_id: null,
      analysis_snapshot_json: { schema_version: 1, state: "absent", warnings: ["analysis_missing"] },
    });
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Registre del moment (anàlisi del plec): sense anàlisi vigent");
    expect(text).not.toContain("compleix");
    expect(text).toContain("Es va decidir sabent-ho: Encara no hi ha cap anàlisi del plec");
    expect(text).not.toContain("no es van registrar");
    expect(text).not.toContain("compliment anterior");
  });

  it("v3: un codi desconegut surt neutre; la branca no depèn d'open_warnings ni dels comptadors", () => {
    const entry = decisionFixture({
      open_warnings: ["mandatory_unknown"],
      confirmed_blockers: 5,
      analysis_snapshot_json: snapshot({ warnings: ["codi_raro"] }),
    });
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [entry] })} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Es va decidir sabent-ho: Avís de l'anàlisi del plec");
    expect(text).not.toContain("codi_raro");
    expect(text).not.toContain("Registre del moment (compliment anterior)");
    expect(text).not.toContain("Hi ha requisits obligatoris sense dades");
    expect(text).not.toContain("bloqueigs confirmats");
  });

  it("legacy (snapshot null) no mostra mai el títol v3", () => {
    const { container } = render(<DecisionHistory workspace={workspaceFixture({ decisions: [decisionFixture()] })} />);
    const text = container.textContent ?? "";
    expect(text).toContain("Registre del moment (compliment anterior): ");
    expect(text).not.toContain("anàlisi del plec");
  });

  it("legacy amb open_warnings null segueix dient «no es van registrar»", () => {
    render(<DecisionHistory workspace={workspaceFixture({ decisions: [decisionFixture({ open_warnings: null })] })} />);
    expect(document.body.textContent).toContain("Els avisos d'aquell moment no es van registrar.");
  });
});
