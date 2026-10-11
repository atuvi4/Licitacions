import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { findingFixture, readinessFixture } from "@/test/readiness-fixture";
import type { FinalReadiness, ReadinessFinding } from "@/lib/readiness";

const get = vi.fn();
let search = "";
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, get: (...args: unknown[]) => get(...args) } };
});
vi.mock("next/navigation", () => ({
  usePathname: () => "/candidatures/w1",
  useSearchParams: () => new URLSearchParams(search),
}));

import { ReadinessControl } from "./ReadinessControl";
import { ReadinessProvider, useInvalidateReadiness } from "./ReadinessProvider";

const flush = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

const renderControl = () =>
  render(
    <ReadinessProvider companyId="c1" workspaceId="w1">
      <ReadinessControl />
    </ReadinessProvider>,
  );

async function mount(readiness: Partial<FinalReadiness>, findings?: ReadinessFinding[]) {
  get.mockResolvedValue(readinessFixture({ ...readiness, ...(findings ? { findings } : {}) }));
  const view = renderControl();
  await flush();
  return view;
}

/** Obre el calaix i torna el botó que l'ha obert. */
async function open() {
  const trigger = screen.getByRole("button", { name: /pendents? de preparació|A punt|Preparació desconeguda/ });
  // Un clic real deixa el focus al botó; jsdom no ho fa sol i el calaix ha de
  // poder tornar el focus on era.
  trigger.focus();
  await act(async () => {
    fireEvent.click(trigger);
  });
  return trigger;
}

describe("Indicador de preparació final", () => {
  beforeEach(() => {
    get.mockReset();
    search = "pas=preparar";
  });

  it("mentre carrega no diu res: ni «A punt» ni cap recompte", async () => {
    get.mockImplementation(() => new Promise(() => {}));
    const { container } = renderControl();
    expect(screen.queryByText("A punt")).toBeNull();
    expect(container.textContent).not.toMatch(/bloqueig|bloquej|pendent/);
    expect(container.querySelector("[aria-busy='true']")).not.toBeNull();
  });

  it("llegeix la preparació de l'endpoint d'S6, sense recalcular-la", async () => {
    await mount({});
    expect(get).toHaveBeenCalledTimes(1);
    expect(get).toHaveBeenCalledWith("/companies/c1/workspaces/w1/final-readiness");
  });

  it("sense pendents: «A punt»", async () => {
    await mount({});
    expect(screen.getByText("A punt")).toBeTruthy();
  });

  it("amb pendents: el recompte, i en singular quan només n'hi ha un", async () => {
    const view = await mount({}, [
      findingFixture({ severity: "blocker" }),
      findingFixture({ severity: "blocker", code: "economic_offer_not_started", domain: "economic" }),
    ]);
    expect(screen.getByText("2 pendents de preparació (totes les àrees)")).toBeTruthy();
    expect(screen.queryByText("A punt")).toBeNull();
    view.unmount();

    await mount({}, [findingFixture({ severity: "blocker" })]);
    expect(screen.getByText("1 pendent de preparació (totes les àrees)")).toBeTruthy();
  });

  it("els avisos i la informació no compten al capçal", async () => {
    await mount({}, [
      findingFixture({ severity: "blocker" }),
      findingFixture({ severity: "warning", code: "technical_solution_requirement_missing", domain: "technical" }),
      findingFixture({ severity: "info", code: "economic_unit_limit_unknown", domain: "economic" }),
    ]);
    expect(screen.getByText("1 pendent de preparació (totes les àrees)")).toBeTruthy();
  });

  it("si no es pot llegir, no diu «A punt»: diu que no ho sap i deixa reintentar", async () => {
    get.mockRejectedValue(Object.assign(new Error("Error intern"), { status: 500 }));
    renderControl();
    await flush();
    expect(screen.queryByText("A punt")).toBeNull();
    expect(screen.getByText("Preparació desconeguda")).toBeTruthy();

    await open();
    expect(screen.getByRole("dialog").textContent).toContain("No s'ha pogut llegir la preparació final");
    get.mockResolvedValue(readinessFixture());
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: /Torna-ho a provar/ }));
    });
    await flush();
    expect(get).toHaveBeenCalledTimes(2);
    expect(screen.getByText("A punt")).toBeTruthy();
  });

  it("una resposta que no es pot interpretar tampoc no es dona per bona", async () => {
    get.mockResolvedValue({ workspace_id: "w1", ready_for_final_review: true });
    renderControl();
    await flush();
    expect(screen.queryByText("A punt")).toBeNull();
    expect(screen.getByText("Preparació desconeguda")).toBeTruthy();
  });
});

describe("Calaix de preparació final", () => {
  beforeEach(() => {
    get.mockReset();
    search = "pas=preparar";
  });

  it("s'obre des del capçal, es tanca amb Escape i el focus torna al control", async () => {
    await mount({}, [findingFixture({ severity: "blocker" })]);
    const trigger = await open();
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("Preparació final");

    await act(async () => {
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it("agrupa pels tres nivells de gravetat, i el domini va dins de cada fila", async () => {
    await mount({}, [
      findingFixture({ domain: "economic", severity: "info", code: "economic_unit_limit_unknown" }),
      findingFixture({ domain: "economic", severity: "blocker", code: "economic_offer_price_missing" }),
      findingFixture({ domain: "dossier", severity: "blocker", code: "mandatory_evidence_missing" }),
    ]);
    await open();
    const headings = screen.getAllByRole("heading", { level: 3 }).map((node) => node.textContent);
    expect(headings[0]).toContain("Cal resoldre per congelar");
    expect(headings[1]).toContain("Per informació");
    expect(headings.some((heading) => heading?.includes("Avisos"))).toBe(false);

    const rows = screen.getAllByRole("listitem").map((row) => row.textContent ?? "");
    // Dins dels pendents, el dossier abans que l'oferta.
    expect(rows[0]).toContain("Falten documents obligatoris");
    expect(rows[0]).toContain("Dossier");
    expect(rows[1]).toContain("sense preu ofert");
    expect(rows[1]).toContain("Oferta econòmica");
  });

  it("un pendent resolt no remena els que queden", async () => {
    const view = await mount({}, [
      findingFixture({ domain: "economic", severity: "blocker", code: "economic_lot_empty" }),
      findingFixture({ domain: "dossier", severity: "blocker", code: "mandatory_evidence_missing" }),
      findingFixture({ domain: "technical", severity: "warning", code: "technical_scope_stale" }),
    ]);
    await open();
    const abans = screen.getAllByRole("listitem").map((row) => row.textContent ?? "");
    view.unmount();

    // Cau el del dossier; els altres dos no s'han tocat.
    await mount({}, [
      findingFixture({ domain: "economic", severity: "blocker", code: "economic_lot_empty" }),
      findingFixture({ domain: "technical", severity: "warning", code: "technical_scope_stale" }),
    ]);
    await open();
    const despres = screen.getAllByRole("listitem").map((row) => row.textContent ?? "");
    expect(abans[1]).toBe(despres[0]);
    expect(abans[2]).toBe(despres[1]);
  });

  it("cada problema conegut es llegeix en paraules, amb la gravetat també en text", async () => {
    await mount({}, [findingFixture({ code: "technical_scope_unreviewed", domain: "technical", severity: "blocker" })]);
    await open();
    const row = screen.getByRole("listitem");
    expect(row.textContent).toContain("Ningú no ha revisat l'abast tècnic");
    expect(row.textContent).toContain("Pendent");
    expect(row.textContent).not.toContain("technical_scope_unreviewed");
  });

  it("un codi desconegut es mostra amb el codi cru i la gravetat que diu el servidor", async () => {
    await mount({}, [findingFixture({ code: "economic_something_new", domain: "economic", severity: "blocker" })]);
    expect(screen.getByText("1 pendent de preparació (totes les àrees)")).toBeTruthy();
    await open();
    const row = screen.getByRole("listitem");
    expect(row.textContent).toContain("encara no reconeix aquest estat");
    expect(row.textContent).toContain("Pendent");
    // El codi hi és, plegat al detall tècnic, per a qui l'hagi de reportar.
    expect(row.querySelector("summary")?.textContent).toBe("Detall tècnic");
    expect(row.querySelector("details")?.textContent).toContain("economic_something_new");
  });

  it("no promou ni rebaixa gravetats: un preu unitari per sobre del màxim és un pendent", async () => {
    await mount({}, [findingFixture({ code: "economic_unit_limit_exceeded", domain: "economic", severity: "blocker" })]);
    expect(screen.getByText("1 pendent de preparació (totes les àrees)")).toBeTruthy();
    await open();
    expect(screen.getByRole("listitem").textContent).toContain("Pendent");
  });

  it("cada problema porta a l'àrea on es resol, conservant el pas", async () => {
    await mount({}, [
      findingFixture({ code: "technical_scope_unreviewed", domain: "technical", severity: "blocker" }),
      findingFixture({ code: "economic_lot_empty", domain: "economic", severity: "blocker", entity_id: "lot-9" }),
      findingFixture({ code: "mandatory_tasks_open", domain: "dossier", severity: "blocker" }),
      findingFixture({ code: "decision_not_go", domain: "workspace", severity: "blocker" }),
    ]);
    await open();
    const href = (name: RegExp) => (screen.getByRole("link", { name }) as HTMLAnchorElement).getAttribute("href");
    expect(href(/Obre la solució tècnica/)).toBe("/candidatures/w1?pas=preparar&area=solucio");
    expect(href(/Obre el lot/)).toBe("/candidatures/w1?pas=preparar&area=oferta&entity=lot-9");
    expect(href(/Obre el pla de treball/)).toBe("/candidatures/w1?pas=preparar&area=pla");
    expect(href(/Ves a Decidir/)).toBe("/candidatures/w1?pas=decidir");
  });

  it("els tres fets dels plecs surten de Preparar i porten a la licitació", async () => {
    // Els plecs i l'anàlisi no són àrees de Preparar: viuen a la fitxa de la
    // licitació, que és on es poden mirar, marcar i tornar a executar.
    await mount({}, [
      findingFixture({ code: "tender_documents_unreviewed", domain: "workspace", severity: "info" }),
      findingFixture({ code: "tender_documents_changed", domain: "workspace", severity: "warning" }),
      findingFixture({
        code: "requirement_analysis_predates_documents",
        domain: "technical",
        severity: "warning",
      }),
    ]);
    await open();
    const href = (name: RegExp) =>
      (screen.getAllByRole("link", { name })[0] as HTMLAnchorElement).getAttribute("href");

    expect(href(/Obre els plecs/)).toBe("/licitacions/t1?tab=plecs");
    expect(href(/Obre els requisits/)).toBe("/licitacions/t1?tab=requisits");
  });

  it("un problema sense entitat no s'inventa cap fila a la URL", async () => {
    search = "pas=preparar&area=dossier&entity=vell";
    await mount({}, [findingFixture({ code: "economic_bid_lots_not_selected", domain: "economic", entity_id: null })]);
    await open();
    const href = (screen.getByRole("link", { name: /Tria els lots/ }) as HTMLAnchorElement).getAttribute("href");
    expect(href).toBe("/candidatures/w1?pas=preparar&area=oferta");
    expect(href).not.toContain("entity");
  });

  it("no hi ha cap segona acció de congelar: el calaix només hi porta", async () => {
    await mount({});
    await open();
    const dialog = screen.getByRole("dialog");
    // Congelar viu al pas Tancar. Aquí no hi ha d'haver cap botó que ho faci:
    // dues portes per a la mateixa acció és exactament el que S6 va evitar.
    const actions = [...dialog.querySelectorAll("button")].map((button) => button.textContent ?? "");
    expect(actions.some((label) => /congel/i.test(label))).toBe(false);
    const link = screen.getByRole("link", { name: /Ves a Tancar/ }) as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("/candidatures/w1?pas=tancar");
  });

  it("amb pendents, el peu diu què cal fer i no ofereix cap drecera a Tancar", async () => {
    await mount({}, [findingFixture({ severity: "blocker" })]);
    await open();
    expect(screen.getByRole("dialog").textContent).toContain("Resol els pendents marcats per poder congelar. Els avisos no ho impedeixen.");
    expect(screen.queryByRole("link", { name: /Ves a Tancar/ })).toBeNull();
  });

  it("sense pendents, el peu diu «Cap pendent per congelar.» i porta a Tancar", async () => {
    await mount({}, [findingFixture({ severity: "warning", code: "technical_scope_stale", domain: "technical" })]);
    await open();
    expect(screen.getByRole("dialog").textContent).toContain("Cap pendent per congelar.");
    expect(screen.getByRole("link", { name: /Ves a Tancar/ })).toBeTruthy();
  });

  it("ni la píndola ni el calaix diuen «bloqueig»/«bloquej», amb pendents o sense", async () => {
    const findings = [
      findingFixture({ severity: "blocker" }),
      findingFixture({ severity: "warning", code: "technical_scope_stale", domain: "technical" }),
      findingFixture({ severity: "info", code: "economic_unit_limit_unknown", domain: "economic" }),
    ];
    const view = await mount({}, findings);
    expect(document.body.textContent).not.toMatch(/bloqueig|bloquej/i);
    await open();
    expect(document.body.textContent).not.toMatch(/bloqueig|bloquej/i);
    expect(screen.getByRole("dialog").textContent).toContain("Cal resoldre per congelar");
    view.unmount();

    await mount({}, []);
    await open();
    expect(document.body.textContent).not.toMatch(/bloqueig|bloquej/i);
  });

  it("el recompte de la píndola continua sent el de severity=blocker", async () => {
    await mount({}, [
      findingFixture({ severity: "blocker" }),
      findingFixture({ severity: "blocker", code: "economic_lot_empty", domain: "economic" }),
      findingFixture({ severity: "blocker", code: "mandatory_evidence_missing", domain: "dossier" }),
      findingFixture({ severity: "warning", code: "technical_scope_stale", domain: "technical" }),
    ]);
    expect(screen.getByText("3 pendents de preparació (totes les àrees)")).toBeTruthy();
  });

  it("no afegeix problemes que el servidor no digui", async () => {
    await mount({}, []);
    await open();
    expect(screen.queryAllByRole("listitem")).toHaveLength(0);
    expect(screen.getByRole("dialog").textContent).toContain("Cap pendent, cap avís");
  });
});

describe("Una sola lectura per a tothom", () => {
  beforeEach(() => {
    get.mockReset();
    search = "pas=preparar";
  });

  it("el distintiu i el calaix surten de la mateixa lectura, no de dues portes", async () => {
    await mount({}, [findingFixture({ severity: "blocker", code: "mandatory_evidence_missing" })]);
    expect(screen.getByText("1 pendent de preparació (totes les àrees)")).toBeTruthy();
    await open();
    // Obrir el calaix no torna a preguntar res: és la mateixa dada.
    expect(get).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("listitem").textContent).toContain("Falten documents obligatoris");
  });

  it("qualsevol secció pot invalidar la preparació després d'una mutació seva", async () => {
    // El que faran les àrees d'S9-B i S9-C: demanar l'invalidador i cridar-lo
    // quan el seu desat hagi anat bé.
    function SeccioQueMuta() {
      const invalidate = useInvalidateReadiness();
      return (
        <button type="button" onClick={() => void invalidate()}>
          desa la meva secció
        </button>
      );
    }
    get.mockResolvedValueOnce(
      readinessFixture({ findings: [findingFixture({ severity: "blocker", domain: "economic", code: "economic_lot_empty" })] }),
    );
    get.mockResolvedValueOnce(readinessFixture());
    render(
      <ReadinessProvider companyId="c1" workspaceId="w1">
        <ReadinessControl />
        <SeccioQueMuta />
      </ReadinessProvider>,
    );
    await flush();
    expect(screen.getByText("1 pendent de preparació (totes les àrees)")).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "desa la meva secció" }));
    });
    await flush();
    expect(get).toHaveBeenCalledTimes(2);
    expect(screen.getByText("A punt")).toBeTruthy();
  });

  it("el control no es pot muntar sense el context: val més petar que mentir", () => {
    const silenci = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<ReadinessControl />)).toThrow(/ReadinessProvider/);
    silenci.mockRestore();
  });
});
