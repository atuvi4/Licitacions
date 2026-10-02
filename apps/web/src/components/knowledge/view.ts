import type {
  KnowledgeRecords,
  KnowledgeSnapshot,
  Observation,
  SnapshotIndex,
  Vocabulary,
} from "@/lib/knowledge";
import type { Certification, FinancialYear, Insurance, Person, Project, Registration, Service } from "@/lib/types";

import type { DeclareTarget } from "./DeclareDialog";

/** Tot el que necessiten les tres vistes de l'àrea, ja carregat i indexat. */
/** Les fitxes de «Qualificació i mitjans» (0052), de les seves pròpies API. */
export interface QualificationRecords {
  financialYears: FinancialYear[];
  projects: Project[];
  insurances: Insurance[];
  registrations: Registration[];
  people: Person[];
}

export const EMPTY_QUALIFICATION: QualificationRecords = {
  financialYears: [],
  projects: [],
  insurances: [],
  registrations: [],
  people: [],
};

export interface KnowledgeView {
  snapshot: KnowledgeSnapshot;
  index: SnapshotIndex;
  /** Candidats de l'històric per id d'observació. */
  observations: Map<string, Observation>;
  /** Ids d'observació que ja tenen una dada actual sobre la mateixa clau. */
  superseded: Set<string>;
  records: KnowledgeRecords;
  certifications: Certification[];
  services: Service[];
  qualification: QualificationRecords;
  vocabulary: Vocabulary | null;
  /** Declarar, editar, retirar i tocar l'evidència. */
  canEdit: boolean;
  canDecide: boolean;
  canVerify: boolean;
  /** Eliminar del tot: propietari/ària o administració. */
  canDelete: boolean;
  reload: () => void;
  declare: (target: DeclareTarget) => void;
  editScope: (certification: Certification) => void;
  /** El que s'ha escrit al cercador (buit: es mostra tot). */
  search: string;
  /** Cert si algun dels textos conté el que s'ha escrit; sense cerca, sempre cert. */
  matches: (...texts: Array<string | null | undefined>) => boolean;
}

export const EMPTY_RECORDS: KnowledgeRecords = {
  operating_facts: [],
  solutions: [],
  solution_capabilities: [],
  products: [],
  solution_products: [],
  partners: [],
  partner_roles: [],
  equipment: [],
};
