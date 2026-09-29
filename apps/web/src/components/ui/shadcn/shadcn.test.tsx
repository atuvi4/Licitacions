import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import { Button } from "../Button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
  type ColumnDef,
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
  SortableTable,
} from "./index";

beforeAll(() => {
  // jsdom no té ResizeObserver; el posicionament del menú de Radix el fa servir.
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

describe("Dialog", () => {
  it("s'obre amb nom accessible, es tanca amb Escape i torna el focus al botó", async () => {
    render(
      <Dialog>
        <DialogTrigger asChild>
          <Button>Obre</Button>
        </DialogTrigger>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Detall de l&apos;empresa</DialogTitle>
            <DialogDescription>Dades de l&apos;alta.</DialogDescription>
          </DialogHeader>
          <DialogBody>Contingut</DialogBody>
        </DialogContent>
      </Dialog>,
    );
    const trigger = screen.getByRole("button", { name: "Obre" });
    // Un clic real hi posa el focus; fireEvent no. Radix el torna on era.
    trigger.focus();
    fireEvent.click(trigger);
    const dialog = await screen.findByRole("dialog", { name: "Detall de l'empresa" });
    expect(dialog.getAttribute("aria-describedby")).toBeTruthy();
    // Aspecte Tuvi: la mateixa superfície que el calaix.
    expect(dialog.className).toMatch(/rounded-card/);
    expect(dialog.className).toMatch(/bg-surface/);
    expect(within(dialog).getByRole("button", { name: "Tanca" })).toBeTruthy();

    fireEvent.keyDown(dialog, { key: "Escape" });
    // Radix retorna el focus en un torn posterior.
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
});

describe("AlertDialog", () => {
  function Confirm({ onConfirm }: { onConfirm: () => void }) {
    return (
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button>Retira el rol</Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Retirar l&apos;administració?</AlertDialogTitle>
            <AlertDialogDescription>Deixarà de poder donar d&apos;alta empreses.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel />
            <AlertDialogAction onClick={onConfirm}>Retira</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    );
  }

  it("cal triar: el focus va a «Cancel·la» i cancel·lar no fa res", async () => {
    const onConfirm = vi.fn();
    render(<Confirm onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: "Retira el rol" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Retirar l'administració?" });
    const cancel = within(dialog).getByRole("button", { name: "Cancel·la" });
    expect(document.activeElement).toBe(cancel);
    // Els botons són els Button Tuvi.
    expect(within(dialog).getByRole("button", { name: "Retira" }).getAttribute("data-variant")).toBe("primary");
    expect(cancel.getAttribute("data-variant")).toBe("secondary");

    fireEvent.click(cancel);
    await act(async () => {});
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("confirmar crida l'acció una vegada i tanca", async () => {
    const onConfirm = vi.fn();
    render(<Confirm onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole("button", { name: "Retira el rol" }));
    fireEvent.click(await screen.findByRole("button", { name: "Retira" }));
    await act(async () => {});
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });
});

describe("DropdownMenu", () => {
  it("s'obre amb el teclat, dona rols de menú i executa l'acció triada", async () => {
    const onSelect = vi.fn();
    render(
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm">Accions</Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>Usuari</DropdownMenuLabel>
          <DropdownMenuItem onSelect={onSelect}>Reenvia l&apos;accés</DropdownMenuItem>
          <DropdownMenuItem destructive>Desactiva</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>,
    );
    const trigger = screen.getByRole("button", { name: "Accions" });
    fireEvent.keyDown(trigger, { key: "Enter" });
    const menu = await screen.findByRole("menu");
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((item) => item.textContent)).toEqual(["Reenvia l'accés", "Desactiva"]);
    expect(items[1].getAttribute("data-destructive")).toBe("true");
    expect(menu.className).toMatch(/bg-surface/);

    fireEvent.click(items[0]);
    await act(async () => {});
    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });
});

interface Company {
  id: string;
  name: string;
  users: number;
}

const COLUMNS: Array<ColumnDef<Company, unknown>> = [
  { accessorKey: "name", header: "Empresa" },
  { accessorKey: "users", header: "Usuaris" },
  { id: "actions", header: "Accions", enableSorting: false, cell: () => "…" },
];
const DATA: Company[] = [
  { id: "c1", name: "Tuvi QA Review", users: 3 },
  { id: "c2", name: "Indaleccius SL", users: 1 },
  { id: "c3", name: "Demo Telecom", users: 2 },
];
const names = () =>
  screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[0].textContent);

describe("SortableTable", () => {
  it("ordena per columna, ho diu amb aria-sort i no ofereix ordenar el que no toca", () => {
    render(<SortableTable label="Empreses" columns={COLUMNS} data={DATA} getRowId={(row) => row.id} />);
    const table = screen.getByRole("table", { name: "Empreses" });
    expect(names()).toEqual(["Tuvi QA Review", "Indaleccius SL", "Demo Telecom"]);

    const header = within(table).getByRole("columnheader", { name: /Empresa/ });
    fireEvent.click(within(header).getByRole("button"));
    expect(header.getAttribute("aria-sort")).toBe("ascending");
    expect(names()).toEqual(["Demo Telecom", "Indaleccius SL", "Tuvi QA Review"]);
    fireEvent.click(within(header).getByRole("button"));
    expect(header.getAttribute("aria-sort")).toBe("descending");
    expect(names()).toEqual(["Tuvi QA Review", "Indaleccius SL", "Demo Telecom"]);

    const actions = within(table).getByRole("columnheader", { name: "Accions" });
    expect(within(actions).queryByRole("button")).toBeNull();
    expect(actions.getAttribute("aria-sort")).toBeNull();
  });

  it("la cerca filtra i compta els resultats; sense res, ho diu", () => {
    render(<SortableTable label="Empreses" columns={COLUMNS} data={DATA} searchPlaceholder="Cerca empreses" />);
    fireEvent.change(screen.getByRole("searchbox", { name: "Cerca empreses" }), { target: { value: "indal" } });
    expect(names()).toEqual(["Indaleccius SL"]);
    expect(screen.getByText("1 resultat")).toBeTruthy();
    fireEvent.change(screen.getByRole("searchbox", { name: "Cerca empreses" }), { target: { value: "cap" } });
    expect(screen.getByText("Cap resultat.")).toBeTruthy();
    expect(screen.getByText("0 resultats")).toBeTruthy();
  });

  it("pagina quan cal", () => {
    render(<SortableTable label="Empreses" columns={COLUMNS} data={DATA} pageSize={2} />);
    expect(screen.getByText("Pàgina 1 de 2")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Anterior" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Següent" }));
    expect(screen.getByText("Pàgina 2 de 2")).toBeTruthy();
    expect(names()).toEqual(["Demo Telecom"]);
  });
});

describe("design system", () => {
  it("les peces shadcn només fan servir tokens Tuvi, no els del tema de shadcn", () => {
    const dir = join(process.cwd(), "src/components/ui/shadcn");
    const shadcnTokens = /\b(?:bg|text|border|ring)-(?:background|foreground|primary|secondary|accent|popover|card|destructive|input|ring)\b/;
    for (const file of readdirSync(dir).filter((name) => name.endsWith(".tsx") && !name.endsWith(".test.tsx"))) {
      expect(readFileSync(join(dir, file), "utf8"), file).not.toMatch(shadcnTokens);
    }
  });
});
