import { useCallback, useMemo } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { appShellToViewState } from "./readModelAdapters";
import { appShellQueryOptions, useAppShell } from "./readModelQueries";
import { readModelQueryKeys } from "./queryKeys";
import type { AppShell, EntityDetail } from "./readModelTypes";
import type { Action, AppState, Application } from "./types";

export type ActionUpdateResult = {
  action: Action;
  posting: Application | null;
};

export function useHunterData() {
  const queryClient = useQueryClient();
  const shellQuery = useAppShell();
  const data = useMemo(
    () => shellQuery.data ? appShellToViewState(shellQuery.data) : null,
    [shellQuery.data]
  );

  const refresh = useCallback(async (): Promise<AppState> => {
    await queryClient.invalidateQueries({
      queryKey: readModelQueryKeys.appShell(),
      refetchType: "none"
    });
    const shell = await queryClient.fetchQuery(appShellQueryOptions());
    return appShellToViewState(shell);
  }, [queryClient]);

  const applyActionUpdate = useCallback((result: ActionUpdateResult) => {
    queryClient.setQueryData<EntityDetail<"action">>(
      readModelQueryKeys.entityDetail("action", result.action.id),
      current => current ? { ...current, item: { ...current.item, ...result.action } } : current
    );
    queryClient.setQueryData<AppShell>(readModelQueryKeys.appShell(), current => current ? {
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
    } : current);
  }, [queryClient]);

  const applyApplicationUpdate = useCallback((application: Application) => {
    const tagList = String(application.tags || "")
      .split(",")
      .map(tag => tag.trim())
      .filter(Boolean);
    queryClient.setQueryData<EntityDetail<"application">>(
      readModelQueryKeys.entityDetail("application", application.id),
      current => current ? { ...current, item: { ...current.item, ...application } } : current
    );
    queryClient.setQueryData<AppShell>(readModelQueryKeys.appShell(), current => current ? {
      ...current,
      generated_at: new Date().toISOString(),
      applications: current.applications.map(currentApplication => currentApplication.id === application.id
        ? { ...currentApplication, ...application, tag_list: tagList }
        : currentApplication)
    } : current);
  }, [queryClient]);

  return {
    data,
    error: shellQuery.error instanceof Error ? shellQuery.error.message : shellQuery.error ? String(shellQuery.error) : "",
    refresh,
    applyActionUpdate,
    applyApplicationUpdate
  };
}
