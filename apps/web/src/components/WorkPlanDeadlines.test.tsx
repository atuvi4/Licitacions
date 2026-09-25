import { act, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Requirement } from "@/lib/requirements";
import { analysisFixture, requirementFixture } from "@/test/technical-fixture";
import { workspaceFixture } from "@/test/workspace-fixture";

let requirements: Requirement[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  const get = async (path: string) => {
    if (path.includes("/analysis/requirements")) return analysisFixture(requirements);
    throw new Error(`GET inesperat: ${path}`);
  };
  return { ...actual, api: { ...actual.api, get } };
});

import { WorkPlanDeadlines } from "./WorkPlanDeadlines";

async function mount() {
  render(<WorkPlanDeadlines workspace={workspaceFixture({ tender_lot_id: null })} />);
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  requirements = [];
});

describe("Pla de treball · terminis del plec", () => {
  it("mostra només els que el servidor envia al Pla de treball", async () => {
    requirements = [
      requirementFixture({ id: "d1", title: "Lliurament del Pla d'implantació", category: "deadline", routing: "work_plan" }),
      requirementFixture({ id: "a1", title: "Declaració responsable DEUC", category: "administrative_documentation", routing: "compliance_dossier" }),
      requirementFixture({ id: "t1", title: "SLA de l'Annex V" }),
    ];
    await mount();

    const list = screen.getByRole("list", { name: "Terminis del plec" });
    expect(within(list).getByText("Lliurament del Pla d'implantació")).toBeTruthy();
    expect(within(list).queryByText("Declaració responsable DEUC")).toBeNull();
    expect(within(list).queryByText("SLA de l'Annex V")).toBeNull();
    expect(within(list).queryByRole("button")).toBeNull();
  });

  it("sense terminis no pinta res", async () => {
    requirements = [requirementFixture({ id: "t1" })];
    await mount();

    expect(screen.queryByRole("list", { name: "Terminis del plec" })).toBeNull();
  });
});
