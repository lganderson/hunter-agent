import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { BriefcaseIcon, ExternalIcon, FilterIcon, SearchIcon, XIcon } from "../components/Icons";
import { SortableHeader } from "../components/Primitives";
import {
  checkCompanyPostings,
  pursueCompanyCandidate,
  updateCompanyCandidate,
  updateCompanyCandidates
} from "../core/api";
import { dateOnlyLabel, titleCase } from "../core/format";
import { routes } from "../core/routes";
import { compareNumber, compareText, nextSortState, type SortDirection, type SortState } from "../core/tableSort";
import type { AppState, Application, CandidateEnrichmentJob, Company, CompanyPostingCandidate, DiscoveryCandidate } from "../core/types";
import { selectionFromParam, selectionParamValue, sortFromParams, usePersistentViewParams } from "../core/viewState";
import {
  CANDIDATE_FILTERS,
  RECOMMENDED_FIT_SCORE,
  STRONG_FIT_SCORE,
  candidateFitScore,
  candidateMatchesFilter,
  fitBand,
  isCurrentNewCandidate,
  isRecommendedCandidate,
  type CandidateFilter
} from "../companies/candidateUtils";
import { DiscoveryMode } from "./DiscoveryMode";
import { CandidateBulkActions, CandidateSelectionCheckbox } from "./CandidateBulkActions";

type CandidateReviewPageProps = {
  data: AppState;
  refresh: () => Promise<AppState>;
  applyCompanyCandidateUpdates: (candidates: CompanyPostingCandidate[]) => void;
  applyDiscoveryCandidateUpdate: (candidate: DiscoveryCandidate, posting?: Application | null, removePostingId?: string) => void;
  enrichmentJob?: CandidateEnrichmentJob | null;
  startDiscoveryJob?: (payload: CandidateEnrichmentJob["request"]) => Promise<CandidateEnrichmentJob>;
  startEnrichmentJob?: (payload: CandidateEnrichmentJob["request"]) => Promise<CandidateEnrichmentJob>;
};

type CandidateRow = {
  candidate: CompanyPostingCandidate;
  company: Company | null;
  fitScore: number;
  latestCheckAt: string;
};

const INTEREST_VALUES = ["interested", "neutral", "archived"];
const FIT_VALUES = ["all", "strong", "recommended", "low"];
const MAX_BULK_INGEST = 25;
type CandidateSortKey = "title" | "company" | "fit" | "status" | "last_seen";
const CANDIDATE_SORT_KEYS: CandidateSortKey[] = ["title", "company", "fit", "status", "last_seen"];

export function CandidatesPage({ data, refresh, applyCompanyCandidateUpdates, applyDiscoveryCandidateUpdate, enrichmentJob = null, startDiscoveryJob, startEnrichmentJob }: CandidateReviewPageProps) {
  const { params: viewParams, updateParams: updateViewParams } = usePersistentViewParams("candidates");
  const mode = viewParams.get("mode") === "discovery" ? "discovery" : "companies";
  const search = viewParams.get("q") || "";
  const candidateFilter = candidateFilterFromQuery(viewParams.get("status"));
  const interestStatuses = selectionFromParam(viewParams.get("interest"), INTEREST_VALUES, INTEREST_VALUES);
  const [operationStatus, setOperationStatus] = useState("");
  const [operationPending, setOperationPending] = useState(false);
  const [ingestedPostingId, setIngestedPostingId] = useState("");
  const [checkingAll, setCheckingAll] = useState(false);
  const [checkProgress, setCheckProgress] = useState<{ completed: number; total: number } | null>(null);
  const [selectedCandidateIds, setSelectedCandidateIds] = useState<Set<string>>(() => new Set());
  const checkAbortController = useRef<AbortController | null>(null);

  const companyById = useMemo(
    () => new Map(data.companies.map(company => [company.id, company])),
    [data.companies]
  );
  const companyOptions = useMemo(
    () => data.companies
      .filter(company => (
        company.tracking_status === "tracked"
        && data.company_posting_candidates.some(candidate => candidate.company_id === company.id)
      ))
      .sort((a, b) => a.name.localeCompare(b.name)),
    [data.companies, data.company_posting_candidates]
  );
  const companyOptionIds = companyOptions.map(company => company.id);
  const companyIds = selectionFromParam(viewParams.get("companies"), companyOptionIds, companyOptionIds);
  const requestedFitFilter = viewParams.get("fit") || "";
  const fitFilter = FIT_VALUES.includes(requestedFitFilter)
    ? requestedFitFilter
    : candidateFilter === "needs-decision" ? "recommended" : "all";
  const requestedLatestFilter = viewParams.get("latest");
  const latestOnly = requestedLatestFilter
    ? requestedLatestFilter === "true"
    : candidateFilter === "needs-decision";
  const requestedScopeFilter = viewParams.get("scope");
  const searchScopeOnly = requestedScopeFilter
    ? requestedScopeFilter === "matching"
    : candidateFilter === "needs-decision";
  const sort = sortFromParams(viewParams, "sort", "direction", CANDIDATE_SORT_KEYS, { key: "fit", direction: "desc" });

  const allRows = useMemo<CandidateRow[]>(
    () => data.company_posting_candidates.map(candidate => {
      const company = companyById.get(candidate.company_id) || null;
      return {
        candidate,
        company,
        fitScore: candidateFitScore(candidate),
        latestCheckAt: company?.last_checked_at || ""
      };
    }).filter(row => (
      row.company?.tracking_status === "tracked"
      && !row.candidate.discovery_candidate_id
    )),
    [companyById, data.company_posting_candidates]
  );

  const rowsBeforeStatus = useMemo(
    () => allRows.filter(row => {
      const { candidate, company, fitScore } = row;
      const query = search.trim().toLowerCase();
      if (company && !matchesSelection(company.interest_status, interestStatuses, INTEREST_VALUES)) return false;
      if (!company && interestStatuses.length !== INTEREST_VALUES.length) return false;
      if (!matchesSelection(candidate.company_id, companyIds, companyOptions.map(item => item.id))) return false;
      if (latestOnly && !isCurrentNewCandidate(candidate, row.latestCheckAt)) return false;
      if (searchScopeOnly && !candidate.lane_match) return false;
      if (["strong", "recommended"].includes(fitFilter) && candidate.review_state !== "ready") return false;
      if (!matchesFitFilter(fitScore, fitFilter)) return false;
      if (query) {
        const haystack = [
          candidate.id,
          candidate.title,
          candidate.url,
          candidate.location,
          candidate.work_mode,
          candidate.category,
          candidate.source_platform,
          candidate.source_job_id,
          candidate.matched_queries,
          candidate.scan_state,
          candidate.status,
          candidate.fit_score,
          candidate.fit_summary,
          candidate.first_seen_at,
          candidate.last_seen_at,
          company?.id || "",
          company?.name || "",
          company?.interest_status || "",
          company?.careers_url || ""
        ].join(" ").toLowerCase();
        if (!haystack.includes(query)) return false;
      }
      return true;
    }),
    [allRows, companyIds, companyOptions, fitFilter, interestStatuses, latestOnly, search, searchScopeOnly]
  );

  const candidateCounts = useMemo(
    () => ({
      "needs-decision": rowsBeforeStatus.filter(row => row.candidate.status === "new").length,
      ignored: rowsBeforeStatus.filter(row => row.candidate.status === "ignored").length,
      pursued: rowsBeforeStatus.filter(row => row.candidate.status === "pursued").length
    }),
    [rowsBeforeStatus]
  );

  const rows = useMemo(
    () => rowsBeforeStatus
      .filter(row => candidateMatchesFilter(row.candidate, candidateFilter, row.latestCheckAt))
      .sort((a, b) => compareCandidateRows(a, b, sort)),
    [candidateFilter, rowsBeforeStatus, sort]
  );

  function changeSort(key: CandidateSortKey, initialDirection: SortDirection) {
    const next = nextSortState(sort, key, initialDirection);
    updateViewParams({
      sort: next.key === "fit" ? null : next.key,
      direction: next.direction === "desc" ? null : next.direction
    });
  }
  const visibleCandidateIds = useMemo(
    () => rows.map(row => row.candidate.id),
    [rows]
  );
  const selectedRows = useMemo(
    () => rows.filter(row => selectedCandidateIds.has(row.candidate.id)),
    [rows, selectedCandidateIds]
  );
  const selectedIngestCandidates = selectedRows.filter(
    row => !["pursued", "unavailable"].includes(row.candidate.status)
      && row.candidate.review_state === "ready"
  );
  const selectedIgnoreCandidates = selectedRows.filter(row => row.candidate.status === "new");
  const selectedRestoreCandidates = selectedRows.filter(row => row.candidate.status === "ignored");
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

  async function setCandidateStatus(candidateId: string, status: string) {
    setCheckProgress(null);
    setIngestedPostingId("");
    setOperationPending(true);
    setOperationStatus(status === "ignored" ? "Ignoring candidate..." : "Updating candidate...");
    try {
      const result = await updateCompanyCandidate(candidateId, status);
      applyCompanyCandidateUpdates([result.candidate]);
      setOperationStatus(status === "ignored" ? "Candidate ignored." : "Candidate returned to Needs decision.");
    } catch (error) {
      setOperationStatus(`Could not update candidate. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setOperationPending(false);
    }
  }

  async function pursueCandidate(candidateId: string) {
    setCheckProgress(null);
    setIngestedPostingId("");
    setOperationPending(true);
    setOperationStatus("Adding role to Considering...");
    try {
      const result = await pursueCompanyCandidate(candidateId);
      await refresh();
      setIngestedPostingId(result.posting?.id || "");
      setOperationStatus("Role pursued and added to Considering.");
    } catch (error) {
      setOperationStatus(`Could not pursue role. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setOperationPending(false);
    }
  }

  async function runBulkCandidateAction(action: "pursue" | "ignored" | "new") {
    const eligibleRows = action === "pursue"
      ? selectedIngestCandidates
      : action === "ignored"
        ? selectedIgnoreCandidates
        : selectedRestoreCandidates;
    if (!eligibleRows.length) return;
    if (action === "pursue" && eligibleRows.length > MAX_BULK_INGEST) return;

    setCheckProgress(null);
    setIngestedPostingId("");
    setOperationPending(true);
    try {
      if (action === "pursue") {
        let successCount = 0;
        let failureCount = 0;
        let postingId = "";
        for (const [index, row] of eligibleRows.entries()) {
          setOperationStatus(`Pursuing ${index + 1} of ${eligibleRows.length} selected candidates...`);
          try {
            const result = await pursueCompanyCandidate(row.candidate.id);
            successCount += 1;
            postingId = result.posting?.id || postingId;
          } catch {
            failureCount += 1;
          }
        }
        await refresh();
        setIngestedPostingId(successCount === 1 ? postingId : "");
        setOperationStatus(
          `${successCount} candidate${successCount === 1 ? "" : "s"} pursued.`
          + (failureCount ? ` ${failureCount} could not be pursued.` : "")
        );
      } else {
        const status = action === "ignored" ? "ignored" : "new";
        setOperationStatus(
          action === "ignored"
            ? `Ignoring ${eligibleRows.length} selected candidates...`
            : `Returning ${eligibleRows.length} selected candidates to Needs decision...`
        );
        const result = await updateCompanyCandidates(
          eligibleRows.map(row => row.candidate.id),
          status
        );
        applyCompanyCandidateUpdates(result.candidates);
        setOperationStatus(
          action === "ignored"
            ? `${eligibleRows.length} candidates ignored.`
            : `${eligibleRows.length} candidates returned to Needs decision.`
        );
      }
      setSelectedCandidateIds(new Set());
    } catch (error) {
      setOperationStatus(`Could not update selected candidates. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setOperationPending(false);
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

  async function checkAllCompanies() {
    const companiesToCheck = data.companies.filter(
      company => company.tracking_status === "tracked"
        && company.interest_status.toLowerCase() !== "archived"
        && company.careers_url.trim()
    );
    const skippedCount = data.companies.length - companiesToCheck.length;
    const totals = {
      checked: 0,
      errors: 0,
      newCandidates: 0,
      recommended: 0,
      unavailable: 0,
      verification: 0,
      verificationSkipped: 0
    };
    const abortController = new AbortController();
    let canceled = false;

    checkAbortController.current = abortController;
    setIngestedPostingId("");
    setCheckingAll(true);
    setOperationStatus("Checking careers pages for all companies...");
    setCheckProgress({ completed: 0, total: companiesToCheck.length });
    try {
      for (const company of companiesToCheck) {
        try {
          const result = await checkCompanyPostings(company.id, abortController.signal);
          totals.checked += 1;
          totals.newCandidates += result.new.length;
          totals.recommended += result.recommended.length;
          totals.unavailable += result.unavailable_count;
          totals.verification += result.verification_count;
          totals.verificationSkipped += result.verification_skipped_count;
        } catch {
          if (abortController.signal.aborted) {
            canceled = true;
            break;
          }
          totals.errors += 1;
        } finally {
          if (!abortController.signal.aborted) {
            setCheckProgress(previous => previous ? { ...previous, completed: previous.completed + 1 } : previous);
          }
        }
      }
      await refresh();
      if (canceled) {
        setOperationStatus(`Canceled after checking ${totals.checked + totals.errors} of ${companiesToCheck.length} companies.`);
        return;
      }
      const errorText = totals.errors ? ` ${totals.errors} failed.` : "";
      const detailChecked = totals.verification ? ` ${totals.verification} detail checked.` : "";
      const detailSkipped = totals.verificationSkipped ? ` ${totals.verificationSkipped} detail skipped.` : "";
      setOperationStatus(
        `Checked ${totals.checked} companies. ${totals.newCandidates} new candidates, ${totals.recommended} recommended, ${totals.unavailable} unavailable. ${skippedCount} skipped.${detailChecked}${detailSkipped}${errorText}`
      );
    } catch (error) {
      setOperationStatus(`Could not check all companies. ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      checkAbortController.current = null;
      setCheckingAll(false);
    }
  }

  function cancelCheckAllCompanies() {
    setOperationStatus("Canceling careers-page checks...");
    checkAbortController.current?.abort();
  }

  function dismissOperationStatus() {
    setOperationStatus("");
    setCheckProgress(null);
    setIngestedPostingId("");
  }

  function clearFilters() {
    updateViewParams({
      q: null,
      status: null,
      interest: null,
      companies: null,
      fit: null,
      latest: null,
      scope: null,
      sort: null,
      direction: null
    });
  }

  function chooseMode(nextMode: "companies" | "discovery") {
    updateViewParams({ mode: nextMode === "discovery" ? "discovery" : null });
  }

  if (mode === "discovery") {
    return (
      <section className="view-section" id="candidates-view" aria-label="Discovery candidates">
        <article className="panel">
          <CandidateModeSwitch mode={mode} chooseMode={chooseMode} />
          <DiscoveryMode data={data} refresh={refresh} applyDiscoveryCandidateUpdate={applyDiscoveryCandidateUpdate} enrichmentJob={enrichmentJob} startDiscoveryJob={startDiscoveryJob} startEnrichmentJob={startEnrichmentJob} />
        </article>
      </section>
    );
  }

  return (
    <section className="view-section" id="candidates-view" aria-label="Posting candidates">
      <article className="panel">
        <CandidateModeSwitch mode={mode} chooseMode={chooseMode} />
        <div className="toolbar" aria-label="Candidate filters">
          <label className="search">
            <span className="sr-only">Search posting candidates</span>
            <SearchIcon />
            <input value={search} onChange={event => updateViewParams({ q: event.target.value || null })} type="search" placeholder="Search candidates, locations, companies, fit notes..." />
          </label>
          <MultiFilter label="Interest" values={INTEREST_VALUES} selected={interestStatuses} onChange={values => updateViewParams({ interest: selectionParamValue(values, INTEREST_VALUES, INTEREST_VALUES) })} />
          <MultiFilter label="Company" values={companyOptionIds} selected={companyIds} onChange={values => updateViewParams({ companies: selectionParamValue(values, companyOptionIds, companyOptionIds) })} labelForValue={id => companyById.get(id)?.name || id} />
          <label className="filter">Fit <select value={fitFilter} onChange={event => updateViewParams({
            fit: event.target.value === (candidateFilter === "needs-decision" ? "recommended" : "all")
              ? null
              : event.target.value
          })}>
            {FIT_VALUES.map(value => <option key={value} value={value}>{fitFilterLabel(value)}</option>)}
          </select></label>
          <label className="toggle"><input checked={latestOnly} onChange={event => updateViewParams({ latest: event.target.checked ? "true" : "false" })} type="checkbox" /> Latest scan</label>
          <label className="toggle"><input checked={searchScopeOnly} onChange={event => updateViewParams({ scope: event.target.checked ? "matching" : "all" })} type="checkbox" /> Search scope</label>
          <button className="button" type="button" onClick={clearFilters}><FilterIcon size={16} /> Clear</button>
          <button className="button primary" type="button" disabled={checkingAll} onClick={checkAllCompanies}>
            {checkingAll ? "Checking..." : "Check All Careers"}
          </button>
        </div>

        <div className="candidate-filter-bar aggregate" aria-label="Candidate status filters">
          {CANDIDATE_FILTERS.map(filter => (
            <button
              className={candidateFilter === filter.id ? "candidate-filter active" : "candidate-filter"}
              key={filter.id}
              type="button"
              onClick={() => updateViewParams({ status: filter.id === "needs-decision" ? null : filter.id })}
            >
              {filter.label}
              <span>{candidateCounts[filter.id]}</span>
            </button>
          ))}
        </div>

        {operationStatus ? (
          <div className="table-operation-status" role="status">
            <div className="table-operation-status-content">
              <span>{operationStatus}</span>
              {checkProgress ? (
                <div className="table-operation-progress">
                  <progress
                    value={checkProgress.total ? checkProgress.completed : Number(!checkingAll)}
                    max={Math.max(1, checkProgress.total)}
                    aria-label="Company careers check progress"
                  />
                  <span>{checkProgress.completed} of {checkProgress.total}</span>
                </div>
              ) : null}
            </div>
            <div className="table-operation-actions">
              {ingestedPostingId ? (
                <Link className="button compact" to={routes.postingDetail(ingestedPostingId)}>
                  <BriefcaseIcon size={15} /> View posting
                </Link>
              ) : null}
              {checkProgress && checkingAll ? (
                <button className="button compact" type="button" onClick={cancelCheckAllCompanies}>
                  Cancel
                </button>
              ) : null}
              {!checkingAll && !operationPending ? (
                <button className="icon-button table-operation-close" type="button" onClick={dismissOperationStatus} aria-label="Dismiss status message">
                  <XIcon size={15} />
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
        {selectedRows.length ? (
          <CandidateBulkActions
            selectedCount={selectedRows.length}
            shownCount={rows.length}
            pending={operationPending}
            clear={() => setSelectedCandidateIds(new Set())}
            actions={[
              {
                id: "pursue",
                label: `Pursue ${selectedIngestCandidates.length}`,
                primary: true,
                disabled: !selectedIngestCandidates.length || selectedIngestCandidates.length > MAX_BULK_INGEST,
                title: selectedIngestCandidates.length > MAX_BULK_INGEST
                  ? `Select ${MAX_BULK_INGEST} or fewer candidates to pursue at once`
                  : "Pursue selected candidates",
                run: () => void runBulkCandidateAction("pursue")
              },
              {
                id: "ignore",
                label: `Ignore ${selectedIgnoreCandidates.length}`,
                disabled: !selectedIgnoreCandidates.length,
                run: () => void runBulkCandidateAction("ignored")
              },
              {
                id: "restore",
                label: `Needs decision ${selectedRestoreCandidates.length}`,
                disabled: !selectedRestoreCandidates.length,
                run: () => void runBulkCandidateAction("new")
              }
            ]}
          />
        ) : (
          <div className="candidate-review-summary">
            <strong>{rows.length}</strong>
            <span>shown from {data.company_posting_candidates.length} total candidates</span>
          </div>
        )}

        <div className="table-scroll">
          <table className="simple-table candidates-table">
            <thead>
              <tr>
                <th className="candidate-select-column">
                  <CandidateSelectionCheckbox
                    checked={allVisibleSelected}
                    indeterminate={someVisibleSelected && !allVisibleSelected}
                    disabled={!visibleCandidateIds.length || operationPending}
                    label={allVisibleSelected ? "Clear all shown candidates" : "Select all shown candidates"}
                    onChange={toggleAllVisibleCandidates}
                  />
                </th>
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Candidate" onSort={changeSort} sortKey="title" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Company" onSort={changeSort} sortKey="company" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Fit" onSort={changeSort} sortKey="fit" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} label="Status" onSort={changeSort} sortKey="status" />
                <SortableHeader activeKey={sort.key} direction={sort.direction} initialDirection="desc" label="Last seen" onSort={changeSort} sortKey="last_seen" />
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ candidate, company, latestCheckAt }) => (
                <tr className={selectedCandidateIds.has(candidate.id) ? "candidate-row-selected" : ""} key={candidate.id}>
                  <td className="candidate-select-column">
                    <CandidateSelectionCheckbox
                      checked={selectedCandidateIds.has(candidate.id)}
                      disabled={operationPending}
                      label={`Select ${candidate.title || "candidate"}${company?.name ? ` at ${company.name}` : ""}`}
                      onChange={checked => toggleCandidateSelection(candidate.id, checked)}
                    />
                  </td>
                  <td className="role-cell candidate-title-cell">
                    <strong>{candidate.title || candidate.url}</strong>
                    <span className="cell-subtle">{candidateLocationLabel(candidate)}</span>
                  </td>
                  <td>
                    {company ? <Link to={routes.companyDetail(company.id)}>{company.name}</Link> : candidate.company_id || "Unknown"}
                    <span className="cell-subtle">{company ? titleCase(company.interest_status) : "No company record"}</span>
                  </td>
                  <td className="candidate-score-cell">
                    <span className={`pill fit-${fitBand(candidate)}`}>{candidate.fit_score || "0"}</span>
                    <span className="cell-subtle">{candidateReviewStateLabel(candidate.review_state)}</span>
                  </td>
                  <td>{titleCase(candidate.status)}</td>
                  <td>
                    {candidateDateLabel(candidate)}
                  </td>
                  <td>
                    <div className="table-actions">
                      <a className="button compact" href={candidate.url} target="_blank" rel="noreferrer"><ExternalIcon size={15} /> Open</a>
                      <button className="button compact" type="button" disabled={candidate.status === "pursued" || candidate.review_state !== "ready" || operationPending} title={candidate.review_state === "ready" ? "Add to Considering" : candidateReviewStateLabel(candidate.review_state)} onClick={() => pursueCandidate(candidate.id)}>Pursue</button>
                      {candidate.status === "ignored"
                        ? <button className="button compact" type="button" disabled={operationPending} onClick={() => setCandidateStatus(candidate.id, "new")}>Needs decision</button>
                        : <button className="button compact" type="button" disabled={candidate.status === "pursued" || operationPending} onClick={() => setCandidateStatus(candidate.id, "ignored")}>Ignore</button>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="empty-state" style={{ display: rows.length ? "none" : "block" }}>No posting candidates match the current filters.</div>
        </div>
      </article>
    </section>
  );
}

function CandidateModeSwitch({
  mode,
  chooseMode
}: {
  mode: "companies" | "discovery";
  chooseMode: (mode: "companies" | "discovery") => void;
}) {
  return (
    <div className="candidate-mode-switch" role="tablist" aria-label="Candidate source mode">
      <button className={mode === "companies" ? "active" : ""} type="button" role="tab" aria-selected={mode === "companies"} onClick={() => chooseMode("companies")}>Tracked Companies</button>
      <button className={mode === "discovery" ? "active" : ""} type="button" role="tab" aria-selected={mode === "discovery"} onClick={() => chooseMode("discovery")}>Discovery</button>
    </div>
  );
}

function MultiFilter({
  label,
  values,
  selected,
  onChange,
  labelForValue = titleCase
}: {
  label: string;
  values: string[];
  selected: string[];
  onChange: (values: string[]) => void;
  labelForValue?: (value: string) => string;
}) {
  const allSelected = values.length === selected.length;
  const summary = allSelected ? "All" : selected.length === 1 ? labelForValue(selected[0]) : `${selected.length} selected`;

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
            {labelForValue(value)}
          </label>
        ))}
      </div>
    </details>
  );
}

function compareCandidateRows(left: CandidateRow, right: CandidateRow, sort: SortState<CandidateSortKey>) {
  let result = 0;
  if (sort.key === "title") result = compareText(left.candidate.title, right.candidate.title, sort.direction);
  if (sort.key === "company") result = compareText(left.company?.name, right.company?.name, sort.direction);
  if (sort.key === "fit") result = compareNumber(left.fitScore, right.fitScore, sort.direction);
  if (sort.key === "status") result = compareText(left.candidate.status, right.candidate.status, sort.direction);
  if (sort.key === "last_seen") result = compareText(candidateDate(left), candidateDate(right), sort.direction);
  if (result) return result;
  if (sort.key === "fit") {
    return compareText(candidateDate(left), candidateDate(right), "desc")
      || compareText(left.candidate.id, right.candidate.id, "asc");
  }
  return compareNumber(left.fitScore, right.fitScore, "desc") || compareText(left.candidate.id, right.candidate.id, "asc");
}

function candidateDate(row: CandidateRow) {
  return row.candidate.last_seen_at || row.candidate.first_seen_at || "";
}

function candidateDateLabel(candidate: CompanyPostingCandidate) {
  const value = candidate.last_seen_at || candidate.first_seen_at || "";
  return value ? dateOnlyLabel(value) : "Not checked";
}

function candidateLocationLabel(candidate: CompanyPostingCandidate) {
  const location = candidate.location || "Location unknown";
  return candidate.work_mode ? `${location} · ${candidate.work_mode}` : location;
}

function matchesSelection(value: string, selected: string[], values: string[]) {
  if (!values.length || selected.length === values.length) return true;
  return selected.includes(value);
}

function candidateFilterFromQuery(value: string | null): CandidateFilter {
  const normalized = value === "recommended" || value === "new" || value === "all" ? "needs-decision" : value === "ingested" ? "pursued" : value;
  return CANDIDATE_FILTERS.some(filter => filter.id === normalized) ? normalized as CandidateFilter : "needs-decision";
}

function matchesFitFilter(score: number, filter: string) {
  if (filter === "strong") return score >= STRONG_FIT_SCORE;
  if (filter === "recommended") return score >= RECOMMENDED_FIT_SCORE;
  if (filter === "low") return score < RECOMMENDED_FIT_SCORE;
  return true;
}

function candidateReviewStateLabel(value: CompanyPostingCandidate["review_state"]) {
  if (value === "ready") return "Ready";
  if (value === "needs-detail") return "Needs detail";
  if (value === "needs-freshness") return "Needs freshness";
  return "Failed extraction";
}

function fitFilterLabel(value: string) {
  if (value === "strong") return "Strong";
  if (value === "recommended") return "45+";
  if (value === "low") return "Low";
  return "All";
}

function sameStringSet(left: Set<string>, right: Set<string>) {
  if (left.size !== right.size) return false;
  return [...left].every(value => right.has(value));
}
