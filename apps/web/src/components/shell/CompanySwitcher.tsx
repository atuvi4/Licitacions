import type { Membership } from "@/lib/types";

interface CompanySwitcherProps {
  membership: Membership;
  companies: Membership[];
  selectCompany: (companyId: string) => void;
}

export function CompanySwitcher({ membership, companies, selectCompany }: CompanySwitcherProps) {
  const companyName = membership.company.trade_name ?? membership.company.legal_name;
  const initials = companyName
    .split(" ")
    .slice(0, 2)
    .map((word) => word[0])
    .join("")
    .toUpperCase();

  return (
    <div className="flex min-w-0 items-center gap-3">
      <span className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-control border border-border bg-subtle text-caption font-semibold text-muted sm:flex" aria-hidden="true">
        {initials || "LI"}
      </span>
      <div className="min-w-0">
        <p className="text-[11px] leading-4 text-faint">Empresa activa</p>
        {companies.length > 1 ? (
          <select
            value={membership.company_id}
            onChange={(event) => selectCompany(event.target.value)}
            aria-label="Empresa activa"
            className="mt-0.5 block h-7 w-full max-w-72 rounded-control border border-border bg-surface px-2 text-[13px] font-medium text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
          >
            {companies.map((item) => (
              <option key={item.company_id} value={item.company_id}>
                {item.company.trade_name ?? item.company.legal_name}
              </option>
            ))}
          </select>
        ) : (
          <p className="truncate text-body font-semibold text-ink" title={membership.company.legal_name}>
            {companyName}
          </p>
        )}
      </div>
    </div>
  );
}
