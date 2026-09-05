import { useCallback, useMemo } from "react";
import { useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { appShellToViewState } from "./readModelAdapters";
import { appShellQueryOptions, useAppShell } from "./readModelQueries";
import { readModelQueryKeys } from "./queryKeys";
import type { AppShell, CandidateDetail, CandidatePage, EntityDetail } from "./readModelTypes";
import type { Action, AppState, Application, CompanyPostingCandidate, DiscoveryCandidate } from "./types";

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

  const applyCompanyCandidateUpdates = useCallback((candidates: CompanyPostingCandidate[]) => {
    const updates = new Map(candidates.map(candidate => [candidate.id, candidate]));
    if (!updates.size) return;
    queryClient.setQueriesData<InfiniteData<CandidatePage<"company">>>(
      { queryKey: readModelQueryKeys.candidateLists("company") },
      current => current ? {
        ...current,
        pages: current.pages.map(page => ({
          ...page,
          items: page.items.map(candidate => updates.has(candidate.id)
            ? { ...candidate, ...updates.get(candidate.id) }
            : candidate)
        }))
      } : current
    );
    candidates.forEach(candidate => {
      queryClient.setQueriesData<CandidateDetail<"company">>(
        { queryKey: readModelQueryKeys.candidateDetails("company") },
        current => current?.item.id === candidate.id
          ? { ...current, item: { ...current.item, ...candidate } }
          : current
      );
    });
  }, [queryClient]);

  const applyDiscoveryCandidateUpdate = useCallback((
    candidate: DiscoveryCandidate,
    posting: Application | null = null,
    removePostingId = ""
  ) => {
    const { company: _legacyCompanyName, ...fields } = candidate;
    // Mutation responses lack the read model's canonical status. Keep it in sync
    // so canonicalization cannot restore the previous decision while refetching.
    const candidateUpdate = { ...fields, canonical_status: candidate.status };
    queryClient.setQueriesData<InfiniteData<CandidatePage<"discovery">>>(
      { queryKey: readModelQueryKeys.candidateLists("discovery") },
      current => current ? {
        ...current,
        pages: current.pages.map(page => ({
          ...page,
          items: page.items.map(currentCandidate => currentCandidate.id === candidate.id
            ? { ...currentCandidate, ...candidateUpdate }
            : currentCandidate)
        }))
      } : current
    );
    queryClient.setQueriesData<CandidateDetail<"discovery">>(
      { queryKey: readModelQueryKeys.candidateDetails("discovery") },
      current => current?.item.id === candidate.id
        ? { ...current, item: { ...current.item, ...candidateUpdate } }
        : current
    );
    queryClient.setQueryData<AppShell>(readModelQueryKeys.appShell(), current => {
      if (!current) return current;
      const existingPosting = posting
        ? current.applications.some(application => application.id === posting.id)
        : false;
      return {
        ...current,
        generated_at: new Date().toISOString(),
        applications: current.applications
          .filter(application => !removePostingId || application.id !== removePostingId)
          .map(application => application.id === posting?.id ? { ...application, ...posting } : application)
          .concat(posting && !existingPosting ? [posting] : [])
      };
    });
    // Reconcile totals, facets, pagination, and linked candidates across both pools.
    void queryClient.invalidateQueries({ queryKey: readModelQueryKeys.candidates() });
  }, [queryClient]);

  return {
    data,
    error: shellQuery.error instanceof Error ? shellQuery.error.message : shellQuery.error ? String(shellQuery.error) : "",
    refresh,
    applyActionUpdate,
    applyApplicationUpdate,
    applyCompanyCandidateUpdates,
    applyDiscoveryCandidateUpdate
  };
}
