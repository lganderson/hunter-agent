import { useDiscoveryCandidateDecisions } from "./useCandidateDecisions";
import type { FormEvent } from "react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { BriefcaseIcon, CheckIcon, ExternalIcon, FilterIcon, PlusIcon, RefreshIcon, SearchIcon, XIcon } from "../components/Icons";
import { SortableHeader } from "../components/Primitives";
import {
  applyDiscoverySearchExclusions,
  captureDiscoveryCandidates,
  dismissSuggestion,
  undoDiscoverySearchExclusions,
  updateDiscoveryCandidateDetails,
  upsertCompany,
  upsertDiscoverySearch
} from "../core/api";
import { dateOnlyLabel } from "../core/format";
import { discoveryDetailToCandidate } from "../core/readModelAdapters";
import { useDiscoveryCandidateDetail } from "../core/readModelQueries";
import type { CandidatePageFacets } from "../core/readModelTypes";
import { routes } from "../core/routes";
import { compareNumber, compareText, nextSortState, type SortDirection } from "../core/tableSort";
import type {
  AppState,
  CandidateEnrichmentJob,
  Company,
  DiscoveryCandidate,
  DiscoveryCandidateDetails,
  DiscoverySearchUpdates
} from "../core/types";
import { sortFromParams, usePersistentViewParams } from "../core/viewState";
import { CandidateBulkActions, CandidateSelectionCheckbox } from "./CandidateBulkActions";
import { CandidateDetailsModal, CandidateReviewModal, DiscoveryMultiFilter, DiscoveryRunDetails, candidateLocationLabel, candidateMatchesExclusionTerms, discoveryCandidateMatches, discoveryLocationScope, discoverySourceLabel, errorMessage, fitClass, freshnessLabel, freshnessShortLabel, hasDiscoveryRunSummary, legacyDiscoveryFilter, newSearchDraft, newSearchLane, processingLabel, sameStringSet, searchUpdates, shortSearchName, storedDiscoveryRunKey, toggleLaneWorkMode, uniqueValues, updateSearchLane } from './DiscoveryDialogs';
import { canonicalCandidateRows } from "./candidateCanonicalization";
import { DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES, DISCOVERY_FILTERS, DISCOVERY_FILTER_VALUES, DISMISSED_DISCOVERY_RUN_KEY, DiscoveryFilter, EMPTY_DETAILS, MAX_BULK_INGEST, ROLE_FAMILY_OPTIONS, WORK_MODE_OPTIONS } from './discoveryConfig';
import { DISCOVERY_SORT_KEYS, discoverySelectedOptions, discoverySelectionParam, type DiscoverySortKey } from "./discoveryFilters";

type DiscoveryModeProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
  enrichmentJob?: CandidateEnrichmentJob | null;
  startDiscoveryJob?: (payload: CandidateEnrichmentJob["request"]) => Promise<CandidateEnrichmentJob>;
  startEnrichmentJob?: (payload: CandidateEnrichmentJob["request"]) => Promise<CandidateEnrichmentJob>;
  hasNextPage?: boolean;
  isFetchingNextPage?: boolean;
  loadMore?: () => void;
  statusCounts?: Record<string, number>;
  filteredTotal?: number;
  facets?: CandidatePageFacets;
};

export function DiscoveryMode({ data, refresh, enrichmentJob = null, startDiscoveryJob, startEnrichmentJob, hasNextPage = false, isFetchingNextPage = false, loadMore, statusCounts = {}, filteredTotal, facets }: DiscoveryModeProps) {
  const decisions = useDiscoveryCandidateDecisions();
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
  const [refreshingCandidateId, setRefreshingCandidateId] = useState("");
  const enrichmentActive = enrichmentJob?.status === "queued" || enrichmentJob?.status === "running";
  const discoveryActive = enrichmentActive && enrichmentJob?.job_type === "candidate-discovery";
  const [editingCandidate, setEditingCandidate] = useState<DiscoveryCandidate | null>(null);
  const [reviewCandidateId, setReviewCandidateId] = useState("");
  const selectedDetailId = editingCandidate?.id || reviewCandidateId;
  const candidateDetailQuery = useDiscoveryCandidateDetail(selectedDetailId);
  const detailedCandidate = candidateDetailQuery.data
    ? discoveryDetailToCandidate(candidateDetailQuery.data)
    : null;
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

  useEffect(() => {
    if (!enrichmentActive) setRefreshingCandidateId("");
  }, [enrichmentActive]);

  const discoveryExcludedCompanyIds = useMemo(
    () => new Set(
      data.companies
        .filter(company => DISCOVERY_EXCLUDED_COMPANY_INTEREST_STATUSES.has(company.interest_status))
        .map(company => company.id)
    ),
    [data.companies]
  );
  const selectedCandidates = useMemo(
    () => canonicalCandidateRows(data.discovery_candidates).filter(
      candidate => (!candidate.company_id || !discoveryExcludedCompanyIds.has(candidate.company_id))
        && (candidate.status !== "new"
          || Boolean(candidate.lane_match)
          || candidate.qualification_status === "needs-verification")
    ),
    [data.discovery_candidates, discoveryExcludedCompanyIds]
  );
  const companyOptions = useMemo(() => {
    const ids = facets?.companies.map(facet => facet.value) || selectedCandidates.map(candidate => candidate.company_id);
    return [...new Set(ids)].map(id => companyById.get(id))
      .filter((company): company is Company => Boolean(company))
      .sort((left, right) => left.name.localeCompare(right.name));
  }, [facets?.companies, companyById, selectedCandidates]);
  const industryOptions = facets?.industries?.map(facet => facet.value) || uniqueValues(companyOptions.map(company => company.industry));
  const sizeOptions = facets?.sizes?.map(facet => facet.value) || uniqueValues(companyOptions.map(company => company.company_size));
  const sourceOptions = facets?.sources?.map(facet => facet.value) || uniqueValues(selectedCandidates.map(discoverySourceLabel));
  const refreshableCandidates = useMemo(
    () => selectedCandidates.filter(candidate => (
      candidate.status === "new"
      && ["needs-qualification", "needs-detail", "needs-freshness"].includes(candidate.review_state)
    )),
    [selectedCandidates]
  );
  const companyOptionIds = companyOptions.map(company => company.id);
  const selectedCompanyIds = discoverySelectedOptions(viewParams.get("discovery_companies"), companyOptionIds);
  const selectedIndustries = discoverySelectedOptions(viewParams.get("discovery_industries"), industryOptions);
  const selectedSizes = discoverySelectedOptions(viewParams.get("discovery_sizes"), sizeOptions);
  const selectedSources = discoverySelectedOptions(viewParams.get("discovery_sources"), sourceOptions);
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
  // The API filters and orders the complete queue before pagination. Keep
  // only optimistic status changes local while their refresh is in flight.
  const candidatesBeforeStatus = selectedCandidates;
  const visibleCandidates = useMemo(
    () => selectedCandidates.filter(candidate => discoveryCandidateMatches(candidate, resultFilter)),
    [selectedCandidates, resultFilter]
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
        filter.id === "needs-decision"
          ? statusCounts.new ?? candidatesBeforeStatus.filter(candidate => discoveryCandidateMatches(candidate, filter.id)).length
          : statusCounts[filter.id] ?? candidatesBeforeStatus.filter(candidate => discoveryCandidateMatches(candidate, filter.id)).length
      ])
    ) as Record<DiscoveryFilter, number>,
    [candidatesBeforeStatus, statusCounts]
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
  const reviewCandidateSummary = selectedCandidates.find(candidate => candidate.id === reviewCandidateId) || null;
  const reviewCandidate = detailedCandidate?.id === reviewCandidateId ? detailedCandidate : null;
  const activeReviewCandidates = reviewCandidateSummary && !reviewBatch.some(candidate => candidate.id === reviewCandidateSummary.id)
    ? [reviewCandidateSummary]
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

  async function runSearch() {
    if (!selectedSearch || !startDiscoveryJob || enrichmentActive) return;
    setPending(true);
    setIngestedPostingId("");
    setOperationStatus("");
    setRunDetailsOpen(false);
    try {
      const job = await startDiscoveryJob({
        search_id: selectedSearch.id,
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

  async function enrichPendingCandidates(candidateId = "") {
    if (!startEnrichmentJob || enrichmentActive) return;
    setRefreshingCandidateId(candidateId);
    setOperationStatus(candidateId ? "Queuing this posting check…" : "Queuing checks for existing candidates…");
    try {
      const job = await startEnrichmentJob(candidateId ? { candidate_id: candidateId, limit: 1 } : {});
      setOperationStatus(job.message);
    } catch (error) {
      setRefreshingCandidateId("");
      setOperationStatus(`Could not start posting checks. ${errorMessage(error)}`);
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
        setOperationStatus(result.candidate.review_next_action || "Role details verified and fit rescored.");
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
      await decisions.setStatus(candidate.id, status);
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
      const result = await decisions.pursue(candidate.id);
      setIngestedPostingId(result.posting.id);
      setDecisionUndo({
        candidateId: candidate.id,
        decision: "pursued",
        applicationId: result.posting.id,
        removePosting: result.created
      });
      setOperationStatus(result.created ? "Role added to Considering." : "Role is already in Postings.");
      return true;
    } catch (error) {
      setOperationStatus(`Could not add role to Considering. ${errorMessage(error)}`);
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
        const { results, failedIds } = await decisions.pursueMany(
          candidates.map(candidate => candidate.id),
          (index, total) => setOperationStatus(`Adding ${index + 1} of ${total} selected roles to Considering...`)
        );
        const successCount = results.length;
        const failureCount = failedIds.length;
        const postingId = results.at(-1)?.posting.id || "";
        setIngestedPostingId(successCount === 1 ? postingId : "");
        setOperationStatus(
          `${successCount} role${successCount === 1 ? "" : "s"} added to Considering.`
          + (failureCount ? ` ${failureCount} could not be added.` : "")
        );
      } else {
        const status = action === "ignored" ? "ignored" : "new";
        setOperationStatus(
          action === "ignored"
            ? `Ignoring ${candidates.length} selected Discovery results...`
            : `Returning ${candidates.length} selected roles to Needs decision...`
        );
        await decisions.setStatuses(
          candidates.map(candidate => candidate.id),
          status
        );
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
      const result = await decisions.markDuplicate(candidate.id, applicationId);
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
      await decisions.undo(
        decisionUndo.candidateId,
        decisionUndo.decision,
        decisionUndo.applicationId,
        decisionUndo.removePosting
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
          title="Finds new roles for this saved search using direct ATS inventories, OpenAI, and Adzuna"
          onClick={() => void runSearch()}
        >
          <SearchIcon size={15} /> {discoveryActive ? "Finding new roles…" : "Find new roles"}
        </button>
        <button
          className="button"
          type="button"
          disabled={!refreshableCandidates.length || pending || enrichmentActive || !startEnrichmentJob}
          title="Checks posting details and availability for existing eligible roles without running saved searches"
          onClick={() => void enrichPendingCandidates()}
        >
          <RefreshIcon size={15} /> {enrichmentActive && !discoveryActive
            ? "Refreshing existing…"
            : refreshableCandidates.length
              ? `Refresh existing (${refreshableCandidates.length})`
              : "No roles to refresh"}
        </button>
        <details className="discovery-actions-menu">
          <summary className="button" aria-label="Manage Discovery search">Manage</summary>
          <div className="discovery-actions-menu-content">
            <button className="button" type="button" onClick={startNewSearch}><PlusIcon size={15} /> New search</button>
            <button className="button" type="button" disabled={!selectedSearch} onClick={editSelectedSearch}>Edit search</button>
            <button className="button" type="button" disabled={!selectedSearch} onClick={() => setCaptureOpen(value => !value)}>
              <PlusIcon size={15} /> Add found roles
            </button>
          </div>
        </details>
        {selectedSearch ? (
          <span className="discovery-search-context">
            {`${shortSearchName(selectedSearch.name)} · ${discoveryLocationScope(selectedSearch.lanes)}`}
            {enrichmentActive
              ? discoveryActive ? " · Finding new roles in background" : " · Refreshing existing roles in background"
              : ""}
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
          onChange={values => updateViewParams({ discovery_companies: discoverySelectionParam(values, companyOptionIds, companyOptionIds) })}
        />
        <DiscoveryMultiFilter label="Industry" options={industryOptions.map(value => ({ id: value, label: value }))} selected={selectedIndustries} onChange={values => updateViewParams({ discovery_industries: discoverySelectionParam(values, industryOptions, industryOptions) })} />
        <DiscoveryMultiFilter label="Size" options={sizeOptions.map(value => ({ id: value, label: value }))} selected={selectedSizes} onChange={values => updateViewParams({ discovery_sizes: discoverySelectionParam(values, sizeOptions, sizeOptions) })} />
        <DiscoveryMultiFilter label="Source" options={sourceOptions.map(value => ({ id: value, label: value }))} selected={selectedSources} onChange={values => updateViewParams({ discovery_sources: discoverySelectionParam(values, sourceOptions, sourceOptions) })} />
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
              label: `Consider ready ${bulkIngestCandidates.length}`,
              primary: true,
              disabled: !bulkIngestCandidates.length || bulkIngestCandidates.length > MAX_BULK_INGEST,
              title: bulkIngestCandidates.length > MAX_BULK_INGEST
                ? `Select ${MAX_BULK_INGEST} or fewer ready roles to consider at once`
                : "Only verified roles with a company and title can be added to Considering",
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
          <span>shown from {filteredTotal ?? decisionHistoryCount} matching roles</span>
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
                  <span className="cell-subtle">{candidate.review_state === "ready" ? processingLabel(candidate) : `⚠ ${processingLabel(candidate)}`}</span>
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
                  <span
                    className={`pill freshness-${candidate.freshness_status || "unchecked"}`}
                    title={candidate.detail_last_error || freshnessLabel(candidate)}
                  >
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
                      <button
                        className="button compact primary"
                        type="button"
                        disabled={pending || rowPending || Boolean(refreshingCandidateId) || enrichmentActive || !["ready", "needs-qualification", "needs-detail", "needs-freshness"].includes(candidate.review_state)}
                        title={candidate.review_state === "ready" ? "Add to Postings in Considering" : candidate.review_next_action}
                        onClick={() => candidate.review_state === "ready"
                          ? void pursueCandidate(candidate)
                          : candidate.freshness_status === "needs-review"
                            ? setReviewCandidateId(candidate.id)
                            : void enrichPendingCandidates(candidate.id)}
                      >
                        {rowPending || refreshingCandidateId === candidate.id
                          ? "Working…"
                          : candidate.review_state === "ready"
                            ? "Consider"
                            : candidate.freshness_status === "needs-review"
                              ? "Review"
                              : candidate.review_state === "needs-qualification" ? "Verify" : candidate.review_state === "needs-freshness" ? "Check" : "Resolve"}
                      </button>
                    ) : null}
                    {candidate.status === "new" ? (
                      <button className="button compact" type="button" disabled={pending || rowPending} onClick={() => void setCandidateStatus(candidate, "ignored")}>Ignore</button>
                    ) : null}
                    {candidate.ingested_application_id ? (
                      <Link className="icon-button" to={routes.postingDetail(candidate.ingested_application_id)} aria-label="Open Considering posting" title="Open Considering posting"><BriefcaseIcon size={15} /></Link>
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
          {selectedSearch
            ? hasNextPage
              ? "No loaded Discovery results match the client-side filters. Load more to continue searching this result set."
              : "No Discovery results match the current filters."
            : "Create a Discovery search to start capturing roles."}
        </div>
      </div>

      {hasNextPage ? (
        <div className="candidate-load-more">
          <button className="button" type="button" disabled={isFetchingNextPage} onClick={loadMore}>
            {isFetchingNextPage ? "Loading…" : "Load more candidates"}
          </button>
        </div>
      ) : null}

      {editingCandidate && detailedCandidate?.id === editingCandidate.id ? (
        <CandidateDetailsModal
          candidate={detailedCandidate}
          companies={data.companies}
          pending={pending}
          close={() => setEditingCandidate(null)}
          save={saveCandidateDetails}
        />
      ) : editingCandidate ? (
        <div className="detail-status" role="status">
          {candidateDetailQuery.error ? `Could not load candidate details. ${candidateDetailQuery.error.message}` : "Loading candidate details…"}
        </div>
      ) : null}
      {reviewCandidate ? (
        <CandidateReviewModal
          key={reviewCandidate.id}
          candidate={reviewCandidate}
          company={companyById.get(reviewCandidate.company_id)}
          applications={data.applications}
          index={activeReviewCandidates.findIndex(candidate => candidate.id === reviewCandidate.id)}
          total={activeReviewCandidates.length}
          pending={pending || pendingCandidateId === reviewCandidate.id || refreshingCandidateId === reviewCandidate.id || (enrichmentActive && !discoveryActive)}
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
          refresh={() => void enrichPendingCandidates(reviewCandidate.id)}
          consider={() => void reviewCandidateStatus(reviewCandidate, "pursued")}
        />
      ) : reviewCandidateId ? (
        <div className="detail-status" role="status">
          {candidateDetailQuery.error ? `Could not load candidate details. ${candidateDetailQuery.error.message}` : "Loading candidate details…"}
        </div>
      ) : null}
    </>
  );
}
