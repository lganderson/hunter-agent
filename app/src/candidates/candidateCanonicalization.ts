type CanonicalCandidate = {
  status: string;
  is_canonical?: boolean;
  canonical_status?: string;
};

export function canonicalCandidateRows<T extends CanonicalCandidate>(candidates: T[]): T[] {
  return candidates
    .filter(candidate => candidate.is_canonical !== false)
    .map(candidate => (
      candidate.canonical_status && candidate.canonical_status !== candidate.status
        ? { ...candidate, status: candidate.canonical_status }
        : candidate
    ));
}

export function candidateStatusCounts<T extends CanonicalCandidate>(candidates: T[]) {
  return canonicalCandidateRows(candidates).reduce<Record<string, number>>((counts, candidate) => {
    counts[candidate.status] = (counts[candidate.status] || 0) + 1;
    return counts;
  }, {});
}
