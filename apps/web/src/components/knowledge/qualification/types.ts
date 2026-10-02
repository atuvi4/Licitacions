/** El que rep cada calaix de «Qualificació i mitjans». */
export interface DrawerProps<T> {
  /** La dada que s'edita; sense, és una alta. */
  record?: T;
  /** Verificada segons la fotografia (avís: editar-la la torna a pendent). */
  verified: boolean;
  /** Eliminar: propietari/ària o administració. */
  canDelete: boolean;
  onClose: () => void;
  /** S'ha desat o eliminat: cal rellegir les llistes i la fotografia. */
  onSaved: () => void;
}
