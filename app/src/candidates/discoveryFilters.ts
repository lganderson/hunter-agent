import type { CandidateListFilters } from "../core/readModelTypes";
import { selectionParamValue, sortFromParams } from "../core/viewState";

export const DISCOVERY_SORT_KEYS = ["candidate", "company", "industry", "size", "match", "source", "freshness"] as const;
export type DiscoverySortKey = typeof DISCOVERY_SORT_KEYS[number];

export function discoverySelection(value: string | null): string[] {
  if (!value || value === "all") return [];
  if (value === "none") return ["__none__"];
  return value.split(",").map(item => {
    try { return decodeURIComponent(item); } catch { return item; }
  }).filter(Boolean);
}

export function discoverySelectedOptions(value: string | null, options: string[]) {
  return !value || value === "all" ? options : discoverySelection(value).filter(item => item !== "__none__");
}

export function discoverySelectionParam(selected: string[], options: string[], fallback: string[]) {
  return selectionParamValue(selected.map(encodeURIComponent), options.map(encodeURIComponent), fallback.map(encodeURIComponent));
}

export function discoveryFiltersFromParams(params: URLSearchParams): CandidateListFilters {
  const rawStatus = params.get("discovery_status") || "";
  const status = rawStatus === "ingested" ? "pursued"
    : ["pursued", "ignored", "duplicate", "unavailable"].includes(rawStatus) ? rawStatus : "new";
  const sort = sortFromParams(params, "discovery_sort", "discovery_direction", DISCOVERY_SORT_KEYS, { key: "match", direction: "desc" });
  return {
    search: params.get("discovery_q") || "",
    status,
    companyIds: discoverySelection(params.get("discovery_companies")),
    industries: discoverySelection(params.get("discovery_industries")),
    sizes: discoverySelection(params.get("discovery_sizes")),
    sources: discoverySelection(params.get("discovery_sources")),
    sort: sort.key,
    direction: sort.direction,
    reviewableOnly: true
  };
}
