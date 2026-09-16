import { cn } from "./cn";
import { Skeleton } from "./Feedback";

export type PageSkeletonVariant = "table" | "list" | "detail" | "decision";

export interface PageSkeletonProps {
  variant: PageSkeletonVariant;
  /** Títol real de la pàgina, si ja se sap. */
  title?: string;
  /** Línia de context, p. ex. «carregant les licitacions…». */
  context?: string;
  /** Columnes reals de la taula (variant `table`). */
  columns?: string[];
  className?: string;
}

const KICKER = "font-heading text-kicker font-semibold tracking-[0.1em] text-muted uppercase";

function GhostRows({ rows, columns }: { rows: number; columns: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, row) => (
        <tr key={row} className="border-b border-border last:border-b-0">
          {Array.from({ length: columns }).map((__, column) => (
            <td key={column} className="px-3 py-3 align-top">
              <Skeleton className={cn("h-3.5", column === 0 ? "w-4/5" : "w-3/5")} />
              {column === 0 && <Skeleton className="mt-1.5 h-2.5 w-1/2" />}
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

function Panel({ rows, header = true }: { rows: number; header?: boolean }) {
  return (
    <div className="border border-border">
      {header && (
        <div className="flex items-center justify-between border-b border-border bg-subtle px-3.5 py-2.5">
          <Skeleton className="h-4 w-40" />
          <Skeleton className="h-4 w-8" />
        </div>
      )}
      {Array.from({ length: rows }).map((_, row) => (
        <div key={row} className="flex items-center gap-4 border-b border-border px-3.5 py-3 last:border-b-0">
          <div className="flex-1">
            <Skeleton className="h-3.5 w-3/4" />
            <Skeleton className="mt-1.5 h-2.5 w-2/5" />
          </div>
          <Skeleton className="h-5 w-20" />
        </div>
      ))}
    </div>
  );
}

/**
 * Amb la forma de la pàgina que arriba (handoff §2.10): pinta el títol, el
 * context i les columnes reals de seguida; només la dada és fantasma. Mai un
 * spinner a pantalla sencera ni un recompte provisional.
 */
export function PageSkeleton({ variant, title, context, columns, className }: PageSkeletonProps) {
  return (
    <div className={cn("flex flex-col gap-5", className)} aria-busy="true">
      <header>
        {title ? (
          <h1 className="font-heading text-page font-semibold text-ink">{title}</h1>
        ) : (
          <Skeleton className="h-7 w-56" />
        )}
        {context ? (
          <p className="mt-1.5 text-body text-muted">{context}</p>
        ) : (
          <Skeleton className="mt-2 h-3.5 w-80" />
        )}
      </header>

      {variant === "table" && (
        <div className="border border-border">
          <table className="w-full border-collapse text-body">
            <thead>
              <tr>
                {(columns && columns.length > 0 ? columns : ["", "", "", ""]).map((column, index) => (
                  <th key={`${column}-${index}`} className={cn("border-b border-border-strong bg-subtle px-3 py-2 text-left", KICKER)}>
                    {column || <Skeleton className="h-2.5 w-16" />}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <GhostRows rows={5} columns={columns && columns.length > 0 ? columns.length : 4} />
            </tbody>
          </table>
        </div>
      )}

      {variant === "list" && (
        <div className="flex flex-col gap-5">
          <Panel rows={3} />
          <Panel rows={2} />
          <Panel rows={3} />
        </div>
      )}

      {variant === "detail" && (
        <div className="flex flex-col gap-5">
          <div className="flex gap-6 border-b border-border pb-3">
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-24" />
            <Skeleton className="h-4 w-24" />
          </div>
          <div className="grid gap-5 lg:grid-cols-2">
            <Panel rows={4} />
            <Panel rows={4} />
          </div>
        </div>
      )}

      {variant === "decision" && (
        <div className="grid gap-5 lg:grid-cols-12">
          <div className="lg:col-span-7">
            <div className="border border-border-strong p-5">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="mt-3 h-6 w-3/4" />
              <div className="mt-5 flex gap-3">
                <Skeleton className="h-12 flex-1" />
                <Skeleton className="h-12 flex-1" />
              </div>
            </div>
          </div>
          <div className="lg:col-span-5">
            <Panel rows={5} />
          </div>
        </div>
      )}
    </div>
  );
}
