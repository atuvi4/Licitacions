import type { Level } from "@/components/ui";
import { formatDate } from "@/lib/format";
import { stepOf } from "@/lib/labels";
import { describeChange } from "@/lib/tender-changes";
import { formatDateTime } from "@/lib/tenders";
import { blockerCountLabel } from "@/lib/readiness";
import {
  DECISION_LABELS,
  DECISION_REASON_LABELS,
  NO_ANALYSIS_TEXT,
  PREPARE_READ_ONLY_TEXT,
  WARNING_LABELS,
  hasNoAnalysis,
  isPrepareEditable,
  type WorkspaceDetail,
  type WorkspaceStatus,
} from "@/lib/workspace";

export { PREPARE_READ_ONLY_TEXT };

export type PrepareMode = "edit" | "read-only";

/**
 * Com es pinten Preparar i Tancar (3B-2 §7.1.6, decisió 4): editables només
 * amb un GO vigent; si no, visibles però en lectura. El valor surt de la
 * decisió humana, mai de l'estat de la feina.
 */
export function prepareMode(workspace: Pick<WorkspaceDetail, "human_decision">): PrepareMode {
  return isPrepareEditable(workspace) ? "edit" : "read-only";
}

/**
 * Els tres passos de la candidatura (handoff L12). No és cap màquina d'estats
 * nova: cada pas es projecta des de l'estat real amb `stepOf`, i el que es
 * veu (`?pas=`) és independent d'on és de debò la candidatura.
 */
export type CandidatureStep = "decidir" | "preparar" | "tancar";

export const STEPS: readonly CandidatureStep[] = ["decidir", "preparar", "tancar"];

export const STEP_TITLES: Record<CandidatureStep, string> = {
  decidir: "Decidir",
  preparar: "Preparar",
  tancar: "Tancar",
};

/**
 * On s'obre la candidatura si la URL no ho diu. `stepOf` té un quart valor,
 * «tancada», que no és cap pas: un NO-GO es mira des de Decidir (la decisió és
 * el que hi ha), i una presentada o retirada des de Tancar.
 */
export function defaultStep(status: WorkspaceStatus): CandidatureStep {
  const step = stepOf(status);
  if (step === "tancada") return status === "no_go_approved" ? "decidir" : "tancar";
  return step;
}

export function resolveStep(param: string | null | undefined, status: WorkspaceStatus): CandidatureStep {
  return param && (STEPS as readonly string[]).includes(param)
    ? (param as CandidatureStep)
    : defaultStep(status);
}

export type StepState = "done" | "on" | "todo";

export interface StepItem {
  key: CandidatureStep;
  number: 1 | 2 | 3;
  label: string;
  /** Progrés REAL (de l'estat), no el pas que s'està mirant. */
  state: StepState;
  /** Subtext curt, sense el «·»: el dibuixa qui ho pinta. */
  note: string | null;
  pill: { level: Level; text: string } | null;
}

/**
 * `readinessBlockers`: el nombre de pendents de la preparació final (la mateixa
 * lectura que el distintiu «N pendents de preparació» i que Tancar, A15), o
 * `null` mentre no se sap. Un sol comptador: `readiness_blockers` del workspace
 * no hi compta.
 */
export function stepperItems(workspace: WorkspaceDetail, readinessBlockers: number | null = null): StepItem[] {
  const real = stepOf(workspace.status);
  const decided = workspace.human_decision;
  const latest = workspace.latest_decision;
  const closedNoGo = workspace.status === "no_go_approved";
  const submitted = workspace.status === "submitted";
  const withdrawn = workspace.status === "withdrawn";

  // Pas 1. «Fet» vol dir que hi ha una decisió que tanca la pregunta (GO o
  // NO-GO). Un ajornament la deixa oberta.
  const decisionText = decided && latest ? `${DECISION_LABELS[decided]} · ${formatDate(latest.decided_at)}` : null;
  const step1State: StepState = decided === "go" || decided === "no_go" ? "done" : "on";
  let step1Pill: StepItem["pill"] = null;
  if (decided && latest && workspace.decision_outdated) {
    step1Pill = {
      level: "attention",
      text: `${DECISION_LABELS[decided]} del ${formatDate(latest.decided_at)} · el plec ha canviat`,
    };
  } else if (decided === "defer") {
    step1Pill = { level: "attention", text: "Ajornada · torna a decidir" };
  }

  // Pas 2. S'obre amb GO; un NO-GO el deixa tancat per sempre.
  const step2State: StepState =
    real === "preparar" ? "on" : real === "tancar" || submitted || withdrawn ? "done" : "todo";
  const step2Note = decided === "go" ? null : closedNoGo ? "tancada amb NO-GO" : "s'obre amb GO";

  // Pas 3. Només «fet» quan s'ha presentat de debò: aprovar un paquet no és presentar.
  const step3State: StepState = submitted ? "done" : real === "tancar" ? "on" : "todo";
  // Sense cap anàlisi del plec, el recompte de pendents no diu res del que el
  // plec exigeix: es diu el que passa (3B-2 §7.1.3).
  const noAnalysis = hasNoAnalysis(workspace.dossier_coverage);
  const step3Note = closedNoGo
    ? "tancada amb NO-GO"
    : withdrawn
      ? "retirada"
      : submitted
        ? "presentada"
        : noAnalysis
          ? NO_ANALYSIS_TEXT.charAt(0).toLowerCase() + NO_ANALYSIS_TEXT.slice(1)
          : readinessBlockers === null
            ? "comprovant la preparació"
            : readinessBlockers > 0
              ? blockerCountLabel(readinessBlockers)
              : "a punt per tancar";

  return [
    { key: "decidir", number: 1, label: STEP_TITLES.decidir, state: step1State, note: decisionText, pill: step1Pill },
    { key: "preparar", number: 2, label: STEP_TITLES.preparar, state: step2State, note: step2Note, pill: null },
    { key: "tancar", number: 3, label: STEP_TITLES.tancar, state: step3State, note: step3Note, pill: null },
  ];
}

export interface PageBanner {
  level: "blocking" | "attention";
  kicker: string;
  title: string;
  /** Una segona frase, si cal explicar què implica. */
  detail: string | null;
  /** La resta de motius vigents, sense repetir el títol. */
  reasons: string[];
  action: { label: string; href: string };
}

/**
 * Un sol bàner per pàgina (handoff §2.4), amb el motiu més greu com a títol
 * i la resta a dins. Ordre: anul·lada > lot retirat > termini passat >
 * decisió caducada perquè el plec ha canviat. Només fets del plec: els
 * bloqueigs, els avisos i l'anàlisi endarrerida del compliment anterior no
 * fan cap bàner. Tot surt del contracte real del workspace; res no s'infereix.
 */
export function primaryBanner(workspace: WorkspaceDetail): PageBanner | null {
  const tender = `/licitacions/${workspace.tender_id}`;
  const openTender = { label: "Obre la licitació →", href: tender };
  const decisionReasons = [
    ...workspace.decision_reasons
      .filter((code) => code !== "tender_changed_since_decision")
      .map((code) => DECISION_REASON_LABELS[code] ?? code),
    ...workspace.changes_since_decision.map((event) => describeChange(event)),
  ];

  type Candidate = Omit<PageBanner, "reasons"> & {
    key: string;
    /** Una línia que el resumeix quan NO és el títol. */
    summary: string | null;
    /** Els motius concrets; surten sempre, també sota el títol. */
    details: string[];
  };
  const candidates: Candidate[] = [];
  if (!workspace.tender_actionable) {
    candidates.push({
      key: "tender",
      level: "blocking",
      kicker: "Anul·lada",
      title: "L'òrgan ha anul·lat aquesta licitació",
      detail:
        "No s'hi pot preparar res de nou. La decisió, la feina i els paquets anteriors es conserven com a història auditable.",
      action: openTender,
      summary: "L'òrgan ha anul·lat aquesta licitació.",
      details: [],
    });
  }
  if (workspace.lot_removed) {
    candidates.push({
      key: "lot",
      level: "blocking",
      kicker: "Lot retirat",
      title: "El lot d'aquesta candidatura ja no és a la licitació",
      detail: "La licitació continua, però sense aquest lot. La feina feta i les decisions es conserven.",
      action: openTender,
      summary: "El lot d'aquesta candidatura ja no és a la licitació.",
      details: [],
    });
  }
  if (workspace.deadline_passed) {
    candidates.push({
      key: "deadline",
      level: "blocking",
      kicker: "Termini",
      title: "El termini de presentació ja ha passat",
      detail: `${workspace.deadline_at ? `Va vèncer el ${formatDateTime(workspace.deadline_at)}. ` : ""}Es pot deixar constància del que es decideix, però un GO exigeix motiu escrit.`,
      action: openTender,
      summary: WARNING_LABELS.deadline_passed,
      details: [],
    });
  }
  if (workspace.decision_outdated) {
    candidates.push({
      key: "decision",
      level: "attention",
      kicker: "Decisió",
      title: workspace.decision_basis_unknown
        ? "Cal revisar la decisió"
        : "La licitació ha canviat després de la decisió",
      detail:
        "La decisió es conserva tal com es va prendre, amb qui la va signar i quan. El que cal revisar és si segueix sent vàlida.",
      action: { label: "Veure els canvis →", href: `${tender}?tab=historial` },
      summary: "La licitació ha canviat després de la decisió.",
      details: decisionReasons,
    });
  }

  const head = candidates[0];
  if (!head) return null;
  const reasons: string[] = [];
  const add = (line: string) => {
    if (line === head.title || line === `${head.title}.`) return;
    if (!reasons.includes(line)) reasons.push(line);
  };
  for (const candidate of candidates) {
    if (candidate !== head && candidate.summary) add(candidate.summary);
    for (const line of candidate.details) add(line);
  }
  return {
    level: head.level,
    kicker: head.kicker,
    title: head.title,
    detail: head.detail,
    reasons,
    action: head.action,
  };
}
