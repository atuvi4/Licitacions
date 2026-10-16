"use client";

/**
 * Un full de càlcul extret, com a taula (P2-07): una fila per cel·la amb el valor tal
 * com és desat. Només un decimal amb un error evident de coma flotant es mostra net
 * («38.326,08»), i al costat sempre el valor sense format, intacte. Res més es formata:
 * ni enters, ni codis, ni dates, ni textos.
 */

import { floatArtifactClean, formatCleanNumber, parseSheetPage } from "@/lib/sheetText";

function CellValue({ value }: { value: string }) {
  const clean = floatArtifactClean(value);
  if (!clean) return <span className="break-words">{value}</span>;
  return (
    <span className="flex flex-col">
      <span className="tabular-nums">{formatCleanNumber(clean)}</span>
      <span className="font-mono text-[11px] text-muted" data-raw-value>
        sense format: {value}
      </span>
    </span>
  );
}

export function SheetPageView({ text }: { text: string }) {
  const page = parseSheetPage(text);
  if (page.rows.length === 0 && page.notes.length === 0) {
    return <p className="px-5 py-4 text-[13px] text-muted">(full sense text extret)</p>;
  }
  return (
    <div className="max-h-[60vh] overflow-auto px-5 py-4">
      {page.notes.length > 0 && (
        <ul className="mb-3 space-y-0.5 font-mono text-[12px] text-muted">
          {page.notes.map((note, index) => (
            <li key={index}>{note}</li>
          ))}
        </ul>
      )}
      {page.rows.length > 0 && (
        <table className="w-full border-collapse text-[13px]">
          <caption className="sr-only">Cel·les del full{page.name ? ` ${page.name}` : ""}</caption>
          <thead>
            <tr className="border-b border-border text-left text-caption text-muted">
              <th scope="col" className="py-1.5 pr-3 font-semibold">Fila</th>
              <th scope="col" className="py-1.5 pr-3 font-semibold">Cel·la</th>
              <th scope="col" className="py-1.5 pr-3 font-semibold">Fórmula</th>
              <th scope="col" className="py-1.5 font-semibold">Valor</th>
            </tr>
          </thead>
          <tbody>
            {page.rows.flatMap((row) =>
              row.cells.map((cell, index) => (
                <tr key={`${row.row}-${cell.ref}`} className="border-b border-border align-top">
                  <td className="py-1.5 pr-3 text-muted tabular-nums">{index === 0 ? row.row : ""}</td>
                  <td className="py-1.5 pr-3 font-mono text-[12px] whitespace-nowrap">
                    {cell.ref}
                    {cell.range && <span className="text-muted"> [{cell.range}]</span>}
                  </td>
                  <td className="py-1.5 pr-3 font-mono text-[12px] break-all text-muted">{cell.formula ? `=${cell.formula}` : ""}</td>
                  <td className="py-1.5">
                    {cell.value === null ? <span className="text-muted">(sense valor desat)</span> : <CellValue value={cell.value} />}
                  </td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
