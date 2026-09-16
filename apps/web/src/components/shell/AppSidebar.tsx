import type { ReactNode } from "react";

import { cn } from "@/components/ui";

interface AppSidebarProps {
  mobileOpen: boolean;
  children: ReactNode;
  footer: ReactNode;
}

/** Barra lateral: 232 px en accent-900, marca «LI» i «Licitacions», navegació i peu (handoff L4, mockup 01). */
export function AppSidebar({ mobileOpen, children, footer }: AppSidebarProps) {
  return (
    <aside
      id="app-sidebar"
      className={cn(
        "fixed inset-y-0 left-0 z-40 flex w-sidebar shrink-0 flex-col bg-sidebar text-sidebar-ink transition-transform duration-150 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
        mobileOpen ? "translate-x-0" : "-translate-x-full",
      )}
    >
      <div className="flex h-topbar shrink-0 items-center gap-2.5 px-5">
        <span
          aria-hidden
          className="grid h-7 w-7 shrink-0 place-items-center bg-brand font-heading text-[13px] font-bold text-white"
        >
          LI
        </span>
        <p className="font-heading text-[17px] font-semibold text-white">Licitacions</p>
      </div>
      {children}
      <div className="shrink-0">{footer}</div>
    </aside>
  );
}
