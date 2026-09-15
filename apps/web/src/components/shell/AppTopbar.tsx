import type { ReactNode } from "react";

interface AppTopbarProps {
  mobileOpen: boolean;
  onToggleMenu: () => void;
  companySwitcher: ReactNode;
  workCenter?: ReactNode;
}

export function AppTopbar({ mobileOpen, onToggleMenu, companySwitcher, workCenter }: AppTopbarProps) {
  return (
    <header className="sticky top-0 z-20 flex h-topbar shrink-0 items-center gap-3 border-b border-border bg-surface px-4 lg:px-8">
      <button
        type="button"
        onClick={onToggleMenu}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control text-muted transition-colors duration-150 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand lg:hidden"
        aria-label="Obre el menú"
        aria-expanded={mobileOpen}
        aria-controls="app-sidebar"
      >
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-5 w-5" aria-hidden="true">
          <path d="M4 6h12M4 10h12M4 14h12" strokeLinecap="round" />
        </svg>
      </button>
      {companySwitcher}
      {/* Espai d'integració del futur Work Center; el shell no consulta ni deriva estats de jobs. */}
      <div className="ml-auto flex shrink-0 items-center justify-end lg:min-w-40">{workCenter}</div>
    </header>
  );
}
