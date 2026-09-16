import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DataTable } from "./DataTable";

type Row = { id: string; name: string; amount: string; blocking?: boolean };

const columns = [
  { key: "name", header: "Licitació", cell: (row: Row) => row.name },
  { key: "amount", header: "Import", align: "right" as const, cell: (row: Row) => row.amount },
];
const rows: Row[] = [
  { id: "a", name: "Fibra òptica", amount: "240.000 €", blocking: true },
  { id: "b", name: "Manteniment", amount: "80.000 €" },
];

describe("DataTable", () => {
  it("renderitza capçaleres i cel·les", () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(row) => row.id} />);
    expect(screen.getByRole("columnheader", { name: "Licitació" })).toBeTruthy();
    expect(screen.getByText("Fibra òptica")).toBeTruthy();
    expect(screen.getByText("80.000 €")).toBeTruthy();
  });

  it("tenyeix la fila segons rowTint i respon al clic i a Enter", () => {
    const onRowClick = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        rowTint={(row) => (row.blocking ? "blocking" : undefined)}
        onRowClick={onRowClick}
      />,
    );
    const row = screen.getByText("Fibra òptica").closest("tr")!;
    expect(row.getAttribute("data-tint")).toBe("blocking");
    fireEvent.click(row);
    fireEvent.keyDown(row, { key: "Enter" });
    expect(onRowClick).toHaveBeenCalledTimes(2);
    expect(onRowClick).toHaveBeenCalledWith(rows[0]);
  });

  it("els grups porten rètol i recompte, i els plegats es despleguen", () => {
    render(
      <DataTable
        columns={columns}
        rows={[]}
        rowKey={(row) => row.id}
        groups={[
          { key: "blocking", label: "Bloquegen", rows: [rows[0]], tint: "blocking" },
          { key: "ok", label: "Acreditats", rows: [rows[1]], collapsed: true },
        ]}
        footer={<span>2 requisits</span>}
      />,
    );
    expect(screen.getByText("Bloquegen")).toBeTruthy();
    expect(screen.getByText("Fibra òptica")).toBeTruthy();
    expect(screen.queryByText("Manteniment")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Acreditats/ }));
    expect(screen.getByText("Manteniment")).toBeTruthy();
    expect(screen.getByText("2 requisits")).toBeTruthy();
  });

  it("sota el punt de tall cada cel·la porta el rètol de la columna", () => {
    render(<DataTable columns={columns} rows={[rows[1]]} rowKey={(row) => row.id} stackBelow="md" />);
    const cell = screen.getByText("80.000 €").closest("td")!;
    expect(cell.textContent).toContain("Import");
  });
});
