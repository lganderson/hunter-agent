import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  applyDiscoverySearchExclusions,
  captureDiscoveryCandidates,
  continueDiscovery,
  dismissSuggestion,
  ingestDiscoveryCandidate,
  markDiscoveryCandidateDuplicate,
  updateDiscoveryCandidate,
  updateDiscoveryCandidateDetails,
  upsertCompany,
  upsertDiscoverySearch,
  undoDiscoverySearchExclusions
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

type DiscoveryFilter = "new" | "recommended" | "needs-details" | "all" | "ignored" | "ingested" | "duplicate" | "unavailable";

const DISCOVERY_FILTERS: Array<{ id: DiscoveryFilter; label: string }> = [
  { id: "new", label: "New" },
  { id: "recommended", label: "Recommended" },
  { id: "needs-details", label: "Needs details" },
  { id: "all", label: "All" },
  { id: "ignored", label: "Ignored" },
  { id: "ingested", label: "Ingested" },
  { id: "duplicate", label: "Duplicates" },
  { id: "unavailable", label: "Closed" }
];
const DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES = new Set(["not-interested", "archived"]);
const IGNORE_REASON_OPTIONS = [
  { id: "wrong-role", label: "Wrong role" },
  { id: "company", label: "Company" },
  { id: "level", label: "Wrong level" },
  { id: "industry", label: "Industry" },
  { id: "location", label: "Location" },
  { id: "stale", label: "Stale posting" },
  { id: "poor-source", label: "Poor source" },
  { id: "other", label: "Other" }
] as const;

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
  const [exclusionUndoIds, setExclusionUndoIds] = useState<string[]>([]);
  const [applyExistingExclusions, setApplyExistingExclusions] = useState(true);
  const [dismissingSuggestionId, setDismissingSuggestionId] = useState("");
  const companyById = useMemo(
    () => new Map(data.companies.map(company => [company.id, company])),
    [data.companies]
  );

  useEffect(() => {
    if (selectedSearch && !editingSearch) setSearchDraft(searchUpdates(selectedSearch));
  }, [editingSearch, selectedSearch]);

  const discoveryExcludedCompanyIds = useMemo(
    () => new Set(
      data.companies
        .filter(company => DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES.has(company.interest_status))
        .map(company => company.id)
    ),
    [data.companies]
  );
  const selectedCandidates = useMemo(
    () => data.discovery_candidates.filter(
      candidate => !candidate.company_id || !discoveryExcludedCompanyIds.has(candidate.company_id)
    ),
    [data.discovery_candidates, discoveryExcludedCompanyIds]
  );
  const preferenceSuggestions = useMemo(
    () => (data.discovery_preference_suggestions || []).filter(
      suggestion => suggestion.search_id === selectedSearch?.id
        && !(data.dismissed_suggestion_ids || []).includes(suggestion.id)
        && !selectedSearch.excluded_terms.some(
          term => term.toLowerCase() === suggestion.term.toLowerCase()
        )
    ),
    [data.discovery_preference_suggestions, data.dismissed_suggestion_ids, selectedSearch]
  );
  const visibleCandidates = useMemo(
    () => selectedCandidates
      .filter(candidate => discoveryCandidateMatches(candidate, resultFilter))
      .filter(candidate => discoveryCandidateIncludes(candidate, companyById.get(candidate.company_id), resultSearch))
      .sort(discoveryCandidateComparator),
    [companyById, resultFilter, resultSearch, selectedCandidates]
  );

  const counts = useMemo(
    () => Object.fromEntries(
      DISCOVERY_FILTERS.map(filter => [
        filter.id,
        selectedCandidates.filter(candidate => discoveryCandidateMatches(candidate, filter.id)).length
      ])
    ) as Record<DiscoveryFilter, number>,
    [selectedCandidates]
  );
  const reviewQueue = useMemo(
    () => [...selectedCandidates
      .filter(candidate => candidate.status === "new" && candidate.freshness_status !== "closed")]
      .sort(discoveryCandidateComparator),
    [selectedCandidates]
  );
  const reviewBatch = reviewQueue.slice(0, 10);
  const reviewCandidate = selectedCandidates.find(candidate => candidate.id === reviewCandidateId) || null;
  const activeReviewCandidates = reviewCandidate && !reviewBatch.some(candidate => candidate.id === reviewCandidate.id)
    ? [reviewCandidate]
    : reviewBatch;

  const capturedUrlCount = (captureText.match(/https?:\/\//gi) || []).length;
  const addedExclusionTerms = useMemo(() => {
    const current = new Set((selectedSearch?.excluded_terms || []).map(term => term.toLowerCase()));
    return searchDraft.excluded_terms.filter(term => !current.has(term.toLowerCase()));
  }, [searchDraft.excluded_terms, selectedSearch]);
  const exclusionImpact = useMemo(
    () => selectedCandidates.filter(candidate =>
      candidate.status === "new"
      && (!editingSearchId || candidate.search_id === editingSearchId)
      && candidateMatchesExclusionTerms(candidate, addedExclusionTerms)
    ),
    [addedExclusionTerms, editingSearchId, selectedCandidates]
  );

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
    setApplyExistingExclusions(true);
  }

  function editSelectedSearch() {
    if (!selectedSearch) return;
    setSearchDraft(searchUpdates(selectedSearch));
    setEditingSearchId(selectedSearch.id);
    setEditingSearch(true);
    setApplyExistingExclusions(true);
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

  async function dismissPreferenceSuggestion(suggestionId: string) {
    setDismissingSuggestionId(suggestionId);
    setOperationStatus("Dismissing suggestion...");
    try {
      await dismissSuggestion(suggestionId);
      await refresh();
      setOperationStatus("Suggestion dismissed. The search itself was not changed.");
    } catch (error) {
      setOperationStatus(`Could not dismiss suggestion. ${errorMessage(error)}`);
    } finally {
      setDismissingSuggestionId("");
    }
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
      let appliedCount = 0;
      let appliedIds: string[] = [];
      if (applyExistingExclusions && exclusionImpact.length) {
        const applied = await applyDiscoverySearchExclusions(
          result.search.id,
          result.search.excluded_terms
        );
        appliedCount = applied.count;
        appliedIds = applied.candidate_ids;
      }
      await refresh();
      const params = new URLSearchParams(searchParams);
      params.set("search_id", result.search.id);
      setSearchParams(params);
      setEditingSearchId(result.search.id);
      setEditingSearch(false);
      setExclusionUndoIds(appliedIds);
      setOperationStatus(
        appliedCount
          ? `Saved ${result.search.name} and hid ${appliedCount} current role${appliedCount === 1 ? "" : "s"} that match the new exclusions.`
          : `Saved ${result.search.name}.`
      );
    } catch (error) {
      setOperationStatus(`Could not save search. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function undoAppliedExclusions() {
    if (!exclusionUndoIds.length) return;
    setPending(true);
    setOperationStatus("Restoring roles hidden by the last search edit...");
    try {
      const result = await undoDiscoverySearchExclusions(exclusionUndoIds);
      await refresh();
      setExclusionUndoIds([]);
      setOperationStatus(`Restored ${result.count} role${result.count === 1 ? "" : "s"} to New. The search exclusions remain saved.`);
    } catch (error) {
      setOperationStatus(`Could not restore roles. ${errorMessage(error)}`);
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
      setResultFilter("new");
      const errorSuffix = result.errors.length
        ? ` ${result.errors.length} source search${result.errors.length === 1 ? "" : "es"} could not be completed.`
        : "";
      const limitSuffix = result.limited_count
        ? ` Hunter retained the top ${result.found_count} and held back ${result.limited_count} lower-ranked matches.`
        : "";
      const enrichmentSuffix = result.enrichment
        ? ` Hunter continued through ${result.enrichment.processed_count} queued role${result.enrichment.processed_count === 1 ? "" : "s"}; ${result.enrichment.ready_count} are ready for review, ${result.enrichment.remaining_count} roles still need work, and ${result.enrichment.company_research_remaining_count || 0} companies remain in the information queue.`
        : "";
      setOperationStatus(
        `Discovery reviewed ${result.evaluated_count} unique links with adaptive paging. `
        + `${result.qualified_count} qualified after validation and lane matching; `
        + `${result.screened_count} were kept out of the review queue; `
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
      const needsDetails = result.captured.filter(candidate => candidate.processing_status !== "ready").length;
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
      const result = await updateDiscoveryCandidateDetails(editingCandidate.id, updates);
      await refresh();
      if (result.candidate.processing_status === "ready") {
        setEditingCandidate(null);
        setOperationStatus("Role details verified and fit rescored.");
      } else {
        setEditingCandidate(result.candidate);
        setOperationStatus("Changes saved. Complete the highlighted requirements before Hunter can verify fit.");
      }
    } catch (error) {
      setOperationStatus(`Could not update role. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function setCandidateStatus(
    candidate: DiscoveryCandidate,
    status: "new" | "ignored",
    ignoreReason = ""
  ) {
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus(status === "ignored" ? "Ignoring Discovery result..." : "Returning result to New...");
    try {
      await updateDiscoveryCandidate(candidate.id, status, ignoreReason);
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
    const index = activeReviewCandidates.findIndex(row => row.id === candidate.id);
    return activeReviewCandidates[index + 1]?.id || activeReviewCandidates[index - 1]?.id || "";
  }

  function nextReviewCandidateIdOutsideCompany(candidate: DiscoveryCandidate) {
    const index = activeReviewCandidates.findIndex(row => row.id === candidate.id);
    const remainingCandidates = [
      ...activeReviewCandidates.slice(index + 1),
      ...activeReviewCandidates.slice(0, index).reverse()
    ];
    return remainingCandidates.find(row => row.company_id !== candidate.company_id)?.id || "";
  }

  async function reviewCandidateStatus(
    candidate: DiscoveryCandidate,
    status: "ignored" | "ingested",
    ignoreReason = ""
  ) {
    const nextId = nextReviewCandidateId(candidate);
    const succeeded = status === "ignored"
      ? await setCandidateStatus(candidate, "ignored", ignoreReason)
      : await ingestCandidate(candidate);
    if (succeeded) setReviewCandidateId(nextId);
  }

  async function markCandidateCompanyNotInterested(candidate: DiscoveryCandidate) {
    const company = companyById.get(candidate.company_id);
    if (!company) {
      setOperationStatus("Add or associate a company before marking it Not interested.");
      return;
    }
    const nextId = nextReviewCandidateIdOutsideCompany(candidate);
    setPending(true);
    setOperationStatus(`Marking ${company.name} Not interested...`);
    try {
      await upsertCompany(company.id, { interest_status: "not-interested" });
      await refresh();
      setReviewCandidateId(nextId);
      setOperationStatus(`${company.name} marked Not interested. Its existing and future roles are hidden from Discovery.`);
    } catch (error) {
      setOperationStatus(`Could not update ${company.name}. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
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
          {exclusionImpact.length ? (
            <section className="discovery-exclusion-preview form-field full" aria-label="Current role impact">
              <div>
                <strong>{exclusionImpact.length} current role{exclusionImpact.length === 1 ? "" : "s"} match the new exclusions</strong>
                <span>{exclusionImpact.slice(0, 3).map(candidate => candidate.title).join(" · ")}</span>
              </div>
              <label>
                <input
                  type="checkbox"
                  checked={applyExistingExclusions}
                  onChange={event => setApplyExistingExclusions(event.target.checked)}
                />
                Hide these roles from New when I save
              </label>
            </section>
          ) : null}
          <div className="detail-actions form-field full discovery-editor-actions">
            <button className="button primary" type="submit" disabled={pending}><FilterIcon size={15} /> Save search</button>
            {selectedSearch ? <button className="button" type="button" onClick={() => setEditingSearch(false)}>Cancel</button> : null}
          </div>
        </form>
      ) : null}

      {selectedSearch && preferenceSuggestions.length ? (
        <details className="discovery-learning" aria-label="Suggestions learned from your decisions">
          <summary>
            <span className="discovery-learning-summary">
              <strong>Hunter learned {preferenceSuggestions.length} {preferenceSuggestions.length === 1 ? "pattern" : "patterns"} from your decisions</strong>
              <span>Review possible search refinements</span>
            </span>
            <span className="discovery-learning-review">Review suggestions</span>
          </summary>
          <div className="discovery-learning-panel">
            <p>Nothing changes automatically. Review an exclusion before saving it to this search, or dismiss suggestions that are not useful.</p>
            <div className="discovery-learning-list">
              {preferenceSuggestions.map(suggestion => (
                <article className="discovery-learning-item" key={suggestion.id}>
                  <div>
                    <strong>“{suggestion.term}”</strong>
                    <span>Appeared in {suggestion.ignored_count} ignored roles</span>
                  </div>
                  <div className="discovery-learning-actions">
                    <button className="button compact" type="button" onClick={() => reviewPreferenceSuggestion(suggestion.term)}>
                      Review exclusion
                    </button>
                    <button
                      className="icon-button"
                      type="button"
                      disabled={Boolean(dismissingSuggestionId)}
                      onClick={() => void dismissPreferenceSuggestion(suggestion.id)}
                      aria-label={`Dismiss suggestion for ${suggestion.term}`}
                      title="Dismiss suggestion"
                    >
                      <XIcon size={14} />
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </div>
        </details>
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
            {exclusionUndoIds.length && !pending ? (
              <button className="button compact" type="button" onClick={() => void undoAppliedExclusions()}>Undo hiding roles</button>
            ) : null}
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
        {reviewQueue.length ? (
          <button
            className="button primary discovery-review-next"
            type="button"
            title={`Review the top ${reviewBatch.length} of ${reviewQueue.length} new roles, ordered by fit and freshness`}
            onClick={() => setReviewCandidateId(reviewBatch[0].id)}
          >
            Review next <span>{reviewBatch.length} of {reviewQueue.length}</span>
          </button>
        ) : null}
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
              <th>Match</th>
              <th>Source</th>
              <th>Freshness</th>
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
                  <span className="cell-subtle">{processingLabel(candidate)}</span>
                </td>
                <td>
                  <span className={`pill source-${candidate.source_trust}`}>{candidate.source_trust_label}</span>
                  <span className="cell-subtle">{candidate.source_confidence} confidence</span>
                </td>
                <td>
                  <span className={`pill freshness-${candidate.freshness_status || "unchecked"}`}>
                    {freshnessShortLabel(candidate)}
                  </span>
                  <span className="cell-subtle">
                    {candidate.freshness_checked_at ? dateOnlyLabel(candidate.freshness_checked_at) : "Not checked"}
                  </span>
                </td>
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
          index={activeReviewCandidates.findIndex(candidate => candidate.id === reviewCandidate.id)}
          total={activeReviewCandidates.length}
          pending={pending}
          close={() => setReviewCandidateId("")}
          previous={() => {
            const index = activeReviewCandidates.findIndex(candidate => candidate.id === reviewCandidate.id);
            setReviewCandidateId(activeReviewCandidates[index - 1]?.id || reviewCandidate.id);
          }}
          next={() => setReviewCandidateId(nextReviewCandidateId(reviewCandidate))}
          edit={() => {
            setReviewCandidateId("");
            setEditingCandidate(reviewCandidate);
          }}
          ignore={reason => void reviewCandidateStatus(reviewCandidate, "ignored", reason)}
          markCompanyNotInterested={() => void markCandidateCompanyNotInterested(reviewCandidate)}
          markDuplicate={applicationId => void reviewCandidateDuplicate(reviewCandidate, applicationId)}
          ingest={() => void reviewCandidateStatus(reviewCandidate, "ingested")}
        />
      ) : null}
    </>
  );
}

function FitBrief({ candidate, company }: { candidate: DiscoveryCandidate; company?: Company }) {
  return (
    <section className="discovery-fit-brief" aria-label="Fit brief">
      <div className="discovery-signal-strip" aria-label="Role quality signals">
        <div>
          <span>Match</span>
          <strong>{candidate.processing_status === "ready" ? candidate.fit_score || "—" : "Pending"}</strong>
        </div>
        <div>
          <span>Source</span>
          <strong>{candidate.source_trust_label}</strong>
        </div>
        <div>
          <span>Freshness</span>
          <strong>{freshnessShortLabel(candidate)}</strong>
        </div>
      </div>
      <div className="discovery-fit-summary">
        <strong>{candidate.fit_summary || "Hunter needs more posting details before scoring fit."}</strong>
        <span>{candidate.lane_match || "Search-lane match needs confirmation"}</span>
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
  markCompanyNotInterested,
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
  ignore: (reason: string) => void;
  markCompanyNotInterested: () => void;
  markDuplicate: (applicationId: string) => void;
  ingest: () => void;
}) {
  const [duplicateOpen, setDuplicateOpen] = useState(false);
  const [ignoreOpen, setIgnoreOpen] = useState(false);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && ignoreOpen) {
        setIgnoreOpen(false);
        return;
      }
      if (event.key === "Escape" && duplicateOpen) {
        setDuplicateOpen(false);
        return;
      }
      if (event.key === "Escape") close();
      if (duplicateOpen || ignoreOpen) return;
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
  }, [close, duplicateOpen, ignoreOpen, next, previous]);

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
        {ignoreOpen ? (
          <section className="discovery-ignore-reasons" aria-label="Why are you ignoring this role?">
            <div>
              <strong>What made this a poor fit?</strong>
              <span>Optional, but it helps Hunter make better suggestions.</span>
            </div>
            <div className="discovery-ignore-options">
              {IGNORE_REASON_OPTIONS.map(option => (
                <button
                  className="button compact"
                  key={option.id}
                  type="button"
                  disabled={pending}
                  onClick={() => ignore(option.id)}
                >
                  {option.label}
                </button>
              ))}
              <button className="button compact subtle" type="button" disabled={pending} onClick={() => ignore("")}>
                Skip reason
              </button>
              <button className="icon-button" type="button" onClick={() => setIgnoreOpen(false)} aria-label="Cancel ignoring"><XIcon size={14} /></button>
            </div>
          </section>
        ) : null}
        <div className="discovery-review-actions">
          <div>
            <button className="button" type="button" disabled={index <= 0 || pending} onClick={previous}>Previous</button>
            <button className="button" type="button" disabled={total <= 1 || pending} onClick={next}>Next</button>
          </div>
          <div>
            <a className="button" href={candidate.canonical_url || candidate.url} target="_blank" rel="noreferrer"><ExternalIcon size={15} /> Open posting</a>
            <button className="button" type="button" disabled={pending} onClick={edit}>Edit details</button>
            <button className="button" type="button" disabled={pending} onClick={() => setIgnoreOpen(true)}>Ignore</button>
            <button
              className="button company-not-interested"
              type="button"
              disabled={pending || !company}
              onClick={markCompanyNotInterested}
              title={company
                ? `Hide current and future Discovery roles from ${company.name}`
                : "Associate a company before marking it Not interested"}
            >
              Not interested in company
            </button>
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
  const initialCompany = companies.find(company => company.id === candidate.company_id);
  const [draft, setDraft] = useState<DiscoveryCandidateDetails>({
    company_id: candidate.company_id,
    company_name: initialCompany?.name || candidate.company || "",
    title: candidate.title,
    canonical_url: candidate.canonical_url,
    location: candidate.location,
    work_mode: candidate.work_mode,
    description_text: candidate.description_text,
    notes: candidate.notes
  });
  const [descriptionEditorOpen, setDescriptionEditorOpen] = useState(
    () => !usableDiscoveryDescription(candidate.description_text)
  );
  const companyName = draft.company_name || "";
  const detailsRequirements = candidateDetailRequirements(draft);
  const descriptionRequired = !usableDiscoveryDescription(draft.description_text || "");
  const linkedCompany = companies.find(company => company.id === draft.company_id)
    || companies.find(company => normalizedCompanyName(company.name) === normalizedCompanyName(companyName));
  const companyOptions = useMemo(
    () => [...companies].sort((left, right) => left.name.localeCompare(right.name)),
    [companies]
  );

  function updateCompanyName(value: string) {
    const match = companies.find(company => normalizedCompanyName(company.name) === normalizedCompanyName(value));
    setDraft(current => ({
      ...current,
      company_id: match?.id || "",
      company_name: value
    }));
  }

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
            <input
              required
              list="discovery-company-options"
              value={companyName}
              onChange={event => updateCompanyName(event.target.value)}
              placeholder="Select or enter a company"
              autoComplete="off"
            />
            <datalist id="discovery-company-options">
              {companyOptions.map(company => <option key={company.id} value={company.name} />)}
            </datalist>
            <small>Select an existing company or type a new company name.</small>
          </label>
          <label className="form-field">Role title <input required value={draft.title || ""} onChange={event => setDraft({ ...draft, title: event.target.value })} /></label>
          <label className="form-field">Location <input value={draft.location || ""} onChange={event => setDraft({ ...draft, location: event.target.value })} /></label>
          <label className="form-field">Work mode <input value={draft.work_mode || ""} onChange={event => setDraft({ ...draft, work_mode: event.target.value })} placeholder="Remote, Hybrid, or On-site" /></label>
          {detailsRequirements.length ? (
            <div className="form-field full discovery-details-requirements" role="note">
              <strong>Still needed before Hunter can verify this role</strong>
              <ul>{detailsRequirements.map(requirement => <li key={requirement}>{requirement}</li>)}</ul>
            </div>
          ) : candidate.processing_status !== "ready" ? (
            <div className="form-field full discovery-details-requirements complete" role="note">
              <strong>Ready to verify</strong>
              <span>Save these details to rescore the role.</span>
            </div>
          ) : null}
          {linkedCompany ? (
            <div className="form-field full discovery-company-source">
              <span>{[linkedCompany.industry, linkedCompany.company_size].filter(Boolean).join(" · ") || "Company details have not been researched yet."}</span>
              <Link to={routes.companyDetail(linkedCompany.id)}>View or research company details</Link>
            </div>
          ) : companyName.trim() ? (
            <div className="form-field full discovery-company-source new-company">
              <span>Hunter will add “{companyName.trim()}” as a discovered company when you save.</span>
            </div>
          ) : null}
          <label className="form-field full">Employer posting URL <input type="url" value={draft.canonical_url || ""} onChange={event => setDraft({ ...draft, canonical_url: event.target.value })} /></label>
          <details
            className="form-field full discovery-description-editor"
            open={descriptionEditorOpen}
            onToggle={event => setDescriptionEditorOpen(event.currentTarget.open)}
          >
            <summary>
              Posting description
              {descriptionRequired ? <span className="discovery-description-required">Required</span> : null}
            </summary>
            <textarea className="discovery-description-input" value={draft.description_text || ""} onChange={event => setDraft({ ...draft, description_text: event.target.value })} />
            <small>
              {(draft.description_text || "").trim().length.toLocaleString()} characters
              {descriptionRequired ? " · Add at least 500 characters of actual posting content, not a redirect or search-page response." : " · Complete enough for fit scoring."}
            </small>
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

function normalizedCompanyName(value: string) {
  return value.trim().toLocaleLowerCase();
}

function usableDiscoveryDescription(value: string) {
  const description = value.trim();
  if (description.length < 500) return false;
  if (!description.startsWith("{")) return true;
  try {
    const payload = JSON.parse(description) as Record<string, unknown>;
    return payload.widget !== "redirect" && !payload.externalSpa;
  } catch {
    return true;
  }
}

function candidateDetailRequirements(details: DiscoveryCandidateDetails) {
  const requirements: string[] = [];
  if (!details.company_id && !(details.company_name || "").trim()) {
    requirements.push("Select or add a company.");
  }
  if (!(details.title || "").trim()) {
    requirements.push("Add the role title.");
  }
  if (!usableDiscoveryDescription(details.description_text || "")) {
    requirements.push("Replace the captured response with the complete posting description.");
  }
  if (!(details.location || "").trim() && (details.work_mode || "").trim().toLocaleLowerCase() !== "remote") {
    requirements.push("Add a location, or set the work mode to Remote.");
  }
  return requirements;
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

function discoveryCandidateMatches(candidate: DiscoveryCandidate, filter: DiscoveryFilter) {
  if (filter === "recommended") {
    return candidate.recommendation_eligible;
  }
  if (filter === "needs-details") return candidate.status === "new" && candidate.processing_status !== "ready";
  if (filter === "all") return true;
  return candidate.status === filter;
}

function discoveryCandidateComparator(left: DiscoveryCandidate, right: DiscoveryCandidate) {
  const freshnessRank: Record<string, number> = {
    "confirmed-open": 3,
    "": 1,
    "needs-review": 0,
    closed: -1
  };
  const sourceRank: Record<string, number> = {
    employer: 3,
    network: 2,
    unverified: 1,
    aggregator: 0,
    closed: -1
  };
  return Number(right.recommendation_eligible) - Number(left.recommendation_eligible)
    || (freshnessRank[right.freshness_status] ?? 0) - (freshnessRank[left.freshness_status] ?? 0)
    || (sourceRank[right.source_trust] ?? 0) - (sourceRank[left.source_trust] ?? 0)
    || Number(right.processing_status === "ready") - Number(left.processing_status === "ready")
    || Number(right.fit_score || 0) - Number(left.fit_score || 0)
    || (right.last_seen_at || "").localeCompare(left.last_seen_at || "");
}

function candidateMatchesExclusionTerms(candidate: DiscoveryCandidate, terms: string[]) {
  const text = `${candidate.title} ${candidate.description_text}`.toLowerCase();
  return terms.some(term => term.trim() && text.includes(term.trim().toLowerCase()));
}

function processingLabel(candidate: DiscoveryCandidate) {
  if (candidate.processing_status === "ready") return "Verified";
  return "Needs Details";
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

function freshnessShortLabel(candidate: DiscoveryCandidate) {
  if (candidate.freshness_status === "confirmed-open") return "Open";
  if (candidate.freshness_status === "closed") return "Closed";
  if (candidate.freshness_status === "needs-review") return "Review";
  return "Unchecked";
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
