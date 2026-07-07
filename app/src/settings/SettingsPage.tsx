import { useEffect, useState } from "react";
import { CalendarIcon, FilterIcon, XIcon } from "../components/Icons";
import {
  archiveWorkflowActionType,
  archiveWorkflowStage,
  deleteResume,
  generateActions,
  getResumeText,
  getSettings,
  getWorkflow,
  saveSettings,
  uploadResume,
  upsertWorkflowActionType,
  upsertWorkflowStage
} from "../core/api";
import { titleCase } from "../core/format";
import type { AppState, FitSignals, ResumeText, SettingsStatus, Workflow, WorkflowActionType, WorkflowStage } from "../core/types";

const emptyWorkflow: Workflow = { stages: [], action_types: [], outcomes: [] };
const emptyResume = { filename: "", uploaded_at: "", text_char_count: 0, extraction_status: "", preview: "", preview_char_count: 0, preview_truncated: false, configured: false };
const emptyFitSignals: FitSignals = {
  role_terms: "",
  domain_terms: "",
  seniority_terms: "",
  search_terms: "",
  low_match_terms: "",
  exclusion_terms: "",
  strength_terms: ""
};

const settingsSections = [
  ["settings-connection", "Connection"],
  ["settings-goals", "Goals"],
  ["settings-signals", "Signals"],
  ["settings-resume", "Resume"],
  ["settings-stages", "Stages"],
  ["settings-action-types", "Action types"]
];

type WeightedTermRow = {
  term: string;
  weight: string;
};

type ImpactOption = {
  label: string;
  value: string;
};

const roleImpactOptions: ImpactOption[] = [
  { label: "Low +12", value: "12" },
  { label: "Medium +22", value: "22" },
  { label: "High +34", value: "34" },
  { label: "Core +42", value: "42" }
];

const domainImpactOptions: ImpactOption[] = [
  { label: "Low +7", value: "7" },
  { label: "Medium +10", value: "10" },
  { label: "High +16", value: "16" },
  { label: "Core +20", value: "20" }
];

const seniorityImpactOptions: ImpactOption[] = [
  { label: "Low +4", value: "4" },
  { label: "Medium +7", value: "7" },
  { label: "High +8", value: "8" },
  { label: "Core +12", value: "12" }
];

export function SettingsPage({ refresh }: { refresh: () => Promise<AppState> }) {
  const [settings, setSettings] = useState<SettingsStatus>({ provider: "openai", model: "", api_base: "", search_goals: "", fit_signals: emptyFitSignals, token_configured: false, resume: emptyResume });
  const [workflow, setWorkflow] = useState<Workflow>(emptyWorkflow);
  const [token, setToken] = useState("");
  const [resumeFile, setResumeFile] = useState<File | null>(null);
  const [resumeText, setResumeText] = useState<ResumeText | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    Promise.all([getSettings(), getWorkflow()])
      .then(([next, nextWorkflow]) => {
        setSettings(next);
        setWorkflow(nextWorkflow);
        setStatus(next.token_configured ? "Token configured locally. Leave token blank to keep the existing value." : "No token configured yet.");
      })
      .catch(() => {
        setStatus("Settings API is unavailable. Start the local app server with: make serve-app");
      });
  }, []);

  async function save() {
    setStatus("Saving settings...");
    try {
      const next = await saveSettings({
        provider: settings.provider,
        model: settings.model,
        api_base: settings.api_base,
        search_goals: settings.search_goals,
        fit_signals: settings.fit_signals,
        api_token: token
      });
      setSettings(next);
      setToken("");
      setStatus(next.token_configured ? "Settings saved locally. Token is configured." : "Settings saved locally. No token is configured.");
    } catch (error) {
      setStatus(`Could not save settings. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function createActions() {
    setStatus("Generating actions...");
    try {
      const result = await generateActions(true);
      const warningText = result.warnings?.length ? ` Warning: ${result.warnings[0]}` : "";
      setStatus(`Created ${result.created} new action${result.created === 1 ? "" : "s"}.${warningText}`);
      if (result.created > 0) await refresh();
    } catch (error) {
      setStatus(`Could not generate actions. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function saveResume() {
    if (!resumeFile) return;
    setStatus("Uploading resume...");
    try {
      const contentBase64 = await fileToBase64(resumeFile);
      const next = await uploadResume(resumeFile.name, contentBase64);
      setSettings(next);
      setResumeFile(null);
      setResumeText(null);
      setStatus(next.resume.configured ? `Resume uploaded: ${next.resume.filename}` : `Resume uploaded, but text was not extracted. ${next.resume.extraction_status}`);
    } catch (error) {
      setStatus(`Could not upload resume. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function clearResume() {
    setStatus("Removing resume...");
    try {
      const next = await deleteResume();
      setSettings(next);
      setResumeFile(null);
      setResumeText(null);
      setStatus("Resume removed.");
    } catch (error) {
      setStatus(`Could not remove resume. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function toggleResumeText() {
    if (resumeText) {
      setResumeText(null);
      return;
    }
    setStatus("Loading extracted resume text...");
    try {
      const next = await getResumeText();
      setResumeText(next);
      setStatus(next.configured ? "Full extracted resume text loaded." : "No extracted resume text is stored.");
    } catch (error) {
      setStatus(`Could not load extracted resume text. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  function updateFitSignal(key: keyof FitSignals, value: string) {
    setSettings({ ...settings, fit_signals: { ...settings.fit_signals, [key]: value } });
  }

  return (
    <section className="view-section settings-page" id="settings-view" aria-label="Settings">
      <aside className="settings-nav-panel" aria-label="Settings sections">
        <nav className="settings-section-nav">
          {settingsSections.map(([id, label]) => <a key={id} href={`#${id}`}>{label}</a>)}
        </nav>
        <div className="settings-status-card" role="status">{status}</div>
      </aside>

      <div className="settings-content">
        <article className="panel settings-card" id="settings-connection">
          <div className="panel-header">
            <h2 className="panel-title">Connection</h2>
            <span className="panel-kicker">{settings.token_configured ? "Token configured" : "No token"}</span>
          </div>
          <div className="settings-grid compact">
            <label className="settings-field">
              <span>Provider</span>
              <select value={settings.provider} onChange={event => setSettings({ ...settings, provider: event.target.value })}>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
              </select>
            </label>
            <label className="settings-field">
              <span>Model</span>
              <input value={settings.model} onChange={event => setSettings({ ...settings, model: event.target.value })} type="text" placeholder="gpt-4.1-mini" />
            </label>
            <label className="settings-field full">
              <span>API Base</span>
              <input value={settings.api_base} onChange={event => setSettings({ ...settings, api_base: event.target.value })} type="text" placeholder="Optional; defaults to provider API" />
            </label>
            <label className="settings-field full">
              <span>API Token</span>
              <input value={token} onChange={event => setToken(event.target.value)} type="password" autoComplete="off" placeholder="Stored only in data/settings.local.json" />
            </label>
          </div>
          <div className="detail-actions">
            <button className="button primary" type="button" onClick={save}><FilterIcon size={16} /> Save Settings</button>
            <button className="button" type="button" onClick={createActions}><CalendarIcon /> Generate Actions</button>
          </div>
        </article>

        <article className="panel settings-card" id="settings-goals">
          <div className="panel-header">
            <h2 className="panel-title">Search Goals</h2>
            <span className="panel-kicker">{settings.search_goals.length.toLocaleString()} chars</span>
          </div>
          <div className="settings-grid">
            <label className="settings-field full">
              <span>Goals</span>
              <textarea
                className="tall-textarea"
                value={settings.search_goals}
                onChange={event => setSettings({ ...settings, search_goals: event.target.value })}
                rows={12}
                placeholder="Describe the roles, domains, and tradeoffs Hunter should use when searching postings and judging fit."
              />
            </label>
          </div>
          <div className="detail-actions">
            <button className="button primary" type="button" onClick={save}><FilterIcon size={16} /> Save Goals</button>
          </div>
        </article>

        <article className="panel settings-card" id="settings-signals">
          <div className="panel-header">
            <h2 className="panel-title">Search and Fit Signals</h2>
            <span className="panel-kicker">Weighted terms</span>
          </div>
          <div className="settings-signal-layout">
            <div className="settings-signal-column">
              <WeightedSignalField label="Role terms" value={settings.fit_signals.role_terms} onChange={value => updateFitSignal("role_terms", value)} termPlaceholder="target role phrase" impactOptions={roleImpactOptions} />
              <WeightedSignalField label="Domain terms" value={settings.fit_signals.domain_terms} onChange={value => updateFitSignal("domain_terms", value)} termPlaceholder="domain signal" impactOptions={domainImpactOptions} />
            </div>
            <div className="settings-signal-column">
              <SignalField label="Search terms" value={settings.fit_signals.search_terms} onChange={value => updateFitSignal("search_terms", value)} rows={5} placeholder="career search phrase" />
              <WeightedSignalField label="Seniority terms" value={settings.fit_signals.seniority_terms} onChange={value => updateFitSignal("seniority_terms", value)} termPlaceholder="seniority signal" impactOptions={seniorityImpactOptions} compact />
              <SignalField label="Low-fit terms" value={settings.fit_signals.low_match_terms} onChange={value => updateFitSignal("low_match_terms", value)} rows={5} placeholder="sales" />
              <SignalField label="Exclusion terms" value={settings.fit_signals.exclusion_terms} onChange={value => updateFitSignal("exclusion_terms", value)} rows={5} placeholder="role to keep below recommendation" />
              <SignalField label="Profile strength terms" value={settings.fit_signals.strength_terms} onChange={value => updateFitSignal("strength_terms", value)} rows={5} placeholder="roadmap" />
            </div>
          </div>
          <div className="detail-actions">
            <button className="button primary" type="button" onClick={save}><FilterIcon size={16} /> Save Signals</button>
          </div>
        </article>

        <article className="panel settings-card" id="settings-resume">
          <div className="panel-header">
            <h2 className="panel-title">Resume Context</h2>
            <span className="panel-kicker">{settings.resume.configured ? `${settings.resume.text_char_count.toLocaleString()} chars` : "Not uploaded"}</span>
          </div>
          <div className="settings-grid compact">
            <label className="settings-field full">
              <span>Resume file</span>
              <input
                type="file"
                accept=".pdf,.docx,.txt,.md,.markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown"
                onChange={event => setResumeFile(event.target.files?.[0] || null)}
              />
            </label>
          </div>
          <div className="resume-summary">
            {settings.resume.filename ? (
              <>
                <div><strong>{settings.resume.filename}</strong><span>{settings.resume.text_char_count.toLocaleString()} extracted characters</span></div>
                <span>{settings.resume.uploaded_at || "Uploaded locally"}</span>
                <span>{settings.resume.extraction_status || "ok"}</span>
                {settings.resume.preview_truncated ? <span>Preview shows first {settings.resume.preview_char_count.toLocaleString()} characters</span> : null}
              </>
            ) : (
              <span>No resume uploaded.</span>
            )}
          </div>
          {settings.resume.preview ? (
            <>
              <div className="resume-preview-label">
                <span>Extracted text preview</span>
                {settings.resume.preview_truncated ? <button className="button compact" type="button" onClick={toggleResumeText}>{resumeText ? "Hide Full Text" : "View Full Text"}</button> : null}
              </div>
              <pre className="resume-preview">{resumeText?.text || settings.resume.preview}</pre>
            </>
          ) : null}
          <div className="detail-actions">
            <button className="button primary" type="button" onClick={saveResume} disabled={!resumeFile}><FilterIcon size={16} /> Upload Resume</button>
            <button className="button" type="button" onClick={clearResume} disabled={!settings.resume.configured && !settings.resume.filename}>Remove Resume</button>
          </div>
        </article>

        <WorkflowStageSettings workflow={workflow} setWorkflow={setWorkflow} setStatus={setStatus} refresh={refresh} />
        <WorkflowActionTypeSettings workflow={workflow} setWorkflow={setWorkflow} setStatus={setStatus} refresh={refresh} />
      </div>
    </section>
  );
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("Could not read file."));
    reader.onload = () => {
      const result = String(reader.result || "");
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    };
    reader.readAsDataURL(file);
  });
}

function parseWeightedTerms(value: string): WeightedTermRow[] {
  const rows = value.split("\n").map(rawLine => {
    const [term = "", weight = ""] = rawLine.split("|");
    return { term: term.trim(), weight: weight.trim() };
  }).filter(row => row.term || row.weight);
  return rows.length ? rows : [{ term: "", weight: "" }];
}

function serializeWeightedTerms(rows: WeightedTermRow[]) {
  return rows
    .map(row => {
      const term = row.term.trim();
      const weight = row.weight.trim();
      if (!term && !weight) return "";
      return weight ? `${term} | ${weight}` : term;
    })
    .filter(Boolean)
    .join("\n");
}

function impactOptionsWithCustomValue(options: ImpactOption[], value: string) {
  const cleanValue = value.trim();
  if (!cleanValue || options.some(option => option.value === cleanValue)) return options;
  return [...options, { label: `Custom +${cleanValue}`, value: cleanValue }];
}

function WeightedSignalField({
  label,
  value,
  onChange,
  termPlaceholder,
  impactOptions,
  compact = false
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  termPlaceholder: string;
  impactOptions: ImpactOption[];
  compact?: boolean;
}) {
  const [rows, setRows] = useState<WeightedTermRow[]>(() => parseWeightedTerms(value));

  useEffect(() => {
    setRows(parseWeightedTerms(value));
  }, [value]);

  function commitRows(nextRows: WeightedTermRow[]) {
    setRows(nextRows.length ? nextRows : [{ term: "", weight: "" }]);
    onChange(serializeWeightedTerms(nextRows));
  }

  function updateRow(index: number, updates: Partial<WeightedTermRow>) {
    const nextRows = rows.map((row, rowIndex) => rowIndex === index ? { ...row, ...updates } : row);
    commitRows(nextRows);
  }

  function removeRow(index: number) {
    const nextRows = rows.filter((_row, rowIndex) => rowIndex !== index);
    commitRows(nextRows);
  }

  function addRow() {
    setRows([...rows, { term: "", weight: "" }]);
  }

  return (
    <div className={`weighted-signal-field${compact ? " compact" : ""}`}>
      <div className="weighted-signal-header">
        <span>{label}</span>
        <button className="button compact weighted-signal-add" type="button" onClick={addRow}>Add</button>
      </div>
      <div className="weighted-signal-labels" aria-hidden="true">
        <span>Term</span>
        <span>Fit Impact</span>
        <span className="weighted-signal-action-label" />
      </div>
      <div className="weighted-signal-rows">
        {rows.map((row, index) => (
          <div className="weighted-signal-row" key={`${label}-${index}`}>
            <input
              aria-label={`${label} term ${index + 1}`}
              value={row.term}
              onChange={event => updateRow(index, { term: event.target.value })}
              placeholder={termPlaceholder}
            />
            <select
              aria-label={`${label} fit impact ${index + 1}`}
              value={row.weight}
              onChange={event => updateRow(index, { weight: event.target.value })}
            >
              <option value="">Choose</option>
              {impactOptionsWithCustomValue(impactOptions, row.weight).map(option => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <button
              className="icon-button weighted-signal-remove"
              type="button"
              onClick={() => removeRow(index)}
              disabled={rows.length === 1 && !row.term && !row.weight}
              aria-label={`Remove ${label} row ${index + 1}`}
              title="Remove"
            >
              <XIcon size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function SignalField({
  label,
  value,
  onChange,
  rows,
  placeholder,
  tall = false
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  rows: number;
  placeholder: string;
  tall?: boolean;
}) {
  return (
    <label className="settings-field full">
      <span>{label}</span>
      <textarea className={tall ? "tall-textarea" : ""} value={value} onChange={event => onChange(event.target.value)} rows={rows} placeholder={placeholder} />
    </label>
  );
}

function WorkflowStageSettings({
  workflow,
  setWorkflow,
  setStatus,
  refresh
}: {
  workflow: Workflow;
  setWorkflow: (workflow: Workflow) => void;
  setStatus: (status: string) => void;
  refresh: () => Promise<AppState>;
}) {
  const [draft, setDraft] = useState<Partial<WorkflowStage>>({ id: "", label: "", sort_order: "", is_terminal: "", is_active: "1" });

  async function saveStage(stage: Partial<WorkflowStage>) {
    setStatus("Saving stage...");
    try {
      const result = await upsertWorkflowStage(stage);
      setWorkflow(result.workflow);
      setDraft({ id: "", label: "", sort_order: "", is_terminal: "", is_active: "1" });
      await refresh();
      setStatus("Stage saved.");
    } catch (error) {
      setStatus(`Could not save stage. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function archiveStage(id: string) {
    setStatus("Archiving stage...");
    try {
      const result = await archiveWorkflowStage(id);
      setWorkflow(result.workflow);
      await refresh();
      setStatus("Stage archived.");
    } catch (error) {
      setStatus(`Could not archive stage. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <article className="panel settings-card" id="settings-stages">
      <div className="panel-header"><h2 className="panel-title">Workflow Stages</h2><span className="panel-kicker">{workflow.stages.length} stages</span></div>
      <div className="workflow-list">
        {workflow.stages.map(stage => <StageRow key={stage.id} stage={stage} onSave={saveStage} onArchive={archiveStage} />)}
      </div>
      <div className="settings-grid">
        <label className="settings-field"><span>ID</span><input value={draft.id || ""} onChange={event => setDraft({ ...draft, id: event.target.value })} placeholder="phone-screen" /></label>
        <label className="settings-field"><span>Label</span><input value={draft.label || ""} onChange={event => setDraft({ ...draft, label: event.target.value })} placeholder="Phone screen" /></label>
        <label className="settings-field"><span>Order</span><input value={draft.sort_order || ""} onChange={event => setDraft({ ...draft, sort_order: event.target.value })} type="number" /></label>
        <label className="toggle"><input checked={draft.is_terminal === "1"} onChange={event => setDraft({ ...draft, is_terminal: event.target.checked ? "1" : "" })} type="checkbox" /> Terminal</label>
      </div>
      <div className="detail-actions"><button className="button primary" type="button" onClick={() => saveStage(draft)}><FilterIcon size={16} /> Add Stage</button></div>
    </article>
  );
}

function StageRow({ stage, onSave, onArchive }: { stage: WorkflowStage; onSave: (stage: WorkflowStage) => void; onArchive: (id: string) => void }) {
  const [draft, setDraft] = useState(stage);
  useEffect(() => {
    setDraft(stage);
  }, [stage]);
  return (
    <div className="workflow-row">
      <input value={draft.label} onChange={event => setDraft({ ...draft, label: event.target.value })} aria-label={`${stage.id} label`} />
      <input value={draft.sort_order} onChange={event => setDraft({ ...draft, sort_order: event.target.value })} type="number" aria-label={`${stage.id} order`} />
      <label className="toggle"><input checked={draft.is_terminal === "1"} onChange={event => setDraft({ ...draft, is_terminal: event.target.checked ? "1" : "" })} type="checkbox" /> Terminal</label>
      <span>{draft.is_active === "1" ? "Active" : "Archived"}</span>
      <button className="button compact" type="button" onClick={() => onSave(draft)}>Save</button>
      <button className="button compact" type="button" onClick={() => onArchive(stage.id)} disabled={stage.id === "closed" || stage.is_active !== "1"}>Archive</button>
    </div>
  );
}

function WorkflowActionTypeSettings({
  workflow,
  setWorkflow,
  setStatus,
  refresh
}: {
  workflow: Workflow;
  setWorkflow: (workflow: Workflow) => void;
  setStatus: (status: string) => void;
  refresh: () => Promise<AppState>;
}) {
  const [draft, setDraft] = useState<Partial<WorkflowActionType>>({
    id: "",
    label: "",
    description: "",
    default_priority: "medium",
    default_due_days: "1",
    allowed_stages: "",
    sort_order: "",
    is_active: "1"
  });

  async function saveActionType(actionType: Partial<WorkflowActionType>) {
    setStatus("Saving action type...");
    try {
      const result = await upsertWorkflowActionType(actionType);
      setWorkflow(result.workflow);
      setDraft({ id: "", label: "", description: "", default_priority: "medium", default_due_days: "1", allowed_stages: "", sort_order: "", is_active: "1" });
      await refresh();
      setStatus("Action type saved.");
    } catch (error) {
      setStatus(`Could not save action type. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function archiveActionType(id: string) {
    setStatus("Archiving action type...");
    try {
      const result = await archiveWorkflowActionType(id);
      setWorkflow(result.workflow);
      await refresh();
      setStatus("Action type archived.");
    } catch (error) {
      setStatus(`Could not archive action type. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <article className="panel settings-card" id="settings-action-types">
      <div className="panel-header"><h2 className="panel-title">Action Types</h2><span className="panel-kicker">{workflow.action_types.length} types</span></div>
      <div className="workflow-list">
        {workflow.action_types.map(actionType => (
          <ActionTypeRow key={actionType.id} actionType={actionType} stages={workflow.stages} onSave={saveActionType} onArchive={archiveActionType} />
        ))}
      </div>
      <div className="settings-grid">
        <label className="settings-field"><span>ID</span><input value={draft.id || ""} onChange={event => setDraft({ ...draft, id: event.target.value })} placeholder="schedule-call" /></label>
        <label className="settings-field"><span>Label</span><input value={draft.label || ""} onChange={event => setDraft({ ...draft, label: event.target.value })} placeholder="Schedule call" /></label>
        <label className="settings-field"><span>Priority</span><select value={draft.default_priority || "medium"} onChange={event => setDraft({ ...draft, default_priority: event.target.value })}><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
        <label className="settings-field"><span>Due days</span><input value={draft.default_due_days || "1"} onChange={event => setDraft({ ...draft, default_due_days: event.target.value })} type="number" /></label>
        <label className="settings-field"><span>Order</span><input value={draft.sort_order || ""} onChange={event => setDraft({ ...draft, sort_order: event.target.value })} type="number" /></label>
        <label className="settings-field full"><span>Allowed stages</span><input value={draft.allowed_stages || ""} onChange={event => setDraft({ ...draft, allowed_stages: event.target.value })} placeholder={workflow.stages.map(stage => stage.id).join(", ")} /></label>
        <label className="settings-field full"><span>Description</span><input value={draft.description || ""} onChange={event => setDraft({ ...draft, description: event.target.value })} /></label>
      </div>
      <div className="detail-actions"><button className="button primary" type="button" onClick={() => saveActionType(draft)}><FilterIcon size={16} /> Add Action Type</button></div>
    </article>
  );
}

function ActionTypeRow({
  actionType,
  stages,
  onSave,
  onArchive
}: {
  actionType: WorkflowActionType;
  stages: WorkflowStage[];
  onSave: (actionType: WorkflowActionType) => void;
  onArchive: (id: string) => void;
}) {
  const [draft, setDraft] = useState(actionType);
  useEffect(() => {
    setDraft(actionType);
  }, [actionType]);
  return (
    <div className="workflow-row action-type-row">
      <input value={draft.label} onChange={event => setDraft({ ...draft, label: event.target.value })} aria-label={`${actionType.id} label`} />
      <select value={draft.default_priority} onChange={event => setDraft({ ...draft, default_priority: event.target.value })} aria-label={`${actionType.id} priority`}>
        <option value="high">High</option>
        <option value="medium">Medium</option>
        <option value="low">Low</option>
      </select>
      <input value={draft.default_due_days} onChange={event => setDraft({ ...draft, default_due_days: event.target.value })} type="number" aria-label={`${actionType.id} due days`} />
      <input value={draft.sort_order} onChange={event => setDraft({ ...draft, sort_order: event.target.value })} type="number" aria-label={`${actionType.id} order`} />
      <input value={draft.allowed_stages} onChange={event => setDraft({ ...draft, allowed_stages: event.target.value })} aria-label={`${actionType.id} allowed stages`} title={stages.map(stage => `${stage.id}: ${titleCase(stage.label)}`).join("\n")} />
      <span>{draft.is_active === "1" ? "Active" : "Archived"}</span>
      <button className="button compact" type="button" onClick={() => onSave(draft)}>Save</button>
      <button className="button compact" type="button" onClick={() => onArchive(actionType.id)} disabled={actionType.is_active !== "1"}>Archive</button>
    </div>
  );
}
