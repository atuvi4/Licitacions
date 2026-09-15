import type { CurrentUser } from "@/lib/types";

interface UserSummaryProps {
  user: CurrentUser;
  role: string;
  logout: () => Promise<void>;
}

export function UserSummary({ user, role, logout }: UserSummaryProps) {
  return (
    <div className="flex items-center justify-between gap-3 border-t border-white/10 px-5 py-4">
      <div className="min-w-0">
        <p className="truncate text-[13px] font-medium text-white" title={user.full_name ?? user.email}>
          {user.full_name ?? user.email}
        </p>
        <p className="mt-0.5 truncate text-caption text-sidebar-muted">{role}</p>
      </div>
      <button
        type="button"
        onClick={() => void logout()}
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-control text-sidebar-ink transition-colors duration-150 hover:bg-sidebar-hover hover:text-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
        aria-label="Tanca la sessió"
        title="Tanca la sessió"
      >
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-4 w-4" aria-hidden="true">
          <path d="M12 6.5V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-1.5M9 10h8m0 0-2.5-2.5M17 10l-2.5 2.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>
    </div>
  );
}
