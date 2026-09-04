import { normalizeCandidateListFilters } from "./queryKeys";
import type {
  AppShell,
  CandidateDetail,
  CandidateListFilters,
  CandidatePage,
  CandidatePool,
  DiscoveryAcquisitionContext,
  EntityDetail,
  EntityResource
} from "./readModelTypes";

export class ReadModelApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ReadModelApiError";
    this.status = status;
  }
}

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const result = (await response.json()) as { error?: string };
      message = result.error || message;
    } catch {
      // Preserve the status fallback for non-JSON server errors.
    }
    throw new ReadModelApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export function candidateListSearchParams(
  filters: CandidateListFilters = {},
  context: DiscoveryAcquisitionContext = {}
): URLSearchParams {
  const normalized = normalizeCandidateListFilters(filters);
  const query = new URLSearchParams();
  query.set("limit", String(normalized.limit));
  if (normalized.cursor) query.set("cursor", normalized.cursor);
  if (normalized.search) query.set("search", normalized.search);
  normalized.statuses.forEach(status => query.append("status", status));
  if (normalized.minimumFitScore > 0) {
    query.set("minimum_fit_score", String(normalized.minimumFitScore));
  }
  if (normalized.companyId) query.set("company_id", normalized.companyId);
  normalized.companyIds.forEach(companyId => query.append("company_id", companyId));
  normalized.interestStatuses.forEach(status => query.append("interest_status", status));
  if (normalized.trackingStatus) query.set("tracking_status", normalized.trackingStatus);
  if (normalized.fitBand !== "all") query.set("fit_band", normalized.fitBand);
  if (normalized.latestOnly) query.set("latest_only", "true");
  if (normalized.laneMatchOnly) query.set("lane_match_only", "true");
  if (normalized.sort !== "fit") query.set("sort", normalized.sort);
  if (normalized.direction !== "desc") query.set("direction", normalized.direction);
  if (normalized.includeExcludedCompanies) query.set("include_excluded_companies", "true");
  if (normalized.includeOutOfScope) query.set("include_out_of_scope", "true");
  const searchId = context.searchId?.trim().toUpperCase();
  if (searchId) query.set("search_id", searchId);
  return query;
}

export async function fetchAppShell(signal?: AbortSignal): Promise<AppShell> {
  return readJson<AppShell>(
    await fetch("/api/app-shell", { cache: "no-store", signal })
  );
}

async function fetchCandidatePage<TPool extends CandidatePool>(
  pool: TPool,
  filters: CandidateListFilters,
  context: DiscoveryAcquisitionContext,
  signal?: AbortSignal
): Promise<CandidatePage<TPool>> {
  const query = candidateListSearchParams(filters, pool === "discovery" ? context : {});
  return readJson<CandidatePage<TPool>>(
    await fetch(`/api/candidates/${pool}?${query.toString()}`, { cache: "no-store", signal })
  );
}

export function fetchCompanyCandidatePage(
  filters: CandidateListFilters = {},
  signal?: AbortSignal
): Promise<CandidatePage<"company">> {
  return fetchCandidatePage("company", filters, {}, signal);
}

export function fetchDiscoveryCandidatePage(
  filters: CandidateListFilters = {},
  context: DiscoveryAcquisitionContext = {},
  signal?: AbortSignal
): Promise<CandidatePage<"discovery">> {
  return fetchCandidatePage("discovery", filters, context, signal);
}

async function fetchCandidateDetail<TPool extends CandidatePool>(
  pool: TPool,
  id: string,
  includeExcludedCompanies: boolean,
  signal?: AbortSignal
): Promise<CandidateDetail<TPool>> {
  const query = new URLSearchParams({ id: id.trim().toUpperCase() });
  if (includeExcludedCompanies) query.set("include_excluded_companies", "true");
  return readJson<CandidateDetail<TPool>>(
    await fetch(`/api/candidates/${pool}/detail?${query.toString()}`, { cache: "no-store", signal })
  );
}

export function fetchCompanyCandidateDetail(
  id: string,
  includeExcludedCompanies = false,
  signal?: AbortSignal
): Promise<CandidateDetail<"company">> {
  return fetchCandidateDetail("company", id, includeExcludedCompanies, signal);
}

export function fetchDiscoveryCandidateDetail(
  id: string,
  includeExcludedCompanies = false,
  signal?: AbortSignal
): Promise<CandidateDetail<"discovery">> {
  return fetchCandidateDetail("discovery", id, includeExcludedCompanies, signal);
}

const ENTITY_RESOURCE_PATHS: Record<EntityResource, string> = {
  application: "applications",
  action: "actions",
  contact: "contacts",
  company: "companies"
};

export async function fetchEntityDetail<TResource extends EntityResource>(
  resource: TResource,
  id: string,
  signal?: AbortSignal
): Promise<EntityDetail<TResource>> {
  const query = new URLSearchParams({ id: id.trim().toUpperCase() });
  return readJson<EntityDetail<TResource>>(
    await fetch(`/api/${ENTITY_RESOURCE_PATHS[resource]}/detail?${query.toString()}`, {
      cache: "no-store",
      signal
    })
  );
}
