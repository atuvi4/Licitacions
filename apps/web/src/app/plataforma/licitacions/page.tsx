"use client";

/**
 * Administració Tuvi · Licitacions públiques: l'entrada cap als documents
 * públics oficials d'una licitació (3B-2 §7.1.5 punt 5, ruling C4).
 *
 * Reutilitza el catàleg públic (`GET /tenders`: el que l'operador pot veure
 * són només licitacions públiques) amb la seva cerca (`q`: títol, expedient)
 * i l'ordre «recent». Un identificador enganxat porta directament a la pantalla
 * de documents. Cap dada d'empresa: ni pre-fit ni candidatures.
 */

import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button, Card, CardHeader, ErrorState, LinkButton, PageSkeleton } from "@/components/ui";
import { useResource } from "@/lib/hooks";
import { PLATFORM_HOME, PLATFORM_TENDERS, looksLikeUuid, platformTenderDocumentsPath } from "@/lib/platform";
import { buildQuery, formatDateTime, type CataloguePage, type TenderListItem } from "@/lib/tenders";

const LIMIT = 20;

export default function PlatformTendersPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const q = searchParams.get("q")?.trim() ?? "";
  const [draft, setDraft] = useState(q);
  const list = useResource<CataloguePage<TenderListItem>>(`/tenders?${buildQuery({ q, sort: "recent", limit: LIMIT })}`);

  function onSearch(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    if (looksLikeUuid(value)) {
      router.push(platformTenderDocumentsPath(value));
      return;
    }
    router.replace(value ? `${PLATFORM_TENDERS}?q=${encodeURIComponent(value)}` : PLATFORM_TENDERS, { scroll: false });
  }

  return (
    <div className="space-y-6">
      <nav aria-label="Ruta" className="flex items-center gap-1.5 text-[13px] text-muted">
        <Link href={PLATFORM_HOME} className="text-brand-strong hover:text-accent-800">
          Empreses
        </Link>
        <ChevronRight size={13} aria-hidden />
        <span className="text-ink">Licitacions</span>
      </nav>

      <header>
        <h1 className="font-heading text-page font-semibold text-ink">Licitacions públiques</h1>
        <p className="mt-1.5 max-w-[70ch] text-body leading-6 text-muted">
          El catàleg públic tal com l&apos;ha ingerit la font. Des de cada licitació s&apos;obre la gestió dels seus
          documents públics oficials: afegir-ne per URL, pujar-ne, verificar-los i revisar-ne el rol.
        </p>
      </header>

      <form role="search" aria-label="Cerca de licitacions" onSubmit={onSearch} className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          aria-label="Cerca per expedient, títol o identificador"
          placeholder="Expedient, títol o identificador de la licitació"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          className="min-w-0 flex-1 rounded-control border border-border-strong bg-surface px-3 py-1.5 text-[13.5px] text-ink placeholder:text-muted focus:outline-2 focus:outline-brand sm:max-w-xl"
        />
        <Button type="submit" variant="secondary" size="sm">
          Cerca
        </Button>
        {q && (
          <LinkButton href={PLATFORM_TENDERS} variant="ghost" size="sm">
            Treu la cerca
          </LinkButton>
        )}
      </form>

      {list.loading && !list.data ? (
        <PageSkeleton variant="table" title="Licitacions públiques" context="carregant el catàleg…" />
      ) : !list.data ? (
        <ErrorState
          title="No s'ha pogut carregar el catàleg"
          description={list.error ?? "La connexió amb el servei ha fallat."}
          action={
            <Button variant="primary" size="md" onClick={() => void list.reload()}>
              Torna-ho a provar
            </Button>
          }
        />
      ) : list.data.items.length === 0 ? (
        <Card>
          <p className="px-5 py-6 text-body text-muted">
            {q
              ? `Cap licitació pública coincideix amb «${q}». Prova l'expedient sencer o enganxa l'identificador de la licitació.`
              : "Cap licitació pública al catàleg: encara no s'ha ingerit cap font."}
          </p>
        </Card>
      ) : (
        <Card>
          <CardHeader
            title={q ? `Resultats per a «${q}» (${list.data.total})` : `Les ${list.data.items.length} més recents`}
            description={q ? "Cerca al catàleg públic per títol i expedient." : `Del total de ${list.data.total} licitacions públiques visibles.`}
          />
          <div className="overflow-x-auto">
            <table aria-label="Licitacions públiques" className="w-full text-[13.5px]">
              <thead>
                <tr className="border-b border-border text-left text-caption text-muted">
                  <th scope="col" className="px-5 py-2.5 font-medium">Expedient</th>
                  <th scope="col" className="px-3 py-2.5 font-medium">Licitació</th>
                  <th scope="col" className="px-3 py-2.5 font-medium">Termini</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">Documents</th>
                  <th scope="col" className="px-5 py-2.5 text-right font-medium">Accions</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((tender) => (
                  <tr key={tender.id} className="border-b border-border align-top last:border-b-0">
                    <td className="px-5 py-3 font-mono text-[12.5px] text-ink">{tender.expediente_number ?? "—"}</td>
                    <td className="px-3 py-3">
                      <span className="block font-medium text-ink">{tender.title}</span>
                      <span className="block text-caption text-muted">{tender.buyer?.name ?? "Òrgan no informat"}</span>
                    </td>
                    <td className="px-3 py-3 text-muted">{tender.deadline_at ? formatDateTime(tender.deadline_at) : "—"}</td>
                    <td className="px-3 py-3 text-right tabular-nums text-muted">{tender.document_count ?? "—"}</td>
                    <td className="px-5 py-3 text-right">
                      <LinkButton href={platformTenderDocumentsPath(tender.id)} variant="secondary" size="sm">
                        Documents →
                      </LinkButton>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
