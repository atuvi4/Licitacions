"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import { ImportTenderWizard } from "@/components/ImportTenderWizard";
import { useSession } from "@/components/SessionProvider";
import { Button, PageHeader } from "@/components/ui";
import { canImportTender } from "@/lib/import-wizard";

/** Capçalera comuna de les dues vistes: títol i «Importa licitació». */
export function TendersHeader({ onImported }: { onImported: () => void }) {
  const { membership } = useSession();
  const [importOpen, setImportOpen] = useState(false);
  const canImport = canImportTender(membership?.role);
  return (
    <>
      <PageHeader
        title="Oportunitats"
        description="Licitacions publicades a la Plataforma de Contratación del Sector Público i a altres fonts, amb el pre-fit calculat sobre el vostre perfil quan existeix."
        actions={
          canImport ? (
            <Button variant="secondary" onClick={() => setImportOpen(true)}>
              <Plus size={15} strokeWidth={1.5} aria-hidden /> Importa licitació
            </Button>
          ) : null
        }
      />
      <ImportTenderWizard open={importOpen} onClose={() => setImportOpen(false)} onFinished={onImported} />
    </>
  );
}
