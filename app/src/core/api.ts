import type {
  Action,
  ActionUpdates,
  AgentChatHistoryMessage,
  AgentChatResponse,
  AgentContext,
  Application,
  ApplicationUpdates,
  Company,
  CompanyCareerScan,
  CompanyCareerSource,
  CompanyDiscoveryRunResult,
  CompanyDiscoveryJob,
  CompanyPostingCandidate,
  CandidateEnrichmentJob,
  CompanyUpdates,
  Contact,
  ContactUpdates,
  DiscoveryCandidate,
  DiscoveryCandidateDetails,
  DiscoveryRunResult,
  DiscoverySearch,
  DiscoverySearchLane,
  DiscoverySearchUpdates,
  PostingSnapshot,
  ResumeText,
  ResumeChange,
  ResumePlan,
  ResumeTailoringStatus,
  ResumeVersion,
  SettingsStatus,
  Workflow,
  WorkflowActionType,
  WorkflowStage
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const result = (await response.json()) as { error?: string; code?: string; api_version?: number };
      message = result.error || message;
      if (result.code === "client_outdated") reloadForAgentUpdate(result.api_version);
    } catch {
      // Keep the original HTTP status when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

const AGENT_CHAT_API_VERSION = 2;
const AGENT_RELOAD_VERSION_KEY = "hunter-agent-chat-reload-version";

function reloadForAgentUpdate(apiVersion?: number): never {
  const version = String(apiVersion ?? "unknown");
  if (window.sessionStorage.getItem(AGENT_RELOAD_VERSION_KEY) !== version) {
    window.sessionStorage.setItem(AGENT_RELOAD_VERSION_KEY, version);
    window.location.reload();
    throw new Error("Hunter was updated. Reloading…");
  }
  throw new Error("Hunter was updated. Reload the page and try again.");
}

async function postJson<T>(url: string, payload: unknown, init: RequestInit = {}): Promise<T> {
  const response = await fetchWithLocalRetry(url, {
    ...init,
    method: "POST",
    headers: { "Content-Type": "application/json", ...init.headers },
    body: JSON.stringify(payload)
  });
  return readJson<T>(response);
}

async function fetchWithLocalRetry(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (error) {
    if (init.signal?.aborted) throw error;
    await new Promise(resolve => window.setTimeout(resolve, 500));
    try {
      return await fetch(url, init);
    } catch {
      const message = error instanceof Error ? error.message : String(error);
      throw new Error(`Could not reach Hunter local API. Restart the matching Hunter API server and reload the page. ${message}`);
    }
  }
}

export async function dismissSuggestion(id: string): Promise<void> {
  await postJson("/api/suggestions/dismiss", { id });
}

export async function restoreSuggestion(id: string): Promise<void> {
  await postJson("/api/suggestions/restore", { id });
}

export async function getPostingSnapshots(applicationId: string): Promise<PostingSnapshot[]> {
  const query = new URLSearchParams({ id: applicationId });
  const result = await readJson<{ snapshots: PostingSnapshot[] }>(
    await fetch(`/api/postings/snapshots?${query.toString()}`, { cache: "no-store" })
  );
  return result.snapshots;
}

export function archivePosting(applicationId: string): Promise<{ created: boolean; snapshot: PostingSnapshot; warnings: string[] }> {
  return postJson("/api/postings/archive", { id: applicationId });
}

export function saveManualPostingArchive(applicationId: string, content: string): Promise<{ created: boolean; snapshot: PostingSnapshot }> {
  return postJson("/api/postings/archive/manual", { id: applicationId, content });
}

export async function getSettings(): Promise<SettingsStatus> {
  return readJson<SettingsStatus>(await fetch("/api/settings"));
}

export function saveSettings(payload: {
  provider: string;
  model: string;
  api_base: string;
  search_goals: string;
  fit_signals: SettingsStatus["fit_signals"];
  api_token: string;
  adzuna_app_id: string;
  adzuna_app_key: string;
}): Promise<SettingsStatus> {
  return postJson<SettingsStatus>("/api/settings", payload);
}

export function uploadResume(filename: string, contentBase64: string): Promise<SettingsStatus> {
  return postJson<SettingsStatus>("/api/settings/resume", {
    filename,
    content_base64: contentBase64
  });
}

export function deleteResume(): Promise<SettingsStatus> {
  return postJson<SettingsStatus>("/api/settings/resume/delete", {});
}

export async function getResumeText(): Promise<ResumeText> {
  return readJson<ResumeText>(await fetch("/api/settings/resume/text", { cache: "no-store" }));
}

export async function getResumeTailoringStatus(applicationId: string): Promise<ResumeTailoringStatus> {
  const query = new URLSearchParams({ application_id: applicationId });
  return readJson<ResumeTailoringStatus>(await fetch(`/api/resumes/status?${query.toString()}`, { cache: "no-store" }));
}

export function planResumeChanges(applicationId: string, guidance: string): Promise<{ plan: ResumePlan }> {
  return postJson<{ plan: ResumePlan }>("/api/resumes/plan", {
    application_id: applicationId,
    guidance
  });
}

export function createResumeVersion(
  applicationId: string,
  guidance: string,
  sourceHash: string,
  changes: ResumeChange[]
): Promise<{ version: ResumeVersion }> {
  return postJson<{ version: ResumeVersion }>("/api/resumes/create", {
    application_id: applicationId,
    guidance,
    source_hash: sourceHash,
    changes
  });
}

export function resumeDownloadUrl(versionId: string, format: "docx" | "pdf"): string {
  const query = new URLSearchParams({ id: versionId, format });
  return `/api/resumes/download?${query.toString()}`;
}

export function generateActions(useAi: boolean): Promise<{ created: number; warnings: string[] }> {
  return postJson<{ created: number; warnings: string[] }>("/api/actions/generate", { use_ai: useAi });
}

export function updateAction(id: string, status: string): Promise<{ action: Action; posting: Application | null }> {
  return postJson<{ action: Action; posting: Application | null }>("/api/actions/update", { id, status });
}

export function createAction(applicationId: string, values: ActionUpdates): Promise<{ action: Action; posting: Application | null }> {
  return postJson<{ action: Action; posting: Application | null }>("/api/actions/create", { application_id: applicationId, values });
}

export function updateActionFields(id: string, updates: Partial<Pick<Action, "title" | "description" | "type" | "priority" | "due_date" | "related_url" | "notes">>): Promise<{ action: Action; posting: Application | null }> {
  return postJson<{ action: Action; posting: Application | null }>("/api/actions/update-fields", { id, updates });
}

export function makeNextAction(id: string): Promise<{ posting: Application | null }> {
  return postJson<{ posting: Application | null }>("/api/actions/make-next", { id });
}

export function updateApplication(id: string, updates: ApplicationUpdates): Promise<{ application: Application }> {
  return postJson<{ application: Application }>("/api/applications/update", { id, updates });
}

export function createApplication(values: ApplicationUpdates): Promise<{ application: Application }> {
  return postJson<{ application: Application }>("/api/applications/create", { values });
}

export function upsertContact(id: string, updates: ContactUpdates): Promise<{ contact: Contact }> {
  return postJson<{ contact: Contact }>("/api/contacts/upsert", { id, updates });
}

export function linkContact(contactId: string, applicationId: string): Promise<{ link: { application_id: string; contact_id: string } }> {
  return postJson<{ link: { application_id: string; contact_id: string } }>("/api/contacts/link", {
    contact_id: contactId,
    application_id: applicationId
  });
}

export function unlinkContact(contactId: string, applicationId: string): Promise<{ link: { application_id: string; contact_id: string } }> {
  return postJson<{ link: { application_id: string; contact_id: string } }>("/api/contacts/unlink", {
    contact_id: contactId,
    application_id: applicationId
  });
}

export function upsertCompany(id: string, updates: CompanyUpdates): Promise<{ company: Company }> {
  return postJson<{ company: Company }>("/api/companies/upsert", { id, updates });
}

export function archiveCompany(id: string): Promise<{ company: Company }> {
  return postJson<{ company: Company }>("/api/companies/archive", { id });
}

export function restoreCompany(id: string, interestStatus = "neutral"): Promise<{ company: Company }> {
  return postJson<{ company: Company }>("/api/companies/restore", { id, interest_status: interestStatus });
}

export type CompanyMetadataSuggestion = {
  id: string;
  field: "industry" | "company_size" | "company_profile_url" | "website";
  current: string;
  suggested: string;
  source_url: string;
  reason: string;
  observed_at: string;
};

export function researchCompany(id: string): Promise<{
  company: Company;
  applied_fields: string[];
  suggestions: CompanyMetadataSuggestion[];
  source_url: string;
}> {
  return postJson("/api/companies/research", { id });
}

export function trackCompany(id: string): Promise<{ company: Company }> {
  return postJson<{ company: Company }>("/api/companies/track", { id });
}

export function untrackCompany(id: string): Promise<{ company: Company }> {
  return postJson<{ company: Company }>("/api/companies/untrack", { id });
}

export function runCompanyDiscovery(payload: {
  focus: string;
  sizes: string[];
  sources: string[];
  locations: string[];
  remote_region?: string;
  metro_area?: string;
}): Promise<CompanyDiscoveryRunResult> {
  return postJson<CompanyDiscoveryRunResult>("/api/companies/discover", payload);
}

export function startCompanyDiscovery(payload: {
  focus: string;
  sizes: string[];
  sources: string[];
  locations: string[];
  remote_region: string;
  metro_area: string;
}): Promise<{ job: CompanyDiscoveryJob }> {
  return postJson<{ job: CompanyDiscoveryJob }>("/api/companies/discovery-jobs", payload);
}

export function startCompanyEvaluation(payload: {
  focus: string;
  sizes: string[];
  locations: string[];
  remote_region: string;
  metro_area: string;
  tracking_status?: string;
  force?: boolean;
  reason?: string;
}): Promise<{ job: CompanyDiscoveryJob }> {
  return postJson<{ job: CompanyDiscoveryJob }>("/api/companies/evaluation-jobs", payload);
}

export async function getCompanyDiscoveryJob(): Promise<{ job: CompanyDiscoveryJob | null }> {
  return readJson<{ job: CompanyDiscoveryJob | null }>(
    await fetch("/api/companies/discovery-jobs/current", { cache: "no-store" })
  );
}

export function resolveCompanyMetadataSuggestion(
  id: string,
  suggestionId: string,
  action: "apply" | "dismiss"
): Promise<{ company: Company }> {
  return postJson<{ company: Company }>("/api/companies/metadata-suggestions/resolve", {
    id,
    suggestion_id: suggestionId,
    action
  });
}

export type CompanyCheckResult = {
  company: Company;
  career_source: CompanyCareerSource | null;
  candidates: CompanyPostingCandidate[];
  new: CompanyPostingCandidate[];
  recommended: CompanyPostingCandidate[];
  unavailable_count: number;
  verification_count: number;
  verification_skipped_count: number;
  scan: CompanyCareerScan;
};

export type CompanyCheckAllResult = {
  checked_count: number;
  skipped_count: number;
  error_count: number;
  new_count: number;
  recommended_count: number;
  unavailable_count: number;
  verification_count: number;
  verification_skipped_count: number;
};

export function checkCompanyPostings(id: string, signal?: AbortSignal): Promise<CompanyCheckResult> {
  return postJson<CompanyCheckResult>("/api/companies/check", { id }, { signal });
}

export function checkAllCompanyPostings(): Promise<CompanyCheckAllResult> {
  return postJson<CompanyCheckAllResult>("/api/companies/check-all", {});
}

export function upsertDiscoverySearch(id: string, updates: DiscoverySearchUpdates): Promise<{ search: DiscoverySearch }> {
  return postJson<{ search: DiscoverySearch }>("/api/discovery/searches/upsert", { id, updates });
}

export function applyDiscoverySearchExclusions(
  id: string,
  excludedTerms: string[]
): Promise<{ candidate_ids: string[]; count: number }> {
  return postJson<{ candidate_ids: string[]; count: number }>(
    "/api/discovery/searches/apply-exclusions",
    { id, excluded_terms: excludedTerms }
  );
}

export function undoDiscoverySearchExclusions(
  candidateIds: string[]
): Promise<{ candidate_ids: string[]; count: number }> {
  return postJson<{ candidate_ids: string[]; count: number }>(
    "/api/discovery/searches/undo-exclusions",
    { candidate_ids: candidateIds }
  );
}

export function openLinkedInDiscoverySearch(
  id: string
): Promise<{ search: DiscoverySearch; url: string; lanes: DiscoverySearchLane[] }> {
  return postJson<{ search: DiscoverySearch; url: string; lanes: DiscoverySearchLane[] }>(
    "/api/discovery/searches/open-linkedin",
    { id }
  );
}

export function runDiscoverySearch(id: string): Promise<DiscoveryRunResult> {
  return postJson<DiscoveryRunResult>("/api/discovery/searches/run", { id });
}

export function continueDiscovery(id: string, enrichmentLimit = 10): Promise<DiscoveryRunResult> {
  return postJson<DiscoveryRunResult>("/api/discovery/continue", {
    id,
    enrichment_limit: enrichmentLimit
  });
}

export async function getCandidateEnrichmentJob(): Promise<{ job: CandidateEnrichmentJob | null }> {
  return readJson<{ job: CandidateEnrichmentJob | null }>(
    await fetch("/api/discovery/jobs/current", { cache: "no-store" })
  );
}

export function startCandidateDiscovery(payload: {
  search_id: string;
  use_browser_fallback?: boolean;
  enrichment_limit?: number;
}): Promise<{ job: CandidateEnrichmentJob }> {
  return postJson<{ job: CandidateEnrichmentJob }>("/api/discovery/search-jobs", payload);
}

export function startCandidateEnrichment(payload: {
  search_id?: string;
  candidate_id?: string;
  limit?: number;
} = {}): Promise<{ job: CandidateEnrichmentJob }> {
  return postJson<{ job: CandidateEnrichmentJob }>("/api/discovery/enrichment-jobs", payload);
}

export function captureDiscoveryCandidates(
  searchId: string,
  captureText: string,
  details: DiscoveryCandidateDetails = {}
): Promise<{ captured: DiscoveryCandidate[]; count: number }> {
  return postJson<{ captured: DiscoveryCandidate[]; count: number }>("/api/discovery/candidates/capture", {
    search_id: searchId,
    capture_text: captureText,
    details
  });
}

export function updateDiscoveryCandidateDetails(
  id: string,
  updates: DiscoveryCandidateDetails
): Promise<{ candidate: DiscoveryCandidate }> {
  return postJson<{ candidate: DiscoveryCandidate }>("/api/discovery/candidates/details", { id, updates });
}

export function updateDiscoveryCandidate(
  id: string,
  status: string,
  ignoreReason = "",
  ignoreReasonDetail = ""
): Promise<{ candidate: DiscoveryCandidate }> {
  return postJson<{ candidate: DiscoveryCandidate }>("/api/discovery/candidates/update", {
    id,
    status,
    ignore_reason: ignoreReason,
    ignore_reason_detail: ignoreReasonDetail
  });
}

export function updateDiscoveryCandidates(
  ids: string[],
  status: string,
  ignoreReason = "",
  ignoreReasonDetail = ""
): Promise<{ candidates: DiscoveryCandidate[]; count: number }> {
  return postJson<{ candidates: DiscoveryCandidate[]; count: number }>(
    "/api/discovery/candidates/bulk-update",
    {
      ids,
      status,
      ignore_reason: ignoreReason,
      ignore_reason_detail: ignoreReasonDetail
    }
  );
}

export function markDiscoveryCandidateDuplicate(
  id: string,
  applicationId: string
): Promise<{ candidate: DiscoveryCandidate; posting: Application }> {
  return postJson<{ candidate: DiscoveryCandidate; posting: Application }>(
    "/api/discovery/candidates/duplicate",
    { id, application_id: applicationId }
  );
}

export function pursueDiscoveryCandidate(
  id: string
): Promise<{ candidate: DiscoveryCandidate; posting: Application; created: boolean }> {
  return postJson<{ candidate: DiscoveryCandidate; posting: Application; created: boolean }>(
    "/api/discovery/candidates/pursue",
    { id }
  );
}

export function undoDiscoveryCandidateDecision(
  id: string,
  decision: "ignored" | "pursued",
  applicationId = "",
  removePosting = false
): Promise<{ candidate: DiscoveryCandidate; posting_removed: boolean }> {
  return postJson<{ candidate: DiscoveryCandidate; posting_removed: boolean }>(
    "/api/discovery/candidates/undo-decision",
    { id, decision, application_id: applicationId, remove_posting: removePosting }
  );
}

export function mergeCompanies(
  keepCompanyId: string,
  mergeCompanyId: string
): Promise<{ company: Company }> {
  return postJson<{ company: Company }>("/api/companies/merge", {
    keep_company_id: keepCompanyId,
    merge_company_id: mergeCompanyId
  });
}

export function linkCompanyContact(companyId: string, contactId: string): Promise<{ link: { company_id: string; contact_id: string } }> {
  return postJson<{ link: { company_id: string; contact_id: string } }>("/api/companies/link-contact", {
    company_id: companyId,
    contact_id: contactId
  });
}

export function unlinkCompanyContact(companyId: string, contactId: string): Promise<{ link: { company_id: string; contact_id: string } }> {
  return postJson<{ link: { company_id: string; contact_id: string } }>("/api/companies/unlink-contact", {
    company_id: companyId,
    contact_id: contactId
  });
}

export function updateCompanyCandidate(id: string, status: string): Promise<{ candidate: CompanyPostingCandidate }> {
  return postJson<{ candidate: CompanyPostingCandidate }>("/api/companies/candidates/update", { id, status });
}

export function updateCompanyCandidates(
  ids: string[],
  status: string
): Promise<{ candidates: CompanyPostingCandidate[]; count: number }> {
  return postJson<{ candidates: CompanyPostingCandidate[]; count: number }>(
    "/api/companies/candidates/bulk-update",
    { ids, status }
  );
}

export function pursueCompanyCandidate(id: string): Promise<{ candidate: CompanyPostingCandidate; posting: Application | null; stdout: string }> {
  return postJson<{ candidate: CompanyPostingCandidate; posting: Application | null; stdout: string }>("/api/companies/candidates/pursue", { id });
}

export async function getAgentChatHistory(): Promise<AgentChatHistoryMessage[]> {
  const result = await readJson<{ api_version: number; messages: AgentChatHistoryMessage[] }>(
    await fetch("/api/agent/history", { cache: "no-store" })
  );
  if (result.api_version !== AGENT_CHAT_API_VERSION) reloadForAgentUpdate(result.api_version);
  window.sessionStorage.removeItem(AGENT_RELOAD_VERSION_KEY);
  return result.messages;
}

export function clearAgentChatHistory(): Promise<{ cleared: number }> {
  return postJson<{ cleared: number }>("/api/agent/history/clear", {});
}

export function sendAgentChat(message: string, context: AgentContext): Promise<AgentChatResponse> {
  return postJson<AgentChatResponse>("/api/agent/chat", {
    api_version: AGENT_CHAT_API_VERSION,
    message,
    context
  });
}

export async function getWorkflow(): Promise<Workflow> {
  return readJson<Workflow>(await fetch("/api/workflow", { cache: "no-store" }));
}

export function upsertWorkflowStage(stage: Partial<WorkflowStage>): Promise<{ stage: WorkflowStage; workflow: Workflow }> {
  return postJson<{ stage: WorkflowStage; workflow: Workflow }>("/api/workflow/stages/upsert", stage);
}

export function archiveWorkflowStage(id: string): Promise<{ stage: Partial<WorkflowStage>; workflow: Workflow }> {
  return postJson<{ stage: Partial<WorkflowStage>; workflow: Workflow }>("/api/workflow/stages/archive", { id });
}

export function upsertWorkflowActionType(actionType: Partial<WorkflowActionType>): Promise<{ action_type: WorkflowActionType; workflow: Workflow }> {
  return postJson<{ action_type: WorkflowActionType; workflow: Workflow }>("/api/workflow/action-types/upsert", actionType);
}

export function archiveWorkflowActionType(id: string): Promise<{ action_type: Partial<WorkflowActionType>; workflow: Workflow }> {
  return postJson<{ action_type: Partial<WorkflowActionType>; workflow: Workflow }>("/api/workflow/action-types/archive", { id });
}
