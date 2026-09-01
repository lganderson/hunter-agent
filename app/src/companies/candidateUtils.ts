import type { CompanyPostingCandidate } from "../core/types";

export const RECOMMENDED_FIT_SCORE = 45;
export const STRONG_FIT_SCORE = 70;
export const RECOMMENDED_CANDIDATE_LIMIT = 25;

export const CANDIDATE_FILTERS = [
  { id: "needs-decision", label: "Needs decision" },
  { id: "pursued", label: "Pursued" },
  { id: "ignored", label: "Ignored" },
] as const;

export type CandidateFilter = typeof CANDIDATE_FILTERS[number]["id"];

export function candidateRank(status: string) {
  const ranks: Record<string, number> = { new: 0, unavailable: 1, ignored: 2, pursued: 3 };
  return ranks[status] ?? 3;
}

export function isRecommendedCandidate(candidate: CompanyPostingCandidate, latestCheckAt: string) {
  return candidate.review_state === "ready"
    && isCurrentNewCandidate(candidate, latestCheckAt)
    && candidateFitScore(candidate) >= RECOMMENDED_FIT_SCORE;
}

export function isCurrentNewCandidate(candidate: CompanyPostingCandidate, latestCheckAt: string) {
  if (candidate.status !== "new") return false;
  return latestCheckAt ? candidate.last_seen_at === latestCheckAt : true;
}

export function candidateMatchesFilter(candidate: CompanyPostingCandidate, filter: CandidateFilter, latestCheckAt: string) {
  if (filter === "needs-decision") return candidate.status === "new";
  return candidate.status === filter;
}

export function candidateEmptyMessage(filter: CandidateFilter, totalCount: number) {
  if (!totalCount) return "No posting candidates have been recorded.";
  if (filter === "needs-decision") return "No roles need a decision.";
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
