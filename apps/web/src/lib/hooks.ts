"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/lib/api";

interface Resource<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  setData: (value: T) => void;
}

/** Lectura simple d'un recurs de l'API amb estats de carrega i error. */
export function useResource<T>(path: string | null): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!path) return;
    setLoading(true);
    setError(null);
    try {
      setData(await api.get<T>(path));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Error desconegut");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, loading, error, reload, setData };
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
