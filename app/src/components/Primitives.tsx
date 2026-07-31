import type { Action, Application } from "../core/types";
import { actionDueLabel, cssClass, isActionComplete, tagColorClass, tagList, titleCase } from "../core/format";
import type { SortDirection } from "../core/tableSort";

export function SortableHeader<Key extends string>({
  activeKey,
  className = "",
  direction,
  initialDirection = "asc",
  label,
  onSort,
  sortKey
}: {
  activeKey: Key;
  className?: string;
  direction: SortDirection;
  initialDirection?: SortDirection;
  label: string;
  onSort: (key: Key, initialDirection: SortDirection) => void;
  sortKey: Key;
}) {
  const active = activeKey === sortKey;
  const nextDirection = active ? direction === "asc" ? "descending" : "ascending" : initialDirection === "asc" ? "ascending" : "descending";
  return (
    <th aria-sort={active ? direction === "asc" ? "ascending" : "descending" : "none"} className={`sortable-th ${className}`.trim()}>
      <button
        aria-label={`Sort by ${label}, ${nextDirection}`}
        className="table-sort-button"
        type="button"
        onClick={() => onSort(sortKey, initialDirection)}
      >
        <span>{label}</span>
        <span className={`table-sort-indicator${active ? " active" : ""}`} aria-hidden="true">{active ? direction === "asc" ? "↑" : "↓" : "↕"}</span>
      </button>
    </th>
  );
}

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
  busy = false,
  busyLabel = "Saving…",
  disabled = false,
  onUpdate
}: {
  action: Action;
  busy?: boolean;
  busyLabel?: string;
  disabled?: boolean;
  onUpdate: (actionId: string, status: string) => void;
}) {
  const complete = isActionComplete(action);
  const nextStatus = complete ? "open" : "done";
  return (
    <button
      aria-busy={busy}
      className={`button compact${complete ? "" : " primary"}`}
      disabled={disabled || busy}
      type="button"
      onClick={() => onUpdate(action.id, nextStatus)}
    >
      {busy ? busyLabel : complete ? "Reopen" : "Done"}
    </button>
  );
}

export function ActionDue({ action }: { action: Action }) {
  const dueClass = action.is_overdue ? "overdue" : action.is_due_soon ? "soon" : "";
  return <span className={`due ${dueClass}`}>{actionDueLabel(action) || "None"}</span>;
}
