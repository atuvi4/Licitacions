"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";

interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** Codi HTTP de l'últim error de l'API (404, 403…), o null. */
  status: number | null;
  /** Torna a llegir i retorna el que ha tornat l'API (o null si ha fallat). */
  reload: () => Promise<T | null>;
  setData: (value: T) => void;
}

/** Lectura simple d'un recurs de l'API amb estats de carrega i error. */
export function useResource<T>(path: string | null): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<number | null>(null);

  const reload = useCallback(async (): Promise<T | null> => {
    if (!path) return null;
    setLoading(true);
    setError(null);
    setStatus(null);
    try {
      const value = await api.get<T>(path);
      setData(value);
      return value;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Error desconegut");
      // Sense `instanceof`: els tests que simulen `@/lib/api` no exporten la classe.
      const code = caught && typeof caught === "object" ? (caught as { status?: unknown }).status : undefined;
      setStatus(typeof code === "number" ? code : null);
      return null;
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, loading, error, status, reload, setData };
}

/** Envolta una accio d'escriptura: estat d'enviament i error llegible. */
export function useMutation() {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  // L'error complet de l'API, per als casos on cal reaccionar al codi concret
  // (per exemple, oferir vincular un document duplicat en comptes de pujar-lo).
  const [apiError, setApiError] = useState<ApiError | null>(null);

  const run = useCallback(async (action: () => Promise<void>): Promise<boolean> => {
    setSaving(true);
    setError(null);
    setFieldErrors({});
    setApiError(null);
    try {
      await action();
      return true;
    } catch (caught) {
      if (caught instanceof ApiError) {
        setApiError(caught);
        setError(caught.message);
        if (caught.fields.length > 0) {
          setFieldErrors(
            Object.fromEntries(caught.fields.map((field) => [field.field, field.message])),
          );
        }
      } else {
        setError(caught instanceof Error ? caught.message : "Error desconegut");
      }
      return false;
    } finally {
      setSaving(false);
    }
  }, []);

  const reset = useCallback(() => {
    setError(null);
    setFieldErrors({});
    setApiError(null);
  }, []);

  return { saving, error, apiError, fieldErrors, run, reset };
}
