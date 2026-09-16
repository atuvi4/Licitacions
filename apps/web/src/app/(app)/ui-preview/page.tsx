"use client";

/**
 * Galeria TEMPORAL dels components base (handoff L2.11): cada component en
 * tots els nivells i variants, amb dades fixes, per comparar amb la secció 07
 * de `Mockups.dc.html`. S'esborra a L15.
 *
 * (El handoff la situava a `(app)/_ui/`, però Next tracta les carpetes amb
 * guió baix inicial com a privades i no les serveix: per això viu a `/ui-preview`.)
 */

import { FileText, Search, Upload } from "lucide-react";
import { Suspense, useState } from "react";

import { Modal } from "@/components/Modal";
import {
  Attribution,
  Badge,
  Banner,
  Button,
  Card,
  CardHeader,
  Checkbox,
  DataTable,
  ErrorState,
  EvidenceRef,
  Field,
  Input,
  PageHeader,
  PageSkeleton,
  PermissionNote,
  Select,
  StatusPill,
  Tabs,
  Textarea,
  type Level,
} from "@/components/ui";
import { EmptyState } from "@/components/ui/EmptyState";

const LEVELS: Array<{ level: Level; label: string }> = [
  { level: "neutral", label: "Sense obrir" },
  { level: "running", label: "Analitzant 3 de 14" },
  { level: "ready", label: "Acreditat" },
  { level: "attention", label: "Cal revisar" },
  { level: "blocking", label: "Bloqueja" },
  { level: "action", label: "92 · encaix alt" },
];

type Row = { id: string; name: string; reason: string; amount: string; level: Level; label: string; blocking?: boolean };

const ROWS: Row[] = [
  { id: "1", name: "Classificació V-5 categoria 3", reason: "Demana categoria ≥ 3 · tenim V-5 cat. 2", amount: "—", level: "blocking", label: "Bloqueja", blocking: true },
  { id: "2", name: "Tres contractes ≥ 400.000 € en 3 anys", reason: "Demana 3 · tenim 2 registrats amb import", amount: "400.000 €", level: "attention", label: "Cal revisar" },
  { id: "3", name: "Pòlissa de responsabilitat civil", reason: "Límit 600.000 € · vigent el dia del termini", amount: "600.000 €", level: "ready", label: "Acreditat" },
];

const COLUMNS = [
  {
    key: "name",
    header: "Requisit · què demana i què tenim",
    cell: (row: Row) => (
      <>
        <p className="font-medium">{row.name}</p>
        <p className="text-[13px] text-muted">{row.reason}</p>
      </>
    ),
  },
  { key: "amount", header: "Xifra", width: "120px", align: "right" as const, cell: (row: Row) => row.amount },
  { key: "state", header: "Estat", width: "130px", cell: (row: Row) => <StatusPill level={row.level}>{row.label}</StatusPill> },
  { key: "action", header: "Acció", width: "120px", cell: () => <Button size="sm">Revisa</Button> },
];

function Section({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <section className="border border-border-strong p-5">
      <p className="font-heading text-[15px] font-semibold text-ink">{title}</p>
      <p className="mb-3.5 text-[12.5px] leading-relaxed text-muted">{note}</p>
      {children}
    </section>
  );
}

function TabsDemo() {
  const [tab, setTab] = useState("resum");
  return (
    <Tabs
      items={[
        { key: "resum", label: "Resum" },
        { key: "plecs", label: "Plecs", count: 3 },
        { key: "requisits", label: "Requisits", count: 2, countLevel: "blocking" },
        { key: "historial", label: "Historial" },
      ]}
      value={tab}
      onChange={setTab}
    />
  );
}

export default function UiPreviewPage() {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>("2");

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="Components base · galeria temporal"
        description="Cada component en tots els nivells i variants, amb dades fixes. Es compara amb la secció 07 de Mockups.dc.html i s'esborra a L15."
        actions={<Button variant="primary">Acció principal</Button>}
      />

      <div className="grid gap-5 lg:grid-cols-3">
        <Section title="Botons" note="Un sol sòlid per pantalla, amb marques. Secundari de línia. Els destructius no tenen color propi: el text ho diu.">
          <div className="flex flex-col items-start gap-3">
            <Button variant="primary">Acció principal</Button>
            <Button variant="secondary">Acció secundària</Button>
            <Button variant="secondary">
              <Upload size={16} strokeWidth={1.5} aria-hidden /> Amb icona Lucide 1,5
            </Button>
            <Button variant="secondary" disabled>
              Desactivat · 45 %
            </Button>
            <Button variant="primary" loading>
              Analitzant
            </Button>
            <Button variant="ghost">Enllaç d&apos;acció →</Button>
            <Button variant="primary" size="sm">
              Primari petit
            </Button>
            <Button variant="danger" size="sm">
              danger (conservat, no per al NO-GO)
            </Button>
          </div>
        </Section>

        <Section title="StatusPill · variants" note="Una per fila. L'etiqueta és el que passa, no el valor de l'API. Sis nivells, ple i contorn.">
          <div className="flex flex-col gap-2.5">
            <div className="flex flex-wrap gap-1.5">
              {LEVELS.map((item) => (
                <StatusPill key={item.level} level={item.level}>
                  {item.label}
                </StatusPill>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {LEVELS.map((item) => (
                <StatusPill key={item.level} level={item.level} outline>
                  {item.label}
                </StatusPill>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {LEVELS.map((item) => (
                <StatusPill key={item.level} level={item.level} size="sm">
                  {item.label}
                </StatusPill>
              ))}
            </div>
            <p className="text-[12.5px] text-muted">Antic Badge (conservat fins a L15):</p>
            <div className="flex flex-wrap gap-1.5">
              <Badge>neutral</Badge>
              <Badge tone="brand">brand</Badge>
              <Badge tone="success">success</Badge>
              <Badge tone="warning">warning</Badge>
              <Badge tone="danger">danger</Badge>
            </div>
          </div>
        </Section>

        <Section title="Attribution · variants" note="Qui ho afirma. Sense color: és una firma. Va al costat del contingut, en cos 12,5.">
          <div className="flex flex-col gap-2.5">
            <Attribution kind="ai" confidence="high" />
            <Attribution kind="ai" confidence="medium" />
            <Attribution kind="ai" confidence="low" />
            <Attribution kind="rule" label="Calculat amb el perfil · regles v3" />
            <Attribution kind="human" person={{ name: "Anna Puig" }} at="2026-09-12T10:00:00Z" />
          </div>
        </Section>

        <Section title="EvidenceRef" note="Cita literal + document + pàgina + revisió d'extracció. Apunta a l'extracció que la va originar.">
          <div className="flex flex-col gap-4">
            <EvidenceRef
              documentName="Plec administratiu"
              pageNumber={3}
              extractionRevision={1}
              href="/licitacions/t1/documents/d1?page=3&extraction=e1"
              quote="…un límite total de 600.000 euros y un sublímite por víctima de 150.000 euros."
            />
            <EvidenceRef documentName="Plec tècnic" pageNumber={12} extractionRevision={2} href="/licitacions/t1/documents/d2?page=12&extraction=e2" compact />
            <EvidenceRef documentName={null} pageNumber={null} href={null} />
          </div>
        </Section>

        <Section title="Banner · 4 nivells" note="Un sol bloc d'avisos per pantalla, ordenat per gravetat, sempre amb enllaç a on es resol.">
          <div className="flex flex-col gap-2.5">
            <Banner level="blocking" kicker="Termini" action={<Button size="sm">Veure la licitació</Button>}>
              El termini de presentació va tancar fa 3 dies.
            </Banner>
            <Banner level="attention" kicker="Abans de decidir" action={<Button size="sm">Resol →</Button>}>
              2 requisits bloquegen: ISO 14001, I-7.
            </Banner>
            <Banner level="neutral" kicker="Anàlisi anterior" action={<Button size="sm">Torna a analitzar</Button>}>
              Anàlisi del 3 de set. · el plec ha canviat.
            </Banner>
            <Banner level="ready" kicker="Dossier complet">
              17 de 17 requisits obligatoris coberts i confirmats.
            </Banner>
          </div>
        </Section>

        <Section title="Patró de targeta" note="Tres nivells i cap més: panell, objecte de decisió amb marques, i llista clau-valor.">
          <div className="flex flex-col gap-4">
            <Card>
              <CardHeader title="Panell" description="Vora d'un pèl, capçalera tenyida." action={<StatusPill level="neutral">meta</StatusPill>} />
              <p className="px-3.5 py-3 text-[13px] text-muted">Per a tot el contingut ordinari.</p>
            </Card>
            <Card frame className="p-4">
              <p className="font-heading text-kicker font-semibold tracking-[0.1em] text-brand-strong uppercase">Objecte de decisió</p>
              <p className="mt-1 text-[13px] text-muted">Marques de registre. Només capçalera de decisió, GO/NO-GO i versió tancada.</p>
            </Card>
          </div>
        </Section>
      </div>

      <Card>
        <CardHeader title="Patró de taula" description="Capçalera condensada · files amb nom (500) i motiu (13) · una pastilla · acció a la dreta · selecció accent-100 · grups · peu." />
        <DataTable
          columns={COLUMNS}
          rows={[]}
          rowKey={(row) => row.id}
          groups={[
            { key: "blocking", label: "Bloquegen", rows: ROWS.filter((row) => row.blocking), tint: "blocking" },
            { key: "review", label: "Per revisar", rows: ROWS.filter((row) => row.level === "attention") },
            { key: "ready", label: "Acreditats", rows: ROWS.filter((row) => row.level === "ready"), collapsed: true },
          ]}
          isSelected={(row) => row.id === selected}
          onRowClick={(row) => setSelected(row.id)}
          stackBelow="md"
          footer={
            <>
              <span className="tabular-nums">1–3 de 40</span>
              <span>Anterior · Següent</span>
            </>
          }
        />
      </Card>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader title="EmptyState" description="Cap buit sense sortida: títol 22, icona en quadrat hairline, acció obligatòria." />
          <EmptyState
            icon={<Search aria-hidden strokeWidth={1.5} />}
            title="Encara no sabem què busqueu"
            description="L'encaix es calcula amb els codis CPV, els territoris i la franja d'import del perfil. Sense això no podem ordenar les 148 licitacions obertes: només llistar-les."
            action={<Button variant="primary">Completa el perfil · 3 passos</Button>}
            secondary={<Button variant="secondary">Mira les 148 obertes</Button>}
          />
        </Card>
        <Card>
          <CardHeader title="ErrorState" description="Frase, acció, detall plegat. El codi no surt mai al títol." />
          <ErrorState
            title="No hem pogut carregar les oportunitats"
            description="La connexió amb el servei ha fallat. No s'ha perdut res: cap filtre ni cap vista desada depèn d'aquesta càrrega."
            action={<Button variant="primary">Torna-ho a provar</Button>}
            secondary={<Button variant="secondary">Torna a Avui</Button>}
            technical={{ method: "GET /tenders", code: "503", requestId: "7f3ac21b", at: "16 set. 11:04" }}
          />
        </Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <Section title="PermissionNote" note="Un permís denegat s'explica: el botó es veu al 45 %, i es diu qui sí que ho pot fer.">
          <PermissionNote
            who="qui té el rol de responsable"
            what="signar la decisió"
            action={<Button variant="secondary" size="sm">Demana la decisió a Anna Puig</Button>}
          >
            <Button variant="primary" disabled>
              Presentar-se · GO
            </Button>
          </PermissionNote>
        </Section>

        <Section title="Navegació · pestanyes" note="Màxim 4, estat a la URL. El recompte només si és feina pendent; en vermell si bloqueja.">
          <Suspense fallback={null}>
            <TabsDemo />
          </Suspense>
        </Section>

        <Section title="Formulari" note="Camps tal com eren, amb els tokens nous: cantonades quadrades, vora hairline, cap ombra.">
          <div className="flex flex-col gap-3">
            <Field label="Correu electrònic" required hint="El compte que t'han facilitat">
              <Input placeholder="nom@empresa.cat" />
            </Field>
            <Field label="Estat" error="Tria'n un">
              <Select options={[{ value: "a", label: "Obert" }, { value: "b", label: "Tancat" }]} placeholder="Tots" />
            </Field>
            <Field label="Motiu">
              <Textarea rows={2} placeholder="Per què aquest document acredita aquest requisit?" />
            </Field>
            <Checkbox label="Només amb termini obert" />
            <div>
              <Button variant="secondary" size="sm" onClick={() => setOpen(true)}>
                Obre un Modal
              </Button>
            </div>
          </div>
        </Section>
      </div>

      <Card>
        <CardHeader title="PageSkeleton · table" description="Títol, context i columnes reals des del primer instant; cap recompte abans de ser real." />
        <div className="p-4">
          <PageSkeleton
            variant="table"
            title="Oportunitats"
            context="carregant les licitacions…"
            columns={["Encaix", "Licitació", "Termini", "Import", "Estat"]}
          />
        </div>
      </Card>

      <Modal
        open={open}
        title="Ens presentem a aquesta licitació?"
        description="Quedarà signat pel teu nom i la data."
        onClose={() => setOpen(false)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              Cancel·la
            </Button>
            <Button variant="primary" onClick={() => setOpen(false)}>
              Presentar-se · GO
            </Button>
          </>
        }
      >
        <p className="text-body leading-6 text-muted">
          <FileText size={16} strokeWidth={1.5} aria-hidden className="mr-1 inline" />
          Modal amb cantonades quadrades, títol condensat de 20 px i l&apos;única elevació del sistema.
        </p>
      </Modal>
    </div>
  );
}
