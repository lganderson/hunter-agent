import type { CompanyPostingCandidate } from "../core/types";

export const RECOMMENDED_FIT_SCORE = 45;
export const STRONG_FIT_SCORE = 70;
export const RECOMMENDED_CANDIDATE_LIMIT = 25;

export const CANDIDATE_FILTERS = [
  { id: "recommended", label: "Recommended" },
  { id: "new", label: "New" },
  { id: "all", label: "All" },
  { id: "ignored", label: "Ignored" },
  { id: "ingested", label: "Ingested" },
  { id: "unavailable", label: "Unavailable" }
] as const;

export type CandidateFilter = typeof CANDIDATE_FILTERS[number]["id"];

export function candidateRank(status: string) {
  const ranks: Record<string, number> = { new: 0, unavailable: 1, ignored: 2, ingested: 3 };
  return ranks[status] ?? 3;
}

export function isRecommendedCandidate(candidate: CompanyPostingCandidate, latestCheckAt: string) {
  return isCurrentNewCandidate(candidate, latestCheckAt) && candidateFitScore(candidate) >= RECOMMENDED_FIT_SCORE;
}

export function isCurrentNewCandidate(candidate: CompanyPostingCandidate, latestCheckAt: string) {
  if (candidate.status !== "new") return false;
  return latestCheckAt ? candidate.last_seen_at === latestCheckAt : true;
}

export function candidateMatchesFilter(candidate: CompanyPostingCandidate, filter: CandidateFilter, latestCheckAt: string) {
  if (filter === "recommended") return isRecommendedCandidate(candidate, latestCheckAt);
  if (filter === "new") return isCurrentNewCandidate(candidate, latestCheckAt);
  if (filter === "all") return true;
  return candidate.status === filter;
}

export function candidateEmptyMessage(filter: CandidateFilter, totalCount: number) {
  if (!totalCount) return "No posting candidates have been recorded.";
  if (filter === "recommended") return "No recommended matches yet. Use New or All to review the full scan.";
  if (filter === "new") return "No new candidates were found in the latest scan.";
  return "No posting candidates match this filter.";
}

export function candidateFitScore(candidate: CompanyPostingCandidate) {
  const parsed = Number.parseInt(candidate.fit_score || "0", 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function fitBand(candidate: CompanyPostingCandidate) {
  const score = candidateFitScore(candidate);
  if (score >= STRONG_FIT_SCORE) return "strong";
  if (score >= RECOMMENDED_FIT_SCORE) return "consider";
  return "low";
}
