"use client";

import { Menu, Search, X } from "lucide-react";
import { useState, type ReactNode } from "react";

import { cn } from "@/components/ui";

interface AppTopbarProps {
  mobileOpen: boolean;
  onToggleMenu: () => void;
  companySwitcher: ReactNode;
  search?: ReactNode;
  workCenter?: ReactNode;
  user?: ReactNode;
}

/**
 * Capçalera de 56 px (handoff L4): empresa activa sempre visible, cerca,
 * centre de feina i usuari. Sota 1024 px porta el botó del menú (drawer);
 * sota 768 px la cerca s'amaga darrere d'una icona.
 */
export function AppTopbar({ mobileOpen, onToggleMenu, companySwitcher, search, workCenter, user }: AppTopbarProps) {
  const [searchOpen, setSearchOpen] = useState(false);
  return (
    <header className="relative sticky top-0 z-20 shrink-0 border-b border-border bg-canvas">
      <div className="flex h-topbar items-center gap-3 px-4 lg:px-6">
        <button
          type="button"
          onClick={onToggleMenu}
          className="flex h-9 w-9 shrink-0 items-center justify-center text-muted transition-colors duration-150 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand lg:hidden"
          aria-label={mobileOpen ? "Tanca el menú" : "Obre el menú"}
          aria-expanded={mobileOpen}
          aria-controls="app-sidebar"
        >
          {mobileOpen ? <X size={20} strokeWidth={1.5} aria-hidden /> : <Menu size={20} strokeWidth={1.5} aria-hidden />}
        </button>
        {companySwitcher}
        {search && (
          <div
            className={cn(
              "min-w-0 flex-1 md:flex md:max-w-[420px]",
              searchOpen ? "absolute inset-x-0 top-full flex border-b border-border bg-canvas px-4 py-2 md:static md:border-0 md:p-0" : "hidden",
            )}
          >
            {search}
          </div>
        )}
        {search && (
          <button
            type="button"
            onClick={() => setSearchOpen((open) => !open)}
            className="flex h-9 w-9 shrink-0 items-center justify-center text-muted transition-colors duration-150 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand md:hidden"
            aria-label={searchOpen ? "Tanca la cerca" : "Cerca"}
            aria-expanded={searchOpen}
          >
            <Search size={18} strokeWidth={1.5} aria-hidden />
          </button>
        )}
        <div className="ml-auto flex shrink-0 items-center gap-3">
          {workCenter}
          {user}
        </div>
      </div>
    </header>
  );
}
