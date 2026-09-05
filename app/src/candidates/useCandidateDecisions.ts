import { useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import * as api from "../core/api";
import { readModelQueryKeys } from "../core/queryKeys";

/** Decisions can affect either pool, linked postings/actions, and company details. */
export async function refreshCandidateViews(client: QueryClient) {
  await Promise.all([
    client.invalidateQueries({ queryKey: readModelQueryKeys.candidates() }),
    client.invalidateQueries({ queryKey: readModelQueryKeys.appShell() }),
    ...(["company", "application", "action"] as const).map(resource =>
      client.invalidateQueries({ queryKey: readModelQueryKeys.entityDetails(resource) })
    )
  ]);
}

function useCandidateMutation<Args extends unknown[], Result>(request: (...args: Args) => Promise<Result>) {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: (args: Args) => request(...args),
    retry: false,
    // Re-read even after an interrupted response: the write may have completed.
    // Read models own canonical status, totals, and related records. Raw mutation
    // responses are deliberately not merged into those projections.
    onSettled: () => refreshCandidateViews(client)
  });
  return (...args: Args) => mutation.mutateAsync(args);
}

async function pursueMany<Result>(
  pursue: (id: string) => Promise<Result>,
  ids: string[],
  onProgress: (completed: number, total: number) => void
) {
  const results: Result[] = [];
  const failedIds: string[] = [];
  for (const [index, id] of ids.entries()) {
    onProgress(index, ids.length);
    try {
      results.push(await pursue(id));
    } catch {
      failedIds.push(id);
    }
  }
  return { results, failedIds };
}

const pursueCompanyBatch = (ids: string[], progress: (completed: number, total: number) => void) =>
  pursueMany(api.pursueCompanyCandidate, ids, progress);
const pursueDiscoveryBatch = (ids: string[], progress: (completed: number, total: number) => void) =>
  pursueMany(api.pursueDiscoveryCandidate, ids, progress);

export function useCompanyCandidateDecisions() {
  return {
    setStatus: useCandidateMutation(api.updateCompanyCandidate),
    setStatuses: useCandidateMutation(api.updateCompanyCandidates),
    pursue: useCandidateMutation(api.pursueCompanyCandidate),
    pursueMany: useCandidateMutation(pursueCompanyBatch)
  };
}

export function useDiscoveryCandidateDecisions() {
  return {
    setStatus: useCandidateMutation(api.updateDiscoveryCandidate),
    setStatuses: useCandidateMutation(api.updateDiscoveryCandidates),
    pursue: useCandidateMutation(api.pursueDiscoveryCandidate),
    pursueMany: useCandidateMutation(pursueDiscoveryBatch),
    markDuplicate: useCandidateMutation(api.markDiscoveryCandidateDuplicate),
    undo: useCandidateMutation(api.undoDiscoveryCandidateDecision)
  };
}
