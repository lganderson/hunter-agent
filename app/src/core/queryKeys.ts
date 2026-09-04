import type {
  CandidateListFilters,
  CandidatePool,
  NormalizedCandidateListFilters
} from "./readModelTypes";
import type { EntityResource } from "./readModelTypes";

export const DEFAULT_CANDIDATE_PAGE_LIMIT = 50;
export const MAX_CANDIDATE_PAGE_LIMIT = 100;

function normalizeStatuses(status: CandidateListFilters["status"]): readonly string[] {
  const values = Array.isArray(status) ? status : status ? [status] : [];
  return [...new Set(values.map(value => value.trim().toLowerCase()).filter(Boolean))].sort();
}

function normalizeValues(values: readonly string[] | undefined, transform: (value: string) => string) {
  return [...new Set((values || []).map(transform).filter(Boolean))].sort();
}

export function normalizeCandidateListFilters(
  filters: CandidateListFilters = {}
): NormalizedCandidateListFilters {
  const requestedLimit = filters.limit ?? DEFAULT_CANDIDATE_PAGE_LIMIT;
  const limit = Number.isFinite(requestedLimit)
    ? Math.min(MAX_CANDIDATE_PAGE_LIMIT, Math.max(1, Math.trunc(requestedLimit)))
    : DEFAULT_CANDIDATE_PAGE_LIMIT;
  const requestedMinimumFit = filters.minimumFitScore ?? 0;
  const minimumFitScore = Number.isFinite(requestedMinimumFit)
    ? Math.min(100, Math.max(0, Math.trunc(requestedMinimumFit)))
    : 0;

  return {
    limit,
    cursor: filters.cursor?.trim() ?? "",
    search: filters.search?.trim().toLowerCase() ?? "",
    statuses: normalizeStatuses(filters.status),
    minimumFitScore,
    companyId: filters.companyId?.trim().toUpperCase() ?? "",
    companyIds: normalizeValues(filters.companyIds, value => value.trim().toUpperCase()),
    interestStatuses: normalizeValues(filters.interestStatuses, value => value.trim().toLowerCase()),
    trackingStatus: filters.trackingStatus?.trim().toLowerCase() ?? "",
    fitBand: filters.fitBand || "all",
    latestOnly: filters.latestOnly === true,
    laneMatchOnly: filters.laneMatchOnly === true,
    sort: filters.sort || "fit",
    direction: filters.direction || "desc",
    includeExcludedCompanies: filters.includeExcludedCompanies === true,
    includeOutOfScope: filters.includeOutOfScope === true
  };
}

export const readModelQueryKeys = {
  all: ["read-models"] as const,
  appShell: () => ["read-models", "app-shell"] as const,
  candidates: () => ["read-models", "candidates"] as const,
  candidateLists: (pool: CandidatePool) => ["read-models", "candidates", pool, "list"] as const,
  candidateList: (pool: CandidatePool, filters: CandidateListFilters = {}) =>
    ["read-models", "candidates", pool, "list", normalizeCandidateListFilters(filters)] as const,
  candidateDetails: (pool: CandidatePool) => ["read-models", "candidates", pool, "detail"] as const,
  candidateDetail: (pool: CandidatePool, id: string, includeExcludedCompanies = false) =>
    [
      "read-models",
      "candidates",
      pool,
      "detail",
      { id: id.trim().toUpperCase(), includeExcludedCompanies }
    ] as const,
  entityDetails: (resource: EntityResource) => ["read-models", resource, "detail"] as const,
  entityDetail: (resource: EntityResource, id: string) =>
    ["read-models", resource, "detail", { id: id.trim().toUpperCase() }] as const
};
