import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { FilterIcon, SearchIcon } from "../components/Icons";
import { Priority, TagList } from "../components/Primitives";
import { DATA_QUALITY_TAGS, dueLabel, normalize, tagList, titleCase } from "../core/format";
import { isWithinPastDays } from "../core/date";
import type { AppState, Application } from "../core/types";

function unique(applications: Application[], field: keyof Application) {
  return [...new Set(applications.map(app => String(app[field] || "").trim()).filter(Boolean))].sort((a, b) => a.localeCompare(b));
}

export function PostingsPage({ data }: { data: AppState }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const stageValues = useMemo(() => orderedStages(data), [data]);
  const defaultStages = useMemo(() => stageValues.filter(stage => stage !== "closed"), [stageValues]);
  const outcomeValues = useMemo(() => unique(data.applications, "outcome"), [data.applications]);
  const tagValues = useMemo(() => [...new Set(data.applications.flatMap(tagList))].sort((a, b) => a.localeCompare(b)), [data.applications]);
  const priorityValues = useMemo(() => unique(data.applications, "priority"), [data.applications]);
  const companyValues = useMemo(() => unique(data.applications, "company"), [data.applications]);
  const sourceValues = useMemo(() => unique(data.applications, "source"), [data.applications]);
  const [stages, setStages] = useState<string[]>(() => querySelection(searchParams.get("stages"), stageValues, defaultStages));
  const [outcomes, setOutcomes] = useState<string[]>(() => querySelection(searchParams.get("outcomes"), outcomeValues, outcomeValues));
  const [tags, setTags] = useState<string[]>(() => querySelection(searchParams.get("tags"), tagValues, tagValues));
  const [priorities, setPriorities] = useState<string[]>(priorityValues);
  const [companies, setCompanies] = useState<string[]>(() => querySelection(searchParams.get("companies"), companyValues, companyValues));
  const [sources, setSources] = useState<string[]>(sourceValues);
  const [dueOnly, setDueOnly] = useState(false);
  const [attention, setAttention] = useState(() => searchParams.get("attention") || "");
  const [applied, setApplied] = useState(() => searchParams.get("applied") || "");

  const queryKey = searchParams.toString();

  useEffect(() => setStages(previous => reconcileSelection(previous, stageValues, defaultStages)), [stageValues, defaultStages]);
  useEffect(() => setOutcomes(previous => reconcileSelection(previous, outcomeValues, outcomeValues)), [outcomeValues]);
  useEffect(() => setTags(previous => reconcileSelection(previous, tagValues, tagValues)), [tagValues]);
  useEffect(() => setPriorities(previous => reconcileSelection(previous, priorityValues, priorityValues)), [priorityValues]);
  useEffect(() => setCompanies(previous => reconcileSelection(previous, companyValues, companyValues)), [companyValues]);
  useEffect(() => setSources(previous => reconcileSelection(previous, sourceValues, sourceValues)), [sourceValues]);
  useEffect(() => {
    const params = new URLSearchParams(queryKey);
    setStages(querySelection(params.get("stages"), stageValues, defaultStages));
    setOutcomes(querySelection(params.get("outcomes"), outcomeValues, outcomeValues));
    setTags(querySelection(params.get("tags"), tagValues, tagValues));
    setCompanies(querySelection(params.get("companies"), companyValues, companyValues));
    setAttention(params.get("attention") || "");
    setApplied(params.get("applied") || "");
  }, [queryKey, stageValues, defaultStages, outcomeValues, tagValues, companyValues]);

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
        app.notes,
        app.posting_markdown
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
    .sort((a, b) => a.sort_due.localeCompare(b.sort_due) || (a.company || "").localeCompare(b.company || ""));

  function clearFilters() {
    setSearch("");
    setStages(defaultStages);
    setOutcomes(outcomeValues);
    setTags(tagValues);
    setPriorities(priorityValues);
    setCompanies(companyValues);
    setSources(sourceValues);
    setDueOnly(false);
    setAttention("");
    setApplied("");
    setSearchParams({});
  }

  return (
    <section className="view-section workspace" id="postings-view" aria-label="Posting workspace">
      <article className="panel">
        <div className="toolbar" aria-label="Posting filters">
          <label className="search">
            <span className="sr-only">Search postings</span>
            <SearchIcon />
            <input value={search} onChange={event => setSearch(event.target.value)} type="search" placeholder="Search postings, companies, notes..." />
          </label>
          <MultiFilter label="Stage" values={stageValues} selected={stages} onChange={setStages} />
          <MultiFilter label="Outcome" values={outcomeValues} selected={outcomes} onChange={setOutcomes} />
          <MultiFilter label="Tag" values={tagValues} selected={tags} onChange={setTags} />
          <MultiFilter label="Priority" values={priorityValues} selected={priorities} onChange={setPriorities} />
          <MultiFilter label="Company" values={companyValues} selected={companies} onChange={setCompanies} />
          <MultiFilter label="Source" values={sourceValues} selected={sources} onChange={setSources} />
          <label className="toggle"><input checked={dueOnly} onChange={event => setDueOnly(event.target.checked)} type="checkbox" /> Due soon</label>
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
          {attention ? <span className="active-filter">Attention: {attention === "missing-next" ? "Missing next action" : "Data cleanup"}</span> : null}
          {applied ? <span className="active-filter">Applied: Last 7 days</span> : null}
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Posting</th>
                <th>Stage</th>
                <th>Company</th>
                <th>Tags</th>
                <th>Priority</th>
                <th>Next action</th>
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

function reconcileSelection(previous: string[], values: string[], fallback: string[]) {
  const selected = previous.filter(value => values.includes(value));
  return selected.length ? selected : fallback;
}

function querySelection(value: string | null, options: string[], fallback: string[]) {
  if (!value) return fallback;
  if (value === "all") return options;
  const requested = value.split(",").filter(item => options.includes(item));
  return requested.length ? requested : fallback;
}

function matchesSelection(value: string, selected: string[], values: string[]) {
  if (!values.length || selected.length === values.length) return true;
  return selected.includes(value);
}

function matchesAnySelection(values: string[], selected: string[], allValues: string[]) {
  if (!allValues.length || selected.length === allValues.length) return true;
  return values.some(value => selected.includes(value));
}
