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
  contact: string;
  resume_version: string;
  cover_letter: string;
  notes: string;
  posting_file: string;
  posting_markdown: string;
  posting_file_exists: boolean;
  tag_list: string[];
  is_closed: boolean;
  is_active: boolean;
  is_overdue: boolean;
  is_due_soon: boolean;
  days_until_next_action: number | null;
  sort_due: string;
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
  website: string;
  careers_url: string;
  notes: string;
  last_checked_at: string;
  last_check_status: string;
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
  status: string;
  first_seen_at: string;
  last_seen_at: string;
  fit_score: string;
  fit_summary: string;
  fit_checked_at: string;
  notes: string;
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

export type AppState = {
  generated_at: string;
  generated_date: string;
  applications: Application[];
  actions: Action[];
  workflow: Workflow;
  contacts: Contact[];
  application_contacts: ApplicationContact[];
  companies: Company[];
  company_contacts: CompanyContact[];
  company_career_sources: CompanyCareerSource[];
  company_posting_candidates: CompanyPostingCandidate[];
};

export type SettingsStatus = {
  provider: string;
  model: string;
  api_base: string;
  search_goals: string;
  fit_signals: FitSignals;
  token_configured: boolean;
  resume: ResumeStatus;
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

export type ApplicationUpdates = Partial<
  Pick<
    Application,
    | "company_id"
    | "company"
    | "stage"
    | "outcome"
    | "priority"
    | "date_applied"
    | "tags"
    | "contact"
    | "resume_version"
    | "cover_letter"
    | "notes"
  >
>;

export type ContactUpdates = Partial<Omit<Contact, "id">>;

export type CompanyUpdates = Partial<Omit<Company, "id" | "last_checked_at" | "last_check_status">>;

export type AgentChatMessage = {
  role: "user" | "assistant";
  content: string;
};

export type AgentToolCall = {
  name: string;
  ok: boolean;
  arguments?: Record<string, unknown>;
  error?: string;
};

export type AgentChatResponse = {
  message: string;
  tool_calls: AgentToolCall[];
  mutated: boolean;
};
