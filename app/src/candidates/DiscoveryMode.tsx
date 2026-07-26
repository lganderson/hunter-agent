import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  captureDiscoveryCandidates,
  continueDiscovery,
  ingestDiscoveryCandidate,
  markDiscoveryCandidateDuplicate,
  updateDiscoveryCandidate,
  updateDiscoveryCandidateDetails,
  upsertDiscoverySearch
} from "../core/api";
import { dateOnlyLabel, titleCase } from "../core/format";
import { routes } from "../core/routes";
import type {
  AppState,
  Application,
  Company,
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

type DiscoveryFilter = "latest" | "new" | "recommended" | "needs-details" | "all" | "ignored" | "ingested" | "duplicate" | "unavailable";

const DISCOVERY_FILTERS: Array<{ id: DiscoveryFilter; label: string }> = [
  { id: "latest", label: "Latest" },
  { id: "new", label: "New" },
  { id: "recommended", label: "Recommended" },
  { id: "needs-details", label: "Needs details" },
  { id: "all", label: "All" },
  { id: "ignored", label: "Ignored" },
  { id: "ingested", label: "Ingested" },
  { id: "duplicate", label: "Duplicates" },
  { id: "unavailable", label: "Closed" }
];

const WORK_MODE_OPTIONS: Array<{ id: DiscoverySearchLaneDefinition["work_modes"][number]; label: string }> = [
  { id: "on-site", label: "On-site" },
  { id: "hybrid", label: "Hybrid" },
  { id: "remote", label: "Remote" }
];
const EMPTY_DETAILS: DiscoveryCandidateDetails = {
  company_id: "",
  company_name: "",
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
  const [reviewCandidateId, setReviewCandidateId] = useState("");
  const [ingestedPostingId, setIngestedPostingId] = useState("");
  const companyById = useMemo(
    () => new Map(data.companies.map(company => [company.id, company])),
    [data.companies]
  );

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
      .filter(candidate => discoveryCandidateIncludes(candidate, companyById.get(candidate.company_id), resultSearch))
      .sort((left, right) => Number(right.processing_status === "ready") - Number(left.processing_status === "ready")
        || Number(right.fit_score || 0) - Number(left.fit_score || 0)
        || (right.last_seen_at || "").localeCompare(left.last_seen_at || "")),
    [companyById, latestSeenAt, resultFilter, resultSearch, selectedCandidates]
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
  const reviewQueue = useMemo(
    () => [...selectedCandidates
      .filter(candidate => candidate.status === "new" && candidate.freshness_status !== "closed")]
      .sort((left, right) => Number(right.processing_status === "ready") - Number(left.processing_status === "ready")
        || Number(right.fit_score || 0) - Number(left.fit_score || 0)
        || (right.last_seen_at || "").localeCompare(left.last_seen_at || "")),
    [selectedCandidates]
  );
  const reviewBatch = reviewQueue.slice(0, 10);
  const reviewCandidate = reviewBatch.find(candidate => candidate.id === reviewCandidateId) || null;

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

  function reviewPreferenceSuggestion(term: string) {
    if (!selectedSearch) return;
    const draft = searchUpdates(selectedSearch);
    setSearchDraft({
      ...draft,
      excluded_terms: draft.excluded_terms.includes(term)
        ? draft.excluded_terms
        : [...draft.excluded_terms, term]
    });
    setEditingSearchId(selectedSearch.id);
    setEditingSearch(true);
    setOperationStatus(`Review the suggested “${term}” exclusion below, then save if it matches your intent.`);
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
      const result = await continueDiscovery(selectedSearch.id);
      await refresh();
      setResultFilter("latest");
      const errorSuffix = result.errors.length
        ? ` ${result.errors.length} source search${result.errors.length === 1 ? "" : "es"} could not be completed.`
        : "";
      const limitSuffix = result.limited_count
        ? ` Hunter retained the top ${result.found_count} and held back ${result.limited_count} lower-ranked matches.`
        : "";
      const enrichmentSuffix = result.enrichment
        ? ` Hunter continued through ${result.enrichment.processed_count} queued role${result.enrichment.processed_count === 1 ? "" : "s"}; ${result.enrichment.ready_count} are ready for review and ${result.enrichment.remaining_count} still need work.`
        : "";
      setOperationStatus(
        `Discovery reviewed ${result.evaluated_count} unique links with adaptive paging. `
        + `${result.qualified_count} qualified after validation and lane matching; `
        + `${result.new_count} are new and ${result.updated_count} were refreshed. `
        + `${result.enriched_count} posting${result.enriched_count === 1 ? "" : "s"} gained verified details. `
        + `Hunter researched ${result.company_researched_count} compan${result.company_researched_count === 1 ? "y" : "ies"}`
        + `${result.company_suggestion_count ? ` and queued ${result.company_suggestion_count} company information suggestion${result.company_suggestion_count === 1 ? "" : "s"}` : ""}. `
        + `${result.duplicate_count} duplicate${result.duplicate_count === 1 ? "" : "s"} collapsed.`
        + `${enrichmentSuffix}${limitSuffix}${errorSuffix}`
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
      return true;
    } catch (error) {
      setOperationStatus(`Could not update result. ${errorMessage(error)}`);
      return false;
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
      return true;
    } catch (error) {
      setOperationStatus(`Could not ingest result. ${errorMessage(error)}`);
      return false;
    } finally {
      setPending(false);
    }
  }

  async function markCandidateDuplicate(candidate: DiscoveryCandidate, applicationId: string) {
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus("Associating duplicate with the existing posting...");
    try {
      const result = await markDiscoveryCandidateDuplicate(candidate.id, applicationId);
      await refresh();
      setIngestedPostingId(result.posting.id);
      setOperationStatus(`Marked as a duplicate of ${result.posting.company} · ${result.posting.role}.`);
      return true;
    } catch (error) {
      setOperationStatus(`Could not mark duplicate. ${errorMessage(error)}`);
      return false;
    } finally {
      setPending(false);
    }
  }

  function nextReviewCandidateId(candidate: DiscoveryCandidate) {
    const index = reviewBatch.findIndex(row => row.id === candidate.id);
    return reviewBatch[index + 1]?.id || reviewBatch[index - 1]?.id || "";
  }

  async function reviewCandidateStatus(candidate: DiscoveryCandidate, status: "ignored" | "ingested") {
    const nextId = nextReviewCandidateId(candidate);
    const succeeded = status === "ignored"
      ? await setCandidateStatus(candidate, "ignored")
      : await ingestCandidate(candidate);
    if (succeeded) setReviewCandidateId(nextId);
  }

  async function reviewCandidateDuplicate(candidate: DiscoveryCandidate, applicationId: string) {
    const nextId = nextReviewCandidateId(candidate);
    const succeeded = await markCandidateDuplicate(candidate, applicationId);
    if (succeeded) setReviewCandidateId(nextId);
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
          <SearchIcon size={15} /> {pending ? "Hunter is working…" : "Continue discovery"}
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
          <span>Discovery runs weighted exact, senior, and adjacent-role searches, continues paging while each page yields useful new postings, and automatically enriches posting details, company industry, and employee range. Watched-company career scans stay in Companies.</span>
        </div>
      ) : null}
      {selectedSearch?.last_run_at && Object.keys(selectedSearch.last_run_summary || {}).length ? (
        <details className="discovery-run-summary">
          <summary>
            <strong>Last run</strong>
            <span>
              Reviewed {selectedSearch.last_run_summary.evaluated_count || 0};
              {" "}{selectedSearch.last_run_summary.qualified_count || 0} qualified;
              {" "}{selectedSearch.last_run_summary.enriched_count || 0} enriched;
              {" "}{selectedSearch.last_run_summary.company_researched_count || 0} companies researched;
              {" "}{selectedSearch.last_run_summary.duplicate_count || 0} duplicates collapsed.
            </span>
          </summary>
          <RunDiagnostics search={selectedSearch} />
        </details>
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
          <label className="form-field full">
            Exclude titles or keywords
            <input
              value={searchDraft.excluded_terms.join(", ")}
              onChange={event => setSearchDraft({
                ...searchDraft,
                excluded_terms: event.target.value.split(",").map(value => value.trim()).filter(Boolean)
              })}
              placeholder="sales, implementation, scrum"
            />
            <small>Optional comma-separated terms. Hunter applies them across every lane in this search.</small>
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

      {selectedSearch && (data.discovery_preference_suggestions || []).length ? (
        <section className="discovery-preference-suggestions" aria-label="Suggested Discovery preferences">
          <div>
            <span className="eyebrow">Learn from your decisions</span>
            <strong>Hunter found repeated patterns in ignored roles</strong>
            <p>Nothing changes automatically. Review a suggestion in the search definition before saving it.</p>
          </div>
          <div>
            {(data.discovery_preference_suggestions || []).map(suggestion => (
              <button className="button compact" type="button" key={suggestion.id} onClick={() => reviewPreferenceSuggestion(suggestion.term)}>
                Exclude “{suggestion.term}” · {suggestion.ignored_count} ignored
              </button>
            ))}
          </div>
        </section>
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
                <label className="form-field">Company <input value={captureDetails.company_name || ""} onChange={event => setCaptureDetails({ ...captureDetails, company_name: event.target.value })} /></label>
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

      {reviewQueue.length ? (
        <section className="discovery-review-callout" aria-label="Discovery review queue">
          <div>
            <span className="eyebrow">Ready for your decision</span>
            <strong>{reviewBatch.length} of {reviewQueue.length} roles in this batch</strong>
            <p>Hunter ordered complete roles by fit and freshness. Review a bounded batch instead of scanning the entire inbox.</p>
          </div>
          <button className="button primary" type="button" onClick={() => setReviewCandidateId(reviewBatch[0].id)}>
            Review next
          </button>
        </section>
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
              <th>Industry</th>
              <th>Size</th>
              <th>Fit</th>
              <th>Processing</th>
              <th>Last seen</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visibleCandidates.map(candidate => {
              const company = companyById.get(candidate.company_id);
              return (
              <tr key={candidate.id}>
                <td className="role-cell candidate-title-cell">
                  <strong>{candidate.title || "Role details needed"}</strong>
                  <span className="cell-subtle">{candidateLocationLabel(candidate)}</span>
                </td>
                <td>
                  {company
                    ? <Link to={routes.companyDetail(company.id)}>{company.name}</Link>
                    : "Company needed"}
                  <span className="cell-subtle">{titleCase(candidate.source_platform || "manual")}</span>
                </td>
                <td className="discovery-metadata-cell" title={company?.industry || undefined}>
                  {company?.industry || "—"}
                </td>
                <td className="discovery-metadata-cell" title={company?.company_size || undefined}>
                  {company?.company_size || "—"}
                </td>
                <td
                  className="candidate-score-cell discovery-fit-cell"
                  title={candidate.fit_summary || undefined}
                  aria-label={candidate.processing_status === "ready"
                    ? `Fit ${candidate.fit_score || "not scored"}. ${candidate.fit_summary || ""}`.trim()
                    : "Fit pending verified posting details."}
                >
                  {candidate.processing_status === "ready" ? (
                    <span className={`pill ${fitClass(candidate.fit_score)}`}>{candidate.fit_score || "—"}</span>
                  ) : (
                    <span className="pill fit-pending">Pending</span>
                  )}
                </td>
                <td>
                  <span className={`pill discovery-${candidate.processing_status}`}>{processingLabel(candidate)}</span>
                  <span className="cell-subtle">{freshnessLabel(candidate)}</span>
                </td>
                <td>{candidate.last_seen_at || candidate.captured_at ? dateOnlyLabel(candidate.last_seen_at || candidate.captured_at) : "Unknown"}</td>
                <td>
                  <div className="table-actions">
                    <a className="button compact" href={candidate.canonical_url || candidate.url} target="_blank" rel="noreferrer"><ExternalIcon size={15} /> Open</a>
                    {candidate.status === "new" ? (
                      <button className="button compact primary" type="button" onClick={() => setReviewCandidateId(candidate.id)}>Review</button>
                    ) : null}
                    {candidate.ingested_application_id ? (
                      <Link className="button compact" to={routes.postingDetail(candidate.ingested_application_id)}><BriefcaseIcon size={15} /> Posting</Link>
                    ) : null}
                    {candidate.status === "ignored" || candidate.status === "duplicate"
                      ? <button className="button compact" type="button" disabled={pending} onClick={() => setCandidateStatus(candidate, "new")}>Mark New</button>
                      : <button className="button compact" type="button" onClick={() => setEditingCandidate(candidate)}>Details</button>}
                  </div>
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
        <div className="empty-state" style={{ display: visibleCandidates.length ? "none" : "block" }}>
          {selectedSearch ? "No Discovery results match the current filters." : "Create a Discovery search to start capturing roles."}
        </div>
      </div>

      {editingCandidate ? (
        <CandidateDetailsModal
          candidate={editingCandidate}
          companies={data.companies}
          pending={pending}
          close={() => setEditingCandidate(null)}
          save={saveCandidateDetails}
        />
      ) : null}
      {reviewCandidate ? (
        <CandidateReviewModal
          key={reviewCandidate.id}
          candidate={reviewCandidate}
          company={companyById.get(reviewCandidate.company_id)}
          applications={data.applications}
          index={reviewBatch.findIndex(candidate => candidate.id === reviewCandidate.id)}
          total={reviewBatch.length}
          pending={pending}
          close={() => setReviewCandidateId("")}
          previous={() => {
            const index = reviewBatch.findIndex(candidate => candidate.id === reviewCandidate.id);
            setReviewCandidateId(reviewBatch[index - 1]?.id || reviewCandidate.id);
          }}
          next={() => setReviewCandidateId(nextReviewCandidateId(reviewCandidate))}
          edit={() => {
            setReviewCandidateId("");
            setEditingCandidate(reviewCandidate);
          }}
          ignore={() => void reviewCandidateStatus(reviewCandidate, "ignored")}
          markDuplicate={applicationId => void reviewCandidateDuplicate(reviewCandidate, applicationId)}
          ingest={() => void reviewCandidateStatus(reviewCandidate, "ingested")}
        />
      ) : null}
    </>
  );
}

function RunDiagnostics({ search }: { search: DiscoverySearch }) {
  const summary = search.last_run_summary;
  const sources = summary.sources || [];
  const enrichment = summary.enrichment;
  return (
    <div className="discovery-run-diagnostics">
      {enrichment ? (
        <div className="discovery-diagnostic-metrics">
          <span><strong>{enrichment.processed_count}</strong> queue processed</span>
          <span><strong>{enrichment.posting_checked_count}</strong> postings checked</span>
          <span><strong>{enrichment.ready_count}</strong> ready</span>
          <span><strong>{enrichment.remaining_count}</strong> remaining</span>
        </div>
      ) : null}
      {sources.length ? (
        <div className="discovery-source-runs">
          {sources.map((source, index) => (
            <div key={`${source.source}-${source.query_family}-${source.lane_id}-${index}`}>
              <strong>{source.label}</strong>
              <span>{source.lane_label} · {source.query_family_label}</span>
              <span>{source.found_count} found across {source.page_count} page{source.page_count === 1 ? "" : "s"}</span>
            </div>
          ))}
        </div>
      ) : <p>No per-source diagnostics were retained for this run.</p>}
      {(summary.errors || []).length ? (
        <ul className="discovery-run-errors">
          {(summary.errors || []).map((error, index) => <li key={`${error}-${index}`}>{error}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

function FitBrief({ candidate, company }: { candidate: DiscoveryCandidate; company?: Company }) {
  return (
    <section className="discovery-fit-brief" aria-label="Fit brief">
      <div className="discovery-fit-headline">
        <span className={`pill ${fitClass(candidate.fit_score)}`}>{candidate.fit_score || "Pending"}</span>
        <div>
          <strong>{candidate.fit_summary || "Hunter needs more posting details before scoring fit."}</strong>
          <span>
            {candidate.lane_match || "Search-lane match needs confirmation"} · {candidate.source_confidence || "Low"} source confidence
          </span>
        </div>
      </div>
      <div className="discovery-fit-columns">
        <div>
          <span className="eyebrow">Why it fits</span>
          {(candidate.fit_strengths || []).length
            ? <ul>{(candidate.fit_strengths || []).map(item => <li key={item}>{item}</li>)}</ul>
            : <p>No supported fit strengths yet.</p>}
        </div>
        <div>
          <span className="eyebrow">Check before deciding</span>
          {(candidate.fit_gaps || []).length
            ? <ul>{(candidate.fit_gaps || []).map(item => <li key={item}>{item}</li>)}</ul>
            : <p>No material gaps identified from the available posting.</p>}
        </div>
      </div>
      {company ? <span className="discovery-fit-company">{company.industry || "Industry unknown"} · {company.company_size || "Size unknown"}</span> : null}
    </section>
  );
}

function CandidateReviewModal({
  candidate,
  company,
  applications,
  index,
  total,
  pending,
  close,
  previous,
  next,
  edit,
  ignore,
  markDuplicate,
  ingest
}: {
  candidate: DiscoveryCandidate;
  company?: Company;
  applications: Application[];
  index: number;
  total: number;
  pending: boolean;
  close: () => void;
  previous: () => void;
  next: () => void;
  edit: () => void;
  ignore: () => void;
  markDuplicate: (applicationId: string) => void;
  ingest: () => void;
}) {
  const [duplicateOpen, setDuplicateOpen] = useState(false);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && duplicateOpen) {
        setDuplicateOpen(false);
        return;
      }
      if (event.key === "Escape") close();
      if (duplicateOpen) return;
      if (
        event.target instanceof HTMLInputElement
        || event.target instanceof HTMLTextAreaElement
        || event.target instanceof HTMLSelectElement
      ) return;
      if (event.key === "ArrowLeft") previous();
      if (event.key === "ArrowRight") next();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [close, duplicateOpen, next, previous]);

  return (
    <div className="modal-backdrop">
      <article className="modal discovery-review-modal" role="dialog" aria-modal="true" aria-labelledby="discovery-review-title">
        <div className="modal-header">
          <div>
            <span className="eyebrow">Review {index + 1} of {total}</span>
            <h2 id="discovery-review-title">{candidate.title || "Role details needed"}</h2>
            <p>{company?.name || "Company needed"} · {candidateLocationLabel(candidate)}</p>
          </div>
          <button className="button compact" type="button" onClick={close}><XIcon size={18} /> Close</button>
        </div>
        <FitBrief candidate={candidate} company={company} />
        <div className="discovery-review-evidence">
          <div>
            <span className="eyebrow">Posting evidence</span>
            <strong>{freshnessLabel(candidate)}</strong>
            <p>{candidate.description_excerpt || "Hunter has not captured a usable posting excerpt yet."}</p>
          </div>
          {(candidate.source_urls || []).length > 1 ? (
            <details>
              <summary>{candidate.source_urls.length} source links</summary>
              <ul>{(candidate.source_urls || []).map(url => <li key={url}><a href={url} target="_blank" rel="noreferrer">{sourceLabel(url)}</a></li>)}</ul>
            </details>
          ) : null}
        </div>
        {duplicateOpen ? (
          <DuplicatePostingPicker
            candidate={candidate}
            company={company}
            applications={applications}
            pending={pending}
            cancel={() => setDuplicateOpen(false)}
            confirm={markDuplicate}
          />
        ) : null}
        <div className="discovery-review-actions">
          <div>
            <button className="button" type="button" disabled={index <= 0 || pending} onClick={previous}>Previous</button>
            <button className="button" type="button" disabled={total <= 1 || pending} onClick={next}>Next</button>
          </div>
          <div>
            <a className="button" href={candidate.canonical_url || candidate.url} target="_blank" rel="noreferrer"><ExternalIcon size={15} /> Open posting</a>
            <button className="button" type="button" disabled={pending} onClick={edit}>Edit details</button>
            <button className="button" type="button" disabled={pending} onClick={ignore}>Ignore</button>
            <button className="button" type="button" disabled={pending || !applications.length} onClick={() => setDuplicateOpen(true)}>
              Mark duplicate
            </button>
            <button className="button primary" type="button" disabled={pending || candidate.processing_status !== "ready"} onClick={ingest}>Ingest</button>
          </div>
        </div>
      </article>
    </div>
  );
}

function DuplicatePostingPicker({
  candidate,
  company,
  applications,
  pending,
  cancel,
  confirm
}: {
  candidate: DiscoveryCandidate;
  company?: Company;
  applications: Application[];
  pending: boolean;
  cancel: () => void;
  confirm: (applicationId: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [selectedApplicationId, setSelectedApplicationId] = useState("");
  const matches = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return [...applications]
      .filter(application => {
        if (!terms.length) return true;
        const searchable = postingSearchText(application);
        return terms.every(term => searchable.includes(term));
      })
      .sort((left, right) => (
        duplicatePostingScore(right, candidate, company) - duplicatePostingScore(left, candidate, company)
        || Number(right.is_active) - Number(left.is_active)
        || (right.date_found || "").localeCompare(left.date_found || "")
        || right.id.localeCompare(left.id)
      ))
      .slice(0, 20);
  }, [applications, candidate, company, query]);

  return (
    <section className="discovery-duplicate-picker" aria-label="Associate duplicate with an existing posting">
      <div className="discovery-duplicate-heading">
        <div>
          <strong>Choose the posting this duplicates</strong>
          <span>Hunter will keep the Discovery result linked to the posting you select.</span>
        </div>
        <button className="icon-button" type="button" onClick={cancel} aria-label="Cancel duplicate association"><XIcon size={15} /></button>
      </div>
      <label className="search discovery-duplicate-search">
        <span className="sr-only">Search existing postings</span>
        <SearchIcon />
        <input
          autoFocus
          value={query}
          onChange={event => setQuery(event.target.value)}
          type="search"
          placeholder="Search by company, role, location, or posting ID..."
        />
      </label>
      <div className="discovery-duplicate-results" role="list" aria-label="Existing postings">
        {matches.map(application => (
          <button
            className={selectedApplicationId === application.id ? "discovery-duplicate-option selected" : "discovery-duplicate-option"}
            key={application.id}
            type="button"
            onClick={() => setSelectedApplicationId(application.id)}
            aria-pressed={selectedApplicationId === application.id}
          >
            <span>
              <strong>{application.role}</strong>
              <small>{application.company} · {application.location || "Location unknown"} · {postingStageLabel(application)}</small>
            </span>
            <span>{application.id}</span>
          </button>
        ))}
        {!matches.length ? <p>No existing postings match that search.</p> : null}
      </div>
      <div className="discovery-duplicate-actions">
        <button className="button" type="button" disabled={pending} onClick={cancel}>Cancel</button>
        <button
          className="button primary"
          type="button"
          disabled={pending || !selectedApplicationId}
          onClick={() => confirm(selectedApplicationId)}
        >
          <BriefcaseIcon size={15} /> Associate duplicate
        </button>
      </div>
    </section>
  );
}

function postingSearchText(application: Application) {
  return [
    application.id,
    application.company,
    application.role,
    application.location,
    application.work_mode,
    application.stage,
    application.outcome,
    application.source_url
  ].join(" ").toLowerCase();
}

function duplicatePostingScore(
  application: Application,
  candidate: DiscoveryCandidate,
  company?: Company
) {
  let score = 0;
  const candidateUrls = new Set(
    [candidate.url, candidate.canonical_url, ...(candidate.source_urls || [])]
      .map(normalizedPostingUrl)
      .filter(Boolean)
  );
  if (candidateUrls.has(normalizedPostingUrl(application.source_url))) score += 1000;
  if (candidate.company_id && application.company_id === candidate.company_id) score += 300;
  if (company?.name && application.company.toLowerCase() === company.name.toLowerCase()) score += 200;

  const candidateTitle = candidate.title.trim().toLowerCase();
  const applicationTitle = application.role.trim().toLowerCase();
  if (candidateTitle && candidateTitle === applicationTitle) score += 400;
  const candidateTerms = new Set(candidateTitle.match(/[a-z0-9]+/g) || []);
  const applicationTerms = new Set(applicationTitle.match(/[a-z0-9]+/g) || []);
  score += [...candidateTerms].filter(term => applicationTerms.has(term)).length * 12;
  return score;
}

function normalizedPostingUrl(value: string) {
  return value.trim().toLowerCase().replace(/\/+$/, "");
}

function postingStageLabel(application: Application) {
  if (application.stage === "closed" && application.outcome) {
    return `${titleCase(application.stage)} · ${titleCase(application.outcome)}`;
  }
  return titleCase(application.stage || "tracked");
}

function CandidateDetailsModal({
  candidate,
  companies,
  pending,
  close,
  save
}: {
  candidate: DiscoveryCandidate;
  companies: Company[];
  pending: boolean;
  close: () => void;
  save: (updates: DiscoveryCandidateDetails) => Promise<void>;
}) {
  const [draft, setDraft] = useState<DiscoveryCandidateDetails>({
    company_id: candidate.company_id,
    title: candidate.title,
    canonical_url: candidate.canonical_url,
    location: candidate.location,
    work_mode: candidate.work_mode,
    description_text: candidate.description_text,
    notes: candidate.notes
  });
  const linkedCompany = companies.find(company => company.id === draft.company_id);

  return (
    <div className="modal-backdrop">
      <article className="modal discovery-details-modal" role="dialog" aria-modal="true" aria-labelledby="discovery-details-title">
        <div className="modal-header">
          <h2 id="discovery-details-title">{candidate.title || "Complete role details"}</h2>
          <button className="button compact" type="button" onClick={close}><XIcon size={18} /> Close</button>
        </div>
        <FitBrief candidate={candidate} company={linkedCompany} />
        <form className="management-form" onSubmit={event => { event.preventDefault(); void save(draft); }}>
          <label className="form-field">
            Company
            <select required value={draft.company_id || ""} onChange={event => setDraft({ ...draft, company_id: event.target.value })}>
              <option value="">Select a company</option>
              {companies.map(company => <option key={company.id} value={company.id}>{company.name}</option>)}
            </select>
          </label>
          <label className="form-field">Role title <input required value={draft.title || ""} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
          <label className="form-field">Location <input value={draft.location || ""} onChange={event => setDraft({ ...draft, location: event.target.value })} /></label>
          <label className="form-field">Work mode <input value={draft.work_mode || ""} onChange={event => setDraft({ ...draft, work_mode: event.target.value })} placeholder="Remote, Hybrid, or On-site" /></label>
          {linkedCompany ? (
            <div className="form-field full discovery-company-source">
              <span>{[linkedCompany.industry, linkedCompany.company_size].filter(Boolean).join(" · ") || "Company details have not been researched yet."}</span>
              <Link to={routes.companyDetail(linkedCompany.id)}>View or research company details</Link>
            </div>
          ) : null}
          <label className="form-field full">Employer posting URL <input type="url" value={draft.canonical_url || ""} onChange={event => setDraft({ ...draft, canonical_url: event.target.value })} /></label>
          <details className="form-field full discovery-description-editor">
            <summary>Edit raw posting description</summary>
            <textarea className="discovery-description-input" value={draft.description_text || ""} onChange={event => setDraft({ ...draft, description_text: event.target.value })} />
          </details>
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
    lanes: search.lanes.map(lane => ({ ...lane, work_modes: [...lane.work_modes] })),
    excluded_terms: [...(search.excluded_terms || [])]
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
  return { name: "", keywords: "", lanes: [newSearchLane(0)], excluded_terms: [] };
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

function discoveryCandidateIncludes(candidate: DiscoveryCandidate, company: Company | undefined, search: string) {
  const query = search.trim().toLowerCase();
  if (!query) return true;
  return [
    company?.name,
    candidate.title,
    candidate.location,
    candidate.work_mode,
    company?.industry,
    company?.company_size,
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

function freshnessLabel(candidate: DiscoveryCandidate) {
  if (candidate.freshness_status === "confirmed-open") {
    return candidate.freshness_checked_at
      ? `Confirmed open ${dateOnlyLabel(candidate.freshness_checked_at)}`
      : "Confirmed open";
  }
  if (candidate.freshness_status === "closed") return "Closed or no longer accepting applications";
  if (candidate.freshness_status === "needs-review") return "Freshness needs review";
  return "Freshness not checked";
}

function sourceLabel(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
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
