"use client";

import { Card } from "@/components/ui";

/** El cercador no ha trobat res en aquesta pestanya. */
export function NoResults({ search }: { search: string }) {
  return (
    <Card>
      <p role="status" className="px-5 py-6 text-center text-body text-muted">
        No s&apos;ha trobat cap resultat.
        <span className="sr-only"> per a «{search}»</span>
      </p>
    </Card>
  );
}
