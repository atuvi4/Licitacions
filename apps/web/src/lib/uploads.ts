/**
 * Validació de fitxers al navegador, abans de pujar-los.
 *
 * Els límits NO es defineixen aquí: venen de `GET /config`, que els llegeix de
 * les variables d'entorn del backend. Així no hi ha manera que frontend i
 * backend divergeixin.
 *
 * El backend continua sent l'autoritat (respon 413 / 415). Això és només UX,
 * i té un motiu concret: quan el backend rebutja un cos massa gran, el proxy de
 * Next talla la connexió i el navegador acabaria veient un 500 genèric en
 * comptes del missatge real.
 */

import type { ClientConfig } from "@/lib/types";

/** Valor per a l'atribut `accept` d'un <input type="file">. */
export function fileAccept(config: ClientConfig): string {
  return config.allowed_mime_types.join(",");
}

export function uploadHint(config: ClientConfig): string {
  return `PDF, imatge o document ofimàtic (màx. ${config.max_upload_size_mb} MB).`;
}

/** Retorna el motiu del rebuig, o null si el fitxer és acceptable. */
export function validateFile(file: File | null, config: ClientConfig): string | null {
  if (!file) return null;
  if (file.size === 0) {
    return "El fitxer és buit.";
  }
  if (file.size > config.max_upload_size_mb * 1024 * 1024) {
    const size = (file.size / (1024 * 1024)).toFixed(1);
    return `El fitxer ocupa ${size} MB i el màxim és ${config.max_upload_size_mb} MB.`;
  }
  if (file.type && !config.allowed_mime_types.includes(file.type)) {
    return "Aquest tipus de fitxer no està permès.";
  }
  return null;
}
