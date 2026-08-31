import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { FilterIcon, PlusIcon, SearchIcon } from "../components/Icons";
import { Priority, SortableHeader, TagList } from "../components/Primitives";
import { DATA_QUALITY_TAGS, dueLabel, normalize, tagList, titleCase } from "../core/format";
import { isWithinPastDays } from "../core/date";
import { compareNumber, compareText, nextSortState, type SortDirection, type SortState } from "../core/tableSort";
import type { AppState, Application } from "../core/types";
import { selectionFromParam, selectionParamValue, sortFromParams, usePersistentViewParams } from "../core/viewState";

type PostingSortKey = "posting" | "stage" | "company" | "tags" | "priority" | "next_action";
const POSTING_SORT_KEYS: PostingSortKey[] = ["posting", "stage", "company", "tags", "priority", "next_action"];

function unique(applications: Application[], field: keyof Application) {
  return [...new Set(applications.map(app => String(app[field] || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

export function PostingsPage({ data }: { data: AppState }) {
  const navigate = useNavigate();
  const { params: viewParams, updateParams: updateViewParams, clearParams: clearViewParams } = usePersistentViewParams("postings");
  const search = viewParams.get("q") || "";
  const stageValues = useMemo(() => orderedStages(data), [data]);
  const defaultStages = useMemo(() => stageValues.filter(stage => stage !== "closed"), [stageValues]);
  const outcomeValues = useMemo(() => unique(data.applications, "outcome"), [data.applications]);
  const tagValues = useMemo(() => [...new Set(data.applications.flatMap(tagList))].sort((a, b) => a.localeCompare(b)), [data.applications]);
  const priorityValues = useMemo(() => unique(data.applications, "priority"), [data.applications]);
  const companyValues = useMemo(() => unique(data.applications, "company"), [data.applications]);
  const sourceValues = useMemo(() => unique(data.applications, "source"), [data.applications]);
  const stages = selectionFromParam(viewParams.get("stages"), stageValues, defaultStages);
  const outcomes = selectionFromParam(viewParams.get("outcomes"), outcomeValues, outcomeValues);
  const tags = selectionFromParam(viewParams.get("tags"), tagValues, tagValues);
  const priorities = selectionFromParam(viewParams.get("priorities"), priorityValues, priorityValues);
  const companies = selectionFromParam(viewParams.get("companies"), companyValues, companyValues);
  const sources = selectionFromParam(viewParams.get("sources"), sourceValues, sourceValues);
  const dueOnly = viewParams.get("due") === "true";
  const attention = viewParams.get("attention") || "";
  const applied = viewParams.get("applied") || "";
  const sort = sortFromParams(viewParams, "sort", "direction", POSTING_SORT_KEYS, { key: "next_action", direction: "asc" });

  const rows = data.applications
    .filter(app => {
      const haystack = [
        app.id,
        app.company,
        app.role,
        app.location,
        app.work_mode,
        app.source,
        app.compensation,
        app.stage,
        app.outcome,
        app.tags,
        tagList(app).join(" "),
        app.next_action,
        app.notes
      ].join(" ").toLowerCase();
      const query = search.toLowerCase();
      if (query && !haystack.includes(query)) return false;
      if (!matchesSelection(app.stage, stages, stageValues)) return false;
      if (!matchesSelection(app.outcome, outcomes, outcomeValues)) return false;
      if (!matchesAnySelection(tagList(app), tags, tagValues)) return false;
      if (!matchesSelection(app.priority, priorities, priorityValues)) return false;
      if (!matchesSelection(app.company, companies, companyValues)) return false;
      if (!matchesSelection(app.source, sources, sourceValues)) return false;
      if (dueOnly && !app.is_due_soon && !app.is_overdue) return false;
      if (attention === "missing-next" && (app.is_closed || Boolean(normalize(app.next_action)))) return false;
      if (attention === "data-quality" && !tagList(app).some(tag => DATA_QUALITY_TAGS.has(tag))) return false;
      if (applied === "last-7-days" && !isWithinPastDays(app.date_applied, data.generated_date, 7)) return false;
      return true;
    })
    .sort((a, b) => comparePostingRows(a, b, sort));

  function changeSort(key: PostingSortKey, initialDirection: SortDirection) {
    const next = nextSortState(sort, key, initialDirection);
    updateViewParams({
      sort: next.key === "next_action" ? null : next.key,
      direction: next.direction === "asc" ? null : next.direction
    });
  }

  function clearFilters() {
    clearViewParams();
  }

  return (
    <section className="view-section workspace" id="postings-view" aria-label="Posting workspace">
      <article className="panel">
        <div className="panel-header postings-header">
          <div><h2 className="panel-title">Postings</h2><p>Track opportunities from discovery through close.</p></div>
          <button className="button primary" type="button" onClick={() => navigate("/postings/new")}><PlusIcon /> Add posting</button>
        </div>
        <div className="toolbar" aria-label="Posting filters">
          <label className="search">
            <span className="sr-only">Search postings</span>
            <SearchIcon />
            <input value={search} onChange={event => updateViewParams({ q: event.target.value || null })} type="search" placeholder="Search postings, companies, notes..." />
          </label>
          <MultiFilter label="Stage" values={stageValues} selected={stages} onChange={values => updateViewParams({ stages: selectionParamValue(values, stageValues, defaultStages) })} />
          <MultiFilter label="Outcome" values={outcomeValues} selected={outcomes} onChange={values => updateViewParams({ outcomes: selectionParamValue(values, outcomeValues, outcomeValues) })} />
          <MultiFilter label="Tag" values={tagValues} selected={tags} onChange={values => updateViewParams({ tags: selectionParamValue(values, tagValues, tagValues) })} />
          <MultiFilter label="Priority" values={priorityValues} selected={priorities} onChange={values => updateViewParams({ priorities: selectionParamValue(values, priorityValues, priorityValues) })} />
          <MultiFilter label="Company" values={companyValues} selected={companies} onChange={values => updateViewParams({ companies: selectionParamValue(values, companyValues, companyValues) })} />
          <MultiFilter label="Source" values={sourceValues} selected={sources} onChange={values => updateViewParams({ sources: selectionParamValue(values, sourceValues, sourceValues) })} />
          <label className="toggle"><input checked={dueOnly} onChange={event => updateViewParams({ due: event.target.checked ? "true" : null })} type="checkbox" /> Due soon</label>
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
          {attention ? <span className="active-filter">Attention: {attention === "missing-next" ? "Missing next action" : "Data cleanup"}</span> : null}
          {applied ? <span className="active-filter">Applied: Last 7 days</span> : null}
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Posting" onSort={changeSort} sortKey="posting" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Stage" onSort={changeSort} sortKey="stage" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Company" onSort={changeSort} sortKey="company" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Tags" onSort={changeSort} sortKey="tags" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Priority" onSort={changeSort} sortKey="priority" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Next action" onSort={changeSort} sortKey="next_action" />
              </tr>
            </thead>
            <tbody>
              {rows.map(app => {
                const dueClass = app.is_overdue ? "overdue" : app.is_due_soon ? "soon" : "";
                const openPosting = () => navigate(`/postings/${encodeURIComponent(app.id)}`);
                return (
                  <tr
                    key={app.id}
                    data-id={app.id}
                    tabIndex={0}
                    onClick={openPosting}
                    onKeyDown={event => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      event.preventDefault();
                      openPosting();
                    }}
                    aria-label={`Open ${app.role || app.id} at ${app.company || "unknown company"}`}
                  >
                    <td className="role-cell"><div className="row-select"><strong>{app.role}</strong><span>{app.location || "Location unknown"}</span></div></td>
                    <td>{titleCase(app.stage)}</td>
                    <td>{app.company || "Unknown company"}</td>
                    <td><TagList app={app} /></td>
                    <td><Priority value={app.priority} /></td>
                    <td className="next-action-cell">
                      <strong>{app.next_action || "None"}</strong>
                      <span className={`due ${dueClass}`}>{dueLabel(app) || "No due date"}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="empty-state" style={{ display: rows.length ? "none" : "block" }}>No postings match the current filters.</div>
        </div>
      </article>
    </section>
  );
}

function comparePostingRows(left: Application, right: Application, sort: SortState<PostingSortKey>) {
  let result = 0;
  if (sort.key === "posting") result = compareText(left.role, right.role, sort.direction);
  if (sort.key === "stage") result = compareText(left.stage, right.stage, sort.direction);
  if (sort.key === "company") result = compareText(left.company, right.company, sort.direction);
  if (sort.key === "tags") result = compareText(tagList(left).join(" "), tagList(right).join(" "), sort.direction);
  if (sort.key === "priority") result = compareNumber(priorityRank(left.priority), priorityRank(right.priority), sort.direction);
  if (sort.key === "next_action") result = compareText(left.next_action_date, right.next_action_date, sort.direction);
  return result || compareText(left.company, right.company, "asc") || compareText(left.id, right.id, "asc");
}

function priorityRank(priority: string) {
  if (priority === "high") return 3;
  if (priority === "medium") return 2;
  if (priority === "low") return 1;
  return Number.NaN;
}

function MultiFilter({
  label,
  values,
  selected,
  onChange
}: {
  label: string;
  values: string[];
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const allSelected = values.length === selected.length;
  const summary = allSelected ? "All" : selected.length === 1 ? titleCase(selected[0]) : `${selected.length} selected`;

  function toggle(value: string) {
    onChange(selected.includes(value) ? selected.filter(item => item !== value) : [...selected, value]);
  }

  return (
    <details className="filter multi-filter">
      <summary>{label} <span>{summary}</span></summary>
      <div className="multi-filter-menu">
        <label className="multi-filter-option">
          <input checked={allSelected} onChange={event => onChange(event.target.checked ? values : [])} type="checkbox" />
          All
        </label>
        {values.map(value => (
          <label className="multi-filter-option" key={value}>
            <input checked={selected.includes(value)} onChange={() => toggle(value)} type="checkbox" />
            {titleCase(value)}
          </label>
        ))}
      </div>
    </details>
  );
}

function orderedStages(data: AppState) {
  const workflowStages = data.workflow.stages.map(stage => stage.id);
  const existing = unique(data.applications, "stage");
  return [
    ...workflowStages.filter(stage => existing.includes(stage)),
    ...existing.filter(stage => !workflowStages.includes(stage))
  ];
}

function matchesSelection(value: string, selected: string[], values: string[]) {
  if (!values.length || selected.length === values.length) return true;
  return selected.includes(value);
}

function matchesAnySelection(values: string[], selected: string[], allValues: string[]) {
  if (!allValues.length || selected.length === allValues.length) return true;
  return values.some(value => selected.includes(value));
}
