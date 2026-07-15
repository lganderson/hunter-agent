import type {
  Action,
  ActionUpdates,
  AgentChatMessage,
  AgentChatResponse,
  AppState,
  Application,
  ApplicationUpdates,
  Company,
  CompanyCareerSource,
  CompanyPostingCandidate,
  CompanyUpdates,
  Contact,
  ContactUpdates,
  ResumeText,
  SettingsStatus,
  Workflow,
  WorkflowActionType,
  WorkflowStage
} from "./types";

async function readJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const result = (await response.json()) as { error?: string };
      message = result.error || message;
    } catch {
      // Keep the original HTTP status when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
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

export async function getAppState(): Promise<AppState> {
  return readJson<AppState>(await fetch("/api/app-state", { cache: "no-store" }));
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

export type CompanyCheckResult = {
  company: Company;
  career_source: CompanyCareerSource | null;
  candidates: CompanyPostingCandidate[];
  new: CompanyPostingCandidate[];
  recommended: CompanyPostingCandidate[];
  unavailable_count: number;
  verification_count: number;
  verification_skipped_count: number;
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

export function ingestCompanyCandidate(id: string): Promise<{ candidate: CompanyPostingCandidate; posting: Application | null; stdout: string }> {
  return postJson<{ candidate: CompanyPostingCandidate; posting: Application | null; stdout: string }>("/api/companies/candidates/ingest", { id });
}

export function sendAgentChat(messages: AgentChatMessage[]): Promise<AgentChatResponse> {
  return postJson<AgentChatResponse>("/api/agent/chat", { messages });
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
