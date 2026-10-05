import { WorkspaceLauncher } from "@/components/WorkspaceLauncher";
import { Card, LinkButton } from "@/components/ui";

export function DecisionFooter({ companyId, tenderId, workspaceId, checked }: { companyId: string; tenderId: string; workspaceId: string | null; checked: boolean }) {
  return (
    <Card>
      <div className="flex flex-col gap-3 px-5 py-4 md:flex-row md:items-center md:justify-between">
        <p className="text-body text-ink">La decisió de presentar-s&apos;hi és vostra: Tuvi no recomana sí ni no.</p>
        {workspaceId ? (
          <LinkButton href={`/candidatures/${workspaceId}?pas=decidir`} variant="primary" className="min-h-11 md:min-h-0">
            Continua la candidatura per decidir
          </LinkButton>
        ) : (
          <WorkspaceLauncher companyId={companyId} tenderId={tenderId} existing={null} checked={checked} className="min-h-11 md:min-h-0" />
        )}
      </div>
    </Card>
  );
}
