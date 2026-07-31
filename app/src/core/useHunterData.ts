import { useCallback, useEffect, useState } from "react";
import { getAppState } from "./api";
import type { Action, AppState, Application } from "./types";

export type ActionUpdateResult = {
  action: Action;
  posting: Application | null;
};

export function useHunterData() {
  const [data, setData] = useState<AppState | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const next = await getAppState();
    setData(next);
    setError("");
    return next;
  }, []);

  const applyActionUpdate = useCallback((result: ActionUpdateResult) => {
    setData(current => {
      if (!current) return current;
      return {
        ...current,
        generated_at: new Date().toISOString(),
        actions: current.actions.map(action => action.id === result.action.id
          ? { ...action, ...result.action }
          : action),
        applications: result.posting
          ? current.applications.map(application => application.id === result.posting?.id
            ? { ...application, ...result.posting }
            : application)
          : current.applications
      };
    });
  }, []);

  const applyApplicationUpdate = useCallback((application: Application) => {
    const tagList = String(application.tags || "")
      .split(",")
      .map(tag => tag.trim())
      .filter(Boolean);
    setData(current => {
      if (!current) return current;
      return {
        ...current,
        generated_at: new Date().toISOString(),
        applications: current.applications.map(currentApplication => currentApplication.id === application.id
          ? { ...currentApplication, ...application, tag_list: tagList }
          : currentApplication)
      };
    });
  }, []);

  useEffect(() => {
    refresh().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
    });
  }, [refresh]);

  return { data, error, refresh, applyActionUpdate, applyApplicationUpdate };
}
