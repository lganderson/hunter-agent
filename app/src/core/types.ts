export type Application = {
  id: string;
  company_id: string;
  company: string;
  role: string;
  location: string;
  work_mode: string;
  source: string;
  source_url: string;
  compensation: string;
  stage: string;
  outcome: string;
  tags: string;
  priority: string;
  date_found: string;
  date_applied: string;
  next_action_id: string;
  next_action: string;
  next_action_date: string;
  open_action_count: number;
  next_action_warning: string;
  contact: string;
  resume_version: string;
  cover_letter: string;
  notes: string;
  tag_list: string[];
  is_closed: boolean;
  is_active: boolean;
  is_overdue: boolean;
  is_due_soon: boolean;
  days_until_next_action: number | null;
  sort_due: string;
};

export type PostingSnapshot = {
  id: string;
  application_id: string;
  source_url: string;
  final_url: string;
  captured_at: string;
  http_status: string;
  capture_method: "fetch" | "manual" | "ai-web" | string;
  capture_model: string;
  sources_json: string;
  content_hash: string;
  content_text: string;
  warnings: string;
  source_html_char_count: number;
};

export type Action = {
  id: string;
  application_id: string;
  company: string;
  role: string;
  type: string;
  title: string;
  description: string;
  status: string;
  priority: string;
  due_date: string;
  created_date: string;
  completed_date: string;
  source: string;
  related_url: string;
  notes: string;
  is_complete: boolean;
  is_open: boolean;
  is_overdue: boolean;
  is_due_soon: boolean;
  days_until_due: number | null;
  sort_due: string;
};

export type Contact = {
  id: string;
  name: string;
  company: string;
  role: string;
  email: string;
  linkedin: string;
  relationship: string;
  status: string;
  last_contacted: string;
  next_follow_up: string;
  notes: string;
};

export type Company = {
  id: string;
  name: string;
  aliases: string;
  interest_status: string;
  tracking_status: "discovered" | "tracked";
  discovered_at: string;
  last_seen_at: string;
  website: string;
  careers_url: string;
  industry: string;
  company_size: string;
  company_profile_url: string;
  company_metadata_source: string;
  company_metadata_checked_at: string;
  company_metadata_suggestions_json: string;
  company_research_status: string;
  company_discovery_source: string;
  company_discovery_source_url: string;
  company_discovery_query: string;
  company_discovery_evidence: string;
  company_location_fit: string;
  company_location: string;
  company_remote_policy: string;
  company_location_evidence: string;
  company_location_checked_at: string;
  company_fit_score: string;
  company_fit_summary: string;
  company_fit_checked_at: string;
  company_evaluation_status: string;
  company_evaluation_version: string;
  company_evaluation_checked_at: string;
  company_evaluation_error: string;
  discovery_role_count: number;
  recommended_discovery_role_count: number;
  ignored_role_count: number;
  pursued_role_count: number;
  tracking_recommendation: string;
  decision_recommendation: string;
  notes: string;
  last_checked_at: string;
  last_check_status: string;
};

export type CompanyMergeSuggestion = {
  id: string;
  keep_company_id: string;
  keep_company_name: string;
  merge_company_id: string;
  merge_company_name: string;
  reason: string;
  match_key: string;
};

export type CompanyDiscoveryRunResult = {
  focus: string;
  sizes: string[];
  sources: string[];
  locations: string[];
  remote_region: string;
  metro_area: string;
  companies: Company[];
  location_verification_companies: Company[];
  review_count: number;
  location_verification_count: number;
  new_count: number;
  updated_count: number;
  already_tracked_count: number;
  skipped_size_count: number;
  skipped_not_interested_count: number;
  research_count: number;
  source_runs: Array<{
    source: string;
    label: string;
    query: string;
    found_count: number;
    qualified_count: number;
    lane_counts?: Record<string, number>;
    location_counts?: Record<string, number>;
  }>;
  errors: string[];
};

export type CompanyDiscoveryJob = {
  id: string;
  job_type?: "company-discovery" | "company-evaluation";
  status: "queued" | "running" | "completed" | "failed";
  phase: string;
  message: string;
  completed_steps: number;
  total_steps: number;
  source: string;
  started_at: string;
  updated_at: string;
  completed_at: string;
  error: string;
  request: {
    focus?: string;
    sizes?: string[];
    sources?: string[];
    locations?: string[];
    remote_region?: string;
    metro_area?: string;
    company_ids?: string[];
    tracking_status?: string;
    force?: boolean;
    reason?: string;
    profile?: CompanyEvaluationProfile;
  };
  result: CompanyDiscoveryRunResult | CompanyEvaluationRunResult | null;
};

export type CandidateEnrichmentResult = {
  target_count: number;
  processed_count: number;
  changed_count: number;
  ready_count: number;
  needs_input_count: number;
  remaining_count: number;
  manual_review_count: number;
  state_counts: Record<DiscoveryDetailState, number>;
  errors: string[];
};

export type CandidateEnrichmentJob = {
  id: string;
  job_type: "candidate-enrichment" | "candidate-discovery";
  status: "queued" | "running" | "completed" | "failed";
  phase: string;
  message: string;
  completed_steps: number;
  total_steps: number;
  source: string;
  started_at: string;
  updated_at: string;
  completed_at: string;
  error: string;
  request: {
    search_id?: string;
    candidate_id?: string;
    limit?: number;
    use_browser_fallback?: boolean;
    enrichment_limit?: number;
  };
  result: CandidateEnrichmentResult | DiscoveryRunResult | null;
};

export type CompanyEvaluationProfile = {
  focus: string;
  sizes: string[];
  locations: string[];
  remote_region: string;
  metro_area: string;
};

export type CompanyEvaluationRunResult = {
  target_count: number;
  evaluated_count: number;
  ready_count: number;
  needs_verification_count: number;
  failed_count: number;
  company_ids: string[];
  evaluation_version: string;
  profile: CompanyEvaluationProfile;
  errors: string[];
};

export type ApplicationContact = {
  application_id: string;
  contact_id: string;
};

export type CompanyContact = {
  company_id: string;
  contact_id: string;
  created_at: string;
};

export type CompanyCareerSource = {
  company_id: string;
  source_url: string;
  platform_type: string;
  config_json: string;
  evidence: string;
  discovered_at: string;
  last_verified_at: string;
  status: string;
  notes: string;
};

export type CompanyPostingCandidate = {
  id: string;
  company_id: string;
  title: string;
  url: string;
  location: string;
  work_mode: string;
  category: string;
  source_platform: string;
  source_job_id: string;
  matched_queries: string;
  description_excerpt: string;
  description_hash: string;
  score_inputs_hash: string;
  normalization_warnings: string;
  scan_state: string;
  last_verified_at: string;
  status: string;
  first_seen_at: string;
  last_seen_at: string;
  fit_score: string;
  fit_summary: string;
  fit_checked_at: string;
  notes: string;
  lane_match: string;
  discovery_candidate_id: string;
  review_state: "ready" | "needs-detail" | "needs-freshness" | "failed-extraction";
  requisition_ids: string[];
  matching_posting_ids: string[];
};

export type CompanyCareerScan = {
  company_id: string;
  checked_at: string;
  platform_type: string;
  status: string;
  requests_succeeded: string;
  requests_failed: string;
  extracted_count: string;
  unique_candidate_count: string;
  new_count: string;
  recommended_count: string;
  unavailable_count: string;
  verification_count: string;
  verification_skipped_count: string;
  errors_json: string;
};

export type DiscoverySearch = {
  id: string;
  name: string;
  keywords: string;
  role_family_ids: string[];
  lanes: DiscoverySearchLaneDefinition[];
  excluded_terms: string[];
  created_at: string;
  updated_at: string;
  last_opened_at: string;
  last_run_at: string;
  last_run_summary: DiscoveryLastRunSummary;
};

export type DiscoveryPreferenceSuggestion = {
  id: string;
  search_id: string;
  search_name: string;
  term: string;
  ignored_count: number;
  sample_titles: string[];
  reason: string;
};

export type DiscoveryLastRunSummary = {
  evaluated_count?: number;
  known_count?: number;
  associated_count?: number;
  qualified_count?: number;
  screened_count?: number;
  skip_reasons?: Record<string, number>;
  screened_reasons?: Record<string, number>;
  found_count?: number;
  new_count?: number;
  updated_count?: number;
  needs_details_count?: number;
  lane_unmatched_count?: number;
  skipped_count?: number;
  duplicate_count?: number;
  limited_count?: number;
  enriched_count?: number;
  company_researched_count?: number;
  company_suggestion_count?: number;
  role_family_counts?: Record<string, number>;
  sources?: DiscoverySourceRun[];
  errors?: string[];
  enrichment?: DiscoveryEnrichmentResult;
};

export type DiscoveryCandidate = {
  id: string;
  search_id: string;
  search_ids: string[];
  search_ids_json: string;
  company_id: string;
  company?: string;
  title: string;
  url: string;
  canonical_url: string;
  location: string;
  work_mode: string;
  source_platform: string;
  captured_at: string;
  last_seen_at: string;
  status: string;
  processing_status: string;
  detail_attempt_count: string;
  detail_last_attempt_at: string;
  detail_last_error: string;
  detail_state: DiscoveryDetailState;
  detail_gaps: DiscoveryDetailGap[];
  detail_next_action: string;
  review_state: "ready" | "needs-detail" | "needs-freshness" | "failed-extraction";
  review_next_action: string;
  requisition_ids: string[];
  matching_posting_ids: string[];
  fit_score: string;
  fit_summary: string;
  fit_checked_at: string;
  description_text: string;
  description_excerpt: string;
  warnings: string;
  source_urls_json: string;
  source_urls: string[];
  freshness_status: string;
  freshness_checked_at: string;
  ignore_reason: string;
  ignore_reason_detail: string;
  fit_strengths: string[];
  fit_gaps: string[];
  source_confidence: string;
  source_trust: "employer" | "network" | "aggregator" | "unverified" | "closed";
  source_trust_label: string;
  is_direct_employer_source: boolean;
  recommendation_eligible: boolean;
  lane_match: string;
  role_family_id: string;
  role_family: string;
  responsibility_signals: string[];
  ingested_application_id: string;
  notes: string;
};

export type DiscoveryDetailState = "ready" | "pending-enrichment" | "source-verification" | "needs-input";

export type DiscoveryDetailGap = {
  id: "missing-identity" | "missing-description" | "missing-location" | "source-verification";
  label: string;
  automatic: boolean;
};

export type DiscoverySearchLaneDefinition = {
  id: string;
  label: string;
  location: string;
  work_modes: Array<"on-site" | "hybrid" | "remote">;
};

export type DiscoverySearchLane = DiscoverySearchLaneDefinition & {
  url: string;
};

export type DiscoverySourceRun = {
  source: string;
  label: string;
  query_family: string;
  query_family_label: string;
  lane_id: string;
  lane_label: string;
  query: string;
  found_count: number;
  page_count: number;
  engine: string;
};

export type DiscoveryRunResult = {
  search: DiscoverySearch;
  captured: DiscoveryCandidate[];
  evaluated_count: number;
  known_count: number;
  associated_count: number;
  qualified_count: number;
  screened_count: number;
  skip_reasons: Record<string, number>;
  screened_reasons: Record<string, number>;
  found_count: number;
  new_count: number;
  updated_count: number;
  needs_details_count: number;
  lane_unmatched_count: number;
  skipped_count: number;
  duplicate_count: number;
  limited_count: number;
  enriched_count: number;
  company_researched_count: number;
  company_suggestion_count: number;
  role_family_counts: Record<string, number>;
  sources: DiscoverySourceRun[];
  errors: string[];
  enrichment?: DiscoveryEnrichmentResult;
};

export type DiscoveryEnrichmentResult = {
  processed_count: number;
  posting_checked_count: number;
  posting_enriched_count: number;
  company_researched_count: number;
  company_research_remaining_count: number;
  unavailable_count: number;
  remaining_count: number;
  ready_count: number;
  errors: string[];
};

export type WorkflowStage = {
  id: string;
  label: string;
  sort_order: string;
  is_terminal: string;
  is_active: string;
};

export type WorkflowActionType = {
  id: string;
  label: string;
  description: string;
  default_priority: string;
  default_due_days: string;
  allowed_stages: string;
  sort_order: string;
  is_active: string;
};

export type Workflow = {
  stages: WorkflowStage[];
  action_types: WorkflowActionType[];
  outcomes: string[];
};

export type CandidateReviewAudit = {
  excluded_company_candidate_count: number;
  discovery_excluded_company_candidate_count: number;
  tracked_company_excluded_company_candidate_count: number;
};

export type AppState = {
  generated_at: string;
  generated_date: string;
  applications: Application[];
  actions: Action[];
  workflow: Workflow;
  contacts: Contact[];
  application_contacts: ApplicationContact[];
  companies: Company[];
  company_merge_suggestions: CompanyMergeSuggestion[];
  company_contacts: CompanyContact[];
  company_career_sources: CompanyCareerSource[];
  company_posting_candidates: CompanyPostingCandidate[];
  company_career_scans: CompanyCareerScan[];
  candidate_review_audit: CandidateReviewAudit;
  discovery_searches: DiscoverySearch[];
  discovery_candidates: DiscoveryCandidate[];
  discovery_preference_suggestions: DiscoveryPreferenceSuggestion[];
  dismissed_suggestion_ids: string[];
};

export type SettingsStatus = {
  provider: string;
  model: string;
  api_base: string;
  search_goals: string;
  fit_signals: FitSignals;
  token_configured: boolean;
  adzuna_configured: boolean;
  resume: ResumeStatus;
  api_usage: ApiUsageSummary;
};

export type ApiUsageTotals = {
  request_count: number;
  input_tokens: number;
  cached_input_tokens: number;
  uncached_input_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  total_tokens: number;
  tool_call_count: number;
  web_search_call_count: number;
};

export type ApiUsageSummary = {
  totals: ApiUsageTotals;
  features: Array<ApiUsageTotals & { feature: string }>;
};

export type FitSignals = {
  role_terms: string;
  domain_terms: string;
  seniority_terms: string;
  search_terms: string;
  low_match_terms: string;
  exclusion_terms: string;
  strength_terms: string;
};

export type ResumeStatus = {
  filename: string;
  uploaded_at: string;
  text_char_count: number;
  extraction_status: string;
  preview: string;
  preview_char_count: number;
  preview_truncated: boolean;
  configured: boolean;
};

export type ResumeText = {
  filename: string;
  text: string;
  text_char_count: number;
  configured: boolean;
};

export type ResumeChange = {
  id: string;
  old_text: string;
  new_text: string;
  reason: string;
  keywords: string[];
};

export type ResumePlan = {
  application_id: string;
  source_filename: string;
  source_hash: string;
  summary: string;
  matched_keywords: string[];
  missing_keywords: string[];
  changes: ResumeChange[];
};

export type ResumeVersion = {
  id: string;
  application_id: string;
  created_at: string;
  guidance: string;
  source_filename: string;
  changes: ResumeChange[];
  warnings: string[];
  docx_available: boolean;
  pdf_available: boolean;
};

export type ResumeTailoringStatus = {
  base_resume: {
    configured: boolean;
    filename: string;
    format_preserving_supported: boolean;
  };
  versions: ResumeVersion[];
};

export type ApplicationUpdates = Partial<
  Pick<
    Application,
    | "company_id"
    | "company"
    | "role"
    | "location"
    | "work_mode"
    | "source"
    | "source_url"
    | "compensation"
    | "stage"
    | "outcome"
    | "priority"
    | "date_found"
    | "date_applied"
    | "tags"
    | "contact"
    | "resume_version"
    | "cover_letter"
    | "notes"
  >
>;

export type ActionUpdates = Partial<Pick<Action, "title" | "description" | "type" | "priority" | "due_date" | "related_url" | "notes">>;

export type ContactUpdates = Partial<Omit<Contact, "id">>;

export type CompanyUpdates = Partial<Omit<Company, "id" | "last_checked_at" | "last_check_status">>;

export type DiscoverySearchUpdates = Pick<DiscoverySearch, "name" | "keywords" | "role_family_ids" | "lanes" | "excluded_terms">;

export type DiscoveryCandidateDetails = Partial<
  Pick<
    DiscoveryCandidate,
    | "company_id"
    | "title"
    | "canonical_url"
    | "location"
    | "work_mode"
    | "description_text"
    | "notes"
  >
> & { company_name?: string };

export type AgentChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export type AgentContext = {
  route: string;
  pathname: string;
  entity_type?: "posting" | "company";
  entity_id?: string;
  label?: string;
  query: Record<string, string>;
};

export type AgentToolCall = {
  name: string;
  ok: boolean;
  arguments?: Record<string, unknown>;
  error?: string;
  receipt?: string;
};

export type AgentChatHistoryMessage = AgentChatMessage & {
  id: number;
  tool_calls: AgentToolCall[];
  context: Partial<AgentContext>;
  created_at: string;
};

export type AgentChatResponse = {
  message: string;
  tool_calls: AgentToolCall[];
  mutated: boolean;
};
