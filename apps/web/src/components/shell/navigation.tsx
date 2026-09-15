import type { ReactNode } from "react";

interface NavItem {
  href: string;
  label: string;
  icon: ReactNode;
}

const icon = (path: ReactNode) => (
  <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-4 w-4 shrink-0" aria-hidden="true">
    {path}
  </svg>
);

export const NAVIGATION_GROUPS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Treball",
    items: [
      {
        href: "/dashboard",
        label: "Resum",
        icon: icon(<path d="M3 10.5 10 4l7 6.5M5 9.5V16h10V9.5" strokeLinecap="round" strokeLinejoin="round" />),
      },
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
        label: "Documents d'empresa",
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

export function isNavigationActive(pathname: string, href: string): boolean {
  if (href === "/empresa") return pathname === "/empresa";
  return pathname === href || pathname.startsWith(`${href}/`);
}

