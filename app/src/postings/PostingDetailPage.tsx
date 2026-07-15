import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ActionCommand, Priority, StatusPill, TagList } from "../components/Primitives";
import { BriefcaseIcon, ExternalIcon, FilterIcon, PlusIcon } from "../components/Icons";
import { createAction, createApplication, linkContact, makeNextAction, unlinkContact, updateAction, updateActionFields, updateApplication } from "../core/api";
import { actionDueLabel, dueLabel, isActionComplete, markdownToHtml, normalizeTag, tagColorClass, tagList, titleCase } from "../core/format";
import type { Action, ActionUpdates, AppState, Application } from "../core/types";

type DetailProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
  createNew?: boolean;
};

export function PostingDetailPage({ data, refresh, createNew = false }: DetailProps) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const app = createNew ? blankApplication(data) : data.applications.find(item => item.id === id);
  const [operationStatus, setOperationStatus] = useState("");
  const [stageDraft, setStageDraft] = useState(app?.stage || "");
  const [tagDraft, setTagDraft] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [tagSaving, setTagSaving] = useState(false);
  const [contactDraft, setContactDraft] = useState("");
  const existingTags = useMemo(
    () => [...new Set(data.applications.flatMap(tagList))].sort((a, b) => a.localeCompare(b)),
    [data.applications]
  );

  useEffect(() => {
    setStageDraft(app?.stage || "");
  }, [app?.id, app?.stage]);

  useEffect(() => {
    setTagDraft(app ? tagList(app) : []);
  }, [app?.id, app?.tags]);

  useEffect(() => {
    setTagInput("");
  }, [app?.id]);

  if (!app) {
    return <div className="empty-state" style={{ display: "block" }}>That posting could not be found. <Link to="/postings">Return to postings.</Link></div>;
  }

  const related = data.actions
    .filter(action => action.application_id === app.id)
    .sort((a, b) => {
      const completeDelta = Number(isActionComplete(a)) - Number(isActionComplete(b));
      if (completeDelta) return completeDelta;
      return a.sort_due.localeCompare(b.sort_due);
    });
  const linkedContactIds = new Set(data.application_contacts.filter(link => link.application_id === app.id).map(link => link.contact_id));
  const linkedContacts = data.contacts.filter(contact => linkedContactIds.has(contact.id));
  const availableContacts = data.contacts.filter(contact => !linkedContactIds.has(contact.id));

  async function savePosting(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setOperationStatus("Saving posting...");
    try {
      const values = {
        company: String(form.get("company") ?? app!.company ?? ""),
        company_id: String(form.get("company_id") ?? app!.company_id ?? ""),
        role: String(form.get("role") || ""),
        location: String(form.get("location") || ""),
        work_mode: String(form.get("work_mode") || ""),
        source: String(form.get("source") ?? app!.source ?? ""),
        source_url: String(form.get("source_url") || ""),
        compensation: String(form.get("compensation") || ""),
        stage: String(form.get("stage") || ""),
        outcome: String(form.get("outcome") || ""),
        priority: String(form.get("priority") || ""),
        date_found: String(form.get("date_found") || ""),
        date_applied: String(form.get("date_applied") || ""),
        tags: String(form.get("tags") ?? app!.tags ?? ""),
        contact: String(form.get("contact") ?? app!.contact ?? ""),
        resume_version: String(form.get("resume_version") ?? app!.resume_version ?? ""),
        cover_letter: String(form.get("cover_letter") ?? app!.cover_letter ?? ""),
        notes: String(form.get("notes") || "")
      };
      const result = createNew ? await createApplication(values) : await updateApplication(app!.id, values);
      await refresh();
      if (createNew) {
        navigate(`/postings/${encodeURIComponent(result.application.id)}`, { replace: true });
      } else {
        setOperationStatus("Posting saved.");
      }
    } catch (error) {
      setOperationStatus(`Could not save posting. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function changeAction(actionId: string, nextStatus: string) {
    setOperationStatus(nextStatus === "open" ? "Reopening action..." : "Completing action...");
    try {
      await updateAction(actionId, nextStatus);
      await refresh();
      setOperationStatus("Action updated.");
    } catch (error) {
      setOperationStatus(`Could not update action. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function saveActionFields(actionId: string, updates: ActionUpdates) {
    setOperationStatus("Saving action...");
    try {
      await updateActionFields(actionId, updates);
      await refresh();
      setOperationStatus("Action saved.");
    } catch (error) {
      setOperationStatus(`Could not save action. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function chooseNextAction(actionId: string) {
    setOperationStatus("Updating next action...");
    try {
      await makeNextAction(actionId);
      await refresh();
      setOperationStatus("Next action updated.");
    } catch (error) {
      setOperationStatus(`Could not update next action. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function addContact() {
    if (!contactDraft) return;
    setOperationStatus("Linking contact...");
    try {
      await linkContact(contactDraft, app!.id);
      await refresh();
      setContactDraft("");
      setOperationStatus("Contact linked.");
    } catch (error) {
      setOperationStatus(`Could not link contact. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function removeContact(contactId: string) {
    setOperationStatus("Unlinking contact...");
    try {
      await unlinkContact(contactId, app!.id);
      await refresh();
      setOperationStatus("Contact unlinked.");
    } catch (error) {
      setOperationStatus(`Could not unlink contact. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async function saveTags(nextTags: string[]) {
    if (createNew || tagSaving) return;
    const previousTags = tagDraft;
    setTagDraft(nextTags);
    setTagSaving(true);
    setOperationStatus("Saving tags...");
    try {
      await updateApplication(app!.id, { tags: nextTags.join(",") });
      await refresh();
      setOperationStatus("Tags saved.");
    } catch (error) {
      setTagDraft(previousTags);
      setOperationStatus(`Could not save tags. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setTagSaving(false);
    }
  }

  return (
    <section className="view-section posting-detail" id="posting-detail-view" aria-label="Posting detail">
      <div className="posting-breadcrumb"><Link to="/postings">Postings</Link><span>/</span><span>{createNew ? "Add posting" : app.role || "Untitled posting"}</span></div>
      <header className="panel posting-hero">
        <div className="posting-hero-main">
          <div className="posting-identity">
            <div className="posting-identity-icon" aria-hidden="true"><BriefcaseIcon size={22} /></div>
          <div>
            <h1>{createNew ? "Add posting" : app.role || "Untitled posting"}</h1>
            <p>{app.company || "Unknown company"}{app.location ? ` · ${app.location}` : ""}{app.work_mode ? ` · ${titleCase(app.work_mode)}` : ""}</p>
            {!createNew ? <div className="posting-hero-meta"><StatusPill value={app.outcome || app.stage} /><Priority value={app.priority} /><TagList app={app} /></div> : null}
          </div>
          </div>
          <div className="posting-hero-actions">
            {!createNew && app.source_url ? <a className="button" href={app.source_url} target="_blank" rel="noreferrer"><ExternalIcon size={16} /> View source</a> : null}
          </div>
        </div>
      </header>

      {!createNew ? <div className="posting-overview-grid" aria-label="Posting overview">
        <div className="posting-overview-card primary">
          <span>Next action</span><strong>{app.next_action || "No next action"}</strong><small className={app.is_overdue ? "overdue" : app.is_due_soon ? "soon" : ""}>{dueLabel(app) || "Add an action to keep this moving"}</small>
        </div>
        <div className="posting-overview-card">
          <span>Compensation</span><strong>{app.compensation || "Not listed"}</strong><small>{app.location || "Location not listed"}</small>
        </div>
        <div className="posting-overview-card">
          <span>Application</span><strong>{app.date_applied ? `Applied ${app.date_applied}` : "Not applied"}</strong><small>{app.date_found ? `Found ${app.date_found}` : "Date not recorded"}</small>
        </div>
      </div> : null}

      <div className={createNew ? "detail-layout create-posting-layout" : "posting-workspace"}>
        <div className={createNew ? undefined : "posting-workspace-main"}>
        <PostingEditor app={app} createNew={createNew} data={data} existingTags={existingTags} onSubmit={savePosting} setStageDraft={setStageDraft} setTagDraft={setTagDraft} setTagInput={setTagInput} stageDraft={stageDraft} tagDraft={tagDraft} tagInput={tagInput} />

        {!createNew ? <article className="panel actions-panel">
          <NewActionForm app={app} data={data} openCount={related.filter(action => !isActionComplete(action)).length} refresh={refresh} setOperationStatus={setOperationStatus} />
          <div className="related-actions">
            {related.length ? related.map(action => (
              <div className={`related-action${app.next_action_id === action.id ? " next-action" : ""}${isActionComplete(action) ? " complete" : ""}`} key={action.id}>
                <div className="action-summary">
                  <div className="action-title-row"><strong>{action.title || "Untitled action"}</strong>{app.next_action_id === action.id ? <span className="next-badge">Next</span> : null}</div>
                  <span>{titleCase(action.type)}<span aria-hidden="true"> · </span><span className={action.is_overdue ? "overdue" : action.is_due_soon ? "soon" : ""}>{actionDueLabel(action) || "No due date"}</span>{isActionComplete(action) ? ` · ${titleCase(action.status)}` : ""}</span>
                </div>
                <ActionControls
                  action={action}
                  isNext={app.next_action_id === action.id}
                  actionTypes={data.workflow.action_types}
                  onFieldsSave={saveActionFields}
                  onMakeNext={chooseNextAction}
                  onStatusUpdate={changeAction}
                />
              </div>
            )) : <div className="empty-state" style={{ display: "block" }}>No actions yet. Add the next concrete step for this posting.</div>}
          </div>
        </article> : null}

        {!createNew ? <details className="panel posting-note-disclosure">
          <summary><span><strong>Posting note</strong><small>Reference description and captured context</small></span><span className="disclosure-label">Show note</span></summary>
          <div className="note-view" dangerouslySetInnerHTML={{ __html: markdownToHtml(app.posting_markdown || "# No posting note\n\nNo Markdown note is available for this row.") }} />
        </details> : null}
        </div>

        {!createNew ? <aside className="posting-workspace-rail">
          <article className="panel posting-tags-panel">
            <div className="posting-section-header compact"><div><h2>Tags</h2><p>Organize this posting for filtering and review.</p></div></div>
            <div className="posting-tags-panel-body">
              <TagEditor
                disabled={tagSaving}
                existingTags={existingTags}
                inputValue={tagInput}
                setInputValue={setTagInput}
                setTags={saveTags}
                showLabel={false}
                tags={tagDraft}
              />
            </div>
          </article>
          <article className="panel contacts-panel">
          <div className="posting-section-header compact"><div><h2>Contacts</h2><p>{linkedContacts.length} linked relationship{linkedContacts.length === 1 ? "" : "s"}.</p></div><Link className="text-link" to="/contacts">Manage all</Link></div>
          <div className="contact-linker">
            <select aria-label="Contact to link" value={contactDraft} onChange={event => setContactDraft(event.target.value)}>
              <option value="">Select a contact</option>
              {availableContacts.map(contact => <option key={contact.id} value={contact.id}>{contact.name}{contact.company ? ` · ${contact.company}` : ""}</option>)}
            </select>
            <button className="button compact" type="button" disabled={!contactDraft} onClick={addContact}>Link</button>
          </div>
          <div className="association-list compact-list">
            {linkedContacts.length ? linkedContacts.map(contact => (
              <div className="association-row" key={contact.id}>
                <div>
                  <strong>{contact.name || "Unnamed contact"}</strong>
                  <span>{[contact.role, contact.company].filter(Boolean).join(" · ") || "No details"}</span>
                </div>
                <button className="button compact quiet" type="button" onClick={() => removeContact(contact.id)}>Unlink</button>
              </div>
            )) : <div className="empty-state compact-empty" style={{ display: "block" }}>No linked contacts yet.</div>}
          </div>
          </article>
        </aside> : null}
      </div>

      {operationStatus ? <div className="posting-operation-status" role="status" aria-live="polite">{operationStatus}</div> : null}
    </section>
  );
}

function PostingEditor({
  app,
  createNew = false,
  data,
  existingTags,
  onSubmit,
  setStageDraft,
  setTagDraft,
  setTagInput,
  stageDraft,
  tagDraft,
  tagInput
}: {
  app: Application;
  createNew?: boolean;
  data: AppState;
  existingTags: string[];
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
  setStageDraft: (value: string) => void;
  setTagDraft: (value: string[]) => void;
  setTagInput: (value: string) => void;
  stageDraft: string;
  tagDraft: string[];
  tagInput: string;
}) {
  return (
    <article className="panel posting-editor-panel">
      <div className="posting-section-header"><div><h2>Posting details</h2><p>Update role, tracking, location, and application timing.</p></div></div>
      <form className="management-form" onSubmit={onSubmit} key={app.id}>
        <label className={createNew ? "form-field" : "form-field full"}>Role <input name="role" type="text" defaultValue={app.role || ""} required /></label>
        {createNew ? <><label className="form-field">Company <input name="company" type="text" defaultValue={app.company || ""} /></label>
        <label className="form-field">Managed company <select name="company_id" defaultValue={app.company_id || ""}>
          <option value="">None</option>
          {data.companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}
        </select></label></> : null}
        <label className="form-field">Location <input name="location" type="text" defaultValue={app.location || ""} /></label>
        <label className="form-field">Work mode <input name="work_mode" type="text" defaultValue={app.work_mode || ""} placeholder="Remote, hybrid, on-site" /></label>
        <label className="form-field full">Source URL <input name="source_url" type="url" defaultValue={app.source_url || ""} /></label>
        <label className="form-field full">Compensation <input name="compensation" type="text" defaultValue={app.compensation || ""} /></label>
        <label className="form-field">Stage <select name="stage" value={stageDraft} onChange={event => setStageDraft(event.target.value)}>
          {stageOptions(data, app.stage).map(stage => <option key={stage.id} value={stage.id}>{stage.label}</option>)}
        </select></label>
        {stageDraft === "closed" ? <label className="form-field">Outcome <select name="outcome" defaultValue={app.outcome || ""}>
          <option value=""></option>
          {data.workflow.outcomes.map(outcome => <option key={outcome} value={outcome}>{titleCase(outcome)}</option>)}
        </select></label> : <input name="outcome" type="hidden" value="" />}
        <label className="form-field">Priority <select name="priority" defaultValue={app.priority || ""}><option value=""></option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
        <label className="form-field">Date found <input name="date_found" type="date" defaultValue={app.date_found || ""} /></label>
        <label className="form-field">Date applied <input name="date_applied" type="date" defaultValue={app.date_applied || ""} /></label>
        {createNew ? <section className="posting-tags-section form-field full" aria-labelledby="posting-tags-heading">
          <div><h3 id="posting-tags-heading">Tags</h3><p>Organize this posting for filtering and review.</p></div>
          <TagEditor tags={tagDraft} existingTags={existingTags} inputValue={tagInput} setInputValue={setTagInput} setTags={setTagDraft} showLabel={false} />
        </section> : null}
        <label className="form-field full">Notes <textarea name="notes" defaultValue={app.notes || ""} /></label>
        <div className="detail-actions form-field full">
          <button className="button primary" type="submit"><FilterIcon size={16} /> {createNew ? "Create posting" : "Save changes"}</button>
        </div>
      </form>
    </article>
  );
}

function ActionControls({
  action,
  isNext,
  actionTypes,
  onFieldsSave,
  onMakeNext,
  onStatusUpdate
}: {
  action: Action;
  isNext: boolean;
  actionTypes: AppState["workflow"]["action_types"];
  onFieldsSave: (actionId: string, updates: ActionUpdates) => void;
  onMakeNext: (actionId: string) => void;
  onStatusUpdate: (actionId: string, status: string) => void;
}) {
  const complete = isActionComplete(action);

  return (
    <>
      <div className="related-action-controls">
        {!complete && !isNext ? <button className="button compact" type="button" onClick={() => onMakeNext(action.id)}>Make next</button> : null}
        <ActionCommand action={action} onUpdate={onStatusUpdate} />
      </div>
      <details className="action-editor">
        <summary>Edit</summary>
        <form onSubmit={event => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          onFieldsSave(action.id, actionValues(form));
        }}>
          <label className="form-field full">Title <input name="title" defaultValue={action.title} required /></label>
          <label className="form-field">Type <select name="type" defaultValue={action.type}>{actionTypeOptions(actionTypes, action.type).map(type => <option key={type.id} value={type.id}>{type.label}</option>)}</select></label>
          <label className="form-field">Priority <select name="priority" defaultValue={action.priority}><option value=""></option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
          <label className="form-field">Due <input name="due_date" type="date" defaultValue={action.due_date} /></label>
          <label className="form-field">Related URL <input name="related_url" type="url" defaultValue={action.related_url} /></label>
          <label className="form-field full">Description <textarea name="description" defaultValue={action.description} /></label>
          <label className="form-field full">Notes <textarea name="notes" defaultValue={action.notes} /></label>
          <div className="form-field full"><button className="button compact" type="submit">Save action</button></div>
        </form>
      </details>
    </>
  );
}

function NewActionForm({
  app,
  data,
  openCount,
  refresh,
  setOperationStatus
}: {
  app: Application;
  data: AppState;
  openCount: number;
  refresh: () => Promise<AppState>;
  setOperationStatus: (status: string) => void;
}) {
  const [open, setOpen] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    setOperationStatus("Adding action...");
    try {
      await createAction(app.id, actionValues(new FormData(formElement)));
      await refresh();
      formElement.reset();
      setOpen(false);
      setOperationStatus("Action added.");
    } catch (error) {
      setOperationStatus(`Could not add action. ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  return (
    <div className="new-action">
      <div className="posting-section-header">
        <div><h2>Actions</h2><p>Keep the next concrete step current.</p></div>
        <div className="action-header-controls"><span>{openCount} open</span><button className="button compact primary" type="button" onClick={() => setOpen(value => !value)}><PlusIcon size={15} /> {open ? "Close" : "Add action"}</button></div>
      </div>
      {open ? <form className="new-action-form" onSubmit={submit}>
        <label className="form-field full">Title <input name="title" required autoFocus /></label>
        <label className="form-field">Type <select name="type" defaultValue={defaultActionType(data, app.stage)}>{actionTypeOptions(data.workflow.action_types, "").map(type => <option key={type.id} value={type.id}>{type.label}</option>)}</select></label>
        <label className="form-field">Priority <select name="priority" defaultValue="medium"><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
        <label className="form-field">Due <input name="due_date" type="date" /></label>
        <label className="form-field">Related URL <input name="related_url" type="url" /></label>
        <label className="form-field full">Description <textarea name="description" /></label>
        <label className="form-field full">Notes <textarea name="notes" /></label>
        <div className="form-field full"><button className="button primary compact" type="submit">Add action</button></div>
      </form> : null}
    </div>
  );
}

function actionValues(form: FormData): ActionUpdates {
  return {
    title: String(form.get("title") || ""),
    type: String(form.get("type") || ""),
    priority: String(form.get("priority") || ""),
    due_date: String(form.get("due_date") || ""),
    related_url: String(form.get("related_url") || ""),
    description: String(form.get("description") || ""),
    notes: String(form.get("notes") || "")
  };
}

function actionTypeOptions(types: AppState["workflow"]["action_types"], current: string) {
  return types.filter(type => type.is_active === "1" || type.id === current);
}

function defaultActionType(data: AppState, stage: string) {
  return data.workflow.action_types.find(type => type.is_active === "1" && (!type.allowed_stages || type.allowed_stages.split(",").includes(stage)))?.id || "";
}

function TagEditor({
  disabled = false,
  tags,
  existingTags,
  inputValue,
  setInputValue,
  setTags,
  showLabel = true
}: {
  disabled?: boolean;
  tags: string[];
  existingTags: string[];
  inputValue: string;
  setInputValue: (value: string) => void;
  setTags: (tags: string[]) => void;
  showLabel?: boolean;
}) {
  const normalizedInput = normalizeTag(inputValue);
  const suggestions = existingTags
    .filter(tag => !tags.includes(tag))
    .filter(tag => !normalizedInput || tag.includes(normalizedInput))
    .slice(0, 6);

  function addTag(rawValue = inputValue) {
    const tag = normalizeTag(rawValue);
    if (!tag || tags.includes(tag)) {
      setInputValue("");
      return;
    }
    setTags([...tags, tag]);
    setInputValue("");
  }

  function removeTag(tag: string) {
    setTags(tags.filter(item => item !== tag));
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter" && event.key !== ",") return;
    event.preventDefault();
    addTag();
  }

  return (
    <div className={showLabel ? "form-field full tag-editor-field" : "tag-editor-field"}>
      {showLabel ? <span>Tags</span> : null}
      <input name="tags" type="hidden" value={tags.join(",")} />
      <div className="tag-editor">
        <div className="tag-editor-chips">
          {tags.length ? tags.map(tag => (
            <button className={`tag-chip editable ${tagColorClass(tag)}`} disabled={disabled} key={tag} type="button" onClick={() => removeTag(tag)} title={`Remove ${tag}`}>
              {tag}<span aria-hidden="true">x</span>
            </button>
          )) : <span className="tag-chip tag-color-muted">no-tags</span>}
        </div>
        <div className="tag-editor-input-row">
          <input
            value={inputValue}
            disabled={disabled}
            onChange={event => setInputValue(event.target.value)}
            onKeyDown={handleKeyDown}
            type="text"
            placeholder="Add tag"
            list="posting-tag-suggestions"
          />
          <button className="button compact" disabled={disabled} type="button" onClick={() => addTag()}>Add</button>
          <datalist id="posting-tag-suggestions">
            {existingTags.filter(tag => !tags.includes(tag)).map(tag => <option key={tag} value={tag} />)}
          </datalist>
        </div>
        {suggestions.length ? (
          <div className="tag-suggestions">
            {suggestions.map(tag => (
              <button className="tag-suggestion" disabled={disabled} key={tag} type="button" onClick={() => addTag(tag)}>{tag}</button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function stageOptions(data: AppState, currentStage: string) {
  const stages = data.workflow.stages.filter(stage => stage.is_active === "1" || stage.id === currentStage);
  if (currentStage && !stages.some(stage => stage.id === currentStage)) {
    return [...stages, { id: currentStage, label: titleCase(currentStage), sort_order: "999", is_terminal: "", is_active: "" }];
  }
  return stages;
}

function blankApplication(data: AppState): Application {
  return {
    id: "",
    company_id: "",
    company: "",
    role: "",
    location: "",
    work_mode: "",
    source: "",
    source_url: "",
    compensation: "",
    stage: data.workflow.stages.find(stage => stage.id === "posting-review" && stage.is_active === "1")?.id
      || data.workflow.stages.find(stage => stage.is_active === "1")?.id
      || "posting-review",
    outcome: "",
    tags: "",
    priority: "medium",
    date_found: data.generated_date,
    date_applied: "",
    next_action_id: "",
    next_action: "",
    next_action_date: "",
    contact: "",
    resume_version: "",
    cover_letter: "",
    notes: "",
    posting_file: "",
    posting_markdown: "",
    posting_file_exists: false,
    tag_list: [],
    is_closed: false,
    is_active: true,
    is_overdue: false,
    is_due_soon: false,
    days_until_next_action: null,
    sort_due: ""
  };
}
