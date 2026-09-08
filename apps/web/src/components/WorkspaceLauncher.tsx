"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import type { WorkspaceCreateResult, WorkspaceDetail } from "@/lib/workspace";

/**
 * El pas de la fitxa de licitació a la candidatura.
 *
 * Un sol botó que canvia de text: «Obre candidatura» quan encara no n'hi ha cap
 * i «Obre el workspace» quan ja existeix. No hi ha manera d'acabar amb dues
 * candidatures per la mateixa licitació ni prement dues vegades: el POST del
 * servidor és idempotent i retorna la que ja hi hagués.
 */
export function WorkspaceLauncher({
  companyId,
  tenderId,
}: {
  companyId: string;
  tenderId: string;
}) {
  const router = useRouter();
  const mutation = useMutation();
  const [existing, setExisting] = useState<string | null>(null);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function look() {
      try {
        const found = await api.get<WorkspaceDetail>(
          `/companies/${companyId}/tenders/${tenderId}/workspace`,
        );
        if (!cancelled) setExisting(found.id);
      } catch (caught) {
        // Un 404 aquí no és un error: vol dir que encara no s'ha obert cap
        // candidatura per aquesta licitació.
        if (!cancelled && !(caught instanceof ApiError && caught.status === 404)) {
          setExisting(null);
        }
      } finally {
        if (!cancelled) setChecked(true);
      }
    }
    void look();
    return () => {
      cancelled = true;
    };
  }, [companyId, tenderId]);

  async function openWorkspace() {
    if (existing) {
      router.push(`/candidatures/${existing}`);
      return;
    }
    await mutation.run(async () => {
      const result = await api.post<WorkspaceCreateResult>(
        `/companies/${companyId}/tenders/${tenderId}/workspace`,
        {},
      );
      router.push(`/candidatures/${result.workspace.id}`);
    });
  }

  if (!checked) return null;

  return (
    <Button variant="primary" size="sm" onClick={() => void openWorkspace()} loading={mutation.saving}>
      {existing ? "Obre el workspace" : "Obre candidatura"}
    </Button>
  );
}
