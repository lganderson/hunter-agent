import { describe, expect, it } from "vitest";
import { createHunterQueryClient } from "./queryClient";
import { readModelQueryKeys } from "./queryKeys";
import { candidateListSearchParams, ReadModelApiError } from "./readModelApi";
import { appShellToLegacyState, companyListItemToLegacyCandidate } from "./readModelAdapters";
import {
  discoveryCandidateListQueryOptions,
  nextCandidatePageParam
} from "./readModelQueries";
import type { AppShell } from "./readModelTypes";

describe("read model candidate query foundation", () => {
  it("expands compact companies and defaults action detail-only fields", () => {
    const shell = {
      api_version: 1,
      generated_at: "2026-09-01T12:00:00",
      generated_date: "2026-09-01",
      revision: 7,
      applications: [],
      actions: [{
        id: "AC0001",
        application_id: "AP0001",
        company: "Example",
        role: "Lead",
        type: "follow-up",
        title: "Send note",
        status: "open",
        priority: "high",
        due_date: "2026-09-02",
        is_complete: false,
        is_open: true,
        is_overdue: false,
        is_due_soon: true,
        days_until_due: 1,
        sort_due: "2026-09-02"
      }],
      workflow: {},
      contacts: [],
      application_contacts: [],
      companies: {
        fields: [
          "id", "name", "interest_status", "tracking_status",
          "company_fit_summary", "company_metadata_suggestion_count",
          "tracking_recommendation"
        ],
        rows: [["CO0001", "Example", "neutral", "discovered", "Strong fit", 3, "Worth reviewing"]]
      },
      company_merge_suggestions: [],
      company_contacts: [],
      company_career_sources: [],
      discovery_searches: [],
      discovery_preference_suggestions: [],
      dismissed_suggestion_ids: [],
      candidate_counts: { company: 0, discovery: 0 },
      candidate_review_audit: {
        excluded_company_candidate_count: 0,
        discovery_excluded_company_candidate_count: 0,
        tracked_company_excluded_company_candidate_count: 0
      },
      audit: { stable_revision: true, omitted_large_fields: [] }
    } as AppShell;

    const state = appShellToLegacyState(shell);
    expect(state.companies[0]).toMatchObject({
      id: "CO0001",
      company_fit_summary: "Strong fit",
      company_metadata_suggestion_count: 3,
      tracking_recommendation: "Worth reviewing"
    });
    expect(state.actions[0]).toMatchObject({
      id: "AC0001",
      description: "",
      source: "",
      related_url: "",
      notes: ""
    });
  });

  it("preserves company lane matches needed by the default review scope", () => {
    const candidate = companyListItemToLegacyCandidate({
      id: "CP0001",
      company_id: "CO0001",
      company: null,
      title: "Platform Lead",
      url: "https://example.invalid/jobs/1",
      location: "Chicago",
      work_mode: "hybrid",
      source_platform: "employer",
      last_seen_at: "2026-09-01",
      status: "new",
      canonical_status: "new",
      fit_score: "80",
      fit_summary: "Strong platform scope",
      fit_checked_at: "2026-09-01",
      review_state: "ready",
      matching_posting_ids: [],
      description_excerpt: "",
      description_truncated: false,
      category: "product",
      source_job_id: "1",
      scan_state: "current",
      last_verified_at: "2026-09-01",
      first_seen_at: "2026-09-01",
      lane_match: "Product and platform strategy",
      discovery_candidate_id: ""
    });

    expect(candidate.lane_match).toBe("Product and platform strategy");
  });

  it("normalizes equivalent filters into the same deterministic key", () => {
    const first = readModelQueryKeys.candidateList("company", {
      search: "  Platform Lead ",
      status: ["New", "pursued", "new"],
      companyId: " cp0001 ",
      trackingStatus: " Tracked ",
      minimumFitScore: 72.8
    });
    const second = readModelQueryKeys.candidateList("company", {
      search: "platform lead",
      status: ["Pursued", "NEW"],
      companyId: "CP0001",
      trackingStatus: "tracked",
      minimumFitScore: 72
    });

    expect(first).toEqual(second);
  });

  it("keeps Discovery acquisition context out of the global result key", () => {
    const first = discoveryCandidateListQueryOptions(
      { status: "new" },
      { searchId: "DS0001" }
    );
    const second = discoveryCandidateListQueryOptions(
      { status: "new" },
      { searchId: "DS0002" }
    );

    expect(first.queryKey).toEqual(second.queryKey);
    expect(JSON.stringify(first.queryKey)).toContain("new");
    expect(JSON.stringify(first.queryKey)).not.toContain("DS0001");
    expect(JSON.stringify(second.queryKey)).not.toContain("DS0002");
  });

  it("serializes ergonomic filters to the exact HTTP contract", () => {
    const query = candidateListSearchParams(
      {
        limit: 75,
        cursor: "opaque-cursor",
        search: " Product Platform ",
        status: ["Pursued", "new", "NEW"],
        minimumFitScore: 65,
        companyIds: ["cp0007", "CP0008"],
        interestStatuses: ["Neutral", "interested"],
        trackingStatus: "Tracked",
        fitBand: "recommended",
        latestOnly: true,
        laneMatchOnly: true,
        sort: "last_seen",
        direction: "asc",
        includeExcludedCompanies: true,
        includeOutOfScope: true
      },
      { searchId: "ds0005" }
    );

    expect(query.toString()).toBe(
      "limit=75&cursor=opaque-cursor&search=product+platform&status=new&status=pursued&minimum_fit_score=65&company_id=CP0007&company_id=CP0008&interest_status=interested&interest_status=neutral&tracking_status=tracked&fit_band=recommended&latest_only=true&lane_match_only=true&sort=last_seen&direction=asc&include_excluded_companies=true&include_out_of_scope=true&search_id=DS0005"
    );
  });

  it("uses the server default page size and only advances valid cursors", () => {
    expect(candidateListSearchParams().toString()).toBe("limit=50");
    expect(
      nextCandidatePageParam({
        page: { limit: 50, offset: 0, has_more: true, next_cursor: "next-page" }
      })
    ).toBe("next-page");
    expect(
      nextCandidatePageParam({
        page: { limit: 50, offset: 50, has_more: false, next_cursor: "" }
      })
    ).toBeUndefined();
    expect(
      nextCandidatePageParam({
        page: { limit: 50, offset: 0, has_more: true, next_cursor: "" }
      })
    ).toBeUndefined();
  });

  it("only retries a revision race once and disables focus/reconnect refetches", () => {
    const defaults = createHunterQueryClient().getDefaultOptions();

    expect(defaults.queries).toMatchObject({
      refetchOnWindowFocus: false,
      refetchOnReconnect: false
    });
    const retry = defaults.queries?.retry;
    expect(typeof retry).toBe("function");
    if (typeof retry === "function") {
      expect(retry(0, new Error("offline"))).toBe(false);
      expect(retry(0, new ReadModelApiError(409, "reload"))).toBe(true);
      expect(retry(1, new ReadModelApiError(409, "reload"))).toBe(false);
    }
    expect(defaults.mutations).toMatchObject({ retry: false });
  });
});
