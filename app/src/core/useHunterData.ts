import { useCallback, useEffect, useState } from "react";
import { getAppState } from "./api";
import type { AppState } from "./types";

export function useHunterData() {
  const [data, setData] = useState<AppState | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const next = await getAppState();
    setData(next);
    setError("");
    return next;
  }, []);

  useEffect(() => {
    refresh().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
    });
  }, [refresh]);

  return { data, error, refresh };
}
