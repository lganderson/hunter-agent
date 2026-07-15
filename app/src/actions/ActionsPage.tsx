import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ActionCommand, ActionDue, Priority, StatusPill } from "../components/Primitives";
import { FilterIcon, SearchIcon } from "../components/Icons";
import { isActionComplete, titleCase } from "../core/format";
import { updateAction } from "../core/api";
import type { Action, AppState } from "../core/types";

type ActionsPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

function unique(actions: Action[], field: keyof Action) {
  return [...new Set(actions.map(action => String(action[field] || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

export function ActionsPage({ data, refresh }: ActionsPageProps) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [type, setType] = useState("all");
  const [status, setStatus] = useState(() => searchParams.get("status") || "open");
  const [priority, setPriority] = useState("all");
  const [due, setDue] = useState(() => validDueFilter(searchParams.get("due")));
  const [operationStatus, setOperationStatus] = useState("");
  const queryKey = searchParams.toString();

  useEffect(() => {
    const params = new URLSearchParams(queryKey);
    setStatus(params.get("status") || "open");
    setDue(validDueFilter(params.get("due")));
  }, [queryKey]);

  const rows = data.actions
    .filter(action => {
      const haystack = [
        action.id,
        action.application_id,
        action.company,
        action.role,
        action.type,
        action.title,
        action.description,
        action.status,
        action.priority,
        action.notes
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
    .sort((a, b) => {
      const completeDelta = Number(isActionComplete(a)) - Number(isActionComplete(b));
      if (completeDelta) return completeDelta;
      return a.sort_due.localeCompare(b.sort_due) || (a.company || "").localeCompare(b.company || "");
    });

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
    setSearch("");
    setType("all");
    setStatus("open");
    setPriority("all");
    setDue("all");
    setSearchParams({});
  }

  return (
    <section className="view-section" id="actions-view" aria-label="Actions">
      <article className="panel">
        <div className="panel-header"><h2 className="panel-title">Actions</h2></div>
        <div className="toolbar" aria-label="Action filters">
          <label className="search">
            <span className="sr-only">Search actions</span>
            <SearchIcon />
            <input value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="Search actions, companies, notes..." />
          </label>
          <Filter label="Type" value={type} values={unique(data.actions, "type")} onChange={setType} />
          <Filter label="Status" value={status} values={["open", ...unique(data.actions, "status").filter(item => item !== "open")]} onChange={setStatus} />
          <Filter label="Priority" value={priority} values={unique(data.actions, "priority")} onChange={setPriority} />
          <Filter label="Due" value={due} values={["overdue", "upcoming"]} onChange={setDue} />
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
        </div>
        <div className="table-scroll">
          <table className="simple-table">
            <thead>
              <tr>
                <th>Action</th>
                <th>Type</th>
                <th>Status</th>
                <th>Priority</th>
                <th>Due date</th>
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
