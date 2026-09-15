import Link from "next/link";

import { cn } from "@/components/ui";
import { isNavigationActive, NAVIGATION_GROUPS } from "./navigation";

interface SidebarNavigationProps {
  pathname: string;
  onNavigate: () => void;
}

export function SidebarNavigation({ pathname, onNavigate }: SidebarNavigationProps) {
  return (
    <nav aria-label="Navegació principal" className="scroll-thin min-h-0 flex-1 overflow-y-auto px-3 py-5">
      <div className="space-y-6">
        {NAVIGATION_GROUPS.map((group) => (
          <div key={group.label}>
            <p className="px-3 pb-2 text-[11px] font-semibold uppercase tracking-widest text-sidebar-muted">
              {group.label}
            </p>
            <ul className="space-y-1">
              {group.items.map((item) => {
                const active = isNavigationActive(pathname, item.href);
                return (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex min-h-9 items-center gap-3 rounded-control border px-3 py-1.5 text-[13px] leading-5 transition-colors duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white",
                        active
                          ? "border-white/10 bg-sidebar-hover font-semibold text-white"
                          : "border-transparent text-sidebar-ink hover:bg-sidebar-hover hover:text-white",
                      )}
                    >
                      {item.icon}
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}
