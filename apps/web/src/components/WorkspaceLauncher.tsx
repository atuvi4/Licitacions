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
 * i «Continua la candidatura» quan ja existeix. No hi ha manera d'acabar amb
 * dues candidatures per la mateixa licitació ni prement dues vegades: el POST
 * del servidor és idempotent i retorna la que ja hi hagués.
 *
 * Si la pàgina ja ha consultat la candidatura (capçalera de L9), la passa amb
 * `existing` i `checked` i aquí no es repeteix la crida.
 */
export function WorkspaceLauncher({
  companyId,
  tenderId,
  existing: existingProp,
  checked: checkedProp,
  className,
}: {
  companyId: string;
  tenderId: string;
  existing?: string | null;
  checked?: boolean;
  className?: string;
}) {
  const router = useRouter();
  const mutation = useMutation();
  const [ownExisting, setOwnExisting] = useState<string | null>(null);
  const [ownChecked, setOwnChecked] = useState(false);
  const managed = checkedProp !== undefined;

  useEffect(() => {
    if (managed) return;
    let cancelled = false;
    async function look() {
      try {
        const found = await api.get<WorkspaceDetail>(
          `/companies/${companyId}/tenders/${tenderId}/workspace`,
        );
        if (!cancelled) setOwnExisting(found.id);
      } catch (caught) {
        // Un 404 aquí no és un error: vol dir que encara no s'ha obert cap
        // candidatura per aquesta licitació.
        if (!cancelled && !(caught instanceof ApiError && caught.status === 404)) {
          setOwnExisting(null);
        }
      } finally {
        if (!cancelled) setOwnChecked(true);
      }
    }
    void look();
    return () => {
      cancelled = true;
    };
  }, [companyId, tenderId, managed]);

  const existing = managed ? (existingProp ?? null) : ownExisting;
  const checked = managed ? checkedProp : ownChecked;

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
    <Button
      variant="primary"
      size="md"
      onClick={() => void openWorkspace()}
      loading={mutation.saving}
      className={className}
    >
      {existing ? "Continua la candidatura" : "Obre candidatura"}
    </Button>
  );
}
