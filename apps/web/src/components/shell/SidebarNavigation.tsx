"use client";

import { Building } from "lucide-react";
import Link from "next/link";

import { cn } from "@/components/ui";

import { NAVIGATION_GROUPS, PROFILE_SUBNAV, isNavigationActive, isSubnavActive, type NavCountKey } from "./navigation";

interface SidebarNavigationProps {
  pathname: string;
  onNavigate: () => void;
  /** Recomptes ja calculats; els absents no es pinten, i un 0 tampoc: només feina pendent. */
  counts?: Partial<Record<NavCountKey, number | string>>;
  /** Administració de la plataforma: afegeix l'alta d'empreses. */
  platformAdmin?: boolean;
}

const PLATFORM_ONBOARDING = "/plataforma/empreses/nova";

export function SidebarNavigation({ pathname, onNavigate, counts = {}, platformAdmin = false }: SidebarNavigationProps) {
  return (
    <nav aria-label="Navegació principal" className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3.5 pt-2 pb-4">
      {NAVIGATION_GROUPS.map((group, index) => (
        <div key={group.label} className={cn(index > 0 && "mt-5")}>
          <p className="mb-1.5 px-2.5 font-heading text-[10.5px] font-semibold tracking-[0.14em] text-sidebar-muted uppercase">
            {group.label}
          </p>
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const active = isNavigationActive(pathname, item.href);
              const count = counts[item.countKey];
              const showCount = count !== undefined && count !== 0;
              const Icon = item.icon;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex min-h-9 items-center gap-2.5 px-2.5 py-2 text-[14px] leading-5 transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white",
                      active
                        ? "bg-sidebar-hover font-semibold text-white"
                        : "text-sidebar-ink/80 hover:bg-white/8 hover:text-white",
                    )}
                  >
                    <Icon size={16} strokeWidth={1.5} aria-hidden className="shrink-0" />
                    <span className="min-w-0 flex-1 truncate">{item.label}</span>
                    {showCount && (
                      <span className={cn("text-[12px] tabular-nums", active ? "text-white" : "text-sidebar-ink/70")}>
                        {count}
                      </span>
                    )}
                  </Link>
                  {item.href === "/empresa" && active && (
                    <ul className="mt-0.5 mb-1 ml-[26px] space-y-0.5 border-l border-white/12 pl-2.5">
                      {PROFILE_SUBNAV.map((sub) => {
                        const subActive = isSubnavActive(pathname, sub.href);
                        return (
                          <li key={sub.href}>
                            <Link
                              href={sub.href}
                              onClick={onNavigate}
                              aria-current={subActive ? "page" : undefined}
                              className={cn(
                                "flex min-h-7 items-center px-2 text-[13px] leading-5 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white",
                                subActive ? "font-semibold text-white" : "text-sidebar-ink/70 hover:text-white",
                              )}
                            >
                              {sub.label}
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      ))}
      {platformAdmin && (
        <div className="mt-5">
          <p className="mb-1.5 px-2.5 font-heading text-[10.5px] font-semibold tracking-[0.14em] text-sidebar-muted uppercase">
            Plataforma
          </p>
          <ul className="space-y-0.5">
            <li>
              <Link
                href={PLATFORM_ONBOARDING}
                onClick={onNavigate}
                className="flex min-h-9 items-center gap-2.5 px-2.5 py-2 text-[14px] leading-5 text-sidebar-ink/80 transition-colors duration-150 hover:bg-white/8 hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
              >
                <Building size={16} strokeWidth={1.5} aria-hidden className="shrink-0" />
                <span className="min-w-0 flex-1 truncate">Alta d&apos;empresa</span>
              </Link>
            </li>
          </ul>
        </div>
      )}
    </nav>
  );
}
