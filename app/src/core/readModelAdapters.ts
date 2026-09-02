import type {
  AppShell,
  AppShellCompany,
  CandidateDetail,
  CompanyCandidateListItem,
  DiscoveryCandidateListItem
} from "./readModelTypes";
import type {
  AppState,
  Company,
  CompanyPostingCandidate,
  DiscoveryCandidate
} from "./types";

function expandShellCompanies(shell: AppShell): AppShellCompany[] {
  // Keep the adapter tolerant of the object-list shape during the local
  // strangler transition and in fixture responses from an older server.
  if (Array.isArray(shell.companies)) return shell.companies;
  const table = shell.companies;
  return table.rows.map(values => Object.fromEntries(
    table.fields.map((field, index) => [field, values[index] ?? ""])
  ) as AppShellCompany);
}

const EMPTY_COMPANY: Company = {
  id: "",
  name: "",
  aliases: "",
  interest_status: "neutral",
  tracking_status: "discovered",
  discovered_at: "",
  last_seen_at: "",
  website: "",
  careers_url: "",
  industry: "",
  company_size: "",
  company_profile_url: "",
  company_metadata_source: "",
  company_metadata_checked_at: "",
  company_metadata_suggestions_json: "",
  company_metadata_suggestion_count: 0,
  company_research_status: "",
  company_discovery_source: "",
  company_discovery_source_url: "",
  company_discovery_query: "",
  company_discovery_evidence: "",
  company_location_fit: "",
  company_location: "",
  company_remote_policy: "",
  company_location_evidence: "",
  company_location_checked_at: "",
  company_fit_score: "",
  company_fit_summary: "",
  company_fit_checked_at: "",
  company_evaluation_status: "",
  company_evaluation_version: "",
  company_evaluation_checked_at: "",
  company_evaluation_error: "",
  discovery_role_count: 0,
  recommended_discovery_role_count: 0,
  ignored_role_count: 0,
  pursued_role_count: 0,
  tracking_recommendation: "",
  decision_recommendation: "",
  notes: "",
  last_checked_at: "",
  last_check_status: ""
};

export function appShellToLegacyState(shell: AppShell): AppState {
  return {
    generated_at: shell.generated_at,
    generated_date: shell.generated_date,
    applications: shell.applications.map(application => ({ ...application, notes: "" })),
    actions: shell.actions.map(action => ({
      description: "",
      created_date: "",
      completed_date: "",
      source: "",
      related_url: "",
      notes: "",
      ...action
    })),
    workflow: shell.workflow,
    contacts: shell.contacts.map(contact => ({ ...contact, notes: "" })),
    application_contacts: shell.application_contacts,
    companies: expandShellCompanies(shell).map(company => ({ ...EMPTY_COMPANY, ...company })),
    company_merge_suggestions: shell.company_merge_suggestions,
    company_contacts: shell.company_contacts,
    company_career_sources: shell.company_career_sources.map(source => ({
      ...source,
      evidence: "",
      notes: ""
    })),
    company_posting_candidates: [],
    company_career_scans: [],
    candidate_review_audit: shell.candidate_review_audit,
    discovery_searches: shell.discovery_searches,
    discovery_candidates: [],
    discovery_preference_suggestions: shell.discovery_preference_suggestions,
    dismissed_suggestion_ids: shell.dismissed_suggestion_ids
  };
}

export function companyListItemToLegacyCandidate(
  item: CompanyCandidateListItem
): CompanyPostingCandidate {
  return {
    ...item,
    matched_queries: "",
    description_hash: "",
    score_inputs_hash: "",
    normalization_warnings: "",
    notes: "",
    requisition_ids: [],
    is_canonical: true,
    canonical_source: "company"
  };
}

export function discoveryListItemToLegacyCandidate(
  item: DiscoveryCandidateListItem
): DiscoveryCandidate {
  const { company, ...candidate } = item;
  return {
    ...candidate,
    company: company?.name || "",
    search_id: item.search_ids[0] || "",
    search_ids_json: JSON.stringify(item.search_ids),
    detail_attempt_count: "",
    detail_last_attempt_at: "",
    detail_gaps: [],
    requisition_ids: [],
    description_text: "",
    warnings: "",
    source_urls_json: "",
    source_urls: [],
    ignore_reason: "",
    ignore_reason_detail: "",
    fit_strengths: [],
    fit_gaps: [],
    is_direct_employer_source: false,
    responsibility_signals: [],
    ingested_application_id: "",
    notes: "",
    is_canonical: true,
    canonical_source: "discovery"
  };
}

export function discoveryDetailToLegacyCandidate(
  detail: CandidateDetail<"discovery">
): DiscoveryCandidate {
  const { company, ...candidate } = detail.item;
  return { ...candidate, company: company?.name || "" };
}
