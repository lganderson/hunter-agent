import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ActionCommand, ActionDue, Priority, SortableHeader, StatusPill } from "../components/Primitives";
import { FilterIcon, SearchIcon } from "../components/Icons";
import { isActionComplete, titleCase } from "../core/format";
import { updateAction } from "../core/api";
import { compareNumber, compareText, nextSortState, type SortDirection, type SortState } from "../core/tableSort";
import type { Action, AppState } from "../core/types";
import { sortFromParams, usePersistentViewParams } from "../core/viewState";

type ActionSortKey = "action" | "type" | "status" | "priority" | "due_date";
const ACTION_SORT_KEYS: ActionSortKey[] = ["action", "type", "status", "priority", "due_date"];

type ActionsPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

function unique(actions: Action[], field: keyof Action) {
  return [...new Set(actions.map(action => String(action[field] || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

export function ActionsPage({ data, refresh }: ActionsPageProps) {
  const navigate = useNavigate();
  const { params: viewParams, updateParams: updateViewParams, clearParams: clearViewParams } = usePersistentViewParams("actions");
  const search = viewParams.get("q") || "";
  const type = viewParams.get("type") || "all";
  const status = viewParams.get("status") || "open";
  const priority = viewParams.get("priority") || "all";
  const due = validDueFilter(viewParams.get("due"));
  const [operationStatus, setOperationStatus] = useState("");
  const sort = sortFromParams(viewParams, "sort", "direction", ACTION_SORT_KEYS, { key: "due_date", direction: "asc" });

  const rows = data.actions
    .filter(action => {
      const haystack = [
        action.id,
        action.application_id,
        action.company,
        action.role,
        action.type,
        action.title,
        action.status,
        action.priority
      ].join(" ").toLowerCase();
      const query = search.toLowerCase();
      if (query && !haystack.includes(query)) return false;
      if (type !== "all" && action.type !== type) return false;
      if (status !== "all") {
        if (status === "open" && isActionComplete(action)) return false;
        if (status !== "open" && action.status !== status) return false;
      }
      if (priority !== "all" && action.priority !== priority) return false;
      if (due === "overdue" && !action.is_overdue) return false;
      if (due === "upcoming" && (!action.is_due_soon || action.is_overdue)) return false;
      return true;
    })
    .sort((a, b) => compareActionRows(a, b, sort));

  function changeSort(key: ActionSortKey, initialDirection: SortDirection) {
    const next = nextSortState(sort, key, initialDirection);
    updateViewParams({
      sort: next.key === "due_date" ? null : next.key,
      direction: next.direction === "asc" ? null : next.direction
    });
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

  function clearFilters() {
    clearViewParams();
  }

  return (
    <section className="view-section" id="actions-view" aria-labelledby="actions-title">
      <article className="panel">
        <div className="panel-header"><h1 className="panel-title" id="actions-title">Actions</h1></div>
        <div className="toolbar" aria-label="Action filters">
          <label className="search">
            <span className="sr-only">Search actions</span>
            <SearchIcon />
            <input value={search} onChange={event => updateViewParams({ q: event.target.value || null })} type="search" placeholder="Search actions and companies..." />
          </label>
          <Filter label="Type" value={type} values={unique(data.actions, "type")} onChange={value => updateViewParams({ type: value === "all" ? null : value })} />
          <Filter label="Status" value={status} values={["open", ...unique(data.actions, "status").filter(item => item !== "open")]} onChange={value => updateViewParams({ status: value === "open" ? null : value })} />
          <Filter label="Priority" value={priority} values={unique(data.actions, "priority")} onChange={value => updateViewParams({ priority: value === "all" ? null : value })} />
          <Filter label="Due" value={due} values={["overdue", "upcoming"]} onChange={value => updateViewParams({ due: value === "all" ? null : value })} />
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
        </div>
        <div className="table-scroll">
          <table className="simple-table">
            <thead>
              <tr>
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Action" onSort={changeSort} sortKey="action" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Type" onSort={changeSort} sortKey="type" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Status" onSort={changeSort} sortKey="status" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Priority" onSort={changeSort} sortKey="priority" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Due date" onSort={changeSort} sortKey="due_date" />
                <th>Update</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(action => (
                <tr key={action.id} data-id={action.application_id}>
                  <td className="role-cell"><button className="row-select" type="button" onClick={() => navigate(`/postings/${encodeURIComponent(action.application_id)}`)}><strong>{action.title || "Untitled action"}</strong><span>{action.company || "Unknown company"} · {action.role || "No linked posting"}</span></button></td>
                  <td>{titleCase(action.type)}</td>
                  <td><StatusPill value={action.status} /></td>
                  <td><Priority value={action.priority} /></td>
                  <td><ActionDue action={action} /></td>
                  <td className="action-command-cell"><ActionCommand action={action} onUpdate={changeAction} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="empty-state" style={{ display: rows.length ? "none" : "block" }}>No actions match the current filters.</div>
        </div>
        <div className="action-status">{operationStatus}</div>
      </article>
    </section>
  );
}

function compareActionRows(left: Action, right: Action, sort: SortState<ActionSortKey>) {
  let result = 0;
  if (sort.key === "action") result = compareText(left.title, right.title, sort.direction);
  if (sort.key === "type") result = compareText(left.type, right.type, sort.direction);
  if (sort.key === "status") result = compareText(left.status, right.status, sort.direction);
  if (sort.key === "priority") result = compareNumber(actionPriorityRank(left.priority), actionPriorityRank(right.priority), sort.direction);
  if (sort.key === "due_date") {
    result = Number(isActionComplete(left)) - Number(isActionComplete(right));
    if (!result) result = compareText(left.due_date, right.due_date, sort.direction);
  }
  return result || compareText(left.company, right.company, "asc") || compareText(left.id, right.id, "asc");
}

function actionPriorityRank(priority: string) {
  if (priority === "high") return 3;
  if (priority === "medium") return 2;
  if (priority === "low") return 1;
  return Number.NaN;
}

function validDueFilter(value: string | null) {
  return value === "overdue" || value === "upcoming" ? value : "all";
}

function Filter({ label, value, values, onChange }: { label: string; value: string; values: string[]; onChange: (value: string) => void }) {
  return (
    <label className="filter">{label} <select value={value} onChange={event => onChange(event.target.value)}>
      <option value="all">All</option>
      {values.map(item => <option key={item} value={item}>{titleCase(item)}</option>)}
    </select></label>
  );
}
