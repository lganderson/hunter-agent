import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  captureDiscoveryCandidates,
  ingestDiscoveryCandidate,
  runDiscoverySearch,
  updateDiscoveryCandidate,
  updateDiscoveryCandidateDetails,
  upsertDiscoverySearch
} from "../core/api";
import { dateOnlyLabel, titleCase } from "../core/format";
import { routes } from "../core/routes";
import type {
  AppState,
  DiscoveryCandidate,
  DiscoveryCandidateDetails,
  DiscoverySearch,
  DiscoverySearchLaneDefinition,
  DiscoverySearchUpdates
} from "../core/types";
import { BriefcaseIcon, ExternalIcon, FilterIcon, PlusIcon, SearchIcon, XIcon } from "../components/Icons";

type DiscoveryModeProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
};

type DiscoveryFilter = "latest" | "new" | "recommended" | "needs-details" | "all" | "ignored" | "ingested";

const DISCOVERY_FILTERS: Array<{ id: DiscoveryFilter; label: string }> = [
  { id: "latest", label: "Latest" },
  { id: "new", label: "New" },
  { id: "recommended", label: "Recommended" },
  { id: "needs-details", label: "Needs details" },
  { id: "all", label: "All" },
  { id: "ignored", label: "Ignored" },
  { id: "ingested", label: "Ingested" }
];

const WORK_MODE_OPTIONS: Array<{ id: DiscoverySearchLaneDefinition["work_modes"][number]; label: string }> = [
  { id: "on-site", label: "On-site" },
  { id: "hybrid", label: "Hybrid" },
  { id: "remote", label: "Remote" }
];
const EMPTY_DETAILS: DiscoveryCandidateDetails = {
  company: "",
  title: "",
  canonical_url: "",
  location: "",
  work_mode: "",
  description_text: "",
  notes: ""
};

export function DiscoveryMode({ data, refresh }: DiscoveryModeProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedSearchId = searchParams.get("search_id") || "";
  const selectedSearch = data.discovery_searches.find(search => search.id === requestedSearchId)
    || data.discovery_searches[0]
    || null;
  const [editingSearch, setEditingSearch] = useState(!selectedSearch);
  const [editingSearchId, setEditingSearchId] = useState(selectedSearch?.id || "");
  const [searchDraft, setSearchDraft] = useState<DiscoverySearchUpdates>(
    selectedSearch ? searchUpdates(selectedSearch) : newSearchDraft()
  );
  const [captureOpen, setCaptureOpen] = useState(false);
  const [captureText, setCaptureText] = useState("");
  const [captureDetails, setCaptureDetails] = useState<DiscoveryCandidateDetails>(EMPTY_DETAILS);
  const [resultSearch, setResultSearch] = useState("");
  const [resultFilter, setResultFilter] = useState<DiscoveryFilter>("new");
  const [operationStatus, setOperationStatus] = useState("");
  const [pending, setPending] = useState(false);
  const [editingCandidate, setEditingCandidate] = useState<DiscoveryCandidate | null>(null);
  const [ingestedPostingId, setIngestedPostingId] = useState("");

  useEffect(() => {
    if (selectedSearch && !editingSearch) setSearchDraft(searchUpdates(selectedSearch));
  }, [editingSearch, selectedSearch]);

  const selectedCandidates = data.discovery_candidates;
  const latestSeenAt = useMemo(
    () => selectedCandidates.reduce(
      (latest, candidate) => candidate.last_seen_at > latest ? candidate.last_seen_at : latest,
      ""
    ),
    [selectedCandidates]
  );

  const visibleCandidates = useMemo(
    () => selectedCandidates
      .filter(candidate => discoveryCandidateMatches(candidate, resultFilter, latestSeenAt))
      .filter(candidate => discoveryCandidateIncludes(candidate, resultSearch))
      .sort((left, right) => Number(right.processing_status === "ready") - Number(left.processing_status === "ready")
        || Number(right.fit_score || 0) - Number(left.fit_score || 0)
        || (right.last_seen_at || "").localeCompare(left.last_seen_at || "")),
    [latestSeenAt, resultFilter, resultSearch, selectedCandidates]
  );

  const counts = useMemo(
    () => Object.fromEntries(
      DISCOVERY_FILTERS.map(filter => [
        filter.id,
        selectedCandidates.filter(candidate => discoveryCandidateMatches(candidate, filter.id, latestSeenAt)).length
      ])
    ) as Record<DiscoveryFilter, number>,
    [latestSeenAt, selectedCandidates]
  );

  const capturedUrlCount = (captureText.match(/https?:\/\//gi) || []).length;

  function chooseSearch(searchId: string) {
    const params = new URLSearchParams(searchParams);
    if (searchId) params.set("search_id", searchId);
    else params.delete("search_id");
    setSearchParams(params);
    setEditingSearchId(searchId);
    setEditingSearch(false);
    setCaptureOpen(false);
  }

  function startNewSearch() {
    setSearchDraft(newSearchDraft());
    setEditingSearchId("");
    setEditingSearch(true);
  }

  function editSelectedSearch() {
    if (!selectedSearch) return;
    setSearchDraft(searchUpdates(selectedSearch));
    setEditingSearchId(selectedSearch.id);
    setEditingSearch(true);
  }

  async function saveSearch(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setOperationStatus("Saving Discovery search...");
    try {
      const result = await upsertDiscoverySearch(
        editingSearchId,
        searchDraft
      );
      await refresh();
      const params = new URLSearchParams(searchParams);
      params.set("search_id", result.search.id);
      setSearchParams(params);
      setEditingSearchId(result.search.id);
      setEditingSearch(false);
      setOperationStatus(`Saved ${result.search.name}.`);
    } catch (error) {
      setOperationStatus(`Could not save search. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function runSearch() {
    if (!selectedSearch) return;
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus("Searching Google and LinkedIn with Hunter Chrome...");
    try {
      const result = await runDiscoverySearch(selectedSearch.id);
      await refresh();
      setResultFilter("latest");
      const errorSuffix = result.errors.length
        ? ` ${result.errors.length} source search${result.errors.length === 1 ? "" : "es"} could not be completed.`
        : "";
      const limitSuffix = result.limited_count
        ? ` Hunter retained the top ${result.found_count} and held back ${result.limited_count} lower-ranked matches.`
        : "";
      setOperationStatus(
        `Discovery reviewed ${result.evaluated_count} unique links with adaptive paging. `
        + `${result.qualified_count} qualified after validation and lane matching; `
        + `${result.new_count} are new and ${result.updated_count} were refreshed. `
        + `${result.enriched_count} posting${result.enriched_count === 1 ? "" : "s"} gained verified details. `
        + `${result.duplicate_count} duplicate${result.duplicate_count === 1 ? "" : "s"} collapsed.`
        + `${limitSuffix}${errorSuffix}`
      );
    } catch (error) {
      setOperationStatus(`Discovery search failed. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function captureRoles(event: FormEvent) {
    event.preventDefault();
    if (!selectedSearch) return;
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus("Processing found roles...");
    try {
      const result = await captureDiscoveryCandidates(
        selectedSearch.id,
        captureText,
        capturedUrlCount === 1 ? captureDetails : {}
      );
      await refresh();
      setCaptureText("");
      setCaptureDetails(EMPTY_DETAILS);
      setCaptureOpen(false);
      const needsDetails = result.captured.filter(candidate => candidate.processing_status === "needs-details").length;
      setOperationStatus(
        `Processed ${result.count} role${result.count === 1 ? "" : "s"}.${needsDetails ? ` ${needsDetails} need copied details.` : ""}`
      );
    } catch (error) {
      setOperationStatus(`Could not process roles. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function saveCandidateDetails(updates: DiscoveryCandidateDetails) {
    if (!editingCandidate) return;
    setPending(true);
    setOperationStatus("Updating role details and fit...");
    try {
      await updateDiscoveryCandidateDetails(editingCandidate.id, updates);
      await refresh();
      setEditingCandidate(null);
      setOperationStatus("Role details updated and fit rescored.");
    } catch (error) {
      setOperationStatus(`Could not update role. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function setCandidateStatus(candidate: DiscoveryCandidate, status: "new" | "ignored") {
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus(status === "ignored" ? "Ignoring Discovery result..." : "Returning result to New...");
    try {
      await updateDiscoveryCandidate(candidate.id, status);
      await refresh();
      setOperationStatus(status === "ignored" ? "Discovery result ignored." : "Discovery result returned to New.");
    } catch (error) {
      setOperationStatus(`Could not update result. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function ingestCandidate(candidate: DiscoveryCandidate) {
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus("Ingesting Discovery result...");
    try {
      const result = await ingestDiscoveryCandidate(candidate.id);
      await refresh();
      setIngestedPostingId(result.posting.id);
      setOperationStatus(result.created ? "Discovery result ingested as a posting." : "This role was already tracked.");
    } catch (error) {
      setOperationStatus(`Could not ingest result. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <div className="discovery-toolbar">
        <label className="filter discovery-search-select">
          Search
          <select
            value={selectedSearch?.id || ""}
            onChange={event => chooseSearch(event.target.value)}
            disabled={!data.discovery_searches.length}
          >
            {!data.discovery_searches.length ? <option value="">No saved searches</option> : null}
            {data.discovery_searches.map(search => <option key={search.id} value={search.id}>{search.name}</option>)}
          </select>
        </label>
        <button className="button" type="button" onClick={startNewSearch}><PlusIcon size={15} /> New search</button>
        <button className="button" type="button" disabled={!selectedSearch} onClick={editSelectedSearch}>Edit search</button>
        <button
          className="button primary"
          type="button"
          disabled={!selectedSearch || pending}
          title="Uses the signed-in Hunter Chrome profile to search Google and LinkedIn across every configured lane"
          onClick={() => void runSearch()}
        >
          <SearchIcon size={15} /> {pending ? "Searching…" : "Search now"}
        </button>
        <button className="button" type="button" disabled={!selectedSearch} onClick={() => setCaptureOpen(value => !value)}>
          <PlusIcon size={15} /> Add found roles
        </button>
        {selectedSearch ? (
          <span className="discovery-search-context">
            {selectedSearch.keywords} · {selectedSearch.lanes.map(lane => laneSummary(lane)).join(" + ")}
          </span>
        ) : null}
      </div>
      {selectedSearch ? (
        <div className="discovery-source-note">
          <strong>Hunter Chrome</strong>
          <span>Discovery runs weighted exact, senior, and adjacent-role searches, continues paging while each page yields useful new postings, and automatically enriches the strongest incomplete results before fit scoring. Watched-company career scans stay in Companies.</span>
        </div>
      ) : null}
      {selectedSearch?.last_run_at && Object.keys(selectedSearch.last_run_summary || {}).length ? (
        <div className="discovery-run-summary">
          <strong>Last run</strong>
          <span>
            Reviewed {selectedSearch.last_run_summary.evaluated_count || 0};
            {" "}{selectedSearch.last_run_summary.qualified_count || 0} qualified;
            {" "}{selectedSearch.last_run_summary.enriched_count || 0} enriched;
            {" "}{selectedSearch.last_run_summary.duplicate_count || 0} duplicates collapsed.
          </span>
        </div>
      ) : null}

      {editingSearch ? (
        <form className="discovery-editor management-form" onSubmit={saveSearch}>
          <div className="discovery-editor-heading form-field full">
            <strong>{editingSearchId ? "Edit Discovery search" : "New Discovery search"}</strong>
            <span>Hunter chooses the search sources, finds postings, and scores them against your Search Goals and resume.</span>
          </div>
          <label className="form-field">
            Name
            <input
              required
              value={searchDraft.name}
              onChange={event => setSearchDraft({ ...searchDraft, name: event.target.value })}
              placeholder="Developer platforms"
            />
          </label>
          <label className="form-field full">
            Role keywords
            <input
              required
              value={searchDraft.keywords}
              onChange={event => setSearchDraft({ ...searchDraft, keywords: event.target.value })}
              placeholder="technical program manager developer tools"
            />
          </label>
          <div className="form-field full discovery-lanes-editor">
            <div className="discovery-lanes-heading">
              <span>
                <strong>Search lanes</strong>
                <small>Define each location and the work modes it should include.</small>
              </span>
              <button
                className="button compact"
                type="button"
                onClick={() => setSearchDraft({
                  ...searchDraft,
                  lanes: [...searchDraft.lanes, newSearchLane(searchDraft.lanes.length)]
                })}
              >
                <PlusIcon size={14} /> Add lane
              </button>
            </div>
            <div className="discovery-lane-list">
              {searchDraft.lanes.map((lane, index) => (
                <div className="discovery-lane-row" key={lane.id}>
                  <label>
                    Lane name
                    <input
                      value={lane.label}
                      onChange={event => updateSearchLane(index, { label: event.target.value }, searchDraft, setSearchDraft)}
                      placeholder="Local market or nationwide remote"
                    />
                  </label>
                  <label>
                    Location
                    <input
                      required
                      value={lane.location}
                      onChange={event => updateSearchLane(index, { location: event.target.value }, searchDraft, setSearchDraft)}
                      placeholder="City, state, or country"
                    />
                  </label>
                  <fieldset>
                    <legend>Work modes</legend>
                    <div className="discovery-work-modes">
                      {WORK_MODE_OPTIONS.map(option => (
                        <label key={option.id}>
                          <input
                            type="checkbox"
                            checked={lane.work_modes.includes(option.id)}
                            disabled={lane.work_modes.length === 1 && lane.work_modes.includes(option.id)}
                            onChange={() => toggleLaneWorkMode(index, option.id, searchDraft, setSearchDraft)}
                          />
                          {option.label}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <button
                    className="icon-button discovery-remove-lane"
                    type="button"
                    disabled={searchDraft.lanes.length === 1}
                    onClick={() => setSearchDraft({
                      ...searchDraft,
                      lanes: searchDraft.lanes.filter((_, laneIndex) => laneIndex !== index)
                    })}
                    aria-label={`Remove ${lane.label || lane.location || `search lane ${index + 1}`}`}
                  >
                    <XIcon size={15} />
                  </button>
                </div>
              ))}
            </div>
          </div>
          <div className="detail-actions form-field full">
            <button className="button primary" type="submit" disabled={pending}><FilterIcon size={15} /> Save search</button>
            {selectedSearch ? <button className="button" type="button" onClick={() => setEditingSearch(false)}>Cancel</button> : null}
          </div>
        </form>
      ) : null}

      {captureOpen && selectedSearch ? (
        <form className="discovery-capture-panel" onSubmit={captureRoles}>
          <div className="discovery-capture-heading">
            <div>
              <strong>Add roles from your search</strong>
              <span>Optional: paste a role Hunter did not find. Direct employer pages are processed automatically.</span>
            </div>
            <button className="icon-button" type="button" onClick={() => setCaptureOpen(false)} aria-label="Close capture tray"><XIcon size={16} /></button>
          </div>
          <label className="form-field full">
            Job links
            <textarea
              required
              value={captureText}
              onChange={event => setCaptureText(event.target.value)}
              placeholder={"Paste one job link per line\nhttps://www.linkedin.com/jobs/view/...\nhttps://company.example/jobs/..."}
            />
          </label>
          {capturedUrlCount === 1 ? (
            <details className="discovery-copied-details">
              <summary>Add copied details for this role</summary>
              <div className="management-form">
                <label className="form-field">Company <input value={captureDetails.company || ""} onChange={event => setCaptureDetails({ ...captureDetails, company: event.target.value })} /></label>
                <label className="form-field">Role title <input value={captureDetails.title || ""} onChange={event => setCaptureDetails({ ...captureDetails, title: event.target.value })} /></label>
                <label className="form-field">Location <input value={captureDetails.location || ""} onChange={event => setCaptureDetails({ ...captureDetails, location: event.target.value })} /></label>
                <label className="form-field">Work mode <input value={captureDetails.work_mode || ""} onChange={event => setCaptureDetails({ ...captureDetails, work_mode: event.target.value })} placeholder="Remote, Hybrid, or On-site" /></label>
                <label className="form-field full">Employer posting URL <input type="url" value={captureDetails.canonical_url || ""} onChange={event => setCaptureDetails({ ...captureDetails, canonical_url: event.target.value })} /></label>
                <label className="form-field full">Posting description <textarea value={captureDetails.description_text || ""} onChange={event => setCaptureDetails({ ...captureDetails, description_text: event.target.value })} /></label>
              </div>
            </details>
          ) : null}
          <div className="detail-actions">
            <button className="button primary" type="submit" disabled={pending}><SearchIcon size={15} /> Process roles</button>
            <button className="button" type="button" onClick={() => setCaptureOpen(false)}>Cancel</button>
          </div>
        </form>
      ) : null}

      {operationStatus ? (
        <div className="table-operation-status" role="status">
          <div className="table-operation-status-content"><span>{operationStatus}</span></div>
          <div className="table-operation-actions">
            {ingestedPostingId ? <Link className="button compact" to={routes.postingDetail(ingestedPostingId)}><BriefcaseIcon size={15} /> View posting</Link> : null}
            {!pending ? <button className="icon-button table-operation-close" type="button" onClick={() => setOperationStatus("")} aria-label="Dismiss status message"><XIcon size={15} /></button> : null}
          </div>
        </div>
      ) : null}

      <div className="toolbar discovery-results-toolbar" aria-label="Discovery result filters">
        <label className="search">
          <span className="sr-only">Search Discovery results</span>
          <SearchIcon />
          <input value={resultSearch} onChange={event => setResultSearch(event.target.value)} type="search" placeholder="Search roles, companies, sources, and fit..." />
        </label>
        <button className="button" type="button" onClick={() => { setResultSearch(""); setResultFilter("new"); }}><FilterIcon size={15} /> Clear</button>
      </div>

      <div className="candidate-filter-bar aggregate" aria-label="Discovery result status filters">
        {DISCOVERY_FILTERS.map(filter => (
          <button
            className={resultFilter === filter.id ? "candidate-filter active" : "candidate-filter"}
            key={filter.id}
            type="button"
            onClick={() => setResultFilter(filter.id)}
          >
            {filter.label}<span>{counts[filter.id]}</span>
          </button>
        ))}
      </div>

      <div className="candidate-review-summary">
        <strong>{visibleCandidates.length}</strong>
        <span>shown from {selectedCandidates.length} roles in the Discovery inbox</span>
      </div>

      <div className="table-scroll">
        <table className="simple-table candidates-table discovery-table">
          <thead>
            <tr>
              <th>Candidate</th>
              <th>Company</th>
              <th>Fit</th>
              <th>Processing</th>
              <th>Last seen</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visibleCandidates.map(candidate => (
              <tr key={candidate.id}>
                <td className="role-cell candidate-title-cell">
                  <strong>{candidate.title || "Role details needed"}</strong>
                  <span className="cell-subtle">{candidateLocationLabel(candidate)}</span>
                </td>
                <td>
                  {candidate.company || "Company needed"}
                  <span className="cell-subtle">{titleCase(candidate.source_platform || "manual")}</span>
                </td>
                <td className="candidate-score-cell discovery-fit-cell">
                  {candidate.processing_status === "ready" ? (
                    <>
                      <span className={`pill ${fitClass(candidate.fit_score)}`}>{candidate.fit_score || "—"}</span>
                      <span className="cell-subtle">{candidate.fit_summary || "Fit calculated from verified details"}</span>
                    </>
                  ) : (
                    <>
                      <span className="pill fit-pending">Pending</span>
                      <span className="cell-subtle">Fit will be calculated after posting details are verified.</span>
                    </>
                  )}
                </td>
                <td>
                  <span className={`pill discovery-${candidate.processing_status}`}>{processingLabel(candidate)}</span>
                  {candidate.warnings ? <span className="cell-subtle">{candidate.warnings.split("\n")[0]}</span> : null}
                </td>
                <td>{candidate.last_seen_at || candidate.captured_at ? dateOnlyLabel(candidate.last_seen_at || candidate.captured_at) : "Unknown"}</td>
                <td>
                  <div className="table-actions">
                    <a className="button compact" href={candidate.canonical_url || candidate.url} target="_blank" rel="noreferrer"><ExternalIcon size={15} /> Open</a>
                    <button className="button compact" type="button" onClick={() => setEditingCandidate(candidate)}>Details</button>
                    {candidate.ingested_application_id ? (
                      <Link className="button compact" to={routes.postingDetail(candidate.ingested_application_id)}><BriefcaseIcon size={15} /> Posting</Link>
                    ) : (
                      <button className="button compact" type="button" disabled={pending || candidate.processing_status !== "ready"} onClick={() => ingestCandidate(candidate)}>Ingest</button>
                    )}
                    {candidate.status === "ignored"
                      ? <button className="button compact" type="button" disabled={pending} onClick={() => setCandidateStatus(candidate, "new")}>Mark New</button>
                      : <button className="button compact" type="button" disabled={pending || candidate.status === "ingested"} onClick={() => setCandidateStatus(candidate, "ignored")}>Ignore</button>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="empty-state" style={{ display: visibleCandidates.length ? "none" : "block" }}>
          {selectedSearch ? "No Discovery results match the current filters." : "Create a Discovery search to start capturing roles."}
        </div>
      </div>

      {editingCandidate ? (
        <CandidateDetailsModal
          candidate={editingCandidate}
          pending={pending}
          close={() => setEditingCandidate(null)}
          save={saveCandidateDetails}
        />
      ) : null}
    </>
  );
}

function CandidateDetailsModal({
  candidate,
  pending,
  close,
  save
}: {
  candidate: DiscoveryCandidate;
  pending: boolean;
  close: () => void;
  save: (updates: DiscoveryCandidateDetails) => Promise<void>;
}) {
  const [draft, setDraft] = useState<DiscoveryCandidateDetails>({
    company: candidate.company,
    title: candidate.title,
    canonical_url: candidate.canonical_url,
    location: candidate.location,
    work_mode: candidate.work_mode,
    description_text: candidate.description_text,
    notes: candidate.notes
  });

  return (
    <div className="modal-backdrop">
      <article className="modal discovery-details-modal" role="dialog" aria-modal="true" aria-labelledby="discovery-details-title">
        <div className="modal-header">
          <h2 id="discovery-details-title">{candidate.title || "Complete role details"}</h2>
          <button className="button compact" type="button" onClick={close}><XIcon size={18} /> Close</button>
        </div>
        <form className="management-form" onSubmit={event => { event.preventDefault(); void save(draft); }}>
          <label className="form-field">Company <input required value={draft.company || ""} onChange={event => setDraft({ ...draft, company: event.target.value })} /></label>
          <label className="form-field">Role title <input required value={draft.title || ""} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
          <label className="form-field">Location <input value={draft.location || ""} onChange={event => setDraft({ ...draft, location: event.target.value })} /></label>
          <label className="form-field">Work mode <input value={draft.work_mode || ""} onChange={event => setDraft({ ...draft, work_mode: event.target.value })} placeholder="Remote, Hybrid, or On-site" /></label>
          <label className="form-field full">Employer posting URL <input type="url" value={draft.canonical_url || ""} onChange={event => setDraft({ ...draft, canonical_url: event.target.value })} /></label>
          <label className="form-field full">Posting description <textarea className="discovery-description-input" value={draft.description_text || ""} onChange={event => setDraft({ ...draft, description_text: event.target.value })} /></label>
          <label className="form-field full">Notes <textarea value={draft.notes || ""} onChange={event => setDraft({ ...draft, notes: event.target.value })} /></label>
          <div className="detail-actions form-field full">
            <button className="button primary" type="submit" disabled={pending}><FilterIcon size={15} /> Save and rescore</button>
            <button className="button" type="button" onClick={close}>Cancel</button>
          </div>
        </form>
      </article>
    </div>
  );
}

function searchUpdates(search: DiscoverySearch): DiscoverySearchUpdates {
  return {
    name: search.name,
    keywords: search.keywords,
    lanes: search.lanes.map(lane => ({ ...lane, work_modes: [...lane.work_modes] }))
  };
}

function newSearchLane(index: number): DiscoverySearchLaneDefinition {
  return {
    id: `lane-${Date.now()}-${index}`,
    label: "",
    location: "",
    work_modes: ["on-site", "hybrid", "remote"]
  };
}

function newSearchDraft(): DiscoverySearchUpdates {
  return { name: "", keywords: "", lanes: [newSearchLane(0)] };
}

function updateSearchLane(
  index: number,
  updates: Partial<DiscoverySearchLaneDefinition>,
  draft: DiscoverySearchUpdates,
  setDraft: (next: DiscoverySearchUpdates) => void
) {
  setDraft({
    ...draft,
    lanes: draft.lanes.map((lane, laneIndex) => laneIndex === index ? { ...lane, ...updates } : lane)
  });
}

function toggleLaneWorkMode(
  index: number,
  workMode: DiscoverySearchLaneDefinition["work_modes"][number],
  draft: DiscoverySearchUpdates,
  setDraft: (next: DiscoverySearchUpdates) => void
) {
  const lane = draft.lanes[index];
  const workModes = lane.work_modes.includes(workMode)
    ? lane.work_modes.filter(value => value !== workMode)
    : [...lane.work_modes, workMode];
  updateSearchLane(index, { work_modes: workModes }, draft, setDraft);
}

function laneSummary(lane: DiscoverySearchLaneDefinition) {
  const label = lane.label || lane.location;
  const modes = lane.work_modes.map(workModeLabel).join(", ");
  return `${label} (${modes})`;
}

function workModeLabel(mode: DiscoverySearchLaneDefinition["work_modes"][number]) {
  return WORK_MODE_OPTIONS.find(option => option.id === mode)?.label || titleCase(mode);
}

function discoveryCandidateMatches(candidate: DiscoveryCandidate, filter: DiscoveryFilter, latestSeenAt: string) {
  if (filter === "latest") return Boolean(latestSeenAt) && candidate.last_seen_at === latestSeenAt;
  if (filter === "recommended") {
    return candidate.status === "new"
      && candidate.processing_status === "ready"
      && Number(candidate.fit_score || 0) >= 45;
  }
  if (filter === "needs-details") return candidate.status === "new" && candidate.processing_status !== "ready";
  if (filter === "all") return true;
  return candidate.status === filter;
}

function processingLabel(candidate: DiscoveryCandidate) {
  if (candidate.processing_status === "ready") return "Verified";
  if (candidate.processing_status === "partial") return "Provisional";
  return titleCase(candidate.processing_status);
}

function discoveryCandidateIncludes(candidate: DiscoveryCandidate, search: string) {
  const query = search.trim().toLowerCase();
  if (!query) return true;
  return [
    candidate.company,
    candidate.title,
    candidate.location,
    candidate.work_mode,
    candidate.source_platform,
    candidate.fit_summary,
    candidate.description_excerpt,
    candidate.processing_status,
    candidate.notes
  ].join(" ").toLowerCase().includes(query);
}

function candidateLocationLabel(candidate: DiscoveryCandidate) {
  const location = candidate.location || "Location unknown";
  return candidate.work_mode ? `${location} · ${candidate.work_mode}` : location;
}

function fitClass(score: string) {
  const value = Number(score || 0);
  if (value >= 70) return "fit-strong";
  if (value >= 45) return "fit-recommended";
  return "fit-low";
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}
