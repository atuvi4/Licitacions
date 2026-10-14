import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { AppTopbar } from "./AppTopbar";

/**
 * Capçalera (auditoria UI P1-03): la cerca no pot quedar a 0 px entre 768 i 1100 px.
 * jsdom no fa layout; la mesura real es fa amb Chrome headless. Aquí es fixen les
 * classes i el comportament que ho garanteixen.
 */
function mount() {
  return render(
    <AppTopbar
      mobileOpen={false}
      onToggleMenu={() => undefined}
      companySwitcher={<div>Indaleccius Broadcasting S.L.</div>}
      search={<input type="search" aria-label="Cerca" />}
      workCenter={<span>Cap tasca en curs</span>}
      user={<span>QA</span>}
    />,
  );
}

describe("AppTopbar · espai de la cerca", () => {
  it("el contenidor de la cerca té una amplada mínima des de md, i un màxim", () => {
    mount();
    const wrapper = screen.getByRole("searchbox").parentElement!;
    expect(wrapper.className).toMatch(/md:min-w-\[12rem\]/);
    expect(wrapper.className).toMatch(/md:max-w-\[420px\]/);
    expect(wrapper.className).toMatch(/flex-1/);
  });

  it("en mòbil la cerca s'obre i es tanca amb el botó, a tota l'amplada", () => {
    mount();
    const wrapper = screen.getByRole("searchbox").parentElement!;
    expect(wrapper.className).toMatch(/(^|\s)hidden(\s|$)/);
    fireEvent.click(screen.getByRole("button", { name: "Cerca" }));
    expect(wrapper.className).toMatch(/absolute inset-x-0/);
    fireEvent.click(screen.getByRole("button", { name: "Tanca la cerca" }));
    expect(wrapper.className).toMatch(/(^|\s)hidden(\s|$)/);
  });
});
