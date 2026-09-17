import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { workspaceFixture } from "@/test/workspace-fixture";

const post = vi.fn();
const patch = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, post: (...args: unknown[]) => post(...args), patch: (...args: unknown[]) => patch(...args) } };
});

import { ChecklistSection, NotesSection, TasksSection } from "./WorkspaceWork";

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const onChange = vi.fn(async () => undefined);
const members = [
  { id: "u1", email: "anna@example.org", full_name: "Anna Puig" },
  { id: "u2", email: "pere@example.org", full_name: null },
];

describe("Checklist · Tasques · Notes (restil sense canviar la lògica)", () => {
  beforeEach(() => {
    post.mockReset();
    patch.mockReset();
    onChange.mockClear();
  });

  it("checklist: pastilles d'estat reals, reconciliar fa el POST de sempre i explica el resultat", async () => {
    post.mockResolvedValue({ created: 2, carried: 1, historic: 1, total: 6, reason: null });
    patch.mockResolvedValue({});
    const workspace = workspaceFixture({
      members,
      checklist: [
        { id: "c1", workspace_id: "w1", requirement_id: "r1", title: "Aportar comptes anuals", category: "economic_solvency", mandatory: true, status: "blocked", source: "requirement", assigned_to: "u1", assigned_to_name: "Anna Puig", due_at: null, note: null, completed_at: null, created_at: "2026-09-10T09:00:00Z" },
        { id: "c2", workspace_id: "w1", requirement_id: null, title: "Preparar annex 3", category: "other", mandatory: false, status: "completed", source: "manual", assigned_to: null, assigned_to_name: null, due_at: null, note: null, completed_at: "2026-09-11T09:00:00Z", created_at: "2026-09-10T09:00:00Z" },
      ],
    });
    const { container } = render(<ChecklistSection workspace={workspace} onChange={onChange} />);
    const blocked = screen.getByText("Aportar comptes anuals").closest("tr")!;
    expect([...blocked.querySelectorAll("[data-level]")].map((p) => p.getAttribute("data-level"))).toContain("blocking");
    expect(blocked.textContent).toContain("Obligatori");
    expect([...blocked.querySelectorAll("a")].some((a) => a.getAttribute("href") === "/licitacions/t1?tab=requisits")).toBe(true);
    expect(screen.getByText("Preparar annex 3").closest("tr")!.textContent).toContain("Manual");
    expect(container.textContent).toContain("1 de 2 completats");

    fireEvent.change(blocked.querySelectorAll("select")[0], { target: { value: "in_progress" } });
    await flush();
    expect(patch).toHaveBeenCalledWith("/companies/c1/workspaces/w1/checklist/c1", { status: "in_progress" });
    fireEvent.change(blocked.querySelectorAll("select")[1], { target: { value: "" } });
    await flush();
    expect(patch).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/checklist/c1", { assigned_to: null });

    fireEvent.click(screen.getByRole("button", { name: /Reconcilia amb l'anàlisi/ }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/checklist/regenerate", {});
    expect(container.textContent).toContain("2 items nous · 1 item conservat.");
    expect(container.textContent).toContain("1 item queda a l'històric");
    expect(onChange).toHaveBeenCalled();

    fireEvent.change(screen.getByPlaceholderText("Preparar annex 3"), { target: { value: "Signar la declaració" } });
    fireEvent.click(screen.getByRole("button", { name: "Afegeix" }));
    await flush();
    expect(post).toHaveBeenLastCalledWith("/companies/c1/workspaces/w1/checklist", { title: "Signar la declaració" });
  });

  it("checklist buida: sortida cap a la reconciliació", () => {
    render(<ChecklistSection workspace={workspaceFixture({ members })} onChange={onChange} />);
    expect(screen.getByText("Encara no hi ha cap item")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /Reconcilia amb l'anàlisi/ }).length).toBeGreaterThan(0);
  });

  it("tasques: només les persones de l'empresa, creació amb el payload de sempre, pastilla d'estat", async () => {
    post.mockResolvedValue({});
    patch.mockResolvedValue({});
    const workspace = workspaceFixture({
      members,
      tasks: [
        { id: "t1", workspace_id: "w1", title: "Demana la renovació", description: null, assigned_to: "u2", mandatory: true, assigned_to_name: null, due_at: "2026-09-18T10:00:00Z", status: "in_progress", created_by: null, created_at: "2026-09-10T09:00:00Z", completed_at: null },
      ],
    });
    const { container } = render(<TasksSection workspace={workspace} onChange={onChange} />);
    expect(container.id || container.querySelector("#tasques")).toBeTruthy();
    const row = screen.getByText("Demana la renovació").closest("tr")!;
    expect([...row.querySelectorAll("[data-level]")].map((p) => `${p.textContent?.trim()}[${p.getAttribute("data-level")}]`)).toContain("En curs[running]");
    const assignee = row.querySelectorAll("select")[1];
    expect([...assignee.options].map((o) => o.textContent)).toEqual(["Sense assignar", "Anna Puig", "pere@example.org"]);
    fireEvent.change(row.querySelectorAll("select")[0], { target: { value: "completed" } });
    await flush();
    expect(patch).toHaveBeenCalledWith("/companies/c1/workspaces/w1/tasks/t1", { status: "completed" });

    fireEvent.change(screen.getByPlaceholderText("Demanar certificat bancari"), { target: { value: "Demanar el certificat de la TGSS" } });
    fireEvent.click(screen.getByRole("button", { name: "Crea" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/tasks", { title: "Demanar el certificat de la TGSS", assigned_to: null, due_at: null });
    expect(onChange).toHaveBeenCalled();
  });

  it("notes: es desen amb el POST de sempre i surten signades i datades", async () => {
    post.mockResolvedValue({});
    const workspace = workspaceFixture({
      notes: [{ id: "n1", workspace_id: "w1", author_id: "u1", author_name: "Anna Puig", text: "Parlat amb el responsable.", created_at: "2026-09-12T09:00:00Z" }],
    });
    const { container } = render(<NotesSection workspace={workspace} onChange={onChange} />);
    expect(container.querySelector("[data-kind='human']")).not.toBeNull();
    expect(container.textContent).toContain("Anna Puig");
    fireEvent.change(container.querySelector("textarea")!, { target: { value: "  Nova nota  " } });
    fireEvent.click(screen.getByRole("button", { name: "Afegeix la nota" }));
    await flush();
    expect(post).toHaveBeenCalledWith("/companies/c1/workspaces/w1/notes", { text: "Nova nota" });
  });
});
