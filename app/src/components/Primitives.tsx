import type { Action, Application } from "../core/types";
import { actionDueLabel, cssClass, isActionComplete, tagColorClass, tagList, titleCase } from "../core/format";

export function TagChip({ tag }: { tag: string }) {
  return <span className={`tag-chip ${tagColorClass(tag)}`}>{tag}</span>;
}

export function TagList({ app }: { app: Application }) {
  const tags = tagList(app);
  if (!tags.length) return <span className="tag-chip tag-color-muted">no-tags</span>;
  return <span className="tag-list">{tags.map(tag => <TagChip key={tag} tag={tag} />)}</span>;
}

export function StatusPill({ value }: { value: string }) {
  return <span className={`pill ${cssClass(value)}`}>{titleCase(value)}</span>;
}

export function Priority({ value }: { value: string }) {
  return <span className={`priority ${value || "blank"}`}>{titleCase(value)}</span>;
}

export function ActionCommand({
  action,
  onUpdate
}: {
  action: Action;
  onUpdate: (actionId: string, status: string) => void;
}) {
  const complete = isActionComplete(action);
  const nextStatus = complete ? "open" : "done";
  return (
    <button className={`button compact${complete ? "" : " primary"}`} type="button" onClick={() => onUpdate(action.id, nextStatus)}>
      {complete ? "Reopen" : "Done"}
    </button>
  );
}

export function ActionDue({ action }: { action: Action }) {
  const dueClass = action.is_overdue ? "overdue" : action.is_due_soon ? "soon" : "";
  return <span className={`due ${dueClass}`}>{actionDueLabel(action) || "None"}</span>;
}
