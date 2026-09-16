import { cn } from "./cn";

const CORNERS = ["tl", "tr", "bl", "br"] as const;

/**
 * Les quatre creus «+» de les cantonades d'un objecte «blueprint» (handoff §1.4).
 *
 * Només les porten el botó primari i les targetes de decisió, d'aprovació i de
 * benvinguda. El contenidor ha de ser `position: relative`. Són decoratives:
 * `aria-hidden`, i `data-mark` perquè es puguin comptar a les proves.
 */
export function RegistrationMarks({
  size = "sm",
  tone = "inverse",
}: {
  /** `sm` = 7 px (botó); `md` = 9 px (targeta). */
  size?: "sm" | "md";
  /** `inverse` = blanc sobre el primari; `accent` = accent-700 sobre la targeta. */
  tone?: "inverse" | "accent";
}) {
  const box = size === "sm" ? "h-[7px] w-[7px]" : "h-[9px] w-[9px]";
  const offset = size === "sm" ? "-4px" : "-5px";
  const color = tone === "inverse" ? "text-white" : "text-brand-strong";
  return (
    <>
      {CORNERS.map((corner) => (
        <i
          key={corner}
          aria-hidden
          data-mark={corner}
          style={{
            left: corner.endsWith("l") ? offset : undefined,
            right: corner.endsWith("r") ? offset : undefined,
            top: corner.startsWith("t") ? offset : undefined,
            bottom: corner.startsWith("b") ? offset : undefined,
          }}
          className={cn(
            "pointer-events-none absolute",
            box,
            color,
            "before:absolute before:top-0 before:left-1/2 before:h-full before:w-px before:-translate-x-1/2 before:bg-current before:content-['']",
            "after:absolute after:top-1/2 after:left-0 after:h-px after:w-full after:-translate-y-1/2 after:bg-current after:content-['']",
          )}
        />
      ))}
    </>
  );
}
