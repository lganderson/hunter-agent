import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  applyDiscoverySearchExclusions,
  captureDiscoveryCandidates,
  dismissSuggestion,
  pursueDiscoveryCandidate,
  markDiscoveryCandidateDuplicate,
  updateDiscoveryCandidate,
  updateDiscoveryCandidates,
  updateDiscoveryCandidateDetails,
  undoDiscoveryCandidateDecision,
  upsertCompany,
  upsertDiscoverySearch,
  undoDiscoverySearchExclusions
} from "../core/api";
import { dateOnlyLabel, titleCase } from "../core/format";
import { routes } from "../core/routes";
import { compareNumber, compareText, nextSortState, type SortDirection, type SortState } from "../core/tableSort";
import { selectionFromParam, selectionParamValue, sortFromParams, usePersistentViewParams } from "../core/viewState";
import type {
  AppState,
  Application,
  Company,
  DiscoveryCandidate,
  CandidateEnrichmentJob,
  DiscoveryCandidateDetails,
  DiscoveryLastRunSummary,
  DiscoverySearch,
  DiscoverySearchLaneDefinition,
  DiscoverySearchUpdates
} from "../core/types";
import { BriefcaseIcon, CheckIcon, ExternalIcon, FilterIcon, PlusIcon, SearchIcon, XIcon } from "../components/Icons";
import { SortableHeader } from "../components/Primitives";
import { CandidateBulkActions, CandidateSelectionCheckbox } from "./CandidateBulkActions";

type DiscoveryModeProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
  applyDiscoveryCandidateUpdate: (candidate: DiscoveryCandidate, posting?: Application | null, removePostingId?: string) => void;
  enrichmentJob?: CandidateEnrichmentJob | null;
  startDiscoveryJob?: (payload: CandidateEnrichmentJob["request"]) => Promise<CandidateEnrichmentJob>;
  startEnrichmentJob?: (payload: CandidateEnrichmentJob["request"]) => Promise<CandidateEnrichmentJob>;
};

type DiscoveryFilter = "needs-decision" | "pursued" | "ignored" | "duplicate" | "unavailable";
type DiscoverySortKey = "candidate" | "company" | "industry" | "size" | "match" | "source" | "freshness";
const DISCOVERY_SORT_KEYS: DiscoverySortKey[] = ["candidate", "company", "industry", "size", "match", "source", "freshness"];

const DISCOVERY_FILTERS: Array<{ id: DiscoveryFilter; label: string }> = [
  { id: "needs-decision", label: "Needs decision" },
  { id: "pursued", label: "Pursued" },
  { id: "ignored", label: "Ignored" },
];
const DISCOVERY_FILTER_VALUES: DiscoveryFilter[] = ["needs-decision", "pursued", "ignored", "duplicate", "unavailable"];
const DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES = new Set(["not-interested", "archived"]);
const WORK_MODE_OPTIONS: Array<{ id: DiscoverySearchLaneDefinition["work_modes"][number]; label: string }> = [
  { id: "on-site", label: "On-site" },
  { id: "hybrid", label: "Hybrid" },
  { id: "remote", label: "Remote" }
];
const ROLE_FAMILY_OPTIONS = [
  { id: "technical-program", label: "Technical program leadership", description: "TPM through staff, principal, and lead levels" },
  { id: "engineering-delivery", label: "Engineering delivery", description: "Engineering programs, technical projects, and delivery leads" },
  { id: "product-platform", label: "Product and platform strategy", description: "Senior and principal product, technical product, platform product, and product strategy leads" },
  { id: "product-operations", label: "Product systems and operations", description: "Product ops, product systems, development operations, and enablement builders" },
  { id: "technologist-prototyping", label: "Technologist and prototyping", description: "Product, creative, and design technologists plus prototyping and innovation leads" },
  { id: "customer-implementation", label: "Customer implementation", description: "Technical solutions, engagement, and implementation programs" },
  { id: "games-interactive", label: "Games and interactive delivery", description: "Technical producers, game producers, and development directors" },
  { id: "systems-hardware", label: "Systems and product development", description: "Systems programs, product development, and NPI" }
] as const;
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
const MAX_BULK_INGEST = 25;
const DISMISSED_DISCOVERY_RUN_KEY = "hunter-dismissed-discovery-run-v1";

export function DiscoveryMode({ data, refresh, applyDiscoveryCandidateUpdate, enrichmentJob = null, startDiscoveryJob, startEnrichmentJob }: DiscoveryModeProps) {
  const { params: viewParams, updateParams: updateViewParams } = usePersistentViewParams("candidates");
  const requestedSearchId = viewParams.get("search_id") || "";
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
  const resultSearch = viewParams.get("discovery_q") || "";
  const rawRequestedResultFilter = viewParams.get("discovery_status");
  const requestedResultFilter = legacyDiscoveryFilter(rawRequestedResultFilter);
  const resultFilter: DiscoveryFilter = DISCOVERY_FILTER_VALUES.includes(requestedResultFilter as DiscoveryFilter)
    ? requestedResultFilter as DiscoveryFilter
    : "needs-decision";
  const [operationStatus, setOperationStatus] = useState("");
  const [pending, setPending] = useState(false);
  const [runDetailsOpen, setRunDetailsOpen] = useState(false);
  const [dismissedRunKey, setDismissedRunKey] = useState(() => storedDiscoveryRunKey());
  const [pendingCandidateId, setPendingCandidateId] = useState("");
  const enrichmentActive = enrichmentJob?.status === "queued" || enrichmentJob?.status === "running";
  const discoveryActive = enrichmentActive && enrichmentJob?.job_type === "candidate-discovery";
  const [editingCandidate, setEditingCandidate] = useState<DiscoveryCandidate | null>(null);
  const [reviewCandidateId, setReviewCandidateId] = useState("");
  const [ingestedPostingId, setIngestedPostingId] = useState("");
  const [exclusionUndoIds, setExclusionUndoIds] = useState<string[]>([]);
  const [applyExistingExclusions, setApplyExistingExclusions] = useState(true);
  const [dismissingSuggestionId, setDismissingSuggestionId] = useState("");
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<Set<string>>(() => new Set());
  const [decisionUndo, setDecisionUndo] = useState<{
    candidateId: string;
    decision: "ignored" | "pursued";
    applicationId: string;
    removePosting: boolean;
  } | null>(null);
  const sort = sortFromParams(viewParams, "discovery_sort", "discovery_direction", DISCOVERY_SORT_KEYS, { key: "match", direction: "desc" });
  const companyById = useMemo(
    () => new Map(data.companies.map(company => [company.id, company])),
    [data.companies]
  );
  const persistedRunKey = selectedSearch?.last_run_at
    ? `${selectedSearch.id}:${selectedSearch.last_run_at}`
    : "";
  const currentRunNotice = selectedSearch?.last_run_at
    ? { runKey: persistedRunKey, searchId: selectedSearch.id, summary: selectedSearch.last_run_summary }
    : null;
  const showRunNotice = Boolean(
    currentRunNotice
      && currentRunNotice.runKey !== dismissedRunKey
      && hasDiscoveryRunSummary(currentRunNotice.summary)
      && !pending
  );
  const currentRunUsableCount = currentRunNotice
    ? Number(currentRunNotice.summary.new_count || 0)
      + Number(currentRunNotice.summary.updated_count || 0)
      + Number(currentRunNotice.summary.associated_count || 0)
    : 0;

  useEffect(() => {
    if (selectedSearch && !editingSearch) setSearchDraft(searchUpdates(selectedSearch));
  }, [editingSearch, selectedSearch]);

  useEffect(() => {
    setRunDetailsOpen(false);
  }, [selectedSearch?.id]);

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
      candidate => (!candidate.company_id || !discoveryExcludedCompanyIds.has(candidate.company_id))
        && (candidate.status !== "new" || Boolean(candidate.lane_match))
    ),
    [data.discovery_candidates, discoveryExcludedCompanyIds]
  );
  const companyOptions = useMemo(() => [...new Map(selectedCandidates
    .map(candidate => companyById.get(candidate.company_id))
    .filter((company): company is Company => Boolean(company))
    .map(company => [company.id, company])).values()].sort((left, right) => left.name.localeCompare(right.name)), [companyById, selectedCandidates]);
  const industryOptions = useMemo(() => uniqueValues(companyOptions.map(company => company.industry)), [companyOptions]);
  const sizeOptions = useMemo(() => uniqueValues(companyOptions.map(company => company.company_size)), [companyOptions]);
  const sourceOptions = useMemo(() => uniqueValues(selectedCandidates.map(discoverySourceLabel)), [selectedCandidates]);
  const companyOptionIds = companyOptions.map(company => company.id);
  const selectedCompanyIds = selectionFromParam(viewParams.get("discovery_companies"), companyOptionIds, companyOptionIds);
  const selectedIndustries = selectionFromParam(viewParams.get("discovery_industries"), industryOptions, industryOptions);
  const selectedSizes = selectionFromParam(viewParams.get("discovery_sizes"), sizeOptions, sizeOptions);
  const selectedSources = selectionFromParam(viewParams.get("discovery_sources"), sourceOptions, sourceOptions);
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
      .filter(candidate => matchesDiscoverySelections(
        candidate,
        companyById.get(candidate.company_id),
        selectedCompanyIds,
        companyOptionIds,
        selectedIndustries,
        industryOptions,
        selectedSizes,
        sizeOptions,
        selectedSources,
        sourceOptions
      ))
      .sort((left, right) => compareDiscoveryCandidateRows(left, right, sort, companyById)),
    [companyById, companyOptionIds, industryOptions, resultFilter, resultSearch, selectedCandidates, selectedCompanyIds, selectedIndustries, selectedSizes, selectedSources, sizeOptions, sort, sourceOptions]
  );

  function changeSort(key: DiscoverySortKey, initialDirection: SortDirection) {
    const next = nextSortState(sort, key, initialDirection);
    updateViewParams({
      discovery_sort: next.key === "match" ? null : next.key,
      discovery_direction: next.direction === "desc" ? null : next.direction
    });
  }
  const visibleCandidateIds = useMemo(
    () => visibleCandidates.map(candidate => candidate.id),
    [visibleCandidates]
  );
  const bulkSelectedCandidates = useMemo(
    () => visibleCandidates.filter(candidate => selectedCandidateIds.has(candidate.id)),
    [selectedCandidateIds, visibleCandidates]
  );
  const bulkIngestCandidates = bulkSelectedCandidates.filter(
    candidate => candidate.recommendation_eligible
      && Boolean(candidate.company_id && candidate.title)
  );
  const bulkIgnoreCandidates = bulkSelectedCandidates.filter(candidate => candidate.status === "new");
  const bulkRestoreCandidates = bulkSelectedCandidates.filter(
    candidate => candidate.status === "ignored" || candidate.status === "duplicate"
  );
  const allVisibleSelected = visibleCandidateIds.length > 0
    && visibleCandidateIds.every(id => selectedCandidateIds.has(id));
  const someVisibleSelected = visibleCandidateIds.some(id => selectedCandidateIds.has(id));

  useEffect(() => {
    const visibleIds = new Set(visibleCandidateIds);
    setSelectedCandidateIds(previous => {
      const next = new Set([...previous].filter(id => visibleIds.has(id)));
      return sameStringSet(previous, next) ? previous : next;
    });
  }, [visibleCandidateIds]);

  const counts = useMemo(
    () => Object.fromEntries(
      DISCOVERY_FILTERS.map(filter => [
        filter.id,
        selectedCandidates.filter(candidate => discoveryCandidateMatches(candidate, filter.id)).length
      ])
    ) as Record<DiscoveryFilter, number>,
    [selectedCandidates]
  );
  const decisionHistoryCount = DISCOVERY_FILTERS.reduce((total, filter) => total + counts[filter.id], 0);
  const reviewQueue = useMemo(
    () => [...selectedCandidates
      .filter(candidate => candidate.recommendation_eligible)]
      .sort((left, right) => (
        compareNumber(left.fit_score, right.fit_score, "desc")
        || compareText(left.title, right.title, "asc")
        || compareText(left.id, right.id, "asc")
      )),
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
      && (!editingSearchId || (candidate.search_ids || [candidate.search_id]).includes(editingSearchId))
      && candidateMatchesExclusionTerms(candidate, addedExclusionTerms)
    ),
    [addedExclusionTerms, editingSearchId, selectedCandidates]
  );

  function chooseSearch(searchId: string) {
    updateViewParams({ search_id: searchId || null });
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
      updateViewParams({ search_id: result.search.id });
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
      setOperationStatus(`Restored ${result.count} role${result.count === 1 ? "" : "s"} to Needs decision. The search exclusions remain saved.`);
    } catch (error) {
      setOperationStatus(`Could not restore roles. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function runSearch(useBrowserFallback = false) {
    if (!selectedSearch || !startDiscoveryJob || enrichmentActive) return;
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus("");
    setRunDetailsOpen(false);
    try {
      const job = await startDiscoveryJob({
        search_id: selectedSearch.id,
        use_browser_fallback: useBrowserFallback,
        enrichment_limit: 100
      });
      setOperationStatus(job.message);
    } catch (error) {
      setOperationStatus(`Discovery search failed. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  function dismissRunNotice() {
    if (!currentRunNotice) return;
    setDismissedRunKey(currentRunNotice.runKey);
    setRunDetailsOpen(false);
    try {
      window.localStorage.setItem(DISMISSED_DISCOVERY_RUN_KEY, currentRunNotice.runKey);
    } catch {
      // The completion notice still dismisses for this session when local storage is unavailable.
    }
  }

  async function enrichPendingCandidates() {
    if (!startEnrichmentJob || enrichmentActive) return;
    setOperationStatus("Queuing automatic candidate detail checks…");
    try {
      const job = await startEnrichmentJob({ limit: 100 });
      setOperationStatus(job.message);
    } catch (error) {
      setOperationStatus(`Could not start candidate enrichment. ${errorMessage(error)}`);
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
      const pendingDetails = result.captured.filter(candidate => candidate.detail_state === "pending-enrichment" || candidate.detail_state === "source-verification").length;
      const needsInput = result.captured.filter(candidate => candidate.detail_state === "needs-input").length;
      setOperationStatus(
        `Processed ${result.count} role${result.count === 1 ? "" : "s"}.`
        + `${pendingDetails ? ` ${pendingDetails} queued for automatic detail enrichment.` : ""}`
        + `${needsInput ? ` ${needsInput} need your input.` : ""}`
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
      if (result.candidate.detail_state === "ready") {
        setEditingCandidate(null);
        setOperationStatus("Role details verified and fit rescored.");
      } else {
        setEditingCandidate(result.candidate);
        setOperationStatus(result.candidate.detail_next_action || "Changes saved. Hunter will continue automatic detail checks.");
      }
    } catch (error) {
      setOperationStatus(`Could not update role. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  async function setCandidateStatus(
    candidate: DiscoveryCandidate,
    status: "new" | "ignored"
  ) {
    setPendingCandidateId(candidate.id);
    setIngestedPostingId("");
    setDecisionUndo(null);
    setOperationStatus(status === "ignored" ? "Ignoring role..." : "Returning role to Needs decision...");
    try {
      const result = await updateDiscoveryCandidate(candidate.id, status);
      applyDiscoveryCandidateUpdate(result.candidate);
      if (status === "ignored") {
        setDecisionUndo({ candidateId: candidate.id, decision: "ignored", applicationId: "", removePosting: false });
      }
      setOperationStatus(status === "ignored" ? "Role ignored." : "Role returned to Needs decision.");
      return true;
    } catch (error) {
      setOperationStatus(`Could not update result. ${errorMessage(error)}`);
      return false;
    } finally {
      setPendingCandidateId("");
    }
  }

  async function pursueCandidate(candidate: DiscoveryCandidate) {
    setPendingCandidateId(candidate.id);
    setIngestedPostingId("");
    setDecisionUndo(null);
    setOperationStatus("Adding role to Considering...");
    try {
      const result = await pursueDiscoveryCandidate(candidate.id);
      applyDiscoveryCandidateUpdate(result.candidate, result.posting);
      setIngestedPostingId(result.posting.id);
      setDecisionUndo({
        candidateId: candidate.id,
        decision: "pursued",
        applicationId: result.posting.id,
        removePosting: result.created
      });
      setOperationStatus(result.created ? "Role pursued and added to Considering." : "Role pursued; the posting was already tracked.");
      return true;
    } catch (error) {
      setOperationStatus(`Could not pursue role. ${errorMessage(error)}`);
      return false;
    } finally {
      setPendingCandidateId("");
    }
  }

  async function runBulkCandidateAction(action: "pursue" | "ignored" | "new") {
    const candidates = action === "pursue"
      ? bulkIngestCandidates
      : action === "ignored"
        ? bulkIgnoreCandidates
        : bulkRestoreCandidates;
    if (!candidates.length) return;
    if (action === "pursue" && candidates.length > MAX_BULK_INGEST) return;

    setPending(true);
    setIngestedPostingId("");
    try {
      if (action === "pursue") {
        let successCount = 0;
        let failureCount = 0;
        let postingId = "";
        for (const [index, candidate] of candidates.entries()) {
          setOperationStatus(`Pursuing ${index + 1} of ${candidates.length} selected roles...`);
          try {
            const result = await pursueDiscoveryCandidate(candidate.id);
            applyDiscoveryCandidateUpdate(result.candidate, result.posting);
            successCount += 1;
            postingId = result.posting.id || postingId;
          } catch {
            failureCount += 1;
          }
        }
        setIngestedPostingId(successCount === 1 ? postingId : "");
        setOperationStatus(
          `${successCount} role${successCount === 1 ? "" : "s"} pursued.`
          + (failureCount ? ` ${failureCount} could not be pursued.` : "")
        );
      } else {
        const status = action === "ignored" ? "ignored" : "new";
        setOperationStatus(
          action === "ignored"
            ? `Ignoring ${candidates.length} selected Discovery results...`
            : `Returning ${candidates.length} selected roles to Needs decision...`
        );
        const result = await updateDiscoveryCandidates(
          candidates.map(candidate => candidate.id),
          status
        );
        result.candidates.forEach(candidate => applyDiscoveryCandidateUpdate(candidate));
        setOperationStatus(
          action === "ignored"
            ? `${candidates.length} Discovery results ignored.`
            : `${candidates.length} roles returned to Needs decision.`
        );
      }
      setSelectedCandidateIds(new Set());
    } catch (error) {
      setOperationStatus(`Could not update selected Discovery results. ${errorMessage(error)}`);
    } finally {
      setPending(false);
    }
  }

  function toggleCandidateSelection(candidateId: string, checked: boolean) {
    setSelectedCandidateIds(previous => {
      const next = new Set(previous);
      if (checked) next.add(candidateId);
      else next.delete(candidateId);
      return next;
    });
  }

  function toggleAllVisibleCandidates(checked: boolean) {
    setSelectedCandidateIds(checked ? new Set(visibleCandidateIds) : new Set());
  }

  async function markCandidateDuplicate(candidate: DiscoveryCandidate, applicationId: string) {
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus("Associating duplicate with the existing posting...");
    try {
      const result = await markDiscoveryCandidateDuplicate(candidate.id, applicationId);
      applyDiscoveryCandidateUpdate(result.candidate);
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
    status: "ignored" | "pursued"
  ) {
    const nextId = nextReviewCandidateId(candidate);
    const succeeded = status === "ignored"
      ? await setCandidateStatus(candidate, "ignored")
      : await pursueCandidate(candidate);
    if (succeeded) setReviewCandidateId(nextId);
  }

  async function undoLastDecision() {
    if (!decisionUndo) return;
    setPendingCandidateId(decisionUndo.candidateId);
    setOperationStatus("Undoing decision...");
    try {
      const result = await undoDiscoveryCandidateDecision(
        decisionUndo.candidateId,
        decisionUndo.decision,
        decisionUndo.applicationId,
        decisionUndo.removePosting
      );
      applyDiscoveryCandidateUpdate(
        result.candidate,
        null,
        result.posting_removed ? decisionUndo.applicationId : ""
      );
      setIngestedPostingId("");
      setDecisionUndo(null);
      setOperationStatus("Decision undone. Role returned to Needs decision.");
    } catch (error) {
      setOperationStatus(`Could not undo decision. ${errorMessage(error)}`);
    } finally {
      setPendingCandidateId("");
    }
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
            {data.discovery_searches.map(search => <option key={search.id} value={search.id}>{shortSearchName(search.name)}</option>)}
          </select>
        </label>
        <button
          className="button primary"
          type="button"
          disabled={!selectedSearch || pending || enrichmentActive || !startDiscoveryJob}
          title="Searches direct ATS inventories, the web with OpenAI, and Adzuna in the background"
          onClick={() => void runSearch()}
        >
          <SearchIcon size={15} /> {discoveryActive ? "Discovery running…" : "Continue discovery"}
        </button>
        <details className="discovery-actions-menu">
          <summary className="button" aria-label="Manage Discovery search">Manage</summary>
          <div className="discovery-actions-menu-content">
            <button className="button" type="button" onClick={startNewSearch}><PlusIcon size={15} /> New search</button>
            <button className="button" type="button" disabled={!selectedSearch} onClick={editSelectedSearch}>Edit search</button>
            <button className="button" type="button" disabled={!selectedSearch} onClick={() => setCaptureOpen(value => !value)}>
              <PlusIcon size={15} /> Add found roles
            </button>
            <button
              className="button"
              type="button"
              disabled={!selectedSearch || pending || enrichmentActive || !startDiscoveryJob}
              title="Uses the signed-in Hunter Chrome profile when API providers miss a role"
              onClick={() => void runSearch(true)}
            >
              Use Chrome fallback
            </button>
          </div>
        </details>
        {selectedSearch ? (
          <span className="discovery-search-context">
            {`${shortSearchName(selectedSearch.name)} · ${discoveryLocationScope(selectedSearch.lanes)}`}
            {enrichmentActive ? " · Resolving role details in background" : ""}
          </span>
        ) : null}
      </div>

      {showRunNotice && currentRunNotice ? (
        <section className="discovery-completion" aria-label="Discovery completed">
          <div className="discovery-completion-summary" role="status">
            <span className="discovery-completion-icon"><CheckIcon size={15} /></span>
            <div className="discovery-completion-copy">
              <strong>{currentRunUsableCount ? "Discovery complete" : "No new eligible roles found"}</strong>
              <span>· {currentRunNotice.summary.new_count || 0} new roles</span>
              <span>· {currentRunNotice.summary.associated_count || 0} already known</span>
              <span>· {currentRunNotice.summary.lane_unmatched_count || 0} outside location scope</span>
            </div>
            <button className="discovery-completion-details-toggle" type="button" aria-expanded={runDetailsOpen} onClick={() => setRunDetailsOpen(value => !value)}>
              {runDetailsOpen ? "Hide details" : "View details"}
            </button>
            <button className="icon-button" type="button" onClick={dismissRunNotice} aria-label="Dismiss Discovery completion"><XIcon size={15} /></button>
          </div>
          {runDetailsOpen ? <DiscoveryRunDetails summary={currentRunNotice.summary} /> : null}
        </section>
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
          <fieldset className="form-field full discovery-role-families">
            <legend>Role families</legend>
            <div className="discovery-role-family-options">
              {ROLE_FAMILY_OPTIONS.map(option => (
                <label key={option.id}>
                  <input
                    type="checkbox"
                    checked={searchDraft.role_family_ids.includes(option.id)}
                    onChange={event => setSearchDraft({
                      ...searchDraft,
                      role_family_ids: event.target.checked
                        ? [...searchDraft.role_family_ids, option.id]
                        : searchDraft.role_family_ids.filter(id => id !== option.id)
                    })}
                  />
                  <span><strong>{option.label}</strong><small>{option.description}</small></span>
                </label>
              ))}
            </div>
            <small>Select independent search lanes. Hunter reserves result space for each selected family.</small>
          </fieldset>
          <label className="form-field full">
            Focus keywords
            <input
              value={searchDraft.keywords}
              onChange={event => setSearchDraft({ ...searchDraft, keywords: event.target.value })}
              placeholder="Optional: developer platforms, games, customer experience"
            />
            <small>Optional domain or product focus applied to every selected family. With no families selected, this becomes the exact role search.</small>
          </label>
          <label className="form-field full">
            Exclude role titles containing
            <input
              value={searchDraft.excluded_terms.join(", ")}
              onChange={event => setSearchDraft({
                ...searchDraft,
                excluded_terms: event.target.value.split(",").map(value => value.trim()).filter(Boolean)
              })}
              placeholder="sales, implementation, scrum"
            />
            <small>Optional comma-separated terms. Hunter filters matching role titles after searching every lane.</small>
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
                Hide these roles from Needs decision when I save
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
            {decisionUndo && !pendingCandidateId ? (
              <button className="button compact" type="button" onClick={() => void undoLastDecision()}>Undo</button>
            ) : null}
            {!pending && !pendingCandidateId ? <button className="icon-button table-operation-close" type="button" onClick={() => setOperationStatus("")} aria-label="Dismiss status message"><XIcon size={15} /></button> : null}
          </div>
        </div>
      ) : null}

      <div className="toolbar discovery-results-toolbar" aria-label="Discovery result filters">
        <label className="search">
          <span className="sr-only">Search Discovery results</span>
          <SearchIcon />
          <input value={resultSearch} onChange={event => updateViewParams({ discovery_q: event.target.value || null })} type="search" placeholder="Search roles, companies, sources, and fit..." />
        </label>
        <DiscoveryMultiFilter
          label="Company"
          options={companyOptions.map(company => ({ id: company.id, label: company.name }))}
          selected={selectedCompanyIds}
          onChange={values => updateViewParams({ discovery_companies: selectionParamValue(values, companyOptionIds, companyOptionIds) })}
        />
        <DiscoveryMultiFilter label="Industry" options={industryOptions.map(value => ({ id: value, label: value }))} selected={selectedIndustries} onChange={values => updateViewParams({ discovery_industries: selectionParamValue(values, industryOptions, industryOptions) })} />
        <DiscoveryMultiFilter label="Size" options={sizeOptions.map(value => ({ id: value, label: value }))} selected={selectedSizes} onChange={values => updateViewParams({ discovery_sizes: selectionParamValue(values, sizeOptions, sizeOptions) })} />
        <DiscoveryMultiFilter label="Source" options={sourceOptions.map(value => ({ id: value, label: value }))} selected={selectedSources} onChange={values => updateViewParams({ discovery_sources: selectionParamValue(values, sourceOptions, sourceOptions) })} />
        {reviewQueue.length ? (
          <button
            className="button primary discovery-review-next"
            type="button"
            title={`Review the top ${reviewBatch.length} of ${reviewQueue.length} verified roles, ordered by match`}
            onClick={() => setReviewCandidateId(reviewBatch[0].id)}
          >
            Review <span>{reviewBatch.length} of {reviewQueue.length} verified</span>
          </button>
        ) : null}
        <button className="button" type="button" onClick={() => updateViewParams({ discovery_q: null, discovery_status: null, discovery_companies: null, discovery_industries: null, discovery_sizes: null, discovery_sources: null, discovery_sort: null, discovery_direction: null })}><FilterIcon size={15} /> Clear</button>
      </div>

      <div className="candidate-filter-bar aggregate" aria-label="Discovery result status filters">
        {DISCOVERY_FILTERS.map(filter => (
          <button
            className={resultFilter === filter.id ? "candidate-filter active" : "candidate-filter"}
            key={filter.id}
            type="button"
            onClick={() => updateViewParams({ discovery_status: filter.id === "needs-decision" ? null : filter.id })}
          >
            {filter.label}<span>{counts[filter.id]}</span>
          </button>
        ))}
      </div>

      {bulkSelectedCandidates.length ? (
        <CandidateBulkActions
          selectedCount={bulkSelectedCandidates.length}
          shownCount={visibleCandidates.length}
          pending={pending}
          clear={() => setSelectedCandidateIds(new Set())}
          actions={[
            {
              id: "pursue",
              label: `Pursue ready ${bulkIngestCandidates.length}`,
              primary: true,
              disabled: !bulkIngestCandidates.length || bulkIngestCandidates.length > MAX_BULK_INGEST,
              title: bulkIngestCandidates.length > MAX_BULK_INGEST
                ? `Select ${MAX_BULK_INGEST} or fewer ready roles to pursue at once`
                : "Only verified roles with a company and title can be pursued",
              run: () => void runBulkCandidateAction("pursue")
            },
            {
              id: "ignore",
              label: `Ignore ${bulkIgnoreCandidates.length}`,
              disabled: !bulkIgnoreCandidates.length,
              run: () => void runBulkCandidateAction("ignored")
            },
            {
              id: "restore",
              label: `Needs decision ${bulkRestoreCandidates.length}`,
              disabled: !bulkRestoreCandidates.length,
              run: () => void runBulkCandidateAction("new")
            }
          ]}
        />
      ) : (
        <div className="candidate-review-summary">
          <strong>{visibleCandidates.length}</strong>
          <span>shown from {decisionHistoryCount} roles in your decision history</span>
        </div>
      )}

      <div className="table-scroll">
        <table className="simple-table candidates-table discovery-table">
          <thead>
            <tr>
              <th className="candidate-select-column">
                <CandidateSelectionCheckbox
                  checked={allVisibleSelected}
                  indeterminate={someVisibleSelected && !allVisibleSelected}
                  disabled={!visibleCandidateIds.length || pending}
                  label={allVisibleSelected ? "Clear all shown Discovery candidates" : "Select all shown Discovery candidates"}
                  onChange={toggleAllVisibleCandidates}
                />
              </th>
              <SortableHeader activeKey={sort.key} direction={sort.direction} label="Candidate" onSort={changeSort} sortKey="candidate" />
              <SortableHeader activeKey={sort.key} direction={sort.direction} label="Company" onSort={changeSort} sortKey="company" />
              <SortableHeader activeKey={sort.key} direction={sort.direction} label="Industry" onSort={changeSort} sortKey="industry" />
              <SortableHeader activeKey={sort.key} direction={sort.direction} label="Size" onSort={changeSort} sortKey="size" />
              <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Match" onSort={changeSort} sortKey="match" />
              <SortableHeader activeKey={sort.key} direction={sort.direction} label="Source" onSort={changeSort} sortKey="source" />
              <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Freshness" onSort={changeSort} sortKey="freshness" />
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {visibleCandidates.map(candidate => {
              const company = companyById.get(candidate.company_id);
              const rowPending = pendingCandidateId === candidate.id;
              return (
              <tr className={selectedCandidateIds.has(candidate.id) ? "candidate-row-selected" : ""} key={candidate.id}>
                <td className="candidate-select-column">
                  <CandidateSelectionCheckbox
                    checked={selectedCandidateIds.has(candidate.id)}
                    disabled={pending || rowPending}
                    label={`Select ${candidate.title || "Discovery candidate"}${company?.name ? ` at ${company.name}` : ""}`}
                    onChange={checked => toggleCandidateSelection(candidate.id, checked)}
                  />
                </td>
                <td className="role-cell candidate-title-cell">
                  <strong>{candidate.title || "Role details needed"}</strong>
                  <span className="cell-subtle" title={candidateLocationLabel(candidate)}>{candidateLocationLabel(candidate)}</span>
                </td>
                <td>
                  {company
                    ? <Link to={routes.companyDetail(company.id)}>{company.name}</Link>
                    : "Company needed"}
                  {candidate.source_platform === "adzuna"
                    ? <a className="cell-subtle" href="https://www.adzuna.com/" target="_blank" rel="noreferrer">Jobs by Adzuna</a>
                    : <span className="cell-subtle">{discoverySourceLabel(candidate)}</span>}
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
                  aria-label={candidate.detail_state === "ready"
                    ? `Fit ${candidate.fit_score || "not scored"}. ${candidate.fit_summary || ""}`.trim()
                    : "Fit pending verified posting details."}
                >
                  {candidate.detail_state === "ready" ? (
                    <span className={`pill ${fitClass(candidate.fit_score)}`}>{candidate.fit_score || "—"}</span>
                  ) : (
                    <span className="pill fit-pending" title={candidate.detail_next_action || undefined}>Needs review</span>
                  )}
                  <span className="cell-subtle">{candidate.detail_state === "ready" ? processingLabel(candidate) : `⚠ ${processingLabel(candidate)}`}</span>
                </td>
                <td>
                  <span
                    className={`pill source-${candidate.source_trust}`}
                    title={`${candidate.source_confidence} confidence`}
                  >
                    {candidate.source_trust_label}
                  </span>
                  <span className="cell-subtle" title={candidate.role_family || "Unclassified search"}>
                    {candidate.role_family || "Unclassified search"}
                  </span>
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
                    <a className="icon-button" href={candidate.canonical_url || candidate.url} target="_blank" rel="noreferrer" aria-label={`Open ${candidate.title || "posting"}`} title="Open posting"><ExternalIcon size={15} /></a>
                    {candidate.status === "new" ? (
                      <button className="button compact" type="button" disabled={pending || rowPending} onClick={() => setReviewCandidateId(candidate.id)}>Review</button>
                    ) : null}
                    {candidate.status === "new" ? (
                      <button className="button compact primary" type="button" disabled={pending || rowPending || candidate.detail_state !== "ready"} title={candidate.detail_state === "ready" ? "Add to Postings in Considering" : candidate.detail_next_action} onClick={() => void pursueCandidate(candidate)}>{rowPending ? "Saving…" : "Pursue"}</button>
                    ) : null}
                    {candidate.status === "new" ? (
                      <button className="button compact" type="button" disabled={pending || rowPending} onClick={() => void setCandidateStatus(candidate, "ignored")}>Ignore</button>
                    ) : null}
                    {candidate.ingested_application_id ? (
                      <Link className="icon-button" to={routes.postingDetail(candidate.ingested_application_id)} aria-label="Open pursued posting" title="Open pursued posting"><BriefcaseIcon size={15} /></Link>
                    ) : null}
                    {candidate.status === "ignored" || candidate.status === "duplicate"
                      ? <button className="button compact" type="button" disabled={pending || rowPending} onClick={() => void setCandidateStatus(candidate, "new")}>Needs decision</button>
                      : null}
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
          pending={pending || pendingCandidateId === reviewCandidate.id}
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
          ignore={() => void reviewCandidateStatus(reviewCandidate, "ignored")}
          markCompanyNotInterested={() => void markCandidateCompanyNotInterested(reviewCandidate)}
          markDuplicate={applicationId => void reviewCandidateDuplicate(reviewCandidate, applicationId)}
          pursue={() => void reviewCandidateStatus(reviewCandidate, "pursued")}
        />
      ) : null}
    </>
  );
}

function FitBrief({ candidate, company }: { candidate: DiscoveryCandidate; company?: Company }) {
  return (
    <section className="discovery-fit-brief" aria-label="Fit brief">
      <div className="discovery-signal-strip" aria-label="Role quality signals">
        <div><span>Match</span><strong>{candidate.detail_state === "ready" ? candidate.fit_score || "—" : "Needs review"}</strong></div>
        <div><span>Role family</span><strong>{candidate.role_family || "Unclassified"}</strong></div>
        <div><span>Source</span><strong>{candidate.source_trust_label}</strong></div>
        <div><span>Freshness</span><strong>{freshnessShortLabel(candidate)}</strong></div>
      </div>
      {candidate.detail_state !== "ready" ? (
        <div className="discovery-review-warning" role="note">
          <strong>{processingLabel(candidate)}</strong>
          <span>{candidate.detail_next_action || "Confirm the posting details before pursuing."}</span>
        </div>
      ) : null}
      <div className="discovery-fit-columns">
        <div>
          <span className="eyebrow">Why it fits</span>
          {(candidate.fit_strengths || []).length
            ? <ul>{candidate.fit_strengths.map(item => <li key={item}>{item}</li>)}</ul>
            : <p>No supported fit strengths yet.</p>}
        </div>
      </div>
      {company ? <span className="discovery-fit-company">{company.industry || "Industry unknown"} · {company.company_size || "Size unknown"}</span> : null}
    </section>
  );
}

function ReviewSummarySection({ title, items, empty }: { title: string; items: string[]; empty: string }) {
  return (
    <section>
      <span className="eyebrow">{title}</span>
      {items.length ? <ul>{items.map(item => <li key={item}>{item}</li>)}</ul> : <p>{empty}</p>}
    </section>
  );
}

function candidateReviewSummary(candidate: DiscoveryCandidate) {
  const text = candidate.description_text || "";
  const responsibilities = extractPostingSection(text, ["responsibilities", "what you will do", "what you'll do", "the role"]);
  const requirements = extractPostingSection(text, ["requirements", "qualifications", "what we are looking for", "what we're looking for"]);
  const compensation = extractPostingSentences(text, /\$\s?\d|salary|compensation|base pay|pay range/i, 2);
  const responsibilityFallback = extractPostingSentences(text, /\b(lead|manage|own|drive|partner|coordinate|deliver|build|develop|oversee)\b/i, 3);
  const requirementFallback = extractPostingSentences(text, /\b(require|experience|years|degree|proficien|ability to)\b/i, 3);
  const concerns = uniqueValues([
    ...(candidate.fit_gaps || []),
    ...(candidate.detail_gaps || []).map(gap => gap.label),
    ...String(candidate.warnings || "").split(/\n+/),
    candidate.freshness_status === "needs-review" ? "Posting freshness needs confirmation" : "",
    !candidate.is_direct_employer_source ? "Direct employer posting link is missing" : ""
  ]).slice(0, 5);
  return {
    responsibilities: (responsibilities.length ? responsibilities : responsibilityFallback).slice(0, 4),
    requirements: (requirements.length ? requirements : requirementFallback).slice(0, 4),
    compensation,
    concerns
  };
}

function extractPostingSection(text: string, headings: string[]) {
  const lines = postingLines(text);
  const headingIndex = lines.findIndex(line => headings.some(heading => line.toLowerCase().replace(/[:\s]+$/, "") === heading));
  if (headingIndex < 0) return [];
  const items: string[] = [];
  for (const line of lines.slice(headingIndex + 1)) {
    if (items.length && /^[A-Z][A-Za-z &/]{2,35}:?$/.test(line)) break;
    if (usablePostingSummaryLine(line)) items.push(line.replace(/^[-•*]\s*/, ""));
    if (items.length >= 4) break;
  }
  return uniqueValuesInOrder(items);
}

function extractPostingSentences(text: string, pattern: RegExp, limit: number) {
  const candidates = postingLines(text).flatMap(line => line.split(/(?<=[.!?])\s+/));
  return uniqueValuesInOrder(candidates.filter(sentence => pattern.test(sentence) && usablePostingSummaryLine(sentence))).slice(0, limit);
}

function postingLines(text: string) {
  return String(text || "")
    .replace(/\r/g, "")
    .split(/\n+/)
    .map(line => line.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function usablePostingSummaryLine(value: string) {
  const line = value.replace(/^[-•*]\s*/, "").trim();
  if (line.length < 28 || line.length > 260) return false;
  if (/apply|save|show match|create cover letter|promoted by hirer|applicants/i.test(line)) return false;
  return line.split(/\s+/).length >= 5;
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
  pursue
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
  markCompanyNotInterested: () => void;
  markDuplicate: (applicationId: string) => void;
  pursue: () => void;
}) {
  const [duplicateOpen, setDuplicateOpen] = useState(false);
  const [decision, setDecision] = useState<"ignored" | "pursued" | "">("");
  const summary = useMemo(() => candidateReviewSummary(candidate), [candidate]);

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
            <p>{company?.name || "Company needed"}</p>
          </div>
          <button className="button compact" type="button" onClick={close}><XIcon size={18} /> Close</button>
        </div>
        <FitBrief candidate={candidate} company={company} />
        <div className="discovery-review-summary-grid">
          <ReviewSummarySection title="What you would do" items={summary.responsibilities} empty="Responsibilities were not captured clearly; open the posting to confirm." />
          <ReviewSummarySection title="Key requirements" items={summary.requirements} empty="Requirements were not captured clearly; open the posting to confirm." />
          <ReviewSummarySection title="Compensation" items={summary.compensation} empty="Not listed in the captured posting." />
          <ReviewSummarySection title="Location and work mode" items={[candidateLocationLabel(candidate)]} empty="Location not captured." />
          <ReviewSummarySection title="Concerns" items={summary.concerns} empty="No additional concerns identified." />
        </div>
        <div className="discovery-review-source-summary">
          <span>{freshnessLabel(candidate)} · {candidate.source_trust_label} source</span>
          {(candidate.source_urls || []).length > 1 ? (
            <details><summary>{candidate.source_urls.length} source links</summary><ul>{candidate.source_urls.map(url => <li key={url}><a href={url} target="_blank" rel="noreferrer">{sourceLabel(url)}</a></li>)}</ul></details>
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
            <button className="button" type="button" disabled={pending} onClick={() => { setDecision("ignored"); ignore(); }}>{pending && decision === "ignored" ? "Saving…" : "Ignore"}</button>
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
            <button className="button primary" type="button" disabled={pending || candidate.detail_state !== "ready"} onClick={() => { setDecision("pursued"); pursue(); }}>{pending && decision === "pursued" ? "Saving…" : "Pursue"}</button>
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

function DiscoveryMultiFilter({
  label,
  options,
  selected,
  onChange
}: {
  label: string;
  options: Array<{ id: string; label: string }>;
  selected: string[];
  onChange: (values: string[]) => void;
}) {
  const allSelected = options.length === selected.length;
  const selectedLabel = options.find(option => option.id === selected[0])?.label;
  const summary = allSelected ? "All" : selected.length === 1 ? selectedLabel || "1 selected" : `${selected.length} selected`;

  function toggle(value: string) {
    onChange(selected.includes(value) ? selected.filter(item => item !== value) : [...selected, value]);
  }

  return (
    <details className="filter multi-filter">
      <summary>{label} <span>{summary}</span></summary>
      <div className="multi-filter-menu">
        <label className="multi-filter-option">
          <input checked={allSelected} onChange={event => onChange(event.target.checked ? options.map(option => option.id) : [])} type="checkbox" />
          All
        </label>
        {options.map(option => (
          <label className="multi-filter-option" key={option.id}>
            <input checked={selected.includes(option.id)} onChange={() => toggle(option.id)} type="checkbox" />
            {option.label}
          </label>
        ))}
      </div>
    </details>
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
          ) : candidate.detail_state !== "ready" ? (
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
    role_family_ids: [...(search.role_family_ids || [])],
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
  return {
    name: "",
    keywords: "",
    role_family_ids: ROLE_FAMILY_OPTIONS.map(option => option.id),
    lanes: [newSearchLane(0)],
    excluded_terms: []
  };
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

function shortSearchName(name: string) {
  return name.split(/\s+[—–-]\s+/)[0]?.trim() || name;
}

function discoveryLocationScope(lanes: DiscoverySearchLaneDefinition[]) {
  const labels = lanes.map(lane => {
    const label = (lane.label || lane.location).trim();
    const remoteOnly = lane.work_modes.length === 1 && lane.work_modes[0] === "remote";
    if (remoteOnly && /^(united states|usa|us)( remote)?$/i.test(label)) return "US remote";
    return label;
  });
  return [...new Set(labels)].join(" + ") || "No location scope";
}

function hasDiscoveryRunSummary(summary: DiscoveryLastRunSummary) {
  return [summary.evaluated_count, summary.new_count, summary.updated_count, summary.enriched_count]
    .some(value => Number(value || 0) > 0);
}

function storedDiscoveryRunKey() {
  try {
    return window.localStorage.getItem(DISMISSED_DISCOVERY_RUN_KEY) || "";
  } catch {
    return "";
  }
}

function DiscoveryRunDetails({ summary }: { summary: DiscoveryLastRunSummary }) {
  const metrics = [
    ["Reviewed", summary.evaluated_count || 0],
    ["Eligible", summary.qualified_count || 0],
    ["Already in Hunter", summary.known_count || 0],
    ["Duplicates removed", summary.duplicate_count || 0],
    ["Screened out", summary.screened_count || 0],
    ["Held for later", summary.limited_count || 0],
    ["Source errors", summary.errors?.length || 0]
  ];
  return (
    <div className="discovery-completion-details">
      {metrics.map(([label, value]) => (
        <span key={String(label)}><strong>{value}</strong> {label}</span>
      ))}
    </div>
  );
}

function workModeLabel(mode: DiscoverySearchLaneDefinition["work_modes"][number]) {
  return WORK_MODE_OPTIONS.find(option => option.id === mode)?.label || titleCase(mode);
}

function discoveryCandidateMatches(candidate: DiscoveryCandidate, filter: DiscoveryFilter) {
  if (filter === "needs-decision") return candidate.status === "new";
  return candidate.status === filter;
}

function legacyDiscoveryFilter(value: string | null) {
  if (!value) return "needs-decision";
  if (["new", "recommended", "pending", "source-verification", "needs-input", "all"].includes(value)) return "needs-decision";
  if (value === "ingested") return "pursued";
  return value;
}

function matchesDiscoverySelections(
  candidate: DiscoveryCandidate,
  company: Company | undefined,
  selectedCompanyIds: string[],
  companyIds: string[],
  selectedIndustries: string[],
  industries: string[],
  selectedSizes: string[],
  sizes: string[],
  selectedSources: string[],
  sources: string[]
) {
  return matchesSelectionValue(candidate.company_id, selectedCompanyIds, companyIds)
    && matchesSelectionValue(company?.industry || "", selectedIndustries, industries)
    && matchesSelectionValue(company?.company_size || "", selectedSizes, sizes)
    && matchesSelectionValue(discoverySourceLabel(candidate), selectedSources, sources);
}

function matchesSelectionValue(value: string, selected: string[], all: string[]) {
  if (selected.length === all.length) return true;
  return selected.includes(value);
}

function uniqueValues(values: string[]) {
  return [...new Set(values.map(value => value.trim()).filter(Boolean))].sort((left, right) => left.localeCompare(right));
}

function uniqueValuesInOrder(values: string[]) {
  return [...new Set(values.map(value => value.trim()).filter(Boolean))];
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

function compareDiscoveryCandidateRows(
  left: DiscoveryCandidate,
  right: DiscoveryCandidate,
  sort: SortState<DiscoverySortKey>,
  companyById: Map<string, Company>
) {
  const leftCompany = companyById.get(left.company_id);
  const rightCompany = companyById.get(right.company_id);
  let result = 0;
  if (sort.key === "candidate") result = compareText(left.title, right.title, sort.direction);
  if (sort.key === "company") result = compareText(leftCompany?.name, rightCompany?.name, sort.direction);
  if (sort.key === "industry") result = compareText(leftCompany?.industry, rightCompany?.industry, sort.direction);
  if (sort.key === "size") result = compareText(leftCompany?.company_size, rightCompany?.company_size, sort.direction);
  if (sort.key === "match") result = compareNumber(left.fit_score, right.fit_score, sort.direction);
  if (sort.key === "source") result = compareText(left.source_trust_label, right.source_trust_label, sort.direction);
  if (sort.key === "freshness") {
    result = compareText(left.freshness_status, right.freshness_status, sort.direction)
      || compareText(left.freshness_checked_at, right.freshness_checked_at, sort.direction);
  }
  return result
    || (sort.key === "match"
      ? discoveryCandidateComparator(left, right)
      : compareNumber(left.fit_score, right.fit_score, "desc"))
    || compareText(left.id, right.id, "asc");
}

function candidateMatchesExclusionTerms(candidate: DiscoveryCandidate, terms: string[]) {
  const text = candidate.title.toLowerCase();
  return terms.some(term => term.trim() && text.includes(term.trim().toLowerCase()));
}

function processingLabel(candidate: DiscoveryCandidate) {
  if (candidate.detail_state === "ready") return "Verified";
  if (candidate.detail_state === "pending-enrichment") return "Enriching";
  if (candidate.detail_state === "source-verification") return "Verify source";
  return "Needs input";
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
    candidate.role_family,
    ...(candidate.responsibility_signals || []),
    candidate.description_excerpt,
    candidate.detail_state,
    candidate.detail_next_action,
    candidate.notes
  ].join(" ").toLowerCase().includes(query);
}

function candidateLocationLabel(candidate: DiscoveryCandidate) {
  const location = candidate.location || "Location unknown";
  return candidate.work_mode ? `${location} · ${candidate.work_mode}` : location;
}

function discoverySourceLabel(candidate: DiscoveryCandidate) {
  return candidate.source_platform === "adzuna"
    ? "Jobs by Adzuna"
    : titleCase(candidate.source_platform || "manual");
}

function discoveryReasonSummary(reasons: Record<string, number> | undefined) {
  const labels: Record<string, string> = {
    "invalid-posting-page": "invalid posting pages",
    "title-exclusion": "title exclusions",
    "lane-mismatch": "location or work-mode mismatches",
    "lane-mismatch-after-enrichment": "location mismatches after enrichment",
    "not-interested-company": "not-interested companies",
    "the ATS URL is a board, redirect, or error page": "invalid ATS pages",
    "the role match score is below 45": "low-match roles",
    "the posting is from an aggregator without a verified employer source": "unverified aggregator roles",
    "the posting is closed": "closed postings",
    "automatic-quality-screen": "other quality screens"
  };
  return Object.entries(reasons || {})
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 4)
    .map(([reason, count]) => `${count} ${labels[reason] || reason}`)
    .join(", ");
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

function sameStringSet(left: Set<string>, right: Set<string>) {
  if (left.size !== right.size) return false;
  return [...left].every(value => right.has(value));
}
