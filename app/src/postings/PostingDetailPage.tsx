import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ActionCommand } from "../components/Primitives";
import { ExternalIcon, FilterIcon, ListIcon } from "../components/Icons";
import { makeNextAction, updateAction, updateActionFields, updateApplication } from "../core/api";
import { actionDueLabel, dueLabel, isActionComplete, markdownToHtml, normalizeTag, tagColorClass, tagList, titleCase } from "../core/format";
import type { Action, AppState, Application } from "../core/types";

type DetailProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

export function PostingDetailPage({ data, refresh }: DetailProps) {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const app = data.applications.find(item => item.id === id) || data.applications[0];
  const [operationStatus, setOperationStatus] = useState("");
  const [stageDraft, setStageDraft] = useState(app?.stage || "");
  const [tagDraft, setTagDraft] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const existingTags = useMemo(
    () => [...new Set(data.applications.flatMap(tagList))].sort((a, b) => a.localeCompare(b)),
    [data.applications]
  );

  useEffect(() => {
    setStageDraft(app?.stage || "");
    setTagDraft(app ? tagList(app) : []);
    setTagInput("");
  }, [app?.id, app?.stage, app?.tags]);

  if (!app) {
    return <div className="empty-state" style={{ display: "block" }}>No posting is available.</div>;
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

  async function savePosting(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setOperationStatus("Saving posting...");
    try {
      await updateApplication(app.id, {
        company: String(form.get("company") || ""),
        company_id: String(form.get("company_id") || ""),
        stage: String(form.get("stage") || ""),
        outcome: String(form.get("outcome") || ""),
        priority: String(form.get("priority") || ""),
        date_applied: String(form.get("date_applied") || ""),
        tags: String(form.get("tags") || ""),
        contact: String(form.get("contact") || ""),
        resume_version: String(form.get("resume_version") || ""),
        cover_letter: String(form.get("cover_letter") || ""),
        notes: String(form.get("notes") || "")
      });
      await refresh();
      setOperationStatus("Posting saved.");
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

  async function saveActionDate(actionId: string, dueDate: string) {
    setOperationStatus("Saving action date...");
    try {
      await updateActionFields(actionId, { due_date: dueDate });
      await refresh();
      setOperationStatus("Action date saved.");
    } catch (error) {
      setOperationStatus(`Could not save action date. Run make serve-app. ${error instanceof Error ? error.message : String(error)}`);
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

  return (
    <section className="view-section posting-detail" id="posting-detail-view" aria-label="Posting detail">
      <article className="panel">
        <div className="detail-topline">
          <div>
            <h2>{app.role || "Untitled posting"}</h2>
            <p>{app.company || "Unknown company"} · {app.stage || "No stage"}{app.outcome ? ` · ${app.outcome}` : ""}</p>
          </div>
          <div className="detail-actions">
            <button className="button" type="button" onClick={() => navigate("/postings")}><ListIcon /> Back</button>
            <a className="button" href={app.source_url || "#"} target="_blank" rel="noreferrer" aria-disabled={app.source_url ? "false" : "true"}><ExternalIcon size={16} /> Source</a>
          </div>
        </div>
        <div className="detail-grid">
          {detailItems(app).map(item => <DetailItem key={item.label} {...item} />)}
        </div>
      </article>

      <div className="detail-layout">
        <article className="panel">
          <div className="panel-header"><h2 className="panel-title">Manage Tracking</h2></div>
          <form className="management-form" onSubmit={savePosting} key={app.id}>
            <label className="form-field">Company <input name="company" type="text" defaultValue={app.company || ""} /></label>
            <label className="form-field">Managed company <select name="company_id" defaultValue={app.company_id || ""}>
              <option value="">None</option>
              {data.companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}
            </select></label>
            <label className="form-field">Stage <select name="stage" value={stageDraft} onChange={event => setStageDraft(event.target.value)}>
              {stageOptions(data, app.stage).map(stage => <option key={stage.id} value={stage.id}>{stage.label}</option>)}
            </select></label>
            {stageDraft === "closed" ? <label className="form-field">Outcome <select name="outcome" defaultValue={app.outcome || ""}>
              <option value=""></option>
              {data.workflow.outcomes.map(outcome => <option key={outcome} value={outcome}>{titleCase(outcome)}</option>)}
            </select></label> : <input name="outcome" type="hidden" value="" />}
            <label className="form-field">Priority <select name="priority" defaultValue={app.priority || ""}><option value=""></option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select></label>
            <label className="form-field">Date applied <input name="date_applied" type="date" defaultValue={app.date_applied || ""} /></label>
            <TagEditor
              tags={tagDraft}
              existingTags={existingTags}
              inputValue={tagInput}
              setInputValue={setTagInput}
              setTags={setTagDraft}
            />
            <label className="form-field">Contact <input name="contact" type="text" defaultValue={app.contact || ""} /></label>
            <label className="form-field">Resume version <input name="resume_version" type="text" defaultValue={app.resume_version || ""} /></label>
            <label className="form-field">Cover letter <input name="cover_letter" type="text" defaultValue={app.cover_letter || ""} /></label>
            <label className="form-field full">Notes <textarea name="notes" defaultValue={app.notes || ""} /></label>
            <div className="detail-actions form-field full">
              <button className="button primary" type="submit"><FilterIcon size={16} /> Save Changes</button>
            </div>
          </form>
          <div className="detail-status">{operationStatus}</div>
        </article>

        <article className="panel">
          <div className="panel-header"><h2 className="panel-title">Related Actions</h2></div>
          <div className="related-actions">
            {related.length ? related.map(action => (
              <div className="related-action" key={action.id}>
                <div>
                  <strong>{action.title || "Untitled action"}</strong>
                  <span>{titleCase(action.type)} · {titleCase(action.status)} · {actionDueLabel(action) || "No due date"}</span>
                </div>
                <ActionControls
                  action={action}
                  isNext={app.next_action_id === action.id}
                  onDateSave={saveActionDate}
                  onMakeNext={chooseNextAction}
                  onStatusUpdate={changeAction}
                />
              </div>
            )) : <div className="empty-state" style={{ display: "block" }}>No actions are linked to this posting.</div>}
          </div>
          <div className="panel-header"><h2 className="panel-title">Associated Contacts</h2></div>
          <div className="association-list">
            {linkedContacts.length ? linkedContacts.map(contact => (
              <div className="association-row" key={contact.id}>
                <div>
                  <strong>{contact.name || "Unnamed contact"}</strong>
                  <span>{[contact.company, contact.role, contact.status].filter(Boolean).join(" · ") || "No details"}</span>
                </div>
                <Link className="button compact" to="/contacts">Open</Link>
              </div>
            )) : <div className="empty-state" style={{ display: "block" }}>No contacts are associated with this posting.</div>}
          </div>
        </article>
      </div>

      <article className="panel">
        <div className="panel-header"><h2 className="panel-title">Posting Note</h2></div>
        <div className="note-view" dangerouslySetInnerHTML={{ __html: markdownToHtml(app.posting_markdown || "# No posting note\n\nNo Markdown note is available for this row.") }} />
      </article>
    </section>
  );
}

function ActionControls({
  action,
  isNext,
  onDateSave,
  onMakeNext,
  onStatusUpdate
}: {
  action: Action;
  isNext: boolean;
  onDateSave: (actionId: string, dueDate: string) => void;
  onMakeNext: (actionId: string) => void;
  onStatusUpdate: (actionId: string, status: string) => void;
}) {
  const [dueDate, setDueDate] = useState(action.due_date || "");
  const complete = isActionComplete(action);

  useEffect(() => {
    setDueDate(action.due_date || "");
  }, [action.id, action.due_date]);

  return (
    <div className="related-action-controls">
      <label className="action-date-field">
        <span>Due</span>
        <input value={dueDate} onChange={event => setDueDate(event.target.value)} type="date" disabled={complete} />
      </label>
      <button className="button compact" type="button" disabled={complete || dueDate === (action.due_date || "")} onClick={() => onDateSave(action.id, dueDate)}>Save date</button>
      <button className="button compact" type="button" disabled={complete || isNext} onClick={() => onMakeNext(action.id)}>{isNext ? "Next" : "Make next"}</button>
      <ActionCommand action={action} onUpdate={onStatusUpdate} />
    </div>
  );
}

function detailItems(app: Application) {
  return [
    { label: "ID", value: app.id },
    { label: "Company ID", value: app.company_id },
    { label: "Stage", value: titleCase(app.stage) },
    { label: "Outcome", value: app.outcome ? titleCase(app.outcome) : "" },
    { label: "Tags", value: tagList(app).join(", ") },
    { label: "Priority", value: titleCase(app.priority) },
    { label: "Location", value: app.location },
    { label: "Work mode", value: app.work_mode },
    { label: "Next action", value: app.next_action },
    { label: "Due", value: dueLabel(app) },
    { label: "Compensation", value: app.compensation },
    { label: "Source", value: app.source },
    { label: "Source URL", value: app.source_url, isLink: true },
    { label: "Date applied", value: app.date_applied },
    { label: "Contact", value: app.contact },
    { label: "Resume", value: app.resume_version },
    { label: "Cover letter", value: app.cover_letter }
  ];
}

function TagEditor({
  tags,
  existingTags,
  inputValue,
  setInputValue,
  setTags
}: {
  tags: string[];
  existingTags: string[];
  inputValue: string;
  setInputValue: (value: string) => void;
  setTags: (tags: string[]) => void;
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
    <div className="form-field full tag-editor-field">
      <span>Tags</span>
      <input name="tags" type="hidden" value={tags.join(",")} />
      <div className="tag-editor">
        <div className="tag-editor-chips">
          {tags.length ? tags.map(tag => (
            <button className={`tag-chip editable ${tagColorClass(tag)}`} key={tag} type="button" onClick={() => removeTag(tag)} title={`Remove ${tag}`}>
              {tag}<span aria-hidden="true">x</span>
            </button>
          )) : <span className="tag-chip tag-color-muted">no-tags</span>}
        </div>
        <div className="tag-editor-input-row">
          <input
            value={inputValue}
            onChange={event => setInputValue(event.target.value)}
            onKeyDown={handleKeyDown}
            type="text"
            placeholder="Add tag"
            list="posting-tag-suggestions"
          />
          <button className="button compact" type="button" onClick={() => addTag()}>Add</button>
          <datalist id="posting-tag-suggestions">
            {existingTags.filter(tag => !tags.includes(tag)).map(tag => <option key={tag} value={tag} />)}
          </datalist>
        </div>
        {suggestions.length ? (
          <div className="tag-suggestions">
            {suggestions.map(tag => (
              <button className="tag-suggestion" key={tag} type="button" onClick={() => addTag(tag)}>{tag}</button>
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

function DetailItem({ label, value, isLink = false }: { label: string; value: string; isLink?: boolean }) {
  const shown = value || "Not listed";
  return (
    <div className="detail-item">
      <span>{label}</span>
      {isLink && value ? <a href={value} target="_blank" rel="noreferrer">{shown}</a> : <strong>{shown}</strong>}
    </div>
  );
}
