"use client";

import Link from "next/link";
import { useRef, useState } from "react";

import {
  Attribution,
  Button,
  Card,
  CardHeader,
  DataTable,
  EmptyState,
  Field,
  Input,
  ResultNotice,
  Select,
  StatusPill,
  Textarea,
  type DataTableColumn,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { CHECKLIST_LEVEL, TASK_LEVEL } from "@/lib/labels";
import { formatDateTime } from "@/lib/tenders";
import {
  CHECKLIST_STATUS_LABELS,
  checklistReconciliationMessage,
  TASK_STATUS_LABELS,
  type ChecklistItem,
  type ChecklistItemStatus,
  type ChecklistRegenerateResult,
  type TaskStatus,
  type WorkspaceDetail,
  type WorkspaceTask,
} from "@/lib/workspace";

function base(workspace: WorkspaceDetail): string {
  return `/companies/${workspace.company_id}/workspaces/${workspace.id}`;
}

function peopleOptions(workspace: WorkspaceDetail) {
  return [
    { value: "", label: "Sense assignar" },
    ...workspace.members.map((person) => ({
      value: person.id,
      // Només la gent de l'empresa: la llista ve dels memberships, no d'un
      // sistema de persones a part.
      label: person.full_name ?? person.email,
    })),
  ];
}

const CHECKLIST_OPTIONS = (Object.keys(CHECKLIST_STATUS_LABELS) as ChecklistItemStatus[]).map((value) => ({
  value,
  label: CHECKLIST_STATUS_LABELS[value],
}));
const TASK_OPTIONS = (Object.keys(TASK_STATUS_LABELS) as TaskStatus[]).map((value) => ({
  value,
  label: TASK_STATUS_LABELS[value],
}));
const CONTROL = "min-h-11 w-full md:min-h-0";

/**
 * Checklist derivada dels requisits.
 *
 * Cada item generat conserva l'enllaç al requisit original: «Veure requisit» no
 * és decoració, és el que permet comprovar d'on surt la tasca sense sortir a
 * buscar-ho al plec. Restil (handoff L13): la lògica i els endpoints són els
 * de sempre.
 */
export function ChecklistSection({
  workspace,
  onChange,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
}) {
  const mutation = useMutation();
  const [title, setTitle] = useState("");
  const [outcome, setOutcome] = useState<ReturnType<typeof checklistReconciliationMessage> | null>(null);

  const done = workspace.checklist.filter((item) => item.status === "completed").length;

  async function update(itemId: string, patch: Record<string, unknown>) {
    await mutation.run(async () => {
      await api.patch(`${base(workspace)}/checklist/${itemId}`, patch);
    });
    await onChange();
  }

  async function addManual() {
    if (!title.trim()) return;
    const ok = await mutation.run(async () => {
      await api.post(`${base(workspace)}/checklist`, { title: title.trim() });
    });
    if (ok) {
      setTitle("");
      await onChange();
    }
  }

  async function regenerate() {
    await mutation.run(async () => {
      const result = await api.post<ChecklistRegenerateResult>(`${base(workspace)}/checklist/regenerate`, {});
      // Sense això l'acció responia 200 i no passava res visible: ni un item,
      // ni un missatge, ni una pista. I era el botó que l'avís et demanava
      // prémer.
      setOutcome(checklistReconciliationMessage(result));
    });
    await onChange();
  }

  const columns: Array<DataTableColumn<ChecklistItem>> = [
    {
      key: "title",
      header: "Cal fer",
      cell: (item) => (
        <div className="min-w-0">
          <p className="text-[14px] leading-5 font-medium text-ink">{item.title}</p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-muted">
            {item.mandatory && (
              <StatusPill level="attention" size="sm" outline>
                Obligatori
              </StatusPill>
            )}
            {item.assigned_to_name && <span>{item.assigned_to_name}</span>}
            {item.note && <span>· {item.note}</span>}
          </p>
        </div>
      ),
    },
    {
      key: "status",
      header: "Estat",
      width: "22%",
      cell: (item) => (
        <div className="flex flex-col gap-1.5">
          <StatusPill level={CHECKLIST_LEVEL[item.status]} size="sm" className="self-start">
            {CHECKLIST_STATUS_LABELS[item.status]}
          </StatusPill>
          <Select
            aria-label="Estat"
            className={CONTROL}
            value={item.status}
            options={CHECKLIST_OPTIONS}
            onChange={(event) => void update(item.id, { status: event.target.value })}
          />
        </div>
      ),
    },
    {
      key: "assignee",
      header: "Responsable",
      width: "22%",
      cell: (item) => (
        <Select
          aria-label="Responsable"
          className={CONTROL}
          value={item.assigned_to ?? ""}
          options={peopleOptions(workspace)}
          onChange={(event) => void update(item.id, { assigned_to: event.target.value || null })}
        />
      ),
    },
    {
      key: "origin",
      header: "Origen",
      width: "16%",
      cell: (item) =>
        item.requirement_id ? (
          <Link href={`/licitacions/${workspace.tender_id}?tab=requisits`} className="text-[13px] text-brand hover:underline">
            Veure requisit →
          </Link>
        ) : (
          <StatusPill level="neutral" size="sm" outline>
            Manual
          </StatusPill>
        ),
    },
  ];

  return (
    <Card>
      <CardHeader
        title="Checklist"
        description={`${done} de ${workspace.checklist.length} completats · derivada dels requisits del plec`}
        action={
          <Button size="sm" variant="secondary" onClick={() => void regenerate()} loading={mutation.saving}>
            Reconcilia amb l&apos;anàlisi
          </Button>
        }
      />
      {outcome && (
        <div className="px-3.5 pt-3">
          <ResultNotice message={outcome.message} detail={outcome.detail} tone={outcome.tone} onDismiss={() => setOutcome(null)} />
        </div>
      )}
      {workspace.checklist.length === 0 ? (
        <EmptyState
          title="Encara no hi ha cap item"
          description="La checklist es deriva dels requisits del plec. Si el plec ja està analitzat, reconcilia-la; si no, afegeix els items a mà."
          action={
            <Button variant="primary" size="md" onClick={() => void regenerate()} loading={mutation.saving}>
              Reconcilia amb l&apos;anàlisi
            </Button>
          }
        />
      ) : (
        <DataTable columns={columns} rows={workspace.checklist} rowKey={(item) => item.id} stackBelow="md" />
      )}
      <div className="flex flex-wrap items-end gap-2 border-t border-border px-3.5 py-3">
        <Field label="Afegeix un item manual" className="min-w-64 flex-1">
          <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Preparar annex 3" />
        </Field>
        <Button variant="secondary" onClick={() => void addManual()} loading={mutation.saving} className="min-h-11 md:min-h-0">
          Afegeix
        </Button>
      </div>
      {mutation.error && <p className="px-3.5 pb-3 text-[13px] text-danger">{mutation.error}</p>}
    </Card>
  );
}

/** Tasques manuals: la feina que no surt de cap requisit. */
export function TasksSection({
  workspace,
  onChange,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
}) {
  const mutation = useMutation();
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState("");
  const [dueAt, setDueAt] = useState("");
  const titleField = useRef<HTMLDivElement>(null);

  async function create() {
    if (!title.trim()) return;
    const ok = await mutation.run(async () => {
      await api.post(`${base(workspace)}/tasks`, {
        title: title.trim(),
        assigned_to: assignee || null,
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
      });
    });
    if (ok) {
      setTitle("");
      setAssignee("");
      setDueAt("");
      await onChange();
    }
  }

  async function update(taskId: string, patch: Record<string, unknown>) {
    await mutation.run(async () => {
      await api.patch(`${base(workspace)}/tasks/${taskId}`, patch);
    });
    await onChange();
  }

  const open = workspace.tasks.filter((task) => task.status === "pending" || task.status === "in_progress").length;

  const columns: Array<DataTableColumn<WorkspaceTask>> = [
    {
      key: "title",
      header: "Tasca",
      cell: (task) => (
        <div className="min-w-0">
          <p className="text-[14px] leading-5 font-medium text-ink">{task.title}</p>
          <p className="mt-1 flex flex-wrap items-center gap-1.5 text-[12.5px] text-muted">
            {task.mandatory && (
              <StatusPill level="attention" size="sm" outline>
                Obligatòria
              </StatusPill>
            )}
            {task.description && <span>{task.description}</span>}
          </p>
        </div>
      ),
    },
    {
      key: "status",
      header: "Estat",
      width: "22%",
      cell: (task) => (
        <div className="flex flex-col gap-1.5">
          <StatusPill level={TASK_LEVEL[task.status]} size="sm" className="self-start">
            {TASK_STATUS_LABELS[task.status]}
          </StatusPill>
          <Select
            aria-label="Estat"
            className={CONTROL}
            value={task.status}
            options={TASK_OPTIONS}
            onChange={(event) => void update(task.id, { status: event.target.value })}
          />
        </div>
      ),
    },
    {
      key: "assignee",
      header: "Responsable",
      width: "22%",
      cell: (task) => (
        <Select
          aria-label="Responsable"
          className={CONTROL}
          value={task.assigned_to ?? ""}
          options={peopleOptions(workspace)}
          onChange={(event) => void update(task.id, { assigned_to: event.target.value || null })}
        />
      ),
    },
    {
      key: "due",
      header: "Venciment",
      width: "18%",
      cell: (task) => <span className="text-[13px] text-muted">{task.due_at ? formatDateTime(task.due_at) : "—"}</span>,
    },
  ];

  return (
    <Card id="tasques">
      <CardHeader
        title="Tasques"
        description={`${open === 0 ? "Cap tasca oberta" : `${open} ${open === 1 ? "oberta" : "obertes"}`} · feina de preparació de la candidatura`}
      />
      {workspace.tasks.length === 0 ? (
        <EmptyState
          title="Cap tasca encara"
          description="Les tasques són la feina que no surt de cap requisit. Escriu la primera aquí sota."
          action={
            <Button variant="secondary" size="md" onClick={() => titleField.current?.querySelector("input")?.focus()}>
              Escriu la primera tasca
            </Button>
          }
        />
      ) : (
        <DataTable columns={columns} rows={workspace.tasks} rowKey={(task) => task.id} stackBelow="md" />
      )}
      <div className="flex flex-wrap items-end gap-2 border-t border-border px-3.5 py-3">
        <div ref={titleField} className="flex min-w-56 flex-1">
          <Field label="Nova tasca" className="flex-1">
          <Input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Demanar certificat bancari" />
          </Field>
        </div>
        <Field label="Responsable" className="w-52">
          <Select value={assignee} options={peopleOptions(workspace)} onChange={(event) => setAssignee(event.target.value)} />
        </Field>
        <Field label="Venciment" className="w-44">
          <Input type="date" value={dueAt} onChange={(event) => setDueAt(event.target.value)} />
        </Field>
        <Button variant="secondary" onClick={() => void create()} loading={mutation.saving} className="min-h-11 md:min-h-0">
          Crea
        </Button>
      </div>
      {mutation.error && <p className="px-3.5 pb-3 text-[13px] text-danger">{mutation.error}</p>}
    </Card>
  );
}

/** Notes: text pla, ordenades de la més recent a la més antiga. */
export function NotesSection({
  workspace,
  onChange,
}: {
  workspace: WorkspaceDetail;
  onChange: () => Promise<void>;
}) {
  const mutation = useMutation();
  const [text, setText] = useState("");

  async function create() {
    if (!text.trim()) return;
    const ok = await mutation.run(async () => {
      await api.post(`${base(workspace)}/notes`, { text: text.trim() });
    });
    if (ok) {
      setText("");
      await onChange();
    }
  }

  return (
    <Card>
      <CardHeader title="Notes" description={workspace.notes.length === 0 ? "Cap nota encara." : undefined} />
      <div className="space-y-3 px-3.5 py-3">
        <Field label="Escriu una nota">
          <Textarea rows={3} value={text} onChange={(event) => setText(event.target.value)} placeholder="Parlat amb el responsable del contracte…" />
        </Field>
        <Button variant="secondary" onClick={() => void create()} loading={mutation.saving} className="min-h-11 md:min-h-0">
          Afegeix la nota
        </Button>
        {mutation.error && <p className="text-[13px] text-danger">{mutation.error}</p>}
      </div>
      {workspace.notes.length > 0 && (
        <ol className="divide-y divide-border border-t border-border">
          {workspace.notes.map((note) => (
            <li key={note.id} className="px-3.5 py-3">
              <Attribution kind="human" person={{ name: note.author_name ?? "Una persona" }} at={note.created_at} />
              <p className="mt-1 text-[13px] leading-5 whitespace-pre-line text-ink">{note.text}</p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}
