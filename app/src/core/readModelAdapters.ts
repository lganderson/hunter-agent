import type {
  AppShell,
  AppShellCompany,
  CandidateDetail,
  CompanyCandidateListItem,
  DiscoveryCandidateListItem
} from "./readModelTypes";
import type {
  AppState,
  CompanyPostingCandidate,
  DiscoveryCandidate,
  DiscoveryCandidateDetail
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

export function appShellToViewState(shell: AppShell): AppState {
  return {
    generated_at: shell.generated_at,
    generated_date: shell.generated_date,
    applications: shell.applications,
    actions: shell.actions,
    workflow: shell.workflow,
    contacts: shell.contacts,
    application_contacts: shell.application_contacts,
    companies: expandShellCompanies(shell),
    company_merge_suggestions: shell.company_merge_suggestions,
    company_contacts: shell.company_contacts,
    company_career_sources: shell.company_career_sources,
    company_posting_candidates: [],
    company_career_scans: [],
    candidate_review_audit: shell.candidate_review_audit,
    discovery_searches: shell.discovery_searches,
    discovery_candidates: [],
    discovery_preference_suggestions: shell.discovery_preference_suggestions,
    dismissed_suggestion_ids: shell.dismissed_suggestion_ids
  };
}

export function companyListItemToSummary(
  item: CompanyCandidateListItem
): CompanyPostingCandidate {
  return {
    ...item,
    is_canonical: true,
    canonical_source: "company"
  };
}

export function discoveryListItemToSummary(
  item: DiscoveryCandidateListItem
): DiscoveryCandidate {
  const { company, ...candidate } = item;
  return {
    ...candidate,
    company: company?.name || "",
    search_id: item.search_ids[0] || "",
    search_ids_json: JSON.stringify(item.search_ids),
    is_canonical: true,
    canonical_source: "discovery"
  };
}

export function discoveryDetailToCandidate(
  detail: CandidateDetail<"discovery">
): DiscoveryCandidateDetail {
  const { company, ...candidate } = detail.item;
  return { ...candidate, company: company?.name || "" };
}
