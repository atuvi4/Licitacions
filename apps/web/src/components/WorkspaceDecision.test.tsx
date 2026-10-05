import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { analysisFixture } from "@/test/analysis-fixture";
import { decisionFixture, workspaceFixture } from "@/test/workspace-fixture";
import type { DecisionAnalysisState } from "@/lib/analysis";

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

function renderPanel(overrides: Parameters<typeof workspaceFixture>[0] = {}, analysis?: DecisionAnalysisState) {
  return render(
    <DecisionPanel workspace={workspaceFixture(overrides)} onDecided={onDecided} signerName="Anna Puig" analysis={analysis} />,
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
    expect(screen.getByText(/Motiu · obligatori si aproves/).textContent).toContain("el registre de la decisió inclou 2 avisos del compliment anterior");
    fireEvent.click(button(/Presentar-se · GO/));
    const modal = dialog();
    expect(modal.textContent).toContain("Signada per");
    expect(modal.textContent).toContain("Anna Puig");
    expect(modal.textContent).toContain("Hi ha requisits obligatoris sense dades al perfil");
    expect(modal.textContent).toContain("Hi ha requisits obligatoris que una persona ha de revisar");
    expect(modal.textContent).toContain("no és una conclusió vigent");
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
    expect(screen.getByRole("dialog").textContent).toContain(
      "Cal un motiu: el registre inclou avisos del compliment anterior: El termini de presentació ja ha passat.",
    );
    expect(screen.getByRole("dialog").textContent).not.toContain("Cal escriure el motiu");
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

  it("indeterminat: el motor antic no es presenta com a conclusió vigent", () => {
    const { container } = renderPanel({
      eligibility: "undetermined",
      recommendation: "undetermined",
      go_requires_reason: true,
      open_warnings: ["undetermined_eligibility", "missing_compliance"],
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
      open_warnings: ["deadline_passed", "unreliable_analysis"],
    });
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).not.toContain("Simulació");
    expect(dialog().textContent).not.toContain("Motor d'anàlisi no configurat");
    expect(dialog().textContent).toContain("El termini de presentació ja ha passat.");
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
    renderPanel({ go_requires_reason: true, open_warnings: ["deadline_passed"] }, { kind: "current", run: analysisFixture() });
    fireEvent.click(button(/Presentar-se · GO/));
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "Motiu" } });
    fireEvent.click(screen.getByRole("button", { name: /Cancel·la/ }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(get).not.toHaveBeenCalled();
  });

  it("el copy dels avisos del compliment anterior (spec §5) és als seus llocs", () => {
    renderPanel({ go_requires_reason: true, open_warnings: ["deadline_passed"] });
    expect(document.body.textContent).toContain(
      "Motiu · obligatori si aproves: el registre de la decisió inclou 1 avís del compliment anterior. NO-GO i ajornar no en demanen.",
    );
    fireEvent.click(button(/Presentar-se · GO/));
    const text = dialog().textContent ?? "";
    expect(text).toContain("Avisos del compliment anterior que quedaran registrats");
    expect(text).toContain("Motiu (obligatori: hi ha avisos del compliment anterior)");
    expect(dialog().querySelector("textarea")!.getAttribute("placeholder")).toBe(
      "Per què es presenta l'empresa malgrat els avisos del compliment anterior?",
    );
    expect(text).toContain(
      "Quedarà registrada la decisió, qui la signa, quan i el motiu, amb els avisos del compliment anterior. El sistema també hi desa, com a registre intern, l'estat d'aquell compliment: no és una conclusió vigent.",
    );
  });

  it("zero-state del bloc d'avisos", () => {
    renderPanel({ open_warnings: [] });
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).toContain("Cap avís del compliment anterior.");
    expect(dialog().textContent).toContain("El sistema en fa la fotografia en signar; ara mateix no n'hi ha cap.");
  });

  it("un codi d'avís desconegut mai no es veu: «Avís del compliment anterior»", async () => {
    post.mockRejectedValue(new ApiError(422, "decision_reason_required", "x", [], { warnings: ["codi_desconegut_backend"] }));
    renderPanel({ go_requires_reason: true, open_warnings: ["codi_desconegut_modal"] });
    fireEvent.click(button(/Presentar-se · GO/));
    expect(dialog().textContent).toContain("Avís del compliment anterior");
    fireEvent.change(dialog().querySelector("textarea")!, { target: { value: "m" } });
    fireEvent.click(submitButton());
    await flush();
    expect(dialog().textContent).toContain("Cal un motiu: el registre inclou avisos del compliment anterior: Avís del compliment anterior.");
    expect(document.body.textContent).not.toContain("codi_desconegut_modal");
    expect(document.body.textContent).not.toContain("codi_desconegut_backend");
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
});
