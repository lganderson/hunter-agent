import { describe, expect, it } from "vitest";

import { candidateStatusCounts, canonicalCandidateRows } from "./candidateCanonicalization";

type TestCandidate = {
  id: string;
  status: string;
  is_canonical?: boolean;
  canonical_status?: string;
};

function visibleAcrossPools(companyCandidates: TestCandidate[], discoveryCandidates: TestCandidate[]) {
  return [
    ...canonicalCandidateRows(companyCandidates).map(candidate => ({ ...candidate, source: "company" })),
    ...canonicalCandidateRows(discoveryCandidates).map(candidate => ({ ...candidate, source: "discovery" }))
  ];
}

describe("candidate canonicalization", () => {
  it("company=new plus discovery=duplicate renders one Needs decision row", () => {
    const rows = visibleAcrossPools(
      [{ id: "CP3864", status: "new", is_canonical: true, canonical_status: "new" }],
      [{ id: "DC0600", status: "duplicate", is_canonical: false, canonical_status: "new" }]
    );

    expect(rows.map(row => [row.id, row.status, row.source])).toEqual([
      ["CP3864", "new", "company"]
    ]);
  });

  it("linked ignored records render one Ignored row", () => {
    const rows = visibleAcrossPools(
      [{ id: "CP3533", status: "ignored", is_canonical: true, canonical_status: "ignored" }],
      [{ id: "DC0157", status: "duplicate", is_canonical: false, canonical_status: "ignored" }]
    );

    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("ignored");
  });

  it("a linked ingested record does not render in Needs decision", () => {
    const rows = visibleAcrossPools(
      [{ id: "CP0001", status: "new", is_canonical: true, canonical_status: "pursued" }],
      [{ id: "DC0001", status: "duplicate", is_canonical: false, canonical_status: "pursued" }]
    );

    expect(rows.filter(row => row.status === "new")).toHaveLength(0);
    expect(rows.filter(row => row.status === "pursued")).toHaveLength(1);
  });

  it("linked roles do not duplicate across Tracked Companies and Discovery", () => {
    const rows = visibleAcrossPools(
      [{ id: "CP0001", status: "new", is_canonical: true, canonical_status: "new" }],
      [{ id: "DC0001", status: "duplicate", is_canonical: false, canonical_status: "new" }]
    );

    expect(rows.map(row => row.id)).toEqual(["CP0001"]);
  });

  it("status counts equal the canonical visible rows", () => {
    const candidates = [
      { id: "CP0001", status: "new", is_canonical: true, canonical_status: "new" },
      { id: "CP0002", status: "new", is_canonical: true, canonical_status: "ignored" },
      { id: "CP0003", status: "new", is_canonical: false, canonical_status: "new" }
    ];
    const rows = canonicalCandidateRows(candidates);

    expect(candidateStatusCounts(candidates)).toEqual({ new: 1, ignored: 1 });
    expect(Object.values(candidateStatusCounts(candidates)).reduce((sum, count) => sum + count, 0)).toBe(rows.length);
  });

  it("unlinked candidates retain their source and status", () => {
    const rows = visibleAcrossPools(
      [{ id: "CP0001", status: "ignored", is_canonical: true, canonical_status: "ignored" }],
      [{ id: "DC0001", status: "new", is_canonical: true, canonical_status: "new" }]
    );

    expect(rows.map(row => [row.id, row.status, row.source])).toEqual([
      ["CP0001", "ignored", "company"],
      ["DC0001", "new", "discovery"]
    ]);
  });
});
