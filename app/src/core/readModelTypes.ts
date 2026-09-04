import type {
  Action,
  Application,
  ApplicationContact,
  Company,
  CompanyCareerSource,
  CompanyContact,
  CompanyMergeSuggestion,
  CompanyPostingCandidate,
  Contact,
  DiscoveryCandidate,
  DiscoveryPreferenceSuggestion,
  DiscoverySearch,
  Workflow
} from "./types";

export type CandidatePool = "company" | "discovery";

export type CandidateListFilters = {
  limit?: number;
  cursor?: string;
  search?: string;
  status?: string | readonly string[];
  minimumFitScore?: number;
  companyId?: string;
  trackingStatus?: string;
  includeExcludedCompanies?: boolean;
};

export type CompanyCandidateListFilters = CandidateListFilters;
export type DiscoveryCandidateListFilters = CandidateListFilters;

/** Acquisition context changes what a saved search runs, never which Discovery rows are returned. */
export type DiscoveryAcquisitionContext = {
  searchId?: string;
};

export type NormalizedCandidateListFilters = {
  limit: number;
  cursor: string;
  search: string;
  statuses: readonly string[];
  minimumFitScore: number;
  companyId: string;
  trackingStatus: string;
  includeExcludedCompanies: boolean;
};

export type CandidateCompanySummary = Pick<
  Company,
  "id" | "name" | "interest_status" | "tracking_status" | "industry" | "company_size" | "website" | "careers_url"
>;

type CandidateListItemBase = {
  id: string;
  company_id: string;
  company: CandidateCompanySummary | null;
  title: string;
  url: string;
  location: string;
  work_mode: string;
  source_platform: string;
  last_seen_at: string;
  status: string;
  canonical_status: string;
  fit_score: string;
  fit_checked_at: string;
  review_state: "ready" | "needs-qualification" | "needs-detail" | "needs-freshness" | "failed-extraction";
  matching_posting_ids: string[];
  description_excerpt: string;
  description_truncated: boolean;
};

export type CompanyCandidateListItem = CandidateListItemBase & {
  category: string;
  source_job_id: string;
  scan_state: string;
  last_verified_at: string;
  first_seen_at: string;
  fit_summary: string;
  lane_match: string;
  discovery_candidate_id: string;
};

export type DiscoveryCandidateListItem = CandidateListItemBase & {
  search_ids: string[];
  canonical_url: string;
  captured_at: string;
  processing_status: string;
  qualification_status: DiscoveryCandidate["qualification_status"];
  qualification_reason: string;
  detail_last_error: string;
  detail_next_action: string;
  review_next_action: string;
  fit_summary: string;
  freshness_status: string;
  freshness_checked_at: string;
  detail_state: DiscoveryCandidate["detail_state"];
  recommendation_eligible: boolean;
  source_trust: DiscoveryCandidate["source_trust"];
  source_trust_label: string;
  source_confidence: string;
  lane_match: string;
  role_family_id: string;
  role_family: string;
  company_candidate_id: string;
};

export type CandidatePageCounts = {
  source: number;
  eligible: number;
  canonical: number;
  filtered: number;
  returned: number;
  excluded_companies: number;
  ignored_sources: number;
};

export type CandidatePageFacets = {
  statuses: Array<{ value: string; count: number }>;
  tracking: Array<{ value: string; count: number }>;
  companies: Array<{ value: string; label: string; count: number }>;
};

export type CandidatePageAudit = {
  stable_revision: boolean;
  filters: {
    search: string;
    status: string[];
    minimum_fit_score: number;
    tracking_status: string;
    company_id: string;
    include_excluded_companies: boolean;
    search_id: string;
  };
  canonical_hidden_count: number;
  search_context: { id: string; name: string; affects_rows: false } | null;
};

export type CandidatePage<TPool extends CandidatePool> = {
  api_version: number;
  pool: TPool;
  revision: number;
  items: TPool extends "company" ? CompanyCandidateListItem[] : DiscoveryCandidateListItem[];
  counts: CandidatePageCounts;
  facets: CandidatePageFacets;
  page: {
    limit: number;
    offset: number;
    has_more: boolean;
    next_cursor: string;
  };
  audit: CandidatePageAudit;
};

export type CandidateDetail<TPool extends CandidatePool> = {
  api_version: number;
  pool: TPool;
  revision: number;
  item: TPool extends "company"
    ? CompanyPostingCandidate & {
        company: CandidateCompanySummary | null;
        source_urls: string[];
      }
    : Omit<DiscoveryCandidate, "company"> & { company: CandidateCompanySummary | null };
  audit: {
    stable_revision: boolean;
    excluded_company: boolean;
    includes_full_description: boolean;
    includes_notes: boolean;
  };
};

export type AppShellApplication = Omit<Application, "notes">;
export type AppShellAction = Pick<
  Action,
  | "id"
  | "application_id"
  | "company"
  | "role"
  | "type"
  | "title"
  | "status"
  | "priority"
  | "due_date"
  | "is_complete"
  | "is_open"
  | "is_overdue"
  | "is_due_soon"
  | "days_until_due"
  | "sort_due"
>;
export type AppShellContact = Omit<Contact, "notes">;
export type AppShellCompany = Partial<Company> &
  Pick<Company, "id" | "name" | "interest_status" | "tracking_status"> & {
    company_metadata_suggestion_count: number;
  };
export type AppShellCompanyTable = {
  fields: Array<keyof AppShellCompany>;
  rows: Array<Array<string | number | boolean | null>>;
};
export type AppShellCareerSource = Omit<CompanyCareerSource, "evidence" | "notes">;

export type AppShell = {
  api_version: number;
  generated_at: string;
  generated_date: string;
  revision: number;
  applications: AppShellApplication[];
  actions: AppShellAction[];
  workflow: Workflow;
  contacts: AppShellContact[];
  application_contacts: ApplicationContact[];
  companies: AppShellCompanyTable | AppShellCompany[];
  company_merge_suggestions: CompanyMergeSuggestion[];
  company_contacts: CompanyContact[];
  company_career_sources: AppShellCareerSource[];
  discovery_searches: DiscoverySearch[];
  discovery_preference_suggestions: DiscoveryPreferenceSuggestion[];
  dismissed_suggestion_ids: string[];
  candidate_counts: Record<CandidatePool, number>;
  candidate_review_audit: {
    excluded_company_candidate_count: number;
    discovery_excluded_company_candidate_count: number;
    tracked_company_excluded_company_candidate_count: number;
  };
  audit: {
    stable_revision: boolean;
    omitted_large_fields: string[];
  };
};

export type EntityResource = "application" | "action" | "contact" | "company";

export type EntityDetailItemMap = {
  application: Application;
  action: Action;
  contact: Contact;
  company: Company & { company_career_source: CompanyCareerSource | null };
};

export type EntityDetail<TResource extends EntityResource> = {
  api_version: number;
  resource: TResource;
  revision: number;
  item: EntityDetailItemMap[TResource];
  audit: {
    stable_revision: boolean;
    includes_omitted_fields: true;
  };
};
