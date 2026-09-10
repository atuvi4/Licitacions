"use client";

import Link from "next/link";
import { useState } from "react";

import {
  Badge,
  Button,
  Card,
  CardHeader,
  Field,
  Input,
  Select,
  ResultNotice,
  Table,
  Td,
  Textarea,
  Th,
  Tr,
} from "@/components/ui";
import { api } from "@/lib/api";
import { useMutation } from "@/lib/hooks";
import { formatDateTime } from "@/lib/tenders";
import {
  CHECKLIST_STATUS_LABELS,
  checklistReconciliationMessage,
  TASK_STATUS_LABELS,
  TASK_STATUS_TONES,
  type ChecklistItemStatus,
  type ChecklistRegenerateResult,
  type TaskStatus,
  type WorkspaceDetail,
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

/**
 * Checklist derivada dels requisits.
 *
 * Cada item generat conserva l'enllaç al requisit original: "Veure requisit" no
 * és decoració, és el que permet comprovar d'on surt la tasca sense sortir a
 * buscar-ho al plec.
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
      const result = await api.post<ChecklistRegenerateResult>(
        `${base(workspace)}/checklist/regenerate`,
        {},
      );
      // Sense això l'acció responia 200 i no passava res visible: ni un item,
      // ni un missatge, ni una pista. I era el botó que l'avís et demanava
      // prémer.
      setOutcome(checklistReconciliationMessage(result));
    });
    await onChange();
  }

  return (
    <Card>
      <CardHeader
        title="Checklist"
        description={`${done} de ${workspace.checklist.length} completats · derivada dels requisits del plec`}
        action={
          <Button size="sm" onClick={() => void regenerate()} loading={mutation.saving}>
            Reconcilia amb l&apos;anàlisi
          </Button>
        }
      />
      {outcome && (
        <div className="px-5 pt-4">
          <ResultNotice
            message={outcome.message}
            detail={outcome.detail}
            tone={outcome.tone}
            onDismiss={() => setOutcome(null)}
          />
        </div>
      )}
      {workspace.checklist.length === 0 ? (
        <p className="px-5 py-5 text-[13px] text-muted">
          Encara no hi ha cap item. Si el plec ja està analitzat, prem «Reconcilia amb
          l&apos;anàlisi».
        </p>
      ) : (
        <Table>
          <thead>
            <Tr>
              <Th>Cal fer</Th>
              <Th className="w-36">Estat</Th>
              <Th className="w-44">Responsable</Th>
              <Th className="w-28">Origen</Th>
            </Tr>
          </thead>
          <tbody>
            {workspace.checklist.map((item) => (
              <Tr key={item.id}>
                <Td>
                  <span className="text-ink">{item.title}</span>
                  {item.mandatory && (
                    <Badge tone="warning" className="ml-2">
                      Obligatori
                    </Badge>
                  )}
                </Td>
                <Td>
                  <Select
                    value={item.status}
                    options={(
                      Object.keys(CHECKLIST_STATUS_LABELS) as ChecklistItemStatus[]
                    ).map((value) => ({ value, label: CHECKLIST_STATUS_LABELS[value] }))}
                    onChange={(event) =>
                      void update(item.id, { status: event.target.value })
                    }
                  />
                </Td>
                <Td>
                  <Select
                    value={item.assigned_to ?? ""}
                    options={peopleOptions(workspace)}
                    onChange={(event) =>
                      void update(item.id, { assigned_to: event.target.value || null })
                    }
                  />
                </Td>
                <Td>
                  {item.requirement_id ? (
                    <Link
                      href={`/licitacions/${workspace.tender_id}#requisits`}
                      className="text-[13px] text-brand hover:underline"
                    >
                      Veure requisit
                    </Link>
                  ) : (
                    <Badge tone="neutral">Manual</Badge>
                  )}
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
      <div className="flex flex-wrap items-end gap-2 border-t border-border px-5 py-4">
        <Field label="Afegeix un item manual" className="min-w-64 flex-1">
          <Input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Preparar annex 3"
          />
        </Field>
        <Button onClick={() => void addManual()} loading={mutation.saving}>
          Afegeix
        </Button>
      </div>
      {mutation.error && <p className="px-5 pb-4 text-xs text-danger">{mutation.error}</p>}
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

  return (
    <Card>
      <CardHeader title="Tasques" description="Feina de preparació de la candidatura." />
      {workspace.tasks.length === 0 ? (
        <p className="px-5 py-5 text-[13px] text-muted">Cap tasca encara.</p>
      ) : (
        <Table>
          <thead>
            <Tr>
              <Th>Tasca</Th>
              <Th className="w-36">Estat</Th>
              <Th className="w-44">Responsable</Th>
              <Th className="w-40">Venciment</Th>
            </Tr>
          </thead>
          <tbody>
            {workspace.tasks.map((task) => (
              <Tr key={task.id}>
                <Td>
                  {task.title}
                  {task.mandatory && (
                    <Badge tone="warning" className="ml-2">
                      Obligatòria
                    </Badge>
                  )}
                </Td>
                <Td>
                  <Select
                    value={task.status}
                    options={(Object.keys(TASK_STATUS_LABELS) as TaskStatus[]).map((value) => ({
                      value,
                      label: TASK_STATUS_LABELS[value],
                    }))}
                    onChange={(event) => void update(task.id, { status: event.target.value })}
                  />
                </Td>
                <Td>
                  <Select
                    value={task.assigned_to ?? ""}
                    options={peopleOptions(workspace)}
                    onChange={(event) =>
                      void update(task.id, { assigned_to: event.target.value || null })
                    }
                  />
                </Td>
                <Td>
                  <span className="text-[13px] text-muted">
                    {task.due_at ? formatDateTime(task.due_at) : "—"}
                  </span>
                  <Badge tone={TASK_STATUS_TONES[task.status]} className="ml-2">
                    {TASK_STATUS_LABELS[task.status]}
                  </Badge>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
      <div className="flex flex-wrap items-end gap-2 border-t border-border px-5 py-4">
        <Field label="Nova tasca" className="min-w-56 flex-1">
          <Input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="Demanar certificat bancari"
          />
        </Field>
        <Field label="Responsable" className="w-52">
          <Select
            value={assignee}
            options={peopleOptions(workspace)}
            onChange={(event) => setAssignee(event.target.value)}
          />
        </Field>
        <Field label="Venciment" className="w-44">
          <Input type="date" value={dueAt} onChange={(event) => setDueAt(event.target.value)} />
        </Field>
        <Button onClick={() => void create()} loading={mutation.saving}>
          Crea
        </Button>
      </div>
      {mutation.error && <p className="px-5 pb-4 text-xs text-danger">{mutation.error}</p>}
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
      <CardHeader title="Notes" />
      <div className="space-y-3 px-5 py-4">
        <Field label="Escriu una nota">
          <Textarea
            rows={3}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="Parlat amb el responsable del contracte…"
          />
        </Field>
        <Button onClick={() => void create()} loading={mutation.saving}>
          Afegeix la nota
        </Button>
        {mutation.error && <p className="text-xs text-danger">{mutation.error}</p>}
      </div>
      {workspace.notes.length > 0 && (
        <ol className="divide-y divide-border border-t border-border">
          {workspace.notes.map((note) => (
            <li key={note.id} className="px-5 py-3">
              <p className="text-[12px] text-faint">
                {note.author_name ?? "—"} · {formatDateTime(note.created_at)}
              </p>
              <p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed text-ink">
                {note.text}
              </p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

/**
 * Els números de la revisió humana.
 *
 * Es mostren desglossats i no resumits en un percentatge: "12 obligatoris, 8
 * compleixen, 1 no, 2 sense dades, 1 a revisar" diu on cal actuar; un "75 %" no
 * diu res.
 */
export function BlockerCounters({ workspace }: { workspace: WorkspaceDetail }) {
  const c = workspace.counters;
  // Desglossament dels OBLIGATORIS, no de tots els requisits: les quatre xifres
  // sumen el total que hi ha a l'esquerra. Una taula que no quadra no s'entén.
  const rows: Array<[string, number, string]> = [
    ["Obligatoris", c.mandatory_total, "text-ink"],
    ["✅ Compleix", c.mandatory_satisfies, "text-success"],
    ["❌ No compleix", c.mandatory_does_not_satisfy, "text-danger"],
    ["❓ Sense dades", c.mandatory_unknown, "text-warning"],
    ["⚠ Cal revisar", c.mandatory_needs_review, "text-warning"],
  ];

  return (
    <Card>
      <CardHeader
        title="Compliment"
        description="Elegibilitat: podem acreditar que complim? No es barreja amb el pre-fit."
      />
      <p className="px-5 pt-4 text-[12px] text-muted">
        Desglossament dels requisits <strong>obligatoris</strong>
        {c.mandatory_not_applicable > 0 && ` · ${c.mandatory_not_applicable} no apliquen`} · en
        total s&apos;han avaluat {c.total} requisits
      </p>
      <div className="grid grid-cols-2 gap-4 px-5 py-5 sm:grid-cols-5">
        {rows.map(([label, value, tone]) => (
          <div key={label}>
            <p className="text-[12px] text-muted">{label}</p>
            <p className={`mt-0.5 text-xl font-semibold tabular-nums ${tone}`}>{value}</p>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 gap-4 border-t border-border px-5 py-4">
        <div>
          <p className="text-[12px] text-muted">Bloqueigs confirmats</p>
          <p
            className={`mt-0.5 text-xl font-semibold tabular-nums ${
              c.confirmed_blockers > 0 ? "text-danger" : "text-ink"
            }`}
          >
            {c.confirmed_blockers}
          </p>
        </div>
        <div>
          <p className="text-[12px] text-muted">Possibles bloqueigs</p>
          <p
            className={`mt-0.5 text-xl font-semibold tabular-nums ${
              c.potential_blockers > 0 ? "text-warning" : "text-ink"
            }`}
          >
            {c.potential_blockers}
          </p>
        </div>
      </div>
      <div className="border-t border-border px-5 py-3">
        <Link
          href={`/licitacions/${workspace.tender_id}`}
          className="text-[13px] text-brand hover:underline"
        >
          Veure el compliment detallat i la font del plec →
        </Link>
      </div>
    </Card>
  );
}
