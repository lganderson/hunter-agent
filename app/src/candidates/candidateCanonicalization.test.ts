import assert from "node:assert/strict";
import test from "node:test";

import { candidateStatusCounts, canonicalCandidateRows } from "./candidateCanonicalization.ts";

function visibleAcrossPools(companyCandidates, discoveryCandidates) {
  return [
    ...canonicalCandidateRows(companyCandidates).map(candidate => ({ ...candidate, source: "company" })),
    ...canonicalCandidateRows(discoveryCandidates).map(candidate => ({ ...candidate, source: "discovery" })),
  ];
}

test("company=new plus discovery=duplicate renders one Needs decision row", () => {
  const rows = visibleAcrossPools(
    [{ id: "CP3864", status: "new", is_canonical: true, canonical_status: "new" }],
    [{ id: "DC0600", status: "duplicate", is_canonical: false, canonical_status: "new" }],
  );

  assert.deepEqual(rows.map(row => [row.id, row.status, row.source]), [
    ["CP3864", "new", "company"],
  ]);
});

test("linked ignored records render one Ignored row", () => {
  const rows = visibleAcrossPools(
    [{ id: "CP3533", status: "ignored", is_canonical: true, canonical_status: "ignored" }],
    [{ id: "DC0157", status: "duplicate", is_canonical: false, canonical_status: "ignored" }],
  );

  assert.equal(rows.length, 1);
  assert.equal(rows[0].status, "ignored");
});

test("a linked ingested record does not render in Needs decision", () => {
  const rows = visibleAcrossPools(
    [{ id: "CP0001", status: "new", is_canonical: true, canonical_status: "pursued" }],
    [{ id: "DC0001", status: "duplicate", is_canonical: false, canonical_status: "pursued" }],
  );

  assert.equal(rows.filter(row => row.status === "new").length, 0);
  assert.equal(rows.filter(row => row.status === "pursued").length, 1);
});

test("linked roles do not duplicate across Tracked Companies and Discovery", () => {
  const rows = visibleAcrossPools(
    [{ id: "CP0001", status: "new", is_canonical: true, canonical_status: "new" }],
    [{ id: "DC0001", status: "duplicate", is_canonical: false, canonical_status: "new" }],
  );

  assert.deepEqual(rows.map(row => row.id), ["CP0001"]);
});

test("status counts equal the canonical visible rows", () => {
  const candidates = [
    { id: "CP0001", status: "new", is_canonical: true, canonical_status: "new" },
    { id: "CP0002", status: "new", is_canonical: true, canonical_status: "ignored" },
    { id: "CP0003", status: "new", is_canonical: false, canonical_status: "new" },
  ];
  const rows = canonicalCandidateRows(candidates);

  assert.deepEqual(candidateStatusCounts(candidates), { new: 1, ignored: 1 });
  assert.equal(Object.values(candidateStatusCounts(candidates)).reduce((sum, count) => sum + count, 0), rows.length);
});

test("unlinked candidates retain their source and status", () => {
  const rows = visibleAcrossPools(
    [{ id: "CP0001", status: "ignored", is_canonical: true, canonical_status: "ignored" }],
    [{ id: "DC0001", status: "new", is_canonical: true, canonical_status: "new" }],
  );

  assert.deepEqual(rows.map(row => [row.id, row.status, row.source]), [
    ["CP0001", "ignored", "company"],
    ["DC0001", "new", "discovery"],
  ]);
});
