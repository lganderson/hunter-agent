import {
  infiniteQueryOptions,
  keepPreviousData,
  queryOptions,
  useInfiniteQuery,
  useQuery
} from "@tanstack/react-query";
import {
  fetchAppShell,
  fetchCompanyCandidateDetail,
  fetchCompanyCandidatePage,
  fetchDiscoveryCandidateDetail,
  fetchDiscoveryCandidatePage,
  fetchEntityDetail
} from "./readModelApi";
import { normalizeCandidateListFilters, readModelQueryKeys } from "./queryKeys";
import type {
  CandidateListFilters,
  CandidatePage,
  CandidatePool,
  DiscoveryAcquisitionContext,
  EntityResource
} from "./readModelTypes";

export function nextCandidatePageParam<TPool extends CandidatePool>(
  lastPage: Pick<CandidatePage<TPool>, "page">
): string | undefined {
  return lastPage.page.has_more && lastPage.page.next_cursor
    ? lastPage.page.next_cursor
    : undefined;
}

export function appShellQueryOptions() {
  return queryOptions({
    queryKey: readModelQueryKeys.appShell(),
    queryFn: ({ signal }) => fetchAppShell(signal)
  });
}

export function companyCandidateListQueryOptions(filters: CandidateListFilters = {}, enabled = true) {
  const normalized = normalizeCandidateListFilters(filters);
  return infiniteQueryOptions({
    queryKey: readModelQueryKeys.candidateList("company", filters),
    placeholderData: keepPreviousData,
    enabled,
    initialPageParam: normalized.cursor || undefined,
    queryFn: ({ pageParam, signal }) =>
      fetchCompanyCandidatePage({ ...filters, cursor: pageParam }, signal),
    getNextPageParam: nextCandidatePageParam
  });
}

export function discoveryCandidateListQueryOptions(
  filters: CandidateListFilters = {},
  context: DiscoveryAcquisitionContext = {},
  enabled = true
) {
  const normalized = normalizeCandidateListFilters(filters);
  return infiniteQueryOptions({
    // Saved-search context is intentionally absent: it configures acquisition, not the global review queue.
    queryKey: readModelQueryKeys.candidateList("discovery", filters),
    placeholderData: keepPreviousData,
    enabled,
    initialPageParam: normalized.cursor || undefined,
    queryFn: ({ pageParam, signal }) =>
      fetchDiscoveryCandidatePage({ ...filters, cursor: pageParam }, context, signal),
    getNextPageParam: nextCandidatePageParam
  });
}

export function companyCandidateDetailQueryOptions(
  id: string,
  includeExcludedCompanies = false
) {
  const normalizedId = id.trim().toUpperCase();
  return queryOptions({
    queryKey: readModelQueryKeys.candidateDetail("company", normalizedId, includeExcludedCompanies),
    queryFn: ({ signal }) =>
      fetchCompanyCandidateDetail(normalizedId, includeExcludedCompanies, signal),
    enabled: Boolean(normalizedId)
  });
}

export function discoveryCandidateDetailQueryOptions(
  id: string,
  includeExcludedCompanies = false
) {
  const normalizedId = id.trim().toUpperCase();
  return queryOptions({
    queryKey: readModelQueryKeys.candidateDetail("discovery", normalizedId, includeExcludedCompanies),
    queryFn: ({ signal }) =>
      fetchDiscoveryCandidateDetail(normalizedId, includeExcludedCompanies, signal),
    enabled: Boolean(normalizedId)
  });
}

export function entityDetailQueryOptions<TResource extends EntityResource>(
  resource: TResource,
  id: string,
  enabled = true
) {
  const normalizedId = id.trim().toUpperCase();
  return queryOptions({
    queryKey: readModelQueryKeys.entityDetail(resource, normalizedId),
    queryFn: ({ signal }) => fetchEntityDetail(resource, normalizedId, signal),
    enabled: enabled && Boolean(normalizedId)
  });
}

export function useAppShell() {
  return useQuery(appShellQueryOptions());
}

export function useCompanyCandidateList(filters: CandidateListFilters = {}, enabled = true) {
  return useInfiniteQuery(companyCandidateListQueryOptions(filters, enabled));
}

export function useDiscoveryCandidateList(
  filters: CandidateListFilters = {},
  context: DiscoveryAcquisitionContext = {},
  enabled = true
) {
  return useInfiniteQuery(discoveryCandidateListQueryOptions(filters, context, enabled));
}

export function useCompanyCandidateDetail(id: string, includeExcludedCompanies = false) {
  return useQuery(companyCandidateDetailQueryOptions(id, includeExcludedCompanies));
}

export function useDiscoveryCandidateDetail(id: string, includeExcludedCompanies = false) {
  return useQuery(discoveryCandidateDetailQueryOptions(id, includeExcludedCompanies));
}

export function useApplicationDetail(id: string, enabled = true) {
  return useQuery(entityDetailQueryOptions("application", id, enabled));
}

export function useActionDetail(id: string, enabled = true) {
  return useQuery(entityDetailQueryOptions("action", id, enabled));
}

export function useContactDetail(id: string, enabled = true) {
  return useQuery(entityDetailQueryOptions("contact", id, enabled));
}

export function useCompanyDetail(id: string, enabled = true) {
  return useQuery(entityDetailQueryOptions("company", id, enabled));
}
