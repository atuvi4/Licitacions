"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";

import { useSession } from "@/components/SessionProvider";
import { cn } from "@/components/ui";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

const icon = (path: ReactNode) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-4 w-4">
    {path}
  </svg>
);

const NAV_GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "General",
    items: [
      {
        href: "/dashboard",
        label: "Dashboard",
        icon: icon(<path d="M3 10.5 10 4l7 6.5M5 9.5V16h10V9.5" strokeLinecap="round" strokeLinejoin="round" />),
      },
    ],
  },
  {
    label: "Empresa",
    items: [
      {
        href: "/empresa",
        label: "Dades generals",
        icon: icon(<path d="M4 17V5.5A1.5 1.5 0 0 1 5.5 4h5A1.5 1.5 0 0 1 12 5.5V17M12 9h3.5A1.5 1.5 0 0 1 17 10.5V17M3 17h14M6.5 7.5h3M6.5 10.5h3M6.5 13.5h3" strokeLinecap="round" />),
      },
      {
        href: "/empresa/serveis",
        label: "Serveis",
        icon: icon(<path d="M4 6h12M4 10h12M4 14h7" strokeLinecap="round" />),
      },
      {
        href: "/empresa/territoris",
        label: "Àmbit geogràfic",
        icon: icon(
          <>
            <circle cx="10" cy="8.5" r="2.2" />
            <path d="M10 17s5-4.4 5-8a5 5 0 0 0-10 0c0 3.6 5 8 5 8Z" strokeLinejoin="round" />
          </>,
        ),
      },
      {
        href: "/empresa/cpv",
        label: "CPV",
        icon: icon(
          <>
            <rect x="3.5" y="4.5" width="13" height="11" rx="1.5" />
            <path d="M6.5 8h7M6.5 11h4" strokeLinecap="round" />
          </>,
        ),
      },
      {
        href: "/empresa/solvencia",
        label: "Solvència",
        icon: icon(<path d="M4 16V9m4 7V5m4 11v-5m4 5V7" strokeLinecap="round" />),
      },
      {
        href: "/empresa/certificacions",
        label: "Certificacions",
        icon: icon(
          <>
            <circle cx="10" cy="8" r="4" />
            <path d="M7.5 11.5 6.5 17l3.5-2 3.5 2-1-5.5" strokeLinejoin="round" />
          </>,
        ),
      },
      {
        href: "/empresa/registres",
        label: "Registres",
        icon: icon(
          <>
            <path d="M5 4h10v12H5z" strokeLinejoin="round" />
            <path d="M7.5 7.5h5M7.5 10h5M7.5 12.5h3" strokeLinecap="round" />
          </>,
        ),
      },
      {
        href: "/empresa/assegurances",
        label: "Assegurances",
        icon: icon(
          <path
            d="M10 3.5 15.5 6v4c0 3.2-2.3 5.6-5.5 6.5-3.2-.9-5.5-3.3-5.5-6.5V6L10 3.5Z"
            strokeLinejoin="round"
          />,
        ),
      },
      {
        href: "/empresa/equip",
        label: "Equip humà",
        icon: icon(
          <>
            <circle cx="8" cy="7.5" r="2.5" />
            <path d="M3.5 16c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4" strokeLinecap="round" />
            <path d="M13.5 6.2a2.3 2.3 0 0 1 0 4.3M14.5 12.4c1.3.5 2 1.8 2 3.6" strokeLinecap="round" />
          </>,
        ),
      },
    ],
  },
  {
    label: "Licitacions",
    items: [
      {
        href: "/licitacions",
        label: "Oportunitats",
        icon: icon(
          <>
            <circle cx="9" cy="9" r="5" />
            <path d="M12.8 12.8 17 17" strokeLinecap="round" />
          </>,
        ),
      },
      {
        href: "/candidatures",
        label: "Candidatures",
        icon: icon(
          <>
            <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h9A1.5 1.5 0 0 1 16 5.5v9a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 4 14.5Z" />
            <path d="m7 10 2 2 4-4.5" strokeLinecap="round" strokeLinejoin="round" />
          </>,
        ),
      },
    ],
  },
  {
    label: "Acreditació",
    items: [
      {
        href: "/experiencia",
        label: "Experiència",
        icon: icon(
          <>
            <rect x="3" y="6" width="14" height="10" rx="1.5" />
            <path d="M7.5 6V4.8A.8.8 0 0 1 8.3 4h3.4a.8.8 0 0 1 .8.8V6" />
          </>,
        ),
      },
      {
        href: "/documents",
        label: "Documents",
        icon: icon(
          <>
            <path d="M11 3H6.5A1.5 1.5 0 0 0 5 4.5v11A1.5 1.5 0 0 0 6.5 17h7a1.5 1.5 0 0 0 1.5-1.5V7l-4-4Z" strokeLinejoin="round" />
            <path d="M11 3v4h4" strokeLinejoin="round" />
          </>,
        ),
      },
    ],
  },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/empresa") return pathname === "/empresa";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const { user, membership, companies, selectCompany, logout } = useSession();
  const [mobileOpen, setMobileOpen] = useState(false);

  const initials = (membership.company.trade_name ?? membership.company.legal_name)
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-40 flex w-60 flex-col bg-sidebar text-sidebar-ink transition-transform lg:static lg:translate-x-0",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-14 items-center gap-2.5 px-4">
          <span className="flex h-7 w-7 items-center justify-center rounded-md bg-brand text-[11px] font-bold text-white">
            {initials || "LI"}
          </span>
          <div className="min-w-0">
            <p className="truncate text-[13px] font-semibold text-white">
              {membership.company.trade_name ?? membership.company.legal_name}
            </p>
            <p className="text-[11px] text-sidebar-ink/70">Licitacions</p>
          </div>
        </div>

        <nav className="scroll-thin flex-1 overflow-y-auto px-2 pb-4">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="mb-5">
              <p className="px-3 pb-1.5 text-[10px] font-semibold uppercase tracking-wider text-sidebar-ink/50">
                {group.label}
              </p>
              <ul className="space-y-0.5">
                {group.items.map((item) => (
                  <li key={item.href}>
                    <Link
                      href={item.href}
                      onClick={() => setMobileOpen(false)}
                      className={cn(
                        "flex items-center gap-2.5 rounded-md px-3 py-1.5 text-[13px] transition-colors",
                        isActive(pathname, item.href)
                          ? "bg-sidebar-hover font-medium text-white"
                          : "hover:bg-sidebar-hover/70 hover:text-white",
                      )}
                    >
                      {item.icon}
                      {item.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <div className="border-t border-white/10 px-3 py-3">
          {companies.length > 1 && (
            <select
              value={membership.company_id}
              onChange={(event) => selectCompany(event.target.value)}
              className="mb-2 w-full rounded-md border border-white/15 bg-sidebar-hover px-2 py-1.5 text-[12px] text-white"
            >
              {companies.map((item) => (
                <option key={item.company_id} value={item.company_id}>
                  {item.company.trade_name ?? item.company.legal_name}
                </option>
              ))}
            </select>
          )}
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0">
              <p className="truncate text-[12px] text-white">{user.full_name ?? user.email}</p>
              <p className="truncate text-[11px] text-sidebar-ink/60">{membership.role}</p>
            </div>
            <button
              type="button"
              onClick={() => void logout()}
              className="rounded p-1.5 text-sidebar-ink transition-colors hover:bg-sidebar-hover hover:text-white"
              aria-label="Tanca la sessió"
              title="Tanca la sessió"
            >
              <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-4 w-4">
                <path d="M12 6.5V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-1.5M9 10h8m0 0-2.5-2.5M17 10l-2.5 2.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
        </div>
      </aside>

      {mobileOpen && (
        <div
          className="fixed inset-0 z-30 bg-ink/40 lg:hidden"
          onClick={() => setMobileOpen(false)}
          aria-hidden
        />
      )}

      {/* Contingut */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-surface/85 px-4 backdrop-blur lg:px-8">
          <button
            type="button"
            onClick={() => setMobileOpen((open) => !open)}
            className="rounded p-1.5 text-muted hover:bg-subtle lg:hidden"
            aria-label="Obre el menú"
          >
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-5 w-5">
              <path d="M4 6h12M4 10h12M4 14h12" strokeLinecap="round" />
            </svg>
          </button>
          <p className="truncate text-[13px] font-medium text-muted">
            {membership.company.legal_name}
          </p>
        </header>
        <main className="flex-1 px-4 py-6 lg:px-8 lg:py-8">
          <div className="mx-auto w-full max-w-6xl">{children}</div>
        </main>
      </div>
    </div>
  );
}
