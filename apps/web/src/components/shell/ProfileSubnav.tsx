"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/components/ui";

import { PROFILE_SUBNAV, isSubnavActive } from "./navigation";

/** Subnavegació de la secció Empresa: la mostren els layouts d'`empresa`, `experiencia` i `documents`. */
export function ProfileSubnav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Seccions del perfil" className="scroll-thin mb-6 flex overflow-x-auto border-b border-border">
      {PROFILE_SUBNAV.map((item) => {
        const active = isSubnavActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px border-b-2 px-4 py-2.5 text-body whitespace-nowrap transition-colors",
              active ? "border-accent-500 font-semibold text-ink" : "border-transparent text-muted hover:text-ink",
            )}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
