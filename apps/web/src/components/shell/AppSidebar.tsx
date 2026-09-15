import type { ReactNode } from "react";

import { cn } from "@/components/ui";

interface AppSidebarProps {
  mobileOpen: boolean;
  children: ReactNode;
  footer: ReactNode;
}

export function AppSidebar({ mobileOpen, children, footer }: AppSidebarProps) {
  return (
    <aside
      id="app-sidebar"
      className={cn(
        "fixed inset-y-0 left-0 z-40 flex w-sidebar shrink-0 flex-col bg-sidebar text-sidebar-ink transition-transform duration-150 lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
        mobileOpen ? "translate-x-0" : "-translate-x-full",
      )}
    >
      <div className="flex h-topbar shrink-0 items-center gap-3 border-b border-white/10 px-6">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-control bg-brand text-caption font-bold tracking-wide text-white" aria-hidden="true">
          LI
        </span>
        <p className="text-section font-semibold tracking-tight text-white">Licitacions</p>
      </div>
      {children}
      <div className="shrink-0">{footer}</div>
    </aside>
  );
}
