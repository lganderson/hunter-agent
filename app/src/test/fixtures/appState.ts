import type { AppState, Company, DiscoveryCandidate, DiscoverySearch } from "../../core/types";

function company(id: string, name: string, industry: string): Company {
  return {
    id,
    name,
    aliases: "",
    interest_status: "interested",
    tracking_status: "tracked",
    discovered_at: "2026-01-01T00:00:00Z",
    last_seen_at: "2026-01-02T00:00:00Z",
    website: `https://${id.toLowerCase()}.example.invalid`,
    careers_url: `https://${id.toLowerCase()}.example.invalid/careers`,
    industry,
    company_size: "51-200",
    company_profile_url: "",
    company_metadata_source: "synthetic-test-fixture",
    company_metadata_checked_at: "2026-01-02T00:00:00Z",
    company_metadata_suggestions_json: "[]",
    company_research_status: "ready",
    company_discovery_source: "synthetic-test-fixture",
    company_discovery_source_url: "",
    company_discovery_query: "",
    company_discovery_evidence: "Synthetic browser-test fixture.",
    company_location_fit: "eligible",
    company_location: "United States",
    company_remote_policy: "Remote",
    company_location_evidence: "Synthetic browser-test fixture.",
    company_location_checked_at: "2026-01-02T00:00:00Z",
    company_fit_score: "80",
    company_fit_summary: "Synthetic fit summary.",
    company_fit_checked_at: "2026-01-02T00:00:00Z",
    company_evaluation_status: "ready",
    company_evaluation_version: "test-v1",
    company_evaluation_checked_at: "2026-01-02T00:00:00Z",
    company_evaluation_error: "",
    discovery_role_count: 1,
    recommended_discovery_role_count: 1,
    ignored_role_count: 0,
    pursued_role_count: 0,
    tracking_recommendation: "track",
    decision_recommendation: "review",
    notes: "",
    last_checked_at: "2026-01-02T00:00:00Z",
    last_check_status: "ready"
  };
}

function search(id: string, name: string, roleFamilyId: string): DiscoverySearch {
  return {
    id,
    name,
    keywords: "synthetic product platform",
    role_family_ids: [roleFamilyId],
    lanes: [{ id: `${id}-lane`, label: "United States", location: "United States", work_modes: ["remote"] }],
    excluded_terms: [],
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    last_opened_at: "",
    last_run_at: "",
    last_run_summary: {}
  };
}

function discoveryCandidate(
  id: string,
  searchId: string,
  companyId: string,
  title: string,
  fitScore: string
): DiscoveryCandidate {
  return {
    id,
    search_id: searchId,
    search_ids: [searchId],
    search_ids_json: JSON.stringify([searchId]),
    company_id: companyId,
    title,
    url: `https://${id.toLowerCase()}.example.invalid/role`,
    canonical_url: `https://${id.toLowerCase()}.example.invalid/role`,
    location: "United States",
    work_mode: "remote",
    source_platform: "manual",
    captured_at: "2026-01-02T00:00:00Z",
    last_seen_at: "2026-01-02T00:00:00Z",
    status: "new",
    processing_status: "ready",
    qualification_status: "eligible",
    qualification_reason: "",
    detail_attempt_count: "1",
    detail_last_attempt_at: "2026-01-02T00:00:00Z",
    detail_last_error: "",
    detail_state: "ready",
    detail_gaps: [],
    detail_next_action: "",
    review_state: "ready",
    review_next_action: "Review role",
    requisition_ids: [],
    matching_posting_ids: [],
    fit_score: fitScore,
    fit_summary: "Synthetic match evidence for browser testing.",
    fit_checked_at: "2026-01-02T00:00:00Z",
    description_text: "Synthetic role description used only by local browser tests.",
    description_excerpt: "Synthetic role description.",
    warnings: "",
    source_urls_json: "[]",
    source_urls: [],
    acquisition_provenance_json: "[]",
    acquisition_provenance: [],
    freshness_status: "confirmed-open",
    freshness_checked_at: "2026-01-02T00:00:00Z",
    ignore_reason: "",
    ignore_reason_detail: "",
    fit_strengths: ["Synthetic strength"],
    fit_gaps: [],
    source_confidence: "high",
    source_trust: "employer",
    source_trust_label: "Employer",
    is_direct_employer_source: true,
    recommendation_eligible: true,
    lane_match: "true",
    role_family_id: "product-platform",
    role_family: "Product and platform strategy",
    responsibility_signals: ["platform strategy"],
    ingested_application_id: "",
    notes: "",
    is_canonical: true,
    canonical_source: "discovery",
    canonical_status: "new"
  };
}

export const syntheticAppState: AppState = {
  generated_at: "2026-01-02T00:00:00Z",
  generated_date: "2026-01-02",
  applications: [],
  actions: [],
  workflow: { stages: [], action_types: [], outcomes: [] },
  contacts: [],
  application_contacts: [],
  companies: [
    company("CO1001", "Atlas Labs", "Developer tools"),
    company("CO1002", "Nova Systems", "Collaboration software")
  ],
  company_merge_suggestions: [],
  company_contacts: [],
  company_career_sources: [],
  company_posting_candidates: [],
  company_career_scans: [],
  candidate_review_audit: {
    excluded_company_candidate_count: 0,
    discovery_excluded_company_candidate_count: 0,
    tracked_company_excluded_company_candidate_count: 0
  },
  discovery_searches: [
    search("DS1001", "Product and Platform Strategy", "product-platform"),
    search("DS1002", "Technical Program Leadership", "technical-program")
  ],
  discovery_candidates: [
    discoveryCandidate("DC1001", "DS1001", "CO1001", "Principal Platform Product Lead", "91"),
    discoveryCandidate("DC1002", "DS1002", "CO1002", "Senior Technical Program Lead", "84")
  ],
  discovery_preference_suggestions: [],
  dismissed_suggestion_ids: []
};
