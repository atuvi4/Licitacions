/**
 * Client HTTP contra el BFF (`/bff/*`), que Next.js reescriu cap a FastAPI.
 *
 * El navegador no coneix mai la URL real de l'API ni manipula cap token:
 * la sessio viatja en una cookie httpOnly que el proxy reenvia.
 */

export type ApiFieldError = { field: string; message: string; type: string };

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly fields: ApiFieldError[];
  readonly details: Record<string, unknown>;

  constructor(
    status: number,
    code: string,
    message: string,
    fields: ApiFieldError[] = [],
    details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields;
    this.details = details;
  }

  get isUnauthorized() {
    return this.status === 401;
  }
}

const BASE = "/bff";

async function parseError(response: Response): Promise<ApiError> {
  let code = "error";
  let message = `Error ${response.status}`;
  let fields: ApiFieldError[] = [];
  let details: Record<string, unknown> = {};
  try {
    const body = await response.json();
    if (body?.error) {
      code = body.error.code ?? code;
      message = body.error.message ?? message;
      details = body.error.details ?? {};
      fields = (details.fields as ApiFieldError[]) ?? [];
    }
  } catch {
    // Resposta sense cos JSON: ens quedem amb el missatge generic.
  }
  return new ApiError(response.status, code, message, fields, details);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    credentials: "same-origin",
    cache: "no-store",
    ...init,
  });

  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function withJsonBody(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) => request<T>(path, withJsonBody("POST", body)),
  patch: <T>(path: string, body: unknown) => request<T>(path, withJsonBody("PATCH", body)),
  put: <T>(path: string, body: unknown) => request<T>(path, withJsonBody("PUT", body)),
  // Alguns DELETE retornen l'entitat actualitzada (p. ex. treure un CPV d'un
  // projecte), per aixo el tipus de retorn es parametritzable.
  delete: <T = void>(path: string) => request<T>(path, { method: "DELETE" }),
  upload: <T>(path: string, form: FormData) => request<T>(path, { method: "POST", body: form }),
  /** Descarrega un fitxer respectant el nom que proposa l'API. */
  download: async (path: string, filename: string) => {
    const response = await fetch(`${BASE}${path}`, { credentials: "same-origin" });
    if (!response.ok) {
      throw await parseError(response);
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  },
};

/** Elimina els camps buits perque el PATCH no escrigui cadenes buides. */
export function cleanPayload<T extends Record<string, unknown>>(input: T): Record<string, unknown> {
  const output: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(input)) {
    if (value === "" || value === undefined) {
      output[key] = null;
    } else {
      output[key] = value;
    }
  }
  return output;
}
